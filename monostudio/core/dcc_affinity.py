"""
Affinity by Canva (Affinity v3) DCC adapter for MonoStudio (Windows only).

The unified Affinity app replaces separate Photo/Designer/Publisher apps.
Typical MSIX install:
  C:\\Program Files\\WindowsApps\\Canva.Affinity_<ver>_x64__...\\App\\Affinity.exe

- open_file: os.startfile(path) (Windows file association for .af / legacy .afphoto)
- create_new_file: copy template then open_file, or launch Affinity.exe with cwd set to work folder
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


def _resolve_via_appx_package() -> str | None:
    """Resolve Affinity by Canva MSIX install via Get-AppxPackage."""
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-AppxPackage -Name 'Canva.Affinity' | Select-Object -First 1).InstallLocation",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        loc = (result.stdout or "").strip()
        if not loc:
            return None
        exe = Path(loc) / "App" / "Affinity.exe"
        if exe.is_file():
            return str(exe)
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _windows_common_affinity_paths() -> list[str]:
    patterns = [
        # Affinity by Canva (MSIX) — glob may fail on WindowsApps ACL; Appx probe is primary.
        r"C:\Program Files\WindowsApps\Canva.Affinity_*\App\Affinity.exe",
        # Optional EXE installer layouts (v3 unified, or legacy v2 apps).
        r"C:\Program Files\Affinity\Affinity\Affinity.exe",
        r"C:\Program Files\Affinity\Photo 2\Photo.exe",
        r"C:\Program Files\Serif\Affinity Photo 2\Photo.exe",
        r"C:\Program Files\Affinity\Photo\Photo.exe",
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


def resolve_affinity_executable(configured: str) -> str | None:
    """
    Resolve Affinity by Canva executable.

    Order:
      env MONOSTUDIO_AFFINITY_EXE (or legacy MONOSTUDIO_AFFINITY_PHOTO_EXE)
      → configured path
      → Get-AppxPackage Canva.Affinity
      → common install paths / PATH
    """
    configured = _norm_exe(configured)
    env = _norm_exe(os.environ.get("MONOSTUDIO_AFFINITY_EXE", ""))
    if not env:
        env = _norm_exe(os.environ.get("MONOSTUDIO_AFFINITY_PHOTO_EXE", ""))

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

    appx = _resolve_via_appx_package()
    if appx:
        return appx

    for name in [configured, "Affinity.exe", "Photo.exe"]:
        name = _norm_exe(name)
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found

    for p in _windows_common_affinity_paths():
        return p

    return None


def _affinity_missing_message(configured: str) -> str:
    configured = _norm_exe(configured) or "Affinity.exe"
    msg_lines = [
        "Failed to launch Affinity.",
        "",
        f"Configured executable: {configured!r}",
        "",
        "Fix options:",
        "- Set Settings key 'integrations/affinity_exe' to the full path of Affinity.exe, OR",
        "- Set env var MONOSTUDIO_AFFINITY_EXE.",
        "",
        "Affinity by Canva (MSIX) is usually auto-detected via Get-AppxPackage.",
    ]
    examples: list[str] = []
    appx = _resolve_via_appx_package()
    if appx:
        examples.append(appx)
    examples.extend(_windows_common_affinity_paths())
    if examples:
        msg_lines.extend(["", "Detected install (example):", f"- {examples[0]}"])
    else:
        msg_lines.extend(
            [
                "",
                "Common install (Affinity by Canva):",
                r"C:\Program Files\WindowsApps\Canva.Affinity_<version>_x64__...\App\Affinity.exe",
            ]
        )
    return "\n".join(msg_lines).strip()


AFFINITY_BLANK_TEMPLATE = "affinity_blank.af"


def _blank_template_path(repo_root: Path) -> Path:
    return Path(repo_root) / "monostudio_data" / "pipeline" / "templates" / AFFINITY_BLANK_TEMPLATE


class AffinityDccAdapter:
    def __init__(self, *, affinity_executable: str, repo_root: Path) -> None:
        self._exe = (affinity_executable or "").strip()
        self._repo_root = Path(repo_root)

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        path = Path(filepath)
        if not path.is_absolute():
            path = path.resolve()
        if not path.is_file():
            raise RuntimeError(f"Affinity open_file: file not found: {path!r}")
        try:
            os.startfile(str(path))
        except OSError as e:
            raise RuntimeError(f"Failed to open file with Affinity: {path!r}") from e

    def create_new_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        exe = resolve_affinity_executable(self._exe)
        if not exe:
            raise RuntimeError(_affinity_missing_message(self._exe))

        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)

        template = _blank_template_path(self._repo_root)
        if template.is_file():
            shutil.copy2(template, dest)
            self.open_file(filepath=filepath, context=context)
            return

        work_dir = str(dest.parent)
        try:
            subprocess.Popen([exe], cwd=work_dir, close_fds=True)
        except Exception as e:
            raise RuntimeError(f"Failed to launch Affinity: {e!r}") from e
