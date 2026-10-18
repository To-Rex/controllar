//! Clipboard relay gate: size limits, echo suppression and loop prevention.
//!
//! Both directions funnel through one gate that remembers the hash of the
//! last text it has seen. When the remote text is applied to the local
//! pasteboard, the pasteboard watcher will fire for that same text — the gate
//! recognizes the hash and swallows it, so clipboard content never bounces
//! back and forth.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

/// What the engine should do with a clipboard change.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ClipDecision {
    /// Send this text to the peer.
    Send,
    /// Apply this text locally (it came from the peer).
    Applied,
    /// Identical to the last text seen in either direction — ignore.
    IgnoreSame,
    /// Larger than `max_bytes` — ignore (logged by the caller).
    TooLarge,
    /// Clipboard sync disabled.
    Disabled,
}

#[derive(Debug)]
pub struct ClipboardGate {
    enabled: bool,
    max_bytes: usize,
    last_hash: Option<u64>,
}

impl ClipboardGate {
    pub fn new(enabled: bool, max_bytes: usize) -> Self {
        ClipboardGate {
            enabled,
            max_bytes: max_bytes.max(16),
            last_hash: None,
        }
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
    }

    pub fn set_max_bytes(&mut self, max_bytes: usize) {
        self.max_bytes = max_bytes.max(16);
    }

    /// A change observed on the local pasteboard.
    pub fn local_change(&mut self, text: &str) -> ClipDecision {
        if !self.enabled {
            return ClipDecision::Disabled;
        }
        let hash = hash_text(text);
        if self.last_hash == Some(hash) {
            return ClipDecision::IgnoreSame;
        }
        self.last_hash = Some(hash);
        if text.len() > self.max_bytes {
            return ClipDecision::TooLarge;
        }
        ClipDecision::Send
    }

    /// Text received from the peer.
    pub fn incoming(&mut self, text: &str) -> ClipDecision {
        if !self.enabled {
            return ClipDecision::Disabled;
        }
        let hash = hash_text(text);
        if self.last_hash == Some(hash) {
            return ClipDecision::IgnoreSame;
        }
        self.last_hash = Some(hash);
        if text.len() > self.max_bytes {
            return ClipDecision::TooLarge;
        }
        ClipDecision::Applied
    }
}

/// Stable in-process hash of clipboard text.
pub fn hash_text(text: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    text.hash(&mut hasher);
    hasher.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_changes_send_once() {
        let mut gate = ClipboardGate::new(true, 1024);
        assert_eq!(gate.local_change("hello"), ClipDecision::Send);
        assert_eq!(gate.local_change("hello"), ClipDecision::IgnoreSame);
        assert_eq!(gate.local_change("world"), ClipDecision::Send);
    }

    #[test]
    fn remote_echo_is_swallowed() {
        let mut gate = ClipboardGate::new(true, 1024);
        // Incoming text from the peer…
        assert_eq!(gate.incoming("from peer"), ClipDecision::Applied);
        // …then the pasteboard watcher fires for the same text locally.
        assert_eq!(gate.local_change("from peer"), ClipDecision::IgnoreSame);
    }

    #[test]
    fn size_limit_is_enforced_in_both_directions() {
        let mut gate = ClipboardGate::new(true, 16);
        assert_eq!(gate.local_change(&"x".repeat(17)), ClipDecision::TooLarge);
        assert_eq!(gate.incoming(&"y".repeat(17)), ClipDecision::TooLarge);
        assert_eq!(gate.local_change("short"), ClipDecision::Send);
    }

    #[test]
    fn disabled_gate_ignores_everything() {
        let mut gate = ClipboardGate::new(false, 1024);
        assert_eq!(gate.local_change("hello"), ClipDecision::Disabled);
        assert_eq!(gate.incoming("hello"), ClipDecision::Disabled);
    }
}
