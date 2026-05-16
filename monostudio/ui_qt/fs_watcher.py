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


_META_WATCH_CAP_PER_ENTITY = 64
_MONO_WATCH_CAP_PER_ENTITY = 48
_SPECIAL_FOLDER_WATCH_CAP_PER_ENTITY = 48


def append_entity_meta_watch_paths(
    entity_base: Path,
    to_add: list[str],
    seen: set[str],
    *,
    max_paths: int,
    per_entity_cap: int = _META_WATCH_CAP_PER_ENTITY,
) -> None:
    """Add ``<entity>/.meta`` plus every file and subdir inside (QFileSystemWatcher is not recursive)."""
    try:
        meta = (Path(entity_base) / ".meta").resolve()
    except OSError:
        return
    if not meta.is_dir():
        return

    budget = per_entity_cap

    def try_add(path: Path) -> None:
        nonlocal budget
        if budget <= 0 or len(to_add) >= max_paths:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not resolved.exists():
            return
        s = str(resolved)
        if s in seen:
            return
        seen.add(s)
        to_add.append(s)
        budget -= 1

    try_add(meta)
    try:
        for child in sorted(meta.rglob("*")):
            if budget <= 0 or len(to_add) >= max_paths:
                break
            try_add(child)
    except OSError:
        pass


def append_entity_monostudio_watch_paths(
    entity_base: Path,
    to_add: list[str],
    seen: set[str],
    *,
    max_paths: int,
    per_entity_cap: int = _MONO_WATCH_CAP_PER_ENTITY,
) -> None:
    """Add ``<entity>/.monostudio`` plus files/subdirs (for ``item_comments.json`` and peers)."""
    try:
        mono = (Path(entity_base) / ".monostudio").resolve()
    except OSError:
        return
    if not mono.exists():
        return
    budget = per_entity_cap

    def try_add(path: Path) -> None:
        nonlocal budget
        if budget <= 0 or len(to_add) >= max_paths:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not resolved.exists():
            return
        s = str(resolved)
        if s in seen:
            return
        seen.add(s)
        to_add.append(s)
        budget -= 1

    if mono.is_dir():
        try_add(mono)
        try:
            for child in sorted(mono.rglob("*")):
                if budget <= 0 or len(to_add) >= max_paths:
                    break
                try_add(child)
        except OSError:
            pass
    elif mono.is_file():
        try_add(mono)


def append_entity_special_folder_watch_paths(
    entity_base: Path,
    to_add: list[str],
    seen: set[str],
    *,
    max_paths: int,
    per_entity_cap: int = _SPECIAL_FOLDER_WATCH_CAP_PER_ENTITY,
) -> None:
    """Add ``<entity>/reference`` and ``<entity>/concept`` plus top-level entries (non-recursive)."""
    try:
        base = Path(entity_base).resolve()
    except OSError:
        return
    budget = per_entity_cap

    def try_add(path: Path) -> None:
        nonlocal budget
        if budget <= 0 or len(to_add) >= max_paths:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not resolved.exists():
            return
        s = str(resolved)
        if s in seen:
            return
        seen.add(s)
        to_add.append(s)
        budget -= 1

    for folder_name in ("reference", "concept"):
        folder = base / folder_name
        if not folder.exists():
            continue
        try_add(folder)
        if not folder.is_dir():
            continue
        try:
            for child in sorted(folder.iterdir()):
                if budget <= 0 or len(to_add) >= max_paths:
                    break
                try_add(child)
        except OSError:
            pass


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


def _path_under_entity_meta(path: Path) -> bool:
    return ".meta" in path.parts


def _entity_path_if_special_folder_touch(
    project_root: Path,
    path: Path,
    type_registry: "TypeRegistry",
    assets_folder: str,
    shots_folder: str,
) -> str | None:
    """Entity root path when ``path`` is under ``<entity>/reference`` or ``<entity>/concept``."""
    aid, sid, _ = _classify_path(project_root, path, type_registry, assets_folder, shots_folder)
    entity_path = aid or sid
    if not entity_path:
        return None
    try:
        rel = path.resolve().relative_to(Path(entity_path).resolve())
    except (OSError, ValueError):
        return None
    if not rel.parts:
        return entity_path
    if rel.parts[0] in ("reference", "concept"):
        return entity_path
    return None


def _path_triggers_item_notes_refresh(path: Path) -> bool:
    if ".monostudio" not in path.parts:
        return False
    n = path.name
    return n == "item_comments.json" or n == ".monostudio"


def _department_from_meta_thumb_filename(name: str) -> str | None:
    if not name.startswith("thumb_"):
        return None
    rest = name[6:]
    if ".user." in rest:
        dep = rest.split(".user.", 1)[0].strip()
        return dep or None
    for ext in (".png", ".jpg", ".jpeg"):
        if rest.lower().endswith(ext):
            dep = rest[: -len(ext)].strip()
            return dep or None
    return None


