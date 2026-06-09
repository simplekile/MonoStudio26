"""Dashboard bento layout model, packing, and QSettings persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings

LAYOUT_VERSION = 1
SETTINGS_KEY_LAYOUT = "dashboard/layout_v1"
MIME_WIDGET_TYPE = "application/x-monos-dashboard-widget"

DASHBOARD_WIDGET_IDS: frozenset[str] = frozenset(
    {
        "header",
        "kpi",
        "pipeline_health",
        "dept_load",
        "next_7_days",
        "needs_attention",
        "recent_notes",
    }
)

DASHBOARD_WIDGET_LABELS: dict[str, str] = {
    "header": "Welcome header",
    "kpi": "KPI tiles",
    "pipeline_health": "Pipeline Health",
    "dept_load": "Department Load",
    "next_7_days": "Next 7 Days",
    "needs_attention": "Needs Attention",
    "recent_notes": "Recent Notes",
}

# Widgets that should stay full-width when toggling span (header locked at span 2).
LOCKED_FULL_WIDTH_IDS: frozenset[str] = frozenset({"header"})


@dataclass
class DashboardWidgetSlot:
    id: str
    span: int = 1
    visible: bool = True

    def normalized(self) -> DashboardWidgetSlot:
        wid = self.id if self.id in DASHBOARD_WIDGET_IDS else ""
        span = 2 if self.id in LOCKED_FULL_WIDTH_IDS else min(max(int(self.span), 1), 2)
        return DashboardWidgetSlot(id=wid, span=span, visible=bool(self.visible))


@dataclass
class BentoPlacement:
    slot: DashboardWidgetSlot
    grid_row: int
    grid_col: int
    col_span: int


DEFAULT_LAYOUT: list[DashboardWidgetSlot] = [
    DashboardWidgetSlot("header", span=2, visible=True),
    DashboardWidgetSlot("kpi", span=2, visible=True),
    DashboardWidgetSlot("pipeline_health", span=1, visible=True),
    DashboardWidgetSlot("dept_load", span=1, visible=True),
    DashboardWidgetSlot("next_7_days", span=1, visible=True),
    DashboardWidgetSlot("needs_attention", span=1, visible=True),
    DashboardWidgetSlot("recent_notes", span=2, visible=True),
]

_DEFAULT_BY_ID: dict[str, DashboardWidgetSlot] = {s.id: s for s in DEFAULT_LAYOUT}


def default_span_for(widget_id: str) -> int:
    slot = _DEFAULT_BY_ID.get(widget_id)
    return slot.span if slot is not None else 1


def pack_bento_placements(
    slots: list[DashboardWidgetSlot],
    *,
    cols: int = 2,
) -> list[BentoPlacement]:
    """Pack visible slots into a 2-column bento grid."""
    placements: list[BentoPlacement] = []
    grid_row = 0
    current_col = 0

    for raw in slots:
        slot = raw.normalized()
        if not slot.visible or not slot.id:
            continue
        span = min(max(slot.span, 1), cols)
        if span >= cols:
            if current_col > 0:
                grid_row += 1
                current_col = 0
            placements.append(BentoPlacement(slot, grid_row, 0, cols))
            grid_row += 1
            current_col = 0
        else:
            if current_col + span > cols:
                grid_row += 1
                current_col = 0
            placements.append(BentoPlacement(slot, grid_row, current_col, span))
            current_col += span
            if current_col >= cols:
                grid_row += 1
                current_col = 0
    return placements


def _merge_with_defaults(slots: list[DashboardWidgetSlot]) -> list[DashboardWidgetSlot]:
    """Ensure every catalog widget appears once; unknown ids dropped."""
    by_id: dict[str, DashboardWidgetSlot] = {}
    order: list[str] = []
    for raw in slots:
        slot = raw.normalized()
        if not slot.id:
            continue
        if slot.id not in by_id:
            order.append(slot.id)
        by_id[slot.id] = slot
    for default in DEFAULT_LAYOUT:
        if default.id not in by_id:
            order.append(default.id)
            by_id[default.id] = DashboardWidgetSlot(
                default.id, span=default.span, visible=default.visible
            )
    return [by_id[i] for i in order]


def slots_to_json(slots: list[DashboardWidgetSlot]) -> str:
    payload = {
        "version": LAYOUT_VERSION,
        "widgets": [asdict(s.normalized()) for s in _merge_with_defaults(slots)],
    }
    return json.dumps(payload, separators=(",", ":"))


def slots_from_json(raw: str) -> list[DashboardWidgetSlot] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("version") != LAYOUT_VERSION:
        return None
    widgets = data.get("widgets")
    if not isinstance(widgets, list):
        return None
    parsed: list[DashboardWidgetSlot] = []
    for item in widgets:
        if not isinstance(item, dict):
            continue
        wid = item.get("id")
        if wid not in DASHBOARD_WIDGET_IDS:
            continue
        parsed.append(
            DashboardWidgetSlot(
                id=str(wid),
                span=int(item.get("span", default_span_for(str(wid)))),
                visible=bool(item.get("visible", True)),
            )
        )
    if not parsed:
        return None
    return _merge_with_defaults(parsed)


def load_dashboard_layout(settings: QSettings | None) -> list[DashboardWidgetSlot]:
    if settings is None:
        return [DashboardWidgetSlot(s.id, s.span, s.visible) for s in DEFAULT_LAYOUT]
    raw = settings.value(SETTINGS_KEY_LAYOUT, "", str) or ""
    if not raw.strip():
        return [DashboardWidgetSlot(s.id, s.span, s.visible) for s in DEFAULT_LAYOUT]
    parsed = slots_from_json(raw)
    if parsed is None:
        return [DashboardWidgetSlot(s.id, s.span, s.visible) for s in DEFAULT_LAYOUT]
    return parsed


def save_dashboard_layout(
    settings: QSettings | None,
    slots: list[DashboardWidgetSlot],
) -> None:
    if settings is None:
        return
    settings.setValue(SETTINGS_KEY_LAYOUT, slots_to_json(slots))


def reorder_slot(slots: list[DashboardWidgetSlot], widget_id: str, insert_index: int) -> list[DashboardWidgetSlot]:
    merged = _merge_with_defaults(slots)
    moving = next((s for s in merged if s.id == widget_id), None)
    if moving is None:
        return merged
    rest = [s for s in merged if s.id != widget_id]
    insert_index = max(0, min(insert_index, len(rest)))
    rest.insert(insert_index, moving)
    return rest


def toggle_slot_span(slots: list[DashboardWidgetSlot], widget_id: str) -> list[DashboardWidgetSlot]:
    if widget_id in LOCKED_FULL_WIDTH_IDS:
        return _merge_with_defaults(slots)
    merged = _merge_with_defaults(slots)
    out: list[DashboardWidgetSlot] = []
    for s in merged:
        if s.id == widget_id:
            new_span = 1 if s.span >= 2 else 2
            out.append(DashboardWidgetSlot(s.id, span=new_span, visible=s.visible))
        else:
            out.append(DashboardWidgetSlot(s.id, s.span, s.visible))
    return out


def set_slot_visible(
    slots: list[DashboardWidgetSlot],
    widget_id: str,
    visible: bool,
) -> list[DashboardWidgetSlot]:
    merged = _merge_with_defaults(slots)
    out: list[DashboardWidgetSlot] = []
    for s in merged:
        if s.id == widget_id:
            out.append(DashboardWidgetSlot(s.id, s.span, visible=visible))
        else:
            out.append(DashboardWidgetSlot(s.id, s.span, s.visible))
    return out


def hidden_widget_ids(slots: list[DashboardWidgetSlot]) -> list[str]:
    return [s.id for s in _merge_with_defaults(slots) if not s.visible]


def any_visible(slots: list[DashboardWidgetSlot]) -> bool:
    return any(s.visible for s in _merge_with_defaults(slots))
