"""Department visibility rules for Schedule (hidden depts, leaf/root scope, wave grouping)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings

if TYPE_CHECKING:
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.project_schedule import TimelineEntityGroup, TimelineRow

INSPECTOR_HIDDEN_DEPTS_KEY = "inspector/hidden_departments"
SCHEDULE_DEPT_SCOPE_KEY = "schedule/dept_scope"
SCHEDULE_RESPECT_HIDDEN_KEY = "schedule/respect_inspector_hidden"
SCHEDULE_BAR_LABEL_KEY = "schedule/bar_label_mode"

BAR_LABEL_DAYS = "days"
BAR_LABEL_DATE_RANGE = "date_range"
BAR_LABEL_ENTITY_NAME = "entity_name"
BAR_LABEL_DEPARTMENT = "department"
BAR_LABEL_OFF = "off"
BAR_LABEL_DEFAULT = BAR_LABEL_DAYS

_VALID_BAR_LABEL_MODES = frozenset(
    {
        BAR_LABEL_DAYS,
        BAR_LABEL_DATE_RANGE,
        BAR_LABEL_ENTITY_NAME,
        BAR_LABEL_DEPARTMENT,
        BAR_LABEL_OFF,
    }
)


def normalize_bar_label_mode(mode: str | None) -> str:
    m = (mode or "").strip()
    return m if m in _VALID_BAR_LABEL_MODES else BAR_LABEL_DEFAULT


DEPT_SCOPE_ALL = "all"
DEPT_SCOPE_LEAF = "leaf"
DEPT_SCOPE_ROOT = "root"


def load_inspector_hidden_departments(settings: QSettings | None = None) -> set[str]:
    s = settings or QSettings()
    raw = s.value(INSPECTOR_HIDDEN_DEPTS_KEY, [], list)
    return {x.strip() for x in raw if isinstance(x, str) and x.strip()}


def department_visible_in_schedule(
    dept_id: str,
    scope: str,
    dept_reg: DepartmentRegistry,
) -> bool:
    dep = (dept_id or "").strip()
    if not dep:
        return False
    if scope == DEPT_SCOPE_LEAF:
        return dept_reg.is_subdepartment(dep) or not dept_reg.has_child_departments(dep)
    if scope == DEPT_SCOPE_ROOT:
        return not dept_reg.is_subdepartment(dep)
    return True


def filter_timeline_row(
    row: "TimelineRow",
    *,
    hidden_departments: set[str],
    dept_scope: str,
    dept_reg: DepartmentRegistry,
    respect_hidden: bool,
) -> bool:
    dep = (row.department or "").strip()
    if not dep:
        return False
    if respect_hidden and dep in hidden_departments:
        return False
    return department_visible_in_schedule(dep, dept_scope, dept_reg)


def filter_entity_groups(
    groups: list[TimelineEntityGroup],
    *,
    hidden_departments: set[str],
    dept_scope: str,
    dept_reg: DepartmentRegistry,
    respect_hidden: bool,
) -> list[TimelineEntityGroup]:
    from monostudio.core.project_schedule import TimelineEntityGroup

    out: list[TimelineEntityGroup] = []
    for group in groups:
        kept = tuple(
            d
            for d in group.departments
            if filter_timeline_row(
                d,
                hidden_departments=hidden_departments,
                dept_scope=dept_scope,
                dept_reg=dept_reg,
                respect_hidden=respect_hidden,
            )
        )
        if not kept:
            continue
        out.append(
            TimelineEntityGroup(
                entity_kind=group.entity_kind,
                entity_rel=group.entity_rel,
                entity_name=group.entity_name,
                departments=kept,
            )
        )
    return out


def filter_groups_by_allowed_departments(
    groups: list[TimelineEntityGroup],
    allowed: set[str] | frozenset[str] | None,
) -> list[TimelineEntityGroup]:
    """Sidebar picker whitelist: drop department rows not in ``allowed`` (silent)."""
    if allowed is None:
        return groups
    allow = {d.strip() for d in allowed if isinstance(d, str) and d.strip()}
    if not allow:
        return []
    from monostudio.core.project_schedule import TimelineEntityGroup

    out: list[TimelineEntityGroup] = []
    for group in groups:
        kept = tuple(
            d for d in group.departments if (d.department or "").strip() in allow
        )
        if not kept:
            continue
        out.append(
            TimelineEntityGroup(
                entity_kind=group.entity_kind,
                entity_rel=group.entity_rel,
                entity_name=group.entity_name,
                departments=kept,
            )
        )
    return out


def wave_rollup_department_id(
    dept_id: str,
    *,
    group_by_parent: bool,
    dept_reg: DepartmentRegistry,
) -> str:
    dep = (dept_id or "").strip()
    if not group_by_parent:
        return dep
    parent = dept_reg.get_parent(dep)
    return (parent or dep).strip() or dep


def rollup_label(dept_id: str, dept_reg: DepartmentRegistry) -> str:
    return dept_reg.get_department_label(dept_id) or dept_id
