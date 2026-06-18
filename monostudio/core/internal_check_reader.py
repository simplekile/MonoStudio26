"""
Internal check before send (outbox/internal_check, date folders only — no client/freelancer).
Metadata in .monostudio/internal_check_meta.json.
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
    get_outbox_root,
)

INTERNAL_CHECK_SUBFOLDER = "internal_check"
INTERNAL_CHECK_META_FILENAME = "internal_check_meta.json"
_LEGACY_SUBFOLDER = "review"
_LEGACY_META_FILENAME = "review_meta.json"


def _meta_added_at_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate_legacy_review_storage(project_root: Path) -> None:
    """One-time: outbox/review → internal_check, review_meta.json → internal_check_meta.json."""
    root = Path(project_root)
    outbox = get_outbox_root(root)
    legacy_dir = outbox / _LEGACY_SUBFOLDER
    new_dir = outbox / INTERNAL_CHECK_SUBFOLDER
    if legacy_dir.is_dir() and not new_dir.exists():
        try:
            legacy_dir.rename(new_dir)
        except OSError:
            pass
    mono = root / ".monostudio"
    legacy_meta = mono / _LEGACY_META_FILENAME
    new_meta = mono / INTERNAL_CHECK_META_FILENAME
    if legacy_meta.is_file() and not new_meta.is_file():
        try:
            legacy_meta.rename(new_meta)
        except OSError:
            pass


def get_internal_check_root(project_root: Path) -> Path:
    return get_outbox_root(project_root) / INTERNAL_CHECK_SUBFOLDER


def ensure_internal_check_root(project_root: Path) -> None:
    migrate_legacy_review_storage(project_root)
    get_internal_check_root(project_root).mkdir(parents=True, exist_ok=True)


def _meta_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / INTERNAL_CHECK_META_FILENAME


def read_internal_check_meta(project_root: Path) -> dict:
    path = _meta_path(project_root)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_internal_check_meta(project_root: Path, data: dict) -> bool:
    path = _meta_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _build_internal_check_item(
    full_path: Path,
    check_root: Path,
    meta: dict,
    *,
    recurse: bool = True,
) -> InboxItem:
    try:
        rel = full_path.relative_to(check_root)
    except ValueError:
        rel = full_path
    relative_path = rel.as_posix()
    entry_meta = meta.get(relative_path) if isinstance(meta.get(relative_path), dict) else {}
    children: list[InboxItem] = []
    if full_path.is_dir() and recurse:
        try:
            for p in sorted(full_path.iterdir()):
                if p.name.startswith("."):
                    continue
                children.append(_build_internal_check_item(p, check_root, meta, recurse=True))
        except OSError:
            pass
    return InboxItem(
        path=full_path,
        relative_path=relative_path,
        name=full_path.name,
        is_dir=full_path.is_dir(),
        source=None,
        added_at=entry_meta.get(META_KEY_ADDED_AT),
        description=entry_meta.get(META_KEY_DESCRIPTION),
        children=children,
    )


def load_internal_check_history(project_root: Path) -> list[dict]:
    root = get_internal_check_root(project_root)
    meta = read_internal_check_meta(project_root)
    entries: list[dict] = []
    for rel, info in meta.items():
        if not isinstance(info, dict):
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


def scan_internal_check(project_root: Path) -> list[InboxItem]:
    root = get_internal_check_root(project_root)
    if not root.is_dir():
        return []
    meta = read_internal_check_meta(project_root)
    out: list[InboxItem] = []
    try:
        for p in sorted(root.iterdir()):
            if p.name.startswith("."):
                continue
            out.append(_build_internal_check_item(p, root, meta, recurse=True))
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


def move_into_internal_check_folder(project_root: Path, source_path: Path, dest_dir: Path) -> bool:
    check_root = get_internal_check_root(project_root)
    try:
        check_res = check_root.resolve()
        dest_dir = Path(dest_dir).resolve()
        dest_dir.relative_to(check_res)
        source_path = Path(source_path).resolve()
        source_path.relative_to(check_res)
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
    old_rel = source_path.relative_to(check_res).as_posix()
    new_rel = dest_path.relative_to(check_res).as_posix()
    meta = read_internal_check_meta(project_root)
    _relocate_meta_keys(meta, old_rel, new_rel)
    write_internal_check_meta(project_root, meta)
    return True


def copy_into_internal_check_folder(
    project_root: Path,
    source_path: Path,
    dest_dir: Path,
    *,
    description: str | None = None,
) -> bool:
    check_root = get_internal_check_root(project_root)
    try:
        dest_dir = Path(dest_dir).resolve()
        dest_dir.relative_to(check_root.resolve())
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
    relative_path = dest_path.relative_to(check_root).as_posix()
    meta = read_internal_check_meta(project_root)
    meta[relative_path] = {
        META_KEY_ADDED_AT: _meta_added_at_iso(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_internal_check_meta(project_root, meta)
    return True


def add_to_internal_check(
    project_root: Path,
    source_path: Path,
    date_str: str | None,
    description: str | None,
) -> InboxItem | None:
    root = get_internal_check_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    folder_name = resolve_date_folder_name(date_str, project_root=project_root)
    dest_dir = root / folder_name
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
    meta = read_internal_check_meta(project_root)
    meta[relative_path] = {
        META_KEY_ADDED_AT: _meta_added_at_iso(),
        META_KEY_DESCRIPTION: (description or "").strip() or None,
    }
    write_internal_check_meta(project_root, meta)
    return _build_internal_check_item(dest_path, root, meta, recurse=False)
