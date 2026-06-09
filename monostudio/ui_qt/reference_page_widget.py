"""
Project Guide page: Inbox-style explorer (tile/list, path bar) per department under project_guide/.
No date folders — sidebar DEPARTMENTS selects the root folder (like Inbox Client/Freelancer).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from monostudio.core.project_guide_tags import read_all_tags
from monostudio.ui_qt.inbox_page_widget import _header_tool_button
from monostudio.ui_qt.inbox_split_view import InboxOutboxTitleRow, ProjectGuideTreePane

PROJECT_GUIDE_DEPARTMENTS = ("reference", "script", "storyboard", "guideline", "concept")


def get_project_guide_root(project_root: Path | None) -> Path | None:
    """Return <project_root>/<project_guide_folder> using StructureRegistry."""
    if not project_root:
        return None
    from monostudio.core.structure_registry import StructureRegistry

    struct_reg = StructureRegistry.for_project(project_root)
    return Path(project_root) / struct_reg.get_folder("project_guide")


def _normalize_department(department_id: str) -> str:
    key = (department_id or PROJECT_GUIDE_DEPARTMENTS[0]).strip().lower()
    return key if key in PROJECT_GUIDE_DEPARTMENTS else PROJECT_GUIDE_DEPARTMENTS[0]


def _department_folder_path(project_root: Path | None, department_id: str) -> Path | None:
    if project_root is None:
        return None
    guide_root = get_project_guide_root(project_root)
    if guide_root is None:
        return None
    dept = _normalize_department(department_id)
    candidate = guide_root / dept
    if candidate.is_dir():
        return candidate
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return guide_root if guide_root.is_dir() else None


class ReferencePageWidget(QWidget):
    """Project Guide: sidebar department + Inbox-style file explorer (no date folders)."""

    tree_selection_changed = Signal(object)  # Path | None
    drop_requested = Signal(object, object, bool)  # list[Path], target folder, copy_only
    import_requested = Signal()
    open_folder_requested = Signal(object)  # Path
    item_tags_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(False)
        self._project_root: Path | None = None
        self._department: str = PROJECT_GUIDE_DEPARTMENTS[0]
        self._header_badge_label: str | None = None
        self._header_badge_icon: str | None = None
        self._tree_state_cache: dict[str, dict] = {}
        self._tree_pane: ProjectGuideTreePane | None = None

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header_v = QVBoxLayout(header)
        header_v.setContentsMargins(12, 12, 12, 10)
        header_v.setSpacing(6)

        top_row = QWidget(header)
        hlay = QHBoxLayout(top_row)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(10)

        self._title_row = InboxOutboxTitleRow("Project Guide", root_icon="folder-open", parent=top_row)
        hlay.addWidget(self._title_row, 0, Qt.AlignmentFlag.AlignVCenter)
        hlay.addStretch(1)

        self._open_folder_btn = _header_tool_button(top_row, "Open folder", "folder-open")
        self._open_folder_btn.clicked.connect(self._on_open_folder_clicked)
        hlay.addWidget(self._open_folder_btn, 0)

        self._import_btn = _header_tool_button(top_row, "Import", "upload", primary=True)
        self._import_btn.clicked.connect(self._on_import_clicked)
        hlay.addWidget(self._import_btn, 0)
        header_v.addWidget(top_row, 0)

        self._path_bar_row = QWidget(header)
        self._path_bar_row.setObjectName("InboxPathBarRow")
        self._path_bar_lay = QHBoxLayout(self._path_bar_row)
        self._path_bar_lay.setContentsMargins(0, 0, 0, 0)
        self._path_bar_lay.setSpacing(12)
        self._path_bar_row.hide()
        header_v.addWidget(self._path_bar_row, 0)

        root_lay.addWidget(header, 0)

        self._content_host = QWidget(self)
        self._content_lay = QVBoxLayout(self._content_host)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(0)
        root_lay.addWidget(self._content_host, 1)
        self._refresh_chrome()

    def _mount_explorer_path_bar(self) -> None:
        if self._tree_pane is None:
            self._path_bar_row.hide()
            return
        bar = self._tree_pane.explorer_path_bar()
        toolbar = self._tree_pane.explorer_toolbar()
        if bar is None:
            self._path_bar_row.hide()
            return
        if bar.parent() is not self._path_bar_row:
            bar.setParent(self._path_bar_row)
        if self._path_bar_lay.indexOf(bar) < 0:
            self._path_bar_lay.addWidget(bar, 1)
        if toolbar is not None:
            toolbar.set_inline(True)
            if toolbar.parent() is not self._path_bar_row:
                toolbar.setParent(self._path_bar_row)
            if self._path_bar_lay.indexOf(toolbar) < 0:
                self._path_bar_lay.addWidget(toolbar, 0, Qt.AlignmentFlag.AlignVCenter)
            toolbar.show()
        self._path_bar_row.show()

    def _refresh_chrome(self) -> None:
        self._title_row.set_context(
            type_filter=self._department,
            date_path=None,
            unified_tree=True,
            badge_role="department",
            badge_label=self._header_badge_label,
            badge_icon=self._header_badge_icon,
        )
        self._mount_explorer_path_bar()

    def set_header_badge_display(self, *, label: str | None = None, icon_name: str | None = None) -> None:
        self._header_badge_label = (label or "").strip() or None
        self._header_badge_icon = (icon_name or "").strip() or None
        self._refresh_chrome()

    def _tree_state_key(self, department_id: str) -> str:
        return _normalize_department(department_id)

    def _ensure_tree_pane(self) -> None:
        root = _department_folder_path(self._project_root, self._department)
        guide_root = get_project_guide_root(self._project_root)
        if root is None:
            return
        if self._tree_pane is None:
            self._tree_pane = ProjectGuideTreePane(
                root,
                self._content_host,
                project_root=self._project_root,
                project_guide_root=guide_root,
                source_filter=self._department,
            )
            self._tree_pane.tree_selection_changed.connect(self._on_tree_selection)
            self._tree_pane.open_folder_requested.connect(self.open_folder_requested.emit)
            self._tree_pane.import_requested.connect(self.import_requested.emit)
            self._tree_pane.external_drop_requested.connect(self.drop_requested.emit)
            self._tree_pane.item_tags_changed.connect(self.item_tags_changed.emit)
            self._content_lay.addWidget(self._tree_pane, 1)
        else:
            self._tree_pane.set_project_guide_root(guide_root, self._project_root)
            if self._project_root:
                self._tree_pane.set_tag_data(read_all_tags(self._project_root))
                self._tree_pane.reload_tag_definitions()
            self._tree_pane.set_date_folder_path(root)
            self._tree_pane.set_chrome_context(self._department, None)
        key = self._tree_state_key(self._department)
        saved = self._tree_state_cache.get(key)
        if saved:
            self._tree_pane.set_tree_state(saved)
        self._refresh_chrome()

    def _on_tree_selection(self, path) -> None:
        self.tree_selection_changed.emit(path)

    def _on_open_folder_clicked(self) -> None:
        if self._tree_pane is not None:
            target = self._tree_pane.current_browse_path()
        else:
            target = _department_folder_path(self._project_root, self._department)
        if target is not None:
            self.open_folder_requested.emit(target)

    def _on_import_clicked(self) -> None:
        self.import_requested.emit()

    def set_project_root(self, path: Path | None) -> None:
        self._project_root = Path(path) if path else None
        self._ensure_tree_pane()
        if self._tree_pane is not None:
            guide_root = get_project_guide_root(self._project_root)
            self._tree_pane.set_project_guide_root(guide_root, self._project_root)
            if self._project_root:
                self._tree_pane.set_tag_data(read_all_tags(self._project_root))
                self._tree_pane.reload_tag_definitions()
            self._tree_pane.refresh_content()

    def set_department(self, department_id: str) -> None:
        new_dept = _normalize_department(department_id)
        if new_dept == self._department and self._tree_pane is not None:
            self._refresh_chrome()
            return
        if self._tree_pane is not None and self._department:
            self._tree_state_cache[self._tree_state_key(self._department)] = self._tree_pane.get_tree_state()
        self._department = new_dept
        self._ensure_tree_pane()
        self._refresh_chrome()

    def set_tag_filter(self, tag_id: str | None) -> None:
        if self._tree_pane is not None:
            self._tree_pane.set_tag_filter(tag_id)

    def get_item_tags(self) -> dict[str, list[str]]:
        if self._tree_pane is not None:
            return self._tree_pane.get_item_tags()
        if self._project_root:
            return read_all_tags(self._project_root)
        return {}

    def reload_tag_definitions(self) -> None:
        if self._tree_pane is not None:
            self._tree_pane.reload_tag_definitions()

    def refresh_tree(self) -> None:
        if self._tree_pane is not None:
            self._tree_pane.refresh_content()
