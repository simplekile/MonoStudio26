"""Houdini-style nav quick view: Ctrl+N assigns page + filter snapshot; N recalls."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)

VALID_NAV_CONTEXTS = frozenset(
    {
        "Dashboard",
        "Assets",
        "Shots",
        "Inbox",
        "Project Guide",
        "Schedule",
        "Outbox",
        "Trash",
    }
)

SLOT_COUNT = 9
_SLOT_COUNT = SLOT_COUNT
_SETTINGS_PREFIX = "ui/nav_quick_slot"


def keyboard_input_blocks_shortcuts() -> bool:
    """True when app-level shortcuts should not fire (text entry or modal)."""
    if QApplication.activeModalWidget() is not None:
        return True
    fw = QApplication.focusWidget()
    if fw is None:
        return False
    if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox)):
        return True
    if isinstance(fw, QComboBox) and fw.isEditable():
        return True
    from PySide6.QtWidgets import QAbstractSpinBox

    return isinstance(fw, QAbstractSpinBox)


def _slot_settings_key(slot: int) -> str:
    return f"{_SETTINGS_PREFIX}/{int(slot)}"


def load_nav_quick_slot(settings: QSettings, slot: int) -> dict[str, Any] | None:
    raw = (settings.value(_slot_settings_key(slot), "", str) or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            ctx = (data.get("context") or "").strip()
            if ctx in VALID_NAV_CONTEXTS:
                return data
        return None
    if raw in VALID_NAV_CONTEXTS:
        return {"context": raw, "filters": None}
    return None


def save_nav_quick_slot(settings: QSettings, slot: int, payload: dict[str, Any]) -> None:
    settings.setValue(_slot_settings_key(slot), json.dumps(payload, ensure_ascii=False))


def contexts_by_slot(settings: QSettings) -> dict[str, list[int]]:
    """Map nav context name -> slot numbers that point at it."""
    out: dict[str, list[int]] = {}
    for slot in range(1, _SLOT_COUNT + 1):
        payload = load_nav_quick_slot(settings, slot)
        if payload is None:
            continue
        ctx = (payload.get("context") or "").strip()
        if ctx:
            out.setdefault(ctx, []).append(slot)
    for ctx in out:
        out[ctx].sort()
    return out


def clear_nav_quick_slot(settings: QSettings, slot: int) -> None:
    settings.remove(_slot_settings_key(slot))


def clear_all_nav_quick_slots(settings: QSettings) -> None:
    for slot in range(1, _SLOT_COUNT + 1):
        settings.remove(_slot_settings_key(slot))


def describe_nav_quick_slot(payload: dict[str, Any] | None) -> str:
    """Short summary for Settings list (page + optional filter ids)."""
    if not payload:
        return "Empty"
    ctx = (payload.get("context") or "").strip() or "?"
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    parts = [ctx]
    type_id = filters.get("active_type")
    dept = filters.get("active_department")
    if isinstance(type_id, str) and type_id.strip():
        parts.append(type_id.strip())
    if isinstance(dept, str) and dept.strip():
        parts.append(dept.strip())
    shots = filters.get("include_shots")
    assets = filters.get("include_assets")
    if shots is not None or assets is not None:
        scope: list[str] = []
        if bool(shots):
            scope.append("shots")
        if bool(assets):
            scope.append("assets")
        if scope:
            parts.append("+".join(scope))
    return " · ".join(parts)


def format_nav_item_tooltip(label: str, slots: list[int] | None, *, include_hint: bool = True) -> str:
    line = (label or "").strip()
    if slots:
        nums = ", ".join(str(s) for s in slots)
        line = f"{line} (Quick view {nums})"
    if include_hint:
        return f"{line}\nCtrl+1–9 assign · 1–9 go"
    return line


class NavQuickViewController:
    def __init__(
        self,
        parent: QWidget,
        settings: QSettings,
        *,
        get_context: Callable[[], str],
        export_filters: Callable[[], dict[str, object]],
        recall_slot: Callable[[dict[str, Any]], None],
        on_assigned: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        self._parent = parent
        self._settings = settings
        self._get_context = get_context
        self._export_filters = export_filters
        self._recall_slot = recall_slot
        self._on_assigned = on_assigned
        self._bind_shortcuts()

    def _bind_shortcuts(self) -> None:
        for slot in range(1, _SLOT_COUNT + 1):
            sc_assign = QShortcut(QKeySequence(f"Ctrl+{slot}"), self._parent)
            sc_assign.setContext(Qt.ShortcutContext.WindowShortcut)
            sc_assign.activated.connect(lambda s=slot: self._assign(s))

            sc_recall = QShortcut(QKeySequence(str(slot)), self._parent)
            sc_recall.setContext(Qt.ShortcutContext.WindowShortcut)
            sc_recall.setAutoRepeat(False)
            sc_recall.activated.connect(lambda s=slot: self._recall(s))

            sc_numpad = QShortcut(QKeySequence(f"Num+{slot}"), self._parent)
            sc_numpad.setContext(Qt.ShortcutContext.WindowShortcut)
            sc_numpad.setAutoRepeat(False)
            sc_numpad.activated.connect(lambda s=slot: self._recall(s))

    def _assign(self, slot: int) -> None:
        if keyboard_input_blocks_shortcuts():
            return
        ctx = (self._get_context() or "").strip()
        if ctx not in VALID_NAV_CONTEXTS:
            return
        filters: dict[str, object] = {}
        if ctx != "Trash":
            filters = dict(self._export_filters())
        payload: dict[str, Any] = {"context": ctx, "filters": filters}
        save_nav_quick_slot(self._settings, slot, payload)
        if self._on_assigned is not None:
            self._on_assigned(slot, payload)

    def _recall(self, slot: int) -> None:
        if keyboard_input_blocks_shortcuts():
            return
        payload = load_nav_quick_slot(self._settings, slot)
        if payload is None:
            return
        self._recall_slot(payload)
