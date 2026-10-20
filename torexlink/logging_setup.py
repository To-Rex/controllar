"""Rotating on-disk logging with secret redaction."""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

from .constants import LOG_BACKUP_COUNT, LOG_MAX_BYTES

_SECRET_PATTERNS = (
    (re.compile(r"(?i)(pair(?:ing)?[ _-]?code\s*[=:]\s*)\d{6}"), r"\1••••••"),
    (re.compile(r"(?i)(token\s*[=:]\s*)[A-Za-z0-9_\-.]{16,}"), r"\1••••••"),
    (re.compile(r"(?i)(authorization\s*[=:]\s*)\S+"), r"\1••••••"),
)


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        for pattern, replacement in _SECRET_PATTERNS:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def configure_logging(log_file: Path, *, debug: bool = False) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # Idempotent startup (e.g. tests create multiple services).
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RedactSecrets())
    root.addHandler(file_handler)
    if not getattr(sys, "frozen", False) and sys.stderr is not None:
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(formatter)
        console.addFilter(RedactSecrets())
        root.addHandler(console)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)
    logging.getLogger("PySide6").setLevel(logging.WARNING)
    return logging.getLogger("torexlink")


def read_log_tail(log_file: Path, max_lines: int = 500) -> str:
    if not log_file.exists():
        return ""
    try:
        with log_file.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            read_size = min(size, 512 * 1024)
            handle.seek(size - read_size)
            data = handle.read()
        text = data.decode("utf-8", "replace")
        return "\n".join(text.splitlines()[-max_lines:])
    except OSError as exc:
        return f"Log faylini o‘qib bo‘lmadi: {exc}"


def install_exception_logging() -> None:
    import sys
    import threading

    log = logging.getLogger("torexlink")

    def thread_hook(args: object) -> None:
        log.critical(
            "Unhandled exception in thread",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),  # type: ignore[attr-defined]
        )

    def exception_hook(exc_type, exc_value, exc_traceback) -> None:
        log.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    threading.excepthook = thread_hook
    sys.excepthook = exception_hook


__all__ = [
    "RedactSecrets",
    "configure_logging",
    "install_exception_logging",
    "read_log_tail",
]
