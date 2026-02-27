from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.models import Asset, DccWorkState, Department, ProjectIndex, Shot

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


def read_use_dcc_folders(project_root: Path) -> bool:
    """
    Read project-level flag: use DCC-specific subfolders under department (e.g. modeling/blender/work).
    Source: <project_root>/.monostudio/project.json key "use_dcc_folders".
    Default: True (enabled) when file or key is missing.
    """
    path = Path(project_root) / ".monostudio" / "project.json"
    try:
        if not path.is_file():
            return True
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    if "use_dcc_folders" not in data:
        return True
    return bool(data.get("use_dcc_folders"))


def save_use_dcc_folders(project_root: Path, value: bool) -> bool:
    """
    Write use_dcc_folders to project.json (merge with existing keys). Returns True on success.
    """
    path = Path(project_root) / ".monostudio" / "project.json"
    try:
        data: dict = {}
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        data["use_dcc_folders"] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except (OSError, json.JSONDecodeError):
        return False


def resolve_work_path(
    dept_dir: Path,
    dcc_id: str,
    use_dcc_folders: bool,
    dcc_registry: "DccRegistry",
) -> Path:
    """
    Resolve the work directory for a department and DCC.
    - If use_dcc_folders is False or missing: dept_dir/work (legacy).
    - If use_dcc_folders is True: dept_dir/<dcc_folder>/work. DCC folder from DccRegistry.get_folder(dcc_id).
    Raises if DCC is unknown.
    """
    if not use_dcc_folders:
        return dept_dir / "work"
    folder = dcc_registry.get_folder(dcc_id)
    return dept_dir / folder / "work"


def _build_asset_departments(
    asset_dir: Path,
    dept_registry: "DepartmentRegistry",
    open_meta: dict,
    use_dcc_folders: bool = False,
    dcc_registry: "DccRegistry | None" = None,
) -> tuple[list[Department], list[tuple[tuple[str, str], DccWorkState]]]:
    """Build department list and per-(dept, dcc) work states for a single asset directory."""
    departments: list[Department] = []
    all_dcc_states: list[tuple[tuple[str, str], DccWorkState]] = []
    reg = dcc_registry if dcc_registry is not None else None
    try:
        reg_use = reg or get_default_dcc_registry()
    except Exception:
        reg_use = None
    # Nested: dùng relative path (vd 01_modelling/01_sculpt) để scan subdepartment
    for rel_path, dept_id in dept_registry.get_department_relative_paths("asset"):
        dept_dir = asset_dir / rel_path
        if not dept_dir.is_dir():
            continue
        publish_path = dept_dir / "publish"
        publish_exists = publish_path.is_dir()
        prefix = work_file_prefix(name=asset_dir.name, department=dept_id)

        if use_dcc_folders and reg is not None:
            available_dccs = reg.get_available_dccs(dept_id)
            work_file_dccs_list: list[str] = []
            work_exists = False
            flat_work = dept_dir / "work"
            if flat_work.is_dir():
                work_exists = True
                for dcc in _scan_work_dccs(flat_work, prefix, reg):
                    if dcc not in work_file_dccs_list:
                        work_file_dccs_list.append(dcc)
            # Scan every DCC's subfolder (houdini/work, blender/work, …); not just available_dccs,
            # so e.g. 02_rigging/houdini/work is found even when Houdini is only in registry for "fx".
            for dcc_id in reg.get_all_dccs():
                try:
                    wp = resolve_work_path(dept_dir, dcc_id, True, reg)
                except RuntimeError:
                    continue
                if wp.is_dir():
                    work_exists = True
                for dcc in _scan_work_dccs(wp, prefix, reg):
                    if dcc not in work_file_dccs_list:
                        work_file_dccs_list.append(dcc)
            work_file_dccs = tuple(work_file_dccs_list)
            work_file_exists = len(work_file_dccs) > 0
            dcc_from_file = work_file_dccs[0] if work_file_dccs else None
            work_file_dcc = _resolve_dcc_for_department(
                dept_name=dept_id,
                meta=open_meta,
                fallback_from_file=dcc_from_file,
            )
            if not work_file_dcc and work_file_dccs:
                work_file_dcc = work_file_dccs[0]
            first_dcc = available_dccs[0] if available_dccs else None
            resolved_dcc = work_file_dcc or first_dcc
            if resolved_dcc is not None:
                work_path = resolve_work_path(dept_dir, resolved_dcc, True, reg)
            else:
                work_path = dept_dir / "work"
        else:
            work_path = dept_dir / "work"
            work_exists = work_path.is_dir()
            work_file_dccs = tuple(_scan_work_dccs(work_path, prefix))
            work_file_exists = len(work_file_dccs) > 0
            dcc_from_file = work_file_dccs[0] if work_file_dccs else None
            work_file_dcc = _resolve_dcc_for_department(
                dept_name=dept_id,
                meta=open_meta,
                fallback_from_file=dcc_from_file,
            )
            if not work_file_dcc and work_file_dccs:
                work_file_dcc = work_file_dccs[0]

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
                work_file_dccs=work_file_dccs,
                publish_exists=publish_exists,
                latest_publish_version=latest_version,
                publish_version_count=version_count,
            )
        )
        if reg_use is not None:
            all_dcc_states.extend(
                _dcc_work_states_for_department(dept_dir, dept_id, prefix, use_dcc_folders, reg_use)
            )
    return (departments, all_dcc_states)


