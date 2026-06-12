"""
Tag system for Project Guide items (macOS-style colored tags).
Storage: <project_root>/.monostudio/project_guide_tags.json
Keys in item_tags are relative paths from project_guide root (forward-slash separated).
Tag definitions are stored per department (reference, script, …) — each has its own tag slots.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from monostudio.core.atomic_write import atomic_write_text

_TAGS_FILENAME = "project_guide_tags.json"

PROJECT_GUIDE_TAG_DEPARTMENTS = ("reference", "script", "storyboard", "guideline", "concept")

DEFAULT_TAG_DEFINITIONS: list[dict[str, str]] = [
    {"id": "red", "color": "#FF3B30", "label": "Red"},
    {"id": "orange", "color": "#FF9500", "label": "Orange"},
    {"id": "yellow", "color": "#FFCC00", "label": "Yellow"},
    {"id": "green", "color": "#34C759", "label": "Green"},
    {"id": "blue", "color": "#007AFF", "label": "Blue"},
    {"id": "purple", "color": "#AF52DE", "label": "Purple"},
    {"id": "gray", "color": "#8E8E93", "label": "Gray"},
]

TAG_COLOR_BY_ID: dict[str, str] = {t["id"]: t["color"] for t in DEFAULT_TAG_DEFINITIONS}
TAG_LABEL_BY_ID: dict[str, str] = {t["id"]: t["label"] for t in DEFAULT_TAG_DEFINITIONS}
ALL_TAG_IDS: list[str] = [t["id"] for t in DEFAULT_TAG_DEFINITIONS]

TAG_COLOR_PALETTE: list[str] = [
    "#FF3B30", "#FF9500", "#FFCC00", "#34C759", "#007AFF",
    "#AF52DE", "#8E8E93", "#FF2D55", "#5856D6", "#00C7BE",
    "#FF6482", "#A2845E",
]


def _tags_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / _TAGS_FILENAME


def _normalize_key(relative_path: str) -> str:
    """Normalize to forward-slash, strip leading/trailing slashes."""
    return (relative_path or "").strip().replace("\\", "/").strip("/")


def normalize_tag_department_id(department_id: str | None) -> str:
    key = (department_id or PROJECT_GUIDE_TAG_DEPARTMENTS[0]).strip().lower()
    return key if key in PROJECT_GUIDE_TAG_DEPARTMENTS else PROJECT_GUIDE_TAG_DEPARTMENTS[0]


def department_for_guide_path(relative_path: str) -> str | None:
    nk = _normalize_key(relative_path)
    if not nk:
        return None
    first = nk.split("/")[0]
    return first if first in PROJECT_GUIDE_TAG_DEPARTMENTS else None


def _read_raw(project_root: Path) -> dict[str, Any]:
    path = _tags_path(project_root)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _write_raw(project_root: Path, data: dict[str, Any]) -> bool:
    path = _tags_path(project_root)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        atomic_write_text(path, content, encoding="utf-8")
        return True
    except OSError:
        return False


def _parse_def_list(raw: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for d in raw:
        if isinstance(d, dict) and d.get("id") and d.get("color") and d.get("label"):
            out.append({"id": d["id"], "color": d["color"], "label": d["label"]})
    return out


def _legacy_tag_definitions(data: dict[str, Any]) -> list[dict[str, str]]:
    raw_defs = data.get("tag_definitions")
    if isinstance(raw_defs, list):
        out = _parse_def_list(raw_defs)
        if out:
            return out
    return list(DEFAULT_TAG_DEFINITIONS)


def _load_storage(project_root: Path) -> dict[str, Any]:
    """Load JSON and migrate legacy global tag_definitions to per-department slots."""
    data = _read_raw(project_root)
    by_dept = data.get("tag_definitions_by_department")
    if isinstance(by_dept, dict) and by_dept:
        return data
    legacy = _legacy_tag_definitions(data)
    migrated = {dept: [dict(d) for d in legacy] for dept in PROJECT_GUIDE_TAG_DEPARTMENTS}
    data["version"] = 3
    data["tag_definitions_by_department"] = migrated
    data.pop("tag_definitions", None)
    if not isinstance(data.get("item_tags"), dict):
        data["item_tags"] = {}
    _write_raw(project_root, data)
    return data


def build_color_map(defs: list[dict[str, str]]) -> dict[str, str]:
    return {t["id"]: t["color"] for t in defs if "id" in t and "color" in t}


def build_label_map(defs: list[dict[str, str]]) -> dict[str, str]:
    return {t["id"]: t["label"] for t in defs if "id" in t and "label" in t}


def read_tag_definitions(project_root: Path, department_id: str) -> list[dict[str, str]]:
    """Read tag slot definitions for one Project Guide department."""
    data = _load_storage(project_root)
    dept = normalize_tag_department_id(department_id)
    by_dept = data.get("tag_definitions_by_department")
    if isinstance(by_dept, dict):
        raw = by_dept.get(dept)
        if isinstance(raw, list):
            out = _parse_def_list(raw)
            if out:
                return out
    return list(DEFAULT_TAG_DEFINITIONS)


def _valid_ids_for_path(project_root: Path, relative_path: str) -> set[str]:
    dept = department_for_guide_path(relative_path)
    if not dept:
        return set()
    return {d["id"] for d in read_tag_definitions(project_root, dept)}


def save_tag_definitions(
    project_root: Path,
    department_id: str,
    defs: list[dict[str, str]],
    item_tags: dict[str, list[str]] | None = None,
) -> bool:
    """Save tag definitions for one department (and optionally item_tags)."""
    data = _load_storage(project_root)
    dept = normalize_tag_department_id(department_id)
    by_dept = data.get("tag_definitions_by_department")
    if not isinstance(by_dept, dict):
        by_dept = {}
    by_dept[dept] = [dict(d) for d in defs if d.get("id") and d.get("color") and d.get("label")]
    data["tag_definitions_by_department"] = by_dept
    data["version"] = 3
    if item_tags is not None:
        clean: dict[str, list[str]] = {}
        for k, v in item_tags.items():
            nk = _normalize_key(k)
            if not nk or not isinstance(v, list):
                continue
            valid_ids = _valid_ids_for_path(project_root, nk)
            tags = [t for t in v if isinstance(t, str) and t in valid_ids]
            if tags:
                clean[nk] = tags
        data["item_tags"] = clean
    return _write_raw(project_root, data)


def add_tag_definition(
    project_root: Path,
    department_id: str,
    label: str,
    color: str,
) -> tuple[bool, list[dict[str, str]]]:
    """Add a new tag to a department slot, return (success, updated_defs)."""
    dept = normalize_tag_department_id(department_id)
    defs = read_tag_definitions(project_root, dept)
    new_id = f"tag_{uuid.uuid4().hex[:8]}"
    defs.append({"id": new_id, "color": color, "label": label})
    ok = save_tag_definitions(project_root, dept, defs)
    return ok, defs


def rename_tag_definition(
    project_root: Path,
    department_id: str,
    tag_id: str,
    new_label: str,
) -> tuple[bool, list[dict[str, str]]]:
    """Rename an existing tag in a department, return (success, updated_defs)."""
    dept = normalize_tag_department_id(department_id)
    defs = read_tag_definitions(project_root, dept)
    for d in defs:
        if d["id"] == tag_id:
            d["label"] = new_label
            break
    ok = save_tag_definitions(project_root, dept, defs)
    return ok, defs


def recolor_tag_definition(
    project_root: Path,
    department_id: str,
    tag_id: str,
    new_color: str,
) -> tuple[bool, list[dict[str, str]]]:
    """Change color of an existing tag in a department, return (success, updated_defs)."""
    dept = normalize_tag_department_id(department_id)
    defs = read_tag_definitions(project_root, dept)
    for d in defs:
        if d["id"] == tag_id:
            d["color"] = new_color
            break
    ok = save_tag_definitions(project_root, dept, defs)
    return ok, defs


def delete_tag_definition(
    project_root: Path,
    department_id: str,
    tag_id: str,
    item_tags: dict[str, list[str]],
) -> tuple[bool, list[dict[str, str]]]:
    """Delete a tag from a department and remove it from that department's items."""
    dept = normalize_tag_department_id(department_id)
    defs = [d for d in read_tag_definitions(project_root, dept) if d["id"] != tag_id]
    prefix = f"{dept}/"
    for k in list(item_tags.keys()):
        nk = _normalize_key(k)
        if not nk.startswith(prefix):
            continue
        item_tags[nk] = [t for t in item_tags.get(nk, []) if t != tag_id]
        if not item_tags[nk]:
            del item_tags[nk]
    ok = save_tag_definitions(project_root, dept, defs, item_tags)
    return ok, defs


