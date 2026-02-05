"""
Per-item user-defined status (asset/shot).
Stored in <item_root>/.monostudio/status.json. Overrides computed status when set.
Writes use atomic write for crash safety.
"""
from __future__ import annotations

import json
from pathlib import Path

from monostudio.core.atomic_write import atomic_write_text

VALID_STATUSES = frozenset({"ready", "progress", "waiting", "blocked"})


def read_item_status(item_root: Path) -> str | None:
    """
    Read user-set status for an item. Returns None if not set or invalid.
    """
    path = Path(item_root) / ".monostudio" / "status.json"
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    s = (data.get("status") or "").strip().lower()
    return s if s in VALID_STATUSES else None


def write_item_status(item_root: Path, status: str) -> None:
    """
    Write user-set status. status must be one of ready, progress, waiting, blocked.
    Creates .monostudio dir if needed. Raises ValueError if status invalid.
    """
    s = (status or "").strip().lower()
    if s not in VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}; must be one of {sorted(VALID_STATUSES)}")
    path = Path(item_root) / ".monostudio" / "status.json"
    content = json.dumps({"status": s}, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content, encoding="utf-8")
