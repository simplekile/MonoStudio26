"""
Inbox pane widgets: InboxTreePane (breadcrumb + file tree for one date folder).
ReferenceTreePane for Project Guide page. Used by InboxPageWidget, ReferencePageWidget.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QFileInfo, QRect, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (
    QAbstractFileIconProvider,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
)
from PySide6.QtWidgets import (
    QFileIconProvider,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import FILE_TYPE_ICON_COLORS, MONOS_COLORS, monos_font

_TREE_ICON_SIZE = 18

# Extension sets for file-type icons (lowercase with leading dot)
_EXT_IMAGE = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".ico", ".svg"})
_EXT_VIDEO = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg"})
_EXT_AUDIO = frozenset({".mp3", ".wav", ".aiff", ".aif", ".ogg", ".flac", ".m4a", ".wma", ".aac"})
_EXT_ARCHIVE = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".zst"})
_EXT_DOCUMENT = frozenset({".pdf", ".doc", ".docx", ".txt", ".rtf", ".md", ".odt", ".xls", ".xlsx", ".csv"})
# DCC workfile extensions (from pipeline/dccs.json)
_EXT_DCC = frozenset({".blend", ".ma", ".mb", ".hip", ".hiplc", ".hipnc", ".spp"})


def _file_icon_spec(is_dir: bool, suffix: str) -> tuple[str, str]:
    """Return (lucide_icon_name, color_hex) for folder or file by suffix."""
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
    if ext in _EXT_DCC:
        return ("box", colors["dcc"])
    if ext in _EXT_ARCHIVE:
        return ("file-archive", colors["archive"])
    if ext in _EXT_DOCUMENT:
        return ("file-text", colors["document"])
    return ("file", colors["file"])


class _LucideFileIconProvider(QFileIconProvider):
    """Icon provider for QFileSystemModel using Lucide icons and file-type colors."""

    def icon(self, arg):  # QFileInfo or QAbstractFileIconProvider.IconType
        if isinstance(arg, QFileInfo):
            name, color = _file_icon_spec(arg.isDir(), arg.suffix() or "")
            return lucide_icon(name, size=_TREE_ICON_SIZE, color_hex=color)
        if arg == QAbstractFileIconProvider.IconType.Folder:
            name, color = _file_icon_spec(True, "")
            return lucide_icon(name, size=_TREE_ICON_SIZE, color_hex=color)
        if arg == QAbstractFileIconProvider.IconType.File:
            name, color = _file_icon_spec(False, "")
            return lucide_icon(name, size=_TREE_ICON_SIZE, color_hex=color)
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
    """Breadcrumb + file tree for one date folder. Emits back_requested, tree_selection_changed, open_folder_requested."""

    back_requested = Signal()
    tree_selection_changed = Signal(object)  # Path | None
    open_folder_requested = Signal(object)  # Path (date folder)

    def __init__(self, date_folder_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._date_folder_path = Path(date_folder_path)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget(self)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(12, 8, 12, 8)
        bar_lay.setSpacing(8)
        bar_lay.addWidget(self._make_breadcrumb(), 0)
        bar_lay.addStretch(1)
        open_btn = QPushButton("Open folder", bar)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _open_icon = lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"])
        if not _open_icon.isNull():
            open_btn.setIcon(_open_icon)
        open_btn.clicked.connect(self._on_open_folder)
        bar_lay.addWidget(open_btn, 0)
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
        self._tree.selectionModel().selectionChanged.connect(self._emit_tree_selection)
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
        segments = ["Inbox", *trail]
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

    def _on_open_folder(self) -> None:
        if self._date_folder_path.is_dir():
            self.open_folder_requested.emit(self._date_folder_path)

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

        QTimer.singleShot(0, apply)


class ReferenceTreePane(QWidget):
    """Tree for Project Guide page: root = project_guide/<department>. Breadcrumb Project Guide > department. Emits tree_selection_changed(Path|None)."""

    tree_selection_changed = Signal(object)  # Path | None

    def __init__(self, root_path: Path | None, department_label: str, parent=None) -> None:
        super().__init__(parent)
        self._root_path = Path(root_path) if root_path else None
        self._department_label = department_label or "Reference"
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
        self._tree = QTreeView(self)
        self._tree.setObjectName("InboxSplitTree")
        self._tree.setModel(self._fs_model)
        self._tree.setSelectionMode(QTreeView.ExtendedSelection)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(20)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.hideColumn(1)
        self._tree.hideColumn(2)
        self._tree.hideColumn(3)
        self._tree.setItemDelegate(_InboxTreeDelegate(self._tree))
        self._tree.selectionModel().selectionChanged.connect(self._emit_tree_selection)
        self._tree.installEventFilter(self)
        lay.addWidget(self._tree, 1)
        if self._root_path and self._root_path.is_dir():
            self._tree.setRootIndex(self._fs_model.index(str(self._root_path.resolve())))
        else:
            self._tree.setRootIndex(self._fs_model.index(""))

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
        path = Path(self._fs_model.filePath(idx))
        self.tree_selection_changed.emit(path)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:
        if obj is self._tree and event.type() == QEvent.Type.FocusIn:
            self._emit_tree_selection()
        return super().eventFilter(obj, event)

    def set_root(self, root_path: Path | None, department_label: str = "") -> None:
        self._root_path = Path(root_path) if root_path else None
        if department_label:
            self._department_label = department_label
        if getattr(self, "_dept_breadcrumb_label", None) is not None:
            self._dept_breadcrumb_label.setText(
                (self._department_label or "").replace("_", " ").strip().title() or "Reference"
            )
        if self._root_path and self._root_path.is_dir():
            self._tree.setRootIndex(self._fs_model.index(str(self._root_path.resolve())))
        else:
            self._tree.setRootIndex(self._fs_model.index(""))
