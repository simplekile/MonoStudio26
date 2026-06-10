"""Shared content toolbar for Inbox/Outbox pages (date folders + file tree)."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from monostudio.ui_qt.toolbar_separators import add_widgets_with_icon_separators


def bind_explorer_view_mode_tab_shortcut(host: QWidget, get_pane: Callable[[], object | None]) -> None:
    """Tab cycles grid/list on Inbox / Outbox / Project Guide explorer pages."""

    def _cycle() -> None:
        pane = get_pane()
        cycle = getattr(pane, "cycle_view_mode", None)
        if callable(cycle):
            cycle()

    tab_sc = QShortcut(QKeySequence(Qt.Key.Key_Tab), host, _cycle)
    tab_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    tab_sc.setAutoRepeat(False)


class InboxContentToolbar(QWidget):
    """Hint + Grid/List toggle (context lives in page header breadcrumb)."""

    view_mode_changed = Signal(str)  # "tile" | "list"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InboxContentToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        lay.addStretch(1)

        self._hint_label = QLabel("", self)
        self._hint_label.setObjectName("InboxToolbarHint")
        lay.addWidget(self._hint_label, 0, Qt.AlignmentFlag.AlignVCenter)

        toggle = QWidget(self)
        toggle.setObjectName("Tier3Container")
        toggle.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        toggle_lay = QHBoxLayout(toggle)
        toggle_lay.setContentsMargins(6, 6, 6, 6)
        toggle_lay.setSpacing(4)

        self._btn_grid = QPushButton("Grid", toggle)
        self._btn_grid.setObjectName("Tier3Pill")
        self._btn_grid.setCheckable(True)
        self._btn_grid.setFlat(True)
        self._btn_list = QPushButton("List", toggle)
        self._btn_list.setObjectName("Tier3Pill")
        self._btn_list.setCheckable(True)
        self._btn_list.setFlat(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self._btn_grid, 0)
        group.addButton(self._btn_list, 1)
        self._btn_grid.clicked.connect(lambda: self.view_mode_changed.emit("tile"))
        self._btn_list.clicked.connect(lambda: self.view_mode_changed.emit("list"))
        add_widgets_with_icon_separators(toggle_lay, [self._btn_grid, self._btn_list], toggle, sep_height=18)
        self._view_toggle = toggle
        lay.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        self._inline = False

    def set_inline(self, inline: bool) -> None:
        """Path-bar row: transparent, no extra vertical padding."""
        if self._inline == inline:
            return
        self._inline = inline
        self.setObjectName("InboxPathBarToolbar" if inline else "InboxContentToolbar")
        lay = self.layout()
        if inline:
            lay.setContentsMargins(0, 0, 0, 0)
        else:
            lay.setContentsMargins(16, 10, 16, 10)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_context(
        self,
        *,
        hint: str = "",
        show_toggle: bool = True,
        section_title: str = "",  # kept for callers; unused
        count: int | None = None,  # kept for callers; unused
    ) -> None:
        del section_title, count
        self._hint_label.setText(hint)
        self._view_toggle.setVisible(show_toggle)

    def set_view_mode(self, mode: str) -> None:
        self._btn_grid.setChecked(mode == "tile")
        self._btn_list.setChecked(mode == "list")

    def view_toggle(self) -> QWidget:
        return self._view_toggle
