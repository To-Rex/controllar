"""Application shell: sidebar, pages, tray/menu bar and status routing."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..constants import APP_VERSION
from ..core.state import ServiceEvent, ServiceSnapshot
from ..i18n import UZ
from .controller import ToRexLinkController
from .icons import icon, logo_icon, logo_pixmap
from .pages.connect import ConnectPage
from .pages.devices import DevicesPage
from .pages.diagnostics import DiagnosticsPage
from .pages.home import HomePage
from .pages.logs import LogsPage
from .pages.settings import SettingsPage
from .theme import Palette, build_stylesheet, palette_for, resolve_theme
from .widgets import (
    AMBER,
    DANGER,
    MINT,
    MUTED,
    Banner,
    Dot,
    Toast,
)

log = logging.getLogger(__name__)

SIDEBAR_MUTED = "#8E96AF"
SIDEBAR_ICON_ACTIVE = "#FFFFFF"


class NavButton(QPushButton):
    def __init__(self, icon_name: str, text: str) -> None:
        super().__init__(text)
        self.setObjectName("NavButton")
        self._icon_name = icon_name
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(18, 18))
        self.setMinimumHeight(38)
        self.toggled.connect(self._update_icon)
        self._update_icon(False)

    def _update_icon(self, checked: bool) -> None:
        color_value = SIDEBAR_ICON_ACTIVE if checked else SIDEBAR_MUTED
        self.setIcon(icon(self._icon_name, color_value, 18, 1.8 if checked else 1.6))

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self._update_icon(self.isChecked())


class SidebarPicker(QPushButton):
    def __init__(self, text: str, icon_name: str) -> None:
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(14, 14))
        self.setStyleSheet(
            """
            QPushButton {
                background: rgba(255,255,255,.045);
                color: #AEB6CE;
                border: 1px solid rgba(255,255,255,.07);
                border-radius: 8px;
                padding: 6px 9px;
                font-size: 10.5px;
                font-weight: 600;
            }
            QPushButton:hover { background: rgba(255,255,255,.09); color: #FFFFFF; }
            QPushButton::menu-indicator { image: none; width: 0; }
            """
        )
        self._icon_name = icon_name
        self._update_icon()

    def _update_icon(self, icon_name: str | None = None) -> None:
        if icon_name:
            self._icon_name = icon_name
        self.setIcon(icon(self._icon_name, "#9EA6C0", 14))


class MainWindow(QWidget):
    closing = Signal()

    def __init__(self, controller: ToRexLinkController) -> None:
        super().__init__()
        self.controller = controller
        self._snapshot: ServiceSnapshot = controller.snapshot
        self._previous_snapshot: ServiceSnapshot = controller.snapshot
        self._startup_ok = False
        self._quitting = False
        self._notified_peer: str | None = None
        self._last_focus_signature: tuple | None = None
        self._dirty_pages: set[str] = set()
        self._tray_notice_shown = False
        self._theme_setting = controller.settings.theme
        self._palette: Palette = palette_for(self._theme_setting)
        self.setWindowTitle("ToRexLink")
        self.setWindowIcon(logo_icon())
        self.setMinimumSize(1000, 650)
        self.resize(1240, 820)
        self.setObjectName("AppRoot")
        self._build()
        self._build_tray()
        self._connect_signals()
        self._retranslate()
        self._apply_theme(self._theme_setting)
        self._apply_snapshot(self._snapshot, None)
        self._position_window()
        self._permission_timer = QTimer(self)
        self._permission_timer.setInterval(7000)
        self._permission_timer.timeout.connect(self._check_permissions)
        self._permission_timer.start()
        QTimer.singleShot(600, self._check_permissions)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(248)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(18, 22, 18, 18)
        side.setSpacing(0)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(11)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(38))
        logo.setFixedSize(38, 38)
        brand_row.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        self.brand_title = QLabel("ToRexLink")
        self.brand_title.setObjectName("BrandTitle")
        self.brand_tag = QLabel("")
        self.brand_tag.setObjectName("BrandTagline")
        self.brand_tag.setWordWrap(True)
        self.brand_tag.setMaximumWidth(176)
        brand_text.addWidget(self.brand_title)
        brand_text.addWidget(self.brand_tag)
        brand_row.addLayout(brand_text, 1)
        side.addLayout(brand_row)
        side.addSpacing(28)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, NavButton] = {}
        self.nav_section_labels: dict[str, QLabel] = {}
        pages = [
            ("home", "home", HomePage),
            ("connect", "connect", ConnectPage),
            ("devices", "devices", DevicesPage),
            ("settings", "settings", SettingsPage),
            ("logs", "logs", LogsPage),
            ("diagnostics", "pulse", DiagnosticsPage),
        ]
        self.pages: dict[str, QWidget] = {}
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        for key, icon_name, page_cls in pages:
            if key in {"home", "connect", "devices"}:
                section = "main"
            else:
                section = "system"
            if section not in self.nav_section_labels:
                if section == "system":
                    side.addSpacing(18)
                section_label = QLabel("")
                section_label.setObjectName("NavSection")
                side.addWidget(section_label)
                side.addSpacing(6)
                self.nav_section_labels[section] = section_label
            button = NavButton(icon_name, "")
            button.clicked.connect(lambda _checked=False, page_key=key: self.navigate(page_key))
            self.nav_group.addButton(button)
            side.addWidget(button)
            side.addSpacing(3)
            self.nav_buttons[key] = button
            page = page_cls(self.controller)
            self.pages[key] = page
            self.stack.addWidget(page)

        self.nav_buttons["home"].setChecked(True)
        side.addStretch(1)

        # Sidebar status card
        self.status_card = QFrame()
        self.status_card.setObjectName("SidebarStatus")
        status_layout = QVBoxLayout(self.status_card)
        status_layout.setContentsMargins(12, 11, 12, 11)
        status_layout.setSpacing(5)
        status_head = QHBoxLayout()
        status_head.setSpacing(6)
        self.status_dot = Dot("#19C39C", 7)
        status_head.addWidget(self.status_dot)
        self.status_title = QLabel("")
        self.status_title.setObjectName("SidebarStatusTitle")
        status_head.addWidget(self.status_title, 1)
        status_layout.addLayout(status_head)
        self.status_detail = QLabel("")
        self.status_detail.setObjectName("SidebarStatusDetail")
        self.status_detail.setWordWrap(True)
        status_layout.addWidget(self.status_detail)
        self.focus_sidebar = QLabel("")
        self.focus_sidebar.setObjectName("SidebarStatusDetail")
        self.focus_sidebar.setWordWrap(True)
        self.focus_sidebar.hide()
        status_layout.addWidget(self.focus_sidebar)
        side.addWidget(self.status_card)
        side.addSpacing(11)

        picker_row = QHBoxLayout()
        picker_row.setSpacing(7)
        self.language_picker = SidebarPicker("UZ", "globe")
        self.language_menu = QMenu(self)
        self.language_actions: dict[str, QAction] = {}
        self._language_group = QActionGroup(self)
        self._language_group.setExclusive(True)
        for locale in ("uz", "ru", "en"):
            action = QAction(self.language_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, loc=locale: self.controller.set_language(loc))
            self._language_group.addAction(action)
            self.language_menu.addAction(action)
            self.language_actions[locale] = action
        self.language_picker.clicked.connect(self._show_language_menu)
        picker_row.addWidget(self.language_picker, 1)
        self.theme_picker = SidebarPicker("", "moon")
        self.theme_menu = QMenu(self)
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self.theme_actions: dict[str, QAction] = {}
        for theme in ("system", "light", "dark"):
            action = QAction(self.theme_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=theme: self.controller.set_theme(value))
            self._theme_group.addAction(action)
            self.theme_menu.addAction(action)
            self.theme_actions[theme] = action
        self.theme_picker.clicked.connect(self._show_theme_menu)
        picker_row.addWidget(self.theme_picker, 1)
        side.addLayout(picker_row)
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setObjectName("SidebarCaption")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        side.addSpacing(8)
        side.addWidget(self.version_label)
        root.addWidget(self.sidebar)

        # Content
        content = QWidget()
        content.setObjectName("PageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.startup_banner = Banner("", "", level="danger", icon_name="alert")
        self.startup_banner.hide()
        self.permission_banner = Banner(
            "",
            "",
            level="warning",
            icon_name="key",
            action=self._make_banner_button(),
        )
        self.permission_banner.hide()
        self.focus_banner = Banner("", "", level="warning", icon_name="cursor")
        self.focus_banner.hide()
        for banner in (self.startup_banner, self.permission_banner, self.focus_banner):
            wrapper = QWidget()
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(30, 12, 30, 0)
            wrapper_layout.addWidget(banner)
            if banner is self.startup_banner:
                self.startup_wrapper = wrapper
            elif banner is self.permission_banner:
                self.permission_wrapper = wrapper
            else:
                self.focus_wrapper = wrapper
            layout.addWidget(wrapper)
        layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        self.startup_wrapper.hide()
        self.permission_wrapper.hide()
        self.focus_wrapper.hide()
        self.startup_banner.show()
        self.permission_banner.show()
        self.focus_banner.hide()
        self.toast = Toast(self)
        self.setStyleSheet("")

    def _make_banner_button(self) -> QPushButton:
        button = QPushButton("")
        button.setObjectName("SecondaryButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self._grant_permissions)
        return button

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("System tray is unavailable")
            return
        self.tray = QSystemTrayIcon(logo_icon(), self)
        self.tray.setToolTip("ToRexLink")
        self.tray.activated.connect(self._on_tray_activated)
        self._tray_menu = QMenu()
        self._tray_menu.aboutToShow.connect(self._rebuild_tray_menu)
        self.tray.setContextMenu(self._tray_menu)
        self.tray.show()
        self._rebuild_tray_menu()

    def _connect_signals(self) -> None:
        self.controller.service_event.connect(self._on_service_event)
        self.controller.ready.connect(self._on_ready)
        self.controller.startup_failed.connect(self._on_startup_failed)
        self.controller.operation_failed.connect(self._on_operation_failed)
        self.controller.language_changed.connect(self._on_language_changed)
        self.controller.theme_changed.connect(self._on_theme_changed)
        self.controller.firewall_changed.connect(lambda _status: None)
        for page in self.pages.values():
            if hasattr(page, "toast"):
                page.toast.connect(self.show_toast)
        home = self.pages["home"]
        connect_page = self.pages["connect"]
        devices = self.pages["devices"]
        home.pair_requested.connect(self._start_pairing_flow)
        home.connect_requested.connect(lambda: self.navigate("connect"))
        home.devices_requested.connect(lambda: self.navigate("devices"))
        home.disconnect_requested.connect(self._disconnect)
        home.connect_peer_requested.connect(self._connect_peer)
        connect_page.navigate_devices.connect(lambda: self.navigate("devices"))
        devices.navigate_connect.connect(lambda: self.navigate("connect"))
        hints = QGuiApplication.styleHints()
        try:
            hints.colorSchemeChanged.connect(self._system_theme_changed)
        except Exception:  # noqa: BLE001
            pass

    def _position_window(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        width = min(1240, max(self.minimumWidth(), geometry.width() - 56))
        height = min(820, max(self.minimumHeight(), geometry.height() - 56))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(geometry.center())
        self.move(frame.topLeft())

    # ------------------------------------------------------------------ theme
    def _apply_theme(self, theme: str) -> None:
        self._theme_setting = theme
        self._palette = palette_for(theme)
        application = QApplication.instance()
        if application is not None:
            application.setStyleSheet(build_stylesheet(theme))
        if hasattr(self.pages, "get"):
            settings_page = self.pages.get("settings")
            if settings_page is not None:
                settings_page.apply_palette(self._palette)
        self.theme_picker._update_icon("sun" if resolve_theme(theme) == "light" else "moon")
        for value, action in self.theme_actions.items():
            action.setChecked(value == theme)
        self.update()

    def _on_theme_changed(self, theme: str) -> None:
        self._theme_setting = theme
        self._apply_theme(theme)

    def _system_theme_changed(self, _scheme) -> None:
        if self._theme_setting == "system":
            self._apply_theme("system")

    # ------------------------------------------------------------- translation
    def _retranslate(self) -> None:
        t = self.controller.translator.t
        self.setWindowTitle(t("app_name"))
        self.brand_tag.setText(t("app_tagline"))
        self.nav_section_labels["main"].setText(t("nav_main"))
        self.nav_section_labels["system"].setText(t("nav_system"))
        for key, button in self.nav_buttons.items():
            button.setText(t(f"nav_{key}"))
        self.language_picker.setText(self.controller.translator.locale.upper())
        self.language_picker.setToolTip(t("language"))
        self.theme_picker.setToolTip(t("theme"))
        for locale, action in self.language_actions.items():
            action.setText({"uz": "O‘zbekcha", "ru": "Русский", "en": "English"}[locale])
            action.setChecked(locale == self.controller.translator.locale)
        for theme, action in self.theme_actions.items():
            action.setText(t(f"theme_{theme}"))
            action.setChecked(theme == self.controller.settings.theme)
        self.permission_banner.title.setText(t("permissions_missing"))
        self.permission_banner.text.setText(t("permissions_restart_hint"))
        self.permission_banner.action.setText(t("permissions_open"))
        for page in self.pages.values():
            if hasattr(page, "retranslate"):
                try:
                    page.retranslate()
                except Exception:  # noqa: BLE001
                    log.debug("Page retranslation failed", exc_info=True)
        self._update_focus_banner()
        self._update_sidebar()
        self._rebuild_tray_menu()

    def _on_language_changed(self, _locale: str) -> None:
        self._retranslate()

    # ------------------------------------------------------------------ pages
    def navigate(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        button = self.nav_buttons.get(key)
        if button is not None:
            button.setChecked(True)
        self._refresh_page(key, page, self._snapshot)
        self._dirty_pages.discard(key)

    def _start_pairing_flow(self) -> None:
        self.show_and_raise()
        self.navigate("connect")
        connect_page: ConnectPage = self.pages["connect"]  # type: ignore[assignment]
        QTimer.singleShot(80, connect_page.start_pairing)

    def _disconnect(self) -> None:
        self.controller.disconnect()

    def _connect_peer(self, peer_id: str) -> None:
        self.controller.connect_peer(
            peer_id,
            on_error=lambda err: self.show_toast(self.controller.error_message(err), "error"),
        )

    # ------------------------------------------------------------------- state
    def _on_service_event(self, event: ServiceEvent) -> None:
        previous = self._snapshot
        self._snapshot = event.snapshot
        self._apply_snapshot(event.snapshot, event)
        self._notify_event(event, previous)

    def _apply_snapshot(self, snapshot: ServiceSnapshot, event: ServiceEvent | None) -> None:
        self._snapshot = snapshot
        current = self.stack.currentWidget()
        for key, page in self.pages.items():
            if page is current and self._should_refresh_page(key, event):
                self._refresh_page(key, page, snapshot)
                self._dirty_pages.discard(key)
            elif key != "logs":
                # Hidden pages are refreshed lazily when opened; rebuilding
                # every page on each focus/discovery event was both wasteful
                # and a visible source of UI stutter.
                self._dirty_pages.add(key)
        self._update_sidebar()
        self._update_focus_banner()
        self._update_window_title()
        self._update_tray_tooltip()
        self._previous_snapshot = snapshot

    def _should_refresh_page(self, key: str, event: ServiceEvent | None) -> bool:
        if event is None:
            return True
        if key == "logs":
            return False
        if event.kind == "focus":
            return key == "home"
        if event.kind in {"clipboard_started", "clipboard_synced", "clipboard_skipped"}:
            return key == "home"
        if event.kind == "discovery":
            return key in {"home", "connect", "devices"}
        return True

    def _refresh_page(self, key: str, page: object, snapshot: ServiceSnapshot) -> None:
        try:
            if key == "logs":
                page.refresh()  # type: ignore[attr-defined]
            elif hasattr(page, "refresh"):
                page.refresh(snapshot)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            log.debug("Page refresh failed for %s", key, exc_info=True)

    def _update_sidebar(self) -> None:
        t = self.controller.translator.t
        snapshot = self._snapshot
        status_map = {
            "offline": ("status_offline", t("status_offline"), MUTED),
            "hosting": ("hosting", t("status_hosting"), "#8B7CFF"),
            "pairing": ("pairing", t("status_pairing"), "#8B7CFF"),
            "connecting": ("connecting", t("status_connecting"), AMBER),
            "connected": ("connected", t("status_connected"), MINT),
            "reconnecting": ("reconnecting", t("status_reconnecting"), AMBER),
            "error": ("error", t("status_error"), DANGER),
        }
        status_key, title, color_value = status_map.get(snapshot.status, status_map["offline"])
        self.status_title.setText(title)
        self.status_dot.set_color(color_value, pulsing=status_key in {"pairing", "connecting", "reconnecting"})
        if snapshot.connection:
            self.status_detail.setText(snapshot.connection.name)
        elif snapshot.status == "pairing":
            self.status_detail.setText(t("pairing_waiting"))
        elif snapshot.status == "hosting":
            self.status_detail.setText(t("status_hosting_desc"))
        elif snapshot.status == "reconnecting":
            detail = t("status_reconnecting_desc")
            if snapshot.reconnect_attempt:
                detail = f"{detail} · #{snapshot.reconnect_attempt}"
            self.status_detail.setText(detail)
        elif snapshot.status == "error":
            self.status_detail.setText(t("status_error_desc"))
        else:
            self.status_detail.setText(t("status_offline_desc"))
        focus = snapshot.focus
        if focus and snapshot.connection:
            if focus.local_controller and focus.local_target:
                text = t("focus_banner_local")
            elif focus.local_controller:
                text = t("home_focus_you_control").format(peer=focus.target_name)
            elif focus.local_target:
                text = t("home_focus_remote").format(peer=focus.controller_name)
            else:
                text = t("focus_banner_other")
            self.focus_sidebar.setText(text)
            self.focus_sidebar.show()
        else:
            self.focus_sidebar.hide()

    def _update_focus_banner(self) -> None:
        # Focus changes are intentionally silent: the service is meant to feel
        # like one computer and must not pop notifications when the cursor
        # crosses to the other screen. The sidebar/tray still show the state.
        self.focus_banner.hide()
        self.focus_wrapper.hide()
        focus = self._snapshot.focus
        connection = self._snapshot.connection
        self._last_focus_signature = (
            (focus.local_controller, focus.local_target, connection.peer_id)
            if focus is not None and connection is not None
            else None
        )

    def _update_window_title(self) -> None:
        if self._snapshot.connection:
            self.setWindowTitle(f"ToRexLink — {self._snapshot.connection.name}")
        else:
            self.setWindowTitle("ToRexLink")

    def _notify_event(self, event: ServiceEvent, previous: ServiceSnapshot) -> None:
        t = self.controller.translator.t
        kind = event.kind
        snapshot = event.snapshot
        visible = self.isVisible() and not self.isMinimized()
        if kind == "connected" and snapshot.connection:
            peer = snapshot.connection.name
            if visible:
                self.show_toast(
                    t("status_connected_desc").format(peer=peer),
                    "success",
                )
            self._send_notification(
                t("notify_connected_title"),
                t("notify_connected_body").format(peer=peer),
            )
            self._notified_peer = peer
        elif kind == "peer_paired":
            peer = str(event.data.get("peer_name") or t("this_computer"))
            if visible:
                self.show_toast(t("toast_peer_paired").format(peer=peer), "success")
            self._send_notification(
                t("notify_pairing_title"),
                t("notify_pairing_body").format(peer=peer),
            )
        elif kind == "disconnected" and previous.connection is not None:
            peer = str(event.data.get("peer_name") or previous.connection.name)
            reason = str(event.data.get("reason") or "")
            if reason not in {"shutdown", "user"} and not self._quitting:
                if visible:
                    self.show_toast(
                        t("notify_disconnected_body").format(peer=peer),
                        "info",
                    )
                self._send_notification(
                    t("notify_disconnected_title"),
                    t("notify_disconnected_body").format(peer=peer),
                )
            self._notified_peer = None
        elif kind == "pairing_expired":
            if visible:
                self.show_toast(t("pairing_expired"), "info")
        elif kind == "input_unavailable":
            if visible:
                self.show_toast(t("toast_input_unavailable"), "error")
            self._check_permissions()
        elif kind == "clipboard_skipped":
            if visible and event.data.get("reason") == "too_large":
                self.show_toast(t("toast_clipboard_too_large"), "info")
        elif kind == "error" and snapshot.last_error and visible:
            # Avoid duplicate generic errors during normal disconnect/reconnect.
            if not snapshot.connection and snapshot.status == "error":
                self.show_toast(t("status_error_desc"), "error")

    def _send_notification(self, title: str, body: str) -> None:
        if self.tray is None or not self.controller.settings.notifications:
            return
        try:
            self.tray.showMessage(title, body, logo_icon(), 5000)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ tray
    def _update_tray_tooltip(self) -> None:
        if self.tray is None:
            return
        t = self.controller.translator.t
        snapshot = self._snapshot
        status_text = (
            t("tray_status_connected").format(peer=snapshot.connection.name)
            if snapshot.connection
            else t("tray_status_idle")
        )
        focus = snapshot.focus
        focus_text = ""
        if focus and snapshot.connection:
            if focus.local_controller and not focus.local_target:
                focus_text = t("tray_focus_local")
            elif focus.local_target and not focus.local_controller:
                focus_text = t("tray_focus_remote").format(peer=focus.controller_name)
            elif not focus.local_controller and not focus.local_target:
                focus_text = t("focus_banner_other")
            elif focus.local_controller and focus.local_target:
                focus_text = t("tray_focus_local")
        tooltip_status = status_text if not focus_text else f"{status_text} · {focus_text}"
        self.tray.setToolTip(t("tray_tooltip").format(status=tooltip_status))

    def _rebuild_tray_menu(self) -> None:
        if self.tray is None:
            return
        t = self.controller.translator.t
        menu = self._tray_menu
        menu.clear()
        snapshot = self._snapshot
        status_text = (
            t("tray_status_connected").format(peer=snapshot.connection.name)
            if snapshot.connection
            else t("tray_status_idle")
        )
        status_action = menu.addAction(status_text)
        status_action.setEnabled(False)
        focus = snapshot.focus
        focus_text = ""
        if focus and snapshot.connection:
            if focus.local_controller and not focus.local_target:
                focus_text = t("tray_focus_local")
            elif focus.local_target and not focus.local_controller:
                focus_text = t("tray_focus_remote").format(peer=focus.controller_name)
            elif not focus.local_controller and not focus.local_target:
                focus_text = t("focus_banner_other")
            elif focus.local_controller and focus.local_target:
                focus_text = t("tray_focus_local")
        if focus_text:
            focus_action = menu.addAction(focus_text)
            focus_action.setEnabled(False)
        menu.addSeparator()
        show_action = menu.addAction(t("tray_hide") if self.isVisible() else t("tray_show"))
        show_action.triggered.connect(self._toggle_window)
        pair_action = menu.addAction(t("tray_pair"))
        pair_action.triggered.connect(self._start_pairing_flow)
        pair_action.setEnabled(snapshot.connection is None)
        devices_action = menu.addAction(t("tray_devices"))
        devices_action.triggered.connect(self._tray_open_devices)
        if snapshot.connection:
            disconnect_action = menu.addAction(t("tray_disconnect"))
            disconnect_action.triggered.connect(self._disconnect)
        menu.addSeparator()
        quit_action = menu.addAction(t("tray_quit"))
        quit_action.triggered.connect(self.quit_application)
        tooltip_status = status_text if not focus_text else f"{status_text} · {focus_text}"
        self.tray.setToolTip(t("tray_tooltip").format(status=tooltip_status))
        self.tray.setContextMenu(menu)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_window()

    def _toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
            self._set_activation_policy(accessory=True)
        else:
            self.show_and_raise()

    def _tray_open_devices(self) -> None:
        self.show_and_raise()
        self.navigate("devices")

    # -------------------------------------------------------------- lifecycle
    def _on_ready(self) -> None:
        self._startup_ok = True
        self.startup_wrapper.hide()
        self._check_permissions()
        self._apply_snapshot(self.controller.snapshot, None)

    def _on_startup_failed(self, message: str) -> None:
        t = self.controller.translator.t
        self.startup_banner.title.setText(t("status_error"))
        self.startup_banner.text.setText(t("runtime_start_failed").format(detail=message))
        self.startup_banner.show()
        self.startup_wrapper.show()

    def _on_operation_failed(self, code: str, detail: str) -> None:
        self.show_toast(self.controller.error_message(_ServiceErrorProxy(code, detail)), "error")

    def _check_permissions(self) -> None:
        try:
            info = self.controller.refresh_permissions()
        except Exception:  # noqa: BLE001
            return
        if not info.ready and not self._quitting:
            self.permission_banner.show()
            self.permission_wrapper.show()
        else:
            self.permission_wrapper.hide()

    def _grant_permissions(self) -> None:
        self.controller.request_permissions()
        self._check_permissions()

    def _show_language_menu(self) -> None:
        self.language_menu.exec(self.language_picker.mapToGlobal(self.language_picker.rect().topLeft()))

    def _show_theme_menu(self) -> None:
        self.theme_menu.exec(self.theme_picker.mapToGlobal(self.theme_picker.rect().topLeft()))

    # ------------------------------------------------------------------- toast
    def show_toast(self, text: str, level: str = "success") -> None:
        if text in UZ:
            text = self.controller.translator.t(text)
        self.toast.show_message(text, level=level)
        self.toast.parent_resized()

    # ------------------------------------------------------------------ window
    def show_and_raise(self) -> None:
        self._set_activation_policy(accessory=False)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._rebuild_tray_menu()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting:
            event.accept()
            return
        if self.controller.settings.background_mode and self.tray is not None:
            event.ignore()
            self.hide()
            self._set_activation_policy(accessory=True)
            self._rebuild_tray_menu()
            if not self._tray_notice_shown and self.controller.settings.notifications:
                self._tray_notice_shown = True
                self.tray.showMessage(
                    "ToRexLink",
                    self.controller.translator.t("tray_tooltip").format(
                        status=self.controller.translator.t("tray_status_idle")
                    ),
                    logo_icon(),
                    4000,
                )
            return
        event.accept()
        self.quit_application()

    def quit_application(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._permission_timer.stop()
        if self.tray is not None:
            self.tray.hide()
        self._set_activation_policy(accessory=False)
        QApplication.quit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "toast"):
            self.toast.parent_resized()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self._rebuild_tray_menu()

    def _set_activation_policy(self, *, accessory: bool) -> None:
        if sys.platform != "darwin":
            return
        try:
            from AppKit import (
                NSApp,
                NSApplicationActivationPolicyAccessory,
                NSApplicationActivationPolicyRegular,
            )

            application = NSApp()
            if application is None:
                return
            application.setActivationPolicy_(
                NSApplicationActivationPolicyAccessory if accessory else NSApplicationActivationPolicyRegular
            )
        except Exception:  # noqa: BLE001
            pass

    def prepare_background_launch(self) -> None:
        self._set_activation_policy(accessory=True)


class _ServiceErrorProxy(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


__all__ = ["MainWindow", "NavButton"]
