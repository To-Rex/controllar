//! Length-prefixed framing for the FlowLink protocol.
//!
//! Every frame is `u32 big-endian length || payload`, where payload is a
//! bincode-serialized [`crate::protocol::Message`]. Frames larger than
//! [`crate::protocol::MAX_FRAME_BYTES`] are rejected before allocation.
//!
//! Reading goes through [`FrameReader`], which keeps partial-frame bytes in
//! the *reader*, never in the read future. The session pump selects on
//! `read_frame()` next to heartbeats and outbound traffic; `tokio::select!`
//! drops the future of the branch that loses a race. A reader that buffered
//! bytes inside that future would silently lose them, shifting the stream out
//! of alignment — peers then see garbage lengths ("frame exceeds maximum"),
//! stalled connections ("no data for 6s") and reconnect storms. Keeping the
//! state in the reader makes a dropped read future harmless.

use std::io;
use std::pin::Pin;
use std::task::{Context, Poll};

use tokio::io::{AsyncRead, AsyncWrite, AsyncWriteExt, ReadBuf};

use crate::protocol::MAX_FRAME_BYTES;

/// Write one frame, flushing.
pub async fn write_frame<W>(writer: &mut W, payload: &[u8]) -> io::Result<()>
where
    W: AsyncWrite + Unpin,
{
    if payload.len() > MAX_FRAME_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("frame too large: {} bytes", payload.len()),
        ));
    }
    let mut header = [0u8; 4];
    header.copy_from_slice(&(payload.len() as u32).to_be_bytes());
    writer.write_all(&header).await?;
    writer.write_all(payload).await?;
    writer.flush().await
}

/// Incremental, cancellation-safe frame reader.
///
/// All partial state (header bytes, payload bytes read so far) lives in this
/// struct, so dropping the future returned by [`FrameReader::read_frame`]
/// between polls — exactly what `tokio::select!` does when another branch
/// wins — never loses a byte off the stream.
pub struct FrameReader<R> {
    inner: R,
    header: [u8; 4],
    header_len: usize,
    payload: Vec<u8>,
    payload_len: usize,
}

impl<R> FrameReader<R> {
    pub fn new(inner: R) -> Self {
        Self {
            inner,
            header: [0u8; 4],
            header_len: 0,
            payload: Vec::new(),
            payload_len: 0,
        }
    }

    pub fn into_inner(self) -> R {
        self.inner
    }
}

impl<R: AsyncRead + Unpin> FrameReader<R> {
    /// Read the next frame.
    ///
    /// Returns `Ok(None)` on a clean EOF *before* any byte of the next frame.
    /// Cancellation-safe: every byte consumed so far is stored in the reader.
    pub async fn read_frame(&mut self) -> io::Result<Option<Vec<u8>>> {
        std::future::poll_fn(|cx| self.poll_frame(cx)).await
    }

