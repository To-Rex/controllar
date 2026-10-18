//! TCP + TLS plumbing shared by client and server roles.

use std::net::SocketAddr;
use std::time::Duration;

use anyhow::{Context, Result};
use tokio::net::{TcpListener, TcpStream};
use tokio_rustls::{TlsAcceptor, TlsConnector, TlsStream};

use crate::crypto;

/// The stream type both roles operate on.
pub type Stream = TlsStream<TcpStream>;

/// Apply low-latency socket options.
pub fn configure_tcp(stream: &TcpStream) -> std::io::Result<()> {
    // Input events are tiny and latency-critical: never wait to coalesce.
    stream.set_nodelay(true)?;
    Ok(())
}

/// Bind the listener for the server role.
pub async fn bind_listener(port: u16) -> Result<TcpListener> {
    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    TcpListener::bind(addr)
        .await
        .with_context(|| format!("binding TCP port {port}"))
}

/// Accept one TCP connection and complete the TLS handshake.
pub async fn accept_tls(
    acceptor: &TlsAcceptor,
    listener: &TcpListener,
) -> Result<(Stream, SocketAddr)> {
    let (tcp, peer) = listener
        .accept()
        .await
        .context("accepting TCP connection")?;
    configure_tcp(&tcp).ok();
    let stream = acceptor
        .accept(tcp)
        .await
        .with_context(|| format!("TLS handshake with {peer}"))?;
    Ok((TlsStream::Server(stream), peer))
}

/// Dial a server and complete the TLS handshake, with a timeout.
pub async fn connect_tls(
    connector: &TlsConnector,
    host: &str,
    port: u16,
    timeout: Duration,
) -> Result<Stream> {
    let addr: SocketAddr = resolve(host, port).await?;
    let stream = tokio::time::timeout(timeout, TcpStream::connect(addr))
        .await
        .context("connect timed out")?
        .with_context(|| format!("connecting to {addr}"))?;
    configure_tcp(&stream).ok();

    // The certificate identity replaces hostname verification (see crypto.rs),
    // but rustls still needs a ServerName value.
    let server_name =
        rustls::pki_types::ServerName::try_from("flowlink.local").context("invalid server name")?;
    let tls = tokio::time::timeout(timeout, connector.connect(server_name, stream))
        .await
        .context("TLS handshake timed out")?
        .context("TLS handshake failed")?;
    Ok(TlsStream::Client(tls))
}

/// Resolve `host:port`, preferring IPv4 results on mixed networks.
async fn resolve(host: &str, port: u16) -> Result<SocketAddr> {
    // Fast path for literal addresses.
    if let Ok(ip) = host.parse::<std::net::IpAddr>() {
        return Ok(SocketAddr::new(ip, port));
    }
    let mut addrs = tokio::net::lookup_host((host, port))
        .await
        .with_context(|| format!("resolving {host}"))?
        .collect::<Vec<_>>();
    addrs.sort_by_key(|a| matches!(a, SocketAddr::V6(_))); // IPv4 first
    addrs
        .into_iter()
        .next()
        .with_context(|| format!("no addresses for {host}"))
}

/// TLS server acceptor from this device's identity.
pub fn tls_acceptor(identity: &crypto::Identity) -> Result<TlsAcceptor> {
    let config = crypto::server_config(identity)?;
    Ok(TlsAcceptor::from(config))
}

/// TLS client connector (TOFU verifier; pinning is enforced at the
/// application layer right after the handshake).
pub fn tls_connector() -> TlsConnector {
    TlsConnector::from(crypto::client_config())
}

/// Fingerprint of the peer certificate on an established stream.
pub fn stream_peer_fingerprint(stream: &Stream) -> Option<String> {
    let (_, conn) = stream.get_ref();
    let certs = conn.peer_certificates()?;
    crypto::peer_fingerprint(certs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crypto::load_or_create_identity;
    use tempfile::TempDir;

    #[tokio::test]
    async fn tls_roundtrip_and_fingerprint() {
        let dir = TempDir::new().unwrap();
        let identity = load_or_create_identity(dir.path()).unwrap();
        let acceptor = tls_acceptor(&identity).unwrap();

        let listener = bind_listener(0).await.unwrap();
        let addr = listener.local_addr().unwrap();

        let server = tokio::spawn(async move {
            let (mut stream, _) = accept_tls(&acceptor, &listener).await.unwrap();
            let fp = stream_peer_fingerprint(&stream);
            let mut buf = [0u8; 4];
            tokio::io::AsyncReadExt::read_exact(&mut stream, &mut buf)
                .await
                .unwrap();
            assert_eq!(&buf, b"ping");
            tokio::io::AsyncWriteExt::write_all(&mut stream, b"pong")
                .await
                .unwrap();
            fp
        });

        let connector = tls_connector();
        let mut stream = connect_tls(&connector, "127.0.0.1", addr.port(), Duration::from_secs(5))
            .await
            .unwrap();
        // Client sees the server identity.
        let client_view = stream_peer_fingerprint(&stream);
        tokio::io::AsyncWriteExt::write_all(&mut stream, b"ping")
            .await
            .unwrap();
        let mut buf = [0u8; 4];
        tokio::io::AsyncReadExt::read_exact(&mut stream, &mut buf)
            .await
            .unwrap();
        assert_eq!(&buf, b"pong");

        // Server sees no client cert (one-way TLS by design), client pins server.
        let server_view = server.await.unwrap();
        assert_eq!(client_view, Some(identity.fingerprint.clone()));
        assert!(server_view.is_none());
    }
}
