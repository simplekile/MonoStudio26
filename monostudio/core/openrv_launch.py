"""Launch OpenRV (rv.exe) as an external review sidecar from MONOS."""

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
from monostudio.ui_qt.video_preview_settings import read_openrv_executable

_log = logging.getLogger("monostudio.openrv")


@dataclass(frozen=True)
class OpenRvLaunchResult:
    ok: bool
    error: str | None = None


def _norm_exe(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    return s


def _windows_registry_rv_exe() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore
    except Exception:
        return None

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\rv.exe"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\rv.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\rv.exe"),
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


def _windows_common_rv_paths() -> list[str]:
    patterns = [
        r"C:\Program Files\OpenRV*\bin\rv.exe",
        r"C:\Program Files\ASWF\OpenRV*\bin\rv.exe",
        r"C:\Program Files (x86)\OpenRV*\bin\rv.exe",
    ]
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        patterns.append(os.path.join(local, "OpenRV*", "bin", "rv.exe"))
    found: list[str] = []
    for pattern in patterns:
        for match in glob(pattern):
            p = Path(match)
            if p.is_file():
                found.append(str(p.resolve()))
    return found


def resolve_openrv_executable(settings: QSettings | None = None) -> str | None:
    """Return path to rv.exe: saved path, registry/common installs, then PATH."""
    raw = read_openrv_executable(settings)
    if raw:
        try:
            p = Path(raw)
            if p.is_file():
                return str(p.resolve())
        except OSError:
            pass

    reg = _windows_registry_rv_exe()
    if reg:
        return reg

    for candidate in _windows_common_rv_paths():
        if Path(candidate).is_file():
            return candidate

    which = shutil.which("rv")
    if which:
        try:
            p = Path(which)
            if p.is_file():
                return str(p.resolve())
        except OSError:
            pass
    if sys.platform == "win32":
        which_exe = shutil.which("rv.exe")
        if which_exe:
            try:
                p = Path(which_exe)
                if p.is_file():
                    return str(p.resolve())
            except OSError:
                pass
    return None


def is_openrv_available(settings: QSettings | None = None) -> bool:
    return resolve_openrv_executable(settings) is not None


def validate_openrv_executable(path: Path) -> bool:
    if not path.is_file():
        return False
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
        return b"RV" in out or b"OpenRV" in out or b"-fps" in out
    except (subprocess.TimeoutExpired, OSError):
        return path.name.lower() in ("rv.exe", "rv")


def build_openrv_argv(
    executable: str,
    media_path: Path,
    *,
    fps: float | None = None,
    frame: int | None = None,
) -> list[str]:
    """Build argv for subprocess.Popen (paths with spaces stay single tokens)."""
    argv: list[str] = [executable]
    if fps is not None and fps > 0:
        argv.extend(["-fps", _format_fps(fps)])

    path_str = str(media_path)
    if frame is not None:
        # Per-source options require bracket grouping (spaces around brackets).
        argv.extend(["[", "-rs", str(int(frame)), path_str, "]"])
    else:
        argv.append(path_str)
    return argv


def _format_fps(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-6:
        return str(int(round(fps)))
    return f"{fps:g}"


def _resolve_media_path(media_path: Path) -> Path | None:
    try:
        p = media_path.resolve()
    except OSError:
        return None
    if p.is_file() or p.is_dir():
        return p
    return None


def launch_openrv(
    settings: QSettings | None,
    media_path: Path,
    *,
    fps: float | None = None,
    frame: int | None = None,
) -> OpenRvLaunchResult:
    exe = resolve_openrv_executable(settings)
    if not exe:
        return OpenRvLaunchResult(
            ok=False,
            error="OpenRV is not configured. Set the rv.exe path in Settings → General → Video player.",
        )

    resolved = _resolve_media_path(media_path)
    if resolved is None:
        return OpenRvLaunchResult(ok=False, error=f"Media not found: {media_path}")

    argv = build_openrv_argv(exe, resolved, fps=fps, frame=frame)
    cwd = str(resolved.parent) if resolved.is_file() else str(resolved)
    try:
        subprocess.Popen(
            argv,
            cwd=cwd,
            close_fds=True,
            **hide_console_subprocess_kwargs(),
        )
        _log.debug("launched OpenRV argv=%s cwd=%s", argv, cwd)
        return OpenRvLaunchResult(ok=True)
    except OSError as e:
        _log.warning("OpenRV launch failed: %s", e)
        return OpenRvLaunchResult(ok=False, error=f"Could not start OpenRV: {e}")


def launch_openrv_review(
    settings: QSettings | None,
    request: object,
) -> OpenRvLaunchResult:
    """Launch OpenRV for a ReviewOpenRequest (video file or image sequence folder)."""
    from monostudio.ui_qt.video_preview_context import ReviewMediaKind, ReviewOpenRequest

    if not isinstance(request, ReviewOpenRequest):
        return OpenRvLaunchResult(ok=False, error="Invalid review request.")

    if request.media_kind == ReviewMediaKind.video:
        if request.path is None:
            return OpenRvLaunchResult(ok=False, error="No video path to open.")
        return launch_openrv(settings, request.path, fps=None, frame=None)

    if request.sequence_folder is None:
        return OpenRvLaunchResult(ok=False, error="No sequence folder to open.")
    fps = float(request.fps) if request.fps and request.fps > 0 else None
    return launch_openrv(settings, request.sequence_folder, fps=fps, frame=None)
