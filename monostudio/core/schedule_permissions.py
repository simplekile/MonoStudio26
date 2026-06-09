"""Role-based schedule write access (roster role + admin session override)."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.access_control import is_admin_capable
from monostudio.core.user_identity import get_current_user

# Artists are read-only; these roster roles may edit schedule and assignees.
SCHEDULE_WRITE_ROLES = frozenset(
    {
        "lead",
        "supervisor",
        "producer",
        "coordinator",
    }
)


def can_edit_schedule(workspace_root: Path | None) -> bool:
    """Return True when the current user may edit schedule dates and assignees."""
    if is_admin_capable():
        return True
    user = get_current_user(workspace_root)
    if user is None:
        return False
    role = (user.role or "artist").strip().lower()
    return role in SCHEDULE_WRITE_ROLES
