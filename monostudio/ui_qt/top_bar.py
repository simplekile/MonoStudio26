from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QToolButton,
    QWidget,
)

from monostudio.core.workspace_reader import DiscoveredProject
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosMenu


class TopBar(QWidget):
    project_switch_requested = Signal(str)  # project root path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        # Ensure QSS background is painted for this container.
        self.setAttribute(Qt.WA_StyledBackground, True)

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
        self._project_switch.setLayoutDirection(Qt.RightToLeft)  # icon on the right
        self._project_switch.setIcon(lucide_icon("chevron-down", size=12, color_hex="#a1a1aa"))
        self._project_switch.setText("SELECT PROJECT")
        self._project_switch.setPopupMode(QToolButton.InstantPopup)
        self._project_switch.clicked.connect(self._show_project_menu_left_aligned)
        try:
            bf = self._project_switch.font()
            bf.setLetterSpacing(QFont.AbsoluteSpacing, 0.2)  # px (tight but readable)
            self._project_switch.setFont(bf)
        except Exception:
            pass

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 10, 24, 10)
        layout.setSpacing(12)
        layout.addWidget(self._project_switch, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addStretch(1)

    def _show_project_menu_left_aligned(self) -> None:
        """Show project menu with left edge aligned to button's left edge."""
        btn = self._project_switch
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._project_menu.popup(pos)

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
        # Paint a crisp status dot (HiDPI aware).
        # (Qt menu doesn't support delegates; we attach an icon to each QAction.)
        try:
            dpr = float(QApplication.primaryScreen().devicePixelRatio())  # type: ignore[name-defined]
        except Exception:
            dpr = 1.0
        px = max(8, int(round(diameter + 2)))
        pm = QPixmap(int(px * dpr), int(px * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color_hex))
        r = pm.rect()
        center = r.center()
        rad = int(round((diameter / 2.0) * dpr))
        p.drawEllipse(center, rad, rad)
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
            self._set_project_switch_state("empty")
        else:
            self._project_switch.setText((current_root.name or "").upper())
            self._set_project_switch_state("active")

        group = QActionGroup(self._project_menu)
        group.setExclusive(True)
        for proj in projects:
            label = proj.root.name
            status = None
            if status_by_root is not None:
                status = status_by_root.get(str(proj.root))
            dot = self._dot_icon(self._status_color_hex(status))
            act = QAction(label, self._project_menu, checkable=True)
            act.setIcon(dot)
            act.setChecked(current == str(proj.root))
            act.triggered.connect(lambda checked=False, p=str(proj.root): self.project_switch_requested.emit(p))
            group.addAction(act)
            self._project_menu.addAction(act)

