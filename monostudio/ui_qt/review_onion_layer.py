"""Onion-skin ghost frames behind the review draw overlay."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ReviewOnionLayer(QWidget):
    """Semi-transparent prev/next plate ghosts (no stroke ghosts in v1)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._enabled = False
        self._prev_pix: QPixmap | None = None
        self._next_pix: QPixmap | None = None
        self._prev_opacity = 0.35
        self._next_opacity = 0.25

    def set_onion_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.setVisible(enabled and (self._prev_pix is not None or self._next_pix is not None))
        self.update()

    def set_opacities(self, prev: float, next_: float) -> None:
        self._prev_opacity = max(0.0, min(1.0, float(prev)))
        self._next_opacity = max(0.0, min(1.0, float(next_)))
        self.update()

    def set_ghost_pixmaps(self, prev: QPixmap | None, next_: QPixmap | None) -> None:
        self._prev_pix = prev if prev is not None and not prev.isNull() else None
        self._next_pix = next_ if next_ is not None and not next_.isNull() else None
        show = self._enabled and (self._prev_pix is not None or self._next_pix is not None)
        self.setVisible(show)
        self.update()

    def clear_ghosts(self) -> None:
        self._prev_pix = None
        self._next_pix = None
        self.setVisible(False)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._enabled:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect()
        if self._prev_pix is not None:
            self._paint_fit(painter, self._prev_pix, rect, self._prev_opacity)
        if self._next_pix is not None:
            self._paint_fit(painter, self._next_pix, rect, self._next_opacity)
        painter.end()

    def _paint_fit(self, painter: QPainter, pix: QPixmap, rect: QRect, opacity: float) -> None:
        if pix.isNull() or rect.isEmpty():
            return
        scaled = pix.scaled(
            rect.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = rect.x() + (rect.width() - scaled.width()) // 2
        y = rect.y() + (rect.height() - scaled.height()) // 2
        painter.setOpacity(opacity)
        painter.drawPixmap(x, y, scaled)
        painter.setOpacity(1.0)
