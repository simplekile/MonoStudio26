"""
Centralized notification service (singleton-like).
Exposes notify.info / success / warning / error; all UI and logic use only this API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import os
import logging

from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.notification.overlay import NotificationOverlayWidget
from monostudio.ui_qt.notification.store import append as _store_append
from monostudio.ui_qt.notification.toast import ToastType
from monostudio.ui_qt.notification.banner import ImportantNotificationBanner

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
    _important_banner: ImportantNotificationBanner | None = None
    _important_anchor_widget: "QWidget | None" = None

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
        """Call from MainWindow.resizeEvent so overlay and banners follow window geometry."""
        if cls._main_window is not None:
            if cls._overlay is not None:
                cls._overlay.setGeometry(cls._main_window.rect())
                cls._overlay.raise_()
            if cls._important_banner is not None:
                cls._important_banner.update_geometry_for_parent(cls._important_anchor_widget)

    @classmethod
    def set_important_anchor_widget(cls, widget: "QWidget | None") -> None:
        """Anchor important banner under this widget (e.g. TopBar update button)."""
        cls._important_anchor_widget = widget
        if cls._important_banner is not None:
            cls._important_banner.update_geometry_for_parent(widget)

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
        # Optional debug: mirror notifications to log when env is set.
        if os.getenv("MONOS_DEBUG_NOTI"):
            logging.getLogger("monostudio.notification").info(
                "NOTI [%s] (%s): %s", level, category, message
            )
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

    @classmethod
    def important(cls, message: str, *, category: str = "general") -> None:
        """
        Persistent banner for important announcements (e.g. new update, first-run walkthrough).
        - Stored in notification history like other general notifications.
        - Shown as a non-modal banner near the top of the main window.
        """
        if category == "general":
            _store_append("important", message)

        if cls._main_window is None:
            # Fallback: no main window yet, store-only.
            return

        # Reuse existing banner if present.
        if cls._important_banner is None:
            banner = ImportantNotificationBanner(parent=cls._main_window)

            def _on_closed() -> None:
                # Clear reference when banner is closed.
                if cls._important_banner is banner:
                    cls._important_banner = None

            banner.closed.connect(_on_closed)
            cls._important_banner = banner

        cls._important_banner.set_message(message)
        cls._important_banner.update_geometry_for_parent(cls._important_anchor_widget)
        cls._important_banner.show()
        cls._important_banner.raise_()


# Singleton-like instance; use via notify.info(), notify.success(), etc.
notify = _NotificationService()
