"""Dialog listing prior versions of a note (edit history)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.item_comments import NoteEditRevision, strip_html_preview
from monostudio.ui_qt.style import MonosDialog, monos_font


def _format_local_time(iso_at: str) -> str:
    s = (iso_at or "").strip()
    if not s:
        return "—"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%H:%M %d/%m/%Y")
    except ValueError:
        return s[:16] if len(s) >= 16 else s


class NoteEditHistoryDialog(MonosDialog):
    def __init__(
        self,
        *,
        entry: ItemCommentEntry,
        workspace_root: Path | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit history")
        self.setModal(True)
        self.setObjectName("NoteEditHistoryDialog")
        self.setMinimumSize(480, 360)
        self.resize(520, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Edit history", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 16, QFont.Weight.Bold))
        root.addWidget(title)

        hint = QLabel(
            f"{len(entry.edit_history)} revision{'s' if len(entry.edit_history) != 1 else ''} "
            "before the current text.",
            self,
        )
        hint.setObjectName("DialogHelper")
        root.addWidget(hint)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        for rev in entry.edit_history:
            lay.addWidget(self._revision_card(rev, workspace_root))

        lay.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogPrimaryButton")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

    def _revision_card(self, rev: NoteEditRevision, workspace_root: Path | None) -> QWidget:
        card = QWidget(self)
        card.setObjectName("ItemNotesCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(12, 10, 12, 10)
        card_l.setSpacing(6)

        meta = QLabel(
            f"{rev.editor} · {_format_local_time(rev.at)}",
            card,
        )
        meta.setObjectName("ItemNotesMeta")
        meta.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        card_l.addWidget(meta)

        preview = strip_html_preview(rev.body_html) if rev.body_html else rev.text
        if len(preview) > 400:
            preview = preview[:399].rstrip() + "…"
        body = QLabel(preview or "—", card)
        body.setWordWrap(True)
        body.setObjectName("DialogHint")
        body.setFont(monos_font("Inter", 12, QFont.Weight.Normal))
        card_l.addWidget(body)
        return card
