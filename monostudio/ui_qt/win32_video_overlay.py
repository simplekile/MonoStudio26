"""Windows z-order helpers for mpv embed + Qt review draw overlay."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
HWND_TOP = 0
SWP_FLAGS = 0x0002 | 0x0001 | 0x0010 | 0x0040  # NOMOVE | NOSIZE | NOACTIVATE | SHOWWINDOW


def _user32():
    return ctypes.windll.user32


def _enum_child_hwnds(parent: int) -> list[int]:
    if sys.platform != "win32" or not parent:
        return []
    user32 = _user32()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumChildWindows(parent, _cb, 0)
    return found


def _raise_hwnd_top(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        _user32().SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_FLAGS)
    except Exception as e:
        logger.debug("raise_hwnd_top: %s", e)


def _set_hwnd_mouse_pass_through(hwnd: int, enabled: bool) -> None:
    if not hwnd:
        return
    try:
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception as e:
        logger.debug("hwnd mouse pass-through: %s", e)


def raise_embedded_video_surface(video_surface: QWidget) -> None:
    """Raise mpv host HWND and visible child windows (default playback stacking)."""
    if sys.platform != "win32":
        return
    if video_surface is None or not video_surface.isVisible():
        return
    parent = int(video_surface.winId())
    if not parent:
        return
    _raise_hwnd_top(parent)
    for child in _enum_child_hwnds(parent):
        _raise_hwnd_top(child)


def restore_embedded_video_input(video_surface: QWidget) -> None:
    """Clear WS_EX_TRANSPARENT on mpv child HWNDs so scrub/MMB work again."""
    if sys.platform != "win32" or video_surface is None:
        return
    parent = int(video_surface.winId())
    if not parent:
        return
    for child in _enum_child_hwnds(parent):
        _set_hwnd_mouse_pass_through(child, False)


def sync_video_draw_overlay_zorder(
    video_surface: QWidget,
    draw_overlay: QWidget,
    *,
    mouse_pass_through: bool,
) -> None:
    """Stack draw overlay above embedded mpv; pass mouse through mpv when drawing."""
    if sys.platform != "win32":
        return
    if video_surface is None or draw_overlay is None:
        return
    if not draw_overlay.isVisible():
        restore_embedded_video_input(video_surface)
        raise_embedded_video_surface(video_surface)
        return

    raise_embedded_video_surface(video_surface)
    parent = int(video_surface.winId())
    if parent:
        for child in _enum_child_hwnds(parent):
            _set_hwnd_mouse_pass_through(child, mouse_pass_through)

    draw_overlay.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    overlay_hwnd = int(draw_overlay.winId())
    if overlay_hwnd:
        _set_hwnd_mouse_pass_through(overlay_hwnd, False)
        _raise_hwnd_top(overlay_hwnd)
