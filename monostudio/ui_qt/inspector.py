from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, QSize, QPoint, QRect, QTimer, QSettings, QEvent, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QDrag,
    QFont,
    QIcon,
    QImage,
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
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, Department, Shot, ProjectIndex
from monostudio.core.inbox_reader import load_inbox_destinations, resolve_destination_path
from monostudio.core.type_registry import TypeRegistry
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.dcc_status import resolve_dcc_status
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.style import MONOS_COLORS, file_icon_spec_for_path, monos_font
from monostudio.ui_qt.inspector_preview_settings import (
    THUMB_SOURCE_RENDER_SEQUENCE,
    default_qsettings,
    read_inspector_thumbnail_open_exe,
    read_inspector_thumbnail_source,
    read_sequence_preview_fps,
)
from monostudio.ui_qt.thumbnails import ThumbnailCache, resolve_thumbnail_path
from monostudio.ui_qt.thumbnail_source_resolve import (
    dept_work_path_for_ref,
    primary_work_file_for_department,
    resolve_entity_thumbnail_source_path,
)
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind, display_name_for_item
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


def _inspector_merge_departments_with_registry(ref: Asset | Shot, registry: DepartmentRegistry) -> tuple[Department, ...]:
    scanned_by_cf: dict[str, Department] = {}
    for d in ref.departments:
        cf = (d.name or "").strip().casefold()
        if cf and cf not in scanned_by_cf:
            scanned_by_cf[cf] = d
    out: list[Department] = []
    for did in registry.get_departments():
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


def _inspector_preview_resolve_sequence(
    work_path: Path | None,
    work_file_path: Path | None,
) -> tuple[Path | None, list[Path]]:
    from monostudio.core.sequence_preview import list_sequence_frames, resolve_sequence_folder

    if work_path is None or not work_path.is_dir():
        return (None, [])
    sq = resolve_sequence_folder(work_path, work_file_path)
    if sq is None or not sq.is_dir():
        return (None, [])
    return (sq, list_sequence_frames(sq))


