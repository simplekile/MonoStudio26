"""
Centralized notification service (singleton-like).
Exposes notify.info / success / warning / error; all UI and logic use only this API.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCursor

from monostudio.ui_qt.activity_log import activity_log
from monostudio.ui_qt.notification.overlay import NotificationOverlayWidget
from monostudio.ui_qt.notification.store import (
    UserAlertPayload,
    append_user_alert,
    unread_count as store_unread_count,
)
from monostudio.ui_qt.notification.toast import ToastType
from monostudio.ui_qt.notification.banner import ImportantNotificationBanner

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow, QWidget


class _NotificationService(QObject):
    """
    Single notification backend for the app.
    Requires set_main_window() to be called (e.g. from MainWindow) before first use.
    """

    unread_count_changed = Signal(int)

    _main_window: QMainWindow | None = None
    _main_view: QWidget | None = None
    _overlay: NotificationOverlayWidget | None = None
    _important_banner: ImportantNotificationBanner | None = None
    _important_anchor_widget: "QWidget | None" = None

    def __init__(self) -> None:
        super().__init__()

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
    def _emit_unread(cls) -> None:
        notify.unread_count_changed.emit(store_unread_count())

    @classmethod
    def _notify(cls, level: ToastType, message: str, *, category: str = "general") -> None:
        if os.getenv("MONOS_DEBUG_NOTI"):
            logging.getLogger("monostudio.notification").info(
                "NOTI [%s] (%s): %s", level, category, message
            )
        if category == "general":
            log_level = "error" if level == "error" else "warning" if level == "warning" else "success" if level == "success" else "info"
            activity_log.append(message, level=log_level)
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
    def user_alert(
        cls,
        message: str,
        *,
        payload: UserAlertPayload | None = None,
        toast_type: ToastType = "info",
    ) -> None:
        """User-targeted alert (e.g. @mention) — stored in bell history, not activity log."""
        append_user_alert(toast_type, message, payload=payload)
        cls._emit_unread()
        overlay = cls._get_overlay()
        if overlay is not None:
            overlay.show_toast(toast_type, message, category="general")
            overlay.raise_()

    @classmethod
    def important(cls, message: str, *, category: str = "general") -> None:
        """
        Persistent banner for important announcements (e.g. new update, first-run walkthrough).
        Shown as a non-modal banner near the top of the main window.
        """
        if category == "general":
            activity_log.append(message, level="info")

        if cls._main_window is None:
            return

        if cls._important_banner is None:
            banner = ImportantNotificationBanner(parent=cls._main_window)

            def _on_closed() -> None:
                if cls._important_banner is banner:
                    cls._important_banner = None

            banner.closed.connect(_on_closed)
            cls._important_banner = banner

        cls._important_banner.set_message(message)
        cls._important_banner.update_geometry_for_parent(cls._important_anchor_widget)
        cls._important_banner.show()
        cls._important_banner.raise_()


notify = _NotificationService()
