"""
Inbox pane widgets: InboxTreePane (breadcrumb + file tree for one date folder).
ReferenceTreePane and ProjectGuideTreePane for Project Guide page. Used by InboxPageWidget, ReferencePageWidget.
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


def _fs_model_index_for_path(model: QFileSystemModel, path: Path):
    try:
        native = QDir.toNativeSeparators(str(Path(path).resolve()))
    except OSError:
        native = QDir.toNativeSeparators(str(path))
    return model.index(native, 0)


def _proxy_map_to_source(
    index: QModelIndex,
    *,
    proxy: QSortFilterProxyModel | None,
    source_model: QFileSystemModel,
) -> QModelIndex:
    if not index.isValid():
        return index
    if proxy is None or index.model() is source_model:
        return index
    if index.model() is proxy:
        return proxy.mapToSource(index)
    return QModelIndex()


def _proxy_map_from_source(
    source_index: QModelIndex,
    *,
    proxy: QSortFilterProxyModel | None,
    source_model: QFileSystemModel,
) -> QModelIndex:
    if not source_index.isValid():
        return source_index
    if proxy is None or source_index.model() is proxy:
        return source_index
    if source_index.model() is source_model:
        return proxy.mapFromSource(source_index)
    return QModelIndex()

from PySide6.QtCore import QDir, QEvent, QFileInfo, QItemSelection, QItemSelectionModel, QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal, QTimer, QUrl
from PySide6.QtCore import QAbstractListModel, QModelIndex, QSettings
from PySide6.QtGui import (
    QAction,
    QAbstractFileIconProvider,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPixmap,
    QShortcut,
)
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileIconProvider,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
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
    paths_with_any_tag,
    normalize_tag_department_id,
    read_tag_definitions,
    set_tags_for_item,
    toggle_tag_for_items,
)
from monostudio.ui_qt.delete_confirm_dialog import ask_delete
from monostudio.ui_qt.explorer_thumbnail_loader import ExplorerThumbnailLoader
from monostudio.ui_qt.inbox_browse_bar import InboxBrowseBar
from monostudio.ui_qt.inbox_grid_card_paint import (
    grid_card_fallback_icon_px,
    paint_grid_card_border,
    paint_grid_card_fill,
    paint_grid_card_icon_band,
    paint_grid_card_labels,
    paint_grid_card_tag_badges,
)
from monostudio.ui_qt.inbox_list_row_paint import (
    _EXPLORER_ICON_SIZE,
    explorer_list_row_size_hint,
    explorer_path_stats,
    explorer_type_label,
    paint_explorer_grid_thumbnail,
    paint_explorer_list_row,
    paint_explorer_thumb_loading_spinner,
)
from monostudio.ui_qt.thumbnails import is_direct_media_preview_path
from monostudio.ui_qt.inbox_page_toolbar import InboxContentToolbar
from monostudio.ui_qt.explorer_drag_out import MiddleMouseDragTracker, collect_tree_drag_paths
from monostudio.ui_qt.external_drop import drop_wants_copy, paths_under_root
from monostudio.ui_qt.external_drop_host import (
    ExplorerDropZone,
    paint_explorer_drop_target_highlight,
    set_explorer_drop_highlight,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification import notify as notification_service
from monostudio.ui_qt.style import (
    FILE_TYPE_ICON_COLORS,
    MONOS_COLORS,
    breadcrumb_filter_icon_color,
    breadcrumb_filter_role,
    clear_stuck_widget_hover,
    monos_font,
    page_badge_accent_color,
)

_TREE_ICON_SIZE = 18


def _relative_paths_under_guide_root(
    paths: list[Path],
    guide_root: Path | None,
) -> list[str]:
    if not guide_root:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p is None or not p.exists():
            continue
        try:
            rel = p.relative_to(guide_root).as_posix()
        except (ValueError, OSError):
            continue
        if rel and rel != "." and rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


def _guide_tag_assignment_states(
    item_tags: dict[str, list[str]],
    relative_paths: list[str],
    tag_id: str,
) -> tuple[bool, bool]:
    """Return (all_assigned, any_assigned) for one tag on the current selection."""
    if not relative_paths:
        return False, False
    states = [tag_id in get_tags_for_item(item_tags, rp) for rp in relative_paths]
    return all(states), any(states)


class _GuideTagMenuRow(QWidget):
    """One tag row in the Project Guide context submenu (checkbox + icon + colored label)."""

    def __init__(
        self,
        *,
        label: str,
        color_hex: str,
        checked: bool,
        any_assigned: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GuideTagMenuRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 6, 28, 6)
        lay.setSpacing(8)

        check = QLabel(self)
        check.setFixedSize(14, 14)
        if checked:
            check.setStyleSheet(
                "background-color: #3b82f6; border: 1px solid #60a5fa; border-radius: 3px;"
            )
        else:
            check.setStyleSheet(
                "background-color: transparent; border: 1px solid #52525b; border-radius: 3px;"
            )

        icon_lbl = QLabel(self)
        icon_lbl.setPixmap(
            lucide_icon(
                "tag-filled" if any_assigned else "tag",
                size=14,
                color_hex=color_hex,
            ).pixmap(14, 14)
        )
        icon_lbl.setFixedSize(14, 14)

        text = QLabel(label, self)
        text.setObjectName("GuideTagMenuLabel")
        if any_assigned:
            text.setStyleSheet(
                f'color: {color_hex}; font-family: "Inter"; font-size: 13px; font-weight: 500;'
                " background: transparent;"
            )
        else:
            text.setStyleSheet(
                'color: #a1a1aa; font-family: "Inter"; font-size: 13px; font-weight: 500;'
                " background: transparent;"
            )

        lay.addWidget(check, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(text, 1, Qt.AlignmentFlag.AlignVCenter)


def _populate_guide_tags_submenu(
    submenu: QMenu,
    *,
    tag_defs: list[dict[str, str]],
    item_tags: dict[str, list[str]],
    relative_paths: list[str],
    menu_icon,
) -> tuple[dict[str, QAction], QAction | None]:
    tag_actions: dict[str, QAction] = {}
    for tdef in tag_defs:
        tid = tdef["id"]
        all_have, any_have = _guide_tag_assignment_states(item_tags, relative_paths, tid)
        wa = QWidgetAction(submenu)
        wa.setDefaultWidget(
            _GuideTagMenuRow(
                label=tdef["label"],
                color_hex=tdef["color"],
                checked=all_have,
                any_assigned=any_have,
            )
        )
        if any_have and not all_have:
            wa.setToolTip("Assigned to some selected items")
        submenu.addAction(wa)
        tag_actions[tid] = wa
    submenu.addSeparator()
    remove_act = submenu.addAction(menu_icon("tag"), "Remove all tags")
    return tag_actions, remove_act


def _find_project_root(start: Path) -> Path | None:
    try:
        p = start.resolve()
    except OSError:
        p = start
    for candidate in [p, *p.parents]:
        if (candidate / ".monostudio" / "project.json").is_file():
            return candidate
    return None


def _purge_inbox_outbox_meta(project_root: Path, deleted_path: Path) -> None:
    """Remove inbox/outbox meta keys for a deleted path and its children."""
    from monostudio.core.inbox_reader import (
        get_inbox_root,
        read_inbox_meta,
        write_inbox_meta,
    )
    from monostudio.core.internal_check_reader import (
        get_internal_check_root,
        read_internal_check_meta,
        write_internal_check_meta,
    )
    from monostudio.core.delivery_reader import (
        get_delivery_root,
        read_delivery_meta,
        write_delivery_meta,
    )

    try:
        resolved = deleted_path.resolve()
    except OSError:
        resolved = deleted_path

    for get_root, read_meta, write_meta in (
        (get_inbox_root, read_inbox_meta, write_inbox_meta),
        (get_internal_check_root, read_internal_check_meta, write_internal_check_meta),
        (get_delivery_root, read_delivery_meta, write_delivery_meta),
    ):
        try:
            root = get_root(project_root).resolve()
            rel = resolved.relative_to(root).as_posix()
        except (ValueError, OSError):
            continue
        meta = read_meta(project_root)
        if not isinstance(meta, dict):
            continue
        prefix = rel.replace("\\", "/")
        keys = [
            k
            for k in list(meta.keys())
            if k.replace("\\", "/") == prefix or k.replace("\\", "/").startswith(prefix + "/")
        ]
        if not keys:
            continue
        for k in keys:
            meta.pop(k, None)
        write_meta(project_root, meta)


def _external_drop_paths_from_mime(mime: QMimeData) -> list[Path]:
    paths: list[Path] = []
    for url in mime.urls():
        if url.isLocalFile():
            p = Path(url.toLocalFile())
            if p.exists():
                paths.append(p)
    return paths


def _accept_external_url_drag(event: QDragEnterEvent | QDropEvent) -> bool:
    if event.mimeData().hasUrls():
        event.acceptProposedAction()
        return True
    return False


def _handle_external_url_drag_move(event: QDragMoveEvent) -> bool:
    if event.mimeData().hasUrls():
        event.acceptProposedAction()
        return True
    return False

# Extension sets for file-type icons (lowercase with leading dot)
_EXT_IMAGE = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".ico", ".svg"})
_EXT_PUREF = frozenset({".pur"})  # PureRef → brand:pureref
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
    if ext in _EXT_PUREF:
        return ("brand:pureref", colors["dcc"])
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


def _tree_file_icon(name: str, color: str, *, size: int | None = None) -> "QIcon":
    """Tree/grid/list icon: brand:slug → brand_icon; else lucide_icon."""
    px = max(12, int(size if size is not None else _TREE_ICON_SIZE))
    if name.startswith("brand:"):
        from monostudio.ui_qt.brand_icons import brand_icon
        slug = name[6:]
        ic = brand_icon(slug, size=px, color_hex=color)
        return ic if not ic.isNull() else lucide_icon("box", size=px, color_hex=color)
    return lucide_icon(name, size=px, color_hex=color)


class _InboxFileSystemModel(QFileSystemModel):
    """Directories always reserve branch space (Explorer-style chevron before first expand)."""

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: N802
        if parent.isValid():
            try:
                if self.fileInfo(parent).isDir():
                    return True
            except (RuntimeError, TypeError):
                pass
        return super().hasChildren(parent)


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


_BRANCH_ICON_SIZE = 14


class _InboxTreeDelegate(QStyledItemDelegate):
    """Explorer-style list rows + branch chevron."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pane: "InboxTreePane | ReferenceTreePane | None" = None

    def set_pane(self, pane: "InboxTreePane | ReferenceTreePane") -> None:
        self._pane = pane

    def _thumb_loader(self) -> ExplorerThumbnailLoader | None:
        pane = self._pane
        if pane is None:
            return None
        return getattr(pane, "_explorer_thumb_loader", None)

    def _fs_model_and_index(self, index: QModelIndex) -> tuple[object | None, QModelIndex]:
        if not index.isValid():
            return None, index
        pane = self._pane
        if pane is not None:
            to_source = getattr(pane, "_to_source_index", None)
            if callable(to_source):
                src_idx = to_source(index)
                fs_model = getattr(pane, "_fs_model", None)
                if fs_model is not None and src_idx.isValid():
                    return fs_model, src_idx
                return None, index
        model = index.model()
        if model is None:
            return None, index
        if hasattr(model, "filePath"):
            return model, index
        map_to_source = getattr(model, "mapToSource", None)
        if callable(map_to_source) and index.model() is model:
            src_idx = map_to_source(index)
            src_model = model.sourceModel()
            if src_model is not None and hasattr(src_model, "filePath"):
                return src_model, src_idx
        return None, index

    def _path_for_delegate_index(self, index: QModelIndex) -> Path | None:
        if not index.isValid():
            return None
        fs_model, fs_index = self._fs_model_and_index(index)
        if fs_model is not None and fs_index.isValid():
            fp = fs_model.filePath(fs_index)
            if fp:
                return Path(fp)
        pane = self._pane
        if pane is not None:
            path_fn = getattr(pane, "_path_for_tree_index", None)
            if callable(path_fn):
                return path_fn(index)
        return None

    def _thumbnail_for(self, path: Path) -> QPixmap | None:
        loader = self._thumb_loader()
        if loader is None:
            return None
        return loader.get_or_request(path)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        if index.column() != 0:
            return super().sizeHint(option, index)
        return explorer_list_row_size_hint(option)

    def paint(self, painter: QPainter, option, index) -> None:
        if index.column() != 0:
            return
        row_rect = option.rect
        view = option.widget
        if view is None or not index.isValid():
            return
        path = self._path_for_delegate_index(index)
        if path is None or not path.exists():
            super().paint(painter, option, index)
        else:
            fs_model, fs_index = self._fs_model_and_index(index)
            is_dir = path.is_dir()
            has_branch = bool(
                is_dir
                and fs_model is not None
                and fs_index.isValid()
                and fs_model.hasChildren(fs_index)
            )

            icon_name, icon_color = _file_icon_spec(is_dir, path.suffix or "")
            file_icon = _tree_file_icon(icon_name, icon_color, size=_EXPLORER_ICON_SIZE)
            loader = self._thumb_loader()
            thumb = None
            thumb_loading = False
            loading_angle = 0.0
            if not is_dir and is_direct_media_preview_path(path):
                thumb = self._thumbnail_for(path)
                if thumb is None and loader is not None and loader.is_pending(path):
                    thumb_loading = True
                    loading_angle = loader.loading_angle
            date_label, size_label = explorer_path_stats(path)

            paint_explorer_list_row(
                painter,
                option,
                viewport_width=int(view.viewport().width()),
                name=path.name or str(path),
                type_label=explorer_type_label(path),
                date_label=date_label,
                size_label=size_label,
                icon=file_icon if thumb is None and not thumb_loading else None,
                thumbnail=thumb,
                loading=thumb_loading,
                loading_angle=loading_angle,
            )

            if has_branch:
                ind = view.indentation()
                branch_rect = QRect(row_rect.x() - ind, row_rect.y(), ind, row_rect.height())
                icon_name = "chevron-down" if view.isExpanded(index) else "chevron-right"
                chevron = lucide_icon(
                    icon_name, size=_BRANCH_ICON_SIZE, color_hex=MONOS_COLORS["text_label"]
                )
                painter.save()
                chevron.paint(painter, branch_rect, Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal)
                painter.restore()

        if self._pane is not None and self._pane.is_drop_hover_tree_index(index):
            full_w = max(int(view.viewport().width()), row_rect.width())
            hl_rect = QRect(0, row_rect.y(), full_w, row_rect.height())
            paint_explorer_drop_target_highlight(painter, hl_rect)

        if path is not None:
            from monostudio.ui_qt.link_reveal import link_reveal, paint_link_reveal_row_overlay

            lr = link_reveal()
            alpha = lr.alpha_for_path(path) if lr.current_alpha() > 0.01 else 0.0
            if alpha > 0:
                full_w = max(int(view.viewport().width()), row_rect.width())
                hl_rect = QRect(0, row_rect.y(), full_w, row_rect.height())
                paint_link_reveal_row_overlay(painter, hl_rect, alpha)


def _inbox_outbox_type_label(source_type: str) -> str:
    t = (source_type or "").strip()
    if not t:
        return ""
    return (t.replace("_", " ").title() or t).strip()


def _inbox_outbox_date_label(path: Path | None) -> str:
    if path is None:
        return ""
    name = (path.name or "").replace("_", " ").strip()
    return name.title() if name else str(path)


def _inbox_outbox_source_type_icon(source_type: str) -> str:
    t = (source_type or "").strip().lower()
    if t == "freelancer":
        return "user"
    if t == "client":
        return "package"
    return "folder"


