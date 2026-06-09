"""Notify roster users when they are newly assigned on the schedule."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.assign_inbox import (
    append_assignments,
    attach_discord_message_id,
    get_inbox_item,
    mark_confirmed,
)
from monostudio.core.project_schedule import ScheduleAllocation
from monostudio.core.user_identity import (
    get_current_user,
    get_current_user_display_name,
    normalize_assignee_ids,
)


def _entity_display_from_rel(entity_rel: str) -> str:
    rel = (entity_rel or "").replace("\\", "/").strip()
    if not rel:
        return ""
    return rel.rsplit("/", 1)[-1].replace("_", " ")


def _previous_assignee_ids(previous: ScheduleAllocation | None) -> list[str]:
    if previous is None:
        return []
    if previous.assignee_ids:
        return list(normalize_assignee_ids(list(previous.assignee_ids)))
    if (previous.assignee_id or "").strip():
        return list(normalize_assignee_ids([previous.assignee_id]))
    return []


def collect_previous_assignee_ids(
    previous: ScheduleAllocation | None,
    *,
    bar_assignee_ids: tuple[str, ...] | list[str] | None = None,
    bar_assignee_id: str = "",
) -> tuple[str, ...]:
    """Assignee ids already on this row before Apply (allocation file, else planned bar)."""
    ids = _previous_assignee_ids(previous)
    if ids:
        return normalize_assignee_ids(ids)
    if bar_assignee_ids:
        norm = normalize_assignee_ids(list(bar_assignee_ids))
        if norm:
            return norm
    legacy = (bar_assignee_id or "").strip()
    if legacy:
        return normalize_assignee_ids([legacy])
    return ()


def _assign_discord_payload(
    project_root: Path,
    *,
    from_id: str,
    from_name: str,
    added: list[str],
    allocation: ScheduleAllocation,
    display: str,
    rel: str,
    dept: str,
    dept_label: str,
    assign_inbox_ids: list[str],
) -> dict:
    return {
        "from_user_id": from_id,
        "from_name": from_name,
        "to_user_ids": added,
        "item_rel": rel,
        "item_display": display,
        "entity_kind": allocation.entity_kind or "",
        "department": dept,
        "department_label": dept_label,
        "due": allocation.due or "",
        "start": allocation.start or "",
        "project_name": project_root.name,
        "allocation_id": allocation.id or "",
        "assign_inbox_ids": assign_inbox_ids,
    }


def notify_new_schedule_assignments(
    project_root: Path,
    workspace_root: Path | None,
    *,
    previous: ScheduleAllocation | None,
    allocation: ScheduleAllocation,
    entity_display: str = "",
    previous_assignee_ids: tuple[str, ...] | None = None,
) -> None:
    """Write assign inbox entries for users newly added to an allocation."""
    if previous_assignee_ids is not None:
        prev_ids = set(normalize_assignee_ids(previous_assignee_ids))
    else:
        prev_ids = set(_previous_assignee_ids(previous))
    new_ids = normalize_assignee_ids(list(allocation.assignee_ids or []))
    added = [uid for uid in new_ids if uid not in prev_ids]
    if not added:
        return

    from_id = ""
    from_name = ""
    if workspace_root is not None:
        user = get_current_user(workspace_root)
        if user is not None:
            from_id = user.id
        from_name = get_current_user_display_name(workspace_root)

    notify_targets = [uid for uid in added if uid.strip() and uid.strip() != from_id]
    if not notify_targets:
        return

    display = (entity_display or "").strip() or _entity_display_from_rel(allocation.entity_rel or "")
    rel = (allocation.entity_rel or "").replace("\\", "/").strip()
    dept = (allocation.department or "").strip()
    if not rel or not dept:
        return

    dept_label = dept
    try:
        from monostudio.core.department_registry import DepartmentRegistry

        dept_label = DepartmentRegistry.for_project(project_root).get_department_label(dept) or dept
    except OSError:
        pass

    try:
        new_items = append_assignments(
            project_root,
            from_user_id=from_id,
            from_name=from_name,
            to_user_ids=notify_targets,
            entity_kind=allocation.entity_kind or "",
            item_rel=rel,
            item_display=display,
            department=dept,
            allocation_id=allocation.id or "",
            due=allocation.due or "",
            start=allocation.start or "",
        )
    except OSError:
        return

    if not new_items:
        return

    if workspace_root is None:
        return

    from monostudio.core.discord_webhook import post_schedule_assigned_discord

    payload = _assign_discord_payload(
        project_root,
        from_id=from_id,
        from_name=from_name,
        added=notify_targets,
        allocation=allocation,
        display=display,
        rel=rel,
        dept=dept,
        dept_label=dept_label,
        assign_inbox_ids=[i.id for i in new_items],
    )
    message_id = post_schedule_assigned_discord(
        workspace_root,
        payload,
        dedupe_key=f"schedule_assigned:{new_items[0].id}",
        project_root=project_root,
    )
    if message_id and message_id != "sent":
        try:
            attach_discord_message_id(project_root, [i.id for i in new_items], message_id)
        except OSError:
            pass


def confirm_schedule_assignment(
    project_root: Path,
    workspace_root: Path | None,
    inbox_id: str,
    *,
    confirmed_by_name: str = "",
) -> bool:
    """Mark assign inbox item confirmed and update Discord message if linked."""
    iid = (inbox_id or "").strip()
    if not iid:
        return False
    item_before = get_inbox_item(project_root, iid)
    if item_before is None or item_before.confirmed:
        return item_before is not None and item_before.confirmed

    name = (confirmed_by_name or "").strip()
    if not name and workspace_root is not None:
        name = get_current_user_display_name(workspace_root)

    updated = mark_confirmed(project_root, iid, confirmed_by_name=name)
    if updated is None:
        return False

    dmid = (updated.discord_message_id or "").strip()
    if workspace_root is not None and dmid:
        from monostudio.core.department_registry import DepartmentRegistry
        from monostudio.core.discord_webhook import update_schedule_assigned_discord_confirmed

        dept_label = updated.department
        try:
            dept_label = (
                DepartmentRegistry.for_project(project_root).get_department_label(updated.department)
                or updated.department
            )
        except OSError:
            pass
        payload = {
            "from_user_id": updated.from_user_id,
            "from_name": updated.from_name,
            "to_user_ids": [updated.to_user_id],
            "item_rel": updated.item_rel,
            "item_display": updated.item_display,
            "entity_kind": updated.entity_kind,
            "department": updated.department,
            "department_label": dept_label,
            "due": updated.due,
            "start": updated.start,
            "project_name": project_root.name,
            "allocation_id": updated.allocation_id,
            "assign_inbox_ids": [updated.id],
        }
        update_schedule_assigned_discord_confirmed(
            workspace_root,
            payload,
            discord_message_id=dmid,
            confirmer_name=name,
        )
    return True
