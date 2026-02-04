from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from monostudio.core.department_registry import get_project_pipeline_dir


@dataclass(frozen=True)
class DepartmentDef:
    dept_id: str
    name: str
    short_name: str
    icon_name: str | None = None


@dataclass(frozen=True)
class TypeDef:
    type_id: str
    name: str
    short_name: str
    departments: list[str]
    icon_name: str | None = None


@dataclass(frozen=True)
class PipelineTypesAndPresets:
    types: dict[str, TypeDef] = field(default_factory=dict)
    departments: dict[str, DepartmentDef] = field(default_factory=dict)


def pipeline_root() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "monostudio_data" / "pipeline"


def pipeline_types_and_presets_path() -> Path:
    return pipeline_root() / "types_and_presets.json"


def pipeline_department_vocabulary_path() -> Path:
    return pipeline_root() / "department_vocabulary.json"


def pipeline_department_presets_dir() -> Path:
    """Directory for shipped department mapping presets (*.json)."""
    return pipeline_root() / "department_presets"


def get_default_pipeline_types_and_presets() -> PipelineTypesAndPresets:
    """Return the built-in default types and departments (same as bootstrap)."""
    default_depts = {
        "layout": DepartmentDef("layout", "Layout", "lay", "layout-dashboard"),
        "model": DepartmentDef("model", "Modeling", "mdl", "box"),
        "rig": DepartmentDef("rig", "Rigging", "rig", "bone"),
        "surfacing": DepartmentDef("surfacing", "Surfacing", "surf", "palette"),
        "grooming": DepartmentDef("grooming", "Grooming", "grm", "scissors"),
        "lookdev": DepartmentDef("lookdev", "Lookdev", "ldv", "sparkles"),
        "anim": DepartmentDef("anim", "Animation", "anim", "clapperboard"),
        "fx": DepartmentDef("fx", "FX", "fx", "zap"),
        "lighting": DepartmentDef("lighting", "Lighting", "lgt", "lightbulb"),
        "comp": DepartmentDef("comp", "Comp", "comp", "sliders-horizontal"),
    }
    default_types = {
        "shot": TypeDef("shot", "Shot", "sh", ["layout", "anim", "fx", "lighting"], "clapperboard"),
        "character": TypeDef("character", "Character", "char", ["model", "rig", "surfacing", "grooming", "lookdev"], "user"),
        "prop": TypeDef("prop", "Prop", "prop", ["model", "surfacing", "grooming", "lookdev"], "package"),
        "environment": TypeDef("environment", "Environment", "env", ["layout", "model", "lighting", "lookdev"], "trees"),
    }
    return PipelineTypesAndPresets(types=default_types, departments=default_depts)


