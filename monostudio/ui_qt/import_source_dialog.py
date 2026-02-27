"""
Import Source Dialog — allows user to browse or drag-drop a source file,
previews the target pipeline-named path, and confirms the import.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog


class _DropZone(QWidget):
    """Dashed-border area that accepts drag-drop and click-to-browse."""

    file_dropped = Signal(str)

    def __init__(self, allowed_extensions: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._allowed_exts = [e.lower() for e in (allowed_extensions or [])]
        self.setAcceptDrops(True)
        self.setMinimumSize(360, 140)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)

        icon_label = QLabel(self)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ico = lucide_icon("upload", size=36, color_hex=MONOS_COLORS.get("text_meta", "#71717a"))
        icon_label.setPixmap(ico.pixmap(36, 36))
        lay.addWidget(icon_label)

        text = QLabel("Drop file here or click Browse", self)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setStyleSheet(f"color: {MONOS_COLORS.get('text_label', '#a1a1aa')}; font-size: 13px; font-weight: 500;")
        lay.addWidget(text)

        if self._allowed_exts:
            ext_hint = QLabel(", ".join(self._allowed_exts), self)
            ext_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ext_hint.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 11px;")
            lay.addWidget(ext_hint)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(MONOS_COLORS.get("surface", "#27272a")))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(2)
        pen.setDashPattern([6, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.drawRoundedRect(r, 10, 10)
        p.end()

    def _is_valid(self, path: str) -> bool:
        if not self._allowed_exts:
            return True
        return Path(path).suffix.lower() in self._allowed_exts

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        md = event.mimeData()
        if md and md.hasUrls():
            for url in md.urls():
                if url.isLocalFile() and self._is_valid(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        md = event.mimeData()
        if md and md.hasUrls():
            for url in md.urls():
                fp = url.toLocalFile()
                if fp and self._is_valid(fp):
                    self.file_dropped.emit(fp)
                    event.acceptProposedAction()
                    return
        event.ignore()


class ImportSourceDialog(MonosDialog):
    """
    Dialog for importing a source file into the pipeline work folder.

    Shows a drag-drop zone + browse button, then previews:
      - Source: original file path
      - Target: pipeline-named path in work folder

    Returns the source path on accept, or None on cancel.
    """

    def __init__(
        self,
        *,
        target_path: Path,
        allowed_extensions: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Source File")
        self.setModal(True)
        self.setMinimumWidth(440)

        self._target_path = target_path
        self._source_path: str | None = None
        self._allowed_exts = allowed_extensions or []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Import Source File", self)
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #fafafa;")
        root.addWidget(title)

        hint = QLabel(
            "Select a file to copy into the work folder with the correct pipeline name.",
            self,
        )
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._drop_zone = _DropZone(allowed_extensions=self._allowed_exts, parent=self)
        self._drop_zone.file_dropped.connect(self._set_source)
        root.addWidget(self._drop_zone)

        browse_row = QWidget(self)
        browse_l = QHBoxLayout(browse_row)
        browse_l.setContentsMargins(0, 0, 0, 0)
        browse_l.setSpacing(8)
        browse_btn = QPushButton("Browse…", self)
        browse_btn.setObjectName("DialogSecondaryButton")
        browse_btn.clicked.connect(self._browse)
        browse_l.addWidget(browse_btn)
        browse_l.addStretch(1)
        root.addWidget(browse_row)

        self._preview_widget = QWidget(self)
        preview_l = QVBoxLayout(self._preview_widget)
        preview_l.setContentsMargins(0, 0, 0, 0)
        preview_l.setSpacing(6)

        src_label = QLabel("Source", self)
        src_label.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;")
        preview_l.addWidget(src_label)

        self._source_display = QLabel("—", self)
        self._source_display.setWordWrap(True)
        self._source_display.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_primary', '#cccccc')}; font-family: 'JetBrains Mono'; font-size: 12px; padding: 6px 8px; "
            f"background: {MONOS_COLORS.get('content_bg', '#121214')}; border-radius: 6px;"
        )
        preview_l.addWidget(self._source_display)

        tgt_label = QLabel("Target", self)
        tgt_label.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;")
        preview_l.addWidget(tgt_label)

        self._target_display = QLabel(str(self._target_path), self)
        self._target_display.setWordWrap(True)
        self._target_display.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_primary', '#cccccc')}; font-family: 'JetBrains Mono'; font-size: 12px; padding: 6px 8px; "
            f"background: {MONOS_COLORS.get('content_bg', '#121214')}; border-radius: 6px;"
        )
        preview_l.addWidget(self._target_display)

        self._preview_widget.setVisible(False)
        root.addWidget(self._preview_widget)

        button_row = QWidget(self)
        btn_l = QHBoxLayout(button_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(10)

        self._btn_confirm = QPushButton("Import", self)
        self._btn_confirm.setObjectName("DialogPrimaryButton")
        self._btn_confirm.setDefault(True)
        self._btn_confirm.setEnabled(False)
        self._btn_confirm.clicked.connect(self._on_accept)

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)

        btn_l.addWidget(self._btn_confirm)
        btn_l.addWidget(cancel_btn)
        btn_l.addStretch(1)
        root.addWidget(button_row)

    def source_path(self) -> str | None:
        return self._source_path

    def _browse(self) -> None:
        ext_filter = ""
        if self._allowed_exts:
            exts = " ".join(f"*{e}" for e in self._allowed_exts)
            ext_filter = f"Supported files ({exts});;All files (*)"
        else:
            ext_filter = "All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Source File", "", ext_filter)
        if path:
            self._set_source(path)

    def _set_source(self, path: str) -> None:
        src = Path(path)
        if not src.is_file():
            return
        self._source_path = str(src)
        self._source_display.setText(str(src))

        target = self._target_path
        if target.suffix.lower() != src.suffix.lower():
            target = target.with_suffix(src.suffix)
            self._target_path = target
        self._target_display.setText(str(target))

        self._preview_widget.setVisible(True)
        self._btn_confirm.setEnabled(True)
        self._drop_zone.setVisible(False)

    def _on_accept(self) -> None:
        if self._source_path:
            self.accept()
