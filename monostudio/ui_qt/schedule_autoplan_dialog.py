"""Auto-plan delivery targets for many shots at once."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from monostudio.ui_qt.calendar_date_picker import MonosDateEdit
from monostudio.core.fs_reader import ProjectIndex
from monostudio.core.project_schedule import (
    DEFAULT_TEMPLATE_NAME,
    ScheduleTarget,
    bulk_set_targets,
    ensure_default_template,
    entity_has_schedule,
    entity_rel_path,
    read_project_schedule,
)
from monostudio.ui_qt.schedule_allocate_dialog import _EntityOption
from monostudio.ui_qt.style import MonosDialog


def entity_options_from_index(
    project_index: ProjectIndex,
    *,
    include_shots: bool = True,
    include_assets: bool = False,
) -> list[_EntityOption]:
    """Build entity picker options for schedule dialogs."""
    root = project_index.root
    out: list[_EntityOption] = []
    if include_shots:
        for shot in project_index.shots:
            rel = entity_rel_path(root, shot.path).replace("\\", "/")
            depts = tuple(
                (d.name or "").strip() for d in shot.departments if (d.name or "").strip()
            )
            out.append(
                _EntityOption(kind="shot", rel=rel, name=shot.name, departments=depts)
            )
    if include_assets:
        for asset in project_index.assets:
            rel = entity_rel_path(root, asset.path).replace("\\", "/")
            depts = tuple(
                (d.name or "").strip() for d in asset.departments if (d.name or "").strip()
            )
            out.append(
                _EntityOption(kind="asset", rel=rel, name=asset.name, departments=depts)
            )
    out.sort(key=lambda e: (0 if e.kind == "shot" else 1, e.name.casefold()))
    return out


class ScheduleAutoPlanDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        project_root: Path,
        entities: list[_EntityOption],
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._entities = entities
        self._saved_count = 0

        schedule = ensure_default_template(self._project_root)
        self._schedule = schedule

        self.setWindowTitle("Auto-plan deliveries")
        self.setModal(True)
        self.setMinimumWidth(400)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(
            "Set a delivery date and pipeline template. The planner will backward-schedule "
            "all departments for each entity (existing pinned bars stay).",
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        form = QFormLayout()
        self._delivery = MonosDateEdit(self)
        self._delivery.setDate(QDate.currentDate().addDays(28))
        form.addRow("Delivery", self._delivery)

        self._template = QComboBox(self)
        for name in sorted(schedule.templates.keys()) or [DEFAULT_TEMPLATE_NAME]:
            self._template.addItem(name, name)
        form.addRow("Template", self._template)
        root.addLayout(form)

        self._only_unscheduled = QCheckBox("Only entities without a plan yet", self)
        self._only_unscheduled.setChecked(True)
        root.addWidget(self._only_unscheduled)

        self._include_shots = QCheckBox("Include shots", self)
        self._include_shots.setChecked(any(e.kind == "shot" for e in entities))
        root.addWidget(self._include_shots)

        self._include_assets = QCheckBox("Include assets", self)
        self._include_assets.setChecked(any(e.kind == "asset" for e in entities))
        root.addWidget(self._include_assets)

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

    def _on_apply(self) -> None:
        delivery = self._delivery.date().toPython()
        if not isinstance(delivery, date):
            return
        template = str(self._template.currentData() or DEFAULT_TEMPLATE_NAME)
        schedule = read_project_schedule(self._project_root)
        only_new = self._only_unscheduled.isChecked()
        include_shots = self._include_shots.isChecked()
        include_assets = self._include_assets.isChecked()

        targets: list[ScheduleTarget] = []
        skipped = 0
        for ent in self._entities:
            if ent.kind == "shot" and not include_shots:
                continue
            if ent.kind == "asset" and not include_assets:
                continue
            if only_new and entity_has_schedule(
                schedule, entity_kind=ent.kind, entity_rel=ent.rel
            ):
                skipped += 1
                continue
            targets.append(
                ScheduleTarget(
                    entity_kind=ent.kind,
                    entity_rel=ent.rel,
                    delivery=delivery.isoformat(),
                    template=template,
                )
            )

        if not targets:
            QMessageBox.warning(
                self,
                "Auto-plan deliveries",
                "No entities matched. Turn off “only unscheduled” or add shots to the project.",
            )
            return

        try:
            bulk_set_targets(self._project_root, targets)
        except OSError as ex:
            QMessageBox.critical(self, "Auto-plan deliveries", f"Failed to save:\n{ex}")
            return

        self._saved_count = len(targets)
        if skipped:
            QMessageBox.information(
                self,
                "Auto-plan deliveries",
                f"Planned {len(targets)} entities.\nSkipped {skipped} that already had a plan.",
            )
        self.accept()
