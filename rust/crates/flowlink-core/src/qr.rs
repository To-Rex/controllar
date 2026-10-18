//! Connection links (`flowlink://connect?...`) and QR code generation.
//!
//! A link carries the server address, its certificate fingerprint and —
//! optionally — a live pairing code. Links are generated *on the server* and
//! displayed as a QR code, so scanning one on the client is equivalent to
//! typing the IP and code, with the fingerprint pinned as a bonus.

use serde::{Deserialize, Serialize};

pub const SCHEME: &str = "flowlink://connect";

/// Errors parsing a connection link.
#[derive(Debug, thiserror::Error)]
pub enum LinkError {
    #[error("not a FlowLink link")]
    NotALink,
    #[error("missing server address")]
    MissingHost,
    #[error("invalid port")]
    InvalidPort,
}

/// Parsed connection link.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ConnectLink {
    pub host: String,
    pub port: u16,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fp: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
}

impl ConnectLink {
    /// Render as `flowlink://connect?h=…&p=…&fp=…&c=…`.
    pub fn to_url(&self) -> String {
        let mut url = format!("{}?h={}&p={}", SCHEME, self.host, self.port);
        if let Some(fp) = &self.fp {
            url.push_str("&fp=");
            url.push_str(fp);
        }
        if let Some(code) = &self.code {
            url.push_str("&c=");
            url.push_str(code);
        }
        url
    }

    /// Parse a link. Accepts the canonical URL, a bare query string, or any
    /// text containing the link (e.g. pasted from a chat app).
    pub fn parse(input: &str) -> Result<Self, LinkError> {
        let start = input
            .find(SCHEME)
            .map(|i| i + SCHEME.len())
            .ok_or(LinkError::NotALink)?;
        let rest = input[start..]
            .trim_start_matches('?')
            .split_whitespace()
            .next()
            .unwrap_or("")
            .trim_end_matches(['.', ',', ';', ')', ']']);

        let mut host = None;
        let mut port = None;
        let mut fp = None;
        let mut code = None;

        for pair in rest.split('&') {
            let mut it = pair.splitn(2, '=');
            let key = it.next().unwrap_or("");
            let value = it.next().unwrap_or("").trim();
            if value.is_empty() {
                continue;
            }
            match key {
                "h" | "host" => host = Some(percent_decode(value)),
                "p" | "port" => {
                    port = value.parse::<u16>().ok();
                }
                "fp" | "fingerprint" => fp = Some(value.to_string()),
                "c" | "code" => code = Some(value.to_string()),
                _ => {}
            }
        }

        let host = host.ok_or(LinkError::MissingHost)?;
        let port = port
            .filter(|p| *p > 0)
            .unwrap_or(crate::config::DEFAULT_PORT);
        Ok(ConnectLink {
            host,
            port,
            fp,
            code,
        })
    }
}

/// Build a server invite link.
pub fn invite_url(host: &str, port: u16, fingerprint: &str, code: Option<&str>) -> String {
    ConnectLink {
        host: host.to_string(),
        port,
        fp: Some(fingerprint.to_string()),
        code: code.map(|c| c.to_string()),
    }
    .to_url()
}

/// A rendered QR code: `size × size` modules, row-major, `1` = dark,
/// including a 4-module quiet zone.
#[derive(Clone, Debug, PartialEq)]
pub struct QrMatrix {
    pub size: u16,
    pub modules: Vec<u8>,
}

impl QrMatrix {
    pub fn dark(&self, x: u16, y: u16) -> bool {
        let idx = y as usize * self.size as usize + x as usize;
        self.modules.get(idx).copied().unwrap_or(0) == 1
    }
}

/// Generate a QR code for `text` (error correction level M — 15% recovery,
/// a good balance for screen-to-camera scanning).
pub fn qr_matrix(text: &str) -> anyhow::Result<QrMatrix> {
    let code = qrcode::QrCode::with_error_correction_level(text.as_bytes(), qrcode::EcLevel::M)?;
    let colors = code.to_colors();
    let width = code.width();
    let quiet = 4usize;
    let size = width + quiet * 2;

    let mut modules = vec![0u8; size * size];
    for y in 0..width {
        for x in 0..width {
            let dark = colors[y * width + x] == qrcode::Color::Dark;
            modules[(y + quiet) * size + (x + quiet)] = dark as u8;
        }
    }
    Ok(QrMatrix {
        size: size as u16,
        modules,
    })
}

/// Minimal percent-decoding for host names in links.
fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(byte) = u8::from_str_radix(&input[i + 1..i + 3], 16) {
                out.push(byte);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn link_roundtrip() {
        let link = ConnectLink {
            host: "192.168.1.24".into(),
            port: 24800,
            fp: Some("aabb".into()),
            code: Some("123456".into()),
        };
        let url = link.to_url();
        assert!(url.starts_with("flowlink://connect?"));
        assert_eq!(ConnectLink::parse(&url).unwrap(), link);
    }

    #[test]
    fn parse_tolerates_noise() {
        let text = "Salom! Mana link: flowlink://connect?h=10.0.0.7&p=24800&c=654321 — ko'rishingiz mumkin.";
        let link = ConnectLink::parse(text).unwrap();
        assert_eq!(link.host, "10.0.0.7");
        assert_eq!(link.port, 24800);
        assert_eq!(link.code.as_deref(), Some("654321"));
    }

    #[test]
    fn parse_defaults_port_and_rejects_garbage() {
        let link = ConnectLink::parse("flowlink://connect?h=10.0.0.7").unwrap();
        assert_eq!(link.port, crate::config::DEFAULT_PORT);
        assert!(ConnectLink::parse("https://example.com").is_err());
    }

    #[test]
    fn qr_matrix_has_quiet_zone_and_content() {
        let m = qr_matrix("flowlink://connect?h=1.2.3.4&p=24800").unwrap();
        assert!(m.size > 21);
        // Quiet zone corner is light.
        assert!(!m.dark(0, 0));
        assert!(!m.dark(3, 3));
        // Find at least one dark module (the finder patterns guarantee it).
        assert!(m.modules.iter().any(|&m| m == 1));
        let expected_len = (m.size as usize) * (m.size as usize);
        assert_eq!(m.modules.len(), expected_len);
    }
}
