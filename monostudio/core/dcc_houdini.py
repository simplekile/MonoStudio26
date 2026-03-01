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
        try:
            subprocess.Popen(
                [exe, filepath_norm],
                cwd=str(self._repo_root),
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
            env = os.environ.copy()
            env["MONOSTUDIO_HOUDINI_SAVE_PATH"] = filepath_norm
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
                        cwd=str(self._repo_root),
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

        try:
            if Path(filepath).is_file():
                subprocess.Popen(
                    [exe, filepath_norm],
                    cwd=str(self._repo_root),
                    close_fds=True,
                )
            else:
                subprocess.Popen(
                    [exe],
                    cwd=str(Path(filepath).parent),
                    close_fds=True,
                )
        except Exception as e:
            raise RuntimeError(f"Failed to launch Houdini: {e!r}") from e
