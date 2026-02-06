from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QObject, QSettings, Signal, QThreadPool, QRunnable

from monostudio.core.dcc_blender import BlenderDccAdapter
from monostudio.core.dcc_houdini import HoudiniDccAdapter
from monostudio.core.dcc_maya import MayaDccAdapter
from monostudio.core.dcc_substance_painter import SubstancePainterDccAdapter
from monostudio.core.dcc_registry import DccRegistry, get_default_dcc_registry
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.fs_reader import (
    get_work_file_path,
    read_use_dcc_folders,
    resolve_work_path,
    work_file_prefix,
)
from monostudio.core.models import Asset, Shot
from monostudio.core.pending_create import add as pending_create_add
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.open_resolver_dialog import OpenResolverDialog
from monostudio.ui_qt.style import MONOS_COLORS


class AppController(QObject):
    """
    Centralized state + integration logic.

    UI widgets emit intents only.
    AppController owns:
    - filter state
    - filesystem path resolution (work file)
    - DCC open/create decision
    """

    departmentChanged = Signal(object)  # str | None
    typeChanged = Signal(object)  # str | None
    # (exe, path_norm, file_created, repo_root) — emitted from worker thread; slot runs on main thread
    mayaLaunchRequested = Signal(str, str, bool, str)

    def __init__(self, *, settings: QSettings, repo_root: Path, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._repo_root = repo_root
        self._dcc_registry: DccRegistry = get_default_dcc_registry()

        self.current_department: str | None = None
        self.current_type: str | None = None

        self._project_root: Path | None = None
        # Inspector explicit focus (per item path).
        self._inspector_focus_by_item: dict[str, str] = {}
        self._inspector_current_item_path: str | None = None

        self.mayaLaunchRequested.connect(self._on_maya_launch_requested)

    def set_project_root(self, project_root: Path | None) -> None:
        self._project_root = project_root

    def on_department_clicked(self, department: object) -> None:
        new = department if isinstance(department, str) and department.strip() else None
        if new is not None and new == self.current_department:
            new = None
        if new == self.current_department:
            return
        self.current_department = new
        self.departmentChanged.emit(new)

    def on_type_clicked(self, type_id: object) -> None:
        new = type_id if isinstance(type_id, str) and type_id.strip() else None
        if new is not None and new == self.current_type:
            new = None
        if new == self.current_type:
            return
        self.current_type = new
        self.typeChanged.emit(new)

    def sync_filter_state(self, *, department: str | None, type_id: str | None) -> None:
        """
        Sync state from restored UI selection when switching pages.
        """
        dep = department if isinstance(department, str) and department.strip() else None
        typ = type_id if isinstance(type_id, str) and type_id.strip() else None

        if dep != self.current_department:
            self.current_department = dep
            self.departmentChanged.emit(dep)
        if typ != self.current_type:
            self.current_type = typ
            self.typeChanged.emit(typ)

    def on_inspector_department_focused(self, *, item_path: Path, department: str) -> None:
        """
        Explicit user intent: Inspector focused a department for this item.
        Stored for Smart Open priority #1.
        """
        p = str(Path(item_path))
        dep = (department or "").strip()
        if not p or not dep:
            return
        self._inspector_focus_by_item[p] = dep

    def on_inspector_item_changed(self, item_path: Path | None) -> None:
        """
        Keeps Smart Open priority #1 honest:
        only use Inspector focus when Inspector is currently showing the same item.
        """
        self._inspector_current_item_path = str(item_path) if item_path else None

    def smart_open(self, *, item: Asset | Shot, force_dialog: bool = False, force_create_new: bool = False, parent=None) -> None:
        """
        Smart Open Resolver (primary interaction for double-click).
        - Resolve Department (priority order)
        - Resolve DCC (priority order)
        - Open existing work file, else create new one (unless force_create_new=True)
        - Update per-item open metadata on success

        Dialog is fallback only (unless force_dialog=True).
        force_create_new=True: show "Create New…" dialog and always create a new work file (never open existing).
        """
        if self._project_root is None:
            raise RuntimeError("No project is selected; cannot open DCC.")

        available_depts = self._available_departments(item)
        if not available_depts:
            raise RuntimeError("This item has no departments; cannot open DCC.")

        meta = self._read_item_open_metadata(item.path)
        project_defaults = self._read_project_defaults(self._project_root)

        resolved_department = self._resolve_department(
            item=item,
            available_departments=available_depts,
            meta=meta,
            project_defaults=project_defaults,
        )

        resolved_dcc = None
        if resolved_department:
            resolved_dcc = self._resolve_dcc(
                department=resolved_department,
                meta=meta,
                project_defaults=project_defaults,
            )

        # Only skip dialog when we open the *resolved* department and that department has a work file.
        resolved_dept_has_work_file = False
        if resolved_department:
            for d in item.departments:
                if self._norm(d.name) == self._norm(resolved_department):
                    resolved_dept_has_work_file = getattr(d, "work_file_exists", False)
                    break

        remember_for_item = False
        show_dialog = force_dialog or force_create_new or not resolved_department or not resolved_dcc or not resolved_dept_has_work_file
        # When no work file exists (e.g. double-click on new item), show Create New dialog instead of Open With.
        use_create_new_dialog = force_create_new or (show_dialog and not resolved_dept_has_work_file)
        # No department dropdown: always use resolved department (fallback to first available).
        if not resolved_department and available_depts:
            resolved_department = available_depts[0]
        if show_dialog:
            dept_registry = DepartmentRegistry.for_project(self._project_root)
            if use_create_new_dialog:
                dialog_title = "Create New…"
                dialog_icon = lucide_icon("file-plus", size=28, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
                hint_text = "Choose a DCC to create a new work file."
                primary_button_text = "Create"
            else:
                dialog_title = "Open With…"
                dialog_icon = lucide_icon("layers", size=28, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
                hint_text = (
                    "Choose a DCC to open. Only DCCs with existing work files are shown."
                )
                primary_button_text = "Open"
            allowed_dcc_ids: list[str] | None = None
            disabled_dcc_ids: list[str] | None = None
            if use_create_new_dialog and resolved_department:
                # Create New: only show DCCs that are available for this department (from dccs.json).
                allowed_dcc_ids = self._dcc_registry.get_available_dccs(resolved_department)
            elif not use_create_new_dialog and item.departments and resolved_department:
                # Open With: only show DCCs that have work files in this department.
                for d in item.departments:
                    if self._norm(d.name) == self._norm(resolved_department) and getattr(
                        d, "work_file_exists", False
                    ):
                        dccs = getattr(d, "work_file_dccs", ()) or ()
                        allowed_dcc_ids = [x.strip() for x in dccs if isinstance(x, str) and x.strip()]
                        break
                else:
                    allowed_dcc_ids = []
            if use_create_new_dialog and resolved_department:
                # Disable DCCs whose work folder already exists for this item in this department.
                _disabled: set[str] = set()
                for (dept_id, dcc_id), state in getattr(item, "dcc_work_states", None) or ():
                    if self._norm(str(dept_id)) == self._norm(resolved_department) and getattr(
                        state, "work_folder_exists", False
                    ):
                        _d = (dcc_id or "").strip()
                        if _d:
                            _disabled.add(_d)
                if _disabled:
                    disabled_dcc_ids = list(_disabled)
            item_name = item.name or ""
            type_folder = getattr(item, "asset_type", None) or "shot"
            department_label = (dept_registry.get_department_label(resolved_department) or resolved_department or "") if resolved_department else ""
            dlg = OpenResolverDialog(
                title=dialog_title,
                department_registry=dept_registry,
                available_department_ids=available_depts,
                dcc_registry=self._dcc_registry,
                initial_department=resolved_department,
                initial_dcc=resolved_dcc,
                icon=dialog_icon,
                hint_text=hint_text,
                primary_button_text=primary_button_text,
                allowed_dcc_ids=allowed_dcc_ids,
                disabled_dcc_ids=disabled_dcc_ids,
                show_department_picker=False,
                item_name=item_name,
                type_folder=type_folder,
                department_label=department_label,
                parent=parent,
            )
            if dlg.exec() != OpenResolverDialog.Accepted:
                return
            choice = dlg.choice()
            if choice is None:
                return
            resolved_department = choice.department
            resolved_dcc = choice.dcc
            remember_for_item = bool(choice.remember_for_item)

        if not resolved_department or not resolved_dcc:
            raise RuntimeError("Failed to resolve Department or DCC.")

        action = self._open_or_create_work_file(
            item=item,
            department=resolved_department,
            dcc=resolved_dcc,
            force_create=force_create_new or use_create_new_dialog,
        )

        # Success: persist last-open metadata and which action was taken (open or create).
        self._write_item_open_metadata(
            item.path,
            department=resolved_department,
            dcc=resolved_dcc,
            remember_for_item=remember_for_item,
            action=action,
        )

    def _available_departments(self, item: Asset | Shot) -> list[str]:
        # Deterministic ordering (filesystem scan is already sorted).
        if isinstance(item, Asset):
            depts = [d.name for d in item.departments]
        else:
            depts = [d.name for d in item.departments]
        out: list[str] = []
        seen: set[str] = set()
        for d in depts:
            if isinstance(d, str) and d.strip() and d not in seen:
                seen.add(d)
                out.append(d)
        return out

    @staticmethod
    def _norm(s: str | None) -> str:
        return (s or "").strip().casefold()

    def _resolve_department(
        self,
        *,
        item: Asset | Shot,
        available_departments: list[str],
        meta: dict[str, Any],
        project_defaults: dict[str, Any],
    ) -> str | None:
        # 1) Inspector Department Focus (explicit)
        if self._inspector_current_item_path == str(item.path):
            focused = self._inspector_focus_by_item.get(str(item.path))
            if focused and any(self._norm(d) == self._norm(focused) for d in available_departments):
                for d in available_departments:
                    if self._norm(d) == self._norm(focused):
                        return d

        # 2) Sidebar Active Department
        if self.current_department and any(self._norm(d) == self._norm(self.current_department) for d in available_departments):
            for d in available_departments:
                if self._norm(d) == self._norm(self.current_department):
                    return d

        # 3) Asset/Shot last-used (or per-item default if present)
        defaults = meta.get("defaults") if isinstance(meta, dict) else None
        if isinstance(defaults, dict):
            dep = defaults.get("department")
            if isinstance(dep, str) and dep.strip():
                for d in available_departments:
                    if self._norm(d) == self._norm(dep):
                        return d

        last_open = meta.get("last_open") if isinstance(meta, dict) else None
        if isinstance(last_open, dict):
            dep = last_open.get("department")
            if isinstance(dep, str) and dep.strip():
                for d in available_departments:
                    if self._norm(d) == self._norm(dep):
                        return d

        # 4) Project default department
        dep = project_defaults.get("department")
        if isinstance(dep, str) and dep.strip():
            for d in available_departments:
                if self._norm(d) == self._norm(dep):
                    return d

        return None

    def _resolve_dcc(self, *, department: str, meta: dict[str, Any], project_defaults: dict[str, Any]) -> str | None:
        # 1) Asset/Department last-used DCC
        last_used: str | None = None
        by_dep = meta.get("last_open_by_department") if isinstance(meta, dict) else None
        if isinstance(by_dep, dict):
            node = by_dep.get(department)
            if isinstance(node, dict):
                dcc = node.get("dcc")
                if isinstance(dcc, str) and dcc.strip():
                    last_used = dcc.strip()

        last_open = meta.get("last_open") if isinstance(meta, dict) else None
        if isinstance(last_open, dict) and self._norm(last_open.get("department")) == self._norm(department):
            dcc = last_open.get("dcc")
            if isinstance(dcc, str) and dcc.strip():
                last_used = dcc.strip()

        defaults = meta.get("defaults") if isinstance(meta, dict) else None
        if isinstance(defaults, dict):
            dcc = defaults.get("dcc")
            if isinstance(dcc, str) and dcc.strip():
                last_used = dcc.strip()

        # Registry is the single source of truth for allowed + defaults.
        return self._dcc_registry.resolve_default_dcc(department=department, last_used=last_used)

    @staticmethod
    def _resolve_open_action(work_file_path: Path) -> Literal["open", "create"]:
        """
        Resolve whether to open existing file or create new one.
        Centralizes the decision for double-click; no dialog, no UI.
        """
        return "open" if work_file_path.is_file() else "create"

    def _open_or_create_work_file(
        self, *, item: Asset | Shot, department: str, dcc: str, force_create: bool = False
    ) -> Literal["open", "create"]:
        """
        Resolve work file path, decide action (open vs create), call the correct adapter method.
        force_create=True: always create new file (used by "Create New…" flow).
        Returns the action taken for recording (metadata, future analytics).
        """
        work_file = self._resolve_work_file(item=item, department=department, dcc=dcc)
        action = "create" if force_create else self._resolve_open_action(work_file)
        work_dir = work_file.parent
        if not work_dir.is_dir():
            if action == "open":
                raise RuntimeError(f"Work folder does not exist: {str(work_dir)!r}")
            try:
                work_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise RuntimeError(f"Cannot create work folder: {work_dir!r}") from e
        ctx = self._build_context(item=item, department=department, dcc=dcc)
        adapter = self._dcc_adapter(dcc)
        if adapter is None:
            raise RuntimeError(f"Unsupported DCC: {dcc!r}")

        if action == "open":
            adapter.open_file(filepath=str(work_file), context=ctx)
        else:
            # Record pending create so UI shows "Creating…" and we avoid false "new" from immediate scan.
            pending_create_add(str(item.path), department, dcc)
            if dcc == "maya":
                # on_ready is called from worker thread; emit signal so slot runs on main thread (QTimer from thread has no event loop)
                adapter.create_new_file(
                    filepath=str(work_file),
                    context=ctx,
                    on_ready=self.mayaLaunchRequested.emit,
                )
            elif dcc == "houdini":
                # Run in background: hython + Popen can take several seconds and freeze UI otherwise
                _exe = self._houdini_executable()
                _repo = self._repo_root
                _path = str(work_file)
                _ctx = ctx

                class _HoudiniCreateRunnable(QRunnable):
                    def run(self):
                        a = HoudiniDccAdapter(houdini_executable=_exe, repo_root=_repo)
                        a.create_new_file(filepath=_path, context=_ctx)

                QThreadPool.globalInstance().start(_HoudiniCreateRunnable())
            else:
                adapter.create_new_file(filepath=str(work_file), context=ctx)
        return action

    def _resolve_work_file(self, *, item: Asset | Shot, department: str, dcc: str) -> Path:
        """Resolve work file path: {name}_{department_folder}_v###{ext}. Respects use_dcc_folders (dept/<dcc>/work)."""
        ext = self._dcc_workfile_extension(dcc)
        dep_norm = (department or "").strip().casefold()
        use_dcc_folders = (
            read_use_dcc_folders(self._project_root) if self._project_root else False
        )
        for d in item.departments:
            if (d.name or "").strip().casefold() == dep_norm:
                prefix = work_file_prefix(name=item.name, department=d.path.name)
                work_path = resolve_work_path(
                    d.path, dcc, use_dcc_folders, self._dcc_registry
                )
                return get_work_file_path(work_path, prefix, ext)
        # Fallback when department string does not match any item.departments: resolve via registry.
        if self._project_root is not None:
            dept_reg = DepartmentRegistry.for_project(self._project_root)
            entity = "asset" if isinstance(item, Asset) else "shot"
            dept_folder = dept_reg.get_department_folder(department, entity)
            dept_dir = item.path / dept_folder
            work_path = resolve_work_path(
                dept_dir, dcc, use_dcc_folders, self._dcc_registry
            )
            # Nested: prefix dùng segment cuối (vd 01_sculpt), không dùng full path
            dept_segment = Path(dept_folder).name if "/" in (dept_folder or "") else (dept_folder or "")
            prefix = work_file_prefix(name=item.name, department=dept_segment)
            return get_work_file_path(work_path, prefix, ext)
        prefix = work_file_prefix(name=item.name, department=department)
        return get_work_file_path(item.path / department / "work", prefix, ext)

    def _build_context(self, *, item: Asset | Shot, department: str, dcc: str) -> dict[str, Any]:
        project_id = self._project_root.name if self._project_root else ""
        if not project_id:
            raise RuntimeError("Cannot resolve project_id from project root.")
        entity_type = "asset" if isinstance(item, Asset) else "shot"
        return {
            "project_id": project_id,
            "entity_type": entity_type,
            "entity_id": item.name,
            "department": department,
            "dcc": dcc,
        }

    def _dcc_workfile_extension(self, dcc_id: str) -> str:
        """
        DCC workfile extension is defined by the DCC registry config.
        If multiple are available, use the first one deterministically.
        """
        info = self._dcc_registry.get_dcc_info(dcc_id)
        exts = info.get("workfile_extensions") if isinstance(info, dict) else None
        if isinstance(exts, list) and exts:
            ext0 = exts[0]
            if isinstance(ext0, str) and ext0.strip():
                return ext0.strip()
        return ""

    def _blender_executable(self) -> str:
        # User override (per-machine); fallback to registry default executable.
        exe = self._settings.value("integrations/blender_exe", "", str)
        exe = (exe or "").strip()
        if exe:
            return exe
        try:
            return str(self._dcc_registry.get_dcc_info("blender").get("executable") or "blender")
        except Exception:
            return "blender"

    def _blender_adapter(self) -> BlenderDccAdapter:
        return BlenderDccAdapter(blender_executable=self._blender_executable(), repo_root=self._repo_root)

    def _maya_executable(self) -> str:
        exe = self._settings.value("integrations/maya_exe", "", str)
        exe = (exe or "").strip()
        if exe:
            return exe
        try:
            return str(self._dcc_registry.get_dcc_info("maya").get("executable") or "maya")
        except Exception:
            return "maya"

    def _maya_adapter(self) -> MayaDccAdapter:
        return MayaDccAdapter(maya_executable=self._maya_executable(), repo_root=self._repo_root)

    def _houdini_executable(self) -> str:
        exe = self._settings.value("integrations/houdini_exe", "", str)
        exe = (exe or "").strip()
        if exe:
            return exe
        try:
            return str(self._dcc_registry.get_dcc_info("houdini").get("executable") or "houdini")
        except Exception:
            return "houdini"

    def _houdini_adapter(self) -> HoudiniDccAdapter:
        return HoudiniDccAdapter(houdini_executable=self._houdini_executable(), repo_root=self._repo_root)

    def _substance_painter_executable(self) -> str:
        exe = self._settings.value("integrations/substance_painter_exe", "", str)
        exe = (exe or "").strip()
        if exe:
            return exe
        try:
            return str(
                self._dcc_registry.get_dcc_info("substance_painter").get("executable") or "substancepainter"
            )
        except Exception:
            return "substancepainter"

    def _substance_painter_adapter(self) -> SubstancePainterDccAdapter:
        return SubstancePainterDccAdapter(
            substance_painter_executable=self._substance_painter_executable(),
            repo_root=self._repo_root,
        )

    def _dcc_adapter(
        self, dcc: str
    ) -> BlenderDccAdapter | MayaDccAdapter | HoudiniDccAdapter | SubstancePainterDccAdapter | None:
        if dcc == "blender":
            return self._blender_adapter()
        if dcc == "maya":
            return self._maya_adapter()
        if dcc == "houdini":
            return self._houdini_adapter()
        if dcc == "substance_painter":
            return self._substance_painter_adapter()
        return None

    def _on_maya_launch_requested(
        self, exe: str, path_norm: str, file_created: bool, repo_root: str
    ) -> None:
        """Runs on main thread when mayabatch has finished (signal emitted from worker)."""
        try:
            if file_created:
                subprocess.Popen(
                    [exe, "-file", path_norm],
                    cwd=repo_root,
                    close_fds=True,
                )
            else:
                subprocess.Popen(
                    [exe],
                    cwd=str(Path(path_norm).parent),
                    close_fds=True,
                )
        except Exception:
            pass

    @staticmethod
    def _item_meta_path(item_root: Path) -> Path:
        return Path(item_root) / ".monostudio" / "open.json"

    def _read_item_open_metadata(self, item_root: Path) -> dict[str, Any]:
        path = self._item_meta_path(item_root)
        try:
            if not path.is_file():
                return {}
        except OSError:
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_item_open_metadata(
        self,
        item_root: Path,
        *,
        department: str,
        dcc: str,
        remember_for_item: bool,
        action: Literal["open", "create"] | None = None,
    ) -> None:
        root = Path(item_root)
        meta_dir = root / ".monostudio"
        path = self._item_meta_path(root)

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        data = self._read_item_open_metadata(root)
        if not isinstance(data, dict):
            data = {}

        data["schema"] = 1
        last_open: dict[str, Any] = {"department": department, "dcc": dcc, "opened_at": now}
        if action is not None:
            last_open["action"] = action
        data["last_open"] = last_open

        by_dep = data.get("last_open_by_department")
        if not isinstance(by_dep, dict):
            by_dep = {}
        by_dep[department] = {"dcc": dcc, "opened_at": now}
        data["last_open_by_department"] = by_dep

        if remember_for_item:
            data["defaults"] = {"department": department, "dcc": dcc}

        try:
            from monostudio.core.atomic_write import atomic_write_text
            content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            atomic_write_text(path, content, encoding="utf-8")
        except OSError:
            # Metadata must never block opening; fail silently.
            pass

    @staticmethod
    def _read_project_defaults(project_root: Path) -> dict[str, Any]:
        """
        Project defaults are stored in: <project>/.monostudio/project.json
        Optional shape:
          {
            "defaults": { "department": "layout", "dcc": "blender" }
          }
        """
        path = Path(project_root) / ".monostudio" / "project.json"
        try:
            if not path.is_file():
                return {}
        except OSError:
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        defaults = data.get("defaults")
        if isinstance(defaults, dict):
            out: dict[str, Any] = {}
            dep = defaults.get("department")
            dcc = defaults.get("dcc")
            if isinstance(dep, str) and dep.strip():
                out["department"] = dep.strip()
            if isinstance(dcc, str) and dcc.strip():
                out["dcc"] = dcc.strip()
            return out
        # Back-compat: also accept top-level keys if present.
        out2: dict[str, Any] = {}
        dep = data.get("default_department")
        dcc = data.get("default_dcc")
        if isinstance(dep, str) and dep.strip():
            out2["department"] = dep.strip()
        if isinstance(dcc, str) and dcc.strip():
            out2["dcc"] = dcc.strip()
        return out2

