"""Windows Action Center toasts for @mention alerts (Win32 only)."""

from __future__ import annotations

import logging
import os
import sys
import winreg
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windows_toasts import InteractableWindowsToaster

_log = logging.getLogger("monostudio.windows_toast")

MONOS_AUMID = "MonoStudio26.MONOS"
_TOASTER_APP_TEXT = "MONOS"
_REGISTRY_KEY = rf"SOFTWARE\Classes\AppUserModelId\{MONOS_AUMID}"
_START_MENU_LNK = "MONOS.lnk"

_toaster: InteractableWindowsToaster | None = None
_focus_callback: Callable[[], None] | None = None
_aumid_registered = False
_registry_registered = False
_shortcut_ensured = False


def _start_menu_lnk_path() -> Path:
    return (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / _START_MENU_LNK
    )


def _icon_uri_for_registry() -> str | None:
    try:
        from monostudio.core.app_paths import get_app_base_path

        icon = get_app_base_path() / "monostudio_data" / "icons" / "app.ico"
        if icon.is_file():
            return str(icon.resolve())
    except Exception:
        pass
    return None


def _app_launch_target() -> tuple[str, str, str]:
    """Return (executable, arguments, working_directory) for Start Menu shortcut."""
    from monostudio.core.app_launch import app_launch_target

    return app_launch_target()


def ensure_start_menu_shortcut() -> bool:
    """
    Desktop apps need a Start Menu shortcut with System.AppUserModel.ID (MS docs).
    Required for reliable toasts when running via ``python app.py`` — not only the installed build.
    """
    global _shortcut_ensured
    if sys.platform != "win32":
        return False
    if _shortcut_ensured and _start_menu_lnk_path().is_file():
        return True
    lnk = _start_menu_lnk_path()
    try:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        import pythoncom
        from win32com.propsys import propsys
        from win32com.shell import shell

        target, args, work_dir = _app_launch_target()
        link = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink,
            None,
            pythoncom.CLSCTX_INPROC_SERVER,
            shell.IID_IShellLink,
        )
        link.SetPath(target)
        if args:
            link.SetArguments(args)
        link.SetWorkingDirectory(work_dir)
        link.SetDescription("MONOS Studio")
        icon_uri = _icon_uri_for_registry()
        if icon_uri:
            link.SetIconLocation(icon_uri, 0)

        persist = link.QueryInterface(pythoncom.IID_IPersistFile)
        persist.Save(str(lnk), True)

        store = link.QueryInterface(propsys.IID_IPropertyStore)
        key = propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID")
        pv = propsys.PROPVARIANTType(MONOS_AUMID, pythoncom.VT_BSTR)
        store.SetValue(key, pv)
        store.Commit()
        persist.Save(str(lnk), True)

        _shortcut_ensured = True
        _log.debug("Start Menu shortcut ready: %s", lnk)
        return True
    except Exception:
        _log.warning("Could not create MONOS Start Menu shortcut for Windows toasts", exc_info=True)
        return False


def register_aumid_in_registry() -> None:
    """Register AUMID in HKCU (display name + icon for Action Center)."""
    global _registry_registered
    if sys.platform != "win32" or _registry_registered:
        return
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, _TOASTER_APP_TEXT)
            icon_uri = _icon_uri_for_registry()
            if icon_uri:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_uri)
        _registry_registered = True
    except OSError:
        _log.debug("AUMID registry registration failed", exc_info=True)


def register_aumid_on_startup() -> None:
    """Bind process + registry + shortcut so toasts work (dev python and frozen exe)."""
    global _aumid_registered
    if sys.platform != "win32":
        return
    register_aumid_in_registry()
    ensure_start_menu_shortcut()
    if _aumid_registered:
        return
    try:
        import ctypes

        hr = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(MONOS_AUMID)
        if hr not in (0, 1):  # S_OK / S_FALSE
            _log.debug("SetCurrentProcessExplicitAppUserModelID returned %s", hr)
    except Exception:
        _log.debug("AUMID process registration failed", exc_info=True)
    _aumid_registered = True


