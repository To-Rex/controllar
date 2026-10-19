//! FlowLink wire protocol (version 1).
//!
//! Messages are serialized with bincode (compact, deterministic) and framed
//! by [`crate::framing`] with a 4-byte big-endian length prefix.
//!
//! ## Handshake
//!
//! ```text
//!   client                                 server
//!     |  -- TLS handshake (pinned identity) -->  |
//!     |  -- Hello -----------------------------> |
//!     |  <------------------------- HelloAck --  |
//!     |         known_device == false            |
//!     |  -- PairRequest { code } --------------> |   (user-visible code)
//!     |  <-- PairResult { ok, token } ---------  |
//!     |         ... or known_device == true      |
//!     |  -- Authenticate { token } ----------->  |
//!     |  <-- AuthResult { ok } ----------------  |
//!     |  == READY: Input / Clipboard / Heartbeat ==
//! ```

use serde::{Deserialize, Serialize};

use crate::types::{DeviceId, InputEvent, Mode, ScreenInfo};

/// Protocol version understood by this build.
///
/// Bumped to 2 for the symmetric focus model: both sides now exchange input,
/// so a 1.x peer would connect but silently drop the other side's input.
/// The handshake rejects mismatches with an explicit `Bye` instead.
pub const PROTOCOL_VERSION: u16 = 2;

/// Hard cap for a single framed message (clipboard text included).
pub const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Hello {
    pub protocol: u16,
    pub device_id: DeviceId,
    pub name: String,
    pub version: String,
    /// Role this peer intends to run as.
    pub mode: Mode,
    /// Size of the peer's combined displays.
    pub screen: ScreenInfo,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HelloAck {
    pub protocol: u16,
    pub device_id: DeviceId,
    pub name: String,
    pub version: String,
    /// True when the server already holds a pairing token for this device id.
    pub known_device: bool,
}

/// All protocol messages.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Message {
    Hello(Hello),
    HelloAck(HelloAck),
    /// Client proves possession of the pairing token.
    Authenticate {
        device_id: DeviceId,
        token: String,
    },
    AuthResult {
        ok: bool,
        reason: Option<String>,
    },
    /// Client asks to pair using the code shown on the server.
    PairRequest {
        device_id: DeviceId,
        name: String,
        code: String,
    },
    PairResult {
        ok: bool,
        reason: Option<String>,
        /// Shared secret, present on success.
        token: Option<String>,
        /// Remaining code attempts (informational, for the UI).
        attempts_left: Option<u8>,
    },
    /// Server → client input events.
    Input(InputEvent),
    /// Clipboard text (either direction).
    Clipboard {
        text: String,
    },
    Heartbeat {
        seq: u64,
    },
    Pong {
        seq: u64,
    },
    /// Client → server: the user pushed the cursor back over the return edge
    /// (or pressed the control hotkey) — give control back to the server.
    Reclaim,
    Bye {
        reason: String,
    },
}

impl Message {
    /// Short name for logs.
    pub fn name(&self) -> &'static str {
        match self {
            Message::Hello(_) => "Hello",
            Message::HelloAck(_) => "HelloAck",
            Message::Authenticate { .. } => "Authenticate",
            Message::AuthResult { .. } => "AuthResult",
            Message::PairRequest { .. } => "PairRequest",
            Message::PairResult { .. } => "PairResult",
            Message::Input(_) => "Input",
            Message::Clipboard { .. } => "Clipboard",
            Message::Heartbeat { .. } => "Heartbeat",
            Message::Pong { .. } => "Pong",
            Message::Reclaim => "Reclaim",
            Message::Bye { .. } => "Bye",
        }
    }
}

/// Errors while (de)serializing protocol messages.
#[derive(Debug, thiserror::Error)]
pub enum ProtocolError {
    #[error("codec error: {0}")]
    Codec(#[from] bincode::Error),
}

fn options() -> impl bincode::Options {
    use bincode::Options;
    bincode::DefaultOptions::new()
        .with_limit(MAX_FRAME_BYTES as u64)
        .with_fixint_encoding()
}

/// Serialize a message into frame payload bytes.
pub fn encode(msg: &Message) -> Result<Vec<u8>, ProtocolError> {
    use bincode::Options;
    Ok(options().serialize(msg)?)
}

/// Deserialize frame payload bytes into a message.
pub fn decode(bytes: &[u8]) -> Result<Message, ProtocolError> {
    use bincode::Options;
    Ok(options().deserialize(bytes)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Edge, Modifiers, MouseButton};

    fn roundtrip(msg: Message) {
        let bytes = encode(&msg).expect("encode");
        let back = decode(&bytes).expect("decode");
        assert_eq!(msg, back);
    }

    #[test]
    fn messages_roundtrip() {
        use crate::hid::usage;
        roundtrip(Message::Hello(Hello {
            protocol: PROTOCOL_VERSION,
            device_id: "abc".into(),
            name: "Mac".into(),
            version: "0.1.0".into(),
            mode: Mode::Server,
            screen: ScreenInfo::default(),
        }));
        roundtrip(Message::HelloAck(HelloAck {
            protocol: PROTOCOL_VERSION,
            device_id: "srv".into(),
            name: "PC".into(),
            version: "0.1.0".into(),
            known_device: true,
        }));
        roundtrip(Message::Authenticate {
            device_id: "abc".into(),
            token: "deadbeef".into(),
        });
        roundtrip(Message::AuthResult {
            ok: false,
            reason: Some("bad token".into()),
        });
        roundtrip(Message::PairRequest {
            device_id: "abc".into(),
            name: "Mac".into(),
            code: "123456".into(),
        });
        roundtrip(Message::PairResult {
            ok: true,
            reason: None,
            token: Some("ff".into()),
            attempts_left: None,
        });
        roundtrip(Message::PairResult {
            ok: false,
            reason: Some("wrong pairing code".into()),
            token: None,
            attempts_left: Some(2),
        });
        roundtrip(Message::Input(InputEvent::Enter {
            edge: Edge::Right,
            along_norm: 0.42,
        }));
        roundtrip(Message::Input(InputEvent::Key {
            usage: usage::A,
            down: true,
            modifiers: Modifiers::from_bits(Modifiers::CTRL | Modifiers::SHIFT),
        }));
        roundtrip(Message::Input(InputEvent::MouseMove {
            dx: -12.5,
            dy: 3.25,
        }));
        roundtrip(Message::Input(InputEvent::MouseButton {
            button: MouseButton::Right,
            down: true,
            modifiers: Modifiers::NONE,
        }));
        roundtrip(Message::Input(InputEvent::Scroll {
            dx: 0.0,
            dy: -40.0,
            precise: true,
        }));
        roundtrip(Message::Input(InputEvent::ReleaseAll));
        roundtrip(Message::Clipboard {
            text: "salom 🌍".into(),
        });
        roundtrip(Message::Heartbeat { seq: 7 });
        roundtrip(Message::Pong { seq: 7 });
        roundtrip(Message::Reclaim);
        roundtrip(Message::Bye {
            reason: "busy".into(),
        });
    }

    #[test]
    fn decode_rejects_garbage() {
        assert!(decode(&[0xff, 0xff, 0xff, 0xff, 0xff]).is_err());
    }
}
