"""Small, dependency-free utilities shared across layers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .constants import DEFAULT_LOCALE, SUPPORTED_LOCALES


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def atomic_write_bytes(path: Path, data: bytes, *, private: bool = True) -> None:
    """Write via fsync + rename so a crash never leaves a partial secret file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600 if private else 0o644)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str, *, private: bool = True) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), private=private)


def atomic_write_json(path: Path, payload: Any, *, private: bool = True) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_write_text(path, text + "\n", private=private)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def random_b64(nbytes: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def b64decode(value: str, *, expected_length: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("invalid base64 value")
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:  # noqa: BLE001 - normalize decoder errors
        raise ValueError("invalid base64 value") from exc
    if expected_length is not None and len(raw) != expected_length:
        raise ValueError("unexpected decoded length")
    return raw


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def valid_locale(value: object) -> str:
    return value if isinstance(value, str) and value in SUPPORTED_LOCALES else DEFAULT_LOCALE


def mask_secret(value: str, visible: int = 4) -> str:
    if len(value) <= visible:
        return "•" * max(4, len(value))
    return "•" * (len(value) - visible) + value[-visible:]


def backoff_delay(attempt: int, delays: tuple[float, ...], jitter: float = 0.25) -> float:
    base = delays[min(max(attempt, 0), len(delays) - 1)]
    spread = base * jitter
    return max(0.5, base + secrets.choice((-spread, spread)))


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def version_at_least(value: str, minimum: str) -> bool:
    """Compare simple numeric dotted versions without a packaging dependency."""
    def parts(text: str) -> tuple[int, ...]:
        chunks: list[int] = []
        for chunk in text.strip().split("."):
            digits = "".join(char for char in chunk if char.isdigit())
            if not digits:
                break
            chunks.append(int(digits))
        return tuple(chunks)

    left = parts(value)
    right = parts(minimum)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


__all__ = [
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "b64decode",
    "b64e",
    "backoff_delay",
    "canonical_json_bytes",
    "clamp",
    "iso_now",
    "mask_secret",
    "monotonic_ms",
    "parse_iso",
    "random_b64",
    "read_json",
    "sha256_hex",
    "utc_now",
    "valid_locale",
    "version_at_least",
]
