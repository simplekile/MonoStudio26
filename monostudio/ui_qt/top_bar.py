from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification.notification_dropdown import NotificationDropdown
from monostudio.ui_qt.notification.notification_list_dialog import NotificationListDialog


class _PanelLayoutGlyphButton(QToolButton):
    """Cursor-style outline frame with sidebar strip (left) or inspector strip (bottom)."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind  # "sidebar" | "inspector"
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setFixedSize(32, 32)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setObjectName("TopBarPanelGlyphBtn")

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        checked = self.isChecked()
        stroke = QColor("#e4e4e7" if checked else "#71717a")
        fill = QColor("#60a5fa" if checked else "#52525b")
        r = self.rect().adjusted(7, 7, -7, -7)
        radius = 3.0
        p.setPen(stroke)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        inner = r.adjusted(3, 3, -3, -3)
        if self._kind == "sidebar":
            bar_w = max(4, min(5, inner.width() - 2))
            bar = QRectF(inner.left(), inner.top(), float(bar_w), float(inner.height()))
            p.drawRoundedRect(bar, 1.5, 1.5)
        else:
            bar_h = max(4.0, float(inner.height()) * 0.28)
            bar = QRectF(
                float(inner.left()),
                float(inner.bottom()) - bar_h + 0.5,
                float(inner.width()),
                bar_h - 0.5,
            )
            p.drawRoundedRect(bar, 1.5, 1.5)


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
    layout_auto_clicked = Signal()
    layout_sidebar_clicked = Signal()
    layout_inspector_clicked = Signal()

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
        _action_icon_w, _action_icon_h = 32, 36
        self._btn_update = QToolButton(self)
        self._btn_update.setObjectName("TopBarUpdateBtn")
        self._btn_update.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_update.setIcon(lucide_icon("download", size=20, color_hex=_win_icon_color))
        self._btn_update.setFixedSize(_action_icon_w, _action_icon_h)
        self._btn_update.setToolTip("Check for updates")
        self._btn_update.clicked.connect(self.update_button_clicked.emit)
        self._update_badge = _UpdateBadge(self._btn_update)
        self._update_badge.move(_action_icon_w - 12, 4)
        self._update_badge.hide()
        self._update_badge.raise_()

        # File watcher toggle (right side, before noti) — eye = watching, eye-off = paused
        self._btn_watcher = QToolButton(self)
        self._btn_watcher.setObjectName("TopBarWatcherBtn")
        self._btn_watcher.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_watcher.setCheckable(True)
        self._btn_watcher.setChecked(True)
        self._btn_watcher.setIcon(lucide_icon("eye", size=20, color_hex="#22c55e"))
        self._btn_watcher.setFixedSize(_action_icon_w, _action_icon_h)
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
        self._btn_settings.setFixedSize(_action_icon_w, _action_icon_h)
        self._btn_settings.setToolTip("Settings")
        self._btn_settings.clicked.connect(self.settings_clicked.emit)

        # Notification button (right side, before window buttons)
        self._btn_noti = QToolButton(self)
        self._btn_noti.setObjectName("TopBarNotiBtn")
        self._btn_noti.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_noti.setIcon(lucide_icon("bell", size=20, color_hex=_win_icon_color))
        self._btn_noti.setFixedSize(_action_icon_w, _action_icon_h)
        self._btn_noti.setToolTip("Notifications")
        self._noti_dropdown_closed_at = 0.0  # monotonic time when dropdown last closed (avoid reopen on same click)
        self._noti_dropdown = NotificationDropdown(self)
        self._noti_dropdown.show_all_requested.connect(self._open_notification_list_dialog)
        self._noti_dropdown.closed.connect(self._on_noti_dropdown_closed)
        self._btn_noti.clicked.connect(self._show_noti_dropdown)

        # Panel layout: Auto (responsive) + sidebar + inspector toggles (Cursor-style)
        self._panel_group = QWidget(self)
        self._panel_group.setObjectName("TopBarPanelGroup")
        panel_l = QHBoxLayout(self._panel_group)
        panel_l.setContentsMargins(2, 2, 2, 2)
        panel_l.setSpacing(0)
        self._btn_layout_auto = QToolButton(self._panel_group)
        self._btn_layout_auto.setObjectName("TopBarPanelAutoBtn")
        self._btn_layout_auto.setText("Auto")
        self._btn_layout_auto.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._btn_layout_auto.setFixedHeight(28)
        self._btn_layout_auto.setMinimumWidth(38)
        self._btn_layout_auto.setToolTip("Auto layout — hide sidebar and Inspector when the window is narrow")
        self._btn_layout_auto.clicked.connect(self._on_layout_auto_clicked)
        self._btn_layout_sidebar = _PanelLayoutGlyphButton("sidebar", self._panel_group)
        self._btn_layout_sidebar.setToolTip("Full sidebar or compact rail (56px)")
        self._btn_layout_sidebar.clicked.connect(self.layout_sidebar_clicked.emit)
        self._btn_layout_inspector = _PanelLayoutGlyphButton("inspector", self._panel_group)
        self._btn_layout_inspector.setToolTip("Show or hide Inspector")
        self._btn_layout_inspector.clicked.connect(self.layout_inspector_clicked.emit)
        panel_l.addWidget(self._btn_layout_auto, 0, Qt.AlignVCenter)
        panel_l.addWidget(self._btn_layout_sidebar, 0, Qt.AlignVCenter)
        panel_l.addWidget(self._btn_layout_inspector, 0, Qt.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 10, 8, 10)
        layout.setSpacing(0)
        layout.addWidget(self._panel_group, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addStretch(1)
        self._action_strip = QWidget(self)
        self._action_strip.setObjectName("TopBarActionStrip")
        action_l = QHBoxLayout(self._action_strip)
        action_l.setContentsMargins(0, 0, 0, 0)
        action_l.setSpacing(0)
        action_l.addWidget(self._btn_update, 0, Qt.AlignVCenter)
        action_l.addWidget(self._btn_watcher, 0, Qt.AlignVCenter)
        action_l.addWidget(self._btn_settings, 0, Qt.AlignVCenter)
        action_l.addWidget(self._btn_noti, 0, Qt.AlignVCenter)
        layout.addWidget(self._action_strip, 0, Qt.AlignRight | Qt.AlignVCenter)
        # Keep window chrome (min / max / close) at original spacing — gap before them only
        layout.addSpacing(10)
        layout.addWidget(self._btn_min, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_max, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_close, 0, Qt.AlignRight | Qt.AlignVCenter)

    def _on_layout_auto_clicked(self) -> None:
        """Always re-enter auto layout (segment stays active until user uses sidebar/inspector toggles)."""
        self.layout_auto_clicked.emit()

    def set_panel_layout_controls(self, *, auto: bool, sidebar_on: bool, inspector_on: bool) -> None:
        """Sync TopBar panel controls from MainWindow (block signals while updating)."""
        self._btn_layout_auto.setProperty("active", auto)
        self._btn_layout_auto.style().unpolish(self._btn_layout_auto)
        self._btn_layout_auto.style().polish(self._btn_layout_auto)
        self._btn_layout_auto.update()
        self._btn_layout_sidebar.blockSignals(True)
        self._btn_layout_sidebar.setChecked(sidebar_on)
        self._btn_layout_sidebar.blockSignals(False)
        self._btn_layout_sidebar.update()
        self._btn_layout_inspector.blockSignals(True)
        self._btn_layout_inspector.setChecked(inspector_on)
        self._btn_layout_inspector.blockSignals(False)
        self._btn_layout_inspector.update()

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
            self._panel_group.geometry().contains(pos)
            or self._action_strip.geometry().contains(pos)
            or self._btn_min.geometry().contains(pos)
            or self._btn_max.geometry().contains(pos)
            or self._btn_close.geometry().contains(pos)
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
