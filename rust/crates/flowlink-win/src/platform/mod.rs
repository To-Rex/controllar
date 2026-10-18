//! Windows platform layer.
//!
//! Everything the engine cannot know about: the key-code tables, the low-level
//! input hooks, `SendInput` injection, the clipboard bridge and the autostart
//! registration.
//!
//! Only [`hid`] builds on non-Windows hosts — it is pure data (plus unit
//! tests) and letting it compile everywhere keeps the mapping testable with
//! `cargo test` on the macOS dev host.

// `hid` is compiled on every platform so its unit tests run in the host
// workspace too; on non-Windows hosts nothing consumes it yet.
#[cfg_attr(not(windows), allow(dead_code))]
pub mod hid;

#[cfg(windows)]
pub mod autostart;
#[cfg(windows)]
pub mod clipboard;
#[cfg(windows)]
pub mod input;
