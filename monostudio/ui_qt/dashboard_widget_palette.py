"""Sidebar widget palette for dashboard customize mode."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.dashboard_layout import (
    DASHBOARD_WIDGET_LABELS,
    DEFAULT_LAYOUT,
    DashboardWidgetSlot,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

_WIDGET_ICONS: dict[str, str] = {
    "header": "layout-dashboard",
    "kpi": "layers",
    "pipeline_health": "activity",
    "dept_load": "users",
    "next_7_days": "calendar-days",
    "recent_notes": "message-square",
}

_ROLE_WIDGET_ID = int(Qt.ItemDataRole.UserRole)


def _sidebar_list_container(parent: QWidget | None = None) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("SidebarFilterListContainer")
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    return frame


class DashboardWidgetPalette(QWidget):
    """Lists dashboard bento widgets; toggle visibility to add/remove from the grid."""

    widget_visibility_toggled = Signal(str, bool)  # widget_id, visible

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardWidgetPalette")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 16)
        root.setSpacing(8)

        header = QLabel("WIDGETS", self)
        header.setObjectName("SidebarSectionHeader")
        header.setFont(monos_font("Inter", 11, QFont.Weight.ExtraBold))
        root.addWidget(header, 0, Qt.AlignmentFlag.AlignTop)

        hint = QLabel("Toggle cards to show or hide them on the dashboard.", self)
        hint.setObjectName("SidebarMutedText")
        hint.setWordWrap(True)
        root.addWidget(hint, 0, Qt.AlignmentFlag.AlignTop)

        self._list_container = _sidebar_list_container(self)
        self._list_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self._list = QListWidget(self._list_container)
        self._list_container.layout().addWidget(self._list, 1)
        self._list.setObjectName("SidebarFilterList")
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setSpacing(2)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._list_container, 1)

        self._build_rows()
        self._list.itemChanged.connect(self._on_item_changed)

    def _build_rows(self) -> None:
        self._list.blockSignals(True)
        try:
            self._list.clear()
            for slot in DEFAULT_LAYOUT:
                wid = slot.id
                label = DASHBOARD_WIDGET_LABELS.get(wid, wid)
                it = QListWidgetItem(label)
                it.setData(_ROLE_WIDGET_ID, wid)
                icon_name = _WIDGET_ICONS.get(wid, "layout-dashboard")
                icon = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_label"])
                if not icon.isNull():
                    it.setIcon(icon)
                it.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                it.setCheckState(Qt.CheckState.Unchecked)
                self._list.addItem(it)
        finally:
            self._list.blockSignals(False)

    def sync_slots(self, slots: list[DashboardWidgetSlot] | None) -> None:
        visibility = {s.id: s.visible for s in (slots or [])}
        self._syncing = True
        self._list.blockSignals(True)
        try:
            for i in range(self._list.count()):
                it = self._list.item(i)
                if it is None:
                    continue
                wid = it.data(_ROLE_WIDGET_ID)
                if not isinstance(wid, str):
                    continue
                visible = bool(visibility.get(wid, False))
                it.setCheckState(
                    Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
                )
        finally:
            self._list.blockSignals(False)
            self._syncing = False

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._syncing:
            return
        wid = item.data(_ROLE_WIDGET_ID)
        if not isinstance(wid, str) or not wid.strip():
            return
        visible = item.checkState() == Qt.CheckState.Checked
        self.widget_visibility_toggled.emit(wid, visible)
