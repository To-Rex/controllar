//! FlowLink for Windows — native shell around `flowlink-core`.
//!
//! The real implementation lives in `app.rs` and `platform/` and is compiled
//! only on Windows (see `cfg(windows)` below); on other platforms this
//! binary is a stub so the workspace still checks everywhere.
//!
//! `platform::hid` is the exception: it is pure data plus tests, so it is
//! compiled everywhere and covered by `cargo test` on the host target too.

#[cfg(windows)]
mod app;
mod platform;
#[cfg(windows)]
mod theme;
#[cfg(windows)]
mod tray;

#[cfg(windows)]
fn main() {
    if let Err(err) = app::run() {
        eprintln!("flowlink-win: fatal: {err:#}");
        std::process::exit(1);
    }
}

#[cfg(not(windows))]
fn main() {
    eprintln!("flowlink-win is a Windows-only application; build it on Windows.");
}
