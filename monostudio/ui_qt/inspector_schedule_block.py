"""Compact schedule summary for Inspector (asset / shot)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPainter, QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, Shot
from monostudio.core.project_schedule import read_project_schedule
from monostudio.core.schedule_planner import (
    STATUS_DONE,
    STATUS_EXCLUDED,
    STATUS_PROGRESS,
    PlannedBar,
    summarize_entity_from_ref,
)
from monostudio.ui_qt.assignee_picker_widget import AssigneePickerWidget
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.view_items import ViewItem

_RED = "#ef4444"
_COLOR_DONE = "#10b981"
_COLOR_PROGRESS = "#3b82f6"
_COLOR_WAITING = "#71717a"

# Production UI v1 — 8pt grid (see .cursor/rules/production_ui_v1.mdc)
_CARD_PAD = 16
_CONTENT_INDENT = 12
_SUB_CARD_PAD = 12
_SUB_CONTENT_INDENT = 8
_SECTION_GAP = 16
_ROW_GAP = 8


def _sub_section_title(text: str, parent: QWidget) -> QLabel:
    lbl = QLabel(text.upper(), parent)
    lbl.setObjectName("InspectorSubSectionTitle")
    lbl.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
    lbl.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')};")
    return lbl


def _schedule_sub_card(title: str, parent: QWidget) -> tuple[QFrame, QVBoxLayout, QLabel]:
    """Nested container inside InspectorScheduleCard (brighter BG, subtle border)."""
    frame = QFrame(parent)
    frame.setObjectName("InspectorScheduleSubCard")
    frame.setAttribute(Qt.WA_StyledBackground, True)
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(_SUB_CARD_PAD, _SUB_CARD_PAD, _SUB_CARD_PAD, _SUB_CARD_PAD)
    outer.setSpacing(_ROW_GAP)
    title_lbl = _sub_section_title(title, frame)
    outer.addWidget(title_lbl)
    body = QWidget(frame)
    body_l = QVBoxLayout(body)
    body_l.setContentsMargins(_SUB_CONTENT_INDENT, 0, 0, 0)
    body_l.setSpacing(_ROW_GAP)
    outer.addWidget(body)
    return frame, body_l, title_lbl


def _indented_block(parent: QWidget, *, indent: int = _CONTENT_INDENT) -> tuple[QWidget, QVBoxLayout]:
    wrap = QWidget(parent)
    lay = QVBoxLayout(wrap)
    lay.setContentsMargins(indent, 0, 0, 0)
    lay.setSpacing(_ROW_GAP)
    return wrap, lay


def _status_badge_props(bar: PlannedBar) -> tuple[str, str, str]:
    """Return (status_key, label, color_hex) for progress color mapping."""
    if bar.status == STATUS_EXCLUDED:
        return ("waiting", "SKIPPED", _COLOR_WAITING)
    if bar.overdue and bar.status != STATUS_DONE:
        return ("blocked", "OVERDUE", _RED)
    if bar.status == STATUS_DONE:
        return ("ready", "DONE", _COLOR_DONE)
    if bar.status == STATUS_PROGRESS:
        return ("progress", "IN PROGRESS", _COLOR_PROGRESS)
    return ("waiting", "WAITING", _COLOR_WAITING)


def _format_display_date(d: date) -> str:
    return d.strftime("%b %d, %Y")


def _metric_label(text: str, parent: QWidget) -> QLabel:
    lbl = QLabel(text, parent)
    lbl.setObjectName("InspectorDeptMetricLabel")
    lbl.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
    return lbl


def _metric_divider(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet("background: rgba(255, 255, 255, 0.06); border: none;")
    return line


def _metric_row(parent: QWidget) -> tuple[QWidget, QHBoxLayout]:
    row = QWidget(parent)
    row.setObjectName("InspectorDeptMetricRow")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(12)
    return row, lay


class _DeptStatusPanel(QFrame):
    """Department deadline metrics: progress, due date, overdue."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDeptStatusPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        progress_row, progress_l = _metric_row(self)
        progress_l.addWidget(_metric_label("Progress", progress_row), 0, Qt.AlignmentFlag.AlignVCenter)
        self._progress_bar = _ScheduleTimelineBar(progress_row)
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setMinimumWidth(72)
        self._progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._progress_pct = QLabel("0%", progress_row)
        self._progress_pct.setObjectName("InspectorDeptMetricValue")
        self._progress_pct.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        self._progress_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._progress_pct.setMinimumWidth(40)
        progress_l.addWidget(self._progress_bar, 1, Qt.AlignmentFlag.AlignVCenter)
        progress_l.addWidget(self._progress_pct, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(progress_row)
        outer.addWidget(_metric_divider(self))

        due_row, due_l = _metric_row(self)
        due_l.addWidget(_metric_label("Due Date", due_row), 0, Qt.AlignmentFlag.AlignVCenter)
        due_l.addStretch(1)
        self._due_value = QLabel("—", due_row)
        self._due_value.setObjectName("InspectorDeptMetricValue")
        self._due_value.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
        self._due_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        due_l.addWidget(self._due_value, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(due_row)
        outer.addWidget(_metric_divider(self))

        overdue_row, overdue_l = _metric_row(self)
        overdue_l.addWidget(_metric_label("Overdue", overdue_row), 0, Qt.AlignmentFlag.AlignVCenter)
        overdue_l.addStretch(1)
        self._overdue_value = QLabel("—", overdue_row)
        self._overdue_value.setObjectName("InspectorDeptMetricValue")
        self._overdue_value.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        self._overdue_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        overdue_l.addWidget(self._overdue_value, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(overdue_row)

    def apply(self, bar: PlannedBar, today: date) -> None:
        total_days = max(1, (bar.due - bar.start).days)
        elapsed = (today - bar.start).days
        if bar.status == STATUS_DONE:
            ratio = 1.0
            bar_color = _COLOR_DONE
        elif bar.overdue:
            ratio = min(1.0, max(0.0, elapsed / total_days))
            bar_color = _RED
        else:
            ratio = min(1.0, max(0.0, elapsed / total_days))
            _, _, status_color = _status_badge_props(bar)
            bar_color = status_color

        pct = int(round(ratio * 100))
        self._progress_bar.set_progress(ratio, color_hex=bar_color)
        self._progress_pct.setText(f"{pct}%")
        self._progress_pct.setStyleSheet(f"color: {bar_color}; background: transparent;")

        self._due_value.setText(_format_display_date(bar.due))
        self._due_value.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_primary', '#fafafa')}; background: transparent;"
        )

        overdue_days = (today - bar.due).days
        if bar.status == STATUS_DONE:
            self._overdue_value.setText("—")
            self._overdue_value.setStyleSheet(
                f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; background: transparent;"
            )
        elif overdue_days > 0:
            unit = "day" if overdue_days == 1 else "days"
            self._overdue_value.setText(f"{overdue_days} {unit}")
            self._overdue_value.setStyleSheet(f"color: {_RED}; background: transparent;")
        else:
            self._overdue_value.setText("—")
            self._overdue_value.setStyleSheet(
                f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; background: transparent;"
            )


class _ScheduleTimelineBar(QWidget):
    """Thin bar: elapsed portion of start→due window (dashboard-style)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fill = 0.0
        self._color = _COLOR_PROGRESS
        self.setFixedHeight(6)
        self.setMinimumWidth(80)

    def set_progress(self, ratio: float, *, color_hex: str) -> None:
        self._fill = max(0.0, min(1.0, float(ratio)))
        self._color = color_hex or _COLOR_PROGRESS
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        radius = h / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#27272a"))
        p.drawRoundedRect(0, 0, int(w), int(h), radius, radius)
        if self._fill > 0:
            fill_w = max(h, self._fill * w)
            p.setBrush(QColor(self._color))
            p.drawRoundedRect(0, 0, int(fill_w), int(h), radius, radius)
        p.end()


class InspectorScheduleBlock(QWidget):
    open_schedule_requested = Signal()
    edit_allocation_requested = Signal()
    assignee_changed = Signal(str, str, str, object)  # kind, rel, department, assignee_ids list
    assignment_confirmed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorScheduleBlock")
        self._project_root: Path | None = None
        self._workspace_root: Path | None = None
        self._active_department: str | None = None
        self._dept_labels: dict[str, str] = {}
        self._focus_bar: PlannedBar | None = None
        self._focus_kind: str = ""
        self._focus_rel: str = ""
        self._assignee_sync = False
        self._schedule_editable = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(_SECTION_GAP)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(_ROW_GAP)
        title = QLabel("SCHEDULE", self)
        title.setObjectName("InspectorSectionTitle")
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._btn_open = QPushButton("Open", self)
        self._btn_open.setObjectName("DialogSecondaryButton")
        self._btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open.clicked.connect(self.open_schedule_requested.emit)
        self._btn_edit = QPushButton("Edit…", self)
        self._btn_edit.setObjectName("DialogSecondaryButton")
        self._btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_edit.setToolTip("Edit deadline and assignee for this department")
        self._btn_edit.clicked.connect(self.edit_allocation_requested.emit)
        self._btn_edit.setEnabled(False)
        hdr.addWidget(title, 1)
        hdr.addWidget(self._btn_open, 0)
        hdr.addWidget(self._btn_edit, 0)
        root.addLayout(hdr)

        card = QFrame(self)
        card.setObjectName("InspectorScheduleCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(_CARD_PAD, _CARD_PAD, _CARD_PAD, _CARD_PAD)
        card_l.setSpacing(_SECTION_GAP)

        self._dept_subcard, dept_l, self._dept_subcard_title = _schedule_sub_card("Department", card)
        self._dept_status_panel = _DeptStatusPanel(self._dept_subcard)
        dept_l.addWidget(self._dept_status_panel)

        self._dept_subcard.setVisible(False)
        card_l.addWidget(self._dept_subcard)

        self._assignee_subcard, assignee_l, _ = _schedule_sub_card("Assignee", card)
        self._assign_confirm_frame = QFrame(self._assignee_subcard)
        self._assign_confirm_frame.setObjectName("InspectorAssignConfirmBanner")
        self._assign_confirm_frame.setAttribute(Qt.WA_StyledBackground, True)
        confirm_l = QHBoxLayout(self._assign_confirm_frame)
        confirm_l.setContentsMargins(8, 8, 8, 8)
        confirm_l.setSpacing(_ROW_GAP)
        self._assign_confirm_label = QLabel("", self._assign_confirm_frame)
        self._assign_confirm_label.setWordWrap(True)
        self._assign_confirm_label.setObjectName("DialogHint")
        self._btn_assign_confirm = QPushButton("Confirm", self._assign_confirm_frame)
        self._btn_assign_confirm.setObjectName("DialogPrimaryButton")
        self._btn_assign_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_assign_confirm.clicked.connect(self._on_assign_confirm_clicked)
        confirm_l.addWidget(self._assign_confirm_label, 1)
        confirm_l.addWidget(self._btn_assign_confirm, 0)
        self._assign_confirm_frame.setVisible(False)
        assignee_l.addWidget(self._assign_confirm_frame)
        self._pending_assign_inbox_id = ""
        self._assignee_picker = AssigneePickerWidget(None, self._assignee_subcard)
        self._assignee_picker.users_changed.connect(self._on_assignee_picker_changed)
        assignee_l.addWidget(self._assignee_picker)
        self._assignee_subcard.setVisible(False)
        card_l.addWidget(self._assignee_subcard)

        hint_block, hint_l = _indented_block(card)
        self._dept_hint = QLabel("Select a department in the sidebar to see its deadline.", card)
        self._dept_hint.setWordWrap(True)
        self._dept_hint.setObjectName("DialogHint")
        self._dept_hint.setVisible(False)
        hint_l.addWidget(self._dept_hint)
        self._dept_hint_block = hint_block
        self._dept_hint_block.setVisible(False)
        card_l.addWidget(self._dept_hint_block)

        empty_block, empty_l = _indented_block(card)
        self._dept_empty = QLabel("", card)
        self._dept_empty.setWordWrap(True)
        self._dept_empty.setObjectName("DialogHint")
        self._dept_empty.setVisible(False)
        empty_l.addWidget(self._dept_empty)
        self._dept_empty_block = empty_block
        self._dept_empty_block.setVisible(False)
        card_l.addWidget(self._dept_empty_block)

        empty_plan_block, empty_plan_l = _indented_block(card)
        self._empty = QLabel("No plan yet — open Schedule to auto-plan or draw bars.", card)
        self._empty.setWordWrap(True)
        self._empty.setObjectName("DialogHint")
        empty_plan_l.addWidget(self._empty)
        self._empty_block = empty_plan_block
        card_l.addWidget(self._empty_block)

        root.addWidget(card)

    def set_dept_labels(self, labels: dict[str, str]) -> None:
        self._dept_labels = dict(labels or {})

    def set_project_root(self, path: Path | str | None) -> None:
        self._project_root = Path(path) if path else None

    def set_workspace_root(self, path: Path | str | None) -> None:
        try:
            self._workspace_root = Path(path).resolve() if path else None
        except OSError:
            self._workspace_root = None
        self._assignee_picker.set_workspace_root(self._workspace_root)
        self._apply_schedule_editable()

    def set_schedule_editable(self, editable: bool) -> None:
        self._schedule_editable = bool(editable)
        self._apply_schedule_editable()

    def _apply_schedule_editable(self) -> None:
        editable = self._schedule_editable
        read_only_tip = (
            "Schedule editing requires lead, supervisor, producer, or coordinator role."
        )
        self._assignee_picker.set_read_only(not editable)
        if editable:
            self._btn_edit.setToolTip("Edit deadline and assignee for this department")
        else:
            self._btn_edit.setToolTip(read_only_tip)
        if self._focus_bar is not None and self._dept_subcard.isVisible():
            self._btn_edit.setEnabled(editable)

    def _on_assignee_picker_changed(self, user_ids: list) -> None:
        if not self._schedule_editable:
            return
        if self._assignee_sync or not self._focus_bar or not self._focus_kind or not self._focus_rel:
            return
        dep = (self._focus_bar.department or "").strip()
        if not dep:
            return
        from monostudio.core.user_identity import normalize_assignee_ids

        ids = list(normalize_assignee_ids(user_ids))
        self.assignee_changed.emit(self._focus_kind, self._focus_rel, dep, ids)

    def _on_assign_confirm_clicked(self) -> None:
        iid = (self._pending_assign_inbox_id or "").strip()
        if not iid or self._project_root is None:
            return
        from monostudio.core.notification_copy import pick_copy
        from monostudio.core.schedule_assign_notify import confirm_schedule_assignment
        from monostudio.core.user_identity import get_current_user_display_name

        ok = confirm_schedule_assignment(
            self._project_root,
            self._workspace_root,
            iid,
            confirmed_by_name=get_current_user_display_name(self._workspace_root),
        )
        if not ok:
            return
        self._assign_confirm_frame.setVisible(False)
        self._pending_assign_inbox_id = ""
        self.assignment_confirmed.emit()
        from monostudio.ui_qt.notification import notify as notification_service

        notification_service.success(
            pick_copy("Đã xác nhận giao việc.", "Assignment confirmed."),
        )

    def _sync_assign_confirm_banner(self, bar: PlannedBar) -> None:
        self._assign_confirm_frame.setVisible(False)
        self._pending_assign_inbox_id = ""
        if self._project_root is None or self._workspace_root is None or not self._focus_rel:
            return
        from monostudio.core.assign_inbox import find_pending_for_user
        from monostudio.core.notification_copy import pick_copy
        from monostudio.core.user_identity import get_current_user, normalize_assignee_ids

        user = get_current_user(self._workspace_root)
        if user is None:
            return
        ids = list(bar.assignee_ids)
        if not ids and (bar.assignee_id or "").strip():
            ids = [(bar.assignee_id or "").strip()]
        if user.id not in normalize_assignee_ids(ids):
            return
        pending = find_pending_for_user(
            self._project_root,
            user.id,
            item_rel=self._focus_rel,
            department=bar.department,
            allocation_id=bar.allocation_id or "",
        )
        if pending is None:
            return
        self._pending_assign_inbox_id = pending.id
        sender = (pending.from_name or "").strip() or pick_copy("Ai đó", "Someone")
        self._assign_confirm_label.setText(
            pick_copy(
                f"{sender} giao việc này cho bạn — cần xác nhận.",
                f"{sender} assigned this to you — please confirm.",
            )
        )
        self._btn_assign_confirm.setText(pick_copy("Xác nhận", "Confirm"))
        self._assign_confirm_frame.setVisible(True)

    def set_active_department(self, department: str | None) -> None:
        self._active_department = (department or "").strip() or None

    def set_item(self, item: ViewItem | None, *, bars: dict[tuple[str, str, str], PlannedBar] | None = None) -> None:
        if item is None or self._project_root is None:
            self._clear()
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            self._clear()
            return

        schedule = read_project_schedule(self._project_root)
        summary = summarize_entity_from_ref(
            self._project_root,
            ref,
            schedule,
            active_department=self._active_department,
        )

        if not summary.has_plan:
            self._show_no_plan()
            return

        self._empty_block.setVisible(False)
        today = date.today()

        kind = "shot" if isinstance(ref, Shot) else "asset"
        try:
            rel = ref.path.resolve().relative_to(self._project_root.resolve()).as_posix()
        except (OSError, ValueError):
            rel = ref.path.as_posix()
        entity_bars = self._entity_bars(kind, rel, bars, schedule, ref)

        dep = self._active_department
        if not dep:
            self._dept_subcard.setVisible(False)
            self._assignee_subcard.setVisible(False)
            self._dept_empty_block.setVisible(False)
            self._dept_hint_block.setVisible(True)
            return

        self._dept_hint_block.setVisible(False)
        focus_bar = next((b for b in entity_bars if b.department == dep), None)
        if focus_bar is None:
            label = self._dept_labels.get(dep, dep)
            self._dept_subcard.setVisible(False)
            self._assignee_subcard.setVisible(False)
            self._btn_edit.setEnabled(False)
            self._dept_empty.setText(f"No schedule bar for {label} — open Schedule to plan this department.")
            self._dept_empty_block.setVisible(True)
            return

        self._dept_empty_block.setVisible(False)
        self._dept_subcard.setVisible(True)
        self._btn_edit.setEnabled(self._schedule_editable)
        self._render_dept_bar(focus_bar, today)

    def _entity_bars(
        self,
        kind: str,
        rel: str,
        bars: dict[tuple[str, str, str], PlannedBar] | None,
        schedule,
        ref: Asset | Shot,
    ) -> list[PlannedBar]:
        rel_norm = rel.replace("\\", "/")
        entity_bars = sorted(
            [b for k, b in (bars or {}).items() if k[0] == kind and k[1] == rel_norm],
            key=lambda b: b.due,
        )
        if entity_bars or bars is not None:
            return entity_bars
        from monostudio.core.schedule_planner import build_planned_bars
        from monostudio.core.models import ProjectIndex

        idx = ProjectIndex(
            root=self._project_root,
            assets=(ref,) if kind == "asset" else (),
            shots=(ref,) if kind == "shot" else (),
        )
        built = build_planned_bars(
            self._project_root,
            idx,
            schedule,
            include_shots=kind == "shot",
            include_assets=kind == "asset",
        )
        return sorted(
            [b for k, b in built.items() if k[0] == kind and k[1] == rel_norm],
            key=lambda b: b.due,
        )

    def _render_dept_bar(self, bar: PlannedBar, today: date) -> None:
        self._focus_bar = bar
        self._focus_kind = bar.entity_kind
        self._focus_rel = bar.entity_rel.replace("\\", "/")
        label = self._dept_labels.get(bar.department, bar.department_label or bar.department)
        self._dept_subcard_title.setText(label.upper())
        self._dept_status_panel.apply(bar, today)

        self._assignee_subcard.setVisible(True)
        self._assignee_sync = True
        try:
            self._assignee_picker.set_workspace_root(self._workspace_root)
            from monostudio.core.user_identity import match_roster_user_by_name, normalize_assignee_ids

            ids = list(bar.assignee_ids)
            if not ids and (bar.assignee_id or "").strip():
                ids = [(bar.assignee_id or "").strip()]
            elif not ids and (bar.assignee or "").strip():
                matched = match_roster_user_by_name(self._workspace_root, bar.assignee)
                if matched:
                    ids = [matched.id]
            self._assignee_picker.set_user_ids(normalize_assignee_ids(ids))
        finally:
            self._assignee_sync = False
        self._sync_assign_confirm_banner(bar)

    def _show_no_plan(self) -> None:
        self._dept_subcard.setVisible(False)
        self._assignee_subcard.setVisible(False)
        self._dept_hint_block.setVisible(False)
        self._dept_empty_block.setVisible(False)
        self._empty_block.setVisible(True)
        self._focus_bar = None
        self._btn_edit.setEnabled(False)

    def _clear(self) -> None:
        self._show_no_plan()
