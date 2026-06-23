# Column header bar for Pipeline List Row view (synced horizontal scroll with list).

from __future__ import annotations

from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QScrollBar, QWidget

from monostudio.ui_qt.pipeline_list_layout import ListSlot, PipelineListLayout
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

_HEADER_HEIGHT = 40
_RESIZE_HIT_PX = 6


class PipelineListHeader(QWidget):
    """Deep-dark column titles aligned with PipelineListLayout x-offsets."""

    column_resized = Signal(object, int)  # ListSlot, width px
    column_resize_finished = Signal()

    def __init__(self, *, list_view, main_view, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PipelineListHeader")
        self._list_view = list_view
        self._main_view = main_view
        self._scroll_x = 0
        self._resize_slot: ListSlot | None = None
        self._resize_start_x = 0
        self._resize_start_w = 0
        self.setFixedHeight(_HEADER_HEIGHT)
        self.setMouseTracking(True)
        sb: QScrollBar | None = list_view.horizontalScrollBar()
        if sb is not None:
            sb.valueChanged.connect(self._on_scroll)

    def _layout(self) -> PipelineListLayout:
        return self._main_view.pipeline_list_layout()

    def _on_scroll(self, value: int) -> None:
        self._scroll_x = int(value)
        self.update()

    def sync_from_list(self) -> None:
        sb = self._list_view.horizontalScrollBar()
        self._scroll_x = sb.value() if sb is not None else 0
        self.update()

    def _resize_slot_at(self, x: int) -> ListSlot | None:
        layout = self._layout()
        sticky_w = layout.sticky_width()
        content_x = layout.content_x_for_viewport_pos(x, 8 - self._scroll_x, scroll_x=self._scroll_x)
        cursor = 0
        for slot in layout.visible_slots():
            w = layout.widths.get(slot, 0)
            if w <= 0:
                continue
            boundary = cursor + w
            if abs(content_x - boundary) <= _RESIZE_HIT_PX:
                return slot
            cursor = boundary
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            slot = self._resize_slot_at(int(event.position().x()))
            if slot is not None:
                self._resize_slot = slot
                self._resize_start_x = int(event.position().x())
                self._resize_start_w = self._layout().widths.get(slot, 0)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._resize_slot is not None:
            delta = int(event.position().x()) - self._resize_start_x
            self.column_resized.emit(self._resize_slot, max(24, self._resize_start_w + delta))
            event.accept()
            return
        if self._resize_slot_at(int(event.position().x())) is not None:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._resize_slot is not None and event.button() == Qt.MouseButton.LeftButton:
            self._resize_slot = None
            self.unsetCursor()
            self.column_resize_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        layout = self._layout()
        sticky_w = layout.sticky_width()
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#0d0d0f"))
            painter.setPen(QColor("#2a2a2c"))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

            font = monos_font("Inter", 11, QFont.Weight.ExtraBold)
            painter.setFont(font)
            fm = QFontMetrics(font)

            def draw_labels(slots, x_start: int) -> int:
                x = x_start
                for slot in slots:
                    w = layout.widths.get(slot, 0)
                    label = layout.header_label(slot)
                    if label:
                        cell = QRect(x, 0, w, self.height())
                        painter.setPen(QColor(MONOS_COLORS.get("text_meta", "#71717a")))
                        text = label.upper()
                        text_rect = cell.adjusted(8, 0, -8, 0)
                        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width())
                        painter.drawText(
                            text_rect,
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                            elided,
                        )
                    x += w
                return x

            if sticky_w > 0:
                painter.save()
                painter.setClipRect(QRect(sticky_w, 0, max(0, self.width() - sticky_w), self.height()))
                draw_labels(layout.scrollable_slots(), 8 + sticky_w - self._scroll_x)
                painter.restore()

                painter.fillRect(0, 0, sticky_w, self.height(), QColor("#0d0d0f"))
                draw_labels(layout.sticky_slots(), 8)
                painter.setPen(QColor("#3f3f46"))
                painter.drawLine(sticky_w - 1, 0, sticky_w - 1, self.height())
            else:
                draw_labels(layout.visible_slots(), 8 - self._scroll_x)
        finally:
            painter.end()
