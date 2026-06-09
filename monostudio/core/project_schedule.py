"""
Project-wide deadline allocations and milestones.

Stored at <project_root>/.monostudio/schedule.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from monostudio.core.atomic_write import atomic_write_text
from monostudio.core.models import ProjectIndex

SCHEDULE_FILENAME = "schedule.json"
SCHEDULE_SCHEMA = 2


@dataclass(frozen=True)
class ScheduleMilestone:
    id: str
    label: str
    date: str  # YYYY-MM-DD

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "date": self.date}


@dataclass(frozen=True)
class ScheduleAllocation:
    """A manually fixed (override) bar. Always wins over computed plan."""

    id: str
    entity_kind: str  # asset | shot
    entity_rel: str
    department: str | None
    start: str  # YYYY-MM-DD
    due: str
    assignee_ids: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()  # cached display names at assignment time
    assignee_id: str = ""  # legacy: first assignee
    assignee: str = ""  # legacy: joined names
    note: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "entity_kind": self.entity_kind,
            "entity_rel": self.entity_rel,
            "department": self.department,
            "start": self.start,
            "due": self.due,
            "note": self.note,
        }
        if self.assignee_ids:
            d["assignee_ids"] = list(self.assignee_ids)
        if self.assignees:
            d["assignees"] = list(self.assignees)
        aid = (self.assignee_id or "").strip()
        if aid:
            d["assignee_id"] = aid
        name = (self.assignee or "").strip()
        if name:
            d["assignee"] = name
        return d


@dataclass(frozen=True)
class ScheduleTemplateStep:
    """One department step in a pipeline template (duration in working/calendar days)."""

    dept: str
    days: int
    after: str | None = None  # dept id this depends on; None = sequential by registry order

    def to_dict(self) -> dict:
        d: dict = {"dept": self.dept, "days": int(self.days)}
        if self.after:
            d["after"] = self.after
        return d


@dataclass(frozen=True)
class ScheduleTarget:
    """A delivery target for an entity; the planner derives per-dept bars from it."""

    entity_kind: str  # asset | shot
    entity_rel: str
    delivery: str  # YYYY-MM-DD
    template: str = "shot_default"

    def to_dict(self) -> dict:
        return {
            "entity_kind": self.entity_kind,
            "entity_rel": self.entity_rel,
            "delivery": self.delivery,
            "template": self.template,
        }


@dataclass(frozen=True)
class ScheduleWave:
    """Department wave: one dept due date for an entity (batch / pass planning)."""

    entity_kind: str  # asset | shot
    entity_rel: str
    department: str
    due: str  # YYYY-MM-DD
    template: str = "shot_default"

    def to_dict(self) -> dict:
        return {
            "entity_kind": self.entity_kind,
            "entity_rel": self.entity_rel,
            "department": self.department,
            "due": self.due,
            "template": self.template,
        }


def _auto_suppress_key(entity_kind: str, entity_rel: str, department: str) -> tuple[str, str, str]:
    return (
        (entity_kind or "").strip().lower(),
        (entity_rel or "").replace("\\", "/"),
        (department or "").strip(),
    )


@dataclass
class ProjectSchedule:
    project_start: str | None = None
    project_end: str | None = None
    milestones: list[ScheduleMilestone] = field(default_factory=list)
    allocations: list[ScheduleAllocation] = field(default_factory=list)  # overrides (locked bars)
    templates: dict[str, list[ScheduleTemplateStep]] = field(default_factory=dict)
    targets: list[ScheduleTarget] = field(default_factory=list)
    waves: list[ScheduleWave] = field(default_factory=list)
    auto_bar_suppressions: set[tuple[str, str, str]] = field(default_factory=set)


def _schedule_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / SCHEDULE_FILENAME


def _project_manifest_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / "project.json"


def read_project_manifest_start_date(project_root: Path) -> str | None:
    """Start date from New Project (``.monostudio/project.json`` → ``start_date``)."""
    path = _project_manifest_path(project_root)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("start_date")
    if not raw:
        return None
    s = str(raw).strip()[:10]
    return s if _parse_date(s) else None


def resolve_schedule_project_start(
    schedule: ProjectSchedule,
    project_root: Path | None,
) -> str | None:
    """``schedule.project_start`` or manifest ``start_date`` when opening the timeline."""
    ps = (schedule.project_start or "").strip()[:10] or None
    if ps and _parse_date(ps):
        return ps
    if project_root is not None:
        return read_project_manifest_start_date(Path(project_root))
    return None


def _parse_date(value: str) -> date | None:
    s = (value or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _parse_milestone(raw: object) -> ScheduleMilestone | None:
    if not isinstance(raw, dict):
        return None
    mid = str(raw.get("id") or "").strip()
    label = str(raw.get("label") or "").strip()
    d = str(raw.get("date") or "").strip()
    if not mid or not label or not _parse_date(d):
        return None
    return ScheduleMilestone(id=mid, label=label, date=d[:10])


def _parse_allocation(raw: object) -> ScheduleAllocation | None:
    if not isinstance(raw, dict):
        return None
    aid = str(raw.get("id") or "").strip()
    kind = str(raw.get("entity_kind") or "").strip().lower()
    rel = str(raw.get("entity_rel") or "").strip().replace("\\", "/")
    start = str(raw.get("start") or "").strip()
    due = str(raw.get("due") or "").strip()
    if not aid or kind not in ("asset", "shot") or not rel:
        return None
    if not _parse_date(start) or not _parse_date(due):
        return None
    dept_raw = raw.get("department")
    dept = str(dept_raw).strip() if dept_raw not in (None, "") else None
    from monostudio.core.user_identity import parse_assignee_ids_raw, parse_assignee_names_raw

    assignee_ids = parse_assignee_ids_raw(raw)
    assignees = parse_assignee_names_raw(raw, assignee_ids)
    assignee_id = assignee_ids[0] if assignee_ids else str(raw.get("assignee_id") or "").strip()
    assignee = ", ".join(assignees) if assignees else str(raw.get("assignee") or "").strip()
    note = str(raw.get("note") or "").strip()
    return ScheduleAllocation(
        id=aid,
        entity_kind=kind,
        entity_rel=rel,
        department=dept,
        start=start[:10],
        due=due[:10],
        assignee_ids=assignee_ids,
        assignees=assignees,
        assignee_id=assignee_id,
        assignee=assignee,
        note=note,
    )


def _parse_template_step(raw: object) -> ScheduleTemplateStep | None:
    if not isinstance(raw, dict):
        return None
    dept = str(raw.get("dept") or "").strip()
    if not dept:
        return None
    try:
        days = int(raw.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        days = 1
    after_raw = raw.get("after")
    after = str(after_raw).strip() if after_raw not in (None, "") else None
    return ScheduleTemplateStep(dept=dept, days=days, after=after)


def _parse_wave(raw: object) -> ScheduleWave | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("entity_kind") or "").strip().lower()
    rel = str(raw.get("entity_rel") or "").strip().replace("\\", "/")
    dept = str(raw.get("department") or "").strip()
    due = str(raw.get("due") or "").strip()
    if kind not in ("asset", "shot") or not rel or not dept or not _parse_date(due):
        return None
    template = str(raw.get("template") or "shot_default").strip() or "shot_default"
    return ScheduleWave(
        entity_kind=kind,
        entity_rel=rel,
        department=dept,
        due=due[:10],
        template=template,
    )


def _parse_target(raw: object) -> ScheduleTarget | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("entity_kind") or "").strip().lower()
    rel = str(raw.get("entity_rel") or "").strip().replace("\\", "/")
    delivery = str(raw.get("delivery") or "").strip()
    if kind not in ("asset", "shot") or not rel or not _parse_date(delivery):
        return None
    template = str(raw.get("template") or "shot_default").strip() or "shot_default"
    return ScheduleTarget(
        entity_kind=kind,
        entity_rel=rel,
        delivery=delivery[:10],
        template=template,
    )


def schedules_equal(a: ProjectSchedule, b: ProjectSchedule) -> bool:
    """True when two schedules have the same persisted fields."""
    return (
        a.project_start == b.project_start
        and a.project_end == b.project_end
        and a.milestones == b.milestones
        and a.allocations == b.allocations
        and a.templates == b.templates
        and a.targets == b.targets
        and a.waves == b.waves
        and a.auto_bar_suppressions == b.auto_bar_suppressions
    )


def load_schedule_from_disk(project_root: Path) -> ProjectSchedule:
    path = _schedule_path(project_root)
    try:
        if not path.is_file():
            return ProjectSchedule()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ProjectSchedule()
    if not isinstance(data, dict):
        return ProjectSchedule()

    milestones: list[ScheduleMilestone] = []
    for raw in data.get("milestones") or []:
        m = _parse_milestone(raw)
        if m:
            milestones.append(m)

    allocations: list[ScheduleAllocation] = []
    for raw in data.get("allocations") or []:
        a = _parse_allocation(raw)
        if a:
            allocations.append(a)

    ps = data.get("project_start")
    pe = data.get("project_end")
    project_start = str(ps).strip()[:10] if ps else None
    project_end = str(pe).strip()[:10] if pe else None
    if project_start and not _parse_date(project_start):
        project_start = None
    if project_end and not _parse_date(project_end):
        project_end = None

    templates: dict[str, list[ScheduleTemplateStep]] = {}
    raw_templates = data.get("templates")
    if isinstance(raw_templates, dict):
        for name, steps in raw_templates.items():
            key = str(name).strip()
            if not key or not isinstance(steps, list):
                continue
            parsed = [s for s in (_parse_template_step(x) for x in steps) if s]
            if parsed:
                templates[key] = parsed

    targets: list[ScheduleTarget] = []
    for raw in data.get("targets") or []:
        t = _parse_target(raw)
        if t:
            targets.append(t)

    waves: list[ScheduleWave] = []
    for raw in data.get("waves") or []:
        w = _parse_wave(raw)
        if w:
            waves.append(w)

    suppressions: set[tuple[str, str, str]] = set()
    for raw in data.get("auto_bar_suppressions") or []:
        if not isinstance(raw, dict):
            continue
        key = _auto_suppress_key(
            str(raw.get("entity_kind") or ""),
            str(raw.get("entity_rel") or ""),
            str(raw.get("department") or ""),
        )
        if key[0] in ("asset", "shot") and key[1] and key[2]:
            suppressions.add(key)

    return ProjectSchedule(
        project_start=project_start,
        project_end=project_end,
        milestones=milestones,
        allocations=allocations,
        templates=templates,
        targets=targets,
        waves=waves,
        auto_bar_suppressions=suppressions,
    )


def read_project_schedule(project_root: Path) -> ProjectSchedule:
    from monostudio.core.schedule_document import read_active_schedule

    active = read_active_schedule(project_root)
    if active is not None:
        return active
    return load_schedule_from_disk(project_root)


def write_project_schedule_to_disk(project_root: Path, schedule: ProjectSchedule) -> None:
    root = Path(project_root)
    payload = {
        "schema": SCHEDULE_SCHEMA,
        "project_start": schedule.project_start,
        "project_end": schedule.project_end,
        "milestones": [m.to_dict() for m in schedule.milestones],
        "allocations": [a.to_dict() for a in schedule.allocations],
        "templates": {
            name: [s.to_dict() for s in steps] for name, steps in schedule.templates.items()
        },
        "targets": [t.to_dict() for t in schedule.targets],
        "waves": [w.to_dict() for w in schedule.waves],
        "auto_bar_suppressions": [
            {
                "entity_kind": k,
                "entity_rel": r,
                "department": d,
            }
            for k, r, d in sorted(schedule.auto_bar_suppressions)
        ],
    }
    path = _schedule_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    from monostudio.core.schedule_history import record_schedule_save

    record_schedule_save(root, schedule)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content, encoding="utf-8")


def write_project_schedule(
    project_root: Path,
    schedule: ProjectSchedule,
    *,
    record_undo: bool = True,
) -> None:
    from monostudio.core.schedule_document import write_active_schedule

    if write_active_schedule(project_root, schedule, record_undo=record_undo):
        return
    write_project_schedule_to_disk(project_root, schedule)


def count_overdue_allocations(schedule: ProjectSchedule, *, today: date | None = None) -> int:
    ref = today or date.today()
    n = 0
    for a in schedule.allocations:
        due = _parse_date(a.due)
        if due is not None and due < ref:
            n += 1
    return n


def entity_has_planned_bars(
    bars: dict[tuple[str, str, str], object],
    *,
    entity_kind: str,
    entity_rel: str,
) -> bool:
    """True when the planner produced at least one department bar for this entity."""
    kind = (entity_kind or "").strip().lower()
    rel = (entity_rel or "").replace("\\", "/").strip()
    if not kind or not rel:
        return False
    return any(
        (k[0] or "").strip().lower() == kind and (k[1] or "").replace("\\", "/").strip() == rel
        for k in bars
    )


def entity_is_unscheduled(
    schedule: ProjectSchedule,
    *,
    entity_kind: str,
    entity_rel: str,
    bars: dict[tuple[str, str, str], object] | None = None,
) -> bool:
    """
    Unscheduled = no planned bars on the timeline.

    When ``bars`` is omitted (no project index), fall back to schedule JSON only
    (targets / waves / allocations).
    """
    kind = (entity_kind or "").strip().lower()
    rel = (entity_rel or "").replace("\\", "/").strip()
    if not kind or not rel:
        return False
    if bars is not None:
        return not entity_has_planned_bars(bars, entity_kind=kind, entity_rel=rel)
    return not entity_has_schedule(schedule, entity_kind=kind, entity_rel=rel)


def collect_unscheduled_entity_keys(
    schedule: ProjectSchedule,
    *,
    shot_paths: Iterable[str],
    asset_paths: Iterable[str],
    bars: dict[tuple[str, str, str], object] | None = None,
) -> list[tuple[str, str]]:
    """(entity_kind, entity_rel) with no planned timeline bars. Assets first."""
    out: list[tuple[str, str]] = []
    for rel in asset_paths:
        rel_n = (rel or "").replace("\\", "/").strip()
        if rel_n and entity_is_unscheduled(
            schedule, entity_kind="asset", entity_rel=rel_n, bars=bars
        ):
            out.append(("asset", rel_n))
    for rel in shot_paths:
        rel_n = (rel or "").replace("\\", "/").strip()
        if rel_n and entity_is_unscheduled(
            schedule, entity_kind="shot", entity_rel=rel_n, bars=bars
        ):
            out.append(("shot", rel_n))
    return out


def count_unscheduled_entities(
    schedule: ProjectSchedule,
    *,
    shot_paths: Iterable[str],
    asset_paths: Iterable[str],
    bars: dict[tuple[str, str, str], object] | None = None,
) -> int:
    """Entities with no planned timeline bars (see ``entity_is_unscheduled``)."""
    return len(
        collect_unscheduled_entity_keys(
            schedule,
            shot_paths=shot_paths,
            asset_paths=asset_paths,
            bars=bars,
        )
    )


def entity_has_schedule(schedule: ProjectSchedule, *, entity_kind: str, entity_rel: str) -> bool:
    """Delivery target, wave, or pinned allocation exists in schedule JSON."""
    key = _target_key(entity_kind, entity_rel)
    for t in schedule.targets:
        if _target_key(t.entity_kind, t.entity_rel) == key:
            return True
    for w in schedule.waves:
        if _target_key(w.entity_kind, w.entity_rel) == key:
            return True
    for a in schedule.allocations:
        if _target_key(a.entity_kind, a.entity_rel) == key:
            return True
    return False


def new_allocation_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def entity_rel_path(project_root: Path, entity_path: Path) -> str:
    try:
        return entity_path.resolve().relative_to(Path(project_root).resolve()).as_posix()
    except (OSError, ValueError):
        return entity_path.as_posix()


def upsert_allocation(project_root: Path, allocation: ScheduleAllocation) -> None:
    schedule = read_project_schedule(project_root)
    out: list[ScheduleAllocation] = []
    replaced = False
    for a in schedule.allocations:
        if a.id == allocation.id:
            out.append(allocation)
            replaced = True
        else:
            out.append(a)
    if not replaced:
        out.append(allocation)
    schedule.allocations = out
    write_project_schedule(project_root, schedule)


def delete_allocation(project_root: Path, allocation_id: str) -> None:
    aid = (allocation_id or "").strip()
    if not aid:
        return
    schedule = read_project_schedule(project_root)
    schedule.allocations = [a for a in schedule.allocations if a.id != aid]
    write_project_schedule(project_root, schedule)


def _row_key(kind: str, rel: str, department: str | None) -> tuple[str, str, str | None]:
    return (kind, rel.replace("\\", "/"), (department or "").strip() or None)


def upsert_allocation_for_row(project_root: Path, allocation: ScheduleAllocation) -> None:
    """Replace allocation matching entity + department (one bar per row)."""
    schedule = read_project_schedule(project_root)
    key = _row_key(allocation.entity_kind, allocation.entity_rel, allocation.department)
    kept = [
        a
        for a in schedule.allocations
        if _row_key(a.entity_kind, a.entity_rel, a.department) != key
    ]
    kept.append(allocation)
    schedule.allocations = kept
    dep = (allocation.department or "").strip()
    if dep:
        schedule.auto_bar_suppressions.discard(
            _auto_suppress_key(allocation.entity_kind, allocation.entity_rel, dep)
        )
    write_project_schedule(project_root, schedule)


def bulk_upsert_allocations(project_root: Path, allocations: Iterable[ScheduleAllocation]) -> None:
    schedule = read_project_schedule(project_root)
    by_row = {_row_key(a.entity_kind, a.entity_rel, a.department): a for a in schedule.allocations}
    for alloc in allocations:
        by_row[_row_key(alloc.entity_kind, alloc.entity_rel, alloc.department)] = alloc
        dep = (alloc.department or "").strip()
        if dep:
            schedule.auto_bar_suppressions.discard(
                _auto_suppress_key(alloc.entity_kind, alloc.entity_rel, dep)
            )
    schedule.allocations = list(by_row.values())
    write_project_schedule(project_root, schedule)


def new_milestone_id() -> str:
    return new_allocation_id()


def upsert_milestone(project_root: Path, milestone: ScheduleMilestone) -> None:
    schedule = read_project_schedule(project_root)
    out: list[ScheduleMilestone] = []
    replaced = False
    for m in schedule.milestones:
        if m.id == milestone.id:
            out.append(milestone)
            replaced = True
        else:
            out.append(m)
    if not replaced:
        out.append(milestone)
    out.sort(key=lambda x: x.date)
    schedule.milestones = out
    write_project_schedule(project_root, schedule)


def set_project_range(
    project_root: Path,
    *,
    project_start: str | None,
    project_end: str | None,
) -> None:
    """Persist production IN/OUT dates (frame-range markers on the timeline)."""
    schedule = read_project_schedule(project_root)
    ps = (project_start or "").strip()[:10] or None
    pe = (project_end or "").strip()[:10] or None
    if ps and not _parse_date(ps):
        ps = None
    if pe and not _parse_date(pe):
        pe = None
    schedule.project_start = ps
    schedule.project_end = pe
    write_project_schedule(project_root, schedule)


def delete_milestone(project_root: Path, milestone_id: str) -> None:
    mid = (milestone_id or "").strip()
    if not mid:
        return
    schedule = read_project_schedule(project_root)
    schedule.milestones = [m for m in schedule.milestones if m.id != mid]
    write_project_schedule(project_root, schedule)


DEFAULT_TEMPLATE_NAME = "shot_default"
DEFAULT_STEP_DAYS = 5


def default_template_for_project(project_root: Path) -> list[ScheduleTemplateStep]:
    """Build a sequential template from the project's department order."""
    from monostudio.core.department_registry import DepartmentRegistry

    dept_reg = DepartmentRegistry.for_project(Path(project_root))
    steps: list[ScheduleTemplateStep] = []
    try:
        depts = dept_reg.get_departments()
    except Exception:  # noqa: BLE001
        depts = []
    for dep in depts:
        dep_id = getattr(dep, "id", None) or getattr(dep, "name", None) or str(dep)
        dep_id = str(dep_id).strip()
        if dep_id:
            steps.append(ScheduleTemplateStep(dept=dep_id, days=DEFAULT_STEP_DAYS))
    return steps


