"""
In-app update check via GitHub Releases API: fetch latest release, compare version, download installer.

Uses https://api.github.com/repos/OWNER/REPO/releases/latest (tag_name, assets, body).

Rate limit: Không token = 60 request/giờ. Set env MONOSTUDIO_GITHUB_TOKEN (hoặc GITHUB_TOKEN)
= Personal Access Token từ GitHub → Settings → Developer settings → Personal access tokens
để được 5000 request/giờ, miễn phí. Không commit token vào repo.

Extra tools (e.g. MonoFXSuite) — install location and version detection:
- MonoStudio install dir: from get_app_base_path() (frozen = exe dir, e.g. C:\\Program Files\\MonoStudio26).
- Recommended install for MonoFXSuite: {MonoStudio base}/tools/MonoFXSuite/
  so that MonoStudio can read installed version from tools/MonoFXSuite/VERSION (or .../monofxsuite_data/VERSION).
- Fallback: %LOCALAPPDATA%\\MonoStudio\\tools\\MonoFXSuite\\VERSION for user-level installs.
- VERSION file format: one line, e.g. "1.0.2" or "v1.0.2".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin
from pathlib import Path
from typing import Any

from monostudio.core.app_paths import get_app_base_path

# Cache kết quả check 1 giờ để tránh vượt rate limit GitHub (60 request/giờ khi không token)
CACHE_TTL_SECONDS = 3600

# GitHub repo for releases: "owner/repo"
GITHUB_REPO = "simplekile/MonoStudio26"

# Extra repos to show latest version in Settings → Updates; display_name used as install folder (e.g. MonoFXSuite)
EXTRA_REPOS: list[tuple[str, str]] = [
    ("MonoFXSuite", "simplekile/MonoFXSuite"),
]

# Subfolder under tools/ for version file (optional; e.g. "monofxsuite_data" mirrors monostudio_data)
EXTRA_TOOL_VERSION_PATHS: dict[str, str] = {
    "MonoFXSuite": "monofxsuite_data",  # tools/MonoFXSuite/monofxsuite_data/VERSION
}

GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# Direct download URL pattern: không phụ thuộc browser_download_url từ API
GITHUB_DOWNLOAD_TEMPLATE = f"https://github.com/{GITHUB_REPO}/releases/download/{{tag}}/{{filename}}"
# User-Agent required by GitHub API when not authenticated
GITHUB_REQUEST_HEADERS = {"Accept": "application/vnd.github.v3+json", "User-Agent": "MonoStudio26-UpdateCheck"}


@dataclass
class UpdateInfo:
    """Result of a successful update check (e.g. from GitHub Release)."""

    version: str  # e.g. "v26.25"
    url: str  # download URL for installer (browser_download_url)
    notes: str = ""  # release body / changelog (markdown)
    html_url: str = ""  # link to release page on GitHub
    asset_api_url: str = ""  # GitHub API asset URL; use for download when set (more reliable redirect)


@dataclass
class CheckResult:
    """Result of check_for_update: optional update + always latest release notes for UI."""

    update_available: bool
    update_info: UpdateInfo | None  # set when update_available
    latest_version: str
    latest_notes: str
    latest_html_url: str = ""


@dataclass
class ExtraRepoRelease:
    """Latest release info for an extra repo (e.g. MonoFXSuite); may include download_url for in-app update."""

    name: str  # display name
    version: str
    html_url: str = ""
    notes: str = ""
    download_url: str = ""  # installer asset URL (e.g. .exe) when available


def get_extra_tool_installed_version(display_name: str) -> str:
    """
    Return installed version of an extra tool (e.g. MonoFXSuite) so MonoStudio can show it in Settings → Updates.

    Lookup order:
    1. {MonoStudio base}/tools/{display_name}/VERSION or .../tools/{display_name}/{subfolder}/VERSION
    2. %LOCALAPPDATA%/MonoStudio/tools/{display_name}/VERSION

    Returns version string with "v" prefix (e.g. "v1.0.2") or "" if not found.
    """
    if not (display_name or "").strip():
        return ""
    name = display_name.strip()
    subfolder = EXTRA_TOOL_VERSION_PATHS.get(name, "")
    base = get_app_base_path()
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    candidates: list[Path] = []
    if subfolder:
        candidates.append(base / "tools" / name / subfolder / "VERSION")
    candidates.append(base / "tools" / name / "VERSION")
    if localappdata:
        if subfolder:
            candidates.append(Path(localappdata) / "MonoStudio" / "tools" / name / subfolder / "VERSION")
        candidates.append(Path(localappdata) / "MonoStudio" / "tools" / name / "VERSION")
    for p in candidates:
        try:
            if p.is_file():
                raw = p.read_text(encoding="utf-8").strip().lstrip("vV")
                if raw:
                    return f"v{raw}" if not raw.startswith("v") else raw
        except OSError:
            continue
    return ""


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


def _github_headers() -> dict[str, str]:
    """Headers for GitHub API; nếu có env GITHUB_TOKEN hoặc MONOSTUDIO_GITHUB_TOKEN thì dùng để tăng rate limit."""
    h = dict(GITHUB_REQUEST_HEADERS)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("MONOSTUDIO_GITHUB_TOKEN")
    if token and isinstance(token, str) and token.strip():
        h["Authorization"] = f"Bearer {token.strip()}"
    return h


def fetch_manifest(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Fetch JSON from url (e.g. GitHub Releases API or custom manifest).
    Uses GitHub-friendly headers when url contains 'api.github.com'.
    """
    if headers is None and "api.github.com" in url:
        headers = _github_headers()
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
        if e.code == 403:
            try:
                body = e.read().decode("utf-8", errors="ignore")
                if "rate limit" in body.lower() or "rate_limit" in body.lower():
                    raise RuntimeError(
                        "GitHub API rate limit exceeded. Try again in about an hour, or download the installer from the release page below."
                    ) from e
            except RuntimeError:
                raise
            except Exception:
                pass
            raise RuntimeError(
                "GitHub returned 403 (access denied or rate limit). Try again later or download from the release page below."
            ) from e
        raise RuntimeError(f"Update server returned {e.code}: {e.reason}") from e
    return json.loads(data)


