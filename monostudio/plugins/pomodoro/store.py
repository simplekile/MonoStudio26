"""QSettings persistence for Pomodoro prefs + checklist."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QSettings

from monostudio.core.tray_preferences import SETTINGS_APP, SETTINGS_ORG

PREFIX = "plugins/pomodoro"

KEY_FOCUS_MIN = f"{PREFIX}/focus_minutes"
KEY_SHORT_BREAK_MIN = f"{PREFIX}/short_break_minutes"
KEY_LONG_BREAK_MIN = f"{PREFIX}/long_break_minutes"
KEY_LONG_EVERY = f"{PREFIX}/long_break_every"
KEY_AUTO_START_BREAK = f"{PREFIX}/auto_start_break"
KEY_SOUND = f"{PREFIX}/sound_enabled"
KEY_CUSTOM_SOUND = f"{PREFIX}/custom_sound_path"
KEY_SOUND_LOOP = f"{PREFIX}/sound_loop"  # legacy bool
KEY_SOUND_LOOP_COUNT = f"{PREFIX}/sound_loop_count"
KEY_TRAY_NOTIFY = f"{PREFIX}/tray_notify"
KEY_IN_APP_NOTIFY = f"{PREFIX}/in_app_notify"
KEY_ALWAYS_ON_TOP = f"{PREFIX}/always_on_top"
KEY_CHECKLIST_VISIBLE = f"{PREFIX}/checklist_visible"
KEY_CHECKLIST = f"{PREFIX}/checklist"


@dataclass
class PomodoroPrefs:
    focus_minutes: int = 25
    short_break_minutes: int = 5
    long_break_minutes: int = 15
    long_break_every: int = 4
    auto_start_break: bool = False
    sound_enabled: bool = True
    custom_sound_path: str = ""
    sound_loop_count: int = 1  # 0 = until stopped; 1..99 = play N times
    tray_notify: bool = True
    in_app_notify: bool = True
    always_on_top: bool = False
    checklist_visible: bool = True


@dataclass
class ChecklistItem:
    id: str
    text: str
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "done": bool(self.done)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChecklistItem | None:
        if not isinstance(data, dict):
            return None
        text = str(data.get("text") or "").strip()
        if not text:
            return None
        raw_id = str(data.get("id") or "").strip() or uuid.uuid4().hex[:12]
        return cls(id=raw_id, text=text, done=bool(data.get("done")))


def default_settings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def _sync(settings: QSettings) -> None:
    try:
        settings.sync()
    except Exception:
        pass


def _clamp_min(v: int, *, lo: int = 1, hi: int = 180) -> int:
    return max(lo, min(hi, int(v)))


def read_prefs(settings: QSettings | None = None) -> PomodoroPrefs:
    s = settings or default_settings()

    def _int(key: str, default: int) -> int:
        try:
            v = s.value(key, default, type=int)
            return int(v) if v is not None else default
        except Exception:
            return default

    def _bool(key: str, default: bool) -> bool:
        try:
            v = s.value(key, default, type=bool)
            return bool(v) if v is not None else default
        except Exception:
            return default

    def _str(key: str, default: str = "") -> str:
        try:
            v = s.value(key, default, type=str)
            return str(v).strip() if v is not None else default
        except Exception:
            return default

    def _loop_count() -> int:
        from monostudio.plugins.pomodoro.sound import clamp_loop_count

        raw = s.value(KEY_SOUND_LOOP_COUNT, None)
        if raw is not None:
            try:
                return clamp_loop_count(int(raw))
            except (TypeError, ValueError):
                pass
        # Migrate legacy bool: True → until stopped (0), False → once (1)
        if _bool(KEY_SOUND_LOOP, False):
            return 0
        return 1

    return PomodoroPrefs(
        focus_minutes=_clamp_min(_int(KEY_FOCUS_MIN, 25)),
        short_break_minutes=_clamp_min(_int(KEY_SHORT_BREAK_MIN, 5)),
        long_break_minutes=_clamp_min(_int(KEY_LONG_BREAK_MIN, 15)),
        long_break_every=max(1, min(12, _int(KEY_LONG_EVERY, 4))),
        auto_start_break=_bool(KEY_AUTO_START_BREAK, False),
        sound_enabled=_bool(KEY_SOUND, True),
        custom_sound_path=_str(KEY_CUSTOM_SOUND, ""),
        sound_loop_count=_loop_count(),
        tray_notify=_bool(KEY_TRAY_NOTIFY, True),
        in_app_notify=_bool(KEY_IN_APP_NOTIFY, True),
        always_on_top=_bool(KEY_ALWAYS_ON_TOP, False),
        checklist_visible=_bool(KEY_CHECKLIST_VISIBLE, True),
    )


def write_prefs(settings: QSettings, prefs: PomodoroPrefs) -> None:
    from monostudio.plugins.pomodoro.sound import clamp_loop_count

    settings.setValue(KEY_FOCUS_MIN, _clamp_min(prefs.focus_minutes))
    settings.setValue(KEY_SHORT_BREAK_MIN, _clamp_min(prefs.short_break_minutes))
    settings.setValue(KEY_LONG_BREAK_MIN, _clamp_min(prefs.long_break_minutes))
    settings.setValue(KEY_LONG_EVERY, max(1, min(12, int(prefs.long_break_every))))
    settings.setValue(KEY_AUTO_START_BREAK, bool(prefs.auto_start_break))
    settings.setValue(KEY_SOUND, bool(prefs.sound_enabled))
    settings.setValue(KEY_CUSTOM_SOUND, (prefs.custom_sound_path or "").strip())
    settings.setValue(KEY_SOUND_LOOP_COUNT, clamp_loop_count(prefs.sound_loop_count))
    settings.setValue(KEY_TRAY_NOTIFY, bool(prefs.tray_notify))
    settings.setValue(KEY_IN_APP_NOTIFY, bool(prefs.in_app_notify))
    settings.setValue(KEY_ALWAYS_ON_TOP, bool(prefs.always_on_top))
    settings.setValue(KEY_CHECKLIST_VISIBLE, bool(prefs.checklist_visible))
    _sync(settings)


def write_auto_start_break(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_AUTO_START_BREAK, bool(enabled))
    _sync(settings)


def write_always_on_top(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_ALWAYS_ON_TOP, bool(enabled))
    _sync(settings)


def write_checklist_visible(settings: QSettings, visible: bool) -> None:
    settings.setValue(KEY_CHECKLIST_VISIBLE, bool(visible))
    _sync(settings)


def read_checklist(settings: QSettings | None = None) -> list[ChecklistItem]:
    s = settings or default_settings()
    try:
        raw = s.value(KEY_CHECKLIST, "", type=str) or ""
    except Exception:
        return []
    if not str(raw).strip():
        return []
    try:
        data = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[ChecklistItem] = []
    for row in data:
        item = ChecklistItem.from_dict(row) if isinstance(row, dict) else None
        if item is not None:
            out.append(item)
    return out


def write_checklist(settings: QSettings, items: list[ChecklistItem]) -> None:
    payload = [i.to_dict() for i in items]
    settings.setValue(KEY_CHECKLIST, json.dumps(payload, ensure_ascii=False))
    _sync(settings)


def new_checklist_item(text: str) -> ChecklistItem:
    return ChecklistItem(id=uuid.uuid4().hex[:12], text=text.strip(), done=False)
