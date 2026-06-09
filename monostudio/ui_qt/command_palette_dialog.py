"""Ctrl+` command palette — jump to pages, projects, assets, shots, inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QKeyEvent
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
    kind: str  # page | quick | entity | project | inbox
    payload: dict[str, Any]
    search_text: str = ""


class CommandPaletteDialog(MonosDialog):
    """Filterable jump list: pages, quick views, workspace projects, assets/shots, inbox."""

    page_selected = Signal(str)
    quick_slot_selected = Signal(object)
    entity_selected = Signal(object)
    project_selected = Signal(str)
    inbox_selected = Signal(str)

    def __init__(
        self,
        *,
        settings: QSettings,
        entities: list[dict[str, Any]] | None = None,
        projects: list[dict[str, Any]] | None = None,
        inbox_items: list[dict[str, Any]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Go to…")
        self._settings = settings
        self._rows = self._build_rows(entities or [], projects or [], inbox_items or [])

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._search = QLineEdit(self)
        self._search.setObjectName("CommandPaletteSearch")
        self._search.setPlaceholderText("Search pages, projects, assets, inbox…")
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)

        self._list = QListWidget(self)
        self._list.setObjectName("CommandPaletteList")
        self._list.setSpacing(2)
        root.addWidget(self._list, 1)

        hint = QLabel("Ctrl+` · ↑↓ navigate · Enter open · Esc close", self)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        self._populate_list(self._rows)
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)
        self._list.itemActivated.connect(self._on_item_activated)
        self.resize(580, 460)

    def _build_rows(
        self,
        entities: list[dict[str, Any]],
        projects: list[dict[str, Any]],
        inbox_items: list[dict[str, Any]],
    ) -> list[_PaletteRow]:
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
        for proj in projects:
            title = (proj.get("title") or "").strip()
            path = (proj.get("path") or "").strip()
            if not title or not path:
                continue
            subtitle = (proj.get("subtitle") or "Project").strip()
            search_text = (proj.get("search_text") or f"{title} {subtitle} {path}").casefold()
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="project",
                    payload={"path": path},
                    search_text=search_text,
                )
            )
        for ent in entities:
            title = (ent.get("title") or "").strip()
            path = (ent.get("path") or "").strip()
            ctx = (ent.get("context") or "").strip()
            if not title or not path or ctx not in ("Assets", "Shots"):
                continue
            subtitle = (ent.get("subtitle") or ctx).strip()
            search_text = (ent.get("search_text") or f"{title} {subtitle} {path}").casefold()
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="entity",
                    payload={"context": ctx, "path": path},
                    search_text=search_text,
                )
            )
        for item in inbox_items:
            title = (item.get("title") or "").strip()
            path = (item.get("path") or "").strip()
            if not title or not path:
                continue
            subtitle = (item.get("subtitle") or "Inbox").strip()
            search_text = (item.get("search_text") or f"{title} {subtitle} {path}").casefold()
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="inbox",
                    payload={"path": path},
                    search_text=search_text,
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

    def _row_matches(self, row: _PaletteRow, q: str) -> bool:
        if not q:
            return True
        if q in row.title.casefold() or q in row.subtitle.casefold():
            return True
        if row.search_text and q in row.search_text:
            return True
        return q in str(row.payload.get("path", "")).casefold()

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().casefold()
        if not q:
            self._populate_list(self._rows)
            return
        filtered = [row for row in self._rows if self._row_matches(row, q)]
        self._populate_list(filtered)

    def _activate_current(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._on_item_activated(item)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._list.count() > 0:
            row = self._list.currentRow()
            if row < 0:
                row = 0
            if key == Qt.Key.Key_Down:
                self._list.setCurrentRow(min(row + 1, self._list.count() - 1))
            else:
                self._list.setCurrentRow(max(row - 1, 0))
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._search.hasFocus() or self._list.hasFocus():
                self._activate_current()
                event.accept()
                return
        super().keyPressEvent(event)

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
        elif row.kind == "project":
            path = (row.payload.get("path") or "").strip()
            if path:
                self.project_selected.emit(path)
        elif row.kind == "inbox":
            path = (row.payload.get("path") or "").strip()
            if path:
                self.inbox_selected.emit(path)
        self.accept()
