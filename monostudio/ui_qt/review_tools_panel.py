"""Review tools panel — sidebar body (Ranges / Markers / Draw)."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QFont, QPainter, QPainterPath, QColor, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.settings_section_widgets import SettingsSegmentedControl
from monostudio.ui_qt.style import monos_font
from monostudio.ui_qt.video_preview_context import PreviewContext
from monostudio.ui_qt.video_marker_list_widget import VideoMarkerListWidget
from monostudio.ui_qt.video_range_list_widget import VideoRangeListWidget
from monostudio.ui_qt.video_review_draw_panel import VideoReviewDrawPanel


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


_TOOLS_BODY_DEFAULT_W = 260
TOOLS_PANEL_MIN_W = 200
TOOLS_PANEL_MAX_W = 480
TOOLS_PANEL_DEFAULT_W = _TOOLS_BODY_DEFAULT_W
_TOOLS_BODY_RADIUS = 12
_TOOLS_BODY_BG = "#1e2124"


class _ReviewToolsBodyFrame(QFrame):
    """Tools sidebar body — painted bottom-right corner (QSS radius does not clip children)."""

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        rect = QRectF(0.0, 0.0, w + 1.0, h + 1.0)
        r = min(float(_TOOLS_BODY_RADIUS), rect.width() / 2, rect.height() / 2)
        path = QPainterPath()
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        path.moveTo(left, top)
        path.lineTo(right, top)
        path.lineTo(right, bottom - r)
        path.arcTo(right - 2 * r, bottom - 2 * r, 2 * r, 2 * r, 0, -90)
        path.lineTo(left, bottom)
        path.lineTo(left, top)
        path.closeSubpath()
        painter.fillPath(path, QColor(_TOOLS_BODY_BG))
        painter.setPen(QPen(QColor(255, 255, 255, 15), 1))
        painter.drawLine(0, 0, 0, int(h))
        super().paintEvent(event)


class ReviewToolsPanel(QWidget):
    workspace_changed = Signal(str)
    tool_mode_changed = Signal(str)
    range_selected = Signal(str, bool)
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
    draw_keyframe_selected = Signal(str, int)
    draw_layer_selected = Signal(str)
    draw_keyframe_add_requested = Signal()
    draw_layer_add_requested = Signal()
    draw_undo_stroke_requested = Signal()
    draw_keyframe_edit_frame_changed = Signal(int)
    draw_keyframe_hold_changed = Signal(int)
    draw_keyframe_delete_requested = Signal()
    draw_layer_visibility_toggle_requested = Signal(str)
    draw_keyframe_visibility_toggle_requested = Signal(str, int)
    draw_layer_default_hold_changed = Signal(str, int)
    draw_layer_delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewToolsPanel")
        self._context = PreviewContext.entity
        self._workspace = ReviewWorkspace.focus
        self._tool_mode = ReviewToolMode.ranges

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._body_wrap = _ReviewToolsBodyFrame(self)
        self._body_wrap.setObjectName("VideoReviewToolsBody")
        self._body_wrap.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
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
                ("", "Ranges (R)", ReviewToolMode.ranges.value, "sliders-horizontal"),
                ("", "Markers (M)", ReviewToolMode.markers.value, "flag"),
                ("", "Draw (D)", ReviewToolMode.draw.value, "pencil"),
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
        self._draw_panel = VideoReviewDrawPanel()
        self._draw_panel.keyframe_selected.connect(self.draw_keyframe_selected.emit)
        self._draw_panel.layer_selected.connect(self.draw_layer_selected.emit)
        self._draw_panel.keyframe_add_requested.connect(self.draw_keyframe_add_requested.emit)
        self._draw_panel.layer_add_requested.connect(self.draw_layer_add_requested.emit)
        self._draw_panel.undo_stroke_requested.connect(self.draw_undo_stroke_requested.emit)
        self._draw_panel.keyframe_edit_frame_changed.connect(self.draw_keyframe_edit_frame_changed.emit)
        self._draw_panel.keyframe_hold_changed.connect(self.draw_keyframe_hold_changed.emit)
        self._draw_panel.keyframe_delete_requested.connect(self.draw_keyframe_delete_requested.emit)
        self._draw_panel.layer_visibility_toggle_requested.connect(
            self.draw_layer_visibility_toggle_requested.emit
        )
        self._draw_panel.keyframe_visibility_toggle_requested.connect(
            self.draw_keyframe_visibility_toggle_requested.emit
        )
        self._draw_panel.layer_default_hold_changed.connect(
            self.draw_layer_default_hold_changed.emit
        )
        self._draw_panel.layer_delete_requested.connect(self.draw_layer_delete_requested.emit)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._range_panel)
        self._stack.addWidget(self._marker_panel)
        self._stack.addWidget(self._draw_panel)
        body_lay.addWidget(self._stack, 1)
        root.addWidget(self._body_wrap)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._active_range_id: str | None = None
        self._active_marker_id: str | None = None
        self.apply_context(self._context)
        self._apply_workspace_layout()

    def apply_context(self, context: PreviewContext) -> None:
        self._context = context
        self._mode_pills.set_segment_visible(ReviewToolMode.draw.value, True)

    def range_list_widget(self) -> VideoRangeListWidget:
        return self._range_panel

    def marker_list_widget(self) -> VideoMarkerListWidget:
        return self._marker_panel

    def draw_panel(self) -> VideoReviewDrawPanel:
        return self._draw_panel

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
        prev_mode = self._tool_mode
        prev_ws = self._workspace
        self._tool_mode = mode
        if self._workspace != ReviewWorkspace.tools:
            self._workspace = ReviewWorkspace.tools
        self._apply_workspace_layout()
        if mode != prev_mode or self._workspace != prev_ws:
            self.tool_mode_changed.emit(self._tool_mode.value)

    def cycle_workspace(self) -> None:
        order = (ReviewWorkspace.focus, ReviewWorkspace.tools)
        idx = order.index(self._workspace) if self._workspace in order else 0
        self.set_workspace(order[(idx + 1) % len(order)])

    def retreat_workspace_or_mode(self) -> bool:
        if self._workspace == ReviewWorkspace.tools:
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
        show = self._workspace == ReviewWorkspace.tools
        self._mode_pills.setVisible(show)
        if not show:
            return
        self._mode_pills.blockSignals(True)
        self._mode_pills.set_value(self._tool_mode.value)
        self._mode_pills.blockSignals(False)

    def _apply_workspace_layout(self) -> None:
        show_body = self._workspace == ReviewWorkspace.tools
        self._body_wrap.setVisible(show_body)
        if show_body:
            self.setMinimumWidth(TOOLS_PANEL_MIN_W)
            self.setMaximumWidth(TOOLS_PANEL_MAX_W)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(0)
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
            elif self._tool_mode == ReviewToolMode.draw:
                self._stack.setCurrentWidget(self._draw_panel)
                self._name_field.hide()
            else:
                self._stack.setCurrentWidget(self._empty)
        self.updateGeometry()
