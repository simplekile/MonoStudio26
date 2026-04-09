"""
Import Source Dialog — allows user to browse or drag-drop a source file,
paste a file path (including from clipboard), previews the target pipeline-named path,
and confirms the import.
"""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QPainter,
    QPen,
    QShowEvent,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog


def _normalize_allowed_extensions(exts: list[str]) -> list[str]:
    out: list[str] = []
    for e in exts:
        s = (e or "").strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        out.append(s)
    return out


def _path_preview_plain(parent: QWidget | None, *, text_color_hex: str) -> QPlainTextEdit:
    """Read-only path preview: wrap long paths (no spaces) and scroll vertically."""
    w = QPlainTextEdit(parent)
    w.setReadOnly(True)
    w.setFrameShape(QFrame.Shape.NoFrame)
    w.setUndoRedoEnabled(False)
    w.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    w.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    w.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
    w.document().setDefaultTextOption(opt)
    w.setMinimumHeight(56)
    w.setMaximumHeight(140)
    w.setTabChangesFocus(True)
    w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    bg = MONOS_COLORS.get("content_bg", "#121214")
    w.setStyleSheet(
        f"QPlainTextEdit {{ background-color: {bg}; color: {text_color_hex}; "
        f'font-family: "JetBrains Mono", "Consolas", monospace; font-size: 12px; '
        f"padding: 6px 8px; border: none; border-radius: 6px; }}"
    )
    return w


def _normalize_path_candidate(text: str) -> str | None:
    """Strip clipboard noise; accept plain paths and file:// URLs."""
    t = (text or "").strip()
    if not t:
        return None
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    if not t:
        return None
    if t.lower().startswith("file:"):
        u = QUrl(t)
        if u.isLocalFile():
            t = u.toLocalFile()
        else:
            return None
    return t or None


