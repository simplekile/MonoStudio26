"""Append-only edit history for project schedule (shared via Dropbox)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from monostudio.core.atomic_write import atomic_write_text
from monostudio.core.project_schedule import (
    ProjectSchedule,
    ScheduleAllocation,
    ScheduleMilestone,
    load_schedule_from_disk,
    schedules_equal,
)
from monostudio.core.user_identity import get_current_user, get_current_user_display_name

HISTORY_SCHEMA = 1
HISTORY_FILENAME = "schedule_history.json"
_MAX_ENTRIES = 200

_history_workspace: Path | None = None


def set_history_workspace(workspace_root: Path | None) -> None:
    """Workspace used to resolve roster user id/name on each save."""
    global _history_workspace
    _history_workspace = Path(workspace_root).resolve() if workspace_root else None


def _history_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / HISTORY_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _editor_identity() -> tuple[str, str]:
    ws = _history_workspace
    user = get_current_user(ws)
    uid = user.id if user else ""
    name = get_current_user_display_name(ws)
    return uid, name


@dataclass(frozen=True)
class ScheduleHistoryEntry:
    id: str
    at: str
    user_id: str
    user_name: str
    summary: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "at": self.at,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "summary": self.summary,
        }


def _entry_from_dict(raw: object) -> ScheduleHistoryEntry | None:
    if not isinstance(raw, dict):
        return None
    eid = str(raw.get("id") or "").strip()
    at = str(raw.get("at") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    if not eid or not at or not summary:
        return None
    return ScheduleHistoryEntry(
        id=eid,
        at=at,
        user_id=str(raw.get("user_id") or "").strip(),
        user_name=str(raw.get("user_name") or "").strip() or "Artist",
        summary=summary,
    )


def _load_all_entries(project_root: Path) -> list[ScheduleHistoryEntry]:
    path = _history_path(project_root)
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[ScheduleHistoryEntry] = []
    for raw in data.get("entries") or []:
        entry = _entry_from_dict(raw)
        if entry is not None:
            out.append(entry)
    return out


def read_schedule_history(
    project_root: Path,
    *,
    limit: int = 100,
) -> list[ScheduleHistoryEntry]:
    out = _load_all_entries(project_root)
    out.sort(key=lambda e: e.at, reverse=True)
    if limit > 0:
        return out[:limit]
    return out


def _milestone_label(m: ScheduleMilestone) -> str:
    label = (m.label or "").strip() or "Milestone"
    date = (m.date or "").strip()[:10]
    return f"{label} ({date})" if date else label


def summarize_schedule_change(before: ProjectSchedule, after: ProjectSchedule) -> str:
    """Short human-readable summary for a schedule save."""
    parts: list[str] = []

    if before.project_start != after.project_start or before.project_end != after.project_end:
        ps = after.project_start or "—"
        pe = after.project_end or "—"
        parts.append(f"Production range {ps} → {pe}")

    before_ms = {m.id: m for m in before.milestones}
    after_ms = {m.id: m for m in after.milestones}
    for mid, m in after_ms.items():
        if mid not in before_ms:
            parts.append(f"Milestone added: {_milestone_label(m)}")
        elif before_ms[mid] != m:
            parts.append(f"Milestone updated: {_milestone_label(m)}")
    for mid, m in before_ms.items():
        if mid not in after_ms:
            parts.append(f"Milestone removed: {_milestone_label(m)}")

    if before.allocations != after.allocations:
        parts.append(_summarize_allocation_change(before.allocations, after.allocations))

    if before.targets != after.targets:
        parts.append(_summarize_count_change("Target", len(before.targets), len(after.targets)))

    if before.waves != after.waves:
        parts.append(_summarize_count_change("Wave", len(before.waves), len(after.waves)))

    if before.templates != after.templates:
        parts.append("Templates updated")

    if before.auto_bar_suppressions != after.auto_bar_suppressions:
        parts.append("Auto-bar suppressions updated")

    if not parts:
        return "Schedule saved"
    if len(parts) == 1:
        return parts[0]
    if len(parts) <= 3:
        return "; ".join(parts)
    return f"{parts[0]}; {parts[1]} (+{len(parts) - 2} more)"


def _summarize_count_change(label: str, old_n: int, new_n: int) -> str:
    if new_n > old_n:
        return f"{label}s added ({old_n} → {new_n})"
    if new_n < old_n:
        return f"{label}s removed ({old_n} → {new_n})"
    return f"{label}s updated"


def _allocation_key(a: ScheduleAllocation) -> tuple:
    return (
        (a.entity_kind or "").strip().lower(),
        (a.entity_rel or "").replace("\\", "/"),
        (a.department or "").strip(),
    )


def _summarize_allocation_change(
    before: list[ScheduleAllocation],
    after: list[ScheduleAllocation],
) -> str:
    before_map = {_allocation_key(a): a for a in before}
    after_map = {_allocation_key(a): a for a in after}
    added = [k for k in after_map if k not in before_map]
    removed = [k for k in before_map if k not in after_map]
    changed = [
        k for k in after_map
        if k in before_map and before_map[k] != after_map[k]
    ]
    bits: list[str] = []
    if added:
        bits.append(f"{len(added)} bar{'s' if len(added) != 1 else ''} added")
    if removed:
        bits.append(f"{len(removed)} bar{'s' if len(removed) != 1 else ''} removed")
    if changed:
        bits.append(f"{len(changed)} bar{'s' if len(changed) != 1 else ''} updated")
    return ", ".join(bits) if bits else "Allocations updated"


def append_schedule_history(
    project_root: Path,
    before: ProjectSchedule,
    after: ProjectSchedule,
) -> None:
    """Record one history row when ``after`` differs from ``before``."""
    if schedules_equal(before, after):
        return
    uid, name = _editor_identity()
    entry = ScheduleHistoryEntry(
        id="h_" + uuid.uuid4().hex[:8],
        at=_utc_now_iso(),
        user_id=uid,
        user_name=name,
        summary=summarize_schedule_change(before, after),
    )
    path = _history_path(project_root)
    existing = _load_all_entries(project_root)
    existing.append(entry)
    existing.sort(key=lambda e: e.at)
    if len(existing) > _MAX_ENTRIES:
        existing = existing[-_MAX_ENTRIES:]
    payload = {
        "schema": HISTORY_SCHEMA,
        "updated_at": _utc_now_iso(),
        "entries": [e.to_dict() for e in existing],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def record_schedule_save(project_root: Path, schedule: ProjectSchedule) -> None:
    """Compare on-disk schedule to ``schedule``, write history if changed."""
    before = load_schedule_from_disk(project_root)
    append_schedule_history(project_root, before, schedule)
