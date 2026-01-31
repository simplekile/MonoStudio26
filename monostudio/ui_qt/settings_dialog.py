from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.pipeline_types_and_presets import (
    PipelineTypesAndPresets,
    TypeDef,
    load_department_vocabulary,
    load_pipeline_types_and_presets,
    save_pipeline_types_and_presets,
)


def _is_valid_type_id(type_id: str) -> bool:
    if not type_id:
        return False
    if type_id.lower() != type_id:
        return False
    if " " in type_id:
        return False
    for ch in type_id:
        if not (ch.islower() or ch.isdigit() or ch == "_"):
            return False
    return True


@dataclass(frozen=True)
class _TypeKey:
    type_id: str


class SettingsDialog(QDialog):
    """
    Settings UI (REQUIRED STRUCTURE):
      Settings
        - App
          - UI (placeholder)
          - Behavior (placeholder)
        - Pipeline
          - Types & Presets (CORE)
        - Project
          - Overview (placeholder)
          - Integrations (placeholder)
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)

        self._vocab = load_department_vocabulary()
        self._vocab_set = set(self._vocab)
        self._config: PipelineTypesAndPresets = load_pipeline_types_and_presets()

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_app_tab(), "App")
        self._tabs.addTab(self._build_pipeline_tab(), "Pipeline")
        self._tabs.addTab(self._build_project_tab(), "Project")

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setText("Save")
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(self._tabs)
        layout.addWidget(self._buttons)
        # Pipeline editor pages self-initialize.

    def open_pipeline_types_and_presets(self) -> None:
        self._tabs.setCurrentIndex(1)
        self._pipeline_tabs.setCurrentIndex(0)

    def _build_app_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._placeholder("App → UI (placeholder)"), "UI")
        tabs.addTab(self._placeholder("App → Behavior (placeholder)"), "Behavior")
        layout.addWidget(tabs)
        return root

    def _build_pipeline_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._pipeline_tabs = QTabWidget()
        self._pipeline_tabs.addTab(self._build_pipeline_types_and_presets_page(), "Types & Presets")
        layout.addWidget(self._pipeline_tabs)
        return root

    def _build_project_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._placeholder("Project → Overview (placeholder)"), "Overview")
        tabs.addTab(self._placeholder("Project → Integrations (placeholder)"), "Integrations")
        layout.addWidget(tabs)
        return root

    @staticmethod
    def _placeholder(text: str) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 12, 12, 12)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("color: #A9ABB0;")
        l.addWidget(lab)
        l.addStretch(1)
        return w

    def _build_pipeline_types_and_presets_page(self) -> QWidget:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # Split into two tabs: Asset / Shot
        self._pipeline_kind_tabs = QTabWidget()
        self._pipeline_kind_tabs.addTab(self._build_types_and_presets_kind(kind="asset"), "Asset")
        self._pipeline_kind_tabs.addTab(self._build_types_and_presets_kind(kind="shot"), "Shot")
        outer.addWidget(self._pipeline_kind_tabs)
        return root

    def _build_types_and_presets_kind(self, *, kind: str) -> QWidget:
        """
        kind:
          - "asset": type_id must NOT be shot/shot_*
          - "shot":  type_id must be shot or shot_*
        """
        def is_shot_type_id(type_id: str) -> bool:
            return type_id == "shot" or type_id.startswith("shot_")

        def allow_type_id(type_id: str) -> bool:
            if not _is_valid_type_id(type_id):
                return False
            if kind == "shot":
                return is_shot_type_id(type_id)
            return not is_shot_type_id(type_id)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Left: Types list
        types_box = QGroupBox("Types")
        types_l = QVBoxLayout(types_box)
        types_l.setContentsMargins(12, 12, 12, 12)
        types_l.setSpacing(10)

        types_list = QListWidget()
        types_list.setSelectionMode(QListWidget.SingleSelection)
        types_l.addWidget(types_list, 1)

        btn_row = QWidget()
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(10)
        btn_type_create = QPushButton("Create Type")
        btn_type_delete = QPushButton("Delete Type")
        btn_type_delete.setEnabled(False)
        btn_l.addWidget(btn_type_create)
        btn_l.addWidget(btn_type_delete)
        btn_l.addStretch(1)
        types_l.addWidget(btn_row)

        # Right: Details + presets + departments
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(12)

        details = QGroupBox("Selected Type")
        details_l = QVBoxLayout(details)
        details_l.setContentsMargins(12, 12, 12, 12)
        details_l.setSpacing(10)

        type_id_field = QLineEdit()
        type_id_field.setReadOnly(True)
        type_name_field = QLineEdit()
        type_short_field = QLineEdit()
        type_short_field.setValidator(QRegularExpressionValidator(QRegularExpression(r"[a-z0-9_]+"), type_short_field))

        details_l.addWidget(self._field("Type ID (immutable)", type_id_field))
        details_l.addWidget(self._field("Name", type_name_field))
        details_l.addWidget(self._field("Short Name", type_short_field))

        presets = QGroupBox("Department Presets")
        presets_l = QVBoxLayout(presets)
        presets_l.setContentsMargins(12, 12, 12, 12)
        presets_l.setSpacing(10)

        # NEW: Type itself is the preset. No "Department Presets" list.
        presets_header = QLabel("Departments (Type = Preset)")
        presets_header.setWordWrap(True)
        presets_header.setStyleSheet("color: #A9ABB0; font-size: 11px;")
        presets_l.addWidget(presets_header)

        dept_label = QLabel("Departments (vocabulary)")
        dept_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        presets_l.addWidget(dept_label)

        dept_container = QWidget()
        dept_layout = QVBoxLayout(dept_container)
        dept_layout.setContentsMargins(0, 0, 0, 0)
        dept_layout.setSpacing(6)

        dept_checkboxes: dict[str, QCheckBox] = {}
        for d in self._vocab:
            cb = QCheckBox(d)
            cb.setEnabled(False)
            dept_layout.addWidget(cb)
            dept_checkboxes[d] = cb
        dept_layout.addStretch(1)

        dept_scroll = QScrollArea()
        dept_scroll.setWidgetResizable(True)
        dept_scroll.setFrameShape(QScrollArea.NoFrame)
        dept_scroll.setWidget(dept_container)
        dept_scroll.setEnabled(False)
        presets_l.addWidget(dept_scroll, 2)

        right_l.addWidget(details, 0)
        right_l.addWidget(presets, 1)

        layout.addWidget(types_box, 1)
        layout.addWidget(right, 2)

        def current_type_id() -> str | None:
            it = types_list.currentItem()
            return it.data(Qt.UserRole) if it else None

        def current_preset() -> str | None:
            return None

        def refresh_types(select: str | None) -> None:
            types_list.blockSignals(True)
            types_list.clear()
            items = [(tid, t) for tid, t in self._config.types.items() if (is_shot_type_id(tid) if kind == "shot" else not is_shot_type_id(tid))]
            for tid, t in sorted(items, key=lambda kv: kv[1].name.lower()):
                item = QListWidgetItem(f"{t.name} ({tid})")
                item.setData(Qt.UserRole, tid)
                types_list.addItem(item)
                if select == tid:
                    types_list.setCurrentItem(item)
            types_list.blockSignals(False)
            on_type_selected()

        def refresh_presets() -> None:
            on_preset_selected()

        def on_type_selected() -> None:
            tid = current_type_id()
            t = self._config.types.get(tid or "")
            has = t is not None
            btn_type_delete.setEnabled(has)

            type_id_field.blockSignals(True)
            type_name_field.blockSignals(True)
            type_short_field.blockSignals(True)
            type_id_field.setText(t.type_id if t else "")
            type_name_field.setText(t.name if t else "")
            type_short_field.setText(t.short_name if t else "")
            type_id_field.blockSignals(False)
            type_name_field.blockSignals(False)
            type_short_field.blockSignals(False)

            refresh_presets()

        def on_type_fields_changed() -> None:
            tid = current_type_id()
            if not tid:
                return
            t = self._config.types.get(tid)
            if t is None:
                return
            name = type_name_field.text().strip()
            short = type_short_field.text().strip()
            if not name or not short:
                return
            self._config.types[tid] = TypeDef(type_id=tid, name=name, short_name=short, departments=t.departments)
            refresh_types(select=tid)

        def on_create_type() -> None:
            from PySide6.QtWidgets import QInputDialog

            type_id, ok = QInputDialog.getText(self, "Create Type", "Type ID (lowercase, immutable):")
            if not ok:
                return
            type_id = (type_id or "").strip()
            if not allow_type_id(type_id):
                return
            if type_id in self._config.types:
                return
            name, ok = QInputDialog.getText(self, "Create Type", "Name (display):")
            if not ok:
                return
            name = (name or "").strip()
            if not name:
                return
            short, ok = QInputDialog.getText(self, "Create Type", "Short Name (prefix):")
            if not ok:
                return
            short = (short or "").strip()
            if not short:
                return
            self._config.types[type_id] = TypeDef(type_id=type_id, name=name, short_name=short, departments=[])
            refresh_types(select=type_id)

        def on_delete_type() -> None:
            tid = current_type_id()
            if not tid or tid not in self._config.types:
                return
            res = QMessageBox.question(self, "Delete Type", f"Delete type '{tid}'?")
            if res != QMessageBox.Yes:
                return
            self._config.types.pop(tid, None)
            refresh_types(select=None)

        def on_preset_selected() -> None:
            tid = current_type_id()
            t = self._config.types.get(tid or "")
            has = bool(t)
            dept_scroll.setEnabled(has)

            for d, cb in dept_checkboxes.items():
                cb.blockSignals(True)
                cb.setEnabled(has)
                cb.setChecked(False)
                cb.blockSignals(False)
            if not has or not t:
                return
            selected = set(t.departments)
            for d, cb in dept_checkboxes.items():
                cb.blockSignals(True)
                cb.setChecked(d in selected)
                cb.blockSignals(False)

        def on_dept_toggled(_checked: bool) -> None:
            tid = current_type_id()
            t = self._config.types.get(tid or "")
            if t is None:
                return
            selected = [d for d, cb in dept_checkboxes.items() if cb.isChecked() and d in self._vocab_set]
            self._config.types[tid] = TypeDef(type_id=tid, name=t.name, short_name=t.short_name, departments=selected)

        # wiring
        types_list.currentItemChanged.connect(lambda _c, _p: on_type_selected())
        type_name_field.textChanged.connect(lambda _t: on_type_fields_changed())
        type_short_field.textChanged.connect(lambda _t: on_type_fields_changed())
        btn_type_create.clicked.connect(on_create_type)
        btn_type_delete.clicked.connect(on_delete_type)
        for cb in dept_checkboxes.values():
            cb.toggled.connect(on_dept_toggled)

        refresh_types(select=None)
        return root

    @staticmethod
    def _field(label: str, widget: QWidget) -> QWidget:
        block = QWidget()
        l = QVBoxLayout(block)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)
        lab = QLabel(label)
        lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        l.addWidget(lab)
        l.addWidget(widget)
        return block

    def _on_save(self) -> None:
        if not save_pipeline_types_and_presets(self._config):
            QMessageBox.critical(self, "Settings", "Failed to save Pipeline Types & Presets.")
            return
        self.accept()

