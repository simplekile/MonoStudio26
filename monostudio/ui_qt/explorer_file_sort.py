"""Shared sort helpers for explorer grid pages (Inbox, Delivery, Project Guide, …)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu

SORT_FIELD_NAME = "name"
SORT_FIELD_DATE = "date"
SORT_FIELDS = (SORT_FIELD_NAME, SORT_FIELD_DATE)


class _SortableEntry(Protocol):
    label: str
    path: Path


def normalize_explorer_sort_field(raw: str | None) -> str:
    key = (raw or SORT_FIELD_NAME).strip().lower()
    return key if key in SORT_FIELDS else SORT_FIELD_NAME


def _entry_folder_tier(path: Path) -> int:
    """Folders before files when ascending."""
    return 1 if path.is_dir() else 0


def _entry_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def sort_explorer_file_entries(
    entries: list[_SortableEntry],
    *,
    field: str,
    ascending: bool,
) -> list[_SortableEntry]:
    sort_field = normalize_explorer_sort_field(field)
    if sort_field == SORT_FIELD_DATE:

        def key(e: _SortableEntry) -> tuple:
            return (
                _entry_folder_tier(e.path),
                _entry_mtime(e.path),
                (e.label or "").casefold(),
                str(e.path),
            )

    else:

        def key(e: _SortableEntry) -> tuple:
            return (
                _entry_folder_tier(e.path),
                (e.label or "").casefold(),
                str(e.path),
            )

    return sorted(entries, key=key, reverse=not ascending)


@dataclass(frozen=True)
class ExplorerSortMenuSection:
    field_actions: dict[str, QAction]
    ascending: QAction
    descending: QAction


def add_explorer_sort_submenu(
    menu: QMenu,
    *,
    field: str,
    ascending: bool,
    sort_icon: QIcon | None = None,
) -> ExplorerSortMenuSection:
    sub = menu.addMenu(sort_icon, "Sort") if sort_icon is not None else menu.addMenu("Sort")
    field_actions: dict[str, QAction] = {}
    for label, key in (("Name", SORT_FIELD_NAME), ("Modified", SORT_FIELD_DATE)):
        act = sub.addAction(label)
        act.setCheckable(True)
        act.setChecked(normalize_explorer_sort_field(field) == key)
        field_actions[key] = act
    sub.addSeparator()
    asc_act = sub.addAction("Ascending")
    asc_act.setCheckable(True)
    asc_act.setChecked(ascending)
    desc_act = sub.addAction("Descending")
    desc_act.setCheckable(True)
    desc_act.setChecked(not ascending)
    return ExplorerSortMenuSection(
        field_actions=field_actions,
        ascending=asc_act,
        descending=desc_act,
    )


def resolve_explorer_sort_action(
    chosen: QAction | None,
    section: ExplorerSortMenuSection,
    *,
    field: str,
    ascending: bool,
) -> tuple[str, bool] | None:
    if chosen is None:
        return None
    current_field = normalize_explorer_sort_field(field)
    for key, act in section.field_actions.items():
        if chosen is act and key != current_field:
            return (key, ascending)
    if chosen is section.ascending and not ascending:
        return (current_field, True)
    if chosen is section.descending and ascending:
        return (current_field, False)
    return None
