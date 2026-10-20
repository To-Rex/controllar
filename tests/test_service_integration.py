from __future__ import annotations

import asyncio

import pytest

from torexlink.pairing_payload import PairingPayload


async def wait_for(predicate, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.03)
    raise AssertionError("condition was not met before timeout")


@pytest.mark.asyncio
async def test_pair_connect_reconnect_and_forget(service_factory) -> None:
    host, host_backend, host_clipboard = service_factory(
        name="host", hosting=True, auto_reconnect=False
    )
    guest, guest_backend, guest_clipboard = service_factory(
        name="guest", hosting=False, auto_reconnect=False
    )
    try:
        await host.start()
        await guest.start()
        assert host.snapshot().listener_port
        pairing = await host.begin_pairing()
        assert len(pairing.code) == 6
        assert pairing.qr_uri.startswith("torexlink://pair")
        payload = PairingPayload.parse(pairing.qr_uri)
        assert payload.code == pairing.code
        record = await guest.pair_with_payload(payload)
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)
        await wait_for(lambda: guest_backend.started and host_backend.started, timeout=6)
        guest_snapshot = guest.snapshot()
        host_snapshot = host.snapshot()
        assert guest_snapshot.connection and guest_snapshot.connection.role == "guest"
        assert host_snapshot.connection and host_snapshot.connection.role == "host"
        assert guest_snapshot.focus is not None
        assert host_snapshot.focus is not None
        assert guest_backend.started and host_backend.started
        assert guest_snapshot.connection.tls_version == "TLS 1.3"
        assert record.peer_id == host.identity.device_id

        await guest.disconnect()
        await wait_for(lambda: host.snapshot().connection is None)
        await asyncio.sleep(0.1)
        await guest.connect_peer(record.peer_id)
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)

        await guest.remove_peer(record.peer_id)
        assert not guest.peer_store.get(record.peer_id)
        assert guest.snapshot().connection is None
    finally:
        await guest.stop()
        await host.stop()


@pytest.mark.asyncio
async def test_wrong_pairing_code_is_rejected(service_factory) -> None:
    host, _, _ = service_factory(name="host2", hosting=True)
    guest, _, _ = service_factory(name="guest2", hosting=False)
    try:
        await host.start()
        await guest.start()
        pairing = await host.begin_pairing()
        bad_code = "000000" if pairing.code != "000000" else "111111"
        from torexlink.core.service import ServiceError

        with pytest.raises(ServiceError):
            await guest.pair_with_code("127.0.0.1", host.snapshot().listener_port, bad_code)
        assert guest.snapshot().connection is None
        assert host.snapshot().pairing is not None
        assert host.snapshot().pairing.attempts_remaining < 5
    finally:
        await guest.stop()
        await host.stop()


@pytest.mark.asyncio
async def test_clipboard_sync_and_size_limit(service_factory) -> None:
    host, _, host_clipboard = service_factory(name="host3", hosting=True)
    guest, _, guest_clipboard = service_factory(name="guest3", hosting=False)
    try:
        await host.start()
        await guest.start()
        pairing = await host.begin_pairing()
        payload = PairingPayload.parse(pairing.qr_uri)
        await guest.pair_with_payload(payload)
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)
        guest_clipboard.text = "bufer matni"
        # The poll interval is deliberately short but still asynchronous.
        await asyncio.sleep(1.2)
        assert host_clipboard.text == "bufer matni"
        host_clipboard.text = "javob matni"
        await asyncio.sleep(1.2)
        assert guest_clipboard.text == "javob matni"
    finally:
        await guest.stop()
        await host.stop()


