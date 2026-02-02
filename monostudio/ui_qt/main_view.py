from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, QSize, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
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
from monostudio.ui_qt.style import MONOS_COLORS, THUMB_TAG_STYLE
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.core.workspace_reader import ProjectQuickStats
from monostudio.core.models import Asset, Shot


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
    - Status badge (top-left)
    - Name (Inter semibold)
    - Version + ID (JetBrains Mono)
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
        self._c_text_primary_highlight = QColor(MONOS_COLORS["text_primary_highlight"])
        self._c_text_primary_selected = QColor(MONOS_COLORS["text_primary_selected"])
        self._c_text_meta = QColor(MONOS_COLORS["text_meta"])
        self._pen_border = QPen(self._c_border, 1)
        self._c_selected = QColor(MONOS_COLORS["blue_600"])
        self._pen_selected = QPen(self._c_selected, 2)

        # Font cache (no per-paint allocations)
        # Shared thumb tag style (status + filter tags): same geometry, only color differs.
        self._font_thumb_tag = QFont("Inter", int(THUMB_TAG_STYLE["font_size"]))
        self._font_thumb_tag.setWeight(QFont.Weight(int(THUMB_TAG_STYLE["font_weight"])))
        self._font_name = QFont("Inter", 13)
        self._font_name.setWeight(QFont.Weight.Medium)
        self._font_mono = QFont("JetBrains Mono", 8)
        # Shared meta style (mono) for ALL cards.
        self._font_meta_mono = QFont(self._font_mono)
        self._font_meta = QFont("Inter", 11)

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

            def status_key() -> str:
                # Lowercase key for styling parity with QSS rule.
                if item.kind.value == "project":
                    stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
                    return (stats.status if stats else "WAITING").lower()
                if isinstance(item.ref, Asset):
                    if any(d.publish_version_count > 0 for d in item.ref.departments):
                        return "ready"
                    if any(d.work_exists for d in item.ref.departments):
                        return "progress"
                    return "waiting"
                if isinstance(item.ref, Shot):
                    if any(d.publish_version_count > 0 for d in item.ref.departments):
                        return "ready"
                    if any(d.work_exists for d in item.ref.departments):
                        return "progress"
                    return "waiting"
                return "waiting"

            def status_style(k: str) -> tuple[QColor, QColor, QColor]:
                # (text, bg, border) with higher alpha for readability over thumbnails.
                a_bg = int(THUMB_TAG_STYLE["bg_alpha"])
                a_border = int(THUMB_TAG_STYLE["border_alpha"])
                def with_alpha(base: QColor, a: int) -> QColor:
                    c2 = QColor(base)
                    c2.setAlpha(int(a))
                    return c2
                if k == "ready":
                    c = QColor(MONOS_COLORS["emerald_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "progress":
                    # Match Inspector: PROGRESS uses amber, not blue.
                    c = QColor(MONOS_COLORS["amber_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "blocked":
                    c = QColor(MONOS_COLORS["red_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                c = QColor(MONOS_COLORS["waiting"])
                return (c, with_alpha(c, a_bg), with_alpha(c, a_border))

            # Unified thumb tag renderer (status + contextual filter tags).
            p.setFont(self._font_thumb_tag)
            metrics = p.fontMetrics()
            pad_x = int(THUMB_TAG_STYLE["pad_x"])
            pad_y = int(THUMB_TAG_STYLE["pad_y"])
            radius = int(THUMB_TAG_STYLE["radius"])
            border_px = int(THUMB_TAG_STYLE["border_px"])

            def draw_thumb_tag(*, x: int, y: int, text: str, text_color: QColor, bg_color: QColor, border_color: QColor) -> QRect:
                t = (text or "").strip().upper()
                w2 = metrics.horizontalAdvance(t) + pad_x * 2
                h2 = metrics.height() + pad_y * 2
                r2 = QRect(x, y, w2, h2)
                self._rounded_rect(p, r2, radius, fill=bg_color, pen=QPen(border_color, border_px))
                p.setPen(text_color)
                p.drawText(r2, Qt.AlignCenter, t)
                return r2

            # Status tag (top-left) for projects/assets/shots
            k = status_key()
            txt = k.upper()
            text_c, bg_c, border_c = status_style(k)
            badge = draw_thumb_tag(
                x=thumb.left() + 12,
                y=thumb.top() + 12,
                text=txt,
                text_color=text_c,
                bg_color=bg_c,
                border_color=border_c,
            )

            # Stop clipping before text to avoid rounded-corner cropping issues
            p.setClipping(False)

            # Text blocks under thumbnail
            # Spec: p-4 (16px) padding for info block
            y = thumb.bottom() + 16
            x = inner.left() + 16
            w = inner.width() - 32

            p.setFont(self._font_name)
            # Highlight state: hover uses highlight (blue), selected uses selected (fafafa).
            if selected:
                p.setPen(self._c_text_primary_selected)
            elif hover:
                p.setPen(self._c_text_primary_highlight)
            else:
                p.setPen(self._c_text_primary)
            name_rect = QRect(x, y, w, 20)
            p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, item.name)

            if item.kind.value == "project":
                stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
                shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
                assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
                # Match shot/asset meta style (mono, label blocks, spaced).
                meta = f"SHOTS {shots}   ASSETS {assets}"
                p.setFont(self._font_meta_mono)
                p.setPen(self._c_text_meta)
                meta_rect = QRect(x, y + 24, w, 16)
                p.drawText(meta_rect, Qt.AlignLeft | Qt.AlignVCenter, meta)
            else:
                p.setFont(self._font_meta_mono)
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
    _SETTINGS_KEY_CARD_SIZE_PREFIX = "main_view/card_size"
    _THUMBNAIL_SIZE_PX = 384  # backing cache size (square); painted as 16:9 in grid
    _THUMB_STATE_ROLE = Qt.UserRole + 1  # per-item state in tile model ("loaded"|"missing")
    _GRID_GAP_PX = 12
    _CARD_SIZE_PRESETS: dict[str, float] = {"small": 0.4, "medium": 0.6, "large": 1.00}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._project_root: str | None = None
        self._empty_override: str | None = None
        self._thumb_cache = ThumbnailCache(size_px=self._THUMBNAIL_SIZE_PX)
        self._thumb_prefetch_scheduled = False

        self._view_mode: str = "tile"
        self._browser_context: str = "asset"  # "project" | "asset" | "shot"
        self._card_size_preset: str = self._load_card_size_preset()
        # Header context (read-only)
        self._base_title: str = ""
        self._active_department: str | None = None

        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        # Ensure QSS background is painted for this container.
        header.setAttribute(Qt.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(12)

        self._context_title = QLabel("Asset", header)
        self._context_title.setObjectName("MainViewContextTitle")
        self._context_title.setTextFormat(Qt.RichText)
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

        # Right: Card size (Small/Medium/Large) for grid mode
        self._card_size_menu = QMenu(self)
        self._card_size_menu.setObjectName("MainViewCardSizeMenu")
        self._card_size_action_group = QActionGroup(self)
        self._card_size_action_group.setExclusive(True)
        self._card_size_actions: dict[str, QAction] = {}

        def add_card_size_action(preset: str, label: str) -> None:
            act = QAction(label, self._card_size_menu)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, p=preset: self.set_card_size_preset(p, save=True))
            self._card_size_action_group.addAction(act)
            self._card_size_menu.addAction(act)
            self._card_size_actions[preset] = act

        add_card_size_action("small", "Small cards")
        add_card_size_action("medium", "Medium cards")
        add_card_size_action("large", "Large cards")

        self._btn_card_size = QToolButton(header)
        self._btn_card_size.setObjectName("MainViewCardSizeButton")
        self._btn_card_size.setAutoRaise(True)
        self._btn_card_size.setCursor(Qt.PointingHandCursor)
        self._btn_card_size.setIcon(lucide_icon("sliders-horizontal", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_card_size.setPopupMode(QToolButton.InstantPopup)
        self._btn_card_size.setMenu(self._card_size_menu)
        self._update_card_size_button()

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
        # Keep viewport width stable to avoid oscillation when scrollbar appears/disappears.
        self._tile_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._tile_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tile_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tile_view.setSelectionMode(QAbstractItemView.SingleSelection)
        # Lock cards: no drag/drop/reorder.
        self._tile_view.setDragEnabled(False)
        self._tile_view.setAcceptDrops(False)
        self._tile_view.setDropIndicatorShown(False)
        self._tile_view.setDragDropMode(QAbstractItemView.NoDragDrop)
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
        self._grid_sync_scheduled = False
        self._grid_last: tuple[int, int, int] | None = None  # (cols, card_w, card_h)
        self._schedule_grid_layout_sync()

        self._tile_placeholder = QLabel("")
        self._tile_placeholder.setAlignment(Qt.AlignCenter)
        self._tile_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Empty state background must match content surface (not app_bg).
        self._tile_placeholder.setStyleSheet("background: #121214; color: #A9ABB0;")
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
        self._list_view.setDragEnabled(False)
        self._list_view.setAcceptDrops(False)
        self._list_view.setDropIndicatorShown(False)
        self._list_view.setDragDropMode(QAbstractItemView.NoDragDrop)
        self._list_view.horizontalHeader().setStretchLastSection(True)
        self._list_view.setSortingEnabled(False)
        self._list_view.verticalHeader().setVisible(False)
        self._list_view.verticalHeader().setDefaultSectionSize(28)
        self._list_view.setShowGrid(False)
        self._list_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._list_view.doubleClicked.connect(self._on_list_activated)
        self._list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_view.customContextMenuRequested.connect(self._on_list_context_menu)

        self._list_placeholder = QLabel("")
        self._list_placeholder.setAlignment(Qt.AlignCenter)
        self._list_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Empty state background must match content surface (not app_bg).
        self._list_placeholder.setStyleSheet("background: #121214; color: #A9ABB0;")
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
        header_layout.addWidget(self._btn_card_size, 0, Qt.AlignVCenter)
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
            self._schedule_grid_layout_sync()
        return super().eventFilter(watched, event)

    def _schedule_grid_layout_sync(self) -> None:
        if getattr(self, "_grid_sync_scheduled", False):
            return
        self._grid_sync_scheduled = True
        QTimer.singleShot(0, self._sync_grid_layout)

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
        self._grid_sync_scheduled = False
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

        # Approximate: cols cards with (cols-1) gaps, then apply user card-size preset scale.
        base_w = max(240, int((inner_w - (cols - 1) * gap) / cols))
        card_w = max(200, int(base_w * self._card_scale()))
        thumb_h = int(card_w * 9 / 16)
        meta_h = 16 + 20 + 4 + 16 + 16  # p-4 + name + gap + meta + bottom breathing
        card_h = thumb_h + meta_h

        sig = (cols, card_w, card_h)
        if getattr(self, "_grid_last", None) == sig:
            return
        self._grid_last = sig

        # Grid cell includes explicit 24px gap on right/bottom.
        self._tile_view.setGridSize(QSize(card_w + gap, card_h + gap))
        self._grid_delegate.set_card_size(QSize(card_w, card_h))

    def set_context_title(self, title: str) -> None:
        self.update_title(base_title=title, department=self._active_department)

    def set_active_department(self, department: str | None) -> None:
        self._active_department = (department or "").strip() or None
        self.update_title(base_title=self._base_title or self._context_title.text(), department=self._active_department)

    def update_title(self, *, base_title: str, department: str | None) -> None:
        """
        Title formatting:
        - Base title always shown (uppercased)
        - If department active: append " · DEPARTMENT" in visually-secondary style
        """
        base = (base_title or "").strip()
        self._base_title = base
        base_up = base.upper() if base else ""
        dep = (department or "").strip()
        if not dep:
            self._context_title.setText(base_up)
            return
        dep_up = dep.upper()
        # Visually secondary: reuse text_label color (no badge/background).
        secondary = MONOS_COLORS.get("text_label", "#a1a1aa")
        self._context_title.setText(f'{base_up}<span style="color:{secondary};"> · {dep_up}</span>')

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
        self._card_size_preset = self._load_card_size_preset()
        self._update_card_size_button()

        title = "Project" if context == "project" else ("Shot" if context == "shot" else "Asset")
        self.set_context_title(title)

        key = self._settings_key_view_mode()
        saved = self._settings.value(key, "", str)
        if saved in ("tile", "list"):
            self.set_view_mode(saved, save=False)
        else:
            default_mode = "list" if context == "shot" else "tile"
            self.set_view_mode(default_mode, save=False)
        self._schedule_grid_layout_sync()

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
        self._apply_list_column_defaults()
        self._all_items = []
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def set_items(self, items: list[ViewItem]) -> None:
        # Explicit input only; no hidden filtering here (filter tree lives in Sidebar).
        self._all_items = items
        self._populate_views(items)

    def invalidate_thumbnail(self, item_root: Path) -> None:
        """
        Force a thumbnail refresh for a specific item.
        Used by explicit overrides (e.g. clipboard paste) to ensure the grid updates immediately.
        """
        root = Path(item_root)

        # Clear cache entries (both user override + auto candidates).
        for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
            self._thumb_cache.invalidate_file(root / name)

        # Tile model stores "loaded"/"missing" states; reset them so prefetch re-loads.
        try:
            rows = int(self._tile_model.rowCount())
        except Exception:
            rows = 0
        for row in range(rows):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            if item.path != root:
                continue
            std_item = self._tile_model.itemFromIndex(idx)
            if std_item is None:
                continue
            std_item.setData(None, self._THUMB_STATE_ROLE)
            std_item.setIcon(self._icon_for_item(item))

        self._schedule_thumbnail_prefetch()

    def _populate_views(self, items: list[ViewItem]) -> None:
        # Populate both Tile and List representations from the same items.
        self._tile_view.clearSelection()
        self._list_view.clearSelection()

        self._tile_model.clear()

        self._list_model.clear()
        self._list_model.setHorizontalHeaderLabels(self._list_headers())
        self._apply_list_column_defaults()

        mono = QFont("JetBrains Mono", 11)

        for idx, item in enumerate(items, start=1):
            # Tile: Name only; metadata painted via icon and secondary lines (delegate-friendly).
            tile_entry = QStandardItem(item.name)
            tile_entry.setEditable(False)
            tile_entry.setData(item, Qt.UserRole)
            tile_entry.setData(None, self._THUMB_STATE_ROLE)
            tile_entry.setIcon(self._icon_for_item(item))
            self._tile_model.appendRow(tile_entry)

            if self._browser_context == "project":
                stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
                status = "WAITING" if not stats else stats.status
                shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
                assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
                updated = "—" if not stats or not stats.last_modified else stats.last_modified

                c_index = QStandardItem(str(idx))
                c_name = QStandardItem(item.name)
                c_status = QStandardItem(status)
                c_shots = QStandardItem(shots)
                c_assets = QStandardItem(assets)
                c_updated = QStandardItem(updated)
                c_path = QStandardItem(str(item.path))
                c_path.setFont(mono)

                for cell in (c_index, c_name, c_status, c_shots, c_assets, c_updated, c_path):
                    cell.setEditable(False)
                    cell.setData(item, Qt.UserRole)

                self._list_model.appendRow([c_index, c_name, c_status, c_shots, c_assets, c_updated, c_path])
            else:
                # List (high-density): Index, Name, Status, Version, Assignee, Last Updated
                c_index = QStandardItem(str(idx))
                c_name = QStandardItem(item.name)
                status = "WAITING"
                if isinstance(item.ref, (Asset, Shot)):
                    if any(d.publish_version_count > 0 for d in item.ref.departments):
                        status = "READY"
                    elif any(d.work_exists for d in item.ref.departments):
                        status = "PROGRESS"
                c_status = QStandardItem(status)
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

    def _settings_key_card_size(self) -> str:
        return f"{self._SETTINGS_KEY_CARD_SIZE_PREFIX}/{self._browser_context}"

    def _load_card_size_preset(self) -> str:
        raw = (self._settings.value(self._settings_key_card_size(), "medium", str) or "").strip().lower()
        return raw if raw in self._CARD_SIZE_PRESETS else "medium"

    def _card_scale(self) -> float:
        return float(self._CARD_SIZE_PRESETS.get(self._card_size_preset, 0.80))

    def _update_card_size_button(self) -> None:
        # Keep menu check-state + tooltip in sync.
        for k, act in getattr(self, "_card_size_actions", {}).items():
            act.setChecked(bool(k == self._card_size_preset))
        label = self._card_size_preset.capitalize()
        if hasattr(self, "_btn_card_size"):
            self._btn_card_size.setToolTip(f"Card size: {label}")
            self._btn_card_size.setEnabled(self._view_mode == "tile")

    def set_card_size_preset(self, preset: str, *, save: bool = True) -> None:
        preset = (preset or "").strip().lower()
        if preset not in self._CARD_SIZE_PRESETS:
            return
        if self._card_size_preset == preset:
            return
        self._card_size_preset = preset
        if save:
            self._settings.setValue(self._settings_key_card_size(), preset)
        self._update_card_size_button()
        self._schedule_grid_layout_sync()

    def _list_headers(self) -> list[str]:
        if self._browser_context == "project":
            return ["#", "Name", "Status", "Shots", "Assets", "Last Updated", "Path"]
        return ["#", "Name", "Status", "Version", "Assignee", "Last Updated"]

    def _apply_list_column_defaults(self) -> None:
        # Keep layout stable: path is hidden by default in dense views.
        if self._browser_context == "project":
            self._list_view.setColumnHidden(6, True)
        else:
            # Existing behavior: no hidden columns in the new dense list.
            pass

    def _icon_for_item(self, item: ViewItem):
        # v1.1: Neutral placeholder tile for Assets/Shots when no thumbnail exists.
        if item.kind.value in ("asset", "shot", "project"):
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
        self._update_card_size_button()

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

    def clear_selection(self) -> None:
        # Inspector "close" action: clear selection only (no rescan, no data mutation).
        try:
            self._tile_view.clearSelection()
        except Exception:
            pass
        try:
            self._list_view.clearSelection()
        except Exception:
            pass
        self.valid_selection_changed.emit(self.has_valid_selection())

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
            if item.kind.value not in ("asset", "shot", "project"):
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

