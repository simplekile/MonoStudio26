"""Core logic for @mention inbox (project-scoped, Dropbox-synced)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from monostudio.core.atomic_write import atomic_write_text

INBOX_FILENAME = "mention_inbox.json"
INBOX_SCHEMA = 1
_MAX_ITEMS = 500
_WRITE_RETRIES = 5


@dataclass(frozen=True)
class MentionInboxItem:
    id: str
    at: str
    to_user_id: str
    from_user_id: str
    from_name: str
    read: bool
    item_rel: str
    item_display: str
    note_id: str
    snippet: str
    department: str = ""

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "at": self.at,
            "to_user_id": self.to_user_id,
            "from_user_id": self.from_user_id,
            "from_name": self.from_name,
            "read": bool(self.read),
            "item_rel": self.item_rel,
            "item_display": self.item_display,
            "note_id": self.note_id,
            "snippet": self.snippet,
        }
        dept = (self.department or "").strip()
        if dept:
            d["department"] = dept
        return d


def _inbox_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / INBOX_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_item(raw: object) -> MentionInboxItem | None:
    if not isinstance(raw, dict):
        return None
    iid = str(raw.get("id") or "").strip()
    at = str(raw.get("at") or "").strip()
    to_uid = str(raw.get("to_user_id") or "").strip()
    if not iid or not at or not to_uid:
        return None
    return MentionInboxItem(
        id=iid,
        at=at,
        to_user_id=to_uid,
        from_user_id=str(raw.get("from_user_id") or "").strip(),
        from_name=str(raw.get("from_name") or "").strip()[:200],
        read=bool(raw.get("read")),
        item_rel=str(raw.get("item_rel") or "").strip(),
        item_display=str(raw.get("item_display") or "").strip()[:200],
        note_id=str(raw.get("note_id") or "").strip(),
        snippet=str(raw.get("snippet") or "").strip()[:200],
        department=str(raw.get("department") or "").strip(),
    )


def read_inbox(project_root: Path) -> list[MentionInboxItem]:
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
    out: list[MentionInboxItem] = []
    for raw in raw_items:
        p = _parse_item(raw)
        if p:
            out.append(p)
    return out[-_MAX_ITEMS:]


def _write_inbox(project_root: Path, items: list[MentionInboxItem]) -> None:
    payload = {
        "schema": INBOX_SCHEMA,
        "items": [i.to_dict() for i in items[-_MAX_ITEMS:]],
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(_inbox_path(project_root), content, encoding="utf-8")


def append_mentions(
    project_root: Path,
    *,
    from_user_id: str,
    from_name: str,
    mentions: tuple[str, ...],
    item_rel: str,
    item_display: str,
    note_id: str,
    snippet: str,
    department: str = "",
) -> list[MentionInboxItem]:
    """Append unread mention items for each target user (excluding author). Returns new items."""
    author_id = (from_user_id or "").strip()
    targets = [u for u in mentions if u.strip() and u.strip() != author_id]
    if not targets:
        return []
    rel = (item_rel or "").strip().replace("\\", "/")
    display = (item_display or "").strip()
    snip = (snippet or "").strip()[:120]
    nid = (note_id or "").strip()
    dept = (department or "").strip()
    now = _utc_now_iso()
    new_items: list[MentionInboxItem] = []
    for uid in targets:
        new_items.append(
            MentionInboxItem(
                id=uuid.uuid4().hex[:16],
                at=now,
                to_user_id=uid.strip(),
                from_user_id=author_id,
                from_name=(from_name or "").strip(),
                read=False,
                item_rel=rel,
                item_display=display,
                note_id=nid,
                snippet=snip,
                department=dept,
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


def resolve_mention_entity_path(
    project_root: Path,
    *,
    item_rel: str = "",
    item_path: str = "",
) -> Path | None:
    """Resolve asset/shot folder for a mention (stored path or project-relative)."""
    path_str = (item_path or "").strip()
    if path_str:
        try:
            p = Path(path_str)
            if p.is_dir():
                return p
        except OSError:
            pass
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


def items_for_user(project_root: Path, user_id: str) -> list[MentionInboxItem]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    return [i for i in read_inbox(project_root) if i.to_user_id == uid]


def unread_for_user(project_root: Path, user_id: str) -> list[MentionInboxItem]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    return [i for i in read_inbox(project_root) if i.to_user_id == uid and not i.read]


def mark_read(project_root: Path, inbox_id: str) -> None:
    iid = (inbox_id or "").strip()
    if not iid:
        return
    items = read_inbox(project_root)
    changed = False
    out: list[MentionInboxItem] = []
    for it in items:
        if it.id == iid and not it.read:
            out.append(
                MentionInboxItem(
                    id=it.id,
                    at=it.at,
                    to_user_id=it.to_user_id,
                    from_user_id=it.from_user_id,
                    from_name=it.from_name,
                    read=True,
                    item_rel=it.item_rel,
                    item_display=it.item_display,
                    note_id=it.note_id,
                    snippet=it.snippet,
                    department=it.department,
                )
            )
            changed = True
        else:
            out.append(it)
    if changed:
        _write_inbox(project_root, out)


def mark_all_read(project_root: Path, user_id: str) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    items = read_inbox(project_root)
    out: list[MentionInboxItem] = []
    changed = False
    for it in items:
        if it.to_user_id == uid and not it.read:
            out.append(
                MentionInboxItem(
                    id=it.id,
                    at=it.at,
                    to_user_id=it.to_user_id,
                    from_user_id=it.from_user_id,
                    from_name=it.from_name,
                    read=True,
                    item_rel=it.item_rel,
                    item_display=it.item_display,
                    note_id=it.note_id,
                    snippet=it.snippet,
                    department=it.department,
                )
            )
            changed = True
        else:
            out.append(it)
    if changed:
        _write_inbox(project_root, out)