def _build_shot_departments(
    shot_dir: Path,
    dept_registry: "DepartmentRegistry",
    open_meta: dict,
    use_dcc_folders: bool = False,
    dcc_registry: "DccRegistry | None" = None,
) -> tuple[list[Department], list[tuple[tuple[str, str], DccWorkState]]]:
    """Build department list and per-(dept, dcc) work states for a single shot directory."""
    departments: list[Department] = []
    all_dcc_states: list[tuple[tuple[str, str], DccWorkState]] = []
    reg = dcc_registry if dcc_registry is not None else None
    try:
        reg_use = reg or get_default_dcc_registry()
    except Exception:
        reg_use = None
    # Nested: dùng relative path để scan subdepartment
    for rel_path, dept_id in dept_registry.get_department_relative_paths("shot"):
        dept_dir = shot_dir / rel_path
        if not dept_dir.is_dir():
            continue
        publish_path = dept_dir / "publish"
        publish_exists = publish_path.is_dir()
        prefix = work_file_prefix(name=shot_dir.name, department=dept_id)

        if use_dcc_folders and reg is not None:
            available_dccs = reg.get_available_dccs(dept_id)
            work_file_dccs_list: list[str] = []
            work_exists = False
            flat_work = dept_dir / "work"
            if flat_work.is_dir():
                work_exists = True
                for dcc in _scan_work_dccs(flat_work, prefix, reg):
                    if dcc not in work_file_dccs_list:
                        work_file_dccs_list.append(dcc)
            for dcc_id in reg.get_all_dccs():
                try:
                    wp = resolve_work_path(dept_dir, dcc_id, True, reg)
                except RuntimeError:
                    continue
                if wp.is_dir():
                    work_exists = True
                for dcc in _scan_work_dccs(wp, prefix, reg):
                    if dcc not in work_file_dccs_list:
                        work_file_dccs_list.append(dcc)
            work_file_dccs = tuple(work_file_dccs_list)
            work_file_exists = len(work_file_dccs) > 0
            dcc_from_file = work_file_dccs[0] if work_file_dccs else None
            work_file_dcc = _resolve_dcc_for_department(
                dept_name=dept_id,
                meta=open_meta,
                fallback_from_file=dcc_from_file,
            )
            if not work_file_dcc and work_file_dccs:
                work_file_dcc = work_file_dccs[0]
            first_dcc = available_dccs[0] if available_dccs else None
            resolved_dcc = work_file_dcc or first_dcc
            if resolved_dcc is not None:
                work_path = resolve_work_path(dept_dir, resolved_dcc, True, reg)
            else:
                work_path = dept_dir / "work"
        else:
            work_path = dept_dir / "work"
            work_exists = work_path.is_dir()
            work_file_dccs = tuple(_scan_work_dccs(work_path, prefix))
            work_file_exists = len(work_file_dccs) > 0
            dcc_from_file = work_file_dccs[0] if work_file_dccs else None
            work_file_dcc = _resolve_dcc_for_department(
                dept_name=dept_id,
                meta=open_meta,
                fallback_from_file=dcc_from_file,
            )
            if not work_file_dcc and work_file_dccs:
                work_file_dcc = work_file_dccs[0]

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
                work_file_dccs=work_file_dccs,
                publish_exists=publish_exists,
                latest_publish_version=latest_version,
                publish_version_count=version_count,
            )
        )
        if reg_use is not None:
            all_dcc_states.extend(
                _dcc_work_states_for_department(dept_dir, dept_id, prefix, use_dcc_folders, reg_use)
            )
    return (departments, all_dcc_states)


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
    Convention: {name}_{department_id}. Department is the logical ID (e.g. sculpt, uv).
    Asset name already includes type prefix (e.g. char_aya). e.g. char_aya_sculpt, shot_01_anim
    """
    name = (name or "").strip()
    department = (department or "").strip()
    if not name:
        return ""
    return f"{name}_{department}"


def _parse_workfile_version(filename: str, prefix: str, ext: str) -> int | None:
    """Parse version from filename like {prefix}_v001[_{description}]{ext}. Returns int or None."""
    if not filename.startswith(prefix + "_v") or not filename.endswith(ext):
        return None
    # Version is exactly 3 digits after "_v"; optional _description may follow before ext
    start = len(prefix) + 2  # first char after "_v"
    if len(filename) < start + 3 + len(ext):
        return None
    mid = filename[start : start + 3]
    if not mid.isdigit():
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


def _scan_work_dccs(work_path: Path, prefix: str, reg: "DccRegistry | None" = None) -> list[str]:
    """
    Scan work_path for files matching {prefix}_v###{ext}; return list of all dcc_ids that have at least one file.
    E.g. if both .blend and .ma exist, returns ["blender", "maya"] (order from registry).
    """
    if not prefix:
        return []
    try:
        reg = reg or get_default_dcc_registry()
    except Exception:
        return []
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
    found_dccs: list[str] = []
    seen: set[str] = set()
    for ext, dcc in candidates:
        if dcc in seen:
            continue
        try:
            for p in work_path.iterdir():
                if not p.is_file():
                    continue
                if not p.name.endswith(ext):
                    continue
                # Versioned: prefix_v001.ext
                if p.name.startswith(prefix + "_v"):
                    v = _parse_workfile_version(p.name, prefix, ext)
                    if v is not None:
                        found_dccs.append(dcc)
                        seen.add(dcc)
                        break
                # No version: prefix.ext (still count for DCC detection/icon)
                if p.name == prefix + ext:
                    found_dccs.append(dcc)
                    seen.add(dcc)
                    break
        except OSError:
            continue
    return found_dccs


def _max_work_version_for_ext(work_path: Path, prefix: str, ext: str) -> int | None:
    """Return the maximum version number for files {prefix}_v###[_{description}]{ext} in work_path, or None."""
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


