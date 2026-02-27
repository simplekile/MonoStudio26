"""
Project Guide page: header (MainView style) + tree by department under project_guide/ (reference, script, storyboard, guideline, concept).
Inspector shows file preview when item selected in tree (same as Inbox).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.inbox_split_view import ReferenceTreePane
from monostudio.ui_qt.lucide_icons import lucide_icon
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
    Project Guide page: (1) Header like MainView (Project Guide + department badge);
    (2) Tree for selected department under project_guide/ (reference/, script/, storyboard/, guideline/, concept/).
    Department is chosen in sidebar. Emits tree_selection_changed(Path|None) for inspector preview.
    """

    tree_selection_changed = Signal(object)  # Path | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._department: str = ""
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Header (MainView style)
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
        root_lay.addWidget(header, 0)

        # Tree (root = project_guide/<department>)
        ref_root = get_project_guide_root(None)
        dept_label = (self._department or "reference").replace("_", " ").title()
        self._tree_pane = ReferenceTreePane(ref_root, dept_label, self)
        self._tree_pane.tree_selection_changed.connect(self.tree_selection_changed.emit)
        root_lay.addWidget(self._tree_pane, 1)

        self._update_dept_badge()

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
                folder = root  # fallback to project_guide/ if department subdir missing
        else:
            folder = root
        self._tree_pane.set_root(folder, label)


