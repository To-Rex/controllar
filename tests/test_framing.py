from __future__ import annotations

import asyncio
import struct

import pytest

from torexlink.constants import MAX_FRAME_BYTES
from torexlink.protocol.framing import FrameError, FrameTooLarge, read_frame, write_frame
from torexlink.protocol.models import ProtocolError, validate_peer_message
from torexlink.util import version_at_least


async def _roundtrip(payload: dict) -> dict:
    async def handle(reader, writer):
        try:
            message = await read_frame(reader)
            await write_frame(writer, {"echo": message})
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await write_frame(writer, payload)
        return await read_frame(reader)
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


def test_roundtrip() -> None:
    payload = {"v": 1, "type": "ping", "id": 4, "text": "o‘zbekcha 🔐"}
    assert asyncio.run(_roundtrip(payload)) == {"echo": payload}


def test_reject_oversized_frame() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack("!I", MAX_FRAME_BYTES + 1))
        with pytest.raises(FrameTooLarge):
            await read_frame(reader)

    asyncio.run(run())


def test_reject_invalid_json() -> None:
    async def run() -> None:
        body = b"{not json"
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack("!I", len(body)) + body)
        with pytest.raises(FrameError):
            await read_frame(reader)

    asyncio.run(run())


def test_input_validation_rejects_unknown_and_oversized_values() -> None:
    base = {"v": 1, "type": "input", "seq": 1, "event": {"t": "move", "dx": 0.1, "dy": -0.1}}
    assert validate_peer_message(base)["event"]["dx"] == 0.1
    for digit in "0123456789":
        digit_message = {
            "v": 1,
            "type": "input",
            "seq": 1,
            "event": {"t": "key", "key": digit, "pressed": True},
        }
        assert validate_peer_message(digit_message)["event"]["key"] == digit
    bad_key = {
        "v": 1,
        "type": "input",
        "seq": 1,
        "event": {"t": "key", "key": "evil key", "pressed": True},
    }
    with pytest.raises(ProtocolError):
        validate_peer_message(bad_key)
    bad_move = {
        "v": 1,
        "type": "input",
        "seq": 1,
        "event": {"t": "move", "dx": 99, "dy": 0},
    }
    with pytest.raises(ProtocolError):
        validate_peer_message(bad_move)


def test_clipboard_digest_enforced() -> None:
    import hashlib

    text = "salom"
    digest = hashlib.sha256(text.encode()).hexdigest()
    message = {
        "v": 1,
        "type": "clipboard",
        "text": text,
        "origin": "a" * 64,
        "sha256": digest,
    }
    assert validate_peer_message(message)["text"] == text
    message["sha256"] = "b" * 64
    with pytest.raises(ProtocolError):
        validate_peer_message(message)


def test_log_exchange_message_validation() -> None:
    request = {
        "v": 1,
        "type": "log_request",
        "request_id": "0123456789abcdef",
        "max_bytes": 65536,
    }
    normalized = validate_peer_message(request)
    assert normalized["max_bytes"] == 65536

    response = {
        "v": 1,
        "type": "log_response",
        "request_id": "0123456789abcdef",
        "text": "line one\nline two\n",
        "truncated": False,
        "device": "Test Mac",
        "version": "1.0.5",
    }
    assert validate_peer_message(response)["text"].startswith("line one")

    for broken in (
        {**request, "request_id": "not-hex"},
        {**request, "max_bytes": 0},
        {**request, "max_bytes": 10**9},
        {**response, "truncated": "no"},
        {**response, "text": "x" * (256 * 1024 + 1)},
    ):
        with pytest.raises(ProtocolError):
            validate_peer_message(broken)


def test_version_at_least_for_remote_log_compatibility() -> None:
    assert version_at_least("1.0.5", "1.0.5")
    assert version_at_least("1.0.6", "1.0.5")
    assert version_at_least("1.1", "1.0.5")
    assert not version_at_least("1.0.4", "1.0.5")
    assert not version_at_least("", "1.0.5")
