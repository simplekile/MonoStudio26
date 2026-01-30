from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NewProjectDialog(QDialog):
    """
    Minimal dialog:
    - Project Name (required)
    - Location (default workspace root, changeable via folder picker)
    """

    def __init__(self, workspace_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setModal(True)

        self._location = workspace_root

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Project_ForestSpirit")
        self._name.textChanged.connect(self._update_ok_enabled)

        self._location_label = QLabel(str(self._location))
        self._location_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._browse = QPushButton("Browse…")
        self._browse.clicked.connect(self._browse_location)

        location_row = QWidget()
        location_layout = QHBoxLayout(location_row)
        location_layout.setContentsMargins(0, 0, 0, 0)
        location_layout.setSpacing(10)
        location_layout.addWidget(self._location_label, 1)
        location_layout.addWidget(self._browse, 0)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("Project Name", self._name)
        form.addRow("Location", location_row)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(self._buttons)

        self._update_ok_enabled()

    def project_name(self) -> str:
        return self._name.text().strip()

    def location_dir(self) -> Path:
        return self._location

    def _browse_location(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose Location",
            str(self._location),
        )
        if not chosen:
            return
        self._location = Path(chosen)
        self._location_label.setText(str(self._location))

    def _update_ok_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is None:
            return
        ok.setEnabled(bool(self.project_name()))

