"""Size grip for frameless MonosDialog subclasses."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QToolButton, QWidget

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS


class DialogSizeGrip(QToolButton):
    """Bottom-right resize handle for frameless dialogs."""

    def __init__(self, resize_target: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DialogSizeGrip")
        self._target = resize_target
        self.setToolTip("Drag to resize")
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAutoRaise(True)
        self.setFixedSize(24, 24)
        self.setIcon(lucide_icon("maximize-2", size=14, color_hex=MONOS_COLORS["text_meta"]))
        self._origin: QPoint | None = None
        self._start_size: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._start_size = QPoint(self._target.width(), self._target.height())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._origin is not None
            and self._start_size is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = event.globalPosition().toPoint() - self._origin
            min_sz = self._target.minimumSize()
            w = max(min_sz.width(), self._start_size.x() + delta.x())
            h = max(min_sz.height(), self._start_size.y() + delta.y())
            self._target.resize(w, h)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = None
            self._start_size = None
        super().mouseReleaseEvent(event)
