"""Minimal name popup when adding a milestone from the timeline."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from monostudio.ui_qt.popup_position import position_popup_near_global_point
from monostudio.ui_qt.style import monos_font


def default_milestone_name(d: date) -> str:
    return d.strftime("%b %d").replace(" 0", " ")


class ScheduleQuickMilestonePopup(QFrame):
    """Single-field popup — date comes from the timeline column, not from this form."""

    name_accepted = Signal(str)

    def __init__(
        self,
        parent: QWidget | None,
        *,
        anchor_global: QPoint,
        milestone_date: date,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("ScheduleQuickMilestonePopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        hint = QLabel(f"New milestone · {milestone_date.isoformat()}", self)
        hint.setObjectName("DialogHint")
        hint.setFont(monos_font("Inter", 11))
        root.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._name = QLineEdit(self)
        self._name.setObjectName("ScheduleSearchEdit")
        self._name.setPlaceholderText("Milestone name…")
        self._name.setText(default_milestone_name(milestone_date))
        self._name.selectAll()
        row.addWidget(self._name, 1)

        btn_ok = QPushButton("Add", self)
        btn_ok.setObjectName("DialogPrimaryButton")
        btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_accept)
        row.addWidget(btn_ok)
        root.addLayout(row)

        self._name.returnPressed.connect(self._on_accept)
        self.setMinimumWidth(280)
        position_popup_near_global_point(self, anchor_global)
        self._name.setFocus()

    def _on_accept(self) -> None:
        label = self._name.text().strip()
        if not label:
            return
        self.name_accepted.emit(label)
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


def show_milestone_name_popup(
    parent: QWidget,
    *,
    anchor_global: QPoint,
    milestone_date: date,
    on_accept: Callable[[str], None],
) -> ScheduleQuickMilestonePopup:
    """Show popup; ``on_accept`` receives the trimmed name."""
    popup = ScheduleQuickMilestonePopup(
        parent,
        anchor_global=anchor_global,
        milestone_date=milestone_date,
    )
    popup.name_accepted.connect(on_accept)
    popup.show()
    return popup
