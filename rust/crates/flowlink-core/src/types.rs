//! Core domain types shared by the protocol, the engine and the native shells.

use serde::{Deserialize, Serialize};

/// Stable per-installation identifier (UUID v4), generated on first run.
pub type DeviceId = String;

// ---------------------------------------------------------------------------
// Mode / topology
// ---------------------------------------------------------------------------

/// Operating mode of this installation.
///
/// * `Server` — the machine whose keyboard/mouse is shared, it *drives* the peer.
/// * `Client` — the machine that receives and injects remote input.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Client,
    Server,
}

impl Mode {
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Client => "client",
            Mode::Server => "server",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "client" => Some(Mode::Client),
            "server" => Some(Mode::Server),
            _ => None,
        }
    }
}

/// A screen edge. Used both for the edge where the cursor leaves the server
/// and (mirrored) for the edge where it enters / returns on the client.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Edge {
    Left,
    Right,
    Top,
    Bottom,
}

impl Edge {
    pub fn as_str(self) -> &'static str {
        match self {
            Edge::Left => "left",
            Edge::Right => "right",
            Edge::Top => "top",
            Edge::Bottom => "bottom",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "left" => Some(Edge::Left),
            "right" => Some(Edge::Right),
            "top" => Some(Edge::Top),
            "bottom" => Some(Edge::Bottom),
            _ => None,
        }
    }

    pub fn opposite(self) -> Self {
        match self {
            Edge::Left => Edge::Right,
            Edge::Right => Edge::Left,
            Edge::Top => Edge::Bottom,
            Edge::Bottom => Edge::Top,
        }
    }

    /// True for left/right edges (the "along" axis is vertical).
    pub fn is_horizontal(self) -> bool {
        matches!(self, Edge::Left | Edge::Right)
    }
}

// ---------------------------------------------------------------------------
// Modifiers / keys
// ---------------------------------------------------------------------------

/// Bit set of currently pressed modifiers.
#[derive(Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Modifiers(pub u8);

impl Modifiers {
    pub const SHIFT: u8 = 0b0001;
    pub const CTRL: u8 = 0b0010;
    pub const ALT: u8 = 0b0100;
    pub const META: u8 = 0b1000;

    pub const NONE: Modifiers = Modifiers(0);

    pub fn bits(self) -> u8 {
        self.0
    }

    pub fn from_bits(bits: u8) -> Self {
        Modifiers(bits & 0b1111)
    }

    pub fn contains(self, flag: u8) -> bool {
        self.0 & flag != 0
    }

    pub fn with(self, flag: u8) -> Self {
        Modifiers(self.0 | flag)
    }

    pub fn without(self, flag: u8) -> Self {
        Modifiers(self.0 & !flag)
    }

    pub fn set(self, flag: u8, on: bool) -> Self {
        if on {
            self.with(flag)
        } else {
            self.without(flag)
        }
    }

    pub fn intersect(self, other: Modifiers) -> Self {
        Modifiers(self.0 & other.0)
    }

    pub fn union(self, other: Modifiers) -> Self {
        Modifiers(self.0 | other.0)
    }
}

impl std::fmt::Debug for Modifiers {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut parts = Vec::new();
        if self.contains(Self::CTRL) {
            parts.push("ctrl");
        }
        if self.contains(Self::ALT) {
            parts.push("alt");
        }
        if self.contains(Self::SHIFT) {
            parts.push("shift");
        }
        if self.contains(Self::META) {
            parts.push("meta");
        }
        write!(f, "Modifiers({})", parts.join("+"))
    }
}

/// Configurable hotkey that toggles remote control.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Hotkey {
    /// Modifier bit set (see [`Modifiers`]).
    pub modifiers: u8,
    /// HID keyboard usage id of the key.
    pub usage: u16,
}

impl Default for Hotkey {
    /// Ctrl+Alt+Space — a deliberate chord that does not collide with common
    /// system shortcuts on either platform (⌃⌥Space is not bound by macOS by
    /// default, and it needs no Fn/ScrollLock keys that laptops lack).
    fn default() -> Self {
        Hotkey {
            modifiers: Modifiers::CTRL | Modifiers::ALT,
            usage: hid::usage::SPACE,
        }
    }
}

impl Hotkey {
    /// True when the incoming key event exactly matches the chord.
    pub fn matches(&self, usage: u16, modifiers: Modifiers) -> bool {
        self.usage == usage && self.modifiers == modifiers.bits()
    }
}

