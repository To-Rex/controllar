//! Low-level input capture (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) and injection
//! (`SendInput`).
//!
//! # Why two different mechanisms
//!
//! * **Capture** must run on the thread that installs the hooks: low-level
//!   hooks are delivered to the installing thread's message queue, so a
//!   dedicated thread owns them together with a `GetMessageW` pump.
//! * **Injection** goes through `SendInput`, which may be called from any
//!   thread; the engine hands us `InputEvent`s on its runtime threads.
//!
//! # Capture policy
//!
//! The hook thread forwards events to the engine and decides whether to
//! swallow them:
//!
//! | situation                     | forward | swallow |
//! |-------------------------------|---------|---------|
//! | capture disabled (UI toggle)  | no      | no      |
//! | hotkey chord (Ctrl+Alt+Space) | yes     | yes     |
//! | server driving the remote     | yes     | yes     |
//! | client being driven           | keys only (hotkey detection), never swallowed |
//! | everything else               | no      | no      |
//!
//! Swallowing prevents the local machine from acting on keystrokes that are
//! meant for the peer. On the client we never swallow: the local user is not
//! "locked out", they simply use the hotkey to take control back.

use std::collections::HashSet;
use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicIsize, AtomicU32, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, LazyLock, OnceLock};
use std::thread;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use flowlink_core::engine::{Cmd, Engine};
use flowlink_core::types::{Edge, HostInput, InputEvent, Modifiers, MouseButton, ScreenInfo};
use parking_lot::{Mutex, RwLock};
use tracing::{debug, info, trace, warn};
use windows::core::PCWSTR;
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::HiDpi::{
    SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, MOUSEEVENTF_HWHEEL, MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_MOVE,
    MOUSEEVENTF_MOVE_NOCOALESCE, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_WHEEL,
    MOUSEINPUT, MOUSE_EVENT_FLAGS, VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::HHOOK;
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, DispatchMessageW, GetCursorPos, GetMessageW, GetSystemMetrics, PeekMessageW,
    PostThreadMessageW, SetCursorPos, SetTimer, SetWindowsHookExW, ShowCursor, TranslateMessage,
    UnhookWindowsHookEx, KBDLLHOOKSTRUCT, LLKHF_INJECTED, LLMHF_INJECTED, MSG, MSLLHOOKSTRUCT,
    PM_NOREMOVE, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    WH_KEYBOARD_LL, WH_MOUSE_LL, WM_KEYDOWN, WM_KEYUP, WM_LBUTTONDOWN, WM_LBUTTONUP,
    WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MOUSEHWHEEL, WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_QUIT,
    WM_RBUTTONDOWN, WM_RBUTTONUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_TIMER,
};

/// One wheel notch, in `mouseData` units on Windows.
const WHEEL_DELTA: f64 = 120.0;

/// `VK_SHIFT`, `VK_CONTROL`, `VK_MENU`, `VK_LWIN` — the keys we press to make
/// the injected modifier state match the peer's.
const MODIFIER_VKS: [(u8, u16); 4] = [
    (Modifiers::SHIFT, 0x10), // VK_SHIFT
    (Modifiers::CTRL, 0x11),  // VK_CONTROL
    (Modifiers::ALT, 0x12),   // VK_MENU
    (Modifiers::META, 0x5B),  // VK_LWIN
];

// ---------------------------------------------------------------------------
// Shared capture state
// ---------------------------------------------------------------------------

/// Everything the hook thread needs to know about the engine, kept in one
/// cheap-to-clone snapshot updated from `Host::on_status`.
#[derive(Clone, Copy, Debug)]
pub struct CaptureState {
    /// UI switch: when false the hooks let everything through untouched.
    pub capture_enabled: bool,
    /// True while this side's input is forwarded to the other computer (the
    /// shared cursor is on the other screen).
    pub forwarding: bool,
    /// Local pointer geometry is used by the edge tracker; the value itself
    /// is only informational here.
    pub remote_edge: Option<Edge>,
    /// Clipboard syncing enabled (read by the clipboard poller).
    pub clipboard_enabled: bool,
    /// Hotkey chord, as HID usage + modifier bits.
    pub hotkey_mods: u8,
    pub hotkey_usage: u16,
}

impl Default for CaptureState {
    fn default() -> Self {
        let hotkey = flowlink_core::types::Hotkey::default();
        CaptureState {
            capture_enabled: true,
            forwarding: false,
            remote_edge: None,
            clipboard_enabled: true,
            hotkey_mods: hotkey.modifiers,
            hotkey_usage: hotkey.usage,
        }
    }
}

impl CaptureState {
    /// True when `usage` with `mods` is exactly the configured hotkey chord.
    fn is_hotkey(&self, usage: u16, mods: u8) -> bool {
        usage == self.hotkey_usage && mods == self.hotkey_mods
    }
}

/// Process-wide handles the hook callbacks and the injection helpers need.
/// They are set once by [`start`]; the hooks cannot be installed before that.
static ENGINE: OnceLock<Arc<Engine>> = OnceLock::new();
static CAPTURE_STATE: OnceLock<Arc<RwLock<CaptureState>>> = OnceLock::new();

