"""QSettings helpers for notification delivery preferences."""

from __future__ import annotations

import sys
from typing import Literal

from PySide6.QtCore import QSettings

MentionDelivery = Literal["builtin", "windows"]

KEY_MENTION_DELIVERY = "notification/mention_delivery"
DEFAULT_MENTION_DELIVERY: MentionDelivery = "builtin"


def read_mention_delivery(settings: QSettings | None = None) -> MentionDelivery:
    """Return how @mention popups are delivered. Non-Windows always uses built-in."""
    if sys.platform != "win32":
        return "builtin"
    s = settings or QSettings("MonoStudio26", "MonoStudio26")
    try:
        raw = str(s.value(KEY_MENTION_DELIVERY, DEFAULT_MENTION_DELIVERY) or "").strip().lower()
    except Exception:
        return DEFAULT_MENTION_DELIVERY
    if raw == "windows":
        return "windows"
    return "builtin"


def write_mention_delivery(settings: QSettings, mode: MentionDelivery) -> None:
    value = "windows" if mode == "windows" and sys.platform == "win32" else "builtin"
    settings.setValue(KEY_MENTION_DELIVERY, value)
    try:
        settings.sync()
    except Exception:
        pass
