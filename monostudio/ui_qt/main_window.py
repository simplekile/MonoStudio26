from __future__ import annotations

import base64
import json
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QFileSystemWatcher, QPoint, Qt, QRect, QSettings, Signal, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QMenu, QMessageBox, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QToolTip, QVBoxLayout, QWidget
from qframelesswindow import FramelessMainWindow

from monostudio.core.app_paths import get_app_base_path
from monostudio.ui_qt.external_drop import accept_url_drag, event_global_pos, paths_from_drop_event
from monostudio.core.update_checker import run_full_update_check
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.fs_move import move_path
from monostudio.core.fs_reader import (
    build_project_index,
    enrich_shots_review_render,
    read_use_dcc_folders,
    resolve_work_path,
    run_incremental_scan,
    scan_single_asset,
    scan_single_shot,
)
from monostudio.core.item_status import set_department_status_override
from monostudio.core.models import Asset, ProjectIndex, Shot
from monostudio.core.structure_registry import StructureRegistry
from monostudio.core.type_registry import TypeRegistry
from monostudio.core.workspace_reader import DiscoveredProject, ProjectQuickStats, discover_projects, read_project_quick_stats
from monostudio.core.project_create import create_new_project
from monostudio.core.pipeline_types_and_presets import (
    PipelineTypesAndPresets,
    ensure_pipeline_bootstrap,
    load_pipeline_types_and_presets_for_project,
    seed_project_from_user_default,
)
from monostudio.core.clipboard_thumbnail_handler import ClipboardThumbnailHandler
from monostudio.core.asset_rename import prepare_work_file_renames, rename_asset
from monostudio.core.pending_create import remove_by_entity, remove_for_entities, clear_all as pending_clear_all
from monostudio.core.project_trash import (
    TrashError,
    move_asset_or_shot_to_trash,
    purge_expired,
    retention_days_from_settings,
)
from monostudio.core.shell_open import open_folder as shell_open_folder
from monostudio.ui_qt.create_entry_dialogs import (
    BatchCreateAssetDialog,
    BatchCreateShotDialog,
    CreateAssetDialog,
    CreateShotDialog,
)
from monostudio.core.inbox_reader import (
    add_to_inbox,
    append_inbox_distributed,
    copy_into_inbox_folder,
    get_inbox_root,
    move_into_inbox_folder,
)
from monostudio.core.internal_check_reader import (
    add_to_internal_check,
    copy_into_internal_check_folder,
    ensure_internal_check_root,
    get_internal_check_root,
    move_into_internal_check_folder,
    send_internal_check_to_delivery,
)
from monostudio.core.delivery_reader import (
    add_to_delivery,
    copy_into_delivery_folder,
    ensure_delivery_source_folders,
    get_delivery_root,
    move_into_delivery_folder,
)
from monostudio.core.outbox_reader import (
    ensure_outbox_source_folders,
    get_outbox_root,
)
from monostudio.ui_qt.external_drop import drop_wants_copy, paths_under_root
from monostudio.ui_qt.inbox_drop_dialog import InboxDropDialog
from monostudio.ui_qt.inbox_page_widget import InboxPageWidget
from monostudio.ui_qt.outbox_page_widget import OutboxPageWidget
from monostudio.ui_qt.internal_check_page_widget import InternalCheckPageWidget
from monostudio.ui_qt.trash_page_widget import TrashPageWidget
from monostudio.ui_qt.dashboard_page_widget import DashboardPageWidget
from monostudio.ui_qt.user_identity_dialog import UserIdentityDialog
from monostudio.ui_qt.schedule_page_widget import SchedulePageWidget
from monostudio.ui_qt.reference_page_widget import ReferencePageWidget
from monostudio.ui_qt.video_preview_dialog import VideoPreviewDialog
from monostudio.ui_qt.video_preview_context import (
    PreviewContext,
    ReviewOpenRequest,
    VideoPreviewOpenRequest,
)
from monostudio.core.video_media import is_video_path, list_video_siblings, paths_under_project_root
from monostudio.ui_qt.inspector import InspectorPanel
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.new_project_dialog import NewProjectDialog
from monostudio.ui_qt.settings_dialog import SettingsDialog
from monostudio.ui_qt.project_picker_dialog import ProjectPickerDialog
from monostudio.ui_qt.sidebar import Sidebar, SidebarContext
from monostudio.ui_qt.sidebar_nav_rail import SidebarNavRail
from monostudio.ui_qt.popup_position import max_popup_height_in_widget
from monostudio.ui_qt.top_bar import TopBar
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind, display_name_for_item
from monostudio.ui_qt.delete_confirm_dialog import DeleteConfirmDialog, ask_delete_folder
from monostudio.ui_qt.rename_asset_dialog import RenameAssetDialog
from monostudio.ui_qt.page_loading_bar import PageLoadingBar, SCANNING_EMPTY_MESSAGE
from monostudio.ui_qt.app_controller import AppController
from monostudio.ui_qt.app_state import AppState
from monostudio.ui_qt.recent_tasks_store import RecentTasksStore
from monostudio.ui_qt.worker_manager import WorkerManager, WorkerTask
from monostudio.ui_qt.thumbnails import ThumbnailManager
from monostudio.ui_qt.fs_watcher import (
    FsEventCollector,
    append_entity_meta_watch_paths,
    append_entity_monostudio_watch_paths,
    append_entity_special_folder_watch_paths,
)
from monostudio.ui_qt.stress_diagnostics_dialog import StressDiagnosticsDialog
from monostudio.ui_qt.stress_profiler import enabled as stress_profiler_enabled
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.app_footer import AppFooter
from monostudio.ui_qt.notification import notify as notification_service


@dataclass
class _ProductionStatusBatchResult:
    failed: list[tuple[str, str]]
    ok_resolved: set[str]
    current_assets: dict[str, Asset]
    current_shots: dict[str, Shot]
    schedule_touched: bool


@dataclass(frozen=True)
class _AssetRenameWorkerResult:
    old_path: Path
    new_path: Path
    index: ProjectIndex


def run_asset_rename_worker(
    *,
    project_root: Path,
    asset_path: Path,
    new_name: str,
    work_file_renames: list[tuple[Path, Path]],
) -> _AssetRenameWorkerResult:
    result = rename_asset(
        project_root=project_root,
        asset_path=asset_path,
        new_name=new_name,
        work_file_renames=work_file_renames,
    )
    index = build_project_index(project_root)
    return _AssetRenameWorkerResult(
        old_path=asset_path,
        new_path=result.new_path,
        index=index,
    )


def _batch_sync_schedule_after_status_overrides(
    project_root: Path,
    applied: list[tuple[Path, str, str | None]],
) -> bool:
    """Batch schedule JSON updates after production status overrides."""
    if not applied:
        return False
    from monostudio.core.production_status import SKIPPED_STATUS_ID
    from monostudio.core.project_schedule import (
        clear_auto_bar_suppressions_for_entities,
        clear_entity_department_schedules,
        entity_rel_path,
    )

    struct_reg = StructureRegistry.for_project(project_root)
    assets_dir = project_root / struct_reg.get_folder("assets")
    skip_rows: list[tuple[str, str, str]] = []
    unskip_by_dep: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for ep, dep_s, sid in applied:
        dep = (dep_s or "").strip()
        if not dep:
            continue
        try:
            ep_r = ep.resolve()
        except OSError:
            ep_r = ep
        try:
            ep_r.relative_to(assets_dir.resolve())
            kind = "asset"
        except ValueError:
            kind = "shot"
        rel = entity_rel_path(project_root, ep_r)
        if sid == SKIPPED_STATUS_ID:
            skip_rows.append((kind, rel, dep))
        elif sid is None:
            unskip_by_dep[dep].append((kind, rel))

    touched = False
    if skip_rows:
        clear_entity_department_schedules(
            project_root, rows=skip_rows, suppress_auto=True
        )
        touched = True
    for dep, entities in unskip_by_dep.items():
        if entities:
            clear_auto_bar_suppressions_for_entities(
                project_root, department=dep, entities=entities
            )
            touched = True
    return touched


def run_production_status_batch(
    project_root: Path,
    updates: list[tuple[Path, str, str | None]],
    *,
    current_assets: dict[str, Asset],
    current_shots: dict[str, Shot],
) -> _ProductionStatusBatchResult:
    """Write status overrides, rescan touched entities, batch schedule sync (worker-safe)."""
    failed: list[tuple[str, str]] = []
    ok_paths: list[Path] = []
    applied: list[tuple[Path, str, str | None]] = []
    seen_write: set[tuple[str, str]] = set()

    for ep, dep, sid in updates:
        dep_s = (dep or "").strip()
        if not dep_s:
            continue
        key = (str(ep), dep_s)
        if key in seen_write:
            continue
        seen_write.add(key)
        try:
            set_department_status_override(ep, dep_s, sid)
            applied.append((ep, dep_s, sid))
            if ep not in ok_paths:
                ok_paths.append(ep)
        except OSError as e:
            failed.append((str(ep), str(e)))
        except ValueError:
            pass

    ok_resolved: set[str] = set()
    if ok_paths:
        dept_reg = DepartmentRegistry.for_project(project_root)
        type_reg = TypeRegistry.for_project(project_root)
        struct_reg = StructureRegistry.for_project(project_root)
        assets_dir = project_root / struct_reg.get_folder("assets")
        shots_dir = project_root / struct_reg.get_folder("shots")

        def same_path(key: str, path_value: Path) -> bool:
            try:
                return Path(key).resolve() == Path(path_value).resolve()
            except OSError:
                return False

        for ep in ok_paths:
            try:
                ep_r = ep.resolve()
            except OSError:
                ep_r = ep
            ok_resolved.add(str(ep_r))

            updated_asset: Asset | None = None
            updated_shot: Shot | None = None
            try:
                ep_r.relative_to(assets_dir.resolve())
                updated_asset = scan_single_asset(project_root, ep_r, dept_reg, type_reg)
            except ValueError:
                pass
            if updated_asset is None:
                try:
                    ep_r.relative_to(shots_dir.resolve())
                    updated_shot = scan_single_shot(project_root, ep_r, dept_reg)
                except ValueError:
                    pass

            if updated_asset is not None:
                key = next(
                    (k for k in current_assets if same_path(k, updated_asset.path)),
                    str(updated_asset.path),
                )
                for k in list(current_assets):
                    if k != key and same_path(k, updated_asset.path):
                        current_assets.pop(k, None)
                current_assets[key] = updated_asset
            if updated_shot is not None:
                key = next(
                    (k for k in current_shots if same_path(k, updated_shot.path)),
                    str(updated_shot.path),
                )
                for k in list(current_shots):
                    if k != key and same_path(k, updated_shot.path):
                        current_shots.pop(k, None)
                current_shots[key] = updated_shot

    schedule_touched = _batch_sync_schedule_after_status_overrides(project_root, applied)
    return _ProductionStatusBatchResult(
        failed=failed,
        ok_resolved=ok_resolved,
        current_assets=current_assets,
        current_shots=current_shots,
        schedule_touched=schedule_touched,
    )


class _StartupUpdateCheckWorker(QThread):
    """Runs full update check (MonoStudio + extra repos) in background at startup; emits (result, error_message)."""

    check_finished = Signal(object, str)

    def run(self) -> None:
        result, _extra, err = run_full_update_check()
        self.check_finished.emit(result, err)


