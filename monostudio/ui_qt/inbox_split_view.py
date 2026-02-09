"""
Inbox split view: trái = cây nội dung date folder, phải = mapping list.
User Add từ trái sang phải; destination + xác nhận trong Inspector (plan 5.3, 5.4).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QAbstractFileIconProvider, QFont
from PySide6.QtWidgets import (
    QFileIconProvider,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
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


class InboxSplitView(QWidget):
    """
    Trái: cây date folder (file/folder). Phải: mapping list (item sẽ distribute).
    Add: chọn trái → Add → thêm vào phải. Remove: bỏ khỏi mapping list.
    """

    back_requested = Signal()
    mapping_selection_changed = Signal(object)  # list[Path]
    open_folder_requested = Signal(object)  # Path

    def __init__(self, date_folder_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._date_folder_path = Path(date_folder_path)
        self._mapping_paths: list[Path] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QWidget(self)
        toolbar.setObjectName("InboxSplitToolbar")
        tlay = QHBoxLayout(toolbar)
        tlay.setContentsMargins(12, 8, 12, 8)
        tlay.setSpacing(8)

        # Breadcrumb: Inbox > Client > 2025-02-07 (first segment clickable = back to list)
        self._breadcrumb_widget = self._make_breadcrumb()
        tlay.addWidget(self._breadcrumb_widget, 0)

        tlay.addStretch(1)
        add_btn = QPushButton("Add to mapping", toolbar)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _add_icon = lucide_icon("plus", size=16, color_hex=MONOS_COLORS["text_label"])
        if not _add_icon.isNull():
            add_btn.setIcon(_add_icon)
        add_btn.clicked.connect(self._on_add)
        tlay.addWidget(add_btn, 0)

        remove_btn = QPushButton("Remove", toolbar)
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(self._on_remove)
        tlay.addWidget(remove_btn, 0)
        self._remove_btn = remove_btn

        refresh_btn = QPushButton("Refresh", toolbar)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _refresh_icon = lucide_icon("refresh-cw", size=16, color_hex=MONOS_COLORS["text_label"])
        if not _refresh_icon.isNull():
            refresh_btn.setIcon(_refresh_icon)
        refresh_btn.clicked.connect(self._on_refresh)
        tlay.addWidget(refresh_btn, 0)

        open_btn = QPushButton("Open folder", toolbar)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _open_icon = lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"])
        if not _open_icon.isNull():
            open_btn.setIcon(_open_icon)
        open_btn.clicked.connect(lambda: self.open_folder_requested.emit(self._date_folder_path))
        tlay.addWidget(open_btn, 0)

        root.addWidget(toolbar, 0)

        self._splitter = QSplitter(Qt.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setStretchFactor(0, 50)
        self._splitter.setStretchFactor(1, 50)

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
        self._splitter.addWidget(self._tree)

        right_w = QWidget(self)
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_header = QLabel("Mapping list (to distribute)", right_w)
        right_header.setObjectName("InboxMappingHeader")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112.0)
        right_header.setFont(f)
        right_lay.addWidget(right_header, 0)
        self._mapping_list = QListWidget(self)
        self._mapping_list.setObjectName("InboxMappingList")
        self._mapping_list.setSelectionMode(QListWidget.ExtendedSelection)
        self._mapping_list.itemSelectionChanged.connect(self._on_mapping_selection_changed)
        right_lay.addWidget(self._mapping_list, 1)
        self._splitter.addWidget(right_w)

        root.addWidget(self._splitter, 1)

        self._sync_remove_enabled()

    def _make_breadcrumb(self) -> QWidget:
        # Inbox › Client › 2025-02-07 — all segments except last clickable (back to list)
        path_parts = self._date_folder_path.parts
        trail = path_parts[-2:] if len(path_parts) >= 2 else path_parts  # e.g. [client, 2025-02-07]
        wrap = QWidget(self)
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        sep_style = "color: #71717a; font-size: 12px;"
        label_style = "color: #a1a1aa; font-size: 13px;"
        link_style = (
            "QPushButton { color: #a1a1aa; font-size: 13px; border: none; background: transparent; }"
            "QPushButton:hover { color: #60a5fa; }"
        )
        segments = ["Inbox", *trail]
        for i, name in enumerate(segments):
            if i > 0:
                sep = QLabel("›", wrap)
                sep.setStyleSheet(sep_style)
                sep.setFont(monos_font("Inter", 12))
                lay.addWidget(sep, 0)
            display = (name or "").replace("_", " ").strip().title() or name
            is_last = i == len(segments) - 1
            if is_last:
                lb = QLabel(display, wrap)
                lb.setStyleSheet(label_style)
                lb.setFont(monos_font("Inter", 13))
                lay.addWidget(lb, 0)
            else:
                btn = QPushButton(display, wrap)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFlat(True)
                btn.setStyleSheet(link_style)
                btn.setFont(monos_font("Inter", 13))
                btn.clicked.connect(self.back_requested.emit)
                lay.addWidget(btn, 0)
        return wrap

    def _collect_expanded_paths(self) -> list[str]:
        root_path = self._date_folder_path.resolve()
        expanded: list[str] = []

        def walk(idx):
            if not idx.isValid():
                return
            path = Path(self._fs_model.filePath(idx))
            try:
                rel = path.relative_to(root_path)
            except ValueError:
                return
            if self._tree.isExpanded(idx):
                expanded.append(str(rel).replace("\\", "/"))
            for r in range(self._fs_model.rowCount(idx)):
                walk(self._fs_model.index(r, 0, idx))

        root_idx = self._tree.rootIndex()
        if root_idx.isValid():
            for r in range(self._fs_model.rowCount(root_idx)):
                walk(self._fs_model.index(r, 0, root_idx))
        return expanded

    def get_tree_state(self) -> dict:
        return {
            "expanded_paths": self._collect_expanded_paths(),
            "splitter_sizes": [int(s) for s in self._splitter.sizes()],
        }

    def set_tree_state(self, state: dict | None) -> None:
        if not state:
            return
        sizes = state.get("splitter_sizes")
        if sizes and len(sizes) == 2 and all(isinstance(s, (int, float)) for s in sizes):
            self._splitter.setSizes([int(s) for s in sizes])
        expanded = state.get("expanded_paths")
        if not expanded or not isinstance(expanded, list):
            return

        def apply_expand():
            root_path = self._date_folder_path.resolve()
            for rel in sorted(expanded, key=lambda p: (p.count("/"), p)):
                full = root_path / rel.replace("\\", "/")
                if not full.exists():
                    continue
                idx = self._fs_model.index(str(full), 0)
                if idx.isValid():
                    self._tree.expand(idx)

        QTimer.singleShot(0, apply_expand)

    def _on_add(self) -> None:
        indexes = self._tree.selectionModel().selectedIndexes()
        if not indexes:
            return
        seen: set[str] = set()
        for idx in indexes:
            if idx.column() != 0:
                continue
            path = Path(self._fs_model.filePath(idx))
            if not path.exists():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            if path not in self._mapping_paths:
                self._mapping_paths.append(path)
        self._refresh_mapping_list()
        self._sync_remove_enabled()

    def _on_remove(self) -> None:
        rows = sorted(set(i.row() for i in self._mapping_list.selectedIndexes()), reverse=True)
        for row in rows:
            if 0 <= row < len(self._mapping_paths):
                self._mapping_paths.pop(row)
        self._refresh_mapping_list()
        self._sync_remove_enabled()
        self._emit_mapping_selection()

    def _on_refresh(self) -> None:
        self._fs_model.setRootPath("")
        self._tree.setRootIndex(self._fs_model.index(str(self._date_folder_path.resolve())))

    def _refresh_mapping_list(self) -> None:
        self._mapping_list.blockSignals(True)
        self._mapping_list.clear()
        for p in self._mapping_paths:
            try:
                rel = p.relative_to(self._date_folder_path)
                display = str(rel) if len(rel.parts) <= 2 else str(rel)
            except ValueError:
                display = p.name
            it = QListWidgetItem(display)
            it.setData(Qt.ItemDataRole.UserRole, str(p))
            it.setToolTip(str(p))
            icon_name, icon_color = _file_icon_spec(p.is_dir(), p.suffix)
            _icon = lucide_icon(icon_name, size=_TREE_ICON_SIZE, color_hex=icon_color)
            if not _icon.isNull():
                it.setIcon(_icon)
            self._mapping_list.addItem(it)
        self._mapping_list.blockSignals(False)

    def _on_mapping_selection_changed(self) -> None:
        self._sync_remove_enabled()
        self._emit_mapping_selection()

    def _emit_mapping_selection(self) -> None:
        paths: list[Path] = []
        for it in self._mapping_list.selectedItems():
            s = it.data(Qt.ItemDataRole.UserRole)
            if s:
                paths.append(Path(s))
        self.mapping_selection_changed.emit(paths)

    def _sync_remove_enabled(self) -> None:
        self._remove_btn.setEnabled(len(self._mapping_list.selectedItems()) > 0)

    def get_mapping_paths(self) -> list[Path]:
        return list(self._mapping_paths)

    def remove_mapping_paths(self, paths: list) -> None:
        resolved = {Path(p).resolve() for p in paths if p}
        self._mapping_paths = [p for p in self._mapping_paths if p.resolve() not in resolved]
        self._refresh_mapping_list()
        self._emit_mapping_selection()

    def date_folder_path(self) -> Path:
        return self._date_folder_path
