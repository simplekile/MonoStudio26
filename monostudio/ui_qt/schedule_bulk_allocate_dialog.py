"""Bulk deadline allocation — same window or spread across shots."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.project_schedule import ScheduleAllocation, bulk_upsert_allocations, new_allocation_id
from monostudio.ui_qt.calendar_date_picker import MonosDateEdit
from monostudio.ui_qt.schedule_allocate_dialog import _EntityOption
from monostudio.ui_qt.style import MonosDialog


class ScheduleBulkAllocateDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        project_root: Path,
        entities: list[_EntityOption],
        dept_labels: dict[str, str],
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._entities = entities
        self._dept_labels = dept_labels
        self._saved_count = 0

        self.setWindowTitle("Bulk allocate")
        self.setModal(True)
        self.setMinimumSize(480, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel("Select entities, department, and date range. Spread staggers due dates in list order.", self)
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        sel_row = QWidget(self)
        sel_l = QVBoxLayout(sel_row)
        sel_l.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self._btn_all = QPushButton("All", sel_row)
        self._btn_none = QPushButton("None", sel_row)
        for b in (self._btn_all, self._btn_none):
            b.setObjectName("DialogSecondaryButton")
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        top.addWidget(self._btn_all)
        top.addWidget(self._btn_none)
        top.addStretch(1)
        sel_l.addLayout(top)

        self._entity_list = QListWidget(sel_row)
        for ent in entities:
            prefix = "Shot" if ent.kind == "shot" else "Asset"
            item = QListWidgetItem(f"{prefix} · {ent.name}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, ent)
            self._entity_list.addItem(item)
        sel_l.addWidget(self._entity_list, 1)
        root.addWidget(sel_row, 1)

        form = QFormLayout()
        self._dept = QComboBox(self)
        dept_ids: set[str] = set()
        for ent in entities:
            dept_ids.update(ent.departments)
        for dep in sorted(dept_ids):
            self._dept.addItem(dept_labels.get(dep, dep), dep)
        form.addRow("Department", self._dept)

        self._start = MonosDateEdit(self)
        self._due = MonosDateEdit(self)
        today = QDate.currentDate()
        self._start.setDate(today)
        self._due.setDate(today.addDays(7))
        form.addRow("Start", self._start)
        form.addRow("Due", self._due)
        root.addLayout(form)

        mode_box = QGroupBox("Mode", self)
        mode_l = QVBoxLayout(mode_box)
        self._mode_same = QRadioButton("Same dates for all selected", mode_box)
        self._mode_spread = QRadioButton("Spread — shift start/due by step per entity", mode_box)
        self._mode_same.setChecked(True)
        mode_l.addWidget(self._mode_same)
        mode_l.addWidget(self._mode_spread)
        step_row = QFormLayout()
        self._step_days = QSpinBox(mode_box)
        self._step_days.setRange(0, 365)
        self._step_days.setValue(3)
        step_row.addRow("Step (days)", self._step_days)
        mode_l.addLayout(step_row)
        root.addWidget(mode_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok = buttons.button(QDialogButtonBox.Ok)
        if ok:
            ok.setText("Apply")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_apply)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def saved_count(self) -> int:
        return self._saved_count

    def _select_all(self) -> None:
        for i in range(self._entity_list.count()):
            it = self._entity_list.item(i)
            if it:
                it.setCheckState(Qt.CheckState.Checked)

    def _select_none(self) -> None:
        for i in range(self._entity_list.count()):
            it = self._entity_list.item(i)
            if it:
                it.setCheckState(Qt.CheckState.Unchecked)

    def _selected_entities(self) -> list[_EntityOption]:
        out: list[_EntityOption] = []
        for i in range(self._entity_list.count()):
            it = self._entity_list.item(i)
            if it is None or it.checkState() != Qt.CheckState.Checked:
                continue
            ent = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(ent, _EntityOption):
                out.append(ent)
        return out

    def _on_apply(self) -> None:
        selected = self._selected_entities()
        if not selected:
            QMessageBox.warning(self, "Bulk allocate", "Select at least one entity.")
            return
        dep = self._dept.currentData()
        if dep is None:
            QMessageBox.warning(self, "Bulk allocate", "Select a department.")
            return
        dep_s = str(dep).strip()
        start_d = self._start.date().toPython()
        due_d = self._due.date().toPython()
        if not isinstance(start_d, date) or not isinstance(due_d, date):
            return
        if due_d < start_d:
            QMessageBox.warning(self, "Bulk allocate", "Due must be on or after start.")
            return

        duration = (due_d - start_d).days
        step = int(self._step_days.value())
        spread = self._mode_spread.isChecked()

        allocations: list[ScheduleAllocation] = []
        for idx, ent in enumerate(selected):
            if dep_s not in ent.departments:
                continue
            offset = timedelta(days=idx * step) if spread else timedelta(days=0)
            s = start_d + offset
            d = s + timedelta(days=duration)
            allocations.append(
                ScheduleAllocation(
                    id=new_allocation_id(),
                    entity_kind=ent.kind,
                    entity_rel=ent.rel,
                    department=dep_s,
                    start=s.isoformat(),
                    due=d.isoformat(),
                )
            )

        if not allocations:
            QMessageBox.warning(
                self,
                "Bulk allocate",
                "No selected entities have the chosen department folder.",
            )
            return

        try:
            bulk_upsert_allocations(self._project_root, allocations)
        except OSError as ex:
            QMessageBox.critical(self, "Bulk allocate", f"Failed to save:\n{ex}")
            return

        self._saved_count = len(allocations)
        self.accept()
