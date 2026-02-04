from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from glob import glob
from pathlib import Path
from typing import Any


def _norm_exe(s: str) -> str:
    s = (s or "").strip()
    # Common when users copy/paste from shell snippets.
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
    # Windows drive path like C:\...
    if len(s) >= 2 and s[1] == ":":
        return True
    return False


def _windows_registry_blender_exe() -> str | None:
    if not _is_windows():
        return None
    try:
        import winreg  # type: ignore
    except Exception:
        return None

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\blender.exe"),
    ]
    for root, subkey in keys:
        try:
            with winreg.OpenKey(root, subkey) as k:
                try:
                    exe, _t = winreg.QueryValueEx(k, "")
                except FileNotFoundError:
                    exe = ""
                exe = _norm_exe(str(exe))
                if exe and Path(exe).is_file():
                    return exe
                try:
                    folder, _t = winreg.QueryValueEx(k, "Path")
                except FileNotFoundError:
                    folder = ""
                folder = _norm_exe(str(folder))
                if folder:
                    candidate = Path(folder) / "blender.exe"
                    if candidate.is_file():
                        return str(candidate)
        except OSError:
            continue
    return None


def _windows_common_blender_paths() -> list[str]:
    if not _is_windows():
        return []

    patterns: list[str] = [
        r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
        r"C:\Program Files (x86)\Blender Foundation\Blender*\blender.exe",
    ]
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        patterns.append(str(Path(local) / r"Programs\Blender Foundation\Blender*\blender.exe"))

    hits: list[str] = []
    for pat in patterns:
        try:
            hits.extend(glob(pat))
        except OSError:
            continue
    # Prefer latest-ish by path sort (version is embedded in folder name).
    hits = sorted({_norm_exe(h) for h in hits if _norm_exe(h)}, reverse=True)
    return [h for h in hits if Path(h).is_file()]


def resolve_blender_executable(configured: str) -> str | None:
    """
    Resolve a usable Blender executable.

    Order:
    - Env var override: MONOSTUDIO_BLENDER_EXE
    - Explicit configured path or name
    - PATH (`shutil.which`)
    - Windows registry App Paths
    - Windows common install locations
    """
    configured = _norm_exe(configured)
    env = _norm_exe(os.environ.get("MONOSTUDIO_BLENDER_EXE", ""))

    # 1) Explicit env override (most deterministic).
    if env:
        p = Path(env)
        if p.is_file():
            return str(p)
        # Also allow a bare command name via PATH.
        found = shutil.which(env)
        if found:
            return found

    # 2) If user gave a path, validate it directly.
    if configured and _is_probably_path(configured):
        p = Path(configured)
        if p.is_file():
            return str(p)

    # 3) PATH resolution.
    for name in [configured, "blender", "blender.exe"]:
        name = _norm_exe(name)
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found

    # 4) Windows registry.
    reg = _windows_registry_blender_exe()
    if reg:
        return reg

    # 5) Common Windows install folders.
    for p in _windows_common_blender_paths():
        return p

    return None


def _blender_missing_message(configured: str) -> str:
    configured = _norm_exe(configured) or "blender"
    msg_lines = [
        "Failed to launch Blender.",
        "",
        f"Configured executable: {configured!r}",
        "",
        "Fix options:",
        "- Add Blender to your PATH, OR",
        "- Set Settings key 'integrations/blender_exe' to the full path of 'blender.exe', OR",
        "- Set env var MONOSTUDIO_BLENDER_EXE to the full path of 'blender.exe'.",
    ]
    if _is_windows():
        examples = _windows_common_blender_paths()
        if examples:
            msg_lines.extend(["", "Detected Blender installs (example):", f"- {examples[0]}"])
        else:
            msg_lines.extend(
                [
                    "",
                    "Common install location:",
                    r"- C:\Program Files\Blender Foundation\Blender <version>\blender.exe",
                ]
            )
    return "\n".join(msg_lines).strip()


class BlenderDccAdapter:
    """
    Desktop-side Blender launcher for MONOS.

    This runs Blender as an external process and executes the Blender-side MONOS adapter
    (`monos_blender.adapter`) via `--python-expr`.
    """

    def __init__(self, *, blender_executable: str, repo_root: Path) -> None:
        self._blender_executable = (blender_executable or "").strip()
        self._repo_root = repo_root

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        self._launch_with_expr(
            self._expr_call("open_file", filepath=filepath, context=context),
        )

    def create_new_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        self._launch_with_expr(
            self._expr_call("create_new_file", filepath=filepath, context=context),
        )

    def _launch_with_expr(self, expr: str) -> None:
        exe = resolve_blender_executable(self._blender_executable)
        if not exe:
            raise RuntimeError(_blender_missing_message(self._blender_executable))

        try:
            subprocess.Popen(
                [exe, "--python-expr", expr],
                cwd=str(self._repo_root),
                close_fds=True,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to launch Blender using executable: {exe!r}") from e

    def _expr_call(self, fn_name: str, *, filepath: str, context: dict[str, Any]) -> str:
        if not isinstance(filepath, str) or not filepath.strip():
            raise RuntimeError("Internal error: filepath must be a non-empty string.")
        if not isinstance(context, dict):
            raise RuntimeError("Internal error: context must be a dict.")

        payload = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ctx_b64 = base64.b64encode(payload).decode("ascii")
        repo = str(self._repo_root)

        # NOTE: This code executes inside Blender's embedded Python.
        return (
            "import sys, json, base64\n"
            f"sys.path.insert(0, {repo!r})\n"
            "from monos_blender import adapter as _a\n"
            f"_ctx = json.loads(base64.b64decode({ctx_b64!r}).decode('utf-8'))\n"
            f"_a.{fn_name}(filepath={filepath!r}, context=_ctx)\n"
        )

