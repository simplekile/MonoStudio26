"""
Houdini DCC adapter for MonoStudio (Windows only).
- open_file: subprocess houdini.exe with sanitized env (avoids MonoStudio Python DLL conflicts).
- create_new_file: hython tạo file trống (env làm sạch), rồi launch GUI cùng env.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from glob import glob
from pathlib import Path
from typing import Any

from monostudio.core.dcc_subprocess_env import env_for_dcc_subprocess


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


def _houdini_from_hfs() -> str | None:
    """Resolve houdini executable from HFS environment variable (Side Effects standard)."""
    hfs = _norm_exe(os.environ.get("HFS", ""))
    if not hfs:
        return None
    p = Path(hfs) / "bin" / "houdini.exe"
    if p.is_file():
        return str(p)
    return None


def _windows_common_houdini_paths() -> list[str]:
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
    name = "hython.exe"
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
    """Backward-compatible alias; see ``env_for_dcc_subprocess``."""
    return env_for_dcc_subprocess()


def _launch_houdini_gui(exe: str, filepath: str | None = None) -> None:
    """Launch Houdini GUI with a sanitized env (avoids MonoStudio Python DLL conflicts)."""
    env = _env_for_houdini_subprocess()
    houdini_bin = str(Path(exe).resolve().parent)
    args = [exe]
    if filepath:
        args.append(filepath)
    subprocess.Popen(args, cwd=houdini_bin, env=env, close_fds=True)


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
    examples = _windows_common_houdini_paths()
    if examples:
        msg_lines.extend(["", "Detected Houdini installs (example):", f"- {examples[0]}"])
    else:
        msg_lines.extend(
            ["", "Common install location:", r"- C:\Program Files\Side Effects Software\Houdini X.X\bin\houdini.exe"]
        )
    return "\n".join(msg_lines).strip()


class HoudiniDccAdapter:
    """Houdini launcher (Windows): subprocess with sanitized env; create uses hython then GUI."""

    def __init__(self, *, houdini_executable: str, repo_root: Path) -> None:
        self._houdini_executable = (houdini_executable or "").strip()
        self._repo_root = Path(repo_root)

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        _ = context
        path = Path(filepath)
        if not path.is_absolute():
            path = path.resolve()
        if not path.is_file():
            raise RuntimeError(f"Houdini open_file: file not found: {path!r}")
        exe = resolve_houdini_executable(self._houdini_executable)
        if not exe:
            raise RuntimeError(_houdini_missing_message(self._houdini_executable))
        try:
            _launch_houdini_gui(exe, str(path))
        except Exception as e:
            raise RuntimeError(f"Failed to open file with Houdini: {path!r}") from e

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

        path_abs = Path(filepath).resolve()
        try:
            if path_abs.is_file():
                _launch_houdini_gui(exe, str(path_abs))
            else:
                _launch_houdini_gui(exe)
        except Exception as e:
            raise RuntimeError(f"Failed to launch Houdini: {e!r}") from e
