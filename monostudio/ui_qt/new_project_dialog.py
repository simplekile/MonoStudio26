from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont, QShowEvent
from PySide6.QtWidgets import QDateEdit, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from monostudio.core.project_id import generate_project_id
from monostudio.ui_qt.style import MonosDialog


class NewProjectDialog(MonosDialog):
    """
    New Project (MONOS v1):
    - Project Name (required, display name)
    - Start Date (required)
    - Project ID is auto-generated (read-only) and immutable after creation
    """

    def __init__(self, workspace_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setModal(True)

        self._workspace_root = workspace_root

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Forest Spirit")
        self._name.textChanged.connect(self._update_ok_enabled)
        self._name.textChanged.connect(self._sync_preview)

        self._start_date = QDateEdit(self)
        self._start_date.setCalendarPopup(True)
        self._start_date.setDate(QDate.currentDate())
        self._start_date.dateChanged.connect(lambda _d: self._update_ok_enabled())

        self._project_id_preview = QLineEdit("")
        self._project_id_preview.setReadOnly(True)
        self._project_id_preview.setProperty("mono", True)
        f = QFont(self._project_id_preview.font())
        f.setLetterSpacing(QFont.PercentageSpacing, 97)
        self._project_id_preview.setFont(f)

        self._workspace_label = QLabel(str(self._workspace_root))
        self._workspace_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._workspace_label.setObjectName("DialogLabelMeta")

        workspace_hint = QLabel("Workspace Root (fixed)")
        workspace_hint.setObjectName("DialogHint")

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("Project Name", self._name)
        form.addRow("Start Date", self._start_date)
        form.addRow("Project ID (auto)", self._project_id_preview)
        form.addRow(workspace_hint, self._workspace_label)

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        self._ok_btn = QPushButton("Create")
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        button_row_l.addWidget(self._ok_btn)
        button_row_l.addWidget(cancel_btn)
        button_row_l.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(button_row)

        self._sync_preview()
        self._update_ok_enabled()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._name.setFocus()

    def project_name(self) -> str:
        return self._name.text().strip()

    def start_date_iso(self) -> str:
        # YYYY-MM-DD
        d = self._start_date.date()
        return f"{d.year():04d}-{d.month():02d}-{d.day():02d}"

    def project_id(self) -> str:
        # Created date is today (not start date) per spec.
        return generate_project_id(self.project_name())

    def _update_ok_enabled(self) -> None:
        self._ok_btn.setEnabled(bool(self.project_name()) and bool(self.start_date_iso()))

    def _sync_preview(self) -> None:
        name = self.project_name()
        self._project_id_preview.setText(generate_project_id(name) if name else "")

