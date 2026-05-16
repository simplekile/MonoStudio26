"""
Project-scoped trash: move asset/shot folders under ``.monostudio/trash/`` with a JSON manifest.
Restore moves back to ``original_relative``; permanent delete removes from disk. Optional retention purge.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.atomic_write import atomic_write_text
from monostudio.core.structure_registry import StructureRegistry

logger = logging.getLogger(__name__)

TRASH_DIRNAME = "trash"
MANIFEST_FILENAME = "trash_manifest.json"


@dataclass(frozen=True)
class TrashEntry:
    id: str
    kind: str  # "asset" | "shot"
    original_relative: str  # posix, relative to project root
    original_name: str
    type_folder: str | None  # asset only: physical type directory under assets/
    trashed_at: str  # ISO8601 (stored as UTC with Z in manifest; UI shows local time)


class TrashError(Exception):
    pass


def _monostudio_dir(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio"


def trash_root(project_root: Path) -> Path:
    return _monostudio_dir(project_root) / TRASH_DIRNAME


def manifest_path(project_root: Path) -> Path:
    return _monostudio_dir(project_root) / MANIFEST_FILENAME


def _read_manifest(project_root: Path) -> dict[str, dict[str, Any]]:
    p = manifest_path(project_root)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("entries")
    if isinstance(entries, dict):
        out: dict[str, dict[str, Any]] = {}
        for k, v in entries.items():
            if isinstance(k, str) and k.strip() and isinstance(v, dict):
                out[k.strip()] = v
        return out
    return {}


def _write_manifest_entries(project_root: Path, entries: dict[str, dict[str, Any]]) -> None:
    payload = {"schema": 1, "entries": entries}
    atomic_write_text(manifest_path(project_root), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_trashed_at_local(iso_timestamp: str) -> str:
    """
    Format manifest ``trashed_at`` for display using the **system local** timezone.
    Accepts ISO strings ending in ``Z`` or with an explicit offset; naive values are treated as UTC.
    """
    raw = (iso_timestamp or "").strip()
    if not raw:
        return ""
    try:
        s = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        local_dt = dt.astimezone()
    except (OSError, ValueError, OverflowError):
        return raw
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")


def _safe_trash_id_suffix(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", (name or "").strip(), flags=re.UNICODE)
    s = s.strip("._") or "item"
    return s[:120]


def _new_trash_folder_id(project_root: Path, display_name: str) -> str:
    # Prefix uses local wall time so folder names match the user's clock (no ``:`` for Windows paths).
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    base = f"{stamp}__{_safe_trash_id_suffix(display_name)}"
    root = trash_root(project_root)
    cand = base
    for _ in range(50):
        if not (root / cand).exists():
            return cand
        cand = f"{base}__{secrets.token_hex(3)}"
    return f"{base}__{secrets.token_hex(8)}"


def list_entries(project_root: Path) -> list[TrashEntry]:
    raw = _read_manifest(project_root)
    troot = trash_root(project_root)
    result: list[TrashEntry] = []
    for tid, meta in sorted(raw.items(), key=lambda kv: (kv[1].get("trashed_at") or "", kv[0])):
        if not (troot / tid).is_dir():
            continue
        kind = (meta.get("kind") or "").strip().lower()
        if kind not in ("asset", "shot"):
            continue
        rel = (meta.get("original_relative") or "").strip().replace("\\", "/")
        if not rel:
            continue
        oname = (meta.get("original_name") or Path(rel).name).strip() or Path(rel).name
        tf = meta.get("type_folder")
        type_folder = tf.strip() if isinstance(tf, str) and tf.strip() else None
        ts = (meta.get("trashed_at") or "").strip() or _utc_now_iso()
        result.append(
            TrashEntry(
                id=tid,
                kind=kind,
                original_relative=rel,
                original_name=oname,
                type_folder=type_folder,
                trashed_at=ts,
            )
        )
    return result


def _validate_pipeline_path(project_root: Path, path: Path, kind: str) -> tuple[str, str | None]:
    root = project_root.resolve()
    try:
        p = path.resolve()
    except OSError as e:
        raise TrashError(f"Cannot resolve path: {e}") from e
    struct = StructureRegistry.for_project(root)
    assets_name = struct.get_folder("assets")
    shots_name = struct.get_folder("shots")
    assets_dir = root / assets_name
    shots_dir = root / shots_name
    if kind == "asset":
        try:
            rel = p.relative_to(assets_dir)
        except ValueError as e:
            raise TrashError("Path is not under the project assets folder.") from e
        parts = rel.parts
        if len(parts) < 2:
            raise TrashError("Asset path must be assets/<type>/<name>.")
        type_folder, name = parts[0], parts[1]
        if p != assets_dir / type_folder / name:
            raise TrashError("Only the asset root folder can be moved to trash (not a subfolder).")
        original_relative = "/".join([assets_name, type_folder, name])
        return original_relative, type_folder
    if kind == "shot":
        try:
            rel = p.relative_to(shots_dir)
        except ValueError as e:
            raise TrashError("Path is not under the project shots folder.") from e
        parts = rel.parts
        if len(parts) != 1:
            raise TrashError("Shot path must be shots/<name>.")
        name = parts[0]
        original_relative = "/".join([shots_name, name])
        return original_relative, None
    raise TrashError(f"Unknown kind: {kind!r}")


def move_asset_or_shot_to_trash(project_root: Path, item_path: Path, kind: str) -> TrashEntry:
    """
    Move ``item_path`` into ``.monostudio/trash/<id>/``. Updates manifest.
    Raises TrashError on invalid path or IO errors.
    """
    root = project_root.resolve()
    original_relative, type_folder = _validate_pipeline_path(root, item_path, kind)
    name = Path(original_relative).name
    if not item_path.is_dir():
        raise TrashError("Only existing directories can be moved to trash.")

    mono = _monostudio_dir(root)
    troot = trash_root(root)
    mono.mkdir(parents=True, exist_ok=True)
    troot.mkdir(parents=True, exist_ok=True)

    tid = _new_trash_folder_id(root, name)
    dest = troot / tid
    if dest.exists():
        raise TrashError("Trash destination already exists; retry.")

    entries = _read_manifest(root)
    if tid in entries:
        raise TrashError("Manifest collision; retry.")

    try:
        item_path.rename(dest)
    except OSError as e:
        raise TrashError(f"Move to trash failed: {e}") from e

    trashed_at = _utc_now_iso()
    entries[tid] = {
        "kind": kind,
        "original_relative": original_relative,
        "original_name": name,
        "type_folder": type_folder,
        "trashed_at": trashed_at,
    }
    _write_manifest_entries(root, entries)
    return TrashEntry(
        id=tid,
        kind=kind,
        original_relative=original_relative,
        original_name=name,
        type_folder=type_folder,
        trashed_at=trashed_at,
    )


def restore_trash_entry(project_root: Path, entry_id: str) -> Path:
    """Move trashed folder back to ``original_relative``. Raises TrashError if target exists."""
    root = project_root.resolve()
    entries = _read_manifest(root)
    tid = (entry_id or "").strip()
    meta = entries.get(tid)
    if not isinstance(meta, dict):
        raise TrashError("Trash entry not found.")
    src = trash_root(root) / tid
    if not src.is_dir():
        raise TrashError("Trashed folder is missing on disk.")

    rel = (meta.get("original_relative") or "").strip().replace("\\", "/")
    if not rel or ".." in rel.split("/"):
        raise TrashError("Invalid original path in manifest.")
    target = (root / Path(*rel.split("/"))).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise TrashError("Restore path escapes project root.") from e
    if target.exists():
        raise TrashError(f"Cannot restore: destination already exists:\n{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(target)
    except OSError as e:
        raise TrashError(f"Restore failed: {e}") from e
    del entries[tid]
    _write_manifest_entries(root, entries)
    return target


def delete_trash_entry_permanently(project_root: Path, entry_id: str) -> None:
    root = project_root.resolve()
    entries = _read_manifest(root)
    tid = (entry_id or "").strip()
    if tid not in entries:
        raise TrashError("Trash entry not found.")
    src = trash_root(root) / tid
    if src.is_dir():
        shutil.rmtree(src)
    elif src.exists():
        try:
            src.unlink()
        except OSError:
            pass
    del entries[tid]
    _write_manifest_entries(root, entries)


def empty_trash(project_root: Path) -> int:
    """Permanently delete all trashed folders. Returns count removed."""
    entries = _read_manifest(project_root.resolve())
    n = 0
    for tid in list(entries.keys()):
        delete_trash_entry_permanently(project_root, tid)
        n += 1
    return n


def purge_expired(project_root: Path, retention_days: int, *, now: datetime | None = None) -> int:
    """
    Permanently delete manifest entries older than ``retention_days`` (>= 1).
    Returns number of entries purged.
    """
    if retention_days < 1:
        return 0
    root = project_root.resolve()
    now = now or datetime.now(timezone.utc)
    removed = 0
    for entry in list_entries(root):
        try:
            ts = entry.trashed_at.replace("Z", "+00:00")
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        age = now - t.astimezone(timezone.utc)
        if age.days >= retention_days:
            try:
                delete_trash_entry_permanently(root, entry.id)
                removed += 1
                logger.info(
                    "trash_purge expired_retention id=%s trashed_at=%s retention_days=%s",
                    entry.id,
                    entry.trashed_at,
                    retention_days,
                )
            except (TrashError, OSError) as e:
                logger.warning("trash_purge failed id=%s: %s", entry.id, e)
    return removed


def retention_days_from_settings(settings: object, *, default: int = 30) -> int:
    """Read ``trash/retention_days`` from QSettings-like object."""
    try:
        v = settings.value("trash/retention_days", default)  # type: ignore[attr-defined]
    except Exception:
        return default
    if isinstance(v, int) and not isinstance(v, bool):
        return max(1, min(3650, v))
    if isinstance(v, str) and v.strip().isdigit():
        return max(1, min(3650, int(v.strip())))
    return default
