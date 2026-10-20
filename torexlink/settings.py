"""Validated, atomically persisted user preferences."""

from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from .constants import DEFAULT_LOCALE, EDGES, SETTINGS_VERSION, SUPPORTED_LOCALES
from .util import atomic_write_json, read_json, valid_locale

log = logging.getLogger(__name__)

_THEMES = frozenset({"system", "light", "dark"})


@dataclass(slots=True)
class Settings:
    version: int = SETTINGS_VERSION
    language: str = DEFAULT_LOCALE
    theme: str = "system"
    device_name: str = ""
    hosting_enabled: bool = True
    background_mode: bool = True
    launch_at_login: bool = False
    auto_reconnect: bool = True
    clipboard_sync: bool = True
    edge_switch: bool = True
    edge: str = "right"
    hotkey_switch: bool = True
    notifications: bool = True
    onboarding_complete: bool = False
    preferred_peer_id: str | None = None
    last_peer_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validated_changes(self, changes: dict[str, Any]) -> Settings:
        """Return an updated copy, validating each externally supplied value."""
        known = {field.name for field in fields(self)}
        sanitized: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in known or key in {"version"}:
                continue
            if key == "language":
                value = valid_locale(value)
            elif key == "device_name":
                if not isinstance(value, str):
                    raise ValueError("invalid device name")
                value = " ".join(value.replace("\x00", " ").split())[:64]
            elif key == "theme":
                if value not in _THEMES:
                    raise ValueError("invalid theme")
            elif key == "edge":
                if value not in EDGES:
                    raise ValueError("invalid edge")
            elif key in {
                "hosting_enabled",
                "background_mode",
                "launch_at_login",
                "auto_reconnect",
                "clipboard_sync",
                "edge_switch",
                "hotkey_switch",
                "notifications",
                "onboarding_complete",
            }:
                value = bool(value)
            elif key in {"preferred_peer_id", "last_peer_id"}:
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"invalid {key}")
                value = value or None
            else:
                continue
            sanitized[key] = value
        return replace(self, **sanitized)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Settings:
        settings = cls()
        """Unknown or invalid fields fall back safely instead of crashing startup."""
        known = {field.name for field in fields(cls)}
        clean: dict[str, Any] = {}
        for key in known:
            if key not in raw:
                continue
            value = raw[key]
            if key == "version":
                continue
            if key == "language":
                value = value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE
            elif key == "device_name":
                value = (
                    " ".join(str(value).replace("\x00", " ").split())[:64]
                    if isinstance(value, str)
                    else ""
                )
            elif key == "theme":
                value = value if value in _THEMES else "system"
            elif key == "edge":
                value = value if value in EDGES else "right"
            elif key in {
                "hosting_enabled",
                "background_mode",
                "launch_at_login",
                "auto_reconnect",
                "clipboard_sync",
                "edge_switch",
                "hotkey_switch",
                "notifications",
                "onboarding_complete",
            }:
                value = bool(value)
            elif key in {"preferred_peer_id", "last_peer_id"}:
                value = value if isinstance(value, str) and value else None
            clean[key] = value
        settings = replace(settings, **clean)
        settings.version = SETTINGS_VERSION
        return settings


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._settings: Settings | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = self.load()
        return self._settings

    def load(self) -> Settings:
        if not self.path.exists():
            self._settings = Settings()
            return self._settings
        try:
            raw = read_json(self.path)
            if not isinstance(raw, dict):
                raise ValueError("settings root is not an object")
            file_version = raw.get("version", 0)
            if isinstance(file_version, int) and file_version > SETTINGS_VERSION:
                log.warning("Future settings version %s; keeping compatible fields", file_version)
            self._settings = Settings.from_dict(raw)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("Settings file is unreadable; a backup was preserved: %s", exc)
            self._backup_corrupt()
            self._settings = Settings()
        return self._settings

    def update(self, changes: dict[str, Any]) -> Settings:
        updated = self.settings.validated_changes(changes)
        self.save(updated)
        return updated

    def save(self, settings: Settings | None = None) -> None:
        if settings is not None:
            self._settings = settings
        elif self._settings is None:
            self._settings = self.load()
        atomic_write_json(self.path, self._settings.as_dict(), private=True)

    def _backup_corrupt(self) -> None:
        try:
            import time

            backup = self.path.with_suffix(f".corrupt-{int(time.time())}.json")
            shutil.copy2(self.path, backup)
        except OSError:
            pass


__all__ = ["Settings", "SettingsStore"]
