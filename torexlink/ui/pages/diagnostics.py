"""System checks, permissions, firewall and exportable diagnostics."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...constants import APP_VERSION
from ...core.state import ServiceSnapshot
from ...platform.firewall import FirewallStatus
from ..dialogs import ConfirmDialog
from ..icons import pixmap
from ..widgets import (
    ACCENT,
    AMBER,
    DANGER,
    MINT,
    MUTED,
    Banner,
    Card,
    Dot,
    PageHeader,
    Pill,
    SectionHeader,
    label,
    make_button,
)

if TYPE_CHECKING:
    from ..controller import ToRexLinkController


@dataclass(slots=True)
class DiagRow:
    label: str
    value: str
    status: str = "info"
    hint: str = ""


class DiagnosticsPage(QWidget):
    toast = Signal(str, str)

    def __init__(self, controller: ToRexLinkController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._snapshot: ServiceSnapshot = controller.snapshot
        self._firewall: FirewallStatus | None = None
        self._rows: list[DiagRow] = []
        self._build()
        self._wire()
        self._refresh_firewall()
        self.refresh(self._snapshot)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("PageContent")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 26, 30, 30)
        layout.setSpacing(16)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.export_button = make_button("", kind="SecondaryButton", icon_name="download")
        self.copy_button = make_button("", kind="SecondaryButton", icon_name="copy")
        self.run_button = make_button("", kind="PrimaryButton", icon_name="pulse")
        for button in (self.export_button, self.copy_button, self.run_button):
            row.addWidget(button)
        self.header = PageHeader("", "", action=actions)
        layout.addWidget(self.header)

        self.banner = Banner("", "", level="success", icon_name="check_circle")
        layout.addWidget(self.banner)

        self.permissions_header = SectionHeader("")
        layout.addWidget(self.permissions_header)
        self.permissions_card = Card(margins=(18, 14, 18, 14), spacing=10)
        self.permissions_body = QVBoxLayout()
        self.permissions_body.setSpacing(6)
        self.permissions_card.body.addLayout(self.permissions_body)
        perm_actions = QHBoxLayout()
        self.permission_state = Pill("", "muted")
        perm_actions.addWidget(self.permission_state)
        perm_actions.addStretch(1)
        self.settings_button = make_button("", kind="GhostButton", icon_name="settings")
        self.allow_button = make_button("", kind="SecondaryButton", icon_name="key")
        perm_actions.addWidget(self.settings_button)
        perm_actions.addWidget(self.allow_button)
        self.permissions_card.body.addLayout(perm_actions)
        layout.addWidget(self.permissions_card)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        self.system_card = self._group_card("diagnostics_system", "monitor")
        self.network_card = self._group_card("diagnostics_network", "network")
        columns.addWidget(self.system_card, 1)
        columns.addWidget(self.network_card, 1)
        layout.addLayout(columns)

        self.security_header = SectionHeader("")
        layout.addWidget(self.security_header)
        self.security_card = Card(margins=(18, 14, 18, 14), spacing=10)
        self.security_body = QVBoxLayout()
        self.security_body.setSpacing(6)
        self.security_card.body.addLayout(self.security_body)
        layout.addWidget(self.security_card)

        self.firewall_header = SectionHeader("")
        self.firewall_header.setVisible(sys.platform == "win32")
        layout.addWidget(self.firewall_header)
        self.firewall_card = Card(margins=(18, 14, 18, 14), spacing=10)
        self.firewall_text = label(object_name="SectionCaption", wrap=True)
        self.firewall_status = Pill("", "muted")
        self.firewall_card.body.addWidget(self.firewall_text)
        fw_row = QHBoxLayout()
        fw_row.addWidget(self.firewall_status)
        fw_row.addStretch(1)
        self.firewall_button = make_button("", kind="SecondaryButton", icon_name="shield")
        fw_row.addWidget(self.firewall_button)
        self.firewall_card.body.addLayout(fw_row)
        self.firewall_card.setVisible(sys.platform == "win32")
        layout.addWidget(self.firewall_card)

        data_row = QHBoxLayout()
        self.data_button = make_button("", kind="GhostButton", icon_name="folder")
        data_row.addWidget(self.data_button)
        data_row.addStretch(1)
        layout.addLayout(data_row)
        self.restart_hint = label(object_name="Faint", wrap=True)
        layout.addWidget(self.restart_hint)
        layout.addStretch(1)

    def _group_card(self, title_key: str, icon_name: str) -> QFrame:
        card = Card(margins=(18, 14, 18, 14), spacing=10)
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(pixmap(icon_name, ACCENT, 16))
        header_row.addWidget(icon)
        title = label(object_name="SectionTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        card.body.addLayout(header_row)
        body = QVBoxLayout()
        body.setSpacing(6)
        card.body.addLayout(body)
        card.title_label = title
        card.body_layout = body
        return card

    def _wire(self) -> None:
        self.run_button.clicked.connect(self._run_checks)
        self.copy_button.clicked.connect(self._copy_report)
        self.export_button.clicked.connect(self._export_report)
        self.allow_button.clicked.connect(self._request_permissions)
        self.settings_button.clicked.connect(lambda: self.controller.open_permission_settings("all"))
        self.data_button.clicked.connect(self._open_data)
        self.firewall_button.clicked.connect(self._configure_firewall)
        self.controller.permissions_changed.connect(lambda _info: self._render_permissions())
        self.controller.firewall_changed.connect(self._on_firewall_changed)

    # ------------------------------------------------------------------ refresh
    def _refresh_firewall(self) -> None:
        if sys.platform == "win32":
            try:
                self._firewall = self.controller.firewall_status()
            except Exception:  # noqa: BLE001
                self._firewall = FirewallStatus(True, False, "Firewall status check failed")
        else:
            self._firewall = None

    def _run_checks(self) -> None:
        self._refresh_firewall()
        self.controller.refresh_permissions()
        self.refresh(self.controller.snapshot)
        self.toast.emit(self.controller.translator.t("refresh_done"), "success")

    def retranslate(self) -> None:
        t = self.controller.translator.t
        self.header.title.setText(t("diagnostics_title"))
        self.header.subtitle.setText(t("diagnostics_subtitle"))
        self.run_button.setText(t("diagnostics_run"))
        self.copy_button.setText(t("diagnostics_copy"))
        self.export_button.setText(t("diagnostics_export"))
        self.permissions_header.title.setText(t("security_permissions"))
        self.security_header.title.setText(t("diagnostics_security"))
        self.firewall_header.title.setText(t("firewall_title"))
        self.allow_button.setText(t("permissions_open"))
        self.settings_button.setText(t("permissions_open_settings"))
        self.data_button.setText(t("diagnostics_open_data"))
        self.firewall_text.setText(t("firewall_desc"))
        self.firewall_button.setText(t("firewall_configure"))
        self.restart_hint.setText(t("diagnostics_restart_hint"))
        self.system_card.title_label.setText(t("diagnostics_checks"))
        self.network_card.title_label.setText(t("diagnostics_network"))
        self.refresh(self._snapshot)

    def refresh(self, snapshot: ServiceSnapshot) -> None:
        self._snapshot = snapshot
        t = self.controller.translator.t
        self._render_permissions()
        self._render_system(snapshot, t)
        self._render_network(snapshot, t)
        self._render_security(snapshot, t)
        error = snapshot.last_error
        if error:
            self.banner.set_content(
                title=t("diagnostics_last_error"),
                text=error,
                level="warning",
            )
        else:
            self.banner.set_content(
                title=t("diagnostics_status_ok"),
                text=t("diagnostics_no_errors"),
                level="success",
            )

    def _render_permissions(self) -> None:
        if not hasattr(self, "permissions_body"):
            return
        info = self.controller.permissions
        t = self.controller.translator.t
        self._clear(self.permissions_body)
        capture = [
            (t("diagnostic_permissions_capture"), info.capture, info.capture_detail),
            (t("diagnostic_permissions_injection"), info.injection, info.injection_detail),
        ]
        for title, ok, hint in capture:
            self.permissions_body.addWidget(self._row_widget(title, t("diagnostic_granted") if ok else t("diagnostic_missing"), "ok" if ok else "warning", hint))
        self.permission_state.set_content(
            t("permissions_granted") if info.ready else t("permissions_missing"),
            "success" if info.ready else "warning",
        )
        if self._firewall is not None:
            self._render_firewall(self._firewall)

    def _render_system(self, snapshot: ServiceSnapshot, t) -> None:
        self._clear(self.system_card.body_layout)
        self.system_card.body_layout.addWidget(
            self._row_widget(t("diagnostic_app"), f"ToRexLink {APP_VERSION}", "ok")
        )
        self.system_card.body_layout.addWidget(
            self._row_widget(
                t("diagnostic_os"),
                f"{platform.system()} {platform.release()} ({platform.machine()})",
                "ok",
            )
        )
        self.system_card.body_layout.addWidget(
            self._row_widget(t("diagnostic_python"), sys.version.split()[0], "info")
        )
        data_dir = str(self.controller.service.paths.root)
        self.system_card.body_layout.addWidget(
            self._row_widget(t("diagnostic_data"), data_dir, "info", data_dir)
        )

    def _render_network(self, snapshot: ServiceSnapshot, t) -> None:
        self._clear(self.network_card.body_layout)
        port_text = f"TCP {snapshot.listener_port}" if snapshot.listener_port else t("diagnostic_disabled")
        self.network_card.body_layout.addWidget(
            self._row_widget(t("diagnostic_listener"), port_text, "ok" if snapshot.listener_port else "warning")
        )
        self.network_card.body_layout.addWidget(
            self._row_widget(
                t("diagnostic_mdns"),
                t("diagnostic_active") if snapshot.mdns_active else t("diagnostic_inactive"),
                "ok" if snapshot.mdns_active else "info",
            )
        )
        self.network_card.body_layout.addWidget(
            self._row_widget(
                t("diagnostic_paired"),
                str(len(snapshot.peers)),
                "ok" if snapshot.peers else "info",
            )
        )
        if snapshot.connection:
            value = f"{snapshot.connection.name} · {snapshot.connection.address}"
            self.network_card.body_layout.addWidget(
                self._row_widget(t("diagnostic_connection"), value, "ok")
            )
        else:
            self.network_card.body_layout.addWidget(
                self._row_widget(t("diagnostic_connection"), t("diagnostic_inactive"), "info")
            )

    def _render_security(self, snapshot: ServiceSnapshot, t) -> None:
        self._clear(self.security_body)
        self.security_body.addWidget(
            self._row_widget(t("diagnostic_tls"), "TLS 1.3 · SHA-256 pinning", "ok")
        )
        local = snapshot.local
        if local:
            self.security_body.addWidget(
                self._row_widget(t("diagnostic_certificate"), local.fingerprint[:24] + "…", "ok", local.fingerprint)
            )
        identity = self.controller.service.identity
        if identity is not None:
            expiry = identity.not_after.astimezone().strftime("%Y-%m-%d")
            self.security_body.addWidget(
                self._row_widget(t("diagnostic_certificate_expiry"), expiry, "ok")
            )
        self.security_body.addWidget(
            self._row_widget(t("diagnostic_last_error"), snapshot.last_error or t("diagnostic_none"), "warning" if snapshot.last_error else "ok")
        )

    def _render_firewall(self, status: FirewallStatus) -> None:
        if not hasattr(self, "firewall_status"):
            return
        t = self.controller.translator.t
        if status.configured:
            self.firewall_status.set_content(t("firewall_configured"), "success")
        elif status.supported:
            self.firewall_status.set_content(t("firewall_not_configured"), "warning")
        else:
            self.firewall_status.set_content(t("firewall_not_windows"), "muted")
            self.firewall_button.setEnabled(False)
        self.firewall_card.setVisible(True)

    def _on_firewall_changed(self, status: FirewallStatus) -> None:
        self._firewall = status
        self._render_firewall(status)
        self.refresh(self.controller.snapshot)
        if status.configured:
            self.toast.emit(self.controller.translator.t("toast_firewall_configured"), "success")

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _row_widget(self, title: str, value: str, status: str = "info", hint: str = "") -> QWidget:
        widget = QFrame()
        widget.setObjectName("InnerCard")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)
        color_value = {"ok": MINT, "warning": AMBER, "error": DANGER}.get(status, MUTED)
        dot = Dot(color_value, 6)
        layout.addWidget(dot)
        label_widget = label(title, object_name="Muted")
        layout.addWidget(label_widget, 1)
        value_widget = label(value, object_name="Value")
        value_widget.setAlignment(Qt.AlignmentFlag.AlignRight)
        value_widget.setWordWrap(True)
        if hint:
            value_widget.setToolTip(hint)
        layout.addWidget(value_widget, 2)
        return widget

    def _refresh_rows(self) -> list[DiagRow]:
        return self._rows

    def _request_permissions(self) -> None:
        self.controller.request_permissions()
        self._render_permissions()
        self.refresh(self.controller.snapshot)
        self.toast.emit(self.controller.translator.t("refresh_done"), "success")

    def _configure_firewall(self) -> None:
        t = self.controller.translator.t
        if not ConfirmDialog.ask(
            self,
            title=t("firewall_consent_title"),
            text=t("firewall_consent_text"),
            confirm_text=t("continue"),
            cancel_text=t("cancel"),
            danger=False,
        ):
            return
        self.firewall_button.setEnabled(False)
        self.toast.emit(t("toast_firewall_started"), "info")
        self.controller.configure_firewall()

    def _report_text(self) -> str:
        t = self.controller.translator.t
        snapshot = self._snapshot
        lines = [
            t("diagnostics_report_title"),
            f"{t('diagnostics_generated')}: {datetime.now(UTC).isoformat(timespec='seconds')}",
            "",
            f"{t('diagnostic_app')}: ToRexLink {APP_VERSION}",
            f"{t('diagnostic_os')}: {platform.system()} {platform.release()} ({platform.machine()})",
            f"{t('diagnostic_python')}: {sys.version.split()[0]}",
            f"{t('diagnostic_data')}: {self.controller.service.paths.root}",
            f"{t('diagnostic_listener')}: {snapshot.listener_port or t('diagnostic_disabled')}",
            f"{t('diagnostic_tls')}: TLS 1.3",
            f"{t('diagnostic_mdns')}: {t('diagnostic_active') if snapshot.mdns_active else t('diagnostic_inactive')}",
            f"{t('diagnostic_paired')}: {len(snapshot.peers)}",
            f"{t('diagnostic_connection')}: {snapshot.connection.name if snapshot.connection else t('diagnostic_inactive')}",
        ]
        if snapshot.local:
            lines.append(f"{t('diagnostic_certificate')}: {snapshot.local.fingerprint}")
        info = self.controller.permissions
        lines.append(
            f"{t('diagnostic_permissions_capture')}: {t('diagnostic_granted') if info.capture else t('diagnostic_missing')}"
        )
        lines.append(
            f"{t('diagnostic_permissions_injection')}: {t('diagnostic_granted') if info.injection else t('diagnostic_missing')}"
        )
        if self._firewall is not None:
            lines.append(f"{t('diagnostic_firewall')}: {self._firewall.detail}")
        lines.append(f"{t('diagnostic_last_error')}: {snapshot.last_error or t('diagnostic_none')}")
        lines.extend(["", t("diagnostics_no_secrets")])
        return "\n".join(lines)

    def _copy_report(self) -> None:
        self.controller.copy_text(self._report_text())
        self.toast.emit(self.controller.translator.t("diagnostics_report_copied"), "success")

    def _export_report(self) -> None:
        t = self.controller.translator.t
        path, _ = QFileDialog.getSaveFileName(
            self,
            t("diagnostics_export"),
            str(Path.home() / "ToRexLink-diagnostics.txt"),
            t("report_file_filter"),
        )
        if not path:
            return
        try:
            Path(path).write_text(self._report_text(), encoding="utf-8")
            self.toast.emit(t("diagnostics_exported"), "success")
        except OSError:
            self.toast.emit(t("toast_export_failed"), "error")

    def _open_data(self) -> None:
        if not self.controller.open_path(self.controller.service.paths.root):
            self.toast.emit(self.controller.translator.t("cannot_open_path"), "error")


__all__ = ["DiagnosticsPage"]
