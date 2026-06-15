"""Skip state for Schedule — synced with asset/shot production status (``omitted``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from monostudio.core.department_status_registry import load_status_registry_for_department
from monostudio.core.production_status import (
    SKIPPED_STATUS_ID,
    ProductionStatusRegistry,
    effective_status_id_for_department,
    override_status_id_for_department,
)
from monostudio.core.schedule_dept_filter import filter_timeline_row

if TYPE_CHECKING:
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.fs_reader import ProjectIndex
    from monostudio.core.models import Asset, Shot
    from monostudio.core.project_schedule import TimelineEntityGroup, TimelineRow


@dataclass(frozen=True)
class SkippedScheduleRow:
    """One list row — fully skipped item or a single skipped department."""

    entity_kind: str
    entity_rel: str
    entity_name: str
    scope: str  # item | department
    department: str
    department_label: str


@dataclass(frozen=True)
class SkippedScheduleSnapshot:
    """Counts + rows for the Schedule skipped metric and list dialog."""

    item_count: int
    department_count: int
    rows: tuple[SkippedScheduleRow, ...]

    @property
    def total_count(self) -> int:
        return self.item_count + self.department_count


_EMPTY_SNAPSHOT = SkippedScheduleSnapshot(item_count=0, department_count=0, rows=())


class ScheduleSkipResolver:
    """Resolve omitted / skipped departments from scanned entity status overrides."""

    def __init__(self, project_root: Path | None) -> None:
        self._root = Path(project_root) if project_root else None
        self._reg_cache: dict[str, ProductionStatusRegistry] = {}

    def registry_for(self, department: str) -> ProductionStatusRegistry | None:
        if self._root is None:
            return None
        dep = (department or "").strip()
        if not dep:
            return None
        if dep not in self._reg_cache:
            self._reg_cache[dep] = load_status_registry_for_department(self._root, dep)
        return self._reg_cache[dep]

    def is_department_skipped(self, ref: Asset | Shot, department: str) -> bool:
        dep = (department or "").strip()
        if not dep:
            return False
        reg = self.registry_for(dep)
        if reg is None:
            return False
        for d in ref.departments:
            if (d.name or "").strip() != dep:
                continue
            oid = override_status_id_for_department(ref, dep)
            sid = effective_status_id_for_department(d, oid, reg)
            return sid == SKIPPED_STATUS_ID
        return False

    def schedule_department_ids_for_entity(
        self,
        ref: Asset | Shot,
        *,
        hidden_departments: set[str],
        dept_scope: str,
        dept_reg: DepartmentRegistry,
        respect_hidden: bool,
        allowed_departments: set[str] | None = None,
    ) -> list[str]:
        """Department ids on this entity that appear on the schedule timeline."""
        from monostudio.core.project_schedule import TimelineRow

        out: list[str] = []
        for d in ref.departments:
            dep = (d.name or "").strip()
            if not dep:
                continue
            row = TimelineRow(
                entity_kind="shot",
                entity_rel="",
                entity_name="",
                department=dep,
                department_label=dep,
            )
            if not filter_timeline_row(
                row,
                hidden_departments=hidden_departments,
                dept_scope=dept_scope,
                dept_reg=dept_reg,
                respect_hidden=respect_hidden,
            ):
                continue
            if allowed_departments is not None and dep not in allowed_departments:
                continue
            out.append(dep)
        return out

    def is_entity_fully_skipped(
        self,
        ref: Asset | Shot,
        *,
        hidden_departments: set[str],
        dept_scope: str,
        dept_reg: DepartmentRegistry,
        respect_hidden: bool,
        allowed_departments: set[str] | None = None,
    ) -> bool:
        deps = self.schedule_department_ids_for_entity(
            ref,
            hidden_departments=hidden_departments,
            dept_scope=dept_scope,
            dept_reg=dept_reg,
            respect_hidden=respect_hidden,
            allowed_departments=allowed_departments,
        )
        if not deps:
            return False
        return all(self.is_department_skipped(ref, dep) for dep in deps)

    def is_lane_fully_skipped(
        self,
        lane: list[tuple[TimelineEntityGroup, TimelineRow]],
        department: str,
        entity_refs: dict[tuple[str, str], Asset | Shot],
    ) -> bool:
        dep = (department or "").strip()
        if not dep or not lane:
            return False
        checked = False
        for group, _row in lane:
            key = (
                (group.entity_kind or "").strip().lower(),
                (group.entity_rel or "").replace("\\", "/"),
            )
            ref = entity_refs.get(key)
            if ref is None:
                continue
            checked = True
            if not self.is_department_skipped(ref, dep):
                return False
        return checked

    def entity_department_ids(self, ref: Asset | Shot) -> list[str]:
        return [(d.name or "").strip() for d in ref.departments if (d.name or "").strip()]


def department_should_show_in_workload(
    resolver: ScheduleSkipResolver,
    project_index: ProjectIndex,
    department: str,
    *,
    include_shots: bool,
    include_assets: bool,
    hidden_departments: set[str],
    respect_hidden: bool,
    dept_scope: str,
    dept_reg: DepartmentRegistry,
    allowed_departments: set[str] | None = None,
) -> bool:
    """Hide a department row when every entity that has it on Schedule skipped it."""
    dep = (department or "").strip()
    if not dep:
        return False
    found = False
    refs: list[Asset | Shot] = []
    if include_assets:
        refs.extend(project_index.assets)
    if include_shots:
        refs.extend(project_index.shots)
    for ref in refs:
        deps = resolver.schedule_department_ids_for_entity(
            ref,
            hidden_departments=hidden_departments,
            dept_scope=dept_scope,
            dept_reg=dept_reg,
            respect_hidden=respect_hidden,
            allowed_departments=allowed_departments,
        )
        if dep not in deps:
            continue
        found = True
        if not resolver.is_department_skipped(ref, dep):
            return True
    return not found


def count_fully_skipped_entities(
    project_root: Path,
    project_index: ProjectIndex,
    *,
    include_shots: bool,
    include_assets: bool,
    hidden_departments: set[str],
    respect_hidden: bool,
    dept_scope: str,
    dept_reg: DepartmentRegistry,
    allowed_departments: set[str] | None = None,
) -> int:
    resolver = ScheduleSkipResolver(project_root)
    count = 0
    if include_assets:
        for asset in project_index.assets:
            if resolver.is_entity_fully_skipped(
                asset,
                hidden_departments=hidden_departments,
                dept_scope=dept_scope,
                dept_reg=dept_reg,
                respect_hidden=respect_hidden,
                allowed_departments=allowed_departments,
            ):
                count += 1
    if include_shots:
        for shot in project_index.shots:
            if resolver.is_entity_fully_skipped(
                shot,
                hidden_departments=hidden_departments,
                dept_scope=dept_scope,
                dept_reg=dept_reg,
                respect_hidden=respect_hidden,
                allowed_departments=allowed_departments,
            ):
                count += 1
    return count


def build_skipped_schedule_snapshot(
    project_root: Path,
    project_index: ProjectIndex,
    *,
    include_shots: bool,
    include_assets: bool,
    hidden_departments: set[str],
    respect_hidden: bool,
    dept_scope: str,
    dept_reg: DepartmentRegistry,
    allowed_departments: set[str] | None = None,
) -> SkippedScheduleSnapshot:
    """Collect fully skipped items and per-department skip rows for metrics + list."""
    resolver = ScheduleSkipResolver(project_root)
    rows: list[SkippedScheduleRow] = []
    item_count = 0
    department_count = 0

    def _append_entity(ref: Asset | Shot, kind: str, rel: str, name: str) -> None:
        nonlocal item_count, department_count
        schedule_deps = resolver.schedule_department_ids_for_entity(
            ref,
            hidden_departments=hidden_departments,
            dept_scope=dept_scope,
            dept_reg=dept_reg,
            respect_hidden=respect_hidden,
            allowed_departments=allowed_departments,
        )
        if not schedule_deps:
            return
        skipped_deps: list[tuple[str, str]] = []
        for dep in schedule_deps:
            if not resolver.is_department_skipped(ref, dep):
                continue
            label = dept_reg.get_department_label(dep) or dep
            skipped_deps.append((dep, label))
        if not skipped_deps:
            return

        department_count += len(skipped_deps)
        fully_skipped = len(skipped_deps) == len(schedule_deps)
        if fully_skipped:
            item_count += 1
            rows.append(
                SkippedScheduleRow(
                    entity_kind=kind,
                    entity_rel=rel,
                    entity_name=name,
                    scope="item",
                    department="",
                    department_label="All departments",
                )
            )
            return

        for dep, label in skipped_deps:
            rows.append(
                SkippedScheduleRow(
                    entity_kind=kind,
                    entity_rel=rel,
                    entity_name=name,
                    scope="department",
                    department=dep,
                    department_label=label,
                )
            )

    from monostudio.core.project_schedule import entity_rel_path

    root = Path(project_root)
    if include_assets:
        for asset in project_index.assets:
            rel = entity_rel_path(root, asset.path).replace("\\", "/")
            _append_entity(asset, "asset", rel, asset.name or rel.rsplit("/", 1)[-1])
    if include_shots:
        for shot in project_index.shots:
            rel = entity_rel_path(root, shot.path).replace("\\", "/")
            _append_entity(shot, "shot", rel, shot.name or rel.rsplit("/", 1)[-1])

    rows.sort(
        key=lambda r: (
            0 if r.scope == "item" else 1,
            r.entity_name.casefold(),
            r.department_label.casefold(),
        )
    )
    return SkippedScheduleSnapshot(
        item_count=item_count,
        department_count=department_count,
        rows=tuple(rows),
    )


def collect_lane_entity_paths(
    groups: list[TimelineEntityGroup],
    department: str,
    project_root: Path,
) -> list[Path]:
    """All entity folder paths that have ``department`` on the schedule."""
    from monostudio.core.project_schedule import entity_rel_path

    dep = (department or "").strip()
    if not dep:
        return []
    root = Path(project_root)
    out: list[Path] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        key = (
            (group.entity_kind or "").strip().lower(),
            (group.entity_rel or "").replace("\\", "/"),
        )
        if key in seen:
            continue
        if not any((d.department or "").strip() == dep for d in group.departments):
            continue
        seen.add(key)
        out.append(root / group.entity_rel.replace("/", "\\"))
    return out
