"""
Project Guide page: header (MainView style) + tree by department under project_guide/ (reference, script, storyboard, guideline, concept).
Inspector shows file preview when item selected in tree (same as Inbox).
Supports: drag-drop onto page/tree, Import button, New folder, Delete, Open folder.
"""
from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)
# Debug: logging.getLogger("monostudio.ui_qt.reference_page_widget").setLevel(logging.DEBUG)

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.project_guide_tags import read_all_tags
from monostudio.ui_qt.inbox_split_view import ReferenceTreePane
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification import notify as notification_service
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

PROJECT_GUIDE_DEPARTMENTS = ("reference", "script", "storyboard", "guideline", "concept")


def get_project_guide_root(project_root: Path | None) -> Path | None:
    """Return <project_root>/<project_guide_folder> using StructureRegistry."""
    if not project_root:
        return None
    from monostudio.core.structure_registry import StructureRegistry
    struct_reg = StructureRegistry.for_project(project_root)
    return Path(project_root) / struct_reg.get_folder("project_guide")


class ReferencePageWidget(QWidget):
    """
    Project Guide page: (1) Header like MainView (Project Guide + department badge + Import);
    (2) Tree for selected department under project_guide/. Supports drag-drop, New folder, Delete, Import.
    Emits tree_selection_changed(Path|None), drop_requested(list[Path]), import_requested(), open_folder_requested(Path).
    """

    tree_selection_changed = Signal(object)  # Path | None
    drop_requested = Signal(object)  # list[Path]
    import_requested = Signal()
    open_folder_requested = Signal(object)  # Path
    item_tags_changed = Signal()  # emitted when tags are assigned/removed (for sidebar count refresh)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Don't accept drops here: frameless MainWindow receives drop on Windows; it forwards by position
        self.setAcceptDrops(False)
        self._project_root: Path | None = None
        self._department: str = ""
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Header (MainView style) + Import button
        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(12, 12, 12, 12)
        hlay.setSpacing(12)
        self._context_title = QLabel("Project Guide", header)
        self._context_title.setObjectName("MainViewContextTitle")
        self._context_title.setFont(monos_font("Inter", 16, QFont.Weight.Bold))
        self._dept_badge = QWidget(header)
        self._dept_badge.setObjectName("MainViewTypeBadge")
        self._dept_badge.setAttribute(Qt.WA_StyledBackground, True)
        badge_lay = QHBoxLayout(self._dept_badge)
        badge_lay.setContentsMargins(8, 4, 10, 4)
        badge_lay.setSpacing(6)
        self._dept_icon = QLabel(self._dept_badge)
        self._dept_icon.setFixedSize(16, 16)
        self._dept_label = QLabel(self._dept_badge)
        self._dept_label.setObjectName("MainViewTypeBadgeLabel")
        self._dept_label.setFont(monos_font("Inter", 13, QFont.Weight.Bold))
        badge_lay.addWidget(self._dept_icon, 0, Qt.AlignVCenter)
        badge_lay.addWidget(self._dept_label, 0, Qt.AlignVCenter)
        hlay.addWidget(self._context_title, 0, Qt.AlignVCenter)
        hlay.addWidget(self._dept_badge, 0, Qt.AlignVCenter)
        hlay.addStretch(1)
        import_btn = QPushButton("Import", header)
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_icon = lucide_icon("upload", size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        if not import_icon.isNull():
            import_btn.setIcon(import_icon)
        import_btn.clicked.connect(self._on_import_clicked)
        hlay.addWidget(import_btn, 0)
        root_lay.addWidget(header, 0)

        # Tree (root = project_guide/<department>)
        ref_root = get_project_guide_root(None)
        dept_label = (self._department or "reference").replace("_", " ").title()
        self._tree_pane = ReferenceTreePane(ref_root, dept_label, self)
        self._tree_pane.tree_selection_changed.connect(self.tree_selection_changed.emit)
        self._tree_pane.open_folder_requested.connect(self.open_folder_requested.emit)
        self._tree_pane.import_requested.connect(self.import_requested.emit)
        self._tree_pane.item_tags_changed.connect(self.item_tags_changed.emit)
        root_lay.addWidget(self._tree_pane, 1)

        self._update_dept_badge()

    def _on_import_clicked(self) -> None:
        self.import_requested.emit()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        _log.debug("Project Guide dragEnterEvent: hasUrls=%s", event.mimeData().hasUrls())
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        _log.debug("Project Guide dropEvent: received")
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.exists():
                    paths.append(p)
        event.acceptProposedAction()
        if not paths:
            _log.debug("Project Guide dropEvent: no valid paths")
            return
        # Resolve drop target by position (like Inbox: page receives all drops)
        pos_in_page = event.position().toPoint()
        pos_in_pane = self._tree_pane.mapFrom(self, pos_in_page)
        pane_rect = self._tree_pane.rect()
        in_pane = pane_rect.contains(pos_in_pane)
        _log.debug(
            "Project Guide dropEvent: pos_page=%s,%s pos_in_pane=%s,%s pane_rect=%s in_pane=%s paths=%s",
            pos_in_page.x(), pos_in_page.y(),
            pos_in_pane.x(), pos_in_pane.y(),
            (pane_rect.x(), pane_rect.y(), pane_rect.width(), pane_rect.height()),
            in_pane,
            [str(p) for p in paths],
        )
        if in_pane:
            target = self._tree_pane.get_drop_target_folder(pos_in_pane)
            _log.debug("Project Guide dropEvent: target=%s is_dir=%s", target, target.is_dir() if target else None)
            if target and target.is_dir():
                self._tree_pane.drop_files_to_folder(paths, target)
                notification_service.success(f"Added {len(paths)} item{'s' if len(paths) != 1 else ''} to Project Guide.")
                return
        _log.debug("Project Guide dropEvent: emitting drop_requested (fallback to dept root)")
        self.drop_requested.emit(paths)

    def _update_dept_badge(self) -> None:
        d = (self._department or "").strip()
        if not d:
            self._dept_badge.setVisible(False)
            return
        label = d.replace("_", " ").title()
        icon = lucide_icon("folder", size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        self._dept_icon.setPixmap(icon.pixmap(16, 16))
        self._dept_label.setText(label.upper())
        self._dept_badge.setVisible(True)

    def set_project_root(self, path: Path | None) -> None:
        self._project_root = Path(path) if path else None
        self._refresh_tree_root()

    def set_department(self, department_id: str) -> None:
        self._department = (department_id or "").strip().lower()
        self._update_dept_badge()
        self._refresh_tree_root()

    def _refresh_tree_root(self) -> None:
        root = get_project_guide_root(self._project_root)
        dept = (self._department or "").strip()
        if not dept or dept not in PROJECT_GUIDE_DEPARTMENTS:
            dept = PROJECT_GUIDE_DEPARTMENTS[0]
        label = dept.replace("_", " ").title()
        if root and dept:
            folder = root / dept
            if not folder.is_dir():
                folder = root
        else:
            folder = root
        self._tree_pane.set_project_guide_root(root, self._project_root)
        if self._project_root:
            self._tree_pane.set_tag_data(read_all_tags(self._project_root))
        self._tree_pane.set_root(folder, label)

    def set_tag_filter(self, tag_id: str | None) -> None:
        self._tree_pane.set_tag_filter(tag_id)

    def get_item_tags(self) -> dict[str, list[str]]:
        """Item tags for Project Guide (path -> tag_ids). Used e.g. for tag-filter toast count."""
        return self._tree_pane.get_item_tags()

    def reload_tag_definitions(self) -> None:
        self._tree_pane.reload_tag_definitions()


