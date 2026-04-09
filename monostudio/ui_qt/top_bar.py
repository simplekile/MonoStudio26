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

# TopBar panel cluster (Auto + glyphs): ~70% of original 44×28 + 32×32 footprint.
_PANEL_CLUSTER_SCALE = 0.7
_PANEL_AUTO_W = max(24, round(44 * _PANEL_CLUSTER_SCALE))
_PANEL_AUTO_H = max(18, round(28 * _PANEL_CLUSTER_SCALE))
_PANEL_GLYPH = max(18, round(32 * _PANEL_CLUSTER_SCALE))
_PANEL_GROUP_MARGIN = max(1, round(2 * _PANEL_CLUSTER_SCALE))


class _PanelLayoutGlyphButton(QToolButton):
    """Cursor-style outline frame with sidebar strip (left) or inspector strip (right)."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind  # "sidebar" | "inspector"
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setFixedSize(_PANEL_GLYPH, _PANEL_GLYPH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setObjectName("TopBarPanelGlyphBtn")

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        checked = self.isChecked()
        enabled = self.isEnabled()
        auto_muted = self.property("autoMuted") is True
        if not enabled:
            stroke = QColor("#52525b")
            fill = QColor("#3f3f46" if checked else "#27272a")
        elif auto_muted:
            # Auto on: still clickable, softer than full manual styling.
            stroke = QColor("#e4e4e7" if checked else "#52525b")
            fill = QColor("#3b82f6" if checked else "#3f3f46")
        else:
            stroke = QColor("#e4e4e7" if checked else "#71717a")
            fill = QColor("#60a5fa" if checked else "#52525b")
        side = min(self.width(), self.height())
        # Proportional to legacy 32px tile (inset 8): keeps glyph scale when cluster is shrunk.
        inset = max(4, min(8, int(round(side * 8.0 / 32.0))))
        r = self.rect().adjusted(inset, inset, -inset, -inset)
        radius = 2.0 if side < 24 else 2.5 if side < 28 else 3.0
        p.setPen(stroke)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        inner_pad = max(1, int(round(3.0 * side / 32.0)))
        inner = r.adjusted(inner_pad, inner_pad, -inner_pad, -inner_pad)
        bar_r = 1.2 if side < 24 else 1.5
        if self._kind == "sidebar":
            bar_w = max(3, min(5, int(inner.width()) - 2))
            bar = QRectF(inner.left(), inner.top(), float(bar_w), float(inner.height()))
            p.drawRoundedRect(bar, bar_r, bar_r)
        else:
            # Inspector: mirror of sidebar glyph (right strip)
            bar_w = max(3, min(5, int(inner.width()) - 2))
            bar = QRectF(
                float(inner.right()) - float(bar_w) + 0.5,
                float(inner.top()),
                float(bar_w) - 0.5,
                float(inner.height()),
            )
            p.drawRoundedRect(bar, bar_r, bar_r)


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
    always_on_top_toggled = Signal(bool)

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

        # Always on top (pin) — toggle window z-order above other apps
        self._btn_always_on_top = QToolButton(self)
        self._btn_always_on_top.setObjectName("TopBarAlwaysOnTopBtn")
        self._btn_always_on_top.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_always_on_top.setCheckable(True)
        self._btn_always_on_top.setChecked(False)
        self._btn_always_on_top.setIcon(lucide_icon("pin", size=20, color_hex=_win_icon_color))
        self._btn_always_on_top.setFixedSize(_action_icon_w, _action_icon_h)
        self._btn_always_on_top.setToolTip("Always on top: off — pin window above other apps")
        self._btn_always_on_top.toggled.connect(self._on_always_on_top_toggled)

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
        self._panel_l = QHBoxLayout(self._panel_group)
        m = _PANEL_GROUP_MARGIN
        self._panel_l.setContentsMargins(m, m, m, m)
        self._panel_l.setSpacing(0)
        self._btn_layout_auto = QToolButton(self._panel_group)
        self._btn_layout_auto.setObjectName("TopBarPanelAutoBtn")
        self._btn_layout_auto.setText("Auto")
        self._btn_layout_auto.setToolButtonStyle(Qt.ToolButtonTextOnly)
        # Keep geometry stable across Auto/manual to avoid "jumping" layout.
        self._btn_layout_auto.setFixedSize(_PANEL_AUTO_W, _PANEL_AUTO_H)
        self._btn_layout_auto.setToolTip("Auto layout — hide sidebar and Inspector when the window is narrow")
        self._btn_layout_auto.clicked.connect(self._on_layout_auto_clicked)
        self._btn_layout_sidebar = _PanelLayoutGlyphButton("sidebar", self._panel_group)
        self._btn_layout_sidebar.setToolTip("Full sidebar or compact rail (56px)")
        self._btn_layout_sidebar.clicked.connect(self.layout_sidebar_clicked.emit)
        self._btn_layout_inspector = _PanelLayoutGlyphButton("inspector", self._panel_group)
        self._btn_layout_inspector.setToolTip("Show or hide Inspector")
        self._btn_layout_inspector.clicked.connect(self.layout_inspector_clicked.emit)
        self._panel_l.addWidget(self._btn_layout_auto, 0, Qt.AlignVCenter)
        self._panel_l.addWidget(self._btn_layout_sidebar, 0, Qt.AlignVCenter)
        self._panel_l.addWidget(self._btn_layout_inspector, 0, Qt.AlignVCenter)

        # Keep visuals in sync with current layout mode (sizes stay fixed).
        self._panel_compact = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 10, 8, 10)
        layout.setSpacing(0)
        layout.addStretch(1)
        layout.addWidget(self._panel_group, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addSpacing(10)
        self._action_strip = QWidget(self)
        self._action_strip.setObjectName("TopBarActionStrip")
        action_l = QHBoxLayout(self._action_strip)
        action_l.setContentsMargins(0, 0, 0, 0)
        action_l.setSpacing(0)
        action_l.addWidget(self._btn_update, 0, Qt.AlignVCenter)
        action_l.addWidget(self._btn_watcher, 0, Qt.AlignVCenter)
        action_l.addWidget(self._btn_always_on_top, 0, Qt.AlignVCenter)
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
        # Auto on: still allow Sidebar/Inspector clicks (exits Auto → manual) — only visuals are muted.
        self._set_panel_group_compact(auto)

        for btn in (self._btn_layout_sidebar, self._btn_layout_inspector):
            btn.setProperty("autoMuted", auto)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

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

    def _set_panel_group_compact(self, compact: bool) -> None:
        """Auto mode = muted visuals; manual = default visuals (keep sizes fixed)."""
        if self._panel_compact != compact:
            self._panel_compact = compact
            self._panel_group.setProperty("autoMode", "true" if compact else "false")
            try:
                st = self._panel_group.style()
                if st:
                    st.unpolish(self._panel_group)
                    st.polish(self._panel_group)
            except Exception:
                pass
        self._panel_group.update()

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

    def _on_always_on_top_toggled(self, checked: bool) -> None:
        self._update_always_on_top_appearance(checked)
        self.always_on_top_toggled.emit(checked)

    def _update_always_on_top_appearance(self, on: bool) -> None:
        color = "#60a5fa" if on else "#d4d4d8"
        self._btn_always_on_top.setIcon(lucide_icon("pin", size=20, color_hex=color))
        self._btn_always_on_top.setToolTip(
            "Always on top: on — window stays above other apps"
            if on
            else "Always on top: off — pin window above other apps"
        )

    def set_always_on_top(self, on: bool) -> None:
        """Sync pin button from MainWindow (e.g. restore settings); does not emit always_on_top_toggled."""
        self._btn_always_on_top.blockSignals(True)
        self._btn_always_on_top.setChecked(on)
        self._btn_always_on_top.blockSignals(False)
        self._update_always_on_top_appearance(on)

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
