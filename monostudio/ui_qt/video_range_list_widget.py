"""Range list panel for video preview dialog."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.video_media import (
    ListSortMode,
    TimeDisplayMode,
    VideoFrameRange,
    format_frame_label,
    format_position_display,
    format_range_span_display,
    format_timecode,
    range_is_synced,
    sort_video_ranges,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosMenu, monos_font
from monostudio.ui_qt.video_range_colors import range_color_hex

_SYNC_COLOR = "#4ade80"
_LOCAL_COLOR = "#fbbf24"
_SYNC_ICON_PX = 12

_SORT_TIMELINE = "timeline"
_SORT_NAME = "name"
_SORT_MODIFIED = "modified"


def _apply_sync_label(lab: QLabel, *, synced: bool) -> None:
    lab.setObjectName(
        "VideoPreviewRangeRowSynced" if synced else "VideoPreviewRangeRowLocal"
    )
    color = _SYNC_COLOR if synced else _LOCAL_COLOR
    icon_name = "sync" if synced else "local"
    lab.setPixmap(lucide_icon(icon_name, size=_SYNC_ICON_PX, color_hex=color).pixmap(
        _SYNC_ICON_PX, _SYNC_ICON_PX
    ))
    lab.setFixedSize(_SYNC_ICON_PX, _SYNC_ICON_PX)
    lab.setToolTip(
        "Matches project sidecar" if synced else "Changed locally — use Sync to save"
    )


def _sync_status_label(parent: QWidget, *, synced: bool) -> QLabel:
    lab = QLabel(parent)
    _apply_sync_label(lab, synced=synced)
    return lab


class _RangeListRowWidget(QWidget):
    """Single range row: color dot + name (top) + frames/time (bottom)."""

    _ROW_H = 52

    def __init__(
        self,
        rng: VideoFrameRange,
        fps: float,
        *,
        display_mode: TimeDisplayMode = "timecode",
        synced: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewRangeRow")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        dot = QLabel(self)
        dot.setFixedSize(8, 8)
        dot.setObjectName("VideoPreviewRangeRowDot")
        color = range_color_hex(rng.id)
        dot.setStyleSheet(f"background-color: {color}; border-radius: 4px; border: none;")
        root.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        root.addSpacing(2)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        name_text = (rng.label or "").strip() or "Untitled"
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        name = QLabel(name_text, self)
        name.setObjectName("VideoPreviewRangeRowName")
        name.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        name_row.addWidget(name, 1)
        self._sync_label = _sync_status_label(self, synced=synced)
        name_row.addWidget(self._sync_label, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        text_col.addLayout(name_row)

        detail = QLabel(
            format_range_span_display(rng, fps, mode=display_mode),
            self,
        )
        detail.setObjectName("VideoPreviewRangeRowDetail")
        detail.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        text_col.addWidget(detail)

        root.addLayout(text_col, 1)

    def set_synced(self, synced: bool) -> None:
        _apply_sync_label(self._sync_label, synced=synced)

    @classmethod
    def size_hint(cls) -> QSize:
        return QSize(0, cls._ROW_H)


class VideoRangeListWidget(QWidget):
    """List of marked frame ranges with selection."""

    range_selected = Signal(str, bool)  # range id, shift_held
    range_delete_requested = Signal(str)
    range_delete_all_requested = Signal()
    range_duplicate_requested = Signal(str)
    range_rename_requested = Signal(str, str)  # id, new label
    go_to_in_requested = Signal(str)
    go_to_out_requested = Signal(str)
    sort_mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewRangePanel")
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
        self._sort_combo.setToolTip("List sort order for [ ] navigation")
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
        self._list.installEventFilter(self)
        lay.addWidget(self._list, 1)

        self._placeholder = QLabel(
            "Click overlap to cycle · E or double-click (single range) to edit",
            self,
        )
        self._placeholder.setObjectName("DialogHint")
        self._placeholder.setWordWrap(True)
        self._placeholder.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        lay.addWidget(self._placeholder, 0)

        self._draft = QLabel("", self)
        self._draft.setObjectName("VideoPreviewDraftHint")
        self._draft.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        lay.addWidget(self._draft, 0)

        self._ranges: list[VideoFrameRange] = []
        self._published_ranges: list[VideoFrameRange] = []
        self._active_id: str | None = None
        self._display_mode: TimeDisplayMode = "timecode"
        self._draft_in: int | None = None
        self._draft_out: int | None = None
        self._block_row_signal = False
        self._last_click_shift = False

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._list and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._last_click_shift = bool(
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                )
        return super().eventFilter(obj, event)

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

    def ordered_ranges(self) -> list[VideoFrameRange]:
        return sort_video_ranges(self._ranges, self._sort_mode)

    def _on_sort_changed(self, _index: int) -> None:
        mode = self._sort_combo.currentData()
        if not isinstance(mode, str) or mode == self._sort_mode:
            return
        self._sort_mode = mode  # type: ignore[assignment]
        self.set_ranges(self._ranges, active_id=self._active_id)
        self.sort_mode_changed.emit(self._sort_mode)

    def set_fps(self, fps: float) -> None:
        fps = max(1e-6, float(fps))
        if fps == self._fps:
            return
        self._fps = fps
        if self._ranges:
            self.set_ranges(self._ranges, active_id=self._active_id)

    def set_display_mode(self, mode: TimeDisplayMode) -> None:
        if mode not in ("frame", "timecode") or mode == self._display_mode:
            return
        self._display_mode = mode
        if self._ranges:
            self.set_ranges(self._ranges, active_id=self._active_id)

    def refresh_display(self) -> None:
        self.set_ranges(self._ranges, active_id=self._active_id)
        self.set_draft_hint(self._draft_in, self._draft_out)

    def set_published_ranges(self, published: list[VideoFrameRange]) -> None:
        pub = list(published)
        if pub == self._published_ranges:
            return
        self._published_ranges = pub
        self._refresh_sync_icons()

    def _refresh_sync_icons(self) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None:
                continue
            rid = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(rid, str):
                continue
            rng = next((r for r in self._ranges if r.id == rid), None)
            if rng is None:
                continue
            row_w = self._list.itemWidget(item)
            if isinstance(row_w, _RangeListRowWidget):
                row_w.set_synced(range_is_synced(rng, self._published_ranges))

    def ordered_ranges(self) -> list[VideoFrameRange]:
        return sort_video_ranges(self._ranges, self._sort_mode)

    def _list_range_ids(self) -> list[str]:
        ids: list[str] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None:
                continue
            rid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(rid, str):
                ids.append(rid)
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

    def set_ranges(self, ranges: list[VideoFrameRange], *, active_id: str | None) -> None:
        new_ranges = list(ranges)
        ordered = sort_video_ranges(new_ranges, self._sort_mode)
        ordered_ids = [r.id for r in ordered]
        list_ids = self._list_range_ids()
        if new_ranges == self._ranges and ordered_ids == list_ids:
            self._active_id = active_id
            self._apply_active_selection(active_id)
            return

        scroll_pos = self._list.verticalScrollBar().value()
        self._ranges = new_ranges
        self._active_id = active_id
        self._placeholder.setVisible(len(ordered) == 0)
        self._block_row_signal = True
        self._list.clear()
        row_hint = _RangeListRowWidget.size_hint()
        for rng in ordered:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, rng.id)
            item.setSizeHint(row_hint)
            self._list.addItem(item)
            synced = range_is_synced(rng, self._published_ranges)
            self._list.setItemWidget(
                item,
                _RangeListRowWidget(
                    rng,
                    self._fps,
                    display_mode=self._display_mode,
                    synced=synced,
                    parent=self._list,
                ),
            )
        if active_id:
            row = next((i for i, r in enumerate(ordered) if r.id == active_id), -1)
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

    def set_draft_hint(self, draft_in: int | None, draft_out: int | None) -> None:
        self._draft_in = draft_in
        self._draft_out = draft_out
        if draft_in is None and draft_out is None:
            self._draft.setText("")
            return
        if draft_in is not None and draft_out is not None and draft_in <= draft_out:
            if self._display_mode == "frame":
                self._draft.setText(
                    f"Draft: {format_frame_label(draft_in)}–{format_frame_label(draft_out)}"
                )
            else:
                tc_in = format_timecode(draft_in / self._fps, fps=self._fps)
                tc_out = format_timecode(draft_out / self._fps, fps=self._fps)
                self._draft.setText(f"Draft: {tc_in}–{tc_out}")
        elif draft_in is not None:
            in_txt = format_position_display(draft_in, self._fps, mode=self._display_mode)
            self._draft.setText(f"Draft In: {in_txt} · Out —")
        elif draft_out is not None:
            out_txt = format_position_display(draft_out, self._fps, mode=self._display_mode)
            self._draft.setText(f"Draft Out: {out_txt} · In —")
        else:
            self._draft.setText("")

    def select_range_id(self, range_id: str | None) -> None:
        self._active_id = range_id
        self._apply_active_selection(range_id)

    def _range_at_row(self, row: int) -> VideoFrameRange | None:
        item = self._list.item(row)
        if item is None:
            return None
        rid = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(rid, str):
            return None
        return next((r for r in self._ranges if r.id == rid), None)

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        rng: VideoFrameRange | None = None
        if item is not None:
            rng = self._range_at_row(self._list.row(item))
        if rng is None and not self._ranges:
            return
        menu = MonosMenu(self)
        act_in = act_out = act_copy = act_rename = act_dup = act_del = act_del_all = None
        if rng is not None:
            act_in = menu.addAction("Go to In")
            act_out = menu.addAction("Go to Out")
            menu.addSeparator()
            act_copy = menu.addAction("Copy range text")
            act_rename = menu.addAction("Rename…")
            act_dup = menu.addAction("Duplicate")
            menu.addSeparator()
            act_del = menu.addAction("Delete")
            act_del.setProperty("danger-action", True)
        if self._ranges:
            if rng is not None:
                menu.addSeparator()
            act_del_all = menu.addAction("Delete all…")
            act_del_all.setProperty("danger-action", True)
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is None:
            return
        if rng is not None:
            if chosen == act_in:
                self.go_to_in_requested.emit(rng.id)
            elif chosen == act_out:
                self.go_to_out_requested.emit(rng.id)
            elif chosen == act_copy:
                text = format_range_span_display(rng, self._fps, mode=self._display_mode)
                cb = QGuiApplication.clipboard()
                if cb:
                    cb.setText(text)
            elif chosen == act_rename:
                self._prompt_rename(rng)
            elif chosen == act_dup:
                self.range_duplicate_requested.emit(rng.id)
            elif chosen == act_del:
                self.range_delete_requested.emit(rng.id)
        if chosen == act_del_all:
            self.range_delete_all_requested.emit()

    def _prompt_rename(self, rng: VideoFrameRange) -> None:
        text, ok = QInputDialog.getText(self, "Rename range", "Name", text=rng.label or "")
        if ok:
            self.range_rename_requested.emit(rng.id, (text or "").strip()[:80])

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        rid = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(rid, str):
            return
        rng = next((r for r in self._ranges if r.id == rid), None)
        if rng is not None:
            self._prompt_rename(rng)

    def _on_row_changed(self, row: int) -> None:
        if self._block_row_signal or row < 0:
            return
        item = self._list.item(row)
        if item is None:
            return
        rid = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(rid, str):
            shift = self._last_click_shift
            self._last_click_shift = False
            self.range_selected.emit(rid, shift)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F2:
            item = self._list.currentItem()
            if item is not None:
                rid = item.data(Qt.ItemDataRole.UserRole)
                rng = next((r for r in self._ranges if r.id == rid), None) if isinstance(rid, str) else None
                if rng is not None:
                    self._prompt_rename(rng)
                    event.accept()
                    return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            item = self._list.currentItem()
            if item is not None:
                rid = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(rid, str):
                    self.range_delete_requested.emit(rid)
                    event.accept()
                    return
        super().keyPressEvent(event)
