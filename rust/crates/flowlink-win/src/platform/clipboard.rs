//! Clipboard bridge: polls the local clipboard and applies peer text.
//!
//! Two directions, one fingerprint:
//!
//! * **outbound** — a polling thread samples the clipboard every
//!   [`POLL_INTERVAL`] and hands changed text to the engine
//!   ([`Cmd::SetClipboardText`]). Text we applied from the peer is ignored,
//!   because its fingerprint was recorded when it was written.
//! * **inbound** — [`set_from_peer`] writes peer text to the clipboard and
//!   records the same fingerprint, so the poller does not echo it back.
//!
//! On Windows the clipboard is a single global, exclusively owned resource:
//! another application may hold it open, so every operation may fail
//! transiently. Failures are logged at low level and simply retried on the
//! next tick.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock};
use std::thread;
use std::time::Duration;

use flowlink_core::engine::{Cmd, Engine};
use parking_lot::{Mutex, RwLock};
use tracing::{debug, trace, warn};

use super::input::CaptureState;

/// How often the local clipboard is sampled.
const POLL_INTERVAL: Duration = Duration::from_millis(400);

/// Largest text we hand to the engine / accept from the peer. Mirrors the
/// core's default `clipboard_max_bytes` (the core re-checks anyway).
const MAX_TEXT_BYTES: usize = 1 << 20; // 1 MiB

/// Set by [`stop`], checked by the polling loop.
static STOP: AtomicBool = AtomicBool::new(false);

/// The one clipboard handle. Re-created on demand: a handle that failed once
/// may be wedged, a fresh one usually is not.
static CLIPBOARD: LazyLock<Mutex<Option<arboard::Clipboard>>> = LazyLock::new(|| Mutex::new(None));

/// Fingerprint of the clipboard content we last saw *or* wrote.
///
/// Shared between the polling thread and the host callback so that a change
/// caused by the peer is never sent back to the peer.
#[derive(Default)]
pub struct Fingerprint {
    last: Mutex<Option<u64>>,
}

impl Fingerprint {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// Record `text` as the current clipboard content.
    pub fn note(&self, text: &str) {
        *self.last.lock() = Some(hash_text(text));
    }

    /// True when `text` is (bit for bit) what we last saw or wrote.
    fn matches(&self, text: &str) -> bool {
        let hash = hash_text(text);
        *self.last.lock() == Some(hash)
    }

    /// Forget the fingerprint (used when syncing is turned off and on again).
    pub fn clear(&self) {
        *self.last.lock() = None;
    }
}

/// Stable-in-process fingerprint of the clipboard text.
fn hash_text(text: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    text.hash(&mut hasher);
    hasher.finish()
}

/// Write peer clipboard text locally and suppress the echo.
pub fn set_from_peer(fingerprint: &Fingerprint, text: &str) {
    match write_clipboard(text) {
        Ok(()) => {
            fingerprint.note(text);
            debug!(bytes = text.len(), "peer clipboard applied");
        }
        Err(err) => {
            // Typically a transient "another app owns the clipboard" error.
            warn!(%err, "could not apply the peer clipboard text");
        }
    }
}

/// Start the polling thread. Returns immediately; stop it with [`stop`].
pub fn spawn_poller(
    engine: Arc<Engine>,
    fingerprint: Arc<Fingerprint>,
    capture: Arc<RwLock<CaptureState>>,
) {
    let spawn = thread::Builder::new()
        .name("flowlink-clipboard".to_string())
        .spawn(move || poll_loop(engine, fingerprint, capture));
    if let Err(err) = spawn {
        // Not fatal: clipboard syncing simply stays off.
        warn!("could not start the clipboard poller: {err}");
    }
}

/// Ask the polling thread to finish (called on shutdown).
pub fn stop() {
    STOP.store(true, Ordering::Relaxed);
}

fn poll_loop(
    engine: Arc<Engine>,
    fingerprint: Arc<Fingerprint>,
    capture: Arc<RwLock<CaptureState>>,
) {
    trace!("clipboard poller started");
    while !STOP.load(Ordering::Relaxed) {
        thread::sleep(POLL_INTERVAL);
        if STOP.load(Ordering::Relaxed) {
            break;
        }

        let text = match read_clipboard() {
            Ok(text) => text,
            Err(err) => {
                // Transient by design; the next tick retries.
                trace!(%err, "clipboard read failed");
                continue;
            }
        };

        if text.is_empty() || fingerprint.matches(&text) {
            continue;
        }
        // Remember it either way: oversized or disabled content must not be
        // retried (and must not be sent if syncing is switched on later).
        fingerprint.note(&text);

        if text.len() > MAX_TEXT_BYTES {
            debug!(bytes = text.len(), "local clipboard too large — not synced");
            continue;
        }
        if !capture.read().clipboard_enabled {
            trace!("clipboard syncing disabled — local change ignored");
            continue;
        }

        engine.execute(Cmd::SetClipboardText(text));
    }
    trace!("clipboard poller stopped");
}

/// Read the clipboard text.
fn read_clipboard() -> Result<String, arboard::Error> {
    with_clipboard(|clipboard| clipboard.get_text())
}

/// Write the clipboard text.
fn write_clipboard(text: &str) -> Result<(), arboard::Error> {
    with_clipboard(|clipboard| clipboard.set_text(text.to_string()))
}

/// Run `operation` against the shared clipboard handle, recreating the handle
/// when it is missing or the operation failed.
fn with_clipboard<T>(
    operation: impl FnOnce(&mut arboard::Clipboard) -> Result<T, arboard::Error>,
) -> Result<T, arboard::Error> {
    let mut guard = CLIPBOARD.lock();
    if guard.is_none() {
        *guard = Some(arboard::Clipboard::new().inspect_err(|err| {
            debug!(%err, "clipboard unavailable");
        })?);
    }
    let clipboard = guard
        .as_mut()
        .ok_or_else(|| arboard::Error::ClipboardNotSupported)?;
    match operation(clipboard) {
        Ok(value) => Ok(value),
        Err(err) => {
            // Drop the handle so the next attempt starts from a clean slate.
            *guard = None;
            Err(err)
        }
    }
}
