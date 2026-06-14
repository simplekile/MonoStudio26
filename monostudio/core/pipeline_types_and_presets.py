from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.department_registry import (
    DepartmentRegistry,
    get_default_department_mapping,
    get_project_pipeline_dir,
)


@dataclass(frozen=True)
class DepartmentDef:
    dept_id: str
    name: str
    short_name: str
    icon_name: str | None = None
    parent: str | None = None  # subdepartment: parent dept_id for grouping in UI


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


EntityScope = Literal["asset", "shot"]

# Departments that exist on shots only (never offered for assets).
SHOT_ONLY_DEPARTMENT_IDS: frozenset[str] = frozenset({"lighting"})


def filter_departments_for_entity_scope(
    dept_ids: list[str],
    scope: EntityScope,
) -> list[str]:
    """Drop shot-only departments when ``scope`` is ``asset``."""
    if scope != "asset":
        return list(dept_ids)
    blocked = SHOT_ONLY_DEPARTMENT_IDS
    return [d for d in dept_ids if (d or "").strip() and d.strip() not in blocked]


# Lucide slugs shipped under monostudio_data/icons/lucide/ (sidebar, picker, badges).
DEPARTMENT_ICON_DEFAULTS: dict[str, str] = {
    "layout": "layout-dashboard",
    "modelling": "box",
    "model": "box",
    "modeling": "box",
    "sculpt": "zbrush",
    "retopo": "hexagon",
    "uv": "checkerboard",
    "rigging": "bone",
    "geoclean": "brush-cleaning",
    "rig": "bone",
    "surfacing": "palette",
    "baking": "chef-hat",
    "texturing": "palette",
    "grooming": "scissors",
    "lookdev": "sparkles",
    "anim": "spline",
    "animation": "spline",
    "fx": "zap",
    "groom": "scissors",
    "crowd": "user",
    "cloth": "layers",
    "pyro": "sun",
    "fluids": "wand",
    "destruction": "triangle-alert",
    "particles": "wand-sparkles",
    "lighting": "lightbulb",
    "comp": "sliders-horizontal",
}


def department_icon_name(dept_id: str, *, explicit: str | None = None) -> str | None:
    """Resolve Lucide icon slug for a department; explicit metadata wins over defaults."""
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return DEPARTMENT_ICON_DEFAULTS.get((dept_id or "").strip())


def is_shot_type_id(type_id: str) -> bool:
    """Pipeline convention: shot types are ``shot`` or prefixed ``shot_``."""
    tid = (type_id or "").strip()
    return bool(tid == "shot" or tid.startswith("shot_"))


def order_department_ids_grouped_by_parent(
    dept_ids: list[str],
    dept_parent: dict[str, str],
    global_order: list[str],
) -> list[str]:
    """
    Order department ids for sidebar / picker: each parent's subdepartments appear
    consecutively under that parent (by global_order), not interleaved with unrelated depts.
    """
    id_set = set(dept_ids)
    if not id_set:
        return []

    by_parent: dict[str, list[str]] = {}
    roots: list[str] = []
    for did in dept_ids:
        p = (dept_parent.get(did) or "").strip()
        if p:
            by_parent.setdefault(p, []).append(did)
        else:
            roots.append(did)

    order_idx = {d: i for i, d in enumerate(global_order)}

    def sort_ids(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda d: (order_idx.get(d, 9999), d.lower()))

    for pid in by_parent:
        by_parent[pid] = sort_ids(by_parent[pid])
    roots = sort_ids(roots)

    out: list[str] = []
    seen: set[str] = set()

    for gid in global_order:
        if gid in roots and gid not in seen:
            out.append(gid)
            seen.add(gid)
        for child in by_parent.get(gid, []):
            if child in id_set and child not in seen:
                out.append(child)
                seen.add(child)

    for did in sort_ids([d for d in dept_ids if d not in seen]):
        out.append(did)
    return out


