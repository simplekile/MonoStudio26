"""
Houdini DCC adapter for MonoStudio: resolve executable and launch Houdini to open/create work files.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path
from typing import Any


def _norm_exe(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    return s


def _is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def _is_probably_path(s: str) -> bool:
    if not s:
        return False
    if "/" in s or "\\" in s:
        return True
    if len(s) >= 2 and s[1] == ":":
        return True
    return False


def _houdini_from_hfs() -> str | None:
    """Resolve houdini executable from HFS environment variable (Side Effects standard)."""
    hfs = _norm_exe(os.environ.get("HFS", ""))
    if not hfs:
        return None
    p = Path(hfs) / "bin" / ("houdini.exe" if _is_windows() else "houdini")
    if p.is_file():
        return str(p)
    return None


def _windows_common_houdini_paths() -> list[str]:
    if not _is_windows():
        return []
    patterns = [
        r"C:\Program Files\Side Effects Software\Houdini*\bin\houdini.exe",
        r"C:\Program Files (x86)\Side Effects Software\Houdini*\bin\houdini.exe",
    ]
    hits: list[str] = []
    for pat in patterns:
        try:
            hits.extend(glob(pat))
        except OSError:
            continue
    hits = sorted({_norm_exe(h) for h in hits if _norm_exe(h)}, reverse=True)
    return [h for h in hits if Path(h).is_file()]


def _hython_executable(houdini_exe: str) -> str | None:
    """Resolve hython (Houdini Python) from same bin dir as houdini."""
    p = Path(houdini_exe)
    if not p.is_file():
        return None
    name = "hython.exe" if _is_windows() else "hython"
    hython = p.parent / name
    if hython.is_file():
        return str(hython)
    return None


def resolve_houdini_executable(configured: str) -> str | None:
    """
    Resolve a usable Houdini executable.

    Order: env MONOSTUDIO_HOUDINI_EXE → configured path → PATH → HFS/bin/houdini → common install paths.
    """
    configured = _norm_exe(configured)
    env = _norm_exe(os.environ.get("MONOSTUDIO_HOUDINI_EXE", ""))

    if env:
        p = Path(env)
        if p.is_file():
            return str(p)
        found = shutil.which(env)
        if found:
            return found

    if configured and _is_probably_path(configured):
        p = Path(configured)
        if p.is_file():
            return str(p)

    for name in [configured, "houdini", "houdini.exe"]:
        name = _norm_exe(name)
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found

    hfs_exe = _houdini_from_hfs()
    if hfs_exe:
        return hfs_exe

    for p in _windows_common_houdini_paths():
        return p

    return None


def _env_for_houdini_subprocess() -> dict[str, str]:
    """
    Build an environment for Houdini/hython subprocess so it does not load
    MonoStudio's Python (e.g. python313.dll from PyInstaller bundle).
    Houdini ships with its own Python (e.g. 3.11); loading another version's DLL causes:
    "Module use of python313.dll conflicts with this version of Python."
    """
    env = os.environ.copy()

    # Unset Python env vars so hython uses only Houdini's bundled Python (PyInstaller/parent may set these)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
        env.pop(key, None)

    path_sep = os.pathsep
    path_raw = env.get("PATH", "")
    if not path_raw:
        return env
    to_remove: set[str] = set()
    exe_dir = ""
    # Path of this process (MonoStudio exe or interpreter)
    try:
        exe_dir = str(Path(sys.executable).resolve().parent).lower()
        to_remove.add(exe_dir)
    except Exception:
        pass
    # PyInstaller: _internal next to the exe
    if getattr(sys, "frozen", False):
        try:
            internal = str(Path(sys.executable).resolve().parent / "_internal").lower()
            to_remove.add(internal)
        except Exception:
            pass
    # Paths containing Python313 / python313 (avoid loading system or bundled 3.13 when Houdini uses 3.11)
    parts = [p.strip() for p in path_raw.split(path_sep) if p.strip()]
    filtered = []
    for p in parts:
        p_lower = p.lower()
        if p_lower in to_remove:
            continue
        if exe_dir and "_internal" in p_lower and exe_dir in p_lower:
            continue
        if "python313" in p_lower:
            continue
        # When frozen: drop any PATH entry that lives under the app install dir (DLL search can still pick it up)
        if exe_dir and exe_dir in p_lower:
            continue
        filtered.append(p)
    env["PATH"] = path_sep.join(filtered)
    return env


def _houdini_missing_message(configured: str) -> str:
    configured = _norm_exe(configured) or "houdini"
    msg_lines = [
        "Failed to launch Houdini.",
        "",
        f"Configured executable: {configured!r}",
        "",
        "Fix options:",
        "- Set HFS to Houdini install root, or add bin to PATH, OR",
        "- Set Settings key 'integrations/houdini_exe' to the full path of 'houdini.exe', OR",
        "- Set env var MONOSTUDIO_HOUDINI_EXE to the full path of 'houdini.exe'.",
    ]
    if _is_windows():
        examples = _windows_common_houdini_paths()
        if examples:
            msg_lines.extend(["", "Detected Houdini installs (example):", f"- {examples[0]}"])
        else:
            msg_lines.extend(
                [
                    "",
                    "Common install location:",
                    r"- C:\Program Files\Side Effects Software\Houdini X.X\bin\houdini.exe",
                ]
            )
    return "\n".join(msg_lines).strip()


class HoudiniDccAdapter:
    """
    Desktop-side Houdini launcher for MonoStudio.

    - open_file: launches Houdini with the file path as argument.
    - create_new_file: uses hython to create an empty scene file (clear + save); extension is from registry (default .hiplc for Indie), then launches Houdini with that file.
    """

    def __init__(self, *, houdini_executable: str, repo_root: Path) -> None:
        self._houdini_executable = (houdini_executable or "").strip()
        self._repo_root = Path(repo_root)

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        exe = resolve_houdini_executable(self._houdini_executable)
        if not exe:
            raise RuntimeError(_houdini_missing_message(self._houdini_executable))
        path = Path(filepath)
        if not path.is_absolute():
            filepath = str(path.resolve())
        filepath_norm = filepath.replace("\\", "/")
        env = _env_for_houdini_subprocess()
        try:
            subprocess.Popen(
                [exe, filepath_norm],
                cwd=str(self._repo_root),
                env=env,
                close_fds=True,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to launch Houdini with file: {filepath_norm!r}") from e

    def create_new_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        exe = resolve_houdini_executable(self._houdini_executable)
        if not exe:
            raise RuntimeError(_houdini_missing_message(self._houdini_executable))
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        filepath_norm = filepath.replace("\\", "/")

        # Use hython to create an empty scene file (.hiplc/.hip/.hipnc per path): hou.hipFile.clear(); hou.hipFile.save(path)
        hython_exe = _hython_executable(exe)
        if hython_exe:
            env = _env_for_houdini_subprocess()
            env["MONOSTUDIO_HOUDINI_SAVE_PATH"] = filepath_norm
            # Run hython with cwd = Houdini bin so DLL search uses Houdini's Python, not MonoStudio's
            hython_cwd = str(Path(hython_exe).resolve().parent)
            script_body = (
                "import os\n"
                "import hou\n"
                "hou.hipFile.clear()\n"
                "hou.hipFile.save(os.environ.get('MONOSTUDIO_HOUDINI_SAVE_PATH', ''))\n"
            )
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".py",
                    delete=False,
                    encoding="utf-8",
                ) as f:
                    f.write(script_body)
                    tmp_script = f.name
                try:
                    subprocess.run(
                        [hython_exe, tmp_script],
                        cwd=hython_cwd,
                        timeout=60,
                        check=False,
                        capture_output=True,
                        env=env,
                    )
                finally:
                    try:
                        os.unlink(tmp_script)
                    except OSError:
                        pass
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

        env = _env_for_houdini_subprocess()
        try:
            if Path(filepath).is_file():
                subprocess.Popen(
                    [exe, filepath_norm],
                    cwd=str(self._repo_root),
                    env=env,
                    close_fds=True,
                )
            else:
                subprocess.Popen(
                    [exe],
                    cwd=str(Path(filepath).parent),
                    env=env,
                    close_fds=True,
                )
        except Exception as e:
            raise RuntimeError(f"Failed to launch Houdini: {e!r}") from e