@pytest.mark.asyncio
async def test_focus_switch_hotkey_remote_input_and_takeover(service_factory) -> None:
    from torexlink.input.events import InputKeyEvent, InputMoveEvent

    host, host_backend, _ = service_factory(name="focus-host", hosting=True)
    guest, guest_backend, _ = service_factory(name="focus-guest", hosting=False)
    try:
        await host.start()
        await guest.start()
        pairing = await host.begin_pairing()
        await guest.pair_with_payload(PairingPayload.parse(pairing.qr_uri))
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)
        await wait_for(lambda: guest.snapshot().focus is not None)
        await wait_for(lambda: guest_backend.started and host_backend.started, timeout=6)
        guest_id = guest.identity.device_id
        host_id = host.identity.device_id
        # The joining machine owns focus initially.
        assert guest.snapshot().focus.local_controller
        assert guest.snapshot().focus.local_target
        assert not host.snapshot().focus.local_controller
        # Idle peer cursor is hidden, not left visible as a second cursor.
        await wait_for(lambda: host_backend.cursor_visible is False)

        guest_backend.emit(InputKeyEvent(key="space", pressed=True, mods=("ctrl", "alt")))
        await wait_for(
            lambda: guest.snapshot().focus is not None
            and guest.snapshot().focus.target_id == host_id
        )
        assert guest.snapshot().focus.controller_id == guest_id
        await wait_for(lambda: host_backend.cursor_visible)
        assert host_backend.cursor == (960, 540)
        # The controller (guest) hides and freezes its own pointer; only the
        # target computer's cursor may visibly move.
        await wait_for(lambda: guest_backend.cursor_visible is False)

        move_suppressed = guest_backend.emit(
            InputMoveEvent(dx=0.01, dy=0.0, x=500, y=500)
        )
        assert move_suppressed is True  # sender freezes its local pointer
        assert guest_backend.freeze_calls > 0
        await wait_for(lambda: len(host_backend.injected) > 0)
        assert any(isinstance(item, InputMoveEvent) for item in host_backend.injected)

        # Moving the target's physical mouse must not steal focus (injected
        # remote motion must never be mistaken for a takeover either).
        suppressed_move = host_backend.emit(
            InputMoveEvent(dx=0.2, dy=0.1, x=800, y=600)
        )
        assert suppressed_move is True
        await asyncio.sleep(0.45)
        assert host.snapshot().focus.local_target is True
        assert guest.snapshot().focus.local_controller is True

        # A deliberate click/key is the takeover gesture.
        suppressed = host_backend.emit(InputKeyEvent(key="a", pressed=True, mods=()))
        assert suppressed is True
        await wait_for(lambda: host.snapshot().focus.local_target is True)
        await wait_for(lambda: guest.snapshot().focus.local_controller is False)
        # The now-idle guest hides and freezes its pointer as well; only the
        # active computer should show a cursor.
        await wait_for(lambda: guest_backend.cursor_visible is False)
        await wait_for(lambda: guest_backend.pointer_frozen is True)
    finally:
        await guest.stop()
        await host.stop()


@pytest.mark.asyncio
async def test_edge_switch_places_remote_cursor_on_opposite_edge(service_factory) -> None:
    from torexlink.input.events import InputMoveEvent

    host, host_backend, _ = service_factory(name="edge-host", hosting=True)
    guest, guest_backend, _ = service_factory(
        name="edge-guest", hosting=False, edge_switch=True, edge="right"
    )
    try:
        await host.start()
        await guest.start()
        pairing = await host.begin_pairing()
        await guest.pair_with_payload(PairingPayload.parse(pairing.qr_uri))
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)
        await wait_for(lambda: guest.snapshot().focus is not None)
        await wait_for(lambda: guest_backend.started and host_backend.started, timeout=6)
        guest_backend.cursor = (1919, 500)
        guest_backend.emit(InputMoveEvent(dx=0.02, dy=0.0, x=1919, y=500))
        await wait_for(lambda: host.snapshot().focus is not None and host.snapshot().focus.local_target)
        host_focus = host.snapshot().focus
        assert host_focus.entry_edge == "left"
        await wait_for(lambda: host_backend.cursor[0] <= 3)
    finally:
        await guest.stop()
        await host.stop()


@pytest.mark.asyncio
async def test_auto_reconnect_after_transport_drop(service_factory) -> None:
    host, _, _ = service_factory(name="auto-host", hosting=True, auto_reconnect=False)
    guest, _, _ = service_factory(name="auto-guest", hosting=False, auto_reconnect=True)
    try:
        await host.start()
        await guest.start()
        pairing = await host.begin_pairing()
        await guest.pair_with_payload(PairingPayload.parse(pairing.qr_uri))
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)
        host_link = host._active
        assert host_link is not None
        await host_link.session.close("test_drop")
        await wait_for(lambda: guest.snapshot().connection is None, timeout=4)
        await wait_for(lambda: guest.snapshot().connection is not None, timeout=12)
        await wait_for(lambda: host.snapshot().connection is not None, timeout=8)
    finally:
        await guest.stop()
        await host.stop()


