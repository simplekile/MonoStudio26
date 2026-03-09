"""
Base path for app resources (monostudio_data, fonts).
Works in development (repo root) and when frozen (PyInstaller onefile/onedir).

Also writes install path to %LOCALAPPDATA%\\MonoStudio\\install_path.txt so other
installers (e.g. MonoFXSuite "Under MonoStudio") can discover where MonoStudio is actually installed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_app_base_path() -> Path:
    """
    Root directory containing monostudio_data/ and fonts/.
    - Development: repo root (parent of monostudio/).
    - Frozen (PyInstaller): sys._MEIPASS (onedir = _internal folder; onefile = temp extract).
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    # Development: from this file monostudio/core/app_paths.py -> parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def get_tools_install_root() -> Path:
    """
    Root directory for tools/ (e.g. tools/MonoFXSuite) when looking for extra tools' VERSION.
    - Frozen onedir: PyInstaller puts app in _internal/, so tools/ is next to _internal (parent of base).
    - Frozen onefile / dev: same as get_app_base_path().
    """
    base = get_app_base_path()
    if getattr(sys, "frozen", False) and base.name == "_internal":
        return base.parent
    return base


def write_install_path_for_tools() -> None:
    """
    Write MonoStudio install path to %LOCALAPPDATA%\\MonoStudio\\install_path.txt
    so external installers (e.g. MonoFXSuite) can read it and default "Under MonoStudio"
    to the actual install dir (not necessarily Program Files).
    """
    try:
        localappdata = os.environ.get("LOCALAPPDATA", "").strip()
        if not localappdata:
            return
        base = get_app_base_path()
        dir_path = Path(localappdata) / "MonoStudio"
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / "install_path.txt"
        file_path.write_text(str(base.resolve()), encoding="utf-8")
    except OSError:
        pass
