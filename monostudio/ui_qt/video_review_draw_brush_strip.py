"""Floating draw brush controls on the video viewer."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.review_draw import REVIEW_DRAW_COLORS
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.video_review_draw_panel import _icon_tool_btn


class VideoReviewDrawBrushStrip(QFrame):
    """Pen / color / width / onion — shown on video while Draw mode is active."""

    tool_changed = Signal(str)
    color_changed = Signal(str)
    width_changed = Signal(float)
    onion_enabled_changed = Signal(bool)
    onion_span_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawBrushStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

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
            ("eraser", "eraser", "Eraser (E on video)"),
        ):
            btn = _icon_tool_btn(self, icon, tip)
            btn.clicked.connect(lambda checked=False, t=tool: self._pick_tool(t))
            self._tool_buttons[tool] = btn
            self._tool_icon_names[tool] = icon
            self._tool_group.addButton(btn)
            tools_row.addWidget(btn)
        tools_row.addStretch(1)
        root.addLayout(tools_row)
        self._pick_tool("pen")

        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        for hex_color in REVIEW_DRAW_COLORS:
            btn = QToolButton(self)
            btn.setFixedSize(20, 20)
            btn.setToolTip(hex_color)
            btn.setStyleSheet(f"background-color: {hex_color}; border-radius: 4px; border: none;")
            btn.clicked.connect(lambda checked=False, c=hex_color: self.color_changed.emit(c))
            color_row.addWidget(btn)
        color_row.addStretch(1)
        root.addLayout(color_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        self._width_spin = QSpinBox(self)
        self._width_spin.setRange(1, 16)
        self._width_spin.setValue(3)
        self._width_spin.setPrefix("W ")
        self._width_spin.setFixedWidth(58)
        self._width_spin.setToolTip("Stroke width")
        self._width_spin.valueChanged.connect(lambda v: self.width_changed.emit(float(v)))
        bottom_row.addWidget(self._width_spin)

        self._btn_onion = _icon_tool_btn(self, "layers-2", "Onion skin (O)")
        self._btn_onion.toggled.connect(self.onion_enabled_changed.emit)
        bottom_row.addWidget(self._btn_onion)

        self._onion_spin = QSpinBox(self)
        self._onion_spin.setObjectName("VideoReviewDrawStackHoldSpin")
        self._onion_spin.setRange(1, 5)
        self._onion_spin.setValue(2)
        self._onion_spin.setPrefix("±")
        self._onion_spin.setFixedWidth(52)
        self._onion_spin.setToolTip("Onion span (frames)")
        self._onion_spin.valueChanged.connect(self.onion_span_changed.emit)
        bottom_row.addWidget(self._onion_spin)
        bottom_row.addStretch(1)
        root.addLayout(bottom_row)

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

    def set_active_width(self, width: float) -> None:
        value = max(1, min(16, int(round(width))))
        self._width_spin.blockSignals(True)
        try:
            self._width_spin.setValue(value)
        finally:
            self._width_spin.blockSignals(False)

    def set_active_color(self, color: str) -> None:
        self.color_changed.emit(color)

    def set_onion_enabled(self, enabled: bool) -> None:
        self._btn_onion.blockSignals(True)
        self._btn_onion.setChecked(bool(enabled))
        self._btn_onion.blockSignals(False)

    def set_onion_span(self, span: int) -> None:
        self._onion_spin.blockSignals(True)
        self._onion_spin.setValue(max(1, min(5, int(span))))
        self._onion_spin.blockSignals(False)