class FsEventCollector(QObject):
    """
    Collects raw filesystem paths, debounces, normalizes, and classifies
    affected scope (single asset, shot, type, unknown). Emits batchReady with optional
    ``rescan_assets_listing`` / ``rescan_shots_listing`` when ``assets/`` or ``shots/`` root fires.
    for incremental scan submission. Never performs heavy work in callbacks.
    """

    # Emits (asset_ids, shot_ids, type_folders, rescan_assets_listing, rescan_shots_listing)
    batchReady = Signal(object, object, object, bool, bool)
    # Emits list[tuple[entity_path, department_or_None]] — department None => all dept thumbs for entity
    metaThumbnailsStale = Signal(object)
    # Emits list[str] — absolute entity root paths (asset/shot) whose ``.monostudio`` data changed
    itemNotesStale = Signal(object)
    # Emits list[str] — entity roots whose ``reference/`` or ``concept/`` tree changed
    entitySpecialFoldersStale = Signal(object)

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
        # entity_path -> department (None = invalidate every dept thumb for that entity)
        meta_thumb_stale: dict[str, str | None] = {}
        notes_entities: set[str] = set()
        special_folder_entities: set[str] = set()
        project_root = self._project_root
        type_reg = self._type_registry
        if project_root is None or type_reg is None:
            logger.debug("FsEventCollector: no project root or type registry; skipping batch")
            return
        from monostudio.core.structure_registry import StructureRegistry
        struct_reg = StructureRegistry.for_project(project_root)
        _assets_f = struct_reg.get_folder("assets")
        _shots_f = struct_reg.get_folder("shots")
        try:
            assets_root = (project_root / _assets_f).resolve()
        except OSError:
            assets_root = project_root / _assets_f
        try:
            shots_root = (project_root / _shots_f).resolve()
        except OSError:
            shots_root = project_root / _shots_f
        rescan_assets_listing = False
        rescan_shots_listing = False
        for raw in paths:
            p = _normalize_path(raw)
            if p is None:
                continue
            if p == assets_root:
                rescan_assets_listing = True
            if p == shots_root:
                rescan_shots_listing = True
            aid, sid, tf = _classify_path(project_root, p, type_reg, _assets_f, _shots_f)
            if aid:
                asset_ids.add(aid)
            if sid:
                shot_ids.add(sid)
            if tf:
                type_folders.add(tf)
            if _path_triggers_item_notes_refresh(p):
                ent = aid or sid
                if ent:
                    notes_entities.add(ent)
            ent_special = _entity_path_if_special_folder_touch(
                project_root, p, type_reg, _assets_f, _shots_f
            )
            if ent_special:
                special_folder_entities.add(ent_special)
            if not _path_under_entity_meta(p):
                continue
            entity = aid or sid
            if not entity:
                continue
            dept: str | None = _department_from_meta_thumb_filename(p.name) if p.is_file() else None
            if entity in meta_thumb_stale and meta_thumb_stale[entity] is None:
                continue
            if dept is None:
                meta_thumb_stale[entity] = None
            elif entity not in meta_thumb_stale:
                meta_thumb_stale[entity] = dept
            elif meta_thumb_stale[entity] != dept:
                meta_thumb_stale[entity] = None
        if asset_ids or shot_ids or type_folders or rescan_assets_listing or rescan_shots_listing:
            a_list, s_list, t_list = list(asset_ids), list(shot_ids), list(type_folders)
            _watcher_log.debug(
                "watcher batch ready paths=%d -> asset_ids=%d shot_ids=%d type_folders=%d rescan_assets=%s rescan_shots=%s",
                len(paths),
                len(a_list),
                len(s_list),
                len(t_list),
                rescan_assets_listing,
                rescan_shots_listing,
            )
            self.batchReady.emit(a_list, s_list, t_list, rescan_assets_listing, rescan_shots_listing)
        if meta_thumb_stale:
            stale = [(ep, meta_thumb_stale[ep]) for ep in meta_thumb_stale]
            _watcher_log.debug("watcher meta thumbnails stale count=%d", len(stale))
            self.metaThumbnailsStale.emit(stale)
        if notes_entities:
            n_list = list(notes_entities)
            _watcher_log.debug("watcher item notes stale entities=%d", len(n_list))
            self.itemNotesStale.emit(n_list)
        if special_folder_entities:
            s_list = list(special_folder_entities)
            _watcher_log.debug("watcher entity special folders stale count=%d", len(s_list))
            self.entitySpecialFoldersStale.emit(s_list)
