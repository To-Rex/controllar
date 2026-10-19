//! C ABI for the FlowLink core.
//!
//! The Swift macOS app links `libflowlink_ffi.a` and talks to the engine
//! through the functions declared in `include/flowlink.h`.
//!
//! ## Threading contract
//!
//! * All `flowlink_*` functions are callable from any thread and return
//!   promptly (commands are queued).
//! * Host callbacks fire on runtime threads; shells must marshal to their
//!   main thread themselves.
//! * Strings passed *into* callbacks are only valid for the duration of the
//!   call — copy them.
//! * Strings/bytes returned by `flowlink_*_json`/`flowlink_*_link`/
//!   `flowlink_pairing_qr_matrix` are heap allocated and must be released
//!   with [`flowlink_free_string`]/[`flowlink_free_bytes`].

use std::ffi::{c_char, c_void, CStr, CString};
use std::path::PathBuf;
use std::sync::Arc;

use flowlink_core::engine::{Cmd, Engine};
use flowlink_core::host::Host;
use flowlink_core::types::{Edge, HostInput, Hotkey, InputEvent, Mode, Modifiers, MouseButton};

// ---------------------------------------------------------------------------
// C-visible types (keep in sync with include/flowlink.h)
// ---------------------------------------------------------------------------

/// Input kinds for `FlHostInput` (shell → core).
pub const FL_HOST_MOUSE_MOVE: i32 = 0;
pub const FL_HOST_KEY: i32 = 1;
pub const FL_HOST_MOUSE_BUTTON: i32 = 2;
pub const FL_HOST_SCROLL: i32 = 3;

/// Input kinds for `FlInputEvent` (core → shell, for injection).
pub const FL_INPUT_ENTER: i32 = 0;
pub const FL_INPUT_KEY: i32 = 1;
pub const FL_INPUT_MOUSE_MOVE: i32 = 2;
pub const FL_INPUT_MOUSE_BUTTON: i32 = 3;
pub const FL_INPUT_SCROLL: i32 = 4;
pub const FL_INPUT_RELEASE_ALL: i32 = 5;

pub const FL_MOD_SHIFT: u8 = 1;
pub const FL_MOD_CTRL: u8 = 2;
pub const FL_MOD_ALT: u8 = 4;
pub const FL_MOD_META: u8 = 8;

pub const FL_EDGE_LEFT: i32 = 0;
pub const FL_EDGE_RIGHT: i32 = 1;
pub const FL_EDGE_TOP: i32 = 2;
pub const FL_EDGE_BOTTOM: i32 = 3;

pub const FL_MODE_CLIENT: i32 = 0;
pub const FL_MODE_SERVER: i32 = 1;

/// Input captured by the shell (see the constants above).
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct FlHostInput {
    pub kind: i32,
    /// HID usage id (keyboard) or `FlMouseButton` value.
    pub usage: u16,
    pub down: u8,
    pub modifiers: u8,
    /// MouseMove: absolute x (px); Key: unused; Scroll: delta dx.
    pub x: f64,
    /// MouseMove: absolute y (px); Scroll: delta dy.
    pub y: f64,
    /// MouseMove: delta dx (px).
    pub dx: f64,
    /// MouseMove: delta dy (px).
    pub dy: f64,
    /// Bit 0: precise scroll.
    pub flags: i32,
}

/// Input event for the shell to inject (core → shell).
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct FlInputEvent {
    pub kind: i32,
    /// HID usage id, or mouse button (1 = left, 2 = right, 4 = middle).
    pub usage: u16,
    pub down: u8,
    pub modifiers: u8,
    /// Enter: along-the-edge position (0..1); MouseMove: dx; Scroll: dx.
    pub x: f64,
    /// MouseMove: dy; Scroll: dy; Enter: unused.
    pub y: f64,
    /// Enter: `FlEdge` value the cursor entered from.
    pub flags: i32,
}

// Layout guardrails against silent ABI drift.
const _: () = assert!(std::mem::size_of::<FlHostInput>() == 48);
const _: () = assert!(std::mem::size_of::<FlInputEvent>() == 32);

