"""@mention autocomplete popup for note compose."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.user_identity import StudioUser, avatar_path, read_roster
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio
from monostudio.ui_qt.style import monos_font

_ROW_H = 30
_AVATAR_PX = 22
_MAX_ROWS = 8


class _MentionRowWidget(QWidget):
    def __init__(
        self,
        user: StudioUser,
        *,
        workspace_root: Path | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 8, 4)
        layout.setSpacing(8)

        self._avatar = QLabel(self)
        self._avatar.setFixedSize(_AVATAR_PX, _AVATAR_PX)
        dpr = effective_device_pixel_ratio(self)
        self._avatar.setPixmap(
            avatar_pixmap_for(
                avatar_path(workspace_root, user) if workspace_root else None,
                user.initials,
                user.color_hex,
                _AVATAR_PX,
                dpr=dpr,
            )
        )
        layout.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        name = QLabel((user.name or user.id).strip(), self)
        name.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        name.setStyleSheet("color: #d4d4d8; background: transparent;")
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(name, 1, Qt.AlignmentFlag.AlignVCenter)


class NoteMentionPopup(QFrame):
    user_selected = Signal(object)  # StudioUser

    def __init__(self, parent=None, *, workspace_root: Path | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("NoteMentionPopup")
        self._workspace_root = Path(workspace_root) if workspace_root else None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._list = QListWidget(self)
        self._list.setObjectName("NoteMentionList")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setUniformItemSizes(True)
        self._list.setSpacing(0)
        self._list.setMouseTracking(True)
        self._list.itemClicked.connect(self._on_item)
        layout.addWidget(self._list)

        self._users: list[StudioUser] = []
        self.setFixedWidth(232)

    def set_users(self, users: list[StudioUser]) -> None:
        self._users = [u for u in users if u.active]

    def reload_users(self) -> None:
        self.set_users(read_roster(self._workspace_root))

    def show_filtered(self, global_pos, *, query: str) -> None:
        q = (query or "").strip().lower()
        self._list.clear()
        matched = 0
        for u in self._users:
            name = (u.name or "").lower()
            if q and q not in name and q not in u.id.lower():
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, u)
            item.setSizeHint(QSize(0, _ROW_H))
            self._list.addItem(item)
            self._list.setItemWidget(
                item,
                _MentionRowWidget(u, workspace_root=self._workspace_root, parent=self._list),
            )
            matched += 1
            if matched >= _MAX_ROWS:
                break
        if matched == 0:
            self.hide()
            return
        list_h = min(matched, _MAX_ROWS) * _ROW_H + 8
        self._list.setFixedHeight(list_h)
        self.adjustSize()
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
