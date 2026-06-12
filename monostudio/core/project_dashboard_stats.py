"""Read-only aggregates for Dashboard (notes, counts)."""
from __future__ import annotations

from dataclasses import dataclass
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
from monostudio.core.schedule_planner import (
    STATUS_DONE,
    STATUS_EXCLUDED,
    STATUS_PROGRESS,
    bar_has_blocked_status,
    build_planned_bars,
    collect_overdue_entity_keys,
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
    """Per-department roll-up across all planned bars (Department Load card)."""

    department: str
    department_label: str
    total: int
    done: int
    in_progress: int
    overdue: int
    color_hex: str

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
    unscheduled_count: int
    unscheduled_entities: tuple[tuple[str, str], ...]  # (entity_kind, entity_rel)
    allocation_count: int
    open_notes: tuple[DashboardNoteRow, ...]
    mention_notes: tuple[DashboardNoteRow, ...]  # open notes @mentioning signed-in user
    upcoming_due: tuple  # UpcomingDueRow
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


def build_dashboard_snapshot(
    project_root: Path | None,
    *,
    assets: tuple,
    shots: tuple,
    workspace_root: Path | None = None,
    project_index=None,
    allowed_departments: set[str] | None = None,
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
    bars: dict = {}
    if project_index is not None:
        bars = build_planned_bars(
            root,
            project_index,
            schedule,
            include_shots=True,
            include_assets=True,
        )
        bars = _filter_bars_by_departments(
            bars,
            root,
            allowed=allowed_departments,
            hidden=hidden_departments or set(),
            respect_hidden=respect_hidden,
            dept_scope=dept_scope,
        )
        overdue_count = count_overdue_bars(bars)
        upcoming = tuple(collect_upcoming_due_rows(bars))
        (
            total_bars,
            done_count,
            in_progress_count,
            waiting_count,
            blocked_count,
            completion_pct,
            dept_stats,
        ) = _summarize_bars(bars, root)

    return DashboardSnapshot(
        assets_count=len(assets),
        shots_count=len(shots),
        open_notes_count=len(open_notes),
        mention_notes_count=len(mention_notes),
        unread_mention_count=unread_mention_count,
        overdue_count=overdue_count,
        overdue_entities=tuple(collect_overdue_entity_keys(bars)),
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
) -> tuple[int, int, int, int, int, float, tuple[DashboardDeptStat, ...]]:
    """Derive pipeline-health counts + per-department roll-ups from planned bars."""
    done = in_progress = waiting = blocked = 0
    in_scope = 0
    status_regs: dict[str, object] = {}
    # dept_id -> [label, total, done, in_progress, overdue, color_hex]
    by_dept: dict[str, list] = {}
    for bar in bars.values():
        if bar.status == STATUS_EXCLUDED:
            continue
        in_scope += 1
        if bar.status == STATUS_DONE:
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
            slot = [bar.department_label or dep, 0, 0, 0, 0, bar.color_hex]
            by_dept[dep] = slot
        slot[1] += 1
        if bar.status == STATUS_DONE:
            slot[2] += 1
        elif bar.status == STATUS_PROGRESS:
            slot[3] += 1
        if bar.overdue:
            slot[4] += 1
        # Prefer a "louder" color for the dept dot: overdue/in-progress over done.
        if bar.overdue or bar.status == STATUS_PROGRESS:
            slot[5] = bar.color_hex

    total = in_scope
    completion = (done / in_scope * 100.0) if in_scope else 0.0
    stats: list[DashboardDeptStat] = []
    for dep, (label, tot, dn, ip, ov, color) in by_dept.items():
        stats.append(
            DashboardDeptStat(
                department=dep,
                department_label=label,
                total=tot,
                done=dn,
                in_progress=ip,
                overdue=ov,
                color_hex=color,
            )
        )
    # Surface the most pressing departments first: overdue, then least complete.
    stats.sort(key=lambda s: (-s.overdue, s.completion_pct, s.department_label.lower()))
    return total, done, in_progress, waiting, blocked, completion, tuple(stats)
