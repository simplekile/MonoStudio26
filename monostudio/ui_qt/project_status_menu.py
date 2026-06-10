"""Project browser status picker (workspace Projects grid/list)."""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont

from monostudio.core.workspace_reader import (
    project_status_color_hex,
    project_status_menu_entries,
    read_project_status_override,
)
from monostudio.ui_qt.production_status_menu import _add_section_header, _menu_status_dot_icon
from monostudio.ui_qt.style import MonosMenu


def pick_project_status_at(
    parent,
    global_pos: QPoint,
    *,
    project_root,
    current_status: str,
) -> object:
    """
    Exec menu at global_pos.

    Returns:
      False — cancelled
      None — Automatic (clear manual override)
      str — chosen status key (WAITING, PROGRESS, …)
    """
    from pathlib import Path

    root = Path(project_root)
    has_override = read_project_status_override(root) is not None
    menu = MonosMenu(parent)
    menu.setObjectName("ProjectStatusMenu")
    menu.setToolTipsVisible(True)

    _add_section_header(menu, "", is_default=True)
    act_auto = menu.addAction("Automatic (from pipeline)")
    act_auto.setData("__auto__")
    act_auto.setIcon(_menu_status_dot_icon("#71717a"))
    act_auto.setToolTip("Derived from folders and schedule; clears manual override.")
    if not has_override:
        font = act_auto.font()
        font.setWeight(QFont.Weight.DemiBold)
        act_auto.setFont(font)

    menu.addSeparator()
    for key, label in project_status_menu_entries():
        act = menu.addAction(label)
        act.setData(key)
        act.setIcon(_menu_status_dot_icon(project_status_color_hex(key)))
        if has_override and (current_status or "").strip().upper() == key:
            font = act.font()
            font.setWeight(QFont.Weight.DemiBold)
            act.setFont(font)

    picked = menu.exec(global_pos)
    if picked is None:
        return False
    data = picked.data()
    if data == "__auto__":
        return None
    if isinstance(data, str) and data.strip():
        return data.strip().upper()
    return False