def ensure_default_template(project_root: Path) -> ProjectSchedule:
    """Make sure a usable default template exists; persist if newly created."""
    schedule = read_project_schedule(project_root)
    if schedule.templates.get(DEFAULT_TEMPLATE_NAME):
        return schedule
    steps = default_template_for_project(project_root)
    if not steps:
        return schedule
    schedule.templates[DEFAULT_TEMPLATE_NAME] = steps
    write_project_schedule(project_root, schedule)
    return schedule


def set_template(
    project_root: Path, name: str, steps: Iterable[ScheduleTemplateStep]
) -> None:
    key = (name or "").strip()
    if not key:
        return
    schedule = read_project_schedule(project_root)
    schedule.templates[key] = list(steps)
    write_project_schedule(project_root, schedule)


def _target_key(kind: str, rel: str) -> tuple[str, str]:
    return ((kind or "").strip().lower(), (rel or "").replace("\\", "/").strip())


def target_for_entity(
    schedule: ProjectSchedule, *, entity_kind: str, entity_rel: str
) -> ScheduleTarget | None:
    key = _target_key(entity_kind, entity_rel)
    for t in schedule.targets:
        if _target_key(t.entity_kind, t.entity_rel) == key:
            return t
    return None


def set_target(project_root: Path, target: ScheduleTarget) -> None:
    schedule = read_project_schedule(project_root)
    key = _target_key(target.entity_kind, target.entity_rel)
    kept = [t for t in schedule.targets if _target_key(t.entity_kind, t.entity_rel) != key]
    kept.append(target)
    schedule.targets = kept
    write_project_schedule(project_root, schedule)


