from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.item_comments import (
    ItemCommentEntry,
    new_comment_entry,
    read_item_comments,
    write_item_comments,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font


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
        (e.id, e.at, e.author, e.text, e.done, e.done_at)
        for e in sorted(entries, key=lambda x: x.id)
    )


class ItemNotesDialog(MonosDialog):
    """
    Notes editor: draft in memory — header + inline add (+) + checklist rows + Cancel / Save changes.
    """

    notes_changed = Signal()

    def __init__(
        self,
        *,
        parent=None,
        item_path: Path,
        item_display_name: str,
    ) -> None:
        super().__init__(parent)
        self._item_path = Path(item_path)
        self._draft: list[ItemCommentEntry] = list(read_item_comments(self._item_path))
        self._initial_fp = _entries_fingerprint(self._draft)

        self.setWindowTitle("Notes")
        self.setModal(True)
        self.setObjectName("ItemNotesDialog")
        self.setMinimumSize(480, 420)
        self.resize(560, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel(f"Notes — {(item_display_name or '—').strip()}", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 16, QFont.Weight.DemiBold))
        root.addWidget(title)

        self._summary = QLabel("", self)
        self._summary.setObjectName("DialogHint")
        self._summary.setFont(monos_font("Inter", 12, QFont.Weight.Normal))
        root.addWidget(self._summary)

        root.addWidget(self._make_h_sep())

        add_row = QWidget(self)
        add_l = QHBoxLayout(add_row)
        add_l.setContentsMargins(0, 0, 0, 0)
        add_l.setSpacing(10)

        self._add_edit = QLineEdit(add_row)
        self._add_edit.setObjectName("ItemNotesLineInput")
        self._add_edit.setPlaceholderText("Add a new note…")
        self._add_edit.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        self._add_edit.returnPressed.connect(self._on_add_draft)
        add_l.addWidget(self._add_edit, 1)

        self._add_btn = QToolButton(add_row)
        self._add_btn.setObjectName("ItemNotesAddPlusButton")
        self._add_btn.setIcon(lucide_icon("plus", size=20, color_hex=MONOS_COLORS["text_label"]))
        self._add_btn.setIconSize(QSize(20, 20))
        self._add_btn.setFixedSize(44, 44)
        self._add_btn.setAutoRaise(True)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setToolTip("Add note")
        self._add_btn.clicked.connect(self._on_add_draft)
        add_l.addWidget(self._add_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        root.addWidget(add_row)
        root.addWidget(self._make_h_sep())

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setObjectName("ItemNotesScroll")

        self._list_host = QWidget(self._scroll)
        self._list_host.setObjectName("ItemNotesListHost")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 4, 4, 0)
        self._list_layout.setSpacing(12)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll, 1)

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
        card = QFrame(self._list_host)
        card.setObjectName("ItemNotesCardDone" if entry.done else "ItemNotesCard")
        card.setFrameShape(QFrame.Shape.NoFrame)

        row = QHBoxLayout(card)
        row.setContentsMargins(12, 10, 10, 10)
        row.setSpacing(12)

        cb = QCheckBox(card)
        cb.setObjectName("ItemNotesDoneCheck")
        cb.setChecked(entry.done)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.toggled.connect(lambda checked, eid=entry.id: self._on_done_toggled(eid, checked))
        row.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        body = QLabel(entry.text, card)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        if entry.done:
            body.setStyleSheet(f"color: {MONOS_COLORS.get('text_muted', '#71717a')}; text-decoration: line-through;")
        else:
            body.setStyleSheet(f"color: {MONOS_COLORS.get('text_body', '#d4d4d8')};")

        time_l = QLabel(_format_local_time(entry.at), card)
        time_l.setObjectName("DialogHint")
        time_l.setFont(monos_font("Inter", 11, QFont.Weight.Normal))

        text_col.addWidget(body)
        text_col.addWidget(time_l)
        row.addLayout(text_col, 1)

        del_btn = QToolButton(card)
        del_btn.setObjectName("ItemNotesDeleteButton")
        del_btn.setAutoRaise(True)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setIcon(lucide_icon("trash-2", size=18, color_hex="#ef4444"))
        del_btn.setToolTip("Remove note")
        del_btn.clicked.connect(lambda _=False, eid=entry.id: self._on_remove_draft(eid))
        row.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)

        return card

    def _on_add_draft(self) -> None:
        text = self._add_edit.text().strip()
        if not text:
            return
        try:
            self._draft.append(new_comment_entry(text))
        except ValueError as ex:
            QMessageBox.warning(self, "Notes", str(ex))
            return
        self._add_edit.clear()
        self._update_summary()
        self._rebuild_list()

    def _on_remove_draft(self, eid: str) -> None:
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
                    )
                )
        self._draft = new_list
        self._update_summary()
        self._rebuild_list()

    def _on_save(self) -> None:
        try:
            write_item_comments(self._item_path, self._draft)
        except OSError as ex:
            QMessageBox.warning(self, "Notes", str(ex) or "Could not save notes.")
            return
        self._initial_fp = _entries_fingerprint(self._draft)
        self.notes_changed.emit()
        self.accept()

    def reject(self) -> None:  # type: ignore[override]
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        super().closeEvent(event)
