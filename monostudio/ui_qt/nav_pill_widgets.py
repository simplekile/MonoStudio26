"""Shared icon pill nav widgets (sidebar scope, title bar Dashboard/Schedule)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolButton, QWidget

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.toolbar_separators import vertical_icon_separator

NAV_PILL_ICON_SIZE = 18
NAV_PILL_SEGMENT_W = 36

DASH_SCHED_PILL_SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("Dashboard", "layout-dashboard", "Dashboard"),
    ("Schedule", "calendar", "Schedule"),
)
DASH_SCHED_PILL_W = 2 * NAV_PILL_SEGMENT_W + 8


class IconPillWidget(QWidget):
    """Icon-only pill (2+ segments). Uses SidebarScopePill QSS from style.py."""

    segment_clicked = Signal(str)

    def __init__(
        self,
        segments: tuple[tuple[str, str, str], ...],
        *,
        icon_size: int = NAV_PILL_ICON_SIZE,
        segment_width: int = NAV_PILL_SEGMENT_W,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarScopePill")
        self.setProperty("display", "iconOnly")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(40)
        self.setMaximumHeight(40)
        self._icon_size = icon_size
        self._segment_width = segment_width
        self._segments = segments

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._buttons: dict[str, QToolButton] = {}
        n = len(segments)
        for i, (ctx, icon_name, tooltip) in enumerate(segments):
            if i > 0:
                layout.addWidget(
                    vertical_icon_separator(self, height=32),
                    0,
                    Qt.AlignmentFlag.AlignVCenter,
                )
            btn = QToolButton(self)
            btn.setObjectName("SidebarScopePillSegment")
            btn.setProperty("segment", ctx)
            btn.setProperty("active", "false")
            pos = "left" if i == 0 else ("right" if i == n - 1 else "center")
            btn.setProperty("position", pos)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setToolTip(tooltip)
            ic = lucide_icon(icon_name, size=icon_size, color_hex=MONOS_COLORS["text_label"])
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(icon_size, icon_size))
            btn.setFixedSize(segment_width, 32)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked=False, c=ctx: self.segment_clicked.emit(c))
            self._buttons[ctx] = btn
            layout.addWidget(btn, 0, Qt.AlignVCenter)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_active_segment(self, context_name: str | None) -> None:
        active_ctx = context_name if context_name in self._buttons else None
        icon_by_ctx = {ctx: ic for ctx, ic, _ in self._segments}
        for ctx, btn in self._buttons.items():
            is_active = ctx == active_ctx
            btn.setProperty("active", "true" if is_active else "false")
            color = MONOS_COLORS["blue_400"] if is_active else MONOS_COLORS["text_label"]
            ic = lucide_icon(icon_by_ctx.get(ctx, "box"), size=self._icon_size, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(self._icon_size, self._icon_size))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
