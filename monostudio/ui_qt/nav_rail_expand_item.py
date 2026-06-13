"""Nav rail item that expands on hover (Linear / Discord style)."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS

RAIL_SLOT_W = 68  # 56px + 20%
ITEM_H = 48
GROUP_ITEM_H = 48
GROUP_PAD_V = 6
GROUP_ITEM_GAP = 6
ICON_SLOT = 48
ICON_SIZE = 24
COLLAPSED_W = ICON_SLOT
FLYOUT_PAD_L = 10
FLYOUT_PAD_R = 12
FLYOUT_GAP = 10
EXPANDED_W_MAX = 168
ANIM_MS = 175
ICON_INSET_X = (RAIL_SLOT_W - ICON_SLOT) // 2

_FLYOUT_RADIUS = 8

_PILL_ACTIVE_FG = MONOS_COLORS["pill_segment_active_fg"]

# Nav rail item states: Electric Blue active/hover; trash keeps red when selected.
# Group pill shells (NavRailGroup) share one neutral gray — no per-group tint.
_GROUP_PILL_BG_RGBA = (255, 255, 255, 16)

_NAV_ICON_COLORS: dict[str, str] = {
    "inactive_icon": "#d4d4d8",
    "hover_icon": "#e4e4e7",
    "active_icon": "#fafafa",
}

_NAV_ITEM_STYLE: dict[str, object] = {
    "hover_bg": "#1e3a8a",
    "active_bg": MONOS_COLORS["blue_600"],
    "active_hover_bg": MONOS_COLORS["blue_500"],
    **_NAV_ICON_COLORS,
}

_NAV_GROUP_STYLES: dict[str, dict[str, object]] = {
    "home": _NAV_ITEM_STYLE,
    "scope": _NAV_ITEM_STYLE,
    "workflow": _NAV_ITEM_STYLE,
    "utility": _NAV_ITEM_STYLE,
    "bottom": _NAV_ITEM_STYLE,
    "trash": {
        "hover_bg": "#7f1d1d",
        "active_bg": "#dc2626",
        "active_hover_bg": "#ef4444",
        "inactive_icon": "#fca5a5",
        "hover_icon": "#fecaca",
        "active_icon": "#fafafa",
    },
}


def _nav_group_style(nav_group: str) -> dict[str, object]:
    return _NAV_GROUP_STYLES.get(nav_group, _NAV_GROUP_STYLES["utility"])


def _group_inactive_color(_nav_group: str = "") -> QColor:
    r, g, b, a = _GROUP_PILL_BG_RGBA
    return QColor(int(r), int(g), int(b), int(a))


class NavRailGroup(QWidget):
    """Vertical stack of rail items sharing one full-round pill background."""

    def __init__(
        self,
        rail: QWidget,
        *,
        nav_group: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NavRailGroup")
        self._rail = rail
        self._nav_group = nav_group
        self._items: list[NavRailExpandItem] = []
        self.setProperty("navGroup", nav_group)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        inset = (RAIL_SLOT_W - ICON_SLOT) // 2
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(inset, GROUP_PAD_V, inset, GROUP_PAD_V)
        self._layout.setSpacing(GROUP_ITEM_GAP)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def items(self) -> list[NavRailExpandItem]:
        return list(self._items)

    def add_item(self, item: NavRailExpandItem) -> NavRailExpandItem:
        item._bind_group(self)
        self._layout.addWidget(item, 0, Qt.AlignmentFlag.AlignHCenter)
        self._items.append(item)
        self._sync_height()
        return item

    def _sync_height(self) -> None:
        n = len(self._items)
        gaps = max(0, n - 1) * GROUP_ITEM_GAP
        h = GROUP_PAD_V * 2 + max(0, n) * GROUP_ITEM_H + gaps
        self.setFixedSize(RAIL_SLOT_W, max(ICON_SLOT + GROUP_PAD_V * 2, h))

    def notify_child_active_changed(self) -> None:
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        if not self._items:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        inset = (self.width() - ICON_SLOT) // 2
        pill_h = self.height() - GROUP_PAD_V * 2
        pill = QRect(inset, GROUP_PAD_V, ICON_SLOT, max(ICON_SLOT, pill_h))
        p.setBrush(_group_inactive_color(self._nav_group))
        p.drawRoundedRect(pill, _FLYOUT_RADIUS, _FLYOUT_RADIUS)
        p.end()
        super().paintEvent(event)


class NavRailFlyout(QFrame):
    """Single shared flyout; expands over the filter panel, not the whole sidebar."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavRailFlyout")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

        self._anim_w = COLLAPSED_W
        self._expanded_w = COLLAPSED_W
        self._target_expanded = False
        self._icon_name = "house"
        self._active = False
        self._hovered = False
        self._nav_group = "home"

        lay = QHBoxLayout(self)
        lay.setContentsMargins(FLYOUT_PAD_L, 0, FLYOUT_PAD_R, 0)
        lay.setSpacing(FLYOUT_GAP)

        self._icon = QLabel(self)
        self._icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._label = QLabel(self)
        self._label.setObjectName("NavRailFlyoutLabel")
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._label, 1, Qt.AlignmentFlag.AlignVCenter)

        self._width_anim = QPropertyAnimation(self, b"flyoutWidth", self)
        self._width_anim.setDuration(ANIM_MS)
        self._width_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._width_anim.finished.connect(self._on_width_anim_finished)

        self.setFixedSize(COLLAPSED_W, ITEM_H)

    def get_flyoutWidth(self) -> int:
        return self._anim_w

    def set_flyoutWidth(self, w: int) -> None:
        self._anim_w = int(w)
        self.setFixedSize(self._anim_w, ITEM_H)
        self._label.setVisible(self._anim_w > COLLAPSED_W + 12)

    flyoutWidth = Property(int, get_flyoutWidth, set_flyoutWidth)

    def set_content(
        self,
        *,
        icon_name: str,
        label: str,
        active: bool,
        nav_group: str = "utility",
        hovered: bool = True,
    ) -> None:
        self._icon_name = icon_name
        self._active = active
        self._nav_group = nav_group
        self._hovered = hovered
        self._label.setText(label)
        self._refresh_icon(hovered=hovered)
        self._sync_label_style()
        self._expanded_w = self._measure_expanded_width()
        self.setProperty("active", "true" if active else "false")
        self.setProperty("navGroup", nav_group)
        if self.style():
            self.style().unpolish(self)
            self.style().polish(self)
        self.update()

    def _measure_expanded_width(self) -> int:
        font = QFont("Inter", 13)
        font.setWeight(QFont.Weight.DemiBold if self._active else QFont.Weight.Medium)
        text_w = QFontMetrics(font).horizontalAdvance(self._label.text() or "")
        inner = FLYOUT_PAD_L + ICON_SIZE + FLYOUT_GAP + text_w + FLYOUT_PAD_R
        return max(COLLAPSED_W, min(inner + 2, EXPANDED_W_MAX))

    def _sync_label_style(self) -> None:
        self._label.setStyleSheet(
            f"color: {_PILL_ACTIVE_FG}; font-weight: {'600' if self._active else '500'};"
        )

    def _refresh_icon(self, *, hovered: bool = False) -> None:
        style = _nav_group_style(self._nav_group)
        if self._active:
            color = str(style["active_icon"])
        elif hovered:
            color = str(style["hover_icon"])
        else:
            color = str(style["inactive_icon"])
        ic = lucide_icon(self._icon_name, size=ICON_SIZE, color_hex=color)
        if not ic.isNull():
            self._icon.setPixmap(ic.pixmap(ICON_SIZE, ICON_SIZE))
        else:
            self._icon.clear()

    def anchor_to(self, slot: NavRailExpandItem, host: QWidget) -> None:
        top_level = slot.window() or host
        if self.parentWidget() is not top_level:
            self.setParent(top_level)
        inset_x = max(0, (slot.width() - ICON_SLOT) // 2)
        inset_y = max(0, (slot.height() - ITEM_H) // 2)
        global_pt = slot.mapToGlobal(QPoint(inset_x, inset_y))
        local = top_level.mapFromGlobal(global_pt)
        self.move(local.x(), local.y())

    def animate_expand(self, expanded: bool) -> None:
        self._target_expanded = expanded
        end = self._expanded_w if expanded else COLLAPSED_W
        self._width_anim.stop()
        self._width_anim.setStartValue(self._anim_w)
        self._width_anim.setEndValue(end)
        self._width_anim.start()
        if expanded:
            self.show()
            self.raise_()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        style = _nav_group_style(self._nav_group)
        if self._active:
            bg = QColor(str(style["active_hover_bg"]))
        else:
            bg = QColor(str(style["hover_bg"]))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect(), _FLYOUT_RADIUS, _FLYOUT_RADIUS)
        p.end()
        super().paintEvent(event)

    def _on_width_anim_finished(self) -> None:
        if not self._target_expanded:
            self.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class NavRailExpandItem(QWidget):
    """Hit target in the 56px rail; hover delegates visual expand to NavRailFlyout."""

    clicked = Signal()

    def __init__(
        self,
        rail: QWidget,
        *,
        icon_name: str,
        label: str,
        object_name: str | None = None,
        nav_group: str = "utility",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self._rail = rail
        self._icon_name = icon_name
        self._label = label
        self._active = False
        self._hovered = False
        self._nav_group = nav_group
        self.setProperty("navGroup", nav_group)
        self._owner_group: NavRailGroup | None = None

        self.setFixedSize(RAIL_SLOT_W, ITEM_H + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("")

        self._icon = QLabel(self)
        self._icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._layout_icon()
        self._refresh_icon()

    def _bind_group(self, group: NavRailGroup) -> None:
        self._owner_group = group
        self.setFixedSize(ICON_SLOT, GROUP_ITEM_H)

    def _layout_icon(self) -> None:
        x = (self.width() - ICON_SIZE) // 2
        y = (self.height() - ICON_SIZE) // 2
        self._icon.move(x, y)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_icon()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self._refresh_icon()
        self.update()
        if self._owner_group is not None:
            self._owner_group.notify_child_active_changed()
        rail = self._rail
        flyout = getattr(rail, "_nav_flyout", None)
        if flyout is not None and getattr(rail, "_flyout_owner", None) is self and flyout.isVisible():
            flyout.set_content(
                icon_name=self._icon_name,
                label=self._label,
                active=self._active,
                nav_group=self._nav_group,
                hovered=self._hovered,
            )

    def set_icon_name(self, icon_name: str) -> None:
        self._icon_name = icon_name
        self._refresh_icon()
        self.update()

    def _refresh_icon(self) -> None:
        style = _nav_group_style(self._nav_group)
        if self._active:
            color = str(style["active_icon"])
        elif self._hovered:
            color = str(style["hover_icon"])
        else:
            color = str(style["inactive_icon"])
        ic = lucide_icon(self._icon_name, size=ICON_SIZE, color_hex=color)
        if not ic.isNull():
            self._icon.setPixmap(ic.pixmap(ICON_SIZE, ICON_SIZE))
        else:
            self._icon.clear()

    def _slot_highlight_color(self) -> QColor | None:
        style = _nav_group_style(self._nav_group)
        if self._active:
            return QColor(str(style["active_bg"]))
        if self._hovered:
            return QColor(str(style["hover_bg"]))
        if self._owner_group is None:
            return _group_inactive_color(self._nav_group)
        return None

    def _paint_slot_highlight(self, p: QPainter, bg: QColor) -> None:
        slot_w = min(ICON_SLOT, self.width())
        slot_h = min(ICON_SLOT, self.height())
        x = (self.width() - slot_w) // 2
        y = (self.height() - slot_h) // 2
        p.setBrush(bg)
        p.drawRoundedRect(x, y, slot_w, slot_h, _FLYOUT_RADIUS, _FLYOUT_RADIUS)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        slot_bg = self._slot_highlight_color()
        if slot_bg is not None and not self._icon.isHidden():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            self._paint_slot_highlight(p, slot_bg)
            p.end()
        super().paintEvent(event)

    def _set_hovered(self, hovered: bool) -> None:
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self._refresh_icon()
        self.update()

    def clear_hover_state(self) -> None:
        """Drop manual hover highlight (e.g. pointer moved to main content)."""
        self.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
        if self._hovered:
            self._hovered = False
            self._refresh_icon()
            self.update()

    def sync_hover_from_global(self, global_pos) -> None:
        """Reconcile manual hover with the current cursor (after main-view steals events)."""
        try:
            local = self.mapFromGlobal(global_pos)
            hovered = self.rect().contains(local)
        except Exception:
            hovered = False
        self._set_hovered(hovered)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._set_hovered(True)
        show = getattr(self._rail, "_show_nav_flyout", None)
        if callable(show):
            show(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hovered(False)
        hide = getattr(self._rail, "_schedule_hide_nav_flyout", None)
        if callable(hide):
            hide(self)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            rail = self._rail
            flyout = getattr(rail, "_nav_flyout", None)
            owner = getattr(rail, "_flyout_owner", None)
            if flyout is not None and flyout.isVisible() and owner is self:
                event.accept()
                return
            self.clicked.emit()
        super().mousePressEvent(event)
