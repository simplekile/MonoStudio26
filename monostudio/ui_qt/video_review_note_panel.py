"""Quick note compose + list for video review (entity context)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.item_comments import (
    ItemCommentEntry,
    entry_preview_text,
    new_comment_entry,
    normalize_note_department_id,
    read_item_comments_for_department,
    record_note_seen,
    write_item_comments_for_department,
)
from monostudio.core.mention_inbox import append_mentions
from monostudio.core.user_identity import get_current_user
from monostudio.core.video_media import format_frame_label, format_timecode
from monostudio.ui_qt.note_author_row import NoteAuthorRow
from monostudio.ui_qt.note_body_browser import NoteListPreviewLabel
from monostudio.ui_qt.note_compose_editor import NoteComposeEditor
from monostudio.ui_qt.note_done_toggle import NoteDoneToggleButton
from monostudio.ui_qt.note_seen_by_label import note_seen_by_label
from monostudio.ui_qt.note_view_dialog import NoteViewDialog
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


class _ReviewNoteCard(QFrame):
    open_requested = Signal(str)

    def __init__(self, entry: ItemCommentEntry, *, frame_match: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setObjectName("ItemNotesCardDone" if entry.done else "ItemNotesCard")
        self.setProperty("noteCardId", entry.id)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to view full note")
        if frame_match:
            self.setProperty("frameMatch", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit(self._entry.id)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class VideoReviewNotePanel(QWidget):
    """Frame-tagged notes at playhead — full history via ItemNotesDialog."""

    open_all_notes_requested = Signal()
    note_added = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewNotePanel")
        self._entity_path: Path | None = None
        self._department_id: str | None = None
        self._department_label = ""
        self._workspace_root: Path | None = None
        self._project_root: Path | None = None
        self._item_display_name = ""
        self._frame = 0
        self._fps = 24.0
        self._frame_hint = ""
        self._entries: list[ItemCommentEntry] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)

        header = QVBoxLayout()
        header.setSpacing(2)
        self._context_label = QLabel("", self)
        self._context_label.setObjectName("VideoReviewDrawSectionTitle")
        self._context_label.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        header.addWidget(self._context_label)
        self._frame_label = QLabel("", self)
        self._frame_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._frame_label.setStyleSheet(f"color: {MONOS_COLORS.get('text_label', '#a1a1aa')};")
        header.addWidget(self._frame_label)
        self._summary_label = QLabel("", self)
        self._summary_label.setObjectName("DialogHint")
        header.addWidget(self._summary_label)
        lay.addLayout(header)

        compose_title = QLabel("New note", self)
        compose_title.setObjectName("DialogHint")
        compose_title.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        lay.addWidget(compose_title)

        self._compose_host = QWidget(self)
        compose_lay = QVBoxLayout(self._compose_host)
        compose_lay.setContentsMargins(0, 0, 0, 0)
        compose_lay.setSpacing(6)
        self._editor = NoteComposeEditor(item_root=Path("."), workspace_root=None, parent=self._compose_host)
        self._editor.setMinimumHeight(88)
        self._editor.setMaximumHeight(160)
        self._editor.setPlaceholderText("Note at playhead… (@mention · paste images)")
        compose_lay.addWidget(self._editor)
        lay.addWidget(self._compose_host)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_add = QPushButton("Add note", self)
        self._btn_add.setObjectName("DialogPrimaryButton")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_all = QPushButton("All notes…", self)
        self._btn_all.setObjectName("DialogSecondaryButton")
        self._btn_all.clicked.connect(self.open_all_notes_requested.emit)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_all)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        list_title = QLabel("Notes", self)
        list_title.setObjectName("VideoReviewDrawSectionTitle")
        lay.addWidget(list_title)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("VideoReviewNoteListScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_host = QWidget(self._scroll)
        self._list_host.setObjectName("VideoReviewNoteListHost")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        lay.addWidget(self._scroll, 1)

        hint = QLabel("Ctrl+Enter add · Notes tag current frame", self)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self._refresh_compose_enabled()

    def set_entity(self, entity_path: Path | None) -> None:
        self.set_context(
            entity_path,
            department_id=self._department_id,
            department_label=self._department_label,
            workspace_root=self._workspace_root,
            project_root=self._project_root,
            item_display_name=self._item_display_name,
        )

    def set_context(
        self,
        entity_path: Path | None,
        *,
        department_id: str | None = None,
        department_label: str | None = None,
        workspace_root: Path | None = None,
        project_root: Path | None = None,
        item_display_name: str = "",
    ) -> None:
        self._entity_path = Path(entity_path) if entity_path else None
        self._department_id = normalize_note_department_id(department_id) or None
        self._department_label = (department_label or "").strip() or (
            (self._department_id or "").replace("_", " ").title() if self._department_id else "General"
        )
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._project_root = Path(project_root) if project_root else None
        self._item_display_name = (item_display_name or "").strip() or (
            self._entity_path.name if self._entity_path is not None else ""
        )
        if self._entity_path is not None:
            self._editor.set_item_root(self._entity_path)
            if self._workspace_root is not None:
                self._editor.set_workspace_root(self._workspace_root)
            self._editor.reset_draft()
        self._context_label.setText(
            f"{self._item_display_name} · {self._department_label}"
            if self._item_display_name
            else self._department_label
        )
        self._refresh_compose_enabled()
        self.reload_notes()

    def set_frame_hint(self, text: str) -> None:
        self._frame_hint = (text or "").strip()
        self._frame_label.setText(self._frame_hint or "—")

    def set_playhead(self, frame: int, fps: float) -> None:
        self._frame = max(0, int(frame))
        self._fps = max(1e-6, float(fps))
        frame_lbl = format_frame_label(self._frame)
        tc = format_timecode(self._frame / self._fps, fps=self._fps)
        parts = [frame_lbl, tc]
        if self._department_label:
            parts.append(self._department_label)
        self.set_frame_hint(" · ".join(parts))
        self._rebuild_list()

    def reload_notes(self) -> None:
        if self._entity_path is None or not self._entity_path.is_dir():
            self._entries = []
        else:
            try:
                self._entries = list(
                    read_item_comments_for_department(self._entity_path, self._department_id)
                )
            except OSError:
                self._entries = []
        self._update_summary()
        self._rebuild_list()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._on_add()
            event.accept()
            return
        super().keyPressEvent(event)

    def _refresh_compose_enabled(self) -> None:
        ready = self._entity_path is not None and self._entity_path.is_dir()
        self._compose_host.setEnabled(ready)
        self._btn_add.setEnabled(ready)

    def _update_summary(self) -> None:
        n_open = sum(1 for e in self._entries if not e.done)
        n_done = sum(1 for e in self._entries if e.done)
        at_frame = sum(1 for e in self._entries if self._entry_matches_playhead(e))
        parts = [f"{n_open} open", f"{n_done} done"]
        if at_frame:
            parts.append(f"{at_frame} at frame")
        self._summary_label.setText(" · ".join(parts))

    def _entry_matches_playhead(self, entry: ItemCommentEntry) -> bool:
        if not self._frame_hint:
            return False
        text = (entry.text or "").strip()
        if self._frame_hint and self._frame_hint in text:
            return True
        frame_lbl = format_frame_label(self._frame)
        return f"F{frame_lbl}" in text or frame_lbl in text

    def _frame_prefix(self) -> str:
        if self._frame_hint:
            return f"[{self._frame_hint}]"
        frame_lbl = format_frame_label(self._frame)
        tc = format_timecode(self._frame / self._fps, fps=self._fps)
        return f"[F{frame_lbl} · {tc}]"

    def _item_rel_path(self) -> str:
        if self._project_root is None or self._entity_path is None:
            return self._entity_path.name if self._entity_path else ""
        try:
            return self._entity_path.relative_to(self._project_root).as_posix()
        except ValueError:
            return self._entity_path.name

    def _dispatch_mentions(self, entry: ItemCommentEntry) -> None:
        if self._project_root is None or not entry.mentions:
            return
        current = get_current_user(self._workspace_root)
        from_uid = current.id if current else ""
        from_name = current.name if current else "Someone"
        try:
            append_mentions(
                self._project_root,
                from_user_id=from_uid,
                from_name=from_name,
                mentions=entry.mentions,
                item_rel=self._item_rel_path(),
                item_display=self._item_display_name,
                note_id=entry.id,
                snippet=entry.text,
                department=self._department_id or "",
            )
        except OSError:
            return

    def _persist_entries(self, entries: list[ItemCommentEntry]) -> None:
        if self._entity_path is None:
            return
        write_item_comments_for_department(self._entity_path, self._department_id, entries)

    def _on_add(self) -> None:
        if self._entity_path is None or not self._entity_path.is_dir():
            return
        if not self._editor.has_content():
            return
        current = get_current_user(self._workspace_root)
        prefix = self._frame_prefix()
        body_plain = self._editor.plain_text().strip()
        plain = f"{prefix} {body_plain}".strip()
        body_html = self._editor.body_html()
        if prefix:
            body_html = (
                f'<p style="color:#71717a; margin-bottom:6px;">{prefix}</p>{body_html}'
            )
        try:
            entry = new_comment_entry(
                plain,
                author=current.name if current else None,
                author_id=current.id if current else None,
                body_html=body_html,
                mentions=self._editor.mention_ids(),
                entry_id=self._editor.draft_entry_id,
                department=self._department_id,
            )
        except ValueError:
            return
        entries = list(self._entries)
        entries.append(entry)
        try:
            self._persist_entries(entries)
        except OSError:
            return
        self._entries = entries
        self._dispatch_mentions(entry)
        self._editor.reset_draft()
        self._update_summary()
        self._rebuild_list()
        self.note_added.emit()

    def _on_done_toggled(self, entry_id: str, checked: bool) -> None:
        now_iso = _utc_stamp()
        updated: list[ItemCommentEntry] = []
        changed = False
        for entry in self._entries:
            if entry.id != entry_id:
                updated.append(entry)
                continue
            changed = True
            if checked:
                updated.append(replace(entry, done=True, done_at=now_iso))
            else:
                updated.append(replace(entry, done=False, done_at=None))
        if not changed:
            return
        try:
            self._persist_entries(updated)
        except OSError:
            return
        self._entries = updated
        self._update_summary()
        self._rebuild_list()
        self.note_added.emit()

    def _open_note_view(self, note_id: str) -> None:
        eid = (note_id or "").strip()
        if not eid or self._entity_path is None:
            return
        entry = next((e for e in self._entries if e.id == eid), None)
        if entry is None:
            return
        user = get_current_user(self._workspace_root)
        if user is not None:
            seen = record_note_seen(
                self._entity_path,
                eid,
                user_id=user.id,
                user_name=user.name,
            )
            if seen is not None:
                self._entries = [seen if e.id == eid else e for e in self._entries]
        dlg = NoteViewDialog(
            entry=entry,
            item_root=self._entity_path,
            item_display_name=self._item_display_name,
            workspace_root=self._workspace_root,
            parent=self.window(),
        )
        dlg.exec()
        self.reload_notes()

    def _rebuild_list(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        ordered = sorted(self._entries, key=lambda e: e.at, reverse=True)
        frame_open = [e for e in ordered if self._entry_matches_playhead(e) and not e.done]
        frame_done = [e for e in ordered if self._entry_matches_playhead(e) and e.done]
        other_open = [e for e in ordered if not self._entry_matches_playhead(e) and not e.done]
        other_done = [e for e in ordered if not self._entry_matches_playhead(e) and e.done]
        ordered = frame_open + frame_done + other_open + other_done

        if not ordered:
            empty = QLabel("No notes for this department yet.", self._list_host)
            empty.setObjectName("DialogHint")
            empty.setWordWrap(True)
            self._list_layout.insertWidget(0, empty)
            return

        for entry in ordered:
            self._list_layout.insertWidget(0, self._make_note_card(entry))

    def _make_note_card(self, entry: ItemCommentEntry) -> QFrame:
        frame_match = self._entry_matches_playhead(entry)
        card = _ReviewNoteCard(entry, frame_match=frame_match, parent=self._list_host)
        card.open_requested.connect(self._open_note_view)
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 8, 8, 8)
        row.setSpacing(8)

        done_btn = NoteDoneToggleButton(checked=entry.done, parent=card)
        done_btn.toggled.connect(
            lambda checked, eid=entry.id: self._on_done_toggled(eid, checked)
        )
        row.addWidget(done_btn, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        text_col.addWidget(
            NoteAuthorRow.for_entry(
                entry,
                self._workspace_root,
                avatar_size=22,
                time_text=_format_local_time(entry.at),
                parent=card,
            )
        )
        preview = NoteListPreviewLabel(card)
        preview.set_preview(entry_preview_text(entry), done=entry.done)
        preview.open_requested.connect(lambda eid=entry.id: self._open_note_view(eid))
        text_col.addWidget(preview, 1)
        seen = note_seen_by_label(entry, self._workspace_root, card)
        if seen is not None:
            text_col.addWidget(seen)
        row.addLayout(text_col, 1)

        if frame_match:
            badge = QLabel("FRAME", card)
            badge.setObjectName("VideoReviewNoteFrameBadge")
            badge.setFont(monos_font("Inter", 9, QFont.Weight.Bold))
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        return card