def set_toast_focus_callback(callback: Callable[[], None] | None) -> None:
    """Called when user activates a Windows toast (phase 1: focus main window)."""
    global _focus_callback
    _focus_callback = callback


def is_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from windows_toasts import InteractableWindowsToaster, Toast  # noqa: F401

        return True
    except ImportError:
        return False


def toast_readiness() -> tuple[bool, str]:
    """(ready, user-facing hint) for Settings / diagnostics."""
    if sys.platform != "win32":
        return False, "Windows notifications are only available on Windows."
    if not is_available():
        return False, (
            "Install: pip install \"windows-toasts>=1.3.1\" "
            "(see requirements.txt)."
        )
    register_aumid_on_startup()
    if not _start_menu_lnk_path().is_file():
        return False, (
            "Could not register MONOS in the Start Menu. "
            "Try running the app once as your user account, or use the installed build."
        )
    if not _notifications_enabled_for_aumid():
        return False, (
            "Notifications for MONOS are turned off in Windows. "
            "Open Settings → System → Notifications and enable MONOS."
        )
    return True, "Ready. Use 'Send test notification' below."


def _get_toaster() -> InteractableWindowsToaster | None:
    global _toaster
    if sys.platform != "win32":
        return None
    if _toaster is not None:
        return _toaster
    try:
        from windows_toasts import InteractableWindowsToaster

        _toaster = InteractableWindowsToaster(_TOASTER_APP_TEXT, MONOS_AUMID)
        return _toaster
    except Exception:
        _log.debug("InteractableWindowsToaster init failed", exc_info=True)
        return None


def _on_toast_activated(_event) -> None:
    cb = _focus_callback
    if cb is None:
        return
    try:
        cb()
    except Exception:
        _log.debug("toast focus callback failed", exc_info=True)


def show_mention_toast(title: str, body: str) -> bool:
    """
    Show a plain-text Windows toast. Returns True if shown; False on failure (caller may fallback).
    """
    if sys.platform != "win32":
        return False
    register_aumid_on_startup()
    ready, hint = toast_readiness()
    if not ready:
        _log.warning("Windows toast not ready: %s", hint)
        return False
    toaster = _get_toaster()
    if toaster is None:
        return False
    try:
        from windows_toasts import Toast
        from windows_toasts.toast_audio import AudioSource, ToastAudio
        from windows_toasts.wrappers import ToastDuration, ToastScenario
    except ImportError:
        _log.warning("windows-toasts is not installed; cannot show Windows notifications")
        return False

    title_s = (title or "MONOS").strip()[:64]
    body_s = (body or "").strip()
    if not body_s:
        body_s = "New mention"
    body_s = body_s[:256]

    failed = {"value": False}

    def _on_failed(_event) -> None:
        failed["value"] = True
        _log.warning("Windows toast failed event: %s", body_s[:80])

    try:
        toast = Toast()
        toast.text_fields = [title_s, body_s]
        toast.scenario = ToastScenario.Default
        toast.duration = ToastDuration.Long
        toast.audio = ToastAudio(AudioSource.Default)
        toast.on_activated = _on_toast_activated
        toast.on_failed = _on_failed
        toaster.show_toast(toast)
        if failed["value"]:
            return False
        if not _notifications_enabled_for_aumid():
            _log.warning(
                "Windows notifications are disabled for MONOS (AUMID %s). "
                "Open Settings → System → Notifications.",
                MONOS_AUMID,
            )
            return False
        _log.debug("Windows toast shown: %s", body_s[:80])
        return True
    except Exception:
        _log.warning("Windows mention toast failed", exc_info=True)
        return False


def _notifications_enabled_for_aumid() -> bool:
    """False when user turned off notifications for this AUMID in Windows Settings."""
    if sys.platform != "win32":
        return True
    try:
        from winrt.windows.ui.notifications import ToastNotificationManager

        notifier = ToastNotificationManager.create_toast_notifier_with_id(MONOS_AUMID)
        setting = notifier.setting
        name = getattr(setting, "name", str(setting)).upper()
        return name == "ENABLED"
    except Exception:
        return True
