//! FlowLink for Windows — native shell around `flowlink-core`.
//!
//! On Windows this is the real application: low-level input hooks,
//! `SendInput` injection, clipboard sync, tray icon and the egui
//! configuration window.
//!
//! On other platforms the same binary still builds and runs in **UI preview
//! mode** (see `docs/BUILD.md`): the real engine and the same egui UI, with
//! no-op platform integrations, plus an offscreen screenshot facility used
//! to keep the Windows design aligned with the macOS app.
//!
//! Preview examples:
//!
//! ```text
//! flowlink-win --config-dir /tmp/flowlink-preview --preview-page server \
//!              --screenshot /tmp/server.png
//! ```

mod app;
mod platform;
mod theme;

#[cfg(windows)]
mod tray;
#[cfg(not(windows))]
#[path = "tray_stub.rs"]
mod tray;

fn main() {
    if let Err(err) = app::run() {
        eprintln!("flowlink-win: fatal: {err:#}");
        std::process::exit(1);
    }
}
