"""Week workload strip for Dashboard Next 7 Days card."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from monostudio.ui_qt.style import MONOS_COLORS, monos_font

_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_CELL_GAP = 4.0
_CELL_PAD = 6.0
_WEEKDAY_H = 12.0
_DAY_NUM_H = 16.0
_LABEL_GAP = 6.0  # space between day number and bar track
_BAR_TRACK_H = 34.0
_BAR_MIN_H = 4.0
_BAR_MAX_H = 28.0
_LABEL_BLOCK_H = _WEEKDAY_H + _DAY_NUM_H + _LABEL_GAP
DASHBOARD_WEEK_STRIP_HEIGHT = int(_CELL_PAD + _LABEL_BLOCK_H + _BAR_TRACK_H + _CELL_PAD)
_STRIP_H = DASHBOARD_WEEK_STRIP_HEIGHT


class DashboardWeekStrip(QWidget):
    """Seven clickable day cells showing relative workload (bar height = task count)."""

    day_clicked = Signal(object)  # date | None — None clears filter

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardWeekStrip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(_STRIP_H)
        self.setFixedHeight(_STRIP_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._today = date.today()
        self._days: list[date] = []
        self._counts: dict[date, int] = {}
        self._selected: date | None = None
        self._hover_index: int | None = None
        self._cell_rects: list[QRectF] = []

    def set_week(
        self,
        *,
        today: date,
        days: int = 7,
        counts: dict[date, int] | None = None,
    ) -> None:
        self._today = today
        span = max(1, int(days))
        self._days = [today + timedelta(days=i) for i in range(span)]
        self._counts = {d: max(0, int((counts or {}).get(d, 0))) for d in self._days}
        self.update()

    def set_selected(self, day: date | None) -> None:
        if self._selected == day:
            return
        self._selected = day
        self.update()

    def _weekday_label(self, d: date) -> str:
        return _WEEKDAY_ABBR[d.weekday()]

    def _bar_height(self, count: int, max_count: int) -> float:
        if count <= 0 or max_count <= 0:
            return 0.0
        ratio = count / max_count
        return _BAR_MIN_H + ratio * (_BAR_MAX_H - _BAR_MIN_H)

    def _index_at(self, pos) -> int | None:
        x = float(pos.x())
        y = float(pos.y())
        for i, rect in enumerate(self._cell_rects):
            if rect.contains(x, y):
                return i
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        idx = self._index_at(event.position())
        if idx != self._hover_index:
            self._hover_index = idx
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._hover_index is not None:
            self._hover_index = None
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        idx = self._index_at(event.position())
        if idx is None or idx >= len(self._days):
            super().mouseReleaseEvent(event)
            return
        clicked = self._days[idx]
        if self._selected == clicked:
            self.day_clicked.emit(None)
        else:
            self.day_clicked.emit(clicked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        n = len(self._days)
        if n <= 0:
            p.end()
            return

        gap = _CELL_GAP
        total_gap = gap * max(0, n - 1)
        cell_w = max(1.0, (w - total_gap) / n)
        max_count = max((self._counts.get(d, 0) for d in self._days), default=0)

        blue = QColor(MONOS_COLORS.get("blue_400", "#60a5fa"))
        muted_bg = QColor("#27272a")
        hover_bg = QColor(255, 255, 255, 15)
        selected_bg = QColor(96, 165, 250, 31)
        bar_fill = QColor(MONOS_COLORS.get("blue_400", "#60a5fa"))
        bar_empty = QColor("#3f3f46")
        text_primary = QColor("#fafafa")
        text_meta = QColor("#71717a")
        text_today = blue

        self._cell_rects = []
        label_top = _CELL_PAD
        bar_top = _CELL_PAD + _LABEL_BLOCK_H

        for i, d in enumerate(self._days):
            x = i * (cell_w + gap)
            rect = QRectF(x, 0.0, cell_w, h)
            self._cell_rects.append(rect)
            count = self._counts.get(d, 0)
            is_today = d == self._today
            is_selected = self._selected == d
            is_hovered = self._hover_index == i

            cell_inner = QRectF(x + 1.0, 1.0, cell_w - 2.0, h - 2.0)
            radius = 6.0
            p.setPen(Qt.PenStyle.NoPen)
            if is_selected:
                p.setBrush(selected_bg)
                p.drawRoundedRect(cell_inner, radius, radius)
                pen = QPen(blue, 1.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(cell_inner, radius, radius)
                p.setPen(Qt.PenStyle.NoPen)
            elif is_hovered:
                p.setBrush(hover_bg)
                p.drawRoundedRect(cell_inner, radius, radius)

            # Weekday + day number (fixed block above bar track)
            wd_font = monos_font("Inter", 9, QFont.Weight.DemiBold)
            p.setFont(wd_font)
            p.setPen(text_today if is_today else text_meta)
            wd_rect = QRectF(x, label_top, cell_w, _WEEKDAY_H)
            p.drawText(
                wd_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                self._weekday_label(d),
            )

            num_font = monos_font("JetBrains Mono", 11, QFont.Weight.DemiBold)
            p.setFont(num_font)
            p.setPen(text_today if is_today else text_primary)
            num_rect = QRectF(x, label_top + _WEEKDAY_H, cell_w, _DAY_NUM_H)
            p.drawText(
                num_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                str(d.day),
            )

            # Workload bar — always below label block, never grows into labels
            track_rect = QRectF(x + 8.0, bar_top, cell_w - 16.0, _BAR_TRACK_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(muted_bg)
            p.drawRoundedRect(track_rect, 3.0, 3.0)

            if count > 0:
                bar_h = min(
                    self._bar_height(count, max_count),
                    track_rect.height(),
                )
                bar_rect = QRectF(
                    track_rect.left(),
                    track_rect.bottom() - bar_h,
                    track_rect.width(),
                    bar_h,
                )
                p.setBrush(bar_fill)
                p.drawRoundedRect(bar_rect, 3.0, 3.0)
                count_font = monos_font("JetBrains Mono", 9, QFont.Weight.DemiBold)
                p.setFont(count_font)
                p.setPen(text_primary)
                if bar_h >= 14.0:
                    p.drawText(
                        bar_rect,
                        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                        str(count),
                    )
                else:
                    p.drawText(
                        QRectF(track_rect.left(), track_rect.top(), track_rect.width(), 10.0),
                        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                        str(count),
                    )
            else:
                p.setBrush(bar_empty)
                empty_h = 3.0
                empty_rect = QRectF(
                    track_rect.left(),
                    track_rect.bottom() - empty_h,
                    track_rect.width(),
                    empty_h,
                )
                p.drawRoundedRect(empty_rect, 1.5, 1.5)

        p.end()
