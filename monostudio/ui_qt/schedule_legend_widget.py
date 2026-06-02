"""Compact color legend for the Schedule timeline."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from monostudio.ui_qt.style import MONOS_COLORS, monos_font


class _LegendSwatch(QWidget):
    """Small painted sample matching timeline visuals."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(22, 14)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self.width(), self.height()
            kind = self._kind

            if kind == "line_in":
                c = QColor("#10b981")
                p.setPen(QPen(c, 2))
                x = 4
                p.drawLine(x, 0, x, h)
                p.drawLine(x, 2, x + 6, 2)
                p.drawLine(x, 2, x, 8)
            elif kind == "line_out":
                c = QColor("#ef4444")
                p.setPen(QPen(c, 2))
                x = w - 5
                p.drawLine(x, 0, x, h)
                p.drawLine(x, 2, x - 6, 2)
                p.drawLine(x, 2, x, 8)
            elif kind == "line_today":
                c = QColor(MONOS_COLORS.get("blue_400", "#60a5fa"))
                p.setPen(QPen(c, 2))
                p.drawLine(w // 2, 0, w // 2, h)
            elif kind == "line_milestone":
                c = QColor("#a855f7")
                p.setPen(QPen(c, 1, Qt.PenStyle.DashLine))
                p.drawLine(w // 2, 0, w // 2, h)
            elif kind == "fill_production":
                p.fillRect(2, 3, w - 4, h - 6, QColor(16, 185, 129, 40))
            elif kind == "fill_outside":
                p.fillRect(2, 3, w - 4, h - 6, QColor(0, 0, 0, 72))
            elif kind == "bar_done":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#10b981"))
                p.drawRoundedRect(2, 4, w - 4, h - 8, 3, 3)
            elif kind == "bar_progress":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#f59e0b"))
                p.drawRoundedRect(2, 4, w - 4, h - 8, 3, 3)
            elif kind == "bar_waiting":
                p.setPen(Qt.PenStyle.NoPen)
                c = QColor("#71717a")
                c.setAlpha(150)
                p.setBrush(c)
                p.drawRoundedRect(2, 4, w - 4, h - 8, 3, 3)
            elif kind == "bar_overdue":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ef4444"))
                p.drawRoundedRect(2, 4, w - 4, h - 8, 3, 3)
            elif kind == "bar_pinned":
                p.setPen(Qt.PenStyle.NoPen)
                c = QColor("#71717a")
                c.setAlpha(150)
                p.setBrush(c)
                p.drawRoundedRect(2, 4, w - 4, h - 8, 3, 3)
                p.setPen(QPen(QColor("#fafafa"), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(2.5, 4.5, w - 5, h - 9, 3, 3)
        finally:
            p.end()


def _legend_item(parent: QWidget, kind: str, label: str) -> QWidget:
    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(_LegendSwatch(kind, row), 0, Qt.AlignmentFlag.AlignVCenter)
    text = QLabel(label, row)
    text.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
    text.setStyleSheet("color: #a1a1aa;")
    lay.addWidget(text, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class ScheduleLegendBar(QWidget):
    """Horizontal legend for timeline markers and bar status colors."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScheduleLegendBar")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        title = QLabel("LEGEND", self)
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        title.setStyleSheet("color: #71717a; letter-spacing: 0.06em;")
        root.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)

        for kind, label in (
            ("line_in", "Start"),
            ("line_out", "Deadline"),
            ("fill_production", "In production"),
            ("fill_outside", "Outside range"),
            ("line_milestone", "Milestone"),
            ("bar_done", "Done"),
            ("bar_progress", "In progress"),
            ("bar_waiting", "Waiting"),
            ("bar_overdue", "Overdue"),
            ("bar_pinned", "Pinned bar"),
        ):
            root.addWidget(_legend_item(self, kind, label), 0, Qt.AlignmentFlag.AlignVCenter)

        root.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
