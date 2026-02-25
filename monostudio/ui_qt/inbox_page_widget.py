"""
Inbox page: header (MainView style) + single column (list date folders | tree).
No mapping pane; distribute from tree selection. History via dialog (button).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.inbox_reader import scan_inbox
from monostudio.ui_qt.inbox_history_dialog import InboxHistoryDialog
from monostudio.ui_qt.inbox_split_view import InboxTreePane
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


class _InboxDateFolderList(QWidget):
    """List of date folders (and section headers). Emits date_folder_clicked(Path) when a folder is clicked."""

    date_folder_clicked = Signal(object)  # Path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._source_filter: str = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget(self)
        self._list.setObjectName("InboxMappingList")
        self._list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self._list, 1)

    def set_content(self, project_root: Path | None, source_filter: str) -> None:
        self._project_root = project_root
        self._source_filter = (source_filter or "").strip().lower()
        self._list.clear()
        if not project_root or not project_root.is_dir():
            return
        if not self._source_filter:
            return
        try:
            nodes = scan_inbox(project_root)
        except Exception:
            nodes = []
        if self._source_filter:
            for node in nodes:
                if (node.name or "").lower() != self._source_filter:
                    continue
                if node.is_dir and node.children:
                    for child in node.children:
                        if getattr(child, "is_dir", True) and getattr(child, "path", None):
                            it = QListWidgetItem(child.name or str(child.path))
                            it.setData(Qt.ItemDataRole.UserRole, str(child.path))
                            it.setToolTip(str(child.path))
                            self._list.addItem(it)
                break

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        try:
            p = Path(path_str)
            if p.is_dir():
                self.date_folder_clicked.emit(p)
        except (TypeError, ValueError):
            pass


class InboxPageWidget(QWidget):
    """
    Inbox page: (1) Header (Inbox + type badge + History button); (2) Single column: list | tree.
    Signals: tree_selection_changed(Path|None), tree_distribute_paths_changed(list[Path]), open_folder_requested(Path), drop_requested(list[Path]).
    """

    tree_selection_changed = Signal(object)  # Path | None
    tree_distribute_paths_changed = Signal(object)  # list[Path]
    open_folder_requested = Signal(object)  # Path
    drop_requested = Signal(object)  # list[Path]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._project_root: Path | None = None
        self._type_filter: str = ""
        self._current_date_path: Path | None = None
        self._history_dialog: InboxHistoryDialog | None = None
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Header (MainView style) + History button
        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(12, 12, 12, 12)
        hlay.setSpacing(12)
        self._context_title = QLabel("Inbox", header)
        self._context_title.setObjectName("MainViewContextTitle")
        self._context_title.setFont(monos_font("Inter", 16, QFont.Weight.Bold))
        self._type_badge = QWidget(header)
        self._type_badge.setObjectName("MainViewTypeBadge")
        self._type_badge.setAttribute(Qt.WA_StyledBackground, True)
        badge_lay = QHBoxLayout(self._type_badge)
        badge_lay.setContentsMargins(8, 4, 10, 4)
        badge_lay.setSpacing(6)
        self._type_icon = QLabel(self._type_badge)
        self._type_icon.setFixedSize(16, 16)
        self._type_label = QLabel(self._type_badge)
        self._type_label.setObjectName("MainViewTypeBadgeLabel")
        self._type_label.setFont(monos_font("Inter", 13, QFont.Weight.Bold))
        badge_lay.addWidget(self._type_icon, 0, Qt.AlignVCenter)
        badge_lay.addWidget(self._type_label, 0, Qt.AlignVCenter)
        hlay.addWidget(self._context_title, 0, Qt.AlignVCenter)
        hlay.addWidget(self._type_badge, 0, Qt.AlignVCenter)
        hlay.addStretch(1)
        history_btn = QPushButton("History", header)
        history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        hist_icon = lucide_icon("layers", size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        if not hist_icon.isNull():
            history_btn.setIcon(hist_icon)
        history_btn.clicked.connect(self._on_history_clicked)
        hlay.addWidget(history_btn, 0)
        root_lay.addWidget(header, 0)

        # Single column: list | tree
        self._left_stack = QStackedWidget(self)
        self._date_list = _InboxDateFolderList(self)
        self._date_list.date_folder_clicked.connect(self._enter_date_folder)
        self._left_stack.addWidget(self._date_list)
        self._tree_pane: InboxTreePane | None = None
        self._left_stack.addWidget(QWidget(self))  # placeholder for tree, replaced on first enter
        root_lay.addWidget(self._left_stack, 1)
        self._update_type_badge()

    def _on_history_clicked(self) -> None:
        if self._history_dialog is None:
            self._history_dialog = InboxHistoryDialog(
                self._project_root,
                self._type_filter or "",
                self.window(),
            )
        self._history_dialog.set_context(self._project_root, self._type_filter or "")
        self._history_dialog.show()
        self._history_dialog.raise_()
        self._history_dialog.activateWindow()

    def _update_type_badge(self) -> None:
        t = (self._type_filter or "").strip()
        if not t:
            self._type_badge.setVisible(False)
            return
        label = t.replace("_", " ").title()
        icon = lucide_icon("inbox", size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        self._type_icon.setPixmap(icon.pixmap(16, 16))
        self._type_label.setText(label.upper())
        self._type_badge.setVisible(True)

    def set_project_root(self, path: Path | None) -> None:
        self._project_root = Path(path) if path else None
        self._date_list.set_content(self._project_root, self._type_filter)

    def set_type_filter(self, source_type: str) -> None:
        self._type_filter = (source_type or "").strip().lower()
        self._update_type_badge()
        self._date_list.set_content(self._project_root, self._type_filter)

    def _on_tree_selection(self, path) -> None:
        self.tree_selection_changed.emit(path)
        paths = []
        if self._tree_pane is not None:
            paths = [p for p in self._tree_pane.get_selected_paths() if p.exists()]
        self.tree_distribute_paths_changed.emit(paths)

    def _enter_date_folder(self, path: Path) -> None:
        self._current_date_path = path
        if self._tree_pane is None:
            self._tree_pane = InboxTreePane(path, self)
            self._tree_pane.back_requested.connect(self._back_to_list)
            self._tree_pane.tree_selection_changed.connect(self._on_tree_selection)
            self._tree_pane.open_folder_requested.connect(self.open_folder_requested.emit)
            self._left_stack.removeWidget(self._left_stack.widget(1))
            self._left_stack.addWidget(self._tree_pane)
        else:
            self._tree_pane.set_date_folder_path(path)
        self._left_stack.setCurrentIndex(1)

    def _back_to_list(self) -> None:
        self._current_date_path = None
        self._left_stack.setCurrentIndex(0)
        self.tree_selection_changed.emit(None)
        self.tree_distribute_paths_changed.emit([])

    def refresh_history_dialog_if_open(self) -> None:
        """Called after distribute: refresh History dialog if it is open."""
        if self._history_dialog is not None and self._history_dialog.isVisible():
            self._history_dialog.set_context(self._project_root, self._type_filter or "")

    def is_showing_tree(self) -> bool:
        return self._left_stack.currentIndex() == 1

    def current_date_folder_path(self) -> Path | None:
        return self._current_date_path

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths: list[Path] = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.exists():
                    paths.append(p)
        event.acceptProposedAction()
        if paths:
            self.drop_requested.emit(paths)
