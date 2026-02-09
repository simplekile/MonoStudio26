from __future__ import annotations

import base64
import json
import logging
import shutil
from datetime import date
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QFileSystemWatcher, Qt, QSettings, Signal, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget
from qframelesswindow import FramelessMainWindow

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.fs_reader import (
    build_project_index,
    read_use_dcc_folders,
    resolve_work_path,
    run_incremental_scan,
)
from monostudio.core.models import Asset, InboxItem, ProjectIndex, Shot
from monostudio.core.inbox_reader import add_to_inbox, get_inbox_root, scan_inbox
from monostudio.core.type_registry import TypeRegistry
from monostudio.core.workspace_reader import DiscoveredProject, ProjectQuickStats, discover_projects, read_project_quick_stats
from monostudio.core.project_create import create_new_project
from monostudio.core.pipeline_types_and_presets import (
    ensure_pipeline_bootstrap,
    load_pipeline_types_and_presets,
    seed_project_from_user_default,
)
from monostudio.core.clipboard_thumbnail_handler import ClipboardThumbnailHandler
from monostudio.core.item_status import read_item_status, write_item_status
from monostudio.core.pending_create import remove_by_entity, remove_for_entities, clear_all as pending_clear_all
from monostudio.ui_qt.create_entry_dialogs import AddToInboxDialog, CreateAssetDialog, CreateShotDialog
from monostudio.ui_qt.inbox_split_view import InboxSplitView
from monostudio.ui_qt.inspector import InspectorPanel
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.new_project_dialog import NewProjectDialog
from monostudio.ui_qt.settings_dialog import SettingsDialog
from monostudio.ui_qt.sidebar import Sidebar
from monostudio.ui_qt.top_bar import TopBar
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind
from monostudio.ui_qt.delete_confirm_dialog import DeleteConfirmDialog
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


