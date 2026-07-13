"""Launch DJV View as an external review sidecar from MONOS."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from glob import glob
from pathlib import Path

from PySide6.QtCore import QSettings

from monostudio.core.subprocess_win import hide_console_subprocess_kwargs
from monostudio.ui_qt.video_preview_settings import read_djv_executable

_log = logging.getLogger("monostudio.djv")


@dataclass(frozen=True)
class DjvLaunchResult:
    ok: bool
    error: str | None = None


def _norm_exe(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    return s


def _windows_registry_djv_exe() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore
    except Exception:
        return None

    for name in ("djv.exe", "djv.com"):
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}"),
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{name}"),
        ]
        for root, subkey in keys:
            try:
                with winreg.OpenKey(root, subkey) as k:
                    try:
                        exe, _t = winreg.QueryValueEx(k, "")
                    except FileNotFoundError:
                        exe = ""
                    exe = _norm_exe(str(exe))
                    if exe and Path(exe).is_file():
                        return exe
            except OSError:
                continue
    return None


def _windows_common_djv_paths() -> list[str]:
    patterns = [
        r"C:\Program Files\DJV2\bin\djv.exe",
        r"C:\Program Files\DJV2\bin\djv.com",
        r"C:\Program Files\DJV\bin\djv.exe",
        r"C:\Program Files\DJV*\bin\djv.exe",
        r"C:\Program Files\DJV*\bin\djv.com",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in glob(pattern):
            p = Path(match)
            if p.is_file():
                found.append(str(p.resolve()))
    return found


def resolve_djv_executable(settings: QSettings | None = None) -> str | None:
    """Return path to djv.exe: saved path, registry/common installs, then PATH."""
    raw = read_djv_executable(settings)
    if raw:
        try:
            p = Path(raw)
            if p.is_file():
                return str(p.resolve())
        except OSError:
            pass

    reg = _windows_registry_djv_exe()
    if reg:
        return reg

    for candidate in _windows_common_djv_paths():
        if Path(candidate).is_file():
            return candidate

    for name in ("djv", "djv.exe", "djv.com"):
        which = shutil.which(name)
        if which:
            try:
                p = Path(which)
                if p.is_file():
                    return str(p.resolve())
            except OSError:
                pass
    return None


def is_djv_available(settings: QSettings | None = None) -> bool:
    return resolve_djv_executable(settings) is not None


def validate_djv_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name.lower()
    if name in ("djv.exe", "djv.com", "djv"):
        return True
    try:
        proc = subprocess.run(
            [str(path.resolve()), "-help"],
            capture_output=True,
            timeout=12,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        out = (proc.stdout or b"") + (proc.stderr or b"")
        if proc.returncode == 0:
            return True
        low = out.lower()
        return b"djv" in low and b"image" in low
    except (subprocess.TimeoutExpired, OSError):
        return name.startswith("djv")


def build_djv_argv(executable: str, media_path: Path) -> list[str]:
    return [executable, str(media_path)]


def _resolve_media_path(media_path: Path) -> Path | None:
    try:
        p = media_path.resolve()
    except OSError:
        return None
    if p.is_file() or p.is_dir():
        return p
    return None


def _djv_open_path(media_path: Path) -> Path | None:
    """File path DJV can open (sequence folder → representative frame)."""
    resolved = _resolve_media_path(media_path)
    if resolved is None:
        return None
    if resolved.is_file():
        return resolved
    from monostudio.core.sequence_preview import list_sequence_frames

    frames = list_sequence_frames(resolved)
    if frames:
        return frames[len(frames) // 2]
    try:
        for entry in resolved.iterdir():
            if entry.is_file():
                return entry
    except OSError:
        return None
    return None


def launch_djv(
    settings: QSettings | None,
    media_path: Path,
) -> DjvLaunchResult:
    exe = resolve_djv_executable(settings)
    if not exe:
        return DjvLaunchResult(
            ok=False,
            error="DJV is not configured. Set the djv.exe path in Settings → General → Video player.",
        )

    open_path = _djv_open_path(media_path)
    if open_path is None:
        return DjvLaunchResult(ok=False, error=f"Media not found: {media_path}")

    argv = build_djv_argv(exe, open_path)
    cwd = str(open_path.parent)
    try:
        subprocess.Popen(
            argv,
            cwd=cwd,
            close_fds=True,
            **hide_console_subprocess_kwargs(),
        )
        _log.debug("launched DJV argv=%s cwd=%s", argv, cwd)
        return DjvLaunchResult(ok=True)
    except OSError as e:
        _log.warning("DJV launch failed: %s", e)
        return DjvLaunchResult(ok=False, error=f"Could not start DJV: {e}")


def launch_djv_review(
    settings: QSettings | None,
    request: object,
) -> DjvLaunchResult:
    """Launch DJV for a ReviewOpenRequest (video file or image sequence folder)."""
    from monostudio.ui_qt.video_preview_context import ReviewMediaKind, ReviewOpenRequest

    if not isinstance(request, ReviewOpenRequest):
        return DjvLaunchResult(ok=False, error="Invalid review request.")

    if request.media_kind == ReviewMediaKind.video:
        if request.path is None:
            return DjvLaunchResult(ok=False, error="No video path to open.")
        return launch_djv(settings, request.path)

    if request.sequence_folder is None:
        return DjvLaunchResult(ok=False, error="No sequence folder to open.")
    return launch_djv(settings, request.sequence_folder)