/// Callbacks implemented by the host shell.
///
/// Every callback is optional (null pointers are skipped).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct FlHostCallbacks {
    pub user_data: *mut c_void,
    /// Status snapshot JSON (see docs/PROTOCOL.md, `Status`).
    pub on_status: Option<unsafe extern "C" fn(*mut c_void, *const c_char)>,
    /// Input event to inject locally (client role).
    pub on_input: Option<unsafe extern "C" fn(*mut c_void, *const FlInputEvent)>,
    /// Clipboard text received from the peer.
    pub on_clipboard: Option<unsafe extern "C" fn(*mut c_void, *const c_char)>,
    /// Notice JSON `{"kind": "...", "message": "..."}`.
    pub on_notice: Option<unsafe extern "C" fn(*mut c_void, *const c_char)>,
    /// Called exactly once when the core releases the handle (free user_data).
    pub on_release: Option<unsafe extern "C" fn(*mut c_void)>,
}

impl Default for FlHostCallbacks {
    fn default() -> Self {
        FlHostCallbacks {
            user_data: std::ptr::null_mut(),
            on_status: None,
            on_input: None,
            on_clipboard: None,
            on_notice: None,
            on_release: None,
        }
    }
}

/// Opaque core handle.
pub struct FlCore {
    engine: Arc<Engine>,
}

// ---------------------------------------------------------------------------
// Host bridging
// ---------------------------------------------------------------------------

struct FfiHost {
    callbacks: FlHostCallbacks,
}

// Safety: the FFI header documents that callbacks must be thread-safe; the
// pointer itself is only passed back to the host.
unsafe impl Send for FfiHost {}
unsafe impl Sync for FfiHost {}

impl FfiHost {
    fn c_str(value: &str) -> Option<CString> {
        CString::new(value).ok()
    }
}

impl Host for FfiHost {
    fn on_status(&self, status_json: &str) {
        if let (Some(cb), Some(text)) = (self.callbacks.on_status, Self::c_str(status_json)) {
            unsafe { cb(self.callbacks.user_data, text.as_ptr()) };
        }
    }

    fn on_input(&self, event: &InputEvent) {
        if let Some(cb) = self.callbacks.on_input {
            let c_event = event_to_c(event);
            unsafe { cb(self.callbacks.user_data, &c_event) };
        }
    }

    fn on_clipboard(&self, text: &str) {
        if let (Some(cb), Some(text)) = (self.callbacks.on_clipboard, Self::c_str(text)) {
            unsafe { cb(self.callbacks.user_data, text.as_ptr()) };
        }
    }

    fn on_notice(&self, notice_json: &str) {
        if let (Some(cb), Some(text)) = (self.callbacks.on_notice, Self::c_str(notice_json)) {
            unsafe { cb(self.callbacks.user_data, text.as_ptr()) };
        }
    }
}

impl Drop for FfiHost {
    fn drop(&mut self) {
        if let Some(cb) = self.callbacks.on_release {
            unsafe { cb(self.callbacks.user_data) };
        }
    }
}

// ---------------------------------------------------------------------------
// Conversions
// ---------------------------------------------------------------------------

fn edge_from_c(value: i32) -> Edge {
    match value {
        FL_EDGE_LEFT => Edge::Left,
        FL_EDGE_TOP => Edge::Top,
        FL_EDGE_BOTTOM => Edge::Bottom,
        _ => Edge::Right,
    }
}

fn edge_to_c(edge: Edge) -> i32 {
    match edge {
        Edge::Left => FL_EDGE_LEFT,
        Edge::Right => FL_EDGE_RIGHT,
        Edge::Top => FL_EDGE_TOP,
        Edge::Bottom => FL_EDGE_BOTTOM,
    }
}

