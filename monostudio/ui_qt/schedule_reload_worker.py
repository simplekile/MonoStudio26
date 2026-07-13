"""Prepare Schedule gantt data off the UI thread."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from monostudio.core.fs_reader import ProjectIndex
from monostudio.core.project_schedule import (
    ProjectSchedule,
    TimelineEntityGroup,
    build_timeline_entity_groups,
    entity_rel_path,
    read_project_schedule,
)
from monostudio.core.schedule_planner import BarStore, build_planned_bars


@dataclass(frozen=True)
class ScheduleReloadPrepared:
    schedule: ProjectSchedule
    all_groups: list[TimelineEntityGroup]
    bars: BarStore
    dept_order: list[str]
    asset_type_by_rel: dict[str, str]


def prepare_schedule_reload(
    project_root: Path,
    project_index: ProjectIndex,
    *,
    include_shots: bool,
    include_assets: bool,
) -> ScheduleReloadPrepared:
    root = project_root.resolve()
    schedule = read_project_schedule(root)
    all_groups = build_timeline_entity_groups(
        root,
        project_index,
        include_shots=include_shots,
        include_assets=include_assets,
    )
    bars = build_planned_bars(
        root,
        project_index,
        schedule,
        include_shots=include_shots,
        include_assets=include_assets,
    )
    from monostudio.core.department_registry import DepartmentRegistry

    dept_reg = DepartmentRegistry.for_project(root)
    dept_order = list(dept_reg.get_departments())
    asset_type_by_rel: dict[str, str] = {}
    for asset in project_index.assets:
        rel = entity_rel_path(root, asset.path).replace("\\", "/")
        asset_type_by_rel[rel] = (asset.asset_type or "").strip().casefold()
    return ScheduleReloadPrepared(
        schedule=schedule,
        all_groups=all_groups,
        bars=bars,
        dept_order=dept_order,
        asset_type_by_rel=asset_type_by_rel,
    )