def bulk_set_targets(project_root: Path, targets: Iterable[ScheduleTarget]) -> None:
    schedule = read_project_schedule(project_root)
    by_key = {_target_key(t.entity_kind, t.entity_rel): t for t in schedule.targets}
    for raw in targets:
        kind = str(raw.entity_kind or "").strip().lower()
        rel = str(raw.entity_rel or "").strip().replace("\\", "/")
        if kind not in ("asset", "shot") or not rel:
            continue
        t = ScheduleTarget(
            entity_kind=kind,
            entity_rel=rel,
            delivery=raw.delivery,
            template=raw.template,
        )
        by_key[_target_key(kind, rel)] = t
    schedule.targets = list(by_key.values())
    write_project_schedule(project_root, schedule)


def delete_target(project_root: Path, *, entity_kind: str, entity_rel: str) -> None:
    schedule = read_project_schedule(project_root)
    key = _target_key(entity_kind, entity_rel)
    schedule.targets = [
        t for t in schedule.targets if _target_key(t.entity_kind, t.entity_rel) != key
    ]
    write_project_schedule(project_root, schedule)


def clear_entity_schedule(project_root: Path, *, entity_kind: str, entity_rel: str) -> None:
    """Remove delivery target, waves, and pinned bars for one entity."""
    schedule = read_project_schedule(project_root)
    key = _target_key(entity_kind, entity_rel)
    rel = (entity_rel or "").replace("\\", "/")
    kind = (entity_kind or "").strip().lower()
    schedule.targets = [
        t for t in schedule.targets if _target_key(t.entity_kind, t.entity_rel) != key
    ]
    schedule.waves = [
        w
        for w in schedule.waves
        if not (
            (w.entity_kind or "").strip().lower() == kind
            and (w.entity_rel or "").replace("\\", "/") == rel
        )
    ]
    schedule.allocations = [
        a
        for a in schedule.allocations
        if not (
            (a.entity_kind or "").strip().lower() == kind
            and (a.entity_rel or "").replace("\\", "/") == rel
        )
    ]
    write_project_schedule(project_root, schedule)


