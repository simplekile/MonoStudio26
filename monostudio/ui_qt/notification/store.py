"""
In-memory store for notification history (general toasts only).
Used by the topbar noti button (recent 5) and the "Show all" dialog (up to 200).
Persisted via QSettings so history survives app restarts.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from PySide6.QtCore import QSettings

ToastType = Literal["info", "success", "warning", "error", "important"]

# Keep last 200 notifications; dialog shows all stored (or 200 cap).
MAX_HISTORY = 200
_SETTINGS_KEY = "notification/history"


def _settings() -> QSettings:
    return QSettings("MonoStudio26", "MonoStudio26")


@dataclass
class NotificationEntry:
    toast_type: ToastType
    message: str
    at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.at, (int, float)):
            self.at = datetime.fromtimestamp(self.at)

    def to_dict(self) -> dict:
        return {
            "toast_type": self.toast_type,
            "message": self.message,
            "at": self.at.isoformat(),
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
        return cls(toast_type=t, message=str(msg), at=at)


_history: deque[NotificationEntry] = deque(maxlen=MAX_HISTORY)


def _load_from_settings() -> None:
    raw = _settings().value(_SETTINGS_KEY)
    if not raw:
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
    for entry in data:  # saved as oldest first (same as deque order)
        if not isinstance(entry, dict):
            continue
        e = NotificationEntry.from_dict(entry)
        if e is not None:
            _history.append(e)


def _save_to_settings() -> None:
    arr = [e.to_dict() for e in _history]
    _settings().setValue(_SETTINGS_KEY, json.dumps(arr, ensure_ascii=False))


# Load persisted history on first import
_load_from_settings()


def append(toast_type: ToastType, message: str) -> None:
    _history.append(NotificationEntry(toast_type=toast_type, message=message, at=datetime.now()))
    _save_to_settings()


def recent(n: int = 5) -> list[NotificationEntry]:
    """Last n entries, newest first."""
    items = list(_history)
    items.reverse()
    return items[:n]


def all_entries() -> list[NotificationEntry]:
    """All stored entries, newest first."""
    items = list(_history)
    items.reverse()
    return items


def count() -> int:
    return len(_history)
