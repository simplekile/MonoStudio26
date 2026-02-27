"""
Inbox: scan and manage inbox folder (client/freelancer, source/date structure).
Metadata in .monostudio/inbox_meta.json. No dialogs; UI uses this for split view and mapping list.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.models import Asset, InboxItem, Shot
from monostudio.core.structure_registry import StructureRegistry

INBOX_META_FILENAME = "inbox_meta.json"
_INBOX_DEFAULT_FOLDER = "inbox"
META_KEY_SOURCE = "source"
META_KEY_ADDED_AT = "added_at"
META_KEY_DESCRIPTION = "description"


def get_inbox_root(project_root: Path) -> Path:
    """Return <project_root>/<inbox_folder> using StructureRegistry."""
    from monostudio.core.structure_registry import StructureRegistry
    struct_reg = StructureRegistry.for_project(project_root)
    return Path(project_root) / struct_reg.get_folder("inbox")


def _meta_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / INBOX_META_FILENAME


def read_inbox_meta(project_root: Path) -> dict:
    """Read .monostudio/inbox_meta.json. Keys are relative paths from inbox root; values are { source, added_at, description }."""
    path = _meta_path(project_root)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_inbox_meta(project_root: Path, data: dict) -> bool:
    """Write .monostudio/inbox_meta.json. Merges with existing if needed; caller can pass full dict."""
    path = _meta_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _infer_source_from_relative_path(relative_path: str) -> str | None:
    """Infer client/freelancer from path like client/2025-02-07/... or freelancer/..."""
    parts = (relative_path or "").strip().replace("\\", "/").strip("/").split("/")
    if not parts:
        return None
    first = parts[0].lower()
    if first == "client":
        return "client"
    if first == "freelancer":
        return "freelancer"
    return None


def _build_inbox_item(
    full_path: Path,
    inbox_root: Path,
    meta: dict,
    *,
    recurse: bool = True,
) -> InboxItem:
    try:
        rel = full_path.relative_to(inbox_root)
    except ValueError:
        rel = full_path
    relative_path = rel.as_posix()
    name = full_path.name
    is_dir = full_path.is_dir()
    entry_meta = meta.get(relative_path) if isinstance(meta.get(relative_path), dict) else {}
    source = entry_meta.get(META_KEY_SOURCE) or _infer_source_from_relative_path(relative_path)
    added_at = entry_meta.get(META_KEY_ADDED_AT)
    description = entry_meta.get(META_KEY_DESCRIPTION)

    children: list[InboxItem] = []
    if is_dir and recurse:
        try:
            for p in sorted(full_path.iterdir()):
                if p.name.startswith("."):
                    continue
                children.append(_build_inbox_item(p, inbox_root, meta, recurse=True))
        except OSError:
            pass

    return InboxItem(
        path=full_path,
        relative_path=relative_path,
        name=name,
        is_dir=is_dir,
        source=source,
        added_at=added_at,
        description=description,
        children=children,
    )


def scan_inbox(project_root: Path) -> list[InboxItem]:
    """
    Scan inbox folder recursively. Returns top-level nodes (client/, freelancer/, or direct children).
    Each node has children populated for directories.
    """
    root = get_inbox_root(project_root)
    if not root.is_dir():
        return []
    meta = read_inbox_meta(project_root)
    out: list[InboxItem] = []
    try:
        for p in sorted(root.iterdir()):
            if p.name.startswith("."):
                continue
            out.append(_build_inbox_item(p, root, meta, recurse=True))
    except OSError:
        pass
    return out


def add_to_inbox(
    project_root: Path,
    source_path: Path,
    source_label: str,
    date_str: str | None,
    description: str | None,
) -> InboxItem | None:
    """
    Copy source_path (file or folder) into inbox under <source_label>/<date_str>/.
    date_str default: today YYYY-MM-DD. Writes meta for the copied root.
    Returns InboxItem for the new root node, or None on failure.
    """
    root = get_inbox_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    if not date_str or not date_str.strip():
        date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        date_str = date_str.strip()
    dest_dir = root / source_label / date_str
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source_path.name
    if dest_path.exists():
        return None
    try:
        if source_path.is_dir():
            shutil.copytree(source_path, dest_path)
        else:
            shutil.copy2(source_path, dest_path)
    except OSError:
        return None
    relative_path = dest_path.relative_to(root).as_posix()
    meta = read_inbox_meta(project_root)
    meta[relative_path] = {
        META_KEY_SOURCE: source_label,
        META_KEY_ADDED_AT: datetime.now().isoformat(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_inbox_meta(project_root, meta)
    return _build_inbox_item(dest_path, root, meta, recurse=False)


def remove_from_inbox(project_root: Path, relative_key: str) -> bool:
    """Remove file or folder from inbox and delete its meta entry. Returns True on success."""
    root = get_inbox_root(project_root)
    target = root / relative_key.replace("\\", "/")
    if not target.exists():
        meta = read_inbox_meta(project_root)
        meta.pop(relative_key, None)
        meta.pop(relative_key.replace("\\", "/"), None)
        write_inbox_meta(project_root, meta)
        return True
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError:
        return False
    meta = read_inbox_meta(project_root)
    meta.pop(relative_key, None)
    meta.pop(relative_key.replace("\\", "/"), None)
    write_inbox_meta(project_root, meta)
    return True


# ---- Destination preset (inbox_destinations.json) ----

_INBOX_DESTINATIONS_FILENAME = "inbox_destinations.json"


def _inbox_destinations_path() -> Path:
    return get_app_base_path() / "monostudio_data" / "pipeline" / _INBOX_DESTINATIONS_FILENAME


def load_inbox_destinations() -> list[dict[str, Any]]:
    """Load inbox destination preset from monostudio_data/pipeline/inbox_destinations.json.

    Each entry has { id, label, context } plus either:
    - path_template (literal relative path), or
    - dept_id + optional subfolder (resolved via DepartmentRegistry at distribute time).
    """
    path = _inbox_destinations_path()
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("id"):
            continue
        has_template = bool(item.get("path_template"))
        has_dept = bool(item.get("dept_id"))
        if not has_template and not has_dept:
            continue
        row: dict[str, Any] = {
            "id": str(item["id"]),
            "label": str(item.get("label") or item["id"]),
            "context": str(item.get("context") or "both"),
        }
        if has_dept:
            row["dept_id"] = str(item["dept_id"])
            subfolder = item.get("subfolder")
            if isinstance(subfolder, str) and subfolder.strip():
                row["subfolder"] = subfolder.strip()
        if has_template:
            row["path_template"] = str(item["path_template"])
        out.append(row)
    return out


def resolve_destination_path(
    project_root: Path,
    destination_id: str,
    entity: Asset | Shot | None,
    dept_registry: Any | None = None,
) -> Path | None:
    """
    Resolve the target path for distributing an inbox item to a destination.

    When the destination has ``dept_id``, the real folder path is resolved
    via *dept_registry* (DepartmentRegistry) so it always matches the
    project's actual department folder names on disk (e.g. ``03_surfacing/02_texturing``).
    An optional ``subfolder`` is appended after the department path.

    Falls back to ``path_template`` (literal) when ``dept_id`` is absent or
    the registry cannot resolve the department.
    """
    destinations = load_inbox_destinations()
    dest = next((d for d in destinations if (d.get("id") or "").strip() == (destination_id or "").strip()), None)
    if not dest:
        return None

    context = (dest.get("context") or "both").strip().lower()

    if context == "project":
        template = (dest.get("path_template") or "").strip()
        if not template:
            return None
        # Resolve first segment via StructureRegistry so project_guide -> _project_guide etc.
        struct_reg = StructureRegistry.for_project(project_root)
        parts = [p for p in template.split("/") if p]
        if parts and parts[0] in struct_reg.get_ids():
            parts[0] = struct_reg.get_folder(parts[0])
        base = Path(project_root)
        for p in parts:
            base = base / p
        return base.resolve()

    if entity is None:
        return None
    if isinstance(entity, Asset) and context == "shot":
        return None
    if isinstance(entity, Shot) and context == "asset":
        return None

    dept_id = (dest.get("dept_id") or "").strip()
    if dept_id and dept_registry is not None:
        dept_context = "shot" if isinstance(entity, Shot) else "asset"
        rel_path = ""
        if hasattr(dept_registry, "get_department_relative_path"):
            rel_path = dept_registry.get_department_relative_path(dept_id, dept_context)
        if rel_path:
            base = entity.path / rel_path
            subfolder = (dest.get("subfolder") or "").strip()
            if subfolder:
                base = base / subfolder
            return base.resolve()

    template = (dest.get("path_template") or "").strip()
    if not template:
        return None
    return (entity.path / template).resolve()


# --- Distributed files persistence (for Inbox mapping "Distributed" list) ---

INBOX_DISTRIBUTED_FILENAME = "inbox_distributed.json"


def _distributed_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / INBOX_DISTRIBUTED_FILENAME


def load_inbox_distributed(project_root: Path, type_filter: str | None) -> list[dict]:
    """
    Load list of distributed entries for a source type.
    type_filter: "client" | "freelancer" | None (returns all, merged).
    Each entry: { "path", "distributed_at" (ISO8601), optional "destination_id", "destination_label",
    "scope", "entity_name", "target_path" }.
    """
    path = _distributed_path(project_root)
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    key = (type_filter or "").strip().lower()
    if key in ("client", "freelancer"):
        return list(data.get(key, [])) if isinstance(data.get(key), list) else []
    out: list[dict] = []
    for k in ("client", "freelancer"):
        part = data.get(k)
        if isinstance(part, list):
            out.extend(part)
    return out


def save_inbox_distributed(project_root: Path, data: dict[str, list]) -> bool:
    """
    Save full distributed map: { "client": [ {...}, ... ], "freelancer": [ ... ] }.
    """
    path = _distributed_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def append_inbox_distributed(
    project_root: Path,
    type_filter: str,
    entry: dict,
) -> bool:
    """
    Append one distributed entry for type_filter ("client" | "freelancer").
    entry: { "path", "distributed_at", optional "destination_id", "destination_label",
    "scope", "entity_name", "target_path" }. Extra keys are preserved when saving.
    """
    path = _distributed_path(project_root)
    data: dict[str, list] = {}
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in ("client", "freelancer"):
                    v = raw.get(k)
                    data[k] = list(v) if isinstance(v, list) else []
        for k in ("client", "freelancer"):
            if k not in data:
                data[k] = []
    except (OSError, json.JSONDecodeError):
        data = {"client": [], "freelancer": []}
    key = (type_filter or "client").strip().lower()
    if key not in ("client", "freelancer"):
        key = "client"
    data[key].append(entry)
    return save_inbox_distributed(project_root, data)
