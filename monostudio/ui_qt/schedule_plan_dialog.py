"""Plan schedule — delivery targets or department waves."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.project_schedule import (
    DEFAULT_TEMPLATE_NAME,
    ScheduleTarget,
    ScheduleWave,
    bulk_set_targets,
    bulk_set_waves,
    ensure_default_template,
)
from monostudio.ui_qt.schedule_allocate_dialog import _EntityOption
from monostudio.ui_qt.style import MonosDialog


class SchedulePlanDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        project_root: Path,
        entities: list[_EntityOption],
        dept_labels: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._entities = entities
        self._dept_labels = dept_labels or {}
        self._saved_count = 0

        schedule = ensure_default_template(self._project_root)
        self._schedule = schedule

        self.setWindowTitle("Plan schedule")
        self.setModal(True)
        self.setMinimumSize(500, 540)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(
            "Select entities below, then choose Delivery (full pipeline from one date) "
            "or Department wave (one dept pass across many shots).",
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        top = QHBoxLayout()
        self._btn_all = QPushButton("All", self)
        self._btn_none = QPushButton("None", self)
        for b in (self._btn_all, self._btn_none):
            b.setObjectName("DialogSecondaryButton")
        self._btn_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        self._btn_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        top.addWidget(self._btn_all)
        top.addWidget(self._btn_none)
        top.addStretch(1)
        root.addLayout(top)

        self._entity_list = QListWidget(self)
        existing_targets = {
            (t.entity_kind, t.entity_rel.replace("\\", "/")): t for t in schedule.targets
        }
        existing_waves: dict[tuple[str, str, str], ScheduleWave] = {}
        for w in schedule.waves:
            existing_waves[(w.entity_kind, w.entity_rel.replace("\\", "/"), w.department)] = w

        for ent in entities:
            prefix = "Shot" if ent.kind == "shot" else "Asset"
            label = f"{prefix} · {ent.name}"
            t = existing_targets.get((ent.kind, ent.rel.replace("\\", "/")))
            if t is not None:
                label += f"   delivery {t.delivery}"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, ent)
            self._entity_list.addItem(item)
        root.addWidget(self._entity_list, 1)

        self._tabs = QTabWidget(self)
        root.addWidget(self._tabs)

        # --- Delivery tab ---
        delivery_tab = QWidget(self)
        delivery_l = QVBoxLayout(delivery_tab)
        delivery_l.setContentsMargins(0, 8, 0, 0)
        delivery_hint = QLabel(
            "Set one delivery date per entity. All departments are scheduled backward "
            "using the pipeline template.",
            delivery_tab,
        )
        delivery_hint.setWordWrap(True)
        delivery_hint.setObjectName("DialogHint")
        delivery_l.addWidget(delivery_hint)

        delivery_form = QFormLayout()
        self._delivery = QDateEdit(delivery_tab)
        self._delivery.setCalendarPopup(True)
        self._delivery.setDisplayFormat("yyyy-MM-dd")
        self._delivery.setDate(QDate.currentDate().addDays(21))
        delivery_form.addRow("Delivery", self._delivery)

        self._delivery_template = QComboBox(delivery_tab)
        self._fill_template_combo(self._delivery_template)
        delivery_form.addRow("Template", self._delivery_template)
        delivery_l.addLayout(delivery_form)
        delivery_l.addStretch(1)
        self._tabs.addTab(delivery_tab, "Delivery")

        # --- Wave tab ---
        wave_tab = QWidget(self)
        wave_l = QVBoxLayout(wave_tab)
        wave_l.setContentsMargins(0, 8, 0, 0)
        wave_hint = QLabel(
            "Set one department due date across selected shots (wave / pass planning). "
            "Start date comes from the template duration for that department.",
            wave_tab,
        )
        wave_hint.setWordWrap(True)
        wave_hint.setObjectName("DialogHint")
        wave_l.addWidget(wave_hint)

        wave_form = QFormLayout()
        self._wave_dept = QComboBox(wave_tab)
        dept_ids: set[str] = set()
        for ent in entities:
            dept_ids.update(ent.departments)
        if not dept_ids and self._dept_labels:
            dept_ids.update(self._dept_labels.keys())
        for dep in sorted(dept_ids):
            self._wave_dept.addItem(self._dept_labels.get(dep, dep), dep)
        wave_form.addRow("Department", self._wave_dept)

        self._wave_due = QDateEdit(wave_tab)
        self._wave_due.setCalendarPopup(True)
        self._wave_due.setDisplayFormat("yyyy-MM-dd")
        self._wave_due.setDate(QDate.currentDate().addDays(14))
        wave_form.addRow("Due", self._wave_due)

        self._wave_template = QComboBox(wave_tab)
        self._fill_template_combo(self._wave_template)
        wave_form.addRow("Template", self._wave_template)
        wave_l.addLayout(wave_form)

        mode_box = QGroupBox("Due dates", wave_tab)
        mode_l = QVBoxLayout(mode_box)
        self._wave_same = QRadioButton("Same due for all selected", mode_box)
        self._wave_stagger = QRadioButton("Stagger — shift due by step per entity (list order)", mode_box)
        self._wave_same.setChecked(True)
        mode_l.addWidget(self._wave_same)
        mode_l.addWidget(self._wave_stagger)
        step_row = QFormLayout()
        self._wave_step = QSpinBox(mode_box)
        self._wave_step.setRange(0, 365)
        self._wave_step.setValue(2)
        step_row.addRow("Step (days)", self._wave_step)
        mode_l.addLayout(step_row)
        wave_l.addWidget(mode_box)
        wave_l.addStretch(1)
        self._tabs.addTab(wave_tab, "Department wave")

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

    def _fill_template_combo(self, combo: QComboBox) -> None:
        names = sorted(self._schedule.templates.keys())
        if DEFAULT_TEMPLATE_NAME not in names:
            names.insert(0, DEFAULT_TEMPLATE_NAME)
        for name in names:
            combo.addItem(name, name)

    def saved_count(self) -> int:
        return self._saved_count

    def _set_all(self, state: Qt.CheckState) -> None:
        for i in range(self._entity_list.count()):
            it = self._entity_list.item(i)
            if it:
                it.setCheckState(state)

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
        if self._tabs.currentIndex() == 0:
            self._apply_delivery()
        else:
            self._apply_wave()

    def _apply_delivery(self) -> None:
        selected = self._selected_entities()
        if not selected:
            QMessageBox.warning(self, "Plan schedule", "Select at least one entity.")
            return
        delivery = self._delivery.date().toPython()
        if not isinstance(delivery, date):
            return
        template = str(self._delivery_template.currentData() or DEFAULT_TEMPLATE_NAME)
        targets = [
            ScheduleTarget(
                entity_kind=ent.kind,
                entity_rel=ent.rel,
                delivery=delivery.isoformat(),
                template=template,
            )
            for ent in selected
        ]
        try:
            bulk_set_targets(self._project_root, targets)
        except OSError as ex:
            QMessageBox.critical(self, "Plan schedule", f"Failed to save:\n{ex}")
            return
        self._saved_count = len(targets)
        self.accept()

    def _apply_wave(self) -> None:
        selected = self._selected_entities()
        if not selected:
            QMessageBox.warning(self, "Plan schedule", "Select at least one entity.")
            return
        dep = self._wave_dept.currentData()
        if dep is None:
            QMessageBox.warning(self, "Plan schedule", "Select a department.")
            return
        dep_s = str(dep).strip()
        base_due = self._wave_due.date().toPython()
        if not isinstance(base_due, date):
            return
        template = str(self._wave_template.currentData() or DEFAULT_TEMPLATE_NAME)
        stagger = self._wave_stagger.isChecked()
        step = int(self._wave_step.value())

        waves: list[ScheduleWave] = []
        skipped = 0
        for idx, ent in enumerate(selected):
            if dep_s not in ent.departments:
                skipped += 1
                continue
            due_d = base_due + timedelta(days=idx * step) if stagger else base_due
            waves.append(
                ScheduleWave(
                    entity_kind=ent.kind,
                    entity_rel=ent.rel,
                    department=dep_s,
                    due=due_d.isoformat(),
                    template=template,
                )
            )

        if not waves:
            QMessageBox.warning(
                self,
                "Plan schedule",
                "No selected entities have the chosen department folder.",
            )
            return

        try:
            bulk_set_waves(self._project_root, waves)
        except OSError as ex:
            QMessageBox.critical(self, "Plan schedule", f"Failed to save:\n{ex}")
            return

        self._saved_count = len(waves)
        if skipped:
            QMessageBox.information(
                self,
                "Plan schedule",
                f"Applied wave to {len(waves)} entities.\n"
                f"Skipped {skipped} without department “{self._dept_labels.get(dep_s, dep_s)}”.",
            )
        self.accept()
