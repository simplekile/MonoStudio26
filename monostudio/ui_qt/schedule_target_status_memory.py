"""Session memory for schedule goal target status (last user choice)."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.department_status_registry import (
    default_target_status_for_department,
    load_status_registry_for_department,
)

_last_target_status_id: str = ""


def remember_target_status(status_id: str) -> None:
    """Record the most recent target status the user chose on any schedule bar."""
    global _last_target_status_id
    sid = (status_id or "").strip()
    if sid:
        _last_target_status_id = sid


def last_target_status_id() -> str:
    return _last_target_status_id


def resolve_target_status_for_new_goal(project_root: Path, department_id: str) -> str:
    """Pick target for a newly drawn goal: last user choice if valid for dept, else default."""
    dep = (department_id or "").strip()
    last = _last_target_status_id.strip()
    if last and dep:
        reg = load_status_registry_for_department(project_root, dep)
        if reg.get(last) is not None and last in reg.menu_status_ids():
            return last
    return default_target_status_for_department(project_root, dep)
