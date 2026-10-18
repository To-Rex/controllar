//! Length-prefixed framing for the FlowLink protocol.
//!
//! Every frame is `u32 big-endian length || payload`, where payload is a
//! bincode-serialized [`crate::protocol::Message`]. Frames larger than
//! [`crate::protocol::MAX_FRAME_BYTES`] are rejected before allocation.

use std::io;

use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

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

/// Read one frame.
///
/// Returns `Ok(None)` on a clean EOF *before* any byte of the next frame.
pub async fn read_frame<R>(reader: &mut R) -> io::Result<Option<Vec<u8>>>
where
    R: AsyncRead + Unpin,
{
    let mut header = [0u8; 4];
    let mut filled = 0usize;
    while filled < header.len() {
        let n = reader.read(&mut header[filled..]).await?;
        if n == 0 {
            return if filled == 0 {
                Ok(None)
            } else {
                Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "connection closed mid-header",
                ))
            };
        }
        filled += n;
    }

    let len = u32::from_be_bytes(header) as usize;
    if len > MAX_FRAME_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("frame exceeds maximum: {len} bytes"),
        ));
    }
    let mut payload = vec![0u8; len];
    reader.read_exact(&mut payload).await?;
    Ok(Some(payload))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    #[tokio::test]
    async fn roundtrip_single_frame() {
        let (mut a, mut b) = duplex(1024);
        write_frame(&mut a, b"hello world").await.unwrap();
        let frame = read_frame(&mut b).await.unwrap().unwrap();
        assert_eq!(frame, b"hello world");
    }

    #[tokio::test]
    async fn roundtrip_many_frames() {
        let (mut a, mut b) = duplex(64);
        let sender = tokio::spawn(async move {
            for i in 0u32..50 {
                write_frame(&mut a, &i.to_be_bytes()).await.unwrap();
            }
        });
        for i in 0u32..50 {
            let frame = read_frame(&mut b).await.unwrap().unwrap();
            assert_eq!(u32::from_be_bytes(frame.try_into().unwrap()), i);
        }
        sender.await.unwrap();
    }

    #[tokio::test]
    async fn clean_eof_returns_none() {
        let (a, mut b) = duplex(64);
        drop(a);
        assert!(read_frame(&mut b).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn mid_header_eof_is_error() {
        let (mut a, mut b) = duplex(64);
        a.write_all(&[0, 0]).await.unwrap();
        drop(a);
        let err = read_frame(&mut b).await.unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::UnexpectedEof);
    }

    #[tokio::test]
    async fn oversized_frame_is_rejected_before_allocation() {
        let (mut a, mut b) = duplex(64);
        let huge = (MAX_FRAME_BYTES as u32 + 1).to_be_bytes();
        a.write_all(&huge).await.unwrap();
        let err = read_frame(&mut b).await.unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::InvalidData);
    }
}
