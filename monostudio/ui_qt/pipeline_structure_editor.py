"""
Pipeline structure tree editor for Settings: root → structure → types → departments.
Colored nodes per role; detail panel; persist structure.json, types.json, departments.json, types_and_presets.json.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize, QModelIndex, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyleOptionViewItem,
    QAbstractItemView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtGui import QStandardItem, QStandardItemModel

from monostudio.core.department_registry import (
    DepartmentRegistry,
    ensure_parent_from_preset,
    get_default_department_mapping,
    save_project_departments,
)
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.fs_reader import build_project_index
from monostudio.core.pipeline_types_and_presets import (
    DepartmentDef,
    PipelineTypesAndPresets,
    TypeDef,
    get_user_default_config_root,
    load_pipeline_types_and_presets,
    load_pipeline_types_and_presets_for_project,
    save_pipeline_types_and_presets_to_project,
    save_pipeline_types_and_presets_to_user_default,
)
from monostudio.core.project_create import get_resolved_user_default_pipeline_mappings
from monostudio.core.structure_registry import StructureRegistry, save_project_structure
from monostudio.core.type_registry import TypeRegistry, get_default_type_mapping, save_project_types
from monostudio.ui_qt.inbox_split_view import _InboxTreeDelegate
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


ROLE_KIND = Qt.ItemDataRole.UserRole + 40
ROLE_LOGICAL_ID = Qt.ItemDataRole.UserRole + 41
ROLE_EXTRA = Qt.ItemDataRole.UserRole + 42

# (vertical_scroll, frozenset of row identity tuples, current row identity or None)
_PipelineTreeViewState = tuple[int, frozenset[tuple[tuple[int, str, str], ...]], tuple[tuple[int, str, str], ...] | None]


class PipelineNodeKind(IntEnum):
    ROOT = 0
    STRUCTURE = 1
    TYPE_ASSET = 2
    TYPE_SHOT = 3
    DEPTS_SECTION = 4
    DEPARTMENT = 5
    SUBDEPARTMENT = 6


KIND_COLORS_HEX = {
    PipelineNodeKind.ROOT: "#a1a1aa",
    PipelineNodeKind.STRUCTURE: "#60a5fa",
    PipelineNodeKind.TYPE_ASSET: "#34d399",
    PipelineNodeKind.TYPE_SHOT: "#2dd4bf",
    PipelineNodeKind.DEPTS_SECTION: "#a78bfa",
    PipelineNodeKind.DEPARTMENT: "#fbbf24",
    PipelineNodeKind.SUBDEPARTMENT: "#fb923c",
}


class _PipelineStructureTreeDelegate(_InboxTreeDelegate):
    """Full-row selection, Lucide chevron branches (same as Inbox); label color by node kind."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        kind = index.data(ROLE_KIND)
        if kind is not None:
            try:
                k = PipelineNodeKind(int(kind))
                c = KIND_COLORS_HEX.get(k)
                if c:
                    opt.palette.setColor(opt.palette.ColorRole.Text, QColor(c))
            except (TypeError, ValueError):
                pass
        super().paint(painter, opt, index)


def _is_shot_type_id(type_id: str) -> bool:
    tid = (type_id or "").strip()
    return tid == "shot" or tid.startswith("shot_")


def _is_valid_type_id(type_id: str) -> bool:
    if not type_id or type_id.lower() != type_id:
        return False
    if " " in type_id:
        return False
    for ch in type_id:
        if not (ch.islower() or ch.isdigit() or ch == "_"):
            return False
    return True


def _dept_roots(depts_map: dict[str, dict]) -> list[str]:
    roots = [
        d
        for d, n in depts_map.items()
        if not (isinstance(n.get("parent"), str) and (n.get("parent") or "").strip())
    ]
    return sorted(roots, key=lambda d: (depts_map.get(d, {}).get("order", 999), d.lower()))


def _dept_children(depts_map: dict[str, dict], parent_id: str) -> list[str]:
    pid = (parent_id or "").strip()
    kids = [
        d
        for d, n in depts_map.items()
        if (isinstance(n.get("parent"), str) and (n.get("parent") or "").strip() == pid)
    ]
    return sorted(kids, key=lambda d: (depts_map.get(d, {}).get("order", 999), d.lower()))


def _department_leaf_ids(depts_map: dict[str, dict]) -> list[str]:
    parents = {
        (n.get("parent") or "").strip()
        for n in depts_map.values()
        if isinstance(n.get("parent"), str) and (n.get("parent") or "").strip()
    }
    has_child = {d for d in depts_map if d in parents}
    return sorted([d for d in depts_map if d not in has_child], key=str.lower)


def _dept_root_id(depts_map: dict[str, dict], dept_id: str) -> str:
    """Top-level department id (walk parent chain)."""
    seen: set[str] = set()
    cur = (dept_id or "").strip()
    while cur and cur not in seen:
        seen.add(cur)
        raw = depts_map.get(cur, {})
        pr = raw.get("parent")
        p = pr.strip() if isinstance(pr, str) and pr.strip() else ""
        if not p:
            return cur
        cur = p
    return cur or (dept_id or "").strip()


def _leaves_under_dept_root(depts_map: dict[str, dict], root_id: str) -> list[str]:
    """Leaf departments whose ancestor chain reaches root_id (includes root if it is a leaf)."""
    rid = (root_id or "").strip()
    leaves = _department_leaf_ids(depts_map)
    out = [lid for lid in leaves if _dept_root_id(depts_map, lid) == rid]
    return sorted(out, key=lambda d: (depts_map.get(d, {}).get("order", 999), d.lower()))


