"""Design tokens and a single, carefully scoped Qt style sheet."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication


@dataclass(frozen=True, slots=True)
class Palette:
    mode: str
    bg: str
    bg_elevated: str
    surface: str
    surface_alt: str
    surface_hover: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    sidebar_start: str
    sidebar_end: str
    sidebar_text: str
    sidebar_muted: str
    sidebar_hover: str
    sidebar_active: str
    shadow: str
    code_bg: str


LIGHT = Palette(
    mode="light",
    bg="#F4F6FB",
    bg_elevated="#FBFCFF",
    surface="#FFFFFF",
    surface_alt="#F7F8FD",
    surface_hover="#F1F3FA",
    border="#E6E9F2",
    border_strong="#D7DCEA",
    text="#171B2D",
    text_secondary="#596079",
    text_muted="#8A91A8",
    accent="#6558F5",
    accent_hover="#5547E6",
    accent_pressed="#463ACF",
    accent_soft="#EEECFF",
    success="#0E9F6E",
    success_soft="#E4F8F0",
    warning="#D97706",
    warning_soft="#FFF5E5",
    danger="#DC3D4B",
    danger_soft="#FDECEF",
    sidebar_start="#14172B",
    sidebar_end="#0D1020",
    sidebar_text="#F3F4FF",
    sidebar_muted="#9299B8",
    sidebar_hover="#FFFFFF",
    sidebar_active="#FFFFFF",
    shadow="#1B214018",
    code_bg="#F5F6FC",
)

DARK = Palette(
    mode="dark",
    bg="#0A0D16",
    bg_elevated="#0F1320",
    surface="#131827",
    surface_alt="#181E30",
    surface_hover="#1C2236",
    border="#242B3E",
    border_strong="#30384F",
    text="#F3F5FF",
    text_secondary="#A7AFC6",
    text_muted="#727C97",
    accent="#8B7CFF",
    accent_hover="#9C90FF",
    accent_pressed="#7464F2",
    accent_soft="#211F3E",
    success="#39D5A0",
    success_soft="#102E27",
    warning="#F5B34A",
    warning_soft="#312619",
    danger="#FF6B7A",
    danger_soft="#361A20",
    sidebar_start="#0B0E19",
    sidebar_end="#070911",
    sidebar_text="#F6F7FF",
    sidebar_muted="#858DA8",
    sidebar_hover="#FFFFFF",
    sidebar_active="#FFFFFF",
    shadow="#00000038",
    code_bg="#0E1220",
)


def resolve_theme(theme: str) -> str:
    if theme in {"light", "dark"}:
        return theme
    app = QGuiApplication.instance()
    if app is not None:
        try:
            hints = app.styleHints()
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
            if scheme == Qt.ColorScheme.Light:
                return "light"
        except Exception:  # noqa: BLE001
            pass
    return "light"


def palette_for(theme: str) -> Palette:
    return DARK if resolve_theme(theme) == "dark" else LIGHT


def color(palette: Palette, name: str) -> QColor:
    return QColor(getattr(palette, name))


def platform_font_family() -> str:
    if sys.platform == "darwin":
        return "SF Pro Text"
    if sys.platform == "win32":
        return "Segoe UI Variable Text"
    return "Inter"


def mono_font_family() -> str:
    if sys.platform == "darwin":
        return "SF Mono"
    if sys.platform == "win32":
        return "Cascadia Mono"
    return "DejaVu Sans Mono"


def build_stylesheet(theme: str) -> str:
    p = palette_for(theme)
    family = platform_font_family()
    mono = mono_font_family()
    return f"""
    * {{
        font-family: "{family}", "Segoe UI", "Helvetica Neue", sans-serif;
        outline: none;
    }}
    QWidget {{
        color: {p.text};
        font-size: 13px;
    }}
    QMainWindow, QWidget#AppRoot {{
        background: {p.bg};
    }}
    QWidget#PageContent {{
        background: transparent;
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 1px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border_strong};
        min-height: 32px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p.text_muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        height: 9px;
        background: transparent;
    }}
    QScrollBar::handle:horizontal {{
        background: {p.border_strong};
        border-radius: 4px;
        min-width: 32px;
    }}
    QWidget#Sidebar {{
        background: qlineargradient(x1:0, y1:0, x2:0.7, y2:1,
            stop:0 {p.sidebar_start}, stop:1 {p.sidebar_end});
        border-right: 1px solid {p.border};
    }}
    QLabel#BrandTitle {{
        color: {p.sidebar_text};
        font-size: 19px;
        font-weight: 700;
        letter-spacing: -0.2px;
    }}
    QLabel#BrandTagline {{
        color: {p.sidebar_muted};
        font-size: 10.5px;
        font-weight: 500;
    }}
    QLabel#NavSection {{
        color: {p.sidebar_muted};
        font-size: 9.5px;
        font-weight: 700;
        letter-spacing: 1.4px;
        padding-left: 10px;
    }}
    QPushButton#NavButton {{
        color: #AAB2CE;
        background: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 9px 11px;
        text-align: left;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton#NavButton:hover {{
        color: {p.sidebar_text};
        background: rgba(255, 255, 255, 0.055);
    }}
    QPushButton#NavButton:checked {{
        color: {p.sidebar_text};
        background: rgba(113, 97, 255, 0.18);
        border: 1px solid rgba(139, 124, 255, 0.22);
        font-weight: 600;
    }}
    QFrame#SidebarStatus {{
        background: rgba(255, 255, 255, 0.045);
        border: 1px solid rgba(255, 255, 255, 0.075);
        border-radius: 12px;
    }}
    QLabel#SidebarStatusTitle {{
        color: {p.sidebar_text};
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel#SidebarStatusDetail {{
        color: {p.sidebar_muted};
        font-size: 10.5px;
    }}
    QLabel#SidebarCaption {{
        color: {p.sidebar_muted};
        font-size: 9px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QLabel#PageTitle {{
        color: {p.text};
        font-size: 26px;
        font-weight: 700;
        letter-spacing: -0.5px;
    }}
    QLabel#PageSubtitle {{
        color: {p.text_secondary};
        font-size: 13px;
    }}
    QLabel#SectionTitle {{
        color: {p.text};
        font-size: 14px;
        font-weight: 650;
    }}
    QLabel#SectionCaption {{
        color: {p.text_secondary};
        font-size: 11.5px;
    }}
    QLabel#Overline {{
        color: {p.text_muted};
        font-size: 9.5px;
        font-weight: 700;
        letter-spacing: 1.3px;
    }}
    QLabel#Muted {{
        color: {p.text_secondary};
    }}
    QLabel#Faint {{
        color: {p.text_muted};
    }}
    QLabel#Value {{
        color: {p.text};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel#Mono {{
        font-family: "{mono}", monospace;
        color: {p.text_secondary};
        font-size: 11.5px;
    }}
    QLabel#MonoValue {{
        font-family: "{mono}", monospace;
        color: {p.text};
        font-size: 12px;
        font-weight: 500;
    }}
    QLabel#HeroTitle {{
        color: white;
        font-size: 29px;
        font-weight: 700;
        letter-spacing: -0.6px;
    }}
    QLabel#HeroSubtitle {{
        color: rgba(242, 244, 255, 0.74);
        font-size: 12.5px;
    }}
    QLabel#HeroEyebrow {{
        color: rgba(202, 205, 255, 0.78);
        font-size: 9.5px;
        font-weight: 700;
        letter-spacing: 1.6px;
    }}
    QLabel#HeroStatusTitle {{
        color: white;
        font-size: 17px;
        font-weight: 650;
    }}
    QLabel#HeroStatusSub {{
        color: rgba(237, 239, 255, 0.64);
        font-size: 11px;
    }}
    QFrame#Card {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 16px;
    }}
    QFrame#CardAlt {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}
    QFrame#CardHover {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 14px;
    }}
    QFrame#CardHover:hover {{
        border: 1px solid {p.border_strong};
        background: {p.surface_alt};
    }}
    QFrame#InnerCard {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}
    QFrame#HeroCard {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 #1B2050, stop:0.55 #2B2371, stop:1 #5547D8);
        border: 1px solid rgba(255, 255, 255, 0.10);
        border-radius: 20px;
    }}
    QFrame#HeroCardLight {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 #181D43, stop:0.5 #2C2476, stop:1 #5A4BE2);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 20px;
    }}
    QFrame#Divider {{
        background: {p.border};
        max-height: 1px;
        min-height: 1px;
        border: none;
    }}
    QFrame#Banner {{
        border-radius: 12px;
        border: 1px solid {p.border};
        background: {p.surface_alt};
    }}
    QFrame#Banner[level="warning"] {{
        background: {p.warning_soft};
        border-color: {p.warning};
    }}
    QFrame#Banner[level="danger"] {{
        background: {p.danger_soft};
        border-color: {p.danger};
    }}
    QFrame#Banner[level="success"] {{
        background: {p.success_soft};
        border-color: {p.success};
    }}
    QLabel#BannerTitle {{
        font-weight: 650;
        font-size: 12.5px;
    }}
    QLabel#BannerText {{
        color: {p.text_secondary};
        font-size: 11.5px;
    }}
    QPushButton {{
        min-height: 18px;
        border-radius: 9px;
        padding: 8px 14px;
        font-size: 12.5px;
        font-weight: 600;
    }}
    QPushButton#PrimaryButton {{
        color: white;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {p.accent}, stop:1 #8A5CFF);
        border: 1px solid rgba(255, 255, 255, 0.10);
    }}
    QPushButton#PrimaryButton:hover {{
        background: {p.accent_hover};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {p.accent_pressed};
    }}
    QPushButton#PrimaryButton:disabled {{
        background: {p.border};
        color: {p.text_muted};
    }}
    QPushButton#SecondaryButton {{
        color: {p.text};
        background: {p.surface};
        border: 1px solid {p.border_strong};
    }}
    QPushButton#SecondaryButton:hover {{
        background: {p.surface_hover};
        border: 1px solid {p.text_muted};
    }}
    QPushButton#SecondaryButton:disabled {{
        color: {p.text_muted};
        border-color: {p.border};
    }}
    QPushButton#SoftButton {{
        color: {p.accent};
        background: {p.accent_soft};
        border: 1px solid transparent;
    }}
    QPushButton#SoftButton:hover {{
        border-color: {p.accent};
    }}
    QPushButton#GhostButton {{
        color: {p.text_secondary};
        background: transparent;
        border: 1px solid transparent;
        font-weight: 500;
    }}
    QPushButton#GhostButton:hover {{
        color: {p.text};
        background: {p.surface_hover};
    }}
    QPushButton#DangerButton {{
        color: {p.danger};
        background: {p.danger_soft};
        border: 1px solid transparent;
    }}
    QPushButton#DangerButton:hover {{
        border-color: {p.danger};
    }}
    QPushButton#LinkButton {{
        color: {p.accent};
        background: transparent;
        border: none;
        padding: 3px 2px;
        text-align: left;
        font-weight: 550;
    }}
    QPushButton#LinkButton:hover {{
        color: {p.accent_hover};
        text-decoration: underline;
    }}
    QPushButton#HeroButton {{
        background: white;
        color: #2C2371;
        border-radius: 10px;
        padding: 9px 16px;
        font-weight: 650;
    }}
    QPushButton#HeroButton:hover {{
        background: #F2F0FF;
    }}
    QPushButton#HeroGhostButton {{
        background: rgba(255, 255, 255, 0.10);
        color: white;
        border: 1px solid rgba(255, 255, 255, 0.14);
        border-radius: 10px;
        padding: 9px 16px;
        font-weight: 550;
    }}
    QPushButton#HeroGhostButton:hover {{
        background: rgba(255, 255, 255, 0.16);
    }}
    QPushButton#IconButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 9px;
        padding: 5px;
        min-width: 28px;
        max-width: 28px;
        min-height: 28px;
        max-height: 28px;
    }}
    QPushButton#IconButton:hover {{
        background: {p.surface_hover};
        border-color: {p.border};
    }}
    QPushButton#SegmentButton {{
        color: {p.text_secondary};
        background: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 6px 10px;
        font-size: 11px;
        font-weight: 550;
    }}
    QPushButton#SegmentButton:checked {{
        color: {p.text};
        background: {p.surface};
        border-color: {p.border};
    }}
    QPushButton#SegmentButton:hover {{
        color: {p.text};
    }}
    QFrame#SegmentBar {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 10px;
    }}
    QLabel#StatusPill {{
        border-radius: 9px;
        padding: 4px 9px;
        font-size: 10px;
        font-weight: 650;
        color: {p.text_secondary};
        background: {p.surface_alt};
        border: 1px solid {p.border};
    }}
    QLabel#StatusPill[status="connected"], QLabel#StatusPill[status="success"] {{
        color: {p.success};
        background: {p.success_soft};
        border-color: rgba(14, 159, 110, 0.22);
    }}
    QLabel#StatusPill[status="pairing"], QLabel#StatusPill[status="hosting"] {{
        color: {p.accent};
        background: {p.accent_soft};
        border-color: rgba(101, 88, 245, 0.22);
    }}
    QLabel#StatusPill[status="connecting"], QLabel#StatusPill[status="reconnecting"] {{
        color: {p.warning};
        background: {p.warning_soft};
        border-color: rgba(217, 119, 6, 0.22);
    }}
    QLabel#StatusPill[status="error"] {{
        color: {p.danger};
        background: {p.danger_soft};
        border-color: rgba(220, 61, 75, 0.22);
    }}
    QLabel#StatusPill[status="offline"] {{
        color: {p.text_secondary};
        background: {p.surface_alt};
    }}
    QLabel#Pill {{
        border-radius: 8px;
        padding: 3px 8px;
        font-size: 9.5px;
        font-weight: 650;
        color: {p.accent};
        background: {p.accent_soft};
    }}
    QLabel#Pill[level="success"] {{
        color: {p.success};
        background: {p.success_soft};
    }}
    QLabel#Pill[level="warning"] {{
        color: {p.warning};
        background: {p.warning_soft};
    }}
    QLabel#Pill[level="muted"] {{
        color: {p.text_secondary};
        background: {p.surface_alt};
    }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox {{
        background: {p.surface_alt};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 8px 11px;
        selection-background-color: {p.accent};
        selection-color: white;
    }}
    QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover {{
        border-color: {p.border_strong};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{
        border: 1px solid {p.accent};
        background: {p.surface};
    }}
    QLineEdit:disabled {{
        color: {p.text_muted};
    }}
    QLineEdit#CodeInput {{
        font-family: "{mono}", monospace;
        font-size: 28px;
        font-weight: 600;
        letter-spacing: 12px;
        padding: 13px 18px;
        text-align: center;
        background: {p.code_bg};
        border: 1px solid {p.border_strong};
        border-radius: 13px;
    }}
    QLineEdit#CodeInput:focus {{
        border: 1px solid {p.accent};
        background: {p.surface};
    }}
    QLineEdit#SearchInput {{
        padding-left: 10px;
        border-radius: 9px;
    }}
    QComboBox {{
        background: {p.surface_alt};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 7px 10px;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border-color: {p.border_strong};
    }}
    QComboBox:focus {{
        border-color: {p.accent};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox::down-arrow {{
        image: none;
        width: 0;
    }}
    QComboBox QAbstractItemView {{
        background: {p.surface};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 9px;
        selection-background-color: {p.accent_soft};
        selection-color: {p.text};
        padding: 4px;
        outline: 0;
    }}
    QCheckBox {{
        color: {p.text_secondary};
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 5px;
        border: 1px solid {p.border_strong};
        background: {p.surface_alt};
    }}
    QCheckBox::indicator:checked {{
        background: {p.accent};
        border-color: {p.accent};
    }}
    QMenu {{
        background: {p.surface};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        background: transparent;
        border-radius: 6px;
        padding: 7px 24px 7px 12px;
        font-size: 12px;
    }}
    QMenu::item:selected {{
        background: {p.accent_soft};
        color: {p.text};
    }}
    QMenu::item:disabled {{
        color: {p.text_muted};
    }}
    QMenu::separator {{
        height: 1px;
        background: {p.border};
        margin: 5px 8px;
    }}
    QMenu::indicator {{
        width: 12px;
        height: 12px;
    }}
    QToolTip {{
        background: {p.text};
        color: {p.surface};
        border: none;
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 11px;
    }}
    QDialog {{
        background: {p.bg};
    }}
    QFrame#DialogCard {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 18px;
    }}
    QLabel#DialogTitle {{
        color: {p.text};
        font-size: 20px;
        font-weight: 700;
        letter-spacing: -0.25px;
    }}
    QLabel#DialogSubtitle {{
        color: {p.text_secondary};
        font-size: 12px;
    }}
    QLabel#DialogHint {{
        color: {p.text_muted};
        font-size: 11px;
    }}
    QPushButton#CloseButton {{
        background: transparent;
        border: none;
        color: {p.text_muted};
        font-size: 16px;
        padding: 3px;
        min-width: 24px;
        min-height: 24px;
    }}
    QPushButton#CloseButton:hover {{
        color: {p.text};
        background: {p.surface_hover};
        border-radius: 7px;
    }}
    QProgressBar {{
        background: {p.surface_alt};
        border: none;
        border-radius: 4px;
        max-height: 7px;
        min-height: 7px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {p.accent};
        border-radius: 4px;
    }}
    QPlainTextEdit#LogView {{
        font-family: "{mono}", monospace;
        font-size: 11px;
        background: {p.code_bg};
        border: 1px solid {p.border};
        border-radius: 12px;
        padding: 12px;
        color: {p.text_secondary};
    }}
    QFrame#CodeCell {{
        background: {p.code_bg};
        border: 1px solid {p.border_strong};
        border-radius: 12px;
    }}
    QLabel#CodeCellDigit {{
        font-family: "{mono}", monospace;
        font-size: 26px;
        font-weight: 700;
        color: {p.text};
    }}
    QLabel#CodeMeta {{
        color: {p.text_muted};
        font-size: 10.5px;
    }}
    QFrame#Toast {{
        background: {p.text};
        color: {p.surface};
        border-radius: 11px;
    }}
    QLabel#ToastText {{
        color: {p.surface};
        font-size: 12px;
        font-weight: 500;
    }}
    QFrame#EmptyStateIcon {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 18px;
    }}
    QLabel#EmptyTitle {{
        font-size: 14px;
        font-weight: 650;
        color: {p.text};
    }}
    QLabel#EmptyDesc {{
        font-size: 11.5px;
        color: {p.text_secondary};
    }}
    QFrame#DeviceRow {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 14px;
    }}
    QFrame#DeviceRow:hover {{
        border-color: {p.border_strong};
        background: {p.surface_alt};
    }}
    QFrame#DeviceRow[connected="true"] {{
        border: 1px solid {p.accent};
        background: {p.surface};
    }}
    QLabel#Avatar {{
        background: {p.accent_soft};
        color: {p.accent};
        border-radius: 20px;
        font-size: 15px;
        font-weight: 700;
    }}
    QLabel#Avatar[level="mint"] {{
        background: {p.success_soft};
        color: {p.success};
    }}
    QLabel#Avatar[level="amber"] {{
        background: {p.warning_soft};
        color: {p.warning};
    }}
    QLabel#DeviceName {{
        font-size: 13px;
        font-weight: 650;
        color: {p.text};
    }}
    QLabel#DeviceMeta {{
        font-size: 10.5px;
        color: {p.text_muted};
    }}
    QFrame#StepCircle {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 12px;
        min-width: 24px;
        max-width: 24px;
        min-height: 24px;
        max-height: 24px;
    }}
    QLabel#StepNumber {{
        color: {p.text_muted};
        font-size: 10px;
        font-weight: 700;
    }}
    QLabel#StepLabel {{
        color: {p.text_muted};
        font-size: 11px;
        font-weight: 550;
    }}
    QLabel#StepLabel[active="true"] {{
        color: {p.text};
        font-weight: 600;
    }}
    QLabel#FocusBanner {{
        background: {p.warning_soft};
        border: 1px solid rgba(217, 119, 6, 0.35);
        border-radius: 10px;
        color: {p.text};
        padding: 7px 10px;
        font-size: 11.5px;
    }}
    QSplitter::handle {{
        background: transparent;
    }}
    """
