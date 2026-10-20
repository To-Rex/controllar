#!/usr/bin/env python3
"""Synthetic localhost input-path benchmark (developer tool)."""

from __future__ import annotations

import asyncio
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.conftest import (  # noqa: E402
    FakeClipboard,
    FakeInputBackend,
    configure_settings,
    make_paths,
)
from torexlink.core.service import ToRexLinkService  # noqa: E402
from torexlink.input.events import InputKeyEvent, InputMoveEvent  # noqa: E402
from torexlink.pairing_payload import PairingPayload  # noqa: E402


async def wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise TimeoutError("condition was not met")


async def main() -> int:
    root = Path("/tmp/torexlink-benchmark")
    shutil.rmtree(root, ignore_errors=True)
    host_paths = make_paths(root / "host")
    guest_paths = make_paths(root / "guest")
    host_settings = configure_settings(
        host_paths, hosting_enabled=True, auto_reconnect=False, device_name="Host"
    )
    guest_settings = configure_settings(
        guest_paths, hosting_enabled=False, auto_reconnect=False, device_name="Guest"
    )
    host_backend = FakeInputBackend()
    guest_backend = FakeInputBackend()
    host = ToRexLinkService(
        host_paths,
        settings_store=host_settings,
        input_backend_factory=lambda: host_backend,
        clipboard_factory=FakeClipboard,
        enable_discovery=False,
        preferred_port=0,
        address_provider=lambda: ("127.0.0.1",),
    )
    guest = ToRexLinkService(
        guest_paths,
        settings_store=guest_settings,
        input_backend_factory=lambda: guest_backend,
        clipboard_factory=FakeClipboard,
        enable_discovery=False,
        preferred_port=0,
        address_provider=lambda: ("127.0.0.1",),
    )
    try:
        await host.start()
        await guest.start()
        pairing = await host.begin_pairing()
        await guest.pair_with_payload(PairingPayload.parse(pairing.qr_uri))
        await wait_for(
            lambda: guest.snapshot().connection
            and host.snapshot().connection
            and guest_backend.started
            and host_backend.started
        )
        guest_backend.emit(InputKeyEvent(key="space", pressed=True, mods=("ctrl", "alt")))
        await wait_for(lambda: host.snapshot().focus and host.snapshot().focus.local_target)
        await asyncio.sleep(0.15)
        move = InputMoveEvent(dx=0.0001, dy=0.0, x=500, y=500)
        samples: list[float] = []
        for _ in range(100):
            before = len(host_backend.injected)
            started = time.perf_counter()
            guest_backend.emit(move)
            while len(host_backend.injected) <= before:
                await asyncio.sleep(0)
            samples.append((time.perf_counter() - started) * 1000.0)
        ordered = sorted(samples)
        print(
            "localhost input round-trip (ms): "
            f"avg={statistics.fmean(samples):.3f} "
            f"p95={ordered[94]:.3f} "
            f"max={ordered[-1]:.3f}"
        )
        return 0
    finally:
        await guest.stop()
        await host.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
