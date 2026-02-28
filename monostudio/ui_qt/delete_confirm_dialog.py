from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.style import MonosDialog


def ask_delete(parent, title: str, message: str) -> bool:
    """Show MONOS-styled delete confirmation; returns True if user confirmed Delete."""
    dlg = SimpleDeleteConfirmDialog(parent=parent, title=title, message=message)
    return dlg.exec() == QDialog.DialogCode.Accepted


class SimpleDeleteConfirmDialog(MonosDialog):
    """Simple delete confirmation: message + Cancel + Delete (styled). Replaces QMessageBox.question."""

    def __init__(self, *, parent=None, title: str = "Delete", message: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("SimpleDeleteConfirmDialog")

        body = QLabel(message or "Delete this item?")
        body.setWordWrap(True)
        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        btn_row = QWidget()
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(10)
        btn_l.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("DialogDestructiveButton")
        delete_btn.clicked.connect(self.accept)
        btn_l.addWidget(cancel_btn)
        btn_l.addWidget(delete_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(body)
        root.addWidget(btn_row)


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