fn host_input_from_c(input: &FlHostInput) -> Option<HostInput> {
    let modifiers = Modifiers::from_bits(input.modifiers);
    match input.kind {
        FL_HOST_MOUSE_MOVE => Some(HostInput::MouseMove {
            x_px: input.x,
            y_px: input.y,
            dx: input.dx,
            dy: input.dy,
        }),
        FL_HOST_KEY => Some(HostInput::Key {
            usage: input.usage,
            down: input.down != 0,
            modifiers,
        }),
        FL_HOST_MOUSE_BUTTON => {
            MouseButton::from_hid_usage(input.usage as u8).map(|button| HostInput::MouseButton {
                button,
                down: input.down != 0,
                modifiers,
            })
        }
        FL_HOST_SCROLL => Some(HostInput::Scroll {
            dx: input.x,
            dy: input.y,
            precise: input.flags & 1 != 0,
        }),
        _ => None,
    }
}

fn event_to_c(event: &InputEvent) -> FlInputEvent {
    match event {
        InputEvent::Enter { edge, along_norm } => FlInputEvent {
            kind: FL_INPUT_ENTER,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: *along_norm as f64,
            y: 0.0,
            flags: edge_to_c(*edge),
        },
        InputEvent::Key {
            usage,
            down,
            modifiers,
        } => FlInputEvent {
            kind: FL_INPUT_KEY,
            usage: *usage,
            down: *down as u8,
            modifiers: modifiers.bits(),
            x: 0.0,
            y: 0.0,
            flags: 0,
        },
        InputEvent::MouseMove { dx, dy } => FlInputEvent {
            kind: FL_INPUT_MOUSE_MOVE,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: *dx as f64,
            y: *dy as f64,
            flags: 0,
        },
        InputEvent::MouseButton {
            button,
            down,
            modifiers,
        } => FlInputEvent {
            kind: FL_INPUT_MOUSE_BUTTON,
            usage: button.hid_usage() as u16,
            down: *down as u8,
            modifiers: modifiers.bits(),
            x: 0.0,
            y: 0.0,
            flags: 0,
        },
        InputEvent::Scroll { dx, dy, precise } => FlInputEvent {
            kind: FL_INPUT_SCROLL,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: *dx as f64,
            y: *dy as f64,
            flags: *precise as i32,
        },
        InputEvent::ReleaseAll => FlInputEvent {
            kind: FL_INPUT_RELEASE_ALL,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: 0.0,
            y: 0.0,
            flags: 0,
        },
    }
}

unsafe fn cstr_to_string(ptr: *const c_char) -> Option<String> {
    if ptr.is_null() {
        return None;
    }
    CStr::from_ptr(ptr).to_str().ok().map(|s| s.to_string())
}

fn engine<'a>(core: *mut FlCore) -> Option<&'a Engine> {
    if core.is_null() {
        return None;
    }
    let core = unsafe { &*core };
    Some(&core.engine)
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

/// Core version string (static, do not free).
#[no_mangle]
pub extern "C" fn flowlink_version() -> *const c_char {
    static VERSION: std::sync::OnceLock<CString> = std::sync::OnceLock::new();
    VERSION
        .get_or_init(|| {
            CString::new(flowlink_core::VERSION).unwrap_or_else(|_| CString::new("0").unwrap())
        })
        .as_ptr()
}

/// Create the engine. `config_dir` may be null (platform default).
/// Returns null on fatal setup errors (e.g. config dir not writable).
///
/// # Safety
/// `callbacks` must point to a valid [`FlHostCallbacks`] that stays alive
/// (and is not mutated) until [`flowlink_free`] returns.
#[no_mangle]
pub unsafe extern "C" fn flowlink_new(
    config_dir: *const c_char,
    callbacks: *const FlHostCallbacks,
) -> *mut FlCore {
    let dir = cstr_to_string(config_dir)
        .map(PathBuf::from)
        .unwrap_or_else(flowlink_core::config::default_config_dir);

    let cb = if callbacks.is_null() {
        FlHostCallbacks::default()
    } else {
        unsafe { *callbacks }
    };

    let host: Arc<dyn Host> = Arc::new(FfiHost { callbacks: cb });
    match Engine::new(dir, host) {
        Ok(engine) => Box::into_raw(Box::new(FlCore { engine })),
        Err(err) => {
            // Logging may not be initialized yet; use eprintln + tracing.
            eprintln!("flowlink: engine init failed: {err:#}");
            tracing::error!("engine init failed: {err:#}");
            std::ptr::null_mut()
        }
    }
}

/// Tear the engine down and free the handle. Safe with null.
///
/// # Safety
/// `core` must come from [`flowlink_new`] and must not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn flowlink_free(core: *mut FlCore) {
    if core.is_null() {
        return;
    }
    let core = unsafe { Box::from_raw(core) };
    core.engine.execute(Cmd::Shutdown);
    // Give the runtime a moment to run the command loop's shutdown path,
    // then drop (dropping the runtime waits for blocking tasks to finish).
    std::thread::sleep(std::time::Duration::from_millis(60));
    drop(core);
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_start(core: *mut FlCore) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::Start);
    }
}