/// Hook handles, so the pump can unhook on the way out.
static KEYBOARD_HOOK: AtomicIsize = AtomicIsize::new(0);
static MOUSE_HOOK: AtomicIsize = AtomicIsize::new(0);

/// Watchdog timer ticks (500 ms each): screen-geometry refresh and the
/// "pointer pinned to an edge while forwarding" rescue.
static WATCHDOG_TICKS: AtomicU32 = AtomicU32::new(0);

/// Injection failures and the one-shot "input is blocked" warning.
static SENDINPUT_FAILURES: AtomicU64 = AtomicU64::new(0);
static SENDINPUT_WARNED: AtomicBool = AtomicBool::new(false);

/// True between the first hotkey key-down and its key-up. Windows repeats
/// `WM_KEYDOWN` while a key is held; without this guard, holding the toggle
/// chord would flip the cursor back and forth at the repeat rate.
static HOTKEY_HELD: AtomicBool = AtomicBool::new(false);

/// VK codes whose key-down was delivered to the local OS (this side was
/// focused). If focus moves away before the release, that release must still
/// pass through locally or the local app sees a stuck key.
static LOCAL_HELD_KEYS: LazyLock<Mutex<HashSet<u16>>> =
    LazyLock::new(|| Mutex::new(HashSet::new()));

/// Mouse buttons whose press was delivered locally (1/2/4); their release
/// must reach the local OS even after focus moved away, or the drag sticks.
static LOCAL_HELD_BUTTONS: LazyLock<Mutex<HashSet<u16>>> =
    LazyLock::new(|| Mutex::new(HashSet::new()));

/// Cursor tracking for relative mouse deltas (physical, unmirrored coords).
static MOUSE_SEEN: AtomicBool = AtomicBool::new(false);
static LAST_MOUSE_X: AtomicI32 = AtomicI32::new(0);
static LAST_MOUSE_Y: AtomicI32 = AtomicI32::new(0);

/// After `Enter` warps the pointer, the hook can still have a move event in
/// flight whose absolute point predates the warp. Zeroing deltas for a short
/// window (and re-baselining on each event) prevents that stale event from
/// being reported as a screen-wide jump — which used to bounce focus back.
static POSITION_GUARD_UNTIL_MS: AtomicU64 = AtomicU64::new(0);

/// Handle for the dedicated hook thread.
pub struct CaptureHandle {
    thread_id: u32,
    stopped: AtomicBool,
}

impl CaptureHandle {
    /// Ask the hook thread to unhook and exit. Safe to call more than once.
    pub fn stop(&self) {
        if self.stopped.swap(true, Ordering::SeqCst) {
            return;
        }
        // SAFETY: PostThreadMessageW is safe with any thread id; if the thread
        // is already gone the call fails and there is nothing left to do.
        unsafe {
            let _ = PostThreadMessageW(self.thread_id, WM_QUIT, WPARAM(0), LPARAM(0));
        }
    }
}

/// Ask Win32 for per-monitor DPI awareness.
///
/// Without this the process may be DPI-virtualised and `GetSystemMetrics`
/// would report scaled (logical) pixels, which would put the edge tracker and
/// the injection geometry in different coordinate spaces. Failing is fine:
/// eframe/winit set the same awareness, and the call is idempotent.
pub fn init_process_dpi_awareness() {
    // SAFETY: no preconditions.
    let result =
        unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
    if let Err(err) = result {
        trace!("DPI awareness already set or unavailable: {err}");
    }
}

/// The local virtual screen: the union of all monitors, in physical pixels.
///
/// This is the coordinate space the core's edge math expects (see
/// `HostInput::MouseMove`), with `(0, 0)` at the top-left of the union.
pub fn local_screen() -> ScreenInfo {
    // SAFETY: GetSystemMetrics has no preconditions.
    let (width, height) = unsafe {
        (
            GetSystemMetrics(SM_CXVIRTUALSCREEN),
            GetSystemMetrics(SM_CYVIRTUALSCREEN),
        )
    };
    ScreenInfo {
        width: width.max(1) as f64,
        height: height.max(1) as f64,
        // Windows geometry is already in physical pixels; there is no extra
        // scaling factor to report.
        scale: 1.0,
    }
}

/// Offset of the virtual-screen origin relative to the primary monitor.
fn virtual_origin() -> (i32, i32) {
    // SAFETY: GetSystemMetrics has no preconditions.
    unsafe {
        (
            GetSystemMetrics(SM_XVIRTUALSCREEN),
            GetSystemMetrics(SM_YVIRTUALSCREEN),
        )
    }
}

/// Update the engine snapshot used by the hook thread and hide/show the local
/// cursor while this side's input is forwarded to the other computer.
pub fn update_forwarding(forwarding: bool) {
    if let Some(state) = CAPTURE_STATE.get() {
        let mut state = state.write();
        if state.forwarding != forwarding {
            info!(
                forwarding,
                "input capture {}",
                if forwarding {
                    "forwarded to the other computer"
                } else {
                    "local again"
                }
            );
        }
        state.forwarding = forwarding;
    }
    let mut injection = INJECTION.lock();
    set_cursor_hidden(&mut injection, forwarding);
}

