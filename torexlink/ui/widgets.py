"""Reusable premium UI primitives."""

from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .icons import icon as svg_icon
from .icons import logo_pixmap, pixmap

ACCENT = "#7161FF"
ACCENT_SOFT = "#8B7CFF"
MINT = "#19C39C"
AMBER = "#E5A13A"
DANGER = "#F05A68"
MUTED = "#8D95AB"
WHITE = "#FFFFFF"

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- helpers
def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)


def make_button(
    text: str,
    *,
    kind: str = "PrimaryButton",
    icon_name: str | None = None,
    icon_color: str | None = None,
    height: int = 34,
    tooltip: str = "",
) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(kind)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(height)
    if icon_name:
        color_map = {
            "PrimaryButton": WHITE,
            "HeroButton": "#2C2371",
            "HeroGhostButton": WHITE,
            "DangerButton": DANGER,
            "SoftButton": ACCENT,
            "SecondaryButton": "#707992",
            "GhostButton": "#7B849A",
        }
        selected = icon_color or color_map.get(kind, ACCENT)
        button.setIcon(svg_icon(icon_name, selected, 16))
        button.setIconSize(QSize(16, 16))
    if tooltip:
        button.setToolTip(tooltip)
    return button


def icon_button(
    icon_name: str,
    *,
    tooltip: str = "",
    color_name: str = MUTED,
    size: int = 18,
    parent: QWidget | None = None,
) -> QPushButton:
    button = QPushButton(parent)
    button.setObjectName("IconButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setIcon(svg_icon(icon_name, color_name, size))
    button.setIconSize(QSize(size, size))
    if tooltip:
        button.setToolTip(tooltip)
    return button


def set_shadow(
    widget: QWidget,
    *,
    blur: int = 28,
    y_offset: int = 6,
    color_value: str = "#1A20401A",
) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(color_value))
    widget.setGraphicsEffect(effect)


def label(
    text: str = "",
    *,
    object_name: str = "",
    wrap: bool = False,
    alignment: Qt.AlignmentFlag | None = None,
) -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(wrap)
    if alignment is not None:
        widget.setAlignment(alignment)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class Card(QFrame):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        object_name: str = "Card",
        margins: tuple[int, int, int, int] = (20, 18, 20, 18),
        spacing: int = 12,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)


class PageHeader(QWidget):
    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        self.title = label(title, object_name="PageTitle")
        self.subtitle = label(subtitle, object_name="PageSubtitle", wrap=True)
        texts.addWidget(self.title)
        texts.addWidget(self.subtitle)
        layout.addLayout(texts, 1)
        self.action = action
        if action is not None:
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)


class SectionHeader(QWidget):
    def __init__(
        self,
        title: str,
        caption: str = "",
        *,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = label(title, object_name="SectionTitle")
        self.caption = label(caption, object_name="SectionCaption", wrap=True)
        self.caption.setVisible(bool(caption))
        texts.addWidget(self.title)
        texts.addWidget(self.caption)
        row.addLayout(texts, 1)
        if action is not None:
            row.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)


