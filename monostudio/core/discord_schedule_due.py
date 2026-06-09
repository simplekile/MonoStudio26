"""Daily schedule-due Discord notifications (overdue + due today)."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from monostudio.core.atomic_write import atomic_write_text
from monostudio.core.integrations_config import load_integrations, webhook_urls_for_event
from monostudio.core.models import ProjectIndex
from monostudio.core.project_schedule import read_project_schedule
from monostudio.core.schedule_planner import (
    STATUS_DONE,
    STATUS_EXCLUDED,
    build_planned_bars,
)

_log = logging.getLogger("monostudio.discord_schedule_due")

_STATE_FILENAME = "discord_schedule_due_state.json"


def _state_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".monostudio" / _STATE_FILENAME


def _load_state(project_root: Path | str) -> dict[str, Any]:
    path = _state_path(project_root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(project_root: Path | str, state: dict[str, Any]) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content, encoding="utf-8")


def _last_sent_date(project_root: Path | str) -> date | None:
    raw = str(_load_state(project_root).get("last_sent_date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def collect_schedule_due_items(
    project_root: Path | str,
    project_index: ProjectIndex,
    *,
    today: date | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Open bars that are overdue or due today."""
    ref = today or date.today()
    try:
        schedule = read_project_schedule(Path(project_root))
        bars = build_planned_bars(
            Path(project_root),
            project_index,
            schedule,
            include_shots=True,
            include_assets=True,
        )
    except Exception:
        _log.debug("schedule due collect failed", exc_info=True)
        return []

    items: list[dict[str, Any]] = []
    for bar in bars.values():
        if bar.status in (STATUS_DONE, STATUS_EXCLUDED):
            continue
        if not bar.overdue and bar.due != ref:
            continue
        items.append(
            {
                "entity_kind": bar.entity_kind,
                "entity_name": bar.entity_name,
                "entity_rel": bar.entity_rel.replace("\\", "/"),
                "department": bar.department,
                "department_label": bar.department_label or bar.department,
                "due": bar.due.isoformat(),
                "overdue": bool(bar.overdue),
            }
        )
    items.sort(key=lambda i: (not i["overdue"], i["due"], i["entity_name"].lower(), i["department"]))
    return items[:limit]


def maybe_dispatch_schedule_due(
    workspace_root: Path | str | None,
    project_root: Path | str | None,
    project_index: ProjectIndex | None,
    *,
    today: date | None = None,
) -> bool:
    """Send at most one schedule-due Discord message per project per calendar day."""
    if workspace_root is None or project_root is None or project_index is None:
        return False

    ref = today or date.today()
    root = Path(project_root)
    config = load_integrations(workspace_root)
    if not webhook_urls_for_event(config, "schedule_due"):
        _log.debug("Discord schedule_due skipped — event disabled or no webhook")
        return False

    if _last_sent_date(root) == ref:
        return False

    from monostudio.core.project_lifecycle import is_project_done_for_notifications

    if is_project_done_for_notifications(root):
        _log.debug("Discord schedule_due skipped — project done: %s", root.name)
        return False

    items = collect_schedule_due_items(root, project_index, today=ref)
    if not items:
        return False

    from monostudio.core.discord_webhook import dispatch_discord_event

    project_name = root.name
    dispatch_discord_event(
        workspace_root,
        "schedule_due",
        {
            "project_name": project_name,
            "items": items,
            "overdue_count": sum(1 for i in items if i.get("overdue")),
            "due_today_count": sum(1 for i in items if not i.get("overdue")),
        },
        dedupe_key=f"schedule_due:{project_name}:{ref.isoformat()}",
        project_root=root,
    )
    _save_state(root, {"last_sent_date": ref.isoformat()})
    _log.debug("Discord schedule_due dispatched for %s (%d items)", project_name, len(items))
    return True
