"""
Open folders in the system file manager with reuse of an existing Explorer window on Windows.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger("monostudio.shell_open")


def _normalize_folder(path: Path) -> Path | None:
    try:
        resolved = path.resolve()
        if not resolved.is_dir():
            return None
        return resolved
    except OSError:
        return None


def _path_from_location_url(url: str) -> Path | None:
    if not url:
        return None
    raw = url.strip()
    if not raw.lower().startswith("file:"):
        return None
    try:
        parsed = urlparse(raw)
        path_str = unquote(parsed.path or "")
        if sys.platform == "win32":
            if path_str.startswith("/") and len(path_str) >= 3 and path_str[2] == ":":
                path_str = path_str[1:]
            path_str = path_str.replace("/", "\\")
        return Path(path_str)
    except (OSError, ValueError):
        return None


def _paths_equal(a: Path, b: Path) -> bool:
    try:
        ar = a.resolve()
        br = b.resolve()
    except OSError:
        return False
    if sys.platform == "win32":
        return str(ar).casefold() == str(br).casefold()
    return ar == br


def _open_folder_desktop_services(folder: Path) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _focus_shell_window(window: object) -> bool:
    try:
        window.Visible = True  # type: ignore[attr-defined]
        hwnd = int(window.HWND)  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return False
    if not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except OSError:
        return False


def _try_focus_existing_explorer(folder: Path) -> bool:
    try:
        from comtypes.client import CreateObject
    except ImportError:
        logger.debug("comtypes unavailable; skipping Explorer window reuse")
        return False

    try:
        shell = CreateObject("Shell.Application")
        windows = shell.Windows()
        count = int(windows.Count)
    except Exception as e:
        logger.debug("Shell.Application enumeration failed: %s", e)
        return False

    for i in range(count):
        try:
            window = windows.Item(i)
        except Exception:
            continue
        location_url = ""
        try:
            location_url = str(window.LocationURL or "")
        except Exception:
            pass
        loc_path = _path_from_location_url(location_url)
        if loc_path is None or not _paths_equal(loc_path, folder):
            continue
        if _focus_shell_window(window):
            logger.debug("Focused existing Explorer window for %s", folder)
            return True
    return False


def open_folder(path: Path) -> None:
    """Open a folder in the file manager; focus an existing Explorer window on Windows if same path."""
    folder = _normalize_folder(path)
    if folder is None:
        return

    if sys.platform == "win32" and _try_focus_existing_explorer(folder):
        return

    logger.debug("Opening folder via desktop services: %s", folder)
    _open_folder_desktop_services(folder)


def reveal_in_folder(path: Path, *, select: bool = True) -> None:
    """Reveal a file or folder in the file manager (select file on Windows when possible)."""
    try:
        target = path.resolve()
    except OSError:
        return

    if sys.platform == "win32" and select and target.is_file():
        try:
            subprocess.Popen(
                ["explorer", "/select,", str(target)],
                **(
                    {"creationflags": subprocess.CREATE_NO_WINDOW}
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else {}
                ),
            )
            return
        except (OSError, ValueError) as e:
            logger.debug("explorer /select failed: %s", e)

    parent = target if target.is_dir() else target.parent
    if parent.is_dir():
        open_folder(parent)
