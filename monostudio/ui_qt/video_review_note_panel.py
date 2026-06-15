"""Quick note compose for video review (entity context)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from monostudio.core.item_comments import append_item_comment
from monostudio.ui_qt.style import monos_font


class VideoReviewNotePanel(QWidget):
    """Minimal note at current frame — full history via ItemNotesDialog."""

    open_all_notes_requested = Signal()
    note_added = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewNotePanel")
        self._entity_path: Path | None = None
        self._frame_hint = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self._frame_label = QLabel("", self)
        self._frame_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        lay.addWidget(self._frame_label)

        self._editor = QTextEdit(self)
        self._editor.setObjectName("DialogLineEdit")
        self._editor.setPlaceholderText("Note at this frame…")
        self._editor.setMaximumHeight(120)
        lay.addWidget(self._editor, 1)

        row = QHBoxLayout()
        self._btn_add = QPushButton("Add note", self)
        self._btn_add.setObjectName("DialogPrimaryButton")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_all = QPushButton("Open all notes…", self)
        self._btn_all.setObjectName("DialogSecondaryButton")
        self._btn_all.clicked.connect(self.open_all_notes_requested.emit)
        row.addWidget(self._btn_add)
        row.addWidget(self._btn_all)
        lay.addLayout(row)

    def set_entity(self, entity_path: Path | None) -> None:
        self._entity_path = Path(entity_path) if entity_path else None

    def set_frame_hint(self, text: str) -> None:
        self._frame_hint = (text or "").strip()
        self._frame_label.setText(self._frame_hint or "—")

    def _on_add(self) -> None:
        if self._entity_path is None or not self._entity_path.is_dir():
            return
        body = self._editor.toPlainText().strip()
        if not body:
            return
        prefix = self._frame_hint
        text = f"[{prefix}] {body}" if prefix else body
        try:
            append_item_comment(self._entity_path, text)
        except OSError:
            return
        self._editor.clear()
        self.note_added.emit()
