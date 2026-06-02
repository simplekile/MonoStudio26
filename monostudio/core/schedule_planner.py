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


@dataclass(frozen=True)
class PlannedBar:
    entity_kind: str
    entity_rel: str
    entity_name: str
    department: str
    department_label: str
    start: date
    due: date
    source: str  # "auto" | "override"
    status: str  # done | progress | waiting
    status_id: str
    color_hex: str
    overdue: bool
    assignee: str = ""
    note: str = ""
    allocation_id: str | None = None  # set when this bar comes from a stored override

    @property
    def row_key(self) -> tuple[str, str, str]:
        return (self.entity_kind, self.entity_rel.replace("\\", "/"), self.department)


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
) -> dict[tuple[str, str, str], PlannedBar]:
    """Return computed bars keyed by (entity_kind, entity_rel, department)."""
    from monostudio.core.department_registry import DepartmentRegistry

    root = Path(project_root)
    today = today or date.today()
    dept_reg = DepartmentRegistry.for_project(root)
    status_reg = load_production_status_registry(root)
    entities = _entity_index(project_index, root)

    bars: dict[tuple[str, str, str], PlannedBar] = {}
    suppressed_auto = set(schedule.auto_bar_suppressions or ())

    def dept_label(dep_id: str) -> str:
        return dept_reg.get_department_label(dep_id) or dep_id

    def _kind_allowed(kind: str) -> bool:
        k = _norm_entity_kind(kind)
        return (k == "shot" and include_shots) or (k == "asset" and include_assets)

    def _status_for(ref: Asset | Shot | None, dep_id: str) -> tuple[str, str, str]:
        """Return (bucket, status_id, color_hex)."""
        if ref is None:
            return STATUS_WAITING, "waiting", CATEGORY_COLOR_HEX["unknown"]
        dep = None
        for d in ref.departments:
            if (d.name or "").strip() == dep_id:
                dep = d
                break
        if dep is None:
            return STATUS_WAITING, "waiting", CATEGORY_COLOR_HEX["unknown"]
        override = override_status_id_for_department(ref, dep_id)
        sid = effective_status_id_for_department(dep, override, status_reg)
        category = status_reg.category_for(sid)
        bucket = _bucket_for_category(category)
        return bucket, sid, CATEGORY_COLOR_HEX.get(category, CATEGORY_COLOR_HEX["unknown"])

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

        # Backward chain: last step ends at delivery; each earlier step ends the day
        # before the next one starts.
        end_cursor = delivery
        for step in reversed(active_steps):
            dep_step = (step.dept or "").strip()
            days = max(1, int(step.days))
            due = end_cursor
            start = due - timedelta(days=days - 1)
            if dep_step and (kind, rel, dep_step) in suppressed_auto:
                # Keep backward chain spacing without drawing a bar.
                end_cursor = start - timedelta(days=1)
                continue
            bucket, sid, color = _status_for(ref, step.dept)
            if _bar_is_excluded(bucket, sid):
                end_cursor = start - timedelta(days=1)
                continue
            overdue = due < today and bucket != STATUS_DONE
            bar = PlannedBar(
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
            )
            bars[bar.row_key] = bar
            end_cursor = start - timedelta(days=1)

    # 2) Department waves — replace auto bars for that entity + dept.
    for wave in schedule.waves:
        kind = _norm_entity_kind(wave.entity_kind)
        if not _kind_allowed(kind):
            continue
        dep_id = (wave.department or "").strip()
        if not dep_id:
            continue
        due = _parse_date(wave.due)
        if not due:
            continue
        rel = _norm_entity_rel(wave.entity_rel)
        ref = entities.get((kind, rel))
        entity_name = getattr(ref, "name", rel.rsplit("/", 1)[-1])
        start, due_d = bar_dates_for_wave_due(
            schedule,
            template_name=wave.template,
            department=dep_id,
            due=due,
            project_root=root,
        )
        bucket, sid, color = _status_for(ref, dep_id)
        excluded = _bar_is_excluded(bucket, sid)
        overdue = not excluded and due_d < today and bucket != STATUS_DONE
        bar = PlannedBar(
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
        )
        bars[bar.row_key] = bar

    # 3) Overrides win: replace (or add) bars for fixed allocations.
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
        bucket, sid, color = _status_for(ref, dep_id)
        excluded = _bar_is_excluded(bucket, sid)
        overdue = not excluded and due < today and bucket != STATUS_DONE
        bar = PlannedBar(
            entity_kind=kind,
            entity_rel=rel,
            entity_name=entity_name,
            department=dep_id,
            department_label=dept_label(dep_id),
            start=start,
            due=due,
            source="override",
            status=bucket,
            status_id=sid,
            color_hex=color,
            overdue=overdue,
            assignee=alloc.assignee,
            note=alloc.note,
            allocation_id=alloc.id,
        )
        bars[bar.row_key] = bar

    return bars


def compute_view_date_range_from_bars(
    bars: dict[tuple[str, str, str], PlannedBar],
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


def count_overdue_bars(bars: dict[tuple[str, str, str], PlannedBar]) -> int:
    return sum(1 for b in bars.values() if b.overdue)


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
    bars: dict[tuple[str, str, str], PlannedBar],
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
    for key, bar in bars.items():
        kind, rel, dep = key
        if (kind, rel) not in entity_keys:
            continue
        dep = (dep or "").strip()
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
        done_count = sum(1 for b in dept_bars if b.status == STATUS_DONE)
        in_progress_count = sum(1 for b in dept_bars if b.status == STATUS_PROGRESS)
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
    bars: dict[tuple[str, str, str], PlannedBar],
    *,
    entity_kind: str,
    entity_rel: str,
) -> list[PlannedBar]:
    rel = entity_rel.replace("\\", "/")
    return [b for k, b in bars.items() if k[0] == entity_kind and k[1] == rel]


def summarize_entity_schedule(
    bars: dict[tuple[str, str, str], PlannedBar],
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
        focus_bar = bars.get((entity_kind, rel, dep))
        if focus_bar is not None:
            focus_due = focus_bar.due
            focus_overdue = focus_bar.overdue

    open_bars = [
        b
        for b in entity_bars
        if b.status not in (STATUS_DONE, STATUS_EXCLUDED)
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
    bars: dict[tuple[str, str, str], PlannedBar],
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
        if bar.status in (STATUS_DONE, STATUS_EXCLUDED):
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
