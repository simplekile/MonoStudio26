"""Trash page: list trashed assets/shots, restore or delete permanently."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.project_trash import (
    TrashError,
    delete_trash_entry_permanently,
    empty_trash,
    format_trashed_at_local,
    list_entries,
    restore_trash_entry,
)
from monostudio.ui_qt.delete_confirm_dialog import ask_delete
from monostudio.ui_qt.link_reveal import link_reveal, paint_link_reveal_row_overlay
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

logger = logging.getLogger(__name__)


class _TrashLinkRevealDelegate(QStyledItemDelegate):
    def __init__(self, table: QTableWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._table = table

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        super().paint(painter, option, index)
        if index.column() != 0:
            return
        tid = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(tid, str) or not tid:
            return
        alpha = link_reveal().alpha_for_trash(tid) if link_reveal().current_alpha() > 0.01 else 0.0
        if alpha <= 0:
            return
        view = option.widget or self._table
        vp_w = view.viewport().width() if hasattr(view, "viewport") else option.rect.width()
        hl_rect = QRect(0, option.rect.y(), vp_w, option.rect.height())
        paint_link_reveal_row_overlay(painter, hl_rect, alpha)


class TrashPageWidget(QWidget):
    """Project trash list + restore / permanent delete / empty trash."""

    trash_changed = Signal()
    copy_link_requested = Signal(str)  # trash entry id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Trash", self)
        title.setFont(monos_font("Inter", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {MONOS_COLORS.get('text_primary', '#fafafa')};")
        root.addWidget(title)

        hint = QLabel(
            "Items here are removed from Assets/Shots but stay on disk until you delete them permanently "
            "from this page. Entries older than 30 days (by default) are removed automatically when you open the project.",
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        hint.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        root.addWidget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._btn_restore = QPushButton("Restore", self)
        self._btn_restore.setObjectName("DialogPrimaryButton")
        self._btn_restore.clicked.connect(self._on_restore)
        self._btn_delete = QPushButton("Delete permanently…", self)
        self._btn_delete.setObjectName("DialogDestructiveButton")
        self._btn_delete.clicked.connect(self._on_delete_one)
        self._btn_empty = QPushButton("Empty trash…", self)
        self._btn_empty.setObjectName("DialogSecondaryButton")
        self._btn_empty.clicked.connect(self._on_empty_all)
        bar.addWidget(self._btn_restore)
        bar.addWidget(self._btn_delete)
        bar.addWidget(self._btn_empty)
        bar.addStretch(1)
        root.addLayout(bar)

        self._table = QTableWidget(self)
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Kind", "Name", "Trashed", "Original path"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.setItemDelegate(_TrashLinkRevealDelegate(self._table, self._table))
        link_reveal().changed.connect(self._on_link_reveal_tick)
        root.addWidget(self._table, 1)

        self._empty = QLabel("Trash is empty.", self)
        self._empty.setStyleSheet("color: #71717a; font-size: 13px;")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty)

    def set_project_root(self, root: Path | None, *, refresh: bool = True) -> None:
        self._project_root = Path(root) if root else None
        if refresh:
            self.refresh()

    def _on_link_reveal_tick(self) -> None:
        if not self.isVisible():
            return
        tid = link_reveal().any_active_trash_id()
        if not tid:
            return
        for row in range(self._table.rowCount()):
            it = self._table.item(row, 0)
            if it is None or it.data(Qt.ItemDataRole.UserRole) != tid:
                continue
            idx = self._table.model().index(row, 0)
            if idx.isValid():
                self._table.viewport().update(self._table.visualRect(idx))
            return

    def refresh(self) -> None:
        self._table.setRowCount(0)
        pr = self._project_root
        if pr is None or not pr.is_dir():
            self._empty.setVisible(True)
            self._table.setVisible(False)
            self._btn_restore.setEnabled(False)
            self._btn_delete.setEnabled(False)
            self._btn_empty.setEnabled(False)
            return
        try:
            entries = list_entries(pr)
        except OSError:
            entries = []
        self._apply_entries(entries)

    def refresh_responsive(self, worker_manager, *, on_done=None) -> None:
        """List trash entries on a worker; call *on_done* on the main thread when applied."""
        from monostudio.ui_qt.ui_worker_loop import run_worker_async

        self._table.setRowCount(0)
        pr = self._project_root
        if pr is None or not pr.is_dir():
            self._empty.setVisible(True)
            self._table.setVisible(False)
            self._btn_restore.setEnabled(False)
            self._btn_delete.setEnabled(False)
            self._btn_empty.setEnabled(False)
            if on_done is not None:
                on_done()
            return

        def _load():
            try:
                return list_entries(pr)
            except OSError:
                return []

        def _on_result(entries, error) -> None:
            if error:
                entries = []
            self._apply_entries(entries or [])
            if on_done is not None:
                on_done()

        run_worker_async(
            worker_manager,
            f"trash_list_{id(self)}",
            _load,
            on_result=_on_result,
        )

    def _apply_entries(self, entries) -> None:
        if not entries:
            self._empty.setVisible(True)
            self._table.setVisible(False)
            self._btn_restore.setEnabled(False)
            self._btn_delete.setEnabled(False)
            self._btn_empty.setEnabled(False)
            return
        self._empty.setVisible(False)
        self._table.setVisible(True)
        self._btn_restore.setEnabled(True)
        self._btn_delete.setEnabled(True)
        self._btn_empty.setEnabled(True)
        self._table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self._table.setItem(row, 0, QTableWidgetItem(e.kind.title()))
            self._table.item(row, 0).setData(Qt.ItemDataRole.UserRole, e.id)
            name_item = QTableWidgetItem(e.original_name)
            name_item.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
            self._table.setItem(row, 1, name_item)
            ts_item = QTableWidgetItem(format_trashed_at_local(e.trashed_at))
            ts_item.setToolTip(e.trashed_at)
            self._table.setItem(row, 2, ts_item)
            path_item = QTableWidgetItem(e.original_relative)
            path_item.setFont(monos_font("JetBrains Mono", 12, QFont.Weight.Normal))
            path_item.setForeground(QColor("#a1a1aa"))
            self._table.setItem(row, 3, path_item)
        self._table.resizeColumnsToContents()

    def _selected_entry_id(self) -> str | None:
        r = self._table.currentRow()
        if r < 0:
            return None
        it = self._table.item(r, 0)
        if it is None:
            return None
        tid = it.data(Qt.ItemDataRole.UserRole)
        return tid if isinstance(tid, str) and tid else None

    def select_entry_by_id(self, trash_id: str) -> bool:
        tid = (trash_id or "").strip()
        if not tid:
            return False
        for row in range(self._table.rowCount()):
            it = self._table.item(row, 0)
            if it is None:
                continue
            if it.data(Qt.ItemDataRole.UserRole) == tid:
                self._table.selectRow(row)
                self._table.scrollToItem(it)
                return True
        return False

    def _on_table_context_menu(self, pos) -> None:
        tid = self._selected_entry_id()
        if not tid:
            return
        menu = QMenu(self._table)
        copy_act = menu.addAction(
            lucide_icon("link", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Copy MONOS Link",
        )
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == copy_act:
            self.copy_link_requested.emit(tid)

    def _on_restore(self) -> None:
        pr = self._project_root
        tid = self._selected_entry_id()
        if pr is None or not tid:
            return
        try:
            restore_trash_entry(pr, tid)
        except TrashError as e:
            QMessageBox.warning(self, "Restore", str(e))
            return
        self.trash_changed.emit()
        self.refresh()

    def _on_delete_one(self) -> None:
        pr = self._project_root
        tid = self._selected_entry_id()
        if pr is None or not tid:
            return
        if not ask_delete(
            self,
            "Delete permanently",
            "This will permanently delete the selected item from the trash folder. This cannot be undone.",
        ):
            return
        try:
            delete_trash_entry_permanently(pr, tid)
        except TrashError as e:
            QMessageBox.warning(self, "Delete", str(e))
            return
        self.trash_changed.emit()
        self.refresh()

    def _on_empty_all(self) -> None:
        pr = self._project_root
        if pr is None:
            return
        try:
            n = len(list_entries(pr))
        except OSError:
            n = 0
        if n == 0:
            return
        if not ask_delete(
            self,
            "Empty trash",
            f"Permanently delete all {n} item(s) in trash? This cannot be undone.",
        ):
            return
        try:
            empty_trash(pr)
        except (TrashError, OSError) as e:
            logger.warning("empty_trash failed: %s", e)
            QMessageBox.warning(self, "Empty trash", str(e))
            return
        self.trash_changed.emit()
        self.refresh()
