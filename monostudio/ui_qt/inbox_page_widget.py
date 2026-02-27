"""
Inbox page: header (MainView style) + single column (list date folders | tree).
No mapping pane; distribute from tree selection. History via dialog (button).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtCore import QSize
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
        self._stack = QStackedWidget(self)
        # Empty state: large import icon + muted hint for both import methods
        self._empty_widget = self._make_empty_placeholder()
        self._stack.addWidget(self._empty_widget)
        self._list = QListWidget(self)
        self._list.setObjectName("InboxMappingList")
        self._list.setIconSize(QSize(20, 20))
        self._list.setSpacing(2)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._stack.addWidget(self._list)
        lay.addWidget(self._stack, 1)

    def _make_empty_placeholder(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("InboxDateListEmpty")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(24, 48, 24, 48)
        v.setSpacing(16)
        v.addStretch(1)
        icon = lucide_icon("upload", size=64, color_hex=MONOS_COLORS.get("text_meta", "#71717a"))
        icon_lb = QLabel(wrap)
        if not icon.isNull():
            icon_lb.setPixmap(icon.pixmap(64, 64))
        icon_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(icon_lb, 0, Qt.AlignmentFlag.AlignHCenter)
        line1 = QLabel("Drag and drop files or folders here", wrap)
        line1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line1.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 13px;")
        line1.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        v.addWidget(line1, 0, Qt.AlignmentFlag.AlignHCenter)
        line2 = QLabel("or use the Import button above", wrap)
        line2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line2.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; font-size: 13px;")
        line2.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        v.addWidget(line2, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(1)
        return wrap

    def set_content(self, project_root: Path | None, source_filter: str) -> None:
        self._project_root = project_root
        self._source_filter = (source_filter or "").strip().lower()
        self._list.clear()
        if not project_root or not project_root.is_dir():
            self._stack.setCurrentWidget(self._empty_widget)
            return
        if not self._source_filter:
            self._stack.setCurrentWidget(self._empty_widget)
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
                    date_icon = lucide_icon("calendar", size=20, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
                    for child in node.children:
                        if getattr(child, "is_dir", True) and getattr(child, "path", None):
                            it = QListWidgetItem(child.name or str(child.path))
                            it.setData(Qt.ItemDataRole.UserRole, str(child.path))
                            it.setToolTip(str(child.path))
                            if not date_icon.isNull():
                                it.setIcon(date_icon)
                            it.setSizeHint(QSize(0, 44))
                            self._list.addItem(it)
                break
        if self._list.count() == 0:
            self._stack.setCurrentWidget(self._empty_widget)
        else:
            self._stack.setCurrentWidget(self._list)

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
    Inbox page: (1) Header (Inbox + type badge + Import button); (2) Single column: list | tree.
    History in tree context menu. Signals: tree_selection_changed, tree_distribute_paths_changed, open_folder_requested, drop_requested, import_requested.
    """

    tree_selection_changed = Signal(object)  # Path | None
    tree_distribute_paths_changed = Signal(object)  # list[Path]
    open_folder_requested = Signal(object)  # Path
    drop_requested = Signal(object)  # list[Path]
    import_requested = Signal(object)  # Path | None (current date folder when in tree)
    date_folder_entered = Signal(str, object)  # (type_filter: str, path: Path)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._project_root: Path | None = None
        self._type_filter: str = ""
        self._current_date_path: Path | None = None
        self._tree_state_cache: dict[str, dict] = {}  # key = (type, path) -> tree state when switching Client/Freelancer
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
        import_btn = QPushButton("Import", header)
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_icon = lucide_icon("upload", size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        if not import_icon.isNull():
            import_btn.setIcon(import_icon)
        import_btn.clicked.connect(self._on_import_clicked)
        hlay.addWidget(import_btn, 0)
        root_lay.addWidget(header, 0)

        # Breadcrumb bar (visible when showing date list; tree pane has its own)
        self._date_breadcrumb_bar = self._make_date_list_breadcrumb()
        root_lay.addWidget(self._date_breadcrumb_bar, 0)

        # Single column: list | tree
        self._left_stack = QStackedWidget(self)
        self._date_list = _InboxDateFolderList(self)
        self._date_list.date_folder_clicked.connect(self._enter_date_folder)
        self._left_stack.addWidget(self._date_list)
        self._tree_pane: InboxTreePane | None = None
        self._left_stack.addWidget(QWidget(self))  # placeholder for tree, replaced on first enter
        root_lay.addWidget(self._left_stack, 1)
        self._update_type_badge()
        self._refresh_date_breadcrumb_visibility()

    def _on_import_clicked(self) -> None:
        """Header Import button: emit import_requested with current date folder (None when on list view)."""
        self.import_requested.emit(self._current_date_path)

    def _on_tree_import_requested(self) -> None:
        """Import from tree context menu: emit with current date folder."""
        self.import_requested.emit(self._current_date_path)

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

    def _make_date_list_breadcrumb(self) -> QWidget:
        """Breadcrumb bar for date list view: Inbox › [Type]. Same visual style as InboxTreePane breadcrumb."""
        bar = QWidget(self)
        bar.setObjectName("InboxDateBreadcrumbBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        wlay = QHBoxLayout(bar)
        wlay.setContentsMargins(12, 8, 12, 8)
        wlay.setSpacing(3)
        sep_style = "color: #71717a; font-size: 10px;"
        label_style = "color: #a1a1aa; font-size: 11px;"
        inbox_lb = QLabel("Inbox", bar)
        inbox_lb.setStyleSheet(label_style)
        inbox_lb.setFont(monos_font("Inter", 11))
        wlay.addWidget(inbox_lb, 0)
        self._date_breadcrumb_sep = QLabel("›", bar)
        self._date_breadcrumb_sep.setStyleSheet(sep_style)
        self._date_breadcrumb_sep.setFont(monos_font("Inter", 10))
        wlay.addWidget(self._date_breadcrumb_sep, 0)
        self._date_breadcrumb_type_label = QLabel("", bar)
        self._date_breadcrumb_type_label.setStyleSheet(label_style)
        self._date_breadcrumb_type_label.setFont(monos_font("Inter", 11))
        wlay.addWidget(self._date_breadcrumb_type_label, 0)
        wlay.addStretch(1)
        return bar

    def _refresh_date_breadcrumb_text(self) -> None:
        t = (self._type_filter or "").strip()
        if not t:
            self._date_breadcrumb_type_label.setText("")
            self._date_breadcrumb_sep.setVisible(False)
        else:
            label = (t.replace("_", " ").title() or t).strip()
            self._date_breadcrumb_type_label.setText(label)
            self._date_breadcrumb_sep.setVisible(True)

    def _refresh_date_breadcrumb_visibility(self) -> None:
        """Show breadcrumb bar when on date list (index 0), hide when on tree (index 1)."""
        self._date_breadcrumb_bar.setVisible(self._left_stack.currentIndex() == 0)

    def _update_type_badge(self) -> None:
        t = (self._type_filter or "").strip()
        self._refresh_date_breadcrumb_text()
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

    def _tree_state_key(self, source_type: str, path: Path) -> str:
        t = (source_type or "").strip().lower() or "client"
        return f"{t}/{path.resolve()}"

    def set_type_filter(self, source_type: str) -> None:
        new_type = (source_type or "").strip().lower()
        type_changed = new_type != self._type_filter
        if type_changed and self.is_showing_tree() and self._tree_pane is not None and self._current_date_path is not None:
            key = self._tree_state_key(self._type_filter, self._current_date_path)
            self._tree_state_cache[key] = self._tree_pane.get_tree_state()
        self._type_filter = new_type
        self._update_type_badge()
        self._date_list.set_content(self._project_root, self._type_filter)
        # Only leave tree view when user actually switched Client <-> Freelancer; keep tree when re-opening Inbox with same type
        if type_changed and self.is_showing_tree():
            self._back_to_list()

    def _on_tree_selection(self, path) -> None:
        self.tree_selection_changed.emit(path)
        paths = []
        if self._tree_pane is not None:
            paths = [p for p in self._tree_pane.get_selected_paths() if p.exists()]
        self.tree_distribute_paths_changed.emit(paths)

    def _enter_date_folder(self, path: Path) -> None:
        self._current_date_path = path
        self.date_folder_entered.emit(self._type_filter or "", path)
        if self._tree_pane is None:
            self._tree_pane = InboxTreePane(path, self, show_history_action=True)
            self._tree_pane.back_requested.connect(self._back_to_list)
            self._tree_pane.tree_selection_changed.connect(self._on_tree_selection)
            self._tree_pane.open_folder_requested.connect(self.open_folder_requested.emit)
            self._tree_pane.import_requested.connect(self._on_tree_import_requested)
            self._tree_pane.history_requested.connect(self._on_history_clicked)
            self._left_stack.removeWidget(self._left_stack.widget(1))
            self._left_stack.addWidget(self._tree_pane)
        else:
            self._tree_pane.set_date_folder_path(path)
        key = self._tree_state_key(self._type_filter or "client", path)
        saved = self._tree_state_cache.get(key)
        if saved:
            self._tree_pane.set_tree_state(saved)
        self._left_stack.setCurrentIndex(1)
        self._refresh_date_breadcrumb_visibility()

    def _back_to_list(self) -> None:
        self._current_date_path = None
        self._left_stack.setCurrentIndex(0)
        self._refresh_date_breadcrumb_visibility()
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
