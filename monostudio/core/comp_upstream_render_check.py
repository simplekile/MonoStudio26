"""Check Fusion comp Loader paths against latest upstream render versions on disk."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from monostudio.core.comp_loader_io import (
    loader_paths_from_comp_text,
    loader_paths_from_loader_block,
    normalize_render_path_versions,
    parse_comp_global_range,
    replace_loader_path_in_comp_text,
    sync_loader_block_range,
    version_stems_in_render_path,
)
from monostudio.core.comp_saver_io import read_comp_text
from monostudio.core.fs_reader import work_file_prefix
from monostudio.core.sequence_preview import (
    _SEQUENCE_SUFFIXES,
    _parse_workfile_version_from_folder_name,
    review_name_matches_work_prefix,
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


def _loader_needs_path_update(path_str: str, base_prefix: str, latest_version: int) -> bool:
    versions = version_stems_in_render_path(path_str, base_prefix)
    if not versions:
        return False
    if len(versions) > 1:
        return True
    ref_ver = max(versions)
    if ref_ver != latest_version:
        return True
    return not Path(path_str).is_file()


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
    from monostudio.core.comp_loader_io import _iter_loader_blocks

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
        else:
            message = (
                f"Retarget {parsed.entity_name} → {expected_entity_name} "
                f"({parsed.base_prefix} → {target_prefix}); "
                f"no render frames found for {target_prefix} on disk."
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
        needs_path_update = any(
            _loader_needs_path_update(path_str, parsed.base_prefix, latest_ver)
            for _tool, path_str in entries
        )

        if needs_path_update:
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
    issues.sort(
        key=lambda i: (
            0 if i.status == UpstreamRenderStatus.WRONG_ENTITY else 1,
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
    entity_name: str | None = None,
    departments: tuple[str, ...] | None = None,
) -> Literal["updated", "unchanged", "failed"]:
    """Bump Loader paths for selected issues; optionally sync trim/range on pipeline Loaders."""
    path_issues = [
        i
        for i in (selected_issues or [])
        if i.status == UpstreamRenderStatus.STALE and i.latest_version
    ]
    wrong_entity_issues = [
        i
        for i in (selected_issues or [])
        if i.status == UpstreamRenderStatus.WRONG_ENTITY and i.expected_entity_name
    ]
    if not path_issues and not wrong_entity_issues and not sync_loader_range:
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
            if not _loader_needs_path_update(path_str, parsed.base_prefix, latest_ver):
                continue
            new_path = normalize_render_path_versions(path_str, parsed.base_prefix, latest_ver)
            if new_path != path_str:
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

    if sync_loader_range:
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
