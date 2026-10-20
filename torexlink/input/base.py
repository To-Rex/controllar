"""Native input backend interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .events import InputEvent


class InputBackendError(RuntimeError):
    """Native input engine could not be started or used."""


@dataclass(frozen=True, slots=True)
class InputPermissions:
    capture: bool
    injection: bool
    capture_detail: str = ""
    injection_detail: str = ""

    @property
    def ready(self) -> bool:
        return self.capture and self.injection


CaptureCallback = Callable[[InputEvent, bool], bool]
"""Callback receives the event and whether it was injected by ToRexLink.

Returns True when the event must be suppressed locally. Injected events are
never suppressed (doing so would create a feedback loop).
"""


class InputBackend(Protocol):
    name: str

    def preflight(self) -> InputPermissions: ...

    def request_permissions(self) -> None: ...

    def start(self, callback: CaptureCallback) -> None: ...

    def stop(self) -> None: ...

    def inject(self, event: InputEvent) -> None: ...

    def warp_pointer(self, x: int, y: int) -> None: ...

    def pointer_position(self) -> tuple[int, int]: ...

    def screen_size(self) -> tuple[int, int]: ...

    def recenter_pointer(self) -> None: ...

    def set_cursor_visible(self, visible: bool) -> None: ...

    def set_pointer_frozen(self, frozen: bool) -> None:
        """Optionally decouple the local pointer from physical motion."""
        return None

    def release_injected_keys(self) -> None: ...

    def release_local_modifiers(self) -> None: ...


__all__ = [
    "CaptureCallback",
    "InputBackend",
    "InputBackendError",
    "InputPermissions",
]
