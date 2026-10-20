"""Human-readable diagnostics with no secrets."""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..constants import APP_VERSION, TLS_MIN_VERSION
from ..security.identity import Identity
from .firewall import FirewallStatus
from .permissions import PermissionInfo


@dataclass(slots=True)
class DiagnosticItem:
    key: str
    label: str
    value: str
    ok: bool | None = None
    hint: str = ""


@dataclass(slots=True)
class DiagnosticReport:
    items: list[DiagnosticItem] = field(default_factory=list)
    generated_at: str = ""

    def add(
        self,
        key: str,
        label: str,
        value: str,
        *,
        ok: bool | None = None,
        hint: str = "",
    ) -> None:
        self.items.append(
            DiagnosticItem(key=key, label=label, value=value, ok=ok, hint=hint)
        )

    def to_text(self) -> str:
        lines = [
            "ToRexLink — diagnostika hisoboti",
            f"Yaratildi: {self.generated_at}",
            "",
        ]
        for item in self.items:
            symbol = (
                "[OK]" if item.ok is True else "[!]" if item.ok is False else "[i]"
            )
            lines.append(f"{symbol} {item.label}: {item.value}")
            if item.hint:
                lines.append(f"    → {item.hint}")
        lines.append("")
        lines.append("Maxfiy tokenlar, juftlash kodlari va clipboard mazmuni kiritilmagan.")
        return "\n".join(lines)


def build_report(
    *,
    paths_root: Path,
    identity: Identity | None,
    permissions: PermissionInfo | None,
    firewall: FirewallStatus | None,
    listener_port: int | None,
    paired_count: int,
    mdns_running: bool,
    connected: bool,
    last_error: str,
    extra: dict[str, Any] | None = None,
) -> DiagnosticReport:
    report = DiagnosticReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds")
    )
    report.add("app", "ToRexLink versiyasi", APP_VERSION)
    report.add(
        "os",
        "Operatsion tizim",
        f"{platform.system()} {platform.release()} ({platform.machine()})",
    )
    report.add("python", "Python", platform.python_version())
    report.add("data", "Ma’lumot papkasi", str(paths_root))
    if identity is not None:
        report.add(
            "certificate",
            "Qurilma sertifikati",
            identity.device_id[:16] + "…",
            ok=True,
            hint=identity.fingerprint,
        )
        report.add(
            "certificate_expiry",
            "Sertifikat amal qiladi",
            identity.not_after.astimezone().strftime("%Y-%m-%d"),
            ok=identity.not_after > datetime.now(UTC),
        )
    else:
        report.add("certificate", "Qurilma sertifikati", "mavjud emas", ok=False)
    if listener_port is not None:
        report.add("listener", "TLS tinglovchisi", f"TCP {listener_port}", ok=True)
    else:
        report.add(
            "listener",
            "TLS tinglovchisi",
            "o‘chirilgan",
            ok=None,
            hint="Sozlamalarda hostingni yoqing",
        )
    report.add(
        "tls",
        "Minimal TLS",
        TLS_MIN_VERSION,
        ok=True,
        hint="Sertifikat fingerprinti pinlanadi",
    )
    if permissions is None:
        report.add("permissions", "Kirish huquqlari", "tekshirilmagan", ok=None)
    else:
        report.add(
            "permissions_capture",
            "Klaviatura/sichqoncha kuzatuvi",
            "ruxsat berilgan" if permissions.capture else "ruxsat yo‘q",
            ok=permissions.capture,
            hint=permissions.capture_detail,
        )
        report.add(
            "permissions_injection",
            "Boshqaruv injectsi",
            "ruxsat berilgan" if permissions.injection else "ruxsat yo‘q",
            ok=permissions.injection,
            hint=permissions.injection_detail,
        )
    if firewall is not None:
        report.add(
            "firewall",
            "Firewall",
            firewall.detail,
            ok=firewall.configured if firewall.supported else None,
        )
    report.add(
        "mdns",
        "mDNS",
        "faol" if mdns_running else "o‘chirilgan / mavjud emas",
        ok=mdns_running,
    )
    report.add(
        "paired",
        "Juftlangan qurilmalar",
        str(paired_count),
        ok=paired_count > 0,
    )
    report.add(
        "connection",
        "Ulanish",
        "faol" if connected else "yo‘q",
        ok=connected if connected else None,
    )
    report.add("last_error", "Oxirgi xatolik", last_error or "yo‘q", ok=not last_error)
    if extra:
        for key, value in extra.items():
            report.add(key, key, str(value))
    return report


__all__ = ["DiagnosticItem", "DiagnosticReport", "build_report"]