def _resolve_latest_work_file_path(work_path: Path, prefix: str, ext: str) -> Path | None:
    """
    Return path to the latest existing workfile matching {prefix}_v###[_{description}]{ext}.
    Used so that work_file_path points to the actual file on disk (e.g. with _fixNecklace suffix).
    """
    ext = (ext or "").strip()
    if not ext.startswith("."):
        ext = "." + ext
    if not prefix:
        return None
    best_ver: int | None = None
    best_path: Path | None = None
    try:
        for p in work_path.iterdir():
            if not p.is_file() or not p.name.startswith(prefix + "_v") or not p.name.endswith(ext):
                continue
            v = _parse_workfile_version(p.name, prefix, ext)
            if v is not None and (best_ver is None or v > best_ver):
                best_ver = v
                best_path = p
    except OSError:
        pass
    return best_path


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


def _dcc_work_states_for_department(
    dept_dir: Path,
    dept_id: str,
    prefix: str,
    use_dcc_folders: bool,
    reg: "DccRegistry",
) -> list[tuple[tuple[str, str], DccWorkState]]:
    """
    Build per-(department, dcc) work file state from filesystem (used by scan only).
    Returns list of ((dept_id, dcc_id), DccWorkState).
    """
    result: list[tuple[tuple[str, str], DccWorkState]] = []
    dept_id = (dept_id or "").strip()
    if not dept_id or not prefix:
        return result
    try:
        all_dccs = reg.get_all_dccs()
    except Exception:
        return result
    for dcc_id in all_dccs:
        try:
            work_folder = resolve_work_path(dept_dir, dcc_id, use_dcc_folders, reg)
        except (RuntimeError, Exception):
            work_folder = dept_dir / "work"
        try:
            folder_exists = work_folder.is_dir()
        except OSError:
            folder_exists = False
        work_file_path: Path | None = None
        if folder_exists:
            try:
                info = reg.get_dcc_info(dcc_id)
                exts = info.get("workfile_extensions") if isinstance(info, dict) else None
                if isinstance(exts, list):
                    for ext in exts:
                        e = (ext or "").strip() if isinstance(ext, str) else ""
                        if not e.startswith("."):
                            e = "." + e if e else ""
                        if e:
                            p = _resolve_latest_work_file_path(work_folder, prefix, e)
                            if p is not None and p.is_file():
                                work_file_path = p
                                break
            except (RuntimeError, OSError):
                pass
        state = DccWorkState(work_file_path=work_file_path, work_folder_exists=folder_exists)
        result.append(((dept_id, dcc_id), state))
    return result