def _wave_key(kind: str, rel: str, department: str) -> tuple[str, str, str]:
    return (kind, rel.replace("\\", "/"), (department or "").strip())


def template_step_days(
    schedule: ProjectSchedule,
    template_name: str,
    department: str,
    project_root: Path,
) -> int:
    dep = (department or "").strip()
    steps = schedule.templates.get(template_name) or schedule.templates.get(DEFAULT_TEMPLATE_NAME)
    if not steps:
        steps = default_template_for_project(project_root)
    for step in steps or []:
        if step.dept == dep:
            return max(1, int(step.days))
    return DEFAULT_STEP_DAYS


def bar_dates_for_wave_due(
    schedule: ProjectSchedule,
    *,
    template_name: str,
    department: str,
    due: date,
    project_root: Path,
) -> tuple[date, date]:
    days = template_step_days(schedule, template_name, department, project_root)
    start = due - timedelta(days=days - 1)
    return start, due


def bulk_set_waves(project_root: Path, waves: Iterable[ScheduleWave]) -> None:
    schedule = read_project_schedule(project_root)
    by_key = {
        _wave_key(w.entity_kind, w.entity_rel, w.department): w for w in schedule.waves
    }
    for w in waves:
        by_key[_wave_key(w.entity_kind, w.entity_rel, w.department)] = w
    schedule.waves = list(by_key.values())
    write_project_schedule(project_root, schedule)


