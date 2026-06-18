"""Quick note compose + list for video review (entity context)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
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
from monostudio.core.note_time_anchors import (
    format_marker_pill_label,
    format_range_pill_label,
    time_href_for_marker,
    time_href_for_range,
)
from monostudio.core.video_media import (
    VideoFrameRange,
    VideoReviewMarker,
    format_frame_label,
    format_range_span_display,
    format_timecode,
)
from monostudio.ui_qt.note_author_row import NoteAuthorRow
from monostudio.ui_qt.note_body_browser import NoteListPreviewLabel, make_note_card_preview
from monostudio.ui_qt.note_compose_editor import NoteComposeEditor
from monostudio.ui_qt.note_done_toggle import NoteDoneToggleButton
from monostudio.ui_qt.note_seen_by_label import note_seen_by_label
from monostudio.ui_qt.note_view_dialog import NoteViewDialog
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_range_note_reference(rng: VideoFrameRange, fps: float) -> str:
    span = format_range_span_display(rng, fps, mode="frame")
    label = (rng.label or "").strip()
    if label:
        return f"[{span} · {label}]"
    return f"[{span}]"


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


class _ElidedMetaLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        text = self._full_text or ""
        if not text:
            self.setText("")
            self.setToolTip("")
            return
        fm = QFontMetrics(self.font())
        max_w = max(8, self.contentsRect().width())
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, max_w)
        self.setText(elided)
        self.setToolTip(text if elided != text else "")


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
    time_anchor_requested = Signal(str)

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
        self._compose_active = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)

        header = QVBoxLayout()
        header.setSpacing(2)
        self._context_label = QLabel("", self)
        self._context_label.setObjectName("VideoReviewDrawSectionTitle")
        self._context_label.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        self._context_label.setWordWrap(True)
        header.addWidget(self._context_label)
        self._frame_label = QLabel("", self)
        self._frame_label.setWordWrap(True)
        self._frame_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._frame_label.setStyleSheet(f"color: {MONOS_COLORS.get('text_label', '#a1a1aa')};")
        header.addWidget(self._frame_label)
        self._summary_label = QLabel("", self)
        self._summary_label.setObjectName("DialogHint")
        self._summary_label.setWordWrap(True)
        header.addWidget(self._summary_label)
        lay.addLayout(header)

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
        self._list_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        self._scroll.viewport().installEventFilter(self)
        lay.addWidget(self._scroll, 1)

        self._compose_host = QWidget(self)
        compose_lay = QVBoxLayout(self._compose_host)
        compose_lay.setContentsMargins(0, 0, 0, 0)
        compose_lay.setSpacing(6)
        compose_title = QLabel("New note", self._compose_host)
        compose_title.setObjectName("DialogHint")
        compose_title.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        compose_lay.addWidget(compose_title)
        self._editor = NoteComposeEditor(item_root=Path("."), workspace_root=None, parent=self._compose_host)
        self._editor.setMinimumHeight(88)
        self._editor.setMaximumHeight(160)
        self._editor.setPlaceholderText("Note at playhead… (@mention · paste images)")
        self._editor.time_anchor_clicked.connect(self.time_anchor_requested.emit)
        compose_lay.addWidget(self._editor)
        compose_btn_row = QHBoxLayout()
        compose_btn_row.setSpacing(8)
        self._chk_auto_add = QCheckBox("Auto add", self._compose_host)
        self._chk_auto_add.setObjectName("VideoReviewNoteAutoAddCheck")
        self._chk_auto_add.setChecked(True)
        self._chk_auto_add.setToolTip("Click ranges and markers to insert pills into the note")
        compose_btn_row.addWidget(self._chk_auto_add)
        self._btn_add = QPushButton("Add note", self._compose_host)
        self._btn_add.setObjectName("DialogPrimaryButton")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_cancel = QPushButton("Cancel", self._compose_host)
        self._btn_cancel.setObjectName("DialogSecondaryButton")
        self._btn_cancel.clicked.connect(self.cancel_compose)
        compose_btn_row.addWidget(self._btn_add)
        compose_btn_row.addWidget(self._btn_cancel)
        compose_btn_row.addStretch(1)
        compose_lay.addLayout(compose_btn_row)
        self._compose_host.setVisible(False)
        lay.addWidget(self._compose_host)

        self._hint = QLabel("New note captures playhead · Esc cancels compose", self)
        self._hint.setObjectName("DialogHint")
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._btn_new_note = QPushButton("New note", self)
        self._btn_new_note.setObjectName("DialogPrimaryButton")
        self._btn_new_note.setToolTip("Compose a note at the current playhead")
        self._btn_new_note.clicked.connect(self._start_compose)
        self._btn_all = QPushButton("All notes…", self)
        self._btn_all.setObjectName("DialogSecondaryButton")
        self._btn_all.clicked.connect(self.open_all_notes_requested.emit)
        action_row.addWidget(self._btn_new_note)
        action_row.addWidget(self._btn_all)
        action_row.addStretch(1)
        lay.addLayout(action_row)

        self._refresh_compose_enabled()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_list_host_width()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_list_host_width()
        return super().eventFilter(obj, event)

    def _sync_list_host_width(self) -> None:
        w = max(1, self._scroll.viewport().width())
        self._list_host.setMinimumWidth(w)
        self._list_host.setMaximumWidth(w)

    def compose_active(self) -> bool:
        return self._compose_active

    def cancel_compose(self) -> None:
        if not self._compose_active:
            return
        self._finish_compose()

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
        self._finish_compose()
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

    def entries(self) -> list[ItemCommentEntry]:
        return list(self._entries)

    def set_frame_hint(self, text: str) -> None:
        self._frame_hint = (text or "").strip()
        self._frame_label.setText(self._frame_hint or "—")

    def auto_add_enabled(self) -> bool:
        return self._compose_active and self._chk_auto_add.isChecked()

    def insert_range_reference(self, rng: VideoFrameRange, fps: float) -> bool:
        if not self._compose_active or self._entity_path is None or not self._compose_host.isEnabled():
            return False
        label = format_range_pill_label(rng, fps)
        href = time_href_for_range(rng.id, rng.in_frame)
        self._editor.insert_time_pill(label, href, kind="range")
        return True

    def insert_marker_reference(self, marker: VideoReviewMarker, fps: float) -> bool:
        if not self._compose_active or self._entity_path is None or not self._compose_host.isEnabled():
            return False
        label = format_marker_pill_label(marker, fps)
        href = time_href_for_marker(marker.id, marker.frame)
        self._editor.insert_time_pill(label, href, kind="marker")
        return True

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
            self._compose_active
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._on_add()
            event.accept()
            return
        super().keyPressEvent(event)

    def _start_compose(self) -> None:
        if self._entity_path is None or not self._entity_path.is_dir():
            return
        self._compose_active = True
        self._editor.reset_draft()
        self._editor.sync_playhead_pill(self._frame, self._fps)
        self._editor.set_playhead_pill_locked(True)
        self._compose_host.setVisible(True)
        self._hint.setText("Playhead locked · Ctrl+Enter add · Esc cancel")
        self._refresh_compose_enabled()
        self._editor.setFocus()

    def _finish_compose(self) -> None:
        if not self._compose_active and not self._compose_host.isVisible():
            self._editor.reset_draft()
            return
        self._compose_active = False
        self._editor.set_playhead_pill_locked(False)
        self._editor.reset_draft()
        self._compose_host.setVisible(False)
        self._hint.setText("New note captures playhead · Esc cancels compose")
        self._refresh_compose_enabled()

    def _refresh_compose_enabled(self) -> None:
        ready = self._entity_path is not None and self._entity_path.is_dir()
        self._compose_host.setEnabled(ready)
        self._btn_new_note.setEnabled(ready and not self._compose_active)
        self._btn_add.setEnabled(ready and self._compose_active)
        self._btn_cancel.setEnabled(ready and self._compose_active)

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
        if not self._compose_active or self._entity_path is None or not self._entity_path.is_dir():
            return
        if not self._editor.has_content():
            return
        current = get_current_user(self._workspace_root)
        body_plain = self._editor.plain_text().strip()
        plain = body_plain
        body_html = self._editor.body_html()
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
        self._finish_compose()
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
        dlg.time_anchor_clicked.connect(self.time_anchor_requested.emit)
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
        self._sync_list_host_width()

    def _make_note_card(self, entry: ItemCommentEntry) -> QFrame:
        frame_match = self._entry_matches_playhead(entry)
        card = _ReviewNoteCard(entry, frame_match=frame_match, parent=self._list_host)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        card.setMinimumWidth(0)
        card.open_requested.connect(self._open_note_view)
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        done_btn = NoteDoneToggleButton(checked=entry.done, parent=card)
        done_btn.toggled.connect(
            lambda checked, eid=entry.id: self._on_done_toggled(eid, checked)
        )
        row.addWidget(done_btn, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        author = NoteAuthorRow.for_entry(
            entry,
            self._workspace_root,
            avatar_size=22,
            name_only=True,
            parent=card,
        )
        author.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        author.setMinimumWidth(0)
        meta_row.addWidget(author, 1)
        if frame_match:
            badge = QLabel("FRAME", card)
            badge.setObjectName("VideoReviewNoteFrameBadge")
            badge.setFont(monos_font("Inter", 9, QFont.Weight.Bold))
            meta_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        text_col.addLayout(meta_row)

        time_l = _ElidedMetaLabel(_format_local_time(entry.at), card)
        time_l.setObjectName("ItemNotesMetaTime")
        time_l.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        text_col.addWidget(time_l)

        preview = self._make_note_preview(entry, card)
        text_col.addWidget(preview, 1)
        seen = note_seen_by_label(entry, self._workspace_root, card)
        if seen is not None:
            text_col.addWidget(seen)
        text_wrap = QWidget(card)
        text_wrap.setObjectName("VideoReviewNoteCardBody")
        text_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_wrap.setMinimumWidth(0)
        text_wrap.setLayout(text_col)
        row.addWidget(text_wrap, 1)

        return card

    def _make_note_preview(self, entry: ItemCommentEntry, card: QWidget) -> QWidget:
        if self._entity_path is None:
            plain = NoteListPreviewLabel(card)
            plain.set_preview(entry_preview_text(entry), done=entry.done)
            plain.open_requested.connect(lambda eid=entry.id: self._open_note_view(eid))
            return plain
        return make_note_card_preview(
            entry,
            item_root=self._entity_path,
            workspace_root=self._workspace_root,
            parent=card,
            on_time_anchor=self.time_anchor_requested.emit,
            on_plain_open=lambda eid=entry.id: self._open_note_view(eid),
        )
