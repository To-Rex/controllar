"""Rotating log viewer with search and export."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...logging_setup import read_log_tail
from ..widgets import (
    Card,
    PageHeader,
    Pill,
    label,
    make_button,
)

if TYPE_CHECKING:
    from ..controller import ToRexLinkController


class LogsPage(QWidget):
    toast = Signal(str, str)

    def __init__(self, controller: ToRexLinkController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._raw = ""
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
        self.export_button = make_button("", kind="SecondaryButton", icon_name="download")
        self.copy_button = make_button("", kind="SecondaryButton", icon_name="copy")
        self.refresh_button = make_button("", kind="PrimaryButton", icon_name="refresh")
        for button in (self.open_button, self.export_button, self.copy_button, self.refresh_button):
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
        self.refresh_button.clicked.connect(self.refresh)
        self.copy_button.clicked.connect(self._copy)
        self.export_button.clicked.connect(self._export)
        self.open_button.clicked.connect(self._open_folder)
        self.search.textChanged.connect(self._apply_filter)

    def retranslate(self) -> None:
        t = self.controller.translator.t
        self.header.title.setText(t("logs_title"))
        self.header.subtitle.setText(t("logs_subtitle"))
        self.refresh_button.setText(t("logs_refresh"))
        self.copy_button.setText(t("logs_copy"))
        self.export_button.setText(t("logs_export"))
        self.open_button.setText(t("logs_open_folder"))
        self.search.setPlaceholderText(t("logs_search_placeholder"))
        self.live_pill.setText(t("diagnostic_active"))
        self.caption.setText(t("logs_auto_refresh"))
        self.view.setPlaceholderText(t("logs_empty"))

    def _auto_refresh(self) -> None:
        if self.isVisible():
            self.refresh(scroll=False)

    def refresh(self, *, scroll: bool = True) -> None:
        self._raw = read_log_tail(self.controller.service.paths.log_file, max_lines=800)
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


__all__ = ["LogsPage"]
