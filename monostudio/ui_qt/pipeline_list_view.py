# Pipeline List Row view (QListView ListMode) for Main View.

from __future__ import annotations

import time

from PySide6.QtCore import QItemSelectionModel, QPersistentModelIndex, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from monostudio.ui_qt.inbox_list_row_paint import paint_inbox_list_row_chrome
from monostudio.ui_qt.pipeline_rubber_band import RubberBandSelectMixin
from monostudio.ui_qt.pipeline_row_paint import list_row_dim_opacity, paint_list_row_selection_overlay
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.view_items import ViewItem

_PIPELINE_LIST_ROW_HEIGHT = 56


def pipeline_list_row_height() -> int:
    return _PIPELINE_LIST_ROW_HEIGHT


class PipelineListRowView(RubberBandSelectMixin, QListView):
    """Finder-style list rows with rubber-band multi-select."""

    uses_grid_card_drag_preview = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rb_init()
        self._shift_anchor_index = None
        self._middle_drag_start_pos: QPoint | None = None
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setResizeMode(QListView.ResizeMode.Fixed)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._scroll_busy = False
        self._scroll_busy_timer = QTimer(self)
        self._scroll_busy_timer.setSingleShot(True)
        self._scroll_busy_timer.setInterval(120)
        self._scroll_busy_timer.timeout.connect(self._end_scroll_busy)
        self._paint_hover_row: int | None = None
        vsb = self.verticalScrollBar()
        if vsb is not None:
            vsb.valueChanged.connect(self._on_scroll_activity)
        sb = self.horizontalScrollBar()
        if sb is not None:
            sb.valueChanged.connect(self._on_horizontal_scroll)

    def _main_view(self):
        m = self.model()
        return m.parent() if m is not None else None

    def _pipeline_layout(self):
        mv = self._main_view()
        return getattr(mv, "_pipeline_list_layout", None) if mv is not None else None

    def _sticky_width(self) -> int:
        layout = self._pipeline_layout()
        return layout.sticky_width() if layout is not None else 0

    def _on_scroll_activity(self, _value: int) -> None:
        self._mark_scroll_busy()

    def _on_horizontal_scroll(self, _value: int) -> None:
        self._mark_scroll_busy()
        sticky_w = self._sticky_width()
        if sticky_w > 0:
            self.viewport().update(QRect(0, 0, sticky_w, self.viewport().height()))
        else:
            self.viewport().update()

    def _mark_scroll_busy(self) -> None:
        self._scroll_busy = True
        self._scroll_busy_timer.start()

    def _end_scroll_busy(self) -> None:
        self._scroll_busy = False
        mv = self._main_view()
        if mv is not None and hasattr(mv, "_schedule_thumbnail_prefetch"):
            mv._schedule_thumbnail_prefetch()
        row = self._cached_hover_row()
        if row is not None:
            m = self.model()
            if m is not None:
                idx = m.index(row, 0)
                if idx.isValid():
                    self.update(idx)
                    return
        sticky_w = self._sticky_width()
        if sticky_w > 0:
            self.viewport().update(QRect(0, 0, sticky_w, self.viewport().height()))

    def scroll_busy(self) -> bool:
        return self._scroll_busy or self._rb_interaction_busy()

    def _visible_row_range(self, exposed: QRect) -> range:
        m = self.model()
        if m is None or m.rowCount() <= 0:
            return range(0)
        top_idx = self.indexAt(QPoint(0, max(0, exposed.top())))
        bot_idx = self.indexAt(QPoint(0, exposed.bottom()))
        top = top_idx.row() if top_idx.isValid() else 0
        bottom = bot_idx.row() if bot_idx.isValid() else m.rowCount() - 1
        if top > bottom:
            return range(0)
        return range(top, min(bottom, m.rowCount() - 1) + 1)

    def _cached_hover_row(self) -> int | None:
        vp = self.viewport()
        idx = self.indexAt(vp.mapFromGlobal(QCursor.pos()))
        return idx.row() if idx.isValid() else None

    def _list_row_bg_option(self, row: int) -> QStyleOptionViewItem:
        m = self.model()
        vp = self.viewport()
        idx = m.index(row, 0) if m is not None else None
        opt = QStyleOptionViewItem()
        if idx is not None and idx.isValid():
            opt.rect = self.visualRect(idx)
        else:
            opt.rect = QRect()
        opt.state = QStyle.StateFlag.State_Enabled
        sm = self.selectionModel()
        if sm is not None and idx is not None and idx.isValid() and sm.isSelected(idx):
            opt.state |= QStyle.StateFlag.State_Selected
        if not self._rb_interaction_busy():
            hover_row = self._paint_hover_row
            if hover_row is None:
                hover_row = self._cached_hover_row()
            if hover_row is not None and hover_row == row:
                opt.state |= QStyle.StateFlag.State_MouseOver
        return opt

    def _paint_row_backgrounds(self, painter: QPainter, exposed: QRect) -> None:
        m = self.model()
        if m is None or m.rowCount() <= 0:
            return
        mv = m.parent()
        browser_mode = getattr(mv, "_browser_mode", "work")
        browser_ctx = getattr(mv, "_browser_context", "asset")
        use_mode_tint = browser_ctx in ("asset", "shot") and browser_mode in ("publish", "review")
        tint_bg = tint_hover = None
        if use_mode_tint:
            from monostudio.ui_qt.main_view import _card_bg_colors_for_browser_mode

            tint_bg, tint_hover = _card_bg_colors_for_browser_mode(
                browser_mode, browser_ctx, hover=False
            )

        vp_w = self.viewport().width()
        painter.save()
        try:
            painter.setClipping(False)
            for row in self._visible_row_range(exposed):
                idx = m.index(row, 0)
                if not idx.isValid():
                    continue
                y = self.visualRect(idx).top()
                h = _PIPELINE_LIST_ROW_HEIGHT
                opt = self._list_row_bg_option(row)
                selected = bool(opt.state & QStyle.StateFlag.State_Selected)
                hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
                item = idx.data(Qt.ItemDataRole.UserRole)
                dim_opacity = 1.0
                if isinstance(item, ViewItem):
                    dim_opacity = list_row_dim_opacity(
                        item,
                        show_publish=browser_mode == "publish",
                        active_department=getattr(mv, "_active_department", None),
                        hover=hover,
                    )

                painter.save()
                try:
                    if dim_opacity < 1.0:
                        painter.setOpacity(dim_opacity)
                    if use_mode_tint and tint_bg is not None and tint_hover is not None:
                        full_rect = QRect(0, y, max(vp_w, opt.rect.width()), h)
                        painter.fillRect(full_rect, tint_hover if hover else tint_bg)
                        if selected:
                            paint_list_row_selection_overlay(painter, full_rect)
                        painter.setPen(QColor("#2a2a2c"))
                        painter.drawLine(
                            full_rect.left(), full_rect.bottom(), full_rect.right(), full_rect.bottom()
                        )
                    else:
                        paint_inbox_list_row_chrome(painter, opt, viewport_width=vp_w)
                finally:
                    painter.restore()

                if selected:
                    accent = QColor(MONOS_COLORS["blue_600"])
                    painter.fillRect(0, y, 2, h, accent)
        finally:
            painter.restore()

    def _paint_sticky_row_chrome(self, painter: QPainter, row: int, y: int, h: int, sticky_w: int) -> None:
        m = self.model()
        if m is None:
            return
        idx = m.index(row, 0)
        if not idx.isValid():
            return
        mv = m.parent()
        browser_mode = getattr(mv, "_browser_mode", "work")
        browser_ctx = getattr(mv, "_browser_context", "asset")
        use_mode_tint = browser_ctx in ("asset", "shot") and browser_mode in ("publish", "review")
        opt = self._list_row_bg_option(row)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        item = idx.data(Qt.ItemDataRole.UserRole)
        dim_opacity = 1.0
        if isinstance(item, ViewItem):
            dim_opacity = list_row_dim_opacity(
                item,
                show_publish=browser_mode == "publish",
                active_department=getattr(mv, "_active_department", None),
                hover=hover,
            )
        clip = QRect(0, y, sticky_w, h)
        painter.save()
        try:
            painter.setClipRect(clip)
            if dim_opacity < 1.0:
                painter.setOpacity(dim_opacity)
            if use_mode_tint:
                from monostudio.ui_qt.main_view import _card_bg_colors_for_browser_mode

                tint_bg, tint_hover = _card_bg_colors_for_browser_mode(
                    browser_mode, browser_ctx, hover=False
                )
                if tint_bg is not None and tint_hover is not None:
                    painter.fillRect(clip, tint_hover if hover else tint_bg)
                    if selected:
                        paint_list_row_selection_overlay(painter, clip)
            else:
                opt.rect = clip
                paint_inbox_list_row_chrome(painter, opt, viewport_width=sticky_w)
            if selected:
                accent = QColor(MONOS_COLORS["blue_600"])
                painter.fillRect(0, y, 2, h, accent)
        finally:
            painter.restore()

    def _paint_sticky_overlay(self, painter: QPainter, exposed: QRect) -> None:
        sticky_w = self._sticky_width()
        if sticky_w <= 0:
            return
        m = self.model()
        if m is None or m.rowCount() <= 0:
            return
        delegate = self.itemDelegate()
        if not isinstance(delegate, QStyledItemDelegate):
            return
        paint_sticky = getattr(delegate, "paint_sticky_columns", None)
        if not callable(paint_sticky):
            return
        for row in self._visible_row_range(exposed):
            idx = m.index(row, 0)
            if not idx.isValid():
                continue
            y = self.visualRect(idx).top()
            h = _PIPELINE_LIST_ROW_HEIGHT
            self._paint_sticky_row_chrome(painter, row, y, h, sticky_w)
            opt = self._list_row_bg_option(row)
            opt.rect = QRect(0, y, sticky_w, h)
            paint_sticky(painter, opt, idx)
        painter.setPen(QColor("#3f3f46"))
        painter.drawLine(sticky_w - 1, exposed.top(), sticky_w - 1, exposed.bottom())

    def paintEvent(self, event: QPaintEvent) -> None:
        self._paint_hover_row = self._cached_hover_row()
        try:
            vp = self.viewport()
            painter = QPainter(vp)
            try:
                self._paint_row_backgrounds(painter, event.rect())
            finally:
                painter.end()
            super().paintEvent(event)
            if self._sticky_width() > 0:
                painter = QPainter(vp)
                try:
                    self._paint_sticky_overlay(painter, event.rect())
                finally:
                    painter.end()
        finally:
            self._paint_hover_row = None

    def sizeHintForRow(self, row: int) -> int:  # type: ignore[override]
        return _PIPELINE_LIST_ROW_HEIGHT

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            sm = self.selectionModel()
            if sm is not None and sm.currentIndex().isValid():
                self.doubleClicked.emit(sm.currentIndex())
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._rb_on_left_press(event.pos(), event.modifiers())
        if event.button() == Qt.MouseButton.MiddleButton:
            idx = self.indexAt(event.pos())
            if idx.isValid():
                sm = self.selectionModel()
                if sm is not None:
                    sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            self._middle_drag_start_pos = event.pos()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            if self.indexAt(event.pos()).isValid():
                self._shift_click_pending = True
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton and not self.indexAt(event.pos()).isValid():
            self._shift_anchor_index = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if bool(event.buttons() & Qt.MouseButton.MiddleButton) and self._middle_drag_start_pos is not None:
            if (event.pos() - self._middle_drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
                self.startDrag(Qt.CopyAction)
                self._middle_drag_start_pos = None
            event.accept()
            return
        self._rb_on_move(event)
        if bool(event.buttons() & Qt.MouseButton.LeftButton) and self._left_press_pos is not None:
            if self._shift_click_pending:
                if self._rb_selecting:
                    self._apply_shift_range_to_pos(event.pos())
                event.accept()
                return
            if self._rb_selecting:
                self._rb_update_rubber_band(event.pos())
                self._apply_rubber_band_row_selection(event.pos())
            event.accept()
            return
        # Interactive cell hits swallow press in MainView.eventFilter without arming rubber-band.
        # Do not fall through to QAbstractItemView DragOnly startDrag.
        if bool(event.buttons() & Qt.MouseButton.LeftButton):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_drag_start_pos = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._shift_click_pending:
                self._handle_shift_left_release(event)
                event.accept()
                return
            self._rb_promote_to_marquee(event.pos())
            if self._rb_selecting:
                self._finish_left_button_release(event)
                event.accept()
                return
            self._release_plain_left_click(event)
            super().mouseReleaseEvent(event)
            return
        super().mouseReleaseEvent(event)

    def _release_plain_left_click(self, event: QMouseEvent) -> None:
        """Rubber-band cleanup + manual double-click; single-click selection via Qt super()."""
        if getattr(self, "_rb_skip_release_click", False):
            self._rb_skip_release_click = False
            self._rb_on_left_release()
            return
        idx = self.indexAt(event.pos())
        if getattr(self, "_rb_empty_press", False) and not idx.isValid():
            if not bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.clearSelection()
                self._shift_anchor_index = None
            self._rb_on_left_release()
            return
        if idx.isValid():
            now = time.monotonic()
            last = self._last_click_index
            dbl_ms = float(QApplication.doubleClickInterval())
            if (
                last is not None
                and last.isValid()
                and idx.row() == last.row()
                and (now - self._last_click_time) * 1000.0 <= dbl_ms
            ):
                self._last_click_index = None
                self._last_click_time = 0.0
                self._rb_on_left_release()
                self.doubleClicked.emit(idx)
                return
            self._last_click_index = QPersistentModelIndex(idx)
            self._last_click_time = now
            if idx.isValid():
                sm = self.selectionModel()
                if sm is not None and sm.currentIndex().isValid():
                    self._shift_anchor_index = QPersistentModelIndex(sm.currentIndex())
        self._rb_on_left_release()

    def startDrag(self, supportedActions) -> None:  # type: ignore[override]
        from monostudio.ui_qt.pipeline_drag_preview import start_pipeline_item_drag

        start_pipeline_item_drag(self, supportedActions)