def _normalize_entity_row(kind: str, rel: str, department: str) -> tuple[str, str, str] | None:
    k = (kind or "").strip().lower()
    r = (rel or "").replace("\\", "/")
    d = (department or "").strip()
    if k not in ("asset", "shot") or not r or not d:
        return None
    return (k, r, d)


def clear_entity_department_schedules(
    project_root: Path,
    *,
    rows: Iterable[tuple[str, str, str]],
    suppress_auto: bool = True,
) -> None:
    """Remove waves/pinned rows per (entity, department); optionally suppress auto bars."""
    triples: set[tuple[str, str, str]] = set()
    for kind, rel, department in rows:
        key = _normalize_entity_row(kind, rel, department)
        if key is not None:
            triples.add(key)
    if not triples:
        return
    schedule = read_project_schedule(project_root)
    schedule.waves = [
        w
        for w in schedule.waves
        if _normalize_entity_row(w.entity_kind, w.entity_rel, w.department) not in triples
    ]
    schedule.allocations = [
        a
        for a in schedule.allocations
        if _normalize_entity_row(a.entity_kind, a.entity_rel, a.department) not in triples
    ]
    if suppress_auto:
        for key in triples:
            schedule.auto_bar_suppressions.add(key)
    write_project_schedule(project_root, schedule)


