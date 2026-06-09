"""Ctrl+K command palette — jump to pages, quick-view slots, assets, and shots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from monostudio.ui_qt.nav_quick_view import (
    SLOT_COUNT,
    VALID_NAV_CONTEXTS,
    describe_nav_quick_slot,
    load_nav_quick_slot,
)
from monostudio.ui_qt.style import MonosDialog


@dataclass(frozen=True)
class _PaletteRow:
    title: str
    subtitle: str
    kind: str  # page | quick | entity
    payload: dict[str, Any]


class CommandPaletteDialog(MonosDialog):
    """Filterable jump list: pages, quick views, project assets/shots."""

    page_selected = Signal(str)
    quick_slot_selected = Signal(object)
    entity_selected = Signal(object)

    def __init__(
        self,
        *,
        settings: QSettings,
        entities: list[dict[str, Any]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Go to…")
        self._settings = settings
        self._rows = self._build_rows(entities or [])

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._search = QLineEdit(self)
        self._search.setObjectName("CommandPaletteSearch")
        self._search.setPlaceholderText("Search pages, assets, shots…")
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)

        self._list = QListWidget(self)
        self._list.setObjectName("CommandPaletteList")
        self._list.setSpacing(2)
        root.addWidget(self._list, 1)

        hint = QLabel("↑↓ navigate · Enter open · Esc close", self)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        self._populate_list(self._rows)
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)
        self._list.itemActivated.connect(self._on_item_activated)
        self.resize(560, 440)

    def _build_rows(self, entities: list[dict[str, Any]]) -> list[_PaletteRow]:
        rows: list[_PaletteRow] = []
        for ctx in (
            "Dashboard",
            "Assets",
            "Shots",
            "Inbox",
            "Project Guide",
            "Schedule",
            "Outbox",
            "Trash",
        ):
            if ctx in VALID_NAV_CONTEXTS:
                rows.append(_PaletteRow(title=ctx, subtitle="Page", kind="page", payload={"context": ctx}))
        for slot in range(1, SLOT_COUNT + 1):
            payload = load_nav_quick_slot(self._settings, slot)
            if payload is None:
                continue
            summary = describe_nav_quick_slot(payload)
            rows.append(
                _PaletteRow(
                    title=f"Quick view {slot}",
                    subtitle=summary,
                    kind="quick",
                    payload=payload,
                )
            )
        for ent in entities:
            title = (ent.get("title") or "").strip()
            path = (ent.get("path") or "").strip()
            ctx = (ent.get("context") or "").strip()
            if not title or not path or ctx not in ("Assets", "Shots"):
                continue
            subtitle = (ent.get("subtitle") or ctx).strip()
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="entity",
                    payload={"context": ctx, "path": path},
                )
            )
        return rows

    def _populate_list(self, rows: list[_PaletteRow]) -> None:
        self._list.clear()
        for row in rows:
            item = QListWidgetItem(f"{row.title}  —  {row.subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().casefold()
        if not q:
            self._populate_list(self._rows)
            return
        filtered = [
            row
            for row in self._rows
            if q in row.title.casefold() or q in row.subtitle.casefold()
        ]
        self._populate_list(filtered)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, _PaletteRow):
            return
        if row.kind == "page":
            ctx = (row.payload.get("context") or "").strip()
            if ctx:
                self.page_selected.emit(ctx)
        elif row.kind == "quick":
            self.quick_slot_selected.emit(row.payload)
        elif row.kind == "entity":
            self.entity_selected.emit(dict(row.payload))
        self.accept()
