from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QToolButton,
    QWidget,
)

from monostudio.core.workspace_reader import DiscoveredProject
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification.notification_dropdown import NotificationDropdown
from monostudio.ui_qt.notification.notification_list_dialog import NotificationListDialog
from monostudio.ui_qt.style import MonosMenu, project_accent_color


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
    project_switch_requested = Signal(str)  # project root path
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    title_double_clicked = Signal()
    update_button_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._drag_start_pos: QPoint | None = None

        self._project_menu = MonosMenu(self, rounded=False)
        self._project_menu.setObjectName("ProjectSwitchMenu")
        self._project_menu.setWindowOpacity(1.0)
        self._project_menu_closed_at = 0.0  # avoid reopen on same click + clear hover
        self._project_menu.aboutToHide.connect(self._on_project_menu_closed)

        shadow = QGraphicsDropShadowEffect(self._project_menu)
        shadow.setBlurRadius(15)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, int(255 * 0.40)))
        self._project_menu.setGraphicsEffect(shadow)

        self._project_switch = QToolButton(self)
        self._project_switch.setObjectName("ProjectSwitch")
        self._project_switch.setProperty("state", "empty")
        self._project_switch.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._project_switch.setLayoutDirection(Qt.RightToLeft)
        self._project_switch.setIcon(lucide_icon("chevron-down", size=12, color_hex="#a1a1aa"))
        self._project_switch.setText("SELECT PROJECT")
        self._project_switch.setPopupMode(QToolButton.InstantPopup)
        self._project_switch.clicked.connect(self._show_project_menu_left_aligned)
        try:
            bf = self._project_switch.font()
            bf.setLetterSpacing(QFont.AbsoluteSpacing, 0.2)
            self._project_switch.setFont(bf)
        except Exception:
            pass

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
        layout.addWidget(self._project_switch, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addStretch(1)
        layout.addWidget(self._btn_update, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_noti, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_min, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_max, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_close, 0, Qt.AlignRight | Qt.AlignVCenter)

    # Grace period (seconds): if popup was closed less than this ago, next button click is treated as "close" not "open"
    _POPUP_REOPEN_GRACE = 0.25

    def _show_project_menu_left_aligned(self) -> None:
        """Show project menu with left edge aligned to button's left edge."""
        if (time.monotonic() - self._project_menu_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        btn = self._project_switch
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._project_menu.popup(pos)

    def _on_project_menu_closed(self) -> None:
        """Record close time and clear project switch button hover/pressed (deferred)."""
        self._project_menu_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._project_switch))

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

    def _set_project_switch_state(self, state: str) -> None:
        # Apply a deterministic state property for QSS styling:
        # - active: current project selected
        # - empty: no current project selected
        # - disabled: no projects available
        self._project_switch.setProperty("state", state)
        try:
            st = self._project_switch.style()
            st.unpolish(self._project_switch)
            st.polish(self._project_switch)
        except Exception:
            pass
        self._project_switch.update()

    @staticmethod
    def _status_color_hex(status: str | None) -> str:
        s = (status or "").strip().upper()
        if s in ("READY", "PROGRESS", "ACTIVE"):
            return "#10b981"  # emerald
        if s in ("WAITING", "PAUSED"):
            return "#f59e0b"  # amber
        if s in ("BLOCKED", "ERROR", "CRITICAL"):
            return "#ef4444"  # red
        return "#71717a"  # zinc-500

    @staticmethod
    def _dot_icon(color_hex: str, *, diameter: int = 6) -> QIcon:
        try:
            dpr = float(QApplication.primaryScreen().devicePixelRatio())
        except Exception:
            dpr = 1.0
        # Canvas large enough so QMenu icon column never clips the dot.
        canvas = max(16, diameter + 8)
        dev_w = int(round(canvas * dpr))
        pm = QPixmap(dev_w, dev_w)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color_hex))
        cx = canvas / 2.0
        cy = canvas / 2.0
        r = diameter / 2.0
        p.drawEllipse(QRectF(cx - r, cy - r, diameter, diameter))
        p.end()
        return QIcon(pm)

    def set_projects(
        self,
        projects: list[DiscoveredProject],
        *,
        current_root: Path | None,
        status_by_root: dict[str, str] | None = None,
    ) -> None:
        self._project_menu.clear()

        current = str(current_root) if current_root else None

        if not projects:
            empty = QAction("No projects", self._project_menu)
            empty.setEnabled(False)
            self._project_menu.addAction(empty)
            self._project_switch.setEnabled(False)
            self._project_switch.setText("NO PROJECTS")
            self._set_project_switch_state("disabled")
            return

        self._project_switch.setEnabled(True)
        if current_root is None:
            self._project_switch.setText("SELECT PROJECT")
            self._project_switch.setIcon(lucide_icon("chevron-down", size=12, color_hex="#a1a1aa"))
            self._set_project_switch_state("empty")
        else:
            folder_name = current_root.name or ""
            self._project_switch.setText(folder_name.upper())
            accent = project_accent_color(folder_name)
            self._project_switch.setIcon(lucide_icon("chevron-down", size=12, color_hex=accent))
            self._set_project_switch_state("active")

        group = QActionGroup(self._project_menu)
        group.setExclusive(True)
        for proj in projects:
            label = proj.root.name
            accent = project_accent_color(label)
            is_current = current == str(proj.root)
            dot = self._dot_icon(accent, diameter=8 if is_current else 6)
            act = QAction(label, self._project_menu, checkable=True)
            act.setIcon(dot)
            act.setChecked(is_current)
            if is_current:
                f = act.font()
                f.setWeight(QFont.Weight.DemiBold)
                act.setFont(f)
            act.triggered.connect(lambda checked=False, p=str(proj.root): self.project_switch_requested.emit(p))
            group.addAction(act)
            self._project_menu.addAction(act)

