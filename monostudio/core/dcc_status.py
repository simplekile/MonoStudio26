"""
Filesystem-driven DCC status for the UI.

- resolve_dcc_status(asset, department, dcc) derives status from AppState/scan data + pending_create.
- UI must NOT read filesystem; UI derives status on render from this utility.
- Source of truth: filesystem (via dcc_work_states); pending_create only adds "creating" during intent.
"""

from __future__ import annotations

import logging
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from monostudio.core.models import Asset, Shot

_log = logging.getLogger("monostudio.dcc_debug")

DccStatus = Literal["exists", "empty", "new", "creating"]


def resolve_dcc_status(
    item: Asset | Shot,
    department: str,
    dcc: str,
) -> DccStatus:
    """
    Pure function: derive DCC status. Filesystem (scan data) always overrides pending_create.

    Priority order (CRITICAL):
      1. If scan says work file exists (work_file_path set) → "exists"
      2. Else if pending_create for this (entity, dept, dcc) → "creating"
      3. Else folder exists but no file → "empty", else → "new"

    UI must never show "Creating…" when work file exists on disk (per scan).
    """
    dept = (department or "").strip()
    dcc_id = (dcc or "").strip()
    if not dept or not dcc_id:
        return "new"

    entity_id = str(getattr(item, "path", "") or "")
    states = getattr(item, "dcc_work_states", ()) or ()
    mapping = dict(states) if isinstance(states, (tuple, list)) else {}
    state = mapping.get((dept, dcc_id))
    has_work_file = state is not None and getattr(state, "work_file_path", None) is not None

    # 1. Filesystem (scan result) takes priority: if file exists, never show "creating"
    if has_work_file:
        _log.debug("resolve_dcc_status entity_id=%r dept=%r dcc=%r -> exists (work_file_path set)", entity_id, dept, dcc_id)
        return "exists"

    # 2. No file yet; show "creating" only while pending
    from monostudio.core.pending_create import is_pending

    if entity_id and is_pending(entity_id, dept, dcc_id):
        _log.debug("resolve_dcc_status entity_id=%r dept=%r dcc=%r -> creating (is_pending)", entity_id, dept, dcc_id)
        return "creating"

    # 3. Resolve empty vs new from scan data
    if state is None:
        _log.debug("resolve_dcc_status entity_id=%r dept=%r dcc=%r -> new (no state)", entity_id, dept, dcc_id)
        return "new"
    if getattr(state, "work_folder_exists", False):
        _log.debug("resolve_dcc_status entity_id=%r dept=%r dcc=%r -> empty", entity_id, dept, dcc_id)
        return "empty"
    _log.debug("resolve_dcc_status entity_id=%r dept=%r dcc=%r -> new", entity_id, dept, dcc_id)
    return "new"
