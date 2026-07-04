from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, QSize, QPoint, QRect, QRectF, QTimer, QSettings, QEvent, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, Department, Shot, ProjectIndex
from monostudio.core.department_status_registry import load_status_registry_for_department
from monostudio.core.production_status import (
    aggregate_status_id_for_item,
    color_hex_for_status_id,
    department_has_status_override,
    effective_status_id_for_department,
    load_production_status_registry,
    override_status_id_for_department,
)
from monostudio.core.entity_folders import (
    EntitySpecialFolderId,
    ensure_entity_special_folder,
    entity_special_folder_path,
    entity_special_folder_paths,
)
from monostudio.core.inbox_reader import load_inbox_destinations, resolve_destination_path
from monostudio.ui_qt.inspector_ref_tab import InspectorRefTab
from monostudio.ui_qt.inspector_schedule_block import InspectorScheduleBlock
from monostudio.core.type_registry import TypeRegistry
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.pipeline_types_and_presets import (
    load_pipeline_types_and_presets_for_project,
    ordered_department_ids_for_scope,
)
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.dcc_status import resolve_dcc_status
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.style import MONOS_COLORS, file_icon_spec_for_path, monos_font
from monostudio.ui_qt.inspector_preview_settings import (
    THUMB_SOURCE_RENDER_SEQUENCE,
    THUMB_SOURCE_USER_THEN_RENDER,
    default_qsettings,
    read_inspector_thumbnail_open_exe,
    read_inspector_thumbnail_source,
    read_sequence_preview_fps,
)
from monostudio.ui_qt.thumbnails import (
    ThumbnailCache,
    active_dcc_segment_for_thumbnail_cache,
    INSPECTOR_INBOX_PREVIEW_DISK_CACHE_VARIANT,
    clamp_decode_side_for_media,
    decode_explorer_preview_qimage_worker,
    get_thumbnail_sequence_ignore_extensions,
    get_thumbnail_sequence_ignore_tokens,
    is_direct_media_preview_path,
    is_video_preview_path,
    media_source_max_side,
    resolve_thumbnail_path,
)
from monostudio.ui_qt.thumbnail_source_resolve import (
    primary_work_file_for_department,
    resolve_department_work_path_for_preview,
    resolve_entity_thumbnail_source_path,
)
from monostudio.core.review_media import ReviewResolveAction, resolve_entity_review_media
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind, display_name_for_item
from monostudio.ui_qt.video_preview_context import PreviewContext, ReviewOpenRequest
from monostudio.ui_qt.view_item_mtime import view_item_last_updated_display
from monostudio.core.fs_reader import work_file_prefix
from monostudio.ui_qt.main_view import (
    ItemHealth,
    _THUMB_HEALTH_CHIP_PAD_PX,
    _THUMB_HEALTH_ICON_PX,
    _department_for_item,
    _item_health_tooltip_text,
    _thumb_note_chip_rect,
    assess_view_item_health,
)
from monostudio.ui_qt.pipeline_row_paint import notes_badge_tooltip_text, paint_note_icon_chip
from monostudio.ui_qt.pipeline_drag_preview import (
    build_single_pipeline_drag_pixmap,
    resolve_grid_card_base_rect,
)
from monostudio.ui_qt.production_status_menu import pick_production_status_at
from monostudio.ui_qt.shell_thumbnail import get_windows_shell_thumbnail
from monostudio.ui_qt.worker_manager import WorkerTask

# Active DCC persistence + version parsing (cùng nguồn với main view)
def _inspector_get_active_dcc(item_path: Path | None, department: str | None) -> str | None:
    from monostudio.ui_qt.main_view import _item_active_dcc
    if not item_path or not department:
        return None
    return _item_active_dcc(item_path, department)


def _inspector_department_registry_from_widget(w: QWidget | None) -> DepartmentRegistry | None:
    p: QWidget | None = w
    while p is not None:
        r = getattr(p, "_department_registry", None)
        if isinstance(r, DepartmentRegistry):
            return r
        p = p.parentWidget()
    return None


def _inspector_canonical_dept_id(hint: str | None, ref: Asset | Shot, registry: DepartmentRegistry | None) -> str | None:
    if not hint or not str(hint).strip():
        return None
    h = str(hint).strip().casefold()
    for d in ref.departments:
        dn = (getattr(d, "name", None) or "").strip()
        if dn and dn.casefold() == h:
            return dn
    if registry is not None:
        for did in registry.get_departments():
            if (did or "").strip().casefold() == h:
                return (did or "").strip()
    return None


def _inspector_synthetic_department(ref: Asset | Shot, dept_id: str, registry: DepartmentRegistry) -> Department:
    ctx = "asset" if isinstance(ref, Asset) else "shot"
    rel = registry.get_department_relative_path(dept_id, ctx)
    base = ref.path / rel if rel else ref.path
    work = base / "work"
    pub = base / "publish"
    work_exists = work.is_dir()
    pub_exists = pub.is_dir()
    return Department(
        name=dept_id,
        path=base,
        work_path=work,
        publish_path=pub,
        work_exists=work_exists,
        work_file_exists=False,
        work_file_dcc=None,
        work_file_dccs=(),
        publish_exists=pub_exists,
        latest_publish_version=None,
        publish_version_count=0,
    )


def _inspector_allowed_department_ids(ref: Asset | Shot, project_root: Path | None) -> list[str] | None:
    """Department IDs for this asset/shot (pipeline types metadata); None → use full registry."""
    if project_root is None:
        return None
    meta = load_pipeline_types_and_presets_for_project(project_root)
    if not meta.types:
        return None
    try:
        registry = DepartmentRegistry.for_project(project_root)
    except OSError:
        registry = None
    scope = "asset" if isinstance(ref, Asset) else "shot"
    type_id = (ref.asset_type or "").strip() if isinstance(ref, Asset) else None
    ids = ordered_department_ids_for_scope(
        meta, scope, type_id=type_id or None, registry=registry
    )
    return ids if ids else None


def _inspector_merge_departments_with_registry(
    ref: Asset | Shot,
    registry: DepartmentRegistry,
    *,
    allowed_dept_ids: list[str] | None = None,
) -> tuple[Department, ...]:
    scanned_by_cf: dict[str, Department] = {}
    for d in ref.departments:
        cf = (d.name or "").strip().casefold()
        if cf and cf not in scanned_by_cf:
            scanned_by_cf[cf] = d
    out: list[Department] = []
    dept_ids = allowed_dept_ids if allowed_dept_ids is not None else registry.get_departments()
    for did in dept_ids:
        cf = (did or "").strip().casefold()
        if cf in scanned_by_cf:
            out.append(scanned_by_cf[cf])
        else:
            out.append(_inspector_synthetic_department(ref, did, registry))
    return tuple(out)


def _inspector_work_and_publish_paths(
    ref: Asset | Shot,
    dept_id: str,
    registry: DepartmentRegistry | None,
) -> tuple[Path, Path] | None:
    dep_cf = (dept_id or "").strip().casefold()
    for d in ref.departments:
        if (d.name or "").strip().casefold() == dep_cf:
            return (d.work_path, d.publish_path)
    if registry is not None:
        ctx = "asset" if isinstance(ref, Asset) else "shot"
        try:
            rel = registry.get_department_relative_path(dept_id.strip(), ctx)
        except Exception:
            return None
        if not rel:
            return None
        base = ref.path / rel
        return (base / "work", base / "publish")
    return None


def _inspector_preview_resolve_sequence_folder(
    work_path: Path | None,
    work_file_path: Path | None,
) -> Path | None:
    from monostudio.core.sequence_preview import (
        resolve_best_available_sequence_folder,
        resolve_sequence_folder,
        sequence_folder_has_frames,
    )

    if work_path is None or not work_path.is_dir():
        return None
    sq = resolve_sequence_folder(work_path, work_file_path)
    if sq is None or not sq.is_dir():
        sq = resolve_best_available_sequence_folder(work_path)
    if sq is None or not sq.is_dir() or not sequence_folder_has_frames(sq):
        return None
    return sq


def _inspector_preview_resolve_sequence(
    work_path: Path | None,
    work_file_path: Path | None,
    *,
    ignore_extensions: frozenset[str] | None = None,
    ignore_name_tokens: frozenset[str] | None = None,
) -> tuple[Path | None, list[Path]]:
    from monostudio.core.sequence_preview import list_sequence_frames

    if work_path is None or not work_path.is_dir():
        return (None, [])
    sq = _inspector_preview_resolve_sequence_folder(work_path, work_file_path)
    if sq is None:
        return (None, [])
    return (
        sq,
        list_sequence_frames(
            sq,
            ignore_extensions=ignore_extensions,
            ignore_name_tokens=ignore_name_tokens,
        ),
    )


def _inspector_preview_worker_run(
    path_str: str,
    *,
    is_inbox: bool,
    dept: str | None,
    mode: str,
    work_path_str: str | None,
    work_file_str: str | None,
    decode_max_side: int = 1024,
    sequence_ignore_extensions: frozenset[str] | None = None,
    sequence_ignore_name_tokens: frozenset[str] | None = None,
) -> tuple[str, QImage | None, bool]:
    """Background: load inspector thumb (sequence folder resolved on main thread after apply)."""
    px = max(1, int(decode_max_side))
    p = Path(path_str)
    if is_inbox and p.is_file():
        if is_direct_media_preview_path(p):
            decoded = decode_explorer_preview_qimage_worker(
                path_str,
                px,
                cache_variant=INSPECTOR_INBOX_PREVIEW_DISK_CACHE_VARIANT,
            )
            if decoded is not None:
                _, img = decoded
                if img is not None and not img.isNull():
                    return (path_str, img, True)
        pix = get_windows_shell_thumbnail(p, px)
        if pix is not None and not pix.isNull():
            return (path_str, pix.toImage(), True)
        return (path_str, None, True)

    wp: Path | None = Path(work_path_str) if work_path_str else None
    wf: Path | None = Path(work_file_str) if work_file_str else None
    if wp is not None and not wp.is_dir():
        wp = None
    if wf is not None and not wf.is_file():
        wf = None

    thumb = resolve_entity_thumbnail_source_path(
        p,
        dept,
        mode,
        wp,
        wf,
        sequence_ignore_extensions=sequence_ignore_extensions,
        sequence_ignore_name_tokens=sequence_ignore_name_tokens,
    )
    if thumb is None:
        return (path_str, None, False)
    src_max = media_source_max_side(thumb)
    if src_max is not None:
        px = min(px, src_max)
    use_fit = ".user." in str(thumb)
    cache = ThumbnailCache(size_px=px)
    pm = cache.load_thumbnail_pixmap(thumb)
    if pm is None or pm.isNull():
        return (path_str, None, use_fit)
    return (path_str, pm.toImage(), use_fit)


def _work_file_version_from_path_for_inspector(path: Path | None) -> int | None:
    """Cùng logic main view: parse version từ path stem (hỗ trợ suffix như _fixNecklace)."""
    from monostudio.ui_qt.main_view import _work_file_version_from_path
    if not path:
        return None
    return _work_file_version_from_path(path)


def _inspector_diff(prev: ViewItem | None, cur: ViewItem | None) -> dict[str, bool]:
    """Shallow diff for Inspector: which fields changed. Used to update only affected sections."""
    if prev is None and cur is None:
        return {}
    if prev is None or cur is None:
        return {"item": True, "name": True, "type": True, "status": True, "thumbnail": True, "departments": True}
    if str(prev.path) != str(cur.path):
        return {"item": True, "name": True, "type": True, "status": True, "thumbnail": True, "departments": True}
    out: dict[str, bool] = {"item": False}
    out["name"] = (display_name_for_item(prev) != display_name_for_item(cur))
    out["type"] = ((prev.type_badge or "") != (cur.type_badge or ""))
    out["status"] = False
    # Thumbnail: assume changed if we are doing an incremental update (caller can force thumbnail refresh).
    out["thumbnail"] = False  # Only refresh when explicitly requested (e.g. thumbnailsChanged).
    prev_ref, cur_ref = prev.ref, cur.ref
    if isinstance(prev_ref, (Asset, Shot)) and isinstance(cur_ref, (Asset, Shot)):
        out["departments"] = len(prev_ref.departments) != len(cur_ref.departments) or any(
            p.name != c.name for p, c in zip(prev_ref.departments, cur_ref.departments)
        )
    else:
        out["departments"] = (prev_ref != cur_ref)
    return out


@dataclass(frozen=True)
class AssetShotInspectorData:
    # Spec 5.1
    name: str
    type: str
    absolute_path: str
    created_date: str = "—"
    last_modified: str = "—"


@dataclass(frozen=True)
class DepartmentInspectorData:
    # Spec 5.2 (excluding version fields, shown in Status section for Phase 2a)
    department_name: str
    work_path: str
    publish_path: str


@dataclass(frozen=True)
class DepartmentStatusData:
    work_exists: str  # "Yes" / "No"
    publish_exists: str  # "Yes" / "No"
    latest_version: str  # folder name or "—"
    version_count: str  # integer string


class _InspectorContent(QWidget):
    """Scrollable body of the Inspector. Used to clear department focus when clicking background."""

    background_clicked = Signal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event and getattr(event, "button", lambda: None)() == Qt.LeftButton:
            # Treat any click that is NOT on a department card (or its children)
            # as a background click → clear department focus.
            pos_fn = getattr(event, "position", None)
            if callable(pos_fn):
                p = pos_fn().toPoint()
            else:
                p = event.pos()
            w = self.childAt(p)

            is_dept_card = False
            if w is not None:
                parent = w
                from PySide6.QtWidgets import QFrame

                while parent is not None:
                    if isinstance(parent, QFrame) and parent.objectName() == "InspectorDeptCard":
                        is_dept_card = True
                        break
                    parent = parent.parent()

            if not is_dept_card:
                self.background_clicked.emit()

        super().mousePressEvent(event)


