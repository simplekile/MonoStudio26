"""Write VERSION file (v26.minor.patch) for PyInstaller build. Run after commit, before pyinstaller.
Bumps version from last commit message: fix: -> patch+1, feat:/style:/docs: -> minor+1 patch=0, chore: -> patch+1.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

APP_MAJOR_VERSION = 26
VERSION_FILE = Path(__file__).resolve().parent / "monostudio_data" / "VERSION"


def _read_current() -> tuple[int, int, int]:
    """Parse existing VERSION file. Returns (major, minor, patch). Legacy v26.24 -> (26, 0, 24)."""
    if not VERSION_FILE.exists():
        return (APP_MAJOR_VERSION, 0, 0)
    raw = VERSION_FILE.read_text(encoding="utf-8").strip().lstrip("vV")
    # 26.1.2 or 26.0.24
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", raw)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # legacy: 26.24 -> (26, 0, 24)
    m = re.match(r"^(\d+)\.(\d+)$", raw)
    if m:
        return (int(m.group(1)), 0, int(m.group(2)))
    return (APP_MAJOR_VERSION, 0, 0)


def _last_commit_message() -> str:
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent),
        )
        return (r.stdout or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def main() -> None:
    repo = Path(__file__).resolve().parent
    major, minor, patch = _read_current()
    msg = _last_commit_message()
    if msg.startswith("fix:"):
        patch += 1
    elif msg.startswith("feat:") or msg.startswith("style:") or msg.startswith("docs:"):
        minor += 1
        patch = 0
    else:
        # chore: or anything else
        patch += 1
    version_str = f"{major}.{minor}.{patch}"
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(version_str, encoding="utf-8")
    print(f"Wrote {VERSION_FILE}: v{version_str}")


if __name__ == "__main__":
    main()
