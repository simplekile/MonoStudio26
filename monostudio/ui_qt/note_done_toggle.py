"""Done/active toggle for note list rows — same Lucide icons as the context menu."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QToolButton

from monostudio.ui_qt.note_context_menu import (
    NOTE_DONE_TOGGLE_ICON_SIZE,
    note_done_toggle_icon,
)


class NoteDoneToggleButton(QToolButton):
    """Checkable icon button: gray circle (active) ↔ green circle-check (done)."""

    def __init__(self, *, checked: bool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ItemNotesDoneCheck")
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        side = NOTE_DONE_TOGGLE_ICON_SIZE
        self.setIconSize(QSize(side, side))
        pad = 6
        self.setFixedSize(side + pad * 2, side + pad * 2)
        self.setChecked(checked)
        self._sync_icon_and_tip()

    def setChecked(self, checked: bool) -> None:  # type: ignore[override]
        super().setChecked(checked)
        self._sync_icon_and_tip()

    def _sync_icon_and_tip(self) -> None:
        done = self.isChecked()
        self.setIcon(note_done_toggle_icon(done=done))
        self.setToolTip("Mark as active" if done else "Mark as done")
