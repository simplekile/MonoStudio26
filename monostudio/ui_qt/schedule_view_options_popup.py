"""View-options popup for the Schedule page (layout, filters, wave draw)."""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.schedule_date_display import (
    DATE_FMT_D_MON,
    DATE_FMT_DM,
    DATE_FMT_DMY,
    DATE_FMT_ISO,
    DATE_FMT_MD,
    DATE_FMT_MDY,
    DATE_FMT_MON_D,
    DATE_FMT_MON_D_Y,
    date_format_preview,
)
from monostudio.core.schedule_dept_filter import (
    BAR_LABEL_DATE_RANGE,
    BAR_LABEL_DAYS,
    BAR_LABEL_DEPARTMENT,
    BAR_LABEL_ENTITY_NAME,
    BAR_LABEL_OFF,
)
from monostudio.ui_qt.popup_position import position_popup_near_anchor
from monostudio.ui_qt.style import monos_font
from PySide6.QtGui import QFont


class ScheduleViewOptionsPopup:
    """Popup anchored under the view-options toolbar button."""

    _POPUP_REOPEN_GRACE = 0.25

    def __init__(
        self,
        parent: QWidget,
        *,
        on_hidden: Callable[[], None] | None = None,
    ) -> None:
        self._parent = parent
        self._on_hidden = on_hidden
        self._closed_at = 0.0

        class _PopupFrame(QFrame):
            def __init__(self, outer: ScheduleViewOptionsPopup) -> None:
                super().__init__(outer._parent)
                self._outer = outer

            def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
                self._outer._closed_at = time.monotonic()
                if self._outer._on_hidden is not None:
                    self._outer._on_hidden()
                super().hideEvent(event)

        self.popup = _PopupFrame(self)
        self.popup.setObjectName("MainViewOptionsPopup")
        self.popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.popup.setAttribute(Qt.WA_StyledBackground, True)
        self.popup.setMinimumWidth(280)
        self.popup.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        outer = QVBoxLayout(self.popup)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(2)
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)

        # Host for page-injected control rows (Tools / Planning), shown above filters.
        self._extra_top = QWidget(self.popup)
        self._extra_top_lay = QVBoxLayout(self._extra_top)
        self._extra_top_lay.setContentsMargins(0, 0, 0, 0)
        self._extra_top_lay.setSpacing(6)
        self._extra_top.setVisible(False)
        outer.addWidget(self._extra_top)

        filter_lbl = QLabel("Display", self.popup)
        filter_lbl.setObjectName("ViewOptionsSectionLabel")
        filter_lbl.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        outer.addWidget(filter_lbl)

        self.chk_respect_hidden = QCheckBox("Inspector hidden depts", self.popup)
        self.chk_respect_hidden.setToolTip(
            "Hide departments marked hidden in Inspector → Departments → Manage"
        )
        self.chk_unscheduled = QCheckBox("Unscheduled only", self.popup)
        self.chk_overdue = QCheckBox("Overdue only", self.popup)
        for chk in (self.chk_respect_hidden, self.chk_unscheduled, self.chk_overdue):
            chk.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            outer.addWidget(chk)

        outer.addWidget(self._hline(self.popup))

        bar_lbl = QLabel("Bar label", self.popup)
        bar_lbl.setObjectName("ViewOptionsSectionLabel")
        bar_lbl.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        outer.addWidget(bar_lbl)

        self.bar_label_mode = QComboBox(self.popup)
        self.bar_label_mode.setToolTip("Text drawn on timeline bars")
        self.bar_label_mode.addItem("Days", BAR_LABEL_DAYS)
        self.bar_label_mode.addItem("Date range", BAR_LABEL_DATE_RANGE)
        self.bar_label_mode.addItem("Item name", BAR_LABEL_ENTITY_NAME)
        self.bar_label_mode.addItem("Department", BAR_LABEL_DEPARTMENT)
        self.bar_label_mode.addItem("Off", BAR_LABEL_OFF)
        self.bar_label_mode.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer.addWidget(self.bar_label_mode)

        self._date_fmt_block = QWidget(self.popup)
        date_fmt_lay = QVBoxLayout(self._date_fmt_block)
        date_fmt_lay.setContentsMargins(0, 0, 0, 0)
        date_fmt_lay.setSpacing(4)
        date_fmt_cap = QLabel("Date format", self._date_fmt_block)
        date_fmt_cap.setObjectName("ViewOptionsSectionLabel")
        date_fmt_cap.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        date_fmt_lay.addWidget(date_fmt_cap)
        self.date_display_format = QComboBox(self._date_fmt_block)
        self.date_display_format.setToolTip(
            "How start and due dates appear when bar label is Date range"
        )
        for label, fid in (
            (f"MM/DD — {date_format_preview(DATE_FMT_MD)}", DATE_FMT_MD),
            (f"DD/MM — {date_format_preview(DATE_FMT_DM)}", DATE_FMT_DM),
            (f"ISO — {date_format_preview(DATE_FMT_ISO)}", DATE_FMT_ISO),
            (f"MM/DD/YYYY — {date_format_preview(DATE_FMT_MDY)}", DATE_FMT_MDY),
            (f"DD/MM/YYYY — {date_format_preview(DATE_FMT_DMY)}", DATE_FMT_DMY),
            (f"Mon D — {date_format_preview(DATE_FMT_MON_D)}", DATE_FMT_MON_D),
            (f"D Mon — {date_format_preview(DATE_FMT_D_MON)}", DATE_FMT_D_MON),
            (f"Mon D, YYYY — {date_format_preview(DATE_FMT_MON_D_Y)}", DATE_FMT_MON_D_Y),
        ):
            self.date_display_format.addItem(label, fid)
        self.date_display_format.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        date_fmt_lay.addWidget(self.date_display_format)
        outer.addWidget(self._date_fmt_block)

        self._wave_sep = self._hline(self.popup)
        outer.addWidget(self._wave_sep)

        self._wave_block = QWidget(self.popup)
        wave_lay = QVBoxLayout(self._wave_block)
        wave_lay.setContentsMargins(0, 0, 0, 0)
        wave_lay.setSpacing(4)
        wave_lbl = QLabel("Wave draw", self._wave_block)
        wave_lbl.setObjectName("ViewOptionsSectionLabel")
        wave_lbl.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        wave_lay.addWidget(wave_lbl)
        self.wave_draw_mode = QComboBox(self._wave_block)
        self.wave_draw_mode.setToolTip(
            "Wave layout + Draw tool: how to apply the drawn range to child items"
        )
        wave_lay.addWidget(self.wave_draw_mode)
        outer.addWidget(self._wave_block)

    def add_top_section(self, title: str, body: QWidget) -> None:
        """Inject a labelled control row (e.g. Tools, Planning) above the filters."""
        cap = QLabel(title, self._extra_top)
        cap.setObjectName("ViewOptionsSectionLabel")
        cap.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        self._extra_top_lay.addWidget(cap)
        body.setParent(self._extra_top)
        self._extra_top_lay.addWidget(body)
        self._extra_top_lay.addWidget(self._hline(self._extra_top))
        self._extra_top.setVisible(True)

    @staticmethod
    def _hline(parent: QWidget) -> QFrame:
        line = QFrame(parent)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedHeight(1)
        line.setStyleSheet("background: #3f3f46; border: none; max-height: 1px;")
        return line

    def set_wave_draw_visible(self, visible: bool) -> None:
        self._wave_sep.setVisible(visible)
        self._wave_block.setVisible(visible)

    def set_date_format_visible(self, visible: bool) -> None:
        self._date_fmt_block.setVisible(visible)

    def toggle_below(self, anchor: QToolButton) -> None:
        if self.popup.isVisible():
            self.popup.close()
            return
        if (time.monotonic() - self._closed_at) < self._POPUP_REOPEN_GRACE:
            return
        self.popup.adjustSize()
        position_popup_near_anchor(self.popup, anchor)
        self.popup.show()

    def close(self) -> None:
        self.popup.close()