@pytest.mark.asyncio
async def test_identity_and_token_survive_restart_auto_connect(tmp_path) -> None:
    from tests.conftest import FakeClipboard, FakeInputBackend, configure_settings, make_paths
    from torexlink.core.service import ToRexLinkService

    host_paths = make_paths(tmp_path / "restart-host")
    guest_paths = make_paths(tmp_path / "restart-guest")
    host_store = configure_settings(
        host_paths, hosting_enabled=True, auto_reconnect=False, device_name="Host"
    )
    guest_store = configure_settings(
        guest_paths, hosting_enabled=False, auto_reconnect=True, device_name="Guest"
    )
    host_backend = FakeInputBackend()
    guest_backend = FakeInputBackend()

    def create_host(port: int) -> ToRexLinkService:
        return ToRexLinkService(
            host_paths,
            settings_store=host_store,
            input_backend_factory=lambda: host_backend,
            clipboard_factory=FakeClipboard,
            enable_discovery=False,
            preferred_port=port,
            address_provider=lambda: ("127.0.0.1",),
        )

    def create_guest() -> ToRexLinkService:
        return ToRexLinkService(
            guest_paths,
            settings_store=guest_store,
            input_backend_factory=lambda: guest_backend,
            clipboard_factory=FakeClipboard,
            enable_discovery=False,
            preferred_port=0,
            address_provider=lambda: ("127.0.0.1",),
        )

    host = create_host(0)
    guest = create_guest()
    old_host_id = None
    old_guest_id = None
    try:
        await host.start()
        await guest.start()
        old_host_id = host.identity.device_id
        old_guest_id = guest.identity.device_id
        old_port = host.snapshot().listener_port
        pairing = await host.begin_pairing()
        await guest.pair_with_payload(PairingPayload.parse(pairing.qr_uri))
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)
    finally:
        await guest.stop()
        await host.stop()

    # New process-equivalent instances with the same on-disk identity/peer data.
    host2 = create_host(old_port)
    guest2 = create_guest()
    try:
        await host2.start()
        await guest2.start()
        assert host2.identity.device_id == old_host_id
        assert guest2.identity.device_id == old_guest_id
        await wait_for(lambda: guest2.snapshot().connection is not None, timeout=15)
        await wait_for(lambda: host2.snapshot().connection is not None, timeout=8)
        assert guest2.snapshot().connection.peer_id == old_host_id
        assert host2.snapshot().connection.peer_id == old_guest_id
    finally:
        await guest2.stop()
        await host2.stop()


@pytest.mark.asyncio
async def test_edge_return_disarms_until_pointer_moves_inward(service_factory) -> None:
    from torexlink.input.events import InputMoveEvent

    host, host_backend, _ = service_factory(name="roundtrip-host", hosting=True)
    guest, guest_backend, _ = service_factory(
        name="roundtrip-guest", hosting=False, edge_switch=True, edge="right"
    )
    try:
        await host.start()
        await guest.start()
        pairing = await host.begin_pairing()
        await guest.pair_with_payload(PairingPayload.parse(pairing.qr_uri))
        await wait_for(lambda: guest.snapshot().connection is not None)
        await wait_for(lambda: host.snapshot().connection is not None)
        await wait_for(lambda: guest.snapshot().focus is not None)
        await wait_for(lambda: guest_backend.started and host_backend.started, timeout=6)

        # 1. Guest crosses its right edge into the host.
        guest_backend.cursor = (1919, 540)
        guest_backend.emit(InputMoveEvent(dx=0.02, dy=0.0, x=1919, y=540))
        await wait_for(lambda: host.snapshot().focus and host.snapshot().focus.local_target)
        await wait_for(lambda: host_backend.cursor[0] <= 3)

        # 2. Remote cursor moves inward, arming the return gesture, then exits
        #    through the same edge and returns focus to the guest.
        guest_backend.emit(InputMoveEvent(dx=0.05, dy=0.0, x=900, y=540))
        await wait_for(lambda: host_backend.cursor[0] >= 40, timeout=3)
        # The target deliberately ignores edge returns during the first
        # settle window, so let it expire before testing return.
        await asyncio.sleep(0.4)
        # Reaching the edge is not enough: this event is injected but must not
        # hand focus back by itself.
        guest_backend.emit(InputMoveEvent(dx=-0.12, dy=0.0, x=900, y=540))
        await wait_for(lambda: host_backend.cursor[0] <= 3, timeout=3)
        await asyncio.sleep(0.1)
        guest_focus = guest.snapshot().focus
        assert guest_focus is not None
        assert guest_focus.local_controller is True
        assert guest_focus.local_target is False
        # A deliberate additional outward push (57.6 px) returns to the Mac.
        guest_backend.emit(InputMoveEvent(dx=-0.03, dy=0.0, x=900, y=540))
        await wait_for(
            lambda: guest.snapshot().focus is not None
            and guest.snapshot().focus.local_target
            and guest.snapshot().focus.controller_id == guest.identity.device_id,
            timeout=6,
        )
        await wait_for(lambda: host.snapshot().focus and not host.snapshot().focus.local_target)

        # 3. Immediately pushing outward again must NOT bounce straight back.
        guest_backend.emit(InputMoveEvent(dx=0.05, dy=0.0, x=1919, y=540))
        await asyncio.sleep(0.5)
        guest_focus = guest.snapshot().focus
        assert guest_focus is not None
        assert guest_focus.local_target is True
        assert guest_focus.local_controller is True

        # 4. Moving away from the edge rearms it; a deliberate second crossing
        #    still works in the same session.
        guest_backend.emit(InputMoveEvent(dx=-0.07, dy=0.0, x=1780, y=540))
        await asyncio.sleep(0.15)
        guest_backend.emit(InputMoveEvent(dx=0.07, dy=0.0, x=1919, y=540))
        await wait_for(lambda: host.snapshot().focus and host.snapshot().focus.local_target)
        assert host.snapshot().focus.target_id == host.identity.device_id
    finally:
        await guest.stop()
        await host.stop()