class PipelineStructureEditorWidget(QWidget):
    """
    Tree + detail editor. Call set_project_root(), reload_from_disk(), save_all_to_project().
    """

    config_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._structure: dict[str, dict[str, str]] = {}
        self._types_map: dict[str, dict] = {}
        self._depts_map: dict[str, dict] = {}
        self._preset_types: dict[str, TypeDef] = {}
        self._preset_depts: dict[str, DepartmentDef] = {}
        self._project_index = None
        try:
            self._pipeline_dcc_reg = get_default_dcc_registry()
        except Exception:
            self._pipeline_dcc_reg = None

        self._model = QStandardItemModel(self)
        self._tree = QTreeView(self)
        self._tree.setObjectName("InboxSplitTree")
        self._tree.setModel(self._model)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(20)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.setItemDelegate(_PipelineStructureTreeDelegate(self._tree))
        self._tree.selectionModel().selectionChanged.connect(self._on_tree_selection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        self._detail_stack = QStackedWidget(self)
        self._page_empty = QWidget(self)
        self._page_empty_l = QVBoxLayout(self._page_empty)
        self._page_empty_l.addWidget(QLabel("Select a node in the tree.", self._page_empty))
        self._detail_stack.addWidget(self._page_empty)

        self._form_structure = self._build_form_structure()
        self._form_type_asset = self._build_form_type_asset()
        self._form_type_shot = self._build_form_type_shot()
        self._form_department = self._build_form_department()
        self._form_type_workflow = self._build_form_type_workflow()

        self._detail_stack.addWidget(self._form_structure["widget"])
        self._detail_stack.addWidget(self._form_type_asset["widget"])
        self._detail_stack.addWidget(self._form_type_shot["widget"])
        self._detail_stack.addWidget(self._form_department["widget"])
        self._detail_stack.addWidget(self._form_type_workflow["widget"])

        self._detail_stack.setCurrentWidget(self._page_empty)

        detail_scroll = QScrollArea(self)
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setWidget(self._detail_stack)
        detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._tree)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        self._btn_save_project = QPushButton("Save", self)
        self._btn_save_project.setObjectName("DialogPrimaryButton")
        self._btn_save_project.clicked.connect(self._on_click_save_project)
        self._btn_save_user_default = QPushButton("Save as default", self)
        self._btn_save_user_default.setObjectName("SettingsCategoryActionButton")
        self._btn_save_user_default.clicked.connect(self._on_click_save_user_default)
        self._btn_reset_user_default = QPushButton("Reset default", self)
        self._btn_reset_user_default.setObjectName("SettingsCategoryActionButton")
        self._btn_reset_user_default.clicked.connect(self._on_click_reset_user_default)
        self._btn_reset_factory = QPushButton("Reset factory", self)
        self._btn_reset_factory.setObjectName("SettingsCategoryActionButton")
        self._btn_reset_factory.clicked.connect(self._on_click_reset_factory)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self._btn_save_project)
        btn_row.addWidget(self._btn_save_user_default)
        btn_row.addWidget(self._btn_reset_user_default)
        btn_row.addWidget(self._btn_reset_factory)
        btn_row.addStretch(1)

        root_l = QVBoxLayout(self)
        root_l.setContentsMargins(0, 0, 0, 0)
        root_l.setSpacing(10)
        root_l.addLayout(btn_row)
        root_l.addWidget(splitter, 1)

        self._hint = QLabel("", self)
        self._hint.setWordWrap(True)
        self._hint.setObjectName("DialogHelper")
        root_l.addWidget(self._hint)

        self._form_department["dcc_global"].toggled.connect(self._on_department_dcc_global_toggled)
        self._form_department["dcc_custom"].toggled.connect(self._on_department_dcc_custom_toggled)

    def set_project_root(self, project_root: Path | None) -> None:
        self._project_root = Path(project_root) if project_root else None
        self._hint.setText(
            ""
            if self._project_root
            else "Select a project in General → Workspace & Project to edit pipeline structure."
        )
        self._set_enabled(bool(self._project_root))
        if self._project_root:
            self.reload_from_disk()

    def _set_enabled(self, on: bool) -> None:
        self._tree.setEnabled(on)
        self._btn_save_user_default.setEnabled(on)
        self._btn_reset_user_default.setEnabled(on)
        self._btn_reset_factory.setEnabled(on)
        self._btn_save_project.setEnabled(on and bool(self._project_root))

    def reload_from_disk(self) -> None:
        if not self._project_root:
            sm = self._tree.selectionModel()
            sm.blockSignals(True)
            try:
                self._model.clear()
            finally:
                sm.blockSignals(False)
            self._on_tree_selection()
            return
        root = self._project_root
        try:
            self._project_index = build_project_index(root)
        except Exception:
            self._project_index = None

        sreg = StructureRegistry.for_project(root)
        self._structure = sreg.get_raw_mapping()

        treg = TypeRegistry.for_project(root)
        self._types_map = treg.get_raw_mapping()

        dreg = DepartmentRegistry.for_project(root)
        self._depts_map = dreg.get_raw_mapping()

        preset = load_pipeline_types_and_presets_for_project(root)
        self._preset_types = dict(preset.types)
        self._preset_depts = dict(preset.departments)

        self._merge_preset_with_disk()
        self._rebuild_model(preserve_tree_view=False)
        self.config_changed.emit()

    def _merge_preset_with_disk(self) -> None:
        for tid, raw in self._types_map.items():
            if _is_shot_type_id(tid):
                continue
            lab = (raw.get("label") or tid).strip()
            if tid not in self._preset_types:
                sn = lab[:4].lower().replace(" ", "_") or tid[:4]
                self._preset_types[tid] = TypeDef(tid, lab, sn, [], None)
            else:
                t = self._preset_types[tid]
                self._preset_types[tid] = TypeDef(tid, t.name or lab, t.short_name, list(t.departments), t.icon_name)

        for tid, t in list(self._preset_types.items()):
            if not _is_shot_type_id(tid):
                continue
            if tid not in self._types_map:
                pass

        for did, raw in self._depts_map.items():
            lab = (raw.get("label") or did).strip()
            pr = raw.get("parent")
            parent = pr.strip() if isinstance(pr, str) and pr.strip() else None
            if did not in self._preset_depts:
                sn = did[:4] if len(did) >= 4 else did
                self._preset_depts[did] = DepartmentDef(did, lab, sn, None, parent)
            else:
                d = self._preset_depts[did]
                self._preset_depts[did] = DepartmentDef(
                    did, d.name or lab, d.short_name, d.icon_name, parent if parent is not None else d.parent
                )

    def build_pipeline_types_and_presets(self) -> PipelineTypesAndPresets:
        self._sync_preset_labels_from_maps()
        return PipelineTypesAndPresets(types=dict(self._preset_types), departments=dict(self._preset_depts))

    def _sync_preset_labels_from_maps(self) -> None:
        for tid, raw in self._types_map.items():
            if _is_shot_type_id(tid):
                continue
            lab = (raw.get("label") or tid).strip()
            t = self._preset_types.get(tid)
            if t:
                self._preset_types[tid] = TypeDef(tid, lab, t.short_name, list(t.departments), t.icon_name)
        for did, raw in self._depts_map.items():
            lab = (raw.get("label") or did).strip()
            d = self._preset_depts.get(did)
            if d:
                pr = raw.get("parent")
                parent = pr.strip() if isinstance(pr, str) and pr.strip() else d.parent
                self._preset_depts[did] = DepartmentDef(did, lab, d.short_name, d.icon_name, parent)

    def save_all_to_project(self, project_root: Path) -> bool:
        self._sync_preset_labels_from_maps()
        cfg = self.build_pipeline_types_and_presets()
        pr = Path(project_root)
        if not save_pipeline_types_and_presets_to_project(pr, cfg):
            return False
        if not save_project_types(pr, self._types_map):
            return False
        dm = ensure_parent_from_preset({k: dict(v) for k, v in self._depts_map.items()})
        self._depts_map = dm
        if not save_project_departments(pr, dm):
            return False
        if not save_project_structure(pr, self._structure):
            return False
        return True

    def _on_click_save_project(self) -> None:
        if not self._project_root:
            return
        if not self.save_all_to_project(self._project_root):
            QMessageBox.critical(self, "Pipeline", "Failed to save pipeline configuration to project.")
            return
        QMessageBox.information(self, "Pipeline", "Pipeline configuration saved to the project.")
        self.config_changed.emit()

    def _on_click_save_user_default(self) -> None:
        cfg = self.build_pipeline_types_and_presets()
        if not save_pipeline_types_and_presets_to_user_default(cfg):
            QMessageBox.critical(
                self,
                "Save as default",
                "Failed to save to Documents/.monostudio/pipeline/",
            )
            return
        pipeline_dir = get_user_default_config_root() / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        saved_parts: list[str] = ["types_and_presets"]
        self.export_user_default_mappings(pipeline_dir)
        saved_parts.extend(["departments", "types", "structure"])
        QMessageBox.information(
            self,
            "Save as default",
            f"Pipeline config saved to:\n{pipeline_dir}\n({', '.join(saved_parts)})",
        )
        self.config_changed.emit()

    def _on_click_reset_user_default(self) -> None:
        ud = get_user_default_config_root() / "pipeline"
        names = ("types_and_presets.json", "types.json", "departments.json", "structure.json")
        if not any((ud / n).is_file() for n in names):
            QMessageBox.information(
                self,
                "Reset default",
                "No files in Documents/.monostudio/pipeline/.\n"
                "Use Save as default first, or use Reset factory.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Reset default",
                "Replace the editor with your saved user default (Documents/.monostudio/pipeline)?\n"
                "Unsaved changes here are lost until you Save to the project.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        depts, types_inner, folders_inner = get_resolved_user_default_pipeline_mappings()
        self._structure = StructureRegistry(folders_inner or {}, None).get_raw_mapping()
        self._types_map = {k: dict(v) if isinstance(v, dict) else v for k, v in types_inner.items()}
        self._depts_map = {k: dict(v) if isinstance(v, dict) else v for k, v in depts.items()}
        preset = load_pipeline_types_and_presets_for_project(None)
        self._preset_types = dict(preset.types)
        self._preset_depts = dict(preset.departments)
        self._merge_preset_with_disk()
        self._rebuild_model(preserve_tree_view=False)
        self.config_changed.emit()

    def _on_click_reset_factory(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Reset factory",
                "Replace the editor with shipped factory defaults (app preset + mono2026)?\n"
                "Unsaved changes here are lost until you Save to the project.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._structure = StructureRegistry({}, None).get_raw_mapping()
        self._types_map = {k: dict(v) for k, v in get_default_type_mapping().items()}
        self._depts_map = ensure_parent_from_preset(dict(get_default_department_mapping()))
        cfg = load_pipeline_types_and_presets()
        self._preset_types = dict(cfg.types)
        self._preset_depts = dict(cfg.departments)
        self._merge_preset_with_disk()
        self._rebuild_model(preserve_tree_view=False)
        self.config_changed.emit()

    def export_user_default_mappings(self, pipeline_dir: Path) -> None:
        """Write departments.json, types.json, structure.json under pipeline_dir (e.g. Documents/.monostudio/pipeline)."""
        from monostudio.core.department_registry import write_departments_to_path
        from monostudio.core.type_registry import write_types_to_path
        from monostudio.core.structure_registry import write_structure_to_path

        pipeline_dir.mkdir(parents=True, exist_ok=True)
        dm = ensure_parent_from_preset({k: dict(v) for k, v in self._depts_map.items()})
        write_departments_to_path(pipeline_dir / "departments.json", dm)
        write_types_to_path(pipeline_dir / "types.json", self._types_map)
        write_structure_to_path(pipeline_dir / "structure.json", self._structure)

    def _pipeline_row_identity(self, idx: QModelIndex) -> tuple[tuple[int, str, str], ...]:
        if not idx.isValid():
            return ()
        parts: list[tuple[int, str, str]] = []
        cur = idx
        while cur.isValid():
            kind = self._model.data(cur, ROLE_KIND)
            lid = self._model.data(cur, ROLE_LOGICAL_ID)
            extra = self._model.data(cur, ROLE_EXTRA)
            k = int(kind) if kind is not None else -1
            lid_s = str(lid) if lid is not None else ""
            if isinstance(extra, str) and extra.strip():
                ex_s = extra.strip()
            else:
                ex_s = ""
            parts.append((k, lid_s, ex_s))
            cur = cur.parent()
        return tuple(reversed(parts))

    def _iter_all_pipeline_model_indices(self):
        root = self._model.index(0, 0)
        if not root.isValid():
            return
        stack = [root]
        while stack:
            idx = stack.pop()
            yield idx
            for r in range(self._model.rowCount(idx) - 1, -1, -1):
                c = self._model.index(r, 0, idx)
                if c.isValid():
                    stack.append(c)

    def _capture_pipeline_tree_state(self) -> _PipelineTreeViewState | None:
        if self._model.rowCount() == 0:
            return None
        root = self._model.index(0, 0)
        if not root.isValid():
            return None
        vs = int(self._tree.verticalScrollBar().value())
        expanded: set[tuple[tuple[int, str, str], ...]] = set()
        for idx in self._iter_all_pipeline_model_indices():
            try:
                if self._tree.isExpanded(idx):
                    expanded.add(self._pipeline_row_identity(idx))
            except Exception:
                pass
        cur = self._tree.currentIndex()
        sel_key: tuple[tuple[int, str, str], ...] | None
        if cur.isValid():
            sk = self._pipeline_row_identity(cur)
            sel_key = sk if sk else None
        else:
            sel_key = None
        return (vs, frozenset(expanded), sel_key)

    def _find_index_for_pipeline_row_identity(self, key: tuple[tuple[int, str, str], ...]) -> QModelIndex:
        if not key:
            return QModelIndex()
        for idx in self._iter_all_pipeline_model_indices():
            if self._pipeline_row_identity(idx) == key:
                return idx
        return QModelIndex()

    def _restore_pipeline_tree_state(self, state: _PipelineTreeViewState | None) -> None:
        if state is None:
            self._tree.expandToDepth(2)
            return
        vs, expanded_keys, sel_key = state
        root = self._model.index(0, 0)
        if root.isValid():
            self._tree.expand(root)
        for path_key in sorted(expanded_keys, key=len):
            idx = self._find_index_for_pipeline_row_identity(path_key)
            if idx.isValid():
                self._tree.setExpanded(idx, True)
        sm = self._tree.selectionModel()
        if sel_key:
            sidx = self._find_index_for_pipeline_row_identity(sel_key)
            if sidx.isValid():
                sm.blockSignals(True)
                try:
                    self._tree.setCurrentIndex(sidx)
                    self._tree.scrollTo(sidx, QAbstractItemView.ScrollHint.PositionAtCenter)
                finally:
                    sm.blockSignals(False)

        vs_clamped = vs

        def _scroll_back() -> None:
            sb = self._tree.verticalScrollBar()
            sb.setValue(min(vs_clamped, sb.maximum()))

        QTimer.singleShot(0, _scroll_back)

    def _rebuild_model(self, *, preserve_tree_view: bool = True) -> None:
        state = self._capture_pipeline_tree_state() if preserve_tree_view else None
        sm = self._tree.selectionModel()
        sm.blockSignals(True)
        try:
            self._model.clear()
            self._model.setHorizontalHeaderLabels(["Pipeline"])
            name = self._project_root.name if self._project_root else "Project"
            root_item = QStandardItem(name)
            root_item.setEditable(False)
            root_item.setData(int(PipelineNodeKind.ROOT), ROLE_KIND)
            f = monos_font("Inter", 12, QFont.Weight.DemiBold)
            root_item.setFont(f)

            order_struct = ["assets", "shots", "inbox", "outbox", "project_guide"]
            for fid in order_struct:
                node = self._structure.get(fid, {})
                label = node.get("label", fid)
                folder = node.get("folder", fid)
                it = QStandardItem(f"{label}  [{folder}]")
                it.setEditable(False)
                it.setData(int(PipelineNodeKind.STRUCTURE), ROLE_KIND)
                it.setData(fid, ROLE_LOGICAL_ID)
                if fid == "assets":
                    for tid in sorted(self._types_map.keys(), key=str.lower):
                        if _is_shot_type_id(tid):
                            continue
                        self._append_type_row(it, tid, shot=False)
                elif fid == "shots":
                    for tid in sorted(self._preset_types.keys(), key=str.lower):
                        if not _is_shot_type_id(tid):
                            continue
                        self._append_type_row(it, tid, shot=True)
                root_item.appendRow([it])

            dept_sec = QStandardItem("Departments")
            dept_sec.setEditable(False)
            dept_sec.setData(int(PipelineNodeKind.DEPTS_SECTION), ROLE_KIND)
            for rid in _dept_roots(self._depts_map):
                self._append_dept_branch(dept_sec, rid)
            root_item.appendRow([dept_sec])

            self._model.appendRow([root_item])
        finally:
            sm.blockSignals(False)
        self._restore_pipeline_tree_state(state)
        self._on_tree_selection()

    def _append_type_row(self, parent_item: QStandardItem, tid: str, *, shot: bool) -> None:
        t = self._preset_types.get(tid)
        disp = t.name if t else tid
        child = QStandardItem(f"{disp}  ({tid})")
        child.setEditable(False)
        child.setData(int(PipelineNodeKind.TYPE_SHOT if shot else PipelineNodeKind.TYPE_ASSET), ROLE_KIND)
        child.setData(tid, ROLE_LOGICAL_ID)
        wf = QStandardItem("Workflow…")
        wf.setEditable(False)
        wf.setData(int(PipelineNodeKind.TYPE_ASSET if not shot else PipelineNodeKind.TYPE_SHOT), ROLE_KIND)
        wf.setData(tid, ROLE_LOGICAL_ID)
        wf.setData("workflow", ROLE_EXTRA)
        parent_item.appendRow([child])
        parent_item.appendRow([wf])

    def _append_dept_branch(self, parent_item: QStandardItem, dept_id: str) -> None:
        raw = self._depts_map.get(dept_id, {})
        lab = raw.get("label", dept_id)
        kind = (
            PipelineNodeKind.SUBDEPARTMENT
            if (isinstance(raw.get("parent"), str) and (raw.get("parent") or "").strip())
            else PipelineNodeKind.DEPARTMENT
        )
        suffix = self._dept_tree_dcc_suffix(dept_id)
        it = QStandardItem(f"{lab}  ({dept_id}){suffix}")
        it.setEditable(False)
        it.setData(int(kind), ROLE_KIND)
        it.setData(dept_id, ROLE_LOGICAL_ID)
        parent_item.appendRow([it])
        for cid in _dept_children(self._depts_map, dept_id):
            self._append_dept_branch(it, cid)

    def _dept_tree_dcc_suffix(self, dept_id: str) -> str:
        reg = self._pipeline_dcc_reg
        if reg is None:
            return ""
        dre = DepartmentRegistry(self._depts_map, None)
        raw = self._depts_map.get(dept_id, {})
        ids = dre.supported_dcc_ids(reg, dept_id)
        explicit = "dccs" in raw and isinstance(raw.get("dccs"), list)
        if not ids:
            if explicit and not raw.get("dccs"):
                return "  · —"
            return "  · (dccs.json)"
        labels: list[str] = []
        for i in ids[:5]:
            try:
                info = reg.get_dcc_info(i)
                ln = info.get("label") if isinstance(info, dict) else None
                labels.append(ln.strip() if isinstance(ln, str) and ln.strip() else i)
            except Exception:
                labels.append(i)
        s = ", ".join(labels)
        if len(ids) > 5:
            s += "…"
        tag = "custom" if explicit else "default"
        return f"  · {s}  [{tag}]"

    def _index_roles(self, idx) -> tuple[object, object, object] | None:
        """Read UserRole data from an index — never QStandardItem (stale after model.clear())."""
        if not idx.isValid():
            return None
        if idx.model() is not self._model:
            return None
        try:
            kind = self._model.data(idx, ROLE_KIND)
            lid = self._model.data(idx, ROLE_LOGICAL_ID)
            extra = self._model.data(idx, ROLE_EXTRA)
        except RuntimeError:
            return None
        return (kind, lid, extra)

    def _current_index_roles(self) -> tuple[object, object, object] | None:
        return self._index_roles(self._tree.currentIndex())

    def _structure_id_for_index(self, idx) -> str | None:
        cur = idx
        while cur.isValid():
            roles = self._index_roles(cur)
            if roles:
                kind, lid, _ = roles
                if kind is not None:
                    try:
                        k = PipelineNodeKind(int(kind))
                        if k == PipelineNodeKind.STRUCTURE and isinstance(lid, str):
                            return lid
                    except (TypeError, ValueError):
                        pass
            cur = cur.parent()
        return None

    def _under_departments_branch(self, idx) -> bool:
        cur = idx
        while cur.isValid():
            roles = self._index_roles(cur)
            if roles and roles[0] is not None:
                try:
                    if PipelineNodeKind(int(roles[0])) == PipelineNodeKind.DEPTS_SECTION:
                        return True
                except (TypeError, ValueError):
                    pass
            cur = cur.parent()
        return False

    def _on_tree_context_menu(self, pos) -> None:
        if not self._project_root:
            return
        idx = self._tree.indexAt(pos)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)

        menu = QMenu(self)
        act_reload = QAction("Reload from disk", self)
        act_reload.triggered.connect(self.reload_from_disk)
        menu.addAction(act_reload)

        struct_id = self._structure_id_for_index(idx)
        under_dept = self._under_departments_branch(idx)
        roles = self._index_roles(idx)
        kind_int: PipelineNodeKind | None = None
        extra: object = None
        if roles:
            kind, _lid, extra = roles
            if kind is not None:
                try:
                    kind_int = PipelineNodeKind(int(kind))
                except (TypeError, ValueError):
                    kind_int = None

        extra_actions: list[QAction] = []
        if struct_id == "assets":
            a = QAction("Add asset type", self)
            a.triggered.connect(lambda: self._on_add_type(shot=False))
            extra_actions.append(a)
        if struct_id == "shots":
            a = QAction("Add shot type", self)
            a.triggered.connect(lambda: self._on_add_type(shot=True))
            extra_actions.append(a)
        if under_dept:
            a = QAction("Add department", self)
            a.triggered.connect(self._on_add_department)
            extra_actions.append(a)
        if kind_int in (PipelineNodeKind.DEPARTMENT, PipelineNodeKind.SUBDEPARTMENT):
            a = QAction("Add subdepartment", self)
            a.triggered.connect(self._on_add_subdepartment)
            extra_actions.append(a)

        removable = False
        if kind_int is not None and extra != "workflow":
            if kind_int in (
                PipelineNodeKind.TYPE_ASSET,
                PipelineNodeKind.TYPE_SHOT,
                PipelineNodeKind.DEPARTMENT,
                PipelineNodeKind.SUBDEPARTMENT,
            ):
                removable = True
        if removable:
            a = QAction("Remove", self)
            a.triggered.connect(self._on_remove_node)
            extra_actions.append(a)

        if extra_actions:
            menu.addSeparator()
            menu.addActions(extra_actions)

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _on_tree_selection(self) -> None:
        roles = self._current_index_roles()
        self._detail_stack.setCurrentWidget(self._page_empty)
        if roles is None:
            return
        kind, lid, extra = roles
        if extra == "workflow" and isinstance(lid, str):
            self._populate_workflow_panel(lid)
            self._detail_stack.setCurrentWidget(self._form_type_workflow["widget"])
            return
        if kind is None:
            return
        try:
            k = PipelineNodeKind(int(kind))
        except (TypeError, ValueError):
            return
        if k == PipelineNodeKind.STRUCTURE and isinstance(lid, str):
            self._populate_structure_panel(lid)
            self._detail_stack.setCurrentWidget(self._form_structure["widget"])
        elif k in (PipelineNodeKind.TYPE_ASSET, PipelineNodeKind.TYPE_SHOT) and isinstance(lid, str):
            if k == PipelineNodeKind.TYPE_ASSET:
                self._populate_type_asset_panel(lid)
                self._detail_stack.setCurrentWidget(self._form_type_asset["widget"])
            else:
                self._populate_type_shot_panel(lid)
                self._detail_stack.setCurrentWidget(self._form_type_shot["widget"])
        elif k in (PipelineNodeKind.DEPARTMENT, PipelineNodeKind.SUBDEPARTMENT) and isinstance(lid, str):
            self._populate_department_panel(lid)
            self._detail_stack.setCurrentWidget(self._form_department["widget"])

    def _build_form_structure(self) -> dict:
        w = QWidget()
        lay = QFormLayout(w)
        lay.setSpacing(10)
        id_lab = QLabel("")
        id_lab.setProperty("mono", True)
        status = QLabel("")
        status.setWordWrap(True)
        le_label = QLineEdit(w)
        le_folder = QLineEdit(w)
        le_label.editingFinished.connect(self._apply_structure_form)
        le_folder.editingFinished.connect(self._apply_structure_form)
        lay.addRow("Logical ID", id_lab)
        lay.addRow("Label", le_label)
        lay.addRow("Folder", le_folder)
        lay.addRow("Status", status)
        return {"widget": w, "id_lab": id_lab, "label": le_label, "folder": le_folder, "status": status, "sid": ""}

    def _build_form_type_asset(self) -> dict:
        w = QWidget()
        lay = QFormLayout(w)
        id_lab = QLabel("")
        id_lab.setProperty("mono", True)
        le_name = QLineEdit(w)
        le_short = QLineEdit(w)
        le_folder = QLineEdit(w)
        for le in (le_name, le_short, le_folder):
            le.editingFinished.connect(self._apply_type_asset_form)
        lay.addRow("Type ID", id_lab)
        lay.addRow("Display name", le_name)
        lay.addRow("Short name", le_short)
        lay.addRow("Folder (under Assets)", le_folder)
        return {"widget": w, "id_lab": id_lab, "name": le_name, "short": le_short, "folder": le_folder, "tid": ""}

    def _build_form_type_shot(self) -> dict:
        w = QWidget()
        lay = QFormLayout(w)
        id_lab = QLabel("")
        id_lab.setProperty("mono", True)
        le_name = QLineEdit(w)
        le_short = QLineEdit(w)
        for le in (le_name, le_short):
            le.editingFinished.connect(self._apply_type_shot_form)
        lay.addRow("Type ID", id_lab)
        lay.addRow("Display name", le_name)
        lay.addRow("Short name", le_short)
        return {"widget": w, "id_lab": id_lab, "name": le_name, "short": le_short, "tid": ""}

    def _build_form_department(self) -> dict:
        w = QWidget()
        root_l = QVBoxLayout(w)
        root_l.setSpacing(12)
        lay = QFormLayout()
        lay.setSpacing(10)
        id_lab = QLabel("")
        id_lab.setProperty("mono", True)
        le_label = QLineEdit(w)
        le_shot = QLineEdit(w)
        le_asset = QLineEdit(w)
        sp_order = QSpinBox(w)
        sp_order.setRange(0, 9999)
        cb_parent = QComboBox(w)
        cb_parent.setProperty("mono", True)
        for le in (le_label, le_shot, le_asset):
            le.editingFinished.connect(self._apply_department_form)
        sp_order.editingFinished.connect(self._apply_department_form)
        cb_parent.currentIndexChanged.connect(self._apply_department_form)
        lay.addRow("Department ID", id_lab)
        lay.addRow("Label", le_label)
        lay.addRow("Shot folder", le_shot)
        lay.addRow("Asset folder", le_asset)
        lay.addRow("Order", sp_order)
        lay.addRow("Parent", cb_parent)
        root_l.addLayout(lay)

        dcc_title = QLabel("Supported DCCs", w)
        dcc_title.setObjectName("DialogSectionTitle")
        root_l.addWidget(dcc_title)
        rb_global = QRadioButton("Use app default (Settings → DCCs / dccs.json)", w)
        rb_custom = QRadioButton("Custom list for this department (saved in departments.json)", w)
        dcc_bg = QButtonGroup(w)
        dcc_bg.addButton(rb_global, 0)
        dcc_bg.addButton(rb_custom, 1)
        root_l.addWidget(rb_global)
        root_l.addWidget(rb_custom)
        dcc_hint = QLabel("", w)
        dcc_hint.setObjectName("DialogHint")
        dcc_hint.setWordWrap(True)
        root_l.addWidget(dcc_hint)
        dcc_inner = QWidget(w)
        dcc_inner_l = QVBoxLayout(dcc_inner)
        dcc_inner_l.setContentsMargins(0, 0, 0, 0)
        dcc_inner_l.setSpacing(4)
        dcc_scroll = QScrollArea(w)
        dcc_scroll.setWidgetResizable(True)
        dcc_scroll.setWidget(dcc_inner)
        dcc_scroll.setMinimumHeight(120)
        dcc_scroll.setMaximumHeight(220)
        dcc_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root_l.addWidget(dcc_scroll, 1)

        return {
            "widget": w,
            "id_lab": id_lab,
            "label": le_label,
            "shot": le_shot,
            "asset": le_asset,
            "order": sp_order,
            "parent": cb_parent,
            "did": "",
            "dcc_bg": dcc_bg,
            "dcc_global": rb_global,
            "dcc_custom": rb_custom,
            "dcc_hint": dcc_hint,
            "dcc_inner": dcc_inner,
            "dcc_inner_l": dcc_inner_l,
            "dcc_scroll": dcc_scroll,
        }

    def _build_form_type_workflow(self) -> dict:
        w = QWidget()
        lay = QVBoxLayout(w)
        title = QLabel("", w)
        title.setObjectName("DialogSectionTitle")
        inner = QWidget(w)
        inner_l = QVBoxLayout(inner)
        inner_l.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(w)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(200)
        lay.addWidget(title)
        lay.addWidget(scroll, 1)
        return {"widget": w, "title": title, "inner_l": inner_l, "tid": ""}

    def _populate_structure_panel(self, sid: str) -> None:
        f = self._form_structure
        f["sid"] = sid
        node = self._structure.get(sid, {})
        f["id_lab"].setText(sid)
        f["label"].setText(node.get("label", sid))
        f["folder"].setText(node.get("folder", sid))
        lvl = "—"
        if self._project_root:
            sreg = StructureRegistry(self._structure, None)
            lvl = sreg.get_mapping_edit_level(self._project_root, sid)
        f["status"].setText(lvl.replace("_", " "))

    def _populate_type_asset_panel(self, tid: str) -> None:
        f = self._form_type_asset
        f["tid"] = tid
        raw = self._types_map.get(tid, {})
        t = self._preset_types.get(tid)
        f["id_lab"].setText(tid)
        f["name"].setText(raw.get("label") or (t.name if t else tid))
        f["short"].setText(t.short_name if t else tid[:4])
        f["folder"].setText(raw.get("folder", tid))

    def _populate_type_shot_panel(self, tid: str) -> None:
        f = self._form_type_shot
        f["tid"] = tid
        t = self._preset_types.get(tid)
        f["id_lab"].setText(tid)
        f["name"].setText(t.name if t else tid)
        f["short"].setText(t.short_name if t else tid[:4])

    def _populate_department_panel(self, did: str) -> None:
        f = self._form_department
        f["did"] = did
        raw = self._depts_map.get(did, {})
        f["id_lab"].setText(did)
        f["label"].setText(raw.get("label", did))
        f["shot"].setText(raw.get("shot_folder") or raw.get("folder") or did)
        f["asset"].setText(raw.get("asset_folder") or raw.get("folder") or did)
        f["order"].setValue(int(raw.get("order", 999)))
        cb = f["parent"]
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("(none)", "")
        cur = (raw.get("parent") or "").strip() if isinstance(raw.get("parent"), str) else ""
        for oid in sorted(self._depts_map.keys(), key=str.lower):
            if oid == did:
                continue
            cb.addItem(f"{oid} — {self._depts_map[oid].get('label', oid)}", oid)
        idx = cb.findData(cur)
        cb.setCurrentIndex(max(0, idx))
        cb.blockSignals(False)
        self._refresh_department_dcc_ui(did)

    def _refresh_department_dcc_ui(self, did: str) -> None:
        f = self._form_department
        reg = self._pipeline_dcc_reg
        raw = self._depts_map.get(did, {})
        has_custom = "dccs" in raw and isinstance(raw.get("dccs"), list)
        f["dcc_global"].blockSignals(True)
        f["dcc_custom"].blockSignals(True)
        f["dcc_global"].setChecked(not has_custom)
        f["dcc_custom"].setChecked(has_custom)
        f["dcc_global"].blockSignals(False)
        f["dcc_custom"].blockSignals(False)
        self._update_dept_dcc_hint(did)
        self._rebuild_dept_dcc_checkboxes(did)

    def _update_dept_dcc_hint(self, did: str) -> None:
        f = self._form_department
        reg = self._pipeline_dcc_reg
        raw = self._depts_map.get(did, {})
        has_custom = "dccs" in raw and isinstance(raw.get("dccs"), list)
        if not reg:
            f["dcc_hint"].setText("App DCC registry unavailable.")
            return
        dre = DepartmentRegistry(self._depts_map, None)
        if has_custom:
            f["dcc_hint"].setText("Checked DCCs are stored as the “dccs” array on this department in departments.json.")
            return
        eff = dre.supported_dcc_ids(reg, did)
        labs: list[str] = []
        for i in eff[:10]:
            try:
                inf = reg.get_dcc_info(i)
                ln = inf.get("label") if isinstance(inf, dict) else None
                labs.append(ln.strip() if isinstance(ln, str) and ln.strip() else i)
            except Exception:
                labs.append(i)
        if not labs:
            f["dcc_hint"].setText(
                "No DCC in dccs.json lists this department. Choose “Custom list” to assign DCCs."
            )
        else:
            tail = "…" if len(eff) > 10 else ""
            f["dcc_hint"].setText("Effective from dccs.json: " + ", ".join(labs) + tail)

    def _rebuild_dept_dcc_checkboxes(self, did: str) -> None:
        f = self._form_department
        reg = self._pipeline_dcc_reg
        inner_l = f["dcc_inner_l"]
        while inner_l.count():
            item = inner_l.takeAt(0)
            w_ch = item.widget()
            if w_ch is not None:
                w_ch.deleteLater()
        if not reg:
            inner_l.addStretch(1)
            return
        raw = self._depts_map.get(did, {})
        has_custom = "dccs" in raw and isinstance(raw.get("dccs"), list)
        dre = DepartmentRegistry(self._depts_map, None)
        effective_ids = dre.supported_dcc_ids(reg, did)
        if has_custom:
            chosen = {str(x).strip().lower() for x in (raw.get("dccs") or []) if isinstance(x, str) and x.strip()}
        else:
            chosen = set(effective_ids)
        custom_on = bool(has_custom)
        parent_w = f["dcc_inner"]
        for dcc_id in reg.get_all_dccs():
            try:
                info = reg.get_dcc_info(dcc_id)
                label = (info.get("label") if isinstance(info, dict) else None) or dcc_id
            except Exception:
                label = dcc_id
            cb = QCheckBox(f"{dcc_id} — {label}", parent_w)
            cb.setProperty("pipeline_dept_dcc_id", dcc_id)
            cb.setChecked(dcc_id in chosen)
            cb.setEnabled(custom_on)
            cb.stateChanged.connect(lambda _s, d=did: self._on_department_dcc_checkbox(d))
            inner_l.addWidget(cb)
        inner_l.addStretch(1)

    def _on_department_dcc_global_toggled(self, on: bool) -> None:
        if on:
            self._on_department_dcc_group(0)

    def _on_department_dcc_custom_toggled(self, on: bool) -> None:
        if on:
            self._on_department_dcc_group(1)

    def _on_department_dcc_group(self, button_id: int) -> None:
        f = self._form_department
        did = f["did"]
        if not did or did not in self._depts_map:
            return
        reg = self._pipeline_dcc_reg
        if button_id == 0:
            self._depts_map[did].pop("dccs", None)
        else:
            cur = self._depts_map[did].get("dccs")
            if not isinstance(cur, list):
                eff = (
                    DepartmentRegistry(self._depts_map, None).supported_dcc_ids(reg, did)
                    if reg
                    else []
                )
                self._depts_map[did]["dccs"] = list(eff)
        self._update_dept_dcc_hint(did)
        self._rebuild_dept_dcc_checkboxes(did)
        self._rebuild_model()
        self.config_changed.emit()

    def _on_department_dcc_checkbox(self, did: str) -> None:
        f = self._form_department
        if did != f["did"] or did not in self._depts_map:
            return
        if not f["dcc_custom"].isChecked():
            return
        inner_l = f["dcc_inner_l"]
        picked: list[str] = []
        for i in range(inner_l.count()):
            w_ch = inner_l.itemAt(i).widget()
            if not isinstance(w_ch, QCheckBox) or not w_ch.isChecked():
                continue
            x = w_ch.property("pipeline_dept_dcc_id")
            if isinstance(x, str) and x.strip():
                picked.append(x.strip().lower())
        self._depts_map[did]["dccs"] = picked
        self._update_dept_dcc_hint(did)
        self._rebuild_model()
        self.config_changed.emit()

    def _populate_workflow_panel(self, tid: str) -> None:
        fw = self._form_type_workflow
        fw["tid"] = tid
        t = self._preset_types.get(tid)
        fw["title"].setText(f"Departments for type: {tid}" + (f" ({t.name})" if t else ""))
        inner_l = fw["inner_l"]
        while inner_l.count():
            w_item = inner_l.takeAt(0)
            w_ch = w_item.widget()
            if w_ch is not None:
                w_ch.deleteLater()
        selected = set(t.departments) if t else set()
        inner_parent = inner_l.parentWidget()
        roots = _dept_roots(self._depts_map)
        first_group = True
        for rid in roots:
            leaves_here = _leaves_under_dept_root(self._depts_map, rid)
            if not leaves_here:
                continue
            raw_lab = (self._depts_map.get(rid, {}).get("label", rid) or rid).strip()
            group_title = QLabel(raw_lab.upper(), inner_parent or fw["widget"])
            group_title.setObjectName("PipelineWorkflowGroupTitle")
            mtop = 2 if first_group else 14
            group_title.setContentsMargins(0, mtop, 0, 6)
            inner_l.addWidget(group_title)
            first_group = False
            for did in leaves_here:
                lab = self._depts_map.get(did, {}).get("label", did)
                cb = QCheckBox(f"{did} — {lab}", inner_parent or fw["widget"])
                cb.setProperty("pipeline_workflow_dept_id", did)
                cb.setChecked(did in selected)
                cb.setContentsMargins(12, 0, 0, 2)
                cb.toggled.connect(self._make_workflow_handler(tid))
                inner_l.addWidget(cb)
        inner_l.addStretch(1)

    def _make_workflow_handler(self, tid: str):
        def _on() -> None:
            self._apply_workflow_checks(tid)

        return _on

    def _apply_workflow_checks(self, tid: str) -> None:
        fw = self._form_type_workflow
        inner = fw["inner_l"]
        picked: list[str] = []
        for i in range(inner.count()):
            item = inner.itemAt(i).widget()
            if not isinstance(item, QCheckBox) or not item.isChecked():
                continue
            did = item.property("pipeline_workflow_dept_id")
            if isinstance(did, str) and did.strip():
                picked.append(did.strip())
        t = self._preset_types.get(tid)
        if t:
            self._preset_types[tid] = TypeDef(tid, t.name, t.short_name, picked, t.icon_name)
        self.config_changed.emit()

    def _apply_structure_form(self) -> None:
        sid = self._form_structure["sid"]
        if not sid or sid not in self._structure:
            return
        self._structure[sid] = {
            "label": self._form_structure["label"].text().strip() or sid,
            "folder": self._form_structure["folder"].text().strip() or sid,
        }
        self._rebuild_model()
        self.config_changed.emit()

    def _apply_type_asset_form(self) -> None:
        tid = self._form_type_asset["tid"]
        if not tid:
            return
        name = self._form_type_asset["name"].text().strip() or tid
        short = self._form_type_asset["short"].text().strip() or tid[:4]
        folder = self._form_type_asset["folder"].text().strip() or tid
        self._types_map[tid] = {"label": name, "folder": folder}
        t = self._preset_types.get(tid)
        if t:
            self._preset_types[tid] = TypeDef(tid, name, short, list(t.departments), t.icon_name)
        else:
            self._preset_types[tid] = TypeDef(tid, name, short, [], None)
        self._rebuild_model()
        self.config_changed.emit()

    def _apply_type_shot_form(self) -> None:
        tid = self._form_type_shot["tid"]
        if not tid:
            return
        name = self._form_type_shot["name"].text().strip() or tid
        short = self._form_type_shot["short"].text().strip() or tid[:4]
        t = self._preset_types.get(tid)
        if t:
            self._preset_types[tid] = TypeDef(tid, name, short, list(t.departments), t.icon_name)
        else:
            self._preset_types[tid] = TypeDef(tid, name, short, [], None)
        self._rebuild_model()
        self.config_changed.emit()

    def _apply_department_form(self) -> None:
        did = self._form_department["did"]
        if not did or did not in self._depts_map:
            return
        cb = self._form_department["parent"]
        parent = cb.currentData()
        parent = parent if isinstance(parent, str) and parent.strip() else ""
        if parent == did:
            parent = ""
        prev = self._depts_map.get(did, {})
        self._depts_map[did] = {
            "label": self._form_department["label"].text().strip() or did,
            "folder": self._form_department["shot"].text().strip() or did,
            "shot_folder": self._form_department["shot"].text().strip() or did,
            "asset_folder": self._form_department["asset"].text().strip() or did,
            "order": int(self._form_department["order"].value()),
        }
        if isinstance(prev.get("dccs"), list):
            self._depts_map[did]["dccs"] = list(prev["dccs"])
        if parent:
            self._depts_map[did]["parent"] = parent
        else:
            self._depts_map[did].pop("parent", None)
        d = self._preset_depts.get(did)
        lab = self._depts_map[did]["label"]
        if d:
            self._preset_depts[did] = DepartmentDef(did, lab, d.short_name, d.icon_name, parent or None)
        else:
            self._preset_depts[did] = DepartmentDef(did, lab, did[:4], None, parent or None)
        self._rebuild_model()
        self.config_changed.emit()

    def _on_add_type(self, *, shot: bool) -> None:
        if not self._project_root:
            return
        from PySide6.QtWidgets import QInputDialog

        tid, ok = QInputDialog.getText(self, "New type", "Type ID (lowercase, underscores):")
        if not ok:
            return
        tid = tid.strip()
        if not _is_valid_type_id(tid):
            QMessageBox.warning(self, "Invalid ID", "Type ID must be lowercase letters, digits, underscores only.")
            return
        if shot:
            if not _is_shot_type_id(tid):
                QMessageBox.warning(self, "Invalid shot type", "Shot type ID must be 'shot' or start with 'shot_'.")
                return
        else:
            if _is_shot_type_id(tid):
                QMessageBox.warning(self, "Invalid asset type", "Asset type cannot be named shot or shot_*.")
                return
        if tid in self._preset_types or (not shot and tid in self._types_map):
            QMessageBox.warning(self, "Duplicate", "That type ID already exists.")
            return
        self._preset_types[tid] = TypeDef(tid, tid.replace("_", " ").title(), tid[:4], [], None)
        if not shot:
            self._types_map[tid] = {"label": self._preset_types[tid].name, "folder": tid}
        self._rebuild_model()
        self.config_changed.emit()

    def _on_add_department(self) -> None:
        if not self._project_root:
            return
        from PySide6.QtWidgets import QInputDialog

        did, ok = QInputDialog.getText(self, "New department", "Department ID (lowercase, underscores):")
        if not ok:
            return
        did = did.strip()
        if not did or not did.replace("_", "").isalnum() or did != did.lower():
            QMessageBox.warning(self, "Invalid ID", "Use lowercase id with letters, digits, underscores.")
            return
        if did in self._depts_map:
            QMessageBox.warning(self, "Duplicate", "That department ID already exists.")
            return
        n = max((int(self._depts_map[k].get("order", 0)) for k in self._depts_map), default=0) + 1
        self._depts_map[did] = {
            "label": did.replace("_", " ").title(),
            "folder": did,
            "shot_folder": did,
            "asset_folder": did,
            "order": n,
        }
        self._preset_depts[did] = DepartmentDef(did, self._depts_map[did]["label"], did[:4], None, None)
        self._rebuild_model()
        self.config_changed.emit()

    def _on_add_subdepartment(self) -> None:
        roles = self._current_index_roles()
        if roles is None:
            QMessageBox.information(self, "Subdepartment", "Select a parent department first.")
            return
        kind, lid, _extra = roles
        if kind is None or not isinstance(lid, str):
            return
        try:
            k = PipelineNodeKind(int(kind))
        except (TypeError, ValueError):
            return
        if k not in (PipelineNodeKind.DEPARTMENT, PipelineNodeKind.SUBDEPARTMENT):
            QMessageBox.information(self, "Subdepartment", "Select a department or subdepartment as parent.")
            return
        parent_id = lid
        from PySide6.QtWidgets import QInputDialog

        did, ok = QInputDialog.getText(self, "New subdepartment", "Department ID:")
        if not ok:
            return
        did = did.strip()
        if not did or did in self._depts_map:
            QMessageBox.warning(self, "Invalid", "ID must be new and non-empty.")
            return
        n = max((int(self._depts_map[k].get("order", 0)) for k in self._depts_map), default=0) + 1
        new_row: dict = {
            "label": did.replace("_", " ").title(),
            "folder": did,
            "shot_folder": did,
            "asset_folder": did,
            "order": n,
            "parent": parent_id,
        }
        parent_raw = self._depts_map.get(parent_id) or {}
        if "dccs" in parent_raw and isinstance(parent_raw.get("dccs"), list):
            new_row["dccs"] = [
                str(x).strip().lower()
                for x in parent_raw["dccs"]
                if isinstance(x, str) and x.strip()
            ]
        self._depts_map[did] = new_row
        self._preset_depts[did] = DepartmentDef(did, self._depts_map[did]["label"], did[:4], None, parent_id)
        self._rebuild_model()
        self.config_changed.emit()

    def _on_remove_node(self) -> None:
        roles = self._current_index_roles()
        if roles is None:
            return
        kind, lid, extra = roles
        if extra == "workflow":
            return
        try:
            k = PipelineNodeKind(int(kind)) if kind is not None else None
        except (TypeError, ValueError):
            return
        if k in (PipelineNodeKind.ROOT, PipelineNodeKind.STRUCTURE, PipelineNodeKind.DEPTS_SECTION):
            QMessageBox.information(self, "Remove", "This node cannot be removed.")
            return
        if k in (PipelineNodeKind.TYPE_ASSET, PipelineNodeKind.TYPE_SHOT) and isinstance(lid, str):
            if QMessageBox.question(self, "Remove type", f"Remove type '{lid}'?") != QMessageBox.StandardButton.Yes:
                return
            self._preset_types.pop(lid, None)
            self._types_map.pop(lid, None)
        elif k in (PipelineNodeKind.DEPARTMENT, PipelineNodeKind.SUBDEPARTMENT) and isinstance(lid, str):
            if QMessageBox.question(self, "Remove department", f"Remove '{lid}'? Children will be promoted.") != QMessageBox.StandardButton.Yes:
                return
            for d, n in list(self._depts_map.items()):
                if (n.get("parent") or "").strip() == lid:
                    n = dict(n)
                    n.pop("parent", None)
                    self._depts_map[d] = n
                    pd = self._preset_depts.get(d)
                    if pd:
                        self._preset_depts[d] = DepartmentDef(pd.dept_id, pd.name, pd.short_name, pd.icon_name, None)
            self._depts_map.pop(lid, None)
            self._preset_depts.pop(lid, None)
            for t in list(self._preset_types.values()):
                if lid in t.departments:
                    nd = [x for x in t.departments if x != lid]
                    self._preset_types[t.type_id] = TypeDef(t.type_id, t.name, t.short_name, nd, t.icon_name)
        else:
            return
        self._rebuild_model()
        self.config_changed.emit()
