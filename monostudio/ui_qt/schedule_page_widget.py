"""Schedule page: deadline allocation Gantt timeline."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.schedule_document import (
    ScheduleDocument,
    activate_schedule_document,
    schedule_document_for_root,
)
from monostudio.core.fs_reader import ProjectIndex
from monostudio.core.project_schedule import (
    ProjectSchedule,
    ScheduleAllocation,
    TimelineRow,
    allocation_for_row,
    clear_entity_schedule,
    read_project_schedule,
)
from monostudio.core.schedule_planner import build_planned_bars, count_overdue_bars
from monostudio.ui_qt.schedule_allocate_dialog import ScheduleAllocateDialog, _EntityOption
from monostudio.ui_qt.schedule_autoplan_dialog import ScheduleAutoPlanDialog
from monostudio.ui_qt.schedule_milestone_dialog import ScheduleMilestoneDialog
from monostudio.ui_qt.schedule_plan_dialog import SchedulePlanDialog
from monostudio.ui_qt.schedule_template_dialog import ScheduleTemplateDialog
from monostudio.core.schedule_date_display import (
    DATE_FMT_DEFAULT,
    SCHEDULE_DATE_FORMAT_KEY,
    normalize_date_display_format,
)
from monostudio.core.schedule_dept_filter import (
    BAR_LABEL_DATE_RANGE,
    BAR_LABEL_DEFAULT,
    DEPT_SCOPE_LEAF,
    SCHEDULE_BAR_LABEL_KEY,
    SCHEDULE_RESPECT_HIDDEN_KEY,
    load_inspector_hidden_departments,
    normalize_bar_label_mode,
)
from monostudio.ui_qt.schedule_legend_widget import ScheduleLegendBar
from monostudio.ui_qt.schedule_timeline_widget import (
    TOOL_DRAW,
    TOOL_SELECT,
    VIEW_DEPARTMENT,
    VIEW_DEPT_WAVE,
    VIEW_ENTITY,
    WAVE_DRAW_DISTRIBUTE,
    WAVE_DRAW_FIRST_ONLY,
    WAVE_DRAW_SAME_DAYS,
    ScheduleGanttWidget,
)
from monostudio.ui_qt.schedule_view_options_popup import ScheduleViewOptionsPopup
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.toolbar_separators import add_widgets_with_icon_separators

# Debounced autosave: 30s after the last edit (only if still dirty).
_SCHEDULE_AUTOSAVE_DELAY_MS = 30_000


def _schedule_layout_pill(parent: QWidget, label: str, tooltip: str) -> QPushButton:
    btn = QPushButton(label, parent)
    btn.setObjectName("Tier3Pill")
    btn.setCheckable(True)
    btn.setFlat(True)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class SchedulePageWidget(QWidget):
    """Deadline allocation timeline — full-page schedule management."""

    schedule_changed = Signal()
    sidebar_department_sync_requested = Signal(object)  # str | None — wave drilldown → sidebar highlight
    entity_row_selected = Signal(str, str)  # entity_kind, entity_rel → Inspector
    entity_row_cleared = Signal()  # deselect timeline row / Inspector
    department_skip_toggle_requested = Signal(str, str, str, bool)  # kind, rel, dep, skip

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._project_index: ProjectIndex | None = None
        self._schedule: ProjectSchedule | None = None
        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._filter_department: str | None = None
        self._filter_type: str | None = None
        self._filter_type_aliases: set[str] = set()
        self._include_shots = True
        self._include_assets = False
        self._schedule_doc: ScheduleDocument | None = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._on_autosave_tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # --- Page header: title + last-saved status (left) | view toggle + options (right) ---
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Schedule", self)
        title.setFont(monos_font("Inter", 20, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {MONOS_COLORS.get('text_primary', '#fafafa')};")
        header.addWidget(title, 0)

        self._saved_check = QLabel(self)
        self._saved_check.setFixedSize(16, 16)
        self._saved_check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._saved_check.setPixmap(
            lucide_icon("circle-check", size=14, color_hex="#10b981").pixmap(14, 14)
        )
        header.addWidget(self._saved_check, 0, Qt.AlignmentFlag.AlignVCenter)

        self._last_saved_label = QLabel("", self)
        self._last_saved_label.setObjectName("DialogHint")
        header.addWidget(self._last_saved_label, 0, Qt.AlignmentFlag.AlignVCenter)

        header.addStretch(1)

        # Layout toggle (Items / Lanes / Wave)
        self._layout_toggle = layout_toggle = QWidget(self)
        layout_toggle.setObjectName("Tier3Container")
        layout_toggle.setAttribute(Qt.WA_StyledBackground, True)
        layout_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout_lay = QHBoxLayout(layout_toggle)
        layout_lay.setContentsMargins(6, 6, 6, 6)
        layout_lay.setSpacing(4)

        self._btn_view_entity = _schedule_layout_pill(
            layout_toggle,
            "Items",
            "Per shot or asset — one row per item, department bars stacked (Tab)",
        )
        self._btn_view_dept_lanes = _schedule_layout_pill(
            layout_toggle,
            "Lanes",
            "Per department — one swimlane per dept, items listed inside (Tab)",
        )
        self._btn_view_dept_wave = _schedule_layout_pill(
            layout_toggle,
            "Wave",
            "Per department — one aggregated bar across all items in that dept (Tab)",
        )
        self._btn_view_entity.setChecked(True)
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self._btn_view_entity, 0)
        self._view_group.addButton(self._btn_view_dept_lanes, 1)
        self._view_group.addButton(self._btn_view_dept_wave, 2)
        self._view_group.idClicked.connect(self._on_view_clicked)
        add_widgets_with_icon_separators(
            layout_lay,
            [self._btn_view_entity, self._btn_view_dept_lanes, self._btn_view_dept_wave],
            layout_toggle,
            sep_height=18,
        )
        header.addWidget(layout_toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        root.addLayout(header)

        # Active timeline tool (Select / Draw) — shown via right-click tools menu.
        self._current_tool = TOOL_SELECT

        self._view_options = ScheduleViewOptionsPopup(self)
        self._view_options.wave_draw_mode.addItem("Same days for all", WAVE_DRAW_SAME_DAYS)
        self._view_options.wave_draw_mode.addItem("Distribute evenly", WAVE_DRAW_DISTRIBUTE)
        self._view_options.wave_draw_mode.addItem("First item only", WAVE_DRAW_FIRST_ONLY)
        self._view_options.chk_respect_hidden.toggled.connect(self._on_dept_display_changed)
        self._view_options.chk_unscheduled.toggled.connect(self._on_filter_changed)
        self._view_options.chk_overdue.toggled.connect(self._on_filter_changed)
        self._view_options.wave_draw_mode.currentIndexChanged.connect(self._on_wave_draw_mode_changed)
        self._view_options.bar_label_mode.currentIndexChanged.connect(self._on_bar_label_mode_changed)
        self._view_options.date_display_format.currentIndexChanged.connect(
            self._on_date_display_format_changed
        )

        # --- Summary stats (icon cards) ---
        summary = QHBoxLayout()
        summary.setSpacing(8)
        self._stat_alloc = self._make_stat(
            "Targets", self, icon_name="timer", accent="#60a5fa"
        )
        self._stat_pinned = self._make_stat(
            "Pinned", self, icon_name="pin", accent="#a855f7"
        )
        self._stat_milestones = self._make_stat(
            "Milestones", self, icon_name="flag", accent="#10b981"
        )
        self._stat_overdue = self._make_stat(
            "Overdue", self, icon_name="power", accent="#ef4444"
        )
        summary.addWidget(self._stat_alloc, 1)
        summary.addWidget(self._stat_pinned, 1)
        summary.addWidget(self._stat_milestones, 1)
        summary.addWidget(self._stat_overdue, 1)
        root.addLayout(summary)

        self._legend = ScheduleLegendBar(self)
        root.addWidget(self._legend)

        self._empty = QLabel(
            "Select a project using the project switcher in the sidebar.",
            self,
        )
        self._empty.setWordWrap(True)
        self._empty.setObjectName("DialogHint")
        root.addWidget(self._empty)

        self._gantt = ScheduleGanttWidget(self)
        self._gantt.schedule_changed.connect(self._on_schedule_changed)
        self._gantt.new_allocation_requested.connect(self._open_allocate_for_row)
        self._gantt.edit_allocation_requested.connect(self._open_allocate_for_existing)
        self._gantt.wave_drilldown_requested.connect(self._on_wave_drilldown)
        self._gantt.entity_plan_requested.connect(self._on_entity_plan_requested)
        self._gantt.entity_clear_plan_requested.connect(self._on_entity_clear_plan)
        self._gantt.entity_row_selected.connect(self.entity_row_selected.emit)
        self._gantt.entity_row_cleared.connect(self.entity_row_cleared.emit)
        self._gantt.department_skip_toggle_requested.connect(
            self.department_skip_toggle_requested.emit
        )
        self._gantt.search_filter_requested.connect(self._toggle_view_options_popup)
        self._gantt.tools_menu_requested.connect(self._show_tools_menu)
        root.addWidget(self._gantt, 1)
        self._install_schedule_shortcuts()

        self._load_view_options_settings()
        self._sync_dept_display_to_gantt(reload_now=False)
        self._on_wave_draw_mode_changed()
        self._on_bar_label_mode_changed()
        self._on_date_display_format_changed()

        self._content = (
            self._saved_check,
            self._last_saved_label,
            self._layout_toggle,
            self._legend,
            self._stat_alloc,
            self._stat_pinned,
            self._stat_milestones,
            self._stat_overdue,
            self._gantt,
        )
        self._sync_wave_draw_controls()
        self._sync_save_status()

    @property
    def _chk_respect_hidden(self):
        return self._view_options.chk_respect_hidden

    @property
    def _chk_unscheduled(self):
        return self._view_options.chk_unscheduled

    @property
    def _chk_overdue(self):
        return self._view_options.chk_overdue

    @property
    def _wave_draw_mode(self):
        return self._view_options.wave_draw_mode

    @property
    def _bar_label_mode(self):
        return self._view_options.bar_label_mode

    @property
    def _date_display_format(self):
        return self._view_options.date_display_format

    def _toggle_view_options_popup(self) -> None:
        self._sync_wave_draw_controls()
        self._sync_bar_label_controls()
        self._view_options.toggle_below(self._gantt.view_options_anchor())

    def _install_schedule_shortcuts(self) -> None:
        """Shortcuts on the gantt tree so they do not fire inside modal dialogs on the page."""
        gantt = self._gantt
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut

        def bind(seq, slot) -> None:
            sc = QShortcut(QKeySequence(seq), gantt, slot)
            sc.setContext(ctx)

        bind("Q", self._activate_select_tool)
        bind("W", self._activate_draw_tool)
        bind("F", self._on_today_clicked)
        bind(QKeySequence.StandardKey.Undo, self._on_undo_clicked)
        bind("Ctrl+Shift+Z", self._on_redo_clicked)
        bind(QKeySequence.StandardKey.Save, self._on_save_clicked)
        tab_sc = QShortcut(QKeySequence(Qt.Key.Key_Tab), gantt, self._cycle_layout_view)
        tab_sc.setContext(ctx)
        tab_sc.setAutoRepeat(False)

    def _activate_select_tool(self) -> None:
        self._current_tool = TOOL_SELECT
        self._gantt.set_tool(TOOL_SELECT)
        self._sync_wave_draw_controls()

    def _activate_draw_tool(self) -> None:
        self._current_tool = TOOL_DRAW
        self._gantt.set_tool(TOOL_DRAW)
        self._sync_wave_draw_controls()

    def _show_tools_menu(self, global_pos) -> None:
        """Right-click on empty timeline → Tools + Planning context menu."""
        from PySide6.QtWidgets import QMenu

        doc = self._schedule_doc
        can_undo = bool(doc and doc.can_undo())
        can_redo = bool(doc and doc.can_redo())
        dirty = bool(doc and doc.is_dirty)
        label_color = MONOS_COLORS["text_label"]

        def act(menu, icon_name, text):
            return menu.addAction(lucide_icon(icon_name, size=16, color_hex=label_color), text)

        menu = QMenu(self)
        sel = act(menu, "hand", "Select — move and resize bars")
        sel.setCheckable(True)
        sel.setChecked(self._current_tool == TOOL_SELECT)
        draw = act(menu, "pencil", "Draw bar — drag to pin dates")
        draw.setCheckable(True)
        draw.setChecked(self._current_tool == TOOL_DRAW)
        menu.addSeparator()
        today = act(menu, "scan", "Focus today")
        menu.addSeparator()
        undo = act(menu, "undo-2", "Undo")
        undo.setEnabled(can_undo)
        redo = act(menu, "redo-2", "Redo")
        redo.setEnabled(can_redo)
        save = act(menu, "save", "Save now")
        save.setEnabled(dirty)
        menu.addSeparator()
        tpl = act(menu, "file-text", "Edit templates…")
        auto = act(menu, "sparkles", "Auto-plan…")
        mile = act(menu, "pin", "Project milestones…")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is sel:
            self._activate_select_tool()
        elif chosen is draw:
            self._activate_draw_tool()
        elif chosen is today:
            self._on_today_clicked()
        elif chosen is undo:
            self._on_undo_clicked()
        elif chosen is redo:
            self._on_redo_clicked()
        elif chosen is save:
            self._on_save_clicked()
        elif chosen is tpl:
            self._on_template_clicked()
        elif chosen is auto:
            self._on_autoplan_clicked()
        elif chosen is mile:
            self._on_milestone_clicked()

    def _cycle_layout_view(self) -> None:
        if self._btn_view_entity.isChecked():
            self._btn_view_dept_lanes.setChecked(True)
            view_id = 1
        elif self._btn_view_dept_lanes.isChecked():
            self._btn_view_dept_wave.setChecked(True)
            view_id = 2
        else:
            self._btn_view_entity.setChecked(True)
            view_id = 0
        self._on_view_clicked(view_id)

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    @classmethod
    def _make_stat(
        cls, label: str, parent: QWidget, *, icon_name: str, accent: str
    ) -> QFrame:
        frame = QFrame(parent)
        frame.setObjectName("DashboardMetricTile")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        r, g, b = cls._hex_to_rgb(accent)
        chip = QLabel(frame)
        chip.setFixedSize(40, 40)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setStyleSheet(
            f"background-color: rgba({r}, {g}, {b}, 0.14); border-radius: 10px;"
        )
        chip.setPixmap(lucide_icon(icon_name, size=18, color_hex=accent).pixmap(18, 18))
        lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        value = QLabel("—", frame)
        value.setFont(monos_font("Inter", 18, QFont.Weight.Bold))
        cap = QLabel(label.upper(), frame)
        cap.setObjectName("DashboardTileLabel")
        text_col.addWidget(value)
        text_col.addWidget(cap)
        lay.addLayout(text_col, 1)
        frame._value_label = value  # type: ignore[attr-defined]
        return frame

    def set_project_root(self, path: Path | None) -> None:
        new_root = Path(path) if path else None
        if self._project_root is not None and new_root != self._project_root:
            self._teardown_schedule_document()
        self._project_root = new_root

    def flush_schedule_document(self) -> None:
        """Persist pending schedule edits (e.g. before closing the project)."""
        self._teardown_schedule_document()

    def set_thumbnail_manager(self, manager) -> None:
        self._gantt.set_thumbnail_manager(manager)

    def refresh_row_thumbnails(self, paths: list | object = ()) -> None:
        self._gantt.refresh_thumbnails_for_paths(paths)
        self._gantt.prefetch_entity_thumbnails()

    def refresh(self, project_index: ProjectIndex | None) -> None:
        self._project_index = project_index
        if self._project_root is None:
            self._teardown_schedule_document()
            self._schedule = None
            self._empty.setVisible(True)
            for w in self._content:
                w.setVisible(False)
            self._gantt.set_project(None, None)
            return

        self._ensure_schedule_document()

        self._empty.setVisible(False)
        for w in self._content:
            w.setVisible(True)

        self._gantt.set_include_shots(self._include_shots)
        self._gantt.set_include_assets(self._include_assets)
        self._sync_dept_display_to_gantt(reload_now=False)
        self._gantt.set_project(self._project_root, project_index)
        self._apply_filters()
        self._update_stats()
        self._sync_save_status()

    def _ensure_schedule_document(self) -> None:
        if self._project_root is None:
            return
        root = self._project_root.resolve()
        doc = schedule_document_for_root(root)
        if doc is not None and doc is self._schedule_doc:
            return
        self._teardown_schedule_document()
        self._schedule_doc = ScheduleDocument(root)
        self._schedule_doc.load_from_disk()
        activate_schedule_document(self._schedule_doc)

    def _schedule_autosave_debounce(self) -> None:
        """Restart 30s countdown after each edit while dirty."""
        if self._schedule_doc is None or not self._schedule_doc.is_dirty:
            self._autosave_timer.stop()
            return
        self._autosave_timer.start(_SCHEDULE_AUTOSAVE_DELAY_MS)

    def _teardown_schedule_document(self, *, save: bool = True) -> None:
        self._autosave_timer.stop()
        if save and self._schedule_doc is not None:
            self._schedule_doc.save_if_dirty()
        if self._schedule_doc is not None:
            activate_schedule_document(None)
        self._schedule_doc = None

    def _on_autosave_tick(self) -> None:
        if self._schedule_doc is None:
            return
        if self._schedule_doc.save_if_dirty():
            self._sync_save_status()

    def _on_undo_clicked(self) -> None:
        if self._schedule_doc is None or not self._schedule_doc.undo():
            return
        self._after_history_change()

    def _on_redo_clicked(self) -> None:
        if self._schedule_doc is None or not self._schedule_doc.redo():
            return
        self._after_history_change()

    def _on_save_clicked(self) -> None:
        if self._schedule_doc is None:
            return
        self._schedule_doc.save_now()
        self._autosave_timer.stop()
        self._sync_save_status()

    def _after_history_change(self) -> None:
        self._gantt.reload()
        self._on_schedule_changed()
        self._sync_save_status()

    @staticmethod
    def _format_last_saved(doc: ScheduleDocument | None) -> str:
        if doc is None:
            return ""
        if doc.is_dirty:
            return "Unsaved changes"
        at = doc.last_saved_at
        if at is None:
            return "Not saved yet"
        now = datetime.now()
        if at.date() == now.date():
            return f"Last saved today, {at.strftime('%H:%M')}"
        yesterday = now.date().fromordinal(now.date().toordinal() - 1)
        if at.date() == yesterday:
            return f"Last saved yesterday, {at.strftime('%H:%M')}"
        return f"Last saved {at.strftime('%Y-%m-%d %H:%M')}"

    def _sync_save_status(self) -> None:
        doc = self._schedule_doc
        if hasattr(self, "_last_saved_label"):
            self._last_saved_label.setText(self._format_last_saved(doc))
        if hasattr(self, "_saved_check"):
            saved_ok = bool(doc and not doc.is_dirty and doc.last_saved_at is not None)
            self._saved_check.setVisible(saved_ok and self._gantt.isVisible())

    def sync_sidebar_filters(
        self,
        *,
        include_shots: bool,
        include_assets: bool,
        department: str | None,
        type_id: str | None,
        type_aliases: set[str] | None = None,
        allowed_department_ids: list[str] | None = None,
    ) -> None:
        """Apply filter panel state from sidebar (Schedule page)."""
        self._include_shots = bool(include_shots)
        self._include_assets = bool(include_assets)
        if not self._include_shots and not self._include_assets:
            self._include_shots = True
        self._filter_department = (department or "").strip() or None
        self._filter_type = (type_id or "").strip() or None
        self._filter_type_aliases = {x.casefold() for x in (type_aliases or set()) if x}
        if self._filter_type and not self._filter_type_aliases:
            self._filter_type_aliases = {self._filter_type.casefold()}
        if self._project_index is None:
            return
        self._gantt.set_include_shots(self._include_shots)
        self._gantt.set_include_assets(self._include_assets)
        self._apply_filters(allowed_department_ids=allowed_department_ids)

    def _load_view_options_settings(self) -> None:
        respect = bool(
            self._settings.value(SCHEDULE_RESPECT_HIDDEN_KEY, True, type=bool)
        )
        self._chk_respect_hidden.blockSignals(True)
        try:
            self._chk_respect_hidden.setChecked(respect)
        finally:
            self._chk_respect_hidden.blockSignals(False)

        saved_label = normalize_bar_label_mode(
            str(self._settings.value(SCHEDULE_BAR_LABEL_KEY, BAR_LABEL_DEFAULT) or "")
        )
        combo = self._bar_label_mode
        combo.blockSignals(True)
        try:
            for i in range(combo.count()):
                if combo.itemData(i, Qt.ItemDataRole.UserRole) == saved_label:
                    combo.setCurrentIndex(i)
                    break
        finally:
            combo.blockSignals(False)

        saved_fmt = normalize_date_display_format(
            str(self._settings.value(SCHEDULE_DATE_FORMAT_KEY, DATE_FMT_DEFAULT) or "")
        )
        fmt_combo = self._date_display_format
        fmt_combo.blockSignals(True)
        try:
            for i in range(fmt_combo.count()):
                if fmt_combo.itemData(i, Qt.ItemDataRole.UserRole) == saved_fmt:
                    fmt_combo.setCurrentIndex(i)
                    break
        finally:
            fmt_combo.blockSignals(False)

    def _save_dept_display_settings(self) -> None:
        self._settings.setValue(SCHEDULE_RESPECT_HIDDEN_KEY, self._chk_respect_hidden.isChecked())

    def _save_bar_label_settings(self) -> None:
        mode = self._bar_label_mode.currentData(Qt.ItemDataRole.UserRole)
        if mode is not None:
            self._settings.setValue(
                SCHEDULE_BAR_LABEL_KEY,
                normalize_bar_label_mode(str(mode)),
            )

    def _sync_dept_display_to_gantt(self, *, reload_now: bool = True) -> None:
        gantt = getattr(self, "_gantt", None)
        if gantt is None:
            return
        hidden = load_inspector_hidden_departments(self._settings)
        gantt.set_dept_display(
            hidden_departments=hidden,
            respect_inspector_hidden=self._chk_respect_hidden.isChecked(),
            dept_scope=DEPT_SCOPE_LEAF,
            wave_group_by_parent=False,
            reload_now=reload_now,
        )

    def set_inspector_hidden_departments(self, hidden: set[str]) -> None:
        if not self._chk_respect_hidden.isChecked():
            return
        self._gantt.set_inspector_hidden_departments(hidden)

    def _on_dept_display_changed(self) -> None:
        self._save_dept_display_settings()
        self._sync_dept_display_to_gantt()
        self._apply_filters()

    def _apply_filters(self, *, allowed_department_ids: list[str] | None = None) -> None:
        self._gantt.apply_filters(
            dept_filter=self._filter_department,
            unscheduled_only=self._chk_unscheduled.isChecked(),
            overdue_only=self._chk_overdue.isChecked(),
            type_filter=self._filter_type,
            type_aliases=self._filter_type_aliases,
            allowed_department_ids=allowed_department_ids,
        )

    def _update_stats(self) -> None:
        if self._project_root is None:
            return
        schedule = read_project_schedule(self._project_root)
        self._schedule = schedule
        self._stat_alloc._value_label.setText(str(len(schedule.targets)))  # type: ignore[attr-defined]
        self._stat_pinned._value_label.setText(
            str(len(schedule.allocations) + len(schedule.waves))
        )  # type: ignore[attr-defined]
        self._stat_milestones._value_label.setText(str(len(schedule.milestones)))  # type: ignore[attr-defined]
        overdue = 0
        if self._project_index is not None:
            bars = build_planned_bars(
                self._project_root,
                self._project_index,
                schedule,
                include_shots=self._include_shots,
                include_assets=self._include_assets,
            )
            overdue = count_overdue_bars(bars)
        self._stat_overdue._value_label.setText(str(overdue))  # type: ignore[attr-defined]

    def reveal_entity(
        self,
        entity_kind: str,
        entity_rel: str,
        *,
        department: str | None = None,
        due: date | None = None,
    ) -> bool:
        """Scroll timeline to entity/dept, highlight rows, emit selection for Inspector."""
        return self._gantt.reveal_entity(
            entity_kind,
            entity_rel,
            department=department,
            due=due,
        )

    def focus_unscheduled_entities(self, entities: list[tuple[str, str]]) -> bool:
        """Open Schedule filter + highlight all unscheduled entities from Dashboard."""
        if not entities:
            return False
        chk = self._view_options.chk_unscheduled
        if not chk.isChecked():
            chk.blockSignals(True)
            try:
                chk.setChecked(True)
            finally:
                chk.blockSignals(False)
            self._apply_filters()
        self._gantt.highlight_entities(entities, expand_entity_rows=True)
        self._gantt.scroll_to_entity_keys(entities)
        kind, rel = entities[0]
        self._gantt.entity_row_selected.emit(kind, rel)
        return True

    def _on_today_clicked(self) -> None:
        self._gantt.scroll_to_today()

    def _on_wave_draw_mode_changed(self) -> None:
        mode = self._wave_draw_mode.currentData()
        if isinstance(mode, str):
            self._gantt.set_wave_draw_apply_mode(mode)

    def _on_bar_label_mode_changed(self) -> None:
        mode = self._bar_label_mode.currentData(Qt.ItemDataRole.UserRole)
        self._gantt.set_bar_label_mode(normalize_bar_label_mode(str(mode) if mode else None))
        self._save_bar_label_settings()
        self._sync_bar_label_controls()

    def _on_date_display_format_changed(self) -> None:
        fmt = self._date_display_format.currentData(Qt.ItemDataRole.UserRole)
        self._gantt.set_date_display_format(normalize_date_display_format(str(fmt) if fmt else None))
        self._save_date_format_settings()

    def _save_date_format_settings(self) -> None:
        fmt = self._date_display_format.currentData(Qt.ItemDataRole.UserRole)
        if fmt is not None:
            self._settings.setValue(
                SCHEDULE_DATE_FORMAT_KEY,
                normalize_date_display_format(str(fmt)),
            )

    def _sync_bar_label_controls(self) -> None:
        mode = self._bar_label_mode.currentData(Qt.ItemDataRole.UserRole)
        show_date_fmt = mode == BAR_LABEL_DATE_RANGE
        self._view_options.set_date_format_visible(show_date_fmt)

    def _sync_wave_draw_controls(self) -> None:
        wave_view = self._btn_view_dept_wave.isChecked()
        draw_tool = self._current_tool == TOOL_DRAW
        show = wave_view and draw_tool
        self._view_options.set_wave_draw_visible(show)
        if show:
            mode = self._wave_draw_mode.currentData()
            if isinstance(mode, str):
                self._gantt.set_wave_draw_apply_mode(mode)

    def _on_view_clicked(self, view_id: int) -> None:
        if view_id == 1:
            mode = VIEW_DEPARTMENT
        elif view_id == 2:
            mode = VIEW_DEPT_WAVE
        else:
            mode = VIEW_ENTITY
        self._gantt.set_view_mode(mode)
        self._sync_wave_draw_controls()

    def _on_wave_drilldown(self, department: str) -> None:
        dep = (department or "").strip()
        if not dep:
            return
        self._filter_department = dep
        self.sidebar_department_sync_requested.emit(dep)
        self._apply_filters()
        self._btn_view_dept_lanes.setChecked(True)
        self._gantt.set_view_mode(VIEW_DEPARTMENT)
        self._sync_wave_draw_controls()

    def _on_filter_changed(self) -> None:
        self._apply_filters()

    def _on_schedule_changed(self) -> None:
        self._update_stats()
        self._sync_save_status()
        self._schedule_autosave_debounce()
        self.schedule_changed.emit()

    def _on_entity_plan_requested(self, entity_kind: str, entity_rel: str) -> None:
        if self._project_root is None:
            return
        rel = (entity_rel or "").replace("\\", "/")
        kind = (entity_kind or "").strip().lower()
        matches = [
            e
            for e in self._entity_options()
            if e.kind == kind and e.rel.replace("\\", "/") == rel
        ]
        if not matches:
            return
        dlg = SchedulePlanDialog(
            parent=self,
            project_root=self._project_root,
            entities=matches,
            dept_labels=self._dept_labels(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._on_schedule_changed()
        self._gantt.reload()

    def _on_entity_clear_plan(self, entity_kind: str, entity_rel: str) -> None:
        if self._project_root is None:
            return
        ok = QMessageBox.question(
            self,
            "Clear plan",
            "Remove delivery target, waves, and pinned bars for this item?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_entity_schedule(
                self._project_root,
                entity_kind=entity_kind,
                entity_rel=entity_rel,
            )
        except OSError:
            return
        self._on_schedule_changed()
        self._gantt.reload()

    def _on_template_clicked(self) -> None:
        if self._project_root is None:
            return
        dlg = ScheduleTemplateDialog(
            parent=self,
            project_root=self._project_root,
            dept_labels=self._dept_labels(),
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_schedule_changed()
            self._gantt.reload()

    def _on_autoplan_clicked(self) -> None:
        if self._project_root is None or self._project_index is None:
            return
        dlg = ScheduleAutoPlanDialog(
            parent=self,
            project_root=self._project_root,
            entities=self._entity_options(),
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_schedule_changed()
            self._gantt.reload()

    def _on_milestone_clicked(self) -> None:
        if self._project_root is None:
            return
        dlg = ScheduleMilestoneDialog(parent=self, project_root=self._project_root)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_schedule_changed()
            self._gantt.reload()

    def _dept_labels(self) -> dict[str, str]:
        if self._project_root is None:
            return {}
        reg = DepartmentRegistry.for_project(self._project_root)
        return {d: reg.get_department_label(d) or d for d in reg.get_departments()}

    def _entity_options(self) -> list[_EntityOption]:
        if self._project_index is None:
            return []
        from monostudio.ui_qt.schedule_autoplan_dialog import entity_options_from_index

        return entity_options_from_index(
            self._project_index,
            include_shots=self._include_shots,
            include_assets=self._include_assets,
        )

    def _open_allocate_for_row(self, row: TimelineRow) -> None:
        if self._project_root is None:
            return
        preset_start: str | None = None
        preset_due: str | None = None
        dates = self._gantt.planned_dates_for_row(row)
        if dates is not None:
            preset_start, preset_due = dates
        dlg = ScheduleAllocateDialog(
            parent=self,
            project_root=self._project_root,
            entities=self._entity_options(),
            dept_labels=self._dept_labels(),
            preset_kind=row.entity_kind,
            preset_rel=row.entity_rel,
            preset_department=row.department,
            preset_start=preset_start,
            preset_due=preset_due,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_schedule_changed()
            self._gantt.reload()

    def _open_allocate_for_existing(self, alloc: ScheduleAllocation) -> None:
        if self._project_root is None:
            return
        schedule = read_project_schedule(self._project_root)
        rel = (alloc.entity_rel or "").replace("\\", "/").strip()
        existing = allocation_for_row(
            schedule,
            entity_kind=alloc.entity_kind,
            entity_rel=rel,
            department=alloc.department,
        )
        dlg = ScheduleAllocateDialog(
            parent=self,
            project_root=self._project_root,
            entities=self._entity_options(),
            dept_labels=self._dept_labels(),
            existing=existing or alloc,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_schedule_changed()
            self._gantt.reload()
