from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
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

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setText("Delete")
            ok.setEnabled(False)
        cancel = self._buttons.button(QDialogButtonBox.Cancel)
        if cancel is not None:
            cancel.setText("Cancel")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(body)
        layout.addSpacing(6)
        layout.addWidget(prompt)
        layout.addWidget(self._input)
        layout.addSpacing(10)
        layout.addWidget(self._buttons)

        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is None:
            return
        typed = self._input.text().strip()  # trim whitespace
        ok.setEnabled(typed == self._expected)

