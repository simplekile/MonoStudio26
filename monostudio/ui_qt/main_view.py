from __future__ import annotations

import json
import shutil
import time
from datetime import date
from pathlib import Path
from typing import Callable, Literal, NamedTuple

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QPersistentModelIndex,
    QModelIndex,
    QMimeData,
    QPoint,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
    QUrl,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QDrag,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QApplication,
    QPushButton,
    QRadioButton,
    QRubberBand,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStackedWidget,
    QTableView,
    QSlider,
    QStyleOptionViewItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.pipeline_view_models import (
    PIPELINE_VIEW_THUMB_STATE_ROLE,
    PipelineTableModel,
    PipelineTileModel,
)
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind, display_name_for_item
from monostudio.ui_qt.view_item_mtime import (
    format_mtime_ts as _format_mtime,
    latest_work_mtime_for_department as _latest_work_mtime_for_department,
    mtime_display_for_path as _mtime_display_for_path,
    mtime_display_for_publish_version_folder as _mtime_display_for_publish_version_folder,
    view_item_last_updated_display as _view_item_last_updated_display,
    view_item_last_updated_ts as _view_item_mtime_sort_ts,
)
from monostudio.ui_qt.thumbnails import ThumbnailCache
from monostudio.ui_qt.inspector_preview_settings import (
    THUMB_SOURCE_RENDER_SEQUENCE,
    THUMB_SOURCE_USER,
    THUMB_SOURCE_USER_THEN_RENDER,
    read_inspector_thumbnail_source,
    write_inspector_thumbnail_source,
)
from monostudio.ui_qt.inbox_list_row_paint import paint_inbox_list_row_chrome
from monostudio.ui_qt.page_loading_bar import MainViewLoadingPlaceholder, is_scanning_empty_message
from monostudio.ui_qt.style import (
    CARD_THUMB_DEPT_BADGE_ICON_COLOR,
    CARD_THUMB_TYPE_BADGE_ICON_COLOR,
    MONOS_COLORS,
    MonosMenu,
    THUMB_TAG_STYLE,
    clear_stuck_widget_hover,
    monos_font,
    page_badge_accent_color,
)
from monostudio.ui_qt.toolbar_separators import add_widgets_with_icon_separators, vertical_icon_separator
from monostudio.ui_qt.production_status_menu import _menu_status_dot_icon
from monostudio.ui_qt.production_status_menu import pick_production_status_at
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import position_popup_near_anchor
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.dcc_status import resolve_dcc_status
from monostudio.core.fs_reader import (
    _parse_workfile_version,
    list_work_file_versions,
    read_use_dcc_folders,
    resolve_work_path,
    work_file_prefix,
)
from monostudio.core.workspace_reader import (
    ProjectQuickStats,
    project_status_color_hex,
    project_status_display_labels,
    project_status_label,
)
from monostudio.core.entity_folders import (
    ensure_entity_special_folder,
    entity_has_concept_files,
    entity_has_reference_files,
    entity_special_folder_path,
)
from monostudio.core.models import Asset, Department, Shot
from monostudio.core.project_schedule import ProjectSchedule, entity_rel_path, read_project_schedule
from monostudio.core.schedule_planner import (
    BarStore,
    PlannedBar,
    list_due_display,
    merged_row_assignee_ids_from_bars,
    primary_bar_for_row,
    summarize_entity_schedule,
)
from monostudio.core.user_identity import resolve_assignee_display
from monostudio.core.department_status_registry import load_status_registry_for_department
from monostudio.core.production_status import (
    ProductionStatusRegistry,
    aggregate_status_id_for_item,
    color_hex_for_status_id,
    load_production_status_registry,
    style_key_for_status_id,
)

import logging
_dcc_debug_log = logging.getLogger("monostudio.dcc_debug")


def _status_registry_for_view(
    project_root: str | Path | None,
    active_department: str | None,
) -> ProductionStatusRegistry:
    """Registry for status pill / dot: dept-specific when a department is focused."""
    pr = Path(project_root) if project_root else None
    dep = (active_department or "").strip()
    if dep:
        return load_status_registry_for_department(pr, dep)
    return load_production_status_registry(pr)

# --- Grid card: meta block under 16:9 thumb (one template, same height for every card) ---
_GRID_META_PAD_TOP = 16
_GRID_NAME_LINE_H = 20
_GRID_GAP_NAME_TO_META = 4
_GRID_META_LINE_H = 16
_GRID_GAP_BETWEEN_META_LINES = 4
_GRID_META_PAD_BOTTOM = 16  # breathing below last meta line when no department status pill
_GRID_GAP_META_TO_PILL = 4
_GRID_META_PAD_BOTTOM_WITH_PILL = 20  # breathing below status pill row


def _normalize_browser_context_title(title: str) -> str:
    """Main view header: match nav page labels (Assets / Shots / Projects)."""
    key = (title or "").strip().lower()
    return {
        "assets": "Assets",
        "asset": "Assets",
        "shots": "Shots",
        "shot": "Shots",
        "projects": "Projects",
        "project": "Projects",
    }.get(key, (title or "").strip())


def _browser_context_badge_icon(browser_context: str) -> str:
    ctx = (browser_context or "").strip().lower()
    if ctx == "shot":
        return "clapperboard"
    if ctx == "asset":
        return "box"
    return "layout-dashboard"


def _make_main_view_title_chevron(parent: QWidget) -> QLabel:
    lbl = QLabel(parent)
    lbl.setObjectName("MainViewTitleChevron")
    lbl.setFixedSize(16, 16)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setVisible(False)
    chev = lucide_icon("chevron-right", size=14, color_hex=MONOS_COLORS["text_label"])
    if not chev.isNull():
        lbl.setPixmap(chev.pixmap(14, 14))
    return lbl


def _grid_status_pill_line_height(fm: QFontMetrics) -> int:
    return max(16, fm.height() + 4)


def _grid_asset_shot_meta_text_block_height(n_lines: int) -> int:
    """Combined height of n mono meta lines (0..4) under the title, including gaps between lines."""
    if n_lines <= 0:
        return 0
    return n_lines * _GRID_META_LINE_H + (n_lines - 1) * _GRID_GAP_BETWEEN_META_LINES


def _grid_asset_shot_meta_head_to_first_line() -> int:
    """Distance from thumb.bottom() to top of first optional meta line."""
    return _GRID_META_PAD_TOP + _GRID_NAME_LINE_H + _GRID_GAP_NAME_TO_META


def _grid_asset_shot_y_pills_offset_from_thumb_bottom(*, n_meta_lines: int) -> int:
    """Distance from thumb.bottom() to top of production-status pill row."""
    return _grid_asset_shot_meta_head_to_first_line() + _grid_asset_shot_meta_text_block_height(n_meta_lines) + _GRID_GAP_META_TO_PILL


def grid_card_meta_block_height_project(*, pill_font_metrics: QFontMetrics | None = None) -> int:
    """Meta block under thumb for project cards (name + stats + status pill)."""
    head = (
        _GRID_META_PAD_TOP
        + _GRID_NAME_LINE_H
        + _GRID_GAP_NAME_TO_META
        + _GRID_META_LINE_H
    )
    pill_h = (
        _grid_status_pill_line_height(pill_font_metrics)
        if pill_font_metrics is not None
        else 22
    )
    return head + _GRID_GAP_META_TO_PILL + pill_h + _GRID_META_PAD_BOTTOM_WITH_PILL


def grid_card_meta_block_height_asset_shot(
    *,
    n_meta_lines: int,
    show_department_status_pill: bool,
    pill_font_metrics: QFontMetrics | None = None,
) -> int:
    """
    Meta block under thumb for asset/shot tiles: optional mono lines (ID/version, last updated,
    current department) + optional production-status pill. All cards share one height.
    """
    head = _grid_asset_shot_meta_head_to_first_line()
    text_h = _grid_asset_shot_meta_text_block_height(n_meta_lines)
    if not show_department_status_pill:
        return head + text_h + _GRID_META_PAD_BOTTOM
    pill_h = (
        _grid_status_pill_line_height(pill_font_metrics)
        if pill_font_metrics is not None
        else 22
    )
    return head + text_h + _GRID_GAP_META_TO_PILL + pill_h + _GRID_META_PAD_BOTTOM_WITH_PILL


def tile_grid_meta_line_count(
    *,
    show_id: bool,
    show_version: bool,
    show_last_updated: bool,
    show_latest_note: bool,
    show_current_department: bool,
    active_department: str | None,
) -> int:
    """How many mono meta lines to reserve under the title on asset/shot tiles (0..4)."""
    n = 0
    if show_id or show_version:
        n += 1
    if show_last_updated:
        n += 1
    if show_latest_note:
        n += 1
    if show_current_department and (active_department or "").strip():
        n += 1
    return n


# Lucide icon names for type/department thumb badges (icon-only, no text).
_TYPE_ICON_MAP: dict[str, str] = {
    "project": "layout-dashboard",
    "shot": "clapperboard",
    "_characters": "user",
    "character": "user",
    "_props": "package",
    "prop": "package",
    "_environment": "trees",
    "environment": "trees",
    "_vehicles": "car",
    "vehicle": "car",
}
_DEPT_ICON_MAP: dict[str, str] = {
    "layout": "layout-dashboard",
    "model": "box",
    "modeling": "box",
    "rig": "bone",
    "rigging": "bone",
    "surfacing": "palette",
    "grooming": "scissors",
    "lookdev": "sparkles",
    "anim": "spline",
    "animation": "spline",
    "fx": "zap",
    "groom": "scissors",
    "crowd": "user",
    "cloth": "layers",
    "pyro": "sun",
    "fluids": "wand",
    "destruction": "triangle-alert",
    "particles": "wand-sparkles",
    "lighting": "lightbulb",
    "comp": "sliders-horizontal",
}

# Labels for type badge tooltip (readable names).
_TYPE_TOOLTIP_MAP: dict[str, str] = {
    "project": "Project",
    "shot": "Shot",
    "character": "Character",
    "_characters": "Character",
    "prop": "Prop",
    "_props": "Prop",
    "environment": "Environment",
    "_environment": "Environment",
    "vehicle": "Vehicle",
    "_vehicles": "Vehicle",
}


def _work_file_version_from_path(path: Path) -> int | None:
    """Parse work file version from path stem (e.g. prefix_v003 -> 3). Returns int or None."""
    stem = (path.stem or "").strip()
    idx = stem.rfind("_v")
    if idx < 0 or len(stem) < idx + 5:
        return None
    mid = stem[idx + 2 : idx + 5]
    if len(mid) == 3 and mid.isdigit():
        return int(mid)
    return None


def _card_work_file_version(
    ref: Asset | Shot,
    active_department: str | None,
    active_dcc_id: str | None = None,
) -> str | None:
    """
    Work file version for card meta when a department is selected.
    When active_dcc_id is set, returns version for that DCC only; else max across all DCCs in department.
    Returns None when department is "all" → caller should hide version.
    Returns "v001" or "—" when department is set.
    """
    dep = (active_department or "").strip()
    if not dep:
        return None
    states = getattr(ref, "dcc_work_states", ()) or ()
    max_ver: int | None = None
    for (dept_id, dcc_id), state in states:
        if (dept_id or "").strip().casefold() != dep.casefold():
            continue
        if active_dcc_id is not None and (dcc_id or "").strip().casefold() != (active_dcc_id or "").strip().casefold():
            continue
        path = getattr(state, "work_file_path", None)
        if path is None:
            continue
        v = _work_file_version_from_path(path)
        if v is not None and (max_ver is None or v > max_ver):
            max_ver = v
    if max_ver is not None:
        return f"v{max_ver:03d}"
    return "—"


def _card_publish_version(ref: Asset | Shot, active_department: str | None) -> str | None:
    """
    Published version for card meta when a department is selected.
    Returns None when department is "all". Returns "v001" or "—" when department is set.
    """
    dep = (active_department or "").strip()
    if not dep:
        return None
    for d in getattr(ref, "departments", ()) or ():
        if (d.name or "").strip().casefold() != dep.casefold():
            continue
        v = getattr(d, "latest_publish_version", None) or ""
        if v and len(v) >= 4 and v[0].lower() == "v" and v[1:4].isdigit():
            return v
        return "—"
    return "—"


def _card_version_for_display(
    ref: Asset | Shot,
    active_department: str | None,
    show_publish: bool,
    active_dcc_id: str | None = None,
) -> str | None:
    """Version string for card: work file version or published version according to show_publish.
    When not in publish mode, active_dcc_id (if set) is used to show version for the selected DCC."""
    if show_publish:
        return _card_publish_version(ref, active_department)
    return _card_work_file_version(ref, active_department, active_dcc_id)


def _item_has_publish_for_department(ref: Asset | Shot, active_department: str | None) -> bool:
    """True if the item has at least one publish version. Checks specific dept or any dept if none given."""
    dep = (active_department or "").strip()
    departments = getattr(ref, "departments", ()) or ()
    if not dep:
        return any((getattr(d, "publish_version_count", 0) or 0) > 0 for d in departments)
    for d in departments:
        if (d.name or "").strip().casefold() != dep.casefold():
            continue
        return (getattr(d, "publish_version_count", 0) or 0) > 0
    return False


def _item_has_work_for_department(ref: Asset | Shot, active_department: str | None) -> bool:
    """True if the item has at least one work file for the department (from dcc_work_states)."""
    dep = (active_department or "").strip().casefold()
    if not dep:
        return False
    states = getattr(ref, "dcc_work_states", ()) or ()
    for (dept_id, _dcc_id), state in states:
        if (dept_id or "").strip().casefold() != dep:
            continue
        wp = getattr(state, "work_file_path", None)
        if isinstance(wp, Path) and wp.is_file():
            return True
    return False


def _item_has_work_folder_for_department(ref: Asset | Shot, active_department: str | None) -> bool:
    """True if the department work folder exists on disk (Department.work_exists from scan)."""
    dep_cf = (active_department or "").strip().casefold()
    if not dep_cf:
        return False
    for d in getattr(ref, "departments", ()) or ():
        if (d.name or "").strip().casefold() != dep_cf:
            continue
        if getattr(d, "work_exists", False):
            return True
    return False


_THUMB_HEALTH_ICON_PX = 14
_THUMB_HEALTH_CHIP_PAD_PX = 4
_ITEM_HEALTH_COLORS: dict[str, str] = {
    "ok": MONOS_COLORS["emerald_500"],
    "warn": MONOS_COLORS["amber_500"],
    "error": MONOS_COLORS["red_500"],
}


class HealthIssue(NamedTuple):
    level: Literal["ok", "warn", "error"]
    title: str
    detail: str
    bad_files: tuple[str, ...] = ()
    bad_files_wrong_name: tuple[str, ...] = ()
    bad_files_wrong_ext: tuple[str, ...] = ()
    issue_id: str = ""


class ItemHealth(NamedTuple):
    level: Literal["ok", "warn", "error"]
    icon_name: str
    color_hex: str
    issues: tuple[HealthIssue, ...]


def _department_for_item(ref: Asset | Shot, active_department: str) -> Department | None:
    dep_cf = (active_department or "").strip().casefold()
    if not dep_cf:
        return None
    for d in getattr(ref, "departments", ()) or ():
        if (d.name or "").strip().casefold() == dep_cf:
            return d
    return None


def _workfile_extensions_set() -> frozenset[str]:
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


def _work_paths_for_department(ref: Asset | Shot, dept: Department) -> list[Path]:
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


def _invalid_work_files_split_in_folder(
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


def _invalid_work_files_in_folder(
    work_path: Path,
    prefix: str,
    work_exts: frozenset[str],
) -> list[Path]:
    a, b = _invalid_work_files_split_in_folder(work_path, prefix, work_exts)
    merged = a + b
    merged.sort(key=lambda path: path.name.casefold())
    return merged


def _split_invalid_work_files_for_department(
    ref: Asset | Shot,
    dept: Department,
    prefix: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Returns (all paths, wrong_name paths, wrong_ext paths) as strings, deduped."""
    work_exts = _workfile_extensions_set()
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

    for wp in _work_paths_for_department(ref, dept):
        n_bad, e_bad = _invalid_work_files_split_in_folder(wp, prefix, work_exts)
        for p in n_bad:
            add_unique(name_paths, p)
        for p in e_bad:
            add_unique(ext_paths, p)
    name_paths.sort(key=lambda s: Path(s).name.casefold())
    ext_paths.sort(key=lambda s: Path(s).name.casefold())
    all_paths = sorted(set(name_paths) | set(ext_paths), key=lambda s: Path(s).name.casefold())
    return tuple(all_paths), tuple(name_paths), tuple(ext_paths)


def _assess_work_naming_in_folder(
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


def _assess_work_naming_for_department(
    ref: Asset | Shot,
    dept: Department,
    prefix: str,
) -> Literal["ok", "warn", "error"]:
    work_exts = _workfile_extensions_set()
    worst: Literal["ok", "warn", "error"] = "ok"
    for wp in _work_paths_for_department(ref, dept):
        level = _assess_work_naming_in_folder(wp, prefix, work_exts)
        if level == "error":
            return "error"
        if level == "warn":
            worst = "warn"
    return worst


_HOUDINI_WORK_EXTS = frozenset({".hip", ".hiplc", ".hipnc"})


def _is_houdini_work_path(work_path: Path) -> bool:
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


def _department_has_houdini_work(ref: Asset | Shot, dept: Department) -> bool:
    dep_cf = (dept.name or "").strip().casefold()
    for (dept_id, dcc_id), _state in getattr(ref, "dcc_work_states", ()) or ():
        if (dept_id or "").strip().casefold() == dep_cf and (dcc_id or "").strip().casefold() == "houdini":
            return True
    for wp in _work_paths_for_department(ref, dept):
        if _is_houdini_work_path(wp):
            return True
    return False


def _houdini_backup_folder_paths_for_department(
    ref: Asset | Shot,
    dept: Department,
) -> tuple[str, ...]:
    """Non-empty Houdini ``backup/`` subfolders under department work paths."""
    dep_cf = (dept.name or "").strip().casefold()
    paths: list[str] = []
    seen: set[str] = set()

    def add_backup_dir(work_path: Path) -> None:
        if not _is_houdini_work_path(work_path):
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

    for wp in _work_paths_for_department(ref, dept):
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


def _latest_publish_mtime_for_department(ref: Asset | Shot, active_department: str) -> float | None:
    pub = _resolve_latest_publish_folder(ref, active_department)
    if pub is None or not pub.exists():
        return None
    try:
        best = pub.stat().st_mtime
    except OSError:
        return None
    try:
        for ch in pub.iterdir():
            try:
                best = max(best, ch.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return best


def _latest_work_file_path_for_department(
    ref: Asset | Shot,
    active_department: str,
    *,
    active_dcc_id: str | None = None,
) -> Path | None:
    dep_cf = (active_department or "").strip().casefold()
    if not dep_cf:
        return None
    best_path: Path | None = None
    best_ts: float | None = None
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
        if best_ts is None or ts >= best_ts:
            best_ts = ts
            best_path = wp
    return best_path


def assess_view_item_health(
    ref: Asset | Shot,
    active_department: str | None,
    *,
    active_dcc_id: str | None = None,
) -> ItemHealth | None:
    """
    Item health for the focused department: work file, publish, naming, work vs publish mtime.
    ok = green heart; warn/error = triangle-alert (amber/red).
    """
    dep = (active_department or "").strip()
    if not dep:
        return None
    dept = _department_for_item(ref, dep)
    if dept is None:
        return None

    issues: list[HealthIssue] = []
    prefix = work_file_prefix(name=ref.name, department=dept.name)
    has_work = _item_has_work_for_department(ref, dep)
    has_publish = _item_has_publish_for_department(ref, dep)
    work_path = _latest_work_file_path_for_department(ref, dep, active_dcc_id=active_dcc_id)
    work_ts = _latest_work_mtime_for_department(ref, dep, active_dcc_id=active_dcc_id)
    pub_ts = _latest_publish_mtime_for_department(ref, dep)
    pub_folder = _resolve_latest_publish_folder(ref, dep)

    if has_work:
        work_detail = str(work_path) if work_path else "Work file present"
        if work_ts is not None:
            work_detail = f"{work_detail}\nModified: {_format_mtime(work_ts)}"
        issues.append(HealthIssue("ok", "Work file", work_detail))
    else:
        issues.append(HealthIssue("warn", "Work file", "No work file found for this department."))

    if has_publish:
        pub_detail = str(pub_folder) if pub_folder else "Publish version present"
        if pub_ts is not None:
            pub_detail = f"{pub_detail}\nModified: {_format_mtime(pub_ts)}"
        issues.append(HealthIssue("ok", "Publish", pub_detail))
    elif has_work:
        issues.append(HealthIssue("warn", "Publish", "No publish version yet."))
    else:
        issues.append(HealthIssue("ok", "Publish", "No publish required until work exists."))

    if getattr(dept, "work_exists", False) or _work_paths_for_department(ref, dept):
        naming = _assess_work_naming_for_department(ref, dept, prefix)
        bad_all, bad_name, bad_ext = _split_invalid_work_files_for_department(ref, dept, prefix)
        expected = f"{prefix}_v###"
        work_exts = _workfile_extensions_set()
        ext_hint = ", ".join(sorted(work_exts)[:6])
        if len(work_exts) > 6:
            ext_hint += ", …"
        if naming == "error":
            detail = f"Files in work folder must match {expected} (plus a registered work extension).\n"
            if bad_name and bad_ext:
                detail += "Some entries have invalid names; others use a non-work extension."
            elif bad_ext:
                detail += f"Non-work extensions (not in DCC registry: {ext_hint}) can be removed with Clean."
            else:
                detail += "Use Fix name to rename files that use a work extension but the wrong stem or version."
            issues.append(
                HealthIssue(
                    "error",
                    "Work file naming",
                    detail,
                    bad_all,
                    bad_name,
                    bad_ext,
                    "work_file_naming",
                )
            )
        elif naming == "warn":
            detail = (
                f"Some files do not match {expected}; check for typos or autosaves.\n"
                f"Registered work extensions include {ext_hint}."
            )
            issues.append(
                HealthIssue(
                    "warn",
                    "Work file naming",
                    detail,
                    bad_all,
                    bad_name,
                    bad_ext,
                    "work_file_naming",
                )
            )
        else:
            issues.append(
                HealthIssue(
                    "ok",
                    "Work file naming",
                    f"Work files follow {expected}.",
                    issue_id="work_file_naming",
                )
            )

    if has_work and has_publish and work_ts is not None and pub_ts is not None:
        if work_ts > pub_ts:
            issues.append(
                HealthIssue(
                    "warn",
                    "Modified dates",
                    (
                        f"Work is newer than the latest publish.\n"
                        f"Work: {_format_mtime(work_ts)}\n"
                        f"Publish: {_format_mtime(pub_ts)}"
                    ),
                )
            )
        else:
            issues.append(
                HealthIssue(
                    "ok",
                    "Modified dates",
                    (
                        f"Publish is up to date with work.\n"
                        f"Work: {_format_mtime(work_ts)}\n"
                        f"Publish: {_format_mtime(pub_ts)}"
                    ),
                )
            )

    if _department_has_houdini_work(ref, dept):
        backup_paths = _houdini_backup_folder_paths_for_department(ref, dept)
        if backup_paths:
            if len(backup_paths) == 1:
                detail = (
                    "Houdini created an automatic backup folder in the work directory.\n"
                    f"{backup_paths[0]}\n"
                    "You can remove it with Clean if you do not need these copies."
                )
            else:
                detail = (
                    "Houdini backup folders found in work directories:\n"
                    + "\n".join(backup_paths)
                    + "\nYou can remove them with Clean if you do not need these copies."
                )
            issues.append(
                HealthIssue(
                    "warn",
                    "Houdini backup folder",
                    detail,
                    bad_files=backup_paths,
                    issue_id="houdini_backup_folder",
                )
            )
        else:
            issues.append(
                HealthIssue(
                    "ok",
                    "Houdini backup folder",
                    "No Houdini backup folder in work.",
                    issue_id="houdini_backup_folder",
                )
            )

    if any(i.level == "error" for i in issues):
        level: Literal["ok", "warn", "error"] = "error"
    elif any(i.level == "warn" for i in issues):
        level = "warn"
    else:
        level = "ok"

    if level == "ok":
        return ItemHealth(
            level="ok",
            icon_name="heart",
            color_hex=_ITEM_HEALTH_COLORS["ok"],
            issues=tuple(issues),
        )
    return ItemHealth(
        level=level,
        icon_name="triangle-alert",
        color_hex=_ITEM_HEALTH_COLORS[level],
        issues=tuple(issues),
    )


def _item_health_tooltip_text(health: ItemHealth) -> str:
    if health.level == "ok":
        return "Healthy — click for details"
    lines = [i.title for i in health.issues if i.level != "ok"]
    if not lines:
        return "Issues detected — click for details"
    return "\n".join(f"• {t}" for t in lines)


def _resolve_work_files_for_drag(ref: Asset | Shot, active_department: str | None) -> list[Path]:
    """Work file path(s) for drag in work mode; only actual files from dcc_work_states."""
    dep = (active_department or "").strip().casefold()
    if not dep:
        return []
    out: list[Path] = []
    seen: set[Path] = set()
    states = getattr(ref, "dcc_work_states", ()) or ()
    for (dept_id, _dcc_id), state in states:
        if (dept_id or "").strip().casefold() != dep:
            continue
        wp = getattr(state, "work_file_path", None)
        if isinstance(wp, Path) and wp.is_file() and wp not in seen:
            seen.add(wp)
            out.append(wp)
    return out


def _resolve_publish_department(ref: Asset | Shot, active_department: str | None):
    """Return the Department with a publish, respecting active filter. Returns None if nothing found."""
    dep = (active_department or "").strip()
    departments = getattr(ref, "departments", ()) or ()
    if dep:
        for d in departments:
            if (d.name or "").strip().casefold() == dep.casefold() and (getattr(d, "publish_version_count", 0) or 0) > 0:
                return d
        return None
    for d in departments:
        if (getattr(d, "publish_version_count", 0) or 0) > 0:
            return d
    return None


def _resolved_work_path_for_copy(
    ref: Asset | Shot, department: str, active_dcc_id: str | None = None
) -> Path | None:
    """
    Path to copy for "Copy Work Path": work file path if it exists, else work folder.
    Uses dcc_work_states when available; falls back to department work_path.
    """
    dep = (department or "").strip().casefold()
    if not dep:
        return None
    dept_obj = None
    for d in getattr(ref, "departments", ()) or ():
        if (d.name or "").strip().casefold() == dep:
            dept_obj = d
            break
    if dept_obj is None:
        return None
    # Prefer actual work file path from scan (any DCC for this department, or active DCC)
    states = getattr(ref, "dcc_work_states", ()) or ()
    for (dept_id, dcc_id), state in states:
        if (dept_id or "").strip().casefold() != dep:
            continue
        if active_dcc_id and (dcc_id or "").strip().casefold() != (active_dcc_id or "").strip().casefold():
            continue
        wp = getattr(state, "work_file_path", None)
        if isinstance(wp, Path) and wp.is_file():
            return wp
    # No file from scan: return work folder so user still gets a usable path
    return Path(dept_obj.work_path)


def _resolve_work_root_folder_any(ref: Asset | Shot, active_department: str | None) -> Path | None:
    """
    Path to work root folder (e.g. <dept>/work/).
    Uses active department if set, otherwise falls back to the first department.
    """
    dep = (active_department or "").strip()
    departments = getattr(ref, "departments", ()) or ()
    if dep:
        for d in departments:
            if (d.name or "").strip().casefold() == dep.casefold():
                return Path(d.work_path)
        return None
    if departments:
        return Path(departments[0].work_path)
    return None


def _resolve_work_file_for_department_dcc(ref: Asset | Shot, department: str, dcc_id: str) -> Path | None:
    """Resolve actual work file path from scan states (preferred source of truth)."""
    dep = (department or "").strip().casefold()
    dcc = (dcc_id or "").strip().casefold()
    if not dep or not dcc:
        return None
    states = getattr(ref, "dcc_work_states", ()) or ()
    for (dept_id, state_dcc_id), state in states:
        if (dept_id or "").strip().casefold() != dep:
            continue
        if (state_dcc_id or "").strip().casefold() != dcc:
            continue
        wp = getattr(state, "work_file_path", None)
        if isinstance(wp, Path) and wp.is_file():
            return wp
    return None


def _next_workfile_version_path(work_path: Path, prefix: str, dcc_id: str, ext: str) -> Path:
    """
    Compute next version path under work_path using existing versions for dcc_id.
    Uses ext from source path (e.g. '.blend').
    """
    ext = (ext or "").strip()
    if ext and not ext.startswith("."):
        ext = "." + ext
    try:
        reg = get_default_dcc_registry()
        versions = list_work_file_versions(work_path, prefix, dcc_id, reg)
        max_ver = max((v for v, _p in versions if isinstance(v, int)), default=0)
    except Exception:
        max_ver = 0
    next_ver = max_ver + 1 if max_ver >= 1 else 1
    safe_prefix = prefix or "unnamed"
    return work_path / f"{safe_prefix}_v{next_ver:03d}{ext}"


def _resolve_latest_publish_folder(ref: Asset | Shot, active_department: str | None) -> Path | None:
    """Path to the latest publish version folder (e.g. <dept>/publish/v003/)."""
    dept = _resolve_publish_department(ref, active_department)
    if dept is None:
        return None
    ver = getattr(dept, "latest_publish_version", None)
    if not ver:
        return None
    return Path(dept.publish_path) / ver


def _resolve_publish_root_folder(ref: Asset | Shot, active_department: str | None) -> Path | None:
    """Path to the publish root folder (e.g. <dept>/publish/). Only returns a department that has publish versions."""
    dept = _resolve_publish_department(ref, active_department)
    if dept is None:
        return None
    return Path(dept.publish_path)


def _resolve_publish_root_folder_any(ref: Asset | Shot, active_department: str | None) -> Path | None:
    """Path to the publish root folder (e.g. <dept>/publish/). Uses active or first department even if no versions yet."""
    dep = (active_department or "").strip()
    departments = getattr(ref, "departments", ()) or ()
    if dep:
        for d in departments:
            if (d.name or "").strip().casefold() == dep.casefold():
                return Path(d.publish_path)
        return None
    if departments:
        return Path(departments[0].publish_path)
    return None


_PREVIEW_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".tga", ".bmp", ".tiff", ".tif"})

# Setting: file extensions to ignore when listing files in publish version folders (comma-separated in UI).
SETTINGS_KEY_PUBLISH_IGNORE_EXT = "pipeline/publish_ignore_extensions"
DEFAULT_PUBLISH_IGNORE_EXTENSIONS = ".tmp,.bak,.mtl,.mb.bak,.ma.bak,.blend1,Thumbs.db,.DS_Store"


def get_publish_ignore_extensions(settings: QSettings | None) -> frozenset[str]:
    """Parse pipeline/publish_ignore_extensions from QSettings; returns normalized set (lowercase, leading dot)."""
    raw = (DEFAULT_PUBLISH_IGNORE_EXTENSIONS if settings is None else
           (settings.value(SETTINGS_KEY_PUBLISH_IGNORE_EXT, DEFAULT_PUBLISH_IGNORE_EXTENSIONS, str) or DEFAULT_PUBLISH_IGNORE_EXTENSIONS))
    result: set[str] = set()
    for part in (raw or "").split(","):
        ext = (part or "").strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        result.add(ext)
    return frozenset(result)


def _resolve_primary_publish_file(
    ref: Asset | Shot,
    active_department: str | None,
    *,
    ignore_extensions: frozenset[str] | None = None,
) -> Path | None:
    """
    Primary file inside the latest publish version folder.
    Prefers non-preview files; falls back to first file alphabetically.
    Returns None if folder is empty or doesn't exist.
    """
    folder = _resolve_latest_publish_folder(ref, active_department)
    if folder is None:
        return None
    try:
        files = sorted(f for f in folder.iterdir() if f.is_file())
    except (OSError, FileNotFoundError):
        return None
    if ignore_extensions:
        files = [f for f in files if (f.suffix or "").strip().lower() not in ignore_extensions]
    if not files:
        return None
    non_preview = [f for f in files if f.suffix.lower() not in _PREVIEW_EXTENSIONS]
    return non_preview[0] if non_preview else files[0]


def _resolve_all_publish_files(
    ref: Asset | Shot,
    active_department: str | None,
    *,
    ignore_extensions: frozenset[str] | None = None,
) -> list[Path]:
    """All files inside the latest publish version folder (for drag & drop). Excludes extensions in ignore_extensions."""
    folder = _resolve_latest_publish_folder(ref, active_department)
    if folder is None:
        return []
    try:
        files = sorted(f for f in folder.iterdir() if f.is_file())
    except (OSError, FileNotFoundError):
        return []
    if ignore_extensions:
        files = [f for f in files if (f.suffix or "").strip().lower() not in ignore_extensions]
    return files


def _open_metadata_path(item_path: Path) -> Path:
    """Single source for item open.json path. Used by main_view, app_controller, inspector."""
    return item_path / ".monostudio" / "open.json"


def _item_last_opened_dcc(item_path: Path, active_department: str) -> str | None:
    """Read last-opened DCC for this item from .monostudio/open.json. Returns dcc_id or None."""
    if not item_path or not isinstance(item_path, Path):
        return None
    meta_path = _open_metadata_path(item_path)
    try:
        if not meta_path.is_file():
            return None
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    dep = (active_department or "").strip().casefold()
    by_dep = data.get("last_open_by_department")
    if isinstance(by_dep, dict):
        node = by_dep.get(dep) or by_dep.get(active_department)
        if isinstance(node, dict):
            dcc = node.get("dcc")
            if isinstance(dcc, str) and dcc.strip():
                return dcc.strip()
    last_open = data.get("last_open")
    if isinstance(last_open, dict) and (last_open.get("department") or "").strip().casefold() == dep:
        dcc = last_open.get("dcc")
        if isinstance(dcc, str) and dcc.strip():
            return dcc.strip()
    return None


def _thumb_badge_rects(cell_rect: QRect, gap_px: int, has_dept: bool) -> tuple[QRect, QRect | None]:
    """Compute type and department badge rects (matches delegate layout). Used for tooltip hit-test."""
    r = cell_rect.adjusted(0, 0, -gap_px, -gap_px)
    border_px = 1
    inner = r.adjusted(border_px, border_px, -border_px, -border_px)
    thumb_w = inner.width()
    thumb_h = max(1, int(thumb_w * 9 / 16))
    thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))
    chip_r = (16 + 5 * 2) // 2
    chip_h = chip_r * 2
    gap = 4
    ix, iy = thumb.left() + 12, thumb.top() + 12
    type_rect = QRect(ix, iy, chip_h, chip_h)
    dept_rect = QRect(ix + chip_h + gap, iy, chip_h, chip_h) if has_dept else None
    return type_rect, dept_rect


def _thumb_rect_from_cell(cell_rect: QRect, gap_px: int) -> QRect:
    """16:9 thumb rect inside a grid cell (1px card border; matches health hit-test geometry)."""
    g = max(0, int(gap_px))
    r = cell_rect.adjusted(0, 0, -g, -g)
    border_px = 1
    inner = r.adjusted(border_px, border_px, -border_px, -border_px)
    thumb_w = inner.width()
    thumb_h = max(1, int(thumb_w * 9 / 16))
    return QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))


def _thumb_health_chip_rect(cell_rect: QRect, gap_px: int) -> QRect:
    """Top-right health icon chip on thumb (matches grid delegate layout). Used for tooltip hit-test."""
    thumb = _thumb_rect_from_cell(cell_rect, gap_px)
    chip = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
    return QRect(thumb.right() - 12 - chip, thumb.top() + 12, chip, chip)


def _thumb_note_chip_rect(thumb: QRect, health_chip_rect: QRect | None) -> QRect:
    """Notes chip to the left of the health chip when present; else same slot as health (top-right)."""
    chip = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
    gap = 4
    if health_chip_rect is not None:
        return QRect(health_chip_rect.left() - gap - chip, health_chip_rect.top(), chip, chip)
    return QRect(thumb.right() - 12 - chip, thumb.top() + 12, chip, chip)


def _list_thumb_dest_rect(cell_rect: QRect, icon: QIcon) -> QRect | None:
    """Thumbnail hit area: full thumb column cell (cover paint fills the rect)."""
    if icon.isNull() or cell_rect.width() <= 0 or cell_rect.height() <= 0:
        return None
    return QRect(cell_rect)


def _list_thumb_cover_paint(painter: QPainter, cell_rect: QRect, icon: QIcon) -> bool:
    """Draw list thumb with object-fit: cover — fill cell, center-crop overflow."""
    if icon.isNull() or cell_rect.width() <= 0 or cell_rect.height() <= 0:
        return False
    cell_w = cell_rect.width()
    cell_h = cell_rect.height()
    actual = icon.actualSize(QSize(cell_w, cell_h))
    pw = actual.width() or cell_w * 2
    ph = actual.height() or cell_h * 2
    pix = icon.pixmap(pw, ph)
    if pix.isNull() or pix.width() <= 0 or pix.height() <= 0:
        return False
    scale = max(cell_w / pix.width(), cell_h / pix.height())
    src_w = max(1, int(cell_w / scale))
    src_h = max(1, int(cell_h / scale))
    src_x = max(0, (pix.width() - src_w) // 2)
    src_y = max(0, (pix.height() - src_h) // 2)
    src_w = min(src_w, pix.width() - src_x)
    src_h = min(src_h, pix.height() - src_y)
    painter.drawPixmap(cell_rect, pix, QRect(src_x, src_y, src_w, src_h))
    return True


def _list_health_chip_rect(cell_rect: QRect) -> QRect:
    """Centered health icon chip in list Health column."""
    chip = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
    return QRect(
        cell_rect.left() + max(0, (cell_rect.width() - chip) // 2),
        cell_rect.top() + max(0, (cell_rect.height() - chip) // 2),
        chip,
        chip,
    )


def _list_header_column_width(header_label: str, *, min_content_px: int = 0) -> int:
    """Width for a fixed list column — fits uppercase header text (global QHeaderView QSS)."""
    font = monos_font("Inter", 12, QFont.Weight.ExtraBold)
    fm = QFontMetrics(font)
    text = header_label.upper()
    text_w = fm.horizontalAdvance(text) + max(0, len(text) - 1)  # letter-spacing: 1px
    # QHeaderView::section uses padding 10px 15px; +6 for section edge/grip.
    return max(min_content_px, text_w + 30 + 6)


_LIST_SPECIAL_FOLDER_ICON_PX = 16
_LIST_SPECIAL_FOLDER_CHIP_PAD_PX = 6


def _list_special_folder_chip_rect(cell_rect: QRect) -> QRect:
    chip = _LIST_SPECIAL_FOLDER_ICON_PX + _LIST_SPECIAL_FOLDER_CHIP_PAD_PX * 2
    return QRect(
        cell_rect.left() + max(0, (cell_rect.width() - chip) // 2),
        cell_rect.top() + max(0, (cell_rect.height() - chip) // 2),
        chip,
        chip,
    )


def _paint_list_special_folder_icon(
    painter: QPainter,
    chip_rect: QRect,
    icon_name: str,
    *,
    has_files: bool,
    hovered: bool,
) -> None:
    if hovered:
        painter.fillRect(chip_rect, QColor(255, 255, 255, 18))
    color = MONOS_COLORS["text_label"] if has_files else MONOS_COLORS["text_meta"]
    pix = lucide_icon(icon_name, size=_LIST_SPECIAL_FOLDER_ICON_PX, color_hex=color).pixmap(
        _LIST_SPECIAL_FOLDER_ICON_PX, _LIST_SPECIAL_FOLDER_ICON_PX
    )
    if pix.isNull():
        return
    x = chip_rect.x() + (chip_rect.width() - pix.width()) // 2
    y = chip_rect.y() + (chip_rect.height() - pix.height()) // 2
    painter.drawPixmap(x, y, pix)


def _paint_health_icon_chip(
    painter: QPainter,
    chip_rect: QRect,
    health: ItemHealth,
    *,
    hovered: bool,
) -> None:
    chip_bg = QColor(0, 0, 0, 220 if hovered else 168)
    if hovered:
        ring = QColor(health.color_hex)
        ring.setAlpha(230)
        painter.setPen(QPen(ring, 2))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(chip_bg)
    painter.drawEllipse(chip_rect)
    icon_px = _THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
    icon = lucide_icon(health.icon_name, size=icon_px, color_hex=health.color_hex)
    pix = icon.pixmap(icon_px, icon_px)
    if not pix.isNull():
        pad = max(2, _THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
        dest = chip_rect.adjusted(pad, pad, -pad, -pad)
        painter.drawPixmap(dest, pix)


def _notes_badge_tooltip_text(open_n: int, visual_mode: str) -> str:
    if visual_mode == "open" or open_n > 0:
        return f"Notes ({open_n} open)"
    if visual_mode == "all_done":
        return "Notes (all completed)"
    return "Notes"


def _paint_note_icon_chip(
    painter: QPainter,
    chip_rect: QRect,
    open_count: int,
    *,
    visual_mode: str = "empty",
    hovered: bool,
) -> None:
    """Notes on thumb: empty=muted; open=yellow+red count; all_done=green (history, none open)."""
    icon_name = "message-circle"
    if open_count > 0 or visual_mode == "open":
        chip_bg = QColor(234, 179, 8, 235 if hovered else 215)
        if hovered:
            ring = QColor(255, 255, 255, 140)
            painter.setPen(QPen(ring, 2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(chip_bg)
        painter.drawEllipse(chip_rect)
        icon_px = _THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
        icon = lucide_icon(icon_name, size=icon_px, color_hex="#18181b")
        pix = icon.pixmap(icon_px, icon_px)
        if not pix.isNull():
            pad = max(2, _THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
            dest = chip_rect.adjusted(pad, pad, -pad, -pad)
            painter.drawPixmap(dest, pix)
        label = "9+" if open_count > 9 else str(open_count)
        bf = monos_font("Inter", 9, QFont.Weight.Bold)
        painter.setFont(bf)
        fm = QFontMetrics(bf)
        pill_w = max(15, fm.horizontalAdvance(label) + 6)
        pill_h = max(15, fm.height())
        bx = chip_rect.right() - pill_w + 5
        by = chip_rect.top() - max(2, pill_h // 2 - 2)
        badge = QRect(bx, by, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#dc2626"))
        painter.drawRoundedRect(badge, 999, 999)
        painter.setPen(QColor("#fafafa"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        return

    if visual_mode == "all_done":
        em = QColor(MONOS_COLORS.get("emerald_500", "#10b981"))
        chip_bg = QColor(em)
        chip_bg.setAlpha(230 if hovered else 200)
        if hovered:
            ring = QColor(255, 255, 255, 130)
            painter.setPen(QPen(ring, 2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(chip_bg)
        painter.drawEllipse(chip_rect)
        icon_px = _THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
        icon = lucide_icon(icon_name, size=icon_px, color_hex="#fafafa")
        pix = icon.pixmap(icon_px, icon_px)
        if not pix.isNull():
            pad = max(2, _THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
            dest = chip_rect.adjusted(pad, pad, -pad, -pad)
            painter.drawPixmap(dest, pix)
        return

    chip_bg = QColor(0, 0, 0, 220 if hovered else 168)
    if hovered:
        ring = QColor(MONOS_COLORS.get("text_meta", "#a1a1aa"))
        ring.setAlpha(200)
        painter.setPen(QPen(ring, 2))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(chip_bg)
    painter.drawEllipse(chip_rect)
    icon_px = _THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
    icon = lucide_icon(icon_name, size=icon_px, color_hex=MONOS_COLORS.get("text_meta", "#a1a1aa"))
    pix = icon.pixmap(icon_px, icon_px)
    if not pix.isNull():
        pad = max(2, _THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
        dest = chip_rect.adjusted(pad, pad, -pad, -pad)
        painter.drawPixmap(dest, pix)


def _overall_status_paint_key_for_item(
    item: ViewItem,
    active_department: str | None,
    *,
    project_root: str | None = None,
    hidden_departments: set[str] | None = None,
) -> str:
    """Paint key for status dot: ready|progress|waiting|blocked|review|hold|na (grid/list/tooltip)."""
    if item.kind.value == "project":
        stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
        from monostudio.core.workspace_reader import project_status_paint_key

        return project_status_paint_key(getattr(stats, "status", None) or "WAITING")
    ref = item.ref
    if isinstance(ref, (Asset, Shot)):
        try:
            reg = _status_registry_for_view(project_root, active_department)
            sid = aggregate_status_id_for_item(
                ref,
                active_department=active_department,
                hidden_departments=hidden_departments or set(),
                registry=reg,
            )
            return style_key_for_status_id(sid, reg)
        except Exception:
            pass
        dep = (active_department or "").strip()
        if dep:
            for d in ref.departments:
                if (d.name or "").strip() == dep:
                    if d.publish_version_count > 0:
                        return "ready"
                    if d.work_exists:
                        return "progress"
                    return "waiting"
            return "waiting"
        depts = ref.departments
        if depts and all(d.publish_version_count > 0 for d in depts):
            return "ready"
        if any(d.work_exists for d in depts):
            return "progress"
        return "waiting"
    return "waiting"


def _overall_status_tooltip_label_for_item(
    item: ViewItem,
    active_department: str | None,
    *,
    project_root: str | None = None,
    hidden_departments: set[str] | None = None,
) -> str:
    """Human-readable status for tooltips (preset label)."""
    if item.kind.value == "project":
        stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
        return project_status_label(getattr(stats, "status", None) or "WAITING")
    ref = item.ref
    if isinstance(ref, (Asset, Shot)):
        try:
            reg = _status_registry_for_view(project_root, active_department)
            sid = aggregate_status_id_for_item(
                ref,
                active_department=active_department,
                hidden_departments=hidden_departments or set(),
                registry=reg,
            )
            return reg.label_for(sid)
        except Exception:
            pass
    key = _overall_status_paint_key_for_item(
        item,
        active_department,
        project_root=project_root,
        hidden_departments=hidden_departments,
    )
    if key == "ready":
        return "Published"
    if key == "progress":
        return "Working"
    if key == "blocked":
        return "Blocked"
    return "Waiting"


def _dcc_ids_for_item(
    item: ViewItem,
    active_department: str | None,
    *,
    dept_registry: DepartmentRegistry | None = None,
) -> list[tuple[str, str]]:
    """Return [(dcc_id, status), ...] for the item's DCC badges (same logic as paint).
    status is "exists" or "creating". Only includes badges matching the active department."""
    ref = item.ref
    if not isinstance(ref, (Asset, Shot)):
        return []
    try:
        reg = get_default_dcc_registry()
    except Exception:
        return []
    _norm = lambda s: (s or "").strip().casefold()
    active_key = _norm(active_department)
    states = getattr(ref, "dcc_work_states", ()) or ()
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []

    def add(dept_id: str, dcc_id: str, status: str) -> None:
        if (dept_id, dcc_id) in seen:
            return
        seen.add((dept_id, dcc_id))
        if status in ("exists", "creating"):
            out.append((dcc_id, status))

    for (dept_id, dcc_id), _state in states:
        dept_id = (dept_id or "").strip()
        dcc_id = (dcc_id or "").strip()
        if not dept_id or not dcc_id:
            continue
        if active_key and _norm(dept_id) != active_key:
            continue
        status = resolve_dcc_status(ref, dept_id, dcc_id)
        if status in ("exists", "creating"):
            add(dept_id, dcc_id, status)
    for d in getattr(ref, "departments", ()) or ():
        dept_name = getattr(d, "name", "") or ""
        if active_key and _norm(dept_name) != active_key:
            continue
        if dept_registry is not None:
            dcc_ids = dept_registry.supported_dcc_ids(reg, dept_name)
        else:
            dcc_ids = reg.get_available_dccs(dept_name) or []
        for dcc_id in dcc_ids:
            dcc_id = (dcc_id or "").strip()
            if not dcc_id:
                continue
            status = resolve_dcc_status(ref, dept_name, dcc_id)
            if status == "creating":
                add(dept_name, dcc_id, "creating")
    return out


def _list_dcc_badge_info(
    item: ViewItem,
    active_department: str | None,
    *,
    dept_registry: DepartmentRegistry | None = None,
) -> list[tuple[QIcon | None, str, str]]:
    """Return [(icon or None, dcc_id, status), ...] for list DCC column paint. status in ('exists', 'creating')."""
    out: list[tuple[QIcon | None, str, str]] = []
    ref = item.ref
    if not isinstance(ref, (Asset, Shot)):
        return out
    try:
        reg = get_default_dcc_registry()
    except Exception:
        return out
    _norm = lambda s: (s or "").strip().casefold()
    active_key = _norm(active_department)
    ids_with_status = _dcc_ids_for_item(item, active_department, dept_registry=dept_registry)
    for dcc_id, status in ids_with_status:
        if status == "creating":
            out.append((None, dcc_id, "creating"))
            continue
        try:
            info = reg.get_dcc_info(dcc_id) if dcc_id else None
        except Exception:
            info = None
        slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
        color = info.get("brand_color_hex") if isinstance(info, dict) else None
        if isinstance(slug, str) and slug.strip():
            ic = brand_icon(slug.strip(), size=14, color_hex=(color if isinstance(color, str) else None))
        else:
            ic = lucide_icon("layers", size=14, color_hex=MONOS_COLORS["text_label"])
        out.append((ic, dcc_id, "exists"))
    return out


def _list_dcc_badge_rects(
    cell_rect: QRect,
    dcc_list: list[tuple[str, str]],
) -> list[tuple[QRect, str]]:
    """Compute DCC badge rects inside a list table cell (horizontal row, no thumb). Returns [(rect, dcc_id), ...]."""
    if not dcc_list:
        return []
    size = 14
    pad = 4
    gap = 3
    max_show = 6
    chip_h = size + pad * 2
    chip_w = chip_h  # square for exists; creating uses wider
    creating_w = 44
    entries = dcc_list[:max_show]
    widths = [creating_w if st == "creating" else chip_w for (_, st) in entries]
    row_w = sum(widths) + (len(widths) - 1) * gap
    base_x = cell_rect.left() + 4
    base_y = cell_rect.top() + max(0, (cell_rect.height() - chip_h) // 2)
    result: list[tuple[QRect, str]] = []
    x_cursor = base_x
    for i, (dcc_id, _st) in enumerate(entries):
        w = widths[i]
        result.append((QRect(x_cursor, base_y, w, chip_h), dcc_id))
        x_cursor += w + gap
    return result


def _dcc_badge_rects(
    cell_rect: QRect,
    gap_px: int,
    dcc_list: list[tuple[object, str, str]] | list[tuple[str, str]],
) -> list[tuple[QRect, str]]:
    """Compute DCC badge rects (mirrors delegate paint layout). Returns [(rect, dcc_id), ...]."""
    if not dcc_list:
        return []
    r = cell_rect.adjusted(0, 0, -gap_px, -gap_px)
    border_px = 1
    inner = r.adjusted(border_px, border_px, -border_px, -border_px)
    thumb_w = inner.width()
    thumb_h = max(1, int(thumb_w * 9 / 16))
    thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))

    size = 16
    pad = 4
    badge_gap = 3
    max_show = 4
    chip_h = size + pad * 2
    creating_chip_w = 56
    entries = dcc_list[:max_show]
    # Accept 2-tuple (dcc_id, status) or 3-tuple (icon, dcc_id, status)
    def _unpack(entry: tuple) -> tuple[str, str]:
        if len(entry) == 2:
            return entry[0], entry[1]
        return entry[1], entry[2]
    parsed = [_unpack(e) for e in entries]
    widths = [creating_chip_w if st == "creating" else chip_h for (_, st) in parsed]
    row_w = sum(widths) + (len(widths) - 1) * badge_gap
    base_x = thumb.right() - 12 - row_w
    base_y = thumb.bottom() - 12 - chip_h

    result: list[tuple[QRect, str]] = []
    x_cursor = base_x
    for i, (dcc_id, _st) in enumerate(parsed):
        w = widths[i]
        result.append((QRect(x_cursor, base_y, w, chip_h), dcc_id))
        x_cursor += w + badge_gap
    return result


def _grid_status_pill_department_at(
    cell_rect: QRect,
    gap_px: int,
    pos: QPoint,
    *,
    selected: bool,
    item: ViewItem,
    active_department: str | None,
    project_root: str | None,
    hidden_departments: set[str],
    n_meta_lines: int,
) -> str | None:
    """Hit-test production status pill on grid card (only when a sidebar department is focused)."""
    if not project_root:
        return None
    ad_focus = (active_department or "").strip()
    if not ad_focus:
        return None
    if not isinstance(item.ref, (Asset, Shot)) or item.kind.value == "project":
        return None
    g = max(0, int(gap_px))
    r = cell_rect.adjusted(0, 0, -g, -g)
    if not r.contains(pos):
        return None
    border_px = 2 if selected else 1
    inner = r.adjusted(border_px, border_px, -border_px, -border_px)
    thumb_w = inner.width()
    thumb_h = max(1, int(thumb_w * 9 / 16))
    thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))
    y_pills = thumb.bottom() + _grid_asset_shot_y_pills_offset_from_thumb_bottom(n_meta_lines=n_meta_lines)
    x = inner.left() + 16
    w = inner.width() - 32
    if pos.y() < y_pills:
        return None

    chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
    fm = QFontMetrics(chip_font)
    chip_h = max(16, fm.height() + 4)
    chip_pad_x = 8
    dot_r = 3

    try:
        reg = _status_registry_for_view(project_root, ad_focus)
    except Exception:
        return None

    try:
        sid = aggregate_status_id_for_item(
            item.ref,
            active_department=ad_focus,
            hidden_departments=set(hidden_departments),
            registry=reg,
        )
        line = reg.label_for(sid)
        tw = fm.horizontalAdvance(line) + chip_pad_x * 2 + dot_r * 2 + 6
        tw = min(tw, w)
        chip_rect = QRect(x, y_pills, tw, chip_h)
        if chip_rect.contains(pos):
            return ad_focus
    except Exception:
        return None
    return None


def _grid_project_status_pill_rect(
    cell_rect: QRect,
    gap_px: int,
    pos: QPoint,
    *,
    selected: bool,
    line: str,
) -> QRect | None:
    """Rect for project card status pill when pos hits it (project browser grid)."""
    g = max(0, int(gap_px))
    r = cell_rect.adjusted(0, 0, -g, -g)
    if not r.contains(pos):
        return None
    border_px = 2 if selected else 1
    inner = r.adjusted(border_px, border_px, -border_px, -border_px)
    thumb_w = inner.width()
    thumb_h = max(1, int(thumb_w * 9 / 16))
    thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))
    y_meta = thumb.bottom() + _grid_asset_shot_meta_head_to_first_line()
    y_pills = y_meta + _GRID_META_LINE_H + _GRID_GAP_BETWEEN_META_LINES
    if pos.y() < y_pills:
        return None
    x = inner.left() + 16
    w = inner.width() - 32
    chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
    fm = QFontMetrics(chip_font)
    chip_h = _grid_status_pill_line_height(fm)
    chip_pad_x = 8
    dot_r = 3
    tw = fm.horizontalAdvance(line) + chip_pad_x * 2 + dot_r * 2 + 6
    tw = min(tw, w)
    chip_rect = QRect(x, y_pills, tw, chip_h)
    return chip_rect if chip_rect.contains(pos) else None


def _list_status_pill_natural_width(line: str, fm: QFontMetrics) -> int:
    """Full pill width (text + dot + padding) before fitting to cell."""
    chip_pad_x = 8
    dot_r = 3
    return fm.horizontalAdvance(line) + chip_pad_x * 2 + dot_r * 2 + 6


def _list_status_pill_rect_for_cell(cell_rect: QRect, line: str, fm: QFontMetrics) -> QRect:
    """Geometry for list Status column pill (matches grid chip metrics)."""
    chip_h = max(16, fm.height() + 4)
    tw = _list_status_pill_natural_width(line, fm)
    tw = min(tw, max(1, cell_rect.width() - 16))
    x = cell_rect.left() + 8
    y = cell_rect.top() + max(0, (cell_rect.height() - chip_h) // 2)
    return QRect(x, y, tw, chip_h)


def _paint_status_pill_chip(
    painter: QPainter,
    pill_rect: QRect,
    line: str,
    color_hex: str,
    *,
    fm: QFontMetrics,
    font: QFont | None = None,
    hovered: bool = False,
) -> None:
    """Shared production-style status pill (dot + tinted chip), list + grid."""
    chip_pad_x = 8
    dot_r = 3
    if font is not None:
        painter.setFont(font)
    painter.setPen(Qt.NoPen)
    qc = QColor(color_hex)
    bg = QColor(qc)
    bg.setAlpha(72 if hovered else 42)
    painter.setBrush(bg)
    painter.drawRoundedRect(pill_rect, 8, 8)
    if hovered:
        bc = QColor(qc)
        bc.setAlpha(140)
        painter.setPen(QPen(bc, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(pill_rect, 8, 8)
    painter.setPen(Qt.NoPen)
    painter.setBrush(qc)
    painter.drawEllipse(
        QPoint(pill_rect.left() + chip_pad_x + dot_r, pill_rect.center().y()),
        dot_r,
        dot_r,
    )
    painter.setPen(QColor(MONOS_COLORS["text_primary"] if hovered else MONOS_COLORS["text_label"]))
    text_rect = pill_rect.adjusted(chip_pad_x + dot_r * 2 + 6, 0, -4, 0)
    elided = fm.elidedText(line, Qt.TextElideMode.ElideRight, text_rect.width())
    painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)


def _item_active_dcc(item_path: Path, active_department: str) -> str | None:
    """Read active_dcc for this item+department from .monostudio/open.json."""
    if not item_path or not isinstance(item_path, Path):
        return None
    meta_path = _open_metadata_path(item_path)
    try:
        if not meta_path.is_file():
            return None
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    dep = (active_department or "").strip().casefold()
    active_by_dep = data.get("active_dcc_by_department")
    if isinstance(active_by_dep, dict):
        dcc = active_by_dep.get(dep) or active_by_dep.get(active_department)
        if isinstance(dcc, str) and dcc.strip():
            return dcc.strip()
    return None


def _write_active_dcc(item_path: Path, active_department: str, dcc_id: str) -> None:
    """Persist active_dcc for this item+department to .monostudio/open.json."""
    if not item_path or not isinstance(item_path, Path):
        return
    meta_path = _open_metadata_path(item_path)
    meta_dir = meta_path.parent
    try:
        data: dict = {}
        if meta_path.is_file():
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
    except (OSError, json.JSONDecodeError):
        data = {}

    dep = (active_department or "").strip().casefold()
    by_dep = data.get("active_dcc_by_department")
    if not isinstance(by_dep, dict):
        by_dep = {}
    by_dep[dep] = dcc_id
    data["active_dcc_by_department"] = by_dep

    try:
        meta_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        meta_path.write_text(content, encoding="utf-8")
    except OSError:
        pass


_RUBBER_BAND_THRESHOLD = 4


def _try_set_minimal_viewport_update(view) -> None:
    """Best-effort: reduce repaint area during selection/drag (not available on all Qt builds)."""
    enum_cls = getattr(QAbstractItemView, "ViewportUpdateMode", None)
    if enum_cls is None:
        return
    mode = getattr(enum_cls, "MinimalViewportUpdate", None)
    if mode is None:
        return
    for name in ("setViewportUpdateMode", "set_viewport_update_mode"):
        fn = getattr(view, name, None)
        if callable(fn):
            fn(mode)
            return
    base_fn = getattr(QAbstractItemView, "setViewportUpdateMode", None) or getattr(
        QAbstractItemView, "set_viewport_update_mode", None
    )
    if callable(base_fn):
        try:
            base_fn(view, mode)
        except Exception:
            pass


class _RubberBandSelectMixin:
    """Track drag-marquee so MainView can defer Inspector updates until release."""

    def _rb_init(self) -> None:
        self._left_press_pos: QPoint | None = None
        self._rb_selecting = False
        self._rb_gesture = False
        self._on_rubber_band_finished: Callable[[], None] | None = None
        self._rb_press_modifiers = Qt.KeyboardModifier.NoModifier
        self._rubber_band_widget: QRubberBand | None = None
        self._last_click_index: QPersistentModelIndex | None = None
        self._last_click_time: float = 0.0
        self._rb_skip_release_click = False
        self._shift_click_pending = False
        self._rb_empty_press = False

    def _rb_acquire_mouse(self) -> None:
        vp = self._rb_viewport()
        if vp is None:
            return
        try:
            vp.grabMouse()
        except Exception:
            pass

    def _rb_release_mouse(self) -> None:
        vp = self._rb_viewport()
        if vp is None:
            return
        try:
            if QWidget.mouseGrabber() is vp:
                vp.releaseMouse()
        except Exception:
            pass

    def _rb_force_cleanup(self) -> None:
        """Always drop marquee state and viewport mouse grab (hover breaks if grab leaks)."""
        self._rb_hide_rubber_band()
        self._rb_release_mouse()
        self._left_press_pos = None
        self._rb_selecting = False
        self._rb_empty_press = False
        self._rb_gesture = False

    def _shift_anchor_model_index(self):
        anchor = getattr(self, "_shift_anchor_index", None)
        if anchor is not None and anchor.isValid():
            return anchor
        sm = self.selectionModel()
        if sm is not None:
            cur = sm.currentIndex()
            if cur.isValid():
                return cur
        return QModelIndex()

    def _row_passes_selection_filter(self, idx) -> bool:
        m = self.model()
        if m is None or not idx.isValid():
            return False
        mv = m.parent()
        is_dimmed = getattr(mv, "_is_item_dimmed", None) if mv is not None else None
        if not callable(is_dimmed):
            return True
        item = idx.data(Qt.UserRole)
        return not (isinstance(item, ViewItem) and is_dimmed(item))

    def _select_shift_range_rows(self, *, anchor, target, add: bool) -> None:
        """Row range for IconMode grid / list — skips dimmed pipeline items."""
        sm = self.selectionModel()
        m = self.model()
        if sm is None or m is None or not (anchor.isValid() and target.isValid()):
            return
        lo = min(anchor.row(), target.row())
        hi = max(anchor.row(), target.row())
        if not add:
            sm.clearSelection()
        for r in range(lo, hi + 1):
            idx = m.index(r, 0)
            if idx.isValid() and self._row_passes_selection_filter(idx):
                sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
        sm.setCurrentIndex(target, QItemSelectionModel.SelectionFlag.NoUpdate)

    def _apply_shift_range_to_pos(self, pos: QPoint, *, add: bool | None = None) -> None:
        target = self.indexAt(pos)
        if not target.isValid():
            return
        if add is None:
            add = bool(self._rb_press_modifiers & Qt.KeyboardModifier.ControlModifier)
        self._select_shift_range_rows(
            anchor=self._shift_anchor_model_index(),
            target=target,
            add=add,
        )

    def _handle_shift_left_release(self, event: QMouseEvent) -> None:
        self._rb_promote_to_marquee(event.pos())
        if self._rb_selecting:
            self._rb_update_rubber_band(event.pos())
            self._apply_rubber_band_row_selection(event.pos())
        else:
            self._apply_shift_range_to_pos(
                event.pos(),
                add=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier),
            )
        self._shift_click_pending = False
        self._rb_on_left_release()
        sm = self.selectionModel()
        if sm is not None and sm.currentIndex().isValid():
            self._shift_anchor_index = QPersistentModelIndex(sm.currentIndex())

    def _apply_plain_left_click(self, event: QMouseEvent) -> None:
        idx = self.indexAt(event.pos())
        sm = self.selectionModel()
        if sm is None or not idx.isValid():
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if sm.isSelected(idx):
                sm.select(idx, QItemSelectionModel.SelectionFlag.Deselect)
            else:
                sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            sm.select(idx, QItemSelectionModel.SelectionFlag.Select)

    def _finish_left_button_release(self, event: QMouseEvent) -> None:
        """Rubber-band end, double-click, or plain click — never delegate to Qt (avoids undoing selection)."""
        if getattr(self, "_rb_skip_release_click", False):
            self._rb_skip_release_click = False
            self._rb_on_left_release()
            return
        self._rb_promote_to_marquee(event.pos())
        if self._rb_selecting:
            self._rb_update_rubber_band(event.pos())
            self._apply_rubber_band_row_selection(event.pos())
            self._rb_on_left_release()
            return
        now = time.monotonic()
        idx = self.indexAt(event.pos())
        if getattr(self, "_rb_empty_press", False) and not idx.isValid():
            if not bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.clearSelection()
                self._shift_anchor_index = None
            self._rb_on_left_release()
            return
        dbl_ms = float(QApplication.doubleClickInterval())
        last = self._last_click_index
        if (
            idx.isValid()
            and last is not None
            and last.isValid()
            and idx.row() == last.row()
            and (now - self._last_click_time) * 1000.0 <= dbl_ms
        ):
            self._last_click_index = None
            self._last_click_time = 0.0
            self._rb_on_left_release()
            self.doubleClicked.emit(idx)
            return
        self._apply_plain_left_click(event)
        if idx.isValid():
            self._last_click_index = QPersistentModelIndex(idx)
            self._last_click_time = now
        else:
            self._last_click_index = None
        self._rb_on_left_release()

    def rubber_band_selecting(self) -> bool:
        return bool(self._rb_selecting)

    def _rb_viewport(self):
        fn = getattr(self, "viewport", None)
        return fn() if callable(fn) else None

    @staticmethod
    def _rb_marquee_threshold() -> int:
        # Do not use startDragDistance() (often 10px) — that caused “must drag twice”.
        return _RUBBER_BAND_THRESHOLD

    def _rb_promote_to_marquee(self, pos: QPoint) -> None:
        if self._rb_selecting or self._left_press_pos is None:
            return
        delta = pos - self._left_press_pos
        if delta.manhattanLength() >= self._rb_marquee_threshold():
            try:
                self.doItemsLayout()
            except Exception:
                pass
            self._rb_selecting = True

    def _ensure_rubber_band_widget(self) -> QRubberBand:
        if self._rubber_band_widget is None:
            self._rubber_band_widget = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        return self._rubber_band_widget

    def _rb_on_left_press(self, pos: QPoint, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        self._rb_release_mouse()
        self._left_press_pos = pos
        self._rb_selecting = False
        self._rb_gesture = True
        self._rb_press_modifiers = modifiers
        self._rb_skip_release_click = False
        self._rb_empty_press = not self.indexAt(pos).isValid()
        self._rb_acquire_mouse()

    def _rb_is_icon_grid(self) -> bool:
        return isinstance(self, QListView) and self.viewMode() == QListView.IconMode

    def _marquee_rect(self, current_pos: QPoint) -> QRect:
        if self._left_press_pos is None:
            return QRect()
        norm = QRect(self._left_press_pos, current_pos).normalized()
        if norm.isNull():
            return norm
        return norm.adjusted(-1, -1, 1, 1)

    def _row_visual_rect(self, row: int) -> QRect:
        """Visible row bounds for marquee hit-testing (table rows span viewport width)."""
        m = self.model()
        if m is None or row < 0 or row >= m.rowCount():
            return QRect()
        if isinstance(self, QTableView):
            try:
                y = self.rowViewportPosition(row)
                h = self.rowHeight(row)
            except Exception:
                return QRect()
            if h <= 0:
                return QRect()
            vp = self.viewport()
            vw = vp.width() if vp is not None else 0
            if vw <= 0:
                idx = m.index(row, 0)
                return self.visualRect(idx) if idx.isValid() else QRect()
            return QRect(0, y, vw, h)
        idx = m.index(row, 0)
        if not idx.isValid():
            return QRect()
        return self.visualRect(idx)

    def _rows_in_marquee_rect(self, norm: QRect) -> list[int]:
        """Model rows whose visual cell intersects the marquee (geometry only)."""
        m = self.model()
        if m is None or norm.isNull():
            return []
        rows: set[int] = set()

        def _add_row(row: int) -> None:
            if row < 0 or row >= m.rowCount():
                return
            idx = m.index(row, 0)
            if idx.isValid() and self._row_passes_selection_filter(idx):
                rows.add(row)

        def _add_at(pt: QPoint) -> None:
            idx = self.indexAt(pt)
            if idx.isValid():
                _add_row(idx.row())

        for pt in (
            norm.topLeft(),
            norm.topRight(),
            norm.bottomLeft(),
            norm.bottomRight(),
            norm.center(),
        ):
            _add_at(pt)

        if self._rb_is_icon_grid():
            gs = self.gridSize()
            gw = int(gs.width())
            gh = int(gs.height())
            if gw > 1 and gh > 1:
                step_x = max(1, gw // 3)
                step_y = max(1, gh // 3)
                y = norm.top()
                while y <= norm.bottom():
                    x = norm.left()
                    while x <= norm.right():
                        _add_at(QPoint(x, y))
                        x += step_x
                    y += step_y

        for row in range(m.rowCount()):
            idx = m.index(row, 0)
            if not idx.isValid():
                continue
            if not self._row_passes_selection_filter(idx):
                continue
            vr = self._row_visual_rect(row)
            if vr.isValid() and not vr.isEmpty() and norm.intersects(vr):
                rows.add(row)

        return sorted(rows)

    def _rb_on_move(self, event: QMouseEvent) -> None:
        if self._left_press_pos is None:
            return
        if not bool(event.buttons() & Qt.MouseButton.LeftButton):
            return
        self._rb_promote_to_marquee(event.pos())

    def _rb_update_rubber_band(self, current_pos: QPoint) -> None:
        if self._left_press_pos is None or not self._rb_selecting:
            return
        rect = QRect(self._left_press_pos, current_pos).normalized()
        rb = self._ensure_rubber_band_widget()
        rb.setGeometry(rect)
        rb.show()

    def _rb_hide_rubber_band(self) -> None:
        rb = self._rubber_band_widget
        if rb is None:
            return
        rb.hide()
        rb.setGeometry(QRect())
        vp = self._rb_viewport()
        if vp is not None:
            vp.update()

    def _apply_rubber_band_row_selection(self, current_pos: QPoint) -> None:
        """Marquee selection = items intersecting the current press→cursor rect."""
        sm = self.selectionModel()
        m = self.model()
        if sm is None or m is None or self._left_press_pos is None:
            return
        shift = bool(self._rb_press_modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(self._rb_press_modifiers & Qt.KeyboardModifier.ControlModifier)
        norm = self._marquee_rect(current_pos)
        if norm.isNull():
            return
        rows_in_rect = self._rows_in_marquee_rect(norm)

        if shift:
            lo = min(rows_in_rect) if rows_in_rect else None
            hi = max(rows_in_rect) if rows_in_rect else None
            anchor = self._shift_anchor_model_index()
            if anchor.isValid():
                lo = min(lo, anchor.row()) if lo is not None else anchor.row()
                hi = max(hi, anchor.row()) if hi is not None else anchor.row()
            elif lo is None or hi is None:
                return
            if not ctrl:
                sm.clearSelection()
            for r in range(lo, hi + 1):
                idx = m.index(r, 0)
                if idx.isValid() and self._row_passes_selection_filter(idx):
                    sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
            return

        selection = QItemSelection()
        for row in rows_in_rect:
            idx = m.index(row, 0)
            if idx.isValid():
                selection.select(idx, idx)
        if ctrl:
            sm.select(selection, QItemSelectionModel.SelectionFlag.Select)
        else:
            sm.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if rows_in_rect:
            last_idx = m.index(rows_in_rect[-1], 0)
            if last_idx.isValid():
                sm.setCurrentIndex(last_idx, QItemSelectionModel.SelectionFlag.NoUpdate)

    def _rb_on_left_release(self) -> bool:
        was_rubber = bool(self._rb_selecting)
        self._rb_force_cleanup()
        if was_rubber and self._on_rubber_band_finished is not None:
            self._on_rubber_band_finished()
        return was_rubber


class _ClearOnEmptyClickListView(_RubberBandSelectMixin, QListView):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rb_init()
        self._middle_drag_start_pos: QPoint | None = None
        self._shift_anchor_index: QPersistentModelIndex | None = None

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._rb_on_left_press(event.pos(), event.modifiers())
        if event.button() == Qt.MouseButton.MiddleButton:
            # Middle button: drag gesture only (do not alter selection).
            idx = self.indexAt(event.pos())
            if idx.isValid():
                sm = self.selectionModel()
                if sm is not None:
                    sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            self._middle_drag_start_pos = event.pos()
            event.accept()
            return
        # Shift+click/drag: defer range until release or live update while dragging.
        if event.button() == Qt.MouseButton.LeftButton and bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if self.indexAt(event.pos()).isValid():
                self._shift_click_pending = True
                event.accept()
                return
        # Clear selection only on primary click on empty area (defer until release — not marquee).
        if event.button() == Qt.MouseButton.LeftButton and not self.indexAt(event.pos()).isValid():
            self._shift_anchor_index = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if bool(event.buttons() & Qt.MouseButton.MiddleButton) and self._middle_drag_start_pos is not None:
            # Start drag once the user moved past the platform threshold.
            if (event.pos() - self._middle_drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
                self.startDrag(Qt.CopyAction)
                self._middle_drag_start_pos = None
            event.accept()
            return
        self._rb_on_move(event)
        if bool(event.buttons() & Qt.MouseButton.LeftButton) and self._left_press_pos is not None:
            if self._shift_click_pending:
                if self._rb_selecting:
                    self._apply_shift_range_to_pos(event.pos())
                event.accept()
                return
            if self._rb_selecting:
                self._rb_update_rubber_band(event.pos())
                self._apply_rubber_band_row_selection(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_drag_start_pos = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._shift_click_pending:
                self._handle_shift_left_release(event)
                event.accept()
                return
            self._rb_promote_to_marquee(event.pos())
            was_rubber = bool(self._rb_selecting)
            skip_click = bool(getattr(self, "_rb_skip_release_click", False))
            self._finish_left_button_release(event)
            if not was_rubber and not skip_click:
                sm = self.selectionModel()
                if sm is not None and sm.currentIndex().isValid():
                    self._shift_anchor_index = QPersistentModelIndex(sm.currentIndex())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def startDrag(self, supportedActions) -> None:  # type: ignore[override]
        buttons = QApplication.mouseButtons()
        # Only allow dragging with middle button (disable left-drag).
        if not (buttons & Qt.MouseButton.MiddleButton) or (buttons & Qt.MouseButton.LeftButton):
            return
        model = self.model()
        if model is None:
            return
        indexes = self.selectedIndexes()
        if not indexes:
            return
        mime = model.mimeData(indexes)
        if mime is None:
            return

        dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
        if dpr <= 0:
            dpr = 1.0

        def render_card(index, *, logical_size: QSize) -> QPixmap | None:
            if not index.isValid():
                return None
            w = max(1, int(logical_size.width() * dpr))
            h = max(1, int(logical_size.height() * dpr))
            pm = QPixmap(w, h)
            pm.setDevicePixelRatio(dpr)
            pm.fill(QColor(0, 0, 0, 0))
            p2 = QPainter(pm)
            try:
                p2.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p2.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                # Lightweight drag preview: thumbnail + name only (no status/id/ver overlays).
                r = QRect(0, 0, logical_size.width(), logical_size.height())
                # Background card
                bg = QColor("#18181b")
                border = QColor("#3f3f46")
                p2.setPen(QPen(border, 1))
                p2.setBrush(bg)
                p2.drawRoundedRect(r.adjusted(0, 0, -1, -1), 12, 12)

                inner = r.adjusted(8, 8, -8, -8)
                if inner.width() > 0 and inner.height() > 0:
                    # 16:9 thumb region
                    thumb_w = inner.width()
                    thumb_h = max(1, int(thumb_w * 9 / 16))
                    thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))
                    icon = index.data(Qt.DecorationRole)
                    if isinstance(icon, QIcon):
                        src = icon.pixmap(256, 256)
                        if not src.isNull():
                            scaled = src.scaled(
                                thumb.size(),
                                Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation,
                            )
                            sx = max(0, (scaled.width() - thumb.width()) // 2)
                            sy = max(0, (scaled.height() - thumb.height()) // 2)
                            crop = scaled.copy(QRect(QPoint(sx, sy), thumb.size()))
                            p2.drawPixmap(thumb, crop)

                    # Name (single line)
                    item = index.data(Qt.UserRole)
                    name = display_name_for_item(item) if isinstance(item, ViewItem) else None
                    name = (name or "").strip() or "—"
                    text_rect = QRect(inner.left(), thumb.bottom() + 8, inner.width(), max(1, inner.bottom() - (thumb.bottom() + 8)))
                    f = QFont("Inter")
                    f.setPointSize(11)
                    f.setWeight(QFont.Weight.DemiBold)
                    p2.setFont(f)
                    p2.setPen(QColor("#e4e4e7"))
                    p2.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, name)
            finally:
                p2.end()
            return pm if not pm.isNull() else None

        # Base card size from the live view cell (logical px).
        try:
            base_rect = self.visualRect(indexes[0])
        except Exception:
            return
        if not base_rect.isValid() or base_rect.isEmpty():
            return

        max_w = 220
        base_logical_w = int(base_rect.width())
        target_w = min(max_w, max(120, int(base_logical_w * 0.62)))
        # Compute a compact height for drag preview (thumb 16:9 + single-line name).
        inner_pad = 8
        gap_thumb_text = 8
        name_font = QFont("Inter")
        name_font.setPointSize(11)
        name_font.setWeight(QFont.Weight.DemiBold)
        name_fm = QFontMetrics(name_font)
        name_h = max(14, name_fm.height())
        inner_w = max(1, target_w - inner_pad * 2)
        thumb_h = max(1, int(inner_w * 9 / 16))
        content_h = inner_pad + thumb_h + gap_thumb_text + name_h + inner_pad
        # Ensure we don't exceed the original cell height too much.
        max_h = max(1, int(base_rect.height() * (target_w / max(1, base_logical_w))))
        target_h = min(max_h, content_h)
        target_size = QSize(target_w, max(1, target_h))

        scaled_cards: list[QPixmap] = []
        for idx in indexes[:3]:  # Keep stack shallow for readability/perf.
            pm = render_card(idx, logical_size=target_size)
            if pm is not None:
                scaled_cards.append(pm)
        if not scaled_cards:
            return

        # Composite as a small stack + count badge.
        offset = 10
        layers = len(scaled_cards)
        w = int(scaled_cards[0].width() / dpr) + offset * (layers - 1)
        h = int(scaled_cards[0].height() / dpr) + offset * (layers - 1)
        out = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        out.setDevicePixelRatio(dpr)
        out.fill(QColor(0, 0, 0, 0))
        p = QPainter(out)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # Back-to-front
            for i in range(layers - 1, -1, -1):
                pm = scaled_cards[i]
                dx = offset * i
                dy = offset * i
                if i > 0:
                    p.setOpacity(0.78)
                else:
                    p.setOpacity(1.0)
                p.drawPixmap(dx, dy, pm)
            p.setOpacity(1.0)

            total = len(indexes)
            if total > 1:
                badge = f"{total}"
                f = QFont("Inter")
                f.setPointSize(10)
                f.setWeight(QFont.Weight.DemiBold)
                p.setFont(f)
                fm = QFontMetrics(f)
                tw = fm.horizontalAdvance(badge)
                pad_x = 8
                pad_y = 4
                bw = tw + pad_x * 2
                bh = fm.height() + pad_y * 2
                # HiDPI: painter uses logical coords when pixmap has devicePixelRatio.
                out_w = int(out.width() / max(dpr, 1.0))
                bx = out_w - bw - 6
                by = 6
                br = 9
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(37, 99, 235, 220))  # blue-600-ish
                p.drawRoundedRect(QRect(bx, by, bw, bh), br, br)
                p.setPen(QColor("#fafafa"))
                p.drawText(QRect(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, badge)
        finally:
            p.end()

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(out)
        drag.setHotSpot(QPoint(24, 24))
        delegate = self.itemDelegate()
        if hasattr(delegate, "set_fast_paint"):
            delegate.set_fast_paint(True)
        try:
            drag.exec(supportedActions)
        finally:
            if hasattr(delegate, "set_fast_paint"):
                delegate.set_fast_paint(False)


class _ClearOnEmptyClickTableView(_RubberBandSelectMixin, QTableView):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rb_init()
        self._shift_anchor_index: QPersistentModelIndex | None = None

    def _list_row_bg_option(self, row: int) -> QStyleOptionViewItem:
        m = self.model()
        vp = self.viewport()
        y = self.rowViewportPosition(row)
        h = self.rowHeight(row)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, y, vp.width(), h)
        opt.state = QStyle.StateFlag.State_Enabled
        sm = self.selectionModel()
        idx0 = m.index(row, 0) if m is not None else QModelIndex()
        if sm is not None and idx0.isValid() and sm.isSelected(idx0):
            opt.state |= QStyle.StateFlag.State_Selected
        hover = self.indexAt(vp.mapFromGlobal(QCursor.pos()))
        if hover.isValid() and hover.row() == row:
            opt.state |= QStyle.StateFlag.State_MouseOver
        return opt

    def _paint_list_row_backgrounds(self, painter: QPainter, exposed: QRect) -> None:
        m = self.model()
        if m is None or m.rowCount() <= 0:
            return
        top = self.rowAt(exposed.top())
        if top < 0:
            top = 0
        bottom = self.rowAt(exposed.bottom())
        if bottom < 0:
            bottom = m.rowCount() - 1
        vp_w = self.viewport().width()
        painter.save()
        try:
            painter.setClipping(False)
            for row in range(top, bottom + 1):
                y = self.rowViewportPosition(row)
                h = self.rowHeight(row)
                if h <= 0 or y + h < exposed.top() or y > exposed.bottom():
                    continue
                opt = self._list_row_bg_option(row)
                paint_inbox_list_row_chrome(painter, opt, viewport_width=vp_w)
                if opt.state & QStyle.StateFlag.State_Selected:
                    accent = QColor(MONOS_COLORS["blue_600"])
                    painter.fillRect(0, y, 2, h, accent)
        finally:
            painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        vp = self.viewport()
        painter = QPainter(vp)
        try:
            self._paint_list_row_backgrounds(painter, event.rect())
        finally:
            painter.end()
        super().paintEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._rb_on_left_press(event.pos(), event.modifiers())
        if event.button() == Qt.MouseButton.MiddleButton:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if self.indexAt(event.pos()).isValid():
                self._shift_click_pending = True
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton and not self.indexAt(event.pos()).isValid():
            self._shift_anchor_index = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        self._rb_on_move(event)
        if bool(event.buttons() & Qt.MouseButton.LeftButton) and self._left_press_pos is not None:
            if self._shift_click_pending:
                if self._rb_selecting:
                    self._apply_shift_range_to_pos(event.pos())
                event.accept()
                return
            if self._rb_selecting:
                self._rb_update_rubber_band(event.pos())
                self._apply_rubber_band_row_selection(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            if self._shift_click_pending:
                self._handle_shift_left_release(event)
                event.accept()
                return
            self._rb_promote_to_marquee(event.pos())
            was_rubber = bool(self._rb_selecting)
            self._finish_left_button_release(event)
            if not was_rubber:
                sm = self.selectionModel()
                if sm is not None and sm.currentIndex().isValid():
                    self._shift_anchor_index = QPersistentModelIndex(sm.currentIndex())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ListRowDelegate(QStyledItemDelegate):
    """Paints thumbnail column, DCC badges column, active-project row (Projects), and status color is via item foreground."""

    _LIST_THUMB_SIZE = 40
    _DCC_BADGE_SIZE = 14
    _DCC_BADGE_PAD = 4
    _DCC_BADGE_GAP = 3
    _DCC_MAX = 6

    def __init__(self, *, view: QTableView, main_view: QWidget) -> None:
        super().__init__(view)
        self._view = view
        self._main_view = main_view  # MainView: for _active_department, _show_publish, _list_col_dcc
        self._active_project_root: str | None = None
        self._hovered_status_row: int | None = None
        self._hovered_health_row: int | None = None
        self._hovered_notes_row: int | None = None
        self._hovered_ref_row: int | None = None
        self._hovered_concept_row: int | None = None

    def set_hovered_status_row(self, row: int | None) -> None:
        if self._hovered_status_row == row:
            return
        self._hovered_status_row = row
        self._view.viewport().update()

    def set_hovered_health_row(self, row: int | None) -> None:
        if self._hovered_health_row == row:
            return
        self._hovered_health_row = row
        self._view.viewport().update()

    def set_hovered_notes_row(self, row: int | None) -> None:
        if self._hovered_notes_row == row:
            return
        self._hovered_notes_row = row
        self._view.viewport().update()

    def set_hovered_ref_row(self, row: int | None) -> None:
        if self._hovered_ref_row == row:
            return
        self._hovered_ref_row = row
        self._view.viewport().update()

    def set_hovered_concept_row(self, row: int | None) -> None:
        if self._hovered_concept_row == row:
            return
        self._hovered_concept_row = row
        self._view.viewport().update()

    def set_active_project_root(self, path: str | None) -> None:
        p = path or None
        if p == self._active_project_root:
            return
        self._active_project_root = p
        self._view.viewport().update()

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        """Widen Status column to fit production pill (ResizeToContents uses delegate hint)."""
        main = self._main_view
        col = index.column()
        list_col_status = main._list_col_status() if hasattr(main, "_list_col_status") else -1
        if col == list_col_status and list_col_status >= 0:
            ctx = getattr(main, "_browser_context", "")
            item = index.data(Qt.UserRole)
            chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
            fm = QFontMetrics(chip_font)
            chip_h = max(16, fm.height() + 4)
            lw = int(getattr(main, "_list_status_pill_layout_width", 0) or 0)
            base = super().sizeHint(option, index)
            if ctx == "project" and isinstance(item, ViewItem) and item.kind == ViewItemKind.PROJECT:
                if lw > 0:
                    return QSize(max(base.width(), lw), max(base.height(), chip_h))
                return QSize(max(base.width(), 120), max(base.height(), chip_h))
            if ctx in ("asset", "shot"):
                dep = (getattr(main, "_active_department", None) or "").strip()
                root = getattr(main, "_project_root", None)
                if dep and root and isinstance(item, ViewItem) and isinstance(item.ref, (Asset, Shot)):
                    if lw > 0:
                        return QSize(max(base.width(), lw), max(base.height(), chip_h))
                    return QSize(max(base.width(), 120), max(base.height(), chip_h))
        list_col_notes = main._list_col_notes() if hasattr(main, "_list_col_notes") else -1
        if col == list_col_notes and list_col_notes >= 0:
            chip = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
            base = super().sizeHint(option, index)
            return QSize(max(base.width(), chip + 12), max(base.height(), chip + 8))
        list_col_health = main._list_col_health() if hasattr(main, "_list_col_health") else -1
        if col == list_col_health and list_col_health >= 0:
            chip = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
            base = super().sizeHint(option, index)
            return QSize(max(base.width(), chip + 12), max(base.height(), chip + 8))
        list_col_ref = main._list_col_ref() if hasattr(main, "_list_col_ref") else -1
        list_col_concept = main._list_col_concept() if hasattr(main, "_list_col_concept") else -1
        if col in (list_col_ref, list_col_concept) and col >= 0:
            chip = _LIST_SPECIAL_FOLDER_ICON_PX + _LIST_SPECIAL_FOLDER_CHIP_PAD_PX * 2
            base = super().sizeHint(option, index)
            return QSize(max(base.width(), chip + 12), max(base.height(), chip + 8))
        return super().sizeHint(option, index)

    def _paint_default_cell(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.showDecorationSelected = False
        super().paint(painter, opt, index)

    def paint(self, painter: QPainter, option, index) -> None:
        item = index.data(Qt.UserRole)
        col = index.column()
        main = self._main_view
        list_col_dcc = main._list_col_dcc() if hasattr(main, "_list_col_dcc") else -1
        active_dep = getattr(main, "_active_department", None) or ""
        show_publish = getattr(main, "_show_publish", False)

        # Active project row: 2px left border amber (column 0)
        active = (
            isinstance(item, ViewItem)
            and item.kind == ViewItemKind.PROJECT
            and self._active_project_root is not None
            and str(item.path) == self._active_project_root
        )
        if active and col == 0:
            c = QColor(MONOS_COLORS["amber_400"])
            c.setAlphaF(0.7)
            painter.fillRect(option.rect.left(), option.rect.top(), 2, option.rect.height(), c)

        # DCC column: paint badges (asset/shot, not publish mode)
        if col == list_col_dcc and list_col_dcc >= 0 and isinstance(item, ViewItem) and not show_publish:
            dept_reg = getattr(main, "_dept_registry", None)
            badge_info = _list_dcc_badge_info(
                item, active_dep.strip() or None, dept_registry=dept_reg
            )
            if badge_info:
                rects = _list_dcc_badge_rects(option.rect, [(dcc_id, st) for (_, dcc_id, st) in badge_info])
                chip_h = self._DCC_BADGE_SIZE + self._DCC_BADGE_PAD * 2
                chip_r = chip_h // 2
                dcc_bg = QColor(0, 0, 0, 160)
                active_dcc = _item_active_dcc(getattr(item, "path", None), active_dep) if getattr(item, "path", None) else None
                existing_ids = {dcc_id for (_, dcc_id, st) in badge_info if st == "exists"}
                if not active_dcc or active_dcc not in existing_ids:
                    active_dcc = next((dcc_id for (_, dcc_id, st) in badge_info if st == "exists"), None)
                c_active = QColor(MONOS_COLORS["amber_400"])
                c_active.setAlphaF(0.7)
                pen_active = QPen(c_active, 2)
                creating_font = monos_font("Inter", 9)
                for i, (dcc_icon, dcc_id, badge_status) in enumerate(badge_info):
                    if i >= len(rects):
                        break
                    r, _ = rects[i]
                    is_active = bool(active_dcc and (dcc_id or "").strip() == active_dcc)
                    if badge_status == "creating":
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(dcc_bg)
                        painter.drawRoundedRect(r, chip_r, chip_r)
                        if is_active:
                            painter.setPen(pen_active)
                            painter.setBrush(Qt.NoBrush)
                            painter.drawRoundedRect(r, chip_r, chip_r)
                        painter.setFont(creating_font)
                        painter.setPen(QColor(255, 255, 255))
                        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "Creating…")
                    else:
                        cx, cy = r.x() + chip_r, r.y() + chip_r
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(dcc_bg)
                        painter.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                        if is_active:
                            painter.setPen(pen_active)
                            painter.setBrush(Qt.NoBrush)
                            painter.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                        if dcc_icon is not None and not dcc_icon.isNull():
                            pix = dcc_icon.pixmap(self._DCC_BADGE_SIZE, self._DCC_BADGE_SIZE)
                            if not pix.isNull():
                                painter.drawPixmap(r.x() + self._DCC_BADGE_PAD, r.y() + self._DCC_BADGE_PAD, pix)
                return

        # Thumbnail column (col 1): cover-fit — fill cell, center-crop
        if col == 1:
            icon = index.data(Qt.DecorationRole)
            if isinstance(icon, QIcon) and not icon.isNull():
                if _list_thumb_cover_paint(painter, option.rect, icon):
                    return
            self._paint_default_cell(painter, option, index)
            return

        list_col_notes = main._list_col_notes() if hasattr(main, "_list_col_notes") else -1
        if (
            col == list_col_notes
            and list_col_notes >= 0
            and getattr(main, "_browser_context", "") in ("asset", "shot")
        ):
            item = index.data(Qt.UserRole)
            if (
                isinstance(item, ViewItem)
                and isinstance(item.ref, (Asset, Shot))
                and getattr(item, "path", None)
            ):
                n, nmode = (0, "empty")
                if hasattr(main, "notes_badge_state"):
                    try:
                        n, nmode = main.notes_badge_state(item.path)  # type: ignore[attr-defined]
                    except Exception:
                        n, nmode = 0, "empty"
                painter.save()
                try:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    chip_rect = _list_health_chip_rect(option.rect)
                    _paint_note_icon_chip(
                        painter,
                        chip_rect,
                        n,
                        visual_mode=nmode,
                        hovered=self._hovered_notes_row == index.row(),
                    )
                finally:
                    painter.restore()
                return

        list_col_health = main._list_col_health() if hasattr(main, "_list_col_health") else -1
        if (
            col == list_col_health
            and list_col_health >= 0
            and getattr(main, "_browser_context", "") in ("asset", "shot")
        ):
            dep = (active_dep or "").strip()
            if dep and isinstance(item, ViewItem) and isinstance(item.ref, (Asset, Shot)):
                active_dcc = (
                    _item_active_dcc(getattr(item, "path", None), dep)
                    if getattr(item, "path", None)
                    else None
                )
                health = assess_view_item_health(
                    item.ref, dep, active_dcc_id=active_dcc
                )
                if health is not None:
                    painter.save()
                    try:
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                        chip_rect = _list_health_chip_rect(option.rect)
                        health_hover = self._hovered_health_row == index.row()
                        _paint_health_icon_chip(
                            painter, chip_rect, health, hovered=health_hover
                        )
                    finally:
                        painter.restore()
                    return

        list_col_ref = main._list_col_ref() if hasattr(main, "_list_col_ref") else -1
        list_col_concept = main._list_col_concept() if hasattr(main, "_list_col_concept") else -1
        if (
            col in (list_col_ref, list_col_concept)
            and col >= 0
            and getattr(main, "_browser_context", "") in ("asset", "shot")
            and isinstance(item, ViewItem)
            and isinstance(item.ref, (Asset, Shot))
        ):
            has_files = False
            if col == list_col_ref and hasattr(main, "entity_has_reference_files_cached"):
                has_files = bool(main.entity_has_reference_files_cached(item))  # type: ignore[attr-defined]
            elif col == list_col_concept and hasattr(main, "entity_has_concept_files_cached"):
                has_files = bool(main.entity_has_concept_files_cached(item))  # type: ignore[attr-defined]
            icon_name = "eye" if col == list_col_ref else "lightbulb"
            hovered = (
                (col == list_col_ref and self._hovered_ref_row == index.row())
                or (col == list_col_concept and self._hovered_concept_row == index.row())
            )
            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                _paint_list_special_folder_icon(
                    painter,
                    _list_special_folder_chip_rect(option.rect),
                    icon_name,
                    has_files=has_files,
                    hovered=hovered,
                )
            finally:
                painter.restore()
            return

        list_col_status = main._list_col_status() if hasattr(main, "_list_col_status") else -1
        if (
            col == list_col_status
            and list_col_status >= 0
            and getattr(main, "_browser_context", "") == "project"
            and isinstance(item, ViewItem)
            and item.kind == ViewItemKind.PROJECT
        ):
            stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
            status = getattr(stats, "status", None) or "WAITING"
            line = project_status_label(status)
            color_hex = project_status_color_hex(status)
            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
                fm = QFontMetrics(chip_font)
                pill_rect = _list_status_pill_rect_for_cell(option.rect, line, fm)
                pill_hover = self._hovered_status_row == index.row()
                _paint_status_pill_chip(
                    painter,
                    pill_rect,
                    line,
                    color_hex,
                    fm=fm,
                    font=chip_font,
                    hovered=pill_hover,
                )
            finally:
                painter.restore()
            return

        if (
            col == list_col_status
            and list_col_status >= 0
            and getattr(main, "_browser_context", "") in ("asset", "shot")
        ):
            root = getattr(main, "_project_root", None)
            dep = (active_dep or "").strip()
            if (
                dep
                and root
                and isinstance(item, ViewItem)
                and isinstance(item.ref, (Asset, Shot))
            ):
                painter.save()
                try:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    reg = main._production_status_registry_cached()
                    hidden = set(getattr(main, "_inspector_hidden_departments", set()) or ())
                    sid = aggregate_status_id_for_item(
                        item.ref,
                        active_department=dep,
                        hidden_departments=hidden,
                        registry=reg,
                    )
                    line = reg.label_for(sid)
                    chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
                    fm = QFontMetrics(chip_font)
                    pill_rect = _list_status_pill_rect_for_cell(option.rect, line, fm)
                    pill_hover = self._hovered_status_row == index.row()
                    _paint_status_pill_chip(
                        painter,
                        pill_rect,
                        line,
                        color_hex_for_status_id(sid, reg),
                        fm=fm,
                        font=chip_font,
                        hovered=pill_hover,
                    )
                except Exception:
                    painter.restore()
                    self._paint_default_cell(painter, option, index)
                    return
                painter.restore()
                return

        self._paint_default_cell(painter, option, index)


class _GridCardDelegate(QStyledItemDelegate):
    """
    Grid card painter (Grid view):
    - 16:9 thumbnail
    - Status badge (top-left)
    - Name (Inter semibold)
    - Version + ID (JetBrains Mono)
    """

    def __init__(self, *, view: QListView, main_view: QWidget | None = None) -> None:
        super().__init__(view)
        self._view = view
        self._main_view = main_view
        self._hovered_row: int | None = None
        self._hovered_pill_row: int | None = None
        self._hovered_health_row: int | None = None
        self._hovered_notes_row: int | None = None
        self._card_size = QSize(320, 260)
        self._gap_px = 24
        self._active_department: str | None = None
        self._active_department_icon_name: str | None = None  # from pipeline (subdepartment-safe)
        self._active_project_root: str | None = None  # current open project (Projects page)
        self._dept_registry: DepartmentRegistry | None = None
        self._show_publish: bool = False
        self._active_dcc_cache: dict[str, str] = {}  # "item_path|department" -> dcc_id
        self._inspector_hidden_departments: frozenset[str] = frozenset()
        self._show_dept_chips: bool = False
        self._tile_meta_show_id: bool = True
        self._tile_meta_show_version: bool = True
        self._tile_meta_show_last_updated: bool = False
        self._tile_meta_show_latest_note: bool = False
        self._tile_meta_show_current_department: bool = False
        self._tile_meta_show_status_pill: bool = True
        self._active_department_label: str | None = None  # pipeline label for tile meta + header

        # Theme cache (no per-paint parsing / allocations)
        self._c_card_bg = QColor(MONOS_COLORS["card_bg"])
        self._c_card_hover = QColor(MONOS_COLORS["card_hover"])
        self._c_border = QColor(MONOS_COLORS["border"])
        self._c_text_primary = QColor(MONOS_COLORS["text_primary"])
        self._c_text_primary_highlight = QColor(MONOS_COLORS["text_primary_highlight"])
        self._c_text_primary_selected = QColor(MONOS_COLORS["text_primary_selected"])
        self._c_text_meta = QColor(MONOS_COLORS["text_meta"])
        self._pen_border = QPen(self._c_border, 1)
        self._c_selected = QColor(MONOS_COLORS["blue_600"])
        self._pen_selected = QPen(self._c_selected, 2)
        self._c_active = QColor(MONOS_COLORS["amber_400"])
        self._c_active.setAlphaF(0.7)
        self._pen_active = QPen(self._c_active, 2)

        # Font cache (no per-paint allocations)
        # Shared thumb tag style (status + filter tags): same geometry, only color differs.
        self._font_thumb_tag = monos_font("Inter", int(THUMB_TAG_STYLE["font_size"]), QFont.Weight(int(THUMB_TAG_STYLE["font_weight"])))
        self._font_name = monos_font("Inter", 13, QFont.Weight.Medium)
        self._font_mono = monos_font("JetBrains Mono", 8)
        # Shared meta style (mono) for ALL cards.
        self._font_meta_mono = QFont(self._font_mono)
        self._font_meta = monos_font("Inter", 11)
        self._font_dept_chip = monos_font("Inter", 10, QFont.Weight.DemiBold)

        st = view.style()
        self._icon_eye = lucide_icon("eye", size=16, color_hex=MONOS_COLORS["text_primary"])
        self._icon_download = lucide_icon("download", size=16, color_hex=MONOS_COLORS["text_primary"])
        self._icon_more = lucide_icon("ellipsis", size=16, color_hex=MONOS_COLORS["text_primary"])
        self._icon_note_meta = lucide_icon("message-circle", size=12, color_hex=MONOS_COLORS["text_meta"])
        self._icon_calendar_meta = lucide_icon("calendar", size=12, color_hex=MONOS_COLORS["text_meta"])
        self._icon_hash_meta = lucide_icon("hash", size=12, color_hex=MONOS_COLORS["text_meta"])

        self._fast_paint = False
        self._thumb_cache: dict[tuple[int, int, int, bool], QPixmap] = {}
        self._thumb_cache_max = 320
        self._production_reg_key: tuple[str | None, str] | None = None
        self._production_reg_cache = None

    @staticmethod
    def _norm(s: str | None) -> str:
        return (s or "").strip().casefold()

    def set_fast_paint(self, enabled: bool) -> None:
        if self._fast_paint == bool(enabled):
            return
        self._fast_paint = bool(enabled)
        self._view.viewport().update()

    def _should_fast_paint(self) -> bool:
        if self._fast_paint:
            return True
        view = self._view
        if hasattr(view, "rubber_band_selecting") and view.rubber_band_selecting():
            return True
        return False

    def _interaction_busy(self) -> bool:
        if self._should_fast_paint():
            return True
        buttons = QApplication.mouseButtons()
        return bool(buttons & (Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton))

    def _clear_thumb_cache(self) -> None:
        self._thumb_cache.clear()

    def _repaint_delegate_rows(self, *rows: int | None) -> None:
        m = self._view.model()
        if m is None:
            self._view.viewport().update()
            return
        seen: set[int] = set()
        for row in rows:
            if row is None or row in seen:
                continue
            seen.add(int(row))
            idx = m.index(int(row), 0)
            if idx.isValid():
                self._view.update(idx)
        if not seen:
            self._view.viewport().update()

    def _production_status_registry(self):
        root = self._active_project_root
        dep = (self._active_department or "").strip()
        cache_key = (root, dep)
        if cache_key == self._production_reg_key:
            return self._production_reg_cache
        self._production_reg_key = cache_key
        if not root:
            self._production_reg_cache = None
            return None
        try:
            self._production_reg_cache = _status_registry_for_view(root, dep or None)
        except Exception:
            self._production_reg_cache = None
        return self._production_reg_cache

    def _cached_thumb_crop(self, icon: QIcon, thumb: QRect, *, fast: bool) -> QPixmap | None:
        if thumb.width() <= 0 or thumb.height() <= 0:
            return None
        key = (thumb.width(), thumb.height(), int(icon.cacheKey()), bool(fast))
        cached = self._thumb_cache.get(key)
        if cached is not None and not cached.isNull():
            return cached
        src = icon.pixmap(256, 256)
        if src.isNull():
            return None
        mode = Qt.FastTransformation if fast else Qt.SmoothTransformation
        scaled = src.scaled(thumb.size(), Qt.KeepAspectRatioByExpanding, mode)
        sx = max(0, (scaled.width() - thumb.width()) // 2)
        sy = max(0, (scaled.height() - thumb.height()) // 2)
        crop = scaled.copy(QRect(QPoint(sx, sy), thumb.size()))
        if crop.isNull():
            return None
        if len(self._thumb_cache) >= self._thumb_cache_max:
            self._thumb_cache.clear()
        self._thumb_cache[key] = crop
        return crop

    def set_hovered_index(self, index) -> None:
        if self._interaction_busy():
            return
        row = index.row() if index and index.isValid() else None
        if self._hovered_row == row:
            return
        old_row = self._hovered_row
        self._hovered_row = row
        self._repaint_delegate_rows(old_row, row)

    def set_hovered_pill_row(self, row: int | None) -> None:
        if self._interaction_busy():
            return
        if self._hovered_pill_row == row:
            return
        old_row = self._hovered_pill_row
        self._hovered_pill_row = row
        self._repaint_delegate_rows(old_row, row)

    def set_hovered_health_row(self, row: int | None) -> None:
        if self._interaction_busy():
            return
        if self._hovered_health_row == row:
            return
        old_row = self._hovered_health_row
        self._hovered_health_row = row
        self._repaint_delegate_rows(old_row, row)

    def set_hovered_notes_row(self, row: int | None) -> None:
        if self._interaction_busy():
            return
        if self._hovered_notes_row == row:
            return
        old_row = self._hovered_notes_row
        self._hovered_notes_row = row
        self._repaint_delegate_rows(old_row, row)

    def set_card_size(self, size: QSize) -> None:
        if size.isValid() and size != self._card_size:
            self._card_size = size
            self._clear_thumb_cache()
            self._view.viewport().update()

    def set_gap_px(self, gap_px: int) -> None:
        if gap_px > 0 and gap_px != self._gap_px:
            self._gap_px = gap_px
            self._view.viewport().update()

    def set_active_department(self, department: str | None, *, icon_name: str | None = None, label: str | None = None) -> None:
        dep = (department or "").strip() or None
        lab = (label or "").strip() or None
        ic = (icon_name or "").strip() or None
        if dep == self._active_department and ic == self._active_department_icon_name and lab == self._active_department_label:
            return
        self._active_department = dep
        self._active_department_icon_name = ic
        self._active_department_label = lab
        self._production_reg_key = None
        self._production_reg_cache = None
        self.invalidate_notes_open_count_cache()
        self._view.viewport().update()

    def set_active_project_root(self, path: str | None) -> None:
        p = path or None
        if p == self._active_project_root:
            return
        self._active_project_root = p
        self._production_reg_key = None
        self._production_reg_cache = None
        self._view.viewport().update()

    def set_dept_registry(self, registry: DepartmentRegistry | None) -> None:
        if registry is self._dept_registry:
            return
        self._dept_registry = registry
        self._view.viewport().update()

    def set_show_publish(self, show_publish: bool) -> None:
        if self._show_publish == show_publish:
            return
        self._show_publish = show_publish
        self._view.viewport().update()

    def set_inspector_hidden_departments(self, hidden: set[str] | frozenset | None) -> None:
        h = frozenset(hidden or ())
        if h == self._inspector_hidden_departments:
            return
        self._inspector_hidden_departments = h
        self._view.viewport().update()

    def set_show_dept_chips(self, show: bool) -> None:
        if show == self._show_dept_chips:
            return
        self._show_dept_chips = bool(show)
        self._view.viewport().update()

    def set_tile_meta_display(
        self,
        *,
        show_id: bool,
        show_version: bool,
        show_last_updated: bool,
        show_latest_note: bool,
        show_current_department: bool,
        show_status_pill: bool,
    ) -> None:
        if (
            self._tile_meta_show_id == show_id
            and self._tile_meta_show_version == show_version
            and self._tile_meta_show_last_updated == show_last_updated
            and self._tile_meta_show_latest_note == show_latest_note
            and self._tile_meta_show_current_department == show_current_department
            and self._tile_meta_show_status_pill == show_status_pill
        ):
            return
        self._tile_meta_show_id = bool(show_id)
        self._tile_meta_show_version = bool(show_version)
        self._tile_meta_show_last_updated = bool(show_last_updated)
        self._tile_meta_show_latest_note = bool(show_latest_note)
        self._tile_meta_show_current_department = bool(show_current_department)
        self._tile_meta_show_status_pill = bool(show_status_pill)
        self._view.viewport().update()

    def get_active_dcc(self, item_path: Path | None, department: str | None) -> str | None:
        if not item_path or not department:
            return None
        key = f"{item_path}|{(department or '').strip().casefold()}"
        cached = self._active_dcc_cache.get(key)
        if cached is not None:
            return cached
        val = _item_active_dcc(item_path, department)
        if val:
            self._active_dcc_cache[key] = val
        return val

    def set_active_dcc(self, item_path: Path, department: str, dcc_id: str) -> None:
        key = f"{item_path}|{(department or '').strip().casefold()}"
        self._active_dcc_cache[key] = dcc_id
        _write_active_dcc(item_path, department, dcc_id)
        self._view.viewport().update()

    @staticmethod
    def _rounded_rect(p: QPainter, r: QRect, radius: int, *, fill: QColor, pen: QPen | None = None) -> None:
        p.setPen(Qt.NoPen if pen is None else pen)
        p.setBrush(fill)
        p.drawRoundedRect(r, radius, radius)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        _timer = QElapsedTimer()
        _timer.start()
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            super().paint(painter, option, index)
            return

        # Section header (inbox_section): full-width bar, no thumbnail
        if item.kind.value == "inbox_section":
            g = max(0, int(self._gap_px))
            r = option.rect.adjusted(0, 0, -g, -g)
            if r.width() <= 0 or r.height() <= 0:
                return
            p = painter
            p.save()
            try:
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setRenderHint(QPainter.TextAntialiasing, True)
                p.fillRect(r, QColor("#1f1f23"))
                p.setPen(QColor("#a1a1aa"))
                f = p.font()
                f.setWeight(QFont.Weight.DemiBold)
                f.setPointSize(11)
                p.setFont(f)
                name = (item.name or "").strip() or "—"
                p.drawText(r.adjusted(12, 0, -12, 0), Qt.AlignLeft | Qt.AlignVCenter, name)
            finally:
                p.restore()
            return

        # Paint inside the grid cell leaving explicit gap on right/bottom.
        g = max(0, int(self._gap_px))
        r = option.rect.adjusted(0, 0, -g, -g)
        if r.width() <= 0 or r.height() <= 0:
            return
        p = painter
        p.save()
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)

            fast = self._should_fast_paint()

            # Card background
            bg = self._c_card_bg
            hover = bool(self._hovered_row == index.row()) and not fast
            if hover:
                bg = self._c_card_hover

            selected = bool(option.state & QStyle.State_Selected)
            active = (
                item.kind == ViewItemKind.PROJECT
                and self._active_project_root
                and str(item.path) == self._active_project_root
            )

            # Dim card when showing Published mode but item has no publish (strong dim, no selection)
            _dim_card = False
            if (
                not fast
                and self._show_publish
                and isinstance(item.ref, (Asset, Shot))
                and not _item_has_publish_for_department(item.ref, self._active_department)
            ):
                _dim_card = True
            if _dim_card:
                if selected:
                    p.setOpacity(1.0)
                elif hover:
                    p.setOpacity(0.45)
                else:
                    p.setOpacity(0.1)
            # Work mode: lighter dim when item has no work file (card still selectable)
            _dim_card_work = False
            if (
                not fast
                and not self._show_publish
                and isinstance(item.ref, (Asset, Shot))
                and not _item_has_work_for_department(item.ref, self._active_department)
            ):
                _dim_card_work = True
            if _dim_card_work:
                if selected:
                    p.setOpacity(1.0)
                elif hover:
                    p.setOpacity(0.8)
                else:
                    p.setOpacity(0.4)

            border_px = 2 if (selected or active) else 1
            if selected:
                border_pen = self._pen_selected  # 2px Blue-600
            elif active:
                border_pen = self._pen_active  # 2px Amber-400 (active project)
            else:
                border_pen = self._pen_border

            outer = r
            # Fill first (no border), then draw content, then draw border ON TOP.
            self._rounded_rect(p, outer, 12, fill=bg, pen=None)

            # Clip content inside the border so thumbnails never paint over it.
            inner = outer.adjusted(border_px, border_px, -border_px, -border_px)
            inner_radius = max(0, 12 - border_px)
            clip = QPainterPath()
            clip.addRoundedRect(inner, inner_radius, inner_radius)
            p.setClipPath(clip)

            # 16:9 thumbnail region (fixed aspect)
            thumb_w = inner.width()
            thumb_h = max(1, int(thumb_w * 9 / 16))
            thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))

            # Draw thumbnail from icon (center-crop)
            icon = index.data(Qt.DecorationRole)
            if isinstance(icon, QIcon):
                crop = self._cached_thumb_crop(icon, thumb, fast=fast)
                if crop is not None and not crop.isNull():
                    p.drawPixmap(thumb, crop)

            if fast:
                p.setClipping(False)
                y = thumb.bottom() + _GRID_META_PAD_TOP
                x = inner.left() + 16
                w = inner.width() - 32
                p.setFont(self._font_name)
                if selected:
                    p.setPen(self._c_text_primary_selected)
                else:
                    p.setPen(self._c_text_primary)
                name_rect = QRect(x, y, w, _GRID_NAME_LINE_H)
                p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, display_name_for_item(item))
                p.setPen(border_pen)
                p.setBrush(Qt.NoBrush)
                stroke_inset = 1
                border_rect = outer.adjusted(stroke_inset, stroke_inset, -stroke_inset, -stroke_inset)
                p.drawRoundedRect(border_rect, 12, 12)
                return

            def status_key() -> str:
                return _overall_status_paint_key_for_item(
                    item,
                    self._active_department,
                    project_root=self._active_project_root,
                    hidden_departments=set(self._inspector_hidden_departments),
                )

            def status_style(k: str) -> tuple[QColor, QColor, QColor]:
                # (text, bg, border) with higher alpha for readability over thumbnails.
                a_bg = int(THUMB_TAG_STYLE["bg_alpha"])
                a_border = int(THUMB_TAG_STYLE["border_alpha"])
                def with_alpha(base: QColor, a: int) -> QColor:
                    c2 = QColor(base)
                    c2.setAlpha(int(a))
                    return c2
                if k == "ready":
                    c = QColor(MONOS_COLORS["emerald_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "progress":
                    # Match Inspector: PROGRESS uses amber, not blue.
                    c = QColor(MONOS_COLORS["amber_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "blocked":
                    c = QColor(MONOS_COLORS["red_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "review":
                    c = QColor("#60a5fa")
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "hold":
                    c = QColor("#fbbf24")
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "na":
                    c = QColor("#52525b")
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                c = QColor(MONOS_COLORS["waiting"])
                return (c, with_alpha(c, a_bg), with_alpha(c, a_border))

            # Unified thumb tag renderer (status + contextual filter tags).
            p.setFont(self._font_thumb_tag)
            metrics = p.fontMetrics()
            pad_x = int(THUMB_TAG_STYLE["pad_x"])
            pad_y = int(THUMB_TAG_STYLE["pad_y"])
            radius = int(THUMB_TAG_STYLE["radius"])
            border_px = int(THUMB_TAG_STYLE["border_px"])

            def draw_thumb_tag(*, x: int, y: int, text: str, text_color: QColor, bg_color: QColor, border_color: QColor) -> QRect:
                t = (text or "").strip().upper()
                w2 = metrics.horizontalAdvance(t) + pad_x * 2
                h2 = metrics.height() + pad_y * 2
                r2 = QRect(x, y, w2, h2)
                self._rounded_rect(p, r2, radius, fill=bg_color, pen=QPen(border_color, border_px))
                p.setPen(text_color)
                p.drawText(r2, Qt.AlignCenter, t)
                return r2

            # Item health (top-right) + notes chip to its left — only asset/shot on disk path.
            dept_focus = (self._active_department or "").strip()
            chip_sz = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
            health_rect: QRect | None = None
            health_obj: ItemHealth | None = None
            if dept_focus and isinstance(item.ref, (Asset, Shot)):
                active_dcc = (
                    self.get_active_dcc(getattr(item, "path", None), self._active_department)
                    if getattr(item, "path", None)
                    else None
                )
                health_obj = assess_view_item_health(
                    item.ref,
                    dept_focus,
                    active_dcc_id=active_dcc,
                )
                if health_obj is not None:
                    health_rect = QRect(
                        thumb.right() - 12 - chip_sz,
                        thumb.top() + 12,
                        chip_sz,
                        chip_sz,
                    )

            if isinstance(item.ref, (Asset, Shot)) and getattr(item, "path", None):
                mw = self._main_view
                n, nmode = (0, "empty")
                if mw is not None and hasattr(mw, "notes_badge_state"):
                    try:
                        n, nmode = mw.notes_badge_state(item.path)  # type: ignore[attr-defined]
                    except Exception:
                        n, nmode = 0, "empty"
                note_rect = _thumb_note_chip_rect(thumb, health_rect)
                note_hover = self._hovered_notes_row is not None and self._hovered_notes_row == index.row()
                _paint_note_icon_chip(p, note_rect, n, visual_mode=nmode, hovered=note_hover)

            if health_obj is not None and health_rect is not None:
                health_hover = (
                    self._hovered_health_row is not None
                    and self._hovered_health_row == index.row()
                )
                _paint_health_icon_chip(p, health_rect, health_obj, hovered=health_hover)

            # Type + Department icons (top-left, side by side, fully round, distinct colors)
            icon_size = 16
            pad = 5
            chip_r = (icon_size + pad * 2) // 2  # radius for circle
            chip_h = chip_r * 2
            gap = 4
            ix = thumb.left() + 12
            iy = thumb.top() + 12
            # Colors: project / shot / type (asset) / department — all different
            type_badge_raw = (item.type_badge or "").strip()
            type_badge_lower = type_badge_raw.lower()
            if item.kind.value == "project":
                type_chip_color = QColor("#8b5cf6")  # violet
            elif item.kind.value == "shot":
                type_chip_color = QColor(MONOS_COLORS["amber_500"])
            else:
                type_chip_color = QColor(MONOS_COLORS["emerald_500"])
            type_chip_color.setAlpha(220)
            type_icon_name = _TYPE_ICON_MAP.get(type_badge_lower) or _TYPE_ICON_MAP.get(type_badge_raw) or "box"
            type_icon = lucide_icon(type_icon_name, size=icon_size, color_hex="#ffffff")
            type_pix = type_icon.pixmap(icon_size, icon_size)
            if not type_pix.isNull():
                cx, cy = ix + chip_r, iy + chip_r
                p.setPen(Qt.NoPen)
                p.setBrush(type_chip_color)
                p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                p.drawPixmap(ix + pad, iy + pad, type_pix)
                ix += chip_h + gap
            dep = (self._active_department or "").strip()
            if dep:
                dept_key = self._norm(dep)
                dept_icon_name = (self._active_department_icon_name or "").strip() or _DEPT_ICON_MAP.get(dept_key, "layers")
                dept_chip_color = QColor(MONOS_COLORS["blue_500"])
                dept_chip_color.setAlpha(220)
                dept_icon = lucide_icon(dept_icon_name, size=icon_size, color_hex="#ffffff")
                dept_pix = dept_icon.pixmap(icon_size, icon_size)
                if not dept_pix.isNull():
                    cx, cy = ix + chip_r, iy + chip_r
                    p.setPen(Qt.NoPen)
                    p.setBrush(dept_chip_color)
                    p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                    p.drawPixmap(ix + pad, iy + pad, dept_pix)

            # DCC badges (bottom-right of thumb) — filesystem-driven; "exists" = icon, "creating" = "Creating…"
            # Prefer dcc_work_states (scan) so subdepartments show badges; fallback to registry for "creating" only.
            def dcc_badges_for_item() -> list[tuple[QIcon | None, str, str]]:
                """Returns (icon or None, dcc_id, status) with status in ("exists", "creating")."""
                out: list[tuple[QIcon | None, str, str]] = []
                ref = item.ref
                if not isinstance(ref, (Asset, Shot)):
                    return out
                try:
                    reg = get_default_dcc_registry()
                except Exception:
                    return out
                active_key = self._norm((self._active_department or "").strip())
                states = getattr(ref, "dcc_work_states", ()) or ()
                seen: set[tuple[str, str]] = set()

                def add_badge(dept_id: str, dcc_id: str, status: str) -> None:
                    if (dept_id, dcc_id) in seen:
                        return
                    seen.add((dept_id, dcc_id))
                    if status == "creating":
                        out.append((None, dcc_id, "creating"))
                        return
                    if status != "exists":
                        return
                    try:
                        info = reg.get_dcc_info(dcc_id) if dcc_id else None
                    except Exception:
                        info = None
                    slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
                    color = info.get("brand_color_hex") if isinstance(info, dict) else None
                    if isinstance(slug, str) and slug.strip():
                        ic = brand_icon(slug.strip(), size=14, color_hex=(color if isinstance(color, str) else None))
                    else:
                        ic = lucide_icon("layers", size=14, color_hex=MONOS_COLORS["text_label"])
                    out.append((ic, dcc_id, "exists"))

                for (dept_id, dcc_id), _state in states:
                    dept_id = (dept_id or "").strip()
                    dcc_id = (dcc_id or "").strip()
                    if not dept_id or not dcc_id:
                        continue
                    if active_key and self._norm(dept_id) != active_key:
                        continue
                    status = resolve_dcc_status(ref, dept_id, dcc_id)
                    if status in ("exists", "creating"):
                        add_badge(dept_id, dcc_id, status)
                for d in getattr(ref, "departments", ()) or ():
                    dept_name = getattr(d, "name", "") or ""
                    if active_key and self._norm(dept_name) != active_key:
                        continue
                    dre = self._dept_registry
                    if dre is not None:
                        dcc_ids = dre.supported_dcc_ids(reg, dept_name)
                    else:
                        dcc_ids = reg.get_available_dccs(dept_name) or []
                    for dcc_id in dcc_ids:
                        dcc_id = (dcc_id or "").strip()
                        if not dcc_id:
                            continue
                        status = resolve_dcc_status(ref, dept_name, dcc_id)
                        if status == "creating":
                            add_badge(dept_name, dcc_id, "creating")
                return out

            if self._show_publish:
                # Published mode: show version pill instead of DCC badges
                if isinstance(item.ref, (Asset, Shot)):
                    pub_ver = _card_publish_version(item.ref, self._active_department)
                    if pub_ver and pub_ver != "—":
                        pub_label = pub_ver
                    else:
                        pub_label = None
                    if pub_label:
                        pub_font = monos_font("Inter", 9, QFont.Weight.Bold)
                        p.setFont(pub_font)
                        fm = p.fontMetrics()
                        text_w = fm.horizontalAdvance(pub_label)
                        pill_pad_x = 10
                        pill_h = 24
                        pill_w = text_w + pill_pad_x * 2
                        pill_r = pill_h // 2
                        pill_x = thumb.right() - 12 - pill_w
                        pill_y = thumb.bottom() - 12 - pill_h
                        pill_rect = QRect(pill_x, pill_y, pill_w, pill_h)
                        p.setPen(Qt.NoPen)
                        p.setBrush(QColor(MONOS_COLORS["blue_600"]))
                        p.drawRoundedRect(pill_rect, pill_r, pill_r)
                        p.setPen(QColor(255, 255, 255))
                        p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, pub_label)
            else:
                dcc_list = dcc_badges_for_item()
                if dcc_list:
                    size = 16
                    pad = 4
                    gap = 3
                    max_show = 4
                    chip_h = size + pad * 2
                    chip_r = chip_h // 2
                    creating_chip_w = 56
                    widths = [creating_chip_w if s == "creating" else chip_h for (_, _, s) in dcc_list[:max_show]]
                    row_w = sum(widths) + (len(widths) - 1) * gap
                    base_x = thumb.right() - 12 - row_w
                    base_y = thumb.bottom() - 12 - chip_h
                    creating_font = monos_font("Inter", 9)
                    dcc_bg = QColor(0, 0, 0, 160)
                    active_dcc = self.get_active_dcc(getattr(item, "path", None), self._active_department) if getattr(item, "path", None) else None
                    _existing_ids = {(_d or "").strip() for (_, _d, _s) in dcc_list[:max_show] if _s == "exists"}
                    if not active_dcc or active_dcc not in _existing_ids:
                        active_dcc = None
                        for _ic, _did, _st in dcc_list[:max_show]:
                            if _st == "exists" and (_did or "").strip():
                                active_dcc = (_did or "").strip()
                                break
                    _pen_dcc_active = QPen(self._c_active, 2)
                    x_cursor = base_x
                    for i, (dcc_icon, _dcc_id, badge_status) in enumerate(dcc_list[:max_show]):
                        w = widths[i]
                        bg_rect = QRect(x_cursor, base_y, w, chip_h)
                        is_active = bool(active_dcc and (_dcc_id or "").strip() == active_dcc)
                        if badge_status == "creating":
                            p.setPen(Qt.NoPen)
                            p.setBrush(dcc_bg)
                            p.drawRoundedRect(bg_rect, chip_r, chip_r)
                            if is_active:
                                p.setPen(_pen_dcc_active)
                                p.setBrush(Qt.NoBrush)
                                p.drawRoundedRect(bg_rect, chip_r, chip_r)
                            _dcc_debug_log.debug("paint DCC badge Creating… entity_path=%r dcc_id=%r", getattr(item.ref, "path", None), _dcc_id)
                            p.setFont(creating_font)
                            p.setPen(QColor(255, 255, 255))
                            p.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, "Creating…")
                        else:
                            cx = x_cursor + chip_r
                            cy = base_y + chip_r
                            p.setPen(Qt.NoPen)
                            p.setBrush(dcc_bg)
                            p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                            if is_active:
                                p.setPen(_pen_dcc_active)
                                p.setBrush(Qt.NoBrush)
                                p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                            if dcc_icon is not None and not dcc_icon.isNull():
                                pix = dcc_icon.pixmap(size, size)
                                if not pix.isNull():
                                    p.drawPixmap(x_cursor + pad, base_y + pad, pix)
                        x_cursor += w + gap

            # Stop clipping before text to avoid rounded-corner cropping issues
            p.setClipping(False)

            # Text blocks under thumbnail (geometry: tile_grid_meta_line_count, grid_card_meta_block_height_asset_shot).
            y = thumb.bottom() + _GRID_META_PAD_TOP
            x = inner.left() + 16
            w = inner.width() - 32

            p.setFont(self._font_name)
            # Highlight state: hover uses highlight (blue), selected uses selected (fafafa).
            if selected:
                p.setPen(self._c_text_primary_selected)
            elif hover:
                p.setPen(self._c_text_primary_highlight)
            else:
                p.setPen(self._c_text_primary)
            name_rect = QRect(x, y, w, _GRID_NAME_LINE_H)
            p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, display_name_for_item(item))

            y_meta = thumb.bottom() + _grid_asset_shot_meta_head_to_first_line()
            if item.kind.value == "project":
                stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
                shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
                assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
                meta = f"ASSETS {assets}   SHOTS {shots}"
                p.setFont(self._font_meta_mono)
                p.setPen(self._c_text_meta)
                meta_rect = QRect(x, y_meta, w, _GRID_META_LINE_H)
                p.drawText(meta_rect, Qt.AlignLeft | Qt.AlignVCenter, meta)
                status = getattr(stats, "status", None) or "WAITING"
                line = project_status_label(status)
                color_hex = project_status_color_hex(status)
                y_pills = y_meta + _GRID_META_LINE_H + _GRID_GAP_BETWEEN_META_LINES
                p.setFont(self._font_dept_chip)
                fm_pill = p.fontMetrics()
                chip_h = _grid_status_pill_line_height(fm_pill)
                chip_pad_x = 8
                dot_r = 3
                tw = fm_pill.horizontalAdvance(line) + chip_pad_x * 2 + dot_r * 2 + 6
                tw = min(tw, w)
                chip_rect = QRect(x, y_pills, tw, chip_h)
                pill_hover = self._hovered_pill_row is not None and self._hovered_pill_row == index.row()
                _paint_status_pill_chip(
                    p,
                    chip_rect,
                    line,
                    color_hex,
                    fm=fm_pill,
                    hovered=pill_hover,
                )
            else:
                cy = y_meta
                p.setFont(self._font_meta_mono)
                p.setPen(self._c_text_meta)
                active_dep = (self._active_department or "").strip()
                active_dcc = self.get_active_dcc(getattr(item, "path", None), self._active_department) if getattr(item, "path", None) else None
                ver_str = (
                    _card_version_for_display(item.ref, self._active_department, self._show_publish, active_dcc_id=active_dcc)
                    if isinstance(item.ref, (Asset, Shot))
                    else None
                )
                if self._tile_meta_show_id or self._tile_meta_show_version:
                    line_top = cy
                    ico_px = 12
                    ico_gap = 4
                    fm = p.fontMetrics()
                    if (
                        self._tile_meta_show_id
                        and self._tile_meta_show_version
                        and active_dep
                        and ver_str is not None
                    ):
                        prefix = f"ID {item.name}   "
                        ver_text = ver_str if ver_str != "—" else "v —"
                        min_tail = ico_px + ico_gap + min(fm.horizontalAdvance(ver_text), 28)
                        prefix_max = max(0, w - min_tail)
                        prefix_draw = fm.elidedText(prefix, Qt.TextElideMode.ElideRight, prefix_max)
                        pw = fm.horizontalAdvance(prefix_draw)
                        p.drawText(
                            QRect(x, line_top, max(1, prefix_max), _GRID_META_LINE_H),
                            Qt.AlignLeft | Qt.AlignVCenter,
                            prefix_draw,
                        )
                        x_h = x + pw + 4
                        pix_hash = self._icon_hash_meta.pixmap(ico_px, ico_px)
                        if not pix_hash.isNull():
                            iy = line_top + (_GRID_META_LINE_H - ico_px) // 2
                            p.drawPixmap(x_h, iy, pix_hash)
                        x_t = x_h + ico_px + ico_gap
                        tw = max(0, x + w - x_t)
                        elided = fm.elidedText(ver_text, Qt.TextElideMode.ElideRight, tw)
                        p.drawText(
                            QRect(x_t, line_top, tw, _GRID_META_LINE_H),
                            Qt.AlignLeft | Qt.AlignVCenter,
                            elided,
                        )
                    elif self._tile_meta_show_version and not self._tile_meta_show_id:
                        ver_text = (ver_str or "—") if isinstance(item.ref, (Asset, Shot)) else "—"
                        pix_hash = self._icon_hash_meta.pixmap(ico_px, ico_px)
                        if not pix_hash.isNull():
                            iy = line_top + (_GRID_META_LINE_H - ico_px) // 2
                            p.drawPixmap(x, iy, pix_hash)
                        tx = x + ico_px + ico_gap
                        tw_line = max(0, w - ico_px - ico_gap)
                        elided = fm.elidedText(ver_text, Qt.TextElideMode.ElideRight, tw_line)
                        p.drawText(
                            QRect(tx, line_top, tw_line, _GRID_META_LINE_H),
                            Qt.AlignLeft | Qt.AlignVCenter,
                            elided,
                        )
                    else:
                        p.drawText(
                            QRect(x, line_top, w, _GRID_META_LINE_H),
                            Qt.AlignLeft | Qt.AlignVCenter,
                            f"ID {item.name}",
                        )
                    cy += _GRID_META_LINE_H + _GRID_GAP_BETWEEN_META_LINES
                if self._tile_meta_show_last_updated:
                    active_dcc = (
                        self.get_active_dcc(getattr(item, "path", None), self._active_department)
                        if isinstance(item, ViewItem)
                        and getattr(item, "path", None)
                        and self._active_department
                        else None
                    )
                    lu = (
                        _view_item_last_updated_display(
                            item,
                            show_publish=self._show_publish,
                            active_department=self._active_department,
                            active_dcc_id=active_dcc,
                        )
                        if isinstance(item, ViewItem)
                        else "—"
                    )
                    line_top = cy
                    ico_px = 12
                    ico_gap = 4
                    pix_cal = self._icon_calendar_meta.pixmap(ico_px, ico_px)
                    if not pix_cal.isNull():
                        iy = line_top + (_GRID_META_LINE_H - ico_px) // 2
                        p.drawPixmap(x, iy, pix_cal)
                    tx = x + ico_px + ico_gap
                    tw_line = max(0, w - ico_px - ico_gap)
                    elided = p.fontMetrics().elidedText(lu, Qt.TextElideMode.ElideRight, tw_line)
                    p.drawText(
                        QRect(tx, line_top, tw_line, _GRID_META_LINE_H),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        elided,
                    )
                    cy += _GRID_META_LINE_H + _GRID_GAP_BETWEEN_META_LINES
                if self._tile_meta_show_latest_note and isinstance(item.ref, (Asset, Shot)) and getattr(item, "path", None):
                    from monostudio.core.item_comments import latest_note_preview_line

                    snip, last_done = latest_note_preview_line(
                        Path(item.path),
                        self._active_department,
                    )
                    note_line = snip if snip else "—"
                    strike = bool(snip and last_done)
                    ico_px = 12
                    ico_gap = 4
                    line_top = cy
                    pix_note = self._icon_note_meta.pixmap(ico_px, ico_px)
                    if not pix_note.isNull():
                        iy = line_top + (_GRID_META_LINE_H - ico_px) // 2
                        p.drawPixmap(x, iy, pix_note)
                    tx = x + ico_px + ico_gap
                    tw_line = max(0, w - ico_px - ico_gap)
                    p.save()
                    try:
                        f_note = QFont(self._font_meta_mono)
                        if strike:
                            f_note.setStrikeOut(True)
                        p.setFont(f_note)
                        p.setPen(
                            QColor(MONOS_COLORS.get("text_muted", "#71717a"))
                            if strike
                            else self._c_text_meta
                        )
                        elided = QFontMetrics(f_note).elidedText(
                            note_line, Qt.TextElideMode.ElideRight, tw_line
                        )
                        p.drawText(
                            QRect(tx, line_top, tw_line, _GRID_META_LINE_H),
                            Qt.AlignLeft | Qt.AlignVCenter,
                            elided,
                        )
                    finally:
                        p.restore()
                    cy += _GRID_META_LINE_H + _GRID_GAP_BETWEEN_META_LINES
                if self._tile_meta_show_current_department and active_dep:
                    dep_disp = (self._active_department_label or active_dep).strip()
                    dept_line = f"DEPT  {dep_disp.replace('_', ' ').upper()}"
                    p.drawText(QRect(x, cy, w, _GRID_META_LINE_H), Qt.AlignLeft | Qt.AlignVCenter, dept_line)
                    cy += _GRID_META_LINE_H + _GRID_GAP_BETWEEN_META_LINES

            # Dept status pill — only when sidebar department is focused (no multi-dept chips without focus).
            ad_focus = (self._active_department or "").strip()
            if (
                self._tile_meta_show_status_pill
                and ad_focus
                and isinstance(item.ref, (Asset, Shot))
                and self._active_project_root
            ):
                n_lines = tile_grid_meta_line_count(
                    show_id=self._tile_meta_show_id,
                    show_version=self._tile_meta_show_version,
                    show_last_updated=self._tile_meta_show_last_updated,
                    show_latest_note=self._tile_meta_show_latest_note,
                    show_current_department=self._tile_meta_show_current_department,
                    active_department=self._active_department,
                )
                y_pills = thumb.bottom() + _grid_asset_shot_y_pills_offset_from_thumb_bottom(n_meta_lines=n_lines)
                try:
                    reg = self._production_status_registry()
                    if reg is None:
                        raise ValueError("no production registry")
                    sid = aggregate_status_id_for_item(
                        item.ref,
                        active_department=ad_focus,
                        hidden_departments=set(self._inspector_hidden_departments),
                        registry=reg,
                    )
                    p.setFont(self._font_dept_chip)
                    fm = p.fontMetrics()
                    chip_h = _grid_status_pill_line_height(fm)
                    chip_pad_x = 8
                    dot_r = 3
                    line = reg.label_for(sid)
                    tw = fm.horizontalAdvance(line) + chip_pad_x * 2 + dot_r * 2 + 6
                    tw = min(tw, w)
                    chip_rect = QRect(x, y_pills, tw, chip_h)
                    pill_hover = self._hovered_pill_row is not None and self._hovered_pill_row == index.row()
                    p.setPen(Qt.NoPen)
                    qc = QColor(color_hex_for_status_id(sid, reg))
                    bg = QColor(qc)
                    bg.setAlpha(72 if pill_hover else 42)
                    p.setBrush(bg)
                    p.drawRoundedRect(chip_rect, 8, 8)
                    if pill_hover:
                        bc = QColor(qc)
                        bc.setAlpha(140)
                        p.setPen(QPen(bc, 1))
                        p.setBrush(Qt.NoBrush)
                        p.drawRoundedRect(chip_rect, 8, 8)
                    p.setPen(Qt.NoPen)
                    p.setBrush(qc)
                    p.drawEllipse(QPoint(chip_rect.left() + chip_pad_x + dot_r, chip_rect.center().y()), dot_r, dot_r)
                    p.setPen(QColor(MONOS_COLORS["text_primary"] if pill_hover else MONOS_COLORS["text_label"]))
                    text_rect = chip_rect.adjusted(chip_pad_x + dot_r * 2 + 6, 0, -4, 0)
                    elided = fm.elidedText(line, Qt.TextElideMode.ElideRight, text_rect.width())
                    p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, elided)
                except Exception:
                    pass

            # Border on top (selected border = 2px)
            p.setPen(border_pen)
            p.setBrush(Qt.NoBrush)
            # Keep stroke safely inside cell.
            stroke_inset = 1
            border_rect = outer.adjusted(stroke_inset, stroke_inset, -stroke_inset, -stroke_inset)
            p.drawRoundedRect(border_rect, 12, 12)

        finally:
            p.restore()
            try:
                from monostudio.ui_qt.stress_profiler import enabled, record_paint_ms
                if enabled():
                    record_paint_ms(float(_timer.elapsed()) if hasattr(_timer, "elapsed") else 0)
            except Exception:
                pass

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        # Responsive card size is controlled by MainView; keep uniform sizes for performance.
        return self._card_size


class _PublishDragPipelineModel(PipelineTileModel):
    """PipelineTileModel plus Published/Work middle-drag MIME (URI list)."""

    def __init__(self, parent=None):
        super().__init__(parent, thumb_state_role=PIPELINE_VIEW_THUMB_STATE_ROLE)
        self._show_publish = False
        self._active_department: str | None = None
        self._ignore_extensions: frozenset[str] = frozenset()

    def set_publish_state(self, show_publish: bool, active_department: str | None) -> None:
        self._show_publish = show_publish
        self._active_department = active_department

    def set_publish_ignore_extensions(self, ignore_extensions: frozenset[str]) -> None:
        self._ignore_extensions = ignore_extensions or frozenset()

    def flags(self, index):  # type: ignore[override]
        default = super().flags(index)
        if not index.isValid():
            return default
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return default
        if self._show_publish:
            if _item_has_publish_for_department(item.ref, self._active_department):
                return default | Qt.ItemIsDragEnabled
        else:
            if _item_has_work_for_department(item.ref, self._active_department):
                return default | Qt.ItemIsDragEnabled
        return default

    def supportedDragActions(self):  # type: ignore[override]
        return Qt.CopyAction

    def mimeTypes(self):  # type: ignore[override]
        return ["text/uri-list"]

    def mimeData(self, indexes):  # type: ignore[override]
        md = QMimeData()
        urls: list[QUrl] = []
        for idx in indexes:
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
                continue
            if self._show_publish:
                files = _resolve_all_publish_files(
                    item.ref, self._active_department, ignore_extensions=self._ignore_extensions
                )
            else:
                files = _resolve_work_files_for_drag(item.ref, self._active_department)
            for f in files:
                urls.append(QUrl.fromLocalFile(str(f)))
        if urls:
            md.setUrls(urls)
        return md


class _FilterStatusMenu(MonosMenu):
    """Multi-select status filter menu; stays open until the user clicks outside."""

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        act = self.actionAt(event.pos())
        if act is not None and act.isEnabled() and not act.isSeparator():
            if act.isCheckable():
                act.setChecked(not act.isChecked())
                act.toggled.emit(act.isChecked())
            else:
                act.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ViewOptionsSubmenuSection(QWidget):
    """Collapsible section in the view-options popup (Filter, Metadata, …)."""

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        expanded: bool = False,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_layout_changed = on_layout_changed
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._header_btn = QToolButton(self)
        self._header_btn.setText(title)
        self._header_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header_btn.setAutoRaise(True)
        self._header_btn.setCheckable(True)
        self._header_btn.setChecked(expanded)
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.setObjectName("ViewOptionsSubmenuHeader")
        self._header_btn.clicked.connect(self._on_header_clicked)
        self._body = QWidget(self)
        body_l = QVBoxLayout(self._body)
        body_l.setContentsMargins(12, 0, 0, 0)
        body_l.setSpacing(0)
        self._body.setVisible(expanded)
        outer.addWidget(self._header_btn)
        outer.addWidget(self._body)
        self._sync_chevron()

    def body_layout(self) -> QVBoxLayout:
        lay = self._body.layout()
        assert isinstance(lay, QVBoxLayout)
        return lay

    def set_expanded(self, expanded: bool) -> None:
        self._header_btn.setChecked(bool(expanded))
        self._body.setVisible(bool(expanded))
        self._sync_chevron()

    def _sync_chevron(self) -> None:
        icon = "chevron-down" if self._header_btn.isChecked() else "chevron-right"
        self._header_btn.setIcon(lucide_icon(icon, size=14, color_hex=MONOS_COLORS["text_label"]))

    def _on_header_clicked(self) -> None:
        self._body.setVisible(self._header_btn.isChecked())
        self._sync_chevron()
        if self._on_layout_changed is not None:
            self._on_layout_changed()
            QTimer.singleShot(0, self._on_layout_changed)


class MainView(QWidget):
    """
    Spec: Main View has Tile (default) and List mode; has Search + Filters.
    Phase 0: UI only (no filesystem model yet), so views start empty.
    """

    valid_selection_changed = Signal(bool)
    selection_id_changed = Signal(object)  # str | None — selection intent for AppState
    item_activated = Signal(object)  # emits ViewItem
    refresh_requested = Signal()
    root_context_menu_requested = Signal(object)  # emits global QPoint
    copy_inventory_requested = Signal(object)  # emits ViewItem (asset/shot only)
    rename_requested = Signal(object)  # emits ViewItem (asset only)
    item_notes_requested = Signal(object)  # emits ViewItem (asset / shot)
    inspector_ref_tab_requested = Signal(object)  # emits ViewItem (asset / shot)
    delete_requested = Signal(object)  # emits ViewItem (asset/shot only)
    open_requested = Signal(object)  # emits ViewItem (asset/shot only)
    open_with_requested = Signal(object)  # emits ViewItem (asset/shot only)
    create_new_requested = Signal(object)  # emits ViewItem (asset/shot only)
    switch_project_requested = Signal(object)  # emits ViewItem (project only)
    primary_action_requested = Signal()  # header primary action
    view_mode_changed = Signal(str)  # "tile" | "list"
    search_query_changed = Signal(str)  # debounced search text; empty string = clear
    show_publish_changed = Signal(bool)  # Work/Published toggle (Assets/Shots only)
    thumbnail_source_changed = Signal()  # user / render sequence / user-then-render (grid + Inspector)
    open_publish_folder_requested = Signal(object)  # emits Path (latest publish version folder)
    dcc_open_requested = Signal(object, str, str)  # (ViewItem, dcc_id, department)
    dcc_folder_requested = Signal(object, str, str)  # (ViewItem, dcc_id, department)
    dcc_copy_path_requested = Signal(object, str, str)  # (ViewItem, dcc_id, department)
    dcc_delete_requested = Signal(object, str, str)  # (ViewItem, dcc_id, department)
    dcc_open_version_requested = Signal(object, str, str, object)  # (ViewItem, dcc_id, department, file_path: Path)
    review_entity_requested = Signal(object)  # ViewItem (asset/shot) — review latest preview
    active_dcc_changed = Signal(object, str, str)  # (path, department, dcc_id) — đồng bộ Inspector
    production_status_override_chosen = Signal(object, str, object)  # (Path | list[Path], department, status_id | None)
    project_status_chosen = Signal(object, object)  # Path, status_key | None (None = automatic)
    type_badge_clicked = Signal()
    department_badge_clicked = Signal()

    _SETTINGS_KEY_VIEW_MODE_PREFIX = "main_view/mode"
    _SETTINGS_KEY_CARD_SIZE_PREFIX = "main_view/card_size"
    _SETTINGS_KEY_SHOW_PUBLISH = "main_view/show_publish"
    _SETTINGS_KEY_SHOW_DEPT_STATUS_CHIPS = "main_view/show_dept_status_chips"
    _SETTINGS_KEY_TILE_META_SHOW_ID = "main_view/tile_meta_show_id"
    _SETTINGS_KEY_TILE_META_SHOW_VERSION = "main_view/tile_meta_show_version"
    _SETTINGS_KEY_TILE_META_SHOW_LAST_UPDATED = "main_view/tile_meta_show_last_updated"
    _SETTINGS_KEY_TILE_META_SHOW_LATEST_NOTE = "main_view/tile_meta_show_latest_note"
    _SETTINGS_KEY_TILE_META_SHOW_CURRENT_DEPT = "main_view/tile_meta_show_current_department"
    _SETTINGS_KEY_TILE_META_SHOW_STATUS_PILL = "main_view/tile_meta_show_status_pill"
    _SETTINGS_KEY_HIDE_SKIPPED_CARDS = "main_view/hide_skipped_cards"
    _SETTINGS_KEY_FILTER_HAS_REFERENCE = "main_view/filter_has_reference"
    _SETTINGS_KEY_FILTER_WORK_FOLDER = "main_view/filter_work_folder"
    _SETTINGS_KEY_FILTER_STATUS_IDS = "main_view/filter_status_ids"
    _FILTER_WORK_ALL = "all"
    _FILTER_WORK_HAS = "has"
    _FILTER_WORK_NO = "no"
    _SETTINGS_KEY_SORT_FIELD = "main_view/sort_field"
    _SETTINGS_KEY_SORT_ASCENDING = "main_view/sort_ascending"
    _SORT_FIELD_NAME = "name"
    _SORT_FIELD_DATE = "date"
    _SORT_FIELD_STATUS = "status"
    _SORT_FIELD_DUE = "due"
    _THUMBNAIL_SIZE_PX = 512  # backing cache size (square); painted as 16:9 in grid
    _THUMB_STATE_ROLE = PIPELINE_VIEW_THUMB_STATE_ROLE  # per-item state in tile model ("loaded"|"missing")
    _GRID_GAP_PX = 12
    _CARD_SCALE_MIN = 0.2
    _CARD_SCALE_MAX = 1.0
    _CARD_SLIDER_RANGE = 100  # slider 0..100 → scale 0.2..1.0
    # Reference width at 100% scale; card size = ref * scale (resize only changes column count).
    _CARD_REFERENCE_WIDTH = 320

    # Thumbnail warm-up: avoid blocking one event-loop tick with hundreds of sync requests.
    _THUMB_PREFETCH_CHUNK_ROWS = 48

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._project_root: str | None = None
        self._dept_registry: DepartmentRegistry | None = None
        self._empty_override: str | None = None
        self._in_batch_set_items: bool = False  # skip stack switch to placeholder during set_items (avoids flicker)
        self._thumb_cache = ThumbnailCache(size_px=self._THUMBNAIL_SIZE_PX)
        self._thumbnail_manager: object | None = None
        self._thumb_prefetch_scheduled = False
        self._thumb_prefetch_gen = 0

        self._view_mode: str = "tile"
        self._browser_context: str = ""  # unset until set_browser_context; "project" | "asset" | "shot"
        self._card_scale_value: float = self._load_card_scale()
        # Header context (read-only)
        self._base_title: str = ""
        self._active_department: str | None = None
        self._active_department_label: str | None = None  # pipeline label (subdepartment-safe)
        self._active_department_icon_name: str | None = None  # pipeline icon (subdepartment-safe)
        self._show_publish: bool = bool(self._settings.value(self._SETTINGS_KEY_SHOW_PUBLISH, False, type=bool))
        self._inspector_hidden_departments: set[str] = set()
        self._show_dept_status_chips: bool = bool(
            self._settings.value(self._SETTINGS_KEY_SHOW_DEPT_STATUS_CHIPS, False, type=bool)
        )
        self._tile_meta_show_id: bool = bool(self._settings.value(self._SETTINGS_KEY_TILE_META_SHOW_ID, True, type=bool))
        self._tile_meta_show_version: bool = bool(
            self._settings.value(self._SETTINGS_KEY_TILE_META_SHOW_VERSION, True, type=bool)
        )
        self._tile_meta_show_last_updated: bool = bool(
            self._settings.value(self._SETTINGS_KEY_TILE_META_SHOW_LAST_UPDATED, False, type=bool)
        )
        self._tile_meta_show_latest_note: bool = bool(
            self._settings.value(self._SETTINGS_KEY_TILE_META_SHOW_LATEST_NOTE, False, type=bool)
        )
        self._tile_meta_show_current_department: bool = bool(
            self._settings.value(self._SETTINGS_KEY_TILE_META_SHOW_CURRENT_DEPT, False, type=bool)
        )
        self._tile_meta_show_status_pill: bool = bool(
            self._settings.value(self._SETTINGS_KEY_TILE_META_SHOW_STATUS_PILL, True, type=bool)
        )
        self._hide_skipped_cards: bool = bool(
            self._settings.value(self._SETTINGS_KEY_HIDE_SKIPPED_CARDS, False, type=bool)
        )
        self._filter_has_reference: bool = bool(
            self._settings.value(self._SETTINGS_KEY_FILTER_HAS_REFERENCE, False, type=bool)
        )
        _wf_raw = str(self._settings.value(self._SETTINGS_KEY_FILTER_WORK_FOLDER, self._FILTER_WORK_ALL) or "").strip().lower()
        self._filter_work_folder: str = (
            _wf_raw if _wf_raw in (self._FILTER_WORK_HAS, self._FILTER_WORK_NO) else self._FILTER_WORK_ALL
        )
        _sf_raw = str(self._settings.value(self._SETTINGS_KEY_SORT_FIELD, self._SORT_FIELD_NAME) or "").strip().lower()
        self._sort_field: str = (
            _sf_raw
            if _sf_raw in (self._SORT_FIELD_DATE, self._SORT_FIELD_STATUS, self._SORT_FIELD_DUE)
            else self._SORT_FIELD_NAME
        )
        self._sort_ascending: bool = bool(self._settings.value(self._SETTINGS_KEY_SORT_ASCENDING, True, type=bool))
        self._filter_status_ids: set[str] = self._load_filter_status_ids_from_settings()
        self._filter_status_actions: dict[str, QAction] = {}
        # Perf: avoid re-reading presets JSON on every list row / sizeHint / paint.
        self._cached_prod_reg_key: tuple[str | None, str] | None = None
        self._cached_prod_reg: object | None = None
        self._notes_badge_cache: dict[str, tuple[int, str]] = {}
        self._entity_reference_cache: dict[str, bool] = {}
        self._entity_concept_cache: dict[str, bool] = {}
        # Precomputed list Status column width (pill); avoids resizeColumnToContents × N rows.
        self._list_status_pill_layout_width: int = 0
        self._schedule_bars: BarStore = {}
        self._schedule_data: ProjectSchedule | None = None
        self._workspace_root: Path | None = None

        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        # Ensure QSS background is painted for this container.
        header.setAttribute(Qt.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(12)

        title_row = QWidget(header)
        title_row.setObjectName("MainViewTitleRow")
        title_row_l = QHBoxLayout(title_row)
        title_row_l.setContentsMargins(0, 0, 0, 0)
        title_row_l.setSpacing(8)

        self._context_badge = QWidget(title_row)
        self._context_badge.setObjectName("MainViewTypeBadge")
        self._context_badge.setAttribute(Qt.WA_StyledBackground, True)
        context_badge_l = QHBoxLayout(self._context_badge)
        context_badge_l.setContentsMargins(8, 4, 10, 4)
        context_badge_l.setSpacing(6)
        self._context_icon = QLabel(self._context_badge)
        self._context_icon.setScaledContents(False)
        self._context_icon.setFixedSize(16, 16)
        context_font = monos_font("Inter", 13, QFont.Weight.Bold)
        self._context_label = QLabel("ASSETS", self._context_badge)
        self._context_label.setObjectName("MainViewTypeBadgeLabel")
        self._context_label.setFont(context_font)
        context_badge_l.addWidget(self._context_icon, 0, Qt.AlignVCenter)
        context_badge_l.addWidget(self._context_label, 0, Qt.AlignVCenter)

        self._title_chevron = _make_main_view_title_chevron(title_row)

        self._type_badge = QWidget(title_row)
        self._type_badge.setObjectName("MainViewFilterBadge")
        self._type_badge.setProperty("filterRole", "type")
        self._type_badge.setAttribute(Qt.WA_StyledBackground, True)
        type_badge_l = QHBoxLayout(self._type_badge)
        type_badge_l.setContentsMargins(8, 4, 10, 4)
        type_badge_l.setSpacing(6)
        self._type_icon = QLabel(self._type_badge)
        self._type_icon.setScaledContents(False)
        self._type_icon.setFixedSize(16, 16)
        type_font = monos_font("Inter", 13, QFont.Weight.Bold)
        self._type_label = QLabel(self._type_badge)
        self._type_label.setObjectName("MainViewFilterBadgeLabel")
        self._type_label.setFont(type_font)
        type_badge_l.addWidget(self._type_icon, 0, Qt.AlignVCenter)
        type_badge_l.addWidget(self._type_label, 0, Qt.AlignVCenter)

        self._department_badge = QWidget(title_row)
        self._department_badge.setObjectName("MainViewFilterBadge")
        self._department_badge.setProperty("filterRole", "department")
        self._department_badge.setAttribute(Qt.WA_StyledBackground, True)
        self._department_badge.setVisible(False)
        badge_l = QHBoxLayout(self._department_badge)
        badge_l.setContentsMargins(8, 4, 10, 4)
        badge_l.setSpacing(6)
        self._department_icon = QLabel(self._department_badge)
        self._department_icon.setScaledContents(False)
        self._department_icon.setFixedSize(16, 16)
        dep_font = monos_font("Inter", 13, QFont.Weight.Bold)
        self._department_label = QLabel(self._department_badge)
        self._department_label.setObjectName("MainViewFilterBadgeLabel")
        self._department_label.setFont(dep_font)
        badge_l.addWidget(self._department_icon, 0, Qt.AlignVCenter)
        badge_l.addWidget(self._department_label, 0, Qt.AlignVCenter)
        self._title_chevron_dept = _make_main_view_title_chevron(title_row)
        title_row_l.addWidget(self._context_badge, 0, Qt.AlignVCenter)
        title_row_l.addWidget(self._title_chevron, 0, Qt.AlignVCenter)
        title_row_l.addWidget(self._type_badge, 0, Qt.AlignVCenter)
        title_row_l.addWidget(self._title_chevron_dept, 0, Qt.AlignVCenter)
        title_row_l.addWidget(self._department_badge, 0, Qt.AlignVCenter)
        self._type_badge.installEventFilter(self)
        self._department_badge.installEventFilter(self)
        self._apply_context_badge("Assets")

        # Search: icon button (right side of bar); popup with QLineEdit opens on click or Ctrl+F
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.timeout.connect(self._emit_search_query)
        self._search_debounce_ms = 180
        self._search_popup_closed_at = 0.0
        self._POPUP_REOPEN_GRACE = 0.25

        class _SearchPopupFrame(QFrame):
            def __init__(self, parent, on_hide_cb):
                super().__init__(parent)
                self._on_hide_cb = on_hide_cb

            def hideEvent(self, event):
                self._on_hide_cb()
                super().hideEvent(event)

        self._search_popup = _SearchPopupFrame(self, self._on_search_popup_hidden)
        self._search_popup.setObjectName("MainViewSearchPopup")
        self._search_popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._search_popup.setFixedSize(260, 40)
        self._search_popup.setAttribute(Qt.WA_StyledBackground, True)
        popup_layout = QHBoxLayout(self._search_popup)
        popup_layout.setContentsMargins(8, 6, 8, 6)
        popup_layout.setSpacing(6)
        self._search_input = QLineEdit(self._search_popup)
        self._search_input.setObjectName("MainViewSearchInput")
        self._search_input.setPlaceholderText("Search…")
        self._search_input.setClearButtonEnabled(False)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._btn_search_clear = QToolButton(self._search_popup)
        self._btn_search_clear.setObjectName("MainViewSearchClear")
        self._btn_search_clear.setIcon(lucide_icon("x", size=14, color_hex=MONOS_COLORS["text_label"]))
        self._btn_search_clear.setAutoRaise(True)
        self._btn_search_clear.setCursor(Qt.PointingHandCursor)
        self._btn_search_clear.setVisible(False)
        self._btn_search_clear.clicked.connect(self._clear_search)
        popup_layout.addWidget(self._search_input, 1)
        popup_layout.addWidget(self._btn_search_clear, 0, Qt.AlignVCenter)
        self._btn_search_icon = QToolButton(header)
        self._btn_search_icon.setObjectName("MainViewSearchIconButton")
        self._btn_search_icon.setToolTip("Search")
        self._btn_search_icon.setAutoRaise(True)
        self._btn_search_icon.setCursor(Qt.PointingHandCursor)
        self._btn_search_icon.setIcon(lucide_icon("search", size=16, color_hex=MONOS_COLORS["text_primary"]))
        self._btn_search_icon.clicked.connect(self._show_search_popup)
        from monostudio.ui_qt.app_hotkeys import bind_hotkey, register_hotkey_tooltip

        self._bound_hotkeys: list[QShortcut] = []
        self._bound_hotkeys.append(
            bind_hotkey(self._settings, "main_view.search", self, self._show_search_popup)
        )
        register_hotkey_tooltip(self._btn_search_icon, "Search", self._settings, "main_view.search")

        # Work/Published toggle — pill with text label, right side of header
        self._work_publish_switch = QPushButton("Work", header)
        self._work_publish_switch.setObjectName("WorkPublishPill")
        self._work_publish_switch.setCheckable(True)
        self._work_publish_switch.setChecked(self._show_publish)
        self._work_publish_switch.setCursor(Qt.PointingHandCursor)
        self._work_publish_switch.setFlat(True)
        self._work_publish_switch.toggled.connect(self._on_work_publish_toggled)
        self._work_publish_switch.setVisible(self._browser_context in ("asset", "shot"))
        register_hotkey_tooltip(
            self._work_publish_switch,
            "Toggle Work / Published",
            self._settings,
            "main_view.toggle_publish",
        )
        self._sync_work_publish_pill()

        # Center: View toggle (Grid | List) — pill UI same as Settings Tier3 (Asset Depts | Shot Depts)
        toggle = QWidget(header)
        toggle.setObjectName("Tier3Container")
        toggle.setAttribute(Qt.WA_StyledBackground, True)
        toggle.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        toggle_layout = QHBoxLayout(toggle)
        toggle_layout.setContentsMargins(6, 6, 6, 6)
        toggle_layout.setSpacing(4)

        self._btn_grid = QPushButton("Grid", toggle)
        self._btn_grid.setObjectName("Tier3Pill")
        self._btn_grid.setCheckable(True)
        self._btn_grid.setFlat(True)

        self._btn_list = QPushButton("List", toggle)
        self._btn_list.setObjectName("Tier3Pill")
        self._btn_list.setCheckable(True)
        self._btn_list.setFlat(True)

        self._view_toggle_group = QButtonGroup(self)
        self._view_toggle_group.setExclusive(True)
        self._view_toggle_group.addButton(self._btn_grid, 0)
        self._view_toggle_group.addButton(self._btn_list, 1)
        self._btn_grid.clicked.connect(lambda: self.set_view_mode("tile", save=True))
        self._btn_list.clicked.connect(lambda: self.set_view_mode("list", save=True))

        add_widgets_with_icon_separators(
            toggle_layout, [self._btn_grid, self._btn_list], toggle, sep_height=18
        )
        from monostudio.ui_qt.app_hotkeys import register_hotkey_tooltip

        register_hotkey_tooltip(
            self._btn_grid, "Grid view — Tab cycles Grid / List", self._settings, "main_view.cycle_view_mode"
        )
        register_hotkey_tooltip(
            self._btn_list, "List view — Tab cycles Grid / List", self._settings, "main_view.cycle_view_mode"
        )

        # Right: Main view options — popup for card size, thumbnail source; room for filter/sort later
        self._main_view_options_popup_closed_at = 0.0

        class _MainViewOptionsPopupFrame(QFrame):
            def __init__(self, parent, on_hide_cb):
                super().__init__(parent)
                self._on_hide_cb = on_hide_cb

            def hideEvent(self, event):
                self._on_hide_cb()
                super().hideEvent(event)

        self._main_view_options_popup = _MainViewOptionsPopupFrame(self, self._on_main_view_options_popup_hidden)
        self._main_view_options_popup.setObjectName("MainViewOptionsPopup")
        self._main_view_options_popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._main_view_options_popup.setAttribute(Qt.WA_StyledBackground, True)
        self._main_view_options_popup.setMinimumWidth(260)
        self._main_view_options_popup.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        popup_outer = QVBoxLayout(self._main_view_options_popup)
        popup_outer.setContentsMargins(8, 6, 8, 6)
        popup_outer.setSpacing(2)
        popup_outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)
        self._main_view_options_size_block = QWidget(self._main_view_options_popup)
        sz_block_l = QVBoxLayout(self._main_view_options_size_block)
        sz_block_l.setContentsMargins(0, 0, 0, 0)
        sz_block_l.setSpacing(0)
        popup_card_row = QHBoxLayout()
        popup_card_row.setSpacing(10)
        _popup_label = QLabel("Card size", self._main_view_options_size_block)
        _popup_label.setObjectName("MainViewOptionsSizeLabel")
        _popup_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._card_size_slider = QSlider(Qt.Horizontal, self._main_view_options_size_block)
        self._card_size_slider.setObjectName("MainViewOptionsSizeSlider")
        self._card_size_slider.setMinimum(0)
        self._card_size_slider.setMaximum(self._CARD_SLIDER_RANGE)
        self._card_size_slider.setSingleStep(1)
        self._card_size_slider.setPageStep(10)
        self._card_size_slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self._card_size_slider.setFixedWidth(120)
        self._card_size_slider.valueChanged.connect(self._on_card_size_slider_changed)
        popup_card_row.addWidget(_popup_label, 0, Qt.AlignVCenter)
        popup_card_row.addWidget(self._card_size_slider, 0, Qt.AlignVCenter)
        sz_block_l.addLayout(popup_card_row)
        popup_outer.addWidget(self._main_view_options_size_block)

        self._main_view_options_sep = QFrame(self._main_view_options_popup)
        self._main_view_options_sep.setFrameShape(QFrame.Shape.HLine)
        self._main_view_options_sep.setFrameShadow(QFrame.Shadow.Plain)
        self._main_view_options_sep.setFixedHeight(1)
        self._main_view_options_sep.setStyleSheet(
            f"background: {MONOS_COLORS['border']}; border: none; max-height: 1px; min-height: 1px;"
        )
        popup_outer.addWidget(self._main_view_options_sep)

        _tip_user = (
            "Only user thumbnails (pasted or .user.* files).\nWork render/preview sequences are ignored."
        )
        _tip_render = (
            "Image sequence under the active work file folder:\n"
            "work/render → preview → playblast → flipbook, then <work name>/."
        )
        _tip_smart = "Prefer the Render sequence when it exists;\notherwise use the User thumbnail."

        self._thumb_source_asset_block = QWidget(self._main_view_options_popup)
        _abl = QVBoxLayout(self._thumb_source_asset_block)
        _abl.setContentsMargins(0, 0, 0, 0)
        _abl.setSpacing(0)
        _la = QLabel("Assets", self._thumb_source_asset_block)
        _la.setObjectName("ViewOptionsSectionLabel")
        _la.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _abl.addWidget(_la)
        self._thumb_source_group_asset = QButtonGroup(self._thumb_source_asset_block)
        self._thumb_source_asset_user = QRadioButton("User", self._thumb_source_asset_block)
        self._thumb_source_asset_user.setToolTip(_tip_user)
        self._thumb_source_asset_render = QRadioButton("Render", self._thumb_source_asset_block)
        self._thumb_source_asset_render.setToolTip(_tip_render)
        self._thumb_source_asset_both = QRadioButton("Smart", self._thumb_source_asset_block)
        self._thumb_source_asset_both.setToolTip(_tip_smart)
        for rb in (self._thumb_source_asset_user, self._thumb_source_asset_render, self._thumb_source_asset_both):
            rb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._thumb_source_group_asset.setExclusive(True)
        self._thumb_source_group_asset.addButton(self._thumb_source_asset_user, 0)
        self._thumb_source_group_asset.addButton(self._thumb_source_asset_render, 1)
        self._thumb_source_group_asset.addButton(self._thumb_source_asset_both, 2)
        self._thumb_source_group_asset.idClicked.connect(self._on_main_view_thumb_source_asset_clicked)
        _ar = QVBoxLayout()
        _ar.setContentsMargins(0, 0, 0, 0)
        _ar.setSpacing(0)
        _ar.addWidget(self._thumb_source_asset_user)
        _ar.addWidget(self._thumb_source_asset_render)
        _ar.addWidget(self._thumb_source_asset_both)
        _abl.addLayout(_ar)
        popup_outer.addWidget(self._thumb_source_asset_block)

        self._thumb_source_mid_sep = QFrame(self._main_view_options_popup)
        self._thumb_source_mid_sep.setFrameShape(QFrame.Shape.HLine)
        self._thumb_source_mid_sep.setFrameShadow(QFrame.Shadow.Plain)
        self._thumb_source_mid_sep.setFixedHeight(1)
        self._thumb_source_mid_sep.setStyleSheet(
            f"background: {MONOS_COLORS['border']}; border: none; max-height: 1px; min-height: 1px;"
        )
        popup_outer.addWidget(self._thumb_source_mid_sep)

        self._thumb_source_shot_block = QWidget(self._main_view_options_popup)
        _sbl = QVBoxLayout(self._thumb_source_shot_block)
        _sbl.setContentsMargins(0, 0, 0, 0)
        _sbl.setSpacing(0)
        _ls = QLabel("Shots", self._thumb_source_shot_block)
        _ls.setObjectName("ViewOptionsSectionLabel")
        _ls.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _sbl.addWidget(_ls)
        self._thumb_source_group_shot = QButtonGroup(self._thumb_source_shot_block)
        self._thumb_source_shot_user = QRadioButton("User", self._thumb_source_shot_block)
        self._thumb_source_shot_user.setToolTip(_tip_user)
        self._thumb_source_shot_render = QRadioButton("Render", self._thumb_source_shot_block)
        self._thumb_source_shot_render.setToolTip(_tip_render)
        self._thumb_source_shot_both = QRadioButton("Smart", self._thumb_source_shot_block)
        self._thumb_source_shot_both.setToolTip(_tip_smart)
        for rb in (self._thumb_source_shot_user, self._thumb_source_shot_render, self._thumb_source_shot_both):
            rb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._thumb_source_group_shot.setExclusive(True)
        self._thumb_source_group_shot.addButton(self._thumb_source_shot_user, 0)
        self._thumb_source_group_shot.addButton(self._thumb_source_shot_render, 1)
        self._thumb_source_group_shot.addButton(self._thumb_source_shot_both, 2)
        self._thumb_source_group_shot.idClicked.connect(self._on_main_view_thumb_source_shot_clicked)
        _sr = QVBoxLayout()
        _sr.setContentsMargins(0, 0, 0, 0)
        _sr.setSpacing(0)
        _sr.addWidget(self._thumb_source_shot_user)
        _sr.addWidget(self._thumb_source_shot_render)
        _sr.addWidget(self._thumb_source_shot_both)
        _sbl.addLayout(_sr)
        popup_outer.addWidget(self._thumb_source_shot_block)

        self._dept_chips_sep = QFrame(self._main_view_options_popup)
        self._dept_chips_sep.setFrameShape(QFrame.Shape.HLine)
        self._dept_chips_sep.setFrameShadow(QFrame.Shadow.Plain)
        self._dept_chips_sep.setFixedHeight(1)
        self._dept_chips_sep.setStyleSheet(
            f"background: {MONOS_COLORS['border']}; border: none; max-height: 1px; min-height: 1px;"
        )
        popup_outer.addWidget(self._dept_chips_sep)
        self._dept_chips_block = QWidget(self._main_view_options_popup)
        _dcl = QVBoxLayout(self._dept_chips_block)
        _dcl.setContentsMargins(0, 0, 0, 0)
        _dcl.setSpacing(0)
        self._chk_dept_status_chips = QCheckBox("Department status chips (tiles)", self._dept_chips_block)
        self._chk_dept_status_chips.setToolTip(
            "When no department filter is active, show one pill per visible department under the title."
        )
        self._chk_dept_status_chips.toggled.connect(self._on_dept_status_chips_toggled)
        _dcl.addWidget(self._chk_dept_status_chips)
        popup_outer.addWidget(self._dept_chips_block)

        self._filter_sep = QFrame(self._main_view_options_popup)
        self._filter_sep.setFrameShape(QFrame.Shape.HLine)
        self._filter_sep.setFrameShadow(QFrame.Shadow.Plain)
        self._filter_sep.setFixedHeight(1)
        self._filter_sep.setStyleSheet(
            f"background: {MONOS_COLORS['border']}; border: none; max-height: 1px; min-height: 1px;"
        )
        popup_outer.addWidget(self._filter_sep)
        self._filter_submenu = _ViewOptionsSubmenuSection(
            "Filter",
            self._main_view_options_popup,
            expanded=False,
            on_layout_changed=self._sync_main_view_options_popup_geometry,
        )
        self._filter_submenu.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        _fl = self._filter_submenu.body_layout()
        self._chk_hide_skipped_cards = QCheckBox("Hide skipped", self._filter_submenu)
        self._chk_hide_skipped_cards.setToolTip(
            "Hide assets/shots whose production status pill is Skipped for the focused department "
            "(status id omitted). No effect when no department is focused."
        )
        self._chk_hide_skipped_cards.toggled.connect(self._on_hide_skipped_cards_toggled)
        self._chk_hide_skipped_cards.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _fl.addWidget(self._chk_hide_skipped_cards)
        self._chk_filter_has_reference = QCheckBox("Has reference files", self._filter_submenu)
        self._chk_filter_has_reference.setToolTip(
            "Only assets/shots with at least one file in the entity reference folder (top-level)."
        )
        self._chk_filter_has_reference.toggled.connect(self._on_filter_has_reference_toggled)
        self._chk_filter_has_reference.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        _fl.addWidget(self._chk_filter_has_reference)
        _fl.addSpacing(2)
        _wf_lbl = QLabel("Work folder", self._filter_submenu)
        _wf_lbl.setObjectName("ViewOptionsGroupLabel")
        _fl.addWidget(_wf_lbl)
        self._filter_work_group = QButtonGroup(self._filter_submenu)
        self._filter_work_all = QRadioButton("All", self._filter_submenu)
        self._filter_work_all.setToolTip("Show every item (default).")
        self._filter_work_has = QRadioButton("With work folder", self._filter_submenu)
        self._filter_work_has.setToolTip(
            "Only items whose focused department already has a work folder on disk."
        )
        self._filter_work_no = QRadioButton("Without work folder", self._filter_submenu)
        self._filter_work_no.setToolTip(
            "Only items whose focused department has no work folder yet."
        )
        for rb in (self._filter_work_all, self._filter_work_has, self._filter_work_no):
            rb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._filter_work_group.setExclusive(True)
        self._filter_work_group.addButton(self._filter_work_all, 0)
        self._filter_work_group.addButton(self._filter_work_has, 1)
        self._filter_work_group.addButton(self._filter_work_no, 2)
        self._filter_work_group.idClicked.connect(self._on_filter_work_folder_clicked)
        _fl.addWidget(self._filter_work_all)
        _fl.addWidget(self._filter_work_has)
        _fl.addWidget(self._filter_work_no)
        self._sync_filter_work_radios_from_settings()
        _fl.addSpacing(2)
        _st_lbl = QLabel("Production status", self._filter_submenu)
        _st_lbl.setObjectName("ViewOptionsGroupLabel")
        _fl.addWidget(_st_lbl)
        self._filter_status_btn = QToolButton(self._filter_submenu)
        self._filter_status_btn.setObjectName("ViewOptionsFilterStatusButton")
        self._filter_status_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._filter_status_btn.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self._filter_status_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._filter_status_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_status_btn.setToolTip(
            "Filter by production status for the focused department. Empty selection shows all."
        )
        self._filter_status_menu = _FilterStatusMenu(self._filter_status_btn)
        self._filter_status_menu.setObjectName("FilterStatusMenu")
        self._filter_status_menu.setToolTipsVisible(True)
        self._filter_status_menu.aboutToHide.connect(self._sync_main_view_options_popup_geometry)
        self._filter_status_btn.clicked.connect(self._show_filter_status_menu)
        _fl.addWidget(self._filter_status_btn)
        self._update_filter_status_button_label()
        popup_outer.addWidget(self._filter_submenu)

        self._sort_sep = QFrame(self._main_view_options_popup)
        self._sort_sep.setFrameShape(QFrame.Shape.HLine)
        self._sort_sep.setFrameShadow(QFrame.Shadow.Plain)
        self._sort_sep.setFixedHeight(1)
        self._sort_sep.setStyleSheet(
            f"background: {MONOS_COLORS['border']}; border: none; max-height: 1px; min-height: 1px;"
        )
        popup_outer.addWidget(self._sort_sep)
        self._sort_submenu = _ViewOptionsSubmenuSection(
            "Sort",
            self._main_view_options_popup,
            expanded=False,
            on_layout_changed=self._sync_main_view_options_popup_geometry,
        )
        self._sort_submenu.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        _sl = self._sort_submenu.body_layout()
        _sort_by_lbl = QLabel("Sort by", self._sort_submenu)
        _sort_by_lbl.setObjectName("ViewOptionsGroupLabel")
        _sl.addWidget(_sort_by_lbl)
        self._sort_field_group = QButtonGroup(self._sort_submenu)
        self._sort_by_name = QRadioButton("Name", self._sort_submenu)
        self._sort_by_name.setToolTip("Asset type + name (assets) or shot name.")
        self._sort_by_date = QRadioButton("Date", self._sort_submenu)
        self._sort_by_date.setToolTip(
            "Last updated: entity folder in Work mode, latest publish version in Published mode."
        )
        self._sort_by_status = QRadioButton("Status", self._sort_submenu)
        self._sort_by_status.setToolTip(
            "Production status for the focused department (pipeline category order)."
        )
        self._sort_by_due = QRadioButton("Due", self._sort_submenu)
        self._sort_by_due.setToolTip("Schedule due date (active department, else delivery).")
        for rb in (self._sort_by_name, self._sort_by_date, self._sort_by_status, self._sort_by_due):
            rb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._sort_field_group.setExclusive(True)
        self._sort_field_group.addButton(self._sort_by_name, 0)
        self._sort_field_group.addButton(self._sort_by_date, 1)
        self._sort_field_group.addButton(self._sort_by_status, 2)
        self._sort_field_group.addButton(self._sort_by_due, 3)
        self._sort_field_group.idClicked.connect(self._on_sort_field_clicked)
        _sl.addWidget(self._sort_by_name)
        _sl.addWidget(self._sort_by_date)
        _sl.addWidget(self._sort_by_status)
        _sl.addWidget(self._sort_by_due)
        _sl.addSpacing(2)
        _sort_order_lbl = QLabel("Order", self._sort_submenu)
        _sort_order_lbl.setObjectName("ViewOptionsGroupLabel")
        _sl.addWidget(_sort_order_lbl)
        self._sort_order_group = QButtonGroup(self._sort_submenu)
        self._sort_ascending_rb = QRadioButton("Ascending", self._sort_submenu)
        self._sort_ascending_rb.setToolTip("A→Z, oldest first, lowest status rank first.")
        self._sort_descending_rb = QRadioButton("Descending", self._sort_submenu)
        self._sort_descending_rb.setToolTip("Z→A, newest first, highest status rank first.")
        for rb in (self._sort_ascending_rb, self._sort_descending_rb):
            rb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._sort_order_group.setExclusive(True)
        self._sort_order_group.addButton(self._sort_ascending_rb, 0)
        self._sort_order_group.addButton(self._sort_descending_rb, 1)
        self._sort_order_group.idClicked.connect(self._on_sort_order_clicked)
        _sl.addWidget(self._sort_ascending_rb)
        _sl.addWidget(self._sort_descending_rb)
        self._sync_sort_radios_from_settings()
        popup_outer.addWidget(self._sort_submenu)

        self._metadata_sep = QFrame(self._main_view_options_popup)
        self._metadata_sep.setFrameShape(QFrame.Shape.HLine)
        self._metadata_sep.setFrameShadow(QFrame.Shadow.Plain)
        self._metadata_sep.setFixedHeight(1)
        self._metadata_sep.setStyleSheet(
            f"background: {MONOS_COLORS['border']}; border: none; max-height: 1px; min-height: 1px;"
        )
        popup_outer.addWidget(self._metadata_sep)
        self._metadata_submenu = _ViewOptionsSubmenuSection(
            "Metadata",
            self._main_view_options_popup,
            expanded=False,
            on_layout_changed=self._sync_main_view_options_popup_geometry,
        )
        self._metadata_submenu.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        _ml = self._metadata_submenu.body_layout()
        self._chk_tile_meta_id = QCheckBox("ID", self._metadata_submenu)
        self._chk_tile_meta_id.setToolTip("Show folder / entity ID under the title.")
        self._chk_tile_meta_id.toggled.connect(self._on_tile_meta_id_toggled)
        self._chk_tile_meta_version = QCheckBox("Version", self._metadata_submenu)
        self._chk_tile_meta_version.setToolTip("Work or published version for the focused department.")
        self._chk_tile_meta_version.toggled.connect(self._on_tile_meta_version_toggled)
        self._chk_tile_meta_last_updated = QCheckBox("Last updated", self._metadata_submenu)
        self._chk_tile_meta_last_updated.setToolTip(
            "Work mode: asset/shot folder mtime. Published mode: latest publish version folder (and contents)."
        )
        self._chk_tile_meta_last_updated.toggled.connect(self._on_tile_meta_last_updated_toggled)
        self._chk_tile_meta_latest_note = QCheckBox("Latest note", self._metadata_submenu)
        self._chk_tile_meta_latest_note.setToolTip(
            "Show the most recent open note under the title on grid cards (completed notes are skipped)."
        )
        self._chk_tile_meta_latest_note.toggled.connect(self._on_tile_meta_latest_note_toggled)
        self._chk_tile_meta_current_dept = QCheckBox("Current department", self._metadata_submenu)
        self._chk_tile_meta_current_dept.setToolTip("Text line for the sidebar department filter (when set).")
        self._chk_tile_meta_current_dept.toggled.connect(self._on_tile_meta_current_dept_toggled)
        self._chk_tile_meta_status_pill = QCheckBox("Production status pill", self._metadata_submenu)
        self._chk_tile_meta_status_pill.setToolTip(
            "Pill under meta lines for the focused department (click to change status)."
        )
        self._chk_tile_meta_status_pill.toggled.connect(self._on_tile_meta_status_pill_toggled)
        for w in (
            self._chk_tile_meta_id,
            self._chk_tile_meta_version,
            self._chk_tile_meta_last_updated,
            self._chk_tile_meta_latest_note,
            self._chk_tile_meta_current_dept,
            self._chk_tile_meta_status_pill,
        ):
            w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            _ml.addWidget(w)
        popup_outer.addWidget(self._metadata_submenu)

        self._btn_main_view_options = QToolButton(header)
        self._btn_main_view_options.setObjectName("MainViewOptionsButton")
        self._btn_main_view_options.setAccessibleName("View options")
        self._btn_main_view_options.setToolTip(
            "View options — card size, thumbnail source for Assets/Shots (filter & sort later)"
        )
        self._btn_main_view_options.setAutoRaise(True)
        self._btn_main_view_options.setCursor(Qt.PointingHandCursor)
        self._btn_main_view_options.setIcon(lucide_icon("sliders-horizontal", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_main_view_options.clicked.connect(self._show_main_view_options_popup)
        self._update_main_view_options_button()

        # (Primary action button removed — replaced by Work/Published pill)

        # Tile view (IconMode) skeleton
        self._tile_model = _PublishDragPipelineModel(self)
        self._tile_model.set_publish_state(self._show_publish, self._active_department)
        self._tile_model.set_publish_ignore_extensions(get_publish_ignore_extensions(self._settings))
        self._tile_view = _ClearOnEmptyClickListView()
        self._tile_view.setObjectName("MainViewGrid")
        self._tile_view.setViewMode(QListView.IconMode)
        self._tile_view.setResizeMode(QListView.Adjust)
        self._tile_view.setUniformItemSizes(True)
        # Use explicit gap in grid sizing (prevents "stuck together" rendering).
        self._tile_view.setSpacing(0)
        # Scrollbar only when content overflows (auto-hide when list not clipped).
        self._tile_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._tile_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tile_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tile_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tile_view.setAcceptDrops(False)
        self._tile_view.setDropIndicatorShown(False)
        _try_set_minimal_viewport_update(self._tile_view)
        self._sync_tile_drag_mode()
        self._tile_view.setIconSize(QSize(self._THUMBNAIL_SIZE_PX, self._THUMBNAIL_SIZE_PX))
        self._tile_view.setModel(self._tile_model)
        self._tile_view.doubleClicked.connect(self._on_tile_activated)
        self._tile_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tile_view.customContextMenuRequested.connect(self._on_tile_context_menu)
        self._tile_view.verticalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)
        self._tile_view.horizontalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)
        self._tile_view.setMouseTracking(True)
        self._tile_view.viewport().installEventFilter(self)
        # Left/top padding = 24px (right/bottom provided by per-cell gap).
        self._tile_view.setViewportMargins(24, 24, 0, 0)

        self._grid_delegate = _GridCardDelegate(view=self._tile_view, main_view=self)
        self._grid_delegate.set_gap_px(self._GRID_GAP_PX)
        self._grid_delegate.set_show_publish(self._show_publish)
        self._grid_delegate.set_show_dept_chips(self._show_dept_status_chips)
        self._apply_tile_meta_to_delegate()
        self._tile_view.setItemDelegate(self._grid_delegate)
        self._tile_view.entered.connect(self._on_tile_entered)
        self._tile_view.viewportEntered.connect(self._on_tile_viewport_left)
        self._grid_sync_scheduled = False
        self._grid_last: tuple[int, int, int] | None = None  # (cols, card_w, card_h)
        self._schedule_grid_layout_sync()

        self._tile_placeholder = MainViewLoadingPlaceholder()
        self._tile_placeholder.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tile_placeholder.customContextMenuRequested.connect(self._on_tile_placeholder_context_menu)

        tile_page = QStackedWidget()
        tile_page.addWidget(self._tile_placeholder)
        tile_page.addWidget(self._tile_view)
        tile_page.setCurrentIndex(0)
        self._tile_page = tile_page

        # List view skeleton (headers set from _list_headers() per context)
        self._list_model = PipelineTableModel(self, self._tile_model)

        self._list_view = _ClearOnEmptyClickTableView()
        self._list_view.setObjectName("MainViewList")
        self._list_view.setModel(self._list_model)
        self._list_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list_view.setDragEnabled(False)
        self._list_view.setAcceptDrops(False)
        self._list_view.setDropIndicatorShown(False)
        self._list_view.setDragDropMode(QAbstractItemView.NoDragDrop)
        _try_set_minimal_viewport_update(self._list_view)
        self._list_view.horizontalHeader().setStretchLastSection(True)
        self._list_view.setSortingEnabled(False)
        self._list_view.verticalHeader().setVisible(False)
        self._list_view.verticalHeader().setDefaultSectionSize(56)
        self._list_view.setShowGrid(False)
        self._list_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list_view.doubleClicked.connect(self._on_list_activated)
        self._list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_view.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list_row_delegate = _ListRowDelegate(view=self._list_view, main_view=self)
        self._list_view.setItemDelegate(self._list_row_delegate)
        self._list_view.setMouseTracking(True)
        self._list_view.viewport().installEventFilter(self)
        self._list_view.verticalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)

        self._list_placeholder = MainViewLoadingPlaceholder()
        self._list_placeholder.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_placeholder.customContextMenuRequested.connect(self._on_list_placeholder_context_menu)

        list_page = QStackedWidget()
        list_page.addWidget(self._list_placeholder)
        list_page.addWidget(self._list_view)
        list_page.setCurrentIndex(0)
        self._list_page = list_page

        self._content = QStackedWidget()
        self._content.addWidget(tile_page)  # index 0 = tile
        self._content.addWidget(list_page)  # index 1 = list

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header, 0)
        layout.addWidget(self._content, 1)

        header_layout.addWidget(title_row, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)
        _header_align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        header_layout.addWidget(toggle, 0, _header_align)
        header_layout.addWidget(vertical_icon_separator(header, height=20), 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self._btn_main_view_options, 0, _header_align)
        header_layout.addWidget(vertical_icon_separator(header, height=20), 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self._btn_search_icon, 0, _header_align)
        header_layout.addWidget(vertical_icon_separator(header, height=20), 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self._work_publish_switch, 0, _header_align)

        self.set_selected_asset_type(None)
        self._work_publish_switch.setVisible(self._browser_context in ("asset", "shot"))

        self._selection_notify_pending = False
        self._tile_view._on_rubber_band_finished = self._flush_deferred_selection_notify
        self._list_view._on_rubber_band_finished = self._flush_deferred_selection_notify
        self._tile_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)
        self._list_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)

        self._tile_model.rowsInserted.connect(self._update_empty_states)
        self._tile_model.rowsRemoved.connect(self._update_empty_states)
        self._tile_model.modelReset.connect(self._update_empty_states)
        self._list_model.rowsInserted.connect(self._update_empty_states)
        self._list_model.rowsRemoved.connect(self._update_empty_states)
        self._list_model.modelReset.connect(self._update_empty_states)

        # View mode becomes context-aware; MainWindow sets initial browser context.
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())

        self._all_items: list[ViewItem] = []
        # Full list from last set_items / diffs; visible rows may be a subset when "Hide skipped" is on.
        self._items_unfiltered: list[ViewItem] = []
        # AssetGrid: asset_id -> row index for O(1) lookup; _order = display order of asset_ids.
        self._items: dict[str, int] = {}
        self._order: list[str] = []
        self._selection_driven_by_state = False
        self._bind_view_mode_shortcuts()

    def bound_hotkeys(self) -> list[QShortcut]:
        return getattr(self, "_bound_hotkeys", [])

    def reload_hotkeys(self) -> None:
        from monostudio.ui_qt.app_hotkeys import reload_bound_shortcuts

        reload_bound_shortcuts(self._settings, self.bound_hotkeys())

    def _toggle_publish_mode_shortcut(self) -> None:
        from monostudio.ui_qt.nav_quick_view import keyboard_input_blocks_shortcuts

        if keyboard_input_blocks_shortcuts():
            return
        if self._browser_context not in ("asset", "shot"):
            return
        if getattr(self, "_work_publish_switch", None) is None:
            return
        self._work_publish_switch.setChecked(not self._show_publish)

    def _bind_view_mode_shortcuts(self) -> None:
        from monostudio.ui_qt.app_hotkeys import bind_hotkey

        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        if not hasattr(self, "_bound_hotkeys"):
            self._bound_hotkeys = []
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "main_view.cycle_view_mode",
                self,
                self._cycle_view_mode,
                context=ctx,
                auto_repeat=False,
            )
        )
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "main_view.toggle_publish",
                self,
                self._toggle_publish_mode_shortcut,
                context=ctx,
                auto_repeat=False,
            )
        )

    def _cycle_view_mode(self) -> None:
        fw = QApplication.focusWidget()
        if fw is not None and fw is self._search_input:
            return
        if self._search_popup.isVisible() and fw is not None and self._search_popup.isAncestorOf(fw):
            return
        next_mode = "list" if self._view_mode == "tile" else "tile"
        self.set_view_mode(next_mode, save=True)

    def _on_search_text_changed(self, _text: str) -> None:
        self._btn_search_clear.setVisible(bool((self._search_input.text() or "").strip()))
        self._search_debounce_timer.stop()
        self._search_debounce_timer.start(self._search_debounce_ms)

    def _emit_search_query(self) -> None:
        text = (self._search_input.text() or "").strip()
        self.search_query_changed.emit(text)

    def _clear_search(self) -> None:
        self._search_debounce_timer.stop()
        self._search_input.clear()
        self._btn_search_clear.setVisible(False)
        self.search_query_changed.emit("")

    def _show_search_popup(self) -> None:
        """Show search popup below the search icon; focus line edit. Same as noti: toggle if open, grace to avoid reopen."""
        if self._search_popup.isVisible():
            self._search_popup.close()
            return
        if (time.monotonic() - self._search_popup_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        position_popup_near_anchor(self._search_popup, self._btn_search_icon, gap=2)
        self._search_popup.show()
        self._search_input.setFocus(Qt.FocusReason.PopupFocusReason)

    def _on_search_popup_hidden(self) -> None:
        self._search_popup_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._btn_search_icon))

    def _clear_tool_button_hover(self, btn: QToolButton) -> None:
        """Clear stuck hover/pressed state after popup closes (same as TopBar/noti)."""
        QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
        btn.setDown(False)
        try:
            st = btn.style()
            if st:
                st.unpolish(btn)
                st.polish(btn)
        except Exception:
            pass
        btn.update()

    def set_search_placeholder(self, placeholder: str) -> None:
        """Set placeholder text for the search input (e.g. context-aware: Search assets, Search shots)."""
        self._search_input.setPlaceholderText(placeholder or "Search…")

    def set_search_query(self, query: str) -> None:
        """Set search input text without emitting (e.g. when clearing on context switch)."""
        self._search_input.blockSignals(True)
        try:
            self._search_input.setText(query or "")
        finally:
            self._search_input.blockSignals(False)
        self._btn_search_clear.setVisible(bool((query or "").strip()))

    def _list_dcc_hit(self, pos: QPoint) -> tuple[ViewItem | None, str | None, str | None]:
        """Hit-test DCC badges in list view at viewport pos. Returns (item, dcc_id, department) or (None, None, None)."""
        if self._view_mode != "list" or self._show_publish:
            return None, None, None
        index = self._list_view.indexAt(pos)
        if not index.isValid():
            return None, None, None
        col = index.column()
        if col != self._list_col_dcc():
            return None, None, None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return None, None, None
        cell_rect = self._list_view.visualRect(index)
        if not cell_rect.contains(pos):
            return None, None, None
        dcc_list = _dcc_ids_for_item(
            item,
            (self._active_department or "").strip() or None,
            dept_registry=getattr(self, "_dept_registry", None),
        )
        if not dcc_list:
            return None, None, None
        rects = _list_dcc_badge_rects(cell_rect, dcc_list)
        for r, dcc_id in rects:
            if r.contains(pos):
                return item, dcc_id, (self._active_department or "").strip() or ""
        return None, None, None

    def _dcc_badge_hit(self, pos) -> tuple[ViewItem | None, str | None, str | None]:
        """Hit-test DCC badges at viewport pos (grid). Returns (item, dcc_id, department) or (None, None, None)."""
        if self._view_mode != "tile" or self._show_publish:
            return None, None, None
        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            return None, None, None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return None, None, None
        active_dep = (getattr(self._grid_delegate, "_active_department", None) or "").strip()
        if not active_dep:
            return None, None, None
        cell_rect = self._tile_view.visualRect(index)
        dcc_ids = _dcc_ids_for_item(
            item, active_dep, dept_registry=getattr(self, "_dept_registry", None)
        )
        if not dcc_ids:
            return None, None, None
        rects = _dcc_badge_rects(cell_rect, self._GRID_GAP_PX, dcc_ids)
        for rect, dcc_id in rects:
            if rect.contains(pos):
                return item, dcc_id, active_dep
        return None, None, None

    def _grid_status_pill_hit(self, pos: QPoint) -> tuple[ViewItem | None, str | None]:
        if self._view_mode != "tile" or not self._project_root:
            return None, None
        if self._browser_context not in ("asset", "shot"):
            return None, None
        if not self._tile_meta_show_status_pill:
            return None, None
        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            return None, None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return None, None
        sm = self._tile_view.selectionModel()
        selected = sm is not None and sm.isSelected(index)
        cell_rect = self._tile_view.visualRect(index)
        dep = _grid_status_pill_department_at(
            cell_rect,
            self._GRID_GAP_PX,
            pos,
            selected=selected,
            item=item,
            active_department=self._active_department,
            project_root=self._project_root,
            hidden_departments=set(self._inspector_hidden_departments),
            n_meta_lines=self._tile_meta_line_count(),
        )
        if dep and item.path:
            return item, dep
        return None, None

    def _grid_project_status_pill_hit(self, pos: QPoint) -> ViewItem | None:
        if self._view_mode != "tile" or self._browser_context != "project":
            return None
        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or item.kind != ViewItemKind.PROJECT:
            return None
        stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
        status = getattr(stats, "status", None) or "WAITING"
        line = project_status_label(status)
        sm = self._tile_view.selectionModel()
        selected = sm is not None and sm.isSelected(index)
        cell_rect = self._tile_view.visualRect(index)
        hit = _grid_project_status_pill_rect(
            cell_rect,
            self._GRID_GAP_PX,
            pos,
            selected=selected,
            line=line,
        )
        return item if hit is not None else None

    def _list_status_hit(self, pos: QPoint) -> ViewItem | None:
        if self._view_mode != "list" or not self._project_root:
            return None
        if self._browser_context not in ("asset", "shot"):
            return None
        index = self._list_view.indexAt(pos)
        if not index.isValid() or index.column() != self._list_col_status():
            return None
        item = index.data(Qt.UserRole)
        if isinstance(item, ViewItem) and isinstance(item.ref, (Asset, Shot)):
            return item
        return None

    def _list_project_status_hit(self, pos: QPoint) -> ViewItem | None:
        if self._view_mode != "list" or self._browser_context != "project":
            return None
        index = self._list_view.indexAt(pos)
        if not index.isValid() or index.column() != self._list_col_status():
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or item.kind != ViewItemKind.PROJECT:
            return None
        stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
        status = getattr(stats, "status", None) or "WAITING"
        line = project_status_label(status)
        chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
        fm = QFontMetrics(chip_font)
        pill_rect = _list_status_pill_rect_for_cell(self._list_view.visualRect(index), line, fm)
        return item if pill_rect.contains(pos) else None

    def _selected_asset_shot_paths_for_batch(self) -> list[Path]:
        """Non-dimmed Asset/Shot paths from current multi-selection (grid or list)."""
        out: list[Path] = []
        seen: set[str] = set()

        def add_item_path(item: object) -> None:
            if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
                return
            if self._is_item_dimmed(item):
                return
            raw = item.path
            if not raw:
                return
            p = Path(raw)
            key = str(p)
            if key in seen:
                return
            seen.add(key)
            out.append(p)

        if self._view_mode == "list":
            sm = self._list_view.selectionModel()
            if sm is None:
                return out
            for idx in sm.selectedRows():
                add_item_path(idx.data(Qt.UserRole))
        else:
            sm = self._tile_view.selectionModel()
            if sm is None:
                return out
            for idx in sm.selectedIndexes():
                add_item_path(idx.data(Qt.UserRole))
        return out

    def _production_status_target_paths(self, primary: Path) -> list[Path]:
        """Apply override to all selected items when the clicked card is part of a multi-selection."""
        selected = self._selected_asset_shot_paths_for_batch()
        if not primary:
            return selected

        def key(p: Path) -> str:
            try:
                return str(p.resolve())
            except OSError:
                return str(p)

        pk = key(primary)
        sk = {key(p) for p in selected}
        if pk not in sk:
            return [primary]
        if len(selected) > 1:
            return selected
        return [primary]

    def _run_production_status_menu_for(self, entity_path: Path, department: str, global_pos: QPoint) -> None:
        pr = Path(self._project_root) if self._project_root else None
        res = pick_production_status_at(self, pr, global_pos, department_id=department)
        if res is False:
            return
        targets = self._production_status_target_paths(entity_path)
        self.production_status_override_chosen.emit(targets, department, res)

    def _run_project_status_menu_for(self, project_path: Path, global_pos: QPoint) -> None:
        from monostudio.ui_qt.project_status_menu import pick_project_status_at

        stats = self._workspace_project_stats_for_path(project_path)
        current = getattr(stats, "status", None) or "WAITING" if stats else "WAITING"
        res = pick_project_status_at(
            self,
            global_pos,
            project_root=project_path,
            current_status=current,
        )
        if res is False:
            return
        self.project_status_chosen.emit(project_path, res)

    def _workspace_project_stats_for_path(self, project_path: Path) -> ProjectQuickStats | None:
        for row in range(self._tile_model.rowCount()):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if (
                isinstance(item, ViewItem)
                and item.kind == ViewItemKind.PROJECT
                and item.path
                and self._paths_equal(item.path, project_path)
            ):
                ref = item.ref
                return ref if isinstance(ref, ProjectQuickStats) else None
        return None

    def _grid_health_hit_row(self, pos) -> int | None:
        """Row index if pos is over the thumb health icon; else None."""
        if self._view_mode != "tile":
            return None
        dep = (self._active_department or "").strip()
        if not dep:
            return None
        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        cell_rect = self._tile_view.visualRect(index)
        if not _thumb_health_chip_rect(cell_rect, self._GRID_GAP_PX).contains(pos):
            return None
        return index.row()

    def _open_inspector_ref_tab_for_item(self, item: ViewItem, *, pos: QPoint | None = None) -> None:
        view = self._tile_view if self._view_mode == "tile" else getattr(self, "_list_view", None)
        if view is not None and pos is not None:
            idx = view.indexAt(pos)
            if idx.isValid():
                sm = view.selectionModel()
                if sm is not None:
                    sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.inspector_ref_tab_requested.emit(item)

    def _grid_note_hit_row(self, pos) -> int | None:
        """Row index if pos is over the thumb notes chip; else None."""
        if self._view_mode != "tile" or self._browser_context not in ("asset", "shot"):
            return None
        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        if not getattr(item, "path", None):
            return None
        cell_rect = self._tile_view.visualRect(index)
        thumb = _thumb_rect_from_cell(cell_rect, self._GRID_GAP_PX)
        dep = (self._active_department or "").strip()
        health_rect: QRect | None = None
        if dep:
            active_dcc = self.get_active_dcc(item.path, dep)
            if (
                assess_view_item_health(
                    item.ref,
                    dep,
                    active_dcc_id=active_dcc,
                )
                is not None
            ):
                health_rect = _thumb_health_chip_rect(cell_rect, self._GRID_GAP_PX)
        note_rect = _thumb_note_chip_rect(thumb, health_rect)
        return index.row() if note_rect.contains(pos) else None

    def _show_item_health_dialog(self, item: ViewItem, department: str) -> None:
        from monostudio.ui_qt.item_health_dialog import ItemHealthDialog

        active_dcc = self.get_active_dcc(getattr(item, "path", None), department)
        health = assess_view_item_health(
            item.ref,
            department,
            active_dcc_id=active_dcc,
        )
        if health is None:
            return
        dept_obj = _department_for_item(item.ref, department)
        naming_prefix = (
            work_file_prefix(name=item.ref.name, department=dept_obj.name) if dept_obj else ""
        )
        dlg = ItemHealthDialog(
            parent=self,
            item_name=display_name_for_item(item),
            department=department,
            health=health,
            naming_prefix=naming_prefix or None,
            on_repaired=lambda: self.refresh_requested.emit(),
            health_refresh=(item.ref, department, active_dcc),
        )
        dlg.exec()

    def _on_tile_entered(self, index) -> None:
        if self._tile_view.rubber_band_selecting():
            return
        if QApplication.mouseButtons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton):
            return
        self._grid_delegate.set_hovered_index(index)

    def _on_tile_viewport_left(self) -> None:
        if self._tile_view.rubber_band_selecting():
            return
        self._grid_delegate.set_hovered_index(None)

    def _update_grid_thumb_interactive_hover(self, pos) -> None:
        """Track hover for production status pill, health icon, and notes chip (grid)."""
        if self._view_mode != "tile":
            return
        pill_row: int | None = None
        if self._browser_context == "project":
            if self._grid_project_status_pill_hit(pos) is not None:
                idx = self._tile_view.indexAt(pos)
                if idx.isValid():
                    pill_row = idx.row()
        else:
            hit_item, hit_dep = self._grid_status_pill_hit(pos)
            if hit_item and hit_dep:
                idx = self._tile_view.indexAt(pos)
                if idx.isValid():
                    pill_row = idx.row()
        self._grid_delegate.set_hovered_pill_row(pill_row)
        health_row = self._grid_health_hit_row(pos)
        self._grid_delegate.set_hovered_health_row(health_row)
        notes_row = self._grid_note_hit_row(pos)
        self._grid_delegate.set_hovered_notes_row(notes_row)
        vp = self._tile_view.viewport()
        if health_row is not None or notes_row is not None:
            vp.setCursor(Qt.CursorShape.PointingHandCursor)
        elif pill_row is not None:
            vp.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            vp.unsetCursor()

    def _list_status_pill_hit_row(self, pos: QPoint) -> int | None:
        """Row index if pos is over list production status pill; else None."""
        if self._view_mode != "list":
            return None
        if self._browser_context == "project":
            hit = self._list_project_status_hit(pos)
            if hit is None or not hit.path:
                return None
            index = self._list_view.indexAt(pos)
            return index.row() if index.isValid() else None
        if not self._project_root:
            return None
        if self._browser_context not in ("asset", "shot"):
            return None
        dep = (self._active_department or "").strip()
        if not dep:
            return None
        index = self._list_view.indexAt(pos)
        if not index.isValid() or index.column() != self._list_col_status():
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        try:
            reg = self._production_status_registry_cached()
            sid = aggregate_status_id_for_item(
                item.ref,
                active_department=dep,
                hidden_departments=self._inspector_hidden_departments,
                registry=reg,
            )
            line = reg.label_for(sid)
        except Exception:
            line = "Waiting"
        chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
        fm = QFontMetrics(chip_font)
        cell_rect = self._list_view.visualRect(index)
        pill_rect = _list_status_pill_rect_for_cell(cell_rect, line, fm)
        return index.row() if pill_rect.contains(pos) else None

    def _list_health_hit_row(self, pos: QPoint) -> int | None:
        if self._view_mode != "list" or self._browser_context not in ("asset", "shot"):
            return None
        dep = (self._active_department or "").strip()
        if not dep:
            return None
        hc = self._list_col_health()
        if hc < 0:
            return None
        index = self._list_view.indexAt(pos)
        if not index.isValid() or index.column() != hc:
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        health = assess_view_item_health(
            item.ref,
            dep,
            active_dcc_id=_item_active_dcc(item.path, dep) if item.path else None,
        )
        if health is None:
            return None
        cell_rect = self._list_view.visualRect(index)
        return index.row() if _list_health_chip_rect(cell_rect).contains(pos) else None

    def _list_thumb_note_hit_row(self, pos: QPoint) -> int | None:
        if self._view_mode != "list" or self._browser_context not in ("asset", "shot"):
            return None
        nc = self._list_col_notes()
        if nc < 0:
            return None
        index = self._list_view.indexAt(pos)
        if not index.isValid() or index.column() != nc:
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)) or not item.path:
            return None
        cell_rect = self._list_view.visualRect(index)
        note_rect = _list_health_chip_rect(cell_rect)
        return index.row() if note_rect.contains(pos) else None

    def _list_thumb_note_hit(self, pos: QPoint) -> ViewItem | None:
        row = self._list_thumb_note_hit_row(pos)
        if row is None:
            return None
        nc = self._list_col_notes()
        if nc < 0:
            return None
        index = self._list_view.model().index(row, nc)
        item = index.data(Qt.UserRole)
        return item if isinstance(item, ViewItem) else None

    def _list_health_hit(self, pos: QPoint) -> ViewItem | None:
        row = self._list_health_hit_row(pos)
        if row is None:
            return None
        index = self._list_view.model().index(row, self._list_col_health())
        item = index.data(Qt.UserRole)
        return item if isinstance(item, ViewItem) else None

    def _list_special_folder_hit(
        self, pos: QPoint, column: int, folder_id: str
    ) -> ViewItem | None:
        if self._view_mode != "list" or self._browser_context not in ("asset", "shot"):
            return None
        if column < 0:
            return None
        index = self._list_view.indexAt(pos)
        if not index.isValid() or index.column() != column:
            return None
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or not isinstance(item.ref, (Asset, Shot)):
            return None
        cell_rect = self._list_view.visualRect(index)
        if not _list_special_folder_chip_rect(cell_rect).contains(pos):
            return None
        return item

    def _list_ref_hit(self, pos: QPoint) -> ViewItem | None:
        return self._list_special_folder_hit(pos, self._list_col_ref(), "reference")

    def _list_concept_hit(self, pos: QPoint) -> ViewItem | None:
        return self._list_special_folder_hit(pos, self._list_col_concept(), "concept")

    def _list_ref_hit_row(self, pos: QPoint) -> int | None:
        item = self._list_ref_hit(pos)
        if item is None:
            return None
        index = self._list_view.indexAt(pos)
        return index.row() if index.isValid() else None

    def _list_concept_hit_row(self, pos: QPoint) -> int | None:
        item = self._list_concept_hit(pos)
        if item is None:
            return None
        index = self._list_view.indexAt(pos)
        return index.row() if index.isValid() else None

    def _update_list_status_pill_hover(self, pos: QPoint) -> None:
        if self._view_mode != "list":
            return
        self._list_row_delegate.set_hovered_status_row(self._list_status_pill_hit_row(pos))

    def _update_list_interactive_hover(self, pos: QPoint) -> None:
        """Hover for list status pill, health icon column, and notes column."""
        if self._view_mode != "list":
            return
        self._update_list_status_pill_hover(pos)
        self._list_row_delegate.set_hovered_health_row(self._list_health_hit_row(pos))
        self._list_row_delegate.set_hovered_notes_row(self._list_thumb_note_hit_row(pos))
        self._list_row_delegate.set_hovered_ref_row(self._list_ref_hit_row(pos))
        self._list_row_delegate.set_hovered_concept_row(self._list_concept_hit_row(pos))
        vp = self._list_view.viewport()
        if (
            self._list_health_hit_row(pos) is not None
            or self._list_thumb_note_hit_row(pos) is not None
            or self._list_status_pill_hit_row(pos) is not None
            or self._list_ref_hit_row(pos) is not None
            or self._list_concept_hit_row(pos) is not None
        ):
            vp.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            vp.unsetCursor()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._grid_last = None
        self._schedule_grid_layout_sync()
        if self._browser_context == "project":
            self._refresh_list_status_column()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _complete_active_view_mouse_gesture(self) -> None:
        """Finish rubber-band state when viewport filter swallowed the release event."""
        view = self._tile_view if self._view_mode == "tile" else self._list_view
        finish = getattr(view, "_rb_on_left_release", None)
        if callable(finish):
            finish()
        other = self._list_view if view is self._tile_view else self._tile_view
        other_cleanup = getattr(other, "_rb_force_cleanup", None)
        if callable(other_cleanup):
            other_cleanup()

    def _release_grid_mouse_grabs(self) -> None:
        for view in (self._tile_view, self._list_view):
            cleanup = getattr(view, "_rb_force_cleanup", None)
            if callable(cleanup):
                cleanup()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched in (self._tile_view.viewport(), self._list_view.viewport()):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton:
                    self._release_grid_mouse_grabs()
        if (
            watched in (self._type_badge, self._department_badge)
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            nav = watched.property("navLink")
            if nav == "true" or nav is True:
                if watched is self._type_badge:
                    self.type_badge_clicked.emit()
                else:
                    self.department_badge_clicked.emit()
                QTimer.singleShot(0, lambda w=watched: clear_stuck_widget_hover(w))
                event.accept()
                return True

        if watched is self._tile_view.viewport() and event.type() == QEvent.Resize:
            self._schedule_grid_layout_sync()

        if watched is self._tile_view.viewport() and self._view_mode == "tile":
            et = event.type()
            if et == QEvent.MouseMove:
                if not (event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton)):
                    self._update_grid_thumb_interactive_hover(event.pos())
            elif et == QEvent.Leave:
                self._grid_delegate.set_hovered_pill_row(None)
                self._grid_delegate.set_hovered_health_row(None)
                self._grid_delegate.set_hovered_notes_row(None)
                self._tile_view.viewport().unsetCursor()

        # Swallow mouse release on grid status pill / health icon so the view does not replace multi-selection.
        if (
            watched is self._tile_view.viewport()
            and self._view_mode == "tile"
            and event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            hit_sv_item, hit_sv_dep = self._grid_status_pill_hit(event.pos())
            if hit_sv_item and hit_sv_dep:
                self._complete_active_view_mouse_gesture()
                event.accept()
                return True
            if self._browser_context == "project" and self._grid_project_status_pill_hit(event.pos()) is not None:
                self._complete_active_view_mouse_gesture()
                event.accept()
                return True
            if self._grid_health_hit_row(event.pos()) is not None:
                self._complete_active_view_mouse_gesture()
                event.accept()
                return True
            if self._grid_note_hit_row(event.pos()) is not None:
                self._complete_active_view_mouse_gesture()
                event.accept()
                return True

        # Left-click on production status pill: pick override (before DCC badge handling)
        if (
            watched is self._tile_view.viewport()
            and event.type() == QEvent.MouseButtonPress
            and self._view_mode == "tile"
        ):
            if event.button() == Qt.MouseButton.LeftButton:
                idx = self._tile_view.indexAt(event.pos())
                hit_proj = self._grid_project_status_pill_hit(event.pos())
                if hit_proj and hit_proj.path:
                    if idx.isValid():
                        sm = self._tile_view.selectionModel()
                        if sm is not None:
                            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                    gp = self._tile_view.viewport().mapToGlobal(event.pos())
                    self._run_project_status_menu_for(Path(hit_proj.path), gp)
                    event.accept()
                    return True
                hit_sv_item, hit_sv_dep = self._grid_status_pill_hit(event.pos())
                if hit_sv_item and hit_sv_dep and hit_sv_item.path:
                    if idx.isValid():
                        sm = self._tile_view.selectionModel()
                        if sm is not None:
                            sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                    gp = self._tile_view.viewport().mapToGlobal(event.pos())
                    self._run_production_status_menu_for(Path(hit_sv_item.path), hit_sv_dep, gp)
                    event.accept()
                    return True
                health_row = self._grid_health_hit_row(event.pos())
                if health_row is not None:
                    idx = self._tile_view.indexAt(event.pos())
                    item = idx.data(Qt.UserRole) if idx.isValid() else None
                    dep = (self._active_department or "").strip()
                    if isinstance(item, ViewItem) and dep:
                        if idx.isValid():
                            sm = self._tile_view.selectionModel()
                            if sm is not None:
                                sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                        self._show_item_health_dialog(item, dep)
                        event.accept()
                        return True
                if self._grid_note_hit_row(event.pos()) is not None:
                    idx = self._tile_view.indexAt(event.pos())
                    n_item = idx.data(Qt.UserRole) if idx.isValid() else None
                    if isinstance(n_item, ViewItem) and getattr(n_item, "path", None):
                        if idx.isValid():
                            sm = self._tile_view.selectionModel()
                            if sm is not None:
                                sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                        self.item_notes_requested.emit(n_item)
                        event.accept()
                        return True
                hit_item, hit_dcc, hit_dep = self._dcc_badge_hit(event.pos())
                if hit_item and hit_dcc and hit_dep:
                    self._grid_delegate.set_active_dcc(hit_item.path, hit_dep, hit_dcc)
                    self.active_dcc_changed.emit(hit_item.path, hit_dep, hit_dcc)
                    try:
                        self.invalidate_thumbnail(hit_item.path, hit_dep)
                    except Exception:
                        pass
                    event.accept()
                    return True

        if (
            watched is self._tile_view.viewport()
            and event.type() == QEvent.ToolTip
            and self._view_mode == "tile"
        ):
            pos = event.pos()
            index = self._tile_view.indexAt(pos)
            if index.isValid():
                item = index.data(Qt.UserRole)
                if isinstance(item, ViewItem):
                    cell_rect = self._tile_view.visualRect(index)
                    if cell_rect.contains(pos):
                        active_dep = (getattr(self._grid_delegate, "_active_department", None) or "").strip()
                        has_dept = bool(active_dep)
                        type_rect, dept_rect = _thumb_badge_rects(cell_rect, self._GRID_GAP_PX, has_dept)
                        if type_rect.contains(pos):
                            tt = _TYPE_TOOLTIP_MAP.get((item.type_badge or "").strip().lower()) or _TYPE_TOOLTIP_MAP.get((item.type_badge or "").strip()) or (item.type_badge or "Type")
                            QToolTip.showText(event.globalPos(), tt)
                            event.accept()
                            return True
                        if has_dept and dept_rect and dept_rect.contains(pos):
                            QToolTip.showText(event.globalPos(), active_dep)
                            event.accept()
                            return True
                        # Notes chip (asset/shot)
                        if isinstance(item.ref, (Asset, Shot)) and getattr(item, "path", None):
                            thumb_tt = _thumb_rect_from_cell(cell_rect, self._GRID_GAP_PX)
                            active_dep_tt = (getattr(self._grid_delegate, "_active_department", None) or "").strip()
                            hr_note: QRect | None = None
                            if active_dep_tt:
                                active_dcc_n = self.get_active_dcc(getattr(item, "path", None), active_dep_tt)
                                if (
                                    assess_view_item_health(
                                        item.ref,
                                        active_dep_tt,
                                        active_dcc_id=active_dcc_n,
                                    )
                                    is not None
                                ):
                                    hr_note = _thumb_health_chip_rect(cell_rect, self._GRID_GAP_PX)
                            note_r = _thumb_note_chip_rect(thumb_tt, hr_note)
                            if note_r.contains(pos):
                                n_open, nmode = self.notes_badge_state(item.path)
                                QToolTip.showText(
                                    event.globalPos(),
                                    _notes_badge_tooltip_text(n_open, nmode),
                                )
                                event.accept()
                                return True
                        # Health icon tooltip (only when department is focused)
                        health_rect = _thumb_health_chip_rect(cell_rect, self._GRID_GAP_PX)
                        active_dep_tt = (getattr(self._grid_delegate, "_active_department", None) or "").strip()
                        if (
                            health_rect.contains(pos)
                            and active_dep_tt
                            and isinstance(item.ref, (Asset, Shot))
                        ):
                            active_dcc_tt = self.get_active_dcc(getattr(item, "path", None), active_dep_tt)
                            health = assess_view_item_health(
                                item.ref,
                                active_dep_tt,
                                active_dcc_id=active_dcc_tt,
                            )
                            if health is not None:
                                QToolTip.showText(
                                    event.globalPos(),
                                    _item_health_tooltip_text(health),
                                )
                                event.accept()
                                return True
                        # DCC badge tooltip: DCC name — Department
                        hit_item, hit_dcc, hit_dep = self._dcc_badge_hit(pos)
                        if hit_item and hit_dcc:
                            try:
                                reg = get_default_dcc_registry()
                                info = reg.get_dcc_info(hit_dcc)
                                dcc_name = info.get("label", hit_dcc) if isinstance(info, dict) else hit_dcc
                            except Exception:
                                dcc_name = hit_dcc
                            dept_display = (hit_dep or "").replace("_", " ").strip().title() or "—"
                            tooltip_text = f"{dcc_name} — {dept_display}"
                            QToolTip.showText(event.globalPos(), tooltip_text)
                            event.accept()
                            return True
                        # Khi không chọn department (hoặc hover vùng không phải badge): vẫn hiện tooltip với tên item
                        fallback_tt = display_name_for_item(item) or (getattr(item, "name", None) or "").strip() or "—"
                        type_label = _TYPE_TOOLTIP_MAP.get((item.type_badge or "").strip().lower()) or _TYPE_TOOLTIP_MAP.get((item.type_badge or "").strip()) or (item.type_badge or "")
                        if type_label:
                            fallback_tt = f"{fallback_tt} — {type_label}"
                        hint_html = '<span style="font-size:80%; color:#71717a;">Double-click to open</span>'
                        QToolTip.showText(event.globalPos(), f"<html>{fallback_tt}<br/><br/>{hint_html}</html>")
                        event.accept()
                        return True

        # List view: DCC column click -> set active DCC (only if _list_view exists, e.g. after __init__)
        list_view = getattr(self, "_list_view", None)
        if list_view is not None and watched is list_view.viewport():
            if self._view_mode == "list":
                let = event.type()
                if let == QEvent.MouseMove:
                    if not (event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton)):
                        self._update_list_interactive_hover(event.pos())
                elif let == QEvent.Leave:
                    self._list_row_delegate.set_hovered_status_row(None)
                    self._list_row_delegate.set_hovered_health_row(None)
                    self._list_row_delegate.set_hovered_notes_row(None)
                    self._list_row_delegate.set_hovered_ref_row(None)
                    self._list_row_delegate.set_hovered_concept_row(None)
                    list_view.viewport().unsetCursor()
            if event.type() == QEvent.MouseButtonRelease and self._view_mode == "list":
                if event.button() == Qt.MouseButton.LeftButton:
                    if self._browser_context == "project" and self._list_project_status_hit(event.pos()) is not None:
                        self._complete_active_view_mouse_gesture()
                        event.accept()
                        return True
                    dep = (self._active_department or "").strip()
                    if dep and self._list_status_hit(event.pos()):
                        self._complete_active_view_mouse_gesture()
                        event.accept()
                        return True
                    if self._list_health_hit_row(event.pos()) is not None:
                        self._complete_active_view_mouse_gesture()
                        event.accept()
                        return True
                    if self._list_thumb_note_hit_row(event.pos()) is not None:
                        self._complete_active_view_mouse_gesture()
                        event.accept()
                        return True
                    if self._list_ref_hit_row(event.pos()) is not None:
                        self._complete_active_view_mouse_gesture()
                        event.accept()
                        return True
                    if self._list_concept_hit_row(event.pos()) is not None:
                        self._complete_active_view_mouse_gesture()
                        event.accept()
                        return True
            if event.type() == QEvent.MouseButtonPress and self._view_mode == "list":
                if event.button() == Qt.MouseButton.LeftButton:
                    hit_ref = self._list_ref_hit(event.pos())
                    if hit_ref:
                        self._open_inspector_ref_tab_for_item(hit_ref, pos=event.pos())
                        event.accept()
                        return True
                    hit_concept = self._list_concept_hit(event.pos())
                    if hit_concept:
                        self._open_entity_special_folder_from_item(hit_concept, "concept")
                        event.accept()
                        return True
                    hit_note = self._list_thumb_note_hit(event.pos())
                    if hit_note and hit_note.path:
                        idx = list_view.indexAt(event.pos())
                        if idx.isValid():
                            sm = list_view.selectionModel()
                            if sm is not None:
                                sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                        self.item_notes_requested.emit(hit_note)
                        event.accept()
                        return True
                    hit_health = self._list_health_hit(event.pos())
                    if hit_health:
                        idx = list_view.indexAt(event.pos())
                        if idx.isValid():
                            sm = list_view.selectionModel()
                            if sm is not None:
                                sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                        dep_h = (self._active_department or "").strip()
                        if dep_h:
                            self._show_item_health_dialog(hit_health, dep_h)
                        event.accept()
                        return True
                    hit_item, hit_dcc, hit_dep = self._list_dcc_hit(event.pos())
                    if hit_item and hit_dcc and hit_dep:
                        self._grid_delegate.set_active_dcc(hit_item.path, hit_dep, hit_dcc)
                        self.active_dcc_changed.emit(hit_item.path, hit_dep, hit_dcc)
                        try:
                            self.invalidate_thumbnail(hit_item.path, hit_dep)
                        except Exception:
                            pass
                        list_view.viewport().update()
                        event.accept()
                        return True
                    hit_proj = self._list_project_status_hit(event.pos())
                    if hit_proj and hit_proj.path:
                        idx = list_view.indexAt(event.pos())
                        if idx.isValid():
                            sm = list_view.selectionModel()
                            if sm is not None:
                                sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                        gpos = list_view.viewport().mapToGlobal(event.pos())
                        self._run_project_status_menu_for(Path(hit_proj.path), gpos)
                        event.accept()
                        return True
                    hit_st = self._list_status_hit(event.pos())
                    if hit_st and hit_st.path:
                        dep = (self._active_department or "").strip()
                        if not dep:
                            QMessageBox.information(
                                self,
                                "Production status",
                                "Select a department in the sidebar to set status for this item.",
                            )
                            event.accept()
                            return True
                        idx = list_view.indexAt(event.pos())
                        if idx.isValid():
                            sm = list_view.selectionModel()
                            if sm is not None:
                                sm.setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
                        gpos = list_view.viewport().mapToGlobal(event.pos())
                        self._run_production_status_menu_for(Path(hit_st.path), dep, gpos)
                        event.accept()
                        return True
            if event.type() == QEvent.ToolTip and self._view_mode == "list":
                if self._list_thumb_note_hit_row(event.pos()) is not None:
                    n_item = self._list_thumb_note_hit(event.pos())
                    if n_item is not None and n_item.path:
                        n_open, nmode = self.notes_badge_state(n_item.path)
                        QToolTip.showText(
                            event.globalPos(),
                            _notes_badge_tooltip_text(n_open, nmode),
                        )
                        event.accept()
                        return True
                hc = self._list_col_health()
                if hc >= 0:
                    index = list_view.indexAt(event.pos())
                    if index.isValid() and index.column() == hc:
                        item = index.data(Qt.UserRole)
                        dep_tt = (self._active_department or "").strip()
                        if (
                            dep_tt
                            and isinstance(item, ViewItem)
                            and isinstance(item.ref, (Asset, Shot))
                        ):
                            health = assess_view_item_health(
                                item.ref,
                                dep_tt,
                                active_dcc_id=_item_active_dcc(item.path, dep_tt)
                                if item.path
                                else None,
                            )
                            if health is not None:
                                QToolTip.showText(
                                    event.globalPos(),
                                    _item_health_tooltip_text(health),
                                )
                                event.accept()
                                return True
                ref_col = self._list_col_ref()
                if ref_col >= 0:
                    index = list_view.indexAt(event.pos())
                    if index.isValid() and index.column() == ref_col:
                        item = index.data(Qt.UserRole)
                        if isinstance(item, ViewItem) and isinstance(item.ref, (Asset, Shot)):
                            has = self.entity_has_reference_files_cached(item)
                            label = display_name_for_item(item) or item.name
                            tip = (
                                f"Open Reference in Inspector — {label}"
                                if has
                                else f"Reference folder (empty) — {label}"
                            )
                            QToolTip.showText(event.globalPos(), tip)
                            event.accept()
                            return True
                concept_col = self._list_col_concept()
                if concept_col >= 0:
                    index = list_view.indexAt(event.pos())
                    if index.isValid() and index.column() == concept_col:
                        item = index.data(Qt.UserRole)
                        if isinstance(item, ViewItem) and isinstance(item.ref, (Asset, Shot)):
                            has = self.entity_has_concept_files_cached(item)
                            label = display_name_for_item(item) or item.name
                            tip = (
                                f"Open concept folder — {label}"
                                if has
                                else f"Concept folder (empty) — {label}"
                            )
                            QToolTip.showText(event.globalPos(), tip)
                            event.accept()
                            return True
                hit_item, hit_dcc, hit_dep = self._list_dcc_hit(event.pos())
                if hit_item and hit_dcc:
                    try:
                        reg = get_default_dcc_registry()
                        info = reg.get_dcc_info(hit_dcc)
                        dcc_name = info.get("label", hit_dcc) if isinstance(info, dict) else hit_dcc
                    except Exception:
                        dcc_name = hit_dcc
                    dept_display = (hit_dep or "").replace("_", " ").strip().title() or "—"
                    QToolTip.showText(event.globalPos(), f"{dcc_name} — {dept_display}")
                    event.accept()
                    return True

        return super().eventFilter(watched, event)

    def _schedule_grid_layout_sync(self) -> None:
        if getattr(self, "_grid_sync_scheduled", False):
            return
        self._grid_sync_scheduled = True
        QTimer.singleShot(0, self._sync_grid_layout)

    def _tile_meta_line_count(self) -> int:
        if self._browser_context not in ("asset", "shot"):
            return 1
        return tile_grid_meta_line_count(
            show_id=self._tile_meta_show_id,
            show_version=self._tile_meta_show_version,
            show_last_updated=self._tile_meta_show_last_updated,
            show_latest_note=self._tile_meta_show_latest_note,
            show_current_department=self._tile_meta_show_current_department,
            active_department=self._active_department,
        )

    def _apply_tile_meta_to_delegate(self) -> None:
        d = getattr(self, "_grid_delegate", None)
        if d is None:
            return
        d.set_tile_meta_display(
            show_id=self._tile_meta_show_id,
            show_version=self._tile_meta_show_version,
            show_last_updated=self._tile_meta_show_last_updated,
            show_latest_note=self._tile_meta_show_latest_note,
            show_current_department=self._tile_meta_show_current_department,
            show_status_pill=self._tile_meta_show_status_pill,
        )

    def _sync_grid_layout(self) -> None:
        """
        Grid: card size from scale only (no auto-scale on resize).
        - card_w = reference_width * scale (fixed by slider)
        - cols = how many cards fit in viewport (resize only changes column count)
        """
        self._grid_sync_scheduled = False
        try:
            vw = int(self._tile_view.viewport().width())
        except Exception:
            return
        if vw <= 0:
            retries = int(getattr(self, "_grid_sync_layout_retries", 0) or 0)
            if retries < 8:
                self._grid_sync_layout_retries = retries + 1
                QTimer.singleShot(0, self._schedule_grid_layout_sync)
            return
        self._grid_sync_layout_retries = 0

        gap = self._GRID_GAP_PX
        inner_w = max(1, vw - 24)

        # Card size from scale only (0.2..1.0), not from viewport width.
        card_w = max(80, int(self._CARD_REFERENCE_WIDTH * self._card_scale()))
        thumb_h = int(card_w * 9 / 16)
        show_asset_status_pill = (
            self._tile_meta_show_status_pill
            and self._browser_context in ("asset", "shot")
            and self._view_mode == "tile"
            and bool((self._active_department or "").strip())
            and bool((self._project_root or "").strip())
        )
        show_project_status_pill = self._browser_context == "project" and self._view_mode == "tile"
        pill_fm = (
            QFontMetrics(monos_font("Inter", 10, QFont.Weight.DemiBold))
            if show_asset_status_pill or show_project_status_pill
            else None
        )
        if self._browser_context == "project":
            meta_h = grid_card_meta_block_height_project(pill_font_metrics=pill_fm)
        else:
            meta_h = grid_card_meta_block_height_asset_shot(
                n_meta_lines=self._tile_meta_line_count(),
                show_department_status_pill=show_asset_status_pill,
                pill_font_metrics=pill_fm,
            )
        card_h = thumb_h + meta_h

        # How many cards fit in one row (resize changes this, not card size).
        cell_w = card_w + gap
        cols = max(1, (inner_w + gap) // cell_w)

        sig = (cols, card_w, card_h)
        if getattr(self, "_grid_last", None) == sig:
            return
        self._grid_last = sig

        self._tile_view.setGridSize(QSize(cell_w, card_h + gap))
        self._grid_delegate.set_card_size(QSize(card_w, card_h))
        # Thumbnail prefetch uses visualRect; it often runs in the same singleShot(0) tick before IconMode
        # lays out wrapped cells, so only the first row intersects the viewport. Prefetch again after grid size.
        self._schedule_thumbnail_prefetch(force=True)

    def set_context_title(self, title: str) -> None:
        self.update_title(base_title=title, department=self._active_department)

    def set_active_department(
        self,
        department: str | None,
        *,
        label: str | None = None,
        icon_name: str | None = None,
        defer_list_rebuild: bool = False,
    ) -> None:
        prev_dept = self._active_department
        self._active_department = (department or "").strip() or None
        self._active_department_label = (label or "").strip() or None
        self._active_department_icon_name = (icon_name or "").strip() or None
        self.update_title(base_title=self._base_title or "Assets", department=self._active_department)
        self._tile_model.set_publish_state(self._show_publish, self._active_department)
        self._tile_model.set_publish_ignore_extensions(get_publish_ignore_extensions(self._settings))
        try:
            self._grid_delegate.set_active_department(
                self._active_department,
                icon_name=self._active_department_icon_name,
                label=self._active_department_label,
            )
        except Exception:
            pass
        if prev_dept != self._active_department:
            self._grid_delegate.set_hovered_pill_row(None)
            self._grid_delegate.set_hovered_health_row(None)
            self._grid_delegate.set_hovered_notes_row(None)
            self._list_row_delegate.set_hovered_status_row(None)
            self._list_row_delegate.set_hovered_health_row(None)
            self._list_row_delegate.set_hovered_notes_row(None)
            self._grid_last = None
            self._schedule_grid_layout_sync()
            if defer_list_rebuild:
                return
            self._refresh_thumbnails_for_department_change()
            self._refresh_list_status_column()
            self._refresh_list_last_updated_column()
            self._refresh_list_due_column()
            self._refresh_list_assignee_column()
            self._tile_view.viewport().update()
            self._list_view.viewport().update()
            if self._items_unfiltered:
                self._resort_main_view_visible()
        else:
            self._schedule_thumbnail_prefetch()

    def get_active_dcc(self, item_path: Path | None, department: str | None) -> str | None:
        """Forward to grid delegate (cache + persistence)."""
        if getattr(self, "_grid_delegate", None) is None:
            return None
        return self._grid_delegate.get_active_dcc(item_path, department)

    def set_active_dcc(self, item_path: Path, department: str, dcc_id: str) -> None:
        """Forward to grid delegate; repaint tile view."""
        if getattr(self, "_grid_delegate", None) is None:
            return
        self._grid_delegate.set_active_dcc(item_path, department, dcc_id)
        self._tile_view.viewport().update()
        # Thumbnail cache is keyed by active DCC; refresh this row so render-sequence / smart thumb updates.
        try:
            self.invalidate_thumbnail(item_path, (department or "").strip() or None)
        except Exception:
            pass
        self._refresh_list_last_updated_column()
        self._list_view.viewport().update()

    def invalidate_entity_reference_cache(self, entity_path: Path | str | None = None) -> None:
        if entity_path is None:
            self._entity_reference_cache.clear()
            self._entity_concept_cache.clear()
        else:
            try:
                key = str(Path(entity_path).resolve())
            except (TypeError, ValueError, OSError):
                key = str(entity_path)
            self._entity_reference_cache.pop(key, None)
            self._entity_concept_cache.pop(key, None)
        self.refresh_reference_hint_badges()

    def entity_has_reference_files_cached(self, item: ViewItem) -> bool:
        if not isinstance(item.ref, (Asset, Shot)):
            return False
        try:
            key = str(Path(item.path).resolve())
        except (TypeError, ValueError, OSError):
            key = str(item.path)
        hit = self._entity_reference_cache.get(key)
        if hit is not None:
            return bool(hit)
        pr: Path | None = None
        if self._project_root is not None:
            try:
                pr = Path(self._project_root).resolve()
            except OSError:
                pr = Path(self._project_root)
        has = entity_has_reference_files(pr, item.ref, dept_registry=self._dept_registry)
        self._entity_reference_cache[key] = has
        return has

    def entity_has_concept_files_cached(self, item: ViewItem) -> bool:
        if not isinstance(item.ref, (Asset, Shot)):
            return False
        try:
            key = str(Path(item.path).resolve())
        except (TypeError, ValueError, OSError):
            key = str(item.path)
        hit = self._entity_concept_cache.get(key)
        if hit is not None:
            return bool(hit)
        pr: Path | None = None
        if self._project_root is not None:
            try:
                pr = Path(self._project_root).resolve()
            except OSError:
                pr = Path(self._project_root)
        has = entity_has_concept_files(pr, item.ref, dept_registry=self._dept_registry)
        self._entity_concept_cache[key] = has
        return has

    def refresh_reference_hint_badges(self) -> None:
        self._tile_view.viewport().update()
        if self._browser_context in ("asset", "shot"):
            for col in (self._list_col_ref(), self._list_col_concept()):
                if col >= 0:
                    self._list_model.refresh_column_for_all_rows(col, [])

    def _view_item_has_reference_files(self, item: ViewItem) -> bool:
        return self.entity_has_reference_files_cached(item)

    def notes_badge_state(
        self,
        item_path: Path | str | None,
        department_id: str | None = None,
    ) -> tuple[int, str]:
        """(open_count, visual_mode) for the active sidebar department."""
        if item_path is None:
            return (0, "empty")
        try:
            p = Path(item_path)
            path_key = str(p.resolve())
        except (TypeError, OSError, ValueError):
            return (0, "empty")
        dept = (
            department_id
            if department_id is not None
            else self._active_department
        )
        from monostudio.core.item_comments import normalize_note_department_id

        dept_key = normalize_note_department_id(dept)
        key = f"{path_key}|{dept_key}"
        hit = self._notes_badge_cache.get(key)
        if hit is not None:
            return hit
        try:
            from monostudio.core.item_comments import notes_badge_visual_mode

            n, mode = notes_badge_visual_mode(p, dept or None)
        except Exception:
            n, mode = 0, "empty"
        self._notes_badge_cache[key] = (int(n), str(mode))
        return self._notes_badge_cache[key]

    def notes_open_count(self, item_path: Path | str | None) -> int:
        return self.notes_badge_state(item_path)[0]

    def _drop_notes_badge_cache_for_path(self, path_key: str) -> None:
        """Remove cached note badge counts for one entity (all department keys)."""
        prefix = f"{path_key}|"
        stale = [k for k in self._notes_badge_cache if k == path_key or k.startswith(prefix)]
        for k in stale:
            self._notes_badge_cache.pop(k, None)

    def invalidate_notes_open_count_cache(self, path: Path | str | None = None) -> None:
        if path is None:
            self._notes_badge_cache.clear()
            self._entity_reference_cache.clear()
            self._entity_concept_cache.clear()
        else:
            try:
                path_key = str(Path(path).resolve())
            except (OSError, TypeError, ValueError):
                return
            self._drop_notes_badge_cache_for_path(path_key)
            row = self._row_for_item_id(path_key)
            if row is not None and row >= 0:
                if row < self._tile_model.rowCount():
                    ix = self._tile_model.index(row, 0)
                    self._tile_model.dataChanged.emit(ix, ix, [])
                notes_col = self._list_col_notes()
                if (
                    notes_col >= 0
                    and row < self._list_model.rowCount()
                    and self._browser_context in ("asset", "shot")
                ):
                    nix = self._list_model.index(row, notes_col)
                    self._list_model.dataChanged.emit(nix, nix, [])
                if row < self._list_model.rowCount():
                    self._list_model.notify_thumb_column(row)
        self._tile_view.viewport().update()
        lv = getattr(self, "_list_view", None)
        if lv is not None:
            lv.viewport().update()

    def update_title(self, *, base_title: str, department: str | None) -> None:
        """
        Title formatting:
        - Base title always shown (uppercased, bold)
        - If department active: show badge with icon + department name (bold, BG + border)
        """
        base = _normalize_browser_context_title(base_title)
        self._base_title = base
        self._apply_context_badge(base)
        dep = (department or "").strip()
        if not dep:
            self._department_badge.setVisible(False)
        else:
            dep_label = (self._active_department_label or "").strip() or dep
            dep_up = dep_label.upper()
            icon_name = (self._active_department_icon_name or "").strip() or "layers"
            icon = lucide_icon(
                icon_name,
                size=16,
                color_hex=CARD_THUMB_DEPT_BADGE_ICON_COLOR,
            )
            self._department_icon.setPixmap(icon.pixmap(16, 16))
            self._department_label.setText(dep_up)
            self._department_badge.setVisible(True)
            self._apply_filter_badge_style(
                self._department_badge, self._department_label, role="department"
            )
        self._sync_header_breadcrumbs()

    def _apply_context_badge(self, title: str = "") -> None:
        """Root breadcrumb chip: Assets / Shots / Projects with nav-rail icon."""
        base = _normalize_browser_context_title(title or self._base_title or "Assets")
        label = base.upper() if base else ""
        ctx = self._browser_context if self._browser_context in ("asset", "shot", "project") else ""
        if not ctx:
            key = base.casefold()
            if key in ("assets", "asset"):
                ctx = "asset"
            elif key in ("shots", "shot"):
                ctx = "shot"
            else:
                ctx = "project"
        kind = ctx if ctx in ("asset", "shot") else ""
        self._context_badge.setProperty("badgeKind", kind)
        icon_name = _browser_context_badge_icon(ctx)
        icon_color = page_badge_accent_color(kind)
        icon = lucide_icon(icon_name, size=16, color_hex=icon_color)
        if not icon.isNull():
            self._context_icon.setPixmap(icon.pixmap(16, 16))
        self._context_label.setText(label)
        for w in (self._context_badge, self._context_label):
            w.style().unpolish(w)
            w.style().polish(w)
        self._context_badge.update()

    def _sync_header_breadcrumbs(self) -> None:
        show_type = self._type_badge.isVisible()
        show_dept = self._department_badge.isVisible()
        self._title_chevron.setVisible(show_type)
        # Shots have no type badge — chevron sits between title and department instead.
        show_dept_chevron = show_dept and (show_type or self._browser_context == "shot")
        self._title_chevron_dept.setVisible(show_dept_chevron)
        self._sync_filter_badge_clickability()

    def _sync_filter_badge_clickability(self) -> None:
        self._set_filter_badge_clickable(
            self._type_badge,
            self._type_badge.isVisible(),
            tooltip="Change type filter",
        )
        self._set_filter_badge_clickable(
            self._department_badge,
            self._department_badge.isVisible(),
            tooltip="Change department filter",
        )

    @staticmethod
    def _set_filter_badge_clickable(
        widget: QWidget, enabled: bool, *, tooltip: str = ""
    ) -> None:
        widget.setProperty("navLink", "true" if enabled else "false")
        widget.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        widget.setToolTip(tooltip if enabled else "")
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def type_badge_widget(self) -> QWidget:
        return self._type_badge

    def department_badge_widget(self) -> QWidget:
        return self._department_badge

    @staticmethod
    def _apply_filter_badge_style(
        badge: QWidget, label: QLabel, *, role: str = ""
    ) -> None:
        badge.setObjectName("MainViewFilterBadge")
        label.setObjectName("MainViewFilterBadgeLabel")
        badge.setProperty("badgeKind", "")
        badge.setProperty("filterRole", (role or "").strip())
        for w in (badge, label):
            w.setStyleSheet("")
            w.style().unpolish(w)
            w.style().polish(w)
        badge.update()
        label.update()

    def set_selected_asset_type(
        self,
        type_id: str | None,
        *,
        label: str | None = None,
        icon_name: str | None = None,
    ) -> None:
        """
        Update type badge from asset type selected in sidebar (Character, Prop, Environment, …).
        When type_id is None (or not in Assets), badge is hidden.
        """
        if not (type_id and (type_id := type_id.strip())):
            self._type_badge.setVisible(False)
            self._sync_header_breadcrumbs()
            return
        display_label = (label or "").strip() or _TYPE_TOOLTIP_MAP.get(type_id, type_id)
        icon = lucide_icon(
            (icon_name or "").strip() or _TYPE_ICON_MAP.get(type_id, _TYPE_ICON_MAP.get(f"_{type_id}", "box")),
            size=16,
            color_hex=CARD_THUMB_TYPE_BADGE_ICON_COLOR,
        )
        self._type_icon.setPixmap(icon.pixmap(16, 16))
        self._type_label.setText(display_label.upper())
        self._apply_filter_badge_style(self._type_badge, self._type_label, role="type")
        self._type_badge.setVisible(True)
        self._sync_header_breadcrumbs()

    def set_primary_action(self, *, label: str, enabled: bool, tooltip: str | None) -> None:
        pass

    def _sync_work_publish_pill(self) -> None:
        on = self._show_publish
        label = "Published" if on else "Work"
        self._work_publish_switch.setText(label)
        _pill_base = (
            "border: none; border-radius: 12px; padding: 4px 14px; "
            "font-family: 'Inter'; font-size: 11px; font-weight: 700; "
            "min-height: 22px; min-width: 72px; max-width: 72px; "
        )
        if on:
            self._work_publish_switch.setStyleSheet(
                f"QPushButton#WorkPublishPill {{ "
                f"background: {MONOS_COLORS['blue_600']}; color: #fafafa; "
                f"{_pill_base} font-style: italic; }}"
                f"QPushButton#WorkPublishPill:hover {{ background: {MONOS_COLORS['blue_500']}; }}"
            )
        else:
            self._work_publish_switch.setStyleSheet(
                f"QPushButton#WorkPublishPill {{ "
                f"background: #2a2a2c; color: {MONOS_COLORS['text_meta']}; "
                f"{_pill_base} }}"
                f"QPushButton#WorkPublishPill:hover {{ background: #3f3f46; color: #fafafa; }}"
            )

    def _sync_tile_drag_mode(self) -> None:
        # Both modes: drag enabled; model flags control which rows are draggable (has publish / has work file).
        if self._browser_context in ("asset", "shot"):
            self._tile_view.setDragEnabled(True)
            self._tile_view.setDragDropMode(QAbstractItemView.DragOnly)
            self._tile_view.setDefaultDropAction(Qt.CopyAction)
        else:
            self._tile_view.setDragEnabled(False)
            self._tile_view.setDragDropMode(QAbstractItemView.NoDragDrop)

    def _on_work_publish_toggled(self, checked: bool) -> None:
        self._show_publish = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_SHOW_PUBLISH, self._show_publish)
        self._sync_work_publish_pill()
        self._grid_delegate.set_show_publish(self._show_publish)
        self._tile_model.set_publish_state(self._show_publish, self._active_department)
        self._tile_model.set_publish_ignore_extensions(get_publish_ignore_extensions(self._settings))
        self._sync_tile_drag_mode()
        self._refresh_list_last_updated_column()
        self._tile_view.viewport().update()
        if self._sort_field == self._SORT_FIELD_DATE and self._items_unfiltered:
            self._resort_main_view_visible()
        self.show_publish_changed.emit(self._show_publish)

    def get_show_publish(self) -> bool:
        return self._show_publish

    def set_browser_context(self, context: str) -> None:
        """
        Asset Browser contexts:
        - "project" -> default Grid
        - "asset"   -> default Grid
        - "shot"    -> default List
        Persist view mode per-context when user toggles it.
        """
        if context not in ("project", "asset", "shot"):
            return
        prev = self._browser_context
        self._browser_context = context
        self._card_scale_value = self._load_card_scale()
        self._update_main_view_options_button()
        if getattr(self, "_work_publish_switch", None) is not None:
            self._work_publish_switch.setVisible(context in ("asset", "shot"))

        title = "Projects" if context == "project" else ("Shots" if context == "shot" else "Assets")
        self.set_context_title(title)
        if self._type_badge.isVisible():
            self._apply_filter_badge_style(self._type_badge, self._type_label, role="type")
        self._sync_header_breadcrumbs()

        if context == prev:
            return

        key = self._settings_key_view_mode()
        saved = self._settings.value(key, "", str)
        if saved in ("tile", "list"):
            self.set_view_mode(saved, save=False)
        else:
            default_mode = "list" if context == "shot" else "tile"
            self.set_view_mode(default_mode, save=False)
        # Table column count is 8 (project) vs 10 (asset/shot). Assets↔Shots stays 10 — skip table resetModel.
        prev_cols = 8 if prev == "project" else 10
        new_cols = 8 if context == "project" else 10
        if prev_cols != new_cols:
            self._list_model.reset_structure()
        self._schedule_grid_layout_sync()

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager

    def _production_status_registry_cached(self):
        """Status registry for pills/filters: dept-specific when a department is focused."""
        root = self._project_root
        dep = (self._active_department or "").strip()
        cache_key = (root, dep)
        if getattr(self, "_cached_prod_reg_key", None) != cache_key:
            self._cached_prod_reg_key = cache_key
            self._cached_prod_reg = _status_registry_for_view(root, dep or None)
        return self._cached_prod_reg

    def set_project_root(self, path: str | None) -> None:
        # Store only; no validation, no scanning (per requirements).
        self._project_root = path or None
        self._cached_prod_reg = None
        self._cached_prod_reg_key = None
        self._schedule_bars = {}
        self._schedule_data = None
        self._prune_filter_status_ids_to_registry()
        self._dept_registry = DepartmentRegistry.for_project(Path(path)) if path else None
        self._grid_delegate.set_active_project_root(self._project_root)
        self._grid_delegate.set_dept_registry(self._dept_registry)
        self._grid_delegate.set_inspector_hidden_departments(self._inspector_hidden_departments)
        if getattr(self, "_list_row_delegate", None) is not None:
            self._list_row_delegate.set_active_project_root(self._project_root)
            self._list_row_delegate.set_hovered_status_row(None)
        self._update_empty_states()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def set_workspace_root(self, path: str | Path | None) -> None:
        try:
            self._workspace_root = Path(path).resolve() if path else None
        except OSError:
            self._workspace_root = None
        self._refresh_list_assignee_column()

    def set_planned_schedule_bars(
        self,
        bars: BarStore | None,
        schedule: ProjectSchedule | None = None,
    ) -> None:
        self._schedule_bars = dict(bars or {})
        self._schedule_data = schedule
        self._refresh_list_due_column()
        self._refresh_list_assignee_column()

    def _refresh_list_due_column(self) -> None:
        if self._browser_context not in ("asset", "shot"):
            return
        col = self._list_col_due()
        if col < 0:
            return
        self._list_model.refresh_column_for_all_rows(
            col, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole]
        )

    def _refresh_list_assignee_column(self) -> None:
        if self._browser_context not in ("asset", "shot"):
            return
        col = self._list_col_assignee()
        if col < 0:
            return
        self._list_model.refresh_column_for_all_rows(
            col, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole]
        )

    def _list_due_text(self, item: ViewItem) -> tuple[str, bool]:
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)) or not self._project_root:
            return "—", False
        schedule = self._schedule_data
        if schedule is None:
            try:
                schedule = read_project_schedule(Path(self._project_root))
            except OSError:
                return "—", False
        kind = "shot" if isinstance(ref, Shot) else "asset"
        try:
            rel = entity_rel_path(Path(self._project_root), ref.path)
        except (OSError, ValueError):
            rel = ref.path.as_posix()
        summary = summarize_entity_schedule(
            self._schedule_bars,
            schedule,
            entity_kind=kind,
            entity_rel=rel,
            active_department=self._active_department,
        )
        return list_due_display(summary, active_department=self._active_department)

    def _list_assignee_display(self, item: ViewItem) -> tuple[str, QColor | None]:
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)) or not self._project_root:
            return "—", None
        dep = (self._active_department or "").strip()
        if not dep:
            return "—", None
        kind = "shot" if isinstance(ref, Shot) else "asset"
        try:
            rel = entity_rel_path(Path(self._project_root), ref.path).replace("\\", "/")
        except (OSError, ValueError):
            rel = ref.path.as_posix()
        if self._schedule_bars is None:
            return "—", None
        row_ids = merged_row_assignee_ids_from_bars(self._schedule_bars, kind, rel, dep)
        if not row_ids:
            bar = primary_bar_for_row(self._schedule_bars, kind, rel, dep)
            if bar is None:
                return "—", None
            row_ids = tuple(bar.assignee_ids) or (
                ((bar.assignee_id or "").strip(),) if (bar.assignee_id or "").strip() else ()
            )
        name, color_hex = resolve_assignee_display(
            self._workspace_root,
            assignee_ids=row_ids,
        )
        if not name:
            return "—", None
        return name, QColor(color_hex)

    def set_inspector_hidden_departments(self, hidden: set[str] | frozenset | None) -> None:
        self._inspector_hidden_departments = set(hidden or ())
        self._grid_delegate.set_inspector_hidden_departments(self._inspector_hidden_departments)
        self._refresh_list_status_column()
        self._tile_view.viewport().update()

    def _list_asset_shot_status_label_and_color(self, ref: Asset | Shot) -> tuple[str, QColor]:
        try:
            reg = self._production_status_registry_cached()
            sid = aggregate_status_id_for_item(
                ref,
                active_department=self._active_department,
                hidden_departments=self._inspector_hidden_departments,
                registry=reg,
            )
            return reg.label_for(sid), QColor(color_hex_for_status_id(sid, reg))
        except Exception:
            return "Waiting", QColor("#71717a")

    @staticmethod
    def _list_status_pill_max_natural_width_for_registry(
        fm: QFontMetrics,
        reg: ProductionStatusRegistry,
    ) -> int:
        """Upper bound for status pill text from registry labels (avoids per-row aggregate_status)."""
        m = _list_status_pill_natural_width("Waiting", fm)
        for st in reg.statuses.values():
            m = max(m, _list_status_pill_natural_width(st.label, fm))
        return m

    def _apply_list_status_column_width(self) -> None:
        """Set Status column width from precomputed pill layout (single resizeSection, no full-table scan)."""
        if self._view_mode != "list":
            return
        sc = self._list_col_status()
        if sc < 0:
            return
        h = self._list_view.horizontalHeader()
        if h is None:
            return
        lw = int(self._list_status_pill_layout_width or 0)
        if self._browser_context == "project":
            if lw > 0:
                h.resizeSection(sc, max(lw, 88))
            return
        if self._browser_context not in ("asset", "shot"):
            return
        dep = (self._active_department or "").strip()
        if dep and lw > 0:
            h.resizeSection(sc, max(lw, 88))

    def _refresh_list_status_column(self) -> None:
        col = self._list_col_status()
        if col < 0:
            return
        chip_font = monos_font("Inter", 10, QFont.Weight.DemiBold)
        fm = QFontMetrics(chip_font)
        if self._browser_context == "project":
            max_natural = max(
                (_list_status_pill_natural_width(lbl, fm) for lbl in project_status_display_labels()),
                default=88,
            )
            self._list_status_pill_layout_width = max_natural + 16
        elif self._browser_context in ("asset", "shot"):
            reg = self._production_status_registry_cached()
            dep = (self._active_department or "").strip()
            max_natural = (
                self._list_status_pill_max_natural_width_for_registry(fm, reg) if dep else 0
            )
            self._list_status_pill_layout_width = max_natural + 16 if dep else 0
        else:
            return
        self._apply_list_status_column_width()
        self._list_model.refresh_column_for_all_rows(col, [Qt.DisplayRole, Qt.ForegroundRole])

    def _refresh_list_last_updated_column(self) -> None:
        if self._browser_context not in ("asset", "shot"):
            return
        col = self._list_col_last_updated()
        if col < 0 or self._list_model.columnCount() <= col:
            return
        self._list_model.refresh_column_for_all_rows(col, [Qt.DisplayRole])

    def set_empty_override(self, message: str | None) -> None:
        # Allows higher-level flows (e.g. workspace discovery) to present a neutral empty state.
        self._empty_override = message
        self._update_empty_states()

    def clear(self) -> None:
        # Clear Main View (no filesystem scan in this phase).
        self._tile_view.clearSelection()
        self._list_view.clearSelection()
        self._all_items = []
        self._items_unfiltered = []
        self._items = {}
        self._order = []
        self._tile_model.bind_rows(self._all_items)
        self._list_model.reset_structure()
        self._apply_list_column_defaults()
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def _load_filter_status_ids_from_settings(self) -> set[str]:
        raw = self._settings.value(self._SETTINGS_KEY_FILTER_STATUS_IDS)
        if raw is None:
            return set()
        if isinstance(raw, (list, tuple)):
            return {str(x).strip() for x in raw if str(x).strip()}
        text = str(raw).strip()
        if not text:
            return set()
        return {p.strip() for p in text.split(",") if p.strip()}

    def _save_filter_status_ids_to_settings(self) -> None:
        self._settings.setValue(
            self._SETTINGS_KEY_FILTER_STATUS_IDS,
            sorted(self._filter_status_ids),
        )

    def _prune_filter_status_ids_to_registry(self) -> None:
        if not self._filter_status_ids:
            return
        try:
            known = set(self._production_status_registry_cached().menu_status_ids())
        except Exception:
            return
        pruned = self._filter_status_ids & known
        if pruned != self._filter_status_ids:
            self._filter_status_ids = pruned
            self._save_filter_status_ids_to_settings()

    def _view_item_production_status_id(self, vi: ViewItem) -> str | None:
        dep = (self._active_department or "").strip()
        if not dep or not isinstance(vi.ref, (Asset, Shot)):
            return None
        try:
            reg = self._production_status_registry_cached()
            return aggregate_status_id_for_item(
                vi.ref,
                active_department=dep,
                hidden_departments=self._inspector_hidden_departments,
                registry=reg,
            )
        except Exception:
            return "waiting"

    def _show_filter_status_menu(self) -> None:
        menu = self._filter_status_menu
        if menu.isVisible():
            menu.close()
            return
        btn = self._filter_status_btn
        pos = btn.mapToGlobal(QPoint(0, btn.height() + 2))
        menu.popup(pos)

    def _update_filter_status_button_label(self) -> None:
        btn = getattr(self, "_filter_status_btn", None)
        if btn is None:
            return
        if not self._filter_status_ids:
            btn.setText("All statuses")
            btn.setIcon(QIcon())
            return
        try:
            reg = self._production_status_registry_cached()
        except Exception:
            reg = None
        if len(self._filter_status_ids) == 1:
            sid = next(iter(self._filter_status_ids))
            label = reg.label_for(sid) if reg is not None else sid
            btn.setText(label)
            if reg is not None:
                btn.setIcon(_menu_status_dot_icon(color_hex_for_status_id(sid, reg)))
            else:
                btn.setIcon(QIcon())
            return
        btn.setText(f"{len(self._filter_status_ids)} statuses")
        btn.setIcon(QIcon())

    def _rebuild_filter_status_dropdown(self) -> None:
        """Rebuild status filter dropdown menu from merged production status registry."""
        menu = self._filter_status_menu
        menu.clear()
        self._filter_status_actions.clear()
        reg = None
        try:
            reg = self._production_status_registry_cached()
            grouped = reg.statuses_grouped_for_menu()
        except Exception:
            grouped = []
        self._prune_filter_status_ids_to_registry()
        first_section = True
        for _cat, status_ids in grouped:
            if not status_ids:
                continue
            if not first_section:
                menu.addSeparator()
            first_section = False
            for sid in status_ids:
                label = reg.label_for(sid) if reg is not None else sid
                act = QAction(label, menu)
                act.setCheckable(True)
                act.setChecked(sid in self._filter_status_ids)
                act.setData(sid)
                if reg is not None:
                    act.setIcon(_menu_status_dot_icon(color_hex_for_status_id(sid, reg)))
                    st_def = reg.get(sid)
                    if st_def and st_def.tooltip:
                        act.setToolTip(st_def.tooltip)
                act.toggled.connect(self._on_filter_status_action_toggled)
                menu.addAction(act)
                self._filter_status_actions[sid] = act
        if menu.actions():
            menu.addSeparator()
        clear_act = QAction("Clear selection", menu)
        clear_act.triggered.connect(self._on_filter_status_clear_selection)
        menu.addAction(clear_act)
        self._update_filter_status_button_label()

    def _main_view_item_is_skipped_for_active_department(self, vi: ViewItem) -> bool:
        """True when the focused-department production pill would show Skipped (preset id omitted)."""
        dep = (self._active_department or "").strip()
        if not dep or not self._project_root:
            return False
        if not isinstance(vi.ref, (Asset, Shot)):
            return False
        try:
            reg = self._production_status_registry_cached()
            sid = aggregate_status_id_for_item(
                vi.ref,
                active_department=dep,
                hidden_departments=self._inspector_hidden_departments,
                registry=reg,
            )
            return sid == "omitted"
        except Exception:
            return False

    def _main_view_filters_active(self) -> bool:
        if self._browser_context not in ("asset", "shot"):
            return False
        if self._filter_has_reference:
            return True
        if not (self._active_department or "").strip():
            return False
        return (
            self._hide_skipped_cards
            or self._filter_work_folder != self._FILTER_WORK_ALL
            or bool(self._filter_status_ids)
        )

    def _apply_main_view_item_filters(self, items: list[ViewItem]) -> list[ViewItem]:
        if self._browser_context not in ("asset", "shot"):
            return list(items)
        out = list(items)
        if self._filter_has_reference:
            out = [vi for vi in out if self._view_item_has_reference_files(vi)]
        dep = (self._active_department or "").strip()
        if not dep:
            return out
        if self._hide_skipped_cards:
            out = [vi for vi in out if not self._main_view_item_is_skipped_for_active_department(vi)]
        wf = self._filter_work_folder
        if wf == self._FILTER_WORK_HAS:
            out = [
                vi
                for vi in out
                if isinstance(vi.ref, (Asset, Shot))
                and _item_has_work_folder_for_department(vi.ref, dep)
            ]
        elif wf == self._FILTER_WORK_NO:
            out = [
                vi
                for vi in out
                if isinstance(vi.ref, (Asset, Shot))
                and not _item_has_work_folder_for_department(vi.ref, dep)
            ]
        if self._filter_status_ids:
            allowed = self._filter_status_ids
            out = [
                vi
                for vi in out
                if isinstance(vi.ref, (Asset, Shot))
                and (self._view_item_production_status_id(vi) or "waiting") in allowed
            ]
        return out

    def _prepare_visible_items(self, items: list[ViewItem]) -> list[ViewItem]:
        """Filter (optional) then sort for Assets/Shots main view."""
        return self._sort_view_items(self._apply_main_view_item_filters(items))

    def _view_item_sort_key(self, vi: ViewItem) -> tuple:
        field = self._sort_field
        if field == self._SORT_FIELD_DATE:
            dep = (self._active_department or "").strip() or None
            active_dcc = self.get_active_dcc(vi.path, dep) if vi.path and dep else None
            return (
                _view_item_mtime_sort_ts(
                    vi,
                    show_publish=self._show_publish,
                    active_department=dep,
                    active_dcc_id=active_dcc,
                ),
                str(vi.path),
            )
        if field == self._SORT_FIELD_STATUS:
            if not isinstance(vi.ref, (Asset, Shot)):
                return (999, 999, "", str(vi.path))
            try:
                reg = self._production_status_registry_cached()
                sid = aggregate_status_id_for_item(
                    vi.ref,
                    active_department=self._active_department,
                    hidden_departments=self._inspector_hidden_departments,
                    registry=reg,
                )
                cat_idx = reg.category_index(reg.category_for(sid))
                ent = reg.get(sid)
                rank = ent.rank if ent else 0
                label = reg.label_for(sid).casefold()
                return (cat_idx, rank, label, str(vi.path))
            except Exception:
                return (999, 999, "waiting", str(vi.path))
        if field == self._SORT_FIELD_DUE:
            if not isinstance(vi.ref, (Asset, Shot)) or self._project_root is None:
                return (1, date.max, "", str(vi.path))
            kind = "shot" if isinstance(vi.ref, Shot) else "asset"
            rel = entity_rel_path(Path(self._project_root), vi.ref.path)
            schedule = self._schedule_data or ProjectSchedule()
            summary = summarize_entity_schedule(
                self._schedule_bars,
                schedule,
                entity_kind=kind,
                entity_rel=rel,
                active_department=self._active_department,
            )
            due_txt, _ = list_due_display(summary, active_department=self._active_department)
            name_key = (vi.ref.name or "").casefold()
            if due_txt == "—":
                return (1, date.max, name_key, str(vi.path))
            try:
                due_d = date.fromisoformat(due_txt[:10])
                return (0, due_d, name_key, str(vi.path))
            except ValueError:
                return (1, date.max, name_key, str(vi.path))
        if isinstance(vi.ref, Asset):
            return (vi.ref.asset_type, (vi.ref.name or "").casefold(), str(vi.path))
        if isinstance(vi.ref, Shot):
            return ((vi.ref.name or "").casefold(), str(vi.path))
        return (display_name_for_item(vi).casefold(), str(vi.path))

    def _sort_view_items(self, items: list[ViewItem]) -> list[ViewItem]:
        if self._browser_context not in ("asset", "shot") or not items:
            return list(items)
        return sorted(items, key=self._view_item_sort_key, reverse=not self._sort_ascending)

    def _insert_view_item_sorted(self, lst: list[ViewItem], vi: ViewItem) -> None:
        if self._browser_context in ("asset", "shot"):
            new_key = self._view_item_sort_key(vi)
            asc = self._sort_ascending
            insert_row = len(lst)
            for i, existing in enumerate(lst):
                ek = self._view_item_sort_key(existing)
                if (ek > new_key) if asc else (ek < new_key):
                    insert_row = i
                    break
            lst.insert(insert_row, vi)
            return
        new_key = self._asset_sort_key(vi)
        insert_row = 0
        for i, existing in enumerate(lst):
            if self._asset_sort_key(existing) > new_key:
                insert_row = i
                break
        else:
            insert_row = len(lst)
        lst.insert(insert_row, vi)

    def _replace_view_item_in_list_by_path(self, lst: list[ViewItem], vi: ViewItem) -> bool:
        for i, u in enumerate(lst):
            if str(u.path) == str(vi.path) or self._paths_equal(u.path, vi.path):
                lst[i] = vi
                return True
        return False

    def _resort_main_view_visible(self) -> None:
        """Rebuild visible rows from _items_unfiltered (filter + sort). Preserves selection when still visible."""
        if self._browser_context not in ("asset", "shot") or not self._items_unfiltered:
            return
        visible = self._prepare_visible_items(self._items_unfiltered)
        if tuple(str(v.path) for v in self._all_items) == tuple(str(v.path) for v in visible):
            return
        prev = self.selected_view_item()
        prev_path = str(prev.path) if prev is not None else None
        self._in_batch_set_items = True
        self._tile_model.blockSignals(True)
        self._list_model.blockSignals(True)
        try:
            self._all_items = visible
            self._populate_views(visible)
            self._order = [str(vi.path) for vi in visible]
            self._rebuild_items_from_order()
            if prev_path:
                self.select_item_by_path(Path(prev_path))
        finally:
            self._tile_model.blockSignals(False)
            self._list_model.blockSignals(False)
            self._in_batch_set_items = False
        self._update_empty_states()
        if self._view_mode == "list":
            self._show_list_content(force=True)
            QTimer.singleShot(0, self._finish_list_view_layout)

    def set_items(self, items: list[ViewItem], preserve_selection_id: str | None = None) -> None:
        # Caller supplies the full list; Filter submenu may trim visible rows when a department is focused.
        # Avoid "all items disappear then reappear": freeze view + block model signals, re-enable next frame.
        self._in_batch_set_items = True
        self.setUpdatesEnabled(False)
        self._tile_model.blockSignals(True)
        self._list_model.blockSignals(True)
        try:
            self.invalidate_notes_open_count_cache()
            self._items_unfiltered = list(items)
            visible = self._prepare_visible_items(self._items_unfiltered)
            self._all_items = visible
            self._populate_views(visible)
            self._order = [str(vi.path) for vi in self._all_items]
            self._rebuild_items_from_order()
            if preserve_selection_id and preserve_selection_id.strip():
                try:
                    self.select_item_by_path(Path(preserve_selection_id))
                except (TypeError, OSError):
                    pass
        finally:
            self._tile_model.blockSignals(False)
            self._list_model.blockSignals(False)
            self._in_batch_set_items = False

        def _reenable_and_update():
            self.setUpdatesEnabled(True)
            self._update_empty_states()
            self._schedule_grid_layout_sync()
            if self._browser_context == "project":
                self._refresh_list_status_column()
            # Schedule thumbnail prefetch after stack has switched to tile view (fixes missing thumbnails on type/department toggle).
            self._schedule_thumbnail_prefetch()

        QTimer.singleShot(0, _reenable_and_update)

    def _paths_equal(self, a: Path | str, b: Path | str) -> bool:
        """Compare paths for equality (resolved when possible so absolute/relative match)."""
        try:
            pa, pb = Path(a), Path(b)
            if pa == pb:
                return True
            try:
                return pa.resolve() == pb.resolve()
            except OSError:
                return str(pa).strip() == str(pb).strip()
        except (TypeError, OSError):
            return False

    def select_item_by_path(self, path: Path) -> bool:
        """Select the row whose item has the given path; returns True if found and selected."""
        path = Path(path)
        for row in range(self._tile_model.rowCount()):
            tile_idx = self._tile_model.index(row, 0)
            if not tile_idx.isValid():
                continue
            item = tile_idx.data(Qt.UserRole)
            if isinstance(item, ViewItem) and self._paths_equal(item.path, path):
                list_idx = self._list_model.index(row, 0)
                for view, model_idx in (
                    (self._tile_view, tile_idx),
                    (self._list_view, list_idx),
                ):
                    sm = view.selectionModel()
                    if sm is None or not model_idx.isValid():
                        continue
                    sm.setCurrentIndex(model_idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                    sm.select(model_idx, QItemSelectionModel.SelectionFlag.Select)
                self._tile_view.viewport().update()
                self._list_view.viewport().update()
                self.valid_selection_changed.emit(self.has_valid_selection())
                return True
        return False

    def _thumbnail_request_extras(self, item: ViewItem) -> dict:
        """Pipeline ref + active DCC for ThumbnailManager (render-sequence / user_then rules)."""
        active_dept = (self._active_department or "").strip() or None
        ref = getattr(item, "ref", None)
        pipeline_ref = ref if isinstance(ref, (Asset, Shot)) else None
        active_dcc: str | None = None
        if pipeline_ref is not None and active_dept and getattr(item, "path", None):
            active_dcc = self.get_active_dcc(item.path, active_dept)
        return {"pipeline_ref": pipeline_ref, "active_dcc_id": active_dcc}

    def invalidate_all_thumbnails_for_source_change(self) -> None:
        """After global thumbnail-source setting change: reset row state so grid reloads with new resolver."""
        for row in range(self._tile_model.rowCount()):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            self._tile_model.reset_thumbnail_slot_row(row)
            self._list_model.notify_thumb_column(row)
        self._schedule_thumbnail_prefetch()

    def invalidate_thumbnail(self, item_root: Path, department: str | None = None) -> None:
        """
        Force a thumbnail refresh for a specific item (and optionally a department).
        Uses ThumbnailManager when set; else legacy cache invalidation.
        """
        root = Path(item_root)
        asset_id = str(root)
        active_dept = department or (self._active_department or "").strip() or None
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is not None and hasattr(mgr, "invalidate"):
            mgr.invalidate(asset_id, department=active_dept)
        else:
            for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
                self._thumb_cache.invalidate_file(root / name)

        # Reset row state and re-request or prefetch.
        try:
            rows = int(self._tile_model.rowCount())
        except Exception:
            rows = 0
        for row in range(rows):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            if item.path != root:
                continue
            self._tile_model.reset_thumbnail_slot_row(row)
            icon: QIcon | None = None
            if mgr is not None and hasattr(mgr, "request_thumbnail"):
                pix = mgr.request_thumbnail(
                    asset_id,
                    department=active_dept,
                    **self._thumbnail_request_extras(item),
                )
                if pix is not None:
                    icon = QIcon(pix)
                    self._tile_model.set_row_thumbnail(row, icon, "loaded")
                else:
                    icon = self._icon_for_item(item)
                    self._tile_model.set_row_thumbnail(row, icon, None)
            else:
                icon = self._icon_for_item(item)
                self._tile_model.set_row_thumbnail(row, icon, None)
            self._list_model.notify_thumb_column(row)

        self._schedule_thumbnail_prefetch()

    def repaint_tiles_for_entity(self, entity_id: str) -> None:
        """Force repaint of tiles so delegate re-evaluates (e.g. pending 'creating' status)."""
        if not entity_id or not str(entity_id).strip():
            return
        self._tile_view.viewport().update()

    def repaint_tile_and_list_views(self) -> None:
        """Force repaint of grid and list so DCC status badges reflect latest AppState after scan."""
        rc = self._tile_model.rowCount()
        if rc > 0:
            tl = self._tile_model.index(0, 0)
            br = self._tile_model.index(rc - 1, 0)
            self._tile_model.dataChanged.emit(tl, br, [Qt.UserRole])
            self._list_model.emit_all_user_role_changed()
        self._refresh_list_last_updated_column()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def refresh_thumbnails_for(self, asset_ids: list[str]) -> None:
        """
        Refresh tile thumbnails for the given asset ids or cache keys
        (e.g. after thumbnail ready or invalidate).
        Uses ThumbnailManager when set; only updates visible/cached rows.
        """
        if not asset_ids:
            return
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is None or not hasattr(mgr, "request_thumbnail"):
            return
        active_dept = (self._active_department or "").strip() or None
        from monostudio.ui_qt.thumbnails import parse_department_cache_key
        seen_rows: set[int] = set()
        for raw_id in asset_ids:
            if not raw_id or not str(raw_id).strip():
                continue
            entity_path, _ = parse_department_cache_key(str(raw_id).strip())
            row = self._row_for_item_id(entity_path)
            if row is None or row in seen_rows:
                continue
            seen_rows.add(row)
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            pix = mgr.request_thumbnail(
                entity_path,
                department=active_dept,
                **self._thumbnail_request_extras(item),
            )
            if pix is not None:
                icon = QIcon(pix)
                self._tile_model.set_row_thumbnail(row, icon, "loaded")
            else:
                icon = self._icon_for_item(item)
                self._tile_model.set_row_thumbnail(row, icon, None)
            self._list_model.notify_thumb_column(row)

    def _row_for_item_id(self, item_id: str) -> int | None:
        """Return the model row index for the item with the given path id; path-normalized so updated_ids match."""
        if self._items and item_id in self._items:
            row = self._items[item_id]
            if row < self._tile_model.rowCount():
                return row
        try:
            target = Path(item_id).resolve()
        except Exception:
            return None
        for row in range(self._tile_model.rowCount()):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            try:
                if Path(item.path).resolve() == target:
                    return row
            except Exception:
                if item.path == Path(item_id):
                    return row
        return None

    @staticmethod
    def _asset_sort_key(vi: ViewItem) -> tuple:
        """Deterministic sort key for grid order (asset_type, name)."""
        if isinstance(vi.ref, Asset):
            return (vi.ref.asset_type, vi.ref.name)
        return ((vi.type_badge or "").lower(), (vi.name or "").lower())

    def _rebuild_items_from_order(self) -> None:
        """Rebuild _items from _order so _items[asset_id] = row index."""
        self._items = {aid: row for row, aid in enumerate(self._order)}

    def _insert_row_at(self, row: int, item: ViewItem, one_based_index: int) -> None:
        """Notify views after `_all_items.insert(row, …)` (caller mutates `_all_items`)."""
        self._tile_model.notify_insert_rows(row, 1)
        self._list_model.notify_insert_rows(row, 1)

    def set_selection_from_state(self, selection_id: str | None) -> None:
        """Drive selection from AppState only; does not emit selection_id_changed back."""
        self._selection_driven_by_state = True
        try:
            active = self._active_select_view()
            sm = active.selectionModel()
            if sm is not None and len(sm.selectedIndexes()) > 1:
                return
            if not selection_id or not selection_id.strip():
                self._tile_view.clearSelection()
                self._list_view.clearSelection()
            else:
                try:
                    found = self.select_item_by_path(Path(selection_id))
                    if not found:
                        self._tile_view.clearSelection()
                        self._list_view.clearSelection()
                except Exception:
                    self._tile_view.clearSelection()
                    self._list_view.clearSelection()
            self._update_empty_states()
            self.valid_selection_changed.emit(self.has_valid_selection())
        finally:
            self._selection_driven_by_state = False

    def apply_assets_diff(
        self,
        added_ids: list[str],
        removed_ids: list[str],
        updated_ids: list[str],
        view_item_resolver: Callable[[str], ViewItem | None],
    ) -> None:
        """Apply diff only: remove, update, add affected items. No full rebuild. Uses _items for O(1) lookup."""
        batch_size = len(added_ids) + len(removed_ids) + len(updated_ids)
        batch_update = batch_size > 8
        if batch_update:
            self._tile_view.setUpdatesEnabled(False)
            self._list_view.setUpdatesEnabled(False)
        try:
            self._apply_assets_diff_impl(
                added_ids, removed_ids, updated_ids, view_item_resolver
            )
        finally:
            if batch_update:
                self._tile_view.setUpdatesEnabled(True)
                self._list_view.setUpdatesEnabled(True)
                self._tile_view.viewport().update()
                self._list_view.viewport().update()
        if self._browser_context in ("asset", "shot") and self._items_unfiltered and (
            added_ids or removed_ids or updated_ids
        ):
            self._resort_main_view_visible()
        self._renumber_list_indices()
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def _apply_assets_diff_impl(
        self,
        added_ids: list[str],
        removed_ids: list[str],
        updated_ids: list[str],
        view_item_resolver: Callable[[str], ViewItem | None],
    ) -> None:
        removed_set = set(removed_ids)

        # 1. Remove: reverse row order so indices stay valid.
        rows_to_remove = []
        for rid in removed_ids:
            r = self._row_for_item_id(rid)
            if r is not None:
                rows_to_remove.append(r)
        rows_to_remove = sorted(set(rows_to_remove), reverse=True)
        for r in rows_to_remove:
            self._tile_model.before_remove_row(r)
            del self._all_items[r]
            self._tile_model.notify_remove_rows(r, 1)
            self._list_model.notify_remove_rows(r, 1)
        self._order = [aid for aid in self._order if aid not in removed_set]
        self._items_unfiltered = [vi for vi in self._items_unfiltered if str(vi.path) not in removed_set]
        self._rebuild_items_from_order()

        # 2. Update in place: only affected visuals (label, status, thumbnail). Use path-normalized row lookup.
        for uid in updated_ids:
            vi = view_item_resolver(uid)
            if vi is None:
                continue
            if not self._replace_view_item_in_list_by_path(self._items_unfiltered, vi):
                self._insert_view_item_sorted(self._items_unfiltered, vi)
            row = self._row_for_item_id(uid)
            if row is None or row >= self._tile_model.rowCount():
                _dcc_debug_log.debug("apply_assets_diff_impl skip update uid=%r row=%s model_rows=%d _order[:5]=%s", uid, row, self._tile_model.rowCount(), (self._order[:5] if self._order else []))
                continue
            _dcc_debug_log.debug("apply_assets_diff_impl updating row=%d uid=%r", row, uid)
            self._tile_model.replace_row_view_item(row, vi)
            self._tile_model.reset_thumbnail_slot_row(row)
            self._list_model.refresh_row(row)
            self._list_model.notify_thumb_column(row)
            if row < len(self._order):
                self._order[row] = uid
            if row < len(self._all_items):
                self._all_items[row] = vi

        if updated_ids:
            self._rebuild_items_from_order()

        # 3. Add: insert at correct sorted position (AppState order).
        for aid in added_ids:
            vi = view_item_resolver(aid)
            if vi is None:
                continue
            self._items_unfiltered = [u for u in self._items_unfiltered if str(u.path) != str(vi.path)]
            self._insert_view_item_sorted(self._items_unfiltered, vi)
            insert_row = len(self._all_items)
            if self._browser_context in ("asset", "shot"):
                new_key = self._view_item_sort_key(vi)
                asc = self._sort_ascending
                for i, existing_vi in enumerate(self._all_items):
                    ek = self._view_item_sort_key(existing_vi)
                    if (ek > new_key) if asc else (ek < new_key):
                        insert_row = i
                        break
            else:
                new_key = self._asset_sort_key(vi)
                for i, existing_vi in enumerate(self._all_items):
                    if self._asset_sort_key(existing_vi) > new_key:
                        insert_row = i
                        break
            self._order.insert(insert_row, aid)
            self._all_items.insert(insert_row, vi)
            self._insert_row_at(insert_row, vi, insert_row + 1)
            self._rebuild_items_from_order()

    def apply_assets_diff_from_assets(
        self,
        added: list[Asset],
        removed: list[str],
        updated: list[Asset],
        view_item_builder: Callable[[Asset], ViewItem],
    ) -> None:
        """Apply diff from Asset lists only. Grid does not query AppState; data comes from signal/coordinator."""
        added_ids = [str(a.path) for a in added]
        removed_ids = list(removed)
        updated_ids = [str(a.path) for a in updated]
        _dcc_debug_log.debug("apply_assets_diff_from_assets added_ids=%s removed_ids=%s updated_ids=%s", added_ids, removed_ids, updated_ids)

        def resolver(item_id: str) -> ViewItem | None:
            for a in added:
                if str(a.path) == item_id:
                    return view_item_builder(a)
            for a in updated:
                if str(a.path) == item_id:
                    return view_item_builder(a)
            return None

        self.apply_assets_diff(added_ids, removed_ids, updated_ids, resolver)

    def apply_shots_diff(
        self,
        added_ids: list[str],
        removed_ids: list[str],
        updated_ids: list[str],
        view_item_resolver: Callable[[str], ViewItem | None],
    ) -> None:
        """Same as apply_assets_diff for shots context."""
        self.apply_assets_diff(added_ids, removed_ids, updated_ids, view_item_resolver)

    def _renumber_list_indices(self) -> None:
        """Set # column (column 0) to 1-based row index for all rows."""
        self._list_model.refresh_index_column()

    def _populate_views(self, items: list[ViewItem]) -> None:
        # Populate both Tile and List from the same backing list (virtual models; no QStandardItem per row).
        if not getattr(self, "_in_batch_set_items", False):
            self._tile_view.clearSelection()
            self._list_view.clearSelection()

        self._tile_model.bind_rows(items)
        self._list_model.reset_structure()
        self._apply_list_column_defaults()

        dep_list_status = (self._active_department or "").strip()
        fm_list_pill = QFontMetrics(monos_font("Inter", 10, QFont.Weight.DemiBold))
        if self._browser_context == "project":
            list_pill_max_natural = max(
                (_list_status_pill_natural_width(lbl, fm_list_pill) for lbl in project_status_display_labels()),
                default=88,
            )
            self._list_status_pill_layout_width = list_pill_max_natural + 16
            self._apply_list_status_column_width()
        elif dep_list_status:
            reg_list_status = self._production_status_registry_cached()
            list_pill_max_natural = self._list_status_pill_max_natural_width_for_registry(
                fm_list_pill, reg_list_status
            )
            self._list_status_pill_layout_width = list_pill_max_natural + 16
            self._apply_list_status_column_width()
        else:
            self._list_status_pill_layout_width = 0
            self._apply_list_status_column_width()

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def _settings_key_view_mode(self) -> str:
        return f"{self._SETTINGS_KEY_VIEW_MODE_PREFIX}/{self._browser_context}"

    def _settings_key_card_size(self) -> str:
        return f"{self._SETTINGS_KEY_CARD_SIZE_PREFIX}/{self._browser_context}"

    def _scale_from_slider(self, value: int) -> float:
        """Map slider 0..CARD_SLIDER_RANGE to scale CARD_SCALE_MIN..CARD_SCALE_MAX."""
        r = self._CARD_SLIDER_RANGE
        if r <= 0:
            return self._CARD_SCALE_MIN
        t = max(0, min(value, r)) / r
        return self._CARD_SCALE_MIN + t * (self._CARD_SCALE_MAX - self._CARD_SCALE_MIN)

    def _slider_from_scale(self, scale: float) -> int:
        """Map scale to slider value 0..CARD_SLIDER_RANGE."""
        s = max(self._CARD_SCALE_MIN, min(scale, self._CARD_SCALE_MAX))
        r = self._CARD_SLIDER_RANGE
        t = (s - self._CARD_SCALE_MIN) / (self._CARD_SCALE_MAX - self._CARD_SCALE_MIN)
        return int(round(t * r))

    def _load_card_scale(self) -> float:
        raw = self._settings.value(self._settings_key_card_size(), 0.7)
        if isinstance(raw, (int, float)):
            s = float(raw)
            return max(self._CARD_SCALE_MIN, min(s, self._CARD_SCALE_MAX))
        raw = (str(raw).strip().lower() or "0.7")
        try:
            s = float(raw)
            return max(self._CARD_SCALE_MIN, min(s, self._CARD_SCALE_MAX))
        except ValueError:
            pass
        # Legacy preset names
        legacy = {"small": 0.4, "medium_small": 0.55, "medium": 0.7, "medium_large": 0.85, "large": 1.0}
        return float(legacy.get(raw, 0.7))

    def _card_scale(self) -> float:
        return self._card_scale_value

    def _sync_main_view_options_popup_geometry(self) -> None:
        """Resize popup to fit current content (submenu expand/collapse)."""
        p = self._main_view_options_popup
        lay = p.layout()
        if lay is not None:
            lay.activate()
        p.updateGeometry()
        hint = p.sizeHint()
        w = max(p.minimumWidth(), hint.width())
        h = hint.height()
        p.setMinimumHeight(0)
        p.setMaximumHeight(16777215)
        if p.isVisible():
            p.resize(w, h)

    def _show_main_view_options_popup(self) -> None:
        """Show main view options below the header button (size, source; filter/sort later). Toggle if open; reopen grace."""
        if self._main_view_options_popup.isVisible():
            self._main_view_options_popup.close()
            return
        if (time.monotonic() - self._main_view_options_popup_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        self._card_size_slider.blockSignals(True)
        self._card_size_slider.setValue(self._slider_from_scale(self._card_scale_value))
        self._card_size_slider.blockSignals(False)
        self._sync_thumb_source_radios_from_settings()
        tile = self._view_mode == "tile"
        self._main_view_options_size_block.setVisible(tile)
        self._main_view_options_sep.setVisible(tile)
        ctx = self._browser_context
        self._thumb_source_asset_block.setVisible(ctx in ("asset", "project"))
        self._thumb_source_shot_block.setVisible(ctx in ("shot", "project"))
        self._thumb_source_mid_sep.setVisible(ctx == "project")
        # Grid status (dot + pill) only when a sidebar department is focused — no separate "chips without dept" mode.
        show_dc = False
        self._dept_chips_sep.setVisible(show_dc)
        self._dept_chips_block.setVisible(show_dc)
        self._chk_dept_status_chips.blockSignals(True)
        self._chk_dept_status_chips.setChecked(self._show_dept_status_chips)
        self._chk_dept_status_chips.blockSignals(False)
        show_asset_shot_opts = ctx in ("asset", "shot")
        show_metadata = tile and show_asset_shot_opts
        self._filter_sep.setVisible(show_asset_shot_opts)
        self._filter_submenu.setVisible(show_asset_shot_opts)
        self._sort_sep.setVisible(show_asset_shot_opts)
        self._sort_submenu.setVisible(show_asset_shot_opts)
        self._metadata_sep.setVisible(show_metadata)
        self._metadata_submenu.setVisible(show_metadata)
        if show_asset_shot_opts:
            self._chk_hide_skipped_cards.blockSignals(True)
            self._chk_hide_skipped_cards.setChecked(self._hide_skipped_cards)
            self._chk_hide_skipped_cards.blockSignals(False)
            self._chk_filter_has_reference.blockSignals(True)
            self._chk_filter_has_reference.setChecked(self._filter_has_reference)
            self._chk_filter_has_reference.blockSignals(False)
            self._sync_filter_work_radios_from_settings()
            self._rebuild_filter_status_dropdown()
            self._sync_main_view_options_popup_geometry()
            self._sync_sort_radios_from_settings()
        if show_metadata:
            for chk, val in (
                (self._chk_tile_meta_id, self._tile_meta_show_id),
                (self._chk_tile_meta_version, self._tile_meta_show_version),
                (self._chk_tile_meta_last_updated, self._tile_meta_show_last_updated),
                (self._chk_tile_meta_latest_note, self._tile_meta_show_latest_note),
                (self._chk_tile_meta_current_dept, self._tile_meta_show_current_department),
                (self._chk_tile_meta_status_pill, self._tile_meta_show_status_pill),
            ):
                chk.blockSignals(True)
                chk.setChecked(val)
                chk.blockSignals(False)
        position_popup_near_anchor(self._main_view_options_popup, self._btn_main_view_options, gap=2)
        self._main_view_options_popup.show()
        self._sync_main_view_options_popup_geometry()
        QTimer.singleShot(0, self._sync_main_view_options_popup_geometry)

    def _sync_thumb_source_radios_from_settings(self) -> None:
        ma = read_inspector_thumbnail_source(self._settings, entity="asset")
        ms = read_inspector_thumbnail_source(self._settings, entity="shot")
        for w in (
            self._thumb_source_asset_user,
            self._thumb_source_asset_render,
            self._thumb_source_asset_both,
            self._thumb_source_shot_user,
            self._thumb_source_shot_render,
            self._thumb_source_shot_both,
        ):
            w.blockSignals(True)
        self._thumb_source_asset_user.setChecked(ma == THUMB_SOURCE_USER)
        self._thumb_source_asset_render.setChecked(ma == THUMB_SOURCE_RENDER_SEQUENCE)
        self._thumb_source_asset_both.setChecked(ma == THUMB_SOURCE_USER_THEN_RENDER)
        self._thumb_source_shot_user.setChecked(ms == THUMB_SOURCE_USER)
        self._thumb_source_shot_render.setChecked(ms == THUMB_SOURCE_RENDER_SEQUENCE)
        self._thumb_source_shot_both.setChecked(ms == THUMB_SOURCE_USER_THEN_RENDER)
        for w in (
            self._thumb_source_asset_user,
            self._thumb_source_asset_render,
            self._thumb_source_asset_both,
            self._thumb_source_shot_user,
            self._thumb_source_shot_render,
            self._thumb_source_shot_both,
        ):
            w.blockSignals(False)

    def _on_main_view_thumb_source_asset_clicked(self, button_id: int) -> None:
        self._apply_main_view_thumb_source_for_entity("asset", int(button_id))

    def _on_main_view_thumb_source_shot_clicked(self, button_id: int) -> None:
        self._apply_main_view_thumb_source_for_entity("shot", int(button_id))

    def _apply_main_view_thumb_source_for_entity(
        self, entity: Literal["asset", "shot"], button_id: int
    ) -> None:
        by_id = {
            0: THUMB_SOURCE_USER,
            1: THUMB_SOURCE_RENDER_SEQUENCE,
            2: THUMB_SOURCE_USER_THEN_RENDER,
        }
        mode = by_id.get(button_id)
        if mode is None:
            return
        if read_inspector_thumbnail_source(self._settings, entity=entity) == mode:
            return
        write_inspector_thumbnail_source(self._settings, mode, entity=entity)
        self.thumbnail_source_changed.emit()

    def _on_dept_status_chips_toggled(self, checked: bool) -> None:
        self._show_dept_status_chips = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_SHOW_DEPT_STATUS_CHIPS, self._show_dept_status_chips)
        self._grid_delegate.set_show_dept_chips(self._show_dept_status_chips)
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()

    def _on_tile_meta_id_toggled(self, checked: bool) -> None:
        self._tile_meta_show_id = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_TILE_META_SHOW_ID, self._tile_meta_show_id)
        self._apply_tile_meta_to_delegate()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()

    def _on_tile_meta_version_toggled(self, checked: bool) -> None:
        self._tile_meta_show_version = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_TILE_META_SHOW_VERSION, self._tile_meta_show_version)
        self._apply_tile_meta_to_delegate()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()

    def _on_tile_meta_last_updated_toggled(self, checked: bool) -> None:
        self._tile_meta_show_last_updated = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_TILE_META_SHOW_LAST_UPDATED, self._tile_meta_show_last_updated)
        self._apply_tile_meta_to_delegate()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()

    def _on_tile_meta_latest_note_toggled(self, checked: bool) -> None:
        self._tile_meta_show_latest_note = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_TILE_META_SHOW_LATEST_NOTE, self._tile_meta_show_latest_note)
        self._apply_tile_meta_to_delegate()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()

    def _on_tile_meta_current_dept_toggled(self, checked: bool) -> None:
        self._tile_meta_show_current_department = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_TILE_META_SHOW_CURRENT_DEPT, self._tile_meta_show_current_department)
        self._apply_tile_meta_to_delegate()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()

    def _on_tile_meta_status_pill_toggled(self, checked: bool) -> None:
        self._tile_meta_show_status_pill = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_TILE_META_SHOW_STATUS_PILL, self._tile_meta_show_status_pill)
        self._apply_tile_meta_to_delegate()
        self._grid_delegate.set_hovered_pill_row(None)
        self._grid_delegate.set_hovered_health_row(None)
        self._grid_delegate.set_hovered_notes_row(None)
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()

    def _on_filter_status_action_toggled(self, checked: bool) -> None:
        act = self.sender()
        if not isinstance(act, QAction):
            return
        sid = str(act.data() or "").strip()
        if not sid:
            return
        if checked:
            self._filter_status_ids.add(sid)
        else:
            self._filter_status_ids.discard(sid)
        self._save_filter_status_ids_to_settings()
        self._update_filter_status_button_label()
        if self._browser_context in ("asset", "shot") and self._items_unfiltered:
            self._resort_main_view_visible()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _on_filter_status_clear_selection(self) -> None:
        if not self._filter_status_ids:
            return
        self._filter_status_ids.clear()
        for act in self._filter_status_actions.values():
            act.blockSignals(True)
            act.setChecked(False)
            act.blockSignals(False)
        self._save_filter_status_ids_to_settings()
        self._update_filter_status_button_label()
        if self._browser_context in ("asset", "shot") and self._items_unfiltered:
            self._resort_main_view_visible()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _on_hide_skipped_cards_toggled(self, checked: bool) -> None:
        self._hide_skipped_cards = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_HIDE_SKIPPED_CARDS, self._hide_skipped_cards)
        if self._browser_context in ("asset", "shot") and self._items_unfiltered:
            self._resort_main_view_visible()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _on_filter_has_reference_toggled(self, checked: bool) -> None:
        self._filter_has_reference = bool(checked)
        self._settings.setValue(self._SETTINGS_KEY_FILTER_HAS_REFERENCE, self._filter_has_reference)
        if self._browser_context in ("asset", "shot") and self._items_unfiltered:
            self._resort_main_view_visible()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _sync_sort_radios_from_settings(self) -> None:
        by_field = {
            self._SORT_FIELD_NAME: 0,
            self._SORT_FIELD_DATE: 1,
            self._SORT_FIELD_STATUS: 2,
            self._SORT_FIELD_DUE: 3,
        }
        field_btn = self._sort_field_group.button(by_field.get(self._sort_field, 0))
        order_btn = self._sort_order_group.button(0 if self._sort_ascending else 1)
        for w in (
            self._sort_by_name,
            self._sort_by_date,
            self._sort_by_status,
            self._sort_by_due,
            self._sort_ascending_rb,
            self._sort_descending_rb,
        ):
            w.blockSignals(True)
        if field_btn is not None:
            field_btn.setChecked(True)
        if order_btn is not None:
            order_btn.setChecked(True)
        for w in (
            self._sort_by_name,
            self._sort_by_date,
            self._sort_by_status,
            self._sort_by_due,
            self._sort_ascending_rb,
            self._sort_descending_rb,
        ):
            w.blockSignals(False)

    def _on_sort_field_clicked(self, button_id: int) -> None:
        by_id = {
            0: self._SORT_FIELD_NAME,
            1: self._SORT_FIELD_DATE,
            2: self._SORT_FIELD_STATUS,
            3: self._SORT_FIELD_DUE,
        }
        field = by_id.get(int(button_id), self._SORT_FIELD_NAME)
        if field == self._sort_field:
            return
        self._sort_field = field
        self._settings.setValue(self._SETTINGS_KEY_SORT_FIELD, field)
        if self._browser_context in ("asset", "shot") and self._items_unfiltered:
            self._resort_main_view_visible()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _on_sort_order_clicked(self, button_id: int) -> None:
        asc = int(button_id) == 0
        if asc == self._sort_ascending:
            return
        self._sort_ascending = asc
        self._settings.setValue(self._SETTINGS_KEY_SORT_ASCENDING, self._sort_ascending)
        if self._browser_context in ("asset", "shot") and self._items_unfiltered:
            self._resort_main_view_visible()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _sync_filter_work_radios_from_settings(self) -> None:
        by_mode = {
            self._FILTER_WORK_ALL: 0,
            self._FILTER_WORK_HAS: 1,
            self._FILTER_WORK_NO: 2,
        }
        btn_id = by_mode.get(self._filter_work_folder, 0)
        btn = self._filter_work_group.button(btn_id)
        for w in (self._filter_work_all, self._filter_work_has, self._filter_work_no):
            w.blockSignals(True)
        if btn is not None:
            btn.setChecked(True)
        for w in (self._filter_work_all, self._filter_work_has, self._filter_work_no):
            w.blockSignals(False)

    def _on_filter_work_folder_clicked(self, button_id: int) -> None:
        by_id = {
            0: self._FILTER_WORK_ALL,
            1: self._FILTER_WORK_HAS,
            2: self._FILTER_WORK_NO,
        }
        mode = by_id.get(int(button_id), self._FILTER_WORK_ALL)
        if mode == self._filter_work_folder:
            return
        self._filter_work_folder = mode
        self._settings.setValue(self._SETTINGS_KEY_FILTER_WORK_FOLDER, mode)
        if self._browser_context in ("asset", "shot") and self._items_unfiltered:
            self._resort_main_view_visible()
        self._grid_last = None
        self._schedule_grid_layout_sync()
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def _on_main_view_options_popup_hidden(self) -> None:
        self._main_view_options_popup_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._btn_main_view_options))

    def _update_main_view_options_button(self) -> None:
        if hasattr(self, "_btn_main_view_options") and self._btn_main_view_options is not None:
            pct = int(round(self._card_scale_value * 100))
            if self._view_mode == "tile":
                self._btn_main_view_options.setToolTip(
                    f"View options — card size {pct}%; Assets & Shots: User / Render / Smart (filter & sort later)"
                )
            else:
                self._btn_main_view_options.setToolTip(
                    "View options — Assets & Shots: User / Render / Smart (filter & sort later)"
                )
            self._btn_main_view_options.setEnabled(True)

    def _on_card_size_slider_changed(self, value: int) -> None:
        scale = self._scale_from_slider(value)
        self.set_card_scale(scale, save=True)

    def set_card_scale(self, scale: float, *, save: bool = True) -> None:
        scale = max(self._CARD_SCALE_MIN, min(float(scale), self._CARD_SCALE_MAX))
        if abs(self._card_scale_value - scale) < 1e-6:
            return
        self._card_scale_value = scale
        if save:
            self._settings.setValue(self._settings_key_card_size(), scale)
        self._update_main_view_options_button()
        self._schedule_grid_layout_sync()

    def _list_headers(self) -> list[str]:
        if self._browser_context == "project":
            return ["", "", "Name", "Status", "Assets", "Shots", "Last Updated", "Path"]
        return [
            "",
            "",
            "Name",
            "Notes",
            "DCC",
            "Health",
            "Ref",
            "Concept",
            "Status",
            "Due",
            "Version",
            "Last Updated",
            "Assignee",
        ]

    def _list_col_notes(self) -> int:
        """Column index for per-item notes chip (asset/shot only); after Name = 3."""
        if self._browser_context != "project":
            return 3
        return -1

    def _list_col_dcc(self) -> int:
        """Column index for DCC badges (asset/shot only); after Notes = 4."""
        if self._browser_context != "project":
            return 4
        return -1

    def _list_col_health(self) -> int:
        """Column index for item health icon (asset/shot only); after DCC = 5."""
        if self._browser_context != "project":
            return 5
        return -1

    def _list_col_ref(self) -> int:
        if self._browser_context != "project":
            return 6
        return -1

    def _list_col_concept(self) -> int:
        if self._browser_context != "project":
            return 7
        return -1

    def _list_col_status(self) -> int:
        """Column index for Status (project: 3, asset/shot: 8)."""
        if self._browser_context == "project":
            return 3
        return 8

    def _list_col_due(self) -> int:
        if self._browser_context != "project":
            return 9
        return -1

    def _list_col_last_updated(self) -> int:
        """Column index for Last Updated (project: 6, asset/shot: 11)."""
        if self._browser_context == "project":
            return 6
        return 11

    def _list_col_assignee(self) -> int:
        if self._browser_context != "project":
            return 12
        return -1

    def _list_version_text(self, item: ViewItem) -> str:
        """Version string for list: work or publish version for active department."""
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return "—"
        ver = _card_version_for_display(
            ref,
            (self._active_department or "").strip() or None,
            self._show_publish,
            _item_active_dcc(item.path, (self._active_department or "").strip() or "") if item.path else None,
        )
        return ver if ver else "—"

    def _list_last_updated(self, item: ViewItem) -> str:
        """Last updated for list/tile: work file or latest publish version folder (Published mode)."""
        dep = (self._active_department or "").strip() or None
        active_dcc = self.get_active_dcc(item.path, dep) if item.path and dep else None
        return _view_item_last_updated_display(
            item,
            show_publish=self._show_publish,
            active_department=dep,
            active_dcc_id=active_dcc,
        )

    @staticmethod
    def _list_departments_text(item: ViewItem) -> str:
        """Comma-separated department names that have work/publish for this item (asset/shot)."""
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return "—"
        labels: list[str] = []
        for d in getattr(ref, "departments", ()) or ():
            if getattr(d, "work_exists", False) or getattr(d, "publish_version_count", 0) > 0:
                name = getattr(d, "name", "") or getattr(d, "label", "")
                if name:
                    labels.append(name)
        return ", ".join(labels) if labels else "—"

    @staticmethod
    def _status_foreground(status: str) -> QColor:
        """Return foreground color for status text (list and badges)."""
        return QColor(project_status_color_hex(status))

    def _apply_list_column_defaults(self) -> None:
        # Column 0 (#) and 1 (Thumb) fixed; others ResizeToContents so min width fits text; Path hidden.
        h = self._list_view.horizontalHeader()
        row_h = 56
        if h:
            h.setMinimumSectionSize(24)
            if self._list_model.columnCount() > 0:
                h.resizeSection(0, 36)  # # column compact
                h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            if self._list_model.columnCount() > 1:
                h.resizeSection(1, row_h)
                h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            # Columns 2..N-2: min width to fit content; last column keeps stretch
            n = self._list_model.columnCount()
            for c in range(2, n - 1):
                h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            if n > 1:
                h.setSectionResizeMode(n - 1, QHeaderView.ResizeMode.Stretch)
        if self._browser_context == "project":
            self._list_view.setColumnHidden(7, True)  # Path
        else:
            dcc_col = self._list_col_dcc()
            if dcc_col >= 0 and h:
                h.setMinimumSectionSize(80)
            notes_col = self._list_col_notes()
            if notes_col >= 0 and h:
                h.resizeSection(notes_col, 44)
                h.setSectionResizeMode(notes_col, QHeaderView.ResizeMode.Fixed)
            health_col = self._list_col_health()
            if health_col >= 0 and h:
                chip = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
                h.resizeSection(
                    health_col,
                    _list_header_column_width("Health", min_content_px=chip + 16),
                )
                h.setSectionResizeMode(health_col, QHeaderView.ResizeMode.Fixed)
            ref_col = self._list_col_ref()
            if ref_col >= 0 and h:
                h.resizeSection(ref_col, 40)
                h.setSectionResizeMode(ref_col, QHeaderView.ResizeMode.Fixed)
            concept_col = self._list_col_concept()
            if concept_col >= 0 and h:
                h.resizeSection(concept_col, 52)
                h.setSectionResizeMode(concept_col, QHeaderView.ResizeMode.Fixed)
            QTimer.singleShot(0, self._apply_list_status_column_width)
        if self._browser_context == "project":
            QTimer.singleShot(0, self._apply_list_status_column_width)

    def _icon_for_item(self, item: ViewItem):
        # Placeholder by kind when no thumbnail: asset/shot/project get type icon; inbox_item = folder.
        if item.kind.value in ("asset", "shot", "project"):
            return self._placeholder_icon_for_kind(item.kind.value)
        if item.kind.value == "inbox_item":
            return lucide_icon("folder", size=20, color_hex=MONOS_COLORS["text_label"])
        if item.kind.value == "inbox_section":
            return lucide_icon("inbox", size=20, color_hex=MONOS_COLORS["text_label"])
        return lucide_icon("folder", size=20, color_hex=MONOS_COLORS["text_label"])

    def _placeholder_icon_for_kind(self, kind: str) -> QIcon:
        """Icon placeholder for tile when user has not set thumbnail or image file is missing."""
        size = self._THUMBNAIL_SIZE_PX
        pix = QPixmap(size, size)
        pix.fill(QColor("#2B2D30"))

        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)
            p.fillRect(0, 0, size, size, QColor("#26282B"))
            p.setPen(QPen(QColor("#3A3D41"), 1))
            p.drawRect(0, 0, size - 1, size - 1)

            icon_name = "box" if kind == "asset" else "clapperboard" if kind == "shot" else "layout-dashboard"
            icon = lucide_icon(icon_name, size=128, color_hex="#A9ABB0")
            src = icon.pixmap(128, 128)
            if not src.isNull():
                x = (size - 128) // 2
                y = (size - 128) // 2
                p.drawPixmap(x, y, src)
        finally:
            p.end()

        return QIcon(pix)

    def _sync_content_stack_pages(self, *, force: bool = False) -> None:
        """Tile/list inner stacks: placeholder (0) vs content view (1) from current row count."""
        if getattr(self, "_in_batch_set_items", False) and not force:
            return
        tile_has_rows = self._tile_model.row_count() > 0
        list_has_rows = self._list_model.rowCount() > 0
        idx_tile = 1 if tile_has_rows else 0
        idx_list = 1 if list_has_rows else 0
        self._tile_page.setCurrentIndex(idx_tile)
        self._list_page.setCurrentIndex(idx_list)
        if tile_has_rows or list_has_rows:
            self._tile_page.update()
            self._list_page.update()
            self.update()

    def _show_list_content(self, *, force: bool = False) -> None:
        """List table is often populated while hidden (grid mode); sync stack + model before show."""
        self._sync_content_stack_pages(force=force)
        if self._tile_model.rowCount() <= 0:
            return
        self._list_page.setCurrentIndex(1)
        self._list_model.reset_structure()
        self._apply_list_column_defaults()
        self._list_view.viewport().update()

    def _finish_list_view_layout(self) -> None:
        if self._view_mode != "list":
            return
        self._apply_list_status_column_width()
        try:
            self._list_view.doItemsLayout()
        except Exception:
            pass
        self._list_view.viewport().update()
        self._schedule_thumbnail_prefetch(force=True)

    def set_view_mode(self, mode: str, *, save: bool = True) -> None:
        # Persistent per-context (stored in QSettings).
        if mode not in ("tile", "list"):
            return
        if mode == self._view_mode:
            if save:
                self._settings.setValue(self._settings_key_view_mode(), mode)
            # Still sync toggle pills when mode unchanged (startup restore may match default).
            self._btn_grid.setChecked(mode == "tile")
            self._btn_list.setChecked(mode == "list")
            return
        self._view_mode = mode
        if mode != "tile":
            self._grid_delegate.set_hovered_pill_row(None)
            self._grid_delegate.set_hovered_health_row(None)
            self._grid_delegate.set_hovered_notes_row(None)
        if mode != "list":
            self._list_row_delegate.set_hovered_status_row(None)
            self._list_row_delegate.set_hovered_health_row(None)
            self._list_row_delegate.set_hovered_notes_row(None)
        self._content.setCurrentIndex(1 if mode == "list" else 0)
        if save:
            self._settings.setValue(self._settings_key_view_mode(), mode)

        # Sync toggle UI
        self._btn_grid.setChecked(mode == "tile")
        self._btn_list.setChecked(mode == "list")
        self.view_mode_changed.emit(mode)
        self._update_main_view_options_button()

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()
        if mode == "tile":
            self._grid_last = None
            self._sync_content_stack_pages(force=True)
            self._schedule_grid_layout_sync()
        if mode == "list":
            self._show_list_content(force=True)
            QTimer.singleShot(0, self._finish_list_view_layout)

    def has_valid_selection(self) -> bool:
        if self._view_mode == "list":
            sm = self._list_view.selectionModel()
            return bool(sm and sm.hasSelection())
        sm = self._tile_view.selectionModel()
        return bool(sm and sm.hasSelection())

    def selected_view_item(self) -> ViewItem | None:
        if self._view_mode == "list":
            sm = self._list_view.selectionModel()
            if sm is None:
                return None
            rows = sm.selectedRows()
            if not rows:
                return None
            item = rows[0].data(Qt.UserRole)
            return item if isinstance(item, ViewItem) else None

        sm = self._tile_view.selectionModel()
        if sm is None:
            return None
        indexes = sm.selectedIndexes()
        if not indexes:
            return None
        item = indexes[0].data(Qt.UserRole)
        return item if isinstance(item, ViewItem) else None

    def clear_selection(self) -> None:
        # Inspector "close" action: clear selection only (no rescan, no data mutation).
        try:
            self._tile_view.clearSelection()
        except Exception:
            pass
        try:
            self._list_view.clearSelection()
        except Exception:
            pass
        self.valid_selection_changed.emit(self.has_valid_selection())

    def _is_item_dimmed(self, item: ViewItem | None) -> bool:
        """True when item should be non-interactive (Published mode, no publish for dept or any dept)."""
        if item is None:
            return False
        if not self._show_publish:
            return False
        if not isinstance(item.ref, (Asset, Shot)):
            return False
        return not _item_has_publish_for_department(item.ref, self._active_department)

    def _active_select_view(self) -> _ClearOnEmptyClickListView | _ClearOnEmptyClickTableView:
        return self._tile_view if self._view_mode == "tile" else self._list_view

    def _deferring_selection_notify(self) -> bool:
        return self._active_select_view().rubber_band_selecting()

    def _selected_index_count(self) -> int:
        view = self._active_select_view()
        sm = view.selectionModel()
        if sm is None:
            return 0
        return len(sm.selectedIndexes())

    def _emit_selection_notify(self) -> None:
        item = self.selected_view_item()
        if self._is_item_dimmed(item):
            view = self._active_select_view()
            view.clearSelection()
            return
        n = self._selected_index_count()
        # AppState is single-select — syncing one id while the view has multi-select would
        # collapse marquee/Ctrl selections via set_selection_from_state → select_item_by_path.
        if n <= 1:
            sid = str(item.path) if item is not None else None
            self.selection_id_changed.emit(sid)
        self.valid_selection_changed.emit(self.has_valid_selection())

    def _flush_deferred_selection_notify(self) -> None:
        if getattr(self, "_selection_driven_by_state", False):
            self._selection_notify_pending = False
            return
        self._selection_notify_pending = False
        self._emit_selection_notify()
        self._repaint_tile_after_marquee()

    def _repaint_tile_after_marquee(self) -> None:
        """Full card paint after marquee (fast-paint is only active while dragging)."""
        if self._view_mode != "tile":
            return
        sm = self._tile_view.selectionModel()
        if sm is None:
            self._tile_view.viewport().update()
            return
        rows = sorted({idx.row() for idx in sm.selectedIndexes()})
        for row in rows:
            idx = self._tile_model.index(row, 0)
            if idx.isValid():
                self._tile_view.update(idx)
        self._tile_view.viewport().update()

    def _on_any_selection_changed(self, *_args) -> None:
        if getattr(self, "_selection_driven_by_state", False):
            return
        if self._deferring_selection_notify():
            self._selection_notify_pending = True
            return
        item = self.selected_view_item()
        if self._is_item_dimmed(item):
            view = self._active_select_view()
            view.clearSelection()
            return
        self._emit_selection_notify()

    def _update_empty_states(self) -> None:
        # Spec: empty states use placeholders; no popup.
        if self._empty_override:
            empty_text = self._empty_override
        elif self._project_root:
            empty_text = "Empty assets / shots"
        else:
            empty_text = "Select a project root to begin"

        loading = is_scanning_empty_message(empty_text)
        self._tile_placeholder.set_content(empty_text, loading=loading)
        self._list_placeholder.set_content(empty_text, loading=loading)

        # During set_items (clear then populate), do not switch stack to placeholder or we get "all items disappear then reappear".
        self._sync_content_stack_pages()

    def _on_tile_activated(self, index) -> None:
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or self._is_item_dimmed(item):
            return
        if isinstance(item.ref, (Asset, Shot)):
            if self._show_publish:
                folder = _resolve_latest_publish_folder(item.ref, self._active_department)
                if folder is not None:
                    self.open_publish_folder_requested.emit(folder)
                return
            if not (self._active_department or "").strip():
                self._notify_select_department()
                return
        self.item_activated.emit(item)

    def _on_list_activated(self, index) -> None:
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or self._is_item_dimmed(item):
            return
        if isinstance(item.ref, (Asset, Shot)):
            if self._show_publish:
                folder = _resolve_latest_publish_folder(item.ref, self._active_department)
                if folder is not None:
                    self.open_publish_folder_requested.emit(folder)
                return
            if not (self._active_department or "").strip():
                self._notify_select_department()
                return
        self.item_activated.emit(item)

    def _notify_transient_hint(self, message: str) -> None:
        from PySide6.QtGui import QCursor
        if getattr(self, "_hint_popup", None) is not None:
            self._hint_popup.deleteLater()
            self._hint_popup = None
        lbl = QLabel(message, self)
        lbl.setStyleSheet(
            "QLabel { background: #18181b; color: #fafafa; border: 1px solid #3f3f46; "
            "border-radius: 8px; padding: 8px 14px; font-family: 'Inter'; font-size: 12px; font-weight: 500; }"
        )
        lbl.setWindowFlags(Qt.ToolTip)
        lbl.adjustSize()
        pos = QCursor.pos()
        lbl.move(pos.x() + 12, pos.y() + 12)
        lbl.show()
        self._hint_popup = lbl
        QTimer.singleShot(3200, lambda: self._dismiss_hint_popup(lbl))

    def _notify_select_department(self) -> None:
        self._notify_transient_hint("Select a department filter first")

    def _resolve_paste_dcc_for_tile(self, item: ViewItem, dep: str, clip: dict | None) -> str | None:
        """
        DCC to use when pasting from tile context: prefer clipboard (from Copy Work File),
        else active DCC / detected work DCC on the destination item.
        """
        if isinstance(clip, dict):
            c = clip.get("dcc_id")
            if isinstance(c, str) and c.strip():
                return c.strip()
        if not isinstance(item.ref, (Asset, Shot)):
            return None
        dep_cf = (dep or "").strip().casefold()
        p = getattr(item.ref, "path", None)
        ad = self.get_active_dcc(p, dep) if p else None
        if isinstance(ad, str) and ad.strip():
            return ad.strip()
        for d in getattr(item.ref, "departments", ()) or ():
            if (d.name or "").strip().casefold() != dep_cf:
                continue
            wfd = getattr(d, "work_file_dcc", None)
            if isinstance(wfd, str) and wfd.strip():
                return wfd.strip()
            dccs = getattr(d, "work_file_dccs", ()) or ()
            for x in dccs:
                if isinstance(x, str) and x.strip():
                    return x.strip()
            break
        return None

    def _perform_paste_work_file(
        self,
        item: ViewItem,
        *,
        department: str,
        dcc_id: str,
        src: Path,
    ) -> bool:
        """Copy src into the resolved work folder as the next version. Returns False on failure."""
        if not isinstance(item.ref, (Asset, Shot)) or not getattr(self, "_project_root", None):
            return False
        dep_norm = (department or "").strip().casefold()
        dept_obj = None
        for d in getattr(item.ref, "departments", ()) or ():
            if (d.name or "").strip().casefold() == dep_norm:
                dept_obj = d
                break
        if dept_obj is None:
            _dcc_debug_log.warning("paste work file: no department %r on item %r", department, getattr(item.ref, "path", None))
            return False
        did = (dcc_id or "").strip()
        if not did:
            return False
        dept_key = (dept_obj.name or "").strip() or (department or "").strip()
        try:
            reg = get_default_dcc_registry()
            use_dcc_folders = read_use_dcc_folders(Path(self._project_root))
            dst_work = resolve_work_path(dept_obj.path, did, use_dcc_folders, reg)
            dst_work.mkdir(parents=True, exist_ok=True)
            prefix = work_file_prefix(
                name=getattr(item.ref, "name", None) or (item.ref.path.name if item.ref.path else ""),
                department=dept_key,
            )
            dst_path = _next_workfile_version_path(dst_work, prefix, did, src.suffix)
            shutil.copy2(src, dst_path)
            self.refresh_requested.emit()
            return True
        except Exception:
            _dcc_debug_log.exception("paste work file failed (dst_work would be under department %r, dcc %r)", department, did)
            return False

    def _dismiss_hint_popup(self, lbl: QLabel) -> None:
        try:
            lbl.hide()
            lbl.deleteLater()
        except RuntimeError:
            pass
        if self._hint_popup is lbl:
            self._hint_popup = None

    def _build_dcc_badge_context_menu(self, item: ViewItem, dcc_id: str, department: str) -> QMenu | None:
        """Build context menu for right-click on a DCC badge."""
        try:
            reg = get_default_dcc_registry()
            info = reg.get_dcc_info(dcc_id)
            dcc_label = info.get("label", dcc_id) if isinstance(info, dict) else dcc_id
            slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
            color = info.get("brand_color_hex") if isinstance(info, dict) else None
        except Exception:
            dcc_label = dcc_id
            slug = None
            color = None

        if isinstance(slug, str) and slug.strip():
            dcc_icon = brand_icon(slug.strip(), size=16, color_hex=(color if isinstance(color, str) else None))
        else:
            dcc_icon = lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"])

        menu = QMenu(self)
        open_act = menu.addAction(dcc_icon, f"Open with {dcc_label}")
        # Submenu "Open older version" when multiple work file versions exist
        older_versions: list[tuple[int, Path]] = []
        if isinstance(item.ref, (Asset, Shot)) and self._project_root:
            dep_norm = (department or "").strip().casefold()
            for d in getattr(item.ref, "departments", ()) or ():
                if (d.name or "").strip().casefold() == dep_norm:
                    use_dcc_folders = read_use_dcc_folders(Path(self._project_root))
                    try:
                        work_path = resolve_work_path(d.path, dcc_id, use_dcc_folders, reg)
                        prefix = work_file_prefix(
                            name=getattr(item.ref, "name", None) or (item.ref.path.name if item.ref.path else ""),
                            department=department,
                        )
                        older_versions = list_work_file_versions(work_path, prefix, dcc_id, reg)
                    except Exception:
                        older_versions = []
                    break
        if len(older_versions) >= 1:
            open_older_menu = QMenu(self)
            open_older_menu.setTitle("Open older version")
            for i, (ver, path) in enumerate(older_versions):
                if i == 0:
                    act = open_older_menu.addAction(f"v{ver:03d} (newest)")
                    act.setEnabled(False)
                else:
                    act = open_older_menu.addAction(f"v{ver:03d}")
                    act.setData(path)
            menu.addMenu(open_older_menu)
        menu.addSeparator()
        folder_act = menu.addAction(
            lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
            f"Open {dcc_label} Folder",
        )
        copy_act = menu.addAction(
            dcc_icon,
            f"Copy {dcc_label} Work Path",
        )
        copy_file_act = menu.addAction(dcc_icon, f"Copy {dcc_label} Work File")
        paste_file_act = menu.addAction(dcc_icon, f"Paste {dcc_label} Work File")
        if not getattr(self, "_work_file_clipboard", None):
            paste_file_act.setEnabled(False)
            paste_file_act.setToolTip("No copied work file yet.")
        else:
            paste_file_act.setToolTip(
                "Paste as next version. Creates missing DCC subfolder and work folder if needed "
                "(or only work/ when project uses flat work paths)."
            )
        menu.addSeparator()
        delete_act = menu.addAction(
            lucide_icon("trash-2", size=16, color_hex="#ef4444"),
            f"Delete {dcc_label} folder\u2026",
        )
        delete_act.setProperty("class", "danger-action")

        menu.setProperty("_dcc_open", open_act)
        menu.setProperty("_dcc_folder", folder_act)
        menu.setProperty("_dcc_copy", copy_act)
        menu.setProperty("_dcc_copy_file", copy_file_act)
        menu.setProperty("_dcc_paste_file", paste_file_act)
        menu.setProperty("_dcc_delete", delete_act)
        menu.setProperty("_dcc_id", dcc_id)
        menu.setProperty("_department", department)
        return menu

    def _dispatch_dcc_badge_action(self, chosen, item: ViewItem, dcc_id: str, department: str) -> None:
        if chosen is None:
            return
        # Open older version: action has path in data()
        path_data = chosen.data() if hasattr(chosen, "data") else None
        if path_data is not None and isinstance(path_data, Path):
            self.dcc_open_version_requested.emit(item, dcc_id, department, path_data)
            return
        text = getattr(chosen, "text", lambda: "")()
        if text.startswith("Open with "):
            self.dcc_open_requested.emit(item, dcc_id, department)
        elif text.startswith("Open ") and text.endswith(" Folder"):
            self.dcc_folder_requested.emit(item, dcc_id, department)
        elif text.startswith("Copy ") and "Work Path" in text:
            self.dcc_copy_path_requested.emit(item, dcc_id, department)
        elif text.startswith("Copy ") and "Work File" in text:
            if isinstance(item.ref, (Asset, Shot)):
                wp = _resolve_work_file_for_department_dcc(item.ref, department, dcc_id)
                if wp is not None:
                    self._work_file_clipboard = {"path": wp, "dcc_id": dcc_id, "department": department}
                    self._copy_full_path(str(wp))
        elif text.startswith("Paste ") and "Work File" in text:
            clip = getattr(self, "_work_file_clipboard", None)
            if isinstance(clip, dict):
                src = clip.get("path")
                if isinstance(src, Path) and src.is_file():
                    if not self._perform_paste_work_file(item, department=department, dcc_id=dcc_id, src=src):
                        self._notify_transient_hint("Could not paste work file (check department path and permissions).")
                else:
                    self._notify_transient_hint("Copied work file is no longer on disk. Copy again.")
            else:
                self._notify_transient_hint("No copied work file. Use Copy Work File first.")
        elif text.startswith("Delete ") and " folder" in text:
            self.dcc_delete_requested.emit(item, dcc_id, department)

    @Slot(QPoint)
    def _on_tile_placeholder_context_menu(self, pos: QPoint) -> None:
        self.root_context_menu_requested.emit(self._tile_placeholder.mapToGlobal(pos))

    @Slot(QPoint)
    def _on_list_placeholder_context_menu(self, pos: QPoint) -> None:
        self.root_context_menu_requested.emit(self._list_placeholder.mapToGlobal(pos))

    def _on_tile_context_menu(self, pos) -> None:
        # DCC badge right-click takes priority
        hit_item, hit_dcc, hit_dep = self._dcc_badge_hit(pos)
        if hit_item and hit_dcc and hit_dep:
            menu = self._build_dcc_badge_context_menu(hit_item, hit_dcc, hit_dep)
            if menu:
                chosen = menu.exec(self._tile_view.viewport().mapToGlobal(pos))
                self._dispatch_dcc_badge_action(chosen, hit_item, hit_dcc, hit_dep)
            return

        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            self.root_context_menu_requested.emit(self._tile_view.viewport().mapToGlobal(pos))
            return
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or self._is_item_dimmed(item):
            return
        menu = self._build_item_context_menu(item)
        if menu is None:
            return
        chosen = menu.exec(self._tile_view.viewport().mapToGlobal(pos))
        self._dispatch_item_context_action(chosen, item)

    def _on_list_context_menu(self, pos) -> None:
        # DCC badge right-click takes priority (same as grid)
        hit_item, hit_dcc, hit_dep = self._list_dcc_hit(pos)
        if hit_item and hit_dcc and hit_dep:
            menu = self._build_dcc_badge_context_menu(hit_item, hit_dcc, hit_dep)
            if menu:
                chosen = menu.exec(self._list_view.viewport().mapToGlobal(pos))
                self._dispatch_dcc_badge_action(chosen, hit_item, hit_dcc, hit_dep)
            return
        index = self._list_view.indexAt(pos)
        if not index.isValid():
            self.root_context_menu_requested.emit(self._list_view.viewport().mapToGlobal(pos))
            return
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem) or self._is_item_dimmed(item):
            return
        menu = self._build_item_context_menu(item)
        if menu is None:
            return
        chosen = menu.exec(self._list_view.viewport().mapToGlobal(pos))
        self._dispatch_item_context_action(chosen, item)

    def _build_item_context_menu(self, item: ViewItem) -> QMenu | None:
        if item.kind.value not in ("asset", "shot", "department", "inbox_item", "project"):
            return None

        menu = QMenu(self)

        if item.kind.value == "project":
            switch_action = menu.addAction(
                lucide_icon("arrow-right", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Switch to Project",
            )
            menu.addSeparator()
            copy_full_path = menu.addAction(
                lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Copy Full Path",
            )
            open_folder = menu.addAction(
                lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open Folder",
            )
            menu.setProperty("_act_switch_project", switch_action)
            menu.setProperty("_act_copy_full_path", copy_full_path)
            menu.setProperty("_act_open_folder", open_folder)
            return menu

        open_action = None
        open_with_action = None
        create_new_action = None
        copy_inventory = None
        if item.kind.value == "inbox_item":
            copy_full_path = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Full Path")
            open_folder = menu.addAction(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Folder")
            menu.setProperty("_act_copy_full_path", copy_full_path)
            menu.setProperty("_act_open_folder", open_folder)
            menu.setProperty("_act_open", None)
            menu.setProperty("_act_open_with", None)
            menu.setProperty("_act_copy_inventory", None)
            menu.setProperty("_act_delete", None)
            menu.setProperty("_act_refresh", None)
            return menu
        has_dept_filter = bool((self._active_department or "").strip())

        if item.kind.value in ("asset", "shot") and self._show_publish:
            open_latest = menu.addAction(lucide_icon("package-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Latest Publish Folder")
            open_pub_root = menu.addAction(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Publish Folder")
            menu.addSeparator()
            if has_dept_filter:
                copy_ctx = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Publish Path")
            else:
                copy_ctx = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Path")
            menu.addSeparator()
            open_folder = menu.addAction(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Folder")
            open_work = menu.addAction(lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Work Folder")
            menu.setProperty("_act_open_latest_publish", open_latest)
            menu.setProperty("_act_open_publish_root", open_pub_root)
            menu.setProperty("_act_copy_context_path", copy_ctx)
            menu.setProperty("_act_open_folder", open_folder)
            menu.setProperty("_act_open", None)
            menu.setProperty("_act_open_with", None)
            menu.setProperty("_act_create_new", None)
            menu.setProperty("_act_refresh", None)
            menu.setProperty("_act_delete", None)
            menu.setProperty("_act_open_work", open_work)
            menu.setProperty("_act_open_publish", None)
            return menu

        if item.kind.value in ("asset", "shot"):
            _no_dept_hint = "Select a department filter first"
            _dim = MONOS_COLORS.get("text_muted", "#52525b")
            _has_work = False
            if has_dept_filter:
                for d in getattr(item.ref, "departments", ()) or ():
                    if (d.name or "").strip().casefold() == (self._active_department or "").strip().casefold():
                        if getattr(d, "work_file_exists", False):
                            _has_work = True
                        break
            active_dcc = self.get_active_dcc(getattr(item.ref, "path", None), self._active_department) if has_dept_filter else None
            # Icon for "Open": if user never clicked badge, use DCC from existing work file (same as double-click).
            if not active_dcc and has_dept_filter and getattr(item.ref, "departments", None):
                for d in item.ref.departments:
                    if (d.name or "").strip().casefold() == (self._active_department or "").strip().casefold():
                        if getattr(d, "work_file_exists", False):
                            active_dcc = getattr(d, "work_file_dcc", None) or (
                                (d.work_file_dccs[0].strip() if d.work_file_dccs else None)
                            )
                        break
            reg = get_default_dcc_registry()
            info = reg.get_dcc_info(active_dcc) if active_dcc else {}
            slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
            color = info.get("brand_color_hex") if isinstance(info, dict) else None
            if isinstance(slug, str) and slug.strip():
                open_icon = brand_icon(slug.strip(), size=16, color_hex=(color if isinstance(color, str) else None))
            else:
                open_icon = lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"] if has_dept_filter else _dim)
            open_action = menu.addAction(open_icon, "Open")
            open_with_action = menu.addAction(lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"] if has_dept_filter else _dim), "Open With…")
            create_new_action = menu.addAction(lucide_icon("file-plus", size=16, color_hex=MONOS_COLORS["text_label"] if has_dept_filter else _dim), "Create New…")
            if not has_dept_filter:
                open_action.setEnabled(False)
                open_action.setToolTip(_no_dept_hint)
                open_with_action.setEnabled(False)
                open_with_action.setToolTip(_no_dept_hint)
                create_new_action.setEnabled(False)
                create_new_action.setToolTip(_no_dept_hint)
            else:
                # Disable Open / Open With when no work file in this department.
                if not _has_work:
                    open_action.setEnabled(False)
                    open_action.setToolTip("No work file in this department.")
                    open_with_action.setEnabled(False)
                    open_with_action.setToolTip("No work file in this department.")
            act_review = menu.addAction(
                lucide_icon("play", size=16, color_hex=MONOS_COLORS["text_label"] if has_dept_filter else _dim),
                "Review latest preview…",
            )
            if not has_dept_filter:
                act_review.setEnabled(False)
                act_review.setToolTip(_no_dept_hint)
            # "Open older version" submenu when right-click on thumbnail: use same active_dcc as icon (already resolved above).
            if has_dept_filter and isinstance(item.ref, (Asset, Shot)) and self._project_root and active_dcc:
                dep_norm = (self._active_department or "").strip().casefold()
                for d in getattr(item.ref, "departments", ()) or ():
                    if (d.name or "").strip().casefold() == dep_norm:
                        use_dcc_folders = read_use_dcc_folders(Path(self._project_root))
                        try:
                            work_path = resolve_work_path(d.path, active_dcc, use_dcc_folders, get_default_dcc_registry())
                            prefix = work_file_prefix(
                                name=getattr(item.ref, "name", None) or (item.ref.path.name if item.ref.path else ""),
                                department=self._active_department,
                            )
                            older_versions = list_work_file_versions(
                                work_path, prefix, active_dcc, get_default_dcc_registry()
                            )
                        except Exception:
                            older_versions = []
                        if len(older_versions) >= 1:
                            open_older_menu = QMenu(self)
                            open_older_menu.setTitle("Open older version")
                            for i, (ver, path) in enumerate(older_versions):
                                if i == 0:
                                    act = open_older_menu.addAction(f"v{ver:03d} (newest)")
                                    act.setEnabled(False)
                                else:
                                    act = open_older_menu.addAction(f"v{ver:03d}")
                                    act.setData((path, active_dcc, self._active_department or ""))
                            menu.addMenu(open_older_menu)
                        break
            menu.addSeparator()
            if has_dept_filter:
                copy_ctx = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Work Path")
                if not _has_work:
                    copy_ctx.setEnabled(False)
                    copy_ctx.setToolTip("No work file in this department.")
                copy_work_file = menu.addAction(open_icon, "Copy Work File")
                paste_work_file = menu.addAction(open_icon, "Paste Work File")
                if not _has_work:
                    copy_work_file.setEnabled(False)
                    copy_work_file.setToolTip("No work file in this department.")
                if not getattr(self, "_work_file_clipboard", None):
                    paste_work_file.setEnabled(False)
                    paste_work_file.setToolTip("No copied work file yet.")
                else:
                    paste_work_file.setToolTip(
                        "Paste as next version. Creates missing DCC subfolder and work folder if needed "
                        "(or only work/ when project uses flat work paths)."
                    )
            else:
                copy_ctx = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Path")
            menu.addSeparator()

        open_folder = menu.addAction(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Folder")
        open_publish_folder = None
        if item.kind.value in ("asset", "shot"):
            open_publish_folder = menu.addAction(
                lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open Publish Folder",
            )
            open_work = menu.addAction(
                lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open Work Folder",
            )
            open_reference = menu.addAction(
                lucide_icon("eye", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open Reference Folder",
            )
            open_concept = menu.addAction(
                lucide_icon("lightbulb", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open Concept Folder",
            )
        else:
            open_reference = None
            open_concept = None

        menu.addSeparator()

        delete_action = None
        rename_action = None
        refresh_action = None
        open_work = None
        open_publish = None

        if item.kind.value in ("asset", "shot"):
            menu.addAction(
                lucide_icon("message-circle", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Notes…",
            )
            refresh_action = menu.addAction(lucide_icon("refresh-cw", size=16, color_hex=MONOS_COLORS["text_label"]), "Refresh")
            if item.kind.value == "asset":
                rename_action = menu.addAction(lucide_icon("pencil", size=16, color_hex=MONOS_COLORS["text_label"]), "Rename… (Beta)")
            kind_word = "Asset" if item.kind.value == "asset" else "Shot"
            ent_name = (item.name or "").strip() or (item.path.name if item.path else "")
            delete_label = f"Move {kind_word} {ent_name} to Trash…" if ent_name else f"Move {kind_word} to Trash…"
            delete_action = menu.addAction(lucide_icon("trash-2", size=16, color_hex="#ef4444"), delete_label)
            if delete_action is not None:
                delete_action.setProperty("class", "danger-action")
                delete_action.setData("delete_asset_or_shot")
        elif item.kind.value == "department":
            open_work = menu.addAction(lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Work Folder")
            open_publish = menu.addAction(lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Publish Folder")

        menu.setProperty("_act_open_folder", open_folder)
        menu.setProperty("_act_open_publish_folder", open_publish_folder)
        menu.setProperty("_act_open", open_action)
        menu.setProperty("_act_open_with", open_with_action)
        menu.setProperty("_act_create_new", create_new_action)
        menu.setProperty("_act_refresh", refresh_action)
        menu.setProperty("_act_rename", rename_action)
        menu.setProperty("_act_delete", delete_action)
        menu.setProperty("_act_open_work", open_work)
        menu.setProperty("_act_open_publish", open_publish)
        menu.setProperty("_act_open_reference", open_reference if item.kind.value in ("asset", "shot") else None)
        menu.setProperty("_act_open_concept", open_concept if item.kind.value in ("asset", "shot") else None)
        return menu

    def _dispatch_item_context_action(self, chosen, item: ViewItem) -> None:
        if chosen is None:
            return

        # Open older version (from thumbnail context menu): action data is (path, dcc_id, department)
        data = getattr(chosen, "data", lambda: None)()
        if isinstance(data, tuple) and len(data) == 3:
            path, dcc_id, department = data[0], data[1], data[2]
            if path is not None and dcc_id and department:
                self.dcc_open_version_requested.emit(item, dcc_id, department, path)
                return

        # Compare by label text; labels are fixed by spec.
        text = getattr(chosen, "text", lambda: "")()

        if text == "Review latest preview…":
            self.review_entity_requested.emit(item)
            return
        if text == "Switch to Project":
            self.switch_project_requested.emit(item)
            return
        if text == "Copy Inventory":
            self.copy_inventory_requested.emit(item)
            return
        if text == "Open":
            self.open_requested.emit(item)
            return
        if text == "Open With…":
            self.open_with_requested.emit(item)
            return
        if text == "Create New…":
            self.create_new_requested.emit(item)
            return
        if text == "Copy Full Path":
            path_str, _ = self._resolved_path_and_folder_for_item(item)
            self._copy_full_path(path_str)
            return
        if text == "Copy Path":
            self._copy_full_path(str(item.path))
            return
        if text == "Copy Work Path":
            if isinstance(item.ref, (Asset, Shot)):
                dep = (self._active_department or "").strip()
                active_dcc = self.get_active_dcc(getattr(item, "path", None), dep) if getattr(item, "path", None) else None
                path_to_copy = _resolved_work_path_for_copy(item.ref, dep, active_dcc)
                if path_to_copy:
                    self._copy_full_path(str(path_to_copy))
                    return
            self._copy_full_path(str(item.path))
            return
        if text == "Copy Work File":
            if isinstance(item.ref, (Asset, Shot)):
                dep = (self._active_department or "").strip()
                if dep:
                    active_dcc = self.get_active_dcc(getattr(item.ref, "path", None), dep)
                    if not active_dcc:
                        for d in getattr(item.ref, "departments", ()) or ():
                            if (d.name or "").strip().casefold() == dep.casefold():
                                active_dcc = getattr(d, "work_file_dcc", None) or (d.work_file_dccs[0].strip() if d.work_file_dccs else None)
                                break
                    if active_dcc:
                        wp = _resolve_work_file_for_department_dcc(item.ref, dep, active_dcc)
                        if wp is not None:
                            self._work_file_clipboard = {"path": wp, "dcc_id": active_dcc, "department": dep}
                            self._copy_full_path(str(wp))
            return
        if text == "Paste Work File":
            if not isinstance(item.ref, (Asset, Shot)) or not getattr(self, "_project_root", None):
                return
            dep = (self._active_department or "").strip()
            if not dep:
                return
            clip = getattr(self, "_work_file_clipboard", None)
            if not isinstance(clip, dict):
                self._notify_transient_hint("No copied work file. Use Copy Work File first.")
                return
            src = clip.get("path")
            if not (isinstance(src, Path) and src.is_file()):
                self._notify_transient_hint("Copied work file is no longer on disk. Copy again.")
                return
            paste_dcc = self._resolve_paste_dcc_for_tile(item, dep, clip)
            if not paste_dcc:
                self._notify_transient_hint(
                    "Could not determine DCC for paste. Copy a work file first, or use Open With on this asset."
                )
                return
            if not self._perform_paste_work_file(item, department=dep, dcc_id=paste_dcc, src=src):
                self._notify_transient_hint("Could not paste work file (check department path and permissions).")
            return
        if text == "Open Folder":
            _, folder = self._resolved_path_and_folder_for_item(item)
            self._open_folder(folder)
            return
        if text == "Open Reference Folder":
            self._open_entity_special_folder_from_item(item, "reference")
            return
        if text == "Open Concept Folder":
            self._open_entity_special_folder_from_item(item, "concept")
            return
        if text == "Notes…":
            self.item_notes_requested.emit(item)
            return
        if text == "Refresh":
            self.refresh_requested.emit()
            return
        if text in ("Rename…", "Rename… (Beta)"):
            if item.kind.value == "asset":
                self.rename_requested.emit(item)
            return
        if getattr(chosen, "data", lambda: None)() == "delete_asset_or_shot":
            self.delete_requested.emit(item)
            return
        if text == "Open Work Folder":
            if hasattr(item, "ref") and item.ref is not None:
                if isinstance(item.ref, (Asset, Shot)):
                    folder = _resolve_work_root_folder_any(item.ref, self._active_department)
                    if folder is not None:
                        self._open_folder(folder)
                elif hasattr(item.ref, "work_path"):
                    self._open_folder(Path(item.ref.work_path))
            return
        if text == "Open Publish Folder":
            if isinstance(item.ref, (Asset, Shot)):
                folder = _resolve_publish_root_folder_any(item.ref, self._active_department)
                if folder is not None:
                    self._open_folder(folder)
            elif hasattr(item, "ref") and item.ref is not None and hasattr(item.ref, "publish_path"):
                self._open_folder(Path(item.ref.publish_path))
            return
        if text == "Open Latest Publish Folder":
            if isinstance(item.ref, (Asset, Shot)):
                folder = _resolve_latest_publish_folder(item.ref, self._active_department)
                if folder is not None:
                    self._open_folder(folder)
            return
        if text == "Copy Publish Path":
            if isinstance(item.ref, (Asset, Shot)):
                ign = get_publish_ignore_extensions(self._settings)
                primary = _resolve_primary_publish_file(
                    item.ref, self._active_department, ignore_extensions=ign
                )
                if primary is not None:
                    self._copy_full_path(str(primary))
                else:
                    folder = _resolve_latest_publish_folder(item.ref, self._active_department)
                    if folder is not None:
                        self._copy_full_path(str(folder))
            return

    def _resolved_path_and_folder_for_item(self, item: ViewItem) -> tuple[str, Path]:
        """
        Resolve path (for copy) and folder (for Open Folder on card) from item and current department.
        When a department is selected and the item (asset/shot) has that department,
        folder is the department folder (d.path); path for copy stays work path when present.
        """
        default_path = Path(item.path)
        active_dep = (self._active_department or "").strip() or None
        if not active_dep or item.kind.value not in ("asset", "shot"):
            return (str(default_path), default_path)
        ref = getattr(item, "ref", None)
        if not isinstance(ref, (Asset, Shot)) or not ref.departments:
            return (str(default_path), default_path)
        for d in ref.departments:
            if (d.name or "").strip().casefold() == active_dep.casefold():
                wp = d.work_path
                path_str = str(wp) if wp.exists() else str(d.path)
                return (path_str, d.path)
        return (str(default_path), default_path)

    def _copy_full_path(self, path_text: str) -> None:
        if not path_text:
            return
        cb = QApplication.clipboard()
        if cb is None:
            return
        cb.setText(path_text)
        from monostudio.ui_qt.notification import notify as notification_service
        notification_service.success(f"Copied: {path_text}")

    def _open_entity_special_folder_from_item(self, item: ViewItem, folder_id: str) -> None:
        if not isinstance(getattr(item, "ref", None), (Asset, Shot)):
            return
        ref = item.ref
        pr = getattr(self, "_project_root", None)
        path = entity_special_folder_path(Path(pr) if pr else None, ref, folder_id)  # type: ignore[arg-type]
        if path is None:
            path = ref.path / folder_id
        if ensure_entity_special_folder(path):
            self._open_folder(path.resolve())

    def _open_folder(self, folder: Path) -> None:
        try:
            if not folder.exists():
                return
        except OSError:
            return
        from monostudio.core.shell_open import open_folder as shell_open_folder

        shell_open_folder(folder)

    def _refresh_thumbnails_for_department_change(self) -> None:
        """Invalidate row thumb slots so prefetch loads the new department — without rebuilding rows."""
        rc = self._tile_model.rowCount()
        if rc <= 0:
            return
        for row in range(rc):
            self._tile_model.reset_thumbnail_slot_row(row)
        self._schedule_thumbnail_prefetch(force=True)

    def _reset_thumb_states_and_prefetch(self) -> None:
        """Clear all thumb states so thumbnails reload for the new department context."""
        self._refresh_thumbnails_for_department_change()

    def _schedule_thumbnail_prefetch(self, *, force: bool = False) -> None:
        if force:
            self._thumb_prefetch_scheduled = False
        if self._thumb_prefetch_scheduled:
            return
        self._thumb_prefetch_scheduled = True
        QTimer.singleShot(0, self._prefetch_visible_thumbnails)

    def _prefetch_visible_thumbnails(self) -> None:
        self._thumb_prefetch_scheduled = False
        self._thumb_prefetch_gen += 1
        gen = self._thumb_prefetch_gen

        if self._tile_model.rowCount() == 0:
            return
        active_dept = (self._active_department or "").strip() or None

        if self._view_mode == "tile":
            if self._tile_page.currentIndex() != 1:
                return
        else:
            list_view = getattr(self, "_list_view", None)
            if list_view is None or self._list_page.currentIndex() != 1:
                return

        self._prefetch_thumbnails_chunk(gen, active_dept, 0)

    def _prefetch_thumbnails_chunk(self, gen: int, active_dept: str | None, start_row: int) -> None:
        if gen != self._thumb_prefetch_gen:
            return

        rc = self._tile_model.rowCount()
        chunk = max(1, int(self._THUMB_PREFETCH_CHUNK_ROWS))
        end = min(start_row + chunk, rc)

        for row in range(start_row, end):
            index = self._tile_model.index(row, 0)
            if not index.isValid():
                continue

            item = index.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            if item.kind.value not in ("asset", "shot", "project"):
                continue

            state = self._tile_model.thumbnail_state_for_row(row)
            if state in ("loaded", "missing"):
                self._list_model.notify_thumb_column(row)
                continue

            asset_id = str(item.path)
            mgr = getattr(self, "_thumbnail_manager", None)
            if mgr is not None and hasattr(mgr, "request_thumbnail"):
                pix = mgr.request_thumbnail(
                    asset_id,
                    department=active_dept,
                    **self._thumbnail_request_extras(item),
                )
                if pix is not None:
                    icon = QIcon(pix)
                    self._tile_model.set_row_thumbnail(row, icon, "loaded")
                    self._list_model.notify_thumb_column(row)
                    continue
                continue

            thumb_file = self._thumb_cache.resolve_thumbnail_file(item.path, department=active_dept)
            if thumb_file is None:
                self._tile_model.set_thumb_state_only(row, "missing")
                continue

            pix = self._thumb_cache.load_thumbnail_pixmap(thumb_file)
            if pix is None:
                self._tile_model.set_thumb_state_only(row, "missing")
                continue

            icon = QIcon(pix)
            self._tile_model.set_row_thumbnail(row, icon, "loaded")
            self._list_model.notify_thumb_column(row)

        if end < rc:
            QTimer.singleShot(
                0,
                lambda g=gen, dep=active_dept, nxt=end: self._prefetch_thumbnails_chunk(g, dep, nxt),
            )

