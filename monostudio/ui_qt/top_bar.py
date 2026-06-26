from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.toolbar_separators import add_widgets_with_icon_separators
from monostudio.ui_qt.notification.notification_dropdown import NotificationDropdown
from monostudio.ui_qt.notification.notification_list_dialog import NotificationListDialog
from monostudio.ui_qt.popup_position import position_popup_near_anchor
from monostudio.ui_qt.style import MonosMenu, monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap, effective_device_pixel_ratio

# TopBar action strip icon buttons (update, watcher, user avatar, …).
_TOPBAR_ACTION_BTN_W = 32
_TOPBAR_ACTION_BTN_H = 36


class _UserAvatarButton(QToolButton):
    """Top-bar user control: circular avatar painted edge-to-edge (no QToolButton icon padding)."""

    _INSET = 1  # px breathing room inside the hit target

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBarUserBtn")
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._avatar_pix: QPixmap | None = None
        self._fallback_icon: QIcon | None = None

    def set_avatar_pixmap(self, pix: QPixmap | None) -> None:
        self._avatar_pix = pix
        self._fallback_icon = None
        self.update()

    def set_fallback_icon(self, icon: QIcon) -> None:
        self._fallback_icon = icon
        self._avatar_pix = None
        self.update()

    def _avatar_diameter(self) -> float:
        return float(min(self.width(), self.height()) - 2 * self._INSET)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        d = self._avatar_diameter()
        x = (self.width() - d) / 2.0
        y = (self.height() - d) / 2.0
        target = QRectF(x, y, d, d)
        if self.underMouse() and self.isEnabled():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 20))
            p.drawEllipse(target)
        if self._avatar_pix is not None and not self._avatar_pix.isNull():
            clip = QPainterPath()
            clip.addEllipse(target)
            p.setClipPath(clip)
            # Draw HiDPI pixmap as-is — do not .scaled() here (destroys DPR → blur).
            pix = self._avatar_pix
            p.drawPixmap(
                target,
                pix,
                QRectF(0, 0, float(pix.width()), float(pix.height())),
            )
        elif self._fallback_icon is not None and not self._fallback_icon.isNull():
            ico_size = 20
            px = self._fallback_icon.pixmap(ico_size, ico_size)
            if not px.isNull():
                p.drawPixmap(
                    int((self.width() - ico_size) / 2),
                    int((self.height() - ico_size) / 2),
                    px,
                )
        p.end()


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


