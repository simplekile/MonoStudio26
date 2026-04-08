"""
Resolve playblast / preview image sequences under department work/.

Convention (shots and assets):
  work/<render|preview|playblast|flipbook>/<work_file_stem_or_name>/  → image sequence files

Root folder names are matched case-insensitively. Priority when several exist:
``render`` → ``preview`` → ``playblast`` → ``flipbook``. If the work-named
subfolder is missing under the first root, the next root is tried (e.g. only
``flipbook/<name>/`` exists while ``render/`` is present but empty).

Within one canonical root name, newer mtime wins if duplicate directories exist.
"""

from __future__ import annotations

import re
from pathlib import Path

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
})


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


def list_sequence_frames(sequence_folder: Path) -> list[Path]:
    """Sorted list of image files directly under ``sequence_folder`` (non-recursive, v1)."""
    if not sequence_folder.is_dir():
        return []
    out: list[Path] = []
    try:
        for p in sequence_folder.iterdir():
            if not p.is_file():
                continue
            # Skip cryptomatte outputs (not meaningful as thumbnails / flipbook).
            try:
                if "cryptomatte" in p.name.casefold():
                    continue
            except Exception:
                pass
            suf = p.suffix.lower()
            if suf in _SEQUENCE_SUFFIXES:
                out.append(p)
    except OSError:
        return []
    out.sort(key=_natural_frame_sort_key)
    return out


def representative_frame_path(frames: list[Path]) -> Path | None:
    if not frames:
        return None
    return frames[len(frames) // 2]