def read_all_tags(project_root: Path) -> dict[str, list[str]]:
    """Read item_tags from JSON. Returns {relative_path: [tag_id, ...]}."""
    data = _load_storage(project_root)
    item_tags = data.get("item_tags")
    if not isinstance(item_tags, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in item_tags.items():
        nk = _normalize_key(k)
        if nk and isinstance(v, list):
            valid_ids = _valid_ids_for_path(project_root, nk)
            tags = [t for t in v if isinstance(t, str) and t in valid_ids]
            if tags:
                out[nk] = tags
    return out


def _write_all_tags(project_root: Path, item_tags: dict[str, list[str]]) -> bool:
    """Write item_tags to JSON (atomic), preserving per-department tag definitions."""
    data = _load_storage(project_root)
    clean: dict[str, list[str]] = {}
    for k, v in item_tags.items():
        nk = _normalize_key(k)
        if not nk or not isinstance(v, list):
            continue
        valid_ids = _valid_ids_for_path(project_root, nk)
        tags = [t for t in v if isinstance(t, str) and t in valid_ids]
        if tags:
            clean[nk] = tags
    data["version"] = 3
    data["item_tags"] = clean
    return _write_raw(project_root, data)


def get_tags_for_item(item_tags: dict[str, list[str]], relative_path: str) -> list[str]:
    """Return tag ids for one item (from cached dict). Empty list if none."""
    return item_tags.get(_normalize_key(relative_path), [])


def set_tags_for_item(
    project_root: Path,
    item_tags: dict[str, list[str]],
    relative_path: str,
    tag_ids: list[str],
) -> bool:
    """Set tags for one item, update cache in-place, and persist to disk."""
    nk = _normalize_key(relative_path)
    if not nk:
        return False
    valid_ids = _valid_ids_for_path(project_root, nk)
    valid = [t for t in tag_ids if t in valid_ids]
    if valid:
        item_tags[nk] = valid
    else:
        item_tags.pop(nk, None)
    return _write_all_tags(project_root, item_tags)


def toggle_tag_for_items(
    project_root: Path,
    item_tags: dict[str, list[str]],
    relative_paths: list[str],
    tag_id: str,
    *,
    department_id: str | None = None,
) -> bool:
    """Toggle a single tag for one or more items. If all have it, remove; otherwise add."""
    dept = normalize_tag_department_id(department_id) if department_id else None
    if dept:
        valid_ids = {d["id"] for d in read_tag_definitions(project_root, dept)}
    else:
        valid_ids = set()
        for rel in relative_paths:
            valid_ids.update(_valid_ids_for_path(project_root, rel))
    if tag_id not in valid_ids:
        return False
    keys = [_normalize_key(p) for p in relative_paths]
    keys = [k for k in keys if k]
    if not keys:
        return False
    all_have = all(tag_id in item_tags.get(k, []) for k in keys)
    for k in keys:
        current = list(item_tags.get(k, []))
        if all_have:
            current = [t for t in current if t != tag_id]
        else:
            if tag_id not in current:
                current.append(tag_id)
        if current:
            item_tags[k] = current
        else:
            item_tags.pop(k, None)
    return _write_all_tags(project_root, item_tags)


def paths_with_tag(
    item_tags: dict[str, list[str]],
    tag_id: str,
    *,
    department_id: str | None = None,
) -> set[str]:
    """Return normalized relative paths that have the given tag, optionally scoped to a department."""
    paths = {k for k, v in item_tags.items() if tag_id in v}
    if department_id:
        prefix = f"{normalize_tag_department_id(department_id)}/"
        paths = {p for p in paths if p.startswith(prefix)}
    return paths


def paths_with_any_tag(
    item_tags: dict[str, list[str]],
    tag_ids: list[str] | set[str],
    *,
    department_id: str | None = None,
) -> set[str]:
    """Union of paths that have any of the given tags (OR filter)."""
    out: set[str] = set()
    for tag_id in tag_ids:
        if tag_id:
            out |= paths_with_tag(item_tags, tag_id, department_id=department_id)
    return out


def ancestor_paths(paths: set[str]) -> set[str]:
    """Given a set of relative paths, return all ancestor prefixes (for tree filter visibility)."""
    ancestors: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            ancestors.add("/".join(parts[:i]))
    return ancestors


def cleanup_stale_keys(
    project_root: Path,
    project_guide_root: Path,
    item_tags: dict[str, list[str]],
) -> bool:
    """Remove keys whose paths no longer exist on disk. Updates cache in-place and persists."""
    stale = [k for k in item_tags if not (project_guide_root / k).exists()]
    if not stale:
        return True
    for k in stale:
        del item_tags[k]
    return _write_all_tags(project_root, item_tags)