class _DropZone(QWidget):
    """Dashed-border area that accepts drag-drop and click-to-browse."""

    file_dropped = Signal(str)

    def __init__(self, allowed_extensions: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._allowed_exts = _normalize_allowed_extensions(list(allowed_extensions or []))
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

    Shows file path field (paste from clipboard), drag-drop zone, browse, then previews:
      - Source: original file path
      - Target: pipeline-named path in work folder
      - Filename: old name → new name (distinct colors)

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
        self.setMinimumWidth(520)

        self._target_path = target_path
        self._source_path: str | None = None
        self._allowed_exts = _normalize_allowed_extensions(list(allowed_extensions or []))

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

        path_label = QLabel("File path", self)
        path_label.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 11px; font-weight: 600; "
            "text-transform: uppercase; letter-spacing: 1px;"
        )
        root.addWidget(path_label)

        path_row = QWidget(self)
        path_row_l = QHBoxLayout(path_row)
        path_row_l.setContentsMargins(0, 0, 0, 0)
        path_row_l.setSpacing(8)

        self._path_edit = QLineEdit(self)
        self._path_edit.setPlaceholderText("Paste path or browse…")
        self._path_edit.setProperty("mono", True)
        self._path_edit.returnPressed.connect(self._apply_path_from_field)
        self._path_edit.editingFinished.connect(self._on_path_editing_finished)
        path_row_l.addWidget(self._path_edit, 1)

        paste_btn = QPushButton("Paste", self)
        paste_btn.setObjectName("DialogSecondaryButton")
        paste_btn.setToolTip("Paste path from clipboard")
        paste_btn.clicked.connect(self._paste_from_clipboard)
        path_row_l.addWidget(paste_btn)

        browse_btn = QPushButton("Browse…", self)
        browse_btn.setObjectName("DialogSecondaryButton")
        browse_btn.clicked.connect(self._browse)
        path_row_l.addWidget(browse_btn)

        root.addWidget(path_row)

        self._path_error = QLabel("", self)
        self._path_error.setObjectName("DialogWarning")
        self._path_error.setWordWrap(True)
        self._path_error.setVisible(False)
        root.addWidget(self._path_error)

        self._drop_zone = _DropZone(
            allowed_extensions=self._allowed_exts if self._allowed_exts else None,
            parent=self,
        )
        self._drop_zone.file_dropped.connect(self._set_source_from_path)
        root.addWidget(self._drop_zone)

        self._preview_widget = QWidget(self)
        preview_l = QVBoxLayout(self._preview_widget)
        preview_l.setContentsMargins(0, 0, 0, 0)
        preview_l.setSpacing(6)

        names_label = QLabel("Filename", self)
        names_label.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 11px; font-weight: 600; "
            "text-transform: uppercase; letter-spacing: 1px;"
        )
        preview_l.addWidget(names_label)

        self._name_compare = QLabel("", self)
        self._name_compare.setWordWrap(True)
        self._name_compare.setTextFormat(Qt.TextFormat.RichText)
        self._name_compare.setObjectName("ImportSourceNameCompare")
        preview_l.addWidget(self._name_compare)

        src_label = QLabel("Source", self)
        src_label.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 11px; font-weight: 600; "
            "text-transform: uppercase; letter-spacing: 1px;"
        )
        preview_l.addWidget(src_label)

        self._source_display = _path_preview_plain(
            self, text_color_hex=MONOS_COLORS.get("text_primary", "#cccccc")
        )
        self._source_display.setPlainText("—")
        preview_l.addWidget(self._source_display)

        tgt_label = QLabel("Target", self)
        tgt_label.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 11px; font-weight: 600; "
            "text-transform: uppercase; letter-spacing: 1px;"
        )
        preview_l.addWidget(tgt_label)

        self._target_display = _path_preview_plain(
            self, text_color_hex=MONOS_COLORS.get("blue_400", "#60a5fa")
        )
        self._target_display.setPlainText(str(self._target_path))
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

        self._auto_clipboard_attempted = False

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._auto_clipboard_attempted:
            return
        self._auto_clipboard_attempted = True
        if self._source_path is not None:
            return
        if self._path_edit.text().strip():
            return
        self._try_auto_fill_from_clipboard()

    def source_path(self) -> str | None:
        return self._source_path

    def _clear_path_error(self) -> None:
        self._path_error.setText("")
        self._path_error.setVisible(False)

    def _set_path_error(self, message: str) -> None:
        self._path_error.setText(message)
        self._path_error.setVisible(True)

    def _sync_path_edit(self, path: str) -> None:
        self._path_edit.blockSignals(True)
        self._path_edit.setText(path)
        self._path_edit.blockSignals(False)

    def _ext_ok(self, suffix: str) -> bool:
        if not self._allowed_exts:
            return True
        s = suffix.lower()
        if not s.startswith("."):
            s = "." + s
        return s in self._allowed_exts

    def _try_auto_fill_from_clipboard(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        cb = app.clipboard()
        if cb is None:
            return
        raw = cb.text()
        normalized = _normalize_path_candidate(raw)
        if not normalized:
            return
        p = Path(normalized)
        try:
            p = p.resolve()
        except OSError:
            return
        if not p.is_file():
            return
        if not self._ext_ok(p.suffix):
            return
        self._sync_path_edit(str(p))
        if self._set_source_from_path(str(p), from_clipboard_auto=True):
            self._clear_path_error()

    def _paste_from_clipboard(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        cb = app.clipboard()
        if cb is None:
            return
        raw = cb.text()
        normalized = _normalize_path_candidate(raw)
        if not normalized:
            self._set_path_error("Clipboard does not contain a usable file path.")
            return
        self._sync_path_edit(normalized)
        self._apply_path_from_field()

    def _on_path_editing_finished(self) -> None:
        if not self._path_edit.text().strip():
            self._reset_to_empty()
            return
        self._apply_path_from_field()

    def _reset_to_empty(self) -> None:
        self._source_path = None
        self._clear_path_error()
        self._preview_widget.setVisible(False)
        self._btn_confirm.setEnabled(False)
        self._drop_zone.setVisible(True)
        self._name_compare.setText("")

    def _apply_path_from_field(self) -> None:
        t = self._path_edit.text().strip()
        if not t:
            self._reset_to_empty()
            return
        normalized = _normalize_path_candidate(t)
        if not normalized:
            self._set_path_error("Could not parse path.")
            self._invalidate_source_keep_field()
            return
        p = Path(normalized)
        try:
            p = p.resolve()
        except OSError as e:
            self._set_path_error(f"Invalid path: {e}")
            self._invalidate_source_keep_field()
            return
        if not p.is_file():
            self._set_path_error("File does not exist.")
            self._invalidate_source_keep_field()
            return
        if not self._ext_ok(p.suffix):
            allowed = ", ".join(self._allowed_exts) if self._allowed_exts else "any"
            self._set_path_error(f"Extension not allowed for this DCC. Expected: {allowed}")
            self._invalidate_source_keep_field()
            return
        self._clear_path_error()
        self._sync_path_edit(str(p))
        self._set_source_from_path(str(p))

    def _invalidate_source_keep_field(self) -> None:
        self._source_path = None
        self._preview_widget.setVisible(False)
        self._btn_confirm.setEnabled(False)
        self._drop_zone.setVisible(True)
        self._name_compare.setText("")

    def _browse(self) -> None:
        ext_filter = ""
        if self._allowed_exts:
            exts = " ".join(f"*{e}" for e in self._allowed_exts)
            ext_filter = f"Supported files ({exts});;All files (*)"
        else:
            ext_filter = "All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Source File", "", ext_filter)
        if path:
            try:
                resolved = str(Path(path).resolve())
            except OSError:
                resolved = path
            self._sync_path_edit(resolved)
            self._set_source_from_path(resolved)

    def _set_source_from_path(self, path: str, *, from_clipboard_auto: bool = False) -> bool:
        src = Path(path)
        try:
            if not src.is_file():
                if not from_clipboard_auto:
                    self._set_path_error("File does not exist.")
                    self._invalidate_source_keep_field()
                return False
        except OSError:
            if not from_clipboard_auto:
                self._set_path_error("Could not access file.")
                self._invalidate_source_keep_field()
            return False
        if not self._ext_ok(src.suffix):
            if not from_clipboard_auto:
                allowed = ", ".join(self._allowed_exts) if self._allowed_exts else "any"
                self._set_path_error(f"Extension not allowed for this DCC. Expected: {allowed}")
                self._invalidate_source_keep_field()
            return False

        try:
            src_resolved = str(src.resolve())
        except OSError:
            src_resolved = str(src)

        self._source_path = src_resolved
        self._sync_path_edit(src_resolved)
        self._source_display.setPlainText(src_resolved)

        target = self._target_path
        if target.suffix.lower() != src.suffix.lower():
            target = target.with_suffix(src.suffix)
            self._target_path = target
        self._target_display.setPlainText(str(target))

        old_name = html.escape(src.name)
        new_name = html.escape(target.name)
        old_c = MONOS_COLORS.get("text_label", "#a1a1aa")
        arrow_c = MONOS_COLORS.get("text_meta", "#71717a")
        new_c = MONOS_COLORS.get("blue_400", "#60a5fa")
        self._name_compare.setText(
            f'<span style="color:{old_c}; font-weight:600; font-family: JetBrains Mono, Consolas, monospace; font-size:12px;">{old_name}</span>'
            f' <span style="color:{arrow_c}; font-size:12px;">→</span> '
            f'<span style="color:{new_c}; font-weight:600; font-family: JetBrains Mono, Consolas, monospace; font-size:12px;">{new_name}</span>'
        )

        self._preview_widget.setVisible(True)
        self._btn_confirm.setEnabled(True)
        self._drop_zone.setVisible(False)
        self._clear_path_error()
        return True

    def _on_accept(self) -> None:
        if self._path_edit.text().strip():
            self._apply_path_from_field()
        if self._source_path:
            self.accept()