/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_stop(core: *mut FlCore) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::Stop);
    }
}

/// Set mode (`FL_MODE_CLIENT` / `FL_MODE_SERVER`) and (re)start.
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_mode(core: *mut FlCore, mode: i32) -> i32 {
    match engine(core) {
        Some(engine) => {
            let mode = if mode == FL_MODE_CLIENT {
                Mode::Client
            } else {
                Mode::Server
            };
            engine.execute(Cmd::SetMode(mode));
            0
        }
        None => -1,
    }
}

/// Connect to a server. `pairing_code` and `expected_fingerprint` may be
/// null. Returns 0 on queued, -1 on invalid handle.
///
/// # Safety
/// All pointers must be null or valid C strings.
#[no_mangle]
pub unsafe extern "C" fn flowlink_connect(
    core: *mut FlCore,
    host: *const c_char,
    port: u16,
    pairing_code: *const c_char,
    expected_fingerprint: *const c_char,
) -> i32 {
    match (engine(core), cstr_to_string(host)) {
        (Some(engine), Some(host)) => {
            engine.execute(Cmd::Connect {
                host,
                port,
                code: cstr_to_string(pairing_code),
                fingerprint: cstr_to_string(expected_fingerprint),
            });
            0
        }
        _ => -1,
    }
}

/// Connect using a `flowlink://connect?...` link (QR content or pasted text).
///
/// # Safety
/// `link` must be a valid C string or null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_connect_link(core: *mut FlCore, link: *const c_char) -> i32 {
    match (engine(core), cstr_to_string(link)) {
        (Some(engine), Some(link)) => {
            engine.execute(Cmd::ConnectLink(link));
            0
        }
        _ => -1,
    }
}

/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_disconnect(core: *mut FlCore) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::Disconnect);
    }
}

/// Provide the pairing code typed by the user (client role).
///
/// # Safety
/// `code` must be a valid C string or null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_submit_pair_code(core: *mut FlCore, code: *const c_char) {
    if let (Some(engine), Some(code)) = (engine(core), cstr_to_string(code)) {
        engine.execute(Cmd::SubmitPairCode(code));
    }
}

/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_regenerate_pair_code(core: *mut FlCore) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::RegeneratePairCode);
    }
}

/// # Safety
/// `device_id` must be a valid C string or null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_forget_device(core: *mut FlCore, device_id: *const c_char) {
    if let (Some(engine), Some(device_id)) = (engine(core), cstr_to_string(device_id)) {
        engine.execute(Cmd::ForgetDevice(device_id));
    }
}

/// Feed input captured by the shell. No-op with a null handle/event.
///
/// # Safety
/// `event` must point to a valid [`FlHostInput`] or be null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_send_input(core: *mut FlCore, event: *const FlHostInput) {
    if event.is_null() {
        return;
    }
    if let Some(engine) = engine(core) {
        if let Some(input) = host_input_from_c(unsafe { &*event }) {
            engine.execute(Cmd::HostInput(input));
        }
    }
}

/// Client role: user pushed the return edge or pressed the hotkey — ask the
/// server to take control back.
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_request_control(core: *mut FlCore) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::RequestControl);
    }
}