class _NotiCountBadge(QWidget):
    """Unread count badge on the notification bell (compact pill, auto-width)."""

    _MIN = 12
    _PAD_X = 5
    _FONT_SIZE = 8
    _BG = "#ef4444"

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._font = monos_font("JetBrains Mono", self._FONT_SIZE, QFont.Weight.Bold)
        self.hide()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        # Keep the pill inside the bell button so Qt does not clip trailing digits.
        x = max(0, parent.width() - self.width())
        self.move(x, 1)

    def set_count(self, n: int) -> None:
        if n <= 0:
            self._text = ""
            self.hide()
            return
        label = str(n) if n < 100 else "99+"
        self._text = label
        fm = QFontMetrics(self._font)
        h = self._MIN
        w = max(h, fm.horizontalAdvance(label) + self._PAD_X)
        self.setFixedSize(w, h)
        self._reposition()
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        if not self._text:
            return
        radius = self.height() // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._BG))
        p.drawRoundedRect(self.rect(), radius, radius)
        p.setFont(self._font)
        p.setPen(QColor("#fafafa"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


class TopBar(QWidget):
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    title_double_clicked = Signal()
    update_button_clicked = Signal()
    watcher_toggled = Signal(bool)  # True = watcher on, False = watcher off
    layout_auto_clicked = Signal()
    layout_sidebar_clicked = Signal()
    layout_inspector_clicked = Signal()
    review_player_clicked = Signal()
    always_on_top_toggled = Signal(bool)
    switch_user_requested = Signal()
    edit_profile_requested = Signal()
    clear_identity_requested = Signal()
    forget_device_requested = Signal()
    manage_team_requested = Signal()
    user_alert_clicked = Signal(object)  # NotificationEntry

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
        _action_icon_w, _action_icon_h = _TOPBAR_ACTION_BTN_W, _TOPBAR_ACTION_BTN_H
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
        self._btn_watcher.setToolTip("File watcher: on — pause (click) before rename or Move to Trash")
        self._btn_watcher.toggled.connect(self._on_watcher_toggled)
        self._watcher_busy = False
        self._watcher_blink_on = True
        self._watcher_busy_timer = QTimer(self)
        self._watcher_busy_timer.setInterval(400)
        self._watcher_busy_timer.timeout.connect(self._on_watcher_busy_blink)

        self._btn_review = QToolButton(self)
        self._btn_review.setObjectName("TopBarReviewBtn")
        self._btn_review.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_review.setIcon(lucide_icon("clapperboard", size=20, color_hex=_win_icon_color))
        self._btn_review.setFixedSize(_action_icon_w, _action_icon_h)
        self._btn_review.setToolTip("Review player")
        self._btn_review.clicked.connect(self.review_player_clicked.emit)

        # Current-user avatar + identity menu (Switch user / Clear identity / Forget device)
        self._user_name: str | None = None
        self._can_manage_team = False
        self._btn_user = _UserAvatarButton(self)
        self._btn_user.setFixedSize(_action_icon_w, _action_icon_h)
        self._btn_user.clicked.connect(self._show_user_menu)
        self.set_identity(None)

        # Notification button (right side, before window buttons)
        self._btn_noti = QToolButton(self)
        self._btn_noti.setObjectName("TopBarNotiBtn")
        self._btn_noti.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_noti.setIcon(lucide_icon("bell", size=20, color_hex=_win_icon_color))
        self._btn_noti.setFixedSize(_action_icon_w, _action_icon_h)
        self._btn_noti.setToolTip("Notifications")
        self._noti_dropdown_closed_at = 0.0  # monotonic time when dropdown last closed (avoid reopen on same click)
        self._notification_workspace_root: Path | None = None
        self._notification_project_root: Path | None = None
        self._notification_user_id: str = ""
        self._noti_dropdown = NotificationDropdown(self)
        self._noti_dropdown.show_all_requested.connect(self._open_notification_list_dialog)
        self._noti_dropdown.user_alert_clicked.connect(self._on_user_alert_clicked)
        self._noti_dropdown.closed.connect(self._on_noti_dropdown_closed)
        self._btn_noti.clicked.connect(self._show_noti_dropdown)
        self._noti_badge = _NotiCountBadge(self._btn_noti)
        self._notification_list_dialog: NotificationListDialog | None = None

        self._ctx_act_auto = QAction("Auto layout", self)
        self._ctx_act_auto.setCheckable(True)
        self._ctx_act_auto.setToolTip("Hide sidebar and Inspector when the window is narrow")
        self._ctx_act_auto.triggered.connect(self._on_ctx_layout_auto)
        self._ctx_act_sidebar = QAction("Full sidebar", self)
        self._ctx_act_sidebar.setCheckable(True)
        self._ctx_act_sidebar.setToolTip("Full sidebar or compact rail (68px)")
        self._ctx_act_sidebar.triggered.connect(self.layout_sidebar_clicked.emit)
        self._ctx_act_inspector = QAction("Inspector", self)
        self._ctx_act_inspector.setCheckable(True)
        self._ctx_act_inspector.setToolTip("Show or hide Inspector")
        self._ctx_act_inspector.triggered.connect(self.layout_inspector_clicked.emit)
        self._ctx_act_always_on_top = QAction("Always on top", self)
        self._ctx_act_always_on_top.setCheckable(True)
        self._ctx_act_always_on_top.toggled.connect(self._on_ctx_always_on_top_toggled)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_layout_context_menu)

        self._project_display_name_raw: str | None = None
        self._project_name_label = QLabel("SELECT PROJECT", self)
        self._project_name_label.setObjectName("TopBarProjectNameLabel")
        self._project_name_label.setWordWrap(False)
        self._project_name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._project_name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._project_name_label.setMinimumWidth(0)
        try:
            pf = self._project_name_label.font()
            pf.setPointSize(14)
            pf.setWeight(QFont.Weight.Bold)
            self._project_name_label.setFont(pf)
        except Exception:
            pass

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 10, 8, 10)
        layout.setSpacing(0)
        layout.addWidget(self._project_name_label, 1, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addSpacing(10)
        self._action_strip = QWidget(self)
        self._action_strip.setObjectName("TopBarActionStrip")
        action_l = QHBoxLayout(self._action_strip)
        action_l.setContentsMargins(0, 0, 0, 0)
        action_l.setSpacing(0)
        add_widgets_with_icon_separators(
            action_l,
            [
                self._btn_review,
                self._btn_update,
                self._btn_watcher,
                self._btn_noti,
                self._btn_user,
            ],
            self._action_strip,
            sep_height=20,
        )
        layout.addWidget(self._action_strip, 0, Qt.AlignRight | Qt.AlignVCenter)
        # Keep window chrome (min / max / close) at original spacing — gap before them only
        layout.addSpacing(10)
        layout.addWidget(self._btn_min, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_max, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_close, 0, Qt.AlignRight | Qt.AlignVCenter)

    def set_project_display_name(self, name: str | None) -> None:
        if not name:
            self._project_display_name_raw = None
            self._project_name_label.setText("SELECT PROJECT")
            self._project_name_label.setToolTip("")
        else:
            self._project_display_name_raw = name
            self._sync_project_name_label_text()

    def _sync_project_name_label_text(self) -> None:
        raw = self._project_display_name_raw
        if not raw:
            return
        display = raw.upper()
        label = self._project_name_label
        available = max(0, label.width())
        if available > 0:
            fm = QFontMetrics(label.font())
            if fm.horizontalAdvance(display) > available:
                label.setText(fm.elidedText(display, Qt.TextElideMode.ElideRight, available))
                label.setToolTip(display)
                return
        label.setText(display)
        label.setToolTip("")

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._project_display_name_raw:
            self._sync_project_name_label_text()

    def set_notification_context(
        self,
        workspace_root: Path | None,
        project_root: Path | None,
        *,
        user_id: str = "",
    ) -> None:
        """Workspace/project/user scope for bell history and mention timestamps."""
        self._notification_workspace_root = workspace_root
        self._notification_project_root = project_root
        self._notification_user_id = (user_id or "").strip()
        self._noti_dropdown.set_context(
            workspace_root,
            project_root,
            user_id=self._notification_user_id,
        )
        if self._notification_list_dialog is not None:
            self._notification_list_dialog.set_context(
                workspace_root,
                project_root,
                user_id=self._notification_user_id,
            )

    def set_identity(
        self,
        name: str | None,
        color_hex: str = "#3f3f46",
        initials: str = "?",
        pixmap: QPixmap | None = None,
    ) -> None:
        """Update the avatar button from the current resolved user (None = unknown)."""
        self._user_name = (name or "").strip() or None
        if self._user_name:
            # Match painted diameter; generate at real screen DPR (no downscale in paint).
            side = max(20, _TOPBAR_ACTION_BTN_W - 2 * _UserAvatarButton._INSET)
            dpr = effective_device_pixel_ratio(self)
            if pixmap is not None:
                icon_pix = pixmap
            else:
                icon_pix = avatar_pixmap(initials, color_hex, side, dpr=dpr)
            self._btn_user.set_avatar_pixmap(icon_pix)
            self._btn_user.setToolTip(f"{self._user_name} — switch / clear identity")
        else:
            self._btn_user.set_fallback_icon(lucide_icon("user", size=20, color_hex="#a1a1aa"))
            self._btn_user.setToolTip("No identity — click to sign in")

    def set_can_manage_team(self, can: bool) -> None:
        self._can_manage_team = bool(can)

    def _show_user_menu(self) -> None:
        menu = QMenu(self)
        if self._user_name:
            header = menu.addAction(f"Signed in as {self._user_name}")
            header.setEnabled(False)
            menu.addSeparator()
            menu.addAction("My profile…", self.edit_profile_requested.emit)
        menu.addAction("Switch user…", self.switch_user_requested.emit)
        clear = menu.addAction("Clear identity (log out)", self.clear_identity_requested.emit)
        clear.setEnabled(self._user_name is not None)
        menu.addSeparator()
        manage = menu.addAction("Manage team…", self.manage_team_requested.emit)
        if not self._can_manage_team:
            manage.setToolTip(
                "Requires admin/developer unlock — Settings → General → Access"
            )
        menu.addSeparator()
        forget = menu.addAction("Forget this device", self.forget_device_requested.emit)
        forget.setProperty("class", "danger-action")
        btn = self._btn_user
        menu.exec(btn.mapToGlobal(btn.rect().bottomRight()))
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(btn))

    def _on_ctx_layout_auto(self) -> None:
        """Always re-enter auto layout (stays active until user uses sidebar/inspector toggles)."""
        self.layout_auto_clicked.emit()

    def _show_layout_context_menu(self, pos: QPoint) -> None:
        if self._is_on_window_buttons(pos):
            return
        menu = MonosMenu(self)
        menu.addAction(self._ctx_act_auto)
        menu.addSeparator()
        menu.addAction(self._ctx_act_sidebar)
        menu.addAction(self._ctx_act_inspector)
        menu.addSeparator()
        menu.addAction(self._ctx_act_always_on_top)
        menu.exec(self.mapToGlobal(pos))

    def set_panel_layout_controls(self, *, auto: bool, sidebar_on: bool, inspector_on: bool) -> None:
        """Sync layout context-menu check states from MainWindow."""
        for act, val in (
            (self._ctx_act_auto, auto),
            (self._ctx_act_sidebar, sidebar_on),
            (self._ctx_act_inspector, inspector_on),
        ):
            act.blockSignals(True)
            act.setChecked(val)
            act.blockSignals(False)

    def _on_ctx_always_on_top_toggled(self, checked: bool) -> None:
        self.always_on_top_toggled.emit(checked)

    def set_always_on_top(self, on: bool) -> None:
        """Sync always-on-top from MainWindow; does not emit always_on_top_toggled."""
        self._ctx_act_always_on_top.blockSignals(True)
        self._ctx_act_always_on_top.setChecked(on)
        self._ctx_act_always_on_top.blockSignals(False)

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

    def clear_transient_hover_states(self) -> None:
        """Clear stuck top-bar button hover when pointer moves to main content."""
        from monostudio.ui_qt.style import clear_stuck_widget_hover

        for btn in self.findChildren(QToolButton):
            self._clear_tool_button_hover(btn)
        for btn in self.findChildren(QPushButton):
            clear_stuck_widget_hover(btn)

    def _position_noti_dropdown(self) -> None:
        win = self.window()
        bounds = win.frameGeometry() if win is not None else None
        position_popup_near_anchor(self._noti_dropdown, self._btn_noti, bounds=bounds)

    def open_noti_dropdown(self) -> None:
        """Open the bell dropdown (e.g. from a Windows toast); does not toggle closed."""
        if self._noti_dropdown.isVisible():
            return
        self._position_noti_dropdown()
        self._noti_dropdown.show()

    def _show_noti_dropdown(self) -> None:
        """Toggle notification dropdown: if open, close; else if just closed (same click), do nothing; else show."""
        if self._noti_dropdown.isVisible():
            self._noti_dropdown.close()
            return
        if (time.monotonic() - self._noti_dropdown_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        self._position_noti_dropdown()
        self._noti_dropdown.show()

    def _on_noti_dropdown_closed(self) -> None:
        """Record close time and clear tool button hover/pressed state (deferred so it takes effect)."""
        self._noti_dropdown_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._btn_noti))

    def _is_on_window_buttons(self, pos: QPoint) -> bool:
        return (
            self._action_strip.geometry().contains(pos)
            or self._btn_min.geometry().contains(pos)
            or self._btn_max.geometry().contains(pos)
            or self._btn_close.geometry().contains(pos)
        )

    def _on_watcher_toggled(self, checked: bool) -> None:
        self.watcher_toggled.emit(checked)
        self._update_watcher_button_appearance(checked)

    def _update_watcher_button_appearance(self, enabled: bool) -> None:
        if self._watcher_busy:
            return
        # On = green (safe to browse); Off = red (required for rename / Move to Trash)
        color = "#22c55e" if enabled else "#ef4444"
        self._btn_watcher.setIcon(
            lucide_icon("eye" if enabled else "eye-off", size=20, color_hex=color)
        )
        self._btn_watcher.setToolTip(
            "File watcher: on — pause (click) before rename or Move to Trash" if enabled
            else "File watcher: paused — rename and Move to Trash allowed (click to resume)"
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

    def set_review_player_hint(self, path: Path | None) -> None:
        if path is not None:
            name = path.name.strip() or str(path)
            self._btn_review.setToolTip(f"Review player — {name}")
        else:
            self._btn_review.setToolTip("Review player — open most recent clip")

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
        if self._notification_list_dialog is None:
            self._notification_list_dialog = NotificationListDialog(
                win,
                workspace_root=self._notification_workspace_root,
                project_root=self._notification_project_root,
            )
            self._notification_list_dialog.user_alert_clicked.connect(self._on_user_alert_clicked)
        else:
            self._notification_list_dialog.set_context(
                self._notification_workspace_root,
                self._notification_project_root,
                user_id=self._notification_user_id,
            )
            self._notification_list_dialog._load()
        self._notification_list_dialog.show()

    def _on_user_alert_clicked(self, entry: object) -> None:
        self._noti_dropdown.close()
        self.user_alert_clicked.emit(entry)

    def set_noti_unread_count(self, count: int) -> None:
        self._noti_badge.set_count(int(count))

    def set_maximized(self, maximized: bool) -> None:
        """Update window button icon (Max vs Restore)."""
        _c = "#d4d4d8"
        if maximized:
            self._btn_max.setIcon(lucide_icon("maximize-2", size=24, color_hex=_c))
        else:
            self._btn_max.setIcon(lucide_icon("square", size=24, color_hex=_c))

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
