from __future__ import annotations

import subprocess

from monostudio.core.app_paths import get_app_base_path

APP_MAJOR_VERSION = 26


def get_app_version() -> str:
    """Return version string like 'v26.21' (major + git commit count)."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(get_app_base_path()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"v{APP_MAJOR_VERSION}.{result.stdout.strip()}"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return f"v{APP_MAJOR_VERSION}"
