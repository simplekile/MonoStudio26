from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.pipeline_types_and_presets import get_user_default_config_root
from monostudio.core.project_id import generate_project_id
from monostudio.core.structure_registry import StructureRegistry

PROJECT_GUIDE_DEPARTMENTS = ("reference", "script", "storyboard", "guideline", "concept")


def _mono2026_preset_path() -> Path:
    return get_app_base_path() / "monostudio_data" / "pipeline" / "department_presets" / "mono2026_preset.json"


def _load_user_default_json(filename: str) -> dict:
    """Load a single JSON config from Documents/.monostudio/pipeline/<filename>."""
    path = get_user_default_config_root() / "pipeline" / filename
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_initial_configs() -> tuple[dict, dict, dict]:
    """
    Resolve initial departments, types, and folders configs for a new project.
    Priority: user defaults (Documents/.monostudio/pipeline/) > mono2026 preset > hardcoded fallback.
    Returns (departments_dict, types_dict, folders_dict) — each is the inner mapping, not the wrapper.
    """
    preset_data: dict = {}
    try:
        preset_path = _mono2026_preset_path()
        if preset_path.is_file():
            preset_data = json.loads(preset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    depts = preset_data.get("departments", {})
    types = preset_data.get("types", {})
    folders = preset_data.get("folders", {})

    user_depts = _load_user_default_json("departments.json")
    if isinstance(user_depts.get("departments"), dict) and user_depts["departments"]:
        depts = dict(user_depts["departments"])
        # Preserve nested layout: re-apply "parent" from preset so subdepartments stay under parent folder.
        preset_depts = preset_data.get("departments") or {}
        for dept_id, preset_node in preset_depts.items():
            if isinstance(preset_node, dict) and isinstance(preset_node.get("parent"), str) and preset_node["parent"].strip():
                if dept_id in depts and isinstance(depts[dept_id], dict):
                    depts[dept_id] = {**depts[dept_id], "parent": preset_node["parent"].strip()}

    user_types = _load_user_default_json("types.json")
    if isinstance(user_types.get("types"), dict) and user_types["types"]:
        types = user_types["types"]

    user_structure = _load_user_default_json("structure.json")
    if isinstance(user_structure.get("folders"), dict) and user_structure["folders"]:
        folders = user_structure["folders"]

    return (depts, types, folders)


@dataclass(frozen=True)
class CreatedProject:
    project_id: str
    display_name: str
    start_date: str  # YYYY-MM-DD
    root: Path


def create_new_project(
    *,
    workspace_root: Path,
    display_name: str,
    start_date: str,
    created_date: date | None = None,
) -> CreatedProject:
    """
    Safe project creation (read/write):
    - Creates a new project folder under workspace_root using an auto-generated Project ID.
    - Creates required structure: assets/, shots/, project_guide/ (with reference, script, storyboard, guideline, concept), .monostudio/project.json
    - Writes metadata deterministically.
    - On failure: best-effort rollback inside the new project folder only.
    """

    name = (display_name or "").strip()
    if not name:
        raise ValueError("Project Name is required.")

    if not workspace_root.is_dir():
        raise FileNotFoundError("Workspace root folder does not exist.")

    project_id = generate_project_id(name, created_date=created_date)
    if not project_id:
        raise ValueError("Failed to generate Project ID.")

    project_root = workspace_root / project_id
    if project_root.exists():
        raise FileExistsError("Target project folder already exists.")

    # Resolve initial configs: user defaults > mono2026 preset > hardcoded fallback.
    preset_depts, preset_types, preset_folders = _resolve_initial_configs()

    if isinstance(preset_folders, dict) and preset_folders:
        struct_reg = StructureRegistry(preset_folders, None)
    else:
        struct_reg = StructureRegistry.for_project(project_root)

    created_paths: list[Path] = []
    try:
        assets_dir = project_root / struct_reg.get_folder("assets")
        assets_dir.mkdir(parents=True, exist_ok=False)
        created_paths.append(assets_dir)
        shots_dir = project_root / struct_reg.get_folder("shots")
        shots_dir.mkdir(parents=True, exist_ok=False)
        created_paths.append(shots_dir)

        project_guide_root = project_root / struct_reg.get_folder("project_guide")
        project_guide_root.mkdir(parents=True, exist_ok=False)
        created_paths.append(project_guide_root)
        for dept in PROJECT_GUIDE_DEPARTMENTS:
            d = project_guide_root / dept
            d.mkdir(parents=True, exist_ok=False)
            created_paths.append(d)

        monostudio_dir = project_root / ".monostudio"
        monostudio_dir.mkdir(parents=True, exist_ok=False)
        created_paths.append(monostudio_dir)

        manifest = monostudio_dir / "project.json"
        manifest.write_text(
            json.dumps(
                {
                    "id": project_id,
                    "name": name,
                    "start_date": start_date,
                    "schema": 1,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # Write pipeline configs so new project starts with the correct mapping.
        pipeline_dir = monostudio_dir / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        created_paths.append(pipeline_dir)

        if isinstance(preset_depts, dict) and preset_depts:
            (pipeline_dir / "departments.json").write_text(
                json.dumps({"departments": preset_depts}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        if isinstance(preset_types, dict) and preset_types:
            (pipeline_dir / "types.json").write_text(
                json.dumps({"types": preset_types}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        if isinstance(preset_folders, dict) and preset_folders:
            (pipeline_dir / "structure.json").write_text(
                json.dumps({"folders": preset_folders}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        return CreatedProject(project_id=project_id, display_name=name, start_date=start_date, root=project_root)
    except Exception:
        # Rollback inside project_root only.
        try:
            if project_root.exists():
                shutil.rmtree(project_root)
        except Exception:
            # Best-effort; caller may need to handle partials manually.
            pass
        raise

