from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.models import Asset, Department, ProjectIndex, Shot

if TYPE_CHECKING:
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.dcc_registry import DccRegistry
    from monostudio.core.type_registry import TypeRegistry


def _iter_dirs(path: Path) -> list[Path]:
    try:
        # Ignore hidden/internal folders (e.g. ".monostudio") so metadata never pollutes the pipeline model.
        return sorted([p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")])
    except FileNotFoundError:
        return []


def _parse_version_dir_name(name: str) -> int | None:
    # Folder-name based version detection only.
    # Naming spec v1: v### (zero-padded). Ignore anything else silently.
    if len(name) != 4 or name[0].lower() != "v":
        return None
    digits = name[1:]
    if not digits.isdigit():
        return None
    return int(digits)


def _scan_publish_versions(publish_path: Path) -> tuple[str | None, int]:
    try:
        candidates: list[tuple[int, str]] = []
        for p in publish_path.iterdir():
            if not p.is_dir():
                continue
            v = _parse_version_dir_name(p.name)
            if v is None:
                continue
            candidates.append((v, p.name))

        if not candidates:
            return None, 0
        latest = max(candidates, key=lambda t: t[0])[1]
        return latest, len(candidates)
    except FileNotFoundError:
        return None, 0


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _read_item_open_meta(item_root: Path) -> dict:
    """
    Read per-item open metadata (best-effort).
    Path: <item>/.monostudio/open.json
    """
    path = Path(item_root) / ".monostudio" / "open.json"
    try:
        if not path.is_file():
            return {}
    except OSError:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dcc_by_workfile_extension(ext: str) -> str | None:
    ext = (ext or "").strip().lower()
    if ext == ".blend":
        return "blender"
    if ext in (".ma", ".mb"):
        return "maya"
    if ext in (".hip", ".hiplc", ".hipnc"):
        return "houdini"
    return None


def work_file_prefix(*, name: str, department: str) -> str:
    """
    Build work file name prefix (before _v001 and extension).
    Convention: {name}_{department_folder}. Department is the physical folder name (e.g. 01_model).
    Asset name already includes type prefix (e.g. char_aya). e.g. char_aya_01_model, shot_01_02_anim
    """
    name = (name or "").strip()
    department = (department or "").strip()
    if not name:
        return ""
    return f"{name}_{department}"


def _parse_workfile_version(filename: str, prefix: str, ext: str) -> int | None:
    """Parse version from filename like {prefix}_v001{ext}. Returns int or None."""
    if not filename.startswith(prefix + "_v") or not filename.endswith(ext):
        return None
    mid = filename[len(prefix) + 2 : -len(ext)]  # between "_v" and ext
    if len(mid) != 3 or not mid.isdigit():
        return None
    return int(mid)


def _scan_work_versions(
    work_path: Path, prefix: str, reg: "DccRegistry | None" = None
) -> tuple[int | None, str | None]:
    """
    Scan work_path for files matching {prefix}_v###{ext}. Returns (max_version, dcc_id) or (None, None).
    """
    if not prefix:
        return None, None
    try:
        reg = reg or get_default_dcc_registry()
    except Exception:
        return None, None
    candidates: list[tuple[str, str]] = []
    for dcc_id in reg.get_all_dccs():
        try:
            info = reg.get_dcc_info(dcc_id)
        except Exception:
            continue
        exts = info.get("workfile_extensions") if isinstance(info, dict) else None
        if not isinstance(exts, list) or not exts:
            continue
        for ext in exts:
            if isinstance(ext, str) and ext.strip().startswith("."):
                candidates.append((ext.strip(), dcc_id))
    max_ver: int | None = None
    found_dcc: str | None = None
    for ext, dcc in candidates:
        try:
            for p in work_path.iterdir():
                if not p.is_file():
                    continue
                if p.name.startswith(prefix + "_v") and p.name.endswith(ext):
                    v = _parse_workfile_version(p.name, prefix, ext)
                    if v is not None and (max_ver is None or v > max_ver):
                        max_ver = v
                        found_dcc = dcc
        except OSError:
            continue
    return max_ver, found_dcc


def _max_work_version_for_ext(work_path: Path, prefix: str, ext: str) -> int | None:
    """Return the maximum version number for files {prefix}_v###{ext} in work_path, or None."""
    ext = (ext or "").strip()
    if not ext.startswith("."):
        ext = "." + ext
    if not prefix:
        return None
    max_ver: int | None = None
    try:
        for p in work_path.iterdir():
            if not p.is_file() or not p.name.startswith(prefix + "_v") or not p.name.endswith(ext):
                continue
            v = _parse_workfile_version(p.name, prefix, ext)
            if v is not None and (max_ver is None or v > max_ver):
                max_ver = v
    except OSError:
        pass
    return max_ver


def get_work_file_path(work_path: Path, prefix: str, ext: str) -> Path:
    """
    Return path to the work file: latest existing version if any, otherwise prefix_v001.ext.
    ext must start with a dot (e.g. .blend).
    """
    ext = (ext or "").strip()
    if not ext.startswith("."):
        ext = "." + ext
    if not prefix:
        return work_path / f"unnamed_v001{ext}"
    max_ver = _max_work_version_for_ext(work_path, prefix, ext)
    version = max_ver if max_ver is not None else 1
    return work_path / f"{prefix}_v{version:03d}{ext}"


def _detect_workfile(
    *,
    work_path: Path,
    basename: str,
    department: str,
) -> tuple[bool, str | None]:
    """
    Detect whether a recognized work file exists and which DCC it belongs to.
    Convention: <work_path>/<prefix>_v###<ext> with prefix = name_department_folder (department = folder name).
    """
    prefix = work_file_prefix(name=basename, department=department)
    if not prefix:
        return False, None
    max_ver, dcc = _scan_work_versions(work_path, prefix)
    return (max_ver is not None, dcc)


def _resolve_dcc_for_department(*, dept_name: str, meta: dict, fallback_from_file: str | None) -> str | None:
    """
    Prefer metadata mapping by department; fall back to detected file extension.
    """
    if isinstance(meta, dict):
        by_dep = meta.get("last_open_by_department")
        if isinstance(by_dep, dict):
            # keys are stored as exact strings; match case-insensitively
            for k, node in by_dep.items():
                if _norm(k) != _norm(dept_name):
                    continue
                if isinstance(node, dict):
                    dcc = node.get("dcc")
                    if isinstance(dcc, str) and dcc.strip():
                        return dcc.strip()

        last_open = meta.get("last_open")
        if isinstance(last_open, dict) and _norm(last_open.get("department")) == _norm(dept_name):
            dcc = last_open.get("dcc")
            if isinstance(dcc, str) and dcc.strip():
                return dcc.strip()

        defaults = meta.get("defaults")
        if isinstance(defaults, dict) and _norm(defaults.get("department")) == _norm(dept_name):
            dcc = defaults.get("dcc")
            if isinstance(dcc, str) and dcc.strip():
                return dcc.strip()

    return fallback_from_file


def build_project_index(
    project_root: Path,
    department_registry: "DepartmentRegistry | None" = None,
    type_registry: "TypeRegistry | None" = None,
) -> ProjectIndex:
    """
    Phase 1: Filesystem is source of truth.
    - Scan only: project_root/assets and project_root/shots (top-level)
    - Type folders under assets/ resolved via TypeRegistry (logical ID = Asset.asset_type)
    - Department folders resolved via DepartmentRegistry (logical ID = Department.name)
    - Skip folders that do not map to a known type or department
    - No validation, no auto-creation, no publish/version logic
    """
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.type_registry import TypeRegistry

    dept_registry = department_registry if department_registry is not None else DepartmentRegistry.for_project(project_root)
    type_reg = type_registry if type_registry is not None else TypeRegistry.for_project(project_root)
    assets_dir = project_root / "assets"
    shots_dir = project_root / "shots"

    assets: list[Asset] = []
    for asset_type_dir in _iter_dirs(assets_dir):
        type_id = type_reg.get_type_by_folder(asset_type_dir.name)
        if type_id is None:
            continue
        for asset_dir in _iter_dirs(asset_type_dir):
            open_meta = _read_item_open_meta(asset_dir)
            departments: list[Department] = []
            for dept_dir in _iter_dirs(asset_dir):
                dept_id = dept_registry.get_department_by_folder(dept_dir.name, "asset")
                if dept_id is None:
                    continue
                work_path = dept_dir / "work"
                publish_path = dept_dir / "publish"
                work_exists = work_path.is_dir()
                publish_exists = publish_path.is_dir()
                work_file_exists, dcc_from_file = _detect_workfile(
                    work_path=work_path,
                    basename=asset_dir.name,
                    department=dept_dir.name,
                )
                work_file_dcc = _resolve_dcc_for_department(
                    dept_name=dept_id,
                    meta=open_meta,
                    fallback_from_file=dcc_from_file,
                )
                if publish_exists:
                    latest_version, version_count = _scan_publish_versions(publish_path)
                else:
                    latest_version, version_count = None, 0
                departments.append(
                    Department(
                        name=dept_id,
                        path=dept_dir,
                        work_path=work_path,
                        publish_path=publish_path,
                        work_exists=work_exists,
                        work_file_exists=work_file_exists,
                        work_file_dcc=work_file_dcc,
                        publish_exists=publish_exists,
                        latest_publish_version=latest_version,
                        publish_version_count=version_count,
                    )
                )

            assets.append(
                Asset(
                    asset_type=type_id,
                    name=asset_dir.name,
                    path=asset_dir,
                    departments=tuple(departments),
                )
            )

    shots: list[Shot] = []
    for shot_dir in _iter_dirs(shots_dir):
        open_meta = _read_item_open_meta(shot_dir)
        departments = []
        for dept_dir in _iter_dirs(shot_dir):
            dept_id = dept_registry.get_department_by_folder(dept_dir.name, "shot")
            if dept_id is None:
                continue
            work_path = dept_dir / "work"
            publish_path = dept_dir / "publish"
            work_exists = work_path.is_dir()
            publish_exists = publish_path.is_dir()
            work_file_exists, dcc_from_file = _detect_workfile(
                work_path=work_path,
                basename=shot_dir.name,
                department=dept_dir.name,
            )
            work_file_dcc = _resolve_dcc_for_department(
                dept_name=dept_id,
                meta=open_meta,
                fallback_from_file=dcc_from_file,
            )
            if publish_exists:
                latest_version, version_count = _scan_publish_versions(publish_path)
            else:
                latest_version, version_count = None, 0
            departments.append(
                Department(
                    name=dept_id,
                    path=dept_dir,
                    work_path=work_path,
                    publish_path=publish_path,
                    work_exists=work_exists,
                    work_file_exists=work_file_exists,
                    work_file_dcc=work_file_dcc,
                    publish_exists=publish_exists,
                    latest_publish_version=latest_version,
                    publish_version_count=version_count,
                )
            )
        shots.append(
            Shot(
                name=shot_dir.name,
                path=shot_dir,
                departments=tuple(departments),
            )
        )

    # Sort deterministically for stable UI ordering
    assets_sorted = tuple(sorted(assets, key=lambda a: (a.asset_type, a.name)))
    shots_sorted = tuple(sorted(shots, key=lambda s: s.name))

    return ProjectIndex(root=project_root, assets=assets_sorted, shots=shots_sorted)

