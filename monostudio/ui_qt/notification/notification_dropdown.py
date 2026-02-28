"""
Dropdown popup for topbar notification button: shows 5 most recent notifications + "Show all".
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.notification.store import recent
from monostudio.ui_qt.notification.toast import TOAST_COLORS, TOAST_ICONS
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

RECENT_COUNT = 5


def _format_time(dt) -> str:
    try:
        return dt.strftime("%H:%M") if hasattr(dt, "strftime") else str(dt)
    except Exception:
        return "—"


class NotificationDropdown(QFrame):
    """Popup showing 5 recent notifications and a 'Show all' button."""

    show_all_requested = Signal()
    # Emitted when the dropdown is hidden (click outside, Show all, or toggle); parent can clear button hover.
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NotificationDropdown")
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(
            """
            NotificationDropdown {
                background-color: #1c1c1f;
                border: 1px solid #3f3f46;
                border-radius: 12px;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("NotificationDropdownScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea::viewport { background: transparent; border: none; }"
        )
        self._content = QWidget(self._scroll)
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 4, 8, 4)
        self._content_layout.setSpacing(0)
        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll, 1)

        show_all_btn = QPushButton("Show all", self)
        show_all_btn.setObjectName("NotificationDropdownShowAll")
        show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_all_btn.setFont(monos_font(point_size=12, weight=QFont.Weight.Medium))
        show_all_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #60a5fa; border: none; padding: 10px 16px; }"
            "QPushButton:hover { background: rgba(96, 165, 250, 0.12); color: #93c5fd; }"
        )
        show_all_btn.clicked.connect(self._on_show_all)
        layout.addWidget(show_all_btn, 0)

        self.setFixedWidth(320)
        self.setMinimumHeight(120)
        self.setMaximumHeight(320)
        self._fill()

    def _fill(self) -> None:
        while self._content_layout.count():
            child = self._content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        entries = recent(RECENT_COUNT)
        if not entries:
            empty = QLabel("No notifications", self._content)
            empty.setFont(monos_font(point_size=13, weight=QFont.Weight.Medium))
            empty.setStyleSheet(f"color: {MONOS_COLORS['text_meta']}; background: transparent; padding: 12px;")
            self._content_layout.addWidget(empty)
        else:
            for entry in entries:
                row = _DropdownRow(entry, self._content)
                self._content_layout.addWidget(row)
        self._content_layout.addStretch(1)

    def _on_show_all(self) -> None:
        self.close()
        self.show_all_requested.emit()

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._fill()
        super().showEvent(event)


class _DropdownRow(QFrame):
    """One notification row in the dropdown."""

    def __init__(self, entry, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
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
        msg_label.setMaximumWidth(220)
        layout.addWidget(msg_label, 1)
        time_label = QLabel(_format_time(entry.at), self)
        time_label.setFont(monos_font(point_size=11, weight=QFont.Weight.Normal))
        time_label.setStyleSheet(f"color: {MONOS_COLORS['text_meta']}; background: transparent; border: none;")
        layout.addWidget(time_label, 0)