def _pick_installer_asset(assets: list[dict[str, Any]]) -> tuple[str | None, str, str]:
    """From GitHub release assets, prefer Windows .exe (Setup); else first asset.
    Returns (browser_download_url, asset_api_url, asset_filename for direct URL).
    """
    if not assets or not isinstance(assets[0], dict):
        return None, "", ""
    for a in assets:
        name = (a.get("name") or "").strip()
        name_lower = name.lower()
        browser_u = a.get("browser_download_url")
        api_u = a.get("url") or ""
        if browser_u and isinstance(browser_u, str) and (".exe" in name_lower or "setup" in name_lower):
            return browser_u, api_u if isinstance(api_u, str) else "", name or "MonoStudio26_Setup.exe"
    a0 = assets[0]
    api_u = a0.get("url")
    fname = (a0.get("name") or "").strip() or "MonoStudio26_Setup.exe"
    return (
        a0.get("browser_download_url") if isinstance(a0.get("browser_download_url"), str) else None,
        api_u if isinstance(api_u, str) else "",
        fname,
    )


def parse_manifest(data: dict[str, Any]) -> UpdateInfo | None:
    """
    Parse manifest dict (or GitHub release JSON) into UpdateInfo.
    For GitHub: tự tính link tải thực tế từ tag + tên file, không dựa vào browser_download_url từ API.
    """
    version = data.get("version") or data.get("tag_name")
    if version and isinstance(version, str) and version.startswith("v"):
        pass
    elif version:
        version = f"v{version}" if isinstance(version, str) else None
    else:
        return None
    # Do NOT use data.get("url") for GitHub: that is the release API URL (e.g. .../releases/291776151), not the installer.
    url: str | None = None
    asset_api_url = ""
    if data.get("assets") and isinstance(data["assets"], list):
        assets = data["assets"]
        browser_u, api_u, filename = _pick_installer_asset(assets)
        asset_api_url = api_u or ""
        if version and filename and GITHUB_REPO and "your-org" not in GITHUB_REPO:
            url = GITHUB_DOWNLOAD_TEMPLATE.format(tag=version, filename=filename)
        else:
            url = browser_u
    if not url or not isinstance(url, str):
        url = data.get("url") if isinstance(data.get("url"), str) else None
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
    return UpdateInfo(version=version, url=url, notes=notes, html_url=html_url, asset_api_url=asset_api_url or "")


