"""Clamp popup placement to screen / window edges (shared by nav rail, top bar, inspector, …)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

DEFAULT_POPUP_MARGIN = 8
DEFAULT_POPUP_GAP = 4


def _screen_for_global_point(point: QPoint):
    screen = QApplication.screenAt(point)
    if screen is not None:
        return screen
    app = QApplication.instance()
    return app.primaryScreen() if app is not None else None


def _bounds_for_point(point: QPoint, bounds: QRect | None) -> QRect:
    if bounds is not None and bounds.isValid():
        return bounds
    screen = _screen_for_global_point(point)
    if screen is None:
        return QRect()
    return screen.availableGeometry()


def clamp_popup_global_pos(
    popup: QWidget,
    global_anchor: QPoint,
    *,
    anchor_rect_global: QRect | None = None,
    bounds: QRect | None = None,
    gap: int = DEFAULT_POPUP_GAP,
    margin: int = DEFAULT_POPUP_MARGIN,
    max_width: int | None = None,
) -> QPoint:
    """Return clamped top-left for *popup* at *global_anchor*; flip above anchor when needed."""
    popup.adjustSize()
    w, h = popup.width(), popup.height()
    x = global_anchor.x()
    y = global_anchor.y()

    ag = _bounds_for_point(global_anchor, bounds)
    if not ag.isValid():
        return QPoint(int(x), int(y))

    cap_w = max_width if max_width is not None else max(260, ag.width() - margin * 2)
    if w > cap_w:
        popup.setFixedWidth(int(cap_w))
        popup.adjustSize()
        w, h = popup.width(), popup.height()

    if x + w > ag.right() - margin:
        if anchor_rect_global is not None and anchor_rect_global.isValid():
            x = anchor_rect_global.right() - w
        else:
            x = ag.right() - w - margin
    if x < ag.left() + margin:
        x = ag.left() + margin
    x = max(ag.left() + margin, min(x, ag.right() - w - margin))

    if y + h > ag.bottom() - margin:
        if anchor_rect_global is not None and anchor_rect_global.isValid():
            y = anchor_rect_global.top() - h - gap
        else:
            y = ag.bottom() - h - margin
    if y < ag.top() + margin:
        y = ag.top() + margin
    y = max(ag.top() + margin, min(y, ag.bottom() - h - margin))

    return QPoint(int(x), int(y))


def position_popup_near_anchor(
    popup: QWidget,
    anchor: QWidget,
    *,
    gap: int = DEFAULT_POPUP_GAP,
    x_offset: int = 0,
    margin: int = DEFAULT_POPUP_MARGIN,
    bounds: QRect | None = None,
    max_width: int | None = None,
) -> None:
    """Place *popup* below *anchor*, flipping above and clamping to *bounds* or screen."""
    if anchor is None or not anchor.isVisible():
        return
    anchor_rect = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
    bottom_left = anchor.mapToGlobal(anchor.rect().bottomLeft())
    global_anchor = QPoint(bottom_left.x() + x_offset, bottom_left.y() + gap)
    popup.move(
        clamp_popup_global_pos(
            popup,
            global_anchor,
            anchor_rect_global=anchor_rect,
            bounds=bounds,
            gap=gap,
            margin=margin,
            max_width=max_width,
        )
    )


def position_popup_near_global_point(
    popup: QWidget,
    global_anchor: QPoint,
    *,
    anchor_rect_global: QRect | None = None,
    bounds: QRect | None = None,
    gap: int = DEFAULT_POPUP_GAP,
    margin: int = DEFAULT_POPUP_MARGIN,
    max_width: int | None = None,
) -> None:
    """Place *popup* at a global point with flip/clamp (assignee picker, …)."""
    popup.move(
        clamp_popup_global_pos(
            popup,
            global_anchor,
            anchor_rect_global=anchor_rect_global,
            bounds=bounds,
            gap=gap,
            margin=margin,
            max_width=max_width,
        )
    )


def position_child_popup_near_global_point(
    popup: QWidget,
    parent: QWidget,
    global_anchor: QPoint,
    *,
    anchor_rect_global: QRect | None = None,
    bounds: QRect | None = None,
    gap: int = DEFAULT_POPUP_GAP,
    margin: int = DEFAULT_POPUP_MARGIN,
    max_width: int | None = None,
) -> None:
    """Like ``position_popup_near_global_point`` but maps to *parent* local coords."""
    pos = clamp_popup_global_pos(
        popup,
        global_anchor,
        anchor_rect_global=anchor_rect_global,
        bounds=bounds,
        gap=gap,
        margin=margin,
        max_width=max_width,
    )
    popup.move(parent.mapFromGlobal(pos))


def position_popup_above_rect(
    popup: QWidget,
    anchor_rect_global: QRect,
    *,
    gap: int = 6,
    margin: int = DEFAULT_POPUP_MARGIN,
    bounds: QRect | None = None,
    h_align: str = "center",
) -> None:
    """Place *popup* above a global rect (tray icon, …)."""
    popup.adjustSize()
    w, h = popup.width(), popup.height()
    if h_align == "center":
        x = anchor_rect_global.center().x() - w // 2
    else:
        x = anchor_rect_global.left()
    y = anchor_rect_global.top() - h - gap

    ag = _bounds_for_point(QPoint(int(x), int(y)), bounds)
    if ag.isValid():
        x = max(ag.left() + margin, min(x, ag.right() - w - margin))
        y = max(ag.top() + margin, min(y, ag.bottom() - h - margin))
    popup.move(int(x), int(y))


def max_popup_height_in_widget(
    host: QWidget,
    *,
    top_offset: int = 0,
    margin: int = DEFAULT_POPUP_MARGIN,
) -> int:
    """Largest popup height from *top_offset* to the bottom of *host* (column / panel)."""
    if host is None or host.height() <= top_offset:
        return 480
    return max(120, host.height() - top_offset - margin)


def max_popup_height_for_anchor(
    anchor: QWidget,
    *,
    gap: int = DEFAULT_POPUP_GAP,
    margin: int = DEFAULT_POPUP_MARGIN,
    bounds: QRect | None = None,
) -> int:
    """Largest popup height that fits above or below *anchor*."""
    if anchor is None or not anchor.isVisible():
        return 480

    top_left = anchor.mapToGlobal(QPoint(0, 0))
    bottom_y = anchor.mapToGlobal(QPoint(0, anchor.height())).y()
    ag = _bounds_for_point(top_left, bounds)
    if not ag.isValid():
        return 480

    space_below = ag.bottom() - margin - (bottom_y + gap)
    space_above = (top_left.y() - gap) - (ag.top() + margin)
    return max(120, space_below, space_above)
