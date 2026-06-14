"""Popover for Dashboard department workload row — overdue + due soon drill-down."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent
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

from monostudio.core.project_dashboard_stats import DashboardDeptStat, DashboardSnapshot
from monostudio.core.schedule_planner import OverdueEntityRow, UpcomingDueRow
from monostudio.ui_qt.dashboard_responsive_row import DashboardElidedLabel
from monostudio.ui_qt.popup_position import max_popup_height_for_anchor, position_popup_near_anchor
from monostudio.ui_qt.style import MONOS_COLORS, monos_font, page_scope_accent, schedule_attention_accent


def _entity_in_department(row: OverdueEntityRow, dept_id: str) -> bool:
    dep = (dept_id or "").strip()
    if not dep:
        return False
    if (row.primary_department or "").strip() == dep:
        return True
    return dep in {(d or "").strip() for d in row.department_ids}


def _transparent_surface(widget: QWidget) -> None:
    """Avoid Qt's default black fill on popup scroll surfaces (Windows)."""
    widget.setAutoFillBackground(False)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class _DeptWorkloadEntityRow(QFrame):
    clicked = Signal()

    _ROW_H = 36

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardDeptWorkloadEntityRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self._ROW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class DeptWorkloadPopover(QFrame):
    """Popup panel: overdue entities + due-soon tasks for one department."""

    open_schedule = Signal(str)  # department id
    entity_nav = Signal(str, str, str, str)  # kind, rel, department, name

    _MAX_ROWS = 8
    _SCROLL_MIN_H = 120
    _SCROLL_MAX_H = 280

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardDeptWorkloadPopover")
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._title = QLabel("", self)
        self._title.setObjectName("DialogSectionTitle")
        self._title.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        root.addWidget(self._title, 0)

        self._summary = QLabel("", self)
        self._summary.setObjectName("DialogHint")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("DashboardDeptWorkloadScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setMinimumHeight(self._SCROLL_MIN_H)
        self._scroll.setMaximumHeight(self._SCROLL_MAX_H)
        _transparent_surface(self._scroll)
        _transparent_surface(self._scroll.viewport())
        self._body = QWidget(self._scroll)
        self._body.setObjectName("DashboardDeptWorkloadScrollBody")
        _transparent_surface(self._body)
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(4)
        self._scroll.setWidget(self._body)
        root.addWidget(self._scroll, 0)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_schedule = QPushButton("Open in Schedule", self)
        self._btn_schedule.setObjectName("DialogPrimaryButton")
        self._btn_schedule.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_schedule.clicked.connect(self._on_open_schedule)
        btn_row.addWidget(self._btn_schedule, 0)
        root.addLayout(btn_row, 0)

        self._dept_id = ""
        self._stat: DashboardDeptStat | None = None
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)

    def show_for_department(
        self,
        *,
        anchor: QWidget,
        stat: DashboardDeptStat,
        snapshot: DashboardSnapshot | None,
    ) -> None:
        self._dept_id = (stat.department or "").strip()
        self._stat = stat
        self._title.setText(stat.department_label or stat.department)
        self._summary.setText(self._build_summary(stat))
        row_count = self._rebuild_body(snapshot)
        scroll_h = min(
            self._SCROLL_MAX_H,
            max(self._SCROLL_MIN_H, row_count * (_DeptWorkloadEntityRow._ROW_H + 4) + 40),
        )
        self._scroll.setFixedHeight(scroll_h)
        self.adjustSize()
        max_h = max_popup_height_for_anchor(anchor)
        if max_h and max_h > 0:
            self.setMaximumHeight(min(max_h, 480))
        position_popup_near_anchor(self, anchor, gap=4)
        self.show()

    def _build_summary(self, stat: DashboardDeptStat) -> str:
        parts: list[str] = []
        if stat.overdue:
            parts.append(f"{stat.overdue} overdue")
        if stat.due_soon:
            parts.append(f"{stat.due_soon} due soon")
        if stat.in_progress:
            parts.append(f"{stat.in_progress} active")
        parts.append(f"{stat.done}/{stat.total} done")
        return " · ".join(parts)

    def _rebuild_body(self, snapshot: DashboardSnapshot | None) -> int:
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        rows_added = 0
        if snapshot is None or not self._dept_id:
            self._body_lay.addWidget(self._hint_label("No schedule data."))
            return 1

        overdue_rows = [
            r
            for r in snapshot.dept_workload_overdue_rows
            if _entity_in_department(r, self._dept_id)
        ]
        due_soon_rows = [
            r
            for r in snapshot.dept_workload_upcoming_due
            if (r.department or "").strip() == self._dept_id and not r.overdue
        ]

        if not overdue_rows and not due_soon_rows:
            self._body_lay.addWidget(
                self._hint_label("No overdue or due-soon items in this department.")
            )
            return 1

        if overdue_rows:
            self._body_lay.addWidget(self._section_label("Overdue"))
            rows_added += 1
            for row in overdue_rows[: self._MAX_ROWS]:
                self._body_lay.addWidget(self._entity_row(row, overdue=True))
                rows_added += 1
            if len(overdue_rows) > self._MAX_ROWS:
                self._body_lay.addWidget(
                    self._hint_label(f"+{len(overdue_rows) - self._MAX_ROWS} more overdue")
                )
                rows_added += 1

        if due_soon_rows:
            self._body_lay.addWidget(self._section_label("Due soon"))
            rows_added += 1
            for row in due_soon_rows[: self._MAX_ROWS]:
                self._body_lay.addWidget(self._entity_row(row, overdue=False))
                rows_added += 1
            if len(due_soon_rows) > self._MAX_ROWS:
                self._body_lay.addWidget(
                    self._hint_label(f"+{len(due_soon_rows) - self._MAX_ROWS} more due soon")
                )
                rows_added += 1

        self._body_lay.addStretch(1)
        return rows_added

    def _section_label(self, text: str) -> QLabel:
        lab = QLabel(text.upper(), self._body)
        lab.setObjectName("DashboardSectionHeader")
        lab.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        lab.setStyleSheet(f"color: {MONOS_COLORS.get('text_label', '#a1a1aa')};")
        return lab

    def _hint_label(self, text: str) -> QLabel:
        lab = QLabel(text, self._body)
        lab.setObjectName("DialogHint")
        lab.setWordWrap(True)
        return lab

    def _entity_row(self, row: OverdueEntityRow | UpcomingDueRow, *, overdue: bool) -> _DeptWorkloadEntityRow:
        kind = (row.entity_kind or "").strip().lower()
        ctx = "Shot" if kind == "shot" else "Asset"
        accent = page_scope_accent(kind)
        frame = _DeptWorkloadEntityRow(self._body)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)

        name = DashboardElidedLabel(
            (row.entity_name or "").strip() or "—",
            font=monos_font("Inter", 13, QFont.Weight.DemiBold),
            parent=frame,
        )
        name.setStyleSheet("color: #e4e4e7; background: transparent;")
        lay.addWidget(name, 1)

        kind_lbl = QLabel(ctx, frame)
        kind_lbl.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        kind_lbl.setStyleSheet(
            f"color: {schedule_attention_accent('overdue') if overdue else accent};"
            " background: transparent;"
        )
        lay.addWidget(kind_lbl, 0)

        if isinstance(row, UpcomingDueRow):
            due_lbl = QLabel(_format_due(row.due), frame)
            due_lbl.setFont(monos_font("JetBrains Mono", 10, QFont.Weight.Medium))
            due_lbl.setStyleSheet("color: #71717a; background: transparent;")
            due_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(due_lbl, 0)

        dep = getattr(row, "department", "") or getattr(row, "primary_department", "")
        frame.clicked.connect(
            lambda k=row.entity_kind, r=row.entity_rel, d=dep, n=row.entity_name: (
                self.entity_nav.emit(k, r, d, n)
            )
        )
        return frame

    def _on_open_schedule(self) -> None:
        if self._dept_id:
            self.open_schedule.emit(self._dept_id)
        self.hide()


def _format_due(due: date) -> str:
    delta = (due - date.today()).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    return f"in {delta}d"
