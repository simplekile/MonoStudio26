# Rubber-band multi-select mixin shared by Main View grid and list.

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    Qt,
)
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QListView,
    QRubberBand,
    QTableView,
    QWidget,
)

from monostudio.ui_qt.view_items import ViewItem

RUBBER_BAND_THRESHOLD = 4


class RubberBandSelectMixin:
    """Track drag-marquee so MainView can defer Inspector updates until release."""

    def _rb_init(self) -> None:
        self._left_press_pos: QPoint | None = None
        self._rb_selecting = False
        self._rb_gesture = False
        self._rb_last_rows: tuple[int, ...] | None = None
        self._on_rubber_band_finished: Callable[[], None] | None = None
        self._rb_press_modifiers = Qt.KeyboardModifier.NoModifier
        self._rubber_band_widget: QRubberBand | None = None
        self._last_click_index: QPersistentModelIndex | None = None
        self._last_click_time: float = 0.0
        self._rb_skip_release_click = False
        self._shift_click_pending = False
        self._rb_empty_press = False
        self._rb_selection_signals_blocked = False

    def _rb_acquire_mouse(self) -> None:
        vp = self._rb_viewport()
        if vp is None:
            return
        try:
            vp.grabMouse()
        except Exception:
            pass

    def _rb_release_mouse(self) -> None:
        vp = self._rb_viewport()
        if vp is None:
            return
        try:
            if QWidget.mouseGrabber() is vp:
                vp.releaseMouse()
        except Exception:
            pass

    def _rb_force_cleanup(self) -> None:
        sm = self.selectionModel()
        if sm is not None and getattr(self, "_rb_selection_signals_blocked", False):
            sm.blockSignals(False)
            self._rb_selection_signals_blocked = False
        fn_stop = getattr(self, "_on_rubber_band_stopped", None)
        if callable(fn_stop):
            try:
                fn_stop()
            except Exception:
                pass
        self._rb_hide_rubber_band()
        self._rb_release_mouse()
        self._left_press_pos = None
        self._rb_selecting = False
        self._rb_empty_press = False
        self._rb_gesture = False
        self._rb_last_rows = None
        self._rb_skip_release_click = False
        self._shift_click_pending = False

    def _shift_anchor_model_index(self):
        anchor = getattr(self, "_shift_anchor_index", None)
        if anchor is not None and anchor.isValid():
            return anchor
        m = self.model()
        mv = m.parent() if m is not None else None
        if mv is not None and hasattr(mv, "_model_index_for_store_path"):
            store = getattr(mv, "_pipeline_selection_store", None)
            store_anchor = store.anchor() if store is not None else None
            if store_anchor is not None:
                idx = mv._model_index_for_store_path(store_anchor, view=self)
                if idx.isValid():
                    return idx
        sm = self.selectionModel()
        if sm is not None:
            cur = sm.currentIndex()
            if cur.isValid():
                return cur
        return QModelIndex()

    def _row_passes_selection_filter(self, idx) -> bool:
        m = self.model()
        if m is None or not idx.isValid():
            return False
        mv = m.parent()
        is_dimmed = getattr(mv, "_is_item_dimmed", None) if mv is not None else None
        if not callable(is_dimmed):
            return True
        item = idx.data(Qt.ItemDataRole.UserRole)
        return not (isinstance(item, ViewItem) and is_dimmed(item))

    def _select_shift_range_rows(self, *, anchor, target, add: bool) -> None:
        sm = self.selectionModel()
        m = self.model()
        if sm is None or m is None or not (anchor.isValid() and target.isValid()):
            return
        lo = min(anchor.row(), target.row())
        hi = max(anchor.row(), target.row())
        if not add:
            sm.clearSelection()
        for r in range(lo, hi + 1):
            idx = m.index(r, 0)
            if idx.isValid() and self._row_passes_selection_filter(idx):
                sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
        sm.setCurrentIndex(target, QItemSelectionModel.SelectionFlag.NoUpdate)

    def _apply_shift_range_to_pos(self, pos: QPoint, *, add: bool | None = None) -> None:
        target = self.indexAt(pos)
        if not target.isValid():
            return
        if add is None:
            add = bool(self._rb_press_modifiers & Qt.KeyboardModifier.ControlModifier)
        self._select_shift_range_rows(
            anchor=self._shift_anchor_model_index(),
            target=target,
            add=add,
        )

    def _handle_shift_left_release(self, event: QMouseEvent) -> None:
        self._rb_promote_to_marquee(event.pos())
        if self._rb_selecting:
            self._rb_update_rubber_band(event.pos())
            self._apply_rubber_band_row_selection(event.pos())
        else:
            self._apply_shift_range_to_pos(
                event.pos(),
                add=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
            )
        self._shift_click_pending = False
        self._rb_on_left_release()
        sm = self.selectionModel()
        if sm is not None and sm.currentIndex().isValid():
            self._shift_anchor_index = QPersistentModelIndex(sm.currentIndex())

    def _apply_plain_left_click(self, event: QMouseEvent) -> None:
        idx = self.indexAt(event.pos())
        sm = self.selectionModel()
        if sm is None or not idx.isValid():
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if sm.isSelected(idx):
                sm.select(idx, QItemSelectionModel.SelectionFlag.Deselect)
            else:
                sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            sm.select(idx, QItemSelectionModel.SelectionFlag.Select)

    def _finish_left_button_release(self, event: QMouseEvent) -> None:
        if getattr(self, "_rb_skip_release_click", False):
            self._rb_skip_release_click = False
            self._rb_on_left_release()
            return
        self._rb_promote_to_marquee(event.pos())
        if self._rb_selecting:
            self._rb_update_rubber_band(event.pos())
            self._apply_rubber_band_row_selection(event.pos())
            self._rb_on_left_release()
            return
        now = time.monotonic()
        idx = self.indexAt(event.pos())
        if getattr(self, "_rb_empty_press", False) and not idx.isValid():
            if not bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.clearSelection()
                self._shift_anchor_index = None
            self._rb_on_left_release()
            return
        dbl_ms = float(QApplication.doubleClickInterval())
        last = self._last_click_index
        if (
            idx.isValid()
            and last is not None
            and last.isValid()
            and idx.row() == last.row()
            and (now - self._last_click_time) * 1000.0 <= dbl_ms
        ):
            self._last_click_index = None
            self._last_click_time = 0.0
            self._rb_on_left_release()
            self.doubleClicked.emit(idx)
            return
        self._apply_plain_left_click(event)
        if idx.isValid():
            self._last_click_index = QPersistentModelIndex(idx)
            self._last_click_time = now
        else:
            self._last_click_index = None
        self._rb_on_left_release()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.pos())
            if idx.isValid() and self._row_passes_selection_filter(idx):
                self._last_click_index = None
                self._last_click_time = 0.0
                self._rb_force_cleanup()
                self.doubleClicked.emit(idx)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def rubber_band_selecting(self) -> bool:
        return bool(self._rb_selecting)

    def _rb_interaction_busy(self) -> bool:
        return bool(self._rb_selecting)

    def _rb_viewport(self):
        fn = getattr(self, "viewport", None)
        return fn() if callable(fn) else None

    @staticmethod
    def _rb_marquee_threshold() -> int:
        return RUBBER_BAND_THRESHOLD

    def _rb_promote_to_marquee(self, pos: QPoint) -> None:
        if self._rb_selecting or self._left_press_pos is None:
            return
        delta = pos - self._left_press_pos
        if delta.manhattanLength() >= self._rb_marquee_threshold():
            if self._rb_is_icon_grid():
                try:
                    self.doItemsLayout()
                except Exception:
                    pass
            self._rb_selecting = True
            sm = self.selectionModel()
            if sm is not None and not getattr(self, "_rb_selection_signals_blocked", False):
                sm.blockSignals(True)
                self._rb_selection_signals_blocked = True
            fn = getattr(self, "_on_rubber_band_marquee_started", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    def _ensure_rubber_band_widget(self) -> QRubberBand:
        if self._rubber_band_widget is None:
            self._rubber_band_widget = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        return self._rubber_band_widget

    def _rb_on_left_press(self, pos: QPoint, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        self._rb_release_mouse()
        self._left_press_pos = pos
        self._rb_selecting = False
        self._rb_last_rows = None
        self._rb_gesture = True
        self._rb_press_modifiers = modifiers
        self._rb_skip_release_click = False
        self._rb_empty_press = not self.indexAt(pos).isValid()
        self._rb_acquire_mouse()

    def _rb_is_icon_grid(self) -> bool:
        return isinstance(self, QListView) and self.viewMode() == QListView.ViewMode.IconMode

    def _marquee_rect(self, current_pos: QPoint) -> QRect:
        if self._left_press_pos is None:
            return QRect()
        norm = QRect(self._left_press_pos, current_pos).normalized()
        if norm.isNull():
            return norm
        return norm.adjusted(-1, -1, 1, 1)

    def _row_visual_rect(self, row: int) -> QRect:
        m = self.model()
        if m is None or row < 0 or row >= m.rowCount():
            return QRect()
        if isinstance(self, QTableView):
            try:
                y = self.rowViewportPosition(row)
                h = self.rowHeight(row)
            except Exception:
                return QRect()
            if h <= 0:
                return QRect()
            vp = self.viewport()
            vw = vp.width() if vp is not None else 0
            if vw <= 0:
                idx = m.index(row, 0)
                return self.visualRect(idx) if idx.isValid() else QRect()
            return QRect(0, y, vw, h)
        idx = m.index(row, 0)
        if not idx.isValid():
            return QRect()
        return self.visualRect(idx)

    def _rows_in_marquee_rect(self, norm: QRect) -> list[int]:
        m = self.model()
        if m is None or norm.isNull():
            return []
        rows: set[int] = set()

        def _add_row(row: int) -> None:
            if row < 0 or row >= m.rowCount():
                return
            idx = m.index(row, 0)
            if idx.isValid() and self._row_passes_selection_filter(idx):
                rows.add(row)

        def _add_at(pt: QPoint) -> None:
            idx = self.indexAt(pt)
            if idx.isValid():
                _add_row(idx.row())

        for pt in (
            norm.topLeft(),
            norm.topRight(),
            norm.bottomLeft(),
            norm.bottomRight(),
            norm.center(),
        ):
            _add_at(pt)

        if self._rb_is_icon_grid():
            gs = self.gridSize()
            gw = int(gs.width())
            gh = int(gs.height())
            if gw > 1 and gh > 1:
                step_x = max(1, gw // 3)
                step_y = max(1, gh // 3)
                y = norm.top()
                while y <= norm.bottom():
                    x = norm.left()
                    while x <= norm.right():
                        _add_at(QPoint(x, y))
                        x += step_x
                    y += step_y

        if isinstance(self, QTableView):
            row_top = self.rowAt(norm.top())
            row_bot = self.rowAt(norm.bottom())
            if row_top < 0:
                row_top = 0
            if row_bot < 0:
                row_bot = m.rowCount() - 1
            lo, hi = min(row_top, row_bot), max(row_top, row_bot)
            for row in range(lo, hi + 1):
                vr = self._row_visual_rect(row)
                if vr.isValid() and not vr.isEmpty() and norm.intersects(vr):
                    _add_row(row)
            return sorted(rows)

        for row in range(m.rowCount()):
            idx = m.index(row, 0)
            if not idx.isValid():
                continue
            if not self._row_passes_selection_filter(idx):
                continue
            vr = self._row_visual_rect(row)
            if vr.isValid() and not vr.isEmpty() and norm.intersects(vr):
                rows.add(row)

        return sorted(rows)

    def _rb_on_move(self, event: QMouseEvent) -> None:
        if self._left_press_pos is None:
            return
        if not bool(event.buttons() & Qt.MouseButton.LeftButton):
            return
        self._rb_promote_to_marquee(event.pos())

    def _rb_update_rubber_band(self, current_pos: QPoint) -> None:
        if self._left_press_pos is None or not self._rb_selecting:
            return
        rect = QRect(self._left_press_pos, current_pos).normalized()
        rb = self._ensure_rubber_band_widget()
        rb.setGeometry(rect)
        rb.show()

    def _rb_hide_rubber_band(self) -> None:
        rb = self._rubber_band_widget
        if rb is None:
            return
        rb.hide()
        rb.setGeometry(QRect())
        vp = self._rb_viewport()
        if vp is not None:
            vp.update()

    def _apply_rubber_band_row_selection(self, current_pos: QPoint) -> None:
        sm = self.selectionModel()
        m = self.model()
        if sm is None or m is None or self._left_press_pos is None:
            return
        shift = bool(self._rb_press_modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(self._rb_press_modifiers & Qt.KeyboardModifier.ControlModifier)
        norm = self._marquee_rect(current_pos)
        if norm.isNull():
            return
        rows_in_rect = self._rows_in_marquee_rect(norm)
        rows_key = tuple(sorted(rows_in_rect))
        if rows_key == getattr(self, "_rb_last_rows", None):
            return
        self._rb_last_rows = rows_key

        vp = self._rb_viewport()
        if vp is not None:
            vp.setUpdatesEnabled(False)
        try:
            if shift:
                lo = min(rows_in_rect) if rows_in_rect else None
                hi = max(rows_in_rect) if rows_in_rect else None
                anchor = self._shift_anchor_model_index()
                if anchor.isValid():
                    lo = min(lo, anchor.row()) if lo is not None else anchor.row()
                    hi = max(hi, anchor.row()) if hi is not None else anchor.row()
                elif lo is None or hi is None:
                    return
                if not ctrl:
                    sm.clearSelection()
                for r in range(lo, hi + 1):
                    idx = m.index(r, 0)
                    if idx.isValid() and self._row_passes_selection_filter(idx):
                        sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
                return

            selection = QItemSelection()
            for row in rows_in_rect:
                idx = m.index(row, 0)
                if idx.isValid():
                    selection.select(idx, idx)
            if ctrl:
                sm.select(selection, QItemSelectionModel.SelectionFlag.Select)
            else:
                sm.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            if rows_in_rect:
                last_idx = m.index(rows_in_rect[-1], 0)
                if last_idx.isValid():
                    sm.setCurrentIndex(last_idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            if vp is not None:
                vp.setUpdatesEnabled(True)
                vp.update()

    def _rb_on_left_release(self) -> bool:
        was_rubber = bool(self._rb_selecting)
        self._rb_force_cleanup()
        if was_rubber and self._on_rubber_band_finished is not None:
            self._on_rubber_band_finished()
        return was_rubber
