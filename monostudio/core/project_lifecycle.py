"""Project lifecycle helpers (done / complete) for notifications and integrations."""

from __future__ import annotations

import json
import logging
from pathlib import Path

_log = logging.getLogger("monostudio.project_lifecycle")

_DONE_LIFECYCLE_VALUES = frozenset({"done", "complete", "completed", "archived"})


def resolve_workspace_project_root(
    workspace_root: Path | str | None,
    project_name: str,
) -> Path | None:
    """Resolve project folder from workspace + folder or display name."""
    ws = Path(workspace_root).resolve() if workspace_root is not None else None
    name = (project_name or "").strip()
    if ws is None or not name:
        return None
    direct = ws / name
    try:
        if (direct / ".monostudio" / "project.json").is_file():
            return direct
    except OSError:
        pass
    try:
        from monostudio.core.workspace_reader import discover_projects

        for proj in discover_projects(ws):
            if proj.root.name == name or proj.name == name:
                return proj.root
    except Exception:
        _log.debug("discover_projects failed during project resolve", exc_info=True)
    return None


def _explicit_lifecycle_done(project_root: Path) -> bool:
    manifest = project_root / ".monostudio" / "project.json"
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    for key in ("lifecycle", "status"):
        raw = str(data.get(key) or "").strip().lower()
        if raw in _DONE_LIFECYCLE_VALUES:
            return True
    return False


def _pipeline_complete(project_root: Path) -> bool:
    """True when every planned schedule bar is done (Dashboard Complete lifecycle)."""
    try:
        from monostudio.core.fs_reader import build_project_index
        from monostudio.core.project_schedule import read_project_schedule
        from monostudio.core.schedule_planner import (
            STATUS_DONE,
            STATUS_EXCLUDED,
            build_planned_bars,
            count_overdue_bars,
        )
    except ImportError:
        return False

    root = project_root.resolve()
    try:
        index = build_project_index(root)
        schedule = read_project_schedule(root)
        bars = build_planned_bars(
            root,
            index,
            schedule,
            include_shots=True,
            include_assets=True,
        )
    except Exception:
        _log.debug("pipeline complete check failed for %s", root.name, exc_info=True)
        return False

    in_scope = [b for b in bars.values() if b.status != STATUS_EXCLUDED]
    if not in_scope:
        return False
    if count_overdue_bars(bars) > 0:
        return False
    return all(b.status == STATUS_DONE for b in in_scope)


def is_project_done_for_notifications(project_root: Path | str | None) -> bool:
    """
    True when Discord (and similar outbound alerts) should be muted for this project.

    - Explicit: ``.monostudio/project.json`` → ``lifecycle`` or ``status`` is done/complete/archived.
    - Computed: all planned schedule bars are done, with at least one bar and no overdue items.
    """
    if project_root is None:
        return False
    root = Path(project_root).resolve()
    try:
        if not (root / ".monostudio" / "project.json").is_file():
            return False
    except OSError:
        return False
    if _explicit_lifecycle_done(root):
        return True
    return _pipeline_complete(root)
