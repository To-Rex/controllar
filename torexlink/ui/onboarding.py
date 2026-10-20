"""First-run onboarding: naming, permissions and readiness."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..constants import APP_VERSION
from .icons import logo_pixmap, pixmap
from .widgets import (
    ACCENT,
    MINT,
    MUTED,
    Card,
    Pill,
    SegmentedControl,
    label,
    make_button,
)

if TYPE_CHECKING:
    from .controller import ToRexLinkController


class OnboardingDialog(QDialog):
    def __init__(self, controller: ToRexLinkController) -> None:
        super().__init__(None)
        self.controller = controller
        self._step = 0
        self._login_requested = False
        self.setWindowTitle("ToRexLink")
        self.setModal(True)
        self.setMinimumSize(860, 600)
        self.resize(880, 620)
        self._build()
        self._retranslate()
        self._update_permissions()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Premium brand panel.
        self.brand = QFrame()
        self.brand.setObjectName("OnboardingBrand")
        self.brand.setStyleSheet(
            "QFrame#OnboardingBrand { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #171B36, stop:0.6 #262061, stop:1 #5546D6); }"
        )
        self.brand.setFixedWidth(300)
        brand_layout = QVBoxLayout(self.brand)
        brand_layout.setContentsMargins(30, 32, 30, 28)
        brand_layout.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(58))
        brand_layout.addWidget(logo)
        self.brand_title = QLabel("ToRexLink")
        self.brand_title.setStyleSheet("color: white; font-size: 25px; font-weight: 750; letter-spacing: -.4px;")
        brand_layout.addWidget(self.brand_title)
        self.brand_tag = QLabel()
        self.brand_tag.setWordWrap(True)
        self.brand_tag.setStyleSheet("color: rgba(238,239,255,.72); font-size: 12px;")
        brand_layout.addWidget(self.brand_tag)
        brand_layout.addSpacing(28)
        for icon_name in ("lock", "sparkles", "switch"):
            row = QHBoxLayout()
            row.setSpacing(10)
            icon_label = QLabel()
            icon_label.setPixmap(pixmap(icon_name, "#9BEADB" if icon_name == "sparkles" else "#BDB7FF", 17))
            icon_label.setFixedWidth(20)
            row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
            text = QLabel()
            text.setWordWrap(True)
            text.setStyleSheet("color: rgba(242,243,255,.82); font-size: 11px;")
            row.addWidget(text, 1)
            if icon_name == "lock":
                self.feature_local = text
            elif icon_name == "sparkles":
                self.feature_simple = text
            else:
                self.feature_secure = text
            brand_layout.addLayout(row)
        brand_layout.addStretch(1)
        version = QLabel(f"v{APP_VERSION}  ·  TLS 1.3")
        version.setStyleSheet("color: rgba(220,222,245,.45); font-size: 10px;")
        brand_layout.addWidget(version)
        root.addWidget(self.brand)

        # Right content.
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(38, 30, 38, 24)
        content_layout.setSpacing(18)
        top = QHBoxLayout()
        self.step_label = label(object_name="Overline")
        top.addWidget(self.step_label)
        top.addStretch(1)
        self.language = SegmentedControl(
            [("uz", "UZ"), ("ru", "RU"), ("en", "EN")],
            self.controller.translator.locale,
        )
        self.language.setFixedWidth(168)
        self.language.changed.connect(self._change_language)
        top.addWidget(self.language)
        content_layout.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_permissions())
        self.stack.addWidget(self._build_ready())
        content_layout.addWidget(self.stack, 1)

        self.step_dots = QLabel()
        self.step_dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_dots.setStyleSheet("color: #7C84A0; font-size: 10px; letter-spacing: 4px;")
        content_layout.addWidget(self.step_dots)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.skip_button = make_button("", kind="GhostButton")
        self.skip_button.clicked.connect(self._skip)
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        self.back_button = make_button("", kind="SecondaryButton")
        self.back_button.clicked.connect(self._back)
        self.next_button = make_button("", kind="PrimaryButton", icon_name="arrow_right")
        self.next_button.clicked.connect(self._next)
        footer.addWidget(self.back_button)
        footer.addWidget(self.next_button)
        content_layout.addLayout(footer)
        root.addWidget(self.content, 1)

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(10)
        self.welcome_title = label(object_name="PageTitle", wrap=True)
        self.welcome_desc = label(object_name="PageSubtitle", wrap=True)
        layout.addWidget(self.welcome_title)
        layout.addWidget(self.welcome_desc)
        layout.addSpacing(18)
        name_card = Card(margins=(18, 16, 18, 16), spacing=8)
        self.name_label = label(object_name="SectionTitle")
        self.name_desc = label(object_name="SectionCaption", wrap=True)
        self.name_input = QLineEdit()
        self.name_input.setText(self.controller.settings.device_name)
        self.name_input.setMaxLength(64)
        self.name_input.setPlaceholderText(self.controller.translator.t("onboard_name_label"))
        self.name_input.textChanged.connect(self._on_name_changed)
        name_card.body.addWidget(self.name_label)
        name_card.body.addWidget(self.name_desc)
        name_card.body.addWidget(self.name_input)
        layout.addWidget(name_card)
        layout.addStretch(1)
        self.welcome_features = QFrame()
        features = QHBoxLayout(self.welcome_features)
        features.setContentsMargins(0, 0, 0, 0)
        features.setSpacing(10)
        for icon_name, title_attr, desc_attr in (
            ("lock", "feature_local_title", "feature_local_desc"),
            ("qr", "feature_simple_title", "feature_simple_desc"),
        ):
            card = Card(margins=(14, 13, 14, 13), spacing=6)
            icon_label = QLabel()
            icon_label.setPixmap(pixmap(icon_name, ACCENT, 17))
            card.body.addWidget(icon_label)
            title = label(object_name="SectionTitle")
            desc = label(object_name="SectionCaption", wrap=True)
            card.body.addWidget(title)
            card.body.addWidget(desc)
            setattr(self, title_attr, title)
            setattr(self, desc_attr, desc)
            features.addWidget(card, 1)
        layout.addWidget(self.welcome_features)
        return page

    def _build_permissions(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(10)
        self.permissions_title = label(object_name="PageTitle", wrap=True)
        self.permissions_desc = label(object_name="PageSubtitle", wrap=True)
        layout.addWidget(self.permissions_title)
        layout.addWidget(self.permissions_desc)
        layout.addSpacing(12)
        self.permissions_card = Card(margins=(18, 16, 18, 16), spacing=12)
        self.capture_row = self._permission_row("keyboard", "onboard_permission_capture")
        self.inject_row = self._permission_row("cursor", "onboard_permission_control")
        self.permissions_card.body.addWidget(self.capture_row[0])
        self.permissions_card.body.addWidget(self.inject_row[0])
        self.windows_note = Card(margins=(18, 16, 18, 16), spacing=6)
        self.windows_note_title = label(object_name="SectionTitle")
        self.windows_note_desc = label(object_name="SectionCaption", wrap=True)
        self.windows_note.body.addWidget(self.windows_note_title)
        self.windows_note.body.addWidget(self.windows_note_desc)
        layout.addWidget(self.permissions_card)
        layout.addWidget(self.windows_note)
        self.permission_hint = label(object_name="Faint", wrap=True)
        layout.addWidget(self.permission_hint)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.allow_button = make_button("", kind="SecondaryButton", icon_name="key")
        self.allow_button.clicked.connect(self._request_permissions)
        self.settings_button = make_button("", kind="GhostButton", icon_name="settings")
        self.settings_button.clicked.connect(lambda: self.controller.open_permission_settings("all"))
        buttons.addWidget(self.allow_button)
        buttons.addWidget(self.settings_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _permission_row(self, icon_name: str, key: str) -> tuple[QWidget, QLabel]:
        frame = QFrame()
        frame.setObjectName("InnerCard")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 11, 12, 11)
        row.setSpacing(10)
        tile = QLabel()
        tile.setFixedSize(34, 34)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(pixmap(icon_name, ACCENT, 17))
        tile.setStyleSheet("background: rgba(113,97,255,.10); border-radius: 9px;")
        row.addWidget(tile)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = label(object_name="SectionTitle")
        desc = label(object_name="SectionCaption", wrap=True)
        texts.addWidget(title)
        texts.addWidget(desc)
        row.addLayout(texts, 1)
        status = Pill("")
        row.addWidget(status)
        setattr(self, f"{key}_title", title)
        setattr(self, f"{key}_desc", desc)
        return frame, status

    def _build_ready(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 22, 0, 0)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(pixmap("check_circle", MINT, 48))
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.ready_title = label(object_name="PageTitle", alignment=Qt.AlignmentFlag.AlignHCenter)
        self.ready_desc = label(
            object_name="PageSubtitle",
            wrap=True,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        self.ready_desc.setFixedWidth(420)
        self.ready_desc.setMinimumHeight(40)
        layout.addWidget(self.ready_title)
        layout.addWidget(self.ready_desc, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(16)
        summary = Card(margins=(18, 14, 18, 14), spacing=10)
        self.summary_device = self._summary_line(summary.body, "monitor", "this_computer")
        self.summary_security = self._summary_line(summary.body, "shield", "security_tls")
        self.summary_hotkey = self._summary_line(summary.body, "switch", "setting_hotkey")
        layout.addWidget(summary)
        self.ready_hint = label(
            object_name="Faint",
            wrap=True,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        layout.addWidget(self.ready_hint)
        layout.addStretch(1)
        return page

    def _summary_line(self, layout: QVBoxLayout, icon_name: str, title_key: str) -> tuple[QLabel, QLabel]:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(pixmap(icon_name, MUTED, 16))
        line.addWidget(icon)
        title = label(object_name="Faint")
        line.addWidget(title)
        line.addStretch(1)
        value = label(object_name="Value")
        line.addWidget(value)
        layout.addWidget(row)
        return title, value

    # ------------------------------------------------------------------ update
    def _retranslate(self) -> None:
        t = self.controller.translator.t
        self.brand_tag.setText(t("app_tagline"))
        self.feature_local.setText(t("onboard_feature_local_title") + " — " + t("onboard_feature_local_desc"))
        self.feature_simple.setText(t("onboard_feature_simple_title") + " — " + t("onboard_feature_simple_desc"))
        self.feature_secure.setText(t("onboard_feature_secure"))
        self.step_label.setText(t("onboard_step").format(step=self._step + 1, total=3))
        self.welcome_title.setText(t("onboard_welcome_title"))
        self.welcome_desc.setText(t("onboard_welcome_desc"))
        self.name_label.setText(t("setting_device_name"))
        self.name_desc.setText(t("onboard_name_label"))
        self.name_input.setPlaceholderText(t("onboard_name_label"))
        self.feature_local_title.setText(t("onboard_feature_local_title"))
        self.feature_local_desc.setText(t("onboard_feature_local_desc"))
        self.feature_simple_title.setText(t("onboard_feature_simple_title"))
        self.feature_simple_desc.setText(t("onboard_feature_simple_desc"))
        self.permissions_title.setText(t("onboard_permissions_title"))
        self.permissions_desc.setText(t("onboard_permissions_desc"))
        self.onboard_permission_capture_title.setText(t("onboard_permission_capture"))
        self.onboard_permission_capture_desc.setText(t("onboard_permission_capture_desc"))
        self.onboard_permission_control_title.setText(t("onboard_permission_control"))
        self.onboard_permission_control_desc.setText(t("onboard_permission_control_desc"))
        self.windows_note_title.setText(t("onboard_no_permissions_title"))
        self.windows_note_desc.setText(t("onboard_no_permissions_desc"))
        self.permission_hint.setText(t("onboard_permissions_hint"))
        self.allow_button.setText(t("onboard_allow"))
        self.settings_button.setText(t("permissions_open_settings"))
        self.ready_title.setText(t("onboard_ready_title"))
        self.ready_desc.setText(t("onboard_ready_desc"))
        self.summary_device[0].setText(t("this_computer"))
        self.summary_device[1].setText(self.name_input.text().strip() or self.controller.settings.device_name)
        self.summary_security[0].setText(t("security_tls"))
        self.summary_security[1].setText(t("tls_13"))
        self.summary_hotkey[0].setText(t("setting_hotkey"))
        self.summary_hotkey[1].setText(t("hotkey_fixed"))
        self.ready_hint.setText(t("onboard_ready_hint"))
        self.skip_button.setText(t("skip"))
        self.back_button.setText(t("back"))
        if self._step == 2:
            self.next_button.setText(t("onboard_finish"))
        else:
            self.next_button.setText(t("next"))
        self.skip_button.setVisible(self._step == 1)
        self.back_button.setVisible(self._step > 0)
        self.step_dots.setText("● ○ ○" if self._step == 0 else "● ● ○" if self._step == 1 else "● ● ●")
        self.windows_note.setVisible(sys.platform == "win32")
        self.permissions_card.setVisible(sys.platform == "darwin")

    def _change_language(self, locale: str) -> None:
        self.controller.set_language(locale)
        self._retranslate()
        self._update_permissions()

    def _on_name_changed(self, text: str) -> None:
        if hasattr(self, "summary_device"):
            self.summary_device[1].setText(text.strip() or self.controller.settings.device_name)

    def _update_permissions(self) -> None:
        info = self.controller.permissions
        statuses: list[Pill] = [self.capture_row[1], self.inject_row[1]]
        t = self.controller.translator.t
        for index, (granted, detail) in enumerate(
            ((info.capture, info.capture_detail), (info.injection, info.injection_detail))
        ):
            statuses[index].setText(t("diagnostic_granted") if granted else t("diagnostic_missing"))
            statuses[index].set_content(
                t("diagnostic_granted") if granted else t("diagnostic_missing"),
                "success" if granted else "warning",
            )
            statuses[index].setToolTip(detail)
        self.allow_button.setVisible(sys.platform == "darwin")
        self.settings_button.setVisible(sys.platform == "darwin")

    def _request_permissions(self) -> None:
        self.controller.request_permissions()
        self._update_permissions()

    def _set_step(self, step: int) -> None:
        self._step = max(0, min(2, step))
        self.stack.setCurrentIndex(self._step)
        self._retranslate()
        if self._step == 2:
            self._update_permissions()

    def _back(self) -> None:
        self._set_step(self._step - 1)

    def _next(self) -> None:
        if self._step < 2:
            self._set_step(self._step + 1)
        else:
            self._finish()

    def _skip(self) -> None:
        if self._step == 1:
            self._set_step(2)
        else:
            self._finish()

    def _finish(self) -> None:
        name = self.name_input.text().strip() or self.controller.settings.device_name
        self.controller.update_settings(
            {"device_name": name, "onboarding_complete": True},
            on_error=lambda _err: None,
        )
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.controller.update_settings(
            {
                "device_name": self.name_input.text().strip() or self.controller.settings.device_name,
                "onboarding_complete": True,
            },
            on_error=lambda _err: None,
        )
        super().closeEvent(event)


__all__ = ["OnboardingDialog"]
