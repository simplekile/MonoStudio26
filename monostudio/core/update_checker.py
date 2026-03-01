"""
In-app update check via GitHub Releases API: fetch latest release, compare version, download installer.

Uses https://api.github.com/repos/OWNER/REPO/releases/latest (tag_name, assets, body).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# GitHub repo for releases: "owner/repo". Replace with your repo.
GITHUB_REPO = "your-org/MonoStudio26"

GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# User-Agent required by GitHub API when not authenticated
GITHUB_REQUEST_HEADERS = {"Accept": "application/vnd.github.v3+json", "User-Agent": "MonoStudio26-UpdateCheck"}


@dataclass
class UpdateInfo:
    """Result of a successful update check (e.g. from GitHub Release)."""

    version: str  # e.g. "v26.25"
    url: str  # download URL for installer
    notes: str = ""  # release body / changelog (markdown)
    html_url: str = ""  # link to release page on GitHub


def parse_version(version_str: str) -> tuple[int, int, int]:
    """
    Parse version string into (major, minor, patch).
    v26.1.2 -> (26, 1, 2); v26.24 (legacy) -> (26, 0, 24). Returns (0, 0, 0) if unparseable.
    """
    version_str = (version_str or "").strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_str)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d+)\.(\d+)$", version_str)
    if m:
        return (int(m.group(1)), 0, int(m.group(2)))
    return (0, 0, 0)


def is_newer_than(current: str, latest: str) -> bool:
    """Return True if latest is strictly newer than current (major.minor.patch)."""
    cur = parse_version(current)
    lat = parse_version(latest)
    if lat[0] != cur[0]:
        return lat[0] > cur[0]
    if lat[1] != cur[1]:
        return lat[1] > cur[1]
    return lat[2] > cur[2]


def fetch_manifest(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Fetch JSON from url (e.g. GitHub Releases API or custom manifest).
    Uses GitHub-friendly headers when url contains 'api.github.com'.
    """
    if headers is None and "api.github.com" in url:
        headers = GITHUB_REQUEST_HEADERS
    if headers is None:
        headers = {"Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def _pick_installer_asset(assets: list[dict[str, Any]]) -> str | None:
    """From GitHub release assets, prefer Windows .exe (Setup); else first asset."""
    if not assets or not isinstance(assets[0], dict):
        return None
    for a in assets:
        name = (a.get("name") or "").lower()
        u = a.get("browser_download_url")
        if u and isinstance(u, str) and (".exe" in name or "setup" in name):
            return u
    return assets[0].get("browser_download_url") if isinstance(assets[0].get("browser_download_url"), str) else None


def parse_manifest(data: dict[str, Any]) -> UpdateInfo | None:
    """
    Parse manifest dict (or GitHub release JSON) into UpdateInfo.
    Expected: version or tag_name; url or assets[].browser_download_url; optional notes/body.
    For GitHub, prefers .exe/Setup asset.
    """
    version = data.get("version") or data.get("tag_name")
    if version and isinstance(version, str) and version.startswith("v"):
        pass
    elif version:
        version = f"v{version}" if isinstance(version, str) else None
    else:
        return None
    url = data.get("url")
    if not url and data.get("assets"):
        assets = data["assets"]
        if isinstance(assets, list):
            url = _pick_installer_asset(assets)
    if not url or not isinstance(url, str):
        return None
    notes = data.get("notes") or data.get("body") or ""
    if isinstance(notes, str):
        notes = notes.strip()[:5000]
    else:
        notes = ""
    html_url = data.get("html_url") or ""
    if isinstance(html_url, str):
        html_url = html_url.strip()
    else:
        html_url = ""
    return UpdateInfo(version=version, url=url, notes=notes, html_url=html_url)


def check_for_update(
    current_version: str,
    manifest_url: str | None = None,
    timeout: int = 15,
) -> UpdateInfo | None:
    """
    Check for update via GitHub Releases API (default) or custom manifest_url.
    Returns UpdateInfo if there is a newer version, else None.
    Returns None on any error (network, parse, or not newer).
    """
    if manifest_url is None:
        manifest_url = GITHUB_API_LATEST
    try:
        data = fetch_manifest(manifest_url, timeout=timeout)
        info = parse_manifest(data)
        if info is None:
            return None
        if is_newer_than(current_version, info.version):
            return info
        return None
    except Exception:
        return None


def download_installer(url: str, dest_path: Path, timeout: int = 300, progress_callback: Any = None) -> None:
    """
    Download installer from url to dest_path.
    progress_callback(current: int, total: int | None) optional; total may be None if unknown.
    Raises on failure.
    """
    req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        read = 0
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if progress_callback:
                    progress_callback(read, total)


def run_installer_and_exit(installer_path: Path) -> None:
    """
    Launch the installer (e.g. Inno Setup exe) and exit the app so files can be replaced.
    On Windows typically: subprocess.Popen([str(installer_path)], ...) then sys.exit(0).
    """
    if sys.platform == "win32":
        subprocess.Popen(
            [str(installer_path)],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    else:
        subprocess.Popen([str(installer_path)])
    sys.exit(0)
