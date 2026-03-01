from __future__ import annotations

import subprocess
import sys

from monostudio.core.app_paths import get_app_base_path

APP_MAJOR_VERSION = 26

_cached_version: str | None = None


def get_app_version() -> str:
    """Return version string 'v26.minor.patch' (e.g. v26.1.2).

    In frozen builds reads from VERSION file (26.minor.patch) baked at build time.
    In dev: read VERSION file if present, else fallback to v26.<git count>. Cached after first call.
    """
    global _cached_version
    if _cached_version is not None:
        return _cached_version

    base = get_app_base_path()
    vf = base / "monostudio_data" / "VERSION"
    try:
        if vf.exists():
            raw = vf.read_text(encoding="utf-8").strip().lstrip("vV")
            if raw:
                _cached_version = f"v{raw}" if not raw.startswith("v") else raw
                return _cached_version
    except OSError:
        pass

    if getattr(sys, "frozen", False):
        _cached_version = f"v{APP_MAJOR_VERSION}.0.0"
        return _cached_version

    # Dev fallback: git count
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=3,
            cwd=str(base),
        )
        if result.returncode == 0 and result.stdout.strip():
            _cached_version = f"v{APP_MAJOR_VERSION}.0.{result.stdout.strip()}"
            return _cached_version
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    _cached_version = f"v{APP_MAJOR_VERSION}.0.0"
    return _cached_version
