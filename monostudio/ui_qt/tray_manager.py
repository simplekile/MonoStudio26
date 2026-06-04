"""System tray icon, context menu, and quick actions for MONOS."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from monostudio.core.tray_preferences import read_tray_enabled
from monostudio.ui_qt.recent_tasks_store import RecentTask
from monostudio.ui_qt.style import MonosMenu

if TYPE_CHECKING:
    from monostudio.ui_qt.main_window import MainWindow

_log = logging.getLogger("monostudio.tray")

_MAX_RECENT_MENU = 8


class TrayManager(QObject):
    def __init__(self, window: MainWindow, *, settings) -> None:
        super().__init__(window)
        self._window = window
        self._settings = settings
        self._tray: QSystemTrayIcon | None = None
        self._menu: MonosMenu | None = None
        self._recent_submenu: QMenu | None = None
        self._shown_tray_hint = False

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
        self._tray = QSystemTrayIcon(icon, self._window)
        self._tray.setToolTip("MONOS")
        self._rebuild_menu()
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        self.refresh_tooltip()
        return True

    def refresh_tooltip(self) -> None:
        if self._tray is None:
            return
        pr = getattr(self._window, "_project_root", None)
        if pr is not None:
            name = pr.name or str(pr)
            self._tray.setToolTip(f"MONOS — {name}")
        else:
            self._tray.setToolTip("MONOS")

    def refresh_menu(self) -> None:
        self._rebuild_menu()

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
            self._window.present()

    def _rebuild_menu(self) -> None:
        if self._tray is None:
            return
        if self._menu is not None:
            self._menu.deleteLater()
        menu = MonosMenu("MONOS", self._window)
        self._menu = menu

        show_act = QAction("Show MONOS", menu)
        show_act.triggered.connect(self._window.present)
        menu.addAction(show_act)

        menu.addSeparator()

        self._recent_submenu = MonosMenu("Recent tasks", menu)
        self._populate_recent_tasks(self._recent_submenu)
        menu.addMenu(self._recent_submenu)

        last_proj = QAction("Open last project", menu)
        last_proj.triggered.connect(self._window.restore_last_project_from_settings)
        menu.addAction(last_proj)

        menu.addSeparator()

        for ctx, label in (
            ("Assets", "Assets"),
            ("Shots", "Shots"),
            ("Inbox", "Inbox"),
        ):
            act = QAction(label, menu)
            act.triggered.connect(lambda _c=False, c=ctx: self._navigate_context(c))
            menu.addAction(act)

        noti_act = QAction("Notifications", menu)
        noti_act.triggered.connect(lambda: self._window.present(open_notifications=True))
        menu.addAction(noti_act)

        menu.addSeparator()

        settings_act = QAction("Settings…", menu)
        settings_act.triggered.connect(self._window.open_settings_tray_section)
        menu.addAction(settings_act)

        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(self._window.quit_application)
        menu.addAction(quit_act)

        self._tray.setContextMenu(menu)

    def _populate_recent_tasks(self, submenu: QMenu) -> None:
        submenu.clear()
        store = getattr(self._window, "_recent_tasks_store", None)
        project_root = getattr(self._window, "_project_root", None)
        if store is None or project_root is None:
            empty = QAction("(No project open)", submenu)
            empty.setEnabled(False)
            submenu.addAction(empty)
            return
        tasks = store.get_for_project(project_root)[:_MAX_RECENT_MENU]
        if not tasks:
            empty = QAction("(No recent tasks)", submenu)
            empty.setEnabled(False)
            submenu.addAction(empty)
            return
        for task in tasks:
            label = self._task_label(task)
            open_act = QAction(label, submenu)
            open_act.triggered.connect(
                lambda _c=False, t=task: self._window.apply_recent_task(t, open_dcc=False)
            )
            submenu.addAction(open_act)
            if (task.department or "").strip() and (task.dcc or "").strip():
                dcc_act = QAction(f"Open in {task.dcc} — {label}", submenu)
                dcc_act.triggered.connect(
                    lambda _c=False, t=task: self._window.apply_recent_task(t, open_dcc=True)
                )
                submenu.addAction(dcc_act)

    @staticmethod
    def _task_label(task: RecentTask) -> str:
        dept = (task.department or "").strip()
        if dept:
            return f"{task.item_name} · {dept}"
        return task.item_name

    def _navigate_context(self, context: str) -> None:
        self._window.present()
        sidebar = getattr(self._window, "_sidebar", None)
        compact = getattr(self._window, "_sidebar_compact", None)
        if sidebar is not None:
            sidebar.set_current_context(context)
        if compact is not None:
            compact.set_current_context(context)
