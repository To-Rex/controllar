"""Platform-neutral input events exchanged between native backends and the link."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


@dataclass(frozen=True, slots=True)
class InputKeyEvent:
    key: str
    pressed: bool
    repeat: bool = False
    mods: tuple[str, ...] = ()
    kind: Literal["key"] = "key"


@dataclass(frozen=True, slots=True)
class InputMoveEvent:
    dx: float
    dy: float
    x: int = 0
    y: int = 0
    kind: Literal["move"] = "move"


@dataclass(frozen=True, slots=True)
class InputButtonEvent:
    button: str
    pressed: bool
    kind: Literal["button"] = "button"


@dataclass(frozen=True, slots=True)
class InputWheelEvent:
    dx: float
    dy: float
    kind: Literal["wheel"] = "wheel"


InputEvent: TypeAlias = InputKeyEvent | InputMoveEvent | InputButtonEvent | InputWheelEvent


def event_to_wire(event: InputEvent) -> dict[str, object]:
    if isinstance(event, InputKeyEvent):
        return {
            "t": "key",
            "key": event.key,
            "pressed": event.pressed,
            "repeat": event.repeat,
            "mods": list(event.mods),
        }
    if isinstance(event, InputMoveEvent):
        return {"t": "move", "dx": round(event.dx, 6), "dy": round(event.dy, 6)}
    if isinstance(event, InputButtonEvent):
        return {"t": "button", "button": event.button, "pressed": event.pressed}
    return {"t": "wheel", "dx": round(event.dx, 4), "dy": round(event.dy, 4)}


def event_from_wire(raw: dict[str, object]) -> InputEvent:
    kind = raw.get("t")
    if kind == "key":
        return InputKeyEvent(
            key=str(raw["key"]),
            pressed=bool(raw["pressed"]),
            repeat=bool(raw.get("repeat", False)),
            mods=tuple(str(item) for item in raw.get("mods", [])),  # type: ignore[union-attr]
        )
    if kind == "move":
        return InputMoveEvent(dx=float(raw["dx"]), dy=float(raw["dy"]))  # type: ignore[arg-type]
    if kind == "button":
        return InputButtonEvent(button=str(raw["button"]), pressed=bool(raw["pressed"]))
    if kind == "wheel":
        return InputWheelEvent(dx=float(raw.get("dx", 0)), dy=float(raw.get("dy", 0)))  # type: ignore[arg-type]
    raise ValueError("unknown input event")


__all__ = [
    "InputButtonEvent",
    "InputEvent",
    "InputKeyEvent",
    "InputMoveEvent",
    "InputWheelEvent",
    "event_from_wire",
    "event_to_wire",
]
