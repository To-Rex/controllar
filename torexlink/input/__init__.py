"""Normalized input model and native platform backends."""

from .base import InputBackend, InputBackendError, InputPermissions
from .bridge import InputBridge
from .events import (
    InputButtonEvent,
    InputEvent,
    InputKeyEvent,
    InputMoveEvent,
    InputWheelEvent,
    event_from_wire,
    event_to_wire,
)
from .factory import create_input_backend

__all__ = [
    "InputBackend",
    "InputBackendError",
    "InputBridge",
    "InputButtonEvent",
    "InputEvent",
    "InputKeyEvent",
    "InputMoveEvent",
    "InputPermissions",
    "InputWheelEvent",
    "create_input_backend",
    "event_from_wire",
    "event_to_wire",
]
