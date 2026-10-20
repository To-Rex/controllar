#!/usr/bin/env python3
"""Generate documentation screenshots using the real ToRexLink core.

The harness uses the same service, pairing, focus and page code as the app. A
non-invasive fake input backend is injected so a documentation capture never
grabs the developer's physical keyboard or mouse.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from torexlink.core.service import ToRexLinkService  # noqa: E402
from torexlink.input.base import InputPermissions  # noqa: E402
from torexlink.input.events import InputEvent  # noqa: E402
from torexlink.pairing_payload import PairingPayload  # noqa: E402
from torexlink.paths import AppPaths  # noqa: E402
from torexlink.settings import SettingsStore  # noqa: E402
from torexlink.ui.controller import ToRexLinkController  # noqa: E402
from torexlink.ui.main_window import MainWindow  # noqa: E402
from torexlink.ui.onboarding import OnboardingDialog  # noqa: E402

OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)


class NoopInput:
    name = "capture-noop"

    def preflight(self) -> InputPermissions:
        return InputPermissions(True, True)

    def request_permissions(self) -> None:
        return None

    def start(self, callback) -> None:
        self.callback = callback

    def stop(self) -> None:
        return None

    def inject(self, event: InputEvent) -> None:
        return None

    def warp_pointer(self, x: int, y: int) -> None:
        return None

    def pointer_position(self) -> tuple[int, int]:
        return (960, 540)

    def screen_size(self) -> tuple[int, int]:
        return (1920, 1080)

    def recenter_pointer(self) -> None:
        return None

    def set_cursor_visible(self, visible: bool) -> None:
        return None

    def release_injected_keys(self) -> None:
        return None

    def release_local_modifiers(self) -> None:
        return None


class NoopClipboard:
    def read_text(self) -> str:
        return ""

    def write_text(self, text: str) -> None:
        return None


def main() -> int:
    app = QApplication([])
    app.setStyle("Fusion")
    tmp = Path(tempfile.mkdtemp(prefix="torexlink-shots-"))
    services: list[ToRexLinkService] = []
    controllers: list[ToRexLinkController] = []
    state = {"phase": "", "finished": False}

    def make(name: str, device_name: str, hosting: bool):
        paths = AppPaths.discover(tmp / name)
        paths.ensure()
        store = SettingsStore(paths.settings_file)
        store.save()
        store.update(
            {
                "onboarding_complete": True,
                "device_name": device_name,
                "hosting_enabled": hosting,
                "auto_reconnect": False,
                "language": "uz",
                "theme": "light",
            }
        )
        service = ToRexLinkService(
            paths,
            settings_store=store,
            input_backend_factory=NoopInput,
            clipboard_factory=NoopClipboard,
            enable_discovery=False,
            preferred_port=0,
            address_provider=lambda: ("127.0.0.1",),
        )
        controller = ToRexLinkController(service)
        services.append(service)
        controllers.append(controller)
        return service, controller

    _, host_controller = make("host", "Studio Mac", True)
    _, guest_controller = make("guest", "MacBook Pro", False)
    window = MainWindow(host_controller)
    window.resize(1240, 820)
    window.show()
    host_controller.start()
    guest_controller.start()

    def wait_until(predicate, then, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout

        def poll() -> None:
            if predicate():
                QTimer.singleShot(200, then)
            elif time.monotonic() >= deadline:
                print("timed out waiting for screenshot state", file=sys.stderr)
                finish(1)
            else:
                QTimer.singleShot(80, poll)

        QTimer.singleShot(80, poll)

    def capture(name: str, widget=None) -> None:
        target = widget or window
        app.processEvents()
        time.sleep(0.12)
        app.processEvents()
        if hasattr(window, "toast"):
            window.toast.hide()
        path = OUT / name
        target.grab().save(str(path))
        print("captured", path.relative_to(ROOT))

    def failures(error: BaseException) -> None:
        print("screenshot pairing failed:", host_controller.error_message(error), file=sys.stderr)
        finish(1)

    def finish(code: int) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        for controller in controllers:
            controller.shutdown()
        app.exit(code)

    def begin_pairing_shot() -> None:
        state["phase"] = "pairing"
        host_controller.begin_pairing(on_success=paired_for_screenshot, on_error=failures)

    def paired_for_screenshot(info: Any) -> None:
        window.navigate("connect")
        connect_page = window.pages["connect"]
        if not connect_page.qr_wrap.isVisible():
            connect_page.qr_toggle.click()
        app.processEvents()
        QTimer.singleShot(350, lambda: capture("connect-pairing.png"))
        QTimer.singleShot(700, start_real_pairing)

    def start_real_pairing() -> None:
        state["phase"] = "connecting"
        host_controller.cancel_pairing()
        QTimer.singleShot(250, lambda: host_controller.begin_pairing(
            on_success=on_host_pairing, on_error=failures
        ))

    def on_host_pairing(info: Any) -> None:
        payload = PairingPayload.parse(info.qr_uri)
        guest_controller.pair_with_payload(payload, on_success=on_guest_paired, on_error=failures)

    def on_guest_paired(_record: Any) -> None:
        wait_until(
            lambda: host_controller.snapshot.connection is not None
            and guest_controller.snapshot.connection is not None,
            capture_connected,
        )

    def capture_connected() -> None:
        if state["phase"] == "captured":
            return
        state["phase"] = "captured"
        window.navigate("home")
        capture("home-light.png")
        window.navigate("devices")
        capture("devices-light.png")
        window.navigate("diagnostics")
        capture("diagnostics-light.png")
        QTimer.singleShot(250, capture_onboarding)

    def capture_onboarding() -> None:
        dialog = OnboardingDialog(guest_controller)
        dialog.show()
        app.processEvents()
        capture("onboarding-light.png", dialog)
        dialog.close()
        dialog.deleteLater()
        host_controller.set_theme("dark")
        QTimer.singleShot(450, capture_dark)

    def capture_dark() -> None:
        window.navigate("settings")
        capture("settings-dark.png")
        QTimer.singleShot(200, lambda: finish(0))

    def start_phase_1() -> None:
        if host_controller._started and guest_controller._started:
            begin_pairing_shot()
        else:
            QTimer.singleShot(100, start_phase_1)

    QTimer.singleShot(1000, start_phase_1)
    QTimer.singleShot(40000, lambda: finish(2))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
