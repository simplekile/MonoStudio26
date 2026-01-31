from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, ProjectIndex, Shot
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS


class SidebarContext(str, Enum):
    DASHBOARD = "Dashboard"
    PROJECTS = "Projects"
    SHOTS = "Shots"
    ASSETS = "Assets"
    LIBRARY = "Library"
    DEPARTMENTS = "Departments"


class _SidebarNavItemWidget(QWidget):
    """
    Primary Nav item (Alignment Matrix locked):
    - height: 36px
    - padding: px-3 (12px) / py-2 (8px)
    - left group: [ icon_container 24x24 ] gap 12px [ label 13px ]
    - right group: [ count badge ] flush-right
    - active indicator: 2px x 16px at left-0, vertically centered, zero-shift
    """

    def __init__(self, context_name: str, icon_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarNavItem")
        self.setProperty("active", False)
        self._context_name = context_name
        self._icon_name = icon_name

        # Fixed height
        self.setMinimumHeight(36)
        self.setMaximumHeight(36)

        # Indicator is absolute-positioned (no text shift).
        self._indicator = QFrame(self)
        self._indicator.setObjectName("SidebarNavIndicator")
        self._indicator.setProperty("active", False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)  # px-3 / py-2
        layout.setSpacing(0)

        left = QWidget(self)
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)  # gap-3

        self._icon_container = QLabel(left)
        self._icon_container.setObjectName("SidebarNavIconContainer")
        self._icon_container.setAlignment(Qt.AlignCenter)
        self._icon_container.setFixedSize(24, 24)
        self._sync_icon(active=False)

        self._label = QLabel(context_name, left)
        self._label.setObjectName("SidebarNavLabel")
        f_label = QFont("Inter", 13)
        f_label.setWeight(QFont.Weight.DemiBold)
        f_label.setLetterSpacing(QFont.PercentageSpacing, 97)  # tracking-tight
        self._label.setFont(f_label)

        left_layout.addWidget(self._icon_container)
        left_layout.addWidget(self._label, 1)

        self._badge = QLabel("", self)
        self._badge.setObjectName("SidebarNavBadge")
        f_badge = QFont("Inter", 10)
        f_badge.setWeight(QFont.Weight.DemiBold)
        self._badge.setFont(f_badge)
        self._badge.setVisible(False)

        layout.addWidget(left, 1)
        layout.addWidget(self._badge, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.setMouseTracking(True)

    def context_name(self) -> str:
        return self._context_name

    def set_active(self, active: bool) -> None:
        self.setProperty("active", bool(active))
        self._indicator.setProperty("active", bool(active))
        self._sync_icon(active=bool(active))
        # Force re-style for dynamic properties.
        self.style().unpolish(self._indicator)
        self.style().polish(self._indicator)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _sync_icon(self, *, active: bool) -> None:
        # Default icon color = label; active icon color = action text (Blue-400).
        color = MONOS_COLORS["blue_400"] if active else MONOS_COLORS["text_label"]
        ic = lucide_icon(self._icon_name, size=16, color_hex=color)
        self._icon_container.setPixmap(ic.pixmap(16, 16))

    def set_count_badge(self, value: int | None) -> None:
        if value is None:
            self._badge.setVisible(False)
            self._badge.setText("")
            return
        self._badge.setText(str(value))
        self._badge.setVisible(True)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # 2px x 16px, flush left, vertically centered
        y = max(0, (self.height() - 16) // 2)
        self._indicator.setGeometry(0, y, 2, 16)


class Sidebar(QWidget):
    """
    MONOS Sidebar (fixed 256px) with 4 blocks:
    1) Brand + Primary Nav (top fixed)
    2) Hierarchy (scrollable): search + tree
    3) Recent Tasks (bottom of scroll) — placeholder only
    4) Global Settings (bottom fixed)
    """

    context_changed = Signal(str)  # emitted when selection changes (nav)
    context_clicked = Signal(str)  # emitted when clicking already-selected nav item
    context_menu_requested = Signal(str, object)  # (context_text, global_pos) for nav items
    settings_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarContainer")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumWidth(256)
        self.setMaximumWidth(256)

        self._last_context_text: str | None = None
        self._project_index: ProjectIndex | None = None
        self._projects_count: int | None = None

        self._nav_widgets: dict[str, _SidebarNavItemWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Block 1: Brand + Primary Nav (top fixed)
        top = QWidget(self)
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(16, 16, 16, 16)  # p-4
        top_layout.setSpacing(12)

        brand = QWidget(top)
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)  # icon-to-text ~12px

        icon_box = QLabel("M", brand)
        icon_box.setObjectName("SidebarBrandIcon")
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setFixedSize(28, 28)

        brand_label = QLabel("MONOS", brand)
        brand_label.setObjectName("SidebarBrandLabel")
        f_brand = QFont("Inter", 14)
        f_brand.setWeight(QFont.Weight.Bold)  # 700
        f_brand.setLetterSpacing(QFont.PercentageSpacing, 97)  # tracking-tight
        brand_label.setFont(f_brand)

        brand_layout.addWidget(icon_box)
        brand_layout.addWidget(brand_label, 1)

        self._nav = QListWidget(top)
        self._nav.setObjectName("SidebarPrimaryNav")
        self._nav.setSelectionMode(QAbstractItemView.SingleSelection)
        self._nav.setUniformItemSizes(True)
        self._nav.setSpacing(4)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._nav.setFocusPolicy(Qt.NoFocus)
        self._nav.setContextMenuPolicy(Qt.CustomContextMenu)
        self._nav.customContextMenuRequested.connect(self._on_nav_context_menu_requested)

        self._nav.currentItemChanged.connect(self._on_current_nav_item_changed)
        self._nav.itemClicked.connect(self._on_nav_item_clicked)

        # Primary nav items (Lucide, order locked by spec)
        self._add_nav_item(SidebarContext.DASHBOARD.value, "layout-dashboard")
        self._add_nav_item(SidebarContext.PROJECTS.value, "folder-kanban")
        self._add_nav_item(SidebarContext.SHOTS.value, "clapperboard")
        self._add_nav_item(SidebarContext.ASSETS.value, "box")
        self._add_nav_item(SidebarContext.LIBRARY.value, "library")
        self._add_nav_item(SidebarContext.DEPARTMENTS.value, "layers")

        top_layout.addWidget(brand, 0)
        top_layout.addWidget(self._nav, 0)

        # --- Block 2+3: Scrollable center (Hierarchy + Recent Tasks)
        scroll = QScrollArea(self)
        scroll.setObjectName("SidebarScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_inner = QWidget(scroll)
        scroll_layout = QVBoxLayout(scroll_inner)
        scroll_layout.setContentsMargins(16, 0, 16, 16)  # side padding only
        scroll_layout.setSpacing(24)  # mt-6 between sections

        # Section: HIERARCHY
        hierarchy_block = QWidget(scroll_inner)
        hierarchy_layout = QVBoxLayout(hierarchy_block)
        hierarchy_layout.setContentsMargins(0, 0, 0, 0)
        hierarchy_layout.setSpacing(8)

        hierarchy_header = QLabel("HIERARCHY", hierarchy_block)
        hierarchy_header.setObjectName("SidebarSectionHeader")
        f_h = QFont("Inter", 10)
        f_h.setWeight(QFont.Weight.ExtraBold)  # 800
        f_h.setLetterSpacing(QFont.PercentageSpacing, 112)  # tracking-widest-ish
        hierarchy_header.setFont(f_h)

        self._tree_search = QLineEdit(hierarchy_block)
        self._tree_search.setObjectName("SidebarTreeSearch")
        self._tree_search.setPlaceholderText("Filter tree")
        self._tree_search.setClearButtonEnabled(True)
        self._tree_search.textChanged.connect(self._apply_tree_filter)

        self._tree = QTreeWidget(hierarchy_block)
        self._tree.setObjectName("SidebarHierarchyTree")
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)  # 14px per level
        self._tree.setAnimated(False)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.setFocusPolicy(Qt.NoFocus)
        self._tree.itemExpanded.connect(lambda _: self._apply_tree_filter(self._tree_search.text()))
        self._tree.itemCollapsed.connect(lambda _: self._apply_tree_filter(self._tree_search.text()))

        hierarchy_layout.addWidget(hierarchy_header, 0)
        hierarchy_layout.addWidget(self._tree_search, 0)
        hierarchy_layout.addWidget(self._tree, 1)

        # Section: RECENT TASKS (placeholder UI only)
        tasks_block = QWidget(scroll_inner)
        tasks_layout = QVBoxLayout(tasks_block)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        tasks_layout.setSpacing(8)

        tasks_header = QLabel("RECENT TASKS", tasks_block)
        tasks_header.setObjectName("SidebarSectionHeader")
        tasks_header.setFont(f_h)

        tasks_empty = QLabel("No tasks", tasks_block)
        tasks_empty.setObjectName("SidebarMutedText")

        tasks_layout.addWidget(tasks_header, 0)
        tasks_layout.addWidget(tasks_empty, 0)
        tasks_layout.addStretch(1)

        scroll_layout.addWidget(hierarchy_block, 1)
        scroll_layout.addWidget(tasks_block, 0)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_inner)

        # --- Block 4: Global Settings (bottom fixed)
        bottom = QWidget(self)
        bottom.setObjectName("SidebarBottom")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 12, 16, 16)
        bottom_layout.setSpacing(8)

        self._settings_btn = QPushButton("Global Settings", bottom)
        self._settings_btn.setObjectName("SidebarSettingsButton")
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        self._settings_btn.clicked.connect(self.settings_requested.emit)

        bottom_layout.addWidget(self._settings_btn, 0)

        root.addWidget(top, 0)
        root.addWidget(scroll, 1)
        root.addWidget(bottom, 0)

        # Default context: Assets (keeps existing workflow stable)
        self.set_current_context(SidebarContext.ASSETS.value)

        # Start with empty hierarchy until MainWindow provides an index.
        self.set_project_index(None)

    def _add_nav_item(self, label: str, icon_name: str) -> None:
        it = QListWidgetItem("")
        it.setData(Qt.UserRole, label)
        it.setSizeHint(QSize(0, 36))  # keep height locked
        self._nav.addItem(it)

        w = _SidebarNavItemWidget(label, icon_name, parent=self._nav)
        self._nav.setItemWidget(it, w)
        self._nav_widgets[label] = w

    def current_context(self) -> str:
        item = self._nav.currentItem()
        if item is None:
            return SidebarContext.ASSETS.value
        v = item.data(Qt.UserRole)
        return str(v) if isinstance(v, str) and v else SidebarContext.ASSETS.value

    def set_current_context(self, context_name: str) -> None:
        for i in range(self._nav.count()):
            it = self._nav.item(i)
            if it is not None and it.data(Qt.UserRole) == context_name:
                if self._nav.currentRow() != i:
                    self._nav.setCurrentRow(i)
                return

    def set_projects_count(self, value: int | None) -> None:
        # Workspace discovery can feed this (no project scans).
        self._projects_count = value
        self._sync_nav_badges()

    def set_project_index(self, project_index: ProjectIndex | None) -> None:
        """
        Populate hierarchy tree from in-memory data only.
        This does NOT scan the filesystem.
        """
        self._project_index = project_index
        self._tree.clear()
        self._sync_nav_badges()

        if project_index is None:
            # Neutral empty state: show nothing (no project selected yet).
            self._apply_tree_filter(self._tree_search.text())
            return

        root = QTreeWidgetItem([project_index.root.name])
        root.setExpanded(True)
        self._tree.addTopLevelItem(root)

        assets_root = QTreeWidgetItem([SidebarContext.ASSETS.value])
        shots_root = QTreeWidgetItem([SidebarContext.SHOTS.value])
        root.addChild(assets_root)
        root.addChild(shots_root)

        # Assets grouped by asset_type
        by_type: dict[str, list[Asset]] = {}
        for a in project_index.assets:
            by_type.setdefault(a.asset_type, []).append(a)
        for asset_type in sorted(by_type.keys()):
            type_node = QTreeWidgetItem([asset_type])
            assets_root.addChild(type_node)
            for a in sorted(by_type[asset_type], key=lambda x: x.name.lower()):
                a_node = QTreeWidgetItem([a.name])
                type_node.addChild(a_node)
                for d in sorted(a.departments, key=lambda x: x.name.lower()):
                    a_node.addChild(QTreeWidgetItem([d.name]))

        # Shots
        for s in sorted(project_index.shots, key=lambda x: x.name.lower()):
            s_node = QTreeWidgetItem([s.name])
            shots_root.addChild(s_node)
            for d in sorted(s.departments, key=lambda x: x.name.lower()):
                s_node.addChild(QTreeWidgetItem([d.name]))

        assets_root.setExpanded(True)
        shots_root.setExpanded(True)
        self._apply_tree_filter(self._tree_search.text())

    def _on_current_nav_item_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        context = current.data(Qt.UserRole)
        if not isinstance(context, str) or not context:
            return

        self._last_context_text = context
        self._sync_nav_active_states()
        self.context_changed.emit(context)

    def _on_nav_item_clicked(self, item: QListWidgetItem) -> None:
        # Click reloads current view (only when clicking already-selected item).
        context = item.data(Qt.UserRole)
        if not isinstance(context, str) or not context:
            return
        if context == self._last_context_text:
            self.context_clicked.emit(context)

    def _on_nav_context_menu_requested(self, pos) -> None:
        item = self._nav.itemAt(pos)
        if item is None:
            return
        context = item.data(Qt.UserRole)
        if not isinstance(context, str) or not context:
            return
        self.context_menu_requested.emit(context, self._nav.viewport().mapToGlobal(pos))

    def _sync_nav_active_states(self) -> None:
        current = self.current_context()
        for name, w in self._nav_widgets.items():
            w.set_active(name == current)

    def _sync_nav_badges(self) -> None:
        # Counts are UI-only, derived from already-loaded memory.
        assets_count = len(self._project_index.assets) if self._project_index is not None else None
        shots_count = len(self._project_index.shots) if self._project_index is not None else None

        for name, w in self._nav_widgets.items():
            if name == SidebarContext.ASSETS.value:
                w.set_count_badge(assets_count)
            elif name == SidebarContext.SHOTS.value:
                w.set_count_badge(shots_count)
            elif name == SidebarContext.PROJECTS.value:
                w.set_count_badge(self._projects_count)
            else:
                w.set_count_badge(None)

    def _apply_tree_filter(self, text: str) -> None:
        q = (text or "").strip().lower()

        def recurse(item: QTreeWidgetItem) -> bool:
            # Returns True if this item or any descendant matches.
            any_child = False
            for i in range(item.childCount()):
                child = item.child(i)
                if child is None:
                    continue
                child_match = recurse(child)
                child.setHidden(not child_match)
                any_child = any_child or child_match

            if not q:
                return True
            return (q in item.text(0).lower()) or any_child

        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top is None:
                continue
            recurse(top)

