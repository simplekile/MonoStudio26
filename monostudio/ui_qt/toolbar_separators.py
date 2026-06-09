"""Vertical separators between toolbar icon buttons."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget


def vertical_icon_separator(parent: QWidget, *, height: int = 24) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("ToolbarIconSeparator")
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedSize(1, height)
    line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return line


def apply_pill_segment_positions(widgets: list[QWidget]) -> None:
    """Assign segment position; inner edges (at divider) use small join radius via QSS."""
    n = len(widgets)
    for i, w in enumerate(widgets):
        if n == 1:
            pos = "solo"
        elif i == 0:
            pos = "left"
        elif i == n - 1:
            pos = "right"
        else:
            pos = "center"
        w.setProperty("position", pos)
        st = w.style()
        if st is not None:
            st.unpolish(w)
            st.polish(w)


def add_widgets_with_icon_separators(
    layout: QHBoxLayout,
    widgets: list[QWidget],
    parent: QWidget,
    *,
    sep_height: int = 24,
) -> None:
    """Append widgets to a horizontal layout with a vertical rule between each."""
    apply_pill_segment_positions(widgets)
    align = Qt.AlignmentFlag.AlignVCenter
    for i, w in enumerate(widgets):
        if i > 0:
            layout.addWidget(vertical_icon_separator(parent, height=sep_height), 0, align)
        layout.addWidget(w, 0, align)
