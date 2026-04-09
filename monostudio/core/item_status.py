"""
Per-item user-defined production status (asset/shot), per department.

Stored in <item_root>/.monostudio/status.json.
Schema v2: { "schema": 2, "by_department": { "<dept_id>": "<status_id>" } }
Legacy: { "status": "ready"|"progress"|"waiting"|"blocked" } — expanded to all
departments when reading with department_names (see read_item_status_overrides).

Writes use atomic write for crash safety.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from monostudio.core.atomic_write import atomic_write_text

STATUS_SCHEMA_V2 = 2

# Legacy single-item file (pre v2)
LEGACY_STATUS_KEYS = frozenset({"ready", "progress", "waiting", "blocked"})

_LEGACY_TO_STATUS_ID = {
    "ready": "published",
    "progress": "working",
    "waiting": "waiting",
    "blocked": "blocked",
}


def _status_json_path(item_root: Path) -> Path:
    return Path(item_root) / ".monostudio" / "status.json"


def _read_raw_status_file(item_root: Path) -> dict | None:
    path = _status_json_path(item_root)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_item_status_overrides(
    item_root: Path,
    department_names: Iterable[str] | None = None,
) -> dict[str, str]:
    """
    Read per-department status_id overrides for an asset/shot folder.

    department_names: when expanding legacy single `status`, apply the mapped
    status_id to every listed department. If None/empty and legacy only, returns {}.
    """
    data = _read_raw_status_file(item_root)
    if not data:
        return {}

    names = [str(n).strip() for n in (department_names or ()) if str(n).strip()]
    name_set = frozenset(names)

    schema = data.get("schema")
    by_dep = data.get("by_department")
    if schema == STATUS_SCHEMA_V2 and isinstance(by_dep, dict):
        out: dict[str, str] = {}
        for k, v in by_dep.items():
            dept = (k or "").strip()
            sid = (v or "").strip()
            if not dept or not sid:
                continue
            if name_set and dept not in name_set:
                continue
            out[dept] = sid
        return out

    # Legacy: top-level "status"
    leg = (data.get("status") or "").strip().lower()
    if leg not in LEGACY_STATUS_KEYS:
        return {}
    mapped = _LEGACY_TO_STATUS_ID.get(leg)
    if not mapped or not names:
        return {}
    return {d: mapped for d in names}


def read_item_status(item_root: Path) -> str | None:
    """
    Back-compat: read legacy single status key only (ready/progress/waiting/blocked).
    Returns None if file is schema v2 or invalid.
    """
    data = _read_raw_status_file(item_root)
    if not data:
        return None
    if data.get("schema") == STATUS_SCHEMA_V2:
        return None
    s = (data.get("status") or "").strip().lower()
    return s if s in LEGACY_STATUS_KEYS else None


def write_item_status(item_root: Path, status: str) -> None:
    """
    Back-compat: write legacy single-status file (overwrites v2 — avoid in new code).
    """
    s = (status or "").strip().lower()
    if s not in LEGACY_STATUS_KEYS:
        raise ValueError(f"Invalid status {status!r}; must be one of {sorted(LEGACY_STATUS_KEYS)}")
    path = _status_json_path(item_root)
    content = json.dumps({"status": s}, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content, encoding="utf-8")


def _load_v2_or_empty(item_root: Path) -> dict:
    data = _read_raw_status_file(item_root)
    if not data:
        return {"schema": STATUS_SCHEMA_V2, "by_department": {}}
    if data.get("schema") == STATUS_SCHEMA_V2 and isinstance(data.get("by_department"), dict):
        return {
            "schema": STATUS_SCHEMA_V2,
            "by_department": dict(data["by_department"]),
        }
    # Migrate legacy → v2 (drop legacy key in output)
    leg = (data.get("status") or "").strip().lower()
    if leg in LEGACY_STATUS_KEYS:
        return {"schema": STATUS_SCHEMA_V2, "by_department": {}}
    return {"schema": STATUS_SCHEMA_V2, "by_department": {}}


def set_department_status_override(
    item_root: Path,
    department: str,
    status_id: str | None,
) -> None:
    """
    Set or clear override for one department. status_id None = automatic (remove key).
    Writes schema v2. Creates .monostudio if needed.
    """
    root = Path(item_root)
    dep = (department or "").strip()
    if not dep:
        raise ValueError("department must be non-empty")

    payload = _load_v2_or_empty(root)
    by_dep: dict[str, str] = dict(payload.get("by_department") or {})

    if status_id is None:
        by_dep.pop(dep, None)
    else:
        sid = (status_id or "").strip()
        if not sid:
            by_dep.pop(dep, None)
        else:
            by_dep[dep] = sid

    payload["schema"] = STATUS_SCHEMA_V2
    payload["by_department"] = by_dep

    path = _status_json_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content, encoding="utf-8")
