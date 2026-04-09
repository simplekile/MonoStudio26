"""Shared QMenu: Automatic + merged production status presets (Inspector + MainView)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget, QWidgetAction

from monostudio.core.production_status import (
    ProductionStatusRegistry,
    color_hex_for_status_id,
    load_production_status_registry,
)
from monostudio.ui_qt.style import MonosMenu

_MENU_STATUS_DOT_SIZE = 14

# Fallback when JSON has no `tooltip` (custom statuses).
_DEFAULT_STATUS_TOOLTIPS: dict[str, str] = {
    "published": "Shipped or published build.",
    "approved": "Signed off internally.",
    "working": "Active work in progress.",
    "rework": "Needs changes after feedback or review.",
    "waiting": "Not started or waiting for handoff.",
    "internal_review": "Waiting for internal review.",
    "client_review": "Waiting for client approval.",
    "ready_for_delivery": "Content complete; ready to hand off.",
    "on_hold": "Paused on purpose (brief, schedule, etc.).",
    "blocked": "Cannot proceed: tech issue, dependency, etc.",
    "omitted": "Not applicable for this department or shot.",
}

_CATEGORY_TOOLTIP_FALLBACK: dict[str, str] = {
    "blocked": "Work cannot continue until unblocked.",
    "hold": "Paused; not the active priority.",
    "review": "In or awaiting an approval step.",
    "in_progress": "Actively being worked on.",
    "not_started": "Not started or waiting to begin.",
    "done": "Finished, approved, or published.",
    "na": "Not applicable for this item.",
    "unknown": "Status not defined.",
}

_CATEGORY_HEADER_LABEL: dict[str, str] = {
    "blocked": "BLOCKED",
    "hold": "ON HOLD",
    "review": "REVIEW",
    "in_progress": "IN PROGRESS",
    "not_started": "NOT STARTED",
    "done": "DONE",
    "na": "N / A",
}


def _section_header_object_name(category: str) -> str:
    c = (category or "").strip()
    if c in _CATEGORY_HEADER_LABEL:
        return f"ProductionStatusMenuSection_{c}"
    return "ProductionStatusMenuSection_other"


def _menu_status_dot_icon(color_hex: str, *, size: int = _MENU_STATUS_DOT_SIZE) -> QIcon:
    """Small filled circle for QMenu action (status color)."""
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        c = QColor(color_hex)
        if not c.isValid():
            c = QColor("#a1a1aa")
        p.setBrush(c)
        cx = size // 2
        cy = size // 2
        r = max(4, size // 2 - 2)
        p.drawEllipse(QPoint(cx, cy), r, r)
    finally:
        p.end()
    return QIcon(pix)


def _add_section_header(menu: MonosMenu, category: str, *, is_default: bool = False) -> None:
    row = QWidget(menu)
    if is_default:
        row.setObjectName("ProductionStatusMenuSection_default")
        title = "DEFAULT"
    else:
        row.setObjectName(_section_header_object_name(category))
        title = _CATEGORY_HEADER_LABEL.get(
            category, (category or "").replace("_", " ").upper()
        )
    lay = QHBoxLayout(row)
    lay.setContentsMargins(12, 5, 12, 5)
    lay.setSpacing(0)
    lab = QLabel(title)
    lab.setObjectName("ProductionStatusMenuSectionLabel")
    lay.addWidget(lab)
    wa = QWidgetAction(menu)
    wa.setDefaultWidget(row)
    menu.addAction(wa)


def _tooltip_for_status(reg: ProductionStatusRegistry, sid: str) -> str:
    d = reg.get(sid)
    if d and d.tooltip:
        return d.tooltip
    if sid in _DEFAULT_STATUS_TOOLTIPS:
        return _DEFAULT_STATUS_TOOLTIPS[sid]
    cat = reg.category_for(sid)
    return _CATEGORY_TOOLTIP_FALLBACK.get(cat, (d.label if d else sid))


def pick_production_status_at(
    parent: QWidget | None,
    project_root: Path | None,
    global_pos: QPoint,
) -> object:
    """
    Exec menu at global_pos.
    Returns:
      False — cancelled
      None — Automatic (clear override)
      str — chosen status id
    """
    reg = load_production_status_registry(project_root)
    menu = MonosMenu(parent)
    menu.setObjectName("ProductionStatusMenu")

    _add_section_header(menu, "", is_default=True)
    act_auto = menu.addAction("Automatic (from files)")
    act_auto.setData("__auto__")
    act_auto.setIcon(_menu_status_dot_icon("#71717a"))
    act_auto.setToolTip("Derived from work and publish state; clears manual override.")

    for cat, sids in reg.statuses_grouped_for_menu():
        _add_section_header(menu, cat)
        for sid in sids:
            a = menu.addAction(reg.label_for(sid))
            a.setData(sid)
            a.setIcon(_menu_status_dot_icon(color_hex_for_status_id(sid, reg)))
            a.setToolTip(_tooltip_for_status(reg, sid))

    picked = menu.exec(global_pos)
    if picked is None:
        return False
    data = picked.data()
    if data == "__auto__":
        return None
    if isinstance(data, str) and data.strip():
        return data.strip()
    return False
