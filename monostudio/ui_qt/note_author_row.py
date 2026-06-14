"""Author avatar + name row for note cards and viewers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from monostudio.core.item_comments import ItemCommentEntry, NoteAuthorVisual, entry_author_visual
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio
from monostudio.ui_qt.style import monos_font


class _AuthorNameLabel(QLabel):
    """Clickable author name (opens studio profile when user_id is set)."""

    def __init__(
        self,
        text: str,
        *,
        on_click: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(text, parent)
        self._on_click = on_click
        if on_click is not None:
            self.setObjectName("NoteAuthorNameLink")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("View profile")
        else:
            self.setObjectName("ItemNotesMeta")
        self.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        self.setWordWrap(False)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._on_click is not None and event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


class NoteAuthorRow(QWidget):
    """Circular avatar with author name and optional time suffix."""

    def __init__(
        self,
        visual: NoteAuthorVisual,
        *,
        avatar_size: int = 24,
        time_text: str = "",
        name_only: bool = False,
        avatar_only: bool = False,
        on_author_click: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NoteAuthorRow")
        self._on_author_click = on_author_click
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed if avatar_only else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._avatar = QLabel(self)
        self._avatar.setObjectName("NoteAuthorAvatar")
        self._avatar.setFixedSize(avatar_size, avatar_size)
        if on_author_click is not None:
            self._avatar.setCursor(Qt.CursorShape.PointingHandCursor)
            self._avatar.setToolTip("View profile")
        dpr = effective_device_pixel_ratio(self)
        self._avatar.setPixmap(
            avatar_pixmap_for(
                visual.image_path,
                visual.initials,
                visual.color_hex,
                avatar_size,
                dpr=dpr,
            )
        )
        layout.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        if avatar_only:
            return

        name_col = QHBoxLayout()
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(6)

        name_col.addWidget(
            _AuthorNameLabel(
                visual.name,
                on_click=on_author_click,
                parent=self,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        if not name_only and time_text:
            time_l = QLabel(f"· {time_text}", self)
            time_l.setObjectName("ItemNotesMetaTime")
            time_l.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
            name_col.addWidget(time_l, 0, Qt.AlignmentFlag.AlignVCenter)

        name_col.addStretch(1)
        layout.addLayout(name_col, 1)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            self._on_author_click is not None
            and event.button() == Qt.MouseButton.LeftButton
            and self._avatar.geometry().contains(event.pos())
        ):
            self._on_author_click()
            event.accept()
            return
        super().mousePressEvent(event)

    @classmethod
    def for_entry(
        cls,
        entry: ItemCommentEntry,
        workspace_root: Path | None,
        *,
        avatar_size: int = 24,
        time_text: str = "",
        name_only: bool = False,
        avatar_only: bool = False,
        on_author_click: Callable[[], None] | None = None,
        parent=None,
    ) -> NoteAuthorRow:
        return cls(
            entry_author_visual(entry, workspace_root),
            avatar_size=avatar_size,
            time_text=time_text,
            name_only=name_only,
            avatar_only=avatar_only,
            on_author_click=on_author_click,
            parent=parent,
        )

    @classmethod
    def for_visual(
        cls,
        visual: NoteAuthorVisual,
        *,
        avatar_size: int = 24,
        time_text: str = "",
        name_only: bool = False,
        avatar_only: bool = False,
        on_author_click: Callable[[], None] | None = None,
        parent=None,
    ) -> NoteAuthorRow:
        return cls(
            visual,
            avatar_size=avatar_size,
            time_text=time_text,
            name_only=name_only,
            avatar_only=avatar_only,
            on_author_click=on_author_click,
            parent=parent,
        )
