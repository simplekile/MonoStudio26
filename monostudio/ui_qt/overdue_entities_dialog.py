"""Dialog listing overdue assets/shots — jump to main view or Schedule."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
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

from monostudio.core.schedule_planner import OverdueEntityRow
from monostudio.ui_qt.style import (
    MonosDialog,
    monos_font,
    page_scope_accent,
    schedule_attention_accent,
)


def _format_overdue_by(days: int) -> str:
    if days <= 0:
        return "Due today"
    unit = "day" if days == 1 else "days"
    return f"{days} {unit} overdue"


def _format_departments(row: OverdueEntityRow) -> str:
    labels = list(row.department_labels)
    if not labels:
        return row.primary_department_label or row.primary_department or "—"
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} +{len(labels) - 1}"


class OverdueEntitiesDialog(MonosDialog):
    """Overdue entity list from Dashboard — open in Assets/Shots or Schedule."""

    open_in_main_view = Signal(str, str, str, str)  # kind, rel, department, name
    open_in_schedule = Signal(str, str, str)  # kind, rel, department

    _COL_NAME = 0
    _COL_TYPE = 1
    _COL_TASKS = 2
    _COL_LATE = 3
    _COL_DEPTS = 4

    def __init__(self, rows: list[OverdueEntityRow] | tuple[OverdueEntityRow, ...] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("OverdueEntitiesDialog")
        self._rows: list[OverdueEntityRow] = list(rows or ())
        self.setWindowTitle("Overdue tasks")
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("OVERDUE TASKS", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        root.addWidget(title, 0)

        self._hint = QLabel("", self)
        self._hint.setObjectName("DialogHint")
        root.addWidget(self._hint, 0)

        self._table = QTableWidget(self)
        self._table.setObjectName("InboxHistoryTable")
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Type", "Tasks", "Overdue by", "Departments"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(self._COL_NAME, QHeaderView.ResizeMode.Stretch)
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

        self.setMinimumSize(760, 380)
        self.resize(920, 480)
        self._load_rows()

    def set_rows(self, rows: list[OverdueEntityRow] | tuple[OverdueEntityRow, ...] | None) -> None:
        self._rows = list(rows or ())
        self._load_rows()

    def _load_rows(self) -> None:
        n = len(self._rows)
        overdue_color = schedule_attention_accent("overdue")
        self._hint.setText(
            f"{n} {'entity' if n == 1 else 'entities'} with overdue schedule tasks."
            if n
            else "No overdue tasks in the current schedule scope."
        )
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

            tasks_item = QTableWidgetItem(str(row.overdue_bar_count))
            tasks_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            self._table.setItem(row_idx, self._COL_TASKS, tasks_item)

            late_item = QTableWidgetItem(_format_overdue_by(row.worst_overdue_days))
            late_item.setForeground(_qcolor(overdue_color))
            self._table.setItem(row_idx, self._COL_LATE, late_item)

            dept_item = QTableWidgetItem(_format_departments(row))
            self._table.setItem(row_idx, self._COL_DEPTS, dept_item)

        if n:
            self._table.selectRow(0)
        self._sync_action_buttons()

    def _selected_row(self) -> OverdueEntityRow | None:
        items = self._table.selectedItems()
        if not items:
            return None
        item = self._table.item(items[0].row(), self._COL_NAME)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, OverdueEntityRow) else None

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

    def _emit_main_view(self, row: OverdueEntityRow) -> None:
        self.open_in_main_view.emit(
            row.entity_kind,
            row.entity_rel,
            row.primary_department,
            row.entity_name,
        )

    def _emit_schedule(self, row: OverdueEntityRow) -> None:
        self.open_in_schedule.emit(
            row.entity_kind,
            row.entity_rel,
            row.primary_department,
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
        item = self._table.item(row, self._COL_NAME)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, OverdueEntityRow):
            self._emit_main_view(data)

    def _on_context_menu(self, position) -> None:
        idx = self._table.indexAt(position)
        if not idx.isValid() or idx.row() < 0:
            return
        item = self._table.item(idx.row(), self._COL_NAME)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, OverdueEntityRow):
            return
        menu = QMenu(self)
        ctx = "Shots" if (data.entity_kind or "").strip().lower() == "shot" else "Assets"
        act_main = menu.addAction(f"Open in {ctx}")
        act_sched = menu.addAction("Open in Schedule")
        chosen = menu.exec(self._table.viewport().mapToGlobal(position))
        if chosen is act_main:
            self._emit_main_view(data)
        elif chosen is act_sched:
            self._emit_schedule(data)


def _qcolor(hex_str: str):
    from PySide6.QtGui import QColor

    return QColor((hex_str or "#fafafa").strip())
