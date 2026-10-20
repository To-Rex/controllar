"""Paired-device management."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.state import PeerView, ServiceSnapshot
from ..dialogs import ConfirmDialog
from ..icons import pixmap
from ..widgets import (
    MINT,
    MUTED,
    Card,
    EmptyState,
    PageHeader,
    Pill,
    StatusPill,
    copy_to_clipboard,
    icon_button,
    label,
    make_avatar_for_name,
    make_button,
)

if TYPE_CHECKING:
    from ..controller import ToRexLinkController


class PeerCard(QFrame):
    connect = Signal(str)
    disconnect = Signal()
    remove = Signal(str)

    def __init__(self, peer: PeerView, t, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._peer = peer
        layer = QHBoxLayout(self)
        layer.setContentsMargins(18, 15, 18, 15)
        layer.setSpacing(13)
        layer.addWidget(make_avatar_for_name(peer.name), 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(5)
        name_row = QHBoxLayout()
        name_row.setSpacing(7)
        name_row.addWidget(label(peer.name, object_name="DeviceName"))
        if peer.connected:
            name_row.addWidget(Pill(t("device_connected_badge"), "success"))
        else:
            name_row.addWidget(Pill(t("device_offline_badge"), "muted"))
        name_row.addStretch(1)
        body.addLayout(name_row)
        meta_parts = []
        if peer.platform:
            key = f"platform_{peer.platform}"
            platform_text = t(key) if t(key) != key else peer.platform
            meta_parts.append(platform_text)
        if peer.address:
            meta_parts.append(f"{peer.address}:{peer.port}" if peer.port else peer.address)
        if peer.last_seen:
            meta_parts.append(f"{t('last_seen')}: {peer.last_seen[:10]}")
        body.addWidget(label("  ·  ".join(meta_parts), object_name="DeviceMeta", wrap=True))
        details = QHBoxLayout()
        details.setSpacing(7)
        fp_label = label(f"{t('fingerprint')}: {peer.fingerprint_short}", object_name="Mono")
        details.addWidget(fp_label)
        copy = icon_button("copy", size=13, tooltip=t("copy_fingerprint"), color_name=MUTED, parent=self)
        copy.clicked.connect(lambda: copy_to_clipboard(peer.fingerprint))
        details.addWidget(copy)
        details.addStretch(1)
        body.addLayout(details)
        layer.addLayout(body, 1)
        actions = QVBoxLayout()
        actions.setSpacing(6)
        if peer.connected:
            button = make_button(t("device_disconnect"), kind="SecondaryButton", icon_name="power", height=30)
            button.clicked.connect(self.disconnect.emit)
        elif peer.outbound:
            button = make_button(t("device_connect"), kind="PrimaryButton", icon_name="arrow_right", height=30)
            button.clicked.connect(lambda: self.connect.emit(peer.peer_id))
        else:
            button = make_button(t("inbound_hint"), kind="GhostButton", height=30)
            button.setEnabled(False)
        actions.addWidget(button)
        remove = make_button(t("device_remove"), kind="GhostButton", icon_name="trash", height=28)
        remove.setStyleSheet("color:#F05A68;")
        remove.clicked.connect(lambda: self.remove.emit(peer.peer_id))
        actions.addWidget(remove)
        actions.addStretch(1)
        layer.addLayout(actions, 0)


class DevicesPage(QWidget):
    navigate_connect = Signal()
    toast = Signal(str, str)

    def __init__(self, controller: ToRexLinkController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._snapshot: ServiceSnapshot = controller.snapshot
        self._peers_signature: tuple | None = None
        self._nearby_signature: tuple | None = None
        self._build()
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

        self.add_button = make_button("", kind="PrimaryButton", icon_name="plus")
        self.add_button.clicked.connect(self.navigate_connect.emit)
        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.status = StatusPill("offline", "")
        action_layout.addWidget(self.status)
        action_layout.addWidget(self.add_button)
        self.header = PageHeader("", "", action=action_row)
        self.layout.addWidget(self.header)

        self.auto_note = QFrame()
        self.auto_note.setObjectName("Banner")
        note = QHBoxLayout(self.auto_note)
        note.setContentsMargins(12, 9, 12, 9)
        note.setSpacing(8)
        ico = QLabel()
        ico.setPixmap(pixmap("shield", MINT, 15))
        note.addWidget(ico)
        self.auto_note_text = label(object_name="BannerText", wrap=True)
        note.addWidget(self.auto_note_text, 1)
        self.layout.addWidget(self.auto_note)

        self.list_card = Card(margins=(16, 14, 16, 14), spacing=9)
        head = QHBoxLayout()
        self.list_title = label(object_name="SectionTitle")
        head.addWidget(self.list_title)
        head.addStretch(1)
        self.count_pill = Pill("0", "muted")
        head.addWidget(self.count_pill)
        self.list_card.body.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(9)
        self.list_card.body.addLayout(self.rows)
        self.empty = EmptyState(
            "devices",
            "",
            "",
            action=None,
        )
        self.add_empty = make_button("", kind="PrimaryButton", icon_name="plus")
        self.add_empty.clicked.connect(self.navigate_connect.emit)
        self.empty.layout().addWidget(self.add_empty, 0, Qt.AlignmentFlag.AlignHCenter)
        self.list_card.body.addWidget(self.empty)
        self.layout.addWidget(self.list_card)

        nearby_head = QHBoxLayout()
        self.nearby_title = label(object_name="SectionTitle")
        nearby_head.addWidget(self.nearby_title)
        nearby_head.addStretch(1)
        self.nearby_action = make_button("", kind="LinkButton")
        self.nearby_action.clicked.connect(self.navigate_connect.emit)
        nearby_head.addWidget(self.nearby_action)
        self.layout.addLayout(nearby_head)
        self.nearby_card = Card(margins=(16, 13, 16, 13), spacing=8)
        self.nearby_rows = QVBoxLayout()
        self.nearby_rows.setSpacing(6)
        self.nearby_card.body.addLayout(self.nearby_rows)
        self.nearby_empty = label(object_name="Faint", wrap=True)
        self.nearby_card.body.addWidget(self.nearby_empty)
        self.layout.addWidget(self.nearby_card)
        self.layout.addStretch(1)

    def retranslate(self) -> None:
        t = self.controller.translator.t
        self.header.title.setText(t("devices_title"))
        self.header.subtitle.setText(t("devices_subtitle"))
        self.add_button.setText(t("devices_add_button"))
        self.auto_note_text.setText(t("devices_no_peers_hint"))
        self.list_title.setText(t("paired_devices"))
        self.empty.title.setText(t("devices_empty_title"))
        self.empty.description.setText(t("devices_empty_desc"))
        self.add_empty.setText(t("devices_add_button"))
        self.nearby_title.setText(t("nearby_devices"))
        self.nearby_action.setText(t("home_see_all"))
        self.nearby_empty.setText(t("no_devices_found"))
        self._peers_signature = None
        self._nearby_signature = None
        self.refresh(self._snapshot)

    def refresh(self, snapshot: ServiceSnapshot) -> None:
        self._snapshot = snapshot
        t = self.controller.translator.t
        status_text = {
            "connected": t("status_connected"),
            "connecting": t("status_connecting"),
            "reconnecting": t("status_reconnecting"),
            "pairing": t("status_pairing"),
            "hosting": t("status_hosting"),
            "error": t("status_error"),
        }.get(snapshot.status, t("status_offline"))
        self.status.set_status(snapshot.status, status_text)
        peers = snapshot.peers
        peers_signature = (self.controller.translator.locale, peers)
        if peers_signature != self._peers_signature:
            self._peers_signature = peers_signature
            while self.rows.count():
                item = self.rows.takeAt(0)
                if item and item.widget():
                    item.widget().hide()
                    item.widget().setParent(None)
                    item.widget().deleteLater()
            for peer in peers:
                card = PeerCard(peer, t)
                card.connect.connect(self._connect_peer)
                card.disconnect.connect(self.controller.disconnect)
                card.remove.connect(self._remove_peer)
                self.rows.addWidget(card)
        self.count_pill.setText(str(len(peers)))
        self.empty.setVisible(not peers)
        self.auto_note.setVisible(not peers)
        discovered = snapshot.discovered[:4]
        nearby_signature = (self.controller.translator.locale, tuple(discovered))
        if nearby_signature != self._nearby_signature:
            self._nearby_signature = nearby_signature
            while self.nearby_rows.count():
                item = self.nearby_rows.takeAt(0)
                if item and item.widget():
                    item.widget().hide()
                    item.widget().setParent(None)
                    item.widget().deleteLater()
            for device in discovered:
                row = QFrame()
                row.setObjectName("DeviceRow")
                line = QHBoxLayout(row)
                line.setContentsMargins(9, 8, 9, 8)
                line.setSpacing(9)
                line.addWidget(make_avatar_for_name(device.name))
                texts = QVBoxLayout()
                texts.setSpacing(1)
                texts.addWidget(label(device.name, object_name="DeviceName"))
                texts.addWidget(label(device.address, object_name="DeviceMeta"))
                line.addLayout(texts, 1)
                if device.paired:
                    line.addWidget(Pill(t("device_paired"), "success"))
                else:
                    line.addWidget(Pill(t("device_new")))
                self.nearby_rows.addWidget(row)

    def _connect_peer(self, peer_id: str) -> None:
        self.controller.connect_peer(
            peer_id,
            on_error=lambda err: self.toast.emit(self.controller.error_message(err), "error"),
        )

    def _remove_peer(self, peer_id: str) -> None:
        peer = next((p for p in self._snapshot.peers if p.peer_id == peer_id), None)
        if peer is None:
            return
        t = self.controller.translator.t
        if ConfirmDialog.ask(
            self,
            title=t("device_remove_confirm_title"),
            text=t("device_remove_confirm_text").format(name=peer.name),
            confirm_text=t("device_remove"),
            cancel_text=t("cancel"),
        ):
            self.controller.remove_peer(
                peer_id,
                on_success=lambda _: self.toast.emit(t("device_removed"), "success"),
                on_error=lambda err: self.toast.emit(self.controller.error_message(err), "error"),
            )


__all__ = ["DevicesPage"]
