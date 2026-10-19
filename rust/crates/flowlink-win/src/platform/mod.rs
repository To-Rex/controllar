//! Platform layer of the Windows shell.
//!
//! On Windows this is the real thing: low-level hooks, `SendInput`
//! injection, clipboard polling and the `HKCU\…\Run` entry. On every other
//! platform the same API is provided by no-op preview implementations, so
//! the egui UI can be built, run and screenshotted anywhere (see
//! `docs/BUILD.md`, "UI preview mode"). Keeping both worlds behind one
//! façade means `app.rs` never needs a `cfg`.

// HID tables are pure data (plus tests) — compiled everywhere.
pub mod hid;

#[cfg(windows)]
pub mod autostart;
#[cfg(windows)]
pub mod clipboard;
#[cfg(windows)]
pub mod firewall;
#[cfg(windows)]
pub mod input;
#[cfg(windows)]
pub mod single_instance;

// ---------------------------------------------------------------------------
// Preview (non-Windows) implementations
// ---------------------------------------------------------------------------

#[cfg(not(windows))]
pub mod autostart {
    //! No-op autostart for preview builds.
    #![allow(dead_code)]

    use std::path::Path;

    use anyhow::Result;

    pub fn is_enabled() -> bool {
        false
    }

    pub fn enable(_exe: &Path) -> Result<()> {
        Ok(())
    }

    pub fn disable() -> Result<()> {
        Ok(())
    }
}

#[cfg(not(windows))]
pub mod input {
    //! No-op capture/injection for preview builds: the UI runs with a real
    //! engine but nothing is captured or injected.
    #![allow(dead_code)]

    use std::sync::Arc;

    use anyhow::Result;
    use flowlink_core::engine::Engine;
    use flowlink_core::types::{Edge, InputEvent, ScreenInfo};
    use parking_lot::RwLock;

    /// Snapshot the hooks read to decide whether to capture — mirrors the
    /// Windows type so the UI code is identical.
    #[derive(Clone, Copy, Debug, Default)]
    pub struct CaptureState {
        pub capture_enabled: bool,
        pub forwarding: bool,
        pub clipboard_enabled: bool,
        pub remote_edge: Option<Edge>,
        pub hotkey_mods: u8,
        pub hotkey_usage: u16,
    }

    /// Handle returned by [`start`]; dropping it stops nothing in preview.
    pub struct CaptureHandle;

    impl CaptureHandle {
        pub fn stop(&self) {}
    }

    pub fn init_process_dpi_awareness() {}

    pub fn local_screen() -> ScreenInfo {
        ScreenInfo::default()
    }

    pub fn update_forwarding(_forwarding: bool) {}

    pub fn set_capture_enabled(_enabled: bool) {}

    pub fn shutdown() {}

    pub fn start(_engine: Arc<Engine>, _state: Arc<RwLock<CaptureState>>) -> Result<CaptureHandle> {
        Ok(CaptureHandle)
    }

    pub fn inject(_event: &InputEvent) {}
}

#[cfg(not(windows))]
pub mod clipboard {
    //! No-op clipboard sync for preview builds.
    #![allow(dead_code)]

    use std::sync::Arc;

    use flowlink_core::engine::Engine;
    use parking_lot::RwLock;

    use super::input::CaptureState;
    /// Echo-suppression fingerprint — mirrors the Windows type.
    #[derive(Default)]
    pub struct Fingerprint {
        _private: (),
    }

    impl Fingerprint {
        pub fn new() -> Arc<Self> {
            Arc::new(Fingerprint::default())
        }

        pub fn note(&self, _text: &str) {}

        pub fn clear(&self) {}
    }

    pub fn set_from_peer(_fingerprint: &Fingerprint, _text: &str) {}

    pub fn spawn_poller(
        _engine: Arc<Engine>,
        _fingerprint: Arc<Fingerprint>,
        _capture: Arc<RwLock<CaptureState>>,
    ) {
    }

    pub fn stop() {}
}

#[cfg(not(windows))]
pub mod firewall {
    //! Firewall management is Windows-only; preview builds report "unknown".
    #![allow(dead_code)]

    use std::path::Path;

    use anyhow::Result;

    pub const RULE_NAME: &str = "FlowLink";

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum RuleState {
        Present,
        Missing,
        Unknown,
    }

    pub fn check(_port: u16) -> RuleState {
        RuleState::Unknown
    }

    pub fn commands(_port: u16, _exe: &Path) -> Vec<String> {
        Vec::new()
    }

    pub fn allow_elevated(_port: u16, _exe: &Path) -> Result<()> {
        Ok(())
    }

    pub fn open_settings() {}
}

#[cfg(not(windows))]
pub mod single_instance {
    //! Single-instance guard is Windows-only; preview builds always proceed.

    pub fn acquire() -> bool {
        true
    }
}
