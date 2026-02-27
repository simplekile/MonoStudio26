"""
Project-level Department Mapping: logical department IDs ↔ physical folder names.

Source of truth: <project_root>/.monostudio/pipeline/departments.json (or custom path via monos.project.json).
The registry is read-only; mapping is edited only via Project Settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from monostudio.core.app_paths import get_app_base_path
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
def _mono2026_preset_path() -> Path:
    return get_app_base_path() / "monostudio_data" / "pipeline" / "department_presets" / "mono2026_preset.json"


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
    loaded = _load_departments_json(_mono2026_preset_path())
    if loaded:
        return loaded
    return _fallback_department_mapping()


def get_default_department_mapping() -> dict[str, dict]:
    """
    Return the built-in default department mapping (same schema as get_raw_mapping).
    Used by Settings UI for "Reset to default".
    """
    return dict(_default_department_mapping())


def ensure_parent_from_preset(
    mapping: dict[str, dict],
    preset_path: Path | None = None,
) -> dict[str, dict]:
    """
    Return a copy of mapping with "parent" from preset merged in for known subdepartments.
    Use when saving to user default so the default always has nested layout (subdepartments under parent folder).
    """
    out = {k: dict(v) for k, v in mapping.items()}
    if preset_path is None:
        preset_path = get_app_base_path() / "monostudio_data" / "pipeline" / "department_presets" / "mono2026_preset.json"
    try:
        if not preset_path.is_file():
            return out
        data = json.loads(preset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    preset_depts = data.get("departments") if isinstance(data, dict) else None
    if not isinstance(preset_depts, dict):
        return out
    for dept_id, preset_node in preset_depts.items():
        if not isinstance(preset_node, dict):
            continue
        parent_val = preset_node.get("parent")
        if isinstance(parent_val, str) and parent_val.strip() and dept_id in out:
            out[dept_id] = {**out[dept_id], "parent": parent_val.strip()}
    return out


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
        node_out: dict = {
            "label": label.strip(),
            "folder": folder.strip(),
            "shot_folder": shot_folder,
            "asset_folder": asset_folder,
            "order": int(order),
        }
        parent_val = node.get("parent")
        if isinstance(parent_val, str) and parent_val.strip():
            node_out["parent"] = parent_val.strip()
        out[dept_id.strip()] = node_out
    return out if out else None


def _compute_relative_paths(mapping: dict[str, dict]) -> tuple[dict[str, str], dict[str, str]]:
    """
    Compute full relative path per dept_id for shot and asset (nested: parent/child on disk).
    Returns (shot_rel: dept_id -> path, asset_rel: dept_id -> path).
    """
    shot_rel: dict[str, str] = {}
    asset_rel: dict[str, str] = {}

    def build_paths(context_key: str) -> None:
        rel = shot_rel if context_key == "shot" else asset_rel
        key = "shot_folder" if context_key == "shot" else "asset_folder"
        ordered: list[str] = []
        seen: set[str] = set()
        while len(ordered) < len(mapping):
            added = False
            for dept_id, node in mapping.items():
                if dept_id in seen:
                    continue
                parent = node.get("parent")
                if not parent or parent not in mapping or parent in seen:
                    ordered.append(dept_id)
                    seen.add(dept_id)
                    added = True
            if not added:
                break
        for dept_id in ordered:
            node = mapping[dept_id]
            folder = (node.get(key) or node.get("folder") or dept_id or "").strip()
            parent = node.get("parent")
            if not parent or parent not in mapping:
                rel[dept_id] = folder
            else:
                parent_path = rel.get(parent) or ""
                rel[dept_id] = f"{parent_path}/{folder}" if parent_path else folder

    build_paths("shot")
    build_paths("asset")
    return shot_rel, asset_rel


class DepartmentRegistry:
    """
    Project-level department mapping: logical ID ↔ physical folder name.

    - Loads from project's pipeline/departments.json (or default when missing).
    - Does not rename folders or migrate data.
    """

    def __init__(self, mapping: dict[str, dict], source_path: Path | None) -> None:
        """
        mapping: dept_id -> { "label", "folder", "shot_folder", "asset_folder", "order", optional "parent" }
        source_path: path to departments.json, or None if using default in-memory mapping.
        Cấu trúc nested: khi có "parent", folder trên disk là relative path (vd 01_modelling/01_sculpt).
        """
        self._mapping = dict(mapping)
        self._source_path = Path(source_path) if source_path else None
        self._shot_folder_to_id: dict[str, str] = {}
        self._asset_folder_to_id: dict[str, str] = {}
        self._shot_relative_path: dict[str, str] = {}
        self._asset_relative_path: dict[str, str] = {}
        self._shot_relative_path_to_id: dict[str, str] = {}
        self._asset_relative_path_to_id: dict[str, str] = {}

        for dept_id, node in sorted(self._mapping.items(), key=lambda kv: kv[1].get("order", 999)):
            shot_f = (node.get("shot_folder") or node.get("folder") or "").strip()
            asset_f = (node.get("asset_folder") or node.get("folder") or "").strip()
            if shot_f and shot_f not in self._shot_folder_to_id:
                self._shot_folder_to_id[shot_f] = dept_id
            if asset_f and asset_f not in self._asset_folder_to_id:
                self._asset_folder_to_id[asset_f] = dept_id

        has_parent = any(
            isinstance(n.get("parent"), str) and (n.get("parent") or "").strip()
            for n in self._mapping.values()
        )
        if has_parent:
            self._shot_relative_path, self._asset_relative_path = _compute_relative_paths(self._mapping)
            for d, p in self._shot_relative_path.items():
                if p and p not in self._shot_relative_path_to_id:
                    self._shot_relative_path_to_id[p] = d
            for d, p in self._asset_relative_path.items():
                if p and p not in self._asset_relative_path_to_id:
                    self._asset_relative_path_to_id[p] = d

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

    def is_subdepartment(self, dept_id: str) -> bool:
        """True if this department has a parent (is a subdepartment / leaf task)."""
        node = self._mapping.get((dept_id or "").strip())
        if not node:
            return False
        p = node.get("parent")
        return isinstance(p, str) and bool((p or "").strip())

    def get_parent(self, dept_id: str) -> str | None:
        """Parent department ID if this is a subdepartment; None otherwise."""
        node = self._mapping.get((dept_id or "").strip())
        if not node:
            return None
        p = node.get("parent")
        return (p or "").strip() or None if isinstance(p, str) else None

    def get_department_folder(self, dept_id: str, context: DepartmentContext = "shot") -> str:
        """Physical folder (single segment or relative path when nested)."""
        return self.get_department_relative_path(dept_id, context)

    def get_department_relative_path(self, dept_id: str, context: DepartmentContext = "shot") -> str:
        """
        Relative path for the department (one segment when flat, parent/child when nested).
        Dùng cho create flow và scan khi nested.
        """
        dept_id = (dept_id or "").strip()
        if self._asset_relative_path or self._shot_relative_path:
            rel = self._shot_relative_path if context == "shot" else self._asset_relative_path
            if rel:
                return rel.get(dept_id) or self._single_folder(dept_id, context)
        return self._single_folder(dept_id, context)

    def _single_folder(self, dept_id: str, context: DepartmentContext) -> str:
        node = self._mapping.get(dept_id)
        if not node:
            return dept_id or ""
        key = "shot_folder" if context == "shot" else "asset_folder"
        folder = (node.get(key) or node.get("folder") or dept_id or "").strip()
        return folder or dept_id

    def get_department_relative_paths(self, context: DepartmentContext) -> list[tuple[str, str]]:
        """
        List (relative_path, dept_id) for all departments.
        Nested: full path (vd 01_modelling/01_sculpt) để scan tìm subdepartment.
        """
        rel = self._shot_relative_path if context == "shot" else self._asset_relative_path
        if rel:
            return [(path, d) for d, path in rel.items() if path]
        out: list[tuple[str, str]] = []
        for dept_id in self.get_departments():
            path = self.get_department_relative_path(dept_id, context)
            if path:
                out.append((path, dept_id))
        return out

    def get_department_by_folder(self, folder_name: str, context: DepartmentContext) -> str | None:
        """Resolve folder name or relative path to logical department ID."""
        key = (folder_name or "").strip()
        if not key:
            return None
        if context == "shot":
            if self._shot_relative_path_to_id:
                return self._shot_relative_path_to_id.get(key)
            return self._shot_folder_to_id.get(key)
        if self._asset_relative_path_to_id:
            return self._asset_relative_path_to_id.get(key)
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


def _department_folder_usage(
    index: ProjectIndex,
    folder_name_or_rel_path: str,
) -> tuple[bool, bool]:
    """
    Check usage of a physical department folder across the project.
    folder_name_or_rel_path: single segment (flat) or relative path (nested, e.g. 01_modelling/01_sculpt).
    Returns (folder_exists_anywhere, files_exist_anywhere).
    """
    folder_exists = False
    files_exist = False
    key = (folder_name_or_rel_path or "").strip()
    use_relative = "/" in key

    def check_item_departments(item_root: Path, departments: tuple) -> None:
        nonlocal folder_exists, files_exist
        for d in departments:
            if use_relative:
                try:
                    rel = d.path.relative_to(item_root)
                    if rel.as_posix() != key:
                        continue
                except (ValueError, TypeError):
                    continue
            else:
                if d.path.name != key:
                    continue
            folder_exists = True
            work_path = d.work_path
            publish_path = d.publish_path
            if _dir_has_files(work_path) or _dir_has_files(publish_path):
                files_exist = True
                return

    for a in index.assets:
        check_item_departments(a.path, a.departments)
        if files_exist:
            return True, True
    for s in index.shots:
        check_item_departments(s.path, s.departments)
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
        row: dict = {
            "label": label.strip(),
            "folder": folder.strip(),
            "shot_folder": shot_folder.strip(),
            "asset_folder": asset_folder.strip(),
            "order": int(order),
        }
        if isinstance(node.get("parent"), str) and (node.get("parent") or "").strip():
            row["parent"] = (node.get("parent") or "").strip()
        payload[dept_id.strip()] = row
    return payload


def write_departments_to_path(path: Path, mapping: dict[str, dict]) -> bool:
    """
    Write departments.json to the given path (e.g. user default).
    Uses atomic write (temp -> flush -> fsync -> rename). Returns False if payload empty or write fails.
    """
    from monostudio.core.atomic_write import atomic_write_text

    payload = _build_departments_payload(mapping)
    if not payload:
        return False
    try:
        content = json.dumps({"departments": payload}, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
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
