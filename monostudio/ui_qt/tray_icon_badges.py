"""Compose system tray icon with optional status dots (notifications / updates)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

_DOT_NOTI = QColor("#ef4444")
_DOT_UPDATE = QColor("#3b82f6")
_DOT_BORDER = QColor("#121214")


def compose_tray_icon(
    base: QIcon,
    *,
    has_notification: bool = False,
    has_update: bool = False,
    logical_size: int = 32,
) -> QIcon:
    """Return a copy of base icon with small corner dots when flags are set."""
    if not has_notification and not has_update:
        return base
    pix = base.pixmap(logical_size, logical_size)
    if pix.isNull():
        return base
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    dot = max(6, logical_size // 5)
    inset = 1
    if has_notification:
        _draw_dot(painter, pix.width() - dot - inset, inset, dot, _DOT_NOTI)
    if has_update:
        _draw_dot(painter, pix.width() - dot - inset, pix.height() - dot - inset, dot, _DOT_UPDATE)
    painter.end()
    return QIcon(pix)


def _draw_dot(painter: QPainter, x: int, y: int, size: int, fill: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_DOT_BORDER)
    painter.drawEllipse(x - 1, y - 1, size + 2, size + 2)
    painter.setBrush(fill)
    painter.drawEllipse(x, y, size, size)
