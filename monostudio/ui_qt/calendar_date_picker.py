"""
MONOS calendar / date picker — custom month grid (Deep Dark), reusable across dialogs.
"""
from __future__ import annotations

import calendar
from typing import TYPE_CHECKING

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as QWidgetType

_WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_CELL = 40
_GRID_ROWS = 6
_GRID_COLS = 7


def _month_year_label(d: QDate) -> str:
    return d.toString("MMMM yyyy")


class _DayButton(QPushButton):
    """Single day cell in the month grid."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MonosCalendarDayBtn")
        self.setFlat(True)
        self.setFixedSize(_CELL, _CELL)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cell_date: QDate | None = None

    def set_day(
        self,
        *,
        qdate: QDate | None,
        in_month: bool,
        selected: bool,
        today: bool,
        weekend: bool,
    ) -> None:
        self._cell_date = qdate
        if qdate is None or not qdate.isValid() or not in_month:
            self.setText("")
            self.setEnabled(False)
            self.setProperty("inMonth", False)
            self.setProperty("selected", False)
            self.setProperty("today", False)
            self.setProperty("weekend", False)
            self._polish()
            return
        self.setText(str(qdate.day()))
        self.setEnabled(True)
        self.setProperty("inMonth", True)
        self.setProperty("selected", selected)
        self.setProperty("today", today)
        self.setProperty("weekend", weekend)
        self._polish()

    def _polish(self) -> None:
        st = self.style()
        st.unpolish(self)
        st.polish(self)

    def cell_date(self) -> QDate | None:
        return self._cell_date


class MonosCalendarWidget(QWidget):
    """
    Month calendar for picking a single date (YYYY-MM-DD).
    Custom 7×6 grid with weekday headers — avoids QCalendarWidget layout/QSS bugs.
    """

    clicked = Signal(QDate)
    currentPageChanged = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MonosCalendar")
        today = QDate.currentDate()
        self._selected = today
        self._year = today.year()
        self._month = today.month()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self._nav_bar = self._build_nav_bar()
        root.addWidget(self._nav_bar, 0)

        weekday_row = QHBoxLayout()
        weekday_row.setSpacing(4)
        for label in _WEEKDAY_LABELS:
            wd = QLabel(label, self)
            wd.setObjectName("MonosCalendarWeekday")
            wd.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wd.setFixedSize(_CELL, 22)
            wd.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
            weekday_row.addWidget(wd)
        root.addLayout(weekday_row)

        grid_host = QWidget(self)
        grid_host.setObjectName("MonosCalendarGrid")
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(4)
        self._grid.setVerticalSpacing(4)
        self._cells: list[_DayButton] = []
        for row in range(_GRID_ROWS):
            for col in range(_GRID_COLS):
                btn = _DayButton(grid_host)
                btn.clicked.connect(self._on_day_clicked)
                self._grid.addWidget(btn, row, col)
                self._cells.append(btn)
        root.addWidget(grid_host, 0)

        self.setMinimumSize(7 * _CELL + 6 * 4 + 16, _GRID_ROWS * _CELL + 22 + 44 + 32)
        self._refresh_grid()
        self.currentPageChanged.connect(lambda _y, _m: self._update_month_label())

    def nav_bar(self) -> QWidget:
        """Nav bar widget (also embedded in layout); for parents that place it separately."""
        return self._nav_bar

    def _build_nav_bar(self) -> QWidget:
        nav = QWidget(self)
        nav.setObjectName("MonosCalendarNavBar")
        nav.setMinimumHeight(40)
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(8)
        icon_color = MONOS_COLORS.get("text_label", "#a1a1aa")
        prev_btn = QPushButton(nav)
        prev_btn.setObjectName("MonosCalendarPrevBtn")
        prev_btn.setIcon(lucide_icon("chevron-left", size=18, color_hex=icon_color))
        prev_btn.setIconSize(QSize(18, 18))
        prev_btn.setFixedSize(36, 36)
        prev_btn.setToolTip("Previous month")
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.clicked.connect(self._go_prev_month)
        nav_lay.addWidget(prev_btn, 0)
        self._month_label = QLabel(nav)
        self._month_label.setObjectName("MonosCalendarMonthLabel")
        self._month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_label.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        nav_lay.addWidget(self._month_label, 1)
        next_btn = QPushButton(nav)
        next_btn.setObjectName("MonosCalendarNextBtn")
        next_btn.setIcon(lucide_icon("chevron-right", size=18, color_hex=icon_color))
        next_btn.setIconSize(QSize(18, 18))
        next_btn.setFixedSize(36, 36)
        next_btn.setToolTip("Next month")
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.clicked.connect(self._go_next_month)
        nav_lay.addWidget(next_btn, 0)
        self._update_month_label()
        return nav

    def selectedDate(self) -> QDate:
        return self._selected

    def setSelectedDate(self, date: QDate) -> None:
        if not date.isValid():
            return
        self._selected = date
        if date.year() != self._year or date.month() != self._month:
            self.setCurrentPage(date.year(), date.month())
        else:
            self._refresh_grid()

    def setCurrentPage(self, year: int, month: int) -> None:
        month = max(1, min(12, month))
        if year == self._year and month == self._month:
            return
        self._year = year
        self._month = month
        self._update_month_label()
        self._refresh_grid()
        self.currentPageChanged.emit(year, month)

    def yearShown(self) -> int:
        return self._year

    def monthShown(self) -> int:
        return self._month

    def _update_month_label(self) -> None:
        self._month_label.setText(_month_year_label(QDate(self._year, self._month, 1)))

    def _go_prev_month(self) -> None:
        m = self._month - 1
        y = self._year
        if m < 1:
            m = 12
            y -= 1
        self.setCurrentPage(y, m)

    def _go_next_month(self) -> None:
        m = self._month + 1
        y = self._year
        if m > 12:
            m = 1
            y += 1
        self.setCurrentPage(y, m)

    def _refresh_grid(self) -> None:
        cal = calendar.Calendar(firstweekday=0)
        weeks = cal.monthdayscalendar(self._year, self._month)
        while len(weeks) < _GRID_ROWS:
            weeks.append([0] * _GRID_COLS)
        today = QDate.currentDate()
        idx = 0
        for row in range(_GRID_ROWS):
            for col in range(_GRID_COLS):
                day = weeks[row][col] if row < len(weeks) else 0
                btn = self._cells[idx]
                idx += 1
                if day == 0:
                    btn.set_day(qdate=None, in_month=False, selected=False, today=False, weekend=False)
                    continue
                qd = QDate(self._year, self._month, day)
                btn.set_day(
                    qdate=qd,
                    in_month=True,
                    selected=qd == self._selected,
                    today=qd == today,
                    weekend=col >= 5,
                )

    def _on_day_clicked(self) -> None:
        btn = self.sender()
        if not isinstance(btn, _DayButton):
            return
        qd = btn.cell_date()
        if qd is None or not qd.isValid():
            return
        self._selected = qd
        self._refresh_grid()
        self.clicked.emit(qd)


def calendar_go_today(cal: MonosCalendarWidget) -> None:
    today = QDate.currentDate()
    cal.setSelectedDate(today)
    cal.setCurrentPage(today.year(), today.month())


def run_date_picker_dialog(
    parent: QWidgetType | None,
    *,
    initial: QDate | None = None,
    title: str = "Choose date",
) -> QDate | None:
    """Modal MONOS calendar dialog; returns chosen date or None if cancelled."""
    popup = MonosDialog(parent)
    popup.setWindowTitle(title)
    lay = QVBoxLayout(popup)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)

    cal = MonosCalendarWidget(popup)
    start = initial if initial and initial.isValid() else QDate.currentDate()
    cal.setSelectedDate(start)
    cal.setCurrentPage(start.year(), start.month())
    lay.addWidget(cal, 0)

    chosen: QDate | None = None

    btn_row = QHBoxLayout()
    today_btn = QPushButton("Today", popup)
    today_btn.setObjectName("DialogSecondaryButton")
    today_btn.setToolTip("Go to current date")
    today_btn.clicked.connect(lambda: calendar_go_today(cal))
    btn_row.addWidget(today_btn, 0)
    btn_row.addStretch(1)
    ok_btn = QPushButton("OK", popup)
    ok_btn.setObjectName("DialogPrimaryButton")
    ok_btn.setDefault(True)

    def on_ok() -> None:
        nonlocal chosen
        d = cal.selectedDate()
        if d.isValid():
            chosen = d
        popup.accept()

    ok_btn.clicked.connect(on_ok)
    btn_row.addWidget(ok_btn, 0)
    cancel_btn = QPushButton("Cancel", popup)
    cancel_btn.setObjectName("DialogSecondaryButton")
    cancel_btn.clicked.connect(popup.reject)
    btn_row.addWidget(cancel_btn, 0)
    lay.addLayout(btn_row, 0)

    popup.setMinimumSize(320, 380)
    popup.resize(340, 400)
    if popup.exec() != popup.DialogCode.Accepted:
        return None
    return chosen


class MonosDateEdit(QWidget):
    """
    Date field with MONOS calendar popup (replaces QDateEdit + setCalendarPopup).
    Calendar button sits inside the field on the right so total width matches QLineEdit.
    API: date(), setDate(), setEnabled(), dateChanged.
    """

    dateChanged = Signal(QDate)

    _CALENDAR_BTN = 24
    _CALENDAR_INSET = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MonosDateEdit")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        field_host = QWidget(self)
        field_host.setObjectName("MonosDateEditHost")
        field_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        host_lay = QGridLayout(field_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.setSpacing(0)

        self._edit = QDateEdit(field_host)
        self._edit.setObjectName("MonosDateEditField")
        self._edit.setCalendarPopup(False)
        self._edit.setDisplayFormat("yyyy-MM-dd")
        self._edit.setButtonSymbols(QDateEdit.ButtonSymbols.NoButtons)
        self._edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        icon_color = MONOS_COLORS.get("text_label", "#a1a1aa")
        self._btn = QToolButton(field_host)
        self._btn.setObjectName("MonosDateEditCalendarBtn")
        self._btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._btn.setAutoRaise(True)
        self._btn.setIcon(lucide_icon("calendar", size=18, color_hex=icon_color))
        self._btn.setIconSize(QSize(18, 18))
        self._btn.setFixedSize(self._CALENDAR_BTN, self._CALENDAR_BTN)
        self._btn.setToolTip("Pick date…")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._open_picker)

        host_lay.addWidget(self._edit, 0, 0)
        host_lay.addWidget(
            self._btn,
            0,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        host_lay.setColumnStretch(0, 1)
        outer.addWidget(field_host, 1)
        self._edit.dateChanged.connect(self.dateChanged.emit)

    def date(self) -> QDate:
        return self._edit.date()

    def setDate(self, date: QDate) -> None:
        self._edit.setDate(date)

    def setDisplayFormat(self, fmt: str) -> None:
        self._edit.setDisplayFormat(fmt)

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self._edit.setEnabled(enabled)
        self._btn.setEnabled(enabled)

    def _open_picker(self) -> None:
        initial = self._edit.date()
        picked = run_date_picker_dialog(self.window(), initial=initial)
        if picked is not None and picked.isValid():
            self._edit.setDate(picked)
