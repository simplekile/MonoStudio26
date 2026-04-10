"""Resolve ffmpeg/ffprobe for preview (optional user path in QSettings + PATH)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings

from monostudio.core.subprocess_win import hide_console_subprocess_kwargs

SETTINGS_ORG = "MonoStudio26"
SETTINGS_APP = "MonoStudio26"
SETTINGS_KEY_FFMPEG_EXE = "tools/ffmpeg_executable"

# gyan.dev CODEX FFMPEG — stable URLs (aliases track “latest” for that package type).
# Page: https://www.gyan.dev/ffmpeg/builds/
FFMPEG_GYAN_BUILDS_PAGE = "https://www.gyan.dev/ffmpeg/builds/"
FFMPEG_GYAN_RELEASE_ESSENTIALS_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_GYAN_RELEASE_FULL_7Z = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z"
FFMPEG_GYAN_GIT_FULL_7Z = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-git-full.7z"

# Backwards compat for imports expecting the old name.
FFMPEG_OFFICIAL_BUILDS_URL = FFMPEG_GYAN_BUILDS_PAGE


def app_qsettings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def read_ffmpeg_executable_path(settings: QSettings | None) -> str:
    s = settings if settings is not None else app_qsettings()
    return (s.value(SETTINGS_KEY_FFMPEG_EXE, "", str) or "").strip()


def write_ffmpeg_executable_path(settings: QSettings, path: str) -> None:
    p = (path or "").strip()
    if p:
        settings.setValue(SETTINGS_KEY_FFMPEG_EXE, p)
    else:
        settings.remove(SETTINGS_KEY_FFMPEG_EXE)


def resolve_ffmpeg_executable(settings: QSettings | None = None) -> str | None:
    """Return path to ffmpeg: saved path if valid file, else ``shutil.which('ffmpeg')``."""
    raw = read_ffmpeg_executable_path(settings)
    if raw:
        try:
            p = Path(raw)
            if p.is_file():
                return str(p.resolve())
        except OSError:
            pass
    return shutil.which("ffmpeg")


def resolve_ffprobe_executable(settings: QSettings | None = None) -> str | None:
    """Prefer ffprobe next to configured ffmpeg; else PATH."""
    ff = resolve_ffmpeg_executable(settings)
    if ff:
        try:
            parent = Path(ff).parent
            name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
            probe = parent / name
            if probe.is_file():
                return str(probe.resolve())
        except OSError:
            pass
    return shutil.which("ffprobe")


def get_ffmpeg_version_line(ffmpeg_exe: str) -> str | None:
    """First line of ``ffmpeg -version``, or None if not runnable."""
    try:
        proc = subprocess.run(
            [ffmpeg_exe, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return None
        line = (proc.stdout or "").strip().splitlines()[0].strip()
        return line or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def get_ffmpeg_version_short(ffmpeg_exe: str) -> str | None:
    """Short label for UI, e.g. ``n7.1.1`` from version line."""
    line = get_ffmpeg_version_line(ffmpeg_exe)
    if not line:
        return None
    m = re.search(r"ffmpeg\s+version\s+(\S+)", line, re.IGNORECASE)
    if m:
        return m.group(1)
    return line[:48] + ("…" if len(line) > 48 else "")


def validate_ffmpeg_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    return get_ffmpeg_version_line(str(path.resolve())) is not None
