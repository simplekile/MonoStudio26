from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QToolButton,
    QWidget,
)

from monostudio.core.workspace_reader import DiscoveredProject
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosMenu, project_accent_color


class TopBar(QWidget):
    project_switch_requested = Signal(str)  # project root path
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    title_double_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._drag_start_pos: QPoint | None = None

        self._project_menu = MonosMenu(self, rounded=False)
        self._project_menu.setObjectName("ProjectSwitchMenu")
        self._project_menu.setWindowOpacity(1.0)

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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 10, 8, 10)
        layout.setSpacing(0)
        layout.addWidget(self._project_switch, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addStretch(1)
        layout.addWidget(self._btn_min, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_max, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._btn_close, 0, Qt.AlignRight | Qt.AlignVCenter)

    def _show_project_menu_left_aligned(self) -> None:
        """Show project menu with left edge aligned to button's left edge."""
        btn = self._project_switch
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._project_menu.popup(pos)

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

