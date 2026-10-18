//! Identity (self-signed TLS certificate), fingerprinting and TLS configs.
//!
//! ## Trust model
//!
//! FlowLink does not use a public CA. Each installation generates an ECDSA
//! P-256 self-signed certificate at first run; its SHA-256 is the device's
//! *fingerprint*. Clients accept the certificate at the TLS layer and then:
//!
//! 1. compare the fingerprint against a pinned value (from QR code, discovery
//!    or the previous connection) — mismatch aborts before any secret is sent;
//! 2. otherwise (first contact) require a successful pairing with the
//!    6-digit code shown on the server screen.
//!
//! This is a deliberate, documented TOFU (trust on first use) design; see
//! `docs/SECURITY.md`.

use std::path::Path;
use std::sync::{Arc, Once};

use anyhow::{Context, Result};
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{DigitallySignedStruct, SignatureScheme};
use sha2::{Digest, Sha256};

/// Install the process-wide rustls crypto provider (`ring`). Idempotent.
pub fn install_crypto_provider() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        // Fails only if another provider was already installed — that is fine.
        let _ = rustls::crypto::ring::default_provider().install_default();
    });
}

/// This device's TLS identity.
#[derive(Clone)]
pub struct Identity {
    pub cert_der: Vec<u8>,
    pub key_der: Vec<u8>,
    /// Canonical lowercase hex SHA-256 of `cert_der`.
    pub fingerprint: String,
}

/// SHA-256 of a DER certificate, lowercase hex (canonical form).
pub fn fingerprint_of(cert_der: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(cert_der);
    hex::encode(hasher.finalize())
}

/// Display form: uppercase hex in 4-byte groups, e.g. `A1B2C3D4:…`.
pub fn format_fingerprint(fp: &str) -> String {
    fp.as_bytes()
        .chunks(4)
        .map(|chunk| String::from_utf8_lossy(chunk).to_uppercase())
        .collect::<Vec<_>>()
        .join(":")
}

/// True when `candidate` equals `expected`, ignoring case and separators.
pub fn fingerprints_match(expected: &str, candidate: &str) -> bool {
    normalize_fp(expected) == normalize_fp(candidate)
}

fn normalize_fp(fp: &str) -> String {
    fp.chars()
        .filter(|c| c.is_ascii_hexdigit())
        .collect::<String>()
        .to_ascii_lowercase()
}

/// Load the identity from `dir`, generating and persisting one on first run.
pub fn load_or_create_identity(dir: &Path) -> Result<Identity> {
    let cert_path = dir.join("identity-cert.der");
    let key_path = dir.join("identity-key.der");

    if cert_path.exists() && key_path.exists() {
        let cert_der = std::fs::read(&cert_path)
            .with_context(|| format!("reading {}", cert_path.display()))?;
        let key_der =
            std::fs::read(&key_path).with_context(|| format!("reading {}", key_path.display()))?;
        return Ok(Identity {
            fingerprint: fingerprint_of(&cert_der),
            cert_der,
            key_der,
        });
    }

    let rcgen::CertifiedKey { cert, key_pair } = rcgen::generate_simple_self_signed(vec![
        "flowlink".to_string(),
        "flowlink.local".to_string(),
    ])
    .context("generating self-signed identity")?;
    let cert_der = cert.der().to_vec();
    let key_der = key_pair.serialize_der();

    std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    write_private(&cert_path, &cert_der)?;
    write_private(&key_path, &key_der)?;

    tracing::info!(fingerprint = %format_fingerprint(&fingerprint_of(&cert_der)), "generated new device identity");

    Ok(Identity {
        fingerprint: fingerprint_of(&cert_der),
        cert_der,
        key_der,
    })
}

fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    std::fs::write(path, bytes).with_context(|| format!("writing {}", path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o600);
        std::fs::set_permissions(path, perms).ok();
    }
    Ok(())
}

/// TLS server configuration (no client certificates — client auth happens at
/// the application layer via pairing token, see `docs/SECURITY.md`).
pub fn server_config(identity: &Identity) -> Result<Arc<rustls::ServerConfig>> {
    install_crypto_provider();
    let certs = vec![CertificateDer::from(identity.cert_der.clone())];
    let key = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(identity.key_der.clone()));
    let config = rustls::ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .context("building TLS server config")?;
    Ok(Arc::new(config))
}

/// TLS client configuration: accepts self-signed certificates at the TLS
/// layer (trust is established by fingerprint pinning + pairing, above).
pub fn client_config() -> Arc<rustls::ClientConfig> {
    install_crypto_provider();
    let config = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(TofuVerifier::new()))
        .with_no_client_auth();
    Arc::new(config)
}

/// The certificate fingerprint of an established TLS connection's peer.
pub fn peer_fingerprint(certs: &[CertificateDer<'static>]) -> Option<String> {
    certs.first().map(|cert| fingerprint_of(cert.as_ref()))
}

/// Certificate verifier that accepts self-signed certificates (signature
/// verification of the handshake itself is still enforced) and records the
/// presented certificate for the engine to pin/inspect after the handshake.
#[derive(Debug)]
struct TofuVerifier {
    provider: Arc<rustls::crypto::CryptoProvider>,
}

impl TofuVerifier {
    fn new() -> Self {
        TofuVerifier {
            provider: Arc::new(rustls::crypto::ring::default_provider()),
        }
    }
}

impl ServerCertVerifier for TofuVerifier {
    fn verify_server_cert(
        &self,
        _end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        // Accepted; the engine compares the fingerprint with the pinned value
        // immediately after the handshake, before sending any secret.
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider
            .signature_verification_algorithms
            .supported_schemes()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn identity_is_created_and_reloaded() {
        let dir = TempDir::new().unwrap();
        let first = load_or_create_identity(dir.path()).unwrap();
        assert_eq!(first.fingerprint.len(), 64);
        let second = load_or_create_identity(dir.path()).unwrap();
        assert_eq!(first.fingerprint, second.fingerprint);
        assert_eq!(first.cert_der, second.cert_der);
        assert_eq!(first.key_der, second.key_der);
    }

    #[test]
    fn fingerprint_normalization() {
        let fp = "abcdef00112233445566778899aabbccddeeff00112233445566778899aabb";
        let spaced = format_fingerprint(fp);
        assert!(spaced.contains(':'));
        assert!(fingerprints_match(&spaced, fp));
        assert!(fingerprints_match(&fp.to_uppercase(), fp));
        assert!(!fingerprints_match(fp, &format!("0{}", &fp[1..])));
    }
}