/// Server role: toggle driving the remote device (UI button / menu bar).
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_toggle_control(core: *mut FlCore) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::ToggleControl);
    }
}

/// Local clipboard text changed.
///
/// # Safety
/// `utf8_text` must be a valid C string or null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_clipboard_text(core: *mut FlCore, utf8_text: *const c_char) {
    if let (Some(engine), Some(text)) = (engine(core), cstr_to_string(utf8_text)) {
        engine.execute(Cmd::SetClipboardText(text));
    }
}

/// # Safety
/// `name` must be a valid C string or null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_device_name(core: *mut FlCore, name: *const c_char) {
    if let (Some(engine), Some(name)) = (engine(core), cstr_to_string(name)) {
        engine.execute(Cmd::SetDeviceName(name));
    }
}

/// Report whether the shell's local input source (event tap / hook) is live.
///
/// The core logs every transition and shows a notice while capture is down,
/// so a dead tap can never look like a dead hotkey.
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`]; `reason` must be a
/// valid C string or null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_input_capture(
    core: *mut FlCore,
    active: i32,
    reason: *const c_char,
) {
    if let Some(engine) = engine(core) {
        engine.set_input_capture(active != 0, cstr_to_string(reason));
    }
}

/// Set the local edge the cursor exits through (server role).
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_edge(core: *mut FlCore, edge: i32) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::SetEdge(edge_from_c(edge)));
    }
}

/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_clipboard_enabled(core: *mut FlCore, enabled: i32) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::SetClipboardEnabled(enabled != 0));
    }
}

/// Set the toggle hotkey from JSON `{"ctrl":bool,"alt":bool,"shift":bool,
/// "meta":bool,"usage":u16}`.
///
/// # Safety
/// `hotkey_json` must be a valid C string or null.
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_hotkey(core: *mut FlCore, hotkey_json: *const c_char) -> i32 {
    let (Some(engine), Some(json)) = (engine(core), cstr_to_string(hotkey_json)) else {
        return -1;
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&json) else {
        return -1;
    };
    let mut modifiers = 0u8;
    if value["shift"].as_bool().unwrap_or(false) {
        modifiers |= Modifiers::SHIFT;
    }
    if value["ctrl"].as_bool().unwrap_or(false) {
        modifiers |= Modifiers::CTRL;
    }
    if value["alt"].as_bool().unwrap_or(false) {
        modifiers |= Modifiers::ALT;
    }
    if value["meta"].as_bool().unwrap_or(false) {
        modifiers |= Modifiers::META;
    }
    let Some(usage) = value["usage"].as_u64() else {
        return -1;
    };
    engine.execute(Cmd::SetHotkey(Hotkey {
        modifiers,
        usage: usage as u16,
    }));
    0
}

/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_listen_port(core: *mut FlCore, port: u16) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::SetListenPort(port));
    }
}

/// Report the local screen size (pixels) used for edge math.
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_set_local_screen(
    core: *mut FlCore,
    width: f64,
    height: f64,
    scale: f64,
) {
    if let Some(engine) = engine(core) {
        engine.execute(Cmd::SetLocalScreen(flowlink_core::types::ScreenInfo {
            width,
            height,
            scale,
        }));
    }
}

// ---------------------------------------------------------------------------
// Queries (returned strings/bytes must be freed by the caller)
// ---------------------------------------------------------------------------

