"""
Outbox page: header + unified file tree (client/freelancer → date folders → files).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.delivery_reader import ensure_delivery_source_folders, get_delivery_root
from monostudio.ui_qt.inbox_page_toolbar import bind_explorer_view_mode_tab_shortcut
from monostudio.ui_qt.inbox_split_view import InboxOutboxTitleRow, InboxTreePane
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.outbox_history_dialog import OutboxHistoryDialog
from monostudio.ui_qt.style import MONOS_COLORS


def _header_tool_button(parent: QWidget, text: str, icon_name: str, *, primary: bool = False) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("InboxPrimaryButton" if primary else "InboxHeaderButton")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIconSize(QSize(16, 16))
    color = "#ffffff" if primary else MONOS_COLORS.get("text_label", "#a1a1aa")
    ic = lucide_icon(icon_name, size=16, color_hex=color)
    if not ic.isNull():
        btn.setIcon(ic)
    return btn


def _normalize_source_type(source_type: str) -> str:
    key = (source_type or "client").strip().lower()
    return key if key in ("client", "freelancer") else "client"


def _source_folder_path(project_root: Path | None, source_type: str) -> Path | None:
    if project_root is None:
        return None
    source = _normalize_source_type(source_type)
    root = get_delivery_root(project_root)
    candidate = root / source
    if candidate.is_dir():
        return candidate
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return root if root.is_dir() else None


class OutboxPageWidget(QWidget):
    """Delivery (send out): client/freelancer recipient trees under outbox/delivery/."""

    tree_selection_changed = Signal(object)  # Path | None
    tree_distribute_paths_changed = Signal(object)  # list[Path]
    open_folder_requested = Signal(object)  # Path
    drop_requested = Signal(object, object, bool)  # list[Path], drop target, copy_only
    import_requested = Signal(object)  # Path | None
    date_folder_entered = Signal(str, object)  # (type_filter, browse path)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(False)
        self._project_root: Path | None = None
        self._type_filter: str = ""
        self._tree_state_cache: dict[str, dict] = {}
        self._history_dialog: OutboxHistoryDialog | None = None
        self._tree_pane: InboxTreePane | None = None

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header_v = QVBoxLayout(header)
        header_v.setContentsMargins(12, 12, 12, 12)
        header_v.setSpacing(6)

        top_row = QWidget(header)
        hlay = QHBoxLayout(top_row)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(12)

        self._title_row = InboxOutboxTitleRow("Delivery", root_icon="send", parent=top_row)
        hlay.addWidget(self._title_row, 0, Qt.AlignmentFlag.AlignVCenter)
        hlay.addStretch(1)

        self._history_btn = _header_tool_button(top_row, "History", "layers")
        self._history_btn.clicked.connect(self._on_history_clicked)
        hlay.addWidget(self._history_btn, 0)

        self._open_folder_btn = _header_tool_button(top_row, "Open folder", "folder-open")
        self._open_folder_btn.clicked.connect(self._on_open_folder_clicked)
        hlay.addWidget(self._open_folder_btn, 0)

        self._import_btn = _header_tool_button(top_row, "Import", "upload", primary=True)
        self._import_btn.clicked.connect(self._on_import_clicked)
        hlay.addWidget(self._import_btn, 0)
        header_v.addWidget(top_row, 0)

        self._path_bar_row = QWidget(header)
        self._path_bar_row.setObjectName("InboxPathBarRow")
        self._path_bar_lay = QHBoxLayout(self._path_bar_row)
        self._path_bar_lay.setContentsMargins(0, 0, 0, 0)
        self._path_bar_lay.setSpacing(12)
        self._path_bar_row.hide()
        header_v.addWidget(self._path_bar_row, 0)

        root_lay.addWidget(header, 0)

        self._content_host = QWidget(self)
        self._content_lay = QVBoxLayout(self._content_host)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(0)
        root_lay.addWidget(self._content_host, 1)
        self._refresh_chrome()
        self._bound_hotkeys = bind_explorer_view_mode_tab_shortcut(self, lambda: self._tree_pane)

    def _mount_explorer_path_bar(self) -> None:
        if self._tree_pane is None:
            self._path_bar_row.hide()
            return
        bar = self._tree_pane.explorer_path_bar()
        toolbar = self._tree_pane.explorer_toolbar()
        if bar is None:
            self._path_bar_row.hide()
            return
        if bar.parent() is not self._path_bar_row:
            bar.setParent(self._path_bar_row)
        if self._path_bar_lay.indexOf(bar) < 0:
            self._path_bar_lay.addWidget(bar, 1)
        if toolbar is not None:
            toolbar.set_inline(True)
            if toolbar.parent() is not self._path_bar_row:
                toolbar.setParent(self._path_bar_row)
            if self._path_bar_lay.indexOf(toolbar) < 0:
                self._path_bar_lay.addWidget(toolbar, 0, Qt.AlignmentFlag.AlignVCenter)
            toolbar.show()
        self._path_bar_row.show()

    def _refresh_chrome(self) -> None:
        self._title_row.set_context(
            type_filter=self._type_filter,
            date_path=None,
            unified_tree=True,
        )
        self._history_btn.setVisible(True)
        self._open_folder_btn.setVisible(True)
        self._mount_explorer_path_bar()

    def _tree_state_key(self, source_type: str) -> str:
        return _normalize_source_type(source_type)

    def _ensure_tree_pane(self) -> None:
        root = _source_folder_path(self._project_root, self._type_filter)
        if root is None:
            return
        if self._tree_pane is None:
            self._tree_pane = InboxTreePane(
                root,
                self._content_host,
                show_history_action=True,
                show_toolbar=True,
                view_settings_key="delivery/view_mode",
                source_filter=self._type_filter,
            )
            self._tree_pane.tree_selection_changed.connect(self._on_tree_selection)
            self._tree_pane.open_folder_requested.connect(self.open_folder_requested.emit)
            self._tree_pane.import_requested.connect(self._on_tree_import_requested)
            self._tree_pane.history_requested.connect(self._on_history_clicked)
            self._tree_pane.browse_path_changed.connect(self._on_browse_path_changed)
            self._tree_pane.external_drop_requested.connect(self.drop_requested.emit)
            self._content_lay.addWidget(self._tree_pane, 1)
        else:
            self._tree_pane.set_date_folder_path(root)
            self._tree_pane.set_chrome_context(self._type_filter, None)
        key = self._tree_state_key(self._type_filter)
        saved = self._tree_state_cache.get(key)
        if saved:
            self._tree_pane.set_tree_state(saved)
        self._refresh_chrome()

    def _on_browse_path_changed(self, path: Path) -> None:
        self.date_folder_entered.emit(self._type_filter or "", path)

    def _outbox_open_target(self) -> Path | None:
        if self._tree_pane is not None:
            return self._tree_pane.current_browse_path()
        return _source_folder_path(self._project_root, self._type_filter)

    def _on_open_folder_clicked(self) -> None:
        target = self._outbox_open_target()
        if target is not None:
            self.open_folder_requested.emit(target)

    def _on_import_clicked(self) -> None:
        self.import_requested.emit(self.current_date_folder_path())

    def _on_tree_import_requested(self) -> None:
        self.import_requested.emit(self.current_date_folder_path())

    def _on_history_clicked(self) -> None:
        if self._history_dialog is None:
            self._history_dialog = OutboxHistoryDialog(
                self._project_root,
                self._type_filter or "",
                self.window(),
            )
        self._history_dialog.set_context(self._project_root, self._type_filter or "")
        self._history_dialog.show()
        self._history_dialog.raise_()
        self._history_dialog.activateWindow()

    def set_project_root(self, path: Path | None) -> None:
        self._project_root = Path(path) if path else None
        if self._project_root is not None:
            ensure_delivery_source_folders(self._project_root)
        self._ensure_tree_pane()
        if self._tree_pane is not None:
            self._tree_pane.refresh_content()

    def set_type_filter(self, source_type: str) -> None:
        new_type = _normalize_source_type(source_type)
        type_changed = new_type != self._type_filter
        if type_changed and self._tree_pane is not None:
            self._tree_state_cache[self._tree_state_key(self._type_filter)] = self._tree_pane.get_tree_state()
        self._type_filter = new_type
        self._ensure_tree_pane()
        self._refresh_chrome()

    def _on_tree_selection(self, path) -> None:
        self.tree_selection_changed.emit(path)
        paths = []
        if self._tree_pane is not None:
            paths = [p for p in self._tree_pane.get_selected_paths() if p.exists()]
        self.tree_distribute_paths_changed.emit(paths)

    def restore_browse_path(self, path: Path) -> None:
        """Restore last browsed folder within the current source tree."""
        self._ensure_tree_pane()
        if self._tree_pane is not None:
            self._tree_pane.navigate_to_path(path)

    def refresh_tree(self) -> None:
        if self._tree_pane is not None:
            self._tree_pane.refresh_content()

    def refresh_history_dialog_if_open(self) -> None:
        if self._history_dialog is not None and self._history_dialog.isVisible():
            self._history_dialog.set_context(self._project_root, self._type_filter or "")

    def is_showing_tree(self) -> bool:
        return self._tree_pane is not None

    def current_date_folder_path(self) -> Path | None:
        if self._tree_pane is None:
            return None
        return self._tree_pane.current_browse_path()

