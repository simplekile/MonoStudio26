"""Resolve entity thumbnail file path from user thumbs vs work sequence roots (shared Inspector + main grid)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monostudio.core.models import Asset, Shot


def _find_project_root_near_entity(item_root: Path) -> Path | None:
    """Walk up from asset/shot folder until .monostudio/project.json (for read_use_dcc_folders)."""
    p = Path(item_root)
    for _ in range(24):
        try:
            if (p / ".monostudio" / "project.json").is_file():
                return p
        except OSError:
            return None
        if p.parent == p:
            return None
        p = p.parent
    return None


def resolve_department_work_path_for_preview(
    ref: "Asset | Shot",
    department: str | None,
    *,
    work_file_path: Path | None,
    item_root: Path,
    active_dcc_id: str | None,
) -> Path | None:
    """
    Folder used for render/preview/playblast sequence resolution.

    ``Department.work_path`` is a single primary folder per department (first / meta DCC), not the active
    badge DCC. When the active DCC has a resolved work file, that file's parent is the correct work root.
    When there is no file yet, resolve ``<dept>/<dcc>/work`` using project ``use_dcc_folders``.
    """
    dep = (department or "").strip()
    if not dep:
        return None
    fallback = dept_work_path_for_ref(ref, department)
    if work_file_path is not None:
        try:
            if work_file_path.is_file():
                return work_file_path.parent
        except OSError:
            pass
    adc = (active_dcc_id or "").strip()
    if not adc:
        return fallback
    dept_dir: Path | None = None
    for d in ref.departments:
        if (d.name or "").strip().casefold() == dep.casefold():
            dept_dir = d.path
            break
    if dept_dir is None:
        return fallback
    pr = _find_project_root_near_entity(item_root)
    try:
        from monostudio.core.dcc_registry import get_default_dcc_registry
        from monostudio.core.fs_reader import read_use_dcc_folders, resolve_work_path

        reg = get_default_dcc_registry()
        use_flag = read_use_dcc_folders(pr) if pr is not None else True
        return resolve_work_path(dept_dir, adc, use_flag, reg)
    except Exception:
        return fallback


def dept_work_path_for_ref(ref: "Asset | Shot", department: str | None) -> Path | None:
    dep = (department or "").strip().casefold()
    if not dep:
        return None
    for d in ref.departments:
        if (d.name or "").strip().casefold() == dep:
            return d.work_path
    return None


def primary_work_file_for_department(
    ref: "Asset | Shot",
    department: str,
    active_dcc_id: str | None,
) -> Path | None:
    dep_cf = (department or "").strip().casefold()
    if not dep_cf:
        return None
    states = getattr(ref, "dcc_work_states", ()) or ()
    adc = (active_dcc_id or "").strip().casefold()
    best: Path | None = None
    for (dept_id, dcc_id), state in states:
        if (dept_id or "").strip().casefold() != dep_cf:
            continue
        wp = getattr(state, "work_file_path", None)
        if isinstance(wp, Path) and wp.is_file():
            if adc and (dcc_id or "").strip().casefold() == adc:
                return wp
            if best is None:
                best = wp
    return best


def resolve_entity_thumbnail_source_path(
    item_root: Path,
    department: str | None,
    mode: str,
    work_path: Path | None,
    work_file_path: Path | None,
    *,
    sequence_ignore_extensions: frozenset[str] | None = None,
    sequence_ignore_name_tokens: frozenset[str] | None = None,
) -> Path | None:
    from monostudio.core.sequence_preview import (
        quick_sequence_preview_frame,
        resolve_best_available_sequence_folder,
        resolve_sequence_folder,
    )
    from monostudio.ui_qt.inspector_preview_settings import (
        THUMB_SOURCE_RENDER_SEQUENCE,
        THUMB_SOURCE_USER,
        THUMB_SOURCE_USER_THEN_RENDER,
    )
    from monostudio.ui_qt.thumbnails import resolve_thumbnail_path, resolve_user_only_thumbnail_path

    # User-only thumbs never depend on work file / active DCC; skip sequence I/O entirely.
    if mode == THUMB_SOURCE_USER:
        return resolve_user_only_thumbnail_path(item_root, department)

    seq_folder: Path | None = None
    rep: Path | None = None
    if work_path is not None and work_path.is_dir():
        seq_folder = resolve_sequence_folder(work_path, work_file_path)
        if seq_folder is not None:
            rep = quick_sequence_preview_frame(
                seq_folder,
                ignore_extensions=sequence_ignore_extensions,
                ignore_name_tokens=sequence_ignore_name_tokens,
            )
        if rep is None:
            best = resolve_best_available_sequence_folder(work_path)
            if best is not None:
                rep = quick_sequence_preview_frame(
                    best,
                    ignore_extensions=sequence_ignore_extensions,
                    ignore_name_tokens=sequence_ignore_name_tokens,
                )

    if mode == THUMB_SOURCE_RENDER_SEQUENCE:
        return rep
    if mode == THUMB_SOURCE_USER_THEN_RENDER:
        if rep is not None:
            return rep
        u = resolve_user_only_thumbnail_path(item_root, department)
        if u is not None:
            return u
        return resolve_thumbnail_path(item_root, department=department)
    return resolve_thumbnail_path(item_root, department=department)


def resolve_grid_thumbnail_file(
    item_root: Path,
    department: str | None,
    *,
    mode: str,
    pipeline_ref: "Asset | Shot | None",
    active_dcc_id: str | None,
    sequence_ignore_extensions: frozenset[str] | None = None,
    sequence_ignore_name_tokens: frozenset[str] | None = None,
) -> Path | None:
    """Main-view grid/list: same rules as Inspector when ref is Asset/Shot; else classic meta thumb."""
    from monostudio.core.models import Asset, Shot
    from monostudio.ui_qt.inspector_preview_settings import THUMB_SOURCE_USER
    from monostudio.ui_qt.thumbnails import resolve_thumbnail_path, resolve_user_only_thumbnail_path

    if not isinstance(pipeline_ref, (Asset, Shot)):
        return resolve_thumbnail_path(item_root, department=department)
    dep = (department or "").strip()
    if mode == THUMB_SOURCE_USER:
        return resolve_user_only_thumbnail_path(item_root, dep or None)
    wf = primary_work_file_for_department(pipeline_ref, dep, active_dcc_id) if dep else None
    wp = (
        resolve_department_work_path_for_preview(
            pipeline_ref,
            department,
            work_file_path=wf,
            item_root=item_root,
            active_dcc_id=active_dcc_id,
        )
        if dep
        else None
    )
    return resolve_entity_thumbnail_source_path(
        item_root,
        department,
        mode,
        wp,
        wf,
        sequence_ignore_extensions=sequence_ignore_extensions,
        sequence_ignore_name_tokens=sequence_ignore_name_tokens,
    )
