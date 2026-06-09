"""Resilient move for explorer drops (Windows sharing locks, Dropbox, preview handles)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

_WIN_SHARING = 32  # ERROR_SHARING_VIOLATION
_WIN_ACCESS = 5  # ERROR_ACCESS_DENIED


def _winerror(exc: OSError) -> int | None:
    return getattr(exc, "winerror", None)


def _retriable_windows(exc: OSError) -> bool:
    if sys.platform != "win32":
        return False
    code = _winerror(exc)
    return code in (_WIN_SHARING, _WIN_ACCESS)


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _move_via_cmd(src: Path, dest: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        subprocess.run(
            ["cmd", "/c", "move", "/Y", str(src), str(dest)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return dest.exists()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def move_path(src: Path, dest: Path, *, max_attempts: int = 6, delay_sec: float = 0.35) -> None:
    """Move *src* to *dest*, retrying Windows sharing/access errors; files fall back to copy+delete."""
    src = Path(src)
    dest = Path(dest)
    if _same_path(src, dest):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)

    last: OSError | None = None
    for attempt in range(max_attempts):
        try:
            shutil.move(str(src), str(dest))
            return
        except OSError as exc:
            last = exc
            if not _retriable_windows(exc) or attempt >= max_attempts - 1:
                break
            time.sleep(delay_sec * (attempt + 1))
            if _move_via_cmd(src, dest):
                return

    if src.is_file() and not src.is_dir():
        shutil.copy2(src, dest)
        for attempt in range(max_attempts):
            try:
                src.unlink()
                return
            except OSError as exc:
                last = exc
                if not _retriable_windows(exc) or attempt >= max_attempts - 1:
                    raise
                time.sleep(delay_sec * (attempt + 1))
        return

    if last is not None:
        raise last
    raise OSError(f"move failed: {src} -> {dest}")