class StatusPill(QLabel):
    def __init__(self, status: str = "offline", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_status(status, "")

    def set_status(self, status: str, text: str) -> None:
        self.setProperty("status", status)
        self.setText(text)
        self.style().unpolish(self)
        self.style().polish(self)


class Pill(QLabel):
    def __init__(self, text: str, level: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Pill")
        self.setProperty("level", level)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_content(self, text: str, level: str = "") -> None:
        self.setText(text)
        self.setProperty("level", level)
        self.style().unpolish(self)
        self.style().polish(self)


class Dot(QWidget):
    def __init__(self, color_value: str = MUTED, diameter: int = 8) -> None:
        super().__init__()
        self._color = QColor(color_value)
        self._diameter = diameter
        self.setFixedSize(diameter + 4, diameter + 4)
        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._pulsing = False

    def set_color(self, value: str, pulsing: bool = False) -> None:
        self._color = QColor(value)
        self._pulsing = pulsing
        if pulsing:
            self._timer.start()
        else:
            self._timer.stop()
            self._pulse = 0.0
        self.update()

    def _tick(self) -> None:
        self._pulse = (self._pulse + 0.05) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            center = self.rect().center()
            if self._pulsing:
                glow = QColor(self._color)
                glow.setAlphaF(0.18 * (1.0 - self._pulse))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(glow)
                radius = self._diameter / 2 + self._pulse * (self._diameter / 1.4)
                painter.drawEllipse(
                    QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
                )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._color)
            radius = self._diameter / 2
            painter.drawEllipse(
                QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            )
        except Exception:  # noqa: BLE001 - painting must never crash the app
            log.exception("Status dot painting failed")
        finally:
            painter.end()


class Banner(QFrame):
    def __init__(
        self,
        title: str = "",
        text: str = "",
        *,
        level: str = "info",
        icon_name: str = "info",
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setProperty("level", level)
        self._level = level
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self.icon = QLabel()
        self.icon.setPixmap(pixmap(icon_name, _level_color(level), 18))
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        self.title = label(title, object_name="BannerTitle")
        self.text = label(text, object_name="BannerText", wrap=True)
        text_column.addWidget(self.title)
        text_column.addWidget(self.text)
        layout.addLayout(text_column, 1)
        self.action = action
        if action is not None:
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_content(self, *, title: str | None = None, text: str | None = None, level: str | None = None) -> None:
        if title is not None:
            self.title.setText(title)
        if text is not None:
            self.text.setText(text)
        if level is not None:
            self._level = level
            self.setProperty("level", level)
            icon_name = {
                "warning": "alert",
                "danger": "x_circle",
                "success": "check_circle",
                "info": "info",
            }.get(level, "info")
            self.icon.setPixmap(pixmap(icon_name, _level_color(level), 18))
            self.style().unpolish(self)
            self.style().polish(self)


def _level_color(level: str) -> str:
    return {
        "warning": AMBER,
        "danger": DANGER,
        "success": MINT,
        "info": ACCENT,
    }.get(level, ACCENT)


class ToggleSwitch(QAbstractButton):
    def __init__(
        self,
        *,
        checked: bool = False,
        parent: QWidget | None = None,
        dark: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)
        self._position = 1.0 if checked else 0.0
        self._dark = dark
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _get_position(self) -> float:
        return self._position

    def _set_position(self, value: float) -> None:
        self._position = value
        self.update()

    position = Property(float, _get_position, _set_position)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(42, 24)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            track_off = QColor("#30384C" if self._dark else "#E1E5F0")
            track_on = QColor(ACCENT)
            track = QColor(
                int(track_off.red() + (track_on.red() - track_off.red()) * self._position),
                int(track_off.green() + (track_on.green() - track_off.green()) * self._position),
                int(track_off.blue() + (track_on.blue() - track_off.blue()) * self._position),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(track)
            painter.drawRoundedRect(QRectF(0, 1, 42, 22), 11, 11)
            knob_x = 3 + self._position * 21
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QRectF(knob_x, 4, 16, 16))
            if self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(ACCENT_SOFT), 1.4))
                painter.drawRoundedRect(QRectF(0.7, 0.7, 40.6, 22.6), 11, 11)
        except Exception:  # noqa: BLE001 - painting must never crash the app
            log.exception("Toggle painting failed")
        finally:
            painter.end()


class SegmentedControl(QFrame):
    changed = Signal(str)

    def __init__(
        self,
        options: list[tuple[str, str]],
        current: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for value, text in options:
            button = QPushButton(text)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, val=value: self._on_click(val))
            self._group.addButton(button)
            layout.addWidget(button)
            self._buttons[value] = button
        self.set_value(current)

    def _on_click(self, value: str) -> None:
        self.set_value(value)
        self.changed.emit(value)

    def set_value(self, value: str) -> None:
        for key, button in self._buttons.items():
            button.setChecked(key == value)

    def set_labels(self, options: list[tuple[str, str]]) -> None:
        for value, text in options:
            if value in self._buttons:
                self._buttons[value].setText(text)