def ensure_pipeline_bootstrap() -> None:
    """
    Mandatory bootstrap:
    - If pipeline types file is missing, create it with the default minimal content.
    """
    root = pipeline_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    path = pipeline_types_and_presets_path()
    if path.exists():
        return

    payload = {
        "departments": {
            "layout": {"name": "Layout", "short_name": "lay", "icon_name": "layout-dashboard"},
            "model": {"name": "Modeling", "short_name": "mdl", "icon_name": "box"},
            "rig": {"name": "Rigging", "short_name": "rig", "icon_name": "bone"},
            "surfacing": {"name": "Surfacing", "short_name": "surf", "icon_name": "palette"},
            "grooming": {"name": "Grooming", "short_name": "grm", "icon_name": "scissors"},
            "lookdev": {"name": "Lookdev", "short_name": "ldv", "icon_name": "sparkles"},
            "anim": {"name": "Animation", "short_name": "anim", "icon_name": "clapperboard"},
            "fx": {"name": "FX", "short_name": "fx", "icon_name": "zap"},
            "lighting": {"name": "Lighting", "short_name": "lgt", "icon_name": "lightbulb"},
            "comp": {"name": "Comp", "short_name": "comp", "icon_name": "sliders-horizontal"},
        },
        "types": {
            # Shots (type_id == "shot" or "shot_*")
            "shot": {
                "id": "shot",
                "name": "Shot",
                "short_name": "sh",
                "icon_name": "clapperboard",
                "departments": ["layout", "anim", "fx", "lighting"],
            },
            # Assets (any type_id not shot/shot_*)
            "character": {
                "id": "character",
                "name": "Character",
                "short_name": "char",
                "icon_name": "user",
                "departments": ["model", "rig", "surfacing", "grooming", "lookdev"],
            },
            "prop": {
                "id": "prop",
                "name": "Prop",
                "short_name": "prop",
                "icon_name": "package",
                "departments": ["model", "surfacing", "grooming", "lookdev"],
            },
            "environment": {
                "id": "environment",
                "name": "Environment",
                "short_name": "env",
                "icon_name": "trees",
                "departments": ["layout", "model", "lighting", "lookdev"],
            },
        }
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


def load_pipeline_types_and_presets() -> PipelineTypesAndPresets:
    ensure_pipeline_bootstrap()
    path = pipeline_types_and_presets_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PipelineTypesAndPresets()

    if not isinstance(data, dict):
        return PipelineTypesAndPresets()

    depts_raw = data.get("departments")
    out_depts: dict[str, DepartmentDef] = {}
    if isinstance(depts_raw, dict):
        for dept_id, node in depts_raw.items():
            if not isinstance(dept_id, str) or not dept_id.strip():
                continue
            if not isinstance(node, dict):
                continue
            name = node.get("name")
            short_name = node.get("short_name")
            icon_name = node.get("icon_name")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(short_name, str) or not short_name.strip():
                continue
            icon = icon_name.strip() if isinstance(icon_name, str) and icon_name.strip() else None
            out_depts[dept_id] = DepartmentDef(
                dept_id=dept_id,
                name=name.strip(),
                short_name=short_name.strip(),
                icon_name=icon,
            )

    types_raw = data.get("types")
    if not isinstance(types_raw, dict):
        return PipelineTypesAndPresets(types={}, departments=out_depts)

    out_types: dict[str, TypeDef] = {}
    for key, node in types_raw.items():
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(node, dict):
            continue
        # Use node["id"] as type_id when present (e.g. _characters), else use object key.
        node_id = node.get("id")
        type_id = (node_id.strip() if isinstance(node_id, str) and node_id.strip() else key)
        name = node.get("name")
        short_name = node.get("short_name")
        icon_name = node.get("icon_name")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(short_name, str) or not short_name.strip():
            continue
        icon = icon_name.strip() if isinstance(icon_name, str) and icon_name.strip() else None

        # New schema: departments: [ ... ]
        # Back-compat: department_presets: { "Default": [ ... ], ... } -> pick "Default" if present, else first preset.
        departments: list[str] = []
        raw_depts = node.get("departments")
        if isinstance(raw_depts, list):
            departments = [d for d in raw_depts if isinstance(d, str) and d.strip()]
        else:
            presets_raw = node.get("department_presets")
            if isinstance(presets_raw, dict) and presets_raw:
                chosen_key = "Default" if "Default" in presets_raw else sorted(presets_raw.keys(), key=lambda s: str(s).lower())[0]
                chosen = presets_raw.get(chosen_key)
                if isinstance(chosen, list):
                    departments = [d for d in chosen if isinstance(d, str) and d.strip()]

        out_types[type_id] = TypeDef(
            type_id=type_id,
            name=name.strip(),
            short_name=short_name.strip(),
            departments=departments,
            icon_name=icon,
        )

    # Back-compat: if departments metadata is missing, synthesize minimal defs from observed department ids.
    if not out_depts:
        seen: set[str] = set()
        for t in out_types.values():
            for d in t.departments:
                if isinstance(d, str) and d.strip() and d not in seen:
                    seen.add(d)
                    out_depts[d] = DepartmentDef(dept_id=d, name=d, short_name=d, icon_name=None)

    return PipelineTypesAndPresets(types=out_types, departments=out_depts)


def save_pipeline_types_and_presets(config: PipelineTypesAndPresets) -> bool:
    ensure_pipeline_bootstrap()
    payload: dict = {"types": {}, "departments": {}}

    depts_out: dict[str, dict] = {}
    for dept_id, d in config.departments.items():
        node: dict[str, object] = {
            "name": d.name,
            "short_name": d.short_name,
        }
        if d.icon_name:
            node["icon_name"] = d.icon_name
        depts_out[dept_id] = node
    payload["departments"] = depts_out

    types_out: dict[str, dict] = {}
    for type_id, t in config.types.items():
        node: dict[str, object] = {
            "id": type_id,
            "name": t.name,
            "short_name": t.short_name,
            "departments": t.departments,
        }
        if t.icon_name:
            node["icon_name"] = t.icon_name
        types_out[type_id] = node
    payload["types"] = types_out

    try:
        pipeline_root().mkdir(parents=True, exist_ok=True)
        pipeline_types_and_presets_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


_TYPES_AND_PRESETS_JSON = "types_and_presets.json"


def get_user_default_config_root() -> Path:
    """User-level default config root: Documents/.monostudio/ (cross-platform)."""
    return Path.home() / "Documents" / ".monostudio"


def ensure_user_default_config_dir() -> None:
    """Create Documents/.monostudio/ and pipeline/ subdir if missing (call at app startup)."""
    try:
        root = get_user_default_config_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "pipeline").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def seed_project_from_user_default(project_root: Path) -> bool:
    """
    If Documents/.monostudio/pipeline/ has types_and_presets.json, types.json or
    departments.json, copy them into the project's .monostudio/pipeline/ so the
    new project uses that config. Returns True if at least one file was copied.
    """
    user_pipeline = get_user_default_config_root() / "pipeline"
    try:
        pipeline_dir = get_project_pipeline_dir(Path(project_root))
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        copied = False
        for name in (_TYPES_AND_PRESETS_JSON, "types.json", "departments.json"):
            src = user_pipeline / name
            if src.is_file():
                shutil.copy2(src, pipeline_dir / name)
                copied = True
        return copied
    except OSError:
        return False


