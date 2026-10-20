"""Modal pairing, QR scanning and confirmation dialogs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRegularExpression, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..pairing_payload import PairingPayload, PairingPayloadError
from .widgets import (
    ACCENT,
    Spinner,
    hline,
    label,
    make_button,
)

if TYPE_CHECKING:
    from .controller import ToRexLinkController

log = logging.getLogger(__name__)


class JoinDialog(QDialog):
    def __init__(
        self,
        controller: ToRexLinkController,
        *,
        device_name: str | None = None,
        host: str = "",
        port: int = 48620,
        fingerprint: str | None = None,
        device_id: str | None = None,
        manual: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._manual = manual or not device_name
        self._host = host
        self._port = port
        self._fingerprint = fingerprint
        self._device_id = device_id
        self._device_name = device_name or controller.translator.t("this_computer")
        self._busy = False
        self._done = False
        self.setWindowTitle(controller.translator.t("join_dialog_title"))
        self.setModal(True)
        self.setMinimumWidth(430)
        self.setMaximumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._build()
        self._retranslate()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.icon_tile = QLabel()
        self.icon_tile.setFixedSize(42, 42)
        self.icon_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_tile.setStyleSheet("background: rgba(113,97,255,.12); border-radius: 12px;")
        from .icons import pixmap

        self.icon_tile.setPixmap(pixmap("key", ACCENT, 20))
        header.addWidget(self.icon_tile, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.title = label(object_name="DialogTitle")
        self.subtitle = label(wrap=True, object_name="DialogSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        root.addLayout(header)

        self.form = QVBoxLayout()
        self.form.setSpacing(8)
        self.address_label = label(object_name="Faint")
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("192.168.1.20")
        self.port_label = label(object_name="Faint")
        self.port_input = QLineEdit(str(self._port))
        self.port_input.setMaximumWidth(120)
        self.port_input.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[0-9]{1,5}"), self)
        )
        address_row = QHBoxLayout()
        address_row.setSpacing(10)
        address_col = QVBoxLayout()
        address_col.setSpacing(6)
        address_col.addWidget(self.address_label)
        address_col.addWidget(self.host_input)
        port_col = QVBoxLayout()
        port_col.setSpacing(6)
        port_col.addWidget(self.port_label)
        port_col.addWidget(self.port_input)
        address_row.addLayout(address_col, 1)
        address_row.addLayout(port_col, 0)
        self.address_container = QWidget()
        self.address_container.setLayout(address_row)
        self.form.addWidget(self.address_container)

        self.code_label = label(object_name="Faint")
        self.code_input = QLineEdit()
        self.code_input.setObjectName("CodeInput")
        self.code_input.setMaxLength(6)
        self.code_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_input.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[0-9]{0,6}"), self)
        )
        self.code_input.setAccessibleName(self.controller.translator.t("code_entry_label"))
        self.form.addWidget(self.code_label)
        self.form.addWidget(self.code_input)
        root.addLayout(self.form)

        self.note = QLabel()
        self.note.setObjectName("DialogHint")
        self.note.setWordWrap(True)
        root.addWidget(self.note)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            "color: #F05A68; background: rgba(240,90,104,.10); border-radius: 8px; padding: 8px 10px;"
        )
        self.error_label.hide()
        root.addWidget(self.error_label)

        controls = QHBoxLayout()
        controls.setSpacing(9)
        self.progress = Spinner(20)
        self.progress.stop()
        self.progress.hide()
        controls.addWidget(self.progress)
        controls.addStretch(1)
        self.cancel_button = make_button("", kind="GhostButton")
        self.connect_button = make_button("", kind="PrimaryButton", icon_name="arrow_right")
        self.cancel_button.clicked.connect(self.reject)
        self.connect_button.clicked.connect(self._submit)
        self.code_input.returnPressed.connect(self._submit)
        self.host_input.returnPressed.connect(self._submit)
        self.port_input.returnPressed.connect(self._submit)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.connect_button)
        root.addLayout(controls)
        self.code_input.setFocus()
        if self._fingerprint:
            self.host_input.setVisible(False)
            self.port_input.setVisible(False)
            self.address_container.setVisible(False)
        else:
            self.host_input.setText(self._host)
            self.port_input.setText(str(self._port))
        self.note.setText(self.controller.translator.t("pairing_only_host"))

    def _retranslate(self) -> None:
        t = self.controller.translator.t
        self.setWindowTitle(t("join_dialog_title") if not self._manual else t("join_manual_title"))
        self.title.setText(t("join_dialog_title") if not self._manual else t("join_manual_title"))
        if self._manual:
            self.subtitle.setText(t("join_manual_subtitle"))
        else:
            self.subtitle.setText(t("join_dialog_subtitle").format(name=self._device_name))
        self.address_label.setText(t("host_address_label"))
        self.host_input.setPlaceholderText(t("host_address_placeholder"))
        self.port_label.setText(t("port_label"))
        self.code_label.setText(t("code_entry_label"))
        self.note.setText(t("pairing_only_host"))
        self.cancel_button.setText(t("cancel"))
        self.connect_button.setText(t("connect_button"))
        self.connect_button.setIcon(load_icon("arrow_right", "#FFFFFF", 16))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.code_input.setEnabled(not busy)
        self.host_input.setEnabled(not busy)
        self.port_input.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.connect_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.start()
            self.connect_button.setText(self.controller.translator.t("pairing_progress"))
            self.error_label.hide()
        else:
            self.progress.stop()
            self.progress.hide()
            self.connect_button.setText(self.controller.translator.t("connect_button"))

    def _submit(self) -> None:
        if self._busy or self._done:
            return
        code = self.code_input.text().strip()
        t = self.controller.translator.t
        if len(code) != 6 or not code.isdigit():
            self._show_error(t("code_invalid"))
            self.code_input.setFocus()
            return
        host = self.host_input.text().strip() if self._manual else self._host
        port_text = self.port_input.text().strip() if self._manual else str(self._port)
        if not host:
            self._show_error(t("host_address_hint"))
            self.host_input.setFocus()
            return
        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self._show_error(t("err_invalid_port"))
            self.port_input.setFocus()
            return
        self._set_busy(True)
        self.controller.pair_with_code(
            host,
            port,
            code,
            expected_fingerprint=self._fingerprint,
            expected_device_id=self._device_id,
            on_success=self._on_success,
            on_error=self._on_error,
        )

    def _on_success(self, _result: object) -> None:
        self._done = True
        self._set_busy(False)
        self.accept()

    def _on_error(self, error: BaseException) -> None:
        self._set_busy(False)
        self._show_error(self.controller.error_message(error))

    def _show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.show()

    def reject(self) -> None:
        if self._busy and not self._done:
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._busy and not self._done:
            event.ignore()
            return
        super().closeEvent(event)


def decode_qr_pixmap(pixmap: QPixmap | QImage) -> str | None:
    image = pixmap.toImage() if isinstance(pixmap, QPixmap) else pixmap
    if image.isNull():
        return None
    try:
        import zxingcpp
        from PIL import Image

        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width = converted.width()
        height = converted.height()
        raw = converted.bits().tobytes()  # type: ignore[union-attr]
        pil = Image.frombuffer("RGBA", (width, height), raw, "raw", "RGBA", 0, 1)
        result = zxingcpp.read_barcode(pil)
        if result is not None and result.valid and result.text:
            return str(result.text)
    except Exception as exc:  # noqa: BLE001 - scanner must degrade gracefully
        log.debug("QR decode failed: %s", exc)
    return None


def decode_qr_file(path: str) -> str | None:
    try:
        import zxingcpp
        from PIL import Image

        with Image.open(path) as image:
            result = zxingcpp.read_barcode(image.convert("RGB"))
        if result is not None and result.valid and result.text:
            return str(result.text)
    except Exception as exc:  # noqa: BLE001
        log.debug("QR image decode failed: %s", exc)
    return None


class CameraPreview(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self.setMinimumSize(420, 300)
        self.setStyleSheet("background: #0B0E18; border-radius: 14px;")

    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.black)
        painter.drawRoundedRect(self.rect(), 14, 14)
        if self._image is not None and not self._image.isNull():
            scaled = self._image.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            painter.setClipPath(_rounded_path(self.rect(), 14))
            painter.drawImage(-x, -y, scaled)
        else:
            painter.setPen(QPen(Qt.GlobalColor.white))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "…")
        painter.setClipping(False)
        frame_size = min(self.width(), self.height()) * 0.58
        x = (self.width() - frame_size) / 2
        y = (self.height() - frame_size) / 2
        pen = QPen(QColor(255, 255, 255, 220), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        segment = 22
        for (sx, sy, dx, dy) in (
            (x, y, 1, 1),
            (x + frame_size, y, -1, 1),
            (x, y + frame_size, 1, -1),
            (x + frame_size, y + frame_size, -1, -1),
        ):
            painter.drawLine(int(sx), int(sy), int(sx + dx * segment), int(sy))
            painter.drawLine(int(sx), int(sy), int(sx), int(sy + dy * segment))


def _rounded_path(rect, radius: float):
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class QRScannerDialog(QDialog):
    def __init__(
        self,
        controller: ToRexLinkController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.payload: PairingPayload | None = None
        self._camera = None
        self._session = None
        self._sink = None
        self._last_image: QImage | None = None
        self._last_text: str | None = None
        self._camera_failed = False
        self.setWindowTitle(controller.translator.t("qr_scan_title"))
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._build()
        self._setup_camera()

    def _build(self) -> None:
        t = self.controller.translator.t
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        header = QVBoxLayout()
        header.setSpacing(4)
        self.title = label(t("qr_scan_title"), object_name="DialogTitle")
        self.subtitle = label(t("qr_scan_subtitle"), object_name="DialogSubtitle", wrap=True)
        header.addWidget(self.title)
        header.addWidget(self.subtitle)
        root.addLayout(header)
        self.preview = CameraPreview()
        root.addWidget(self.preview, 1)
        self.status = label(t("qr_waiting"), object_name="Faint", wrap=True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.status)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.image_button = make_button(t("qr_choose_image"), kind="SecondaryButton", icon_name="upload")
        self.image_button.clicked.connect(self._pick_image)
        controls.addWidget(self.image_button)
        controls.addStretch(1)
        self.cancel_button = make_button(t("cancel"), kind="GhostButton")
        self.cancel_button.clicked.connect(self.reject)
        controls.addWidget(self.cancel_button)
        root.addLayout(controls)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            "color: #F05A68; background: rgba(240,90,104,.10); border-radius: 8px; padding: 8px 10px;"
        )
        self.error_label.hide()
        root.addWidget(self.error_label)

    def _setup_camera(self) -> None:
        t = self.controller.translator.t
        try:
            from PySide6.QtMultimedia import (
                QCamera,
                QMediaCaptureSession,
                QMediaDevices,
                QVideoSink,
            )

            inputs = QMediaDevices.videoInputs()
            if not inputs:
                self._camera_unavailable(t("camera_unavailable"))
                return
            self._camera = QCamera(inputs[0], self)
            self._session = QMediaCaptureSession(self)
            self._sink = QVideoSink(self)
            self._session.setCamera(self._camera)
            self._session.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._on_video_frame)
            self._camera.errorOccurred.connect(self._on_camera_error)
            self._camera.start()
            self._timer = QTimer(self)
            self._timer.setInterval(280)
            self._timer.timeout.connect(self._scan_frame)
            self._timer.start()
            self.status.setText(t("qr_scan_hint"))
        except Exception as exc:  # noqa: BLE001
            log.info("Camera QR scanner unavailable: %s", exc)
            self._camera_unavailable(t("camera_unavailable"))

    def _camera_unavailable(self, message: str) -> None:
        self._camera_failed = True
        self.status.setText(message)
        self.preview.setToolTip(message)
        self.image_button.setFocus()

    def _on_video_frame(self, frame) -> None:
        try:
            image = frame.toImage()
            if not image.isNull():
                self._last_image = image
                self.preview.set_image(image)
        except Exception:  # noqa: BLE001
            pass

    def _on_camera_error(self, *args) -> None:
        self._camera_failed = True
        self.status.setText(self.controller.translator.t("camera_start_failed"))
        message = ""
        with __import__("contextlib").suppress(Exception):
            message = str(self._camera.errorString())
        if not message and len(args) > 1:
            message = str(args[-1])
        self.preview.setToolTip(message)
        if self._camera is not None:
            self._camera.stop()

    def _scan_frame(self) -> None:
        if self._last_image is None:
            return
        text = decode_qr_pixmap(self._last_image)
        if text:
            self._handle_text(text)

    def _pick_image(self) -> None:
        t = self.controller.translator.t
        path, _ = QFileDialog.getOpenFileName(
            self,
            t("qr_import_title"),
            str(Path.home()),
            t("qr_image_filter"),
        )
        if not path:
            return
        text = decode_qr_file(path)
        if not text:
            self._show_error(t("qr_invalid"))
            return
        self._handle_text(text)

    def _handle_text(self, text: str) -> None:
        if self.payload is not None:
            return
        if text == self._last_text:
            return
        self._last_text = text
        try:
            payload = PairingPayload.parse(text)
        except PairingPayloadError:
            self._show_error(self.controller.translator.t("qr_invalid"))
            return
        self.payload = payload
        self._show_error("")
        self.status.setText(self.controller.translator.t("qr_detected"))
        self._stop_camera()
        QTimer.singleShot(320, self.accept)

    def _stop_camera(self) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
        if self._camera is not None:
            self._camera.stop()

    def _show_error(self, text: str) -> None:
        if not text:
            self.error_label.hide()
            return
        self.error_label.setText(text)
        self.error_label.show()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_camera()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_camera()
        super().reject()


class ConfirmDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        title: str,
        text: str,
        confirm_text: str,
        cancel_text: str,
        danger: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        title_label = label(title, object_name="DialogTitle")
        body = label(text, object_name="DialogSubtitle", wrap=True)
        body.setMinimumHeight(40)
        root.addWidget(title_label)
        root.addWidget(body)
        root.addWidget(hline())
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel = make_button(cancel_text, kind="GhostButton")
        self.confirm = make_button(
            confirm_text,
            kind="DangerButton" if danger else "PrimaryButton",
        )
        self.cancel.clicked.connect(self.reject)
        self.confirm.clicked.connect(self.accept)
        row.addWidget(self.cancel)
        row.addWidget(self.confirm)
        root.addLayout(row)
        self.confirm.setFocus()

    @classmethod
    def ask(
        cls,
        parent: QWidget | None,
        *,
        title: str,
        text: str,
        confirm_text: str,
        cancel_text: str,
        danger: bool = True,
    ) -> bool:
        dialog = cls(
            parent,
            title=title,
            text=text,
            confirm_text=confirm_text,
            cancel_text=cancel_text,
            danger=danger,
        )
        return dialog.exec() == QDialog.DialogCode.Accepted


def load_icon(name: str, color: str, size: int = 16):
    from .icons import icon

    return icon(name, color, size)


__all__ = [
    "CameraPreview",
    "ConfirmDialog",
    "JoinDialog",
    "QRScannerDialog",
    "decode_qr_file",
    "decode_qr_pixmap",
]
