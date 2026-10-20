"""Windows Firewall configuration with explicit UAC consent.

Only the ToRexLink port range and DNS-SD/mDNS UDP port are opened, on private
and domain profiles. No firewall changes happen without the user pressing the
consent button; UAC itself is a second consent gate.
"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys
import time
from dataclasses import dataclass

from ..constants import DEFAULT_PORT, PORT_SCAN_COUNT

log = logging.getLogger(__name__)

TCP_RULE = "ToRexLink Inbound TCP"
UDP_RULE = "ToRexLink mDNS UDP"
_CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True, slots=True)
class FirewallStatus:
    supported: bool
    configured: bool
    detail: str


class FirewallManager:
    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def _run_netsh(self, arguments: list[str], *, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["netsh", "advfirewall", "firewall", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

    def _rule_enabled(self, name: str) -> bool:
        if not self.supported:
            return False
        try:
            result = self._run_netsh(["show", "rule", f"name={name}"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("Firewall rule check failed: %s", exc)
            return False
        if result.returncode != 0:
            return False
        lowered = result.stdout.lower()
        return name.lower() in lowered

    def status(self) -> FirewallStatus:
        if not self.supported:
            detail = (
                "macOS Application Firewall is managed by the OS; allow ToRexLink "
                "when prompted."
                if sys.platform == "darwin"
                else "Automatic firewall configuration is supported on Windows only"
            )
            return FirewallStatus(supported=False, configured=False, detail=detail)
        tcp = self._rule_enabled(TCP_RULE)
        udp = self._rule_enabled(UDP_RULE)
        if tcp and udp:
            return FirewallStatus(True, True, "ToRexLink inbound rules are active")
        missing = ", ".join(
            name for name, enabled in ((TCP_RULE, tcp), (UDP_RULE, udp)) if not enabled
        )
        return FirewallStatus(True, False, f"Missing firewall rules: {missing}")

    def build_elevated_script(self) -> str:
        port_high = DEFAULT_PORT + PORT_SCAN_COUNT - 1
        lines = [
            "$ErrorActionPreference = 'Stop'",
            f"netsh advfirewall firewall delete rule name=\"{TCP_RULE}\" | Out-Null",
            f"netsh advfirewall firewall delete rule name=\"{UDP_RULE}\" | Out-Null",
            (
                "netsh advfirewall firewall add rule "
                f'name="{TCP_RULE}" dir=in action=allow protocol=TCP '
                f"localport={DEFAULT_PORT}-{port_high} profile=private,domain"
            ),
            (
                "netsh advfirewall firewall add rule "
                f'name="{UDP_RULE}" dir=in action=allow protocol=UDP '
                "localport=5353 profile=private,domain"
            ),
        ]
        return "\n".join(lines)

    def request_rules(self) -> bool:
        """Launch an elevated PowerShell. The UAC prompt is the consent gate."""
        if not self.supported:
            return False
        script = self.build_elevated_script()
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        parameters = (
            "-NoProfile -NonInteractive -WindowStyle Hidden "
            f"-ExecutionPolicy Bypass -EncodedCommand {encoded}"
        )
        try:
            import ctypes

            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                "powershell.exe",
                parameters,
                None,
                0,
            )
        except Exception:  # noqa: BLE001
            log.exception("Could not launch the elevated firewall helper")
            return False
        if int(result) <= 32:
            log.info("Firewall elevation was cancelled or failed (code %s)", result)
            return False
        log.info("Elevated firewall configuration requested")
        return True

    def wait_for_rules(self, timeout: float = 25.0) -> FirewallStatus:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.status()
            if status.configured:
                return status
            time.sleep(0.7)
        return self.status()


__all__ = ["FirewallManager", "FirewallStatus", "TCP_RULE", "UDP_RULE"]
