from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QToolButton,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification.notification_dropdown import NotificationDropdown
from monostudio.ui_qt.notification.notification_list_dialog import NotificationListDialog


class _UpdateBadge(QWidget):
    """Red dot badge on update button when a new release is available."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ef4444"))
        p.drawEllipse(1, 1, 8, 8)
        p.end()


class TopBar(QWidget):
    settings_clicked = Signal()
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    title_double_clicked = Signal()
    update_button_clicked = Signal()
    watcher_toggled = Signal(bool)  # True = watcher on, False = watcher off

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._drag_start_pos: QPoint | None = None

        # Window buttons — render at 24px then Qt scales down = sharper on HiDPI
        _win_icon_color = "#d4d4d8"
        _win_icon_size = 24
        self._btn_min = QToolButton(self)
        self._btn_min.setObjectName("WindowMinBtn")
        self._btn_min.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_min.setIcon(lucide_icon("minus", size=_win_icon_size, color_hex=_win_icon_color))
        self._btn_min.setFixedSize(44, 36)
        self._btn_min.clicked.connect(self.minimize_clicked.emit)
        self._btn_max = QToolButton(self)
        self._btn_max.setObjectName("WindowMaxBtn")
        self._btn_max.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_max.setIcon(lucide_icon("square", size=_win_icon_size, color_hex=_win_icon_color))
        self._btn_max.setFixedSize(44, 36)
        self._btn_max.clicked.connect(self.maximize_clicked.emit)
        self._btn_close = QToolButton(self)
        self._btn_close.setObjectName("WindowCloseBtn")
        self._btn_close.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_close.setIcon(lucide_icon("x", size=_win_icon_size, color_hex=_win_icon_color))
        self._btn_close.setFixedSize(44, 36)
        self._btn_close.clicked.connect(self.close_clicked.emit)

        # Update button (right side, before noti) — icon: download for "update"
        self._btn_update = QToolButton(self)
        self._btn_update.setObjectName("TopBarUpdateBtn")
        self._btn_update.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_update.setIcon(lucide_icon("download", size=20, color_hex=_win_icon_color))
        self._btn_update.setFixedSize(40, 36)
        self._btn_update.setToolTip("Check for updates")
        self._btn_update.clicked.connect(self.update_button_clicked.emit)
        self._update_badge = _UpdateBadge(self._btn_update)
        self._update_badge.move(26, 4)
        self._update_badge.hide()
        self._update_badge.raise_()

        # File watcher toggle (right side, before noti) — eye = watching, eye-off = paused
        self._btn_watcher = QToolButton(self)
        self._btn_watcher.setObjectName("TopBarWatcherBtn")
        self._btn_watcher.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_watcher.setCheckable(True)
        self._btn_watcher.setChecked(True)
        self._btn_watcher.setIcon(lucide_icon("eye", size=20, color_hex="#22c55e"))
        self._btn_watcher.setFixedSize(40, 36)
        self._btn_watcher.setToolTip("File watcher: on — pause (click) before rename/delete")
        self._btn_watcher.toggled.connect(self._on_watcher_toggled)
        self._watcher_busy = False
        self._watcher_blink_on = True
        self._watcher_busy_timer = QTimer(self)
        self._watcher_busy_timer.setInterval(400)
        self._watcher_busy_timer.timeout.connect(self._on_watcher_busy_blink)

        # Settings button (next to noti)
        self._btn_settings = QToolButton(self)
        self._btn_settings.setObjectName("TopBarSettingsBtn")
        self._btn_settings.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_settings.setIcon(lucide_icon("settings", size=20, color_hex=_win_icon_color))
        self._btn_settings.setFixedSize(40, 36)
        self._btn_settings.setToolTip("Settings")
        self._btn_settings.clicked.connect(self.settings_clicked.emit)

        # Notification button (right side, before window buttons)
        self._btn_noti = QToolButton(self)
        self._btn_noti.setObjectName("TopBarNotiBtn")
        self._btn_noti.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_noti.setIcon(lucide_icon("bell", size=20, color_hex=_win_icon_color))
        self._btn_noti.setFixedSize(40, 36)
        self._btn_noti.setToolTip("Notifications")
        self._noti_dropdown_closed_at = 0.0  # monotonic time when dropdown last closed (avoid reopen on same click)
        self._noti_dropdown = NotificationDropdown(self)
        self._noti_dropdown.show_all_requested.connect(self._open_notification_list_dialog)
        self._noti_dropdown.closed.connect(self._on_noti_dropdown_closed)
        self._btn_noti.clicked.connect(self._show_noti_dropdown)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 10, 8, 10)
        layout.setSpacing(0)
        layout.addStretch(1)
        layout.addWidget(self._btn_update, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_watcher, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_settings, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_noti, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_min, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_max, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_close, 0, Qt.AlignRight | Qt.AlignVCenter)

    # Grace period (seconds): if popup was closed less than this ago, next button click is treated as "close" not "open"
    _POPUP_REOPEN_GRACE = 0.25

    def _clear_tool_button_hover(self, btn: QToolButton) -> None:
        """Clear stuck hover/pressed state on a tool button (used after popup/dropdown closes)."""
        QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
        btn.setDown(False)
        try:
            st = btn.style()
            if st:
                st.unpolish(btn)
                st.polish(btn)
        except Exception:
            pass
        btn.update()

    def _show_noti_dropdown(self) -> None:
        """Toggle notification dropdown: if open, close; else if just closed (same click), do nothing; else show.
        Position is clamped so the dropdown stays inside the main window."""
        if self._noti_dropdown.isVisible():
            self._noti_dropdown.close()
            return
        if (time.monotonic() - self._noti_dropdown_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        btn = self._btn_noti
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        win = self.window()
        frame = win.frameGeometry()  # main window rect in global coords
        dw = self._noti_dropdown.width()
        dh = self._noti_dropdown.height()
        gap = 4
        margin = 8  # inset from window edges (8px grid)
        x = pos.x()
        y = pos.y() + gap
        # Clamp horizontally: keep dropdown inside window with margin
        if x + dw > frame.right() - margin:
            x = frame.right() - margin - dw
        if x < frame.left() + margin:
            x = frame.left() + margin
        # Clamp vertically: if would go below window, show above button; respect bottom margin
        if y + dh > frame.bottom() - margin:
            y = pos.y() - gap - dh
        if y < frame.top() + margin:
            y = frame.top() + margin
        self._noti_dropdown.move(x, y)
        self._noti_dropdown.show()

    def _on_noti_dropdown_closed(self) -> None:
        """Record close time and clear tool button hover/pressed state (deferred so it takes effect)."""
        self._noti_dropdown_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._btn_noti))

    def _on_watcher_toggled(self, checked: bool) -> None:
        self.watcher_toggled.emit(checked)
        self._update_watcher_button_appearance(checked)

    def _update_watcher_button_appearance(self, enabled: bool) -> None:
        if self._watcher_busy:
            return
        # On = green (safe to browse); Off = red (required for rename/delete)
        color = "#22c55e" if enabled else "#ef4444"
        self._btn_watcher.setIcon(
            lucide_icon("eye" if enabled else "eye-off", size=20, color_hex=color)
        )
        self._btn_watcher.setToolTip(
            "File watcher: on — pause (click) before rename/delete" if enabled
            else "File watcher: paused — rename/delete allowed (click to resume)"
        )

    def _on_watcher_busy_blink(self) -> None:
        """Timer tick while watcher is turning off: alternate icon brightness."""
        if not self._watcher_busy:
            return
        self._watcher_blink_on = not self._watcher_blink_on
        color = "#ef4444" if self._watcher_blink_on else "#7f1d1d"
        self._btn_watcher.setIcon(lucide_icon("eye-off", size=20, color_hex=color))

    def set_watcher_busy(self, busy: bool) -> None:
        """While True: button disabled and icon blinks (watcher is turning off)."""
        self._watcher_busy = busy
        self._btn_watcher.setEnabled(not busy)
        if busy:
            self._watcher_blink_on = True
            self._btn_watcher.setIcon(lucide_icon("eye-off", size=20, color_hex="#ef4444"))
            self._btn_watcher.setToolTip("Turning off file watcher…")
            self._watcher_busy_timer.start()
        else:
            self._watcher_busy_timer.stop()
            self._update_watcher_button_appearance(self._btn_watcher.isChecked())

    def set_watcher_enabled(self, enabled: bool) -> None:
        """Set watcher toggle state and icon (called from MainWindow when watcher is turned on/off)."""
        self._btn_watcher.blockSignals(True)
        self._btn_watcher.setChecked(enabled)
        self._btn_watcher.blockSignals(False)
        self._update_watcher_button_appearance(enabled)

    def get_noti_button(self) -> QToolButton:
        """Return the notification toolbar button (for anchoring general toasts below it)."""
        return self._btn_noti

    def get_update_button(self) -> QToolButton:
        """Return the update toolbar button (e.g. for showing tooltip at startup)."""
        return self._btn_update

    def set_update_available(self, available: bool, latest_version: str = "") -> None:
        """Show/hide red dot on update button and set tooltip (e.g. after startup check)."""
        if available:
            self._update_badge.show()
            self._btn_update.setToolTip(f"Update available: {latest_version}. Click to open Settings → Updates.")
        else:
            self._update_badge.hide()
            self._btn_update.setToolTip("Check for updates")

    def _open_notification_list_dialog(self) -> None:
        """Open the full notification list dialog (lazy-created)."""
        win = self.window()
        dlg = NotificationListDialog(win)
        dlg.show()

    def set_maximized(self, maximized: bool) -> None:
        """Update window button icon (Max vs Restore)."""
        _c = "#d4d4d8"
        if maximized:
            self._btn_max.setIcon(lucide_icon("maximize-2", size=24, color_hex=_c))
        else:
            self._btn_max.setIcon(lucide_icon("square", size=24, color_hex=_c))

    def _is_on_window_buttons(self, pos: QPoint) -> bool:
        return (
            self._btn_min.geometry().contains(pos)
            or self._btn_max.geometry().contains(pos)
            or self._btn_close.geometry().contains(pos)
            or self._btn_update.geometry().contains(pos)
            or self._btn_watcher.geometry().contains(pos)
            or self._btn_settings.geometry().contains(pos)
            or self._btn_noti.geometry().contains(pos)
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._is_on_window_buttons(event.pos()):
            self._drag_start_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win and win.windowHandle():
                try:
                    win.windowHandle().startSystemMove()
                    self._drag_start_pos = None
                except AttributeError:
                    delta = event.globalPosition().toPoint() - self._drag_start_pos
                    win.move(win.x() + delta.x(), win.y() + delta.y())
                    self._drag_start_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._is_on_window_buttons(event.pos()):
            self.title_double_clicked.emit()
        else:
            super().mouseDoubleClickEvent(event)
