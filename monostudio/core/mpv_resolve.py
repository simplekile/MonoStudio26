"""Resolve libmpv (mpv-2.dll) for embedded video playback."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QSettings

from monostudio.core.app_paths import get_tools_install_root

SETTINGS_ORG = "MonoStudio26"
SETTINGS_APP = "MonoStudio26"
SETTINGS_KEY_MPV_DIR = "tools/mpv_directory"

MpvSourceKind = Literal["bundled", "settings", "localappdata", "path", "none"]


def mpv_bundle_install_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        return Path(localappdata) / "MonoStudio" / "tools" / "mpv"
    import tempfile

    return Path(tempfile.gettempdir()) / "MonoStudio" / "tools" / "mpv"


def bundled_mpv_root() -> Path:
    """``{install_dir}/tools/mpv`` — shipped next to PyInstaller onedir ``_internal``."""
    return get_tools_install_root() / "tools" / "mpv"


def read_mpv_directory(settings: QSettings | None) -> str:
    if settings is None:
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    v = settings.value(SETTINGS_KEY_MPV_DIR, "")
    return (v or "").strip() if isinstance(v, str) else str(v or "").strip()


def write_mpv_directory(settings: QSettings, directory: str) -> None:
    d = (directory or "").strip()
    if d:
        settings.setValue(SETTINGS_KEY_MPV_DIR, d)
    else:
        settings.remove(SETTINGS_KEY_MPV_DIR)


_MPV_DLL_SEARCH: tuple[tuple[str, str], ...] = (
    ("mpv-2.dll", "mpv-2.dll"),
    ("libmpv-2.dll", "mpv-2.dll"),
    ("mpv-1.dll", "mpv-1.dll"),
    ("libmpv-1.dll", "mpv-1.dll"),
)


def find_mpv_dll_under(root: Path) -> Path | None:
    """Locate libmpv DLL under *root* (accepts ``libmpv-2.dll`` or ``mpv-2.dll``)."""
    try:
        for search_name, _install_name in _MPV_DLL_SEARCH:
            for p in root.rglob(search_name):
                if p.is_file():
                    return p
    except OSError:
        return None
    return None


def mpv_dll_install_name(path: Path) -> str:
    """Target filename when copying into MonoStudio tools folder."""
    lower = path.name.lower()
    if lower in ("libmpv-2.dll", "mpv-2.dll"):
        return "mpv-2.dll"
    if lower in ("libmpv-1.dll", "mpv-1.dll"):
        return "mpv-1.dll"
    return path.name


def resolve_mpv_dll(settings: QSettings | None = None) -> Path | None:
    """Return path to mpv-2.dll if found."""
    raw = read_mpv_directory(settings)
    if raw:
        try:
            p = Path(raw)
            if p.is_file() and p.suffix.lower() == ".dll":
                return p.resolve()
            if p.is_dir():
                found = find_mpv_dll_under(p)
                if found is not None:
                    return found.resolve()
        except OSError:
            pass

    bundled = bundled_mpv_root()
    if bundled.is_dir():
        found = find_mpv_dll_under(bundled)
        if found is not None:
            return found.resolve()

    bundle = mpv_bundle_install_root()
    if bundle.is_dir():
        found = find_mpv_dll_under(bundle)
        if found is not None:
            return found.resolve()

    for name in ("mpv-2.dll", "mpv-1.dll"):
        which = shutil.which(name)
        if which:
            try:
                wp = Path(which)
                if wp.is_file():
                    return wp.resolve()
            except OSError:
                pass
    return None


def resolve_mpv_source(settings: QSettings | None = None) -> tuple[Path | None, MpvSourceKind]:
    """Return (dll path, source kind) for Settings status text."""
    raw = read_mpv_directory(settings)
    if raw:
        try:
            p = Path(raw)
            if p.is_file() and p.suffix.lower() == ".dll":
                return p.resolve(), "settings"
            if p.is_dir():
                found = find_mpv_dll_under(p)
                if found is not None:
                    return found.resolve(), "settings"
        except OSError:
            pass

    bundled = bundled_mpv_root()
    if bundled.is_dir():
        found = find_mpv_dll_under(bundled)
        if found is not None:
            return found.resolve(), "bundled"

    bundle = mpv_bundle_install_root()
    if bundle.is_dir():
        found = find_mpv_dll_under(bundle)
        if found is not None:
            return found.resolve(), "localappdata"

    for name in ("mpv-2.dll", "mpv-1.dll"):
        which = shutil.which(name)
        if which:
            try:
                wp = Path(which)
                if wp.is_file():
                    return wp.resolve(), "path"
            except OSError:
                pass
    return None, "none"


def format_mpv_detect_status(settings: QSettings | None = None) -> str:
    dll, kind = resolve_mpv_source(settings)
    if dll is None:
        return "not found — use Settings -> Updates -> libmpv (Get -> Install) or bundle tools/mpv at build"
    labels = {
        "bundled": "bundled",
        "settings": "manual",
        "localappdata": "installed",
        "path": "PATH",
    }
    return f"{labels.get(kind, kind)}: {dll}"


def ensure_mpv_dll_path(settings: QSettings | None = None) -> Path | None:
    """Locate mpv DLL and prepend its directory to PATH for python-mpv."""
    dll = resolve_mpv_dll(settings)
    if dll is None:
        return None
    dll_dir = str(dll.parent)
    path_env = os.environ.get("PATH", "")
    if dll_dir not in path_env.split(os.pathsep):
        os.environ["PATH"] = dll_dir + os.pathsep + path_env
    prepare_mpv_python_bindings()
    return dll


def prepare_mpv_python_bindings() -> None:
    """Qt overrides LC_NUMERIC; libmpv requires C locale before the first ``import mpv``."""
    import locale

    try:
        locale.setlocale(locale.LC_NUMERIC, "C")
    except locale.Error:
        pass


def mpv_available(settings: QSettings | None = None) -> bool:
    dll = ensure_mpv_dll_path(settings)
    if dll is None:
        return False
    try:
        import mpv  # noqa: F401

        return True
    except Exception:
        return False
