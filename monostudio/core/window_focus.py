"""Force a top-level window to the foreground (Windows deep link / second instance)."""

from __future__ import annotations

import logging
import sys

_log = logging.getLogger("monostudio.window_focus")

# Allow any process to SetForegroundWindow (used by secondary instance before notify).
_ASFW_ANY = -1
_SW_RESTORE = 9
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_SHOWWINDOW = 0x0040
_SWP_NOACTIVATE = 0x0010


def allow_set_foreground_window_any() -> None:
    """Call from the process that currently may own focus (e.g. protocol handler)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(_ASFW_ANY)
    except Exception as e:
        _log.debug("AllowSetForegroundWindow failed: %s", e)


def force_hwnd_to_foreground(hwnd: int) -> bool:
    """Best-effort Windows foreground steal for ``hwnd``. Return True if set as FG."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)

        fg = int(user32.GetForegroundWindow() or 0)
        if fg == hwnd:
            return True

        current_tid = int(kernel32.GetCurrentThreadId())
        fg_tid = 0
        if fg:
            pid = wintypes.DWORD()
            fg_tid = int(user32.GetWindowThreadProcessId(fg, ctypes.byref(pid)) or 0)

        attached = False
        if fg_tid and fg_tid != current_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, current_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            # Brief TOPMOST nudge — common fallback when SetForegroundWindow is ignored.
            flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW
            user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags)
            user32.SetWindowPos(
                hwnd,
                _HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
            )
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, current_tid, False)

        return int(user32.GetForegroundWindow() or 0) == hwnd
    except Exception as e:
        _log.debug("force_hwnd_to_foreground failed: %s", e)
        return False


def force_widget_to_foreground(widget) -> bool:
    """Raise a Qt widget/window to the foreground. No-op on non-Windows."""
    if widget is None:
        return False
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False
    return force_hwnd_to_foreground(hwnd)
