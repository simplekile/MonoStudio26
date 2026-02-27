"""
Safe pending-create tracking for DCC file creation.

- In-memory only; no persistent storage.
- Records intent to create so UI can show "creating" and we avoid false "new" from immediate scan.
- Cleared when filesystem event or bounded fallback scan completes.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger("monostudio.dcc_debug")

# (entity_id, department, dcc) -> timestamp
_pending: dict[tuple[str, str, str], float] = {}


def _norm_path(p: str) -> Path | None:
    """Resolve path for comparison; return None on error."""
    try:
        return Path(p or "").expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def add(entity_id: str, department: str, dcc: str) -> None:
    """Record that a DCC file create was triggered for this (entity, department, dcc)."""
    e = (entity_id or "").strip()
    d = (department or "").strip()
    c = (dcc or "").strip()
    if e and d and c:
        _pending[(e, d, c)] = time.monotonic()
        _log.debug("pending_create.add entity_id=%r department=%r dcc=%r | keys_count=%d", e, d, c, len(_pending))


def remove(entity_id: str, department: str, dcc: str) -> None:
    """Remove one pending entry."""
    key = ((entity_id or "").strip(), (department or "").strip(), (dcc or "").strip())
    _pending.pop(key, None)


def remove_by_entity(entity_id: str) -> None:
    """Remove all pending entries for this entity (e.g. after incremental scan). Path-normalized so scan result ids match."""
    e = (entity_id or "").strip()
    if not e:
        return
    target = _norm_path(e)
    to_drop: list[tuple[str, str, str]] = []
    for k in _pending:
        if k[0] == e:
            to_drop.append(k)
        elif target is not None:
            other = _norm_path(k[0])
            if other is not None and target == other:
                to_drop.append(k)
    for k in to_drop:
        _pending.pop(k, None)
    if to_drop:
        _log.debug("pending_create.remove_by_entity entity_id=%r dropped=%d keys=%s | remaining=%d", e, len(to_drop), to_drop, len(_pending))


def remove_for_entities(entity_ids: list[str]) -> None:
    """Remove all pending entries for any of the given entity ids."""
    ids = list(entity_ids or [])
    if ids:
        _log.debug("pending_create.remove_for_entities entity_ids=%s", ids)
    for eid in ids:
        remove_by_entity(eid)


def get_entity_ids() -> list[str]:
    """Return list of entity_ids that have at least one pending create (for fallback scan)."""
    out: list[str] = []
    seen: set[str] = set()
    for (eid, _, _) in _pending:
        if eid and eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


_TTL_SECONDS = 30.0


def is_pending(entity_id: str, department: str, dcc: str) -> bool:
    """True if (entity_id, department, dcc) has a non-expired pending create."""
    key = ((entity_id or "").strip(), (department or "").strip(), (dcc or "").strip())
    ts = _pending.get(key)
    if ts is None:
        return False
    if (time.monotonic() - ts) > _TTL_SECONDS:
        _pending.pop(key, None)
        _log.debug("pending_create.is_pending entity_id=%r dept=%r dcc=%r -> expired (%.1fs)", key[0], key[1], key[2], time.monotonic() - ts)
        return False
    _log.debug("pending_create.is_pending entity_id=%r dept=%r dcc=%r -> True (age=%.1fs, keys=%d)", key[0], key[1], key[2], time.monotonic() - ts, len(_pending))
    return True


def clear_all() -> None:
    """Clear all pending entries (e.g. on project close)."""
    _pending.clear()
