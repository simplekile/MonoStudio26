"""Marker list panel for video preview dialog."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QInputDialog,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.video_media import (
    ListSortMode,
    TimeDisplayMode,
    VideoReviewMarker,
    format_frame_label,
    format_position_display,
    marker_is_synced,
    sort_video_markers,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu, monos_font

_SORT_TIMELINE = "timeline"
_SORT_NAME = "name"
_SORT_MODIFIED = "modified"

_MARKER_COLOR = "#f472b6"


class _MarkerListRowWidget(QWidget):
    _ROW_H = 44

    def __init__(
        self,
        marker: VideoReviewMarker,
        fps: float,
        *,
        display_mode: TimeDisplayMode = "timecode",
        synced: bool = False,
        list_widget: QListWidget | None = None,
        list_item: QListWidgetItem | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._list_widget = list_widget
        self._list_item = list_item
        self.setObjectName("VideoPreviewMarkerRow")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        diamond = QLabel("◆", self)
        diamond.setStyleSheet(f"color: {_MARKER_COLOR}; font-size: 11px;")
        diamond.setFixedWidth(12)
        root.addWidget(diamond, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        name_text = (marker.label or "").strip() or "Untitled"
        name = QLabel(name_text, self)
        name.setObjectName("VideoPreviewRangeRowName")
        name.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        text_col.addWidget(name)

        pos = format_position_display(marker.frame, fps, mode=display_mode)
        detail = QLabel(pos, self)
        detail.setObjectName("VideoPreviewRangeRowDetail")
        detail.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        text_col.addWidget(detail)
        root.addLayout(text_col, 1)

        if synced:
            sync_lab = QLabel(self)
            sync_lab.setPixmap(
                lucide_icon("sync", size=12, color_hex="#4ade80").pixmap(12, 12)
            )
            sync_lab.setFixedSize(12, 12)
            sync_lab.setToolTip("Matches project sidecar")
            root.addWidget(sync_lab, 0, Qt.AlignmentFlag.AlignTop)
        else:
            local_lab = QLabel(self)
            local_lab.setPixmap(
                lucide_icon("local", size=12, color_hex="#fbbf24").pixmap(12, 12)
            )
            local_lab.setFixedSize(12, 12)
            local_lab.setToolTip("Changed locally — use Sync to save")
            root.addWidget(local_lab, 0, Qt.AlignmentFlag.AlignTop)

    @classmethod
    def size_hint(cls) -> QSize:
        return QSize(0, cls._ROW_H)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._list_widget is not None
            and self._list_item is not None
        ):
            row = self._list_widget.row(self._list_item)
            if row >= 0:
                self._list_widget.setCurrentRow(row)
        super().mousePressEvent(event)


class VideoMarkerListWidget(QWidget):
    marker_selected = Signal(str)
    marker_deselected = Signal()
    marker_delete_requested = Signal(str)
    marker_delete_all_requested = Signal()
    marker_rename_requested = Signal(str, str)
    sort_mode_changed = Signal(str)
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewMarkerPanel")
        self._fps = 24.0
        self._sort_mode: ListSortMode = "timeline"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.addStretch(1)
        self._sort_combo = QComboBox(self)
        self._sort_combo.setObjectName("VideoPreviewTimeDisplayCombo")
        self._sort_combo.addItem("Timeline", _SORT_TIMELINE)
        self._sort_combo.addItem("Name", _SORT_NAME)
        self._sort_combo.addItem("Modified", _SORT_MODIFIED)
        self._sort_combo.setToolTip("List sort order for , . navigation")
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        header.addWidget(self._sort_combo)
        lay.addLayout(header)

        self._list = QListWidget(self)
        self._list.setObjectName("VideoPreviewRangeList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setSpacing(2)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.viewport().installEventFilter(self)
        lay.addWidget(self._list, 1)

        self._placeholder = QLabel(
            "K — add marker at playhead · , . — jump markers",
            self,
        )
        self._placeholder.setObjectName("DialogHint")
        self._placeholder.setWordWrap(True)
        self._placeholder.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        lay.addWidget(self._placeholder, 0)

        self._btn_export = QPushButton("Export PNG…", self)
        self._btn_export.setObjectName("DialogSecondaryButton")
        self._btn_export.setIcon(
            lucide_icon("download", size=16, color_hex=MONOS_COLORS["text_label"])
        )
        self._btn_export.clicked.connect(self.export_requested.emit)
        lay.addWidget(self._btn_export, 0)

        self._markers: list[VideoReviewMarker] = []
        self._published_markers: list[VideoReviewMarker] = []
        self._active_id: str | None = None
        self._display_mode: TimeDisplayMode = "timecode"
        self._block_row_signal = False

    def sort_mode(self) -> ListSortMode:
        return self._sort_mode

    def set_sort_mode(self, mode: ListSortMode) -> None:
        if mode not in (_SORT_TIMELINE, _SORT_NAME, _SORT_MODIFIED):
            return
        self._sort_mode = mode
        idx = self._sort_combo.findData(mode)
        if idx >= 0:
            self._sort_combo.blockSignals(True)
            self._sort_combo.setCurrentIndex(idx)
            self._sort_combo.blockSignals(False)

    def set_fps(self, fps: float) -> None:
        fps = max(1e-6, float(fps))
        if fps == self._fps:
            return
        self._fps = fps
        if self._markers:
            self.set_markers(self._markers, active_id=self._active_id)

    def set_display_mode(self, mode: TimeDisplayMode) -> None:
        if mode not in ("frame", "timecode") or mode == self._display_mode:
            return
        self._display_mode = mode
        if self._markers:
            self.set_markers(self._markers, active_id=self._active_id)

    def refresh_display(self) -> None:
        self.set_markers(self._markers, active_id=self._active_id)

    def set_published_markers(self, published: list[VideoReviewMarker]) -> None:
        pub = list(published)
        if pub == self._published_markers:
            return
        self._published_markers = pub
        if self._markers:
            self.set_markers(self._markers, active_id=self._active_id)

    def ordered_markers(self) -> list[VideoReviewMarker]:
        return sort_video_markers(self._markers, self._sort_mode)

    def _list_marker_ids(self) -> list[str]:
        ids: list[str] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None:
                continue
            mid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(mid, str):
                ids.append(mid)
        return ids

    def _apply_active_selection(self, active_id: str | None) -> None:
        self._block_row_signal = True
        if active_id:
            for row in range(self._list.count()):
                item = self._list.item(row)
                if item is None:
                    continue
                if item.data(Qt.ItemDataRole.UserRole) == active_id:
                    if self._list.currentRow() != row:
                        self._list.setCurrentRow(row)
                    break
        else:
            self._list.clearSelection()
            self._list.setCurrentRow(-1)
        self._block_row_signal = False

    def set_markers(self, markers: list[VideoReviewMarker], *, active_id: str | None) -> None:
        new_markers = list(markers)
        ordered = sort_video_markers(new_markers, self._sort_mode)
        ordered_ids = [m.id for m in ordered]
        list_ids = self._list_marker_ids()
        if new_markers == self._markers and ordered_ids == list_ids:
            self._active_id = active_id
            self._apply_active_selection(active_id)
            return

        scroll_pos = self._list.verticalScrollBar().value()
        self._markers = new_markers
        self._active_id = active_id
        self._placeholder.setVisible(len(ordered) == 0)
        self._btn_export.setEnabled(len(ordered) > 0)
        self._block_row_signal = True
        self._list.clear()
        row_hint = _MarkerListRowWidget.size_hint()
        for marker in ordered:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, marker.id)
            item.setSizeHint(row_hint)
            self._list.addItem(item)
            synced = marker_is_synced(marker, self._published_markers)
            self._list.setItemWidget(
                item,
                _MarkerListRowWidget(
                    marker,
                    self._fps,
                    display_mode=self._display_mode,
                    synced=synced,
                    list_widget=self._list,
                    list_item=item,
                    parent=self._list,
                ),
            )
        if active_id:
            row = next((i for i, m in enumerate(ordered) if m.id == active_id), -1)
            self._list.setCurrentRow(row)
            if row >= 0:
                item = self._list.item(row)
                if item is not None:
                    self._list.scrollToItem(
                        item,
                        QAbstractItemView.ScrollHint.EnsureVisible,
                    )
        else:
            self._list.clearSelection()
            self._list.setCurrentRow(-1)
            self._list.verticalScrollBar().setValue(scroll_pos)
        self._block_row_signal = False

    def _on_sort_changed(self, _index: int) -> None:
        mode = self._sort_combo.currentData()
        if not isinstance(mode, str) or mode == self._sort_mode:
            return
        self._sort_mode = mode  # type: ignore[assignment]
        self.set_markers(self._markers, active_id=self._active_id)
        self.sort_mode_changed.emit(self._sort_mode)

    def _on_row_changed(self, row: int) -> None:
        if self._block_row_signal:
            return
        if row < 0:
            if self._active_id is not None:
                self.marker_deselected.emit()
            return
        item = self._list.item(row)
        if item is None:
            return
        mid = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(mid, str):
            self.marker_selected.emit(mid)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        mid = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(mid, str):
            return
        marker = next((m for m in self._markers if m.id == mid), None)
        if marker is None:
            return
        self.marker_selected.emit(mid)

    def _marker_at_pos(self, pos) -> VideoReviewMarker | None:
        item = self._list.itemAt(pos)
        if item is None:
            return None
        mid = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(mid, str):
            return None
        return next((m for m in self._markers if m.id == mid), None)

    def _on_context_menu(self, pos) -> None:
        marker = self._marker_at_pos(pos)
        if marker is None and not self._markers:
            return
        menu = MonosMenu(self)
        act_go = act_rename = act_delete = act_delete_all = None
        if marker is not None:
            act_go = menu.addAction(f"Go to {format_frame_label(marker.frame)}")
            act_rename = menu.addAction("Rename…")
            menu.addSeparator()
            act_delete = menu.addAction("Delete")
            act_delete.setProperty("danger-action", True)
        if self._markers:
            if marker is not None:
                menu.addSeparator()
            act_delete_all = menu.addAction("Delete all…")
            act_delete_all.setProperty("danger-action", True)
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if marker is not None:
            if chosen is act_go:
                self.marker_selected.emit(marker.id)
            elif chosen is act_rename:
                text, ok = QInputDialog.getText(self, "Rename marker", "Label:", text=marker.label)
                if ok:
                    self.marker_rename_requested.emit(marker.id, text.strip())
            elif chosen is act_delete:
                self.marker_delete_requested.emit(marker.id)
        if chosen is act_delete_all:
            self.marker_delete_all_requested.emit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._list.viewport():
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and isinstance(event, QMouseEvent)
                and event.button() == Qt.MouseButton.LeftButton
                and self._list.itemAt(event.position().toPoint()) is None
            ):
                self._block_row_signal = True
                self._list.clearSelection()
                self._list.setCurrentRow(-1)
                self._block_row_signal = False
                if self._active_id is not None:
                    self.marker_deselected.emit()
                return False
        return super().eventFilter(watched, event)
