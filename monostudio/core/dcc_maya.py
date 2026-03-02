"""
Maya DCC adapter for MonoStudio (Windows only).
- open_file: os.startfile(path)
- create_new_file: mayabatch tạo file, rồi os.startfile(path) hoặc Popen(maya)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from glob import glob
from pathlib import Path
from typing import Any, Callable


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


def _windows_registry_maya_exe() -> str | None:
    try:
        import winreg  # type: ignore
    except Exception:
        return None

    # Autodesk Maya: HKLM\SOFTWARE\Autodesk\Maya\<version>\Setup\InstallPath or MAYA_INSTALL_PATH
    base = r"SOFTWARE\Autodesk\Maya"
    exe_name = "maya.exe"
    for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root_key, base) as key:
                i = 0
                while True:
                    try:
                        ver = winreg.EnumKey(key, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root_key, f"{base}\\{ver}\\Setup") as setup:
                            try:
                                install_path, _ = winreg.QueryValueEx(setup, "InstallPath")
                            except FileNotFoundError:
                                try:
                                    install_path, _ = winreg.QueryValueEx(setup, "MAYA_INSTALL_PATH")
                                except FileNotFoundError:
                                    continue
                            install_path = _norm_exe(str(install_path))
                            if install_path:
                                candidate = Path(install_path) / "bin" / exe_name
                                if candidate.is_file():
                                    return str(candidate)
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _windows_common_maya_paths() -> list[str]:
    patterns = [
        r"C:\Program Files\Autodesk\Maya*\bin\maya.exe",
        r"C:\Program Files (x86)\Autodesk\Maya*\bin\maya.exe",
    ]
    hits: list[str] = []
    for pat in patterns:
        try:
            hits.extend(glob(pat))
        except OSError:
            continue
    hits = sorted({_norm_exe(h) for h in hits if _norm_exe(h)}, reverse=True)
    return [h for h in hits if Path(h).is_file()]


def resolve_maya_executable(configured: str) -> str | None:
    """
    Resolve a usable Maya executable (maya.exe on Windows).

    Order: env MONOSTUDIO_MAYA_EXE → configured path → PATH → Windows registry → common install paths.
    """
    configured = _norm_exe(configured)
    env = _norm_exe(os.environ.get("MONOSTUDIO_MAYA_EXE", ""))

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

    for name in [configured, "maya", "maya.exe"]:
        name = _norm_exe(name)
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found

    reg = _windows_registry_maya_exe()
    if reg:
        return reg

    for p in _windows_common_maya_paths():
        return p

    return None


def _maya_batch_executable(maya_exe: str) -> str:
    p = Path(maya_exe)
    batch = p.parent / "mayabatch.exe"
    if batch.is_file():
        return str(batch)
    return maya_exe


def _maya_missing_message(configured: str) -> str:
    configured = _norm_exe(configured) or "maya"
    msg_lines = [
        "Failed to launch Maya.",
        "",
        f"Configured executable: {configured!r}",
        "",
        "Fix options:",
        "- Add Maya bin folder to your PATH, OR",
        "- Set Settings key 'integrations/maya_exe' to the full path of 'maya.exe', OR",
        "- Set env var MONOSTUDIO_MAYA_EXE to the full path of 'maya.exe'.",
    ]
    examples = _windows_common_maya_paths()
    if examples:
        msg_lines.extend(["", "Detected Maya installs (example):", f"- {examples[0]}"])
    else:
        msg_lines.extend(["", "Common install location:", r"- C:\Program Files\Autodesk\Maya<version>\bin\maya.exe"])
    return "\n".join(msg_lines).strip()


class MayaDccAdapter:
    """
    Desktop-side Maya launcher for MonoStudio.

    - open_file: launches Maya with -file <path>
    - create_new_file: creates the file via mayabatch (file -new; file -rename; file -save), then opens it with Maya GUI
    """

    def __init__(self, *, maya_executable: str, repo_root: Path) -> None:
        self._maya_executable = (maya_executable or "").strip()
        self._repo_root = Path(repo_root)

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        path = Path(filepath)
        if not path.is_absolute():
            path = path.resolve()
        if not path.is_file():
            raise RuntimeError(f"Maya open_file: file not found: {path!r}")
        try:
            os.startfile(str(path))
        except OSError as e:
            raise RuntimeError(f"Failed to open file with Maya: {path!r}") from e

    def create_new_file(
        self,
        *,
        filepath: str,
        context: dict[str, Any],
        on_ready: Callable[[str, str, bool, str], None] | None = None,
    ) -> None:
        """
        Create a new Maya work file and launch Maya GUI.
        If on_ready is provided, the batch step runs in a background thread and on_ready
        is called with (exe, filepath_norm, file_created, repo_root) so the caller can
        launch Maya on the main thread (e.g. via QTimer.singleShot) and avoid freezing the UI.
        """
        exe = resolve_maya_executable(self._maya_executable)
        if not exe:
            raise RuntimeError(_maya_missing_message(self._maya_executable))
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        filepath_norm = filepath.replace("\\", "/")
        repo_root_str = str(self._repo_root)
        batch_exe = _maya_batch_executable(exe)
        mel_script = f'file -new; file -rename "{filepath_norm}"; file -save;'

        def _run_batch() -> None:
            try:
                subprocess.run(
                    [batch_exe, "-command", mel_script],
                    cwd=repo_root_str,
                    timeout=60,
                    check=False,
                    capture_output=True,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            file_created = Path(filepath).is_file()
            if on_ready is not None:
                on_ready(exe, filepath_norm, file_created, repo_root_str)

        if on_ready is not None:
            thread = threading.Thread(target=_run_batch, daemon=True)
            thread.start()
            return

        _run_batch()
        path_abs = Path(filepath).resolve()
        try:
            if path_abs.is_file():
                os.startfile(str(path_abs))
            else:
                subprocess.Popen([exe], cwd=str(path_abs.parent), close_fds=True)
        except Exception as e:
            raise RuntimeError(f"Failed to launch Maya: {e!r}") from e
