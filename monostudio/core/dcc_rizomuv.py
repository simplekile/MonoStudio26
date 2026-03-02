"""
RizomUV DCC adapter for MonoStudio (Windows only).
RizomUV không có project file, mở trực tiếp file 3D (.fbx, .obj). Luôn dùng exe từ Settings
để mở đúng RizomUV (không dùng os.startfile vì .fbx có thể gắn app khác).
- open_file: Popen(exe, path), cwd = thư mục file
- create_new_file: Popen(exe), cwd = thư mục đích
"""
from __future__ import annotations

import os
import shutil
import subprocess
from glob import glob
from pathlib import Path
from typing import Any


def _norm_exe(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    return s


def _is_probably_path(s: str) -> bool:
    if not s:
        return False
    if "/" in s or "\\" in s:
        return True
    if len(s) >= 2 and s[1] == ":":
        return True
    return False


def _windows_common_rizomuv_paths() -> list[str]:
    base = r"C:\Program Files\Rizom Lab"
    hits: list[str] = []
    try:
        patterns = [
            os.path.join(base, "**", "rizomuv_vs.exe"),
            os.path.join(base, "**", "RizomUV_VS.exe"),
            os.path.join(base, "**", "rizomuv.exe"),
            os.path.join(base, "**", "RizomUV.exe"),
        ]
        for pat in patterns:
            hits.extend(glob(pat, recursive=True))
    except OSError:
        pass
    hits = sorted({_norm_exe(h) for h in hits if _norm_exe(h)}, reverse=True)
    return [h for h in hits if Path(h).is_file()]


def resolve_rizomuv_executable(configured: str) -> str | None:
    """
    Resolve RizomUV executable.

    Order: env MONOSTUDIO_RIZOMUV_EXE → configured path → PATH → common install paths.
    """
    configured = _norm_exe(configured)
    env = _norm_exe(os.environ.get("MONOSTUDIO_RIZOMUV_EXE", ""))

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

    for name in [configured, "rizomuv_vs", "RizomUV_VS.exe", "rizomuv", "RizomUV.exe"]:
        name = _norm_exe(name)
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found

    for p in _windows_common_rizomuv_paths():
        return p

    return None


def _rizomuv_missing_message(configured: str) -> str:
    configured = _norm_exe(configured) or "rizomuv"
    msg_lines = [
        "Failed to launch RizomUV.",
        "",
        f"Configured executable: {configured!r}",
        "",
        "Fix options:",
        "- Set Settings key 'integrations/rizomuv_exe' to the full path of the executable, OR",
        "- Set env var MONOSTUDIO_RIZOMUV_EXE.",
    ]
    examples = _windows_common_rizomuv_paths()
    if examples:
        msg_lines.extend(["", "Detected install (example):", f"- {examples[0]}"])
    else:
        msg_lines.extend(["", "Common install:", r"C:\Program Files\Rizom Lab\RizomUV 2025.0\rizomuv_vs.exe"])
    return "\n".join(msg_lines).strip()


class RizomUVDccAdapter:
    """
    Desktop-side RizomUV launcher for MonoStudio.

    RizomUV works with FBX/OBJ files directly (no proprietary project format).

    - open_file: launches RizomUV with the .fbx file path.
    - create_new_file: creates work dir + placeholder .fbx (or copies template),
      then launches RizomUV. User exports mesh from modelling DCC, opens in
      RizomUV, and saves to the pipeline-named file.
    """

    def __init__(self, *, rizomuv_executable: str, repo_root: Path) -> None:
        self._exe = (rizomuv_executable or "").strip()
        self._repo_root = Path(repo_root)

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        exe = resolve_rizomuv_executable(self._exe)
        if not exe:
            raise RuntimeError(_rizomuv_missing_message(self._exe))
        path = Path(filepath)
        if not path.is_absolute():
            path = path.resolve()
        if not path.is_file():
            raise RuntimeError(f"RizomUV open_file: file does not exist: {path!r}")
        file_dir = str(path.parent)
        path_arg = str(path)
        try:
            subprocess.Popen([exe, path_arg], cwd=file_dir, close_fds=True)
        except Exception as e:
            raise RuntimeError(f"Failed to launch RizomUV: {path_arg!r}") from e

    def create_new_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        """
        Fallback create: ensures work dir exists then launches RizomUV.
        Normally the Import Source flow (import_source_dialog) handles copying
        the real source file before calling open_file instead.
        """
        exe = resolve_rizomuv_executable(self._exe)
        if not exe:
            raise RuntimeError(_rizomuv_missing_message(self._exe))
        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(
                [exe],
                cwd=str(dest.parent),
                close_fds=True,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to launch RizomUV: {e!r}") from e