/// Tell the injector which local edge faces the peer (from the engine
/// status). This matters at session start, when the server owns the cursor
/// but no `Enter` has arrived yet to seed the tracker.
pub fn set_peer_edge(edge: Option<Edge>) {
    let mut state = INJECTION.lock();
    state.edge.set_peer_edge(edge);
}

/// Enable/disable hook processing (UI switch or shutdown).
pub fn set_capture_enabled(enabled: bool) {
    if let Some(state) = CAPTURE_STATE.get() {
        state.write().capture_enabled = enabled;
    }
    if !enabled {
        LOCAL_HELD_KEYS.lock().clear();
        LOCAL_HELD_BUTTONS.lock().clear();
        HOTKEY_HELD.store(false, Ordering::Relaxed);
    }
}

/// Release every key/button we injected and restore the cursor.
///
/// Called on shutdown so nothing stays stuck on the machine after FlowLink
/// exits.
pub fn shutdown() {
    set_capture_enabled(false);
    let mut injection = INJECTION.lock();
    set_cursor_hidden(&mut injection, false);
    release_all_inner(&mut injection);
}

// ---------------------------------------------------------------------------
// Hook thread
// ---------------------------------------------------------------------------

/// Install the low-level hooks on a dedicated thread and start its message
/// pump. Blocks until the hooks are installed (or installation failed).
pub fn start(engine: Arc<Engine>, state: Arc<RwLock<CaptureState>>) -> Result<CaptureHandle> {
    if ENGINE.set(engine).is_err() {
        bail!("input hooks already started");
    }
    if CAPTURE_STATE.set(state).is_err() {
        bail!("input capture state already installed");
    }

    let (ready_tx, ready_rx) = mpsc::channel::<Result<u32, String>>();
    thread::Builder::new()
        .name("flowlink-hooks".to_string())
        .spawn(move || hook_thread(ready_tx))
        .context("spawning the input hook thread")?;

    match ready_rx.recv_timeout(Duration::from_secs(5)) {
        Ok(Ok(thread_id)) => {
            info!(thread_id, "input hooks installed");
            Ok(CaptureHandle {
                thread_id,
                stopped: AtomicBool::new(false),
            })
        }
        Ok(Err(message)) => bail!("{message}"),
        Err(err) => bail!("input hook thread did not report readiness: {err}"),
    }
}

/// Hook thread body: install, announce readiness, pump messages.
fn hook_thread(ready: mpsc::Sender<Result<u32, String>>) {
    let thread_id = match unsafe { install_hooks() } {
        Ok(thread_id) => thread_id,
        Err(err) => {
            let _ = ready.send(Err(format!("{err:#}")));
            return;
        }
    };
    if ready.send(Ok(thread_id)).is_err() {
        // Nobody is waiting for us anymore — the caller gave up.
        unsafe { unhook() };
        return;
    }
    unsafe { message_pump() };
}

/// Install both low-level hooks. Must run on the hook thread.
unsafe fn install_hooks() -> Result<u32> {
    // The module handle is only needed to satisfy the API: low-level hooks are
    // called in the context of the installing thread, so the handle of the
    // running executable is the right one.
    let module = GetModuleHandleW(PCWSTR::null()).context("GetModuleHandleW failed")?;

    let keyboard = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_proc), module, 0)
        .context("installing the keyboard hook")?;
    KEYBOARD_HOOK.store(keyboard.0 as isize, Ordering::SeqCst);

    let mouse = match SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_proc), module, 0) {
        Ok(hook) => hook,
        Err(err) => {
            unhook();
            return Err(err).context("installing the mouse hook");
        }
    };
    MOUSE_HOOK.store(mouse.0 as isize, Ordering::SeqCst);

    // Create this thread's message queue *before* anyone can post `WM_QUIT` to
    // it (PostThreadMessageW fails for threads without a queue).
    let mut msg = MSG::default();
    let _ = PeekMessageW(&mut msg, HWND::default(), 0, 0, PM_NOREMOVE);

    // Periodic rescue timer: Windows stops reporting position changes once
    // the (hidden) pointer is pinned to a desktop edge, so forwarding can
    // otherwise freeze at that edge. The timer also keeps the engine's screen
    // geometry in sync after resolution / monitor changes.
    if SetTimer(HWND::default(), 1, 500, None) == 0 {
        tracing::debug!("watchdog timer could not be created");
    }

    Ok(GetCurrentThreadId())
}

/// Remove both hooks (idempotent).
unsafe fn unhook() {
    for slot in [&KEYBOARD_HOOK, &MOUSE_HOOK] {
        let raw = slot.swap(0, Ordering::SeqCst);
        if raw != 0 {
            let _ = UnhookWindowsHookEx(HHOOK(raw as *mut c_void));
        }
    }
}

/// Standard Win32 message pump; returns on `WM_QUIT` (posted by
/// [`CaptureHandle::stop`]) or on a fatal `GetMessageW` error.
unsafe fn message_pump() {
    let mut msg = MSG::default();
    loop {
        let result = GetMessageW(&mut msg, HWND::default(), 0, 0);
        if result.0 <= 0 {
            // 0 = WM_QUIT, -1 = error.
            break;
        }
        if msg.message == WM_TIMER {
            watchdog_tick();
            continue;
        }
        let _ = TranslateMessage(&msg);
        let _ = DispatchMessageW(&msg);
    }
    unhook();
    info!("input hook thread stopped");
}