def replace_entity_department_allocations(
    project_root: Path,
    *,
    clear_rows: Iterable[tuple[str, str, str]],
    allocations: Iterable[ScheduleAllocation],
    suppress_auto_on_clear: bool = False,
) -> None:
    """Atomically clear per-entity department rows then write new pinned allocations."""
    triples: set[tuple[str, str, str]] = set()
    for kind, rel, department in clear_rows:
        key = _normalize_entity_row(kind, rel, department)
        if key is not None:
            triples.add(key)
    schedule = read_project_schedule(project_root)
    if triples:
        schedule.waves = [
            w
            for w in schedule.waves
            if _normalize_entity_row(w.entity_kind, w.entity_rel, w.department) not in triples
        ]
        schedule.allocations = [
            a
            for a in schedule.allocations
            if _normalize_entity_row(a.entity_kind, a.entity_rel, a.department) not in triples
        ]
        if suppress_auto_on_clear:
            for key in triples:
                schedule.auto_bar_suppressions.add(key)
    by_row = {
        _row_key(a.entity_kind, a.entity_rel, a.department): a for a in schedule.allocations
    }
    for alloc in allocations:
        row = _normalize_entity_row(alloc.entity_kind, alloc.entity_rel, alloc.department)
        if row is None:
            continue
        by_row[_row_key(alloc.entity_kind, alloc.entity_rel, alloc.department)] = alloc
        schedule.auto_bar_suppressions.discard(row)
    schedule.allocations = list(by_row.values())
    write_project_schedule(project_root, schedule)


