"""
Project-level Type Mapping: logical asset type IDs ↔ physical folder names.

Source of truth: <project_root>/.monostudio/pipeline/types.json (same pipeline dir as departments.json).
Lightweight: no migration logic, no edit-level classification.
"""

from __future__ import annotations

import json
from pathlib import Path

from monostudio.core.department_registry import get_project_pipeline_dir
from monostudio.core.pipeline_types_and_presets import load_pipeline_types_and_presets


_TYPES_JSON = "types.json"


def get_project_types_path(project_root: Path) -> Path:
    """Path to the project's types.json file (may not exist)."""
    return get_project_pipeline_dir(project_root) / _TYPES_JSON


def get_default_type_mapping() -> dict[str, dict]:
    """Shallow copy of built-in / shipped-derived type mapping (no project). For UI reset-to-factory."""
    return dict(_default_type_mapping())


def _default_type_mapping() -> dict[str, dict]:
    """
    Default mapping when no project types.json exists.
    Derive from pipeline types_and_presets so type_ids match (e.g. _characters, _props).
    """
    try:
        config = load_pipeline_types_and_presets()
        out: dict[str, dict] = {}
        for tid, t in config.types.items():
            if tid == "shot" or tid.startswith("shot_"):
                continue
            folder = tid  # use type_id as folder so assets/_characters, etc.
            out[tid] = {"label": t.name, "folder": folder}
        if out:
            return out
    except Exception:
        pass
    return {
        "character": {"label": "Character", "folder": "character"},
        "prop": {"label": "Prop", "folder": "prop"},
        "environment": {"label": "Environment", "folder": "environment"},
    }


def _load_types_json(path: Path) -> dict[str, dict]:
    """
    Load and validate types.json.
    Raises RuntimeError on invalid config (malformed, missing required fields, duplicate folders, non-lowercase id).
    Returns empty dict if file is missing (caller may substitute default).
    """
    if not path.is_file():
        return {}
    try:
        data = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Could not read types config: {path!s}") from e
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in types config: {path!s}") from e
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Types config must be a JSON object: {path!s}")
    types_node = parsed.get("types")
    if not isinstance(types_node, dict):
        return {}
    if not types_node:
        return {}

    out: dict[str, dict] = {}
    seen_folders: dict[str, str] = {}  # folder -> first type_id that used it
    for type_id, node in types_node.items():
        if not isinstance(type_id, str) or not type_id.strip():
            raise RuntimeError(f"Types config: type id must be a non-empty string: {type_id!r} ({path!s})")
        tid = type_id.strip()
        if tid != tid.lower():
            raise RuntimeError(f"Types config: type id must be lowercase: {tid!r} ({path!s})")
        if not isinstance(node, dict):
            raise RuntimeError(f"Types config: entry for {tid!r} must be an object ({path!s})")
        label = node.get("label")
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError(f"Types config: {tid!r} must define 'label' (non-empty string) ({path!s})")
        folder = node.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            raise RuntimeError(f"Types config: {tid!r} must define 'folder' (non-empty string) ({path!s})")
        folder = folder.strip()
        if folder in seen_folders and seen_folders[folder] != tid:
            raise RuntimeError(
                f"Types config: folder {folder!r} is used by both {seen_folders[folder]!r} and {tid!r} ({path!s})"
            )
        seen_folders[folder] = tid
        out[tid] = {"label": label.strip(), "folder": folder}
    return out


