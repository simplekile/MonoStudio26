"""Vector draw overlay on the review player viewport."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QRectF, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPen, QColor, QPolygonF
from PySide6.QtWidgets import QWidget

from monostudio.core.review_draw import (
    DrawTool,
    ReviewDrawLayer,
    ReviewDrawStroke,
    composite_strokes_at,
    onion_strokes_next,
    onion_strokes_prev,
)


def _norm_point(widget: QWidget, pos: QPointF) -> tuple[float, float]:
    if hasattr(widget, "_map_event_to_content_space"):
        pos = widget._map_event_to_content_space(pos)  # type: ignore[attr-defined]
    content = _content_rect_f(widget)
    return (
        max(0.0, min(1.0, (pos.x() - content.x()) / max(1.0, content.width()))),
        max(0.0, min(1.0, (pos.y() - content.y()) / max(1.0, content.height()))),
    )


def _content_rect_f(widget: QWidget) -> QRectF:
    if hasattr(widget, "_video_content_rect_f"):
        return widget._video_content_rect_f()  # type: ignore[attr-defined]
    w = max(1.0, float(widget.width()))
    h = max(1.0, float(widget.height()))
    return QRectF(0.0, 0.0, w, h)


def _to_widget(widget: QWidget, pt: tuple[float, float]) -> QPointF:
    content = _content_rect_f(widget)
    return QPointF(
        content.x() + pt[0] * content.width(),
        content.y() + pt[1] * content.height(),
    )


class ReviewDrawOverlay(QWidget):
    """Transparent overlay for pen / arrow / rect strokes."""

    stroke_committed = Signal(object)  # ReviewDrawStroke

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._draw_active = False
        self._tool: DrawTool = "pen"
        self._color = "#ef4444"
        self._width_px = 3.0
        self._layers: list[ReviewDrawLayer] = []
        self._active_layer_id: str | None = None
        self._current_frame = 0
        self._total_frames = 0
        self._onion_enabled = False
        self._onion_span = 2
        self._draft_points: list[tuple[float, float]] = []
        self._dragging = False
        self._viewport_zoom = 1.0
        self._viewport_pan = QPointF(0.0, 0.0)
        self._viewport_aspect = 16.0 / 9.0
        self._viewport_paint_transform = False

    def set_viewport_transform(
        self,
        zoom: float,
        pan: QPointF,
        aspect: float,
        *,
        enabled: bool = True,
    ) -> None:
        self._viewport_zoom = max(1.0, float(zoom))
        self._viewport_pan = QPointF(pan)
        self._viewport_aspect = max(1e-6, float(aspect))
        self._viewport_paint_transform = bool(enabled) and (
            self._viewport_zoom > 1.001
            or abs(self._viewport_pan.x()) > 0.5
            or abs(self._viewport_pan.y()) > 0.5
        )
        self.update()

    def _video_content_rect_f(self) -> QRectF:
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        aspect = self._viewport_aspect
        if w / h > aspect:
            ch = h
            cw = ch * aspect
        else:
            cw = w
            ch = cw / aspect
        return QRectF((w - cw) / 2.0, (h - ch) / 2.0, cw, ch)

    def _viewport_pivot(self) -> QPointF:
        center = self._video_content_rect_f().center()
        return QPointF(center.x() + self._viewport_pan.x(), center.y() + self._viewport_pan.y())

    def _map_event_to_content_space(self, pos: QPointF) -> QPointF:
        if not self._viewport_paint_transform:
            return pos
        pivot = self._viewport_pivot()
        z = self._viewport_zoom
        return pivot + (pos - pivot) / z

    def set_draw_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._draw_active:
            return
        self._draw_active = active
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not active,
        )
        self.setMouseTracking(active)
        if not active:
            self._draft_points = []
            self._dragging = False
        self.update()

    def set_tool(self, tool: DrawTool) -> None:
        self._tool = tool
        self._draft_points = []
        self._dragging = False
        self.update()

    def set_color(self, color: str) -> None:
        self._color = (color or "#ef4444").strip() or "#ef4444"
        self.update()

    def set_width_px(self, width: float) -> None:
        self._width_px = max(1.0, min(24.0, float(width)))
        self.update()

    def tool(self) -> DrawTool:
        return self._tool

    def color(self) -> str:
        return self._color

    def width_px(self) -> float:
        return self._width_px

    def set_total_frames(self, total: int) -> None:
        self._total_frames = max(0, int(total))
        self.update()

    def set_layers(
        self,
        layers: list[ReviewDrawLayer],
        *,
        active_layer_id: str | None = None,
    ) -> None:
        self._layers = list(layers)
        self._active_layer_id = active_layer_id
        self.update()

    def set_current_frame(self, frame: int) -> None:
        frame = int(frame)
        if frame == self._current_frame and not self._onion_enabled and not self._draft_points:
            return
        self._current_frame = frame
        self.update()

    def set_onion(self, *, enabled: bool, span: int = 2) -> None:
        self._onion_enabled = bool(enabled)
        self._onion_span = max(1, min(5, int(span)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._viewport_paint_transform:
            pivot = self._viewport_pivot()
            painter.translate(pivot)
            painter.scale(self._viewport_zoom, self._viewport_zoom)
            painter.translate(-pivot)
        scale = max(1.0, float(self.height()) / 1080.0)
        frame = self._current_frame
        if self._onion_enabled:
            prev_strokes = onion_strokes_prev(
                self._layers,
                frame,
                span=self._onion_span,
                active_layer_id=self._active_layer_id,
            )
            next_strokes = onion_strokes_next(
                self._layers,
                frame,
                span=self._onion_span,
                active_layer_id=self._active_layer_id,
            )
            for stroke in prev_strokes:
                self._paint_stroke(painter, stroke, scale, draft_alpha=90)
            for stroke in next_strokes:
                self._paint_stroke(painter, stroke, scale, draft_alpha=60)
        for stroke in composite_strokes_at(self._layers, frame, total_frames=self._total_frames):
            self._paint_stroke(painter, stroke, scale)
        if self._draft_points:
            draft_tool = "pen" if self._tool == "eraser" else self._tool
            draft = ReviewDrawStroke(draft_tool, self._color, self._width_px, list(self._draft_points))
            draft_alpha = 120 if self._tool == "eraser" else 200
            self._paint_stroke(painter, draft, scale, draft_alpha=draft_alpha)
        painter.end()

    def _paint_stroke(
        self,
        painter: QPainter,
        stroke: ReviewDrawStroke,
        scale: float,
        *,
        draft_alpha: int = 255,
    ) -> None:
        if not stroke.points:
            return
        width = max(1.0, stroke.width_px * scale)
        color = QColor(stroke.color)
        color.setAlpha(draft_alpha)
        if stroke.tool == "eraser":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            pen = QPen(Qt.GlobalColor.transparent, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        else:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        pts = [_to_widget(self, p) for p in stroke.points]
        if stroke.tool == "pen":
            for i in range(len(pts) - 1):
                painter.drawLine(pts[i], pts[i + 1])
            return
        if stroke.tool in ("arrow", "rect") and len(pts) >= 2:
            a, b = pts[0], pts[-1]
            if stroke.tool == "rect":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(_rectf_from_points(a, b))
                return
            painter.drawLine(a, b)
            self._draw_arrow_head(painter, a, b, width)
            return
        if len(pts) == 1:
            painter.drawPoint(pts[0])

    def _draw_arrow_head(self, painter: QPainter, a: QPointF, b: QPointF, width: float) -> None:
        import math

        dx = b.x() - a.x()
        dy = b.y() - a.y()
        length = math.hypot(dx, dy)
        if length < 1e-3:
            return
        ux, uy = dx / length, dy / length
        size = max(8.0, width * 3.0)
        px, py = -uy, ux
        tip = b
        left = QPointF(tip.x() - ux * size + px * size * 0.4, tip.y() - uy * size + py * size * 0.4)
        right = QPointF(tip.x() - ux * size - px * size * 0.4, tip.y() - uy * size - py * size * 0.4)
        painter.setBrush(painter.pen().color())
        painter.drawPolygon(QPolygonF([tip, left, right]))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            event.ignore()
            return
        if not self._draw_active or event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        pt = _norm_point(self, event.position())
        if self._tool == "pen" or self._tool == "eraser":
            self._draft_points = [pt]
        else:
            self._draft_points = [pt, pt]
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._draw_active or not self._dragging:
            return
        pt = _norm_point(self, event.position())
        if self._tool == "pen" or self._tool == "eraser":
            self._draft_points.append(pt)
        elif self._draft_points:
            if len(self._draft_points) == 1:
                self._draft_points.append(pt)
            else:
                self._draft_points[-1] = pt
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._draw_active or event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = False
        if not self._draft_points:
            return
        pts = list(self._draft_points)
        if self._tool in ("arrow", "rect") and len(pts) >= 2:
            pts = [pts[0], pts[-1]]
        elif self._tool == "pen" and len(pts) < 2:
            pts = [pts[0], (pts[0][0] + 0.001, pts[0][1] + 0.001)]
        elif self._tool == "eraser" and len(pts) < 2:
            pts = [pts[0], (pts[0][0] + 0.001, pts[0][1] + 0.001)]
        if self._tool == "eraser":
            self._draft_points = []
            self.stroke_committed.emit(
                ReviewDrawStroke("eraser", self._color, self._width_px, pts)
            )
            self.update()
            return
        stroke = ReviewDrawStroke(self._tool, self._color, self._width_px, pts)
        self._draft_points = []
        self.stroke_committed.emit(stroke)
        self.update()


def _rectf_from_points(a: QPointF, b: QPointF) -> QRectF:
    return QRectF(
        min(a.x(), b.x()),
        min(a.y(), b.y()),
        abs(b.x() - a.x()),
        abs(b.y() - a.y()),
    )
