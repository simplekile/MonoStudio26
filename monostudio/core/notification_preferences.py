"""QSettings helpers for notification delivery preferences."""

from __future__ import annotations

import sys
from typing import Literal

from PySide6.QtCore import QSettings

MentionDelivery = Literal["builtin", "windows"]

KEY_MENTION_DELIVERY = "notification/mention_delivery"
KEY_NOTIFICATION_VIETNAMESE = "notification/vietnamese"
KEY_DISCORD_DISABLED_LOCALLY = "discord/disabled_locally"
DEFAULT_MENTION_DELIVERY: MentionDelivery = "builtin"
DEFAULT_NOTIFICATION_VIETNAMESE = True
DEFAULT_DISCORD_DISABLED_LOCALLY = False


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


def read_notification_vietnamese(settings: QSettings | None = None) -> bool:
    """When True, user-facing notification copy uses Vietnamese (default)."""
    s = settings or QSettings("MonoStudio26", "MonoStudio26")
    try:
        raw = s.value(KEY_NOTIFICATION_VIETNAMESE, DEFAULT_NOTIFICATION_VIETNAMESE)
        if raw is None:
            return DEFAULT_NOTIFICATION_VIETNAMESE
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in ("0", "false", "no", "off"):
                return False
            if lowered in ("1", "true", "yes", "on"):
                return True
            return DEFAULT_NOTIFICATION_VIETNAMESE
        return bool(raw)
    except Exception:
        return DEFAULT_NOTIFICATION_VIETNAMESE


def write_notification_vietnamese(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_NOTIFICATION_VIETNAMESE, bool(enabled))
    try:
        settings.sync()
    except Exception:
        pass


def read_discord_disabled_locally(settings: QSettings | None = None) -> bool:
    """When True, this machine does not POST Discord webhooks (workspace config unchanged)."""
    s = settings or QSettings("MonoStudio26", "MonoStudio26")
    try:
        raw = s.value(KEY_DISCORD_DISABLED_LOCALLY, DEFAULT_DISCORD_DISABLED_LOCALLY)
        if raw is None:
            return DEFAULT_DISCORD_DISABLED_LOCALLY
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in ("0", "false", "no", "off"):
                return False
            if lowered in ("1", "true", "yes", "on"):
                return True
            return DEFAULT_DISCORD_DISABLED_LOCALLY
        return bool(raw)
    except Exception:
        return DEFAULT_DISCORD_DISABLED_LOCALLY


def write_discord_disabled_locally(settings: QSettings, disabled: bool) -> None:
    settings.setValue(KEY_DISCORD_DISABLED_LOCALLY, bool(disabled))
    try:
        settings.sync()
    except Exception:
        pass