fn into_c_string(value: String) -> *mut c_char {
    match CString::new(value) {
        Ok(cstr) => cstr.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

/// Current status as JSON (caller frees).
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_status_json(core: *mut FlCore) -> *mut c_char {
    match engine(core) {
        Some(engine) => into_c_string(engine.status_json()),
        None => std::ptr::null_mut(),
    }
}

/// Configuration as JSON (caller frees).
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_config_json(core: *mut FlCore) -> *mut c_char {
    match engine(core) {
        Some(engine) => into_c_string(engine.config_json()),
        None => std::ptr::null_mut(),
    }
}

/// Current invite link (caller frees).
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_invite_link(core: *mut FlCore) -> *mut c_char {
    match engine(core) {
        Some(engine) => into_c_string(engine.invite_link()),
        None => std::ptr::null_mut(),
    }
}

/// QR code for the invite link: `size*size` bytes (0/1), caller frees with
/// [`flowlink_free_bytes`]. Returns null on error.
///
/// # Safety
/// `out_size` must be a valid pointer (or null); `core` must be valid.
#[no_mangle]
pub unsafe extern "C" fn flowlink_pairing_qr_matrix(
    core: *mut FlCore,
    out_size: *mut i32,
) -> *mut u8 {
    let Some(engine) = engine(core) else {
        return std::ptr::null_mut();
    };
    let Ok(matrix) = engine.pairing_qr() else {
        return std::ptr::null_mut();
    };
    if !out_size.is_null() {
        unsafe { *out_size = matrix.size as i32 };
    }
    let mut boxed = matrix.modules.into_boxed_slice();
    let ptr = boxed.as_mut_ptr();
    std::mem::forget(boxed);
    ptr
}

/// Logs directory (caller frees).
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_logs_dir(core: *mut FlCore) -> *mut c_char {
    match engine(core) {
        Some(engine) => into_c_string(
            flowlink_core::logging::logs_dir(engine.config_dir())
                .to_string_lossy()
                .to_string(),
        ),
        None => std::ptr::null_mut(),
    }
}

/// Last `max_lines` log lines (caller frees).
///
/// # Safety
/// `core` must be a valid handle from [`flowlink_new`].
#[no_mangle]
pub unsafe extern "C" fn flowlink_tail_logs(core: *mut FlCore, max_lines: i32) -> *mut c_char {
    match engine(core) {
        Some(engine) => {
            let lines =
                flowlink_core::logging::tail_lines(engine.config_dir(), max_lines.max(1) as usize);
            into_c_string(lines.join("\n"))
        }
        None => std::ptr::null_mut(),
    }
}

/// Free a string returned by this library.
///
/// # Safety
/// `value` must come from this library and be freed exactly once.
#[no_mangle]
pub unsafe extern "C" fn flowlink_free_string(value: *mut c_char) {
    if !value.is_null() {
        drop(unsafe { CString::from_raw(value) });
    }
}

/// Free a byte buffer returned by this library.
///
/// # Safety
/// `value` must come from this library with matching `len` and be freed
/// exactly once.
#[no_mangle]
pub unsafe extern "C" fn flowlink_free_bytes(value: *mut u8, len: usize) {
    if !value.is_null() {
        let slice = std::ptr::slice_from_raw_parts_mut(value, len);
        drop(unsafe { Box::from_raw(slice) });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn conversions_roundtrip() {
        let host = FlHostInput {
            kind: FL_HOST_MOUSE_MOVE,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: 100.0,
            y: 200.0,
            dx: -3.0,
            dy: 4.0,
            flags: 0,
        };
        match host_input_from_c(&host) {
            Some(HostInput::MouseMove { x_px, y_px, dx, dy }) => {
                assert_eq!((x_px, y_px, dx, dy), (100.0, 200.0, -3.0, 4.0));
            }
            other => panic!("unexpected {other:?}"),
        }

        let enter = InputEvent::Enter {
            edge: Edge::Top,
            along_norm: 0.25,
        };
        let c = event_to_c(&enter);
        assert_eq!(c.kind, FL_INPUT_ENTER);
        assert_eq!(c.flags, FL_EDGE_TOP);
        assert!((c.x - 0.25).abs() < 1e-6);

        let release = event_to_c(&InputEvent::ReleaseAll);
        assert_eq!(release.kind, FL_INPUT_RELEASE_ALL);
    }

    #[test]
    fn invalid_kind_is_rejected() {
        let input = FlHostInput {
            kind: 999,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: 0.0,
            y: 0.0,
            dx: 0.0,
            dy: 0.0,
            flags: 0,
        };
        assert!(host_input_from_c(&input).is_none());
    }
}