def _inspector_preview_worker_run(
    path_str: str,
    *,
    is_inbox: bool,
    dept: str | None,
    mode: str,
    work_path_str: str | None,
    work_file_str: str | None,
    decode_max_side: int = 1024,
) -> tuple[str, QImage | None, bool]:
    """Background: load inspector thumb (sequence folder resolved on main thread after apply)."""
    px = max(256, min(1024, int(decode_max_side)))
    p = Path(path_str)
    if is_inbox and p.is_file():
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

    thumb = resolve_entity_thumbnail_source_path(p, dept, mode, wp, wf)
    if thumb is None:
        return (path_str, None, False)
    use_fit = ".user." in str(thumb)
    cache = ThumbnailCache(size_px=px)
    pm = cache.load_thumbnail_pixmap(thumb)
    if (pm is None or pm.isNull()) and thumb.suffix.lower() in (".exr", ".hdr"):
        from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

        img = load_preview_frame_qimage(thumb, max_side=px)
        if img is not None and not img.isNull():
            pm = QPixmap.fromImage(img)
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
        root.addWidget(self._header, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("InspectorScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)

        content = _InspectorContent(self._scroll)
        content.setObjectName("InspectorContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(12, 12, 12, 12)
        self._content_layout.setSpacing(16)

        self._empty = _InspectorEmptyState()
        self._preview = _InspectorPreview()
        self._asset_status = _InspectorAssetStatusBlock()
        self._dept_pipeline = _DepartmentPipeline()
        self._tech = _TechnicalSpecs()
        self._stakeholders = _Stakeholders()

        self._dept_pipeline.manage_clicked.connect(self.manage_departments_requested.emit)
        self._dept_pipeline.department_focused.connect(self._on_department_focused)
        self._dept_pipeline.hidden_departments_changed.connect(self._on_hidden_departments_changed)
        self._preview.paste_requested.connect(self._on_paste_requested)
        self._preview.remove_requested.connect(self._on_remove_requested)
        self._show_publish: bool = False
        self._last_focused_department: str | None = None
        self._asset_status.open_asset_folder_clicked.connect(self._on_open_asset_folder_requested)
        self._asset_status.open_work_folder_clicked.connect(self._on_open_work_folder_requested)
        self._asset_status.open_publish_folder_clicked.connect(self._on_open_publish_folder_requested)
        self._asset_status._identity.active_dcc_changed.connect(self._on_identity_active_dcc_changed)
        # Sync global production status (READY / PROGRESS / WAITING / BLOCKED) to preview thumbnail dot.
        self._asset_status._health.status_changed.connect(
            lambda color, label: self._preview._container._w.set_global_status_indicator(color, label)
        )

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
            self._tech,
            self._stakeholders,
            self._inbox_destination,
        ):
            self._content_layout.addWidget(w, 0)

        self._content_layout.addStretch(1)
        self._inbox_destination.setVisible(False)
        self._scroll.setWidget(content)

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
        self._thumbnail_manager: object | None = None
        self._worker_manager: object | None = None
        self._department_label_resolver: object | None = None  # callable[[str], str] | None
        self._department_registry: object | None = None  # DepartmentRegistry | None (để biết subdepartment, display name)
        self._department_icon_map: dict[str, str] = {}  # dept_id -> lucide icon name
        self._type_short_name_map: dict[str, str] = {}  # type_id -> short_name
        self._inspector_settings: QSettings = default_qsettings()
        self._preview.set_qsettings(self._inspector_settings)
        self.set_item(None)

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

    def set_app_settings(self, settings: QSettings) -> None:
        """Share MainWindow QSettings so Inspector reads the same keys as Settings dialog."""
        self._inspector_settings = settings
        self._preview.set_qsettings(settings)

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
        self._asset_status.setVisible(False)
        self._dept_pipeline.setVisible(False)
        self._tech.setVisible(False)
        self._stakeholders.setVisible(False)
        self._inbox_action_wrapper.setVisible(True)

    def set_inbox_tree_preview(self, path: Path | None) -> None:
        """Inbox/Reference: khi chọn file trong tree → hiện thumb + metadata, ẩn block distribute."""
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

    def set_item(self, item: ViewItem | None, active_department_hint: str | None = None) -> None:
        # Diff-based: never rebuild layout. Update only changed sections; preserve scroll position.
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_inspector_update
            if enabled():
                record_inspector_update("set_item")
        except Exception:
            pass
        prev = self._current_item
        self._previous_item = prev
        self._current_item = item
        has_item = item is not None
        self._empty.setVisible(not has_item)
        for w in (self._preview, self._asset_status, self._dept_pipeline, self._tech, self._stakeholders):
            w.setVisible(has_item)
        if has_item:
            self._inbox_action_wrapper.setVisible(False)

        if item is None:
            self._empty.set_message("Select an item to view details")
            return

        # Đồng bộ department từ sidebar (active_department_hint):
        # - sidebar_focus (vàng) luôn bám theo hint hợp lệ (kể cả department mới chỉ có trong registry)
        # - _last_focused_department dùng cho logic (Tech row, preview, status, DCC)
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
            # Inbox: preview chỉ lấy height theo tỉ lệ ảnh (stretch 0)
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

        if scroll_bar and scroll_bar.value() != scroll_pos:
            scroll_bar.setValue(scroll_pos)

        # Khi có department focus (từ sidebar hoặc Inspector), đảm bảo Tech row + preview
        # được sync ngay cả lần đầu mở Inspector.
        if self._last_focused_department:
            self._on_department_focused(self._last_focused_department)

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

    def _on_department_focused(self, department_name: str) -> None:
        """Update Tech row, status pill, and preview thumbnail with the clicked department."""
        self._last_focused_department = (department_name or "").strip() or None
        self._asset_status.set_focused_department(self._last_focused_department)
        self._preview.set_active_department(self._last_focused_department)
        self._preview.update_thumbnail_only()
        if self._current_item is not None:
            _ad = self._last_focused_department
            _ac = _inspector_get_active_dcc(getattr(self._current_item, "path", None), _ad) if self._current_item else None
            self._asset_status.set_item(self._current_item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        dep = self._last_focused_department
        if not dep:
            self._tech.set_resolved_path(None)
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


def _format_mtime(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel("INSPECTOR", self)
        title.setObjectName("InspectorHeaderTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        title.setFont(f)

        btn = QToolButton(self)
        btn.setObjectName("InspectorCloseButton")
        btn.setAutoRaise(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(lucide_icon("x", size=16, color_hex=MONOS_COLORS["text_label"]))
        btn.clicked.connect(self.close_clicked.emit)

        layout.addWidget(title, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        layout.addWidget(btn, 0, Qt.AlignVCenter)


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
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        # Global production status indicator (small dot top-right, color + tooltip text).
        self._status_color_hex: str | None = None
        self._status_label: str | None = None

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
            self._placeholder_file_icon = ()
        if self._inbox_mode:
            self.updateGeometry()
        self.image_changed.emit(self._has_image)
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

    def set_global_status_indicator(self, color_hex: str | None, label: str | None) -> None:
        """Set global status indicator (dot) color and tooltip label."""
        color_hex = (color_hex or "").strip() or None
        label = (label or "").strip() or None
        if self._status_color_hex == color_hex and self._status_label == label:
            return
        self._status_color_hex = color_hex
        self._status_label = label
        self.update()

    def _draw_status_dot(self, p: QPainter, r: QRect) -> None:
        """Draw small status dot at top-right inside thumbnail, similar to main view."""
        if not self._status_color_hex:
            return
        try:
            color = QColor(self._status_color_hex)
        except Exception:
            return
        # Slightly transparent to match main view accent (≈80% opacity).
        if color.isValid():
            color.setAlpha(204)
        pad = 10
        radius = 6
        cx = r.right() - pad - radius
        cy = r.top() + pad + radius
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPoint(cx, cy), radius, radius)
        p.restore()

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
                # Overlay global status dot on top of image.
                self._draw_status_dot(p, r)
                return

            # Placeholder: icon by kind (asset/shot/project) or letter/em-dash
            p.setClipping(False)
            p.setPen(QPen(QColor(MONOS_COLORS["border"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0, 0, -1, -1), radius, radius)

            if self._placeholder_kind in ("asset", "shot", "project"):
                icon_name = "box" if self._placeholder_kind == "asset" else "clapperboard" if self._placeholder_kind == "shot" else "layout-dashboard"
                icon = lucide_icon(icon_name, size=64, color_hex=MONOS_COLORS["text_meta"])
                src = icon.pixmap(64, 64)
                if not src.isNull():
                    x = r.x() + (r.width() - 64) // 2
                    y = r.y() + (r.height() - 64) // 2
                    p.drawPixmap(x, y, src)
                self._draw_status_dot(p, r)
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
                self._draw_status_dot(p, r)
                return

            if self._placeholder_letter:
                p.setPen(QColor(MONOS_COLORS["text_meta"]))
                f = monos_font("Inter", 28, QFont.Weight.DemiBold)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, self._placeholder_letter)
                self._draw_status_dot(p, r)
                return

            p.setPen(QColor(MONOS_COLORS["text_meta"]))
            f = monos_font("Inter", 11, QFont.Weight.DemiBold)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, "—")
        finally:
            p.end()

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

    _PREVIEW_CACHE_MAX = 50

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
        self._drag_start_pos: QPoint | None = None
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
        self._set_paste_enabled(False)
        self._seq_pool: QThreadPool | None = None
        self._seq_sig = _InspectorSeqDecodeSignaler(self)
        self._seq_sig.frame_ready.connect(self._on_inspector_seq_frame_ready, Qt.ConnectionType.QueuedConnection)
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

    def _inspector_preview_decode_max_side(self) -> int:
        """Decode / scale thumbs to match preview cell (DPR), capped for memory."""
        wgt = self._container._w
        w = wgt.width()
        if w < 1:
            w = max(280, wgt.sizeHint().width())
        h = wgt.heightForWidth(w) if wgt.hasHeightForWidth() else max(1, wgt.height())
        h = max(1, h)
        dpr = max(1.0, float(wgt.devicePixelRatioF()))
        side = int(max(w, h) * dpr * 1.08)
        return max(256, min(1024, side))

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

    def invalidate_settings_dependent_cache(self) -> None:
        self._preview_thumb_cache.clear()
        self._seq_decode_bucket = None

    def _work_paths_for_preview_item(self, item: ViewItem) -> tuple[Path | None, Path | None]:
        ref = getattr(item, "ref", None)
        dept = self._active_department
        if not isinstance(ref, (Asset, Shot)) or not (dept or "").strip():
            return (None, None)
        ds = dept.strip()
        wp = dept_work_path_for_ref(ref, dept)
        wf = primary_work_file_for_department(ref, ds, _inspector_get_active_dcc(item.path, dept))
        return (wp, wf)

    def _sync_sequence_context_for_inspector_preview(self) -> None:
        self._sequence_folder = None
        self._sequence_frames = []
        item = self._item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            self._update_sequence_play_button()
            self._sync_inspector_thumb_tooltip()
            return
        wp, wf = self._work_paths_for_preview_item(item)
        sq, frames = _inspector_preview_resolve_sequence(wp, wf)
        self._sequence_folder = sq
        self._sequence_frames = frames
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
            self._container.set_preview_help_text(
                "Double-click: open file.\n"
                "Right-click: menu."
            )
            return
        if item.kind in (ViewItemKind.PROJECT, ViewItemKind.DEPARTMENT):
            self._container.set_preview_help_text("Right-click: menu.")
            return
        lines: list[str] = [
            "Double-click: open the thumbnail image (default app in Settings).",
        ]
        if self._sequence_frames:
            lines.append("Hold left button and drag: drag the sequence folder (to Explorer / a DCC).")
            lines.append("Middle mouse + drag horizontally: scrub frames.")
        src = read_inspector_thumbnail_source(self._qsettings)
        if src != THUMB_SOURCE_RENDER_SEQUENCE:
            lines.append("Hover: Fill / Fit, Paste, Remove (top).")
        lines.append("Right-click: menu (paste, remove, open file…).")
        self._container.set_preview_help_text("\n".join(lines))

    def _update_sequence_play_button(self) -> None:
        self._container.update_sequence_play_control(
            available=bool(self._sequence_frames),
            playing=self._seq_playing,
        )

    def _perform_sequence_folder_drag(self) -> None:
        if self._sequence_folder is None or not self._sequence_folder.is_dir():
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(self._sequence_folder.resolve()))])
        drag = QDrag(self._container._w)
        drag.setMimeData(md)
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
        mode = read_inspector_thumbnail_source(self._qsettings)
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)):
            wp, wf = self._work_paths_for_preview_item(item)
            p = resolve_entity_thumbnail_source_path(
                Path(item.path),
                self._active_department,
                mode,
                wp,
                wf,
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
        mode = read_inspector_thumbnail_source(self._qsettings)
        self._container.set_render_sequence_hide_controls(mode == THUMB_SOURCE_RENDER_SEQUENCE)
        self._sync_inspector_thumb_tooltip()

    def _halt_inline_sequence_ui(self) -> None:
        self._seq_playing = False
        self._seq_scrubbing = False
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
        return bool(self._sequence_frames) and all(p.suffix.lower() in heavy for p in self._sequence_frames)

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is not self._container._w:
            return super().eventFilter(watched, event)
        try:
            et = event.type()
        except Exception:
            return False
        if et == QEvent.Type.Resize:
            self._on_inspector_preview_resize()
            return False
        if et == QEvent.Type.MouseButtonDblClick and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                if self._resolve_inspector_thumbnail_disk_path() is None:
                    return False
                if self._seq_playing:
                    self._seq_playing = False
                    self._seq_tick.stop()
                    self._seq_poll.stop()
                    self._restore_static_thumb_from_cache()
                self._open_inspector_thumbnail_externally()
                self._update_sequence_play_button()
                return True
        if et == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                if hasattr(event, "position"):
                    self._drag_start_pos = QPoint(int(event.position().x()), int(event.position().y()))
                else:
                    self._drag_start_pos = event.pos()
            elif event.button() == Qt.MouseButton.MiddleButton and self._sequence_frames:
                self._seq_scrubbing = True
                self._scrub_inspector_seq_from_event(event)
                return True
        elif et == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if self._seq_scrubbing and bool(event.buttons() & Qt.MouseButton.MiddleButton):
                self._scrub_inspector_seq_from_event(event)
                return True
            if self._drag_start_pos is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):
                if hasattr(event, "position"):
                    pos = QPoint(int(event.position().x()), int(event.position().y()))
                else:
                    pos = event.pos()
                d = pos - self._drag_start_pos
                if (abs(d.x()) + abs(d.y())) >= QApplication.startDragDistance():
                    self._perform_sequence_folder_drag()
                    self._drag_start_pos = None
        elif et == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.MiddleButton:
                self._seq_scrubbing = False
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                # Play/pause chỉ qua nút overlay — tránh xung đột với drag folder + double-click mở file.
                self._drag_start_pos = None
        return False

    def apply_preview_thumb(self, path_str: str, image_or_none: QImage | None, use_fit: bool) -> None:
        """Main thread only: apply thumb from worker. path_str must match current item."""
        self._halt_inline_sequence_ui()
        self._seq_index = 0
        self._last_thumb_use_fit = use_fit
        w = self._container._w
        w.set_loading(False)
        item = self._item
        if item is None or str(item.path) != path_str:
            return
        cache_key = self._preview_cache_key(Path(path_str))
        pix: QPixmap | None = None
        if image_or_none is not None and not image_or_none.isNull():
            pix = QPixmap.fromImage(image_or_none)
            if not pix.isNull():
                w.set_pixmap(pix, use_fit=use_fit)
                self._seq_live_display = False
        if pix is None:
            if item.kind == ViewItemKind.INBOX_ITEM and item.path:
                try:
                    icon_name, color_hex = file_icon_spec_for_path(item.path)
                    w.set_placeholder_file_icon(icon_name, color_hex)
                except Exception:
                    pass
            w.set_pixmap(None)
            self._seq_live_display = False
        while len(self._preview_thumb_cache) >= self._PREVIEW_CACHE_MAX:
            self._preview_thumb_cache.popitem(last=False)
        self._preview_thumb_cache[cache_key] = (pix, use_fit)
        self._preview_thumb_cache.move_to_end(cache_key)
        self._sync_sequence_context_for_inspector_preview()
        self._sync_thumbnail_overlay_mode()

    def clear_preview_loading(self) -> None:
        """Tắt loading spinner (khi worker lỗi)."""
        self._container._w.set_loading(False)

    def set_active_department(self, department: str | None) -> None:
        self._active_department = (department or "").strip() or None

    def _preview_cache_key(self, path: Path) -> str:
        try:
            base = str(path.resolve())
        except Exception:
            base = str(path)
        dep = (self._active_department or "").strip()
        mode = read_inspector_thumbnail_source(self._qsettings)
        if dep:
            return f"{base}::dept::{dep}::ts::{mode}"
        return f"{base}::ts::{mode}"

    def set_item(self, item: ViewItem) -> None:
        self._halt_inline_sequence_ui()
        self._seq_index = 0
        self._seq_decode_bucket = None
        self._seq_live_display = False
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
        path = item.path
        path_str = str(path)
        cache_key = self._preview_cache_key(path)
        dept = self._active_department
        mgr = self._worker_manager

        # Đã load rồi thì dùng cache, không load lại
        if cache_key in self._preview_thumb_cache:
            cached_pix, cached_fit = self._preview_thumb_cache[cache_key]
            self._preview_thumb_cache.move_to_end(cache_key)
            if cached_pix is not None and not cached_pix.isNull():
                w.set_pixmap(cached_pix, use_fit=cached_fit)
                self._seq_live_display = False
                self._sync_sequence_context_for_inspector_preview()
                return
            # cache lưu (None, fit) khi không có thumb → hiện placeholder
            if is_inbox and path:
                try:
                    icon_name, color_hex = file_icon_spec_for_path(path)
                    w.set_placeholder_file_icon(icon_name, color_hex)
                except Exception:
                    pass
            w.set_pixmap(None)
            self._seq_live_display = False
            self._sync_sequence_context_for_inspector_preview()
            return

        mode = read_inspector_thumbnail_source(self._qsettings)
        wp, wf = self._work_paths_for_preview_item(item)
        wps = str(wp) if wp is not None else None
        wfs = str(wf) if wf is not None else None

        if mgr is not None and hasattr(mgr, "submit_task"):
            w.set_loading(True)
            w.update()
            QApplication.processEvents()

            def submit() -> None:
                if getattr(self, "_item", None) is not item or str(self._item.path) != path_str:
                    w.set_loading(False)
                    return
                ms = self._inspector_preview_decode_max_side()

                def run_load() -> tuple[str, QImage | None, bool]:
                    return _inspector_preview_worker_run(
                        path_str,
                        is_inbox=is_inbox,
                        dept=dept,
                        mode=mode,
                        work_path_str=wps,
                        work_file_str=wfs,
                        decode_max_side=ms,
                    )

                task = WorkerTask("inspector_preview_thumb", run_load, manager=mgr)
                mgr.submit_task(task, category="inspector_preview_thumb", replace_existing=True)

            QTimer.singleShot(0, submit)
            return

        def load() -> None:
            if getattr(self, "_item", None) is not item or str(self._item.path) != path_str:
                return
            ms = self._inspector_preview_decode_max_side()
            ps, img, uf = _inspector_preview_worker_run(
                path_str,
                is_inbox=is_inbox,
                dept=dept,
                mode=mode,
                work_path_str=wps,
                work_file_str=wfs,
                decode_max_side=ms,
            )
            self.apply_preview_thumb(ps, img, uf)

        QTimer.singleShot(0, load)

    def update_thumbnail_only(self) -> None:
        """Update thumbnail image only (e.g. after thumbnailsChanged or department change)."""
        item = self._item
        if item is None:
            return
        self._halt_inline_sequence_ui()
        path = item.path
        path_str = str(path)
        cache_key = self._preview_cache_key(path)
        dept = self._active_department
        is_inbox = item.kind == ViewItemKind.INBOX_ITEM
        mgr = self._worker_manager
        mode = read_inspector_thumbnail_source(self._qsettings)
        wp, wf = self._work_paths_for_preview_item(item)
        wps = str(wp) if wp is not None else None
        wfs = str(wf) if wf is not None else None

        if cache_key in self._preview_thumb_cache:
            cached_pix, cached_fit = self._preview_thumb_cache[cache_key]
            self._preview_thumb_cache.move_to_end(cache_key)
            w = self._container._w
            if cached_pix is not None and not cached_pix.isNull():
                w.set_pixmap(cached_pix, use_fit=cached_fit)
                self._seq_live_display = False
                self._sync_sequence_context_for_inspector_preview()
                self._sync_thumbnail_overlay_mode()
                return
            if is_inbox and path:
                try:
                    icon_name, color_hex = file_icon_spec_for_path(path)
                    w.set_placeholder_file_icon(icon_name, color_hex)
                except Exception:
                    pass
            w.set_pixmap(None)
            self._seq_live_display = False
            self._sync_sequence_context_for_inspector_preview()
            self._sync_thumbnail_overlay_mode()
            return

        if mgr is not None and hasattr(mgr, "submit_task"):
            w = self._container._w
            w.set_loading(True)
            w.update()
            QApplication.processEvents()
            self._sync_thumbnail_overlay_mode()

            def submit() -> None:
                if self._item is not item or str(self._item.path) != path_str:
                    w.set_loading(False)
                    return
                ms = self._inspector_preview_decode_max_side()

                def run_load() -> tuple[str, QImage | None, bool]:
                    return _inspector_preview_worker_run(
                        path_str,
                        is_inbox=is_inbox,
                        dept=dept,
                        mode=mode,
                        work_path_str=wps,
                        work_file_str=wfs,
                        decode_max_side=ms,
                    )

                task = WorkerTask("inspector_preview_thumb", run_load, manager=mgr)
                mgr.submit_task(task, category="inspector_preview_thumb", replace_existing=True)

            QTimer.singleShot(0, submit)
            return

        def load() -> None:
            if self._item is not item or str(self._item.path) != path_str:
                return
            ms = self._inspector_preview_decode_max_side()
            ps, img, uf = _inspector_preview_worker_run(
                path_str,
                is_inbox=is_inbox,
                dept=dept,
                mode=mode,
                work_path_str=wps,
                work_file_str=wfs,
                decode_max_side=ms,
            )
            self.apply_preview_thumb(ps, img, uf)

        QTimer.singleShot(0, load)

    def refresh_thumbnail(self) -> None:
        item = self._item
        if item is None:
            return
        self._preview_thumb_cache.pop(self._preview_cache_key(item.path), None)
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is not None and hasattr(mgr, "invalidate"):
            mgr.invalidate(str(item.path), department=self._active_department)
        for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
            self._thumbs.invalidate_file(item.path / name)
        self.set_item(item)

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
        menu.exec(gp)


class _IdentityBlock(QWidget):
    open_clicked = Signal()
    open_with_clicked = Signal()
    active_dcc_changed = Signal(object, str, str)  # path, department, dcc_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorIdentity")
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
        self._name.setText(display_name_for_item(item))
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


class _InspectorAssetStatusBlock(QWidget):
    """One container: row1 = Asset info (name+meta) | Status pill; row2 = folder shortcuts."""
    open_asset_folder_clicked = Signal()
    open_work_folder_clicked = Signal()
    open_publish_folder_clicked = Signal()

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
        self._health = _ProductionHealth(self)
        row1_l.addWidget(self._identity, 1)
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

        self._quick_actions_btn.setMenu(menu)
        row2_l.addWidget(self._quick_actions_btn, 0)
        row2_l.addStretch(1)

        l.addWidget(row1, 0)
        l.addWidget(row2, 0)

    def _on_open_asset_folder_clicked(self) -> None:
        self.open_asset_folder_clicked.emit()

    def _on_open_work_folder_clicked(self) -> None:
        self.open_work_folder_clicked.emit()

    def _on_open_publish_folder_clicked(self) -> None:
        self.open_publish_folder_clicked.emit()

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
        self._health.set_item(item)
        is_asset_or_shot = bool(item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))
        self._quick_actions_btn.setEnabled(is_asset_or_shot)
        self._act_open_asset_folder.setEnabled(is_asset_or_shot)
        self._act_open_work_folder.setEnabled(is_asset_or_shot)
        self._act_open_publish_folder.setEnabled(is_asset_or_shot)
        self._act_copy_dcc_work_path.setEnabled(is_asset_or_shot)
        self._act_copy_publish_path.setEnabled(is_asset_or_shot)

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
    """Read-only global status indicator; computes overall status and exposes it as a colored dot with tooltip."""

    # color_hex, label
    status_changed = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorProductionHealth")
        self._current_item: ViewItem | None = None
        self._focused_department: str | None = None
        self._hidden_departments: set[str] = set()

        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        # No visible child; global status is rendered inside the thumbnail via _PreviewWidget.
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        l.addWidget(spacer)

    def set_focused_department(self, dept_name: str | None) -> None:
        self._focused_department = (dept_name or "").strip() or None
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
        if item is None:
            self.status_changed.emit("", "")
            return
        status = "WAITING"
        ref = item.ref
        if isinstance(ref, Department):
            status = _status_from_department(ref)
        elif isinstance(ref, (Asset, Shot)):
            dep = (self._focused_department or "").strip()
            dep_cf = dep.casefold() if dep else ""
            if dep_cf:
                for d in ref.departments:
                    if (d.name or "").strip().casefold() == dep_cf:
                        status = _status_from_department(d)
                        break
            else:
                visible = [d for d in ref.departments if (d.name or "") not in self._hidden_departments]
                if not visible:
                    visible = list(ref.departments)
                if visible and all(d.publish_version_count > 0 for d in visible):
                    status = "READY"
                elif any(d.work_exists for d in visible):
                    status = "PROGRESS"
                else:
                    status = "WAITING"
        else:
            self.status_changed.emit("", "")
            return

        color = _status_color(status)
        display = _status_display_label(status)
        # Emit for Inspector preview to render as a dot inside thumbnail.
        self.status_changed.emit(color, display)


