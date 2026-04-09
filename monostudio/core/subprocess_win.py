"""Windows: spawn CLI subprocesses without flashing a console (important for frozen GUI builds)."""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def hide_console_subprocess_kwargs() -> dict[str, Any]:
    """
    Extra kwargs for subprocess.run / Popen on Windows so ffmpeg/ffprobe etc. do not open cmd windows.
    No-op on other platforms.
    """
    if sys.platform != "win32":
        return {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo}
