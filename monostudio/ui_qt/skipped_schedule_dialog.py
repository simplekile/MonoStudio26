"""Dialog listing skipped schedule items and departments."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from monostudio.core.schedule_skip import SkippedScheduleRow, SkippedScheduleSnapshot
from monostudio.ui_qt.style import MonosDialog, monos_font, page_scope_accent


def _qcolor(hex_color: str) -> QColor:
    return QColor(hex_color)


def _scope_label(row: SkippedScheduleRow) -> str:
    return "Item" if (row.scope or "").strip().lower() == "item" else "Department"


class SkippedScheduleDialog(MonosDialog):
    """Skipped items / departments from Schedule — open in Assets/Shots or reveal on timeline."""

    open_in_main_view = Signal(str, str, str, str)  # kind, rel, department, name
    open_in_schedule = Signal(str, str, str)  # kind, rel, department

    _COL_NAME = 0
    _COL_TYPE = 1
    _COL_SCOPE = 2
    _COL_DEPT = 3

    def __init__(
        self,
        snapshot: SkippedScheduleSnapshot | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SkippedScheduleDialog")
        self._snapshot = snapshot or SkippedScheduleSnapshot(0, 0, ())
        self._rows: list[SkippedScheduleRow] = list(self._snapshot.rows)
        self.setWindowTitle("Skipped")
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("SKIPPED", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        root.addWidget(title, 0)

        self._hint = QLabel("", self)
        self._hint.setObjectName("DialogHint")
        root.addWidget(self._hint, 0)

        self._table = QTableWidget(self)
        self._table.setObjectName("InboxHistoryTable")
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Name", "Type", "Scope", "Department"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            self._COL_NAME, QHeaderView.ResizeMode.Stretch
        )
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.cellDoubleClicked.connect(self._on_row_activated)
        self._table.itemSelectionChanged.connect(self._sync_action_buttons)
        root.addWidget(self._table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._btn_open_main = QPushButton("Open in Assets", self)
        self._btn_open_main.setObjectName("DialogPrimaryButton")
        self._btn_open_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_main.clicked.connect(self._open_selected_in_main_view)
        actions.addWidget(self._btn_open_main, 0)

        self._btn_open_schedule = QPushButton("Open in Schedule", self)
        self._btn_open_schedule.setObjectName("DialogSecondaryButton")
        self._btn_open_schedule.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_schedule.clicked.connect(self._open_selected_in_schedule)
        actions.addWidget(self._btn_open_schedule, 0)

        actions.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogSecondaryButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn, 0)
        root.addLayout(actions, 0)

        self.setMinimumSize(720, 360)
        self.resize(880, 460)
        self._load_rows()

    def set_snapshot(self, snapshot: SkippedScheduleSnapshot | None) -> None:
        self._snapshot = snapshot or SkippedScheduleSnapshot(0, 0, ())
        self._rows = list(self._snapshot.rows)
        self._load_rows()

    def _load_rows(self) -> None:
        snap = self._snapshot
        n = len(self._rows)
        if n:
            self._hint.setText(
                f"{snap.item_count} fully skipped {'item' if snap.item_count == 1 else 'items'}"
                f" · {snap.department_count} skipped "
                f"{'department' if snap.department_count == 1 else 'departments'}."
            )
        else:
            self._hint.setText("No skipped items or departments in the current schedule scope.")

        self._table.setRowCount(n)
        for row_idx, row in enumerate(self._rows):
            kind_label = "Shot" if (row.entity_kind or "").strip().lower() == "shot" else "Asset"
            kind_accent = page_scope_accent("shot" if kind_label == "Shot" else "asset")

            name_item = QTableWidgetItem((row.entity_name or "").strip() or "—")
            name_item.setData(Qt.ItemDataRole.UserRole, row)
            self._table.setItem(row_idx, self._COL_NAME, name_item)

            type_item = QTableWidgetItem(kind_label.upper())
            type_item.setForeground(_qcolor(kind_accent))
            self._table.setItem(row_idx, self._COL_TYPE, type_item)

            scope_item = QTableWidgetItem(_scope_label(row))
            scope_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            self._table.setItem(row_idx, self._COL_SCOPE, scope_item)

            dept_item = QTableWidgetItem((row.department_label or "—").strip() or "—")
            self._table.setItem(row_idx, self._COL_DEPT, dept_item)

        if n:
            self._table.selectRow(0)
        self._sync_action_buttons()

    def _selected_row(self) -> SkippedScheduleRow | None:
        items = self._table.selectedItems()
        if not items:
            return None
        item = self._table.item(items[0].row(), self._COL_NAME)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, SkippedScheduleRow) else None

    def _sync_action_buttons(self) -> None:
        row = self._selected_row()
        has_row = row is not None
        self._btn_open_main.setEnabled(has_row)
        self._btn_open_schedule.setEnabled(has_row)
        if row is None:
            self._btn_open_main.setText("Open in Assets")
            return
        ctx = "Shots" if (row.entity_kind or "").strip().lower() == "shot" else "Assets"
        self._btn_open_main.setText(f"Open in {ctx}")

    def _department_for_row(self, row: SkippedScheduleRow) -> str:
        if (row.scope or "").strip().lower() == "item":
            return ""
        return (row.department or "").strip()

    def _emit_main_view(self, row: SkippedScheduleRow) -> None:
        self.open_in_main_view.emit(
            row.entity_kind,
            row.entity_rel,
            self._department_for_row(row),
            row.entity_name,
        )

    def _emit_schedule(self, row: SkippedScheduleRow) -> None:
        self.open_in_schedule.emit(
            row.entity_kind,
            row.entity_rel,
            self._department_for_row(row),
        )

    def _open_selected_in_main_view(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._emit_main_view(row)

    def _open_selected_in_schedule(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._emit_schedule(row)

    def _on_row_activated(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._rows):
            return
        self._emit_schedule(self._rows[row])

    def _on_context_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        if item is None:
            return
        row_idx = item.row()
        if row_idx < 0 or row_idx >= len(self._rows):
            return
        row = self._rows[row_idx]
        menu = QMenu(self)
        ctx = "Shots" if (row.entity_kind or "").strip().lower() == "shot" else "Assets"
        open_main = menu.addAction(f"Open in {ctx}")
        open_sched = menu.addAction("Open in Schedule")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is open_main:
            self._emit_main_view(row)
        elif chosen is open_sched:
            self._emit_schedule(row)
