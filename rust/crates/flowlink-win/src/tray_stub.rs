//! Tray stub for non-Windows builds.
//!
//! The egui UI is cross-platform so it can be previewed and screenshotted
//! offscreen (see `docs/BUILD.md`); this module provides the same surface as
//! the real Windows tray (`tray.rs`) without creating any system icon.
#![allow(dead_code)]

use std::sync::Arc;

use anyhow::Result;
use crossbeam_channel::Sender;

use crate::app::AppShared;

/// Actions requested from the tray menu (same set as the Windows tray).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TrayCommand {
    Show,
    ModeServer,
    ModeClient,
    Start,
    Stop,
    Quit,
}

/// No-op handle.
pub struct TrayHandle;

impl TrayHandle {
    pub fn set_mode(&self, _server: bool) {}

    pub fn set_running(&self, _running: bool) {}
}

/// Succeed without creating anything; callers take the same code path.
pub fn install(
    _shared: &Arc<AppShared>,
    _sender: Sender<TrayCommand>,
    _server_mode: bool,
    _running: bool,
) -> Result<TrayHandle> {
    Ok(TrayHandle)
}
