"""Windows WM_NCHITTEST helpers for frameless window resize and caption drag."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from PySide6.QtCore import QByteArray, QPoint, QRect, Qt
from PySide6.QtWidgets import QWidget

RESIZE_MARGIN_PX = 8

HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTBOTTOM = 13
HTTOPLEFT = 14
HTTOPRIGHT = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

WM_NCHITTEST = 0x0084


def _screen_xy_from_lparam(lparam: int) -> tuple[int, int]:
    import ctypes

    x = ctypes.c_int16(lparam & 0xFFFF).value
    y = ctypes.c_int16((lparam >> 16) & 0xFFFF).value
    return x, y


def hit_test(
    window: QWidget,
    point_window: QPoint,
    caption_rect: QRect | None,
    *,
    excluded_rects: Sequence[QRect] | None = None,
) -> int:
    """Return a Windows HT* code for a point in window coordinates."""
    if not window.isVisible() or window.isMaximized() or window.isFullScreen():
        return 0
    r = window.rect()
    if r.isEmpty():
        return 0
    for ex in excluded_rects or ():
        if ex.isValid() and ex.contains(point_window):
            return HTCLIENT
    x, y = point_window.x(), point_window.y()
    m = RESIZE_MARGIN_PX
    w, h = r.width(), r.height()
    on_left = x < m
    on_right = x >= w - m
    on_top = y < m
    on_bottom = y >= h - m
    if on_top and on_left:
        return HTTOPLEFT
    if on_top and on_right:
        return HTTOPRIGHT
    if on_bottom and on_left:
        return HTBOTTOMLEFT
    if on_bottom and on_right:
        return HTBOTTOMRIGHT
    if on_top:
        return HTTOP
    if on_bottom:
        return HTBOTTOM
    if on_left:
        return HTLEFT
    if on_right:
        return HTRIGHT
    if caption_rect is not None and not caption_rect.isEmpty() and caption_rect.contains(point_window):
        return HTCAPTION
    return 0


def handle_native_event(
    window: QWidget,
    event_type: QByteArray,
    message: int,
    caption_rect_fn: Callable[[], QRect],
    *,
    excluded_rects_fn: Callable[[], Sequence[QRect]] | None = None,
) -> tuple[bool, int] | None:
    """Handle WM_NCHITTEST; return (handled, hit_code) or None."""
    if sys.platform != "win32" or bytes(event_type) != b"windows_generic_MSG":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        addr = message.__int__() if hasattr(message, "__int__") else int(message)
        msg = wintypes.MSG.from_address(addr)
        if msg.message != WM_NCHITTEST:
            return None
        try:
            import win32api
            import win32gui

            hwnd = int(window.winId())
            x_pos, y_pos = win32gui.ScreenToClient(hwnd, win32api.GetCursorPos())
            pt = QPoint(int(x_pos), int(y_pos))
        except ImportError:
            sx, sy = _screen_xy_from_lparam(int(msg.lParam))
            pt = window.mapFromGlobal(QPoint(sx, sy))
        excluded = excluded_rects_fn() if excluded_rects_fn is not None else ()
        hit = hit_test(window, pt, caption_rect_fn(), excluded_rects=excluded)
        if hit == 0:
            return None
        return True, hit
    except Exception:
        return None


def resize_edges_at(window: QWidget, point_window: QPoint, *, margin: int = RESIZE_MARGIN_PX) -> Qt.Edge | None:
    """Map a window-local point to Qt resize edges (mouse fallback when NCHITTEST fails)."""
    if not window.isVisible() or window.isMaximized() or window.isFullScreen():
        return None
    r = window.rect()
    if r.isEmpty():
        return None
    x, y = point_window.x(), point_window.y()
    w, h = r.width(), r.height()
    on_left = x < margin
    on_right = x >= w - margin
    on_top = y < margin
    on_bottom = y >= h - margin
    if not (on_left or on_right or on_top or on_bottom):
        return None
    edges = Qt.Edge(0)
    if on_left:
        edges |= Qt.Edge.LeftEdge
    if on_right:
        edges |= Qt.Edge.RightEdge
    if on_top:
        edges |= Qt.Edge.TopEdge
    if on_bottom:
        edges |= Qt.Edge.BottomEdge
    return edges
