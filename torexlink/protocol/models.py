"""Strict validation for post-handshake messages."""

from __future__ import annotations

import re
from typing import Any

from ..constants import (
    CLIPBOARD_MAX_BYTES,
    EDGES,
    FOCUS_ACTIONS,
    MAX_DEVICE_NAME,
    PROTOCOL_VERSION,
)
from ..util import clamp

_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_BUTTONS = frozenset({"left", "right", "middle", "x1", "x2"})
_MODS = frozenset({"ctrl", "alt", "shift", "meta", "caps"})
_EVENT_TYPES = frozenset({"key", "move", "button", "wheel"})
_MESSAGE_TYPES = frozenset(
    {"ping", "pong", "input", "focus_request", "focus_state", "clipboard", "bye", "error"}
)


class ProtocolError(ValueError):
    """Message failed schema or safety validation."""


def _require_int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ProtocolError(f"{name} out of range")
    return value


def _require_float(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a number")
    number = float(value)
    if not low <= number <= high or number != number:
        raise ProtocolError(f"{name} out of range")
    return number


def _require_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ProtocolError(f"invalid {name}")
    return value


def _require_short_str(value: Any, name: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProtocolError(f"invalid {name}")
    if any(ord(char) < 32 for char in value):
        raise ProtocolError(f"invalid control character in {name}")
    return value


def validate_input_event(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProtocolError("input event must be an object")
    event_type = raw.get("t")
    if event_type not in _EVENT_TYPES:
        raise ProtocolError("unknown input event type")
    if event_type == "key":
        key = raw.get("key")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise ProtocolError("invalid key name")
        pressed = raw.get("pressed")
        if not isinstance(pressed, bool):
            raise ProtocolError("key pressed must be boolean")
        repeat = raw.get("repeat", False)
        if not isinstance(repeat, bool):
            raise ProtocolError("key repeat must be boolean")
        mods_raw = raw.get("mods", [])
        if not isinstance(mods_raw, list) or len(mods_raw) > len(_MODS):
            raise ProtocolError("invalid modifiers")
        mods = list(dict.fromkeys(mods_raw))
        if any(mod not in _MODS for mod in mods):
            raise ProtocolError("invalid modifier")
        return {"t": "key", "key": key, "pressed": pressed, "repeat": repeat, "mods": mods}
    if event_type == "move":
        return {
            "t": "move",
            "dx": round(clamp(_require_float(raw.get("dx"), "dx", -2.0, 2.0), -2.0, 2.0), 6),
            "dy": round(clamp(_require_float(raw.get("dy"), "dy", -2.0, 2.0), -2.0, 2.0), 6),
        }
    if event_type == "button":
        button = raw.get("button")
        if button not in _BUTTONS:
            raise ProtocolError("invalid mouse button")
        pressed = raw.get("pressed")
        if not isinstance(pressed, bool):
            raise ProtocolError("button pressed must be boolean")
        return {"t": "button", "button": button, "pressed": pressed}
    # wheel
    return {
        "t": "wheel",
        "dx": round(_require_float(raw.get("dx", 0), "wheel dx", -120.0, 120.0), 3),
        "dy": round(_require_float(raw.get("dy", 0), "wheel dy", -120.0, 120.0), 3),
    }


def _validate_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    if message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    message_type = message.get("type")
    if message_type not in _MESSAGE_TYPES:
        raise ProtocolError("unknown message type")

    if message_type == "ping":
        return {
            "v": PROTOCOL_VERSION,
            "type": "ping",
            "id": _require_int(message.get("id"), "ping id", 0, 2**31 - 1),
        }
    if message_type == "pong":
        return {
            "v": PROTOCOL_VERSION,
            "type": "pong",
            "id": _require_int(message.get("id"), "pong id", 0, 2**31 - 1),
        }
    if message_type == "input":
        seq = _require_int(message.get("seq", 0), "seq", 0, 2**63 - 1)
        event = validate_input_event(message.get("event"))
        return {"v": PROTOCOL_VERSION, "type": "input", "seq": seq, "event": event}
    if message_type == "focus_request":
        action = message.get("action")
        if action not in FOCUS_ACTIONS:
            raise ProtocolError("invalid focus action")
        edge = message.get("edge")
        if edge is not None and edge not in EDGES:
            raise ProtocolError("invalid edge")
        position = message.get("position")
        if position is not None:
            position = _require_float(position, "position", 0.0, 1.0)
        request_id = _require_short_str(message.get("request_id"), "request id", 64)
        return {
            "v": PROTOCOL_VERSION,
            "type": "focus_request",
            "action": action,
            "edge": edge,
            "position": position,
            "request_id": request_id,
        }
    if message_type == "focus_state":
        epoch = _require_int(message.get("epoch"), "epoch", 0, 2**63 - 1)
        controller = _require_id(message.get("controller"), "controller")
        target = _require_id(message.get("target"), "target")
        reason = message.get("reason")
        if reason not in {"hotkey", "edge", "takeover", "remote-return", "initial", "connect"}:
            raise ProtocolError("invalid focus reason")
        entry_edge = message.get("entry_edge")
        if entry_edge is not None and entry_edge not in EDGES:
            raise ProtocolError("invalid entry edge")
        position = message.get("position")
        if position is not None:
            position = _require_float(position, "position", 0.0, 1.0)
        return {
            "v": PROTOCOL_VERSION,
            "type": "focus_state",
            "epoch": epoch,
            "controller": controller,
            "target": target,
            "reason": reason,
            "entry_edge": entry_edge,
            "position": position,
        }
    if message_type == "clipboard":
        text = message.get("text")
        origin = _require_id(message.get("origin"), "clipboard origin")
        digest = message.get("sha256")
        if not isinstance(text, str):
            raise ProtocolError("clipboard text must be a string")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProtocolError("invalid clipboard digest")
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProtocolError("clipboard is not valid Unicode") from exc
        if len(encoded) > CLIPBOARD_MAX_BYTES:
            raise ProtocolError("clipboard exceeds 1 MB limit")
        import hashlib

        if hashlib.sha256(encoded).hexdigest() != digest:
            raise ProtocolError("clipboard digest mismatch")
        return {
            "v": PROTOCOL_VERSION,
            "type": "clipboard",
            "text": text,
            "origin": origin,
            "sha256": digest,
        }
    if message_type == "bye":
        reason = message.get("reason", "normal")
        if not isinstance(reason, str) or len(reason) > 120:
            raise ProtocolError("invalid bye reason")
        return {"v": PROTOCOL_VERSION, "type": "bye", "reason": reason}
    # error
    code = message.get("code", "error")
    if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,32}", code):
        raise ProtocolError("invalid error code")
    return {"v": PROTOCOL_VERSION, "type": "error", "code": code}


def validate_peer_message(message: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized, restricted copy safe for application dispatch."""
    return _validate_message(message)


def device_name(value: str) -> str:
    cleaned = " ".join(value.replace("\x00", " ").split())
    return (cleaned or "ToRexLink")[:MAX_DEVICE_NAME]


__all__ = [
    "ProtocolError",
    "device_name",
    "validate_input_event",
    "validate_peer_message",
]
