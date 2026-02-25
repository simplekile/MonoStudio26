"""
Inbox History dialog: shows distributed entries (path, distributed_at) for current project + type.
Read-only list with Refresh; optional Open folder. Triggered by History button on Inbox page.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from monostudio.core.inbox_reader import load_inbox_distributed
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import FILE_TYPE_ICON_COLORS, MonosDialog, MONOS_COLORS, monos_font

_ICON_SIZE = 18
_EXT_IMAGE = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".ico", ".svg"})
_EXT_VIDEO = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg"})
_EXT_AUDIO = frozenset({".mp3", ".wav", ".aiff", ".aif", ".ogg", ".flac", ".m4a", ".wma", ".aac"})
_EXT_ARCHIVE = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".zst"})
_EXT_DOCUMENT = frozenset({".pdf", ".doc", ".docx", ".txt", ".rtf", ".md", ".odt", ".xls", ".xlsx", ".csv"})
_EXT_DCC = frozenset({".blend", ".ma", ".mb", ".hip", ".hiplc", ".hipnc", ".spp"})


def _file_icon_spec(is_dir: bool, suffix: str) -> tuple[str, str]:
    colors = FILE_TYPE_ICON_COLORS
    if is_dir:
        return ("folder", colors.get("folder", "#71717a"))
    ext = (suffix or "").strip().lower()
    if not ext.startswith("."):
        ext = "." + ext if ext else ""
    if ext in _EXT_IMAGE:
        return ("file-image", colors.get("image", "#22c55e"))
    if ext in _EXT_VIDEO:
        return ("file-video", colors.get("video", "#ef4444"))
    if ext in _EXT_AUDIO:
        return ("file-music", colors.get("audio", "#eab308"))
    if ext in _EXT_DCC:
        return ("box", colors.get("dcc", "#8b5cf6"))
    if ext in _EXT_ARCHIVE:
        return ("file-archive", colors.get("archive", "#f97316"))
    if ext in _EXT_DOCUMENT:
        return ("file-text", colors.get("document", "#3b82f6"))
    return ("file", colors.get("file", "#a1a1aa"))


class InboxHistoryDialog(MonosDialog):
    """Dialog showing distributed inbox entries (history). Filter by project_root + type_filter."""

    def __init__(self, project_root: Path | None, type_filter: str, parent=None) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root) if project_root else None
        self._type_filter = (type_filter or "").strip().lower() or None
        self.setWindowTitle("Inbox — History")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        title = QLabel("Distributed", self)
        title.setObjectName("InboxMappingHeader")
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        root.addWidget(title, 0)
        self._list = QListWidget(self)
        self._list.setObjectName("InboxMappingList")
        self._list.setSelectionMode(QListWidget.NoSelection)
        root.addWidget(self._list, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        refresh_btn = QPushButton("Refresh", self)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ref_icon = lucide_icon("refresh-cw", size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        if not ref_icon.isNull():
            refresh_btn.setIcon(ref_icon)
        refresh_btn.clicked.connect(self._load)
        btn_row.addWidget(refresh_btn, 0)
        close_btn = QPushButton("Close", self)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn, 0)
        root.addLayout(btn_row, 0)
        self.setMinimumSize(400, 320)
        self.resize(480, 400)
        self._load()

    def set_context(self, project_root: Path | None, type_filter: str) -> None:
        self._project_root = Path(project_root) if project_root else None
        self._type_filter = (type_filter or "").strip().lower() or None
        self._load()

    def _load(self) -> None:
        self._list.clear()
        if not self._project_root or not self._project_root.is_dir():
            return
        entries = load_inbox_distributed(self._project_root, self._type_filter)
        for e in entries:
            if not isinstance(e, dict):
                continue
            path_str = e.get("path") or ""
            distributed_at = e.get("distributed_at") or "—"
            try:
                p = Path(path_str)
                display = p.name
                is_dir = p.is_dir() if p.exists() else False
                suffix = p.suffix
            except (TypeError, ValueError):
                display = path_str or "—"
                is_dir = False
                suffix = ""
            icon_name, icon_color = _file_icon_spec(is_dir, suffix)
            it = QListWidgetItem(display)
            it.setData(Qt.ItemDataRole.UserRole, path_str)
            it.setToolTip(f"{path_str}\nDistributed: {distributed_at}")
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            icon = lucide_icon(icon_name, size=_ICON_SIZE, color_hex=icon_color)
            if not icon.isNull():
                it.setIcon(icon)
            self._list.addItem(it)
