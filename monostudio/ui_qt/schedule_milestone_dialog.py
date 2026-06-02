"""Add / edit project schedule milestones."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QDateEdit,
    QVBoxLayout,
)

from monostudio.core.project_schedule import (
    ScheduleMilestone,
    delete_milestone,
    new_milestone_id,
    read_project_manifest_start_date,
    read_project_schedule,
    set_project_range,
    upsert_milestone,
)
from monostudio.ui_qt.style import MonosDialog


class ScheduleMilestoneDialog(MonosDialog):
    def __init__(self, *, parent=None, project_root: Path) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self.setWindowTitle("Milestones & range")
        self.setModal(True)
        self.setMinimumSize(420, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        range_hint = QLabel(
            "Production range — IN/OUT markers (green / amber) like a frame range on the timeline.",
            self,
        )
        range_hint.setWordWrap(True)
        range_hint.setObjectName("DialogHint")
        root.addWidget(range_hint)

        range_form = QFormLayout()
        self._chk_in = QCheckBox("Set start (IN)", self)
        self._date_in = QDateEdit(self)
        self._date_in.setCalendarPopup(True)
        self._date_in.setDisplayFormat("yyyy-MM-dd")
        self._date_in.setDate(QDate.currentDate())
        self._date_in.setEnabled(False)
        self._chk_in.toggled.connect(self._date_in.setEnabled)
        in_row = QHBoxLayout()
        in_row.addWidget(self._chk_in)
        in_row.addWidget(self._date_in, 1)
        range_form.addRow("Start", in_row)

        self._chk_out = QCheckBox("Set end (OUT)", self)
        self._date_out = QDateEdit(self)
        self._date_out.setCalendarPopup(True)
        self._date_out.setDisplayFormat("yyyy-MM-dd")
        self._date_out.setDate(QDate.currentDate())
        self._date_out.setEnabled(False)
        self._chk_out.toggled.connect(self._date_out.setEnabled)
        out_row = QHBoxLayout()
        out_row.addWidget(self._chk_out)
        out_row.addWidget(self._date_out, 1)
        range_form.addRow("End", out_row)
        root.addLayout(range_form)

        self._btn_save_range = QPushButton("Save range", self)
        self._btn_save_range.setObjectName("DialogSecondaryButton")
        self._btn_save_range.clicked.connect(self._on_save_range)
        root.addWidget(self._btn_save_range)

        hint = QLabel("Milestones — vertical dashed lines between IN and OUT.", self)
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        self._list = QListWidget(self)
        self._list.currentItemChanged.connect(self._on_pick)
        root.addWidget(self._list, 1)

        form = QFormLayout()
        self._label = QLineEdit(self)
        self._label.setPlaceholderText("e.g. Anim lock")
        self._date = QDateEdit(self)
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._date.setDate(QDate.currentDate())
        form.addRow("Label", self._label)
        form.addRow("Date", self._date)
        root.addLayout(form)

        row = QHBoxLayout()
        self._btn_new = QPushButton("New", self)
        self._btn_new.setObjectName("DialogSecondaryButton")
        self._btn_new.clicked.connect(self._on_new)
        self._btn_delete = QPushButton("Delete", self)
        self._btn_delete.setObjectName("DialogDestructiveButton")
        self._btn_delete.clicked.connect(self._on_delete)
        row.addWidget(self._btn_new)
        row.addWidget(self._btn_delete)
        row.addStretch(1)
        root.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close, self)
        save = buttons.button(QDialogButtonBox.Save)
        if save:
            save.setText("Save milestone")
            save.setObjectName("DialogPrimaryButton")
        close = buttons.button(QDialogButtonBox.Close)
        if close:
            close.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self._on_close)
        root.addWidget(buttons)

        self._editing_id: str | None = None
        self._load_range()
        self._reload_list()

    def _load_range(self) -> None:
        schedule = read_project_schedule(self._project_root)
        start_iso = schedule.project_start or read_project_manifest_start_date(self._project_root)
        if start_iso:
            d = QDate.fromString(start_iso[:10], "yyyy-MM-dd")
            if d.isValid():
                self._chk_in.setChecked(True)
                self._date_in.setDate(d)
        if schedule.project_end:
            d = QDate.fromString(schedule.project_end[:10], "yyyy-MM-dd")
            if d.isValid():
                self._chk_out.setChecked(True)
                self._date_out.setDate(d)

    def _on_save_range(self) -> None:
        ps: str | None = None
        pe: str | None = None
        if self._chk_in.isChecked():
            d = self._date_in.date().toPython()
            if isinstance(d, date):
                ps = d.isoformat()
        if self._chk_out.isChecked():
            d = self._date_out.date().toPython()
            if isinstance(d, date):
                pe = d.isoformat()
        if ps and pe and ps > pe:
            QMessageBox.warning(self, "Production range", "Start (IN) must be on or before end (OUT).")
            return
        try:
            set_project_range(self._project_root, project_start=ps, project_end=pe)
        except OSError as ex:
            QMessageBox.critical(self, "Production range", f"Failed to save:\n{ex}")
            return

    def _reload_list(self) -> None:
        self._list.clear()
        schedule = read_project_schedule(self._project_root)
        for m in schedule.milestones:
            item = QListWidgetItem(f"{m.date}  ·  {m.label}")
            item.setData(Qt.ItemDataRole.UserRole, m.id)
            self._list.addItem(item)

    def _on_pick(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            self._editing_id = None
            self._label.clear()
            return
        mid = current.data(Qt.ItemDataRole.UserRole)
        self._editing_id = str(mid) if mid else None
        schedule = read_project_schedule(self._project_root)
        for m in schedule.milestones:
            if m.id == self._editing_id:
                self._label.setText(m.label)
                d = QDate.fromString(m.date[:10], "yyyy-MM-dd")
                if d.isValid():
                    self._date.setDate(d)
                break

    def _on_new(self) -> None:
        self._list.clearSelection()
        self._editing_id = None
        self._label.clear()
        self._date.setDate(QDate.currentDate())

    def _on_save(self) -> None:
        label = self._label.text().strip()
        if not label:
            QMessageBox.warning(self, "Milestones", "Label is required.")
            return
        d = self._date.date().toPython()
        if not isinstance(d, date):
            return
        mid = self._editing_id or new_milestone_id()
        ms = ScheduleMilestone(id=mid, label=label, date=d.isoformat())
        try:
            upsert_milestone(self._project_root, ms)
        except OSError as ex:
            QMessageBox.critical(self, "Milestones", f"Failed to save:\n{ex}")
            return
        self._editing_id = mid
        self._reload_list()
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == mid:
                self._list.setCurrentItem(it)
                break

    def _on_close(self) -> None:
        ps: str | None = None
        pe: str | None = None
        if self._chk_in.isChecked():
            d = self._date_in.date().toPython()
            if isinstance(d, date):
                ps = d.isoformat()
        if self._chk_out.isChecked():
            d = self._date_out.date().toPython()
            if isinstance(d, date):
                pe = d.isoformat()
        if ps and pe and ps > pe:
            QMessageBox.warning(self, "Production range", "Start (IN) must be on or before end (OUT).")
            return
        try:
            set_project_range(self._project_root, project_start=ps, project_end=pe)
        except OSError as ex:
            QMessageBox.critical(self, "Production range", f"Failed to save:\n{ex}")
            return
        self.accept()

    def _on_delete(self) -> None:
        if not self._editing_id:
            return
        if (
            QMessageBox.question(
                self,
                "Delete milestone",
                "Remove this milestone?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            delete_milestone(self._project_root, self._editing_id)
        except OSError as ex:
            QMessageBox.critical(self, "Milestones", f"Failed to delete:\n{ex}")
            return
        self._on_new()
        self._reload_list()
