"""Pick a department target status when creating a schedule goal bar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from monostudio.core.department_status_registry import (
    default_target_status_for_department,
    load_status_registry_for_department,
)
from monostudio.ui_qt.style import MonosDialog


class ScheduleGoalStatusDialog(MonosDialog):
    """Choose which workflow status this schedule goal targets."""

    def __init__(
        self,
        *,
        parent=None,
        project_root: Path,
        department_id: str,
        department_label: str = "",
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._department_id = (department_id or "").strip()
        self._chosen: str = ""

        title_dep = (department_label or self._department_id or "Department").strip()
        self.setWindowTitle(f"Goal status · {title_dep}")
        self.setModal(True)
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(
            "This bar tracks progress toward the selected status by the due date.",
            self,
        )
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        self._status_combo = QComboBox(self)
        reg = load_status_registry_for_department(self._project_root, self._department_id)
        default_id = default_target_status_for_department(self._project_root, self._department_id)
        pick_ix = 0
        for cat, sids in reg.statuses_grouped_for_menu():
            for sid in sids:
                self._status_combo.addItem(reg.label_for(sid), sid)
                if sid == default_id:
                    pick_ix = self._status_combo.count() - 1
        if self._status_combo.count():
            self._status_combo.setCurrentIndex(pick_ix)
        form.addRow("Target status", self._status_combo)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok = buttons.button(QDialogButtonBox.Ok)
        if ok:
            ok.setText("Add goal")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        data = self._status_combo.currentData()
        sid = str(data).strip() if data is not None else ""
        if not sid:
            return
        self._chosen = sid
        self.accept()

    def chosen_status_id(self) -> str:
        return self._chosen
