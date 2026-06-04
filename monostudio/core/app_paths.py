"""
Base path for app resources (monostudio_data, fonts).
Works in development (repo root) and when frozen (PyInstaller onefile/onedir).

Also writes install path to %LOCALAPPDATA%\\MonoStudio\\install_path.txt so other
installers (e.g. MonoFXSuite "Under MonoStudio") can discover where MonoStudio is actually installed.
"""

from __future__ import annotations

import os
import shutil
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


def get_app_user_config_dir() -> Path:
    """Per-user writable config (sessions, window geometry) — survives app reinstall."""
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        return Path(localappdata) / "MonoStudio" / "config"
    return get_app_base_path() / "monostudio_data" / "config"


def get_app_settings_path() -> Path:
    return get_app_user_config_dir() / "app_settings.json"


def _legacy_app_settings_paths() -> list[Path]:
    """Older builds stored settings under the install / _internal tree."""
    paths: list[Path] = [get_app_base_path() / "monostudio_data" / "config" / "app_settings.json"]
    if getattr(sys, "frozen", False):
        base = get_app_base_path()
        if base.name == "_internal":
            paths.append(base.parent / "monostudio_data" / "config" / "app_settings.json")
    return paths


def migrate_app_settings_if_needed() -> None:
    """Copy legacy app_settings.json into %LOCALAPPDATA%\\MonoStudio\\config once."""
    target = get_app_settings_path()
    if target.is_file():
        return
    for legacy in _legacy_app_settings_paths():
        if not legacy.is_file() or legacy.resolve() == target.resolve():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
        except OSError:
            pass
        return


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