class MainWindow(FramelessMainWindow):
    """
    Phase 0 shell:
    - 3 panels: Sidebar (~15%), Main View (~60%), Inspector (~25%, hidden by default)
    - On narrow resize: hide inspector, then hide sidebar (responsive), unless user chose manual layout via TopBar toggles.
    - No filesystem logic
    - No publish logic
    - No database
    """

    # Width thresholds: below these, hide inspector then sidebar (content area width).
    _WIDTH_HIDE_INSPECTOR = 1000
    _WIDTH_HIDE_SIDEBAR = 720

    SIDEBAR_RAIL_W = 68  # nav_rail_expand_item.RAIL_SLOT_W (+20% vs 56)
    SIDEBAR_PANEL_W = 256
    SIDEBAR_FULL_W = SIDEBAR_RAIL_W + SIDEBAR_PANEL_W  # 324

    departmentChanged = Signal(object)  # str | None
    typeChanged = Signal(object)  # str | None

    def __init__(self, *, splash_status: Callable[[str], None] | None = None) -> None:
        super().__init__()
        _splash = splash_status or (lambda _msg: None)
        self.setWindowTitle("MONOS")
        ensure_pipeline_bootstrap()

        _splash("Initializing pipeline…")

        # Minimum window size (usability floor).
        self.setMinimumSize(640, 480)
        self.setObjectName("MonosMainWindow")
        # Always-on-top (persisted); on Windows we drive z-order via Win32 to avoid setWindowFlags flicker.
        self._window_always_on_top: bool = False

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        repo_root = get_app_base_path()
        self._controller = AppController(settings=self._settings, repo_root=repo_root, parent=self)
        self._recent_tasks_store = RecentTasksStore(self._settings)
        self._controller.set_recent_tasks_store(self._recent_tasks_store)
        # Guard: context switches must never trigger Open DCC flows or spawn dialogs.
        self._context_switch_in_progress: bool = False
        self._nav_quick_pending_filters: dict[str, object] | None = None
        # Short cooldown after switching to Inbox so a delayed filter signal does not trigger a second reload (items flash then placeholder).
        self._inbox_switch_cooldown: bool = False
        # Background filesystem_scan after Assets↔Shots switch: sync index only; do not wipe thumb cache + full main view reload.
        self._filesystem_scan_soft: bool = False
        # Guard: filter (department/type) changes must never trigger Open DCC flows or spawn dialogs.
        self._filter_switch_in_progress: bool = False
        self._startup_complete: bool = False
        self._identity_prompt_pending: bool = False
        self._pending_deep_link: str | None = None
        self.launch_hidden_to_tray: bool = False
        self._pending_restore_maximized: bool = False
        self._force_quit: bool = False
        self._tray_manager = None
        self._workspace_root: Path | None = None
        self._workspace_projects: list[DiscoveredProject] = []
        self._workspace_project_status: dict[str, str] = {}
        self._workspace_project_quick_stats: dict[str, ProjectQuickStats] = {}

        self._project_root: Path | None = None
        self._project_index: ProjectIndex | None = None
        self._app_state = AppState(self)
        self._worker_manager = WorkerManager(self)
        self._worker_manager.taskFinished.connect(self._on_worker_task_finished)
        self._thumbnail_manager = ThumbnailManager(
            self,
            app_state=self._app_state,
            worker_manager=self._worker_manager,
            size_px=512,
            max_memory=200,
            settings=self._settings,
        )
        self._fs_watcher = QFileSystemWatcher(self)
        self._watcher_manually_disabled = False  # user can toggle watcher off via top bar
        self._fs_event_collector = FsEventCollector(self, debounce_ms=300)
        self._fs_watcher.fileChanged.connect(self._fs_event_collector.add_path)
        self._fs_watcher.directoryChanged.connect(self._fs_event_collector.add_path)
        self._fs_event_collector.batchReady.connect(self._on_fs_batch_ready)
        self._fs_event_collector.metaThumbnailsStale.connect(self._on_fs_meta_thumbnails_stale)
        self._fs_event_collector.itemNotesStale.connect(self._on_fs_item_notes_stale)
        self._fs_event_collector.entitySpecialFoldersStale.connect(self._on_fs_entity_special_folders_stale)
        self._fs_event_collector.mentionInboxStale.connect(self._sync_user_inbox_alerts)
        self._entered_parent: Asset | Shot | None = None

        # Centralized filter state (UI-only; no filtering engine yet)
        self.current_department: str | None = None
        self.current_type: str | None = None
        self.current_search_query: str = ""
        # Per (context, type) main-view selection — same asset stays selected when switching departments.
        self._main_view_selection_by_filter: dict[tuple[str, str], str] = {}
        self._apply_pipeline_types_and_presets_metadata(load_pipeline_types_and_presets_for_project(self._project_root))

        self._filter_panel = Sidebar()
        self._nav_rail = SidebarNavRail(self)
        self._nav_rail.set_filter_source(self._filter_panel.filters())
        # Persist sidebar filter selections per page (assets/shots).
        try:
            self._filter_panel.filters().set_settings(self._settings)
        except Exception:
            pass
        # Filter panel only — nav rail + content stack are restored after context_changed is connected.
        self._restore_sidebar_context(nav_rail=False)
        self._sidebar_container = QWidget(self)
        self._sidebar_container.setObjectName("SidebarContainer")
        self._sidebar_container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._sidebar_container.setMinimumWidth(self.SIDEBAR_FULL_W)
        self._sidebar_container.setMaximumWidth(self.SIDEBAR_FULL_W)
        _sidebar_row = QHBoxLayout(self._sidebar_container)
        _sidebar_row.setContentsMargins(0, 0, 0, 0)
        _sidebar_row.setSpacing(0)
        _sidebar_row.addWidget(self._nav_rail, 0)
        _sidebar_row.addWidget(self._filter_panel, 0)
        self._sidebar_panel_visible = True
        self._main_view = MainView()
        self._main_view.set_thumbnail_manager(self._thumbnail_manager)
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._main_view)
        self._inbox_page_widget: InboxPageWidget | None = None
        self._outbox_page_widget: OutboxPageWidget | None = None
        self._internal_check_page_widget: InternalCheckPageWidget | None = None
        self._trash_page_widget: TrashPageWidget | None = None
        self._dashboard_page_widget: DashboardPageWidget | None = None
        self._schedule_page_widget: SchedulePageWidget | None = None
        self._schedule_inspector_item: ViewItem | None = None
        # Dashboard → Schedule jump: (entity_kind, entity_rel, department, due_iso)
        self._pending_schedule_jump: tuple[str, str, str, str] | None = None
        self._pending_unscheduled_entities: list[tuple[str, str]] | None = None
        self._pending_overdue_entities: list[tuple[str, str]] | None = None
        self._active_nav_context: str | None = None
        self._reference_page_widget: ReferencePageWidget | None = None
        self._review_player_dialog: VideoPreviewDialog | None = None
        self._nav_quick_picker_dialog: object | None = None
        self._inspector = InspectorPanel()
        self._inspector.set_app_settings(self._settings)
        self._inspector.set_thumbnail_manager(self._thumbnail_manager)
        self._inspector.set_worker_manager(self._worker_manager)
        self._inspector.setMinimumWidth(240)
        self._top_bar = TopBar(self)
        self._top_bar.setFixedHeight(56)  # so FramelessMainWindow resize keeps height
        self.setTitleBar(self._top_bar)  # replace library title bar with MONOS TopBar
        _splash("Building workspace…")
        self._geometry_before_maximize: QRect | None = None  # restore về đúng kích thước khi bấm restore
        self._clipboard_thumbs = ClipboardThumbnailHandler(parent=self)

        # Topbar replaces the menu bar (no menus).
        try:
            self.menuBar().hide()
        except Exception:
            pass
        self._restore_workspace_root()
        self._restore_window_geometry()

        # L1: Main layout (horizontal) -> [Sidebar] + [Right container]
        # L2: Right container (vertical) -> [spacer for title bar] + [Main content]
        # Title bar is window child via setTitleBar(); spacer keeps content below it.
        _title_bar_spacer = QWidget(self)
        _title_bar_spacer.setFixedHeight(56)
        right_container = QWidget(self)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(_title_bar_spacer, 0)

        self._content_splitter = QSplitter(Qt.Horizontal)
        self._content_splitter.setObjectName("ContentSplitter")
        self._content_splitter.setChildrenCollapsible(False)
        self._content_splitter.addWidget(self._content_stack)
        self._content_splitter.addWidget(self._inspector)
        self._content_splitter.setStretchFactor(0, 70)
        self._content_splitter.setStretchFactor(1, 30)
        self._content_splitter.setSizes([800, 320])
        right_layout.addWidget(self._content_splitter, 1)
        self._right_container = right_container
        self._page_loading_bar = PageLoadingBar(self._content_stack)
        self._page_loading_visible = False
        self._project_load_save_on_complete = False

        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.setObjectName("MainSplitter")
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(self._sidebar_container)
        self._main_splitter.addWidget(right_container)
        self._main_splitter.setStretchFactor(0, 20)
        self._main_splitter.setStretchFactor(1, 80)
        self._main_splitter.setSizes([self.SIDEBAR_FULL_W, 1100])

        self._app_footer = AppFooter(self)
        _central = QWidget(self)
        _central_layout = QVBoxLayout(_central)
        _central_layout.setContentsMargins(0, 0, 0, 0)
        _central_layout.setSpacing(0)
        _central_layout.addWidget(self._main_splitter, 1)
        _central_layout.addWidget(self._app_footer, 0)
        self.setCentralWidget(_central)
        _central.installEventFilter(self)
        from monostudio.ui_qt.splash import prepare_main_window_shell

        prepare_main_window_shell(self)
        _splash("Assembling layout…")
        self._content_stack.installEventFilter(self)
        self._inspector.installEventFilter(self)
        self._main_view.installEventFilter(self)
        self._main_view._tile_view.viewport().installEventFilter(self)
        self._main_view._list_view.viewport().installEventFilter(self)
        self._restore_project_root()
        _splash("Loading project…")
        self._restore_splitter_sizes()
        # Store sizes to restore when showing panels again after narrow resize.
        self._content_splitter_sizes_restore: list[int] = [800, 320]
        self._main_splitter_sizes_restore: list[int] = [self.SIDEBAR_FULL_W, 1100]
        self._panel_layout_auto: bool = True
        # Manual sidebar: full (324px) vs compact rail (68px) — không ẩn hẳn cột sidebar.
        self._manual_sidebar_full: bool = True
        self._manual_inspector_visible: bool = True
        self._load_panel_layout_prefs()
        self._compact_filter_popup: QFrame | None = None
        self._compact_filter_scroll: QScrollArea | None = None
        self._compact_filter_popup_closed_at = 0.0
        self._POPUP_REOPEN_GRACE = 0.25
        # Frameless window on Windows: accept drops at window + page level (see external_drop.py).
        self.setAcceptDrops(True)
        # Title bar only over right pane (not over sidebar); update when splitter/window resizes.
        self._update_title_bar_geometry()
        self._top_bar.raise_()
        self._main_splitter.splitterMoved.connect(self._update_title_bar_geometry)

        self._nav_rail.context_changed.connect(self._on_context_switched)
        _splash("Connecting UI…")
        self._nav_rail.context_clicked.connect(self._on_context_clicked)
        self._nav_rail.project_switch_requested.connect(self._switch_project)
        self._nav_rail.browse_projects_requested.connect(self._open_project_picker)
        self._nav_rail.new_project_requested.connect(self._new_project)
        self._top_bar.settings_clicked.connect(self._open_settings)
        self._top_bar.layout_auto_clicked.connect(self._on_top_bar_layout_auto_clicked)
        self._top_bar.layout_sidebar_clicked.connect(self._on_top_bar_layout_sidebar_clicked)
        self._top_bar.layout_inspector_clicked.connect(self._on_top_bar_layout_inspector_clicked)
        self._nav_rail.recent_task_clicked.connect(self._on_recent_task_clicked)
        self._nav_rail.recent_task_double_clicked.connect(self._on_recent_task_double_clicked)
        self._nav_rail.clear_recent_tasks_requested.connect(self._on_clear_recent_tasks)
        self._filter_panel.recent_task_clicked.connect(self._on_recent_task_clicked)
        self._filter_panel.recent_task_double_clicked.connect(self._on_recent_task_double_clicked)
        self._filter_panel.clear_recent_tasks_requested.connect(self._on_clear_recent_tasks)
        self._nav_rail.filter_requested.connect(self._on_compact_filter_requested)
        # Metadata-driven filter sidebar (UI-only; wiring stub).
        self._filter_panel.filters().departmentClicked.connect(self._on_sidebar_department_clicked)
        self._filter_panel.filters().typeClicked.connect(self._on_sidebar_type_clicked)
        self._filter_panel.filters().entityScopeChanged.connect(self._on_schedule_sidebar_filters_changed)
        self._filter_panel.filters().tagClicked.connect(self._on_tag_filter_changed)
        self._filter_panel.filters().tagsDefinitionsChanged.connect(self._on_tag_definitions_changed)
        self._filter_panel.dashboard_widget_visibility_toggled.connect(
            self._on_dashboard_widget_palette_toggled
        )
        self._controller.departmentChanged.connect(lambda v: self._set_current_department(v, toggle_if_same=False))
        self._controller.typeChanged.connect(lambda v: self._set_current_type(v, toggle_if_same=False))
        self._controller.departmentChanged.connect(self._on_department_changed_notify)
        self._controller.typeChanged.connect(self._on_type_changed_notify)
        self.departmentChanged.connect(self._on_department_filter_changed)
        self.typeChanged.connect(self._on_type_filter_changed)
        self._top_bar.minimize_clicked.connect(self.showMinimized)
        self._top_bar.maximize_clicked.connect(self._toggle_maximize)
        self._top_bar.close_clicked.connect(self.request_close)
        self._top_bar.title_double_clicked.connect(self._toggle_maximize)
        self._top_bar.switch_user_requested.connect(self._on_switch_user)
        self._top_bar.edit_profile_requested.connect(self._on_edit_profile)
        self._top_bar.clear_identity_requested.connect(self._on_clear_identity)
        self._top_bar.forget_device_requested.connect(self._on_forget_device)
        self._top_bar.manage_team_requested.connect(self._on_manage_team)
        self._inspector.close_requested.connect(self._main_view.clear_selection)
        self._inspector.paste_thumbnail_requested.connect(self._on_paste_thumbnail_requested)
        self._inspector.remove_thumbnail_requested.connect(self._on_remove_thumbnail_requested)
        self._main_view.valid_selection_changed.connect(self._on_valid_selection_changed)
        self._main_view.item_activated.connect(self._on_item_activated)
        self._main_view.refresh_requested.connect(self._on_refresh_requested)
        self._main_view.root_context_menu_requested.connect(self._on_root_context_menu_requested)
        self._main_view.copy_inventory_requested.connect(self._on_copy_item_inventory_requested)
        self._main_view.open_requested.connect(self._on_open_requested)
        self._main_view.review_entity_requested.connect(self._on_review_entity_requested)
        self._main_view.open_with_requested.connect(self._on_open_with_requested)
        self._main_view.create_new_requested.connect(self._on_create_new_requested)
        self._main_view.selection_id_changed.connect(self._on_main_view_selection_id_changed)
        self._main_view.type_badge_clicked.connect(
            lambda: self._open_header_type_filter_picker(self._main_view.type_badge_widget())
        )
        self._main_view.department_badge_clicked.connect(
            lambda: self._open_header_department_filter_picker(self._main_view.department_badge_widget())
        )
        self._app_state.selectionChanged.connect(self._main_view.set_selection_from_state)
        self._app_state.assetsChanged.connect(self._on_app_state_assets_changed)
        self._app_state.shotsChanged.connect(self._on_app_state_shots_changed)
        self._app_state.filtersChanged.connect(self._on_app_state_filters_changed)
        self._app_state.thumbnailsChanged.connect(self._on_app_state_thumbnails_changed)

        # Clipboard thumbnail overrides: refresh UI after successful paste.
        self._clipboard_thumbs.thumbnailUpdated.connect(self._on_thumbnail_updated)
        self._main_view.delete_requested.connect(self._on_delete_requested)
        self._main_view.rename_requested.connect(self._on_rename_asset_requested)
        self._main_view.item_notes_requested.connect(self._on_item_notes_dialog_requested)
        self._main_view.inspector_ref_tab_requested.connect(self._on_inspector_ref_tab_requested)
        self._main_view.switch_project_requested.connect(self._on_switch_project_requested)
        self._main_view.primary_action_requested.connect(self._on_primary_action_requested)
        self._main_view.search_query_changed.connect(self._on_search_query_changed)
        self._main_view.show_publish_changed.connect(self._on_show_publish_changed)
        self._main_view.browser_mode_changed.connect(self._on_browser_mode_changed)
        self._main_view.open_publish_folder_requested.connect(self._on_open_publish_folder_requested)
        self._main_view.dcc_open_requested.connect(self._on_dcc_open_requested)
        self._main_view.dcc_folder_requested.connect(self._on_dcc_folder_requested)
        self._main_view.dcc_copy_path_requested.connect(self._on_dcc_copy_path_requested)
        self._main_view.dcc_delete_requested.connect(self._on_dcc_delete_requested)
        self._main_view.dcc_open_version_requested.connect(self._on_dcc_open_version_requested)

        # Inspector intents (explicit)
        self._inspector.open_folder_requested.connect(self._on_inspector_open_folder_requested)
        self._inspector.inbox_distribute_finished.connect(self._on_inbox_distribute_finished)
        self._inspector.active_dcc_changed.connect(self._on_inspector_active_dcc_changed)
        self._inspector.inspector_hidden_departments_changed.connect(
            self._on_inspector_hidden_departments_changed
        )
        self._inspector.production_status_override_requested.connect(self._on_production_status_override)
        self._inspector.item_notes_dialog_requested.connect(self._on_item_notes_dialog_requested)
        self._inspector.open_schedule_requested.connect(self._on_dashboard_open_schedule)
        self._inspector.edit_allocation_requested.connect(self._on_inspector_edit_allocation)
        self._inspector.assignee_changed.connect(self._on_inspector_assignee_changed)
        self._inspector.assignment_confirmed.connect(self._refresh_noti_unread_badge)
        self._inspector.video_preview_requested.connect(self._open_video_preview_from_inspector)
        self._inspector.review_open_requested.connect(self._open_review_player)
        self._bound_hotkeys: list[QShortcut] = []
        from monostudio.ui_qt.app_hotkeys import bind_hotkey

        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "inspector.tab_pipeline",
                self,
                lambda: self._inspector.set_inspector_tab_index(0),
            )
        )
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "inspector.tab_reference",
                self,
                lambda: self._inspector.set_inspector_tab_index(1),
            )
        )
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "inspector.tab_details",
                self,
                lambda: self._inspector.set_inspector_tab_index(2),
            )
        )
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "inspector.open_reference",
                self,
                self._inspector.open_reference_folder_for_selection,
            )
        )
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "inspector.open_concept",
                self,
                self._inspector.open_concept_folder_for_selection,
            )
        )
        from monostudio.ui_qt.nav_quick_view import NavQuickViewController

        self._nav_quick_view = NavQuickViewController(
            self,
            self._settings,
            get_context=lambda: self._nav_rail.current_context(),
            export_filters=lambda: self._filter_panel.filters().export_filter_snapshot(),
            recall_slot=self._recall_nav_quick_slot,
            on_assigned=self._on_nav_quick_slot_assigned,
        )
        self._bound_hotkeys.extend(self._nav_quick_view.bound_shortcuts())
        self._nav_rail.refresh_quick_view_tooltips(self._settings)
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "global.command_palette",
                self,
                self._open_command_palette,
                context=Qt.ShortcutContext.WindowShortcut,
            )
        )
        self._bound_hotkeys.append(
            bind_hotkey(
                self._settings,
                "global.nav_quick_picker",
                self,
                self._open_nav_quick_picker,
                context=Qt.ShortcutContext.WindowShortcut,
            )
        )
        self._main_view.production_status_override_chosen.connect(self._on_production_status_override)
        self._main_view.active_dcc_changed.connect(self._on_main_view_active_dcc_changed)
        self._main_view.thumbnail_source_changed.connect(self._on_main_view_thumbnail_source_changed)

        # Restore last nav page (Assets/Shots/Inbox/...) after connections so Inbox switch builds split view.
        self._restore_sidebar_context(force=True)

        self._pull_browser_filters_from_sidebar()
        # Initial population is driven by project-root restore (scan trigger) and current context.
        self._reload_main_view()
        self._sync_main_view_header()
        self._inspector.set_item(None)
        self._sync_primary_action()
        self._sync_main_view_header()
        self._sync_top_bar()

        notification_service.set_main_window(self, self._main_view)
        from monostudio.ui_qt.windows_toast_bridge import install_windows_toast_focus

        self._windows_toast_focus_bridge = install_windows_toast_focus(self)
        from monostudio.ui_qt.tray_manager import TrayManager

        self._tray_manager = TrayManager(self, settings=self._settings)
        self._tray_manager.install()
        self._sync_tray_status_badges()
        notification_service.set_general_toast_anchor_widget(self._top_bar.get_noti_button())
        # Anchor important banner under the update button so it appears as a callout.
        notification_service.set_important_anchor_widget(self._top_bar.get_update_button())
        notification_service.unread_count_changed.connect(self._refresh_noti_unread_badge)
        self._top_bar.user_alert_clicked.connect(self._on_user_alert_clicked)
        self._mention_sync_timer = QTimer(self)
        self._mention_sync_timer.setInterval(45000)
        self._mention_sync_timer.timeout.connect(self._sync_user_inbox_alerts)
        self._mention_sync_timer.start()
        self._discord_schedule_due_timer = QTimer(self)
        self._discord_schedule_due_timer.setInterval(86_400_000)
        self._discord_schedule_due_timer.timeout.connect(self._maybe_discord_schedule_due)
        self._discord_schedule_due_timer.start()
        self._refresh_noti_unread_badge()

        self._top_bar.update_button_clicked.connect(self._open_settings_to_updates)
        self._top_bar.watcher_toggled.connect(self._on_watcher_toggled)
        self._top_bar.always_on_top_toggled.connect(self._on_always_on_top_toggled)
        self._startup_update_check_worker: _StartupUpdateCheckWorker | None = None
        QTimer.singleShot(800, self._start_startup_update_check)

    def _start_startup_update_check(self) -> None:
        """Run update check in background; on result cache it and show red dot + tooltip if update available."""
        self._startup_update_check_worker = _StartupUpdateCheckWorker(self)
        self._startup_update_check_worker.check_finished.connect(self._on_startup_update_check_finished)
        self._startup_update_check_worker.finished.connect(lambda: setattr(self, "_startup_update_check_worker", None))
        self._startup_update_check_worker.start()

    def _on_startup_update_check_finished(self, result, error_message: str) -> None:
        # Debug: always pretend there is an update if env is set.
        debug_fake_update = os.getenv("MONOS_DEBUG_FAKE_UPDATE")
        if debug_fake_update:
            class _FakeUpdateResult:
                def __init__(self, version: str) -> None:
                    self.update_available = True
                    self.latest_version = version

            fake_version = debug_fake_update.strip() or (getattr(result, "latest_version", None) or "9.9.9-debug")
            result = _FakeUpdateResult(fake_version)
            error_message = ""

        if error_message or result is None:
            return
        self._settings.setValue("updates/last_check_time", datetime.now().isoformat())
        self._top_bar.set_update_available(result.update_available, result.latest_version)
        if self._tray_manager is not None:
            self._tray_manager.set_update_available(bool(result.update_available))
        if result.update_available:
            # Important: show a sticky notification that only disappears when the user closes it.
            # Format:
            # - Line 1: UPDATE AVAILABLE (bold, uppercase)
            # - Line 2: version name (italic)
            # - Line 3: "Check it out"
            message = (
                "<b>UPDATE AVAILABLE:</b><br>"
                f"- {result.latest_version}<br>"
                "<i>Check it out!</i>"
            )
            notification_service.important(message)

    def _open_settings_to_updates(self) -> None:
        """Open Settings dialog with General → Updates tab (e.g. from top bar update button)."""
        dialog = SettingsDialog(
            workspace_root=self._workspace_root,
            project_root=self._project_root,
            settings=self._settings,
            parent=self,
        )
        dialog.workspace_root_selected.connect(lambda p: self._apply_workspace_root(p, save=True))
        dialog.project_root_selected.connect(lambda p: self._apply_project_root(p, save=True))
        dialog.access_session_changed.connect(self._refresh_user_button)
        dialog.nav_quick_slots_changed.connect(
            lambda: self._nav_rail.refresh_quick_view_tooltips(self._settings)
        )
        dialog.hotkeys_changed.connect(self._reload_app_hotkeys)
        dialog.open_to_updates_tab()
        dialog.exec()
        self._refresh_user_button()
        self._sync_pipeline_preset_metadata_ui()
        if self._project_root is not None:
            try:
                dept_reg = DepartmentRegistry.for_project(self._project_root)
                self._inspector.set_department_registry(dept_reg)
                self._inspector.set_department_icon_map(self._dept_icon_map)
                self._inspector.set_type_short_name_map(self._type_short_name_map)
            except Exception:
                self._inspector.set_department_registry(None)
                self._inspector.set_department_icon_map({})
                self._inspector.set_type_short_name_map({})
        renamed_to = dialog.project_root_renamed_to()
        if renamed_to is None:
            return
        old = self._project_root
        self._apply_project_root(str(renamed_to), save=True)
        if old is not None:
            updated = []
            for p in self._workspace_projects:
                updated.append(DiscoveredProject(name=p.name, root=renamed_to if p.root == old else p.root))
            self._workspace_projects = updated
            self._sync_top_bar()

    def _on_watcher_toggled(self, enabled: bool) -> None:
        """User toggled file watcher from top bar: on -> resume watching, off -> release all handles."""
        self._watcher_manually_disabled = not enabled
        if not enabled:
            # On Windows, removePaths() often does not release directory handles. Cancel scan workers
            # so no thread holds dirs, then replace the watcher with a new one so the old one is
            # destroyed and the OS releases handles (rename/delete then work in Explorer too).
            self._top_bar.set_watcher_busy(True)
            try:
                self._worker_manager.cancel_category("filesystem_scan")
                self._worker_manager.cancel_category("incremental_scan")
                for _ in range(20):
                    QApplication.processEvents()
                    time.sleep(0.1)
                old_watcher = self._fs_watcher
                old_watcher.fileChanged.disconnect()
                old_watcher.directoryChanged.disconnect()
                self._fs_watcher = QFileSystemWatcher(self)
                self._fs_watcher.fileChanged.connect(self._fs_event_collector.add_path)
                self._fs_watcher.directoryChanged.connect(self._fs_event_collector.add_path)
                old_watcher.setParent(None)
                old_watcher.deleteLater()
                self._fs_event_collector.set_project_root(None)
                self._fs_event_collector.set_registries(None, None)
                for _ in range(15):
                    QApplication.processEvents()
                    time.sleep(0.05)
            finally:
                self._top_bar.set_watcher_busy(False)
            notification_service.success("File watcher paused. Rename and Move to Trash are now allowed.")
        else:
            self._update_fs_watcher_paths()
            notification_service.success("File watcher on. Changes will be detected automatically.")

    def _apply_win32_always_on_top(self, on: bool) -> bool:
        """Windows: HWND_TOPMOST without Qt setWindowFlags — avoids full window recreate / flicker."""
        if sys.platform != "win32":
            return False
        try:
            import win32con
            import win32gui
        except ImportError:
            return False
        try:
            wid = self.winId()
            if not wid:
                return False
            hwnd = int(wid)
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            after = win32con.HWND_TOPMOST if on else win32con.HWND_NOTOPMOST
            win32gui.SetWindowPos(hwnd, after, 0, 0, 0, 0, flags)
            return True
        except Exception:
            return False

    def _on_always_on_top_toggled(self, on: bool) -> None:
        self._window_always_on_top = on
        if sys.platform == "win32" and self._apply_win32_always_on_top(on):
            return
        # Linux/macOS (or Win32 fallback): qframelesswindow path — must refresh frameless after flag change.
        self.setStayOnTop(on)

    def _update_title_bar_geometry(self) -> None:
        """Place title bar over right pane only (x = sidebar width), not over sidebar."""
        sizes = self._main_splitter.sizes()
        left_w = sizes[0] if sizes else 0
        self._top_bar.setGeometry(left_w, 0, self.width() - left_w, self._top_bar.height())
        self._top_bar.raise_()

    def _load_panel_layout_prefs(self) -> None:
        def _bool_pref(key: str, default: bool) -> bool:
            v = self._settings.value(key, default)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return default

        self._panel_layout_auto = _bool_pref("ui/panel_layout_auto", True)
        if self._settings.contains("ui/panel_manual_sidebar_full"):
            self._manual_sidebar_full = _bool_pref("ui/panel_manual_sidebar_full", True)
        else:
            self._manual_sidebar_full = _bool_pref("ui/panel_manual_sidebar", True)
        self._manual_inspector_visible = _bool_pref("ui/panel_manual_inspector", True)

    def _persist_panel_layout_prefs(self) -> None:
        try:
            self._settings.setValue("ui/panel_layout_auto", self._panel_layout_auto)
            self._settings.setValue("ui/panel_manual_sidebar_full", self._manual_sidebar_full)
            self._settings.setValue("ui/panel_manual_inspector", self._manual_inspector_visible)
        except Exception:
            pass

    def _apply_panel_layout(self, *, full_manual: bool = False) -> None:
        """Auto: width-based sidebar/inspector. Manual: refresh geometry on resize; full apply after toggles."""
        if self._panel_layout_auto:
            self._apply_responsive_panels_impl()
        elif full_manual:
            self._apply_manual_panel_layout_full()
        else:
            self._refresh_manual_panel_geometry()
        self._sync_panel_layout_top_bar()

    def _sync_panel_layout_top_bar(self) -> None:
        sw = self._main_splitter.sizes()
        sw0 = sw[0] if sw else 0
        # Glyph: checked = full-width rail; unchecked = compact 68px (vẫn còn sidebar).
        sidebar_expanded = sw0 > 80
        self._top_bar.set_panel_layout_controls(
            auto=self._panel_layout_auto,
            sidebar_on=sidebar_expanded,
            inspector_on=self._inspector.isVisible(),
        )

    def _restore_compact_filter_widget(self) -> None:
        """Return detached filter lists from compact popup scroll area into the sidebar layout."""
        scroll = self._compact_filter_scroll
        if scroll is not None:
            w = scroll.takeWidget()
            if w is not None:
                self._filter_panel.restore_filters_center(w)
            self._compact_filter_scroll = None
        self._filter_panel.ensure_filters_center_attached()

    def _finalize_compact_filter_popup(self) -> None:
        """Single cleanup path after compact filter popup closes."""
        popup = self._compact_filter_popup
        self._compact_filter_popup = None
        self._restore_compact_filter_widget()
        self._compact_filter_popup_closed_at = time.monotonic()
        self._nav_rail.set_filter_popup_active(False)
        if popup is not None:
            popup.deleteLater()
        from monostudio.ui_qt.style import release_stuck_mouse_grab

        release_stuck_mouse_grab(force=True)

    def _dismiss_compact_filter_popup(self) -> None:
        """Close compact filter popup and ensure filter lists return to the sidebar layout."""
        popup = self._compact_filter_popup
        if popup is not None and popup.isVisible():
            popup.hide()
            return
        if popup is not None:
            self._finalize_compact_filter_popup()
            return
        self._restore_compact_filter_widget()
        self._nav_rail.set_filter_popup_active(False)

    def _dismiss_chrome_popups_on_main_view_press(self) -> None:
        if self._compact_filter_popup is not None and self._compact_filter_popup.isVisible():
            self._dismiss_compact_filter_popup()

    def _finish_main_view_mouse_gestures(self) -> None:
        """Drop viewport mouse grabs left after main-view clicks (restores chrome hover)."""
        mv = self._main_view
        for view in (mv._tile_view, mv._list_view):
            cleanup = getattr(view, "_rb_force_cleanup", None)
            if callable(cleanup):
                cleanup()

    def _reset_pointer_hover_state(self) -> None:
        """Release popup grab / viewport grab after click on main content."""
        from monostudio.ui_qt.style import release_stuck_mouse_grab

        self._finish_main_view_mouse_gestures()
        release_stuck_mouse_grab()

    def _is_main_view_descendant(self, obj: object) -> bool:
        if not isinstance(obj, QWidget):
            return False
        w: QWidget | None = obj
        while w is not None:
            if w is self._main_view:
                return True
            w = w.parentWidget()
        return False

    def _set_sidebar_panel_visible(self, visible: bool) -> None:
        if visible:
            self._dismiss_compact_filter_popup()
            if self._dashboard_customize_sidebar_open():
                self._filter_panel.set_dashboard_customize_mode(True)
        self._sidebar_panel_visible = bool(visible)
        self._filter_panel.setVisible(visible)
        if visible:
            self._filter_panel.setMinimumWidth(self.SIDEBAR_PANEL_W)
            self._filter_panel.setMaximumWidth(self.SIDEBAR_PANEL_W)
            lay = self._sidebar_container.layout()
            if lay is not None:
                lay.activate()
            self._sidebar_container.updateGeometry()
        else:
            self._filter_panel.setMinimumWidth(0)
            self._filter_panel.setMaximumWidth(0)

    def _dashboard_customize_sidebar_open(self) -> bool:
        w = self._dashboard_page_widget
        return w is not None and w.is_customize_mode()

    def _sync_dashboard_sidebar_panel(self) -> None:
        """Dashboard browse: rail-only. Customize: full sidebar with widget palette."""
        if self._nav_rail.current_context() != "Dashboard":
            return
        in_customize = self._dashboard_customize_sidebar_open()
        self._filter_panel.set_dashboard_customize_mode(in_customize)
        if in_customize and self._dashboard_page_widget is not None:
            self._filter_panel.sync_dashboard_widget_slots(
                self._dashboard_page_widget.dashboard_slots()
            )
            self._apply_main_splitter_sidebar_metric("full")
        else:
            self._apply_main_splitter_sidebar_metric("compact")

    def _on_dashboard_customize_mode_changed(self, enabled: bool) -> None:
        _ = enabled
        self._sync_dashboard_sidebar_panel()

    def _on_dashboard_layout_changed(self, slots: object) -> None:
        if self._nav_rail.current_context() != "Dashboard":
            return
        if not self._dashboard_customize_sidebar_open():
            return
        self._filter_panel.sync_dashboard_widget_slots(slots)

    def _on_dashboard_widget_palette_toggled(self, widget_id: str, visible: bool) -> None:
        if self._dashboard_page_widget is None:
            return
        self._dashboard_page_widget.set_dashboard_widget_visible(widget_id, visible)

    def _apply_main_splitter_sidebar_metric(self, mode: str) -> None:
        """
        Set main splitter so the first pane width exactly matches the sidebar column (no dead gap).
        mode: 'compact' (rail only, 68px) | 'full' (rail + filter panel, 324px)
        """
        if (
            mode == "full"
            and self._nav_rail.current_context() == "Dashboard"
            and not self._dashboard_customize_sidebar_open()
        ):
            mode = "compact"
        w = max(0, self._main_splitter.width())
        if mode == "compact":
            self._set_sidebar_panel_visible(False)
            self._sidebar_container.setMinimumWidth(self.SIDEBAR_RAIL_W)
            self._sidebar_container.setMaximumWidth(self.SIDEBAR_RAIL_W)
            sw = min(self.SIDEBAR_RAIL_W, w)
            self._main_splitter.setSizes([sw, max(0, w - sw)])
        else:
            self._set_sidebar_panel_visible(True)
            self._sidebar_container.setMinimumWidth(self.SIDEBAR_FULL_W)
            self._sidebar_container.setMaximumWidth(self.SIDEBAR_FULL_W)
            sw = min(self.SIDEBAR_FULL_W, w)
            self._main_splitter.setSizes([sw, max(0, w - sw)])

    def _apply_manual_panel_layout_full(self) -> None:
        """Apply user-chosen sidebar / Inspector visibility (after TopBar toggles or first show in manual mode)."""
        if self._manual_sidebar_full:
            self._apply_main_splitter_sidebar_metric("full")
        else:
            sizes_now = self._main_splitter.sizes()
            if len(sizes_now) >= 2 and sizes_now[0] > self.SIDEBAR_RAIL_W:
                self._main_splitter_sizes_restore = list(sizes_now)
            self._apply_main_splitter_sidebar_metric("compact")

        cw = max(0, self._content_splitter.width())
        if self._manual_inspector_visible:
            self._inspector.setVisible(True)
            cs = list(self._content_splitter_sizes_restore)
            default_iw = max(240, min(360, max(0, cw // 4)))
            iw = int(cs[1]) if cs and len(cs) >= 2 and cs[1] > 0 else default_iw
            if cw > 200:
                iw = max(180, min(iw, cw - 100))
            else:
                iw = max(80, min(iw, max(0, cw - 40)))
            self._content_splitter.setSizes([max(0, cw - iw), iw])
        else:
            if self._inspector.isVisible():
                sizes_c = self._content_splitter.sizes()
                if len(sizes_c) >= 2 and sizes_c[1] > 0:
                    self._content_splitter_sizes_restore = list(sizes_c)
            self._inspector.setVisible(False)
            self._content_splitter.setSizes([cw, 0])

        self._update_title_bar_geometry()

    def _refresh_manual_panel_geometry(self) -> None:
        """Keep manual layout consistent on window resize without resetting user splitter drags."""
        if self._manual_sidebar_full:
            self._apply_main_splitter_sidebar_metric("full")
        else:
            self._apply_main_splitter_sidebar_metric("compact")

        cw = max(0, self._content_splitter.width())
        if not self._manual_inspector_visible:
            self._content_splitter.setSizes([cw, 0])
        elif self._inspector.isVisible():
            s = self._content_splitter.sizes()
            if len(s) >= 2 and s[1] > 0:
                right = min(s[1], max(1, cw - 120))
                self._content_splitter.setSizes([max(0, cw - right), right])

        self._update_title_bar_geometry()

    def _on_top_bar_layout_auto_clicked(self) -> None:
        self._panel_layout_auto = True
        self._persist_panel_layout_prefs()
        self._apply_panel_layout()

    def _on_top_bar_layout_sidebar_clicked(self) -> None:
        was_auto = self._panel_layout_auto
        self._panel_layout_auto = False
        if was_auto:
            sw = self._main_splitter.sizes()
            sw0 = sw[0] if sw else 0
            # Đang full → thu compact; đang compact (hoặc auto hẹp) → mở full.
            self._manual_sidebar_full = sw0 > self.SIDEBAR_RAIL_W
        else:
            self._manual_sidebar_full = not self._manual_sidebar_full
        self._persist_panel_layout_prefs()
        self._apply_panel_layout(full_manual=True)

    def _on_top_bar_layout_inspector_clicked(self) -> None:
        was_auto = self._panel_layout_auto
        self._panel_layout_auto = False
        if was_auto:
            self._manual_inspector_visible = not self._inspector.isVisible()
        else:
            self._manual_inspector_visible = not self._manual_inspector_visible
        self._persist_panel_layout_prefs()
        self._apply_panel_layout(full_manual=True)

    def _apply_responsive_panels_impl(self) -> None:
        """Narrow: hide inspector. Very narrow: hide filter panel (rail-only 68px)."""
        w = max(0, self._main_splitter.width())
        is_compact = not self._sidebar_panel_visible
        if w < self._WIDTH_HIDE_SIDEBAR:
            if not is_compact:
                sizes = self._main_splitter.sizes()
                if len(sizes) >= 2 and sizes[0] > 0:
                    self._main_splitter_sizes_restore = list(sizes)
            self._apply_main_splitter_sidebar_metric("compact")
            if self._inspector.isVisible():
                sizes = self._content_splitter.sizes()
                if len(sizes) >= 2 and sizes[1] > 0:
                    self._content_splitter_sizes_restore = list(sizes)
                self._inspector.setVisible(False)
            self._content_splitter.setSizes([self._content_splitter.width(), 0])
        elif w < self._WIDTH_HIDE_INSPECTOR:
            if is_compact:
                self._apply_main_splitter_sidebar_metric("full")
            else:
                self._apply_main_splitter_sidebar_metric("full")
            if self._inspector.isVisible():
                sizes = self._content_splitter.sizes()
                if len(sizes) >= 2 and sizes[1] > 0:
                    self._content_splitter_sizes_restore = list(sizes)
                self._inspector.setVisible(False)
            self._content_splitter.setSizes([self._content_splitter.width(), 0])
        else:
            if is_compact:
                self._apply_main_splitter_sidebar_metric("full")
            else:
                self._apply_main_splitter_sidebar_metric("full")
            if not self._inspector.isVisible():
                self._inspector.setVisible(True)
                self._content_splitter.setSizes(self._content_splitter_sizes_restore)

    def _apply_maximized_geometry_if_needed(self) -> None:
        """Ép geometry khít availableGeometry khi đang maximized (tránh khoảng hở do Qt/WM)."""
        if not self.isMaximized():
            return
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        desired = screen.availableGeometry()
        if self.geometry() != desired:
            self.setGeometry(desired)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Defer: Win32 topmost needs a valid HWND; panel layout matches previous first-paint behavior.
        QTimer.singleShot(0, self._deferred_after_show)

    def _deferred_after_show(self) -> None:
        if sys.platform == "win32" and self._window_always_on_top:
            self._apply_win32_always_on_top(True)
        self._apply_panel_layout(full_manual=not self._panel_layout_auto)
        QTimer.singleShot(4000, self._maybe_discord_schedule_due)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isMaximized():
            self._apply_maximized_geometry_if_needed()
        self._apply_panel_layout()
        self._update_title_bar_geometry()
        notification_service.update_overlay_geometry()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._top_bar.set_maximized(self.isMaximized())

    def _external_drop_watch_widget(self, obj: object) -> bool:
        if not isinstance(obj, QWidget):
            return False
        if isinstance(obj, QDialog):
            return False
        w: QWidget | None = obj
        while w is not None:
            if w is self:
                return True
            w = w.parentWidget()
        return False

    def _dispatch_explorer_file_drop(
        self,
        paths: list[Path],
        global_pos: QPoint,
        *,
        drop_event: QDropEvent | None = None,
    ) -> None:
        if not paths:
            return
        try:
            self._dispatch_explorer_file_drop_impl(paths, global_pos, drop_event=drop_event)
        except Exception:
            logging.getLogger(__name__).exception("Explorer file drop failed")

    def _dispatch_explorer_file_drop_impl(
        self,
        paths: list[Path],
        global_pos: QPoint,
        *,
        drop_event: QDropEvent | None = None,
    ) -> None:
        if self._inspector.isVisible():
            pos_in_inspector = self._inspector.mapFromGlobal(global_pos)
            if self._inspector.rect().contains(pos_in_inspector):
                if self._inspector.try_handle_ref_tab_external_drop(paths, global_pos):
                    return
        current = self._content_stack.currentWidget()
        logging.debug(
            "MainWindow explorer drop: paths=%s current=%s",
            [str(p) for p in paths],
            type(current).__name__ if current is not None else None,
        )
        pos_in_window = self.mapFromGlobal(global_pos)
        if current is self._reference_page_widget and self._reference_page_widget is not None:
            target = self._inbox_outbox_drop_target_at_global(
                self._reference_page_widget, global_pos, pos_in_window
            )
            pane = getattr(self._reference_page_widget, "_tree_pane", None)
            storage_root = pane._storage_root() if pane is not None else None
            copy_only = (
                drop_wants_copy(drop_event, paths=paths, storage_root=storage_root)
                if drop_event is not None
                else not (storage_root is not None and paths_under_root(paths, storage_root))
            )
            self._on_reference_drop_requested(paths, target, copy_only)
        elif current is self._inbox_page_widget and self._inbox_page_widget is not None:
            target = self._inbox_outbox_drop_target_at_global(self._inbox_page_widget, global_pos, pos_in_window)
            storage_root = get_inbox_root(self._project_root) if self._project_root else None
            copy_only = (
                drop_wants_copy(drop_event, paths=paths, storage_root=storage_root)
                if drop_event is not None
                else not (storage_root is not None and paths_under_root(paths, storage_root))
            )
            self._on_inbox_drop_requested(paths, target, copy_only)
        elif current is self._outbox_page_widget and self._outbox_page_widget is not None:
            target = self._inbox_outbox_drop_target_at_global(self._outbox_page_widget, global_pos, pos_in_window)
            storage_root = get_delivery_root(self._project_root) if self._project_root else None
            copy_only = (
                drop_wants_copy(drop_event, paths=paths, storage_root=storage_root)
                if drop_event is not None
                else not (storage_root is not None and paths_under_root(paths, storage_root))
            )
            self._on_outbox_drop_requested(paths, target, copy_only)
        elif current is self._internal_check_page_widget and self._internal_check_page_widget is not None:
            target = self._inbox_outbox_drop_target_at_global(self._internal_check_page_widget, global_pos, pos_in_window)
            storage_root = get_internal_check_root(self._project_root) if self._project_root else None
            copy_only = (
                drop_wants_copy(drop_event, paths=paths, storage_root=storage_root)
                if drop_event is not None
                else not (storage_root is not None and paths_under_root(paths, storage_root))
            )
            self._on_internal_check_drop_requested(paths, target, copy_only)

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if (
            et == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
            and self._is_main_view_descendant(obj)
        ):
            self._dismiss_chrome_popups_on_main_view_press()
        elif (
            et == QEvent.Type.MouseButtonRelease
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
            and self._is_main_view_descendant(obj)
        ):
            QTimer.singleShot(0, self._finish_main_view_mouse_gestures)
        if et not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return super().eventFilter(obj, event)
        if not self._external_drop_watch_widget(obj):
            return super().eventFilter(obj, event)
        if not isinstance(event, (QDragEnterEvent, QDragMoveEvent, QDropEvent)):
            return super().eventFilter(obj, event)
        if not event.mimeData().hasUrls():
            return super().eventFilter(obj, event)
        if et in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            event.acceptProposedAction()
            return True
        paths = paths_from_drop_event(event)
        if not paths:
            return super().eventFilter(obj, event)
        event.acceptProposedAction()
        self._dispatch_explorer_file_drop(
            paths, event_global_pos(event, self), drop_event=event
        )
        return True

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if accept_url_drag(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if accept_url_drag(event):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = paths_from_drop_event(event)
        if not paths:
            super().dropEvent(event)
            return
        accept_url_drag(event)
        self._dispatch_explorer_file_drop(
            paths, event_global_pos(event, self), drop_event=event
        )

    def _apply_pipeline_types_and_presets_metadata(self, meta: PipelineTypesAndPresets) -> None:
        from monostudio.core.pipeline_types_and_presets import department_icon_name

        self._dept_icon_map = {}
        for dept_id, ddef in meta.departments.items():
            slug = department_icon_name(dept_id, explicit=ddef.icon_name)
            if slug:
                self._dept_icon_map[dept_id] = slug
        self._type_short_name_map = {
            k: v.short_name for k, v in meta.types.items() if v.short_name
        }
        self._type_name_by_id = {k: v.name for k, v in meta.types.items()}
        self._type_aliases_by_id = {}
        for type_id, t in meta.types.items():
            aliases_raw = [type_id, t.name, t.short_name]
            aliases = {self._norm(a) for a in aliases_raw if isinstance(a, str) and a.strip()}
            if aliases:
                self._type_aliases_by_id[self._norm(type_id)] = aliases

    def _sync_pipeline_preset_metadata_ui(self) -> None:
        self._apply_pipeline_types_and_presets_metadata(
            load_pipeline_types_and_presets_for_project(self._project_root)
        )
        try:
            ctx = self._nav_rail.current_context()
            self._filter_panel.sync_nav_context(ctx, force=True)
        except Exception:
            pass

    @staticmethod
    def _norm(s: str | None) -> str:
        return (s or "").strip().casefold()

    @staticmethod
    def _path_matches_selection(path_or_str: Path | str, selection_id: str) -> bool:
        """True if path (asset/shot folder) matches selection_id (path string); for preserve_selection_id."""
        if not selection_id or not str(selection_id).strip():
            return False
        try:
            return Path(path_or_str).resolve() == Path(selection_id).resolve()
        except (OSError, TypeError):
            return str(path_or_str).strip() == str(selection_id).strip()

    def _selection_filter_key(self) -> tuple[str, str]:
        ctx = self._nav_rail.current_context() or ""
        type_id = (self.current_type or "").strip()
        return (ctx, type_id)

    def _remember_main_view_selection(self) -> None:
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            return
        key = self._selection_filter_key()
        sid = self._app_state.selection_id()
        if sid:
            self._main_view_selection_by_filter[key] = sid
        else:
            self._main_view_selection_by_filter.pop(key, None)

    def _recall_main_view_selection(self) -> str | None:
        return self._main_view_selection_by_filter.get(self._selection_filter_key())

    def _on_main_view_selection_id_changed(self, selection_id: object) -> None:
        sid = (selection_id or "").strip() if isinstance(selection_id, str) else None
        sid = sid or None
        # clearSelection() during filter reload/resort is not user intent — keep the asset selected.
        if sid is None and (
            getattr(self, "_filter_switch_in_progress", False)
            or getattr(self, "_context_switch_in_progress", False)
            or getattr(self._main_view, "_in_batch_set_items", False)
        ):
            return
        self._app_state.set_selection(sid)
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            return
        key = self._selection_filter_key()
        if sid:
            self._main_view_selection_by_filter[key] = sid
        else:
            self._main_view_selection_by_filter.pop(key, None)

    def _restore_main_view_selection_from_recall(self) -> None:
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            return
        sid = self._recall_main_view_selection() or self._app_state.selection_id()
        if not sid:
            return
        if self._main_view.select_item_by_path(Path(sid)):
            if self._app_state.selection_id() != sid:
                self._app_state.set_selection(sid)

    def _pull_browser_filters_from_sidebar(self) -> None:
        """Apply sidebar dept/type to MainWindow + AppController without emitting filter-changed signals."""
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            return
        filters = self._filter_panel.filters()
        new_dept = filters.current_department()
        new_type = None if ctx == "Shots" else filters.current_type()
        self.current_department = new_dept if isinstance(new_dept, str) and new_dept.strip() else None
        self.current_type = new_type if isinstance(new_type, str) and new_type.strip() else None
        self._controller.current_department = self.current_department
        self._controller.current_type = self.current_type
        self._app_state.set_filters(self.current_department, self.current_type)

    def filter_assets(self, assets: list[Asset], department: str | None, type_id: str | None) -> list[Asset]:
        """
        AND-only filtering for assets.
        - If department is None → allow all
        - If type is None → allow all
        - Asset must satisfy BOTH if both are set
        """
        out: list[Asset] = []
        dept_key = self._norm(department) if department is not None else ""
        type_key = self._norm(type_id) if type_id is not None else ""
        type_aliases = self._type_aliases_by_id.get(type_key) if type_id is not None else None
        if type_id is not None and not type_aliases:
            # Fallback: match exact normalized id only.
            type_aliases = {type_key}

        for a in assets:
            if type_id is not None:
                asset_type_key = self._norm(a.asset_type)
                if asset_type_key not in type_aliases:
                    continue
            if department is not None:
                if not any(self._norm(d.name) == dept_key for d in a.departments):
                    continue
            out.append(a)
        return out

    def _apply_search_filter(self, items: list[ViewItem], query: str) -> list[ViewItem]:
        """Filter ViewItems by substring match on name, type_badge, path (case-insensitive). Empty query = no filter."""
        q = (query or "").strip().lower()
        if not q:
            return items
        out: list[ViewItem] = []
        for item in items:
            name_norm = (item.name or "").lower()
            type_norm = (item.type_badge or "").lower()
            path_norm = str(item.path).lower()
            if q in name_norm or q in type_norm or q in path_norm:
                out.append(item)
        return out

    # Filter click handlers now live in AppController.

    def _on_department_filter_changed(self, _value=None) -> None:
        """Department switch: update dept context on cards — no full grid rebuild for Shots."""
        if getattr(self, "_filter_switch_in_progress", False):
            return
        if getattr(self, "_context_switch_in_progress", False):
            return
        ctx = self._nav_rail.current_context()
        if ctx == "Project Guide":
            if self._reference_page_widget is not None:
                dept = self._filter_panel.filters().current_department() or "reference"
                self._reference_page_widget.set_department(dept)
                dep_label, dep_icon = self._filter_panel.filters().get_department_display(dept)
                self._reference_page_widget.set_header_badge_display(label=dep_label, icon_name=dep_icon)
                self._update_reference_tag_badge()
            return
        if ctx not in ("Assets", "Shots"):
            return
        if ctx == "Assets" and self._entered_parent is not None:
            return
        self._filter_switch_in_progress = True
        try:
            if ctx == "Shots":
                self._set_main_view_department()
            else:
                self._sync_filter_state_from_sidebar()
                self._reload_main_view()
        finally:
            self._filter_switch_in_progress = False
            try:
                self._on_valid_selection_changed(self._main_view.has_valid_selection())
            except Exception:
                pass

    def _on_type_filter_changed(self, _value=None) -> None:
        # Type changes rebuild the item list; never allow that to trigger Open flows.
        if getattr(self, "_filter_switch_in_progress", False):
            return
        if getattr(self, "_context_switch_in_progress", False):
            return
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots", "Inbox", "Project Guide", SidebarContext.INTERNAL_CHECK.value, "Delivery"):
            return
        if ctx in ("Inbox", SidebarContext.INTERNAL_CHECK.value, "Delivery") and getattr(self, "_inbox_switch_cooldown", False):
            return
        if ctx == "Assets" and self._entered_parent is not None:
            return
        self._filter_switch_in_progress = True
        try:
            if ctx == "Assets":
                self._sync_filter_state_from_sidebar()
                self._main_view.clear()
            self._reload_main_view()
        finally:
            self._filter_switch_in_progress = False
            try:
                self._on_valid_selection_changed(self._main_view.has_valid_selection())
            except Exception:
                pass

    def _on_filter_state_changed(self, _value=None) -> None:
        """Deprecated alias — kept for any external callers."""
        self._on_type_filter_changed(_value)

    def _set_main_view_department(self, _value: object = None, *, defer_list_rebuild: bool = False) -> None:
        """Sync main view header + thumb badge + inspector preview with current department."""
        dep = self._controller.current_department
        label, icon_name = self._filter_panel.filters().get_department_display(dep) if dep else (None, None)
        self._main_view.set_active_department(
            dep,
            label=label,
            icon_name=icon_name,
            defer_list_rebuild=defer_list_rebuild,
        )
        if not defer_list_rebuild:
            self._restore_main_view_selection_from_recall()
        self._refresh_inspector_selection()

    def _set_main_view_type(self) -> None:
        """Sync main view type badge from sidebar (Character, Prop, shot types, …)."""
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            self._main_view.set_selected_asset_type(None)
            return
        if ctx == "Shots":
            self._main_view.set_selected_asset_type(None)
            return
        type_id = self._filter_panel.filters().current_type()
        if not type_id:
            self._main_view.set_selected_asset_type(None)
            return
        label, icon_name = self._filter_panel.filters().get_type_display(type_id)
        self._main_view.set_selected_asset_type(type_id, label=label, icon_name=icon_name)

    def _sync_main_view_header(self) -> None:
        """Header breadcrumbs (Assets → type → dept) — safe before project index is ready."""
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            return
        self._pull_browser_filters_from_sidebar()
        self._set_main_view_department(defer_list_rebuild=True)
        self._set_main_view_type()

    def _on_sidebar_department_clicked(self, department: object) -> None:
        if self._nav_rail.current_context() == "Schedule":
            self._apply_schedule_sidebar_filters()
            return
        self._controller.on_department_clicked(department)

    def _on_sidebar_type_clicked(self, type_id: object) -> None:
        if self._nav_rail.current_context() == "Schedule":
            self._apply_schedule_sidebar_filters()
            return
        self._controller.on_type_clicked(type_id)

    def _connect_inbox_outbox_title_row(self, title_row) -> None:
        title_row.type_clicked.connect(
            lambda tr=title_row: self._open_header_type_filter_picker(tr.filter_badge_widget())
        )
        title_row.department_clicked.connect(
            lambda tr=title_row: self._open_header_department_filter_picker(tr.filter_badge_widget())
        )

    def _update_reference_tag_badge(self) -> None:
        if self._reference_page_widget is None:
            return
        filters = self._filter_panel.filters()
        tag_ids = filters.current_tags()
        if not tag_ids:
            self._reference_page_widget.set_tag_filter_badges([])
            return
        badges: list[tuple[str, str]] = []
        for tag_id in tag_ids:
            label = filters.get_tag_display(tag_id)
            if not label:
                continue
            badges.append((label, filters.get_tag_color(tag_id) or "#a1a1aa", tag_id))
        self._reference_page_widget.set_tag_filter_badges(badges)

    def _on_reference_tag_badge_clear(self, tag_id: str) -> None:
        self._filter_panel.filters().remove_active_tag(tag_id)

    def _open_header_type_filter_picker(self, anchor) -> None:
        filters = self._filter_panel.filters()
        if filters is None or anchor is None:
            return
        filters.show_type_filter_popup(anchor)

    def _open_header_department_filter_picker(self, anchor) -> None:
        filters = self._filter_panel.filters()
        if filters is None or anchor is None:
            return
        filters.show_department_filter_popup(anchor)

    def _on_schedule_sidebar_filters_changed(self, *_args) -> None:
        self._apply_schedule_sidebar_filters()
        self._schedule_dashboard_refresh()

    def _on_schedule_sidebar_department_sync(self, department: object) -> None:
        """Wave drilldown / toolbar: align sidebar DEPARTMENTS highlight with timeline filter."""
        if self._nav_rail.current_context() != "Schedule":
            return
        dep = department if isinstance(department, str) and department.strip() else None
        self._filter_panel.filters().set_selected_department(dep, emit=False)
        self._apply_schedule_sidebar_filters()

    def _ensure_schedule_scope_for_entity_keys(self, keys: list[tuple[str, str]]) -> None:
        """Enable Assets/Shots scope toggles required to show dashboard-driven entity keys."""
        has_shot = any((kind or "").strip().lower() == "shot" for kind, _rel in keys)
        has_asset = any((kind or "").strip().lower() == "asset" for kind, _rel in keys)
        if not has_shot and not has_asset:
            return
        panel = self._filter_panel.filters()
        shots, assets = panel.entity_scope()
        if has_shot:
            shots = True
        if has_asset:
            assets = True
        panel.set_entity_scope(include_shots=shots, include_assets=assets, emit=False)

    def _apply_schedule_sidebar_filters(self) -> None:
        if self._schedule_page_widget is None or self._nav_rail.current_context() != "Schedule":
            return
        panel = self._filter_panel.filters()
        shots, assets = panel.entity_scope()
        type_id = panel.current_type()
        dept = panel.current_department()
        self._schedule_page_widget.sync_sidebar_filters(
            include_shots=shots,
            include_assets=assets,
            department=dept,
            type_id=type_id,
            type_aliases=self._type_aliases_for_id(type_id),
            allowed_department_ids=panel.schedule_visible_department_ids(),
        )
        dep_label, dep_icon = panel.get_department_display(dept) if dept else (None, None)
        self._main_view.set_active_department(dept, label=dep_label, icon_name=dep_icon)
        self._refresh_inspector_selection()

    def _push_dashboard_filter(self) -> None:
        """Mirror Schedule department visibility (picker whitelist + inspector hidden)."""
        if self._dashboard_page_widget is None:
            return
        from monostudio.core.schedule_dept_filter import (
            DEPT_SCOPE_LEAF,
            SCHEDULE_RESPECT_HIDDEN_KEY,
            load_inspector_hidden_departments,
        )

        respect_hidden = bool(
            self._settings.value(SCHEDULE_RESPECT_HIDDEN_KEY, True, type=bool)
        )
        shots, assets = self._filter_panel.filters().entity_scope()
        panel = self._filter_panel.filters()
        eligible = panel.schedule_eligible_department_ids()
        self._dashboard_page_widget.set_dept_filter(
            allowed_department_ids=panel.schedule_visible_department_ids(),
            workload_department_ids=eligible,
            workload_department_order=tuple(eligible),
            workload_shot_department_ids=panel.schedule_shot_department_ids(),
            workload_asset_department_ids=panel.schedule_asset_department_ids(),
            hidden_departments=load_inspector_hidden_departments(self._settings),
            respect_hidden=respect_hidden,
            dept_scope=DEPT_SCOPE_LEAF,
            include_shots=shots,
            include_assets=assets,
        )

    def _schedule_dashboard_refresh(self) -> None:
        """Coalesce rapid filter signals into a single dashboard refresh."""
        if self._dashboard_page_widget is None:
            return
        timer = getattr(self, "_dashboard_refresh_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._run_dashboard_refresh)
            self._dashboard_refresh_timer = timer
        timer.start(50)

    def _run_dashboard_refresh(self) -> None:
        if self._dashboard_page_widget is None or self._nav_rail.current_context() != "Dashboard":
            return
        self._refresh_dashboard_page()

    def _refresh_dashboard_page(self) -> None:
        """Reload dashboard metrics only — no main-view / inspector churn."""
        if self._dashboard_page_widget is None:
            return
        self._dashboard_page_widget.set_project_root(self._project_root)
        self._dashboard_page_widget.set_workspace_root(self._workspace_root)
        self._push_dashboard_filter()
        self._dashboard_page_widget.refresh(self._project_index)
        self._refresh_dashboard_mention_unread_dot()

    def _refresh_inspector_selection(self) -> None:
        """Push current selection into Inspector (main view or Schedule timeline)."""
        ctx = self._nav_rail.current_context()
        if ctx == "Schedule":
            item = self._schedule_inspector_item
        else:
            item = self._main_view.selected_view_item()
        dep = self._filter_panel.filters().current_department() or self.current_department
        if item is not None:
            self._inspector.set_item(item, active_department_hint=dep)
        elif ctx == "Schedule":
            self._inspector.set_item(None)
        # Assets/Shots: leave empty state to valid_selection_changed when nothing selected

    def _view_item_for_schedule_entity(self, entity_kind: str, entity_rel: str) -> ViewItem | None:
        if self._project_root is None or self._project_index is None:
            return None
        from monostudio.core.project_schedule import entity_rel_path

        rel_norm = (entity_rel or "").replace("\\", "/")
        kind = (entity_kind or "").strip().lower()
        if kind == "shot":
            for shot in self._project_index.shots:
                if entity_rel_path(self._project_root, shot.path).replace("\\", "/") == rel_norm:
                    return ViewItem(
                        kind=ViewItemKind.SHOT,
                        name=shot.name,
                        type_badge="shot",
                        path=shot.path,
                        departments_count=len(shot.departments),
                        ref=shot,
                    )
        elif kind == "asset":
            type_reg = TypeRegistry.for_project(self._project_root)
            for asset in self._project_index.assets:
                if entity_rel_path(self._project_root, asset.path).replace("\\", "/") == rel_norm:
                    type_folder = (
                        (type_reg.get_type_folder(asset.asset_type) or "").strip() if type_reg else ""
                    )
                    return ViewItem(
                        kind=ViewItemKind.ASSET,
                        name=asset.name,
                        type_badge=asset.asset_type,
                        path=asset.path,
                        departments_count=len(asset.departments),
                        ref=asset,
                        type_folder=type_folder,
                    )
        return None

    def _schedule_entity_key_from_view_item(self, item: ViewItem | None) -> tuple[str, str] | None:
        if self._project_root is None or item is None:
            return None
        ref = getattr(item, "ref", None)
        if not isinstance(ref, (Asset, Shot)):
            return None
        from monostudio.core.project_schedule import entity_rel_path

        kind = "shot" if item.kind == ViewItemKind.SHOT else "asset"
        rel = entity_rel_path(self._project_root, ref.path).replace("\\", "/")
        return kind, rel

    def _on_schedule_entity_row_selected(self, entity_kind: str, entity_rel: str) -> None:
        if self._nav_rail.current_context() != "Schedule":
            return
        item = self._view_item_for_schedule_entity(entity_kind, entity_rel)
        self._schedule_inspector_item = item
        self._refresh_schedule_cache()
        self._refresh_inspector_selection()

    def _on_schedule_jump_to_entity(
        self, entity_kind: str, entity_rel: str, department: str
    ) -> None:
        item = self._view_item_for_schedule_entity(entity_kind, entity_rel)
        name = (item.name if item is not None else "") or ""
        self._navigate_dashboard_to_entity(
            entity_kind=entity_kind,
            entity_name=name,
            department=department or "",
            entity_rel=entity_rel,
        )

    def _on_schedule_entity_row_cleared(self) -> None:
        if self._nav_rail.current_context() != "Schedule":
            return
        self._schedule_inspector_item = None
        self._refresh_inspector_selection()

    def _set_inspector_empty_hint_for_context(self, context: str) -> None:
        if context == "Schedule":
            self._inspector.set_empty_message("Select a shot or asset on the timeline")
        else:
            self._inspector.set_empty_message("Select an item to view details")

    def _type_aliases_for_id(self, type_id: str | None) -> set[str]:
        if not type_id:
            return set()
        key = self._norm(type_id)
        aliases = self._type_aliases_by_id.get(key)
        if aliases:
            return set(aliases)
        return {key}

    def _set_current_department(self, department, *, toggle_if_same: bool) -> None:
        new = department if isinstance(department, str) and department.strip() else None
        if toggle_if_same and new is not None and new == self.current_department:
            new = None
        if new == self.current_department:
            return
        self.current_department = new
        self._app_state.set_filters(self.current_department, self.current_type)
        if self._nav_rail.current_context() == "Project Guide" and self._reference_page_widget is not None:
            self._reference_page_widget.set_department(self.current_department or "reference")
            self._restore_project_guide_browse_state()
        self.departmentChanged.emit(new)

    def _set_current_type(self, type_id, *, toggle_if_same: bool) -> None:
        new = type_id if isinstance(type_id, str) and type_id.strip() else None
        if toggle_if_same and new is not None and new == self.current_type:
            new = None
        if new == self.current_type:
            return
        self._remember_main_view_selection()
        self.current_type = new
        self._app_state.set_filters(self.current_department, self.current_type)
        # When on Inbox, keep page filter in sync and restore tree for that type if we had one open.
        if self._nav_rail.current_context() == "Inbox" and self._inbox_page_widget is not None:
            self._inbox_page_widget.set_type_filter(self.current_type or "")
            self._restore_inbox_date_folder_state()
        # When on Outbox, same: restore tree for that type if we had a date folder open.
        if self._nav_rail.current_context() == "Delivery" and self._outbox_page_widget is not None:
            self._outbox_page_widget.set_type_filter(self.current_type or "")
            self._restore_outbox_date_folder_state()
        if self._nav_rail.current_context() == SidebarContext.INTERNAL_CHECK.value and self._internal_check_page_widget is not None:
            self._restore_internal_check_date_folder_state()
        self.typeChanged.emit(new)

    def _sync_filter_state_from_sidebar(self) -> None:
        """
        Keep centralized filter state in sync with the SidebarWidget selection
        when switching pages (Assets vs Shots) where SidebarWidget restores per-page state.
        For Inbox we do not clear type (source = Client/Freelancer); we pull sidebar type
        into our state without emitting, so _reload_main_view sees a valid source and
        no second reload wipes items.
        """
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            if ctx == "Inbox":
                # Sync sidebar → local state only (no emit) so _reload_main_view has a valid source filter.
                filters = self._filter_panel.filters()
                t = filters.current_type()
                d = filters.current_department()
                if t is not None:
                    self.current_type = t
                    self._app_state.set_filters(self.current_department, self.current_type)
                if d is not None:
                    self.current_department = d
                    self._app_state.set_filters(self.current_department, self.current_type)
            elif ctx in ("Delivery", "Outbox"):
                filters = self._filter_panel.filters()
                t = filters.current_type()
                if t is not None:
                    self.current_type = t
                    self._app_state.set_filters(self.current_department, self.current_type)
            elif ctx == "Project Guide":
                filters = self._filter_panel.filters()
                d = filters.current_department()
                if d is not None:
                    self.current_department = d
                    self._app_state.set_filters(self.current_department, self.current_type)
            elif ctx in ("Schedule", "Dashboard"):
                # Schedule/Dashboard filters live in sidebar schedule mode; keep Assets/Shots memory intact.
                return
            else:
                self._set_current_department(None, toggle_if_same=False)
                self._set_current_type(None, toggle_if_same=False)
            return
        filters = self._filter_panel.filters()
        new_dept = filters.current_department()
        new_type = filters.current_type()
        if (new_type or None) != self.current_type:
            self._remember_main_view_selection()
        self._pull_browser_filters_from_sidebar()
        self._set_main_view_type()

    def _current_type_name(self) -> str | None:
        if self.current_type is None:
            return None
        key = self._norm(self.current_type)
        # Prefer exact id match first, then normalized lookup fallback.
        return self._type_name_by_id.get(self.current_type) or self._type_name_by_id.get(key) or self.current_type

    def _sync_primary_action(self) -> None:
        context = self._nav_rail.current_context()
        if context == "Dashboard":
            self._main_view.set_primary_action(label="", enabled=False, tooltip="")
            return
        if context == "Schedule":
            self._main_view.set_primary_action(label="", enabled=False, tooltip="")
            return
        if context == "Shots":
            enabled = self._project_root is not None
            tooltip = None if enabled else "Select a project to create a new shot"
            self._main_view.set_primary_action(label="New Shot", enabled=enabled, tooltip=tooltip)
            self._main_view.set_browser_context("shot")
            return
        if context == "Assets":
            enabled = self._project_root is not None
            tooltip = None if enabled else "Select a project to create a new asset"
            self._main_view.set_primary_action(label="New Asset", enabled=enabled, tooltip=tooltip)
            self._main_view.set_browser_context("asset")
            return

        # Non-browser areas: keep the button visible but disabled.
        self._main_view.set_context_title(context)
        self._main_view.set_primary_action(label="", enabled=False, tooltip=f"{context} does not support creation yet.")

    def _on_primary_action_requested(self) -> None:
        context = self._nav_rail.current_context()
        if context == "Shots":
            self._create_shot()
            return
        if context == "Assets":
            self._create_asset()
            return
        return

    def _project_display_name(self) -> str | None:
        if self._project_root is None:
            return None
        for p in self._workspace_projects:
            if p.root == self._project_root:
                return p.name or self._project_root.name
        return self._project_root.name

    _WORKSPACE_STATS_WORKER = "workspace_quick_stats"
    _PRODUCTION_STATUS_BATCH_WORKER = "production_status_batch"
    _SHOT_REVIEW_RENDER_ENRICH = "shot_review_render_enrich"
    _ASSET_RENAME_WORKER = "asset_rename"
    _SKIP_STATUS_LOADING_MESSAGE = "Updating skip status…"
    _RENAME_LOADING_MESSAGE = "Renaming asset…"
    _PRODUCTION_STATUS_ASYNC_MIN = 3

    def _refresh_workspace_project_stats(self, *, schedule_aware: bool = False) -> None:
        """Rebuild per-project quick stats on the UI thread (light scans only)."""
        if schedule_aware:
            self._schedule_workspace_stats_refresh_async()
            return
        self._workspace_project_status = {}
        self._workspace_project_quick_stats = {}
        for proj in self._workspace_projects:
            try:
                stats = read_project_quick_stats(proj.root, schedule_aware=False)
                key = str(proj.root)
                self._workspace_project_status[key] = stats.status
                self._workspace_project_quick_stats[key] = stats
            except Exception:
                continue
        self._sync_top_bar()

    def _schedule_workspace_stats_refresh_async(self) -> None:
        """Deep schedule-aware stats in a worker — avoids freezing the UI after startup."""
        if not self._workspace_projects:
            return
        expected_root = self._workspace_root
        project_roots = [proj.root for proj in self._workspace_projects]

        def _run() -> tuple[Path | None, dict[str, str], dict[str, ProjectQuickStats]]:
            status: dict[str, str] = {}
            stats: dict[str, ProjectQuickStats] = {}
            for root in project_roots:
                try:
                    row = read_project_quick_stats(root, schedule_aware=True)
                    key = str(root)
                    status[key] = row.status
                    stats[key] = row
                except Exception:
                    continue
            return expected_root, status, stats

        task = WorkerTask(self._WORKSPACE_STATS_WORKER, _run, manager=self._worker_manager)
        self._worker_manager.submit_task(
            task,
            category=self._WORKSPACE_STATS_WORKER,
            replace_existing=True,
            debounce_ms=400,
        )

    def _apply_workspace_stats_worker_result(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 3:
            return
        expected_root, status, stats = result
        if self._workspace_root != expected_root:
            return
        if isinstance(status, dict):
            self._workspace_project_status = status
        if isinstance(stats, dict):
            self._workspace_project_quick_stats = stats
        self._sync_top_bar()

    def _on_project_browser_status_chosen(self, project_path: object, status: object) -> None:
        from monostudio.core.workspace_reader import (
            PROJECT_BROWSER_STATUS_KEYS,
            read_project_quick_stats,
            write_project_status_override,
        )

        try:
            root = Path(project_path)
        except (TypeError, OSError):
            return
        if status is None:
            sid = None
        else:
            sid = str(status).strip().upper()
            if sid and sid not in PROJECT_BROWSER_STATUS_KEYS:
                return
        if not write_project_status_override(root, sid):
            return
        key = str(root)
        try:
            stats = read_project_quick_stats(root, schedule_aware=True)
        except Exception:
            return
        self._workspace_project_status[key] = stats.status
        self._workspace_project_quick_stats[key] = stats
        self._sync_top_bar()

    def _open_project_picker(self) -> None:
        self._schedule_workspace_stats_refresh_async()
        dlg = ProjectPickerDialog(
            workspace_root=self._workspace_root,
            workspace_projects=self._workspace_projects,
            quick_stats_by_root=self._workspace_project_quick_stats,
            status_by_root=self._workspace_project_status,
            current_project_root=self._project_root,
            thumbnail_manager=self._thumbnail_manager,
            parent=self,
        )
        dlg.project_selected.connect(self._switch_project)
        dlg.exec()

    def _sync_top_bar(self) -> None:
        self._nav_rail.set_projects(
            self._workspace_projects,
            current_root=self._project_root,
            status_by_root=self._workspace_project_status,
        )
        display_name = self._project_display_name()
        self._filter_panel.set_project_display_name(
            display_name, project_root=self._project_root
        )
        self._top_bar.set_project_display_name(display_name)
        from monostudio.core.user_identity import get_current_user

        user = get_current_user(self._workspace_root)
        self._top_bar.set_notification_context(
            self._workspace_root,
            self._project_root,
            user_id=user.id if user is not None else "",
        )

    def _copy_project_inventory(self) -> None:
        """
        v1.2 Candidate 3:
        - Explicit trigger only
        - Read-only: uses current in-memory project index ONLY
        - Writes clipboard ONLY (plain text)
        - Silent no-op on failure
        """
        text = self._inventory_text_project(include_assets=True, include_shots=True)
        if text is None:
            return
        self._copy_to_clipboard(text)

    def _inventory_project_name(self) -> str | None:
        if self._project_root is None:
            return None
        # Prefer already-discovered workspace project name (no filesystem reads here).
        for p in self._workspace_projects:
            if p.root == self._project_root:
                return p.name
        return self._project_root.name

    def _inventory_text_project(self, *, include_assets: bool, include_shots: bool) -> str | None:
        """
        Deterministic plain-text inventory from in-memory index only.
        Formatting matches existing Tools -> Copy Project Inventory output when both sections included.
        """
        if self._project_index is None:
            return None
        project_name = self._inventory_project_name()
        if project_name is None:
            return None

        lines: list[str] = []
        lines.append(f"Project: {project_name}")

        if include_assets:
            lines.append("")
            lines.append("Assets:")
            for asset in self._project_index.assets:
                lines.append(f"  {asset.name}")
                for dept in asset.departments:
                    lines.append(f"    - {dept.name}")

        if include_shots:
            lines.append("")
            lines.append("Shots:")
            for shot in self._project_index.shots:
                lines.append(f"  {shot.name}")
                for dept in shot.departments:
                    lines.append(f"    - {dept.name}")

        return "\n".join(lines).strip() + "\n"

    def _inventory_text_item(self, kind: str, name: str, departments: list[str]) -> str:
        lines: list[str] = [f"{kind}: {name}"]
        for d in departments:
            lines.append(f"  - {d}")
        return "\n".join(lines).strip() + "\n"

    def _copy_to_clipboard(self, text: str) -> None:
        cb = QApplication.clipboard()
        if cb is None:
            return
        cb.setText(text)

    def _on_sidebar_context_menu_requested(self, context_text: str, global_pos) -> None:
        # Contextual inventory (read-only) from existing in-memory index only.
        if self._project_index is None:
            return
        if context_text not in ("Assets", "Shots"):
            return

        menu = QMenu(self)
        if context_text == "Assets":
            act = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Assets Inventory")
        else:
            act = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Shots Inventory")
        stress_act = None
        if stress_profiler_enabled():
            stress_act = menu.addAction("Stress diagnostics…")
        chosen = menu.exec(global_pos)
        if chosen == stress_act:
            self._open_stress_diagnostics()
            return
        if chosen != act:
            return

        if context_text == "Assets":
            text = self._inventory_text_project(include_assets=True, include_shots=False)
        else:
            text = self._inventory_text_project(include_assets=False, include_shots=True)
        if text is None:
            return
        self._copy_to_clipboard(text)

    def _on_copy_item_inventory_requested(self, item: ViewItem) -> None:
        # Item-level contextual inventory (asset/shot only).
        if self._project_index is None:
            return
        if item.kind == ViewItemKind.ASSET and isinstance(item.ref, Asset):
            depts = [d.name for d in item.ref.departments]
            self._copy_to_clipboard(self._inventory_text_item("Asset", item.ref.name, depts))
            return
        if item.kind == ViewItemKind.SHOT and isinstance(item.ref, Shot):
            depts = [d.name for d in item.ref.departments]
            self._copy_to_clipboard(self._inventory_text_item("Shot", item.ref.name, depts))
            return

    def _on_paste_thumbnail_requested(self, item: object) -> None:
        """
        Explicit override only:
        - Available only from Inspector thumbnail UI / preview context menu.
        - Reads image from clipboard, normalizes, writes thumbnail.
        - When a department is active, writes to .meta/thumb_{dept}.user.png.
        """
        if not isinstance(item, ViewItem):
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return

        kind = "asset" if item.kind == ViewItemKind.ASSET else "shot"
        active_dept = (self._controller.current_department or "").strip() or None
        try:
            self._clipboard_thumbs.paste_thumbnail(
                item_root=item.path,
                kind=kind,
                item_id=str(item.path),
                department=active_dept,
                fmt="png",
            )
        except Exception as e:
            QMessageBox.critical(self, "Paste Thumbnail", str(e))
            return

        self._ensure_entity_meta_watched(Path(item.path))
        self._inspector.refresh_thumbnail()
        self._main_view.invalidate_thumbnail(item.path, department=active_dept)

    def _on_remove_thumbnail_requested(self, item: object) -> None:
        """Remove user thumbnail files for the item (and active department if set)."""
        if not isinstance(item, ViewItem):
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        active_dept = (self._controller.current_department or "").strip() or None
        root = Path(item.path)
        if not root.is_dir():
            return
        removed = False
        for name in ("thumbnail.user.png", "thumbnail.user.jpg"):
            p = root / name
            if p.is_file():
                try:
                    p.unlink()
                    removed = True
                except OSError:
                    pass
        if active_dept:
            meta = root / ".meta"
            for name in (f"thumb_{active_dept}.user.png", f"thumb_{active_dept}.user.jpg"):
                p = meta / name
                if p.is_file():
                    try:
                        p.unlink()
                        removed = True
                    except OSError:
                        pass
        if removed:
            self._app_state.invalidate_thumbnails([str(item.path)])
            self._inspector.refresh_thumbnail()
            self._main_view.invalidate_thumbnail(item.path, department=active_dept)

    def _on_thumbnail_updated(self, item_id: object) -> None:
        """
        UI refresh hook for explicit thumbnail overrides.
        Current convention: item_id is an absolute path string.
        """
        if not isinstance(item_id, str) or not item_id.strip():
            return
        try:
            p = Path(item_id)
        except Exception:
            return
        self._app_state.invalidate_thumbnails([item_id])
        # If Inspector is currently showing this item, refresh it too.
        try:
            cur = self._main_view.selected_view_item()
            if cur and cur.path == p:
                self._inspector.refresh_thumbnail()
        except Exception:
            pass

    def _on_delete_requested(self, item: ViewItem) -> None:
        """
        Move asset/shot to project trash (recoverable):
        - Requires file watcher to be paused (toggle in top bar)
        - Confirmation requires typing exact folder name
        - On success: update in-memory index and app_state, clear inspector if needed, refresh UI
        """
        if self._project_index is None or self._project_root is None:
            return
        if item.kind.value not in ("asset", "shot"):
            return
        if not self._watcher_manually_disabled:
            notification_service.warning(
                "Pause the file watcher (click the eye icon in the top bar) before moving items to Trash."
            )
            return

        path = item.path
        name = path.name
        kind_label = "Asset" if item.kind.value == "asset" else "Shot"

        dialog = DeleteConfirmDialog(
            kind_label=kind_label,
            folder_name=name,
            absolute_path=path,
            parent=self,
            move_to_trash=True,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        try:
            if not path.exists():
                return
        except OSError:
            return

        try:
            move_asset_or_shot_to_trash(self._project_root, path, item.kind.value)
        except TrashError as e:
            notification_service.error(f"Could not move to Trash: {e}")
            return
        except (OSError, PermissionError) as e:
            logging.warning("Move to trash failed: %s", e)
            notification_service.error(
                f"Move to Trash failed: {e}. Close any app using files in this folder and try again."
            )
            return
        except Exception as e:
            logging.exception("Move to trash failed unexpectedly: %s", e)
            notification_service.error(f"Move to Trash failed: {e}")
            return

        cur = self._main_view.selected_view_item()
        if cur is not None and getattr(cur, "path", None) == path:
            self._inspector.set_item(None)
        self._app_state.invalidate_thumbnails([str(path)])

        self._project_index = build_project_index(self._project_index.root)
        self._app_state.update_assets(list(self._project_index.assets))
        self._app_state.update_shots(list(self._project_index.shots))
        self._app_state.commit_immediate()
        self._filter_panel.set_project_index(self._project_index)
        self._reload_main_view()
        self._sync_primary_action()
        notification_service.success(f"Moved {kind_label} '{name}' to Trash.")

    def _on_trash_changed_from_trash_page(self) -> None:
        """After restore or permanent delete from Trash page: rescan pipeline index."""
        if self._project_root is None:
            return
        self._rescan_project()
        self._reload_main_view()

    def _refresh_dashboard_if_visible(self) -> None:
        if self._dashboard_page_widget is None or self._nav_rail.current_context() != "Dashboard":
            return
        self._schedule_dashboard_refresh()

    def _refresh_schedule_if_visible(self) -> None:
        if self._schedule_page_widget is None or self._nav_rail.current_context() != "Schedule":
            return
        self._schedule_page_widget.refresh(self._project_index)
        self._apply_schedule_sidebar_filters()

    def _on_schedule_changed(self) -> None:
        self._refresh_dashboard_if_visible()
        self._refresh_schedule_if_visible()
        self._refresh_schedule_cache()

    def _refresh_schedule_cache(self) -> None:
        if self._project_root is None or self._project_index is None:
            self._main_view.set_planned_schedule_bars(None, None)
            self._inspector.set_schedule_bars(None)
            return
        try:
            from monostudio.core.project_schedule import read_project_schedule
            from monostudio.core.schedule_planner import build_planned_bars

            schedule = read_project_schedule(self._project_root)
            bars = build_planned_bars(
                self._project_root,
                self._project_index,
                schedule,
                include_shots=True,
                include_assets=True,
            )
            self._main_view.set_planned_schedule_bars(bars, schedule)
            self._inspector.set_schedule_bars(bars)
            if self._project_root is not None:
                try:
                    dept_reg = DepartmentRegistry.for_project(self._project_root)
                    labels = {
                        d: dept_reg.get_department_label(d) or d for d in dept_reg.get_departments()
                    }
                    self._inspector.set_schedule_dept_labels(labels)
                except Exception:
                    self._inspector.set_schedule_dept_labels({})
            if self._nav_rail.current_context() == "Schedule":
                if self._schedule_inspector_item is not None:
                    self._inspector.set_item(
                        self._schedule_inspector_item,
                        active_department_hint=self._filter_panel.filters().current_department()
                        or self.current_department,
                    )
            else:
                selected = self._main_view.selected_view_item()
                if selected is not None:
                    self._inspector.set_item(selected, active_department_hint=self.current_department)
        except Exception:
            logging.getLogger(__name__).debug("schedule cache refresh failed", exc_info=True)

    def _on_inspector_edit_allocation(self) -> None:
        if not self._can_edit_schedule():
            return
        dep = (self.current_department or "").strip()
        if not dep:
            QMessageBox.information(
                self,
                "Schedule",
                "Select a department in the sidebar first.",
            )
            return
        item = self._main_view.selected_view_item()
        key = self._schedule_entity_key_from_view_item(item)
        if key is None:
            return
        kind, rel = key
        if self._schedule_page_widget is not None:
            self._schedule_page_widget.open_allocate_for_entity(kind, rel, dep)
            return
        self._open_schedule_allocate_dialog(kind, rel, dep)

    def _open_schedule_allocate_dialog(self, kind: str, rel: str, department: str) -> None:
        if not self._can_edit_schedule():
            return
        if self._project_root is None or self._project_index is None:
            return
        from monostudio.core.department_registry import DepartmentRegistry
        from monostudio.core.project_schedule import allocation_for_row, read_project_schedule
        from monostudio.ui_qt.schedule_allocate_dialog import ScheduleAllocateDialog
        from monostudio.ui_qt.schedule_autoplan_dialog import entity_options_from_index

        schedule = read_project_schedule(self._project_root)
        existing = allocation_for_row(
            schedule,
            entity_kind=kind,
            entity_rel=rel,
            department=department,
        )
        dept_reg = DepartmentRegistry.for_project(self._project_root)
        dept_labels = {
            d: dept_reg.get_department_label(d) or d for d in dept_reg.get_departments()
        }
        entities = entity_options_from_index(
            self._project_index,
            include_shots=True,
            include_assets=True,
        )
        dlg = ScheduleAllocateDialog(
            parent=self,
            project_root=self._project_root,
            workspace_root=self._workspace_root,
            entities=entities,
            dept_labels=dept_labels,
            existing=existing,
            preset_kind=kind,
            preset_rel=rel,
            preset_department=department,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._on_schedule_changed()

    def _on_inspector_assignee_changed(
        self,
        entity_kind: str,
        entity_rel: str,
        department: str,
        assignee_ids: list,
    ) -> None:
        if not self._can_edit_schedule():
            return
        if self._project_root is None:
            return
        from dataclasses import replace

        from monostudio.core.project_schedule import (
            AllocationBulkPatch,
            ScheduleAllocation,
            allocations_for_row,
            allocation_for_row,
            bulk_patch_allocations,
            merged_assignee_ids_for_row,
            new_allocation_id,
            read_project_schedule,
            upsert_allocation_for_row,
        )
        from monostudio.core.schedule_planner import (
            bars_for_row,
            build_planned_bars,
            next_unmet_goal_in_row,
        )
        from monostudio.core.user_identity import build_schedule_assignee_fields, normalize_assignee_ids

        kind = (entity_kind or "").strip().lower()
        rel = (entity_rel or "").replace("\\", "/").strip()
        dept = (department or "").strip()
        if not kind or not rel or not dept:
            return

        schedule = read_project_schedule(self._project_root)
        bar = None
        if self._project_index is not None:
            bars = build_planned_bars(
                self._project_root,
                self._project_index,
                schedule,
                include_shots=True,
                include_assets=True,
            )
            dept_goals = bars_for_row(bars, kind, rel, dept)
            bar = next_unmet_goal_in_row(dept_goals) or (dept_goals[0] if dept_goals else None)

        existing = None
        bar_alloc_id = (bar.allocation_id if bar is not None else "") or ""
        if bar_alloc_id:
            existing = next((a for a in schedule.allocations if a.id == bar_alloc_id), None)
        if existing is None:
            existing = allocation_for_row(
                schedule,
                entity_kind=kind,
                entity_rel=rel,
                department=dept,
            )

        if existing is not None:
            start_s, due_s = existing.start, existing.due
            aid = existing.id
            note = existing.note
        elif bar is not None:
            start_s, due_s = bar.start.isoformat(), bar.due.isoformat()
            aid = bar.allocation_id or new_allocation_id()
            note = bar.note or ""
        else:
            return

        ids, names, legacy_id, legacy_name = build_schedule_assignee_fields(
            self._workspace_root,
            normalize_assignee_ids(assignee_ids),
        )
        row_allocs = allocations_for_row(
            schedule,
            entity_kind=kind,
            entity_rel=rel,
            department=dept,
        )
        from monostudio.core.department_status_registry import default_target_status_for_department
        from monostudio.core.schedule_assign_notify import (
            collect_previous_assignee_ids,
            notify_new_schedule_assignments,
        )

        entity_name = rel.rsplit("/", 1)[-1] if rel else ""
        prev_assignee_ids = merged_assignee_ids_for_row(
            schedule,
            entity_kind=kind,
            entity_rel=rel,
            department=dept,
        )
        if not prev_assignee_ids:
            prev_assignee_ids = collect_previous_assignee_ids(
                existing,
                bar_assignee_ids=bar.assignee_ids if bar is not None else None,
                bar_assignee_id=(bar.assignee_id if bar is not None else "") or "",
            )

        if row_allocs:
            row_ids = [a.id for a in row_allocs]
            try:
                updated = bulk_patch_allocations(
                    self._project_root,
                    row_ids,
                    AllocationBulkPatch(
                        assignee_ids=ids,
                        assignees=names,
                        assignee_id=legacy_id,
                        assignee=legacy_name,
                    ),
                )
            except OSError:
                return
            if not updated:
                return
            alloc = replace(
                row_allocs[0],
                assignee_ids=ids,
                assignees=names,
                assignee_id=legacy_id,
                assignee=legacy_name,
            )
        else:
            target_status_id = (
                (bar.target_status_id if bar is not None else "")
                or (existing.target_status_id if existing is not None else "")
                or ""
            )
            if not target_status_id:
                target_status_id = default_target_status_for_department(self._project_root, dept)
            alloc = ScheduleAllocation(
                id=aid,
                entity_kind=kind,
                entity_rel=rel,
                department=dept,
                start=start_s,
                due=due_s,
                assignee_ids=ids,
                assignees=names,
                assignee_id=legacy_id,
                assignee=legacy_name,
                note=note,
                target_status_id=target_status_id,
            )
            try:
                upsert_allocation_for_row(self._project_root, alloc)
            except OSError:
                return

        try:
            notify_new_schedule_assignments(
                self._project_root,
                self._workspace_root,
                previous=existing,
                allocation=alloc,
                entity_display=entity_name,
                previous_assignee_ids=prev_assignee_ids,
            )
        except OSError:
            pass
        self._sync_assign_inbox_alerts()
        self._on_schedule_changed()
        if self._schedule_page_widget is not None:
            self._schedule_page_widget.refresh(self._project_index)

    # --- user identity (serverless, Dropbox roster) ------------------------
    def _current_author_name(self) -> str:
        """Resolved studio user name for authoring notes (falls back to OS name)."""
        from monostudio.core.user_identity import get_current_user_display_name

        return get_current_user_display_name(self._workspace_root)

    def _can_edit_schedule(self) -> bool:
        from monostudio.core.schedule_permissions import can_edit_schedule

        return can_edit_schedule(self._workspace_root)

    def _refresh_schedule_edit_access(self) -> None:
        editable = self._can_edit_schedule()
        self._inspector.set_schedule_editable(editable)
        if self._schedule_page_widget is not None:
            self._schedule_page_widget.set_schedule_editable(editable)

    def _refresh_user_button(self) -> None:
        """Sync the top-bar avatar from the currently resolved studio user."""
        from monostudio.core.access_control import is_admin_capable
        from monostudio.core.user_identity import avatar_path, get_current_user
        from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio
        from monostudio.ui_qt.top_bar import _TOPBAR_ACTION_BTN_W, _UserAvatarButton

        self._top_bar.set_can_manage_team(bool(is_admin_capable()))
        user = get_current_user(self._workspace_root)
        if user is not None:
            side = max(20, _TOPBAR_ACTION_BTN_W - 2 * _UserAvatarButton._INSET)
            dpr = effective_device_pixel_ratio(self._top_bar)
            pix = avatar_pixmap_for(
                avatar_path(self._workspace_root, user),
                user.initials,
                user.color_hex,
                side,
                dpr=dpr,
            )
            self._top_bar.set_identity(user.name, user.color_hex, user.initials, pix)
        else:
            self._top_bar.set_identity(None)
        self._top_bar.set_notification_context(
            self._workspace_root,
            self._project_root,
            user_id=user.id if user is not None else "",
        )
        self._refresh_schedule_edit_access()

    def _schedule_identity_prompt(self) -> None:
        """Prompt sign-in once workspace is ready — never while splash is still on screen."""
        if self._startup_complete and self.isVisible():
            QTimer.singleShot(0, self._ensure_identity_for_workspace)
        else:
            self._identity_prompt_pending = True

    def apply_pending_window_state(self) -> None:
        """Apply saved maximize after first dark paint (splash transition — avoids white fullscreen flash)."""
        if self._pending_restore_maximized:
            self._pending_restore_maximized = False
            self._apply_restore_maximized()

    def complete_startup(self) -> None:
        """Called from app.py after splash.dismiss; runs deferred sign-in prompt."""
        self._startup_complete = True
        if self._identity_prompt_pending:
            self._identity_prompt_pending = False
            QTimer.singleShot(0, self._ensure_identity_for_workspace)
        pending = (self._pending_deep_link or "").strip()
        if pending:
            self._pending_deep_link = None
            QTimer.singleShot(200, lambda u=pending: self.handle_deep_link(u))

    def set_pending_deep_link(self, url: str) -> None:
        self._pending_deep_link = (url or "").strip() or None

    def handle_deep_link(self, url: str) -> None:
        """Open MONOS from monostudio:// links (e.g. Discord assign buttons)."""
        from monostudio.core.deep_link import parse_assign_deep_link
        from monostudio.core.notification_copy import pick_copy

        link = parse_assign_deep_link(url)
        if link is None:
            self.present()
            return

        def _run() -> None:
            self.present()
            if self._workspace_root is None:
                notification_service.warning(
                    pick_copy(
                        "Chọn workspace trong Settings trước khi mở liên kết MONOS.",
                        "Select a workspace in Settings before opening a MONOS link.",
                    )
                )
                return
            from monostudio.core.assign_inbox import find_assign_inbox_across_projects, resolve_assign_entity_path

            found = find_assign_inbox_across_projects(self._workspace_root, link.inbox_id)
            if found is None:
                notification_service.warning(
                    pick_copy(
                        "Không tìm thấy giao việc này trong workspace hiện tại.",
                        "Could not find that assignment in the current workspace.",
                    )
                )
                return
            project_root, inbox_item = found
            try:
                if self._project_root is None or self._project_root.resolve() != project_root.resolve():
                    self._apply_project_root(str(project_root.resolve()), save=True)
            except OSError:
                self._apply_project_root(str(project_root), save=True)
            entity = resolve_assign_entity_path(project_root, item_rel=inbox_item.item_rel)
            if entity is None:
                notification_service.warning(
                    pick_copy(
                        "Không tìm thấy asset/shot của giao việc này.",
                        "Could not find the asset or shot for this assignment.",
                    )
                )
                return
            self._navigate_to_entity_for_notes(entity, inbox_item.department)
            self._inspector.set_inspector_tab_index(2)
            self._sync_assign_inbox_alerts()
            if link.action == "confirm" and not inbox_item.confirmed:
                from monostudio.ui_qt.notification.store import UserAlertPayload

                self._prompt_assign_confirm(
                    inbox_item,
                    UserAlertPayload(
                        item_rel=inbox_item.item_rel,
                        item_display=inbox_item.item_display,
                        assign_inbox_id=inbox_item.id,
                        department=inbox_item.department,
                        from_name=inbox_item.from_name,
                        from_user_id=inbox_item.from_user_id,
                    ),
                )

        if self._startup_complete:
            QTimer.singleShot(0, _run)
        else:
            self._pending_deep_link = url

    def present(self, *, open_notifications: bool = False) -> None:
        """Raise main window (from tray, toast, or second instance)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if open_notifications:
            self._top_bar.open_noti_dropdown()

    def request_close(self) -> None:
        """User clicked window close — may hide to tray per settings."""
        self.close()

    def apply_recent_task(
        self, task: object, *, open_dcc: bool = False, present: bool = True
    ) -> None:
        if open_dcc:
            if present:
                self._on_recent_task_double_clicked(task)
            else:
                self._open_recent_task_file_silent(task)
        elif present:
            self.present()
            self._on_recent_task_clicked(task)
        else:
            self._on_recent_task_clicked(task)

    def open_tray_entity(
        self,
        *,
        item_type: str,
        item_path: Path,
        department: str | None = None,
        type_id: str | None = None,
    ) -> None:
        """Open asset or shot from tray mini popup with filters aligned to sidebar."""
        kind = (item_type or "").strip().lower()
        if kind not in ("asset", "shot"):
            return
        self.present()
        ctx = "Assets" if kind == "asset" else "Shots"
        self._open_pipeline_entity_in_main_view(
            context=ctx,
            path=Path(item_path),
            type_id=type_id,
            department=department,
        )

    def _entity_has_department(self, ref: Asset | Shot, department: str) -> bool:
        dept_key = self._norm(department)
        if not dept_key:
            return False
        return any(self._norm(d.name) == dept_key for d in ref.departments)

    def _pipeline_ref_for_path(self, item_path: Path, item_type: str) -> Asset | Shot | None:
        if self._project_index is None:
            return None
        try:
            target = item_path.resolve()
        except OSError:
            target = item_path
        kind = (item_type or "").strip().lower()

        def _matches(p: Path) -> bool:
            try:
                return p.resolve() == target
            except OSError:
                return p == item_path

        if kind == "asset":
            for asset in self._project_index.assets:
                if _matches(asset.path):
                    return asset
        elif kind == "shot":
            for shot in self._project_index.shots:
                if _matches(shot.path):
                    return shot
        return None

    def _sync_tray_filter_state(
        self,
        *,
        item_type: str,
        department: str | None,
        type_id: str | None,
    ) -> None:
        """Update sidebar/controller filters for tray open without raising the window."""
        kind = (item_type or "").strip().lower()
        if kind not in ("asset", "shot"):
            return
        ctx = self._nav_rail.current_context()
        if ctx not in ("Assets", "Shots"):
            return
        filters = self._filter_panel.filters()
        filters.set_mode("assets" if kind == "asset" else "shots")
        dept = (department or "").strip() or None
        typ = (type_id or "").strip() or None
        if typ:
            filters.set_selected_type(typ, emit=False)
        if dept:
            filters.set_selected_department(dept, emit=False)
        elif typ:
            filters.set_selected_department(filters.current_department(), emit=False)
        self._sync_filter_state_from_sidebar()

    def open_tray_entity_file(
        self,
        *,
        item_type: str,
        item_path: Path,
        department: str | None = None,
        type_id: str | None = None,
    ) -> None:
        """Tray double-click: open work file in DCC without focusing main window."""
        ref = self._pipeline_ref_for_path(Path(item_path), item_type)
        if ref is None:
            return
        self._sync_tray_filter_state(
            item_type=item_type,
            department=department,
            type_id=type_id,
        )
        try:
            if self._controller.smart_open(item=ref, force_dialog=False, parent=self):
                self._refresh_recent_tasks()
        except Exception as e:
            logging.warning("Tray DCC launch failed: %s", e, exc_info=True)
            QMessageBox.critical(self, "Open DCC", str(e))

    def _open_recent_task_file_silent(self, task: object) -> None:
        from monostudio.ui_qt.recent_tasks_store import RecentTask

        if not isinstance(task, RecentTask):
            return
        ref = self._pipeline_ref_for_path(Path(task.item_path), task.item_type)
        if ref is None:
            return
        self._controller.sync_filter_state(
            department=task.department, type_id=self.current_type
        )
        self._filter_panel.filters().set_selected_department(task.department, emit=False)
        dept = (task.department or "").strip()
        dcc = (task.dcc or "").strip()
        try:
            if dept and dcc:
                self._controller.open_with_dcc(
                    item=ref, department=dept, dcc=dcc, parent=self
                )
            elif self._controller.smart_open(item=ref, force_dialog=False, parent=self):
                pass
            self._refresh_recent_tasks()
        except Exception as e:
            logging.warning("Tray DCC launch failed (recent): %s", e, exc_info=True)
            QMessageBox.critical(self, "Open DCC", str(e))

    def restore_last_project_from_settings(self) -> None:
        self.present()
        path = self._settings.value("project/root", "", str)
        if path:
            self._apply_project_root(path, save=False)

    def open_settings_tray_section(self) -> None:
        self.present()
        dialog = SettingsDialog(
            workspace_root=self._workspace_root,
            project_root=self._project_root,
            settings=self._settings,
            parent=self,
        )
        dialog.workspace_root_selected.connect(lambda p: self._apply_workspace_root(p, save=True))
        dialog.project_root_selected.connect(lambda p: self._apply_project_root(p, save=True))
        dialog.access_session_changed.connect(self._refresh_user_button)
        dialog.hotkeys_changed.connect(self._reload_app_hotkeys)
        dialog.open_to_ui_tab()
        dialog.exec()
        self._refresh_user_button()

    def quit_application(self) -> None:
        """Exit from tray menu — bypass hide-to-tray."""
        self._force_quit = True
        self._worker_manager.shutdown()
        self._flush_discord_inbox_outbox()
        self._persist_window_state()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            self.close()

    def _persist_window_state(self) -> None:
        try:
            self._settings.setValue("ui/sidebar_context", self._nav_rail.current_context())
            self._persist_panel_layout_prefs()
        except Exception:
            pass
        path = self._app_settings_path()
        ms_cur = self._main_splitter.sizes()
        if len(ms_cur) >= 1 and ms_cur[0] == 0:
            main_sizes = self._main_splitter_sizes_restore
        elif not self._sidebar_panel_visible:
            main_sizes = self._main_splitter_sizes_restore
        else:
            main_sizes = ms_cur
        content_sizes = (
            self._content_splitter_sizes_restore
            if not self._inspector.isVisible()
            else self._content_splitter.sizes()
        )
        payload = {
            "window_geometry_b64": base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
            "window_maximized": self.isMaximized(),
            "window_always_on_top": self._window_always_on_top,
            "main_splitter_sizes": main_sizes,
            "content_splitter_sizes": content_sizes,
        }
        try:
            from monostudio.core.user_identity import update_app_settings

            update_app_settings(payload)
        except OSError:
            pass

    def _ensure_identity_for_workspace(self) -> None:
        """Prompt sign-in once when a workspace is chosen but nobody is signed in."""
        from monostudio.core.user_identity import get_current_user

        if self._workspace_root is None:
            return
        if get_current_user(self._workspace_root) is not None:
            return
        self._on_switch_user()

    def _on_switch_user(self) -> None:
        from monostudio.core.access_control import is_admin_capable
        from monostudio.core.user_identity import session_sign_in

        if self._workspace_root is None:
            notification_service.info("Select a workspace first to sign in.")
            return
        dlg = UserIdentityDialog(
            workspace_root=self._workspace_root,
            is_admin=bool(is_admin_capable()),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        user_id = dlg.selected_user_id()
        if not user_id:
            return
        remember = dlg.remember()
        warn = session_sign_in(
            self._workspace_root, user_id,
            remember=remember,
            register_device_too=True,
        )
        if warn:
            notification_service.warning(warn)
        self._refresh_user_button()
        self._refresh_dashboard_if_visible()
        self._reload_mention_alerts_for_current_user()

    def _on_clear_identity(self) -> None:
        from monostudio.core.user_identity import session_sign_out

        if self._workspace_root is None:
            return
        # Logout = clear session only; keep device binding (pre-fills next sign-in).
        session_sign_out(self._workspace_root)
        self._refresh_user_button()
        self._refresh_dashboard_if_visible()
        self._refresh_noti_unread_badge()

    def _on_forget_device(self) -> None:
        from monostudio.core.user_identity import forget_device, session_sign_out

        if self._workspace_root is None:
            return
        forget_device(self._workspace_root)
        session_sign_out(self._workspace_root)
        self._refresh_user_button()
        self._refresh_dashboard_if_visible()
        self._refresh_noti_unread_badge()
        notification_service.info("This device was unlinked from your studio account.")

    def _on_edit_profile(self) -> None:
        from monostudio.core.user_identity import get_current_user

        if self._workspace_root is None:
            notification_service.info("Select a workspace first.")
            return
        user = get_current_user(self._workspace_root)
        if user is None:
            notification_service.info("Sign in to edit your profile.")
            self._on_switch_user()
            return
        from monostudio.ui_qt.user_profile_dialog import UserProfileDialog

        dlg = UserProfileDialog(workspace_root=self._workspace_root, user=user, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._refresh_user_button()
        self._refresh_dashboard_if_visible()
        from monostudio.core.user_identity import avatar_path

        updated = dlg.updated_user()
        if dlg.accent_color_changed() and avatar_path(self._workspace_root, updated) is not None:
            notification_service.info(
                "Accent color saved. Remove profile photo to show it on the top bar."
            )

    def _on_manage_team(self) -> None:
        from monostudio.core.access_control import is_admin_capable

        if self._workspace_root is None:
            return
        if not is_admin_capable():
            notification_service.warning(
                "Admin/developer unlock required for team management. "
                "Open Settings → General → Access, enter your key, then Unlock."
            )
            return
        from monostudio.ui_qt.team_management_dialog import TeamManagementDialog

        dlg = TeamManagementDialog(workspace_root=self._workspace_root, parent=self)
        dlg.exec()
        self._refresh_user_button()
        self._refresh_dashboard_if_visible()

    def _on_dashboard_open_schedule(self) -> None:
        self._pending_schedule_jump = None
        self._pending_unscheduled_entities = None
        self._pending_overdue_entities = None
        self._nav_rail.set_current_context("Schedule", force=True)

    def _on_schedule_back_to_dashboard(self) -> None:
        self._nav_rail.set_current_context("Dashboard", force=True)

    def _on_dashboard_unscheduled_entities(self, entities: object) -> None:
        raw = list(entities) if entities is not None else []
        keys: list[tuple[str, str]] = []
        for item in raw:
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            kind = str(item[0] or "").strip().lower()
            rel = str(item[1] or "").replace("\\", "/").strip()
            if kind and rel:
                keys.append((kind, rel))
        self._pending_schedule_jump = None
        self._pending_overdue_entities = None
        self._pending_unscheduled_entities = keys or None
        self._nav_rail.set_current_context("Schedule", force=True)

    def _on_dashboard_overdue_entities(self, entities: object) -> None:
        self._open_overdue_entities_dialog()

    def _open_overdue_entities_dialog(self) -> None:
        from monostudio.ui_qt.overdue_entities_dialog import OverdueEntitiesDialog

        rows = ()
        if self._dashboard_page_widget is not None:
            rows = self._dashboard_page_widget.overdue_entity_rows()
        dlg = getattr(self, "_overdue_entities_dialog", None)
        if dlg is None:
            dlg = OverdueEntitiesDialog(parent=self)
            dlg.open_in_main_view.connect(self._on_overdue_dialog_open_main_view)
            dlg.open_in_schedule.connect(self._on_overdue_dialog_open_schedule)
            self._overdue_entities_dialog = dlg
        dlg.set_rows(rows)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _open_skipped_schedule_dialog(self) -> None:
        from monostudio.core.schedule_skip import SkippedScheduleSnapshot
        from monostudio.ui_qt.skipped_schedule_dialog import SkippedScheduleDialog

        snap = SkippedScheduleSnapshot(0, 0, ())
        if self._schedule_page_widget is not None:
            snap = self._schedule_page_widget.skipped_snapshot()
        dlg = getattr(self, "_skipped_schedule_dialog", None)
        if dlg is None:
            dlg = SkippedScheduleDialog(parent=self)
            dlg.open_in_main_view.connect(self._on_overdue_dialog_open_main_view)
            dlg.open_in_schedule.connect(self._on_skipped_dialog_open_schedule)
            self._skipped_schedule_dialog = dlg
        dlg.set_snapshot(snap)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_skipped_dialog_open_schedule(
        self,
        entity_kind: str,
        entity_rel: str,
        department: str,
    ) -> None:
        kind = (entity_kind or "").strip().lower()
        rel = (entity_rel or "").replace("\\", "/").strip()
        if not kind or not rel:
            return
        dep = (department or "").strip() or None
        if self._nav_rail.current_context() != "Schedule":
            self._pending_schedule_jump = (kind, rel, dep or "", "")
            self._pending_overdue_entities = None
            self._pending_unscheduled_entities = None
            self._nav_rail.set_current_context("Schedule", force=True)
            return
        if dep:
            self._controller.sync_filter_state(department=dep, type_id=self.current_type)
            self._filter_panel.filters().set_selected_department(dep, emit=False)
            self._apply_schedule_sidebar_filters()
        if self._schedule_page_widget is not None:
            self._schedule_page_widget.clear_transient_view_filters()
            self._schedule_page_widget.reveal_entity(kind, rel, department=dep)
        item = self._view_item_for_schedule_entity(kind, rel)
        if item is not None:
            self._schedule_inspector_item = item
            self._refresh_inspector_selection()

    def _on_overdue_dialog_open_main_view(
        self,
        entity_kind: str,
        entity_rel: str,
        department: str,
        entity_name: str,
    ) -> None:
        self._navigate_dashboard_to_entity(
            entity_kind=entity_kind,
            entity_name=entity_name,
            department=department,
            entity_rel=entity_rel,
        )

    def _on_overdue_dialog_open_schedule(
        self,
        entity_kind: str,
        entity_rel: str,
        department: str,
    ) -> None:
        kind = (entity_kind or "").strip().lower()
        rel = (entity_rel or "").replace("\\", "/").strip()
        if not kind or not rel:
            return
        dep = (department or "").strip() or None
        self._pending_schedule_jump = None
        self._pending_unscheduled_entities = None
        self._pending_overdue_entities = [(kind, rel)]
        if dep:
            self._filter_panel.filters().set_selected_department(dep, emit=False)
        self._nav_rail.set_current_context("Schedule", force=True)

    def _on_dashboard_schedule_jump(
        self,
        entity_kind: object,
        entity_rel: object,
        department: object,
        due_iso: object,
    ) -> None:
        self._pending_unscheduled_entities = None
        self._pending_overdue_entities = None
        self._pending_schedule_jump = (
            str(entity_kind or ""),
            str(entity_rel or ""),
            str(department or ""),
            str(due_iso or ""),
        )
        self._nav_rail.set_current_context("Schedule", force=True)

    def _consume_pending_schedule_jump(self) -> None:
        if self._schedule_page_widget is None:
            return
        if self._nav_rail.current_context() != "Schedule":
            return

        unscheduled = self._pending_unscheduled_entities
        if unscheduled:
            self._pending_unscheduled_entities = None
            self._pending_schedule_jump = None
            self._ensure_schedule_scope_for_entity_keys(unscheduled)
            self._apply_schedule_sidebar_filters()
            self._schedule_page_widget.focus_unscheduled_entities(unscheduled)
            kind, rel = unscheduled[0]
            item = self._view_item_for_schedule_entity(kind, rel)
            self._schedule_inspector_item = item
            self._refresh_inspector_selection()
            return

        overdue = self._pending_overdue_entities
        if overdue is not None:
            self._pending_overdue_entities = None
            self._pending_schedule_jump = None
            self._ensure_schedule_scope_for_entity_keys(overdue)
            self._apply_schedule_sidebar_filters()
            self._schedule_page_widget.focus_overdue_entities(overdue)
            if overdue:
                kind, rel = overdue[0]
                item = self._view_item_for_schedule_entity(kind, rel)
                self._schedule_inspector_item = item
                self._refresh_inspector_selection()
            return

        self._schedule_page_widget.clear_transient_view_filters()

        pending = self._pending_schedule_jump
        if pending is None:
            return
        self._pending_schedule_jump = None
        kind, rel, dept, due_s = pending
        dep = (dept or "").strip() or None
        if dep and not (kind or "").strip():
            self._on_schedule_sidebar_department_sync(dep)
            return
        kind_n = (kind or "").strip().lower()
        rel_n = (rel or "").replace("\\", "/").strip()
        if not kind_n or not rel_n:
            return
        due: date | None = None
        if due_s:
            try:
                due = date.fromisoformat(str(due_s)[:10])
            except ValueError:
                due = None
        self._ensure_schedule_scope_for_entity_keys([(kind_n, rel_n)])
        if dep:
            self._filter_panel.filters().set_selected_department(dep, emit=False)
        self._apply_schedule_sidebar_filters()
        self._schedule_page_widget.reveal_entity(
            kind_n, rel_n, department=dep, due=due
        )

    def _on_dashboard_open_scope(self, scope: object) -> None:
        name = str(scope or "").strip()
        if name in ("Assets", "Shots"):
            self._nav_rail.set_current_context(name)

    def _current_author_id(self) -> str | None:
        from monostudio.core.user_identity import get_current_user

        user = get_current_user(self._workspace_root)
        return user.id if user is not None else None

    def _refresh_noti_unread_badge(self) -> None:
        from monostudio.core.user_identity import get_current_user
        from monostudio.ui_qt.notification.store import unread_count

        user = get_current_user(self._workspace_root)
        uid = user.id if user is not None else ""
        count = unread_count(user_id=uid, project_root=self._project_root)
        self._top_bar.set_noti_unread_count(count)
        self._on_tray_notification_count(count)
        self._refresh_dashboard_mention_unread_dot()

    def _refresh_dashboard_mention_unread_dot(self) -> None:
        from monostudio.core.project_dashboard_stats import count_unread_mentions
        from monostudio.core.user_identity import get_current_user

        user = get_current_user(self._workspace_root)
        unread = 0
        if user is not None and self._project_root is not None:
            unread = count_unread_mentions(self._project_root, user.id)
        self._nav_rail.set_dashboard_unread(unread > 0)
        if self._dashboard_page_widget is not None:
            self._dashboard_page_widget.set_mentions_unread_dot(unread > 0)

    def _on_tray_notification_count(self, count: int) -> None:
        if self._tray_manager is not None:
            self._tray_manager.set_notification_pending(count > 0)
            self._tray_manager.refresh_menu()

    def _sync_tray_status_badges(self) -> None:
        if self._tray_manager is None:
            return
        from monostudio.core.update_checker import get_cached_check_result
        from monostudio.core.user_identity import get_current_user
        from monostudio.ui_qt.notification.store import unread_count

        user = get_current_user(self._workspace_root)
        uid = user.id if user is not None else ""
        self._tray_manager.set_notification_pending(
            unread_count(user_id=uid, project_root=self._project_root) > 0
        )
        cached = get_cached_check_result()
        has_update = bool(cached is not None and getattr(cached, "update_available", False))
        self._tray_manager.set_update_available(has_update)

    def _reload_mention_alerts_for_current_user(self) -> None:
        from monostudio.core.user_identity import get_current_user
        from monostudio.ui_qt.notification.store import prune_mention_alerts_not_for_user

        user = get_current_user(self._workspace_root)
        if user is None:
            from monostudio.ui_qt.notification.store import clear_mention_user_alerts

            clear_mention_user_alerts()
            notification_service.reset_mention_popup_session()
            self._refresh_noti_unread_badge()
            return
        prune_mention_alerts_not_for_user(user.id, self._project_root)
        notification_service.reset_mention_popup_session()
        self._sync_user_inbox_alerts()

    def _sync_user_inbox_alerts(self) -> None:
        self._sync_mention_inbox_alerts()
        self._sync_assign_inbox_alerts()

    def _sync_assign_inbox_alerts(self) -> None:
        from monostudio.core.assign_inbox import AssignInboxItem, items_for_user, resolve_assign_entity_path
        from monostudio.core.user_identity import get_current_user
        from monostudio.ui_qt.notification.assign_alert_format import (
            assign_alert_bulk_plain_message,
            assign_alert_plain_message,
        )
        from monostudio.ui_qt.notification.store import (
            UserAlertPayload,
            has_assign_batch_id,
            has_assign_inbox_id,
            prune_mention_alerts_not_for_user,
        )

        if self._project_root is None:
            return
        user = get_current_user(self._workspace_root)
        if user is None:
            return
        prune_mention_alerts_not_for_user(user.id, self._project_root)

        all_items = list(items_for_user(self._project_root, user.id))
        pending = [i for i in all_items if not has_assign_inbox_id(i.id)]
        batch_groups: dict[str, list[AssignInboxItem]] = {}
        singles: list[AssignInboxItem] = []
        for item in pending:
            bid = (item.batch_id or "").strip()
            if bid:
                batch_groups.setdefault(bid, []).append(item)
            else:
                singles.append(item)

        for bid, group in batch_groups.items():
            if has_assign_batch_id(bid):
                continue
            if len(group) == 1:
                singles.append(group[0])
                continue
            first = group[0]
            from_name = (first.from_name or "").strip() or "Someone"
            inbox_ids = tuple(i.id for i in group if (i.id or "").strip())
            entity = resolve_assign_entity_path(self._project_root, item_rel=first.item_rel)
            item_path_str = str(entity) if entity is not None else ""
            notification_service.user_alert(
                assign_alert_bulk_plain_message(from_name=from_name, count=len(group)),
                payload=UserAlertPayload(
                    item_path=item_path_str,
                    item_rel=first.item_rel,
                    item_display=str(len(group)),
                    assign_inbox_id=first.id,
                    assign_inbox_ids=inbox_ids,
                    assign_batch_id=bid,
                    department=first.department,
                    from_name=from_name,
                    from_user_id=first.from_user_id,
                    department_label=self._department_label_for_id(first.department),
                    to_user_id=user.id,
                ),
                toast_type="info",
                read=all(i.read for i in group),
                show_popup=False,
            )

        for item in singles:
            entity = resolve_assign_entity_path(self._project_root, item_rel=item.item_rel)
            item_path_str = str(entity) if entity is not None else ""
            from_name = (item.from_name or "").strip() or "Someone"
            item_display = item.item_display or item.item_rel or "an item"
            dept_label = self._department_label_for_id(item.department)
            notification_service.user_alert(
                assign_alert_plain_message(
                    from_name=from_name,
                    item_display=item_display,
                    department_id=item.department,
                    department_label=dept_label,
                ),
                payload=UserAlertPayload(
                    item_path=item_path_str,
                    item_rel=item.item_rel,
                    item_display=item_display,
                    assign_inbox_id=item.id,
                    department=item.department,
                    from_name=from_name,
                    from_user_id=item.from_user_id,
                    department_label=dept_label,
                    to_user_id=user.id,
                ),
                toast_type="info",
                read=item.read,
                show_popup=False,
            )

        popup_batch: list[tuple[str, str]] = []
        popup_keys: set[str] = set()
        for item in all_items:
            if item.read:
                continue
            from_name = (item.from_name or "").strip() or "Someone"
            bid = (item.batch_id or "").strip()
            popup_key = f"batch:{bid}" if bid else item.id
            if popup_key in popup_keys:
                continue
            popup_keys.add(popup_key)
            popup_batch.append((item.id, from_name))
        if popup_batch:
            notification_service.deliver_assign_popup_batch(popup_batch)
        self._refresh_noti_unread_badge()

    def _sync_mention_inbox_alerts(self) -> None:
        from monostudio.core.mention_inbox import items_for_user, resolve_mention_entity_path
        from monostudio.core.user_identity import get_current_user
        from monostudio.ui_qt.notification.mention_alert_format import mention_alert_plain_message
        from monostudio.ui_qt.notification.store import (
            UserAlertPayload,
            has_mention_inbox_id,
            prune_mention_alerts_not_for_user,
        )

        if self._project_root is None:
            return
        user = get_current_user(self._workspace_root)
        if user is None:
            return
        prune_mention_alerts_not_for_user(user.id, self._project_root)
        popup_batch: list[tuple[str, str]] = []
        for item in items_for_user(self._project_root, user.id):
            entity = resolve_mention_entity_path(
                self._project_root,
                item_rel=item.item_rel,
            )
            item_path_str = str(entity) if entity is not None else ""
            from_name = (item.from_name or "").strip() or "Someone"
            item_display = item.item_display or item.item_rel or "an item"
            dept_label = self._department_label_for_id(item.department)
            msg = mention_alert_plain_message(
                from_name=from_name,
                item_display=item_display,
                department_id=item.department,
                department_label=dept_label,
            )
            in_bell = has_mention_inbox_id(item.id)
            if in_bell:
                if not item.read:
                    popup_batch.append((item.id, from_name))
                continue
            notification_service.user_alert(
                msg,
                payload=UserAlertPayload(
                    item_path=item_path_str,
                    item_rel=item.item_rel,
                    item_display=item_display,
                    note_id=item.note_id,
                    mention_inbox_id=item.id,
                    department=item.department,
                    from_name=from_name,
                    from_user_id=item.from_user_id,
                    department_label=dept_label,
                    to_user_id=user.id,
                ),
                toast_type="info",
                read=item.read,
                show_popup=False,
            )
            if not item.read:
                popup_batch.append((item.id, from_name))
        if popup_batch:
            notification_service.deliver_mention_popup_batch(popup_batch)
        self._refresh_noti_unread_badge()

    def _on_user_alert_clicked(self, entry: object) -> None:
        from monostudio.core.assign_inbox import mark_read as assign_inbox_mark_read, resolve_assign_entity_path
        from monostudio.core.item_comments import department_for_note_id
        from monostudio.core.mention_inbox import mark_read as inbox_mark_read, resolve_mention_entity_path
        from monostudio.ui_qt.notification.store import NotificationEntry, mark_read as store_mark_read

        if not isinstance(entry, NotificationEntry):
            return
        payload = entry.payload
        raw_assign_ids = list(payload.assign_inbox_ids or ())
        if (payload.assign_inbox_id or "").strip():
            raw_assign_ids.append(payload.assign_inbox_id)
        assign_inbox_ids: list[str] = []
        seen_inbox: set[str] = set()
        for aid in raw_assign_ids:
            iid = (aid or "").strip()
            if not iid or iid in seen_inbox:
                continue
            seen_inbox.add(iid)
            assign_inbox_ids.append(iid)
        if assign_inbox_ids and self._project_root is not None:
            from monostudio.core.assign_inbox import get_inbox_item

            inbox_item = get_inbox_item(self._project_root, assign_inbox_ids[0])
            entity = resolve_assign_entity_path(
                self._project_root,
                item_rel=payload.item_rel,
            )
            if entity is None and (payload.item_path or "").strip():
                try:
                    p = Path(payload.item_path)
                    if p.is_dir():
                        entity = p
                except OSError:
                    pass
            if entity is None:
                notification_service.warning("Could not find that asset or shot in this project.")
                return
            dept_id = (payload.department or "").strip()
            self._navigate_to_entity_for_notes(entity, dept_id)
            self._inspector.set_inspector_tab_index(2)
            if inbox_item is not None and not inbox_item.confirmed:
                self._prompt_assign_confirm(inbox_item, payload)
            else:
                for aid in assign_inbox_ids:
                    try:
                        assign_inbox_mark_read(self._project_root, aid)
                    except OSError:
                        pass
                    store_mark_read(aid)
                self._refresh_noti_unread_badge()
            return
        if payload.mention_inbox_id and self._project_root is not None:
            try:
                inbox_mark_read(self._project_root, payload.mention_inbox_id)
            except OSError:
                pass
            store_mark_read(payload.mention_inbox_id)
            self._refresh_noti_unread_badge()
        if self._project_root is None:
            return
        if not payload.mention_inbox_id:
            return
        entity = resolve_mention_entity_path(
            self._project_root,
            item_rel=payload.item_rel,
            item_path=payload.item_path,
        )
        if entity is None:
            notification_service.warning("Could not find that asset or shot in this project.")
            return
        dept_id = (payload.department or "").strip()
        if not dept_id and payload.note_id:
            dept_id = department_for_note_id(entity, payload.note_id)
        self._navigate_to_entity_for_notes(entity, dept_id)
        dept_label = self._department_label_for_id(dept_id)
        self._open_item_notes_dialog(
            entity,
            display_name=payload.item_display or entity.name,
            highlight_note_id=payload.note_id or None,
            department_id=dept_id or None,
            department_label=dept_label,
        )

    def _prompt_assign_confirm(self, inbox_item: object, payload: object) -> None:
        from monostudio.core.discord_webhook import format_schedule_dates
        from monostudio.core.notification_copy import pick_copy
        from monostudio.core.schedule_assign_notify import confirm_schedule_assignment
        from monostudio.core.user_identity import get_current_user_display_name
        from monostudio.ui_qt.assign_confirm_dialog import AssignConfirmDialog
        from monostudio.ui_qt.notification.store import UserAlertPayload, mark_read as store_mark_read

        if self._project_root is None:
            return
        p = payload if isinstance(payload, UserAlertPayload) else UserAlertPayload()
        from_name = (p.from_name or getattr(inbox_item, "from_name", "") or "").strip()
        item_display = (p.item_display or getattr(inbox_item, "item_display", "") or "").strip()
        dept_label = (p.department_label or "").strip() or self._department_label_for_id(
            (p.department or getattr(inbox_item, "department", "") or "").strip()
        )
        schedule_label = format_schedule_dates(
            getattr(inbox_item, "start", "") or "",
            getattr(inbox_item, "due", "") or "",
        )
        if not AssignConfirmDialog.ask(
            self,
            from_name=from_name,
            item_display=item_display,
            department_label=dept_label,
            schedule_label=schedule_label,
        ):
            return
        iid = (p.assign_inbox_id or getattr(inbox_item, "id", "") or "").strip()
        if not iid:
            return
        if confirm_schedule_assignment(
            self._project_root,
            self._workspace_root,
            iid,
            confirmed_by_name=get_current_user_display_name(self._workspace_root),
        ):
            store_mark_read(iid)
            self._refresh_noti_unread_badge()
            self._refresh_inspector_selection()
            notification_service.success(
                pick_copy("Đã xác nhận giao việc.", "Assignment confirmed."),
            )

    def _navigate_to_entity_for_notes(self, entity_path: Path, department_id: str) -> None:
        """Select asset/shot in main view and align sidebar department before opening Notes."""
        try:
            rel = entity_path.relative_to(self._project_root).as_posix()
        except ValueError:
            rel = ""
        ctx = "Shots" if rel.startswith("shots/") else "Assets"
        kind = "shot" if ctx == "Shots" else "asset"
        ref = self._pipeline_ref_for_path(entity_path, kind)
        typ = None
        if isinstance(ref, Asset):
            typ = (ref.asset_type or "").strip() or None
        dept = (department_id or "").strip() or None
        if not self._open_pipeline_entity_in_main_view(
            context=ctx,
            path=entity_path,
            type_id=typ,
            department=dept,
        ):
            notification_service.warning(
                f"Could not find {(entity_path.name)} in the current view."
            )

    def _notes_department_for_dialog(self) -> tuple[str, str]:
        """(department_id, display_label) from sidebar / main view focus."""
        dept_id = (
            self._filter_panel.filters().current_department()
            or self.current_department
            or ""
        )
        dept_id = (dept_id or "").strip()
        if not dept_id:
            return ("", "General")
        label = dept_id
        if self._project_root is not None:
            try:
                from monostudio.core.department_registry import DepartmentRegistry

                label = DepartmentRegistry.for_project(self._project_root).get_department_label(
                    dept_id
                )
            except OSError:
                pass
        return (dept_id, label or dept_id)

    def _open_item_notes_dialog(
        self,
        item_path: Path,
        *,
        display_name: str | None = None,
        highlight_note_id: str | None = None,
        department_id: str | None = None,
        department_label: str | None = None,
    ) -> None:
        from monostudio.ui_qt.item_notes_dialog import ItemNotesDialog

        p = Path(item_path)
        if department_id is not None or department_label is not None:
            dept_id = (department_id or "").strip()
            dept_label = (department_label or dept_id or "General").strip()
        else:
            dept_id, dept_label = self._notes_department_for_dialog()
        dlg = ItemNotesDialog(
            parent=self,
            item_path=p,
            item_display_name=(display_name or p.name).strip(),
            author=self._current_author_name(),
            author_id=self._current_author_id(),
            workspace_root=self._workspace_root,
            project_root=self._project_root,
            department_id=dept_id or None,
            department_label=dept_label,
            highlight_note_id=highlight_note_id,
        )
        dlg.notes_changed.connect(lambda: self._on_item_notes_saved(p))
        dlg.notes_changed.connect(lambda: self._ensure_entity_monostudio_watched(p))
        dlg.notes_changed.connect(self._refresh_dashboard_if_visible)
        dlg.notes_changed.connect(self._sync_user_inbox_alerts)
        dlg.exec()

    def _on_item_notes_saved(self, item_path: Path) -> None:
        """Refresh main view note badges / metadata after Notes dialog save."""
        self._main_view.invalidate_notes_open_count_cache(item_path)
        self._inspector.refresh_notes_badge()

    def _on_review_player_notes_changed(self) -> None:
        dlg = self._alive_review_player()
        entity_path = getattr(dlg, "_entity_path", None) if dlg is not None else None
        if entity_path is not None:
            try:
                self._main_view.invalidate_notes_open_count_cache(Path(entity_path))
            except (TypeError, ValueError, OSError):
                pass
        self._inspector.refresh_notes_badge()
        self._refresh_dashboard_if_visible()
        self._sync_user_inbox_alerts()

    def _on_dashboard_open_notes_entity(self, target: object) -> None:
        from monostudio.core.project_dashboard_stats import DashboardNoteRow

        if isinstance(target, DashboardNoteRow):
            note = target
            try:
                p = Path(note.entity_path)
            except (TypeError, ValueError):
                return
            if not self._note_entity_path_valid(p):
                return
            if note.comment_id:
                self._mark_mention_inbox_read_for_note(note.comment_id)
            dept_label = self._department_label_for_id(note.department)
            self._open_item_notes_dialog(
                p,
                display_name=note.entity_name,
                highlight_note_id=note.comment_id or None,
                department_id=note.department or None,
                department_label=dept_label,
            )
            return
        try:
            p = Path(target)
        except (TypeError, ValueError):
            return
        if not self._note_entity_path_valid(p):
            return
        self._open_item_notes_dialog(p, display_name=p.name)

    @staticmethod
    def _note_entity_path_valid(p: Path) -> bool:
        try:
            return p.is_dir()
        except OSError:
            return False

    def _mark_mention_inbox_read_for_note(self, note_id: str) -> None:
        """Mark matching unread @mention inbox rows read when the note is opened."""
        nid = (note_id or "").strip()
        if not nid or self._project_root is None:
            return
        from monostudio.core.mention_inbox import items_for_user, mark_read
        from monostudio.core.user_identity import get_current_user
        from monostudio.ui_qt.notification.store import mark_read as store_mark_read

        user = get_current_user(self._workspace_root)
        if user is None:
            return
        changed = False
        for item in items_for_user(self._project_root, user.id):
            if item.note_id != nid or item.read:
                continue
            try:
                mark_read(self._project_root, item.id)
            except OSError:
                continue
            store_mark_read(item.id)
            changed = True
        if changed:
            self._refresh_noti_unread_badge()
            self._refresh_dashboard_if_visible()

    def _department_label_for_id(self, dept_id: str) -> str:
        did = (dept_id or "").strip()
        if not did:
            return "General"
        if self._project_root is not None:
            try:
                from monostudio.core.department_registry import DepartmentRegistry

                return DepartmentRegistry.for_project(self._project_root).get_department_label(did)
            except OSError:
                pass
        return did.replace("_", " ").title()

    def _navigate_dashboard_to_entity(
        self,
        *,
        entity_kind: str,
        entity_name: str,
        department: str = "",
        entity_path: Path | None = None,
        entity_rel: str = "",
    ) -> None:
        p: Path | None = entity_path
        if p is None and self._project_root is not None:
            rel = (entity_rel or "").replace("\\", "/").strip()
            if rel:
                try:
                    candidate = (self._project_root / rel).resolve()
                except (OSError, ValueError):
                    candidate = None
                if candidate is not None and self._note_entity_path_valid(candidate):
                    p = candidate
        if p is None:
            item = self._view_item_for_schedule_entity(entity_kind, entity_rel)
            if item is not None:
                try:
                    p = Path(item.path)
                except (TypeError, ValueError):
                    p = None
        if p is None or not self._note_entity_path_valid(p):
            name = (entity_name or "").strip() or "entity"
            notification_service.warning(f"Could not find {name} in the current view.")
            return
        dept = (department or "").strip() or None
        ctx = "Shots" if (entity_kind or "").strip().lower() == "shot" else "Assets"
        kind = "shot" if ctx == "Shots" else "asset"
        ref = self._pipeline_ref_for_path(p, kind)
        typ = None
        if isinstance(ref, Asset):
            typ = (ref.asset_type or "").strip() or None
        display = (entity_name or "").strip() or p.name
        if not self._open_pipeline_entity_in_main_view(
            context=ctx,
            path=p,
            type_id=typ,
            department=dept,
        ):
            notification_service.warning(f"Could not find {display} in the current view.")
            return
        label = self._department_label_for_id(dept or "")
        if dept:
            notification_service.info(f"{display} · {label}")
        else:
            notification_service.info(f"{display} — opened in {ctx}.")

    def _on_dashboard_entity_nav(
        self,
        entity_kind: object,
        entity_rel: object,
        department: object,
        entity_name: object,
    ) -> None:
        self._navigate_dashboard_to_entity(
            entity_kind=str(entity_kind or ""),
            entity_name=str(entity_name or ""),
            department=str(department or ""),
            entity_rel=str(entity_rel or ""),
        )

    def _on_dashboard_note_go_to_department(self, target: object) -> None:
        from monostudio.core.project_dashboard_stats import DashboardNoteRow

        if not isinstance(target, DashboardNoteRow):
            return
        try:
            p = Path(target.entity_path)
        except (TypeError, ValueError):
            return
        self._navigate_dashboard_to_entity(
            entity_kind=target.entity_kind,
            entity_name=target.entity_name,
            department=target.department,
            entity_path=p,
        )

    def _on_item_notes_dialog_requested(self, item: object) -> None:
        if not isinstance(item, ViewItem):
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        try:
            p = Path(item.path)
        except (TypeError, ValueError):
            return
        try:
            if not p.is_dir():
                return
        except OSError:
            return
        self._open_item_notes_dialog(p, display_name=display_name_for_item(item))

    def _on_inspector_ref_tab_requested(self, item: object) -> None:
        if not isinstance(item, ViewItem):
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        if self._manual_inspector_visible and not self._inspector.isVisible():
            self._inspector.setVisible(True)
        dep = self._filter_panel.filters().current_department() or self.current_department
        self._inspector.set_item(item, active_department_hint=dep)
        self._inspector.set_inspector_tab_index(1)

    def _on_rename_asset_requested(self, item: ViewItem) -> None:
        """
        Rename (asset only):
        - Requires file watcher to be paused (toggle in top bar)
        - Dialog validates target name
        - Renames asset folder on disk + renames work files to match pipeline prefix
        - On success: refresh in-memory index and app_state (same style as delete), keep selection on renamed asset
        """
        if self._project_index is None or self._project_root is None:
            return
        if item.kind.value != "asset":
            return
        if not self._watcher_manually_disabled:
            notification_service.warning(
                "Pause the file watcher (click the eye icon in the top bar) before renaming."
            )
            return

        old_path = Path(item.path)
        try:
            if not old_path.exists():
                return
        except OSError:
            return

        dlg = RenameAssetDialog(project_root=self._project_root, asset_path=old_path, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        final_name = dlg.final_name()
        project_root = self._project_root
        try:
            expected_root = project_root.resolve()
        except OSError:
            expected_root = project_root

        try:
            work_file_renames = prepare_work_file_renames(
                project_root=project_root,
                asset_path=old_path,
                new_name=final_name,
            )
        except (OSError, ValueError, FileNotFoundError) as e:
            logging.warning("Prepare rename failed: %s", e)
            notification_service.error(f"Rename failed: {e}")
            return

        self._show_page_loading(self._RENAME_LOADING_MESSAGE)

        def run() -> _AssetRenameWorkerResult:
            return run_asset_rename_worker(
                project_root=expected_root,
                asset_path=old_path,
                new_name=final_name,
                work_file_renames=work_file_renames,
            )

        task = WorkerTask(self._ASSET_RENAME_WORKER, run, manager=self._worker_manager)
        self._worker_manager.submit_task(
            task,
            category=self._ASSET_RENAME_WORKER,
            replace_existing=True,
        )

    def _finish_asset_rename(self, result: _AssetRenameWorkerResult) -> None:
        old_path = result.old_path
        new_path = result.new_path

        cur = self._main_view.selected_view_item()
        if cur is not None and getattr(cur, "path", None) in (old_path, new_path):
            self._inspector.set_item(None)

        self._app_state.invalidate_thumbnails([str(old_path), str(new_path)])

        self._project_index = result.index
        self._app_state.update_assets(list(result.index.assets))
        self._app_state.update_shots(list(result.index.shots))
        self._app_state.commit_immediate()
        self._filter_panel.set_project_index(self._project_index)
        self._reload_main_view()
        self._sync_primary_action()
        self._update_fs_watcher_paths()

        try:
            if new_path.exists():
                self._app_state.set_selection(str(new_path))
        except Exception:
            pass

        notification_service.success(f"Renamed Asset '{old_path.name}' → '{new_path.name}'.")

    def _notify_asset_rename_failed(self, error: str) -> None:
        err = (error or "").strip()
        if not err:
            notification_service.error("Rename failed.")
            return
        lower = err.lower()
        if "winerror 5" in lower or "access is denied" in lower or "permission denied" in lower:
            notification_service.error(
                "Rename failed: Access denied. The folder may be in use by Dropbox or another app. "
                "Try pausing sync for this folder, or rename it in Explorer and refresh the project."
            )
            return
        notification_service.error(f"Rename failed: {err}")

    def _restore_workspace_root(self) -> None:
        path = self._settings.value("workspace/root", "", str)
        self._apply_workspace_root(path or None, save=False)

    def _restore_project_root(self) -> None:
        path = self._settings.value("project/root", "", str)
        self._apply_project_root(path or None, save=False)

    def _restore_sidebar_context(
        self,
        *,
        force: bool = False,
        nav_rail: bool = True,
        filter_panel: bool = True,
    ) -> None:
        """Restore last selected nav page from QSettings."""
        _valid = (
            "Assets", "Shots", "Inbox", "Project Guide", "Dashboard", "Schedule",
            SidebarContext.INTERNAL_CHECK.value, "Delivery", "Trash",
        )
        ctx = (self._settings.value("ui/sidebar_context", "Assets", str) or "Assets").strip()
        if ctx == "Projects":
            ctx = "Dashboard"
        if ctx == "Outbox":
            ctx = "Delivery"
        if ctx == "Review":
            ctx = SidebarContext.INTERNAL_CHECK.value
        if ctx not in _valid:
            return
        if filter_panel:
            self._filter_panel.sync_nav_context(ctx, force=force)
        if nav_rail:
            self._nav_rail.set_current_context(ctx, force=force)

    def _on_context_switched(self, context_name: str) -> None:
        # Trigger: user switches between top-level contexts.
        self._filter_panel.sync_nav_context(context_name)
        prev_context = self._active_nav_context
        if prev_context == "Dashboard" and context_name != "Dashboard":
            if self._dashboard_page_widget is not None:
                self._dashboard_page_widget.exit_customize_mode()
            self._filter_panel.set_dashboard_customize_mode(False)
            # Dashboard browse uses rail-only; restore filter panel for other pages.
            self._apply_panel_layout()
        if (
            prev_context in ("Assets", "Shots")
            and context_name not in ("Assets", "Shots")
            and (self.current_search_query or "").strip()
        ):
            self._clear_main_view_search()
        if (
            prev_context == "Schedule"
            and context_name != "Schedule"
            and self._schedule_page_widget is not None
        ):
            self._schedule_page_widget.clear_transient_view_filters()
        self._context_switch_in_progress = True
        try:
            # Close any stray popup menus to avoid accidental triggers during switch.
            try:
                p = QApplication.activePopupWidget()
                if p is not None:
                    p.close()
            except Exception:
                pass

            self._main_view.set_context_title(context_name)
            self._entered_parent = None
            if context_name not in (
                "Inbox", "Project Guide", "Schedule", SidebarContext.INTERNAL_CHECK.value, "Delivery", "Trash", "Dashboard",
            ):
                self._content_stack.setCurrentWidget(self._main_view)
                self._main_view.clear()
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
            elif context_name == "Project Guide":
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
            # Clear selection first; selection churn during model resets is a common source of re-entrant UI.
            try:
                self._main_view.clear_selection()
            except Exception:
                pass
            self._inspector.set_item(None)

            if context_name in ("Assets", "Shots"):
                self._schedule_inspector_item = None
                self._set_inspector_empty_hint_for_context(context_name)
                # Sync filter state first so _reload_main_view uses correct (page, dept, type).
                self._pull_browser_filters_from_sidebar()
                if self._nav_rail.current_context() not in ("Assets", "Shots"):
                    self._sync_filter_state_from_sidebar()
                # Clear so diff application does not mix with previous context data.
                self._main_view.clear()
                # Scan in background to avoid blocking UI when switching between Assets/Shots.
                self._submit_rescan_task(soft=True)
                self._reload_main_view()
                if context_name == "Shots":
                    self._schedule_shot_review_render_enrich()
            elif context_name == "Inbox":
                self._sync_filter_state_from_sidebar()
                self._inbox_switch_cooldown = True
                QTimer.singleShot(120, lambda: setattr(self, "_inbox_switch_cooldown", False))
                if self._inbox_page_widget is None:
                    self._inbox_page_widget = InboxPageWidget(self)
                    self._inbox_page_widget.tree_distribute_paths_changed.connect(self._on_inbox_tree_distribute_paths_changed)
                    self._inbox_page_widget.open_folder_requested.connect(self._on_inbox_open_folder_requested)
                    self._inbox_page_widget.drop_requested.connect(self._on_inbox_drop_requested)
                    self._inbox_page_widget.import_requested.connect(self._on_inbox_import_requested)
                    self._inbox_page_widget.date_folder_entered.connect(self._on_inbox_date_folder_entered)
                    self._inbox_page_widget.video_preview_requested.connect(
                        self._open_video_preview_from_inbox
                    )
                    self._connect_inbox_outbox_title_row(self._inbox_page_widget._title_row)
                    self._content_stack.addWidget(self._inbox_page_widget)
                self._inbox_page_widget.set_project_root(self._project_root)
                self._inbox_page_widget.set_type_filter(self._filter_panel.filters().current_type() or "")
                self._content_stack.setCurrentWidget(self._inbox_page_widget)
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
                self._restore_inbox_date_folder_state()
            elif context_name == "Project Guide":
                self._sync_filter_state_from_sidebar()
                if self._reference_page_widget is None:
                    self._reference_page_widget = ReferencePageWidget(self)
                    self._reference_page_widget.tree_selection_changed.connect(self._on_reference_tree_selection_changed)
                    self._reference_page_widget.drop_requested.connect(self._on_reference_drop_requested)
                    self._reference_page_widget.import_requested.connect(self._on_reference_import_requested)
                    self._reference_page_widget.open_folder_requested.connect(self._on_reference_open_folder_requested)
                    self._reference_page_widget.item_tags_changed.connect(self._on_reference_item_tags_changed)
                    self._reference_page_widget.tag_filter_badge_clicked.connect(
                        self._on_reference_tag_badge_clear
                    )
                    self._reference_page_widget.video_preview_requested.connect(
                        self._open_video_preview_from_project_guide
                    )
                    self._reference_page_widget.browse_path_changed.connect(
                        self._on_project_guide_browse_path_changed
                    )
                    self._connect_inbox_outbox_title_row(self._reference_page_widget._title_row)
                    self._content_stack.addWidget(self._reference_page_widget)
                self._reference_page_widget.set_project_root(self._project_root)
                dept = self._filter_panel.filters().current_department() or "reference"
                dep_label, dep_icon = self._filter_panel.filters().get_department_display(dept)
                self._reference_page_widget.set_header_badge_display(label=dep_label, icon_name=dep_icon)
                self._reference_page_widget.set_department(dept)
                self._filter_panel.filters().sync_tags_for_department(dept, emit_filter=False)
                self._filter_panel.filters().set_tag_item_tags(self._reference_page_widget.get_item_tags())
                self._reference_page_widget.set_tag_filter(self._filter_panel.filters().current_tags())
                self._update_reference_tag_badge()
                self._content_stack.setCurrentWidget(self._reference_page_widget)
                self._restore_project_guide_browse_state()
                self._inspector.set_inbox_tree_preview(None)
            elif context_name == SidebarContext.INTERNAL_CHECK.value:
                self._sync_filter_state_from_sidebar()
                self._inbox_switch_cooldown = True
                QTimer.singleShot(120, lambda: setattr(self, "_inbox_switch_cooldown", False))
                if self._internal_check_page_widget is None:
                    self._internal_check_page_widget = InternalCheckPageWidget(self)
                    self._internal_check_page_widget.tree_distribute_paths_changed.connect(
                        self._on_internal_check_tree_distribute_paths_changed
                    )
                    self._internal_check_page_widget.open_folder_requested.connect(self._on_internal_check_open_folder_requested)
                    self._internal_check_page_widget.drop_requested.connect(self._on_internal_check_drop_requested)
                    self._internal_check_page_widget.import_requested.connect(self._on_internal_check_import_requested)
                    self._internal_check_page_widget.date_folder_entered.connect(self._on_internal_check_date_folder_entered)
                    self._internal_check_page_widget.video_preview_requested.connect(
                        self._open_video_preview_from_internal_check
                    )
                    self._internal_check_page_widget.send_to_delivery_requested.connect(
                        self._on_internal_check_send_to_delivery_requested
                    )
                    self._connect_inbox_outbox_title_row(self._internal_check_page_widget._title_row)
                    self._content_stack.addWidget(self._internal_check_page_widget)
                self._internal_check_page_widget.set_project_root(self._project_root)
                self._content_stack.setCurrentWidget(self._internal_check_page_widget)
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
                self._restore_internal_check_date_folder_state()
            elif context_name == "Delivery":
                self._sync_filter_state_from_sidebar()
                self._inbox_switch_cooldown = True
                QTimer.singleShot(120, lambda: setattr(self, "_inbox_switch_cooldown", False))
                if self._outbox_page_widget is None:
                    self._outbox_page_widget = OutboxPageWidget(self)
                    self._outbox_page_widget.tree_distribute_paths_changed.connect(
                        self._on_outbox_tree_distribute_paths_changed
                    )
                    self._outbox_page_widget.open_folder_requested.connect(self._on_outbox_open_folder_requested)
                    self._outbox_page_widget.drop_requested.connect(self._on_outbox_drop_requested)
                    self._outbox_page_widget.import_requested.connect(self._on_outbox_import_requested)
                    self._outbox_page_widget.date_folder_entered.connect(self._on_outbox_date_folder_entered)
                    self._outbox_page_widget.video_preview_requested.connect(
                        self._open_video_preview_from_delivery
                    )
                    self._connect_inbox_outbox_title_row(self._outbox_page_widget._title_row)
                    self._content_stack.addWidget(self._outbox_page_widget)
                self._outbox_page_widget.set_project_root(self._project_root)
                self._outbox_page_widget.set_type_filter(self._filter_panel.filters().current_type() or "")
                self._content_stack.setCurrentWidget(self._outbox_page_widget)
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
                self._restore_outbox_date_folder_state()
            elif context_name == "Outbox":
                self._on_context_switched("Delivery")
                return
            elif context_name == "Trash":
                self._sync_filter_state_from_sidebar()
                if self._trash_page_widget is None:
                    self._trash_page_widget = TrashPageWidget(self)
                    self._trash_page_widget.trash_changed.connect(self._on_trash_changed_from_trash_page)
                    self._content_stack.addWidget(self._trash_page_widget)
                self._trash_page_widget.set_project_root(self._project_root)
                self._content_stack.setCurrentWidget(self._trash_page_widget)
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
            elif context_name == "Dashboard":
                self._sync_filter_state_from_sidebar()
                if self._dashboard_page_widget is None:
                    self._dashboard_page_widget = DashboardPageWidget(self)
                    self._dashboard_page_widget.open_schedule_requested.connect(self._on_dashboard_open_schedule)
                    self._dashboard_page_widget.open_notes_entity_requested.connect(
                        self._on_dashboard_open_notes_entity
                    )
                    self._dashboard_page_widget.note_go_to_department_requested.connect(
                        self._on_dashboard_note_go_to_department
                    )
                    self._dashboard_page_widget.dashboard_entity_nav_requested.connect(
                        self._on_dashboard_entity_nav
                    )
                    self._dashboard_page_widget.open_scope_requested.connect(self._on_dashboard_open_scope)
                    self._dashboard_page_widget.schedule_jump_requested.connect(
                        self._on_dashboard_schedule_jump
                    )
                    self._dashboard_page_widget.unscheduled_entities_requested.connect(
                        self._on_dashboard_unscheduled_entities,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._dashboard_page_widget.overdue_entities_requested.connect(
                        self._on_dashboard_overdue_entities,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._dashboard_page_widget.customize_mode_changed.connect(
                        self._on_dashboard_customize_mode_changed,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._dashboard_page_widget.dashboard_layout_changed.connect(
                        self._on_dashboard_layout_changed,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._content_stack.addWidget(self._dashboard_page_widget)
                self._refresh_dashboard_page()
                self._content_stack.setCurrentWidget(self._dashboard_page_widget)
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
                self._sync_dashboard_sidebar_panel()
            elif context_name == "Schedule":
                self._sync_filter_state_from_sidebar()
                self._set_inspector_empty_hint_for_context("Schedule")
                if self._schedule_page_widget is None:
                    self._schedule_page_widget = SchedulePageWidget(self)
                    self._schedule_page_widget.schedule_changed.connect(self._on_schedule_changed)
                    self._schedule_page_widget.sidebar_department_sync_requested.connect(
                        self._on_schedule_sidebar_department_sync
                    )
                    self._content_stack.addWidget(self._schedule_page_widget)
                    self._schedule_page_widget.entity_row_selected.connect(
                        self._on_schedule_entity_row_selected,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._schedule_page_widget.entity_row_cleared.connect(
                        self._on_schedule_entity_row_cleared,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._schedule_page_widget.department_skip_toggle_requested.connect(
                        self._on_schedule_department_skip_toggle,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._schedule_page_widget.entity_skip_toggle_requested.connect(
                        self._on_schedule_entity_skip_toggle,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._schedule_page_widget.lane_skip_toggle_requested.connect(
                        self._on_schedule_lane_skip_toggle,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._schedule_page_widget.skipped_list_requested.connect(
                        self._open_skipped_schedule_dialog,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._schedule_page_widget.jump_to_entity_requested.connect(
                        self._on_schedule_jump_to_entity,
                        Qt.ConnectionType.UniqueConnection,
                    )
                    self._schedule_page_widget.back_to_dashboard_requested.connect(
                        self._on_schedule_back_to_dashboard
                    )
                self._schedule_page_widget.set_project_root(self._project_root)
                self._schedule_page_widget.set_workspace_root(self._workspace_root)
                self._schedule_page_widget.set_schedule_editable(self._can_edit_schedule())
                self._schedule_page_widget.set_thumbnail_manager(self._thumbnail_manager)
                self._schedule_page_widget.refresh(self._project_index)
                self._apply_schedule_sidebar_filters()
                self._content_stack.setCurrentWidget(self._schedule_page_widget)
                QTimer.singleShot(0, self._consume_pending_schedule_jump)
                QTimer.singleShot(800, self._maybe_discord_schedule_due)
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
                self._refresh_inspector_selection()
            else:
                self._main_view.clear()
                self._main_view.set_empty_override(self._empty_message_for_context(context_name))

            self._sync_primary_action()
            self._sync_filter_state_from_sidebar()
            self._active_nav_context = context_name
        finally:
            self._context_switch_in_progress = False
            self._raise_page_loading_if_visible()
            if self._nav_quick_pending_filters is not None:
                snap = self._nav_quick_pending_filters
                self._nav_quick_pending_filters = None
                self._apply_nav_quick_filter_snapshot(snap)

    def _command_palette_projects(self) -> list[dict]:
        rows: list[dict] = []
        for proj in sorted(self._workspace_projects, key=lambda p: (p.name or "").casefold()):
            name = (proj.name or proj.root.name or "").strip()
            if not name:
                continue
            root = str(proj.root)
            rows.append(
                {
                    "title": name,
                    "path": root,
                    "subtitle": "Project",
                    "search_text": f"{name} {root}".casefold(),
                }
            )
        cur = str(self._project_root) if self._project_root else ""
        if cur:
            for row in rows:
                if row.get("path") == cur:
                    row["subtitle"] = "Project · current"
                    break
        return rows

    def _command_palette_inbox(self) -> list[dict]:
        if self._project_root is None:
            return []
        from monostudio.core.inbox_reader import flatten_inbox_for_palette

        try:
            return flatten_inbox_for_palette(self._project_root)
        except Exception:
            return []

    def _command_palette_entities(self) -> list[dict]:
        pi = self._project_index
        if pi is None:
            return []
        rows: list[dict] = []
        for asset in sorted(pi.assets, key=lambda a: ((a.name or a.path.name or "").casefold())):
            name = (asset.name or asset.path.name or "").strip()
            if not name:
                continue
            typ = (getattr(asset, "asset_type", None) or "").strip()
            _, type_icon = self._filter_panel.filters().get_type_display(typ or None)
            rows.append(
                {
                    "context": "Assets",
                    "path": str(asset.path),
                    "title": name,
                    "subtitle": f"Asset · {typ}" if typ else "Asset",
                    "type_id": typ or None,
                    "icon_name": type_icon or "box",
                }
            )
        for shot in sorted(pi.shots, key=lambda s: ((s.name or s.path.name or "").casefold())):
            name = (shot.name or shot.path.name or "").strip()
            if not name:
                continue
            rows.append(
                {
                    "context": "Shots",
                    "path": str(shot.path),
                    "title": name,
                    "subtitle": "Shot",
                    "icon_name": "clapperboard",
                }
            )
        return rows

    def _open_pipeline_entity_in_main_view(
        self,
        *,
        context: str,
        path: Path,
        type_id: str | None = None,
        department: str | None = None,
    ) -> bool:
        """Switch Assets/Shots context and sidebar filters so the entity is visible, then select it."""
        ctx = (context or "").strip()
        if ctx not in ("Assets", "Shots"):
            return False
        kind = "asset" if ctx == "Assets" else "shot"
        ref = self._pipeline_ref_for_path(path, kind)
        typ = (type_id or "").strip() or None
        if ctx == "Assets" and not typ and isinstance(ref, Asset):
            typ = (ref.asset_type or "").strip() or None
        active_dept = (department or "").strip() or None
        filter_dept = active_dept
        if filter_dept and ref is not None and not self._entity_has_department(ref, filter_dept):
            filter_dept = None
        self._nav_rail.set_current_context(ctx)
        filters = self._filter_panel.filters()
        if typ:
            filters.set_selected_type(typ, emit=False)
        filters.set_selected_department(filter_dept, emit=False)
        self._controller.sync_filter_state(department=filter_dept, type_id=typ)
        self._sync_filter_state_from_sidebar()
        self._reload_main_view()
        self._app_state.set_selection(str(path))
        if not self._main_view.select_item_by_path(path):
            return False
        if active_dept:
            filters.set_selected_department(active_dept, emit=False)
            self._controller.sync_filter_state(department=active_dept, type_id=typ)
        self._set_main_view_department()
        self._set_main_view_type()
        self._refresh_inspector_selection()
        return True

    def _on_command_palette_entity(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        ctx = (payload.get("context") or "").strip()
        path_str = (payload.get("path") or "").strip()
        if ctx not in ("Assets", "Shots") or not path_str:
            return
        type_id = payload.get("type_id")
        typ = type_id.strip() if isinstance(type_id, str) and type_id.strip() else None
        self._open_pipeline_entity_in_main_view(
            context=ctx,
            path=Path(path_str),
            type_id=typ,
        )

    def _on_command_palette_project(self, project_root: str) -> None:
        path = (project_root or "").strip()
        if path:
            self._switch_project(path)

    def _on_command_palette_inbox(self, item_path: str) -> None:
        path_str = (item_path or "").strip()
        if not path_str or self._project_root is None:
            return
        item_path_p = Path(path_str)
        if self._nav_rail.current_context() != "Inbox":
            self._nav_rail.set_current_context("Inbox")
        if self._inbox_page_widget is not None:
            self._inbox_page_widget.open_item_path(self._project_root, item_path_p)

    def _open_nav_quick_picker(self) -> None:
        from monostudio.ui_qt.nav_quick_picker_dialog import NavQuickPickerDialog
        from monostudio.ui_qt.nav_quick_view import keyboard_input_blocks_shortcuts

        if keyboard_input_blocks_shortcuts():
            return
        existing = self._nav_quick_picker_dialog
        if existing is not None and existing.isVisible():
            existing.reject()
            return
        dialog = NavQuickPickerDialog(settings=self._settings, parent=self)
        self._nav_quick_picker_dialog = dialog
        dialog.finished.connect(lambda _=0: setattr(self, "_nav_quick_picker_dialog", None))
        dialog.slot_selected.connect(self._recall_nav_quick_slot)
        dialog.slots_changed.connect(self._on_nav_quick_picker_slots_changed)
        dialog.exec()

    def _on_nav_quick_picker_slots_changed(self) -> None:
        self._nav_rail.refresh_quick_view_tooltips(self._settings)

    def _open_command_palette(self) -> None:
        from monostudio.ui_qt.command_palette_dialog import CommandPaletteDialog
        from monostudio.ui_qt.nav_quick_view import keyboard_input_blocks_shortcuts

        if keyboard_input_blocks_shortcuts():
            return
        dialog = CommandPaletteDialog(
            settings=self._settings,
            entities=self._command_palette_entities(),
            projects=self._command_palette_projects(),
            inbox_items=self._command_palette_inbox(),
            parent=self,
        )
        dialog.page_selected.connect(self._nav_rail.set_current_context)
        dialog.quick_slot_selected.connect(self._recall_nav_quick_slot)
        dialog.entity_selected.connect(self._on_command_palette_entity)
        dialog.project_selected.connect(self._on_command_palette_project)
        dialog.inbox_selected.connect(self._on_command_palette_inbox)
        dialog.exec()

    def _recall_nav_quick_slot(self, payload: dict) -> None:
        ctx = (payload.get("context") or "").strip()
        if not ctx:
            return
        filters = payload.get("filters")
        snap = dict(filters) if isinstance(filters, dict) and filters else None
        if self._nav_rail.current_context() == ctx:
            if snap is not None:
                self._apply_nav_quick_filter_snapshot(snap)
        else:
            self._nav_quick_pending_filters = snap
            self._nav_rail.set_current_context(ctx)
        self._show_nav_quick_recall_toast(payload)

    def _nav_quick_filter_suffix(self, payload: dict) -> str:
        """Human-readable type · department suffix from a quick-view snapshot."""
        panel = self._filter_panel.filters()
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        detail_parts: list[str] = []
        type_id = filters.get("active_type") if isinstance(filters, dict) else None
        dept = filters.get("active_department") if isinstance(filters, dict) else None
        if isinstance(type_id, str) and type_id.strip():
            label, _ = panel.get_type_display(type_id.strip())
            if label:
                detail_parts.append(label)
        if isinstance(dept, str) and dept.strip():
            dep_label, _ = panel.get_department_display(dept.strip())
            if dep_label:
                detail_parts.append(dep_label)
        return f" · {' · '.join(detail_parts)}" if detail_parts else ""

    def _show_nav_quick_recall_toast(self, payload: dict) -> None:
        ctx = (payload.get("context") or "").strip()
        if not ctx:
            return
        suffix = self._nav_quick_filter_suffix(payload)
        notification_service.operational_success(f"Switched to {ctx}{suffix}")

    def _apply_nav_quick_filter_snapshot(self, filters: dict[str, object]) -> None:
        panel = self._filter_panel.filters()
        panel.import_filter_snapshot(filters, emit=False)
        ctx = self._nav_rail.current_context()
        if ctx in ("Assets", "Shots"):
            self._pull_browser_filters_from_sidebar()
            self._set_main_view_type()
            self._set_main_view_department()
            self._reload_main_view()
        elif ctx == "Schedule":
            self._apply_schedule_sidebar_filters()
        elif ctx == "Inbox" and self._inbox_page_widget is not None:
            self._inbox_page_widget.set_type_filter(panel.current_type() or "")
        elif ctx in ("Delivery", "Outbox") and self._outbox_page_widget is not None:
            self._outbox_page_widget.set_type_filter(panel.current_type() or "")
        elif ctx == SidebarContext.INTERNAL_CHECK.value and self._internal_check_page_widget is not None:
            pass
        elif ctx == "Project Guide" and self._reference_page_widget is not None:
            dept = panel.current_department() or "reference"
            dep_label, dep_icon = panel.get_department_display(dept)
            self._reference_page_widget.set_header_badge_display(label=dep_label, icon_name=dep_icon)
            self._reference_page_widget.set_department(dept)

    def _on_nav_quick_slot_assigned(self, slot: int, payload: dict) -> None:
        ctx = (payload.get("context") or "").strip()
        suffix = self._nav_quick_filter_suffix(payload)
        notification_service.operational_success(f"Quick view {slot} → {ctx}{suffix}")
        self._nav_rail.refresh_quick_view_tooltips(self._settings)

    def _on_context_clicked(self, context_name: str) -> None:
        # Reload current view (click on already-selected nav item). No page-change toast here
        # to avoid duplicate with context_changed when user clicks a different page.
        # Spec: click reloads Main View. (No autoscan trigger unless it was a switch.)
        # Note: Switching to Inbox emits both context_changed and context_clicked; we must not clear Inbox here.
        if context_name == "Dashboard":
            self._refresh_dashboard_page()
            return
        self._context_switch_in_progress = True
        try:
            self._main_view.set_context_title(context_name)
            self._entered_parent = None
            try:
                self._main_view.clear_selection()
            except Exception:
                pass
            self._inspector.set_item(None)

            if context_name in ("Assets", "Shots", "Schedule"):
                if context_name in ("Assets", "Shots"):
                    self._sync_filter_state_from_sidebar()
                if context_name == "Schedule" and self._schedule_page_widget is not None:
                    self._schedule_page_widget.set_project_root(self._project_root)
                    self._schedule_page_widget.refresh(self._project_index)
                    self._apply_schedule_sidebar_filters()
                elif context_name in ("Assets", "Shots"):
                    self._reload_main_view()
            elif context_name == "Inbox":
                self._sync_filter_state_from_sidebar()
                self._reload_main_view()
            elif context_name == "Outbox":
                self._sync_filter_state_from_sidebar()
                self._reload_main_view()
            elif context_name == "Delivery":
                self._sync_filter_state_from_sidebar()
                self._reload_main_view()
            elif context_name == "Project Guide":
                self._sync_filter_state_from_sidebar()
                if self._reference_page_widget is not None:
                    self._reference_page_widget.set_project_root(self._project_root)
                    self._reference_page_widget.set_department(self._filter_panel.filters().current_department() or "reference")
            elif context_name == "Trash":
                self._sync_filter_state_from_sidebar()
                if self._trash_page_widget is not None:
                    self._trash_page_widget.set_project_root(self._project_root)
            else:
                self._main_view.clear()
                self._main_view.set_empty_override(self._empty_message_for_context(context_name))
            self._sync_primary_action()
            self._sync_filter_state_from_sidebar()
        finally:
            self._context_switch_in_progress = False

    def _on_department_changed_notify(self, department: str | None) -> None:
        pass  # No toast for department filter change

    def _on_type_changed_notify(self, type_id: str | None) -> None:
        pass  # No toast for type filter change

    def _empty_message_for_context(self, context_name: str) -> str:
        if context_name == "Projects":
            if self._workspace_root is None:
                return "Set a workspace folder in Settings to browse projects."
            return "No projects found in this workspace"
        if context_name == "Dashboard":
            if self._project_root is None:
                return "Select a project using the project switcher in the sidebar."
            return "Loading project overview…"
        if context_name == "Schedule":
            if self._project_root is None:
                return "Select a project using the project switcher in the sidebar."
            return "Loading schedule…"
        return f"{context_name} is not available yet."

    def _on_valid_selection_changed(self, has_selection: bool) -> None:
        if getattr(self, "_context_switch_in_progress", False) or getattr(self, "_filter_switch_in_progress", False):
            # During context switches we intentionally keep Inspector cleared and avoid re-entrant UI updates.
            self._inspector.set_item(None)
            return
        ctx = self._nav_rail.current_context()
        if ctx in ("Inbox", "Project Guide", SidebarContext.INTERNAL_CHECK.value, "Delivery", "Outbox"):
            # Explorer tree/grid drives inspector preview on these pages.
            return
        if not has_selection:
            if getattr(self._inspector, "_current_item", None) is None:
                return
            self._inspector.set_item(None)
            return

        selected = self._main_view.selected_view_item()
        cur = getattr(self._inspector, "_current_item", None)
        sel_path = str(selected.path) if selected is not None and getattr(selected, "path", None) else None
        cur_path = str(cur.path) if cur is not None and getattr(cur, "path", None) else None
        if sel_path is not None and sel_path == cur_path:
            return
        self._inspector.set_item(selected, active_department_hint=self.current_department)

    def _asset_passes_filter(self, asset: Asset | None) -> bool:
        if asset is None:
            return False
        return len(self.filter_assets([asset], self.current_department, self.current_type)) > 0

    def _on_app_state_assets_changed(self, added: list, removed: list, updated: list) -> None:
        _dcc_log = logging.getLogger("monostudio.dcc_debug")
        _dcc_log.debug("assetsChanged signal added=%s removed=%s updated=%s", added, removed, updated)
        if self._nav_rail.current_context() != "Assets":
            _dcc_log.debug("assetsChanged ignored (context != Assets)")
            return
        # Do not apply diff during filter switch (type/department change); _reload_main_view does full replace.
        if getattr(self, "_filter_switch_in_progress", False):
            _dcc_log.debug("assetsChanged ignored (_filter_switch_in_progress)")
            return
        # Build Asset lists so grid receives diffs only; grid does not query AppState.
        added_assets = []
        for i in added:
            a = self._app_state.get_asset(i)
            if a is not None and self._asset_passes_filter(a):
                added_assets.append(a)
        updated_assets = []
        for i in updated:
            a = self._app_state.get_asset(i)
            if a is not None and self._asset_passes_filter(a):
                updated_assets.append(a)
        _dcc_log.debug("assetsChanged resolved added=%d updated=%d -> apply_assets_diff", len(added_assets), len(updated_assets))
        # Clear pending_create for any asset that now has work_file_path (so "Creating…" goes away even if incremental_scan never completed).
        repaint_entity_ids: list[str] = []
        for eid in (added or []) + (updated or []):
            asset = self._app_state.get_asset(eid)
            if not isinstance(asset, Asset):
                continue
            states = getattr(asset, "dcc_work_states", None) or ()
            has_work_path = False
            for key_st in states:
                if isinstance(key_st, (tuple, list)) and len(key_st) >= 2 and getattr(key_st[1], "work_file_path", None):
                    has_work_path = True
                    break
            if has_work_path:
                remove_by_entity(eid)
                repaint_entity_ids.append(eid)
        if repaint_entity_ids:
            _dcc_log.debug("assetsChanged cleared pending for entities with work_file_path: %s", repaint_entity_ids)
            for eid in repaint_entity_ids:
                try:
                    self._main_view.repaint_tiles_for_entity(eid)
                except Exception:
                    pass
        type_reg = TypeRegistry.for_project(self._project_root) if self._project_root else None

        def view_item_builder(asset: Asset) -> ViewItem:
            type_folder = (type_reg.get_type_folder(asset.asset_type) or "").strip() if type_reg else ""
            return ViewItem(
                kind=ViewItemKind.ASSET,
                name=asset.name,
                type_badge=asset.asset_type,
                path=asset.path,
                departments_count=len(asset.departments),
                ref=asset,
                type_folder=type_folder,
            )

        # Capture before apply: apply may clear selection and emit None, overwriting AppState
        _sid = self._app_state.selection_id()
        self._main_view.apply_assets_diff_from_assets(
            added_assets, removed, updated_assets, view_item_builder
        )
        try:
            self._inspector.refresh_last_modified_display()
        except Exception:
            pass
        def _restore():
            self._app_state.set_selection(_sid)
        QTimer.singleShot(0, _restore)

    def _on_app_state_shots_changed(self, added: list, removed: list, updated: list) -> None:
        if self._nav_rail.current_context() != "Shots":
            return

        def resolver(item_id: str) -> ViewItem | None:
            s = self._app_state.get_shot(item_id)
            if s is None:
                return None
            return ViewItem(
                kind=ViewItemKind.SHOT,
                name=s.name,
                type_badge="shot",
                path=s.path,
                departments_count=len(s.departments),
                ref=s,
            )

        _sid = self._app_state.selection_id()
        self._main_view.apply_shots_diff(added, removed, updated, resolver)
        try:
            self._inspector.refresh_last_modified_display()
        except Exception:
            pass
        def _restore():
            self._app_state.set_selection(_sid)
        QTimer.singleShot(0, _restore)

    def _on_app_state_filters_changed(self) -> None:
        # Browser reload is driven by departmentChanged / typeChanged — avoid duplicate full rebuilds.
        return

    def _on_app_state_thumbnails_changed(self, asset_ids: list) -> None:
        """Refresh UI for these asset ids (thumbnail ready or invalidate requested). Do not clear cache here."""
        ids_set = set(asset_ids or [])
        if not ids_set:
            return
        try:
            self._main_view.refresh_thumbnails_for(list(ids_set))
        except Exception:
            pass
        try:
            from monostudio.ui_qt.thumbnails import parse_department_cache_key

            entity_ids: set[str] = set()
            for raw in ids_set:
                ep, _ = parse_department_cache_key(str(raw).strip())
                if ep:
                    entity_ids.add(ep)
            cur = self._main_view.selected_view_item()
            if cur is not None and str(cur.path) in entity_ids:
                self._inspector.update_thumbnail_for_current()
        except Exception:
            pass
        if self._schedule_page_widget is not None and self._nav_rail.current_context() == "Schedule":
            try:
                from monostudio.ui_qt.thumbnails import parse_department_cache_key

                entity_paths: list[str] = []
                for raw in ids_set:
                    ep, _ = parse_department_cache_key(str(raw).strip())
                    if ep:
                        entity_paths.append(ep)
                self._schedule_page_widget.refresh_row_thumbnails(entity_paths or list(ids_set))
            except Exception:
                self._schedule_page_widget.refresh_row_thumbnails(list(ids_set))

    def _on_worker_task_finished(self, category: str, result: object, error: str | None) -> None:
        """Forward worker results to AppState only; never update UI directly."""
        _dcc_log = logging.getLogger("monostudio.dcc_debug")
        if error is not None:
            logging.getLogger(__name__).warning("Worker task %s failed: %s", category, error)
            _dcc_log.debug("worker taskFinished category=%s error=%s", category, error)
            if category == self._PRODUCTION_STATUS_BATCH_WORKER:
                self._hide_page_loading()
                QMessageBox.warning(
                    self,
                    "Production status",
                    f"Could not update skip status:\n{error}",
                )
            elif category == "inspector_preview_thumb":
                self._inspector.clear_preview_loading()
            elif category == "project_load":
                failed_path = str(self._project_root) if self._project_root is not None else ""
                self._handle_project_load_failed(
                    failed_path,
                    error,
                    save=bool(getattr(self, "_project_load_save_on_complete", False)),
                )
            elif category == self._ASSET_RENAME_WORKER:
                self._hide_page_loading()
                logging.warning("Rename asset failed: %s", error)
                self._notify_asset_rename_failed(error or "")
            return
        if category == self._PRODUCTION_STATUS_BATCH_WORKER:
            self._hide_page_loading()
            if isinstance(result, _ProductionStatusBatchResult):
                self._finish_production_status_batch(result)
            return
        if category == "project_load" and isinstance(result, tuple) and len(result) == 2:
            expected_root, index = result[0], result[1]
            if self._project_root is None:
                self._hide_page_loading()
                return
            try:
                current_root = self._project_root.resolve()
            except OSError:
                current_root = self._project_root
            try:
                loaded_root = expected_root.resolve()
            except OSError:
                loaded_root = expected_root
            if current_root != loaded_root or not isinstance(index, ProjectIndex):
                return
            self._complete_project_load(index)
            return
        if category == self._ASSET_RENAME_WORKER and isinstance(result, _AssetRenameWorkerResult):
            if self._project_root is None:
                self._hide_page_loading()
                return
            try:
                current_root = self._project_root.resolve()
            except OSError:
                current_root = self._project_root
            try:
                index_root = result.index.root.resolve()
            except OSError:
                index_root = result.index.root
            if current_root != index_root:
                self._hide_page_loading()
                return
            self._hide_page_loading()
            self._finish_asset_rename(result)
            return
        if category == self._WORKSPACE_STATS_WORKER:
            self._apply_workspace_stats_worker_result(result)
            return
        if category == self._SHOT_REVIEW_RENDER_ENRICH and isinstance(result, list):
            shots = [s for s in result if isinstance(s, Shot)]
            if shots:
                self._apply_shot_review_render_enrich(shots)
            return
        if category == "inspector_preview_thumb" and isinstance(result, tuple) and len(result) >= 3:
            path_str, image_or_none, use_fit = result[0], result[1], result[2]
            self._inspector.apply_preview_thumb(path_str, image_or_none, use_fit)
            return
        if category == "incremental_scan" and not (isinstance(result, tuple) and len(result) >= 4):
            _dcc_log.debug("worker taskFinished incremental_scan result type=%s len=%s (expected tuple len>=4)",
                           type(result).__name__, len(result) if isinstance(result, (tuple, list)) else "n/a")
            return
        if category == "filesystem_scan" and isinstance(result, ProjectIndex):
            self._project_index = result
            self._app_state.update_assets(list(result.assets))
            self._app_state.update_shots(list(result.shots))
            self._app_state.commit_immediate()
            self._filter_panel.set_project_index(result)
            try:
                self._update_fs_watcher_paths()
            except Exception:
                pass
            soft = bool(getattr(self, "_filesystem_scan_soft", False))
            self._filesystem_scan_soft = False
            if soft:
                # Already reloaded on tab switch; a second full reload clears tile icons → visible grid flicker.
                # AppState diffs update rows; keep thumbnail memory cache warm.
                QTimer.singleShot(0, self._main_view.repaint_tile_and_list_views)
            else:
                # Full rescan (e.g. Refresh): drop in-memory thumbs so grid/inspector reload from disk (mtime / new sources).
                try:
                    self._thumbnail_manager.clear_memory_cache()
                except Exception:
                    pass
                self._reload_main_view()
            self._schedule_shot_review_render_enrich()
            self._sync_primary_action()
            self._sync_top_bar()
        elif category == "incremental_scan" and isinstance(result, tuple) and len(result) >= 4:
            new_assets, new_shots, requested_asset_ids, requested_shot_ids = (
                result[0], result[1], result[2], result[3]
            )
            _dcc_log = logging.getLogger("monostudio.dcc_debug")
            _dcc_log.debug("incremental_scan taskFinished success (will clear pending and repaint)")
            _dcc_log.debug(
                "incremental_scan done requested_asset_ids=%s requested_shot_ids=%s new_assets_count=%s new_shots_count=%s",
                requested_asset_ids,
                requested_shot_ids,
                len(new_assets) if isinstance(new_assets, list) else 0,
                len(new_shots) if isinstance(new_shots, list) else 0,
            )
            if not isinstance(new_assets, list) or not isinstance(new_shots, list):
                return
            for a in new_assets or []:
                if isinstance(a, Asset):
                    states = dict(getattr(a, "dcc_work_states", ()) or ())
                    for (dept, dcc), st in states.items():
                        if getattr(st, "work_file_path", None):
                            _dcc_log.debug("incremental_scan asset path=%s (dept=%s dcc=%s) has work_file_path=%s", a.path, dept, dcc, getattr(st, "work_file_path"))
            current_assets = dict(self._app_state.assets())
            current_shots = dict(self._app_state.shots())
            new_asset_paths = {str(Path(a.path).resolve()) for a in new_assets if isinstance(a, Asset)}
            new_shot_paths = {str(Path(s.path).resolve()) for s in new_shots if isinstance(s, Shot)}

            def same_path(key: str, path_value: Path) -> bool:
                try:
                    return Path(key).resolve() == Path(path_value).resolve()
                except OSError:
                    return False

            for aid in requested_asset_ids or []:
                if not aid:
                    continue
                if aid not in new_asset_paths and not any(same_path(aid, a.path) for a in new_assets if isinstance(a, Asset)):
                    current_assets.pop(aid, None)
            for a in new_assets:
                if not isinstance(a, Asset):
                    continue
                # Keep existing AppState key so diff reports "updated" and the same tile row is refreshed.
                existing_key = next((k for k in (requested_asset_ids or []) if same_path(k, a.path)), None)
                key = existing_key if existing_key else str(a.path)
                for k in list(current_assets):
                    if k != key and same_path(k, a.path):
                        current_assets.pop(k, None)
                current_assets[key] = a

            for sid in requested_shot_ids or []:
                if not sid:
                    continue
                if sid not in new_shot_paths and not any(same_path(sid, s.path) for s in new_shots if isinstance(s, Shot)):
                    current_shots.pop(sid, None)
            for s in new_shots:
                if not isinstance(s, Shot):
                    continue
                existing_key = next((k for k in (requested_shot_ids or []) if same_path(k, s.path)), None)
                key = existing_key if existing_key else str(s.path)
                for k in list(current_shots):
                    if k != key and same_path(k, s.path):
                        current_shots.pop(k, None)
                current_shots[key] = s
            self._app_state.update_assets(current_assets)
            self._app_state.update_shots(current_shots)
            self._app_state.commit_immediate()
            # Clear pending only when scan found work_file_path (so "Creating…" goes away when file exists)
            # or when requested entity is missing (deleted on disk). Avoid clearing too early so badge
            # stays "Creating…" until file appears; watcher-driven scan will then clear when file is saved.
            _to_clear: list[str] = []
            def _entity_has_work_path(obj: Asset | Shot) -> bool:
                for key_st in getattr(obj, "dcc_work_states", ()) or ():
                    if isinstance(key_st, (tuple, list)) and len(key_st) >= 2 and getattr(key_st[1], "work_file_path", None):
                        return True
                return False
            for a in new_assets or []:
                if isinstance(a, Asset) and _entity_has_work_path(a):
                    _to_clear.append(str(Path(a.path).resolve()))
            for s in new_shots or []:
                if isinstance(s, Shot) and _entity_has_work_path(s):
                    _to_clear.append(str(Path(s.path).resolve()))
            for aid in requested_asset_ids or []:
                if aid not in new_asset_paths and not any(same_path(aid, a.path) for a in new_assets if isinstance(a, Asset)):
                    _to_clear.append(aid)
            for sid in requested_shot_ids or []:
                if sid not in new_shot_paths and not any(same_path(sid, s.path) for s in new_shots if isinstance(s, Shot)):
                    _to_clear.append(sid)
            if _to_clear:
                _dcc_log.debug("incremental_scan clearing pending for entity_ids=%s", _to_clear)
                remove_for_entities(_to_clear)
            _dcc_log.debug("incremental_scan updating project_index + repaint (skip full reload for Assets/Shots to avoid flicker)")
            if self._project_index is not None:
                self._project_index = ProjectIndex(
                    root=self._project_index.root,
                    assets=tuple(sorted(current_assets.values(), key=lambda x: (x.asset_type, x.name))),
                    shots=tuple(sorted(current_shots.values(), key=lambda x: x.name)),
                )
                self._filter_panel.set_project_index(self._project_index)
                # So new assets/shots (e.g. just created) get their paths watched
                self._update_fs_watcher_paths()
            # Grid already updated via assetsChanged/shotsChanged from commit_immediate(); full reload would clear+repopulate and cause flicker.
            ctx = self._nav_rail.current_context()
            if ctx not in ("Assets", "Shots", "Trash"):
                self._reload_main_view()
            def _repaint_after_scan() -> None:
                self._main_view.repaint_tile_and_list_views()
                try:
                    self._inspector.refresh_last_modified_display()
                except Exception:
                    pass

            QTimer.singleShot(0, _repaint_after_scan)
            if new_shots:
                self._schedule_shot_review_render_enrich()
            self._sync_primary_action()
            self._sync_top_bar()

    def _show_page_loading(self, message: str | None = None) -> None:
        bar = getattr(self, "_page_loading_bar", None)
        if bar is None:
            return
        self._page_loading_visible = True
        bar.show_loading(message)
        QApplication.processEvents()

    def _hide_page_loading(self) -> None:
        self._page_loading_visible = False
        bar = getattr(self, "_page_loading_bar", None)
        if bar is not None:
            bar.hide_loading()

    def _raise_page_loading_if_visible(self) -> None:
        if not getattr(self, "_page_loading_visible", False):
            return
        bar = getattr(self, "_page_loading_bar", None)
        if bar is not None and bar.isVisible():
            bar.raise_()

    def _handle_project_load_failed(self, failed_path: str, error: str, *, save: bool) -> None:
        logging.error("Failed to load project at %s: %s", failed_path, error)
        self._hide_page_loading()
        self._project_index = None
        self._project_root = None
        self._app_state.clear_project_data()
        pending_clear_all()
        self._entered_parent = None
        self._main_view.clear()
        self._filter_panel.filters().set_project_root(None)
        self._sync_pipeline_preset_metadata_ui()
        self._filter_panel.set_project_index(None)
        self._inspector.set_department_registry(None)
        self._inspector.set_department_icon_map({})
        self._inspector.set_type_short_name_map({})
        self._update_fs_watcher_paths()
        self._inspector.set_item(None)
        self._sync_primary_action()
        self._sync_top_bar()
        if save:
            self._settings.setValue("project/root", "")
        QMessageBox.warning(
            self,
            "Project load failed",
            f"Could not open project:\n{failed_path}\n\n{error}\n\nOpen Settings to choose another project.",
        )

    def _complete_project_load(self, index: ProjectIndex) -> None:
        self._hide_page_loading()
        self._project_index = index
        self._entered_parent = None
        self._app_state.update_assets(list(index.assets))
        self._app_state.update_shots(list(index.shots))
        self._app_state.commit_immediate()
        self._filter_panel.set_project_index(index)
        self._update_fs_watcher_paths()
        self._reload_main_view()
        self._refresh_schedule_cache()
        self._sync_user_inbox_alerts()
        self._inspector.set_item(None)
        self._refresh_recent_tasks()
        self._sync_primary_action()
        self._sync_top_bar()
        if self._project_root is not None:
            QTimer.singleShot(2500, self._maybe_discord_schedule_due)
        if self._tray_manager is not None:
            self._tray_manager.refresh_tooltip()
        self._schedule_shot_review_render_enrich()

    def _submit_project_load_task(self) -> None:
        root = self._project_root
        if root is None:
            return
        try:
            expected_root = root.resolve()
        except OSError:
            expected_root = root

        def run() -> tuple[Path, ProjectIndex]:
            index = build_project_index(expected_root)
            try:
                nd = retention_days_from_settings(self._settings)
                purge_expired(expected_root, nd)
            except Exception:
                logging.getLogger(__name__).debug("trash retention purge skipped", exc_info=True)
            return expected_root, index

        task = WorkerTask("project_load", run, manager=self._worker_manager)
        self._worker_manager.submit_task(task, category="project_load", replace_existing=True)

    def _apply_project_root(self, folder: str | None, *, save: bool) -> None:
        # No validation (per rules). Store path and reload UI.
        if save:
            self._settings.setValue("project/root", folder or "")
        try:
            from monostudio.core.crash_recovery import set_crash_context
            set_crash_context(last_project_path=folder or "")
        except Exception:
            pass

        if self._schedule_page_widget is not None:
            self._schedule_page_widget.flush_schedule_document()

        self._worker_manager.cancel_category("project_load")
        self._hide_page_loading()

        self._project_root = Path(folder) if folder else None
        self._controller.set_project_root(self._project_root)
        self._filter_panel.filters().set_project_root(self._project_root)
        self._sync_pipeline_preset_metadata_ui()
        self._main_view.set_project_root(folder)
        self._main_view.set_empty_override(None)
        self._inspector.set_project_root(self._project_root)

        if self._project_root is not None:
            try:
                ensure_outbox_source_folders(self._project_root)
                ensure_delivery_source_folders(self._project_root)
                ensure_internal_check_root(self._project_root)
            except OSError:
                pass
            try:
                dept_reg = DepartmentRegistry.for_project(self._project_root)
                self._inspector.set_department_registry(dept_reg)
                self._inspector.set_department_icon_map(self._dept_icon_map)
                self._inspector.set_type_short_name_map(self._type_short_name_map)
            except Exception:
                self._inspector.set_department_registry(None)
                self._inspector.set_department_icon_map({})
                self._inspector.set_type_short_name_map({})
        else:
            self._inspector.set_department_registry(None)
            self._inspector.set_department_icon_map({})
            self._inspector.set_type_short_name_map({})

        if self._project_root is None:
            self._project_index = None
            self._app_state.clear_project_data()
            pending_clear_all()
            self._entered_parent = None
            self._main_view.clear()
            self._filter_panel.set_project_index(None)
            self._update_fs_watcher_paths()
            self._sync_user_inbox_alerts()
            self._inspector.set_item(None)
            self._refresh_recent_tasks()
            self._sync_primary_action()
            self._sync_top_bar()
            if self._tray_manager is not None:
                self._tray_manager.refresh_tooltip()
            return

        self._project_load_save_on_complete = save
        self._entered_parent = None
        self._project_index = None
        self._app_state.clear_project_data()
        pending_clear_all()
        self._filter_panel.set_project_index(None)
        self._update_fs_watcher_paths()

        project_name = self._project_root.name or "project"
        self._show_page_loading(f"Loading {project_name}…")
        self._main_view.set_empty_override(SCANNING_EMPTY_MESSAGE)
        self._reload_main_view()
        self._inspector.set_item(None)
        self._refresh_recent_tasks()
        self._sync_primary_action()
        self._sync_top_bar()
        if self._tray_manager is not None:
            self._tray_manager.refresh_tooltip()
        self._submit_project_load_task()

    def _refresh_recent_tasks(self) -> None:
        tasks = self._recent_tasks_store.get_for_project(self._project_root) if self._project_root else []
        self._filter_panel.set_recent_tasks(tasks)
        self._nav_rail.set_recent_tasks(tasks)
        if self._tray_manager is not None:
            self._tray_manager.refresh_menu()

    def _apply_workspace_root(self, folder: str | None, *, save: bool) -> None:
        # No validation. Read-only discovery.
        if save:
            self._settings.setValue("workspace/root", folder or "")

        self._workspace_root = Path(folder) if folder else None
        self._workspace_projects = discover_projects(self._workspace_root) if self._workspace_root else []
        self._refresh_workspace_project_stats(schedule_aware=False)
        if self._workspace_projects:
            QTimer.singleShot(1200, self._schedule_workspace_stats_refresh_async)
        self._filter_panel.set_projects_count(len(self._workspace_projects) if self._workspace_root is not None else None)

        if not self._workspace_projects:
            self._main_view.set_empty_override("No projects found in this workspace")
        else:
            self._main_view.set_empty_override(None)

        self._sync_primary_action()
        self._sync_top_bar()

        if self._dashboard_page_widget is not None:
            self._dashboard_page_widget.set_workspace_root(self._workspace_root)
        if self._schedule_page_widget is not None:
            self._schedule_page_widget.set_workspace_root(self._workspace_root)
        self._main_view.set_workspace_root(self._workspace_root)
        self._inspector.set_workspace_root(self._workspace_root)
        self._refresh_user_button()
        self._schedule_identity_prompt()
        self._restore_discord_inbox_outbox()

    def _restore_discord_inbox_outbox(self) -> None:
        if self._workspace_root is None:
            return
        try:
            from monostudio.core.discord_inbox_debounce import restore_inbox_received_outbox
            from monostudio.core.discord_inbox_distributed_debounce import (
                restore_inbox_distributed_outbox,
            )
            from monostudio.core.discord_outbox_received_debounce import (
                restore_outbox_received_outbox,
            )
            from monostudio.core.discord_webhook import restore_failed_posts

            restore_inbox_received_outbox(self._workspace_root)
            restore_inbox_distributed_outbox(self._workspace_root)
            restore_outbox_received_outbox(self._workspace_root)
            restore_failed_posts(self._workspace_root)
        except Exception:
            logging.getLogger(__name__).debug("Discord inbox outbox restore skipped", exc_info=True)

    def _flush_discord_inbox_outbox(self) -> None:
        if self._workspace_root is None:
            return
        try:
            from monostudio.core.discord_inbox_debounce import flush_all_pending_inbox_received
            from monostudio.core.discord_inbox_distributed_debounce import (
                flush_all_pending_inbox_distributed,
            )
            from monostudio.core.discord_outbox_received_debounce import (
                flush_all_pending_outbox_received,
            )
            from monostudio.core.discord_webhook import flush_failed_posts

            flush_all_pending_inbox_received(self._workspace_root)
            flush_all_pending_inbox_distributed(self._workspace_root)
            flush_all_pending_outbox_received(self._workspace_root)
            flush_failed_posts(self._workspace_root)
        except Exception:
            logging.getLogger(__name__).debug("Discord inbox outbox flush skipped", exc_info=True)

    def _rescan_project(self) -> None:
        # Synchronous rescan (e.g. context switch); use when UI must have data immediately.
        if self._project_root is None:
            self._project_index = None
            self._app_state.clear_project_data()
            self._filter_panel.set_project_index(None)
            self._update_fs_watcher_paths()
            return
        self._project_index = build_project_index(self._project_root)
        self._filter_panel.set_project_index(self._project_index)
        self._app_state.update_assets(list(self._project_index.assets))
        self._app_state.update_shots(list(self._project_index.shots))
        self._app_state.commit_immediate()
        # So watcher includes new/updated asset and shot paths (incl. nested dept work dirs).
        self._update_fs_watcher_paths()
        self._schedule_shot_review_render_enrich()

    def _schedule_shot_review_render_enrich(self) -> None:
        """Background render/sequence scan for shot review cards (hybrid — heavy pass)."""
        if self._project_index is None or not self._project_index.shots:
            return
        shots = list(self._project_index.shots)

        def run() -> list[Shot]:
            return enrich_shots_review_render(shots)

        task = WorkerTask(self._SHOT_REVIEW_RENDER_ENRICH, run, manager=self._worker_manager)
        self._worker_manager.submit_task(
            task,
            category=self._SHOT_REVIEW_RENDER_ENRICH,
            replace_existing=True,
        )

    def _apply_shot_review_render_enrich(self, enriched: list[Shot]) -> None:
        if self._project_index is None or not enriched:
            return
        shots = tuple(enriched)
        self._project_index = ProjectIndex(
            root=self._project_index.root,
            assets=self._project_index.assets,
            shots=shots,
        )
        self._app_state.update_shots(list(shots))
        self._app_state.commit_immediate()
        try:
            self._main_view.repaint_tile_and_list_views()
        except Exception:
            pass

    def _submit_rescan_task(self, *, soft: bool = False) -> None:
        """Submit a filesystem scan to WorkerManager; result is forwarded to AppState in _on_worker_task_finished.

        soft=True: after Assets↔Shots switch — merge scan into AppState without clearing thumbnail cache or full main view rebuild.
        """
        if self._project_root is None:
            return
        self._filesystem_scan_soft = bool(soft)
        root = self._project_root

        def run() -> ProjectIndex:
            return build_project_index(root)

        task = WorkerTask("filesystem_scan", run, manager=self._worker_manager)
        self._worker_manager.submit_task(task, category="filesystem_scan", replace_existing=True)

    def _on_fs_meta_thumbnails_stale(self, stale: object) -> None:
        """Invalidate in-memory thumbnails when ``.meta`` files change (watcher does not rescan thumbs)."""
        if not isinstance(stale, list):
            return
        mgr = self._thumbnail_manager
        active_dept = (self._controller.current_department or "").strip() or None
        for entry in stale:
            if not isinstance(entry, (tuple, list)) or len(entry) < 2:
                continue
            entity_path, dept = entry[0], entry[1]
            if not isinstance(entity_path, str) or not entity_path.strip():
                continue
            ep = entity_path.strip()
            if dept is None:
                mgr.invalidate_entity(ep)
            elif isinstance(dept, str) and dept.strip():
                mgr.invalidate(ep, department=dept.strip())
            else:
                mgr.invalidate_entity(ep)
            try:
                self._main_view.invalidate_thumbnail(Path(ep), department=active_dept)
            except Exception:
                pass
            try:
                cur = self._main_view.selected_view_item()
                if cur is not None and str(cur.path) == ep:
                    self._inspector.update_thumbnail_for_current()
            except Exception:
                pass

    def _on_fs_item_notes_stale(self, entities: object) -> None:
        """External edits to ``.monostudio/item_comments.json``: refresh note badges."""
        if not isinstance(entities, list):
            return
        for ep in entities:
            if not isinstance(ep, str) or not ep.strip():
                continue
            try:
                self._main_view.invalidate_notes_open_count_cache(Path(ep.strip()))
            except Exception:
                pass
        try:
            self._inspector.refresh_notes_badge()
        except Exception:
            pass
        self._refresh_dashboard_if_visible()

    def _ensure_entity_special_folders_watched_for_paths(self, entity_paths: list[str]) -> None:
        for ep in entity_paths:
            if not isinstance(ep, str) or not ep.strip():
                continue
            try:
                self._ensure_entity_special_folders_watched(Path(ep.strip()))
            except Exception:
                pass

    def _on_fs_entity_special_folders_stale(self, entities: object) -> None:
        """Changes under ``reference/`` or ``concept/``: refresh Ref tab + grid hint cache."""
        if not isinstance(entities, list):
            return
        entity_paths = [ep for ep in entities if isinstance(ep, str) and ep.strip()]
        if entity_paths:
            self._ensure_entity_special_folders_watched_for_paths(entity_paths)
        for ep in entities:
            if not isinstance(ep, str) or not ep.strip():
                continue
            try:
                self._main_view.invalidate_entity_reference_cache(Path(ep.strip()))
            except Exception:
                pass
        try:
            self._inspector.refresh_special_folders_for_entity_paths(entities)
        except Exception:
            pass
        try:
            self._main_view.refresh_reference_hint_badges()
        except Exception:
            pass

    def _on_fs_batch_ready(
        self,
        asset_ids: list,
        shot_ids: list,
        type_folders: list,
        rescan_assets_listing: bool = False,
        rescan_shots_listing: bool = False,
    ) -> None:
        """Submit incremental scan for fs watcher batch; never full rescan."""
        _watcher_log = logging.getLogger("monostudio.fs_watcher")
        if self._project_root is None:
            return
        root = self._project_root
        a_ids = [x for x in (asset_ids or []) if isinstance(x, str) and x.strip()]
        s_ids = [x for x in (shot_ids or []) if isinstance(x, str) and x.strip()]
        t_folders = [x for x in (type_folders or []) if isinstance(x, str) and x.strip()]
        ra = bool(rescan_assets_listing)
        rs = bool(rescan_shots_listing)
        if not a_ids and not s_ids and not t_folders and not ra and not rs:
            return
        touched_entities = list(dict.fromkeys([*a_ids, *s_ids]))
        if touched_entities:
            self._ensure_entity_special_folders_watched_for_paths(touched_entities)
        _watcher_log.debug(
            "fs_watcher batch_ready -> incremental_scan asset_ids=%s shot_ids=%s type_folders=%s rescan_assets=%s rescan_shots=%s",
            len(a_ids),
            len(s_ids),
            len(t_folders),
            ra,
            rs,
        )

        current_assets = dict(self._app_state.assets())
        current_shots = dict(self._app_state.shots())
        snap_a = list(current_assets.keys()) if ra else None
        snap_s = list(current_shots.keys()) if rs else None

        def run() -> tuple[list[Asset], list[Shot], list[str], list[str]]:
            return run_incremental_scan(
                root,
                a_ids,
                s_ids,
                t_folders,
                rescan_assets_listing=ra,
                rescan_shots_listing=rs,
                listing_snapshot_asset_ids=snap_a,
                listing_snapshot_shot_ids=snap_s,
            )

        task = WorkerTask("incremental_scan", run, manager=self._worker_manager)
        self._worker_manager.submit_task(
            task,
            category="incremental_scan",
            replace_existing=True,
            debounce_ms=400,
        )

    def _submit_incremental_scan_for_item(self, item: Asset | Shot) -> None:
        """Rescan a single asset/shot so AppState and tile DCC badges update (e.g. after Create New)."""
        if self._project_root is None:
            return
        root = self._project_root.resolve()
        # Ensure absolute path so worker (any cwd) scans the correct directory.
        p = Path(item.path)
        if p.is_absolute():
            item_path = str(p.resolve())
        else:
            item_path = str((root / p).resolve())
        if isinstance(item, Asset):
            a_ids, s_ids = [item_path], []
        else:
            a_ids, s_ids = [], [item_path]

        def run() -> tuple[list[Asset], list[Shot], list[str], list[str]]:
            return run_incremental_scan(root, a_ids, s_ids, [])

        task = WorkerTask("incremental_scan", run, manager=self._worker_manager)
        self._worker_manager.submit_task(
            task,
            category="incremental_scan",
            replace_existing=True,
            debounce_ms=0,
        )

    def _append_entity_meta_watch_path(
        self,
        entity_base: Path,
        to_add: list[str],
        seen: set[str],
        *,
        max_paths: int,
    ) -> None:
        append_entity_meta_watch_paths(entity_base, to_add, seen, max_paths=max_paths)

    def _ensure_entity_meta_watched(self, entity_base: Path) -> None:
        """Register ``.meta`` and its contents after first write (e.g. paste thumbnail)."""
        if self._watcher_manually_disabled or self._project_root is None:
            return
        existing = set(self._fs_watcher.directories()) | set(self._fs_watcher.files())
        to_add: list[str] = []
        seen = set(existing)
        append_entity_meta_watch_paths(entity_base, to_add, seen, max_paths=2000)
        new_paths = [p for p in to_add if p not in existing]
        if new_paths:
            self._fs_watcher.addPaths(new_paths)

    def _append_entity_monostudio_watch_path(
        self,
        entity_base: Path,
        to_add: list[str],
        seen: set[str],
        *,
        max_paths: int,
    ) -> None:
        append_entity_monostudio_watch_paths(entity_base, to_add, seen, max_paths=max_paths)

    def _append_entity_special_folder_watch_path(
        self,
        entity_base: Path,
        to_add: list[str],
        seen: set[str],
        *,
        max_paths: int,
    ) -> None:
        append_entity_special_folder_watch_paths(entity_base, to_add, seen, max_paths=max_paths)

    def _ensure_entity_special_folders_watched(self, entity_base: Path) -> None:
        """Register ``reference/`` and ``concept/`` after mkdir or first file drop."""
        if self._watcher_manually_disabled or self._project_root is None:
            return
        existing = set(self._fs_watcher.directories()) | set(self._fs_watcher.files())
        to_add: list[str] = []
        seen = set(existing)
        append_entity_special_folder_watch_paths(entity_base, to_add, seen, max_paths=2000)
        new_paths = [p for p in to_add if p not in existing]
        if new_paths:
            self._fs_watcher.addPaths(new_paths)

    def _ensure_entity_monostudio_watched(self, entity_base: Path) -> None:
        """Register ``.monostudio`` after first notes write (folder may not exist at initial watcher setup)."""
        if self._watcher_manually_disabled or self._project_root is None:
            return
        existing = set(self._fs_watcher.directories()) | set(self._fs_watcher.files())
        to_add: list[str] = []
        seen = set(existing)
        append_entity_monostudio_watch_paths(entity_base, to_add, seen, max_paths=2000)
        new_paths = [p for p in to_add if p not in existing]
        if new_paths:
            self._fs_watcher.addPaths(new_paths)

    def _watcher_paths_for_asset(self, root: Path, asset: Asset, *, max_paths: int = 2000) -> list[str]:
        """Return watcher paths for one asset (dept/work/publish, ``.meta``, ``.monostudio``). Used after rename."""
        to_add: list[str] = []
        _seen: set[str] = set()
        base = Path(asset.path)
        if not base.is_absolute():
            base = (root / base).resolve()
        use_dcc_folders = read_use_dcc_folders(root)
        try:
            _dcc_reg = get_default_dcc_registry()
        except Exception:
            _dcc_reg = None

        def _add_dir_and_ancestors(dir_path: Path, entity_base: Path) -> None:
            try:
                resolved = dir_path.resolve()
                base_resolved = entity_base.resolve()
            except OSError:
                return
            if not resolved.is_dir() or len(to_add) >= max_paths:
                return
            s = str(resolved)
            if s not in _seen:
                _seen.add(s)
                to_add.append(s)
            parent = resolved.parent
            while parent != base_resolved and len(parent.parts) > len(base_resolved.parts):
                if parent.is_dir() and len(to_add) < max_paths:
                    ps = str(parent)
                    if ps not in _seen:
                        _seen.add(ps)
                        to_add.append(ps)
                parent = parent.parent

        for dept in asset.departments:
            dept_dir = Path(dept.path) if Path(dept.path).is_absolute() else (root / dept.path).resolve()
            _add_dir_and_ancestors(dept_dir, base)
            if use_dcc_folders and _dcc_reg is not None:
                for dcc_id in _dcc_reg.get_all_dccs():
                    try:
                        wp = resolve_work_path(dept_dir, dcc_id, True, _dcc_reg)
                    except Exception:
                        continue
                    if wp.is_dir() and len(to_add) < max_paths:
                        s = str(wp)
                        if s not in _seen:
                            _seen.add(s)
                            to_add.append(s)
            else:
                wp = Path(dept.work_path) if Path(dept.work_path).is_absolute() else (root / dept.work_path).resolve()
                if wp.is_dir() and len(to_add) < max_paths:
                    s = str(wp)
                    if s not in _seen:
                        _seen.add(s)
                        to_add.append(s)
            pp = Path(dept.publish_path) if Path(dept.publish_path).is_absolute() else (root / dept.publish_path).resolve()
            if pp.is_dir() and len(to_add) < max_paths:
                s = str(pp)
                if s not in _seen:
                    _seen.add(s)
                    to_add.append(s)
        self._append_entity_meta_watch_path(base, to_add, _seen, max_paths=max_paths)
        self._append_entity_monostudio_watch_path(base, to_add, _seen, max_paths=max_paths)
        self._append_entity_special_folder_watch_path(base, to_add, _seen, max_paths=max_paths)
        return to_add

    def _update_fs_watcher_paths(self) -> None:
        """Set or clear watched paths and collector state from current project root.
        Watches project root, assets/, shots/, each registered ``assets/<type>/`` (new asset folders),
        each entity ``.meta/`` (files + subdirs inside),
        each entity ``.monostudio/`` (notes JSON and peers),
        each entity ``reference/`` and ``concept/`` (top-level entries),
        and dept/work/publish so changes are detected on Windows
        (no recursive watch).
        For nested (subdepartment) layout, also watches parent dirs of each department
        so that new subdepartment folders (e.g. surfacing/lookdev) trigger a scan.
        """
        _watcher_log = logging.getLogger("monostudio.fs_watcher")
        existing = self._fs_watcher.directories() + self._fs_watcher.files()
        if existing:
            self._fs_watcher.removePaths(existing)
        self._fs_event_collector.set_project_root(None)
        self._fs_event_collector.set_registries(None, None)
        if self._project_root is None:
            _watcher_log.debug("fs_watcher paths cleared (no project)")
            return
        if self._watcher_manually_disabled:
            _watcher_log.debug("fs_watcher paths not added (manually paused)")
            return
        try:
            root = self._project_root.resolve()
        except OSError:
            return
        to_add: list[str] = []
        if root.is_dir():
            to_add.append(str(root))
        struct_reg = StructureRegistry.for_project(root)
        assets_dir = root / struct_reg.get_folder("assets")
        shots_dir = root / struct_reg.get_folder("shots")
        if assets_dir.is_dir():
            to_add.append(str(assets_dir))
        if shots_dir.is_dir():
            to_add.append(str(shots_dir))
        # Watch each asset/shot dir and every DCC work/ dir (model/blender/work, model/maya/work, …)
        _max_paths = 2000
        _seen: set[str] = set(to_add)
        try:
            proj_mono = root / ".monostudio"
            proj_mono.mkdir(parents=True, exist_ok=True)
            inbox_path = proj_mono / "mention_inbox.json"
            if inbox_path.is_file():
                s_inbox = str(inbox_path.resolve())
                if s_inbox not in _seen:
                    _seen.add(s_inbox)
                    to_add.append(s_inbox)
            assign_path = proj_mono / "assign_inbox.json"
            if assign_path.is_file():
                s_assign = str(assign_path.resolve())
                if s_assign not in _seen:
                    _seen.add(s_assign)
                    to_add.append(s_assign)
        except OSError:
            pass
        use_dcc_folders = read_use_dcc_folders(root)
        try:
            _dcc_reg = get_default_dcc_registry()
        except Exception:
            _dcc_reg = None

        try:
            _watch_type_reg = TypeRegistry.for_project(root)
            for _tid in _watch_type_reg.get_types():
                _tf = (_watch_type_reg.get_type_folder(_tid) or "").strip()
                if not _tf:
                    continue
                _td = assets_dir / _tf
                if _td.is_dir() and len(to_add) < _max_paths:
                    try:
                        _s = str(_td.resolve())
                    except OSError:
                        _s = str(_td)
                    if _s not in _seen:
                        _seen.add(_s)
                        to_add.append(_s)
        except Exception:
            pass

        def _add_dir_and_ancestors(dir_path: Path, entity_base: Path) -> None:
            """Add dir_path and its parent chain up to (not including) entity_base so nested subdepartments are watched."""
            try:
                resolved = dir_path.resolve()
                base_resolved = entity_base.resolve()
            except OSError:
                return
            if not resolved.is_dir() or len(to_add) >= _max_paths:
                return
            # Add this directory
            s = str(resolved)
            if s not in _seen:
                _seen.add(s)
                to_add.append(s)
            # Add parent chain for nested layout (e.g. surfacing when dept is surfacing/texturing)
            parent = resolved.parent
            while parent != base_resolved and len(parent.parts) > len(base_resolved.parts):
                if parent.is_dir() and len(to_add) < _max_paths:
                    ps = str(parent)
                    if ps not in _seen:
                        _seen.add(ps)
                        to_add.append(ps)
                parent = parent.parent

        # Do not watch the asset/shot folder itself — it would lock the folder on Windows and block rename.
        # Watch only department, work, publish subdirs so we still get change events without holding the entity handle.
        if self._project_index is not None:
            for asset in self._project_index.assets:
                base = Path(asset.path)
                if not base.is_absolute():
                    base = (root / base).resolve()
                for dept in asset.departments:
                    dept_dir = dept.path if Path(dept.path).is_absolute() else (root / dept.path).resolve()
                    _add_dir_and_ancestors(dept_dir, base)
                    if use_dcc_folders and _dcc_reg is not None:
                        for dcc_id in _dcc_reg.get_all_dccs():
                            try:
                                wp = resolve_work_path(dept_dir, dcc_id, True, _dcc_reg)
                            except Exception:
                                continue
                            if wp.is_dir() and len(to_add) < _max_paths:
                                s = str(wp)
                                if s not in _seen:
                                    _seen.add(s)
                                    to_add.append(s)
                    else:
                        wp = dept.work_path if Path(dept.work_path).is_absolute() else (root / dept.work_path).resolve()
                        if wp.is_dir() and len(to_add) < _max_paths:
                            s = str(wp)
                            if s not in _seen:
                                _seen.add(s)
                                to_add.append(s)
                    pp = dept.publish_path if Path(dept.publish_path).is_absolute() else (root / dept.publish_path).resolve()
                    if pp.is_dir() and len(to_add) < _max_paths:
                        s = str(pp)
                        if s not in _seen:
                            _seen.add(s)
                            to_add.append(s)
                self._append_entity_meta_watch_path(base, to_add, _seen, max_paths=_max_paths)
                self._append_entity_monostudio_watch_path(base, to_add, _seen, max_paths=_max_paths)
                self._append_entity_special_folder_watch_path(base, to_add, _seen, max_paths=_max_paths)
            for shot in self._project_index.shots:
                base = Path(shot.path)
                if not base.is_absolute():
                    base = (root / base).resolve()
                for dept in shot.departments:
                    dept_dir = dept.path if Path(dept.path).is_absolute() else (root / dept.path).resolve()
                    _add_dir_and_ancestors(dept_dir, base)
                    if use_dcc_folders and _dcc_reg is not None:
                        for dcc_id in _dcc_reg.get_all_dccs():
                            try:
                                wp = resolve_work_path(dept_dir, dcc_id, True, _dcc_reg)
                            except Exception:
                                continue
                            if wp.is_dir() and len(to_add) < _max_paths:
                                s = str(wp)
                                if s not in _seen:
                                    _seen.add(s)
                                    to_add.append(s)
                    else:
                        wp = dept.work_path if Path(dept.work_path).is_absolute() else (root / dept.work_path).resolve()
                        if wp.is_dir() and len(to_add) < _max_paths:
                            s = str(wp)
                            if s not in _seen:
                                _seen.add(s)
                                to_add.append(s)
                    pp = dept.publish_path if Path(dept.publish_path).is_absolute() else (root / dept.publish_path).resolve()
                    if pp.is_dir() and len(to_add) < _max_paths:
                        s = str(pp)
                        if s not in _seen:
                            _seen.add(s)
                            to_add.append(s)
                self._append_entity_meta_watch_path(base, to_add, _seen, max_paths=_max_paths)
                self._append_entity_monostudio_watch_path(base, to_add, _seen, max_paths=_max_paths)
                self._append_entity_special_folder_watch_path(base, to_add, _seen, max_paths=_max_paths)
        if to_add:
            added = self._fs_watcher.addPaths(to_add)
            failed = len(to_add) - len(added)
            _watcher_log.debug("fs_watcher addPaths: requested=%d added=%d failed=%d", len(to_add), len(added), failed)
            if failed:
                _watcher_log.debug("fs_watcher paths not added: %s", set(to_add) - set(added))
        self._fs_event_collector.set_project_root(root)
        try:
            type_reg = TypeRegistry.for_project(root)
            dept_reg = DepartmentRegistry.for_project(root)
            self._fs_event_collector.set_registries(type_reg, dept_reg)
        except Exception:
            pass

    def _on_refresh_requested(self) -> None:
        # Trigger: user clicks Refresh -> rescan in background via WorkerManager.
        self._entered_parent = None
        try:
            self._main_view.clear_selection()
        except Exception:
            pass
        self._inspector.set_item(None)
        self._submit_rescan_task()
        self._sync_primary_action()

    def _clear_main_view_search(self) -> None:
        self.current_search_query = ""
        self._main_view.set_search_query("")

    def _on_search_query_changed(self, query: str) -> None:
        self.current_search_query = (query or "").strip()
        self._reload_main_view()

    def _reload_main_view(self) -> None:
        context = self._nav_rail.current_context()
        if context in ("Assets", "Shots"):
            self._pull_browser_filters_from_sidebar()
        if context == "Dashboard" and self._dashboard_page_widget is not None:
            self._dashboard_page_widget.set_project_root(self._project_root)
            self._dashboard_page_widget.set_workspace_root(self._workspace_root)
            self._schedule_dashboard_refresh()
            return
        if context == "Schedule" and self._schedule_page_widget is not None:
            self._schedule_page_widget.set_project_root(self._project_root)
            self._schedule_page_widget.refresh(self._project_index)
            self._apply_schedule_sidebar_filters()
            key = self._schedule_entity_key_from_view_item(self._schedule_inspector_item)
            if key is not None:
                self._schedule_inspector_item = self._view_item_for_schedule_entity(*key)
            self._refresh_inspector_selection()
            return
        if context == "Trash" and self._trash_page_widget is not None:
            self._trash_page_widget.set_project_root(self._project_root)
            return
        if context == "Inbox" and self._inbox_page_widget is not None:
            self._inbox_page_widget.set_project_root(self._project_root)
            self._inbox_page_widget.set_type_filter(self._filter_panel.filters().current_type() or "")
            return
        if context in ("Delivery", "Outbox") and self._outbox_page_widget is not None:
            self._outbox_page_widget.set_project_root(self._project_root)
            self._outbox_page_widget.set_type_filter(self._filter_panel.filters().current_type() or "")
            return
        if context == SidebarContext.INTERNAL_CHECK.value and self._internal_check_page_widget is not None:
            self._internal_check_page_widget.set_project_root(self._project_root)
            return
        if context == "Project Guide" and self._reference_page_widget is not None:
            dept = self._filter_panel.filters().current_department() or "reference"
            self._reference_page_widget.set_project_root(self._project_root)
            self._reference_page_widget.set_department(dept)
            self._filter_panel.filters().sync_tags_for_department(dept, emit_filter=False)
            self._filter_panel.filters().set_tag_item_tags(self._reference_page_widget.get_item_tags())
            self._reference_page_widget.set_tag_filter(self._filter_panel.filters().current_tags())
            self._update_reference_tag_badge()
            return
        # Placeholder for search input (context-aware).
        placeholders = {"Assets": "Search assets", "Shots": "Search shots"}
        self._main_view.set_search_placeholder(placeholders.get(context, "Search…"))
        items: list[ViewItem] = []

        if self._project_index is None:
            # Keep whatever is currently shown (avoid "click -> empty list") while scan/index is pending.
            self._main_view.set_empty_override(SCANNING_EMPTY_MESSAGE)
            if context in ("Assets", "Shots"):
                self._sync_main_view_header()
            return
        else:
            # Clear any loading override once we have an index.
            self._main_view.set_empty_override(None)

        if context == "Assets":
            # Render from AppState; filter and build ViewItems (used for initial load and on filtersChanged).
            assets_ordered = self._app_state.get_assets_in_order()
            filtered_assets = self.filter_assets(
                assets_ordered,
                self.current_department,
                self.current_type,
            )
            type_reg = TypeRegistry.for_project(self._project_root) if self._project_root else None
            for asset in filtered_assets:
                type_folder = (type_reg.get_type_folder(asset.asset_type) or "").strip() if type_reg else ""
                items.append(
                    ViewItem(
                        kind=ViewItemKind.ASSET,
                        name=asset.name,
                        type_badge=asset.asset_type,
                        path=asset.path,
                        departments_count=len(asset.departments),
                        ref=asset,
                        type_folder=type_folder,
                    )
                )
        elif context == "Shots":
            for shot in self._app_state.get_shots_in_order():
                items.append(
                    ViewItem(
                        kind=ViewItemKind.SHOT,
                        name=shot.name,
                        type_badge="shot",
                        path=shot.path,
                        departments_count=len(shot.departments),
                        ref=shot,
                    )
                )
        else:
            self._main_view.clear()
            self._main_view.set_empty_override(self._empty_message_for_context(context))
            return

        items = self._apply_search_filter(items, self.current_search_query)
        if self._project_root is not None:
            if not items and (self.current_search_query or "").strip():
                self._main_view.set_empty_override('No matches for "' + self.current_search_query.strip() + '"')
            else:
                self._main_view.set_empty_override(None)
        if context in ("Assets", "Shots"):
            self._sync_main_view_header()
            self._inspector.set_show_publish(self._main_view.get_show_publish())
        else:
            self._main_view.set_active_department(None)
            self._main_view.set_selected_asset_type(None)
        # Preserve selection across department changes within the same type when the item is visible.
        candidate_ids: list[str] = []
        recalled = self._recall_main_view_selection()
        current_id = self._app_state.selection_id()
        for cid in (recalled, current_id):
            if cid and cid not in candidate_ids:
                candidate_ids.append(cid)
        preserve = None
        for cid in candidate_ids:
            if not cid or not items:
                continue
            for vi in items:
                if self._path_matches_selection(vi.path, cid):
                    preserve = cid
                    break
            if preserve:
                break
        self._main_view.set_items(items, preserve_selection_id=preserve)
        if preserve is None and (recalled or current_id):
            self._restore_main_view_selection_from_recall()
        self._filter_panel.set_project_index(self._project_index)
        if preserve is not None:
            if self._app_state.selection_id() != preserve:
                self._app_state.set_selection(preserve)
        elif not recalled and not current_id:
            self._app_state.set_selection(None)

    def _on_item_activated(self, item: ViewItem) -> None:
        if getattr(self, "_context_switch_in_progress", False) or getattr(self, "_filter_switch_in_progress", False):
            return
        # NOTE: "Enter departments" navigation has been removed.
        # Double click / Enter will be repurposed by a different function later.
        if item.kind == ViewItemKind.PROJECT:
            # Explicit action: open/switch project by double-clicking a project card.
            self._switch_project(str(item.path))
            return
        if item.kind == ViewItemKind.ASSET and isinstance(item.ref, Asset):
            try:
                if self._controller.smart_open(item=item.ref, force_dialog=False, parent=self):
                    self._refresh_recent_tasks()
                    notification_service.success(f"Opened Asset '{item.ref.name}'.")
            except Exception as e:
                logging.warning("DCC launch failed (asset): %s", e, exc_info=True)
                QMessageBox.critical(self, "Open DCC", str(e))
            return
        if item.kind == ViewItemKind.SHOT and isinstance(item.ref, Shot):
            try:
                if self._controller.smart_open(item=item.ref, force_dialog=False, parent=self):
                    self._refresh_recent_tasks()
                    notification_service.success(f"Opened Shot '{item.ref.name}'.")
            except Exception as e:
                logging.warning("DCC launch failed (shot): %s", e, exc_info=True)
                QMessageBox.critical(self, "Open DCC", str(e))
            return
        return

    def _inbox_date_folder_settings_key(self, source_type: str) -> str:
        key = (source_type or "client").strip().lower()
        if key not in ("client", "freelancer"):
            key = "client"
        return f"inbox/last_date_folder_path/{key}"

    def _inbox_restore_split_key(self) -> str:
        return "inbox/restore_split_view"

    def _on_inbox_date_folder_entered(self, source_type: str, path: Path) -> None:
        """Persist which date folder is open per Client/Freelancer so we can restore when switching back."""
        self._save_inbox_date_folder_state(source_type, path)

    def _save_inbox_date_folder_state(self, source_type: str, path: Path) -> None:
        if not path or not path.is_dir() or not self._project_root:
            return
        if not str(path).startswith(str(self._project_root)):
            return
        key = self._inbox_date_folder_settings_key(source_type)
        self._settings.setValue(key, str(path.resolve()))

    def _restore_inbox_date_folder_state(self) -> bool:
        """Restore Inbox date folder (tree) for current type when user had it open. Returns True if restored."""
        if not self._inbox_page_widget or not self._project_root:
            return False
        raw = self._settings.value(self._inbox_restore_split_key(), True)
        if raw in (False, "false", "0", 0):
            return False
        # Restore expanded tree / last browse folder for this source type.
        source_type = (self._filter_panel.filters().current_type() or "client").strip().lower()
        if source_type not in ("client", "freelancer"):
            source_type = "client"
        path_str = self._settings.value(self._inbox_date_folder_settings_key(source_type), "", str)
        if not path_str:
            return False
        path = Path(path_str)
        if not path.is_dir():
            return False
        try:
            if not str(path.resolve()).startswith(str(self._project_root.resolve())):
                return False
        except OSError:
            return False
        self._inbox_page_widget.restore_browse_path(path)
        return True

    def _outbox_date_folder_settings_key(self, source_type: str) -> str:
        key = (source_type or "client").strip().lower()
        if key not in ("client", "freelancer"):
            key = "client"
        return f"delivery/last_date_folder_path/{key}"

    def _outbox_restore_split_key(self) -> str:
        return "delivery/restore_split_view"

    def _internal_check_date_folder_settings_key(self) -> str:
        return "internal_check/last_date_folder_path"

    def _internal_check_restore_split_key(self) -> str:
        return "internal_check/restore_split_view"

    def _on_internal_check_date_folder_entered(self, _source_type: str, path: Path) -> None:
        self._save_internal_check_date_folder_state(path)

    def _save_internal_check_date_folder_state(self, path: Path) -> None:
        if not path or not path.is_dir() or not self._project_root:
            return
        if not str(path).startswith(str(self._project_root)):
            return
        self._settings.setValue(self._internal_check_date_folder_settings_key(), str(path.resolve()))

    def _restore_internal_check_date_folder_state(self) -> bool:
        if not self._internal_check_page_widget or not self._project_root:
            return False
        raw = self._settings.value(self._internal_check_restore_split_key(), True)
        if raw in (False, "false", "0", 0):
            raw = self._settings.value("review/restore_split_view", True)
        if raw in (False, "false", "0", 0):
            return False
        path_str = self._settings.value(self._internal_check_date_folder_settings_key(), "", str)
        if not path_str:
            path_str = self._settings.value("review/last_date_folder_path", "", str)
        path = Path(path_str)
        if not path.is_dir():
            return False
        try:
            if not str(path.resolve()).startswith(str(self._project_root.resolve())):
                return False
        except OSError:
            return False
        self._internal_check_page_widget.restore_browse_path(path)
        return True

    def _on_outbox_date_folder_entered(self, source_type: str, path: Path) -> None:
        """Persist which date folder is open per Client/Freelancer so we can restore when switching back."""
        self._save_outbox_date_folder_state(source_type, path)

    def _save_outbox_date_folder_state(self, source_type: str, path: Path) -> None:
        if not path or not path.is_dir() or not self._project_root:
            return
        if not str(path).startswith(str(self._project_root)):
            return
        key = self._outbox_date_folder_settings_key(source_type)
        self._settings.setValue(key, str(path.resolve()))

    def _restore_outbox_date_folder_state(self) -> bool:
        """Restore Delivery date folder (tree) for current recipient when user had it open."""
        if not self._outbox_page_widget or not self._project_root:
            return False
        raw = self._settings.value(self._outbox_restore_split_key(), True)
        if raw in (False, "false", "0", 0):
            return False
        source_type = (self._filter_panel.filters().current_type() or "client").strip().lower()
        if source_type not in ("client", "freelancer"):
            source_type = "client"
        path_str = self._settings.value(self._outbox_date_folder_settings_key(source_type), "", str)
        if not path_str:
            return False
        path = Path(path_str)
        if not path.is_dir():
            return False
        try:
            if not str(path.resolve()).startswith(str(self._project_root.resolve())):
                return False
        except OSError:
            return False
        self._outbox_page_widget.restore_browse_path(path)
        return True

    def _project_guide_browse_settings_key(self, department: str) -> str:
        from monostudio.ui_qt.reference_page_widget import PROJECT_GUIDE_DEPARTMENTS

        dept = (department or PROJECT_GUIDE_DEPARTMENTS[0]).strip().lower()
        if dept not in PROJECT_GUIDE_DEPARTMENTS:
            dept = PROJECT_GUIDE_DEPARTMENTS[0]
        return f"project_guide/last_browse_path/{dept}"

    def _on_project_guide_browse_path_changed(self, department: str, path: Path) -> None:
        """Persist browse folder per Project Guide department so we can restore when switching back."""
        self._save_project_guide_browse_state(department, path)

    def _save_project_guide_browse_state(self, department: str, path: Path) -> None:
        if not path or not path.is_dir() or not self._project_root:
            return
        if not str(path).startswith(str(self._project_root)):
            return
        key = self._project_guide_browse_settings_key(department)
        self._settings.setValue(key, str(path.resolve()))

    def _restore_project_guide_browse_state(self) -> bool:
        """Restore Project Guide browse folder for current department. Returns True if restored."""
        if not self._reference_page_widget or not self._project_root:
            return False
        department = self._filter_panel.filters().current_department() or "reference"
        path_str = self._settings.value(self._project_guide_browse_settings_key(department), "", str)
        if not path_str:
            return False
        path = Path(path_str)
        if not path.is_dir():
            return False
        try:
            if not str(path.resolve()).startswith(str(self._project_root.resolve())):
                return False
        except OSError:
            return False
        self._reference_page_widget.restore_browse_path(path)
        return True

    def _release_video_preview(self) -> None:
        dlg = self._alive_review_player()
        if dlg is None:
            return
        try:
            dlg.release_player()
            dlg.close()
        except Exception:
            pass
        self._review_player_dialog = None

    def _open_video_preview_from_inbox(self, path) -> None:
        p = Path(path) if path is not None else None
        if p is None or not is_video_path(p):
            return
        self._open_video_preview_with_request(
            VideoPreviewOpenRequest(path=p, context=PreviewContext.inbox, sibling_paths=list_video_siblings(p))
        )

    def _open_video_preview_from_internal_check(self, path) -> None:
        p = Path(path) if path is not None else None
        if p is None or not is_video_path(p):
            return
        self._open_video_preview_with_request(
            VideoPreviewOpenRequest(
                path=p,
                context=PreviewContext.internal_check,
                sibling_paths=list_video_siblings(p),
            )
        )

    def _open_video_preview_from_delivery(self, path) -> None:
        p = Path(path) if path is not None else None
        if p is None or not is_video_path(p):
            return
        self._open_video_preview_with_request(
            VideoPreviewOpenRequest(
                path=p,
                context=PreviewContext.delivery,
                sibling_paths=list_video_siblings(p),
            )
        )

    def _open_video_preview_from_project_guide(self, path) -> None:
        p = Path(path) if path is not None else None
        if p is None or not is_video_path(p):
            return
        self._open_video_preview_with_request(
            VideoPreviewOpenRequest(
                path=p,
                context=PreviewContext.project_guide,
                sibling_paths=list_video_siblings(p),
            )
        )

    def _open_video_preview_from_inspector(self, path) -> None:
        p = Path(path) if path is not None else None
        if p is None or not is_video_path(p):
            return
        entity_path = None
        dept_id = None
        try:
            item = getattr(self._inspector, "_current_item", None)
            if item is not None and getattr(item, "path", None):
                entity_path = Path(item.path)
            dept_id = getattr(self._inspector, "_last_focused_department", None)
        except Exception:
            pass
        self._open_video_preview_with_request(
            VideoPreviewOpenRequest(
                path=p,
                context=PreviewContext.entity,
                sibling_paths=list_video_siblings(p),
                entity_path=entity_path,
                department_id=dept_id,
            )
        )

    def _department_display_label(self, department_id: str | None) -> str | None:
        dep = (department_id or "").strip()
        if not dep:
            return None
        resolver = getattr(self._inspector, "_department_label_resolver", None)
        if callable(resolver):
            try:
                lab = resolver(dep)
            except Exception:
                lab = None
            if lab:
                return str(lab).strip() or None
        return dep.replace("_", " ").title()

    def _on_review_entity_requested(self, item: object) -> None:
        from monostudio.core.review_media import ReviewResolveAction, resolve_entity_review_media
        from monostudio.ui_qt.inspector_preview_settings import read_sequence_preview_fps
        from monostudio.ui_qt.thumbnail_source_resolve import (
            primary_work_file_for_department,
            resolve_department_work_path_for_preview,
        )
        from monostudio.ui_qt.thumbnails import resolve_thumbnail_path

        if not isinstance(item, ViewItem) or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        ref = getattr(item, "ref", None)
        if not isinstance(ref, (Asset, Shot)):
            return
        dep = (self._main_view._active_department or "").strip()
        if not dep:
            return
        active_dcc = self._main_view.get_active_dcc(item.path, dep)
        wf = primary_work_file_for_department(ref, dep, active_dcc)
        wp = resolve_department_work_path_for_preview(
            ref,
            dep,
            work_file_path=wf,
            item_root=Path(item.path),
            active_dcc_id=active_dcc,
        )
        thumb = resolve_thumbnail_path(Path(item.path), department=dep)
        fps = read_sequence_preview_fps(self._settings)
        resolved = resolve_entity_review_media(
            thumb_path=thumb,
            work_path=wp,
            work_file_path=wf,
            sequence_frames=None,
            sequence_folder=None,
            fps=fps,
            context=PreviewContext.entity,
            entity_path=Path(item.path),
            department_id=dep,
            department_label=self._department_display_label(dep),
        )
        if resolved.action == ReviewResolveAction.open_player and resolved.request is not None:
            self._open_review_player(resolved.request)

    def _review_player_matches_entity(
        self,
        dlg: VideoPreviewDialog,
        entity_path: Path | None,
        department_id: str | None,
    ) -> bool:
        if entity_path is None:
            return False
        ep = getattr(dlg, "_entity_path", None)
        if ep is None:
            return False
        try:
            if Path(ep).resolve() != Path(entity_path).resolve():
                return False
        except OSError:
            return False
        dep_a = (getattr(dlg, "_department_id", None) or "").strip()
        dep_b = (department_id or "").strip()
        return dep_a == dep_b

    def _resolve_review_request_for_entity_path(
        self,
        entity_path: Path,
        *,
        department_id: str | None,
        department_label: str | None,
    ) -> ReviewOpenRequest | None:
        from monostudio.core.review_media import ReviewResolveAction, resolve_entity_review_media
        from monostudio.ui_qt.inspector_preview_settings import read_sequence_preview_fps
        from monostudio.ui_qt.thumbnail_source_resolve import (
            primary_work_file_for_department,
            resolve_department_work_path_for_preview,
        )
        from monostudio.ui_qt.thumbnails import resolve_thumbnail_path

        dep = (department_id or "").strip()
        if not dep:
            dep = (getattr(self._inspector, "_last_focused_department", None) or "").strip()
        if not dep:
            dep, _ = self._notes_department_for_dialog()
        if not dep:
            return None

        ref = self._pipeline_ref_for_path(entity_path, "asset")
        if ref is None:
            ref = self._pipeline_ref_for_path(entity_path, "shot")
        if ref is None:
            return None

        active_dcc = self._main_view.get_active_dcc(str(entity_path), dep)
        wf = primary_work_file_for_department(ref, dep, active_dcc)
        wp = resolve_department_work_path_for_preview(
            ref,
            dep,
            work_file_path=wf,
            item_root=entity_path,
            active_dcc_id=active_dcc,
        )
        thumb = resolve_thumbnail_path(entity_path, department=dep)
        fps = read_sequence_preview_fps(self._settings)
        label = (department_label or "").strip() or self._department_display_label(dep) or dep
        resolved = resolve_entity_review_media(
            thumb_path=thumb,
            work_path=wp,
            work_file_path=wf,
            sequence_frames=None,
            sequence_folder=None,
            fps=fps,
            context=PreviewContext.entity,
            entity_path=entity_path,
            department_id=dep,
            department_label=label,
        )
        if resolved.action == ReviewResolveAction.open_player and resolved.request is not None:
            return resolved.request
        return None

    def jump_to_note_time_anchor(
        self,
        *,
        entity_path: Path,
        department_id: str | None = None,
        department_label: str | None = None,
        href: str,
    ) -> None:
        from monostudio.core.note_time_anchors import parse_time_href

        if parse_time_href(href) is None:
            return

        dep = (department_id or "").strip() or None
        existing = self._alive_review_player()
        if existing is not None and self._review_player_matches_entity(existing, entity_path, dep):
            existing.apply_time_anchor(href)
            self._bring_review_player_to_front(existing)
            return

        request = self._resolve_review_request_for_entity_path(
            entity_path,
            department_id=dep,
            department_label=department_label,
        )
        if request is None:
            notification_service.warning("No review video found for this department.")
            return
        self._open_review_player(request, pending_time_anchor=href)

    def _alive_review_player(self) -> VideoPreviewDialog | None:
        dlg = self._review_player_dialog
        if dlg is None:
            return None
        try:
            from shiboken6 import isValid

            if not isValid(dlg):
                self._review_player_dialog = None
                return None
        except Exception:
            self._review_player_dialog = None
            return None
        return dlg

    def _open_review_player(self, request: object, *, pending_time_anchor: str | None = None) -> None:
        if isinstance(request, VideoPreviewOpenRequest):
            request = request.to_review_request()
        if not isinstance(request, ReviewOpenRequest):
            return
        href = (pending_time_anchor or "").strip() or None
        if href:
            existing = self._alive_review_player()
            if (
                existing is not None
                and existing.isVisible()
                and self._review_player_matches_entity(
                    existing,
                    request.entity_path,
                    request.department_id,
                )
            ):
                existing.apply_time_anchor(href)
                self._bring_review_player_to_front(existing)
                return
        existing = self._alive_review_player()
        if existing is not None and existing.isVisible():
            try:
                existing.release_player()
                existing.close()
            except Exception:
                pass
        path_arg = request.path if request.media_kind.value == "video" else None
        dlg = VideoPreviewDialog(
            path_arg,
            request=request,
            settings=self._settings,
            parent=None,
            geometry_anchor=self,
        )
        if href:
            dlg.set_pending_time_anchor(href)
        self._review_player_dialog = dlg
        dlg.destroyed.connect(lambda *_: setattr(self, "_review_player_dialog", None))
        dlg.closed.connect(self._on_review_player_closed)
        dlg.export_completed.connect(self._on_video_export_completed)
        dlg.open_all_notes_requested.connect(self._on_video_preview_open_all_notes)
        dlg.notes_changed.connect(lambda: self._on_review_player_notes_changed())
        dlg.show()
        self._bring_review_player_to_front(dlg)

    def _bring_review_player_to_front(self, dlg: VideoPreviewDialog | None = None) -> None:
        player = dlg if dlg is not None else self._alive_review_player()
        if player is None:
            return
        player.showNormal()
        player.raise_()
        player.activateWindow()
        QTimer.singleShot(0, lambda p=player: self._raise_review_player_once(p))
        QTimer.singleShot(80, lambda p=player: self._raise_review_player_once(p))

    def _raise_review_player_once(self, player: VideoPreviewDialog) -> None:
        try:
            from shiboken6 import isValid

            if not isValid(player) or not player.isVisible():
                return
        except Exception:
            return
        player.raise_()
        player.activateWindow()
        wh = player.windowHandle()
        if wh is not None:
            wh.requestActivate()
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(player.winId())
                if hwnd:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        bring = getattr(player, "_raise_video_chrome_overlays", None)
        if callable(bring):
            bring()

    def _item_notes_dialog_awaiting_restore(self) -> bool:
        from monostudio.ui_qt.item_notes_dialog import ItemNotesDialog

        app = QApplication.instance()
        if app is None:
            return False
        for w in app.topLevelWidgets():
            if isinstance(w, ItemNotesDialog) and getattr(w, "_hidden_for_review_jump", False):
                return True
        return False

    def _on_review_player_closed(self) -> None:
        self._review_player_dialog = None
        from monostudio.ui_qt.style import release_stuck_mouse_grab, resync_hover_at_cursor

        release_stuck_mouse_grab(force=True)
        if not self._item_notes_dialog_awaiting_restore():
            self.activateWindow()
            self.raise_()
        QTimer.singleShot(0, resync_hover_at_cursor)

    def _open_video_preview_with_request(self, request: VideoPreviewOpenRequest) -> None:
        self._open_review_player(request.to_review_request())

    def _on_video_preview_open_all_notes(self) -> None:
        item = getattr(self._inspector, "_current_item", None)
        if item is None:
            return
        self._on_item_notes_dialog_requested(item)

    def _open_sequence_preview(self, request: object) -> None:
        """Legacy entry — redirects to unified review player."""
        from monostudio.ui_qt.video_preview_context import ReviewMediaKind

        if hasattr(request, "frames") and hasattr(request, "sequence_folder"):
            rev = ReviewOpenRequest(
                media_kind=ReviewMediaKind.sequence,
                context=PreviewContext.entity,
                frames=list(getattr(request, "frames", []) or []),
                sequence_folder=getattr(request, "sequence_folder"),
                fps=int(getattr(request, "fps", 24) or 24),
                entity_path=getattr(request, "entity_path", None),
            )
            self._open_review_player(rev)

    def _open_video_preview(self, path) -> None:
        """Legacy entry — treat as entity context."""
        p = Path(path) if path is not None else None
        if p is None or not is_video_path(p):
            return
        self._open_video_preview_with_request(
            VideoPreviewOpenRequest(
                path=p,
                context=PreviewContext.entity,
                sibling_paths=list_video_siblings(p),
            )
        )

    def _on_video_export_completed(self, outputs) -> None:
        """Refresh thumbnails for exported clips written inside the open project."""
        if not outputs:
            return
        try:
            paths = [Path(p) for p in outputs]
        except Exception:
            return
        root = getattr(self, "_project_root", None)
        in_project = paths_under_project_root(paths, root)
        if not in_project:
            return
        mgr = getattr(self, "_thumbnail_manager", None)
        for p in in_project:
            if mgr is not None:
                try:
                    mgr.invalidate_file(p)
                except Exception:
                    pass
            try:
                self._app_state.invalidate_thumbnails([str(p)])
            except Exception:
                pass
        # New files under Project Guide / Inbox trees — best-effort refresh.
        try:
            if self._reference_page_widget is not None:
                self._reference_page_widget.refresh_tree()
        except Exception:
            pass
        try:
            if self._inbox_page_widget is not None:
                self._inbox_page_widget.refresh_tree()
        except Exception:
            pass

    def _on_inbox_tree_selection_changed(self, path) -> None:
        self._inspector.set_inbox_tree_preview(Path(path) if path else None)

    def _on_tag_filter_changed(self, tag_ids) -> None:
        """Sidebar tag filter changed: forward to Project Guide tree proxy."""
        if self._reference_page_widget is not None:
            ids = list(tag_ids) if tag_ids else []
            self._reference_page_widget.set_tag_filter(ids)
            self._update_reference_tag_badge()

    def _on_tag_definitions_changed(self) -> None:
        """Tag definitions were modified (add/rename/delete/recolor). Reload on tree pane."""
        if self._reference_page_widget is not None:
            self._reference_page_widget.reload_tag_definitions()
            self._update_reference_tag_badge()

    def _on_reference_tree_selection_changed(self, path) -> None:
        """Reference page: show file preview in inspector (same as Inbox tree selection)."""
        self._inspector.set_inbox_tree_preview(Path(path) if path else None)

    def _on_reference_item_tags_changed(self) -> None:
        """Tags were updated in Project Guide tree; refresh sidebar tag counts."""
        if self._reference_page_widget is not None:
            tags = self._reference_page_widget.get_item_tags()
            self._filter_panel.filters().set_tag_item_tags(tags)
            self._reference_page_widget.set_tag_data(tags)

    def _on_reference_import_requested(self) -> None:
        """Import (header or context menu): open file dialog, then copy to project_guide/<current_department>/."""
        if not self._project_root:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Import to Project Guide", "", "All Files (*)")
        if not files:
            return
        path_list = [Path(f) for f in files if f and Path(f).exists()]
        if not path_list:
            return
        self._on_reference_drop_requested(path_list)

    def _on_reference_drop_requested(
        self, paths: list, target_folder=None, copy_only: bool = True
    ) -> None:
        """Drop onto Project Guide explorer: copy/move into tree target or current department folder."""
        logging.debug(
            "Project Guide _on_reference_drop_requested: paths=%s target=%s copy_only=%s",
            [str(p) for p in (paths or [])],
            target_folder,
            copy_only,
        )
        if not paths or not self._project_root:
            return
        path_list = [Path(p) for p in paths if p and Path(p).exists()]
        if not path_list:
            return
        pane = getattr(self._reference_page_widget, "_tree_pane", None)
        dest = pane.resolve_drop_dest_dir(target_folder) if pane is not None and target_folder else None
        if dest is None:
            dept = (self._filter_panel.filters().current_department() or "reference").strip().lower()
            struct_reg = StructureRegistry.for_project(self._project_root)
            guide_folder = struct_reg.get_folder("project_guide")
            dest = Path(self._project_root) / guide_folder / dept
            dest.mkdir(parents=True, exist_ok=True)
        if not dest.is_dir():
            return
        if not copy_only:
            # Release inspector preview (video/thumb handles) before in-place moves.
            self._release_video_preview()
            self._inspector.set_inbox_tree_preview(None)
            QApplication.processEvents()
        added = 0
        dest_paths: list[Path] = []
        failed = 0
        verb = "copied" if copy_only else "moved"
        for src in path_list:
            try:
                target_path = dest / src.name
                try:
                    if src.resolve() == target_path.resolve():
                        continue
                except OSError:
                    if src == target_path:
                        continue
                if copy_only:
                    if src.is_dir():
                        if target_path.exists():
                            for item in src.iterdir():
                                child = target_path / item.name
                                if item.is_file():
                                    shutil.copy2(item, child)
                                else:
                                    shutil.copytree(item, child, dirs_exist_ok=True)
                        else:
                            shutil.copytree(src, target_path)
                    else:
                        shutil.copy2(src, target_path)
                else:
                    if target_path.exists() and not (
                        src.exists() and src.resolve() == target_path.resolve()
                    ):
                        if target_path.is_dir():
                            shutil.rmtree(target_path)
                        else:
                            target_path.unlink()
                    move_path(src, target_path)
                added += 1
                dest_paths.append(target_path)
            except OSError as e:
                failed += 1
                logging.warning("Project Guide drop failed for %s: %s", src, e)
        if added > 0:
            if self._reference_page_widget is not None:
                self._reference_page_widget.refresh_tree()
                if pane is not None and dest_paths:
                    pane.select_dropped_paths(dest_paths)
            notification_service.success(
                f"{'Added' if copy_only else 'Moved'} {added} item{'s' if added != 1 else ''} to Project Guide."
            )
        elif failed > 0:
            notification_service.warning(
                "Could not move — file may be open in another app. Close the preview/player and try again."
            )

    def _open_path_in_explorer(self, path: object) -> None:
        if not path:
            return
        try:
            shell_open_folder(Path(path))
        except (OSError, TypeError, ValueError):
            pass

    def _on_reference_open_folder_requested(self, path) -> None:
        self._open_path_in_explorer(path)

    def _on_inbox_open_folder_requested(self, path) -> None:
        self._open_path_in_explorer(path)

    def _on_outbox_tree_distribute_paths_changed(self, paths: list) -> None:
        """Delivery: inspector preview only (no distribute block)."""
        path_list = [Path(p) for p in paths if p] if paths else []
        self._inspector.set_inbox_distribute_paths([], None, None)
        self._inspector.set_inbox_tree_preview(path_list[0] if path_list else None)

    def _on_outbox_open_folder_requested(self, path) -> None:
        self._open_path_in_explorer(path)

    def _on_internal_check_tree_distribute_paths_changed(self, paths: list) -> None:
        """Internal check: inspector preview only (no distribute block)."""
        path_list = [Path(p) for p in paths if p] if paths else []
        self._inspector.set_inbox_distribute_paths([], None, None)
        self._inspector.set_inbox_tree_preview(path_list[0] if path_list else None)

    def _on_internal_check_open_folder_requested(self, path) -> None:
        self._open_path_in_explorer(path)

    def _on_internal_check_import_requested(self, _date_path=None) -> None:
        if not self._project_root:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Add to Internal check", "", "All Files (*)")
        if not files:
            return
        path_list = [Path(f) for f in files if f and Path(f).exists()]
        if not path_list:
            return
        self._on_internal_check_drop_requested(path_list)

    def _on_internal_check_drop_requested(self, paths: list, target_folder=None, copy_only: bool = True) -> None:
        if not paths or not self._project_root:
            return
        try:
            path_list = [Path(p) for p in paths if p and Path(p).exists()]
            if not path_list:
                return
            if self._try_direct_inbox_outbox_drop(
                path_list, target_folder, target="internal_check", copy_only=copy_only
            ):
                return
        except Exception:
            logging.getLogger(__name__).exception("Review drop failed")
            notification_service.warning("Could not add items to Internal check.")
            return
        initial_date_str, prefer_existing = self._inbox_outbox_dialog_date_defaults(
            target_folder, page_widget=self._internal_check_page_widget
        )
        dialog = InboxDropDialog(
            path_list,
            self._project_root,
            "",
            self,
            target="internal_check",
            initial_date_str=initial_date_str,
            prefer_existing_date=prefer_existing,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        _source, date_str, description = dialog.result_values()
        if not date_str:
            return
        added = 0
        dest_paths: list[Path] = []
        for p in path_list:
            try:
                result = add_to_internal_check(self._project_root, p, date_str, description)
                if result is not None:
                    added += 1
                    dest_paths.append(get_internal_check_root(self._project_root) / date_str / p.name)
            except Exception as e:
                logging.warning("Add to review failed for %s: %s", p, e)
        if added > 0:
            self._reload_main_view()
            if self._internal_check_page_widget is not None:
                self._internal_check_page_widget.refresh_tree()
                self._internal_check_page_widget.refresh_history_dialog_if_open()
                pane = getattr(self._internal_check_page_widget, "_tree_pane", None)
                if pane is not None and dest_paths:
                    pane.select_dropped_paths(dest_paths)
            notification_service.success(
                f"Added {added} item{'s' if added != 1 else ''} to Internal check."
            )

    def _on_internal_check_send_to_delivery_requested(self, paths: list) -> None:
        if not paths or not self._project_root:
            return
        path_list = [Path(p) for p in paths if p and Path(p).exists()]
        if not path_list:
            return
        initial_source = (self._filter_panel.filters().current_type() or "client").strip().lower()
        if initial_source not in ("client", "freelancer"):
            initial_source = "client"
        initial_date_str, prefer_existing = self._inbox_outbox_dialog_date_defaults(
            None, page_widget=self._outbox_page_widget
        )
        dialog = InboxDropDialog(
            path_list,
            self._project_root,
            initial_source,
            self,
            target="delivery",
            initial_date_str=initial_date_str,
            prefer_existing_date=prefer_existing,
            dialog_title="Send to Delivery",
        )
        if dialog.exec() != QDialog.Accepted:
            return
        source, date_str, description = dialog.result_values()
        if not date_str:
            return
        sent = 0
        dest_paths: list[Path] = []
        for p in path_list:
            try:
                dest = send_internal_check_to_delivery(
                    self._project_root, p, source, date_str, description
                )
                if dest is not None:
                    sent += 1
                    dest_paths.append(dest)
            except Exception as e:
                logging.warning("Send to delivery failed for %s: %s", p, e)
        if sent > 0:
            self._reload_main_view()
            if self._internal_check_page_widget is not None:
                self._internal_check_page_widget.refresh_tree()
                self._internal_check_page_widget.refresh_history_dialog_if_open()
            if self._outbox_page_widget is not None:
                self._outbox_page_widget.refresh_tree()
                self._outbox_page_widget.refresh_history_dialog_if_open()
                pane = getattr(self._outbox_page_widget, "_tree_pane", None)
                if pane is not None and dest_paths:
                    pane.select_dropped_paths(dest_paths)
            notification_service.success(
                f"Sent {sent} item{'s' if sent != 1 else ''} to Delivery."
            )
        elif path_list:
            notification_service.warning("Could not send selected items to Delivery.")

    def _on_outbox_import_requested(self, _date_path=None) -> None:
        if not self._project_root:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Add to Delivery", "", "All Files (*)")
        if not files:
            return
        path_list = [Path(f) for f in files if f and Path(f).exists()]
        if not path_list:
            return
        self._on_outbox_drop_requested(path_list)

    def _on_outbox_drop_requested(self, paths: list, target_folder=None, copy_only: bool = True) -> None:
        """Files/folders dropped onto Delivery page."""
        if not paths or not self._project_root:
            return
        try:
            path_list = [Path(p) for p in paths if p and Path(p).exists()]
            if not path_list:
                return
            if self._try_direct_inbox_outbox_drop(
                path_list, target_folder, target="delivery", copy_only=copy_only
            ):
                return
        except Exception:
            logging.getLogger(__name__).exception("Delivery drop failed")
            notification_service.warning("Could not complete Delivery drop.")
            return
        initial_source = (self._filter_panel.filters().current_type() or "").strip().lower()
        if initial_source not in ("client", "freelancer"):
            initial_source = "client"
        initial_date_str, prefer_existing = self._inbox_outbox_dialog_date_defaults(
            target_folder, page_widget=self._outbox_page_widget
        )
        dialog = InboxDropDialog(
            path_list,
            self._project_root,
            initial_source,
            self,
            target="delivery",
            initial_date_str=initial_date_str,
            prefer_existing_date=prefer_existing,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        source, date_str, description = dialog.result_values()
        if not date_str:
            return
        added = 0
        added_names: list[str] = []
        dest_paths: list[Path] = []
        for p in path_list:
            try:
                result = add_to_delivery(self._project_root, p, source, date_str, description)
                if result is not None:
                    added += 1
                    added_names.append(p.name)
                    dest_paths.append(get_delivery_root(self._project_root) / source / date_str / p.name)
            except Exception as e:
                logging.warning("Add to delivery failed for %s: %s", p, e)
        if added > 0:
            self._reload_main_view()
            if self._outbox_page_widget is not None:
                self._outbox_page_widget.refresh_tree()
                self._outbox_page_widget.refresh_history_dialog_if_open()
                pane = getattr(self._outbox_page_widget, "_tree_pane", None)
                if pane is not None and dest_paths:
                    pane.select_dropped_paths(dest_paths)
            notification_service.success(f"Added {added} item{'s' if added != 1 else ''} to Delivery.")
            self._dispatch_discord_outbox_received(
                count=added,
                source=source,
                date_str=date_str,
                file_names=added_names,
            )

    def _on_inbox_tree_distribute_paths_changed(self, paths: list) -> None:
        path_list = [Path(p) for p in paths if p] if paths else []
        self._inspector.set_inbox_distribute_paths(
            path_list,
            self._project_root,
            self._project_index,
        )

    def _on_inbox_distribute_finished(self, payload: list) -> None:
        from datetime import datetime, timezone
        if not payload or not self._project_root:
            return
        type_filter = ""
        iso_now = datetime.now(timezone.utc).isoformat()
        count = 0
        dest_label = ""
        for item in payload:
            if not isinstance(item, dict):
                continue
            p = item.get("path")
            path_str = str(Path(p).resolve()) if p else ""
            if not path_str:
                continue
            if not dest_label and item.get("destination_label"):
                dest_label = (item.get("destination_label") or "").strip()
            entry = {
                "path": path_str,
                "distributed_at": iso_now,
                "destination_id": item.get("destination_id") or "",
                "destination_label": item.get("destination_label") or "",
                "scope": item.get("scope") or "",
                "entity_name": item.get("entity_name") or "",
                "target_path": item.get("target_path") or "",
            }
            append_inbox_distributed(self._project_root, type_filter, entry)
            count += 1
        if self._inbox_page_widget:
            self._inbox_page_widget.refresh_history_dialog_if_open()
        if count > 0:
            msg = f"Distributed {count} item{'s' if count != 1 else ''}"
            if dest_label:
                msg += f" to {dest_label}"
            msg += "."
            notification_service.success(msg)
            groups: dict[str, list[str]] = {}
            for item in payload:
                if not isinstance(item, dict):
                    continue
                dl = (item.get("destination_label") or "").strip() or dest_label or "pipeline"
                name = (item.get("entity_name") or "").strip()
                if not name:
                    continue
                bucket = groups.setdefault(dl, [])
                if name not in bucket:
                    bucket.append(name)
            for dl, entity_names in groups.items():
                self._dispatch_discord_inbox_distributed(
                    count=len(entity_names),
                    dest_label=dl,
                    type_filter=type_filter,
                    entity_names=entity_names,
                )

    def _dispatch_discord_inbox_distributed(
        self,
        *,
        count: int,
        dest_label: str,
        type_filter: str,
        entity_names: list[str],
    ) -> None:
        if self._workspace_root is None or self._project_root is None or count <= 0:
            return
        from monostudio.core.discord_inbox_distributed_debounce import enqueue_inbox_distributed
        from monostudio.core.user_identity import get_current_user_display_name

        enqueue_inbox_distributed(
            self._workspace_root,
            project_root=self._project_root,
            project_name=self._project_root.name,
            actor_name=get_current_user_display_name(self._workspace_root),
            source=type_filter,
            dest_label=dest_label,
            count=count,
            entity_names=entity_names,
        )

    def _maybe_discord_schedule_due(self) -> None:
        if self._workspace_root is None or self._project_root is None or self._project_index is None:
            return
        try:
            from monostudio.core.discord_schedule_due import maybe_dispatch_schedule_due

            maybe_dispatch_schedule_due(
                self._workspace_root,
                self._project_root,
                self._project_index,
            )
        except Exception:
            logging.getLogger(__name__).debug("Discord schedule_due skipped", exc_info=True)

    def _on_inbox_import_requested(self, _date_path=None) -> None:
        """Import (header or context menu): open file dialog, then InboxDropDialog."""
        if not self._project_root:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Import to Inbox", "", "All Files (*)")
        if not files:
            return
        path_list = [Path(f) for f in files if f and Path(f).exists()]
        if not path_list:
            return
        self._on_inbox_drop_requested(path_list)

    def _inbox_outbox_drop_target_at_global(self, page_widget, global_pos: QPoint, _pos_in_window: QPoint):
        pane = getattr(page_widget, "_tree_pane", None)
        if pane is None:
            return None
        return pane.drop_target_at_global_pos(global_pos)

    def _inbox_outbox_dialog_date_defaults(
        self,
        target_folder,
        *,
        page_widget,
    ) -> tuple[str | None, bool]:
        pane = getattr(page_widget, "_tree_pane", None) if page_widget is not None else None
        if pane is None:
            return None, False
        candidate = target_folder
        if candidate is None:
            try:
                candidate = pane.current_browse_path()
            except Exception:
                candidate = None
        dest = pane.resolve_drop_dest_dir(candidate) if candidate is not None else None
        if dest is None:
            return None, False
        try:
            rel = dest.relative_to(pane._source_tree_root())
        except (ValueError, OSError, AttributeError):
            return None, False
        if not rel.parts:
            return None, False
        return rel.parts[0], True

    def _inbox_outbox_page_for_target(self, target: str):
        key = (target or "inbox").strip().lower()
        if key == "outbox":
            key = "delivery"
        if key == "review":
            key = "internal_check"
        if key == "inbox":
            return self._inbox_page_widget
        if key == "internal_check":
            return self._internal_check_page_widget
        if key == "delivery":
            return self._outbox_page_widget
        return self._inbox_page_widget

    def _inbox_outbox_label_for_target(self, target: str) -> str:
        key = (target or "inbox").strip().lower()
        if key == "outbox":
            key = "delivery"
        if key == "review":
            key = "internal_check"
        labels = {"inbox": "Inbox", "delivery": "Delivery", "internal_check": "Internal check"}
        return labels.get(key, "Inbox")

    def _inbox_outbox_root_for_target(self, target: str) -> Path:
        key = (target or "inbox").strip().lower()
        if key == "outbox":
            key = "delivery"
        if key == "review":
            key = "internal_check"
        if key == "inbox":
            return get_inbox_root(self._project_root)
        if key == "internal_check":
            return get_internal_check_root(self._project_root)
        if key == "delivery":
            return get_delivery_root(self._project_root)
        return get_inbox_root(self._project_root)

    def _try_direct_inbox_outbox_drop(
        self,
        path_list: list[Path],
        target_folder,
        *,
        target: str,
        copy_only: bool = True,
    ) -> bool:
        page = self._inbox_outbox_page_for_target(target)
        pane = getattr(page, "_tree_pane", None) if page is not None else None
        if pane is None or target_folder is None:
            return False
        dest_dir = pane.resolve_drop_dest_dir(target_folder)
        if dest_dir is None:
            return False
        copy_fns = {
            "inbox": copy_into_inbox_folder,
            "delivery": copy_into_delivery_folder,
            "internal_check": copy_into_internal_check_folder,
        }
        move_fns = {
            "inbox": move_into_inbox_folder,
            "delivery": move_into_delivery_folder,
            "internal_check": move_into_internal_check_folder,
        }
        key = (target or "inbox").strip().lower()
        copy_fn = copy_fns.get(key, copy_into_inbox_folder)
        move_fn = move_fns.get(key, move_into_inbox_folder)
        op_fn = copy_fn if copy_only else move_fn
        verb = "copied" if copy_only else "moved"
        added = 0
        added_names: list[str] = []
        dest_paths: list[Path] = []
        duplicate_count = 0
        for p in path_list:
            try:
                if op_fn(self._project_root, p, dest_dir):
                    added += 1
                    added_names.append(p.name)
                    dest_paths.append(dest_dir / p.name)
                elif (dest_dir / p.name).exists():
                    duplicate_count += 1
            except Exception as e:
                logging.warning("Direct %s drop failed for %s: %s", target, p, e)
        if added <= 0:
            label = self._inbox_outbox_label_for_target(target)
            if duplicate_count > 0:
                folder_label = dest_dir.name
                if duplicate_count == len(path_list):
                    notification_service.warning(
                        f"Already in {label} / {folder_label} — nothing was {verb}."
                    )
                else:
                    notification_service.warning(
                        f"Some items already exist in {label} / {folder_label}; nothing was {verb}."
                    )
                return True
            return False
        label = self._inbox_outbox_label_for_target(target)
        try:
            root = self._inbox_outbox_root_for_target(target).resolve()
            rel = dest_dir.relative_to(root)
            source = rel.parts[0] if rel.parts else ""
            date_str = rel.parts[1] if len(rel.parts) > 1 else dest_dir.name
        except (ValueError, OSError):
            source = ""
            date_str = dest_dir.name
        try:
            if page is not None:
                page.refresh_tree()
                refresh_hist = getattr(page, "refresh_history_dialog_if_open", None)
                if callable(refresh_hist):
                    refresh_hist()
            if pane is not None and dest_paths:
                pane.select_dropped_paths(dest_paths)
        except Exception:
            logging.getLogger(__name__).warning(
                "Inbox/Outbox/Delivery UI refresh after drop failed", exc_info=True
            )
        folder_label = dest_dir.name
        if copy_only:
            notification_service.success(
                f"Added {added} item{'s' if added != 1 else ''} to {label} / {folder_label}."
            )
        else:
            notification_service.success(
                f"Moved {added} item{'s' if added != 1 else ''} in {label} / {folder_label}."
            )
        if copy_only:
            if target == "inbox":
                self._dispatch_discord_inbox_received(
                    count=added,
                    source=source,
                    date_str=date_str,
                    file_names=added_names,
                )
            elif target == "delivery":
                self._dispatch_discord_outbox_received(
                    count=added,
                    source=source,
                    date_str=date_str,
                    file_names=added_names,
                )
        return True

    def _on_inbox_drop_requested(self, paths: list, target_folder=None, copy_only: bool = True) -> None:
        """Files/folders dropped onto Inbox page: copy/move into tree target or open InboxDropDialog."""
        if not paths or not self._project_root:
            return
        try:
            path_list = [Path(p) for p in paths if p and Path(p).exists()]
            if not path_list:
                return
            if self._try_direct_inbox_outbox_drop(
                path_list, target_folder, target="inbox", copy_only=copy_only
            ):
                return
        except Exception:
            logging.getLogger(__name__).exception("Inbox drop failed")
            notification_service.warning("Could not complete Inbox drop.")
            return
        initial_source = (self._filter_panel.filters().current_type() or "").strip().lower()
        if initial_source not in ("client", "freelancer"):
            initial_source = "client"
        initial_date_str, prefer_existing = self._inbox_outbox_dialog_date_defaults(
            target_folder, page_widget=self._inbox_page_widget
        )
        dialog = InboxDropDialog(
            path_list,
            self._project_root,
            initial_source,
            self,
            initial_date_str=initial_date_str,
            prefer_existing_date=prefer_existing,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        source, date_str, description = dialog.result_values()
        if not date_str:
            return
        added = 0
        added_names: list[str] = []
        dest_paths: list[Path] = []
        for p in path_list:
            try:
                result = add_to_inbox(self._project_root, p, source, date_str, description)
                if result is not None:
                    added += 1
                    added_names.append(p.name)
                    dest_paths.append(get_inbox_root(self._project_root) / source / date_str / p.name)
            except Exception as e:
                logging.warning("Add to inbox failed for %s: %s", p, e)
        if added > 0:
            self._reload_main_view()
            if self._inbox_page_widget is not None:
                self._inbox_page_widget.refresh_tree()
                pane = getattr(self._inbox_page_widget, "_tree_pane", None)
                if pane is not None and dest_paths:
                    pane.select_dropped_paths(dest_paths)
            notification_service.success(f"Added {added} item{'s' if added != 1 else ''} to Inbox.")
            self._dispatch_discord_inbox_received(
                count=added,
                source=source,
                date_str=date_str,
                file_names=added_names,
            )

    def _dispatch_discord_inbox_received(
        self,
        *,
        count: int,
        source: str,
        date_str: str,
        file_names: list[str],
    ) -> None:
        if self._workspace_root is None or self._project_root is None or count <= 0:
            return
        from monostudio.core.discord_inbox_debounce import enqueue_inbox_received
        from monostudio.core.user_identity import get_current_user_display_name

        enqueue_inbox_received(
            self._workspace_root,
            project_root=self._project_root,
            project_name=self._project_root.name,
            actor_name=get_current_user_display_name(self._workspace_root),
            source=source,
            date_str=date_str,
            count=count,
            file_names=file_names,
        )

    def _dispatch_discord_outbox_received(
        self,
        *,
        count: int,
        source: str,
        date_str: str,
        file_names: list[str],
    ) -> None:
        if self._workspace_root is None or self._project_root is None or count <= 0:
            return
        from monostudio.core.discord_outbox_received_debounce import enqueue_outbox_received
        from monostudio.core.user_identity import get_current_user_display_name

        enqueue_outbox_received(
            self._workspace_root,
            project_root=self._project_root,
            project_name=self._project_root.name,
            actor_name=get_current_user_display_name(self._workspace_root),
            source=source,
            date_str=date_str,
            count=count,
            file_names=file_names,
        )

    def _on_open_requested(self, item: object) -> None:
        if getattr(self, "_context_switch_in_progress", False) or getattr(self, "_filter_switch_in_progress", False):
            return
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            if self._controller.smart_open(item=ref, force_dialog=False, parent=self):
                self._refresh_recent_tasks()
                kind_label = "Asset" if isinstance(ref, Asset) else "Shot"
                notification_service.success(f"Opened {kind_label} '{ref.name}'.")
        except Exception as e:
            logging.warning("DCC launch failed: %s", e, exc_info=True)
            QMessageBox.critical(self, "Open DCC", str(e))

    def _on_open_with_requested(self, item: object) -> None:
        if getattr(self, "_context_switch_in_progress", False) or getattr(self, "_filter_switch_in_progress", False):
            return
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            if self._controller.smart_open(item=ref, force_dialog=True, force_open_with=True, parent=self):
                self._refresh_recent_tasks()
        except Exception as e:
            logging.warning("DCC launch failed (open with): %s", e, exc_info=True)
            QMessageBox.critical(self, "Open With…", str(e))

    def _on_create_new_requested(self, item: object) -> None:
        if getattr(self, "_context_switch_in_progress", False) or getattr(self, "_filter_switch_in_progress", False):
            return
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            if self._controller.smart_open(item=ref, force_dialog=True, force_create_new=True, parent=self):
                # Repaint tile so delegate shows "Creating…" from resolve_dcc_status (pending already recorded).
                self._main_view.repaint_tiles_for_entity(str(ref.path))
                self._refresh_recent_tasks()
                kind_label = "Asset" if isinstance(ref, Asset) else "Shot"
                notification_service.success(f"Creating new work file for {kind_label} '{ref.name}'.")
            # Pending cleared when watcher triggers incremental_scan and scan finds work_file_path (or via assetsChanged).
        except Exception as e:
            logging.warning("DCC launch failed (create new): %s", e, exc_info=True)
            QMessageBox.critical(self, "Create New…", str(e))

    def _on_dcc_open_requested(self, item: object, dcc_id: str, department: str) -> None:
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            self._controller.open_with_dcc(item=ref, department=department, dcc=dcc_id, parent=self)
            self._refresh_recent_tasks()
        except Exception as e:
            logging.warning("DCC badge open failed: %s", e, exc_info=True)
            QMessageBox.critical(self, "Open DCC", str(e))

    def _on_dcc_open_version_requested(
        self, item: object, dcc_id: str, department: str, file_path: object
    ) -> None:
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        path = Path(file_path) if not isinstance(file_path, Path) else file_path
        try:
            self._controller.open_file_path_with_dcc(
                item=ref, department=department, dcc=dcc_id, file_path=path
            )
        except Exception as e:
            logging.warning("DCC open version failed: %s", e, exc_info=True)
            QMessageBox.critical(self, "Open older version", str(e))

    def _on_dcc_folder_requested(self, item: object, dcc_id: str, department: str) -> None:
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            from monostudio.core.dcc_registry import get_default_dcc_registry
            reg = get_default_dcc_registry()
            use_dcc_folders = read_use_dcc_folders(self._project_root)
            for d in ref.departments:
                if (d.name or "").strip().casefold() == department.strip().casefold():
                    work_path = resolve_work_path(d.path, dcc_id, use_dcc_folders, reg)
                    folder = work_path if work_path.is_dir() else work_path.parent
                    if folder.is_dir():
                        shell_open_folder(folder)
                    return
        except Exception as e:
            logging.warning("DCC badge open folder failed: %s", e, exc_info=True)

    def _on_dcc_copy_path_requested(self, item: object, dcc_id: str, department: str) -> None:
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            dep_norm = (department or "").strip().casefold()
            dcc_norm = (dcc_id or "").strip().casefold()
            path_to_copy: str | None = None
            for (dept_id, dc_id), state in getattr(ref, "dcc_work_states", ()) or ():
                if (dept_id or "").strip().casefold() != dep_norm or (dc_id or "").strip().casefold() != dcc_norm:
                    continue
                wp = getattr(state, "work_file_path", None)
                if isinstance(wp, Path) and wp.is_file():
                    path_to_copy = str(wp)
                    break
            if path_to_copy is None:
                from monostudio.core.dcc_registry import get_default_dcc_registry
                reg = get_default_dcc_registry()
                use_dcc_folders = read_use_dcc_folders(self._project_root)
                for d in ref.departments:
                    if (d.name or "").strip().casefold() == dep_norm:
                        work_path = resolve_work_path(d.path, dcc_id, use_dcc_folders, reg)
                        path_to_copy = str(work_path)
                        break
            if path_to_copy:
                cb = QApplication.clipboard()
                if cb:
                    cb.setText(path_to_copy)
                notification_service.success("Copied work path to clipboard.")
        except Exception as e:
            logging.warning("DCC badge copy path failed: %s", e, exc_info=True)

    def _on_dcc_delete_requested(self, item: object, dcc_id: str, department: str) -> None:
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            import shutil
            from monostudio.core.dcc_registry import get_default_dcc_registry
            reg = get_default_dcc_registry()
            info = reg.get_dcc_info(dcc_id)
            dcc_label = info.get("label", dcc_id) if isinstance(info, dict) else dcc_id
            use_dcc_folders = read_use_dcc_folders(self._project_root)
            work_path: Path | None = None
            dept_dir: Path | None = None
            for d in ref.departments:
                if (d.name or "").strip().casefold() == department.strip().casefold():
                    work_path = resolve_work_path(d.path, dcc_id, use_dcc_folders, reg)
                    dept_dir = d.path
                    break
            if work_path is None or dept_dir is None:
                return
            # Delete DCC folder (parent of work) when use_dcc_folders; else delete work folder only
            to_delete = work_path.parent if use_dcc_folders else work_path
            if not to_delete.is_dir():
                QMessageBox.information(self, f"Delete {dcc_label} folder", "Folder does not exist.")
                return
            # Build structured content: full paths for each section
            path_str = str(to_delete.resolve())
            other_in_dcc: list[str] = []
            sub_in_work: list[str] = []
            if use_dcc_folders:
                other_in_dcc = [str(p.resolve()) for p in to_delete.iterdir() if p.is_dir() and p.name != "work"]
                sub_in_work = [str(p.resolve()) for p in work_path.iterdir() if p.is_dir()] if work_path.is_dir() else []
            else:
                sub_in_work = [str(p.resolve()) for p in to_delete.iterdir() if p.is_dir()]
            intro = f"Delete the {dcc_label} folder and all its contents?" if not (other_in_dcc or sub_in_work) else ""
            if not ask_delete_folder(
                self,
                f"Delete {dcc_label} folder",
                folder_to_delete=path_str,
                other_folders=other_in_dcc if other_in_dcc else None,
                work_subfolders=sub_in_work if sub_in_work else None,
                intro_text=intro,
            ):
                return
            shutil.rmtree(to_delete, ignore_errors=True)
            notification_service.success(f"Deleted {dcc_label} folder.")
            self._reload_main_view()
        except Exception as e:
            logging.warning("DCC badge delete failed: %s", e, exc_info=True)
            QMessageBox.critical(self, "Delete folder", str(e))

    def _on_inspector_open_folder_requested(self, path_or_item: object) -> None:
        if isinstance(path_or_item, Path):
            path = path_or_item
        elif isinstance(path_or_item, ViewItem) and getattr(path_or_item, "path", None):
            path = path_or_item.path
        else:
            return
        try:
            if not path.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                return
        except (OSError, TypeError):
            return
        if path.name.lower() in ("reference", "concept"):
            try:
                self._ensure_entity_special_folders_watched(path.parent)
            except Exception:
                pass
        shell_open_folder(path)

    def _on_inspector_hidden_departments_changed(self, hidden: set) -> None:
        hidden_set = set(hidden or ())
        self._main_view.set_inspector_hidden_departments(hidden_set)
        if self._schedule_page_widget is not None:
            self._schedule_page_widget.set_inspector_hidden_departments(hidden_set)
        self._refresh_dashboard_if_visible()

    def _on_inspector_active_dcc_changed(self, path: object, department: str, dcc_id: str) -> None:
        """Đồng bộ active DCC từ Inspector sang Main View (cache + repaint)."""
        p = path if isinstance(path, Path) else (Path(str(path)) if path else None)
        if p is None or not department or not dcc_id:
            return
        self._main_view.set_active_dcc(p, department, dcc_id)

    def _on_production_status_override(self, entity_path: object, department: str, status_id: object) -> None:
        if self._project_root is None:
            return
        dep = (department or "").strip()
        if not dep:
            return

        raw_paths: list[Path]
        if isinstance(entity_path, (list, tuple)):
            raw_paths = []
            for p in entity_path:
                try:
                    raw_paths.append(Path(p))
                except (TypeError, ValueError):
                    continue
        else:
            try:
                raw_paths = [Path(entity_path)]
            except (TypeError, ValueError):
                return
        raw_paths = [p for p in raw_paths if str(p)]
        if not raw_paths:
            return

        sid: str | None
        if status_id is None:
            sid = None
        else:
            s = str(status_id).strip()
            sid = s if s else None

        updates = [(ep, dep, sid) for ep in raw_paths]
        self._apply_production_status_overrides(updates)

    def _apply_production_status_overrides(
        self,
        updates: list[tuple[Path, str, str | None]],
        *,
        show_progress: bool = False,
    ) -> None:
        if self._project_root is None or not updates:
            return

        use_async = show_progress or len(updates) >= self._PRODUCTION_STATUS_ASYNC_MIN
        if use_async:
            self._show_page_loading(self._SKIP_STATUS_LOADING_MESSAGE)
            root = self._project_root
            assets = dict(self._app_state.assets())
            shots = dict(self._app_state.shots())
            batch_updates = list(updates)

            def run() -> _ProductionStatusBatchResult:
                return run_production_status_batch(
                    root,
                    batch_updates,
                    current_assets=assets,
                    current_shots=shots,
                )

            task = WorkerTask(
                self._PRODUCTION_STATUS_BATCH_WORKER,
                run,
                manager=self._worker_manager,
            )
            self._worker_manager.submit_task(
                task,
                category=self._PRODUCTION_STATUS_BATCH_WORKER,
                replace_existing=True,
            )
            return

        result = run_production_status_batch(
            self._project_root,
            updates,
            current_assets=dict(self._app_state.assets()),
            current_shots=dict(self._app_state.shots()),
        )
        self._finish_production_status_batch(result)

    def _finish_production_status_batch(self, result: _ProductionStatusBatchResult) -> None:
        if self._project_root is None:
            return

        if not result.ok_resolved:
            if result.failed:
                msg = "\n".join(f"{fp}: {err}" for fp, err in result.failed[:5])
                if len(result.failed) > 5:
                    msg += f"\n… (+{len(result.failed) - 5} more)"
                QMessageBox.warning(self, "Production status", f"Could not save status:\n{msg}")
            return

        if result.failed:
            msg = "\n".join(f"{fp}: {err}" for fp, err in result.failed[:5])
            if len(result.failed) > 5:
                msg += f"\n… (+{len(result.failed) - 5} more)"
            QMessageBox.warning(
                self,
                "Production status",
                f"Saved {len(result.ok_resolved)} item(s); {len(result.failed)} failed:\n{msg}",
            )

        self._app_state.update_assets(result.current_assets)
        self._app_state.update_shots(result.current_shots)
        self._app_state.commit_immediate()
        if self._project_index is not None:
            ca = dict(self._app_state.assets())
            cs = dict(self._app_state.shots())
            self._project_index = ProjectIndex(
                root=self._project_index.root,
                assets=tuple(sorted(ca.values(), key=lambda x: (x.asset_type, x.name))),
                shots=tuple(sorted(cs.values(), key=lambda x: x.name)),
            )
            self._filter_panel.set_project_index(self._project_index)

        if result.schedule_touched:
            self._on_schedule_changed()

        ok_resolved = result.ok_resolved

        def _refresh_inspector() -> None:
            sel = self._main_view.selected_view_item()
            if sel is None or not sel.path:
                return
            try:
                sp = Path(sel.path).resolve()
            except OSError:
                sp = Path(sel.path)
            if str(sp) in ok_resolved:
                self._inspector.set_item(sel, active_department_hint=self.current_department)

        QTimer.singleShot(0, _refresh_inspector)
        QTimer.singleShot(0, self._main_view.repaint_tile_and_list_views)
        sw = getattr(self, "_schedule_page_widget", None)
        if sw is not None and self._project_index is not None:
            QTimer.singleShot(0, lambda: sw.refresh(self._project_index))

    def _on_schedule_department_skip_toggle(
        self,
        entity_kind: str,
        entity_rel: str,
        department: str,
        skip: bool,
    ) -> None:
        if not self._can_edit_schedule():
            return
        if self._project_root is None:
            return
        rel = (entity_rel or "").replace("\\", "/").strip()
        if not rel:
            return
        path = self._project_root / rel
        try:
            if not path.is_dir():
                return
        except OSError:
            return
        from monostudio.core.production_status import SKIPPED_STATUS_ID

        self._on_production_status_override(
            path,
            department,
            SKIPPED_STATUS_ID if skip else None,
        )

    def _on_schedule_entity_skip_toggle(
        self,
        entity_kind: str,
        entity_rel: str,
        skip: bool,
    ) -> None:
        if not self._can_edit_schedule() or self._project_root is None:
            return
        rel = (entity_rel or "").replace("\\", "/").strip()
        if not rel:
            return
        path = self._project_root / rel
        try:
            if not path.is_dir():
                return
        except OSError:
            return
        from monostudio.core.models import Asset, Shot
        from monostudio.core.production_status import SKIPPED_STATUS_ID
        from monostudio.core.project_schedule import entity_rel_path
        from monostudio.core.schedule_skip import ScheduleSkipResolver

        kind = (entity_kind or "").strip().lower()
        ref: Asset | Shot | None = None
        if self._project_index is not None:
            for asset in self._project_index.assets:
                if (
                    kind == "asset"
                    and entity_rel_path(self._project_root, asset.path).replace("\\", "/") == rel
                ):
                    ref = asset
                    break
            if ref is None:
                for shot in self._project_index.shots:
                    if (
                        kind == "shot"
                        and entity_rel_path(self._project_root, shot.path).replace("\\", "/") == rel
                    ):
                        ref = shot
                        break
        if ref is None:
            return
        resolver = ScheduleSkipResolver(self._project_root)
        dept_ids = resolver.entity_department_ids(ref)
        if not dept_ids:
            return
        sid = SKIPPED_STATUS_ID if skip else None
        self._apply_production_status_overrides(
            [(path, dep, sid) for dep in dept_ids],
            show_progress=True,
        )

    def _on_schedule_lane_skip_toggle(self, department: str, skip: bool) -> None:
        if not self._can_edit_schedule() or self._project_root is None:
            return
        dep = (department or "").strip()
        if not dep:
            return
        from monostudio.core.production_status import SKIPPED_STATUS_ID
        from monostudio.core.project_schedule import build_timeline_entity_groups
        from monostudio.core.schedule_skip import collect_lane_entity_paths

        sw = getattr(self, "_schedule_page_widget", None)
        groups = list(getattr(getattr(sw, "_gantt", None), "_groups", []) or [])
        if not groups and self._project_index is not None:
            include_shots = bool(getattr(sw, "_include_shots", True)) if sw else True
            include_assets = bool(getattr(sw, "_include_assets", False)) if sw else False
            groups = build_timeline_entity_groups(
                self._project_root,
                self._project_index,
                include_shots=include_shots,
                include_assets=include_assets,
            )
        paths = collect_lane_entity_paths(groups, dep, self._project_root)
        if not paths:
            return
        sid = SKIPPED_STATUS_ID if skip else None
        self._apply_production_status_overrides(
            [(p, dep, sid) for p in paths],
            show_progress=True,
        )

    def _on_main_view_active_dcc_changed(self, path: object, department: str, dcc_id: str) -> None:
        """Đồng bộ active DCC từ Main View sang Inspector (refresh identity)."""
        item = self._main_view.selected_view_item()
        if not item:
            return
        item_path = getattr(item, "path", None)
        if not item_path or str(item_path) != str(path):
            return
        self._inspector.set_item(item, active_department_hint=department)

    def _on_open_publish_folder_requested(self, path: object) -> None:
        if not isinstance(path, Path):
            return
        try:
            if not path.exists():
                return
        except (OSError, TypeError):
            return
        shell_open_folder(path)

    def _on_show_publish_changed(self, show_publish: bool) -> None:
        self._inspector.set_show_publish(show_publish)
        if self._main_view.has_valid_selection():
            item = self._main_view.selected_view_item()
            if item is not None:
                self._inspector.set_item(item, active_department_hint=self.current_department)

    def _on_browser_mode_changed(self, mode: str) -> None:
        if mode == "review" and self._nav_rail.current_context() == "Shots":
            self._schedule_shot_review_render_enrich()

    def _on_recent_task_clicked(self, task: object) -> None:
        from monostudio.ui_qt.recent_tasks_store import RecentTask
        if not isinstance(task, RecentTask):
            return
        # Switch to Assets or Shots context.
        ctx = "Assets" if task.item_type == "asset" else "Shots"
        self._nav_rail.set_current_context(ctx)
        # Set department filter: sync controller first, then sidebar with emit=False so clicking
        # two tasks with the same department does not trigger controller's "same dept → toggle off".
        self._controller.sync_filter_state(department=task.department, type_id=self.current_type)
        self._filter_panel.filters().set_selected_department(task.department, emit=False)
        # Select the item in main view.
        self._main_view.select_item_by_path(Path(task.item_path))
        # Sidebar toast for task selection (single-click).
        # Anchor vertically to the recent task row instead of raw cursor Y.
        try:
            tasks_list = getattr(self._filter_panel, "_tasks_list", None)
            if tasks_list is not None:
                row = tasks_list.currentRow()
                if row >= 0:
                    item = tasks_list.item(row)
                    if item is not None:
                        rect = tasks_list.visualItemRect(item)
                        top_left = tasks_list.viewport().mapToGlobal(rect.topLeft())
                        notification_service.set_sidebar_anchor_from_global_y(top_left.y())
            else:
                notification_service.set_sidebar_anchor_from_cursor()
        except Exception:
            notification_service.set_sidebar_anchor_from_cursor()
        label = (task.department or "").strip()
        dcc = (task.dcc or "").strip()
        if label and dcc:
            msg = f"Task: {task.item_name} · {label} · {dcc}"
        elif label:
            msg = f"Task: {task.item_name} · {label}"
        else:
            msg = f"Task: {task.item_name}"
        notification_service.info(msg, category="sidebar")

    def _on_recent_task_double_clicked(self, task: object) -> None:
        from monostudio.ui_qt.recent_tasks_store import RecentTask
        if not isinstance(task, RecentTask):
            return
        self._on_recent_task_clicked(task)
        item = self._main_view.selected_view_item()
        if item is None:
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        dept = (task.department or "").strip()
        dcc = (task.dcc or "").strip()
        if not dept or not dcc:
            self._on_open_requested(item)
            return
        try:
            self._controller.open_with_dcc(item=ref, department=dept, dcc=dcc, parent=self)
            self._refresh_recent_tasks()
        except Exception as e:
            logging.warning("DCC launch failed (recent task): %s", e, exc_info=True)
            QMessageBox.critical(self, "Open DCC", str(e))

    def _on_clear_recent_tasks(self) -> None:
        if self._project_root is None:
            return
        self._recent_tasks_store.clear_for_project(self._project_root)
        self._refresh_recent_tasks()

    def _on_compact_filter_requested(self) -> None:
        """Show full filter panel (Departments & Types) in a popup when filter panel is hidden.
        Same as noti button: if popup is open, close it; if just closed (grace), don't reopen."""
        if self._sidebar_panel_visible:
            return
        # Toggle: if popup is visible, close it and return
        if self._compact_filter_popup is not None and self._compact_filter_popup.isVisible():
            self._compact_filter_popup.close()
            return
        if (time.monotonic() - self._compact_filter_popup_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        filter_widget = self._filter_panel.take_filters_center()
        if filter_widget is None:
            return

        class _FilterPopupFrame(QFrame):
            def __init__(self, parent, on_hide_cb):
                super().__init__(parent)
                self._on_hide_cb = on_hide_cb

            def hideEvent(self, event):
                self._on_hide_cb()
                super().hideEvent(event)

        def _on_filter_popup_hidden():
            self._finalize_compact_filter_popup()
            btn = getattr(self._nav_rail, "_filter_btn", None)
            if btn is not None:
                QTimer.singleShot(0, lambda: self._nav_rail._clear_tool_button_hover(btn))

        popup = _FilterPopupFrame(None, _on_filter_popup_hidden)
        popup.setObjectName("SidebarCompactFilterPopup")
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.FramelessWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground, False)
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(0, 0, 0, 0)
        _TOP_BAR_HEIGHT = 56
        _POPUP_BOTTOM_MARGIN = 8
        max_h = max_popup_height_in_widget(
            self._sidebar_container,
            top_offset=_TOP_BAR_HEIGHT,
            margin=_POPUP_BOTTOM_MARGIN,
        )
        scroll = QScrollArea(popup)
        scroll.setObjectName("SidebarCompactFilterScroll")
        scroll.setAttribute(Qt.WA_StyledBackground, True)
        scroll.setWidget(filter_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAttribute(Qt.WA_StyledBackground, True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        lay.addWidget(scroll, 1)
        popup.setFixedWidth(self.SIDEBAR_PANEL_W)
        popup.setFixedHeight(max_h)
        self._compact_filter_popup = popup
        self._compact_filter_scroll = scroll
        # Align with the filter column slot (right of nav rail), not the filter button flyout.
        panel_origin = self._sidebar_container.mapToGlobal(
            QPoint(self.SIDEBAR_RAIL_W, _TOP_BAR_HEIGHT)
        )
        popup.move(panel_origin)
        popup.show()
        self._nav_rail.set_filter_popup_active(True)

    def _new_project(self) -> None:
        if self._workspace_root is None:
            return

        dialog = NewProjectDialog(self._workspace_root, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        display_name = dialog.project_name()
        start_date = dialog.start_date_iso()

        try:
            created = create_new_project(
                workspace_root=self._workspace_root,
                display_name=display_name,
                start_date=start_date,
            )
        except FileExistsError:
            QMessageBox.critical(self, "New Project", "Target project folder already exists.")
            return
        except OSError:
            QMessageBox.critical(self, "New Project", "Failed to create project.")
            return
        except Exception:
            QMessageBox.critical(self, "New Project", "Failed to create project.")
            return

        seed_project_from_user_default(created.root)

        # Do NOT rescan workspace from scratch; append to existing list.
        discovered = DiscoveredProject(name=created.display_name, root=created.root)
        self._workspace_projects.append(discovered)
        # Auto-switch to new project (sets project/root, triggers existing autoscan, resets Inspector).
        self._apply_project_root(str(created.root), save=True)
        notification_service.success(f"Created Project '{created.display_name}'.")

    def _open_stress_diagnostics(self) -> None:
        """Open stress diagnostics dialog (only when MONOS_STRESS=1 or MONOS_PROFILE=1)."""
        if not stress_profiler_enabled():
            return
        dialog = StressDiagnosticsDialog(
            app_state=self._app_state,
            main_view=self._main_view,
            thumbnail_manager=self._thumbnail_manager,
            fs_collector=self._fs_event_collector,
            parent=self,
        )
        dialog.show()

    def _reload_app_hotkeys(self) -> None:
        from monostudio.ui_qt.app_hotkeys import refresh_all_hotkey_tooltips, reload_bound_shortcuts

        reload_bound_shortcuts(self._settings, self._bound_hotkeys)
        self._main_view.reload_hotkeys()
        self._inspector.reload_hotkey_tooltips()
        refresh_all_hotkey_tooltips(self._settings)
        self._nav_rail.refresh_quick_view_tooltips(self._settings)
        for page in (
            self._inbox_page_widget,
            self._outbox_page_widget,
            self._internal_check_page_widget,
            self._reference_page_widget,
        ):
            if page is None:
                continue
            reload_bound_shortcuts(self._settings, getattr(page, "_bound_hotkeys", []))
            pane = getattr(page, "_tree_pane", None)
            if pane is not None:
                sync_tt = getattr(pane, "sync_hotkey_tooltips", None)
                if callable(sync_tt):
                    sync_tt(self._settings)
        if self._schedule_page_widget is not None:
            self._schedule_page_widget.reload_hotkeys()

    def _on_main_view_thumbnail_source_changed(self) -> None:
        """Header popup: user vs render sequence — same refresh path as saving Settings."""
        self._thumbnail_manager.clear_memory_cache()
        self._main_view.invalidate_all_thumbnails_for_source_change()
        self._inspector.invalidate_inspector_preview_settings_cache()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            workspace_root=self._workspace_root,
            project_root=self._project_root,
            settings=self._settings,
            parent=self,
        )
        dialog.workspace_root_selected.connect(lambda p: self._apply_workspace_root(p, save=True))
        dialog.project_root_selected.connect(lambda p: self._apply_project_root(p, save=True))
        dialog.access_session_changed.connect(self._refresh_user_button)
        dialog.nav_quick_slots_changed.connect(
            lambda: self._nav_rail.refresh_quick_view_tooltips(self._settings)
        )
        dialog.hotkeys_changed.connect(self._reload_app_hotkeys)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._refresh_user_button()
        if accepted:
            self._thumbnail_manager.clear_memory_cache()
            self._main_view.invalidate_all_thumbnails_for_source_change()
            self._inspector.invalidate_inspector_preview_settings_cache()
        self._sync_pipeline_preset_metadata_ui()
        if self._project_root is not None:
            try:
                dept_reg = DepartmentRegistry.for_project(self._project_root)
                self._inspector.set_department_registry(dept_reg)
                self._inspector.set_department_icon_map(self._dept_icon_map)
                self._inspector.set_type_short_name_map(self._type_short_name_map)
            except Exception:
                self._inspector.set_department_registry(None)
                self._inspector.set_department_icon_map({})
                self._inspector.set_type_short_name_map({})

        renamed_to = dialog.project_root_renamed_to()
        if renamed_to is None:
            return

        old = self._project_root
        # Switch project to renamed root (explicit, no background work).
        self._apply_project_root(str(renamed_to), save=True)

        # Update in-memory workspace project list (best-effort).
        if old is not None:
            updated: list[DiscoveredProject] = []
            for p in self._workspace_projects:
                if p.root == old:
                    updated.append(DiscoveredProject(name=p.name, root=renamed_to))
                else:
                    updated.append(p)
            self._workspace_projects = updated
            self._sync_top_bar()

    @staticmethod
    def _app_settings_path() -> Path:
        from monostudio.core.app_paths import get_app_settings_path

        return get_app_settings_path()

    def _toggle_maximize(self) -> None:
        if sys.platform == "win32":
            from qframelesswindow.utils import toggleMaxState
            from qframelesswindow.utils.win32_utils import isMaximized as win32_is_maximized
            hwnd = int(self.winId())
            was_max = win32_is_maximized(hwnd)
            if not was_max:
                self._geometry_before_maximize = self.geometry()
            toggleMaxState(self)
            self._top_bar.set_maximized(not was_max)
            if was_max and self._geometry_before_maximize is not None and self._geometry_before_maximize.isValid():
                QTimer.singleShot(50, self._apply_geometry_before_maximize)
        else:
            if self.isMaximized():
                if self._geometry_before_maximize is not None and self._geometry_before_maximize.isValid():
                    self.showNormal()
                    QTimer.singleShot(0, self._apply_geometry_before_maximize)
                else:
                    self.showNormal()
                self._top_bar.set_maximized(False)
            else:
                self._geometry_before_maximize = self.geometry()
                self.showMaximized()
                QTimer.singleShot(0, self._on_maximize_applied)

    def _on_maximize_applied(self) -> None:
        """Chạy sau showMaximized (non-Win): ép geometry và cập nhật icon."""
        self._apply_maximized_geometry_if_needed()
        self._top_bar.set_maximized(self.isMaximized())

    def _apply_geometry_before_maximize(self) -> None:
        """Áp lại kích thước/vị trí đã lưu trước khi maximize (restore về đúng cửa sổ cũ)."""
        if self._geometry_before_maximize is None or not self._geometry_before_maximize.isValid():
            return
        self.setGeometry(self._geometry_before_maximize)
        self._update_title_bar_geometry()
        notification_service.update_overlay_geometry()

    def _maximize_to_screen(self) -> None:
        """Maximize khi restore từ settings; trên Win32 dùng Win32 API để khít màn hình."""
        if sys.platform == "win32":
            from qframelesswindow.utils.win32_utils import isMaximized as win32_is_maximized
            import win32con
            import win32gui
            if not win32_is_maximized(int(self.winId())):
                win32gui.PostMessage(int(self.winId()), win32con.WM_SYSCOMMAND, win32con.SC_MAXIMIZE, 0)
            self._top_bar.set_maximized(True)
        else:
            screen = self.screen() or QApplication.primaryScreen()
            if not screen:
                self.showMaximized()
            else:
                self.showMaximized()
            QTimer.singleShot(0, self._on_maximize_applied)

    def _apply_restore_maximized(self) -> None:
        """Restore maximized state khi load; icon cập nhật trong _on_maximize_applied."""
        self._maximize_to_screen()

    def _restore_window_geometry(self) -> None:
        """
        Restore saved window geometry BEFORE showing the window.
        Storage: monostudio26/config/app_settings.json (app-level only)
        """
        path = self._app_settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None

        restored = False
        if isinstance(data, dict):
            b64 = data.get("window_geometry_b64")
            if isinstance(b64, str) and b64.strip():
                try:
                    raw = base64.b64decode(b64.encode("ascii"), validate=False)
                    restored = bool(self.restoreGeometry(QByteArray(raw)))
                except (OSError, ValueError):
                    restored = False
            # Restore maximized state (6b B: gọi set_maximized sau showMaximized)
            if data.get("window_maximized") is True and restored:
                self._pending_restore_maximized = True
            always_top = data.get("window_always_on_top") is True
            self._window_always_on_top = always_top
            if always_top and sys.platform != "win32":
                # Same as FramelessMainWindow.setStayOnTop but without show() — window is still hidden (splash).
                self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                self.updateFrameless()
            self._top_bar.set_always_on_top(always_top)

        if not restored:
            # First launch / no saved geometry: default size.
            self.resize(1920, 1080)

    def _restore_splitter_sizes(self) -> None:
        """Restore main/content splitter sizes from app_settings.json (after central widget is set)."""
        path = self._app_settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if not isinstance(data, dict):
            return
        for key, splitter, default in (
            ("main_splitter_sizes", self._main_splitter, [256, 1100]),
            ("content_splitter_sizes", self._content_splitter, [800, 320]),
        ):
            raw = data.get(key)
            if isinstance(raw, list) and len(raw) == 2:
                sizes = [int(x) for x in raw if isinstance(x, (int, float))]
                if len(sizes) == 2 and all(s > 0 for s in sizes):
                    splitter.setSizes(sizes)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """
        Save window geometry on close (size + position + maximized).
        May hide to system tray instead of quitting when configured.
        """
        if not self._force_quit:
            from monostudio.core.tray_preferences import (
                read_close_action,
                read_tray_enabled,
                should_prompt_close_behavior,
                write_close_action,
                write_close_prompt_shown,
            )
            from monostudio.ui_qt.close_behavior_dialog import CloseBehaviorDialog

            action = read_close_action(self._settings)
            if should_prompt_close_behavior(self._settings):
                dlg = CloseBehaviorDialog(self)
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    event.ignore()
                    return
                action = dlg.chosen_action()
                if dlg.remember_choice():
                    write_close_action(self._settings, action)
                    write_close_prompt_shown(self._settings, True)
                else:
                    write_close_prompt_shown(self._settings, True)

            tray_ok = (
                read_tray_enabled(self._settings)
                and self._tray_manager is not None
                and self._tray_manager.is_available()
            )
            if action == "minimize" and tray_ok:
                self._persist_window_state()
                self._flush_discord_inbox_outbox()
                event.ignore()
                self.hide()
                if not self._tray_manager._shown_tray_hint:
                    self._tray_manager._shown_tray_hint = True
                    self._tray_manager.show_tray_message(
                        "MONOS",
                        "MONOS is still running in the system tray.",
                    )
                return

        self._persist_window_state()
        if self._force_quit:
            self._flush_discord_inbox_outbox()
        self._worker_manager.shutdown()
        super().closeEvent(event)

    def _switch_project(self, project_root: str) -> None:
        if not project_root:
            return
        prev_root = self._project_root
        self._apply_project_root(project_root, save=True)
        self._sync_top_bar()
        if self._project_root is not None and self._project_root != prev_root:
            name = self._project_root.name or ""
            notification_service.info(f"Switched to {name}")

    def _on_switch_project_requested(self, item) -> None:
        if hasattr(item, "path") and item.path:
            self._switch_project(str(item.path))

    def _on_root_context_menu_requested(self, global_pos) -> None:
        context = self._nav_rail.current_context()

        if context == "Dashboard":
            if self._workspace_root is None:
                return
            menu = QMenu(self)
            act_refresh = menu.addAction(
                lucide_icon("refresh-cw", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Refresh",
            )
            menu.addSeparator()
            new_proj = menu.addAction(
                lucide_icon("folder-plus", size=16, color_hex=MONOS_COLORS["text_label"]),
                "New Project…",
            )
            open_folder = None
            if self._project_root is not None:
                open_folder = menu.addAction(
                    lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
                    "Open Project Folder",
                )
            chosen = menu.exec(global_pos)
            if chosen == act_refresh:
                if self._dashboard_page_widget is not None:
                    self._schedule_dashboard_refresh()
                return
            if chosen is not None and chosen == new_proj:
                self._new_project()
                return
            if open_folder is not None and chosen == open_folder and self._project_root is not None:
                shell_open_folder(self._project_root)
            return

        if self._project_root is None or self._project_index is None:
            return
        if self._entered_parent is not None:
            return

        if context not in ("Assets", "Shots"):
            return
        menu = QMenu(self)
        act_refresh = menu.addAction(
            lucide_icon("refresh-cw", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Refresh",
        )
        menu.addSeparator()
        create_asset = None
        create_shot = None

        batch_create_asset = None
        batch_create_shot = None
        if context == "Assets":
            create_asset = menu.addAction(lucide_icon("box", size=16, color_hex=MONOS_COLORS["text_label"]), "Create Asset…")
            batch_create_asset = menu.addAction(
                lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Batch Create Assets…",
            )
        elif context == "Shots":
            create_shot = menu.addAction(lucide_icon("clapperboard", size=16, color_hex=MONOS_COLORS["text_label"]), "Create Shot…")
            batch_create_shot = menu.addAction(
                lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Batch Create Shots…",
            )

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == act_refresh:
            self._on_refresh_requested()
            return
        if create_asset is not None and chosen == create_asset:
            self._create_asset()
        if batch_create_asset is not None and chosen == batch_create_asset:
            self._batch_create_assets()
        if create_shot is not None and chosen == create_shot:
            self._create_shot()
        if batch_create_shot is not None and chosen == batch_create_shot:
            self._batch_create_shots()

    @staticmethod
    def _is_safe_single_folder_name(name: str) -> bool:
        # Minimal safety to ensure we only ever create inside the target folder.
        # (Still "minimal": non-empty only, plus prevent path traversal/separators.)
        if not name:
            return False
        if name in (".", ".."):
            return False
        if any(ch in name for ch in ("/", "\\", ":", "\n", "\r", "\t")):
            return False
        return True

    def _mkdir_pipeline_entry(
        self,
        target: Path,
        departments: list[str],
        *,
        entity_kind: str,
        create_subfolders: bool,
    ) -> bool:
        """Create asset/shot folder tree at target. Returns False on failure (best-effort rollback)."""
        if self._project_root is None:
            return False
        dept_reg = DepartmentRegistry.for_project(self._project_root)
        use_dcc_folders = read_use_dcc_folders(self._project_root)
        created: list[Path] = []
        try:
            to_create: list[Path] = [target, target / "reference", target / "concept"]
            for d in departments:
                dept_folder = dept_reg.get_department_relative_path(d, entity_kind)
                if not (dept_folder or "").strip():
                    continue
                dept_dir = target / dept_folder
                to_create.append(dept_dir)
                if create_subfolders:
                    if not use_dcc_folders:
                        to_create.append(dept_dir / "work")
                    to_create.append(dept_dir / "publish")

            for p in to_create:
                try:
                    p.mkdir(parents=True, exist_ok=False)
                    created.append(p)
                except FileExistsError:
                    continue
        except OSError:
            for p in reversed(created):
                try:
                    p.rmdir()
                except OSError:
                    pass
            return False
        return True

    def _after_pipeline_entries_created(self) -> None:
        self._entered_parent = None
        self._rescan_project()
        self._inspector.set_item(None)
        QTimer.singleShot(0, self._main_view.repaint_tile_and_list_views)

    def _create_asset(self) -> None:
        if self._project_root is None:
            return

        dialog = CreateAssetDialog(
            self._project_root, self, initial_type_id=self.current_type
        )
        if dialog.exec() != QDialog.Accepted:
            return

        asset_type = dialog.asset_type()
        asset_name = dialog.asset_name()
        departments = dialog.selected_departments()
        create_subfolders = dialog.create_subfolders()
        if not asset_type or not asset_name:
            return

        type_reg = TypeRegistry.for_project(self._project_root)
        struct_reg = StructureRegistry.for_project(self._project_root)
        type_folder = type_reg.get_type_folder(asset_type)
        target = self._project_root / struct_reg.get_folder("assets") / type_folder / asset_name
        if target.exists():
            return

        if not self._mkdir_pipeline_entry(
            target, departments, entity_kind="asset", create_subfolders=create_subfolders
        ):
            return

        self._after_pipeline_entries_created()
        notification_service.success(f"Created Asset '{asset_name}'.")

    def _batch_create_assets(self) -> None:
        if self._project_root is None:
            return

        dialog = BatchCreateAssetDialog(
            self._project_root, self, initial_type_id=self.current_type
        )
        if dialog.exec() != QDialog.Accepted:
            return

        asset_type = dialog.asset_type()
        asset_names = dialog.asset_names()
        departments = dialog.selected_departments()
        create_subfolders = dialog.create_subfolders()
        if not asset_type or not asset_names:
            return

        type_reg = TypeRegistry.for_project(self._project_root)
        struct_reg = StructureRegistry.for_project(self._project_root)
        type_folder = type_reg.get_type_folder(asset_type)
        assets_root = self._project_root / struct_reg.get_folder("assets") / type_folder

        created_names: list[str] = []
        for asset_name in asset_names:
            target = assets_root / asset_name
            if target.exists():
                continue
            if not self._is_safe_single_folder_name(asset_name):
                continue
            if self._mkdir_pipeline_entry(
                target, departments, entity_kind="asset", create_subfolders=create_subfolders
            ):
                created_names.append(asset_name)

        if not created_names:
            return

        self._after_pipeline_entries_created()
        if len(created_names) == 1:
            notification_service.success(f"Created Asset '{created_names[0]}'.")
        else:
            notification_service.success(f"Created {len(created_names)} assets.")

    def _create_shot(self) -> None:
        if self._project_root is None:
            return

        dialog = CreateShotDialog(
            self._project_root, self, initial_type_id=self.current_type
        )
        if dialog.exec() != QDialog.Accepted:
            return

        shot_name = dialog.shot_name()
        departments = dialog.selected_departments()
        create_subfolders = dialog.create_subfolders()
        if not shot_name:
            return

        struct_reg = StructureRegistry.for_project(self._project_root)
        target = self._project_root / struct_reg.get_folder("shots") / shot_name
        if target.exists():
            return

        if not self._mkdir_pipeline_entry(
            target, departments, entity_kind="shot", create_subfolders=create_subfolders
        ):
            return

        self._after_pipeline_entries_created()
        notification_service.success(f"Created Shot '{shot_name}'.")

    def _batch_create_shots(self) -> None:
        if self._project_root is None:
            return

        dialog = BatchCreateShotDialog(
            self._project_root, self, initial_type_id=self.current_type
        )
        if dialog.exec() != QDialog.Accepted:
            return

        shot_names = dialog.shot_names()
        departments = dialog.selected_departments()
        create_subfolders = dialog.create_subfolders()
        if not shot_names:
            return

        struct_reg = StructureRegistry.for_project(self._project_root)
        shots_root = self._project_root / struct_reg.get_folder("shots")

        created_names: list[str] = []
        for shot_name in shot_names:
            target = shots_root / shot_name
            if target.exists():
                continue
            if not self._is_safe_single_folder_name(shot_name):
                continue
            if self._mkdir_pipeline_entry(
                target, departments, entity_kind="shot", create_subfolders=create_subfolders
            ):
                created_names.append(shot_name)

        if not created_names:
            return

        self._after_pipeline_entries_created()
        if len(created_names) == 1:
            notification_service.success(f"Created Shot '{created_names[0]}'.")
        else:
            notification_service.success(f"Created {len(created_names)} shots.")

