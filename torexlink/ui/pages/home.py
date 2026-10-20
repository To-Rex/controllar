"""Landing page: at-a-glance status, quick actions and saved peers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...constants import HOTKEY_LABEL
from ...core.state import PeerView, ServiceSnapshot
from ..icons import pixmap
from ..widgets import (
    ACCENT,
    AMBER,
    DANGER,
    MINT,
    MUTED,
    Card,
    Dot,
    IconTile,
    PageHeader,
    Pill,
    StatusPill,
    copy_to_clipboard,
    hline,
    icon_button,
    label,
    make_avatar_for_name,
    make_button,
)

if TYPE_CHECKING:
    from ..controller import ToRexLinkController


class HeroCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeroCard")
        self.setMinimumHeight(244)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(width - 190, -180, 420, 420)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawEllipse(width - 120, -110, 280, 280)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(108, 92, 255, 42))
        painter.drawEllipse(width - 160, 70, 230, 230)
        painter.end()


class HomePage(QWidget):
    toast = Signal(str, str)
    pair_requested = Signal()
    connect_requested = Signal()
    devices_requested = Signal()
    disconnect_requested = Signal()
    connect_peer_requested = Signal(str)

    def __init__(self, controller: ToRexLinkController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._snapshot: ServiceSnapshot = controller.snapshot
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
        self.layout.setSpacing(20)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        header_action = QWidget()
        header_row = QHBoxLayout(header_action)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)
        self.header_status = StatusPill("hosting", "")
        header_row.addWidget(self.header_status)
        self.refresh_button = icon_button("refresh", tooltip="Refresh", size=16)
        self.refresh_button.clicked.connect(lambda: self.refresh(self.controller.snapshot))
        header_row.addWidget(self.refresh_button)
        self.header = PageHeader(
            "Xush kelibsiz",
            "Bir klaviatura va sichqoncha bilan ikki kompyuterni boshqaring.",
            action=header_action,
        )
        self.layout.addWidget(self.header)

        # Hero
        self.hero = HeroCard()
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(30, 26, 26, 26)
        hero_layout.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(7)
        self.eyebrow = label(object_name="HeroEyebrow")
        left.addWidget(self.eyebrow)
        self.hero_title = label(object_name="HeroTitle", wrap=True)
        left.addWidget(self.hero_title)
        self.hero_subtitle = label(object_name="HeroSubtitle", wrap=True)
        self.hero_subtitle.setMaximumWidth(560)
        left.addWidget(self.hero_subtitle)
        left.addSpacing(12)
        actions = QHBoxLayout()
        actions.setSpacing(9)
        self.pair_button = make_button("", kind="HeroButton", icon_name="plus", icon_color="#2C2371")
        self.secondary_button = make_button("", kind="HeroGhostButton")
        actions.addWidget(self.pair_button)
        actions.addWidget(self.secondary_button)
        actions.addStretch(1)
        left.addLayout(actions)
        left.addStretch(1)
        hero_layout.addLayout(left, 1)

        self.glass = QFrame()
        self.glass.setFixedWidth(252)
        self.glass.setObjectName("HeroGlass")
        self.glass.setStyleSheet(
            "QFrame#HeroGlass { background: rgba(255,255,255,.095);"
            " border: 1px solid rgba(255,255,255,.15); border-radius: 16px; }"
        )
        glass_layout = QVBoxLayout(self.glass)
        glass_layout.setContentsMargins(18, 16, 18, 16)
        glass_layout.setSpacing(10)
        glass_top = QHBoxLayout()
        self.glass_dot = Dot("#57E4C1", 8)
        glass_top.addWidget(self.glass_dot)
        self.glass_state = label(object_name="HeroStatusTitle")
        glass_top.addWidget(self.glass_state, 1)
        glass_layout.addLayout(glass_top)
        self.glass_detail = label(object_name="HeroStatusSub", wrap=True)
        glass_layout.addWidget(self.glass_detail)
        glass_layout.addStretch(1)
        glass_layout.addWidget(hline())
        self.glass_meta = label(object_name="HeroStatusSub", wrap=True)
        glass_layout.addWidget(self.glass_meta)
        hero_layout.addWidget(self.glass, 0)
        self.layout.addWidget(self.hero)

        # Trio of status cards
        cards = QHBoxLayout()
        cards.setSpacing(14)
        (
            self.local_card,
            self.local_title,
            self.local_value,
            self.local_meta,
            self.local_copy,
        ) = self._info_card("monitor", copyable=True)
        self.local_copy.setToolTip("")
        cards.addWidget(self.local_card, 1)

        self.security_card, self.security_title, self.security_value, self.security_meta, _ = (
            self._info_card("shield")
        )
        cards.addWidget(self.security_card, 1)

        self.clipboard_card, self.clipboard_title, self.clipboard_value, self.clipboard_meta, _ = (
            self._info_card("clipboard")
        )
        cards.addWidget(self.clipboard_card, 1)
        self.layout.addLayout(cards)

        # Active peer panel
        self.peer_panel = QFrame()
        self.peer_panel.setObjectName("Card")
        peer_layout = QVBoxLayout(self.peer_panel)
        peer_layout.setContentsMargins(20, 16, 20, 16)
        peer_layout.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.focus_icon = QLabel()
        self.focus_icon.setPixmap(pixmap("cursor", ACCENT, 18))
        top.addWidget(self.focus_icon)
        self.focus_title = label(object_name="SectionTitle")
        top.addWidget(self.focus_title)
        top.addStretch(1)
        self.focus_pill = StatusPill("connected", "")
        top.addWidget(self.focus_pill)
        peer_layout.addLayout(top)
        self.peer_content = QWidget()
        self.peer_content_layout = QVBoxLayout(self.peer_content)
        self.peer_content_layout.setContentsMargins(0, 0, 0, 0)
        self.peer_content_layout.setSpacing(10)
        peer_layout.addWidget(self.peer_content)
        self.focus_hint = label(object_name="Faint", wrap=True)
        peer_layout.addWidget(self.focus_hint)
        peer_layout.addWidget(hline())
        peer_actions = QHBoxLayout()
        peer_actions.addStretch(1)
        self.details_button = make_button("", kind="GhostButton", icon_name="arrow_right")
        self.disconnect_button = make_button("", kind="SecondaryButton", icon_name="power")
        peer_actions.addWidget(self.details_button)
        peer_actions.addWidget(self.disconnect_button)
        peer_layout.addLayout(peer_actions)
        self.layout.addWidget(self.peer_panel)

        # Two lists
        lists = QHBoxLayout()
        lists.setSpacing(14)
        peers_card = Card(margins=(20, 16, 20, 14), spacing=8)
        head = QHBoxLayout()
        self.peers_title = label(object_name="SectionTitle")
        head.addWidget(self.peers_title)
        head.addStretch(1)
        self.peers_see_all = make_button("", kind="LinkButton")
        head.addWidget(self.peers_see_all)
        peers_card.body.addLayout(head)
        self.peers_container = QVBoxLayout()
        self.peers_container.setSpacing(6)
        peers_card.body.addLayout(self.peers_container)
        lists.addWidget(peers_card, 1)

        nearby_card = Card(margins=(20, 16, 20, 14), spacing=8)
        near_head = QHBoxLayout()
        self.nearby_title = label(object_name="SectionTitle")
        near_head.addWidget(self.nearby_title)
        near_head.addStretch(1)
        self.nearby_see_all = make_button("", kind="LinkButton")
        near_head.addWidget(self.nearby_see_all)
        nearby_card.body.addLayout(near_head)
        self.nearby_container = QVBoxLayout()
        self.nearby_container.setSpacing(6)
        nearby_card.body.addLayout(self.nearby_container)
        lists.addWidget(nearby_card, 1)
        self.layout.addLayout(lists)

        # Feature strip
        features = QHBoxLayout()
        features.setSpacing(14)
        for icon_name, title_key, desc_key, color_value in (
            ("lock", "home_feature_secure_title", "home_feature_secure_desc", ACCENT),
            ("pulse", "home_feature_fast_title", "home_feature_fast_desc", MINT),
            ("switch", "home_feature_smart_title", "home_feature_smart_desc", AMBER),
        ):
            card = Card(margins=(16, 14, 16, 14), spacing=7)
            tile = IconTile(icon_name, color_name=color_value, size=32, icon_size=16)
            card.body.addWidget(tile)
            title = label(object_name="SectionTitle")
            desc = label(object_name="SectionCaption", wrap=True)
            card.body.addWidget(title)
            card.body.addWidget(desc)
            setattr(self, f"feature_{title_key}", title)
            setattr(self, f"feature_{desc_key}", desc)
            features.addWidget(card, 1)
        self.layout.addLayout(features)
        self.layout.addStretch(1)

    def _info_card(self, icon_name: str, *, copyable: bool = False):
        card = Card(margins=(16, 15, 16, 15), spacing=8)
        row = QHBoxLayout()
        row.setSpacing(9)
        tile = IconTile(icon_name, color_name=ACCENT, size=32, icon_size=16)
        row.addWidget(tile)
        title = label(object_name="SectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        copy = None
        if copyable:
            copy = icon_button("copy", size=15, parent=card)
            row.addWidget(copy)
        card.body.addLayout(row)
        primary = label("—", object_name="InfoPrimary", wrap=True)
        primary.setStyleSheet("font-size: 13px; font-weight: 600;")
        secondary = label("", object_name="InfoSecondary", wrap=True)
        secondary.setStyleSheet("font-size: 10.5px; color: #8D95AB;")
        card.body.addWidget(primary)
        card.body.addWidget(secondary)
        return card, title, primary, secondary, copy

    def _wire(self) -> None:
        self.pair_button.clicked.connect(self._primary_action)
        self.secondary_button.clicked.connect(self.connect_requested.emit)
        self.peers_see_all.clicked.connect(self.devices_requested.emit)
        self.nearby_see_all.clicked.connect(self.connect_requested.emit)
        self.details_button.clicked.connect(self.devices_requested.emit)
        self.disconnect_button.clicked.connect(self.disconnect_requested.emit)
        if self.local_copy is not None:
            self.local_copy.clicked.connect(self._copy_fingerprint)
        self._local_fingerprint = ""

    def _primary_action(self) -> None:
        if self._snapshot.status == "connected":
            self.disconnect_requested.emit()
        else:
            self.pair_requested.emit()

    def _copy_fingerprint(self) -> None:
        if copy_to_clipboard(self._local_fingerprint):
            self.toast.emit(self.controller.translator.t("copied"), "success")

    def retranslate(self) -> None:
        t = self.controller.translator.t
        name = self.controller.settings.device_name or (self._snapshot.local.name if self._snapshot.local else "ToRexLink")
        self.header.title.setText(f"{t('home_greeting')}, {name.split(' ')[0]}")
        self.header.subtitle.setText(t("home_header_subtitle"))
        self.eyebrow.setText(t("home_hero_eyebrow"))
        self.hero_title.setText(t("home_title"))
        self.hero_subtitle.setText(t("home_subtitle"))
        self.pair_button.setText(t("home_pair_cta"))
        self.secondary_button.setText(t("home_connect_cta"))
        self.peers_see_all.setText(t("home_see_all"))
        self.nearby_see_all.setText(t("home_see_all"))
        self.focus_title.setText(t("home_focus_now"))
        self.disconnect_button.setText(t("home_disconnect_cta"))
        self.details_button.setText(t("home_see_all"))
        self.local_title.setText(t("home_local_title"))
        self.security_title.setText(t("home_security_title"))
        self.clipboard_title.setText(t("home_clipboard_title"))
        self.security_meta.setText(t("home_security_desc"))
        self.feature_home_feature_secure_title.setText(t("home_feature_secure_title"))
        self.feature_home_feature_secure_desc.setText(t("home_feature_secure_desc"))
        self.feature_home_feature_fast_title.setText(t("home_feature_fast_title"))
        self.feature_home_feature_fast_desc.setText(t("home_feature_fast_desc"))
        self.feature_home_feature_smart_title.setText(t("home_feature_smart_title"))
        self.feature_home_feature_smart_desc.setText(t("home_feature_smart_desc"))
        self.peers_title.setText(t("home_peers_title"))
        self.nearby_title.setText(t("home_discovered_title"))
        self.refresh(self._snapshot)

    # ------------------------------------------------------------------ refresh
    def refresh(self, snapshot: ServiceSnapshot) -> None:
        self._snapshot = snapshot
        t = self.controller.translator.t
        status_text = {
            "offline": t("status_offline"),
            "hosting": t("status_hosting"),
            "pairing": t("status_pairing"),
            "connecting": t("status_connecting"),
            "connected": t("status_connected"),
            "reconnecting": t("status_reconnecting"),
            "error": t("status_error"),
        }.get(snapshot.status, t("status_offline"))
        self.header_status.set_status(snapshot.status, status_text)
        self.refresh_button.setToolTip(t("refresh"))
        connection = snapshot.connection
        peer_name = connection.name if connection else ""
        if snapshot.status == "connected" and connection:
            self.glass_state.setText(t("home_hero_connected").format(peer=peer_name))
            self.glass_detail.setText(t("home_peer_latency").format(ms=connection.latency_ms))
            self.glass_dot.set_color("#57E4C1", pulsing=True)
            self.glass_meta.setText(f"{connection.address}:{connection.port} · TLS 1.3")
            self.pair_button.setText(t("home_disconnect_cta"))
            self.secondary_button.setText(t("home_see_all"))
            self.pair_button.show()
            self.secondary_button.show()
        else:
            hero_map = {
                "pairing": "home_hero_pairing",
                "connecting": "home_hero_connecting",
                "reconnecting": "home_hero_connecting",
                "error": "home_hero_error",
                "hosting": "home_hero_ready",
                "offline": "home_hero_ready",
            }
            self.glass_state.setText(t(hero_map.get(snapshot.status, "home_hero_ready")))
            self.glass_detail.setText(t(f"status_{snapshot.status}_desc", peer=peer_name or t("this_computer")))
            self.glass_dot.set_color({"pairing": ACCENT, "error": DANGER, "connecting": AMBER}.get(snapshot.status, MINT), pulsing=snapshot.status in {"pairing", "connecting", "reconnecting"})
            if snapshot.local and snapshot.local.addresses:
                self.glass_meta.setText(f"{snapshot.local.addresses[0]} · {t('tls_13')}")
            else:
                self.glass_meta.setText(t("home_hero_eyebrow"))
            self.pair_button.setText(t("home_pair_cta"))
            self.secondary_button.setText(t("home_connect_cta"))
        from ..icons import icon as svg_icon

        self.pair_button.setIcon(svg_icon("power" if snapshot.status == "connected" else "plus", "#2C2371", 16))
        self.secondary_button.setIcon(svg_icon("devices", "#FFFFFF", 16))

        local = snapshot.local
        if local:
            self.local_value.setText(local.name)
            self.local_meta.setText(
                f"{local.addresses[0] if local.addresses else t('unknown')}  ·  {local.fingerprint[:19]}…"
            )
            self._local_fingerprint = local.fingerprint
            if self.local_copy is not None:
                self.local_copy.setToolTip(t("copy_fingerprint"))
        else:
            self.local_value.setText(t("loading"))
            self.local_meta.setText("")
        self.security_value.setText(t("tls_13") + "  ·  SHA-256")
        self.security_meta.setText(t("home_security_desc"))
        clipboard_on = snapshot.clipboard_enabled
        self.clipboard_value.setText(t("home_clipboard_on") if clipboard_on else t("home_clipboard_off"))
        self.clipboard_meta.setText(t("clipboard_security_note"))
        self._render_peer_panel(snapshot)
        self._render_peers(snapshot.peers)
        self._render_nearby(snapshot.discovered)

    def _render_peer_panel(self, snapshot: ServiceSnapshot) -> None:
        t = self.controller.translator.t
        while self.peer_content_layout.count():
            item = self.peer_content_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        connection = snapshot.connection
        if connection is None:
            self.focus_title.setText(t("home_focus_now"))
            self.focus_pill.set_status(snapshot.status, {
                "hosting": t("status_hosting"),
                "pairing": t("status_pairing"),
                "offline": t("status_offline"),
                "connecting": t("status_connecting"),
                "reconnecting": t("status_reconnecting"),
                "error": t("status_error"),
            }.get(snapshot.status, t("status_offline")))
            empty = QHBoxLayout()
            icon = QLabel()
            icon.setPixmap(pixmap("link", MUTED, 22))
            empty.addWidget(icon)
            message = label(t("home_peers_empty"), object_name="Muted", wrap=True)
            empty.addWidget(message, 1)
            wrapper = QWidget()
            wrapper.setLayout(empty)
            self.peer_content_layout.addWidget(wrapper)
            self.focus_hint.setText(t("focus_tip").format(hotkey=HOTKEY_LABEL))
            self.disconnect_button.setVisible(False)
            self.details_button.setVisible(True)
            return
        row = QHBoxLayout()
        row.setSpacing(11)
        avatar = make_avatar_for_name(connection.name)
        row.addWidget(avatar)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = label(connection.name, object_name="SectionTitle")
        meta = label(
            t("connection_peer_meta").format(
                platform=t(f"platform_{connection.platform}") if connection.platform in {"macos", "windows"} else connection.platform,
                version=f"v{connection.app_version}",
            ),
            object_name="DeviceMeta",
        )
        texts.addWidget(name)
        texts.addWidget(meta)
        row.addLayout(texts, 1)
        role = Pill(
            t("connection_role_host") if connection.role == "host" else t("connection_role_guest"),
            "muted",
        )
        row.addWidget(role)
        row.addWidget(Pill(f"{connection.latency_ms} ms"))
        wrapper = QWidget()
        wrapper.setLayout(row)
        self.peer_content_layout.addWidget(wrapper)
        focus = snapshot.focus
        if focus:
            if focus.local_target:
                self.focus_pill.set_status("connected", t("home_focus_local"))
                hint = t("focus_tip").format(hotkey=HOTKEY_LABEL)
            elif focus.local_controller:
                self.focus_pill.set_status("pairing", t("home_focus_you_control").format(peer=connection.name))
                hint = t("focus_banner_return_hint").format(hotkey=HOTKEY_LABEL)
            else:
                self.focus_pill.set_status("reconnecting", t("home_focus_remote").format(peer=connection.name))
                hint = t("focus_banner_takeover_hint")
            self.focus_hint.setText(hint)
        self.disconnect_button.setVisible(True)
        self.details_button.setVisible(True)

    def _render_peers(self, peers: tuple[PeerView, ...]) -> None:
        t = self.controller.translator.t
        self._clear(self.peers_container)
        if not peers:
            empty = label(t("home_peers_empty"), object_name="Faint", wrap=True)
            empty.setContentsMargins(0, 8, 0, 8)
            self.peers_container.addWidget(empty)
            return
        for peer in peers[:3]:
            row = QFrame()
            row.setObjectName("DeviceRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(9, 8, 9, 8)
            line.setSpacing(9)
            line.addWidget(make_avatar_for_name(peer.name))
            texts = QVBoxLayout()
            texts.setSpacing(1)
            texts.addWidget(label(peer.name, object_name="DeviceName"))
            texts.addWidget(
                label(
                    t("device_connected_badge") if peer.connected else t("device_offline_badge"),
                    object_name="DeviceMeta",
                )
            )
            line.addLayout(texts, 1)
            dot = Dot(MINT if peer.connected else MUTED, 6)
            line.addWidget(dot)
            connect = icon_button("arrow_right", size=15, parent=row)
            connect.setToolTip(t("device_connect"))
            connect.clicked.connect(lambda _=False, pid=peer.peer_id: self.connect_peer_requested.emit(pid))
            line.addWidget(connect)
            self.peers_container.addWidget(row)

    def _render_nearby(self, devices) -> None:
        t = self.controller.translator.t
        self._clear(self.nearby_container)
        if not devices:
            empty = label(t("home_discovered_empty"), object_name="Faint", wrap=True)
            empty.setContentsMargins(0, 8, 0, 8)
            self.nearby_container.addWidget(empty)
            return
        for device in devices[:3]:
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
            self.nearby_container.addWidget(row)

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()


__all__ = ["HomePage"]
