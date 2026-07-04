"""Windows WM_NCHITTEST + Qt edge handles for frameless window resize."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from PySide6.QtCore import QByteArray, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget

RESIZE_MARGIN_PX = 5
MEDIA_PLAYER_RESIZE_MARGIN_PX = 10

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
    client_size: QSize | None = None,
    excluded_rects: Sequence[QRect] | None = None,
    margin: int = RESIZE_MARGIN_PX,
) -> int:
    """Return a Windows HT* code for a point in window coordinates."""
    if not window.isVisible() or window.isMaximized() or window.isFullScreen():
        return 0
    if client_size is not None and client_size.width() > 0 and client_size.height() > 0:
        w, h = client_size.width(), client_size.height()
    else:
        r = window.rect()
        if r.isEmpty():
            return 0
        w, h = r.width(), r.height()
    x, y = point_window.x(), point_window.y()
    m = margin
    on_left = x < m
    on_right = x >= w - m
    on_top = y < m
    on_bottom = y >= h - m
    for ex in excluded_rects or ():
        if ex.isValid() and ex.contains(point_window):
            return HTCLIENT
    # Window corners beat title bar drag.
    if on_top and on_left:
        return HTTOPLEFT
    if on_top and on_right:
        return HTTOPRIGHT
    if on_bottom and on_left:
        return HTBOTTOMLEFT
    if on_bottom and on_right:
        return HTBOTTOMRIGHT
    # Edge bands beat title bar — keeps a resize strip along each border.
    if on_top:
        return HTTOP
    if on_bottom:
        return HTBOTTOM
    if on_left:
        return HTLEFT
    if on_right:
        return HTRIGHT
    if caption_rect is not None and caption_rect.isValid() and caption_rect.contains(point_window):
        return HTCLIENT
    return 0


def handle_native_event(
    window: QWidget,
    event_type: QByteArray,
    message: object,
    caption_rect_fn: Callable[[], QRect],
    *,
    excluded_rects_fn: Callable[[], Sequence[QRect]] | None = None,
    margin: int = RESIZE_MARGIN_PX,
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
        hwnd = int(msg.hWnd) if msg.hWnd else int(window.winId())
        try:
            import win32api
            import win32gui

            x_pos, y_pos = win32gui.ScreenToClient(hwnd, win32api.GetCursorPos())
            pt = QPoint(int(x_pos), int(y_pos))
            client = win32gui.GetClientRect(hwnd)
            client_size = QSize(client[2] - client[0], client[3] - client[1])
        except ImportError:
            sx, sy = _screen_xy_from_lparam(int(msg.lParam))
            pt = window.mapFromGlobal(QPoint(sx, sy))
            client_size = None
        excluded = excluded_rects_fn() if excluded_rects_fn is not None else ()
        hit = hit_test(
            window,
            pt,
            caption_rect_fn(),
            client_size=client_size,
            excluded_rects=excluded,
            margin=margin,
        )
        if hit == 0:
            return None
        return True, hit
    except Exception:
        return None


def resize_edges_at(
    window: QWidget,
    point_window: QPoint,
    *,
    margin: int = RESIZE_MARGIN_PX,
    caption_rect: QRect | None = None,
) -> Qt.Edge | None:
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
    if on_top and on_left:
        return Qt.Edge.LeftEdge | Qt.Edge.TopEdge
    if on_top and on_right:
        return Qt.Edge.RightEdge | Qt.Edge.TopEdge
    if on_bottom and on_left:
        return Qt.Edge.LeftEdge | Qt.Edge.BottomEdge
    if on_bottom and on_right:
        return Qt.Edge.RightEdge | Qt.Edge.BottomEdge
    if on_top:
        return Qt.Edge.TopEdge
    if on_bottom:
        return Qt.Edge.BottomEdge
    if caption_rect is not None and caption_rect.isValid() and caption_rect.contains(point_window):
        return None
    if on_left:
        return Qt.Edge.LeftEdge
    if on_right:
        return Qt.Edge.RightEdge
    return None


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
    left = bool(edges & Qt.Edge.LeftEdge)
    right = bool(edges & Qt.Edge.RightEdge)
    top = bool(edges & Qt.Edge.TopEdge)
    bottom = bool(edges & Qt.Edge.BottomEdge)
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return None


def _win32_start_resize(window: QWidget, edges: Qt.Edge) -> bool:
    """Begin OS resize loop (Windows). Prefer over QWindow.startSystemResize on frameless hosts."""
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
        cx, cy = win32api.GetCursorPos()
        l_param = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
        win32api.SendMessage(hwnd, win32con.WM_NCLBUTTONDOWN, ht, l_param)
        return True
    except Exception:
        return False


def _start_system_resize(window: QWidget, edges: Qt.Edge, *, global_pos: QPoint | None = None) -> bool:
    if _win32_start_resize(window, edges):
        return True
    wh = window.windowHandle()
    if wh is None:
        return False
    try:
        wh.startSystemResize(edges)
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
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        win = self.window()
        if win is None:
            super().mousePressEvent(event)
            return
        gpos = event.globalPosition().toPoint()
        if _start_system_resize(win, self._edges, global_pos=gpos):
            event.accept()
            return
        super().mousePressEvent(event)


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
            ("tr", Qt.Edge.RightEdge | Qt.Edge.TopEdge),
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
        side_top = max(m, chrome_h)
        rects: dict[str, QRect] = {
            "tl": QRect(0, 0, m, m),
            "tr": QRect(w - m, 0, m, m),
            "bl": QRect(0, h - m, m, m),
            "br": QRect(w - m, h - m, m, m),
            "l": QRect(0, side_top, m, max(m, h - side_top - m)),
            "r": QRect(w - m, side_top, m, max(m, h - side_top - m)),
            "t": QRect(),
            "b": QRect(m, h - m, max(m, w - 2 * m), m),
        }
        if chrome_h < m:
            rects["t"] = QRect(m, 0, max(m, w - 2 * m - right_reserve), m)
        for handle, tag in self._handles:
            rect = rects[tag]
            if rect.width() <= 0 or rect.height() <= 0:
                handle.hide()
                continue
            handle.setGeometry(rect)
            handle.show()

    def raise_handles(self) -> None:
        corner_tags = frozenset({"tl", "tr", "bl", "br"})
        for handle, tag in self._handles:
            if handle.isVisible() and tag not in corner_tags:
                handle.raise_()
        for handle, tag in self._handles:
            if handle.isVisible() and tag in corner_tags:
                handle.raise_()
