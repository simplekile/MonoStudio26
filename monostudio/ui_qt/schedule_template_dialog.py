"""Edit pipeline template step durations for auto-planning."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.project_schedule import (
    DEFAULT_TEMPLATE_NAME,
    ScheduleTemplateStep,
    ensure_default_template,
    read_project_schedule,
    set_template,
)
from monostudio.ui_qt.style import MonosDialog


class ScheduleTemplateDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        project_root: Path,
        dept_labels: dict[str, str],
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._dept_labels = dept_labels
        self._spinboxes: dict[str, QSpinBox] = {}

        schedule = ensure_default_template(self._project_root)
        self._schedule = schedule

        self.setWindowTitle("Pipeline template")
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(
            "Days per department used when auto-planning backward from a delivery date "
            "or computing wave bar length.",
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        pick = QFormLayout()
        self._template = QComboBox(self)
        for name in sorted(schedule.templates.keys()) or [DEFAULT_TEMPLATE_NAME]:
            self._template.addItem(name, name)
        self._template.currentIndexChanged.connect(self._reload_steps)
        pick.addRow("Template", self._template)
        root.addLayout(pick)

        self._steps_host = QWidget(self)
        self._steps_layout = QFormLayout(self._steps_host)
        self._steps_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._steps_host)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        save = buttons.button(QDialogButtonBox.Save)
        if save:
            save.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._reload_steps()

    def _current_template_name(self) -> str:
        return str(self._template.currentData() or DEFAULT_TEMPLATE_NAME)

    def _reload_steps(self) -> None:
        while self._steps_layout.count():
            item = self._steps_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._spinboxes.clear()

        name = self._current_template_name()
        steps = self._schedule.templates.get(name) or []
        if not steps:
            empty = QLabel("No steps in this template.", self._steps_host)
            empty.setObjectName("DialogHint")
            self._steps_layout.addRow(empty)
            return

        for step in steps:
            spin = QSpinBox(self._steps_host)
            spin.setRange(1, 365)
            spin.setValue(max(1, int(step.days)))
            label = self._dept_labels.get(step.dept, step.dept)
            self._steps_layout.addRow(label, spin)
            self._spinboxes[step.dept] = spin

    def _on_save(self) -> None:
        name = self._current_template_name()
        old = self._schedule.templates.get(name) or []
        if not old:
            QMessageBox.warning(self, "Pipeline template", "Nothing to save.")
            return
        steps: list[ScheduleTemplateStep] = []
        for step in old:
            spin = self._spinboxes.get(step.dept)
            days = int(spin.value()) if spin else step.days
            steps.append(
                ScheduleTemplateStep(dept=step.dept, days=days, after=step.after)
            )
        try:
            set_template(self._project_root, name, steps)
            self._schedule = read_project_schedule(self._project_root)
        except OSError as ex:
            QMessageBox.critical(self, "Pipeline template", f"Failed to save:\n{ex}")
            return
        self.accept()
