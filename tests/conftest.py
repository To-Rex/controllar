from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from torexlink.input.base import InputPermissions
from torexlink.input.events import InputEvent, InputMoveEvent
from torexlink.paths import AppPaths
from torexlink.settings import SettingsStore


class FakeInputBackend:
    name = "Test input"

    def __init__(self) -> None:
        self.callback: Callable[[InputEvent, bool], bool] | None = None
        self.injected: list[InputEvent] = []
        self.cursor_visible = True
        self.cursor = (100, 100)
        self.started = False

    def preflight(self) -> InputPermissions:
        return InputPermissions(capture=True, injection=True)

    def request_permissions(self) -> None:
        return None

    def start(self, callback: Callable[[InputEvent, bool], bool]) -> None:
        self.callback = callback
        self.started = True

    def stop(self) -> None:
        self.started = False

    def inject(self, event: InputEvent) -> None:
        self.injected.append(event)
        if isinstance(event, InputMoveEvent):
            self.cursor = (
                self.cursor[0] + int(event.dx * self.screen_size()[0]),
                self.cursor[1] + int(event.dy * self.screen_size()[1]),
            )

    def emit(self, event: InputEvent, injected: bool = False) -> bool:
        assert self.callback is not None
        return self.callback(event, injected)

    def warp_pointer(self, x: int, y: int) -> None:
        self.cursor = (x, y)

    def pointer_position(self) -> tuple[int, int]:
        return self.cursor

    def screen_size(self) -> tuple[int, int]:
        return (1920, 1080)

    def recenter_pointer(self) -> None:
        self.cursor = (960, 540)

    def set_cursor_visible(self, visible: bool) -> None:
        self.cursor_visible = visible

    def release_injected_keys(self) -> None:
        return None

    def release_local_modifiers(self) -> None:
        return None


class FakeClipboard:
    def __init__(self) -> None:
        self.text = ""

    def read_text(self) -> str:
        return self.text

    def write_text(self, text: str) -> None:
        self.text = text


def make_paths(root: Path) -> AppPaths:
    paths = AppPaths.discover(root)
    paths.ensure()
    return paths


def configure_settings(paths: AppPaths, **changes: Any) -> SettingsStore:
    store = SettingsStore(paths.settings_file)
    store.save()
    store.update(changes)
    return store


@pytest.fixture
def service_factory(tmp_path):
    from torexlink.core.service import ToRexLinkService

    services = []
    backends: list[FakeInputBackend] = []
    clipboards: list[FakeClipboard] = []

    def factory(
        *,
        name: str = "device",
        hosting: bool = True,
        auto_reconnect: bool = False,
        **changes: Any,
    ) -> tuple[ToRexLinkService, FakeInputBackend, FakeClipboard]:
        paths = make_paths(tmp_path / name)
        settings = {
            "hosting_enabled": hosting,
            "auto_reconnect": auto_reconnect,
            "auto_start": False,
            "onboarding_complete": True,
            **changes,
        }
        for key in list(settings):
            if key == "auto_start":
                settings.pop(key)
        store = configure_settings(paths, **settings)
        backend = FakeInputBackend()
        clipboard = FakeClipboard()
        service = ToRexLinkService(
            paths,
            settings_store=store,
            input_backend_factory=lambda: backend,
            clipboard_factory=lambda: clipboard,
            enable_discovery=False,
            preferred_port=0,
            address_provider=lambda: ("127.0.0.1",),
        )
        services.append(service)
        backends.append(backend)
        clipboards.append(clipboard)
        return service, backend, clipboard

    yield factory

    async def cleanup():
        for service in services:
            await service.stop()

    # The test itself controls the loop; pytest-asyncio will have closed it by
    # teardown, so individual tests must stop services explicitly.
