"""
Substance Painter (Adobe Substance 3D Painter) DCC adapter for MonoStudio.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
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


def _windows_common_substance_painter_paths() -> list[str]:
    if not _is_windows():
        return []
    patterns = [
        r"C:\Program Files\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe",
        r"C:\Program Files\Adobe\Adobe Substance Painter\Adobe Substance Painter.exe",
        r"C:\Program Files (x86)\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe",
    ]
    hits: list[str] = []
    for pat in patterns:
        try:
            found = list(glob(pat))
            hits.extend(found)
        except OSError:
            continue
    hits = sorted({_norm_exe(h) for h in hits if _norm_exe(h)}, reverse=True)
    return [h for h in hits if Path(h).is_file()]


def resolve_substance_painter_executable(configured: str) -> str | None:
    """
    Resolve Substance Painter (Adobe Substance 3D Painter) executable.

    Order: env MONOSTUDIO_SUBSTANCE_PAINTER_EXE → configured path → PATH → common install paths.
    """
    configured = _norm_exe(configured)
    env = _norm_exe(os.environ.get("MONOSTUDIO_SUBSTANCE_PAINTER_EXE", ""))

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

    for name in [configured, "substancepainter", "Adobe Substance 3D Painter.exe", "Substance Painter.exe"]:
        name = _norm_exe(name)
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found

    for p in _windows_common_substance_painter_paths():
        return p

    return None


def _substance_painter_missing_message(configured: str) -> str:
    configured = _norm_exe(configured) or "substancepainter"
    msg_lines = [
        "Failed to launch Substance Painter.",
        "",
        f"Configured executable: {configured!r}",
        "",
        "Fix options:",
        "- Set Settings key 'integrations/substance_painter_exe' to the full path of the executable, OR",
        "- Set env var MONOSTUDIO_SUBSTANCE_PAINTER_EXE.",
    ]
    if _is_windows():
        examples = _windows_common_substance_painter_paths()
        if examples:
            msg_lines.extend(["", "Detected install (example):", f"- {examples[0]}"])
        else:
            msg_lines.extend(
                [
                    "",
                    "Common install:",
                    r"C:\Program Files\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe",
                ]
            )
    return "\n".join(msg_lines).strip()


# Template filename under monostudio_data/pipeline/templates/ for copy-on-create.
SUBSTANCE_PAINTER_BLANK_TEMPLATE = "substance_painter_blank.spp"


def _blank_spp_template_path(repo_root: Path) -> Path:
    """Path to optional blank .spp template (copy + rename for new work file)."""
    return Path(repo_root) / "monostudio_data" / "pipeline" / "templates" / SUBSTANCE_PAINTER_BLANK_TEMPLATE


class SubstancePainterDccAdapter:
    """
    Desktop-side Substance Painter launcher for MonoStudio.

    - open_file: launches Substance Painter with the .spp file path.
    - create_new_file: if a blank template exists, copies it to the target path and opens it;
      otherwise launches Substance Painter with cwd set to work folder (user saves manually).
    """

    def __init__(self, *, substance_painter_executable: str, repo_root: Path) -> None:
        self._exe = (substance_painter_executable or "").strip()
        self._repo_root = Path(repo_root)

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        exe = resolve_substance_painter_executable(self._exe)
        if not exe:
            raise RuntimeError(_substance_painter_missing_message(self._exe))
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
            raise RuntimeError(f"Failed to launch Substance Painter: {filepath_norm!r}") from e

    def create_new_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        exe = resolve_substance_painter_executable(self._exe)
        if not exe:
            raise RuntimeError(_substance_painter_missing_message(self._exe))
        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        template = _blank_spp_template_path(self._repo_root)
        if template.is_file():
            shutil.copy2(template, dest)
            self.open_file(filepath=filepath, context=context)
            return
        work_dir = str(dest.parent)
        try:
            subprocess.Popen(
                [exe],
                cwd=work_dir,
                close_fds=True,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to launch Substance Painter: {e!r}") from e
