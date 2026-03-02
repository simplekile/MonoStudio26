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


def ask_delete_folder(
    parent,
    title: str,
    *,
    folder_to_delete: str,
    other_folders: list[str] | None = None,
    work_subfolders: list[str] | None = None,
    intro_text: str = "",
) -> bool:
    """Show MONOS-styled delete folder confirmation with section headers and mono paths."""
    dlg = DeleteFolderConfirmDialog(
        parent=parent,
        title=title,
        folder_to_delete=folder_to_delete,
        other_folders=other_folders or [],
        work_subfolders=work_subfolders or [],
        intro_text=intro_text,
    )
    return dlg.exec() == QDialog.DialogCode.Accepted


class DeleteFolderConfirmDialog(MonosDialog):
    """Structured delete folder confirmation: section titles + mono path blocks."""

    def __init__(
        self,
        *,
        parent=None,
        title: str = "Delete folder",
        folder_to_delete: str = "",
        other_folders: list[str],
        work_subfolders: list[str],
        intro_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("SimpleDeleteConfirmDialog")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        if intro_text:
            intro = QLabel(intro_text)
            intro.setWordWrap(True)
            intro.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            root.addWidget(intro)
            root.addSpacing(6)

        if other_folders:
            h1 = QLabel("Folder contains other folders besides work:")
            h1.setObjectName("DialogSectionTitle")
            h1.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            root.addWidget(h1)
            paths1 = QLabel("\n".join(other_folders))
            paths1.setProperty("mono", True)
            paths1.setWordWrap(True)
            paths1.setTextInteractionFlags(Qt.TextSelectableByMouse)
            paths1.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            root.addWidget(paths1)
            root.addSpacing(8)

        if work_subfolders:
            h2 = QLabel("Work folder contains subfolders:")
            h2.setObjectName("DialogSectionTitle")
            h2.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            root.addWidget(h2)
            paths2 = QLabel("\n".join(work_subfolders))
            paths2.setProperty("mono", True)
            paths2.setWordWrap(True)
            paths2.setTextInteractionFlags(Qt.TextSelectableByMouse)
            paths2.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            root.addWidget(paths2)
            root.addSpacing(8)

        h3 = QLabel("Folder to be deleted:")
        h3.setObjectName("DialogSectionTitle")
        h3.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        root.addWidget(h3)
        path_main = QLabel(folder_to_delete)
        path_main.setProperty("mono", True)
        path_main.setWordWrap(True)
        path_main.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_main.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        root.addWidget(path_main)
        root.addSpacing(8)

        if other_folders or work_subfolders:
            prompt = QLabel("Delete anyway?")
            prompt.setObjectName("DialogHint")
            root.addWidget(prompt)
            root.addSpacing(4)

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
        root.addWidget(btn_row)


class SimpleDeleteConfirmDialog(MonosDialog):
    """Simple delete confirmation: message + Cancel + Delete (styled). Replaces QMessageBox.question."""

    def __init__(self, *, parent=None, title: str = "Delete", message: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("SimpleDeleteConfirmDialog")
        self.setMinimumWidth(520)

        body = QLabel(message or "Delete this item?")
        body.setWordWrap(True)
        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)

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

