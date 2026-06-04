"""Windows user-level autostart via HKCU Run key."""

from __future__ import annotations

import logging
import sys

_log = logging.getLogger("monostudio.windows_autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MONOS"


def is_available() -> bool:
    return sys.platform == "win32"


def is_autostart_enabled() -> bool:
    if not is_available():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False
    except Exception:
        _log.debug("is_autostart_enabled failed", exc_info=True)
        return False


def set_autostart(enabled: bool) -> tuple[bool, str]:
    """
    Register or remove MONOS in HKCU Run.
    Returns (success, user-facing message).
    """
    if not is_available():
        return False, "Start with Windows is only available on Windows."

    try:
        import winreg

        from monostudio.core.app_launch import app_autostart_command

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                cmd = app_autostart_command(startup=True)
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
                return True, "MONOS will start when you sign in to Windows."
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except OSError:
                pass
            return True, "MONOS will no longer start automatically with Windows."
    except PermissionError:
        return False, "Could not update Windows startup settings (permission denied)."
    except OSError as e:
        _log.warning("set_autostart failed: %s", e)
        return False, f"Could not update Windows startup settings: {e}"
    except Exception as e:
        _log.warning("set_autostart failed", exc_info=True)
        return False, f"Could not update Windows startup settings: {e}"
