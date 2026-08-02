"""Check Fusion comp Loader paths against latest upstream render versions on disk."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from monostudio.core.comp_loader_io import (
    _iter_loader_blocks,
    intersect_frame_ranges,
    loader_block_range_matches_global,
    loader_paths_from_comp_text,
    loader_paths_from_loader_block,
    normalize_render_path_versions,
    parse_comp_global_range,
    replace_comp_global_range,
    replace_loader_path_in_comp_text,
    sync_loader_block_range,
    version_stems_in_render_path,
)
from monostudio.core.comp_saver_io import read_comp_text
from monostudio.core.fs_reader import work_file_prefix
from monostudio.core.sequence_preview import (
    _SEQUENCE_SUFFIXES,
    _parse_workfile_version_from_folder_name,
    list_sequence_frames,
    review_name_matches_work_prefix,
    sequence_folder_frame_extent,
)

_RENDER_IN_PATH_RE = re.compile(r"[\\/]render[\\/]", re.IGNORECASE)


def _matches_department_filter(department: str, departments: tuple[str, ...] | None) -> bool:
    """When ``departments`` is None, every parsed pipeline render Loader is included."""
    if departments is None:
        return True
    return department.casefold() in {d.casefold() for d in departments}


class UpstreamRenderStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    MISSING_ON_DISK = "missing_on_disk"
    WRONG_ENTITY = "wrong_entity"
    RANGE_MISMATCH = "range_mismatch"
    FRAME_REF = "frame_ref"


@dataclass(frozen=True)
class ParsedPipelineRenderPath:
    loader_path: Path
    render_root: Path
    version_folder: str
    version: int
    base_prefix: str
    department: str
    entity_name: str


@dataclass(frozen=True)
class UpstreamRenderIssue:
    status: UpstreamRenderStatus
    base_prefix: str
    department: str
    entity_name: str
    comp_version: int
    latest_version: int | None
    latest_folder: Path | None
    sample_loader_path: str
    loader_count: int
    message: str
    expected_entity_name: str = ""
    loader_range_start: int | None = None
    loader_range_end: int | None = None
    comp_range_start: int | None = None
    comp_range_end: int | None = None
    referenced_frame: int | None = None
    repair_frame: int | None = None
    apply_summary: str = ""


def _base_prefix_from_version_folder(folder_name: str) -> str | None:
    v = _parse_workfile_version_from_folder_name(folder_name)
    if v is None:
        return None
    m = re.search(r"(.+)_v\d{3}", folder_name, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def _entity_and_department_from_prefix(prefix: str) -> tuple[str, str]:
    if "_" not in prefix:
        return prefix, ""
    entity, dept = prefix.rsplit("_", 1)
    return entity, dept


def _loader_needs_version_update(path_str: str, base_prefix: str, latest_version: int) -> bool:
    versions = version_stems_in_render_path(path_str, base_prefix)
    if not versions:
        return False
    if len(versions) > 1:
        return True
    return max(versions) != latest_version


def _loader_needs_frame_repair(
    path_str: str,
    parsed: ParsedPipelineRenderPath,
    *,
    latest_version: int,
    latest_folder: Path,
) -> bool:
    """True when version folder is current but the referenced frame file is missing."""
    if parsed.loader_path.is_file():
        return False
    versions = version_stems_in_render_path(path_str, parsed.base_prefix)
    if not versions or len(versions) > 1:
        return False
    if max(versions) != latest_version:
        return False
    return _render_folder_has_frames(latest_folder, parsed.base_prefix)


def _loader_needs_path_update(path_str: str, base_prefix: str, latest_version: int) -> bool:
    if _loader_needs_version_update(path_str, base_prefix, latest_version):
        return True
    parsed = parse_pipeline_render_loader_path(path_str)
    if parsed is None:
        return False
    folder = parsed.loader_path.parent
    latest = find_latest_render_version(parsed.render_root, base_prefix)
    if latest is None:
        return not Path(path_str).is_file()
    latest_ver, latest_folder = latest
    return _loader_needs_frame_repair(
        path_str,
        parsed,
        latest_version=latest_ver,
        latest_folder=latest_folder,
    )


def _frame_number_from_stem(stem: str) -> int | None:
    m = re.search(r"(\d+)$", stem)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _sequence_file_for_frame(
    folder: Path,
    *,
    base_prefix: str,
    frame: int,
) -> Path | None:
    prefix_cf = f"{base_prefix}_v".casefold()
    for path in list_sequence_frames(folder):
        if not path.stem.casefold().startswith(prefix_cf):
            continue
        num = _frame_number_from_stem(path.stem)
        if num == frame:
            return path
    return None


def repair_pipeline_loader_frame_path(
    path_str: str,
    parsed: ParsedPipelineRenderPath,
    *,
    render_folder: Path | None = None,
) -> str | None:
    """Point a loader path at an existing frame file in the same version folder."""
    folder = render_folder or parsed.loader_path.parent
    if not folder.is_dir():
        return None
    extent = sequence_folder_frame_extent(folder, base_prefix=parsed.base_prefix)
    if extent is None:
        return None
    d0, d1 = extent
    ref_frame = _frame_number_from_stem(parsed.loader_path.stem)
    target = d0 if ref_frame is None else max(d0, min(d1, ref_frame))
    replacement = _sequence_file_for_frame(folder, base_prefix=parsed.base_prefix, frame=target)
    if replacement is None:
        frames = [
            f
            for f in list_sequence_frames(folder)
            if f.stem.casefold().startswith(f"{parsed.base_prefix}_v".casefold())
        ]
        if not frames:
            return None
        replacement = frames[0]
    if replacement == parsed.loader_path:
        return None
    new_path = str(parsed.loader_path.parent / replacement.name)
    if "\\\\" in path_str:
        return new_path.replace("\\", "\\\\")
    if "/" in path_str and "\\" not in path_str.replace("\\\\", ""):
        return new_path.replace("\\", "/")
    return new_path


def _render_folder_has_frames(folder: Path, base_prefix: str) -> bool:
    """True when a version folder contains at least one pipeline sequence frame."""
    if not folder.is_dir():
        return False
    prefix_cf = f"{base_prefix}_v".casefold()
    try:
        children = list(folder.iterdir())
    except OSError:
        return False
    for child in children:
        if not child.is_file():
            continue
        if child.suffix.casefold() not in _SEQUENCE_SUFFIXES:
            continue
        if child.stem.casefold().startswith(prefix_cf):
            return True
    return False


def _sync_pipeline_loader_ranges(
    comp_text: str,
    global_start: int,
    global_end: int,
    *,
    entity_name: str | None = None,
    departments: tuple[str, ...] | None = None,
) -> str:
    deps = departments
    deps_cf = {d.casefold() for d in deps} if deps is not None else None
    entity_cf = entity_name.strip().casefold() if entity_name else None
    new_text = comp_text
    for _tool_name, block in _iter_loader_blocks(comp_text):
        matches = False
        for path_str in loader_paths_from_loader_block(block):
            parsed = parse_pipeline_render_loader_path(path_str)
            if parsed is None:
                continue
            if deps_cf is not None and parsed.department.casefold() not in deps_cf:
                continue
            if entity_cf and parsed.entity_name.casefold() != entity_cf:
                continue
            matches = True
            break
        if not matches:
            continue
        updated = sync_loader_block_range(block, global_start, global_end)
        if updated != block:
            new_text = new_text.replace(block, updated, 1)
    return new_text


def parse_pipeline_render_loader_path(path_str: str) -> ParsedPipelineRenderPath | None:
    """Parse Loader paths like .../work/render/sh009_lighting_v002/sh009_lighting_v002.0065.exr."""
    if not path_str or not _RENDER_IN_PATH_RE.search(path_str):
        return None
    p = Path(path_str)
    parts = [x for x in p.parts if x]
    render_idx = next(
        (i for i, part in enumerate(parts) if part.casefold() == "render"),
        None,
    )
    if render_idx is None or render_idx + 1 >= len(parts):
        return None
    version_folder = parts[render_idx + 1]
    version = _parse_workfile_version_from_folder_name(version_folder)
    base_prefix = _base_prefix_from_version_folder(version_folder)
    if version is None or not base_prefix:
        return None
    render_root = Path(*parts[: render_idx + 1])
    entity, dept = _entity_and_department_from_prefix(base_prefix)
    return ParsedPipelineRenderPath(
        loader_path=p,
        render_root=render_root,
        version_folder=version_folder,
        version=version,
        base_prefix=base_prefix,
        department=dept,
        entity_name=entity,
    )


def find_latest_render_version(render_root: Path, base_prefix: str) -> tuple[int, Path] | None:
    """Return (version, folder_path) for newest render folder that has sequence frames."""
    if not render_root.is_dir():
        return None
    best_ver: int | None = None
    best_path: Path | None = None
    try:
        children = list(render_root.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_dir():
            continue
        if not review_name_matches_work_prefix(
            child.name,
            base_prefix=base_prefix,
            exact_names=(),
        ):
            continue
        ver = _parse_workfile_version_from_folder_name(child.name)
        if ver is None:
            continue
        if not _render_folder_has_frames(child, base_prefix):
            continue
        if best_ver is None or ver > best_ver:
            best_ver = ver
            best_path = child
    if best_ver is None or best_path is None:
        return None
    return best_ver, best_path


def _target_base_prefix(expected_entity_name: str, department: str) -> str:
    return work_file_prefix(name=expected_entity_name, department=department)


def repoint_pipeline_render_loader_path(
    path_str: str,
    *,
    from_prefix: str,
    to_prefix: str,
    from_entity: str,
    to_entity: str,
    target_version: int | None = None,
) -> str:
    """Rewrite loader path from one pipeline entity/prefix to another."""
    out = path_str
    if from_prefix.casefold() != to_prefix.casefold():
        prefix_pat = re.compile(re.escape(from_prefix), re.IGNORECASE)
        out = prefix_pat.sub(to_prefix, out)
    if from_entity.casefold() != to_entity.casefold():
        entity_pat = re.compile(
            rf"([/\\]){re.escape(from_entity)}([/\\])",
            re.IGNORECASE,
        )
        out = entity_pat.sub(rf"\1{to_entity}\2", out)
    if target_version is not None:
        out = normalize_render_path_versions(out, to_prefix, target_version)
    return out


def _append_wrong_entity_issues(
    issues: list[UpstreamRenderIssue],
    grouped: dict[tuple[str, str], list[tuple[str, str]]],
    *,
    expected_entity_name: str,
) -> None:
    for (_prefix_key, _entity_key), entries in grouped.items():
        sample = entries[0][1]
        parsed = parse_pipeline_render_loader_path(sample)
        if parsed is None:
            continue
        target_prefix = _target_base_prefix(expected_entity_name, parsed.department)
        repointed = repoint_pipeline_render_loader_path(
            sample,
            from_prefix=parsed.base_prefix,
            to_prefix=target_prefix,
            from_entity=parsed.entity_name,
            to_entity=expected_entity_name,
        )
        parsed_target = parse_pipeline_render_loader_path(repointed)
        latest_ver: int | None = None
        latest_folder: Path | None = None
        if parsed_target is not None:
            latest = find_latest_render_version(parsed_target.render_root, target_prefix)
            if latest is not None:
                latest_ver, latest_folder = latest
        if latest_ver is not None:
            message = (
                f"Retarget {parsed.base_prefix} → {target_prefix} "
                f"(v{parsed.version:03d} → v{latest_ver:03d})."
            )
            apply_summary = (
                f"Apply: retarget loader paths from {parsed.base_prefix} "
                f"to {target_prefix} (v{latest_ver:03d})."
            )
        else:
            message = (
                f"Retarget {parsed.entity_name} → {expected_entity_name} "
                f"({parsed.base_prefix} → {target_prefix}); "
                f"no render frames found for {target_prefix} on disk."
            )
            apply_summary = (
                f"Apply: retarget loader paths from {parsed.base_prefix} to {target_prefix}."
            )
        issues.append(
            UpstreamRenderIssue(
                status=UpstreamRenderStatus.WRONG_ENTITY,
                base_prefix=parsed.base_prefix,
                department=parsed.department,
                entity_name=parsed.entity_name,
                expected_entity_name=expected_entity_name,
                comp_version=parsed.version,
                latest_version=latest_ver,
                latest_folder=latest_folder,
                sample_loader_path=sample,
                loader_count=len(entries),
                message=message,
                apply_summary=apply_summary,
            )
        )


def _parsed_for_disk_after_entity_retarget(
    path_str: str,
    parsed: ParsedPipelineRenderPath,
    expected_entity_name: str,
) -> ParsedPipelineRenderPath | None:
    """Resolve loader path to the comp shot entity before reading frames on disk."""
    if parsed.entity_name.casefold() == expected_entity_name.casefold():
        return parsed
    target_prefix = _target_base_prefix(expected_entity_name, parsed.department)
    repointed = repoint_pipeline_render_loader_path(
        path_str,
        from_prefix=parsed.base_prefix,
        to_prefix=target_prefix,
        from_entity=parsed.entity_name,
        to_entity=expected_entity_name,
    )
    parsed_target = parse_pipeline_render_loader_path(repointed)
    if parsed_target is None:
        return None
    latest = find_latest_render_version(parsed_target.render_root, target_prefix)
    if latest is not None:
        latest_ver, _folder = latest
        repointed = repoint_pipeline_render_loader_path(
            path_str,
            from_prefix=parsed.base_prefix,
            to_prefix=target_prefix,
            from_entity=parsed.entity_name,
            to_entity=expected_entity_name,
            target_version=latest_ver,
        )
        parsed_target = parse_pipeline_render_loader_path(repointed)
    return parsed_target


def _disk_extent_for_pipeline_render(
    parsed: ParsedPipelineRenderPath,
) -> tuple[tuple[int, int] | None, Path]:
    """Frame span on disk, preferring the latest render version folder for the prefix."""
    latest = find_latest_render_version(parsed.render_root, parsed.base_prefix)
    if latest is not None:
        _ver, latest_folder = latest
        extent = sequence_folder_frame_extent(latest_folder, base_prefix=parsed.base_prefix)
        if extent is not None:
            return extent, latest_folder
    referenced = parsed.loader_path.parent
    return sequence_folder_frame_extent(referenced, base_prefix=parsed.base_prefix), referenced


def collect_pipeline_loader_disk_extents(
    comp_text: str,
    *,
    entity_name: str | None = None,
    departments: tuple[str, ...] | None = None,
    retarget_wrong_entity: bool = False,
) -> list[tuple[int, int]]:
    """Disk min/max frame per pipeline render prefix (latest version folder on disk)."""
    entity_cf = entity_name.strip().casefold() if entity_name else None
    expected_entity = entity_name.strip() if entity_name else ""
    seen_prefixes: set[tuple[str, str]] = set()
    extents: list[tuple[int, int]] = []
    for _tool_name, block in _iter_loader_blocks(comp_text):
        paths = loader_paths_from_loader_block(block)
        parsed_any: ParsedPipelineRenderPath | None = None
        for path_str in paths:
            parsed = parse_pipeline_render_loader_path(path_str)
            if parsed is None:
                continue
            if not _matches_department_filter(parsed.department, departments):
                continue
            if entity_cf and parsed.entity_name.casefold() != entity_cf:
                if not retarget_wrong_entity or not expected_entity:
                    continue
                parsed = _parsed_for_disk_after_entity_retarget(
                    path_str,
                    parsed,
                    expected_entity,
                )
                if parsed is None:
                    continue
            parsed_any = parsed
            break
        if parsed_any is None:
            continue
        prefix_key = (
            str(parsed_any.render_root).casefold(),
            parsed_any.base_prefix.casefold(),
        )
        if prefix_key in seen_prefixes:
            continue
        seen_prefixes.add(prefix_key)
        extent, _folder = _disk_extent_for_pipeline_render(parsed_any)
        if extent is not None:
            extents.append(extent)
    return extents


def resolve_comp_range_from_disk(
    comp_text: str,
    *,
    entity_name: str | None = None,
    departments: tuple[str, ...] | None = None,
    retarget_wrong_entity: bool = False,
) -> tuple[int, int] | None:
    """Best comp GlobalRange that fits all referenced pipeline render folders on disk."""
    return intersect_frame_ranges(
        collect_pipeline_loader_disk_extents(
            comp_text,
            entity_name=entity_name,
            departments=departments,
            retarget_wrong_entity=retarget_wrong_entity,
        )
    )


def _global_range_covered_by_disk(
    global_start: int,
    global_end: int,
    disk_start: int,
    disk_end: int,
) -> bool:
    return disk_start <= global_start and disk_end >= global_end


def _comp_range_matches_disk(
    global_start: int,
    global_end: int,
    disk_start: int,
    disk_end: int,
) -> bool:
    return global_start == disk_start and global_end == disk_end


def _inclusive_frame_count(range_start: int, range_end: int) -> int:
    return max(0, range_end - range_start + 1)


def has_loader_trim_mismatch_with_global(
    comp_path: Path,
    *,
    entity_name: str | None = None,
    departments: tuple[str, ...] | None = None,
) -> bool:
    """True when a pipeline Loader clip trim differs from comp GlobalRange."""
    try:
        text = read_comp_text(comp_path)
    except OSError:
        return False
    global_range = parse_comp_global_range(text)
    if global_range is None:
        return False
    g0, g1 = global_range
    entity_cf = entity_name.strip().casefold() if entity_name else None
    for _tool_name, block in _iter_loader_blocks(text):
        paths = loader_paths_from_loader_block(block)
        matches = False
        for path_str in paths:
            parsed = parse_pipeline_render_loader_path(path_str)
            if parsed is None:
                continue
            if not _matches_department_filter(parsed.department, departments):
                continue
            if entity_cf and parsed.entity_name.casefold() != entity_cf:
                continue
            matches = True
            break
        if not matches:
            continue
        if not loader_block_range_matches_global(block, g0, g1):
            return True
    return False


def _append_range_mismatch_issues(
    issues: list[UpstreamRenderIssue],
    comp_text: str,
    *,
    entity_name: str | None,
    departments: tuple[str, ...] | None,
) -> None:
    global_range = parse_comp_global_range(comp_text)
    if global_range is None:
        return
    g0, g1 = global_range
    entity_cf = entity_name.strip().casefold() if entity_name else None
    expected_entity = entity_name.strip() if entity_name else ""
    grouped: dict[tuple[str, str], list[tuple[str, str, ParsedPipelineRenderPath, bool]]] = {}
    for tool_name, block in _iter_loader_blocks(comp_text):
        paths = loader_paths_from_loader_block(block)
        parsed_any: ParsedPipelineRenderPath | None = None
        sample_path = ""
        after_retarget = False
        for path_str in paths:
            parsed = parse_pipeline_render_loader_path(path_str)
            if parsed is None:
                continue
            if not _matches_department_filter(parsed.department, departments):
                continue
            sample_path = path_str
            if entity_cf and parsed.entity_name.casefold() != entity_cf:
                if not expected_entity:
                    continue
                parsed_disk = _parsed_for_disk_after_entity_retarget(
                    path_str,
                    parsed,
                    expected_entity,
                )
                if parsed_disk is None:
                    continue
                parsed_any = parsed_disk
                after_retarget = True
            else:
                parsed_any = parsed
            break
        if parsed_any is None:
            continue
        key = (
            str(parsed_any.render_root).casefold(),
            parsed_any.base_prefix.casefold(),
        )
        grouped.setdefault(key, []).append((tool_name, sample_path, parsed_any, after_retarget))

    for (_root_key, _prefix_key), entries in grouped.items():
        parsed = entries[0][2]
        after_retarget = entries[0][3]
        disk_extent, disk_folder = _disk_extent_for_pipeline_render(parsed)
        if disk_extent is None:
            continue
        d0, d1 = disk_extent
        if _comp_range_matches_disk(g0, g1, d0, d1):
            continue
        comp_frames = _inclusive_frame_count(g0, g1)
        disk_frames = _inclusive_frame_count(d0, d1)
        referenced_folder = parsed.loader_path.parent
        retarget_note = (
            f" After retarget to {parsed.base_prefix}."
            if after_retarget
            else ""
        )
        if referenced_folder != disk_folder:
            message = (
                f"Latest render {disk_folder.name} on disk is {d0}–{d1} ({disk_frames} frames); "
                f"comp GlobalRange is {g0}–{g1} ({comp_frames} frames). "
                f"Loaders reference {referenced_folder.name}.{retarget_note}"
            )
        elif not _global_range_covered_by_disk(g0, g1, d0, d1):
            message = (
                f"Comp GlobalRange {g0}–{g1} ({comp_frames} frames) is not fully covered by "
                f"render on disk {d0}–{d1} ({disk_frames} frames) in {disk_folder.name}."
                f"{retarget_note}"
            )
        else:
            message = (
                f"Render on disk {d0}–{d1} ({disk_frames} frames) is wider than comp "
                f"GlobalRange {g0}–{g1} ({comp_frames} frames) in {disk_folder.name}."
                f"{retarget_note}"
            )
        issues.append(
            UpstreamRenderIssue(
                status=UpstreamRenderStatus.RANGE_MISMATCH,
                base_prefix=parsed.base_prefix,
                department=parsed.department,
                entity_name=parsed.entity_name,
                comp_version=0,
                latest_version=None,
                latest_folder=disk_folder,
                sample_loader_path=entries[0][1],
                loader_count=len(entries),
                message=message,
                expected_entity_name=expected_entity if after_retarget else "",
                loader_range_start=d0,
                loader_range_end=d1,
                comp_range_start=g0,
                comp_range_end=g1,
            )
        )


def audit_comp_upstream_renders(
    comp_path: Path,
    *,
    entity_name: str | None = None,
    departments: tuple[str, ...] | None = None,
) -> list[UpstreamRenderIssue]:
    """
    Find Loader paths under work/render that are not on the latest version folder.

    Any Loader whose path matches the pipeline version-folder pattern
    (``.../work/render/<prefix>_v###/...``) is checked. Pass ``departments`` only
    to restrict to specific department suffixes (e.g. lighting, fx).
    """
    try:
        text = read_comp_text(comp_path)
    except OSError:
        return []

    grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
    wrong_entity_grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
    expected_cf = entity_name.strip().casefold() if entity_name else ""
    for tool_name, path_str in loader_paths_from_comp_text(text):
        parsed = parse_pipeline_render_loader_path(path_str)
        if parsed is None:
            continue
        if not _matches_department_filter(parsed.department, departments):
            continue
        if expected_cf and parsed.entity_name.casefold() != expected_cf:
            wrong_key = (
                parsed.base_prefix.casefold(),
                parsed.entity_name.casefold(),
            )
            wrong_entity_grouped.setdefault(wrong_key, []).append((tool_name, path_str))
            continue
        key = (
            str(parsed.render_root).casefold(),
            parsed.base_prefix.casefold(),
        )
        grouped.setdefault(key, []).append((tool_name, path_str))

    issues: list[UpstreamRenderIssue] = []
    if expected_cf:
        _append_wrong_entity_issues(
            issues,
            wrong_entity_grouped,
            expected_entity_name=entity_name.strip(),
        )
    for (_root_key, _prefix_key), entries in grouped.items():
        sample = entries[0][1]
        parsed = parse_pipeline_render_loader_path(sample)
        if parsed is None:
            continue
        latest = find_latest_render_version(parsed.render_root, parsed.base_prefix)
        loader_count = len(entries)
        path_versions: set[int] = set()
        missing_count = 0
        mismatch_count = 0
        for _tool, path_str in entries:
            path_versions |= version_stems_in_render_path(path_str, parsed.base_prefix)
            if len(version_stems_in_render_path(path_str, parsed.base_prefix)) > 1:
                mismatch_count += 1
            pp = parse_pipeline_render_loader_path(path_str)
            if pp is not None and not pp.loader_path.is_file():
                missing_count += 1

        if latest is None:
            if missing_count > 0:
                comp_version = min(path_versions) if path_versions else 0
                issues.append(
                    UpstreamRenderIssue(
                        status=UpstreamRenderStatus.MISSING_ON_DISK,
                        base_prefix=parsed.base_prefix,
                        department=parsed.department,
                        entity_name=parsed.entity_name,
                        comp_version=comp_version,
                        latest_version=None,
                        latest_folder=None,
                        sample_loader_path=sample,
                        loader_count=loader_count,
                        message="Referenced render file is missing on disk.",
                    )
                )
            continue

        latest_ver, latest_folder = latest
        stale_versions = sorted(
            {
                v
                for _tool, path_str in entries
                for v in version_stems_in_render_path(path_str, parsed.base_prefix)
                if v != latest_ver
            }
        )
        needs_version_update = any(
            _loader_needs_version_update(path_str, parsed.base_prefix, latest_ver)
            for _tool, path_str in entries
        )
        needs_frame_repair = not needs_version_update and any(
            _loader_needs_frame_repair(
                path_str,
                pp,
                latest_version=latest_ver,
                latest_folder=latest_folder,
            )
            for _tool, path_str in entries
            for pp in [parse_pipeline_render_loader_path(path_str)]
            if pp is not None
        )

        if needs_version_update:
            if stale_versions:
                comp_version = stale_versions[-1]
            else:
                comp_version = max(path_versions) if path_versions else 0
            msg = (
                f"Comp references {parsed.base_prefix}_v{comp_version:03d}; "
                f"latest on disk is v{latest_ver:03d}."
            )
            if mismatch_count > 0:
                msg = (
                    f"Comp has mismatched render path versions for {parsed.base_prefix}; "
                    f"latest on disk is v{latest_ver:03d}."
                )
            elif comp_version > latest_ver:
                msg = (
                    f"Comp references {parsed.base_prefix}_v{comp_version:03d} "
                    f"(not available on disk); latest is v{latest_ver:03d}."
                )
            if missing_count > 0:
                msg = (
                    f"Comp references {parsed.base_prefix} render(s) "
                    f"({missing_count} missing on disk); latest on disk is v{latest_ver:03d}."
                )
            apply_summary = (
                f"Apply: update loader paths to {parsed.base_prefix}_v{latest_ver:03d}."
            )
            issues.append(
                UpstreamRenderIssue(
                    status=UpstreamRenderStatus.STALE,
                    base_prefix=parsed.base_prefix,
                    department=parsed.department,
                    entity_name=parsed.entity_name,
                    comp_version=comp_version,
                    latest_version=latest_ver,
                    latest_folder=latest_folder,
                    sample_loader_path=sample,
                    loader_count=loader_count,
                    message=msg,
                    apply_summary=apply_summary,
                )
            )
            continue

        if needs_frame_repair:
            sample_parsed = parse_pipeline_render_loader_path(sample)
            ref_frame = (
                _frame_number_from_stem(sample_parsed.loader_path.stem)
                if sample_parsed is not None
                else None
            )
            disk_extent = sequence_folder_frame_extent(
                latest_folder,
                base_prefix=parsed.base_prefix,
            )
            repair_frame: int | None = None
            if sample_parsed is not None:
                repair_path = repair_pipeline_loader_frame_path(
                    sample,
                    sample_parsed,
                    render_folder=latest_folder,
                )
                if repair_path is not None:
                    repair_frame = _frame_number_from_stem(
                        Path(repair_path.replace("\\\\", "\\")).stem
                    )
            d0, d1 = disk_extent if disk_extent is not None else (None, None)
            apply_summary = ""
            if ref_frame is not None and repair_frame is not None:
                extent_note = f" (disk has frames {d0}–{d1})" if d0 is not None and d1 is not None else ""
                apply_summary = (
                    f"Apply: keep {parsed.base_prefix}_v{latest_ver:03d}, "
                    f"retarget loader clip paths from frame {ref_frame} → {repair_frame}"
                    f"{extent_note}."
                )
            issues.append(
                UpstreamRenderIssue(
                    status=UpstreamRenderStatus.FRAME_REF,
                    base_prefix=parsed.base_prefix,
                    department=parsed.department,
                    entity_name=parsed.entity_name,
                    comp_version=latest_ver,
                    latest_version=latest_ver,
                    latest_folder=latest_folder,
                    sample_loader_path=sample,
                    loader_count=loader_count,
                    message=(
                        f"{missing_count} loader(s) reference a frame file that is not on disk "
                        f"in {parsed.base_prefix}_v{latest_ver:03d} — common when a comp was copied "
                        f"from another shot."
                    ),
                    referenced_frame=ref_frame,
                    repair_frame=repair_frame,
                    loader_range_start=d0,
                    loader_range_end=d1,
                    apply_summary=apply_summary,
                )
            )
            continue

        if missing_count > 0:
            comp_version = min(path_versions) if path_versions else 0
            issues.append(
                UpstreamRenderIssue(
                    status=UpstreamRenderStatus.MISSING_ON_DISK,
                    base_prefix=parsed.base_prefix,
                    department=parsed.department,
                    entity_name=parsed.entity_name,
                    comp_version=comp_version,
                    latest_version=latest_ver,
                    latest_folder=latest_folder,
                    sample_loader_path=sample,
                    loader_count=loader_count,
                    message="Referenced render file is missing on disk.",
                )
            )
    _append_range_mismatch_issues(
        issues,
        text,
        entity_name=entity_name,
        departments=departments,
    )
    issues.sort(
        key=lambda i: (
            {
                UpstreamRenderStatus.WRONG_ENTITY: 0,
                UpstreamRenderStatus.RANGE_MISMATCH: 1,
                UpstreamRenderStatus.STALE: 2,
                UpstreamRenderStatus.FRAME_REF: 3,
                UpstreamRenderStatus.MISSING_ON_DISK: 4,
            }.get(i.status, 9),
            i.department,
            i.comp_version,
        )
    )
    return issues


def apply_upstream_render_updates(
    comp_path: Path,
    issues: list[UpstreamRenderIssue],
    *,
    selected_issues: list[UpstreamRenderIssue] | None = None,
    sync_loader_range: bool = False,
    clamp_comp_range_to_disk: bool = False,
    entity_name: str | None = None,
    departments: tuple[str, ...] | None = None,
) -> Literal["updated", "unchanged", "failed"]:
    """Bump Loader paths for selected issues; optionally sync trim/range on pipeline Loaders."""
    path_issues = [
        i
        for i in (selected_issues or [])
        if i.status == UpstreamRenderStatus.STALE and i.latest_version
    ]
    frame_ref_issues = [
        i
        for i in (selected_issues or [])
        if i.status == UpstreamRenderStatus.FRAME_REF and i.latest_folder
    ]
    wrong_entity_issues = [
        i
        for i in (selected_issues or [])
        if i.status == UpstreamRenderStatus.WRONG_ENTITY and i.expected_entity_name
    ]
    if (
        not path_issues
        and not frame_ref_issues
        and not wrong_entity_issues
        and not sync_loader_range
        and not clamp_comp_range_to_disk
    ):
        return "unchanged"
    try:
        text = read_comp_text(comp_path)
    except OSError:
        return "failed"
    new_text = text
    prefixes_to_latest = {
        issue.base_prefix: issue.latest_version
        for issue in path_issues
        if issue.latest_version is not None
    }
    if prefixes_to_latest:
        seen_paths: set[str] = set()
        for _tool, path_str in loader_paths_from_comp_text(new_text):
            key = path_str.casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            parsed = parse_pipeline_render_loader_path(path_str)
            if parsed is None:
                continue
            latest_ver = prefixes_to_latest.get(parsed.base_prefix)
            if latest_ver is None:
                continue
            if not _loader_needs_version_update(path_str, parsed.base_prefix, latest_ver):
                continue
            new_path = normalize_render_path_versions(path_str, parsed.base_prefix, latest_ver)
            if new_path != path_str:
                new_text = replace_loader_path_in_comp_text(new_text, path_str, new_path)

    if frame_ref_issues:
        frame_prefixes = {issue.base_prefix: issue for issue in frame_ref_issues}
        seen_paths: set[str] = set()
        for _tool, path_str in loader_paths_from_comp_text(new_text):
            key = path_str.casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            parsed = parse_pipeline_render_loader_path(path_str)
            if parsed is None:
                continue
            issue = frame_prefixes.get(parsed.base_prefix)
            if issue is None or issue.latest_folder is None:
                continue
            if not _loader_needs_frame_repair(
                path_str,
                parsed,
                latest_version=issue.latest_version or parsed.version,
                latest_folder=issue.latest_folder,
            ):
                continue
            new_path = repair_pipeline_loader_frame_path(
                path_str,
                parsed,
                render_folder=issue.latest_folder,
            )
            if new_path and new_path != path_str:
                new_text = replace_loader_path_in_comp_text(new_text, path_str, new_path)

    if wrong_entity_issues:
        wrong_keys = {
            (i.base_prefix.casefold(), i.entity_name.casefold()): i
            for i in wrong_entity_issues
        }
        seen_paths: set[str] = set()
        for _tool, path_str in loader_paths_from_comp_text(new_text):
            key = path_str.casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            parsed = parse_pipeline_render_loader_path(path_str)
            if parsed is None:
                continue
            issue = wrong_keys.get(
                (parsed.base_prefix.casefold(), parsed.entity_name.casefold())
            )
            if issue is None:
                continue
            target_prefix = _target_base_prefix(
                issue.expected_entity_name,
                parsed.department,
            )
            new_path = repoint_pipeline_render_loader_path(
                path_str,
                from_prefix=parsed.base_prefix,
                to_prefix=target_prefix,
                from_entity=parsed.entity_name,
                to_entity=issue.expected_entity_name,
                target_version=issue.latest_version,
            )
            if new_path != path_str:
                new_text = replace_loader_path_in_comp_text(new_text, path_str, new_path)

    if clamp_comp_range_to_disk:
        disk_range = resolve_comp_range_from_disk(
            new_text,
            entity_name=entity_name,
            departments=departments,
            retarget_wrong_entity=bool(wrong_entity_issues),
        )
        if disk_range is not None:
            d0, d1 = disk_range
            new_text = replace_comp_global_range(new_text, d0, d1)
            new_text = _sync_pipeline_loader_ranges(
                new_text,
                d0,
                d1,
                entity_name=entity_name,
                departments=departments,
            )
    elif sync_loader_range:
        global_range = parse_comp_global_range(new_text)
        if global_range is not None:
            g0, g1 = global_range
            new_text = _sync_pipeline_loader_ranges(
                new_text,
                g0,
                g1,
                entity_name=entity_name,
                departments=departments,
            )

    if new_text == text:
        return "unchanged"
    try:
        comp_path.write_text(new_text, encoding="utf-8")
    except OSError:
        return "failed"
    return "updated"


def expected_upstream_prefix(entity_name: str, department: str) -> str:
    return work_file_prefix(name=entity_name, department=department)
