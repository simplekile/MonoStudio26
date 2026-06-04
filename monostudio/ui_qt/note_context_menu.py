"""Context menu for note cards in ItemNotesDialog."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QWidget

from monostudio.core.item_comments import ItemCommentEntry
from monostudio.ui_qt.lucide_icons import lucide_icon

# Shared with NoteDoneToggleButton (list row) — context menu uses 16px, row toggle 22px.
NOTE_DONE_TOGGLE_ICON_SIZE = 22
_COLOR_NOTE_ACTIVE = "#71717a"  # zinc-500 — empty circle
_COLOR_NOTE_DONE = "#10b981"  # emerald — circle-check


def note_done_toggle_icon(*, done: bool, size: int = NOTE_DONE_TOGGLE_ICON_SIZE) -> QIcon:
    """State icons: active → gray circle; completed → green circle-check."""
    if done:
        return lucide_icon("circle-check", size=size, color_hex=_COLOR_NOTE_DONE)
    return lucide_icon("circle", size=size, color_hex=_COLOR_NOTE_ACTIVE)


def _add_action(
    menu: QMenu,
    *,
    icon_name: str,
    label: str,
    color_hex: str,
    handler: Callable[[], None],
    enabled: bool = True,
    danger: bool = False,
) -> QAction:
    act = QAction(lucide_icon(icon_name, size=16, color_hex=color_hex), label, menu)
    act.setEnabled(enabled)
    if danger:
        act.setProperty("class", "danger-action")
    act.triggered.connect(handler)
    menu.addAction(act)
    return act


def build_note_context_menu(
    parent: QWidget,
    entry: ItemCommentEntry,
    *,
    can_edit: bool,
    can_delete: bool,
    on_view: Callable[[], None],
    on_copy: Callable[[], None],
    on_edit: Callable[[], None],
    on_history: Callable[[], None],
    on_toggle_done: Callable[[], None],
    on_delete: Callable[[], None],
) -> QMenu:
    """Order: view → copy → edit → history → mark done → delete."""
    menu = QMenu(parent)

    _add_action(
        menu,
        icon_name="maximize-2",
        label="View full note…",
        color_hex="#d4d4d8",
        handler=on_view,
    )
    _add_action(
        menu,
        icon_name="copy",
        label="Copy note",
        color_hex="#a1a1aa",
        handler=on_copy,
    )

    menu.addSeparator()

    _add_action(
        menu,
        icon_name="pencil",
        label="Edit note…",
        color_hex="#60a5fa",
        handler=on_edit,
        enabled=can_edit,
    )
    _add_action(
        menu,
        icon_name="history",
        label="Edit history…",
        color_hex="#a1a1aa",
        handler=on_history,
        enabled=bool(entry.edit_history),
    )

    menu.addSeparator()

    if entry.done:
        _add_action(
            menu,
            icon_name="circle",
            label="Mark as active",
            color_hex=_COLOR_NOTE_ACTIVE,
            handler=on_toggle_done,
        )
    else:
        _add_action(
            menu,
            icon_name="circle-check",
            label="Mark as done",
            color_hex=_COLOR_NOTE_DONE,
            handler=on_toggle_done,
        )

    menu.addSeparator()

    _add_action(
        menu,
        icon_name="trash-2",
        label="Delete note…",
        color_hex="#ef4444",
        handler=on_delete,
        enabled=can_delete,
        danger=True,
    )

    return menu
