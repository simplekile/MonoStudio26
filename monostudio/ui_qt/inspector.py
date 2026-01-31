from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QFormLayout, QLineEdit, QMenu, QStackedLayout, QVBoxLayout, QWidget


@dataclass(frozen=True)
class AssetShotInspectorData:
    # Spec 5.1
    name: str
    type: str
    absolute_path: str
    created_date: str = "—"
    last_modified: str = "—"


@dataclass(frozen=True)
class DepartmentInspectorData:
    # Spec 5.2 (excluding version fields, shown in Status section for Phase 2a)
    department_name: str
    work_path: str
    publish_path: str


@dataclass(frozen=True)
class DepartmentStatusData:
    work_exists: str  # "Yes" / "No"
    publish_exists: str  # "Yes" / "No"
    latest_version: str  # folder name or "—"
    version_count: str  # integer string


class InspectorPanel(QWidget):
    """
    Spec: Inspector = info only (read-only).
    Always visible (layout stable). Selection only swaps content.
    """

    _EMPTY_MESSAGE = "Select an asset or shot to view details"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # Empty state (centered, muted). No icons/buttons/links.
        self._empty_state = QLabel(self._EMPTY_MESSAGE)
        self._empty_state.setAlignment(Qt.AlignCenter)
        self._empty_state.setStyleSheet("color: #A9ABB0;")

        # Page: Asset / Shot
        self._asset_name = QLabel("")
        self._asset_type = QLabel("")
        self._asset_absolute_path = QLineEdit()
        self._asset_absolute_path.setReadOnly(True)
        self._asset_absolute_path.setClearButtonEnabled(False)
        self._asset_absolute_path.setProperty("mono", True)
        self._install_copy_full_path_menu(self._asset_absolute_path)
        self._asset_created_date = QLabel("")
        self._asset_last_modified = QLabel("")

        asset_form = QFormLayout()
        asset_form.setContentsMargins(0, 0, 0, 0)
        asset_form.setHorizontalSpacing(12)
        asset_form.setVerticalSpacing(10)
        asset_form.addRow("Name", self._asset_name)
        asset_form.addRow("Type", self._asset_type)
        asset_form.addRow("Absolute Path", self._asset_absolute_path)
        asset_form.addRow("Created Date", self._asset_created_date)
        asset_form.addRow("Last Modified", self._asset_last_modified)

        asset_page = QWidget()
        asset_layout = QVBoxLayout(asset_page)
        asset_layout.setContentsMargins(12, 12, 12, 12)
        asset_layout.setSpacing(12)
        asset_layout.addLayout(asset_form)
        asset_layout.addStretch(1)

        # Page: Department / Version
        self._dept_name = QLabel("")
        self._dept_work_path = QLineEdit()
        self._dept_work_path.setReadOnly(True)
        self._dept_work_path.setClearButtonEnabled(False)
        self._dept_work_path.setProperty("mono", True)
        self._install_copy_full_path_menu(self._dept_work_path)

        self._dept_publish_path = QLineEdit()
        self._dept_publish_path.setReadOnly(True)
        self._dept_publish_path.setClearButtonEnabled(False)
        self._dept_publish_path.setProperty("mono", True)
        self._install_copy_full_path_menu(self._dept_publish_path)

        dept_form = QFormLayout()
        dept_form.setContentsMargins(0, 0, 0, 0)
        dept_form.setHorizontalSpacing(12)
        dept_form.setVerticalSpacing(10)
        dept_form.addRow("Department Name", self._dept_name)
        dept_form.addRow("Work Path", self._dept_work_path)
        dept_form.addRow("Publish Path", self._dept_publish_path)

        status_title = QLabel("Status")
        status_title.setStyleSheet("color: #A9ABB0; font-weight: 600;")

        self._status_work_exists = QLabel("")
        self._status_publish_exists = QLabel("")
        self._status_latest_version = QLabel("")
        self._status_version_count = QLabel("")

        status_form = QFormLayout()
        status_form.setContentsMargins(0, 0, 0, 0)
        status_form.setHorizontalSpacing(12)
        status_form.setVerticalSpacing(10)
        status_form.addRow("Work Folder Exists", self._status_work_exists)
        status_form.addRow("Publish Folder Exists", self._status_publish_exists)
        status_form.addRow("Latest Version", self._status_latest_version)
        status_form.addRow("Version Count", self._status_version_count)

        dept_page = QWidget()
        dept_layout = QVBoxLayout(dept_page)
        dept_layout.setContentsMargins(12, 12, 12, 12)
        dept_layout.setSpacing(12)
        dept_layout.addLayout(dept_form)
        dept_layout.addWidget(status_title)
        dept_layout.addLayout(status_form)
        dept_layout.addStretch(1)

        root = QStackedLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._empty_state)  # page 0
        root.addWidget(asset_page)  # page 1
        root.addWidget(dept_page)  # page 2
        self._stack = root

        self.set_empty_state()

    def _install_copy_full_path_menu(self, field: QLineEdit) -> None:
        # Candidate 1: Inspector right-click on path field -> Copy Full Path
        field.setContextMenuPolicy(Qt.CustomContextMenu)
        field.customContextMenuRequested.connect(lambda pos, f=field: self._show_path_menu(f, pos))

    def _show_path_menu(self, field: QLineEdit, pos) -> None:
        menu = QMenu(self)
        act_copy_full = menu.addAction("Copy Full Path")
        menu.addSeparator()

        # Keep standard actions (Copy) available; read-only prevents edits anyway.
        std = field.createStandardContextMenu()
        menu.addActions(std.actions())

        chosen = menu.exec(field.mapToGlobal(pos))
        if chosen == act_copy_full:
            text = field.text()
            if not text:
                return
            cb = QApplication.clipboard()
            if cb is None:
                return
            cb.setText(text)

    def clear(self) -> None:
        # Clear displayed values (does not change empty-state visibility).
        self._asset_name.setText("")
        self._asset_type.setText("")
        self._asset_absolute_path.setText("")
        self._asset_absolute_path.setToolTip("")
        self._asset_created_date.setText("")
        self._asset_last_modified.setText("")

        self._dept_name.setText("")
        self._dept_work_path.setText("")
        self._dept_work_path.setToolTip("")
        self._dept_publish_path.setText("")
        self._dept_publish_path.setToolTip("")

        self._status_work_exists.setText("")
        self._status_publish_exists.setText("")
        self._status_latest_version.setText("")
        self._status_version_count.setText("")

    def set_asset_shot(self, data: AssetShotInspectorData) -> None:
        self._stack.setCurrentIndex(1)
        self._asset_name.setText(data.name)
        self._asset_type.setText(data.type)
        self._asset_absolute_path.setText(data.absolute_path)
        self._asset_absolute_path.setToolTip(data.absolute_path)
        self._asset_created_date.setText(data.created_date)
        self._asset_last_modified.setText(data.last_modified)

    def set_department(self, data: DepartmentInspectorData, status: DepartmentStatusData) -> None:
        self._stack.setCurrentIndex(2)
        self._dept_name.setText(data.department_name)
        self._dept_work_path.setText(data.work_path)
        self._dept_work_path.setToolTip(data.work_path)
        self._dept_publish_path.setText(data.publish_path)
        self._dept_publish_path.setToolTip(data.publish_path)

        self._status_work_exists.setText(status.work_exists)
        self._status_publish_exists.setText(status.publish_exists)
        self._status_latest_version.setText(status.latest_version)
        self._status_version_count.setText(status.version_count)

    # Backward compatibility for existing call sites
    def set_data(self, data: AssetShotInspectorData) -> None:
        self.set_asset_shot(data)

    def set_empty_state(self, _message: str | None = None) -> None:
        """
        Switch to the neutral empty state.

        Message is fixed by UX requirement; parameter is accepted only for backward compatibility.
        """
        self._empty_state.setText(self._EMPTY_MESSAGE)
        self._stack.setCurrentIndex(0)
        self.clear()

