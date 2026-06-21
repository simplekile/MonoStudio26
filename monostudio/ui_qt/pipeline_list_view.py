# Pipeline List Row view (QListView ListMode) for Main View.

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QItemSelectionModel,
    QListView,
    QStyle,
    QStyleOptionViewItem,
)

from monostudio.ui_qt.inbox_list_row_paint import paint_inbox_list_row_chrome
from monostudio.ui_qt.pipeline_rubber_band import RubberBandSelectMixin
from monostudio.ui_qt.style import MONOS_COLORS

_PIPELINE_LIST_ROW_HEIGHT = 56


def pipeline_list_row_height() -> int:
    return _PIPELINE_LIST_ROW_HEIGHT


class PipelineListRowView(RubberBandSelectMixin, QListView):
    """Finder-style list rows with rubber-band multi-select."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rb_init()
        self._shift_anchor_index = None
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setResizeMode(QListView.ResizeMode.Fixed)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

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
            hover = self.indexAt(vp.mapFromGlobal(QCursor.pos()))
            if hover.isValid() and hover.row() == row:
                opt.state |= QStyle.StateFlag.State_MouseOver
        return opt

    def _paint_row_backgrounds(self, painter: QPainter, exposed: QRect) -> None:
        m = self.model()
        if m is None or m.rowCount() <= 0:
            return
        mv = m.parent()
        browser_mode = getattr(mv, "_browser_mode", "work")
        browser_ctx = getattr(mv, "_browser_context", "asset")
        use_tint = (
            not self._rb_interaction_busy()
            and browser_mode == "publish"
            and browser_ctx in ("asset", "shot")
        )
        if use_tint:
            from monostudio.ui_qt.main_view import _card_bg_colors_for_browser_mode

            tint_bg, tint_hover = _card_bg_colors_for_browser_mode(
                browser_mode, browser_ctx, hover=False
            )
        else:
            tint_bg = tint_hover = None

        vp_w = self.viewport().width()
        painter.save()
        try:
            painter.setClipping(False)
            for row in range(m.rowCount()):
                idx = m.index(row, 0)
                if not idx.isValid():
                    continue
                y = self.visualRect(idx).top()
                h = _PIPELINE_LIST_ROW_HEIGHT
                if y + h < exposed.top() or y > exposed.bottom():
                    continue
                opt = self._list_row_bg_option(row)
                selected = bool(opt.state & QStyle.StateFlag.State_Selected)
                hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
                if use_tint and tint_bg is not None and tint_hover is not None and not selected:
                    full_rect = QRect(0, y, max(vp_w, opt.rect.width()), h)
                    painter.fillRect(full_rect, tint_hover if hover else tint_bg)
                    painter.setPen(QColor("#2a2a2c"))
                    painter.drawLine(full_rect.left(), full_rect.bottom(), full_rect.right(), full_rect.bottom())
                else:
                    paint_inbox_list_row_chrome(painter, opt, viewport_width=vp_w)
                if selected:
                    accent = QColor(MONOS_COLORS["blue_600"])
                    painter.fillRect(0, y, 2, h, accent)
        finally:
            painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._rb_interaction_busy():
            vp = self.viewport()
            painter = QPainter(vp)
            try:
                self._paint_row_backgrounds(painter, event.rect())
            finally:
                painter.end()
        super().paintEvent(event)

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
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
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
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            if self._shift_click_pending:
                self._handle_shift_left_release(event)
                event.accept()
                return
            self._rb_promote_to_marquee(event.pos())
            was_rubber = bool(self._rb_selecting)
            self._finish_left_button_release(event)
            if not was_rubber:
                sm = self.selectionModel()
                if sm is not None and sm.currentIndex().isValid():
                    from PySide6.QtCore import QPersistentModelIndex

                    self._shift_anchor_index = QPersistentModelIndex(sm.currentIndex())
            event.accept()
            return
        super().mouseReleaseEvent(event)
