"""Regression tests for the two diagnosed input-bridge bugs.

1. The controller cursor kept moving on macOS while forwarding because the
   macOS backend never declared ``suppress_moves_when_forwarding``.
2. The first edge push after a return was always swallowed because the
   cursor was parked 25px from the edge while the edge only re-arms at
   >= EDGE_REARM_MARGIN_PX (56px).
"""

from __future__ import annotations

import pytest

from tests.conftest import FakeInputBackend
from torexlink.constants import EDGE_REARM_MARGIN_PX
from torexlink.input.bridge import InputBridge, InputBridgeCallbacks
from torexlink.input.events import InputMoveEvent


def _make_bridge(backend):
    sent: list[dict] = []
    focus_requests: list[tuple[str, str | None, float | None]] = []

    def request_focus(action, edge, position):
        focus_requests.append((action, edge, position))

    bridge = InputBridge(
        self_id="self",
        peer_id="peer",
        backend=backend,
        callbacks=InputBridgeCallbacks(
            send_input=sent.append,
            request_focus=request_focus,
        ),
        edge="right",
        edge_switch=True,
        hotkey_switch=True,
    )
    return bridge, sent, focus_requests


def test_restore_parks_beyond_rearm_margin() -> None:
    """After a return to local, the cursor must start outside the rearm zone."""
    backend = FakeInputBackend()
    bridge, _, _ = _make_bridge(backend)
    bridge.apply_focus(epoch=1, controller="self", target="self", reason="remote-return")
    width, _ = backend.screen_size()
    distance_from_edge = width - 1 - backend.cursor[0]
    assert distance_from_edge >= EDGE_REARM_MARGIN_PX


def test_first_push_after_return_crosses_on_first_attempt() -> None:
    """A single continuous push after a return must cross without a rehearsal.

    Regression: the cursor used to be parked inside the rearm zone, so the
    first push hit the edge with the edge still disarmed and was silently
    swallowed ("must try twice to cross").
    """
    backend = FakeInputBackend()
    bridge, _, focus_requests = _make_bridge(backend)
    bridge.apply_focus(epoch=1, controller="self", target="self", reason="remote-return")
    bridge.start()
    width, height = backend.screen_size()

    # One continuous outward push from the parked position to the edge.
    for x in range(backend.cursor[0], width - 1, 3):
        bridge._handle_capture(InputMoveEvent(dx=0.002, dy=0.0, x=x, y=540), False)
    bridge._handle_capture(InputMoveEvent(dx=0.002, dy=0.0, x=width - 1, y=540), False)

    assert focus_requests, "edge crossing was not requested on the first push"
    action, edge, position = focus_requests[-1]
    assert action == "edge"
    assert edge == "right"
    assert position == pytest.approx(540 / max(1, height - 1), abs=0.01)


def test_moves_are_suppressed_locally_while_forwarding() -> None:
    """Move events must be swallowed at the tap while driving the peer."""
    backend = FakeInputBackend()  # suppress_moves_when_forwarding = True
    bridge, sent, _ = _make_bridge(backend)
    bridge.apply_focus(epoch=2, controller="self", target="peer", reason="edge")
    bridge.start()
    suppressed = bridge._handle_capture(
        InputMoveEvent(dx=0.01, dy=0.0, x=960, y=540), False
    )
    assert suppressed is True
    assert len(sent) == 1 and sent[0]["t"] == "move"


def test_moves_without_backend_suppression_flag_are_not_suppressed() -> None:
    """Backends that need deltas to keep flowing (Windows) stay unsuppressed."""

    class NoSuppressBackend(FakeInputBackend):
        suppress_moves_when_forwarding = False

    backend = NoSuppressBackend()
    bridge, sent, _ = _make_bridge(backend)
    bridge.apply_focus(epoch=2, controller="self", target="peer", reason="edge")
    bridge.start()
    suppressed = bridge._handle_capture(
        InputMoveEvent(dx=0.01, dy=0.0, x=960, y=540), False
    )
    assert suppressed is False
    assert len(sent) == 1 and sent[0]["t"] == "move"


def test_receiving_clicks_and_moves_land_locally() -> None:
    """Two mice, one device: while being driven, local input lands here."""

    backend = FakeInputBackend()
    bridge, _, focus_requests = _make_bridge(backend)
    bridge.apply_focus(epoch=2, controller="peer", target="self", reason="edge")
    bridge.start()
    # A tiny bump stays below the takeover threshold.
    suppressed = bridge._handle_capture(
        InputMoveEvent(dx=0.001, dy=0.0, x=960, y=540), False
    )
    assert suppressed is False
    assert not any(action == "takeover" for action, _, _ in focus_requests)
    # Local clicks land locally and must NOT request any focus change.
    from torexlink.input.events import InputButtonEvent

    suppressed = bridge._handle_capture(InputButtonEvent(button="left", pressed=True), False)
    assert suppressed is False
    assert focus_requests == []