def save_pipeline_types_and_presets_to_user_default(config: PipelineTypesAndPresets) -> bool:
    """Write types and departments config to Documents/.monostudio/pipeline/types_and_presets.json."""
    payload: dict = {"types": {}, "departments": {}}

    depts_out: dict[str, dict] = {}
    for dept_id, d in config.departments.items():
        node: dict[str, object] = {
            "name": d.name,
            "short_name": d.short_name,
        }
        if d.icon_name:
            node["icon_name"] = d.icon_name
        depts_out[dept_id] = node
    payload["departments"] = depts_out

    types_out: dict[str, dict] = {}
    for type_id, t in config.types.items():
        node: dict[str, object] = {
            "id": type_id,
            "name": t.name,
            "short_name": t.short_name,
            "departments": t.departments,
        }
        if t.icon_name:
            node["icon_name"] = t.icon_name
        types_out[type_id] = node
    payload["types"] = types_out

    try:
        root = get_user_default_config_root()
        pipeline_dir = root / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_dir / _TYPES_AND_PRESETS_JSON).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def save_pipeline_types_and_presets_to_project(project_root: Path, config: PipelineTypesAndPresets) -> bool:
    """Write types and departments config to <project_root>/.monostudio/pipeline/types_and_presets.json."""
    payload: dict = {"types": {}, "departments": {}}

    depts_out: dict[str, dict] = {}
    for dept_id, d in config.departments.items():
        node: dict[str, object] = {
            "name": d.name,
            "short_name": d.short_name,
        }
        if d.icon_name:
            node["icon_name"] = d.icon_name
        depts_out[dept_id] = node
    payload["departments"] = depts_out

    types_out: dict[str, dict] = {}
    for type_id, t in config.types.items():
        node: dict[str, object] = {
            "id": type_id,
            "name": t.name,
            "short_name": t.short_name,
            "departments": t.departments,
        }
        if t.icon_name:
            node["icon_name"] = t.icon_name
        types_out[type_id] = node
    payload["types"] = types_out

    try:
        pipeline_dir = get_project_pipeline_dir(Path(project_root))
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_dir / _TYPES_AND_PRESETS_JSON).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def load_department_vocabulary() -> list[str]:
    """
    Pipeline vocabulary file (optional):
      monostudio26/pipeline/department_vocabulary.json

    If the file is missing/invalid/empty, fall back to departments defined in
    types_and_presets.json (single source of truth).
    """
    ensure_pipeline_bootstrap()
    path = pipeline_department_vocabulary_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    if not isinstance(data, list):
        data = None
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(data, list):
        for x in data:
            if isinstance(x, str) and x.strip() and x not in seen:
                seen.add(x)
                out.append(x)
    if out:
        return out

    # Fallback: departments metadata keys.
    cfg = load_pipeline_types_and_presets()
    for dept_id in sorted(cfg.departments.keys(), key=lambda s: str(s).lower()):
        if isinstance(dept_id, str) and dept_id.strip() and dept_id not in seen:
            seen.add(dept_id)
            out.append(dept_id)
    return out