/// Runs on the hook thread every 500 ms.
fn watchdog_tick() {
    let tick = WATCHDOG_TICKS.fetch_add(1, Ordering::Relaxed);
    // Refresh screen geometry every 2 s: a resolution change, DPI change or
    // monitor hot-plug must never leave the edge math in a stale coordinate
    // space.
    if tick % 4 == 0 {
        if let Some(engine) = ENGINE.get() {
            engine.execute(Cmd::SetLocalScreen(local_screen()));
        }
    }
    let forwarding = CAPTURE_STATE
        .get()
        .map(|state| state.read().forwarding)
        .unwrap_or(false);
    if forwarding {
        recenter_if_outside_runway();
    }
}

/// Pass the event to the next hook in the chain.
fn pass_through(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    // SAFETY: the parameters are exactly the ones Win32 handed us.
    unsafe { CallNextHookEx(HHOOK(std::ptr::null_mut()), code, wparam, lparam) }
}

/// `WH_KEYBOARD_LL` callback.
unsafe extern "system" fn keyboard_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code < 0 {
        return pass_through(code, wparam, lparam);
    }
    let (Some(engine), Some(state)) = (ENGINE.get(), CAPTURE_STATE.get()) else {
        return pass_through(code, wparam, lparam);
    };

    let info = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
    // Ignore our own injected events (and those of other automation tools).
    if info.flags.0 & LLKHF_INJECTED.0 != 0 {
        return pass_through(code, wparam, lparam);
    }

    let message = wparam.0 as u32;
    let down = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
    let up = message == WM_KEYUP || message == WM_SYSKEYUP;
    if !down && !up {
        return pass_through(code, wparam, lparam);
    }

    let snapshot = *state.read();
    if !snapshot.capture_enabled {
        // Hooks paused: do not leave the repeat guard latched from a chord
        // that was held while capture stopped.
        HOTKEY_HELD.store(false, Ordering::Relaxed);
        return pass_through(code, wparam, lparam);
    }

    // Track whether this key-down was delivered locally. Repeats while
    // focused just keep it in the set; a key pressed while forwarding is not
    // local and its release stays swallowed.
    let vk = info.vkCode as u16;
    let release_locally = {
        let mut held = LOCAL_HELD_KEYS.lock();
        if down {
            if !snapshot.forwarding {
                held.insert(vk);
            }
            false
        } else {
            held.remove(&vk)
        }
    };

    let Some(usage) = super::hid::vk_to_hid(vk) else {
        // The keyboard belongs to the focused computer: an unmapped key
        // cannot be forwarded, but it must not leak into this machine's
        // local apps while this side is forwarding either. Its release still
        // passes if its press was local.
        if snapshot.forwarding && !release_locally {
            return LRESULT(1);
        }
        return pass_through(code, wparam, lparam);
    };
    let mods = super::hid::modifiers_from_vk_states();
    let is_hotkey = snapshot.is_hotkey(usage, mods);

    // The toggle chord is ours: fire once on the initial key-down, swallow
    // auto-repeat, and swallow the release even if the modifiers were let go
    // first (otherwise the next press could be ignored by the repeat guard).
    if usage == snapshot.hotkey_usage {
        if down {
            if is_hotkey {
                if !HOTKEY_HELD.swap(true, Ordering::Relaxed) {
                    engine.execute(Cmd::HostInput(HostInput::Key {
                        usage,
                        down: true,
                        modifiers: Modifiers::from_bits(mods),
                    }));
                }
                return LRESULT(1);
            }
        } else {
            let was_held = HOTKEY_HELD.swap(false, Ordering::Relaxed);
            if was_held || is_hotkey {
                // A key that was already delivered locally must be released
                // locally even if its chord became the hotkey afterwards.
                if release_locally {
                    return pass_through(code, wparam, lparam);
                }
                return LRESULT(1);
            }
        }
    }

    // Every other key is fed to the engine: while this side owns the cursor
    // the engine ignores it (the shell passes it through), while the cursor is
    // on the peer the engine forwards it there.
    engine.execute(Cmd::HostInput(HostInput::Key {
        usage,
        down,
        modifiers: Modifiers::from_bits(mods),
    }));

    // Swallow anything sent to the peer, except the release of a key that
    // was pressed locally before focus moved away (that release clears the
    // local OS state).
    if snapshot.forwarding && !release_locally {
        return LRESULT(1);
    }
    pass_through(code, wparam, lparam)
}

