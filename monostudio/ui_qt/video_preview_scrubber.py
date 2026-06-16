"""Timeline scrubber with time ruler, range segments, and playhead."""

from __future__ import annotations

import math
from typing import Literal

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QMouseEvent, QPainter, QPen, QPolygon, QWheelEvent
from PySide6.QtWidgets import QInputDialog, QSizePolicy, QWidget

from monostudio.core.video_media import (
    VideoFrameRange,
    VideoReviewMarker,
    format_frame_label,
    format_range_span_display,
    format_ruler_tick,
    format_timecode,
    TimeDisplayMode,
)
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu, monos_font
from monostudio.ui_qt.video_range_colors import range_color_qcolor


TimelineListMode = Literal["ranges", "markers"]
_MARKER_COLOR = QColor("#f472b6")


class VideoPreviewScrubber(QWidget):
    """Horizontal timeline: time ruler + range track + playhead."""

    sliderPressed = Signal()
    valueChanged = Signal(int)
    seek_released = Signal(float)
    frame_preview = Signal(int)
    hover_frame = Signal(int)
    footer_context_changed = Signal(str)
    in_out_changed = Signal(int, int)
    range_handles_drag_started = Signal()
    range_highlighted = Signal(str)
    range_edit_requested = Signal(str)
    range_deselected = Signal()
    marker_highlighted = Signal(str)
    marker_deselected = Signal()
    go_to_in_requested = Signal(str)
    go_to_out_requested = Signal(str)
    range_duplicate_requested = Signal(str)
    range_delete_requested = Signal(str)
    range_rename_requested = Signal(str, str)
    focus_range_requested = Signal(str)
    seek_to_frame = Signal(int)
    mark_in_at_frame = Signal(int)
    mark_out_at_frame = Signal(int)
    add_range_requested = Signal()
    fit_timeline_requested = Signal()

    _MARGIN_H = 0
    _RULER_H = 26
    _TRACK_H = 32
    _RANGE_BAND_H = 18
    _GAP = 0
    _HANDLE_W = 11
    _END_CAP_W = 6
    _PLAYHEAD_W = 2
    _PROXY_RULER_BAR_H = 5
    _MIN_VIEW_SPAN = 8.0
    _ZOOM_WHEEL_FACTOR = 1.15
    _WHEEL_ZOOM_DEG = 120.0
    _FOCUS_RANGE_PAD_FRAMES = 12
    _CLICK_DRAG_THRESHOLD_PX = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewTimeline")
        self.setMinimumHeight(self._RULER_H + self._GAP + self._TRACK_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self._fps = 24.0
        self._total_frames = 1
        self._playhead_frame = 0
        self._ranges: list[VideoFrameRange] = []
        self._markers: list[VideoReviewMarker] = []
        self._timeline_list_mode: TimelineListMode = "ranges"
        self._marker_highlight_id: str | None = None
        self._highlight_id: str | None = None
        self._edit_id: str | None = None
        self._draft_in: int | None = None
        self._draft_out: int | None = None
        self._dragging = False
        self._drag_handle: str | None = None  # "in" | "out" | "move" | None
        self._drag_move_anchor_frame = 0
        self._drag_move_origin_in = 0
        self._drag_move_origin_out = 0
        self._last_hover_frame: int | None = None
        self._last_footer_zone: str | None = None
        self._block_value_signal = False
        self._overlap_cycle_frame: int | None = None
        self._overlap_cycle_ids: tuple[str, ...] = ()
        self._view_start = 0.0
        self._view_span = 1.0
        self._panning = False
        self._pan_anchor_x = 0
        self._pan_origin_start = 0.0
        self._press_x = 0
        self._press_frame = 0
        self._press_outside_range = False
        self._press_scrub_clears_selection = False
        self._press_outside_marker = False
        self._scrub_pointer_x: int | None = None
        self._proxy_full_timeline = False
        self._proxy_spans: list[tuple[int, int]] = []
        self._proxy_build_span: tuple[int, int] | None = None
        self._proxy_build_fraction = 0.0
        self._time_display_mode: TimeDisplayMode = "timecode"
        self._sync_view_to_fit()

    def minimum(self) -> int:
        return 0

    def maximum(self) -> int:
        return max(0, self._total_frames - 1)

    def value(self) -> int:
        return self._playhead_frame

    def blockSignals(self, block: bool) -> bool:  # noqa: N802
        return super().blockSignals(block)

    def setValue(self, frame: int) -> None:  # noqa: N802
        frame = max(0, min(self.maximum(), int(frame)))
        if frame == self._playhead_frame:
            return
        self._playhead_frame = frame
        if not self._block_value_signal:
            self.valueChanged.emit(frame)
        self.update()

    def set_frame_count(self, total: int, *, refit_view: bool = False) -> None:
        total = max(1, int(total))
        changed = total != self._total_frames
        self._total_frames = total
        self._playhead_frame = max(0, min(self._playhead_frame, self.maximum()))
        if refit_view or changed:
            self.reset_view()
        else:
            self._clamp_view()
            self.update()

    def reset_view(self) -> None:
        self._sync_view_to_fit()
        self.update()

    def focus_frame_range(
        self,
        in_frame: int,
        out_frame: int,
        *,
        padding_frames: int | None = None,
    ) -> None:
        lo, hi = sorted((int(in_frame), int(out_frame)))
        pad = padding_frames if padding_frames is not None else self._FOCUS_RANGE_PAD_FRAMES
        pad = max(pad, int((hi - lo + 1) * 0.12))
        start = max(0.0, float(lo - pad))
        end = min(float(self.maximum() + 1), float(hi + pad + 1))
        self._view_start = start
        self._view_span = max(self._MIN_VIEW_SPAN, end - start)
        self._clamp_view()
        self.update()

    def is_zoomed(self) -> bool:
        return self._view_span + 0.5 < float(self.maximum() + 1)

    def _sync_view_to_fit(self) -> None:
        self._view_start = 0.0
        self._view_span = float(max(1, self.maximum() + 1))

    def _view_end(self) -> float:
        return min(float(self.maximum() + 1), self._view_start + self._view_span)

    def _clamp_view(self) -> None:
        max_end = float(self.maximum() + 1)
        span = max(self._MIN_VIEW_SPAN, min(self._view_span, max_end))
        start = max(0.0, min(self._view_start, max(0.0, max_end - span)))
        self._view_start = start
        self._view_span = span

    def _zoom_at(self, factor: float, anchor_x: int) -> None:
        if factor <= 0 or self.maximum() <= 0:
            return
        track = self._track_rect()
        if track.width() <= 0:
            return
        anchor_frame = self._frame_at_x(anchor_x)
        ratio = (anchor_x - track.left()) / track.width()
        ratio = max(0.0, min(1.0, ratio))
        new_span = self._view_span / factor
        max_span = float(self.maximum() + 1)
        new_span = max(self._MIN_VIEW_SPAN, min(new_span, max_span))
        self._view_span = new_span
        self._view_start = anchor_frame - ratio * new_span
        self._clamp_view()
        self.update()

    def _pan_by_frames(self, delta_frames: float) -> None:
        if abs(delta_frames) < 1e-6:
            return
        self._view_start += delta_frames
        self._clamp_view()
        self.update()

    def set_fps(self, fps: float) -> None:
        self._fps = max(1e-6, float(fps))
        self.update()

    def set_time_display_mode(self, mode: TimeDisplayMode) -> None:
        if mode not in ("frame", "timecode"):
            return
        if mode != self._time_display_mode:
            self._time_display_mode = mode
            self.update()

    def set_proxy_full_timeline(self, *, ready: bool) -> None:
        if self._proxy_full_timeline != ready:
            self._proxy_full_timeline = ready
            self.update()

    def set_proxy_spans(self, spans: list[tuple[int, int]]) -> None:
        self._proxy_spans = list(spans)
        self.update()

    def set_proxy_build_progress(self, in_frame: int, out_frame: int, fraction: float) -> None:
        lo, hi = sorted((int(in_frame), int(out_frame)))
        self._proxy_build_span = (lo, hi)
        self._proxy_build_fraction = max(0.0, min(1.0, float(fraction)))
        self.update()

    def set_proxy_build_fraction(self, fraction: float) -> None:
        if self._proxy_build_span is None:
            return
        self._proxy_build_fraction = max(0.0, min(1.0, float(fraction)))
        self.update()

    def clear_proxy_build_progress(self) -> None:
        if self._proxy_build_span is None and self._proxy_build_fraction <= 0.0:
            return
        self._proxy_build_span = None
        self._proxy_build_fraction = 0.0
        self.update()

    def _proxy_ruler_bar_y(self, ruler_y: int) -> int:
        return max(0, ruler_y - self._PROXY_RULER_BAR_H + 1)

    def _draw_proxy_ruler_overlay(self, painter: QPainter, ruler_y: int) -> None:
        bar_y = self._proxy_ruler_bar_y(ruler_y)
        bar_h = self._PROXY_RULER_BAR_H
        if self._proxy_full_timeline:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(34, 197, 94, 90))
            painter.drawRect(self._MARGIN_H, bar_y, self.width() - 2 * self._MARGIN_H, bar_h)
            return
        if self._proxy_spans:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(34, 197, 94, 90))
            for in_f, out_f in self._proxy_spans:
                x1 = self._x_for_frame(in_f)
                x2 = self._x_for_frame(out_f)
                if x2 < x1:
                    x1, x2 = x2, x1
                painter.drawRect(x1, bar_y, max(2, x2 - x1), bar_h)
        if self._proxy_build_span is not None:
            lo, hi = self._proxy_build_span
            x1 = self._x_for_frame(lo)
            x2 = self._x_for_frame(hi)
            if x2 < x1:
                x1, x2 = x2, x1
            span_w = max(2, x2 - x1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(34, 197, 94, 36))
            painter.drawRect(x1, bar_y, span_w, bar_h)
            if self._proxy_build_fraction > 0.0:
                prog_w = max(2, int(span_w * self._proxy_build_fraction))
                painter.setBrush(QColor(34, 197, 94, 155))
                painter.drawRect(x1, bar_y, prog_w, bar_h)

    def set_timeline_list_mode(self, mode: TimelineListMode) -> None:
        if mode not in ("ranges", "markers"):
            return
        if mode == self._timeline_list_mode:
            return
        self._timeline_list_mode = mode
        self.update()

    def set_markers(
        self,
        markers: list[VideoReviewMarker],
        *,
        highlight_id: str | None,
    ) -> None:
        self._markers = list(markers)
        self._marker_highlight_id = highlight_id
        self.update()

    def set_ranges(
        self,
        ranges: list[VideoFrameRange],
        *,
        highlight_id: str | None,
        edit_id: str | None = None,
    ) -> None:
        self._ranges = list(ranges)
        self._highlight_id = highlight_id
        self._edit_id = edit_id
        self.update()

    def set_draft(self, draft_in: int | None, draft_out: int | None) -> None:
        self._draft_in = draft_in
        self._draft_out = draft_out
        self.update()

    def set_position_frame(self, frame: int) -> None:
        if self._dragging:
            return
        self._block_value_signal = True
        self.setValue(frame)
        self._block_value_signal = False

    def x_for_frame(self, frame: int) -> int:
        return self._x_for_frame(frame)

    def _ruler_h(self) -> int:
        h = max(self._RULER_H + self._GAP + self._TRACK_H, self.height())
        base = self._RULER_H + self._GAP + self._TRACK_H
        if h <= base:
            return self._RULER_H
        extra = h - base
        return self._RULER_H + (extra + 1) // 2

    def _track_rect(self):
        w = max(1, self.width() - 2 * self._MARGIN_H)
        top = self._ruler_h() + self._GAP
        h = max(self._TRACK_H, self.height() - top)
        return QRect(self._MARGIN_H, top, w, h)

    def _band_metrics(self, track: QRect) -> tuple[int, int]:
        band_h = min(self._RANGE_BAND_H, max(4, track.height()))
        band_top = track.top() + (track.height() - band_h) // 2
        return band_top, band_h

    def _duration_sec(self) -> float:
        return self._total_frames / self._fps if self._fps > 0 else 0.0

    def _visible_duration_sec(self) -> float:
        return self._view_span / self._fps if self._fps > 0 else 0.0

    def _x_for_time(self, t_sec: float) -> int:
        frame = t_sec * self._fps
        return self._x_for_frame(int(round(frame)))

    def _minor_tick_interval_sec(self, track_width: int, major_sec: float) -> float | None:
        duration = self._visible_duration_sec()
        if duration <= 0 or track_width <= 0 or major_sec <= 0:
            return None
        min_px = 5.0
        for count in (10, 5, 4, 2):
            sec = major_sec / count
            if sec / duration * track_width >= min_px:
                return sec
        return None

    def _draw_frame_ticks(
        self,
        painter: QPainter,
        track: QRect,
        *,
        major_sec: float,
    ) -> None:
        duration = self._duration_sec()
        if duration <= 0:
            return
        ruler_y = self._ruler_h() - 1
        minor_sec = self._minor_tick_interval_sec(track.width(), major_sec)
        t_start = self._view_start / self._fps
        t_end = self._view_end() / self._fps

        frame_pen = QPen(QColor(255, 255, 255, 34), 1)

        if minor_sec is not None:
            t = math.floor(t_start / minor_sec) * minor_sec
            while t <= t_end + 1e-9:
                on_major = major_sec > 0 and abs(t - round(t / major_sec) * major_sec) < 1e-5
                if not on_major:
                    x = self._x_for_time(t)
                    painter.setPen(frame_pen)
                    painter.drawLine(x, ruler_y - 5, x, ruler_y)
                t += minor_sec

    def _frame_at_x(self, x: int) -> int:
        track = self._track_rect()
        if track.width() <= 0:
            return 0
        ratio = (x - track.left()) / track.width()
        ratio = max(0.0, min(1.0, ratio))
        frame = self._view_start + ratio * self._view_span
        return int(round(max(0.0, min(float(self.maximum()), frame))))

    def _x_for_frame(self, frame: int) -> int:
        track = self._track_rect()
        if self._view_span <= 0:
            return track.left()
        ratio = (float(frame) - self._view_start) / self._view_span
        return int(track.left() + ratio * track.width())

    def _clamp_track_x(self, x: int) -> int:
        track = self._track_rect()
        return max(track.left(), min(int(x), track.right()))

    def _playhead_x(self) -> int:
        if self._dragging and self._drag_handle is None and self._scrub_pointer_x is not None:
            return self._scrub_pointer_x
        return self._x_for_frame(self._playhead_frame)

    def _range_color(self, rng: VideoFrameRange, *, active: bool) -> QColor:
        return range_color_qcolor(rng.id, active=active, alpha=150 if not active else 255)

    def clear_overlap_cycle(self) -> None:
        self._overlap_cycle_frame = None
        self._overlap_cycle_ids = ()

    def _ranges_at_frame(self, frame: int) -> list[VideoFrameRange]:
        hits: list[VideoFrameRange] = []
        for rng in self._ranges:
            lo, hi = sorted((rng.in_frame, rng.out_frame))
            if lo <= frame <= hi:
                hits.append(rng)
        hits.sort(key=lambda r: (-r.in_frame, -r.out_frame, r.id))
        return hits

    def _footer_zone_at(self, x: int, y: int) -> str:
        track = self._track_rect()
        if x < 0 or y < 0 or x >= self.width() or y >= self.height():
            return ""
        if y < track.top():
            return "ruler"
        if not track.contains(x, y):
            return "ruler"
        if self._timeline_list_mode == "markers" and self._hit_marker_at(x, y) is not None:
            return "marker"
        handle = self._hit_handle(x, y)
        if handle == "in":
            return "handle_in"
        if handle == "out":
            return "handle_out"
        if self._edit_id and self._hit_edit_body(x, y) is not None:
            return "range_move"
        frame = self._frame_at_x(x)
        hits = self._ranges_at_frame(frame)
        if len(hits) > 1:
            return "range_overlap"
        if hits:
            return "range"
        return "track"

    def _emit_footer_context(self, x: int, y: int) -> None:
        zone = self._footer_zone_at(x, y)
        if zone == self._last_footer_zone:
            return
        self._last_footer_zone = zone
        self.footer_context_changed.emit(zone)

    def _marker_at_frame(self, frame: int, *, tolerance: int = 2) -> VideoReviewMarker | None:
        best: VideoReviewMarker | None = None
        best_d = tolerance + 1
        for m in self._markers:
            d = abs(m.frame - frame)
            if d <= tolerance and d < best_d:
                best_d = d
                best = m
        return best

    def _hit_marker_at(self, x: int, y: int, *, px_tolerance: int = 10) -> VideoReviewMarker | None:
        if not self._markers:
            return None
        if y < 0 or y > self._track_rect().bottom():
            return None
        best: VideoReviewMarker | None = None
        best_dx = px_tolerance + 1
        for m in self._markers:
            px = self._x_for_frame(m.frame)
            dx = abs(x - px)
            if dx <= px_tolerance and dx < best_dx:
                best_dx = dx
                best = m
        return best

    def _draw_markers(self, painter: QPainter, track: QRect) -> None:
        if not self._markers:
            return
        for m in self._markers:
            px = self._x_for_frame(m.frame)
            active = m.id == self._marker_highlight_id
            color = QColor("#fafafa") if active else _MARKER_COLOR
            painter.setPen(QPen(color, 2 if active else 1))
            painter.drawLine(px, 0, px, track.bottom())
            cy = max(8, self._ruler_h() // 2)
            size = 7 if active else 5
            painter.setBrush(color if active else QColor(244, 114, 182, 210))
            painter.drawPolygon(
                QPolygon(
                    [
                        QPoint(px, cy - size),
                        QPoint(px + size, cy),
                        QPoint(px, cy + size),
                        QPoint(px - size, cy),
                    ]
                )
            )

    def _pick_range_at(self, frame: int) -> VideoFrameRange | None:
        hits = self._ranges_at_frame(frame)
        if not hits:
            self.clear_overlap_cycle()
            return None
        ids = tuple(r.id for r in hits)
        if len(hits) == 1:
            self._overlap_cycle_frame = frame
            self._overlap_cycle_ids = ids
            return hits[0]
        if (
            self._overlap_cycle_frame == frame
            and self._overlap_cycle_ids == ids
            and self._highlight_id in ids
        ):
            idx = ids.index(self._highlight_id)
            return hits[(idx + 1) % len(hits)]
        self._overlap_cycle_frame = frame
        self._overlap_cycle_ids = ids
        return hits[0]

    def _hit_range_bar_at(self, x: int, y: int) -> VideoFrameRange | None:
        """Range under the colored bar band (not ruler / empty track row)."""
        if self._timeline_list_mode != "ranges":
            return None
        track = self._track_rect()
        if not track.contains(x, y):
            return None
        band_top, band_h = self._band_metrics(track)
        if not (band_top <= y <= band_top + band_h - 1):
            return None
        return self._pick_range_at(self._frame_at_x(x))

    def _frame_in_range(self, frame: int, range_id: str) -> bool:
        rng = next((r for r in self._ranges if r.id == range_id), None)
        if rng is None:
            return False
        lo, hi = sorted((rng.in_frame, rng.out_frame))
        return lo <= int(frame) <= hi

    def _hit_handle(self, x: int, y: int) -> str | None:
        if self._timeline_list_mode == "markers" or self._edit_id is None:
            return None
        active = next((r for r in self._ranges if r.id == self._edit_id), None)
        if active is None:
            return None
        track = self._track_rect()
        band_top, band_h = self._band_metrics(track)
        band_bottom = band_top + band_h - 1
        if not (band_top <= y <= band_bottom):
            return None
        for role, frame in (("in", active.in_frame), ("out", active.out_frame)):
            cx = self._x_for_frame(frame)
            if abs(x - cx) <= self._HANDLE_W + 4:
                return role
        return None

    def _hit_edit_body(self, x: int, y: int) -> VideoFrameRange | None:
        if self._timeline_list_mode == "markers" or self._edit_id is None or self._hit_handle(x, y) is not None:
            return None
        active = next((r for r in self._ranges if r.id == self._edit_id), None)
        if active is None:
            return None
        track = self._track_rect()
        band_top, band_h = self._band_metrics(track)
        band_bottom = band_top + band_h - 1
        if not (band_top <= y <= band_bottom):
            return None
        frame = self._frame_at_x(x)
        lo, hi = sorted((active.in_frame, active.out_frame))
        if lo <= frame <= hi:
            return active
        return None

    def _clamp_moved_range(self, in_f: int, out_f: int, delta: int) -> tuple[int, int]:
        lo, hi = sorted((int(in_f), int(out_f)))
        new_lo = lo + delta
        new_hi = hi + delta
        max_f = self.maximum()
        if new_lo < 0:
            new_hi += -new_lo
            new_lo = 0
        if new_hi > max_f:
            new_lo -= new_hi - max_f
            new_hi = max_f
        new_lo = max(0, min(new_lo, max_f))
        new_hi = max(0, min(new_hi, max_f))
        return new_lo, new_hi

    def _major_tick_interval_sec(self, track_width: int) -> float:
        duration = self._visible_duration_sec()
        if duration <= 0 or track_width <= 0:
            return 1.0
        min_px = 72.0
        candidates = (0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0)
        for sec in candidates:
            if sec / duration * track_width >= min_px:
                return sec
        return 300.0

    def _draw_selection_dim(self, painter: QPainter, track: QRect, widget_w: int) -> None:
        sel_id = self._highlight_id or self._edit_id
        if not sel_id:
            return
        rng = next((r for r in self._ranges if r.id == sel_id), None)
        if rng is None:
            return
        lo, hi = sorted((rng.in_frame, rng.out_frame))
        x_in = self._x_for_frame(lo)
        x_out = self._x_for_frame(hi)
        left = self._MARGIN_H
        right = max(left, widget_w - self._MARGIN_H)
        top = 0
        bottom = track.bottom()
        h = bottom - top + 1
        dim = QColor(0, 0, 0, 150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dim)
        if x_in > left:
            painter.drawRect(left, top, x_in - left, h)
        if x_out < right:
            painter.drawRect(x_out, top, right - x_out, h)

    def _draw_range_end_caps(
        self,
        painter: QPainter,
        track: QRect,
        in_f: int,
        out_f: int,
        *,
        bold: bool,
    ) -> None:
        band_top, band_h = self._band_metrics(track)
        if bold:
            cap_w = self._HANDLE_W
            y = band_top - 2
            h = band_h + 4
            painter.setBrush(QColor(MONOS_COLORS.get("blue_400", "#60a5fa")))
            painter.setPen(QPen(QColor("#fafafa"), 2))
        else:
            cap_w = self._END_CAP_W
            y = band_top
            h = band_h
            painter.setBrush(QColor(255, 255, 255, 210))
            painter.setPen(QPen(QColor(255, 255, 255, 180), 2))
        for f in (in_f, out_f):
            cx = self._x_for_frame(f)
            painter.drawRoundedRect(cx - cap_w // 2, y, cap_w, h, 2, 2)

    def _draw_draft_marker(
        self,
        painter: QPainter,
        track: QRect,
        frame: int,
        role: str,
    ) -> None:
        px = self._x_for_frame(frame)
        accent = QColor(MONOS_COLORS.get("blue_400", "#60a5fa"))
        band_top, band_h = self._band_metrics(track)
        cap_w = self._END_CAP_W

        painter.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
        painter.drawLine(px, 0, px, track.bottom())

        painter.setPen(QPen(accent, 2))
        painter.setBrush(QColor(37, 99, 235, 150))
        painter.drawRoundedRect(px - cap_w // 2, band_top, cap_w, band_h, 2, 2)

        short = "I" if role == "in" else "O"
        label = f"{short} {format_frame_label(frame)}"
        font = monos_font("JetBrains Mono", 10, QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        pad_x = 5
        pad_y = 2
        tw = fm.horizontalAdvance(label) + pad_x * 2
        th = fm.height() + pad_y * 2
        x = px - tw // 2
        x = max(2, min(x, self.width() - tw - 2))
        y = 2
        painter.setPen(QPen(accent, 1))
        painter.setBrush(QColor(9, 9, 11, 235))
        painter.drawRoundedRect(x, y, tw, th, 4, 4)
        painter.setPen(accent)
        painter.drawText(x + pad_x, y + th - pad_y - 1, label)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = self._track_rect()
        w = self.width()
        bg = QColor(MONOS_COLORS.get("bg_elevated", "#1b1b1b"))
        painter.fillRect(self.rect(), bg)

        # Ruler baseline — proxy cache (solid) + in-progress build (growing green)
        ruler_y = self._ruler_h() - 1
        self._draw_proxy_ruler_overlay(painter, ruler_y)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.drawLine(self._MARGIN_H, ruler_y, w - self._MARGIN_H, ruler_y)

        major_sec = self._major_tick_interval_sec(track.width())
        self._draw_frame_ticks(painter, track, major_sec=major_sec)

        duration_sec = self._duration_sec()
        t_start = self._view_start / self._fps
        t_end = self._view_end() / self._fps
        tick_font = monos_font("JetBrains Mono", 9, QFont.Weight.Medium)
        painter.setFont(tick_font)
        painter.setPen(QColor(MONOS_COLORS.get("text_muted", "#71717a")))
        t = math.floor(t_start / major_sec) * major_sec if major_sec > 0 else t_start
        while t <= t_end + 1e-9:
            x = self._x_for_time(t)
            painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
            painter.drawLine(x, ruler_y - 8, x, ruler_y)
            label = format_ruler_tick(t, self._fps, mode=self._time_display_mode)
            painter.setPen(QColor(MONOS_COLORS.get("text_muted", "#71717a")))
            painter.drawText(x + 3, ruler_y - 9, label)
            t += major_sec

        # Track background (range band row only — scrubber chrome height unchanged)
        band_top, band_h = self._band_metrics(track)
        band_row = QRect(track.left(), band_top, track.width(), band_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 14))
        painter.drawRoundedRect(band_row, 3, 3)

        if self._timeline_list_mode == "markers":
            self._draw_markers(painter, track)

        if self._timeline_list_mode == "ranges":

            def draw_band(
                in_f: int,
                out_f: int,
                rng: VideoFrameRange | None,
                *,
                editing: bool,
                highlighted: bool,
                draft: bool,
            ) -> None:
                x1 = self._x_for_frame(in_f)
                x2 = self._x_for_frame(out_f)
                if x2 < x1:
                    x1, x2 = x2, x1
                band_top, band_h = self._band_metrics(track)
                width = max(3, x2 - x1)
                if draft:
                    painter.setPen(QPen(QColor(MONOS_COLORS.get("blue_400", "#60a5fa")), 1, Qt.PenStyle.DashLine))
                    painter.setBrush(QColor(37, 99, 235, 48))
                elif editing:
                    painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
                    painter.setBrush(self._range_color(rng, active=True) if rng else QColor(37, 99, 235, 180))
                elif highlighted:
                    painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
                    color = self._range_color(rng, active=False) if rng else QColor(37, 99, 235, 64)
                    painter.setBrush(color)
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    color = self._range_color(rng, active=False) if rng else QColor(37, 99, 235, 64)
                    painter.setBrush(color)
                painter.drawRoundedRect(x1, band_top, width, band_h, 3, 3)

            for rng in self._ranges:
                if rng.id in (self._highlight_id, self._edit_id):
                    continue
                draw_band(
                    rng.in_frame,
                    rng.out_frame,
                    rng,
                    editing=False,
                    highlighted=False,
                    draft=False,
                )

            if self._highlight_id and self._highlight_id != self._edit_id:
                rng = next((r for r in self._ranges if r.id == self._highlight_id), None)
                if rng is not None:
                    draw_band(
                        rng.in_frame,
                        rng.out_frame,
                        rng,
                        editing=False,
                        highlighted=True,
                        draft=False,
                    )

            if self._edit_id:
                rng = next((r for r in self._ranges if r.id == self._edit_id), None)
                if rng is not None:
                    draw_band(
                        rng.in_frame,
                        rng.out_frame,
                        rng,
                        editing=True,
                        highlighted=False,
                        draft=False,
                    )

            has_draft_in = self._draft_in is not None
            has_draft_out = self._draft_out is not None
            if has_draft_in and has_draft_out:
                in_f = int(self._draft_in)
                out_f = int(self._draft_out)
                if in_f <= out_f:
                    draw_band(in_f, out_f, None, editing=False, highlighted=False, draft=True)
                    self._draw_range_end_caps(painter, track, in_f, out_f, bold=False)
                else:
                    self._draw_draft_marker(painter, track, in_f, "in")
                    self._draw_draft_marker(painter, track, out_f, "out")
            elif has_draft_in:
                self._draw_draft_marker(painter, track, int(self._draft_in), "in")
            elif has_draft_out:
                self._draw_draft_marker(painter, track, int(self._draft_out), "out")

            self._draw_selection_dim(painter, track, w)

            if self._highlight_id and self._highlight_id != self._edit_id:
                rng = next((r for r in self._ranges if r.id == self._highlight_id), None)
                if rng is not None:
                    self._draw_range_end_caps(
                        painter, track, rng.in_frame, rng.out_frame, bold=False
                    )

            if self._edit_id:
                rng = next((r for r in self._ranges if r.id == self._edit_id), None)
                if rng is not None:
                    self._draw_range_end_caps(
                        painter, track, rng.in_frame, rng.out_frame, bold=True
                    )

        # Playhead
        px = self._playhead_x()
        painter.setPen(QPen(QColor("#fafafa"), self._PLAYHEAD_W))
        painter.drawLine(px, 0, px, track.bottom())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#fafafa"))
        painter.drawPolygon(
            QPolygon([QPoint(px - 5, 0), QPoint(px + 5, 0), QPoint(px, 7)])
        )
        self._draw_playhead_frame_badge(painter, track, px, self._playhead_frame)

        painter.end()

    def _draw_playhead_frame_badge(
        self,
        painter: QPainter,
        track: QRect,
        px: int,
        frame: int,
    ) -> None:
        label = format_frame_label(frame)
        font = monos_font("JetBrains Mono", 10, QFont.Weight.Medium)
        painter.setFont(font)
        fm = painter.fontMetrics()
        pad_x = 6
        pad_y = 2
        tw = fm.horizontalAdvance(label) + pad_x * 2
        th = fm.height() + pad_y * 2
        gap = 8
        x = px + gap
        if x + tw > self.width() - 2:
            x = px - tw - gap
        x = max(2, min(x, self.width() - tw - 2))
        y = track.top() + max(2, (track.height() - th) // 2)
        painter.setPen(QPen(QColor("#3f3f46"), 1))
        painter.setBrush(QColor(9, 9, 11, 230))
        painter.drawRoundedRect(x, y, tw, th, 4, 4)
        painter.setPen(QColor("#fafafa"))
        painter.drawText(x + pad_x, y + th - pad_y - 1, label)

    @staticmethod
    def _wheel_vertical_delta(event: QWheelEvent) -> float:
        """Vertical scroll amount; Qt maps Alt+wheel to angleDelta.x on Windows."""
        dy = event.angleDelta().y()
        if dy:
            return float(dy)
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            dx = event.angleDelta().x()
            if dx:
                return float(dx)
        py = event.pixelDelta().y()
        if py:
            return float(py)
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            px = event.pixelDelta().x()
            if px:
                return float(px)
        return 0.0

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta_y = self._wheel_vertical_delta(event)
        if delta_y == 0:
            super().wheelEvent(event)
            return
        pos = event.position()
        anchor_x = int(pos.x()) if hasattr(event, "position") else event.x()
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            steps = delta_y / self._WHEEL_ZOOM_DEG
            factor = math.pow(self._ZOOM_WHEEL_FACTOR, steps)
            self._zoom_at(factor, anchor_x)
            event.accept()
            return
        if self.is_zoomed():
            pan = -delta_y / self._WHEEL_ZOOM_DEG * self._view_span * 0.12
            self._pan_by_frames(pan)
            event.accept()
            return
        super().wheelEvent(event)

    def _prompt_rename(self, rng: VideoFrameRange) -> None:
        text, ok = QInputDialog.getText(self, "Rename range", "Name", text=rng.label or "")
        if ok:
            self.range_rename_requested.emit(rng.id, (text or "").strip()[:80])

    def _copy_range_text(self, rng: VideoFrameRange) -> None:
        text = format_range_span_display(rng, self._fps, mode=self._time_display_mode)
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(text)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        pos = event.pos()
        x = int(pos.x())
        y = int(pos.y())
        frame = self._frame_at_x(x)
        track = self._track_rect()
        on_track = track.contains(x, y)

        rng: VideoFrameRange | None = None
        if on_track:
            rng = self._pick_range_at(frame)
            if rng is not None:
                self.range_highlighted.emit(rng.id)

        menu = MonosMenu(self)
        act_seek = act_min = act_mout = act_add = act_fit = act_zoom_view = act_clear = None
        act_in = act_out = act_edit = act_zoom = act_rename = act_dup = act_copy = act_del = None

        if rng is not None:
            act_in = menu.addAction("Go to In")
            act_out = menu.addAction("Go to Out")
            act_edit = menu.addAction("Edit range")
            act_zoom = menu.addAction("Zoom to range")
            menu.addSeparator()
            act_min = menu.addAction("Set In here")
            act_mout = menu.addAction("Set Out here")
            menu.addSeparator()
            act_rename = menu.addAction("Rename…")
            act_dup = menu.addAction("Duplicate")
            act_copy = menu.addAction("Copy range text")
            menu.addSeparator()
            act_del = menu.addAction("Delete")
            act_del.setProperty("class", "danger-action")
        else:
            act_seek = menu.addAction(f"Seek to {format_frame_label(frame)}")
            act_min = menu.addAction("Mark In")
            act_mout = menu.addAction("Mark Out")

        if (
            self._draft_in is not None
            and self._draft_out is not None
            and self._draft_in <= self._draft_out
        ):
            menu.addSeparator()
            act_add = menu.addAction("Add range")

        menu.addSeparator()
        act_fit = menu.addAction("Fit timeline (Alt+F)")
        focus_id = rng.id if rng is not None else self._highlight_id
        if focus_id and rng is None:
            act_zoom_view = menu.addAction("Focus to selected range (F)")
        if self._highlight_id or self._edit_id:
            menu.addSeparator()
            act_clear = menu.addAction("Deselect range")
        act_clear_marker = None
        if self._timeline_list_mode == "markers" and self._marker_highlight_id:
            menu.addSeparator()
            act_clear_marker = menu.addAction("Deselect marker")

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return

        if chosen == act_in and rng is not None:
            self.go_to_in_requested.emit(rng.id)
        elif chosen == act_out and rng is not None:
            self.go_to_out_requested.emit(rng.id)
        elif chosen == act_edit and rng is not None:
            self.range_edit_requested.emit(rng.id)
        elif chosen == act_zoom and rng is not None:
            self.focus_range_requested.emit(rng.id)
        elif chosen == act_min:
            if rng is not None:
                self.range_edit_requested.emit(rng.id)
            self.mark_in_at_frame.emit(frame)
        elif chosen == act_mout:
            if rng is not None:
                self.range_edit_requested.emit(rng.id)
            self.mark_out_at_frame.emit(frame)
        elif chosen == act_rename and rng is not None:
            self._prompt_rename(rng)
        elif chosen == act_dup and rng is not None:
            self.range_duplicate_requested.emit(rng.id)
        elif chosen == act_copy and rng is not None:
            self._copy_range_text(rng)
        elif chosen == act_del and rng is not None:
            self.range_delete_requested.emit(rng.id)
        elif chosen == act_seek:
            self.seek_to_frame.emit(frame)
        elif chosen == act_add:
            self.add_range_requested.emit()
        elif chosen == act_fit:
            self.fit_timeline_requested.emit()
        elif chosen == act_zoom_view and focus_id:
            self.focus_range_requested.emit(focus_id)
        elif chosen == act_clear:
            self.clear_overlap_cycle()
            self.range_deselected.emit()
        elif chosen == act_clear_marker:
            self.marker_deselected.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_anchor_x = int(event.position().x()) if hasattr(event, "position") else event.x()
            self._pan_origin_start = self._view_start
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x = int(event.position().x()) if hasattr(event, "position") else event.x()
        y = int(event.position().y()) if hasattr(event, "position") else event.y()

        if self._timeline_list_mode == "markers":
            hit_m = self._hit_marker_at(x, y)
            if hit_m is not None:
                self.marker_highlighted.emit(hit_m.id)
                event.accept()
                return

        handle = self._hit_handle(x, y)
        if handle is not None:
            self._dragging = True
            self._drag_handle = handle
            self.range_handles_drag_started.emit()
            event.accept()
            return

        move_rng = self._hit_edit_body(x, y)
        if move_rng is not None:
            self._dragging = True
            self._drag_handle = "move"
            self._drag_move_anchor_frame = self._frame_at_x(x)
            self._drag_move_origin_in = move_rng.in_frame
            self._drag_move_origin_out = move_rng.out_frame
            self.range_handles_drag_started.emit()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        track = self._track_rect()
        if track.contains(x, y):
            if self._timeline_list_mode == "ranges":
                hit = self._hit_range_bar_at(x, y)
                if hit is not None:
                    self.range_highlighted.emit(hit.id)
                    event.accept()
                    return

        self.clear_overlap_cycle()

        press_zone = self._footer_zone_at(x, y)
        self._press_x = x
        self._press_frame = self._frame_at_x(x)
        self._press_outside_marker = bool(
            self._timeline_list_mode == "markers"
            and self._marker_highlight_id
            and self._hit_marker_at(x, y) is None
        )
        self._dragging = True
        self._drag_handle = None
        self._scrub_pointer_x = self._clamp_track_x(x)
        frame = self._frame_at_x(x)
        self.setValue(frame)
        self.sliderPressed.emit()
        self.frame_preview.emit(frame)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        x = int(event.position().x()) if hasattr(event, "position") else event.x()
        y = int(event.position().y()) if hasattr(event, "position") else event.y()
        track = self._track_rect()
        if not track.contains(x, y):
            super().mouseDoubleClickEvent(event)
            return
        frame = self._frame_at_x(x)
        if self._timeline_list_mode == "ranges":
            sel_id = self._edit_id or self._highlight_id
            if sel_id and self._hit_range_bar_at(x, y) is None:
                if not self._frame_in_range(frame, sel_id):
                    self.clear_overlap_cycle()
                    self.range_deselected.emit()
                    event.accept()
                    return
        hits = self._ranges_at_frame(frame)
        if not hits:
            super().mouseDoubleClickEvent(event)
            return
        if len(hits) > 1:
            event.accept()
            return
        rid = self._highlight_id or hits[0].id
        self.range_edit_requested.emit(rid)
        event.accept()

    def enterEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            pos = self.mapFromGlobal(QCursor.pos())
            x = max(0, min(int(pos.x()), self.width() - 1))
            y = max(0, min(int(pos.y()), self.height() - 1))
            frame = self._frame_at_x(x)
            self._last_hover_frame = frame
            self.hover_frame.emit(frame)
            self._emit_footer_context(x, y)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._last_hover_frame = None
        self.hover_frame.emit(-1)
        self._last_footer_zone = None
        self.footer_context_changed.emit("")
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        x = int(event.position().x()) if hasattr(event, "position") else event.x()
        y = int(event.position().y()) if hasattr(event, "position") else event.y()
        if self._panning:
            track = self._track_rect()
            if track.width() > 0:
                dx = x - self._pan_anchor_x
                frame_delta = -dx / track.width() * self._view_span
                self._view_start = self._pan_origin_start + frame_delta
                self._clamp_view()
                self.update()
            event.accept()
            return
        if not self._dragging:
            frame = self._frame_at_x(x)
            if frame != self._last_hover_frame:
                self._last_hover_frame = frame
                self.hover_frame.emit(frame)
            self._emit_footer_context(x, y)
            if self._edit_id and self._hit_edit_body(x, y) is not None:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            elif self._edit_id and self._hit_handle(x, y) is not None:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.unsetCursor()
            super().mouseMoveEvent(event)
            return

        frame = self._frame_at_x(x)
        editing = next((r for r in self._ranges if r.id == self._edit_id), None)
        if editing is not None and self._drag_handle == "in":
            if frame <= editing.out_frame:
                self.in_out_changed.emit(frame, editing.out_frame)
            self.update()
            event.accept()
            return
        if editing is not None and self._drag_handle == "out":
            if frame >= editing.in_frame:
                self.in_out_changed.emit(editing.in_frame, frame)
            self.update()
            event.accept()
            return
        if editing is not None and self._drag_handle == "move":
            delta = frame - self._drag_move_anchor_frame
            in_f, out_f = self._clamp_moved_range(
                self._drag_move_origin_in,
                self._drag_move_origin_out,
                delta,
            )
            self.in_out_changed.emit(in_f, out_f)
            self.update()
            event.accept()
            return
        self._scrub_pointer_x = self._clamp_track_x(x)
        self.setValue(frame)
        self.frame_preview.emit(frame)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        was_dragging = self._dragging
        handle = self._drag_handle
        self._dragging = False
        self._drag_handle = None
        self._scrub_pointer_x = None
        self.unsetCursor()
        if was_dragging and handle is None:
            rx = int(event.position().x()) if hasattr(event, "position") else event.x()
            small_click = abs(rx - self._press_x) <= self._CLICK_DRAG_THRESHOLD_PX
            if self._press_outside_marker and small_click:
                self.marker_deselected.emit()
            self.seek_released.emit(float(self.value()))
        self._press_outside_marker = False
        super().mouseReleaseEvent(event)
