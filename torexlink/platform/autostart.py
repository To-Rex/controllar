"""Launch-at-login integration using LaunchAgent (macOS) or Run key (Windows)."""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from ..constants import APP_BUNDLE_ID
from ..paths import executable_command

log = logging.getLogger(__name__)

_LABEL = APP_BUNDLE_ID
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "ToRexLink"


def _bundle_path() -> Path | None:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
    return None


class AutostartManager:
    @property
    def supported(self) -> bool:
        return sys.platform in {"darwin", "win32"}

    @property
    def _launch_agent_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"

    def is_enabled(self) -> bool:
        if sys.platform == "darwin":
            return self._launch_agent_path.exists()
        if sys.platform == "win32":
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                    value, _ = winreg.QueryValueEx(key, _RUN_VALUE)
                    return bool(value)
            except FileNotFoundError:
                return False
            except OSError:
                return False
        return False

    def set_enabled(self, enabled: bool) -> None:
        if not self.supported:
            raise RuntimeError("launch-at-login is supported on macOS and Windows only")
        if sys.platform == "darwin":
            self._set_macos(enabled)
        else:
            self._set_windows(enabled)

    def _set_macos(self, enabled: bool) -> None:
        path = self._launch_agent_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        uid = os.getuid()
        if not enabled:
            path.unlink(missing_ok=True)
            # Disable without terminating a currently running instance. This
            # prevents launchd from relaunching at the next login.
            self._run_launchctl(["disable", f"gui/{uid}/{_LABEL}"])
            return
        bundle = _bundle_path()
        if bundle is not None:
            args = ["/usr/bin/open", "-a", str(bundle), "--args", "--background"]
        else:
            args = executable_command()
        log_dir = Path.home() / "Library" / "Logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        payload = {
            "Label": _LABEL,
            "ProgramArguments": args,
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Interactive",
            "LimitLoadToSessionType": "Aqua",
            "StandardOutPath": str(log_dir / "ToRexLink.log"),
            "StandardErrorPath": str(log_dir / "ToRexLink.log"),
        }
        if bundle is None:
            project_root = str(Path(__file__).resolve().parents[2])
            payload["WorkingDirectory"] = project_root
        data = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
        path.write_bytes(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._run_launchctl(["enable", f"gui/{uid}/{_LABEL}"])

    def _run_launchctl(self, arguments: list[str]) -> None:
        try:
            result = subprocess.run(
                ["/bin/launchctl", *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode not in (0, 1, 113):
                log.debug("launchctl %s exited %s: %s", arguments, result.returncode, result.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("Could not update launchd login item: %s", exc)

    def _set_windows(self, enabled: bool) -> None:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                command = self._windows_command()
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                except FileNotFoundError:
                    pass

    def _windows_command(self) -> str:
        if getattr(sys, "frozen", False):
            return subprocess.list2cmdline(executable_command())
        # A source checkout is not necessarily installed into site-packages;
        # embed the project root so login startup works regardless of cwd.
        interpreter = Path(sys.executable)
        pythonw = interpreter.with_name("pythonw.exe")
        if pythonw.exists():
            interpreter = pythonw
        project_root = str(Path(__file__).resolve().parents[2])
        script = (
            "import sys; "
            f"sys.path.insert(0, {project_root!r}); "
            "from torexlink.app import main; "
            "raise SystemExit(main(['--background']))"
        )
        return subprocess.list2cmdline([str(interpreter), "-c", script])

    def command_description(self) -> str:
        if sys.platform == "darwin":
            return str(self._launch_agent_path)
        if sys.platform == "win32":
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                    return str(winreg.QueryValueEx(key, _RUN_VALUE)[0])
            except OSError:
                return shlex.join(executable_command())
        return ""


__all__ = ["AutostartManager"]