class _DeptCard(QFrame):
    clicked = Signal()

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
        self._pill.setStyleSheet(
            f"padding: 1px 6px; border-radius: 8px; border: none; background: rgba(255,255,255,0.06); color: {MONOS_COLORS['text_label']};"
        )

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
        self._btn_open.clicked.connect(self._open_folder)

    def set_department(self, dept: Department, display_name: str | None = None, icon_name: str | None = None) -> None:
        self._dept = dept
        raw = (display_name or dept.name or "").strip()
        text = raw.replace("_", " ").title() if raw else ""
        self._name.setText(text)
        ico = lucide_icon((icon_name or "").strip() or "layers", size=14, color_hex=MONOS_COLORS["text_label"])
        self._icon_label.setPixmap(ico.pixmap(14, 14))
        status = _status_from_department(dept)
        self._pill.setText(_status_display_label(status))
        self._pill.setStyleSheet(
            f"padding: 1px 6px; border-radius: 8px; border: none; background: rgba(255,255,255,0.06); color: {_status_color(status)};"
        )
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
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        if self._dept is None:
            return
        try:
            if not self._dept.path.exists():
                return
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._dept.path)))


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
        for _ in range(_MAX_DEPT_CARDS):
            card = _DeptCard(self._list)
            card.setVisible(False)
            self._dept_cards.append(card)
            self._dept_card_slots.append(None)

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

        if isinstance(ref, Department):
            depts = (ref,)
        elif isinstance(ref, (Asset, Shot)):
            if isinstance(registry, DepartmentRegistry):
                depts = _inspector_merge_departments_with_registry(ref, registry)
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

        if registry and hasattr(registry, "get_departments"):
            ordered_ids = list(registry.get_departments())
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
                card.set_department(d, display_name, dept_icon_name)
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
        self._modified.set_value(_format_mtime(item.path))

    def set_resolved_path(self, path: Path | None) -> None:
        """Update displayed path to a department work path (or reset to item path when None)."""
        self._resolved_path = path
        if path is not None:
            self._src.setText(str(path))
            self._modified.set_value(_format_mtime(path))
        elif self._last_item is not None:
            self._src.setText(str(self._last_item.path))
            self._modified.set_value(_format_mtime(self._last_item.path))

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

