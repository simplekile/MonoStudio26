from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.item_comments import (
    ItemCommentEntry,
    delete_note_media,
    entry_preview_text,
    new_comment_entry,
    normalize_note_department_id,
    read_item_comments_for_department,
    write_item_comments_for_department,
)
from monostudio.core.mention_inbox import append_mentions
from monostudio.core.user_identity import get_current_user
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.note_author_row import NoteAuthorRow
from monostudio.ui_qt.note_body_browser import NoteListPreviewLabel
from monostudio.ui_qt.note_compose_editor import NoteComposeEditor
from monostudio.ui_qt.note_view_dialog import NoteViewDialog
from monostudio.ui_qt.style import MonosDialog, monos_font


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


def _entries_fingerprint(entries: list[ItemCommentEntry]) -> tuple:
    return tuple(
        (
            e.id,
            e.at,
            e.author,
            e.text,
            e.done,
            e.done_at,
            e.author_id,
            e.body_html,
            e.mentions,
            e.department,
        )
        for e in sorted(entries, key=lambda x: x.id)
    )


def _click_target_blocks_card(w: QWidget | None) -> bool:
    while w is not None:
        if isinstance(w, (QCheckBox, QToolButton)):
            return True
        w = w.parentWidget()
    return False


class _NoteListCard(QFrame):
    """Compact note row; click opens full note viewer."""

    def __init__(self, entry: ItemCommentEntry, *, parent=None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setObjectName("ItemNotesCardDone" if entry.done else "ItemNotesCard")
        self.setProperty("noteCardId", entry.id)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to view full note")

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if not _click_target_blocks_card(self.childAt(pos)):
                host = self.window()
                opener = getattr(host, "_open_note_view", None)
                if callable(opener):
                    opener(self._entry.id)
                    event.accept()
                    return
        super().mouseReleaseEvent(event)


class ItemNotesDialog(MonosDialog):
    """
    Notes editor: draft in memory — header + rich compose + checklist rows + Cancel / Save changes.
    """

    notes_changed = Signal()

    def __init__(
        self,
        *,
        parent=None,
        item_path: Path,
        item_display_name: str,
        author: str | None = None,
        author_id: str | None = None,
        workspace_root: Path | None = None,
        project_root: Path | None = None,
        department_id: str | None = None,
        department_label: str | None = None,
        highlight_note_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._item_path = Path(item_path)
        self._item_display_name = (item_display_name or "—").strip()
        self._author = (author or "").strip() or None
        self._author_id = (author_id or "").strip() or None
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._project_root = Path(project_root) if project_root else None
        self._department_id = normalize_note_department_id(department_id)
        self._department_label = (department_label or "").strip() or (
            self._department_id or "General"
        )
        self._highlight_note_id = (highlight_note_id or "").strip() or None
        self._draft: list[ItemCommentEntry] = list(
            read_item_comments_for_department(self._item_path, self._department_id or None)
        )
        self._initial_fp = _entries_fingerprint(self._draft)
        self._known_ids = {e.id for e in self._draft}

        self.setWindowTitle("Notes")
        self.setModal(True)
        self.setObjectName("ItemNotesDialog")
        self.setMinimumSize(880, 560)
        self.resize(960, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel(
            f"Notes — {self._item_display_name} · {self._department_label}",
            self,
        )
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 16, QFont.Weight.DemiBold))
        root.addWidget(title)

        self._summary = QLabel("", self)
        self._summary.setObjectName("DialogHint")
        self._summary.setFont(monos_font("Inter", 12, QFont.Weight.Normal))
        root.addWidget(self._summary)

        root.addWidget(self._make_h_sep())

        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setObjectName("ItemNotesSplit")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)

        # --- Left: compose ---
        compose_panel = QWidget(split)
        compose_panel.setObjectName("ItemNotesComposePanel")
        compose_l = QVBoxLayout(compose_panel)
        compose_l.setContentsMargins(0, 0, 8, 0)
        compose_l.setSpacing(10)

        compose_heading = QLabel("New note", compose_panel)
        compose_heading.setObjectName("DialogHint")
        compose_heading.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        compose_l.addWidget(compose_heading, 0)

        self._add_edit = NoteComposeEditor(
            item_root=self._item_path,
            workspace_root=self._workspace_root,
            parent=compose_panel,
        )
        compose_l.addWidget(self._add_edit, 1)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(10)
        add_row.addStretch(1)
        self._add_btn = QPushButton("Add note", compose_panel)
        self._add_btn.setObjectName("ItemNotesAddButton")
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._on_add_draft)
        add_row.addWidget(self._add_btn, 0)
        compose_l.addLayout(add_row, 0)

        split.addWidget(compose_panel)

        # --- Right: note list ---
        list_panel = QWidget(split)
        list_panel.setObjectName("ItemNotesListPanel")
        list_l = QVBoxLayout(list_panel)
        list_l.setContentsMargins(8, 0, 0, 0)
        list_l.setSpacing(10)

        list_heading = QLabel(f"{self._department_label} notes", list_panel)
        list_heading.setObjectName("DialogHint")
        list_heading.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        list_l.addWidget(list_heading, 0)

        self._scroll = QScrollArea(list_panel)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setObjectName("ItemNotesScroll")

        self._list_host = QWidget(self._scroll)
        self._list_host.setObjectName("ItemNotesListHost")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        list_l.addWidget(self._scroll, 1)

        split.addWidget(list_panel)
        split.setStretchFactor(0, 44)
        split.setStretchFactor(1, 56)
        split.setSizes([420, 520])

        root.addWidget(split, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(12)
        footer.addStretch(1)

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn, 0)

        save_btn = QPushButton("Save changes", self)
        save_btn.setObjectName("DialogPrimaryButton")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        footer.addWidget(save_btn, 0)

        root.addLayout(footer)

        self._update_summary()
        self._rebuild_list()
        if self._highlight_note_id:
            QTimer.singleShot(0, self._scroll_to_highlight)

    def _scroll_to_highlight(self) -> None:
        if not self._highlight_note_id:
            return
        for card in self._list_host.findChildren(QFrame):
            if card.property("noteCardId") == self._highlight_note_id:
                self._scroll.ensureWidgetVisible(card, 0, 40)
                break
        QTimer.singleShot(0, lambda: self._open_note_view(self._highlight_note_id))

    def _open_note_view(self, note_id: str) -> None:
        eid = (note_id or "").strip()
        if not eid:
            return
        entry = next((e for e in self._draft if e.id == eid), None)
        if entry is None:
            return
        dlg = NoteViewDialog(
            entry=entry,
            item_root=self._item_path,
            item_display_name=self._item_display_name,
            workspace_root=self._workspace_root,
            parent=self,
        )
        dlg.exec()

    def _make_h_sep(self) -> QFrame:
        line = QFrame(self)
        line.setObjectName("ItemNotesHRule")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return line

    def _update_summary(self) -> None:
        n_open = sum(1 for e in self._draft if not e.done)
        n_done = sum(1 for e in self._draft if e.done)
        self._summary.setText(f"{n_open} active · {n_done} completed")

    def _rebuild_list(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        ordered = sorted(self._draft, key=lambda e: e.at, reverse=True)
        if not ordered:
            empty = QLabel("No notes yet.", self._list_host)
            empty.setObjectName("DialogHint")
            self._list_layout.insertWidget(0, empty)
            return

        for e in ordered:
            self._list_layout.insertWidget(0, self._make_note_card(e))

    def _make_note_card(self, entry: ItemCommentEntry) -> QFrame:
        card = _NoteListCard(entry, parent=self._list_host)

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(10)

        cb = QCheckBox(card)
        cb.setObjectName("ItemNotesDoneCheck")
        cb.setChecked(entry.done)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.toggled.connect(lambda checked, eid=entry.id: self._on_done_toggled(eid, checked))
        row.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        text_col.addWidget(
            NoteAuthorRow.for_entry(
                entry,
                self._workspace_root,
                avatar_size=24,
                time_text=_format_local_time(entry.at),
                parent=card,
            )
        )

        preview = NoteListPreviewLabel(card)
        preview.set_preview(entry_preview_text(entry), done=entry.done)
        preview.open_requested.connect(lambda eid=entry.id: self._open_note_view(eid))
        text_col.addWidget(preview, 1)
        row.addLayout(text_col, 1)

        open_btn = QToolButton(card)
        open_btn.setObjectName("ItemNotesOpenButton")
        open_btn.setAutoRaise(True)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setIcon(lucide_icon("maximize-2", size=16, color_hex="#a1a1aa"))
        open_btn.setIconSize(QSize(16, 16))
        open_btn.setToolTip("View full note")
        open_btn.clicked.connect(lambda _=False, eid=entry.id: self._open_note_view(eid))
        row.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignTop)

        del_btn = QToolButton(card)
        del_btn.setObjectName("ItemNotesDeleteButton")
        del_btn.setAutoRaise(True)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setIcon(lucide_icon("trash-2", size=16, color_hex="#ef4444"))
        del_btn.setIconSize(QSize(16, 16))
        del_btn.setToolTip("Remove note")
        del_btn.clicked.connect(lambda _=False, eid=entry.id: self._on_remove_draft(eid))
        row.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)

        return card

    def _on_add_draft(self) -> None:
        if not self._add_edit.has_content():
            return
        try:
            entry = new_comment_entry(
                self._add_edit.plain_text(),
                author=self._author,
                author_id=self._author_id,
                body_html=self._add_edit.body_html(),
                mentions=self._add_edit.mention_ids(),
                entry_id=self._add_edit.draft_entry_id,
                department=self._department_id or None,
            )
        except ValueError as ex:
            QMessageBox.warning(self, "Notes", str(ex))
            return
        self._draft.append(entry)
        self._add_edit.reset_draft()
        self._update_summary()
        self._rebuild_list()

    def _on_remove_draft(self, eid: str) -> None:
        delete_note_media(self._item_path, eid)
        self._draft = [e for e in self._draft if e.id != eid]
        self._update_summary()
        self._rebuild_list()

    def _on_done_toggled(self, eid: str, checked: bool) -> None:
        now_iso = _utc_stamp()
        new_list: list[ItemCommentEntry] = []
        for e in self._draft:
            if e.id != eid:
                new_list.append(e)
                continue
            if checked:
                new_list.append(
                    ItemCommentEntry(
                        id=e.id,
                        at=e.at,
                        author=e.author,
                        text=e.text,
                        done=True,
                        done_at=now_iso,
                        author_id=e.author_id,
                        body_html=e.body_html,
                        mentions=e.mentions,
                        department=e.department,
                    )
                )
            else:
                new_list.append(
                    ItemCommentEntry(
                        id=e.id,
                        at=e.at,
                        author=e.author,
                        text=e.text,
                        done=False,
                        done_at=None,
                        author_id=e.author_id,
                        body_html=e.body_html,
                        mentions=e.mentions,
                        department=e.department,
                    )
                )
        self._draft = new_list
        self._update_summary()
        self._rebuild_list()

    def _item_rel_path(self) -> str:
        if self._project_root is None:
            return self._item_path.name
        try:
            return self._item_path.relative_to(self._project_root).as_posix()
        except ValueError:
            return self._item_path.name

    def _dispatch_mentions_for_new_entries(self) -> None:
        if self._project_root is None:
            return
        current = get_current_user(self._workspace_root)
        from_uid = self._author_id or (current.id if current else "")
        from_name = self._author or (current.name if current else "Someone")
        for e in self._draft:
            if e.id in self._known_ids:
                continue
            if not e.mentions:
                continue
            try:
                new_items = append_mentions(
                    self._project_root,
                    from_user_id=from_uid,
                    from_name=from_name,
                    mentions=e.mentions,
                    item_rel=self._item_rel_path(),
                    item_display=self._item_display_name,
                    note_id=e.id,
                    snippet=e.text,
                )
            except OSError:
                continue
            # Bell alerts are created in MainWindow._sync_mention_inbox_alerts for the
            # signed-in user only — do not notify the author's session for other targets.

    def _on_save(self) -> None:
        try:
            write_item_comments_for_department(
                self._item_path,
                self._department_id or None,
                self._draft,
            )
        except OSError as ex:
            QMessageBox.warning(self, "Notes", str(ex) or "Could not save notes.")
            return
        self._dispatch_mentions_for_new_entries()
        self._initial_fp = _entries_fingerprint(self._draft)
        self._known_ids = {e.id for e in self._draft}
        self.notes_changed.emit()
        self.accept()

    def reject(self) -> None:  # type: ignore[override]
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        super().closeEvent(event)
