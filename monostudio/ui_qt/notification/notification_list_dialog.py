"""
Notification list dialog: shows up to 200 most recent general notifications (newest first).
Opened from topbar noti dropdown "Show all".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.notification.store import all_entries
from monostudio.ui_qt.notification.toast import TOAST_COLORS, TOAST_ICONS, ToastType
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, MONOS_COLORS, monos_font


def _format_time(dt) -> str:
    try:
        return dt.strftime("%H:%M") if hasattr(dt, "strftime") else str(dt)
    except Exception:
        return "—"


class NotificationListDialog(MonosDialog):
    """Shows full notification history (newest first, up to store max)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Notifications")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Notifications", self)
        title.setObjectName("InboxMappingHeader")
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        root.addWidget(title, 0)

        self._list = QListWidget(self)
        self._list.setObjectName("NotificationList")
        self._list.setSelectionMode(QAbstractItemView.NoSelection)
        self._list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list.setAlternatingRowColors(False)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn, 0)
        root.addLayout(btn_row, 0)

        self.setMinimumSize(420, 360)
        self.resize(480, 440)
        self._load()

    def _load(self) -> None:
        self._list.clear()
        entries = all_entries()
        for entry in entries:
            item = QListWidgetItem(self._list)
            row = _NotificationRowWidget(entry, self._list)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)


class _NotificationRowWidget(QWidget):
    """Single row: type icon + message + time."""

    def __init__(self, entry, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        t = entry.toast_type
        pair = TOAST_COLORS.get(t, (MONOS_COLORS["blue_600"], MONOS_COLORS["text_label"]))
        accent_hex = pair[0]
        icon_name = TOAST_ICONS.get(t, "message-circle")
        icon = lucide_icon(icon_name, size=16, color_hex=accent_hex)
        if not icon.isNull():
            icon_label = QLabel(self)
            icon_label.setPixmap(icon.pixmap(16, 16))
            icon_label.setScaledContents(False)
            layout.addWidget(icon_label, 0)
        msg_label = QLabel(entry.message, self)
        msg_label.setFont(monos_font(point_size=13, weight=QFont.Weight.Medium))
        msg_label.setStyleSheet(f"color: {MONOS_COLORS['text_primary']}; background: transparent; border: none;")
        msg_label.setWordWrap(True)
        msg_label.setMaximumWidth(360)
        layout.addWidget(msg_label, 1)
        time_label = QLabel(_format_time(entry.at), self)
        time_label.setFont(monos_font(point_size=11, weight=QFont.Weight.Normal))
        time_label.setStyleSheet(f"color: {MONOS_COLORS['text_meta']}; background: transparent; border: none;")
        layout.addWidget(time_label, 0)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
