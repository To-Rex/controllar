"""Native Windows input backend using low-level hooks and SendInput.

WH_KEYBOARD_LL / WH_MOUSE_LL give the process access to system-wide input and
allow a native event to be swallowed. Synthetic events are tagged with
``dwExtraInfo`` and the OS injected flag, so they are never echoed.

Mouse movement is intentionally not suppressed while forwarding: the hidden
local cursor provides the deltas, and it is re-centered near screen boundaries.
On the passively controlled machine physical mouse motion is also allowed for a
few pixels so local input can reclaim focus; clicks, keys and wheel events stay
blocked until ownership changes.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Any

from .base import InputBackendError, InputPermissions
from .events import (
    InputButtonEvent,
    InputEvent,
    InputKeyEvent,
    InputMoveEvent,
    InputWheelEvent,
)
from .keys import (
    MODIFIER_KEYS,
    WIN_NAME_TO_KEY,
    WIN_VK_TO_NAME,
    modifier_aliases,
)

log = logging.getLogger(__name__)

_IS_WINDOWS = hasattr(ctypes, "WinDLL")
_INJECT_TAG = 0x544F5245

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
# Private thread message: apply the desired cursor visibility on the hook
# thread. ShowCursor's display counter belongs to the calling thread's input
# queue, so all hide/show work must run on ONE thread — the hook thread that
# owns the message loop — otherwise a show from a different thread cannot
# cancel an earlier hide.
OCR_NORMAL = 32512
WM_APP_CURSOR = 0x8001


WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
LLMHF_LOWER_IL_INJECTED = 0x02
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
WHEEL_DELTA = 120
SM_CXSCREEN = 0
SM_CYSCREEN = 1
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CAPITAL = 0x14
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t


if _IS_WINDOWS:  # pragma: no cover - native code is exercised on Windows
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hWnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", WPARAM),
            ("lParam", LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        HOOKPROC,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,
        ctypes.c_int,
        WPARAM,
        LPARAM,
    ]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, WPARAM, LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetKeyState.argtypes = [ctypes.c_int]
    user32.GetKeyState.restype = ctypes.c_short
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.mouse_event.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    user32.mouse_event.restype = None
    user32.keybd_event.argtypes = [
        ctypes.c_ubyte,
        ctypes.c_ubyte,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    user32.keybd_event.restype = None
    user32.ShowCursor.argtypes = [wintypes.BOOL]
    user32.ShowCursor.restype = ctypes.c_int
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.CopyIcon.argtypes = [wintypes.HANDLE]
    user32.CopyIcon.restype = wintypes.HANDLE
    user32.CreateCursor.argtypes = [
        wintypes.HINSTANCE,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.CreateCursor.restype = wintypes.HANDLE
    user32.SetSystemCursor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    user32.SetSystemCursor.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0, 0),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0, 0),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0, 0),
    "x1": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1, XBUTTON1),
    "x2": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2, XBUTTON2),
}


class WindowsInputBackend:
    name = "Windows low-level hooks"
    # Two mice, one device: while the PEER drives this machine, the local
    # physical mouse must still move the cursor directly (it is the cursor's
    # machine). While idle, the hidden cursor may roam freely without harm.
    # The default True would swallow every local move while receiving, which
    # froze the cursor AND starved the local return detector.
    suppress_moves_when_passive = False

    def __init__(self) -> None:
        if not _IS_WINDOWS:
            raise InputBackendError("the Windows input backend is only available on Windows")
        self._callback: Any = None
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._started_flag = False
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._lock = threading.RLock()
        self._keyboard_hook: Any = None
        self._mouse_hook: Any = None
        self._keyboard_proc: Any = None
        self._mouse_proc: Any = None
        self._physical_keys: set[str] = set()
        self._physical_mod_keys: set[str] = set()
        self._physical_buttons: set[str] = set()
        self._injected_keys_down: set[str] = set()
        self._injected_buttons_down: set[str] = set()
        self._last_pointer: tuple[int, int] | None = None
        self._cursor_hidden = False
        self._blank_cursor: Any = None
        self._saved_system_cursor: Any = None
        self._warp_target: tuple[int, int] | None = None
        self._warp_until = 0.0
        self._scroll_accum_x = 0.0
        self._scroll_accum_y = 0.0

    def preflight(self) -> InputPermissions:
        return InputPermissions(
            capture=True,
            injection=True,
            capture_detail="",
            injection_detail="",
        )

    def request_permissions(self) -> None:
        # Low-level hooks and SendInput do not require a per-app consent prompt
        # on Windows. Network access is handled by the firewall helper.
        return None

    # ---------------------------------------------------------------- lifecycle
    def start(self, callback: Any) -> None:
        if self._started_flag:
            return
        self._callback = callback
        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(
            target=self._message_loop,
            name="ToRexLink-WinInputHooks",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(5.0):
            raise InputBackendError("could not start Windows input hooks")
        if self._start_error is not None:
            raise InputBackendError(str(self._start_error))
        self._started_flag = True
        log.info("Windows low-level keyboard and mouse hooks active")

    def _message_loop(self) -> None:  # pragma: no cover - Windows native
        self._thread_id = kernel32.GetCurrentThreadId()
        self._keyboard_proc = HOOKPROC(self._keyboard_hook_proc)
        self._mouse_proc = HOOKPROC(self._mouse_hook_proc)
        try:
            module = kernel32.GetModuleHandleW(None)
            self._keyboard_hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._keyboard_proc, module, 0
            )
            if not self._keyboard_hook:
                raise OSError(ctypes.get_last_error(), "SetWindowsHookExW(keyboard) failed")
            self._mouse_hook = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._mouse_proc, module, 0
            )
            if not self._mouse_hook:
                raise OSError(ctypes.get_last_error(), "SetWindowsHookExW(mouse) failed")
            # Safety net for a crashed previous instance: if the last run died
            # while the cursor shape was blanked, the system arrow would stay
            # invisible. Re-setting the current arrow restores it cheaply and
            # heals that state (idempotent when it was never blanked).
            with contextlib.suppress(Exception):
                user32.SetSystemCursor(
                    user32.CopyIcon(
                        user32.LoadCursorW(None, ctypes.c_void_p(OCR_NORMAL))
                    ),
                    OCR_NORMAL,
                )
            self._ready.set()
            msg = MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_APP_CURSOR:
                    self._apply_cursor_visibility()
                    continue
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # noqa: BLE001
            self._start_error = exc
            log.exception("Windows input hook startup failed")
            self._ready.set()
        finally:
            if self._keyboard_hook:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
                self._keyboard_hook = None
            if self._mouse_hook:
                user32.UnhookWindowsHookEx(self._mouse_hook)
                self._mouse_hook = None

    def stop(self) -> None:
        if not self._started_flag and self._thread is None:
            return
        self._started_flag = False
        thread = self._thread
        self._thread = None
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.5)
        with self._lock:
            self.release_injected_keys()
            self.release_local_modifiers()
            # Apply inline: the hook thread may already have exited, and a
            # message posted after WM_QUIT would never be processed.
            self._cursor_hidden = False
            self._apply_cursor_visibility()
        self._callback = None
        log.info("Windows input hooks stopped")

    # ------------------------------------------------------------------- hooks
    def _mods(self) -> tuple[str, ...]:
        mods: list[str] = []
        for key in self._physical_mod_keys:
            mods.extend(modifier_aliases(key))
        if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
            mods.append("ctrl")
        if user32.GetAsyncKeyState(VK_MENU) & 0x8000:
            mods.append("alt")
        if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
            mods.append("shift")
        if (user32.GetAsyncKeyState(VK_LWIN) | user32.GetAsyncKeyState(VK_RWIN)) & 0x8000:
            mods.append("meta")
        if user32.GetKeyState(VK_CAPITAL) & 1:
            mods.append("caps")
        return tuple(dict.fromkeys(mods))

    def _keyboard_hook_proc(self, n_code: int, w_param: int, l_param: int) -> int:  # pragma: no cover
        if n_code < 0:
            return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)
        try:
            info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if (info.flags & LLKHF_INJECTED) or int(info.dwExtraInfo) == _INJECT_TAG:
                return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)
            message = int(w_param)
            pressed = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
            released = message in (WM_KEYUP, WM_SYSKEYUP)
            if not pressed and not released:
                return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)
            key = WIN_VK_TO_NAME.get(int(info.vkCode))
            if key is None:
                return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)
            if key in MODIFIER_KEYS or key == "caps_lock":
                if pressed:
                    self._physical_mod_keys.add(key)
                else:
                    self._physical_mod_keys.discard(key)
            repeat = False
            if pressed:
                repeat = key in self._physical_keys
                self._physical_keys.add(key)
            else:
                self._physical_keys.discard(key)
            event = InputKeyEvent(
                key=key,
                pressed=pressed,
                repeat=repeat,
                mods=self._mods(),
            )
            callback = self._callback
            if callback is None:
                return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)
            suppress = bool(callback(event, False))
            if suppress:
                return 1
        except Exception:  # noqa: BLE001 - never terminate the hook loop
            log.debug("Windows keyboard hook error", exc_info=True)
        return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)

    def _mouse_hook_proc(self, n_code: int, w_param: int, l_param: int) -> int:  # pragma: no cover
        if n_code < 0:
            return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)
        try:
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if (info.flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED)) or int(
                info.dwExtraInfo
            ) == _INJECT_TAG:
                return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)
            # The OS auto-shows the cursor on any physical activity, undoing
            # our hide (ShowCursor counter drifts back to >= 0). Re-assert the
            # hide on every physical mouse event while the cursor is meant to
            # be hidden — otherwise the idle machine's parked cursor becomes
            # visible and the user sees two cursors ("parallel cursors").
            if self._cursor_hidden:
                self._apply_cursor_visibility()
            message = int(w_param)
            point = (int(info.pt.x), int(info.pt.y))
            if (
                self._warp_target is not None
                and time.monotonic() <= self._warp_until
                and abs(point[0] - self._warp_target[0]) <= 2
                and abs(point[1] - self._warp_target[1]) <= 2
            ):
                self._warp_target = None
                self._last_pointer = point
                return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)
            last = self._last_pointer or point
            raw_dx = point[0] - last[0]
            raw_dy = point[1] - last[1]
            self._last_pointer = point
            screen_w, screen_h = self.screen_size()
            event: InputEvent | None = None
            if message == WM_MOUSEMOVE:
                event = InputMoveEvent(
                    dx=max(-2.0, min(2.0, raw_dx / max(1, screen_w))),
                    dy=max(-2.0, min(2.0, raw_dy / max(1, screen_h))),
                    x=point[0],
                    y=point[1],
                )
            elif message in (
                WM_LBUTTONDOWN,
                WM_LBUTTONUP,
                WM_RBUTTONDOWN,
                WM_RBUTTONUP,
                WM_MBUTTONDOWN,
                WM_MBUTTONUP,
                WM_XBUTTONDOWN,
                WM_XBUTTONUP,
            ):
                if message in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                    button = "left"
                elif message in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                    button = "right"
                elif message in (WM_MBUTTONDOWN, WM_MBUTTONUP):
                    button = "middle"
                else:
                    xbutton = (int(info.mouseData) >> 16) & 0xFFFF
                    button = "x1" if xbutton == XBUTTON1 else "x2"
                pressed = message in (
                    WM_LBUTTONDOWN,
                    WM_RBUTTONDOWN,
                    WM_MBUTTONDOWN,
                    WM_XBUTTONDOWN,
                )
                if pressed:
                    self._physical_buttons.add(button)
                else:
                    self._physical_buttons.discard(button)
                event = InputButtonEvent(button=button, pressed=pressed)
            elif message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                delta = ctypes.c_short((int(info.mouseData) >> 16) & 0xFFFF).value
                if message == WM_MOUSEWHEEL:
                    event = InputWheelEvent(dx=0.0, dy=delta / WHEEL_DELTA)
                else:
                    event = InputWheelEvent(dx=delta / WHEEL_DELTA, dy=0.0)
            if event is None:
                return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)
            callback = self._callback
            if callback is None:
                return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)
            suppress = bool(callback(event, False))
            if suppress:
                return 1
        except Exception:  # noqa: BLE001
            log.debug("Windows mouse hook error", exc_info=True)
        return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)

    # --------------------------------------------------------------- injection
    def _send_input(self, items: list[INPUT]) -> None:
        """Deliver synthetic input via the legacy APIs.

        SendInput is unreliable on real systems: driver hooks can accept it
        and then silently drop the events. Verified on the user's Windows
        machine — SendInput moves AND buttons vanished (GetAsyncKeyState
        confirmed the button never landed) while SetCursorPos, mouse_event
        and keybd_event all work. The legacy APIs are what mainstream KVM
        tools use for exactly this reason.
        """
        if not items:
            return
        for item in items:
            if item.type == INPUT_MOUSE:
                mi = item.mi
                if mi.dwFlags & MOUSEEVENTF_MOVE:
                    point = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    width, height = self.screen_size()
                    target = (
                        max(0, min(width - 1, point.x + mi.dx)),
                        max(0, min(height - 1, point.y + mi.dy)),
                    )
                    # warp_pointer clamps, updates _last_pointer (so the hook's
                    # next physical delta is measured from here) and sets the
                    # echo-swallow window for the hook.
                    self.warp_pointer(target[0], target[1])
                    continue
                user32.mouse_event(
                    mi.dwFlags,
                    0,
                    0,
                    mi.mouseData,
                    ctypes.c_void_p(_INJECT_TAG),
                )
                continue
            # Keyboard event.
            user32.keybd_event(
                item.ki.wVk,
                0,
                item.ki.dwFlags,
                ctypes.c_void_p(_INJECT_TAG),
            )

    def _key_input(self, key: str, pressed: bool) -> INPUT | None:
        definition = WIN_NAME_TO_KEY.get(key)
        if definition is None:
            return None
        vk, extended = definition
        flags = 0
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not pressed:
            flags |= KEYEVENTF_KEYUP
        item = INPUT(type=INPUT_KEYBOARD)
        item.ki = KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=_INJECT_TAG,
        )
        return item

    def _inject_key(self, event: InputKeyEvent) -> None:
        item = self._key_input(event.key, event.pressed)
        if item is None:
            log.debug("Unsupported remote key: %s", event.key)
            return
        with self._lock:
            if event.pressed:
                self._injected_keys_down.add(event.key)
            else:
                self._injected_keys_down.discard(event.key)
        self._send_input([item])

    def _inject_move(self, event: InputMoveEvent) -> None:
        screen_w, screen_h = self.screen_size()
        dx = int(round(event.dx * screen_w))
        dy = int(round(event.dy * screen_h))
        if dx == 0 and dy == 0:
            return
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        target = (
            max(0, min(screen_w - 1, point.x + dx)),
            max(0, min(screen_h - 1, point.y + dy)),
        )
        with self._lock:
            user32.SetCursorPos(target[0], target[1])
            self._last_pointer = target
            self._warp_target = target
            self._warp_until = time.monotonic() + 0.15
        verify = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(verify))
        log.info("inject move -> (%d,%d) verify (%d,%d)", target[0], target[1], verify.x, verify.y)

    def _inject_button(self, event: InputButtonEvent) -> None:
        definition = _BUTTON_FLAGS.get(event.button)
        if definition is None:
            return
        down_flag, up_flag, down_data, up_data = definition
        with self._lock:
            if event.pressed:
                self._injected_buttons_down.add(event.button)
            else:
                self._injected_buttons_down.discard(event.button)
            item = INPUT(type=INPUT_MOUSE)
            item.mi = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=down_data if event.pressed else up_data,
                dwFlags=down_flag if event.pressed else up_flag,
                time=0,
                dwExtraInfo=_INJECT_TAG,
            )
            self._send_input([item])

    def _inject_wheel(self, event: InputWheelEvent) -> None:
        self._scroll_accum_y += event.dy
        self._scroll_accum_x += event.dx
        dy = int(self._scroll_accum_y)
        dx = int(self._scroll_accum_x)
        if dy == 0 and dx == 0:
            return
        self._scroll_accum_y -= dy
        self._scroll_accum_x -= dx
        items: list[INPUT] = []
        if dy:
            item = INPUT(type=INPUT_MOUSE)
            item.mi = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=(dy * WHEEL_DELTA) & 0xFFFFFFFF,
                dwFlags=MOUSEEVENTF_WHEEL,
                time=0,
                dwExtraInfo=_INJECT_TAG,
            )
            items.append(item)
        if dx:
            item = INPUT(type=INPUT_MOUSE)
            item.mi = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=(dx * WHEEL_DELTA) & 0xFFFFFFFF,
                dwFlags=MOUSEEVENTF_HWHEEL,
                time=0,
                dwExtraInfo=_INJECT_TAG,
            )
            items.append(item)
        self._send_input(items)

    def inject(self, event: InputEvent) -> None:
        if not self._started_flag:
            return
        try:
            if isinstance(event, InputKeyEvent):
                self._inject_key(event)
            elif isinstance(event, InputMoveEvent):
                self._inject_move(event)
            elif isinstance(event, InputButtonEvent):
                self._inject_button(event)
            elif isinstance(event, InputWheelEvent):
                self._inject_wheel(event)
        except Exception:  # noqa: BLE001
            log.exception("Windows input injection failed")

    def release_injected_keys(self) -> None:
        with self._lock:
            keys = list(self._injected_keys_down)
            buttons = list(self._injected_buttons_down)
            self._injected_keys_down.clear()
            self._injected_buttons_down.clear()
        items: list[INPUT] = []
        for key in keys:
            item = self._key_input(key, False)
            if item is not None:
                items.append(item)
        for button in buttons:
            definition = _BUTTON_FLAGS.get(button)
            if not definition:
                continue
            _, up_flag, _, up_data = definition
            item = INPUT(type=INPUT_MOUSE)
            item.mi = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=up_data,
                dwFlags=up_flag,
                time=0,
                dwExtraInfo=_INJECT_TAG,
            )
            items.append(item)
        self._send_input(items)

    def release_local_modifiers(self) -> None:
        with self._lock:
            keys = list(self._physical_mod_keys)
            self._physical_mod_keys.clear()
        items = []
        for key in keys:
            item = self._key_input(key, False)
            if item is not None:
                items.append(item)
        self._send_input(items)

    @staticmethod
    def is_supported() -> bool:
        return _IS_WINDOWS

    # ------------------------------------------------------------------ cursor
    def screen_size(self) -> tuple[int, int]:
        return (
            max(1, int(user32.GetSystemMetrics(SM_CXSCREEN))),
            max(1, int(user32.GetSystemMetrics(SM_CYSCREEN))),
        )

    def pointer_position(self) -> tuple[int, int]:
        cursor = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        return int(cursor.x), int(cursor.y)

    def warp_pointer(self, x: int, y: int) -> None:
        width, height = self.screen_size()
        x = max(0, min(width - 1, int(x)))
        y = max(0, min(height - 1, int(y)))
        with self._lock:
            user32.SetCursorPos(x, y)
            self._last_pointer = (x, y)
            self._warp_target = (x, y)
            self._warp_until = time.monotonic() + 0.15

    def recenter_pointer(self) -> None:
        width, height = self.screen_size()
        self.warp_pointer(width // 2, height // 2)

    def set_cursor_visible(self, visible: bool) -> None:
        """Set the desired cursor visibility.

        ShowCursor's display counter is tied to the input queue of the thread
        that calls it: a hide from one thread cannot be undone by a show from
        another. All show/hide work is therefore marshalled to the hook
        thread's message loop (WM_APP_CURSOR). Falls back to an inline apply
        before the loop starts or after it stops.
        """
        with self._lock:
            if visible == (not self._cursor_hidden):
                return
            self._cursor_hidden = not visible
            if self._thread_id and user32.PostThreadMessageW(
                self._thread_id, WM_APP_CURSOR, 0, 0
            ):
                log.info("cursor visibility -> %s (posted)", "show" if visible else "hide")
                return
            log.info("cursor visibility -> %s (inline)", "show" if visible else "hide")
            self._apply_cursor_visibility()

    def _apply_cursor_visibility(self) -> None:
        """Apply the desired visibility on the current thread.

        The ShowCursor counter alone cannot hide the cursor: the OS
        auto-shows it on any physical activity, so the counter drifts back
        to >= 0 and the idle machine's parked cursor reappears next to the
        driven one (the 'parallel display' symptom). While hidden, the
        system's normal arrow is therefore swapped for a fully transparent
        cursor — the OS cannot draw what has no pixels — and the original
        (themed) arrow is restored on show/stop.
        """
        with self._lock:
            if not self._cursor_hidden:
                if self._saved_system_cursor is not None:
                    user32.SetSystemCursor(self._saved_system_cursor, OCR_NORMAL)
                    self._saved_system_cursor = None
                    self._blank_cursor = None
                    log.info("system cursor shape restored")
                # ShowCursor returns the counter after the call; loop until it
                # is non-negative so a drifted or double-decremented counter
                # can never leave the cursor invisible. Capped to keep the
                # hook thread responsive.
                for _ in range(32):
                    counter = user32.ShowCursor(True)
                    log.debug("ShowCursor(TRUE) counter=%d", counter)
                    if counter >= 0:
                        break
            else:
                if self._saved_system_cursor is None:
                    original = user32.CopyIcon(
                        user32.LoadCursorW(None, ctypes.c_void_p(OCR_NORMAL))
                    )
                    blank = self._make_blank_cursor()
                    if original and blank:
                        self._saved_system_cursor = original
                        self._blank_cursor = blank
                        if user32.SetSystemCursor(blank, OCR_NORMAL):
                            log.info("system cursor shape blanked")
                # A single ShowCursor(FALSE) decrement is NOT enough: the OS
                # auto-shows the cursor on user activity, so the counter often
                # sits at 0 or 1 and one decrement leaves the cursor visible.
                # Loop until the counter is negative so a hide really hides.
                for _ in range(32):
                    counter = user32.ShowCursor(False)
                    log.debug("ShowCursor(FALSE) counter=%d", counter)
                    if counter < 0:
                        break

    def _make_blank_cursor(self) -> Any:
        """A fully transparent 1x1 cursor (AND + XOR masks all zero)."""
        try:
            hinst = kernel32.GetModuleHandleW(None)
            mask = (ctypes.c_ubyte * 1)(0x00)
            return user32.CreateCursor(
                hinst,
                0,
                0,
                1,
                1,
                ctypes.cast(mask, ctypes.c_void_p),
                ctypes.cast(mask, ctypes.c_void_p),
            )
        except Exception:  # noqa: BLE001
            return None


__all__ = ["WindowsInputBackend"]
