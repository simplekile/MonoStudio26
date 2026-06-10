"""Shared last-modified / mtime display for asset & shot tiles, list, and Inspector."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from monostudio.core.fs_reader import Asset, Shot
from monostudio.ui_qt.view_items import ViewItem


def format_mtime_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def mtime_display_for_path(path: Path) -> str:
    try:
        if path.is_dir() or path.is_file():
            mtime = path.stat().st_mtime
        else:
            return "—"
    except OSError:
        return "—"
    return format_mtime_ts(mtime)


def mtime_ts_for_publish_version_folder(folder: Path) -> float:
    """Folder mtime plus immediate children (publish drops inside version folder)."""
    try:
        best = folder.stat().st_mtime
    except OSError:
        return 0.0
    try:
        for ch in folder.iterdir():
            try:
                best = max(best, ch.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return best


def mtime_display_for_publish_version_folder(folder: Path) -> str:
    try:
        if not folder.exists():
            return "—"
    except OSError:
        return "—"
    ts = mtime_ts_for_publish_version_folder(folder)
    if ts <= 0:
        return "—"
    return format_mtime_ts(ts)


def path_last_modified_display(path: Path) -> str:
    """Display mtime for an arbitrary path (file, folder, or publish version folder)."""
    try:
        if not path.exists():
            return "—"
    except OSError:
        return "—"
    if path.is_dir():
        return mtime_display_for_publish_version_folder(path)
    return mtime_display_for_path(path)


def _resolve_publish_department(ref: Asset | Shot, active_department: str | None):
    dep = (active_department or "").strip()
    departments = getattr(ref, "departments", ()) or ()
    if dep:
        for d in departments:
            if (d.name or "").strip().casefold() == dep.casefold() and (
                getattr(d, "publish_version_count", 0) or 0
            ) > 0:
                return d
        return None
    for d in departments:
        if (getattr(d, "publish_version_count", 0) or 0) > 0:
            return d
    return None


def resolve_latest_publish_folder(ref: Asset | Shot, active_department: str | None) -> Path | None:
    dept = _resolve_publish_department(ref, active_department)
    if dept is None:
        return None
    ver = getattr(dept, "latest_publish_version", None)
    if not ver:
        return None
    return Path(dept.publish_path) / ver


def latest_work_mtime_for_department(
    ref: Asset | Shot,
    active_department: str,
    *,
    active_dcc_id: str | None = None,
) -> float | None:
    dep_cf = (active_department or "").strip().casefold()
    if not dep_cf:
        return None
    best: float | None = None
    dcc_cf = (active_dcc_id or "").strip().casefold() if active_dcc_id else None
    for (dept_id, dcc_id), state in getattr(ref, "dcc_work_states", ()) or ():
        if (dept_id or "").strip().casefold() != dep_cf:
            continue
        if dcc_cf and (dcc_id or "").strip().casefold() != dcc_cf:
            continue
        wp = getattr(state, "work_file_path", None)
        if not isinstance(wp, Path) or not wp.is_file():
            continue
        try:
            ts = wp.stat().st_mtime
        except OSError:
            continue
        if best is None or ts > best:
            best = ts
    return best


def latest_work_mtime_any(ref: Asset | Shot) -> float | None:
    best: float | None = None
    for (_dept_id, _dcc_id), state in getattr(ref, "dcc_work_states", ()) or ():
        wp = getattr(state, "work_file_path", None)
        if not isinstance(wp, Path) or not wp.is_file():
            continue
        try:
            ts = wp.stat().st_mtime
        except OSError:
            continue
        if best is None or ts > best:
            best = ts
    return best


def view_item_last_updated_ts(
    item: ViewItem,
    *,
    show_publish: bool,
    active_department: str | None,
    active_dcc_id: str | None = None,
) -> float:
    """
    Numeric mtime for sort / display.
    Work mode: active dept work file (optional DCC), else newest work file, else entity root.
    Published mode: latest publish version folder (+ children).
    """
    ref = item.ref
    if show_publish and isinstance(ref, (Asset, Shot)):
        pub = resolve_latest_publish_folder(ref, active_department)
        if pub is None or not pub.exists():
            return 0.0
        return mtime_ts_for_publish_version_folder(pub)

    if isinstance(ref, (Asset, Shot)):
        dep = (active_department or "").strip()
        if dep:
            ts = latest_work_mtime_for_department(ref, dep, active_dcc_id=active_dcc_id)
            if ts is not None:
                return ts
        ts = latest_work_mtime_any(ref)
        if ts is not None:
            return ts

    path = getattr(item, "path", None)
    if not path or not isinstance(path, Path):
        return 0.0
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def view_item_last_updated_display(
    item: ViewItem,
    *,
    show_publish: bool,
    active_department: str | None,
    active_dcc_id: str | None = None,
) -> str:
    ts = view_item_last_updated_ts(
        item,
        show_publish=show_publish,
        active_department=active_department,
        active_dcc_id=active_dcc_id,
    )
    if ts <= 0:
        return "—"
    return format_mtime_ts(ts)
