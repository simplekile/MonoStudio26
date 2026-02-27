"""
Inbox History dialog: shows distributed entries in a table with metadata (path, distributed_at, destination, entity, target_path).
Read-only; Refresh and Close. Triggered by History button on Inbox page.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from monostudio.core.inbox_reader import load_inbox_distributed
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, MONOS_COLORS, monos_font


def _format_distributed_at(iso_str: str) -> str:
    """Format ISO8601 to short date/time for display."""
    if not iso_str or not iso_str.strip():
        return "—"
    s = (iso_str or "").strip()
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        return s[:16] if len(s) >= 16 else s
    except (ValueError, TypeError):
        return s[:20] if len(s) > 20 else s


def _truncate_path(path_str: str, max_len: int = 48) -> str:
    if not path_str or len(path_str) <= max_len:
        return path_str or "—"
    return "…" + path_str[-(max_len - 1):]


class InboxHistoryDialog(MonosDialog):
    """Dialog showing distributed inbox entries in a table. Filter by project_root + type_filter."""

    _COL_NAME = 0
    _COL_DISTRIBUTED_AT = 1
    _COL_DESTINATION = 2
    _COL_ENTITY = 3
    _COL_TARGET_PATH = 4

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
        self._table = QTableWidget(self)
        self._table.setObjectName("InboxHistoryTable")
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            "Name",
            "Distributed at",
            "Destination",
            "Entity",
            "Target path",
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(self._COL_TARGET_PATH, QHeaderView.ResizeMode.Stretch)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self._table, 1)
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
        self.setMinimumSize(720, 360)
        self.resize(900, 440)
        self._load()

    def set_context(self, project_root: Path | None, type_filter: str) -> None:
        self._project_root = Path(project_root) if project_root else None
        self._type_filter = (type_filter or "").strip().lower() or None
        self._load()

    def _on_context_menu(self, position) -> None:
        idx = self._table.indexAt(position)
        if not idx.isValid() or idx.row() < 0:
            return
        name_item = self._table.item(idx.row(), self._COL_NAME)
        if not name_item:
            return
        paths = name_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(paths, dict):
            return
        source_path = (paths.get("path") or "").strip()
        target_path = (paths.get("target_path") or "").strip()
        if not source_path and not target_path:
            return
        menu = QMenu(self)
        if source_path:
            copy_source = menu.addAction("Copy source path")
            copy_source.triggered.connect(lambda *, s=source_path: self._copy_to_clipboard(s))
        if target_path:
            copy_target = menu.addAction("Copy target path")
            copy_target.triggered.connect(lambda *, s=target_path: self._copy_to_clipboard(s))
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _copy_to_clipboard(self, text: str) -> None:
        if text:
            cb = QApplication.clipboard()
            if cb:
                cb.setText(text)

    def _load(self) -> None:
        self._table.setRowCount(0)
        if not self._project_root or not self._project_root.is_dir():
            return
        entries = load_inbox_distributed(self._project_root, self._type_filter)
        for e in entries:
            if not isinstance(e, dict):
                continue
            path_str = e.get("path") or ""
            distributed_at = e.get("distributed_at") or ""
            destination_label = (e.get("destination_label") or e.get("destination_id") or "").strip() or "—"
            entity_name = (e.get("entity_name") or "").strip() or "—"
            target_path = (e.get("target_path") or "").strip() or "—"
            scope = (e.get("scope") or "").strip().lower()
            if scope and entity_name == "—" and scope != "project":
                entity_name = f"({scope})"
            try:
                p = Path(path_str)
                name = p.name
            except (TypeError, ValueError):
                name = path_str or "—"
            row = self._table.rowCount()
            self._table.insertRow(row)
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(path_str or "")
            name_item.setData(Qt.ItemDataRole.UserRole, {"path": path_str, "target_path": target_path})
            self._table.setItem(row, self._COL_NAME, name_item)
            at_item = QTableWidgetItem(_format_distributed_at(distributed_at))
            at_item.setToolTip(distributed_at or "")
            self._table.setItem(row, self._COL_DISTRIBUTED_AT, at_item)
            self._table.setItem(row, self._COL_DESTINATION, QTableWidgetItem(destination_label))
            self._table.setItem(row, self._COL_ENTITY, QTableWidgetItem(entity_name))
            target_item = QTableWidgetItem(_truncate_path(target_path))
            target_item.setToolTip(target_path or "")
            target_item.setFont(monos_font("JetBrains Mono", 11))
            self._table.setItem(row, self._COL_TARGET_PATH, target_item)
