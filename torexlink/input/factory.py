"""Platform backend factory with lazy imports."""

from __future__ import annotations

import sys

from .base import InputBackend, InputBackendError


def create_input_backend() -> InputBackend:
    if sys.platform == "darwin":
        from .macos import MacOSInputBackend

        return MacOSInputBackend()
    if sys.platform == "win32":
        from .windows import WindowsInputBackend

        return WindowsInputBackend()
    raise InputBackendError("ToRexLink supports macOS and Windows only")


__all__ = ["create_input_backend"]