def clear_department_schedule_for_entities(
    project_root: Path,
    *,
    department: str,
    entities: Iterable[tuple[str, str]],
    suppress_auto: bool = True,
) -> None:
    """Remove waves and pinned allocations for one department id across entities."""
    dep = (department or "").strip()
    if not dep:
        return
    rows = []
    for kind, rel in entities:
        key = _normalize_entity_row(kind, rel, dep)
        if key is not None:
            rows.append(key)
    clear_entity_department_schedules(
        project_root, rows=rows, suppress_auto=suppress_auto
    )


def clear_auto_bar_suppression_for_row(
    project_root: Path,
    *,
    entity_kind: str,
    entity_rel: str,
    department: str,
) -> None:
    key = _auto_suppress_key(entity_kind, entity_rel, department)
    schedule = read_project_schedule(project_root)
    if key not in schedule.auto_bar_suppressions:
        return
    schedule.auto_bar_suppressions.discard(key)
    write_project_schedule(project_root, schedule)


def clear_auto_bar_suppressions_for_entities(
    project_root: Path,
    *,
    department: str,
    entities: Iterable[tuple[str, str]],
) -> None:
    dep = (department or "").strip()
    if not dep:
        return
    schedule = read_project_schedule(project_root)
    changed = False
    for kind, rel in entities:
        key = _auto_suppress_key(kind, rel, dep)
        if key in schedule.auto_bar_suppressions:
            schedule.auto_bar_suppressions.discard(key)
            changed = True
    if changed:
        write_project_schedule(project_root, schedule)


def delete_wave_for_row(
    project_root: Path,
    *,
    entity_kind: str,
    entity_rel: str,
    department: str,
) -> None:
    schedule = read_project_schedule(project_root)
    key = _wave_key(entity_kind, entity_rel, department)
    schedule.waves = [
        w
        for w in schedule.waves
        if _wave_key(w.entity_kind, w.entity_rel, w.department) != key
    ]
    write_project_schedule(project_root, schedule)


def allocation_for_row(
    schedule: ProjectSchedule,
    *,
    entity_kind: str,
    entity_rel: str,
    department: str | None,
) -> ScheduleAllocation | None:
    rel = entity_rel.replace("\\", "/")
    dept = (department or "").strip() or None
    for a in schedule.allocations:
        if a.entity_kind != entity_kind:
            continue
        if a.entity_rel.replace("\\", "/") != rel:
            continue
        a_dept = (a.department or "").strip() or None
        if a_dept == dept:
            return a
    return None


@dataclass(frozen=True)
class TimelineRow:
    entity_kind: str
    entity_rel: str
    entity_name: str
    department: str | None
    department_label: str


@dataclass(frozen=True)
class TimelineEntityGroup:
    """One shot/asset and its department rows."""

    entity_kind: str
    entity_rel: str
    entity_name: str
    departments: tuple[TimelineRow, ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.entity_kind, self.entity_rel.replace("\\", "/"))


