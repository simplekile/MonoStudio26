"""Read-only full note viewer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout

from monostudio.core.item_comments import ItemCommentEntry
from monostudio.ui_qt.note_author_row import NoteAuthorRow
from monostudio.ui_qt.note_body_browser import NoteBodyBrowser
from monostudio.ui_qt.note_seen_by_label import note_seen_by_label
from monostudio.ui_qt.style import MonosDialog, monos_font


def _format_local_time(iso_at: str) -> str:
    from datetime import datetime, timezone

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


class NoteViewDialog(MonosDialog):
    time_anchor_clicked = Signal(str)

    def __init__(
        self,
        *,
        entry: ItemCommentEntry,
        item_root: Path,
        item_display_name: str,
        workspace_root: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Note")
        self.setModal(True)
        self.setObjectName("NoteViewDialog")
        self.setMinimumSize(520, 420)
        self.resize(640, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"Note — {item_display_name}", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 15, QFont.Weight.DemiBold))
        root.addWidget(title)

        time_meta = _format_local_time(entry.at)
        if entry.done:
            time_meta = f"{time_meta} · Completed"
        uid = (entry.author_id or "").strip()
        on_author = None
        if uid:

            def _open_profile(u: str = uid) -> None:
                from monostudio.ui_qt.user_profile_view_dialog import open_studio_user_profile

                open_studio_user_profile(workspace_root, u, parent=self)

            on_author = _open_profile

        root.addWidget(
            NoteAuthorRow.for_entry(
                entry,
                workspace_root,
                avatar_size=36,
                time_text=time_meta,
                elide_name=False,
                time_on_right=True,
                on_author_click=on_author,
                parent=self,
            )
        )

        line = QFrame(self)
        line.setObjectName("ItemNotesHRule")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        root.addWidget(line)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("ItemNotesScroll")

        body = NoteBodyBrowser(
            item_root=item_root,
            workspace_root=workspace_root,
            parent=scroll,
        )
        body.set_body(entry.body_html, plain_fallback=entry.text, done=entry.done)
        body.setMinimumHeight(max(120, body.document().size().height()))
        body.time_anchor_clicked.connect(self.time_anchor_clicked.emit)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        seen_lab = note_seen_by_label(entry, workspace_root, self)
        if seen_lab is not None:
            root.addWidget(seen_lab, 0)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogSecondaryButton")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
