"""
Project-level Department Mapping: logical department IDs ↔ physical folder names.

Source of truth: <project_root>/.monostudio/pipeline/departments.json (or custom path via monos.project.json).
The registry is read-only; mapping is edited only via Project Settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from monostudio.core.models import Asset, ProjectIndex, Shot


# Edit safety level for changing a department's folder mapping.
MappingEditLevel = Literal["FREE", "WARNING", "MIGRATION_REQUIRED"]

# Context for department folder: shot (under shots/) vs asset (under assets/).
# Same logical department (e.g. fx) can map to different folder names (e.g. 02_fx vs 06_fx).
DepartmentContext = Literal["shot", "asset"]

# Default pipeline config dir relative to project root (under .monostudio for consistency).
_DEFAULT_PIPELINE_SUBDIR = Path(".monostudio") / "pipeline"
_MONOS_PROJECT_JSON = "monos.project.json"
_DEPARTMENTS_JSON = "departments.json"

# Shipped default department mapping (monostudio_data/pipeline/department_presets/mono2026_preset.json).
_MONO2026_PRESET_PATH = Path(__file__).resolve().parents[2] / "monostudio_data" / "pipeline" / "department_presets" / "mono2026_preset.json"


def get_project_pipeline_dir(project_root: Path) -> Path:
    """
    Resolve the project's pipeline config directory.

    - Default: <project_root>/.monostudio/pipeline
    - If <project_root>/monos.project.json exists and has "pipeline_config_path",
      that path is used (relative to project_root, or absolute if path is absolute).
    """
    root = Path(project_root)
    config_file = root / _MONOS_PROJECT_JSON
    try:
        if config_file.is_file():
            data = json.loads(config_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = data.get("pipeline_config_path")
                if isinstance(raw, str) and raw.strip():
                    p = Path(raw.strip())
                    if p.is_absolute():
                        return p
                    return (root / p).resolve()
    except (OSError, json.JSONDecodeError):
        pass
    return root / _DEFAULT_PIPELINE_SUBDIR


def get_project_departments_path(project_root: Path) -> Path:
    """Path to the project's departments.json file (may not exist)."""
    return get_project_pipeline_dir(project_root) / _DEPARTMENTS_JSON


def _fallback_department_mapping() -> dict[str, dict]:
    """Fallback when mono2026_preset.json is missing (folder = id)."""
    defaults = [
        ("layout", "Layout", "layout"),
        ("model", "Modeling", "model"),
        ("rig", "Rigging", "rig"),
        ("surfacing", "Surfacing", "surfacing"),
        ("grooming", "Grooming", "grooming"),
        ("lookdev", "Lookdev", "lookdev"),
        ("anim", "Animation", "anim"),
        ("fx", "FX", "fx"),
        ("lighting", "Lighting", "lighting"),
        ("comp", "Comp", "comp"),
    ]
    return {
        dept_id: {"label": label, "folder": folder, "shot_folder": folder, "asset_folder": folder, "order": i}
        for i, (dept_id, label, folder) in enumerate(defaults, start=1)
    }


def _default_department_mapping() -> dict[str, dict]:
    """
    Default mapping when no project departments.json exists.
    Uses monostudio_data/pipeline/department_presets/mono2026_preset.json when present.
    """
    loaded = _load_departments_json(_MONO2026_PRESET_PATH)
    if loaded:
        return loaded
    return _fallback_department_mapping()


def get_default_department_mapping() -> dict[str, dict]:
    """
    Return the built-in default department mapping (same schema as get_raw_mapping).
    Used by Settings UI for "Reset to default".
    """
    return dict(_default_department_mapping())


def load_department_mapping_from_file(path: Path) -> dict[str, dict] | None:
    """
    Load department mapping from a JSON file (same format as project departments.json).
    Returns normalized mapping dict or None if file is missing/invalid.
    Used by Settings UI for "Load preset".
    """
    return _load_departments_json(Path(path))