def build_timeline_entity_groups(
    project_root: Path,
    project_index: ProjectIndex,
    *,
    include_shots: bool = True,
    include_assets: bool = False,
) -> list[TimelineEntityGroup]:
    flat = build_timeline_rows(
        project_root,
        project_index,
        include_shots=include_shots,
        include_assets=include_assets,
    )
    order: list[tuple[str, str]] = []
    buckets: dict[tuple[str, str], list[TimelineRow]] = {}
    for row in flat:
        key = (row.entity_kind, row.entity_rel.replace("\\", "/"))
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(row)
    from monostudio.core.department_registry import DepartmentRegistry

    dept_reg = DepartmentRegistry.for_project(Path(project_root))
    out_groups: list[TimelineEntityGroup] = []
    for key in order:
        dept_rows = sorted(
            buckets[key],
            key=lambda r: dept_reg.department_sort_key((r.department or "").strip()),
        )
        out_groups.append(
            TimelineEntityGroup(
                entity_kind=key[0],
                entity_rel=key[1],
                entity_name=dept_rows[0].entity_name,
                departments=tuple(dept_rows),
            )
        )
    return out_groups


def build_timeline_rows(
    project_root: Path,
    project_index: ProjectIndex,
    *,
    include_shots: bool = True,
    include_assets: bool = False,
) -> list[TimelineRow]:
    from monostudio.core.department_registry import DepartmentRegistry

    root = Path(project_root)
    dept_reg = DepartmentRegistry.for_project(root)
    rows: list[TimelineRow] = []

    if include_assets:
        for asset in project_index.assets:
            rel = entity_rel_path(root, asset.path)
            for dept in asset.departments:
                dep_id = (dept.name or "").strip()
                if not dep_id:
                    continue
                label = dept_reg.get_department_label(dep_id) or dep_id
                rows.append(
                    TimelineRow(
                        entity_kind="asset",
                        entity_rel=rel,
                        entity_name=asset.name,
                        department=dep_id,
                        department_label=label,
                    )
                )

    if include_shots:
        for shot in project_index.shots:
            rel = entity_rel_path(root, shot.path)
            for dept in shot.departments:
                dep_id = (dept.name or "").strip()
                if not dep_id:
                    continue
                label = dept_reg.get_department_label(dep_id) or dep_id
                rows.append(
                    TimelineRow(
                        entity_kind="shot",
                        entity_rel=rel,
                        entity_name=shot.name,
                        department=dep_id,
                        department_label=label,
                    )
                )

    return rows


def compute_timeline_view_range(
    schedule: ProjectSchedule,
    bars: object | None = None,
    *,
    project_root: Path | None = None,
    padding_days: int = 7,
    outside_padding_days: int = 14,
    default_span_days: int = 56,
) -> tuple[date, date]:
    """
    Horizontal span for the Gantt: bars, milestones, project start (manifest/schedule),
    deadline (``project_end``), plus extra days before start and after deadline.
    """
    today = date.today()
    starts: list[date] = [today]
    ends: list[date] = [today]

    ps = _parse_date(resolve_schedule_project_start(schedule, project_root) or "")
    pe = _parse_date(schedule.project_end or "")
    if ps:
        starts.append(ps)
    if pe:
        ends.append(pe)

    bar_values = getattr(bars, "values", None)
    iterable = bar_values() if callable(bar_values) else (bars or ())
    for bar in iterable:
        b_start = getattr(bar, "start", None)
        b_due = getattr(bar, "due", None)
        if isinstance(b_start, date):
            starts.append(b_start)
        if isinstance(b_due, date):
            ends.append(b_due)

    for a in schedule.allocations:
        ds = _parse_date(a.start)
        dd = _parse_date(a.due)
        if ds:
            starts.append(ds)
        if dd:
            ends.append(dd)
    for m in schedule.milestones:
        d = _parse_date(m.date)
        if d:
            starts.append(d)
            ends.append(d)
    for t in schedule.targets:
        d = _parse_date(t.delivery)
        if d:
            ends.append(d)
    for w in schedule.waves:
        d = _parse_date(w.due)
        if d:
            ends.append(d)

    view_start = min(starts) - timedelta(days=padding_days)
    view_end = max(ends) + timedelta(days=padding_days)

    if ps:
        view_start = min(view_start, ps - timedelta(days=outside_padding_days))
    if pe:
        view_end = max(view_end, pe + timedelta(days=outside_padding_days))

    if (view_end - view_start).days < default_span_days:
        view_end = view_start + timedelta(days=default_span_days)
    return view_start, view_end


def compute_view_date_range(
    schedule: ProjectSchedule,
    *,
    project_root: Path | None = None,
    padding_days: int = 7,
    default_span_days: int = 56,
) -> tuple[date, date]:
    return compute_timeline_view_range(
        schedule,
        bars=None,
        project_root=project_root,
        padding_days=padding_days,
        outside_padding_days=14,
        default_span_days=default_span_days,
    )