class TypeRegistry:
    """
    Project-level type mapping: logical type ID ↔ physical folder name.

    Lightweight: load, validate, resolve. No migration, no edit-level logic.
    """

    def __init__(self, mapping: dict[str, dict], source_path: Path | None) -> None:
        """
        mapping: type_id -> { "label", "folder" }
        source_path: path to types.json, or None if using default in-memory mapping.
        """
        self._mapping = dict(mapping)
        self._source_path = Path(source_path) if source_path else None
        self._folder_to_id: dict[str, str] = {}
        for type_id, node in self._mapping.items():
            folder = (node.get("folder") or "").strip()
            if folder:
                self._folder_to_id[folder] = type_id

    @classmethod
    def for_project(cls, project_root: Path) -> "TypeRegistry":
        """
        Load registry for the given project.
        If types.json is missing, returns default in-memory mapping.
        Raises RuntimeError if types.json exists but is invalid.
        """
        path = get_project_types_path(project_root)
        raw = _load_types_json(path)
        if raw:
            return cls(raw, path)
        return cls(_default_type_mapping(), None)

    def get_types(self) -> list[str]:
        """Logical type IDs in stable order (sorted)."""
        return sorted(self._mapping.keys())

    def get_type_label(self, type_id: str) -> str:
        """UI label for the type; falls back to type_id if unknown."""
        node = self._mapping.get((type_id or "").strip())
        if node and isinstance(node.get("label"), str):
            return node["label"].strip()
        return (type_id or "").strip() or ""

    def get_type_folder(self, type_id: str) -> str:
        """Physical folder name for the type; falls back to type_id if unknown."""
        node = self._mapping.get((type_id or "").strip())
        if node and isinstance(node.get("folder"), str):
            return node["folder"].strip()
        return (type_id or "").strip() or ""

    def get_type_by_folder(self, folder_name: str) -> str | None:
        """Resolve physical folder name to logical type ID, or None."""
        key = (folder_name or "").strip()
        return self._folder_to_id.get(key)

    def get_mapping_edit_level(self, project_root: Path, type_id: str) -> str:
        """
        Classify how safe it is to edit this type's folder mapping.
        Returns FREE / WARNING / MIGRATION_REQUIRED.
        """
        folder = self.get_type_folder(type_id)
        if not folder:
            return "FREE"
        from monostudio.core.structure_registry import StructureRegistry
        struct_reg = StructureRegistry.for_project(project_root)
        assets_dir = project_root / struct_reg.get_folder("assets")
        type_dir = assets_dir / folder
        if not type_dir.is_dir():
            return "FREE"
        try:
            has_children = any(p.is_dir() for p in type_dir.iterdir() if not p.name.startswith("."))
        except OSError:
            return "FREE"
        if not has_children:
            return "WARNING"
        try:
            for child in type_dir.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if any(True for _ in child.iterdir()):
                    return "MIGRATION_REQUIRED"
        except OSError:
            pass
        return "WARNING"

    def get_raw_mapping(self) -> dict[str, dict]:
        """Copy of raw mapping for UI editing / save."""
        return {k: dict(v) for k, v in self._mapping.items()}

    @property
    def source_path(self) -> Path | None:
        """Path to types.json, or None if default in-memory."""
        return self._source_path


def _build_types_payload(mapping: dict[str, dict]) -> dict[str, dict]:
    """Build types.json payload from mapping (type_id -> { label, folder })."""
    payload: dict[str, dict] = {}
    for type_id, node in mapping.items():
        if not isinstance(type_id, str) or not type_id.strip():
            continue
        if not isinstance(node, dict):
            continue
        folder = node.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            continue
        label = node.get("label")
        if not isinstance(label, str) or not label.strip():
            label = type_id
        payload[type_id.strip()] = {"label": label.strip(), "folder": folder.strip()}
    return payload


def write_types_to_path(path: Path, mapping: dict[str, dict]) -> bool:
    """
    Write types.json to the given path (e.g. user default).
    Uses atomic write (temp -> flush -> fsync -> rename). Returns False if payload empty or write fails.
    """
    from monostudio.core.atomic_write import atomic_write_text

    payload = _build_types_payload(mapping)
    if not payload:
        return False
    try:
        content = json.dumps({"types": payload}, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
        return True
    except OSError:
        return False


def save_project_types(project_root: Path, mapping: dict[str, dict]) -> bool:
    """
    Write project's types.json from a mapping dict.

    mapping: type_id -> { "label", "folder" } (same schema as file).
    Creates pipeline dir if needed. Does not validate (caller must ensure valid mapping).
    """
    path = get_project_pipeline_dir(project_root) / _TYPES_JSON
    return write_types_to_path(path, mapping)
