"""Windows WM_NCHITTEST + Qt edge handles for frameless window resize."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from PySide6.QtCore import QByteArray, QPoint, QRect, Qt
from PySide6.QtGui import QMouseEvent
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


def _is_windows_generic_msg(event_type: QByteArray | bytes | str) -> bool:
    if isinstance(event_type, QByteArray):
        return bytes(event_type) == b"windows_generic_MSG"
    if isinstance(event_type, bytes):
        return event_type == b"windows_generic_MSG"
    return str(event_type) == "windows_generic_MSG"


def _screen_xy_from_lparam(lparam: int) -> tuple[int, int]:
    import ctypes

    x = ctypes.c_int16(lparam & 0xFFFF).value
    y = ctypes.c_int16((lparam >> 16) & 0xFFFF).value
    return x, y


def _msg_address(message: object) -> int:
    if hasattr(message, "__int__"):
        return int(message)
    return int(message)


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
    # Top-bar drag/buttons are handled in Qt (_VideoPreviewTopBar); HTCAPTION here
    # steals clicks from child widgets (transport, timeline, scrubber, …).
    return 0


def handle_native_event(
    window: QWidget,
    event_type: QByteArray,
    message: object,
    caption_rect_fn: Callable[[], QRect],
    *,
    excluded_rects_fn: Callable[[], Sequence[QRect]] | None = None,
) -> tuple[bool, int] | None:
    """Handle WM_NCHITTEST; return (handled, hit_code) or None."""
    if sys.platform != "win32" or not _is_windows_generic_msg(event_type):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(_msg_address(message))
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


def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
    if (edges & Qt.Edge.TopEdge) and (edges & Qt.Edge.LeftEdge):
        return Qt.CursorShape.SizeFDiagCursor
    if (edges & Qt.Edge.BottomEdge) and (edges & Qt.Edge.RightEdge):
        return Qt.CursorShape.SizeFDiagCursor
    if (edges & Qt.Edge.TopEdge) and (edges & Qt.Edge.RightEdge):
        return Qt.CursorShape.SizeBDiagCursor
    if (edges & Qt.Edge.BottomEdge) and (edges & Qt.Edge.LeftEdge):
        return Qt.CursorShape.SizeBDiagCursor
    if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
        return Qt.CursorShape.SizeHorCursor
    return Qt.CursorShape.SizeVerCursor


def _edges_to_ht(edges: Qt.Edge) -> int | None:
    if edges == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge):
        return HTTOPLEFT
    if edges == (Qt.Edge.RightEdge | Qt.Edge.TopEdge):
        return HTTOPRIGHT
    if edges == (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
        return HTBOTTOMLEFT
    if edges == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
        return HTBOTTOMRIGHT
    if edges == Qt.Edge.LeftEdge:
        return HTLEFT
    if edges == Qt.Edge.RightEdge:
        return HTRIGHT
    if edges == Qt.Edge.TopEdge:
        return HTTOP
    if edges == Qt.Edge.BottomEdge:
        return HTBOTTOM
    return None


def _begin_win32_resize(window: QWidget, edges: Qt.Edge) -> bool:
    if sys.platform != "win32":
        return False
    ht = _edges_to_ht(edges)
    if ht is None:
        return False
    try:
        import win32api
        import win32con
        import win32gui

        hwnd = int(window.winId())
        if not hwnd:
            return False
        win32gui.ReleaseCapture()
        win32api.SendMessage(hwnd, win32con.WM_NCLBUTTONDOWN, ht, 0)
        return True
    except Exception:
        return False


class _ResizeHandle(QWidget):
    """Thin invisible edge target; works when embedded native video blocks WM_NCHITTEST."""

    def __init__(self, parent: QWidget, edges: Qt.Edge) -> None:
        super().__init__(parent)
        self._edges = edges
        self.setObjectName("FramelessResizeHandle")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setCursor(_cursor_for_edges(edges))
        self.setMouseTracking(True)
        self._dragging = False
        self._press_global: QPoint | None = None
        self._start_geom: QRect | None = None

    def _apply_manual_resize(self, win: QWidget, global_pos: QPoint) -> None:
        if self._press_global is None or self._start_geom is None:
            return
        delta = global_pos - self._press_global
        geom = QRect(self._start_geom)
        min_w = max(win.minimumWidth(), 1)
        min_h = max(win.minimumHeight(), 1)
        e = self._edges
        if e & Qt.Edge.LeftEdge:
            new_left = self._start_geom.left() + delta.x()
            if self._start_geom.right() - new_left + 1 >= min_w:
                geom.setLeft(new_left)
            else:
                geom.setLeft(self._start_geom.right() - min_w + 1)
        if e & Qt.Edge.RightEdge:
            new_right = self._start_geom.right() + delta.x()
            if new_right - self._start_geom.left() + 1 >= min_w:
                geom.setRight(new_right)
            else:
                geom.setRight(self._start_geom.left() + min_w - 1)
        if e & Qt.Edge.TopEdge:
            new_top = self._start_geom.top() + delta.y()
            if self._start_geom.bottom() - new_top + 1 >= min_h:
                geom.setTop(new_top)
            else:
                geom.setTop(self._start_geom.bottom() - min_h + 1)
        if e & Qt.Edge.BottomEdge:
            new_bottom = self._start_geom.bottom() + delta.y()
            if new_bottom - self._start_geom.top() + 1 >= min_h:
                geom.setBottom(new_bottom)
            else:
                geom.setBottom(self._start_geom.top() + min_h - 1)
        win.setGeometry(geom)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        win = self.window()
        if win is None:
            super().mousePressEvent(event)
            return
        self._dragging = True
        self._press_global = event.globalPosition().toPoint()
        self._start_geom = win.frameGeometry()
        self.grabMouse()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and self._press_global is not None and self._start_geom is not None:
            win = self.window()
            if win is not None:
                self._apply_manual_resize(win, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._press_global = None
            self._start_geom = None
            self.releaseMouse()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FramelessResizeHandles:
    """Edge/corner hit targets raised above client content for frameless resize."""

    def __init__(
        self,
        window: QWidget,
        *,
        margin: int = RESIZE_MARGIN_PX,
        top_chrome_h: int = 0,
        top_right_reserve: int = 0,
    ) -> None:
        self._window = window
        self._margin = margin
        self._top_chrome_h = top_chrome_h
        self._top_right_reserve = top_right_reserve
        self._handles: list[tuple[_ResizeHandle, str]] = []
        for tag, edges in (
            ("tl", Qt.Edge.LeftEdge | Qt.Edge.TopEdge),
            ("bl", Qt.Edge.LeftEdge | Qt.Edge.BottomEdge),
            ("br", Qt.Edge.RightEdge | Qt.Edge.BottomEdge),
            ("l", Qt.Edge.LeftEdge),
            ("r", Qt.Edge.RightEdge),
            ("t", Qt.Edge.TopEdge),
            ("b", Qt.Edge.BottomEdge),
        ):
            handle = _ResizeHandle(window, edges)
            handle.hide()
            self._handles.append((handle, tag))

    def set_top_chrome_h(self, height: int) -> None:
        self._top_chrome_h = max(0, int(height))

    def set_top_right_reserve(self, width: int) -> None:
        self._top_right_reserve = max(0, int(width))

    def sync_geometry(self) -> None:
        win = self._window
        if not win.isVisible() or win.isFullScreen() or win.isMaximized():
            for handle, _ in self._handles:
                handle.hide()
            return
        w = max(1, win.width())
        h = max(1, win.height())
        m = self._margin
        chrome_h = self._top_chrome_h
        right_reserve = self._top_right_reserve
        rects: dict[str, QRect] = {
            "tl": QRect(0, 0, m, m),
            "bl": QRect(0, h - m, m, m),
            "br": QRect(w - m, h - m, m, m),
            "l": QRect(0, m, m, max(m, h - 2 * m)),
            "r": QRect(w - m, max(m, chrome_h), m, max(m, h - max(m, chrome_h) - m)),
            "t": QRect(m, 0, max(m, w - 2 * m - right_reserve), m),
            "b": QRect(m, h - m, max(m, w - 2 * m), m),
        }
        for handle, tag in self._handles:
            rect = rects[tag]
            if rect.width() <= 0 or rect.height() <= 0:
                handle.hide()
                continue
            handle.setGeometry(rect)
            handle.show()

    def raise_handles(self) -> None:
        for handle, _ in self._handles:
            if handle.isVisible():
                handle.raise_()
