from __future__ import annotations

import asyncio

import pytest

from torexlink.core.clipboard import ClipboardSync, text_digest


class Native:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def read_text(self) -> str:
        return self.text

    def write_text(self, text: str) -> None:
        self.text = text


@pytest.mark.asyncio
async def test_local_clipboard_is_sent_once() -> None:
    native = Native()
    sent: list[tuple[str, str]] = []
    sync = ClipboardSync(native, on_send=lambda text, digest: sent.append((text, digest)))
    await sync.start()
    try:
        native.text = "salom dunyo"
        await asyncio.sleep(0.55)
        assert sent == [("salom dunyo", text_digest("salom dunyo"))]
        await asyncio.sleep(0.5)
        assert len(sent) == 1
    finally:
        await sync.stop()


@pytest.mark.asyncio
async def test_remote_clipboard_not_echoed() -> None:
    native = Native("old")
    sent: list[tuple[str, str]] = []
    sync = ClipboardSync(native, on_send=lambda text, digest: sent.append((text, digest)))
    await sync.start()
    try:
        text = "remote value"
        digest = text_digest(text)
        assert await sync.apply_remote(text, digest, "peer")
        assert native.text == text
        await asyncio.sleep(0.6)
        assert sent == []
    finally:
        await sync.stop()


@pytest.mark.asyncio
async def test_remote_rejects_digest_mismatch() -> None:
    native = Native()
    sync = ClipboardSync(native, on_send=lambda *_: None)
    await sync.start()
    try:
        assert not await sync.apply_remote("tampered", "a" * 64, "peer")
        assert native.text == ""
    finally:
        await sync.stop()


@pytest.mark.asyncio
async def test_clipboard_size_limit() -> None:
    native = Native()
    skipped: list[str] = []
    sync = ClipboardSync(
        native,
        on_send=lambda *_: None,
        on_skip=skipped.append,
        max_bytes=10,
    )
    await sync.start()
    try:
        native.text = "x" * 11
        await asyncio.sleep(0.55)
        assert skipped == ["too_large"]
        assert not await sync.apply_remote("y" * 11, text_digest("y" * 11), "peer")
    finally:
        await sync.stop()