// ---------------------------------------------------------------------------
// Mouse
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MouseButton {
    Left,
    Right,
    Middle,
}

impl MouseButton {
    /// HID button page usage id.
    pub fn hid_usage(self) -> u8 {
        match self {
            MouseButton::Left => 1,
            MouseButton::Right => 2,
            MouseButton::Middle => 4,
        }
    }

    pub fn from_hid_usage(usage: u8) -> Option<Self> {
        match usage {
            1 => Some(MouseButton::Left),
            2 => Some(MouseButton::Right),
            4 => Some(MouseButton::Middle),
            _ => None,
        }
    }
}

// ---------------------------------------------------------------------------
// Screens & input events
// ---------------------------------------------------------------------------

/// Size of a display / union of displays, in physical pixels.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct ScreenInfo {
    pub width: f64,
    pub height: f64,
    pub scale: f64,
}

impl Default for ScreenInfo {
    fn default() -> Self {
        ScreenInfo {
            width: 1920.0,
            height: 1080.0,
            scale: 1.0,
        }
    }
}

/// Input captured natively by the host shell, in host pixel coordinates.
///
/// The shell feeds *every* relevant event to the engine; the engine decides
/// whether the event stays local, activates edge control, or is forwarded to
/// the peer (see [`crate::edge`] and [`crate::engine`]).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum HostInput {
    Key {
        usage: u16,
        down: bool,
        modifiers: Modifiers,
    },
    /// Absolute cursor position in pixels (origin = top-left of the union of
    /// all displays) plus the pixel delta of this event.
    MouseMove {
        x_px: f64,
        y_px: f64,
        dx: f64,
        dy: f64,
    },
    MouseButton {
        button: MouseButton,
        down: bool,
        modifiers: Modifiers,
    },
    Scroll {
        dx: f64,
        dy: f64,
        precise: bool,
    },
}

/// Input event as it travels on the wire (server → client) and as it is handed
/// back to the *receiving* host shell for injection.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub enum InputEvent {
    /// The remote cursor enters the client: place the pointer on `edge`'s
    /// opposite side at `along_norm` (0..1) of that edge.
    Enter {
        edge: Edge,
        along_norm: f32,
    },
    Key {
        usage: u16,
        down: bool,
        modifiers: Modifiers,
    },
    /// Relative mouse movement in pixels.
    MouseMove {
        dx: f32,
        dy: f32,
    },
    MouseButton {
        button: MouseButton,
        down: bool,
        modifiers: Modifiers,
    },
    Scroll {
        dx: f32,
        dy: f32,
        precise: bool,
    },
    /// Release every key/button that the receiving side currently holds down.
    /// Sent when control leaves the device so nothing gets stuck (e.g. a
    /// modifier held while the cursor returns).
    ReleaseAll,
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

/// A device seen on the local network (via mDNS) or manually entered.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub id: String,
    pub name: String,
    /// Canonical lowercase-hex SHA-256 of the peer's TLS certificate.
    pub fingerprint: String,
    pub addr: Option<String>,
    pub port: u16,
    /// Announced role, when known.
    pub role: Option<Mode>,
    pub version: Option<String>,
    /// Unix ms timestamp of the last sighting.
    pub last_seen_ms: u64,
}

/// A device we have completed a pairing with.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairedDevice {
    pub id: DeviceId,
    pub name: String,
    /// Shared secret established during pairing (hex).
    pub token: String,
    pub fingerprint: String,
    pub last_addr: Option<String>,
    pub last_seen_ms: u64,
}

/// Public (token-free) projection of [`PairedDevice`] exposed in status/UI.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairedDevicePublic {
    pub id: DeviceId,
    pub name: String,
    pub fingerprint: String,
    pub last_addr: Option<String>,
    pub last_seen_ms: u64,
}

impl From<&PairedDevice> for PairedDevicePublic {
    fn from(value: &PairedDevice) -> Self {
        PairedDevicePublic {
            id: value.id.clone(),
            name: value.name.clone(),
            fingerprint: value.fingerprint.clone(),
            last_addr: value.last_addr.clone(),
            last_seen_ms: value.last_seen_ms,
        }
    }
}

/// Where the client should look for a server next time.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LastServer {
    pub host: String,
    pub port: u16,
    pub device_id: Option<DeviceId>,
    pub fingerprint: Option<String>,
    /// Shared token for silent reconnects, established at pairing time.
    pub token: Option<String>,
}

pub use crate::hid;
