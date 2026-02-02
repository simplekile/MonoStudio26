from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, Signal

from monostudio.core.dcc_blender import BlenderDccAdapter
from monostudio.core.models import Asset


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

    def __init__(self, *, settings: QSettings, repo_root: Path, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._repo_root = repo_root

        self.current_department: str | None = None
        self.current_type: str | None = None

        self._project_root: Path | None = None

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

    def handle_department_activated(self, *, asset: Asset, department: str) -> None:
        """
        Open or create the DCC work file for (asset, department).
        """
        if self._project_root is None:
            raise RuntimeError("No project is selected; cannot resolve work file path.")
        if not isinstance(department, str) or not department.strip():
            raise RuntimeError("Department is required.")

        work_file = self._resolve_asset_work_file(asset=asset, department=department)
        if not work_file.parent.is_dir():
            raise RuntimeError(f"Work folder does not exist: {str(work_file.parent)!r}")

        ctx = self._build_context(asset=asset, department=department)
        dcc = self._blender_adapter()

        if work_file.is_file():
            dcc.open_file(filepath=str(work_file), context=ctx)
        else:
            dcc.create_new_file(filepath=str(work_file), context=ctx)

    def _resolve_asset_work_file(self, *, asset: Asset, department: str) -> Path:
        """
        Deterministic work file path (Blender v1):
          <asset>/<department>/work/<asset_folder_name>.blend
        """
        return asset.path / department / "work" / f"{asset.name}.blend"

    def _build_context(self, *, asset: Asset, department: str) -> dict[str, Any]:
        project_id = self._project_root.name if self._project_root else ""
        if not project_id:
            raise RuntimeError("Cannot resolve project_id from project root.")
        return {
            "project_id": project_id,
            "entity_type": "asset",
            "entity_id": asset.name,
            "department": department,
            "dcc": "blender",
        }

    def _blender_executable(self) -> str:
        # Configurable via Settings later; keep deterministic keys.
        exe = self._settings.value("integrations/blender_exe", "", str)
        exe = (exe or "").strip()
        return exe or "blender"

    def _blender_adapter(self) -> BlenderDccAdapter:
        return BlenderDccAdapter(blender_executable=self._blender_executable(), repo_root=self._repo_root)