def resolve_department_ids_for_ui(
    dept_ids: list[str],
    *,
    meta: PipelineTypesAndPresets,
    registry: DepartmentRegistry | None = None,
) -> list[str]:
    """
    Expand parent-only IDs to leaf subdepartments; drop parents when children are listed.

    Used by sidebar / inspector filters so legacy type presets (e.g. ``fx``) still show
    ``groom``, ``destruction``, … when the department registry defines nested children.
    """
    children_by_parent: dict[str, list[str]] = {}
    parent_of: dict[str, str] = {}

    def add_child(child: str, parent: str) -> None:
        c = (child or "").strip()
        p = (parent or "").strip()
        if not c or not p:
            return
        parent_of[c] = p
        bucket = children_by_parent.setdefault(p, [])
        if c not in bucket:
            bucket.append(c)

    for child_id, ddef in meta.departments.items():
        if ddef.parent:
            add_child(child_id, ddef.parent)

    if registry is not None:
        for child_id in registry.get_departments():
            parent = registry.get_parent(child_id)
            if parent:
                add_child(child_id, parent)

    global_order = (
        registry.get_departments() if registry is not None else list(meta.departments.keys())
    )

    def ordered_children(parent: str) -> list[str]:
        kids = set(children_by_parent.get(parent, []))
        return [k for k in global_order if k in kids]

    seen: set[str] = set()
    out: list[str] = []
    for raw in dept_ids:
        did = (raw or "").strip()
        if not did:
            continue
        if children_by_parent.get(did):
            for kid in ordered_children(did):
                if kid not in seen:
                    seen.add(kid)
                    out.append(kid)
            continue
        if did not in seen:
            seen.add(did)
            out.append(did)

    parents_in_out = {d for d in out if children_by_parent.get(d)}
    if parents_in_out:
        out = [d for d in out if d not in parents_in_out]
    return out


def expand_pipeline_types_and_presets_with_registry(
    config: PipelineTypesAndPresets,
    registry: DepartmentRegistry | None,
) -> PipelineTypesAndPresets:
    """
    Expand type department lists (e.g. legacy ``fx`` → FX leaves) and enrich department defs
    from the project registry (factory merge applied in DepartmentRegistry.for_project).
    """
    if not config.types and not config.departments:
        return config

    new_types: dict[str, TypeDef] = {}
    for type_id, tdef in config.types.items():
        expanded = resolve_department_ids_for_ui(list(tdef.departments), meta=config, registry=registry)
        if expanded != tdef.departments:
            new_types[type_id] = TypeDef(
                type_id, tdef.name, tdef.short_name, expanded, tdef.icon_name
            )
        else:
            new_types[type_id] = tdef

    new_depts = dict(config.departments)
    if registry is not None:
        for dept_id in registry.get_departments():
            parent = registry.get_parent(dept_id)
            existing = new_depts.get(dept_id)
            label = registry.get_department_label(dept_id)
            short = (existing.short_name if existing else (dept_id[:4] if len(dept_id) >= 4 else dept_id))
            icon = department_icon_name(
                dept_id, explicit=existing.icon_name if existing else None
            )
            parent_val = parent or (existing.parent if existing else None)
            if existing is None:
                new_depts[dept_id] = DepartmentDef(dept_id, label, short, icon, parent_val)
            elif parent_val and not existing.parent:
                new_depts[dept_id] = DepartmentDef(
                    dept_id, existing.name, existing.short_name, icon or existing.icon_name, parent_val
                )
            elif not existing.icon_name and icon:
                new_depts[dept_id] = DepartmentDef(
                    dept_id, existing.name, existing.short_name, icon, existing.parent
                )

    return PipelineTypesAndPresets(types=new_types, departments=new_depts)


def ordered_department_ids_for_scope(
    meta: PipelineTypesAndPresets,
    scope: EntityScope,
    *,
    type_id: str | None = None,
    registry: DepartmentRegistry | None = None,
) -> list[str]:
    """
    Department IDs for asset or shot scope, ordered like sidebar filters.

    When ``type_id`` is set and matches ``scope``, only that type's departments are returned.
    Otherwise returns the union across all types in the scope.
    """
    assets = scope == "asset"
    raw_ordered: list[str] = []
    raw_seen: set[str] = set()
    tid = (type_id or "").strip()

    if tid and tid in meta.types:
        if (assets and not is_shot_type_id(tid)) or (not assets and is_shot_type_id(tid)):
            for d in meta.types[tid].departments:
                if isinstance(d, str) and d.strip() and d not in raw_seen:
                    raw_seen.add(d.strip())
                    raw_ordered.append(d.strip())
    else:
        for type_key, tdef in meta.types.items():
            if assets and is_shot_type_id(type_key):
                continue
            if not assets and not is_shot_type_id(type_key):
                continue
            for d in tdef.departments:
                if isinstance(d, str) and d.strip() and d not in raw_seen:
                    raw_seen.add(d.strip())
                    raw_ordered.append(d.strip())

    resolved = resolve_department_ids_for_ui(raw_ordered, meta=meta, registry=registry)
    seen = set(resolved)

    if registry is not None:
        order_source = registry.get_departments()
    else:
        order_source = list(meta.departments.keys())

    depts: list[str] = [dept_id for dept_id in order_source if dept_id in seen]
    missing = [d for d in resolved if d not in depts]
    depts.extend(missing)
    parent_of: dict[str, str] = {}
    for dept_id, ddef in meta.departments.items():
        if ddef.parent:
            parent_of[dept_id] = ddef.parent.strip()
    if registry is not None:
        for dept_id in registry.get_departments():
            p = registry.get_parent(dept_id)
            if p:
                parent_of[dept_id] = p
    ordered = order_department_ids_grouped_by_parent(depts, parent_of, order_source)
    return filter_departments_for_entity_scope(ordered, scope)


