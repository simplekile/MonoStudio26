from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from monostudio.core.shell_open import open_folder as shell_open_folder
from monostudio.core.workspace_reader import DiscoveredProject, ProjectQuickStats
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.main_view import MainView
from monostudio.ui_qt.project_view_items import populate_project_browser_main_view
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, MonosMenu
from monostudio.ui_qt.view_items import ViewItem

_DIALOG_WIDTH = 640
_DIALOG_HEIGHT = 440


class ProjectPickerDialog(MonosDialog):
    """Full Projects browser (MainView project mode); switch via double-click or context menu."""

    project_selected = Signal(str)

    def __init__(
        self,
        *,
        workspace_root: Path | None,
        workspace_projects: list[DiscoveredProject],
        quick_stats_by_root: dict[str, ProjectQuickStats],
        status_by_root: dict[str, str],
        current_project_root: Path | None = None,
        thumbnail_manager: object | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectPickerDialog")
        self.setWindowTitle("Projects")
        self.setModal(True)

        self._workspace_root = workspace_root
        self._workspace_projects = list(workspace_projects)
        self._quick_stats = dict(quick_stats_by_root)
        self._status_by_root = dict(status_by_root)
        self._current_root = str(current_project_root) if current_project_root else None
        self._search_query = ""

        content_w, content_h = _DIALOG_WIDTH, _DIALOG_HEIGHT
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            content_w = min(content_w, max(480, int(avail.width() * 0.85)))
            content_h = min(content_h, max(360, int(avail.height() * 0.75)))
        self.resize(content_w, content_h)

        self._main_view = MainView(self)
        self._main_view.set_browser_context("project")
        self._main_view.set_search_placeholder("Search projects")
        if workspace_root is not None:
            self._main_view.set_workspace_root(workspace_root)
        if current_project_root is not None:
            self._main_view.set_project_root(str(current_project_root))
        if thumbnail_manager is not None:
            self._main_view.set_thumbnail_manager(thumbnail_manager)

        self._main_view.switch_project_requested.connect(self._on_switch_requested)
        self._main_view.item_activated.connect(self._on_item_activated)
        self._main_view.search_query_changed.connect(self._on_search_changed)
        self._main_view.root_context_menu_requested.connect(self._on_root_context_menu)
        self._main_view.project_status_chosen.connect(self._on_project_status_chosen)

        header = QWidget(self)
        header.setObjectName("ProjectPickerDialogTitleBar")
        header.setFixedHeight(40)
        _close_inset = 8
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, _close_inset, _close_inset, 0)
        header_lay.setSpacing(8)

        title = QLabel("PROJECTS", header)
        title.setObjectName("ProjectPickerDialogTitle")
        header_lay.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        header_lay.addStretch(1)

        close_btn = QToolButton(header)
        close_btn.setObjectName("ProjectPickerDialogCloseBtn")
        close_btn.setAutoRaise(False)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(28, 28)
        close_btn.setIcon(lucide_icon("x", size=16, color_hex="#fafafa"))
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.reject)
        header_lay.addWidget(
            close_btn,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._main_view, 1)

        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.reject)

        self._reload_items()

    def refresh_workspace_data(
        self,
        *,
        workspace_projects: list[DiscoveredProject] | None = None,
        quick_stats_by_root: dict[str, ProjectQuickStats] | None = None,
        status_by_root: dict[str, str] | None = None,
    ) -> None:
        if workspace_projects is not None:
            self._workspace_projects = list(workspace_projects)
        if quick_stats_by_root is not None:
            self._quick_stats = dict(quick_stats_by_root)
        if status_by_root is not None:
            self._status_by_root = dict(status_by_root)
        self._reload_items()

    def _on_search_changed(self, query: str) -> None:
        self._search_query = (query or "").strip()
        self._reload_items()

    def _reload_items(self) -> None:
        populate_project_browser_main_view(
            self._main_view,
            workspace_root=self._workspace_root,
            workspace_projects=self._workspace_projects,
            quick_stats_by_root=self._quick_stats,
            status_by_root=self._status_by_root,
            search_query=self._search_query,
            preserve_selection_id=self._current_root,
        )

    def _on_switch_requested(self, item) -> None:
        if hasattr(item, "path") and item.path:
            self.project_selected.emit(str(item.path))
            self.accept()

    def _on_item_activated(self, item: ViewItem) -> None:
        if item.path:
            self.project_selected.emit(str(item.path))
            self.accept()

    def _on_project_status_chosen(self, project_path, status) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "_on_project_browser_status_chosen"):
            parent._on_project_browser_status_chosen(project_path, status)
        if parent is not None:
            self.refresh_workspace_data(
                quick_stats_by_root=getattr(parent, "_workspace_project_quick_stats", None),
                status_by_root=getattr(parent, "_workspace_project_status", None),
            )
        else:
            self._reload_items()

    def _on_root_context_menu(self, global_pos) -> None:
        if self._workspace_root is None:
            return
        menu = MonosMenu(self)
        act_refresh = menu.addAction(
            lucide_icon("refresh-cw", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Refresh",
        )
        menu.addSeparator()
        new_proj = menu.addAction(
            lucide_icon("folder-plus", size=16, color_hex=MONOS_COLORS["text_label"]),
            "New Project…",
        )
        open_folder_act = None
        if self._current_root:
            open_folder_act = menu.addAction(
                lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open Project Folder",
            )
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == act_refresh:
            parent = self.parent()
            if parent is not None and hasattr(parent, "_apply_workspace_root"):
                parent._apply_workspace_root(
                    str(self._workspace_root) if self._workspace_root else None,
                    save=False,
                )
                if hasattr(parent, "_workspace_projects"):
                    self.refresh_workspace_data(
                        workspace_projects=list(parent._workspace_projects),
                        quick_stats_by_root=getattr(parent, "_workspace_project_quick_stats", None),
                        status_by_root=getattr(parent, "_workspace_project_status", None),
                    )
            return
        if chosen == new_proj:
            parent = self.parent()
            if parent is not None and hasattr(parent, "_new_project"):
                self.reject()
                parent._new_project()
            return
        if open_folder_act is not None and chosen == open_folder_act and self._current_root:
            shell_open_folder(Path(self._current_root))
