"""
Delivery: outbound deliverables to client/freelancer (outbox/delivery/<recipient>/<date>/).
Metadata in .monostudio/delivery_meta.json.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.inbox_date_folder import resolve_date_folder_name
from monostudio.core.models import InboxItem
from monostudio.core.outbox_reader import (
    META_KEY_ADDED_AT,
    META_KEY_DESCRIPTION,
    META_KEY_SOURCE,
    ensure_source_folders,
    get_outbox_root,
)

DELIVERY_SUBFOLDER = "delivery"
DELIVERY_META_FILENAME = "delivery_meta.json"


def _meta_added_at_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_delivery_root(project_root: Path) -> Path:
    """Return <outbox_root>/delivery."""
    return get_outbox_root(project_root) / DELIVERY_SUBFOLDER


def ensure_delivery_source_folders(project_root: Path) -> None:
    ensure_source_folders(get_delivery_root(project_root))


def _meta_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / DELIVERY_META_FILENAME


def read_delivery_meta(project_root: Path) -> dict:
    path = _meta_path(project_root)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_delivery_meta(project_root: Path, data: dict) -> bool:
    path = _meta_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _infer_source_from_relative_path(relative_path: str) -> str | None:
    parts = (relative_path or "").strip().replace("\\", "/").strip("/").split("/")
    if not parts:
        return None
    first = parts[0].lower()
    if first == "client":
        return "client"
    if first == "freelancer":
        return "freelancer"
    return None


def infer_delivery_source_from_path(project_root: Path, path: Path) -> str | None:
    """Infer client/freelancer from delivery/<source>/… path or meta."""
    root = get_delivery_root(project_root)
    try:
        rel = Path(path).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    rel_str = rel.as_posix()
    from_path = _infer_source_from_relative_path(rel_str)
    if from_path in ("client", "freelancer"):
        return from_path
    meta = read_delivery_meta(project_root)
    entry = meta.get(rel_str) if isinstance(meta.get(rel_str), dict) else None
    if entry is not None:
        source = (entry.get(META_KEY_SOURCE) or "").strip().lower()
        if source in ("client", "freelancer"):
            return source
    return None


def resolve_delivery_location(project_root: Path, path: Path) -> Path | None:
    """Return date-folder path for a delivery item (delivery/<source>/<date>/…)."""
    root = get_delivery_root(project_root)
    try:
        rel = Path(path).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    parts = rel.parts
    if not parts:
        return None
    first = parts[0].lower()
    if first in ("client", "freelancer") and len(parts) >= 2:
        date_folder = (root / parts[0] / parts[1]).resolve()
    else:
        date_folder = (root / parts[0]).resolve()
    if not date_folder.is_dir():
        return None
    return date_folder


def _build_delivery_item(
    full_path: Path,
    delivery_root: Path,
    meta: dict,
    *,
    recurse: bool = True,
) -> InboxItem:
    try:
        rel = full_path.relative_to(delivery_root)
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
                children.append(_build_delivery_item(p, delivery_root, meta, recurse=True))
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


def load_delivery_history(project_root: Path, type_filter: str | None) -> list[dict]:
    root = get_delivery_root(project_root)
    meta = read_delivery_meta(project_root)
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


def scan_delivery(project_root: Path) -> list[InboxItem]:
    root = get_delivery_root(project_root)
    if not root.is_dir():
        return []
    meta = read_delivery_meta(project_root)
    out: list[InboxItem] = []
    try:
        for p in sorted(root.iterdir()):
            if p.name.startswith("."):
                continue
            out.append(_build_delivery_item(p, root, meta, recurse=True))
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


def move_into_delivery_folder(
    project_root: Path,
    source_path: Path,
    dest_dir: Path,
) -> bool:
    delivery_root = get_delivery_root(project_root)
    try:
        delivery_res = delivery_root.resolve()
        dest_dir = Path(dest_dir).resolve()
        dest_dir.relative_to(delivery_res)
        source_path = Path(source_path).resolve()
        source_path.relative_to(delivery_res)
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
    old_rel = source_path.relative_to(delivery_res).as_posix()
    new_rel = dest_path.relative_to(delivery_res).as_posix()
    meta = read_delivery_meta(project_root)
    _relocate_meta_keys(meta, old_rel, new_rel)
    write_delivery_meta(project_root, meta)
    return True


def copy_into_delivery_folder(
    project_root: Path,
    source_path: Path,
    dest_dir: Path,
    *,
    description: str | None = None,
) -> bool:
    delivery_root = get_delivery_root(project_root)
    try:
        dest_dir = Path(dest_dir).resolve()
        dest_dir.relative_to(delivery_root.resolve())
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
        rel_parts = dest_dir.relative_to(delivery_root.resolve()).parts
        source_label = rel_parts[0] if rel_parts else ""
    except ValueError:
        source_label = ""
    relative_path = dest_path.relative_to(delivery_root).as_posix()
    meta = read_delivery_meta(project_root)
    meta[relative_path] = {
        META_KEY_SOURCE: source_label,
        META_KEY_ADDED_AT: _meta_added_at_iso(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_delivery_meta(project_root, meta)
    return True


def add_to_delivery(
    project_root: Path,
    source_path: Path,
    source_label: str,
    date_str: str | None,
    description: str | None,
) -> InboxItem | None:
    root = get_delivery_root(project_root)
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
    meta = read_delivery_meta(project_root)
    meta[relative_path] = {
        META_KEY_SOURCE: source_label,
        META_KEY_ADDED_AT: _meta_added_at_iso(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_delivery_meta(project_root, meta)
    return _build_delivery_item(dest_path, root, meta, recurse=False)
