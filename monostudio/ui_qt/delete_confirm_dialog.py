from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.style import MonosDialog


class DeleteConfirmDialog(MonosDialog):
    """
    Guarded delete confirmation (explicit, boring):
    - User must type exact folder name to enable Delete
    - No warnings/toasts on success/failure (handled by caller)
    """

    def __init__(self, *, kind_label: str, folder_name: str, absolute_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(f"Delete {kind_label}")

        self._expected = folder_name

        body = QLabel(
            "This will permanently delete the following folder from disk:\n\n"
            f"{str(absolute_path)}\n\n"
            "This action cannot be undone."
        )
        body.setWordWrap(True)
        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)

        prompt = QLabel("Type the name to confirm:")
        prompt.setObjectName("DialogHint")
        prompt.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._input = QLineEdit()
        self._input.setPlaceholderText(self._expected)
        self._input.textChanged.connect(self._update_ok_enabled)

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        self._ok_btn = QPushButton("Delete")
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        button_row_l.addWidget(self._ok_btn)
        button_row_l.addWidget(cancel_btn)
        button_row_l.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(body)
        layout.addSpacing(6)
        layout.addWidget(prompt)
        layout.addWidget(self._input)
        layout.addSpacing(10)
        layout.addWidget(button_row)

        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        typed = self._input.text().strip()  # trim whitespace
        self._ok_btn.setEnabled(typed == self._expected)

