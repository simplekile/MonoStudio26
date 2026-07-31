"""Comp department render / Fusion Saver path conventions (MONOS pipeline)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from monostudio.core.fs_reader import _parse_workfile_version, list_work_file_versions, work_file_prefix

MANAGED_SAVER_NODE_NAME = "MONOS_Output"
DEFAULT_FUSION_PATH_MAP = "Comp"
DEFAULT_FRAME_PATTERN = "####"
DEFAULT_OUTPUT_EXT = ".exr"

# Fusion Saver Clip paths use a zero-padded frame (e.g. .0000.exr), not literal ####.
_FRAME_IN_SAVER_FILENAME_RE = re.compile(
    r"(\.)((?:#+)|(?:\d+))(\.(?:exr|png|jpe?g|tiff?|dpx|mov|mxf))$",
    re.IGNORECASE,
)


def fusion_frame_token_for_write(frame_pattern: str = DEFAULT_FRAME_PATTERN) -> str:
    """Map pipeline frame pattern (####) to Fusion Saver filename token (0000)."""
    pat = (frame_pattern or DEFAULT_FRAME_PATTERN).strip()
    if pat and set(pat) == {"#"}:
        return "0" * len(pat)
    if pat.isdigit():
        return pat.zfill(4) if len(pat) < 4 else pat
    return "0000"


@dataclass(frozen=True)
class CompSaverSpec:
    """Expected managed Saver output for one comp work file."""

    prefix: str
    stem: str
    work_version: int
    render_dir_relative: str
    render_dir_absolute: Path
    saver_path_fusion: str
    saver_node_name: str = MANAGED_SAVER_NODE_NAME
    fusion_path_map: str = DEFAULT_FUSION_PATH_MAP
    frame_pattern: str = DEFAULT_FRAME_PATTERN
    output_ext: str = DEFAULT_OUTPUT_EXT

    def as_context_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["render_dir_absolute"] = str(self.render_dir_absolute)
        return data


def fusion_saver_path(
    *,
    fusion_path_map: str,
    render_dir_relative: str,
    stem: str,
    frame_pattern: str = DEFAULT_FRAME_PATTERN,
    output_ext: str = DEFAULT_OUTPUT_EXT,
) -> str:
    """Build Fusion-style saver path using a mapped drive (e.g. Comp:)."""
    root = (fusion_path_map or DEFAULT_FUSION_PATH_MAP).strip().rstrip(":")
    rel = (render_dir_relative or "").replace("/", "\\").strip("\\")
    ext = output_ext if output_ext.startswith(".") else f".{output_ext}"
    frame_token = fusion_frame_token_for_write(frame_pattern)
    return f"{root}:\\{rel}\\{stem}.{frame_token}{ext}"


def _normalize_saver_path(path: str) -> str:
    s = (path or "").strip().replace("/", "\\")
    while "\\\\" in s:
        s = s.replace("\\\\", "\\")
    return s.casefold()


def _saver_path_signature(path: str) -> str:
    """Canonical form for comparing Saver paths (#### vs 0000 vs other frame digits)."""
    norm = _normalize_saver_path(path)
    return _FRAME_IN_SAVER_FILENAME_RE.sub(r".<frame>\3", norm)


def saver_paths_match(expected: str, actual: str | None) -> bool:
    """True when Fusion saver paths are equivalent (case / slash / frame token insensitive)."""
    if not actual or not str(actual).strip():
        return False
    return _saver_path_signature(expected) == _saver_path_signature(actual)


def parse_work_version_from_comp_path(work_file: Path, *, prefix: str, ext: str) -> int:
    """Parse v### from comp work filename; default 1 when missing."""
    name = work_file.name
    if not name.endswith(ext):
        parsed = _parse_workfile_version(name, prefix, ext)
        return parsed if parsed is not None else 1
    parsed = _parse_workfile_version(name, prefix, ext)
    return parsed if parsed is not None else 1


def build_comp_saver_spec(
    *,
    entity_name: str,
    department: str,
    work_file: Path,
    work_path: Path,
    fusion_path_map: str = DEFAULT_FUSION_PATH_MAP,
    frame_pattern: str = DEFAULT_FRAME_PATTERN,
    output_ext: str = DEFAULT_OUTPUT_EXT,
) -> CompSaverSpec:
    """Derive expected managed Saver output from a comp work file path."""
    prefix = work_file_prefix(name=entity_name, department=department)
    ext = work_file.suffix or ".comp"
    version = parse_work_version_from_comp_path(work_file, prefix=prefix, ext=ext)
    stem = f"{prefix}_v{version:03d}"
    render_dir_relative = f"render/{stem}"
    render_dir_absolute = (work_path / "render" / stem).resolve()
    saver_path = fusion_saver_path(
        fusion_path_map=fusion_path_map,
        render_dir_relative=render_dir_relative.replace("/", "\\"),
        stem=stem,
        frame_pattern=frame_pattern,
        output_ext=output_ext,
    )
    return CompSaverSpec(
        prefix=prefix,
        stem=stem,
        work_version=version,
        render_dir_relative=render_dir_relative,
        render_dir_absolute=render_dir_absolute,
        saver_path_fusion=saver_path,
        fusion_path_map=fusion_path_map,
        frame_pattern=frame_pattern,
        output_ext=output_ext,
    )


_WORK_STEM_RE = re.compile(r"^(.+)_v(\d{3})$", re.IGNORECASE)


def try_build_comp_saver_spec_from_work_file(work_file: Path, work_path: Path) -> CompSaverSpec | None:
    """Best-effort spec when only the comp path is known (batch / CLI)."""
    stem = work_file.stem
    m = _WORK_STEM_RE.match(stem)
    if not m:
        return None
    base, ver_s = m.group(1), m.group(2)
    if "_" not in base:
        return None
    entity_name, department = base.rsplit("_", 1)
    if not entity_name or not department:
        return None
    try:
        version = int(ver_s)
    except ValueError:
        return None
    prefix = base
    render_dir_relative = f"render/{stem}"
    render_dir_absolute = (work_path / "render" / stem).resolve()
    saver_path = fusion_saver_path(
        fusion_path_map=DEFAULT_FUSION_PATH_MAP,
        render_dir_relative=render_dir_relative.replace("/", "\\"),
        stem=stem,
    )
    return CompSaverSpec(
        prefix=prefix,
        stem=stem,
        work_version=version,
        render_dir_relative=render_dir_relative,
        render_dir_absolute=render_dir_absolute,
        saver_path_fusion=saver_path,
    )


def resolve_next_comp_work_path(
    current: Path,
    *,
    prefix: str,
    dcc_id: str = "fusion",
) -> Path:
    """Next unused work-file version path in the same folder as ``current``."""
    work_path = current.parent
    ext = current.suffix or ".comp"
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    try:
        from monostudio.core.dcc_registry import get_default_dcc_registry

        reg = get_default_dcc_registry()
        versions = list_work_file_versions(work_path, prefix, dcc_id, reg)
        max_ver = max((v for v, _p in versions if isinstance(v, int)), default=0)
    except Exception:
        max_ver = 0
    next_ver = max_ver + 1 if max_ver >= 1 else 1
    safe_prefix = (prefix or "").strip() or "unnamed"
    return work_path / f"{safe_prefix}_v{next_ver:03d}{ext}"


def rebuild_comp_saver_spec(work_file: Path, base: CompSaverSpec, *, entity_name: str | None) -> CompSaverSpec:
    """Rebuild saver spec for a (possibly new) comp work file path."""
    department = "comp"
    entity = (entity_name or "").strip()
    prefix = (base.prefix or "").strip()
    if prefix and "_" in prefix:
        parsed_entity, parsed_dept = prefix.rsplit("_", 1)
        if parsed_entity and parsed_dept:
            entity = entity or parsed_entity
            department = parsed_dept
    if not entity:
        entity = prefix.rsplit("_", 1)[0] if "_" in prefix else prefix
    return build_comp_saver_spec(
        entity_name=entity,
        department=department,
        work_file=work_file,
        work_path=work_file.parent,
        fusion_path_map=base.fusion_path_map,
        frame_pattern=base.frame_pattern,
        output_ext=base.output_ext,
    )
