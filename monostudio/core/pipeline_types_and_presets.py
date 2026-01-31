from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TypeDef:
    type_id: str
    name: str
    short_name: str
    departments: list[str]


@dataclass(frozen=True)
class PipelineTypesAndPresets:
    types: dict[str, TypeDef]


def pipeline_root() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "monostudio26" / "pipeline"


def pipeline_types_and_presets_path() -> Path:
    return pipeline_root() / "types_and_presets.json"


def pipeline_department_vocabulary_path() -> Path:
    return pipeline_root() / "department_vocabulary.json"


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
        "types": {
            # Shots (type_id == "shot" or "shot_*")
            "shot": {
                "id": "shot",
                "name": "Shot",
                "short_name": "sh",
                "departments": ["layout", "anim", "fx", "lighting"],
            },
            # Assets (any type_id not shot/shot_*)
            "character": {
                "id": "character",
                "name": "Character",
                "short_name": "char",
                "departments": ["model", "rig", "anim"],
            },
            "prop": {
                "id": "prop",
                "name": "Prop",
                "short_name": "prop",
                "departments": ["model"],
            },
            "environment": {
                "id": "environment",
                "name": "Environment",
                "short_name": "env",
                "departments": ["layout", "model", "lighting"],
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
        return PipelineTypesAndPresets(types={})

    if not isinstance(data, dict):
        return PipelineTypesAndPresets(types={})

    types_raw = data.get("types")
    if not isinstance(types_raw, dict):
        return PipelineTypesAndPresets(types={})

    out: dict[str, TypeDef] = {}
    for type_id, node in types_raw.items():
        if not isinstance(type_id, str) or not type_id:
            continue
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id and node_id != type_id:
            # Ignore mismatched nodes silently.
            continue
        name = node.get("name")
        short_name = node.get("short_name")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(short_name, str) or not short_name.strip():
            continue

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

        out[type_id] = TypeDef(
            type_id=type_id,
            name=name.strip(),
            short_name=short_name.strip(),
            departments=departments,
        )

    return PipelineTypesAndPresets(types=out)


def save_pipeline_types_and_presets(config: PipelineTypesAndPresets) -> bool:
    ensure_pipeline_bootstrap()
    payload: dict = {"types": {}}
    types_out: dict[str, dict] = {}
    for type_id, t in config.types.items():
        types_out[type_id] = {
            "id": type_id,
            "name": t.name,
            "short_name": t.short_name,
            "departments": t.departments,
        }
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


def load_department_vocabulary() -> list[str]:
    """
    Pipeline vocabulary file (optional):
      monostudio26/pipeline/department_vocabulary.json
    Missing/invalid => empty list (no vocabulary constraints).
    """
    ensure_pipeline_bootstrap()
    path = pipeline_department_vocabulary_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in data:
        if isinstance(x, str) and x.strip() and x not in seen:
            seen.add(x)
            out.append(x)
    return out