# Cache kết quả check để Settings hiển thị và để giảm gọi API (tránh rate limit).
_cached_check_result: CheckResult | None = None
_cached_check_time: float = 0.0


def get_cached_check_result() -> CheckResult | None:
    """Return the last update check result (e.g. from startup)."""
    return _cached_check_result


def set_cached_check_result(result: CheckResult | None) -> None:
    """Store update check result so Settings → Updates can show it without re-checking."""
    global _cached_check_result, _cached_check_time
    _cached_check_result = result
    _cached_check_time = time.time() if result is not None else 0.0


# Cache for extra repos (e.g. MonoFXSuite): name -> ExtraRepoRelease
_cached_extra_repos: dict[str, ExtraRepoRelease] = {}
_cached_extra_repos_time: float = 0.0


def _pick_installer_asset_extra(assets: list[dict[str, Any]], tag: str, repo: str) -> tuple[str, str]:
    """Pick first .exe/Setup asset from release; return (download_url, filename)."""
    if not assets or "/" not in repo:
        return "", ""
    owner_repo = repo.strip()
    for a in assets:
        name = (a.get("name") or "").strip()
        name_lower = name.lower()
        browser_u = a.get("browser_download_url")
        if browser_u and isinstance(browser_u, str) and (".exe" in name_lower or "setup" in name_lower):
            return browser_u, name or "Setup.exe"
    a0 = assets[0]
    browser_u = a0.get("browser_download_url") if isinstance(a0.get("browser_download_url"), str) else None
    fname = (a0.get("name") or "").strip() or "Setup.exe"
    if browser_u:
        return browser_u, fname
    if tag and fname:
        return f"https://github.com/{owner_repo}/releases/download/{tag}/{fname}", fname
    return "", ""


def _parse_release_to_extra(data: dict[str, Any], repo: str) -> ExtraRepoRelease | None:
    """Parse GitHub release JSON to ExtraRepoRelease (version, html_url, notes, download_url)."""
    version = data.get("tag_name") or data.get("version")
    if not version or not isinstance(version, str):
        return None
    if not version.startswith("v"):
        version = f"v{version}"
    html_url = (data.get("html_url") or "").strip() if isinstance(data.get("html_url"), str) else ""
    notes = (data.get("body") or data.get("notes") or "")
    notes = notes.strip()[:2000] if isinstance(notes, str) else ""
    name = repo.split("/")[-1] if "/" in repo else repo
    download_url = ""
    if data.get("assets") and isinstance(data["assets"], list):
        download_url, _ = _pick_installer_asset_extra(data["assets"], version, repo)
    return ExtraRepoRelease(name=name, version=version, html_url=html_url, notes=notes, download_url=download_url)


def get_latest_release_for_repo(
    repo: str,
    timeout: int = 15,
) -> ExtraRepoRelease | None:
    """
    Fetch latest release for a GitHub repo (owner/repo). Returns version, html_url, notes.
    Uses /releases/latest; if 404 (e.g. only prereleases exist) falls back to /releases and takes first.
    """
    if not repo or "/" not in repo:
        return None
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        data = fetch_manifest(api_url, timeout=timeout)
        out = _parse_release_to_extra(data, repo)
        if out:
            return out
        return None
    except Exception as e:
        # 404 / not found: often means no "latest" (e.g. only prereleases) — try listing releases
        err_msg = str(e).lower()
        is_404 = getattr(e, "code", None) == 404 or "404" in err_msg or "not found" in err_msg
        if is_404:
            try:
                list_url = f"https://api.github.com/repos/{repo}/releases?per_page=5"
                raw = fetch_manifest(list_url, timeout=timeout)
                if isinstance(raw, list) and raw:
                    data = raw[0]
                    out = _parse_release_to_extra(data, repo)
                    if out:
                        return out
            except Exception:
                pass
        print(f"[MonoStudio] Extra repo {repo}: {e}", flush=True)
        return None


