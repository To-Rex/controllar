"""A small UI-facing facade over native permission checks and prompts."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from ..input.base import InputBackend, InputBackendError, InputPermissions

log = logging.getLogger(__name__)

_MAC_INPUT_MONITORING = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
)
_MAC_ACCESSIBILITY = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)


@dataclass(frozen=True, slots=True)
class PermissionInfo:
    supported: bool
    capture: bool
    injection: bool
    capture_detail: str
    injection_detail: str

    @property
    def ready(self) -> bool:
        return self.supported and self.capture and self.injection


class PermissionManager:
    def __init__(self, backend_factory: Callable[[], InputBackend]) -> None:
        self._backend_factory = backend_factory
        self._cached_backend: InputBackend | None = None

    def _backend(self) -> InputBackend | None:
        if self._cached_backend is None:
            try:
                self._cached_backend = self._backend_factory()
            except (InputBackendError, ImportError) as exc:
                log.info("Input backend is unavailable: %s", exc)
                return None
        return self._cached_backend

    def status(self) -> PermissionInfo:
        backend = self._backend()
        if backend is None:
            return PermissionInfo(
                supported=False,
                capture=False,
                injection=False,
                capture_detail="Native input support is unavailable on this system",
                injection_detail="Native input support is unavailable on this system",
            )
        try:
            permissions: InputPermissions = backend.preflight()
        except Exception:  # noqa: BLE001
            log.debug("Permission preflight failed", exc_info=True)
            return PermissionInfo(
                supported=False,
                capture=False,
                injection=False,
                capture_detail="Permission check failed",
                injection_detail="Permission check failed",
            )
        return PermissionInfo(
            supported=True,
            capture=permissions.capture,
            injection=permissions.injection,
            capture_detail=permissions.capture_detail,
            injection_detail=permissions.injection_detail,
        )

    def request(self) -> PermissionInfo:
        backend = self._backend()
        if backend is not None:
            try:
                backend.request_permissions()
            except Exception:  # noqa: BLE001
                log.debug("Permission request failed", exc_info=True)
        return self.status()

    def open_settings(self, area: str = "all") -> None:
        if sys.platform != "darwin":
            return
        urls: list[str] = []
        if area in {"all", "capture"}:
            urls.append(_MAC_INPUT_MONITORING)
        if area in {"all", "injection"}:
            urls.append(_MAC_ACCESSIBILITY)
        for url in urls:
            try:
                subprocess.Popen(
                    ["/usr/bin/open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                log.debug("Could not open permission settings", exc_info=True)

    def reset_cache(self) -> None:
        self._cached_backend = None


__all__ = ["PermissionInfo", "PermissionManager"]
