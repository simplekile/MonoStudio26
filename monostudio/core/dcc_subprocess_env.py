"""
Sanitized subprocess environment for launching external DCCs from MonoStudio.

When MonoStudio runs as a PyInstaller bundle it ships Python 3.13 DLLs on PATH.
External DCCs (Houdini, Blender, …) bundle their own Python; inheriting MonoStudio's
env causes crashes like "Module use of python313.dll conflicts with this version of Python."
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def env_for_dcc_subprocess() -> dict[str, str]:
    env = os.environ.copy()

    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
        env.pop(key, None)

    path_sep = os.pathsep
    path_raw = env.get("PATH", "")
    if not path_raw:
        return env

    to_remove: set[str] = set()
    exe_dir = ""
    try:
        exe_dir = str(Path(sys.executable).resolve().parent).lower()
        to_remove.add(exe_dir)
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        try:
            internal = str(Path(sys.executable).resolve().parent / "_internal").lower()
            to_remove.add(internal)
        except Exception:
            pass

    filtered: list[str] = []
    for p in (part.strip() for part in path_raw.split(path_sep) if part.strip()):
        p_lower = p.lower()
        if p_lower in to_remove:
            continue
        if exe_dir and "_internal" in p_lower and exe_dir in p_lower:
            continue
        if "python313" in p_lower:
            continue
        if exe_dir and exe_dir in p_lower:
            continue
        filtered.append(p)

    env["PATH"] = path_sep.join(filtered)
    return env
