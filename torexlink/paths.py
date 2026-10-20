"""Filesystem locations shared by the core, platform services and UI."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .constants import APP_BUNDLE_ID, APP_NAME


def _default_data_root() -> Path:
    override = os.environ.get("TOREXLINK_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    # Linux is not a supported release target, but keeping this makes local
    # development and headless tests predictable.
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "torexlink"


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved, private storage paths for one application instance."""

    root: Path
    settings_file: Path
    peers_file: Path
    certificate_file: Path
    private_key_file: Path
    logs_dir: Path
    run_dir: Path

    @classmethod
    def discover(cls, root: Path | None = None) -> AppPaths:
        base = (root or _default_data_root()).expanduser().resolve()
        run_root = base
        # macOS runtime socket paths must stay below the 104-byte sun_path limit;
        # a short /tmp directory is used for the single-instance socket only.
        if sys.platform == "darwin" and len(str(base / "run")) > 92:
            import hashlib

            digest = hashlib.sha256(str(base).encode()).hexdigest()[:12]
            run_root = Path("/tmp") / f"torexlink-{digest}"
        return cls(
            root=base,
            settings_file=base / "settings.json",
            peers_file=base / "peers.json",
            certificate_file=base / "certs" / "device.pem",
            private_key_file=base / "certs" / "device-key.pem",
            logs_dir=base / "logs",
            run_dir=run_root / "run",
        )

    def ensure(self) -> None:
        """Create private directories with restrictive permissions."""
        for directory in (
            self.root,
            self.certificate_file.parent,
            self.logs_dir,
            self.run_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "torexlink.log"


def resource_path(*parts: str) -> Path:
    """Return a bundled resource, supporting PyInstaller one-file builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        # PyInstaller stores package data at ``_MEIPASS/torexlink/resources``.
        candidate = base.joinpath("torexlink", "resources", *parts)
        if candidate.exists():
            return candidate
        return base.joinpath(*parts)
    return Path(__file__).resolve().parent / "resources" / Path(*parts)


def executable_command() -> list[str]:
    """Command used by autostart and process relaunch."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--background"]
    return [str(Path(sys.executable).resolve()), "-m", "torexlink", "--background"]


__all__ = ["APP_BUNDLE_ID", "AppPaths", "executable_command", "resource_path"]