def test_receiving_mouse_swipe_takes_over() -> None:
    """A deliberate swipe of the driven machine's own mouse takes control.

    The peer stops forwarding so the two mice never fight over the cursor.
    """
    backend = FakeInputBackend()
    bridge, _, focus_requests = _make_bridge(backend)
    bridge.apply_focus(epoch=2, controller="peer", target="self", reason="edge")
    bridge.start()
    bridge._handle_capture(InputMoveEvent(dx=0.004, dy=0.0, x=960, y=540), False)
    assert any(action == "takeover" for action, _, _ in focus_requests)


def test_passive_mouse_motion_takes_over() -> None:
    """Moving the idle machine's mouse pulls the cursor to that machine."""
    backend = FakeInputBackend()
    bridge, _, focus_requests = _make_bridge(backend)
    bridge.apply_focus(epoch=2, controller="peer", target="peer", reason="initial")
    bridge.start()
    bridge._handle_capture(InputMoveEvent(dx=0.004, dy=0.0, x=100, y=100), False)
    assert any(action == "takeover" for action, _, _ in focus_requests)


def test_receiving_takeover_then_entry_edge_crosses_back() -> None:
    """Grab the driven cursor, then push it back through the entry edge."""
    import time as _time

    backend = FakeInputBackend()
    bridge, _, focus_requests = _make_bridge(backend)
    bridge.apply_focus(
        epoch=2, controller="peer", target="self", reason="edge", entry_edge="left"
    )
    bridge.start()
    # A swipe of the local mouse takes over — the cursor stays here.
    bridge._handle_capture(InputMoveEvent(dx=0.004, dy=0.0, x=960, y=540), False)
    assert any(action == "takeover" for action, _, _ in focus_requests)
    # The takeover preserves the entry edge (the bridge keeps it when the
    # transition carries none)...
    bridge.apply_focus(epoch=3, controller="self", target="self", reason="takeover")
    assert bridge._entry_edge == "left"
    # ...so after a rearm move into the screen, pushing through the entry
    # edge crosses back to the peer like a real monitor edge.
    _time.sleep(0.2)
    bridge._handle_capture(InputMoveEvent(dx=0.02, dy=0.0, x=200, y=540), False)
    bridge._handle_capture(InputMoveEvent(dx=-0.01, dy=0.0, x=0, y=540), False)
    assert any(
        action == "edge" and edge == "left" for action, edge, _ in focus_requests
    )


def test_return_requires_arming() -> None:
    """A pull-back through the entry edge must NOT return focus before the
    cursor has moved into the screen (rearm margin)."""
    import time as _time

    backend = FakeInputBackend()
    bridge, _, focus_requests = _make_bridge(backend)
    bridge.apply_focus(
        epoch=2, controller="peer", target="self", reason="edge", entry_edge="left"
    )
    bridge.start()
    # Move into the screen to arm the return, let the settle window expire,
    # then pull back THROUGH the edge in one continuous motion (no dwell).
    bridge.handle_remote_event(InputMoveEvent(dx=0.05, dy=0.0))
    _time.sleep(0.7)
    bridge._virtual_pointer = (5.0, 5.0)
    bridge.handle_remote_event(InputMoveEvent(dx=-0.01, dy=0.0))
    bridge.handle_remote_event(InputMoveEvent(dx=-0.01, dy=0.0))
    bridge.handle_remote_event(InputMoveEvent(dx=-0.01, dy=0.0))
    # Continuous push after the cursor is back at the edge: accumulates but
    # stays below the push threshold, so no return.
    assert not any(action == "return" for action, _, _ in focus_requests)
    # One more solid push crosses the threshold: the return fires.
    _time.sleep(0.4)
    bridge.handle_remote_event(InputMoveEvent(dx=-0.05, dy=0.0))
    assert any(action == "return" for action, _, _ in focus_requests)


def test_edge_crossing_retries_when_throttled() -> None:
    """A crossing that races a takeover must not be silently swallowed."""
    import time as _time

    backend = FakeInputBackend()
    bridge, _, focus_requests = _make_bridge(backend)
    bridge.apply_focus(epoch=1, controller="self", target="self", reason="initial")
    bridge.start()
    # Occupy the focus-request throttle window (as a fresh takeover would).
    assert bridge._request_focus("takeover", None, None) is True
    width, _ = backend.screen_size()
    # Immediate push to the edge: throttled, but the edge stays armed.
    bridge._handle_capture(InputMoveEvent(dx=0.02, dy=0.0, x=width - 1, y=540), False)
    assert not any(action == "edge" for action, _, _ in focus_requests)
    # Continued push after the throttle window passes crosses.
    _time.sleep(0.2)
    bridge._handle_capture(InputMoveEvent(dx=0.02, dy=0.0, x=width - 1, y=540), False)
    assert any(action == "edge" for action, _, _ in focus_requests)
