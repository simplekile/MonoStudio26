"""Resolve entity thumbnail file path from user thumbs vs work sequence roots (shared Inspector + main grid)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monostudio.core.models import Asset, Shot


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
) -> Path | None:
    from monostudio.core.sequence_preview import (
        list_sequence_frames,
        representative_frame_path,
        resolve_sequence_folder,
    )
    from monostudio.ui_qt.inspector_preview_settings import (
        THUMB_SOURCE_RENDER_SEQUENCE,
        THUMB_SOURCE_USER,
        THUMB_SOURCE_USER_THEN_RENDER,
    )
    from monostudio.ui_qt.thumbnails import resolve_thumbnail_path, resolve_user_only_thumbnail_path

    seq_folder: Path | None = None
    rep: Path | None = None
    if work_path is not None and work_path.is_dir():
        seq_folder = resolve_sequence_folder(work_path, work_file_path)
        if seq_folder is not None:
            frames = list_sequence_frames(seq_folder)
            rep = representative_frame_path(frames)

    if mode == THUMB_SOURCE_USER:
        return resolve_user_only_thumbnail_path(item_root, department)
    if mode == THUMB_SOURCE_RENDER_SEQUENCE:
        return rep
    if mode == THUMB_SOURCE_USER_THEN_RENDER:
        u = resolve_user_only_thumbnail_path(item_root, department)
        if u is not None:
            return u
        if rep is not None:
            return rep
        return resolve_thumbnail_path(item_root, department=department)
    return resolve_thumbnail_path(item_root, department=department)


def resolve_grid_thumbnail_file(
    item_root: Path,
    department: str | None,
    *,
    mode: str,
    pipeline_ref: "Asset | Shot | None",
    active_dcc_id: str | None,
) -> Path | None:
    """Main-view grid/list: same rules as Inspector when ref is Asset/Shot; else classic meta thumb."""
    from monostudio.core.models import Asset, Shot
    from monostudio.ui_qt.thumbnails import resolve_thumbnail_path

    if not isinstance(pipeline_ref, (Asset, Shot)):
        return resolve_thumbnail_path(item_root, department=department)
    dep = (department or "").strip()
    wp = dept_work_path_for_ref(pipeline_ref, department)
    wf = primary_work_file_for_department(pipeline_ref, dep, active_dcc_id) if dep else None
    return resolve_entity_thumbnail_source_path(item_root, department, mode, wp, wf)
