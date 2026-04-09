from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QFileSystemWatcher, QPoint, Qt, QRect, QSettings, Signal, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QFrame, QMenu, QMessageBox, QSizePolicy, QSplitter, QStackedWidget, QToolTip, QVBoxLayout, QWidget
from qframelesswindow import FramelessMainWindow

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.update_checker import run_full_update_check
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.fs_reader import (
    build_project_index,
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
from monostudio.ui_qt.create_entry_dialogs import CreateAssetDialog, CreateShotDialog
from monostudio.core.inbox_reader import add_to_inbox, append_inbox_distributed
from monostudio.core.outbox_reader import add_to_outbox
from monostudio.ui_qt.inbox_drop_dialog import InboxDropDialog
from monostudio.ui_qt.inbox_page_widget import InboxPageWidget
from monostudio.ui_qt.outbox_page_widget import OutboxPageWidget
from monostudio.ui_qt.reference_page_widget import ReferencePageWidget
from monostudio.ui_qt.inspector import InspectorPanel
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.new_project_dialog import NewProjectDialog
from monostudio.ui_qt.settings_dialog import SettingsDialog
from monostudio.ui_qt.sidebar import Sidebar, SidebarCompact
from monostudio.ui_qt.top_bar import TopBar
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind
from monostudio.ui_qt.delete_confirm_dialog import DeleteConfirmDialog, ask_delete_folder
from monostudio.ui_qt.rename_asset_dialog import RenameAssetDialog
from monostudio.ui_qt.app_controller import AppController
from monostudio.ui_qt.app_state import AppState
from monostudio.ui_qt.recent_tasks_store import RecentTasksStore
from monostudio.ui_qt.worker_manager import WorkerManager, WorkerTask
from monostudio.ui_qt.thumbnails import ThumbnailManager
from monostudio.ui_qt.fs_watcher import FsEventCollector
from monostudio.ui_qt.stress_diagnostics_dialog import StressDiagnosticsDialog
from monostudio.ui_qt.stress_profiler import enabled as stress_profiler_enabled
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.notification import notify as notification_service


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

    departmentChanged = Signal(object)  # str | None
    typeChanged = Signal(object)  # str | None

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MONOS")
        ensure_pipeline_bootstrap()

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
        # Short cooldown after switching to Inbox so a delayed filter signal does not trigger a second reload (items flash then placeholder).
        self._inbox_switch_cooldown: bool = False
        # Guard: filter (department/type) changes must never trigger Open DCC flows or spawn dialogs.
        self._filter_switch_in_progress: bool = False
        self._workspace_root: Path | None = None
        self._workspace_projects: list[DiscoveredProject] = []
        self._workspace_project_status: dict[str, str] = {}

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
        self._entered_parent: Asset | Shot | None = None

        # Centralized filter state (UI-only; no filtering engine yet)
        self.current_department: str | None = None
        self.current_type: str | None = None
        self.current_search_query: str = ""
        self._apply_pipeline_types_and_presets_metadata(load_pipeline_types_and_presets_for_project(self._project_root))

        self._sidebar = Sidebar()
        self._sidebar_compact = SidebarCompact(self)
        self._sidebar_compact.set_filter_source(self._sidebar.filters())
        # Persist sidebar filter selections per page (assets/shots).
        try:
            self._sidebar.filters().set_settings(self._settings)
        except Exception:
            pass
        self._sidebar_container = QWidget(self)
        self._sidebar_container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._sidebar_container.setMinimumWidth(256)
        self._sidebar_container.setMaximumWidth(256)  # like old Sidebar: fixed 256, no gap
        _sidebar_stack = QStackedWidget(self._sidebar_container)
        _sidebar_stack.setContentsMargins(0, 0, 0, 0)
        _sidebar_stack.addWidget(self._sidebar)
        _sidebar_stack.addWidget(self._sidebar_compact)
        _sidebar_stack.setCurrentIndex(0)
        _sidebar_container_layout = QVBoxLayout(self._sidebar_container)
        _sidebar_container_layout.setContentsMargins(0, 0, 0, 0)
        _sidebar_container_layout.setSpacing(0)
        _sidebar_container_layout.addWidget(_sidebar_stack)
        self._sidebar_stack = _sidebar_stack
        self._main_view = MainView()
        self._main_view.set_thumbnail_manager(self._thumbnail_manager)
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._main_view)
        self._inbox_page_widget: InboxPageWidget | None = None
        self._outbox_page_widget: OutboxPageWidget | None = None
        self._reference_page_widget: ReferencePageWidget | None = None
        self._inspector = InspectorPanel()
        self._inspector.set_app_settings(self._settings)
        self._inspector.set_thumbnail_manager(self._thumbnail_manager)
        self._inspector.set_worker_manager(self._worker_manager)
        self._inspector.setMinimumWidth(240)
        self._top_bar = TopBar(self)
        self._top_bar.setFixedHeight(56)  # so FramelessMainWindow resize keeps height
        self.setTitleBar(self._top_bar)  # replace library title bar with MONOS TopBar
        self._geometry_before_maximize: QRect | None = None  # restore về đúng kích thước khi bấm restore
        self._clipboard_thumbs = ClipboardThumbnailHandler(parent=self)

        # Topbar replaces the menu bar (no menus).
        try:
            self.menuBar().hide()
        except Exception:
            pass
        self._restore_workspace_root()
        self._restore_project_root()
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

        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.setObjectName("MainSplitter")
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(self._sidebar_container)
        self._main_splitter.addWidget(right_container)
        self._main_splitter.setStretchFactor(0, 20)
        self._main_splitter.setStretchFactor(1, 80)
        self._main_splitter.setSizes([256, 1100])

        self.setCentralWidget(self._main_splitter)
        self._restore_splitter_sizes()
        # Store sizes to restore when showing panels again after narrow resize.
        self._content_splitter_sizes_restore: list[int] = [800, 320]
        self._main_splitter_sizes_restore: list[int] = [256, 1100]
        self._panel_layout_auto: bool = True
        # Manual sidebar: full (256px) vs compact rail (56px) — không ẩn hẳn cột sidebar.
        self._manual_sidebar_full: bool = True
        self._manual_inspector_visible: bool = True
        self._load_panel_layout_prefs()
        self._compact_filter_popup: QFrame | None = None
        self._compact_filter_popup_closed_at = 0.0
        self._POPUP_REOPEN_GRACE = 0.25
        # Frameless window on Windows often receives drop at window level; forward to current page
        self.setAcceptDrops(True)
        # Title bar only over right pane (not over sidebar); update when splitter/window resizes.
        self._update_title_bar_geometry()
        self._top_bar.raise_()
        self._main_splitter.splitterMoved.connect(self._update_title_bar_geometry)

        self._sidebar.context_changed.connect(self._on_context_switched)
        self._sidebar.context_clicked.connect(self._on_context_clicked)
        self._sidebar.context_menu_requested.connect(self._on_sidebar_context_menu_requested)
        self._sidebar.settings_requested.connect(self._open_settings)
        self._sidebar.project_switch_requested.connect(self._switch_project)
        self._top_bar.settings_clicked.connect(self._open_settings)
        self._top_bar.layout_auto_clicked.connect(self._on_top_bar_layout_auto_clicked)
        self._top_bar.layout_sidebar_clicked.connect(self._on_top_bar_layout_sidebar_clicked)
        self._top_bar.layout_inspector_clicked.connect(self._on_top_bar_layout_inspector_clicked)
        self._sidebar.recent_task_clicked.connect(self._on_recent_task_clicked)
        self._sidebar.recent_task_double_clicked.connect(self._on_recent_task_double_clicked)
        self._sidebar.clear_recent_tasks_requested.connect(self._on_clear_recent_tasks)
        # Compact sidebar: sync context to full sidebar then full sidebar emits; wire other signals to same handlers.
        self._sidebar_compact.context_changed.connect(lambda ctx: self._sidebar.set_current_context(ctx))
        self._sidebar_compact.context_clicked.connect(self._on_context_clicked)
        self._sidebar_compact.project_switch_requested.connect(self._switch_project)
        self._sidebar_compact.recent_task_clicked.connect(self._on_recent_task_clicked)
        self._sidebar_compact.recent_task_double_clicked.connect(self._on_recent_task_double_clicked)
        self._sidebar_compact.clear_recent_tasks_requested.connect(self._on_clear_recent_tasks)
        self._sidebar_compact.filter_requested.connect(self._on_compact_filter_requested)
        # Metadata-driven filter sidebar (UI-only; wiring stub).
        self._sidebar.filters().departmentClicked.connect(self._controller.on_department_clicked)
        self._sidebar.filters().typeClicked.connect(self._controller.on_type_clicked)
        self._sidebar.filters().tagClicked.connect(self._on_tag_filter_changed)
        self._sidebar.filters().tagsDefinitionsChanged.connect(self._on_tag_definitions_changed)
        self._controller.departmentChanged.connect(lambda v: self._set_current_department(v, toggle_if_same=False))
        self._controller.typeChanged.connect(lambda v: self._set_current_type(v, toggle_if_same=False))
        self._controller.departmentChanged.connect(self._on_department_changed_notify)
        self._controller.typeChanged.connect(self._on_type_changed_notify)
        self._controller.departmentChanged.connect(self._set_main_view_department)
        self.departmentChanged.connect(self._on_filter_state_changed)
        self.typeChanged.connect(self._on_filter_state_changed)
        self._top_bar.minimize_clicked.connect(self.showMinimized)
        self._top_bar.maximize_clicked.connect(self._toggle_maximize)
        self._top_bar.close_clicked.connect(self.close)
        self._top_bar.title_double_clicked.connect(self._toggle_maximize)
        self._inspector.close_requested.connect(self._main_view.clear_selection)
        self._inspector.paste_thumbnail_requested.connect(self._on_paste_thumbnail_requested)
        self._inspector.remove_thumbnail_requested.connect(self._on_remove_thumbnail_requested)
        self._main_view.valid_selection_changed.connect(self._on_valid_selection_changed)
        self._main_view.item_activated.connect(self._on_item_activated)
        self._main_view.refresh_requested.connect(self._on_refresh_requested)
        self._main_view.root_context_menu_requested.connect(self._on_root_context_menu_requested)
        self._main_view.copy_inventory_requested.connect(self._on_copy_item_inventory_requested)
        self._main_view.open_requested.connect(self._on_open_requested)
        self._main_view.open_with_requested.connect(self._on_open_with_requested)
        self._main_view.create_new_requested.connect(self._on_create_new_requested)
        self._main_view.selection_id_changed.connect(self._app_state.set_selection)
        self._app_state.selectionChanged.connect(self._main_view.set_selection_from_state)
        self._app_state.assetsChanged.connect(self._on_app_state_assets_changed)
        self._app_state.shotsChanged.connect(self._on_app_state_shots_changed)
        self._app_state.filtersChanged.connect(self._on_app_state_filters_changed)
        self._app_state.thumbnailsChanged.connect(self._on_app_state_thumbnails_changed)

        # Clipboard thumbnail overrides: refresh UI after successful paste.
        self._clipboard_thumbs.thumbnailUpdated.connect(self._on_thumbnail_updated)
        self._main_view.delete_requested.connect(self._on_delete_requested)
        self._main_view.rename_requested.connect(self._on_rename_asset_requested)
        self._main_view.switch_project_requested.connect(self._on_switch_project_requested)
        self._main_view.primary_action_requested.connect(self._on_primary_action_requested)
        self._main_view.search_query_changed.connect(self._on_search_query_changed)
        self._main_view.show_publish_changed.connect(self._on_show_publish_changed)
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
        self._inspector.inspector_hidden_departments_changed.connect(self._main_view.set_inspector_hidden_departments)
        self._inspector.production_status_override_requested.connect(self._on_production_status_override)
        self._main_view.production_status_override_chosen.connect(self._on_production_status_override)
        self._main_view.active_dcc_changed.connect(self._on_main_view_active_dcc_changed)
        self._main_view.thumbnail_source_changed.connect(self._on_main_view_thumbnail_source_changed)

        # Restore last nav page (Assets/Shots/Inbox/...) after connections so Inbox switch builds split view.
        self._restore_sidebar_context()

        # Initial population is driven by project-root restore (scan trigger) and current context.
        self._reload_main_view()
        self._inspector.set_item(None)
        self._sync_primary_action()
        self._sync_top_bar()
        self._sync_filter_state_from_sidebar()
        self._app_state.set_filters(self.current_department, self.current_type)

        notification_service.set_main_window(self, self._main_view)
        notification_service.set_general_toast_anchor_widget(self._top_bar.get_noti_button())
        # Anchor important banner under the update button so it appears as a callout.
        notification_service.set_important_anchor_widget(self._top_bar.get_update_button())

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
        dialog.open_to_updates_tab()
        dialog.exec()
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
            notification_service.success("File watcher paused. Rename and delete are now allowed.")
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
        # Glyph: checked = full-width rail; unchecked = compact 56px (vẫn còn sidebar).
        sidebar_expanded = sw0 > 80
        self._top_bar.set_panel_layout_controls(
            auto=self._panel_layout_auto,
            sidebar_on=sidebar_expanded,
            inspector_on=self._inspector.isVisible(),
        )

    def _apply_main_splitter_sidebar_metric(self, mode: str) -> None:
        """
        Set main splitter so the first pane width exactly matches the sidebar column (no dead gap).
        Sidebar uses Fixed policy + hard min/max; if splitter[0] > maxWidth, Qt leaves empty space.
        mode: 'compact' | 'full'
        """
        w = max(0, self._main_splitter.width())
        if mode == "compact":
            self._sidebar_container.setMinimumWidth(56)
            self._sidebar_container.setMaximumWidth(56)
            sw = min(56, w)
            self._main_splitter.setSizes([sw, max(0, w - sw)])
        else:
            self._sidebar_container.setMinimumWidth(256)
            self._sidebar_container.setMaximumWidth(256)
            sw = min(256, w)
            self._main_splitter.setSizes([sw, max(0, w - sw)])

    def _apply_manual_panel_layout_full(self) -> None:
        """Apply user-chosen sidebar / Inspector visibility (after TopBar toggles or first show in manual mode)."""
        if self._manual_sidebar_full:
            if self._sidebar_stack.currentIndex() != 0:
                self._sidebar.set_current_context(self._sidebar_compact.current_context())
                self._sidebar_stack.setCurrentIndex(0)
            self._apply_main_splitter_sidebar_metric("full")
        else:
            if self._sidebar_stack.currentIndex() != 1:
                sizes_now = self._main_splitter.sizes()
                if len(sizes_now) >= 2 and sizes_now[0] > 56:
                    self._main_splitter_sizes_restore = list(sizes_now)
                self._sidebar_compact.set_current_context(self._sidebar.current_context())
                self._sidebar_stack.setCurrentIndex(1)
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
            self._manual_sidebar_full = sw0 <= 56
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
        """Narrow: hide inspector. Very narrow: switch to compact sidebar (56px)."""
        w = max(0, self._main_splitter.width())
        is_compact = self._sidebar_stack.currentIndex() == 1
        if w < self._WIDTH_HIDE_SIDEBAR:
            if not is_compact:
                sizes = self._main_splitter.sizes()
                if len(sizes) >= 2 and sizes[0] > 0:
                    self._main_splitter_sizes_restore = list(sizes)
                self._sidebar_compact.set_current_context(self._sidebar.current_context())
                self._sidebar_stack.setCurrentIndex(1)
            self._apply_main_splitter_sidebar_metric("compact")
            if self._inspector.isVisible():
                sizes = self._content_splitter.sizes()
                if len(sizes) >= 2 and sizes[1] > 0:
                    self._content_splitter_sizes_restore = list(sizes)
                self._inspector.setVisible(False)
            self._content_splitter.setSizes([self._content_splitter.width(), 0])
        elif w < self._WIDTH_HIDE_INSPECTOR:
            if is_compact:
                self._sidebar.set_current_context(self._sidebar_compact.current_context())
                self._sidebar_stack.setCurrentIndex(0)
            self._apply_main_splitter_sidebar_metric("full")
            if self._inspector.isVisible():
                sizes = self._content_splitter.sizes()
                if len(sizes) >= 2 and sizes[1] > 0:
                    self._content_splitter_sizes_restore = list(sizes)
                self._inspector.setVisible(False)
            self._content_splitter.setSizes([self._content_splitter.width(), 0])
        else:
            if is_compact:
                self._sidebar.set_current_context(self._sidebar_compact.current_context())
                self._sidebar_stack.setCurrentIndex(0)
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

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        paths = [p for p in paths if p.exists()]
        event.acceptProposedAction()
        if not paths:
            return
        # Forward to current content page (frameless window on Windows often gets drop at window level)
        current = self._content_stack.currentWidget()
        logging.debug("MainWindow dropEvent: paths=%s current=%s", [str(p) for p in paths], type(current).__name__)
        pos_in_window = event.position().toPoint()
        if current is self._reference_page_widget and self._reference_page_widget is not None:
            pos_in_page = self._reference_page_widget.mapFrom(self, pos_in_window)
            tree_pane = self._reference_page_widget._tree_pane
            pos_in_pane = tree_pane.mapFrom(self._reference_page_widget, pos_in_page)
            if tree_pane.rect().contains(pos_in_pane):
                target = tree_pane.get_drop_target_folder(pos_in_pane)
                if target and target.is_dir():
                    root = getattr(tree_pane, "_root_path", None)
                    try:
                        use_move = (
                            root is not None
                            and paths
                            and all(
                                p.resolve().exists() and p.resolve().is_relative_to(Path(root).resolve())
                                for p in paths
                            )
                        )
                    except (ValueError, OSError):
                        use_move = False
                    if use_move:
                        tree_pane.move_files_to_folder(paths, target)
                        notification_service.success(f"Moved {len(paths)} item{'s' if len(paths) != 1 else ''} in Project Guide.")
                    else:
                        tree_pane.drop_files_to_folder(paths, target)
                        notification_service.success(f"Added {len(paths)} item{'s' if len(paths) != 1 else ''} to Project Guide.")
                    return
            self._on_reference_drop_requested(paths)
        elif current is self._inbox_page_widget and self._inbox_page_widget is not None:
            self._on_inbox_drop_requested(paths)
        elif current is self._outbox_page_widget and self._outbox_page_widget is not None:
            self._on_outbox_drop_requested(paths)
        else:
            # Other pages: no drop handling
            pass

    def _apply_pipeline_types_and_presets_metadata(self, meta: PipelineTypesAndPresets) -> None:
        self._dept_icon_map = {
            k: v.icon_name for k, v in meta.departments.items() if v.icon_name
        }
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
            self._sidebar.filters().reload_from_pipeline_metadata()
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

    def _on_filter_state_changed(self, _value=None) -> None:
        # Filter changes can cause selection/model churn; never allow it to trigger Open flows.
        if getattr(self, "_filter_switch_in_progress", False):
            return
        # During context switch, do not reload from filter signals (avoids second reload that can wipe Inbox items).
        if getattr(self, "_context_switch_in_progress", False):
            return
        ctx = self._sidebar.current_context()
        if ctx not in ("Assets", "Inbox", "Project Guide", "Outbox"):
            return
        if ctx == "Inbox" and getattr(self, "_inbox_switch_cooldown", False):
            return
        if ctx == "Project Guide":
            if self._reference_page_widget is not None:
                self._reference_page_widget.set_department(self._sidebar.filters().current_department() or "reference")
            self._filter_switch_in_progress = True
            try:
                pass  # no main view reload for Reference
            finally:
                self._filter_switch_in_progress = False
            return
        if ctx == "Assets" and self._entered_parent is not None:
            return
        self._filter_switch_in_progress = True
        try:
            # Force sync filter from sidebar before reload (fixes type desync: Assets→Shots→Assets then switch to Character).
            if ctx == "Assets":
                self._sync_filter_state_from_sidebar()
                self._main_view.clear()
            self._reload_main_view()
        finally:
            self._filter_switch_in_progress = False
            # Sync inspector exactly once after the filter switch settles.
            try:
                self._on_valid_selection_changed(self._main_view.has_valid_selection())
            except Exception:
                pass

    def _set_main_view_department(self, _value: object = None) -> None:
        """Sync main view header + thumb badge + inspector preview with current department."""
        dep = self._controller.current_department
        label, icon_name = self._sidebar.filters().get_department_display(dep) if dep else (None, None)
        self._main_view.set_active_department(dep, label=label, icon_name=icon_name)
        self._inspector.set_active_department(dep)

    def _set_main_view_type(self) -> None:
        """Sync main view type badge: Assets = asset type (Character, Prop, …)."""
        ctx = self._sidebar.current_context()
        if ctx != "Assets":
            self._main_view.set_selected_asset_type(None)
            return
        type_id = self._sidebar.filters().current_type()
        if not type_id:
            self._main_view.set_selected_asset_type(None)
            return
        label, icon_name = self._sidebar.filters().get_type_display(type_id)
        self._main_view.set_selected_asset_type(type_id, label=label, icon_name=icon_name)

    def _set_current_department(self, department, *, toggle_if_same: bool) -> None:
        new = department if isinstance(department, str) and department.strip() else None
        if toggle_if_same and new is not None and new == self.current_department:
            new = None
        if new == self.current_department:
            return
        self.current_department = new
        self._app_state.set_filters(self.current_department, self.current_type)
        self.departmentChanged.emit(new)

    def _set_current_type(self, type_id, *, toggle_if_same: bool) -> None:
        new = type_id if isinstance(type_id, str) and type_id.strip() else None
        if toggle_if_same and new is not None and new == self.current_type:
            new = None
        if new == self.current_type:
            return
        self.current_type = new
        self._app_state.set_filters(self.current_department, self.current_type)
        # When on Inbox, keep page filter in sync and restore tree for that type if we had one open.
        if self._sidebar.current_context() == "Inbox" and self._inbox_page_widget is not None:
            self._inbox_page_widget.set_type_filter(self.current_type or "")
            self._restore_inbox_date_folder_state()
        # When on Outbox, same: restore tree for that type if we had a date folder open.
        if self._sidebar.current_context() == "Outbox" and self._outbox_page_widget is not None:
            self._outbox_page_widget.set_type_filter(self.current_type or "")
            self._restore_outbox_date_folder_state()
        self.typeChanged.emit(new)

    def _sync_filter_state_from_sidebar(self) -> None:
        """
        Keep centralized filter state in sync with the SidebarWidget selection
        when switching pages (Assets vs Shots) where SidebarWidget restores per-page state.
        For Inbox we do not clear type (source = Client/Freelancer); we pull sidebar type
        into our state without emitting, so _reload_main_view sees a valid source and
        no second reload wipes items.
        """
        ctx = self._sidebar.current_context()
        if ctx not in ("Assets", "Shots"):
            if ctx == "Inbox":
                # Sync sidebar → local state only (no emit) so _reload_main_view has a valid source filter.
                filters = self._sidebar.filters()
                t = filters.current_type()
                d = filters.current_department()
                if t is not None:
                    self.current_type = t
                    self._app_state.set_filters(self.current_department, self.current_type)
                if d is not None:
                    self.current_department = d
                    self._app_state.set_filters(self.current_department, self.current_type)
            elif ctx == "Outbox":
                filters = self._sidebar.filters()
                t = filters.current_type()
                if t is not None:
                    self.current_type = t
                    self._app_state.set_filters(self.current_department, self.current_type)
            elif ctx == "Project Guide":
                filters = self._sidebar.filters()
                d = filters.current_department()
                if d is not None:
                    self.current_department = d
                    self._app_state.set_filters(self.current_department, self.current_type)
            else:
                self._set_current_department(None, toggle_if_same=False)
                self._set_current_type(None, toggle_if_same=False)
            return
        filters = self._sidebar.filters()
        new_dept = filters.current_department()
        new_type = filters.current_type()
        # Đồng bộ trực tiếp trên main window để tránh desync khi controller bỏ qua signal
        # (vd: controller.current_type vẫn None nhưng main window giữ "client" từ Inbox).
        self.current_department = new_dept if isinstance(new_dept, str) and new_dept.strip() else None
        self.current_type = new_type if isinstance(new_type, str) and new_type.strip() else None
        self._app_state.set_filters(self.current_department, self.current_type)
        self._controller.sync_filter_state(
            department=new_dept,
            type_id=new_type,
        )
        self._set_main_view_type()

    def _current_type_name(self) -> str | None:
        if self.current_type is None:
            return None
        key = self._norm(self.current_type)
        # Prefer exact id match first, then normalized lookup fallback.
        return self._type_name_by_id.get(self.current_type) or self._type_name_by_id.get(key) or self.current_type

    def _sync_primary_action(self) -> None:
        context = self._sidebar.current_context()
        if context == "Projects":
            enabled = self._workspace_root is not None
            tooltip = None if enabled else "Open Workspace… in Settings → App → Workspace to create a new project"
            self._main_view.set_primary_action(label="New Project", enabled=enabled, tooltip=tooltip)
            self._main_view.set_browser_context("project")
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
        context = self._sidebar.current_context()
        if context == "Projects":
            self._new_project()
            return
        if context == "Shots":
            self._create_shot()
            return
        if context == "Assets":
            self._create_asset()
            return
        return

    def _sync_top_bar(self) -> None:
        self._sidebar.set_projects(
            self._workspace_projects,
            current_root=self._project_root,
            status_by_root=self._workspace_project_status,
        )
        self._sidebar_compact.set_projects(
            self._workspace_projects,
            current_root=self._project_root,
            status_by_root=self._workspace_project_status,
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
        Guarded delete (asset/shot only):
        - Requires file watcher to be paused (toggle in top bar)
        - Confirmation requires typing exact folder name
        - Deletes folder recursively from disk
        - On success: update in-memory index and app_state, clear inspector if needed, refresh UI (NO rescan)
        - On failure: show notification (e.g. file in use on Windows)
        """
        if self._project_index is None:
            return
        if item.kind.value not in ("asset", "shot"):
            return
        if not self._watcher_manually_disabled:
            notification_service.warning(
                "Pause the file watcher (click the eye icon in the top bar) before deleting."
            )
            return

        path = item.path
        name = path.name
        kind_label = "Asset" if item.kind.value == "asset" else "Shot"

        dialog = DeleteConfirmDialog(kind_label=kind_label, folder_name=name, absolute_path=path, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        try:
            if not path.exists():
                return
        except OSError:
            return

        try:
            shutil.rmtree(path)
        except (OSError, PermissionError) as e:
            logging.warning("Delete failed: %s", e)
            notification_service.error(f"Delete failed: {e}. Close any app using files in this folder and try again.")
            return
        except Exception as e:
            logging.exception("Delete failed unexpectedly: %s", e)
            notification_service.error(f"Delete failed: {e}")
            return

        # Clear inspector and thumbnails for deleted path before reload so nothing accesses the removed folder.
        cur = self._main_view.selected_view_item()
        if cur is not None and getattr(cur, "path", None) == path:
            self._inspector.set_item(None)
        self._app_state.invalidate_thumbnails([str(path)])

        self._project_index = build_project_index(self._project_index.root)
        self._app_state.update_assets(list(self._project_index.assets))
        self._app_state.update_shots(list(self._project_index.shots))
        self._app_state.commit_immediate()
        self._sidebar.set_project_index(self._project_index)
        self._reload_main_view()
        self._sync_primary_action()
        notification_service.success(f"Deleted {kind_label} '{name}'.")

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

        try:
            work_file_renames = prepare_work_file_renames(
                project_root=self._project_root,
                asset_path=old_path,
                new_name=dlg.final_name(),
            )
        except (OSError, ValueError, FileNotFoundError) as e:
            logging.warning("Prepare rename failed: %s", e)
            notification_service.error(f"Rename failed: {e}")
            return

        try:
            result = rename_asset(
                project_root=self._project_root,
                asset_path=old_path,
                new_name=dlg.final_name(),
                work_file_renames=work_file_renames,
            )
        except (OSError, PermissionError, ValueError, FileExistsError) as e:
            logging.warning("Rename asset failed: %s", e)
            if getattr(e, "winerror", None) == 5:
                notification_service.error(
                    "Rename failed: Access denied. The folder may be in use by Dropbox or another app. "
                    "Try pausing sync for this folder, or rename it in Explorer and refresh the project."
                )
            else:
                notification_service.error(f"Rename failed: {e}")
            return
        except Exception as e:
            logging.exception("Rename asset failed unexpectedly: %s", e)
            notification_service.error(f"Rename failed: {e}")
            return

        new_path = result.new_path

        # Clear inspector if it points at old or new path (will be reloaded by selection).
        cur = self._main_view.selected_view_item()
        if cur is not None and getattr(cur, "path", None) in (old_path, new_path):
            self._inspector.set_item(None)

        # Invalidate thumbnails for both ids (ids are absolute path strings).
        self._app_state.invalidate_thumbnails([str(old_path), str(new_path)])

        # Refresh index/state; simplest is rebuild index (consistent with delete).
        self._project_index = build_project_index(self._project_index.root)
        self._app_state.update_assets(list(self._project_index.assets))
        self._app_state.update_shots(list(self._project_index.shots))
        self._app_state.commit_immediate()
        self._sidebar.set_project_index(self._project_index)
        self._reload_main_view()
        self._sync_primary_action()
        self._update_fs_watcher_paths()

        # Keep selection on renamed asset if it exists in the refreshed index.
        try:
            if new_path.exists():
                self._app_state.set_selection(str(new_path))
        except Exception:
            pass

        notification_service.success(f"Renamed Asset '{old_path.name}' → '{new_path.name}'.")

    def _restore_workspace_root(self) -> None:
        path = self._settings.value("workspace/root", "", str)
        self._apply_workspace_root(path or None, save=False)

    def _restore_project_root(self) -> None:
        path = self._settings.value("project/root", "", str)
        self._apply_project_root(path or None, save=False)

    def _restore_sidebar_context(self) -> None:
        """Restore last selected nav page (Assets/Shots/Inbox/Projects/Outbox) from QSettings."""
        _valid = ("Assets", "Shots", "Inbox", "Project Guide", "Projects", "Outbox")
        ctx = (self._settings.value("ui/sidebar_context", "Assets", str) or "Assets").strip()
        if ctx in _valid:
            self._sidebar.set_current_context(ctx)

    def _on_context_switched(self, context_name: str) -> None:
        # Trigger: user switches between top-level contexts.
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
            if context_name not in ("Inbox", "Project Guide", "Outbox"):
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
                # Sync filter state first so _reload_main_view uses correct (page, dept, type).
                self._sync_filter_state_from_sidebar()
                # Clear so diff application does not mix with previous context data.
                self._main_view.clear()
                # Scan in background to avoid blocking UI when switching between Assets/Shots.
                self._submit_rescan_task()
                self._reload_main_view()
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
                    self._content_stack.addWidget(self._inbox_page_widget)
                self._inbox_page_widget.set_project_root(self._project_root)
                self._inbox_page_widget.set_type_filter(self._sidebar.filters().current_type() or "")
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
                    self._content_stack.addWidget(self._reference_page_widget)
                self._reference_page_widget.set_project_root(self._project_root)
                self._reference_page_widget.set_department(self._sidebar.filters().current_department() or "reference")
                self._sidebar.filters().set_tag_item_tags(self._reference_page_widget.get_item_tags())
                self._content_stack.setCurrentWidget(self._reference_page_widget)
                self._inspector.set_inbox_tree_preview(None)
            elif context_name == "Outbox":
                self._sync_filter_state_from_sidebar()
                if self._outbox_page_widget is None:
                    self._outbox_page_widget = OutboxPageWidget(self)
                    self._outbox_page_widget.tree_selection_changed.connect(self._on_outbox_tree_selection_changed)
                    self._outbox_page_widget.open_folder_requested.connect(self._on_outbox_open_folder_requested)
                    self._outbox_page_widget.drop_requested.connect(self._on_outbox_drop_requested)
                    self._outbox_page_widget.import_requested.connect(self._on_outbox_import_requested)
                    self._outbox_page_widget.date_folder_entered.connect(self._on_outbox_date_folder_entered)
                    self._content_stack.addWidget(self._outbox_page_widget)
                self._outbox_page_widget.set_project_root(self._project_root)
                self._outbox_page_widget.set_type_filter(self._sidebar.filters().current_type() or "")
                self._content_stack.setCurrentWidget(self._outbox_page_widget)
                self._inspector.set_inbox_distribute_paths([], None, None)
                self._inspector.set_inbox_tree_preview(None)
            elif context_name == "Projects":
                self._reload_main_view()
            else:
                self._main_view.clear()
                self._main_view.set_empty_override(self._empty_message_for_context(context_name))

            self._sync_primary_action()
            self._sync_filter_state_from_sidebar()
        finally:
            self._context_switch_in_progress = False

    def _on_context_clicked(self, context_name: str) -> None:
        # Reload current view (click on already-selected nav item). No page-change toast here
        # to avoid duplicate with context_changed when user clicks a different page.
        # Spec: click reloads Main View. (No autoscan trigger unless it was a switch.)
        # Note: Switching to Inbox emits both context_changed and context_clicked; we must not clear Inbox here.
        self._context_switch_in_progress = True
        try:
            self._main_view.set_context_title(context_name)
            self._entered_parent = None
            try:
                self._main_view.clear_selection()
            except Exception:
                pass
            self._inspector.set_item(None)

            if context_name in ("Assets", "Shots", "Projects"):
                if context_name in ("Assets", "Shots"):
                    # Clicking an already-selected page should NOT wipe the list/grid.
                    # If a background scan hasn't produced a ProjectIndex yet, clearing here
                    # can leave the view empty until another event forces a reload.
                    self._sync_filter_state_from_sidebar()
                self._reload_main_view()
            elif context_name == "Inbox":
                self._sync_filter_state_from_sidebar()
                self._reload_main_view()
            elif context_name == "Outbox":
                self._sync_filter_state_from_sidebar()
                self._reload_main_view()
            elif context_name == "Project Guide":
                self._sync_filter_state_from_sidebar()
                if self._reference_page_widget is not None:
                    self._reference_page_widget.set_project_root(self._project_root)
                    self._reference_page_widget.set_department(self._sidebar.filters().current_department() or "reference")
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
                return "Open Workspace… in Settings → App → Workspace."
            if not self._workspace_projects:
                return "No projects found in this workspace"
            return "Select a project using the project switcher in the top bar."
        return f"{context_name} is not available yet."

    def _on_valid_selection_changed(self, has_selection: bool) -> None:
        if getattr(self, "_context_switch_in_progress", False) or getattr(self, "_filter_switch_in_progress", False):
            # During context switches we intentionally keep Inspector cleared and avoid re-entrant UI updates.
            self._inspector.set_item(None)
            return
        if not has_selection:
            self._inspector.set_item(None)
            return

        selected = self._main_view.selected_view_item()
        self._inspector.set_item(selected, active_department_hint=self.current_department)

    def _asset_passes_filter(self, asset: Asset | None) -> bool:
        if asset is None:
            return False
        return len(self.filter_assets([asset], self.current_department, self.current_type)) > 0

    def _on_app_state_assets_changed(self, added: list, removed: list, updated: list) -> None:
        _dcc_log = logging.getLogger("monostudio.dcc_debug")
        _dcc_log.debug("assetsChanged signal added=%s removed=%s updated=%s", added, removed, updated)
        if self._sidebar.current_context() != "Assets":
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
        def _restore():
            self._app_state.set_selection(_sid)
        QTimer.singleShot(0, _restore)

    def _on_app_state_shots_changed(self, added: list, removed: list, updated: list) -> None:
        if self._sidebar.current_context() != "Shots":
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
        def _restore():
            self._app_state.set_selection(_sid)
        QTimer.singleShot(0, _restore)

    def _on_app_state_filters_changed(self) -> None:
        ctx = self._sidebar.current_context()
        if ctx in ("Assets", "Shots"):
            self._reload_main_view()

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
            cur = self._main_view.selected_view_item()
            if cur is not None and str(cur.path) in ids_set:
                self._inspector.update_thumbnail_for_current()
        except Exception:
            pass

    def _on_worker_task_finished(self, category: str, result: object, error: str | None) -> None:
        """Forward worker results to AppState only; never update UI directly."""
        _dcc_log = logging.getLogger("monostudio.dcc_debug")
        if error is not None:
            logging.getLogger(__name__).warning("Worker task %s failed: %s", category, error)
            _dcc_log.debug("worker taskFinished category=%s error=%s", category, error)
            if category == "inspector_preview_thumb":
                self._inspector.clear_preview_loading()
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
            self._sidebar.set_project_index(result)
            # Full rescan: drop in-memory thumbs so grid/inspector reload from disk (mtime / new sources).
            try:
                self._thumbnail_manager.clear_memory_cache()
            except Exception:
                pass
            self._reload_main_view()
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
                self._sidebar.set_project_index(self._project_index)
                # So new assets/shots (e.g. just created) get their paths watched
                self._update_fs_watcher_paths()
            # Grid already updated via assetsChanged/shotsChanged from commit_immediate(); full reload would clear+repopulate and cause flicker.
            ctx = self._sidebar.current_context()
            if ctx not in ("Assets", "Shots"):
                self._reload_main_view()
            QTimer.singleShot(0, self._main_view.repaint_tile_and_list_views)
            self._sync_primary_action()
            self._sync_top_bar()

    def _apply_project_root(self, folder: str | None, *, save: bool) -> None:
        # No validation (per rules). Store path and reload UI.
        if save:
            self._settings.setValue("project/root", folder or "")
        try:
            from monostudio.core.crash_recovery import set_crash_context
            set_crash_context(last_project_path=folder or "")
        except Exception:
            pass

        self._project_root = Path(folder) if folder else None
        self._controller.set_project_root(self._project_root)
        self._sidebar.filters().set_project_root(self._project_root)
        self._sync_pipeline_preset_metadata_ui()
        self._main_view.set_project_root(folder)
        self._main_view.set_empty_override(None)
        self._inspector.set_project_root(self._project_root)

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
        else:
            self._inspector.set_department_registry(None)
            self._inspector.set_department_icon_map({})
            self._inspector.set_type_short_name_map({})

        # Reload index if root set; otherwise clear.
        if self._project_root is None:
            self._project_index = None
            self._app_state.clear_project_data()
            pending_clear_all()
            self._entered_parent = None
            self._main_view.clear()
        else:
            try:
                self._project_index = build_project_index(self._project_root)
                self._entered_parent = None
                self._app_state.update_assets(list(self._project_index.assets))
                self._app_state.update_shots(list(self._project_index.shots))
                self._app_state.commit_immediate()
                self._reload_main_view()
            except Exception as e:
                failed_path = str(self._project_root)
                logging.exception("Failed to load project at %s", failed_path)
                self._project_index = None
                self._project_root = None
                self._app_state.clear_project_data()
                self._entered_parent = None
                self._main_view.clear()
                self._sidebar.filters().set_project_root(None)
                self._sync_pipeline_preset_metadata_ui()
                self._sidebar.set_project_index(None)
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
                    f"Could not open project:\n{failed_path}\n\n{e}\n\nOpen Settings to choose another project.",
                )
                return
        self._sidebar.set_project_index(self._project_index)
        self._update_fs_watcher_paths()

        # After selecting a folder: keep layout stable, show neutral empty-state.
        self._inspector.set_item(None)

        self._refresh_recent_tasks()
        self._sync_primary_action()
        self._sync_top_bar()

    def _refresh_recent_tasks(self) -> None:
        tasks = self._recent_tasks_store.get_for_project(self._project_root) if self._project_root else []
        self._sidebar.set_recent_tasks(tasks)
        self._sidebar_compact.set_recent_tasks(tasks)

    def _apply_workspace_root(self, folder: str | None, *, save: bool) -> None:
        # No validation. Read-only discovery.
        if save:
            self._settings.setValue("workspace/root", folder or "")

        self._workspace_root = Path(folder) if folder else None
        self._workspace_projects = discover_projects(self._workspace_root) if self._workspace_root else []
        self._workspace_project_status = {}
        for proj in self._workspace_projects:
            try:
                stats = read_project_quick_stats(proj.root)
                self._workspace_project_status[str(proj.root)] = stats.status
            except Exception:
                continue
        self._sidebar.set_projects_count(len(self._workspace_projects) if self._workspace_root is not None else None)

        if not self._workspace_projects:
            self._main_view.set_empty_override("No projects found in this workspace")
        else:
            self._main_view.set_empty_override(None)

        self._sync_primary_action()
        self._sync_top_bar()

    def _rescan_project(self) -> None:
        # Synchronous rescan (e.g. context switch); use when UI must have data immediately.
        if self._project_root is None:
            self._project_index = None
            self._app_state.clear_project_data()
            self._sidebar.set_project_index(None)
            self._update_fs_watcher_paths()
            return
        self._project_index = build_project_index(self._project_root)
        self._sidebar.set_project_index(self._project_index)
        self._app_state.update_assets(list(self._project_index.assets))
        self._app_state.update_shots(list(self._project_index.shots))
        self._app_state.commit_immediate()
        # So watcher includes new/updated asset and shot paths (incl. nested dept work dirs).
        self._update_fs_watcher_paths()

    def _submit_rescan_task(self) -> None:
        """Submit a filesystem scan to WorkerManager; result is forwarded to AppState in _on_worker_task_finished."""
        if self._project_root is None:
            return
        root = self._project_root

        def run() -> ProjectIndex:
            return build_project_index(root)

        task = WorkerTask("filesystem_scan", run, manager=self._worker_manager)
        self._worker_manager.submit_task(task, category="filesystem_scan", replace_existing=True)

    def _on_fs_batch_ready(self, asset_ids: list, shot_ids: list, type_folders: list) -> None:
        """Submit incremental scan for fs watcher batch; never full rescan."""
        _watcher_log = logging.getLogger("monostudio.fs_watcher")
        if self._project_root is None:
            return
        root = self._project_root
        a_ids = [x for x in (asset_ids or []) if isinstance(x, str) and x.strip()]
        s_ids = [x for x in (shot_ids or []) if isinstance(x, str) and x.strip()]
        t_folders = [x for x in (type_folders or []) if isinstance(x, str) and x.strip()]
        if not a_ids and not s_ids and not t_folders:
            return
        _watcher_log.debug("fs_watcher batch_ready -> incremental_scan asset_ids=%s shot_ids=%s type_folders=%s", len(a_ids), len(s_ids), len(t_folders))

        def run() -> tuple[list[Asset], list[Shot], list[str], list[str]]:
            return run_incremental_scan(root, a_ids, s_ids, t_folders)

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

    def _watcher_paths_for_asset(self, root: Path, asset: Asset, *, max_paths: int = 2000) -> list[str]:
        """Return the list of watcher paths for a single asset (dept dirs, work, publish). Used after rename to add only the new asset's paths."""
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
        return to_add

    def _update_fs_watcher_paths(self) -> None:
        """Set or clear watched paths and collector state from current project root.
        Watches project root, assets/, shots/, and each asset/shot directory so changes
        inside entity folders (e.g. work/) are detected on Windows (no recursive watch).
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
        use_dcc_folders = read_use_dcc_folders(root)
        try:
            _dcc_reg = get_default_dcc_registry()
        except Exception:
            _dcc_reg = None

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

    def _on_search_query_changed(self, query: str) -> None:
        self.current_search_query = (query or "").strip()
        self._reload_main_view()

    def _reload_main_view(self) -> None:
        context = self._sidebar.current_context()
        if context == "Inbox" and self._inbox_page_widget is not None:
            self._inbox_page_widget.set_project_root(self._project_root)
            self._inbox_page_widget.set_type_filter(self._sidebar.filters().current_type() or "")
            return
        if context == "Outbox" and self._outbox_page_widget is not None:
            self._outbox_page_widget.set_project_root(self._project_root)
            self._outbox_page_widget.set_type_filter(self._sidebar.filters().current_type() or "")
            return
        if context == "Project Guide" and self._reference_page_widget is not None:
            self._reference_page_widget.set_project_root(self._project_root)
            self._reference_page_widget.set_department(self._sidebar.filters().current_department() or "reference")
            self._sidebar.filters().set_tag_item_tags(self._reference_page_widget.get_item_tags())
            return
        # Placeholder for search input (context-aware).
        placeholders = {"Assets": "Search assets", "Shots": "Search shots", "Projects": "Search projects"}
        self._main_view.set_search_placeholder(placeholders.get(context, "Search…"))
        items: list[ViewItem] = []

        # Projects context: show workspace discovery results (read-only).
        if context == "Projects":
            self._main_view.set_active_department(None)
            self._main_view.set_selected_asset_type(None)
            for proj in self._workspace_projects:
                stats: ProjectQuickStats | None
                try:
                    stats = read_project_quick_stats(proj.root)
                except Exception:
                    stats = None
                items.append(
                    ViewItem(
                        kind=ViewItemKind.PROJECT,
                        name=proj.name,
                        type_badge="project",
                        path=proj.root,
                        departments_count=None,
                        ref=stats,
                    )
                )
            items = self._apply_search_filter(items, self.current_search_query)
            if not items and (self.current_search_query or "").strip():
                self._main_view.set_empty_override('No matches for "' + self.current_search_query.strip() + '"')
            else:
                self._main_view.set_empty_override(None if items else "No projects found in this workspace")
            self._main_view.set_items(items)
            return

        if self._project_index is None:
            # Keep whatever is currently shown (avoid "click -> empty list") while scan/index is pending.
            self._main_view.set_empty_override("Scanning project…")
            return
        else:
            # Clear any loading override once we have an index.
            self._main_view.set_empty_override(None)

        if context == "Assets":
            # Sync type from sidebar so filter matches visible selection (fixes desync after Assets→Shots→Assets then type change).
            _type_from_sidebar = self._sidebar.filters().current_type()
            if _type_from_sidebar is not None:
                self.current_type = _type_from_sidebar
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
            self._set_main_view_department()
            self._set_main_view_type()
            self._inspector.set_show_publish(self._main_view.get_show_publish())
        else:
            self._main_view.set_active_department(None)
            self._main_view.set_selected_asset_type(None)
        # Preserve selection if still in list (Option C; no defer to avoid flicker).
        current_id = self._app_state.selection_id()
        preserve = None
        if current_id and items:
            for vi in items:
                if self._path_matches_selection(vi.path, current_id):
                    preserve = current_id
                    break
        self._main_view.set_items(items, preserve_selection_id=preserve)
        self._sidebar.set_project_index(self._project_index)
        if preserve is None:
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
                self._controller.smart_open(item=item.ref, force_dialog=False, parent=self)
                self._refresh_recent_tasks()
                notification_service.success(f"Opened Asset '{item.ref.name}'.")
            except Exception as e:
                logging.warning("DCC launch failed (asset): %s", e, exc_info=True)
                QMessageBox.critical(self, "Open DCC", str(e))
            return
        if item.kind == ViewItemKind.SHOT and isinstance(item.ref, Shot):
            try:
                self._controller.smart_open(item=item.ref, force_dialog=False, parent=self)
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
        # Already in tree view (same type) → state kept by widget, nothing to restore
        if self._inbox_page_widget.is_showing_tree():
            return False
        source_type = (self._sidebar.filters().current_type() or "client").strip().lower()
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
        self._inbox_page_widget._enter_date_folder(path)
        return True

    def _outbox_date_folder_settings_key(self, source_type: str) -> str:
        key = (source_type or "client").strip().lower()
        if key not in ("client", "freelancer"):
            key = "client"
        return f"outbox/last_date_folder_path/{key}"

    def _outbox_restore_split_key(self) -> str:
        return "outbox/restore_split_view"

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
        """Restore Outbox date folder (tree) for current type when user had it open. Returns True if restored."""
        if not self._outbox_page_widget or not self._project_root:
            return False
        raw = self._settings.value(self._outbox_restore_split_key(), True)
        if raw in (False, "false", "0", 0):
            return False
        if self._outbox_page_widget.is_showing_tree():
            return False
        source_type = (self._sidebar.filters().current_type() or "client").strip().lower()
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
        self._outbox_page_widget._enter_date_folder(path)
        return True

    def _on_inbox_tree_selection_changed(self, path) -> None:
        self._inspector.set_inbox_tree_preview(Path(path) if path else None)

    def _on_tag_filter_changed(self, tag_id) -> None:
        """Sidebar tag filter changed: forward to Project Guide tree proxy."""
        if self._reference_page_widget is not None:
            self._reference_page_widget.set_tag_filter(tag_id)

    def _on_tag_definitions_changed(self) -> None:
        """Tag definitions were modified (add/rename/delete/recolor). Reload on tree pane."""
        if self._reference_page_widget is not None:
            self._reference_page_widget.reload_tag_definitions()

    def _on_reference_tree_selection_changed(self, path) -> None:
        """Reference page: show file preview in inspector (same as Inbox tree selection)."""
        self._inspector.set_inbox_tree_preview(Path(path) if path else None)

    def _on_reference_item_tags_changed(self) -> None:
        """Tags were updated in Project Guide tree; refresh sidebar tag counts."""
        if self._reference_page_widget is not None:
            self._sidebar.filters().set_tag_item_tags(self._reference_page_widget.get_item_tags())

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

    def _on_reference_drop_requested(self, paths: list) -> None:
        """Files/folders dropped onto Project Guide page: copy to project_guide/<current_department>/ (fallback when not over tree)."""
        logging.debug("Project Guide _on_reference_drop_requested: paths=%s", [str(p) for p in (paths or [])])
        if not paths or not self._project_root:
            return
        path_list = [Path(p) for p in paths if p and Path(p).exists()]
        if not path_list:
            return
        dept = (self._sidebar.filters().current_department() or "reference").strip().lower()
        struct_reg = StructureRegistry.for_project(self._project_root)
        guide_folder = struct_reg.get_folder("project_guide")
        dest_root = Path(self._project_root) / guide_folder / dept
        dest_root.mkdir(parents=True, exist_ok=True)
        added = 0
        for src in path_list:
            try:
                dest = dest_root / src.name
                if src.is_dir():
                    if dest.exists():
                        for item in src.iterdir():
                            d = dest / item.name
                            if item.is_file():
                                shutil.copy2(item, d)
                            else:
                                shutil.copytree(item, d)
                    else:
                        shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
                added += 1
            except OSError as e:
                logging.warning("Project Guide copy failed for %s: %s", src, e)
        if added > 0:
            if self._reference_page_widget is not None:
                self._reference_page_widget.set_project_root(self._project_root)
            notification_service.success(f"Added {added} item{'s' if added != 1 else ''} to Project Guide.")

    def _on_reference_open_folder_requested(self, path) -> None:
        if path:
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path))))
            except Exception:
                pass

    def _on_inbox_open_folder_requested(self, path) -> None:
        if path:
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            except Exception:
                pass

    def _on_outbox_tree_selection_changed(self, path) -> None:
        """Outbox: inspector preview only (no distribute)."""
        self._inspector.set_inbox_tree_preview(Path(path) if path else None)

    def _on_outbox_open_folder_requested(self, path) -> None:
        if path:
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            except Exception:
                pass

    def _on_outbox_import_requested(self, _date_path=None) -> None:
        """Import (header or context menu): open file dialog, then Outbox drop dialog."""
        if not self._project_root:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Import to Outbox", "", "All Files (*)")
        if not files:
            return
        path_list = [Path(f) for f in files if f and Path(f).exists()]
        if not path_list:
            return
        self._on_outbox_drop_requested(path_list)

    def _on_outbox_drop_requested(self, paths: list) -> None:
        """Files/folders dropped onto Outbox page: open dialog, then copy into outbox/<source>/<date>/."""
        if not paths or not self._project_root:
            return
        path_list = [Path(p) for p in paths if p and Path(p).exists()]
        if not path_list:
            return
        initial_source = (self._sidebar.filters().current_type() or "").strip().lower()
        if initial_source not in ("client", "freelancer"):
            initial_source = "client"
        dialog = InboxDropDialog(path_list, self._project_root, initial_source, self, target="outbox")
        if dialog.exec() != QDialog.Accepted:
            return
        source, date_str, description = dialog.result_values()
        if not date_str:
            return
        added = 0
        for p in path_list:
            try:
                result = add_to_outbox(self._project_root, p, source, date_str, description)
                if result is not None:
                    added += 1
            except Exception as e:
                logging.warning("Add to outbox failed for %s: %s", p, e)
        if added > 0:
            self._reload_main_view()
            notification_service.success(f"Added {added} item{'s' if added != 1 else ''} to Outbox.")

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
        type_filter = (self._sidebar.filters().current_type() or "client").strip().lower()
        if type_filter not in ("client", "freelancer"):
            type_filter = "client"
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

    def _on_inbox_drop_requested(self, paths: list) -> None:
        """Files/folders dropped onto Inbox page: open InboxDropDialog, then copy into inbox/<source>/<date>/."""
        if not paths or not self._project_root:
            return
        path_list = [Path(p) for p in paths if p and Path(p).exists()]
        if not path_list:
            return
        initial_source = (self._sidebar.filters().current_type() or "").strip().lower()
        if initial_source not in ("client", "freelancer"):
            initial_source = "client"
        dialog = InboxDropDialog(path_list, self._project_root, initial_source, self)
        if dialog.exec() != QDialog.Accepted:
            return
        source, date_str, description = dialog.result_values()
        if not date_str:
            return
        added = 0
        for p in path_list:
            try:
                result = add_to_inbox(self._project_root, p, source, date_str, description)
                if result is not None:
                    added += 1
            except Exception as e:
                logging.warning("Add to inbox failed for %s: %s", p, e)
        if added > 0:
            self._reload_main_view()
            notification_service.success(f"Added {added} item{'s' if added != 1 else ''} to Inbox.")

    def _on_open_requested(self, item: object) -> None:
        if getattr(self, "_context_switch_in_progress", False) or getattr(self, "_filter_switch_in_progress", False):
            return
        if not isinstance(item, ViewItem):
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            return
        try:
            self._controller.smart_open(item=ref, force_dialog=False, parent=self)
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
            self._controller.smart_open(item=ref, force_dialog=True, force_open_with=True, parent=self)
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
            self._controller.smart_open(item=ref, force_dialog=True, force_create_new=True, parent=self)
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
                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
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
            if not path.exists():
                if (path.name or "").strip().casefold() == "work":
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    return
        except (OSError, TypeError):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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

        failed: list[tuple[Path, str]] = []
        ok_paths: list[Path] = []
        for ep in raw_paths:
            try:
                set_department_status_override(ep, dep, sid)
                ok_paths.append(ep)
            except OSError as e:
                failed.append((ep, str(e)))
            except ValueError:
                pass

        if not ok_paths:
            if failed:
                msg = "\n".join(f"{fp}: {err}" for fp, err in failed[:5])
                if len(failed) > 5:
                    msg += f"\n… (+{len(failed) - 5} more)"
                QMessageBox.warning(self, "Production status", f"Could not save status:\n{msg}")
            return

        if failed:
            msg = "\n".join(f"{fp}: {err}" for fp, err in failed[:5])
            if len(failed) > 5:
                msg += f"\n… (+{len(failed) - 5} more)"
            QMessageBox.warning(
                self,
                "Production status",
                f"Saved {len(ok_paths)} item(s); {len(failed)} failed:\n{msg}",
            )

        dept_reg = DepartmentRegistry.for_project(self._project_root)
        type_reg = TypeRegistry.for_project(self._project_root)
        struct_reg = StructureRegistry.for_project(self._project_root)
        assets_dir = self._project_root / struct_reg.get_folder("assets")
        shots_dir = self._project_root / struct_reg.get_folder("shots")

        current_assets = dict(self._app_state.assets())
        current_shots = dict(self._app_state.shots())

        def same_path(key: str, path_value: Path) -> bool:
            try:
                return Path(key).resolve() == Path(path_value).resolve()
            except OSError:
                return False

        ok_resolved: set[str] = set()
        for ep in ok_paths:
            try:
                ep_r = ep.resolve()
            except OSError:
                ep_r = ep
            ok_resolved.add(str(ep_r))

            try:
                ep_r.relative_to(assets_dir.resolve())
                updated_asset = scan_single_asset(self._project_root, ep_r, dept_reg, type_reg)
            except ValueError:
                updated_asset = None
            updated_shot: Shot | None = None
            if updated_asset is None:
                try:
                    ep_r.relative_to(shots_dir.resolve())
                    updated_shot = scan_single_shot(self._project_root, ep_r, dept_reg)
                except ValueError:
                    pass

            if updated_asset is not None:
                key = next((k for k in current_assets if same_path(k, updated_asset.path)), str(updated_asset.path))
                for k in list(current_assets):
                    if k != key and same_path(k, updated_asset.path):
                        current_assets.pop(k, None)
                current_assets[key] = updated_asset
            if updated_shot is not None:
                key = next((k for k in current_shots if same_path(k, updated_shot.path)), str(updated_shot.path))
                for k in list(current_shots):
                    if k != key and same_path(k, updated_shot.path):
                        current_shots.pop(k, None)
                current_shots[key] = updated_shot

        self._app_state.update_assets(current_assets)
        self._app_state.update_shots(current_shots)
        self._app_state.commit_immediate()
        if self._project_index is not None:
            ca = dict(self._app_state.assets())
            cs = dict(self._app_state.shots())
            self._project_index = ProjectIndex(
                root=self._project_index.root,
                assets=tuple(sorted(ca.values(), key=lambda x: (x.asset_type, x.name))),
                shots=tuple(sorted(cs.values(), key=lambda x: x.name)),
            )
            self._sidebar.set_project_index(self._project_index)

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
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_show_publish_changed(self, show_publish: bool) -> None:
        self._inspector.set_show_publish(show_publish)
        if self._main_view.has_valid_selection():
            item = self._main_view.selected_view_item()
            if item is not None:
                self._inspector.set_item(item, active_department_hint=self.current_department)

    def _on_recent_task_clicked(self, task: object) -> None:
        from monostudio.ui_qt.recent_tasks_store import RecentTask
        if not isinstance(task, RecentTask):
            return
        # Switch to Assets or Shots context.
        ctx = "Assets" if task.item_type == "asset" else "Shots"
        self._sidebar.set_current_context(ctx)
        self._sidebar_compact.set_current_context(ctx)
        # Set department filter: sync controller first, then sidebar with emit=False so clicking
        # two tasks with the same department does not trigger controller's "same dept → toggle off".
        self._controller.sync_filter_state(department=task.department, type_id=self.current_type)
        self._sidebar.filters().set_selected_department(task.department, emit=False)
        # Select the item in main view.
        self._main_view.select_item_by_path(Path(task.item_path))
        # Sidebar toast for task selection (single-click).
        # Anchor vertically to the recent task row instead of raw cursor Y.
        try:
            tasks_list = getattr(self._sidebar, "_tasks_list", None)
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
        """Show full filter panel (Departments & Types) in a popup when sidebar is compact.
        Same as noti button: if popup is open, close it; if just closed (grace), don't reopen."""
        if self._sidebar_stack.currentIndex() != 1:
            return
        # Toggle: if popup is visible, close it and return
        if self._compact_filter_popup is not None and self._compact_filter_popup.isVisible():
            self._compact_filter_popup.close()
            return
        if (time.monotonic() - self._compact_filter_popup_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        filter_widget = self._sidebar.take_filters_center()
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
            self._sidebar.restore_filters_center(filter_widget)
            self._compact_filter_popup_closed_at = time.monotonic()
            self._compact_filter_popup = None
            btn = getattr(self._sidebar_compact, "_filter_btn", None)
            if btn is not None:
                QTimer.singleShot(0, lambda: self._sidebar_compact._clear_tool_button_hover(btn))

        popup = _FilterPopupFrame(self, _on_filter_popup_hidden)
        popup.setObjectName("SidebarCompactFilterPopup")
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.FramelessWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground, False)
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(filter_widget)
        popup.setMinimumWidth(260)
        popup.setMinimumHeight(280)
        # Position below the filter icon (compact sidebar is on the left)
        btn = getattr(self._sidebar_compact, "_filter_btn", None)
        if btn is not None:
            pos = btn.mapToGlobal(btn.rect().bottomLeft())
            popup.move(pos.x(), pos.y() + 4)
        else:
            popup.move(self._sidebar_container.mapToGlobal(self._sidebar_container.rect().topRight()).x() + 4, 80)
        self._compact_filter_popup = popup
        popup.show()

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
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
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
        return get_app_base_path() / "monostudio_data" / "config" / "app_settings.json"

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
                QTimer.singleShot(0, self._apply_restore_maximized)
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
        Save sidebar nav page (Assets/Shots/Inbox/...) to QSettings.
        Save splitter sizes to app_settings.json.
        Silent failure on IO errors.
        """
        try:
            self._settings.setValue("ui/sidebar_context", self._sidebar.current_context())
            self._persist_panel_layout_prefs()
        except Exception:
            pass
        path = self._app_settings_path()
        # Persist "full" layout (sizes when both panels visible) so restore doesn't get 0 for hidden panels.
        ms_cur = self._main_splitter.sizes()
        if len(ms_cur) >= 1 and ms_cur[0] == 0:
            main_sizes = self._main_splitter_sizes_restore
        elif self._sidebar_stack.currentIndex() == 1:
            main_sizes = self._main_splitter_sizes_restore
        else:
            main_sizes = ms_cur
        content_sizes = self._content_splitter_sizes_restore if not self._inspector.isVisible() else self._content_splitter.sizes()
        payload = {
            "window_geometry_b64": base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
            "window_maximized": self.isMaximized(),
            "window_always_on_top": self._window_always_on_top,
            "main_splitter_sizes": main_sizes,
            "content_splitter_sizes": content_sizes,
        }
        try:
            from monostudio.core.atomic_write import atomic_write_text
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            atomic_write_text(path, content, encoding="utf-8")
        except OSError:
            pass
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
        context = self._sidebar.current_context()

        if context == "Projects":
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
            chosen = menu.exec(global_pos)
            if chosen == act_refresh:
                self._apply_workspace_root(str(self._workspace_root), save=False)
                self._reload_main_view()
                return
            if chosen is not None and chosen == new_proj:
                self._new_project()
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

        if context == "Assets":
            create_asset = menu.addAction(lucide_icon("box", size=16, color_hex=MONOS_COLORS["text_label"]), "Create Asset…")
        elif context == "Shots":
            create_shot = menu.addAction(lucide_icon("clapperboard", size=16, color_hex=MONOS_COLORS["text_label"]), "Create Shot…")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == act_refresh:
            self._on_refresh_requested()
            return
        if create_asset is not None and chosen == create_asset:
            self._create_asset()
        if create_shot is not None and chosen == create_shot:
            self._create_shot()

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
        dept_reg = DepartmentRegistry.for_project(self._project_root)
        struct_reg = StructureRegistry.for_project(self._project_root)
        type_folder = type_reg.get_type_folder(asset_type)
        target = self._project_root / struct_reg.get_folder("assets") / type_folder / asset_name
        if target.exists():
            return

        created: list[Path] = []
        try:
            use_dcc_folders = read_use_dcc_folders(self._project_root)
            to_create: list[Path] = [target]
            for d in departments:
                # Nested: relative path can be multi-segment (e.g. 01_modelling/01_sculpt) when mapping has parent.
                dept_folder = dept_reg.get_department_relative_path(d, "asset")
                dept_dir = target / dept_folder
                to_create.append(dept_dir)
                if create_subfolders:
                    # Only department + work + publish. DCC subfolders (dept/<dcc>/work) are created on demand when user does "Create New…".
                    if not use_dcc_folders:
                        to_create.append(dept_dir / "work")
                    to_create.append(dept_dir / "publish")

            for p in to_create:
                try:
                    p.mkdir(parents=True, exist_ok=False)
                    created.append(p)
                except FileExistsError:
                    # Folder already exists: skip silently.
                    continue
        except OSError:
            # Best-effort rollback inside target only (no dialogs).
            for p in reversed(created):
                try:
                    p.rmdir()
                except OSError:
                    pass
            return

        # After creation: rescan so app_state has the new asset; commit_immediate() emits
        # assetsChanged and apply_assets_diff updates the grid incrementally (no full reload = no flicker).
        self._entered_parent = None
        self._rescan_project()
        self._inspector.set_item(None)
        QTimer.singleShot(0, self._main_view.repaint_tile_and_list_views)
        notification_service.success(f"Created Asset '{asset_name}'.")

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

        dept_reg = DepartmentRegistry.for_project(self._project_root)
        use_dcc_folders = read_use_dcc_folders(self._project_root)
        created: list[Path] = []
        try:
            to_create: list[Path] = [target]
            for d in departments:
                # Nested: relative path can be multi-segment (e.g. 01_modelling/01_sculpt) when mapping has parent.
                dept_folder = dept_reg.get_department_relative_path(d, "shot")
                dept_dir = target / dept_folder
                to_create.append(dept_dir)
                if create_subfolders:
                    # Only department + work + publish. DCC subfolders (dept/<dcc>/work) are created on demand when user does "Create New…".
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
            return

        # After creation: rescan so app_state has the new shot; commit_immediate() emits
        # shotsChanged and apply_assets_diff updates the grid incrementally (no full reload = no flicker).
        self._entered_parent = None
        self._rescan_project()
        self._inspector.set_item(None)
        QTimer.singleShot(0, self._main_view.repaint_tile_and_list_views)
        notification_service.success(f"Created Shot '{shot_name}'.")

