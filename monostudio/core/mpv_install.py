"""Download / extract portable libmpv into LocalAppData — mirror ffmpeg_install pattern."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from monostudio.core.mpv_resolve import find_mpv_dll_under, mpv_bundle_install_root, mpv_dll_install_name
from monostudio.core.subprocess_win import hide_console_subprocess_kwargs

# libmpv dev builds (shinchiro / SourceForge) — NOT the ``release/`` player zips (no DLL).
MPV_BUILDS_PAGE = "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/"
# Pinned to match mpv 0.41 era; update when bumping bundled mpv.
MPV_WIN64_7Z_VERSION = "20251228-git-a58dd8a"
MPV_WIN64_7Z_NAME = f"mpv-dev-x86_64-{MPV_WIN64_7Z_VERSION}.7z"
MPV_WIN64_7Z_FALLBACK_VERSION = "20251214-git-f7be2ee"
MPV_WIN64_7Z_FALLBACK_NAME = f"mpv-dev-x86_64-{MPV_WIN64_7Z_FALLBACK_VERSION}.7z"


def sourceforge_file_download_url(project: str, file_path: str) -> str:
    """SourceForge browser download URL (redirects to CDN). Direct ``downloads.sf.net`` links 404."""
    rel = file_path.strip("/")
    return f"https://sourceforge.net/projects/{project.strip('/')}/files/{rel}/download"


MPV_WIN64_7Z_URL = sourceforge_file_download_url("mpv-player-windows", f"libmpv/{MPV_WIN64_7Z_NAME}")
MPV_WIN64_7Z_FALLBACK_URL = sourceforge_file_download_url(
    "mpv-player-windows", f"libmpv/{MPV_WIN64_7Z_FALLBACK_NAME}"
)

_MPV_DLL_NAMES = ("mpv-2.dll", "mpv-1.dll", "libmpv-2.dll", "libmpv-1.dll")
_MPV_SUPPORT_SUFFIXES = {".dll", ".exe", ".manifest"}


def is_plausible_zip(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < 500_000:
            return False
        with open(path, "rb") as f:
            sig = f.read(4)
        return sig == b"PK\x03\x04" or (len(sig) >= 2 and sig[:2] == b"PK")
    except OSError:
        return False


def is_plausible_7z(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < 5_000_000:
            return False
        with open(path, "rb") as f:
            sig = f.read(6)
        return sig == b"7z\xbc\xaf\x27\x1c"
    except OSError:
        return False


def invalid_archive_hint(path: Path) -> str:
    """Human-readable reason when a downloaded archive fails signature checks."""
    try:
        if not path.is_file():
            return "Download did not create a file on disk."
        size = path.stat().st_size
        if size < 5000:
            return "Downloaded file is too small (likely an error page)."
        with open(path, "rb") as f:
            head = f.read(512)
        if head.lstrip().startswith(b"<!") or b"<html" in head[:400].lower():
            return "Download returned a web page instead of a 7z archive (try again or use Official builds)."
        if head[:2] == b"PK":
            return "Downloaded file is a zip, not 7z — extract manually or pick the .7z build."
        return ""
    except OSError:
        return ""


def download_mpv_win64_7z(
    dest_path: Path,
    *,
    timeout: int = 900,
    progress_callback=None,
    abort=None,
) -> None:
    """Download portable mpv ``.7z``; tries current release then one version back."""
    from monostudio.core.update_checker import download_to_file

    urls = [MPV_WIN64_7Z_URL, MPV_WIN64_7Z_FALLBACK_URL]
    last_error = ""
    for url in urls:
        if abort is not None and abort.is_cancelled():
            raise RuntimeError("Cancelled")
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if dest_path.is_file():
                dest_path.unlink(missing_ok=True)
            download_to_file(url, dest_path, timeout=timeout, progress_callback=progress_callback, abort=abort)
            if is_plausible_7z(dest_path):
                return
            hint = invalid_archive_hint(dest_path)
            last_error = hint or "Downloaded file is not a valid 7z (try again or use Official builds)."
            dest_path.unlink(missing_ok=True)
        except RuntimeError:
            raise
        except Exception as e:
            last_error = str(e).replace("\n", " ")[:400]
    raise RuntimeError(last_error or "Download failed.")


def find_7z_exe() -> Path | None:
    candidates = [
        shutil.which("7z"),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
    ]
    for raw in candidates:
        if not raw:
            continue
        p = Path(raw)
        if p.is_file() and p.name.lower() == "7z.exe":
            return p.resolve()
        guess = p / "7-Zip" / "7z.exe"
        if guess.is_file():
            return guess.resolve()
    return None


def _pick_mpv_bundle_root(extract_dir: Path) -> Path:
    found = find_mpv_dll_under(extract_dir)
    if found is not None:
        return found.parent
    dirs = [
        c
        for c in extract_dir.iterdir()
        if c.is_dir() and c.name != "__MACOSX" and not c.name.startswith(".")
    ]
    for d in dirs:
        if find_mpv_dll_under(d) is not None:
            return d
    if len(dirs) == 1:
        return dirs[0]
    raise RuntimeError(
        "Could not find mpv-2.dll in the archive. "
        "The release player zip does not include libmpv — use Get libmpv again (dev build)."
    )


def _copy_portable_mpv_files(source_dir: Path, target_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    dll = find_mpv_dll_under(source_dir)
    if dll is None:
        raise RuntimeError(
            "mpv-2.dll not found in the archive. "
            "Use a libmpv dev build (libmpv-2.dll), not the release player zip."
        )

    dll_dir = dll.parent
    copied_main = False
    for item in dll_dir.iterdir():
        if not item.is_file():
            continue
        suffix = item.suffix.lower()
        if suffix not in _MPV_SUPPORT_SUFFIXES and item.name.lower() not in _MPV_DLL_NAMES:
            continue
        dest_name = mpv_dll_install_name(item) if item.name.lower().endswith(".dll") else item.name
        dest = target_dir / dest_name
        shutil.copy2(item, dest)
        if dest_name.startswith("mpv-") and dest_name.endswith(".dll"):
            copied_main = True

    if not copied_main:
        dest = target_dir / mpv_dll_install_name(dll)
        shutil.copy2(dll, dest)

    installed = find_mpv_dll_under(target_dir)
    if installed is None:
        raise RuntimeError("mpv-2.dll missing after install.")
    # python-mpv loads ``mpv-2.dll`` by name — normalize if we only copied libmpv-2.dll.
    canonical = target_dir / mpv_dll_install_name(installed)
    if installed.name.lower() != canonical.name.lower():
        shutil.copy2(installed, canonical)
    if not (target_dir / "mpv-2.dll").is_file() and not (target_dir / "mpv-1.dll").is_file():
        raise RuntimeError("mpv-2.dll missing after install.")
    return target_dir / "mpv-2.dll" if (target_dir / "mpv-2.dll").is_file() else target_dir / "mpv-1.dll"


def install_mpv_from_portable_folder(source_dir: Path) -> Path:
    """Copy mpv DLL (+ sibling deps) from a portable folder into the app bundle root."""
    root = mpv_bundle_install_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    return _copy_portable_mpv_files(source_dir, root)


def extract_mpv_portable_zip(zip_path: Path) -> Path:
    """Extract a portable mpv zip into ``mpv_bundle_install_root()``."""
    if not is_plausible_zip(zip_path):
        raise RuntimeError("Downloaded file is not a valid zip archive.")
    root = mpv_bundle_install_root()
    root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=str(root.parent)) as td_name:
        td = Path(td_name)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(td)
        bundle = _pick_mpv_bundle_root(td)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        return _copy_portable_mpv_files(bundle, root)


def extract_mpv_portable_7z(archive_path: Path) -> Path:
    """Extract shinchiro ``.7z`` portable mpv into ``mpv_bundle_install_root()``."""
    if not is_plausible_7z(archive_path):
        raise RuntimeError("Downloaded file is not a valid 7z archive.")
    seven = find_7z_exe()
    if seven is None:
        raise RuntimeError(
            "7-Zip is required to extract mpv. Install from https://www.7-zip.org/ "
            "or extract the archive manually and use Browse in Video preview settings."
        )
    root = mpv_bundle_install_root()
    root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=str(root.parent)) as td_name:
        td = Path(td_name)
        proc = subprocess.run(
            [str(seven), "x", "-y", f"-o{td}", str(archive_path)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:400]
            raise RuntimeError(err or "7-Zip extraction failed.")
        bundle = _pick_mpv_bundle_root(td)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        return _copy_portable_mpv_files(bundle, root)
