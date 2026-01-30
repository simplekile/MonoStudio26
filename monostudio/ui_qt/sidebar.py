from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle


class SidebarContext(str, Enum):
    ASSETS = "Assets"
    SHOTS = "Shots"


class Sidebar(QListWidget):
    """
    Spec: vertical list, icon + label, single selection.
    No tree, no sub-level, no filters, no actions.
    """

    context_changed = Signal(str)  # emitted when selection changes (Assets <-> Shots)
    context_clicked = Signal(str)  # emitted when clicking already-selected item (reload)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setUniformItemSizes(True)
        self._last_context_text: str | None = None

        assets_item = QListWidgetItem(SidebarContext.ASSETS.value)
        shots_item = QListWidgetItem(SidebarContext.SHOTS.value)

        folder_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        assets_item.setIcon(folder_icon)
        shots_item.setIcon(folder_icon)

        self.addItem(assets_item)
        self.addItem(shots_item)

        self.currentItemChanged.connect(self._on_current_item_changed)
        self.itemClicked.connect(self._on_item_clicked)

        # Default context: Assets (high-level context switch)
        self.setCurrentRow(0)

    def _on_current_item_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        self._last_context_text = current.text()
        self.context_changed.emit(current.text())

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        # Spec: click reloads Main View.
        # Keep autoscan triggers strict: only emit "clicked" when re-clicking same selection.
        if item.text() == self._last_context_text:
            self.context_clicked.emit(item.text())

