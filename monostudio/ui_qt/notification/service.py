"""
Centralized notification service (singleton-like).
Exposes notify.info / success / warning / error; all UI and logic use only this API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.notification.overlay import NotificationOverlayWidget
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
    def _notify(cls, level: ToastType, message: str, *, category: str = "general") -> None:
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
