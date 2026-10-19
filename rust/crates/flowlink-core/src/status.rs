//! Runtime status snapshot — the single source of truth the UI renders from.
//!
//! The engine mutates [`Status`] under a lock and pushes a serialized copy to
//! the host shell on every meaningful change.

use serde::Serialize;

use crate::types::{DeviceId, DeviceInfo, Edge, Hotkey, Mode, PairedDevicePublic};

/// Connection state machine, surfaced to the UI as a short string.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConnState {
    /// Not running.
    Off,
    /// Server is listening for clients.
    Listening,
    /// Client is dialing the server.
    Connecting,
    /// TLS + protocol handshake in progress.
    Handshake,
    /// Connected, but this device still needs to enter the pairing code.
    AwaitingPair,
    /// A pairing request is being verified.
    Pairing,
    /// Fully established and authenticated.
    Connected,
    /// Waiting for the backoff timer before the next attempt.
    Reconnecting,
    /// Last attempt failed; details in `error`.
    Error,
}

impl ConnState {
    pub fn as_str(self) -> &'static str {
        match self {
            ConnState::Off => "off",
            ConnState::Listening => "listening",
            ConnState::Connecting => "connecting",
            ConnState::Handshake => "handshake",
            ConnState::AwaitingPair => "awaiting_pair",
            ConnState::Pairing => "pairing",
            ConnState::Connected => "connected",
            ConnState::Reconnecting => "reconnecting",
            ConnState::Error => "error",
        }
    }

    /// True when input can flow (either direction) in this state family.
    pub fn is_live(self) -> bool {
        matches!(self, ConnState::Connected)
    }
}

/// This device's public identity.
#[derive(Clone, Debug, Serialize)]
pub struct LocalInfo {
    pub id: DeviceId,
    pub name: String,
    pub fingerprint: String,
    pub addrs: Vec<String>,
    pub port: u16,
}

/// The currently connected peer.
#[derive(Clone, Debug, Serialize)]
pub struct PeerStatus {
    pub id: DeviceId,
    pub name: String,
    /// Peer application version, when known.
    pub version: Option<String>,
    pub fingerprint: Option<String>,
    pub addr: String,
    pub paired: bool,
    pub since_ms: u64,
}

/// Pairing UI state (server: code to display; client: awaiting code entry).
#[derive(Clone, Debug, Serialize)]
pub struct PairingStatus {
    pub code: Option<String>,
    pub attempts_left: u8,
    pub expires_in_ms: u64,
    /// Client-side: paired-with-this-server still needs the code.
    pub awaiting_input: bool,
}

/// Traffic/latency counters for the dashboard.
#[derive(Clone, Debug, Default, Serialize)]
pub struct Metrics {
    pub rtt_ms: f32,
    pub rx_bytes: u64,
    pub tx_bytes: u64,
    pub events_out: u64,
    pub events_in: u64,
    pub connected_since_ms: Option<u64>,
    pub uptime_ms: u64,
}

/// Shared atomic counters, updated by the session without touching `Status`.
#[derive(Debug, Default)]
pub struct Counters {
    pub rx_bytes: std::sync::atomic::AtomicU64,
    pub tx_bytes: std::sync::atomic::AtomicU64,
    pub events_out: std::sync::atomic::AtomicU64,
    pub events_in: std::sync::atomic::AtomicU64,
    pub rtt_micros: std::sync::atomic::AtomicU64,
}

impl Counters {
    pub fn reset(&self) {
        use std::sync::atomic::Ordering::Relaxed;
        self.rx_bytes.store(0, Relaxed);
        self.tx_bytes.store(0, Relaxed);
        self.events_out.store(0, Relaxed);
        self.events_in.store(0, Relaxed);
        self.rtt_micros.store(0, Relaxed);
    }

    pub fn snapshot_into(&self, metrics: &mut Metrics) {
        use std::sync::atomic::Ordering::Relaxed;
        metrics.rx_bytes = self.rx_bytes.load(Relaxed);
        metrics.tx_bytes = self.tx_bytes.load(Relaxed);
        metrics.events_out = self.events_out.load(Relaxed);
        metrics.events_in = self.events_in.load(Relaxed);
        metrics.rtt_ms = self.rtt_micros.load(Relaxed) as f32 / 1000.0;
    }
}

/// Full snapshot pushed to the UI.
#[derive(Clone, Debug, Serialize)]
pub struct Status {
    pub app: String,
    pub version: String,
    pub mode: Mode,
    pub state: ConnState,
    pub error: Option<String>,
    /// Millisecond countdown while in [`ConnState::Reconnecting`].
    pub reconnect_in_ms: Option<u64>,
    pub local: LocalInfo,
    pub peer: Option<PeerStatus>,
    pub pairing: Option<PairingStatus>,
    /// Server: we currently drive the remote cursor.
    /// Client: we are being driven (remote cursor is here).
    /// Deprecated alias of [`Status::forwarding`] kept for older shells.
    pub remote_active: bool,
    /// This side owns the shared cursor: local input is used locally and the
    /// configured edge hands the cursor to the peer.
    pub focused: bool,
    /// This side's input is being captured and forwarded to the peer (the
    /// shared cursor is on the other computer).
    pub forwarding: bool,
    /// Client: the server edge the cursor entered from (also the edge to push
    /// to give control back).
    pub remote_edge: Option<Edge>,
    /// Server: configured edge the cursor exits through.
    pub edge: Edge,
    pub clipboard_enabled: bool,
    /// Configured maximum clipboard text size (bytes).
    pub clipboard_max_bytes: usize,
    pub hotkey: Hotkey,
    pub discovered: Vec<DeviceInfo>,
    pub paired_devices: Vec<PairedDevicePublic>,
    pub metrics: Metrics,
    /// Server: is the listener actually bound (vs. port conflict error).
    pub listening: bool,
}

impl Status {
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| "{}".to_string())
    }
}
