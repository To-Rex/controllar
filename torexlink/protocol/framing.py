"""Length-prefixed JSON frames over TLS/TCP.

4-byte unsigned big-endian length, UTF-8 JSON object. Frames are bounded before
allocation so a malicious peer cannot exhaust memory.
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from ..constants import MAX_FRAME_BYTES

_HEADER = struct.Struct("!I")


class FrameError(ValueError):
    """Invalid frame contents."""


class FrameTooLarge(FrameError):
    """Declared frame exceeds the configured protocol limit."""


async def read_frame_sized(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int = MAX_FRAME_BYTES,
) -> tuple[dict[str, Any], int]:
    header = await reader.readexactly(4)
    (length,) = _HEADER.unpack(header)
    if length == 0:
        raise FrameError("empty frame")
    if length > max_bytes:
        raise FrameTooLarge(f"frame of {length} bytes exceeds limit")
    payload = await reader.readexactly(length)
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameError("frame is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise FrameError("frame root must be an object")
    return decoded, 4 + length


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int = MAX_FRAME_BYTES,
) -> dict[str, Any]:
    decoded, _size = await read_frame_sized(reader, max_bytes=max_bytes)
    return decoded


async def write_frame(
    writer: asyncio.StreamWriter,
    payload: dict[str, Any],
    *,
    lock: asyncio.Lock | None = None,
) -> int:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FrameError("message is not JSON serializable") from exc
    if len(encoded) > MAX_FRAME_BYTES:
        raise FrameTooLarge("outgoing frame is too large")
    frame = _HEADER.pack(len(encoded)) + encoded
    if lock is None:
        writer.write(frame)
        await writer.drain()
        return len(frame)
    async with lock:
        writer.write(frame)
        await writer.drain()
    return len(frame)


async def write_frames_at_once(
    writer: asyncio.StreamWriter,
    payloads: list[dict[str, Any]],
    *,
    lock: asyncio.Lock,
) -> int:
    """Write a small batch under a single drain for latency-sensitive input."""
    encoded_parts: list[bytes] = []
    for payload in payloads:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_FRAME_BYTES:
            raise FrameTooLarge("outgoing frame is too large")
        encoded_parts.append(_HEADER.pack(len(encoded)) + encoded)
    if not encoded_parts:
        return 0
    total = sum(len(part) for part in encoded_parts)
    async with lock:
        for part in encoded_parts:
            writer.write(part)
        await writer.drain()
    return total


__all__ = [
    "FrameError",
    "FrameTooLarge",
    "read_frame",
    "read_frame_sized",
    "write_frame",
    "write_frames_at_once",
]