def _detect_workfile(
    *,
    work_path: Path,
    basename: str,
    department: str,
) -> tuple[bool, str | None]:
    """
    Detect whether a recognized work file exists and which DCC it belongs to.
    Convention: <work_path>/<prefix>_v###<ext> with prefix = name_department_id (department = logical ID).
    """
    prefix = work_file_prefix(name=basename, department=department)
    if not prefix:
        return False, None
    max_ver, dcc = _scan_work_versions(work_path, prefix)
    return (max_ver is not None, dcc)


def _resolve_dcc_for_department(*, dept_name: str, meta: dict, fallback_from_file: str | None) -> str | None:
    """
    Prefer DCC detected from actual work file on disk (source of truth for icon/display).
    When no work file is detected, use metadata (last_open_by_department / last_open / defaults).
    """
    if fallback_from_file and str(fallback_from_file).strip():
        return fallback_from_file.strip()
    if isinstance(meta, dict):
        by_dep = meta.get("last_open_by_department")
        if isinstance(by_dep, dict):
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
    - Scan only: project_root/<assets_folder> and project_root/<shots_folder> (top-level)
    - Type folders under assets/ resolved via TypeRegistry (logical ID = Asset.asset_type)
    - Department folders resolved via DepartmentRegistry (logical ID = Department.name)
    - Skip folders that do not map to a known type or department
    - No validation, no auto-creation, no publish/version logic
    """
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.structure_registry import StructureRegistry
    from monostudio.core.type_registry import TypeRegistry

    dept_registry = department_registry if department_registry is not None else DepartmentRegistry.for_project(project_root)
    type_reg = type_registry if type_registry is not None else TypeRegistry.for_project(project_root)
    struct_reg = StructureRegistry.for_project(project_root)
    use_dcc_folders = read_use_dcc_folders(project_root)
    dcc_reg = get_default_dcc_registry()
    assets_dir = project_root / struct_reg.get_folder("assets")
    shots_dir = project_root / struct_reg.get_folder("shots")

    assets: list[Asset] = []
    for asset_type_dir in _iter_dirs(assets_dir):
        type_id = type_reg.get_type_by_folder(asset_type_dir.name)
        if type_id is None:
            continue
        for asset_dir in _iter_dirs(asset_type_dir):
            open_meta = _read_item_open_meta(asset_dir)
            departments, dcc_states = _build_asset_departments(
                asset_dir, dept_registry, open_meta, use_dcc_folders, dcc_reg
            )
            assets.append(
                Asset(
                    asset_type=type_id,
                    name=asset_dir.name,
                    path=asset_dir,
                    departments=tuple(departments),
                    dcc_work_states=tuple(dcc_states),
                )
            )

    shots: list[Shot] = []
    for shot_dir in _iter_dirs(shots_dir):
        open_meta = _read_item_open_meta(shot_dir)
        departments, dcc_states = _build_shot_departments(
            shot_dir, dept_registry, open_meta, use_dcc_folders, dcc_reg
        )
        shots.append(
            Shot(
                name=shot_dir.name,
                path=shot_dir,
                departments=tuple(departments),
                dcc_work_states=tuple(dcc_states),
            )
        )

    # Sort deterministically for stable UI ordering
    assets_sorted = tuple(sorted(assets, key=lambda a: (a.asset_type, a.name)))
    shots_sorted = tuple(sorted(shots, key=lambda s: s.name))

    return ProjectIndex(root=project_root, assets=assets_sorted, shots=shots_sorted)


def scan_single_asset(
    project_root: Path,
    asset_dir: Path,
    department_registry: "DepartmentRegistry | None" = None,
    type_registry: "TypeRegistry | None" = None,
) -> Asset | None:
    """
    Scan a single asset directory. Returns Asset or None if path is not a valid asset
    (e.g. type folder unknown). Used for incremental updates.
    """
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.type_registry import TypeRegistry

    dept_reg = department_registry or DepartmentRegistry.for_project(project_root)
    type_reg = type_registry or TypeRegistry.for_project(project_root)
    from monostudio.core.structure_registry import StructureRegistry
    struct_reg = StructureRegistry.for_project(project_root)
    assets_dir = project_root / struct_reg.get_folder("assets")
    try:
        asset_dir = asset_dir.resolve()
        project_root = project_root.resolve()
    except OSError:
        return None
    if not asset_dir.is_dir():
        return None
    try:
        asset_dir.relative_to(assets_dir)
    except ValueError:
        return None
    if len(asset_dir.parents) < 2 or asset_dir.parent.parent != assets_dir:
        return None
    type_folder = asset_dir.parent.name
    type_id = type_reg.get_type_by_folder(type_folder)
    if type_id is None:
        return None
    use_dcc_folders = read_use_dcc_folders(project_root)
    dcc_reg = get_default_dcc_registry()
    open_meta = _read_item_open_meta(asset_dir)
    departments, dcc_states = _build_asset_departments(
        asset_dir, dept_reg, open_meta, use_dcc_folders, dcc_reg
    )
    return Asset(
        asset_type=type_id,
        name=asset_dir.name,
        path=asset_dir,
        departments=tuple(departments),
        dcc_work_states=tuple(dcc_states),
    )


def scan_single_shot(
    project_root: Path,
    shot_dir: Path,
    department_registry: "DepartmentRegistry | None" = None,
) -> Shot | None:
    """
    Scan a single shot directory. Returns Shot or None if path is not a valid shot.
    Used for incremental updates.
    """
    from monostudio.core.department_registry import DepartmentRegistry

    dept_reg = department_registry or DepartmentRegistry.for_project(project_root)
    from monostudio.core.structure_registry import StructureRegistry
    struct_reg = StructureRegistry.for_project(project_root)
    shots_dir = project_root / struct_reg.get_folder("shots")
    try:
        shot_dir = shot_dir.resolve()
        project_root = project_root.resolve()
    except OSError:
        return None
    if not shot_dir.is_dir():
        return None
    try:
        shot_dir.relative_to(shots_dir)
    except ValueError:
        return None
    if shot_dir.parent != shots_dir:
        return None
    use_dcc_folders = read_use_dcc_folders(project_root)
    dcc_reg = get_default_dcc_registry()
    open_meta = _read_item_open_meta(shot_dir)
    departments, dcc_states = _build_shot_departments(
        shot_dir, dept_reg, open_meta, use_dcc_folders, dcc_reg
    )
    return Shot(
        name=shot_dir.name,
        path=shot_dir,
        departments=tuple(departments),
        dcc_work_states=tuple(dcc_states),
    )


def scan_assets_in_type(
    project_root: Path,
    type_folder_name: str,
    department_registry: "DepartmentRegistry | None" = None,
    type_registry: "TypeRegistry | None" = None,
) -> list[Asset]:
    """
    Scan all assets under assets/<type_folder_name>. Returns list of Asset.
    Used for incremental updates when a type folder is affected.
    """
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.type_registry import TypeRegistry

    dept_reg = department_registry or DepartmentRegistry.for_project(project_root)
    type_reg = type_registry or TypeRegistry.for_project(project_root)
    type_id = type_reg.get_type_by_folder(type_folder_name)
    if type_id is None:
        return []
    from monostudio.core.structure_registry import StructureRegistry
    struct_reg = StructureRegistry.for_project(project_root)
    assets_dir = project_root / struct_reg.get_folder("assets")
    type_dir = assets_dir / type_folder_name
    if not type_dir.is_dir():
        return []
    use_dcc_folders = read_use_dcc_folders(project_root)
    dcc_reg = get_default_dcc_registry()
    assets: list[Asset] = []
    for asset_dir in _iter_dirs(type_dir):
        open_meta = _read_item_open_meta(asset_dir)
        departments, dcc_states = _build_asset_departments(
            asset_dir, dept_reg, open_meta, use_dcc_folders, dcc_reg
        )
        assets.append(
            Asset(
                asset_type=type_id,
                name=asset_dir.name,
                path=asset_dir,
                departments=tuple(departments),
                dcc_work_states=tuple(dcc_states),
            )
        )
    return sorted(assets, key=lambda a: (a.asset_type, a.name))


def run_incremental_scan(
    project_root: Path,
    asset_ids: list[str],
    shot_ids: list[str],
    type_folders: list[str],
    department_registry: "DepartmentRegistry | None" = None,
    type_registry: "TypeRegistry | None" = None,
) -> tuple[list[Asset], list[Shot], list[str], list[str]]:
    """
    Incremental scan: rescan only the given asset paths, shot paths, and type folders.
    Returns (new_assets, new_shots, requested_asset_ids, requested_shot_ids).
    Caller can remove from state any requested id not present in new_* (e.g. deleted on disk).
    """
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.type_registry import TypeRegistry

    dept_reg = department_registry or DepartmentRegistry.for_project(project_root)
    type_reg = type_registry or TypeRegistry.for_project(project_root)
    root = Path(project_root).resolve()
    assets_out: list[Asset] = []
    shots_out: list[Shot] = []
    seen_asset_ids: set[str] = set()
    for aid in asset_ids:
        if not aid or aid in seen_asset_ids:
            continue
        try:
            p = Path(aid).resolve()
        except OSError:
            continue
        a = scan_single_asset(root, p, dept_reg, type_reg)
        if a is not None:
            seen_asset_ids.add(aid)
            assets_out.append(a)
    for sid in shot_ids:
        if not sid:
            continue
        try:
            p = Path(sid).resolve()
        except OSError:
            continue
        s = scan_single_shot(root, p, dept_reg)
        if s is not None:
            shots_out.append(s)
    for type_folder in type_folders:
        if not type_folder:
            continue
        for a in scan_assets_in_type(root, type_folder, dept_reg, type_reg):
            aid = str(a.path)
            if aid not in seen_asset_ids:
                seen_asset_ids.add(aid)
                assets_out.append(a)
    return (assets_out, shots_out, list(asset_ids), list(shot_ids))

