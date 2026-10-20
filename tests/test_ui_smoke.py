from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from torexlink.core.service import ToRexLinkService  # noqa: E402
from torexlink.paths import AppPaths  # noqa: E402
from torexlink.settings import SettingsStore  # noqa: E402
from torexlink.ui.controller import ToRexLinkController  # noqa: E402
from torexlink.ui.dialogs import JoinDialog, decode_qr_file  # noqa: E402
from torexlink.ui.main_window import MainWindow  # noqa: E402
from torexlink.ui.onboarding import OnboardingDialog  # noqa: E402
from torexlink.ui.theme import build_stylesheet  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


def test_pages_language_theme_and_dialogs(qapp, tmp_path, monkeypatch) -> None:
    paths = AppPaths.discover(tmp_path / "ui-data")
    paths.ensure()
    store = SettingsStore(paths.settings_file)
    store.save()
    store.update(
        {
            "onboarding_complete": True,
            "hosting_enabled": False,
            "auto_reconnect": False,
            "language": "uz",
        }
    )
    service = ToRexLinkService(
        paths,
        settings_store=store,
        enable_discovery=False,
        preferred_port=0,
        address_provider=lambda: ("192.168.1.5",),
    )
    controller = ToRexLinkController(service)
    window = MainWindow(controller)
    try:
        window.show()
        qapp.processEvents()
        for key in ("home", "connect", "devices", "settings", "logs", "diagnostics"):
            window.navigate(key)
            qapp.processEvents()
            assert window.stack.currentWidget() is window.pages[key]
            assert window.grab().width() > 0
        qapp.setStyleSheet(build_stylesheet("dark"))
        assert "#0A0D16" in qapp.styleSheet()
        controller.translator.set_locale("ru")
        window._retranslate()
        assert window.nav_buttons["home"].text() == "Главная"

        join = JoinDialog(controller, manual=True, parent=window)
        join.show()
        qapp.processEvents()
        assert join.code_input.maxLength() == 6
        assert join.windowTitle()
        # Regression: a bad QPen argument in Spinner.paintEvent crashed the
        # packaged app exactly while this pairing spinner was visible.
        join._set_busy(True)
        qapp.processEvents()
        join.grab()
        assert join.progress.isVisible()
        join._busy = False
        join.close()
        join.deleteLater()

        onboarding = OnboardingDialog(controller)
        onboarding.show()
        qapp.processEvents()
        assert onboarding.stack.count() == 3
        onboarding._set_step(2)
        qapp.processEvents()
        assert onboarding.stack.currentIndex() == 2
        onboarding.close()
        onboarding.deleteLater()
    finally:
        window.hide()
        window.deleteLater()
        qapp.processEvents()

    # The same QR implementation is used for camera frames and imported files.
    import qrcode

    path = tmp_path / "pairing.png"
    image = qrcode.make("torexlink://pair?d=invalid")
    image.save(path)
    assert decode_qr_file(str(path)) == "torexlink://pair?d=invalid"
