from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.fs_reader import read_use_dcc_folders, scan_single_asset, work_file_prefix
from monostudio.core.pipeline_types_and_presets import load_pipeline_types_and_presets
from monostudio.core.type_registry import TypeRegistry


@dataclass(frozen=True)
class RenameAssetResult:
    old_path: Path
    new_path: Path
    final_name: str


def _is_safe_single_folder_name(name: str) -> bool:
    # Keep consistent with MainWindow._is_safe_single_folder_name (minimal safety).
    if not name:
        return False
    if name in (".", ".."):
        return False
    if any(ch in name for ch in ("/", "\\", ":", "\n", "\r", "\t")):
        return False
    return True


def _normalize_asset_name_for_type(*, project_root: Path, type_folder: str, raw_name: str) -> str:
    """
    Apply the same 'type short_name prefix' rule as CreateAssetDialog.asset_name().
    Returns the final asset folder name (may equal raw_name if no short_name is resolved).
    """
    base = (raw_name or "").strip()
    if not base:
        return ""

    type_id = TypeRegistry.for_project(project_root).get_type_by_folder(type_folder)
    if not type_id:
        return base

    try:
        tdef = load_pipeline_types_and_presets().types.get(type_id)
    except Exception:
        tdef = None
    short = (getattr(tdef, "short_name", "") or "").strip() if tdef is not None else ""
    if not short:
        return base
    prefix = short if short.endswith("_") else f"{short}_"
    return base if base.startswith(prefix) else f"{prefix}{base}"


def _iter_existing_work_roots(*, dept_dir: Path, use_dcc_folders: bool) -> list[Path]:
    """
    Return existing work roots for this department folder.
    - when use_dcc_folders=False: [dept/work] if exists
    - when use_dcc_folders=True: [dept/<dcc>/work] for each known DCC where folder exists
    """
    roots: list[Path] = []
    if not use_dcc_folders:
        p = dept_dir / "work"
        if p.is_dir():
            roots.append(p)
        return roots

    reg = get_default_dcc_registry()
    for dcc_id in reg.get_all_dccs():
        try:
            dcc_folder = reg.get_folder(dcc_id)
        except Exception:
            continue
        p = dept_dir / dcc_folder / "work"
        if p.is_dir():
            roots.append(p)
    return roots


def _collect_work_file_renames(
    *,
    asset_dir: Path,
    old_asset_name: str,
    new_asset_name: str,
    project_root: Path,
) -> list[tuple[Path, Path]]:
    """
    Build a list of (old_rel_path, new_rel_path) for work files under the asset.
    Paths are relative to asset_dir, so the list is stable across the asset folder rename.
    """
    if not old_asset_name or not new_asset_name or old_asset_name == new_asset_name:
        return []

    use_dcc_folders = read_use_dcc_folders(project_root)
    scanned = scan_single_asset(project_root, asset_dir)
    if scanned is None:
        raise FileNotFoundError(f"Asset folder not found or not recognized: {str(asset_dir)!r}")

    out: list[tuple[Path, Path]] = []
    for dept in scanned.departments or ():
        dept_id = (dept.name or "").strip()
        if not dept_id:
            continue
        prefix_old = work_file_prefix(name=old_asset_name, department=dept_id)
        prefix_new = work_file_prefix(name=new_asset_name, department=dept_id)
        if not prefix_old or not prefix_new:
            continue

        for work_root in _iter_existing_work_roots(dept_dir=Path(dept.path), use_dcc_folders=use_dcc_folders):
            try:
                for p in work_root.iterdir():
                    if not p.is_file():
                        continue
                    fn = p.name
                    if not fn.startswith(prefix_old + "_v"):
                        continue
                    new_fn = prefix_new + fn[len(prefix_old) :]
                    old_rel = p.relative_to(asset_dir)
                    new_rel = old_rel.with_name(new_fn)
                    out.append((old_rel, new_rel))
            except OSError:
                continue

    # De-dupe deterministically (can happen if scan has overlapping dept dirs).
    seen: set[tuple[str, str]] = set()
    dedup: list[tuple[Path, Path]] = []
    for a, b in out:
        key = (str(a), str(b))
        if key in seen:
            continue
        seen.add(key)
        dedup.append((a, b))
    return dedup


def rename_asset(*, project_root: Path, asset_path: Path, new_name: str) -> RenameAssetResult:
    """
    Rename an asset folder (assets/<type>/<asset_name>) and rename its work files so the scanner still matches them.
    Does not touch publish files/folders and does not update external DCC references.
    """
    project_root = Path(project_root)
    asset_path = Path(asset_path)
    if not project_root.is_dir():
        raise FileNotFoundError(f"Project root does not exist: {str(project_root)!r}")
    if not asset_path.is_dir():
        raise FileNotFoundError(f"Asset folder does not exist: {str(asset_path)!r}")

    type_folder = asset_path.parent.name
    old_name = asset_path.name
    final_name = _normalize_asset_name_for_type(project_root=project_root, type_folder=type_folder, raw_name=new_name)
    if not _is_safe_single_folder_name(final_name):
        raise ValueError("Invalid asset folder name.")
    if final_name == old_name:
        return RenameAssetResult(old_path=asset_path, new_path=asset_path, final_name=final_name)

    target = asset_path.parent / final_name
    if target.exists():
        raise FileExistsError(f"Target asset folder already exists: {str(target)!r}")

    renames = _collect_work_file_renames(
        asset_dir=asset_path,
        old_asset_name=old_name,
        new_asset_name=final_name,
        project_root=project_root,
    )

    renamed_folder = False
    completed: list[tuple[Path, Path]] = []  # (new_abs, old_abs) for rollback
    try:
        asset_path.rename(target)
        renamed_folder = True

        # Apply work-file renames inside the new asset folder.
        for old_rel, new_rel in renames:
            old_abs = target / old_rel
            new_abs = target / new_rel
            if not old_abs.exists():
                continue
            if new_abs.exists():
                # Prevent silent overwrite; safer to abort.
                raise FileExistsError(f"Target work file already exists: {str(new_abs)!r}")
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            old_abs.rename(new_abs)
            completed.append((new_abs, old_abs))

        return RenameAssetResult(old_path=asset_path, new_path=target, final_name=final_name)
    except Exception:
        # Rollback best-effort.
        try:
            for new_abs, old_abs in reversed(completed):
                try:
                    if new_abs.exists() and not old_abs.exists():
                        new_abs.rename(old_abs)
                except OSError:
                    pass
        finally:
            if renamed_folder:
                try:
                    target.rename(asset_path)
                except OSError:
                    pass
        raise