/// `WH_MOUSE_LL` callback.
unsafe extern "system" fn mouse_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code < 0 {
        return pass_through(code, wparam, lparam);
    }
    let (Some(engine), Some(state)) = (ENGINE.get(), CAPTURE_STATE.get()) else {
        return pass_through(code, wparam, lparam);
    };

    let info = &*(lparam.0 as *const MSLLHOOKSTRUCT);
    if info.flags & LLMHF_INJECTED != 0 {
        return pass_through(code, wparam, lparam);
    }

    let snapshot = *state.read();
    if !snapshot.capture_enabled {
        return pass_through(code, wparam, lparam);
    }

    // Every pointer event is fed to the engine: the focused side uses it for
    // edge detection, the other side forwards it to the peer.
    match wparam.0 as u32 {
        WM_MOUSEMOVE => {
            // Compute the real delta *before* any recenter warp: the warp
            // resets the baseline, so recentering first used to turn the next
            // event into a huge spurious jump (the cursor visibly teleported
            // whenever forwarding neared a screen edge).
            let (mut dx, mut dy) = mouse_delta(info.pt.x, info.pt.y);
            // Events already in flight when Enter warped the pointer still
            // carry the old absolute position; their position-derived delta
            // is meaningless. Drop it and keep re-baselining until the warp
            // has settled.
            if flowlink_core::util::now_ms() < POSITION_GUARD_UNTIL_MS.load(Ordering::Relaxed) {
                dx = 0.0;
                dy = 0.0;
            }
            // While forwarding, the physical pointer is clamped at the
            // desktop edges. Position-based deltas then read as zero and the
            // remote cursor freezes (keyboard keeps working, so it looks like
            // "the cursor doesn't move"). Keep the hidden pointer inside a
            // runway so deltas always flow.
            if snapshot.forwarding {
                recenter_if_outside_runway();
            }
            let (origin_x, origin_y) = virtual_origin();
            engine.execute(Cmd::HostInput(HostInput::MouseMove {
                x_px: (info.pt.x - origin_x) as f64,
                y_px: (info.pt.y - origin_y) as f64,
                dx,
                dy,
            }));
            if snapshot.forwarding {
                return LRESULT(1);
            }
            pass_through(code, wparam, lparam)
        }
        WM_LBUTTONDOWN | WM_LBUTTONUP | WM_RBUTTONDOWN | WM_RBUTTONUP | WM_MBUTTONDOWN
        | WM_MBUTTONUP => {
            let button = match wparam.0 as u32 {
                WM_LBUTTONDOWN | WM_LBUTTONUP => MouseButton::Left,
                WM_RBUTTONDOWN | WM_RBUTTONUP => MouseButton::Right,
                _ => MouseButton::Middle,
            };
            let down = matches!(
                wparam.0 as u32,
                WM_LBUTTONDOWN | WM_RBUTTONDOWN | WM_MBUTTONDOWN
            );
            // A button event may move the pointer implicitly; refresh the
            // delta baseline so the next move is not inflated.
            let _ = mouse_delta(info.pt.x, info.pt.y);
            let button_id = match button {
                MouseButton::Left => 1u16,
                MouseButton::Right => 2,
                MouseButton::Middle => 4,
            };
            let release_locally = {
                let mut held = LOCAL_HELD_BUTTONS.lock();
                if down {
                    if !snapshot.forwarding {
                        held.insert(button_id);
                    }
                    false
                } else {
                    held.remove(&button_id)
                }
            };
            engine.execute(Cmd::HostInput(HostInput::MouseButton {
                button,
                down,
                modifiers: Modifiers::from_bits(super::hid::modifiers_from_vk_states()),
            }));
            if snapshot.forwarding && !release_locally {
                return LRESULT(1);
            }
            pass_through(code, wparam, lparam)
        }
        WM_MOUSEWHEEL | WM_MOUSEHWHEEL => {
            // `mouseData` carries a signed delta in the high word; one notch =
            // 120 units. Wire convention (docs/PROTOCOL.md): positive `dy` =
            // wheel away from the user (up) — the same sign Windows uses for
            // `mouseData` — while positive `dx` means "towards the left", so
            // the horizontal wheel is negated here.
            let high = (info.mouseData >> 16) as u16;
            let notches = (high as i16) as f64 / WHEEL_DELTA;
            let (dx, dy) = if wparam.0 as u32 == WM_MOUSEWHEEL {
                (0.0, notches)
            } else {
                (-notches, 0.0)
            };
            engine.execute(Cmd::HostInput(HostInput::Scroll {
                dx,
                dy,
                // A wheel tick is a discrete event; trackpads are not
                // available over these hook messages.
                precise: false,
            }));
            if snapshot.forwarding {
                return LRESULT(1);
            }
            pass_through(code, wparam, lparam)
        }
        _ => pass_through(code, wparam, lparam),
    }
}

/// Pixel delta of a mouse move, using the module-level baseline.
fn mouse_delta(x: i32, y: i32) -> (f64, f64) {
    if !MOUSE_SEEN.swap(true, Ordering::Relaxed) {
        LAST_MOUSE_X.store(x, Ordering::Relaxed);
        LAST_MOUSE_Y.store(y, Ordering::Relaxed);
        return (0.0, 0.0);
    }
    let last_x = LAST_MOUSE_X.swap(x, Ordering::Relaxed);
    let last_y = LAST_MOUSE_Y.swap(y, Ordering::Relaxed);
    ((x - last_x) as f64, (y - last_y) as f64)
}

/// How far from the virtual-screen edge the tucked-away pointer is kept while
/// the server drives the peer.
const RUNWAY_MARGIN: i32 = 160;

