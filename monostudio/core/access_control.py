"""
Session roles (admin / developer) unlocked against keys bundled in the repository.

Keys are read from monostudio.core.access_keys_bundled only — not from user-writable
paths or environment variables. Change keys by editing that module and shipping a new build.

QSettings is used only for per-user preferences: verbose debug, splash duration.
"""

from __future__ import annotations

import hmac
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from monostudio.core.access_keys_bundled import BUNDLED_ADMIN_KEY, BUNDLED_DEV_KEY

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings

KEY_DEBUG_VERBOSE = "debug/verbose_ui_logging"
KEY_SPLASH_MS = "ui/splash_display_ms"
KEY_ACCESS_REMEMBER = "access_remember"
KEY_ACCESS_REMEMBER_PREFERRED = "access_remember_preferred"

DEFAULT_SPLASH_DISPLAY_MS = 2000
MIN_SPLASH_DISPLAY_MS = 0
MAX_SPLASH_DISPLAY_MS = 60_000


class AccessRole(IntEnum):
    NONE = 0
    ADMIN = 1
    DEV = 2


_session_role: AccessRole = AccessRole.NONE


def _norm(s: str) -> str:
    return (s or "").strip()


def _secure_str_equals(a: str, b: str) -> bool:
    ae, be = a.encode("utf-8"), b.encode("utf-8")
    if len(ae) != len(be):
        return False
    return hmac.compare_digest(ae, be)


def bundled_access_keys_module_path() -> Path:
    """Absolute path to the bundled keys module (for display in Settings)."""
    import monostudio.core.access_keys_bundled as mod

    return Path(mod.__file__).resolve()


def get_effective_access_keys() -> tuple[str, str]:
    return _norm(BUNDLED_ADMIN_KEY), _norm(BUNDLED_DEV_KEY)


def admin_key_configured() -> bool:
    return bool(get_effective_access_keys()[0])


def dev_key_configured() -> bool:
    return bool(get_effective_access_keys()[1])


def has_access_restrictions() -> bool:
    a, d = get_effective_access_keys()
    return bool(a or d)


def session_role() -> AccessRole:
    return _session_role


def set_session_role(role: AccessRole) -> None:
    global _session_role
    _session_role = role


def clear_session() -> None:
    global _session_role
    _session_role = AccessRole.NONE


def read_access_remember_preferred() -> bool:
    """Default for Settings → Access → Remember unlock on this device."""
    from monostudio.core.user_identity import _read_app_settings

    return bool(_read_app_settings().get(KEY_ACCESS_REMEMBER_PREFERRED, False))


def write_access_remember_preferred(on: bool) -> None:
    from monostudio.core.user_identity import update_app_settings

    update_app_settings({KEY_ACCESS_REMEMBER_PREFERRED: bool(on)})


def remember_access_session(role: AccessRole) -> None:
    """Persist unlock for this machine (app_settings.json, not synced)."""
    from monostudio.core.user_identity import device_fingerprint, update_app_settings

    if role == AccessRole.NONE:
        return
    tag = "dev" if role == AccessRole.DEV else "admin"
    update_app_settings(
        {
            KEY_ACCESS_REMEMBER: {
                "device_fp": device_fingerprint(),
                "role": tag,
            }
        }
    )


def forget_remembered_access() -> None:
    from monostudio.core.user_identity import _read_app_settings, _write_app_settings

    data = _read_app_settings()
    if KEY_ACCESS_REMEMBER in data:
        del data[KEY_ACCESS_REMEMBER]
        _write_app_settings(data)


def restore_remembered_access() -> AccessRole | None:
    """Re-apply admin/dev session from a prior Remember unlock on this device."""
    global _session_role
    if not has_access_restrictions():
        return None
    from monostudio.core.user_identity import _read_app_settings, device_fingerprint

    raw = _read_app_settings().get(KEY_ACCESS_REMEMBER)
    if not isinstance(raw, dict):
        return None
    if str(raw.get("device_fp") or "").strip() != device_fingerprint():
        return None
    tag = str(raw.get("role") or "").strip().lower()
    if tag == "dev" and dev_key_configured():
        _session_role = AccessRole.DEV
        return AccessRole.DEV
    if tag == "admin" and admin_key_configured():
        _session_role = AccessRole.ADMIN
        return AccessRole.ADMIN
    return None


def try_unlock(entered: str, *, remember: bool = False) -> AccessRole | None:
    """Set session role if entered key matches dev (preferred) or admin. Returns new role or None."""
    global _session_role
    key = _norm(entered)
    if not key:
        return None
    ak, dk = get_effective_access_keys()
    if dk and _secure_str_equals(key, dk):
        _session_role = AccessRole.DEV
        if remember:
            remember_access_session(AccessRole.DEV)
        return AccessRole.DEV
    if ak and _secure_str_equals(key, ak):
        _session_role = AccessRole.ADMIN
        if remember:
            remember_access_session(AccessRole.ADMIN)
        return AccessRole.ADMIN
    return None


def is_admin_capable() -> bool:
    if not has_access_restrictions():
        return True
    return _session_role in (AccessRole.ADMIN, AccessRole.DEV)


def is_dev_session() -> bool:
    if not dev_key_configured():
        return False
    return _session_role == AccessRole.DEV


def read_splash_display_ms(settings: QSettings | None) -> int:
    if settings is None:
        return DEFAULT_SPLASH_DISPLAY_MS
    try:
        v = int(settings.value(KEY_SPLASH_MS, DEFAULT_SPLASH_DISPLAY_MS, int))
    except (TypeError, ValueError):
        v = DEFAULT_SPLASH_DISPLAY_MS
    return max(MIN_SPLASH_DISPLAY_MS, min(MAX_SPLASH_DISPLAY_MS, v))


def write_splash_display_ms(settings: QSettings, ms: int) -> None:
    v = max(MIN_SPLASH_DISPLAY_MS, min(MAX_SPLASH_DISPLAY_MS, int(ms)))
    settings.setValue(KEY_SPLASH_MS, v)


def read_verbose_debug_enabled(settings: QSettings | None) -> bool:
    if settings is None:
        return False
    try:
        return bool(settings.value(KEY_DEBUG_VERBOSE, False, type=bool))
    except Exception:
        return False


def write_verbose_debug_enabled(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_DEBUG_VERBOSE, bool(enabled))
