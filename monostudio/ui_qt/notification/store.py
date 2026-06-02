"""
In-memory store for user notification history (@mentions and user alerts only).
Used by the topbar noti button (recent 5) and the "Show all" dialog (up to 200).
Persisted via QSettings so history survives app restarts.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
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
    item_display: str = ""
    note_id: str = ""
    mention_inbox_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "item_path": self.item_path,
            "item_display": self.item_display,
            "note_id": self.note_id,
            "mention_inbox_id": self.mention_inbox_id,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> UserAlertPayload:
        if not isinstance(data, dict):
            return cls()
        return cls(
            item_path=str(data.get("item_path") or ""),
            item_display=str(data.get("item_display") or ""),
            note_id=str(data.get("note_id") or ""),
            mention_inbox_id=str(data.get("mention_inbox_id") or ""),
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
            self.at = datetime.fromtimestamp(self.at)
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
            at = datetime.fromisoformat(str(at_raw).replace("Z", "+00:00")) if at_raw else datetime.now()
        except (ValueError, TypeError):
            at = datetime.now()
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
    """Drop legacy operational log entries on first run after upgrade."""
    s = _settings()
    if s.value(_MIGRATED_KEY):
        return
    _history.clear()
    s.setValue(_SETTINGS_KEY, json.dumps([], ensure_ascii=False))
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
    _settings().setValue(_SETTINGS_KEY, json.dumps(arr, ensure_ascii=False))


_load_from_settings()


def append_user_alert(
    toast_type: ToastType,
    message: str,
    *,
    payload: UserAlertPayload | None = None,
) -> NotificationEntry:
    entry = NotificationEntry(
        toast_type=toast_type,
        message=message,
        at=datetime.now(),
        kind="user",
        payload=payload or UserAlertPayload(),
        read=False,
    )
    _history.append(entry)
    _save_to_settings()
    return entry


def recent(n: int = 5) -> list[NotificationEntry]:
    """Last n user entries, newest first."""
    items = [e for e in _history if e.kind == "user"]
    items.reverse()
    return items[:n]


def all_entries() -> list[NotificationEntry]:
    """All stored user entries, newest first."""
    items = [e for e in _history if e.kind == "user"]
    items.reverse()
    return items


def count() -> int:
    return sum(1 for e in _history if e.kind == "user")


def unread_count() -> int:
    return sum(1 for e in _history if e.kind == "user" and not e.read)


def has_mention_inbox_id(mention_inbox_id: str) -> bool:
    mid = (mention_inbox_id or "").strip()
    if not mid:
        return False
    return any(
        e.kind == "user" and e.payload.mention_inbox_id == mid
        for e in _history
    )


def clear_mention_user_alerts() -> None:
    """Drop cached @mention bell rows (e.g. before reloading inbox for another user)."""
    kept = [
        e
        for e in _history
        if not (e.kind == "user" and (e.payload.mention_inbox_id or "").strip())
    ]
    if len(kept) == len(_history):
        return
    _history.clear()
    _history.extend(kept)
    _save_to_settings()


def mark_read(mention_inbox_id: str) -> None:
    mid = (mention_inbox_id or "").strip()
    if not mid:
        return
    changed = False
    for i, e in enumerate(_history):
        if e.kind == "user" and e.payload.mention_inbox_id == mid and not e.read:
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


def mark_all_read() -> None:
    changed = False
    new_hist: deque[NotificationEntry] = deque(maxlen=MAX_HISTORY)
    for e in _history:
        if e.kind == "user" and not e.read:
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
