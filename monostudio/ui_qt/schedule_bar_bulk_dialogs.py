"""Quick bulk-edit dialogs for selected schedule goal bars."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from monostudio.core.user_identity import build_schedule_assignee_fields
from monostudio.ui_qt.assignee_picker_widget import AssigneePickerWidget
from monostudio.ui_qt.calendar_date_picker import MonosDateEdit
from monostudio.ui_qt.style import MonosDialog


class ScheduleBulkDatesDialog(MonosDialog):
    """Set the same start/due on every selected goal bar."""

    def __init__(self, *, parent=None, count: int, preset_start: date | None = None, preset_due: date | None = None) -> None:
        super().__init__(parent)
        self._start_d: date | None = None
        self._due_d: date | None = None

        self.setWindowTitle("Set dates")
        self.setModal(True)
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(f"Apply start and due to {count} selected goal bar(s).", self)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        self._start = MonosDateEdit(self)
        self._due = MonosDateEdit(self)
        today = QDate.currentDate()
        self._start.setDate(QDate.fromString(preset_start.isoformat(), "yyyy-MM-dd") if preset_start else today)
        self._due.setDate(QDate.fromString(preset_due.isoformat(), "yyyy-MM-dd") if preset_due else today.addDays(7))
        form.addRow("Start", self._start)
        form.addRow("Due", self._due)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok = buttons.button(QDialogButtonBox.Ok)
        if ok:
            ok.setText("Apply")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        start_d = self._start.date().toPython()
        due_d = self._due.date().toPython()
        if not isinstance(start_d, date) or not isinstance(due_d, date):
            return
        if due_d < start_d:
            QMessageBox.warning(self, "Set dates", "Due must be on or after start.")
            return
        self._start_d = start_d
        self._due_d = due_d
        self.accept()

    def dates(self) -> tuple[date, date] | None:
        if self._start_d is None or self._due_d is None:
            return None
        return self._start_d, self._due_d


class ScheduleBulkNoteDialog(MonosDialog):
    def __init__(self, *, parent=None, count: int, preset: str = "") -> None:
        super().__init__(parent)
        self._note = ""

        self.setWindowTitle("Set note")
        self.setModal(True)
        self.setMinimumWidth(400)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(f"Replace note on {count} selected goal bar(s).", self)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._field = QLineEdit(self)
        self._field.setPlaceholderText("Optional")
        self._field.setText(preset)
        root.addWidget(self._field)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok = buttons.button(QDialogButtonBox.Ok)
        if ok:
            ok.setText("Apply")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        self._note = self._field.text().strip()
        self.accept()

    def note(self) -> str:
        return self._note


class ScheduleBulkAssignDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        workspace_root: Path | None,
        count: int,
        preset_user_ids: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._ids: tuple[str, ...] = ()
        self._names: tuple[str, ...] = ()
        self._legacy_id = ""
        self._legacy_label = ""

        self.setWindowTitle("Set assignees")
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(
            f"Replace assignees for {count} department row goal(s). "
            "Assignees apply to the whole department row, not individual status goals.",
            self,
        )
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._assignee = AssigneePickerWidget(self._workspace_root, self)
        if preset_user_ids:
            self._assignee.set_user_ids(preset_user_ids)
        root.addWidget(self._assignee)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok = buttons.button(QDialogButtonBox.Ok)
        if ok:
            ok.setText("Apply")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        ids, names, legacy_id, legacy_label = build_schedule_assignee_fields(
            self._workspace_root,
            self._assignee.selected_user_ids(),
        )
        self._ids = ids
        self._names = names
        self._legacy_id = legacy_id
        self._legacy_label = legacy_label
        self.accept()

    def assignee_fields(self) -> tuple[tuple[str, ...], tuple[str, ...], str, str]:
        return self._ids, self._names, self._legacy_id, self._legacy_label
