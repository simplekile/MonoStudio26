from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, QSize, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QApplication,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.view_items import ViewItem
from monostudio.ui_qt.thumbnails import ThumbnailCache
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.lucide_icons import lucide_icon


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


class _GridCardDelegate(QStyledItemDelegate):
    """
    Grid card painter (Grid view):
    - 16:9 thumbnail
    - Status badge (top-right)
    - Name (Inter semibold)
    - Version + ID (JetBrains Mono)
    - Hover quick actions (icons only) without obscuring >30% of thumbnail
    """

    def __init__(self, *, view: QListView) -> None:
        super().__init__(view)
        self._view = view
        self._hovered_row: int | None = None
        self._card_size = QSize(320, 260)
        self._gap_px = 24

        # Theme cache (no per-paint parsing / allocations)
        self._c_card_bg = QColor(MONOS_COLORS["card_bg"])
        self._c_card_hover = QColor(MONOS_COLORS["card_hover"])
        self._c_border = QColor(MONOS_COLORS["border"])
        self._c_text_primary = QColor(MONOS_COLORS["text_primary"])
        self._c_text_meta = QColor(MONOS_COLORS["text_meta"])
        self._pen_border = QPen(self._c_border, 1)
        self._c_selected = QColor(MONOS_COLORS["blue_600"])
        self._pen_selected = QPen(self._c_selected, 2)

        # Font cache (no per-paint allocations)
        self._font_badge = QFont("Inter", 10)
        self._font_badge.setWeight(QFont.Weight.DemiBold)
        self._font_name = QFont("Inter", 13)
        self._font_name.setWeight(QFont.Weight.DemiBold)
        self._font_mono = QFont("JetBrains Mono", 11)

        st = view.style()
        self._icon_eye = lucide_icon("eye", size=16, color_hex=MONOS_COLORS["text_primary"])
        self._icon_download = lucide_icon("download", size=16, color_hex=MONOS_COLORS["text_primary"])
        self._icon_more = lucide_icon("ellipsis", size=16, color_hex=MONOS_COLORS["text_primary"])

    def set_hovered_index(self, index) -> None:
        row = index.row() if index and index.isValid() else None
        if self._hovered_row == row:
            return
        self._hovered_row = row
        self._view.viewport().update()

    def set_card_size(self, size: QSize) -> None:
        if size.isValid() and size != self._card_size:
            self._card_size = size
            self._view.viewport().update()

    def set_gap_px(self, gap_px: int) -> None:
        if gap_px > 0 and gap_px != self._gap_px:
            self._gap_px = gap_px
            self._view.viewport().update()

    @staticmethod
    def _rounded_rect(p: QPainter, r: QRect, radius: int, *, fill: QColor, pen: QPen | None = None) -> None:
        p.setPen(Qt.NoPen if pen is None else pen)
        p.setBrush(fill)
        p.drawRoundedRect(r, radius, radius)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            super().paint(painter, option, index)
            return

        # Paint inside the grid cell leaving explicit gap on right/bottom.
        g = max(0, int(self._gap_px))
        r = option.rect.adjusted(0, 0, -g, -g)
        if r.width() <= 0 or r.height() <= 0:
            return
        p = painter
        p.save()
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)

            # Card background
            bg = self._c_card_bg
            hover = bool(self._hovered_row == index.row())
            if hover:
                bg = self._c_card_hover

            selected = bool(option.state & QStyle.State_Selected)
            border_px = 2 if selected else 1
            border_pen = self._pen_selected if selected else self._pen_border

            outer = r
            # Fill first (no border), then draw content, then draw border ON TOP.
            self._rounded_rect(p, outer, 12, fill=bg, pen=None)

            # Clip content inside the border so thumbnails never paint over it.
            inner = outer.adjusted(border_px, border_px, -border_px, -border_px)
            inner_radius = max(0, 12 - border_px)
            clip = QPainterPath()
            clip.addRoundedRect(inner, inner_radius, inner_radius)
            p.setClipPath(clip)

            # 16:9 thumbnail region (fixed aspect)
            thumb_w = inner.width()
            thumb_h = max(1, int(thumb_w * 9 / 16))
            thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))

            # Draw thumbnail from icon (center-crop)
            icon = index.data(Qt.DecorationRole)
            if isinstance(icon, QIcon):
                src = icon.pixmap(256, 256)
                if not src.isNull():
                    scaled = src.scaled(
                        thumb.size(),
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation,
                    )
                    sx = max(0, (scaled.width() - thumb.width()) // 2)
                    sy = max(0, (scaled.height() - thumb.height()) // 2)
                    crop = scaled.copy(QRect(QPoint(sx, sy), thumb.size()))
                    p.drawPixmap(thumb, crop)

            # Status badge (placeholder, deterministic)
            badge_text = "—"
            p.setFont(self._font_badge)
            metrics = p.fontMetrics()
            pad_x = 6
            pad_y = 2
            bw = metrics.horizontalAdvance(badge_text) + pad_x * 2
            bh = metrics.height() + pad_y * 2
            badge = QRect(thumb.right() - bw - 12, thumb.top() + 12, bw, bh)
            self._rounded_rect(p, badge, 6, fill=QColor(0, 0, 0, 110), pen=None)
            p.setPen(self._c_text_primary)
            p.drawText(badge, Qt.AlignCenter, badge_text)

            # Hover quick actions (icons only)
            if hover:
                icons = [self._icon_eye, self._icon_download, self._icon_more]
                size = 16
                gap = 6
                total_w = size * len(icons) + gap * (len(icons) - 1)
                x0 = thumb.right() - total_w - 12
                y0 = thumb.bottom() - size - 12
                # No big overlay — keep under 30% of thumbnail
                for i, ic in enumerate(icons):
                    px = x0 + i * (size + gap)
                    rect = QRect(px, y0, size, size)
                    ic.paint(p, rect)

            # Stop clipping before text to avoid rounded-corner cropping issues
            p.setClipping(False)

            # Text blocks under thumbnail
            # Spec: p-4 (16px) padding for info block
            y = thumb.bottom() + 16
            x = inner.left() + 16
            w = inner.width() - 32

            p.setFont(self._font_name)
            p.setPen(self._c_text_primary)
            name_rect = QRect(x, y, w, 20)
            p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, item.name)

            p.setFont(self._font_mono)
            p.setPen(self._c_text_meta)
            meta = f"ID {item.name}   v —   assignee —"
            meta_rect = QRect(x, y + 24, w, 16)
            p.drawText(meta_rect, Qt.AlignLeft | Qt.AlignVCenter, meta)

            # Border on top (selected border = 2px)
            p.setPen(border_pen)
            p.setBrush(Qt.NoBrush)
            # Keep stroke safely inside cell.
            stroke_inset = 1
            border_rect = outer.adjusted(stroke_inset, stroke_inset, -stroke_inset, -stroke_inset)
            p.drawRoundedRect(border_rect, 12, 12)

        finally:
            p.restore()

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        # Responsive card size is controlled by MainView; keep uniform sizes for performance.
        return self._card_size


class MainView(QWidget):
    """
    Spec: Main View has Tile (default) and List mode; has Search + Filters.
    Phase 0: UI only (no filesystem model yet), so views start empty.
    """

    valid_selection_changed = Signal(bool)
    item_activated = Signal(object)  # emits ViewItem
    refresh_requested = Signal()
    root_context_menu_requested = Signal(object)  # emits global QPoint
    copy_inventory_requested = Signal(object)  # emits ViewItem (asset/shot only)
    delete_requested = Signal(object)  # emits ViewItem (asset/shot only)
    primary_action_requested = Signal()  # header primary action
    view_mode_changed = Signal(str)  # "tile" | "list"

    _SETTINGS_KEY_VIEW_MODE_PREFIX = "main_view/mode"
    _THUMBNAIL_SIZE_PX = 384  # backing cache size (square); painted as 16:9 in grid
    _THUMB_STATE_ROLE = Qt.UserRole + 1  # per-item state in tile model ("loaded"|"missing")
    _GRID_GAP_PX = 12

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._project_root: str | None = None
        self._empty_override: str | None = None
        self._thumb_cache = ThumbnailCache(size_px=self._THUMBNAIL_SIZE_PX)
        self._thumb_prefetch_scheduled = False

        self._view_mode: str = "tile"
        self._browser_context: str = "asset"  # "project" | "asset" | "shot"

        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(12)

        self._context_title = QLabel("Asset", header)
        self._context_title.setObjectName("MainViewContextTitle")
        f_title = QFont("Inter", 14)
        f_title.setWeight(QFont.Weight.DemiBold)
        self._context_title.setFont(f_title)

        # Center: View toggle (Grid | List)
        toggle = QWidget(header)
        toggle_layout = QHBoxLayout(toggle)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(6)

        self._btn_grid = QToolButton(toggle)
        self._btn_grid.setText("Grid")
        self._btn_grid.setCheckable(True)
        self._btn_grid.setAutoRaise(True)

        self._btn_list = QToolButton(toggle)
        self._btn_list.setText("List")
        self._btn_list.setCheckable(True)
        self._btn_list.setAutoRaise(True)

        self._view_toggle_group = QButtonGroup(self)
        self._view_toggle_group.setExclusive(True)
        self._view_toggle_group.addButton(self._btn_grid, 0)
        self._view_toggle_group.addButton(self._btn_list, 1)
        self._btn_grid.clicked.connect(lambda: self.set_view_mode("tile", save=True))
        self._btn_list.clicked.connect(lambda: self.set_view_mode("list", save=True))

        toggle_layout.addWidget(self._btn_grid)
        toggle_layout.addWidget(self._btn_list)

        # Right: ONE primary action button
        self._primary_action = QPushButton("+", header)
        self._primary_action.setObjectName("MainViewPrimaryAction")
        self._primary_action.setCursor(Qt.PointingHandCursor)
        self._primary_action.setMinimumHeight(32)
        self._primary_action.clicked.connect(self.primary_action_requested.emit)

        # Tile view (IconMode) skeleton
        self._tile_model = QStandardItemModel(self)
        self._tile_view = _ClearOnEmptyClickListView()
        self._tile_view.setObjectName("MainViewGrid")
        self._tile_view.setViewMode(QListView.IconMode)
        self._tile_view.setResizeMode(QListView.Adjust)
        self._tile_view.setUniformItemSizes(True)
        # Use explicit gap in grid sizing (prevents "stuck together" rendering).
        self._tile_view.setSpacing(0)
        self._tile_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tile_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tile_view.setIconSize(QSize(self._THUMBNAIL_SIZE_PX, self._THUMBNAIL_SIZE_PX))
        self._tile_view.setModel(self._tile_model)
        self._tile_view.doubleClicked.connect(self._on_tile_activated)
        self._tile_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tile_view.customContextMenuRequested.connect(self._on_tile_context_menu)
        self._tile_view.verticalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)
        self._tile_view.horizontalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)
        self._tile_view.setMouseTracking(True)
        self._tile_view.viewport().installEventFilter(self)
        # Left/top padding = 24px (right/bottom provided by per-cell gap).
        self._tile_view.setViewportMargins(24, 24, 0, 0)

        self._grid_delegate = _GridCardDelegate(view=self._tile_view)
        self._grid_delegate.set_gap_px(self._GRID_GAP_PX)
        self._tile_view.setItemDelegate(self._grid_delegate)
        self._tile_view.entered.connect(self._grid_delegate.set_hovered_index)
        self._tile_view.viewportEntered.connect(lambda: self._grid_delegate.set_hovered_index(None))
        self._sync_grid_layout()

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
        self._list_view.setObjectName("MainViewList")
        self._list_view.setModel(self._list_model)
        self._list_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list_view.horizontalHeader().setStretchLastSection(True)
        self._list_view.setSortingEnabled(False)
        self._list_view.verticalHeader().setVisible(False)
        self._list_view.verticalHeader().setDefaultSectionSize(28)
        self._list_view.setShowGrid(False)
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
        layout.setSpacing(0)
        layout.addWidget(header, 0)
        layout.addWidget(self._content, 1)

        header_layout.addWidget(self._context_title, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)
        header_layout.addWidget(toggle, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)
        header_layout.addWidget(self._primary_action, 0, Qt.AlignVCenter)

        self._tile_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)
        self._list_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)

        self._tile_model.rowsInserted.connect(self._update_empty_states)
        self._tile_model.rowsRemoved.connect(self._update_empty_states)
        self._tile_model.modelReset.connect(self._update_empty_states)
        self._list_model.rowsInserted.connect(self._update_empty_states)
        self._list_model.rowsRemoved.connect(self._update_empty_states)
        self._list_model.modelReset.connect(self._update_empty_states)

        # View mode becomes context-aware; MainWindow sets initial browser context.
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())

        self._all_items: list[ViewItem] = []

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self._tile_view.viewport() and event.type() == QEvent.Resize:
            self._sync_grid_layout()
        return super().eventFilter(watched, event)

    @staticmethod
    def _grid_columns_for_width(px: int) -> int:
        # Tailwind-like breakpoints:
        # <640: 1 col, <1024: 2 col, <1280: 3 col, <1536: 4 col, else: 5 col
        if px < 640:
            return 1
        if px < 1024:
            return 2
        if px < 1280:
            return 3
        if px < 1536:
            return 4
        return 5

    def _sync_grid_layout(self) -> None:
        """
        Responsive grid:
        - width adapts to viewport; column count by breakpoints
        - gap fixed at 24px
        - thumbnail fixed 16:9 inside card
        """
        try:
            vw = int(self._tile_view.viewport().width())
        except Exception:
            return
        if vw <= 0:
            return

        cols = self._grid_columns_for_width(vw)
        gap = self._GRID_GAP_PX

        # Available width inside the 24px left margin (right margin is 0 by design).
        inner_w = max(1, vw - 24)

        # Approximate: cols cards with (cols-1) gaps
        card_w = max(240, int((inner_w - (cols - 1) * gap) / cols))
        # Spec adjustment: shrink card size to 80% (keep columns + gap).
        card_w = max(200, int(card_w * 0.8))
        thumb_h = int(card_w * 9 / 16)
        meta_h = 16 + 20 + 4 + 16 + 16  # p-4 + name + gap + meta + bottom breathing
        card_h = thumb_h + meta_h

        # Grid cell includes explicit 24px gap on right/bottom.
        self._tile_view.setGridSize(QSize(card_w + gap, card_h + gap))
        self._grid_delegate.set_card_size(QSize(card_w, card_h))

    def set_context_title(self, title: str) -> None:
        self._context_title.setText(title)

    def set_primary_action(self, *, label: str, enabled: bool, tooltip: str | None) -> None:
        # Always visible; enabled and tooltip reflect current context requirements.
        self._primary_action.setText(f"+ {label}".strip())
        self._primary_action.setEnabled(bool(enabled))
        self._primary_action.setToolTip(tooltip or "")

    def set_browser_context(self, context: str) -> None:
        """
        Asset Browser contexts:
        - "project" -> default Grid
        - "asset"   -> default Grid
        - "shot"    -> default List
        Persist view mode per-context when user toggles it.
        """
        if context not in ("project", "asset", "shot"):
            return
        self._browser_context = context

        title = "Project" if context == "project" else ("Shot" if context == "shot" else "Asset")
        self.set_context_title(title)

        key = self._settings_key_view_mode()
        saved = self._settings.value(key, "", str)
        if saved in ("tile", "list"):
            self.set_view_mode(saved, save=False)
        else:
            default_mode = "list" if context == "shot" else "tile"
            self.set_view_mode(default_mode, save=False)

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
        self._list_model.setHorizontalHeaderLabels(self._list_headers())
        self._all_items = []
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def set_items(self, items: list[ViewItem]) -> None:
        # Explicit input only; no hidden filtering here (filter tree lives in Sidebar).
        self._all_items = items
        self._populate_views(items)

    def _populate_views(self, items: list[ViewItem]) -> None:
        # Populate both Tile and List representations from the same items.
        self._tile_view.clearSelection()
        self._list_view.clearSelection()

        self._tile_model.clear()

        self._list_model.clear()
        self._list_model.setHorizontalHeaderLabels(self._list_headers())

        mono = QFont("JetBrains Mono", 11)

        for idx, item in enumerate(items, start=1):
            # Tile: Name only; metadata painted via icon and secondary lines (delegate-friendly).
            tile_entry = QStandardItem(item.name)
            tile_entry.setEditable(False)
            tile_entry.setData(item, Qt.UserRole)
            tile_entry.setData(None, self._THUMB_STATE_ROLE)
            tile_entry.setIcon(self._icon_for_item(item))
            self._tile_model.appendRow(tile_entry)

            # List (high-density): Index, Name, Status, Version, Assignee, Last Updated
            c_index = QStandardItem(str(idx))
            c_name = QStandardItem(item.name)
            c_status = QStandardItem("—")
            c_version = QStandardItem("—")
            c_assignee = QStandardItem("—")
            c_updated = QStandardItem("—")

            c_version.setFont(mono)

            for cell in (c_index, c_name, c_status, c_version, c_assignee, c_updated):
                cell.setEditable(False)
                cell.setData(item, Qt.UserRole)

            self._list_model.appendRow([c_index, c_name, c_status, c_version, c_assignee, c_updated])

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def _settings_key_view_mode(self) -> str:
        return f"{self._SETTINGS_KEY_VIEW_MODE_PREFIX}/{self._browser_context}"

    @staticmethod
    def _list_headers() -> list[str]:
        return ["#", "Name", "Status", "Version", "Assignee", "Last Updated"]

    def _icon_for_item(self, item: ViewItem):
        # v1.1: Neutral placeholder tile for Assets/Shots when no thumbnail exists.
        if item.kind.value in ("asset", "shot"):
            return self._neutral_placeholder_icon(item.name)
        return lucide_icon("folder", size=20, color_hex=MONOS_COLORS["text_label"])

    def _neutral_placeholder_icon(self, name: str) -> QIcon:
        size = self._THUMBNAIL_SIZE_PX
        pix = QPixmap(size, size)
        pix.fill(QColor("#2B2D30"))

        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)
            # Flat, minimal, low-contrast placeholder.
            p.fillRect(0, 0, size, size, QColor("#26282B"))
            p.setPen(QPen(QColor("#3A3D41"), 1))
            p.drawRect(0, 0, size - 1, size - 1)

            # Optional: first letter of item name (subtle).
            letter = (name.strip()[:1] or "").upper()
            if letter:
                p.setPen(QColor("#A9ABB0"))
                font = QFont()
                font.setPointSize(20)
                font.setBold(True)
                p.setFont(font)
                p.drawText(pix.rect(), Qt.AlignCenter, letter)
        finally:
            p.end()

        return QIcon(pix)

    def set_view_mode(self, mode: str, *, save: bool = True) -> None:
        # Persistent per-context (stored in QSettings).
        if mode not in ("tile", "list"):
            return
        self._view_mode = mode
        self._content.setCurrentIndex(1 if mode == "list" else 0)
        if save:
            self._settings.setValue(self._settings_key_view_mode(), mode)

        # Sync toggle UI
        self._btn_grid.setChecked(mode == "tile")
        self._btn_list.setChecked(mode == "list")
        self.view_mode_changed.emit(mode)

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def has_valid_selection(self) -> bool:
        if self._view_mode == "list":
            sm = self._list_view.selectionModel()
            return bool(sm and sm.hasSelection())
        sm = self._tile_view.selectionModel()
        return bool(sm and sm.hasSelection())

    def selected_view_item(self) -> ViewItem | None:
        if self._view_mode == "list":
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
        if self._empty_override:
            empty_text = self._empty_override
        elif self._project_root:
            empty_text = "Empty assets / shots"
        else:
            empty_text = "Select a project root to begin"

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
        menu = self._build_item_context_menu(item)
        if menu is None:
            return
        chosen = menu.exec(self._tile_view.viewport().mapToGlobal(pos))
        self._dispatch_item_context_action(chosen, item)

    def _on_list_context_menu(self, pos) -> None:
        index = self._list_view.indexAt(pos)
        if not index.isValid():
            self.root_context_menu_requested.emit(self._list_view.viewport().mapToGlobal(pos))
            return
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return
        menu = self._build_item_context_menu(item)
        if menu is None:
            return
        chosen = menu.exec(self._list_view.viewport().mapToGlobal(pos))
        self._dispatch_item_context_action(chosen, item)

    def _build_item_context_menu(self, item: ViewItem) -> QMenu | None:
        # Candidate 1 + 2 helpers (explicit, silent).
        # Asset / Shot / Department only.
        if item.kind.value not in ("asset", "shot", "department"):
            return None

        menu = QMenu(self)

        copy_inventory = None
        if item.kind.value in ("asset", "shot"):
            copy_inventory = menu.addAction("Copy Inventory")
            menu.addSeparator()

        copy_full_path = menu.addAction("Copy Full Path")
        open_folder = menu.addAction("Open Folder")

        menu.addSeparator()

        delete_action = None
        refresh_action = None
        open_work = None
        open_publish = None

        if item.kind.value in ("asset", "shot"):
            # Existing v1 behavior: Refresh on Asset/Shot items.
            refresh_action = menu.addAction("Refresh")
            delete_action = menu.addAction("Delete…")
        elif item.kind.value == "department":
            # Optional (already meaningful in UI): open work/publish folders
            open_work = menu.addAction("Open Work Folder")
            open_publish = menu.addAction("Open Publish Folder")

        # Store action ids on the menu for dispatch without global state
        menu.setProperty("_act_copy_full_path", copy_full_path)
        menu.setProperty("_act_open_folder", open_folder)
        menu.setProperty("_act_copy_inventory", copy_inventory)
        menu.setProperty("_act_refresh", refresh_action)
        menu.setProperty("_act_delete", delete_action)
        menu.setProperty("_act_open_work", open_work)
        menu.setProperty("_act_open_publish", open_publish)
        return menu

    def _dispatch_item_context_action(self, chosen, item: ViewItem) -> None:
        if chosen is None:
            return

        # Compare by label text; labels are fixed by spec.
        text = getattr(chosen, "text", lambda: "")()

        if text == "Copy Inventory":
            # v1.2 extension: delegate generation to MainWindow (in-memory index)
            self.copy_inventory_requested.emit(item)
            return
        if text == "Copy Full Path":
            self._copy_full_path(str(item.path))
            return
        if text == "Open Folder":
            self._open_folder(Path(item.path))
            return
        if text == "Refresh":
            self.refresh_requested.emit()
            return
        if text == "Delete…":
            self.delete_requested.emit(item)
            return
        if text == "Open Work Folder":
            if hasattr(item, "ref") and item.ref is not None and hasattr(item.ref, "work_path"):
                self._open_folder(Path(item.ref.work_path))
            return
        if text == "Open Publish Folder":
            if hasattr(item, "ref") and item.ref is not None and hasattr(item.ref, "publish_path"):
                self._open_folder(Path(item.ref.publish_path))
            return

    def _copy_full_path(self, path_text: str) -> None:
        if not path_text:
            return
        cb = QApplication.clipboard()
        if cb is None:
            return
        cb.setText(path_text)

    def _open_folder(self, folder: Path) -> None:
        # Silent no-op if missing/invalid.
        try:
            if not folder.exists():
                return
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _schedule_thumbnail_prefetch(self) -> None:
        # Lazy loading: only attempt thumbnails for visible tile items.
        if self._thumb_prefetch_scheduled:
            return
        self._thumb_prefetch_scheduled = True
        QTimer.singleShot(0, self._prefetch_visible_thumbnails)

    def _prefetch_visible_thumbnails(self) -> None:
        self._thumb_prefetch_scheduled = False

        # Tile-only integration
        if self._view_mode != "tile":
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

