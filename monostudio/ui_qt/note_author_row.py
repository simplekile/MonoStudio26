"""Author avatar + name row for note cards and viewers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from monostudio.core.item_comments import ItemCommentEntry, NoteAuthorVisual, entry_author_visual
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio
from monostudio.ui_qt.style import monos_font


class NoteAuthorRow(QWidget):
    """Circular avatar with author name and optional time suffix."""

    def __init__(
        self,
        visual: NoteAuthorVisual,
        *,
        avatar_size: int = 24,
        time_text: str = "",
        name_only: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NoteAuthorRow")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._avatar = QLabel(self)
        self._avatar.setObjectName("NoteAuthorAvatar")
        self._avatar.setFixedSize(avatar_size, avatar_size)
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
        layout.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignTop)

        if name_only:
            label_text = visual.name
        elif time_text:
            label_text = f"{visual.name} · {time_text}"
        else:
            label_text = visual.name

        self._name = QLabel(label_text, self)
        self._name.setObjectName("ItemNotesMeta")
        self._name.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        self._name.setWordWrap(False)
        layout.addWidget(self._name, 1, Qt.AlignmentFlag.AlignVCenter)

    @classmethod
    def for_entry(
        cls,
        entry: ItemCommentEntry,
        workspace_root: Path | None,
        *,
        avatar_size: int = 24,
        time_text: str = "",
        name_only: bool = False,
        parent=None,
    ) -> NoteAuthorRow:
        return cls(
            entry_author_visual(entry, workspace_root),
            avatar_size=avatar_size,
            time_text=time_text,
            name_only=name_only,
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
        parent=None,
    ) -> NoteAuthorRow:
        return cls(
            visual,
            avatar_size=avatar_size,
            time_text=time_text,
            name_only=name_only,
            parent=parent,
        )
