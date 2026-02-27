"""
Project-level Structure Mapping: logical top-level folder IDs ↔ physical folder names.

Covers the five root-level project folders: assets, shots, inbox, outbox, project_guide.
Source of truth: <project_root>/.monostudio/pipeline/structure.json.
Lightweight: no migration logic, no rename-on-disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from monostudio.core.department_registry import get_project_pipeline_dir

_STRUCTURE_JSON = "structure.json"

_ALL_FOLDER_IDS = ("assets", "shots", "inbox", "outbox", "project_guide")

_DEFAULT_MAPPING: dict[str, dict[str, str]] = {
    "assets":        {"label": "Assets",        "folder": "assets"},
    "shots":         {"label": "Shots",         "folder": "shots"},
    "inbox":         {"label": "Inbox",         "folder": "inbox"},
    "outbox":        {"label": "Outbox",        "folder": "outbox"},
    "project_guide": {"label": "Project Guide", "folder": "project_guide"},
}


def get_project_structure_path(project_root: Path) -> Path:
    return get_project_pipeline_dir(project_root) / _STRUCTURE_JSON


def _load_structure_json(path: Path) -> dict[str, dict[str, str]]:
    """
    Load and validate structure.json.
    Returns empty dict if file missing (caller substitutes default).
    Raises RuntimeError on malformed config.
    """
    if not path.is_file():
        return {}
    try:
        data = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Could not read structure config: {path!s}") from e
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in structure config: {path!s}") from e
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Structure config must be a JSON object: {path!s}")
    folders_node = parsed.get("folders")
    if not isinstance(folders_node, dict) or not folders_node:
        return {}

    out: dict[str, dict[str, str]] = {}
    seen_folders: dict[str, str] = {}
    for fid, node in folders_node.items():
        if not isinstance(fid, str) or not fid.strip():
            raise RuntimeError(f"Structure config: id must be a non-empty string: {fid!r} ({path!s})")
        fid = fid.strip()
        if fid not in _ALL_FOLDER_IDS:
            continue
        if not isinstance(node, dict):
            raise RuntimeError(f"Structure config: entry for {fid!r} must be an object ({path!s})")
        label = node.get("label")
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError(f"Structure config: {fid!r} must define 'label' (non-empty string) ({path!s})")
        folder = node.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            raise RuntimeError(f"Structure config: {fid!r} must define 'folder' (non-empty string) ({path!s})")
        folder = folder.strip()
        if folder in seen_folders and seen_folders[folder] != fid:
            raise RuntimeError(
                f"Structure config: folder {folder!r} used by both {seen_folders[folder]!r} and {fid!r} ({path!s})"
            )
        seen_folders[folder] = fid
        out[fid] = {"label": label.strip(), "folder": folder}
    return out


class StructureRegistry:
    """
    Project-level structure mapping: logical folder ID ↔ physical folder name.

    IDs: assets, shots, inbox, outbox, project_guide.
    """

    def __init__(self, mapping: dict[str, dict[str, str]], source_path: Path | None) -> None:
        self._mapping: dict[str, dict[str, str]] = {}
        for fid in _ALL_FOLDER_IDS:
            self._mapping[fid] = dict(mapping.get(fid) or _DEFAULT_MAPPING[fid])
        self._source_path = Path(source_path) if source_path else None

    @classmethod
    def for_project(cls, project_root: Path) -> StructureRegistry:
        path = get_project_structure_path(project_root)
        raw = _load_structure_json(path)
        if raw:
            merged = dict(_DEFAULT_MAPPING)
            for fid, node in raw.items():
                merged[fid] = node
            return cls(merged, path)
        return cls(dict(_DEFAULT_MAPPING), None)

    def get_folder(self, logical_id: str) -> str:
        """Physical folder name for the logical ID. Falls back to default."""
        node = self._mapping.get(logical_id)
        if node and isinstance(node.get("folder"), str):
            return node["folder"]
        default = _DEFAULT_MAPPING.get(logical_id)
        return default["folder"] if default else logical_id

    def get_label(self, logical_id: str) -> str:
        node = self._mapping.get(logical_id)
        if node and isinstance(node.get("label"), str):
            return node["label"]
        default = _DEFAULT_MAPPING.get(logical_id)
        return default["label"] if default else logical_id

    def get_ids(self) -> list[str]:
        return list(_ALL_FOLDER_IDS)

    def get_mapping_edit_level(self, project_root: Path, logical_id: str) -> str:
        """
        Classify how safe it is to edit this folder mapping.
        Returns FREE / WARNING / MIGRATION_REQUIRED.
        """
        folder = self.get_folder(logical_id)
        if not folder:
            return "FREE"
        target = project_root / folder
        if not target.is_dir():
            return "FREE"
        try:
            has_content = any(True for p in target.iterdir() if not p.name.startswith("."))
        except OSError:
            return "FREE"
        return "MIGRATION_REQUIRED" if has_content else "WARNING"

    def get_raw_mapping(self) -> dict[str, dict[str, str]]:
        return {k: dict(v) for k, v in self._mapping.items()}

    @property
    def source_path(self) -> Path | None:
        return self._source_path


def write_structure_to_path(path: Path, mapping: dict[str, dict[str, str]]) -> bool:
    """
    Write structure.json to the given path (e.g. user default).
    Uses atomic write. Returns False if payload empty or write fails.
    """
    from monostudio.core.atomic_write import atomic_write_text

    payload: dict[str, dict[str, str]] = {}
    for fid, node in mapping.items():
        if fid not in _ALL_FOLDER_IDS:
            continue
        if not isinstance(node, dict):
            continue
        folder = (node.get("folder") or "").strip()
        label = (node.get("label") or "").strip() or fid
        if not folder:
            continue
        payload[fid] = {"label": label, "folder": folder}
    if not payload:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"folders": payload}, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
        return True
    except OSError:
        return False


def save_project_structure(project_root: Path, mapping: dict[str, dict[str, str]]) -> bool:
    """
    Write project's structure.json from a mapping dict.
    Creates pipeline dir if needed.
    """
    from monostudio.core.atomic_write import atomic_write_text

    payload: dict[str, dict[str, str]] = {}
    for fid, node in mapping.items():
        if fid not in _ALL_FOLDER_IDS:
            continue
        if not isinstance(node, dict):
            continue
        folder = (node.get("folder") or "").strip()
        label = (node.get("label") or "").strip() or fid
        if not folder:
            continue
        payload[fid] = {"label": label, "folder": folder}
    if not payload:
        return False
    try:
        path = get_project_pipeline_dir(project_root) / _STRUCTURE_JSON
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"folders": payload}, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
        return True
    except OSError:
        return False
