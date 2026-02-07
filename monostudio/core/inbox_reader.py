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

INBOX_META_FILENAME = "inbox_meta.json"
INBOX_ROOT_NAME = "inbox"
META_KEY_SOURCE = "source"
META_KEY_ADDED_AT = "added_at"
META_KEY_DESCRIPTION = "description"


def get_inbox_root(project_root: Path) -> Path:
    """Return <project_root>/inbox."""
    return Path(project_root) / INBOX_ROOT_NAME


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
    """Load inbox destination preset from monostudio_data/pipeline/inbox_destinations.json. Returns list of { id, label, path_template, context }."""
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
        if not item.get("id") or not item.get("path_template"):
            continue
        out.append({
            "id": str(item["id"]),
            "label": str(item.get("label") or item["id"]),
            "path_template": str(item["path_template"]),
            "context": str(item.get("context") or "both"),
        })
    return out


def resolve_destination_path(
    project_root: Path,
    destination_id: str,
    entity: Asset | Shot,
) -> Path | None:
    """
    Resolve the target path for distributing an inbox item to a destination (e.g. reference, concept).
    entity: Asset or Shot. Returns entity.path / path_template, or None if destination not applicable.
    """
    destinations = load_inbox_destinations()
    dest = next((d for d in destinations if (d.get("id") or "").strip() == (destination_id or "").strip()), None)
    if not dest:
        return None
    context = (dest.get("context") or "both").strip().lower()
    if isinstance(entity, Asset):
        if context == "shot":
            return None
    elif isinstance(entity, Shot):
        if context == "asset":
            return None
    template = (dest.get("path_template") or "").strip()
    if not template:
        return None
    return (entity.path / template).resolve()