class InspectorPanel(QWidget):
    """
    MONOS Inspector (read-mostly):
    - Header is sticky (outside scroll)
    - Body is a vertical scroll area
    - Sections are read-only and scan-friendly
    - Data injection is via a single entrypoint: set_item(ViewItem | None)
    """

    close_requested = Signal()
    manage_departments_requested = Signal()
    paste_thumbnail_requested = Signal(object)  # emits ViewItem (asset/shot only)
    remove_thumbnail_requested = Signal(object)  # emits ViewItem (asset/shot only)
    open_folder_requested = Signal(object)  # emits ViewItem — mở folder trong explorer
    inbox_distribute_finished = Signal(list)  # list of dicts: {path, destination_id, destination_label, scope, entity_name, target_path}
    active_dcc_changed = Signal(object, str, str)  # path, department, dcc_id — đồng bộ với main view
    production_status_override_requested = Signal(object, str, object)  # Path, department, status_id | None
    inspector_hidden_departments_changed = Signal(set)
    item_notes_dialog_requested = Signal(object)  # ViewItem (asset / shot)
    open_schedule_requested = Signal()
    edit_allocation_requested = Signal()
    assignee_changed = Signal(str, str, str, object)
    assignment_confirmed = Signal()
    video_preview_requested = Signal(object)  # Path — legacy
    sequence_preview_requested = Signal(object)  # legacy
    review_open_requested = Signal(object)  # ReviewOpenRequest
    open_in_openrv_requested = Signal(object)  # ReviewOpenRequest

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorPanel")
        # Ensure QSS background is painted for this container.
        self.setAttribute(Qt.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _InspectorHeader(self)
        self._header.close_clicked.connect(self.close_requested.emit)
        self._tab_buttons = self._header.tab_buttons
        for idx, btn in enumerate(self._tab_buttons):
            btn.clicked.connect(lambda checked=False, i=idx: self._set_inspector_tab(i))
        root.addWidget(self._header, 0)

        self._body_stack = QStackedWidget(self)
        self._body_stack.setObjectName("InspectorBodyStack")
        self._body_stack.setAttribute(Qt.WA_StyledBackground, True)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("InspectorScrollArea")
        self._scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.viewport().setAutoFillBackground(False)

        content = _InspectorContent(self._scroll)
        content.setObjectName("InspectorContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(12, 12, 12, 12)
        self._content_layout.setSpacing(16)

        self._empty = _InspectorEmptyState()
        self._preview = _InspectorPreview()
        self._asset_status = _InspectorAssetStatusBlock()
        self._dept_pipeline = _DepartmentPipeline()
        self._schedule_block = InspectorScheduleBlock()
        self._schedule_block.open_schedule_requested.connect(self.open_schedule_requested.emit)
        self._schedule_block.edit_allocation_requested.connect(self.edit_allocation_requested.emit)
        self._schedule_block.assignee_changed.connect(self.assignee_changed.emit)
        self._schedule_block.assignment_confirmed.connect(self.assignment_confirmed.emit)
        self._tech = _TechnicalSpecs()
        self._stakeholders = _Stakeholders()

        self._dept_pipeline.manage_clicked.connect(self.manage_departments_requested.emit)
        self._dept_pipeline.department_focused.connect(self._on_department_focused)
        self._dept_pipeline.hidden_departments_changed.connect(self._on_hidden_departments_changed)
        self._dept_pipeline.production_status_override_requested.connect(self.production_status_override_requested.emit)
        self._asset_status.item_notes_clicked.connect(self.item_notes_dialog_requested.emit)
        self._preview.paste_requested.connect(self._on_paste_requested)
        self._preview.remove_requested.connect(self._on_remove_requested)
        self._preview.video_preview_requested.connect(self.video_preview_requested.emit)
        self._preview.review_open_requested.connect(self.review_open_requested.emit)
        self._preview.open_in_openrv_requested.connect(self.open_in_openrv_requested.emit)
        self._preview.sequence_preview_requested.connect(self.sequence_preview_requested.emit)
        self._show_publish: bool = False
        self._last_focused_department: str | None = None
        self._asset_status.open_asset_folder_clicked.connect(self._on_open_asset_folder_requested)
        self._asset_status.open_work_folder_clicked.connect(self._on_open_work_folder_requested)
        self._asset_status.open_publish_folder_clicked.connect(self._on_open_publish_folder_requested)
        self._asset_status.open_reference_folder_clicked.connect(self._on_open_reference_folder_requested)
        self._asset_status.open_concept_folder_clicked.connect(self._on_open_concept_folder_requested)
        self._asset_status.copy_reference_path_clicked.connect(
            lambda: self._copy_entity_special_folder_path("reference")
        )
        self._asset_status.copy_concept_path_clicked.connect(
            lambda: self._copy_entity_special_folder_path("concept")
        )
        self._asset_status._identity.active_dcc_changed.connect(self._on_identity_active_dcc_changed)
        self._asset_status._health.health_changed.connect(self._preview._container._w.set_item_health)
        self._preview._container._w.health_chip_clicked.connect(self._on_preview_health_chip_clicked)
        self._preview._container._w.notes_chip_clicked.connect(self._on_preview_notes_chip_clicked)

        self._inbox_destination = _InboxDestinationBlock()
        self._inbox_destination.distribute_finished.connect(self.inbox_distribute_finished.emit)

        self._separator = QFrame(content)
        self._separator.setFrameShape(QFrame.HLine)
        self._separator.setStyleSheet("color: #27272a; background: #27272a; max-height: 1px;")

        for w in (
            self._empty,
            self._preview,
            self._asset_status,
            self._separator,
            self._dept_pipeline,
            self._inbox_destination,
        ):
            self._content_layout.addWidget(w, 0)

        self._content_layout.addStretch(1)
        self._inbox_destination.setVisible(False)
        self._scroll.setWidget(content)
        self._body_stack.addWidget(self._scroll)

        self._ref_tab = InspectorRefTab(self)
        self._ref_tab.open_folder_requested.connect(self.open_folder_requested.emit)
        self._ref_tab.total_file_count_changed.connect(self._on_ref_file_count_changed)
        self._body_stack.addWidget(self._ref_tab)

        details_content = QWidget(self)
        details_content.setObjectName("InspectorDetailsContent")
        details_content.setAttribute(Qt.WA_StyledBackground, True)
        details_l = QVBoxLayout(details_content)
        details_l.setContentsMargins(12, 12, 12, 12)
        details_l.setSpacing(16)
        self._details_empty = _InspectorEmptyState()
        self._details_empty.set_message("Select an item to view details")
        details_l.addWidget(self._details_empty, 0)
        details_l.addWidget(self._schedule_block, 0)
        details_l.addWidget(self._tech, 0)
        details_l.addWidget(self._stakeholders, 0)
        details_l.addStretch(1)
        self._details_scroll = QScrollArea(self)
        self._details_scroll.setObjectName("InspectorDetailsScrollArea")
        self._details_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._details_scroll.setWidgetResizable(True)
        self._details_scroll.setFrameShape(QFrame.NoFrame)
        self._details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._details_scroll.viewport().setAutoFillBackground(False)
        self._details_scroll.setWidget(details_content)
        self._body_stack.addWidget(self._details_scroll)

        root.addWidget(self._body_stack, 1)
        self.setAcceptDrops(True)
        self._body_stack.setAcceptDrops(True)
        self._ref_tab.setAcceptDrops(True)

        # ACTION card pinned below the scroll area (always visible at bottom when distributing)
        action_wrap = QWidget(self)
        action_wrap.setObjectName("InboxActionWrapper")
        aw_lay = QVBoxLayout(action_wrap)
        aw_lay.setContentsMargins(12, 8, 12, 12)
        aw_lay.setSpacing(0)
        aw_lay.addWidget(self._inbox_destination.action_card)
        self._inbox_action_wrapper = action_wrap
        self._inbox_action_wrapper.setVisible(False)
        root.addWidget(self._inbox_action_wrapper, 0)

        self._current_item: ViewItem | None = None
        self._previous_item: ViewItem | None = None
        self._set_item_generation = 0
        self._pending_set_item_hint: str | None = None
        self._empty_message_override: str | None = None
        self._project_root: Path | None = None
        self._schedule_bars: dict = {}
        self._thumbnail_manager: object | None = None
        self._worker_manager: object | None = None
        self._department_label_resolver: object | None = None  # callable[[str], str] | None
        self._department_registry: object | None = None  # DepartmentRegistry | None (để biết subdepartment, display name)
        self._department_icon_map: dict[str, str] = {}  # dept_id -> lucide icon name
        self._type_short_name_map: dict[str, str] = {}  # type_id -> short_name
        self._inspector_settings: QSettings = default_qsettings()
        self._preview.set_qsettings(self._inspector_settings)
        self.set_item(None)
        try:
            saved_tab = int(self._inspector_settings.value("inspector/last_tab_index", 0))
        except (TypeError, ValueError):
            saved_tab = 0
        self._set_inspector_tab(max(0, min(2, saved_tab)), persist=False)

        # Clear department focus when clicking anywhere in the Inspector content
        # that is not a department card.
        content.background_clicked.connect(self._dept_pipeline._on_empty_clicked)

    def set_department_label_resolver(self, resolver: object | None) -> None:
        """Gán hàm dept_id -> label (từ DepartmentRegistry.get_department_label) để hiển thị tên thay ID; None để dùng id."""
        self._department_label_resolver = resolver

    def set_department_registry(self, registry: object | None) -> None:
        """Gán DepartmentRegistry: dùng cho display name + ưu tiên subdepartment trong meta."""
        self._department_registry = registry
        if registry is not None and hasattr(registry, "get_department_label"):
            self._department_label_resolver = registry.get_department_label
        else:
            self._department_label_resolver = None

    def set_department_icon_map(self, icon_map: dict[str, str]) -> None:
        self._department_icon_map = dict(icon_map) if icon_map else {}

    def set_type_short_name_map(self, m: dict[str, str]) -> None:
        self._type_short_name_map = dict(m) if m else {}

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager
        self._preview.set_thumbnail_manager(manager)

    def set_worker_manager(self, manager: object | None) -> None:
        """Optional WorkerManager: load preview thumb in background + loading spinner (như Explorer)."""
        self._worker_manager = manager
        self._preview.set_worker_manager(manager)

    def set_empty_message(self, message: str) -> None:
        """Override default empty-state hint (e.g. Schedule timeline)."""
        self._empty_message_override = (message or "").strip() or None
        if self._current_item is None:
            self._sync_empty_message()

    def _sync_empty_message(self) -> None:
        default = "Select an item to view details"
        self._empty.set_message(self._empty_message_override or default)

    def set_project_root(self, path: Path | str | None) -> None:
        """Open project root for production status presets + overrides."""
        self._project_root = Path(path) if path else None
        self._schedule_block.set_project_root(self._project_root)

    def set_workspace_root(self, path: Path | str | None) -> None:
        self._schedule_block.set_workspace_root(path)
        if self._current_item is not None:
            self._refresh_schedule_block()

    def set_schedule_bars(self, bars: dict | None) -> None:
        self._schedule_bars = bars or {}
        if self._current_item is not None:
            self._refresh_schedule_block()

    def set_schedule_dept_labels(self, labels: dict[str, str]) -> None:
        self._schedule_block.set_dept_labels(labels)

    def set_schedule_editable(self, editable: bool) -> None:
        self._schedule_block.set_schedule_editable(editable)

    def set_app_settings(self, settings: QSettings) -> None:
        """Share MainWindow QSettings so Inspector reads the same keys as Settings dialog."""
        self._inspector_settings = settings
        self._preview.set_qsettings(settings)
        self._header.set_inspector_settings(settings)
        self._asset_status.sync_action_shortcuts(settings)

    def reload_hotkey_tooltips(self) -> None:
        self._header.sync_tab_hotkey_tooltips(self._inspector_settings)
        self._asset_status.sync_action_shortcuts(self._inspector_settings)

    def apply_preview_thumb(self, path_str: str, image_or_none: QImage | None, use_fit: bool) -> None:
        """Main thread: áp dụng thumb đã load từ worker (chỉ khi path khớp item hiện tại)."""
        self._preview.apply_preview_thumb(path_str, image_or_none, use_fit)

    def invalidate_inspector_preview_settings_cache(self) -> None:
        """After Settings save: drop preview RAM cache so thumbnail source / FPS apply."""
        self._preview.invalidate_settings_dependent_cache()
        if self._current_item is not None:
            self._preview.update_thumbnail_only()

    def clear_preview_loading(self) -> None:
        """Tắt loading spinner (khi worker lỗi hoặc hủy)."""
        self._preview.clear_preview_loading()

    def set_active_department(self, department: str | None) -> None:
        """Sync active department from sidebar for department-specific thumbnails."""
        self._preview.set_active_department(department)

    def set_inbox_mapping_selection(
        self,
        paths: list,
        project_root: Path | None,
        project_index: ProjectIndex | None,
    ) -> None:
        """Legacy: use set_inbox_distribute_paths. Inbox mapping list removed; distribute from tree selection."""
        self.set_inbox_distribute_paths(paths, project_root, project_index)

    def set_inbox_distribute_paths(
        self,
        paths: list,
        project_root: Path | None,
        project_index: ProjectIndex | None,
    ) -> None:
        """Inbox: tree selection → preview (first path) + block DESTINATION (all paths). Empty paths → hide block, clear preview."""
        path_list = [Path(p) for p in paths if p] if paths else []
        if not path_list:
            self._inbox_destination.setVisible(False)
            self._inbox_action_wrapper.setVisible(False)
            self._inbox_destination.set_data([], None, None)
            self.set_item(None)
            return
        self._inbox_destination.set_data(path_list, project_root, project_index)
        self._inbox_destination.setVisible(True)
        self._inbox_action_wrapper.setVisible(True)
        first = path_list[0]
        fake = ViewItem(
            kind=ViewItemKind.INBOX_ITEM,
            name=first.name,
            type_badge="",
            path=first,
            departments_count=None,
            ref=None,
        )
        self.set_item(fake)
        self.load_inbox_tree_preview_thumb()
        self._asset_status.setVisible(False)
        self._dept_pipeline.setVisible(False)
        self._tech.setVisible(False)
        self._stakeholders.setVisible(False)
        self._inbox_action_wrapper.setVisible(True)
        self._set_inspector_tab(0, persist=False)

    def clear_transient_hover_states(self) -> None:
        """Clear stuck inspector hover when pointer moves to main content."""
        from monostudio.ui_qt.style import clear_stuck_widget_hover

        self._preview.clear_transient_hover_states()
        for btn in self.findChildren(QToolButton):
            clear_stuck_widget_hover(btn)
        for btn in self.findChildren(QPushButton):
            clear_stuck_widget_hover(btn)
        clear_stuck_widget_hover(self._scroll.viewport())

    def set_inbox_tree_preview(self, path: Path | None) -> None:
        """Inbox / Internal check / Delivery / Reference: tree selection → thumb + metadata; hide distribute."""
        self._inbox_destination.setVisible(False)
        self._inbox_action_wrapper.setVisible(False)
        self._inbox_destination.set_data([], None, None)
        if not path or not path.exists():
            self.set_item(None)
            return
        fake = ViewItem(
            kind=ViewItemKind.INBOX_ITEM,
            name=path.name,
            type_badge="",
            path=path,
            departments_count=None,
            ref=None,
        )
        self.set_item(fake)
        self.load_inbox_tree_preview_thumb()

    def load_inbox_tree_preview_thumb(self) -> None:
        """Load HD preview for explorer file selection (Inbox, Internal check, Delivery, Project Guide)."""
        self._preview.load_inbox_item_preview()

    def set_item(self, item: ViewItem | None, active_department_hint: str | None = None) -> None:
        """Shell updates immediately; heavy sections deferred to next event-loop tick."""
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_inspector_update
            if enabled():
                record_inspector_update("set_item")
        except Exception:
            pass
        prev = self._current_item
        self._previous_item = prev
        self._current_item = item
        self._apply_item_shell(item)
        if item is None:
            return
        self._pending_set_item_hint = active_department_hint
        self._schedule_apply_item_body(item, prev)

    def _apply_item_shell(self, item: ViewItem | None) -> None:
        has_item = item is not None
        self._empty.setVisible(not has_item)
        is_asset_or_shot = has_item and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT)
        is_inbox_item = has_item and item.kind == ViewItemKind.INBOX_ITEM
        self._header.set_tabs_visible(True)
        for w in (self._preview, self._asset_status, self._separator, self._dept_pipeline):
            w.setVisible(has_item and (is_asset_or_shot or is_inbox_item))
        self._schedule_block.setVisible(has_item and is_asset_or_shot)
        self._tech.setVisible(is_asset_or_shot)
        self._stakeholders.setVisible(is_asset_or_shot)
        if has_item:
            self._inbox_action_wrapper.setVisible(False)
        self._ref_tab.set_show_placeholder(not is_asset_or_shot)
        self._details_empty.setVisible(not is_asset_or_shot)
        if not has_item:
            self._sync_empty_message()
            self.inspector_hidden_departments_changed.emit(set())
            self._preview.set_inspector_notes_chip(False, 0)
            self._sync_ref_tab_paths()

    def _schedule_apply_item_body(self, item: ViewItem, prev: ViewItem | None) -> None:
        self._set_item_generation += 1
        gen = self._set_item_generation

        def _run() -> None:
            if gen != self._set_item_generation or self._current_item is not item:
                return
            self._apply_item_body(item, prev, self._pending_set_item_hint)

        QTimer.singleShot(0, _run)

    def _apply_item_body(
        self,
        item: ViewItem,
        prev: ViewItem | None,
        active_department_hint: str | None,
    ) -> None:
        is_asset_or_shot = item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT)
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)):
            hint = (active_department_hint or "").strip() or None
            reg = self._department_registry if isinstance(self._department_registry, DepartmentRegistry) else None
            canon = _inspector_canonical_dept_id(hint, ref, reg) if hint else None
            if canon:
                self._last_focused_department = canon
                self._dept_pipeline.set_sidebar_focus(canon)
            else:
                self._last_focused_department = None
                self._dept_pipeline.set_sidebar_focus(None)

        scroll_bar = self._scroll.verticalScrollBar()
        scroll_pos = scroll_bar.value() if scroll_bar else 0

        diff = _inspector_diff(prev, item)
        full_update = diff.get("item", True) or not prev or str(prev.path) != str(item.path)

        if full_update:
            self._preview.set_item(item)
            try:
                idx = self._content_layout.indexOf(self._preview)
                if idx >= 0:
                    self._content_layout.setStretchFactor(self._preview, 0)
            except Exception:
                pass
            self._dept_pipeline.set_item(item)
            self._asset_status.set_hidden_departments(self._dept_pipeline._hidden_departments)
            _ad = self._last_focused_department
            _ac = _inspector_get_active_dcc(getattr(item, "path", None), _ad) if item else None
            self._asset_status.set_item(item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
            self._tech.set_item(item)
            self._stakeholders.set_item(item)
            self._refresh_schedule_block(item)
            self._sync_tech_last_modified()
        else:
            if diff.get("departments"):
                self._dept_pipeline.set_item(item)
                self._asset_status.set_hidden_departments(self._dept_pipeline._hidden_departments)
            if diff.get("name") or diff.get("type") or diff.get("departments"):
                _ad = self._last_focused_department
                _ac = _inspector_get_active_dcc(getattr(item, "path", None), _ad) if item else None
                self._asset_status.set_item(item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
            if diff.get("status"):
                self._asset_status.update_status(item)
            if diff.get("thumbnail"):
                self._preview.update_thumbnail_only()
            if diff.get("name") or diff.get("type"):
                self._tech.set_item(item)
            if is_asset_or_shot and (diff.get("departments") or diff.get("name") or diff.get("type")):
                self._sync_tech_last_modified()

        if isinstance(ref, (Asset, Shot)):
            self._refresh_schedule_block(item)
            self.inspector_hidden_departments_changed.emit(set(self._dept_pipeline._hidden_departments))
        else:
            self.inspector_hidden_departments_changed.emit(set())

        if scroll_bar and scroll_bar.value() != scroll_pos:
            scroll_bar.setValue(scroll_pos)

        if self._last_focused_department:
            self._on_department_focused(self._last_focused_department)

        if is_asset_or_shot:
            self._sync_preview_notes_chip()
            self._sync_ref_tab_paths()
            if self._body_stack.currentIndex() == 1:
                self._ref_tab.notify_tab_visible()
        else:
            self._preview.set_inspector_notes_chip(False, 0)
            self._sync_ref_tab_paths()

    def _refresh_schedule_block(self, item: ViewItem | None = None) -> None:
        item = item or self._current_item
        self._schedule_block.set_active_department(self._last_focused_department)
        self._schedule_block.set_item(item, bars=self._schedule_bars or None)

    def _entity_for_special_folder(self) -> Asset | Shot | None:
        item = self._current_item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return None
        ref = getattr(item, "ref", None)
        return ref if isinstance(ref, (Asset, Shot)) else None

    def _open_entity_special_folder(self, folder_id: EntitySpecialFolderId) -> None:
        entity = self._entity_for_special_folder()
        if entity is None:
            return
        reg = self._department_registry if isinstance(self._department_registry, DepartmentRegistry) else None
        path = entity_special_folder_path(self._project_root, entity, folder_id, dept_registry=reg)
        if path is None:
            path = entity.path / folder_id
        if ensure_entity_special_folder(path):
            self.open_folder_requested.emit(path.resolve())
            self._sync_ref_tab_paths()
            if self._body_stack.currentIndex() == 1:
                self._ref_tab.refresh_from_disk()

    def _copy_entity_special_folder_path(self, folder_id: EntitySpecialFolderId) -> None:
        entity = self._entity_for_special_folder()
        if entity is None:
            return
        reg = self._department_registry if isinstance(self._department_registry, DepartmentRegistry) else None
        path = entity_special_folder_path(self._project_root, entity, folder_id, dept_registry=reg)
        if path is None or not path.is_dir():
            return
        text = str(path.resolve())
        _TechnicalSpecs._copy_text(text)
        from monostudio.ui_qt.notification import notify as notification_service

        notification_service.success(f"Copied: {text}")

    def _on_open_reference_folder_requested(self) -> None:
        self._open_entity_special_folder("reference")

    def _on_open_concept_folder_requested(self) -> None:
        self._open_entity_special_folder("concept")

    def _sync_ref_tab_paths(self) -> None:
        entity = self._entity_for_special_folder()
        if entity is None:
            self._ref_tab.set_entity_paths(None, {})
            return
        reg = self._department_registry if isinstance(self._department_registry, DepartmentRegistry) else None
        paths = entity_special_folder_paths(self._project_root, entity, dept_registry=reg)
        self._ref_tab.set_entity_paths(entity, paths)
        try:
            win = self.window()
            if win is not None and hasattr(win, "_ensure_entity_special_folders_watched"):
                win._ensure_entity_special_folders_watched(Path(entity.path))  # type: ignore[attr-defined]
        except Exception:
            pass

    def can_handle_ref_tab_external_drop(self) -> bool:
        if self._body_stack.currentIndex() != 1:
            return False
        return self._ref_tab.can_accept_external_drop()

    def try_handle_ref_tab_external_drop(self, paths: list[Path], global_pos: QPoint) -> bool:
        if self._body_stack.currentIndex() != 1:
            return False
        return self._ref_tab.handle_external_drop(paths, global_pos)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._body_stack.currentIndex() == 1 and self._ref_tab.handle_explorer_drag(event):
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._body_stack.currentIndex() == 1 and self._ref_tab.handle_explorer_drag(event):
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._body_stack.currentIndex() == 1 and self._ref_tab.handle_explorer_drop(event):
            return
        event.ignore()

    def _on_ref_file_count_changed(self, count: int) -> None:
        self._header.set_ref_tab_badge(count)

    def _set_inspector_tab(self, index: int, *, persist: bool = True) -> None:
        index = max(0, min(2, int(index)))
        self._header.set_active_tab(index)
        self._body_stack.setCurrentIndex(index)
        if persist:
            self._inspector_settings.setValue("inspector/last_tab_index", index)
        if index == 1:
            self._sync_ref_tab_paths()
            self._ref_tab.notify_tab_visible()

    def set_inspector_tab_index(self, index: int) -> None:
        """Public API for shortcuts (0=Pipeline, 1=Ref, 2=Details)."""
        if self._header.tabs_visible():
            self._set_inspector_tab(index)

    def open_reference_folder_for_selection(self) -> None:
        self._open_entity_special_folder("reference")

    def open_concept_folder_for_selection(self) -> None:
        self._open_entity_special_folder("concept")

    def _sync_preview_notes_chip(self) -> None:
        item = self._current_item
        if item is not None and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            try:
                from monostudio.core.item_comments import notes_badge_visual_mode

                n, mode = notes_badge_visual_mode(
                    Path(item.path),
                    self._last_focused_department,
                )
            except Exception:
                n, mode = 0, "empty"
            self._preview.set_inspector_notes_chip(True, int(n), str(mode))
        else:
            self._preview.set_inspector_notes_chip(False, 0, "empty")

    def refresh_notes_badge(self) -> None:
        """Re-read open note count from disk (e.g. after Notes dialog edits)."""
        item = self._current_item
        if item is not None and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            self._asset_status.refresh_notes_display(item)
        else:
            self._asset_status.refresh_notes_display(None)
        self._sync_preview_notes_chip()

    def refresh_special_folders_for_entity_paths(self, entity_paths: object) -> None:
        """Refresh Ref tab when ``reference/`` or ``concept/`` changed on disk."""
        if not isinstance(entity_paths, list):
            return
        entity = self._entity_for_special_folder()
        if entity is None:
            return
        try:
            current = str(Path(entity.path).resolve())
        except OSError:
            current = str(entity.path)
        touched: set[str] = set()
        for ep in entity_paths:
            if not isinstance(ep, str) or not ep.strip():
                continue
            try:
                touched.add(str(Path(ep.strip()).resolve()))
            except OSError:
                touched.add(ep.strip())
        if current not in touched:
            return
        self._sync_ref_tab_paths()
        self._ref_tab.refresh_from_disk()

    def refresh_thumbnail(self) -> None:
        # Best-effort; safe no-op if nothing selected.
        try:
            self._preview.refresh_thumbnail()
        except Exception:
            pass

    def update_thumbnail_for_current(self) -> None:
        """Update thumbnail only for current item (e.g. after thumbnailsChanged). No layout change."""
        if self._current_item is None:
            return
        try:
            self._preview.drop_preview_thumb_cache_for_item(self._current_item.path)
        except Exception:
            pass
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_inspector_update
            if enabled():
                record_inspector_update("update_thumbnail_only")
        except Exception:
            pass
        try:
            self._preview.update_thumbnail_only()
        except Exception:
            pass

    def _on_open_asset_folder_requested(self) -> None:
        item = self._current_item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        # Always open the asset root folder.
        self.open_folder_requested.emit(item.path)

    def _on_open_work_folder_requested(self) -> None:
        item = self._current_item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)) and self._last_focused_department:
            dep = (self._last_focused_department or "").strip()
            reg = self._department_registry if isinstance(self._department_registry, DepartmentRegistry) else None
            paths = _inspector_work_and_publish_paths(ref, dep, reg)
            if paths:
                self.open_folder_requested.emit(Path(paths[0]))

    def _on_open_publish_folder_requested(self) -> None:
        item = self._current_item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)) and self._last_focused_department:
            dep = (self._last_focused_department or "").strip()
            reg = self._department_registry if isinstance(self._department_registry, DepartmentRegistry) else None
            paths = _inspector_work_and_publish_paths(ref, dep, reg)
            if paths:
                self.open_folder_requested.emit(Path(paths[1]))

    def _on_paste_requested(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.paste_thumbnail_requested.emit(item)

    def _on_remove_requested(self, item: ViewItem) -> None:
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.remove_thumbnail_requested.emit(item)

    def _on_hidden_departments_changed(self, hidden: set) -> None:
        self._asset_status.set_hidden_departments(hidden)
        self.inspector_hidden_departments_changed.emit(set(hidden))

    def _on_preview_health_chip_clicked(self) -> None:
        item = self._current_item
        dep = (self._last_focused_department or "").strip()
        if item is None or not dep or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        ref = getattr(item, "ref", None)
        if not isinstance(ref, (Asset, Shot)):
            return
        active_dcc = _inspector_get_active_dcc(getattr(item, "path", None), dep)
        health = assess_view_item_health(ref, dep, active_dcc_id=active_dcc)
        if health is None:
            return
        from monostudio.ui_qt.item_health_dialog import ItemHealthDialog

        dept_obj = _department_for_item(ref, dep)
        naming_prefix = (
            work_file_prefix(name=getattr(ref, "name", "") or "", department=dept_obj.name)
            if dept_obj
            else ""
        )

        def _trigger_main_refresh() -> None:
            win = self.window()
            mv = getattr(win, "_main_view", None)
            if mv is not None:
                mv.refresh_requested.emit()

        dlg = ItemHealthDialog(
            parent=self,
            item_name=display_name_for_item(item),
            department=dep,
            health=health,
            naming_prefix=naming_prefix or None,
            on_repaired=_trigger_main_refresh,
            health_refresh=(ref, dep, active_dcc),
        )
        dlg.exec()

    def _on_preview_notes_chip_clicked(self) -> None:
        item = self._current_item
        if item is not None and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            self.item_notes_dialog_requested.emit(item)

    def _on_identity_active_dcc_changed(self, path, department: str, dcc_id: str) -> None:
        """Sync active DCC với main view: emit signal và refresh identity để version đúng DCC."""
        self.active_dcc_changed.emit(path, department, dcc_id)
        if self._current_item and getattr(self._current_item, "path", None) == path:
            self._asset_status.set_item(
                self._current_item,
                self._show_publish,
                active_department=department,
                active_dcc_id=dcc_id,
            )
            # Preview resolve uses primary work file for (dept, active DCC); refresh sequence + thumb.
            self._preview.update_thumbnail_only(active_dcc_hint=dcc_id)
            self._sync_tech_last_modified()

    def refresh_last_modified_display(self) -> None:
        """Refresh Last Modified row after filesystem scan (same source as grid/list)."""
        self._sync_tech_last_modified()

    def _sync_tech_last_modified(self) -> None:
        item = self._current_item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        dep = self._last_focused_department
        dcc = _inspector_get_active_dcc(getattr(item, "path", None), dep) if item else None
        self._tech.set_last_modified(
            view_item_last_updated_display(
                item,
                show_publish=self._show_publish,
                active_department=dep,
                active_dcc_id=dcc,
            )
        )

    def _on_department_focused(self, department_name: str) -> None:
        """Update Tech row, status pill, and preview thumbnail with the clicked department."""
        self._last_focused_department = (department_name or "").strip() or None
        self._asset_status.set_focused_department(self._last_focused_department)
        self._preview.set_active_department(self._last_focused_department)
        self._schedule_block.set_active_department(self._last_focused_department)
        self._preview.update_thumbnail_only()
        if self._current_item is not None:
            _ad = self._last_focused_department
            _ac = _inspector_get_active_dcc(getattr(self._current_item, "path", None), _ad) if self._current_item else None
            self._asset_status.set_item(self._current_item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
            self._refresh_schedule_block()
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self._sync_preview_notes_chip()
        dep = self._last_focused_department
        if not dep:
            self._tech.set_resolved_path(None)
            self._sync_tech_last_modified()
            return
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)):
            reg = self._department_registry if isinstance(self._department_registry, DepartmentRegistry) else None
            paths = _inspector_work_and_publish_paths(ref, dep.strip(), reg)
            if paths:
                self._tech.set_resolved_path(paths[1] if self._show_publish else paths[0])
            else:
                self._tech.set_resolved_path(None)
        else:
            self._tech.set_resolved_path(None)
        self._sync_tech_last_modified()

    def set_show_publish(self, show_publish: bool) -> None:
        if self._show_publish == show_publish:
            return
        self._show_publish = show_publish
        if self._current_item is not None:
            _ad = self._last_focused_department
            _ac = _inspector_get_active_dcc(getattr(self._current_item, "path", None), _ad) if self._current_item else None
            self._asset_status.set_item(self._current_item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
        if self._last_focused_department and self._current_item is not None:
            self._on_department_focused(self._last_focused_department)
        else:
            self._sync_tech_last_modified()

    # Backward compatibility (legacy call sites)
    def set_empty_state(self, _message: str | None = None) -> None:
        self.set_item(None)

    def set_asset_shot(self, data: AssetShotInspectorData) -> None:
        # Legacy: present minimal info without departments.
        fake = ViewItem(kind=ViewItemKind.ASSET, name=data.name, type_badge=data.type, path=Path(data.absolute_path))
        self.set_item(fake)

    def set_department(self, data: DepartmentInspectorData, status: DepartmentStatusData) -> None:
        fake = ViewItem(kind=ViewItemKind.DEPARTMENT, name=data.department_name, type_badge="Department", path=Path(data.publish_path))
        self.set_item(fake)


_V_RE = re.compile(r"^v(\d{3})$")


def _version_from_path(path: Path | None) -> str:
    """Rút version từ path: ưu tiên _v001 trong tên file (vd char_Zephy_01_sculpt_v002.blend → v002)."""
    if not path:
        return "—"
    name = path.name or ""
    # Tên file: ..._v002.blend hoặc ..._v002 hoặc ..._v002_fixEar.blend
    m = re.search(r"_v(\d{3})(?:_[^.]*)?(?:\.\w+)?$", name)
    if m:
        return f"v{m.group(1)}"
    # Folder tên v001, v002
    if _V_RE.match(name):
        return name
    # Trong đường dẫn có segment v001/v002 (vd .../publish/v002)
    for part in path.parts:
        if _V_RE.match(part):
            return part
    return "—"


def _path_for_version(
    item: ViewItem,
    active_department: str | None = None,
    active_dcc_id: str | None = None,
) -> Path | None:
    """
    Path dùng để rút version: ưu tiên path FILE work từ dcc_work_states.
    Khi active_department + active_dcc_id cho trước: chỉ xét state (dept, dcc) đó.
    Không thì lấy version cao nhất trong department; không có thì item.path.
    """
    ref = item.ref
    if not isinstance(ref, (Asset, Shot)):
        return item.path
    states = getattr(ref, "dcc_work_states", None) or ()
    paths_with_version: list[tuple[Path, int]] = []
    dep_key = (active_department or "").strip().casefold() if active_department else None
    dcc_key = (active_dcc_id or "").strip().casefold() if active_dcc_id else None
    for key_st in states:
        if not isinstance(key_st, (tuple, list)) or len(key_st) < 2:
            continue
        dept_id = (key_st[0][0] or "").strip().casefold() if isinstance(key_st[0], (tuple, list)) and len(key_st[0]) >= 1 else ""
        dcc_id = (key_st[0][1] or "").strip().casefold() if isinstance(key_st[0], (tuple, list)) and len(key_st[0]) >= 2 else ""
        if dep_key is not None and dept_id != dep_key:
            continue
        if dcc_key is not None and dcc_id != dcc_key:
            continue
        st = key_st[1]
        wp = getattr(st, "work_file_path", None)
        if wp and isinstance(wp, Path):
            ver_str = _version_from_path(wp)
            if ver_str != "—" and _V_RE.match(ver_str):
                paths_with_version.append((wp, int(_V_RE.match(ver_str).group(1))))
    if paths_with_version:
        best = max(paths_with_version, key=lambda pv: pv[1])
        return best[0]
    return item.path


def _description_from_work_path(path: Path | None) -> str:
    """Extract description suffix from work file path (e.g. prefix_v005_fixNecklace.ext -> fixNecklace)."""
    if not path:
        return ""
    stem = path.stem or ""
    m = re.search(r"_v\d{3}_(.*)", stem)
    if m:
        return m.group(1).strip()
    return ""


def _infer_latest_version_from_departments(depts: tuple[Department, ...]) -> str:
    best: int | None = None
    for d in depts:
        v = d.latest_publish_version or ""
        m = _V_RE.match(v)
        if not m:
            continue
        n = int(m.group(1))
        if best is None or n > best:
            best = n
    return f"v{best:03d}" if best is not None else "—"


def _status_from_department(dept: Department) -> str:
    if dept.publish_version_count > 0:
        return "READY"
    if dept.work_exists:
        return "PROGRESS"
    return "WAITING"


def _status_display_label(status: str) -> str:
    """Display label for department status in Inspector: READY→Published, PROGRESS→Working."""
    if status == "READY":
        return "Published"
    if status == "PROGRESS":
        return "Working"
    if status == "BLOCKED":
        return "Blocked"
    return "Waiting"


def _status_color(status: str) -> str:
    if status == "READY":
        return MONOS_COLORS["emerald_500"]
    if status == "PROGRESS":
        return MONOS_COLORS["amber_500"]
    if status == "BLOCKED":
        return MONOS_COLORS["red_500"]
    return MONOS_COLORS["waiting"]


class _InspectorHeader(QWidget):
    close_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorHeader")
        self._tabs_visible = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)

        self._title = QLabel("INSPECTOR", self)
        self._title.setObjectName("InspectorHeaderTitle")
        self._title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))

        self._tab_row = QWidget(self)
        self._tab_row.setObjectName("InspectorTabRow")
        tab_row_l = QHBoxLayout(self._tab_row)
        tab_row_l.setContentsMargins(0, 0, 0, 0)
        tab_row_l.setSpacing(8)

        # Reuse SidebarScopePill* objectNames + QSS (Project | Shot | Asset pill).
        self._tab_pill = QWidget(self._tab_row)
        self._tab_pill.setObjectName("SidebarScopePill")
        self._tab_pill.setAttribute(Qt.WA_StyledBackground, True)
        self._tab_pill.setMinimumHeight(40)
        self._tab_pill.setMaximumHeight(40)
        pill_l = QHBoxLayout(self._tab_pill)
        pill_l.setContentsMargins(4, 4, 4, 4)
        pill_l.setSpacing(0)

        self._tab_buttons: list[QToolButton] = []
        self._tab_icon_names: list[str] = []
        self._tab_tooltip_bases: list[str] = []
        self._tab_hotkey_ids = (
            "inspector.tab_pipeline",
            "inspector.tab_reference",
            "inspector.tab_details",
        )
        self._ref_tab_badge_count = 0
        self._inspector_settings: QSettings | None = None
        _tab_specs = (
            ("Pipeline — thumbnail and departments", "Pipeline", "layers"),
            ("Reference and concept folders", "Ref", "eye"),
            ("Technical specs and metadata", "Details", "sliders-horizontal"),
        )
        for idx, (tip, label, icon_name) in enumerate(_tab_specs):
            self._tab_tooltip_bases.append(tip)
            btn = QToolButton(self._tab_pill)
            btn.setObjectName("SidebarScopePillSegment")
            btn.setProperty(
                "position",
                "left" if idx == 0 else ("right" if idx == len(_tab_specs) - 1 else "center"),
            )
            btn.setProperty("active", "false")
            btn.setAutoRaise(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setText(label)
            f = monos_font("Inter", 13, QFont.Weight.DemiBold)
            f.setLetterSpacing(QFont.PercentageSpacing, 97)
            btn.setFont(f)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            ic = lucide_icon(icon_name, size=15, color_hex=MONOS_COLORS["pill_segment_inactive_fg"])
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(15, 15))
            btn.setFixedHeight(32)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._tab_icon_names.append(icon_name)
            self._tab_buttons.append(btn)
            pill_l.addWidget(btn, 0, Qt.AlignVCenter)

        tab_row_l.addWidget(self._tab_pill, 0, Qt.AlignLeft | Qt.AlignVCenter)
        tab_row_l.addStretch(1)
        self._ref_tab_base_tooltip = "Reference and concept folders"
        self.sync_tab_hotkey_tooltips(None)

        self._tab_row.setVisible(True)
        self._title.setVisible(False)
        self.setProperty("tabMode", "true")
        self._active_tab_index = 0

        self._close_btn = QToolButton(self)
        self._close_btn.setObjectName("InspectorCloseButton")
        self._close_btn.setAutoRaise(True)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setIcon(lucide_icon("x", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._close_btn.clicked.connect(self.close_clicked.emit)

        layout.addWidget(self._title, 0, Qt.AlignVCenter)
        layout.addWidget(self._tab_row, 1, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self._close_btn, 0, Qt.AlignVCenter)

    @property
    def tab_buttons(self) -> list[QToolButton]:
        return self._tab_buttons

    def tabs_visible(self) -> bool:
        return self._tabs_visible

    def set_tabs_visible(self, visible: bool) -> None:
        self._tabs_visible = bool(visible)
        self._title.setVisible(not visible)
        self._tab_row.setVisible(visible)
        self.setProperty("tabMode", "true" if visible else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_active_tab(self, index: int) -> None:
        index = max(0, min(len(self._tab_buttons) - 1, int(index)))
        self._active_tab_index = index
        for i, btn in enumerate(self._tab_buttons):
            is_active = i == index
            btn.setProperty("active", "true" if is_active else "false")
            icon_name = self._tab_icon_names[i] if i < len(self._tab_icon_names) else "layers"
            if is_active:
                color = MONOS_COLORS.get("pill_segment_subtle_active_icon_fg", MONOS_COLORS["blue_400"])
            else:
                color = MONOS_COLORS["pill_segment_inactive_fg"]
            ic = lucide_icon(icon_name, size=15, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_inspector_settings(self, settings: QSettings | None) -> None:
        self._inspector_settings = settings
        self.sync_tab_hotkey_tooltips(settings)

    def sync_tab_hotkey_tooltips(self, settings: QSettings | None) -> None:
        from monostudio.ui_qt.app_hotkeys import tooltip_with_hotkey

        settings = settings if settings is not None else self._inspector_settings
        for idx, btn in enumerate(self._tab_buttons):
            if idx >= len(self._tab_tooltip_bases) or idx >= len(self._tab_hotkey_ids):
                continue
            base = self._tab_tooltip_bases[idx]
            if idx == 1 and self._ref_tab_badge_count > 0:
                n = str(self._ref_tab_badge_count) if self._ref_tab_badge_count <= 99 else "99+"
                word = "file" if self._ref_tab_badge_count == 1 else "files"
                base = f"{self._ref_tab_base_tooltip} — {n} preview {word}"
            btn.setToolTip(tooltip_with_hotkey(base, settings, self._tab_hotkey_ids[idx]))

    def set_ref_tab_badge(self, count: int) -> None:
        self._ref_tab_badge_count = max(0, int(count))
        self.sync_tab_hotkey_tooltips(self._inspector_settings)


class _InspectorEmptyState(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorEmpty")
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 80, 0, 0)
        l.setSpacing(8)
        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._label.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        l.addWidget(self._label, 0)
        l.addStretch(1)

    def set_message(self, text: str) -> None:
        self._label.setText(text)


class _PreviewWidget(QWidget):
    context_menu_requested = Signal(object)  # emits QPoint (global)
    image_changed = Signal(bool)  # has_image
    health_chip_clicked = Signal()
    notes_chip_clicked = Signal()

    # Inbox: tỉ lệ theo ảnh input; Asset/Shot: 16:9
    INBOX_PREVIEW_MIN_HEIGHT = 120
    INBOX_PREVIEW_MAX_HEIGHT = 720

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorPreview")
        self.setMouseTracking(True)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._pix: QPixmap | None = None
        self._has_image = False
        self._placeholder_kind: str = ""  # "asset" | "shot" | "project" for icon; else letter
        self._placeholder_letter: str = ""
        self._placeholder_file_icon: tuple[str, str] = ()  # (icon_name, color_hex) for Inbox file type
        self._inbox_mode = False  # True = Inbox (tỉ lệ theo ảnh), False = 16:9 (Asset/Shot)
        self._display_fit = False  # from path (user thumb); only used when no user override
        self._user_fit = False  # user toggle: False = fill (default), True = fit
        self._loading = False  # True = đang load thumb (hiện spinner như Explorer)
        self._loading_angle = 0.0  # độ (0–360) để vẽ icon quay
        self._loading_timer: QTimer | None = None
        self._unreadable_ext: str = ""  # e.g. ".EXR" (JetBrains Mono, green)
        self._unreadable_hint: str = ""  # Inter, muted (word wrap)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self._item_health: ItemHealth | None = None
        self._health_hovered = False
        self._notes_chip_visible = False
        self._notes_open_count = 0
        self._notes_visual_mode = "empty"
        self._notes_hovered = False
        # Version badge (always visible when a work version is known).
        self._version_badge_text: str = ""
        self._version_badge_bg: QColor | None = None
        self._version_badge_tooltip: str = ""
        self._version_badge_rect: QRect | None = None

    def set_version_badge(self, *, text: str, bg: QColor | None, tooltip: str = "") -> None:
        t = (text or "").strip()
        tip = (tooltip or "").strip()
        same_bg = (self._version_badge_bg == bg) if (self._version_badge_bg is not None or bg is not None) else True
        if self._version_badge_text == t and same_bg and self._version_badge_tooltip == tip:
            return
        self._version_badge_text = t
        self._version_badge_bg = bg
        self._version_badge_tooltip = tip
        self._version_badge_rect = None
        # Clear tooltip unless we are actively hovering the badge.
        if not t:
            self.setToolTip("")
        self.update()

    def get_user_fit(self) -> bool:
        return self._user_fit

    def set_user_fit(self, fit: bool) -> None:
        if self._user_fit == fit:
            return
        self._user_fit = fit
        self.update()

    def set_pixmap(self, pix: QPixmap | None, *, use_fit: bool = False) -> None:
        self._pix = pix
        self._has_image = bool(pix and not pix.isNull())
        self._display_fit = bool(use_fit)
        if self._has_image:
            self._unreadable_ext = ""
            self._unreadable_hint = ""
            self._placeholder_file_icon = ()
        if self._inbox_mode:
            self.updateGeometry()
        self.image_changed.emit(self._has_image)
        self.update()

    def set_unreadable_preview(self, ext_display: str, hint: str) -> None:
        ext = (ext_display or "").strip()
        h = (hint or "").strip()
        if ext:
            self._placeholder_file_icon = ()
        if self._unreadable_ext == ext and self._unreadable_hint == h:
            return
        self._unreadable_ext = ext
        self._unreadable_hint = h
        self.update()

    def set_placeholder_kind(self, kind: str, *, letter: str = "") -> None:
        self._placeholder_kind = (kind or "").strip().lower()
        self._placeholder_letter = (letter or "").strip()[:1].upper()
        self._placeholder_file_icon = ()
        self.updateGeometry()
        self.update()

    def set_placeholder_file_icon(self, icon_name: str, color_hex: str) -> None:
        """Inbox: hiển thị icon theo loại file (folder, file-text, box/DCC, …) khi không có thumbnail."""
        self._placeholder_file_icon = ((icon_name or "file").strip(), (color_hex or "").strip())
        self.update()

    def set_loading(self, loading: bool) -> None:
        """Bật/tắt trạng thái loading (spinner quay) khi load thumb nặng."""
        if self._loading == loading:
            return
        self._loading = loading
        if loading:
            if self._loading_timer is None:
                self._loading_timer = QTimer(self)
                self._loading_timer.timeout.connect(self._on_loading_tick)
            self._loading_angle = 0.0
            self._loading_timer.start(50)
        else:
            if self._loading_timer is not None:
                self._loading_timer.stop()
        self.update()

    def _on_loading_tick(self) -> None:
        if not self._loading:
            return
        self._loading_angle = (self._loading_angle + 30.0) % 360.0
        self.update()

    def set_placeholder_letter(self, letter: str) -> None:
        self._placeholder_letter = (letter or "").strip()[:1].upper()
        self.update()

    def set_inbox_mode(self, on: bool) -> None:
        """Inbox: tỉ lệ theo ảnh input (heightForWidth từ pixmap); Asset/Shot: 16:9."""
        if self._inbox_mode == on:
            return
        self._inbox_mode = on
        if on:
            policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            policy.setHeightForWidth(True)
            self.setSizePolicy(policy)
            self.setMinimumHeight(self.INBOX_PREVIEW_MIN_HEIGHT)
        else:
            policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            policy.setHeightForWidth(True)
            self.setSizePolicy(policy)
            self.setMinimumHeight(0)
        self.updateGeometry()

    def set_item_health(self, health: ItemHealth | None) -> None:
        if self._item_health == health:
            return
        self._item_health = health
        if health is None:
            self._health_hovered = False
            if not self._notes_hovered:
                self.unsetCursor()
        self.update()

    def _health_chip_rect(self, r: QRect) -> QRect:
        chip = _THUMB_HEALTH_ICON_PX + _THUMB_HEALTH_CHIP_PAD_PX * 2
        return QRect(r.right() - 12 - chip, r.top() + 12, chip, chip)

    def _health_hit(self, pos: QPoint) -> bool:
        if self._item_health is None:
            return False
        return self._health_chip_rect(self.rect()).contains(pos)

    def _draw_health_chip(self, p: QPainter, r: QRect) -> None:
        """Item health icon top-right (same as main view grid cards)."""
        health = self._item_health
        if health is None:
            return
        chip_rect = self._health_chip_rect(r)
        health_hover = self._health_hovered
        chip_bg = QColor(0, 0, 0, 220 if health_hover else 168)
        p.save()
        if health_hover:
            ring = QColor(health.color_hex)
            ring.setAlpha(230)
            p.setPen(QPen(ring, 2))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(chip_bg)
        p.drawEllipse(chip_rect)
        icon_px = _THUMB_HEALTH_ICON_PX + (2 if health_hover else 0)
        icon = lucide_icon(health.icon_name, size=icon_px, color_hex=health.color_hex)
        pix = icon.pixmap(icon_px, icon_px)
        if not pix.isNull():
            pad = max(2, _THUMB_HEALTH_CHIP_PAD_PX - (1 if health_hover else 0))
            dest = chip_rect.adjusted(pad, pad, -pad, -pad)
            p.drawPixmap(dest, pix)
        p.restore()

    def set_notes_chip(self, visible: bool, open_count: int = 0, visual_mode: str = "empty") -> None:
        vis = bool(visible)
        oc = max(0, int(open_count))
        vm = visual_mode if visual_mode in ("empty", "open", "all_done") else "empty"
        if vis == self._notes_chip_visible and oc == self._notes_open_count and vm == self._notes_visual_mode:
            return
        self._notes_chip_visible = vis
        self._notes_open_count = oc
        self._notes_visual_mode = vm
        if not vis:
            self._notes_hovered = False
            if not self._health_hovered:
                self.unsetCursor()
        self.update()

    def _note_chip_rect(self, r: QRect) -> QRect:
        hr = self._health_chip_rect(r) if self._item_health is not None else None
        return _thumb_note_chip_rect(r, hr)

    def _note_hit(self, pos: QPoint) -> bool:
        if not self._notes_chip_visible:
            return False
        return self._note_chip_rect(self.rect()).contains(pos)

    def _draw_thumb_overlay_chips(self, p: QPainter, r: QRect) -> None:
        if self._notes_chip_visible:
            p.save()
            try:
                p.setRenderHint(QPainter.Antialiasing, True)
                note_r = self._note_chip_rect(r)
                paint_note_icon_chip(
                    p,
                    note_r,
                    self._notes_open_count,
                    visual_mode=self._notes_visual_mode,
                    hovered=self._notes_hovered,
                )
            finally:
                p.restore()
        self._draw_health_chip(p, r)

    def heightForWidth(self, w: int) -> int:  # type: ignore[override]
        if self._inbox_mode:
            # Tỉ lệ theo ảnh đã load; chưa có ảnh thì dùng 16:9
            if self._has_image and self._pix is not None and self._pix.width() > 0:
                h = int(w * self._pix.height() / self._pix.width())
                return max(self.INBOX_PREVIEW_MIN_HEIGHT, min(h, self.INBOX_PREVIEW_MAX_HEIGHT))
            return max(self.INBOX_PREVIEW_MIN_HEIGHT, min(int(w * 9 / 16), self.INBOX_PREVIEW_MAX_HEIGHT))
        # Asset: 1:1; Shot/Project: 16:9
        if self._placeholder_kind == "asset":
            return max(1, w)
        return max(1, int(w * 9 / 16))

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return True

    def sizeHint(self) -> QSize:  # type: ignore[override]
        if self._inbox_mode:
            w = 320
            h = self.heightForWidth(w)
            return QSize(w, h)
        # Asset: 1:1; Shot/Project: 16:9
        if self._placeholder_kind == "asset":
            return QSize(320, 320)
        return QSize(320, 180)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)

            r = self.rect()
            radius = 8
            path = QPainterPath()
            path.addRoundedRect(r, radius, radius)
            p.setClipPath(path)

            # Background
            p.fillRect(r, QColor(MONOS_COLORS["content_bg"]))

            # Loading spinner: icon loader-2 quay tròn
            if self._loading:
                icon = lucide_icon("loader-2", size=40, color_hex=MONOS_COLORS["text_meta"])
                src = icon.pixmap(40, 40)
                if not src.isNull():
                    cx = r.x() + r.width() // 2
                    cy = r.y() + r.height() // 2
                    p.save()
                    p.translate(cx, cy)
                    p.rotate(self._loading_angle)
                    p.translate(-20, -20)
                    p.drawPixmap(0, 0, src)
                    p.restore()
                return

            if self._has_image and self._pix is not None:
                use_fit = self._inbox_mode or self._user_fit
                dpr = self.devicePixelRatioF()
                target = QSize(round(r.width() * dpr), round(r.height() * dpr))
                if use_fit:
                    scaled = self._pix.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    scaled.setDevicePixelRatio(dpr)
                    lw = scaled.width() / dpr
                    lh = scaled.height() / dpr
                    x = r.x() + int((r.width() - lw) / 2)
                    y = r.y() + int((r.height() - lh) / 2)
                    p.drawPixmap(x, y, scaled)
                else:
                    scaled = self._pix.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    pw = round(r.width() * dpr)
                    ph = round(r.height() * dpr)
                    sx = max(0, (scaled.width() - pw) // 2)
                    sy = max(0, (scaled.height() - ph) // 2)
                    crop = scaled.copy(sx, sy, pw, ph)
                    crop.setDevicePixelRatio(dpr)
                    p.drawPixmap(r, crop)
                self._draw_thumb_overlay_chips(p, r)
                # Version badge (color encodes fresh vs old).
                if self._version_badge_text and self._version_badge_bg is not None:
                    pad = 10
                    f = monos_font("Inter", 10, QFont.Weight.Bold)
                    p.save()
                    p.setFont(f)
                    fm = p.fontMetrics()
                    text = self._version_badge_text.strip().upper()
                    # Add a small "film" icon to clarify this is preview version.
                    icon_size = 12
                    icon_gap = 6
                    play_icon = lucide_icon("film", size=icon_size, color_hex="#ffffff")
                    play_pix = play_icon.pixmap(icon_size, icon_size)
                    tw = fm.horizontalAdvance(text) + (icon_size + icon_gap if not play_pix.isNull() else 0)
                    th = fm.height()
                    pill_pad_x = 10
                    pill_pad_y = 4
                    pill_w = tw + pill_pad_x * 2
                    pill_h = th + pill_pad_y * 2
                    x = r.left() + pad
                    y = r.bottom() - pad - pill_h
                    rect = QRect(x, y, pill_w, pill_h)
                    self._version_badge_rect = rect
                    bg = QColor(self._version_badge_bg)
                    bg.setAlpha(190)
                    border = QColor(255, 255, 255, 64)
                    p.setPen(QPen(border, 1))
                    p.setBrush(bg)
                    p.drawRoundedRect(rect, 8, 8)
                    p.setPen(QColor("#ffffff"))
                    # Draw icon + text centered as a group.
                    content_w = fm.horizontalAdvance(text) + (icon_size + icon_gap if not play_pix.isNull() else 0)
                    start_x = rect.center().x() - (content_w // 2)
                    if not play_pix.isNull():
                        iy = rect.center().y() - (icon_size // 2)
                        p.drawPixmap(start_x, iy, play_pix)
                        start_x += icon_size + icon_gap
                    text_rect = QRect(start_x, rect.top(), rect.right() - start_x, rect.height())
                    p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
                    p.restore()
                return

            # Placeholder: icon by kind (asset/shot/project) or letter/em-dash
            p.setClipping(False)
            p.setPen(QPen(QColor(MONOS_COLORS["border"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0, 0, -1, -1), radius, radius)

            if self._unreadable_ext:
                p.fillRect(r, QColor(167, 243, 208, 20))
                margin = 22
                inner = r.adjusted(margin, margin, -margin, -margin)
                f_ext = monos_font("JetBrains Mono", 22, QFont.Weight.DemiBold)
                f_hint = monos_font("Inter", 11, QFont.Weight.Medium)
                fm_ext = QFontMetrics(f_ext)
                gap = 8
                hint_h = 0
                if self._unreadable_hint:
                    fm_hint = QFontMetrics(f_hint)
                    hint_rect_probe = QRect(inner.left(), inner.top(), inner.width(), inner.height())
                    hint_h = fm_hint.boundingRect(
                        hint_rect_probe,
                        Qt.AlignHCenter | Qt.TextWordWrap,
                        self._unreadable_hint,
                    ).height()
                total_h = fm_ext.height() + (gap + hint_h if self._unreadable_hint else 0)
                y0 = inner.top() + max(0, (inner.height() - total_h) // 2)
                p.setFont(f_ext)
                p.setPen(QColor("#a7f3d0"))
                ext_rect = QRect(inner.left(), y0, inner.width(), fm_ext.height())
                p.drawText(ext_rect, Qt.AlignHCenter | Qt.AlignTop, self._unreadable_ext)
                if self._unreadable_hint:
                    p.setFont(f_hint)
                    p.setPen(QColor(MONOS_COLORS["text_meta"]))
                    top_hint = y0 + fm_ext.height() + gap
                    hint_draw = QRect(inner.left(), top_hint, inner.width(), max(1, inner.bottom() - top_hint))
                    p.drawText(
                        hint_draw,
                        Qt.AlignHCenter | Qt.TextWordWrap | Qt.AlignTop,
                        self._unreadable_hint,
                    )
                self._draw_thumb_overlay_chips(p, r)
                return

            if self._placeholder_kind in ("asset", "shot", "project"):
                icon_name = "box" if self._placeholder_kind == "asset" else "clapperboard" if self._placeholder_kind == "shot" else "layout-dashboard"
                icon = lucide_icon(icon_name, size=64, color_hex=MONOS_COLORS["text_meta"])
                src = icon.pixmap(64, 64)
                if not src.isNull():
                    x = r.x() + (r.width() - 64) // 2
                    y = r.y() + (r.height() - 64) // 2
                    p.drawPixmap(x, y, src)
                self._draw_thumb_overlay_chips(p, r)
                return

            if self._placeholder_file_icon:
                icon_name, color_hex = self._placeholder_file_icon
                color = color_hex or MONOS_COLORS["text_meta"]
                if icon_name.startswith("brand:"):
                    icon = brand_icon(icon_name[6:], size=64, color_hex=color)
                    if icon.isNull():
                        icon = lucide_icon("box", size=64, color_hex=color)
                else:
                    icon = lucide_icon(icon_name, size=64, color_hex=color)
                src = icon.pixmap(64, 64)
                if not src.isNull():
                    x = r.x() + (r.width() - 64) // 2
                    y = r.y() + (r.height() - 64) // 2
                    p.drawPixmap(x, y, src)
                self._draw_thumb_overlay_chips(p, r)
                return

            if self._placeholder_letter:
                p.setPen(QColor(MONOS_COLORS["text_meta"]))
                f = monos_font("Inter", 28, QFont.Weight.DemiBold)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, self._placeholder_letter)
                self._draw_thumb_overlay_chips(p, r)
                return

            p.setPen(QColor(MONOS_COLORS["text_meta"]))
            f = monos_font("Inter", 11, QFont.Weight.DemiBold)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, "—")
        finally:
            p.end()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        try:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        except Exception:
            pos = None
        if pos is not None:
            note_hover = self._note_hit(pos)
            health_hover = self._health_hit(pos)
            if note_hover != self._notes_hovered or health_hover != self._health_hovered:
                self._notes_hovered = note_hover
                self._health_hovered = health_hover
                if note_hover or health_hover:
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    self.unsetCursor()
                self.update()
            gpt = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
            if note_hover:
                QToolTip.showText(
                    gpt,
                    notes_badge_tooltip_text(self._notes_open_count, self._notes_visual_mode),
                )
            elif health_hover and self._item_health is not None:
                QToolTip.showText(gpt, _item_health_tooltip_text(self._item_health))
        rect = self._version_badge_rect
        if (
            pos is not None
            and rect is not None
            and self._version_badge_tooltip
            and rect.contains(pos)
        ):
            self.setToolTip(self._version_badge_tooltip)
        elif pos is None or (not self._health_hit(pos) and not self._note_hit(pos)):
            if self.toolTip():
                self.setToolTip("")
        return super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.clear_transient_hover_states()
        super().leaveEvent(event)

    def clear_transient_hover_states(self) -> None:
        if not (self._health_hovered or self._notes_hovered):
            return
        self._health_hovered = False
        self._notes_hovered = False
        self.unsetCursor()
        if self.toolTip():
            self.setToolTip("")
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            try:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            except Exception:
                pos = None
            if pos is not None and self._note_hit(pos):
                self.notes_chip_clicked.emit()
                event.accept()
                return
            if pos is not None and self._health_hit(pos):
                self.health_chip_clicked.emit()
                event.accept()
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        try:
            gp = event.globalPos()
        except Exception:
            gp = QPoint(0, 0)
        self.context_menu_requested.emit(gp)


def _thumb_button_style() -> str:
    return (
        "QToolButton { border: none; border-radius: 22px; background: rgba(0,0,0,0.35); } "
        "QToolButton:hover { background: rgba(0,0,0,0.55); } "
        "QToolButton:disabled { background: rgba(0,0,0,0.18); }"
    )


class _PreviewContainer(QWidget):
    """Container for thumbnail with Fill/Fit, Paste and Remove buttons at top-left."""
    paste_requested = Signal()
    remove_requested = Signal()
    sequence_play_clicked = Signal()

    _THUMB_BTN_MARGIN = 8
    _THUMB_BTN_GAP = 4
    _THUMB_BTN_SIZE = 44
    _INFO_BTN_SIZE = 32

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hovered = False
        self._pending_action: str | None = None  # "paste" | "remove" | None
        self._preview_help_text: str = ""
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._container_layout = QVBoxLayout(self)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)
        self._w = _PreviewWidget(self)
        self._w.installEventFilter(self)
        self._container_layout.addWidget(self._w, 0)
        self._inbox_mode = False
        self._show_fill_fit = False
        self._show_remove = False
        self._render_sequence_hide_controls = False

        self._btn_fill_fit = QToolButton(self)
        self._btn_fill_fit.setMouseTracking(True)
        self._btn_fill_fit.setCursor(Qt.PointingHandCursor)
        self._btn_fill_fit.setIconSize(QSize(24, 24))
        self._btn_fill_fit.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_fill_fit.setStyleSheet(_thumb_button_style())
        self._btn_fill_fit.clicked.connect(self._on_fill_fit_clicked)
        self._update_fill_fit_icon()
        self._btn_fill_fit.setVisible(False)
        self._btn_fill_fit.installEventFilter(self)

        self._btn_paste = QToolButton(self)
        self._btn_paste.setMouseTracking(True)
        self._btn_paste.setCursor(Qt.PointingHandCursor)
        self._btn_paste.setToolTip("Paste thumbnail from clipboard")
        self._btn_paste.setIcon(lucide_icon("clipboard-paste", size=20, color_hex=MONOS_COLORS["text_label"]))
        self._btn_paste.setIconSize(QSize(24, 24))
        self._btn_paste.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_paste.setStyleSheet(_thumb_button_style())
        self._btn_paste.clicked.connect(self._on_paste_clicked)
        self._btn_paste.setVisible(False)
        self._btn_paste.installEventFilter(self)

        self._btn_remove = QToolButton(self)
        self._btn_remove.setMouseTracking(True)
        self._btn_remove.setCursor(Qt.PointingHandCursor)
        self._btn_remove.setToolTip("Remove thumbnail")
        self._btn_remove.setIcon(lucide_icon("trash-2", size=20, color_hex=MONOS_COLORS["text_label"]))
        self._btn_remove.setIconSize(QSize(24, 24))
        self._btn_remove.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_remove.setStyleSheet(_thumb_button_style())
        self._btn_remove.clicked.connect(self._on_remove_clicked)
        self._btn_remove.setVisible(False)
        self._btn_remove.installEventFilter(self)

        # Confirm / cancel buttons (top-right) — appear only after paste/remove is requested.
        self._btn_confirm = QToolButton(self)
        self._btn_confirm.setMouseTracking(True)
        self._btn_confirm.setCursor(Qt.PointingHandCursor)
        self._btn_confirm.setToolTip("Apply thumbnail change")
        self._btn_confirm.setIcon(
            lucide_icon("square-check", size=20, color_hex=MONOS_COLORS["emerald_500"])
        )
        self._btn_confirm.setIconSize(QSize(24, 24))
        self._btn_confirm.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_confirm.setStyleSheet(_thumb_button_style())
        self._btn_confirm.clicked.connect(self._on_confirm_clicked)
        self._btn_confirm.setVisible(False)
        self._btn_confirm.installEventFilter(self)

        self._btn_cancel = QToolButton(self)
        self._btn_cancel.setMouseTracking(True)
        self._btn_cancel.setCursor(Qt.PointingHandCursor)
        self._btn_cancel.setToolTip("Cancel thumbnail change")
        self._btn_cancel.setIcon(
            lucide_icon("x", size=20, color_hex=MONOS_COLORS["red_500"])
        )
        self._btn_cancel.setIconSize(QSize(24, 24))
        self._btn_cancel.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_cancel.setStyleSheet(_thumb_button_style())
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)
        self._btn_cancel.setVisible(False)
        self._btn_cancel.installEventFilter(self)

        self._btn_seq_play = QToolButton(self)
        self._btn_seq_play.setMouseTracking(True)
        self._btn_seq_play.setCursor(Qt.PointingHandCursor)
        self._btn_seq_play.setIconSize(QSize(24, 24))
        self._btn_seq_play.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_seq_play.setStyleSheet(_thumb_button_style())
        self._btn_seq_play.setIcon(lucide_icon("play", size=24, color_hex=MONOS_COLORS["text_label"]))
        self._btn_seq_play.setToolTip("Play sequence")
        self._btn_seq_play.clicked.connect(self.sequence_play_clicked.emit)
        self._btn_seq_play.setVisible(False)
        self._btn_seq_play.installEventFilter(self)
        self._sequence_play_available = False

        self._btn_info = QToolButton(self)
        self._btn_info.setMouseTracking(True)
        self._btn_info.setCursor(Qt.PointingHandCursor)
        self._btn_info.setIcon(lucide_icon("circle-help", size=18, color_hex=MONOS_COLORS["text_label"]))
        self._btn_info.setIconSize(QSize(20, 20))
        self._btn_info.setFixedSize(self._INFO_BTN_SIZE, self._INFO_BTN_SIZE)
        self._btn_info.setStyleSheet(_thumb_button_style())
        self._btn_info.setToolTip("")
        self._btn_info.setVisible(False)
        self._btn_info.clicked.connect(self._on_preview_info_clicked)
        self._btn_info.installEventFilter(self)

        self._w.image_changed.connect(self._on_preview_image_changed)

        # Mouse-move fallback scoped to Inspector scroll viewport (not whole app).
        self._viewport: QWidget | None = None
        p = self.parent()
        while p is not None and not isinstance(p, QScrollArea):
            p = p.parent()
        if isinstance(p, QScrollArea):
            self._viewport = p.viewport()
            if self._viewport is not None:
                self._viewport.installEventFilter(self)

    def _update_fill_fit_icon(self) -> None:
        fit = self._w.get_user_fit()
        if fit:
            self._btn_fill_fit.setIcon(lucide_icon("crop", size=20, color_hex=MONOS_COLORS["text_label"]))
            self._btn_fill_fit.setToolTip("Fill (crop to fill)")
        else:
            self._btn_fill_fit.setIcon(lucide_icon("maximize-2", size=20, color_hex=MONOS_COLORS["text_label"]))
            self._btn_fill_fit.setToolTip("Fit (show full image)")

    def _on_fill_fit_clicked(self) -> None:
        self._w.set_user_fit(not self._w.get_user_fit())
        self._update_fill_fit_icon()

    def _begin_pending_action(self, action: str) -> None:
        # action: "paste" or "remove"
        if action not in ("paste", "remove"):
            return
        self._pending_action = action
        # Ensure confirm/cancel visible while hovered; main buttons remain for context.
        if self._hovered:
            self._btn_confirm.setVisible(True)
            self._btn_cancel.setVisible(True)
        else:
            self._btn_confirm.setVisible(False)
            self._btn_cancel.setVisible(False)

    def _on_paste_clicked(self) -> None:
        # Stage paste; user must confirm to actually emit paste_requested.
        self._begin_pending_action("paste")

    def _on_remove_clicked(self) -> None:
        # Stage remove; user must confirm to actually emit remove_requested.
        self._begin_pending_action("remove")

    def _on_confirm_clicked(self) -> None:
        action = self._pending_action
        self._pending_action = None
        self._btn_confirm.setVisible(False)
        self._btn_cancel.setVisible(False)
        if action == "paste":
            self.paste_requested.emit()
        elif action == "remove":
            self.remove_requested.emit()

    def _on_cancel_clicked(self) -> None:
        # Drop pending state, keep current thumbnail as-is.
        self._pending_action = None
        self._btn_confirm.setVisible(False)
        self._btn_cancel.setVisible(False)

    def set_preview_help_text(self, text: str) -> None:
        """Multi-line hints shown when the user clicks the corner info button (not on hover over the thumb)."""
        t = (text or "").strip()
        self._preview_help_text = t
        self._btn_info.setVisible(bool(t))
        self._layout_thumb_overlay_buttons()

    def _on_preview_info_clicked(self) -> None:
        t = (self._preview_help_text or "").strip()
        if not t:
            return
        gp = self._btn_info.mapToGlobal(self._btn_info.rect().bottomLeft() + QPoint(0, 4))
        QToolTip.showText(gp, t, self._btn_info, QRect(), 20000)

    def _on_preview_image_changed(self, has_image: bool) -> None:
        show = not self._inbox_mode and has_image
        self.set_show_fill_fit(show)
        self.set_show_remove(show)

    def _layout_thumb_overlay_buttons(self) -> None:
        r = self._w.geometry()
        margin = self._THUMB_BTN_MARGIN
        gap = self._THUMB_BTN_GAP
        size = self._THUMB_BTN_SIZE
        x0 = r.x() + margin
        y0 = r.y() + margin
        self._btn_fill_fit.move(x0, y0)
        self._btn_fill_fit.raise_()
        self._btn_paste.move(x0 + size + gap, y0)
        self._btn_paste.raise_()
        self._btn_remove.move(x0 + (size + gap) * 2, y0)
        self._btn_remove.raise_()
        x_right = r.x() + r.width() - margin - size
        self._btn_confirm.move(x_right, y0)
        self._btn_confirm.raise_()
        self._btn_cancel.move(x_right - (size + gap), y0)
        self._btn_cancel.raise_()
        bx = r.x() + (r.width() - size) // 2
        by = r.y() + (r.height() - size) // 2
        self._btn_seq_play.move(bx, by)
        self._btn_seq_play.raise_()
        isz = self._INFO_BTN_SIZE
        self._btn_info.move(r.x() + r.width() - margin - isz, r.y() + r.height() - margin - isz)
        if self._btn_info.isVisible():
            self._btn_info.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_thumb_overlay_buttons()

    def _apply_sequence_play_visibility(self) -> None:
        if not self._sequence_play_available:
            self._btn_seq_play.setVisible(False)
            return
        self._btn_seq_play.setVisible(bool(self._hovered))

    def update_sequence_play_control(self, *, available: bool, playing: bool) -> None:
        """Có sequence thì bật nút play/pause (hiện khi hover); icon theo trạng thái phát."""
        self._sequence_play_available = bool(available)
        if not available:
            self._btn_seq_play.setVisible(False)
            return
        if playing:
            self._btn_seq_play.setIcon(lucide_icon("pause", size=24, color_hex=MONOS_COLORS["text_label"]))
            self._btn_seq_play.setToolTip("Pause sequence")
        else:
            self._btn_seq_play.setIcon(lucide_icon("play", size=24, color_hex=MONOS_COLORS["text_label"]))
            self._btn_seq_play.setToolTip("Play sequence")
        self._apply_sequence_play_visibility()

    def set_paste_enabled(self, enabled: bool) -> None:
        on = bool(enabled)
        self._btn_paste.setEnabled(on)
        self._btn_paste.setVisible(self._hovered and on)

    def set_show_fill_fit(self, show: bool) -> None:
        self._show_fill_fit = bool(show)
        self._btn_fill_fit.setVisible(self._hovered and self._show_fill_fit and not self._render_sequence_hide_controls)
        if self._show_fill_fit:
            self._update_fill_fit_icon()

    def set_show_remove(self, show: bool) -> None:
        self._show_remove = bool(show)
        self._btn_remove.setVisible(self._hovered and self._show_remove and not self._render_sequence_hide_controls)

    def set_render_sequence_hide_controls(self, hide: bool) -> None:
        """When True (Settings: render sequence only), hide paste/fill/remove; play vẫn theo hover."""
        self._render_sequence_hide_controls = bool(hide)
        if hide:
            self._pending_action = None
            self._btn_fill_fit.setVisible(False)
            self._btn_paste.setVisible(False)
            self._btn_remove.setVisible(False)
            self._btn_confirm.setVisible(False)
            self._btn_cancel.setVisible(False)
        self._set_hovered(self._hovered)

    def set_inbox_mode(self, on: bool) -> None:
        """Inbox: preview widget tự quyết height theo heightForWidth (tỉ lệ ảnh)."""
        self._inbox_mode = bool(on)
        self._container_layout.setStretchFactor(self._w, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        show = not self._inbox_mode and self._w._has_image
        self.set_show_fill_fit(show)
        self.set_show_remove(show)
        self.updateGeometry()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        # Only hide when mouse truly leaves the whole overlay area (image + buttons).
        if not self._any_under_mouse():
            self._set_hovered(False)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        # Fallback: some paths into the preview area don't reliably trigger Enter events.
        # Mouse move is always delivered when tracking is enabled.
        if self._any_under_mouse():
            self._set_hovered(True)
        else:
            self._set_hovered(False)
        super().mouseMoveEvent(event)

    def _any_under_mouse(self) -> bool:
        return bool(
            self.underMouse()
            or self._w.underMouse()
            or self._btn_fill_fit.underMouse()
            or self._btn_paste.underMouse()
            or self._btn_remove.underMouse()
            or self._btn_confirm.underMouse()
            or self._btn_cancel.underMouse()
            or self._btn_seq_play.underMouse()
            or self._btn_info.underMouse()
        )

    def _cursor_in_hover_region(self) -> bool:
        gp = QCursor.pos()
        for w in (
            self._w,
            self._btn_fill_fit,
            self._btn_paste,
            self._btn_remove,
            self._btn_confirm,
            self._btn_cancel,
            self._btn_seq_play,
            self._btn_info,
        ):
            if not w.isVisible():
                continue
            try:
                lp = w.mapFromGlobal(gp)
            except Exception:
                continue
            if w.rect().contains(lp):
                return True
        return False

    def _set_hovered(self, on: bool) -> None:
        self._hovered = bool(on)
        if self._render_sequence_hide_controls:
            self._btn_fill_fit.setVisible(False)
            self._btn_paste.setVisible(False)
            self._btn_remove.setVisible(False)
            self._btn_confirm.setVisible(False)
            self._btn_cancel.setVisible(False)
        elif not self._hovered:
            self._btn_fill_fit.setVisible(False)
            self._btn_paste.setVisible(False)
            self._btn_remove.setVisible(False)
            self._btn_confirm.setVisible(False)
            self._btn_cancel.setVisible(False)
        else:
            if self._show_fill_fit:
                self._btn_fill_fit.setVisible(True)
            if self._btn_paste.isEnabled():
                self._btn_paste.setVisible(True)
            if self._show_remove:
                self._btn_remove.setVisible(True)
            if self._pending_action is not None:
                self._btn_confirm.setVisible(True)
                self._btn_cancel.setVisible(True)
        self._apply_sequence_play_visibility()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        # Hover có thể di chuyển giữa preview widget, các nút overlay và khoảng trống trong Inspector viewport.
        # - watched == viewport: giữ hover state ổn định khi rê chậm từ khoảng trống bên trái.
        # - watched == preview/buttons: gom thành một vùng hover duy nhất để tránh flicker.
        try:
            et = event.type()
        except Exception:
            return super().eventFilter(watched, event)

        if watched is self._viewport:
            if et == QEvent.Type.MouseMove:
                if self.isVisible() and self._cursor_in_hover_region():
                    self._set_hovered(True)
                elif self._hovered and not self._cursor_in_hover_region():
                    self._set_hovered(False)
            return super().eventFilter(watched, event)

        if watched in (
            self._w,
            self._btn_fill_fit,
            self._btn_paste,
            self._btn_remove,
            self._btn_confirm,
            self._btn_cancel,
            self._btn_seq_play,
            self._btn_info,
        ):
            if et in (QEvent.Type.Enter, QEvent.Type.HoverEnter):
                self._set_hovered(True)
            elif et in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
                # Keep hovered state alive when moving from left blank area into thumb slowly.
                if self._any_under_mouse() or self._cursor_in_hover_region():
                    self._set_hovered(True)
            elif et in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
                if not self._any_under_mouse():
                    self._set_hovered(False)

        return super().eventFilter(watched, event)


class _InspectorSeqDecodeSignaler(QObject):
    frame_ready = Signal(int, object)


class _InspectorSeqListSignaler(QObject):
    ready = Signal(int, object)  # token, list[Path]


class _InspectorSeqListRunnable(QRunnable):
    def __init__(
        self,
        token: int,
        folder: Path,
        ignore_extensions: frozenset[str] | None,
        ignore_name_tokens: frozenset[str] | None,
        signaler: _InspectorSeqListSignaler,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._token = token
        self._folder = folder
        self._ign_ext = ignore_extensions
        self._ign_tok = ignore_name_tokens
        self._signaler = signaler

    def run(self) -> None:
        from monostudio.core.sequence_preview import list_sequence_frames

        try:
            frames = list_sequence_frames(
                self._folder,
                ignore_extensions=self._ign_ext,
                ignore_name_tokens=self._ign_tok,
            )
        except Exception:
            frames = []
        self._signaler.ready.emit(self._token, frames)


class _InspectorSeqDecodeRunnable(QRunnable):
    def __init__(self, idx: int, path: Path, max_side: int, signaler: _InspectorSeqDecodeSignaler) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._idx = idx
        self._path = path
        self._max_side = max_side
        self._signaler = signaler

    def run(self) -> None:
        from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

        img = load_preview_frame_qimage(self._path, self._max_side)
        self._signaler.frame_ready.emit(self._idx, img)


class _InspectorPreview(QWidget):
    paste_requested = Signal()
    remove_requested = Signal(object)  # emits ViewItem (asset/shot only)
    video_preview_requested = Signal(object)  # Path — legacy
    sequence_preview_requested = Signal(object)  # legacy
    review_open_requested = Signal(object)  # ReviewOpenRequest
    open_in_openrv_requested = Signal(object)  # ReviewOpenRequest

    _PREVIEW_CACHE_MAX = 50
    _PREVIEW_RESIZE_DEBOUNCE_MS = 200

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thumbs = ThumbnailCache(size_px=1024)
        self._thumbnail_manager: object | None = None
        self._worker_manager: object | None = None
        self._active_department: str | None = None
        self._item: ViewItem | None = None
        self._preview_thumb_cache: OrderedDict[str, tuple[QPixmap, bool]] = OrderedDict()
        self._qsettings: QSettings | None = None
        self._sequence_folder: Path | None = None
        self._sequence_frames: list[Path] = []
        self._mmb_folder_drag_start: QPoint | None = None
        self._preview_layout = QVBoxLayout(self)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_layout.setSpacing(0)
        self._container = _PreviewContainer(self)
        self._preview_layout.addWidget(self._container, 0)
        self._container.paste_requested.connect(self.paste_requested.emit)
        self._container.remove_requested.connect(self._on_remove_requested)
        self._container.sequence_play_clicked.connect(self._toggle_inspector_inline_seq_play)
        self._container._w.context_menu_requested.connect(self._open_context_menu)
        self._container._w.installEventFilter(self)
        self._container._w.setToolTip("Double-click for large review · Ctrl+F fullscreen in player")
        self._set_paste_enabled(False)
        self._seq_pool: QThreadPool | None = None
        self._seq_sig = _InspectorSeqDecodeSignaler(self)
        self._seq_sig.frame_ready.connect(self._on_inspector_seq_frame_ready, Qt.ConnectionType.QueuedConnection)
        self._seq_list_token = 0
        self._seq_list_sig = _InspectorSeqListSignaler(self)
        self._seq_list_sig.ready.connect(
            self._on_inspector_sequence_frames_listed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._seq_tick = QTimer(self)
        self._seq_tick.setSingleShot(True)
        self._seq_tick.timeout.connect(self._on_inspector_seq_tick)
        self._seq_poll = QTimer(self)
        self._seq_poll.setSingleShot(True)
        self._seq_poll.timeout.connect(self._on_inspector_seq_tick)
        self._seq_buffer: dict[int, QPixmap] = {}
        self._seq_in_flight: set[int] = set()
        self._seq_playing = False
        self._seq_index = 0
        self._seq_scrubbing = False
        self._last_thumb_use_fit = False
        self._seq_decode_bucket: int | None = None
        self._seq_live_display = False
        self._preview_resizing = False
        self._thumb_reload_after_resize = False
        self._thumb_decode_bucket: int | None = None
        self._preview_resize_debounce = QTimer(self)
        self._preview_resize_debounce.setSingleShot(True)
        self._preview_resize_debounce.setInterval(self._PREVIEW_RESIZE_DEBOUNCE_MS)
        self._preview_resize_debounce.timeout.connect(self._on_preview_resize_settled)

    def clear_transient_hover_states(self) -> None:
        self._container.clear_transient_hover_states()

    def _inspector_preview_decode_max_side(self) -> int:
        """Decode thumbs to current preview cell size (DPR), quantized to reduce churn."""
        wgt = self._container._w
        w = wgt.width()
        if w < 1:
            w = max(1, wgt.sizeHint().width())
        if wgt.hasHeightForWidth():
            h = max(1, wgt.heightForWidth(w))
        else:
            h = max(1, wgt.height() if wgt.height() > 0 else 1)
        dpr = max(1.0, float(wgt.devicePixelRatioF()))
        side = int(max(w, h) * dpr)
        return max(64, min(2048, ((side + 31) // 32) * 32))

    def _decode_side_for_current_item(self) -> int:
        """Preview-sized decode, capped to native source resolution."""
        side = self._inspector_preview_decode_max_side()
        item = self._item
        if item is None:
            return side
        src_path: Path | None = None
        if item.kind == ViewItemKind.INBOX_ITEM and item.path.is_file():
            src_path = item.path
        elif item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            src_path = self._resolve_inspector_thumbnail_disk_path()
        if src_path is not None:
            side = clamp_decode_side_for_media(side, src_path)
        return max(1, side)

    def _mark_preview_resizing(self) -> None:
        self._preview_resizing = True
        self._preview_resize_debounce.start(self._PREVIEW_RESIZE_DEBOUNCE_MS)

    def _defer_thumb_decode_if_resizing(self) -> bool:
        if not self._preview_resizing:
            return False
        self._thumb_reload_after_resize = True
        return True

    def _on_preview_resize_settled(self) -> None:
        self._preview_resizing = False
        new_bucket = self._inspector_preview_decode_max_side()
        bucket_changed = (
            self._thumb_decode_bucket is not None
            and new_bucket != self._thumb_decode_bucket
        )
        had_deferred = self._thumb_reload_after_resize
        self._thumb_reload_after_resize = False
        self._on_inspector_preview_resize()
        item = self._item
        if item is None or (not had_deferred and not bucket_changed):
            return
        if item.kind == ViewItemKind.INBOX_ITEM:
            self.load_inbox_item_preview()
        elif item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT) and not self._seq_live_display:
            self.update_thumbnail_only()

    def _on_remove_requested(self) -> None:
        if self._item is not None:
            self.remove_requested.emit(self._item)

    def _set_paste_enabled(self, enabled: bool) -> None:
        self._container.set_paste_enabled(enabled)

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager

    def set_worker_manager(self, manager: object | None) -> None:
        """Optional: load preview thumb in background, show loading spinner."""
        self._worker_manager = manager

    def set_qsettings(self, settings: QSettings | None) -> None:
        self._qsettings = settings

    def set_inspector_notes_chip(self, visible: bool, open_count: int = 0, visual_mode: str = "empty") -> None:
        self._container._w.set_notes_chip(visible, open_count, visual_mode)

    def _inspector_thumb_source_mode(self) -> str:
        """Thumbnail source mode from settings (separate keys for Asset vs Shot)."""
        item = self._item
        if item is not None and item.kind == ViewItemKind.SHOT:
            return read_inspector_thumbnail_source(self._qsettings, entity="shot")
        return read_inspector_thumbnail_source(self._qsettings, entity="asset")

    def _thumbnail_player_label(self) -> str:
        """User-facing app label for opening a thumbnail externally (Settings or OS default)."""
        exe = read_inspector_thumbnail_open_exe(self._qsettings)
        if exe:
            try:
                p = Path(exe)
                n = p.name if p.name else str(exe)
                return n
            except Exception:
                return str(exe)
        return "default app"

    def invalidate_settings_dependent_cache(self) -> None:
        self._preview_thumb_cache.clear()
        self._seq_decode_bucket = None

    def drop_preview_thumb_cache_for_item(self, item_path: Path) -> None:
        """Drop cached inspector preview pixmaps for one entity (e.g. after ``.meta`` thumb change)."""
        try:
            prefix = str(item_path.resolve())
        except OSError:
            prefix = str(item_path)
        dead = [
            k
            for k in self._preview_thumb_cache
            if k == prefix or k.startswith(f"{prefix}::")
        ]
        for k in dead:
            self._preview_thumb_cache.pop(k, None)

    def _work_paths_for_preview_item(
        self,
        item: ViewItem,
        *,
        active_dcc_hint: str | None = None,
    ) -> tuple[Path | None, Path | None]:
        ref = getattr(item, "ref", None)
        dept = self._active_department
        if not isinstance(ref, (Asset, Shot)) or not (dept or "").strip():
            return (None, None)
        ds = dept.strip()
        if active_dcc_hint is not None:
            adc = (active_dcc_hint or "").strip() or None
        else:
            adc = _inspector_get_active_dcc(item.path, dept)
        wf = primary_work_file_for_department(ref, ds, adc)
        wp = resolve_department_work_path_for_preview(
            ref,
            dept,
            work_file_path=wf,
            item_root=Path(item.path),
            active_dcc_id=adc,
        )
        return (wp, wf)

    def _unreadable_thumb_hint(self) -> str:
        """Short hint for EXR/HDR (and similar) when in-app preview is unavailable."""
        open_path: Path | None = None
        try:
            open_path = self._resolve_inspector_thumbnail_disk_path()
        except Exception:
            open_path = None
        has_file = False
        if open_path is not None:
            try:
                has_file = open_path.is_file()
            except OSError:
                has_file = False
        seq_dir = self._sequence_folder
        has_seq = seq_dir is not None and seq_dir.is_dir()
        player = self._thumbnail_player_label()
        if has_file and has_seq:
            return f"Double-click: open with {player}.\nRight-click: Open thumbnail file… or Open render folder…"
        if has_seq:
            return f"Double-click: open with {player}.\nRight-click: Open render folder…"
        if has_file:
            return f"Double-click: open with {player}.\nRight-click: Open thumbnail file…"
        return f"Double-click: open with {player}.\nRight-click: menu."

    def _apply_inspector_thumb_decode_failure(self, w: _PreviewWidget, *, is_inbox: bool, path: Path | None) -> None:
        """No pixmap: show unreadable extension state for EXR/HDR, else Inbox file icon / clear."""
        heavy_ext: str | None = None
        try:
            p = self._resolve_inspector_thumbnail_disk_path()
            if p is not None:
                sl = p.suffix.lower()
                if sl in (".exr", ".hdr"):
                    heavy_ext = p.suffix.upper()
        except Exception:
            heavy_ext = None
        w.set_pixmap(None)
        if heavy_ext:
            w.set_unreadable_preview(heavy_ext, self._unreadable_thumb_hint())
            return
        w.set_unreadable_preview("", "")
        if is_inbox and path:
            try:
                icon_name, color_hex = file_icon_spec_for_path(path)
                w.set_placeholder_file_icon(icon_name, color_hex)
            except Exception:
                pass

    def _sync_sequence_context_for_inspector_preview(self) -> None:
        self._seq_list_token += 1
        list_token = self._seq_list_token
        self._sequence_folder = None
        self._sequence_frames = []
        try:
            self._container._w.set_version_badge(text="", bg=None, tooltip="")
        except Exception:
            pass
        item = self._item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            self._seq_index = 0
            self._update_sequence_play_button()
            self._sync_inspector_thumb_tooltip()
            return
        wp, wf = self._work_paths_for_preview_item(item)
        ign_ext = get_thumbnail_sequence_ignore_extensions(self._qsettings)
        ign_tok = get_thumbnail_sequence_ignore_tokens(self._qsettings)
        sq = _inspector_preview_resolve_sequence_folder(wp, wf)
        self._sequence_folder = sq
        self._seq_index = 0

        # Version badge: always show when latest work version is known.
        # Color encodes whether we're showing a sequence folder for the latest work (green) or an older fallback (red).
        try:
            mode = self._inspector_thumb_source_mode()
            latest_v = _work_file_version_from_path_for_inspector(wf)
            if latest_v is not None:
                label_latest = f"v{int(latest_v):03d}"
            else:
                label_latest = ""

            badge_text = label_latest
            badge_bg: QColor | None = None
            tooltip = ""

            if badge_text:
                # Default: latest known work version.
                badge_bg = QColor(MONOS_COLORS["emerald_500"])
                tooltip = f"Preview version: {badge_text} (matches latest work)."

            # When in Render / Smart, and latest has no matching sequence folder, badge becomes red and shows fallback version.
            if mode in (THUMB_SOURCE_RENDER_SEQUENCE, THUMB_SOURCE_USER_THEN_RENDER) and wp is not None and wp.is_dir():
                from monostudio.core.sequence_preview import (
                    resolve_best_available_sequence_folder,
                    resolve_sequence_folder,
                )
                cur = resolve_sequence_folder(wp, wf)
                if cur is None:
                    best = resolve_best_available_sequence_folder(wp)
                    if best is not None:
                        import re

                        m = re.search(r"(?:^|_)v(\d{3,})(?:_|$)", best.name, flags=re.IGNORECASE)
                        fallback_v = int(m.group(1)) if m else None
                        if fallback_v is not None:
                            badge_text = f"v{fallback_v:03d}"
                        # Mark as old only when we actually have to fall back.
                        badge_bg = QColor(MONOS_COLORS["red_500"])
                        if badge_text and label_latest and badge_text != label_latest:
                            tooltip = (
                                f"Preview version: {badge_text} (older preview).\n"
                                f"Latest work: {label_latest} (no preview yet)."
                            )
                        elif label_latest:
                            tooltip = (
                                "Preview version: older preview.\n"
                                f"Latest work: {label_latest} (no preview yet)."
                            )
                        else:
                            tooltip = "Preview version: older preview (latest work has no preview yet)."

            if badge_text:
                self._container._w.set_version_badge(text=badge_text, bg=badge_bg, tooltip=tooltip)
        except Exception:
            pass
        if sq is not None:
            self._ensure_inspector_seq_pool()
            assert self._seq_pool is not None
            self._seq_pool.start(
                _InspectorSeqListRunnable(list_token, sq, ign_ext, ign_tok, self._seq_list_sig)
            )
        self._update_sequence_play_button()
        self._sync_inspector_thumb_tooltip()

    def _on_inspector_sequence_frames_listed(self, token: int, frames: object) -> None:
        if token != self._seq_list_token:
            return
        listed = frames if isinstance(frames, list) else []
        self._sequence_frames = listed
        self._seq_index = (len(listed) // 2) if listed else 0
        self._update_sequence_play_button()
        self._sync_inspector_thumb_tooltip()

    def _sync_inspector_thumb_tooltip(self) -> None:
        """Help text for the corner info button; preview widget has no hover tooltip."""
        w = self._container._w
        w.setToolTip("")
        item = self._item
        if item is None:
            self._container.set_preview_help_text("")
            return
        if item.kind == ViewItemKind.INBOX_ITEM:
            if item.path.is_dir():
                self._container.set_preview_help_text(
                    "Double-click: browse folder.\n"
                    "Right-click: menu."
                )
            else:
                self._container.set_preview_help_text(
                    "Double-click: open file.\n"
                    "Right-click: menu."
                )
            return
        if item.kind in (ViewItemKind.PROJECT, ViewItemKind.DEPARTMENT):
            self._container.set_preview_help_text("Right-click: menu.")
            return
        player = self._thumbnail_player_label()
        lines: list[str] = [
            f"Double-click: open with {player}.",
        ]
        if self._sequence_frames:
            lines.append("Middle mouse + drag: drag sequence folder into Explorer / a DCC.")
            lines.append("Left-click / drag horizontally on preview: scrub frames.")
        src = self._inspector_thumb_source_mode()
        if src != THUMB_SOURCE_RENDER_SEQUENCE:
            lines.append("Hover: Fill / Fit, Paste, Remove (top).")
        lines.append("Right-click: menu (paste, remove, open file…).")
        self._container.set_preview_help_text("\n".join(lines))

    def _update_sequence_play_button(self) -> None:
        self._container.update_sequence_play_control(
            available=bool(self._sequence_frames),
            playing=self._seq_playing,
        )

    def _inspector_thumb_hit_test_blocks_scrub(self, pos: QPoint) -> bool:
        """Notes / health chips: keep LMB for chip clicks, not scrub."""
        wgt = self._container._w
        if isinstance(wgt, _PreviewWidget):
            return bool(wgt._note_hit(pos) or wgt._health_hit(pos))
        return False

    def _inspector_sequence_folder_drag_pixmap(self) -> tuple[QPixmap | None, QPoint]:
        """Card-style pixmap under cursor (shared with grid/list middle-drag)."""
        wgt = self._container._w
        item = self._item
        src_pix: QPixmap | None = getattr(wgt, "_pix", None)
        has_img = bool(getattr(wgt, "_has_image", False) and src_pix is not None and not src_pix.isNull())
        name = ""
        if item is not None:
            try:
                name = (display_name_for_item(item) or "").strip()
            except Exception:
                name = ""
        if not name and self._sequence_folder is not None:
            name = self._sequence_folder.name
        return build_single_pipeline_drag_pixmap(
            wgt,
            base_rect=resolve_grid_card_base_rect(self),
            name=name or "—",
            thumb_pixmap=src_pix if has_img else None,
            folder_fallback=not has_img,
        )

    def _perform_sequence_folder_drag(self) -> None:
        if self._sequence_folder is None or not self._sequence_folder.is_dir():
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(self._sequence_folder.resolve()))])
        drag = QDrag(self._container._w)
        drag.setMimeData(md)
        dp, hs = self._inspector_sequence_folder_drag_pixmap()
        if dp is not None and not dp.isNull():
            drag.setPixmap(dp)
            drag.setHotSpot(hs)
        drag.exec(Qt.DropAction.CopyAction)

    def _resolve_inspector_thumbnail_disk_path(self) -> Path | None:
        """On-disk image file shown in preview (sequence frame when scrubbing/playing, else resolved thumb)."""
        item = self._item
        if item is None:
            return None
        if item.kind == ViewItemKind.INBOX_ITEM:
            p = item.path
            return p if isinstance(p, Path) and p.is_file() else None
        if self._seq_live_display and self._sequence_frames:
            i = self._seq_index
            if 0 <= i < len(self._sequence_frames):
                f = self._sequence_frames[i]
                try:
                    if f.is_file():
                        return f
                except OSError:
                    pass
        mode = self._inspector_thumb_source_mode()
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)):
            wp, wf = self._work_paths_for_preview_item(item)
            ign_ext = get_thumbnail_sequence_ignore_extensions(self._qsettings)
            ign_tok = get_thumbnail_sequence_ignore_tokens(self._qsettings)
            p = resolve_entity_thumbnail_source_path(
                Path(item.path),
                self._active_department,
                mode,
                wp,
                wf,
                sequence_ignore_extensions=ign_ext,
                sequence_ignore_name_tokens=ign_tok,
            )
            if p is not None:
                try:
                    if p.is_file():
                        return p
                except OSError:
                    pass
        p2 = resolve_thumbnail_path(Path(item.path), department=self._active_department)
        if p2 is not None:
            try:
                if p2.is_file():
                    return p2
            except OSError:
                pass
        return None

    def _open_inspector_thumbnail_externally(self) -> None:
        path = self._resolve_inspector_thumbnail_disk_path()
        if path is None:
            return
        try:
            path = path.resolve()
        except OSError:
            return
        if not path.is_file():
            return
        exe = read_inspector_thumbnail_open_exe(self._qsettings)
        if exe:
            exep = Path(exe)
            if exep.is_file():
                try:
                    subprocess.Popen([str(exep), str(path)], cwd=str(path.parent))
                    return
                except OSError as e:
                    logging.getLogger(__name__).warning("Open thumbnail with configured app failed: %s", e)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _sync_thumbnail_overlay_mode(self) -> None:
        mode = self._inspector_thumb_source_mode()
        self._container.set_render_sequence_hide_controls(mode == THUMB_SOURCE_RENDER_SEQUENCE)
        self._sync_inspector_thumb_tooltip()

    def _halt_inline_sequence_ui(self) -> None:
        self._seq_playing = False
        self._seq_scrubbing = False
        self._mmb_folder_drag_start = None
        self._seq_live_display = False
        self._seq_tick.stop()
        self._seq_poll.stop()
        self._seq_buffer.clear()
        self._seq_in_flight.clear()
        if self._seq_pool is not None:
            self._seq_pool.clear()
        self._update_sequence_play_button()

    def _restore_static_thumb_from_cache(self) -> None:
        item = self._item
        w = self._container._w
        if item is None:
            return
        ck = self._preview_cache_key(item.path)
        t = self._preview_thumb_cache.get(ck)
        if t is not None:
            pix, uf = t
            if pix is not None and not pix.isNull():
                w.set_pixmap(pix, use_fit=uf)
                self._seq_live_display = False
                return

    def _ensure_inspector_seq_pool(self) -> None:
        if self._seq_pool is None:
            self._seq_pool = QThreadPool(self)
            self._seq_pool.setMaxThreadCount(4)

    def _inspector_seq_is_heavy(self) -> bool:
        heavy = {".exr", ".hdr"}
        if not self._sequence_frames:
            return False
        for p in (self._sequence_frames[0], self._sequence_frames[-1]):
            if p.suffix.lower() not in heavy:
                return False
        return True

    def _inspector_seq_prefetch_n(self) -> int:
        return 1 if self._inspector_seq_is_heavy() else 3

    def _trim_inspector_seq_buffer(self) -> None:
        cap = 6
        while len(self._seq_buffer) > cap:
            best_k = None
            best_d = -1
            for k in self._seq_buffer:
                d = abs(k - self._seq_index)
                if d > best_d:
                    best_d = d
                    best_k = k
            if best_k is not None:
                del self._seq_buffer[best_k]
            else:
                break

    def _request_inspector_seq_decode(self, idx: int) -> None:
        n = len(self._sequence_frames)
        if idx < 0 or idx >= n:
            return
        if self._preview_resizing:
            return
        if idx in self._seq_buffer or idx in self._seq_in_flight:
            return
        self._ensure_inspector_seq_pool()
        self._seq_in_flight.add(idx)
        mx = self._inspector_preview_decode_max_side()
        self._seq_pool.start(
            _InspectorSeqDecodeRunnable(idx, self._sequence_frames[idx], mx, self._seq_sig)
        )

    def _show_inspector_seq_frame(self, idx: int) -> None:
        n = len(self._sequence_frames)
        if n <= 0:
            return
        idx = max(0, min(n - 1, idx))
        self._seq_index = idx
        w = self._container._w
        if idx in self._seq_buffer:
            w.set_pixmap(self._seq_buffer[idx], use_fit=self._last_thumb_use_fit)
            self._seq_live_display = True
            return
        self._request_inspector_seq_decode(idx)

    def _scrub_inspector_seq_to_x(self, lx: int, width: int) -> None:
        n = len(self._sequence_frames)
        if n <= 0:
            return
        self._seq_playing = False
        self._seq_tick.stop()
        self._seq_poll.stop()
        ww = max(1, width)
        frac = max(0.0, min(1.0, lx / float(ww)))
        idx = int(round(frac * (n - 1)))
        self._show_inspector_seq_frame(idx)

    def _scrub_inspector_seq_from_event(self, event: QMouseEvent) -> None:
        w = self._container._w
        lx = int(event.position().x()) if hasattr(event, "position") else int(event.pos().x())
        self._scrub_inspector_seq_to_x(lx, max(1, w.width()))

    def _toggle_inspector_inline_seq_play(self) -> None:
        if not self._sequence_frames:
            return
        if self._seq_playing:
            # Pause: giữ frame + index; lần play sau tiếp từ đây.
            self._seq_playing = False
            self._seq_tick.stop()
            self._seq_poll.stop()
            self._update_sequence_play_button()
            return
        self._seq_playing = False
        self._seq_tick.stop()
        self._seq_poll.stop()
        self._seq_in_flight.clear()
        if self._seq_pool is not None:
            self._seq_pool.clear()
        self._seq_playing = True
        self._request_inspector_seq_decode(self._seq_index)
        pn = self._inspector_seq_prefetch_n()
        n = len(self._sequence_frames)
        for k in range(1, min(pn + 1, n)):
            j = (self._seq_index + k) % n
            self._request_inspector_seq_decode(j)
        self._schedule_inspector_seq_tick()
        self._update_sequence_play_button()

    def _schedule_inspector_seq_tick(self) -> None:
        if not self._seq_playing or not self._sequence_frames:
            return
        fps = read_sequence_preview_fps(self._qsettings)
        ms = max(1, round(1000 / max(1, min(60, int(fps)))))
        self._seq_tick.start(ms)

    def _on_inspector_seq_tick(self) -> None:
        if not self._seq_playing or not self._sequence_frames or self._seq_scrubbing:
            return
        n = len(self._sequence_frames)
        nxt = (self._seq_index + 1) % n
        pn = self._inspector_seq_prefetch_n()
        if nxt in self._seq_buffer:
            self._show_inspector_seq_frame(nxt)
            for k in range(1, pn + 1):
                j = (self._seq_index + k) % n
                self._request_inspector_seq_decode(j)
            self._schedule_inspector_seq_tick()
        else:
            self._request_inspector_seq_decode(nxt)
            self._seq_poll.start(16)

    def _on_inspector_seq_frame_ready(self, idx: int, image: object) -> None:
        self._seq_in_flight.discard(idx)
        n = len(self._sequence_frames)
        if idx < 0 or idx >= n:
            return
        if isinstance(image, QImage) and not image.isNull():
            pix = QPixmap.fromImage(image)
            if not pix.isNull():
                self._seq_buffer[idx] = pix
                self._trim_inspector_seq_buffer()
        if idx == self._seq_index and idx in self._seq_buffer:
            self._container._w.set_pixmap(self._seq_buffer[idx], use_fit=self._last_thumb_use_fit)
            self._seq_live_display = True

    def _on_inspector_preview_resize(self) -> None:
        if not self._sequence_frames:
            self._seq_decode_bucket = None
            return
        mx = self._inspector_preview_decode_max_side()
        b = max(64, (mx // 64) * 64)
        if b == self._seq_decode_bucket:
            return
        self._seq_decode_bucket = b
        if not self._seq_live_display:
            return
        self._seq_buffer.clear()
        self._seq_in_flight.clear()
        if self._seq_pool is not None:
            self._seq_pool.clear()
        self._show_inspector_seq_frame(self._seq_index)
        if self._seq_playing:
            pn = self._inspector_seq_prefetch_n()
            n = len(self._sequence_frames)
            for k in range(1, min(pn + 1, n)):
                j = (self._seq_index + k) % n
                self._request_inspector_seq_decode(j)

    def _department_label_for_active(self) -> str | None:
        dept_id = self._active_department
        if not dept_id:
            return None
        w = self.parent()
        while w is not None:
            resolver = getattr(w, "_department_label_resolver", None)
            if callable(resolver):
                try:
                    lab = resolver(dept_id)
                except Exception:
                    lab = None
                if lab:
                    return str(lab).strip() or None
            w = w.parent()
        return dept_id.replace("_", " ").title()

    def _resolve_entity_review_media(self):
        thumb_path = self._resolve_inspector_thumbnail_disk_path()
        if thumb_path is None:
            return None
        item = self._item
        entity_path = Path(item.path) if item is not None and getattr(item, "path", None) else None
        wp, wf = (None, None)
        if item is not None and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            wp, wf = self._work_paths_for_preview_item(item)
        fps = read_sequence_preview_fps(self._qsettings)
        return resolve_entity_review_media(
            thumb_path=thumb_path,
            work_path=wp,
            work_file_path=wf,
            sequence_frames=None,
            sequence_folder=self._sequence_folder,
            fps=fps,
            context=PreviewContext.entity,
            entity_path=entity_path,
            department_id=self._active_department,
            department_label=self._department_label_for_active(),
        )

    def _resolve_review_open_request(self):
        resolved = self._resolve_entity_review_media()
        if resolved is None:
            return None
        if resolved.action == ReviewResolveAction.open_player and resolved.request is not None:
            return resolved.request
        return None

    def _try_emit_review_open(self) -> bool:
        resolved = self._resolve_entity_review_media()
        if resolved is None:
            return False
        if resolved.action == ReviewResolveAction.open_player and resolved.request is not None:
            self.review_open_requested.emit(resolved.request)
            return True
        if resolved.action == ReviewResolveAction.open_external:
            if self._seq_playing:
                self._seq_playing = False
                self._seq_tick.stop()
                self._seq_poll.stop()
                self._restore_static_thumb_from_cache()
            self._open_inspector_thumbnail_externally()
            self._update_sequence_play_button()
            return True
        return False

    def _emit_open_in_openrv(self) -> None:
        request = self._resolve_review_open_request()
        if request is not None:
            self.open_in_openrv_requested.emit(request)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is not self._container._w:
            return super().eventFilter(watched, event)
        try:
            et = event.type()
        except Exception:
            return False
        if et == QEvent.Type.Resize:
            self._mark_preview_resizing()
            return False
        if et == QEvent.Type.MouseButtonDblClick and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                if self._try_emit_review_open():
                    return True
                return False
        if et == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            try:
                mpos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            except Exception:
                mpos = QPoint(0, 0)
            if event.button() == Qt.MouseButton.LeftButton and self._sequence_frames:
                if not self._inspector_thumb_hit_test_blocks_scrub(mpos):
                    self._seq_scrubbing = True
                    self._scrub_inspector_seq_from_event(event)
                    return True
            if event.button() == Qt.MouseButton.MiddleButton and self._sequence_folder and self._sequence_folder.is_dir():
                if hasattr(event, "position"):
                    self._mmb_folder_drag_start = QPoint(int(event.position().x()), int(event.position().y()))
                else:
                    self._mmb_folder_drag_start = event.pos()
        elif et == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if self._seq_scrubbing and bool(event.buttons() & Qt.MouseButton.LeftButton):
                self._scrub_inspector_seq_from_event(event)
                return True
            if self._mmb_folder_drag_start is not None and bool(event.buttons() & Qt.MouseButton.MiddleButton):
                if hasattr(event, "position"):
                    pos = QPoint(int(event.position().x()), int(event.position().y()))
                else:
                    pos = event.pos()
                d = pos - self._mmb_folder_drag_start
                if d.manhattanLength() >= QApplication.startDragDistance():
                    self._perform_sequence_folder_drag()
                    self._mmb_folder_drag_start = None
        elif et == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton and self._seq_scrubbing:
                self._seq_scrubbing = False
                return True
            if event.button() == Qt.MouseButton.MiddleButton:
                self._mmb_folder_drag_start = None
        return False

    def apply_preview_thumb(self, path_str: str, image_or_none: QImage | None, use_fit: bool) -> None:
        """Main thread only: apply thumb from worker. path_str must match current item."""
        self._halt_inline_sequence_ui()
        self._last_thumb_use_fit = use_fit
        w = self._container._w
        w.set_loading(False)
        item = self._item
        if item is None or str(item.path) != path_str:
            return
        self._sync_sequence_context_for_inspector_preview()
        cache_key = self._preview_cache_key(Path(path_str))
        pix: QPixmap | None = None
        if image_or_none is not None and not image_or_none.isNull():
            pix = QPixmap.fromImage(image_or_none)
            if not pix.isNull():
                w.set_pixmap(pix, use_fit=use_fit)
                w.set_unreadable_preview("", "")
                self._seq_live_display = False
        if pix is None:
            self._apply_inspector_thumb_decode_failure(
                w,
                is_inbox=item.kind == ViewItemKind.INBOX_ITEM,
                path=item.path,
            )
            self._seq_live_display = False
        while len(self._preview_thumb_cache) >= self._PREVIEW_CACHE_MAX:
            self._preview_thumb_cache.popitem(last=False)
        self._preview_thumb_cache[cache_key] = (pix, use_fit)
        self._preview_thumb_cache.move_to_end(cache_key)
        self._thumb_decode_bucket = self._inspector_preview_decode_max_side()
        self._sync_thumbnail_overlay_mode()

    def clear_preview_loading(self) -> None:
        """Tắt loading spinner (khi worker lỗi)."""
        self._container._w.set_loading(False)

    def set_active_department(self, department: str | None) -> None:
        self._active_department = (department or "").strip() or None

    def _preview_cache_key(self, path: Path, *, active_dcc_hint: str | None = None) -> str:
        try:
            base = str(path.resolve())
        except Exception:
            base = str(path)
        dep = (self._active_department or "").strip()
        mode = self._inspector_thumb_source_mode()
        item = self._item
        if (
            dep
            and item is not None
            and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT)
            and str(getattr(item, "path", "")) == str(path)
        ):
            if active_dcc_hint is not None:
                ac = active_dcc_hint
            else:
                ac = _inspector_get_active_dcc(path, dep)
            adc_seg = active_dcc_segment_for_thumbnail_cache(ac)
            key = f"{base}::dept::{dep}::adc::{adc_seg}::ts::{mode}"
        elif dep:
            key = f"{base}::dept::{dep}::ts::{mode}"
        else:
            key = f"{base}::ts::{mode}"
        bucket = self._inspector_preview_decode_max_side()
        return f"{key}::dbg::{bucket}"

    def set_item(self, item: ViewItem) -> None:
        self._halt_inline_sequence_ui()
        self._seq_index = 0
        self._seq_decode_bucket = None
        self._seq_live_display = False
        self._thumb_decode_bucket = None
        self._item = item
        self._sequence_folder = None
        self._sequence_frames = []
        self._update_sequence_play_button()
        can_paste = item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT)
        self._set_paste_enabled(can_paste)
        is_inbox = item.kind == ViewItemKind.INBOX_ITEM
        w = self._container._w
        w.set_inbox_mode(is_inbox)
        self._container.set_inbox_mode(is_inbox)
        self._sync_thumbnail_overlay_mode()
        self._preview_layout.setStretchFactor(self._container, 0)
        w.set_placeholder_kind(item.kind.value, letter=(display_name_for_item(item) or "").strip()[:1])
        w.set_user_fit(False)  # default fill when switching item
        w.set_pixmap(None)
        w.set_unreadable_preview("", "")
        path = item.path
        path_str = str(path)
        cache_key = self._preview_cache_key(path)
        dept = self._active_department
        mgr = self._worker_manager

        if is_inbox:
            self._sync_sequence_context_for_inspector_preview()
            if path.is_file():
                self._apply_inspector_thumb_decode_failure(w, is_inbox=True, path=path)
            w.set_loading(False)
            return

        # Đã load rồi thì dùng cache, không load lại
        if cache_key in self._preview_thumb_cache:
            cached_pix, cached_fit = self._preview_thumb_cache[cache_key]
            self._preview_thumb_cache.move_to_end(cache_key)
            if cached_pix is not None and not cached_pix.isNull():
                w.set_pixmap(cached_pix, use_fit=cached_fit)
                self._seq_live_display = False
                self._thumb_decode_bucket = self._inspector_preview_decode_max_side()
                self._sync_sequence_context_for_inspector_preview()
                return
            # cache lưu (None, fit) khi không có thumb → placeholder hoặc EXR/HDR unreadable
            self._sync_sequence_context_for_inspector_preview()
            self._apply_inspector_thumb_decode_failure(w, is_inbox=is_inbox, path=path)
            self._seq_live_display = False
            return

        if self._defer_thumb_decode_if_resizing():
            return

        mode = self._inspector_thumb_source_mode()
        wp, wf = self._work_paths_for_preview_item(item)
        wps = str(wp) if wp is not None else None
        wfs = str(wf) if wf is not None else None
        ign_ext = get_thumbnail_sequence_ignore_extensions(self._qsettings)
        ign_tok = get_thumbnail_sequence_ignore_tokens(self._qsettings)

        if mgr is not None and hasattr(mgr, "submit_task"):
            w.set_loading(True)
            w.update()

            def submit() -> None:
                if getattr(self, "_item", None) is not item or str(self._item.path) != path_str:
                    w.set_loading(False)
                    return
                if self._preview_resizing:
                    self._thumb_reload_after_resize = True
                    w.set_loading(False)
                    return
                ms = self._decode_side_for_current_item()

                def run_load() -> tuple[str, QImage | None, bool]:
                    return _inspector_preview_worker_run(
                        path_str,
                        is_inbox=False,
                        dept=dept,
                        mode=mode,
                        work_path_str=wps,
                        work_file_str=wfs,
                        decode_max_side=ms,
                        sequence_ignore_extensions=ign_ext,
                        sequence_ignore_name_tokens=ign_tok,
                    )

                task = WorkerTask("inspector_preview_thumb", run_load, manager=mgr)
                mgr.submit_task(task, category="inspector_preview_thumb", replace_existing=True)

            QTimer.singleShot(0, submit)
            return

        def load() -> None:
            if getattr(self, "_item", None) is not item or str(self._item.path) != path_str:
                return
            ms = self._decode_side_for_current_item()
            ps, img, uf = _inspector_preview_worker_run(
                path_str,
                is_inbox=is_inbox,
                dept=dept,
                mode=mode,
                work_path_str=wps,
                work_file_str=wfs,
                decode_max_side=ms,
                sequence_ignore_extensions=ign_ext,
                sequence_ignore_name_tokens=ign_tok,
            )
            self.apply_preview_thumb(ps, img, uf)

        QTimer.singleShot(0, load)

    def load_inbox_item_preview(self) -> None:
        """HD thumb for explorer tree file selection."""
        item = self._item
        if item is None or item.kind != ViewItemKind.INBOX_ITEM:
            return
        path = item.path
        w = self._container._w
        if path.is_dir():
            w.set_loading(False)
            w.set_pixmap(None)
            self._seq_live_display = False
            self._sequence_frames = []
            w.set_placeholder_file_icon("folder", MONOS_COLORS.get("text_meta", "#71717a"))
            w.set_unreadable_preview("", str(path))
            self._thumb_decode_bucket = self._inspector_preview_decode_max_side()
            self._sync_sequence_context_for_inspector_preview()
            self._sync_thumbnail_overlay_mode()
            self._sync_inspector_thumb_tooltip()
            return
        if not path.is_file():
            return
        if self._defer_thumb_decode_if_resizing():
            return
        path_str = str(path)
        cache_key = self._preview_cache_key(path)
        w = self._container._w
        mgr = self._worker_manager

        if cache_key in self._preview_thumb_cache:
            cached_pix, cached_fit = self._preview_thumb_cache[cache_key]
            self._preview_thumb_cache.move_to_end(cache_key)
            if cached_pix is not None and not cached_pix.isNull():
                w.set_pixmap(cached_pix, use_fit=cached_fit)
                w.set_unreadable_preview("", "")
                self._seq_live_display = False
                self._thumb_decode_bucket = self._inspector_preview_decode_max_side()
                self._sync_sequence_context_for_inspector_preview()
                self._sync_thumbnail_overlay_mode()
                return
            self._sync_sequence_context_for_inspector_preview()
            self._apply_inspector_thumb_decode_failure(w, is_inbox=True, path=path)
            self._seq_live_display = False
            self._sync_thumbnail_overlay_mode()
            return

        w.set_loading(True)
        self._sync_thumbnail_overlay_mode()
        decode_ms = self._decode_side_for_current_item()

        if mgr is not None and hasattr(mgr, "submit_task"):
            w.update()

            def submit() -> None:
                if self._item is not item or str(self._item.path) != path_str:
                    w.set_loading(False)
                    return
                if self._preview_resizing:
                    self._thumb_reload_after_resize = True
                    w.set_loading(False)
                    return

                def run_load() -> tuple[str, QImage | None, bool]:
                    return _inspector_preview_worker_run(
                        path_str,
                        is_inbox=True,
                        dept=self._active_department,
                        mode=self._inspector_thumb_source_mode(),
                        work_path_str=None,
                        work_file_str=None,
                        decode_max_side=decode_ms,
                    )

                task = WorkerTask("inspector_preview_thumb", run_load, manager=mgr)
                mgr.submit_task(task, category="inspector_preview_thumb", replace_existing=True)

            QTimer.singleShot(0, submit)
            return

        def load() -> None:
            if self._item is not item or str(self._item.path) != path_str:
                return
            ps, img, uf = _inspector_preview_worker_run(
                path_str,
                is_inbox=True,
                dept=self._active_department,
                mode=self._inspector_thumb_source_mode(),
                work_path_str=None,
                work_file_str=None,
                decode_max_side=decode_ms,
            )
            self.apply_preview_thumb(ps, img, uf)

        QTimer.singleShot(0, load)

    def update_thumbnail_only(self, *, active_dcc_hint: str | None = None) -> None:
        """Update thumbnail image only (e.g. after thumbnailsChanged or department change)."""
        item = self._item
        if item is None:
            return
        self._halt_inline_sequence_ui()
        path = item.path
        path_str = str(path)
        cache_key = self._preview_cache_key(path, active_dcc_hint=active_dcc_hint)
        dept = self._active_department
        is_inbox = item.kind == ViewItemKind.INBOX_ITEM
        if is_inbox:
            self.load_inbox_item_preview()
            return
        mgr = self._worker_manager
        mode = self._inspector_thumb_source_mode()
        wp, wf = self._work_paths_for_preview_item(item, active_dcc_hint=active_dcc_hint)
        wps = str(wp) if wp is not None else None
        wfs = str(wf) if wf is not None else None
        ign_ext = get_thumbnail_sequence_ignore_extensions(self._qsettings)
        ign_tok = get_thumbnail_sequence_ignore_tokens(self._qsettings)

        if cache_key in self._preview_thumb_cache:
            cached_pix, cached_fit = self._preview_thumb_cache[cache_key]
            self._preview_thumb_cache.move_to_end(cache_key)
            w = self._container._w
            if cached_pix is not None and not cached_pix.isNull():
                w.set_pixmap(cached_pix, use_fit=cached_fit)
                self._seq_live_display = False
                self._thumb_decode_bucket = self._inspector_preview_decode_max_side()
                self._sync_sequence_context_for_inspector_preview()
                self._sync_thumbnail_overlay_mode()
                return
            self._sync_sequence_context_for_inspector_preview()
            self._apply_inspector_thumb_decode_failure(w, is_inbox=is_inbox, path=path)
            self._seq_live_display = False
            self._sync_thumbnail_overlay_mode()
            return

        if self._defer_thumb_decode_if_resizing():
            return

        w = self._container._w
        if mgr is not None and hasattr(mgr, "submit_task"):
            w.set_loading(True)
            w.update()
            self._sync_thumbnail_overlay_mode()

            def submit() -> None:
                if self._item is not item or str(self._item.path) != path_str:
                    w.set_loading(False)
                    return
                if self._preview_resizing:
                    self._thumb_reload_after_resize = True
                    w.set_loading(False)
                    return
                ms = self._decode_side_for_current_item()

                def run_load() -> tuple[str, QImage | None, bool]:
                    return _inspector_preview_worker_run(
                        path_str,
                        is_inbox=is_inbox,
                        dept=dept,
                        mode=mode,
                        work_path_str=wps,
                        work_file_str=wfs,
                        decode_max_side=ms,
                        sequence_ignore_extensions=ign_ext,
                        sequence_ignore_name_tokens=ign_tok,
                    )

                task = WorkerTask("inspector_preview_thumb", run_load, manager=mgr)
                mgr.submit_task(task, category="inspector_preview_thumb", replace_existing=True)

            QTimer.singleShot(0, submit)
            return

        def load() -> None:
            if self._item is not item or str(self._item.path) != path_str:
                return
            ms = self._decode_side_for_current_item()
            ps, img, uf = _inspector_preview_worker_run(
                path_str,
                is_inbox=is_inbox,
                dept=dept,
                mode=mode,
                work_path_str=wps,
                work_file_str=wfs,
                decode_max_side=ms,
                sequence_ignore_extensions=ign_ext,
                sequence_ignore_name_tokens=ign_tok,
            )
            self.apply_preview_thumb(ps, img, uf)

        QTimer.singleShot(0, load)

    def refresh_thumbnail(self) -> None:
        item = self._item
        if item is None:
            return
        self.drop_preview_thumb_cache_for_item(item.path)
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is not None and hasattr(mgr, "invalidate"):
            mgr.invalidate(str(item.path), department=self._active_department)
        for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
            self._thumbs.invalidate_file(item.path / name)
        self.set_item(item)
        if item.kind == ViewItemKind.INBOX_ITEM:
            self.load_inbox_item_preview()

    def _open_context_menu(self, global_pos: object) -> None:
        gp = global_pos if isinstance(global_pos, QPoint) else QPoint(0, 0)
        item = self._item
        can_paste = bool(item and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))

        menu = QMenu(self)
        act = QAction(lucide_icon("clipboard-paste", size=16, color_hex=MONOS_COLORS["text_label"]), "Paste thumbnail from Clipboard", menu)
        act.setEnabled(can_paste)
        act.triggered.connect(self.paste_requested.emit)
        menu.addAction(act)
        act_remove = QAction(lucide_icon("trash-2", size=16, color_hex=MONOS_COLORS["text_label"]), "Remove thumbnail", menu)
        act_remove.setEnabled(can_paste)
        act_remove.triggered.connect(lambda: self.remove_requested.emit(self._item) if self._item else None)
        menu.addAction(act_remove)
        open_path = self._resolve_inspector_thumbnail_disk_path()
        act_open = QAction(lucide_icon("file-image", size=16, color_hex=MONOS_COLORS["text_label"]), "Open thumbnail file…", menu)
        act_open.setEnabled(open_path is not None)
        act_open.triggered.connect(self._open_inspector_thumbnail_externally)
        menu.addAction(act_open)

        if item is not None and item.kind == ViewItemKind.INBOX_ITEM:
            vpath = item.path
            if vpath.is_file() and is_video_preview_path(vpath):
                act_play = QAction(
                    lucide_icon("play", size=16, color_hex=MONOS_COLORS["text_label"]),
                    "Play video…",
                    menu,
                )
                act_play.triggered.connect(lambda: self.video_preview_requested.emit(vpath))
                menu.insertAction(act_open, act_play)

        if item is not None and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            act_review = QAction(
                lucide_icon("play", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Review latest preview…",
                menu,
            )
            act_review.triggered.connect(self._try_emit_review_open)
            menu.insertAction(act_open, act_review)
            from monostudio.core.openrv_launch import is_openrv_available

            act_openrv = QAction(
                lucide_icon("external-link", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open in OpenRV…",
                menu,
            )
            rv_available = is_openrv_available(self._qsettings)
            act_openrv.setEnabled(rv_available)
            if not rv_available:
                act_openrv.setToolTip("Configure OpenRV path in Settings → General → Video player.")
            act_openrv.triggered.connect(self._emit_open_in_openrv)
            menu.insertAction(act_open, act_openrv)

        seq_dir = self._sequence_folder if (self._sequence_folder is not None and self._sequence_folder.is_dir()) else None
        act_open_render = QAction(
            lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Open render folder…",
            menu,
        )
        act_open_render.setEnabled(bool(seq_dir))
        if seq_dir is not None:
            act_open_render.triggered.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(seq_dir)))
            )
        menu.addAction(act_open_render)
        menu.exec(gp)


def _inspector_primary_name_text(text: str) -> str:
    """Soft-wrap friendly display for long file/entity names in the identity block."""
    s = (text or "").strip()
    if len(s) <= 48:
        return s
    zwsp = "\u200b"
    out: list[str] = []
    run = 0
    for ch in s:
        out.append(ch)
        run += 1
        if ch in " _.-【】()·\t":
            run = 0
        elif run >= 24:
            out.append(zwsp)
            run = 0
    return "".join(out)


class _IdentityBlock(QWidget):
    open_clicked = Signal()
    open_with_clicked = Signal()
    active_dcc_changed = Signal(object, str, str)  # path, department, dcc_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorIdentity")
        block_policy = self.sizePolicy()
        block_policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        block_policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self.setSizePolicy(block_policy)
        self._current_item: ViewItem | None = None
        self._active_department: str | None = None
        self._active_dcc_id: str | None = None
        self._show_publish = False

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        self._name = QLabel("", self)
        self._name.setObjectName("InspectorPrimaryName")
        f = monos_font("Inter", 15, QFont.Weight.DemiBold)
        self._name.setFont(f)
        self._name.setWordWrap(True)
        self._name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        name_policy = self._name.sizePolicy()
        name_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        name_policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self._name.setSizePolicy(name_policy)

        meta_row = QWidget(self)
        meta_l = QHBoxLayout(meta_row)
        meta_l.setContentsMargins(0, 0, 0, 0)
        meta_l.setSpacing(6)

        self._meta_type_badge = QLabel("", self)
        self._meta_type_badge.setObjectName("InspectorTypeBadge")
        badge_font = monos_font("Inter", 10, QFont.Weight.Bold)
        self._meta_type_badge.setFont(badge_font)

        self._meta_dept_badge = QLabel("", self)
        self._meta_dept_badge.setObjectName("InspectorDeptBadge")
        dept_badge_font = monos_font("Inter", 10, QFont.Weight.Bold)
        self._meta_dept_badge.setFont(dept_badge_font)

        self._meta_version = QLabel("—", self)
        self._meta_version.setProperty("mono", True)
        self._meta_version.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._meta_desc_sep = QLabel("·", self)
        self._meta_desc_sep.setFont(monos_font("Inter", 14, QFont.Weight.Bold))
        self._meta_desc_sep.setStyleSheet(f"color: {MONOS_COLORS['placeholder']};")
        self._meta_desc_sep.setVisible(False)

        self._meta_description = QLabel("", self)
        self._meta_description.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        self._meta_description.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._meta_description.setVisible(False)

        meta_l.addWidget(self._meta_type_badge, 0)
        meta_l.addWidget(self._meta_dept_badge, 0)
        meta_l.addWidget(self._meta_version, 0)
        meta_l.addWidget(self._meta_desc_sep, 0)
        meta_l.addWidget(self._meta_description, 0)
        meta_l.addStretch(1)

        l.addWidget(self._name, 0)
        l.addWidget(meta_row, 0)

        self._dcc_badges_row = QWidget(self)
        self._dcc_badges_l = QHBoxLayout(self._dcc_badges_row)
        self._dcc_badges_l.setContentsMargins(0, 4, 0, 0)
        self._dcc_badges_l.setSpacing(4)
        self._dcc_badges_row.setVisible(False)
        self._dcc_chip_buttons: list[QToolButton] = []
        self._dcc_more_label: QLabel | None = None  # "+N" khi số badge vượt giới hạn
        l.addWidget(self._dcc_badges_row, 0)

    def set_item(
        self,
        item: ViewItem,
        show_publish: bool = False,
        active_department: str | None = None,
        active_dcc_id: str | None = None,
    ) -> None:
        self._name.setText(_inspector_primary_name_text(display_name_for_item(item)))
        ref = item.ref

        # Resolve short_name from pipeline metadata via parent walk
        type_short_map: dict[str, str] = {}
        registry = None
        label_resolver = None
        p = self.parent()
        while p:
            if getattr(p, "_department_registry", None) is not None:
                registry = getattr(p, "_department_registry", None)
            if getattr(p, "_department_label_resolver", None) is not None:
                label_resolver = getattr(p, "_department_label_resolver", None)
            m = getattr(p, "_type_short_name_map", None)
            if isinstance(m, dict) and m and not type_short_map:
                type_short_map = m
            if registry is not None and label_resolver is not None:
                break
            p = p.parent()

        # Type badge: prefer short_name, fall back to type_id
        if isinstance(ref, Asset) and (ref.asset_type or "").strip():
            type_id = (ref.asset_type or "").strip()
            badge_text = (type_short_map.get(type_id) or type_id).upper()
        else:
            badge_text = item.kind.value.upper()

        self._meta_type_badge.setText(badge_text)
        self._meta_type_badge.setStyleSheet(
            f"padding: 1px 6px; border-radius: 4px; "
            f"background: rgba(255,255,255,0.08); color: {MONOS_COLORS['text_label']}; "
            f"font-size: 10px;"
        )

        # Department badge: chỉ hiện khi có department đang chọn (asset/shot) hoặc item là Department
        dept_str = "—"
        show_dept_badge = False
        if isinstance(ref, Department):
            raw = ref.name or "—"
            dept_str = (_department_display_name(raw, label_resolver) if raw != "—" else "—")
            show_dept_badge = True
        elif isinstance(ref, (Asset, Shot)):
            active_key = (active_department or "").strip().casefold()
            if active_key:
                matched_id = None
                for d in ref.departments:
                    dn = (getattr(d, "name", None) or "").strip()
                    if dn and dn.casefold() == active_key:
                        matched_id = dn
                        break
                if matched_id is None and registry is not None and hasattr(registry, "get_departments"):
                    for did in registry.get_departments():
                        if (did or "").strip().casefold() == active_key:
                            matched_id = (did or "").strip()
                            break
                if matched_id:
                    dept_str = _department_display_name(matched_id, label_resolver)
                    show_dept_badge = True

        self._meta_dept_badge.setVisible(show_dept_badge)
        dept_badge_text = dept_str.replace("_", " ").title() if dept_str != "—" else "—"
        self._meta_dept_badge.setText(dept_badge_text)
        self._meta_dept_badge.setStyleSheet(
            f"padding: 1px 6px; border-radius: 4px; "
            f"background: rgba(255,255,255,0.08); color: {MONOS_COLORS['text_label']}; "
            f"font-size: 10px;"
        )

        # Version (đồng bộ với main view: theo active DCC khi có)
        self._current_item = item
        self._active_department = (active_department or "").strip() or None
        self._active_dcc_id = (active_dcc_id or "").strip() or None
        self._show_publish = show_publish

        version = "—"
        if isinstance(ref, Department):
            if ref.latest_publish_version and _V_RE.match(ref.latest_publish_version):
                version = (ref.latest_publish_version or "").upper()
        elif isinstance(ref, (Asset, Shot)):
            # Lấy trực tiếp từ main view: một nguồn duy nhất, đã hỗ trợ suffix (vd _v005_fixNecklace)
            from monostudio.ui_qt.main_view import _card_version_for_display
            version_str = _card_version_for_display(
                ref, self._active_department, show_publish, active_dcc_id=self._active_dcc_id
            )
            version = version_str if (version_str and version_str != "—") else "—"

        self._meta_version.setText(version)

        # Description suffix from work file (e.g. _v005_fixNecklace -> "fixNecklace")
        desc = ""
        if isinstance(ref, (Asset, Shot)) and not show_publish:
            work_path = _path_for_version(item, self._active_department, self._active_dcc_id)
            desc = _description_from_work_path(work_path)
        self._meta_desc_sep.setVisible(bool(desc))
        self._meta_description.setText(desc)
        self._meta_description.setVisible(bool(desc))

        # DCC badges (chỉ khi asset/shot + department đang focus, không phải publish mode)
        self._update_dcc_badges()

    def _update_dcc_badges(self) -> None:
        """Build DCC badge chips for current item + active department; sync active state with main view."""
        while self._dcc_badges_l.count():
            it = self._dcc_badges_l.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._dcc_chip_buttons.clear()
        self._dcc_more_label = None

        item = self._current_item
        ref = getattr(item, "ref", None) if item else None
        dep = self._active_department
        if not item or not isinstance(ref, (Asset, Shot)) or self._show_publish:
            self._dcc_badges_row.setVisible(False)
            return

        try:
            reg = get_default_dcc_registry()
        except Exception:
            self._dcc_badges_row.setVisible(False)
            return

        active_key = (dep or "").strip().casefold()
        states = getattr(ref, "dcc_work_states", ()) or ()
        seen: set[tuple[str, str]] = set()
        badges: list[tuple[str, str, str]] = []  # (dcc_id, status, dept_id)

        for (dept_id, dcc_id), _state in states:
            dept_id = (dept_id or "").strip()
            dcc_id = (dcc_id or "").strip()
            if not dept_id or not dcc_id:
                continue
            if active_key and (dept_id or "").casefold() != active_key:
                continue
            if (dept_id, dcc_id) in seen:
                continue
            seen.add((dept_id, dcc_id))
            status = resolve_dcc_status(ref, dept_id, dcc_id)
            if status in ("exists", "creating"):
                badges.append((dcc_id, status, dept_id))

        dre = None
        p = self.parent()
        while p:
            for attr in ("_department_registry", "_dept_reg"):
                r = getattr(p, attr, None)
                if r is not None and hasattr(r, "supported_dcc_ids"):
                    dre = r
                    break
            if dre is not None:
                break
            p = p.parent()

        for d in getattr(ref, "departments", ()) or ():
            dn = (getattr(d, "name", None) or "").strip()
            if active_key and (dn or "").casefold() != active_key:
                continue
            if dre is not None:
                dcc_loop = dre.supported_dcc_ids(reg, dn)
            else:
                dcc_loop = reg.get_available_dccs(dn) or []
            for dcc_id in dcc_loop:
                dcc_id = (dcc_id or "").strip()
                if not dcc_id or (dn, dcc_id) in seen:
                    continue
                status = resolve_dcc_status(ref, dn, dcc_id)
                if status == "creating":
                    badges.append((dcc_id, status, dn))
                    seen.add((dn, dcc_id))

        chip_size = 32
        icon_size = 28
        amber_border_rgba = "rgba(251, 191, 36, 0.45)"  # amber_400 với alpha thấp

        if not badges:
            # Một badge rỗng (icon trống) khi không có DCC nào
            btn = QToolButton(self._dcc_badges_row)
            btn.setCursor(Qt.ArrowCursor)
            btn.setToolTip("No DCC")
            btn.setAutoRaise(True)
            btn.setFixedSize(chip_size, chip_size)
            btn.setProperty("dcc_id", "")
            btn.setIcon(QIcon())  # icon rỗng
            btn.setIconSize(QSize(icon_size, icon_size))
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            btn.setStyleSheet(
                "border-radius: 16px; border: 1px solid transparent; background-color: #0Fffffff; padding: 4px; margin: 0;"
            )
            btn.setEnabled(False)
            self._dcc_badges_l.addWidget(btn, 0)
            self._dcc_chip_buttons.append(btn)
            self._dcc_badges_l.addStretch(1)
            self._dcc_badges_row.setVisible(True)
            return

        # Giới hạn số badge hiển thị (khi không chọn department có thể rất nhiều)
        _MAX_DCC_BADGES_VISIBLE = 8
        badges_to_show = badges[: _MAX_DCC_BADGES_VISIBLE]
        overflow_count = len(badges) - len(badges_to_show)

        # Inspector badge: chip 32px, icon to (28px), padding tối thiểu; border active dùng rgba để giảm alpha
        active_dcc = self._active_dcc_id or (badges[0][0] if badges else None)
        for dcc_id, status, dept_id in badges_to_show:
            try:
                info = reg.get_dcc_info(dcc_id)
                info = info if isinstance(info, dict) else None
            except Exception:
                info = None
            dcc_name = (info or {}).get("label") if isinstance(info, dict) else None
            dcc_name = str(dcc_name).strip() if dcc_name else (dcc_id or "—")
            dept_display = (dept_id or "").replace("_", " ").strip().title() or "—"
            tooltip_text = f"{dcc_name} — {dept_display}"

            btn = QToolButton(self._dcc_badges_row)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tooltip_text)
            btn.setAutoRaise(True)
            btn.setFixedSize(chip_size, chip_size)
            btn.setProperty("dcc_id", dcc_id)
            is_active = (active_dcc or "").strip().casefold() == (dcc_id or "").strip().casefold()
            if status == "creating":
                btn.setText("…")
                btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
                btn.setStyleSheet(
                    "border-radius: 16px; border: 1px solid %s; color: %s; font-size: 12px;"
                    % (MONOS_COLORS["text_meta"], MONOS_COLORS["text_meta"])
                )
            else:
                slug = (info or {}).get("brand_icon_slug") if isinstance(info, dict) else None
                color = (info or {}).get("brand_color_hex") if isinstance(info, dict) else None
                if isinstance(slug, str) and slug.strip():
                    icon = brand_icon(slug.strip(), size=icon_size, color_hex=color if isinstance(color, str) else None)
                else:
                    icon = lucide_icon("layers", size=icon_size, color_hex=MONOS_COLORS["text_label"])
                btn.setIcon(icon)
                btn.setIconSize(QSize(icon_size, icon_size))
                btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
                if is_active:
                    btn.setStyleSheet(
                        "border-radius: 16px; border: 2px solid %s; background-color: #66000000; padding: 4px; margin: 0;"
                        % amber_border_rgba
                    )
                else:
                    btn.setStyleSheet(
                        "border-radius: 16px; border: 1px solid transparent; background-color: #0Fffffff; padding: 4px; margin: 0;"
                    )
            btn.clicked.connect(lambda checked=False, did=dcc_id: self._on_dcc_badge_clicked(did))
            btn.installEventFilter(self)
            self._dcc_badges_l.addWidget(btn, 0)
            self._dcc_chip_buttons.append(btn)

        # "+N" khi vượt quá _MAX_DCC_BADGES_VISIBLE; tooltip liệt kê các DCC còn lại
        if overflow_count > 0:
            more_lines = []
            for dcc_id, _status, dept_id in badges[_MAX_DCC_BADGES_VISIBLE:]:
                try:
                    info = reg.get_dcc_info(dcc_id)
                    dcc_name = (info or {}).get("label", dcc_id) if isinstance(info, dict) else dcc_id
                except Exception:
                    dcc_name = dcc_id
                dept_display = (dept_id or "").replace("_", " ").strip().title() or "—"
                more_lines.append(f"{dcc_name} — {dept_display}")
            self._dcc_more_label = QLabel(f"+{overflow_count}", self._dcc_badges_row)
            self._dcc_more_label.setStyleSheet(
                f"color: {MONOS_COLORS['text_meta']}; font-size: 11px; padding: 0 4px;"
            )
            self._dcc_more_label.setToolTip("\n".join(more_lines) if more_lines else f"+{overflow_count} more")
            self._dcc_badges_l.addWidget(self._dcc_more_label, 0)

        self._dcc_badges_l.addStretch(1)
        self._dcc_badges_row.setVisible(True)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        """Hiện tooltip giống MainView: QToolTip.showText(globalPos) thay vì tooltip mặc định."""
        if watched in self._dcc_chip_buttons:
            if event.type() == QEvent.ToolTip:
                pos = getattr(event, "globalPos", None)
                if callable(pos):
                    pos = pos()
                if pos is None:
                    pos = watched.mapToGlobal(watched.rect().center())
                text = (watched.toolTip() or "").strip()
                if text:
                    QToolTip.showText(pos, text)
                return True
            if event.type() == QEvent.MouseButtonDblClick:
                dcc_id = watched.property("dcc_id")
                if isinstance(dcc_id, str) and dcc_id.strip():
                    self._open_work_file_for_dcc(dcc_id.strip())
                return True
        return super().eventFilter(watched, event)

    def _on_dcc_badge_clicked(self, dcc_id: str) -> None:
        if not self._current_item or not self._active_department:
            return
        path = getattr(self._current_item, "path", None)
        if not path:
            return
        # Single write path: emit only; MainWindow calls main_view.set_active_dcc() which persists.
        self._active_dcc_id = dcc_id
        self.active_dcc_changed.emit(path, self._active_department, dcc_id)
        self._update_dcc_badges()

    def _open_work_file_for_dcc(self, dcc_id: str) -> None:
        """Open the latest work file for the given DCC (double-click on badge)."""
        if not self._current_item or not self._active_department:
            return
        try:
            path = _path_for_version(self._current_item, self._active_department, dcc_id)
        except Exception:
            return
        if not path:
            return
        try:
            from pathlib import Path as _Path
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            p = path if isinstance(path, _Path) else _Path(str(path))
            if not p.exists() or not p.is_file():
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        except Exception:
            return


class _InspectorItemNotesBadge(QWidget):
    """Notes entry point: icon + optional open-count chip (left of health)."""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorItemNotesBadge")
        self.setFixedSize(40, 36)
        self._btn = QToolButton(self)
        self._btn.setObjectName("InspectorItemNotesBadgeButton")
        self._btn.setAutoRaise(True)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setIcon(lucide_icon("message-circle", size=18, color_hex=MONOS_COLORS["text_label"]))
        self._btn.setIconSize(QSize(18, 18))
        self._btn.setToolTip("Notes")
        self._btn.clicked.connect(self.clicked.emit)
        self._badge = QLabel(self)
        self._badge.setObjectName("InspectorItemNotesBadgeCount")
        self._badge.hide()
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._btn.setGeometry(6, 4, 28, 28)
        self._badge.setGeometry(self.width() - 16, 0, 16, 16)

    def set_open_count(self, n: int) -> None:
        if n <= 0:
            self._badge.hide()
            self._btn.setToolTip("Notes")
            return
        self._badge.setText("9+" if n > 9 else str(n))
        self._badge.show()
        self._btn.setToolTip(f"Notes ({n} open)")


class _InspectorAssetStatusBlock(QWidget):
    """One container: row1 = Asset info (name+meta) | Status pill; row2 = folder shortcuts."""
    open_asset_folder_clicked = Signal()
    open_work_folder_clicked = Signal()
    open_publish_folder_clicked = Signal()
    open_reference_folder_clicked = Signal()
    open_concept_folder_clicked = Signal()
    copy_reference_path_clicked = Signal()
    copy_concept_path_clicked = Signal()
    item_notes_clicked = Signal(ViewItem)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorAssetStatusBlock")
        self._current_item: ViewItem | None = None
        self._last_show_publish = False
        self._last_active_department: str | None = None
        self._last_active_dcc_id: str | None = None
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        row1 = QWidget(self)
        row1_l = QHBoxLayout(row1)
        row1_l.setContentsMargins(0, 0, 0, 0)
        row1_l.setSpacing(12)
        self._identity = _IdentityBlock(self)
        self._notes_badge = _InspectorItemNotesBadge(self)
        self._notes_badge.clicked.connect(self._on_notes_badge_clicked)
        self._health = _ProductionHealth(self)
        row1_l.addWidget(self._identity, 1)
        row1_l.addWidget(self._notes_badge, 0, Qt.AlignVCenter)
        row1_l.addWidget(self._health, 0, Qt.AlignVCenter)

        row2 = QWidget(self)
        row2_l = QHBoxLayout(row2)
        row2_l.setContentsMargins(0, 0, 0, 0)
        row2_l.setSpacing(8)
        self._quick_actions_btn = QToolButton(row2)
        self._quick_actions_btn.setText("Quick actions")
        self._quick_actions_btn.setCursor(Qt.PointingHandCursor)
        self._quick_actions_btn.setAutoRaise(True)
        self._quick_actions_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._quick_actions_btn.setPopupMode(QToolButton.InstantPopup)
        self._quick_actions_btn.setIcon(lucide_icon("zap", size=16, color_hex=MONOS_COLORS["text_label"]))

        menu = QMenu(self._quick_actions_btn)
        menu.setObjectName("InspectorQuickActionsMenu")

        self._act_open_asset_folder = QAction(
            lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Open Asset Folder",
            menu,
        )
        self._act_open_asset_folder.triggered.connect(self._on_open_asset_folder_clicked)
        menu.addAction(self._act_open_asset_folder)

        self._act_open_work_folder = QAction(
            lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Open Work Folder",
            menu,
        )
        self._act_open_work_folder.triggered.connect(self._on_open_work_folder_clicked)
        menu.addAction(self._act_open_work_folder)

        self._act_open_publish_folder = QAction(
            lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Open Publish Folder",
            menu,
        )
        self._act_open_publish_folder.triggered.connect(self._on_open_publish_folder_clicked)
        menu.addAction(self._act_open_publish_folder)

        self._act_open_reference_folder = QAction(
            lucide_icon("eye", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Open Reference Folder",
            menu,
        )
        self._act_open_reference_folder.triggered.connect(self._on_open_reference_folder_clicked)
        menu.addAction(self._act_open_reference_folder)

        self._act_open_concept_folder = QAction(
            lucide_icon("lightbulb", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Open Concept Folder",
            menu,
        )
        self._act_open_concept_folder.triggered.connect(self._on_open_concept_folder_clicked)
        menu.addAction(self._act_open_concept_folder)

        menu.addSeparator()

        self._act_copy_dcc_work_path = QAction(
            lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Copy DCC Work File Path",
            menu,
        )
        self._act_copy_dcc_work_path.triggered.connect(self._on_copy_dcc_work_path_clicked)
        menu.addAction(self._act_copy_dcc_work_path)

        self._act_copy_publish_path = QAction(
            lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Copy Publish Folder Path",
            menu,
        )
        self._act_copy_publish_path.triggered.connect(self._on_copy_publish_folder_path_clicked)
        menu.addAction(self._act_copy_publish_path)

        self._act_copy_reference_path = QAction(
            lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Copy Reference Folder Path",
            menu,
        )
        self._act_copy_reference_path.triggered.connect(self._on_copy_reference_path_clicked)
        menu.addAction(self._act_copy_reference_path)

        self._act_copy_concept_path = QAction(
            lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Copy Concept Folder Path",
            menu,
        )
        self._act_copy_concept_path.triggered.connect(self._on_copy_concept_path_clicked)
        menu.addAction(self._act_copy_concept_path)

        self._quick_actions_btn.setMenu(menu)
        row2_l.addWidget(self._quick_actions_btn, 0)
        row2_l.addStretch(1)

        l.addWidget(row1, 0)
        l.addWidget(row2, 0)

    def sync_action_shortcuts(self, settings: QSettings | None) -> None:
        from monostudio.ui_qt.app_hotkeys import read_hotkey_sequence

        if settings is None:
            self._act_open_reference_folder.setShortcut(QKeySequence())
            self._act_open_concept_folder.setShortcut(QKeySequence())
            return
        self._act_open_reference_folder.setShortcut(
            read_hotkey_sequence(settings, "inspector.open_reference")
        )
        self._act_open_concept_folder.setShortcut(
            read_hotkey_sequence(settings, "inspector.open_concept")
        )

    def _on_open_asset_folder_clicked(self) -> None:
        self.open_asset_folder_clicked.emit()

    def _on_open_work_folder_clicked(self) -> None:
        self.open_work_folder_clicked.emit()

    def _on_open_publish_folder_clicked(self) -> None:
        self.open_publish_folder_clicked.emit()

    def _on_open_reference_folder_clicked(self) -> None:
        self.open_reference_folder_clicked.emit()

    def _on_open_concept_folder_clicked(self) -> None:
        self.open_concept_folder_clicked.emit()

    def _on_copy_reference_path_clicked(self) -> None:
        self.copy_reference_path_clicked.emit()

    def _on_copy_concept_path_clicked(self) -> None:
        self.copy_concept_path_clicked.emit()

    def _on_notes_badge_clicked(self) -> None:
        item = self._current_item
        if item is not None and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            self.item_notes_clicked.emit(item)

    def refresh_notes_display(self, item: ViewItem | None = None) -> None:
        """Notes entry is on the preview thumbnail; keep row badge hidden."""
        self._notes_badge.setVisible(False)

    def set_item(
        self,
        item: ViewItem,
        show_publish: bool = False,
        active_department: str | None = None,
        active_dcc_id: str | None = None,
    ) -> None:
        self._current_item = item
        self._last_show_publish = show_publish
        self._last_active_department = active_department
        self._last_active_dcc_id = active_dcc_id
        self._identity.set_item(item, show_publish, active_department=active_department, active_dcc_id=active_dcc_id)
        self._health.set_focused_department(active_department)
        self._health.set_active_dcc(active_dcc_id)
        self._health.set_item(item)
        is_asset_or_shot = bool(item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))
        self._quick_actions_btn.setEnabled(is_asset_or_shot)
        self._act_open_asset_folder.setEnabled(is_asset_or_shot)
        self._act_open_work_folder.setEnabled(is_asset_or_shot)
        self._act_open_publish_folder.setEnabled(is_asset_or_shot)
        self._act_open_reference_folder.setEnabled(is_asset_or_shot)
        self._act_open_concept_folder.setEnabled(is_asset_or_shot)
        self._act_copy_dcc_work_path.setEnabled(is_asset_or_shot)
        self._act_copy_publish_path.setEnabled(is_asset_or_shot)
        self._act_copy_reference_path.setEnabled(is_asset_or_shot)
        self._act_copy_concept_path.setEnabled(is_asset_or_shot)
        self.refresh_notes_display(item if is_asset_or_shot else None)

    def _on_copy_dcc_work_path_clicked(self) -> None:
        item = self._current_item
        if not item or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        try:
            path = _path_for_version(item, self._last_active_department, self._last_active_dcc_id)
        except Exception:
            path = None
        if not path:
            return
        _TechnicalSpecs._copy_text(str(path))

    def _on_copy_publish_folder_path_clicked(self) -> None:
        item = self._current_item
        if not item or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        ref = getattr(item, "ref", None)
        if not isinstance(ref, (Asset, Shot)):
            return
        dep = (self._last_active_department or "").strip()
        if not dep:
            return
        reg = _inspector_department_registry_from_widget(self)
        paths = _inspector_work_and_publish_paths(ref, dep, reg)
        if paths:
            _TechnicalSpecs._copy_text(str(Path(paths[1])))

    def set_focused_department(self, dept_name: str | None) -> None:
        self._last_active_department = dept_name
        self._health.set_focused_department(dept_name)

    def set_hidden_departments(self, hidden: set[str]) -> None:
        self._health.set_hidden_departments(hidden)

    def update_identity(self, item: ViewItem) -> None:
        self._identity.set_item(
            item,
            self._last_show_publish,
            active_department=self._last_active_department,
            active_dcc_id=self._last_active_dcc_id,
        )

    def update_status(self, item: ViewItem) -> None:
        self._health.set_item(item)


class _MiniInfoCard(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorMiniCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(6)

        hdr = QLabel(title, self)
        hdr.setObjectName("InspectorMiniCardTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        hdr.setFont(f)
        hdr.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._body = QWidget(self)
        self._body_l = QHBoxLayout(self._body)
        self._body_l.setContentsMargins(0, 0, 0, 0)
        self._body_l.setSpacing(8)

        l.addWidget(hdr, 0)
        l.addWidget(self._body, 0)


class _ProductionHealth(QWidget):
    """Computes item health for the focused department; drives the preview thumbnail health icon."""

    health_changed = Signal(object)  # ItemHealth | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorProductionHealth")
        self._current_item: ViewItem | None = None
        self._focused_department: str | None = None
        self._active_dcc_id: str | None = None
        self._hidden_departments: set[str] = set()

        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        # No visible child; global status is rendered inside the thumbnail via _PreviewWidget.
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        l.addWidget(spacer)

    @staticmethod
    def _project_root_from_parent(widget: QWidget | None) -> Path | None:
        p = widget.parent() if widget is not None else None
        while p is not None:
            r = getattr(p, "_project_root", None)
            if r is not None:
                return Path(r) if r else None
            p = p.parent()
        return None

    def set_focused_department(self, dept_name: str | None) -> None:
        self._focused_department = (dept_name or "").strip() or None
        if self._current_item is not None:
            self._refresh()

    def set_active_dcc(self, dcc_id: str | None) -> None:
        self._active_dcc_id = (dcc_id or "").strip() or None
        if self._current_item is not None:
            self._refresh()

    def set_hidden_departments(self, hidden: set[str]) -> None:
        self._hidden_departments = set(hidden)
        if self._current_item is not None:
            self._refresh()

    def set_item(self, item: ViewItem) -> None:
        self._current_item = item
        self._refresh()

    def _refresh(self) -> None:
        item = self._current_item
        dep = self._focused_department
        if item is None or not dep:
            self.health_changed.emit(None)
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            self.health_changed.emit(None)
            return
        health = assess_view_item_health(ref, dep, active_dcc_id=self._active_dcc_id)
        self.health_changed.emit(health)


def _dept_status_pill_qss(text_color: str) -> str:
    """QLabel#InspectorStatusPill base + hover (requires WA_Hover on the label)."""
    return (
        f"QLabel#InspectorStatusPill {{ padding: 1px 8px; border-radius: 12px; "
        f"border: 1px solid transparent; background-color: rgba(255,255,255,0.06); color: {text_color}; }}"
        f"QLabel#InspectorStatusPill:hover {{ background-color: rgba(255,255,255,0.12); "
        f"border: 1px solid rgba(255,255,255,0.14); }}"
    )


class _DeptCard(QFrame):
    clicked = Signal()
    production_status_override_requested = Signal(str, object)  # dept_name, status_id | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDeptCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setProperty("focused", False)
        self.setProperty("sidebarFocused", False)

        l = QVBoxLayout(self)
        l.setContentsMargins(8, 4, 8, 4)
        l.setSpacing(0)

        row = QWidget(self)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        # Give more breathing room between label / status pill / folder icon
        row_l.setSpacing(10)

        self._icon_label = QLabel("", self)
        self._icon_label.setFixedSize(14, 14)
        self._icon_label.setAlignment(Qt.AlignCenter)

        self._name = QLabel("", self)
        self._name.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        f = monos_font("Inter", 11, QFont.Weight.Medium)
        self._name.setFont(f)

        self._pill = QLabel("", self)
        self._pill.setObjectName("InspectorStatusPill")
        pill_font = monos_font("Inter", 9, QFont.Weight.DemiBold)
        self._pill.setFont(pill_font)
        self._pill.setAttribute(Qt.WA_Hover, True)
        self._pill.setStyleSheet(_dept_status_pill_qss(MONOS_COLORS["text_label"]))
        self._pill.installEventFilter(self)

        self._btn_open = QToolButton(self)
        self._btn_open.setObjectName("InspectorDeptOpenButton")
        self._btn_open.setAutoRaise(True)
        self._btn_open.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_open.setFixedSize(24, 24)
        self._btn_open.setIconSize(QSize(16, 16))
        self._btn_open.setIcon(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_primary"]))
        self._btn_open.setToolTip("Open folder")

        row_l.addWidget(self._icon_label, 0, Qt.AlignVCenter)
        row_l.addWidget(self._name, 1)
        row_l.addWidget(self._pill, 0, Qt.AlignVCenter)
        row_l.addWidget(self._btn_open, 0, Qt.AlignVCenter)

        l.addWidget(row, 0)

        self._dept: Department | None = None
        self._dept_name_key: str = ""
        self._asset_shot_ref: Asset | Shot | None = None
        self._project_root: Path | None = None
        self._btn_open.clicked.connect(self._open_folder)

    def set_department(
        self,
        dept: Department,
        display_name: str | None = None,
        icon_name: str | None = None,
        *,
        ref: Asset | Shot | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._dept = dept
        self._dept_name_key = (dept.name or "").strip()
        self._asset_shot_ref = ref
        self._project_root = project_root
        raw = (display_name or dept.name or "").strip()
        text = raw.replace("_", " ").title() if raw else ""
        self._name.setText(text)
        ico = lucide_icon((icon_name or "").strip() or "layers", size=14, color_hex=MONOS_COLORS["text_label"])
        self._icon_label.setPixmap(ico.pixmap(14, 14))

        show_menu = ref is not None
        self._pill.setCursor(Qt.PointingHandCursor if show_menu else Qt.ArrowCursor)

        try:
            dep_key = (self._dept_name_key or "").strip()
            if dep_key:
                reg = load_status_registry_for_department(project_root, dep_key)
            else:
                reg = load_production_status_registry(project_root)
            oid = (
                override_status_id_for_department(ref, self._dept_name_key)
                if ref is not None and self._dept_name_key
                else None
            )
            eff = effective_status_id_for_department(dept, oid, reg)
            self._pill.setText(reg.label_for(eff))
            col = color_hex_for_status_id(eff, reg)
            self._pill.setStyleSheet(_dept_status_pill_qss(col))
            manual = bool(ref is not None and department_has_status_override(ref, self._dept_name_key))
            src = "Manual" if manual else "From files"
            self._pill.setToolTip(f"{src}: {reg.label_for(eff)}")
        except Exception:
            status = _status_from_department(dept)
            self._pill.setText(_status_display_label(status))
            self._pill.setStyleSheet(_dept_status_pill_qss(_status_color(status)))
            self._pill.setToolTip("")

        dept_root_ok = False
        try:
            dept_root_ok = bool(dept.path.exists() and dept.path.is_dir())
        except OSError:
            dept_root_ok = False
        self._btn_open.setEnabled(dept_root_ok)
        if dept_root_ok:
            self._btn_open.setToolTip("Open department folder")
        else:
            self._btn_open.setToolTip(
                "Department folder is not on disk yet. Use Create New in the main view or create the folder manually."
            )

    def _show_status_menu(self) -> None:
        if self._asset_shot_ref is None or not self._dept_name_key:
            return
        res = pick_production_status_at(
            self,
            self._project_root,
            QCursor.pos(),
            department_id=self._dept_name_key,
        )
        if res is False:
            return
        self.production_status_override_requested.emit(self._dept_name_key, res)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if obj is self._pill and event.type() == QEvent.Type.MouseButtonPress:
            me = event
            if isinstance(me, QMouseEvent) and me.button() == Qt.MouseButton.LeftButton:
                if self._asset_shot_ref is not None and self._dept_name_key:
                    self._show_status_menu()
                    return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # Clicking the card updates Tech row with this department's work path.
        if event and getattr(event, "button", lambda: None)() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_focused(self, focused: bool) -> None:
        self.setProperty("focused", bool(focused))
        try:
            self.style().unpolish(self)
            self.style().polish(self)
        except Exception:
            pass

    def set_sidebar_focused(self, focused: bool) -> None:
        self.setProperty("sidebarFocused", bool(focused))
        try:
            self.style().unpolish(self)
            self.style().polish(self)
        except Exception:
            pass

    def _open_folder(self) -> None:
        from monostudio.core.shell_open import open_folder as shell_open_folder

        if self._dept is None:
            return
        try:
            if not self._dept.path.exists():
                return
        except OSError:
            return
        shell_open_folder(self._dept.path)


_MAX_DEPT_CARDS = 32
_MAX_DEPT_SECTIONS = 16


def _department_display_name(dept_id: str, label_resolver: object | None) -> str:
    """Display name: label from registry if available, else dept_id; title-case."""
    if callable(label_resolver):
        label = label_resolver(dept_id)
        return (label or dept_id or "").strip()
    return (dept_id or "").strip()


class _DeptPipelineList(QWidget):
    clicked_empty = Signal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event and getattr(event, "button", lambda: None)() == Qt.LeftButton:
            # Only treat as "empty click" when the user clicked on blank space
            # (or on the section header label), not when clicking a card/child control.
            pos = getattr(event, "position", None)
            if callable(pos):
                p = pos().toPoint()
            else:
                p = event.pos()
            w = self.childAt(p)
            if w is None:
                self.clicked_empty.emit()
            else:
                # Allow section header label clicks to act like empty clicks.
                try:
                    if isinstance(w, QLabel) and w.objectName() == "InspectorSectionTitle":
                        self.clicked_empty.emit()
                except Exception:
                    pass
        super().mousePressEvent(event)


class _DepartmentPipeline(QWidget):
    manage_clicked = Signal()
    department_focused = Signal(str)
    hidden_departments_changed = Signal(set)
    production_status_override_requested = Signal(object, str, object)  # Path, department, status_id | None

    _SETTINGS_KEY_HIDDEN = "inspector/hidden_departments"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDepartmentPipeline")
        self._settings = QSettings()
        self._hidden_departments: set[str] = set(self._load_hidden())
        self._current_all_dept_ids: list[str] = []
        self._current_item: ViewItem | None = None
        self._focused_dept_id: str | None = None
        self._prev_focused_dept_id: str | None = None
        self._sidebar_focused_dept_id: str | None = None

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        hdr = QWidget(self)
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        hdr_l.setSpacing(8)

        title = QLabel("DEPARTMENTS", self)
        title.setObjectName("InspectorSectionTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._manage_btn = QToolButton(self)
        self._manage_btn.setObjectName("InspectorManageButton")
        self._manage_btn.setText("MANAGE")
        self._manage_btn.setAutoRaise(True)
        self._manage_btn.setCursor(Qt.PointingHandCursor)
        self._manage_btn.setToolTip("Toggle department visibility")
        self._manage_btn.clicked.connect(self._show_manage_menu)

        hdr_l.addWidget(title, 1)
        hdr_l.addWidget(self._manage_btn, 0, Qt.AlignRight)

        self._list = _DeptPipelineList(self)
        self._list_l = QVBoxLayout(self._list)
        self._list_l.setContentsMargins(0, 0, 0, 0)
        self._list_l.setSpacing(4)
        self._list.clicked_empty.connect(self._on_empty_clicked)

        self._section_titles: list[QLabel] = []
        for _ in range(_MAX_DEPT_SECTIONS):
            lbl = QLabel(self._list)
            lbl.setObjectName("InspectorSectionTitle")
            f = monos_font("Inter", 9, QFont.Weight.ExtraBold)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
            lbl.setVisible(False)
            self._section_titles.append(lbl)

        self._dept_cards: list[_DeptCard] = []
        self._dept_card_slots: list[object] = []
        self._dept_card_production_status_connected: list[bool] = []
        for _ in range(_MAX_DEPT_CARDS):
            card = _DeptCard(self._list)
            card.setVisible(False)
            self._dept_cards.append(card)
            self._dept_card_slots.append(None)
            self._dept_card_production_status_connected.append(False)

        self._empty = QLabel("—", self)
        self._empty.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        l.addWidget(hdr, 0)
        l.addWidget(self._list, 0)
        l.addWidget(self._empty, 0)

    def _load_hidden(self) -> list[str]:
        raw = QSettings().value(self._SETTINGS_KEY_HIDDEN, [], list)
        return [s for s in raw if isinstance(s, str) and s.strip()]

    def _save_hidden(self) -> None:
        self._settings.setValue(self._SETTINGS_KEY_HIDDEN, sorted(self._hidden_departments))

    def _show_manage_menu(self) -> None:
        if not self._current_all_dept_ids:
            return
        from PySide6.QtWidgets import QDialog
        from monostudio.ui_qt.sidebar import _FilterPickDialog

        icon_map = self._resolve_icon_map()
        label_resolver = self._resolve_label()
        dept_parent = self._resolve_dept_parent()
        dept_label_by_id = self._resolve_dept_label_map()

        items: list[tuple[str, str, str | None]] = []
        for dept_id in self._current_all_dept_ids:
            display = _department_display_name(dept_id, label_resolver)
            icon_name = icon_map.get(dept_id)
            items.append((dept_id, display, icon_name))

        visible = set(self._current_all_dept_ids) - self._hidden_departments

        dlg = _FilterPickDialog(
            title="Select Departments",
            items=items,
            selected=visible,
            max_selected=None,
            parent=self,
            dept_parent=dept_parent,
            dept_label_by_id=dept_label_by_id,
            list_min_height_px=580,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        picked = set(dlg.selected_items())
        self._hidden_departments = set(self._current_all_dept_ids) - picked
        self._save_hidden()
        self.hidden_departments_changed.emit(self._hidden_departments)
        if self._current_item is not None:
            self.set_item(self._current_item)

    def _resolve_icon_map(self) -> dict[str, str]:
        p = self.parent()
        while p:
            m = getattr(p, "_department_icon_map", None)
            if isinstance(m, dict) and m:
                return m
            p = p.parent()
        return {}

    def _resolve_label(self) -> object | None:
        p = self.parent()
        while p:
            r = getattr(p, "_department_label_resolver", None)
            if r is not None:
                return r
            p = p.parent()
        return None

    def _resolve_registry(self) -> object | None:
        p = self.parent()
        while p:
            r = getattr(p, "_department_registry", None)
            if r is not None:
                return r
            p = p.parent()
        return None

    def _resolve_dept_parent(self) -> dict[str, str]:
        registry = self._resolve_registry()
        if registry is None or not hasattr(registry, "get_parent"):
            return {}
        out: dict[str, str] = {}
        for dept_id in self._current_all_dept_ids:
            parent = registry.get_parent(dept_id)
            if parent:
                out[dept_id] = parent
        return out

    def _resolve_dept_label_map(self) -> dict[str, str]:
        resolver = self._resolve_label()
        if not callable(resolver):
            return {}
        out: dict[str, str] = {}
        for dept_id in self._current_all_dept_ids:
            label = resolver(dept_id)
            if label:
                out[dept_id] = label
        registry = self._resolve_registry()
        if registry and hasattr(registry, "get_parent"):
            for dept_id in self._current_all_dept_ids:
                parent = registry.get_parent(dept_id)
                if parent and parent not in out:
                    label = resolver(parent)
                    if label:
                        out[parent] = label
        return out

    def _inspector_project_root(self) -> Path | None:
        p = self.parent()
        while p is not None:
            r = getattr(p, "_project_root", None)
            if r is not None:
                return Path(r) if r else None
            p = p.parent()
        return None

    def _emit_production_status_override_for_card(self, dept_name: str, status_id: object) -> None:
        item = self._current_item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.production_status_override_requested.emit(item.path, dept_name, status_id)

    def set_sidebar_focus(self, dept_name: str | None) -> None:
        """Highlight the department that is focused from the sidebar (persistent, yellow border)."""
        self._sidebar_focused_dept_id = (dept_name or "").strip() or None
        for c in self._dept_cards:
            if not c.isVisible():
                continue
            d = getattr(c, "_dept", None)
            cid = getattr(d, "name", None) if d is not None else None
            c.set_sidebar_focused(bool(self._sidebar_focused_dept_id and cid == self._sidebar_focused_dept_id))

    def set_item(self, item: ViewItem) -> None:
        self._current_item = item
        ref = item.ref

        registry = None
        label_resolver = None
        icon_map: dict[str, str] = {}
        p = self.parent()
        while p:
            if getattr(p, "_department_registry", None) is not None:
                registry = getattr(p, "_department_registry", None)
            if getattr(p, "_department_label_resolver", None) is not None:
                label_resolver = getattr(p, "_department_label_resolver", None)
            m = getattr(p, "_department_icon_map", None)
            if isinstance(m, dict) and m and not icon_map:
                icon_map = m
            if registry is not None and label_resolver is not None:
                break
            p = p.parent()

        pr_root = self._inspector_project_root()
        ref_as = ref if isinstance(ref, (Asset, Shot)) else None
        allowed_ids: list[str] | None = None
        if ref_as is not None:
            allowed_ids = _inspector_allowed_department_ids(ref_as, pr_root)

        if isinstance(ref, Department):
            depts = (ref,)
        elif isinstance(ref, (Asset, Shot)):
            if isinstance(registry, DepartmentRegistry):
                depts = _inspector_merge_departments_with_registry(
                    ref, registry, allowed_dept_ids=allowed_ids
                )
            else:
                depts = ref.departments
        else:
            depts = ()

        if not depts:
            self._current_all_dept_ids = []
            while self._list_l.count():
                self._list_l.takeAt(0)
            for c in self._dept_cards:
                c.setVisible(False)
            for s in self._section_titles:
                s.setVisible(False)
            self._empty.setVisible(True)
            return

        self._empty.setVisible(False)

        if allowed_ids is not None:
            ordered_ids = list(allowed_ids)
        elif registry and hasattr(registry, "get_departments"):
            ordered_ids = list(registry.get_departments())
        else:
            ordered_ids = []
        if ordered_ids:
            dept_by_id = {d.name: d for d in depts}
            ordered_depts = [dept_by_id[dept_id] for dept_id in ordered_ids if dept_id in dept_by_id]
        else:
            ordered_depts = list(depts)

        self._current_all_dept_ids = [d.name for d in ordered_depts if d.name]

        if self._focused_dept_id and self._focused_dept_id not in self._current_all_dept_ids:
            self._focused_dept_id = None
        if self._prev_focused_dept_id and self._prev_focused_dept_id not in self._current_all_dept_ids:
            self._prev_focused_dept_id = None
        if self._sidebar_focused_dept_id and self._sidebar_focused_dept_id not in self._current_all_dept_ids:
            self._sidebar_focused_dept_id = None

        visible_depts = [d for d in ordered_depts if d.name not in self._hidden_departments]

        # Hide top-level departments that only act as parents for subdepartments.
        parent_ids_with_visible_children: set[str] = set()
        if registry and hasattr(registry, "get_parent"):
            for d in visible_depts:
                dept_id = d.name or ""
                parent_id = registry.get_parent(dept_id)
                if parent_id:
                    parent_ids_with_visible_children.add(parent_id)

        rows: list[tuple[str, object]] = []
        sections_emitted: set[str] = set()
        for d in visible_depts:
            dept_id = d.name or ""
            parent_id = registry.get_parent(dept_id) if registry and hasattr(registry, "get_parent") else None

            # If this department is a parent for any visible subdepartments,
            # skip rendering its own card and only show the subdepartments.
            if dept_id in parent_ids_with_visible_children:
                continue

            # Always emit a section title to visually separate groups.
            # - If the dept has a parent: section = parent
            # - Else: section = the dept itself (standalone)
            section_id = parent_id or dept_id
            if section_id and section_id not in sections_emitted:
                if registry and hasattr(registry, "get_department_label"):
                    section_label = (registry.get_department_label(section_id) or section_id).strip()
                else:
                    section_label = (section_id or "").strip()
                # Capitalize only the first character; rest lower-case.
                if section_label:
                    section_label = section_label[:1].upper() + section_label[1:].lower()
                if section_label:
                    rows.append(("section", section_label))
                    sections_emitted.add(section_id)
            rows.append(("dept", d))

        for i, card in enumerate(self._dept_cards):
            slot = self._dept_card_slots[i] if i < len(self._dept_card_slots) else None
            if slot is not None:
                try:
                    card.clicked.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
                self._dept_card_slots[i] = None
            if i < len(self._dept_card_production_status_connected) and self._dept_card_production_status_connected[i]:
                try:
                    card.production_status_override_requested.disconnect(self._emit_production_status_override_for_card)
                except (TypeError, RuntimeError):
                    pass
                self._dept_card_production_status_connected[i] = False

        while self._list_l.count():
            self._list_l.takeAt(0)

        section_idx = 0
        card_idx = 0
        for typ, data in rows:
            if typ == "section":
                if section_idx >= len(self._section_titles):
                    break
                w = self._section_titles[section_idx]
                w.setText(str(data))
                w.setVisible(True)
                self._list_l.addWidget(w, 0)
                section_idx += 1
            else:
                if card_idx >= len(self._dept_cards):
                    break
                d = data
                card = self._dept_cards[card_idx]
                display_name = _department_display_name(d.name or "", label_resolver)
                dept_icon_name = icon_map.get(d.name or "", "layers")
                card.set_department(d, display_name, dept_icon_name, ref=ref_as, project_root=pr_root)
                card.set_sidebar_focused(bool(self._sidebar_focused_dept_id and (d.name or "") == self._sidebar_focused_dept_id))
                card.set_focused(bool(self._focused_dept_id and (d.name or "") == self._focused_dept_id))
                card.setVisible(True)
                dept_name = d.name

                def _emit(dept: str) -> None:
                    self._on_dept_clicked(dept)

                slot = lambda _d=dept_name: _emit(_d)
                card.clicked.connect(slot)
                if card_idx < len(self._dept_card_slots):
                    self._dept_card_slots[card_idx] = slot
                card.production_status_override_requested.connect(self._emit_production_status_override_for_card)
                if card_idx < len(self._dept_card_production_status_connected):
                    self._dept_card_production_status_connected[card_idx] = True
                self._list_l.addWidget(card, 0)
                card_idx += 1

        for i in range(section_idx, len(self._section_titles)):
            self._section_titles[i].setVisible(False)
        for i in range(card_idx, len(self._dept_cards)):
            self._dept_cards[i].setVisible(False)

    def _on_dept_clicked(self, dept_id: str | None) -> None:
        dept_id = (dept_id or "").strip() or None
        if dept_id == self._focused_dept_id:
            return
        self._prev_focused_dept_id = self._focused_dept_id
        self._focused_dept_id = dept_id
        # Update focus border for visible cards
        for c in self._dept_cards:
            if not c.isVisible():
                continue
            d = getattr(c, "_dept", None)
            cid = getattr(d, "name", None) if d is not None else None
            c.set_sidebar_focused(bool(self._sidebar_focused_dept_id and cid == self._sidebar_focused_dept_id))
            c.set_focused(bool(self._focused_dept_id and cid == self._focused_dept_id))
        self.department_focused.emit(self._focused_dept_id or "")

    def _on_empty_clicked(self) -> None:
        # Clicking on empty space clears temporary (Inspector) focus.
        # If there is a sidebar-focused department, revert logic focus back to that.
        if self._focused_dept_id is None and not self._sidebar_focused_dept_id:
            return
        self._prev_focused_dept_id = self._focused_dept_id
        self._focused_dept_id = None
        for c in self._dept_cards:
            if not c.isVisible():
                continue
            d = getattr(c, "_dept", None)
            cid = getattr(d, "name", None) if d is not None else None
            c.set_sidebar_focused(bool(self._sidebar_focused_dept_id and cid == self._sidebar_focused_dept_id))
            c.set_focused(bool(self._focused_dept_id and cid == self._focused_dept_id))
        # Emit sidebar-focused department (if any) so Inspector re-syncs Tech/preview/status.
        self.department_focused.emit(self._sidebar_focused_dept_id or "")


class _TechRow(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)
        self._k = QLabel(label, self)
        self._k.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._v = QLabel("—", self)
        self._v.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        l.addWidget(self._k, 1)
        l.addWidget(self._v, 0, Qt.AlignRight)

    def set_value(self, text: str) -> None:
        self._v.setText(text or "—")


class _TechnicalSpecs(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorTechnicalSpecs")
        self._last_item: ViewItem | None = None
        self._resolved_path: Path | None = None

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        title = QLabel("TECHNICAL SPECS", self)
        title.setObjectName("InspectorSectionTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._frame = _TechRow("Frame Range", self)
        self._fps = _TechRow("FPS", self)
        self._res = _TechRow("Resolution", self)
        self._modified = _TechRow("Last Modified", self)

        # Source Directory (monospace, copyable)
        src_row = QWidget(self)
        src_l = QHBoxLayout(src_row)
        src_l.setContentsMargins(0, 0, 0, 0)
        src_l.setSpacing(8)
        k = QLabel("Source Directory", self)
        k.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._src = QLineEdit(self)
        self._src.setReadOnly(True)
        self._src.setProperty("mono", True)
        self._src.setStyleSheet("padding: 6px 8px;")
        btn_copy = QToolButton(self)
        btn_copy.setAutoRaise(True)
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.setIcon(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]))
        btn_copy.setToolTip("Copy")
        btn_copy.clicked.connect(lambda: self._copy_text(self._src.text()))

        src_l.addWidget(k, 1)
        src_l.addWidget(self._src, 0)
        src_l.addWidget(btn_copy, 0)

        l.addWidget(title, 0)
        l.addWidget(self._frame, 0)
        l.addWidget(self._fps, 0)
        l.addWidget(self._res, 0)
        l.addWidget(src_row, 0)
        l.addWidget(self._modified, 0)

    def set_item(self, item: ViewItem) -> None:
        self._last_item = item
        self._resolved_path = None
        self._frame.set_value("—")
        self._fps.set_value("—")
        self._res.set_value("—")
        self._src.setText(str(item.path))

    def set_last_modified(self, text: str) -> None:
        self._modified.set_value(text or "—")

    def set_resolved_path(self, path: Path | None) -> None:
        """Update displayed path to a department work path (or reset to item path when None)."""
        self._resolved_path = path
        if path is not None:
            self._src.setText(str(path))
        elif self._last_item is not None:
            self._src.setText(str(self._last_item.path))

    @staticmethod
    def _copy_text(text: str) -> None:
        if not text:
            return
        cb = QApplication.clipboard()
        if cb is None:
            return
        cb.setText(text)


class _Stakeholders(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorStakeholders")
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)

        btn = QToolButton(self)
        btn.setText("STAKEHOLDERS")
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setArrowType(Qt.RightArrow)
        btn.setAutoRaise(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"color: {MONOS_COLORS['text_meta']}; font-weight: 800; font-size: 10px;")

        self._content = QLabel("—", self)
        self._content.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._content.setVisible(False)

        def sync() -> None:
            open_ = btn.isChecked()
            btn.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
            self._content.setVisible(open_)

        btn.toggled.connect(lambda _checked: sync())
        sync()

        l.addWidget(btn, 0)
        l.addWidget(self._content, 0)

    def set_item(self, _item: ViewItem) -> None:
        # No stakeholders data in current model.
        self._content.setText("—")


# Scope: user chọn trước (global / asset / shot), sau đó destination (script, texture, ...), cuối cùng entity.
_INBOX_SCOPE_PROJECT = "project"
_INBOX_SCOPE_ASSET = "asset"
_INBOX_SCOPE_SHOT = "shot"


class _InboxDestinationBlock(QWidget):
    """Flow: Scope (Global | Asset | Shot) → Destination (icon+label list) → Type → Entity."""

    distribute_finished = Signal(object)  # list[Path] đã distribute

    _SCOPE_ITEMS: list[tuple[str, str, str]] = [
        ("Global", _INBOX_SCOPE_PROJECT, "layers"),
        ("Asset", _INBOX_SCOPE_ASSET, "box"),
        ("Shot", _INBOX_SCOPE_SHOT, "clapperboard"),
    ]

    _DEST_ICON_MAP: dict[str, str] = {
        "global_reference": "eye",
        "reference_script": "file-text",
        "reference_storyboard": "layout-dashboard",
        "reference_guideline": "library",
        "reference_concept": "lightbulb",
        "reference": "eye",
        "concept": "lightbulb",
        "texture": "palette",
        "character_sculpt": "bone",
        "shot_reference": "clapperboard",
    }

    _TYPE_ICON_MAP: dict[str, str] = {
        "character": "user",
        "prop": "package",
        "environment": "trees",
        "vehicle": "car",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InboxDestinationBlock")
        self._paths: list[Path] = []
        self._project_root: Path | None = None
        self._project_index: ProjectIndex | None = None
        self._type_reg: TypeRegistry | None = None
        self._dept_reg: DepartmentRegistry | None = None
        self._destinations: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        title = QLabel("DESTINATION", self)
        title.setObjectName("InboxDestinationTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112.0)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        root.addWidget(title, 0)

        # ── Card WHERE: Scope (toggle buttons) + Destination (selectable list) ──
        card_where = QFrame(self)
        card_where.setObjectName("InboxDestCardWhere")
        card_where.setFrameShape(QFrame.NoFrame)
        card_where.setAttribute(Qt.WA_StyledBackground, True)
        lw = QVBoxLayout(card_where)
        lw.setContentsMargins(12, 12, 12, 12)
        lw.setSpacing(8)

        title_where = QLabel("WHERE", card_where)
        title_where.setObjectName("InboxDestCardTitle")
        lw.addWidget(title_where, 0)

        lw.addWidget(self._make_section_label("Scope", "layers", card_where), 0)
        scope_row = QWidget(card_where)
        scope_lay = QHBoxLayout(scope_row)
        scope_lay.setContentsMargins(0, 0, 0, 0)
        scope_lay.setSpacing(4)
        self._scope_group = QButtonGroup(self)
        self._scope_group.setExclusive(True)
        for label, data, icon_name in self._SCOPE_ITEMS:
            btn = QPushButton(label, scope_row)
            btn.setObjectName("InboxScopeButton")
            btn.setIcon(lucide_icon(icon_name, size=14))
            btn.setCheckable(True)
            btn.setProperty("item_data", data)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._scope_group.addButton(btn)
            scope_lay.addWidget(btn)
        scope_lay.addStretch(1)
        self._scope_group.buttons()[0].setChecked(True)
        lw.addWidget(scope_row, 0)

        lw.addWidget(self._make_section_label("Destination", "folder-open", card_where), 0)
        self._dest_container = QWidget(card_where)
        self._dest_layout = QVBoxLayout(self._dest_container)
        self._dest_layout.setContentsMargins(0, 0, 0, 0)
        self._dest_layout.setSpacing(2)
        self._dest_group = QButtonGroup(self)
        self._dest_group.setExclusive(True)
        lw.addWidget(self._dest_container, 0)

        root.addWidget(card_where, 0)

        # ── Card TARGET: Type (selectable list, asset-only) + Entity (combo) ──
        card_target = QFrame(self)
        card_target.setObjectName("InboxDestCardTarget")
        card_target.setFrameShape(QFrame.NoFrame)
        card_target.setAttribute(Qt.WA_StyledBackground, True)
        lt = QVBoxLayout(card_target)
        lt.setContentsMargins(12, 12, 12, 12)
        lt.setSpacing(8)

        title_target = QLabel("TARGET", card_target)
        title_target.setObjectName("InboxDestCardTitle")
        lt.addWidget(title_target, 0)

        self._type_section = QWidget(card_target)
        ts_lay = QVBoxLayout(self._type_section)
        ts_lay.setContentsMargins(0, 0, 0, 0)
        ts_lay.setSpacing(4)
        ts_lay.addWidget(self._make_section_label("Type", "box", self._type_section))
        self._type_container = QWidget(self._type_section)
        self._type_layout = QVBoxLayout(self._type_container)
        self._type_layout.setContentsMargins(0, 0, 0, 0)
        self._type_layout.setSpacing(2)
        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        ts_lay.addWidget(self._type_container)
        lt.addWidget(self._type_section, 0)

        entity_l = QHBoxLayout()
        entity_l.addWidget(self._make_section_label("Entity", "package", card_target), 0)
        self._entity_combo = QComboBox(card_target)
        self._entity_combo.setObjectName("InboxEntityCombo")
        entity_l.addWidget(self._entity_combo, 1)
        lt.addLayout(entity_l, 0)

        root.addWidget(card_target, 0)

        # ── Card ACTION — placed externally by InspectorPanel (bottom-pinned) ──
        self._card_action = QFrame()
        self._card_action.setObjectName("InboxDestCardAction")
        self._card_action.setFrameShape(QFrame.NoFrame)
        self._card_action.setAttribute(Qt.WA_StyledBackground, True)
        la = QVBoxLayout(self._card_action)
        la.setContentsMargins(12, 12, 12, 12)
        la.setSpacing(8)
        title_action = QLabel("ACTION", self._card_action)
        title_action.setObjectName("InboxDestCardTitle")
        la.addWidget(title_action, 0)
        copy_move_l = QHBoxLayout()
        self._copy_radio = QRadioButton("Copy", self._card_action)
        self._move_radio = QRadioButton("Move", self._card_action)
        self._copy_radio.setChecked(True)
        copy_move_grp = QButtonGroup(self)
        copy_move_grp.addButton(self._copy_radio)
        copy_move_grp.addButton(self._move_radio)
        copy_move_l.addWidget(self._copy_radio, 0)
        copy_move_l.addWidget(self._move_radio, 0)
        copy_move_l.addStretch(1)
        la.addLayout(copy_move_l, 0)
        self._distribute_btn = QPushButton("Distribute", self._card_action)
        self._distribute_btn.setObjectName("InboxDistributeButton")
        self._distribute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._distribute_btn.clicked.connect(self._on_distribute)
        la.addWidget(self._distribute_btn, 0)

        # ── Signals ──
        self._scope_group.buttonClicked.connect(lambda _btn: self._on_scope_selection_changed())
        self._dest_group.buttonClicked.connect(lambda _btn: self._update_distribute_enabled())
        self._type_group.buttonClicked.connect(lambda _btn: self._refill_entity_combo())
        self._entity_combo.currentIndexChanged.connect(self._update_distribute_enabled)

    # ── helpers ──

    @property
    def action_card(self) -> QFrame:
        """The ACTION card widget (Copy/Move + Distribute). Placed externally by InspectorPanel."""
        return self._card_action

    @staticmethod
    def _make_section_label(text: str, icon_name: str, parent: QWidget) -> QWidget:
        """Build an [icon] Label widget for a section header."""
        w = QWidget(parent)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        ic = QLabel(w)
        ic.setPixmap(lucide_icon(icon_name, size=14, color_hex=MONOS_COLORS["text_meta"]).pixmap(14, 14))
        ic.setFixedSize(14, 14)
        lay.addWidget(ic, 0)
        lbl = QLabel(text, w)
        lbl.setObjectName("InboxFieldLabel")
        lay.addWidget(lbl, 0)
        return w

    def _make_item_button(self, label: str, data: str, icon_name: str,
                          obj_name: str, parent: QWidget) -> QPushButton:
        btn = QPushButton(label, parent)
        btn.setObjectName(obj_name)
        btn.setIcon(lucide_icon(icon_name, size=14))
        btn.setCheckable(True)
        btn.setProperty("item_data", data)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    # ── data ──

    def set_data(
        self,
        paths: list[Path],
        project_root: Path | None,
        project_index: ProjectIndex | None,
    ) -> None:
        self._paths = list(paths) if paths else []
        self._project_root = Path(project_root) if project_root else None
        self._project_index = project_index
        try:
            self._type_reg = TypeRegistry.for_project(self._project_root) if self._project_root else None
        except Exception:
            self._type_reg = None
        try:
            self._dept_reg = DepartmentRegistry.for_project(self._project_root) if self._project_root else None
        except Exception:
            self._dept_reg = None
        self._destinations = load_inbox_destinations()
        self._refill_dest_items()
        self._on_scope_changed()
        self._update_distribute_enabled()

    # ── scope ──

    def _current_scope(self) -> str:
        btn = self._scope_group.checkedButton()
        if btn:
            return (btn.property("item_data") or _INBOX_SCOPE_PROJECT).strip().lower()
        return _INBOX_SCOPE_PROJECT

    def _on_scope_selection_changed(self) -> None:
        self._refill_dest_items()
        self._on_scope_changed()

    def _on_scope_changed(self) -> None:
        scope = self._current_scope()
        if scope == _INBOX_SCOPE_PROJECT:
            self._type_section.setVisible(False)
            self._entity_combo.clear()
            self._entity_combo.addItem("Project (global)", None)
            self._update_distribute_enabled()
            return
        if scope == _INBOX_SCOPE_SHOT:
            self._type_section.setVisible(False)
            self._refill_entity_combo()
            self._update_distribute_enabled()
            return
        self._type_section.setVisible(True)
        self._refill_type_items()
        self._refill_entity_combo()
        self._update_distribute_enabled()

    # ── destination buttons ──

    def _refill_dest_items(self) -> None:
        for btn in list(self._dest_group.buttons()):
            self._dest_group.removeButton(btn)
            btn.deleteLater()
        scope = self._current_scope()
        first_btn: QPushButton | None = None
        for d in self._destinations:
            ctx = (d.get("context") or "both").strip().lower()
            match = (
                (scope == _INBOX_SCOPE_PROJECT and ctx == "project")
                or (scope == _INBOX_SCOPE_ASSET and ctx in ("asset", "both"))
                or (scope == _INBOX_SCOPE_SHOT and ctx in ("shot", "both"))
            )
            if not match:
                continue
            did = d.get("id", "")
            icon_name = self._DEST_ICON_MAP.get(did, "folder-open")
            btn = self._make_item_button(
                d.get("label", did), did, icon_name,
                "InboxDestItemButton", self._dest_container,
            )
            self._dest_group.addButton(btn)
            self._dest_layout.addWidget(btn)
            if first_btn is None:
                first_btn = btn
        if first_btn:
            first_btn.setChecked(True)
        self._update_distribute_enabled()

    # ── type buttons ──

    def _refill_type_items(self) -> None:
        for btn in list(self._type_group.buttons()):
            self._type_group.removeButton(btn)
            btn.deleteLater()
        first_btn: QPushButton | None = None
        if self._type_reg:
            for tid in self._type_reg.get_types():
                if (tid or "").lower() == "shot":
                    continue
                label = self._type_reg.get_type_label(tid) or tid
                icon_name = self._TYPE_ICON_MAP.get((tid or "").lower(), "box")
                btn = self._make_item_button(
                    label, tid, icon_name,
                    "InboxTypeItemButton", self._type_container,
                )
                self._type_group.addButton(btn)
                self._type_layout.addWidget(btn)
                if first_btn is None:
                    first_btn = btn
        if first_btn:
            first_btn.setChecked(True)
        self._refill_entity_combo()

    # ── entity combo ──

    def _refill_entity_combo(self) -> None:
        self._entity_combo.clear()
        scope = self._current_scope()
        if scope == _INBOX_SCOPE_PROJECT:
            self._entity_combo.addItem("Project (global)", None)
            self._update_distribute_enabled()
            return
        if not self._project_index:
            self._update_distribute_enabled()
            return
        if scope == _INBOX_SCOPE_SHOT:
            for s in self._project_index.shots:
                self._entity_combo.addItem(f"{s.name} (Shot)", s)
            self._update_distribute_enabled()
            return
        type_btn = self._type_group.checkedButton()
        type_id = type_btn.property("item_data") if type_btn else None
        if self._project_index and type_id:
            for a in self._project_index.assets:
                if (a.asset_type or "").strip().lower() != (type_id or "").strip().lower():
                    continue
                label = self._type_reg.get_type_label(type_id) if self._type_reg else type_id
                self._entity_combo.addItem(f"{a.name} ({label})", a)
        self._update_distribute_enabled()

    # ── distribute ──

    def _update_distribute_enabled(self) -> None:
        has_dest = self._dest_group.checkedButton() is not None
        self._distribute_btn.setEnabled(
            bool(
                self._paths
                and self._project_root
                and has_dest
                and self._entity_combo.count() > 0
            )
        )

    def _on_distribute(self) -> None:
        if not self._paths or not self._project_root:
            return
        dest_btn = self._dest_group.checkedButton()
        dest_id = dest_btn.property("item_data") if dest_btn else None
        entity = self._entity_combo.currentData()
        if not dest_id:
            return
        if entity is not None and not isinstance(entity, (Asset, Shot)):
            return
        move = self._move_radio.isChecked()
        dest_label = dest_btn.text().strip() if dest_btn else (dest_id or "")
        scope = self._current_scope()
        entity_name = ""
        if isinstance(entity, Asset):
            entity_name = getattr(entity, "name", "") or ""
        elif isinstance(entity, Shot):
            entity_name = getattr(entity, "name", "") or ""
        done: list[dict] = []
        for src in self._paths:
            dest_dir = resolve_destination_path(self._project_root, dest_id, entity, self._dept_reg)
            if not dest_dir:
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / src.name
            target_path_str = str(dest_path.resolve()) if dest_path else ""
            try:
                if src.is_dir():
                    if dest_path.exists():
                        shutil.rmtree(dest_path)
                    shutil.copytree(src, dest_path)
                else:
                    shutil.copy2(src, dest_path)
                if move:
                    if src.is_dir():
                        shutil.rmtree(src)
                    else:
                        src.unlink()
                done.append({
                    "path": src,
                    "destination_id": dest_id or "",
                    "destination_label": dest_label,
                    "scope": scope,
                    "entity_name": entity_name,
                    "target_path": target_path_str,
                })
            except OSError:
                pass
        if done:
            self.distribute_finished.emit(done)

