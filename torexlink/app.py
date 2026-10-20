"""Desktop entry point and single-instance bootstrap."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import sys

from .constants import APP_BUNDLE_ID, APP_NAME, APP_VERSION
from .core.service import ToRexLinkService
from .logging_setup import configure_logging, install_exception_logging
from .paths import AppPaths
from .settings import SettingsStore
from .ui.controller import ToRexLinkController
from .ui.icons import logo_icon
from .ui.main_window import MainWindow
from .ui.onboarding import OnboardingDialog


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="torexlink", description="ToRexLink")
    parser.add_argument(
        "--background",
        action="store_true",
        help="start in the menu bar / system tray without showing the window",
    )
    parser.add_argument("--debug", action="store_true", help="enable verbose logging")
    parser.add_argument("--version", action="version", version=f"ToRexLink {APP_VERSION}")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _self_test() -> int:
    """Dependency smoke check used by packaging CI and support diagnostics."""
    import importlib
    import json
    import platform

    results: dict[str, object] = {
        "app": APP_VERSION,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "modules": {},
    }
    modules = ["PySide6", "cryptography", "zeroconf", "zeroconf.asyncio", "qrcode", "zxingcpp", "PIL"]
    if sys.platform == "darwin":
        modules += ["objc", "Quartz", "AppKit", "ApplicationServices"]
    failed = False
    module_results = results["modules"]
    assert isinstance(module_results, dict)
    for name in modules:
        try:
            importlib.import_module(name)
            module_results[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            module_results[name] = f"error: {exc}"
            failed = True
    if sys.platform == "darwin":
        try:
            import AppKit
            import ApplicationServices
            import Quartz

            checks = {
                "CGEventTapCreate": hasattr(Quartz, "CGEventTapCreate"),
                "CGEventPost": hasattr(Quartz, "CGEventPost"),
                "NSPasteboard": hasattr(AppKit, "NSPasteboard"),
                "AXIsProcessTrusted": hasattr(ApplicationServices, "AXIsProcessTrusted"),
            }
            results["macos_native"] = checks
            failed = failed or not all(checks.values())
        except Exception as exc:  # noqa: BLE001
            results["macos_native"] = f"error: {exc}"
            failed = True
    results["ok"] = not failed
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if not failed else 1


def _server_name(paths: AppPaths) -> str:
    digest = hashlib.sha256(str(paths.root).encode("utf-8")).hexdigest()[:16]
    return f"uz.torexlink.{digest}"


def main(argv: list[str] | None = None) -> int:
    args, _qt_unknown = _argument_parser().parse_known_args(argv)
    if args.self_test:
        return _self_test()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    paths = AppPaths.discover()
    paths.ensure()
    configure_logging(paths.log_file, debug=args.debug)
    install_exception_logging()
    log = logging.getLogger("torexlink")
    log.info("Starting ToRexLink %s (%s)", APP_VERSION, paths.root)

    if sys.platform == "darwin":
        # Qt needs this to keep the custom menu bar item consistent across
        # displays and to participate correctly in Spaces.
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setOrganizationName("ToRexLink")
    QApplication.setOrganizationDomain(APP_BUNDLE_ID)
    app = QApplication([sys.argv[0] if sys.argv else "ToRexLink"])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(logo_icon())
    app.setStyle("Fusion")

    # Single instance: reuse the existing window. The socket is not a security
    # boundary; it only prevents accidental duplicate tray icons.
    server_name = _server_name(paths)
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    if probe.waitForConnected(250):
        if not args.background:
            probe.write(b"show\n")
            probe.flush()
            probe.waitForBytesWritten(400)
        probe.disconnectFromServer()
        log.info("An existing ToRexLink instance is already running")
        return 0
    QLocalServer.removeServer(server_name)
    single_instance = QLocalServer()
    if not single_instance.listen(server_name):
        log.error("Could not create single-instance socket: %s", single_instance.errorString())
    else:
        import contextlib

        with contextlib.suppress(OSError):
            os.chmod(single_instance.fullServerName(), 0o600)

    settings_store = SettingsStore(paths.settings_file)
    service = ToRexLinkService(paths, settings_store=settings_store)
    controller = ToRexLinkController(service)
    window = MainWindow(controller)

    def handle_new_connection() -> None:
        connection = single_instance.nextPendingConnection()
        if connection is None:
            return
        connection.readyRead.connect(lambda: _handle_instance_message(connection, window))
        connection.disconnected.connect(connection.deleteLater)

    def _handle_instance_message(connection, main_window: MainWindow) -> None:
        data = bytes(connection.readAll()).strip().lower()
        if b"show" in data:
            main_window.show_and_raise()
        connection.disconnectFromServer()

    single_instance.newConnection.connect(handle_new_connection)

    def shutdown() -> None:
        if not getattr(shutdown, "_done", False):
            shutdown._done = True  # type: ignore[attr-defined]
            log.info("Shutting down ToRexLink")
            controller.shutdown()
            single_instance.close()

    app.aboutToQuit.connect(shutdown)

    # Graceful Ctrl+C when launched from a terminal.
    def on_signal(_signum, _frame) -> None:
        window.quit_application()

    import contextlib

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, on_signal)

    controller.start()

    onboarding_needed = not settings_store.settings.onboarding_complete
    if onboarding_needed:
        onboarding = OnboardingDialog(controller)
        onboarding.exec()
        # The dialog writes onboarding_complete on both accept and close; a
        # close is treated as a skip rather than trapping the user next start.
        if not args.background or window.tray is None:
            window.show()
            window.raise_()
    elif args.background and window.tray is not None:
        window.prepare_background_launch()
    else:
        window.show()
        window.raise_()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
