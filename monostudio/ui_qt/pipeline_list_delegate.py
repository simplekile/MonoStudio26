# Full-row delegate for Pipeline List Row view.

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from monostudio.core.models import Asset, Shot
from monostudio.core.workspace_reader import ProjectQuickStats
from monostudio.ui_qt.pipeline_list_layout import ListSlot, PipelineListLayout
from monostudio.ui_qt.pipeline_row_paint import (
    list_dcc_badge_rects,
    list_health_chip_rect,
    list_row_dim_opacity,
    list_special_folder_chip_rect,
    list_status_pill_rect_for_cell,
    list_thumb_cover_paint,
    paint_health_icon_chip,
    paint_list_assignee_avatars,
    paint_list_special_folder_icon,
    paint_note_icon_chip,
    paint_status_pill_chip,
)
from monostudio.ui_qt.pipeline_list_view import pipeline_list_row_height
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind, display_name_for_item


class PipelineListRowDelegate(QStyledItemDelegate):
    _LIST_THUMB_SIZE = 40

    def __init__(self, *, view, main_view) -> None:
        super().__init__(view)
        self._view = view
        self._main_view = main_view
        self._active_project_root: str | None = None
        self._hovered_status_row: int | None = None
        self._hovered_health_row: int | None = None
        self._hovered_notes_row: int | None = None
        self._hovered_ref_row: int | None = None
        self._hovered_concept_row: int | None = None
        self._fast_paint = False
        self._assignee_pm_cache: dict[tuple[str, float], object] = {}

    def set_fast_paint(self, enabled: bool) -> None:
        if self._fast_paint == bool(enabled):
            return
        self._fast_paint = bool(enabled)

    def _repaint_after_hover_change(self) -> None:
        self._view.viewport().update()

    def set_active_project_root(self, path: str | None) -> None:
        p = path or None
        if p == self._active_project_root:
            return
        self._active_project_root = p
        self._repaint_after_hover_change()

    def set_hovered_status_row(self, row: int | None) -> None:
        if self._hovered_status_row == row:
            return
        self._hovered_status_row = row
        self._repaint_after_hover_change()

    def set_hovered_health_row(self, row: int | None) -> None:
        if self._hovered_health_row == row:
            return
        self._hovered_health_row = row
        self._repaint_after_hover_change()

    def set_hovered_notes_row(self, row: int | None) -> None:
        if self._hovered_notes_row == row:
            return
        self._hovered_notes_row = row
        self._repaint_after_hover_change()

    def set_hovered_ref_row(self, row: int | None) -> None:
        if self._hovered_ref_row == row:
            return
        self._hovered_ref_row = row
        self._repaint_after_hover_change()

    def set_hovered_concept_row(self, row: int | None) -> None:
        if self._hovered_concept_row == row:
            return
        self._hovered_concept_row = row
        self._repaint_after_hover_change()

    def _layout(self) -> PipelineListLayout:
        return self._main_view.pipeline_list_layout()

    def _should_fast_paint(self) -> bool:
        if self._fast_paint:
            return True
        view = self._view
        return bool(hasattr(view, "rubber_band_selecting") and view.rubber_band_selecting())

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        layout = self._layout()
        return QSize(max(layout.total_width(), option.rect.width()), pipeline_list_row_height())

    def _paint_slot(
        self,
        painter: QPainter,
        slot: ListSlot,
        cell: QRect,
        *,
        index,
        item: ViewItem,
        row: int,
        option,
        main,
        fast: bool,
    ) -> None:
        if slot == ListSlot.INDEX:
            self._paint_index(painter, cell, row, option)
        elif slot == ListSlot.THUMB:
            self._paint_thumb(painter, cell, index, fast=fast)
        elif slot == ListSlot.NAME:
            self._paint_name(painter, cell, item, option, fast=fast)
        elif fast:
            return
        elif slot == ListSlot.NOTES:
            self._paint_notes(painter, cell, item, row)
        elif slot == ListSlot.DCC:
            self._paint_dcc(painter, cell, item, main)
        elif slot == ListSlot.HEALTH:
            self._paint_health(painter, cell, item, main, row)
        elif slot == ListSlot.REF:
            self._paint_special_folder(painter, cell, item, main, row, ref=True)
        elif slot == ListSlot.CONCEPT:
            self._paint_special_folder(painter, cell, item, main, row, ref=False)
        elif slot == ListSlot.STATUS:
            self._paint_status(painter, cell, item, main, row)
        elif slot == ListSlot.DUE:
            self._paint_text_meta(painter, cell, main._list_due_text(item))
        elif slot == ListSlot.VERSION:
            self._paint_text_meta(painter, cell, (main._list_version_text(item), False))
        elif slot == ListSlot.LAST_UPDATED:
            self._paint_text_meta(painter, cell, (main._list_last_updated(item), False))
        elif slot == ListSlot.ASSIGNEE:
            self._paint_assignee(painter, cell, item, main)
        elif slot == ListSlot.ASSETS and isinstance(item.ref, ProjectQuickStats):
            self._paint_text_meta(painter, cell, (str(item.ref.assets_count or "—"), False))
        elif slot == ListSlot.SHOTS and isinstance(item.ref, ProjectQuickStats):
            self._paint_text_meta(painter, cell, (str(item.ref.shots_count or "—"), False))
        elif slot == ListSlot.PATH:
            self._paint_path(painter, cell, item)

    def paint_sticky_columns(self, painter: QPainter, option, index) -> None:
        """Paint frozen Index + Thumb + Name overlay (viewport coords)."""
        item = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, ViewItem):
            return
        main = self._main_view
        layout = self._layout()
        row = index.row()
        fast = self._should_fast_paint()
        content_opacity = list_row_dim_opacity(
            item,
            show_publish=bool(getattr(main, "_show_publish", False)),
            active_department=getattr(main, "_active_department", None),
            hover=bool(option.state & QStyle.StateFlag.State_MouseOver),
        )
        painter.save()
        try:
            if content_opacity < 1.0:
                painter.setOpacity(content_opacity)
            if not fast:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            x = option.rect.left()
            for slot in layout.sticky_slots():
                w = layout.widths.get(slot, 0)
                cell = QRect(x, option.rect.top(), w, option.rect.height())
                x += w
                self._paint_slot(
                    painter,
                    slot,
                    cell,
                    index=index,
                    item=item,
                    row=row,
                    option=option,
                    main=main,
                    fast=fast,
                )
        finally:
            painter.restore()

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        item = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, ViewItem):
            return
        main = self._main_view
        layout = self._layout()
        row = index.row()
        fast = self._should_fast_paint()
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        content_opacity = list_row_dim_opacity(
            item,
            show_publish=bool(getattr(main, "_show_publish", False)),
            active_department=getattr(main, "_active_department", None),
            hover=hover,
        )
        painter.save()
        try:
            if content_opacity < 1.0:
                painter.setOpacity(content_opacity)
            if not fast:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            sticky_w = self._view._sticky_width() if hasattr(self._view, "_sticky_width") else 0
            if sticky_w > 0:
                slots = layout.scrollable_slots()
                x = option.rect.left() + sticky_w
            else:
                slots = layout.visible_slots()
                x = option.rect.left()
            for slot in slots:
                w = layout.widths.get(slot, 0)
                cell = QRect(x, option.rect.top(), w, option.rect.height())
                x += w
                self._paint_slot(
                    painter,
                    slot,
                    cell,
                    index=index,
                    item=item,
                    row=row,
                    option=option,
                    main=main,
                    fast=fast,
                )

            if (
                item.kind == ViewItemKind.PROJECT
                and self._active_project_root
                and str(item.path) == self._active_project_root
            ):
                c = QColor(MONOS_COLORS["amber_400"])
                c.setAlphaF(0.7)
                painter.fillRect(option.rect.left(), option.rect.top(), 2, option.rect.height(), c)
        finally:
            painter.restore()

    def _paint_index(self, painter: QPainter, rect: QRect, row: int, option) -> None:
        painter.setPen(QColor(MONOS_COLORS["text_meta"]))
        painter.setFont(monos_font("JetBrains Mono", 11))
        painter.drawText(rect.adjusted(8, 0, -4, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(row + 1))

    def _paint_thumb(self, painter: QPainter, rect: QRect, index, *, fast: bool) -> None:
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if not isinstance(icon, QIcon) or icon.isNull():
            return
        size = self._LIST_THUMB_SIZE
        thumb_rect = QRect(
            rect.left() + max(0, (rect.width() - size) // 2),
            rect.top() + max(0, (rect.height() - size) // 2),
            size,
            size,
        )
        list_thumb_cover_paint(painter, thumb_rect, icon, fast=fast)

    def _paint_name(self, painter: QPainter, rect: QRect, item: ViewItem, option, *, fast: bool) -> None:
        text = display_name_for_item(item)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QColor(MONOS_COLORS["text_primary_selected"]))
        else:
            painter.setPen(QColor(MONOS_COLORS["text_primary"]))
        painter.setFont(monos_font("Inter", 13, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_text_meta(self, painter: QPainter, rect: QRect, payload: tuple[str, bool]) -> None:
        text, overdue = payload
        color = QColor("#ef4444") if overdue else QColor(MONOS_COLORS["text_meta"])
        painter.setPen(color)
        painter.setFont(monos_font("JetBrains Mono", 11))
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_path(self, painter: QPainter, rect: QRect, item: ViewItem) -> None:
        painter.setPen(QColor(MONOS_COLORS["text_meta"]))
        painter.setFont(monos_font("JetBrains Mono", 11))
        fm = QFontMetrics(painter.font())
        elided = fm.elidedText(str(item.path), Qt.TextElideMode.ElideMiddle, rect.width() - 16)
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

    def _paint_assignee(self, painter: QPainter, rect: QRect, item: ViewItem, main) -> None:
        users = main._list_assignee_users_for_item(item)
        dpr = float(painter.device().devicePixelRatioF() or 1.0)
        paint_list_assignee_avatars(
            painter,
            rect,
            users,
            getattr(main, "_workspace_root", None),
            dpr=dpr,
            pixmap_cache=self._assignee_pm_cache,
        )

    def _paint_notes(self, painter: QPainter, rect: QRect, item: ViewItem, row: int) -> None:
        if not isinstance(item.ref, (Asset, Shot)) or not item.path:
            return
        n, nmode = (0, "empty")
        if hasattr(self._main_view, "notes_badge_state"):
            try:
                n, nmode = self._main_view.notes_badge_state(item.path)
            except Exception:
                pass
        paint_note_icon_chip(
            painter,
            list_health_chip_rect(rect),
            n,
            visual_mode=nmode,
            hovered=self._hovered_notes_row == row,
        )

    def _paint_health(self, painter: QPainter, rect: QRect, item: ViewItem, main, row: int) -> None:
        dep = (getattr(main, "_active_department", None) or "").strip()
        if not dep or not isinstance(item.ref, (Asset, Shot)):
            return
        from monostudio.ui_qt.main_view import _item_active_dcc, assess_view_item_health

        health = assess_view_item_health(
            item.ref,
            dep,
            active_dcc_id=_item_active_dcc(item.path, dep) if item.path else None,
        )
        if health is None:
            return
        paint_health_icon_chip(
            painter,
            list_health_chip_rect(rect),
            health,
            hovered=self._hovered_health_row == row,
        )

    def _paint_special_folder(self, painter: QPainter, rect: QRect, item: ViewItem, main, row: int, *, ref: bool) -> None:
        if not isinstance(item.ref, (Asset, Shot)):
            return
        has_files = False
        if ref and hasattr(main, "entity_has_reference_files_cached"):
            has_files = bool(main.entity_has_reference_files_cached(item))
        elif not ref and hasattr(main, "entity_has_concept_files_cached"):
            has_files = bool(main.entity_has_concept_files_cached(item))
        hovered = (ref and self._hovered_ref_row == row) or (not ref and self._hovered_concept_row == row)
        paint_list_special_folder_icon(
            painter,
            list_special_folder_chip_rect(rect),
            "eye" if ref else "lightbulb",
            has_files=has_files,
            hovered=hovered,
        )

    def _paint_dcc(self, painter: QPainter, rect: QRect, item: ViewItem, main) -> None:
        if getattr(main, "_show_publish", False) or not isinstance(item, ViewItem):
            return
        from monostudio.ui_qt.main_view import _item_active_dcc
        from monostudio.ui_qt.pipeline_row_paint import list_dcc_badge_info

        active_dep = (getattr(main, "_active_department", None) or "").strip() or None
        dept_reg = getattr(main, "_dept_registry", None)
        badge_info = list_dcc_badge_info(item, active_dep, dept_registry=dept_reg)
        if not badge_info:
            return
        rects = list_dcc_badge_rects(rect, [(dcc_id, st) for (_, dcc_id, st) in badge_info])
        chip_h = 14 + 8
        chip_r = chip_h // 2
        dcc_bg = QColor(0, 0, 0, 160)
        active_dcc = _item_active_dcc(getattr(item, "path", None), active_dep or "") if getattr(item, "path", None) else None
        existing_ids = {dcc_id for (_, dcc_id, st) in badge_info if st == "exists"}
        if not active_dcc or active_dcc not in existing_ids:
            active_dcc = next((dcc_id for (_, dcc_id, st) in badge_info if st == "exists"), None)
        c_active = QColor(MONOS_COLORS["amber_400"])
        c_active.setAlphaF(0.7)
        from PySide6.QtGui import QPen as QPenCls

        pen_active = QPenCls(c_active, 2)
        creating_font = monos_font("Inter", 9)
        for i, (dcc_icon, dcc_id, badge_status) in enumerate(badge_info):
            if i >= len(rects):
                break
            r, _ = rects[i]
            is_active = bool(active_dcc and (dcc_id or "").strip() == active_dcc)
            if badge_status == "creating":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(dcc_bg)
                painter.drawRoundedRect(r, chip_r, chip_r)
                if is_active:
                    painter.setPen(pen_active)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(r, chip_r, chip_r)
                painter.setFont(creating_font)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "Creating…")
            else:
                cx, cy = r.x() + chip_r, r.y() + chip_r
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(dcc_bg)
                painter.drawEllipse(cx - chip_r, cy - chip_r, chip_h, chip_h)
                if is_active:
                    painter.setPen(pen_active)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(cx - chip_r, cy - chip_r, chip_h, chip_h)
                if dcc_icon is not None and not dcc_icon.isNull():
                    pix = dcc_icon.pixmap(14, 14)
                    if not pix.isNull():
                        painter.drawPixmap(r.x() + 4, r.y() + 4, pix)

    def _paint_status(self, painter: QPainter, rect: QRect, item: ViewItem, main, row: int) -> None:
        ctx = getattr(main, "_browser_context", "")
        chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
        fm = QFontMetrics(chip_font)
        pill_hover = self._hovered_status_row == row
        if ctx == "project" and item.kind == ViewItemKind.PROJECT:
            from monostudio.core.workspace_reader import project_status_color_hex, project_status_label

            stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
            status = getattr(stats, "status", None) or "WAITING"
            line = project_status_label(status)
            color_hex = project_status_color_hex(status)
        elif ctx in ("asset", "shot") and isinstance(item.ref, (Asset, Shot)):
            dep = (getattr(main, "_active_department", None) or "").strip()
            if not dep:
                return
            from monostudio.core.production_status import aggregate_status_id_for_item, color_hex_for_status_id

            reg = main._production_status_registry_cached()
            hidden = set(getattr(main, "_inspector_hidden_departments", set()) or ())
            sid = aggregate_status_id_for_item(
                item.ref,
                active_department=dep,
                hidden_departments=hidden,
                registry=reg,
            )
            line = reg.label_for(sid)
            color_hex = color_hex_for_status_id(sid, reg)
        else:
            return
        pill_rect = list_status_pill_rect_for_cell(rect, line, fm)
        paint_status_pill_chip(
            painter,
            pill_rect,
            line,
            color_hex,
            fm=fm,
            font=chip_font,
            hovered=pill_hover,
        )
