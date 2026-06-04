"""
Sanitized subprocess environment for launching external DCCs from MonoStudio.

When MonoStudio runs as a PyInstaller bundle it ships Python 3.13 DLLs on PATH.
External DCCs (Houdini, Blender, …) bundle their own Python; inheriting MonoStudio's
env causes crashes like "Module use of python313.dll conflicts with this version of Python."

Houdini also uses Qt — Qt plugin paths from PySide6 must not be inherited.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Variables that must not leak into DCC subprocesses (wrong Python / wrong Qt build).
_DCC_STRIP_ENV_KEYS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "PYSIDE6_OPTION_PYTHON_ENUM",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
)


def _strip_dcc_conflicting_env_vars(env: dict[str, str]) -> None:
    for key in _DCC_STRIP_ENV_KEYS:
        env.pop(key, None)


def _path_is_under_root(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def _should_drop_path_for_dcc(
    path_part: str,
    *,
    to_remove: set[str],
    preserve_roots: tuple[str, ...],
) -> bool:
    p_lower = path_part.lower()
    if p_lower in to_remove:
        return True
    for root in preserve_roots:
        if _path_is_under_root(path_part, root):
            return False
    if "python launcher" in p_lower:
        return True
    if r"\programs\python" in p_lower or "/programs/python" in p_lower:
        return True
    if "site-packages" in p_lower:
        return True
    if "pyside6" in p_lower or "shiboken6" in p_lower:
        return True
    if "python313" in p_lower or "python312" in p_lower:
        return True
    if "_internal" in p_lower and ("monostudio" in p_lower or "mono studio" in p_lower):
        return True
    return False


def _mono_exe_dirs_to_remove() -> set[str]:
    to_remove: set[str] = set()
    exe_dir = ""
    try:
        exe_dir = str(Path(sys.executable).resolve().parent).lower()
        to_remove.add(exe_dir)
    except OSError:
        pass
    if getattr(sys, "frozen", False):
        try:
            internal = str(Path(sys.executable).resolve().parent / "_internal").lower()
            to_remove.add(internal)
        except OSError:
            pass
    return to_remove, exe_dir


def filter_path_for_dcc(
    path_raw: str,
    *,
    preserve_roots: tuple[str, ...] = (),
    prepend: tuple[str, ...] = (),
) -> str:
    """Return PATH safe for external DCC subprocesses."""
    to_remove, exe_dir = _mono_exe_dirs_to_remove()
    filtered: list[str] = []
    seen: set[str] = set()
    for part in prepend:
        part = part.strip()
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        filtered.append(part)
    for p in (part.strip() for part in path_raw.split(os.pathsep) if part.strip()):
        p_lower = p.lower()
        if p_lower in seen:
            continue
        if _should_drop_path_for_dcc(p, to_remove=to_remove, preserve_roots=preserve_roots):
            continue
        if exe_dir and "_internal" in p_lower and exe_dir in p_lower:
            continue
        if exe_dir and exe_dir in p_lower:
            continue
        seen.add(p_lower)
        filtered.append(p)
    return os.pathsep.join(filtered)


def env_for_dcc_subprocess(*, preserve_path_roots: tuple[str, ...] = ()) -> dict[str, str]:
    env = os.environ.copy()
    _strip_dcc_conflicting_env_vars(env)
    path_raw = env.get("PATH", "")
    if path_raw:
        env["PATH"] = filter_path_for_dcc(path_raw, preserve_roots=preserve_path_roots)
    return env
