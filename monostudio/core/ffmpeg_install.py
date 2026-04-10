"""Download validation + extract Gyan ``ffmpeg-release-essentials.zip`` into LocalAppData and register path."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

def ffmpeg_bundle_install_root() -> Path:
    """``%LOCALAPPDATA%/MonoStudio/tools/ffmpeg`` (fallback: temp)."""
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        return Path(localappdata) / "MonoStudio" / "tools" / "ffmpeg"
    return Path(tempfile.gettempdir()) / "MonoStudio" / "tools" / "ffmpeg"


def is_plausible_zip(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < 4_000_000:
            return False
        with open(path, "rb") as f:
            sig = f.read(4)
        return sig == b"PK\x03\x04" or (len(sig) >= 2 and sig[:2] == b"PK")
    except OSError:
        return False


def _pick_gyan_bundle_root(extract_dir: Path) -> Path:
    dirs = [
        c
        for c in extract_dir.iterdir()
        if c.is_dir() and c.name != "__MACOSX" and not c.name.startswith(".")
    ]
    for d in dirs:
        if (d / "bin" / "ffmpeg.exe").is_file():
            return d
    if len(dirs) == 1:
        return dirs[0]
    raise RuntimeError("Could not find FFmpeg folder in zip (expected a folder containing bin/ffmpeg.exe).")


def find_ffmpeg_exe_under(root: Path) -> Path | None:
    best_non_bin: Path | None = None
    try:
        for p in root.rglob("ffmpeg.exe"):
            if not p.is_file():
                continue
            if p.parent.name.lower() == "bin":
                return p
            if best_non_bin is None:
                best_non_bin = p
    except OSError:
        return None
    return best_non_bin


def extract_gyan_ffmpeg_essentials_zip(zip_path: Path) -> Path:
    """
    Extract Gyan ``ffmpeg-release-essentials.zip`` into ``ffmpeg_bundle_install_root()``.
    Returns path to ``ffmpeg.exe``. Call ``write_ffmpeg_executable_path`` on the **GUI thread** only.
    """
    if not is_plausible_zip(zip_path):
        raise RuntimeError("Downloaded file is not a valid zip archive.")
    root = ffmpeg_bundle_install_root()
    root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=str(root)) as td_name:
        td = Path(td_name)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(td)
        bundle = _pick_gyan_bundle_root(td)
        target = root / bundle.name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(bundle), str(target))

    ffmpeg_exe = find_ffmpeg_exe_under(root)
    if ffmpeg_exe is None:
        raise RuntimeError("ffmpeg.exe not found after extracting the archive.")
    return ffmpeg_exe