def fetch_extra_repos(timeout: int = 15) -> dict[str, ExtraRepoRelease]:
    """Fetch latest release for all EXTRA_REPOS; update cache and return."""
    global _cached_extra_repos, _cached_extra_repos_time
    result: dict[str, ExtraRepoRelease] = {}
    for display_name, repo in EXTRA_REPOS:
        info = get_latest_release_for_repo(repo, timeout=timeout)
        if info:
            result[display_name] = info
    _cached_extra_repos = result
    _cached_extra_repos_time = time.time()
    return result


def get_cached_extra_repos() -> dict[str, ExtraRepoRelease]:
    """Return cached extra repo releases (e.g. from last check)."""
    return dict(_cached_extra_repos)


def check_for_update(
    current_version: str,
    manifest_url: str | None = None,
    timeout: int = 15,
) -> CheckResult:
    """
    Check for update via GitHub Releases API; returns update (if newer) and latest release notes.
    Kết quả cache 1 giờ để tránh vượt rate limit GitHub (chỉ gọi API khi cache hết hạn).
    Set env MONOSTUDIO_FAKE_UPDATE=1 to force "update available" for testing.
    Raises on network/HTTP error or invalid response.
    """
    if manifest_url is None:
        manifest_url = GITHUB_API_LATEST
    if "your-org" in manifest_url or GITHUB_REPO.startswith("your-org"):
        raise RuntimeError(
            "GITHUB_REPO is not configured. Edit monostudio/core/update_checker.py and set "
            "GITHUB_REPO = 'owner/repo' to your GitHub repository."
        )
    # Dùng cache nếu còn hạn → không gọi API, tránh rate limit
    global _cached_check_result, _cached_check_time
    if _cached_check_result is not None and (_cached_check_time > 0 and (time.time() - _cached_check_time) < CACHE_TTL_SECONDS):
        c = _cached_check_result
        update_available = os.environ.get("MONOSTUDIO_FAKE_UPDATE", "").strip() in ("1", "true", "yes") or is_newer_than(current_version, c.latest_version)
        return CheckResult(
            update_available=update_available,
            update_info=c.update_info if update_available else None,
            latest_version=c.latest_version,
            latest_notes=c.latest_notes,
            latest_html_url=c.latest_html_url or "",
        )
    data = fetch_manifest(manifest_url, timeout=timeout)
    info = parse_manifest(data)
    if info is None:
        raise RuntimeError(
            "Latest release has no tag or installer asset. Create a Release on GitHub with a tag (e.g. v26.0.25) "
            "and attach MonoStudio26_Setup.exe."
        )
    fake_update = os.environ.get("MONOSTUDIO_FAKE_UPDATE", "").strip() in ("1", "true", "yes")
    update_available = fake_update or is_newer_than(current_version, info.version)
    notes = info.notes or ""
    if fake_update:
        notes = "**Debug:** MONOSTUDIO_FAKE_UPDATE=1 — Download button shown for testing. Real latest: " + info.version + "\n\n" + notes
    result = CheckResult(
        update_available=update_available,
        update_info=info if update_available else None,
        latest_version=info.version,
        latest_notes=notes,
        latest_html_url=info.html_url or "",
    )
    set_cached_check_result(result)
    return result


# Headers sent on every request (including after redirect) so GitHub CDN accepts the download
_DOWNLOAD_HEADERS = {
    "Accept": "application/octet-stream",
    "User-Agent": "MonoStudio26-UpdateCheck/1.0 (Windows; Python)",
}


class _RedirectWithHeadersHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects but re-send our headers on the new request (GitHub CDN needs User-Agent)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fullurl = urljoin(req.get_full_url(), newurl)
        return urllib.request.Request(fullurl, headers=_DOWNLOAD_HEADERS, method="GET")


def _download_with_urllib(url: str, dest_path: Path, timeout: int, progress_callback: Any) -> None:
    """Download using urllib with redirect handler that re-sends headers."""
    opener = urllib.request.build_opener(_RedirectWithHeadersHandler())
    req = urllib.request.Request(url, headers=_DOWNLOAD_HEADERS, method="GET")
    resp = opener.open(req, timeout=timeout)
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


