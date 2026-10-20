"""Focus-aware routing between native capture, the network and local OS input."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..constants import (
    EDGE_REARM_MARGIN_PX,
    EVENT_COOLDOWN_SECONDS,
    HOTKEY_LABEL,
    REMOTE_RETURN_PUSH_PX,
    REMOTE_RETURN_REARM_MARGIN_PX,
    REMOTE_RETURN_SETTLE_SECONDS,
    SCREEN_EDGE_MARGIN_PX,
    TAKEOVER_MOVE_THRESHOLD_PX,
)
from .base import InputBackend, InputBackendError
from .events import (
    InputButtonEvent,
    InputEvent,
    InputKeyEvent,
    InputMoveEvent,
    InputWheelEvent,
    event_to_wire,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class InputBridgeCallbacks:
    send_input: Callable[[dict[str, Any]], None]
    request_focus: Callable[[str, str | None, float | None], None]


class InputBridge:
    """Owns suppression policy for one authenticated peer session."""

    def __init__(
        self,
        *,
        self_id: str,
        peer_id: str,
        backend: InputBackend,
        callbacks: InputBridgeCallbacks,
        edge: str = "right",
        edge_switch: bool = True,
        hotkey_switch: bool = True,
    ) -> None:
        self.self_id = self_id
        self.peer_id = peer_id
        self.backend = backend
        self.callbacks = callbacks
        self.edge = edge
        self.edge_switch = edge_switch
        self.hotkey_switch = hotkey_switch
        self._lock = threading.RLock()
        self._backend_lock = threading.RLock()
        self._controller = ""
        self._target = ""
        self._epoch = 0
        self._entry_edge: str | None = None
        self._entry_position: float | None = None
        self._reason = ""
        self._started = False
        self._cursor_hidden = False
        self._saved_pointer: tuple[int, int] | None = None
        self._takeover_travel = 0.0
        self._edge_latched = False
        self._edge_armed = False
        self._remote_return_armed = False
        self._remote_return_push = 0.0
        self._receiving_since = 0.0
        self._injected_return_latched = False
        self._last_focus_request = 0.0
        self._last_hotkey = 0.0
        self._last_injected_check = 0.0
        self._virtual_pointer: tuple[float, float] = (0.0, 0.0)

    # ------------------------------------------------------------------ state
    def apply_focus(
        self,
        *,
        epoch: int,
        controller: str,
        target: str,
        reason: str = "",
        entry_edge: str | None = None,
        position: float | None = None,
    ) -> None:
        if controller not in {self.self_id, self.peer_id} or target not in {
            self.self_id,
            self.peer_id,
        }:
            raise InputBackendError("focus references an unknown device")
        with self._lock:
            if epoch < self._epoch:
                return
            previous = (
                self._controller,
                self._target,
                self._entry_edge,
                self._entry_position,
                self._reason,
            )
            if epoch == self._epoch and previous[0:2] == (controller, target):
                self._entry_edge = entry_edge
                self._entry_position = position
                self._reason = reason
                return
            self._epoch = epoch
            self._controller = controller
            self._target = target
            self._reason = reason
            self._entry_edge = entry_edge
            self._entry_position = position
            self._takeover_travel = 0.0
            self._edge_latched = False
            self._injected_return_latched = False
            self._edge_armed = False
            self._remote_return_armed = False
            self._remote_return_push = 0.0
            if controller == self.self_id and target == self.self_id:
                # After an edge/remote return, require the user to move the
                # pointer away from the exit edge before it can trigger again.
                self._edge_armed = reason != "remote-return"
            if controller == self.peer_id and target == self.self_id:
                self._receiving_since = time.monotonic()
        try:
            was_forwarding = previous[0] == self.self_id and previous[1] == self.peer_id
            is_forwarding = controller == self.self_id and target == self.peer_id
            is_receiving = controller == self.peer_id and target == self.self_id
            is_local_active = controller == self.self_id and target == self.self_id
            if is_forwarding:
                if not was_forwarding:
                    self._saved_pointer = self.backend.pointer_position()
                    self.backend.release_local_modifiers()
                    width, height = self.backend.screen_size()
                    self.backend.warp_pointer(width // 2, height // 2)
                self._set_cursor_visible(False)
            elif is_receiving:
                self._set_cursor_visible(True)
                if entry_edge is not None:
                    self._warp_to_entry(entry_edge, position if position is not None else 0.5)
                elif not (previous[0] == self.peer_id and previous[1] == self.self_id):
                    width, height = self.backend.screen_size()
                    self.backend.warp_pointer(width // 2, height // 2)
                self._virtual_pointer = (
                    float(self.backend.pointer_position()[0]),
                    float(self.backend.pointer_position()[1]),
                )
                self.backend.release_local_modifiers()
            elif is_local_active:
                self._set_cursor_visible(True)
                if previous[0] == self.peer_id or previous[1] == self.peer_id:
                    self.backend.release_injected_keys()
                if reason == "remote-return" and previous[0] == self.peer_id:
                    self._restore_pointer(inset=True)
                elif not (previous[0] == self.self_id and previous[1] == self.self_id):
                    self._restore_pointer(inset=False)
            else:
                self._set_cursor_visible(True)
                if previous[1] == self.self_id and previous[0] == self.peer_id:
                    self.backend.release_injected_keys()
            if not is_forwarding and was_forwarding:
                self.backend.release_local_modifiers()
            log.info(
                "Focus epoch=%s controller=%s target=%s reason=%s",
                epoch,
                self._short(controller),
                self._short(target),
                reason,
            )
        except Exception:  # noqa: BLE001 - focus must not crash a session
            log.exception("Could not apply focus transition")

    def _short(self, device_id: str) -> str:
        return "local" if device_id == self.self_id else "peer"

    def update_options(
        self,
        *,
        edge: str,
        edge_switch: bool,
        hotkey_switch: bool,
    ) -> None:
        with self._lock:
            self.edge = edge
            self.edge_switch = edge_switch
            self.hotkey_switch = hotkey_switch

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._started:
            return
        permissions = self.backend.preflight()
        if not permissions.capture or not permissions.injection:
            detail = permissions.capture_detail or permissions.injection_detail
            raise InputBackendError(detail or "input permissions are missing")
        self.backend.start(self._handle_capture)
        self._started = True
        self._set_cursor_visible(True)
        log.info("Native input backend started: %s", self.backend.name)

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            self.backend.stop()
        finally:
            try:
                self.backend.release_injected_keys()
                self.backend.release_local_modifiers()
            finally:
                self._set_cursor_visible(True)
        log.info("Native input backend stopped")

    # ---------------------------------------------------------------- capture
    def _handle_capture(self, event: InputEvent, injected: bool) -> bool:
        with self._lock:
            controller = self._controller
            target = self._target
            edge = self.edge
            edge_switch = self.edge_switch
            hotkey_switch = self.hotkey_switch
            local_active = controller == self.self_id and target == self.self_id
            forwarding = controller == self.self_id and target == self.peer_id
            receiving = controller == self.peer_id and target == self.self_id
            passive = controller == self.peer_id and target == self.peer_id
            now = time.monotonic()

        if injected:
            # Never echo our own synthetic input, and never let it steal focus.
            return False

        if not self._started:
            return False

        hotkey = (
            hotkey_switch
            and isinstance(event, InputKeyEvent)
            and event.key == "space"
            and event.pressed
            and not event.repeat
            and "ctrl" in event.mods
            and "alt" in event.mods
        )
        if hotkey:
            with self._lock:
                if now - self._last_hotkey < EVENT_COOLDOWN_SECONDS:
                    return True
                self._last_hotkey = now
            action = "takeover" if controller == self.peer_id else "toggle"
            log.info("%s detected (%s)", HOTKEY_LABEL, action)
            self._request_focus(action, None, None)
            return True

        if local_active:
            self._maybe_edge_switch(event, edge, edge_switch)
            return False

        if forwarding:
            if isinstance(event, InputMoveEvent):
                self._send(event)
                self._maybe_recenter(event)
                # Allowing the hidden local cursor to move gives us continuous
                # relative deltas on both Windows and macOS. Buttons and keys
                # remain suppressible and local clicks cannot leak through.
                return False
            self._send(event)
            return True

        # Either this computer is currently being driven from the peer, or it
        # is idle. Native local input is suppressed; physical activity takes
        # back control. Injected input is handled above and never causes theft.
        if receiving or passive:
            if isinstance(event, InputMoveEvent):
                width, _ = self.backend.screen_size()
                with self._lock:
                    self._takeover_travel += math.hypot(event.dx * width, event.dy * width)
                    should_take = (
                        self._takeover_travel >= TAKEOVER_MOVE_THRESHOLD_PX
                        and now - self._last_focus_request >= EVENT_COOLDOWN_SECONDS
                    )
                    if should_take:
                        self._takeover_travel = 0.0
                if should_take:
                    self._request_focus("takeover", None, None)
                # Windows reports no useful movement delta if the low-level
                # hook blocks the move, so its hidden cursor must be allowed to
                # move. macOS provides raw deltas and stays fully suppressed.
                return bool(getattr(self.backend, "suppress_moves_when_passive", True))
            elif isinstance(event, (InputKeyEvent, InputButtonEvent, InputWheelEvent)):
                if now - self._last_focus_request >= EVENT_COOLDOWN_SECONDS:
                    self._request_focus("takeover", None, None)
            return True
        return False

    def _send(self, event: InputEvent) -> None:
        try:
            self.callbacks.send_input(event_to_wire(event))
        except Exception:  # noqa: BLE001 - never break native event tap
            log.debug("Input send failed", exc_info=True)

    def _request_focus(
        self,
        action: str,
        edge: str | None,
        position: float | None,
    ) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._last_focus_request < 0.15:
                return
            self._last_focus_request = now
        try:
            self.callbacks.request_focus(action, edge, position)
        except Exception:  # noqa: BLE001
            log.debug("Focus request callback failed", exc_info=True)

    def _maybe_edge_switch(self, event: InputEvent, edge: str, enabled: bool) -> None:
        if not enabled or not isinstance(event, InputMoveEvent):
            return
        width, height = self.backend.screen_size()
        if width <= 0 or height <= 0:
            return
        margin = SCREEN_EDGE_MARGIN_PX
        at_edge = False
        outward = False
        distance = 0
        position = 0.5
        if edge == "left":
            at_edge = event.x <= margin
            outward = event.dx < 0
            distance = event.x
            position = min(1.0, max(0.0, event.y / max(1, height - 1)))
        elif edge == "right":
            at_edge = event.x >= width - margin - 1
            outward = event.dx > 0
            distance = width - 1 - event.x
            position = min(1.0, max(0.0, event.y / max(1, height - 1)))
        elif edge == "top":
            at_edge = event.y <= margin
            outward = event.dy < 0
            distance = event.y
            position = min(1.0, max(0.0, event.x / max(1, width - 1)))
        elif edge == "bottom":
            at_edge = event.y >= height - margin - 1
            outward = event.dy > 0
            distance = height - 1 - event.y
            position = min(1.0, max(0.0, event.x / max(1, width - 1)))

        with self._lock:
            if not at_edge:
                self._edge_latched = False
                if distance >= EDGE_REARM_MARGIN_PX:
                    self._edge_armed = True
                return
            if not self._edge_armed or not outward or self._edge_latched:
                return
            # One crossing per armed excursion. A subsequent return must move
            # comfortably away from the edge before another switch can happen.
            self._edge_latched = True
            self._edge_armed = False
        self._request_focus("edge", edge, position)

    def _maybe_recenter(self, event: InputMoveEvent) -> None:
        width, height = self.backend.screen_size()
        if width <= 0 or height <= 0:
            return
        margin = 4
        if (
            event.x <= margin
            or event.y <= margin
            or event.x >= width - margin - 1
            or event.y >= height - margin - 1
        ):
            try:
                self.backend.recenter_pointer()
            except Exception:  # noqa: BLE001
                log.debug("Pointer recenter failed", exc_info=True)

    def handle_remote_event(self, event: InputEvent) -> None:
        """Inject an authenticated remote event and detect target-edge return.

        Tracking remote motion in software is deterministic and avoids relying
        on the OS echoing our own injected events back into the capture tap.
        """
        with self._lock:
            if self._controller != self.peer_id or self._target != self.self_id:
                return
            entry_edge = self._entry_edge
            epoch = self._epoch
            if isinstance(event, InputMoveEvent):
                width, height = self.backend.screen_size()
                if width <= 0 or height <= 0:
                    return
                previous_x, previous_y = self._virtual_pointer
                nx = previous_x + event.dx * width
                ny = previous_y + event.dy * height
                nx = min(width - 1, max(0.0, nx))
                ny = min(height - 1, max(0.0, ny))
                ratio_x = nx / max(1, width - 1)
                ratio_y = ny / max(1, height - 1)
                self._virtual_pointer = (nx, ny)
                should_return = False
                at_edge = False
                was_at_edge = False
                distance = 0
                outward_travel = 0.0
                if entry_edge is not None:
                    margin = SCREEN_EDGE_MARGIN_PX
                    if entry_edge == "left":
                        at_edge = nx <= margin
                        was_at_edge = previous_x <= margin
                        distance = nx
                        should_return = at_edge and event.dx < 0
                        outward_travel = abs(event.dx) * width
                    elif entry_edge == "right":
                        at_edge = nx >= width - margin - 1
                        was_at_edge = previous_x >= width - margin - 1
                        distance = width - 1 - nx
                        should_return = at_edge and event.dx > 0
                        outward_travel = abs(event.dx) * width
                    elif entry_edge == "top":
                        at_edge = ny <= margin
                        was_at_edge = previous_y <= margin
                        distance = ny
                        should_return = at_edge and event.dy < 0
                        outward_travel = abs(event.dy) * height
                    elif entry_edge == "bottom":
                        at_edge = ny >= height - margin - 1
                        was_at_edge = previous_y >= height - margin - 1
                        distance = height - 1 - ny
                        should_return = at_edge and event.dy > 0
                        outward_travel = abs(event.dy) * height
                if entry_edge is not None and not at_edge:
                    self._injected_return_latched = False
                    self._remote_return_push = 0.0
                    if distance >= REMOTE_RETURN_REARM_MARGIN_PX:
                        self._remote_return_armed = True
                if at_edge:
                    # A single event that merely reaches the edge must not
                    # return focus. The user has to keep pushing outward for
                    # at least REMOTE_RETURN_PUSH_PX after arriving there.
                    if not was_at_edge or not should_return or not self._remote_return_armed:
                        self._remote_return_push = 0.0
                    elif time.monotonic() - self._receiving_since >= REMOTE_RETURN_SETTLE_SECONDS:
                        self._remote_return_push += outward_travel
                        if (
                            self._remote_return_push >= REMOTE_RETURN_PUSH_PX
                            and not self._injected_return_latched
                        ):
                            self._injected_return_latched = True
                            self._remote_return_armed = False
                            self._remote_return_push = 0.0
                            self._last_focus_request = time.monotonic()
                            log.info(
                                "Remote cursor pushed through target edge (%.1f px); returning focus",
                                REMOTE_RETURN_PUSH_PX,
                            )
                            try:
                                self.callbacks.request_focus("return", None, None)
                            except Exception:  # noqa: BLE001
                                log.debug("Return focus callback failed", exc_info=True)
                            return
                del ratio_x, ratio_y, epoch
        try:
            self.backend.inject(event)
        except Exception:  # noqa: BLE001
            log.debug("Remote input injection failed", exc_info=True)

    def release_remote_modifiers(self) -> None:
        try:
            self.backend.release_injected_keys()
        except Exception:  # noqa: BLE001
            log.debug("Could not release injected keys", exc_info=True)

    # --------------------------------------------------------------- cursor
    def _warp_to_entry(self, edge: str, position: float) -> None:
        width, height = self.backend.screen_size()
        if width <= 0 or height <= 0:
            return
        ratio = min(1.0, max(0.0, position))
        inset = 2
        if edge == "left":
            self.backend.warp_pointer(inset, int(ratio * (height - 1)))
        elif edge == "right":
            self.backend.warp_pointer(width - inset - 1, int(ratio * (height - 1)))
        elif edge == "top":
            self.backend.warp_pointer(int(ratio * (width - 1)), inset)
        elif edge == "bottom":
            self.backend.warp_pointer(int(ratio * (width - 1)), height - inset - 1)

    def _restore_pointer(self, *, inset: bool) -> None:
        pointer = self._saved_pointer or self.backend.pointer_position()
        if inset:
            width, height = self.backend.screen_size()
            if self.edge == "left":
                pointer = (min(width - 1, 24), pointer[1])
            elif self.edge == "right":
                pointer = (max(0, width - 25), pointer[1])
            elif self.edge == "top":
                pointer = (pointer[0], min(height - 1, 24))
            else:
                pointer = (pointer[0], max(0, height - 25))
        self.backend.warp_pointer(pointer[0], pointer[1])

    def _set_cursor_visible(self, visible: bool) -> None:
        with self._backend_lock:
            if visible == (not self._cursor_hidden):
                return
            try:
                self.backend.set_cursor_visible(visible)
                self._cursor_hidden = not visible
            except Exception:  # noqa: BLE001
                log.debug("Cursor visibility update failed", exc_info=True)

    @property
    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "controller": self._controller,
                "target": self._target,
                "epoch": self._epoch,
                "entry_edge": self._entry_edge,
                "reason": self._reason,
                "started": self._started,
            }


__all__ = ["InputBridge", "InputBridgeCallbacks"]