/// Re-center the (hidden) physical pointer when it approaches a desktop edge.
///
/// Windows clamps the cursor at screen boundaries, so `WM_MOUSEMOVE` stops
/// reporting position changes once the pointer is pinned to an edge — relative
/// deltas become zero and the remote cursor freezes. Warping the pointer back
/// to the middle keeps deltas flowing; the user never sees it because the
/// local cursor is hidden for the duration of remote control.
fn recenter_if_outside_runway() {
    if !CAPTURE_STATE
        .get()
        .map(|state| state.read().forwarding)
        .unwrap_or(false)
    {
        return;
    }
    let width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
    let height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };
    if width <= RUNWAY_MARGIN * 2 || height <= RUNWAY_MARGIN * 2 {
        return;
    }
    let (origin_x, origin_y) = virtual_origin();
    let mut point = POINT::default();
    // SAFETY: `point` is a valid writable POINT.
    if unsafe { GetCursorPos(&mut point) }.is_err() {
        return;
    }
    let inside = point.x >= origin_x + RUNWAY_MARGIN
        && point.x < origin_x + width - RUNWAY_MARGIN
        && point.y >= origin_y + RUNWAY_MARGIN
        && point.y < origin_y + height - RUNWAY_MARGIN;
    if inside {
        return;
    }
    let center = POINT {
        x: origin_x + width / 2,
        y: origin_y + height / 2,
    };
    // SAFETY: SetCursorPos has no preconditions beyond a valid point.
    if unsafe { SetCursorPos(center.x, center.y) }.is_ok() {
        // The warp is not user motion: reset the delta baseline so the next
        // event does not look like a jump across half the screen.
        LAST_MOUSE_X.store(center.x, Ordering::Relaxed);
        LAST_MOUSE_Y.store(center.y, Ordering::Relaxed);
    }
}

// ---------------------------------------------------------------------------
// Injection
// ---------------------------------------------------------------------------

/// Injection bookkeeping: what we pressed, and the client-side return edge.
#[derive(Default)]
struct InjectionState {
    /// HID usages of non-modifier keys currently held down by the peer.
    pressed_keys: HashSet<u16>,
    /// Modifier *flags* (bits of [`Modifiers`]) we have pressed ourselves.
    mods_down: u8,
    /// HID button usages (1/2/4) currently held down by the peer.
    pressed_buttons: HashSet<u8>,
    /// Whether [`set_cursor_hidden`] currently holds the cursor hidden.
    cursor_hidden: bool,
    /// Injected-edge return state (see `platform::edge_push`).
    edge: super::edge_push::EdgePushTracker,
}

static INJECTION: LazyLock<Mutex<InjectionState>> =
    LazyLock::new(|| Mutex::new(InjectionState::default()));

/// Apply an event coming from the peer to the local machine.
pub fn inject(event: &InputEvent) {
    let mut state = INJECTION.lock();
    match *event {
        InputEvent::Enter { edge, along_norm } => enter(&mut state, edge, along_norm),
        InputEvent::MouseMove { dx, dy } => {
            detect_injected_edge(&mut state, dx as f64, dy as f64);
            let (dx, dy) = (dx.round() as i32, dy.round() as i32);
            if dx != 0 || dy != 0 {
                send(&[mouse_input(
                    dx,
                    dy,
                    MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE,
                    0,
                )]);
            }
        }
        InputEvent::MouseButton { button, down, .. } => button_event(&mut state, button, down),
        InputEvent::Scroll { dx, dy, precise } => scroll(dx, dy, precise),
        InputEvent::Key {
            usage,
            down,
            modifiers,
        } => key_event(&mut state, usage, down, modifiers),
        InputEvent::ReleaseAll => release_all_inner(&mut state),
    }
}

/// Place the pointer at the mirrored entry position for `edge`, a comfortable
/// distance inside the screen so a sideways twitch cannot immediately hand
/// focus back.
fn enter(state: &mut InjectionState, edge: Edge, along_norm: f32) {
    const INSET: f64 = 24.0;
    let screen = local_screen();
    let (x, y) = flowlink_core::edge::enter_position(edge, along_norm, screen);
    let (mut target_x, mut target_y) = (x, y);
    match edge {
        // Exited the peer to the right → we appear at the left edge.
        Edge::Right => target_x += INSET,
        Edge::Left => target_x -= INSET,
        // Exited upwards → we appear at the bottom edge.
        Edge::Top => target_y += INSET,
        Edge::Bottom => target_y -= INSET,
    }
    let target_x = target_x.clamp(0.0, screen.width.max(1.0));
    let target_y = target_y.clamp(0.0, screen.height.max(1.0));
    let (origin_x, origin_y) = virtual_origin();
    // `SetCursorPos` is relative to the primary monitor; the edge math works
    // in union-screen pixels.
    let target_x = target_x.round() as i32 + origin_x;
    let target_y = target_y.round() as i32 + origin_y;
    // The cursor is coming to this computer: show it *before* the pointer
    // jumps (the status callback that also does this is asynchronous).
    set_cursor_hidden(state, false);
    // SAFETY: SetCursorPos has no preconditions beyond a valid point.
    let result = unsafe { SetCursorPos(target_x, target_y) };
    if let Err(err) = result {
        warn!("SetCursorPos({target_x}, {target_y}) failed: {err}");
    }
    // The warp is not user motion. Reset the capture delta baseline or the
    // first real move would be reported as a jump across the screen (and could
    // immediately push focus back again).
    MOUSE_SEEN.store(true, Ordering::Relaxed);
    LAST_MOUSE_X.store(target_x, Ordering::Relaxed);
    LAST_MOUSE_Y.store(target_y, Ordering::Relaxed);
    POSITION_GUARD_UNTIL_MS.store(flowlink_core::util::now_ms() + 80, Ordering::Relaxed);

    // Return-edge state for this stay: the edge the cursor came in through is
    // the mirror of the sender's exit edge. Re-arm only after moving inward.
    state.edge.enter(edge);
}

