"""Read-only aggregates for Dashboard (notes, counts)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from monostudio.core.item_comments import (
    entry_author_display,
    entry_preview_text,
    normalize_note_department_id,
    read_item_comments,
)
from monostudio.core.project_schedule import (
    ProjectSchedule,
    collect_unscheduled_entity_keys,
    read_project_schedule,
)
from monostudio.core.schedule_dept_filter import DEPT_SCOPE_ALL
from monostudio.core.schedule_planner import (
    STATUS_DONE,
    STATUS_EXCLUDED,
    STATUS_PROGRESS,
    bar_has_blocked_status,
    build_planned_bars,
    collect_overdue_entity_keys,
    collect_overdue_entity_rows,
    collect_upcoming_due_rows,
    count_overdue_bars,
)


@dataclass(frozen=True)
class DashboardNoteRow:
    entity_kind: str
    entity_name: str
    entity_path: Path
    comment_id: str
    at: str
    author: str
    author_id: str | None
    text: str
    department: str = ""  # note department id (empty = legacy / general)
    mentions: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardDeptStat:
    """Per-department roll-up for the Department workload card."""

    department: str
    department_label: str
    total: int
    done: int
    in_progress: int
    overdue: int
    waiting: int
    due_soon: int
    color_hex: str
    shots_total: int = 0
    assets_total: int = 0
    shots_due_soon: int = 0
    assets_due_soon: int = 0
    shots_overdue: int = 0
    assets_overdue: int = 0
    applies_to_shots: bool = False
    applies_to_assets: bool = False

    @property
    def completion_pct(self) -> float:
        return (self.done / self.total * 100.0) if self.total else 0.0


@dataclass(frozen=True)
class DashboardSnapshot:
    assets_count: int
    shots_count: int
    open_notes_count: int
    mention_notes_count: int
    unread_mention_count: int  # unread @mentions in mention_inbox (not yet viewed)
    overdue_count: int
    overdue_entities: tuple[tuple[str, str], ...]  # (entity_kind, entity_rel)
    overdue_entity_rows: tuple  # OverdueEntityRow
    unscheduled_count: int
    unscheduled_entities: tuple[tuple[str, str], ...]  # (entity_kind, entity_rel)
    allocation_count: int
    open_notes: tuple[DashboardNoteRow, ...]
    mention_notes: tuple[DashboardNoteRow, ...]  # open notes @mentioning signed-in user
    upcoming_due: tuple  # UpcomingDueRow
    dept_workload_overdue_rows: tuple = ()  # OverdueEntityRow — all depts (workload popover)
    dept_workload_upcoming_due: tuple = ()  # UpcomingDueRow — all depts (workload popover)
    # Pipeline health (derived from planned bars)
    total_bars: int = 0
    done_count: int = 0
    in_progress_count: int = 0
    waiting_count: int = 0
    blocked_count: int = 0
    completion_pct: float = 0.0
    dept_stats: tuple[DashboardDeptStat, ...] = ()


def _rel_path(project_root: Path, entity_path: Path) -> str:
    try:
        return entity_path.resolve().relative_to(project_root.resolve()).as_posix()
    except (OSError, ValueError):
        return entity_path.as_posix()


def collect_open_notes(
    project_root: Path,
    assets: tuple,
    shots: tuple,
    *,
    workspace_root: Path | None = None,
    limit: int = 100,
) -> list[DashboardNoteRow]:
    rows: list[DashboardNoteRow] = []
    for asset in assets:
        path = Path(asset.path)
        for entry in read_item_comments(path):
            if entry.done:
                continue
            rows.append(
                DashboardNoteRow(
                    entity_kind="asset",
                    entity_name=asset.name,
                    entity_path=path,
                    comment_id=entry.id,
                    at=entry.at,
                    author=entry_author_display(entry, workspace_root),
                    author_id=entry.author_id,
                    text=entry_preview_text(entry),
                    department=normalize_note_department_id(entry.department),
                    mentions=entry.mentions,
                )
            )
    for shot in shots:
        path = Path(shot.path)
        for entry in read_item_comments(path):
            if entry.done:
                continue
            rows.append(
                DashboardNoteRow(
                    entity_kind="shot",
                    entity_name=shot.name,
                    entity_path=path,
                    comment_id=entry.id,
                    at=entry.at,
                    author=entry_author_display(entry, workspace_root),
                    author_id=entry.author_id,
                    text=entry_preview_text(entry),
                    department=normalize_note_department_id(entry.department),
                    mentions=entry.mentions,
                )
            )
    rows.sort(key=lambda r: r.at, reverse=True)
    return rows[:limit]


def count_unread_mentions(project_root: Path, user_id: str) -> int:
    """Unread @mention inbox rows for ``user_id`` (same source as the bell)."""
    from monostudio.core.mention_inbox import unread_for_user

    uid = (user_id or "").strip()
    if not uid:
        return 0
    return len(unread_for_user(Path(project_root), uid))


def collect_mention_notes(
    open_notes: list[DashboardNoteRow],
    user_id: str,
    *,
    limit: int = 50,
) -> list[DashboardNoteRow]:
    """Open notes whose @mentions include ``user_id`` (Recent Notes filter)."""
    uid = (user_id or "").strip()
    if not uid:
        return []
    rows = [n for n in open_notes if uid in n.mentions]
    return rows[:limit]


def _filter_bars_by_departments(
    bars: dict,
    root: Path,
    *,
    allowed: set[str] | None,
    hidden: set[str],
    respect_hidden: bool,
    dept_scope: str,
) -> dict:
    """Keep only bars whose department is visible under the shared sidebar/Schedule rules.

    - ``allowed`` whitelist (sidebar department picker): None = keep all, empty = keep none.
    - ``hidden`` departments are dropped when ``respect_hidden`` is set.
    - ``dept_scope`` follows the Schedule leaf/root/all visibility.
    """
    if allowed is None and not hidden and dept_scope == "all":
        return bars
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.schedule_dept_filter import department_visible_in_schedule

    dept_reg = DepartmentRegistry.for_project(root)
    allow = None if allowed is None else {d.strip() for d in allowed if (d or "").strip()}
    out: dict = {}
    for key, bar in bars.items():
        dep = (bar.department or "").strip()
        if not dep:
            continue
        if respect_hidden and dep in hidden:
            continue
        if allow is not None and dep not in allow:
            continue
        if not department_visible_in_schedule(dep, dept_scope, dept_reg):
            continue
        out[key] = bar
    return out


def build_metrics_planned_bars(
    project_root: Path,
    project_index,
    schedule: ProjectSchedule,
    *,
    include_shots: bool = True,
    include_assets: bool = True,
    allowed_departments: set[str] | None = None,
    hidden_departments: set[str] | None = None,
    respect_hidden: bool = True,
    dept_scope: str = "leaf",
) -> dict:
    """Planned bars for Dashboard KPIs and Schedule overdue stat — shared scope rules."""
    bars = build_planned_bars(
        project_root,
        project_index,
        schedule,
        include_shots=bool(include_shots),
        include_assets=bool(include_assets),
    )
    return _filter_bars_by_departments(
        bars,
        project_root,
        allowed=allowed_departments,
        hidden=hidden_departments or set(),
        respect_hidden=respect_hidden,
        dept_scope=dept_scope,
    )


def build_dashboard_snapshot(
    project_root: Path | None,
    *,
    assets: tuple,
    shots: tuple,
    workspace_root: Path | None = None,
    project_index=None,
    include_shots: bool = True,
    include_assets: bool = True,
    allowed_departments: set[str] | None = None,
    workload_departments: set[str] | None = None,
    workload_department_order: tuple[str, ...] | None = None,
    workload_shot_departments: set[str] | None = None,
    workload_asset_departments: set[str] | None = None,
    hidden_departments: set[str] | None = None,
    respect_hidden: bool = True,
    dept_scope: str = "all",
) -> DashboardSnapshot | None:
    if project_root is None:
        return None
    root = Path(project_root)
    schedule: ProjectSchedule = read_project_schedule(root)
    open_notes = collect_open_notes(root, assets, shots, workspace_root=workspace_root)
    mention_notes: list[DashboardNoteRow] = []
    unread_mention_count = 0
    if workspace_root is not None:
        from monostudio.core.user_identity import get_current_user

        user = get_current_user(workspace_root)
        if user is not None:
            mention_notes = collect_mention_notes(open_notes, user.id)
            unread_mention_count = count_unread_mentions(root, user.id)
    shot_rels = [_rel_path(root, Path(s.path)) for s in shots]
    asset_rels = [_rel_path(root, Path(a.path)) for a in assets]

    overdue_count = 0
    upcoming: tuple = ()
    total_bars = 0
    done_count = 0
    in_progress_count = 0
    waiting_count = 0
    blocked_count = 0
    completion_pct = 0.0
    dept_stats: tuple[DashboardDeptStat, ...] = ()
    dept_workload_overdue: tuple = ()
    dept_workload_upcoming: tuple = ()
    bars: dict = {}
    if project_index is not None:
        bars = build_metrics_planned_bars(
            root,
            project_index,
            schedule,
            include_shots=include_shots,
            include_assets=include_assets,
            allowed_departments=allowed_departments,
            hidden_departments=hidden_departments,
            respect_hidden=respect_hidden,
            dept_scope=dept_scope,
        )
        overdue_count = count_overdue_bars(bars)
        overdue_rows = tuple(collect_overdue_entity_rows(bars))
        upcoming = tuple(collect_upcoming_due_rows(bars))
        (
            total_bars,
            done_count,
            in_progress_count,
            waiting_count,
            blocked_count,
            completion_pct,
            _,
        ) = _summarize_bars(bars, root)
        # Department workload: Schedule universe, both scopes, all dept levels.
        workload_allow = workload_departments if workload_departments is not None else allowed_departments
        bars_workload = build_metrics_planned_bars(
            root,
            project_index,
            schedule,
            include_shots=True,
            include_assets=True,
            allowed_departments=workload_allow,
            hidden_departments=hidden_departments,
            respect_hidden=respect_hidden,
            dept_scope=DEPT_SCOPE_ALL,
        )
        dept_stats = _summarize_dept_workload(
            bars_workload,
            root,
            project_index=project_index,
            universe_ids=workload_department_order or tuple(workload_allow or ()),
            shot_dept_ids=workload_shot_departments or set(),
            asset_dept_ids=workload_asset_departments or set(),
            hidden_departments=hidden_departments,
            respect_hidden=respect_hidden,
            workload_allowed_departments=workload_allow,
        )
        dept_workload_overdue = tuple(collect_overdue_entity_rows(bars_workload))
        dept_workload_upcoming = tuple(collect_upcoming_due_rows(bars_workload))

    return DashboardSnapshot(
        assets_count=len(assets),
        shots_count=len(shots),
        open_notes_count=len(open_notes),
        mention_notes_count=len(mention_notes),
        unread_mention_count=unread_mention_count,
        overdue_count=overdue_count,
        overdue_entities=tuple(collect_overdue_entity_keys(bars)),
        overdue_entity_rows=overdue_rows,
        unscheduled_entities=tuple(
            unscheduled_keys := collect_unscheduled_entity_keys(
                schedule,
                shot_paths=shot_rels,
                asset_paths=asset_rels,
                bars=bars,
            )
        ),
        unscheduled_count=len(unscheduled_keys),
        allocation_count=len(schedule.targets) + len(schedule.waves) + len(schedule.allocations),
        open_notes=tuple(open_notes),
        mention_notes=tuple(mention_notes),
        upcoming_due=upcoming,
        dept_workload_overdue_rows=dept_workload_overdue,
        dept_workload_upcoming_due=dept_workload_upcoming,
        total_bars=total_bars,
        done_count=done_count,
        in_progress_count=in_progress_count,
        waiting_count=waiting_count,
        blocked_count=blocked_count,
        completion_pct=completion_pct,
        dept_stats=dept_stats,
    )


def _summarize_bars(
    bars: dict,
    project_root: Path,
    *,
    today: date | None = None,
) -> tuple[int, int, int, int, int, float, tuple[DashboardDeptStat, ...]]:
    """Derive pipeline-health counts + per-department roll-ups from planned bars."""
    ref = today or date.today()
    horizon = ref + timedelta(days=7)
    done = in_progress = waiting = blocked = 0
    in_scope = 0
    status_regs: dict[str, object] = {}
    # dept_id -> [label, total, done, in_progress, overdue, waiting, due_soon, color_hex]
    by_dept: dict[str, list] = {}
    for bar in bars.values():
        if bar.status == STATUS_EXCLUDED:
            continue
        in_scope += 1
        is_done = bar.status == STATUS_DONE or bar.goal_met
        if is_done:
            done += 1
        elif bar.status == STATUS_PROGRESS:
            in_progress += 1
        else:
            waiting += 1
        if bar_has_blocked_status(bar, project_root, reg_cache=status_regs):
            blocked += 1
        dep = (bar.department or "").strip() or "unknown"
        slot = by_dept.get(dep)
        if slot is None:
            slot = [bar.department_label or dep, 0, 0, 0, 0, 0, 0, bar.color_hex]
            by_dept[dep] = slot
        slot[1] += 1
        if is_done:
            slot[2] += 1
        elif bar.status == STATUS_PROGRESS:
            slot[3] += 1
        elif bar.overdue:
            slot[4] += 1
        else:
            slot[5] += 1
        if (
            not is_done
            and bar.status != STATUS_EXCLUDED
            and ref <= bar.due <= horizon
        ):
            slot[6] += 1
        if bar.overdue:
            slot[7] = "#ef4444"
        elif bar.status == STATUS_PROGRESS:
            slot[7] = bar.color_hex
        elif slot[4] == 0 and slot[6] > 0:
            slot[7] = "#60a5fa"

    total = in_scope
    completion = (done / in_scope * 100.0) if in_scope else 0.0
    stats: list[DashboardDeptStat] = []
    for dep, (label, tot, dn, ip, ov, wt, due_soon, color) in by_dept.items():
        stats.append(
            DashboardDeptStat(
                department=dep,
                department_label=label,
                total=tot,
                done=dn,
                in_progress=ip,
                overdue=ov,
                waiting=wt,
                due_soon=due_soon,
                color_hex=color,
            )
        )
    stats.sort(
        key=lambda s: (-s.overdue, -s.due_soon, s.completion_pct, s.department_label.lower())
    )
    return total, done, in_progress, waiting, blocked, completion, tuple(stats)


def _summarize_dept_workload(
    bars: dict,
    project_root: Path,
    *,
    project_index=None,
    universe_ids: tuple[str, ...],
    shot_dept_ids: set[str],
    asset_dept_ids: set[str],
    hidden_departments: set[str] | None = None,
    respect_hidden: bool = True,
    workload_allowed_departments: set[str] | None = None,
    today: date | None = None,
) -> tuple[DashboardDeptStat, ...]:
    """Roll up workload per Schedule department, split by shot/asset, fill empty universe rows."""
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.schedule_skip import (
        ScheduleSkipResolver,
        department_should_show_in_workload,
    )

    ref = today or date.today()
    horizon = ref + timedelta(days=7)
    dept_reg = DepartmentRegistry.for_project(project_root)
    status_regs: dict[str, object] = {}
    hidden = hidden_departments or set()
    # dep -> mutable slot
    slots: dict[str, dict[str, object]] = {}

    def _slot(dep: str, label: str, color: str) -> dict[str, object]:
        existing = slots.get(dep)
        if existing is not None:
            return existing
        created: dict[str, object] = {
            "label": label,
            "total": 0,
            "done": 0,
            "in_progress": 0,
            "overdue": 0,
            "waiting": 0,
            "due_soon": 0,
            "color": color,
            "shots_total": 0,
            "assets_total": 0,
            "shots_due_soon": 0,
            "assets_due_soon": 0,
            "shots_overdue": 0,
            "assets_overdue": 0,
        }
        slots[dep] = created
        return created

    for bar in bars.values():
        if bar.status == STATUS_EXCLUDED:
            continue
        dep = (bar.department or "").strip() or "unknown"
        is_shot = (bar.entity_kind or "").strip().lower() == "shot"
        is_done = bar.status == STATUS_DONE or bar.goal_met
        slot = _slot(dep, bar.department_label or dep, bar.color_hex)
        slot["total"] = int(slot["total"]) + 1
        if is_shot:
            slot["shots_total"] = int(slot["shots_total"]) + 1
        else:
            slot["assets_total"] = int(slot["assets_total"]) + 1
        if is_done:
            slot["done"] = int(slot["done"]) + 1
        elif bar.status == STATUS_PROGRESS:
            slot["in_progress"] = int(slot["in_progress"]) + 1
        elif bar.overdue:
            slot["overdue"] = int(slot["overdue"]) + 1
            if is_shot:
                slot["shots_overdue"] = int(slot["shots_overdue"]) + 1
            else:
                slot["assets_overdue"] = int(slot["assets_overdue"]) + 1
        else:
            slot["waiting"] = int(slot["waiting"]) + 1
        due_soon = (
            not is_done
            and bar.status != STATUS_EXCLUDED
            and ref <= bar.due <= horizon
        )
        if due_soon:
            slot["due_soon"] = int(slot["due_soon"]) + 1
            if is_shot:
                slot["shots_due_soon"] = int(slot["shots_due_soon"]) + 1
            else:
                slot["assets_due_soon"] = int(slot["assets_due_soon"]) + 1
        if bar.overdue:
            slot["color"] = "#ef4444"
        elif bar.status == STATUS_PROGRESS:
            slot["color"] = bar.color_hex
        elif int(slot["overdue"]) == 0 and int(slot["due_soon"]) > 0:
            slot["color"] = "#60a5fa"
        if bar_has_blocked_status(bar, project_root, reg_cache=status_regs):
            pass

    def _visible_dep(dep: str) -> bool:
        return not (respect_hidden and dep in hidden)

    skip_resolver: ScheduleSkipResolver | None = None

    def _show_dep_in_workload(dep: str, *, total: int) -> bool:
        if total > 0:
            return True
        if project_index is None:
            return True
        nonlocal skip_resolver
        if skip_resolver is None:
            skip_resolver = ScheduleSkipResolver(project_root)
        return department_should_show_in_workload(
            skip_resolver,
            project_index,
            dep,
            include_shots=True,
            include_assets=True,
            hidden_departments=hidden,
            respect_hidden=respect_hidden,
            dept_scope=DEPT_SCOPE_ALL,
            dept_reg=dept_reg,
            allowed_departments=workload_allowed_departments,
        )

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for dep in universe_ids:
        d = (dep or "").strip()
        if not d or d in seen or not _visible_dep(d):
            continue
        seen.add(d)
        ordered_ids.append(d)
    for dep in sorted(slots.keys(), key=lambda s: s.lower()):
        if dep not in seen and _visible_dep(dep):
            ordered_ids.append(dep)

    stats: list[DashboardDeptStat] = []
    for dep in ordered_ids:
        slot = slots.get(dep)
        label = dept_reg.get_department_label(dep) or dep
        tot_preview = int(slot["total"]) if slot is not None else 0
        if not _show_dep_in_workload(dep, total=tot_preview):
            continue
        if slot is None:
            stats.append(
                DashboardDeptStat(
                    department=dep,
                    department_label=label,
                    total=0,
                    done=0,
                    in_progress=0,
                    overdue=0,
                    waiting=0,
                    due_soon=0,
                    color_hex="#71717a",
                    applies_to_shots=dep in shot_dept_ids,
                    applies_to_assets=dep in asset_dept_ids,
                )
            )
            continue
        tot = int(slot["total"])
        dn = int(slot["done"])
        stats.append(
            DashboardDeptStat(
                department=dep,
                department_label=str(slot["label"]) or label,
                total=tot,
                done=dn,
                in_progress=int(slot["in_progress"]),
                overdue=int(slot["overdue"]),
                waiting=int(slot["waiting"]),
                due_soon=int(slot["due_soon"]),
                color_hex=str(slot["color"]),
                shots_total=int(slot["shots_total"]),
                assets_total=int(slot["assets_total"]),
                shots_due_soon=int(slot["shots_due_soon"]),
                assets_due_soon=int(slot["assets_due_soon"]),
                shots_overdue=int(slot["shots_overdue"]),
                assets_overdue=int(slot["assets_overdue"]),
                applies_to_shots=dep in shot_dept_ids,
                applies_to_assets=dep in asset_dept_ids,
            )
        )

    stats.sort(
        key=lambda s: (
            0 if s.total > 0 else 1,
            -s.overdue,
            -s.due_soon,
            s.completion_pct if s.total else 100.0,
            s.department_label.lower(),
        )
    )
    return tuple(stats)
