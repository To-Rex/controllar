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
