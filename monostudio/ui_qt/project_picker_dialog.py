from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from monostudio.core.workspace_reader import DiscoveredProject, ProjectQuickStats
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.project_view_items import build_project_view_items, project_picker_empty_message
from monostudio.ui_qt.style import MonosDialog
from monostudio.ui_qt.view_items import ViewItem


class ProjectPickerDialog(MonosDialog):
    """Browse workspace projects; switch on Open or double-click."""

    project_selected = Signal(str)

    def __init__(
        self,
        *,
        workspace_root: Path | None,
        workspace_projects: list[DiscoveredProject],
        quick_stats_by_root: dict[str, ProjectQuickStats],
        status_by_root: dict[str, str],
        current_project_root: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectPickerDialog")
        self.setWindowTitle("Projects")
        self.setModal(True)
        self.resize(880, 560)

        self._workspace_root = workspace_root
        self._workspace_projects = list(workspace_projects)
        self._quick_stats = dict(quick_stats_by_root)
        self._status_by_root = dict(status_by_root)
        self._current_root = str(current_project_root) if current_project_root else None
        self._search_query = ""

        self._main_view = MainView(self)
        self._main_view.set_browser_context("project")
        self._main_view.set_search_placeholder("Search projects")
        self._main_view.switch_project_requested.connect(self._on_switch_requested)
        self._main_view.item_activated.connect(self._on_item_activated)
        self._main_view.search_query_changed.connect(self._on_search_changed)
        self._main_view.primary_action_requested.connect(self._on_new_project)
        self._main_view.valid_selection_changed.connect(lambda _: self._update_open_enabled())
        self._main_view.set_primary_action(label="New Project", enabled=workspace_root is not None, tooltip=None)

        self._open_btn = QPushButton("Open")
        self._open_btn.setObjectName("DialogPrimaryButton")
        self._open_btn.setDefault(True)
        self._open_btn.clicked.connect(self._on_open_clicked)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)

        btn_row = QWidget(self)
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(10)
        btn_l.addWidget(self._open_btn)
        btn_l.addWidget(cancel_btn)
        btn_l.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self._main_view, 1)
        layout.addWidget(btn_row, 0)

        self._reload_items()
        self._update_open_enabled()

    def _on_new_project(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "_new_project"):
            self.reject()
            parent._new_project()

    def _on_search_changed(self, query: str) -> None:
        self._search_query = (query or "").strip()
        self._reload_items()

    def _apply_search(self, items: list[ViewItem]) -> list[ViewItem]:
        q = self._search_query.casefold()
        if not q:
            return items
        out: list[ViewItem] = []
        for it in items:
            name = (it.name or "").casefold()
            path = str(it.path or "").casefold()
            if q in name or q in path:
                out.append(it)
        return out

    def _reload_items(self) -> None:
        items = build_project_view_items(
            self._workspace_projects,
            quick_stats_by_root=self._quick_stats,
            status_by_root=self._status_by_root,
        )
        items = self._apply_search(items)
        if self._workspace_root is None:
            self._main_view.set_empty_override(project_picker_empty_message(workspace_root=None))
        elif not items and self._search_query:
            self._main_view.set_empty_override(f'No matches for "{self._search_query}"')
        elif not items:
            self._main_view.set_empty_override(project_picker_empty_message(workspace_root=self._workspace_root))
        else:
            self._main_view.set_empty_override(None)
        self._main_view.set_active_department(None)
        self._main_view.set_selected_asset_type(None)
        self._main_view.set_items(items, preserve_selection_id=self._current_root)
        self._update_open_enabled()

    def _selected_project_path(self) -> str | None:
        item = self._main_view.selected_view_item()
        if item is None or not getattr(item, "path", None):
            return None
        return str(item.path)

    def _update_open_enabled(self) -> None:
        self._open_btn.setEnabled(self._selected_project_path() is not None)

    def _on_switch_requested(self, item) -> None:
        if hasattr(item, "path") and item.path:
            self.project_selected.emit(str(item.path))
            self.accept()

    def _on_item_activated(self, item: ViewItem) -> None:
        if item.path:
            self.project_selected.emit(str(item.path))
            self.accept()

    def _on_open_clicked(self) -> None:
        path = self._selected_project_path()
        if path:
            self.project_selected.emit(path)
            self.accept()
