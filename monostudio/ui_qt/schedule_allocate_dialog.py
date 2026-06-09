"""Dialog to create or edit a schedule allocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from monostudio.core.user_identity import (
    build_schedule_assignee_fields,
    get_current_user,
    match_roster_user_by_name,
)
from monostudio.ui_qt.calendar_date_picker import MonosDateEdit
from monostudio.core.project_schedule import (
    ScheduleAllocation,
    delete_allocation,
    new_allocation_id,
    upsert_allocation_for_row,
)
from monostudio.ui_qt.assignee_picker_widget import AssigneePickerWidget
from monostudio.ui_qt.style import MonosDialog


@dataclass(frozen=True)
class _EntityOption:
    kind: str
    rel: str
    name: str
    departments: tuple[str, ...]


class ScheduleAllocateDialog(MonosDialog):
    """Create or edit one allocation (entity + department + date range)."""

    def __init__(
        self,
        *,
        parent=None,
        project_root: Path,
        workspace_root: Path | None = None,
        entities: list[_EntityOption],
        dept_labels: dict[str, str],
        existing: ScheduleAllocation | None = None,
        preset_kind: str | None = None,
        preset_rel: str | None = None,
        preset_department: str | None = None,
        preset_start: str | None = None,
        preset_due: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._existing = existing
        self._entities = entities
        self._dept_labels = dept_labels
        self._saved: ScheduleAllocation | None = None
        self._deleted = False

        self.setWindowTitle("Edit allocation" if existing else "Allocate deadline")
        self.setModal(True)
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self._entity_combo = QComboBox(self)
        for i, ent in enumerate(entities):
            prefix = "Shot" if ent.kind == "shot" else "Asset"
            self._entity_combo.addItem(f"{prefix} · {ent.name}", i)
        form.addRow("Entity", self._entity_combo)

        self._dept_combo = QComboBox(self)
        form.addRow("Department", self._dept_combo)

        self._start = MonosDateEdit(self)
        self._due = MonosDateEdit(self)
        today = QDate.currentDate()
        self._start.setDate(today)
        self._due.setDate(today.addDays(7))
        form.addRow("Start", self._start)
        form.addRow("Due", self._due)

        self._assignee = AssigneePickerWidget(self._workspace_root, self)
        form.addRow("Assignees", self._assignee)

        self._note = QLineEdit(self)
        self._note.setPlaceholderText("Optional")
        form.addRow("Note", self._note)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        self._delete_btn = QPushButton("Delete", self)
        self._delete_btn.setObjectName("DialogDestructiveButton")
        self._delete_btn.setVisible(existing is not None)
        buttons.addButton(self._delete_btn, QDialogButtonBox.DestructiveRole)
        ok = buttons.button(QDialogButtonBox.Save)
        if ok:
            ok.setText("Save")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        self._delete_btn.clicked.connect(self._on_delete)
        root.addWidget(buttons)

        self._entity_combo.currentIndexChanged.connect(self._rebuild_departments)
        self._rebuild_departments()

        if existing is not None:
            self._load_existing(existing)
        elif preset_kind and preset_rel:
            self._select_entity(preset_kind, preset_rel, preset_department)
            if preset_start:
                ds = QDate.fromString(preset_start[:10], "yyyy-MM-dd")
                if ds.isValid():
                    self._start.setDate(ds)
            if preset_due:
                dd = QDate.fromString(preset_due[:10], "yyyy-MM-dd")
                if dd.isValid():
                    self._due.setDate(dd)
            current = get_current_user(self._workspace_root)
            if current is not None:
                self._assignee.set_user_ids([current.id])
        elif get_current_user(self._workspace_root) is not None:
            cur = get_current_user(self._workspace_root)
            if cur is not None:
                self._assignee.set_user_ids([cur.id])

    def result_allocation(self) -> ScheduleAllocation | None:
        return self._saved

    def was_deleted(self) -> bool:
        return self._deleted

    def _current_entity(self) -> _EntityOption | None:
        idx = self._entity_combo.currentData()
        if idx is None:
            return None
        try:
            i = int(idx)
        except (TypeError, ValueError):
            return None
        if i < 0 or i >= len(self._entities):
            return None
        return self._entities[i]

    def _rebuild_departments(self) -> None:
        ent = self._current_entity()
        prev = self._dept_combo.currentData()
        self._dept_combo.clear()
        if ent is None:
            return
        for dep in ent.departments:
            label = self._dept_labels.get(dep, dep)
            self._dept_combo.addItem(label, dep)
        if prev is not None:
            ix = self._dept_combo.findData(prev)
            if ix >= 0:
                self._dept_combo.setCurrentIndex(ix)

    def _select_entity(self, kind: str, rel: str, department: str | None) -> None:
        rel_n = rel.replace("\\", "/")
        for i, ent in enumerate(self._entities):
            if ent.kind == kind and ent.rel.replace("\\", "/") == rel_n:
                self._entity_combo.setCurrentIndex(i)
                break
        self._rebuild_departments()
        if department:
            ix = self._dept_combo.findData(department)
            if ix >= 0:
                self._dept_combo.setCurrentIndex(ix)

    def _load_existing(self, alloc: ScheduleAllocation) -> None:
        self._select_entity(alloc.entity_kind, alloc.entity_rel, alloc.department)
        ds = QDate.fromString(alloc.start[:10], "yyyy-MM-dd")
        dd = QDate.fromString(alloc.due[:10], "yyyy-MM-dd")
        if ds.isValid():
            self._start.setDate(ds)
        if dd.isValid():
            self._due.setDate(dd)
        if alloc.assignee_ids:
            self._assignee.set_user_ids(list(alloc.assignee_ids))
        elif (alloc.assignee_id or "").strip():
            self._assignee.set_user_ids([alloc.assignee_id])
        elif (alloc.assignee or "").strip():
            matched = match_roster_user_by_name(self._workspace_root, alloc.assignee)
            self._assignee.set_user_ids([matched.id] if matched else [])
        else:
            self._assignee.set_user_ids([])
        self._note.setText(alloc.note or "")

    def _on_save(self) -> None:
        ent = self._current_entity()
        if ent is None:
            return
        dep = self._dept_combo.currentData()
        if dep is not None:
            dep = str(dep).strip() or None
        start_d = self._start.date().toPython()
        due_d = self._due.date().toPython()
        if not isinstance(start_d, date) or not isinstance(due_d, date):
            return
        if due_d < start_d:
            QMessageBox.warning(self, "Schedule", "Due date must be on or after start date.")
            return
        aid = self._existing.id if self._existing else new_allocation_id()
        ids, names, legacy_id, legacy_label = build_schedule_assignee_fields(
            self._workspace_root,
            self._assignee.selected_user_ids(),
        )
        alloc = ScheduleAllocation(
            id=aid,
            entity_kind=ent.kind,
            entity_rel=ent.rel,
            department=str(dep) if dep else None,
            start=start_d.isoformat(),
            due=due_d.isoformat(),
            assignee_ids=ids,
            assignees=names,
            assignee_id=legacy_id,
            assignee=legacy_label,
            note=self._note.text().strip(),
        )
        try:
            upsert_allocation_for_row(self._project_root, alloc)
        except OSError as ex:
            QMessageBox.critical(self, "Schedule", f"Failed to save allocation:\n{ex}")
            return
        from monostudio.core.schedule_assign_notify import (
            collect_previous_assignee_ids,
            notify_new_schedule_assignments,
        )

        ent = self._current_entity()
        display = ent.name if ent is not None else ""
        prev_assignee_ids = collect_previous_assignee_ids(self._existing)
        try:
            notify_new_schedule_assignments(
                self._project_root,
                self._workspace_root,
                previous=self._existing,
                allocation=alloc,
                entity_display=display,
                previous_assignee_ids=prev_assignee_ids,
            )
        except OSError:
            pass
        self._saved = alloc
        self.accept()

    def _on_delete(self) -> None:
        if self._existing is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete allocation",
                "Remove this deadline allocation?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            delete_allocation(self._project_root, self._existing.id)
        except OSError as ex:
            QMessageBox.critical(self, "Schedule", f"Failed to delete:\n{ex}")
            return
        self._deleted = True
        self.accept()


def default_dates_for_new_row() -> tuple[date, date]:
    today = date.today()
    return today, today + timedelta(days=7)
