"""Range list panel for video preview dialog."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.video_media import (
    TimeDisplayMode,
    VideoFrameRange,
    format_frame_label,
    format_position_display,
    format_range_span_display,
    format_timecode,
    range_is_synced,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosMenu, monos_font
from monostudio.ui_qt.video_range_colors import range_color_hex

_SYNC_COLOR = "#4ade80"
_LOCAL_COLOR = "#fbbf24"
_SYNC_ICON_PX = 12


def _sync_status_label(parent: QWidget, *, synced: bool) -> QLabel:
    lab = QLabel(parent)
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
        name_row.addWidget(_sync_status_label(self, synced=synced), 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        text_col.addLayout(name_row)

        detail = QLabel(
            format_range_span_display(rng, fps, mode=display_mode),
            self,
        )
        detail.setObjectName("VideoPreviewRangeRowDetail")
        detail.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        text_col.addWidget(detail)

        root.addLayout(text_col, 1)

    @classmethod
    def size_hint(cls) -> QSize:
        return QSize(0, cls._ROW_H)


class VideoRangeListWidget(QWidget):
    """List of marked frame ranges with selection."""

    range_selected = Signal(str)  # range id
    range_delete_requested = Signal(str)
    range_duplicate_requested = Signal(str)
    range_rename_requested = Signal(str, str)  # id, new label
    go_to_in_requested = Signal(str)
    go_to_out_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewRangePanel")
        self._fps = 24.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        title = QLabel("RANGES", self)
        title.setObjectName("VideoPreviewRangeTitle")
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        lay.addWidget(title, 0)

        self._list = QListWidget(self)
        self._list.setObjectName("VideoPreviewRangeList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setSpacing(2)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
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

    def set_fps(self, fps: float) -> None:
        self._fps = max(1e-6, float(fps))

    def set_display_mode(self, mode: TimeDisplayMode) -> None:
        if mode in ("frame", "timecode"):
            self._display_mode = mode

    def refresh_display(self) -> None:
        self.set_ranges(self._ranges, active_id=self._active_id)
        self.set_draft_hint(self._draft_in, self._draft_out)

    def set_published_ranges(self, published: list[VideoFrameRange]) -> None:
        self._published_ranges = list(published)

    def set_ranges(self, ranges: list[VideoFrameRange], *, active_id: str | None) -> None:
        self._ranges = list(ranges)
        self._active_id = active_id
        self._placeholder.setVisible(len(ranges) == 0)
        self._block_row_signal = True
        self._list.clear()
        row_hint = _RangeListRowWidget.size_hint()
        for rng in ranges:
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
            self._list.setCurrentRow(next((i for i, r in enumerate(ranges) if r.id == active_id), -1))
        else:
            self._list.clearSelection()
            self._list.setCurrentRow(-1)
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
        self._block_row_signal = True
        if range_id is None:
            self._list.clearSelection()
            self._list.setCurrentRow(-1)
            self._block_row_signal = False
            return
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == range_id:
                self._list.setCurrentRow(row)
                self._block_row_signal = False
                return
        self._block_row_signal = False

    def _range_at_row(self, row: int) -> VideoFrameRange | None:
        if row < 0 or row >= len(self._ranges):
            return None
        return self._ranges[row]

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        row = self._list.row(item)
        rng = self._range_at_row(row)
        if rng is None:
            return
        menu = MonosMenu(self)
        act_in = menu.addAction("Go to In")
        act_out = menu.addAction("Go to Out")
        menu.addSeparator()
        act_copy = menu.addAction("Copy range text")
        act_rename = menu.addAction("Rename…")
        act_dup = menu.addAction("Duplicate")
        menu.addSeparator()
        act_del = menu.addAction("Delete")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is None:
            return
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
            self.range_selected.emit(rid)

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
