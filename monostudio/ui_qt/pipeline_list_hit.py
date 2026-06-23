# Hit-testing for Pipeline List Row view (chip columns, status pill, DCC badges).

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QWidget

from monostudio.core.models import Asset, Shot
from monostudio.core.production_status import aggregate_status_id_for_item
from monostudio.core.workspace_reader import ProjectQuickStats, project_status_label
from monostudio.ui_qt.pipeline_list_layout import ListSlot
from monostudio.ui_qt.pipeline_row_paint import (
    list_dcc_badge_rects,
    list_health_chip_rect,
    list_special_folder_chip_rect,
    list_status_pill_rect_for_cell,
)
from monostudio.ui_qt.style import monos_font
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind

if TYPE_CHECKING:
    from monostudio.ui_qt.main_view import MainView


class PipelineListHitTest:
    """Map viewport coordinates to list row slots and interactive chips."""

    def __init__(self, main_view: MainView) -> None:
        self._mv = main_view

    def _horizontal_scroll(self) -> int:
        sb = self._mv._list_view.horizontalScrollBar()
        return int(sb.value()) if sb is not None else 0

    def row_slot_rect(self, row: int, slot: ListSlot) -> QRect:
        idx = self._mv._list_model.index(row, 0)
        if not idx.isValid():
            return QRect()
        row_rect = self._mv._list_view.visualRect(idx)
        return self._mv._pipeline_list_layout.slot_rect(row_rect, slot)

    def slot_at_pos(self, pos: QPoint) -> tuple[int, ListSlot] | None:
        idx = self._mv._list_view.indexAt(pos)
        if not idx.isValid():
            return None
        row_rect = self._mv._list_view.visualRect(idx)
        layout = self._mv._pipeline_list_layout
        scroll_x = self._horizontal_scroll()
        content_x = layout.content_x_for_viewport_pos(pos.x(), row_rect.left(), scroll_x=scroll_x)
        slot = layout.slot_at_content_x(content_x)
        if slot is None:
            return None
        return idx.row(), slot

    def dcc_hit(self, pos: QPoint) -> tuple[ViewItem | None, str | None, str | None]:
        mv = self._mv
        if mv._view_mode != "list" or mv._show_publish:
            return None, None, None
        hit = self.slot_at_pos(pos)
        if hit is None or hit[1] != ListSlot.DCC:
            return None, None, None
        row, _ = hit
        index = mv._list_view.indexAt(pos)
        if not index.isValid():
            return None, None, None
        item = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, ViewItem):
            return None, None, None
        from monostudio.ui_qt.main_view import _dcc_ids_for_item

        cell_rect = self.row_slot_rect(row, ListSlot.DCC)
        dcc_list = _dcc_ids_for_item(
            item,
            (mv._active_department or "").strip() or None,
            dept_registry=getattr(mv, "_dept_registry", None),
        )
        if not dcc_list:
            return None, None, None
        for r, dcc_id in list_dcc_badge_rects(cell_rect, dcc_list):
            if r.contains(pos):
                return item, dcc_id, (mv._active_department or "").strip() or ""
        return None, None, None

    def status_hit(self, pos: QPoint) -> ViewItem | None:
        mv = self._mv
        if mv._view_mode != "list" or not mv._project_root:
            return None
        if mv._browser_context not in ("asset", "shot"):
            return None
        hit = self.slot_at_pos(pos)
        if hit is None or hit[1] != ListSlot.STATUS:
            return None
        idx = mv._list_view.indexAt(pos)
        item = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        if isinstance(item, ViewItem) and isinstance(item.ref, (Asset, Shot)):
            return item
        return None

    def project_status_hit(self, pos: QPoint) -> ViewItem | None:
        mv = self._mv
        if mv._view_mode != "list" or mv._browser_context != "project":
            return None
        hit = self.slot_at_pos(pos)
        if hit is None or hit[1] != ListSlot.STATUS:
            return None
        row, _ = hit
        index = mv._list_view.indexAt(pos)
        item = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        if not isinstance(item, ViewItem) or item.kind != ViewItemKind.PROJECT:
            return None
        stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
        status = getattr(stats, "status", None) or "WAITING"
        line = project_status_label(status)
        chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
        fm = QFontMetrics(chip_font)
        cell_rect = self.row_slot_rect(row, ListSlot.STATUS)
        pill_rect = list_status_pill_rect_for_cell(cell_rect, line, fm)
        return item if pill_rect.contains(pos) else None

    def status_pill_hit_row(self, pos: QPoint) -> int | None:
        mv = self._mv
        if mv._view_mode != "list":
            return None
        hit = self.slot_at_pos(pos)
        if hit is None:
            return None
        row, slot = hit
        if slot != ListSlot.STATUS:
            return None
        if mv._browser_context == "project":
            return row
        if not mv._project_root or mv._browser_context not in ("asset", "shot"):
            return None
        dep = (mv._active_department or "").strip()
        if not dep:
            return None
        idx = mv._list_view.indexAt(pos)
        if not idx.isValid():
            return None
        item = idx.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        try:
            reg = mv._production_status_registry_cached()
            sid = aggregate_status_id_for_item(
                item.ref,
                active_department=dep,
                hidden_departments=mv._inspector_hidden_departments,
                registry=reg,
            )
            line = reg.label_for(sid)
        except Exception:
            line = "Waiting"
        chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
        fm = QFontMetrics(chip_font)
        cell_rect = self.row_slot_rect(row, ListSlot.STATUS)
        pill_rect = list_status_pill_rect_for_cell(cell_rect, line, fm)
        return row if pill_rect.contains(pos) else None

    def health_hit_row(self, pos: QPoint) -> int | None:
        mv = self._mv
        if mv._view_mode != "list" or mv._browser_context not in ("asset", "shot"):
            return None
        dep = (mv._active_department or "").strip()
        if not dep:
            return None
        hit = self.slot_at_pos(pos)
        if hit is None or hit[1] != ListSlot.HEALTH:
            return None
        row, _ = hit
        idx = mv._list_view.indexAt(pos)
        item = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        from monostudio.ui_qt.main_view import _item_active_dcc, assess_view_item_health

        health = assess_view_item_health(
            item.ref,
            dep,
            active_dcc_id=_item_active_dcc(item.path, dep) if item.path else None,
        )
        if health is None:
            return None
        cell_rect = self.row_slot_rect(row, ListSlot.HEALTH)
        return row if list_health_chip_rect(cell_rect).contains(pos) else None

    def thumb_note_hit_row(self, pos: QPoint) -> int | None:
        mv = self._mv
        if mv._view_mode != "list" or mv._browser_context not in ("asset", "shot"):
            return None
        hit = self.slot_at_pos(pos)
        if hit is None or hit[1] != ListSlot.NOTES:
            return None
        row, _ = hit
        idx = mv._list_view.indexAt(pos)
        item = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)) or not item.path:
            return None
        cell_rect = self.row_slot_rect(row, ListSlot.NOTES)
        return row if list_health_chip_rect(cell_rect).contains(pos) else None

    def thumb_note_hit(self, pos: QPoint) -> ViewItem | None:
        row = self.thumb_note_hit_row(pos)
        if row is None:
            return None
        idx = self._mv._list_model.index(row, 0)
        item = idx.data(Qt.ItemDataRole.UserRole)
        return item if isinstance(item, ViewItem) else None

    def health_hit(self, pos: QPoint) -> ViewItem | None:
        row = self.health_hit_row(pos)
        if row is None:
            return None
        idx = self._mv._list_model.index(row, 0)
        item = idx.data(Qt.ItemDataRole.UserRole)
        return item if isinstance(item, ViewItem) else None

    def special_folder_hit(self, pos: QPoint, slot: ListSlot) -> ViewItem | None:
        mv = self._mv
        if mv._view_mode != "list" or mv._browser_context not in ("asset", "shot"):
            return None
        hit = self.slot_at_pos(pos)
        if hit is None or hit[1] != slot:
            return None
        row, _ = hit
        idx = mv._list_view.indexAt(pos)
        item = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        cell_rect = self.row_slot_rect(row, slot)
        if not list_special_folder_chip_rect(cell_rect).contains(pos):
            return None
        return item

    def ref_hit(self, pos: QPoint) -> ViewItem | None:
        return self.special_folder_hit(pos, ListSlot.REF)

    def concept_hit(self, pos: QPoint) -> ViewItem | None:
        return self.special_folder_hit(pos, ListSlot.CONCEPT)

    def ref_hit_row(self, pos: QPoint) -> int | None:
        item = self.ref_hit(pos)
        if item is None:
            return None
        index = self._mv._list_view.indexAt(pos)
        return index.row() if index.isValid() else None

    def concept_hit_row(self, pos: QPoint) -> int | None:
        item = self.concept_hit(pos)
        if item is None:
            return None
        index = self._mv._list_view.indexAt(pos)
        return index.row() if index.isValid() else None

    def assignee_hit_row(self, pos: QPoint) -> int | None:
        hit = self.slot_at_pos(pos)
        if hit is None or hit[1] != ListSlot.ASSIGNEE:
            return None
        return hit[0]

    def assignee_hit_item(self, pos: QPoint) -> ViewItem | None:
        row = self.assignee_hit_row(pos)
        if row is None:
            return None
        idx = self._mv._list_model.index(row, 0)
        item = idx.data(Qt.ItemDataRole.UserRole)
        return item if isinstance(item, ViewItem) else None

    def clear_hover(self) -> None:
        delegate = self._mv._list_row_delegate
        delegate.set_hovered_status_row(None)
        delegate.set_hovered_health_row(None)
        delegate.set_hovered_notes_row(None)
        delegate.set_hovered_ref_row(None)
        delegate.set_hovered_concept_row(None)
        self._mv._list_view.viewport().unsetCursor()

    def update_interactive_hover(self, pos: QPoint) -> None:
        mv = self._mv
        if mv._view_mode != "list":
            return
        if mv.interaction_fast_paint():
            return
        delegate = mv._list_row_delegate
        delegate.set_hovered_status_row(self.status_pill_hit_row(pos))
        delegate.set_hovered_health_row(self.health_hit_row(pos))
        delegate.set_hovered_notes_row(self.thumb_note_hit_row(pos))
        delegate.set_hovered_ref_row(self.ref_hit_row(pos))
        delegate.set_hovered_concept_row(self.concept_hit_row(pos))
        vp = mv._list_view.viewport()
        if (
            self.health_hit_row(pos) is not None
            or self.thumb_note_hit_row(pos) is not None
            or self.status_pill_hit_row(pos) is not None
            or self.ref_hit_row(pos) is not None
            or self.concept_hit_row(pos) is not None
            or self.assignee_hit_row(pos) is not None
        ):
            vp.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            vp.unsetCursor()
