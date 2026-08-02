"""Extract Loader clip paths from Fusion .comp files."""

from __future__ import annotations

import re
from pathlib import Path

from monostudio.core.comp_saver_io import (
    _LEGACY_FILENAME_RE,
    _extract_braced_block,
    _tools_section_bounds,
    _unescape_comp_path,
    read_comp_text,
    trim_comp_text_to_valid_composition,
)

_LOADER_ASSIGN_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*Loader\s*\{",
    re.IGNORECASE,
)
_GLOBAL_RANGE_RE = re.compile(r"GlobalRange\s*=\s*\{\s*(\d+)\s*,\s*(\d+)\s*\}")
_RENDER_RANGE_RE = re.compile(r"RenderRange\s*=\s*\{\s*(\d+)\s*,\s*(\d+)\s*\}")
_TRIM_IN_RE = re.compile(r"TrimIn\s*=\s*(-?\d+)", re.IGNORECASE)
_TRIM_OUT_RE = re.compile(r"TrimOut\s*=\s*(-?\d+)", re.IGNORECASE)
_GLOBAL_START_RE = re.compile(r"GlobalStart\s*=\s*(-?\d+)", re.IGNORECASE)
_GLOBAL_END_RE = re.compile(r"GlobalEnd\s*=\s*(-?\d+)", re.IGNORECASE)


