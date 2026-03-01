"""
In-app update check via GitHub Releases API: fetch latest release, compare version, download installer.

Uses https://api.github.com/repos/OWNER/REPO/releases/latest (tag_name, assets, body).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin
from pathlib import Path
from typing import Any

# GitHub repo for releases: "owner/repo"
GITHUB_REPO = "simplekile/MonoStudio26"

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


@dataclass
class CheckResult:
    """Result of check_for_update: optional update + always latest release notes for UI."""

    update_available: bool
    update_info: UpdateInfo | None  # set when update_available
    latest_version: str
    latest_notes: str
    latest_html_url: str = ""


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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(
                "Repository or release not found. Set GITHUB_REPO in monostudio/core/update_checker.py "
                "to your repo (e.g. owner/MonoStudio26) and create a Release with tag v26.0.x and an installer .exe asset."
            ) from e
        raise RuntimeError(f"Update server returned {e.code}: {e.reason}") from e
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


# Cache for startup check result; Settings → Updates can use it without re-checking.
_cached_check_result: CheckResult | None = None


def get_cached_check_result() -> CheckResult | None:
    """Return the last update check result (e.g. from startup)."""
    return _cached_check_result


def set_cached_check_result(result: CheckResult | None) -> None:
    """Store update check result so Settings → Updates can show it without re-checking."""
    global _cached_check_result
    _cached_check_result = result


def check_for_update(
    current_version: str,
    manifest_url: str | None = None,
    timeout: int = 15,
) -> CheckResult:
    """
    Check for update via GitHub Releases API; returns update (if newer) and latest release notes.
    UI can always show release notes for the latest version, even when up to date.
    Set env MONOSTUDIO_FAKE_UPDATE=1 to force update available (for testing).
    Raises on network/HTTP error or invalid response.
    """
    if os.environ.get("MONOSTUDIO_FAKE_UPDATE", "").strip() in ("1", "true", "yes"):
        fake_version = "v99.0.0"
        return CheckResult(
            update_available=True,
            update_info=UpdateInfo(
                version=fake_version,
                url="https://github.com/simplekile/MonoStudio26/releases/latest",
                notes="**Debug fake update** – Set `MONOSTUDIO_FAKE_UPDATE=0` to disable.",
                html_url="https://github.com/simplekile/MonoStudio26/releases",
            ),
            latest_version=fake_version,
            latest_notes="**Debug fake update** – Used for testing the update UI.",
            latest_html_url="https://github.com/simplekile/MonoStudio26/releases",
        )
    if manifest_url is None:
        manifest_url = GITHUB_API_LATEST
    if "your-org" in manifest_url or GITHUB_REPO.startswith("your-org"):
        raise RuntimeError(
            "GITHUB_REPO is not configured. Edit monostudio/core/update_checker.py and set "
            "GITHUB_REPO = 'owner/repo' to your GitHub repository."
        )
    data = fetch_manifest(manifest_url, timeout=timeout)
    info = parse_manifest(data)
    if info is None:
        raise RuntimeError(
            "Latest release has no tag or installer asset. Create a Release on GitHub with a tag (e.g. v26.0.25) "
            "and attach MonoStudio26_Setup.exe."
        )
    update_available = is_newer_than(current_version, info.version)
    return CheckResult(
        update_available=update_available,
        update_info=info if update_available else None,
        latest_version=info.version,
        latest_notes=info.notes or "",
        latest_html_url=info.html_url or "",
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent automatic redirect so we can re-send User-Agent on the next request (GitHub CDN)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # do not follow; caller will request newurl with same headers


def download_installer(url: str, dest_path: Path, timeout: int = 300, progress_callback: Any = None) -> None:
    """
    Download installer from url to dest_path.
    progress_callback(current: int, total: int | None) optional; total may be None if unknown.
    Follows redirects manually so User-Agent is sent to GitHub CDN (urllib does not re-send headers on redirect).
    Raises on failure.
    """
    headers = {"Accept": "application/octet-stream", "User-Agent": "MonoStudio26-UpdateCheck/1.0"}
    opener = urllib.request.build_opener(_NoRedirectHandler())
    current_url = url
    redirect_limit = 10
    while redirect_limit > 0:
        redirect_limit -= 1
        req = urllib.request.Request(current_url, headers=headers, method="GET")
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location")
                if location:
                    current_url = urljoin(current_url, location)
                    continue
            raise
        # 200 OK
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
        resp.close()
        return
    raise RuntimeError("Too many redirects while downloading installer")


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