def _load_departments_json(path: Path) -> dict[str, dict] | None:
    """Load and validate departments.json; return None if missing/invalid."""
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    depts = data.get("departments")
    if not isinstance(depts, dict) or not depts:
        return None
    out: dict[str, dict] = {}
    for dept_id, node in depts.items():
        if not isinstance(dept_id, str) or not dept_id.strip():
            continue
        if not isinstance(node, dict):
            continue
        folder = node.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            folder = dept_id.strip()
        shot_folder = node.get("shot_folder")
        if isinstance(shot_folder, str) and shot_folder.strip():
            shot_folder = shot_folder.strip()
        else:
            shot_folder = folder.strip()
        asset_folder = node.get("asset_folder")
        if isinstance(asset_folder, str) and asset_folder.strip():
            asset_folder = asset_folder.strip()
        else:
            asset_folder = folder.strip()
        label = node.get("label")
        if not isinstance(label, str) or not label.strip():
            label = dept_id
        order = node.get("order")
        if not isinstance(order, (int, float)):
            order = 999
        out[dept_id.strip()] = {
            "label": label.strip(),
            "folder": folder.strip(),
            "shot_folder": shot_folder,
            "asset_folder": asset_folder,
            "order": int(order),
        }
    return out if out else None


class DepartmentRegistry:
    """
    Project-level department mapping: logical ID ↔ physical folder name.

    - Loads from project's pipeline/departments.json (or default when missing).
    - Does not rename folders or migrate data.
    """

    def __init__(self, mapping: dict[str, dict], source_path: Path | None) -> None:
        """
        mapping: dept_id -> { "label", "folder", "shot_folder", "asset_folder", "order" }
        source_path: path to departments.json, or None if using default in-memory mapping.
        """
        self._mapping = dict(mapping)
        self._source_path = Path(source_path) if source_path else None
        # Index folder -> dept_id per context (first wins if duplicate folders)
        self._shot_folder_to_id: dict[str, str] = {}
        self._asset_folder_to_id: dict[str, str] = {}
        for dept_id, node in sorted(self._mapping.items(), key=lambda kv: kv[1].get("order", 999)):
            shot_f = (node.get("shot_folder") or node.get("folder") or "").strip()
            asset_f = (node.get("asset_folder") or node.get("folder") or "").strip()
            if shot_f and shot_f not in self._shot_folder_to_id:
                self._shot_folder_to_id[shot_f] = dept_id
            if asset_f and asset_f not in self._asset_folder_to_id:
                self._asset_folder_to_id[asset_f] = dept_id

    @classmethod
    def for_project(cls, project_root: Path) -> "DepartmentRegistry":
        """
        Load registry for the given project.
        If departments.json is missing, returns default in-memory mapping (folder = id).
        """
        path = get_project_departments_path(project_root)
        raw = _load_departments_json(path)
        if raw is not None:
            return cls(raw, path)
        return cls(_default_department_mapping(), None)

    # ---------- Public API ----------

    def get_departments(self) -> list[str]:
        """Logical department IDs in order."""
        return sorted(
            self._mapping.keys(),
            key=lambda d: (self._mapping[d].get("order", 999), d),
        )

    def get_department_label(self, dept_id: str) -> str:
        """UI label for the department; falls back to dept_id if unknown."""
        node = self._mapping.get((dept_id or "").strip())
        if node and isinstance(node.get("label"), str):
            return node["label"].strip()
        return (dept_id or "").strip() or ""

    def get_department_folder(self, dept_id: str, context: DepartmentContext = "shot") -> str:
        """Physical folder name for the department in the given context (shot or asset)."""
        node = self._mapping.get((dept_id or "").strip())
        if not node:
            return (dept_id or "").strip() or ""
        if context == "shot":
            folder = (node.get("shot_folder") or node.get("folder") or "").strip()
        else:
            folder = (node.get("asset_folder") or node.get("folder") or "").strip()
        return folder or (dept_id or "").strip() or ""

    def get_department_by_folder(self, folder_name: str, context: DepartmentContext) -> str | None:
        """Resolve physical folder name to logical department ID in the given context (shot or asset)."""
        key = (folder_name or "").strip()
        if context == "shot":
            return self._shot_folder_to_id.get(key)
        return self._asset_folder_to_id.get(key)

    def get_mapping_edit_level(
        self,
        project_root: Path,
        dept_id: str,
        project_index: ProjectIndex | None = None,
    ) -> MappingEditLevel:
        """
        Classify how safe it is to edit this department's folder mapping.
        Checks both shot and asset folder names for this department; returns worst level.
        """
        from monostudio.core.fs_reader import build_project_index

        idx = project_index if project_index is not None else build_project_index(project_root, self)
        has_items = bool(idx.assets or idx.shots)
        if not has_items:
            return "FREE"

        shot_folder = self.get_department_folder(dept_id, "shot")
        asset_folder = self.get_department_folder(dept_id, "asset")
        level_shot = _edit_level_for_folder(idx, shot_folder)
        level_asset = _edit_level_for_folder(idx, asset_folder)
        if level_shot == "MIGRATION_REQUIRED" or level_asset == "MIGRATION_REQUIRED":
            return "MIGRATION_REQUIRED"
        if level_shot == "WARNING" or level_asset == "WARNING":
            return "WARNING"
        return "FREE"

    def get_raw_mapping(self) -> dict[str, dict]:
        """Return a copy of the raw mapping (for UI editing / save)."""
        return {k: dict(v) for k, v in self._mapping.items()}

    @property
    def source_path(self) -> Path | None:
        """Path to departments.json, or None if default in-memory."""
        return self._source_path