def _set_lucide_on_label(label: QLabel, icon_name: str, *, size: int, color_hex: str) -> None:
    ic = lucide_icon(icon_name, size=size, color_hex=color_hex)
    if ic.isNull():
        label.clear()
        return
    label.setPixmap(ic.pixmap(size, size))


def _inbox_outbox_root_badge_kind(root_title: str) -> str:
    key = (root_title or "").strip().casefold()
    return {
        "inbox": "inbox",
        "internal check": "internal_check",
        "internal_check": "internal_check",
        "review": "internal_check",  # legacy nav title
        "outbox": "delivery",
        "delivery": "delivery",
        "project guide": "guide",
    }.get(key, "")


def _make_inbox_outbox_title_chevron(parent: QWidget) -> QLabel:
    lbl = QLabel(parent)
    lbl.setObjectName("MainViewTitleChevron")
    lbl.setFixedSize(16, 16)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    chev = lucide_icon("chevron-right", size=14, color_hex=MONOS_COLORS["text_label"])
    if not chev.isNull():
        lbl.setPixmap(chev.pixmap(14, 14))
    return lbl


class InboxOutboxTitleRow(QWidget):
    """Header breadcrumb for Inbox/Outbox: Root › Type › Date. Same chrome in list + tree; ancestors clickable in tree."""

    root_clicked = Signal()
    type_clicked = Signal()
    department_clicked = Signal()
    tag_filter_clicked = Signal(str)  # tag_id to remove from filter

    def __init__(
        self,
        root_title: str,
        *,
        root_icon: str = "inbox",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._root_title = (root_title or "Inbox").strip()
        self._root_icon = (root_icon or "inbox").strip()
        self._type_filter = ""
        self._date_path: Path | None = None
        self._in_tree = False
        self._unified_tree = False
        self._badge_role = "type"
        self._badge_label_override: str | None = None
        self._badge_icon_override: str | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._root_static = QWidget(self)
        self._root_static.setObjectName("MainViewTypeBadge")
        self._root_static.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._root_page_badge_kind = _inbox_outbox_root_badge_kind(self._root_title)
        self._root_static.setProperty("badgeKind", self._root_page_badge_kind)
        root_static_l = QHBoxLayout(self._root_static)
        root_static_l.setContentsMargins(8, 4, 10, 4)
        root_static_l.setSpacing(6)
        self._root_static_icon = QLabel(self._root_static)
        self._root_static_icon.setFixedSize(16, 16)
        self._root_static_icon.setScaledContents(False)
        self._root_label = QLabel(self._root_title.upper(), self._root_static)
        self._root_label.setObjectName("MainViewTypeBadgeLabel")
        self._root_label.setFont(monos_font("Inter", 13, QFont.Weight.Bold))
        root_static_l.addWidget(self._root_static_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        root_static_l.addWidget(self._root_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._root_static.installEventFilter(self)

        self._chevron_type = _make_inbox_outbox_title_chevron(self)
        self._chevron_type.hide()

        self._type_badge = QWidget(self)
        self._type_badge.setObjectName("MainViewFilterBadge")
        self._type_badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge_lay = QHBoxLayout(self._type_badge)
        badge_lay.setContentsMargins(8, 4, 10, 4)
        badge_lay.setSpacing(6)
        self._type_icon_label = QLabel(self._type_badge)
        self._type_icon_label.setFixedSize(16, 16)
        self._type_label = QLabel(self._type_badge)
        self._type_label.setObjectName("MainViewFilterBadgeLabel")
        self._type_label.setFont(monos_font("Inter", 13, QFont.Weight.Bold))
        badge_lay.addWidget(self._type_icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        badge_lay.addWidget(self._type_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._type_badge.hide()
        self._type_badge.installEventFilter(self)

        self._chevron_date = _make_inbox_outbox_title_chevron(self)
        self._chevron_date.hide()

        self._date_chip = QWidget(self)
        self._date_chip.setObjectName("MainViewFilterBadge")
        self._date_chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        date_lay = QHBoxLayout(self._date_chip)
        date_lay.setContentsMargins(8, 4, 10, 4)
        date_lay.setSpacing(6)
        self._date_icon = QLabel(self._date_chip)
        self._date_icon.setFixedSize(16, 16)
        self._date_label = QLabel(self._date_chip)
        self._date_label.setObjectName("MainViewFilterBadgeLabel")
        self._date_label.setFont(monos_font("Inter", 13, QFont.Weight.Bold))
        date_lay.addWidget(self._date_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        date_lay.addWidget(self._date_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._date_chip.hide()

        self._chevron_tag = _make_inbox_outbox_title_chevron(self)
        self._chevron_tag.hide()

        self._tag_badges_host = QWidget(self)
        self._tag_badges_host.setObjectName("MainViewTagFilterBadgesHost")
        self._tag_badges_layout = QHBoxLayout(self._tag_badges_host)
        self._tag_badges_layout.setContentsMargins(0, 0, 0, 0)
        self._tag_badges_layout.setSpacing(4)
        self._tag_filter_chips: list[QWidget] = []
        self._tag_badges_host.hide()

        lay.addWidget(self._root_static, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._chevron_type, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._type_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._chevron_tag, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._tag_badges_host, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._chevron_date, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._date_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self._apply_root_page_badge_style()

    def _apply_root_page_badge_style(self) -> None:
        self._root_static.setProperty("badgeKind", self._root_page_badge_kind)
        for w in (self._root_static, self._root_label):
            w.style().unpolish(w)
            w.style().polish(w)
        self._root_static.update()
        self._root_label.update()

    def filter_badge_widget(self) -> QWidget:
        return self._type_badge

    def tag_filter_badge_widget(self) -> QWidget:
        return self._tag_badges_host

    def badge_role(self) -> str:
        return self._badge_role

    def _apply_tag_chip_tint(self, chip: QWidget, color_hex: str) -> None:
        c = QColor(color_hex)
        bg = QColor(c.red(), c.green(), c.blue(), 72)
        border = QColor(c.red(), c.green(), c.blue(), 160)
        hover_bg = QColor(c.red(), c.green(), c.blue(), 110)
        chip.setStyleSheet(
            f"QWidget#MainViewTagFilterBadge {{"
            f" background-color: rgba({bg.red()}, {bg.green()}, {bg.blue()}, {bg.alpha()});"
            f" border: 1px solid rgba({border.red()}, {border.green()}, {border.blue()}, {border.alpha()});"
            f" border-radius: 6px;"
            f"}}"
            f"QWidget#MainViewTagFilterBadge[navLink=\"true\"]:hover {{"
            f" background-color: rgba({hover_bg.red()}, {hover_bg.green()}, {hover_bg.blue()}, {hover_bg.alpha()});"
            f"}}"
            f"QLabel#MainViewTagFilterBadgeLabel {{ color: #fafafa; font-weight: 700; }}"
        )

    def _set_tag_chip_icon(self, chip: QWidget, *, hovered: bool) -> None:
        icon_label = getattr(chip, "_tag_icon_label", None)
        color = getattr(chip, "_tag_color", "#a1a1aa")
        if not isinstance(icon_label, QLabel):
            return
        if hovered:
            _set_lucide_on_label(icon_label, "x", size=16, color_hex="#fafafa")
        else:
            _set_lucide_on_label(icon_label, "tag-filled", size=16, color_hex=color)

    def _build_tag_filter_chip(self, label: str, color_hex: str, tag_id: str = "") -> QWidget:
        chip = QWidget(self._tag_badges_host)
        chip.setObjectName("MainViewTagFilterBadge")
        chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        chip_lay = QHBoxLayout(chip)
        chip_lay.setContentsMargins(8, 4, 10, 4)
        chip_lay.setSpacing(6)
        icon_label = QLabel(chip)
        icon_label.setFixedSize(16, 16)
        text_label = QLabel((label or "").strip().upper(), chip)
        text_label.setObjectName("MainViewTagFilterBadgeLabel")
        text_label.setFont(monos_font("Inter", 13, QFont.Weight.Bold))
        chip_lay.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        chip_lay.addWidget(text_label, 0, Qt.AlignmentFlag.AlignVCenter)
        chip._tag_icon_label = icon_label  # type: ignore[attr-defined]
        chip._tag_color = color_hex  # type: ignore[attr-defined]
        chip._tag_id = (tag_id or "").strip()  # type: ignore[attr-defined]
        self._apply_tag_chip_tint(chip, color_hex)
        self._set_tag_chip_icon(chip, hovered=False)
        self._set_nav_link(chip, True, tooltip="Remove this tag filter")
        chip.setMouseTracking(True)
        chip.installEventFilter(self)
        return chip

    def _clear_tag_filter_chips(self) -> None:
        for chip in self._tag_filter_chips:
            chip.removeEventFilter(self)
            chip.setParent(None)
            chip.deleteLater()
        self._tag_filter_chips.clear()
        while self._tag_badges_layout.count():
            item = self._tag_badges_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

    def set_tag_filter_badges(self, badges: list[tuple[str, str, str]]) -> None:
        """Show one breadcrumb chip per active tag filter (label, color_hex, tag_id)."""
        self._clear_tag_filter_chips()
        cleaned: list[tuple[str, str, str]] = []
        for item in badges:
            if len(item) < 2:
                continue
            label = (item[0] or "").strip()
            color = (item[1] or "").strip()
            tag_id = (item[2] or "").strip() if len(item) > 2 else ""
            if label and color:
                cleaned.append((label, color, tag_id))
        if not cleaned:
            self._chevron_tag.hide()
            self._tag_badges_host.hide()
            return
        for label, color_hex, tag_id in cleaned:
            chip = self._build_tag_filter_chip(label, color_hex, tag_id)
            self._tag_badges_layout.addWidget(chip)
            self._tag_filter_chips.append(chip)
        self._chevron_tag.setVisible(self._type_badge.isVisible())
        self._tag_badges_host.show()

    def set_tag_filter_badge(self, label: str | None, *, color_hex: str | None = None, tag_id: str = "") -> None:
        text = (label or "").strip()
        if not text:
            self.set_tag_filter_badges([])
            return
        self.set_tag_filter_badges([(text, (color_hex or "").strip() or "#a1a1aa", (tag_id or "").strip())])

    def _apply_filter_chip_style(
        self,
        badge: QWidget,
        label: QLabel,
        *,
        badge_role: str = "type",
        segment: str = "filter",
    ) -> None:
        badge.setObjectName("MainViewFilterBadge")
        label.setObjectName("MainViewFilterBadgeLabel")
        badge.setProperty("badgeKind", "")
        badge.setProperty(
            "filterRole",
            breadcrumb_filter_role(
                self._root_page_badge_kind,
                badge_role=badge_role,
                segment=segment,
            ),
        )
        for w in (badge, label):
            w.style().unpolish(w)
            w.style().polish(w)
        badge.update()
        label.update()

    def _apply_badge_role_style(self) -> None:
        self._apply_filter_chip_style(
            self._type_badge,
            self._type_label,
            badge_role=self._badge_role,
        )

    def _set_nav_link(self, widget: QWidget, enabled: bool, *, tooltip: str = "") -> None:
        widget.setProperty("navLink", "true" if enabled else "false")
        widget.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        widget.setToolTip(tooltip if enabled else "")
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def set_context(
        self,
        *,
        type_filter: str,
        date_path: Path | None = None,
        unified_tree: bool = False,
        badge_role: str = "type",
        badge_label: str | None = None,
        badge_icon: str | None = None,
    ) -> None:
        self._type_filter = (type_filter or "").strip().lower()
        self._date_path = Path(date_path) if date_path else None
        self._unified_tree = bool(unified_tree)
        self._in_tree = self._unified_tree or self._date_path is not None
        self._badge_role = "department" if badge_role == "department" else "type"
        self._badge_label_override = (badge_label or "").strip() or None
        self._badge_icon_override = (badge_icon or "").strip() or None
        type_text = self._badge_label_override or _inbox_outbox_type_label(self._type_filter)
        has_type = bool(type_text)
        filter_role = breadcrumb_filter_role(
            self._root_page_badge_kind,
            badge_role=self._badge_role,
        )
        badge_icon_color = breadcrumb_filter_icon_color(filter_role)
        root_icon_color = page_badge_accent_color(self._root_page_badge_kind)

        _set_lucide_on_label(
            self._root_static_icon, self._root_icon, size=16, color_hex=root_icon_color
        )

        self._chevron_type.setVisible(has_type)
        if has_type:
            if self._badge_icon_override:
                type_icon_name = self._badge_icon_override
            elif self._badge_role == "department":
                type_icon_name = "layers"
            else:
                type_icon_name = _inbox_outbox_source_type_icon(self._type_filter)
            _set_lucide_on_label(
                self._type_icon_label, type_icon_name, size=16, color_hex=badge_icon_color
            )
            self._type_label.setText(type_text.upper())

        self._type_badge.setVisible(has_type)
        if has_type:
            self._apply_badge_role_style()

        filter_tooltip = (
            "Change department filter"
            if self._badge_role == "department"
            else "Change source filter"
        )
        if unified_tree:
            self._set_nav_link(self._type_badge, has_type, tooltip=filter_tooltip)
            self._set_nav_link(self._root_static, False)
            self._chevron_date.setVisible(False)
            self._date_chip.setVisible(False)
            return

        # Legacy: type pill clickable when inside a date-folder tree drill-down.
        self._set_nav_link(
            self._type_badge,
            self._in_tree and has_type,
            tooltip="Back to source list" if self._in_tree and has_type else "",
        )
        self._set_nav_link(
            self._root_static,
            self._in_tree,
            tooltip="Back to inbox root" if self._in_tree else "",
        )

        show_date = self._in_tree and self._date_path is not None
        self._chevron_date.setVisible(show_date)
        self._date_chip.setVisible(show_date)
        if show_date:
            date_filter_role = breadcrumb_filter_role(
                self._root_page_badge_kind,
                segment="date",
            )
            _set_lucide_on_label(
                self._date_icon,
                "calendar",
                size=16,
                color_hex=breadcrumb_filter_icon_color(date_filter_role),
            )
            self._date_label.setText(_inbox_outbox_date_label(self._date_path))
            self._apply_filter_chip_style(
                self._date_chip,
                self._date_label,
                segment="date",
            )

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj in self._tag_filter_chips:
            et = event.type()
            if et == QEvent.Type.Enter:
                self._set_tag_chip_icon(obj, hovered=True)
            elif et in (QEvent.Type.Leave, QEvent.Type.Hide):
                self._set_tag_chip_icon(obj, hovered=False)
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if obj is self._type_badge and self._type_badge.isVisible():
                nav = self._type_badge.property("navLink")
                if nav == "true" or nav is True:
                    if self._unified_tree:
                        if self._badge_role == "department":
                            self.department_clicked.emit()
                        else:
                            self.type_clicked.emit()
                        QTimer.singleShot(0, lambda: clear_stuck_widget_hover(self._type_badge))
                        return True
                    if self._in_tree:
                        self.type_clicked.emit()
                        return True
            if obj in self._tag_filter_chips and obj.isVisible():
                nav = obj.property("navLink")
                tag_id = str(getattr(obj, "_tag_id", "") or "").strip()
                if (nav == "true" or nav is True) and tag_id:
                    self.tag_filter_clicked.emit(tag_id)
                    return True
            if (
                obj is self._root_static
                and self._in_tree
                and not self._unified_tree
            ):
                self.root_clicked.emit()
                return True
        return super().eventFilter(obj, event)


_FILE_CARD_W = 200
_FILE_CARD_RADIUS = 10
_FILE_GRID_GAP = 20
_FILE_GRID_THUMB_TEXT_GAP = 10
_FILE_GRID_LABEL_PAD_H = 8
_FILE_GRID_LABEL_PAD_BOTTOM = 8
# 16:9 thumb (full card width) + text band below.
_FILE_CARD_H = (_FILE_CARD_W * 9) // 16 + _FILE_GRID_THUMB_TEXT_GAP + 58


def _file_card_thumb_height(card_width: int) -> int:
    return max(1, int(card_width) * 9 // 16)


class _InboxFileEntry:
    __slots__ = ("label", "path", "meta", "icon_name", "icon_color")

    def __init__(self, label: str, path: Path, *, meta: str, icon_name: str, icon_color: str) -> None:
        self.label = label
        self.path = path
        self.meta = meta
        self.icon_name = icon_name
        self.icon_color = icon_color


class _InboxFileListModel(QAbstractListModel):
    RoleMeta = Qt.ItemDataRole.UserRole + 1
    RoleIconName = Qt.ItemDataRole.UserRole + 2
    RoleIconColor = Qt.ItemDataRole.UserRole + 3

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[_InboxFileEntry] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or index.row() >= len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return entry.label if role == Qt.ItemDataRole.DisplayRole else f"{entry.label}\n{entry.meta}"
        if role == self.RoleMeta:
            return entry.meta
        if role == self.RoleIconName:
            return entry.icon_name
        if role == self.RoleIconColor:
            return entry.icon_color
        if role == Qt.ItemDataRole.UserRole:
            return str(entry.path)
        return None

    def set_entries(self, entries: list[_InboxFileEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entry_at(self, row: int) -> _InboxFileEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None


class _InboxFileGridDelegate(QStyledItemDelegate):
    def __init__(self, *, view: QListView, parent=None) -> None:
        super().__init__(parent)
        self._pane: "InboxTreePane | None" = None
        self._tag_pixmap_cache: dict[str, QPixmap] = {}
        self._card_size = QSize(_FILE_CARD_W, _FILE_CARD_H)
        self._c_bg = MONOS_COLORS.get("card_bg", "#191b1e")
        self._c_hover = MONOS_COLORS.get("card_hover", "#1d1f23")
        self._c_border = MONOS_COLORS.get("border", "#27272a")
        self._c_text = MONOS_COLORS.get("text_primary", "#fafafa")
        self._c_muted = MONOS_COLORS.get("text_label", "#a1a1aa")
        self._c_meta = MONOS_COLORS.get("text_meta", "#71717a")
        self._c_selected = "#3b82f6"

    def set_pane(self, pane: InboxTreePane) -> None:
        self._pane = pane

    def set_card_size(self, size: QSize) -> None:
        self._card_size = size

    def _thumbnail_for(self, path: Path) -> QPixmap | None:
        pane = self._pane
        if pane is None:
            return None
        loader = getattr(pane, "_explorer_thumb_loader", None)
        if loader is None:
            return None
        return loader.get_or_request(path)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(self._card_size.width() + _FILE_GRID_GAP, self._card_size.height() + _FILE_GRID_GAP)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        if not index.isValid():
            return
        label = index.data(Qt.ItemDataRole.DisplayRole) or ""
        meta = index.data(_InboxFileListModel.RoleMeta) or ""
        icon_name = index.data(_InboxFileListModel.RoleIconName) or "file"
        icon_color = index.data(_InboxFileListModel.RoleIconColor) or self._c_muted
        path_str = index.data(Qt.ItemDataRole.UserRole)
        path = Path(path_str) if path_str else None
        rect = option.rect.adjusted(_FILE_GRID_GAP // 2, _FILE_GRID_GAP // 2, -_FILE_GRID_GAP // 2, -_FILE_GRID_GAP // 2)
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
            border_px = 2 if selected else 1
            outer = rect
            inner = outer.adjusted(border_px, border_px, -border_px, -border_px)
            inner_radius = max(0, _FILE_CARD_RADIUS - border_px)
            paint_grid_card_fill(p, outer, bg=bg, radius=_FILE_CARD_RADIUS)

            loader = getattr(self._pane, "_explorer_thumb_loader", None) if self._pane else None
            is_media = path is not None and is_direct_media_preview_path(path)
            thumb_pix = self._thumbnail_for(path) if is_media else None
            thumb_loading = bool(
                is_media
                and (thumb_pix is None or thumb_pix.isNull())
                and loader is not None
                and loader.is_pending(path)
            )
            thumb_h = _file_card_thumb_height(inner.width())
            thumb_rect = QRect(inner.left(), inner.top(), inner.width(), thumb_h)
            fallback_icon_px = grid_card_fallback_icon_px(thumb_rect)
            file_icon = _tree_file_icon(
                str(icon_name), str(icon_color), size=fallback_icon_px
            )
            content_clip = QPainterPath()
            content_clip.addRoundedRect(QRectF(inner), inner_radius, inner_radius)
            p.save()
            p.setClipPath(content_clip)
            p.fillRect(thumb_rect, QColor("#27272a"))
            if thumb_pix is not None and not thumb_pix.isNull():
                paint_explorer_grid_thumbnail(p, thumb_rect, thumb_pix)
            elif thumb_loading:
                paint_explorer_thumb_loading_spinner(
                    p, thumb_rect, angle=loader.loading_angle if loader else 0.0
                )
            else:
                paint_grid_card_icon_band(
                    p,
                    thumb_rect,
                    file_icon,
                    icon_px=fallback_icon_px,
                )
            if path is not None and self._pane is not None:
                tag_fn = getattr(self._pane, "tags_for_guide_path", None)
                color_map = getattr(self._pane, "_tag_color_map", None)
                if callable(tag_fn) and isinstance(color_map, dict):
                    tag_ids = tag_fn(path)
                    if tag_ids:
                        paint_grid_card_tag_badges(
                            p,
                            thumb_rect,
                            tag_ids,
                            color_map,
                            pixmap_cache=self._tag_pixmap_cache,
                        )
            paint_grid_card_labels(
                p,
                inner,
                title=str(label),
                meta=str(meta) if meta else "",
                title_color=title_color,
                meta_color=meta_color,
                icon_band_h=thumb_h,
                title_px=12,
                band_from_card_top=True,
                band_gap_after=_FILE_GRID_THUMB_TEXT_GAP,
                label_pad_h=_FILE_GRID_LABEL_PAD_H,
                label_pad_bottom=_FILE_GRID_LABEL_PAD_BOTTOM,
            )
            p.restore()

            if self._pane is not None and self._pane.is_drop_hover_grid_row(index.row()):
                paint_explorer_drop_target_highlight(p, outer)
            paint_grid_card_border(
                p,
                outer,
                border=border,
                radius=_FILE_CARD_RADIUS,
                width=border_px,
            )
            if path is not None:
                from monostudio.ui_qt.link_reveal import link_reveal, paint_link_reveal_card_border

                lr = link_reveal()
                alpha = lr.alpha_for_path(path) if lr.current_alpha() > 0.01 else 0.0
                if alpha > 0:
                    paint_link_reveal_card_border(p, outer, alpha, radius=_FILE_CARD_RADIUS)
        finally:
            p.restore()


def _make_inbox_file_entry(child: Path, *, meta_root: Path | None = None) -> _InboxFileEntry:
    icon_name, icon_color = _file_icon_spec(child.is_dir(), child.suffix or "")
    if child.is_dir():
        files, folders = 0, 0
        try:
            for sub in child.iterdir():
                if sub.name.startswith("."):
                    continue
                if sub.is_dir():
                    folders += 1
                elif sub.is_file():
                    files += 1
        except OSError:
            pass
        bits = []
        if files:
            bits.append(f"{files} file{'s' if files != 1 else ''}")
        if folders:
            bits.append(f"{folders} folder{'s' if folders != 1 else ''}")
        meta = " · ".join(bits) if bits else "Folder"
    else:
        meta = (child.suffix or "file").lstrip(".").upper() or "File"
        if meta_root is not None:
            try:
                rel_parent = child.parent.relative_to(meta_root).as_posix()
                if rel_parent and rel_parent != ".":
                    meta = rel_parent
            except (ValueError, OSError):
                pass
    return _InboxFileEntry(
        child.name,
        child,
        meta=meta,
        icon_name=icon_name,
        icon_color=icon_color,
    )


def _collect_inbox_file_entries(root: Path) -> list[_InboxFileEntry]:
    entries: list[_InboxFileEntry] = []
    if not root.is_dir():
        return entries
    children: list[Path] = []
    try:
        for child in root.iterdir():
            if child.name.startswith("."):
                continue
            children.append(child)
    except OSError:
        return entries
    children.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    for child in children:
        entries.append(_make_inbox_file_entry(child))
    return entries


def _collect_tag_filtered_file_entries(
    browse_root: Path,
    guide_root: Path,
    tagged_rels: set[str],
) -> list[_InboxFileEntry]:
    """Flat list of tagged items under *browse_root* (files + tagged folders)."""
    try:
        browse = browse_root.resolve()
        guide = guide_root.resolve()
    except OSError:
        browse, guide = browse_root, guide_root
    if not browse.is_dir() or not tagged_rels:
        return []
    out: list[_InboxFileEntry] = []
    seen: set[str] = set()
    for rel in sorted(tagged_rels):
        if not rel or rel in seen:
            continue
        full = guide / rel
        try:
            full_res = full.resolve()
        except OSError:
            full_res = full
        if not full_res.exists():
            continue
        try:
            full_res.relative_to(browse)
        except ValueError:
            continue
        seen.add(rel)
        out.append(_make_inbox_file_entry(full_res, meta_root=browse))
    out.sort(key=lambda e: (not e.path.is_dir(), e.label.lower()))
    return out


def inbox_tree_selection_hint_text(mode: str, count: int) -> str | None:
    """Footer hint for explorer tree selection; None when hint bar should hide."""
    if count <= 0:
        return None
    noun = "item" if count == 1 else "items"
    if (mode or "inbox").strip().lower() == "inbox":
        return f"{count} {noun} selected — choose destination in Inspector, then Distribute"
    return f"{count} {noun} selected"


class InboxTreePane(QWidget):
    """Breadcrumb + file tree for one date folder. Emits back_requested, tree_selection_changed, open_folder_requested, import_requested, history_requested (if show_history_action)."""

    back_requested = Signal()
    tree_selection_changed = Signal(object)  # Path | None
    open_folder_requested = Signal(object)  # Path (date folder)
    import_requested = Signal()
    history_requested = Signal()
    selection_count_changed = Signal(int)
    browse_path_changed = Signal(object)  # Path
    external_drop_requested = Signal(object, object, bool)  # list[Path], target folder, copy_only
    video_preview_requested = Signal(object)  # Path
    send_to_delivery_requested = Signal(object)  # list[Path]
    copy_link_requested = Signal(str, object)  # page name, Path

    def __init__(
        self,
        date_folder_path: Path,
        parent=None,
        *,
        show_history_action: bool = False,
        show_toolbar: bool = False,
        view_settings_key: str = "inbox/view_mode",
        source_filter: str = "",
        breadcrumb_title: str = "Inbox",
        show_breadcrumb: bool = False,
        allow_root_drop: bool = False,
        storage_root_override: Path | None = None,
        selection_hint_mode: str = "inbox",
    ) -> None:
        super().__init__(parent)
        self._drop_hover_tree_index = QModelIndex()
        self._drop_hover_path: Path | None = None
        self._drop_hover_grid_row: int | None = None
        self._date_folder_path = Path(date_folder_path)
        self._grid_browse_root = Path(date_folder_path)
        self._show_history_action = show_history_action
        self._show_toolbar = show_toolbar
        self._view_settings_key = view_settings_key
        self._source_filter = (source_filter or "").strip().lower()
        self._breadcrumb_title = breadcrumb_title or "Inbox"
        self._selection_hint_mode = (selection_hint_mode or "inbox").strip().lower()
        self._allow_root_drop = bool(allow_root_drop)
        self._storage_root_override = (
            Path(storage_root_override).resolve() if storage_root_override else None
        )
        self._view_mode = "tile"
        self._grid_sync_scheduled = False
        self._grid_last: tuple[int, int, int] | None = None
        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._content_toolbar = None
        self._view_stack = None
        self._file_grid = None
        self._file_model = None
        self._file_grid_delegate = None
        self._hint_bar = None
        self._hint_label = None
        self._browse_bar = None
        self._nav_history: list[Path] = [Path(date_folder_path)]
        self._nav_index = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if show_breadcrumb:
            bar = QWidget(self)
            bar_lay = QHBoxLayout(bar)
            bar_lay.setContentsMargins(12, 8, 12, 8)
            bar_lay.setSpacing(8)
            bar_lay.addWidget(self._make_breadcrumb(), 0)
            bar_lay.addStretch(1)
            lay.addWidget(bar, 0)

        if show_toolbar:
            self._content_toolbar = InboxContentToolbar(self)
            self._content_toolbar.set_sort_settings_key(f"{view_settings_key}/sort")
            self._content_toolbar.view_mode_changed.connect(self._on_view_mode_changed)
            self._content_toolbar.sort_changed.connect(self._on_explorer_sort_changed)
        else:
            self._content_toolbar = None

        tree_host = QWidget(self)
        self._tree_host = tree_host
        tree_host_lay = QVBoxLayout(tree_host)
        tree_host_lay.setContentsMargins(0, 0, 0, 0)
        tree_host_lay.setSpacing(0)

        self._fs_model = _InboxFileSystemModel(self)
        self._fs_model.setRootPath("")
        self._fs_model.setIconProvider(_LucideFileIconProvider())
        self._tree = QTreeView(tree_host)
        self._tree.setObjectName("InboxSplitTree")
        self._tree.setModel(self._fs_model)
        self._tree.setSelectionMode(QTreeView.ExtendedSelection)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(20)
        self._tree.setUniformRowHeights(True)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.hideColumn(1)
        self._tree.hideColumn(2)
        self._tree.hideColumn(3)
        self._explorer_thumb_loader = ExplorerThumbnailLoader(self)
        self._tree_delegate = _InboxTreeDelegate(self._tree)
        self._tree.setItemDelegate(self._tree_delegate)
        self._explorer_thumb_loader.register_view(self._tree)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.selectionModel().selectionChanged.connect(self._on_tree_selection_changed)
        self._tree.doubleClicked.connect(self._on_tree_double_clicked)

        if show_toolbar:
            self._view_stack = QStackedWidget(tree_host)
            self._view_stack.addWidget(self._tree)

            self._file_grid = QListView(tree_host)
            self._file_grid.setObjectName("MainViewGrid")
            self._file_grid.setViewMode(QListView.ViewMode.IconMode)
            self._file_grid.setResizeMode(QListView.ResizeMode.Adjust)
            self._file_grid.setMovement(QListView.Movement.Static)
            self._file_grid.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
            self._file_grid.setUniformItemSizes(True)
            self._file_grid.setSpacing(0)
            self._file_grid.setMouseTracking(True)
            self._file_model = _InboxFileListModel(self._file_grid)
            self._file_grid.setModel(self._file_model)
            self._file_grid_delegate = _InboxFileGridDelegate(view=self._file_grid, parent=self._file_grid)
            self._file_grid_delegate.set_pane(self)
            self._file_grid.setItemDelegate(self._file_grid_delegate)
            self._explorer_thumb_loader.register_view(self._file_grid)
            grid_sm = self._file_grid.selectionModel()
            grid_sm.selectionChanged.connect(self._on_tree_selection_changed)
            grid_sm.currentChanged.connect(self._on_tree_selection_changed)
            self._file_grid.doubleClicked.connect(self._on_file_grid_double_clicked)
            self._file_grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._file_grid.customContextMenuRequested.connect(self._on_file_grid_context_menu)
            self._file_grid.viewport().installEventFilter(self)
            self._file_grid.installEventFilter(self)
            self._view_stack.addWidget(self._file_grid)
            tree_host_lay.addWidget(self._view_stack, 1)
        else:
            self._view_stack = None
            self._file_grid = None
            self._file_model = None
            tree_host_lay.addWidget(self._tree, 1)

        self._empty_overlay = QWidget(tree_host)
        self._empty_overlay.setObjectName("InboxTreeEmptyOverlay")
        self._empty_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        empty_lay = QVBoxLayout(self._empty_overlay)
        empty_lay.setContentsMargins(24, 48, 24, 48)
        empty_lay.setSpacing(12)
        empty_lay.addStretch(2)
        empty_icon = QLabel(self._empty_overlay)
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon.setPixmap(
            lucide_icon("folder-open", size=48, color_hex=MONOS_COLORS.get("text_meta", "#71717a")).pixmap(48, 48)
        )
        empty_lay.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_title = QLabel("This folder is empty", self._empty_overlay)
        empty_title.setObjectName("InboxEmptyStateTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setFont(monos_font("Inter", 14, QFont.Weight.DemiBold))
        empty_lay.addWidget(empty_title, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_sub = QLabel("Import files or drag and drop here", self._empty_overlay)
        empty_sub.setObjectName("InboxEmptyStateSubtitle")
        empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_sub.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        empty_lay.addWidget(empty_sub, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_lay.addStretch(3)
        self._empty_overlay.setVisible(False)
        self._empty_overlay.setParent(tree_host)
        self._empty_overlay.raise_()

        lay.addWidget(tree_host, 1)

        if show_toolbar:
            self._hint_bar = QWidget(self)
            self._hint_bar.setObjectName("InboxSelectionHintBar")
            self._hint_bar.setAttribute(Qt.WA_StyledBackground, True)
            hint_lay = QHBoxLayout(self._hint_bar)
            hint_lay.setContentsMargins(16, 10, 16, 10)
            hint_lay.setSpacing(8)
            hint_icon = QLabel(self._hint_bar)
            hint_icon.setFixedSize(16, 16)
            hint_icon.setPixmap(
                lucide_icon("arrow-right", size=14, color_hex="#60a5fa").pixmap(14, 14)
            )
            hint_lay.addWidget(hint_icon, 0, Qt.AlignmentFlag.AlignVCenter)
            self._hint_label = QLabel("", self._hint_bar)
            self._hint_label.setObjectName("InboxSelectionHintText")
            hint_lay.addWidget(self._hint_label, 1)
            self._hint_bar.setVisible(False)
            lay.addWidget(self._hint_bar, 0)

            self._browse_bar = InboxBrowseBar(self)
            self._browse_bar.set_handlers(on_back=self._nav_back, on_forward=self._nav_forward)
            self._browse_bar.navigate_requested.connect(self._navigate_to)

            self._load_view_mode()
            self._reload_file_entries()
            self._sync_content_toolbar()
        else:
            self._hint_bar = None
            self._hint_label = None
            self._browse_bar = None

        self._tree.installEventFilter(self)
        self._tree.viewport().installEventFilter(self)
        from monostudio.ui_qt.link_reveal import link_reveal

        link_reveal().changed.connect(self._on_link_reveal_tick)
        self._middle_drag = MiddleMouseDragTracker()
        self._tree_delegate.set_pane(self)
        self._explorer_drop = ExplorerDropZone(
            self,
            highlight_widget=tree_host,
            on_drop=self._on_explorer_drop,
            on_drag_hover=self._sync_drop_hover_from_event,
            on_drag_leave=self._clear_drop_hover,
            is_internal_drag=self._is_internal_storage_drag,
        )
        drop_hosts: list[QWidget] = [tree_host, self._tree, self._tree.viewport()]
        if self._view_stack is not None:
            drop_hosts.append(self._view_stack)
        if self._file_grid is not None:
            drop_hosts.extend([self._file_grid, self._file_grid.viewport()])
        # Pane itself accepts too — empty chrome / layout gaps still get Explorer drops
        # (frameless Windows walks to the nearest acceptDrops ancestor).
        drop_hosts.append(self)
        self._explorer_drop.mount(*drop_hosts)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._reload_fs_tree_root()
        QTimer.singleShot(0, self._sync_empty_overlay)

    def explorer_path_bar(self) -> InboxBrowseBar | None:
        return getattr(self, "_browse_bar", None)

    def explorer_toolbar(self) -> InboxContentToolbar | None:
        return self._content_toolbar

    def sync_hotkey_tooltips(self, settings) -> None:
        if self._content_toolbar is not None:
            self._content_toolbar.sync_hotkey_tooltips(settings)

    def set_chrome_context(self, source_filter: str, date_path: Path | None = None) -> None:
        self._source_filter = (source_filter or "").strip().lower()
        if date_path is not None:
            self._date_folder_path = Path(date_path)
            self._grid_browse_root = Path(date_path)
            self._nav_history = [Path(date_path)]
            self._nav_index = 0
        self._sync_content_toolbar()

    def _load_view_mode(self) -> None:
        raw = self._settings.value(self._view_settings_key, "tile")
        mode = str(raw).strip().lower()
        if mode not in ("tile", "list"):
            mode = "tile"
        self._apply_view_mode(mode, save=False)

    def _on_view_mode_changed(self, mode: str) -> None:
        self._apply_view_mode(mode, save=True)

    def cycle_view_mode(self) -> None:
        next_mode = "list" if self._view_mode == "tile" else "tile"
        self._apply_view_mode(next_mode, save=True)

    def _apply_view_mode(self, mode: str, *, save: bool) -> None:
        if mode not in ("tile", "list"):
            return
        self._view_mode = mode
        if self._content_toolbar is not None:
            self._content_toolbar.set_view_mode(mode)
        if self._view_stack is not None:
            self._view_stack.setCurrentIndex(0 if mode == "list" else 1)
        if save:
            self._settings.setValue(self._view_settings_key, mode)
        if mode == "tile":
            self._grid_last = None
            self._schedule_file_grid_sync()
        self._on_tree_selection_changed()

    def _grid_browse_root_path(self) -> Path:
        return getattr(self, "_grid_browse_root", self._date_folder_path)

    def _grid_browse_subtitle(self) -> str:
        browse = self._grid_browse_root_path().resolve()
        date_root = self._date_folder_path.resolve()
        try:
            rel = browse.relative_to(date_root)
            return " › ".join(rel.parts) if rel.parts else ""
        except ValueError:
            return ""

    def _sync_content_toolbar(self) -> None:
        if self._content_toolbar is None:
            return
        if getattr(self, "_selection_hint_mode", "inbox") == "inbox":
            hint = "Double-click folder to browse · Double-click file to open · Select files to distribute"
        else:
            hint = "Double-click folder to browse · Double-click file to open"
        self._content_toolbar.set_context(hint=hint, show_toggle=True)
        self._sync_browse_bar()

    def _sync_browse_bar(self) -> None:
        if self._browse_bar is None:
            return
        self._browse_bar.set_state(
            date_root=self._date_folder_path,
            current=self._grid_browse_root_path(),
            can_back=self._nav_index > 0,
            can_forward=self._nav_index < len(self._nav_history) - 1,
        )

    def _file_entries_for_browse_root(self, root: Path) -> list[_InboxFileEntry]:
        return self._sorted_file_entries(_collect_inbox_file_entries(root))

    def _sorted_file_entries(self, entries: list[_InboxFileEntry]) -> list[_InboxFileEntry]:
        toolbar = self._content_toolbar
        if toolbar is None:
            return entries
        from monostudio.ui_qt.explorer_file_sort import sort_explorer_file_entries

        return list(
            sort_explorer_file_entries(
                entries,
                field=toolbar.sort_field(),
                ascending=toolbar.sort_ascending(),
            )
        )

    def _on_explorer_sort_changed(self, _field: str, _ascending: bool) -> None:
        self._reload_file_entries()

    def _apply_browse_root(self, path: Path) -> None:
        self._grid_browse_root = Path(path)
        if self._file_model is not None:
            self._file_model.set_entries(self._file_entries_for_browse_root(self._grid_browse_root_path()))
        self._sync_content_toolbar()
        self._sync_tree_to_browse_root()
        if self._view_mode == "tile":
            self._grid_last = None
            self._schedule_file_grid_sync()
        if self._file_grid is not None:
            self._file_grid.clearSelection()
            self._on_tree_selection_changed()
        QTimer.singleShot(0, self._sync_empty_overlay)
        self.browse_path_changed.emit(self._grid_browse_root_path())

    def _navigate_to(self, path: Path) -> None:
        path = Path(path)
        if not path.is_dir():
            return
        try:
            path.resolve().relative_to(self._date_folder_path.resolve())
        except ValueError:
            path = Path(self._date_folder_path)
        try:
            if self._grid_browse_root_path().resolve() == path.resolve():
                self._sync_browse_bar()
                return
        except OSError:
            if self._grid_browse_root_path() == path:
                self._sync_browse_bar()
                return
        self._nav_history = self._nav_history[: self._nav_index + 1]
        self._nav_history.append(path)
        self._nav_index = len(self._nav_history) - 1
        self._apply_browse_root(path)

    def _nav_back(self) -> None:
        if self._nav_index <= 0:
            return
        self._nav_index -= 1
        self._apply_browse_root(self._nav_history[self._nav_index])

    def _nav_forward(self) -> None:
        if self._nav_index >= len(self._nav_history) - 1:
            return
        self._nav_index += 1
        self._apply_browse_root(self._nav_history[self._nav_index])

    def _reload_file_entries(self) -> None:
        if self._file_model is None:
            return
        self._file_model.set_entries(self._file_entries_for_browse_root(self._grid_browse_root_path()))
        self._sync_content_toolbar()
        if self._view_mode == "tile":
            self._grid_last = None
            self._schedule_file_grid_sync()
        QTimer.singleShot(0, self._sync_empty_overlay)

    def _schedule_file_grid_sync(self) -> None:
        if self._grid_sync_scheduled or self._file_grid is None:
            return
        self._grid_sync_scheduled = True
        QTimer.singleShot(0, self._sync_file_grid_layout)

    def _sync_file_grid_layout(self) -> None:
        self._grid_sync_scheduled = False
        if self._view_mode != "tile" or self._file_grid is None:
            return
        try:
            vw = int(self._file_grid.viewport().width())
        except Exception:
            return
        if vw <= 0:
            return
        inner_w = max(1, vw - 32)
        cell_w = _FILE_CARD_W + _FILE_GRID_GAP
        sig = (max(1, (inner_w + _FILE_GRID_GAP) // cell_w), _FILE_CARD_W, _FILE_CARD_H)
        if self._grid_last == sig:
            return
        self._grid_last = sig
        self._file_grid.setGridSize(QSize(cell_w, _FILE_CARD_H + _FILE_GRID_GAP))
        self._file_grid_delegate.set_card_size(QSize(_FILE_CARD_W, _FILE_CARD_H))

    def _on_tree_selection_changed(self) -> None:
        self._emit_tree_selection()
        count = len(self.get_selected_paths())
        self.selection_count_changed.emit(count)
        if self._hint_label is not None and self._hint_bar is not None:
            hint_text = inbox_tree_selection_hint_text(
                getattr(self, "_selection_hint_mode", "inbox"),
                count,
            )
            if hint_text:
                self._hint_label.setText(hint_text)
                self._hint_bar.setVisible(True)
            else:
                self._hint_bar.setVisible(False)

    def _sync_empty_overlay(self) -> None:
        if not hasattr(self, "_empty_overlay"):
            return
        if self._show_toolbar and self._view_mode == "tile":
            has_children = bool(self._file_entries_for_browse_root(self._grid_browse_root_path()))
        else:
            root_idx = self._tree.rootIndex()
            has_children = root_idx.isValid() and self._tree.model().rowCount(root_idx) > 0
        self._empty_overlay.setVisible(not has_children)
        if not has_children:
            self._empty_overlay.setGeometry(self._tree_host.rect())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_empty_overlay") and self._empty_overlay.isVisible():
            self._empty_overlay.setGeometry(self._tree_host.rect())
        if self._view_mode == "tile" and getattr(self, "_file_grid", None) is not None:
            self._schedule_file_grid_sync()

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

    def emit_tree_selection(self) -> None:
        """Re-emit current explorer selection (tree or tile grid)."""
        self._emit_tree_selection()

    def _emit_tree_selection(self) -> None:
        if self._show_toolbar and self._view_mode == "tile" and self._file_grid is not None:
            sm = self._file_grid.selectionModel()
            indexes = sm.selectedIndexes() if sm is not None else []
            if not indexes and sm is not None:
                current = sm.currentIndex()
                if current.isValid():
                    indexes = [current]
            if not indexes:
                self.tree_selection_changed.emit(None)
                return
            entry = self._file_model.entry_at(indexes[0].row()) if self._file_model is not None else None
            self.tree_selection_changed.emit(entry.path if entry is not None else None)
            return
        sm = self._tree.selectionModel()
        idx = QModelIndex()
        if sm is not None:
            selected = sm.selectedIndexes()
            if selected:
                idx = selected[0]
            else:
                idx = self._tree.currentIndex()
        else:
            idx = self._tree.currentIndex()
        if not idx.isValid():
            self.tree_selection_changed.emit(None)
            return
        path = self._path_for_tree_index(idx)
        self.tree_selection_changed.emit(path)

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        idx = self._tree.indexAt(pos)
        path = None
        tree_index = None
        if idx.isValid():
            candidate = self._path_for_tree_index(idx)
            if candidate.exists():
                path = candidate
                tree_index = idx
        self._show_file_context_menu(pos, self._tree.viewport(), path, tree_index=tree_index)

    def _on_file_grid_context_menu(self, pos: QPoint) -> None:
        if self._file_grid is None:
            return
        idx = self._file_grid.indexAt(pos)
        path = None
        if idx.isValid() and self._file_model is not None:
            entry = self._file_model.entry_at(idx.row())
            if entry is not None and entry.path.exists():
                path = entry.path
        self._show_file_context_menu(pos, self._file_grid.viewport(), path, tree_index=None)

    def _supports_project_guide_tags(self) -> bool:
        return False

    def _context_menu_targets(self, path: Path | None) -> list[Path]:
        """Right-click on selection → all selected; otherwise the clicked item only."""
        if path is None:
            return []
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        selected = self.get_selected_paths()
        if not selected:
            return [path]
        sel_keys = set()
        for p in selected:
            try:
                sel_keys.add(str(p.resolve()))
            except OSError:
                sel_keys.add(str(p))
        if key in sel_keys:
            return selected
        return [path]

    def _show_file_context_menu(
        self,
        pos: QPoint,
        viewport: QWidget,
        path: Path | None,
        *,
        tree_index=None,
    ) -> None:
        menu = QMenu(viewport)
        icon = lambda name: lucide_icon(name, size=16, color_hex=MONOS_COLORS["text_label"])
        icon_red = lambda name: lucide_icon(name, size=16, color_hex=MONOS_COLORS.get("destructive", "#ef4444"))
        targets = self._context_menu_targets(path)

        if path is None:
            sort_section = None
            if self._show_toolbar and self._content_toolbar is not None:
                from monostudio.ui_qt.explorer_file_sort import (
                    add_explorer_sort_submenu,
                    resolve_explorer_sort_action,
                )

                sort_section = add_explorer_sort_submenu(
                    menu,
                    field=self._content_toolbar.sort_field(),
                    ascending=self._content_toolbar.sort_ascending(),
                    sort_icon=icon("sliders-horizontal"),
                )
                menu.addSeparator()
            open_folder_act = menu.addAction(icon("folder-open"), "Open folder")
            import_act = menu.addAction(icon("upload"), "Import")
            if self._show_history_action:
                menu.addSeparator()
                history_act = menu.addAction(icon("layers"), "History")
            else:
                history_act = None
            action = menu.exec(viewport.mapToGlobal(pos))
            if action is None:
                return
            if sort_section is not None and self._content_toolbar is not None:
                resolved = resolve_explorer_sort_action(
                    action,
                    sort_section,
                    field=self._content_toolbar.sort_field(),
                    ascending=self._content_toolbar.sort_ascending(),
                )
                if resolved is not None:
                    new_field, new_asc = resolved
                    self._content_toolbar.apply_sort_choice(new_field, new_asc)
                    return
            if action == open_folder_act:
                target = self._grid_browse_root_path() if self._view_mode == "tile" else self._date_folder_path
                self.open_folder_requested.emit(target)
            elif action == import_act:
                self.import_requested.emit()
            elif action == history_act and self._show_history_action:
                self.history_requested.emit()
            return

        multi = len(targets) > 1
        browse_act = None
        if not multi and path.is_dir():
            browse_act = menu.addAction(icon("folder"), "Browse")
        open_act = None
        open_folder_act = None
        if not multi:
            open_act = menu.addAction(icon("file"), "Open")
            open_folder_act = menu.addAction(icon("folder-open"), "Open folder")
        copy_link_act = menu.addAction(icon("link"), "Copy MONOS Link")
        rename_act = None
        if not multi and tree_index is not None:
            rename_act = menu.addAction(icon("copy"), "Rename")
        send_delivery_act = None
        if (
            getattr(self, "_selection_hint_mode", "inbox") == "internal_check"
            and targets
        ):
            send_delivery_act = menu.addAction(icon("send"), "Send to Delivery…")
        menu.addSeparator()
        delete_label = f"Delete {len(targets)} items" if multi else "Delete"
        delete_act = menu.addAction(icon_red("x"), delete_label)
        tag_actions: dict[str, QAction] = {}
        remove_tags_act = None
        sel_rel_paths: list[str] = []
        if self._supports_project_guide_tags():
            menu.addSeparator()
            tags_submenu = menu.addMenu(icon("tag"), "Tags")
            sel_rel_paths = self._relative_paths_for_guide_targets(targets)
            tag_actions, remove_tags_act = _populate_guide_tags_submenu(
                tags_submenu,
                tag_defs=getattr(self, "_tag_defs", DEFAULT_TAG_DEFINITIONS),
                item_tags=self._item_tags,
                relative_paths=sel_rel_paths,
                menu_icon=icon,
            )
        menu.addSeparator()
        import_act = menu.addAction(icon("upload"), "Import")
        if self._show_history_action:
            menu.addSeparator()
            history_act = menu.addAction(icon("layers"), "History")
        else:
            history_act = None

        action = menu.exec(viewport.mapToGlobal(pos))
        if action is None:
            return
        if remove_tags_act is not None and action == remove_tags_act:
            self._remove_all_tags(sel_rel_paths)
            return
        for tid, tact in tag_actions.items():
            if action == tact:
                self._toggle_tag(sel_rel_paths, tid)
                return
        if action == browse_act and path.is_dir():
            self._browse_into_folder(path)
        elif action == open_act:
            self._tree_open_path(path)
        elif action == open_folder_act:
            self._tree_open_folder(path)
        elif action == copy_link_act and path is not None:
            self.copy_link_requested.emit(self._page_name_for_copy_link(), path)
        elif action == rename_act and tree_index is not None:
            self._tree.edit(tree_index)
        elif action == send_delivery_act:
            self.send_to_delivery_requested.emit(targets)
        elif action == delete_act:
            self._tree_delete_paths(targets)
        elif action == import_act:
            self.import_requested.emit()
        elif action == history_act and self._show_history_action:
            self.history_requested.emit()

    def _page_name_for_copy_link(self) -> str:
        if isinstance(self, ProjectGuideTreePane):
            return "Project Guide"
        mode = getattr(self, "_selection_hint_mode", "inbox")
        if mode == "delivery":
            return "Delivery"
        if mode == "internal_check":
            return "Internal check"
        return "Inbox"

    def _tree_open_path(self, path: Path) -> None:
        """Open file with default app or folder in explorer."""
        if path.is_dir():
            try:
                from monostudio.core.shell_open import open_folder as shell_open_folder

                shell_open_folder(path)
            except Exception:
                pass
        else:
            from monostudio.ui_qt.thumbnails import is_video_preview_path

            if is_video_preview_path(path):
                self.video_preview_requested.emit(path)
                return
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

    def _tree_delete_paths(self, paths: list[Path]) -> None:
        """Delete one or more files/folders after confirmation."""
        targets = [p for p in paths if p is not None]
        if not targets:
            return
        if len(targets) == 1:
            path = targets[0]
            name = path.name or str(path)
            if path.is_dir():
                msg = f"Delete folder \"{name}\" and all its contents?"
            else:
                msg = f"Delete file \"{name}\"?"
        else:
            folders = sum(1 for p in targets if p.is_dir())
            files = len(targets) - folders
            parts: list[str] = []
            if files:
                parts.append(f"{files} file{'s' if files != 1 else ''}")
            if folders:
                parts.append(f"{folders} folder{'s' if folders != 1 else ''}")
            summary = " and ".join(parts) if parts else f"{len(targets)} items"
            msg = f"Delete {summary}?"
        if not ask_delete(self._tree, "Delete", msg):
            return

        project_root = _find_project_root(self._date_folder_path)
        deleted = 0
        deleted_labels: list[tuple[str, bool]] = []
        errors: list[str] = []
        for path in targets:
            try:
                if not path.exists():
                    continue
                name = path.name or str(path)
                is_dir = path.is_dir()
                if is_dir:
                    shutil.rmtree(path)
                else:
                    path.unlink()
                if project_root is not None:
                    _purge_inbox_outbox_meta(project_root, path)
                deleted += 1
                deleted_labels.append((name, is_dir))
            except OSError as e:
                errors.append(f"{path.name}: {e}")

        if deleted > 0:
            self._reload_fs_tree_root()
            if deleted == 1:
                name, is_dir = deleted_labels[0]
                kind = "folder" if is_dir else "file"
                notification_service.operational_success(f'Deleted {kind} "{name}".')
            else:
                notification_service.operational_success(f"Deleted {deleted} items.")
            QTimer.singleShot(0, self._sync_empty_overlay)
            self._reload_file_entries()
            self._emit_tree_selection()
        if errors:
            QMessageBox.warning(
                self._tree,
                "Delete",
                "Some items could not be deleted:\n" + "\n".join(errors[:8]),
            )

    def _browse_into_folder(self, path: Path) -> None:
        self._navigate_to(path)

    def _expand_tree_index_only(self, index) -> None:
        """Reveal index in tree without changing selection."""
        if not index.isValid():
            return
        src = self._to_source_index(index)
        root_src = self._to_source_index(self._tree.rootIndex())
        parent = src.parent()
        while parent.isValid() and parent != root_src:
            if self._fs_model.canFetchMore(parent):
                self._fs_model.fetchMore(parent)
            proxy_parent = self._to_tree_index(parent)
            if proxy_parent.isValid() and not self._tree.isExpanded(proxy_parent):
                self._tree.expand(proxy_parent)
            parent = parent.parent()
        if self._fs_model.canFetchMore(src):
            self._fs_model.fetchMore(src)
        if self._fs_model.isDir(src):
            self._tree.expand(index)

    def _fetch_and_expand_tree_index(self, index) -> None:
        self._expand_tree_index_only(index)
        self._tree.setCurrentIndex(index)
        self._tree.scrollTo(index)

    def _on_tree_double_clicked(self, index) -> None:
        if not index.isValid():
            return
        path = self._path_for_tree_index(index)
        if path is None:
            return
        if path.is_dir():
            if self._show_toolbar and self._view_mode == "tile":
                self._browse_into_folder(path)
            else:
                # List tree: expand branch + sync path bar (no Explorer on folder double-click).
                self._fetch_and_expand_tree_index(index)
                if self._show_toolbar:
                    self._navigate_to(path)
            return
        self._tree_open_path(path)

    def _on_file_grid_double_clicked(self, index) -> None:
        if not index.isValid() or self._file_model is None:
            return
        entry = self._file_model.entry_at(index.row())
        if entry is None:
            return
        if entry.path.is_dir():
            self._browse_into_folder(entry.path)
            return
        self._tree_open_path(entry.path)

    def _grid_browse_up(self) -> bool:
        root = self._grid_browse_root_path().resolve()
        date_root = self._date_folder_path.resolve()
        if root == date_root:
            return False
        parent = root.parent
        if not parent.exists():
            return False
        try:
            parent.resolve().relative_to(date_root)
            target = parent
        except ValueError:
            target = Path(self._date_folder_path)
        self._navigate_to(target)
        return True

    def _sync_tree_to_browse_root(self) -> None:
        browse = self._grid_browse_root_path().resolve()
        date_root = self._date_folder_path.resolve()
        root_src = self._fs_model.index(str(date_root))
        root_tree = self._to_tree_index(root_src)
        if not root_tree.isValid():
            return
        if browse == date_root:
            self._tree.setCurrentIndex(root_tree)
            return
        try:
            rel = browse.relative_to(date_root)
        except ValueError:
            return
        current_src = root_src
        for part in rel.parts:
            found = False
            for r in range(self._fs_model.rowCount(current_src)):
                src_idx = self._fs_model.index(r, 0, current_src)
                if Path(self._fs_model.filePath(src_idx)).name == part:
                    tree_idx = self._to_tree_index(src_idx)
                    if tree_idx.isValid():
                        self._tree.expand(tree_idx)
                    current_src = src_idx
                    found = True
                    break
            if not found:
                break
        final_tree = self._to_tree_index(current_src)
        if final_tree.isValid():
            self._tree.setCurrentIndex(final_tree)
            self._tree.scrollTo(final_tree)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if (
            self._show_toolbar
            and self._view_mode == "tile"
            and event.key() == Qt.Key.Key_Backspace
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            if self._grid_browse_up():
                event.accept()
                return
        super().keyPressEvent(event)

    def _source_tree_root(self) -> Path:
        return Path(self._date_folder_path).resolve()

    def _to_tree_index(self, source_index: QModelIndex) -> QModelIndex:
        return source_index

    def _to_source_index(self, tree_index: QModelIndex) -> QModelIndex:
        return tree_index

    def _reload_fs_tree_root(self) -> None:
        path = self._date_folder_path
        if path and path.is_dir():
            root_str = str(path.resolve())
            self._fs_model.setRootPath("")
            self._fs_model.setRootPath(root_str)
            src = self._fs_model.index(root_str)
            if src.isValid():
                tree_idx = self._to_tree_index(src)
                if tree_idx.isValid():
                    self._tree.setRootIndex(tree_idx)
        else:
            self._fs_model.setRootPath("")
            empty = self._to_tree_index(self._fs_model.index(""))
            if empty.isValid():
                self._tree.setRootIndex(empty)

    def _storage_root(self) -> Path | None:
        if self._storage_root_override is not None:
            return self._storage_root_override
        project_root = _find_project_root(self._date_folder_path)
        if project_root is None:
            return None
        from monostudio.core.inbox_reader import get_inbox_root
        from monostudio.core.internal_check_reader import get_internal_check_root
        from monostudio.core.delivery_reader import get_delivery_root

        key = str(self._view_settings_key)
        if key.startswith("internal_check"):
            return get_internal_check_root(project_root)
        if key.startswith("delivery"):
            return get_delivery_root(project_root)
        return get_inbox_root(project_root)

    def _is_internal_storage_drag(self, paths: list[Path]) -> bool:
        root = self._storage_root()
        return root is not None and paths_under_root(paths, root)

    def _path_for_tree_index(self, index: QModelIndex) -> Path | None:
        if not index.isValid():
            return None
        src = self._to_source_index(index)
        fp = self._fs_model.filePath(src)
        return Path(fp) if fp else None

    def is_drop_hover_tree_index(self, index: QModelIndex) -> bool:
        if self._drop_hover_path is None or not index.isValid():
            return False
        row_path = self._path_for_tree_index(index)
        if row_path is None:
            return False
        try:
            return row_path.resolve() == self._drop_hover_path.resolve()
        except OSError:
            return row_path == self._drop_hover_path

    def is_drop_hover_grid_row(self, row: int) -> bool:
        return self._drop_hover_grid_row is not None and row == self._drop_hover_grid_row

    def _tree_index_at_global(self, global_pos: QPoint) -> QModelIndex:
        return self._tree.indexAt(self._tree.viewport().mapFromGlobal(global_pos))

    def _grid_row_at_global(self, global_pos: QPoint) -> int | None:
        if not (self._show_toolbar and self._view_mode == "tile" and self._file_grid is not None):
            return None
        grid_vp = self._file_grid.viewport()
        pos = grid_vp.mapFromGlobal(global_pos)
        if not grid_vp.rect().contains(pos):
            return None
        idx = self._file_grid.indexAt(pos)
        return idx.row() if idx.isValid() else None

    def _sync_drop_hover_from_event(self, event: QDragEnterEvent | QDragMoveEvent) -> None:
        gpos = ExplorerDropZone.event_global_pos(event, self)
        if self._show_toolbar and self._view_mode == "tile" and self._file_grid is not None:
            self._drop_hover_path = None
            new_grid_row = self._grid_row_at_global(gpos)
            self._drop_hover_grid_row = self._grid_row_at_global(gpos)
            self._file_grid.viewport().update()
            return

        self._drop_hover_grid_row = None
        new_path: Path | None = None
        tree_idx = self._tree_index_at_global(gpos)
        if tree_idx.isValid():
            new_path = self._path_for_tree_index(tree_idx)
        if new_path is None:
            try:
                new_path = self._grid_browse_root_path().resolve()
            except OSError:
                new_path = None
        self._drop_hover_path = new_path
        self._drop_hover_tree_index = tree_idx
        self._tree.viewport().update()

    def _clear_drop_hover(self) -> None:
        self._drop_hover_tree_index = QModelIndex()
        self._drop_hover_path = None
        self._drop_hover_grid_row = None
        self._tree.viewport().update()
        if self._file_grid is not None:
            self._file_grid.viewport().update()

    def can_drop_directly_to(self, target: Path | None) -> bool:
        """True when target is inside the browse root (Inbox: date subfolder; Project Guide: dept root)."""
        if target is None:
            return False
        try:
            rel = Path(target).resolve().relative_to(self._source_tree_root())
        except (ValueError, OSError):
            return False
        if self._allow_root_drop:
            return True
        return len(rel.parts) >= 1

    def resolve_drop_dest_dir(self, target: Path | None) -> Path | None:
        if not self.can_drop_directly_to(target):
            return None
        path = Path(target).resolve()
        if path.is_dir():
            return path
        if path.exists():
            return path.parent.resolve()
        return None

    def drop_target_at_global_pos(self, global_pos: QPoint) -> Path | None:
        gpos = global_pos
        if (
            self._show_toolbar
            and self._view_mode == "tile"
            and self._file_grid is not None
        ):
            grid_vp = self._file_grid.viewport()
            pos = grid_vp.mapFromGlobal(gpos)
            if grid_vp.rect().contains(pos):
                idx = self._file_grid.indexAt(pos)
                if idx.isValid() and self._file_model is not None:
                    entry = self._file_model.entry_at(idx.row())
                    if entry is not None:
                        if entry.path.is_dir():
                            return entry.path
                        return entry.path.parent
                return self._grid_browse_root_path()
        viewport = self._tree.viewport()
        vp_pos = viewport.mapFromGlobal(gpos)
        idx = self._tree.indexAt(vp_pos)
        if idx.isValid():
            path = self._path_for_tree_index(idx)
            if path is not None:
                if path.is_dir():
                    return path
                return path.parent
        if self._show_toolbar:
            return self._grid_browse_root_path()
        return Path(self._date_folder_path)

    def drop_target_for_event(self, event: QDropEvent) -> Path | None:
        return self.drop_target_at_global_pos(ExplorerDropZone.event_global_pos(event, self))

    def _on_explorer_drop(self, paths: list[Path], event: QDropEvent) -> None:
        try:
            target = self.drop_target_for_event(event)
            copy_only = drop_wants_copy(event, paths=paths, storage_root=self._storage_root())
            self.external_drop_requested.emit(paths, target, copy_only)
        except Exception:
            _log_ref.exception("Explorer drop on inbox/outbox tree failed")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._explorer_drop.handle_drag_enter(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._explorer_drop.handle_drag_move(event):
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._explorer_drop.handle_drag_leave()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._explorer_drop.handle_drop(event):
            return
        super().dropEvent(event)

    def _resolve_tree_drag_paths(self, anchor_pos: QPoint | None) -> list[Path]:
        if self._show_toolbar and self._view_mode == "tile" and self._file_grid is not None:
            paths = self.get_selected_paths()
            if not paths and anchor_pos is not None:
                idx = self._file_grid.indexAt(anchor_pos)
                if idx.isValid() and self._file_model is not None:
                    entry = self._file_model.entry_at(idx.row())
                    if entry is not None and entry.path.exists():
                        paths = [entry.path]
            return paths
        sm = self._tree.selectionModel()
        selected = sm.selectedIndexes() if sm is not None else []
        return collect_tree_drag_paths(
            selected_indexes=selected,
            path_for_index=self._path_for_tree_index,
            anchor_pos=anchor_pos,
            index_at=self._tree.indexAt,
        )

    def _handle_middle_drag_event(self, obj: QWidget, event: QEvent, *, source: QWidget, resolve_paths) -> bool | None:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            self._middle_drag.on_mouse_press(event)
            return False
        if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            self._middle_drag.on_mouse_release(event)
            return False
        if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if self._middle_drag.try_start_drag(
                event,
                source=source,
                resolve_paths=resolve_paths,
            ):
                return True
            return False
        return None

    def _primary_selected_file_path(self) -> Path | None:
        if self._show_toolbar and self._view_mode == "tile" and self._file_grid is not None:
            sm = self._file_grid.selectionModel()
            idx = QModelIndex()
            if sm is not None:
                selected = sm.selectedIndexes()
                idx = selected[0] if selected else sm.currentIndex()
            if idx.isValid() and self._file_model is not None:
                entry = self._file_model.entry_at(idx.row())
                return entry.path if entry is not None else None
            return None
        sm = self._tree.selectionModel()
        idx = QModelIndex()
        if sm is not None:
            selected = sm.selectedIndexes()
            idx = selected[0] if selected else self._tree.currentIndex()
        else:
            idx = self._tree.currentIndex()
        if not idx.isValid():
            return None
        return self._path_for_tree_index(idx)

    def _open_video_if_selected(self) -> bool:
        from monostudio.ui_qt.thumbnails import is_video_preview_path

        path = self._primary_selected_file_path()
        if path is None or not path.is_file() or not is_video_preview_path(path):
            return False
        self.video_preview_requested.emit(path)
        return True

    def _delete_selected_paths(self) -> bool:
        paths = self.get_selected_paths()
        if not paths:
            return False
        self._tree_delete_paths(paths)
        return True

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:
        if obj is self._tree.viewport():
            handled = self._handle_middle_drag_event(
                obj,
                event,
                source=self._tree.viewport(),
                resolve_paths=self._resolve_tree_drag_paths,
            )
            if handled is not None:
                return handled
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                if self._open_video_if_selected():
                    return True
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Delete
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                if self._delete_selected_paths():
                    return True
        file_grid = getattr(self, "_file_grid", None)
        if file_grid is not None and obj is file_grid.viewport():
            handled = self._handle_middle_drag_event(
                obj,
                event,
                source=file_grid.viewport(),
                resolve_paths=self._resolve_tree_drag_paths,
            )
            if handled is not None:
                return handled
        if file_grid is not None and obj in (file_grid, file_grid.viewport()):
            if event.type() == QEvent.Type.Resize:
                self._schedule_file_grid_sync()
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                if self._open_video_if_selected():
                    return True
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Delete
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                if self._delete_selected_paths():
                    return True
            if (
                event.type() == QEvent.Type.KeyPress
                and self._view_mode == "tile"
                and event.key() == Qt.Key.Key_Backspace
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                if self._grid_browse_up():
                    return True
        if obj is self._tree:
            if event.type() == QEvent.Type.FocusIn:
                self._emit_tree_selection()
            elif (
                event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                if self._open_video_if_selected():
                    return True
            elif (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Delete
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                if self._delete_selected_paths():
                    return True
        return super().eventFilter(obj, event)

    def _on_link_reveal_tick(self) -> None:
        from monostudio.ui_qt.link_reveal import link_reveal

        if not self.isVisible():
            self._link_reveal_row = None
            return
        lr = link_reveal()
        if not lr.is_active():
            self._link_reveal_row = None
            return
        path = lr.any_active_path()
        if path is None:
            self._link_reveal_row = None
            return
        path = Path(path)
        if self._show_toolbar and self._view_mode == "tile" and self._file_grid is not None and self._file_model is not None:
            rows = self._file_model.rowCount()

            def _update_grid_row(row: int) -> bool:
                if row < 0 or row >= rows:
                    return False
                entry = self._file_model.entry_at(row)
                if entry is None or not lr.matches_path(entry.path):
                    return False
                self._link_reveal_row = row
                idx = self._file_model.index(row, 0)
                if idx.isValid():
                    self._file_grid.viewport().update(self._file_grid.visualRect(idx))
                return True

            cached = getattr(self, "_link_reveal_row", None)
            if cached is not None and _update_grid_row(int(cached)):
                return
            for row in range(rows):
                if _update_grid_row(row):
                    return
            self._link_reveal_row = None
            return
        try:
            src_idx = self._fs_model.index(str(path), 0)
            if not src_idx.isValid():
                src_idx = self._fs_model.index(str(path.resolve()), 0)
        except OSError:
            src_idx = self._fs_model.index(str(path), 0)
        if not src_idx.isValid():
            return
        tree_idx = self._to_tree_index(src_idx)
        if tree_idx.isValid():
            self._tree.viewport().update(self._tree.visualRect(tree_idx))

    def date_folder_path(self) -> Path:
        return self._date_folder_path

    def current_browse_path(self) -> Path:
        return self._grid_browse_root_path()

    def refresh_content(self) -> None:
        self._reload_fs_tree_root()
        self._reload_file_entries()
        QTimer.singleShot(0, self._sync_empty_overlay)

    def refresh_content_responsive(self, worker_manager, *, on_pump=None) -> None:
        """Reload explorer content; scan file grid on a worker while the UI event loop keeps running."""
        from monostudio.ui_qt.ui_worker_loop import run_worker_blocking_ui

        if on_pump is not None:
            on_pump()
        self._reload_fs_tree_root()
        if on_pump is not None:
            on_pump()
        self._reload_file_entries_responsive(worker_manager, run_worker_blocking_ui, on_pump=on_pump)
        QTimer.singleShot(0, self._sync_empty_overlay)

    def _reload_file_entries_responsive(self, worker_manager, run_blocking_ui, *, on_pump=None) -> None:
        if self._file_model is None:
            return
        root = self._grid_browse_root_path()
        entries, error = run_blocking_ui(
            worker_manager,
            f"explorer_entries_{id(self)}",
            lambda: self._file_entries_for_browse_root(root),
            on_pump=on_pump,
        )
        if error:
            _log_ref.warning("explorer file entries failed: %s", error)
            entries = []
        self._file_model.set_entries(entries or [])
        self._sync_content_toolbar()
        if self._view_mode == "tile":
            self._grid_last = None
            self._schedule_file_grid_sync()

    def select_dropped_paths(self, paths: list[Path]) -> None:
        """Reveal and multi-select items in the tree after a drop (deferred until model reload)."""
        resolved: list[Path] = []
        for raw in paths:
            try:
                p = Path(raw).resolve()
            except OSError:
                continue
            if p.exists():
                resolved.append(p)
        if not resolved:
            return
        self._pending_drop_selection = list(resolved)
        QTimer.singleShot(120, self._apply_pending_drop_selection)

    def _apply_pending_drop_selection(self) -> None:
        paths = getattr(self, "_pending_drop_selection", None)
        self._pending_drop_selection = None
        if not paths:
            self._drop_selection_retries = 0
            return
        try:
            unresolved = self._apply_dropped_path_selection(paths)
        except Exception:
            _log_ref.debug("Could not select paths after drop", exc_info=True)
            self._drop_selection_retries = 0
            return
        if unresolved:
            retries = getattr(self, "_drop_selection_retries", 0)
            if retries < 3:
                self._drop_selection_retries = retries + 1
                self._pending_drop_selection = list(unresolved)
                QTimer.singleShot(100, self._apply_pending_drop_selection)
                return
        self._drop_selection_retries = 0

    def _tree_index_for_path(self, path: Path):
        src = _fs_model_index_for_path(self._fs_model, path)
        return self._to_tree_index(src)

    def _apply_dropped_path_selection(self, paths: list[Path]) -> list[Path]:
        """Select dropped items in tree or grid; return paths that could not be resolved yet."""
        unresolved: list[Path] = []
        if not paths or not self.isVisible():
            return list(paths)
        if self._show_toolbar and self._view_mode == "tile":
            unresolved = self._select_dropped_paths_in_grid(paths, navigate=True)
            self._on_tree_selection_changed()
            return unresolved
        sm = self._tree.selectionModel()
        if sm is None:
            return list(paths)
        sm.clearSelection()
        selection = QItemSelection()
        first_idx = None
        for p in paths:
            if not p.exists():
                unresolved.append(p)
                continue
            idx = self._tree_index_for_path(p)
            if not idx.isValid():
                unresolved.append(p)
                continue
            self._expand_tree_index_only(idx)
            selection.select(idx, idx)
            if first_idx is None:
                first_idx = idx
        if not selection.isEmpty():
            sm.select(selection, QItemSelectionModel.SelectionFlag.Select)
        if first_idx is not None:
            sm.setCurrentIndex(first_idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            self._tree.scrollTo(first_idx)
        self._on_tree_selection_changed()
        return unresolved

    def _select_dropped_paths_in_grid(self, paths: list[Path], *, navigate: bool = False) -> list[Path]:
        unresolved: list[Path] = []
        if not self._show_toolbar or self._view_mode != "tile":
            return list(paths)
        if self._file_grid is None or self._file_model is None:
            return list(paths)
        try:
            parents = {p.resolve().parent for p in paths}
        except OSError:
            parents = {p.parent for p in paths}
        if len(parents) != 1:
            return list(paths)
        parent = next(iter(parents))
        if navigate and parent.is_dir():
            try:
                need_nav = parent.resolve() != self._grid_browse_root_path().resolve()
            except OSError:
                need_nav = parent != self._grid_browse_root_path()
            if need_nav:
                self._navigate_to(parent)
        try:
            if parent.resolve() != self._grid_browse_root_path().resolve():
                return list(paths)
        except OSError:
            if parent != self._grid_browse_root_path():
                return list(paths)
        sm = self._file_grid.selectionModel()
        if sm is None:
            return list(paths)
        sm.clearSelection()
        names = {p.name for p in paths}
        found_names: set[str] = set()
        selection = QItemSelection()
        first_idx = None
        for row in range(self._file_model.rowCount()):
            entry = self._file_model.entry_at(row)
            if entry is None or entry.path.name not in names:
                continue
            found_names.add(entry.path.name)
            idx = self._file_model.index(row, 0)
            selection.select(idx, idx)
            if first_idx is None:
                first_idx = idx
        for p in paths:
            if p.name not in found_names:
                unresolved.append(p)
        if not selection.isEmpty():
            sm.select(selection, QItemSelectionModel.SelectionFlag.Select)
        if first_idx is not None:
            sm.setCurrentIndex(first_idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            self._file_grid.scrollTo(first_idx)
        return unresolved

    def reveal_path(self, path: Path, *, link_reveal: bool = False) -> bool:
        """Reveal a file or folder under the current date-folder scope."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            path.resolve().relative_to(self._date_folder_path.resolve())
        except (OSError, ValueError):
            return False
        ok = False
        if path.is_dir() and self._show_toolbar and self._view_mode == "tile":
            # Tile mode: select the folder card in its parent grid (same as files).
            # navigate_to_path would enter the folder and clear grid selection.
            unresolved = self._select_dropped_paths_in_grid([path], navigate=True)
            ok = not unresolved
            if ok:
                self._on_tree_selection_changed()
        elif path.is_dir():
            self.navigate_to_path(path)
            ok = True
        elif self._show_toolbar and self._view_mode == "tile":
            unresolved = self._select_dropped_paths_in_grid([path], navigate=True)
            ok = not unresolved
            if ok:
                self._on_tree_selection_changed()
        else:
            parent = path.parent
            # QFileSystemModel populates asynchronously — nudge parents before lookup.
            walk = parent if parent.is_dir() else None
            while walk is not None:
                try:
                    walk.resolve().relative_to(self._date_folder_path.resolve())
                except (OSError, ValueError):
                    break
                src = self._fs_model.index(str(walk.resolve()), 0)
                if src.isValid() and self._fs_model.canFetchMore(src):
                    self._fs_model.fetchMore(src)
                if walk.resolve() == self._date_folder_path.resolve():
                    break
                walk = walk.parent if walk.parent != walk else None
            parent_src = self._fs_model.index(str(parent.resolve()), 0) if parent.is_dir() else QModelIndex()
            if not parent_src.isValid():
                return False
            if self._fs_model.canFetchMore(parent_src):
                self._fs_model.fetchMore(parent_src)
            file_src = self._fs_model.index(str(path.resolve()), 0)
            if not file_src.isValid():
                return False
            file_idx = self._to_tree_index(file_src)
            if not file_idx.isValid():
                return False
            parent_src = file_src.parent()
            while parent_src.isValid():
                parent_tree = self._to_tree_index(parent_src)
                if parent_tree == self._tree.rootIndex():
                    break
                if parent_tree.isValid():
                    self._tree.expand(parent_tree)
                parent_src = parent_src.parent()
            self._tree.scrollTo(file_idx)
            sm = self._tree.selectionModel()
            if sm is not None:
                from PySide6.QtCore import QItemSelectionModel

                sm.setCurrentIndex(
                    file_idx,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
            self._on_tree_selection_changed()
            ok = True
        if ok and link_reveal:
            from monostudio.ui_qt.link_reveal import link_reveal as lr

            lr().reveal_path(path)
        return ok

    def navigate_to_path(self, path: Path) -> None:
        """Reveal and select a folder inside the current source tree."""
        path = Path(path)
        if not path.is_dir():
            return
        try:
            path.resolve().relative_to(self._date_folder_path.resolve())
        except ValueError:
            return
        src_idx = self._fs_model.index(str(path.resolve()), 0)
        if not src_idx.isValid():
            return
        tree_idx = self._to_tree_index(src_idx)
        if not tree_idx.isValid():
            return
        parent_src = src_idx.parent()
        while parent_src.isValid():
            parent_tree = self._to_tree_index(parent_src)
            if parent_tree == self._tree.rootIndex():
                break
            if parent_tree.isValid():
                self._tree.expand(parent_tree)
            parent_src = parent_src.parent()
        if self._show_toolbar:
            try:
                if path.resolve() != self._date_folder_path.resolve():
                    self._navigate_to(path)
            except OSError:
                if path != self._date_folder_path:
                    self._navigate_to(path)
        self._tree.scrollTo(tree_idx)
        sm = self._tree.selectionModel()
        if sm is not None:
            from PySide6.QtCore import QItemSelectionModel

            sm.setCurrentIndex(
                tree_idx,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )

    def get_selected_paths(self) -> list[Path]:
        """Return list of selected file/folder paths (tree or grid)."""
        if self._show_toolbar and self._view_mode == "tile" and self._file_grid is not None:
            paths: list[Path] = []
            seen: set[str] = set()
            sm = self._file_grid.selectionModel()
            if sm is None:
                return paths
            for idx in sm.selectedIndexes():
                if self._file_model is None:
                    break
                entry = self._file_model.entry_at(idx.row())
                if entry is None:
                    continue
                key = str(entry.path.resolve())
                if key not in seen and entry.path.exists():
                    seen.add(key)
                    paths.append(entry.path)
            return paths
        paths = []
        seen = set()
        for idx in self._tree.selectionModel().selectedIndexes():
            if idx.column() != 0:
                continue
            path = self._path_for_tree_index(idx)
            if path is None:
                continue
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def set_date_folder_path(self, path: Path) -> None:
        self._date_folder_path = Path(path)
        self._grid_browse_root = Path(path)
        self._nav_history = [Path(path)]
        self._nav_index = 0
        if self._storage_root_override is not None:
            try:
                self._storage_root_override = Path(path).resolve()
            except OSError:
                self._storage_root_override = Path(path)
        self._reload_fs_tree_root()
        self._reload_file_entries()
        QTimer.singleShot(0, self._sync_empty_overlay)
        self.browse_path_changed.emit(self._date_folder_path)

    def get_tree_state(self) -> dict:
        expanded: list[str] = []
        root_path = self._date_folder_path.resolve()

        def walk(index):
            if not index.isValid():
                return
            src = self._to_source_index(index)
            p = Path(self._fs_model.filePath(src))
            try:
                rel = p.relative_to(root_path)
            except ValueError:
                return
            if self._tree.isExpanded(index):
                expanded.append(str(rel).replace("\\", "/"))
            for r in range(self._tree.model().rowCount(index)):
                walk(self._tree.model().index(r, 0, index))

        root_idx = self._tree.rootIndex()
        if root_idx.isValid():
            model = self._tree.model()
            for r in range(model.rowCount(root_idx)):
                walk(model.index(r, 0, root_idx))
        state: dict = {"expanded_paths": expanded}
        if self._show_toolbar:
            state["browse_path"] = str(self._grid_browse_root_path().resolve())
        return state

    def set_tree_state(self, state: dict | None) -> None:
        if not state:
            return
        expanded = state.get("expanded_paths")
        browse_path_str = state.get("browse_path") if self._show_toolbar else None
        if (not expanded or not isinstance(expanded, list)) and not browse_path_str:
            return
        root_path = self._date_folder_path.resolve()

        def apply():
            if expanded and isinstance(expanded, list):
                for rel in sorted(expanded, key=lambda p: (p.count("/"), p)):
                    full = root_path / rel.replace("\\", "/")
                    if not full.exists():
                        continue
                    idx = self._fs_model.index(str(full), 0)
                    if idx.isValid():
                        tree_idx = self._to_tree_index(idx)
                        if tree_idx.isValid():
                            self._tree.expand(tree_idx)
            if browse_path_str:
                browse_path = Path(str(browse_path_str))
                if browse_path.is_dir():
                    try:
                        browse_path.resolve().relative_to(root_path)
                    except ValueError:
                        return
                    try:
                        if browse_path.resolve() != self._grid_browse_root_path().resolve():
                            self._navigate_to(browse_path)
                    except OSError:
                        if browse_path != self._grid_browse_root_path():
                            self._navigate_to(browse_path)

        # Defer so root index and model are ready (e.g. after set_date_folder_path / setRootIndex).
        QTimer.singleShot(50, apply)


class ProjectGuideTreePane(InboxTreePane):
    """Inbox-style explorer for project_guide/<department> (no date folders; sidebar tag filter)."""

    item_tags_changed = Signal()

    def __init__(
        self,
        dept_path: Path,
        parent=None,
        *,
        project_root: Path | None,
        project_guide_root: Path | None,
        source_filter: str,
    ) -> None:
        dept_res = Path(dept_path).resolve()
        self._pg_project_root = Path(project_root) if project_root else None
        self._pg_guide_root = Path(project_guide_root) if project_guide_root else None
        self._item_tags: dict[str, list[str]] = {}
        self._tag_defs: list[dict[str, str]] = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map: dict[str, str] = dict(TAG_COLOR_BY_ID)
        self._tag_proxy = None
        super().__init__(
            dept_res,
            parent,
            show_history_action=False,
            show_toolbar=True,
            view_settings_key="project_guide/view_mode",
            source_filter=source_filter,
            breadcrumb_title="Project Guide",
            allow_root_drop=True,
            storage_root_override=dept_res,
        )
        self._tag_proxy = _TagFilterProxy(self)
        self._tag_proxy.setSourceModel(self._fs_model)
        self._tag_proxy.set_project_guide_root(self._pg_guide_root)
        self._tree.setModel(self._tag_proxy)
        self._ref_delegate = _RefTreeDelegate(self._tree)
        self._ref_delegate.set_pane(self)
        self._tree.setItemDelegate(self._ref_delegate)

        self._empty_tag_overlay = QWidget(self._tree_host)
        self._empty_tag_overlay.setObjectName("TagEmptyOverlay")
        self._empty_tag_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        tag_ov_lay = QVBoxLayout(self._empty_tag_overlay)
        tag_ov_lay.setContentsMargins(24, 48, 24, 48)
        tag_ov_lay.setSpacing(12)
        tag_ov_lay.addStretch(2)
        tag_ov_icon = QLabel(self._empty_tag_overlay)
        tag_ov_icon.setAlignment(Qt.AlignCenter)
        tag_ov_icon.setPixmap(
            lucide_icon("tag", size=48, color_hex=MONOS_COLORS.get("text_meta", "#71717a")).pixmap(48, 48)
        )
        tag_ov_lay.addWidget(tag_ov_icon, 0, Qt.AlignCenter)
        tag_ov_text = QLabel("No files tagged", self._empty_tag_overlay)
        tag_ov_text.setAlignment(Qt.AlignCenter)
        tag_ov_text.setObjectName("InboxEmptyStateTitle")
        tag_ov_text.setFont(monos_font("Inter", 14, QFont.Weight.DemiBold))
        tag_ov_lay.addWidget(tag_ov_text, 0, Qt.AlignCenter)
        tag_ov_sub = QLabel("Select another tag or clear the filter", self._empty_tag_overlay)
        tag_ov_sub.setAlignment(Qt.AlignCenter)
        tag_ov_sub.setObjectName("InboxEmptyStateSubtitle")
        tag_ov_sub.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        tag_ov_lay.addWidget(tag_ov_sub, 0, Qt.AlignCenter)
        tag_ov_lay.addStretch(3)
        self._empty_tag_overlay.hide()
        self._empty_tag_overlay.setParent(self._tree_host)
        self._empty_tag_overlay.raise_()
        self._reload_fs_tree_root()

    @property
    def _project_guide_root(self) -> Path | None:
        return self._pg_guide_root

    @property
    def _project_root(self) -> Path | None:
        return self._pg_project_root

    @property
    def _root_path(self) -> Path | None:
        return self._date_folder_path

    def _to_tree_index(self, source_index: QModelIndex) -> QModelIndex:
        if not source_index.isValid():
            return source_index
        proxy = getattr(self, "_tag_proxy", None)
        if proxy is None or self._tree.model() is self._fs_model:
            return source_index
        return _proxy_map_from_source(
            source_index,
            proxy=proxy,
            source_model=self._fs_model,
        )

    def _to_source_index(self, tree_index: QModelIndex) -> QModelIndex:
        if not tree_index.isValid():
            return tree_index
        proxy = getattr(self, "_tag_proxy", None)
        if proxy is None or self._tree.model() is self._fs_model:
            return tree_index
        return _proxy_map_to_source(
            tree_index,
            proxy=proxy,
            source_model=self._fs_model,
        )

    def _reload_fs_tree_root(self) -> None:
        proxy = getattr(self, "_tag_proxy", None)
        if proxy is None:
            super()._reload_fs_tree_root()
            return
        root = self._date_folder_path
        proxy.set_tree_root_path(root)
        if root and root.is_dir():
            root_str = str(root.resolve())
            self._fs_model.setRootPath("")
            self._fs_model.setRootPath(root_str)
            src = self._fs_model.index(root_str)
            if src.isValid():
                proxy_idx = proxy.mapFromSource(src)
                if proxy_idx.isValid():
                    self._tree.setRootIndex(proxy_idx)
        else:
            self._fs_model.setRootPath("")
            empty = proxy.mapFromSource(self._fs_model.index(""))
            if empty.isValid():
                self._tree.setRootIndex(empty)
        self._reload_file_entries()
        self._sync_tree_to_browse_root()
        QTimer.singleShot(0, self._sync_empty_overlay)

    def refresh_content_responsive(self, worker_manager, *, on_pump=None) -> None:
        from monostudio.ui_qt.ui_worker_loop import run_worker_blocking_ui

        proxy = getattr(self, "_tag_proxy", None)
        if proxy is None:
            super().refresh_content_responsive(worker_manager, on_pump=on_pump)
            return
        if on_pump is not None:
            on_pump()
        root = self._date_folder_path
        proxy.set_tree_root_path(root)
        if root and root.is_dir():
            root_str = str(root.resolve())
            self._fs_model.setRootPath("")
            self._fs_model.setRootPath(root_str)
            src = self._fs_model.index(root_str)
            if src.isValid():
                proxy_idx = proxy.mapFromSource(src)
                if proxy_idx.isValid():
                    self._tree.setRootIndex(proxy_idx)
        else:
            self._fs_model.setRootPath("")
            empty = proxy.mapFromSource(self._fs_model.index(""))
            if empty.isValid():
                self._tree.setRootIndex(empty)
        if on_pump is not None:
            on_pump()
        self._reload_file_entries_responsive(worker_manager, run_worker_blocking_ui, on_pump=on_pump)
        self._sync_tree_to_browse_root()
        QTimer.singleShot(0, self._sync_empty_overlay)

    def _sync_empty_overlay(self) -> None:
        if self._tag_proxy is not None and self._tag_proxy._active_tags:
            self._sync_empty_tag_overlay()
            return
        if hasattr(self, "_empty_tag_overlay"):
            self._empty_tag_overlay.setVisible(False)
        super()._sync_empty_overlay()

    def _on_tree_double_clicked(self, index) -> None:
        if not index.isValid():
            return
        path = self._path_for_tree_index(index)
        if path is None:
            return
        if path.is_dir():
            if self._show_toolbar and self._view_mode == "tile":
                self._browse_into_folder(path)
            else:
                try:
                    already_here = self._grid_browse_root_path().resolve() == path.resolve()
                except OSError:
                    already_here = self._grid_browse_root_path() == path
                if already_here and self._tree.isExpanded(index):
                    self._tree_open_path(path)
                    return
                self._fetch_and_expand_tree_index(index)
                if self._show_toolbar:
                    self._navigate_to(path)
            return
        self._tree_open_path(path)

    def _sync_content_toolbar(self) -> None:
        if self._content_toolbar is None:
            return
        self._content_toolbar.set_context(
            hint="Double-click folder to browse · Double-click file to open",
            show_toggle=True,
        )
        self._sync_browse_bar()

    def _on_tree_selection_changed(self) -> None:
        self._emit_tree_selection()
        count = len(self.get_selected_paths())
        self.selection_count_changed.emit(count)
        if self._hint_bar is not None:
            self._hint_bar.setVisible(False)

    def set_project_guide_root(self, root: Path | None, project_root: Path | None = None) -> None:
        self._pg_guide_root = Path(root) if root else None
        if project_root is not None:
            self._pg_project_root = Path(project_root)
        self._tag_proxy.set_project_guide_root(self._pg_guide_root)

    def tags_for_guide_path(self, path: Path) -> list[str]:
        if not self._pg_guide_root:
            return []
        try:
            rel = path.resolve().relative_to(self._pg_guide_root.resolve()).as_posix()
        except (ValueError, OSError):
            return []
        return get_tags_for_item(self._item_tags, rel)

    def _file_entries_for_browse_root(self, root: Path) -> list[_InboxFileEntry]:
        if not self._tag_proxy or not self._tag_proxy._active_tags or not self._pg_guide_root:
            return self._sorted_file_entries(_collect_inbox_file_entries(root))
        tagged = self._tag_proxy._tagged_paths if self._tag_proxy else set()
        if not tagged:
            return []
        return self._sorted_file_entries(
            _collect_tag_filtered_file_entries(root, self._pg_guide_root, tagged)
        )

    def set_tag_data(self, item_tags: dict[str, list[str]]) -> None:
        self._item_tags = item_tags
        if self._tag_proxy is not None and self._tag_proxy._active_tags:
            self._tag_proxy.set_tag_filter(
                self._tag_proxy._active_tags,
                self._item_tags,
                department_id=self._source_filter,
            )
            self._reload_file_entries()
            self._sync_tree_to_browse_root()
            QTimer.singleShot(0, self._sync_empty_overlay)

    def reload_tag_definitions(self) -> None:
        if self._pg_project_root:
            self._tag_defs = read_tag_definitions(self._pg_project_root, self._source_filter)
        else:
            self._tag_defs = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map = build_color_map(self._tag_defs)
        self._tree.viewport().update()
        if self._file_grid is not None:
            self._file_grid.viewport().update()

    def set_tag_filter(self, tag_ids: list[str] | None) -> None:
        prev = list(self._tag_proxy._active_tags)
        ids = [t for t in (tag_ids or []) if t]
        filter_changed = ids != prev
        self._tag_proxy.set_tag_filter(ids, self._item_tags, department_id=self._source_filter)
        if ids and not prev:
            self._grid_browse_root = Path(self._date_folder_path)
            self._nav_history = [Path(self._date_folder_path)]
            self._nav_index = 0
        if filter_changed:
            self._reload_fs_tree_root()
        elif ids:
            self._reload_file_entries()
            QTimer.singleShot(0, self._sync_empty_overlay)

    def get_item_tags(self) -> dict[str, list[str]]:
        return self._item_tags

    def _supports_project_guide_tags(self) -> bool:
        return True

    def _relative_paths_for_guide_targets(self, targets: list[Path]) -> list[str]:
        return _relative_paths_under_guide_root(targets, self._pg_guide_root)

    def _toggle_tag(self, relative_paths: list[str], tag_id: str) -> None:
        if not self._pg_project_root or not relative_paths:
            return
        toggle_tag_for_items(
            self._pg_project_root,
            self._item_tags,
            relative_paths,
            tag_id,
            department_id=self._source_filter,
        )
        self._tree.viewport().update()
        if self._file_grid is not None:
            self._file_grid.viewport().update()
        self.item_tags_changed.emit()

    def _remove_all_tags(self, relative_paths: list[str]) -> None:
        if not self._pg_project_root or not relative_paths:
            return
        for rel in relative_paths:
            set_tags_for_item(self._pg_project_root, self._item_tags, rel, [])
        self._tree.viewport().update()
        if self._file_grid is not None:
            self._file_grid.viewport().update()
        self.item_tags_changed.emit()

    def _apply_browse_root(self, path: Path) -> None:
        super()._apply_browse_root(path)
        QTimer.singleShot(0, self._sync_empty_overlay)

    def _sync_empty_tag_overlay(self) -> None:
        if not hasattr(self, "_empty_tag_overlay") or self._tag_proxy is None:
            return
        tags = self._tag_proxy._active_tags
        if not tags:
            self._empty_tag_overlay.setVisible(False)
            super()._sync_empty_overlay()
            return
        if self._show_toolbar and self._view_mode == "tile":
            has_items = bool(self._file_entries_for_browse_root(self._grid_browse_root_path()))
        else:
            root_idx = self._tree.rootIndex()
            has_items = root_idx.isValid() and self._tree.model().rowCount(root_idx) > 0
        show_tag_empty = not has_items
        if hasattr(self, "_empty_overlay"):
            self._empty_overlay.setVisible(False)
        self._empty_tag_overlay.setVisible(show_tag_empty)
        if show_tag_empty:
            self._empty_tag_overlay.setGeometry(self._tree_host.rect())
            self._empty_tag_overlay.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_empty_tag_overlay") and self._empty_tag_overlay.isVisible():
            self._empty_tag_overlay.setGeometry(self._tree_host.rect())


class _RefDropViewport(QWidget):
    """Custom tree viewport (middle-mouse drag); Explorer drops handled by ReferenceTreePane."""

    def __init__(self, tree: QTreeView, parent=None) -> None:
        super().__init__(parent)
        self._tree = tree


_TAG_ICON_SIZE = 10
_TAG_ICON_SPACING = 2
_TAG_ICON_RIGHT_MARGIN = 8


class _RefTreeDelegate(_InboxTreeDelegate):
    """Extends _InboxTreeDelegate with colored tag icons drawn to the right of the item text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tag_pixmap_cache: dict[str, QPixmap] = {}

    def set_pane(self, pane: "ReferenceTreePane") -> None:
        super().set_pane(pane)

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
        pane = self._pane
        if not index.isValid() or index.column() != 0 or pane is None:
            return
        path_fn = getattr(pane, "_path_from_tree_index", None)
        if path_fn is None:
            path_fn = getattr(pane, "_path_for_tree_index", None)
        if not callable(path_fn):
            return
        path = path_fn(index)
        if path is None:
            return
        pg_root = getattr(pane, "_project_guide_root", None)
        if pg_root is None:
            return
        try:
            rel = path.relative_to(pg_root).as_posix()
        except (ValueError, OSError):
            return
        tags = get_tags_for_item(pane._item_tags, rel)
        if not tags:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = option.rect
        x = rect.right() - _TAG_ICON_RIGHT_MARGIN
        cy = rect.center().y()
        for tag_id in reversed(tags):
            color_hex = pane._tag_color_map.get(tag_id)
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
        self._active_tags: list[str] = []
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

    def set_tag_filter(
        self,
        tag_ids: list[str] | None,
        item_tags: dict[str, list[str]],
        *,
        department_id: str | None = None,
    ) -> None:
        self._active_tags = [t for t in (tag_ids or []) if t]
        if self._active_tags:
            self._tagged_paths = paths_with_any_tag(
                item_tags, self._active_tags, department_id=department_id,
            )
            self._ancestor_paths = ancestor_paths(self._tagged_paths)
        else:
            self._tagged_paths = set()
            self._ancestor_paths = set()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self._active_tags:
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
    video_preview_requested = Signal(object)  # Path

    def __init__(self, root_path: Path | None, department_label: str, parent=None) -> None:
        super().__init__(parent)
        self._drop_hover_tree_index = QModelIndex()
        self._drop_hover_path: Path | None = None
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
        self._fs_model = _InboxFileSystemModel(self)
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
        self._tree.setUniformRowHeights(True)
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
        ref_viewport = _RefDropViewport(self._tree, self._tree)
        self._tree.setViewport(ref_viewport)
        ref_viewport.installEventFilter(self)
        self._middle_drag = MiddleMouseDragTracker()

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
        self._explorer_drop = ExplorerDropZone(
            self,
            highlight_widget=tree_stack,
            on_drop=self._on_explorer_drop,
            enabled=lambda: self._root_path is not None and self._root_path.is_dir(),
            on_drag_hover=self._sync_drop_hover_from_event,
            on_drag_leave=self._clear_drop_hover,
            is_internal_drag=self._is_internal_ref_drag,
        )
        self._explorer_drop.mount(tree_stack, self._tree, ref_viewport, self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._apply_root_index()

    # ---- Helpers: proxy-aware index ↔ path ----

    def _to_tree_index(self, source_index: QModelIndex) -> QModelIndex:
        return _proxy_map_from_source(
            source_index,
            proxy=self._proxy,
            source_model=self._fs_model,
        )

    def _to_source_index(self, tree_index: QModelIndex) -> QModelIndex:
        return _proxy_map_to_source(
            tree_index,
            proxy=self._proxy,
            source_model=self._fs_model,
        )

    def _path_from_tree_index(self, proxy_idx) -> Path | None:
        """Convert a proxy (tree) index to a filesystem Path."""
        src_idx = self._to_source_index(proxy_idx)
        if not src_idx.isValid():
            return None
        fp = self._fs_model.filePath(src_idx)
        return Path(fp) if fp else None

    def _tree_index_from_path(self, path: Path):
        """Convert a filesystem Path to a proxy (tree) index."""
        src_idx = self._fs_model.index(str(Path(path).resolve()))
        return self._to_tree_index(src_idx)

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

    def _tag_department_id(self) -> str:
        if self._root_path and self._project_guide_root:
            try:
                rel = self._root_path.resolve().relative_to(self._project_guide_root.resolve()).as_posix()
                first = rel.split("/")[0] if rel else ""
                if first:
                    return normalize_tag_department_id(first)
            except (ValueError, OSError):
                pass
        return normalize_tag_department_id(self._department_label)

    def reload_tag_definitions(self) -> None:
        if self._project_root:
            self._tag_defs = read_tag_definitions(self._project_root, self._tag_department_id())
        else:
            self._tag_defs = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map = build_color_map(self._tag_defs)
        self._tree.viewport().update()

    def set_tag_filter(self, tag_ids: list[str] | None) -> None:
        ids = [t for t in (tag_ids or []) if t]
        self._proxy.set_tag_filter(ids, self._item_tags, department_id=self._tag_department_id())
        self._apply_root_index()

    def _sync_empty_tag_overlay(self) -> None:
        tag = self._proxy._active_tags
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
        sel = self._tree.selectionModel()
        if sel is None:
            return
        sel.clearSelection()
        selection = QItemSelection()
        first_idx = None
        for p in paths:
            idx = self._tree_index_from_path(p)
            if idx.isValid():
                selection.select(idx, idx)
                if first_idx is None:
                    first_idx = idx
        if not selection.isEmpty():
            sel.select(selection, QItemSelectionModel.SelectionFlag.Select)
        if first_idx is not None:
            sel.setCurrentIndex(first_idx, QItemSelectionModel.SelectionFlag.NoUpdate)
            self._tree.scrollTo(first_idx)

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

    def _is_internal_ref_drag(self, paths: list[Path]) -> bool:
        root = self._root_path
        if root is None:
            return False
        return paths_under_root(paths, root)

    def _ref_do_drop(self, paths: list, viewport_pos: QPoint, event: QDropEvent) -> None:
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
        if not valid:
            return
        ctrl_copy = bool(event.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        use_move = root and all(_is_under_root(p) for p in valid) and not ctrl_copy
        if use_move:
            self.move_files_to_folder(valid, target)
            notification_service.success(
                f"Moved {len(valid)} item{'s' if len(valid) != 1 else ''} in Project Guide."
            )
        else:
            self.drop_files_to_folder(valid, target)
            notification_service.success(
                f"Added {len(valid)} item{'s' if len(valid) != 1 else ''} to Project Guide."
            )

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

        ref_targets = self._ref_context_menu_targets(path)
        multi = len(ref_targets) > 1
        open_act = None
        open_folder_act = None
        if not multi:
            open_act = menu.addAction(_icon("file"), "Open")
            open_folder_act = menu.addAction(_icon("folder-open"), "Open folder")
        new_folder_act = menu.addAction(_icon("folder-plus"), "New folder")
        rename_act = None
        if not multi:
            rename_act = menu.addAction(_icon("copy"), "Rename")
        menu.addSeparator()
        delete_label = f"Delete {len(ref_targets)} items" if multi else "Delete"
        delete_act = menu.addAction(_icon_red("x"), delete_label)
        menu.addSeparator()
        tags_submenu = menu.addMenu(_icon("tag"), "Tags")
        sel_rel_paths = _relative_paths_under_guide_root(ref_targets, self._project_guide_root)
        tag_actions, remove_tags_act = _populate_guide_tags_submenu(
            tags_submenu,
            tag_defs=self._tag_defs,
            item_tags=self._item_tags,
            relative_paths=sel_rel_paths,
            menu_icon=_icon,
        )
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
            self._ref_delete_paths(ref_targets)
        elif action == import_act:
            self.import_requested.emit()

    def _ref_selected_paths(self) -> list[Path]:
        pg_root = self._project_guide_root
        if not pg_root:
            return []
        out: list[Path] = []
        seen: set[str] = set()
        for rel in self._selected_relative_paths():
            p = pg_root / rel.replace("/", os.sep)
            try:
                key = str(p.resolve())
            except OSError:
                key = str(p)
            if key not in seen and p.exists():
                seen.add(key)
                out.append(p)
        return out

    def _ref_context_menu_targets(self, path: Path | None) -> list[Path]:
        if path is None:
            return []
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        selected = self._ref_selected_paths()
        if not selected:
            return [path]
        sel_keys = set()
        for p in selected:
            try:
                sel_keys.add(str(p.resolve()))
            except OSError:
                sel_keys.add(str(p))
        if key in sel_keys:
            return selected
        return [path]

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
        toggle_tag_for_items(
            self._project_root,
            self._item_tags,
            relative_paths,
            tag_id,
            department_id=self._tag_department_id(),
        )
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
                from monostudio.core.shell_open import open_folder as shell_open_folder

                shell_open_folder(path)
            except Exception:
                pass
        else:
            from monostudio.ui_qt.thumbnails import is_video_preview_path

            if is_video_preview_path(path):
                self.video_preview_requested.emit(path)
                return
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

    def _ref_delete_paths(self, paths: list[Path]) -> None:
        targets = [p for p in paths if p is not None]
        if not targets:
            return
        if len(targets) == 1:
            path = targets[0]
            name = path.name or str(path)
            if path.is_dir():
                msg = f'Delete folder "{name}" and all its contents?'
            else:
                msg = f'Delete file "{name}"?'
        else:
            folders = sum(1 for p in targets if p.is_dir())
            files = len(targets) - folders
            parts: list[str] = []
            if files:
                parts.append(f"{files} file{'s' if files != 1 else ''}")
            if folders:
                parts.append(f"{folders} folder{'s' if folders != 1 else ''}")
            summary = " and ".join(parts) if parts else f"{len(targets)} items"
            msg = f"Delete {summary}?"
        if not ask_delete(self._tree, "Delete", msg):
            return
        deleted = 0
        deleted_labels: list[tuple[str, bool]] = []
        errors: list[str] = []
        for path in targets:
            try:
                if not path.exists():
                    continue
                name = path.name or str(path)
                is_dir = path.is_dir()
                if is_dir:
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted += 1
                deleted_labels.append((name, is_dir))
            except OSError as e:
                errors.append(f"{path.name}: {e}")
        if deleted > 0:
            self._force_reload()
            if deleted == 1:
                name, is_dir = deleted_labels[0]
                kind = "folder" if is_dir else "file"
                notification_service.operational_success(f'Deleted {kind} "{name}".')
            else:
                notification_service.operational_success(f"Deleted {deleted} items.")
        if errors:
            QMessageBox.warning(
                self._tree,
                "Delete",
                "Some items could not be deleted:\n" + "\n".join(errors[:8]),
            )

    def _on_ref_tree_double_clicked(self, index) -> None:
        if not index.isValid():
            return
        path = self._path_from_tree_index(index)
        if path and path.is_file():
            from monostudio.ui_qt.thumbnails import is_video_preview_path

            if is_video_preview_path(path):
                self.video_preview_requested.emit(path)
                return
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

    def is_drop_hover_tree_index(self, index: QModelIndex) -> bool:
        if self._drop_hover_path is None or not index.isValid():
            return False
        row_path = self._path_from_tree_index(index)
        if row_path is None:
            return False
        try:
            return row_path.resolve() == self._drop_hover_path.resolve()
        except OSError:
            return row_path == self._drop_hover_path

    def _sync_drop_hover_from_event(self, event: QDragEnterEvent | QDragMoveEvent) -> None:
        gpos = ExplorerDropZone.event_global_pos(event, self)
        new_path: Path | None = None
        tree_idx = self._tree.indexAt(self._tree.viewport().mapFromGlobal(gpos))
        if tree_idx.isValid():
            new_path = self._path_from_tree_index(tree_idx)
        if new_path is None and self._root_path is not None:
            new_path = self._root_path
        self._drop_hover_path = new_path
        self._drop_hover_tree_index = tree_idx
        self._tree.viewport().update()

    def _clear_drop_hover(self) -> None:
        self._drop_hover_tree_index = QModelIndex()
        self._drop_hover_path = None
        self._tree.viewport().update()

    def _on_explorer_drop(self, paths: list[Path], event: QDropEvent) -> None:
        viewport = self._tree.viewport()
        gpos = ExplorerDropZone.event_global_pos(event, self)
        viewport_pos = viewport.mapFromGlobal(gpos)
        self._ref_do_drop(paths, viewport_pos, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._explorer_drop.handle_drag_enter(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._explorer_drop.handle_drag_move(event):
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._explorer_drop.handle_drag_leave()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._explorer_drop.handle_drop(event):
            return
        super().dropEvent(event)

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
        if obj is self._tree:
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
            ):
                idx = self._tree.currentIndex()
                path = self._path_from_tree_index(idx) if idx.isValid() else None
                if path is not None and path.is_file():
                    from monostudio.ui_qt.thumbnails import is_video_preview_path

                    if is_video_preview_path(path):
                        self.video_preview_requested.emit(path)
                        return True
        if obj is self._tree.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                self._middle_drag.on_mouse_press(event)
                return False
            if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                self._middle_drag.on_mouse_release(event)
                return False
            if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                if self._middle_drag.try_start_drag(
                    event,
                    source=self._tree.viewport(),
                    resolve_paths=self._resolve_tree_drag_paths,
                ):
                    return True
                return False
        return super().eventFilter(obj, event)

    def _resolve_tree_drag_paths(self, anchor_pos: QPoint | None) -> list[Path]:
        sm = self._tree.selectionModel()
        selected = sm.selectedIndexes() if sm is not None else []
        return collect_tree_drag_paths(
            selected_indexes=selected,
            path_for_index=self._path_from_tree_index,
            anchor_pos=anchor_pos,
            index_at=self._tree.indexAt,
        )

    def set_root(self, root_path: Path | None, department_label: str = "") -> None:
        self._root_path = Path(root_path) if root_path else None
        if department_label:
            self._department_label = department_label
        if getattr(self, "_dept_breadcrumb_label", None) is not None:
            self._dept_breadcrumb_label.setText(
                (self._department_label or "").replace("_", " ").strip().title() or "Reference"
            )
        self._apply_root_index()
