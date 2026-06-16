"""Review tools panel — floating tool strip + sidebar body (Ranges / Note / Draw)."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.settings_section_widgets import SettingsSegmentedControl
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.video_preview_context import PreviewContext
from monostudio.ui_qt.video_marker_list_widget import VideoMarkerListWidget
from monostudio.ui_qt.video_range_list_widget import VideoRangeListWidget
from monostudio.ui_qt.video_review_note_panel import VideoReviewNotePanel


class ReviewWorkspace(StrEnum):
    focus = "focus"
    review = "review"
    tools = "tools"
    theater = "theater"


class ReviewToolMode(StrEnum):
    ranges = "ranges"
    markers = "markers"
    note = "note"
    draw = "draw"


_STRIP_W = 36
_BODY_W = 260


class ReviewToolStrip(QWidget):
    """Vertical tool strip — parent on video surface as a floating overlay."""

    ranges_clicked = Signal()
    markers_clicked = Signal()
    note_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewToolStripFloat")
        self.setFixedWidth(_STRIP_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(4)

        self._btn_ranges = self._make_btn("scissors", "Ranges (R)")
        self._btn_ranges.clicked.connect(self.ranges_clicked.emit)
        self._btn_ranges.setVisible(False)
        lay.addWidget(self._btn_ranges)
        self._btn_markers = self._make_btn("pin", "Markers (M)")
        self._btn_markers.clicked.connect(self.markers_clicked.emit)
        self._btn_markers.setVisible(False)
        lay.addWidget(self._btn_markers)
        self._btn_note = self._make_btn("message-square", "Note (N)")
        self._btn_note.clicked.connect(self.note_clicked.emit)
        lay.addWidget(self._btn_note)
        self._btn_draw = self._make_btn("pencil", "Draw (defer)")
        self._btn_draw.setVisible(False)
        self._btn_draw.setEnabled(False)
        lay.addWidget(self._btn_draw)
        lay.addStretch(1)
        self.adjustSize()
        self.setFixedHeight(max(self.sizeHint().height(), 120))

    def _make_btn(self, icon: str, tip: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setIcon(lucide_icon(icon, size=18, color_hex=MONOS_COLORS["text_label"]))
        btn.setIconSize(QSize(18, 18))
        btn.setToolTip(tip)
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setFixedSize(28, 28)
        return btn

    def apply_context(self, context: PreviewContext) -> None:
        show_note = context == PreviewContext.entity
        self._btn_note.setVisible(show_note)

    def set_mode_checked(self, mode: ReviewToolMode, checked: bool) -> None:
        self._btn_ranges.setChecked(mode == ReviewToolMode.ranges and checked)
        self._btn_markers.setChecked(mode == ReviewToolMode.markers and checked)
        self._btn_note.setChecked(mode == ReviewToolMode.note and checked)


class ReviewToolsPanel(QWidget):
    workspace_changed = Signal(str)
    tool_mode_changed = Signal(str)
    strip_visibility_changed = Signal(bool)
    range_selected = Signal(str)
    range_delete_requested = Signal(str)
    range_delete_all_requested = Signal()
    range_duplicate_requested = Signal(str)
    range_label_changed = Signal(str, str)
    go_to_in_requested = Signal(str)
    go_to_out_requested = Signal(str)
    marker_selected = Signal(str)
    marker_deselected = Signal()
    marker_delete_requested = Signal(str)
    marker_delete_all_requested = Signal()
    marker_label_changed = Signal(str, str)
    marker_export_requested = Signal()
    open_all_notes_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewToolsPanel")
        self._context = PreviewContext.entity
        self._workspace = ReviewWorkspace.focus
        self._tool_mode = ReviewToolMode.ranges
        self._strip: ReviewToolStrip | None = None
        self._strip_visible = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._body_wrap = QWidget(self)
        self._body_wrap.setObjectName("VideoReviewToolsBody")
        self._body_wrap.setFixedWidth(_BODY_W)
        body_lay = QVBoxLayout(self._body_wrap)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(8)

        mode_row = QWidget(self._body_wrap)
        mode_row.setObjectName("VideoReviewModePillRow")
        mode_lay = QHBoxLayout(mode_row)
        mode_lay.setContentsMargins(12, 12, 12, 0)
        mode_lay.setSpacing(0)
        self._mode_pills = SettingsSegmentedControl(
            [
                ("Ranges", "Ranges (R)", ReviewToolMode.ranges.value),
                ("Markers", "Markers (M)", ReviewToolMode.markers.value),
            ],
            mode_row,
        )
        self._mode_pills.value_changed.connect(self._on_mode_pill_changed)
        mode_lay.addWidget(self._mode_pills, 0, Qt.AlignmentFlag.AlignLeft)
        mode_lay.addStretch(1)
        body_lay.addWidget(mode_row)

        name_wrap = QWidget(self._body_wrap)
        name_lay = QHBoxLayout(name_wrap)
        name_lay.setContentsMargins(12, 0, 12, 0)
        name_lay.setSpacing(0)
        self._name_field = QLineEdit(name_wrap)
        self._name_field.setObjectName("DialogLineEdit")
        self._name_field.setPlaceholderText("Name (optional)")
        self._name_field.setFont(monos_font("Inter", 12, QFont.Weight.Normal))
        self._name_field.editingFinished.connect(self._on_name_edited)
        name_lay.addWidget(self._name_field)
        body_lay.addWidget(name_wrap)

        self._stack = QStackedWidget(self._body_wrap)
        self._empty = QWidget()
        self._range_panel = VideoRangeListWidget()
        self._range_panel.range_selected.connect(self.range_selected.emit)
        self._range_panel.range_delete_requested.connect(self.range_delete_requested.emit)
        self._range_panel.range_delete_all_requested.connect(self.range_delete_all_requested.emit)
        self._range_panel.range_duplicate_requested.connect(self.range_duplicate_requested.emit)
        self._range_panel.range_rename_requested.connect(self._on_range_rename)
        self._range_panel.go_to_in_requested.connect(self.go_to_in_requested.emit)
        self._range_panel.go_to_out_requested.connect(self.go_to_out_requested.emit)
        self._marker_panel = VideoMarkerListWidget()
        self._marker_panel.marker_selected.connect(self.marker_selected.emit)
        self._marker_panel.marker_deselected.connect(self.marker_deselected.emit)
        self._marker_panel.marker_delete_requested.connect(self.marker_delete_requested.emit)
        self._marker_panel.marker_delete_all_requested.connect(self.marker_delete_all_requested.emit)
        self._marker_panel.marker_rename_requested.connect(self.marker_label_changed.emit)
        self._marker_panel.export_requested.connect(self.marker_export_requested.emit)
        self._note_panel = VideoReviewNotePanel()
        self._note_panel.open_all_notes_requested.connect(self.open_all_notes_requested.emit)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._range_panel)
        self._stack.addWidget(self._marker_panel)
        self._stack.addWidget(self._note_panel)
        body_lay.addWidget(self._stack, 1)
        root.addWidget(self._body_wrap)

        self._active_range_id: str | None = None
        self._active_marker_id: str | None = None
        self._apply_workspace_layout()

    def bind_strip(self, strip: ReviewToolStrip) -> None:
        self._strip = strip
        strip.ranges_clicked.connect(lambda: self.activate_tool_mode(ReviewToolMode.ranges))
        strip.markers_clicked.connect(lambda: self.activate_tool_mode(ReviewToolMode.markers))
        strip.note_clicked.connect(lambda: self.activate_tool_mode(ReviewToolMode.note))
        self._sync_strip_checks()

    def strip_visible(self) -> bool:
        return self._strip_visible

    def set_strip_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._strip_visible:
            return
        self._strip_visible = visible
        if self._strip is not None:
            self._strip.setVisible(visible)
            self._strip.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                not visible,
            )
        self.strip_visibility_changed.emit(visible)

    def toggle_strip(self) -> bool:
        self.set_strip_visible(not self._strip_visible)
        return self._strip_visible

    def apply_context(self, context: PreviewContext) -> None:
        self._context = context
        if self._strip is not None:
            self._strip.apply_context(context)

    def range_list_widget(self) -> VideoRangeListWidget:
        return self._range_panel

    def marker_list_widget(self) -> VideoMarkerListWidget:
        return self._marker_panel

    def note_panel(self) -> VideoReviewNotePanel:
        return self._note_panel

    def workspace(self) -> ReviewWorkspace:
        return self._workspace

    def tool_mode(self) -> ReviewToolMode:
        return self._tool_mode

    def set_workspace(self, ws: ReviewWorkspace | str) -> None:
        if isinstance(ws, str):
            try:
                ws = ReviewWorkspace(ws)
            except ValueError:
                ws = ReviewWorkspace.focus
        if ws == ReviewWorkspace.review:
            ws = ReviewWorkspace.focus
            self.set_strip_visible(True)
        if ws == self._workspace:
            self._apply_workspace_layout()
            return
        self._workspace = ws
        self._apply_workspace_layout()
        self.workspace_changed.emit(self._workspace.value)

    def activate_tool_mode(self, mode: ReviewToolMode | str) -> None:
        if isinstance(mode, str):
            try:
                mode = ReviewToolMode(mode)
            except ValueError:
                mode = ReviewToolMode.ranges
        if mode == ReviewToolMode.note and self._context != PreviewContext.entity:
            mode = ReviewToolMode.ranges
        if mode == ReviewToolMode.draw:
            return
        self._tool_mode = mode
        if self._workspace != ReviewWorkspace.tools:
            self._workspace = ReviewWorkspace.tools
        self._apply_workspace_layout()
        self.tool_mode_changed.emit(self._tool_mode.value)

    def cycle_workspace(self) -> None:
        order = (ReviewWorkspace.focus, ReviewWorkspace.tools)
        idx = order.index(self._workspace) if self._workspace in order else 0
        self.set_workspace(order[(idx + 1) % len(order)])

    def retreat_workspace_or_mode(self) -> bool:
        if self._workspace == ReviewWorkspace.tools:
            if self._tool_mode == ReviewToolMode.note:
                self._tool_mode = ReviewToolMode.ranges
                self._apply_workspace_layout()
                return True
            if self._tool_mode == ReviewToolMode.markers:
                self._tool_mode = ReviewToolMode.ranges
                self._apply_workspace_layout()
                return True
            if self._tool_mode != ReviewToolMode.ranges:
                self._tool_mode = ReviewToolMode.ranges
                self._apply_workspace_layout()
                return True
            self.set_workspace(ReviewWorkspace.focus)
            return True
        if self._strip_visible:
            self.set_strip_visible(False)
            return True
        return False

    def set_active_range_id(self, range_id: str | None, *, label: str = "") -> None:
        self._active_range_id = range_id
        if self._tool_mode == ReviewToolMode.ranges:
            self._name_field.blockSignals(True)
            self._name_field.setText(label if range_id else "")
            self._name_field.setEnabled(bool(range_id))
            self._name_field.blockSignals(False)

    def set_active_marker_id(self, marker_id: str | None, *, label: str = "") -> None:
        self._active_marker_id = marker_id
        if self._tool_mode == ReviewToolMode.markers:
            self._name_field.blockSignals(True)
            self._name_field.setText(label if marker_id else "")
            self._name_field.setEnabled(bool(marker_id))
            self._name_field.blockSignals(False)

    def focus_range_name_field(self) -> None:
        self._name_field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._name_field.selectAll()

    def _on_name_edited(self) -> None:
        text = self._name_field.text().strip()
        if self._tool_mode == ReviewToolMode.markers:
            if self._active_marker_id:
                self.marker_label_changed.emit(self._active_marker_id, text)
            return
        if self._active_range_id:
            self.range_label_changed.emit(self._active_range_id, text)

    def _on_range_rename(self, range_id: str, label: str) -> None:
        self.range_label_changed.emit(range_id, label)

    def _on_mode_pill_changed(self) -> None:
        mode = self._mode_pills.value()
        if mode == self._tool_mode.value:
            return
        self.activate_tool_mode(mode)

    def _sync_mode_pills(self) -> None:
        if self._tool_mode in (ReviewToolMode.ranges, ReviewToolMode.markers):
            self._mode_pills.blockSignals(True)
            self._mode_pills.set_value(self._tool_mode.value)
            self._mode_pills.blockSignals(False)
            self._mode_pills.setVisible(True)
        else:
            self._mode_pills.setVisible(False)

    def _sync_strip_checks(self) -> None:
        if self._strip is None:
            return
        active = self._workspace == ReviewWorkspace.tools
        self._strip.set_mode_checked(self._tool_mode, active)

    def _apply_workspace_layout(self) -> None:
        show_body = self._workspace == ReviewWorkspace.tools
        self._body_wrap.setVisible(show_body)
        self.setFixedWidth(_BODY_W if show_body else 0)
        self._sync_strip_checks()
        self._sync_mode_pills()
        if show_body:
            if self._tool_mode == ReviewToolMode.ranges:
                self._stack.setCurrentWidget(self._range_panel)
                self._name_field.show()
                self._name_field.setPlaceholderText("Range name (optional)")
            elif self._tool_mode == ReviewToolMode.markers:
                self._stack.setCurrentWidget(self._marker_panel)
                self._name_field.show()
                self._name_field.setPlaceholderText("Marker label (optional)")
            elif self._tool_mode == ReviewToolMode.note:
                self._stack.setCurrentWidget(self._note_panel)
                self._name_field.hide()
            else:
                self._stack.setCurrentWidget(self._empty)
        self.updateGeometry()
