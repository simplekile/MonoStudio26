"""
Tag system for Project Guide items (macOS-style colored tags).
Storage: <project_root>/.monostudio/project_guide_tags.json
Keys in item_tags are relative paths from project_guide root (forward-slash separated).
Tag definitions are stored per-project; defaults are used when none exist.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from monostudio.core.atomic_write import atomic_write_text

_TAGS_FILENAME = "project_guide_tags.json"

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


def build_color_map(defs: list[dict[str, str]]) -> dict[str, str]:
    return {t["id"]: t["color"] for t in defs if "id" in t and "color" in t}


def build_label_map(defs: list[dict[str, str]]) -> dict[str, str]:
    return {t["id"]: t["label"] for t in defs if "id" in t and "label" in t}


def read_tag_definitions(project_root: Path) -> list[dict[str, str]]:
    """Read tag definitions from JSON. Falls back to DEFAULT_TAG_DEFINITIONS."""
    data = _read_raw(project_root)
    raw_defs = data.get("tag_definitions")
    if isinstance(raw_defs, list) and raw_defs:
        out: list[dict[str, str]] = []
        for d in raw_defs:
            if isinstance(d, dict) and d.get("id") and d.get("color") and d.get("label"):
                out.append({"id": d["id"], "color": d["color"], "label": d["label"]})
        if out:
            return out
    return list(DEFAULT_TAG_DEFINITIONS)


def save_tag_definitions(
    project_root: Path,
    defs: list[dict[str, str]],
    item_tags: dict[str, list[str]] | None = None,
) -> bool:
    """Save tag definitions (and optionally item_tags) to JSON."""
    data = _read_raw(project_root)
    data["version"] = 2
    data["tag_definitions"] = defs
    if item_tags is not None:
        valid_ids = {d["id"] for d in defs}
        clean: dict[str, list[str]] = {}
        for k, v in item_tags.items():
            nk = _normalize_key(k)
            tags = [t for t in v if isinstance(t, str) and t in valid_ids]
            if nk and tags:
                clean[nk] = tags
        data["item_tags"] = clean
    return _write_raw(project_root, data)


def add_tag_definition(
    project_root: Path, label: str, color: str,
) -> tuple[bool, list[dict[str, str]]]:
    """Add a new tag, return (success, updated_defs)."""
    defs = read_tag_definitions(project_root)
    new_id = f"tag_{uuid.uuid4().hex[:8]}"
    defs.append({"id": new_id, "color": color, "label": label})
    ok = save_tag_definitions(project_root, defs)
    return ok, defs


def rename_tag_definition(
    project_root: Path, tag_id: str, new_label: str,
) -> tuple[bool, list[dict[str, str]]]:
    """Rename an existing tag, return (success, updated_defs)."""
    defs = read_tag_definitions(project_root)
    for d in defs:
        if d["id"] == tag_id:
            d["label"] = new_label
            break
    ok = save_tag_definitions(project_root, defs)
    return ok, defs


def recolor_tag_definition(
    project_root: Path, tag_id: str, new_color: str,
) -> tuple[bool, list[dict[str, str]]]:
    """Change color of an existing tag, return (success, updated_defs)."""
    defs = read_tag_definitions(project_root)
    for d in defs:
        if d["id"] == tag_id:
            d["color"] = new_color
            break
    ok = save_tag_definitions(project_root, defs)
    return ok, defs


def delete_tag_definition(
    project_root: Path, tag_id: str, item_tags: dict[str, list[str]],
) -> tuple[bool, list[dict[str, str]]]:
    """Delete a tag, remove it from all items, return (success, updated_defs)."""
    defs = [d for d in read_tag_definitions(project_root) if d["id"] != tag_id]
    for k in list(item_tags.keys()):
        item_tags[k] = [t for t in item_tags[k] if t != tag_id]
        if not item_tags[k]:
            del item_tags[k]
    ok = save_tag_definitions(project_root, defs, item_tags)
    return ok, defs


def read_all_tags(project_root: Path) -> dict[str, list[str]]:
    """Read item_tags from JSON. Returns {relative_path: [tag_id, ...]}."""
    data = _read_raw(project_root)
    defs = read_tag_definitions(project_root)
    valid_ids = {d["id"] for d in defs}
    item_tags = data.get("item_tags")
    if not isinstance(item_tags, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in item_tags.items():
        nk = _normalize_key(k)
        if nk and isinstance(v, list):
            out[nk] = [t for t in v if isinstance(t, str) and t in valid_ids]
    return out


def _write_all_tags(project_root: Path, item_tags: dict[str, list[str]]) -> bool:
    """Write item_tags to JSON (atomic), preserving existing tag_definitions."""
    data = _read_raw(project_root)
    defs = read_tag_definitions(project_root)
    valid_ids = {d["id"] for d in defs}
    clean: dict[str, list[str]] = {}
    for k, v in item_tags.items():
        nk = _normalize_key(k)
        tags = [t for t in v if isinstance(t, str) and t in valid_ids]
        if nk and tags:
            clean[nk] = tags
    data["version"] = 2
    data["tag_definitions"] = defs
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
    defs = read_tag_definitions(project_root)
    valid_ids = {d["id"] for d in defs}
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
) -> bool:
    """Toggle a single tag for one or more items. If all have it, remove; otherwise add."""
    defs = read_tag_definitions(project_root)
    valid_ids = {d["id"] for d in defs}
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


def paths_with_tag(item_tags: dict[str, list[str]], tag_id: str) -> set[str]:
    """Return set of normalized relative paths that have the given tag."""
    return {k for k, v in item_tags.items() if tag_id in v}


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
