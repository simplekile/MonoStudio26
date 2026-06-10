"""Target status submenu for schedule context menus."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMenu

from monostudio.core.department_status_registry import load_status_registry_for_department
from monostudio.core.production_status import color_hex_for_status_id
from monostudio.ui_qt.production_status_menu import (
    _add_section_header,
    _menu_status_dot_icon,
    _tooltip_for_status,
)
from monostudio.ui_qt.style import MonosMenu


def add_target_status_submenu(
    parent_menu: QMenu,
    *,
    project_root: Path,
    department_id: str,
    title: str = "Target status",
) -> MonosMenu:
    """Add a nested menu of department workflow statuses; returns the submenu."""
    dep = (department_id or "").strip()
    submenu = MonosMenu(parent_menu)
    submenu.setTitle(title)
    submenu.setObjectName("ScheduleTargetStatusMenu")
    parent_menu.addMenu(submenu)
    submenu.setToolTipsVisible(True)
    if not dep:
        act = submenu.addAction("Select a department filter first")
        act.setEnabled(False)
        return submenu

    reg = load_status_registry_for_department(project_root, dep)
    for cat, sids in reg.statuses_grouped_for_menu():
        _add_section_header(submenu, cat)
        for sid in sids:
            act = submenu.addAction(reg.label_for(sid))
            act.setData(sid)
            act.setProperty("schedule_target_dep", dep)
            act.setIcon(_menu_status_dot_icon(color_hex_for_status_id(sid, reg)))
            act.setToolTip(_tooltip_for_status(reg, sid))
    return submenu
