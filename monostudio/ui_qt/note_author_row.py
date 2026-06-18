"""Author avatar + name row for note cards and viewers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent
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
        elide: bool = True,
        parent=None,
    ) -> None:
        super().__init__(text, parent)
        self._on_click = on_click
        self._elide = elide
        if on_click is not None:
            self.setObjectName("NoteAuthorNameLink")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("View profile")
        else:
            self.setObjectName("ItemNotesMeta")
        self.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        self.setWordWrap(False)
        self._full_text = text
        if elide:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setMinimumWidth(0)
            self._apply_elide()
        else:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self.setText(text)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._elide:
            self._apply_elide()

    def _apply_elide(self) -> None:
        if not self._elide:
            return
        text = self._full_text or ""
        if not text:
            self.setText("")
            return
        fm = QFontMetrics(self.font())
        max_w = max(8, self.contentsRect().width())
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, max_w)
        self.setText(elided)
        tip = text if elided != text else ""
        if self._on_click is not None and tip:
            self.setToolTip(tip)
        elif self._on_click is None:
            self.setToolTip(tip)

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
        elide_name: bool = True,
        time_on_right: bool = False,
        on_author_click: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NoteAuthorRow")
        self._on_author_click = on_author_click
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if not avatar_only else QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.setMinimumWidth(0 if not avatar_only else avatar_size)

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
                elide=elide_name,
                parent=self,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        show_inline_time = bool(time_text) and not name_only and not time_on_right
        if show_inline_time:
            time_l = QLabel(f"· {time_text}", self)
            time_l.setObjectName("ItemNotesMetaTime")
            time_l.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
            name_col.addWidget(time_l, 0, Qt.AlignmentFlag.AlignVCenter)

        name_col.addStretch(1)
        layout.addLayout(name_col, 1)

        if time_text and not name_only and time_on_right:
            time_r = QLabel(time_text, self)
            time_r.setObjectName("ItemNotesMetaTime")
            time_r.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
            time_r.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(time_r, 0, Qt.AlignmentFlag.AlignVCenter)

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
        elide_name: bool = True,
        time_on_right: bool = False,
        on_author_click: Callable[[], None] | None = None,
        parent=None,
    ) -> NoteAuthorRow:
        return cls(
            entry_author_visual(entry, workspace_root),
            avatar_size=avatar_size,
            time_text=time_text,
            name_only=name_only,
            avatar_only=avatar_only,
            elide_name=elide_name,
            time_on_right=time_on_right,
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
        elide_name: bool = True,
        time_on_right: bool = False,
        on_author_click: Callable[[], None] | None = None,
        parent=None,
    ) -> NoteAuthorRow:
        return cls(
            visual,
            avatar_size=avatar_size,
            time_text=time_text,
            name_only=name_only,
            avatar_only=avatar_only,
            elide_name=elide_name,
            time_on_right=time_on_right,
            on_author_click=on_author_click,
            parent=parent,
        )
