"""
Schedule planner: derive per-department bars from delivery targets + pipeline
templates (backward planning), then overlay manual overrides and real production
status read from the project index.

The planner never persists computed bars; only targets/templates/overrides live in
schedule.json. Bars are recomputed on the fly so they stay in sync with both the
plan and actual publish progress.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from monostudio.core.models import Asset, ProjectIndex, Shot
from monostudio.core.department_status_registry import (
    goal_is_met,
    load_status_registry_for_department,
    status_workflow_order,
)
from monostudio.core.production_status import (
    CATEGORY_COLOR_HEX,
    SKIPPED_STATUS_ID,
    computed_status_id_from_department,
    effective_status_id_for_department,
    load_production_status_registry,
    override_status_id_for_department,
)
from monostudio.core.project_schedule import (
    DEFAULT_STEP_DAYS,
    DEFAULT_TEMPLATE_NAME,
    ProjectSchedule,
    ScheduleTemplateStep,
    _parse_date,
    bar_dates_for_wave_due,
    default_template_for_project,
    entity_rel_path,
)

# Coarse buckets used for bar styling.
STATUS_DONE = "done"
STATUS_PROGRESS = "progress"
STATUS_WAITING = "waiting"
STATUS_EXCLUDED = "excluded"  # na / Skipped — out of completion & overdue rollups

AUTO_BAR_ID = "__auto__"
WAVE_BAR_ID = "__wave__"

BarStoreKey = tuple[str, str, str, str]
BarStore = dict[BarStoreKey, "PlannedBar"]


@dataclass(frozen=True)
class PlannedBar:
    entity_kind: str
    entity_rel: str
    entity_name: str
    department: str
    department_label: str
    start: date
    due: date
    source: str  # "auto" | "wave" | "override"
    status: str  # done | progress | waiting | excluded
    status_id: str
    color_hex: str
    overdue: bool
    assignee_ids: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    assignee_id: str = ""
    assignee: str = ""
    note: str = ""
    allocation_id: str | None = None  # set when this bar comes from a stored override
    target_status_id: str = ""
    target_status_label: str = ""
    goal_met: bool = False
    target_workflow_order: int = 0
    bar_id: str = ""

    @property
    def row_key(self) -> tuple[str, str, str]:
        return (self.entity_kind, self.entity_rel.replace("\\", "/"), self.department)

    @property
    def store_key(self) -> BarStoreKey:
        bid = (self.bar_id or self.allocation_id or AUTO_BAR_ID).strip() or AUTO_BAR_ID
        return bar_store_key(self.entity_kind, self.entity_rel, self.department, bid)


def bar_store_key(
    entity_kind: str,
    entity_rel: str,
    department: str,
    bar_id: str,
) -> BarStoreKey:
    return (
        _norm_entity_kind(entity_kind),
        _norm_entity_rel(entity_rel),
        (department or "").strip(),
        (bar_id or "").strip() or AUTO_BAR_ID,
    )


def bars_for_row(
    bars: BarStore,
    entity_kind: str,
    entity_rel: str,
    department: str,
) -> list[PlannedBar]:
    kind = _norm_entity_kind(entity_kind)
    rel = _norm_entity_rel(entity_rel)
    dep = (department or "").strip()
    found = [
        b
        for key, b in bars.items()
        if key[0] == kind and key[1] == rel and key[2] == dep
    ]
    found.sort(key=lambda b: (b.target_workflow_order, b.due, b.start, b.bar_id))
    return found


def merged_row_assignee_ids_from_bars(
    bars: BarStore,
    entity_kind: str,
    entity_rel: str,
    department: str,
) -> tuple[str, ...]:
    """Union of assignee ids across every planned bar in an entity department row."""
    from monostudio.core.user_identity import normalize_assignee_ids

    merged: list[str] = []
    seen: set[str] = set()
    for bar in bars_for_row(bars, entity_kind, entity_rel, department):
        raw: list[str] = list(bar.assignee_ids)
        if not raw and (bar.assignee_id or "").strip():
            raw = [(bar.assignee_id or "").strip()]
        for uid in normalize_assignee_ids(raw):
            if uid not in seen:
                seen.add(uid)
                merged.append(uid)
    return tuple(merged)


def primary_bar_for_row(
    bars: BarStore,
    entity_kind: str,
    entity_rel: str,
    department: str,
) -> PlannedBar | None:
    row = bars_for_row(bars, entity_kind, entity_rel, department)
    if not row:
        return None
    unmet = [b for b in row if not b.goal_met and b.status != STATUS_EXCLUDED]
    if unmet:
        return unmet[0]
    return row[0]


def get_bar(
    bars: BarStore,
    entity_kind: str,
    entity_rel: str,
    department: str,
    bar_id: str = AUTO_BAR_ID,
) -> PlannedBar | None:
    return bars.get(bar_store_key(entity_kind, entity_rel, department, bar_id))


def _bucket_for_category(category: str) -> str:
    if category == "na":
        return STATUS_EXCLUDED
    if category == "done":
        return STATUS_DONE
    if category in ("in_progress", "review"):
        return STATUS_PROGRESS
    return STATUS_WAITING


def _bar_is_excluded(bucket: str, status_id: str) -> bool:
    return bucket == STATUS_EXCLUDED or (status_id or "").strip() == SKIPPED_STATUS_ID


def _norm_entity_kind(kind: str) -> str:
    return (kind or "").strip().lower()


def _norm_entity_rel(rel: str) -> str:
    return (rel or "").replace("\\", "/").strip()


def _entity_index(project_index: ProjectIndex, root: Path) -> dict[tuple[str, str], Asset | Shot]:
    out: dict[tuple[str, str], Asset | Shot] = {}
    for asset in project_index.assets:
        out[("asset", _norm_entity_rel(entity_rel_path(root, asset.path)))] = asset
    for shot in project_index.shots:
        out[("shot", _norm_entity_rel(entity_rel_path(root, shot.path)))] = shot
    return out


def _ordered_template_steps(
    schedule: ProjectSchedule,
    template_name: str,
    project_root: Path,
) -> list[ScheduleTemplateStep]:
    steps = schedule.templates.get(template_name)
    if not steps:
        steps = schedule.templates.get(DEFAULT_TEMPLATE_NAME)
    if not steps:
        steps = default_template_for_project(project_root)
    return list(steps or [])


def _active_template_steps_for_entity(
    steps: list[ScheduleTemplateStep],
    entity_depts: set[str],
    dept_reg: object,
) -> list[ScheduleTemplateStep]:
    """
    Map template steps to departments that exist on the entity (shot/asset folders).

    When template dept ids do not match scan ids (legacy templates, parent-only steps),
    fall back to one backward step per on-disk department in registry order.
    """
    if not entity_depts:
        return list(steps)
    matched = [s for s in steps if s.dept in entity_depts]
    if matched:
        return matched
    order = {d: i for i, d in enumerate(dept_reg.get_departments())}  # type: ignore[union-attr]
    days_by_dept = {s.dept: max(1, int(s.days)) for s in steps}
    out: list[ScheduleTemplateStep] = []
    for dep in sorted(entity_depts, key=lambda d: order.get(d, 9999)):
        days = days_by_dept.get(dep)
        if days is None:
            parent = dept_reg.get_parent(dep)  # type: ignore[union-attr]
            while parent and days is None:
                days = days_by_dept.get(parent)
                parent = dept_reg.get_parent(parent)  # type: ignore[union-attr]
        out.append(ScheduleTemplateStep(dept=dep, days=days or DEFAULT_STEP_DAYS))
    return out


def build_planned_bars(
    project_root: Path,
    project_index: ProjectIndex,
    schedule: ProjectSchedule,
    *,
    include_shots: bool = True,
    include_assets: bool = False,
    today: date | None = None,
) -> BarStore:
    """Return computed bars keyed by (entity_kind, entity_rel, department, bar_id)."""
    from monostudio.core.department_registry import DepartmentRegistry

    root = Path(project_root)
    today = today or date.today()
    dept_reg = DepartmentRegistry.for_project(root)
    entities = _entity_index(project_index, root)

    bars: BarStore = {}
    suppressed_auto = set(schedule.auto_bar_suppressions or ())

    manual_goal_rows: set[tuple[str, str, str]] = set()
    for alloc in schedule.allocations:
        kind = _norm_entity_kind(alloc.entity_kind)
        dep_id = (alloc.department or "").strip()
        if dep_id:
            manual_goal_rows.add((kind, _norm_entity_rel(alloc.entity_rel), dep_id))

    def dept_label(dep_id: str) -> str:
        return dept_reg.get_department_label(dep_id) or dep_id

    def _kind_allowed(kind: str) -> bool:
        k = _norm_entity_kind(kind)
        return (k == "shot" and include_shots) or (k == "asset" and include_assets)

    def _dept_status_reg(dep_id: str):
        return load_status_registry_for_department(root, dep_id)

    def _status_for(
        ref: Asset | Shot | None, dep_id: str
    ) -> tuple[str, str, str, object]:
        """Return (bucket, status_id, color_hex, registry)."""
        reg = _dept_status_reg(dep_id)
        if ref is None:
            return STATUS_WAITING, "waiting", CATEGORY_COLOR_HEX["unknown"], reg
        dep = None
        for d in ref.departments:
            if (d.name or "").strip() == dep_id:
                dep = d
                break
        if dep is None:
            return STATUS_WAITING, "waiting", CATEGORY_COLOR_HEX["unknown"], reg
        override = override_status_id_for_department(ref, dep_id)
        sid = effective_status_id_for_department(dep, override, reg)
        category = reg.category_for(sid)
        bucket = _bucket_for_category(category)
        return bucket, sid, CATEGORY_COLOR_HEX.get(category, CATEGORY_COLOR_HEX["unknown"]), reg

    def _goal_bar_fields(
        ref: Asset | Shot | None,
        dep_id: str,
        target_status_id: str,
    ) -> tuple[bool, str, str, str, int]:
        bucket, current_sid, _, reg = _status_for(ref, dep_id)
        target = (target_status_id or "").strip()
        if not target:
            met = bucket == STATUS_DONE
            label = ""
            order = 0
            tcat = reg.category_for(current_sid)
            tcolor = CATEGORY_COLOR_HEX.get(tcat, CATEGORY_COLOR_HEX["unknown"])
            return met, label, tcolor, current_sid, order
        met = goal_is_met(current_sid, target, reg)
        label = reg.label_for(target)
        order = status_workflow_order(reg, target)
        tcat = reg.category_for(target)
        tcolor = CATEGORY_COLOR_HEX.get(tcat, CATEGORY_COLOR_HEX["unknown"])
        if met:
            bucket = STATUS_DONE
        return met, label, tcolor, current_sid, order

    def _store_bar(bar: PlannedBar) -> None:
        bars[bar.store_key] = bar

    # 1) Auto bars from delivery targets, backward-scheduled along the template.
    for target in schedule.targets:
        kind = _norm_entity_kind(target.entity_kind)
        if not _kind_allowed(kind):
            continue
        delivery = _parse_date(target.delivery)
        if not delivery:
            continue
        rel = _norm_entity_rel(target.entity_rel)
        ref = entities.get((kind, rel))
        entity_name = getattr(ref, "name", rel.rsplit("/", 1)[-1])
        entity_depts = (
            {(d.name or "").strip() for d in ref.departments if (d.name or "").strip()}
            if ref is not None
            else set()
        )
        steps = _ordered_template_steps(schedule, target.template, root)
        active_steps = _active_template_steps_for_entity(steps, entity_depts, dept_reg)

        end_cursor = delivery
        for step in reversed(active_steps):
            dep_step = (step.dept or "").strip()
            days = max(1, int(step.days))
            due = end_cursor
            start = due - timedelta(days=days - 1)
            if dep_step and (kind, rel, dep_step) in suppressed_auto:
                end_cursor = start - timedelta(days=1)
                continue
            if dep_step and (kind, rel, dep_step) in manual_goal_rows:
                end_cursor = start - timedelta(days=1)
                continue
            bucket, sid, color, _ = _status_for(ref, step.dept)
            if _bar_is_excluded(bucket, sid):
                end_cursor = start - timedelta(days=1)
                continue
            overdue = due < today and bucket != STATUS_DONE
            _store_bar(
                PlannedBar(
                    entity_kind=kind,
                    entity_rel=rel,
                    entity_name=entity_name,
                    department=step.dept,
                    department_label=dept_label(step.dept),
                    start=start,
                    due=due,
                    source="auto",
                    status=bucket,
                    status_id=sid,
                    color_hex=color,
                    overdue=overdue,
                    bar_id=AUTO_BAR_ID,
                )
            )
            end_cursor = start - timedelta(days=1)

    # 2) Department waves — replace auto bars for that entity + dept.
    for wave in schedule.waves:
        kind = _norm_entity_kind(wave.entity_kind)
        if not _kind_allowed(kind):
            continue
        dep_id = (wave.department or "").strip()
        if not dep_id:
            continue
        rel = _norm_entity_rel(wave.entity_rel)
        if (kind, rel, dep_id) in manual_goal_rows:
            continue
        due = _parse_date(wave.due)
        if not due:
            continue
        ref = entities.get((kind, rel))
        entity_name = getattr(ref, "name", rel.rsplit("/", 1)[-1])
        start, due_d = bar_dates_for_wave_due(
            schedule,
            template_name=wave.template,
            department=dep_id,
            due=due,
            project_root=root,
        )
        bucket, sid, color, _ = _status_for(ref, dep_id)
        excluded = _bar_is_excluded(bucket, sid)
        overdue = not excluded and due_d < today and bucket != STATUS_DONE
        _store_bar(
            PlannedBar(
                entity_kind=kind,
                entity_rel=rel,
                entity_name=entity_name,
                department=dep_id,
                department_label=dept_label(dep_id),
                start=start,
                due=due_d,
                source="wave",
                status=bucket,
                status_id=sid,
                color_hex=color,
                overdue=overdue,
                bar_id=WAVE_BAR_ID,
            )
        )

    # 3) Manual goal allocations (multiple per entity + department).
    for alloc in schedule.allocations:
        kind = _norm_entity_kind(alloc.entity_kind)
        if not _kind_allowed(kind):
            continue
        dep_id = (alloc.department or "").strip()
        if not dep_id:
            continue
        start = _parse_date(alloc.start)
        due = _parse_date(alloc.due)
        if not start or not due:
            continue
        rel = _norm_entity_rel(alloc.entity_rel)
        ref = entities.get((kind, rel))
        entity_name = getattr(ref, "name", rel.rsplit("/", 1)[-1])
        target_id = (alloc.target_status_id or "").strip()
        met, target_label, target_color, current_sid, order = _goal_bar_fields(
            ref, dep_id, target_id
        )
        bucket, _, _, _ = _status_for(ref, dep_id)
        if met:
            bucket = STATUS_DONE
        excluded = _bar_is_excluded(bucket, current_sid)
        overdue = not excluded and not met and due < today
        color = "#10b981" if met else ("#ef4444" if overdue else target_color)
        _store_bar(
            PlannedBar(
                entity_kind=kind,
                entity_rel=rel,
                entity_name=entity_name,
                department=dep_id,
                department_label=dept_label(dep_id),
                start=start,
                due=due,
                source="override",
                status=STATUS_DONE if met else bucket,
                status_id=current_sid,
                color_hex=color,
                overdue=overdue,
                assignee_ids=alloc.assignee_ids,
                assignees=alloc.assignees,
                assignee_id=alloc.assignee_id,
                assignee=alloc.assignee,
                note=alloc.note,
                allocation_id=alloc.id,
                target_status_id=target_id,
                target_status_label=target_label,
                goal_met=met,
                target_workflow_order=order,
                bar_id=alloc.id,
            )
        )

    return bars


def compute_view_date_range_from_bars(
    bars: BarStore,
    schedule: ProjectSchedule,
    *,
    project_root: Path | None = None,
    padding_days: int = 7,
    outside_padding_days: int = 14,
    default_span_days: int = 56,
) -> tuple[date, date]:
    from monostudio.core.project_schedule import compute_timeline_view_range

    return compute_timeline_view_range(
        schedule,
        bars,
        project_root=project_root,
        padding_days=padding_days,
        outside_padding_days=outside_padding_days,
        default_span_days=default_span_days,
    )


def count_overdue_bars(bars: BarStore) -> int:
    return sum(1 for b in bars.values() if b.overdue)


def collect_overdue_entity_keys(
    bars: BarStore,
) -> list[tuple[str, str]]:
    """Unique (entity_kind, entity_rel) with at least one overdue bar. Assets first."""
    asset_keys: list[tuple[str, str]] = []
    shot_keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for bar in bars.values():
        if not bar.overdue:
            continue
        kind = (bar.entity_kind or "").strip().lower()
        rel = (bar.entity_rel or "").replace("\\", "/").strip()
        if not kind or not rel:
            continue
        key = (kind, rel)
        if key in seen:
            continue
        seen.add(key)
        if kind == "asset":
            asset_keys.append(key)
        else:
            shot_keys.append(key)
    return asset_keys + shot_keys


@dataclass(frozen=True)
class DeptWaveRollup:
    """Aggregated department pass across filtered entities (min start → max due)."""

    department: str
    department_label: str
    start: date
    due: date
    entity_count: int
    overdue_count: int
    done_count: int
    in_progress_count: int
    color_hex: str
    overdue: bool
    entity_keys: tuple[tuple[str, str], ...] = ()

    @property
    def duration_days(self) -> int:
        return max(1, (self.due - self.start).days + 1)


def rollup_bars_by_department(
    bars: BarStore,
    groups: list,
    dept_order: list[str],
    *,
    dept_filter: str | None = None,
    hidden_departments: set[str] | None = None,
    dept_scope: str = "all",
    dept_reg: object | None = None,
    respect_hidden: bool = True,
    group_by_parent: bool = False,
) -> list[DeptWaveRollup]:
    """Roll up per-entity dept bars into one bar per department (wave / capacity view).

    Includes departments present on filtered timeline entities (shots/assets) even
    when no planned bar exists yet, so Dept · wave lists the full pipeline footprint.
    """
    from monostudio.core.schedule_dept_filter import (
        department_visible_in_schedule,
        filter_timeline_row,
        rollup_label,
        wave_rollup_department_id,
    )

    hidden = hidden_departments or set()
    entity_keys: set[tuple[str, str]] = set()
    dept_entities: dict[str, set[tuple[str, str]]] = {}
    labels: dict[str, str] = {}

    def _register_rollup_dep(rollup_dep: str, entity_key: tuple[str, str], label: str) -> None:
        dept_entities.setdefault(rollup_dep, set()).add(entity_key)
        if rollup_dep not in labels:
            labels[rollup_dep] = label

    for g in groups:
        kind = getattr(g, "entity_kind", None)
        rel = getattr(g, "entity_rel", None)
        if not kind or not rel:
            continue
        ek = (str(kind), str(rel).replace("\\", "/"))
        entity_keys.add(ek)
        for row in getattr(g, "departments", ()) or ():
            if dept_reg is not None:
                if not filter_timeline_row(
                    row,
                    hidden_departments=hidden,
                    dept_scope=dept_scope,
                    dept_reg=dept_reg,
                    respect_hidden=respect_hidden,
                ):
                    continue
            dep = (getattr(row, "department", None) or "").strip()
            if not dep:
                continue
            rollup_dep = dep
            if dept_reg is not None:
                rollup_dep = wave_rollup_department_id(
                    dep, group_by_parent=group_by_parent, dept_reg=dept_reg
                )
            if dept_filter and rollup_dep != dept_filter:
                continue
            row_label = (getattr(row, "department_label", None) or "").strip()
            if dept_reg is not None:
                row_label = rollup_label(rollup_dep, dept_reg)
            _register_rollup_dep(rollup_dep, ek, row_label or rollup_dep)

    by_dept: dict[str, list[PlannedBar]] = {}
    for bar in bars.values():
        kind = bar.entity_kind
        rel = bar.entity_rel.replace("\\", "/")
        dep = (bar.department or "").strip()
        if (kind, rel) not in entity_keys:
            continue
        if not dep:
            continue
        if respect_hidden and dep in hidden:
            continue
        if dept_reg is not None and not department_visible_in_schedule(dep, dept_scope, dept_reg):
            continue
        rollup_dep = dep
        if dept_reg is not None:
            rollup_dep = wave_rollup_department_id(
                dep, group_by_parent=group_by_parent, dept_reg=dept_reg
            )
        if dept_filter and rollup_dep != dept_filter:
            continue
        by_dept.setdefault(rollup_dep, []).append(bar)
        if rollup_dep not in labels:
            if dept_reg is not None:
                labels[rollup_dep] = rollup_label(rollup_dep, dept_reg)
            else:
                labels[rollup_dep] = bar.department_label or rollup_dep

    def _rollup(
        dep_id: str,
        dept_bars: list[PlannedBar],
        entities_with_dept: set[tuple[str, str]],
    ) -> DeptWaveRollup:
        entity_count = len(entities_with_dept) if entities_with_dept else 0
        entity_tuple = tuple(sorted(entities_with_dept))
        if not dept_bars:
            today = date.today()
            return DeptWaveRollup(
                department=dep_id,
                department_label=labels.get(dep_id, dep_id),
                start=today,
                due=today,
                entity_count=entity_count,
                overdue_count=0,
                done_count=0,
                in_progress_count=0,
                color_hex=CATEGORY_COLOR_HEX.get("unknown", "#71717a"),
                overdue=False,
                entity_keys=entity_tuple,
            )
        overdue_count = sum(1 for b in dept_bars if b.overdue)
        done_count = sum(1 for b in dept_bars if b.goal_met or b.status == STATUS_DONE)
        in_progress_count = sum(
            1
            for b in dept_bars
            if not b.goal_met and b.status == STATUS_PROGRESS
        )
        starts = [b.start for b in dept_bars]
        dues = [b.due for b in dept_bars]
        scheduled_entities = {
            (b.entity_kind, b.entity_rel.replace("\\", "/")) for b in dept_bars
        }
        entity_count = max(entity_count, len(scheduled_entities))
        dominant = dept_bars[0]
        for b in dept_bars:
            if b.overdue:
                dominant = b
                break
            if b.status == STATUS_PROGRESS:
                dominant = b
        return DeptWaveRollup(
            department=dep_id,
            department_label=labels.get(dep_id, dep_id),
            start=min(starts),
            due=max(dues),
            entity_count=entity_count,
            overdue_count=overdue_count,
            done_count=done_count,
            in_progress_count=in_progress_count,
            color_hex=dominant.color_hex,
            overdue=overdue_count > 0,
            entity_keys=entity_tuple,
        )

    all_dept_ids = set(by_dept.keys()) | set(dept_entities.keys())
    if dept_reg is not None:
        display_order = dept_reg.sort_department_ids(all_dept_ids)  # type: ignore[union-attr]
    else:
        rank = {(d or "").strip(): i for i, d in enumerate(dept_order) if (d or "").strip()}
        display_order = sorted(
            all_dept_ids,
            key=lambda d: (rank.get(d, 99999), d.casefold()),
        )

    out: list[DeptWaveRollup] = []
    for dep_id in display_order:
        if not dep_id:
            continue
        if dept_filter and dep_id != dept_filter:
            continue
        out.append(
            _rollup(
                dep_id,
                by_dept.get(dep_id, []),
                dept_entities.get(dep_id, set()),
            )
        )
    return out


@dataclass(frozen=True)
class EntityScheduleSummary:
    delivery: date | None
    span_start: date | None
    span_end: date | None
    focus_due: date | None
    focus_overdue: bool
    nearest_open_due: date | None
    any_overdue: bool
    has_plan: bool


@dataclass(frozen=True)
class UpcomingDueRow:
    entity_kind: str
    entity_name: str
    entity_rel: str
    department: str
    department_label: str
    due: date
    overdue: bool


def _entity_bars(
    bars: BarStore,
    *,
    entity_kind: str,
    entity_rel: str,
) -> list[PlannedBar]:
    kind = _norm_entity_kind(entity_kind)
    rel = _norm_entity_rel(entity_rel)
    return [b for b in bars.values() if b.entity_kind == kind and b.entity_rel == rel]


def next_unmet_goal_in_row(bars: list[PlannedBar]) -> PlannedBar | None:
    open_goals = [
        b
        for b in bars
        if not b.goal_met and b.status != STATUS_EXCLUDED and b.source == "override"
    ]
    if not open_goals:
        open_legacy = [
            b
            for b in bars
            if b.status not in (STATUS_DONE, STATUS_EXCLUDED) and b.source != "override"
        ]
        if not open_legacy:
            return None
        open_legacy.sort(key=lambda b: (b.due, b.target_workflow_order))
        return open_legacy[0]
    open_goals.sort(key=lambda b: (b.target_workflow_order, b.due, b.start))
    return open_goals[0]


def summarize_entity_schedule(
    bars: BarStore,
    schedule: ProjectSchedule,
    *,
    entity_kind: str,
    entity_rel: str,
    active_department: str | None = None,
) -> EntityScheduleSummary:
    from monostudio.core.project_schedule import entity_has_schedule, target_for_entity

    rel = entity_rel.replace("\\", "/")
    target = target_for_entity(schedule, entity_kind=entity_kind, entity_rel=rel)
    delivery = _parse_date(target.delivery) if target else None
    entity_bars = _entity_bars(bars, entity_kind=entity_kind, entity_rel=rel)
    has_plan = entity_has_schedule(schedule, entity_kind=entity_kind, entity_rel=rel) or bool(
        entity_bars
    )

    focus_due: date | None = None
    focus_overdue = False
    dep = (active_department or "").strip()
    if dep:
        row_bars = bars_for_row(bars, entity_kind, rel, dep)
        focus_goal = next_unmet_goal_in_row(row_bars)
        if focus_goal is not None:
            focus_due = focus_goal.due
            focus_overdue = focus_goal.overdue
        else:
            primary = primary_bar_for_row(bars, entity_kind, rel, dep)
            if primary is not None:
                focus_due = primary.due
                focus_overdue = primary.overdue

    open_bars = [
        b
        for b in entity_bars
        if not b.goal_met and b.status not in (STATUS_DONE, STATUS_EXCLUDED)
    ]
    nearest_open_due = min((b.due for b in open_bars), default=None)
    any_overdue = any(b.overdue for b in entity_bars)
    span_start = min((b.start for b in entity_bars), default=None)
    span_end = max((b.due for b in entity_bars), default=None)

    return EntityScheduleSummary(
        delivery=delivery,
        span_start=span_start,
        span_end=span_end,
        focus_due=focus_due,
        focus_overdue=focus_overdue,
        nearest_open_due=nearest_open_due,
        any_overdue=any_overdue,
        has_plan=has_plan,
    )


def collect_upcoming_due_rows(
    bars: BarStore,
    *,
    today: date | None = None,
    within_days: int = 7,
    limit: int = 40,
) -> list[UpcomingDueRow]:
    """Open bars due within N days (plus all overdue)."""
    ref = today or date.today()
    horizon = ref + timedelta(days=max(1, within_days))
    rows: list[UpcomingDueRow] = []
    for bar in bars.values():
        if bar.goal_met or bar.status in (STATUS_DONE, STATUS_EXCLUDED):
            continue
        if not bar.overdue and (bar.due < ref or bar.due > horizon):
            continue
        rows.append(
            UpcomingDueRow(
                entity_kind=bar.entity_kind,
                entity_name=bar.entity_name,
                entity_rel=bar.entity_rel.replace("\\", "/"),
                department=bar.department,
                department_label=bar.department_label or bar.department,
                due=bar.due,
                overdue=bar.overdue,
            )
        )
    rows.sort(key=lambda r: (not r.overdue, r.due, r.entity_name.lower(), r.department))
    return rows[:limit]


def list_due_display(
    summary: EntityScheduleSummary,
    *,
    active_department: str | None = None,
) -> tuple[str, bool]:
    """Return (display text, is_overdue) for list Due column."""
    dep = (active_department or "").strip()
    if dep and summary.focus_due is not None:
        return summary.focus_due.isoformat(), summary.focus_overdue
    if summary.delivery is not None:
        overdue = summary.delivery < date.today() and summary.any_overdue
        return summary.delivery.isoformat(), overdue
    if summary.nearest_open_due is not None:
        overdue = summary.any_overdue
        return summary.nearest_open_due.isoformat(), overdue
    return "—", False


def summarize_entity_from_ref(
    project_root: Path,
    entity: Asset | Shot,
    schedule: ProjectSchedule,
    *,
    active_department: str | None = None,
    today: date | None = None,
) -> EntityScheduleSummary:
    kind = "shot" if isinstance(entity, Shot) else "asset"
    rel = entity_rel_path(project_root, entity.path)
    idx = ProjectIndex(
        assets=(entity,) if kind == "asset" else (),
        shots=(entity,) if kind == "shot" else (),
        root=project_root,
    )
    bars = build_planned_bars(
        project_root,
        idx,
        schedule,
        include_shots=kind == "shot",
        include_assets=kind == "asset",
        today=today,
    )
    return summarize_entity_schedule(
        bars,
        schedule,
        entity_kind=kind,
        entity_rel=rel,
        active_department=active_department,
    )
