"""Shared transport controls for video and sequence preview dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QToolButton, QWidget

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS


def make_transport_tool_button(parent: QWidget, icon: str, tip: str) -> QToolButton:
    btn = QToolButton(parent)
    btn.setIcon(lucide_icon(icon, size=18, color_hex=MONOS_COLORS["text_label"]))
    btn.setIconSize(QSize(18, 18))
    btn.setToolTip(tip)
    btn.setAutoRaise(True)
    btn.setFixedSize(32, 32)
    return btn


class BasicMediaTransportRow(QWidget):
    """Minimal play + close row used by sequence preview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MediaPreviewTransportRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.btn_play = make_transport_tool_button(self, "play", "Play / pause (Space)")
        row.addWidget(self.btn_play)
        row.addStretch(1)
        self.btn_close = QPushButton("Close", self)
        self.btn_close.setObjectName("DialogSecondaryButton")
        self.btn_close.setIcon(lucide_icon("x", size=16, color_hex=MONOS_COLORS["text_label"]))
        self.btn_close.setIconSize(QSize(16, 16))
        row.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignRight)
