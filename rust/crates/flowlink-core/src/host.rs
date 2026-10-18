//! The callback surface the native shells implement.
//!
//! Everything the core needs from the platform lives here: injecting input,
//! applying clipboard text, and UI notifications. Implementations must be
//! cheap and non-blocking — they are called from runtime threads.

use crate::types::InputEvent;

pub trait Host: Send + Sync + 'static {
    /// Full status snapshot (JSON, see [`crate::status::Status`]).
    /// Called on meaningful changes; may be called from any thread.
    fn on_status(&self, status_json: &str);

    /// Input events the client shell must inject locally.
    fn on_input(&self, event: &InputEvent);

    /// Clipboard text received from the peer — apply it to the local
    /// pasteboard (and suppress the echo the resulting change would cause).
    fn on_clipboard(&self, text: &str);

    /// Non-fatal notice for the UI (`{"kind": "...", "message": "..."}`).
    fn on_notice(&self, notice_json: &str);
}

/// A [`Host`] that discards everything — used in tests and headless runs.
pub struct NullHost;

impl Host for NullHost {
    fn on_status(&self, _status_json: &str) {}
    fn on_input(&self, _event: &InputEvent) {}
    fn on_clipboard(&self, _text: &str) {}
    fn on_notice(&self, _notice_json: &str) {}
}

/// Build a notice payload.
pub fn notice_json(kind: &str, message: &str) -> String {
    serde_json::json!({ "kind": kind, "message": message }).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn notice_serialization() {
        let json = notice_json("pairing", "code expired");
        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(value["kind"], "pairing");
        assert_eq!(value["message"], "code expired");
    }
}
