"""
MONOS calendar widget for date selection (Inbox drop dialog, reuse elsewhere).
QCalendarWidget with rounded selected cell (8px), Deep Dark style.
Custom Prev | Month Year | Next bar so icons always show; weekday header styled to avoid "..." elision.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QLocale, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPalette, QPen
from PySide6.QtWidgets import (
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProxyStyle,
    QPushButton,
    QStyle,
    QTableView,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


def _month_year_label(d: QDate) -> str:
    return d.toString("MMMM yyyy")


class _NoFocusRectStyle(QProxyStyle):
    """Hide focus/selection frame and extra decoration on calendar grid cells (only our paintCell draws selection)."""

    def drawPrimitive(self, element, option, painter, widget):
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
            return
        # Skip frame/line drawing that can produce the blue line under the selected cell
        if element == QStyle.PrimitiveElement.PE_Frame:
            return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget):
        if element == QStyle.ControlElement.CE_ItemViewItem:
            from PySide6.QtWidgets import QStyleOptionViewItem
            if isinstance(option, QStyleOptionViewItem):
                opt = QStyleOptionViewItem(option)
                # No focus/selection decoration from style — selected cell is drawn entirely in paintCell
                opt.state &= ~int(QStyle.StateFlag.State_HasFocus)
                opt.state &= ~int(QStyle.StateFlag.State_Selected)
                super().drawControl(element, opt, painter, widget)
                return
        super().drawControl(element, option, painter, widget)


class MonosCalendarWidget(QCalendarWidget):
    """
    Calendar for picking a single date (YYYY-MM-DD).
    Custom nav bar: Prev | Month Year | Next (lucide chevrons). Selected cell 8px rounded.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MonosCalendar")
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setNavigationBarVisible(False)
        today = QDate.currentDate()
        self.setSelectedDate(today)
        self.setCurrentPage(today.year(), today.month())
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))

        # Custom nav bar: [Prev] Month Year [Next]
        nav = QWidget(self)
        nav.setObjectName("MonosCalendarNavBar")
        nav.setMinimumHeight(44)
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(0, 0, 0, 8)
        nav_lay.setSpacing(8)
        icon_color = MONOS_COLORS.get("text_label", "#a1a1aa")
        prev_btn = QPushButton(nav)
        prev_btn.setObjectName("MonosCalendarPrevBtn")
        prev_btn.setIcon(lucide_icon("chevron-left", size=20, color_hex=icon_color))
        prev_btn.setIconSize(QSize(20, 20))
        prev_btn.setFixedSize(36, 36)
        prev_btn.setToolTip("Previous month")
        prev_btn.clicked.connect(self._go_prev_month)
        nav_lay.addWidget(prev_btn, 0)
        self._month_label = QLabel(nav)
        self._month_label.setObjectName("MonosCalendarMonthLabel")
        self._month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_label.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        self._month_label.setStyleSheet(f"color: {MONOS_COLORS.get('text_primary', '#e4e4e7')};")
        nav_lay.addWidget(self._month_label, 1)
        next_btn = QPushButton(nav)
        next_btn.setObjectName("MonosCalendarNextBtn")
        next_btn.setIcon(lucide_icon("chevron-right", size=20, color_hex=icon_color))
        next_btn.setIconSize(QSize(20, 20))
        next_btn.setFixedSize(36, 36)
        next_btn.setToolTip("Next month")
        next_btn.clicked.connect(self._go_next_month)
        nav_lay.addWidget(next_btn, 0)
        self._nav_bar = nav
        self._nav_bar_inserted = False
        self._update_month_label()
        self._try_insert_nav_bar()

        # Hide default selection rect; generous cell sizes so day numbers don't elide
        table = self.findChild(QTableView)
        if table is not None:
            table.setFrameShape(QFrame.Shape.NoFrame)
            table.setShowGrid(False)
            table.setStyle(_NoFocusRectStyle(table.style()))
            pal = table.palette()
            pal.setColor(QPalette.ColorRole.Highlight, Qt.GlobalColor.transparent)
            table.setPalette(pal)
            table.horizontalHeader().setDefaultSectionSize(56)
            table.verticalHeader().setDefaultSectionSize(46)
            table.horizontalHeader().setMinimumSectionSize(48)
            table.verticalHeader().setMinimumSectionSize(40)
            hdr = table.horizontalHeader()
            hdr_font = hdr.font()
            hdr_font.setPointSize(10)
            hdr.setFont(hdr_font)

        # Use Blue-400 for selected cell (plan: MONOS)
        pal = self.palette()
        pal.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight, QColor(MONOS_COLORS.get("blue_400", "#60a5fa")))
        pal.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.setPalette(pal)

        self.currentPageChanged.connect(self._on_page_changed)

    def nav_bar(self) -> QWidget:
        """Return the custom nav bar widget so the parent can add it above the calendar (recommended)."""
        return self._nav_bar

    def _try_insert_nav_bar(self) -> None:
        """Insert custom nav bar above the grid if not yet added by parent; retry in showEvent if layout not ready at init."""
        if getattr(self, "_nav_bar_inserted", True):
            return
        lay = self.layout()
        if lay is not None:
            lay.insertWidget(0, self._nav_bar)
            self._nav_bar_inserted = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._try_insert_nav_bar()

    def _update_month_label(self) -> None:
        y, m = self.yearShown(), self.monthShown()
        self._month_label.setText(_month_year_label(QDate(y, m, 1)))

    def _on_page_changed(self, year: int, month: int) -> None:
        self._update_month_label()

    def _go_prev_month(self) -> None:
        y, m = self.yearShown(), self.monthShown()
        m -= 1
        if m < 1:
            m = 12
            y -= 1
        self.setCurrentPage(y, m)

    def _go_next_month(self) -> None:
        y, m = self.yearShown(), self.monthShown()
        m += 1
        if m > 12:
            m = 1
            y += 1
        self.setCurrentPage(y, m)

    def paintCell(self, painter, rect, date) -> None:
        today = QDate.currentDate()
        radius = 8
        is_selected = date == self.selectedDate()
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform, True)
        # Today (when not selected): subtle border so it's visible but distinct from selected
        if date == today and not is_selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(MONOS_COLORS.get("text_meta", "#71717a")), 2))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), radius - 1, radius - 1)
        # Selected: we draw everything ourselves to avoid any default selection line/rect from base
        if is_selected:
            painter.setBrush(self.palette().highlight())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText))
            painter.setFont(self.font())
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), str(date.day()))
            painter.restore()
            return
        painter.restore()
        super().paintCell(painter, rect, date)
