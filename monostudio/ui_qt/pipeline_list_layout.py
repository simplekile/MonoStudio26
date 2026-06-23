# Column layout for Pipeline List Row view (Finder-style x-offsets, not QTableView columns).

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QRect

from monostudio.ui_qt.pipeline_row_paint import list_header_column_width


class ListSlot(str, Enum):
    INDEX = "index"
    THUMB = "thumb"
    NAME = "name"
    NOTES = "notes"
    DCC = "dcc"
    HEALTH = "health"
    REF = "ref"
    CONCEPT = "concept"
    STATUS = "status"
    DUE = "due"
    VERSION = "version"
    LAST_UPDATED = "last_updated"
    ASSIGNEE = "assignee"
    ASSETS = "assets"
    SHOTS = "shots"
    PATH = "path"


_DEFAULT_WIDTHS: dict[ListSlot, int] = {
    ListSlot.INDEX: 36,
    ListSlot.THUMB: 56,
    ListSlot.NAME: 200,
    ListSlot.NOTES: 44,
    ListSlot.DCC: 120,
    ListSlot.HEALTH: 52,
    ListSlot.REF: 40,
    ListSlot.CONCEPT: 52,
    ListSlot.STATUS: 120,
    ListSlot.DUE: 88,
    ListSlot.VERSION: 72,
    ListSlot.LAST_UPDATED: 120,
    ListSlot.ASSIGNEE: 84,
    ListSlot.ASSETS: 72,
    ListSlot.SHOTS: 72,
    ListSlot.PATH: 200,
}

_PROJECT_SLOTS: tuple[ListSlot, ...] = (
    ListSlot.INDEX,
    ListSlot.THUMB,
    ListSlot.NAME,
    ListSlot.STATUS,
    ListSlot.ASSETS,
    ListSlot.SHOTS,
    ListSlot.LAST_UPDATED,
    ListSlot.PATH,
)

_ASSET_SHOT_SLOTS: tuple[ListSlot, ...] = (
    ListSlot.INDEX,
    ListSlot.THUMB,
    ListSlot.NAME,
    ListSlot.NOTES,
    ListSlot.DCC,
    ListSlot.HEALTH,
    ListSlot.REF,
    ListSlot.CONCEPT,
    ListSlot.STATUS,
    ListSlot.DUE,
    ListSlot.VERSION,
    ListSlot.LAST_UPDATED,
    ListSlot.ASSIGNEE,
)

_STICKY_SLOTS: tuple[ListSlot, ...] = (ListSlot.INDEX, ListSlot.THUMB, ListSlot.NAME)

_SLOT_HEADERS: dict[ListSlot, str] = {
    ListSlot.INDEX: "",
    ListSlot.THUMB: "",
    ListSlot.NAME: "Name",
    ListSlot.NOTES: "Notes",
    ListSlot.DCC: "DCC",
    ListSlot.HEALTH: "Health",
    ListSlot.REF: "Ref",
    ListSlot.CONCEPT: "Concept",
    ListSlot.STATUS: "Status",
    ListSlot.DUE: "Due",
    ListSlot.VERSION: "Version",
    ListSlot.LAST_UPDATED: "Last Updated",
    ListSlot.ASSIGNEE: "Assignee",
    ListSlot.ASSETS: "Assets",
    ListSlot.SHOTS: "Shots",
    ListSlot.PATH: "Path",
}


@dataclass
class PipelineListLayout:
    """Fixed x-offset column layout for a browser context."""

    context: str
    widths: dict[ListSlot, int] = field(default_factory=dict)
    hidden: set[ListSlot] = field(default_factory=set)

    @classmethod
    def for_context(cls, context: str) -> PipelineListLayout:
        slots = _PROJECT_SLOTS if context == "project" else _ASSET_SHOT_SLOTS
        widths = {slot: _DEFAULT_WIDTHS[slot] for slot in slots}
        hidden: set[ListSlot] = set()
        if context == "project":
            hidden.add(ListSlot.PATH)
        layout = cls(context=context, widths=widths, hidden=hidden)
        layout._apply_header_mins()
        return layout

    def slots(self) -> tuple[ListSlot, ...]:
        return _PROJECT_SLOTS if self.context == "project" else _ASSET_SHOT_SLOTS

    def visible_slots(self) -> tuple[ListSlot, ...]:
        return tuple(s for s in self.slots() if s not in self.hidden)

    def sticky_slots(self) -> tuple[ListSlot, ...]:
        visible = set(self.visible_slots())
        return tuple(s for s in _STICKY_SLOTS if s in visible)

    def scrollable_slots(self) -> tuple[ListSlot, ...]:
        sticky = set(self.sticky_slots())
        return tuple(s for s in self.visible_slots() if s not in sticky)

    def sticky_width(self) -> int:
        return sum(self.widths.get(s, 0) for s in self.sticky_slots())

    def content_x_for_viewport_pos(self, pos_x: int, row_rect_left: int, *, scroll_x: int) -> int:
        """Map viewport x to absolute column content x (sticky zone ignores scroll)."""
        sticky_w = self.sticky_width()
        if pos_x < sticky_w:
            return max(0, pos_x)
        return max(0, scroll_x + pos_x - row_rect_left)

    def headers(self) -> list[str]:
        return [_SLOT_HEADERS.get(s, "") for s in self.slots()]

    def header_label(self, slot: ListSlot) -> str:
        return _SLOT_HEADERS.get(slot, "")

    def set_width(self, slot: ListSlot, width: int) -> None:
        if slot in self.widths:
            self.widths[slot] = max(24, int(width))

    def set_status_width(self, width: int) -> None:
        self.set_width(ListSlot.STATUS, max(88, int(width)))

    def total_width(self) -> int:
        return sum(self.widths[s] for s in self.visible_slots())

    def slot_at(self, x: int, *, scroll_x: int = 0) -> ListSlot | None:
        """Return slot under content x (viewport coords + scroll offset)."""
        return self.slot_at_content_x(x + scroll_x if scroll_x else x)

    def slot_at_content_x(self, content_x: int) -> ListSlot | None:
        """Return slot at absolute x within the row content (0 = first column)."""
        cursor = 0
        for slot in self.visible_slots():
            w = self.widths.get(slot, 0)
            if w <= 0:
                continue
            if cursor <= content_x < cursor + w:
                return slot
            cursor += w
        return None

    def slot_rect(self, row_rect: QRect, slot: ListSlot) -> QRect:
        x = row_rect.left()
        for s in self.visible_slots():
            w = self.widths.get(s, 0)
            if s == slot:
                return QRect(x, row_rect.top(), w, row_rect.height())
            x += w
        return QRect()

    def _apply_header_mins(self) -> None:
        for slot in self.slots():
            label = _SLOT_HEADERS.get(slot, "")
            if not label:
                continue
            min_w = list_header_column_width(label)
            if slot in (ListSlot.HEALTH, ListSlot.NOTES):
                min_w = max(min_w, self.widths.get(slot, 0))
            self.widths[slot] = max(self.widths.get(slot, 0), min_w)
