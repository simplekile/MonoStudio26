"""
Crash logging and recovery context.

- Global exception handler logs uncaught exceptions to disk with context.
- Crash context (last project, active task) is set by app/worker; handler records it.
- Crash logs persist across restarts for diagnostics. No UI, no auto-repair.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Context set by MainWindow / WorkerManager (string paths; safe to read from any thread at crash time)
_last_project_path: str | None = None
_current_task_category: str | None = None


def set_crash_context(*, last_project_path: str | None = None, current_task_category: str | None = None) -> None:
    """Update context for crash reports. Pass None to leave unchanged."""
    global _last_project_path, _current_task_category
    if last_project_path is not None:
        _last_project_path = last_project_path
    if current_task_category is not None:
        _current_task_category = current_task_category


def get_crash_context() -> tuple[str | None, str | None]:
    """Return (last_project_path, current_task_category)."""
    return (_last_project_path, _current_task_category)


def _crash_log_dir() -> Path:
    """Directory for crash logs; persists across restarts. No Qt dependency so safe at crash time."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "")) or Path.home()
    else:
        base = Path.home()
    return base / ".monostudio" / "crash_logs"


def _install_excepthook() -> None:
    """Install global excepthook that logs to disk and re-raises."""
    _original = sys.excepthook

    def _hook(etype, value, tb):
        try:
            log_dir = _crash_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"monos_crash_{ts}.log"
            proj, task = get_crash_context()
            lines = [
                "MONOS crash report",
                f"Time: {datetime.now(timezone.utc).isoformat()}",
                f"Platform: {sys.platform}",
                f"Python: {sys.version.split()[0]}",
                f"Last project: {proj or '(none)'}",
                f"Active task category: {task or '(none)'}",
                "",
                "Traceback:",
                "".join(traceback.format_exception(etype, value, tb)),
            ]
            log_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass
        if _original is not None:
            _original(etype, value, tb)

    sys.excepthook = _hook


def install_crash_logging() -> None:
    """Call once at application startup to install crash logging."""
    _install_excepthook()
