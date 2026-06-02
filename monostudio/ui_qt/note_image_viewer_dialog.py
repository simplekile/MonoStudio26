"""Full-size image viewer for inline note images."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout

from monostudio.ui_qt.style import MonosDialog, monos_font


class NoteImageViewerDialog(MonosDialog):
    def __init__(self, image_path: Path, *, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image")
        self.setModal(True)
        self.setMinimumSize(480, 360)
        self.resize(720, 540)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        host = QLabel(scroll)
        host.setAlignment(Qt.AlignmentFlag.AlignCenter)
        host.setObjectName("DialogHint")
        px = QPixmap(str(image_path))
        if px.isNull():
            host.setText("Could not load image.")
        else:
            host.setPixmap(px)
            host.setScaledContents(False)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogSecondaryButton")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
