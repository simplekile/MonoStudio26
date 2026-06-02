"""@mention autocomplete popup for note compose."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QVBoxLayout

from monostudio.core.user_identity import StudioUser
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


class NoteMentionPopup(QFrame):
    user_selected = Signal(object)  # StudioUser

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("NoteMentionPopup")
        self.setStyleSheet(
            """
            NoteMentionPopup {
                background-color: #1c1c1f;
                border: 1px solid #3f3f46;
                border-radius: 8px;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        self._list = QListWidget(self)
        self._list.setObjectName("NoteMentionList")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item)
        layout.addWidget(self._list)
        self._users: list[StudioUser] = []
        self.setFixedWidth(220)
        self.setMaximumHeight(200)

    def set_users(self, users: list[StudioUser]) -> None:
        self._users = [u for u in users if u.active]

    def show_filtered(self, global_pos, *, query: str) -> None:
        q = (query or "").strip().lower()
        self._list.clear()
        matched = 0
        for u in self._users:
            name = (u.name or "").lower()
            if q and q not in name and q not in u.id.lower():
                continue
            item = QListWidgetItem(u.name, self._list)
            item.setData(Qt.ItemDataRole.UserRole, u)
            item.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
            matched += 1
            if matched >= 8:
                break
        if matched == 0:
            self.hide()
            return
        self.move(global_pos)
        self.show()
        self.raise_()
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_item(self, item: QListWidgetItem) -> None:
        user = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(user, StudioUser):
            self.user_selected.emit(user)
        self.hide()
