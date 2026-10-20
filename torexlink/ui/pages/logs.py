"""Rotating log viewer with search and export."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...constants import REMOTE_LOGS_MIN_VERSION
from ...logging_setup import read_log_tail
from ...util import version_at_least
from ..widgets import (
    Card,
    PageHeader,
    Pill,
    label,
    make_button,
)

if TYPE_CHECKING:
    from ...core.service import RemoteLogs
    from ..controller import ToRexLinkController


class RemoteLogsDialog(QDialog):
    """Read-only viewer for logs fetched from the paired computer."""

    def __init__(
        self,
        controller: ToRexLinkController,
        result: RemoteLogs,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        t = controller.translator.t
        self.setWindowTitle(t("logs_remote_title").format(peer=result.peer_name))
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        meta = label(
            f"{result.peer_name} · {result.app_version} · "
            + t("logs_remote_saved").format(path=result.saved_path),
            object_name="Faint",
            wrap=True,
        )
        layout.addWidget(meta)
        if result.truncated:
            layout.addWidget(label(t("logs_remote_truncated"), object_name="Faint", wrap=True))
        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setPlainText(result.text or t("logs_empty"))
        layout.addWidget(self.view, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy_button = make_button(t("copy"), kind="SecondaryButton", icon_name="copy")
        save_button = make_button(t("save"), kind="SecondaryButton", icon_name="download")
        close_button = make_button(t("close"), kind="PrimaryButton")
        copy_button.clicked.connect(self._copy)
        save_button.clicked.connect(self._save)
        close_button.clicked.connect(self.accept)
        for button in (copy_button, save_button, close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def _copy(self) -> None:
        self.controller.copy_text(self.view.toPlainText())

    def _save(self) -> None:
        t = self.controller.translator.t
        path, _ = QFileDialog.getSaveFileName(
            self,
            t("save"),
            str(Path.home() / "ToRexLink-remote-log.txt"),
            t("logs_file_filter"),
        )
        if not path:
            return
        try:
            Path(path).write_text(self.view.toPlainText(), encoding="utf-8")
        except OSError:
            pass


class LogsPage(QWidget):
    toast = Signal(str, str)

    def __init__(self, controller: ToRexLinkController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._raw = ""
        self._log_signature: tuple[int, int] | None = None
        self._build()
        self._wire()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start()
        self.refresh()

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

        action_row = QWidget()
        actions = QHBoxLayout(action_row)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(7)
        self.open_button = make_button("", kind="GhostButton", icon_name="folder")
        self.remote_button = make_button("", kind="SecondaryButton", icon_name="download")
        self.export_button = make_button("", kind="SecondaryButton", icon_name="download")
        self.copy_button = make_button("", kind="SecondaryButton", icon_name="copy")
        self.refresh_button = make_button("", kind="PrimaryButton", icon_name="refresh")
        for button in (
            self.open_button,
            self.remote_button,
            self.export_button,
            self.copy_button,
            self.refresh_button,
        ):
            actions.addWidget(button)
        self.header = PageHeader("", "", action=action_row)
        layout.addWidget(self.header)

        card = Card(margins=(16, 14, 16, 14), spacing=10)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("SearchInput")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, 1)
        self.live_pill = Pill("", "success")
        toolbar.addWidget(self.live_pill)
        card.body.addLayout(toolbar)
        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMinimumHeight(430)
        card.body.addWidget(self.view, 1)
        self.caption = label(object_name="Faint")
        card.body.addWidget(self.caption)
        layout.addWidget(card, 1)

    def _wire(self) -> None:
        self.refresh_button.clicked.connect(lambda: self.refresh(force=True))
        self.copy_button.clicked.connect(self._copy)
        self.export_button.clicked.connect(self._export)
        self.open_button.clicked.connect(self._open_folder)
        self.remote_button.clicked.connect(self._fetch_remote)
        self.search.textChanged.connect(self._apply_filter)

    def retranslate(self) -> None:
        t = self.controller.translator.t
        self.header.title.setText(t("logs_title"))
        self.header.subtitle.setText(t("logs_subtitle"))
        self.refresh_button.setText(t("logs_refresh"))
        self.copy_button.setText(t("logs_copy"))
        self.export_button.setText(t("logs_export"))
        self.open_button.setText(t("logs_open_folder"))
        self.remote_button.setText(t("logs_remote"))
        self.search.setPlaceholderText(t("logs_search_placeholder"))
        self.live_pill.setText(t("diagnostic_active"))
        self.caption.setText(t("logs_auto_refresh"))
        self.view.setPlaceholderText(t("logs_empty"))

    def _auto_refresh(self) -> None:
        if self.isVisible():
            self.refresh(scroll=False)

    def refresh(self, *, scroll: bool = True, force: bool = False) -> None:
        log_file = self.controller.service.paths.log_file
        try:
            stat = log_file.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        if not force and signature is not None and signature == self._log_signature:
            return
        self._log_signature = signature
        self._raw = read_log_tail(log_file, max_lines=800)
        self._apply_filter(scroll=scroll)

    def _apply_filter(self, *_args, scroll: bool = False) -> None:
        query = self.search.text().strip().lower()
        if query:
            text = "\n".join(line for line in self._raw.splitlines() if query in line.lower())
        else:
            text = self._raw
        self.view.setPlainText(text)
        if scroll:
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _copy(self) -> None:
        self.controller.copy_text(self.view.toPlainText())
        self.toast.emit(self.controller.translator.t("logs_copied"), "success")

    def _export(self) -> None:
        t = self.controller.translator.t
        path, _ = QFileDialog.getSaveFileName(
            self,
            t("logs_export"),
            str(Path.home() / "ToRexLink-log.txt"),
            t("logs_file_filter"),
        )
        if not path:
            return
        try:
            Path(path).write_text(self.view.toPlainText(), encoding="utf-8")
            self.toast.emit(t("logs_exported"), "success")
        except OSError:
            self.toast.emit(t("toast_export_failed"), "error")

    def _open_folder(self) -> None:
        if not self.controller.open_path(self.controller.service.paths.logs_dir):
            self.toast.emit(self.controller.translator.t("logs_open_error"), "error")

    def _fetch_remote(self) -> None:
        t = self.controller.translator.t
        connection = self.controller.snapshot.connection
        if connection is None:
            self.toast.emit(t("logs_remote_no_connection"), "info")
            return
        if not version_at_least(connection.app_version, REMOTE_LOGS_MIN_VERSION):
            self.toast.emit(t("logs_remote_update_required"), "error")
            return
        self.remote_button.setEnabled(False)
        self.remote_button.setText(t("logs_remote_loading"))
        self.controller.fetch_remote_logs(self._remote_loaded, self._remote_failed)

    def _remote_loaded(self, result: object) -> None:
        t = self.controller.translator.t
        self.remote_button.setEnabled(True)
        self.remote_button.setText(t("logs_remote"))
        text = getattr(result, "text", None)
        if not isinstance(text, str):
            self.toast.emit(t("logs_remote_failed"), "error")
            return
        RemoteLogsDialog(self.controller, result, self).exec()  # type: ignore[arg-type]

    def _remote_failed(self, error: BaseException) -> None:
        self.remote_button.setEnabled(True)
        self.remote_button.setText(self.controller.translator.t("logs_remote"))
        self.toast.emit(self.controller.error_message(error), "error")


__all__ = ["LogsPage", "RemoteLogsDialog"]
