from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QSizePolicy,
    QStyle,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.view_items import ViewItem
from monostudio.ui_qt.thumbnails import ThumbnailCache


class _ClearOnEmptyClickListView(QListView):
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if not self.indexAt(event.pos()).isValid():
            self.clearSelection()
        super().mousePressEvent(event)


class _ClearOnEmptyClickTableView(QTableView):
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if not self.indexAt(event.pos()).isValid():
            self.clearSelection()
        super().mousePressEvent(event)


class MainView(QWidget):
    """
    Spec: Main View has Tile (default) and List mode; has Search + Filters.
    Phase 0: UI only (no filesystem model yet), so views start empty.
    """

    valid_selection_changed = Signal(bool)
    item_activated = Signal(object)  # emits ViewItem
    refresh_requested = Signal()
    root_context_menu_requested = Signal(object)  # emits global QPoint

    _SETTINGS_KEY_VIEW_MODE = "main_view/mode"  # "tile" | "list"
    _THUMBNAIL_SIZE_PX = 96
    _THUMB_STATE_ROLE = Qt.UserRole + 1  # per-item state in tile model ("loaded"|"missing")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._project_root: str | None = None
        self._empty_override: str | None = None
        self._thumb_cache = ThumbnailCache(size_px=self._THUMBNAIL_SIZE_PX)
        self._thumb_prefetch_scheduled = False

        self._mode = QComboBox()
        self._mode.addItem("Tile", userData="tile")
        self._mode.addItem("List", userData="list")
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search")
        self._search.setClearButtonEnabled(True)

        self._filter_type = QComboBox()
        self._filter_type.addItem("Type", userData=None)
        self._filter_type.addItem("char", userData="char")
        self._filter_type.addItem("prop", userData="prop")
        self._filter_type.addItem("env", userData="env")
        self._filter_type.addItem("shot", userData="shot")

        self._filter_department = QComboBox()
        self._filter_department.addItem("Department", userData=None)
        self._filter_department.addItem("anim", userData="anim")
        self._filter_department.addItem("model", userData="model")
        self._filter_department.addItem("comp", userData="comp")

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 0)
        header_layout.setSpacing(10)
        header_layout.addWidget(self._search, 1)
        header_layout.addWidget(self._filter_type, 0)
        header_layout.addWidget(self._filter_department, 0)
        header_layout.addStretch(1)
        header_layout.addWidget(self._mode, 0)

        # Tile view (IconMode) skeleton
        self._tile_model = QStandardItemModel(self)
        self._tile_view = _ClearOnEmptyClickListView()
        self._tile_view.setViewMode(QListView.IconMode)
        self._tile_view.setResizeMode(QListView.Adjust)
        self._tile_view.setUniformItemSizes(True)
        self._tile_view.setSpacing(12)
        self._tile_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tile_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tile_view.setIconSize(QSize(self._THUMBNAIL_SIZE_PX, self._THUMBNAIL_SIZE_PX))
        self._tile_view.setModel(self._tile_model)
        self._tile_view.doubleClicked.connect(self._on_tile_activated)
        self._tile_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tile_view.customContextMenuRequested.connect(self._on_tile_context_menu)
        self._tile_view.verticalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)
        self._tile_view.horizontalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)

        self._tile_placeholder = QLabel("")
        self._tile_placeholder.setAlignment(Qt.AlignCenter)
        self._tile_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._tile_placeholder.setStyleSheet("color: #A9ABB0;")
        self._tile_placeholder.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tile_placeholder.customContextMenuRequested.connect(
            lambda p: self.root_context_menu_requested.emit(self._tile_placeholder.mapToGlobal(p))
        )

        tile_page = QStackedWidget()
        tile_page.addWidget(self._tile_placeholder)
        tile_page.addWidget(self._tile_view)
        tile_page.setCurrentIndex(0)
        self._tile_page = tile_page

        # List view skeleton
        self._list_model = QStandardItemModel(self)
        self._list_model.setHorizontalHeaderLabels(
            [
                "Name",
                "Type",
                "Departments count",
                "Path",
            ]
        )

        self._list_view = _ClearOnEmptyClickTableView()
        self._list_view.setModel(self._list_model)
        self._list_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list_view.horizontalHeader().setStretchLastSection(True)
        self._list_view.setSortingEnabled(False)
        # Spec: Path column hidden by default
        self._list_view.setColumnHidden(3, True)
        self._list_view.doubleClicked.connect(self._on_list_activated)
        self._list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_view.customContextMenuRequested.connect(self._on_list_context_menu)

        self._list_placeholder = QLabel("")
        self._list_placeholder.setAlignment(Qt.AlignCenter)
        self._list_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._list_placeholder.setStyleSheet("color: #A9ABB0;")
        self._list_placeholder.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_placeholder.customContextMenuRequested.connect(
            lambda p: self.root_context_menu_requested.emit(self._list_placeholder.mapToGlobal(p))
        )

        list_page = QStackedWidget()
        list_page.addWidget(self._list_placeholder)
        list_page.addWidget(self._list_view)
        list_page.setCurrentIndex(0)
        self._list_page = list_page

        self._content = QStackedWidget()
        self._content.addWidget(tile_page)  # index 0 = tile
        self._content.addWidget(list_page)  # index 1 = list

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(header, 0)
        layout.addWidget(self._content, 1)

        self._tile_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)
        self._list_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)

        self._tile_model.rowsInserted.connect(self._update_empty_states)
        self._tile_model.rowsRemoved.connect(self._update_empty_states)
        self._tile_model.modelReset.connect(self._update_empty_states)
        self._list_model.rowsInserted.connect(self._update_empty_states)
        self._list_model.rowsRemoved.connect(self._update_empty_states)
        self._list_model.modelReset.connect(self._update_empty_states)

        self._restore_view_mode()
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())

    def set_context_title(self, title: str) -> None:
        # Spec does not define a visible title bar; this is a no-op for Phase 0.
        _ = title

    def set_project_root(self, path: str | None) -> None:
        # Store only; no validation, no scanning (per requirements).
        self._project_root = path or None
        self._update_empty_states()

    def set_empty_override(self, message: str | None) -> None:
        # Allows higher-level flows (e.g. workspace discovery) to present a neutral empty state.
        self._empty_override = message
        self._update_empty_states()

    def clear(self) -> None:
        # Clear Main View (no filesystem scan in this phase).
        self._tile_view.clearSelection()
        self._list_view.clearSelection()
        self._tile_model.clear()
        self._list_model.clear()
        self._list_model.setHorizontalHeaderLabels(
            [
                "Name",
                "Type",
                "Departments count",
                "Path",
            ]
        )
        self._list_view.setColumnHidden(3, True)  # Path hidden by default (spec)
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())

    def set_items(self, items: list[ViewItem]) -> None:
        # Populate both Tile and List representations from the same items.
        self._tile_view.clearSelection()
        self._list_view.clearSelection()

        self._tile_model.clear()

        self._list_model.clear()
        self._list_model.setHorizontalHeaderLabels(
            [
                "Name",
                "Type",
                "Departments count",
                "Path",
            ]
        )
        self._list_view.setColumnHidden(3, True)  # Path hidden by default (spec)

        for item in items:
            # Tile: Name + Type badge as 2-line label, placeholder icon by kind/type.
            tile_entry = QStandardItem(f"{item.name}\n{item.type_badge}")
            tile_entry.setEditable(False)
            tile_entry.setData(item, Qt.UserRole)
            tile_entry.setData(None, self._THUMB_STATE_ROLE)
            tile_entry.setIcon(self._icon_for_item(item))
            self._tile_model.appendRow(tile_entry)

            # List: Name, Type, Departments count, Path (hidden by default)
            name_cell = QStandardItem(item.name)
            type_cell = QStandardItem(item.type_badge)
            dept_cell = QStandardItem("" if item.departments_count is None else str(item.departments_count))
            path_cell = QStandardItem(str(item.path))

            for cell in (name_cell, type_cell, dept_cell, path_cell):
                cell.setEditable(False)
                cell.setData(item, Qt.UserRole)

            self._list_model.appendRow([name_cell, type_cell, dept_cell, path_cell])

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def _icon_for_item(self, item: ViewItem):
        # Spec: placeholder icon by type. Use standard icons (no custom art in v1).
        st = self.style()
        if item.kind.value in ("asset", "shot"):
            return st.standardIcon(QStyle.SP_DirIcon)
        return st.standardIcon(QStyle.SP_DirOpenIcon)

    def _restore_view_mode(self) -> None:
        mode = self._settings.value(self._SETTINGS_KEY_VIEW_MODE, "tile")
        if mode == "list":
            self._mode.setCurrentIndex(1)
        else:
            self._mode.setCurrentIndex(0)

    def _on_mode_changed(self) -> None:
        mode = self._mode.currentData()
        if mode == "list":
            self._content.setCurrentIndex(1)
            self._settings.setValue(self._SETTINGS_KEY_VIEW_MODE, "list")
        else:
            self._content.setCurrentIndex(0)
            self._settings.setValue(self._SETTINGS_KEY_VIEW_MODE, "tile")

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def has_valid_selection(self) -> bool:
        mode = self._mode.currentData()
        if mode == "list":
            sm = self._list_view.selectionModel()
            return bool(sm and sm.hasSelection())
        sm = self._tile_view.selectionModel()
        return bool(sm and sm.hasSelection())

    def selected_view_item(self) -> ViewItem | None:
        mode = self._mode.currentData()
        if mode == "list":
            sm = self._list_view.selectionModel()
            if sm is None:
                return None
            rows = sm.selectedRows()
            if not rows:
                return None
            item = rows[0].data(Qt.UserRole)
            return item if isinstance(item, ViewItem) else None

        sm = self._tile_view.selectionModel()
        if sm is None:
            return None
        indexes = sm.selectedIndexes()
        if not indexes:
            return None
        item = indexes[0].data(Qt.UserRole)
        return item if isinstance(item, ViewItem) else None

    def _on_any_selection_changed(self, *_args) -> None:
        self.valid_selection_changed.emit(self.has_valid_selection())

    def _update_empty_states(self) -> None:
        # Spec: empty states use placeholders; no popup.
        if self._project_root:
            empty_text = "Empty assets / shots"
        else:
            empty_text = self._empty_override or "Select a project root to begin"

        self._tile_placeholder.setText(empty_text)
        self._list_placeholder.setText(empty_text)

        tile_has_rows = self._tile_model.rowCount() > 0
        list_has_rows = self._list_model.rowCount() > 0
        self._tile_page.setCurrentIndex(1 if tile_has_rows else 0)
        self._list_page.setCurrentIndex(1 if list_has_rows else 0)

    def _on_tile_activated(self, index) -> None:
        item = index.data(Qt.UserRole)
        if isinstance(item, ViewItem):
            self.item_activated.emit(item)

    def _on_list_activated(self, index) -> None:
        item = index.data(Qt.UserRole)
        if isinstance(item, ViewItem):
            self.item_activated.emit(item)

    def _on_tile_context_menu(self, pos) -> None:
        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            self.root_context_menu_requested.emit(self._tile_view.viewport().mapToGlobal(pos))
            return
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return
        if item.kind.value not in ("asset", "shot"):
            return

        menu = QMenu(self)
        refresh = menu.addAction("Refresh")
        chosen = menu.exec(self._tile_view.viewport().mapToGlobal(pos))
        if chosen == refresh:
            self.refresh_requested.emit()

    def _on_list_context_menu(self, pos) -> None:
        index = self._list_view.indexAt(pos)
        if not index.isValid():
            self.root_context_menu_requested.emit(self._list_view.viewport().mapToGlobal(pos))
            return
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return
        if item.kind.value not in ("asset", "shot"):
            return

        menu = QMenu(self)
        refresh = menu.addAction("Refresh")
        chosen = menu.exec(self._list_view.viewport().mapToGlobal(pos))
        if chosen == refresh:
            self.refresh_requested.emit()

    def _schedule_thumbnail_prefetch(self) -> None:
        # Lazy loading: only attempt thumbnails for visible tile items.
        if self._thumb_prefetch_scheduled:
            return
        self._thumb_prefetch_scheduled = True
        QTimer.singleShot(0, self._prefetch_visible_thumbnails)

    def _prefetch_visible_thumbnails(self) -> None:
        self._thumb_prefetch_scheduled = False

        # Tile-only integration
        if self._mode.currentData() != "tile":
            return
        if self._tile_model.rowCount() == 0:
            return
        if self._tile_page.currentIndex() != 1:
            return

        viewport = self._tile_view.viewport()
        vp_rect = viewport.rect()

        for row in range(self._tile_model.rowCount()):
            index = self._tile_model.index(row, 0)
            if not index.isValid():
                continue
            if not self._tile_view.visualRect(index).intersects(vp_rect):
                continue

            item = index.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            if item.kind.value not in ("asset", "shot"):
                continue

            std_item = self._tile_model.itemFromIndex(index)
            if std_item is None:
                continue

            state = std_item.data(self._THUMB_STATE_ROLE)
            if state in ("loaded", "missing"):
                continue

            thumb_file = self._thumb_cache.resolve_thumbnail_file(item.path)
            if thumb_file is None:
                std_item.setData("missing", self._THUMB_STATE_ROLE)
                continue

            pix = self._thumb_cache.load_thumbnail_pixmap(thumb_file)
            if pix is None:
                std_item.setData("missing", self._THUMB_STATE_ROLE)
                continue

            std_item.setIcon(QIcon(pix))
            std_item.setData("loaded", self._THUMB_STATE_ROLE)

