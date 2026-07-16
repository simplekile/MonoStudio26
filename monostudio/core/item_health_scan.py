"""Filesystem scans for per-item / project health (work naming, Houdini backups)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.fs_reader import _parse_workfile_version, work_file_prefix
from monostudio.core.models import Asset, Department, Shot

_HOUDINI_WORK_EXTS = frozenset({".hip", ".hiplc", ".hipnc"})


def workfile_extensions_set() -> frozenset[str]:
    exts: set[str] = set()
    try:
        reg = get_default_dcc_registry()
        for dcc_id in reg.get_all_dccs():
            try:
                info = reg.get_dcc_info(dcc_id)
            except Exception:
                continue
            raw = info.get("workfile_extensions") if isinstance(info, dict) else None
            if not isinstance(raw, list):
                continue
            for ext in raw:
                if isinstance(ext, str) and ext.strip().startswith("."):
                    exts.add(ext.strip().lower())
    except Exception:
        pass
    return frozenset(exts)


def department_for_item(ref: Asset | Shot, active_department: str) -> Department | None:
    dep_cf = (active_department or "").strip().casefold()
    if not dep_cf:
        return None
    for d in getattr(ref, "departments", ()) or ():
        if (d.name or "").strip().casefold() == dep_cf:
            return d
    return None


def work_paths_for_department(ref: Asset | Shot, dept: Department) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    dep_cf = (dept.name or "").strip().casefold()

    def add(p: Path | None) -> None:
        if p is None:
            return
        try:
            key = str(p.resolve()).casefold()
        except OSError:
            key = str(p).casefold()
        if key in seen:
            return
        seen.add(key)
        if p.is_dir():
            paths.append(p)

    add(Path(dept.work_path))
    for (dept_id, _dcc_id), state in getattr(ref, "dcc_work_states", ()) or ():
        if (dept_id or "").strip().casefold() != dep_cf:
            continue
        wfp = getattr(state, "work_file_path", None)
        if isinstance(wfp, Path):
            add(wfp.parent)
    return paths


def invalid_work_files_split_in_folder(
    work_path: Path,
    prefix: str,
    work_exts: frozenset[str],
) -> tuple[list[Path], list[Path]]:
    """
    Files in work folder that look like work files but fail convention.
    Returns (wrong_name, wrong_ext): wrong_ext = starts with prefix but extension not a work DCC ext.
    """
    name_bad: list[Path] = []
    ext_bad: list[Path] = []
    if not prefix or not work_path.is_dir():
        return name_bad, ext_bad
    try:
        entries = list(work_path.iterdir())
    except OSError:
        return name_bad, ext_bad
    for p in entries:
        if not p.is_file():
            continue
        ext_lower = (p.suffix or "").lower()
        starts = p.name.startswith(prefix)
        if not (starts or ext_lower in work_exts):
            continue
        matched = False
        if ext_lower in work_exts:
            if _parse_workfile_version(p.name, prefix, ext_lower) is not None:
                matched = True
            elif p.name == prefix + ext_lower:
                matched = True
        if matched:
            continue
        if starts and ext_lower not in work_exts:
            ext_bad.append(p)
        else:
            name_bad.append(p)
    return name_bad, ext_bad


def split_invalid_work_files_for_department(
    ref: Asset | Shot,
    dept: Department,
    prefix: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Returns (all paths, wrong_name paths, wrong_ext paths) as strings, deduped."""
    work_exts = workfile_extensions_set()
    seen: set[str] = set()
    name_paths: list[str] = []
    ext_paths: list[str] = []

    def add_unique(lst: list[str], p: Path) -> None:
        try:
            key = str(p.resolve()).casefold()
        except OSError:
            key = str(p).casefold()
        if key in seen:
            return
        seen.add(key)
        lst.append(str(p))

    for wp in work_paths_for_department(ref, dept):
        n_bad, e_bad = invalid_work_files_split_in_folder(wp, prefix, work_exts)
        for p in n_bad:
            add_unique(name_paths, p)
        for p in e_bad:
            add_unique(ext_paths, p)
    name_paths.sort(key=lambda s: Path(s).name.casefold())
    ext_paths.sort(key=lambda s: Path(s).name.casefold())
    all_paths = sorted(set(name_paths) | set(ext_paths), key=lambda s: Path(s).name.casefold())
    return tuple(all_paths), tuple(name_paths), tuple(ext_paths)


