"""QSettings helpers for system tray and close / startup behavior."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QSettings

SETTINGS_ORG = "MonoStudio26"
SETTINGS_APP = "MonoStudio26"

CloseAction = Literal["minimize", "quit", "unset"]

KEY_TRAY_ENABLED = "tray/enabled"
KEY_CLOSE_ACTION = "tray/close_action"
KEY_CLOSE_PROMPT_SHOWN = "tray/close_prompt_shown"
KEY_START_WITH_WINDOWS = "tray/start_with_windows"
KEY_START_MINIMIZED_TO_TRAY = "tray/start_minimized_to_tray"
KEY_STARTUP_SPLASH_MS = "tray/startup_splash_ms"

DEFAULT_CLOSE_ACTION: CloseAction = "unset"
DEFAULT_STARTUP_SPLASH_MS = 1600


def default_settings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def _sync(settings: QSettings) -> None:
    try:
        settings.sync()
    except Exception:
        pass


def read_tray_enabled(settings: QSettings | None = None) -> bool:
    s = settings or default_settings()
    try:
        v = s.value(KEY_TRAY_ENABLED, True, type=bool)
        return bool(v) if v is not None else True
    except Exception:
        return True


def write_tray_enabled(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_TRAY_ENABLED, bool(enabled))
    _sync(settings)


def read_close_action(settings: QSettings | None = None) -> CloseAction:
    s = settings or default_settings()
    try:
        raw = str(s.value(KEY_CLOSE_ACTION, DEFAULT_CLOSE_ACTION) or "").strip().lower()
    except Exception:
        return DEFAULT_CLOSE_ACTION
    if raw in ("minimize", "quit", "unset"):
        return raw  # type: ignore[return-value]
    return DEFAULT_CLOSE_ACTION


def write_close_action(settings: QSettings, action: CloseAction) -> None:
    value = action if action in ("minimize", "quit", "unset") else DEFAULT_CLOSE_ACTION
    settings.setValue(KEY_CLOSE_ACTION, value)
    _sync(settings)


def read_close_prompt_shown(settings: QSettings | None = None) -> bool:
    s = settings or default_settings()
    try:
        v = s.value(KEY_CLOSE_PROMPT_SHOWN, False, type=bool)
        return bool(v) if v is not None else False
    except Exception:
        return False


def write_close_prompt_shown(settings: QSettings, shown: bool) -> None:
    settings.setValue(KEY_CLOSE_PROMPT_SHOWN, bool(shown))
    _sync(settings)


def read_start_with_windows(settings: QSettings | None = None) -> bool:
    s = settings or default_settings()
    try:
        v = s.value(KEY_START_WITH_WINDOWS, False, type=bool)
        return bool(v) if v is not None else False
    except Exception:
        return False


def write_start_with_windows(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_START_WITH_WINDOWS, bool(enabled))
    _sync(settings)


def read_start_minimized_to_tray(settings: QSettings | None = None) -> bool:
    s = settings or default_settings()
    try:
        v = s.value(KEY_START_MINIMIZED_TO_TRAY, True, type=bool)
        return bool(v) if v is not None else True
    except Exception:
        return True


def write_start_minimized_to_tray(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_START_MINIMIZED_TO_TRAY, bool(enabled))
    _sync(settings)


def read_startup_splash_ms(settings: QSettings | None = None) -> int:
    s = settings or default_settings()
    try:
        v = int(s.value(KEY_STARTUP_SPLASH_MS, DEFAULT_STARTUP_SPLASH_MS))
        return max(0, min(30_000, v))
    except Exception:
        return DEFAULT_STARTUP_SPLASH_MS


def write_startup_splash_ms(settings: QSettings, ms: int) -> None:
    settings.setValue(KEY_STARTUP_SPLASH_MS, max(0, min(30_000, int(ms))))
    _sync(settings)


def should_prompt_close_behavior(settings: QSettings | None = None) -> bool:
    """True when user has not chosen a remembered close action."""
    s = settings or default_settings()
    if read_close_action(s) == "unset":
        return True
    return not read_close_prompt_shown(s)
