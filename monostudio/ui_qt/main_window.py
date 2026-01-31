from __future__ import annotations

import base64
import json
import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QSettings
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow, QMenu, QMessageBox, QSplitter

from monostudio.core.fs_reader import build_project_index
from monostudio.core.models import Asset, Department, ProjectIndex, Shot
from monostudio.core.workspace_reader import DiscoveredProject, discover_projects
from monostudio.core.pipeline_types_and_presets import ensure_pipeline_bootstrap
from monostudio.ui_qt.create_entry_dialogs import CreateAssetDialog, CreateShotDialog
from monostudio.ui_qt.inspector import (
    AssetShotInspectorData,
    DepartmentInspectorData,
    DepartmentStatusData,
    InspectorPanel,
)
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.new_project_dialog import NewProjectDialog
from monostudio.ui_qt.settings_dialog import SettingsDialog
from monostudio.ui_qt.sidebar import Sidebar
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind
from monostudio.ui_qt.delete_confirm_dialog import DeleteConfirmDialog


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
        self.setWindowTitle("MONOS")
        ensure_pipeline_bootstrap()

        # Minimum window size (usability floor).
        self.setMinimumSize(640, 480)

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
        self._restore_window_geometry()

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._main_view)
        splitter.addWidget(self._inspector)

        # Initial ratio: Sidebar 15%, Main 60%, Inspector 25% (when visible).
        splitter.setStretchFactor(0, 15)
        splitter.setStretchFactor(1, 60)
        splitter.setStretchFactor(2, 25)
        splitter.setSizes([256, 600, 250])

        # Use splitter directly as the central widget
        self.setCentralWidget(splitter)

        self._sidebar.context_changed.connect(self._on_context_switched)
        self._sidebar.context_clicked.connect(self._on_context_clicked)
        self._sidebar.context_menu_requested.connect(self._on_sidebar_context_menu_requested)
        self._sidebar.settings_requested.connect(self._open_settings)
        self._main_view.valid_selection_changed.connect(self._on_valid_selection_changed)
        self._main_view.item_activated.connect(self._on_item_activated)
        self._main_view.refresh_requested.connect(self._on_refresh_requested)
        self._main_view.root_context_menu_requested.connect(self._on_root_context_menu_requested)
        self._main_view.copy_inventory_requested.connect(self._on_copy_item_inventory_requested)
        self._main_view.delete_requested.connect(self._on_delete_requested)
        self._main_view.primary_action_requested.connect(self._on_primary_action_requested)
        self._main_view.view_mode_changed.connect(self._sync_view_menu_checks)

        # Initial population is driven by project-root restore (scan trigger) and current context.
        self._reload_main_view()
        self._inspector.set_empty_state()
        self._sync_primary_action()
        self._sync_view_menu_checks()

    def _sync_view_menu_checks(self) -> None:
        if not hasattr(self, "_view_tile") or not hasattr(self, "_view_list"):
            return
        mode = getattr(self._main_view, "_view_mode", "tile")
        self._view_tile.setChecked(mode == "tile")
        self._view_list.setChecked(mode == "list")

    def _sync_primary_action(self) -> None:
        context = self._sidebar.current_context()
        if context == "Projects":
            enabled = self._workspace_root is not None
            tooltip = None if enabled else "Open Workspace… to create a new project"
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

        tools_menu = self.menuBar().addMenu("Tools")
        copy_inventory_action = QAction("Copy Project Inventory", self)
        copy_inventory_action.triggered.connect(self._copy_project_inventory)
        tools_menu.addAction(copy_inventory_action)

        # Project switching via menu (no combo box).
        self._project_menu = self.menuBar().addMenu("Project")
        self._project_menu.aboutToShow.connect(self._rebuild_project_menu)

        settings_menu = self.menuBar().addMenu("Settings")
        open_settings = QAction("Settings…", self)
        open_settings.triggered.connect(self._open_settings)
        settings_menu.addAction(open_settings)

        # View mode via menu (no combo box).
        view_menu = self.menuBar().addMenu("View")
        self._view_mode_group = QActionGroup(self)
        self._view_mode_group.setExclusive(True)
        self._view_tile = QAction("Grid", self, checkable=True)
        self._view_list = QAction("List", self, checkable=True)
        self._view_mode_group.addAction(self._view_tile)
        self._view_mode_group.addAction(self._view_list)
        self._view_tile.triggered.connect(lambda: self._main_view.set_view_mode("tile"))
        self._view_list.triggered.connect(lambda: self._main_view.set_view_mode("list"))
        view_menu.addAction(self._view_tile)
        view_menu.addAction(self._view_list)

    def _copy_project_inventory(self) -> None:
        """
        v1.2 Candidate 3:
        - Explicit trigger only (Tools menu)
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
        self._inspector.set_empty_state()

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
        # Trigger: user switches between top-level contexts.
        self._main_view.set_context_title(context_name)
        self._entered_parent = None
        if context_name in ("Assets", "Shots"):
            # Deterministic autoscan trigger (locked rule).
            self._rescan_project()
            self._reload_main_view()
        else:
            # Non-project-browser areas are placeholders (no scans, no side effects).
            self._main_view.clear()
            self._main_view.set_empty_override(self._empty_message_for_context(context_name))
        self._inspector.set_empty_state()
        self._sync_primary_action()

    def _on_context_clicked(self, context_name: str) -> None:
        # Spec: click reloads Main View. (No autoscan trigger unless it was a switch.)
        self._main_view.set_context_title(context_name)
        self._entered_parent = None
        if context_name in ("Assets", "Shots"):
            self._reload_main_view()
        else:
            self._main_view.clear()
            self._main_view.set_empty_override(self._empty_message_for_context(context_name))
        self._inspector.set_empty_state()
        self._sync_primary_action()

    def _empty_message_for_context(self, context_name: str) -> str:
        if context_name == "Projects":
            if self._workspace_root is None:
                return "Open Workspace… to discover projects."
            if not self._workspace_projects:
                return "No projects found in this workspace"
            return "Use Project menu to switch projects."
        return f"{context_name} is not available yet."

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
        self._sidebar.set_project_index(self._project_index)

        # After selecting a folder: keep layout stable, show neutral empty-state.
        self._inspector.set_empty_state()

        self._sync_project_menu_title()
        self._sync_primary_action()

    def _apply_workspace_root(self, folder: str | None, *, save: bool) -> None:
        # No validation. Read-only discovery.
        if save:
            self._settings.setValue("workspace/root", folder or "")

        self._workspace_root = Path(folder) if folder else None
        self._workspace_projects = discover_projects(self._workspace_root) if self._workspace_root else []
        self._new_project_action.setEnabled(self._workspace_root is not None)
        self._sidebar.set_projects_count(len(self._workspace_projects) if self._workspace_root is not None else None)

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
            self._sidebar.set_project_index(None)
            return
        self._project_index = build_project_index(self._project_root)
        self._sidebar.set_project_index(self._project_index)

    def _on_refresh_requested(self) -> None:
        # Trigger: user clicks Refresh (context menu) -> rescan synchronously.
        self._entered_parent = None
        self._rescan_project()
        self._reload_main_view()
        self._inspector.set_empty_state()
        self._sync_primary_action()

    def _reload_main_view(self) -> None:
        context = self._sidebar.current_context()
        items: list[ViewItem] = []

        # Projects context: show workspace discovery results (read-only).
        if context == "Projects":
            for proj in self._workspace_projects:
                items.append(
                    ViewItem(
                        kind=ViewItemKind.PROJECT,
                        name=proj.name,
                        type_badge="project",
                        path=proj.root,
                        departments_count=None,
                        ref=proj,
                    )
                )
            self._main_view.set_empty_override(None if items else "No projects found in this workspace")
            self._main_view.set_items(items)
            return

        if self._project_index is None:
            self._main_view.clear()
            return

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
        elif context == "Shots":
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
        else:
            self._main_view.clear()
            self._main_view.set_empty_override(self._empty_message_for_context(context))
            return

        # v1.1 clarity: if department list is empty, explain it inline (no warnings, no auto-creation).
        if self._project_root is not None and self._entered_parent is not None and len(items) == 0:
            self._main_view.set_empty_override("Departments appear when folders exist on disk.")
        elif self._project_root is not None:
            # In a project, use default empty states unless overridden by other flows.
            self._main_view.set_empty_override(None)

        self._main_view.set_items(items)
        self._sidebar.set_project_index(self._project_index)

    def _on_item_activated(self, item: ViewItem) -> None:
        # Spec: Double click -> enter (asset/shot -> departments).
        if item.kind == ViewItemKind.PROJECT:
            # Explicit action: open/switch project by double-clicking a project card.
            self._switch_project(str(item.path))
            return
        if self._project_index is None:
            return

        context = self._sidebar.current_context()
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

    def _open_settings(self) -> None:
        dialog = SettingsDialog(parent=self)
        dialog.exec()

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
        self._inspector.set_empty_state()

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
        self._inspector.set_empty_state()

