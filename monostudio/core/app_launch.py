"""Resolve executable, arguments, and working directory for shortcuts and autostart."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _gui_launch_executable() -> str:
    """Executable for GUI launch without a console window (autostart, shortcuts)."""
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return exe
    exe_path = Path(exe)
    if exe_path.name.lower() == "python.exe":
        pythonw = exe_path.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return exe


def gui_launch_executable() -> str:
    """Public alias for protocol handlers and shortcuts."""
    return _gui_launch_executable()


def app_deep_link_protocol_command(link_placeholder: str = "%1") -> str:
    """Windows registry ``shell\\open\\command`` for ``monostudio://`` (no console)."""
    exe = Path(_gui_launch_executable()).resolve()
    link = (link_placeholder or "%1").strip() or "%1"
    if getattr(sys, "frozen", False):
        return f'"{exe}" {link}'
    try:
        from monostudio.core.app_paths import get_app_base_path

        app_py = (get_app_base_path() / "app.py").resolve()
        if app_py.is_file():
            return f'"{exe}" "{app_py}" {link}'
    except Exception:
        pass
    repo_root = Path(__file__).resolve().parents[2]
    app_py = (repo_root / "app.py").resolve()
    return f'"{exe}" "{app_py}" {link}'


def app_launch_target() -> tuple[str, str, str]:
    """Return (executable, arguments, working_directory) for launching MONOS."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return exe, "", str(Path(exe).parent)
    try:
        from monostudio.core.app_paths import get_app_base_path

        root = get_app_base_path()
        app_py = root / "app.py"
        if app_py.is_file():
            return sys.executable, f'"{app_py.resolve()}"', str(root)
    except Exception:
        pass
    return sys.executable, "", os.getcwd()


def app_autostart_command(*, startup: bool = True) -> str:
    """Full command line for Windows Run registry (quoted where needed)."""
    exe, args, _work = app_launch_target()
    exe = _gui_launch_executable()
    parts: list[str] = [f'"{exe}"']
    if args:
        parts.append(args)
    if startup:
        parts.append("--startup")
    return " ".join(parts)