def _iter_loader_blocks(comp_text: str) -> list[tuple[str, str]]:
    def _scan(section: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for m in _LOADER_ASSIGN_RE.finditer(section):
            name = m.group("name")
            brace_idx = section.find("{", m.end() - 1)
            if brace_idx < 0:
                continue
            block, _end = _extract_braced_block(section, brace_idx)
            if block:
                out.append((name, block))
        return out

    bounds = _tools_section_bounds(comp_text)
    if bounds is not None:
        found = _scan(comp_text[bounds[0] : bounds[1]])
        if found:
            return found
    return _scan(comp_text)


def loader_paths_from_comp_text(comp_text: str) -> list[tuple[str, str]]:
    """Return (loader_tool_name, absolute_path) for every Loader clip filename."""
    out: list[tuple[str, str]] = []
    for tool_name, block in _iter_loader_blocks(comp_text):
        for path in loader_paths_from_loader_block(block):
            out.append((tool_name, path))
    return out


def loader_paths_from_loader_block(block: str) -> list[str]:
    out: list[str] = []
    for m in _LEGACY_FILENAME_RE.finditer(block):
        raw = _unescape_comp_path(m.group(1))
        if raw and raw.strip():
            out.append(raw.strip())
    return out


def unique_loader_paths_from_comp(comp_path: Path) -> list[str]:
    try:
        text = read_comp_text(comp_path)
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for _tool, path in loader_paths_from_comp_text(text):
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def version_stems_in_render_path(path_str: str, base_prefix: str) -> set[int]:
    """All v### version numbers for ``base_prefix`` appearing anywhere in a render path."""
    if not path_str or not base_prefix:
        return set()
    pat = re.compile(re.escape(base_prefix) + r"_v(\d{3})", re.IGNORECASE)
    return {int(m.group(1)) for m in pat.finditer(path_str)}


def normalize_render_path_versions(path_str: str, base_prefix: str, target_version: int) -> str:
    """Rewrite every ``base_prefix_v###`` segment in a path to ``base_prefix_vNNN``."""
    target_stem = f"{base_prefix}_v{target_version:03d}"
    pat = re.compile(re.escape(base_prefix) + r"_v\d{3}", re.IGNORECASE)
    return pat.sub(target_stem, path_str)


def _path_replace_variants(path: str) -> set[str]:
    variants = {
        path,
        path.replace("\\", "\\\\"),
        path.replace("/", "\\"),
        path.replace("\\", "/"),
    }
    return {v for v in variants if v}


def replace_loader_path_in_comp_text(comp_text: str, old_path: str, new_path: str) -> str:
    """Replace one Loader clip path (handles escaped backslashes)."""
    if not old_path or old_path == new_path:
        return comp_text
    out = comp_text
    for old in _path_replace_variants(old_path):
        new = new_path
        if "\\\\" in old:
            new = new_path.replace("\\", "\\\\")
        elif "/" in old and "\\" not in old:
            new = new_path.replace("\\", "/")
        if old in out:
            out = out.replace(old, new)
    return out


def parse_comp_global_range(comp_text: str) -> tuple[int, int] | None:
    m = _GLOBAL_RANGE_RE.search(comp_text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def replace_comp_global_range(comp_text: str, start: int, end: int) -> str:
    """Update composition GlobalRange (and RenderRange when present)."""
    out = _GLOBAL_RANGE_RE.sub(f"GlobalRange = {{ {start}, {end} }}", comp_text, count=1)
    if _RENDER_RANGE_RE.search(out):
        out = _RENDER_RANGE_RE.sub(f"RenderRange = {{ {start}, {end} }}", out, count=1)
    return out


def intersect_frame_ranges(ranges: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Intersection of inclusive frame ranges; None when disjoint or empty."""
    if not ranges:
        return None
    start = max(r[0] for r in ranges)
    end = min(r[1] for r in ranges)
    if start > end:
        return None
    return start, end


def parse_loader_block_clip_range(block: str) -> tuple[int, int] | None:
    """Return (start, end) trim range from the first Clip in a Loader block."""
    trim_in = _TRIM_IN_RE.search(block)
    trim_out = _TRIM_OUT_RE.search(block)
    if trim_in and trim_out:
        return int(trim_in.group(1)), int(trim_out.group(1))
    g0 = _GLOBAL_START_RE.search(block)
    g1 = _GLOBAL_END_RE.search(block)
    if g0 and g1:
        return int(g0.group(1)), int(g1.group(1))
    return None


def loader_block_range_matches_global(
    block: str,
    global_start: int,
    global_end: int,
) -> bool:
    clip_range = parse_loader_block_clip_range(block)
    if clip_range is None:
        return True
    start, end = clip_range
    return start == global_start and end == global_end


def sync_loader_block_range(block: str, global_start: int, global_end: int) -> str:
    length = max(1, global_end - global_start + 1)
    values = {
        "trimin": global_start,
        "trimout": global_end,
        "length": length,
        "globalstart": global_start,
        "globalend": global_end,
    }
    out = block
    for field, value in values.items():
        pat = re.compile(rf"({field}\s*=\s*)\d+", re.IGNORECASE)
        if pat.search(out):
            out = pat.sub(rf"\g<1>{value}", out, count=1)
    manual_pat = re.compile(r"LengthSetManually\s*=\s*true", re.IGNORECASE)
    if manual_pat.search(out):
        out = manual_pat.sub("LengthSetManually = false", out, count=1)
    return out


def sync_loader_ranges_for_stems(
    comp_text: str,
    stems: list[str],
    global_start: int,
    global_end: int,
) -> str:
    """Update Clip trim/length on Loader blocks whose path contains one of ``stems``."""
    if not stems:
        return comp_text
    stems_cf = [s.casefold() for s in stems if s]
    new_text = comp_text
    for _tool_name, block in _iter_loader_blocks(comp_text):
        if not any(s in block.casefold() for s in stems_cf):
            continue
        updated = sync_loader_block_range(block, global_start, global_end)
        if updated != block:
            new_text = new_text.replace(block, updated, 1)
    return new_text


def replace_path_prefix_in_comp_text(comp_text: str, old_prefix: str, new_prefix: str) -> str:
    """Replace escaped/unescaped path prefix occurrences (for bulk loader updates)."""
    if not old_prefix or old_prefix == new_prefix:
        return comp_text
    variants = {
        old_prefix,
        old_prefix.replace("\\", "\\\\"),
        old_prefix.replace("/", "\\"),
        old_prefix.replace("\\", "/"),
    }
    new_variants = {
        old_prefix: new_prefix,
        old_prefix.replace("\\", "\\\\"): new_prefix.replace("\\", "\\\\"),
        old_prefix.replace("/", "\\"): new_prefix.replace("\\", "\\\\"),
        old_prefix.replace("\\", "/"): new_prefix.replace("/", "\\"),
    }
    out = comp_text
    for old, new in new_variants.items():
        if old in out:
            out = out.replace(old, new)
    return out
