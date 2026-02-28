"""
Inbox pane widgets: InboxTreePane (breadcrumb + file tree for one date folder).
ReferenceTreePane for Project Guide page. Used by InboxPageWidget, ReferencePageWidget.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_log_ref = logging.getLogger(__name__)


def _fs_model_force_reload(model: QFileSystemModel, tree: QTreeView, tree_root_path: Path | None) -> None:
    """Qt6: QFileSystemModel has no refresh(); force update by resetting root path."""
    if not tree_root_path or not tree_root_path.is_dir():
        return
    root_str = str(tree_root_path.resolve())
    model.setRootPath("")
    model.setRootPath(root_str)
    idx = model.index(root_str)
    if idx.isValid():
        tree.setRootIndex(idx)

from PySide6.QtCore import QEvent, QFileInfo, QItemSelectionModel, QMimeData, QPoint, QRect, QSize, Qt, Signal, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QAbstractFileIconProvider,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
)
from PySide6.QtGui import QDrag, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileIconProvider,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import QSortFilterProxyModel
from PySide6.QtGui import QDesktopServices

import shutil

from monostudio.core.project_guide_tags import (
    ALL_TAG_IDS,
    DEFAULT_TAG_DEFINITIONS,
    TAG_COLOR_BY_ID,
    TAG_LABEL_BY_ID,
    ancestor_paths,
    build_color_map,
    get_tags_for_item,
    paths_with_tag,
    read_tag_definitions,
    set_tags_for_item,
    toggle_tag_for_items,
)
from monostudio.ui_qt.delete_confirm_dialog import ask_delete
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification import notify as notification_service
from monostudio.ui_qt.style import FILE_TYPE_ICON_COLORS, MONOS_COLORS, monos_font

_TREE_ICON_SIZE = 18

# Extension sets for file-type icons (lowercase with leading dot)
_EXT_IMAGE = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".ico", ".svg", ".pur"})  # .pur = PureRef
_EXT_VIDEO = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg", ".ts"})
_EXT_AUDIO = frozenset({".mp3", ".wav", ".aiff", ".aif", ".ogg", ".flac", ".m4a", ".wma", ".aac"})
_EXT_ARCHIVE = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".zst"})
_EXT_DOCUMENT = frozenset({".pdf", ".txt", ".rtf", ".md", ".odt", ".xls", ".xlsx", ".csv"})  # .doc/.docx → _EXT_DOC; .pptx/.ppt → _EXT_PPTX
# DCC workfile extensions (from pipeline/dccs.json)
_EXT_DCC = frozenset({".blend", ".ma", ".mb", ".hip", ".hiplc", ".hipnc"})
_EXT_SPP = frozenset({".spp"})  # Substance Painter → brand:substancepainter
# Brand/DCC icons (tree + Inspector)
_EXT_PS = frozenset({".psd", ".psb"})
_EXT_3DSMAX = frozenset({".max"})
_EXT_ZBRUSH = frozenset({".zbr", ".ztl", ".zpr"})
# 3D interchange / engine
_EXT_FBX = frozenset({".fbx"})
_EXT_OBJ = frozenset({".obj"})
_EXT_ABC = frozenset({".abc"})
_EXT_USD = frozenset({".usd", ".usda", ".usdc"})
_EXT_UNITY = frozenset({".unity", ".prefab"})
_EXT_UNREAL = frozenset({".uproject", ".umap"})
# Office (brand when SVG có; không thì file-text)
_EXT_PPTX = frozenset({".pptx", ".ppt"})
_EXT_DOC = frozenset({".doc", ".docx"})


def _file_icon_spec(is_dir: bool, suffix: str) -> tuple[str, str]:
    """Return (lucide_icon_name or 'brand:slug', color_hex) for folder or file by suffix."""
    colors = FILE_TYPE_ICON_COLORS
    if is_dir:
        return ("folder", colors["folder"])
    ext = (suffix or "").strip().lower()
    if not ext.startswith("."):
        ext = "." + ext if ext else ""
    if ext in _EXT_IMAGE:
        return ("file-image", colors["image"])
    if ext in _EXT_VIDEO:
        return ("file-video", colors["video"])
    if ext in _EXT_AUDIO:
        return ("file-music", colors["audio"])
    if ext in _EXT_PS:
        return ("brand:photoshop", colors["dcc"])
    if ext in _EXT_3DSMAX:
        return ("brand:3dsmax", colors["dcc"])
    if ext in _EXT_ZBRUSH:
        return ("zbrush", colors["dcc"])
    if ext in _EXT_FBX:
        return ("box", colors["dcc"])
    if ext in _EXT_USD:
        return ("brand:usd", colors["dcc"])
    if ext in _EXT_OBJ or ext in _EXT_ABC:
        return ("box", colors["dcc"])
    if ext in _EXT_UNITY:
        return ("brand:unity", colors["dcc"])
    if ext in _EXT_UNREAL:
        return ("brand:unrealengine", colors["dcc"])
    if ext in _EXT_PPTX:
        return ("file-text", colors["document"])
    if ext in _EXT_DOC:
        return ("file-text", colors["document"])
    if ext in _EXT_SPP:
        return ("brand:substancepainter", colors["dcc"])
    if ext in _EXT_DCC:
        return ("box", colors["dcc"])
    if ext in _EXT_ARCHIVE:
        return ("file-archive", colors["archive"])
    if ext in _EXT_DOCUMENT:
        return ("file-text", colors["document"])
    return ("file", colors["file"])


def _tree_file_icon(name: str, color: str) -> "QIcon":
    """Tree icon: brand:slug → brand_icon; else lucide_icon."""
    if name.startswith("brand:"):
        from monostudio.ui_qt.brand_icons import brand_icon
        slug = name[6:]
        ic = brand_icon(slug, size=_TREE_ICON_SIZE, color_hex=color)
        return ic if not ic.isNull() else lucide_icon("box", size=_TREE_ICON_SIZE, color_hex=color)
    return lucide_icon(name, size=_TREE_ICON_SIZE, color_hex=color)


class _LucideFileIconProvider(QFileIconProvider):
    """Icon provider for QFileSystemModel using Lucide + brand icons and file-type colors."""

    def icon(self, arg):  # QFileInfo or QAbstractFileIconProvider.IconType
        if isinstance(arg, QFileInfo):
            name, color = _file_icon_spec(arg.isDir(), arg.suffix() or "")
            return _tree_file_icon(name, color)
        if arg == QAbstractFileIconProvider.IconType.Folder:
            name, color = _file_icon_spec(True, "")
            return _tree_file_icon(name, color)
        if arg == QAbstractFileIconProvider.IconType.File:
            name, color = _file_icon_spec(False, "")
            return _tree_file_icon(name, color)
        return super().icon(arg)


# Full-row highlight for tree (bỏ gap giữa branch và item)
def _tree_selected_brush() -> QBrush:
    return QBrush(QColor(59, 130, 246, int(255 * 0.12)))


def _tree_hover_brush() -> QBrush:
    return QBrush(QColor(255, 255, 255, int(255 * 0.06)))


_BRANCH_ICON_SIZE = 14


class _InboxTreeDelegate(QStyledItemDelegate):
    """Vẽ selection/hover full-row và branch arrow bằng Lucide chevron."""

    def paint(self, painter: QPainter, option, index) -> None:
        view = option.widget
        if view is not None and index.isValid():
            row_rect = option.rect
            full_width = view.viewport().width()
            if full_width > 0 and row_rect.height() > 0:
                full_rect = QRect(0, row_rect.y(), full_width, row_rect.height())
                selected = option.state & QStyle.StateFlag.State_Selected
                hover = option.state & QStyle.StateFlag.State_MouseOver
                if selected:
                    painter.fillRect(full_rect, _tree_selected_brush())
                elif hover:
                    painter.fillRect(full_rect, _tree_hover_brush())
            # Branch arrow (Lucide chevron) khi có con: vùng branch = ô ngay trái ô item
            if index.column() == 0:
                model = index.model()
                if model is not None and model.hasChildren(index):
                    ind = view.indentation()
                    branch_rect = QRect(
                        row_rect.x() - ind,
                        row_rect.y(),
                        ind,
                        row_rect.height(),
                    )
                    icon_name = "chevron-down" if view.isExpanded(index) else "chevron-right"
                    icon = lucide_icon(
                        icon_name,
                        size=_BRANCH_ICON_SIZE,
                        color_hex=MONOS_COLORS["text_label"],
                    )
                    icon.paint(
                        painter,
                        branch_rect,
                        Qt.AlignmentFlag.AlignCenter,
                        QIcon.Mode.Normal,
                    )
        super().paint(painter, option, index)


class InboxTreePane(QWidget):
    """Breadcrumb + file tree for one date folder. Emits back_requested, tree_selection_changed, open_folder_requested, import_requested, history_requested (if show_history_action)."""

    back_requested = Signal()
    tree_selection_changed = Signal(object)  # Path | None
    open_folder_requested = Signal(object)  # Path (date folder)
    import_requested = Signal()
    history_requested = Signal()

    def __init__(self, date_folder_path: Path, parent=None, *, show_history_action: bool = False, breadcrumb_title: str = "Inbox") -> None:
        super().__init__(parent)
        self._date_folder_path = Path(date_folder_path)
        self._show_history_action = show_history_action
        self._breadcrumb_title = breadcrumb_title or "Inbox"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget(self)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(12, 8, 12, 8)
        bar_lay.setSpacing(8)
        bar_lay.addWidget(self._make_breadcrumb(), 0)
        bar_lay.addStretch(1)
        lay.addWidget(bar, 0)
        self._fs_model = QFileSystemModel(self)
        self._fs_model.setRootPath("")
        self._fs_model.setIconProvider(_LucideFileIconProvider())
        self._tree = QTreeView(self)
        self._tree.setObjectName("InboxSplitTree")
        self._tree.setModel(self._fs_model)
        self._tree.setRootIndex(self._fs_model.index(str(self._date_folder_path.resolve())))
        self._tree.setSelectionMode(QTreeView.ExtendedSelection)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(20)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.hideColumn(1)
        self._tree.hideColumn(2)
        self._tree.hideColumn(3)
        self._tree.setItemDelegate(_InboxTreeDelegate(self._tree))
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.selectionModel().selectionChanged.connect(self._emit_tree_selection)
        self._tree.doubleClicked.connect(self._on_tree_double_clicked)
        self._tree.installEventFilter(self)
        lay.addWidget(self._tree, 1)

    def _make_breadcrumb(self) -> QWidget:
        path_parts = self._date_folder_path.parts
        trail = path_parts[-2:] if len(path_parts) >= 2 else path_parts
        wrap = QWidget(self)
        wlay = QHBoxLayout(wrap)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(3)
        sep_style = "color: #71717a; font-size: 10px;"
        label_style = "color: #a1a1aa; font-size: 11px;"
        link_style = (
            "QPushButton { color: #a1a1aa; font-size: 11px; border: none; background: transparent; }"
            "QPushButton:hover { color: #60a5fa; }"
        )
        segments = [self._breadcrumb_title, *trail]
        for i, name in enumerate(segments):
            if i > 0:
                sep = QLabel("›", wrap)
                sep.setStyleSheet(sep_style)
                sep.setFont(monos_font("Inter", 10))
                wlay.addWidget(sep, 0)
            display = (name or "").replace("_", " ").strip().title() or name
            is_last = i == len(segments) - 1
            if is_last:
                lb = QLabel(display, wrap)
                lb.setStyleSheet(label_style)
                lb.setFont(monos_font("Inter", 11))
                wlay.addWidget(lb, 0)
            else:
                btn = QPushButton(display, wrap)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFlat(True)
                btn.setStyleSheet(link_style)
                btn.setFont(monos_font("Inter", 11))
                btn.clicked.connect(self.back_requested.emit)
                wlay.addWidget(btn, 0)
        return wrap

    def _emit_tree_selection(self) -> None:
        idx = self._tree.currentIndex()
        if not idx.isValid():
            self.tree_selection_changed.emit(None)
            return
        path = Path(self._fs_model.filePath(idx))
        self.tree_selection_changed.emit(path)

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        """Context menu: when click on empty area → Open folder, Import [, History]. When click on an item → full menu. Use only indexAt(pos), not currentIndex()."""
        idx = self._tree.indexAt(pos)
        has_selection = idx.isValid()
        path = None
        if has_selection:
            path = Path(self._fs_model.filePath(idx))
            if not path.exists():
                has_selection = False
                path = None

        menu = QMenu(self._tree)
        _icon = lambda name: lucide_icon(name, size=16, color_hex=MONOS_COLORS["text_label"])
        _icon_red = lambda name: lucide_icon(name, size=16, color_hex=MONOS_COLORS.get("destructive", "#ef4444"))

        if not has_selection:
            open_folder_act = menu.addAction(_icon("folder-open"), "Open folder")
            import_act = menu.addAction(_icon("upload"), "Import")
            if self._show_history_action:
                menu.addSeparator()
                history_act = menu.addAction(_icon("layers"), "History")
            else:
                history_act = None
            action = menu.exec(self._tree.viewport().mapToGlobal(pos))
            if action is None:
                return
            if action == open_folder_act:
                self.open_folder_requested.emit(self._date_folder_path)
            elif action == import_act:
                self.import_requested.emit()
            elif action == history_act and self._show_history_action:
                self.history_requested.emit()
            return

        open_act = menu.addAction(_icon("file"), "Open")
        open_folder_act = menu.addAction(_icon("folder-open"), "Open folder")
        rename_act = menu.addAction(_icon("copy"), "Rename")
        menu.addSeparator()
        delete_act = menu.addAction(_icon_red("x"), "Delete")
        menu.addSeparator()
        import_act = menu.addAction(_icon("upload"), "Import")
        if self._show_history_action:
            menu.addSeparator()
            history_act = menu.addAction(_icon("layers"), "History")
        else:
            history_act = None
        action = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == open_act:
            self._tree_open_path(path)
        elif action == open_folder_act:
            self._tree_open_folder(path)
        elif action == rename_act:
            self._tree.edit(idx)
        elif action == delete_act:
            self._tree_delete_path(path, idx)
        elif action == import_act:
            self.import_requested.emit()
        elif action == history_act and self._show_history_action:
            self.history_requested.emit()

    def _tree_open_path(self, path: Path) -> None:
        """Open file with default app or folder in explorer."""
        if path.is_dir():
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            except Exception:
                pass
        else:
            try:
                os.startfile(path.resolve())
            except (OSError, AttributeError):
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
                except Exception:
                    pass

    def _tree_open_folder(self, path: Path) -> None:
        """Open containing folder in explorer (parent if item is file)."""
        target = path if path.is_dir() else path.parent
        if target.is_dir():
            self.open_folder_requested.emit(target)

    def _tree_delete_path(self, path: Path, index) -> None:
        """Delete file or folder after confirmation."""
        name = path.name or str(path)
        if path.is_dir():
            msg = f"Delete folder \"{name}\" and all its contents?"
        else:
            msg = f"Delete file \"{name}\"?"
        if not ask_delete(self._tree, "Delete", msg):
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            _fs_model_force_reload(self._fs_model, self._tree, self._date_folder_path)
            label = "Folder" if path.is_dir() else "File"
            notification_service.success(f"Deleted {label} \"{name}\".")
        except OSError as e:
            QMessageBox.warning(self._tree, "Delete", f"Could not delete: {e}")

    def _on_tree_double_clicked(self, index) -> None:
        if not index.isValid():
            return
        path = Path(self._fs_model.filePath(index))
        if path.is_file():
            try:
                os.startfile(path.resolve())
            except OSError:
                pass

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:
        if obj is self._tree and event.type() == QEvent.Type.FocusIn:
            self._emit_tree_selection()
        return super().eventFilter(obj, event)

    def date_folder_path(self) -> Path:
        return self._date_folder_path

    def get_selected_paths(self) -> list[Path]:
        """Return list of selected file/folder paths in the tree (for distribute)."""
        paths = []
        seen = set()
        for idx in self._tree.selectionModel().selectedIndexes():
            if idx.column() != 0:
                continue
            path = Path(self._fs_model.filePath(idx))
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def set_date_folder_path(self, path: Path) -> None:
        self._date_folder_path = Path(path)
        self._tree.setRootIndex(self._fs_model.index(str(self._date_folder_path.resolve())))

    def get_tree_state(self) -> dict:
        expanded: list[str] = []
        root_path = self._date_folder_path.resolve()

        def walk(index):
            if not index.isValid():
                return
            p = Path(self._fs_model.filePath(index))
            try:
                rel = p.relative_to(root_path)
            except ValueError:
                return
            if self._tree.isExpanded(index):
                expanded.append(str(rel).replace("\\", "/"))
            for r in range(self._fs_model.rowCount(index)):
                walk(self._fs_model.index(r, 0, index))

        root_idx = self._tree.rootIndex()
        if root_idx.isValid():
            for r in range(self._fs_model.rowCount(root_idx)):
                walk(self._fs_model.index(r, 0, root_idx))
        return {"expanded_paths": expanded}

    def set_tree_state(self, state: dict | None) -> None:
        if not state:
            return
        expanded = state.get("expanded_paths")
        if not expanded or not isinstance(expanded, list):
            return
        root_path = self._date_folder_path.resolve()

        def apply():
            for rel in sorted(expanded, key=lambda p: (p.count("/"), p)):
                full = root_path / rel.replace("\\", "/")
                if not full.exists():
                    continue
                idx = self._fs_model.index(str(full), 0)
                if idx.isValid():
                    self._tree.expand(idx)

        # Defer so root index and model are ready (e.g. after set_date_folder_path / setRootIndex).
        QTimer.singleShot(50, apply)


class _RefDropViewport(QWidget):
    """Viewport that accepts file/folder drops and forwards to callback(paths, viewport_pos)."""

    def __init__(self, tree: QTreeView, on_drop, parent=None) -> None:
        super().__init__(parent)
        self._tree = tree
        self._on_drop = on_drop
        # False: MainWindow receives all drops and forwards; move/copy decided there by path origin
        self.setAcceptDrops(False)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        _log_ref.debug("_RefDropViewport dragEnterEvent: hasUrls=%s", event.mimeData().hasUrls())
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        _log_ref.debug("_RefDropViewport dropEvent: received")
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        pos = event.position().toPoint()
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.exists():
                    paths.append(p)
        event.acceptProposedAction()
        _log_ref.debug("_RefDropViewport dropEvent: pos=(%s,%s) paths=%s", pos.x(), pos.y(), [str(p) for p in paths])
        if paths and callable(self._on_drop):
            self._on_drop(paths, pos)


_TAG_ICON_SIZE = 10
_TAG_ICON_SPACING = 2
_TAG_ICON_RIGHT_MARGIN = 8


class _RefTreeDelegate(_InboxTreeDelegate):
    """Extends _InboxTreeDelegate with colored tag icons drawn to the right of the item text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pane: "ReferenceTreePane | None" = None
        self._tag_pixmap_cache: dict[str, QPixmap] = {}

    def set_pane(self, pane: "ReferenceTreePane") -> None:
        self._pane = pane

    def _get_tag_pixmap(self, color_hex: str) -> QPixmap:
        cached = self._tag_pixmap_cache.get(color_hex)
        if cached is not None:
            return cached
        from monostudio.ui_qt.lucide_icons import lucide_icon
        ic = lucide_icon("tag-filled", size=_TAG_ICON_SIZE, color_hex=color_hex)
        px = ic.pixmap(_TAG_ICON_SIZE, _TAG_ICON_SIZE)
        self._tag_pixmap_cache[color_hex] = px
        return px

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if not index.isValid() or index.column() != 0 or self._pane is None:
            return
        path = self._pane._path_from_tree_index(index)
        if path is None:
            return
        pg_root = self._pane._project_guide_root
        if pg_root is None:
            return
        try:
            rel = path.relative_to(pg_root).as_posix()
        except (ValueError, OSError):
            return
        tags = get_tags_for_item(self._pane._item_tags, rel)
        if not tags:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = option.rect
        x = rect.right() - _TAG_ICON_RIGHT_MARGIN
        cy = rect.center().y()
        for tag_id in reversed(tags):
            color_hex = self._pane._tag_color_map.get(tag_id)
            if not color_hex:
                continue
            px = self._get_tag_pixmap(color_hex)
            x -= _TAG_ICON_SIZE
            painter.drawPixmap(x, cy - _TAG_ICON_SIZE // 2, px)
            x -= _TAG_ICON_SPACING
        painter.restore()


class _TagFilterProxy(QSortFilterProxyModel):
    """Proxy that filters QFileSystemModel rows by tag. Only items with the active tag
    (and their ancestor folders) are shown. When no tag filter is set, all rows pass."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active_tag: str | None = None
        self._tagged_paths: set[str] = set()
        self._ancestor_paths: set[str] = set()
        self._project_guide_root: Path | None = None
        self._tree_root_rel: str | None = None

    def set_project_guide_root(self, root: Path | None) -> None:
        self._project_guide_root = Path(root) if root else None

    def set_tree_root_path(self, tree_root: Path | None) -> None:
        """Store the tree's root path so filterAcceptsRow always accepts it (prevents drives fallback)."""
        if tree_root and self._project_guide_root:
            try:
                self._tree_root_rel = tree_root.relative_to(self._project_guide_root).as_posix()
            except (ValueError, OSError):
                self._tree_root_rel = None
        else:
            self._tree_root_rel = None

    def set_tag_filter(self, tag_id: str | None, item_tags: dict[str, list[str]]) -> None:
        self._active_tag = tag_id
        if tag_id:
            self._tagged_paths = paths_with_tag(item_tags, tag_id)
            self._ancestor_paths = ancestor_paths(self._tagged_paths)
        else:
            self._tagged_paths = set()
            self._ancestor_paths = set()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self._active_tag:
            return True
        src = self.sourceModel()
        if src is None:
            return True
        idx = src.index(source_row, 0, source_parent)
        if not idx.isValid():
            return True
        file_path = src.filePath(idx)
        if not file_path or not self._project_guide_root:
            return True
        try:
            rel = Path(file_path).relative_to(self._project_guide_root).as_posix()
        except (ValueError, OSError):
            return True
        if not rel or rel == ".":
            return True
        if self._tree_root_rel and rel == self._tree_root_rel:
            return True
        return rel in self._tagged_paths or rel in self._ancestor_paths


class ReferenceTreePane(QWidget):
    """Tree for Project Guide page: root = project_guide/<department>. Breadcrumb Project Guide > department.
    Emits tree_selection_changed(Path|None), open_folder_requested(Path), import_requested().
    Supports context menu: Open, Open folder, New folder, Rename, Delete, Import. Drag-drop copies into folder."""

    tree_selection_changed = Signal(object)  # Path | None
    open_folder_requested = Signal(object)  # Path
    import_requested = Signal()
    item_tags_changed = Signal()  # emitted after tag assign/remove so sidebar can refresh counts

    def __init__(self, root_path: Path | None, department_label: str, parent=None) -> None:
        super().__init__(parent)
        self._root_path = Path(root_path) if root_path else None
        self._department_label = department_label or "Reference"
        self._project_root: Path | None = None
        self._project_guide_root: Path | None = None
        self._item_tags: dict[str, list[str]] = {}
        self._tag_defs: list[dict[str, str]] = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map: dict[str, str] = dict(TAG_COLOR_BY_ID)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QWidget(self)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(12, 8, 12, 8)
        bar_lay.setSpacing(8)
        self._breadcrumb_wrap = self._make_breadcrumb()
        bar_lay.addWidget(self._breadcrumb_wrap, 0)
        bar_lay.addStretch(1)
        lay.addWidget(bar, 0)
        self._fs_model = QFileSystemModel(self)
        self._fs_model.setRootPath("")
        self._fs_model.setIconProvider(_LucideFileIconProvider())
        self._fs_model.directoryLoaded.connect(self._on_fs_directory_loaded)
        self._proxy = _TagFilterProxy(self)
        self._proxy.setSourceModel(self._fs_model)
        self._tree = QTreeView(self)
        self._tree.setObjectName("InboxSplitTree")
        self._tree.setModel(self._proxy)
        self._tree.setSelectionMode(QTreeView.ExtendedSelection)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(20)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.hideColumn(1)
        self._tree.hideColumn(2)
        self._tree.hideColumn(3)
        self._ref_delegate = _RefTreeDelegate(self._tree)
        self._ref_delegate.set_pane(self)
        self._tree.setItemDelegate(self._ref_delegate)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_ref_tree_context_menu)
        self._tree.selectionModel().selectionChanged.connect(self._emit_tree_selection)
        self._tree.doubleClicked.connect(self._on_ref_tree_double_clicked)
        self._tree.setAcceptDrops(False)
        self._tree.installEventFilter(self)
        ref_viewport = _RefDropViewport(self._tree, self._ref_do_drop, self._tree)
        self._tree.setViewport(ref_viewport)
        ref_viewport.installEventFilter(self)
        self._ref_middle_drag_start: QPoint | None = None

        self._empty_tag_overlay = QWidget(self)
        self._empty_tag_overlay.setObjectName("TagEmptyOverlay")
        self._empty_tag_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        ov_lay = QVBoxLayout(self._empty_tag_overlay)
        ov_lay.setContentsMargins(0, 0, 0, 0)
        ov_lay.setSpacing(8)
        ov_lay.addStretch(2)
        ov_icon = QLabel(self._empty_tag_overlay)
        ov_icon.setAlignment(Qt.AlignCenter)
        ov_icon.setPixmap(
            lucide_icon("tag", size=48, color_hex="#3f3f46").pixmap(48, 48)
        )
        ov_lay.addWidget(ov_icon, 0, Qt.AlignCenter)
        ov_text = QLabel("No files tagged", self._empty_tag_overlay)
        ov_text.setAlignment(Qt.AlignCenter)
        ov_text.setObjectName("TagEmptyOverlayText")
        ov_lay.addWidget(ov_text, 0, Qt.AlignCenter)
        ov_lay.addStretch(3)
        self._empty_tag_overlay.setVisible(False)

        self._empty_dept_overlay = QWidget(self)
        self._empty_dept_overlay.setObjectName("RefDeptEmptyOverlay")
        self._empty_dept_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        dept_ov_lay = QVBoxLayout(self._empty_dept_overlay)
        dept_ov_lay.setContentsMargins(24, 48, 24, 48)
        dept_ov_lay.setSpacing(16)
        dept_ov_lay.addStretch(2)
        dept_ov_icon = QLabel(self._empty_dept_overlay)
        dept_ov_icon.setAlignment(Qt.AlignCenter)
        dept_ov_icon.setPixmap(
            lucide_icon("upload", size=64, color_hex=MONOS_COLORS.get("text_meta", "#71717a")).pixmap(64, 64)
        )
        dept_ov_lay.addWidget(dept_ov_icon, 0, Qt.AlignCenter)
        dept_ov_line1 = QLabel("Drag and drop files or folders here", self._empty_dept_overlay)
        dept_ov_line1.setAlignment(Qt.AlignCenter)
        dept_ov_line1.setObjectName("RefDeptEmptyOverlayText")
        dept_ov_lay.addWidget(dept_ov_line1, 0, Qt.AlignCenter)
        dept_ov_line2 = QLabel("or use the Import button above", self._empty_dept_overlay)
        dept_ov_line2.setAlignment(Qt.AlignCenter)
        dept_ov_line2.setObjectName("RefDeptEmptyOverlayText")
        dept_ov_lay.addWidget(dept_ov_line2, 0, Qt.AlignCenter)
        dept_ov_lay.addStretch(3)
        self._empty_dept_overlay.setVisible(False)

        tree_stack = QWidget(self)
        tree_stack_lay = QVBoxLayout(tree_stack)
        tree_stack_lay.setContentsMargins(0, 0, 0, 0)
        tree_stack_lay.setSpacing(0)
        tree_stack_lay.addWidget(self._tree)
        self._empty_tag_overlay.setParent(tree_stack)
        self._empty_tag_overlay.raise_()
        self._empty_dept_overlay.setParent(tree_stack)
        self._empty_dept_overlay.raise_()

        lay.addWidget(tree_stack, 1)
        self._tree_stack = tree_stack
        self._apply_root_index()

    # ---- Helpers: proxy-aware index ↔ path ----

    def _path_from_tree_index(self, proxy_idx) -> Path | None:
        """Convert a proxy (tree) index to a filesystem Path."""
        if not proxy_idx.isValid():
            return None
        src_idx = self._proxy.mapToSource(proxy_idx)
        fp = self._fs_model.filePath(src_idx)
        return Path(fp) if fp else None

    def _tree_index_from_path(self, path: Path):
        """Convert a filesystem Path to a proxy (tree) index."""
        src_idx = self._fs_model.index(str(Path(path).resolve()))
        return self._proxy.mapFromSource(src_idx)

    def _on_fs_directory_loaded(self, path: str) -> None:
        """Re-apply tree root when the fs model finishes loading (avoids drives flash)."""
        if self._root_path and path and Path(path).resolve() == self._root_path.resolve():
            QTimer.singleShot(0, self._apply_root_index)

    def _apply_root_index(self) -> None:
        self._proxy.set_tree_root_path(self._root_path)
        if self._root_path and self._root_path.is_dir():
            root_str = str(self._root_path.resolve())
            self._fs_model.setRootPath(root_str)
            src_idx = self._fs_model.index(root_str)
            if src_idx.isValid():
                proxy_idx = self._proxy.mapFromSource(src_idx)
                if proxy_idx.isValid():
                    self._tree.setRootIndex(proxy_idx)
        else:
            self._fs_model.setRootPath("")
            self._tree.setRootIndex(self._proxy.mapFromSource(self._fs_model.index("")))
        self._sync_empty_tag_overlay()

    def _force_reload(self) -> None:
        """Reload QFileSystemModel and re-apply root index through proxy."""
        if not self._root_path or not self._root_path.is_dir():
            return
        root_str = str(self._root_path.resolve())
        self._fs_model.setRootPath("")
        self._fs_model.setRootPath(root_str)
        src_idx = self._fs_model.index(root_str)
        if src_idx.isValid():
            proxy_idx = self._proxy.mapFromSource(src_idx)
            if proxy_idx.isValid():
                self._tree.setRootIndex(proxy_idx)
        self._sync_empty_tag_overlay()

    # ---- Tag data & filter ----

    def set_project_guide_root(self, root: Path | None, project_root: Path | None = None) -> None:
        self._project_guide_root = Path(root) if root else None
        self._project_root = Path(project_root) if project_root else None
        self._proxy.set_project_guide_root(self._project_guide_root)

    def set_tag_data(self, item_tags: dict[str, list[str]]) -> None:
        self._item_tags = item_tags

    def reload_tag_definitions(self) -> None:
        if self._project_root:
            self._tag_defs = read_tag_definitions(self._project_root)
        else:
            self._tag_defs = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map = build_color_map(self._tag_defs)
        self._tree.viewport().update()

    def set_tag_filter(self, tag_id: str | None) -> None:
        self._proxy.set_tag_filter(tag_id, self._item_tags)
        self._apply_root_index()

    def _sync_empty_tag_overlay(self) -> None:
        tag = self._proxy._active_tag
        if tag:
            has_items = bool(self._proxy._tagged_paths)
            self._empty_tag_overlay.setVisible(not has_items)
            self._empty_dept_overlay.setVisible(False)
            if not has_items:
                self._empty_tag_overlay.setGeometry(self._tree_stack.rect())
                self._empty_tag_overlay.raise_()
            return
        self._empty_tag_overlay.setVisible(False)
        root_idx = self._tree.rootIndex()
        has_children = (
            bool(self._root_path)
            and root_idx.isValid()
            and self._proxy.rowCount(root_idx) > 0
        )
        self._empty_dept_overlay.setVisible(not has_children)
        if not has_children:
            self._empty_dept_overlay.setGeometry(self._tree_stack.rect())
            self._empty_dept_overlay.raise_()

    def get_item_tags(self) -> dict[str, list[str]]:
        return self._item_tags

    def _ref_drop_target_folder(self, viewport_pos: QPoint) -> Path | None:
        """Resolve drop position (in viewport coords) to the folder where files should be copied."""
        idx = self._tree.indexAt(viewport_pos)
        if idx.isValid():
            path = self._path_from_tree_index(idx)
            if path and path.is_dir():
                return path
            if path:
                return path.parent
        if self._root_path and self._root_path.is_dir():
            return self._root_path
        return None

    def get_drop_target_folder(self, pos_in_pane: QPoint) -> Path | None:
        """Given position in this pane's coords, return the folder to drop into (for page-level drop handling)."""
        viewport = self._tree.viewport()
        viewport_pos = viewport.mapFrom(self, pos_in_pane)
        out = self._ref_drop_target_folder(viewport_pos)
        _log_ref.debug(
            "RefTree get_drop_target: pos_in_pane=(%s,%s) viewport_pos=(%s,%s) viewport.rect=%s target=%s",
            pos_in_pane.x(), pos_in_pane.y(),
            viewport_pos.x(), viewport_pos.y(),
            (viewport.rect().x(), viewport.rect().y(), viewport.rect().width(), viewport.rect().height()),
            out,
        )
        return out

    def drop_files_to_folder(self, paths: list, target: Path) -> None:
        """Copy paths into target folder, refresh tree, and select the added items."""
        _log_ref.debug("RefTree drop_files_to_folder: target=%s paths=%s", target, [str(p) for p in paths])
        if not target or not target.is_dir():
            _log_ref.debug("RefTree drop_files_to_folder: skip (no target or not dir)")
            return
        added: list[Path] = []
        for src in paths:
            try:
                dest = target / src.name
                if src.is_dir():
                    if dest.exists():
                        for item in src.iterdir():
                            shutil.copy2(item, dest / item.name) if item.is_file() else shutil.copytree(item, dest / item.name)
                    else:
                        shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
                added.append(dest)
            except OSError:
                pass
        if self._root_path and target.is_relative_to(self._root_path):
            self._force_reload()
            if added:
                QTimer.singleShot(80, lambda: self._select_paths_in_tree(added))
                QTimer.singleShot(120, self._sync_empty_tag_overlay)

    def _select_paths_in_tree(self, paths: list[Path]) -> None:
        """Select the given paths in the tree (used after drop to highlight added items)."""
        if not paths:
            return
        self._tree.selectionModel().clearSelection()
        sel = self._tree.selectionModel()
        for i, p in enumerate(paths):
            idx = self._tree_index_from_path(p)
            if idx.isValid():
                sel.select(idx, QItemSelectionModel.SelectionFlag.Select)
                if i == 0:
                    self._tree.setCurrentIndex(idx)
                    self._tree.scrollTo(idx)

    def move_files_to_folder(self, paths: list, target: Path) -> None:
        """Move paths into target folder (reorder within tree). Refresh and select moved items."""
        if not target or not target.is_dir():
            return
        root = self._root_path
        if not root:
            return
        target_res = Path(target).resolve()
        moved: list[Path] = []
        for src in paths:
            try:
                src = Path(src).resolve()
                dest = target_res / src.name
                if not src.exists() or src == dest:
                    continue
                if dest.resolve() == src:
                    continue
                try:
                    if src.is_dir() and target_res.is_relative_to(src):
                        continue
                except ValueError:
                    pass
                if dest.exists():
                    dest.unlink() if dest.is_file() else shutil.rmtree(dest)
                shutil.move(str(src), str(dest))
                moved.append(dest)
            except OSError:
                pass
        if moved and root:
            self._force_reload()
            QTimer.singleShot(80, lambda: self._select_paths_in_tree(moved))

    def _ref_do_drop(self, paths: list, viewport_pos: QPoint) -> None:
        """Called when files/folders are dropped on tree: move if from this tree, else copy."""
        target = self._ref_drop_target_folder(viewport_pos)
        if not target or not target.is_dir():
            return
        root = self._root_path
        def _is_under_root(p: Path) -> bool:
            try:
                return root and p.resolve().is_relative_to(root.resolve())
            except (ValueError, OSError):
                return False
        valid = [Path(p) for p in paths if Path(p).exists()]
        use_move = root and valid and all(_is_under_root(p) for p in valid)
        if use_move:
            self.move_files_to_folder(paths, target)
        else:
            self.drop_files_to_folder(paths, target)

    def _on_ref_tree_context_menu(self, pos: QPoint) -> None:
        idx = self._tree.indexAt(pos)
        has_selection = idx.isValid()
        path = None
        if has_selection:
            path = self._path_from_tree_index(idx)
            if not path or not path.exists():
                has_selection = False
                path = None

        menu = QMenu(self._tree)
        _icon = lambda name: lucide_icon(name, size=16, color_hex=MONOS_COLORS["text_label"])
        _icon_red = lambda name: lucide_icon(name, size=16, color_hex=MONOS_COLORS.get("destructive", "#ef4444"))

        if not has_selection:
            open_folder_act = menu.addAction(_icon("folder-open"), "Open folder")
            new_folder_act = menu.addAction(_icon("folder-plus"), "New folder")
            menu.addSeparator()
            import_act = menu.addAction(_icon("upload"), "Import")
            action = menu.exec(self._tree.viewport().mapToGlobal(pos))
            if action is None:
                return
            if action == open_folder_act:
                self._ref_open_folder(self._root_path)
            elif action == new_folder_act:
                self._ref_new_folder(self._root_path)
            elif action == import_act:
                self.import_requested.emit()
            return

        open_act = menu.addAction(_icon("file"), "Open")
        open_folder_act = menu.addAction(_icon("folder-open"), "Open folder")
        new_folder_act = menu.addAction(_icon("folder-plus"), "New folder")
        rename_act = menu.addAction(_icon("copy"), "Rename")
        menu.addSeparator()
        delete_act = menu.addAction(_icon_red("x"), "Delete")
        menu.addSeparator()
        tags_submenu = menu.addMenu(_icon("tag"), "Tags")
        sel_rel_paths = self._selected_relative_paths()
        tag_actions: dict[str, QAction] = {}
        for tdef in self._tag_defs:
            tid = tdef["id"]
            act = tags_submenu.addAction(
                lucide_icon("tag-filled", size=14, color_hex=tdef["color"]),
                tdef["label"],
            )
            act.setCheckable(True)
            if sel_rel_paths:
                all_have = all(tid in get_tags_for_item(self._item_tags, rp) for rp in sel_rel_paths)
                act.setChecked(all_have)
            tag_actions[tid] = act
        tags_submenu.addSeparator()
        remove_tags_act = tags_submenu.addAction(_icon("tag"), "Remove all tags")
        menu.addSeparator()
        import_act = menu.addAction(_icon("upload"), "Import")
        action = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == remove_tags_act:
            self._remove_all_tags(sel_rel_paths)
            return
        for tid, tact in tag_actions.items():
            if action == tact:
                self._toggle_tag(sel_rel_paths, tid)
                return
        if action == open_act:
            self._ref_open_path(path)
        elif action == open_folder_act:
            self._ref_open_folder(path)
        elif action == new_folder_act:
            parent = path if path.is_dir() else path.parent
            self._ref_new_folder(parent)
        elif action == rename_act:
            self._tree.edit(idx)
        elif action == delete_act:
            self._ref_delete_path(path, idx)
        elif action == import_act:
            self.import_requested.emit()

    def _selected_relative_paths(self) -> list[str]:
        """Return relative paths (from project_guide root) for all selected items."""
        pg_root = self._project_guide_root
        if not pg_root:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for idx in self._tree.selectionModel().selectedIndexes():
            if idx.column() != 0:
                continue
            p = self._path_from_tree_index(idx)
            if p is None:
                continue
            try:
                rel = p.relative_to(pg_root).as_posix()
            except (ValueError, OSError):
                continue
            if rel and rel != "." and rel not in seen:
                seen.add(rel)
                out.append(rel)
        return out

    def _toggle_tag(self, relative_paths: list[str], tag_id: str) -> None:
        """Toggle a tag for the given items and refresh the view."""
        if not self._project_root or not relative_paths:
            return
        toggle_tag_for_items(self._project_root, self._item_tags, relative_paths, tag_id)
        self._tree.viewport().update()
        self.item_tags_changed.emit()

    def _remove_all_tags(self, relative_paths: list[str]) -> None:
        """Remove all tags from the given items."""
        if not self._project_root or not relative_paths:
            return
        for rel in relative_paths:
            set_tags_for_item(self._project_root, self._item_tags, rel, [])
        self._tree.viewport().update()
        self.item_tags_changed.emit()

    def _ref_open_path(self, path: Path) -> None:
        if path.is_dir():
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            except Exception:
                pass
        else:
            try:
                os.startfile(path.resolve())
            except (OSError, AttributeError):
                try:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
                except Exception:
                    pass

    def _ref_open_folder(self, path: Path | None) -> None:
        target = path if path and path.is_dir() else (path.parent if path else self._root_path)
        if target and target.is_dir():
            self.open_folder_requested.emit(target)

    def _ref_new_folder(self, parent: Path | None) -> None:
        if not parent or not parent.is_dir():
            return
        name, ok = QInputDialog.getText(self._tree, "New folder", "Folder name:", text="New folder")
        if not ok or not (name or "").strip():
            return
        name = (name or "").strip()
        new_path = parent / name
        if new_path.exists():
            QMessageBox.warning(self._tree, "New folder", f"A file or folder named '{name}' already exists.")
            return
        try:
            new_path.mkdir(parents=False)
            self._force_reload()
            QTimer.singleShot(100, self._sync_empty_tag_overlay)
        except OSError as e:
            QMessageBox.warning(self._tree, "New folder", f"Could not create folder: {e}")

    def _ref_delete_path(self, path: Path, index) -> None:
        name = path.name or str(path)
        if path.is_dir():
            msg = f'Delete folder "{name}" and all its contents?'
        else:
            msg = f'Delete file "{name}"?'
        if not ask_delete(self._tree, "Delete", msg):
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            self._force_reload()
            label = "Folder" if path.is_dir() else "File"
            notification_service.success(f"Deleted {label} \"{name}\".")
        except OSError as e:
            QMessageBox.warning(self._tree, "Delete", f"Could not delete: {e}")

    def _on_ref_tree_double_clicked(self, index) -> None:
        if not index.isValid():
            return
        path = self._path_from_tree_index(index)
        if path and path.is_file():
            try:
                os.startfile(path.resolve())
            except OSError:
                pass

    def _make_breadcrumb(self) -> QWidget:
        wrap = QWidget(self)
        wlay = QHBoxLayout(wrap)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(3)
        sep_style = "color: #71717a; font-size: 10px;"
        label_style = "color: #a1a1aa; font-size: 11px;"
        dept_text = (self._department_label or "").replace("_", " ").strip().title() or "Reference"
        for i, name in enumerate(["Project Guide", dept_text]):
            if i > 0:
                sep = QLabel("›", wrap)
                sep.setStyleSheet(sep_style)
                sep.setFont(monos_font("Inter", 10))
                wlay.addWidget(sep, 0)
            lb = QLabel(name, wrap)
            lb.setStyleSheet(label_style)
            lb.setFont(monos_font("Inter", 11))
            wlay.addWidget(lb, 0)
            if i == 1:
                self._dept_breadcrumb_label = lb
        return wrap

    def _emit_tree_selection(self) -> None:
        idx = self._tree.currentIndex()
        if not idx.isValid():
            self.tree_selection_changed.emit(None)
            return
        path = self._path_from_tree_index(idx)
        self.tree_selection_changed.emit(path)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_tree_stack"):
            r = self._tree_stack.rect()
            if hasattr(self, "_empty_tag_overlay"):
                self._empty_tag_overlay.setGeometry(r)
            if hasattr(self, "_empty_dept_overlay"):
                self._empty_dept_overlay.setGeometry(r)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:
        if obj is self._tree and event.type() == QEvent.Type.FocusIn:
            self._emit_tree_selection()
            return super().eventFilter(obj, event)
        if obj is self._tree.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.MiddleButton:
                    self._ref_middle_drag_start = event.position().toPoint()
                    return False
            if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.MiddleButton:
                    self._ref_middle_drag_start = None
                return False
            if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                if self._ref_middle_drag_start is not None and (event.buttons() & Qt.MouseButton.MiddleButton):
                    pos = event.position().toPoint()
                    if (pos - self._ref_middle_drag_start).manhattanLength() > 8:
                        paths = self._ref_get_drag_paths()
                        self._ref_middle_drag_start = None
                        if paths:
                            mime = QMimeData()
                            mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
                            drag = QDrag(self._tree.viewport())
                            drag.setMimeData(mime)
                            drag.exec(Qt.DropAction.MoveAction)
                        return True
                return False
        return super().eventFilter(obj, event)

    def _ref_get_drag_paths(self) -> list[Path]:
        """Paths to use for middle-mouse drag: selected items or item under cursor."""
        seen: set[str] = set()
        paths: list[Path] = []
        for idx in self._tree.selectionModel().selectedIndexes():
            if idx.column() != 0:
                continue
            p = self._path_from_tree_index(idx)
            if p is None:
                continue
            key = str(p.resolve())
            if key not in seen and p.exists():
                seen.add(key)
                paths.append(p)
        if not paths and self._ref_middle_drag_start is not None:
            idx = self._tree.indexAt(self._ref_middle_drag_start)
            if idx.isValid():
                p = self._path_from_tree_index(idx)
                if p and p.exists():
                    paths = [p]
        return paths

    def set_root(self, root_path: Path | None, department_label: str = "") -> None:
        self._root_path = Path(root_path) if root_path else None
        if department_label:
            self._department_label = department_label
        if getattr(self, "_dept_breadcrumb_label", None) is not None:
            self._dept_breadcrumb_label.setText(
                (self._department_label or "").replace("_", " ").strip().title() or "Reference"
            )
        self._apply_root_index()
