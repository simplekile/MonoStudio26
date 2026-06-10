"""
In-memory store for user notification history (@mentions and user alerts only).
Used by the topbar noti button (recent 5) and the "Show all" dialog (up to 200).
Persisted via QSettings so history survives app restarts.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QSettings

ToastType = Literal["info", "success", "warning", "error", "important"]
NotificationKind = Literal["user", "system"]

MAX_HISTORY = 200
_SETTINGS_KEY = "notification/history"
_MIGRATED_KEY = "notification/history_migrated_v2"


def _settings() -> QSettings:
    return QSettings("MonoStudio26", "MonoStudio26")


@dataclass
class UserAlertPayload:
    """Metadata for a user-targeted notification (e.g. @mention)."""

    item_path: str = ""
    item_rel: str = ""
    item_display: str = ""
    note_id: str = ""
    mention_inbox_id: str = ""
    assign_inbox_id: str = ""
    department: str = ""
    from_name: str = ""
    from_user_id: str = ""
    department_label: str = ""
    to_user_id: str = ""
    assign_batch_id: str = ""
    assign_inbox_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, str]:
        d = {
            "item_path": self.item_path,
            "item_rel": self.item_rel,
            "item_display": self.item_display,
            "note_id": self.note_id,
            "mention_inbox_id": self.mention_inbox_id,
            "assign_inbox_id": self.assign_inbox_id,
            "department": self.department,
            "from_name": self.from_name,
            "from_user_id": self.from_user_id,
            "department_label": self.department_label,
            "to_user_id": self.to_user_id,
            "assign_batch_id": self.assign_batch_id,
        }
        if self.assign_inbox_ids:
            d["assign_inbox_ids"] = list(self.assign_inbox_ids)
        return d

    @classmethod
    def from_dict(cls, data: dict | None) -> UserAlertPayload:
        if not isinstance(data, dict):
            return cls()
        return cls(
            item_path=str(data.get("item_path") or ""),
            item_rel=str(data.get("item_rel") or ""),
            item_display=str(data.get("item_display") or ""),
            note_id=str(data.get("note_id") or ""),
            mention_inbox_id=str(data.get("mention_inbox_id") or ""),
            assign_inbox_id=str(data.get("assign_inbox_id") or ""),
            department=str(data.get("department") or ""),
            from_name=str(data.get("from_name") or ""),
            from_user_id=str(data.get("from_user_id") or ""),
            department_label=str(data.get("department_label") or ""),
            to_user_id=str(data.get("to_user_id") or ""),
            assign_batch_id=str(data.get("assign_batch_id") or ""),
            assign_inbox_ids=tuple(
                str(i).strip()
                for i in (data.get("assign_inbox_ids") or [])
                if str(i).strip()
            ),
        )


@dataclass
class NotificationEntry:
    toast_type: ToastType
    message: str
    at: datetime
    kind: NotificationKind = "user"
    payload: UserAlertPayload = field(default_factory=UserAlertPayload)
    read: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.at, (int, float)):
            self.at = datetime.fromtimestamp(self.at, tz=timezone.utc)
        elif isinstance(self.at, datetime) and self.at.tzinfo is None:
            self.at = self.at.replace(tzinfo=timezone.utc)
        if isinstance(self.payload, dict):
            self.payload = UserAlertPayload.from_dict(self.payload)

    def to_dict(self) -> dict:
        return {
            "toast_type": self.toast_type,
            "message": self.message,
            "at": self.at.isoformat(),
            "kind": self.kind,
            "payload": self.payload.to_dict(),
            "read": bool(self.read),
        }

    @classmethod
    def from_dict(cls, data: dict) -> NotificationEntry | None:
        if not isinstance(data, dict):
            return None
        t = data.get("toast_type")
        msg = data.get("message")
        at_raw = data.get("at")
        if t not in ("info", "success", "warning", "error", "important") or msg is None:
            return None
        try:
            at = (
                datetime.fromisoformat(str(at_raw).replace("Z", "+00:00"))
                if at_raw
                else datetime.now(timezone.utc)
            )
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            at = datetime.now(timezone.utc)
        kind_raw = str(data.get("kind") or "system")
        kind: NotificationKind = "user" if kind_raw == "user" else "system"
        payload = UserAlertPayload.from_dict(data.get("payload") if isinstance(data.get("payload"), dict) else None)
        return cls(
            toast_type=t,
            message=str(msg),
            at=at,
            kind=kind,
            payload=payload,
            read=bool(data.get("read")),
        )


_history: deque[NotificationEntry] = deque(maxlen=MAX_HISTORY)


def _migrate_legacy_history() -> None:
    """One-time flag: older builds stored operational log rows in bell history."""
    s = _settings()
    if s.value(_MIGRATED_KEY):
        return
    s.setValue(_MIGRATED_KEY, True)


def _load_from_settings() -> None:
    raw = _settings().value(_SETTINGS_KEY)
    if not raw:
        _migrate_legacy_history()
        return
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
    else:
        data = raw
    if not isinstance(data, list):
        return
    _history.clear()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        e = NotificationEntry.from_dict(entry)
        if e is not None and e.kind == "user":
            _history.append(e)
    if not _settings().value(_MIGRATED_KEY):
        _save_to_settings()
        _settings().setValue(_MIGRATED_KEY, True)


def _save_to_settings() -> None:
    arr = [e.to_dict() for e in _history]
    s = _settings()
    s.setValue(_SETTINGS_KEY, json.dumps(arr, ensure_ascii=False))
    s.sync()


_load_from_settings()


def entry_belongs_to_user(
    entry: NotificationEntry,
    user_id: str,
    project_root: Path | None = None,
) -> bool:
    """True if this bell row is for ``user_id`` (legacy rows resolve via mention inbox)."""
    uid = (user_id or "").strip()
    if not uid or entry.kind != "user":
        return False
    tid = (entry.payload.to_user_id or "").strip()
    if tid:
        return tid == uid
    mid = (entry.payload.mention_inbox_id or "").strip()
    aid = (entry.payload.assign_inbox_id or "").strip()
    if (not mid and not aid) or project_root is None:
        return False
    try:
        if mid:
            from monostudio.core.mention_inbox import read_inbox

            for item in read_inbox(Path(project_root)):
                if item.id == mid:
                    return item.to_user_id == uid
        if aid:
            from monostudio.core.assign_inbox import read_inbox as read_assign_inbox

            for item in read_assign_inbox(Path(project_root)):
                if item.id == aid:
                    return item.to_user_id == uid
    except OSError:
        pass
    return False


def _user_entries(
    *,
    user_id: str = "",
    project_root: Path | None = None,
) -> list[NotificationEntry]:
    items = [e for e in _history if e.kind == "user"]
    uid = (user_id or "").strip()
    if not uid:
        return []
    return [e for e in items if entry_belongs_to_user(e, uid, project_root)]


def append_user_alert(
    toast_type: ToastType,
    message: str,
    *,
    payload: UserAlertPayload | None = None,
    read: bool = False,
) -> NotificationEntry:
    entry = NotificationEntry(
        toast_type=toast_type,
        message=message,
        at=datetime.now(timezone.utc),
        kind="user",
        payload=payload or UserAlertPayload(),
        read=bool(read),
    )
    _history.append(entry)
    _save_to_settings()
    return entry


def recent(
    n: int = 5,
    *,
    user_id: str = "",
    project_root: Path | None = None,
) -> list[NotificationEntry]:
    """Last n user entries for ``user_id``, newest first."""
    items = _user_entries(user_id=user_id, project_root=project_root)
    items.reverse()
    return items[:n]


def all_entries(
    *,
    user_id: str = "",
    project_root: Path | None = None,
) -> list[NotificationEntry]:
    """All stored user entries for ``user_id``, newest first."""
    items = _user_entries(user_id=user_id, project_root=project_root)
    items.reverse()
    return items


def count(
    *,
    user_id: str = "",
    project_root: Path | None = None,
) -> int:
    return len(_user_entries(user_id=user_id, project_root=project_root))


def unread_count(
    *,
    user_id: str = "",
    project_root: Path | None = None,
) -> int:
    return sum(1 for e in _user_entries(user_id=user_id, project_root=project_root) if not e.read)


def has_mention_inbox_id(mention_inbox_id: str) -> bool:
    mid = (mention_inbox_id or "").strip()
    if not mid:
        return False
    return any(
        e.kind == "user" and e.payload.mention_inbox_id == mid
        for e in _history
    )


def has_assign_inbox_id(assign_inbox_id: str) -> bool:
    aid = (assign_inbox_id or "").strip()
    if not aid:
        return False
    return any(
        e.kind == "user"
        and (
            e.payload.assign_inbox_id == aid
            or aid in (e.payload.assign_inbox_ids or ())
        )
        for e in _history
    )


def has_assign_batch_id(assign_batch_id: str) -> bool:
    bid = (assign_batch_id or "").strip()
    if not bid:
        return False
    return any(
        e.kind == "user" and (e.payload.assign_batch_id or "").strip() == bid
        for e in _history
    )


def clear_mention_user_alerts() -> None:
    """Drop all cached user inbox bell rows (@mention and schedule assign)."""
    kept = [
        e
        for e in _history
        if not (
            e.kind == "user"
            and (
                (e.payload.mention_inbox_id or "").strip()
                or (e.payload.assign_inbox_id or "").strip()
            )
        )
    ]
    if len(kept) == len(_history):
        return
    _history.clear()
    _history.extend(kept)
    _save_to_settings()


def prune_mention_alerts_not_for_user(
    user_id: str,
    project_root: Path | None = None,
) -> None:
    """Remove @mention bell rows that belong to another signed-in account."""
    uid = (user_id or "").strip()
    if not uid:
        clear_mention_user_alerts()
        return
    kept: list[NotificationEntry] = []
    changed = False
    for e in _history:
        mid = (e.payload.mention_inbox_id or "").strip() if e.kind == "user" else ""
        aid = (e.payload.assign_inbox_id or "").strip() if e.kind == "user" else ""
        if not mid and not aid:
            kept.append(e)
            continue
        if entry_belongs_to_user(e, uid, project_root):
            kept.append(e)
        else:
            changed = True
    if not changed:
        return
    _history.clear()
    _history.extend(kept)
    _save_to_settings()


def mark_read(inbox_id: str) -> None:
    iid = (inbox_id or "").strip()
    if not iid:
        return
    changed = False
    for i, e in enumerate(_history):
        if e.kind != "user" or e.read:
            continue
        p = e.payload
        if (
            p.mention_inbox_id != iid
            and p.assign_inbox_id != iid
            and iid not in (p.assign_inbox_ids or ())
        ):
            continue
        if e.kind == "user" and not e.read:
            _history[i] = NotificationEntry(
                toast_type=e.toast_type,
                message=e.message,
                at=e.at,
                kind=e.kind,
                payload=e.payload,
                read=True,
            )
            changed = True
    if changed:
        _save_to_settings()


def mark_all_read(
    *,
    user_id: str = "",
    project_root: Path | None = None,
) -> None:
    """Mark user notification rows read; scoped to *user_id* when set."""
    uid = (user_id or "").strip()
    changed = False
    new_hist: deque[NotificationEntry] = deque(maxlen=MAX_HISTORY)
    for e in _history:
        if e.kind == "user" and not e.read:
            if uid and not entry_belongs_to_user(e, uid, project_root):
                new_hist.append(e)
                continue
            new_hist.append(
                NotificationEntry(
                    toast_type=e.toast_type,
                    message=e.message,
                    at=e.at,
                    kind=e.kind,
                    payload=e.payload,
                    read=True,
                )
            )
            changed = True
        else:
            new_hist.append(e)
    if changed:
        _history.clear()
        _history.extend(new_hist)
        _save_to_settings()
