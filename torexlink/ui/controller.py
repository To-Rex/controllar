"""Qt/async boundary. The core lives on a dedicated asyncio thread."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ..core.service import ServiceError, ToRexLinkService
from ..core.state import ServiceEvent, ServiceSnapshot
from ..i18n import Translator
from ..pairing_payload import PairingPayload
from ..platform.autostart import AutostartManager
from ..platform.firewall import FirewallManager, FirewallStatus
from ..platform.permissions import PermissionInfo, PermissionManager
from ..settings import Settings

log = logging.getLogger(__name__)


class AsyncRuntime(QThread):
    """Runs the non-Qt service in one asyncio loop, forever."""

    service_event = Signal(object)
    ready = Signal()
    startup_failed = Signal(str)

    def __init__(self, service: ToRexLinkService) -> None:
        super().__init__()
        self.service = service
        self.loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._startup_error: str = ""
        self._stopping = False

    def run(self) -> None:  # pragma: no cover - thread lifecycle is exercised via app
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            loop.run_until_complete(self._startup())
            if self._startup_error:
                self.startup_failed.emit(self._startup_error)
                return
            self._loop_ready.set()
            self.ready.emit()
            loop.run_forever()
        finally:
            self._loop_ready.set()
            with contextlib.suppress(Exception):
                tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self.loop = None

    async def _startup(self) -> None:
        try:
            await self.service.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("Service startup failed")
            self._startup_error = str(exc)

    def wait_until_ready(self, timeout: float = 20.0) -> bool:
        return self._loop_ready.wait(timeout)

    def submit(self, coro) -> concurrent.futures.Future:
        loop = self.loop
        if loop is None or loop.is_closed():
            if hasattr(coro, "close"):
                coro.close()
            raise RuntimeError("async runtime is not running")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        loop = self.loop
        if loop is not None and not loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(self.service.stop(), loop)
                with contextlib.suppress(Exception):
                    future.result(timeout=8.0)
            except Exception:  # noqa: BLE001
                log.debug("Service shutdown failed", exc_info=True)
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(loop.stop)
        if self.isRunning():
            self.wait(5000)


class ToRexLinkController(QObject):
    service_event = Signal(object)
    ready = Signal()
    startup_failed = Signal(str)
    operation_failed = Signal(str, str)
    language_changed = Signal(str)
    theme_changed = Signal(str)
    permissions_changed = Signal(object)
    firewall_changed = Signal(object)
    _call_finished = Signal(str, object, object)

    def __init__(self, service: ToRexLinkService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.translator = Translator(service.settings.language)
        self.runtime = AsyncRuntime(service)
        self.runtime.service_event.connect(self._on_service_event)
        self.runtime.ready.connect(self._on_runtime_ready)
        self.runtime.startup_failed.connect(self._on_startup_failed)
        self._call_finished.connect(self._handle_call_finished)
        self._calls: dict[str, tuple[Callable | None, Callable | None]] = {}
        self._pending_calls: list[tuple[str, object]] = []
        self._snapshot: ServiceSnapshot = service.snapshot()
        self._started = False
        self._shutting_down = False
        self._permissions = PermissionManager(service.input_backend_factory)
        self.autostart = AutostartManager()
        self.firewall = FirewallManager()
        self._last_event: ServiceEvent | None = None
        self._call_counter = 0

        service.set_event_callback(self._emit_service_event)

    # ------------------------------------------------------------------ signals
    def _emit_service_event(self, event: ServiceEvent) -> None:
        # This runs on the asyncio thread. Emitting a Qt signal is thread-safe
        # and the slot is delivered on the QObject's (main) thread.
        self.runtime.service_event.emit(event)

    @Slot(object)
    def _on_service_event(self, event: ServiceEvent) -> None:
        self._snapshot = event.snapshot
        self._last_event = event
        self.service_event.emit(event)

    @Slot()
    def _on_runtime_ready(self) -> None:
        self._started = True
        pending = self._pending_calls
        self._pending_calls = []
        for token, coro in pending:
            self._submit_coro_token(token, coro)
        self.ready.emit()

    @Slot(str)
    def _on_startup_failed(self, message: str) -> None:
        for token, coro in self._pending_calls:
            if hasattr(coro, "close"):
                coro.close()
            callbacks = self._calls.pop(token, None)
            if callbacks is not None and callbacks[1] is not None:
                with contextlib.suppress(Exception):
                    callbacks[1](ServiceError("not_running", message))
        self._pending_calls = []
        self.startup_failed.emit(message)

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.runtime.isRunning():
            return
        self.runtime.start()

    @property
    def snapshot(self) -> ServiceSnapshot:
        return self._snapshot

    @property
    def last_event(self) -> ServiceEvent | None:
        return self._last_event

    @property
    def settings(self) -> Settings:
        return self.service.settings

    @property
    def startup_error(self) -> str:
        return ""

    @property
    def permissions(self) -> PermissionInfo:
        return self._permissions.status()

    def refresh_permissions(self) -> PermissionInfo:
        info = self._permissions.status(force=True)
        self.permissions_changed.emit(info)
        return info

    def request_permissions(self) -> PermissionInfo:
        info = self._permissions.request()
        self.permissions_changed.emit(info)
        return info

    def open_permission_settings(self, area: str = "all") -> None:
        self._permissions.open_settings(area)

    def firewall_status(self) -> FirewallStatus:
        return self.firewall.status()

    def configure_firewall(self) -> None:
        self.run_coro(self._configure_firewall_async(), silent=True)

    async def _configure_firewall_async(self) -> FirewallStatus:
        launched = await asyncio.to_thread(self.firewall.request_rules)
        if launched:
            status = await asyncio.to_thread(self.firewall.wait_for_rules)
        else:
            status = await asyncio.to_thread(self.firewall.status)
        self.firewall_changed.emit(status)
        return status

    def start_step(self) -> None:
        self.start()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.runtime.shutdown()

    # ------------------------------------------------------------ async bridge
    def run_coro(
        self,
        coro,
        *,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        silent: bool = False,
    ) -> str | None:
        if self._shutting_down:
            if hasattr(coro, "close"):
                coro.close()
            error = ServiceError("not_running", "application is shutting down")
            if on_error:
                on_error(error)
            elif not silent:
                self.operation_failed.emit(error.code, str(error))
            return None
        self._call_counter += 1
        token = f"c{self._call_counter}"
        self._calls[token] = (
            on_success,
            on_error if on_error is not None else (None if silent else self._default_error_handler),
        )
        if not self._started:
            # Startup takes a moment (identity + TLS listener). Queue UI actions
            # rather than failing them or dropping onboarding completion.
            if len(self._pending_calls) >= 32:
                if hasattr(coro, "close"):
                    coro.close()
                self._calls.pop(token, None)
                self.operation_failed.emit("not_running", "too many pending operations")
                return None
            self._pending_calls.append((token, coro))
            return token
        self._submit_coro_token(token, coro)
        return token

    def _submit_coro_token(self, token: str, coro) -> None:
        try:
            future = self.runtime.submit(coro)
        except RuntimeError as exc:
            callbacks = self._calls.pop(token, None)
            if hasattr(coro, "close"):
                coro.close()
            error = ServiceError("not_running", str(exc))
            if callbacks is not None and callbacks[1] is not None:
                callbacks[1](error)
            return

        def done(completed: concurrent.futures.Future) -> None:
            try:
                result = completed.result()
                error: BaseException | None = None
            except BaseException as exc:  # noqa: BLE001
                result = None
                error = exc
            try:
                self._call_finished.emit(token, result, error)
            except RuntimeError:
                pass

        future.add_done_callback(done)

    @Slot(str, object, object)
    def _handle_call_finished(self, token: str, result: object, error: object) -> None:
        callbacks = self._calls.pop(token, None)
        if callbacks is None:
            return
        on_success, on_error = callbacks
        if error is not None:
            if on_error is not None:
                with contextlib.suppress(Exception):
                    on_error(error)  # type: ignore[arg-type]
            return
        if on_success is not None:
            with contextlib.suppress(Exception):
                on_success(result)

    def _default_error_handler(self, error: BaseException) -> None:
        code = error.code if isinstance(error, ServiceError) else "generic"
        detail = str(error)
        self.operation_failed.emit(code, detail)

    # ------------------------------------------------------------- pairing/API
    def begin_pairing(self, on_success=None, on_error=None) -> None:
        self.run_coro(self.service.begin_pairing(), on_success=on_success, on_error=on_error)

    def cancel_pairing(self) -> None:
        self.run_coro(self.service.cancel_pairing(), silent=True)

    def pair_with_payload(
        self,
        payload: PairingPayload,
        on_success=None,
        on_error=None,
    ) -> None:
        self.run_coro(
            self.service.pair_with_payload(payload),
            on_success=on_success,
            on_error=on_error,
        )

    def pair_with_code(
        self,
        host: str,
        port: int,
        code: str,
        *,
        expected_fingerprint: str | None = None,
        expected_device_id: str | None = None,
        on_success=None,
        on_error=None,
    ) -> None:
        self.run_coro(
            self.service.pair_with_code(
                host,
                port,
                code,
                expected_fingerprint=expected_fingerprint,
                expected_device_id=expected_device_id,
            ),
            on_success=on_success,
            on_error=on_error,
        )

    def connect_peer(self, peer_id: str, on_success=None, on_error=None) -> None:
        self.run_coro(self.service.connect_peer(peer_id), on_success=on_success, on_error=on_error)

    def disconnect(self, on_success=None) -> None:
        self.run_coro(self.service.disconnect(), on_success=on_success)

    def remove_peer(self, peer_id: str, on_success=None, on_error=None) -> None:
        self.run_coro(self.service.remove_peer(peer_id), on_success=on_success, on_error=on_error)

    def clear_peers(self, on_success=None, on_error=None) -> None:
        self.run_coro(self.service.clear_peers(), on_success=on_success, on_error=on_error)

    def fetch_remote_logs(self, on_success=None, on_error=None) -> None:
        self.run_coro(
            self.service.request_remote_logs(),
            on_success=on_success,
            on_error=on_error,
        )

    # ------------------------------------------------------------------ settings
    def update_settings(
        self,
        changes: dict[str, Any],
        *,
        on_success: Callable[[Settings], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if "launch_at_login" in changes:
            self._update_autostart_and_settings(changes, on_success, on_error)
            return
        self.run_coro(
            self.service.update_settings(changes),
            on_success=on_success,
            on_error=on_error,
        )

    def _update_autostart_and_settings(
        self,
        changes: dict[str, Any],
        on_success: Callable[[Settings], None] | None,
        on_error: Callable[[BaseException], None] | None,
    ) -> None:
        async def apply() -> Settings:
            enabled = bool(changes["launch_at_login"])
            old = self.service.settings.launch_at_login
            await asyncio.to_thread(self.autostart.set_enabled, enabled)
            try:
                return await self.service.update_settings(changes)
            except Exception:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.autostart.set_enabled, old)
                raise

        self.run_coro(apply(), on_success=on_success, on_error=on_error)

    def set_language(self, locale: str) -> None:
        if locale == self.translator.locale:
            return
        self.translator.set_locale(locale)
        self.language_changed.emit(locale)
        self.update_settings({"language": locale}, on_error=self._background_error)

    def set_theme(self, theme: str) -> None:
        self.theme_changed.emit(theme)
        self.update_settings({"theme": theme}, on_error=self._background_error)

    def _background_error(self, error: BaseException) -> None:
        code = error.code if isinstance(error, ServiceError) else "generic"
        self.operation_failed.emit(code, str(error))

    # ------------------------------------------------------------------- errors
    def error_key(self, error: object) -> str:
        code = getattr(error, "code", None)
        if isinstance(code, str):
            candidate = f"err_{code}"
            if self.translator.t(candidate) != candidate:
                return candidate
        return "generic_error"

    def error_message(self, error: object) -> str:
        code = getattr(error, "code", None)
        if isinstance(code, str):
            key = f"err_{code}"
            translated = self.translator.t(key)
            if translated != key:
                return translated
            detail = getattr(error, "detail", str(error))
            if detail:
                return str(detail)
        return self.translator.t("generic_error")

    # ----------------------------------------------------------------- utilities
    def open_path(self, path: Path) -> bool:
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def copy_text(self, text: str) -> bool:
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        return True

    def local_interval_ms(self) -> int:
        return int((time.monotonic() * 1000) % 1000)

    def set_device_name(self, name: str, on_success=None, on_error=None) -> None:
        self.update_settings({"device_name": name}, on_success=on_success, on_error=on_error)
