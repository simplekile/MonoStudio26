from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QDialog, QFileDialog, QMainWindow, QMenu, QMessageBox, QSplitter

from monostudio.core.fs_reader import build_project_index
from monostudio.core.models import Asset, Department, ProjectIndex, Shot
from monostudio.core.workspace_reader import DiscoveredProject, discover_projects
from monostudio.ui_qt.create_entry_dialogs import CreateAssetDialog, CreateShotDialog
from monostudio.ui_qt.inspector import (
    AssetShotInspectorData,
    DepartmentInspectorData,
    DepartmentStatusData,
    InspectorPanel,
)
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.new_project_dialog import NewProjectDialog
from monostudio.ui_qt.sidebar import Sidebar
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind


class MainWindow(QMainWindow):
    """
    Phase 0 shell:
    - 3 panels: Sidebar (~15%), Main View (~60%), Inspector (~25%, hidden by default)
    - No filesystem logic
    - No publish logic
    - No database
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MonoStudio 26")

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._workspace_root: Path | None = None
        self._workspace_projects: list[DiscoveredProject] = []

        self._project_root: Path | None = None
        self._project_index: ProjectIndex | None = None
        self._entered_parent: Asset | Shot | None = None

        self._sidebar = Sidebar()
        self._main_view = MainView()
        self._inspector = InspectorPanel()
        self._inspector.setMinimumWidth(240)

        self._build_menu()
        self._restore_workspace_root()
        self._restore_project_root()
        # Sync view menu checks to persisted state
        if self._settings.value("main_view/mode", "tile") == "list":
            self._view_list.setChecked(True)
        else:
            self._view_tile.setChecked(True)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._main_view)
        splitter.addWidget(self._inspector)

        # Initial ratio: Sidebar 15%, Main 60%, Inspector 25% (when visible).
        splitter.setStretchFactor(0, 15)
        splitter.setStretchFactor(1, 60)
        splitter.setStretchFactor(2, 25)
        splitter.setSizes([150, 600, 250])

        # Use splitter directly as the central widget
        self.setCentralWidget(splitter)

        self._sidebar.context_changed.connect(self._on_context_switched)
        self._sidebar.context_clicked.connect(self._on_context_clicked)
        self._main_view.valid_selection_changed.connect(self._on_valid_selection_changed)
        self._main_view.item_activated.connect(self._on_item_activated)
        self._main_view.refresh_requested.connect(self._on_refresh_requested)
        self._main_view.root_context_menu_requested.connect(self._on_root_context_menu_requested)

        # Initial population is driven by project-root restore (scan trigger) and current context.
        self._reload_main_view()
        self._inspector.set_empty_state()

    def _build_menu(self) -> None:
        # Minimal menu bar per requirement.
        file_menu = self.menuBar().addMenu("File")

        open_workspace_action = QAction("Open Workspace…", self)
        open_workspace_action.triggered.connect(self._open_workspace)
        file_menu.addAction(open_workspace_action)

        self._new_project_action = QAction("New Project…", self)
        self._new_project_action.setEnabled(False)
        self._new_project_action.triggered.connect(self._new_project)
        file_menu.addAction(self._new_project_action)

        open_project_root_action = QAction("Open Project Root…", self)
        open_project_root_action.triggered.connect(self._open_project_root)
        file_menu.addAction(open_project_root_action)

        # Project switching via menu (no combo box).
        self._project_menu = self.menuBar().addMenu("Project")
        self._project_menu.aboutToShow.connect(self._rebuild_project_menu)

        # View mode via menu (no combo box).
        view_menu = self.menuBar().addMenu("View")
        self._view_mode_group = QActionGroup(self)
        self._view_mode_group.setExclusive(True)
        self._view_tile = QAction("Tile", self, checkable=True)
        self._view_list = QAction("List", self, checkable=True)
        self._view_mode_group.addAction(self._view_tile)
        self._view_mode_group.addAction(self._view_list)
        self._view_tile.triggered.connect(lambda: self._main_view.set_view_mode("tile"))
        self._view_list.triggered.connect(lambda: self._main_view.set_view_mode("list"))
        view_menu.addAction(self._view_tile)
        view_menu.addAction(self._view_list)

    def _restore_workspace_root(self) -> None:
        path = self._settings.value("workspace/root", "", str)
        self._apply_workspace_root(path or None, save=False)

    def _restore_project_root(self) -> None:
        path = self._settings.value("project/root", "", str)
        self._apply_project_root(path or None, save=False)

    def _open_project_root(self) -> None:
        start_dir = self._settings.value("project/root", "", str)
        folder = QFileDialog.getExistingDirectory(
            self,
            "Open Project Root",
            start_dir or "",
        )
        if not folder:
            return

        self._apply_project_root(folder, save=True)

    def _open_workspace(self) -> None:
        start_dir = self._settings.value("workspace/root", "", str)
        folder = QFileDialog.getExistingDirectory(
            self,
            "Open Workspace",
            start_dir or "",
        )
        if not folder:
            return

        self._apply_workspace_root(folder, save=True)

    def _on_context_switched(self, context_name: str) -> None:
        # Trigger: user switches between Assets / Shots -> rescan synchronously.
        self._main_view.set_context_title(context_name)
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_empty_state()

    def _on_context_clicked(self, context_name: str) -> None:
        # Spec: click reloads Main View. (No autoscan trigger unless it was a switch.)
        self._main_view.set_context_title(context_name)
        self._entered_parent = None
        self._reload_main_view()
        self._inspector.set_empty_state()

    def _on_valid_selection_changed(self, has_selection: bool) -> None:
        if not has_selection:
            self._inspector.set_empty_state()
            return

        selected = self._main_view.selected_view_item()
        if selected is None:
            self._inspector.set_empty_state()
            return

        # Phase 2a: derive from existing in-memory scan results only.
        if selected.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            self._inspector.set_asset_shot(
                AssetShotInspectorData(
                    name=selected.name,
                    type=selected.type_badge,
                    absolute_path=str(selected.path),
                    created_date="—",
                    last_modified="—",
                )
            )
            return

        if selected.kind == ViewItemKind.DEPARTMENT and isinstance(selected.ref, Department):
            dept = selected.ref
            self._inspector.set_department(
                DepartmentInspectorData(
                    department_name=dept.name,
                    work_path=str(dept.work_path),
                    publish_path=str(dept.publish_path),
                ),
                DepartmentStatusData(
                    work_exists="Yes" if dept.work_exists else "No",
                    publish_exists="Yes" if dept.publish_exists else "No",
                    latest_version=dept.latest_publish_version or "—",
                    version_count=str(dept.publish_version_count),
                ),
            )
            return

        self._inspector.set_empty_state()

    def _apply_project_root(self, folder: str | None, *, save: bool) -> None:
        # No validation (per rules). Store path and reload UI.
        if save:
            self._settings.setValue("project/root", folder or "")

        self._project_root = Path(folder) if folder else None
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

        # After selecting a folder: keep layout stable, show neutral empty-state.
        self._inspector.set_empty_state()

        self._sync_project_menu_title()

    def _apply_workspace_root(self, folder: str | None, *, save: bool) -> None:
        # No validation. Read-only discovery.
        if save:
            self._settings.setValue("workspace/root", folder or "")

        self._workspace_root = Path(folder) if folder else None
        self._workspace_projects = discover_projects(self._workspace_root) if self._workspace_root else []
        self._new_project_action.setEnabled(self._workspace_root is not None)

        if not self._workspace_projects:
            self._main_view.set_empty_override("No projects found in this workspace")
        else:
            self._main_view.set_empty_override(None)

        # Menu will reflect current state on open; also update title now.
        self._sync_project_menu_title()

    def _rescan_project(self) -> None:
        # Autoscan must be deterministic and synchronous.
        if self._project_root is None:
            self._project_index = None
            return
        self._project_index = build_project_index(self._project_root)

    def _on_refresh_requested(self) -> None:
        # Trigger: user clicks Refresh (context menu) -> rescan synchronously.
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_empty_state()

    def _reload_main_view(self) -> None:
        if self._project_index is None:
            self._main_view.clear()
            return

        context = self._sidebar.currentItem().text() if self._sidebar.currentItem() else "Assets"
        items: list[ViewItem] = []

        if context == "Assets":
            if self._entered_parent is None:
                for asset in self._project_index.assets:
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
            else:
                parent = self._entered_parent
                if isinstance(parent, Asset):
                    for dept in parent.departments:
                        items.append(
                            ViewItem(
                                kind=ViewItemKind.DEPARTMENT,
                                name=dept.name,
                                type_badge="Department",
                                path=dept.path,
                                departments_count=None,
                                ref=dept,
                            )
                        )
        else:  # Shots
            if self._entered_parent is None:
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
                parent = self._entered_parent
                if isinstance(parent, Shot):
                    for dept in parent.departments:
                        items.append(
                            ViewItem(
                                kind=ViewItemKind.DEPARTMENT,
                                name=dept.name,
                                type_badge="Department",
                                path=dept.path,
                                departments_count=None,
                                ref=dept,
                            )
                        )

        # v1.1 clarity: if department list is empty, explain it inline (no warnings, no auto-creation).
        if self._project_root is not None and self._entered_parent is not None and len(items) == 0:
            self._main_view.set_empty_override("Departments appear when folders exist on disk.")
        elif self._project_root is not None:
            # In a project, use default empty states unless overridden by other flows.
            self._main_view.set_empty_override(None)

        self._main_view.set_items(items)

    def _on_item_activated(self, item: ViewItem) -> None:
        # Spec: Double click -> enter (asset/shot -> departments).
        if self._project_index is None:
            return

        context = self._sidebar.currentItem().text() if self._sidebar.currentItem() else "Assets"
        if context == "Assets" and item.kind == ViewItemKind.ASSET and isinstance(item.ref, Asset):
            self._entered_parent = item.ref
            self._reload_main_view()
            self._inspector.set_empty_state()
            return

        if context == "Shots" and item.kind == ViewItemKind.SHOT and isinstance(item.ref, Shot):
            self._entered_parent = item.ref
            self._reload_main_view()
            self._inspector.set_empty_state()
            return

    def _sync_project_menu_title(self) -> None:
        # Keep it short and stable.
        if not hasattr(self, "_project_menu"):
            return
        if self._project_root is None:
            self._project_menu.setTitle("Project")
            return
        self._project_menu.setTitle(f"Project: {self._project_root.name}")

    def _new_project(self) -> None:
        if self._workspace_root is None:
            return

        dialog = NewProjectDialog(self._workspace_root, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        name = dialog.project_name()
        location = dialog.location_dir()

        # Minimal, explicit safety: prevent path traversal / separators.
        if any(ch in name for ch in ("/", "\\", ":", "\n", "\r", "\t")):
            QMessageBox.critical(self, "New Project", "Invalid project name.")
            return

        if not location.is_dir():
            QMessageBox.critical(self, "New Project", "Location folder does not exist.")
            return

        project_root = location / name
        if project_root.exists():
            QMessageBox.critical(self, "New Project", "Target project folder already exists.")
            return

        try:
            # Create only inside the new project folder.
            (project_root / "assets").mkdir(parents=True, exist_ok=False)
            (project_root / "shots").mkdir(parents=True, exist_ok=False)
            monostudio_dir = project_root / ".monostudio"
            monostudio_dir.mkdir(parents=True, exist_ok=False)
            manifest = monostudio_dir / "project.json"
            manifest.write_text(
                json.dumps({"name": name, "schema": 1}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except FileExistsError:
            QMessageBox.critical(self, "New Project", "Target project folder already exists.")
            return
        except OSError:
            QMessageBox.critical(self, "New Project", "Failed to create project.")
            return

        # Do NOT rescan workspace from scratch; append to existing list.
        created = DiscoveredProject(name=name, root=project_root)
        self._workspace_projects.append(created)
        # Auto-switch to new project (sets project/root, triggers existing autoscan, resets Inspector).
        self._apply_project_root(str(project_root), save=True)

    def _rebuild_project_menu(self) -> None:
        # Rebuild dynamically when menu opens (no background work).
        self._project_menu.clear()
        self._sync_project_menu_title()

        if not self._workspace_projects:
            empty = QAction("No projects", self)
            empty.setEnabled(False)
            self._project_menu.addAction(empty)
            return

        group = QActionGroup(self._project_menu)
        group.setExclusive(True)
        current = str(self._project_root) if self._project_root else None

        for proj in self._workspace_projects:
            act = QAction(proj.name, self._project_menu, checkable=True)
            act.setData(str(proj.root))
            act.setChecked(current == str(proj.root))
            act.triggered.connect(lambda checked=False, p=str(proj.root): self._switch_project(p))
            group.addAction(act)
            self._project_menu.addAction(act)

    def _switch_project(self, project_root: str) -> None:
        if not project_root:
            return
        self._apply_project_root(project_root, save=True)
        self._sync_project_menu_title()

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

        context = self._sidebar.currentItem().text() if self._sidebar.currentItem() else "Assets"
        menu = QMenu(self)
        create_asset = None
        create_shot = None

        if context == "Assets":
            create_asset = menu.addAction("Create Asset…")
        else:
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

        dialog = CreateAssetDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        asset_type = dialog.asset_type()
        asset_name = dialog.asset_name()
        if not self._is_safe_single_folder_name(asset_type) or not self._is_safe_single_folder_name(asset_name):
            QMessageBox.critical(self, "Create Asset", "Names must be non-empty.")
            return

        target = self._project_root / "assets" / asset_type / asset_name
        if target.exists():
            QMessageBox.critical(self, "Create Asset", "Target folder already exists.")
            return

        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            QMessageBox.critical(self, "Create Asset", "Target folder already exists.")
            return
        except OSError:
            QMessageBox.critical(self, "Create Asset", "Failed to create asset folder.")
            return

        # After creation, trigger existing autoscan logic (Phase 1) for current project.
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_empty_state()

    def _create_shot(self) -> None:
        if self._project_root is None:
            return

        dialog = CreateShotDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        shot_name = dialog.shot_name()
        if not self._is_safe_single_folder_name(shot_name):
            QMessageBox.critical(self, "Create Shot", "Name must be non-empty.")
            return

        target = self._project_root / "shots" / shot_name
        if target.exists():
            QMessageBox.critical(self, "Create Shot", "Target folder already exists.")
            return

        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            QMessageBox.critical(self, "Create Shot", "Target folder already exists.")
            return
        except OSError:
            QMessageBox.critical(self, "Create Shot", "Failed to create shot folder.")
            return

        # After creation, trigger existing autoscan logic (Phase 1) for current project.
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_empty_state()

