"""Geometry-based hit testing for inline note images in QTextDocument widgets."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF
from PySide6.QtWidgets import QAbstractScrollArea, QTextEdit


def _viewport_to_document_point(widget: QTextEdit, viewport_pos: QPoint) -> QPointF:
    x = float(viewport_pos.x())
    y = float(viewport_pos.y())
    if isinstance(widget, QAbstractScrollArea):
        x += float(widget.horizontalScrollBar().value())
        y += float(widget.verticalScrollBar().value())
    return QPointF(x, y)


def image_href_at_widget_pos(widget: QTextEdit, viewport_pos: QPoint) -> str | None:
    """
    Return image resource href under a viewport position.

    Uses QAbstractTextDocumentLayout.imageAt() for full thumbnail hit area.
    """
    doc = widget.document()
    layout = doc.documentLayout()
    if layout is None:
        return None

    doc_pt = _viewport_to_document_point(widget, viewport_pos)
    href = (layout.imageAt(doc_pt) or "").strip()
    if href:
        return href

    cursor = widget.cursorForPosition(viewport_pos)
    fmt = cursor.charFormat()
    if fmt.isImageFormat():
        return fmt.toImageFormat().name()
    return None
