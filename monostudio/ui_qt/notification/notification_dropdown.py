"""
Dropdown popup for topbar notification button: shows up to 10 recent user alerts + "Show all".
"""

from __future__ import annotations

from pathlib import Path

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

from monostudio.core.notification_copy import pick_copy
from monostudio.core.notification_preferences import read_notification_vietnamese
from monostudio.ui_qt.notification.notification_row_widget import NotificationAlertRow
from monostudio.ui_qt.notification.store import recent, unread_count
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

RECENT_COUNT = 10
_ROW_MIN_HEIGHT = 76
_FOOTER_HEIGHT = 48
_DROPDOWN_CHROME_V = 16
_VIEWPORT_MAX_HEIGHT = RECENT_COUNT * _ROW_MIN_HEIGHT


class NotificationDropdown(QFrame):
    """Popup showing up to 10 recent user notifications and a 'Show all' button."""

    show_all_requested = Signal()
    user_alert_clicked = Signal(object)  # NotificationEntry
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
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("NotificationDropdownScroll")
        # False: do not squash all rows into the viewport height (caused footer overlap).
        self._scroll.setWidgetResizable(False)
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
        self._content_layout.setContentsMargins(6, 4, 6, 8)
        self._content_layout.setSpacing(2)
        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll, 0)

        footer = QFrame(self)
        footer.setObjectName("NotificationDropdownFooter")
        footer.setFixedHeight(_FOOTER_HEIGHT)
        footer.setStyleSheet(
            """
            QFrame#NotificationDropdownFooter {
                background-color: #1c1c1f;
                border: none;
                border-top: 1px solid #3f3f46;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }
            """
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        _footer_btn_qss = (
            "QPushButton { background: transparent; border: none; padding: 12px 16px; }"
            "QPushButton:disabled { color: #52525b; }"
        )
        self._mark_all_btn = QPushButton(
            pick_copy("Đã xem tất cả", "Mark all read", vietnamese=read_notification_vietnamese()),
            footer,
        )
        self._mark_all_btn.setObjectName("NotificationDropdownMarkAll")
        self._mark_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mark_all_btn.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        self._mark_all_btn.setStyleSheet(
            _footer_btn_qss
            + "QPushButton { color: #a1a1aa; }"
            + "QPushButton:hover:enabled { background: rgba(255, 255, 255, 0.06); color: #e4e4e7; }"
        )
        self._mark_all_btn.clicked.connect(self._on_mark_all_read)
        footer_layout.addWidget(self._mark_all_btn, 0)
        footer_layout.addStretch(1)
        show_all_btn = QPushButton("Show all", footer)
        show_all_btn.setObjectName("NotificationDropdownShowAll")
        show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_all_btn.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        show_all_btn.setStyleSheet(
            _footer_btn_qss
            + "QPushButton { color: #60a5fa; }"
            + "QPushButton:hover { background: rgba(96, 165, 250, 0.12); color: #93c5fd; }"
        )
        show_all_btn.clicked.connect(self._on_show_all)
        footer_layout.addWidget(show_all_btn, 0)
        layout.addWidget(footer, 0)

        self.setFixedWidth(380)
        self._workspace_root: Path | None = None
        self._project_root: Path | None = None
        self._current_user_id: str = ""
        self._fill()

    def set_context(
        self,
        workspace_root: Path | None,
        project_root: Path | None,
        *,
        user_id: str = "",
    ) -> None:
        self._workspace_root = workspace_root
        self._project_root = project_root
        self._current_user_id = (user_id or "").strip()

    def _update_mark_all_enabled(self) -> None:
        n = unread_count(
            user_id=self._current_user_id,
            project_root=self._project_root,
        )
        self._mark_all_btn.setEnabled(n > 0)

    def _fill(self) -> None:
        while self._content_layout.count():
            child = self._content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        entries = recent(
            RECENT_COUNT,
            user_id=self._current_user_id,
            project_root=self._project_root,
        )
        if not entries:
            empty = QLabel("No notifications", self._content)
            empty.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
            empty.setStyleSheet(
                f"color: {MONOS_COLORS['text_meta']}; background: transparent; padding: 12px;"
            )
            self._content_layout.addWidget(empty)
            self._adjust_popup_size(0)
        else:
            for entry in entries:
                row = NotificationAlertRow(
                    entry,
                    self._content,
                    workspace_root=self._workspace_root,
                    project_root=self._project_root,
                )
                row.setMinimumHeight(_ROW_MIN_HEIGHT)
                row.clicked.connect(self.user_alert_clicked.emit)
                self._content_layout.addWidget(row)
            self._adjust_popup_size(len(entries))
        self._update_mark_all_enabled()

    def _on_mark_all_read(self) -> None:
        from monostudio.ui_qt.notification.service import notify

        notify.mark_all_read(
            user_id=self._current_user_id,
            project_root=self._project_root,
        )
        self._fill()

    def _adjust_popup_size(self, entry_count: int) -> None:
        """Viewport scrolls when content exceeds max; footer stays below the list."""
        inner_w = max(360, self.width() - 12)
        if entry_count <= 0:
            content_h = 72
        else:
            self._content.adjustSize()
            hint = self._content_layout.sizeHint()
            content_h = max(hint.height(), entry_count * _ROW_MIN_HEIGHT)
        self._content.setFixedWidth(inner_w)
        self._content.setFixedHeight(content_h)
        viewport_h = min(content_h, _VIEWPORT_MAX_HEIGHT)
        self._scroll.setFixedHeight(viewport_h)
        total_h = viewport_h + _FOOTER_HEIGHT + _DROPDOWN_CHROME_V
        self.setFixedHeight(total_h)

    def _on_show_all(self) -> None:
        self.close()
        self.show_all_requested.emit()

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._fill()
        super().showEvent(event)