/// Detect a deliberate outward push at the peer-facing edge while this side
/// owns the cursor.
///
/// The physical mouse may be on the *other* computer, so the local low-level
/// hook never sees those movements (synthetic events are deliberately
/// ignored there). The injector is the only place that can notice the cursor
/// being pinned to the edge and hand the shared cursor back. The decision
/// itself is the tested `EdgePushTracker`; this function only converts Win32
/// coordinates into the union-local space it expects.
fn detect_injected_edge(state: &mut InjectionState, dx: f64, dy: f64) {
    let screen = local_screen();
    if screen.width <= 1.0 || screen.height <= 1.0 {
        return;
    }
    let (origin_x, origin_y) = virtual_origin();
    let mut point = POINT::default();
    // SAFETY: `point` is a valid writable POINT.
    if unsafe { GetCursorPos(&mut point) }.is_err() {
        return;
    }
    let local_x = (point.x - origin_x) as f64;
    let local_y = (point.y - origin_y) as f64;
    let Some((edge, along)) = state.edge.feed(
        local_x,
        local_y,
        dx.round(),
        dy.round(),
        screen.width,
        screen.height,
        flowlink_core::util::now_ms(),
    ) else {
        return;
    };
    if let Some(engine) = ENGINE.get() {
        tracing::info!(
            edge = edge.as_str(),
            along,
            "injected pointer crossed the peer edge — handing back"
        );
        engine.execute(Cmd::HandOff {
            edge,
            along_norm: along,
        });
    }
}

/// Keep the injected modifier state in sync with the peer's view.
fn sync_modifiers(state: &mut InjectionState, target: Modifiers) {
    let target = target.bits();
    for (flag, vk) in MODIFIER_VKS {
        let wanted = target & flag != 0;
        let held = state.mods_down & flag != 0;
        if wanted && !held {
            send(&[key_input(vk, false)]);
            state.mods_down |= flag;
        } else if !wanted && held {
            send(&[key_input(vk, true)]);
            state.mods_down &= !flag;
        }
    }
}

/// Inject a (non-modifier) key. Modifier keys are handled through the
/// `modifiers` flags so both sides agree on the logical modifier state, even
/// when the peer used the "other" shift/ctrl/alt key.
fn key_event(state: &mut InjectionState, usage: u16, down: bool, modifiers: Modifiers) {
    // The `modifiers` field is authoritative, but a modifier's own key event
    // also states its direction — fold that in so a peer that omits the flag
    // still presses the right key.
    let mut target = modifiers.bits();
    if let Some(flag) = flowlink_core::hid::modifier_flag(usage) {
        target = if down { target | flag } else { target & !flag };
    }
    sync_modifiers(state, Modifiers::from_bits(target));
    if flowlink_core::hid::is_modifier(usage) {
        return;
    }
    let Some(vk) = super::hid::hid_to_vk(usage) else {
        debug!(usage, "no VK mapping for key — skipped");
        return;
    };
    if down {
        state.pressed_keys.insert(usage);
    } else {
        state.pressed_keys.remove(&usage);
    }
    send(&[key_input(vk, !down)]);
}

/// Inject a mouse button press/release.
fn button_event(state: &mut InjectionState, button: MouseButton, down: bool) {
    let (flags, hid_usage) = match (button, down) {
        (MouseButton::Left, true) => (MOUSEEVENTF_LEFTDOWN, 1),
        (MouseButton::Left, false) => (MOUSEEVENTF_LEFTUP, 1),
        (MouseButton::Right, true) => (MOUSEEVENTF_RIGHTDOWN, 2),
        (MouseButton::Right, false) => (MOUSEEVENTF_RIGHTUP, 2),
        (MouseButton::Middle, true) => (MOUSEEVENTF_MIDDLEDOWN, 4),
        (MouseButton::Middle, false) => (MOUSEEVENTF_MIDDLEUP, 4),
    };
    if down {
        state.pressed_buttons.insert(hid_usage);
    } else {
        state.pressed_buttons.remove(&hid_usage);
    }
    send(&[mouse_input(0, 0, flags, 0)]);
}

