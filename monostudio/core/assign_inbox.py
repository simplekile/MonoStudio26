"""Core logic for schedule assignee inbox (project-scoped, Dropbox-synced)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from monostudio.core.atomic_write import atomic_write_text

INBOX_FILENAME = "assign_inbox.json"
INBOX_SCHEMA = 2
_MAX_ITEMS = 500
_WRITE_RETRIES = 5


@dataclass(frozen=True)
class AssignInboxItem:
    id: str
    at: str
    to_user_id: str
    from_user_id: str
    from_name: str
    read: bool
    item_rel: str
    item_display: str
    entity_kind: str
    department: str
    allocation_id: str = ""
    due: str = ""
    start: str = ""
    confirmed: bool = False
    confirmed_at: str = ""
    confirmed_by_name: str = ""
    discord_message_id: str = ""
    batch_id: str = ""

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "at": self.at,
            "to_user_id": self.to_user_id,
            "from_user_id": self.from_user_id,
            "from_name": self.from_name,
            "read": bool(self.read),
            "confirmed": bool(self.confirmed),
            "item_rel": self.item_rel,
            "item_display": self.item_display,
            "entity_kind": self.entity_kind,
            "department": self.department,
        }
        aid = (self.allocation_id or "").strip()
        if aid:
            d["allocation_id"] = aid
        due = (self.due or "").strip()
        if due:
            d["due"] = due
        start = (self.start or "").strip()
        if start:
            d["start"] = start
        cat = (self.confirmed_at or "").strip()
        if cat:
            d["confirmed_at"] = cat
        cname = (self.confirmed_by_name or "").strip()
        if cname:
            d["confirmed_by_name"] = cname
        dmid = (self.discord_message_id or "").strip()
        if dmid:
            d["discord_message_id"] = dmid
        bid = (self.batch_id or "").strip()
        if bid:
            d["batch_id"] = bid
        return d


def _inbox_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / INBOX_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_item(raw: object) -> AssignInboxItem | None:
    if not isinstance(raw, dict):
        return None
    iid = str(raw.get("id") or "").strip()
    at = str(raw.get("at") or "").strip()
    to_uid = str(raw.get("to_user_id") or "").strip()
    if not iid or not at or not to_uid:
        return None
    return AssignInboxItem(
        id=iid,
        at=at,
        to_user_id=to_uid,
        from_user_id=str(raw.get("from_user_id") or "").strip(),
        from_name=str(raw.get("from_name") or "").strip()[:200],
        read=bool(raw.get("read")),
        confirmed=bool(raw.get("confirmed")),
        item_rel=str(raw.get("item_rel") or "").strip(),
        item_display=str(raw.get("item_display") or "").strip()[:200],
        entity_kind=str(raw.get("entity_kind") or "").strip(),
        department=str(raw.get("department") or "").strip(),
        allocation_id=str(raw.get("allocation_id") or "").strip(),
        due=str(raw.get("due") or "").strip(),
        start=str(raw.get("start") or "").strip(),
        confirmed_at=str(raw.get("confirmed_at") or "").strip(),
        confirmed_by_name=str(raw.get("confirmed_by_name") or "").strip(),
        discord_message_id=str(raw.get("discord_message_id") or "").strip(),
        batch_id=str(raw.get("batch_id") or "").strip(),
    )


def read_inbox(project_root: Path) -> list[AssignInboxItem]:
    path = _inbox_path(project_root)
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return []
    out: list[AssignInboxItem] = []
    for raw in raw_items:
        p = _parse_item(raw)
        if p:
            out.append(p)
    return out[-_MAX_ITEMS:]


def _write_inbox(project_root: Path, items: list[AssignInboxItem]) -> None:
    payload = {
        "schema": INBOX_SCHEMA,
        "items": [i.to_dict() for i in items[-_MAX_ITEMS:]],
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(_inbox_path(project_root), content, encoding="utf-8")


def _update_items(
    project_root: Path,
    inbox_id: str,
    mutator,
) -> AssignInboxItem | None:
    iid = (inbox_id or "").strip()
    if not iid:
        return None
    last_exc: OSError | None = None
    for attempt in range(_WRITE_RETRIES):
        cur = read_inbox(project_root)
        out: list[AssignInboxItem] = []
        updated: AssignInboxItem | None = None
        for it in cur:
            if it.id == iid:
                updated = mutator(it)
                out.append(updated)
            else:
                out.append(it)
        if updated is None:
            return None
        try:
            _write_inbox(project_root, out)
            return updated
        except OSError as ex:
            last_exc = ex
            time.sleep(0.05 * (attempt + 1))
    if last_exc:
        raise last_exc
    return None


def resolve_assign_entity_path(
    project_root: Path,
    *,
    item_rel: str = "",
) -> Path | None:
    rel = (item_rel or "").strip().replace("\\", "/")
    if not rel:
        return None
    try:
        p = (Path(project_root) / rel).resolve()
        if p.is_dir():
            return p
    except OSError:
        pass
    return None


def append_assignments(
    project_root: Path,
    *,
    from_user_id: str,
    from_name: str,
    to_user_ids: tuple[str, ...] | list[str],
    entity_kind: str,
    item_rel: str,
    item_display: str,
    department: str,
    allocation_id: str = "",
    due: str = "",
    start: str = "",
    discord_message_id: str = "",
    batch_id: str = "",
) -> list[AssignInboxItem]:
    """Append unread assign items for each target user (excluding assigner)."""
    author_id = (from_user_id or "").strip()
    targets = [u for u in to_user_ids if u.strip() and u.strip() != author_id]
    if not targets:
        return []
    rel = (item_rel or "").strip().replace("\\", "/")
    display = (item_display or "").strip()
    kind = (entity_kind or "").strip().lower()
    dept = (department or "").strip()
    aid = (allocation_id or "").strip()
    due_s = (due or "").strip()
    start_s = (start or "").strip()
    dmid = (discord_message_id or "").strip()
    bid = (batch_id or "").strip()
    now = _utc_now_iso()
    new_items: list[AssignInboxItem] = []
    for uid in targets:
        new_items.append(
            AssignInboxItem(
                id=uuid.uuid4().hex[:16],
                at=now,
                to_user_id=uid.strip(),
                from_user_id=author_id,
                from_name=(from_name or "").strip(),
                read=False,
                confirmed=False,
                item_rel=rel,
                item_display=display,
                entity_kind=kind,
                department=dept,
                allocation_id=aid,
                due=due_s,
                start=start_s,
                discord_message_id=dmid,
                batch_id=bid,
            )
        )

    last_exc: OSError | None = None
    for attempt in range(_WRITE_RETRIES):
        cur = read_inbox(project_root)
        merged = cur + new_items
        try:
            _write_inbox(project_root, merged)
            return new_items
        except OSError as ex:
            last_exc = ex
            time.sleep(0.05 * (attempt + 1))
    if last_exc:
        raise last_exc
    return new_items


def attach_discord_message_id(
    project_root: Path,
    inbox_ids: list[str] | tuple[str, ...],
    discord_message_id: str,
) -> None:
    mid = (discord_message_id or "").strip()
    ids = {(i or "").strip() for i in inbox_ids if (i or "").strip()}
    if not mid or not ids:
        return
    last_exc: OSError | None = None
    for attempt in range(_WRITE_RETRIES):
        cur = read_inbox(project_root)
        out: list[AssignInboxItem] = []
        changed = False
        for it in cur:
            if it.id in ids:
                out.append(replace(it, discord_message_id=mid))
                changed = True
            else:
                out.append(it)
        if not changed:
            return
        try:
            _write_inbox(project_root, out)
            return
        except OSError as ex:
            last_exc = ex
            time.sleep(0.05 * (attempt + 1))
    if last_exc:
        raise last_exc


def items_for_user(project_root: Path, user_id: str) -> list[AssignInboxItem]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    return [i for i in read_inbox(project_root) if i.to_user_id == uid]


def unread_for_user(project_root: Path, user_id: str) -> list[AssignInboxItem]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    return [i for i in read_inbox(project_root) if i.to_user_id == uid and not i.read]


def get_inbox_item(project_root: Path, inbox_id: str) -> AssignInboxItem | None:
    iid = (inbox_id or "").strip()
    if not iid:
        return None
    for it in read_inbox(project_root):
        if it.id == iid:
            return it
    return None


def find_pending_for_user(
    project_root: Path,
    user_id: str,
    *,
    item_rel: str = "",
    department: str = "",
    allocation_id: str = "",
) -> AssignInboxItem | None:
    rel = (item_rel or "").replace("\\", "/").strip()
    dept = (department or "").strip()
    aid = (allocation_id or "").strip()
    for it in items_for_user(project_root, user_id):
        if it.confirmed:
            continue
        if rel and it.item_rel.replace("\\", "/") != rel:
            continue
        if dept and it.department != dept:
            continue
        if aid and it.allocation_id and it.allocation_id != aid:
            continue
        return it
    return None


def find_assign_inbox_across_projects(
    workspace_root: Path,
    inbox_id: str,
) -> tuple[Path, AssignInboxItem] | None:
    """Locate an assign inbox row by id under any project in the workspace."""
    from monostudio.core.workspace_reader import discover_projects

    iid = (inbox_id or "").strip()
    if not iid:
        return None
    try:
        projects = discover_projects(workspace_root)
    except OSError:
        projects = []
    for proj in projects:
        for item in read_inbox(proj.root):
            if item.id == iid:
                return proj.root, item
    return None


def mark_read(project_root: Path, inbox_id: str) -> None:
    def _mut(it: AssignInboxItem) -> AssignInboxItem:
        return replace(it, read=True) if not it.read else it

    try:
        _update_items(project_root, inbox_id, _mut)
    except OSError:
        pass


def mark_confirmed(
    project_root: Path,
    inbox_id: str,
    *,
    confirmed_by_name: str = "",
) -> AssignInboxItem | None:
    now = _utc_now_iso()
    name = (confirmed_by_name or "").strip()

    def _mut(it: AssignInboxItem) -> AssignInboxItem:
        if it.confirmed:
            return it
        return replace(
            it,
            confirmed=True,
            confirmed_at=now,
            confirmed_by_name=name,
            read=True,
        )

    try:
        return _update_items(project_root, inbox_id, _mut)
    except OSError:
        return None
