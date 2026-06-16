"""Confirm dialog before building a heavy full-timeline proxy."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from monostudio.ui_qt.style import MonosDialog


def ask_build_full_proxy(parent, *, detail: str) -> bool:
    dlg = BuildFullProxyConfirmDialog(parent=parent, detail=detail)
    return dlg.exec() == QDialog.DialogCode.Accepted


class BuildFullProxyConfirmDialog(MonosDialog):
    def __init__(self, *, parent=None, detail: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Build full proxy?")
        self.setModal(True)
        self.setObjectName("BuildFullProxyConfirmDialog")
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel(
            "This video may take several minutes to proxy. "
            "Playback will use the original file until the proxy is ready."
        )
        intro.setWordWrap(True)
        intro.setObjectName("DialogBody")
        root.addWidget(intro)

        detail_l = QLabel(detail)
        detail_l.setWordWrap(True)
        detail_l.setObjectName("DialogHelper")
        detail_l.setProperty("mono", True)
        root.addWidget(detail_l)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            Qt.Orientation.Horizontal,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Build proxy")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
