"""System tray icon, context menu, and quick actions for MONOS."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from monostudio.core.tray_preferences import read_tray_enabled
from monostudio.ui_qt.notification import notify as notification_service
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import position_popup_above_rect
from monostudio.ui_qt.recent_tasks_store import RecentTask
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu
from monostudio.ui_qt.tray_icon_badges import compose_tray_icon
from monostudio.ui_qt.tray_mini_popup import TrayMiniPopup

if TYPE_CHECKING:
    from monostudio.ui_qt.main_window import MainWindow

_log = logging.getLogger("monostudio.tray")

_MAX_RECENT_MENU = 8
_ICON_SIZE = 16
_POPUP_REOPEN_GRACE = 0.25

_CLR_ACTIVE = MONOS_COLORS.get("blue_400", "#60a5fa")
_CLR_LABEL = MONOS_COLORS.get("text_label", "#a1a1aa")
_CLR_MUTED = MONOS_COLORS.get("text_meta", "#71717a")
_CLR_WARN = "#f59e0b"
_CLR_DANGER = "#ef4444"


class TrayManager(QObject):
    def __init__(self, window: MainWindow, *, settings) -> None:
        super().__init__(window)
        self._window = window
        self._settings = settings
        self._tray: QSystemTrayIcon | None = None
        self._menu: MonosMenu | None = None
        self._mini_popup: TrayMiniPopup | None = None
        self._mini_popup_closed_at = 0.0
        self._shown_tray_hint = False
        self._base_icon = QIcon()
        self._has_notification = False
        self._has_update = False

    def is_available(self) -> bool:
        if not read_tray_enabled(self._settings):
            return False
        return QSystemTrayIcon.isSystemTrayAvailable()

    def install(self) -> bool:
        if not self.is_available():
            _log.warning("System tray is not available on this system")
            return False
        app = QApplication.instance()
        icon = self._window.windowIcon()
        if icon.isNull() and app is not None:
            icon = app.windowIcon()
        self._base_icon = icon
        self._tray = QSystemTrayIcon(self._window)
        self._apply_tray_icon()
        self._tray.setToolTip("MONOS")
        self._mini_popup = TrayMiniPopup()
        self._mini_popup.task_selected.connect(self._on_mini_task_selected)
        self._mini_popup.task_open_file_requested.connect(self._on_mini_task_open_file)
        self._mini_popup.entity_selected.connect(self._on_mini_entity_selected)
        self._mini_popup.entity_open_file_requested.connect(self._on_mini_entity_open_file)
        self._mini_popup.notification_selected.connect(self._on_mini_notification_selected)
        self._mini_popup.open_monos_requested.connect(self._window.present)
        self._mini_popup.open_notifications_requested.connect(
            lambda: self._window.present(open_notifications=True)
        )
        notification_service.unread_count_changed.connect(self._on_noti_count_for_mini_popup)
        app_state = getattr(self._window, "_app_state", None)
        if app_state is not None:
            app_state.thumbnailsChanged.connect(self._on_thumbnails_changed)
        self._rebuild_menu()
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        self.refresh_tooltip()
        return True

    def set_notification_pending(self, pending: bool) -> None:
        if self._has_notification == pending:
            return
        self._has_notification = pending
        self._apply_tray_icon()
        self.refresh_tooltip()
        self._rebuild_menu()

    def set_update_available(self, available: bool) -> None:
        if self._has_update == available:
            return
        self._has_update = available
        self._apply_tray_icon()
        self.refresh_tooltip()

    def _apply_tray_icon(self) -> None:
        if self._tray is None:
            return
        self._tray.setIcon(
            compose_tray_icon(
                self._base_icon,
                has_notification=self._has_notification,
                has_update=self._has_update,
            )
        )

    def refresh_tooltip(self) -> None:
        if self._tray is None:
            return
        parts = ["MONOS"]
        pr = getattr(self._window, "_project_root", None)
        if pr is not None:
            parts.append(pr.name or str(pr))
        plugin = getattr(self._window, "_pomodoro_plugin", None)
        if plugin is not None and plugin.engine().is_active():
            parts.append(plugin.status_text())
        if self._has_notification:
            parts.append("Unread notifications")
        if self._has_update:
            parts.append("Update available")
        self._tray.setToolTip(" — ".join(parts))

    def refresh_menu(self) -> None:
        self._rebuild_menu()
        # Never rebuild the mini-popup while hidden — creating QListWidgets/QLabels
        # during processEvents (page switch workers) can SystemError.
        self._reload_mini_popup_if_visible()

    def _reload_mini_popup_if_visible(self) -> None:
        popup = self._mini_popup
        if popup is None or not popup.isVisible():
            return
        self._reload_mini_popup()

    def show_tray_message(self, title: str, message: str) -> None:
        if self._tray is None:
            return
        self._tray.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._hide_mini_popup()
            self._window.present()
            return
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_mini_popup()

    def _toggle_mini_popup(self) -> None:
        popup = self._mini_popup
        if popup is None:
            return
        if popup.isVisible():
            popup.hide()
            self._mini_popup_closed_at = time.monotonic()
            return
        if (time.monotonic() - self._mini_popup_closed_at) < _POPUP_REOPEN_GRACE:
            return
        self._reload_mini_popup()
        self._position_mini_popup()
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def _hide_mini_popup(self) -> None:
        if self._mini_popup is not None and self._mini_popup.isVisible():
            self._mini_popup.hide()
            self._mini_popup_closed_at = time.monotonic()

    def _reload_mini_popup(self) -> None:
        popup = self._mini_popup
        if popup is None:
            return
        popup.reload_from_window(self._window)

    def _on_thumbnails_changed(self) -> None:
        if self._mini_popup is not None and self._mini_popup.isVisible():
            self._mini_popup.refresh_thumbnails()

    def _on_mini_entity_selected(
        self,
        kind: str,
        path: object,
        department: object,
        type_id: object,
    ) -> None:
        if not isinstance(path, Path):
            try:
                path = Path(str(path))
            except Exception:
                return
        dept = department if isinstance(department, str) else None
        typ = type_id if isinstance(type_id, str) else None
        self._window.open_tray_entity(
            item_type=str(kind),
            item_path=path,
            department=dept,
            type_id=typ,
        )

    def _position_mini_popup(self) -> None:
        popup = self._mini_popup
        tray = self._tray
        if popup is None or tray is None:
            return
        geo = tray.geometry()
        if geo.isValid() and geo.width() > 0 and geo.height() > 0:
            position_popup_above_rect(popup, geo)
            return
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            position_popup_above_rect(popup, screen.availableGeometry(), h_align="left")
        else:
            popup.move(100, 100)

    def _on_mini_task_selected(self, task: object) -> None:
        if isinstance(task, RecentTask):
            self._window.apply_recent_task(task, open_dcc=False)

    def _on_mini_task_open_file(self, task: object) -> None:
        if isinstance(task, RecentTask):
            self._window.apply_recent_task(task, open_dcc=True, present=False)

    def _on_mini_entity_open_file(
        self,
        kind: str,
        path: object,
        department: object,
        type_id: object,
    ) -> None:
        if not isinstance(path, Path):
            try:
                path = Path(str(path))
            except Exception:
                return
        dept = department if isinstance(department, str) else None
        typ = type_id if isinstance(type_id, str) else None
        self._window.open_tray_entity_file(
            item_type=str(kind),
            item_path=path,
            department=dept,
            type_id=typ,
        )

    def _on_mini_notification_selected(self, entry: object) -> None:
        self._window.present()
        self._window._on_user_alert_clicked(entry)

    def _on_noti_count_for_mini_popup(self, _count: int) -> None:
        popup = self._mini_popup
        if popup is not None and popup.isVisible():
            popup.refresh_notifications()

    @staticmethod
    def _menu_action(
        parent: QMenu,
        text: str,
        *,
        icon: QIcon | None = None,
        tooltip: str = "",
    ) -> QAction:
        act = QAction(text, parent)
        if icon is not None and not icon.isNull():
            act.setIcon(icon)
        if tooltip:
            act.setToolTip(tooltip)
        return act

    def _rebuild_menu(self) -> None:
        if self._tray is None:
            return
        if self._menu is not None:
            self._menu.deleteLater()
        menu = MonosMenu(self._window)
        self._menu = menu

        show_act = self._menu_action(
            menu,
            "Show MONOS",
            icon=lucide_icon("maximize-2", size=_ICON_SIZE, color_hex=_CLR_ACTIVE),
        )
        show_act.triggered.connect(self._window.present)
        menu.addAction(show_act)

        menu.addSeparator()

        last_proj = self._menu_action(
            menu,
            "Open last project",
            icon=lucide_icon("folder-open", size=_ICON_SIZE, color_hex=_CLR_LABEL),
        )
        last_proj.triggered.connect(self._window.restore_last_project_from_settings)
        menu.addAction(last_proj)

        menu.addSeparator()

        nav_icons = {
            "Assets": ("box", _CLR_ACTIVE),
            "Shots": ("clapperboard", _CLR_ACTIVE),
            "Inbox": ("inbox", _CLR_WARN),
        }
        for ctx, label in (("Assets", "Assets"), ("Shots", "Shots"), ("Inbox", "Inbox")):
            icon_name, color = nav_icons.get(label, ("circle", _CLR_LABEL))
            act = self._menu_action(
                menu,
                label,
                icon=lucide_icon(icon_name, size=_ICON_SIZE, color_hex=color),
            )
            act.triggered.connect(lambda _c=False, c=ctx: self._navigate_context(c))
            menu.addAction(act)

        noti_color = _CLR_DANGER if self._has_notification else _CLR_LABEL
        noti_act = self._menu_action(
            menu,
            "Notifications",
            icon=lucide_icon("bell", size=_ICON_SIZE, color_hex=noti_color),
            tooltip="Open notification center",
        )
        noti_act.triggered.connect(lambda: self._window.present(open_notifications=True))
        menu.addAction(noti_act)

        plugin = getattr(self._window, "_pomodoro_plugin", None)
        pomo_label = "Focus timer…"
        if plugin is not None and plugin.engine().is_active():
            pomo_label = f"Focus timer — {plugin.status_text()}"
        pomo_act = self._menu_action(
            menu,
            pomo_label,
            icon=lucide_icon("timer", size=_ICON_SIZE, color_hex=_CLR_ACTIVE),
            tooltip="Open Focus timer",
        )
        pomo_act.triggered.connect(self._window.open_pomodoro_timer)
        menu.addAction(pomo_act)

        menu.addSeparator()

        settings_act = self._menu_action(
            menu,
            "Settings…",
            icon=lucide_icon("settings", size=_ICON_SIZE, color_hex=_CLR_LABEL),
        )
        settings_act.triggered.connect(self._window.open_settings_tray_section)
        menu.addAction(settings_act)

        quit_act = self._menu_action(
            menu,
            "Quit",
            icon=lucide_icon("log-out", size=_ICON_SIZE, color_hex=_CLR_MUTED),
        )
        quit_act.triggered.connect(self._window.quit_application)
        menu.addAction(quit_act)

        self._tray.setContextMenu(menu)

    def _navigate_context(self, context: str) -> None:
        self._hide_mini_popup()
        self._window.present()
        nav_rail = getattr(self._window, "_nav_rail", None)
        filter_panel = getattr(self._window, "_filter_panel", None)
        if nav_rail is not None:
            nav_rail.set_current_context(context)
        if filter_panel is not None and hasattr(filter_panel, "sync_nav_context"):
            filter_panel.sync_nav_context(context)