class MainWindow(FramelessMainWindow):
    """
    Phase 0 shell:
    - 3 panels: Sidebar (~15%), Main View (~60%), Inspector (~25%, hidden by default)
    - No filesystem logic
    - No publish logic
    - No database
    """

    departmentChanged = Signal(object)  # str | None
    typeChanged = Signal(object)  # str | None

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MONOS")
        ensure_pipeline_bootstrap()

        # Minimum window size (usability floor).
        self.setMinimumSize(640, 480)
        self.setObjectName("MonosMainWindow")

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
            size_px=384,
            max_memory=200,
        )
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_event_collector = FsEventCollector(self, debounce_ms=300)
        self._fs_watcher.fileChanged.connect(self._fs_event_collector.add_path)
        self._fs_watcher.directoryChanged.connect(self._fs_event_collector.add_path)
        self._fs_event_collector.batchReady.connect(self._on_fs_batch_ready)
        self._entered_parent: Asset | Shot | None = None

        # Centralized filter state (UI-only; no filtering engine yet)
        self.current_department: str | None = None
        self.current_type: str | None = None
        self.current_search_query: str = ""
        meta = load_pipeline_types_and_presets()
        self._type_name_by_id: dict[str, str] = {k: v.name for k, v in meta.types.items()}
        # Type aliases allow robust matching against filesystem folder names:
        # e.g. "environment" may appear as "env" or "Environment" in legacy projects.
        self._type_aliases_by_id: dict[str, set[str]] = {}
        for type_id, t in meta.types.items():
            aliases_raw = [type_id, t.name, t.short_name]
            aliases = {self._norm(a) for a in aliases_raw if isinstance(a, str) and a.strip()}
            if aliases:
                self._type_aliases_by_id[self._norm(type_id)] = aliases

        self._sidebar = Sidebar()
        # Persist sidebar filter selections per page (assets/shots).
        try:
            self._sidebar.filters().set_settings(self._settings)
        except Exception:
            pass
        self._main_view = MainView()
        self._main_view.set_thumbnail_manager(self._thumbnail_manager)
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._main_view)
        self._inbox_split_view: InboxSplitView | None = None
        self._inspector = InspectorPanel()
        self._inspector.set_thumbnail_manager(self._thumbnail_manager)
        self._inspector.setMinimumWidth(240)
        self._top_bar = TopBar(self)
        self._top_bar.setFixedHeight(56)  # so FramelessMainWindow resize keeps height
        self.setTitleBar(self._top_bar)  # replace library title bar with MONOS TopBar
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
        self._main_splitter.addWidget(self._sidebar)
        self._main_splitter.addWidget(right_container)
        self._main_splitter.setStretchFactor(0, 20)
        self._main_splitter.setStretchFactor(1, 80)
        self._main_splitter.setSizes([256, 1100])

        self.setCentralWidget(self._main_splitter)
        self._restore_splitter_sizes()
        # Title bar must stay on top so it receives drag and window buttons (min/max/close).
        self._top_bar.raise_()

        self._sidebar.context_changed.connect(self._on_context_switched)
        self._sidebar.context_clicked.connect(self._on_context_clicked)
        self._sidebar.context_menu_requested.connect(self._on_sidebar_context_menu_requested)
        self._sidebar.settings_requested.connect(self._open_settings)
        self._sidebar.recent_task_clicked.connect(self._on_recent_task_clicked)
        # Metadata-driven filter sidebar (UI-only; wiring stub).
        self._sidebar.filters().departmentClicked.connect(self._controller.on_department_clicked)
        self._sidebar.filters().typeClicked.connect(self._controller.on_type_clicked)
        self._controller.departmentChanged.connect(lambda v: self._set_current_department(v, toggle_if_same=False))
        self._controller.typeChanged.connect(lambda v: self._set_current_type(v, toggle_if_same=False))
        self._controller.departmentChanged.connect(self._on_department_changed_notify)
        self._controller.typeChanged.connect(self._on_type_changed_notify)
        self._controller.departmentChanged.connect(self._set_main_view_department)
        self.departmentChanged.connect(self._on_filter_state_changed)
        self.typeChanged.connect(self._on_filter_state_changed)
        self._top_bar.project_switch_requested.connect(self._switch_project)
        self._top_bar.minimize_clicked.connect(self.showMinimized)
        self._top_bar.maximize_clicked.connect(self._toggle_maximize)
        self._top_bar.close_clicked.connect(self.close)
        self._top_bar.title_double_clicked.connect(self._toggle_maximize)
        self._inspector.close_requested.connect(self._main_view.clear_selection)
        self._inspector.paste_thumbnail_requested.connect(self._on_paste_thumbnail_requested)
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
        self._main_view.status_set_requested.connect(self._on_status_set_requested)
        self._main_view.primary_action_requested.connect(self._on_primary_action_requested)
        self._main_view.inbox_drop_requested.connect(self._on_inbox_drop_requested)
        self._main_view.search_query_changed.connect(self._on_search_query_changed)

        # Inspector intents (explicit)
        self._inspector.open_folder_requested.connect(self._on_inspector_open_folder_requested)
        self._inspector.status_change_requested.connect(self._on_status_set_requested)
        self._inspector.inbox_distribute_finished.connect(self._on_inbox_distribute_finished)

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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._top_bar.raise_()  # keep title bar on top so drag + min/max/close work
        notification_service.update_overlay_geometry()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._top_bar.set_maximized(self.isMaximized())

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
        # Reload for Assets (filter) and Inbox (source = client/freelancer).
        if ctx not in ("Assets", "Inbox"):
            return
        # Ignore filter-driven reload for Inbox briefly after switching to Inbox (avoids items flash then placeholder).
        if ctx == "Inbox" and getattr(self, "_inbox_switch_cooldown", False):
            return
        if ctx == "Assets" and self._entered_parent is not None:
            return
        self._filter_switch_in_progress = True
        try:
            self._reload_main_view()
        finally:
            self._filter_switch_in_progress = False
            # Sync inspector exactly once after the filter switch settles.
            try:
                self._on_valid_selection_changed(self._main_view.has_valid_selection())
            except Exception:
                pass

    def _set_main_view_department(self, _value: object = None) -> None:
        """Sync main view header + thumb badge with current department (pipeline label/icon for subdepartments)."""
        dep = self._controller.current_department
        label, icon_name = self._sidebar.filters().get_department_display(dep) if dep else (None, None)
        self._main_view.set_active_department(dep, label=label, icon_name=icon_name)

    def _set_main_view_type(self) -> None:
        """Sync main view type badge: Assets = asset type (Character, Prop, …); Inbox = source (Client, Freelancer)."""
        ctx = self._sidebar.current_context()
        if ctx == "Inbox":
            type_id = self._sidebar.filters().current_type()
            if not type_id:
                self._main_view.set_selected_asset_type(None)
                return
            label, icon_name = self._sidebar.filters().get_type_display(type_id)
            self._main_view.set_selected_asset_type(type_id, label=label, icon_name=icon_name)
            return
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
                # Sync sidebar → local state only (no emit) so first _reload_main_view has a valid source.
                filters = self._sidebar.filters()
                t = filters.current_type()
                d = filters.current_department()
                if t is not None:
                    self.current_type = t
                    self._app_state.set_filters(self.current_department, self.current_type)
                if d is not None:
                    self.current_department = d
                    self._app_state.set_filters(self.current_department, self.current_type)
                self._set_main_view_type()  # type badge in headbar (Client / Freelancer)
            else:
                self._set_current_department(None, toggle_if_same=False)
                self._set_current_type(None, toggle_if_same=False)
            return
        filters = self._sidebar.filters()
        self._controller.sync_filter_state(
            department=filters.current_department(),
            type_id=filters.current_type(),
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
        self._top_bar.set_projects(
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
        - Reads image from clipboard, normalizes, writes thumbnail.user.(png|jpg).
        """
        if not isinstance(item, ViewItem):
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return

        kind = "asset" if item.kind == ViewItemKind.ASSET else "shot"
        try:
            # Use absolute path as a stable item id for refresh routing.
            self._clipboard_thumbs.paste_thumbnail(item_root=item.path, kind=kind, item_id=str(item.path), fmt="png")
        except Exception as e:
            # Explicit error; do not crash app.
            QMessageBox.critical(self, "Paste Thumbnail", str(e))
            return

        # Refresh Inspector + grid immediately.
        self._inspector.refresh_thumbnail()
        self._main_view.invalidate_thumbnail(item.path)

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
        - Confirmation requires typing exact folder name
        - Deletes folder recursively from disk
        - On success: update in-memory index and refresh UI (NO rescan)
        - On failure: silent no-op (log only)
        """
        if self._project_index is None:
            return
        if item.kind.value not in ("asset", "shot"):
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
        except OSError as e:
            logging.warning("Delete failed: %s", e)
            return

        self._project_index = build_project_index(self._project_index.root, project_root=self._project_index.root)
        self._reload_main_view()
        cur = self._main_view.selected_view_item()
        if cur and cur.path == path:
            self._inspector.set_item(None)
        self._sync_primary_action()

    def _on_status_set_requested(self, item: ViewItem, status: str) -> None:
        """Write user-set status to .monostudio/status.json and refresh list (no rescan)."""
        if item.kind.value not in ("asset", "shot"):
            return
        try:
            write_item_status(Path(item.path), status)
        except (ValueError, OSError) as e:
            logging.warning("Failed to write item status: %s", e)
            return
        self._reload_main_view()
        if self._main_view.select_item_by_path(item.path):
            cur = self._main_view.selected_view_item()
            if cur:
                self._inspector.set_item(cur)
        self._sync_primary_action()

    def _restore_workspace_root(self) -> None:
        path = self._settings.value("workspace/root", "", str)
        self._apply_workspace_root(path or None, save=False)

    def _restore_project_root(self) -> None:
        path = self._settings.value("project/root", "", str)
        self._apply_project_root(path or None, save=False)

    def _restore_sidebar_context(self) -> None:
        """Restore last selected nav page (Assets/Shots/Inbox/Projects/Library) from QSettings."""
        _valid = ("Assets", "Shots", "Inbox", "Projects", "Library")
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
            if context_name != "Inbox" and self._inbox_split_view is not None:
                self._save_inbox_date_folder_state(self._inbox_split_view.date_folder_path())
                self._save_inbox_tree_state(self._inbox_split_view)
                self._settings.setValue(self._inbox_restore_split_key(), True)  # khi quay lại Inbox thì mở lại date folder
                self._content_stack.setCurrentWidget(self._main_view)
                self._main_view.clear()  # avoid showing stale Inbox list before next page loads
                self._content_stack.removeWidget(self._inbox_split_view)
                self._inbox_split_view.deleteLater()
                self._inbox_split_view = None
                self._inspector.set_inbox_mapping_selection([], None, None)
            # Clear selection first; selection churn during model resets is a common source of re-entrant UI.
            try:
                self._main_view.clear_selection()
            except Exception:
                pass
            self._inspector.set_item(None)

            self._main_view.set_accept_inbox_drop(False)
            if context_name in ("Assets", "Shots"):
                # Sync filter state first so _reload_main_view uses correct (page, dept, type).
                self._sync_filter_state_from_sidebar()
                # Clear so diff application does not mix with previous context data.
                self._main_view.clear()
                # Deterministic autoscan trigger (locked rule).
                self._rescan_project()
                self._reload_main_view()
            elif context_name == "Inbox":
                self._main_view.set_accept_inbox_drop(True)
                self._sync_filter_state_from_sidebar()  # ensure sidebar source (client/freelancer) is in sync before reload
                self._inbox_switch_cooldown = True
                QTimer.singleShot(120, lambda: setattr(self, "_inbox_switch_cooldown", False))
                self._main_view.clear()  # avoid showing stale Assets/Shots/Projects content before Inbox loads
                restored = self._restore_inbox_date_folder_state()
                if not restored:
                    self._reload_main_view()  # run synchronously so no second reload (e.g. from filter signal) clears items
            elif context_name == "Projects":
                self._reload_main_view()
            else:
                # Library and others: no drop; clear inbox drop if was on Inbox.
                self._main_view.set_accept_inbox_drop(False)
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
                    self._sync_filter_state_from_sidebar()
                    self._main_view.clear()
                self._reload_main_view()
            elif context_name == "Inbox":
                self._sync_filter_state_from_sidebar()
                self._reload_main_view()
            else:
                self._main_view.clear()
                self._main_view.set_empty_override(self._empty_message_for_context(context_name))
            self._sync_primary_action()
            self._sync_filter_state_from_sidebar()
        finally:
            self._context_switch_in_progress = False

    def _on_department_changed_notify(self, department: str | None) -> None:
        if getattr(self, "_context_switch_in_progress", False):
            return
        # Sidebar notifications disabled.

    def _on_type_changed_notify(self, type_id: str | None) -> None:
        if getattr(self, "_context_switch_in_progress", False):
            return
        # Sidebar notifications disabled.

    def _empty_message_for_context(self, context_name: str) -> str:
        if context_name == "Projects":
            if self._workspace_root is None:
                return "Open Workspace… in Settings → App → Workspace."
            if not self._workspace_projects:
                return "No projects found in this workspace"
            return "Select a project using the project switcher in the top bar."
        if context_name == "Inbox":
            return "Select Client or Freelancer in sidebar, or add folders to inbox."
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
        self._inspector.set_item(selected)

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
            user_status = read_item_status(asset.path)
            return ViewItem(
                kind=ViewItemKind.ASSET,
                name=asset.name,
                type_badge=asset.asset_type,
                path=asset.path,
                departments_count=len(asset.departments),
                ref=asset,
                type_folder=type_folder,
                user_status=user_status,
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
            user_status = read_item_status(s.path)
            return ViewItem(
                kind=ViewItemKind.SHOT,
                name=s.name,
                type_badge="shot",
                path=s.path,
                departments_count=len(s.departments),
                ref=s,
                user_status=user_status,
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
            _dcc_log.debug("incremental_scan calling _reload_main_view + repaint_tile_and_list_views (singleShot 0)")
            if self._project_index is not None:
                self._project_index = ProjectIndex(
                    root=self._project_index.root,
                    assets=tuple(sorted(current_assets.values(), key=lambda x: (x.asset_type, x.name))),
                    shots=tuple(sorted(current_shots.values(), key=lambda x: x.name)),
                )
                self._sidebar.set_project_index(self._project_index)
                # So new assets/shots (e.g. just created) get their paths watched
                self._update_fs_watcher_paths()
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
        self._main_view.set_project_root(folder)
        self._main_view.set_empty_override(None)

        if self._project_root is not None:
            try:
                dept_reg = DepartmentRegistry.for_project(self._project_root)
                self._inspector.set_department_registry(dept_reg)
            except Exception:
                self._inspector.set_department_registry(None)
        else:
            self._inspector.set_department_registry(None)

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
                self._sidebar.set_project_index(None)
                self._inspector.set_department_registry(None)
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
        try:
            root = self._project_root.resolve()
        except OSError:
            return
        to_add: list[str] = []
        if root.is_dir():
            to_add.append(str(root))
        assets_dir = root / "assets"
        shots_dir = root / "shots"
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

        if self._project_index is not None:
            for asset in self._project_index.assets:
                base = Path(asset.path)
                if not base.is_absolute():
                    base = (root / base).resolve()
                if base.is_dir() and len(to_add) < _max_paths:
                    s = str(base)
                    if s not in _seen:
                        _seen.add(s)
                        to_add.append(s)
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
            for shot in self._project_index.shots:
                base = Path(shot.path)
                if not base.is_absolute():
                    base = (root / base).resolve()
                if base.is_dir() and len(to_add) < _max_paths:
                    s = str(base)
                    if s not in _seen:
                        _seen.add(s)
                        to_add.append(s)
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
        # Placeholder for search input (context-aware).
        placeholders = {"Assets": "Search assets", "Shots": "Search shots", "Projects": "Search projects", "Inbox": "Search inbox"}
        self._main_view.set_search_placeholder(placeholders.get(context, "Search…"))
        items: list[ViewItem] = []

        # Projects context: show workspace discovery results (read-only).
        if context == "Projects":
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

        if context == "Inbox":
            self._main_view.set_selected_asset_type(None)
            self._main_view.set_active_department(None)
            if self._project_root is None:
                self._main_view.clear()
                self._main_view.set_empty_override(self._empty_message_for_context(context))
                return
            try:
                inbox_nodes = scan_inbox(self._project_root)
            except Exception:
                inbox_nodes = []
            source_filter = (self._sidebar.filters().current_type() or "").strip().lower()  # "client", "freelancer", or "" = both
            if source_filter:
                for node in inbox_nodes:
                    if (node.name or "").lower() != source_filter:
                        continue
                    if node.is_dir and node.children:
                        for child in node.children:
                            items.append(
                                ViewItem(
                                    kind=ViewItemKind.INBOX_ITEM,
                                    name=child.name,
                                    type_badge=node.name or "",
                                    path=child.path,
                                    departments_count=None,
                                    ref=child,
                                )
                            )
                    break
            else:
                # No Client/Freelancer selected: show both with section titles.
                for node in inbox_nodes:
                    name_lower = (node.name or "").lower()
                    if name_lower not in ("client", "freelancer"):
                        continue
                    if not node.is_dir:
                        continue
                    title = (node.name or "").strip().replace("_", " ").title() or node.name
                    section_path = Path(node.path)
                    items.append(
                        ViewItem(
                            kind=ViewItemKind.INBOX_SECTION,
                            name=title,
                            type_badge="",
                            path=section_path,
                            departments_count=None,
                            ref=node,
                        )
                    )
                    if node.children:
                        for child in node.children:
                            items.append(
                                ViewItem(
                                    kind=ViewItemKind.INBOX_ITEM,
                                    name=child.name,
                                    type_badge=node.name or "",
                                    path=child.path,
                                    departments_count=None,
                                    ref=child,
                                )
                            )
            items = self._apply_search_filter(items, self.current_search_query)
            if not items and (self.current_search_query or "").strip():
                self._main_view.set_empty_override('No matches for "' + self.current_search_query.strip() + '"')
            else:
                self._main_view.set_empty_override(
                    None if items else "Select Client or Freelancer in sidebar, or add folders to inbox."
                )
            self._main_view.set_items(items)
            self._set_main_view_type()  # headbar type badge (Client / Freelancer when selected)
            return

        if self._project_index is None:
            self._main_view.clear()
            return

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
                user_status = read_item_status(asset.path)
                items.append(
                    ViewItem(
                        kind=ViewItemKind.ASSET,
                        name=asset.name,
                        type_badge=asset.asset_type,
                        path=asset.path,
                        departments_count=len(asset.departments),
                        ref=asset,
                        type_folder=type_folder,
                        user_status=user_status,
                    )
                )
        elif context == "Shots":
            for shot in self._app_state.get_shots_in_order():
                user_status = read_item_status(shot.path)
                items.append(
                    ViewItem(
                        kind=ViewItemKind.SHOT,
                        name=shot.name,
                        type_badge="shot",
                        path=shot.path,
                        departments_count=len(shot.departments),
                        ref=shot,
                        user_status=user_status,
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
        if item.kind == ViewItemKind.INBOX_SECTION:
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
            except Exception as e:
                logging.warning("DCC launch failed (asset): %s", e, exc_info=True)
                QMessageBox.critical(self, "Open DCC", str(e))
            return
        if item.kind == ViewItemKind.SHOT and isinstance(item.ref, Shot):
            try:
                self._controller.smart_open(item=item.ref, force_dialog=False, parent=self)
                self._refresh_recent_tasks()
            except Exception as e:
                logging.warning("DCC launch failed (shot): %s", e, exc_info=True)
                QMessageBox.critical(self, "Open DCC", str(e))
            return
        if item.kind == ViewItemKind.INBOX_ITEM and item.path:
            ref = getattr(item, "ref", None)
            if ref is not None and getattr(ref, "is_dir", False):
                self._enter_inbox_date_folder(Path(item.path))
                return
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.path)))
            except Exception:
                pass
            return
        return

    def _inbox_date_folder_settings_key(self) -> str:
        return "inbox/last_date_folder_path"

    def _inbox_restore_split_key(self) -> str:
        return "inbox/restore_split_view"

    def _inbox_tree_expanded_key(self) -> str:
        return "inbox/tree_expanded_paths"

    def _inbox_splitter_sizes_key(self) -> str:
        return "inbox/splitter_sizes"

    def _save_inbox_date_folder_state(self, path: Path) -> None:
        if path and path.is_dir() and self._project_root and str(path).startswith(str(self._project_root)):
            self._settings.setValue(self._inbox_date_folder_settings_key(), str(path.resolve()))

    def _save_inbox_tree_state(self, view: InboxSplitView) -> None:
        state = view.get_tree_state()
        expanded = state.get("expanded_paths") or []
        self._settings.setValue(self._inbox_tree_expanded_key(), "\n".join(expanded))
        sizes = state.get("splitter_sizes") or []
        self._settings.setValue(self._inbox_splitter_sizes_key(), ",".join(str(s) for s in sizes))

    def _load_inbox_tree_state(self) -> dict | None:
        expanded_str = self._settings.value(self._inbox_tree_expanded_key(), "", str)
        expanded = [p.strip() for p in (expanded_str or "").split("\n") if p.strip()]
        sizes_str = self._settings.value(self._inbox_splitter_sizes_key(), "", str)
        sizes = []
        for s in (sizes_str or "").split(","):
            s = s.strip()
            if s.isdigit():
                sizes.append(int(s))
        if not expanded and len(sizes) != 2:
            return None
        return {
            "expanded_paths": expanded,
            "splitter_sizes": sizes if len(sizes) == 2 else [],
        }

    def _restore_inbox_date_folder_state(self) -> bool:
        """Restore Inbox split view (date folder + tree state) when user had it open. Returns True if restored."""
        raw = self._settings.value(self._inbox_restore_split_key(), True)
        if raw in (False, "false", "0", 0):
            return False
        path_str = self._settings.value(self._inbox_date_folder_settings_key(), "", str)
        if not path_str or not self._project_root:
            return False
        path = Path(path_str)
        if not path.is_dir():
            return False
        try:
            if not str(path.resolve()).startswith(str(self._project_root.resolve())):
                return False
        except OSError:
            return False
        self._enter_inbox_date_folder(path, restore_tree_state=True)
        return True

    def _enter_inbox_date_folder(self, date_folder_path: Path, *, restore_tree_state: bool = False) -> None:
        if not date_folder_path.is_dir():
            return
        self._save_inbox_date_folder_state(date_folder_path)
        self._inbox_split_view = InboxSplitView(date_folder_path, self)
        self._inbox_split_view.back_requested.connect(self._on_inbox_split_back)
        self._inbox_split_view.mapping_selection_changed.connect(self._on_inbox_mapping_selection_changed)
        self._inbox_split_view.open_folder_requested.connect(
            lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        )
        self._content_stack.addWidget(self._inbox_split_view)
        self._content_stack.setCurrentWidget(self._inbox_split_view)
        self._inspector.set_item(None)
        self._inspector.set_inbox_mapping_selection([], None, None)
        if restore_tree_state:
            tree_state = self._load_inbox_tree_state()
            if tree_state:
                self._inbox_split_view.set_tree_state(tree_state)

    def _on_inbox_split_back(self) -> None:
        if self._inbox_split_view is None:
            return
        self._save_inbox_date_folder_state(self._inbox_split_view.date_folder_path())
        self._save_inbox_tree_state(self._inbox_split_view)
        self._settings.setValue(self._inbox_restore_split_key(), False)  # user chọn về list, lần sau vào Inbox hiện list
        self._content_stack.setCurrentWidget(self._main_view)
        self._content_stack.removeWidget(self._inbox_split_view)
        self._inbox_split_view.deleteLater()
        self._inbox_split_view = None
        self._inspector.set_inbox_mapping_selection([], None, None)
        self._reload_main_view()

    def _on_inbox_mapping_selection_changed(self, paths: list) -> None:
        path_list = [Path(p) for p in paths if p] if paths else []
        self._inspector.set_inbox_mapping_selection(
            path_list,
            self._project_root,
            self._project_index,
        )

    def _on_inbox_distribute_finished(self, paths: list) -> None:
        if self._inbox_split_view and paths:
            self._inbox_split_view.remove_mapping_paths([Path(p) for p in paths if p])

    def _on_inbox_drop_requested(self, paths: list) -> None:
        """User dropped files/folders onto Inbox view: show Add to Inbox form, then copy into inbox."""
        if not paths or not self._project_root:
            return
        try:
            path_list = [Path(p) for p in paths if p]
        except (TypeError, ValueError):
            return
        if not path_list:
            return
        dialog = AddToInboxDialog(default_date=date.today(), parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        source_label = dialog.source()
        date_str = dialog.date_str()
        description = dialog.description()
        for p in path_list:
            try:
                add_to_inbox(
                    self._project_root,
                    p,
                    source_label,
                    date_str,
                    description,
                )
            except Exception as e:
                logging.warning("Add to inbox failed for %s: %s", p, e)
        self._reload_main_view()

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
            # Pending cleared when watcher triggers incremental_scan and scan finds work_file_path (or via assetsChanged).
        except Exception as e:
            logging.warning("DCC launch failed (create new): %s", e, exc_info=True)
            QMessageBox.critical(self, "Create New…", str(e))

    def _on_inspector_open_folder_requested(self, item: object) -> None:
        if not isinstance(item, ViewItem) or not getattr(item, "path", None):
            return
        path = item.path
        if not path.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_recent_task_clicked(self, task: object) -> None:
        from monostudio.ui_qt.recent_tasks_store import RecentTask
        if not isinstance(task, RecentTask):
            return
        # Switch to Assets or Shots context.
        ctx = "Assets" if task.item_type == "asset" else "Shots"
        self._sidebar.set_current_context(ctx)
        # Set department filter: sync controller first, then sidebar with emit=False so clicking
        # two tasks with the same department does not trigger controller's "same dept → toggle off".
        self._controller.sync_filter_state(department=task.department, type_id=self.current_type)
        self._sidebar.filters().set_selected_department(task.department, emit=False)
        # Select the item in main view.
        self._main_view.select_item_by_path(Path(task.item_path))

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

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            workspace_root=self._workspace_root,
            project_root=self._project_root,
            settings=self._settings,
            parent=self,
        )
        dialog.workspace_root_selected.connect(lambda p: self._apply_workspace_root(p, save=True))
        dialog.project_root_selected.connect(lambda p: self._apply_project_root(p, save=True))
        dialog.exec()

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
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._top_bar.set_maximized(self.isMaximized())

    def _apply_restore_maximized(self) -> None:
        """Restore maximized state khi load; 6b B: cập nhật icon sau showMaximized."""
        self.showMaximized()
        self._top_bar.set_maximized(True)

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
        except Exception:
            pass
        path = self._app_settings_path()
        payload = {
            "window_geometry_b64": base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
            "window_maximized": self.isMaximized(),
            "main_splitter_sizes": self._main_splitter.sizes(),
            "content_splitter_sizes": self._content_splitter.sizes(),
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
        self._apply_project_root(project_root, save=True)
        self._sync_top_bar()

    def _on_root_context_menu_requested(self, global_pos) -> None:
        # Entry points (context menu only):
        # - Assets root -> Create Asset…
        # - Shots root  -> Create Shot…
        #
        # Root here means: user is at top-level list (not inside an asset/shot).
        if self._project_root is None or self._project_index is None:
            return
        if self._entered_parent is not None:
            return

        context = self._sidebar.current_context()
        if context not in ("Assets", "Shots"):
            return
        menu = QMenu(self)
        create_asset = None
        create_shot = None

        if context == "Assets":
            create_asset = menu.addAction(lucide_icon("box", size=16, color_hex=MONOS_COLORS["text_label"]), "Create Asset…")
        elif context == "Shots":
            create_shot = menu.addAction(lucide_icon("clapperboard", size=16, color_hex=MONOS_COLORS["text_label"]), "Create Shot…")

        chosen = menu.exec(global_pos)
        if chosen is None:
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

        dialog = CreateAssetDialog(self._project_root, self)
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
        type_folder = type_reg.get_type_folder(asset_type)
        target = self._project_root / "assets" / type_folder / asset_name
        if target.exists():
            return

        created: list[Path] = []
        try:
            use_dcc_folders = read_use_dcc_folders(self._project_root)
            to_create: list[Path] = [target]
            for d in departments:
                dept_folder = dept_reg.get_department_folder(d, "asset")
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

        # After creation, trigger existing autoscan logic (Phase 1) for current project.
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_item(None)

    def _create_shot(self) -> None:
        if self._project_root is None:
            return

        dialog = CreateShotDialog(self._project_root, self)
        if dialog.exec() != QDialog.Accepted:
            return

        shot_name = dialog.shot_name()
        departments = dialog.selected_departments()
        create_subfolders = dialog.create_subfolders()
        if not shot_name:
            return

        target = self._project_root / "shots" / shot_name
        if target.exists():
            return

        dept_reg = DepartmentRegistry.for_project(self._project_root)
        use_dcc_folders = read_use_dcc_folders(self._project_root)
        created: list[Path] = []
        try:
            to_create: list[Path] = [target]
            for d in departments:
                dept_folder = dept_reg.get_department_folder(d, "shot")
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

        # After creation, trigger existing autoscan logic (Phase 1) for current project.
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_item(None)

