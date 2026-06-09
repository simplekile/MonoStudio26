"""
Outbox: scan and manage outbox folder (deliverables to client; client/freelancer, source/date structure).
Metadata in .monostudio/outbox_meta.json. Same schema as InboxItem; used for Outbox page (review metadata only).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.inbox_date_folder import resolve_date_folder_name
from monostudio.core.models import InboxItem

OUTBOX_META_FILENAME = "outbox_meta.json"
_OUTBOX_DEFAULT_FOLDER = "outbox"
META_KEY_SOURCE = "source"
META_KEY_ADDED_AT = "added_at"
META_KEY_DESCRIPTION = "description"


def _meta_added_at_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def load_outbox_history(project_root: Path, type_filter: str | None) -> list[dict]:
    """
    Load outbox meta entries for a source type, newest first.
    Each entry: { "path", "relative_path", "added_at", "description" }.
    """
    root = get_outbox_root(project_root)
    meta = read_outbox_meta(project_root)
    want = (type_filter or "").strip().lower() or None
    entries: list[dict] = []
    for rel, info in meta.items():
        if not isinstance(info, dict):
            continue
        source = (info.get(META_KEY_SOURCE) or _infer_source_from_relative_path(rel) or "").strip().lower()
        if want and source != want:
            continue
        full_path = root / rel
        entries.append(
            {
                "path": str(full_path.resolve()) if full_path.exists() else rel,
                "relative_path": rel,
                "added_at": info.get(META_KEY_ADDED_AT) or "",
                "description": info.get(META_KEY_DESCRIPTION) or "",
            }
        )
    entries.sort(key=lambda e: (e.get("added_at") or ""), reverse=True)
    return entries


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


def _relocate_meta_keys(meta: dict, old_rel: str, new_rel: str) -> None:
    old_rel = old_rel.replace("\\", "/").rstrip("/")
    new_rel = new_rel.replace("\\", "/").rstrip("/")
    to_write: list[tuple[str, Any]] = []
    for key in list(meta.keys()):
        kn = key.replace("\\", "/")
        if kn == old_rel or kn.startswith(old_rel + "/"):
            suffix = kn[len(old_rel) :]
            to_write.append((new_rel + suffix, meta.pop(key)))
    for new_key, value in to_write:
        meta[new_key] = value


def move_into_outbox_folder(
    project_root: Path,
    source_path: Path,
    dest_dir: Path,
) -> bool:
    """Move a file/folder into another outbox directory; relocate meta keys."""
    outbox_root = get_outbox_root(project_root)
    try:
        outbox_res = outbox_root.resolve()
        dest_dir = Path(dest_dir).resolve()
        dest_dir.relative_to(outbox_res)
        source_path = Path(source_path).resolve()
        source_path.relative_to(outbox_res)
    except (ValueError, OSError):
        return False
    if not dest_dir.is_dir() or not source_path.exists():
        return False
    if source_path.is_dir():
        try:
            dest_dir.relative_to(source_path)
            return False
        except ValueError:
            pass
    dest_path = dest_dir / source_path.name
    if dest_path == source_path or dest_path.exists():
        return False
    try:
        shutil.move(str(source_path), str(dest_path))
    except OSError:
        return False
    old_rel = source_path.relative_to(outbox_res).as_posix()
    new_rel = dest_path.relative_to(outbox_res).as_posix()
    meta = read_outbox_meta(project_root)
    _relocate_meta_keys(meta, old_rel, new_rel)
    write_outbox_meta(project_root, meta)
    return True


def copy_into_outbox_folder(
    project_root: Path,
    source_path: Path,
    dest_dir: Path,
    *,
    description: str | None = None,
) -> bool:
    """Copy a file/folder into an existing outbox directory; write meta for the new item."""
    outbox_root = get_outbox_root(project_root)
    try:
        dest_dir = Path(dest_dir).resolve()
        dest_dir.relative_to(outbox_root.resolve())
    except (ValueError, OSError):
        return False
    if not dest_dir.is_dir():
        return False
    source_path = Path(source_path)
    if not source_path.exists():
        return False
    dest_path = dest_dir / source_path.name
    if dest_path.exists():
        return False
    try:
        if source_path.is_dir():
            shutil.copytree(source_path, dest_path)
        else:
            shutil.copy2(source_path, dest_path)
    except OSError:
        return False
    try:
        rel_parts = dest_dir.relative_to(outbox_root.resolve()).parts
        source_label = rel_parts[0] if rel_parts else ""
    except ValueError:
        source_label = ""
    relative_path = dest_path.relative_to(outbox_root).as_posix()
    meta = read_outbox_meta(project_root)
    meta[relative_path] = {
        META_KEY_SOURCE: source_label,
        META_KEY_ADDED_AT: _meta_added_at_iso(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_outbox_meta(project_root, meta)
    return True


def add_to_outbox(
    project_root: Path,
    source_path: Path,
    source_label: str,
    date_str: str | None,
    description: str | None,
) -> InboxItem | None:
    """
    Copy source_path (file or folder) into outbox under <source_label>/<date_folder>/.
    date_str: folder name (DDMMYY_suffix, e.g. 260515_Stb) or legacy YYYY-MM-DD.
    Default: today with project suffix. Writes meta for the copied root.
    Returns InboxItem for the new root node, or None on failure.
    """
    root = get_outbox_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    folder_name = resolve_date_folder_name(date_str, project_root=project_root)
    dest_dir = root / source_label / folder_name
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
        META_KEY_ADDED_AT: _meta_added_at_iso(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_outbox_meta(project_root, meta)
    return _build_outbox_item(dest_path, root, meta, recurse=False)
