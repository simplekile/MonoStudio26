from __future__ import annotations

import base64
import json
import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QSettings, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QMenu, QMessageBox, QSplitter, QVBoxLayout, QWidget

from monostudio.core.fs_reader import build_project_index
from monostudio.core.models import Asset, ProjectIndex, Shot
from monostudio.core.workspace_reader import DiscoveredProject, ProjectQuickStats, discover_projects, read_project_quick_stats
from monostudio.core.project_create import create_new_project
from monostudio.core.pipeline_types_and_presets import ensure_pipeline_bootstrap, load_pipeline_types_and_presets
from monostudio.core.clipboard_thumbnail_handler import ClipboardThumbnailHandler
from monostudio.ui_qt.create_entry_dialogs import CreateAssetDialog, CreateShotDialog
from monostudio.ui_qt.inspector import InspectorPanel
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.new_project_dialog import NewProjectDialog
from monostudio.ui_qt.settings_dialog import SettingsDialog
from monostudio.ui_qt.sidebar import Sidebar
from monostudio.ui_qt.top_bar import TopBar
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind
from monostudio.ui_qt.delete_confirm_dialog import DeleteConfirmDialog
from monostudio.ui_qt.app_controller import AppController


class MainWindow(QMainWindow):
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

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        repo_root = Path(__file__).resolve().parents[2]
        self._controller = AppController(settings=self._settings, repo_root=repo_root, parent=self)
        self._workspace_root: Path | None = None
        self._workspace_projects: list[DiscoveredProject] = []
        self._workspace_project_status: dict[str, str] = {}

        self._project_root: Path | None = None
        self._project_index: ProjectIndex | None = None
        self._entered_parent: Asset | Shot | None = None

        # Centralized filter state (UI-only; no filtering engine yet)
        self.current_department: str | None = None
        self.current_type: str | None = None
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
        self._inspector = InspectorPanel()
        self._inspector.setMinimumWidth(240)
        self._top_bar = TopBar(self)
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
        # L2: Right container (vertical) -> [Topbar] + [Main content]
        right_container = QWidget(self)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._top_bar, 0)

        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        content_splitter.addWidget(self._main_view)
        content_splitter.addWidget(self._inspector)
        content_splitter.setStretchFactor(0, 70)
        content_splitter.setStretchFactor(1, 30)
        content_splitter.setSizes([800, 320])
        right_layout.addWidget(content_splitter, 1)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.addWidget(self._sidebar)
        main_splitter.addWidget(right_container)
        main_splitter.setStretchFactor(0, 20)
        main_splitter.setStretchFactor(1, 80)
        main_splitter.setSizes([256, 1100])

        self.setCentralWidget(main_splitter)

        self._sidebar.context_changed.connect(self._on_context_switched)
        self._sidebar.context_clicked.connect(self._on_context_clicked)
        self._sidebar.context_menu_requested.connect(self._on_sidebar_context_menu_requested)
        self._sidebar.settings_requested.connect(self._open_settings)
        # Metadata-driven filter sidebar (UI-only; wiring stub).
        self._sidebar.filters().departmentClicked.connect(self._controller.on_department_clicked)
        self._sidebar.filters().typeClicked.connect(self._controller.on_type_clicked)
        self._controller.departmentChanged.connect(lambda v: self._set_current_department(v, toggle_if_same=False))
        self._controller.typeChanged.connect(lambda v: self._set_current_type(v, toggle_if_same=False))
        self._controller.departmentChanged.connect(lambda _v: self._main_view.set_active_department(self._controller.current_department))
        self.departmentChanged.connect(self._on_filter_state_changed)
        self.typeChanged.connect(self._on_filter_state_changed)
        self._top_bar.project_switch_requested.connect(self._switch_project)
        self._inspector.close_requested.connect(self._main_view.clear_selection)
        self._inspector.paste_thumbnail_requested.connect(self._on_paste_thumbnail_requested)
        self._main_view.valid_selection_changed.connect(self._on_valid_selection_changed)
        self._main_view.item_activated.connect(self._on_item_activated)
        self._main_view.refresh_requested.connect(self._on_refresh_requested)
        self._main_view.root_context_menu_requested.connect(self._on_root_context_menu_requested)
        self._main_view.copy_inventory_requested.connect(self._on_copy_item_inventory_requested)

        # Clipboard thumbnail overrides: refresh UI after successful paste.
        self._clipboard_thumbs.thumbnailUpdated.connect(self._on_thumbnail_updated)
        self._main_view.delete_requested.connect(self._on_delete_requested)
        self._main_view.primary_action_requested.connect(self._on_primary_action_requested)

        # Initial population is driven by project-root restore (scan trigger) and current context.
        self._reload_main_view()
        self._inspector.set_item(None)
        self._sync_primary_action()
        self._sync_top_bar()
        self._sync_filter_state_from_sidebar()

    @staticmethod
    def _norm(s: str | None) -> str:
        return (s or "").strip().casefold()

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

    # Filter click handlers now live in AppController.

    def _on_filter_state_changed(self, _value=None) -> None:
        # UI-only: rebuild grid/list from existing in-memory data (no rescans).
        if self._sidebar.current_context() != "Assets":
            return
        if self._entered_parent is not None:
            return
        self._reload_main_view()

    def _set_current_department(self, department, *, toggle_if_same: bool) -> None:
        new = department if isinstance(department, str) and department.strip() else None
        if toggle_if_same and new is not None and new == self.current_department:
            new = None
        if new == self.current_department:
            return
        self.current_department = new
        self.departmentChanged.emit(new)

    def _set_current_type(self, type_id, *, toggle_if_same: bool) -> None:
        new = type_id if isinstance(type_id, str) and type_id.strip() else None
        if toggle_if_same and new is not None and new == self.current_type:
            new = None
        if new == self.current_type:
            return
        self.current_type = new
        self.typeChanged.emit(new)

    def _sync_filter_state_from_sidebar(self) -> None:
        """
        Keep centralized filter state in sync with the SidebarWidget selection
        when switching pages (Assets vs Shots) where SidebarWidget restores per-page state.
        """
        ctx = self._sidebar.current_context()
        if ctx not in ("Assets", "Shots"):
            self._set_current_department(None, toggle_if_same=False)
            self._set_current_type(None, toggle_if_same=False)
            return
        filters = self._sidebar.filters()
        self._controller.sync_filter_state(
            department=filters.current_department(),
            type_id=filters.current_type(),
        )

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
            act = menu.addAction("Copy Assets Inventory")
        else:
            act = menu.addAction("Copy Shots Inventory")

        chosen = menu.exec(global_pos)
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
        self._main_view.invalidate_thumbnail(p)
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
        except OSError:
            logging.getLogger(__name__).exception("Failed to delete folder: %s", str(path))
            return

        # Success: update in-memory index (NO rescan)
        if item.kind.value == "asset":
            assets = tuple(a for a in self._project_index.assets if a.path != path)
            self._project_index = ProjectIndex(root=self._project_index.root, assets=assets, shots=self._project_index.shots)
        else:
            shots = tuple(s for s in self._project_index.shots if s.path != path)
            self._project_index = ProjectIndex(root=self._project_index.root, assets=self._project_index.assets, shots=shots)

        self._entered_parent = None
        self._reload_main_view()
        self._inspector.set_item(None)

    def _restore_workspace_root(self) -> None:
        path = self._settings.value("workspace/root", "", str)
        self._apply_workspace_root(path or None, save=False)

    def _restore_project_root(self) -> None:
        path = self._settings.value("project/root", "", str)
        self._apply_project_root(path or None, save=False)

    def _on_context_switched(self, context_name: str) -> None:
        # Trigger: user switches between top-level contexts.
        self._main_view.set_context_title(context_name)
        self._entered_parent = None
        if context_name in ("Assets", "Shots"):
            # Deterministic autoscan trigger (locked rule).
            self._rescan_project()
            self._reload_main_view()
        elif context_name == "Projects":
            # Projects browser: read-only workspace view (no project rescan).
            self._reload_main_view()
        else:
            # Non-project-browser areas are placeholders (no scans, no side effects).
            self._main_view.clear()
            self._main_view.set_empty_override(self._empty_message_for_context(context_name))
        self._inspector.set_item(None)
        self._sync_primary_action()
        self._sync_filter_state_from_sidebar()

    def _on_context_clicked(self, context_name: str) -> None:
        # Spec: click reloads Main View. (No autoscan trigger unless it was a switch.)
        self._main_view.set_context_title(context_name)
        self._entered_parent = None
        if context_name in ("Assets", "Shots", "Projects"):
            self._reload_main_view()
        else:
            self._main_view.clear()
            self._main_view.set_empty_override(self._empty_message_for_context(context_name))
        self._inspector.set_item(None)
        self._sync_primary_action()
        self._sync_filter_state_from_sidebar()

    def _empty_message_for_context(self, context_name: str) -> str:
        if context_name == "Projects":
            if self._workspace_root is None:
                return "Open Workspace… in Settings → App → Workspace."
            if not self._workspace_projects:
                return "No projects found in this workspace"
            return "Select a project using the project switcher in the top bar."
        return f"{context_name} is not available yet."

    def _on_valid_selection_changed(self, has_selection: bool) -> None:
        if not has_selection:
            self._inspector.set_item(None)
            return

        selected = self._main_view.selected_view_item()
        self._inspector.set_item(selected)

    def _apply_project_root(self, folder: str | None, *, save: bool) -> None:
        # No validation (per rules). Store path and reload UI.
        if save:
            self._settings.setValue("project/root", folder or "")

        self._project_root = Path(folder) if folder else None
        self._controller.set_project_root(self._project_root)
        self._main_view.set_project_root(folder)
        self._main_view.set_empty_override(None)

        # Reload index if root set; otherwise clear.
        if self._project_root is None:
            self._project_index = None
            self._entered_parent = None
            self._main_view.clear()
        else:
            self._project_index = build_project_index(self._project_root)
            self._entered_parent = None
            self._reload_main_view()
        self._sidebar.set_project_index(self._project_index)

        # After selecting a folder: keep layout stable, show neutral empty-state.
        self._inspector.set_item(None)

        self._sync_primary_action()
        self._sync_top_bar()

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
        # Autoscan must be deterministic and synchronous.
        if self._project_root is None:
            self._project_index = None
            self._sidebar.set_project_index(None)
            return
        self._project_index = build_project_index(self._project_root)
        self._sidebar.set_project_index(self._project_index)

    def _on_refresh_requested(self) -> None:
        # Trigger: user clicks Refresh (context menu) -> rescan synchronously.
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_item(None)
        self._sync_primary_action()

    def _reload_main_view(self) -> None:
        context = self._sidebar.current_context()
        items: list[ViewItem] = []

        # Projects context: show workspace discovery results (read-only).
        if context == "Projects":
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
            self._main_view.set_empty_override(None if items else "No projects found in this workspace")
            self._main_view.set_items(items)
            return

        if self._project_index is None:
            self._main_view.clear()
            return

        if context == "Assets":
            filtered_assets = self.filter_assets(
                list(self._project_index.assets),
                self.current_department,
                self.current_type,
            )
            for asset in filtered_assets:
                items.append(
                    ViewItem(
                        kind=ViewItemKind.ASSET,
                        name=asset.name,
                        type_badge=asset.asset_type,
                        path=asset.path,
                        departments_count=len(asset.departments),
                        ref=asset,
                    )
                )
        elif context == "Shots":
            for shot in self._project_index.shots:
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

        if self._project_root is not None:
            # In a project, use default empty states unless overridden by other flows.
            self._main_view.set_empty_override(None)

        # Header context: show active department inline with title.
        if context in ("Assets", "Shots"):
            self._main_view.set_active_department(self._controller.current_department)
        else:
            self._main_view.set_active_department(None)

        self._main_view.set_items(items)
        self._sidebar.set_project_index(self._project_index)

    def _on_item_activated(self, item: ViewItem) -> None:
        # NOTE: "Enter departments" navigation has been removed.
        # Double click / Enter will be repurposed by a different function later.
        if item.kind == ViewItemKind.PROJECT:
            # Explicit action: open/switch project by double-clicking a project card.
            self._switch_project(str(item.path))
            return
        if item.kind == ViewItemKind.ASSET and isinstance(item.ref, Asset):
            dept = self._controller.current_department
            if dept is None:
                QMessageBox.information(self, "Open DCC", "Select a Department filter first.")
                return
            try:
                self._controller.handle_department_activated(asset=item.ref, department=dept)
            except Exception as e:
                QMessageBox.critical(self, "Open DCC", str(e))
            return
        return

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

        # Do NOT rescan workspace from scratch; append to existing list.
        discovered = DiscoveredProject(name=created.display_name, root=created.root)
        self._workspace_projects.append(discovered)
        # Auto-switch to new project (sets project/root, triggers existing autoscan, resets Inspector).
        self._apply_project_root(str(created.root), save=True)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(workspace_root=self._workspace_root, project_root=self._project_root, parent=self)
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
        repo_root = Path(__file__).resolve().parents[2]
        return repo_root / "monostudio_data" / "config" / "app_settings.json"

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

        if not restored:
            # First launch / no saved geometry: default size.
            self.resize(1920, 1080)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """
        Save window geometry on close (size + position).
        Silent failure on IO errors.
        """
        path = self._app_settings_path()
        payload = {
            "window_geometry_b64": base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
            create_asset = menu.addAction("Create Asset…")
        elif context == "Shots":
            create_shot = menu.addAction("Create Shot…")

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

        target = self._project_root / "assets" / asset_type / asset_name
        if target.exists():
            return

        created: list[Path] = []
        try:
            to_create: list[Path] = [target]
            for d in departments:
                dept_dir = target / d
                to_create.append(dept_dir)
                if create_subfolders:
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

        created: list[Path] = []
        try:
            to_create: list[Path] = [target]
            for d in departments:
                dept_dir = target / d
                to_create.append(dept_dir)
                if create_subfolders:
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

