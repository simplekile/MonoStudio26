"""
Centralized notification service (singleton-like).
Exposes notify.info / success / warning / error; all UI and logic use only this API.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QCursor

from monostudio.core.notification_preferences import read_mention_delivery
from monostudio.core.windows_toast import show_mention_toast
from monostudio.ui_qt.notification.mention_alert_format import aggregated_mention_popup_message
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


def _plain_toast_message(message: str) -> str:
    """Strip simple HTML tags for Windows toast body."""
    text = (message or "").strip()
    if not text:
        return "New mention"
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip() or "New mention"


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
    _mention_popup_shown: set[str] = set()

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
            cls._overlay.installEventFilter(cls._main_window)
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
            # Routine ops (info/success) → footer only; avoid a toast on every action.
            if level in ("info", "success"):
                return
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
    def operational_success(cls, message: str) -> None:
        """Important user action: activity log + toast (not footer-only)."""
        msg = (message or "").strip()
        if not msg:
            return
        activity_log.append(msg, level="success")
        overlay = cls._get_overlay()
        if overlay is None:
            return
        overlay.show_toast("success", msg, category="general")
        overlay.raise_()

    @classmethod
    def warning(cls, message: str, *, category: str = "general") -> None:
        cls._notify("warning", message, category=category)

    @classmethod
    def error(cls, message: str, *, category: str = "general") -> None:
        cls._notify("error", message, category=category)

    @classmethod
    def reset_mention_popup_session(cls) -> None:
        """Allow @mention popups again for unread items (e.g. after switch user)."""
        cls._mention_popup_shown.clear()

    @classmethod
    def _show_mention_popup_message(
        cls,
        message: str,
        *,
        toast_type: ToastType = "info",
    ) -> bool:
        use_windows = read_mention_delivery(QSettings("MonoStudio26", "MonoStudio26")) == "windows"
        if use_windows:
            plain = _plain_toast_message(message)
            if show_mention_toast("MONOS", plain):
                return True
            logging.getLogger("monostudio.windows_toast").warning(
                "Windows mention toast failed; using in-app fallback",
            )
        overlay = cls._get_overlay()
        if overlay is not None:
            overlay.show_toast(toast_type, message, category="general")
            overlay.raise_()
            return True
        return False

    @classmethod
    def deliver_mention_popup_batch(
        cls,
        items: list[tuple[str, str]],
        *,
        toast_type: ToastType = "info",
    ) -> None:
        """
        One popup for a batch of unread mentions.
        items: (mention_inbox_id, from_name) pairs not yet shown this session.
        """
        pending = [
            ((mid or "").strip(), (name or "").strip() or "Someone")
            for mid, name in items
            if (mid or "").strip() and (mid or "").strip() not in cls._mention_popup_shown
        ]
        if not pending:
            return

        if len(pending) == 1:
            _mid, name = pending[0]
            from monostudio.core.notification_copy import pick_copy

            body = pick_copy(f"{name} đã nhắc bạn", f"{name} mentioned you")
        else:
            body = aggregated_mention_popup_message([name for _, name in pending])

        if cls._show_mention_popup_message(body, toast_type=toast_type):
            for mid, _ in pending:
                cls._mention_popup_shown.add(mid)

    @classmethod
    def deliver_assign_popup_batch(
        cls,
        items: list[tuple[str, str]],
        *,
        toast_type: ToastType = "info",
    ) -> None:
        """One popup for a batch of unread schedule assignments."""
        from monostudio.core.notification_copy import pick_copy

        pending = [
            ((aid or "").strip(), (name or "").strip() or "Someone")
            for aid, name in items
            if (aid or "").strip() and (aid or "").strip() not in cls._mention_popup_shown
        ]
        if not pending:
            return

        if len(pending) == 1:
            _aid, name = pending[0]
            body = pick_copy(f"{name} đã giao việc cho bạn", f"{name} assigned work to you")
        else:
            names = [name for _, name in pending]
            body = pick_copy(
                f"{names[0]} và người khác đã giao việc cho bạn +{len(pending) - 1}",
                f"{names[0]} and others assigned work to you +{len(pending) - 1}",
            )

        if cls._show_mention_popup_message(body, toast_type=toast_type):
            for aid, _ in pending:
                cls._mention_popup_shown.add(aid)

    @classmethod
    def deliver_mention_popup(
        cls,
        message: str,
        *,
        mention_inbox_id: str = "",
        toast_type: ToastType = "info",
    ) -> None:
        """Show @mention popup for a single inbox id (delegates to batch API)."""
        mid = (mention_inbox_id or "").strip()
        if not mid:
            cls._show_mention_popup_message(message, toast_type=toast_type)
            return
        name = "Someone"
        if " mentioned you in " in message:
            name = message.split(" mentioned you in ", 1)[0].strip() or name
        cls.deliver_mention_popup_batch([(mid, name)], toast_type=toast_type)

    @classmethod
    def user_alert(
        cls,
        message: str,
        *,
        payload: UserAlertPayload | None = None,
        toast_type: ToastType = "info",
        read: bool = False,
        show_popup: bool = True,
    ) -> None:
        """User-targeted alert (e.g. @mention) — stored in bell history, not activity log."""
        append_user_alert(toast_type, message, payload=payload, read=read)
        cls._emit_unread()
        if read or not show_popup:
            return
        mid = ""
        aid = ""
        name = "Someone"
        if payload is not None:
            mid = (payload.mention_inbox_id or "").strip()
            aid = (payload.assign_inbox_id or "").strip()
            name = (payload.from_name or "").strip() or name
        if aid:
            cls.deliver_assign_popup_batch([(aid, name)], toast_type=toast_type)
        elif mid:
            cls.deliver_mention_popup_batch([(mid, name)], toast_type=toast_type)
        else:
            cls.deliver_mention_popup(message, mention_inbox_id=mid, toast_type=toast_type)

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

    @classmethod
    def mark_all_read(
        cls,
        *,
        user_id: str = "",
        project_root: Path | str | None = None,
    ) -> None:
        """Mark all in-app bell notifications read for the current user (+ inbox rows)."""
        from pathlib import Path as PathCls

        from monostudio.ui_qt.notification.store import mark_all_read as store_mark_all

        uid = (user_id or "").strip()
        pr = PathCls(project_root) if project_root is not None else None
        store_mark_all(user_id=uid, project_root=pr)
        if uid and pr is not None:
            try:
                from monostudio.core.mention_inbox import mark_all_read as mention_mark_all

                mention_mark_all(pr, uid)
            except OSError:
                pass
            try:
                from monostudio.core.assign_inbox import items_for_user, mark_read as assign_mark_read

                for item in items_for_user(pr, uid):
                    if not item.read:
                        assign_mark_read(pr, item.id)
            except OSError:
                pass
        cls._emit_unread()


notify = _NotificationService()
