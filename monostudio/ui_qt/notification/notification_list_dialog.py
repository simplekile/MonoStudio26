"""
Notification list dialog: shows up to 200 most recent user alerts (newest first).
Opened from topbar noti dropdown "Show all".
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from monostudio.ui_qt.notification.notification_row_widget import NotificationAlertRow
from monostudio.ui_qt.notification.store import NotificationEntry, all_entries
from monostudio.ui_qt.style import MonosDialog, monos_font


class NotificationListDialog(MonosDialog):
    """Shows full user notification history (newest first, up to store max)."""

    user_alert_clicked = Signal(object)

    def __init__(
        self,
        parent=None,
        *,
        workspace_root: Path | None = None,
        project_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NotificationListDialog")
        self._workspace_root = workspace_root
        self._project_root = project_root
        self.setWindowTitle("Notifications")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Notifications", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 14, QFont.Weight.DemiBold))
        root.addWidget(title, 0)

        self._list = QListWidget(self)
        self._list.setObjectName("NotificationList")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setSelectionMode(QAbstractItemView.NoSelection)
        self._list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list.setAlternatingRowColors(False)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSpacing(2)
        self._list.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        list_bg = QColor("#18181b")
        list_pal = self._list.palette()
        list_pal.setColor(QPalette.ColorRole.Base, list_bg)
        list_pal.setColor(QPalette.ColorRole.Window, list_bg)
        self._list.setPalette(list_pal)
        self._list.setAutoFillBackground(False)
        root.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogSecondaryButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn, 0)
        root.addLayout(btn_row, 0)

        self.setMinimumSize(440, 400)
        self.resize(480, 520)
        self._load()

    def set_context(
        self,
        workspace_root: Path | None,
        project_root: Path | None,
    ) -> None:
        self._workspace_root = workspace_root
        self._project_root = project_root

    def _load(self) -> None:
        self._list.clear()
        for entry in all_entries():
            item = QListWidgetItem(self._list)
            row = NotificationAlertRow(
                entry,
                self._list,
                workspace_root=self._workspace_root,
                project_root=self._project_root,
            )
            row.clicked.connect(self.user_alert_clicked.emit)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