def _edit_level_for_folder(index: ProjectIndex, folder_name: str) -> MappingEditLevel:
    """Return FREE / WARNING / MIGRATION_REQUIRED for a single folder name."""
    if not (folder_name or "").strip():
        return "FREE"
    folder_exists, has_files = _department_folder_usage(index, folder_name)
    if not folder_exists:
        return "FREE"
    if has_files:
        return "MIGRATION_REQUIRED"
    return "WARNING"


def _department_folder_usage(index: ProjectIndex, folder_name: str) -> tuple[bool, bool]:
    """
    Check usage of a physical department folder across the project.

    Returns (folder_exists_anywhere, files_exist_anywhere).
    """
    folder_exists = False
    files_exist = False

    def check_item_departments(departments: tuple) -> None:
        nonlocal folder_exists, files_exist
        for d in departments:
            if d.path.name != folder_name:
                continue
            folder_exists = True
            work_path = d.work_path
            publish_path = d.publish_path
            if _dir_has_files(work_path) or _dir_has_files(publish_path):
                files_exist = True
                return

    for a in index.assets:
        check_item_departments(a.departments)
        if files_exist:
            return True, True
    for s in index.shots:
        check_item_departments(s.departments)
        if files_exist:
            return True, True

    return folder_exists, files_exist


def _dir_has_files(path: Path) -> bool:
    """True if directory exists and contains any file or subdir (excluding .)."""
    try:
        if not path.is_dir():
            return False
        for _ in path.iterdir():
            return True
        return False
    except OSError:
        return False


def _build_departments_payload(mapping: dict[str, dict]) -> dict[str, dict]:
    """Build departments.json payload from mapping."""
    payload: dict[str, dict] = {}
    for dept_id, node in mapping.items():
        if not isinstance(dept_id, str) or not dept_id.strip():
            continue
        if not isinstance(node, dict):
            continue
        folder = node.get("folder")
        if not isinstance(folder, str) or not folder.strip():
            folder = dept_id.strip()
        shot_folder = node.get("shot_folder")
        if not isinstance(shot_folder, str) or not shot_folder.strip():
            shot_folder = folder.strip()
        asset_folder = node.get("asset_folder")
        if not isinstance(asset_folder, str) or not asset_folder.strip():
            asset_folder = folder.strip()
        label = node.get("label")
        if not isinstance(label, str) or not label.strip():
            label = dept_id
        order = node.get("order")
        if not isinstance(order, (int, float)):
            order = 999
        payload[dept_id.strip()] = {
            "label": label.strip(),
            "folder": folder.strip(),
            "shot_folder": shot_folder.strip(),
            "asset_folder": asset_folder.strip(),
            "order": int(order),
        }
    return payload


def write_departments_to_path(path: Path, mapping: dict[str, dict]) -> bool:
    """
    Write departments.json to the given path (e.g. user default).
    Creates parent dirs if needed. Returns False if payload empty or write fails.
    """
    payload = _build_departments_payload(mapping)
    if not payload:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"departments": payload}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def save_project_departments(project_root: Path, mapping: dict[str, dict]) -> bool:
    """
    Write project's departments.json from a mapping dict.

    mapping: dept_id -> { "label", "folder", "shot_folder", "asset_folder", "order" }.
    shot_folder/asset_folder can differ (e.g. shot 02_fx, asset 06_fx). Creates pipeline dir if needed.
    """
    path = get_project_pipeline_dir(project_root) / _DEPARTMENTS_JSON
    return write_departments_to_path(path, mapping)
