"""Bidirectional plain-text clipboard synchronization (1 MiB limit).

Only text is exchanged. Clipboard feedback loops are prevented by comparing
content hashes and by never re-sending a value that was just applied remotely.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import sys
import time
from collections.abc import Callable
from typing import Protocol

from ..constants import CLIPBOARD_MAX_BYTES, CLIPBOARD_MIN_INTERVAL, CLIPBOARD_POLL_SECONDS

log = logging.getLogger(__name__)


class NativeClipboard(Protocol):
    def read_text(self) -> str: ...

    def write_text(self, text: str) -> None: ...


class ClipboardError(RuntimeError):
    pass


class MacClipboard:
    def __init__(self) -> None:
        try:
            import objc
            from AppKit import NSPasteboard, NSPasteboardTypeString
        except ImportError as exc:
            raise ClipboardError("PyObjC AppKit is required for clipboard sync") from exc
        self._objc = objc
        self._pasteboard = NSPasteboard.generalPasteboard()
        self._string_type = NSPasteboardTypeString

    def read_text(self) -> str:
        with self._objc.autorelease_pool():
            value = self._pasteboard.stringForType_(self._string_type)
            return str(value) if value is not None else ""

    def write_text(self, text: str) -> None:
        with self._objc.autorelease_pool():
            self._pasteboard.clearContents()
            if text:
                if not self._pasteboard.setString_forType_(text, self._string_type):
                    raise ClipboardError("macOS rejected the clipboard write")


class WindowsClipboard:
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    def __init__(self) -> None:
        if os.name != "nt":
            raise ClipboardError("Windows clipboard is unavailable on this platform")
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.OpenClipboard.argtypes = [wintypes.HWND]
        self.user32.OpenClipboard.restype = wintypes.BOOL
        self.user32.CloseClipboard.restype = wintypes.BOOL
        self.user32.EmptyClipboard.restype = wintypes.BOOL
        self.user32.GetClipboardData.argtypes = [wintypes.UINT]
        self.user32.GetClipboardData.restype = wintypes.HANDLE
        self.user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user32.SetClipboardData.restype = wintypes.HANDLE
        self.user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        self.user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        self.user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
        self.kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        self.kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalLock.restype = wintypes.LPVOID
        self.kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalUnlock.restype = wintypes.BOOL
        self.kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalFree.restype = wintypes.HGLOBAL

    def _open(self) -> None:
        for _ in range(8):
            if self.user32.OpenClipboard(None):
                return
            time.sleep(0.012)
        raise ClipboardError("clipboard is busy")

    def read_text(self) -> str:
        self._open()
        try:
            if not self.user32.IsClipboardFormatAvailable(self.CF_UNICODETEXT):
                return ""
            handle = self.user32.GetClipboardData(self.CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = self.kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return str(self.ctypes.wstring_at(pointer))
            finally:
                self.kernel32.GlobalUnlock(handle)
        finally:
            self.user32.CloseClipboard()

    def write_text(self, text: str) -> None:
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = self.kernel32.GlobalAlloc(self.GMEM_MOVEABLE, len(data))
        if not handle:
            raise ClipboardError("could not allocate clipboard memory")
        pointer = self.kernel32.GlobalLock(handle)
        if not pointer:
            self.kernel32.GlobalFree(handle)
            raise ClipboardError("could not lock clipboard memory")
        try:
            self.ctypes.memmove(pointer, data, len(data))
        finally:
            self.kernel32.GlobalUnlock(handle)
        self._open()
        try:
            if not self.user32.EmptyClipboard():
                self.kernel32.GlobalFree(handle)
                raise ClipboardError("could not empty the clipboard")
            if not self.user32.SetClipboardData(self.CF_UNICODETEXT, handle):
                self.kernel32.GlobalFree(handle)
                raise ClipboardError("could not set clipboard text")
        finally:
            self.user32.CloseClipboard()


def create_native_clipboard() -> NativeClipboard:
    if sys.platform == "darwin":
        return MacClipboard()
    if sys.platform == "win32":
        return WindowsClipboard()
    raise ClipboardError("clipboard sync supports macOS and Windows only")


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClipboardSync:
    def __init__(
        self,
        native: NativeClipboard,
        *,
        on_send: Callable[[str, str], None],
        on_remote_applied: Callable[[str], None] | None = None,
        on_skip: Callable[[str], None] | None = None,
        max_bytes: int = CLIPBOARD_MAX_BYTES,
    ) -> None:
        self.native = native
        self.on_send = on_send
        self.on_remote_applied = on_remote_applied
        self.on_skip = on_skip
        self.max_bytes = max_bytes
        self._last_seen_digest: str | None = None
        self._suppress_digest: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._last_sent_at = 0.0
        self._last_applied_at = 0.0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            initial = await asyncio.to_thread(self.native.read_text)
            if len(initial.encode("utf-8")) <= self.max_bytes:
                self._last_seen_digest = text_digest(initial)
        except Exception:  # noqa: BLE001
            log.debug("Initial clipboard read failed", exc_info=True)
        self._task = asyncio.create_task(self._poll_loop(), name="ToRexLink-Clipboard")

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _poll_loop(self) -> None:
        while self._running:
            await asyncio.sleep(CLIPBOARD_POLL_SECONDS)
            try:
                text = await asyncio.to_thread(self.native.read_text)
            except Exception:  # noqa: BLE001 - clipboard can be temporarily locked
                continue
            encoded = text.encode("utf-8")
            digest = text_digest(text)
            if digest == self._last_seen_digest:
                continue
            if len(encoded) > self.max_bytes:
                self._last_seen_digest = digest
                if self.on_skip:
                    with contextlib.suppress(Exception):
                        self.on_skip("too_large")
                continue
            if digest == self._suppress_digest:
                self._last_seen_digest = digest
                self._suppress_digest = None
                continue
            now = time.monotonic()
            if now - self._last_sent_at < CLIPBOARD_MIN_INTERVAL:
                continue
            self._last_seen_digest = digest
            if not text:
                continue
            self._last_sent_at = now
            try:
                self.on_send(text, digest)
            except Exception:  # noqa: BLE001
                log.debug("Clipboard send callback failed", exc_info=True)

    async def apply_remote(self, text: str, digest: str, origin: str) -> bool:
        encoded = text.encode("utf-8")
        if len(encoded) > self.max_bytes:
            log.warning("Ignored oversized remote clipboard (%s bytes)", len(encoded))
            return False
        if text_digest(text) != digest:
            log.warning("Ignored remote clipboard with a mismatched digest")
            return False
        if digest == self._last_seen_digest:
            return False
        now = time.monotonic()
        if now - self._last_applied_at < CLIPBOARD_MIN_INTERVAL:
            return False
        try:
            await asyncio.to_thread(self.native.write_text, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not apply remote clipboard: %s", exc)
            return False
        self._last_applied_at = now
        self._last_seen_digest = digest
        self._suppress_digest = digest
        if self.on_remote_applied:
            with contextlib.suppress(Exception):
                self.on_remote_applied(origin)
        return True


__all__ = [
    "ClipboardError",
    "ClipboardSync",
    "MacClipboard",
    "NativeClipboard",
    "WindowsClipboard",
    "create_native_clipboard",
    "text_digest",
]
