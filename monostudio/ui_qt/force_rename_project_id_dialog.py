from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRegularExpression
from PySide6.QtGui import QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.project_risk import ExternalReferencesStatus, ProjectRenameImpact, RiskLevel, assess_force_rename_project_id
from monostudio.core.project_rename import force_rename_project_id
from monostudio.ui_qt.style import MONOS_COLORS


_SAFE_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _read_project_display_name(project_root: Path) -> str:
    """
    Best-effort read of project display name.
    Falls back to folder name.
    """
    manifest = project_root / ".monostudio" / "project.json"
    try:
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    except Exception:
        pass
    return project_root.name


def _sanitize_project_id(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _suggest_new_project_id(*, display_name: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    base = _sanitize_project_id(display_name) or "project"
    return f"{stamp}_{base}"


def _risk_to_qss_key(level: RiskLevel) -> str:
    if level == RiskLevel.SAFE:
        return "safe"
    if level == RiskLevel.MEDIUM:
        return "medium"
    if level == RiskLevel.HIGH:
        return "high"
    return "critical"


class _ImpactRow(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)
        self._k = QLabel(label, self)
        self._k.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._v = QLabel("—", self)
        self._v.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        l.addWidget(self._k, 1)
        l.addWidget(self._v, 0, Qt.AlignRight)

    def set_value(self, text: str) -> None:
        self._v.setText(text or "—")


class ForceRenameProjectIdDialog(QDialog):
    """
    UI-only dialog for the dangerous operation: Force Rename Project ID.
    (The actual rename execution is handled elsewhere.)
    """

    def __init__(self, *, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("⚠️ Force Rename Project ID")
        self.setModal(True)

        self._project_root = project_root
        self._current_id = project_root.name
        self._impact: ProjectRenameImpact | None = None
        self._renamed_to: Path | None = None

        display_name = _read_project_display_name(project_root)
        suggested = _suggest_new_project_id(display_name=display_name)

        # --- Fields
        self._current_id_field = QLineEdit(self._current_id, self)
        self._current_id_field.setReadOnly(True)
        self._current_id_field.setProperty("mono", True)

        self._new_id_field = QLineEdit(suggested, self)
        self._new_id_field.setPlaceholderText("new_project_id")
        self._new_id_field.setClearButtonEnabled(True)
        self._new_id_field.setValidator(QRegularExpressionValidator(QRegularExpression(r"[a-z0-9_]+"), self._new_id_field))

        # --- Impact Analysis
        self._impact_assets = _ImpactRow("Assets", self)
        self._impact_shots = _ImpactRow("Shots", self)
        self._impact_versions = _ImpactRow("Publish versions", self)
        self._impact_external = _ImpactRow("External references", self)
        self._impact_render_cache = _ImpactRow("Render cache", self)

        self._risk_badge = QLabel("—", self)
        self._risk_badge.setObjectName("RiskBadge")
        f = QFont("Inter", 10)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.PercentageSpacing, 95)  # tracking-tighter
        self._risk_badge.setFont(f)
        self._risk_badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # --- Confirmations
        self._confirm_check = QCheckBox("I understand this may break references and cached data", self)
        self._confirm_text = QLineEdit("", self)
        self._confirm_text.setPlaceholderText("Type CURRENT Project ID to confirm")
        self._confirm_text.setProperty("mono", True)

        # Buttons
        self._buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=self)
        self._btn_force = QPushButton("Force Rename", self)
        self._btn_force.setEnabled(False)
        self._btn_force.clicked.connect(self._on_force_rename_clicked)
        self._buttons.addButton(self._btn_force, QDialogButtonBox.AcceptRole)
        self._buttons.rejected.connect(self.reject)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("Current Project ID", self._current_id_field)
        form.addRow("New Project ID", self._new_id_field)

        impact_box = QGroupBox("Impact Analysis", self)
        impact_l = QVBoxLayout(impact_box)
        impact_l.setContentsMargins(12, 12, 12, 12)
        impact_l.setSpacing(8)
        impact_l.addWidget(self._impact_assets)
        impact_l.addWidget(self._impact_shots)
        impact_l.addWidget(self._impact_versions)
        impact_l.addWidget(self._impact_external)
        impact_l.addWidget(self._impact_render_cache)

        risk_row = QWidget(self)
        risk_l = QHBoxLayout(risk_row)
        risk_l.setContentsMargins(0, 0, 0, 0)
        risk_l.setSpacing(8)
        risk_label = QLabel("Risk Level", self)
        risk_label.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        risk_l.addWidget(risk_label, 1)
        risk_l.addWidget(self._risk_badge, 0, Qt.AlignRight)

        confirm_box = QGroupBox("Confirmation", self)
        confirm_l = QVBoxLayout(confirm_box)
        confirm_l.setContentsMargins(12, 12, 12, 12)
        confirm_l.setSpacing(10)
        confirm_l.addWidget(self._confirm_check)
        confirm_l.addWidget(self._confirm_text)

        layout.addLayout(form)
        layout.addWidget(impact_box)
        layout.addWidget(risk_row)
        layout.addWidget(confirm_box)
        layout.addWidget(self._buttons)

        # Wiring
        self._confirm_check.toggled.connect(lambda _v: self._sync_force_enabled())
        self._confirm_text.textChanged.connect(lambda _t: self._sync_force_enabled())
        self._new_id_field.textChanged.connect(lambda _t: self._sync_force_enabled())

        # Compute impact after dialog paints once (keeps UI responsive).
        QTimer.singleShot(0, self._compute_impact)
        self._sync_force_enabled()

    def new_project_id(self) -> str:
        return (self._new_id_field.text() or "").strip()

    def impact(self) -> ProjectRenameImpact | None:
        return self._impact

    def renamed_to(self) -> Path | None:
        return self._renamed_to

    def _compute_impact(self) -> None:
        try:
            impact = assess_force_rename_project_id(self._project_root)
        except Exception:
            impact = None

        self._impact = impact
        if impact is None:
            self._impact_assets.set_value("—")
            self._impact_shots.set_value("—")
            self._impact_versions.set_value("—")
            self._impact_external.set_value(ExternalReferencesStatus.UNKNOWN.value)
            self._impact_render_cache.set_value("Unknown")
            self._set_risk(RiskLevel.CRITICAL)
            self._sync_force_enabled()
            return

        self._impact_assets.set_value(str(impact.asset_count))
        self._impact_shots.set_value(str(impact.shot_count))
        self._impact_versions.set_value(str(impact.total_publish_versions))
        self._impact_external.set_value(impact.external_references.value)
        if impact.has_render_cache is None:
            self._impact_render_cache.set_value("Unknown")
        else:
            self._impact_render_cache.set_value("Yes" if impact.has_render_cache else "No")
        self._set_risk(impact.risk_level)
        self._sync_force_enabled()

    def _set_risk(self, level: RiskLevel) -> None:
        self._risk_badge.setText(level.value)
        self._risk_badge.setProperty("risk", _risk_to_qss_key(level))
        self._risk_badge.style().unpolish(self._risk_badge)
        self._risk_badge.style().polish(self._risk_badge)
        self._risk_badge.update()

    def _sync_force_enabled(self) -> None:
        # Risk must be computed before enabling.
        if self._impact is None:
            self._btn_force.setEnabled(False)
            return

        new_id = self.new_project_id()
        has_ack = self._confirm_check.isChecked()
        typed_ok = (self._confirm_text.text() or "") == self._current_id

        valid_new = bool(new_id) and bool(_SAFE_ID_RE.match(new_id)) and new_id != self._current_id
        self._btn_force.setEnabled(bool(has_ack and typed_ok and valid_new))

    def _on_force_rename_clicked(self) -> None:
        if self._impact is None:
            return
        new_id = self.new_project_id()
        try:
            result = force_rename_project_id(project_root=self._project_root, new_project_id=new_id, impact=self._impact)
        except Exception as e:
            QMessageBox.critical(self, "Force Rename Project ID", f"Failed to rename project folder.\n\n{e}")
            return

        self._renamed_to = result.new_root
        self.accept()

