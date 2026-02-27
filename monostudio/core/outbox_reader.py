"""
Outbox: scan and manage outbox folder (deliverables to client; client/freelancer, source/date structure).
Metadata in .monostudio/outbox_meta.json. Same schema as InboxItem; used for Outbox page (review metadata only).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from monostudio.core.models import InboxItem

OUTBOX_META_FILENAME = "outbox_meta.json"
_OUTBOX_DEFAULT_FOLDER = "outbox"
META_KEY_SOURCE = "source"
META_KEY_ADDED_AT = "added_at"
META_KEY_DESCRIPTION = "description"


def get_outbox_root(project_root: Path) -> Path:
    """Return <project_root>/<outbox_folder> using StructureRegistry."""
    from monostudio.core.structure_registry import StructureRegistry
    struct_reg = StructureRegistry.for_project(project_root)
    return Path(project_root) / struct_reg.get_folder("outbox")


def _meta_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / OUTBOX_META_FILENAME


def read_outbox_meta(project_root: Path) -> dict:
    """Read .monostudio/outbox_meta.json. Keys are relative paths from outbox root; values are { source, added_at, description }."""
    path = _meta_path(project_root)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_outbox_meta(project_root: Path, data: dict) -> bool:
    """Write .monostudio/outbox_meta.json."""
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


def _build_outbox_item(
    full_path: Path,
    outbox_root: Path,
    meta: dict,
    *,
    recurse: bool = True,
) -> InboxItem:
    """Build InboxItem (same schema) for outbox path."""
    try:
        rel = full_path.relative_to(outbox_root)
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
                children.append(_build_outbox_item(p, outbox_root, meta, recurse=True))
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


def scan_outbox(project_root: Path) -> list[InboxItem]:
    """
    Scan outbox folder recursively. Returns top-level nodes (client/, freelancer/, or direct children).
    Each node has children populated for directories. Uses InboxItem (same schema).
    """
    root = get_outbox_root(project_root)
    if not root.is_dir():
        return []
    meta = read_outbox_meta(project_root)
    out: list[InboxItem] = []
    try:
        for p in sorted(root.iterdir()):
            if p.name.startswith("."):
                continue
            out.append(_build_outbox_item(p, root, meta, recurse=True))
    except OSError:
        pass
    return out


def add_to_outbox(
    project_root: Path,
    source_path: Path,
    source_label: str,
    date_str: str | None,
    description: str | None,
) -> InboxItem | None:
    """
    Copy source_path (file or folder) into outbox under <source_label>/<date_str>/.
    date_str default: today YYYY-MM-DD. Writes meta for the copied root.
    Returns InboxItem for the new root node, or None on failure.
    """
    root = get_outbox_root(project_root)
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
    meta = read_outbox_meta(project_root)
    meta[relative_path] = {
        META_KEY_SOURCE: source_label,
        META_KEY_ADDED_AT: datetime.now().isoformat(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_outbox_meta(project_root, meta)
    return _build_outbox_item(dest_path, root, meta, recurse=False)
