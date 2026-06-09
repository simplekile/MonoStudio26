"""Gantt-style timeline for the project schedule.

Entity rows are grouped (one row per shot/asset) with stacked mini-bars when
collapsed. Click the chevron to expand into per-department rows.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.fs_reader import ProjectIndex
from monostudio.core.project_schedule import (
    ProjectSchedule,
    ScheduleAllocation,
    TimelineEntityGroup,
    TimelineRow,
    allocation_for_row,
    build_timeline_entity_groups,
    delete_allocation,
    bulk_upsert_allocations,
    clear_auto_bar_suppression_for_row,
    clear_department_schedule_for_entities,
    clear_entity_department_schedules,
    delete_wave_for_row,
    replace_entity_department_allocations,
    entity_has_schedule,
    entity_is_unscheduled,
    entity_rel_path,
    new_allocation_id,
    read_project_schedule,
    resolve_schedule_project_start,
    target_for_entity,
    upsert_allocation_for_row,
)
from monostudio.core.schedule_date_display import (
    format_schedule_date_span,
    min_bar_width_for_date_format,
    normalize_date_display_format,
)
from monostudio.core.schedule_dept_filter import (
    BAR_LABEL_DATE_RANGE,
    BAR_LABEL_DAYS,
    BAR_LABEL_DEPARTMENT,
    BAR_LABEL_ENTITY_NAME,
    BAR_LABEL_OFF,
    DEPT_SCOPE_LEAF,
    filter_entity_groups,
    load_inspector_hidden_departments,
    normalize_bar_label_mode,
)
from monostudio.core.production_status import SKIPPED_STATUS_ID
from monostudio.core.schedule_planner import (
    STATUS_DONE,
    STATUS_EXCLUDED,
    STATUS_PROGRESS,
    STATUS_WAITING,
    DeptWaveRollup,
    PlannedBar,
    build_planned_bars,
    compute_view_date_range_from_bars,
    rollup_bars_by_department,
)
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.lucide_icons import lucide_icon

if TYPE_CHECKING:
    from monostudio.core.models import Asset, Shot
    from monostudio.ui_qt.thumbnails import ThumbnailManager

_LABEL_W_DEFAULT = 220
_LABEL_W_MIN = 160
_LABEL_W_MAX = 420
_LABEL_COL_RESIZE_W = 10
_LABEL_COL_EDGE_GRAB = 10
_ROW_H = 50
_ROW_TITLE_Y = 7
_ROW_TITLE_H = 18
_ROW_SUB_Y = 28
_ROW_SUB_H = 16
# Header rows below IN/OUT band (14px): milestone names → month → weekday + day numbers.
_HEADER_RANGE_BAND_H = 14
_HEADER_MILESTONE_TOP = 15
_HEADER_MILESTONE_H = 11
_HEADER_MONTH_TOP = 28
_HEADER_MONTH_H = 12
_HEADER_MONTH_DAY_GAP = 7
_HEADER_DAY_TOP = _HEADER_MONTH_TOP + _HEADER_MONTH_H + _HEADER_MONTH_DAY_GAP
_HEADER_WEEKDAY_H = 13  # fits Inter 9 without vertical clip
_HEADER_DAY_GAP = 3
_HEADER_DAY_NUM_H = 13  # fits JetBrains Mono 9 without vertical clip
_HEADER_DAY_H = _HEADER_WEEKDAY_H + _HEADER_DAY_GAP + _HEADER_DAY_NUM_H
_HEADER_BOTTOM_PAD = 4
_HEADER_H = _HEADER_DAY_TOP + _HEADER_DAY_H + _HEADER_BOTTOM_PAD
# Vertical marker lines in the header stop above the day-number row.
_HEADER_MARKER_LINE_BOTTOM = _HEADER_DAY_TOP - 1
_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DAY_W = 28.0
_BAR_H = 18
_MINI_BAR_H = 6
_MINI_BAR_GAP = 2
_EDGE_GRAB = 7
# Hover/drag key for collapsed entity row (whole block, not a single dept mini-bar).
_COLLAPSED_GROUP_HOVER = "__group__"
_CHEVRON_W = 20
_LABEL_THUMB = 28
_LABEL_TEXT_LEFT = _CHEVRON_W + _LABEL_THUMB + 6
_DEPT_INDENT = 16
# Expanded entity → department sub-rows (label column).
_DEPT_SUBROW_COLOR = QColor("#52525b")  # zinc-600
_SCOPE_LABEL_SHOTS = "SHOTS"
_SCOPE_LABEL_ASSETS = "ASSETS"

_OVERDUE_HEX = "#ef4444"
_RANGE_IN_HEX = "#10b981"
_RANGE_OUT_HEX = "#ef4444"
_RANGE_FILL = QColor(16, 185, 129, 12)
_OUTSIDE_RANGE_FILL = QColor(0, 0, 0, 28)
_DEADLINE_HEADER = QColor("#ef4444")

TOOL_SELECT = "select"
TOOL_DRAW = "draw"
WAVE_DRAW_SAME_DAYS = "same_days"
WAVE_DRAW_DISTRIBUTE = "distribute"
WAVE_DRAW_FIRST_ONLY = "first_only"
VIEW_ENTITY = "entity"
VIEW_DEPARTMENT = "department"
VIEW_DEPT_WAVE = "dept_wave"
_MIN_DAY_W = 4.0
_MAX_DAY_W = 56.0
# At or below this day width, header shows in-month weeks (W1, W2, …) instead of per-day labels.
_DAY_W_WEEK_IN_MONTH_MAX = 12.0
_ZOOM_STEP_FACTOR = 1.12
# Alt+RMB drag: screen pixels from press → day width (absolute, so slow drags still apply).
_ZOOM_DRAG_PIXELS_PER_DAY_W = 1.0 / 44.0
_WHEEL_ZOOM_DEGREES_PER_STEP = 120.0
_PANE_LABEL = "label"
_PANE_TIMELINE = "timeline"
_PANE_HEADER = "header"
_PANE_CORNER = "corner"
_DRAW_PREVIEW = QColor(MONOS_COLORS.get("blue_400", "#60a5fa"))


def _row_key(kind: str, rel: str, dept: str | None) -> tuple[str, str, str]:
    return (kind, (rel or "").replace("\\", "/"), (dept or "").strip())


@dataclass(frozen=True)
class _DisplayRow:
    """One painted/interactive row."""

    mode: str  # collapsed | header | dept | dept_lane_header | dept_lane | dept_wave | scope_separator
    group: TimelineEntityGroup | None = None
    dept: TimelineRow | None = None
    lane_label: str = ""
    wave: DeptWaveRollup | None = None
    count: int = 0  # scope_separator: number of items in the section (ASSETS n / SHOTS n)


@dataclass
class _BarHit:
    visible_index: int
    department: str
    mode: str  # move | resize_start | resize_end
    is_wave_row: bool = False
    is_collapsed_group: bool = False


@dataclass
class _DrawState:
    visible_index: int
    department: str
    anchor: date
    start: date
    due: date
    is_wave_row: bool = False


def _scope_sections(
    groups: list[TimelineEntityGroup],
) -> list[tuple[str, list[TimelineEntityGroup]]]:
    """Partition groups into shot / asset sections (sorted by name)."""
    shots = sorted(
        [g for g in groups if (g.entity_kind or "").strip().lower() == "shot"],
        key=lambda g: (g.entity_name or "").casefold(),
    )
    assets = sorted(
        [g for g in groups if (g.entity_kind or "").strip().lower() == "asset"],
        key=lambda g: (g.entity_name or "").casefold(),
    )
    out: list[tuple[str, list[TimelineEntityGroup]]] = []
    if assets:
        out.append(("asset", assets))
    if shots:
        out.append(("shot", shots))
    return out


def _scope_separator_row(label: str, count: int = 0) -> _DisplayRow:
    return _DisplayRow(mode="scope_separator", lane_label=label, count=count)


def _dept_lane_entity_rows(
    lane: list[tuple[TimelineEntityGroup, TimelineRow]],
    *,
    show_scope_separators: bool,
) -> list[_DisplayRow]:
    """Entity rows under a department lane, optional SHOTS / ASSETS separators."""
    shots = sorted(
        [pair for pair in lane if (pair[0].entity_kind or "").strip().lower() == "shot"],
        key=lambda x: x[0].entity_name.casefold(),
    )
    assets = sorted(
        [pair for pair in lane if (pair[0].entity_kind or "").strip().lower() == "asset"],
        key=lambda x: x[0].entity_name.casefold(),
    )
    sections: list[tuple[str, list[tuple[TimelineEntityGroup, TimelineRow]]]] = []
    if assets:
        sections.append((_SCOPE_LABEL_ASSETS, assets))
    if shots:
        sections.append((_SCOPE_LABEL_SHOTS, shots))
    multi = show_scope_separators and len(sections) > 1
    rows: list[_DisplayRow] = []
    for label, items in sections:
        if multi:
            rows.append(_scope_separator_row(label, count=len(items)))
        for group, dept_row in items:
            rows.append(_DisplayRow(mode="dept_lane", group=group, dept=dept_row))
    return rows


def _collect_department_lanes(
    groups: list[TimelineEntityGroup],
    dept_order: list[str],
    dept_filter: str | None,
) -> list[tuple[str, str, list[tuple[TimelineEntityGroup, TimelineRow]]]]:
    """(dep_id, department_label, entity rows) in registry order."""
    order = list(dept_order)
    seen: set[str] = set()
    blocks: list[tuple[str, str, list[tuple[TimelineEntityGroup, TimelineRow]]]] = []

    def _lane_for(dep_id: str) -> list[tuple[TimelineEntityGroup, TimelineRow]]:
        out: list[tuple[TimelineEntityGroup, TimelineRow]] = []
        for group in groups:
            for dept_row in group.departments:
                if (dept_row.department or "").strip() == dep_id:
                    out.append((group, dept_row))
        return out

    for dep_id in order:
        dep_id = (dep_id or "").strip()
        if not dep_id or dep_id in seen:
            continue
        if dept_filter and dep_id != dept_filter:
            continue
        seen.add(dep_id)
        lane = _lane_for(dep_id)
        if not lane:
            continue
        label = lane[0][1].department_label or dep_id
        blocks.append((dep_id, label, lane))

    rank = {d: i for i, d in enumerate(order)}
    trailing: list[str] = []
    for group in groups:
        for dept_row in group.departments:
            dep_id = (dept_row.department or "").strip()
            if not dep_id or dep_id in seen:
                continue
            if dept_filter and dep_id != dept_filter:
                continue
            trailing.append(dep_id)
    for dep_id in sorted(set(trailing), key=lambda d: (rank.get(d, 99999), d.casefold())):
        seen.add(dep_id)
        lane = _lane_for(dep_id)
        if not lane:
            continue
        label = lane[0][1].department_label or dep_id
        blocks.append((dep_id, label, lane))

    return blocks


def _lane_scope(lane: list[tuple[TimelineEntityGroup, TimelineRow]]) -> str:
    kinds = {(p[0].entity_kind or "").strip().lower() for p in lane if (p[0].entity_kind or "").strip()}
    kinds.discard("")
    if kinds == {"shot"}:
        return "shot"
    if kinds == {"asset"}:
        return "asset"
    return "mixed"


def _append_dept_lane_block(
    rows: list[_DisplayRow],
    *,
    dep_label: str,
    lane: list[tuple[TimelineEntityGroup, TimelineRow]],
) -> None:
    rows.append(
        _DisplayRow(
            mode="dept_lane_header",
            group=lane[0][0],
            dept=lane[0][1],
            lane_label=dep_label,
        )
    )
    for group, dept_row in sorted(lane, key=lambda x: x[0].entity_name.casefold()):
        rows.append(_DisplayRow(mode="dept_lane", group=group, dept=dept_row))


def _build_dept_lane_rows(
    groups: list[TimelineEntityGroup],
    dept_order: list[str],
    dept_filter: str | None,
    *,
    show_scope_separators: bool = False,
) -> list[_DisplayRow]:
    """
    Dept · lanes: global SHOTS / ASSETS sections, then department blocks inside each.
    (Per-dept separators only appeared when one dept had both kinds — rare.)
    """
    blocks = _collect_department_lanes(groups, dept_order, dept_filter)
    shot_blocks: list[tuple[str, str, list]] = []
    asset_blocks: list[tuple[str, str, list]] = []
    mixed_blocks: list[tuple[str, str, list]] = []

    for _dep_id, label, lane in blocks:
        scope = _lane_scope(lane)
        if scope == "shot":
            shot_blocks.append((label, lane))
        elif scope == "asset":
            asset_blocks.append((label, lane))
        else:
            mixed_blocks.append((label, lane))

    sections: list[tuple[str, list[tuple[str, list]]]] = []
    if asset_blocks:
        sections.append((_SCOPE_LABEL_ASSETS, asset_blocks))
    if shot_blocks:
        sections.append((_SCOPE_LABEL_SHOTS, shot_blocks))

    rows: list[_DisplayRow] = []
    multi_scope = show_scope_separators and len(sections) > 1

    for sec_label, dept_blocks in sections:
        if multi_scope:
            distinct = {g.key for _lbl, lane in dept_blocks for g, _r in lane}
            rows.append(_scope_separator_row(sec_label, count=len(distinct)))
        for label, lane in dept_blocks:
            _append_dept_lane_block(rows, dep_label=label, lane=lane)

    for label, lane in mixed_blocks:
        rows.append(
            _DisplayRow(
                mode="dept_lane_header",
                group=lane[0][0],
                dept=lane[0][1],
                lane_label=label,
            )
        )
        rows.extend(_dept_lane_entity_rows(lane, show_scope_separators=True))

    return rows


def _wave_rollup_entity_scope(rollup: DeptWaveRollup) -> str:
    """shot | asset | mixed — from entities that have this department in the wave."""
    kinds = {(k or "").strip().lower() for k, _ in rollup.entity_keys if (k or "").strip()}
    kinds.discard("")
    if kinds == {"shot"}:
        return "shot"
    if kinds == {"asset"}:
        return "asset"
    return "mixed"


def _build_dept_wave_rows(rollups: list[DeptWaveRollup]) -> list[_DisplayRow]:
    """Dept · wave rows with SHOTS / ASSETS separators when scope is split."""
    shot_r: list[DeptWaveRollup] = []
    asset_r: list[DeptWaveRollup] = []
    mixed_r: list[DeptWaveRollup] = []
    for rollup in rollups:
        scope = _wave_rollup_entity_scope(rollup)
        if scope == "shot":
            shot_r.append(rollup)
        elif scope == "asset":
            asset_r.append(rollup)
        else:
            mixed_r.append(rollup)

    sections: list[tuple[str | None, list[DeptWaveRollup]]] = []
    if asset_r:
        sections.append((_SCOPE_LABEL_ASSETS, asset_r))
    if shot_r:
        sections.append((_SCOPE_LABEL_SHOTS, shot_r))
    if mixed_r:
        sections.append((None, mixed_r))

    labeled = [label for label, _ in sections if label]
    show_sep = len(labeled) > 1

    out: list[_DisplayRow] = []
    for label, items in sections:
        if label and (show_sep or len(labeled) == 1):
            out.append(_scope_separator_row(label, count=len(items)))
        for rollup in items:
            out.append(
                _DisplayRow(
                    mode="dept_wave",
                    wave=rollup,
                    lane_label=rollup.department_label,
                )
            )
    return out


def _build_visible_rows(
    groups: list[TimelineEntityGroup],
    expanded: set[tuple[str, str]],
    *,
    view_mode: str = VIEW_ENTITY,
    dept_order: list[str] | None = None,
    dept_filter: str | None = None,
    wave_rollups: list[DeptWaveRollup] | None = None,
) -> list[_DisplayRow]:
    if view_mode == VIEW_DEPT_WAVE:
        return _build_dept_wave_rows(wave_rollups or [])
    if view_mode == VIEW_DEPARTMENT:
        return _build_dept_lane_rows(
            groups,
            dept_order or [],
            dept_filter,
            show_scope_separators=True,
        )
    out: list[_DisplayRow] = []
    sections = _scope_sections(groups)
    multi_scope = len(sections) > 1
    for kind, section_groups in sections:
        if multi_scope:
            label = _SCOPE_LABEL_SHOTS if kind == "shot" else _SCOPE_LABEL_ASSETS
            out.append(_scope_separator_row(label, count=len(section_groups)))
        for group in section_groups:
            if group.key in expanded:
                out.append(_DisplayRow(mode="header", group=group))
                for dept_row in group.departments:
                    if dept_filter and (dept_row.department or "").strip() != dept_filter:
                        continue
                    out.append(_DisplayRow(mode="dept", group=group, dept=dept_row))
            else:
                out.append(_DisplayRow(mode="collapsed", group=group))
    return out


# (entity_kind, entity_rel, department or None for all depts on entity)
_ScheduleHighlight = tuple[str, str, str | None]

_HIGHLIGHT_FILL = QColor(37, 99, 235, 42)
_HIGHLIGHT_EDGE = QColor("#60a5fa")


class _GanttCanvas(QWidget):
    row_activated = Signal(int)  # visible row index — double-click
    override_committed = Signal()
    expand_toggled = Signal(tuple, bool)  # group key, is_expanded
    wave_drilldown_requested = Signal(str)  # department id
    entity_plan_requested = Signal(str, str)  # entity_kind, entity_rel
    entity_clear_plan_requested = Signal(str, str)
    entity_row_selected = Signal(str, str)  # entity_kind, entity_rel
    entity_row_cleared = Signal()  # Ctrl+click or empty-area click — clear highlight / Inspector
    department_skip_toggle_requested = Signal(str, str, str, bool)  # kind, rel, dep, skip

    def __init__(self, parent=None, *, pane: str = _PANE_TIMELINE) -> None:
        super().__init__(parent)
        self._pane = pane if pane in (_PANE_LABEL, _PANE_TIMELINE, _PANE_HEADER, _PANE_CORNER) else _PANE_TIMELINE
        self._partner: _GanttCanvas | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._groups: list[TimelineEntityGroup] = []
        self._visible: list[_DisplayRow] = []
        self._expanded: set[tuple[str, str]] = set()
        self._bars: dict[tuple[str, str, str], PlannedBar] = {}
        self._schedule = ProjectSchedule()
        self._view_start = date.today()
        self._view_end = date.today() + timedelta(days=56)
        self._project_root: Path | None = None
        self._workspace_root: Path | None = None
        self._drag: _BarHit | None = None
        self._drag_origin_x = 0
        self._drag_orig_start: date | None = None
        self._drag_orig_due: date | None = None
        self._drag_dates: dict[tuple[str, str, str], tuple[date, date]] = {}
        self._drag_collapsed_orig: dict[tuple[str, str, str], tuple[date, date]] = {}
        self._hover_row: int | None = None
        self._hover_bar: tuple[int, str] | None = None  # (visible_index, dept)
        self._highlight: _ScheduleHighlight | None = None
        self._highlight_entities: frozenset[tuple[str, str]] | None = None
        self._tool = TOOL_SELECT
        self._draw_state: _DrawState | None = None
        self._view_mode = VIEW_ENTITY
        self._dept_order: list[str] = []
        self._dept_filter: str | None = None
        self._day_w = _DAY_W
        self._wave_rollups: list[DeptWaveRollup] = []
        self._wave_draw_apply_mode: str = WAVE_DRAW_SAME_DAYS
        self._bar_label_mode: str = BAR_LABEL_DAYS
        self._date_display_format: str = normalize_date_display_format(None)
        self._wave_drag_preview: dict[str, tuple[date, date]] = {}
        self._gantt: ScheduleGanttWidget | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _schedule_editable(self) -> bool:
        return self._gantt is None or self._gantt._schedule_editable

    @staticmethod
    def _owns_mouse_grab(widget: QWidget) -> bool:
        return QWidget.mouseGrabber() is widget

    @staticmethod
    def _release_if_grabbed(widget: QWidget) -> None:
        if _GanttCanvas._owns_mouse_grab(widget):
            widget.releaseMouse()

    def set_day_width(self, day_w: float) -> None:
        self._day_w = max(_MIN_DAY_W, min(_MAX_DAY_W, float(day_w)))
        self._update_minimum_size()
        self.update()

    @staticmethod
    def _tool_idle_cursor(tool: str) -> Qt.CursorShape:
        if tool == TOOL_DRAW:
            return Qt.CursorShape.CrossCursor
        return Qt.CursorShape.OpenHandCursor

    def set_tool(self, tool: str) -> None:
        self._tool = tool if tool in (TOOL_SELECT, TOOL_DRAW) else TOOL_SELECT
        self._set_draw_state(None)
        self._drag = None
        self._drag_dates.clear()
        self._drag_collapsed_orig.clear()
        self._wave_drag_preview.clear()
        if self._gantt is not None:
            self._gantt._apply_tool_cursors()
        else:
            self.setCursor(self._tool_idle_cursor(self._tool))
        self.update()

    def _set_draw_state(self, state: _DrawState | None) -> None:
        self._draw_state = state
        if self._gantt is None:
            self.update()
            return
        for pane in (self._gantt._label_pane, self._gantt._timeline_pane):
            if pane is not self:
                pane._draw_state = state
                pane.update()
        self.update()

    def set_wave_draw_apply_mode(self, mode: str) -> None:
        if mode in (WAVE_DRAW_SAME_DAYS, WAVE_DRAW_DISTRIBUTE, WAVE_DRAW_FIRST_ONLY):
            self._wave_draw_apply_mode = mode

    def set_bar_label_mode(self, mode: str) -> None:
        self._bar_label_mode = normalize_bar_label_mode(mode)
        self.update()

    def set_date_display_format(self, fmt_id: str) -> None:
        self._date_display_format = normalize_date_display_format(fmt_id)
        self.update()

    def _format_date_span(self, start: date, due: date) -> str:
        return format_schedule_date_span(start, due, self._date_display_format)

    def _bar_label_text(
        self,
        *,
        start: date,
        due: date,
        bar: PlannedBar | None = None,
        wave: DeptWaveRollup | None = None,
    ) -> str:
        mode = self._bar_label_mode
        if mode == BAR_LABEL_OFF:
            return ""
        days = max(1, (due - start).days + 1)
        if mode == BAR_LABEL_DAYS:
            return f"{days}d"
        if mode == BAR_LABEL_DATE_RANGE:
            return self._format_date_span(start, due)
        if mode == BAR_LABEL_ENTITY_NAME:
            if bar is not None:
                name = (bar.entity_name or "").strip()
                if name:
                    return name
                rel = (bar.entity_rel or "").replace("\\", "/")
                return rel.rsplit("/", 1)[-1] if rel else ""
            if wave is not None:
                return f"{wave.entity_count} items"
            return ""
        if mode == BAR_LABEL_DEPARTMENT:
            if bar is not None:
                return (bar.department_label or bar.department or "").strip()
            if wave is not None:
                return (wave.department_label or wave.department or "").strip()
        return ""

    def _min_bar_label_width(self) -> int:
        mode = self._bar_label_mode
        if mode == BAR_LABEL_OFF:
            return 0
        if mode == BAR_LABEL_DATE_RANGE:
            return min_bar_width_for_date_format(self._date_display_format)
        if mode == BAR_LABEL_DAYS:
            return 14
        return 32

    def _paint_bar_label_text(
        self,
        p: QPainter,
        rect: QRect | QRectF,
        text: str,
        *,
        status: str,
        overdue: bool,
    ) -> None:
        if not text:
            return
        r = rect.toRect() if isinstance(rect, QRectF) else rect
        min_w = self._min_bar_label_width()
        if min_w and r.width() < min_w:
            return
        font = monos_font("Inter", 9, QFont.Weight.DemiBold)
        p.setFont(font)
        fm = QFontMetrics(font)
        pad_x, pad_y = 6, 2
        max_text_w = max(8, r.width() - pad_x * 2 - 4)
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, max_text_w)
        text_w = fm.horizontalAdvance(elided)
        text_h = fm.height()
        pill_w = min(max(12, r.width() - 4), text_w + pad_x * 2)
        pill_h = min(max(10, r.height() - 2), text_h + pad_y * 2)
        pill_x = r.x() + (r.width() - pill_w) // 2
        pill_y = r.y() + (r.height() - pill_h) // 2
        pill = QRect(pill_x, pill_y, pill_w, pill_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 120))
        p.drawRoundedRect(pill, 4, 4)
        p.setPen(QColor("#18181b"))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, elided)

    def set_data(
        self,
        *,
        project_root: Path | None,
        workspace_root: Path | None = None,
        groups: list[TimelineEntityGroup],
        expanded: set[tuple[str, str]],
        bars: dict[tuple[str, str, str], PlannedBar],
        schedule: ProjectSchedule,
        view_start: date,
        view_end: date,
        view_mode: str = VIEW_ENTITY,
        dept_order: list[str] | None = None,
        dept_filter: str | None = None,
        wave_rollups: list[DeptWaveRollup] | None = None,
    ) -> None:
        self._project_root = Path(project_root) if project_root else None
        try:
            self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        except OSError:
            self._workspace_root = None
        self._groups = list(groups)
        self._expanded = set(expanded)
        self._bars = dict(bars)
        self._schedule = schedule
        self._view_start = view_start
        self._view_end = view_end
        self._view_mode = view_mode if view_mode in (VIEW_ENTITY, VIEW_DEPARTMENT, VIEW_DEPT_WAVE) else VIEW_ENTITY
        self._dept_order = list(dept_order or [])
        self._dept_filter = dept_filter
        self._wave_rollups = list(wave_rollups or [])
        self._visible = _build_visible_rows(
            self._groups,
            self._expanded,
            view_mode=self._view_mode,
            dept_order=self._dept_order,
            dept_filter=self._dept_filter,
            wave_rollups=self._wave_rollups,
        )
        self._drag_dates.clear()
        self._drag_collapsed_orig.clear()
        self._wave_drag_preview.clear()
        self._update_minimum_size()
        self.update()

    def _wave_for_department(self, department: str) -> DeptWaveRollup | None:
        dep = (department or "").strip()
        if not dep:
            return None
        for wave in self._wave_rollups:
            if (wave.department or "").strip() == dep:
                return wave
        return None

    def _entity_department_id_for_wave(self, wave_dep: str, kind: str, rel: str) -> str:
        """Timeline department folder id for one entity on a wave row."""
        for group in self._groups:
            if (group.entity_kind or "").strip().lower() != kind:
                continue
            if (group.entity_rel or "").replace("\\", "/") != rel:
                continue
            for row in group.departments:
                row_dep = (row.department or "").strip()
                if row_dep == wave_dep:
                    return row_dep
            break
        return wave_dep

    def _wave_draw_targets(self, wave: DeptWaveRollup) -> list[tuple[str, str, str]]:
        """(entity_kind, entity_rel, department_id) rows affected by a dept-wave draw."""
        wave_dep = (wave.department or "").strip()
        if not wave_dep or not wave.entity_keys:
            return []
        out: list[tuple[str, str, str]] = []
        for kind, rel in wave.entity_keys:
            kind_n = (kind or "").strip().lower()
            rel_n = (rel or "").replace("\\", "/")
            if kind_n not in ("asset", "shot") or not rel_n:
                continue
            dept_id = self._entity_department_id_for_wave(wave_dep, kind_n, rel_n)
            out.append((kind_n, rel_n, dept_id))
        out.sort(key=lambda x: (x[2].casefold(), x[1].casefold()))
        return out

    def _wave_bar_keys(self, wave: DeptWaveRollup | None) -> list[tuple[str, str, str]]:
        keys: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        if wave is None:
            return keys
        for kind, rel, dept_id in self._wave_draw_targets(wave):
            key = _row_key(kind, rel, dept_id)
            if key in self._bars and key not in seen:
                seen.add(key)
                keys.append(key)
        return keys

    def _wave_department_has_bars(
        self, department: str, *, wave: DeptWaveRollup | None = None
    ) -> bool:
        wave = wave or self._wave_for_department(department)
        return bool(self._wave_bar_keys(wave))

    def _wave_row_dates(self, wave: DeptWaveRollup) -> tuple[date, date]:
        dep = (wave.department or "").strip()
        if dep in self._wave_drag_preview:
            return self._wave_drag_preview[dep]
        return wave.start, wave.due

    @staticmethod
    def _split_date_range_evenly(start: date, due: date, count: int) -> list[tuple[date, date]]:
        if count <= 0:
            return []
        if count == 1:
            return [(start, due)]
        spans: list[tuple[date, date]] = []
        cursor = start
        for i in range(count):
            remaining = count - i
            remaining_days = max(1, (due - cursor).days + 1)
            chunk = max(1, remaining_days // remaining)
            seg_end = cursor + timedelta(days=chunk - 1)
            if i == count - 1:
                seg_end = due
            spans.append((cursor, seg_end))
            cursor = seg_end + timedelta(days=1)
            if cursor > due:
                break
        while len(spans) < count:
            spans.append((due, due))
        return spans[:count]

    def _num_days(self) -> int:
        return max(1, (self._view_end - self._view_start).days + 1)

    def _is_label_pane(self) -> bool:
        return self._pane == _PANE_LABEL

    def _is_body_pane(self) -> bool:
        return self._pane in (_PANE_LABEL, _PANE_TIMELINE)

    def _body_row_y(self, visible_index: int) -> int:
        return visible_index * _ROW_H

    def _label_col_w(self) -> int:
        g = self._gantt
        if g is not None:
            return g.label_column_width()
        return _LABEL_W_DEFAULT

    def _content_width(self) -> int:
        if self._pane in (_PANE_CORNER, _PANE_LABEL):
            return self._label_col_w()
        return int(self._num_days() * self._day_w)

    def _content_height(self) -> int:
        if self._pane in (_PANE_CORNER, _PANE_HEADER):
            return _HEADER_H
        return max(1, len(self._visible)) * _ROW_H

    def _update_minimum_size(self) -> None:
        cw = self._content_width()
        ch = self._content_height()
        self.setMinimumSize(cw, ch)
        if self.width() != cw or self.height() != ch:
            self.resize(cw, ch)

    def _date_to_x(self, d: date) -> float:
        offset = (d - self._view_start).days
        return offset * self._day_w + self._day_w * 0.5

    def _date_to_x_start(self, d: date) -> float:
        return (d - self._view_start).days * self._day_w

    def _date_to_x_end(self, d: date) -> float:
        return (d - self._view_start).days * self._day_w + self._day_w

    @staticmethod
    def _month_last_day(d: date) -> date:
        if d.month == 12:
            return date(d.year, 12, 31)
        return date(d.year, d.month + 1, 1) - timedelta(days=1)

    def _month_label_span_px(self, start_i: int) -> int:
        """Width for a month title from day index ``start_i`` through month end (in view)."""
        d0 = self._view_start + timedelta(days=start_i)
        clip_end = min(self._month_last_day(d0), self._view_end)
        x0 = int(start_i * self._day_w)
        x1 = int(self._date_to_x_end(clip_end))
        return max(int(self._day_w), x1 - x0)

    def _project_range(self) -> tuple[date | None, date | None]:
        ps_raw = resolve_schedule_project_start(self._schedule, self._project_root)
        ps = self._parse(ps_raw or "")
        pe = self._parse(self._schedule.project_end or "")
        return ps, pe

    def _deadline_date(self) -> date | None:
        _ps, pe = self._project_range()
        return pe

    def _deadline_x(self) -> int | None:
        """Right edge of the deadline day column (OUT marker sits at end of that cell)."""
        pe = self._deadline_date()
        if pe is None or pe < self._view_start or pe > self._view_end:
            return None
        return int(self._date_to_x_end(pe))

    def _show_weekday_in_day_header(self) -> bool:
        """Weekday row stays until per-day numbers give way to W1/W2 week bands."""
        return self._day_w > _DAY_W_WEEK_IN_MONTH_MAX

    def _day_header_text_inset(self, col_w: int) -> tuple[int, int]:
        pad = 3 if col_w >= 16 else 1
        return pad, max(1, col_w - 2 * pad)

    def _weekday_abbreviated_mode(self, col_w: int) -> bool:
        """If any weekday abbr would clip, abbreviate all columns to one letter."""
        _, text_w = self._day_header_text_inset(col_w)
        fm = QFontMetrics(monos_font("Inter", 9))
        widest = max(fm.horizontalAdvance(abbr) for abbr in _WEEKDAY_ABBR)
        return widest > text_w

    @staticmethod
    def _weekday_header_label(d: date, *, abbreviated: bool) -> str:
        full = _WEEKDAY_ABBR[d.weekday()]
        return full[0] if abbreviated else full

    def _paint_day_header_cell(
        self,
        p: QPainter,
        x: int,
        col_w: int,
        d: date,
        day_pen: QColor,
        *,
        abbrev_weekday: bool = False,
        day_font: QFont | None = None,
    ) -> None:
        inset_x, text_w = self._day_header_text_inset(col_w)
        tx = x + inset_x
        if self._show_weekday_in_day_header():
            p.setPen(QColor("#71717a"))
            p.setFont(monos_font("Inter", 9))
            p.drawText(
                QRect(tx, _HEADER_DAY_TOP, text_w, _HEADER_WEEKDAY_H),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                self._weekday_header_label(d, abbreviated=abbrev_weekday),
            )
            num_top = _HEADER_DAY_TOP + _HEADER_WEEKDAY_H + _HEADER_DAY_GAP
            num_h = _HEADER_DAY_NUM_H
        else:
            num_top = _HEADER_DAY_TOP
            num_h = _HEADER_DAY_H
        p.setPen(day_pen)
        p.setFont(day_font or monos_font("JetBrains Mono", 9))
        p.drawText(
            QRect(tx, num_top, text_w, num_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            str(d.day),
        )

    def _paint_header_column_line(
        self,
        p: QPainter,
        x: int,
        color: QColor,
        *,
        width: int = 2,
        dashed: bool = False,
    ) -> None:
        """Vertical guide in the header — never crosses the day-number row."""
        pen = QPen(color, width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(x, _HEADER_RANGE_BAND_H, x, _HEADER_MARKER_LINE_BOTTOM)

    def _paint_today_header_marker(self, p: QPainter) -> None:
        today = date.today()
        if not (self._view_start <= today <= self._view_end):
            return
        tx = int(self._date_to_x(today))
        blue = QColor(MONOS_COLORS.get("blue_400", "#60a5fa"))
        self._paint_header_column_line(p, tx, blue, width=2)

        if self._day_w <= _DAY_W_WEEK_IN_MONTH_MAX:
            return

        i = (today - self._view_start).days
        x = int(i * self._day_w)
        col_w = max(int(self._day_w), 1)
        underline_y = _HEADER_DAY_TOP + _HEADER_DAY_H - 3
        p.setPen(QPen(blue, 2))
        p.drawLine(x + 3, underline_y, x + col_w - 3, underline_y)
        self._paint_day_header_cell(
            p,
            x,
            col_w,
            today,
            QColor(MONOS_COLORS.get("blue_400", "#60a5fa")),
            abbrev_weekday=self._weekday_abbreviated_mode(col_w),
            day_font=monos_font("JetBrains Mono", 9, QFont.Weight.DemiBold),
        )

    def _paint_deadline_marker(self, p: QPainter, h: int, *, header: bool) -> None:
        x = self._deadline_x()
        if x is None:
            return
        marker_h = _HEADER_RANGE_BAND_H if header else h
        self._paint_range_edge_marker(
            p, x, marker_h, edge="out", color=_DEADLINE_HEADER, header=header
        )

    def _x_to_date(self, x: float) -> date:
        rel = max(0.0, x)
        day_index = int(rel / self._day_w)
        day_index = max(0, min(day_index, self._num_days() - 1))
        return self._view_start + timedelta(days=day_index)

    def _pointer_to_date(self, event: QMouseEvent) -> date:
        if self._is_label_pane() and self._gantt is not None:
            tl = self._gantt._timeline_pane
            pt = tl.mapFromGlobal(event.globalPosition().toPoint())
            return tl._x_to_date(pt.x())
        return self._x_to_date(event.position().x())

    def _row_at_y(self, y: float) -> int | None:
        if not self._is_body_pane():
            return None
        idx = int(y // _ROW_H)
        if idx < 0 or idx >= len(self._visible):
            return None
        return idx

    @staticmethod
    def _entity_thumb_rect(y: int) -> QRect:
        ty = y + (_ROW_H - _LABEL_THUMB) // 2
        return QRect(_CHEVRON_W + 2, ty, _LABEL_THUMB, _LABEL_THUMB)

    def _paint_entity_thumbnail(
        self,
        p: QPainter,
        y: int,
        *,
        entity_kind: str,
        entity_rel: str,
        entity_name: str,
    ) -> None:
        rect = self._entity_thumb_rect(y)
        gantt = self._gantt
        pix: QPixmap | None = None
        if gantt is not None:
            pix = gantt.pixmap_for_entity(entity_kind, entity_rel)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 4, 4)
        p.save()
        p.setClipPath(path)
        p.fillPath(path, QColor("#27272a"))
        if pix is not None and not pix.isNull():
            scaled = pix.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            dx = rect.x() + (rect.width() - scaled.width()) // 2
            dy = rect.y() + (rect.height() - scaled.height()) // 2
            p.drawPixmap(dx, dy, scaled)
        else:
            p.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
            p.setPen(QColor("#52525b"))
            letter = (entity_name or "?").strip()[:1].upper() or "?"
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)
        p.restore()
        p.setPen(QPen(QColor("#3f3f46"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 4, 4)

    def _chevron_rect(self, visible_index: int) -> QRect:
        y = self._body_row_y(visible_index)
        return QRect(4, y + (_ROW_H - 16) // 2, _CHEVRON_W, 16)

    def _bar_dates(self, entity_kind: str, entity_rel: str, department: str) -> tuple[date, date] | None:
        key = _row_key(entity_kind, entity_rel, department)
        if key in self._drag_dates:
            return self._drag_dates[key]
        bar = self._bars.get(key)
        if bar is None:
            return None
        return bar.start, bar.due

    def _bar_x_range(self, start: date, due: date) -> tuple[float, float]:
        x0 = (start - self._view_start).days * self._day_w + 2
        x1 = (due - self._view_start).days * self._day_w + self._day_w - 2
        return min(x0, x1), max(4.0, abs(x1 - x0))

    def _full_bar_rect(self, visible_index: int, start: date, due: date) -> QRectF | None:
        x0, width = self._bar_x_range(start, due)
        if x0 + width < 0 or x0 > self._content_width():
            return None
        y = self._body_row_y(visible_index) + (_ROW_H - _BAR_H) // 2
        return QRectF(x0, y, width, _BAR_H)

    def _collapsed_group_rect(
        self, visible_index: int, start: date, due: date
    ) -> QRectF | None:
        """Entity span hit area when row is folded (full row height, min→max dates)."""
        x0, width = self._bar_x_range(start, due)
        if x0 + width < 0 or x0 > self._content_width():
            return None
        y = self._body_row_y(visible_index) + 2
        return QRectF(x0, y, width, _ROW_H - 4)

    def _mini_bar_rect(
        self, visible_index: int, slot: int, slot_count: int, start: date, due: date
    ) -> QRectF | None:
        x0, width = self._bar_x_range(start, due)
        if x0 + width < 0 or x0 > self._content_width():
            return None
        row_y = self._body_row_y(visible_index)
        mini_h = min(_MINI_BAR_H, max(4, (_ROW_H - 4 - (slot_count - 1) * _MINI_BAR_GAP) // max(1, slot_count)))
        slot_y = row_y + 2 + slot * (mini_h + _MINI_BAR_GAP)
        return QRectF(x0, slot_y, width, mini_h)

    def _dept_rows_for_visible(self, visible_index: int) -> list[TimelineRow]:
        row = self._visible[visible_index]
        if row.mode == "dept_wave":
            return []
        if row.group is None:
            return []
        if row.mode in ("dept", "dept_lane") and row.dept is not None:
            return [row.dept]
        if row.mode == "collapsed":
            return list(row.group.departments)
        return []

    def _resolve_department_for_draw(self, visible_index: int, y: float) -> str | None:
        display = self._visible[visible_index]
        if display.mode == "scope_separator":
            return None
        if display.mode == "dept_wave" and display.wave is not None:
            dep = (display.wave.department or "").strip()
            if dep and display.wave.entity_keys:
                return dep
            return None
        if self._view_mode == VIEW_DEPT_WAVE:
            return None
        if display.group is None:
            return None
        if display.mode in ("header", "dept_lane_header"):
            return None
        if display.mode in ("dept", "dept_lane") and display.dept is not None:
            dep = (display.dept.department or "").strip()
            return dep or None
        dept_rows = list(display.group.departments)
        if not dept_rows:
            return None
        row_y = self._body_row_y(visible_index)
        n = len(dept_rows)
        mini_h = min(_MINI_BAR_H, max(4, (_ROW_H - 4 - (n - 1) * _MINI_BAR_GAP) // max(1, n)))
        rel_y = y - row_y
        slot = int((rel_y - 2) // (mini_h + _MINI_BAR_GAP))
        slot = max(0, min(slot, n - 1))
        dep = (dept_rows[slot].department or "").strip()
        return dep or None

    def _draw_preview_rect(self, state: _DrawState) -> QRectF | None:
        if state.is_wave_row:
            return self._full_bar_rect(state.visible_index, state.start, state.due)
        display = self._visible[state.visible_index]
        collapsed = display.mode == "collapsed"
        dept_rows = self._dept_rows_for_visible(state.visible_index)
        slot = 0
        for i, row in enumerate(dept_rows):
            if (row.department or "").strip() == state.department:
                slot = i
                break
        if collapsed:
            return self._mini_bar_rect(
                state.visible_index, slot, len(dept_rows), state.start, state.due
            )
        return self._full_bar_rect(state.visible_index, state.start, state.due)

    def _hit_test(self, pos: QPoint) -> _BarHit | None:
        if self._view_mode == VIEW_DEPT_WAVE:
            visible_index = self._row_at_y(pos.y())
            if visible_index is None:
                return None
            display = self._visible[visible_index]
            if display.mode != "dept_wave" or display.wave is None:
                return None
            dep = (display.wave.department or "").strip()
            if not dep or not self._wave_department_has_bars(dep):
                return None
            ws, wd = self._wave_row_dates(display.wave)
            rect = self._full_bar_rect(visible_index, ws, wd)
            if rect is None or not rect.contains(QPointF(pos)):
                return None
            if abs(pos.x() - rect.left()) <= _EDGE_GRAB:
                return _BarHit(visible_index, dep, "resize_start", is_wave_row=True)
            if abs(pos.x() - rect.right()) <= _EDGE_GRAB:
                return _BarHit(visible_index, dep, "resize_end", is_wave_row=True)
            return _BarHit(visible_index, dep, "move", is_wave_row=True)
        visible_index = self._row_at_y(pos.y())
        if visible_index is None:
            return None
        display = self._visible[visible_index]
        if display.mode == "scope_separator":
            return None
        if display.mode == "collapsed" and display.group is not None:
            dept_rows = list(display.group.departments)
            span = self._entity_span_dates(
                display.group.entity_kind, display.group.entity_rel, dept_rows
            )
            if span[0] is not None and span[1] is not None:
                rect = self._collapsed_group_rect(visible_index, span[0], span[1])
                if rect is not None and rect.contains(QPointF(pos)):
                    if abs(pos.x() - rect.left()) <= _EDGE_GRAB:
                        return _BarHit(
                            visible_index,
                            _COLLAPSED_GROUP_HOVER,
                            "resize_start",
                            is_collapsed_group=True,
                        )
                    if abs(pos.x() - rect.right()) <= _EDGE_GRAB:
                        return _BarHit(
                            visible_index,
                            _COLLAPSED_GROUP_HOVER,
                            "resize_end",
                            is_collapsed_group=True,
                        )
                    return _BarHit(
                        visible_index,
                        _COLLAPSED_GROUP_HOVER,
                        "move",
                        is_collapsed_group=True,
                    )
            return None
        dept_rows = self._dept_rows_for_visible(visible_index)
        for slot, dept_row in enumerate(dept_rows):
            dep = (dept_row.department or "").strip()
            if not dep:
                continue
            dates = self._bar_dates(dept_row.entity_kind, dept_row.entity_rel, dep)
            if dates is None:
                continue
            rect = self._full_bar_rect(visible_index, dates[0], dates[1])
            if rect is None or not rect.contains(QPointF(pos)):
                continue
            if abs(pos.x() - rect.left()) <= _EDGE_GRAB:
                return _BarHit(visible_index, dep, "resize_start")
            if abs(pos.x() - rect.right()) <= _EDGE_GRAB:
                return _BarHit(visible_index, dep, "resize_end")
            return _BarHit(visible_index, dep, "move")
        return None

    def _group_subtitle(self, group: TimelineEntityGroup) -> str:
        target = target_for_entity(
            self._schedule, entity_kind=group.entity_kind, entity_rel=group.entity_rel
        )
        if target is not None:
            return f"Delivery {target.delivery}"
        labels = [d.department_label for d in group.departments if d.department_label]
        if labels:
            joined = " · ".join(labels[:3])
            if len(labels) > 3:
                joined += f" · +{len(labels) - 3}"
            return joined
        return f"{len(group.departments)} departments"

    def _grid_day_indices(self) -> list[int]:
        num = self._num_days()
        if num <= 0:
            return []
        dw = self._day_w
        if dw >= 7:
            return list(range(num))
        if dw <= _DAY_W_WEEK_IN_MONTH_MAX:
            return [
                i
                for i in range(num)
                if i == 0 or (self._view_start + timedelta(days=i)).day in (1, 8, 15, 22, 29)
            ]
        if dw >= 4:
            return [
                i
                for i in range(num)
                if i == 0 or (self._view_start + timedelta(days=i)).weekday() == 0
            ]
        indices = [0]
        for i in range(1, num):
            if (self._view_start + timedelta(days=i)).day == 1:
                indices.append(i)
        return indices

    @staticmethod
    def _last_day_of_month(y: int, m: int) -> date:
        if m == 12:
            return date(y, 12, 31)
        return date(y, m + 1, 1) - timedelta(days=1)

    def _iter_view_month_weeks(self) -> list[tuple[date, date, int]]:
        """Week bands inside each calendar month (W1 = days 1–7, W2 = 8–14, …)."""
        out: list[tuple[date, date, int]] = []
        cur = date(self._view_start.year, self._view_start.month, 1)
        while cur <= self._view_end:
            y, m = cur.year, cur.month
            last = self._last_day_of_month(y, m)
            day = 1
            wnum = 1
            while day <= last.day:
                ws = date(y, m, day)
                we = date(y, m, min(day + 6, last.day))
                if we >= self._view_start and ws <= self._view_end:
                    out.append((ws, we, wnum))
                day += 7
                wnum += 1
            cur = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        return out

    def _paint_time_header_week_in_month(self, p: QPainter, w: int) -> None:
        num_days = self._num_days()
        major = set(self._grid_day_indices())
        major_pen = QPen(QColor("#1e1e20"), 1)

        dw = self._day_w
        grid_top = _HEADER_RANGE_BAND_H
        for i in range(num_days):
            x = int(i * dw)
            if i in major:
                p.setPen(major_pen)
                p.drawLine(x, grid_top, x, _HEADER_H)

        month_pen = QColor("#71717a")
        week_pen = QColor("#52525b")
        month_font = monos_font("Inter", 9, QFont.Weight.DemiBold)
        week_font = monos_font("JetBrains Mono", 9)

        for ws, we, wnum in self._iter_view_month_weeks():
            clip_start = max(ws, self._view_start)
            clip_end = min(we, self._view_end)
            x0 = int(self._date_to_x_start(clip_start))
            x1 = int(self._date_to_x_end(clip_end))
            band_w = max(1, x1 - x0)

            if wnum == 1:
                p.setPen(month_pen)
                p.setFont(month_font)
                month_label = ws.strftime("%b %Y").upper()
                month_end = min(self._month_last_day(ws), self._view_end)
                month_w = max(band_w, int(self._date_to_x_end(month_end)) - x0)
                p.drawText(
                    QRect(x0 + 4, _HEADER_MONTH_TOP, max(1, month_w - 4), _HEADER_MONTH_H),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    month_label,
                )

            label = f"W{wnum}"
            min_w = 12 + len(label) * 5
            if band_w >= min_w:
                p.setPen(week_pen)
                p.setFont(week_font)
                p.drawText(
                    QRect(x0, _HEADER_DAY_TOP, band_w, _HEADER_DAY_H),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )

    def _paint_time_header_labels(self, p: QPainter, w: int) -> None:
        if self._day_w <= _DAY_W_WEEK_IN_MONTH_MAX:
            self._paint_time_header_week_in_month(p, w)
            return
        num_days = self._num_days()
        dw = self._day_w
        major = set(self._grid_day_indices())
        _ps, pe = self._project_range()

        minor_pen = QPen(QColor("#18181b"), 1)
        major_pen = QPen(QColor("#1e1e20"), 1)

        grid_top = _HEADER_RANGE_BAND_H
        for i in range(num_days):
            d = self._view_start + timedelta(days=i)
            x = int(i * dw)
            is_major = i in major
            is_deadline = pe is not None and d == pe
            if is_major or (4 <= dw < 7):
                p.setPen(major_pen if is_major else minor_pen)
                p.drawLine(x, grid_top, x, _HEADER_H)

        col_w_base = max(int(dw), 1)
        abbrev_weekday = self._weekday_abbreviated_mode(col_w_base)

        for i in sorted(major):
            d = self._view_start + timedelta(days=i)
            x = int(i * dw)
            col_w = col_w_base
            is_deadline = pe is not None and d == pe
            day_pen = _DEADLINE_HEADER if is_deadline else QColor("#52525b")
            month_pen = _DEADLINE_HEADER if is_deadline else QColor("#71717a")

            if dw >= 18:
                if d.day == 1 or i == 0:
                    month_w = self._month_label_span_px(i)
                    p.setPen(month_pen)
                    p.setFont(monos_font("Inter", 9, QFont.Weight.DemiBold))
                    p.drawText(
                        QRect(x + 4, _HEADER_MONTH_TOP, max(1, month_w - 4), _HEADER_MONTH_H),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        d.strftime("%b %Y"),
                    )
                self._paint_day_header_cell(
                    p, x, col_w, d, day_pen, abbrev_weekday=abbrev_weekday
                )
            elif dw >= 9:
                if d.day == 1 or i == 0:
                    month_w = self._month_label_span_px(i)
                    p.setPen(month_pen)
                    p.setFont(monos_font("Inter", 9, QFont.Weight.DemiBold))
                    p.drawText(
                        QRect(x + 4, _HEADER_MONTH_TOP, max(1, month_w - 4), _HEADER_MONTH_H),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        d.strftime("%b"),
                    )
                if col_w >= 11 or d.weekday() < 5:
                    self._paint_day_header_cell(
                        p, x, col_w, d, day_pen, abbrev_weekday=abbrev_weekday
                    )
            elif dw >= 4:
                month_w = self._month_label_span_px(i) if i == 0 else col_w
                p.setPen(month_pen)
                p.setFont(monos_font("Inter", 9, QFont.Weight.DemiBold))
                label = d.strftime("%b %d") if i == 0 else d.strftime("%d %b")
                p.drawText(
                    QRect(
                        x + (4 if i == 0 else 0),
                        _HEADER_MONTH_TOP,
                        max(1, (month_w - 4) if i == 0 else col_w),
                        _HEADER_MONTH_H + _HEADER_DAY_H,
                    ),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
            else:
                month_w = self._month_label_span_px(i)
                p.setPen(month_pen)
                p.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
                p.drawText(
                    QRect(x + 4, _HEADER_MONTH_TOP, max(1, month_w - 4), _HEADER_MONTH_H + _HEADER_DAY_H),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    d.strftime("%b %Y"),
                )

    def _paint_outside_production_range(self, p: QPainter, w: int, h: int) -> None:
        """Dim days before project start and after deadline (still visible in the scroll range)."""
        ps, pe = self._project_range()
        if ps is not None and ps > self._view_start:
            x1 = int(self._date_to_x_start(ps))
            if x1 > 0:
                p.fillRect(0, 0, x1, h, _OUTSIDE_RANGE_FILL)
        if pe is not None and pe < self._view_end:
            x0 = int(self._date_to_x_end(pe))
            if x0 < w:
                p.fillRect(x0, 0, w - x0, h, _OUTSIDE_RANGE_FILL)

    def _paint_filler_rows(self, p: QPainter, w: int, h: int, *, inner_w: int | None = None) -> None:
        """Horizontal row lines below real items so empty viewport area still looks like a grid."""
        data_h = len(self._visible) * _ROW_H
        if h <= data_h:
            return
        line_w = inner_w if inner_w is not None else w
        y = data_h
        row_idx = len(self._visible)
        while y < h:
            if row_idx % 2 == 1:
                p.fillRect(0, y, line_w, min(_ROW_H, h - y), QColor(255, 255, 255, 3))
            p.setPen(QPen(QColor("#1e1e20"), 1))
            p.drawLine(0, y + _ROW_H, line_w, y + _ROW_H)
            y += _ROW_H
            row_idx += 1

    def _paint_timeline_body_grid(self, p: QPainter, w: int, h: int) -> None:
        num_days = self._num_days()
        dw = self._day_w
        major = set(self._grid_day_indices())
        _ps, pe = self._project_range()

        if dw >= 7:
            for i in range(num_days):
                d = self._view_start + timedelta(days=i)
                if d.weekday() >= 5:
                    x = int(i * dw)
                    p.fillRect(
                        x + 1,
                        1,
                        max(1, int(dw) - 1),
                        h - 1,
                        QColor(255, 255, 255, 4),
                    )

        minor_pen = QPen(QColor("#18181b"), 1)
        major_pen = QPen(QColor("#1e1e20"), 1)

        for i in range(num_days):
            d = self._view_start + timedelta(days=i)
            x = int(i * dw)
            is_major = i in major
            if is_major or (4 <= dw < 7):
                p.setPen(major_pen if is_major else minor_pen)
                p.drawLine(x, 0, x, h)
            elif 4 <= dw < 7 and i not in major:
                p.setPen(minor_pen)
                p.drawLine(x, 0, x, h)

    def _sync_hover_row(self, row: int | None) -> None:
        if self._partner is not None and self._partner._hover_row != row:
            self._partner._hover_row = row
            self._partner.update()

    def _sync_highlight(self, highlight: _ScheduleHighlight | None) -> None:
        self._highlight = highlight
        self._highlight_entities = None
        if self._partner is not None:
            self._partner._highlight = highlight
            self._partner._highlight_entities = None
            self._partner.update()
        self.update()

    def _sync_highlight_entities(self, keys: frozenset[tuple[str, str]] | None) -> None:
        self._highlight_entities = keys
        self._highlight = None
        if self._partner is not None:
            self._partner._highlight_entities = keys
            self._partner._highlight = None
            self._partner.update()
        self.update()

    def _deselect_entity_row(self) -> None:
        if self._gantt is None:
            return
        self._gantt.clear_entity_highlight()
        self.entity_row_cleared.emit()

    def _click_should_clear_selection(self, row: int | None, pos: QPoint, *, ctrl: bool) -> bool:
        if ctrl:
            return True
        if row is None:
            return True
        if row < 0 or row >= len(self._visible):
            return True
        mode = self._visible[row].mode
        if mode in ("scope_separator", "dept_lane_header"):
            return True
        if not self._is_label_pane() and self._tool == TOOL_SELECT and self._hit_test(pos) is None:
            return True
        return False

    @staticmethod
    def _norm_entity_key(entity_kind: str, entity_rel: str) -> tuple[str, str]:
        return ((entity_kind or "").strip().lower(), (entity_rel or "").replace("\\", "/").strip())

    def _entity_key_from_display(self, display: _DisplayRow) -> tuple[str, str] | None:
        if display.dept is not None:
            return self._norm_entity_key(display.dept.entity_kind, display.dept.entity_rel)
        if display.group is not None:
            return self._norm_entity_key(display.group.entity_kind, display.group.entity_rel)
        return None

    def _row_matches_highlight(self, display: _DisplayRow) -> bool:
        if display.mode in ("scope_separator", "dept_lane_header"):
            return False
        multi = self._highlight_entities
        if multi:
            key = self._entity_key_from_display(display)
            return key is not None and key in multi
        h = self._highlight
        if h is None:
            return False
        kind, rel, dep_filter = h
        group = display.group
        if group is not None:
            if self._norm_entity_key(group.entity_kind, group.entity_rel) == (kind, rel):
                if dep_filter is None:
                    return display.mode in ("collapsed", "header", "dept", "dept_lane")
                if display.mode in ("collapsed", "header"):
                    return True
        dept_row = display.dept
        if dept_row is not None:
            if self._norm_entity_key(dept_row.entity_kind, dept_row.entity_rel) != (kind, rel):
                return False
            row_dep = (dept_row.department or "").strip()
            return dep_filter is None or row_dep == dep_filter
        return False

    def _paint_row_highlight(self, p: QPainter, y: int, row_w: int) -> None:
        p.fillRect(0, y, row_w, _ROW_H, _HIGHLIGHT_FILL)
        p.fillRect(0, y, 3, _ROW_H, _HIGHLIGHT_EDGE)

    @staticmethod
    def _paint_range_edge_marker(
        p: QPainter,
        x: int,
        h: int,
        *,
        edge: str,
        color: QColor,
        header: bool,
    ) -> None:
        p.setPen(QPen(color, 2))
        p.drawLine(x, 0, x, h)
        cap = 10 if header else 6
        y0 = 2
        if edge == "in":
            p.drawLine(x, y0, x + cap, y0)
            p.drawLine(x, y0, x, y0 + cap)
        else:
            p.drawLine(x, y0, x - cap, y0)
            p.drawLine(x, y0, x, y0 + cap)

    def _paint_project_range_header(self, p: QPainter, w: int, h: int) -> None:
        del w, h  # band height is fixed; full header width comes from paint rect
        band = _HEADER_RANGE_BAND_H
        line_y = band - 4
        ps, pe = self._project_range()
        if ps is not None and self._view_start <= ps <= self._view_end:
            x = int(self._date_to_x_start(ps))
            self._paint_range_edge_marker(
                p, x, band, edge="in", color=QColor(_RANGE_IN_HEX), header=True
            )
        if ps is not None and pe is not None and ps <= pe:
            x0 = int(self._date_to_x_start(ps))
            x1 = int(self._date_to_x_end(pe))
            if x1 > x0:
                p.setPen(QPen(QColor(_RANGE_IN_HEX), 2))
                p.drawLine(x0, line_y, x1, line_y)
        p.setPen(QPen(QColor("#27272a"), 1))
        p.drawLine(0, band, self._content_width(), band)

    def _paint_project_range_body(self, p: QPainter, w: int, h: int) -> None:
        ps, pe = self._project_range()
        if ps is not None and pe is not None and ps <= pe:
            if pe >= self._view_start and ps <= self._view_end:
                x0 = int(self._date_to_x_start(max(ps, self._view_start)))
                x1 = int(self._date_to_x_end(min(pe, self._view_end)))
                if x1 > x0:
                    p.fillRect(x0, 0, x1 - x0, h, _RANGE_FILL)
        if ps is not None and self._view_start <= ps <= self._view_end:
            x = int(self._date_to_x_start(ps))
            self._paint_range_edge_marker(
                p, x, h, edge="in", color=QColor(_RANGE_IN_HEX), header=False
            )
        if pe is not None and self._view_start <= pe <= self._view_end:
            x = self._deadline_x()
            if x is not None:
                self._paint_range_edge_marker(
                    p, x, h, edge="out", color=_DEADLINE_HEADER, header=False
                )

    def _entity_span_dates(
        self, entity_kind: str, entity_rel: str, departments: list
    ) -> tuple[date | None, date | None]:
        starts: list[date] = []
        ends: list[date] = []
        for dept_row in departments:
            dep = (getattr(dept_row, "department", None) or "").strip()
            if not dep:
                continue
            dates = self._bar_dates(entity_kind, entity_rel, dep)
            if dates is None:
                continue
            starts.append(dates[0])
            ends.append(dates[1])
        if not starts:
            return None, None
        return min(starts), max(ends)

    def _update_collapsed_group_drag(self, delta: int) -> None:
        if (
            self._drag is None
            or not self._drag.is_collapsed_group
            or self._drag_orig_start is None
            or self._drag_orig_due is None
        ):
            return
        g0 = self._drag_orig_start
        g1 = self._drag_orig_due
        if self._drag.mode == "move":
            for key, (os, od) in self._drag_collapsed_orig.items():
                duration = (od - os).days
                ns = os + timedelta(days=delta)
                nd = ns + timedelta(days=duration)
                if ns > nd:
                    ns, nd = nd, ns
                self._drag_dates[key] = (ns, nd)
            return

        # Stretch the block: anchor the opposite edge, scale each bar within the span.
        old_len = max(1, (g1 - g0).days)
        if self._drag.mode == "resize_start":
            new_g0 = g0 + timedelta(days=delta)
            if new_g0 > g1:
                new_g0 = g1
            anchor_start = new_g0
            anchor_end = g1
        else:
            new_g1 = g1 + timedelta(days=delta)
            if new_g1 < g0:
                new_g1 = g0
            anchor_start = g0
            anchor_end = new_g1
        new_len = max(1, (anchor_end - anchor_start).days)
        for key, (os, od) in self._drag_collapsed_orig.items():
            rel_s = (os - g0).days / old_len
            rel_d = (od - g0).days / old_len
            ns = anchor_start + timedelta(days=round(rel_s * new_len))
            nd = anchor_start + timedelta(days=round(rel_d * new_len))
            if nd < ns:
                nd = ns
            self._drag_dates[key] = (ns, nd)

    def _paint_row_span_markers(
        self,
        p: QPainter,
        y: int,
        start: date,
        due: date,
    ) -> None:
        if due < self._view_start or start > self._view_end:
            return
        x0 = int(self._date_to_x_start(max(start, self._view_start)))
        x1 = int(self._date_to_x_end(min(due, self._view_end)))
        if x1 - x0 < 4:
            return
        color = QColor("#71717a")
        p.setPen(QPen(color, 1))
        top = y + 3
        bot = y + _ROW_H - 3
        p.drawLine(x0, top, x1, top)
        p.drawLine(x0, bot, x1, bot)
        p.drawLine(x0, top, x0, bot)
        p.drawLine(x1, top, x1, bot)
        tick = 5
        p.drawLine(x0, top, x0 + tick, top + tick)
        p.drawLine(x1, top, x1 - tick, top + tick)
        p.drawLine(x0, bot, x0 + tick, bot - tick)
        p.drawLine(x1, bot, x1 - tick, bot - tick)

    def _paint_corner_pane(self, p: QPainter, w: int, h: int) -> None:
        p.fillRect(self.rect(), QColor("#141416"))
        p.fillRect(0, 0, w, h, QColor("#0d0d0f"))
        p.setPen(QColor("#52525b"))
        p.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        p.drawText(QRect(8, 0, w - 12, h), Qt.AlignLeft | Qt.AlignVCenter, "ITEM")
        p.setPen(QPen(QColor("#3f3f46"), 1))
        p.drawLine(w - 1, 0, w - 1, h)
        p.drawLine(0, h - 1, w, h - 1)

    def _paint_milestone_header_markers(
        self, p: QPainter, *, header_grid_top: int, h: int
    ) -> None:
        """Purple milestone guides + labels above month/day header rows."""
        del header_grid_top, h
        by_date: dict[date, list] = defaultdict(list)
        for m in self._schedule.milestones:
            md = self._parse(m.date)
            if md is None or md < self._view_start or md > self._view_end:
                continue
            by_date[md].append(m)

        label_font = monos_font("Inter", 8, QFont.Weight.DemiBold)
        label_fm = QFontMetrics(label_font)
        max_label_bottom = _HEADER_MONTH_TOP - 1

        for md in sorted(by_date.keys()):
            items = by_date[md]
            mx = int(self._date_to_x(md))
            self._paint_header_column_line(
                p, mx, QColor("#a855f7"), width=1, dashed=True
            )

            if self._day_w < 8:
                continue

            parts = [(m.label or "").strip() for m in items if (m.label or "").strip()]
            if not parts:
                continue
            combined = " · ".join(parts)
            max_w = max(56, min(int(self._day_w * 2.5), 140))
            text = label_fm.elidedText(combined, Qt.TextElideMode.ElideRight, max_w)
            if not text:
                continue

            tx = mx + 5
            tw = label_fm.horizontalAdvance(text)
            ty = _HEADER_MILESTONE_TOP
            if ty + _HEADER_MILESTONE_H > max_label_bottom:
                continue

            pill = QRect(tx, ty, tw + 6, _HEADER_MILESTONE_H)
            p.fillRect(pill, QColor(24, 24, 27, 230))
            p.setPen(QColor("#c084fc"))
            p.setFont(label_font)
            p.drawText(
                QRect(tx + 3, ty, tw, _HEADER_MILESTONE_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

    def _paint_milestone_day_marks(self, p: QPainter) -> None:
        """Purple underline on day cells that have milestones (header only)."""
        if self._day_w <= _DAY_W_WEEK_IN_MONTH_MAX:
            return
        today = date.today()
        purple = QColor("#a855f7")
        underline_y = _HEADER_DAY_TOP + _HEADER_DAY_H - 3
        seen: set[date] = set()
        for m in self._schedule.milestones:
            md = self._parse(m.date)
            if md is None or md in seen or md < self._view_start or md > self._view_end:
                continue
            if md == today:
                continue
            seen.add(md)
            i = (md - self._view_start).days
            x = int(i * self._day_w)
            col_w = max(int(self._day_w), 1)
            p.setPen(QPen(purple, 2))
            p.drawLine(x + 3, underline_y, x + col_w - 3, underline_y)

    def _paint_header_pane(self, p: QPainter, w: int, h: int) -> None:
        p.fillRect(self.rect(), QColor("#0d0d0f"))
        self._paint_project_range_header(p, w, h)
        self._paint_deadline_marker(p, h, header=True)

        header_grid_top = _HEADER_RANGE_BAND_H
        self._paint_milestone_header_markers(p, header_grid_top=header_grid_top, h=h)
        self._paint_time_header_labels(p, w)
        self._paint_milestone_day_marks(p)
        self._paint_today_header_marker(p)

        p.setPen(QPen(QColor("#3f3f46"), 1))
        p.drawLine(0, h - 1, w, h - 1)

    def _paint_label_pane(self, p: QPainter, w: int, h: int) -> None:
        p.fillRect(self.rect(), QColor("#141416"))
        inner_w = max(1, w - 1)  # keep column right edge for continuous border

        fm = QFontMetrics(p.font())
        if not self._visible:
            self._paint_filler_rows(p, w, h, inner_w=inner_w)
            p.setPen(QPen(QColor("#3f3f46"), 1))
            p.drawLine(w - 1, 0, w - 1, h)
            return

        label_font = monos_font("Inter", 11, QFont.Weight.DemiBold)
        sub_font = monos_font("Inter", 10, QFont.Weight.Medium)
        for vi, display in enumerate(self._visible):
            y = self._body_row_y(vi)
            p.setPen(QPen(QColor("#1e1e20"), 1))
            p.drawLine(0, y + _ROW_H, inner_w, y + _ROW_H)
            if self._row_matches_highlight(display):
                self._paint_row_highlight(p, y, inner_w)
            elif vi == self._hover_row and display.mode != "scope_separator":
                p.fillRect(0, y, inner_w, _ROW_H, QColor(255, 255, 255, 10))

            if display.mode == "collapsed":
                assert display.group is not None
                self._paint_entity_label(
                    p, fm, label_font, sub_font, vi, display, y, inner_w, expanded=False
                )
            elif display.mode == "header":
                assert display.group is not None
                self._paint_entity_label(
                    p, fm, label_font, sub_font, vi, display, y, inner_w, expanded=True
                )
            elif display.mode == "scope_separator":
                self._paint_scope_separator(p, y, display.lane_label, inner_w, display.count)
            elif display.mode == "dept_wave":
                assert display.wave is not None
                self._paint_wave_label(
                    p, fm, label_font, sub_font, vi, display.wave, y, inner_w
                )
            elif display.mode == "dept_lane_header":
                p.fillRect(0, y, inner_w, _ROW_H, QColor(255, 255, 255, 4))
                dep_label = (display.lane_label or "").strip()
                p.setFont(sub_font)
                p.setPen(_DEPT_SUBROW_COLOR)
                sub_fm = QFontMetrics(sub_font)
                p.drawText(
                    QRect(8, y, inner_w - 16, _ROW_H),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    sub_fm.elidedText(dep_label, Qt.ElideRight, inner_w - 24),
                )
            elif display.mode == "dept":
                assert display.dept is not None
                dep_row = display.dept
                dep_label = (dep_row.department_label or dep_row.department or "—").strip()
                p.setFont(sub_font)
                p.setPen(_DEPT_SUBROW_COLOR)
                sub_fm = QFontMetrics(sub_font)
                p.drawText(
                    QRect(_DEPT_INDENT, y, inner_w - _DEPT_INDENT - 8, _ROW_H),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    sub_fm.elidedText(dep_label, Qt.ElideRight, inner_w - _DEPT_INDENT - 12),
                )
            elif display.mode == "dept_lane":
                assert display.dept is not None
                dep_row = display.dept
                self._paint_entity_thumbnail(
                    p,
                    y,
                    entity_kind=dep_row.entity_kind,
                    entity_rel=dep_row.entity_rel,
                    entity_name=dep_row.entity_name,
                )
                text_w = inner_w - _LABEL_TEXT_LEFT - 8
                p.setFont(label_font)
                p.setPen(QColor("#e4e4e7"))
                p.drawText(
                    QRect(_LABEL_TEXT_LEFT, y + _ROW_TITLE_Y, text_w, _ROW_TITLE_H),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    fm.elidedText(dep_row.entity_name or "—", Qt.ElideRight, text_w),
                )

        self._paint_filler_rows(p, w, h, inner_w=inner_w)
        p.setPen(QPen(QColor("#3f3f46"), 1))
        p.drawLine(w - 1, 0, w - 1, h)

    def _paint_timeline_body(self, p: QPainter, w: int, h: int) -> None:
        p.fillRect(self.rect(), QColor(MONOS_COLORS.get("content_bg", "#121214")))
        self._paint_timeline_body_grid(p, w, h)
        self._paint_outside_production_range(p, w, h)
        self._paint_project_range_body(p, w, h)
        self._paint_deadline_marker(p, h, header=False)

        today = date.today()
        if self._view_start <= today <= self._view_end:
            tx = int(self._date_to_x(today))
            p.setPen(QPen(QColor(MONOS_COLORS.get("blue_400", "#60a5fa")), 2))
            p.drawLine(tx, 0, tx, h)

        if not self._visible:
            self._paint_filler_rows(p, w, h)
            p.setPen(QColor("#71717a"))
            p.setFont(monos_font("Inter", 12))
            empty_msg = (
                "No department waves in view.\nEnable Shots or Assets, or adjust filters."
                if self._view_mode == VIEW_DEPT_WAVE
                else "No timeline rows.\nEnable Shots or Assets above, or scan the project."
            )
            p.drawText(
                QRect(16, 24, max(200, w - 32), 80),
                Qt.AlignLeft | Qt.AlignTop,
                empty_msg,
            )
            return

        for m in self._schedule.milestones:
            md = self._parse(m.date)
            if md is None or md < self._view_start or md > self._view_end:
                continue
            mx = int(self._date_to_x(md))
            p.setPen(QPen(QColor("#a855f7"), 1, Qt.PenStyle.DashLine))
            p.drawLine(mx, 0, mx, h)

        for vi, display in enumerate(self._visible):
            y = self._body_row_y(vi)
            p.setPen(QPen(QColor("#1e1e20"), 1))
            p.drawLine(0, y + _ROW_H, w, y + _ROW_H)
            if self._row_matches_highlight(display):
                self._paint_row_highlight(p, y, w)
            elif vi == self._hover_row and display.mode != "scope_separator":
                p.fillRect(0, y, w, _ROW_H, QColor(255, 255, 255, 10))

            if display.mode == "collapsed":
                assert display.group is not None
                dept_rows = list(display.group.departments)
                span = self._entity_span_dates(
                    display.group.entity_kind, display.group.entity_rel, dept_rows
                )
                if span[0] is not None and span[1] is not None:
                    self._paint_row_span_markers(p, y, span[0], span[1])
                for slot, dept_row in enumerate(dept_rows):
                    dep = (dept_row.department or "").strip()
                    if dep:
                        key = _row_key(
                            display.group.entity_kind,
                            display.group.entity_rel,
                            dep,
                        )
                        b = self._bars.get(key)
                        if b is not None and b.status == STATUS_EXCLUDED:
                            continue
                        self._paint_bar(
                            p,
                            vi,
                            display.group.entity_kind,
                            display.group.entity_rel,
                            dep,
                            collapsed=True,
                            slot=slot,
                            slot_count=len(dept_rows),
                        )
            elif display.mode == "scope_separator":
                p.fillRect(0, y, w, _ROW_H, QColor(18, 18, 20))
            elif display.mode == "dept_lane_header":
                p.fillRect(0, y, w, _ROW_H, QColor(255, 255, 255, 4))
            elif display.mode == "dept_wave":
                assert display.wave is not None
                self._paint_wave_bar(p, vi, display.wave, y)
            elif display.mode in ("dept", "dept_lane"):
                assert display.dept is not None
                dep_row = display.dept
                dep = (dep_row.department or "").strip()
                if dep:
                    self._paint_bar(
                        p,
                        vi,
                        dep_row.entity_kind,
                        dep_row.entity_rel,
                        dep,
                        collapsed=False,
                    )

        self._paint_filler_rows(p, w, h)

        if self._draw_state is not None and self._pane == _PANE_TIMELINE:
            preview = self._draw_preview_rect(self._draw_state)
            if preview is not None:
                fill = QColor(_DRAW_PREVIEW)
                fill.setAlpha(120)
                p.setPen(QPen(_DRAW_PREVIEW, 1, Qt.PenStyle.DashLine))
                p.setBrush(fill)
                p.drawRoundedRect(preview, 4, 4)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w = self.width()
        h = self.height()
        if self._pane == _PANE_CORNER:
            self._paint_corner_pane(p, w, h)
        elif self._pane == _PANE_HEADER:
            self._paint_header_pane(p, w, h)
        elif self._pane == _PANE_LABEL:
            self._paint_label_pane(p, w, h)
        else:
            self._paint_timeline_body(p, w, h)
        p.end()

    def _paint_scope_separator(
        self, p: QPainter, y: int, label: str, inner_w: int, count: int = 0
    ) -> None:
        """Dark band + section label (+ count) — ITEM column only (keeps right border)."""
        if not self._is_label_pane():
            return
        p.fillRect(0, y, inner_w, _ROW_H, QColor(18, 18, 20))
        sec_font = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        p.setFont(sec_font)
        p.setPen(QColor("#71717a"))
        text = (label or "").strip().upper()
        p.drawText(QRect(8, y, inner_w - 16, _ROW_H), Qt.AlignLeft | Qt.AlignVCenter, text)
        if count > 0:
            label_w = QFontMetrics(sec_font).horizontalAdvance(text)
            p.setPen(QColor("#52525b"))
            p.drawText(
                QRect(8 + label_w + 8, y, inner_w - 16 - label_w - 8, _ROW_H),
                Qt.AlignLeft | Qt.AlignVCenter,
                str(count),
            )

    def _paint_wave_label(
        self,
        p: QPainter,
        fm: QFontMetrics,
        label_font: QFont,
        sub_font: QFont,
        visible_index: int,
        wave: DeptWaveRollup,
        y: int,
        inner_w: int,
    ) -> None:
        p.fillRect(0, y, inner_w, _ROW_H, QColor(255, 255, 255, 4))
        p.setFont(label_font)
        p.setPen(QColor("#e4e4e7"))
        title = wave.department_label or wave.department
        p.drawText(
            QRect(8, y + _ROW_TITLE_Y, inner_w - 16, _ROW_TITLE_H),
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(title, Qt.ElideRight, inner_w - 20),
        )
        n = wave.entity_count
        parts = [f"{n} item{'s' if n != 1 else ''}"]
        parts.append(f"{wave.duration_days}d")
        if wave.overdue_count:
            parts.append(f"{wave.overdue_count} overdue")
        p.setFont(sub_font)
        p.setPen(QColor("#71717a"))
        p.drawText(
            QRect(8, y + _ROW_SUB_Y, inner_w - 16, _ROW_SUB_H),
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(" · ".join(parts), Qt.ElideRight, inner_w - 20),
        )

    def _paint_wave_bar(
        self,
        p: QPainter,
        visible_index: int,
        wave: DeptWaveRollup,
        y: int,
    ) -> None:
        dep = (wave.department or "").strip()
        if dep and not self._wave_department_has_bars(dep):
            return
        ws, wd = self._wave_row_dates(wave)
        rect = self._full_bar_rect(visible_index, ws, wd)
        if rect is None:
            return
        if wave.overdue:
            fill = QColor(_OVERDUE_HEX)
        elif wave.done_count == wave.entity_count:
            fill = QColor("#10b981")
        elif wave.in_progress_count > 0:
            fill = QColor("#f59e0b")
        else:
            base = QColor(wave.color_hex)
            base.setAlpha(170)
            fill = base
        if self._hover_row == visible_index:
            fill = fill.lighter(118)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 4, 4)
        label = self._bar_label_text(start=ws, due=wd, wave=wave)
        if label:
            if wave.overdue:
                wave_status = STATUS_WAITING
            elif wave.done_count == wave.entity_count:
                wave_status = STATUS_DONE
            elif wave.in_progress_count > 0:
                wave_status = STATUS_PROGRESS
            else:
                wave_status = STATUS_WAITING
            self._paint_bar_label_text(
                p,
                rect,
                label,
                status=wave_status,
                overdue=wave.overdue,
            )

    def _paint_entity_label(
        self,
        p: QPainter,
        fm: QFontMetrics,
        label_font: QFont,
        sub_font: QFont,
        visible_index: int,
        display: _DisplayRow,
        y: int,
        inner_w: int,
        *,
        expanded: bool,
    ) -> None:
        group = display.group
        assert group is not None
        chevron = "▾" if expanded else "▸"
        p.setFont(monos_font("Inter", 10))
        p.setPen(QColor("#71717a"))
        p.drawText(self._chevron_rect(visible_index), Qt.AlignHCenter | Qt.AlignVCenter, chevron)
        self._paint_entity_thumbnail(
            p,
            y,
            entity_kind=group.entity_kind,
            entity_rel=group.entity_rel,
            entity_name=group.entity_name,
        )

        text_w = max(1, inner_w - _LABEL_TEXT_LEFT - 8)
        p.setFont(label_font)
        p.setPen(QColor("#e4e4e7"))
        p.drawText(
            QRect(_LABEL_TEXT_LEFT, y + _ROW_TITLE_Y, text_w, _ROW_TITLE_H),
            Qt.AlignLeft | Qt.AlignVCenter,
            fm.elidedText(group.entity_name or "—", Qt.ElideRight, text_w),
        )
        p.setFont(sub_font)
        p.setPen(QColor("#71717a"))
        subtitle = self._group_subtitle(group) if not expanded else f"{len(group.departments)} departments"
        sub_fm = QFontMetrics(sub_font)
        p.drawText(
            QRect(_LABEL_TEXT_LEFT, y + _ROW_SUB_Y, text_w, _ROW_SUB_H),
            Qt.AlignLeft | Qt.AlignVCenter,
            sub_fm.elidedText(subtitle, Qt.ElideRight, text_w),
        )

    def _paint_bar(
        self,
        p: QPainter,
        visible_index: int,
        entity_kind: str,
        entity_rel: str,
        department: str,
        *,
        collapsed: bool,
        slot: int = 0,
        slot_count: int = 1,
    ) -> None:
        key = _row_key(entity_kind, entity_rel, department)
        bar = self._bars.get(key)
        dates = self._drag_dates.get(key)
        if bar is None and dates is None:
            return
        start, due = dates if dates is not None else (bar.start, bar.due)  # type: ignore[union-attr]
        if collapsed:
            rect = self._mini_bar_rect(visible_index, slot, slot_count, start, due)
        else:
            rect = self._full_bar_rect(visible_index, start, due)
        if rect is None:
            return

        status = bar.status if bar is not None else "waiting"
        overdue = bar.overdue if bar is not None else False
        is_override = bar is not None and bar.source == "override"

        if overdue:
            fill = QColor(_OVERDUE_HEX)
        elif status == STATUS_EXCLUDED:
            fill = QColor("#52525b")
            fill.setAlpha(110)
        elif status == STATUS_DONE:
            fill = QColor("#10b981")
        elif status == STATUS_PROGRESS:
            fill = QColor("#f59e0b")
        else:
            base = QColor(bar.color_hex if bar is not None else "#3f3f46")
            base.setAlpha(150)
            fill = base

        hover_group = (
            collapsed
            and self._hover_bar is not None
            and self._hover_bar[0] == visible_index
            and self._hover_bar[1] == _COLLAPSED_GROUP_HOVER
        )
        if self._hover_bar == (visible_index, department) or hover_group:
            fill = fill.lighter(118)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        radius = 2 if collapsed else 4
        p.drawRoundedRect(rect, radius, radius)

        if is_override and not collapsed:
            pen = QPen(QColor("#fafafa"), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        if not collapsed and bar is not None:
            label = self._bar_label_text(start=start, due=due, bar=bar)
            self._paint_bar_label_text(
                p, rect, label, status=status, overdue=overdue
            )

    @staticmethod
    def _parse(d: str) -> date | None:
        try:
            return date.fromisoformat(d[:10])
        except ValueError:
            return None

    def _toggle_expand(self, group_key: tuple[str, str]) -> None:
        expanded_now = group_key not in self._expanded
        self.expand_toggled.emit(group_key, expanded_now)

    def _format_bar_tooltip(self, bar: PlannedBar) -> str:
        src = {"auto": "Auto", "wave": "Wave", "override": "Pinned"}.get(bar.source, bar.source)
        st = {
            STATUS_DONE: "Done",
            STATUS_PROGRESS: "In progress",
            STATUS_WAITING: "Waiting",
            STATUS_EXCLUDED: "Skipped (N/A)",
        }.get(bar.status, bar.status)
        days = (bar.due - bar.start).days + 1
        lines = [
            bar.department_label or bar.department,
            f"{bar.start.isoformat()} → {bar.due.isoformat()} ({days}d)",
            f"{st} · {src}",
        ]
        if bar.overdue:
            lines.append("Overdue")
        from monostudio.core.user_identity import resolve_assignee_display

        assignee_name, _ = resolve_assignee_display(
            self._workspace_root,
            assignee_id=bar.assignee_id,
            assignee_name=bar.assignee,
            assignee_ids=bar.assignee_ids,
        )
        if assignee_name:
            lines.append(f"Assignee: {assignee_name}")
        if (bar.note or "").strip():
            lines.append(f"Note: {bar.note.strip()}")
        return "\n".join(lines)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._gantt is not None and self._gantt._forward_nav_mouse_move(event):
            return

        if self._is_label_pane():
            if self._gantt is not None and self._gantt.is_label_column_resizing():
                self._gantt.update_label_column_resize(int(event.globalPosition().x()))
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                return
            edge_x = self.width() - _LABEL_COL_EDGE_GRAB
            on_resize_edge = event.position().x() >= edge_x
            row = self._row_at_y(event.position().y())
            self._hover_row = row
            self._sync_hover_row(row)
            if on_resize_edge:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                self.setToolTip("Drag to resize item column")
                self.update()
                return
            if self._tool == TOOL_DRAW:
                if row is not None and self._resolve_department_for_draw(
                    row, event.position().y()
                ):
                    self.setCursor(Qt.CursorShape.CrossCursor)
                elif row is not None:
                    self.setCursor(Qt.CursorShape.ForbiddenCursor)
                else:
                    self.setCursor(self._tool_idle_cursor(self._tool))
            elif row is not None and self._visible[row].mode in (
                "collapsed",
                "header",
                "dept_wave",
            ):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(self._tool_idle_cursor(self._tool))
            self.update()
            return

        if self._draw_state is not None:
            current = self._pointer_to_date(event)
            anchor = self._draw_state.anchor
            ds = min(anchor, current)
            dd = max(anchor, current)
            self._set_draw_state(
                _DrawState(
                    visible_index=self._draw_state.visible_index,
                    department=self._draw_state.department,
                    anchor=anchor,
                    start=ds,
                    due=dd,
                    is_wave_row=self._draw_state.is_wave_row,
                )
            )
            return

        if self._drag is not None:
            if self._drag_orig_start is None or self._drag_orig_due is None:
                return
            delta = int(round((event.position().x() - self._drag_origin_x) / self._day_w))
            if self._drag.is_collapsed_group:
                self._update_collapsed_group_drag(delta)
                self.update()
                return
            ds = self._drag_orig_start
            dd = self._drag_orig_due
            duration = (dd - ds).days
            if self._drag.mode == "move":
                ds = ds + timedelta(days=delta)
                dd = ds + timedelta(days=duration)
            elif self._drag.mode == "resize_start":
                ds = ds + timedelta(days=delta)
                if ds > dd:
                    ds = dd
            elif self._drag.mode == "resize_end":
                dd = dd + timedelta(days=delta)
                if dd < ds:
                    dd = ds
            if self._drag.is_wave_row:
                dep = self._drag.department
                self._wave_drag_preview[dep] = (ds, dd)
                wave = self._wave_for_department(dep)
                for key in self._wave_bar_keys(wave):
                    bar = self._bars.get(key)
                    if bar is None:
                        continue
                    bar_duration = (bar.due - bar.start).days
                    if self._drag.mode == "move":
                        child_start = bar.start + timedelta(days=delta)
                        child_due = child_start + timedelta(days=bar_duration)
                    elif self._drag.mode == "resize_start":
                        child_start = ds
                        child_due = bar.due
                        if child_start > child_due:
                            child_start = child_due
                    else:
                        child_start = bar.start
                        child_due = dd
                        if child_due < child_start:
                            child_due = child_start
                    self._drag_dates[key] = (child_start, child_due)
            else:
                display = self._visible[self._drag.visible_index]
                if display.group is None:
                    return
                self._drag_dates[
                    _row_key(
                        display.group.entity_kind,
                        display.group.entity_rel,
                        self._drag.department,
                    )
                ] = (ds, dd)
            self.update()
            return

        row = self._row_at_y(event.position().y())
        self._hover_row = row
        self._sync_hover_row(row)
        hit = self._hit_test(event.position().toPoint())
        self._hover_bar = (hit.visible_index, hit.department) if hit else None
        if hit:
            self.setCursor(
                Qt.CursorShape.SizeHorCursor if hit.mode != "move" else Qt.CursorShape.OpenHandCursor
            )
            display = self._visible[hit.visible_index]
            if hit.is_collapsed_group and display.group is not None:
                span = self._entity_span_dates(
                    display.group.entity_kind,
                    display.group.entity_rel,
                    list(display.group.departments),
                )
                if span[0] is not None and span[1] is not None:
                    n = sum(
                        1
                        for d in display.group.departments
                        if (d.department or "").strip()
                        and self._bar_dates(
                            display.group.entity_kind,
                            display.group.entity_rel,
                            (d.department or "").strip(),
                        )
                    )
                    self.setToolTip(
                        f"{display.group.entity_name}: {span[0].isoformat()} → "
                        f"{span[1].isoformat()} ({n} departments)"
                    )
                else:
                    self.setToolTip(display.group.entity_name)
            elif display.group is not None and hit.department != _COLLAPSED_GROUP_HOVER:
                bar = self._bars.get(
                    _row_key(display.group.entity_kind, display.group.entity_rel, hit.department)
                )
                if bar is not None:
                    self.setToolTip(self._format_bar_tooltip(bar))
                else:
                    self.setToolTip("")
            else:
                self.setToolTip("")
        elif row is not None and self._visible[row].mode == "dept_wave":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            wave = self._visible[row].wave
            if wave is not None:
                self.setToolTip(
                    f"{wave.department_label}: {wave.start.isoformat()} → {wave.due.isoformat()} "
                    f"({wave.entity_count} items, {wave.duration_days} days)"
                )
            else:
                self.setToolTip("")
        elif self._tool == TOOL_DRAW and row is not None:
            display = self._visible[row]
            if display.mode == "dept_wave" and display.wave is not None:
                dep = (display.wave.department or "").strip()
                if dep and display.wave.entity_keys:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                    self.setToolTip("")
                else:
                    self.setCursor(Qt.CursorShape.ForbiddenCursor)
                    self.setToolTip("No shots/assets in this department for the current filters.")
            elif display.mode != "header":
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(self._tool_idle_cursor(self._tool))
        else:
            self.setCursor(self._tool_idle_cursor(self._tool))
            self.setToolTip("")
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._gantt is not None:
            self._gantt.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._gantt is not None and self._gantt._try_begin_nav_from_canvas(event):
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        row = self._row_at_y(pos.y())
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self._click_should_clear_selection(row, pos, ctrl=ctrl):
            self._deselect_entity_row()
            return

        if self._is_label_pane():
            if event.position().x() >= self.width() - _LABEL_COL_EDGE_GRAB:
                if self._gantt is not None:
                    self._gantt.begin_label_column_resize(int(event.globalPosition().x()))
                    self.grabMouse()
                    event.accept()
                    return
            if self._tool == TOOL_DRAW and row is not None and self._schedule_editable():
                dep = self._resolve_department_for_draw(row, pos.y())
                if dep:
                    display = self._visible[row]
                    anchor = self._pointer_to_date(event)
                    self._set_draw_state(
                        _DrawState(
                            visible_index=row,
                            department=dep,
                            anchor=anchor,
                            start=anchor,
                            due=anchor,
                            is_wave_row=display.mode == "dept_wave",
                        )
                    )
                    self.grabMouse()
                    self.setFocus()
                    return
            if row is not None:
                display = self._visible[row]
                group = display.group
                if group is not None and display.mode in ("collapsed", "header"):
                    if self._chevron_rect(row).contains(pos):
                        self._toggle_expand(group.key)
                        return
                if group is not None and display.mode in (
                    "collapsed",
                    "header",
                    "dept",
                    "dept_lane",
                ):
                    dep_h: str | None = None
                    if display.dept is not None:
                        dep_h = (display.dept.department or "").strip() or None
                    if self._gantt is not None:
                        self._gantt.set_entity_highlight(
                            group.entity_kind, group.entity_rel, dep_h
                        )
                    kind = group.entity_kind
                    rel = group.entity_rel
                    if display.mode == "dept_lane" and display.dept is not None:
                        kind = display.dept.entity_kind
                        rel = display.dept.entity_rel
                    self.entity_row_selected.emit(kind, rel)
            return

        if self._tool == TOOL_DRAW and row is not None and self._schedule_editable():
            dep = self._resolve_department_for_draw(row, pos.y())
            if dep:
                display = self._visible[row]
                anchor = self._pointer_to_date(event)
                self._set_draw_state(
                    _DrawState(
                        visible_index=row,
                        department=dep,
                        anchor=anchor,
                        start=anchor,
                        due=anchor,
                        is_wave_row=display.mode == "dept_wave",
                    )
                )
                self.grabMouse()
                self.setFocus()
            return

        if self._tool != TOOL_SELECT:
            return

        if not self._schedule_editable():
            return

        hit = self._hit_test(pos)
        if hit is None:
            return
        if hit.is_wave_row:
            display = self._visible[hit.visible_index]
            if display.wave is None:
                return
            dates = self._wave_row_dates(display.wave)
        elif hit.is_collapsed_group:
            display = self._visible[hit.visible_index]
            if display.group is None:
                return
            span = self._entity_span_dates(
                display.group.entity_kind,
                display.group.entity_rel,
                list(display.group.departments),
            )
            if span[0] is None or span[1] is None:
                return
            dates = span
            self._drag_collapsed_orig.clear()
            for dept_row in display.group.departments:
                dep = (dept_row.department or "").strip()
                if not dep:
                    continue
                bar_dates = self._bar_dates(
                    display.group.entity_kind, display.group.entity_rel, dep
                )
                if bar_dates is not None:
                    self._drag_collapsed_orig[
                        _row_key(display.group.entity_kind, display.group.entity_rel, dep)
                    ] = bar_dates
            if not self._drag_collapsed_orig:
                return
        else:
            display = self._visible[hit.visible_index]
            if display.group is None:
                return
            dates = self._bar_dates(
                display.group.entity_kind, display.group.entity_rel, hit.department
            )
            if dates is None:
                return
        self._drag = hit
        self._drag_origin_x = event.position().x()
        self._drag_orig_start, self._drag_orig_due = dates
        if hit.mode == "move":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.grabMouse()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._gantt is not None and self._gantt._forward_nav_mouse_release(event):
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._gantt is not None and self._gantt.is_label_column_resizing():
            self._gantt.end_label_column_resize()
            self._release_if_grabbed(self)
            event.accept()
            return

        if self._draw_state is not None:
            if not self._schedule_editable():
                self._set_draw_state(None)
                self._release_if_grabbed(self)
                return
            state = self._draw_state
            self._set_draw_state(None)
            self._release_if_grabbed(self)
            self._drag_dates.clear()
            self.setCursor(Qt.CursorShape.CrossCursor)
            if state.is_wave_row:
                display = self._visible[state.visible_index]
                wave_dep = (state.department or "").strip()
                if (
                    display.mode != "dept_wave"
                    or display.wave is None
                    or (display.wave.department or "").strip() != wave_dep
                ):
                    self.update()
                    return
                self._commit_wave_draw(wave_dep, state.start, state.due, wave=display.wave)
            else:
                display = self._visible[state.visible_index]
                self._commit_override(display, state.department, state.start, state.due)
            return

        if self._drag is None:
            return
        if not self._schedule_editable():
            self._drag = None
            self._drag_dates.clear()
            self._drag_collapsed_orig.clear()
            self._wave_drag_preview.clear()
            self._release_if_grabbed(self)
            self.update()
            return
        drag = self._drag
        self._drag = None
        self._release_if_grabbed(self)
        self.setCursor(self._tool_idle_cursor(self._tool))
        if drag.is_wave_row:
            dept = drag.department
            preview = self._wave_drag_preview.pop(dept, None)
            child_dates = {
                k: v
                for k, v in self._drag_dates.items()
                if (k[2] or "").strip() == dept
            }
            self._drag_dates.clear()
            if (
                preview is None
                or self._project_root is None
                or self._drag_orig_start is None
                or self._drag_orig_due is None
            ):
                self.update()
                return
            ds, dd = preview
            if ds == self._drag_orig_start and dd == self._drag_orig_due:
                self.update()
                return
            self._commit_wave_move(dept, child_dates, wave=self._wave_for_department(dept))
            return
        if drag.is_collapsed_group:
            display = self._visible[drag.visible_index]
            changed = {
                key: dates
                for key, dates in self._drag_dates.items()
                if key in self._drag_collapsed_orig
                and dates != self._drag_collapsed_orig[key]
            }
            self._drag_dates.clear()
            self._drag_collapsed_orig.clear()
            if not changed or self._project_root is None:
                self.update()
                return
            self._commit_collapsed_group_move(display, changed)
            return
        display = self._visible[drag.visible_index]
        if display.group is None:
            return
        key = _row_key(display.group.entity_kind, display.group.entity_rel, drag.department)
        new_dates = self._drag_dates.get(key)
        if (
            new_dates is None
            or self._project_root is None
            or self._drag_orig_start is None
            or self._drag_orig_due is None
        ):
            self._drag_dates.pop(key, None)
            self.update()
            return
        ds, dd = new_dates
        if ds == self._drag_orig_start and dd == self._drag_orig_due:
            self._drag_dates.pop(key, None)
            self.update()
            return
        self._commit_override(display, drag.department, ds, dd)

    def _commit_override(
        self, display: _DisplayRow, department: str, start: date, due: date
    ) -> None:
        if display.group is None:
            return
        key = _row_key(display.group.entity_kind, display.group.entity_rel, department)
        existing = self._bars.get(key)
        aid = existing.allocation_id if (existing and existing.allocation_id) else new_allocation_id()
        assignee_ids = existing.assignee_ids if existing else ()
        assignees = existing.assignees if existing else ()
        assignee_id = existing.assignee_id if existing else ""
        assignee = existing.assignee if existing else ""
        note = existing.note if existing else ""
        alloc = ScheduleAllocation(
            id=aid,
            entity_kind=display.group.entity_kind,
            entity_rel=display.group.entity_rel,
            department=department,
            start=start.isoformat(),
            due=due.isoformat(),
            assignee_ids=assignee_ids,
            assignees=assignees,
            assignee_id=assignee_id,
            assignee=assignee,
            note=note,
        )
        try:
            upsert_allocation_for_row(self._project_root, alloc)  # type: ignore[arg-type]
        except OSError:
            self._drag_dates.pop(key, None)
            self.update()
            return
        self.override_committed.emit()

    def _commit_collapsed_group_move(
        self,
        display: _DisplayRow,
        child_dates: dict[tuple[str, str, str], tuple[date, date]],
    ) -> None:
        root = self._project_root
        if root is None or display.group is None:
            return
        allocs: list[ScheduleAllocation] = []
        for key, (seg_start, seg_due) in child_dates.items():
            kind, rel, dep = key
            if kind != display.group.entity_kind or rel != display.group.entity_rel:
                continue
            existing = self._bars.get(key)
            if existing is None:
                continue
            if seg_start > seg_due:
                seg_start, seg_due = seg_due, seg_start
            aid = existing.allocation_id or new_allocation_id()
            if existing.source == "wave":
                try:
                    delete_wave_for_row(
                        root,
                        entity_kind=kind,
                        entity_rel=rel,
                        department=dep,
                    )
                except OSError:
                    return
            allocs.append(
                ScheduleAllocation(
                    id=aid,
                    entity_kind=kind,
                    entity_rel=rel,
                    department=dep,
                    start=seg_start.isoformat(),
                    due=seg_due.isoformat(),
                    assignee_ids=existing.assignee_ids,
                    assignees=existing.assignees,
                    assignee_id=existing.assignee_id,
                    assignee=existing.assignee,
                    note=existing.note,
                )
            )
        if not allocs:
            return
        try:
            bulk_upsert_allocations(root, allocs)
        except OSError:
            return
        self._drag_dates.clear()
        self.override_committed.emit()

    def _commit_wave_draw(
        self,
        department: str,
        start: date,
        due: date,
        *,
        wave: DeptWaveRollup | None = None,
    ) -> None:
        root = self._project_root
        if root is None:
            return
        dep = (department or "").strip()
        if not dep:
            return
        if start > due:
            start, due = due, start
        wave = wave or self._wave_for_department(dep)
        if wave is None:
            return
        targets = self._wave_draw_targets(wave)
        if not targets:
            return
        mode = self._wave_draw_apply_mode
        if mode == WAVE_DRAW_FIRST_ONLY:
            targets = targets[:1]
        spans: list[tuple[date, date]] = []
        if mode == WAVE_DRAW_DISTRIBUTE:
            spans = self._split_date_range_evenly(start, due, len(targets))
        allocs: list[ScheduleAllocation] = []
        for i, (kind, rel, dept_id) in enumerate(targets):
            if mode == WAVE_DRAW_DISTRIBUTE:
                seg_start, seg_due = spans[i]
            else:
                seg_start, seg_due = start, due
            existing = self._bars.get(_row_key(kind, rel, dept_id))
            aid = (
                existing.allocation_id
                if existing and existing.allocation_id
                else new_allocation_id()
            )
            assignee_ids = existing.assignee_ids if existing else ()
            assignees = existing.assignees if existing else ()
            assignee_id = existing.assignee_id if existing else ""
            assignee = existing.assignee if existing else ""
            note = existing.note if existing else ""
            allocs.append(
                ScheduleAllocation(
                    id=aid,
                    entity_kind=kind,
                    entity_rel=rel,
                    department=dept_id,
                    start=seg_start.isoformat(),
                    due=seg_due.isoformat(),
                    assignee_ids=assignee_ids,
                    assignees=assignees,
                    assignee_id=assignee_id,
                    assignee=assignee,
                    note=note,
                )
            )
        try:
            replace_entity_department_allocations(
                root,
                clear_rows=targets,
                allocations=allocs,
                suppress_auto_on_clear=False,
            )
        except OSError:
            return
        self._drag_dates.clear()
        self.override_committed.emit()

    def _commit_wave_move(
        self,
        department: str,
        child_dates: dict[tuple[str, str, str], tuple[date, date]],
        *,
        wave: DeptWaveRollup | None = None,
    ) -> None:
        root = self._project_root
        if root is None:
            return
        dep = (department or "").strip()
        if not dep:
            return
        wave = wave or self._wave_for_department(dep)
        allowed = set(self._wave_bar_keys(wave))
        allocs: list[ScheduleAllocation] = []
        for key, (seg_start, seg_due) in child_dates.items():
            if key not in allowed:
                continue
            kind, rel, row_dep = key
            existing = self._bars.get(key)
            if existing is None:
                continue
            if seg_start > seg_due:
                seg_start, seg_due = seg_due, seg_start
            aid = existing.allocation_id or new_allocation_id()
            if existing.source == "wave":
                try:
                    delete_wave_for_row(
                        root,
                        entity_kind=kind,
                        entity_rel=rel,
                        department=dep,
                    )
                except OSError:
                    return
            allocs.append(
                ScheduleAllocation(
                    id=aid,
                    entity_kind=kind,
                    entity_rel=rel,
                    department=dep,
                    start=seg_start.isoformat(),
                    due=seg_due.isoformat(),
                    assignee_ids=existing.assignee_ids,
                    assignees=existing.assignees,
                    assignee_id=existing.assignee_id,
                    assignee=existing.assignee,
                    note=existing.note,
                )
            )
        if not allocs:
            return
        try:
            bulk_upsert_allocations(root, allocs)
        except OSError:
            return
        self._drag_dates.clear()
        self.override_committed.emit()

    def _delete_wave_department_bar(self, department: str) -> None:
        root = self._project_root
        if root is None:
            return
        dep = (department or "").strip()
        wave = self._wave_for_department(dep)
        if wave is None:
            return
        targets = self._wave_draw_targets(wave)
        if not targets:
            return
        try:
            clear_entity_department_schedules(
                root, rows=targets, suppress_auto=True
            )
        except OSError:
            return
        self._wave_drag_preview.pop(dep, None)
        self._drag_dates.clear()
        self.override_committed.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._tool != TOOL_SELECT:
            return
        pos = event.position().toPoint()
        row = self._row_at_y(pos.y())
        if self._is_label_pane():
            if row is not None:
                display = self._visible[row]
                if display.mode == "dept_wave" and display.wave is not None:
                    self.wave_drilldown_requested.emit(display.wave.department)
                    return
                if display.mode == "collapsed":
                    group = display.group
                    if group is not None:
                        self._toggle_expand(group.key)
            return
        if row is not None:
            display = self._visible[row]
            if display.mode == "dept_wave" and display.wave is not None:
                self.wave_drilldown_requested.emit(display.wave.department)
                return
        if self._hit_test(pos) is not None:
            if row is not None:
                self.row_activated.emit(row)
            return
        if row is not None and self._visible[row].mode == "collapsed":
            group = self._visible[row].group
            if group is not None:
                self._toggle_expand(group.key)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape and self._draw_state is not None:
            self._set_draw_state(None)
            return
        super().keyPressEvent(event)

    def _exec_dept_wave_context_menu(self, row: int, global_pos) -> bool:
        if row < 0 or row >= len(self._visible):
            return False
        display = self._visible[row]
        if display.mode != "dept_wave" or display.wave is None:
            return False
        dep = (display.wave.department or "").strip()
        editable = self._schedule_editable()
        menu = QMenu(self)
        delete_act = menu.addAction("Delete wave bar…")
        delete_act.setEnabled(editable and bool(dep) and self._wave_department_has_bars(dep))
        drill = menu.addAction("Show shots in department…")
        chosen = menu.exec(global_pos)
        if chosen is delete_act and dep:
            self._delete_wave_department_bar(dep)
        elif chosen is drill and dep:
            self.wave_drilldown_requested.emit(dep)
        return True

    def _request_tools_menu(self, global_pos) -> None:
        """Fallback: right-click on empty/header area → page tools & planning menu."""
        if self._gantt is not None:
            self._gantt.tools_menu_requested.emit(global_pos)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return
        pos = event.pos()
        row = self._row_at_y(pos.y())
        if row is not None and 0 <= row < len(self._visible) and self._visible[row].mode == "dept_wave":
            if self._exec_dept_wave_context_menu(row, event.globalPos()):
                return

        # Entity label menu (Plan / Clear) — Select tool, label pane, on a real row.
        if self._tool == TOOL_SELECT and self._is_label_pane() and row is not None:
            display = self._visible[row]
            if display.group is not None and display.mode in (
                "collapsed",
                "header",
                "dept",
                "dept_lane",
            ):
                group = display.group
                from monostudio.core.project_schedule import (
                    entity_has_schedule,
                    read_project_schedule,
                )

                has_plan = False
                if self._project_root is not None:
                    sched = read_project_schedule(self._project_root)
                    has_plan = entity_has_schedule(
                        sched,
                        entity_kind=group.entity_kind,
                        entity_rel=group.entity_rel,
                    )
                menu = QMenu(self)
                plan_act = menu.addAction("Plan delivery…")
                clear_act = menu.addAction("Clear plan…")
                editable = self._schedule_editable()
                plan_act.setEnabled(editable)
                clear_act.setEnabled(editable and has_plan)
                chosen = menu.exec(event.globalPos())
                if chosen is plan_act:
                    self.entity_plan_requested.emit(group.entity_kind, group.entity_rel)
                elif chosen is clear_act and has_plan:
                    self.entity_clear_plan_requested.emit(group.entity_kind, group.entity_rel)
                return

        # Bar menu (Edit / Reset / Skip) — Select tool, timeline pane, on a bar.
        if self._tool == TOOL_SELECT and self._pane == _PANE_TIMELINE:
            hit = self._hit_test(pos)
            if hit is not None:
                display = self._visible[hit.visible_index]
                if display.mode not in ("header", "dept_lane_header") and display.group is not None:
                    bar = self._bars.get(
                        _row_key(
                            display.group.entity_kind, display.group.entity_rel, hit.department
                        )
                    )
                    menu = QMenu(self)
                    editable = self._schedule_editable()
                    edit_act = menu.addAction("Edit…")
                    reset_act = menu.addAction("Reset to auto")
                    reset_act.setEnabled(
                        editable and bar is not None and bar.source in ("override", "wave")
                    )
                    is_skipped = bar is not None and (
                        bar.status == STATUS_EXCLUDED or bar.status_id == SKIPPED_STATUS_ID
                    )
                    skip_act = menu.addAction(
                        "Unskip department" if is_skipped else "Skip department (N/A)…"
                    )
                    skip_act.setEnabled(editable and bar is not None)
                    edit_act.setEnabled(editable)
                    menu.addSeparator()
                    chosen = menu.exec(event.globalPos())
                    if chosen is edit_act:
                        self.row_activated.emit(hit.visible_index)
                    elif chosen is reset_act and bar is not None and self._project_root is not None:
                        self._reset_bar_to_auto(display, hit.department, bar)
                    elif chosen is skip_act and display.group is not None:
                        self.department_skip_toggle_requested.emit(
                            display.group.entity_kind,
                            display.group.entity_rel,
                            hit.department,
                            not is_skipped,
                        )
                    return

        # Empty timeline / header / draw-tool right-click → tools & planning menu.
        self._request_tools_menu(event.globalPos())

    def _reset_bar_to_auto(self, display: _DisplayRow, department: str, bar: PlannedBar) -> None:
        root = self._project_root
        if root is None or display.group is None:
            return
        if bar.allocation_id:
            try:
                delete_allocation(root, bar.allocation_id)
            except OSError:
                return
            try:
                clear_auto_bar_suppression_for_row(
                    root,
                    entity_kind=display.group.entity_kind,  # type: ignore[union-attr]
                    entity_rel=display.group.entity_rel,  # type: ignore[union-attr]
                    department=department,
                )
            except OSError:
                return
        elif bar.source == "wave":
            try:
                delete_wave_for_row(
                    root,
                    entity_kind=display.group.entity_kind,  # type: ignore[union-attr]
                    entity_rel=display.group.entity_rel,  # type: ignore[union-attr]
                    department=department,
                )
            except OSError:
                return
        else:
            return
        try:
            clear_auto_bar_suppression_for_row(
                root,
                entity_kind=display.group.entity_kind,  # type: ignore[union-attr]
                entity_rel=display.group.entity_rel,  # type: ignore[union-attr]
                department=department,
            )
        except OSError:
            return
        self.override_committed.emit()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover_row = None
        if not self._is_label_pane():
            self._hover_bar = None
        self._sync_hover_row(None)
        if self._gantt is not None:
            self._gantt._apply_tool_cursors()
        else:
            self.setCursor(self._tool_idle_cursor(self._tool))
        self.update()
        super().leaveEvent(event)


class _ScheduleLabelColResizeHandle(QWidget):
    """Drag handle between the item column and the timeline."""

    def __init__(self, gantt: ScheduleGanttWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gantt = gantt
        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_w = _LABEL_W_DEFAULT
        self.setObjectName("ScheduleLabelColResizeHandle")
        self.setFixedWidth(_LABEL_COL_RESIZE_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self.setToolTip("Drag to resize item column")
        self._hovered = False

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255, 10 if self._hovered or self._dragging else 4))
        x = self.width() // 2
        color = QColor("#71717a") if self._hovered or self._dragging else QColor("#52525b")
        p.setPen(QPen(color, 1))
        p.drawLine(x, 6, x, max(6, self.height() - 6))
        p.end()
        super().paintEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._gantt.begin_label_column_resize(int(event.globalPosition().x()))
            self._dragging = True
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._gantt.update_label_column_resize(int(event.globalPosition().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._gantt.end_label_column_resize()
            self.releaseMouse()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ScheduleGanttWidget(QWidget):
    schedule_changed = Signal()
    edit_allocation_requested = Signal(object)
    new_allocation_requested = Signal(object)
    wave_drilldown_requested = Signal(str)
    entity_plan_requested = Signal(str, str)
    entity_clear_plan_requested = Signal(str, str)
    entity_row_selected = Signal(str, str)
    entity_row_cleared = Signal()
    department_skip_toggle_requested = Signal(str, str, str, bool)
    search_filter_requested = Signal()  # corner gear icon → page view-options popup
    tools_menu_requested = Signal(object)  # right-click empty area → page tools/planning menu

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._label_w = _LABEL_W_DEFAULT
        self._col_resizing = False
        self._col_resize_start_x = 0
        self._col_resize_start_w = _LABEL_W_DEFAULT

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)

        self._corner_pane = self._build_corner_search()

        self._header_scroll = QScrollArea(self)
        self._header_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._header_scroll.setWidgetResizable(False)
        self._header_scroll.setFixedHeight(_HEADER_H)
        self._header_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._header_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._header_pane = _GanttCanvas(pane=_PANE_HEADER)
        self._header_pane._gantt = self
        self._header_scroll.setWidget(self._header_pane)

        self._header_col_resize = _ScheduleLabelColResizeHandle(self, self)
        self._header_col_resize.setFixedSize(_LABEL_COL_RESIZE_W, _HEADER_H)
        top_row.addWidget(self._corner_pane)
        top_row.addWidget(self._header_col_resize)
        top_row.addWidget(self._header_scroll, 1)
        root.addLayout(top_row)

        # One vertical scroll for ITEM + timeline rows (avoids dual-scroll drift).
        self._body_vscroll = QScrollArea(self)
        self._body_vscroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._body_vscroll.setWidgetResizable(False)
        self._body_vscroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._body_vscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body_vscroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._body_host = QWidget(self._body_vscroll)
        body_host_lay = QHBoxLayout(self._body_host)
        body_host_lay.setContentsMargins(0, 0, 0, 0)
        body_host_lay.setSpacing(0)
        body_host_lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._label_pane = _GanttCanvas(pane=_PANE_LABEL)
        self._label_col_resize = _ScheduleLabelColResizeHandle(self, self._body_host)
        self._timeline_pane = _GanttCanvas(pane=_PANE_TIMELINE)
        self._label_pane._partner = self._timeline_pane
        self._timeline_pane._partner = self._label_pane
        self._label_pane._gantt = self
        self._timeline_pane._gantt = self

        self._timeline_hscroll = QScrollArea(self._body_host)
        self._timeline_hscroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._timeline_hscroll.setWidgetResizable(False)
        self._timeline_hscroll.setViewportMargins(0, 0, 0, 0)
        self._timeline_hscroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Native H bar is hidden — rows are as tall as the full list, so the bar would sit
        # below the last row. Use a sticky footer bar synced to the timeline instead.
        self._timeline_hscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._timeline_hscroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._timeline_hscroll.setWidget(self._timeline_pane)

        _row_top = Qt.AlignmentFlag.AlignTop
        body_host_lay.addWidget(self._label_pane, 0, _row_top)
        body_host_lay.addWidget(self._label_col_resize, 0, _row_top)
        body_host_lay.addWidget(self._timeline_hscroll, 1, _row_top)
        self._body_vscroll.setWidget(self._body_host)
        root.addWidget(self._body_vscroll, 1)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(0)
        self._footer_hspacer = QWidget(self)
        self._footer_hspacer.setFixedWidth(self._label_left_width())
        self._footer_hspacer.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        footer_row.addWidget(self._footer_hspacer, 0)
        self._footer_hbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self._footer_hbar.setObjectName("ScheduleFooterHScroll")
        footer_row.addWidget(self._footer_hbar, 1)
        root.addLayout(footer_row)

        self._header_pane.installEventFilter(self)
        self._label_pane.installEventFilter(self)
        self._timeline_pane.installEventFilter(self)
        self._body_vscroll.viewport().installEventFilter(self)
        self._timeline_hscroll.viewport().installEventFilter(self)

        self._hscroll_sync = False
        hs_tl = self._timeline_hscroll.horizontalScrollBar()
        hs_hd = self._header_scroll.horizontalScrollBar()
        hs_tl.valueChanged.connect(lambda v: self._sync_hscroll_all("timeline", v))
        hs_hd.valueChanged.connect(lambda v: self._sync_hscroll_all("header", v))
        self._footer_hbar.valueChanged.connect(lambda v: self._sync_hscroll_all("footer", v))
        hs_tl.rangeChanged.connect(lambda _a, _b: self._sync_footer_hbar_range())

        self._nav_panning = False
        self._nav_pan_origin_global: QPoint | None = None
        self._nav_pan_scroll = (0, 0)
        self._nav_zooming = False
        self._nav_zoom_start_global_x = 0.0
        self._nav_zoom_start_day_w = _DAY_W
        self._nav_zoom_anchor_x = 0.0
        self._project_root: Path | None = None
        self._workspace_root: Path | None = None
        self._project_index: ProjectIndex | None = None
        self._include_shots = True
        self._include_assets = False
        self._groups: list[TimelineEntityGroup] = []
        self._expanded: set[tuple[str, str]] = set()
        self._bars: dict[tuple[str, str, str], PlannedBar] = {}
        self._schedule = ProjectSchedule()
        self._view_mode = VIEW_ENTITY
        self._dept_filter: str | None = None
        self._type_filter: str | None = None
        self._type_filter_aliases: set[str] = set()
        self._asset_type_by_rel: dict[str, str] = {}
        self._unscheduled_only = False
        self._overdue_only = False
        self._dept_order: list[str] = []
        self._dept_reg = None
        self._hidden_departments: set[str] = set()
        self._respect_inspector_hidden = True
        self._dept_scope = DEPT_SCOPE_LEAF
        self._wave_group_parent = False
        self._allowed_departments: frozenset[str] | None = None
        self._thumbnail_manager: ThumbnailManager | None = None
        self._entity_refs: dict[tuple[str, str], Asset | Shot] = {}
        self._schedule_editable = True

        for pane in (self._label_pane, self._timeline_pane):
            pane.row_activated.connect(self._on_row_activated)
            pane.override_committed.connect(self._on_override_committed)
            pane.expand_toggled.connect(self._on_expand_toggled)
            pane.wave_drilldown_requested.connect(self.wave_drilldown_requested.emit)
        self._label_pane.entity_plan_requested.connect(self.entity_plan_requested.emit)
        self._label_pane.entity_clear_plan_requested.connect(
            self.entity_clear_plan_requested.emit
        )
        self._label_pane.entity_row_selected.connect(self.entity_row_selected.emit)
        self._label_pane.entity_row_cleared.connect(self.entity_row_cleared.emit)
        self._timeline_pane.entity_row_cleared.connect(self.entity_row_cleared.emit)
        self._timeline_pane.department_skip_toggle_requested.connect(
            self.department_skip_toggle_requested.emit
        )
        self._apply_tool_cursors()

    @property
    def _canvas(self) -> _GanttCanvas:
        return self._timeline_pane

    def label_column_width(self) -> int:
        return self._label_w

    def _label_left_width(self) -> int:
        return self._label_w + _LABEL_COL_RESIZE_W

    def set_label_column_width(self, width: int) -> None:
        w = max(_LABEL_W_MIN, min(_LABEL_W_MAX, int(width)))
        if w == self._label_w:
            return
        self._label_w = w
        self._corner_pane.setFixedWidth(w)
        self._footer_hspacer.setFixedWidth(self._label_left_width())
        self._label_pane._update_minimum_size()
        self._ensure_body_pane_heights()
        self._label_pane.update()

    def is_label_column_resizing(self) -> bool:
        return self._col_resizing

    def begin_label_column_resize(self, global_x: int) -> None:
        self._col_resizing = True
        self._col_resize_start_x = int(global_x)
        self._col_resize_start_w = self._label_w

    def update_label_column_resize(self, global_x: int) -> None:
        if not self._col_resizing:
            return
        delta = int(global_x) - self._col_resize_start_x
        self.set_label_column_width(self._col_resize_start_w + delta)

    def end_label_column_resize(self) -> None:
        self._col_resizing = False

    def _set_body_vscroll(self, value: int) -> None:
        bar = self._body_vscroll.verticalScrollBar()
        v = max(bar.minimum(), min(int(value), bar.maximum()))
        if bar.value() != v:
            bar.setValue(v)

    def _body_vscroll_wheel(self, delta_y: int) -> None:
        if not delta_y:
            return
        bar = self._body_vscroll.verticalScrollBar()
        self._set_body_vscroll(bar.value() - delta_y)

    def _apply_wheel_hscroll(self, wheel) -> bool:
        """Trackpad / mouse horizontal wheel and Shift+vertical → timeline scroll."""
        delta = wheel.angleDelta()
        dx = int(delta.x())
        dy = int(delta.y())
        if wheel.modifiers() & Qt.KeyboardModifier.ShiftModifier and dy and abs(dy) >= abs(dx):
            dx, dy = dy, 0
        if not dx:
            return False
        bar = self._timeline_hscroll.horizontalScrollBar()
        bar.setValue(bar.value() - dx)
        return True

    def _body_visible_width(self) -> int:
        """Width of the body row visible inside the vertical scroll area."""
        vp = self._body_vscroll.viewport()
        w = vp.width() if vp is not None else 0
        left_w = self._label_left_width()
        if w < left_w + 32:
            w = max(w, self.width() - 8)
        return max(left_w + 32, w)

    def _timeline_viewport_width(self) -> int:
        return max(32, self._body_visible_width() - self._label_left_width())

    def _body_content_height(self) -> int:
        """At least as tall as the visible body area so empty rows can show grid cells."""
        data_h = max(_ROW_H, len(self._label_pane._visible) * _ROW_H)
        vp = self._body_vscroll.viewport()
        vp_h = vp.height() if vp is not None else 0
        if vp_h <= 0:
            return data_h
        return max(data_h, vp_h)

    def _reset_timeline_vertical_scroll(self) -> None:
        """Nested QScrollArea must never keep a vertical offset — body_vscroll owns Y."""
        vbar = self._timeline_hscroll.verticalScrollBar()
        if vbar.value() != 0:
            vbar.setValue(0)

    def _ensure_body_pane_heights(self) -> None:
        """Label + timeline: one vertical scroll; timeline pans horizontally inside a fixed viewport."""
        ch = self._body_content_height()
        tw = self._timeline_pane._content_width()
        host_w = self._body_visible_width()
        tl_vp_w = self._timeline_viewport_width()

        fixed = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._label_pane.setSizePolicy(fixed)
        self._timeline_hscroll.setSizePolicy(fixed)

        lw = self._label_w
        if self._label_pane.width() != lw or self._label_pane.height() != ch:
            self._label_pane.setFixedSize(lw, ch)

        row_h = self._label_pane.height()
        if (
            self._label_col_resize.width() != _LABEL_COL_RESIZE_W
            or self._label_col_resize.height() != row_h
        ):
            self._label_col_resize.setFixedSize(_LABEL_COL_RESIZE_W, row_h)
        if self._timeline_hscroll.width() != tl_vp_w or self._timeline_hscroll.height() != row_h:
            self._timeline_hscroll.setFixedSize(tl_vp_w, row_h)
        self._reset_timeline_vertical_scroll()

        # Canvas height must match the label column — never taller than the scroll viewport.
        if self._timeline_pane.width() != tw or self._timeline_pane.height() != row_h:
            self._timeline_pane.resize(tw, row_h)
        self._reset_timeline_vertical_scroll()

        # Host matches the visible viewport — NOT full timeline width (that kills H-scroll range).
        if self._body_host.width() != host_w or self._body_host.height() != row_h:
            self._body_host.setMinimumSize(host_w, row_h)
            self._body_host.resize(host_w, row_h)
        self._sync_footer_hbar_range()

    def _sync_footer_hbar_range(self) -> None:
        if not hasattr(self, "_footer_hbar"):
            return
        tl = self._timeline_hscroll.horizontalScrollBar()
        ft = self._footer_hbar
        ft.setRange(tl.minimum(), tl.maximum())
        ft.setPageStep(tl.pageStep())
        ft.setSingleStep(tl.singleStep())
        if ft.value() != tl.value():
            ft.blockSignals(True)
            try:
                ft.setValue(tl.value())
            finally:
                ft.blockSignals(False)

    def _sync_hscroll_all(self, source: str, value: int) -> None:
        if self._hscroll_sync:
            return
        self._hscroll_sync = True
        targets: dict[str, QScrollBar] = {
            "timeline": self._timeline_hscroll.horizontalScrollBar(),
            "header": self._header_scroll.horizontalScrollBar(),
            "footer": self._footer_hbar,
        }
        for name, bar in targets.items():
            if name != source and bar.value() != value:
                bar.setValue(value)
        self._hscroll_sync = False

    def _set_pane_data(self, **kwargs) -> None:
        self._header_pane.set_data(**kwargs)
        self._label_pane.set_data(**kwargs)
        self._timeline_pane.set_data(**kwargs)
        self._ensure_body_pane_heights()
        QTimer.singleShot(0, self._ensure_body_pane_heights)

    def set_workspace_root(self, path: Path | None) -> None:
        try:
            self._workspace_root = Path(path).resolve() if path else None
        except OSError:
            self._workspace_root = None
        for pane in (self._header_pane, self._label_pane, self._timeline_pane):
            pane._workspace_root = self._workspace_root

    def set_include_assets(self, enabled: bool) -> None:
        self._include_assets = enabled
        self.reload()

    def set_include_shots(self, enabled: bool) -> None:
        self._include_shots = enabled
        self.reload()

    def scroll_to_today(self) -> None:
        today = date.today()
        vp = self._timeline_hscroll.viewport()
        x = int(self._timeline_pane._date_to_x(today) - vp.width() * 0.3)
        self._timeline_hscroll.horizontalScrollBar().setValue(max(0, x))

    def set_entity_highlight(
        self,
        entity_kind: str,
        entity_rel: str,
        department: str | None = None,
    ) -> None:
        kind, rel = _GanttCanvas._norm_entity_key(entity_kind, entity_rel)
        if not kind or not rel:
            self.clear_entity_highlight()
            return
        dep = (department or "").strip() or None
        highlight: _ScheduleHighlight = (kind, rel, dep)
        self._label_pane._sync_highlight(highlight)
        self._timeline_pane._sync_highlight(highlight)

    def highlight_entities(
        self,
        entities: list[tuple[str, str]],
        *,
        expand_entity_rows: bool = False,
    ) -> None:
        """Highlight every visible row for each (entity_kind, entity_rel)."""
        keys = frozenset(
            _GanttCanvas._norm_entity_key(kind, rel)
            for kind, rel in entities
            if (kind or "").strip() and (rel or "").strip()
        )
        if not keys:
            self.clear_entity_highlight()
            return
        if expand_entity_rows and self._view_mode == VIEW_ENTITY:
            for g in self._groups:
                if _GanttCanvas._norm_entity_key(g.entity_kind, g.entity_rel) in keys:
                    self._expanded.add(g.key)
            self._apply_canvas_data()
        self._label_pane._sync_highlight_entities(keys)
        self._timeline_pane._sync_highlight_entities(keys)

    def clear_entity_highlight(self) -> None:
        self._label_pane._sync_highlight(None)
        self._timeline_pane._sync_highlight(None)

    def scroll_to_entity_keys(self, entities: list[tuple[str, str]]) -> int | None:
        """Scroll so the first visible row among entities is in view; return that row index."""
        if not entities:
            return None
        visible = self._label_pane._visible
        indices: list[int] = []
        for kind, rel in entities:
            row = self._find_reveal_row(visible, kind, rel, None)
            if row is not None:
                indices.append(row)
        if not indices:
            return None
        target = min(indices)
        self._scroll_to_visible_row(target)
        return target

    def _scroll_to_visible_row(self, visible_index: int) -> None:
        y = max(0, visible_index * _ROW_H)
        vp_h = max(1, self._body_vscroll.viewport().height())
        vs = self._body_vscroll.verticalScrollBar()
        self._set_body_vscroll(min(max(0, y - vp_h // 3), vs.maximum()))

    def _scroll_to_date(self, d: date) -> None:
        vp = self._timeline_hscroll.viewport()
        x = int(self._timeline_pane._date_to_x(d) - vp.width() * 0.35)
        self._timeline_hscroll.horizontalScrollBar().setValue(max(0, x))

    def _find_reveal_row(
        self,
        visible: list[_DisplayRow],
        entity_kind: str,
        entity_rel: str,
        department: str | None,
    ) -> int | None:
        key = _GanttCanvas._norm_entity_key(entity_kind, entity_rel)
        dep = (department or "").strip() or None
        dept_rows: list[int] = []
        entity_rows: list[int] = []
        for vi, display in enumerate(visible):
            if display.mode == "scope_separator":
                continue
            if display.mode == "dept_lane" and display.dept is not None:
                dr = display.dept
                if _GanttCanvas._norm_entity_key(dr.entity_kind, dr.entity_rel) != key:
                    continue
                row_dep = (dr.department or "").strip()
                if dep is None or row_dep == dep:
                    dept_rows.append(vi)
            elif display.dept is not None and display.mode == "dept":
                dr = display.dept
                if _GanttCanvas._norm_entity_key(dr.entity_kind, dr.entity_rel) != key:
                    continue
                row_dep = (dr.department or "").strip()
                if dep is None or row_dep == dep:
                    dept_rows.append(vi)
            elif display.group is not None:
                if _GanttCanvas._norm_entity_key(
                    display.group.entity_kind, display.group.entity_rel
                ) != key:
                    continue
                if display.mode in ("collapsed", "header"):
                    if dep is None:
                        entity_rows.append(vi)
        if dep and dept_rows:
            return dept_rows[0]
        if dept_rows:
            return dept_rows[0]
        if entity_rows:
            return entity_rows[0]
        return None

    def reveal_entity(
        self,
        entity_kind: str,
        entity_rel: str,
        *,
        department: str | None = None,
        due: date | None = None,
    ) -> bool:
        """Scroll to entity (and optional department), highlight rows, select for Inspector."""
        kind, rel = _GanttCanvas._norm_entity_key(entity_kind, entity_rel)
        if not kind or not rel:
            return False
        found_key: tuple[str, str] | None = None
        for g in self._groups:
            if _GanttCanvas._norm_entity_key(g.entity_kind, g.entity_rel) == (kind, rel):
                found_key = g.key
                break
        if found_key is None:
            return False
        if self._view_mode == VIEW_ENTITY:
            self._expanded.add(found_key)
            self._apply_canvas_data()
        visible = self._label_pane._visible
        row = self._find_reveal_row(visible, kind, rel, department)
        if row is None:
            return False
        dep = (department or "").strip() or None
        self.set_entity_highlight(kind, rel, dep)
        self._scroll_to_visible_row(row)
        scroll_date = due
        if scroll_date is None and dep:
            bar = self._bars.get((kind, rel, dep))
            if bar is not None:
                scroll_date = bar.due
        if scroll_date is not None:
            self._scroll_to_date(scroll_date)
        self.entity_row_selected.emit(kind, rel)
        self._label_pane.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def pan_by(self, dx: int, dy: int) -> None:
        h = self._timeline_hscroll.horizontalScrollBar()
        v = self._body_vscroll.verticalScrollBar()
        h.setValue(h.value() + dx)
        if dy:
            self._set_body_vscroll(v.value() + dy)

    def zoom_in(self, anchor_viewport_x: float | None = None) -> None:
        if anchor_viewport_x is None:
            anchor_viewport_x = self._timeline_hscroll.viewport().width() / 2
        self._zoom_to_day_width(self._timeline_pane._day_w * _ZOOM_STEP_FACTOR, anchor_viewport_x)

    def zoom_out(self, anchor_viewport_x: float | None = None) -> None:
        if anchor_viewport_x is None:
            anchor_viewport_x = self._timeline_hscroll.viewport().width() / 2
        self._zoom_to_day_width(self._timeline_pane._day_w / _ZOOM_STEP_FACTOR, anchor_viewport_x)

    def _viewport_x_from_canvas(self, event: QMouseEvent) -> float:
        return self._viewport_x_from_global(event.globalPosition().toPoint())

    def _apply_day_width(
        self,
        day_w: float,
        *,
        adjust_scroll: bool,
        anchor_viewport_x: float = 0.0,
        min_delta: float = 0.05,
    ) -> None:
        old_dw = self._timeline_pane._day_w
        new_dw = max(_MIN_DAY_W, min(_MAX_DAY_W, float(day_w)))
        if min_delta > 0 and abs(new_dw - old_dw) < min_delta:
            return
        self._timeline_pane.set_day_width(new_dw)
        self._header_pane.set_day_width(new_dw)
        self._ensure_body_pane_heights()
        if not adjust_scroll:
            return
        hscroll = self._timeline_hscroll.horizontalScrollBar()
        canvas_x = hscroll.value() + anchor_viewport_x
        day_frac = max(0.0, canvas_x) / old_dw if old_dw > 0 else 0.0
        new_canvas_x = day_frac * new_dw
        hscroll.setValue(int(max(0, new_canvas_x - anchor_viewport_x)))

    def _zoom_to_day_width(self, day_w: float, anchor_viewport_x: float) -> None:
        self._apply_day_width(day_w, adjust_scroll=True, anchor_viewport_x=anchor_viewport_x)

    def is_nav_active(self) -> bool:
        return self._nav_panning or self._nav_zooming

    def _nav_begin_pan(self, event: QMouseEvent) -> None:
        self._nav_panning = True
        self._nav_pan_origin_global = event.globalPosition().toPoint()
        self._nav_pan_scroll = (
            self._timeline_hscroll.horizontalScrollBar().value(),
            self._body_vscroll.verticalScrollBar().value(),
        )
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _viewport_x_from_global(self, global_pt: QPoint) -> float:
        vp = self._timeline_hscroll.viewport()
        vx = float(vp.mapFromGlobal(global_pt).x())
        return max(0.0, min(vx, float(max(0, vp.width() - 1))))

    def _try_begin_nav_from_canvas(self, event: QMouseEvent) -> bool:
        if self._try_nav_press(event):
            self._begin_nav_grab()
            return True
        return False

    def _begin_nav_grab(self) -> None:
        self.grabMouse()
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _end_nav_grab(self) -> None:
        if self.mouseGrabber() is self:
            self.releaseMouse()
        _GanttCanvas._release_if_grabbed(self._timeline_pane)
        _GanttCanvas._release_if_grabbed(self._label_pane)
        _GanttCanvas._release_if_grabbed(self._header_pane)

    def _forward_nav_mouse_move(self, event: QMouseEvent) -> bool:
        if not self.is_nav_active():
            return False
        self._nav_mouse_move(event)
        event.accept()
        return True

    def _forward_nav_mouse_release(self, event: QMouseEvent) -> bool:
        if not self.is_nav_active():
            return False
        if event.button() not in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            return False
        self._nav_mouse_release(event)
        self._end_nav_grab()
        return True

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._forward_nav_mouse_move(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._forward_nav_mouse_release(event):
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_label_pane"):
            self._ensure_body_pane_heights()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if hasattr(self, "_label_pane"):
            self._ensure_body_pane_heights()

    def _nav_begin_zoom(self, event: QMouseEvent) -> None:
        self._nav_zooming = True
        g = event.globalPosition().toPoint()
        self._nav_zoom_start_global_x = float(event.globalPosition().x())
        self._nav_zoom_start_day_w = self._timeline_pane._day_w
        self._nav_zoom_anchor_x = self._viewport_x_from_global(g)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _nav_zoom_day_width_for_global_x(self, global_x: float) -> float:
        drag_px = global_x - self._nav_zoom_start_global_x
        return self._nav_zoom_start_day_w + drag_px * _ZOOM_DRAG_PIXELS_PER_DAY_W

    def _nav_mouse_move(self, event: QMouseEvent) -> None:
        if self._nav_panning and self._nav_pan_origin_global is not None:
            delta = event.globalPosition().toPoint() - self._nav_pan_origin_global
            h = self._timeline_hscroll.horizontalScrollBar()
            h.setValue(self._nav_pan_scroll[0] - delta.x())
            self._set_body_vscroll(self._nav_pan_scroll[1] - delta.y())
            return
        if self._nav_zooming:
            new_dw = self._nav_zoom_day_width_for_global_x(float(event.globalPosition().x()))
            self._apply_day_width(
                new_dw,
                adjust_scroll=True,
                anchor_viewport_x=self._nav_zoom_anchor_x,
                min_delta=0.0,
            )

    def _nav_mouse_release(self, event: QMouseEvent) -> None:
        if self._nav_panning and event.button() == Qt.MouseButton.MiddleButton:
            self._nav_panning = False
            self._nav_pan_origin_global = None
            self.unsetCursor()
        elif self._nav_zooming and event.button() == Qt.MouseButton.RightButton:
            self._nav_zooming = False
            self._apply_day_width(
                self._nav_zoom_day_width_for_global_x(float(event.globalPosition().x())),
                adjust_scroll=True,
                anchor_viewport_x=self._nav_zoom_anchor_x,
                min_delta=0.0,
            )
            self.unsetCursor()

    def _try_nav_press(self, event: QMouseEvent) -> bool:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._nav_begin_pan(event)
            return True
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            if event.button() == Qt.MouseButton.RightButton:
                self._nav_begin_zoom(event)
                return True
        return False

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if not hasattr(self, "_body_vscroll"):
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.Resize and obj is self._body_vscroll.viewport():
            self._ensure_body_pane_heights()
        if event.type() == QEvent.Type.Wheel:
            wheel = event
            scroll_targets = (
                self._label_pane,
                self._timeline_pane,
                self._body_vscroll.viewport(),
                self._timeline_hscroll.viewport(),
                self._header_pane,
                self._header_scroll.viewport(),
            )
            if obj in scroll_targets:
                if (
                    obj in (self._timeline_hscroll.viewport(), self._timeline_pane)
                    and wheel.modifiers() & Qt.KeyboardModifier.AltModifier
                    and wheel.angleDelta().y()
                ):
                    steps = wheel.angleDelta().y() / _WHEEL_ZOOM_DEGREES_PER_STEP
                    factor = math.pow(_ZOOM_STEP_FACTOR, steps)
                    anchor = self._viewport_x_from_global(wheel.globalPosition().toPoint())
                    new_dw = self._timeline_pane._day_w * factor
                    self._apply_day_width(
                        new_dw,
                        adjust_scroll=True,
                        anchor_viewport_x=anchor,
                    )
                    return True
                if self._apply_wheel_hscroll(wheel):
                    if not wheel.angleDelta().y() or (
                        wheel.modifiers() & Qt.KeyboardModifier.ShiftModifier
                    ):
                        return True
                delta_y = wheel.angleDelta().y()
                if delta_y:
                    self._body_vscroll_wheel(delta_y)
                    return True
        if obj in (
            self._label_pane,
            self._header_pane,
            self._timeline_pane,
            self._timeline_hscroll.viewport(),
        ):
            if event.type() == QEvent.Type.MouseButtonPress:
                me = event
                if isinstance(me, QMouseEvent) and self._try_begin_nav_from_canvas(me):
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        step = max(24, int(self._canvas._day_w * 3))
        if key in (Qt.Key.Key_Left, Qt.Key.Key_H):
            self.pan_by(-step, 0)
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_L):
            self.pan_by(step, 0)
            return
        if key == Qt.Key.Key_Up:
            self.pan_by(0, -_ROW_H)
            return
        if key == Qt.Key.Key_Down:
            self.pan_by(0, _ROW_H)
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            return
        if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_out()
            return
        if key in (Qt.Key.Key_Home, Qt.Key.Key_T):
            self.scroll_to_today()
            return
        super().keyPressEvent(event)

    def set_tool(self, tool: str) -> None:
        self._label_pane.set_tool(tool)
        self._timeline_pane.set_tool(tool)
        self._apply_tool_cursors()

    def set_schedule_editable(self, editable: bool) -> None:
        self._schedule_editable = bool(editable)
        if not editable:
            self.set_tool(TOOL_SELECT)
        self._label_pane.update()
        self._timeline_pane.update()

    def _apply_tool_cursors(self) -> None:
        """Default pointer for the active tool (hover handlers may override)."""
        tool = self._timeline_pane._tool
        shape = _GanttCanvas._tool_idle_cursor(tool)
        for widget in (
            self,
            self._header_pane,
            self._label_pane,
            self._timeline_pane,
            self._header_scroll.viewport(),
            self._body_vscroll.viewport(),
            self._timeline_hscroll.viewport(),
        ):
            widget.setCursor(shape)

    def set_dept_display(
        self,
        *,
        hidden_departments: set[str] | None = None,
        respect_inspector_hidden: bool | None = None,
        dept_scope: str | None = None,
        wave_group_by_parent: bool | None = None,
        reload_now: bool = True,
    ) -> None:
        if hidden_departments is not None:
            self._hidden_departments = set(hidden_departments)
        if respect_inspector_hidden is not None:
            self._respect_inspector_hidden = bool(respect_inspector_hidden)
        if dept_scope is not None:
            self._dept_scope = DEPT_SCOPE_LEAF
        if wave_group_by_parent is not None:
            self._wave_group_parent = bool(wave_group_by_parent)
        if (
            reload_now
            and self._project_root is not None
            and self._project_index is not None
        ):
            self.reload()

    def set_inspector_hidden_departments(self, hidden: set[str]) -> None:
        self._hidden_departments = set(hidden or ())
        if self._respect_inspector_hidden and self._project_root is not None:
            self.reload()

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = (
            mode if mode in (VIEW_ENTITY, VIEW_DEPARTMENT, VIEW_DEPT_WAVE) else VIEW_ENTITY
        )
        self._apply_canvas_data()

    def set_wave_draw_apply_mode(self, mode: str) -> None:
        self._timeline_pane.set_wave_draw_apply_mode(mode)

    def set_bar_label_mode(self, mode: str) -> None:
        for pane in (self._label_pane, self._timeline_pane, self._header_pane):
            pane.set_bar_label_mode(mode)

    def set_date_display_format(self, fmt_id: str) -> None:
        for pane in (self._label_pane, self._timeline_pane, self._header_pane):
            pane.set_date_display_format(fmt_id)

    def day_width(self) -> float:
        return self._timeline_pane._day_w

    def view_options_anchor(self) -> QWidget:
        """Corner gear button — anchor for the page's view-options popup."""
        return self._btn_corner_filter

    def current_tool(self) -> str:
        return self._timeline_pane._tool

    def set_allowed_departments(self, dept_ids: list[str] | None) -> None:
        """Sidebar visible departments — timeline ignores other departments (no errors)."""
        if dept_ids is None:
            self._allowed_departments = None
        else:
            self._allowed_departments = frozenset(
                d.strip() for d in dept_ids if isinstance(d, str) and d.strip()
            )

    def _build_corner_search(self) -> QWidget:
        """Top-left corner: search field + filter button (aligns with month header)."""
        container = QWidget(self)
        container.setObjectName("ScheduleCornerSearch")
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setFixedSize(_LABEL_W_DEFAULT, _HEADER_H)
        lay = QHBoxLayout(container)
        lay.setContentsMargins(8, 0, 6, 0)
        lay.setSpacing(4)

        self._search_edit = QLineEdit(container)
        self._search_edit.setObjectName("ScheduleSearchEdit")
        self._search_edit.setPlaceholderText("Search assets or shots…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.addAction(
            lucide_icon("search", size=14, color_hex="#71717a"),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        lay.addWidget(self._search_edit, 1)

        self._btn_corner_filter = QToolButton(container)
        self._btn_corner_filter.setObjectName("ScheduleSearchFilterBtn")
        self._btn_corner_filter.setToolTip("View options — filters, bar label, date, wave draw")
        self._btn_corner_filter.setAutoRaise(True)
        self._btn_corner_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_corner_filter.setIcon(lucide_icon("settings", size=15, color_hex="#a1a1aa"))
        self._btn_corner_filter.setIconSize(QSize(15, 15))
        self._btn_corner_filter.setFixedSize(26, 26)
        self._btn_corner_filter.clicked.connect(self.search_filter_requested.emit)
        lay.addWidget(self._btn_corner_filter, 0, Qt.AlignmentFlag.AlignVCenter)

        self._name_filter = ""
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(180)
        self._search_debounce.timeout.connect(self._apply_search_filter)
        self._search_edit.textChanged.connect(lambda _t: self._search_debounce.start())
        return container

    def _apply_search_filter(self) -> None:
        text = (self._search_edit.text() or "").strip().casefold()
        if text == self._name_filter:
            return
        self._name_filter = text
        self._rebuild_filtered_groups()

    def set_name_filter(self, text: str) -> None:
        nf = (text or "").strip()
        if self._search_edit.text() != nf:
            self._search_edit.setText(nf)
        else:
            self._apply_search_filter()

    def apply_filters(
        self,
        *,
        dept_filter: str | None,
        unscheduled_only: bool,
        overdue_only: bool = False,
        type_filter: str | None = None,
        type_aliases: set[str] | None = None,
        allowed_department_ids: list[str] | None = None,
    ) -> None:
        if allowed_department_ids is not None:
            self.set_allowed_departments(allowed_department_ids)
        self._dept_filter = (dept_filter or "").strip() or None
        self._type_filter = (type_filter or "").strip() or None
        self._type_filter_aliases = {x.casefold() for x in (type_aliases or set()) if x}
        if self._type_filter and not self._type_filter_aliases:
            self._type_filter_aliases = {self._type_filter.casefold()}
        self._unscheduled_only = bool(unscheduled_only)
        self._overdue_only = bool(overdue_only)
        self._rebuild_filtered_groups()

    def _rebuild_filtered_groups(self) -> None:
        if self._project_root is None or self._project_index is None:
            return
        all_groups = build_timeline_entity_groups(
            self._project_root,
            self._project_index,
            include_shots=self._include_shots,
            include_assets=self._include_assets,
        )
        all_groups = self._apply_dept_visibility(all_groups)
        self._groups = self._filter_groups(all_groups)
        self._apply_canvas_data()

    def visible_department_labels(self) -> dict[str, str]:
        return self._visible_departments_for_filter()

    def _visible_departments_for_filter(self) -> dict[str, str]:
        """dept_id -> label for combo (respects hidden + scope)."""
        if self._dept_reg is None:
            return {}
        labels: dict[str, str] = {}
        for dep_id in self._dept_order:
            dep = (dep_id or "").strip()
            if not dep:
                continue
            from monostudio.core.schedule_dept_filter import filter_timeline_row

            dummy = TimelineRow(
                entity_kind="shot",
                entity_rel="",
                entity_name="",
                department=dep,
                department_label=self._dept_reg.get_department_label(dep),
            )
            if not filter_timeline_row(
                dummy,
                hidden_departments=self._hidden_departments,
                dept_scope=self._dept_scope,
                dept_reg=self._dept_reg,
                respect_hidden=self._respect_inspector_hidden,
            ):
                continue
            labels[dep] = self._dept_reg.get_department_label(dep) or dep
        return labels

    def _apply_dept_visibility(self, groups: list[TimelineEntityGroup]) -> list[TimelineEntityGroup]:
        from monostudio.core.schedule_dept_filter import filter_groups_by_allowed_departments

        if self._dept_reg is not None:
            groups = filter_entity_groups(
                groups,
                hidden_departments=self._hidden_departments,
                dept_scope=self._dept_scope,
                dept_reg=self._dept_reg,
                respect_hidden=self._respect_inspector_hidden,
            )
        return filter_groups_by_allowed_departments(groups, self._allowed_departments)

    def _bars_for_display(self) -> dict[tuple[str, str, str], PlannedBar]:
        allowed = self._allowed_departments
        if allowed is None:
            return self._bars
        return {
            k: v
            for k, v in self._bars.items()
            if (k[2] or "").strip() in allowed
        }

    def _rollup_kwargs(self) -> dict:
        return {
            "hidden_departments": self._hidden_departments,
            "dept_scope": self._dept_scope,
            "dept_reg": self._dept_reg,
            "respect_hidden": self._respect_inspector_hidden,
            "group_by_parent": self._wave_group_parent,
        }

    @staticmethod
    def _is_shot_type_id(type_id: str) -> bool:
        tid = (type_id or "").strip()
        return tid == "shot" or tid.startswith("shot_")

    def _group_matches_type_filter(self, group: TimelineEntityGroup) -> bool:
        tid = self._type_filter
        if not tid:
            return True
        if self._is_shot_type_id(tid):
            return group.entity_kind == "shot"
        if group.entity_kind != "asset":
            return False
        rel = group.entity_rel.replace("\\", "/")
        at = self._asset_type_by_rel.get(rel, "")
        return at in self._type_filter_aliases

    def _filter_groups(self, groups: list[TimelineEntityGroup]) -> list[TimelineEntityGroup]:
        out: list[TimelineEntityGroup] = []
        for group in groups:
            if not self._group_matches_type_filter(group):
                continue
            if self._name_filter:
                name = (group.entity_name or "").casefold()
                rel = group.entity_rel.replace("\\", "/").casefold()
                if self._name_filter not in name and self._name_filter not in rel:
                    continue
            if self._unscheduled_only and not entity_is_unscheduled(
                self._schedule,
                entity_kind=group.entity_kind,
                entity_rel=group.entity_rel,
                bars=self._bars_for_display(),
            ):
                continue
            if self._dept_filter:
                has_dept = any(
                    (d.department or "").strip() == self._dept_filter for d in group.departments
                )
                if not has_dept:
                    continue
            if self._overdue_only:
                rel = group.entity_rel.replace("\\", "/")
                entity_bars = [
                    b
                    for k, b in self._bars.items()
                    if k[0] == group.entity_kind and k[1] == rel
                ]
                if not any(b.overdue for b in entity_bars):
                    continue
            out.append(group)
        return out

    def _apply_canvas_data(self) -> None:
        display_bars = self._bars_for_display()
        v0, v1 = compute_view_date_range_from_bars(
            display_bars,
            self._schedule,
            project_root=self._project_root,
        )
        rollups = rollup_bars_by_department(
            display_bars,
            self._groups,
            self._dept_order,
            dept_filter=self._dept_filter,
            **self._rollup_kwargs(),
        )
        self._set_pane_data(
            project_root=self._project_root,
            workspace_root=self._workspace_root,
            groups=self._groups,
            expanded=self._expanded,
            bars=display_bars,
            schedule=self._schedule,
            view_start=v0,
            view_end=v1,
            view_mode=self._view_mode,
            dept_order=self._dept_order,
            dept_filter=self._dept_filter,
            wave_rollups=rollups,
        )

    def reload(self) -> None:
        if self._project_root is None or self._project_index is None:
            self._groups = []
            self._bars = {}
            self._schedule = ProjectSchedule()
            self._entity_refs = {}
            self._set_pane_data(
                project_root=None,
                workspace_root=self._workspace_root,
                groups=[],
                expanded=set(),
                bars={},
                schedule=self._schedule,
                view_start=date.today(),
                view_end=date.today() + timedelta(days=56),
            )
            return
        self._schedule = read_project_schedule(self._project_root)
        self._rebuild_entity_refs()
        from monostudio.core.department_registry import DepartmentRegistry

        dept_reg = DepartmentRegistry.for_project(self._project_root)
        self._dept_reg = dept_reg
        self._dept_order = list(dept_reg.get_departments())
        self._asset_type_by_rel = {}
        for asset in self._project_index.assets:
            rel = entity_rel_path(self._project_root, asset.path).replace("\\", "/")
            self._asset_type_by_rel[rel] = (asset.asset_type or "").strip().casefold()
        all_groups = build_timeline_entity_groups(
            self._project_root,
            self._project_index,
            include_shots=self._include_shots,
            include_assets=self._include_assets,
        )
        all_groups = self._apply_dept_visibility(all_groups)
        self._bars = build_planned_bars(
            self._project_root,
            self._project_index,
            self._schedule,
            include_shots=self._include_shots,
            include_assets=self._include_assets,
        )
        self._groups = self._filter_groups(all_groups)
        known = {g.key for g in self._groups}
        self._expanded = {k for k in self._expanded if k in known}
        self._apply_canvas_data()
        QTimer.singleShot(0, self.prefetch_entity_thumbnails)

    def set_thumbnail_manager(self, manager: ThumbnailManager | None) -> None:
        self._thumbnail_manager = manager
        self.prefetch_entity_thumbnails()
        self._label_pane.update()

    def _rebuild_entity_refs(self) -> None:
        from monostudio.core.models import Asset, Shot

        refs: dict[tuple[str, str], Asset | Shot] = {}
        if self._project_root is None or self._project_index is None:
            self._entity_refs = refs
            return
        root = self._project_root
        for shot in self._project_index.shots:
            rel = entity_rel_path(root, shot.path).replace("\\", "/")
            refs[("shot", rel)] = shot
        for asset in self._project_index.assets:
            rel = entity_rel_path(root, asset.path).replace("\\", "/")
            refs[("asset", rel)] = asset
        self._entity_refs = refs

    def pixmap_for_entity(self, entity_kind: str, entity_rel: str) -> QPixmap | None:
        mgr = self._thumbnail_manager
        if mgr is None:
            return None
        key = ((entity_kind or "").strip().lower(), (entity_rel or "").replace("\\", "/"))
        ref = self._entity_refs.get(key)
        if ref is None:
            return None
        path = getattr(ref, "path", None)
        if path is None:
            return None
        return mgr.request_thumbnail(
            str(path),
            department=None,
            pipeline_ref=ref,
        )

    def prefetch_entity_thumbnails(self) -> None:
        if self._thumbnail_manager is None:
            return
        seen: set[tuple[str, str]] = set()
        for group in self._groups:
            if group.key in seen:
                continue
            seen.add(group.key)
            self.pixmap_for_entity(group.entity_kind, group.entity_rel)

    def refresh_thumbnails_for_paths(self, paths: list | object) -> None:
        if not paths:
            return
        id_set = {str(p).strip() for p in paths if p}
        if not id_set:
            return
        for ref in self._entity_refs.values():
            ep = str(getattr(ref, "path", "") or "")
            if ep in id_set:
                self._label_pane.update()
                return

    def set_project(self, project_root: Path | None, project_index: ProjectIndex | None) -> None:
        self._project_root = Path(project_root) if project_root else None
        self._project_index = project_index
        self.reload()

    def _on_expand_toggled(self, group_key: tuple, expanded: bool) -> None:
        if expanded:
            self._expanded.add(group_key)
        else:
            self._expanded.discard(group_key)
        self._apply_canvas_data()

    def _on_override_committed(self) -> None:
        if self._project_root is None or self._project_index is None:
            self.schedule_changed.emit()
            return
        self._schedule = read_project_schedule(self._project_root)
        self._bars = build_planned_bars(
            self._project_root,
            self._project_index,
            self._schedule,
            include_shots=self._include_shots,
            include_assets=self._include_assets,
        )
        self._apply_canvas_data()
        self.schedule_changed.emit()

    def _on_row_activated(self, visible_index: int) -> None:
        if not self._schedule_editable:
            return
        rollups = rollup_bars_by_department(
            self._bars,
            self._groups,
            self._dept_order,
            dept_filter=self._dept_filter,
            **self._rollup_kwargs(),
        )
        visible = _build_visible_rows(
            self._groups,
            self._expanded,
            view_mode=self._view_mode,
            dept_order=self._dept_order,
            dept_filter=self._dept_filter,
            wave_rollups=rollups,
        )
        if visible_index < 0 or visible_index >= len(visible):
            return
        display = visible[visible_index]
        if display.mode == "dept_wave" and display.wave is not None:
            self.wave_drilldown_requested.emit(display.wave.department)
            return
        if display.mode in ("header", "dept_lane_header"):
            return
        if display.group is None:
            return
        dept_row: TimelineRow | None = display.dept
        if display.mode == "collapsed":
            hover = self._canvas._hover_bar
            if hover and hover[0] == visible_index:
                dep = hover[1]
                for row in display.group.departments:
                    if (row.department or "").strip() == dep:
                        dept_row = row
                        break
            if dept_row is None and display.group.departments:
                dept_row = display.group.departments[0]
        if dept_row is None:
            return
        existing = allocation_for_row(
            self._schedule,
            entity_kind=dept_row.entity_kind,
            entity_rel=dept_row.entity_rel,
            department=dept_row.department,
        )
        if existing is not None:
            self.edit_allocation_requested.emit(existing)
        else:
            self.new_allocation_requested.emit(dept_row)

    def planned_dates_for_row(self, row: TimelineRow) -> tuple[str, str] | None:
        key = _row_key(row.entity_kind, row.entity_rel, row.department)
        bar = self._bars.get(key)
        if bar is None:
            return None
        return bar.start.isoformat(), bar.due.isoformat()

    def timeline_rows(self) -> list[TimelineRow]:
        out: list[TimelineRow] = []
        for group in self._groups:
            out.extend(group.departments)
        return out

    def current_schedule(self) -> ProjectSchedule:
        return self._schedule
