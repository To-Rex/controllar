"""Settings page: friendly defaults first, technical details folded away."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...constants import APP_VERSION, DEFAULT_PORT, HOTKEY_LABEL
from ...core.state import ServiceSnapshot
from ...platform.firewall import FirewallStatus
from ..dialogs import ConfirmDialog
from ..icons import pixmap
from ..widgets import (
    MINT,
    MUTED,
    Card,
    CopyField,
    FormRow,
    IconTile,
    PageHeader,
    Pill,
    SectionHeader,
    SegmentedControl,
    ToggleSwitch,
    copy_to_clipboard,
    hline,
    label,
    make_button,
)

if TYPE_CHECKING:
    from ..controller import ToRexLinkController


class SettingsPage(QWidget):
    toast = Signal(str, str)

    def __init__(self, controller: ToRexLinkController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._snapshot: ServiceSnapshot = controller.snapshot
        self._advanced_open = False
        self._firewall_status = FirewallStatus(False, False, "")
        self._build()
        self._load_values()
        self._wire()
        self._refresh_permissions()
        self._refresh_firewall()
        self.refresh(self._snapshot)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("PageContent")
        self.layout = QVBoxLayout(container)
        self.layout.setContentsMargins(30, 26, 30, 30)
        self.layout.setSpacing(18)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        self.header = PageHeader("", "")
        self.layout.addWidget(self.header)

        # General / appearance
        self.general_section = SectionHeader("")
        self.layout.addWidget(self.general_section)
        general = Card(margins=(20, 17, 20, 6), spacing=0)
        name_wrap = QWidget()
        name_layout = QVBoxLayout(name_wrap)
        name_layout.setContentsMargins(0, 0, 0, 14)
        name_layout.setSpacing(6)
        title = label(object_name="SectionTitle")
        title.setText(self.controller.translator.t("setting_device_name"))
        desc = label(object_name="SectionCaption", wrap=True)
        desc.setText(self.controller.translator.t("setting_device_name_hint"))
        name_layout.addWidget(title)
        name_layout.addWidget(desc)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(64)
        self.save_name = make_button("", kind="SecondaryButton")
        self.save_name.clicked.connect(self._save_name)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.save_name)
        name_layout.addLayout(row)
        general.body.addWidget(name_wrap)
        general.body.addWidget(hline())

        self.language_row = FormRow("", "", None, divider=True)
        self.language_control = SegmentedControl([("uz", "UZ"), ("ru", "RU"), ("en", "EN")], "uz")
        self.language_control.setFixedWidth(168)
        self.language_row.set_control(self.language_control)
        general.body.addWidget(self.language_row)

        self.theme_row = FormRow("", "", None, divider=True)
        _t = self.controller.translator.t
        self.theme_control = SegmentedControl(
            [
                ("system", _t("theme_system")),
                ("light", _t("theme_light")),
                ("dark", _t("theme_dark")),
            ],
            self.controller.settings.theme,
        )
        self.theme_control.setFixedWidth(260)
        self.theme_row.set_control(self.theme_control)
        general.body.addWidget(self.theme_row)
        self.layout.addWidget(general)

        # Connection
        self.connection_section = SectionHeader("")
        self.layout.addWidget(self.connection_section)
        connection = Card(margins=(20, 17, 20, 6), spacing=0)
        self.hosting_toggle = ToggleSwitch(dark=self.controller.translator.locale == "en" and False)
        self.hosting_row = FormRow("", "", self.hosting_toggle)
        connection.body.addWidget(self.hosting_row)
        self.auto_toggle = ToggleSwitch()
        self.auto_row = FormRow("", "", self.auto_toggle)
        connection.body.addWidget(self.auto_row)
        self.clipboard_toggle = ToggleSwitch()
        self.clipboard_row = FormRow("", "", self.clipboard_toggle)
        connection.body.addWidget(self.clipboard_row)
        self.edge_row = FormRow("", "")
        self.edge_control = SegmentedControl(
            [
                ("left", ""),
                ("right", ""),
                ("top", ""),
                ("bottom", ""),
            ],
            "right",
        )
        self.edge_control.setFixedWidth(300)
        self.edge_row.set_control(self.edge_control)
        connection.body.addWidget(self.edge_row)
        self.edge_switch_row = FormRow("", "", ToggleSwitch())
        self.edge_switch = self.edge_switch_row.control
        connection.body.addWidget(self.edge_switch_row)
        self.hotkey_row = FormRow("", "")
        self.hotkey_pill = Pill(HOTKEY_LABEL, "muted")
        self.hotkey_row.set_control(self.hotkey_pill)
        connection.body.addWidget(self.hotkey_row)
        self.layout.addWidget(connection)

        # Startup
        self.startup_section = SectionHeader("")
        self.layout.addWidget(self.startup_section)
        startup = Card(margins=(20, 17, 20, 6), spacing=0)
        self.background_toggle = ToggleSwitch()
        self.background_row = FormRow("", "", self.background_toggle)
        startup.body.addWidget(self.background_row)
        self.autostart_toggle = ToggleSwitch()
        self.autostart_row = FormRow("", "", self.autostart_toggle)
        self.autostart_row.setVisible(self.controller.autostart.supported)
        startup.body.addWidget(self.autostart_row)
        self.notifications_toggle = ToggleSwitch()
        self.notifications_row = FormRow("", "", self.notifications_toggle)
        startup.body.addWidget(self.notifications_row)
        self.layout.addWidget(startup)

        # Security
        self.security_section = SectionHeader("")
        self.layout.addWidget(self.security_section)
        security = Card(margins=(20, 17, 20, 8), spacing=12)
        tls_row = QWidget()
        tls_layout = QHBoxLayout(tls_row)
        tls_layout.setContentsMargins(0, 0, 0, 0)
        tls_layout.setSpacing(12)
        tls_layout.addWidget(IconTile("shield", color_name=MINT, size=38, icon_size=19))
        tls_texts = QVBoxLayout()
        tls_texts.setSpacing(2)
        self.tls_title = label(object_name="SectionTitle")
        self.tls_desc = label(object_name="SectionCaption", wrap=True)
        tls_texts.addWidget(self.tls_title)
        tls_texts.addWidget(self.tls_desc)
        tls_layout.addLayout(tls_texts, 1)
        tls_layout.addWidget(Pill("TLS 1.3", "success"))
        security.body.addWidget(tls_row)
        security.body.addWidget(hline())
        self.fp_title = label(object_name="SectionTitle")
        self.fp_desc = label(object_name="SectionCaption", wrap=True)
        security.body.addWidget(self.fp_title)
        security.body.addWidget(self.fp_desc)
        self.fingerprint_field = CopyField()
        self.fingerprint_field.button.clicked.disconnect()
        self.fingerprint_field.button.clicked.connect(self._copy_fingerprint)
        security.body.addWidget(self.fingerprint_field)
        security.body.addWidget(hline())
        self.permissions_title = label(object_name="SectionTitle")
        self.permissions_desc = label(object_name="SectionCaption", wrap=True)
        security.body.addWidget(self.permissions_title)
        security.body.addWidget(self.permissions_desc)
        perm_row = QHBoxLayout()
        perm_row.setSpacing(8)
        self.permission_pill = Pill("", "muted")
        self.permission_button = make_button("", kind="SecondaryButton", icon_name="key")
        self.permission_button.clicked.connect(self._request_permissions)
        perm_row.addWidget(self.permission_pill)
        perm_row.addStretch(1)
        perm_row.addWidget(self.permission_button)
        security.body.addLayout(perm_row)
        self.permission_restart = label(object_name="Faint", wrap=True)
        security.body.addWidget(self.permission_restart)
        security.body.addWidget(hline())
        self.clear_title = label(object_name="SectionTitle")
        self.clear_desc = label(object_name="SectionCaption", wrap=True)
        security.body.addWidget(self.clear_title)
        security.body.addWidget(self.clear_desc)
        self.clear_button = make_button("", kind="DangerButton", icon_name="trash")
        self.clear_button.clicked.connect(self._clear_peers)
        security.body.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignLeft)
        if sys.platform == "win32":
            security.body.addWidget(hline())
            self.firewall_title = label(object_name="SectionTitle")
            self.firewall_desc = label(object_name="SectionCaption", wrap=True)
            self.firewall_status = Pill("", "muted")
            self.firewall_button = make_button("", kind="SecondaryButton", icon_name="shield")
            self.firewall_button.clicked.connect(self._configure_firewall)
            security.body.addWidget(self.firewall_title)
            security.body.addWidget(self.firewall_desc)
            fw_row = QHBoxLayout()
            fw_row.addWidget(self.firewall_status)
            fw_row.addStretch(1)
            fw_row.addWidget(self.firewall_button)
            security.body.addLayout(fw_row)
        else:
            self.firewall_title = None
        self.layout.addWidget(security)

        # Advanced
        self.advanced_section = SectionHeader("")
        self.layout.addWidget(self.advanced_section)
        advanced = Card(margins=(20, 14, 20, 14), spacing=10)
        self.advanced_toggle = make_button("", kind="GhostButton", icon_name="chevron_down")
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        advanced.body.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.advanced_body = QWidget()
        adv_layout = QVBoxLayout(self.advanced_body)
        adv_layout.setContentsMargins(0, 4, 0, 0)
        adv_layout.setSpacing(8)
        self.port_label = label(object_name="Mono")
        self.data_title = label(object_name="Faint")
        self.data_value = label(object_name="Mono", wrap=True)
        self.protocol_label = label(object_name="Mono")
        self.open_data = make_button("", kind="GhostButton", icon_name="folder")
        self.open_data.clicked.connect(self._open_data)
        for widget in (self.port_label, self.data_title, self.data_value, self.protocol_label):
            adv_layout.addWidget(widget)
        adv_layout.addWidget(self.open_data, 0, Qt.AlignmentFlag.AlignLeft)
        self.advanced_body.hide()
        advanced.body.addWidget(self.advanced_body)
        self.layout.addWidget(advanced)
        self.layout.addStretch(1)

    # -------------------------------------------------------------------- wire
    def _wire(self) -> None:
        self.language_control.changed.connect(self._language_changed)
        self.theme_control.changed.connect(self._theme_changed)
        self.hosting_toggle.toggled.connect(self._hosting_changed)
        self.auto_toggle.toggled.connect(
            lambda checked: self._apply_setting("auto_reconnect", checked, self.auto_toggle)
        )
        self.clipboard_toggle.toggled.connect(
            lambda checked: self._apply_setting("clipboard_sync", checked, self.clipboard_toggle)
        )
        self.edge_control.changed.connect(
            lambda value: self._apply_setting("edge", value, self.edge_control)
        )
        self.edge_switch.toggled.connect(
            lambda checked: self._apply_setting("edge_switch", checked, self.edge_switch)
        )
        self.background_toggle.toggled.connect(
            lambda checked: self._apply_setting("background_mode", checked, self.background_toggle)
        )
        self.autostart_toggle.toggled.connect(self._autostart_changed)
        self.notifications_toggle.toggled.connect(
            lambda checked: self._apply_setting("notifications", checked, self.notifications_toggle)
        )
        self.controller.permissions_changed.connect(lambda _info: self._refresh_permissions())
        self.controller.firewall_changed.connect(self._on_firewall_changed)

    def _load_values(self) -> None:
        settings = self.controller.settings
        self.name_edit.setText(settings.device_name)
        self.language_control.set_value(settings.language)
        self._rebuild_theme_control()
        self.hosting_toggle.setChecked(settings.hosting_enabled)
        self.auto_toggle.setChecked(settings.auto_reconnect)
        self.clipboard_toggle.setChecked(settings.clipboard_sync)
        self.edge_control.set_value(settings.edge)
        self.edge_switch.setChecked(settings.edge_switch)
        self.background_toggle.setChecked(settings.background_mode)
        self.autostart_toggle.setChecked(self.controller.autostart.is_enabled())
        self.notifications_toggle.setChecked(settings.notifications)

    def _rebuild_theme_control(self) -> None:
        t = self.controller.translator.t
        self.theme_control.set_labels(
            [
                ("system", t("theme_system")),
                ("light", t("theme_light")),
                ("dark", t("theme_dark")),
            ]
        )
        self.theme_control.set_value(self.controller.settings.theme)

    # ------------------------------------------------------------------ actions
    def _save_name(self) -> None:
        name = self.name_edit.text().strip()
        self.save_name.setEnabled(False)
        self.controller.set_device_name(
            name,
            on_success=lambda _settings: self._saved_name(),
            on_error=lambda err: self._revert_error(err, None),
        )

    def _saved_name(self) -> None:
        self.save_name.setEnabled(True)
        self.toast.emit(self.controller.translator.t("setting_device_name_saved"), "success")

    def _language_changed(self, locale: str) -> None:
        self.controller.set_language(locale)

    def _theme_changed(self, theme: str) -> None:
        self.controller.set_theme(theme)

    def _hosting_changed(self, checked: bool) -> None:
        has_active_host = (
            self._snapshot.connection is not None
            and self._snapshot.connection.role == "host"
            and not checked
        )
        if has_active_host:
            t = self.controller.translator.t
            proceed = ConfirmDialog.ask(
                self,
                title=t("setting_hosting"),
                text=t("hosting_stop_warning"),
                confirm_text=t("confirm"),
                cancel_text=t("cancel"),
            )
            if not proceed:
                self.hosting_toggle.blockSignals(True)
                self.hosting_toggle.setChecked(True)
                self.hosting_toggle.blockSignals(False)
                return
        self._apply_setting("hosting_enabled", checked, self.hosting_toggle)

    def _autostart_changed(self, checked: bool) -> None:
        if not self.controller.autostart.supported:
            return
        self.controller.update_settings(
            {"launch_at_login": checked},
            on_error=lambda err: self._revert_error(err, self.autostart_toggle),
        )

    def _apply_setting(self, key: str, value, control) -> None:
        def on_error(error: BaseException) -> None:
            self._revert_error(error, control)

        self.controller.update_settings({key: value}, on_error=on_error)

    def _revert_error(self, error: BaseException, control) -> None:
        self.toast.emit(self.controller.error_message(error), "error")
        self._load_values()

    def _request_permissions(self) -> None:
        self.controller.request_permissions()
        self._refresh_permissions()
        t = self.controller.translator.t
        self.toast.emit(t("toast_permissions_ready"), "info")

    def _refresh_permissions(self) -> None:
        if not hasattr(self, "permission_pill"):
            return
        info = self.controller.permissions
        t = self.controller.translator.t
        ready = info.ready
        self.permission_pill.set_content(
            t("permissions_granted") if ready else t("permissions_missing"),
            "success" if ready else "warning",
        )
        self.permission_pill.setText(
            t("permissions_granted") if ready else t("permissions_missing")
        )
        detail = info.capture_detail or info.injection_detail
        self.permission_pill.setToolTip(detail)
        self.permission_button.setVisible(sys.platform == "darwin")

    def _copy_fingerprint(self) -> None:
        fingerprint = self._snapshot.local.fingerprint if self._snapshot.local else ""
        if fingerprint and copy_to_clipboard(fingerprint):
            self.toast.emit(self.controller.translator.t("copied"), "success")

    def _clear_peers(self) -> None:
        t = self.controller.translator.t
        if ConfirmDialog.ask(
            self,
            title=t("clear_peers_title"),
            text=t("clear_peers_text"),
            confirm_text=t("clear_peers_confirm"),
            cancel_text=t("cancel"),
        ):
            self.controller.clear_peers(
                on_success=lambda _: self.toast.emit(t("device_removed"), "success"),
                on_error=lambda e: self.toast.emit(self.controller.error_message(e), "error"),
            )

    def _refresh_firewall(self) -> None:
        if sys.platform != "win32":
            return
        try:
            self._firewall_status = self.controller.firewall_status()
        except Exception:  # noqa: BLE001
            return
        self._on_firewall_changed(self._firewall_status)

    def _on_firewall_changed(self, status: FirewallStatus) -> None:
        if sys.platform != "win32" or not hasattr(self, "firewall_status"):
            return
        self._firewall_status = status
        t = self.controller.translator.t
        if status.configured:
            self.firewall_status.set_content(t("firewall_configured"), "success")
            self.firewall_button.setText(t("firewall_configure"))
            self.firewall_button.setEnabled(True)
        else:
            self.firewall_status.set_content(t("firewall_not_configured"), "warning")
            self.firewall_button.setEnabled(True)

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

    def _toggle_advanced(self) -> None:
        self._advanced_open = not self._advanced_open
        self.advanced_body.setVisible(self._advanced_open)
        self.advanced_toggle.setText(
            self.controller.translator.t("advanced_hide" if self._advanced_open else "advanced_show")
        )
        self.advanced_toggle.setIcon(
            pixmap("chevron_down" if not self._advanced_open else "chevron_down", MUTED, 15)
        )

    def _open_data(self) -> None:
        if not self.controller.open_path(self.controller.service.paths.root):
            self.toast.emit(self.controller.translator.t("cannot_open_path"), "error")

    # ------------------------------------------------------------------ update
    def retranslate(self) -> None:
        t = self.controller.translator.t
        self.header.title.setText(t("settings_title"))
        self.header.subtitle.setText(t("settings_subtitle"))
        self.general_section.title.setText(t("settings_general"))
        self.connection_section.title.setText(t("settings_connection"))
        self.startup_section.title.setText(t("settings_startup"))
        self.security_section.title.setText(t("settings_security"))
        self.advanced_section.title.setText(t("settings_advanced"))
        self.save_name.setText(t("setting_device_name_apply"))
        self.language_row.title.setText(t("setting_language"))
        self.language_row.set_description(t("setting_language_desc"))
        self.theme_row.title.setText(t("setting_theme"))
        self.theme_row.set_description(t("setting_theme_desc"))
        self.hosting_row.title.setText(t("setting_hosting"))
        self.hosting_row.set_description(t("setting_hosting_desc"))
        self.auto_row.title.setText(t("setting_auto_reconnect"))
        self.auto_row.set_description(t("setting_auto_reconnect_desc"))
        self.clipboard_row.title.setText(t("setting_clipboard"))
        self.clipboard_row.set_description(t("setting_clipboard_desc"))
        self.edge_row.title.setText(t("setting_edge"))
        self.edge_row.set_description(t("setting_edge_desc"))
        self.edge_switch_row.title.setText(t("setting_edge_switch"))
        self.edge_switch_row.set_description(t("setting_edge_switch_desc"))
        self.hotkey_row.title.setText(t("setting_hotkey"))
        self.hotkey_row.set_description(t("setting_hotkey_desc"))
        self.background_row.title.setText(t("setting_background"))
        self.background_row.set_description(t("setting_background_desc"))
        self.autostart_row.title.setText(t("setting_launch_at_login"))
        self.autostart_row.set_description(t("setting_launch_desc"))
        self.notifications_row.title.setText(t("setting_notifications"))
        self.notifications_row.set_description(t("setting_notifications_desc"))
        self.tls_title.setText(t("security_tls"))
        self.tls_desc.setText(t("security_tls_desc"))
        self.fp_title.setText(t("security_fingerprint"))
        self.fp_desc.setText(t("security_fingerprint_desc"))
        self.permissions_title.setText(t("security_permissions"))
        self.permissions_desc.setText(t("security_permissions_desc"))
        self.permission_restart.setText(t("permissions_restart_hint"))
        self.clear_title.setText(t("security_clear_peers"))
        self.clear_desc.setText(t("security_paired_devices"))
        self.clear_button.setText(t("security_clear_peers"))
        self.permission_button.setText(t("permissions_open"))
        self.advanced_toggle.setText(t("advanced_hide") if self._advanced_open else t("advanced_show"))
        self.data_title.setText(t("data_directory"))
        self.open_data.setText(t("open_data_dir"))
        if sys.platform == "win32" and self.firewall_title is not None:
            self.firewall_title.setText(t("firewall_title"))
            self.firewall_desc.setText(t("firewall_desc"))
            self.firewall_button.setText(t("firewall_configure"))
        self.edge_control.set_labels(
            [
                ("left", t("edge_left")),
                ("right", t("edge_right")),
                ("top", t("edge_top")),
                ("bottom", t("edge_bottom")),
            ]
        )
        self._rebuild_theme_control()
        self._refresh_permissions()
        self._refresh_firewall_labels()

    def _refresh_firewall_labels(self) -> None:
        if sys.platform != "win32" or not hasattr(self, "firewall_status"):
            return
        t = self.controller.translator.t
        self.firewall_status.set_content(
            t("firewall_configured") if self._firewall_status.configured else t("firewall_not_configured"),
            "success" if self._firewall_status.configured else "warning",
        )

    def apply_palette(self, palette) -> None:
        dark = palette.mode == "dark"
        for toggle in (
            self.hosting_toggle,
            self.auto_toggle,
            self.clipboard_toggle,
            self.edge_switch,
            self.background_toggle,
            self.autostart_toggle,
            self.notifications_toggle,
        ):
            if isinstance(toggle, ToggleSwitch):
                toggle.set_dark(dark)

    def refresh(self, snapshot: ServiceSnapshot) -> None:
        self._snapshot = snapshot
        if hasattr(self, "permission_pill"):
            self._refresh_permissions()
        local = snapshot.local
        if local:
            self.fingerprint_field.set_text(local.fingerprint)
        port = snapshot.listener_port or DEFAULT_PORT
        self.port_label.setText(f"{self.controller.translator.t('listener_port')}: {port}")
        self.protocol_label.setText(
            f"{self.controller.translator.t('advanced_protocol')}: {APP_VERSION} · TLS 1.3"
        )
        self.data_value.setText(str(self.controller.service.paths.root))
        # Keep toggles in sync when values change outside this page.
        settings = self.controller.settings
        for control, value in (
            (self.hosting_toggle, settings.hosting_enabled),
            (self.auto_toggle, settings.auto_reconnect),
            (self.clipboard_toggle, settings.clipboard_sync),
            (self.edge_switch, settings.edge_switch),
            (self.background_toggle, settings.background_mode),
            (self.notifications_toggle, settings.notifications),
        ):
            control.blockSignals(True)
            control.setChecked(value)
            control.blockSignals(False)
        self.edge_control.blockSignals(True)
        self.edge_control.set_value(settings.edge)
        self.edge_control.blockSignals(False)
        self.language_control.set_value(settings.language)
        self.theme_control.set_value(settings.theme)


__all__ = ["SettingsPage"]