    fn poll_frame(&mut self, cx: &mut Context<'_>) -> Poll<io::Result<Option<Vec<u8>>>> {
        loop {
            // ---- Phase 1: the 4-byte big-endian length header ----
            while self.header_len < self.header.len() {
                let mut buf = ReadBuf::new(&mut self.header[self.header_len..]);
                match Pin::new(&mut self.inner).poll_read(cx, &mut buf) {
                    Poll::Pending => return Poll::Pending,
                    Poll::Ready(Err(err)) => {
                        self.reset();
                        return Poll::Ready(Err(err));
                    }
                    Poll::Ready(Ok(())) => {
                        let n = buf.filled().len();
                        if n == 0 {
                            let mid_header = self.header_len > 0;
                            self.reset();
                            return if mid_header {
                                Poll::Ready(Err(io::Error::new(
                                    io::ErrorKind::UnexpectedEof,
                                    "connection closed mid-header",
                                )))
                            } else {
                                Poll::Ready(Ok(None))
                            };
                        }
                        self.header_len += n;
                    }
                }
            }

            // Header complete — validate before allocating anything.
            let len = u32::from_be_bytes(self.header) as usize;
            if len > MAX_FRAME_BYTES {
                self.reset();
                return Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("frame exceeds maximum: {len} bytes"),
                )));
            }
            if self.payload.len() != len {
                self.payload.resize(len, 0);
                self.payload_len = 0;
            }

            // ---- Phase 2: the payload ----
            while self.payload_len < len {
                let mut buf = ReadBuf::new(&mut self.payload[self.payload_len..]);
                match Pin::new(&mut self.inner).poll_read(cx, &mut buf) {
                    Poll::Pending => return Poll::Pending,
                    Poll::Ready(Err(err)) => {
                        self.reset();
                        return Poll::Ready(Err(err));
                    }
                    Poll::Ready(Ok(())) => {
                        let n = buf.filled().len();
                        if n == 0 {
                            self.reset();
                            return Poll::Ready(Err(io::Error::new(
                                io::ErrorKind::UnexpectedEof,
                                "connection closed mid-payload",
                            )));
                        }
                        self.payload_len += n;
                    }
                }
            }

            // ---- Frame complete ----
            self.header_len = 0;
            self.payload_len = 0;
            return Poll::Ready(Ok(Some(std::mem::take(&mut self.payload))));
        }
    }

    fn reset(&mut self) {
        self.header_len = 0;
        self.payload_len = 0;
        self.payload.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    async fn write_raw(writer: &mut (impl tokio::io::AsyncWrite + Unpin), payload: &[u8]) {
        write_frame(writer, payload).await.unwrap();
    }

    #[tokio::test]
    async fn roundtrip_single_frame() {
        let (mut a, b) = duplex(1024);
        write_raw(&mut a, b"hello world").await;
        let mut reader = FrameReader::new(b);
        let frame = reader.read_frame().await.unwrap().unwrap();
        assert_eq!(frame, b"hello world");
    }

    #[tokio::test]
    async fn roundtrip_many_frames() {
        let (mut a, b) = duplex(64);
        let sender = tokio::spawn(async move {
            for i in 0u32..50 {
                write_frame(&mut a, &i.to_be_bytes()).await.unwrap();
            }
        });
        let mut reader = FrameReader::new(b);
        for i in 0u32..50 {
            let frame = reader.read_frame().await.unwrap().unwrap();
            assert_eq!(u32::from_be_bytes(frame.try_into().unwrap()), i);
        }
        sender.await.unwrap();
    }

    #[tokio::test]
    async fn clean_eof_returns_none() {
        let (a, b) = duplex(64);
        drop(a);
        let mut reader = FrameReader::new(b);
        assert!(reader.read_frame().await.unwrap().is_none());
    }

    #[tokio::test]
    async fn mid_header_eof_is_error() {
        let (mut a, b) = duplex(64);
        a.write_all(&[0, 0]).await.unwrap();
        drop(a);
        let mut reader = FrameReader::new(b);
        let err = reader.read_frame().await.unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::UnexpectedEof);
    }

    #[tokio::test]
    async fn mid_payload_eof_is_error() {
        let (mut a, b) = duplex(64);
        // Header promises 16 bytes, only 4 follow.
        a.write_all(&16u32.to_be_bytes()).await.unwrap();
        a.write_all(&[1, 2, 3, 4]).await.unwrap();
        drop(a);
        let mut reader = FrameReader::new(b);
        let err = reader.read_frame().await.unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::UnexpectedEof);
    }

    #[tokio::test]
    async fn oversized_frame_is_rejected_before_allocation() {
        let (mut a, b) = duplex(64);
        let huge = (MAX_FRAME_BYTES as u32 + 1).to_be_bytes();
        a.write_all(&huge).await.unwrap();
        let mut reader = FrameReader::new(b);
        let err = reader.read_frame().await.unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::InvalidData);
    }

    /// The regression this design exists for: a read future dropped
    /// mid-frame (what `tokio::select!` does every time a heartbeat or an
    /// outbound message wins the race) must not lose the bytes already
    /// consumed, or the stream drifts out of alignment.
    #[tokio::test]
    async fn partial_frame_survives_future_drop() {
        let (mut a, b) = duplex(1024);
        let payload: Vec<u8> = (0..100u8).collect();
        let mut raw = Vec::new();
        raw.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        raw.extend_from_slice(&payload);

        // Deliver only 6 bytes (full header + 2 payload bytes), then poll the
        // reader once so it is stuck mid-frame — the state select! would
        // throw away with a future-local buffer.
        a.write_all(&raw[..6]).await.unwrap();
        let mut reader = FrameReader::new(b);
        let waker = std::task::Waker::noop();
        let mut cx = Context::from_waker(waker);
        let first = reader.poll_frame(&mut cx);
        assert!(
            first.is_pending(),
            "must be waiting for the rest of the payload"
        );

        // Another branch of the select would have completed here; the frame
        // then continues arriving.
        a.write_all(&raw[6..]).await.unwrap();
        let frame = reader.read_frame().await.unwrap().unwrap();
        assert_eq!(
            frame, payload,
            "bytes consumed before the drop must survive"
        );
    }
}
