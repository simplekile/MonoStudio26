"""
Filesystem watcher and event collection for incremental project updates.

- QFileSystemWatcher records changed paths only; no processing in callbacks.
- FsEventCollector debounces, normalizes, and classifies paths into scope
  (single asset, single shot, type folder, unknown) using TypeRegistry and
  DepartmentRegistry. Never infers logic from folder names alone.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

if TYPE_CHECKING:
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.type_registry import TypeRegistry

logger = logging.getLogger(__name__)
# Use this in app to see watcher activity: logging.getLogger("monostudio.fs_watcher").setLevel(logging.DEBUG)
_watcher_log = logging.getLogger("monostudio.fs_watcher")

# Debounce window (ms): collect events before processing
DEFAULT_DEBOUNCE_MS = 300


def _normalize_path(path: str | Path) -> Path | None:
    """Resolve to absolute path; resolve symlinks. Return None on error."""
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            return None
        return p.resolve()
    except (OSError, RuntimeError):
        return None


def _classify_path(
    project_root: Path,
    path: Path,
    type_registry: "TypeRegistry",
    assets_folder: str = "assets",
    shots_folder: str = "shots",
) -> tuple[str | None, str | None, str | None]:
    """
    Classify a path under project_root into scope.
    Returns (asset_id or None, shot_id or None, type_folder or None).
    Uses TypeRegistry to resolve type folder; does not infer from names.
    """
    try:
        path = path.resolve()
        project_root = project_root.resolve()
    except OSError:
        return (None, None, None)
    try:
        rel = path.relative_to(project_root)
    except ValueError:
        return (None, None, None)
    parts = rel.parts
    if not parts:
        return (None, None, None)
    if parts[0] == assets_folder:
        if len(parts) == 1:
            return (None, None, None)
        if len(parts) == 2:
            type_folder = parts[1]
            if type_registry.get_type_by_folder(type_folder) is not None:
                return (None, None, type_folder)
            return (None, None, None)
        if len(parts) >= 3:
            type_folder = parts[1]
            asset_name = parts[2]
            if type_registry.get_type_by_folder(type_folder) is not None:
                asset_dir = project_root / assets_folder / type_folder / asset_name
                return (str(asset_dir), None, None)
        return (None, None, None)
    if parts[0] == shots_folder:
        if len(parts) == 1:
            return (None, None, None)
        if len(parts) >= 2:
            shot_name = parts[1]
            shot_dir = project_root / shots_folder / shot_name
            return (None, str(shot_dir), None)
    return (None, None, None)


class FsEventCollector(QObject):
    """
    Collects raw filesystem paths, debounces, normalizes, and classifies
    affected scope (single asset, shot, type, unknown). Emits batchReady
    for incremental scan submission. Never performs heavy work in callbacks.
    """

    # Emits (asset_ids: list[str], shot_ids: list[str], type_folders: list[str])
    batchReady = Signal(object, object, object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    ) -> None:
        super().__init__(parent)
        self._debounce_ms = max(200, min(500, debounce_ms))
        self._pending_paths: set[str] = set()
        self._timer: QTimer | None = None
        self._project_root: Path | None = None
        self._type_registry: "TypeRegistry | None" = None
        self._department_registry: "DepartmentRegistry | None" = None

    def set_project_root(self, project_root: Path | str | None) -> None:
        """Set project root for path classification. Pass None to disable."""
        if project_root is None:
            self._project_root = None
            return
        try:
            self._project_root = Path(project_root).resolve()
        except OSError:
            self._project_root = None

    def set_registries(
        self,
        type_registry: "TypeRegistry | None" = None,
        department_registry: "DepartmentRegistry | None" = None,
    ) -> None:
        """Set registries for scope resolution. Required for classification."""
        self._type_registry = type_registry
        self._department_registry = department_registry

    def add_path(self, raw_path: str | Path) -> None:
        """
        Record a changed path. No processing here; just enqueue and (re)start debounce.
        Call from QFileSystemWatcher slots only.
        """
        if not raw_path:
            return
        normalized = _normalize_path(raw_path)
        if normalized is not None:
            self._pending_paths.add(str(normalized))
        _watcher_log.debug("watcher event received path=%s (pending=%d)", raw_path, len(self._pending_paths))
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._flush_batch)
        if not self._timer.isActive():
            self._timer.start(self._debounce_ms)

    def _flush_batch(self) -> None:
        paths = self._pending_paths
        self._pending_paths = set()
        if not paths:
            return
        asset_ids: set[str] = set()
        shot_ids: set[str] = set()
        type_folders: set[str] = set()
        project_root = self._project_root
        type_reg = self._type_registry
        if project_root is None or type_reg is None:
            logger.debug("FsEventCollector: no project root or type registry; skipping batch")
            return
        from monostudio.core.structure_registry import StructureRegistry
        struct_reg = StructureRegistry.for_project(project_root)
        _assets_f = struct_reg.get_folder("assets")
        _shots_f = struct_reg.get_folder("shots")
        for raw in paths:
            p = _normalize_path(raw)
            if p is None:
                continue
            aid, sid, tf = _classify_path(project_root, p, type_reg, _assets_f, _shots_f)
            if aid:
                asset_ids.add(aid)
            if sid:
                shot_ids.add(sid)
            if tf:
                type_folders.add(tf)
        if asset_ids or shot_ids or type_folders:
            a_list, s_list, t_list = list(asset_ids), list(shot_ids), list(type_folders)
            _watcher_log.debug("watcher batch ready paths=%d -> asset_ids=%d shot_ids=%d type_folders=%d", len(paths), len(a_list), len(s_list), len(t_list))
            self.batchReady.emit(a_list, s_list, t_list)
