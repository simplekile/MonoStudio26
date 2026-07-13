"""
Resolve playblast / preview image sequences under department work/.

Convention (shots and assets):
  work/<render|preview|playblast|flipbook>/<work_file_stem_or_name>/  → image sequence files

Root folder names are matched case-insensitively. Priority when several exist:
``render`` → ``preview`` → ``playblast`` → ``flipbook``. If the work-named
subfolder is missing under the first root, the next root is tried (e.g. only
``flipbook/<name>/`` exists while ``render/`` is present but empty).

Within one canonical root name, newer mtime wins if duplicate directories exist.

Supported frame extensions include common plate formats (e.g. ``.dpx``) plus png/jpg/tif/tga/exr/webp.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

# Direct children of work_path only (case-insensitive); search order.
_SEQUENCE_ROOT_PRIORITY = ("render", "preview", "playblast", "flipbook")
_SEQUENCE_ROOT_NAMES_CF = frozenset(_SEQUENCE_ROOT_PRIORITY)

# Frames we list for flipbook / representative thumb (flat folder only, v1).
_SEQUENCE_SUFFIXES = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".tga",
    ".exr",
    ".webp",
    ".dpx",
})


def path_matches_sequence_ignore_tokens(
    path: Path | str,
    ignore_name_tokens: Iterable[str] | None,
) -> bool:
    """True when filename contains any ignore token (case-insensitive substring)."""
    ign_tok = {str(s).strip().casefold() for s in (ignore_name_tokens or ()) if str(s).strip()}
    if not ign_tok:
        return False
    name_cf = Path(path).name.casefold()
    return any(t in name_cf for t in ign_tok)


def _mtime_key_ns(p: Path) -> int:
    try:
        return int(p.stat().st_mtime_ns)
    except OSError:
        return 0


def _sequence_roots_by_priority(work_path: Path) -> list[Path]:
    """Existing ``work_path/<render|preview|playblast|flipbook>/`` dirs, best mtime per name, in priority order."""
    if not work_path.is_dir():
        return []
    try:
        children = [c for c in work_path.iterdir() if c.is_dir()]
    except OSError:
        return []
    best_by_cf: dict[str, Path] = {}
    for c in children:
        cf = c.name.casefold()
        if cf not in _SEQUENCE_ROOT_NAMES_CF:
            continue
        prev = best_by_cf.get(cf)
        if prev is None or _mtime_key_ns(c) > _mtime_key_ns(prev):
            best_by_cf[cf] = c
    out: list[Path] = []
    for name in _SEQUENCE_ROOT_PRIORITY:
        hit = best_by_cf.get(name)
        if hit is not None:
            out.append(hit)
    return out


def work_file_folder_name_candidates(work_file_path: Path | None) -> tuple[str, ...]:
    """Stem and full filename (no path) for matching a child folder under sequence roots."""
    if work_file_path is None:
        return ()
    out: list[str] = []
    for s in (work_file_path.stem, work_file_path.name):
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)
    return tuple(out)


def resolve_sequence_folder(work_path: Path, work_file_path: Path | None) -> Path | None:
    """
    Return folder containing sequence frames:
    ``work/<render|preview|playblast|flipbook>/<work_name>/``.
    Tries each root in priority order until a matching work-named child exists.
    Requires ``work_file_path`` to derive folder names; no fallback to arbitrary subdirs.
    """
    names = work_file_folder_name_candidates(work_file_path)
    if not names:
        return None
    for root in _sequence_roots_by_priority(work_path):
        for n in names:
            p = root / n
            if p.is_dir():
                return p
        try:
            by_cf = {c.name.casefold(): c for c in root.iterdir() if c.is_dir()}
        except OSError:
            continue
        for n in names:
            hit = by_cf.get(n.casefold())
            if hit is not None:
                return hit
    return None


_WORKFILE_VERSION_RE = re.compile(r"(?:^|_)v(\d{3,})(?:_|$)", re.IGNORECASE)


def _parse_workfile_version_from_folder_name(folder_name: str) -> int | None:
    """
    Best-effort: parse a version number from a work-named sequence folder.
    Expected patterns include "..._v001" or "..._v001_fixNecklace".
    """
    s = (folder_name or "").strip()
    if not s:
        return None
    m = _WORKFILE_VERSION_RE.search(s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def resolve_best_available_sequence_folder(work_path: Path) -> Path | None:
    """
    Fallback: when the latest work file has no render/preview sequence yet, pick the best available
    work-named folder under ``work/<render|preview|playblast|flipbook>/``.

    Selection rules (v1):
    - Root priority: render → preview → playblast → flipbook
    - Within the first root that contains any usable sequence folder:
      - Prefer the highest parsed version (if any folders have a parseable v###)
      - Otherwise prefer newest folder mtime
    - Requires the candidate folder to contain at least one frame file.
    """
    if not work_path.is_dir():
        return None
    for root in _sequence_roots_by_priority(work_path):
        try:
            children = [c for c in root.iterdir() if c.is_dir()]
        except OSError:
            continue
        candidates: list[Path] = []
        for c in children:
            if sequence_folder_has_frames(c):
                candidates.append(c)
        if not candidates:
            continue
        # Prefer highest v### when available; otherwise newest mtime.
        with_ver: list[tuple[int, Path]] = []
        for c in candidates:
            v = _parse_workfile_version_from_folder_name(c.name)
            if v is not None:
                with_ver.append((v, c))
        if with_ver:
            with_ver.sort(key=lambda t: (t[0], _mtime_key_ns(t[1])), reverse=True)
            return with_ver[0][1]
        candidates.sort(key=_mtime_key_ns, reverse=True)
        return candidates[0]
    return None


def _natural_frame_sort_key(path: Path) -> tuple[str, int, str]:
    stem = path.stem
    m = re.search(r"(\d+)$", stem)
    if m:
        prefix = stem[: m.start()]
        try:
            num = int(m.group(1))
        except ValueError:
            num = 0
        return (prefix, num, path.suffix.lower())
    return (stem.lower(), 0, path.suffix.lower())


def list_sequence_frames(
    sequence_folder: Path,
    *,
    ignore_extensions: Iterable[str] | None = None,
    ignore_name_tokens: Iterable[str] | None = None,
) -> list[Path]:
    """Sorted list of image files directly under ``sequence_folder`` (non-recursive, v1)."""
    if not sequence_folder.is_dir():
        return []
    ign_ext = {str(s).strip().lower() for s in (ignore_extensions or ()) if str(s).strip()}
    ign_tok = {str(s).strip().casefold() for s in (ignore_name_tokens or ()) if str(s).strip()}
    out: list[Path] = []
    try:
        with os.scandir(sequence_folder) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                suf = Path(entry.name).suffix.lower()
                if suf not in _SEQUENCE_SUFFIXES:
                    continue
                if ign_ext and suf in ign_ext:
                    continue
                if ign_tok and path_matches_sequence_ignore_tokens(entry.name, ign_tok):
                    continue
                out.append(Path(entry.path))
    except OSError:
        return []
    out.sort(key=_natural_frame_sort_key)
    return out


def sequence_folder_has_frames(sequence_folder: Path) -> bool:
    """Fast existence check — does not sort or collect all paths."""
    if not sequence_folder.is_dir():
        return False
    try:
        with os.scandir(sequence_folder) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in _SEQUENCE_SUFFIXES:
                    return True
    except OSError:
        return False
    return False


def count_sequence_frames(sequence_folder: Path) -> int:
    """Frame count without sorting (for labels / picker)."""
    if not sequence_folder.is_dir():
        return 0
    n = 0
    try:
        with os.scandir(sequence_folder) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in _SEQUENCE_SUFFIXES:
                    n += 1
    except OSError:
        return 0
    return n


def representative_frame_path(frames: list[Path]) -> Path | None:
    if not frames:
        return None
    return frames[len(frames) // 2]


def quick_sequence_preview_frame(
    sequence_folder: Path,
    *,
    ignore_extensions: Iterable[str] | None = None,
    ignore_name_tokens: Iterable[str] | None = None,
) -> Path | None:
    """First suitable frame for thumbnail — no full list or sort."""
    if not sequence_folder.is_dir():
        return None
    ign_ext = {str(s).strip().lower() for s in (ignore_extensions or ()) if str(s).strip()}
    ign_tok = {str(s).strip().casefold() for s in (ignore_name_tokens or ()) if str(s).strip()}
    try:
        with os.scandir(sequence_folder) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                suf = Path(entry.name).suffix.lower()
                if suf not in _SEQUENCE_SUFFIXES:
                    continue
                if ign_ext and suf in ign_ext:
                    continue
                if ign_tok and path_matches_sequence_ignore_tokens(entry.name, ign_tok):
                    continue
                return Path(entry.path)
    except OSError:
        return None
    return None
