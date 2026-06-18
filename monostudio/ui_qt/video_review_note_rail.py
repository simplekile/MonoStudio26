"""Left note rail for unified review player — toggle with N."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from monostudio.ui_qt.video_preview_context import PreviewContext
from monostudio.ui_qt.video_review_note_panel import VideoReviewNotePanel

_NOTE_RAIL_W = 260
_RAIL_RADIUS = 12
_RAIL_BG = "#1e2124"


class _NoteRailBody(QFrame):
    """Note rail body — painted bottom-left corner."""

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        rect = QRectF(0.0, 0.0, w + 1.0, h + 1.0)
        r = min(float(_RAIL_RADIUS), rect.width() / 2, rect.height() / 2)
        path = QPainterPath()
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        path.moveTo(right, top)
        path.lineTo(left + r, top)
        path.arcTo(left, top, 2 * r, 2 * r, 90, 90)
        path.lineTo(left, bottom - r)
        path.arcTo(left, bottom - 2 * r, 2 * r, 2 * r, 180, 90)
        path.lineTo(right, bottom)
        path.lineTo(right, top)
        path.closeSubpath()
        painter.fillPath(path, QColor(_RAIL_BG))
        painter.setPen(QPen(QColor(255, 255, 255, 15), 1))
        painter.drawLine(int(right), 0, int(right), int(h))
        super().paintEvent(event)


class VideoReviewNoteRail(QWidget):
    """Collapsible left sidebar for shot notes (entity context only)."""

    open_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewNoteRail")
        self._context = PreviewContext.entity
        self._open = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._body = _NoteRailBody(self)
        self._body.setObjectName("VideoReviewNoteBody")
        self._body.setFixedWidth(_NOTE_RAIL_W)
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._panel = VideoReviewNotePanel(self._body)
        body_lay.addWidget(self._panel, 1)
        root.addWidget(self._body)
        self._apply_open_layout()

    def panel(self) -> VideoReviewNotePanel:
        return self._panel

    def is_open(self) -> bool:
        return self._open

    def apply_context(self, context: PreviewContext) -> None:
        self._context = context
        if context != PreviewContext.entity and self._open:
            self.set_open(False)

    def set_open(self, open: bool) -> None:  # noqa: A003
        open = bool(open) and self._context == PreviewContext.entity
        if self._open == open:
            self._apply_open_layout()
            return
        self._open = open
        self._apply_open_layout()
        self.open_changed.emit(self._open)

    def toggle(self) -> None:
        self.set_open(not self._open)

    def _apply_open_layout(self) -> None:
        show = self._open
        self._body.setVisible(show)
        self.setFixedWidth(_NOTE_RAIL_W if show else 0)
        self.updateGeometry()