def assess_work_naming_in_folder(
    work_path: Path,
    prefix: str,
    work_exts: frozenset[str],
) -> Literal["ok", "warn", "error"]:
    if not prefix or not work_path.is_dir():
        return "ok"
    valid = 0
    suspect = 0
    try:
        entries = list(work_path.iterdir())
    except OSError:
        return "ok"
    for p in entries:
        if not p.is_file():
            continue
        ext_lower = (p.suffix or "").lower()
        is_candidate = p.name.startswith(prefix) or ext_lower in work_exts
        if not is_candidate:
            continue
        matched = False
        if ext_lower in work_exts:
            if _parse_workfile_version(p.name, prefix, ext_lower) is not None:
                matched = True
            elif p.name == prefix + ext_lower:
                matched = True
        if matched:
            valid += 1
        else:
            suspect += 1
    if suspect > 0 and valid == 0:
        return "error"
    if suspect > 0:
        return "warn"
    return "ok"


def assess_work_naming_for_department(
    ref: Asset | Shot,
    dept: Department,
    prefix: str,
) -> Literal["ok", "warn", "error"]:
    work_exts = workfile_extensions_set()
    worst: Literal["ok", "warn", "error"] = "ok"
    for wp in work_paths_for_department(ref, dept):
        level = assess_work_naming_in_folder(wp, prefix, work_exts)
        if level == "error":
            return "error"
        if level == "warn":
            worst = "warn"
    return worst


def is_houdini_work_path(work_path: Path) -> bool:
    """True when work_path is a Houdini work directory (layout or hip files on disk)."""
    try:
        if work_path.parent.name.casefold() == "houdini" and work_path.name.casefold() == "work":
            return True
    except Exception:
        pass
    if not work_path.is_dir():
        return False
    try:
        for p in work_path.iterdir():
            if p.is_file() and (p.suffix or "").lower() in _HOUDINI_WORK_EXTS:
                return True
    except OSError:
        pass
    return False


def department_has_houdini_work(ref: Asset | Shot, dept: Department) -> bool:
    dep_cf = (dept.name or "").strip().casefold()
    for (dept_id, dcc_id), _state in getattr(ref, "dcc_work_states", ()) or ():
        if (dept_id or "").strip().casefold() == dep_cf and (dcc_id or "").strip().casefold() == "houdini":
            return True
    for wp in work_paths_for_department(ref, dept):
        if is_houdini_work_path(wp):
            return True
    return False


def houdini_backup_folder_paths_for_department(
    ref: Asset | Shot,
    dept: Department,
) -> tuple[str, ...]:
    """Non-empty Houdini ``backup/`` subfolders under department work paths."""
    dep_cf = (dept.name or "").strip().casefold()
    paths: list[str] = []
    seen: set[str] = set()

    def add_backup_dir(work_path: Path) -> None:
        if not is_houdini_work_path(work_path):
            return
        backup = work_path / "backup"
        if not backup.is_dir():
            return
        try:
            if not any(backup.iterdir()):
                return
        except OSError:
            return
        try:
            key = str(backup.resolve()).casefold()
        except OSError:
            key = str(backup).casefold()
        if key in seen:
            return
        seen.add(key)
        paths.append(str(backup))

    for wp in work_paths_for_department(ref, dept):
        add_backup_dir(wp)
    for (dept_id, dcc_id), state in getattr(ref, "dcc_work_states", ()) or ():
        if (dept_id or "").strip().casefold() != dep_cf:
            continue
        if (dcc_id or "").strip().casefold() != "houdini":
            continue
        wfp = getattr(state, "work_file_path", None)
        if isinstance(wfp, Path):
            add_backup_dir(wfp.parent)

    paths.sort(key=lambda s: Path(s).name.casefold())
    return tuple(paths)


def work_file_prefix_for_item(ref: Asset | Shot, dept: Department) -> str:
    return work_file_prefix(name=ref.name, department=dept.name)
