"""
Shared Inbox/Outbox date-folder browser: Grid + List views, contextual empty states, content toolbar.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSettings, QSize, Qt, Signal, QRect, QPoint
from PySide6.QtGui import QFont, QPainter, QColor
from PySide6.QtWidgets import (
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.inbox_date_folder import date_folder_sort_key, parse_date_folder_name
from monostudio.ui_qt.inbox_page_toolbar import InboxContentToolbar
from monostudio.ui_qt.inbox_grid_card_paint import paint_grid_card_labels
from monostudio.ui_qt.inbox_list_row_paint import (
    EXPLORER_LIST_ROW_H,
    explorer_list_row_size_hint,
    explorer_path_stats,
    explorer_type_label,
    paint_explorer_list_row,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

_SETTINGS_ORG = "MonoStudio26"
_SETTINGS_APP = "MonoStudio26"

_CARD_W = 220
_CARD_H = 136
_GRID_GAP = 20
_LIST_ROW_H = EXPLORER_LIST_ROW_H


class _DateFolderEntry:
    __slots__ = ("label", "path", "meta", "parsed_date")

    def __init__(self, label: str, path: Path, *, meta: str = "", parsed_date: date | None = None) -> None:
        self.label = label
        self.path = path
        self.meta = meta
        self.parsed_date = parsed_date


def _folder_stats(path: Path) -> tuple[int, int]:
    """Return (file_count, folder_count) for direct children (non-hidden)."""
    files = folders = 0
    try:
        for child in path.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                folders += 1
            elif child.is_file():
                files += 1
    except OSError:
        pass
    return files, folders


def _format_folder_meta(path: Path, folder_name: str) -> tuple[str, date | None]:
    parsed = parse_date_folder_name(folder_name)
    files, folders = _folder_stats(path)
    parts: list[str] = []
    if parsed is not None:
        parts.append(parsed.strftime("%b %d, %Y"))
    item_bits: list[str] = []
    if files:
        item_bits.append(f"{files} file{'s' if files != 1 else ''}")
    if folders:
        item_bits.append(f"{folders} folder{'s' if folders != 1 else ''}")
    if item_bits:
        parts.append(" · ".join(item_bits))
    elif not parts:
        parts.append("Empty folder")
    return (" · ".join(parts) if parts else ""), parsed


def _source_label(source_filter: str) -> str:
    t = (source_filter or "").strip().lower()
    if t == "client":
        return "Client"
    if t == "freelancer":
        return "Freelancer"
    return ""


class _DateFolderListModel(QAbstractListModel):
    RoleMeta = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[_DateFolderEntry] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or index.row() >= len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            tip = f"{entry.label}\n{entry.meta}" if entry.meta else entry.label
            return entry.label if role == Qt.ItemDataRole.DisplayRole else tip
        if role == self.RoleMeta:
            return entry.meta
        if role == Qt.ItemDataRole.UserRole:
            return str(entry.path)
        return None

    def set_entries(self, entries: list[_DateFolderEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entry_at(self, row: int) -> _DateFolderEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None


class _DateFolderListDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        if not index.isValid():
            return
        view = option.widget
        vw = int(view.viewport().width()) if view is not None else option.rect.width()
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        path_str = index.data(Qt.ItemDataRole.UserRole)
        path = Path(path_str) if path_str else None
        date_label, _ = explorer_path_stats(path) if path is not None and path.exists() else ("", "")
        files, folders = _folder_stats(path) if path is not None else (0, 0)
        size_bits: list[str] = []
        if files:
            size_bits.append(f"{files} file{'s' if files != 1 else ''}")
        if folders:
            size_bits.append(f"{folders} folder{'s' if folders != 1 else ''}")
        size_label = ", ".join(size_bits)
        icon = lucide_icon("calendar", size=22, color_hex="#a1a1aa")

        p = painter
        p.save()
        try:
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            paint_explorer_list_row(
                p,
                option,
                viewport_width=vw,
                name=str(title),
                type_label=explorer_type_label(path) if path is not None else "File folder",
                date_label=date_label,
                size_label=size_label,
                icon=icon,
            )
        finally:
            p.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return explorer_list_row_size_hint(option)


class _DateFolderGridDelegate(QStyledItemDelegate):
    def __init__(self, *, view: QListView, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        self._card_size = QSize(_CARD_W, _CARD_H)
        self._c_bg = MONOS_COLORS.get("card_bg", "#191b1e")
        self._c_hover = MONOS_COLORS.get("card_hover", "#1d1f23")
        self._c_border = MONOS_COLORS.get("border", "#27272a")
        self._c_text = MONOS_COLORS.get("text_primary", "#fafafa")
        self._c_muted = MONOS_COLORS.get("text_label", "#a1a1aa")
        self._c_meta = MONOS_COLORS.get("text_meta", "#71717a")
        self._c_selected = "#3b82f6"
        self._icon = lucide_icon("calendar", size=32, color_hex=self._c_muted)

    def set_card_size(self, size: QSize) -> None:
        self._card_size = size

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(self._card_size.width() + _GRID_GAP, self._card_size.height() + _GRID_GAP)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        if not index.isValid():
            return
        label = index.data(Qt.ItemDataRole.DisplayRole) or ""
        meta = index.data(_DateFolderListModel.RoleMeta) or ""
        rect = option.rect.adjusted(_GRID_GAP // 2, _GRID_GAP // 2, -_GRID_GAP // 2, -_GRID_GAP // 2)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg = QColor(self._c_hover if hover else self._c_bg)
        border = QColor(self._c_selected if selected else self._c_border)
        title_color = QColor(self._c_text if selected or hover else self._c_muted)
        meta_color = QColor("#93c5fd" if selected else self._c_meta)

        p = painter
        p.save()
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(border)
            p.setBrush(bg)
            p.drawRoundedRect(rect, 10, 10)

            icon_rect = rect.adjusted(0, 16, 0, -48)
            if not self._icon.isNull():
                px = self._icon.pixmap(32, 32)
                ix = icon_rect.center().x() - px.width() // 2
                iy = icon_rect.top()
                p.drawPixmap(ix, iy, px)

            paint_grid_card_labels(
                p,
                rect,
                title=str(label),
                meta=str(meta),
                title_color=title_color,
                meta_color=meta_color,
                icon_band_h=44,
                title_px=12,
            )
        finally:
            p.restore()


class InboxOutboxDateFolderBrowser(QWidget):
    """Date folder list/grid for Inbox or Outbox. Emits date_folder_clicked(Path)."""

    date_folder_clicked = Signal(object)  # Path
    import_clicked = Signal()
    open_folder_requested = Signal(object)  # Path

    def __init__(
        self,
        *,
        page_title: str = "Inbox",
        empty_icon: str = "upload",
        settings_key: str = "inbox/view_mode",
        scan_fn: Callable[[Path], list] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._page_title = page_title
        self._empty_icon = empty_icon
        self._settings_key = settings_key
        self._scan_fn = scan_fn
        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self._project_root: Path | None = None
        self._source_filter: str = ""
        self._view_mode: str = "tile"
        self._entries: list[_DateFolderEntry] = []
        self._grid_sync_scheduled = False
        self._grid_last: tuple[int, int, int] | None = None

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        self._content_toolbar = InboxContentToolbar(self)
        self._content_toolbar.view_mode_changed.connect(self._on_view_mode_changed)
        root_lay.addWidget(self._content_toolbar, 0)

        self._outer_stack = QStackedWidget(self)
        self._empty_stack = QStackedWidget(self)
        self._empty_no_project = self._make_empty_page(
            "folder-kanban",
            "Open a project",
            "Choose a project to browse incoming deliveries.",
            show_import=False,
        )
        self._empty_no_source = self._make_empty_page(
            "user",
            "Choose a source",
            "Select Client or Freelancer in the sidebar to view date folders.",
            show_import=False,
        )
        self._empty_no_folders = self._make_empty_page(
            empty_icon,
            "No deliveries yet",
            "Import files from a client or freelancer, or drag and drop here.",
            show_import=True,
        )
        self._empty_stack.addWidget(self._empty_no_project)
        self._empty_stack.addWidget(self._empty_no_source)
        self._empty_stack.addWidget(self._empty_no_folders)
        self._outer_stack.addWidget(self._empty_stack)

        content = QWidget(self)
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        self._view_stack = QStackedWidget(content)
        self._list = QListWidget(content)
        self._list.setObjectName("InboxMappingList")
        self._list.setIconSize(QSize(18, 18))
        self._list.setSpacing(0)
        self._list.setItemDelegate(_DateFolderListDelegate(self._list))
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list.itemDoubleClicked.connect(self._on_list_item_double_clicked)
        self._view_stack.addWidget(self._list)

        self._grid = QListView(content)
        self._grid.setObjectName("MainViewGrid")
        self._grid.setViewMode(QListView.ViewMode.IconMode)
        self._grid.setResizeMode(QListView.ResizeMode.Adjust)
        self._grid.setMovement(QListView.Movement.Static)
        self._grid.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._grid.setUniformItemSizes(True)
        self._grid.setSpacing(0)
        self._grid.setMouseTracking(True)
        self._grid_model = _DateFolderListModel(self._grid)
        self._grid.setModel(self._grid_model)
        self._grid_delegate = _DateFolderGridDelegate(view=self._grid, parent=self._grid)
        self._grid.setItemDelegate(self._grid_delegate)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._on_grid_context_menu)
        self._grid.doubleClicked.connect(self._on_grid_double_clicked)
        self._grid.viewport().installEventFilter(self)
        self._view_stack.addWidget(self._grid)

        content_lay.addWidget(self._view_stack, 1)
        self._outer_stack.addWidget(content)
        root_lay.addWidget(self._outer_stack, 1)

        self._load_view_mode()
        self._sync_ui()

    def view_toggle(self) -> QWidget:
        return self._content_toolbar.view_toggle()

    def view_mode(self) -> str:
        return self._view_mode

    def reload_view_mode_from_settings(self) -> None:
        self._load_view_mode()

    def _on_view_mode_changed(self, mode: str) -> None:
        self.set_view_mode(mode, save=True)

    def _make_empty_page(
        self,
        icon_name: str,
        title: str,
        subtitle: str,
        *,
        show_import: bool,
    ) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("InboxEmptyState")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(32, 64, 32, 64)
        v.setSpacing(12)
        v.addStretch(2)
        icon = lucide_icon(icon_name, size=56, color_hex=MONOS_COLORS.get("text_meta", "#71717a"))
        icon_lb = QLabel(wrap)
        icon_lb.setObjectName("InboxEmptyStateIcon")
        if not icon.isNull():
            icon_lb.setPixmap(icon.pixmap(56, 56))
        icon_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(icon_lb, 0, Qt.AlignmentFlag.AlignHCenter)
        title_lb = QLabel(title, wrap)
        title_lb.setObjectName("InboxEmptyStateTitle")
        title_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lb.setFont(monos_font("Inter", 15, QFont.Weight.DemiBold))
        v.addWidget(title_lb, 0, Qt.AlignmentFlag.AlignHCenter)
        sub_lb = QLabel(subtitle, wrap)
        sub_lb.setObjectName("InboxEmptyStateSubtitle")
        sub_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lb.setWordWrap(True)
        sub_lb.setMaximumWidth(420)
        sub_lb.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        v.addWidget(sub_lb, 0, Qt.AlignmentFlag.AlignHCenter)
        if show_import:
            import_btn = QPushButton("Import delivery", wrap)
            import_btn.setObjectName("InboxPrimaryButton")
            import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            import_icon = lucide_icon("upload", size=16, color_hex="#ffffff")
            if not import_icon.isNull():
                import_btn.setIcon(import_icon)
            import_btn.clicked.connect(self.import_clicked.emit)
            v.addSpacing(8)
            v.addWidget(import_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(3)
        return wrap

    def _load_view_mode(self) -> None:
        raw = self._settings.value(self._settings_key, "tile")
        mode = str(raw).strip().lower()
        if mode not in ("tile", "list"):
            mode = "tile"
        self.set_view_mode(mode, save=False)

    def set_view_mode(self, mode: str, *, save: bool = True) -> None:
        if mode not in ("tile", "list"):
            return
        self._view_mode = mode
        self._view_stack.setCurrentIndex(0 if mode == "list" else 1)
        self._content_toolbar.set_view_mode(mode)
        if save:
            self._settings.setValue(self._settings_key, mode)
        if mode == "tile":
            self._grid_last = None
            self._schedule_grid_layout_sync()

    def set_content(self, project_root: Path | None, source_filter: str) -> None:
        self._project_root = project_root
        self._source_filter = (source_filter or "").strip().lower()
        self._entries = self._collect_entries()
        self._populate_views()
        self._sync_ui()

    def _collect_entries(self) -> list[_DateFolderEntry]:
        if not self._project_root or not self._project_root.is_dir() or not self._source_filter:
            return []
        if self._scan_fn is None:
            return []
        try:
            nodes = self._scan_fn(self._project_root)
        except Exception:
            nodes = []
        for node in nodes:
            if (node.name or "").lower() != self._source_filter:
                continue
            if not node.is_dir or not node.children:
                return []
            children = [
                child
                for child in node.children
                if getattr(child, "is_dir", True) and getattr(child, "path", None)
            ]
            children.sort(key=lambda c: date_folder_sort_key(c.name or ""), reverse=True)
            out: list[_DateFolderEntry] = []
            for child in children:
                p = Path(child.path)
                name = child.name or str(p)
                meta, parsed = _format_folder_meta(p, name)
                out.append(_DateFolderEntry(name, p, meta=meta, parsed_date=parsed))
            return out
        return []

    def _populate_views(self) -> None:
        self._list.clear()
        for entry in self._entries:
            it = QListWidgetItem(entry.label)
            it.setData(Qt.ItemDataRole.UserRole, str(entry.path))
            it.setData(_DateFolderListModel.RoleMeta, entry.meta)
            it.setToolTip(f"{entry.label}\n{entry.meta}" if entry.meta else entry.label)
            it.setSizeHint(QSize(0, _LIST_ROW_H))
            self._list.addItem(it)
        self._grid_model.set_entries(self._entries)

    def _sync_ui(self) -> None:
        source = _source_label(self._source_filter)
        count = len(self._entries)
        if not self._project_root or not self._project_root.is_dir():
            self._outer_stack.setCurrentIndex(0)
            self._empty_stack.setCurrentIndex(0)
            self._content_toolbar.setVisible(False)
            return
        if not self._source_filter:
            self._outer_stack.setCurrentIndex(0)
            self._empty_stack.setCurrentIndex(1)
            self._content_toolbar.setVisible(False)
            return
        if count == 0:
            self._outer_stack.setCurrentIndex(0)
            self._empty_stack.setCurrentIndex(2)
            self._content_toolbar.setVisible(False)
            return

        self._outer_stack.setCurrentIndex(1)
        self._content_toolbar.setVisible(True)
        self._content_toolbar.set_context(
            hint="Double-click a folder to browse and distribute files",
            show_toggle=True,
        )
        if self._view_mode == "tile":
            self._grid_last = None
            self._schedule_grid_layout_sync()

    def _emit_date_folder_open(self, path: Path) -> None:
        if not path.is_dir():
            return
        self.date_folder_clicked.emit(path)

    def _source_folder_path(self) -> Path | None:
        if self._entries:
            parent = self._entries[0].path.parent
            if parent.is_dir():
                return parent
        if not self._project_root or not self._source_filter:
            return None
        try:
            root = self._scan_fn(self._project_root) if self._scan_fn else []
        except Exception:
            root = []
        for node in root:
            if (node.name or "").lower() == self._source_filter and getattr(node, "path", None):
                candidate = Path(node.path)
                if candidate.is_dir():
                    return candidate
        return None

    def _show_date_context_menu(self, pos: QPoint, viewport: QWidget, folder_path: Path | None) -> None:
        menu = QMenu(viewport)
        icon = lambda name: lucide_icon(name, size=16, color_hex=MONOS_COLORS["text_label"])

        if folder_path is None:
            import_act = menu.addAction(icon("upload"), "Import")
            open_src_act = menu.addAction(icon("folder-open"), "Open folder")
            action = menu.exec(viewport.mapToGlobal(pos))
            if action is None:
                return
            if action == import_act:
                self.import_clicked.emit()
            elif action == open_src_act:
                src = self._source_folder_path()
                if src is not None:
                    self.open_folder_requested.emit(src)
            return

        browse_act = menu.addAction(icon("folder"), "Browse")
        open_folder_act = menu.addAction(icon("folder-open"), "Open folder")
        menu.addSeparator()
        import_act = menu.addAction(icon("upload"), "Import")
        action = menu.exec(viewport.mapToGlobal(pos))
        if action is None:
            return
        if action == browse_act:
            self._emit_date_folder_open(folder_path)
        elif action == open_folder_act:
            self.open_folder_requested.emit(folder_path)
        elif action == import_act:
            self.import_clicked.emit()

    def _on_list_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        folder_path = None
        if item is not None:
            path_str = item.data(Qt.ItemDataRole.UserRole)
            if path_str:
                try:
                    folder_path = Path(path_str)
                    if not folder_path.is_dir():
                        folder_path = None
                except (TypeError, ValueError):
                    folder_path = None
        self._show_date_context_menu(pos, self._list.viewport(), folder_path)

    def _on_grid_context_menu(self, pos: QPoint) -> None:
        index = self._grid.indexAt(pos)
        folder_path = None
        if index.isValid():
            entry = self._grid_model.entry_at(index.row())
            if entry is not None and entry.path.is_dir():
                folder_path = entry.path
        self._show_date_context_menu(pos, self._grid.viewport(), folder_path)

    def _on_list_item_double_clicked(self, item: QListWidgetItem) -> None:
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        try:
            self._emit_date_folder_open(Path(path_str))
        except (TypeError, ValueError):
            pass

    def _on_grid_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        entry = self._grid_model.entry_at(index.row())
        if entry is not None:
            self._emit_date_folder_open(entry.path)

    def _schedule_grid_layout_sync(self) -> None:
        if self._grid_sync_scheduled:
            return
        self._grid_sync_scheduled = True
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._sync_grid_layout)

    def _sync_grid_layout(self) -> None:
        self._grid_sync_scheduled = False
        if self._view_mode != "tile":
            return
        try:
            vw = int(self._grid.viewport().width())
        except Exception:
            return
        if vw <= 0:
            return
        inner_w = max(1, vw - 32)
        cell_w = _CARD_W + _GRID_GAP
        cols = max(1, (inner_w + _GRID_GAP) // cell_w)
        sig = (cols, _CARD_W, _CARD_H)
        if self._grid_last == sig:
            return
        self._grid_last = sig
        self._grid.setGridSize(QSize(cell_w, _CARD_H + _GRID_GAP))
        self._grid_delegate.set_card_size(QSize(_CARD_W, _CARD_H))

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._grid.viewport() and event.type() == event.Type.Resize:
            self._schedule_grid_layout_sync()
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._view_mode == "tile":
            self._schedule_grid_layout_sync()

    def set_toolbar_visible(self, visible: bool) -> None:
        if visible and self._entries:
            self._content_toolbar.setVisible(True)
        else:
            self._content_toolbar.setVisible(False)