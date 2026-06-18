"""Draw tool panel for unified review player."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.review_draw import REVIEW_DRAW_COLORS, ReviewDrawLayer
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.video_review_draw_stack import VideoReviewDrawStackWidget


def _icon_tool_btn(parent: QWidget, icon: str, tip: str, *, checkable: bool = True) -> QToolButton:
    btn = QToolButton(parent)
    btn.setObjectName("VideoReviewDrawTool")
    btn.setToolTip(tip)
    btn.setIcon(lucide_icon(icon, size=16, color_hex=MONOS_COLORS["text_label"]))
    btn.setIconSize(QSize(16, 16))
    btn.setFixedSize(32, 32)
    btn.setCheckable(checkable)
    btn.setAutoRaise(True)
    return btn


class VideoReviewDrawTransportActions(QWidget):
    """Draw keyframe / layer / onion / undo controls in the transport bar."""

    keyframe_add_requested = Signal()
    layer_add_requested = Signal()
    undo_stroke_requested = Signal()
    onion_enabled_changed = Signal(bool)
    onion_span_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawTransportActions")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._btn_add_key = _icon_tool_btn(
            self, "diamond", "Add keyframe at playhead", checkable=False
        )
        self._btn_add_key.clicked.connect(self.keyframe_add_requested.emit)
        lay.addWidget(self._btn_add_key)

        self._btn_onion = _icon_tool_btn(
            self, "layers-2", "Onion skin — ghost draw on adjacent keyframes (O)"
        )
        self._btn_onion.toggled.connect(self.onion_enabled_changed.emit)
        lay.addWidget(self._btn_onion)

        self._btn_add_layer = _icon_tool_btn(self, "layers", "Add layer", checkable=False)
        self._btn_add_layer.clicked.connect(self.layer_add_requested.emit)
        lay.addWidget(self._btn_add_layer)

        self._btn_undo = _icon_tool_btn(self, "undo-2", "Undo stroke (Ctrl+Z)", checkable=False)
        self._btn_undo.clicked.connect(self.undo_stroke_requested.emit)
        lay.addWidget(self._btn_undo)

        self._onion_spin = QSpinBox(self)
        self._onion_spin.setObjectName("VideoReviewDrawStackHoldSpin")
        self._onion_spin.setRange(1, 5)
        self._onion_spin.setValue(2)
        self._onion_spin.setPrefix("±")
        self._onion_spin.setFixedWidth(52)
        self._onion_spin.setToolTip("Onion span (frames)")
        self._onion_spin.valueChanged.connect(self.onion_span_changed.emit)
        lay.addWidget(self._onion_spin)

    def set_onion_enabled(self, enabled: bool) -> None:
        self._btn_onion.blockSignals(True)
        self._btn_onion.setChecked(bool(enabled))
        self._btn_onion.blockSignals(False)

    def set_onion_span(self, span: int) -> None:
        self._onion_spin.blockSignals(True)
        self._onion_spin.setValue(max(1, min(5, int(span))))
        self._onion_spin.blockSignals(False)


class VideoReviewDrawPanel(QWidget):
    tool_changed = Signal(str)
    color_changed = Signal(str)
    width_changed = Signal(float)
    onion_enabled_changed = Signal(bool)
    onion_span_changed = Signal(int)
    keyframe_selected = Signal(str, int)
    layer_selected = Signal(str)
    keyframe_add_requested = Signal()
    layer_add_requested = Signal()
    undo_stroke_requested = Signal()
    keyframe_edit_frame_changed = Signal(int)
    keyframe_hold_changed = Signal(int)
    keyframe_delete_requested = Signal()
    layer_visibility_toggle_requested = Signal(str)
    keyframe_visibility_toggle_requested = Signal(str, int)
    layer_default_hold_changed = Signal(str, int)
    layer_delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawPanel")
        self._active_keyframe_frame: int | None = None
        self._active_layer_id: str | None = None
        self._edit_sync_guard = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(4)
        self._tool_buttons: dict[str, QToolButton] = {}
        self._tool_icon_names: dict[str, str] = {}
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        for tool, icon, tip in (
            ("pen", "pencil", "Pen"),
            ("arrow", "move-up-right", "Arrow"),
            ("rect", "square", "Rectangle"),
            ("eraser", "eraser", "Eraser"),
        ):
            btn = _icon_tool_btn(self, icon, tip)
            btn.clicked.connect(lambda checked=False, t=tool: self._pick_tool(t))
            self._tool_buttons[tool] = btn
            self._tool_icon_names[tool] = icon
            self._tool_group.addButton(btn)
            tools_row.addWidget(btn)
        tools_row.addStretch(1)
        lay.addLayout(tools_row)
        self._pick_tool("pen")

        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        self._color_buttons: list[QToolButton] = []
        for hex_color in REVIEW_DRAW_COLORS:
            btn = QToolButton(self)
            btn.setFixedSize(22, 22)
            btn.setToolTip(hex_color)
            btn.setStyleSheet(f"background-color: {hex_color}; border-radius: 4px; border: none;")
            btn.clicked.connect(lambda checked=False, c=hex_color: self._pick_color(c))
            self._color_buttons.append(btn)
            color_row.addWidget(btn)
        color_row.addStretch(1)
        lay.addLayout(color_row)

        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("Width", self))
        self._width_spin = QSpinBox(self)
        self._width_spin.setRange(1, 16)
        self._width_spin.setValue(3)
        self._width_spin.valueChanged.connect(lambda v: self.width_changed.emit(float(v)))
        width_row.addWidget(self._width_spin)
        width_row.addStretch(1)
        lay.addLayout(width_row)

        self._edit_row = QWidget(self)
        self._edit_row.setObjectName("VideoReviewDrawEditRow")
        edit_lay = QHBoxLayout(self._edit_row)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        edit_lay.setSpacing(8)
        edit_lay.addWidget(QLabel("Edit key", self._edit_row))
        self._edit_frame_spin = QSpinBox(self._edit_row)
        self._edit_frame_spin.setPrefix("F")
        self._edit_frame_spin.setToolTip("Keyframe frame (or drag diamond on timeline)")
        self._edit_frame_spin.valueChanged.connect(self._on_edit_frame_changed)
        edit_lay.addWidget(self._edit_frame_spin)
        self._btn_delete_key = _icon_tool_btn(self._edit_row, "trash-2", "Delete keyframe (Del)")
        self._btn_delete_key.setCheckable(False)
        self._btn_delete_key.clicked.connect(self.keyframe_delete_requested.emit)
        edit_lay.addWidget(self._btn_delete_key)
        edit_lay.addStretch(1)
        self._edit_row.setVisible(False)
        lay.addWidget(self._edit_row)

        layers_title = QLabel("Layers", self)
        layers_title.setObjectName("VideoReviewDrawSectionTitle")
        lay.addWidget(layers_title)

        self._stack = VideoReviewDrawStackWidget(self)
        self._stack.layer_selected.connect(self.layer_selected.emit)
        self._stack.keyframe_selected.connect(self.keyframe_selected.emit)
        self._stack.layer_visibility_toggled.connect(self.layer_visibility_toggle_requested.emit)
        self._stack.keyframe_visibility_toggled.connect(self.keyframe_visibility_toggle_requested.emit)
        self._stack.layer_default_hold_changed.connect(self.layer_default_hold_changed.emit)
        self._stack.layer_delete_requested.connect(self.layer_delete_requested.emit)
        self._stack.keyframe_hold_changed.connect(self.keyframe_hold_changed.emit)
        lay.addWidget(self._stack, 1)

        hint = QLabel("D draw · E edit key · O onion · [ ] hold on key popup", self)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

    def _pick_tool(self, tool: str) -> None:
        active_color = MONOS_COLORS.get("blue_400", "#60a5fa")
        idle_color = MONOS_COLORS["text_label"]
        for t, btn in self._tool_buttons.items():
            btn.blockSignals(True)
            checked = t == tool
            btn.setChecked(checked)
            btn.setIcon(
                lucide_icon(
                    self._tool_icon_names[t],
                    size=16,
                    color_hex=active_color if checked else idle_color,
                )
            )
            btn.blockSignals(False)
        self.tool_changed.emit(tool)

    def set_active_tool(self, tool: str) -> None:
        if tool in self._tool_buttons:
            self._pick_tool(tool)

    def active_tool(self) -> str:
        for tool, btn in self._tool_buttons.items():
            if btn.isChecked():
                return tool
        return "pen"

    def active_width(self) -> float:
        return float(self._width_spin.value())

    def set_active_width(self, width: float) -> None:
        value = max(1, min(16, int(round(width))))
        self._width_spin.blockSignals(True)
        try:
            self._width_spin.setValue(value)
        finally:
            self._width_spin.blockSignals(False)
        self.width_changed.emit(float(value))

    def set_active_color(self, color: str) -> None:
        self._pick_color(color)

    def _pick_color(self, color: str) -> None:
        self.color_changed.emit(color)

    def set_keyframe_edit_mode(
        self,
        enabled: bool,
        *,
        frame: int | None = None,
        hold: int = 1,
        max_frame: int = 0,
        layer_id: str | None = None,
    ) -> None:
        self._edit_row.setVisible(bool(enabled))
        self._stack.set_keyframe_edit_state(
            enabled=bool(enabled),
            layer_id=layer_id,
            frame=frame,
            hold=hold,
        )
        if not enabled:
            return
        self._edit_sync_guard = True
        try:
            self._edit_frame_spin.setRange(0, max(0, int(max_frame)))
            if frame is not None:
                self._edit_frame_spin.setValue(int(frame))
        finally:
            self._edit_sync_guard = False

    def sync_keyframe_edit_hold(self, hold: int) -> None:
        self._stack.sync_keyframe_edit_hold(hold)

    def _on_edit_frame_changed(self, value: int) -> None:
        if self._edit_sync_guard:
            return
        self.keyframe_edit_frame_changed.emit(int(value))

    def set_active_keyframe(self, frame: int | None) -> None:
        self._active_keyframe_frame = frame

    def set_active_layer_id(self, layer_id: str | None) -> None:
        self._active_layer_id = layer_id

    def set_layers(
        self,
        layers: list[ReviewDrawLayer],
        *,
        active_frame: int | None,
        active_layer_id: str | None,
    ) -> None:
        self._active_keyframe_frame = active_frame
        self._active_layer_id = active_layer_id
        self._stack.set_layers(
            layers,
            active_frame=active_frame,
            active_layer_id=active_layer_id,
        )
