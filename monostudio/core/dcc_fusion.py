"""
Fusion DCC adapter for MonoStudio (Windows only).

Open an existing .comp by launching Fusion executable (or fallback to OS association).
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


def _windows_common_fusion_paths() -> list[str]:
    # Common install locations for standalone Fusion.
    patterns = [
        r"C:\Program Files\Blackmagic Design\Fusion*\Fusion.exe",
        r"C:\Program Files\Blackmagic Design\Fusion*\Fusion Studio.exe",
        r"C:\Program Files\Blackmagic Design\Fusion*\Fusion Studio\Fusion Studio.exe",
        r"C:\Program Files\Blackmagic Design\Fusion\Fusion.exe",
        r"C:\Program Files\Blackmagic Design\Fusion Studio\Fusion Studio.exe",
    ]
    hits: list[str] = []
    for pat in patterns:
        try:
            hits.extend(glob(pat))
        except OSError:
            continue
    hits = sorted({_norm_exe(h) for h in hits if _norm_exe(h)}, reverse=True)
    return [h for h in hits if Path(h).is_file()]


def resolve_fusion_executable(configured: str) -> str | None:
    """
    Resolve a usable Fusion executable.

    Order: env MONOSTUDIO_FUSION_EXE → configured path → PATH → common install paths.
    """
    configured = _norm_exe(configured)
    env = _norm_exe(os.environ.get("MONOSTUDIO_FUSION_EXE", ""))

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

    for name in [configured, "Fusion", "Fusion.exe", "Fusion Studio", "Fusion Studio.exe"]:
        name = _norm_exe(name)
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found

    for p in _windows_common_fusion_paths():
        return p

    return None


class FusionDccAdapter:
    def __init__(self, *, fusion_executable: str, repo_root: Path) -> None:
        self._configured_exe = (fusion_executable or "").strip()
        self._repo_root = Path(repo_root)

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        # Context currently unused for Fusion; kept for parity with other adapters.
        _ = context
        f = Path(filepath)
        if not f.is_file():
            return

        exe = resolve_fusion_executable(self._configured_exe)
        if exe:
            try:
                subprocess.Popen(
                    [exe, str(f)],
                    cwd=str(self._repo_root) if self._repo_root else None,
                    close_fds=True,
                )
                return
            except Exception:
                pass
        try:
            os.startfile(str(f))  # type: ignore[attr-defined]
        except Exception:
            pass

    def create_new_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        """
        Create a new Fusion work file then launch Fusion.

        Fusion .comp is typically a text-based composition. We write a tiny valid-ish stub so:
        - the pipeline "pending create" can resolve once the file exists
        - double-click open behaves consistently across DCCs
        If Fusion rejects the stub for any reason, we still fall back to launching Fusion without args.
        """
        _ = context
        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not dest.exists():
            try:
                # Minimal stub composition (Lua table). Safe single-color / no external deps.
                dest.write_text("Composition { }\n", encoding="utf-8")
            except OSError:
                pass

        # Prefer opening the file (even if stub) so user lands in correct work context.
        try:
            self.open_file(filepath=str(dest), context=context)
            return
        except Exception:
            pass

        exe = resolve_fusion_executable(self._configured_exe)
        if exe:
            try:
                subprocess.Popen(
                    [exe],
                    cwd=str(dest.parent),
                    close_fds=True,
                )
                return
            except Exception:
                pass