class StepStrip(QWidget):
    def __init__(self, steps: list[str], current: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps = steps
        self._current = current
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._circles: list[QLabel] = []
        self._labels: list[QLabel] = []
        self._lines: list[QFrame] = []
        for index, step in enumerate(steps):
            circle = QLabel(str(index + 1))
            circle.setObjectName("StepNumber")
            circle.setFixedSize(24, 24)
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            circle.setProperty("state", "idle")
            self._circles.append(circle)
            text = QLabel(step)
            text.setObjectName("StepLabel")
            self._labels.append(text)
            self._layout.addWidget(circle)
            self._layout.addWidget(text)
            if index < len(steps) - 1:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setFixedHeight(1)
                line.setMinimumWidth(24)
                line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                line.setStyleSheet("background: rgba(130,138,160,.28);")
                self._lines.append(line)
                self._layout.addWidget(line, 1)
        self.set_current(current)

    def set_current(self, current: int) -> None:
        self._current = max(0, min(current, len(self._steps) - 1))
        for index, (circle, text) in enumerate(zip(self._circles, self._labels, strict=False)):
            active = index <= self._current
            current_step = index == self._current
            text.setProperty("active", "true" if current_step else "false")
            text.style().unpolish(text)
            text.style().polish(text)
            circle.setProperty("state", "active" if active else "idle")
            circle.setStyleSheet(_step_style(active, current_step))

    def set_labels(self, labels: list[str]) -> None:
        self._steps = labels
        for text, value in zip(self._labels, labels, strict=False):
            text.setText(value)


def _step_style(active: bool, current: bool) -> str:
    if current:
        return (
            "QLabel { background: #7161FF; color: #FFFFFF; border-radius: 12px;"
            " font-size: 10px; font-weight: 700; }"
        )
    if active:
        return (
            "QLabel { background: rgba(113,97,255,.16); color: #8B7CFF; border-radius: 12px;"
            " font-size: 10px; font-weight: 700; }"
        )
    return (
        "QLabel { background: rgba(130,138,160,.10); color: #8D95AB; border-radius: 12px;"
        " font-size: 10px; font-weight: 700; }"
    )


class Spinner(QWidget):
    def __init__(self, diameter: int = 26, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._angle = 0
        self.setFixedSize(diameter, diameter)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._angle = (self._angle + 5) % 360
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(3, 3, self._diameter - 6, self._diameter - 6)
            painter.setPen(QPen(QColor(113, 97, 255, 48), 2.4))
            painter.drawEllipse(rect)
            arc_pen = QPen(QColor(ACCENT_SOFT), 2.4)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            painter.drawArc(rect, int(-self._angle * 16), int(110 * 16))
        except Exception:  # noqa: BLE001 - painting must never crash the app
            log.exception("Spinner painting failed")
        finally:
            painter.end()

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()


class CountdownLabel(QLabel):
    expired = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._deadline: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self.setText("00:00")

    def set_deadline(self, deadline: float) -> None:
        self._deadline = deadline
        self._tick()
        self._timer.start()

    def _tick(self) -> None:
        if self._deadline is None:
            self.setText("—")
            return
        remaining = max(0.0, self._deadline - time.time())
        minutes, seconds = divmod(int(math.ceil(remaining)), 60)
        self.setText(f"{minutes:02d}:{seconds:02d}")
        if remaining <= 0:
            self._timer.stop()
            self._deadline = None
            self.expired.emit()


class CodeDisplay(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._cells: list[QLabel] = []
        self._digits = "------"
        for _ in range(6):
            cell = QFrame()
            cell.setObjectName("CodeCell")
            cell.setFixedSize(48, 58)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            digit = QLabel("•")
            digit.setObjectName("CodeCellDigit")
            digit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(digit)
            self._cells.append(digit)
            layout.addWidget(cell)
        layout.addStretch(1)

    def set_code(self, code: str) -> None:
        self._digits = code
        for index, cell in enumerate(self._cells):
            cell.setText(code[index] if index < len(code) else "•")


class QRCodeView(QWidget):
    def __init__(self, size: int = 220, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = ""
        self._pixmap: QPixmap | None = None
        self._size = size
        self.setFixedSize(size, size)
        self.setAccessibleName("QR code")

    def set_data(self, data: str) -> None:
        self._data = data
        self._pixmap = None
        self.update()

    def _render(self) -> QPixmap:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_H

        qr = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_H,
            box_size=12,
            border=2,
        )
        qr.add_data(self._data)
        qr.make(fit=True)
        image = qr.make_image(fill_color="#12162A", back_color="#FFFFFF").convert("RGB")
        width, height = image.size
        raw = image.tobytes("raw", "RGB")
        from PySide6.QtGui import QImage

        qimage = QImage(raw, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimage)
        # A small centre mark keeps the QR premium while remaining readable at
        # the 30% error-correction level used here.
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = int(pixmap.width() * 0.19)
        x = (pixmap.width() - side) // 2
        y = (pixmap.height() - side) // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(QRectF(x - 4, y - 4, side + 8, side + 8), 8, 8)
        logo = logo_pixmap(side)
        painter.drawPixmap(x, y, logo)
        painter.end()
        return pixmap

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 14, 14)
            if not self._data:
                return
            if self._pixmap is None:
                self._pixmap = self._render()
            inset = 10
            target = self.rect().adjusted(inset, inset, -inset, -inset)
            scaled = self._pixmap.scaled(
                target.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = target.x() + (target.width() - scaled.width()) // 2
            y = target.y() + (target.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        except Exception:  # noqa: BLE001 - painting must never crash the app
            log.exception("QR code painting failed")
        finally:
            painter.end()


class Avatar(QLabel):
    LEVELS = ("violet", "mint", "amber")

    def __init__(self, text: str, *, size: int = 40, level: str = "violet") -> None:
        super().__init__(text)
        self.setObjectName("Avatar")
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setProperty("level", level)
        self.set_level(level)

    def set_level(self, level: str) -> None:
        if level not in self.LEVELS:
            level = "violet"
        self.setProperty("level", level)
        self.style().unpolish(self)
        self.style().polish(self)


class IconTile(QLabel):
    def __init__(
        self,
        icon_name: str,
        *,
        color_name: str = ACCENT,
        size: int = 38,
        icon_size: int = 19,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPixmap(pixmap(icon_name, color_name, icon_size))
        self.setStyleSheet(
            f"QLabel {{ background: rgba(113,97,255,.10); border-radius: {size // 2}px; }}"
        )


class CopyField(QWidget):
    copied = Signal()

    def __init__(
        self,
        text: str = "",
        *,
        mono: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(text)
        self.edit.setReadOnly(True)
        if mono:
            self.edit.setProperty("class", "mono")
            self.edit.setStyleSheet("font-family: 'SF Mono', 'Cascadia Mono', monospace; font-size: 11px;")
        layout.addWidget(self.edit, 1)
        self.button = icon_button("copy", tooltip="Copy", size=15)
        self.button.clicked.connect(self._copy)
        layout.addWidget(self.button)

    def set_text(self, text: str) -> None:
        self.edit.setText(text)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.edit.text())
        self.copied.emit()


class EmptyState(QWidget):
    def __init__(
        self,
        icon_name: str,
        title: str,
        description: str,
        *,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(10)
        tile = QLabel()
        tile.setObjectName("EmptyStateIcon")
        tile.setFixedSize(52, 52)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(pixmap(icon_name, ACCENT, 23))
        layout.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = label(
            title,
            object_name="EmptyTitle",
            wrap=True,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        self.description = label(
            description,
            object_name="EmptyDesc",
            wrap=True,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        for item in (self.title, self.description):
            item.setMinimumWidth(1)
            item.setMaximumWidth(390)
            item.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        if action is not None:
            layout.addSpacing(4)
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)


class FormRow(QWidget):
    """A settings row with a label, description and trailing control."""

    def __init__(
        self,
        title: str,
        description: str = "",
        control: QWidget | None = None,
        *,
        parent: QWidget | None = None,
        divider: bool = True,
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(18)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.title = label(title, object_name="SectionTitle")
        self.description = label(description, object_name="SectionCaption", wrap=True)
        self.description.setVisible(bool(description))
        texts.addWidget(self.title)
        texts.addWidget(self.description)
        row.addLayout(texts, 1)
        self._row = row
        self.control = control
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(row)
        if divider:
            outer.addSpacing(14)
            outer.addWidget(hline())
        self._divider = divider

    def set_description(self, text: str) -> None:
        self.description.setText(text)
        self.description.setVisible(bool(text))

    def set_control(self, control: QWidget) -> None:
        if self.control is not None and self.control is not control:
            self.control.deleteLater()
        self.control = control
        self._row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)


class Toast(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(8)
        self.icon = QLabel()
        layout.addWidget(self.icon)
        self.text = QLabel("")
        self.text.setObjectName("ToastText")
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(520)
        layout.addWidget(self.text)
        self.hide()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(180)

    def show_message(self, text: str, *, level: str = "success", duration: int = 2600) -> None:
        icon_name = {"success": "check_circle", "error": "x_circle", "info": "info"}.get(level, "info")
        color_value = {"success": MINT, "error": DANGER, "info": ACCENT}.get(level, ACCENT)
        self.icon.setPixmap(pixmap(icon_name, color_value, 16))
        self.text.setText(text)
        self._opacity.setOpacity(1.0)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(duration)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 26
        self.move(max(margin, parent.width() - self.width() - margin), parent.height() - self.height() - margin)

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.stop()
        try:
            self._fade.finished.disconnect(self._finish_hide)
        except (TypeError, RuntimeError):
            pass
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._finish_hide)
        self._fade.start()

    def _finish_hide(self) -> None:
        try:
            self._fade.finished.disconnect(self._finish_hide)
        except (TypeError, RuntimeError):
            pass
        self.hide()

    def parent_resized(self) -> None:
        if self.isVisible():
            self._reposition()


class IconValue(QWidget):
    """Compact icon + title + value line used in detail cards."""

    def __init__(
        self,
        icon_name: str,
        title: str,
        value: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        icon_label = QLabel()
        icon_label.setPixmap(pixmap(icon_name, MUTED, 16))
        row.addWidget(icon_label)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = label(title, object_name="Faint")
        self.value = label(value, object_name="Value")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(self.title)
        texts.addWidget(self.value)
        row.addLayout(texts, 1)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


def make_avatar_for_name(name: str) -> Avatar:
    import hashlib

    digest = hashlib.sha256(name.encode("utf-8")).digest()
    levels = Avatar.LEVELS
    return Avatar((name.strip()[:1] or "?").upper(), level=levels[digest[0] % len(levels)])


def copy_to_clipboard(text: str) -> bool:
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return False
    clipboard.setText(text)
    return True


__all__ = [
    "ACCENT",
    "Avatar",
    "Banner",
    "Card",
    "CodeDisplay",
    "CopyField",
    "CountdownLabel",
    "Dot",
    "EmptyState",
    "FormRow",
    "IconTile",
    "IconValue",
    "PageHeader",
    "Pill",
    "QRCodeView",
    "SectionHeader",
    "SegmentedControl",
    "Spinner",
    "StatusPill",
    "StepStrip",
    "Toast",
    "ToggleSwitch",
    "clear_layout",
    "copy_to_clipboard",
    "hline",
    "icon_button",
    "label",
    "make_avatar_for_name",
    "make_button",
    "set_shadow",
]
