"""Session activity log — operational messages shown in the app footer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from PySide6.QtCore import QObject, Signal

LogLevel = Literal["info", "success", "warning", "error"]

_MAX_ENTRIES = 50


@dataclass(frozen=True)
class ActivityLogEntry:
    level: LogLevel
    message: str
    at: datetime


class _ActivityLogService(QObject):
    message_changed = Signal(str, str)  # message, level

    def __init__(self) -> None:
        super().__init__()
        self._entries: deque[ActivityLogEntry] = deque(maxlen=_MAX_ENTRIES)

    def append(self, message: str, *, level: LogLevel = "info") -> None:
        msg = (message or "").strip()
        if not msg:
            return
        self._entries.append(ActivityLogEntry(level=level, message=msg, at=datetime.now()))
        self.message_changed.emit(msg, level)

    def latest(self) -> ActivityLogEntry | None:
        if not self._entries:
            return None
        return self._entries[-1]

    def all_entries(self) -> list[ActivityLogEntry]:
        return list(self._entries)


activity_log = _ActivityLogService()
