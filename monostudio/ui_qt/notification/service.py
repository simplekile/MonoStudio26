"""
Centralized notification service (singleton-like).
Exposes notify.info / success / warning / error; all UI and logic use only this API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.notification.overlay import NotificationOverlayWidget
from monostudio.ui_qt.notification.store import append as _store_append
from monostudio.ui_qt.notification.toast import ToastType

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow, QWidget


class _NotificationService:
    """
    Single notification backend for the app.
    Requires set_main_window() to be called (e.g. from MainWindow) before first use.
    """

    _main_window: QMainWindow | None = None
    _main_view: QWidget | None = None
    _overlay: NotificationOverlayWidget | None = None

    @classmethod
    def set_main_window(cls, main_window: QMainWindow, main_view: QWidget | None = None) -> None:
        cls._main_window = main_window
        cls._main_view = main_view

    @classmethod
    def _get_overlay(cls) -> NotificationOverlayWidget | None:
        if cls._main_window is None:
            return None
        if cls._overlay is None:
            cls._overlay = NotificationOverlayWidget(
                parent=cls._main_window,
                main_view=cls._main_view,
            )
            cls._overlay.setGeometry(cls._main_window.rect())
            cls._overlay.raise_()
            cls._overlay.show()
        return cls._overlay

    @classmethod
    def update_overlay_geometry(cls) -> None:
        """Call from MainWindow.resizeEvent so the overlay fills the window."""
        if cls._main_window is not None and cls._overlay is not None:
            cls._overlay.setGeometry(cls._main_window.rect())
            cls._overlay.raise_()

    @classmethod
    def set_general_toast_anchor_widget(cls, widget: "QWidget | None") -> None:
        """Position general toasts below this widget (e.g. topbar noti button). Call after set_main_window."""
        overlay = cls._get_overlay()
        if overlay is None:
            return
        overlay.set_general_toast_anchor_widget(widget)

    @classmethod
    def set_sidebar_anchor_from_cursor(cls) -> None:
        """Anchor sidebar toasts vertically near current cursor position (typically sidebar click)."""
        overlay = cls._get_overlay()
        if overlay is None:
            return
        pos = QCursor.pos()
        overlay.set_sidebar_anchor_y_from_global(pos.y())

    @classmethod
    def set_sidebar_anchor_from_global_y(cls, y: int | None) -> None:
        """Anchor sidebar toasts vertically using an explicit global Y coordinate (item row)."""
        overlay = cls._get_overlay()
        if overlay is None:
            return
        overlay.set_sidebar_anchor_y_from_global(y)

    @classmethod
    def _notify(cls, level: ToastType, message: str, *, category: str = "general") -> None:
        if category == "general":
            _store_append(level, message)
        overlay = cls._get_overlay()
        if overlay is None:
            return
        overlay.show_toast(level, message, category=category)
        overlay.raise_()

    @classmethod
    def info(cls, message: str, *, category: str = "general") -> None:
        cls._notify("info", message, category=category)

    @classmethod
    def success(cls, message: str, *, category: str = "general") -> None:
        cls._notify("success", message, category=category)

    @classmethod
    def warning(cls, message: str, *, category: str = "general") -> None:
        cls._notify("warning", message, category=category)

    @classmethod
    def error(cls, message: str, *, category: str = "general") -> None:
        cls._notify("error", message, category=category)


# Singleton-like instance; use via notify.info(), notify.success(), etc.
notify = _NotificationService()
