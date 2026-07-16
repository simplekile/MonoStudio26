"""Project-wide health scan and cleanup (autosaves, stray work files, DCC backups)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from monostudio.core.fs_reader import _parse_workfile_version
from monostudio.core.item_health_scan import (
    department_has_houdini_work,
    houdini_backup_folder_paths_for_department,
    split_invalid_work_files_for_department,
    work_file_prefix_for_item,
    work_paths_for_department,
    workfile_extensions_set,
)
from monostudio.core.models import Asset, ProjectIndex, Shot

# Blender incremental backup beside .blend (not registered work types).
_BLENDER_BACKUP_EXTENSIONS = frozenset({".blend1", ".blend2", ".blend3"})

# Other DCC incremental-save / backup extensions.
_AUTOSAVE_EXTRA_EXTENSIONS = frozenset(
    {
        ".ma~",
        ".mb~",
        ".bak",
    }
)


@dataclass(frozen=True)
class ProjectHealthScan:
    """Aggregated cleanable paths across assets and shots."""

    autosave_files: tuple[str, ...]
    blender_backup_files: tuple[str, ...]
    wrong_ext_files: tuple[str, ...]
    houdini_backup_dirs: tuple[str, ...]
    rename_candidates: tuple[str, ...]
    autosave_bytes: int = 0
    blender_backup_bytes: int = 0
    wrong_ext_bytes: int = 0
    houdini_backup_bytes: int = 0
    rename_bytes: int = 0

    @property
    def deletable_file_count(self) -> int:
        return (
            len(self.autosave_files)
            + len(self.blender_backup_files)
            + len(self.wrong_ext_files)
        )

    @property
    def deletable_folder_count(self) -> int:
        return len(self.houdini_backup_dirs)

    @property
    def total_deletable(self) -> int:
        return self.deletable_file_count + self.deletable_folder_count

    @property
    def total_deletable_bytes(self) -> int:
        return (
            self.autosave_bytes
            + self.blender_backup_bytes
            + self.wrong_ext_bytes
            + self.houdini_backup_bytes
        )

    def is_empty(self) -> bool:
        return self.total_deletable == 0 and not self.rename_candidates


def format_byte_size(n: int) -> str:
    """Human-readable size (binary units)."""
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    units = ("KB", "MB", "GB", "TB")
    value = float(n)
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            if value >= 100 or unit == "KB":
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
    return f"{n} B"


def path_size_bytes(path: Path) -> int:
    """File size, or recursive directory size. Returns 0 on error / missing."""
    try:
        if path.is_file():
            return int(path.stat().st_size)
        if not path.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += int(child.stat().st_size)
            except OSError:
                continue
    except OSError:
        return total
    return total


def paths_total_bytes(paths: list[str] | tuple[str, ...]) -> int:
    return sum(path_size_bytes(Path(p)) for p in paths)


def is_blender_backup_filename(name: str) -> bool:
    """True for Blender .blend1 / .blend2 / .blend3 backups."""
    ext = (Path(name or "").suffix or "").casefold()
    return ext in _BLENDER_BACKUP_EXTENSIONS


def is_probable_autosave_filename(name: str, prefix: str, work_exts: frozenset[str]) -> bool:
    """Heuristic: DCC autosave / incremental save, not a deliberate misnamed work file."""
    name = (name or "").strip()
    prefix = (prefix or "").strip()
    if not name:
        return False
    if is_blender_backup_filename(name):
        return False
    lower = name.casefold()
    ext = (Path(name).suffix or "").casefold()
    if ext in _AUTOSAVE_EXTRA_EXTENSIONS:
        return True
    if "#" in name or "@" in name:
        return True
    if not prefix:
        return False
    for work_ext in work_exts:
        we = work_ext if work_ext.startswith(".") else f".{work_ext}"
        if not lower.endswith(we):
            continue
        if _parse_workfile_version(name, prefix, we) is not None:
            return False
        stem = name[: -len(we)]
        if not stem.startswith(prefix + "_v"):
            continue
        tail = stem[len(prefix) + 2 :]
        if "." in tail:
            return True
    return False


def _collect_blender_backups_in_work(work_path: Path) -> list[Path]:
    """Any .blend1/.blend2/.blend3 under a work folder (not only prefix-matched)."""
    out: list[Path] = []
    if not work_path.is_dir():
        return out
    try:
        for p in work_path.iterdir():
            if p.is_file() and is_blender_backup_filename(p.name):
                out.append(p)
    except OSError:
        pass
    return out


def scan_project_health(index: ProjectIndex) -> ProjectHealthScan:
    work_exts = workfile_extensions_set()
    autosave: list[str] = []
    blender_backups: list[str] = []
    rename_candidates: list[str] = []
    wrong_ext: list[str] = []
    houdini_backups: list[str] = []
    seen_files: set[str] = set()
    seen_dirs: set[str] = set()

    def add_file(bucket: list[str], raw: str) -> None:
        key = raw.casefold()
        if key in seen_files:
            return
        seen_files.add(key)
        bucket.append(raw)

    def add_dir(raw: str) -> None:
        key = raw.casefold()
        if key in seen_dirs:
            return
        seen_dirs.add(key)
        houdini_backups.append(raw)

    entities: list[Asset | Shot] = list(index.assets) + list(index.shots)
    for ref in entities:
        for dept in getattr(ref, "departments", ()) or ():
            if not (
                getattr(dept, "work_exists", False)
                or getattr(dept, "work_path", None)
            ):
                continue
            prefix = work_file_prefix_for_item(ref, dept)
            if not prefix:
                continue
            _all_bad, bad_name, bad_ext = split_invalid_work_files_for_department(
                ref, dept, prefix
            )
            for raw in bad_name:
                name = Path(raw).name
                if is_blender_backup_filename(name):
                    add_file(blender_backups, raw)
                elif is_probable_autosave_filename(name, prefix, work_exts):
                    add_file(autosave, raw)
                else:
                    add_file(rename_candidates, raw)
            for raw in bad_ext:
                name = Path(raw).name
                if is_blender_backup_filename(name):
                    add_file(blender_backups, raw)
                else:
                    add_file(wrong_ext, raw)
            for wp in work_paths_for_department(ref, dept):
                for p in _collect_blender_backups_in_work(wp):
                    add_file(blender_backups, str(p))
            if department_has_houdini_work(ref, dept):
                for raw in houdini_backup_folder_paths_for_department(ref, dept):
                    add_dir(raw)

    autosave.sort(key=lambda s: Path(s).name.casefold())
    blender_backups.sort(key=lambda s: Path(s).name.casefold())
    rename_candidates.sort(key=lambda s: Path(s).name.casefold())
    wrong_ext.sort(key=lambda s: Path(s).name.casefold())
    houdini_backups.sort(key=lambda s: Path(s).name.casefold())
    return ProjectHealthScan(
        autosave_files=tuple(autosave),
        blender_backup_files=tuple(blender_backups),
        wrong_ext_files=tuple(wrong_ext),
        houdini_backup_dirs=tuple(houdini_backups),
        rename_candidates=tuple(rename_candidates),
        autosave_bytes=paths_total_bytes(autosave),
        blender_backup_bytes=paths_total_bytes(blender_backups),
        wrong_ext_bytes=paths_total_bytes(wrong_ext),
        houdini_backup_bytes=paths_total_bytes(houdini_backups),
        rename_bytes=paths_total_bytes(rename_candidates),
    )


def delete_project_health_files(paths: list[Path] | tuple[Path, ...]) -> list[tuple[Path, str]]:
    """Permanently delete files. Returns failures as (path, error_message)."""
    failures: list[tuple[Path, str]] = []
    for p in paths:
        try:
            if p.is_file():
                p.unlink()
        except OSError as e:
            failures.append((p, str(e)))
    return failures


def delete_project_health_folders(paths: list[Path] | tuple[Path, ...]) -> list[tuple[Path, str]]:
    """Permanently delete directories (e.g. Houdini backup/)."""
    failures: list[tuple[Path, str]] = []
    for p in paths:
        try:
            if p.is_dir():
                shutil.rmtree(p)
        except OSError as e:
            failures.append((p, str(e)))
    return failures
