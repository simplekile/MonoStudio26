"""Floating draw tool picker on video right-click."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.review_draw import REVIEW_DRAW_COLORS
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import position_popup_near_global_point
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.video_review_draw_panel import _icon_tool_btn


class VideoReviewDrawQuickPopup(QFrame):
    tool_changed = Signal(str)
    color_changed = Signal(str)
    width_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("VideoReviewDrawQuickPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._sync_guard = False
        self._tool_buttons: dict[str, QToolButton] = {}
        self._tool_icon_names: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        tools_row = QHBoxLayout()
        tools_row.setSpacing(4)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        for tool, icon, tip in (
            ("pen", "pencil", "Pen"),
            ("arrow", "move-up-right", "Arrow"),
            ("rect", "square", "Rectangle"),
            ("eraser", "eraser", "Eraser"),
        ):
            btn = _icon_tool_btn(self, icon, tip)
            btn.clicked.connect(lambda checked=False, t=tool: self._on_tool_picked(t))
            self._tool_buttons[tool] = btn
            self._tool_icon_names[tool] = icon
            self._tool_group.addButton(btn)
            tools_row.addWidget(btn)
        root.addLayout(tools_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        for hex_color in REVIEW_DRAW_COLORS:
            btn = QToolButton(self)
            btn.setObjectName("VideoReviewDrawQuickColor")
            btn.setFixedSize(22, 22)
            btn.setToolTip(hex_color)
            btn.setStyleSheet(
                f"background-color: {hex_color}; border-radius: 4px; border: none;"
            )
            btn.clicked.connect(lambda checked=False, c=hex_color: self._on_color_picked(c))
            color_row.addWidget(btn)
        root.addLayout(color_row)

        width_row = QHBoxLayout()
        width_row.setSpacing(8)
        width_label = QLabel("Width", self)
        width_label.setObjectName("VideoReviewDrawQuickLabel")
        width_label.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        width_row.addWidget(width_label)
        self._width_spin = QSpinBox(self)
        self._width_spin.setObjectName("VideoReviewDrawStackHoldSpin")
        self._width_spin.setRange(1, 16)
        self._width_spin.setValue(3)
        self._width_spin.valueChanged.connect(self._on_width_changed)
        width_row.addWidget(self._width_spin)
        width_row.addStretch(1)
        root.addLayout(width_row)

    def show_at(self, global_pos: QPoint) -> None:
        self.adjustSize()
        position_popup_near_global_point(self, global_pos)
        self.show()

    def set_state(self, *, tool: str, color: str, width: float) -> None:
        self._sync_guard = True
        try:
            self._set_tool_visual(tool)
            self._width_spin.setValue(max(1, min(16, int(round(width)))))
        finally:
            self._sync_guard = False
        self._active_color = (color or "#ef4444").strip() or "#ef4444"

    def _set_tool_visual(self, tool: str) -> None:
        active_color = MONOS_COLORS.get("blue_400", "#60a5fa")
        idle_color = MONOS_COLORS["text_label"]
        for t, btn in self._tool_buttons.items():
            checked = t == tool
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.setIcon(
                lucide_icon(
                    self._tool_icon_names[t],
                    size=16,
                    color_hex=active_color if checked else idle_color,
                )
            )
            btn.blockSignals(False)

    def _on_tool_picked(self, tool: str) -> None:
        if self._sync_guard:
            return
        self._set_tool_visual(tool)
        self.tool_changed.emit(tool)

    def _on_color_picked(self, color: str) -> None:
        if self._sync_guard:
            return
        self._active_color = color
        self.color_changed.emit(color)

    def _on_width_changed(self, value: int) -> None:
        if self._sync_guard:
            return
        self.width_changed.emit(float(value))
