"""Pairing and connection workflow page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.state import DiscoveredView, PairingInfo, ServiceSnapshot
from ..dialogs import JoinDialog, QRScannerDialog
from ..icons import pixmap
from ..widgets import (
    ACCENT,
    MINT,
    MUTED,
    Banner,
    Card,
    CodeDisplay,
    CountdownLabel,
    EmptyState,
    IconTile,
    PageHeader,
    Pill,
    QRCodeView,
    SectionHeader,
    StatusPill,
    StepStrip,
    copy_to_clipboard,
    hline,
    icon_button,
    label,
    make_avatar_for_name,
    make_button,
)

if TYPE_CHECKING:
    from ..controller import ToRexLinkController


class DevicePickRow(QFrame):
    connect_clicked = Signal(str)

    def __init__(self, device: DiscoveredView, t, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DeviceRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 9, 10, 9)
        row.setSpacing(9)
        row.addWidget(make_avatar_for_name(device.name))
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(label(device.name, object_name="DeviceName"))
        meta = f"{t('platform_' + device.platform) if device.platform in {'macos', 'windows'} else device.platform} · {device.address}"
        texts.addWidget(label(meta, object_name="DeviceMeta"))
        row.addLayout(texts, 1)
        if device.paired:
            row.addWidget(Pill(t("device_paired"), "success"))
        button = icon_button("arrow_right", size=15, parent=self)
        button.setToolTip(t("connect_to_device"))
        button.clicked.connect(lambda: self.connect_clicked.emit(device.device_id))
        row.addWidget(button)


class ConnectPage(QWidget):
    navigate_devices = Signal()
    toast = Signal(str, str)

    def __init__(self, controller: ToRexLinkController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._snapshot: ServiceSnapshot = controller.snapshot
        self._pairing_busy = False
        self._join_busy = False
        self._discovered_signature: tuple | None = None
        self._build()
        self._wire()
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
        self.layout = QVBoxLayout(container)
        self.layout.setContentsMargins(30, 26, 30, 30)
        self.layout.setSpacing(18)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        action = QWidget()
        action_row = QHBoxLayout(action)
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.header_status = StatusPill("hosting", "")
        action_row.addWidget(self.header_status)
        self.header = PageHeader("", "", action=action)
        self.layout.addWidget(self.header)

        self.stepper = StepStrip(["", "", ""], 0)
        self.layout.addWidget(self.stepper)

        self.banner = Banner("", "", level="warning", icon_name="alert")
        self.banner.hide()
        self.layout.addWidget(self.banner)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(self._build_host_card(), 1)
        columns.addWidget(self._build_join_card(), 1)
        self.layout.addLayout(columns)

        self.connection_card = Card(margins=(20, 16, 20, 16), spacing=10)
        self.connection_card.hide()
        self.layout.addWidget(self.connection_card)
        self.conn_title = label(object_name="SectionTitle")
        self.connection_card.body.addWidget(self.conn_title)
        self.conn_text = label(object_name="SectionCaption", wrap=True)
        self.connection_card.body.addWidget(self.conn_text)
        self.conn_actions = QHBoxLayout()
        self.conn_actions.addStretch(1)
        self.conn_disconnect = make_button("", kind="SecondaryButton", icon_name="power")
        self.conn_actions.addWidget(self.conn_disconnect)
        self.connection_card.body.addLayout(self.conn_actions)

        self.layout.addStretch(1)

    def _build_host_card(self) -> QFrame:
        self.host_card = Card(margins=(22, 20, 22, 18), spacing=12)
        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        head_row.addWidget(IconTile("monitor", color_name=ACCENT, size=36, icon_size=18))
        head_texts = QVBoxLayout()
        head_texts.setSpacing(2)
        self.host_title = label(object_name="SectionTitle")
        self.host_desc = label(object_name="SectionCaption", wrap=True)
        self.host_desc.setMinimumWidth(1)
        self.host_desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head_texts.addWidget(self.host_title)
        head_texts.addWidget(self.host_desc)
        head_row.addLayout(head_texts, 1)
        self.host_card.body.addLayout(head_row)
        self.host_card.body.addWidget(hline())

        self.host_ready = QWidget()
        ready_layout = QVBoxLayout(self.host_ready)
        ready_layout.setContentsMargins(0, 4, 0, 0)
        ready_layout.setSpacing(11)
        self.host_pill = Pill("", "muted")
        ready_layout.addWidget(self.host_pill, 0, Qt.AlignmentFlag.AlignLeft)
        self.host_hint = label(object_name="Faint", wrap=True)
        ready_layout.addWidget(self.host_hint)
        self.start_button = make_button("", kind="PrimaryButton", icon_name="plus")
        ready_layout.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.host_card.body.addWidget(self.host_ready)

        self.host_pairing = QWidget()
        pairing_layout = QVBoxLayout(self.host_pairing)
        pairing_layout.setContentsMargins(0, 4, 0, 0)
        pairing_layout.setSpacing(10)
        code_head = QHBoxLayout()
        self.code_label = label(object_name="Faint")
        code_head.addWidget(self.code_label)
        code_head.addStretch(1)
        self.attempts = label(object_name="Faint")
        code_head.addWidget(self.attempts)
        pairing_layout.addLayout(code_head)
        self.code_display = CodeDisplay()
        pairing_layout.addWidget(self.code_display)
        timer_row = QHBoxLayout()
        timer_row.setSpacing(5)
        timer_icon = QLabel()
        timer_icon.setPixmap(pixmap("clock", MUTED, 13))
        timer_row.addWidget(timer_icon)
        self.timer_caption = label(object_name="Faint")
        timer_row.addWidget(self.timer_caption)
        self.countdown = CountdownLabel()
        self.countdown.setObjectName("MonoValue")
        timer_row.addWidget(self.countdown)
        timer_row.addStretch(1)
        pairing_layout.addLayout(timer_row)
        self.copy_code_button = make_button("", kind="SoftButton", icon_name="copy")
        self.new_code_button = make_button("", kind="GhostButton", icon_name="refresh")
        self.cancel_code_button = make_button("", kind="GhostButton", icon_name="close")
        buttons = QHBoxLayout()
        buttons.setSpacing(7)
        buttons.addWidget(self.copy_code_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_code_button)
        buttons.addWidget(self.new_code_button)
        pairing_layout.addLayout(buttons)
        self.qr_toggle = make_button("", kind="GhostButton", icon_name="qr")
        pairing_layout.addWidget(self.qr_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.qr_wrap = QFrame()
        self.qr_wrap.setObjectName("InnerCard")
        qr_layout = QVBoxLayout(self.qr_wrap)
        qr_layout.setContentsMargins(16, 16, 16, 12)
        qr_layout.setSpacing(8)
        self.qr = QRCodeView(218)
        qr_layout.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        self.qr_hint = label(object_name="Faint", wrap=True, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.qr_hint.setMaximumWidth(300)
        qr_layout.addWidget(self.qr_hint)
        self.qr_wrap.hide()
        pairing_layout.addWidget(self.qr_wrap, 0, Qt.AlignmentFlag.AlignHCenter)
        self.host_pairing.hide()
        self.host_card.body.addWidget(self.host_pairing)

        self.host_disabled = QWidget()
        disabled_layout = QVBoxLayout(self.host_disabled)
        disabled_layout.setContentsMargins(0, 4, 0, 0)
        disabled_layout.setSpacing(8)
        self.disabled_hint = label(object_name="Faint", wrap=True)
        disabled_layout.addWidget(self.disabled_hint)
        self.enable_button = make_button("", kind="SecondaryButton", icon_name="power")
        disabled_layout.addWidget(self.enable_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.host_disabled.hide()
        self.host_card.body.addWidget(self.host_disabled)
        self.host_card.body.addStretch(1)
        return self.host_card

    def _build_join_card(self) -> QFrame:
        self.join_card = Card(margins=(22, 20, 22, 18), spacing=12)
        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        head_row.addWidget(IconTile("link", color_name=MINT, size=36, icon_size=18))
        head_texts = QVBoxLayout()
        head_texts.setSpacing(2)
        self.join_title = label(object_name="SectionTitle")
        self.join_desc = label(object_name="SectionCaption", wrap=True)
        self.join_desc.setMinimumWidth(1)
        self.join_desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head_texts.addWidget(self.join_title)
        head_texts.addWidget(self.join_desc)
        head_row.addLayout(head_texts, 1)
        self.join_card.body.addLayout(head_row)
        self.join_card.body.addWidget(hline())

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(7)
        self.code_button = make_button("", kind="SecondaryButton", icon_name="key")
        self.qr_button = make_button("", kind="SecondaryButton", icon_name="scan")
        self.ip_button = make_button("", kind="GhostButton", icon_name="network")
        buttons_row.addWidget(self.code_button)
        buttons_row.addWidget(self.qr_button)
        buttons_row.addWidget(self.ip_button)
        buttons_row.addStretch(1)
        self.join_card.body.addLayout(buttons_row)

        self.devices_head = SectionHeader("")
        self.join_card.body.addWidget(self.devices_head)
        self.devices_container = QVBoxLayout()
        self.devices_container.setSpacing(6)
        self.join_card.body.addLayout(self.devices_container)
        self.no_devices = EmptyState(
            "wifi",
            "",
            "",
        )
        self.no_devices.setStyleSheet("background: transparent;")
        self.no_devices.hide()
        self.join_card.body.addWidget(self.no_devices)
        self.scan_note = label(
            "scanning",
            object_name="Faint",
            wrap=True,
        )
        self.scan_note.hide()
        self.join_card.body.addWidget(self.scan_note)
        self.join_card.body.addStretch(1)

        self.pair_actions = QWidget()
        actions = QHBoxLayout(self.pair_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.join_card.body.addWidget(self.pair_actions)
        return self.join_card

    def _wire(self) -> None:
        self.start_button.clicked.connect(self.start_pairing)
        self.enable_button.clicked.connect(self._enable_hosting)
        self.copy_code_button.clicked.connect(self._copy_code)
        self.new_code_button.clicked.connect(self.start_pairing)
        self.cancel_code_button.clicked.connect(self._cancel_pairing)
        self.qr_toggle.clicked.connect(self._toggle_qr)
        self.code_button.clicked.connect(lambda: self._open_manual())
        self.ip_button.clicked.connect(lambda: self._open_manual(manual=True))
        self.qr_button.clicked.connect(self._open_scanner)
        self.conn_disconnect.clicked.connect(self.controller.disconnect)
        self.countdown.expired.connect(lambda: self.refresh(self.controller.snapshot))
        self.header_status.set_status("hosting", "")

    # --------------------------------------------------------------- operations
    def start_pairing(self) -> None:
        if self._pairing_busy:
            return
        self._pairing_busy = True
        self._set_banner("", "", "info")
        self.start_button.setEnabled(False)
        self.start_button.setText(self.controller.translator.t("loading"))
        self.controller.begin_pairing(
            on_success=self._pairing_started,
            on_error=self._pairing_error,
        )

    def _pairing_started(self, pair_info: object) -> None:
        self._pairing_busy = False
        self.start_button.setEnabled(True)
        self.refresh(self.controller.snapshot)

    def _pairing_error(self, error: BaseException) -> None:
        self._pairing_busy = False
        self.start_button.setEnabled(True)
        self.start_button.setText(self.controller.translator.t("start_pairing"))
        self._set_banner(
            self.controller.translator.t("pairing_failed_title"),
            self.controller.error_message(error),
            "danger",
        )

    def _enable_hosting(self) -> None:
        self.controller.update_settings({"hosting_enabled": True})

    def _cancel_pairing(self) -> None:
        self.controller.cancel_pairing()
        self._set_banner("", "", "info")
        self.refresh(self.controller.snapshot)

    def _copy_code(self) -> None:
        pairing = self._snapshot.pairing
        if pairing is None:
            return
        if copy_to_clipboard(pairing.code):
            self.toast.emit("toast_code_copied", "success")

    def _toggle_qr(self) -> None:
        show = not self.qr_wrap.isVisible()
        self.qr_wrap.setVisible(show)
        self.qr_toggle.setText(
            self.controller.translator.t("pairing_hide_qr" if show else "pairing_show_qr")
        )
        if show and self._snapshot.pairing:
            self.qr.set_data(self._snapshot.pairing.qr_uri)

    def _open_manual(self, manual: bool = False) -> None:
        dialog = JoinDialog(self.controller, manual=True, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set_banner("", "", "info")

    def _open_scanner(self) -> None:
        dialog = QRScannerDialog(self.controller, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.payload is not None:
            self._show_join_busy()
            self.controller.pair_with_payload(
                dialog.payload,
                on_success=self._join_success,
                on_error=self._join_error,
            )

    def _show_join_busy(self) -> None:
        self._join_busy = True
        for control in (self.qr_button, self.code_button, self.ip_button, self.start_button):
            control.setEnabled(False)
        self.qr_button.setText(self.controller.translator.t("pairing_progress"))
        self._set_banner("", "", "info")

    def _clear_join_busy(self) -> None:
        self._join_busy = False
        self.qr_button.setText(self.controller.translator.t("join_qr_button"))
        if self._snapshot.connection is None:
            for control in (self.qr_button, self.code_button, self.ip_button, self.start_button):
                control.setEnabled(True)

    def _join_success(self, _peer: object) -> None:
        self._clear_join_busy()
        self.refresh(self.controller.snapshot)
        self.toast.emit("pairing_completed_title", "success")

    def _join_error(self, error: BaseException) -> None:
        self._clear_join_busy()
        self._set_banner(
            self.controller.translator.t("pairing_failed_title"),
            self.controller.error_message(error),
            "danger",
        )
        self.refresh(self.controller.snapshot)

    def _set_banner(self, title: str, text: str, level: str) -> None:
        if not title and not text:
            self.banner.hide()
            return
        self.banner.set_content(title=title, text=text, level=level)
        self.banner.show()

    def _open_join_for(self, device: DiscoveredView) -> None:
        if device.paired:
            peer = next(
                (item for item in self._snapshot.peers if item.peer_id == device.device_id),
                None,
            )
            if peer is not None and peer.outbound:
                self._show_join_busy()
                self.controller.connect_peer(
                    peer.peer_id,
                    on_success=self._join_success,
                    on_error=self._join_error,
                )
                return
        dialog = JoinDialog(
            self.controller,
            device_name=device.name,
            host=device.address,
            port=device.port,
            fingerprint=device.fingerprint,
            device_id=device.device_id,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set_banner("", "", "info")

    # ---------------------------------------------------------------- rendering
    def retranslate(self) -> None:
        t = self.controller.translator.t
        self.header.title.setText(t("connect_title"))
        self.header.subtitle.setText(t("connect_subtitle"))
        self.stepper.set_labels([t("step_choose"), t("step_verify"), t("step_connect")])
        self.host_title.setText(t("host_card_title"))
        self.host_desc.setText(t("host_card_desc"))
        self.join_title.setText(t("join_card_title"))
        self.join_desc.setText(t("join_card_desc"))
        self.code_label.setText(t("pairing_code_label"))
        self.timer_caption.setText(t("pairing_expires"))
        self.copy_code_button.setText(t("pairing_copy_code"))
        self.new_code_button.setText(t("pairing_new_code"))
        self.cancel_code_button.setText(t("cancel"))
        self.qr_toggle.setText(t("pairing_hide_qr") if self.qr_wrap.isVisible() else t("pairing_show_qr"))
        self.qr_hint.setText(t("pairing_code_hint"))
        self.start_button.setText(t("start_pairing"))
        self.enable_button.setText(t("enable_hosting"))
        self.disabled_hint.setText(t("host_disabled_hint"))
        self.code_button.setText(t("join_code_button"))
        self.qr_button.setText(t("join_qr_button"))
        self.ip_button.setText(t("join_manual_button"))
        self.devices_head.title.setText(t("nearby_devices"))
        self.no_devices.title.setText(t("no_devices_found"))
        self.no_devices.description.setText(t("join_card_desc"))
        self.scan_note.setText(t("searching_devices"))
        self.conn_title.setText(t("active_connection"))
        self._discovered_signature = None
        self.conn_disconnect.setText(t("disconnect"))
        self.host_pill.set_content(t("ready"), "muted")
        self.host_hint.setText(t("pairing_code_hint"))
        self.refresh(self._snapshot)

    def refresh(self, snapshot: ServiceSnapshot) -> None:
        self._snapshot = snapshot
        t = self.controller.translator.t
        status_map = {
            "offline": t("status_offline"),
            "hosting": t("status_hosting"),
            "pairing": t("status_pairing"),
            "connecting": t("status_connecting"),
            "connected": t("status_connected"),
            "reconnecting": t("status_reconnecting"),
            "error": t("status_error"),
        }
        self.header_status.set_status(snapshot.status, status_map.get(snapshot.status, t("status_offline")))
        self.stepper.set_current(2 if snapshot.connection else 1 if snapshot.pairing or snapshot.status in {"connecting", "pairing"} else 0)
        pairing = snapshot.pairing
        has_session = snapshot.connection is not None
        if not snapshot.hosting_enabled and not pairing and not has_session:
            self.host_ready.hide()
            self.host_pairing.hide()
            self.host_disabled.show()
        elif pairing:
            self.host_ready.hide()
            self.host_disabled.hide()
            self.host_pairing.show()
            self._render_pairing(pairing)
        else:
            self.host_disabled.hide()
            self.host_pairing.hide()
            self.host_ready.show()
            self.start_button.setEnabled(not has_session and not self._pairing_busy)
            self.start_button.setVisible(not has_session)
            self.start_button.setText(t("start_pairing"))
            self.host_hint.setText(t("pairing_code_hint") if not has_session else t("pairing_completed_desc"))
            self.host_pill.set_content(
                t("ready") if not has_session else t("status_connected"),
                "muted" if not has_session else "success",
            )
        self._render_discovered(snapshot, t)
        connection = snapshot.connection
        if connection:
            self.connection_card.show()
            self.conn_text.setText(
                t("active_connection_hint").format(
                    name=f"{connection.name}",
                    address=f"{connection.address}:{connection.port}",
                )
            )
            self.conn_title.setText(t("active_connection"))
            self.code_button.setEnabled(False)
            self.qr_button.setEnabled(not self._join_busy)
            self.start_button.setEnabled(False)
        else:
            self.connection_card.hide()
            self.code_button.setEnabled(True)
            self.qr_button.setEnabled(not self._join_busy)

    def _render_pairing(self, pairing: PairingInfo) -> None:
        t = self.controller.translator.t
        self.code_display.set_code(pairing.code)
        self.attempts.setText(t("pairing_attempts").format(count=pairing.attempts_remaining))
        self.countdown.set_deadline(pairing.expires_at)
        if self.qr_wrap.isVisible():
            self.qr.set_data(pairing.qr_uri)
        self.qr_hint.setText(t("pairing_code_hint"))

    def _render_discovered(self, snapshot: ServiceSnapshot, t) -> None:
        devices = snapshot.discovered[:4]
        signature = (self.controller.translator.locale, tuple(devices))
        if signature != self._discovered_signature:
            self._discovered_signature = signature
            while self.devices_container.count():
                item = self.devices_container.takeAt(0)
                if item and item.widget():
                    item.widget().hide()
                    item.widget().setParent(None)
                    item.widget().deleteLater()
            for device in devices:
                row = DevicePickRow(device, t)
                row.connect_clicked.connect(lambda _id, d=device: self._open_join_for(d))
                self.devices_container.addWidget(row)
        self.no_devices.setVisible(not devices)
        self.scan_note.setVisible(False)
        if not devices:
            self.no_devices.title.setText(t("no_devices_found"))
            self.no_devices.description.setText(t("join_card_desc"))


__all__ = ["ConnectPage"]