def _download_with_powershell(url: str, dest_path: Path, timeout: int) -> None:
    """Fallback on Windows: use PowerShell Invoke-WebRequest (often works when Python HTTP fails)."""
    import tempfile
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    ua = _DOWNLOAD_HEADERS.get("User-Agent", "MonoStudio26")
    script = f"""
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -Uri $args[0] -OutFile $args[1] -UseBasicParsing -UserAgent $args[2] -TimeoutSec $args[3]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(script)
        script_path = f.name
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path, url, str(dest_path), ua, str(timeout)],
            capture_output=True,
            text=True,
            timeout=timeout + 15,
        )
        if out.returncode != 0:
            err = (out.stderr or out.stdout or "").strip() or f"Exit code {out.returncode}"
            raise RuntimeError(f"PowerShell: {err}")
    finally:
        try:
            Path(script_path).unlink(missing_ok=True)
        except Exception:
            pass


def _do_download(url: str, dest_path: Path, timeout: int, progress_callback: Any) -> None:
    """Single attempt: requests -> urllib -> PowerShell. Raises on failure."""
    last_error: Exception | None = None
    try:
        import requests
        r = requests.get(url, headers=_DOWNLOAD_HEADERS, timeout=timeout, stream=True, allow_redirects=True)
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None
        read = 0
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    read += len(chunk)
                    if progress_callback:
                        progress_callback(read, total)
        return
    except ImportError:
        pass
    except Exception as e:
        last_error = e
    try:
        _download_with_urllib(url, dest_path, timeout, progress_callback)
        return
    except Exception as e:
        last_error = e
    if sys.platform == "win32":
        try:
            _download_with_powershell(url, dest_path, timeout)
            return
        except Exception as e:
            last_error = e
    raise RuntimeError(str(last_error) if last_error else "Download failed")


def download_installer(
    url: str,
    dest_path: Path,
    timeout: int = 300,
    progress_callback: Any = None,
    fallback_url: str | None = None,
) -> None:
    """
    Download installer from url to dest_path. If fallback_url is set and the file is invalid
    (e.g. HTML/error page), retry with fallback_url so we try both API and browser_download_url.
    """
    fallback_url = (fallback_url or "").strip() or None
    if fallback_url == url:
        fallback_url = None
    print(f"[MonoStudio] Download URL: {url}", flush=True)
    _do_download(url, dest_path, timeout, progress_callback)
    if fallback_url and not is_valid_installer(dest_path):
        try:
            dest_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[MonoStudio] Retry download (fallback): {fallback_url}", flush=True)
        _do_download(fallback_url, dest_path, timeout, progress_callback)


# Minimum size for a valid installer (bytes); smaller likely HTML/error page
_MIN_INSTALLER_SIZE = 1 * 1024 * 1024  # 1 MB


def is_valid_installer(path: Path) -> bool:
    """Return True if path exists, has reasonable size, and looks like a Windows PE (.exe)."""
    try:
        if not path.is_file():
            return False
        st = path.stat()
        if st.st_size < _MIN_INSTALLER_SIZE:
            return False
        with open(path, "rb") as f:
            header = f.read(2)
        return header == b"MZ"
    except (OSError, IOError):
        return False


def launch_installer(installer_path: Path) -> None:
    """
    Launch the installer (e.g. .exe) without exiting the app.
    Raises RuntimeError if the file is not a valid Windows executable.
    """
    if not is_valid_installer(installer_path):
        raise RuntimeError(
            "Downloaded file is not a valid installer (may be corrupted or an error page). "
            "Try downloading from the release page in your browser."
        )
    if sys.platform == "win32":
        subprocess.Popen(
            [str(installer_path)],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    else:
        subprocess.Popen([str(installer_path)])


def run_installer_and_exit(installer_path: Path) -> None:
    """
    Launch the installer and exit the app so MonoStudio's files can be replaced.
    For other tools use launch_installer() so the app stays open.
    """
    launch_installer(installer_path)
    sys.exit(0)