/// Inject scroll deltas.
///
/// Wire convention (docs/PROTOCOL.md): positive `dy` = wheel away from the
/// user (up); positive `dx` = wheel towards the left. Values are in notches
/// when `precise == false` (one notch per physical tick) and in pixels when
/// `precise == true` (trackpads). Windows' `mouseData` counts one
/// `WHEEL_DELTA` per notch, and positive `HWHEEL` means "right", so:
///
/// * vertical passes through with the same sign;
/// * horizontal is negated.
fn scroll(dx: f32, dy: f32, precise: bool) {
    let to_notches = |value: f64| if precise { value / 12.0 } else { value };
    if dy != 0.0 {
        let data = to_mouse_data(to_notches(dy as f64) * WHEEL_DELTA);
        send(&[mouse_input(0, 0, MOUSEEVENTF_WHEEL, data)]);
    }
    if dx != 0.0 {
        let data = to_mouse_data(-to_notches(dx as f64) * WHEEL_DELTA);
        send(&[mouse_input(0, 0, MOUSEEVENTF_HWHEEL, data)]);
    }
}

/// Convert a wheel delta into the signed `mouseData` bit pattern.
fn to_mouse_data(value: f64) -> u32 {
    let clamped = value.round().clamp(i32::MIN as f64, i32::MAX as f64) as i32;
    clamped as u32
}

/// Release every key/button the peer currently holds.
///
/// Cursor visibility is deliberately *not* touched here: it is driven by the
/// forwarding state alone. Showing it on every release used to make the cursor
/// flash when focus changed (and could leave it visible while forwarding).
fn release_all_inner(state: &mut InjectionState) {
    for usage in state.pressed_keys.drain() {
        if let Some(vk) = super::hid::hid_to_vk(usage) {
            send(&[key_input(vk, true)]);
        }
    }
    for (flag, vk) in MODIFIER_VKS {
        if state.mods_down & flag != 0 {
            send(&[key_input(vk, true)]);
            state.mods_down &= !flag;
        }
    }
    for hid_usage in state.pressed_buttons.drain() {
        let flags = match hid_usage {
            1 => MOUSEEVENTF_LEFTUP,
            2 => MOUSEEVENTF_RIGHTUP,
            4 => MOUSEEVENTF_MIDDLEUP,
            _ => continue,
        };
        send(&[mouse_input(0, 0, flags, 0)]);
    }
}

/// Show/hide the local cursor, guarded so the display counter stays balanced.
///
/// `ShowCursor`'s display counter is process-wide and may have been moved by
/// other code, so a single call is not guaranteed to flip the cursor: hide
/// until the count is negative and restore until it is non-negative.
fn set_cursor_hidden(state: &mut InjectionState, hidden: bool) {
    if state.cursor_hidden == hidden {
        return;
    }
    state.cursor_hidden = hidden;
    let mut guard = 0;
    // SAFETY: ShowCursor only adjusts this process' cursor display counter.
    unsafe {
        if hidden {
            while ShowCursor(false) >= 0 && guard < 32 {
                guard += 1;
            }
        } else {
            while ShowCursor(true) < 0 && guard < 32 {
                guard += 1;
            }
        }
    }
    trace!(hidden, guard, "local cursor visibility changed");
}

/// Send a batch of synthetic input events.
fn send(inputs: &[INPUT]) {
    // SAFETY: `inputs` is a valid slice and `size_of::<INPUT>()` is the size
    // Win32 expects for the structure version we link against.
    let sent = unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) };
    if sent as usize != inputs.len() {
        // Normal while a higher-integrity window (UAC) owns the input
        // desktop, or when the session is locked — but it is also the reason
        // "the other keyboard types nothing", so surface it once.
        let failures = SENDINPUT_FAILURES.fetch_add(1, Ordering::Relaxed) + 1;
        trace!(
            sent,
            expected = inputs.len(),
            failures,
            "SendInput incomplete"
        );
        if !SENDINPUT_WARNED.swap(true, Ordering::Relaxed) {
            warn!(
                sent,
                expected = inputs.len(),
                "SendInput was blocked — an elevated window or the secure desktop owns input; \
                 run FlowLink as administrator to type into elevated apps"
            );
            if let Some(engine) = ENGINE.get() {
                engine.notice(
                    "input",
                    "Windows blocked synthetic input (an elevated app or the secure desktop is focused). Run FlowLink as administrator to control elevated windows.",
                );
            }
        }
    }
}

/// Build a mouse `INPUT` record.
fn mouse_input(dx: i32, dy: i32, flags: MOUSE_EVENT_FLAGS, data: u32) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx,
                dy,
                mouseData: data,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

/// Build a keyboard `INPUT` record from a virtual-key code.
fn key_input(vk: u16, up: bool) -> INPUT {
    // Extended keys (navigation cluster, arrows, numpad divide/numlock,
    // Windows/Apps keys, right Ctrl/Alt) need the extended flag or some apps
    // (and many games) do not see them at all.
    let extended = matches!(
        vk,
        0x21..=0x28 | 0x2D | 0x2E | 0x5B..=0x5D | 0x6F | 0x90
    );
    let mut flags = if up {
        KEYEVENTF_KEYUP
    } else {
        KEYBD_EVENT_FLAGS(0)
    };
    if extended {
        flags |= KEYEVENTF_EXTENDEDKEY;
    }
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

#[cfg(test)]
mod tests {
    use super::to_mouse_data;

    #[test]
    fn wheel_data_rounds_and_wraps() {
        assert_eq!(to_mouse_data(120.0), 120);
        assert_eq!(to_mouse_data(-120.0), (-120i32) as u32);
        assert_eq!(to_mouse_data(0.4), 0);
    }
}