def pipeline_root() -> Path:
    return get_app_base_path() / "monostudio_data" / "pipeline"


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
        "anim": DepartmentDef("anim", "Animation", "anim", "spline"),
        "fx": DepartmentDef("fx", "FX", "fx", "zap"),
        "lighting": DepartmentDef("lighting", "Lighting", "lgt", "lightbulb"),
        "comp": DepartmentDef("comp", "Comp", "comp", "sliders-horizontal"),
    }
    default_types = {
        "shot": TypeDef("shot", "Shot", "sh", ["layout", "anim", "fx", "lighting"], "clapperboard"),
        "character": TypeDef("character", "Character", "char", ["model", "rig", "surfacing", "grooming", "lookdev"], "user"),
        "prop": TypeDef("prop", "Prop", "prop", ["model", "surfacing", "grooming", "lookdev"], "package"),
        "environment": TypeDef("environment", "Environment", "env", ["layout", "model", "lookdev"], "trees"),
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
            "anim": {"name": "Animation", "short_name": "anim", "icon_name": "spline"},
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
                "departments": ["layout", "model", "lookdev"],
            },
        }
    }
    try:
        from monostudio.core.atomic_write import atomic_write_text
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
    except OSError:
        return


def _parse_types_and_presets_data(data: dict) -> PipelineTypesAndPresets:
    """Parse types_and_presets JSON object into PipelineTypesAndPresets."""
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
            parent_raw = node.get("parent")
            parent = (parent_raw.strip() if isinstance(parent_raw, str) and parent_raw.strip() else None) or None
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
                parent=parent,
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

    if not out_depts:
        seen: set[str] = set()
        for t in out_types.values():
            for d in t.departments:
                if isinstance(d, str) and d.strip() and d not in seen:
                    seen.add(d)
                    out_depts[d] = DepartmentDef(dept_id=d, name=d, short_name=d, icon_name=None)

    return PipelineTypesAndPresets(types=out_types, departments=out_depts)


def load_pipeline_types_and_presets() -> PipelineTypesAndPresets:
    ensure_pipeline_bootstrap()
    path = pipeline_types_and_presets_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PipelineTypesAndPresets()

    if not isinstance(data, dict):
        return PipelineTypesAndPresets()
    return _parse_types_and_presets_data(data)


def load_pipeline_types_and_presets_for_project(project_root: Path | None) -> PipelineTypesAndPresets:
    """
    Load types_and_presets metadata for UI: project file first, then user default, then app shipped file.
    """
    paths: list[Path] = []
    if project_root is not None:
        paths.append(get_project_pipeline_dir(Path(project_root)) / _TYPES_AND_PRESETS_JSON)
    ensure_user_default_config_dir()
    paths.append(get_user_default_config_root() / "pipeline" / _TYPES_AND_PRESETS_JSON)
    paths.append(pipeline_types_and_presets_path())

    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        cfg = _parse_types_and_presets_data(data)
        if cfg.types or cfg.departments:
            registry: DepartmentRegistry | None = None
            try:
                if project_root is not None:
                    registry = DepartmentRegistry.for_project(Path(project_root))
                else:
                    registry = DepartmentRegistry(get_default_department_mapping(), None)
            except OSError:
                registry = None
            return expand_pipeline_types_and_presets_with_registry(cfg, registry)

    ensure_pipeline_bootstrap()
    return PipelineTypesAndPresets()


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
        if d.parent:
            node["parent"] = d.parent
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
        from monostudio.core.atomic_write import atomic_write_text
        pipeline_root().mkdir(parents=True, exist_ok=True)
        path = pipeline_types_and_presets_path()
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
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
        if d.parent:
            node["parent"] = d.parent
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
        from monostudio.core.atomic_write import atomic_write_text
        root = get_user_default_config_root()
        pipeline_dir = root / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        path = pipeline_dir / _TYPES_AND_PRESETS_JSON
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
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
        if d.parent:
            node["parent"] = d.parent
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
        from monostudio.core.atomic_write import atomic_write_text
        pipeline_dir = get_project_pipeline_dir(Path(project_root))
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        path = pipeline_dir / _TYPES_AND_PRESETS_JSON
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
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

