"""Add / edit project schedule milestones and production IN/OUT range."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.calendar_date_picker import MonosDateEdit
from monostudio.core.project_schedule import (
    ScheduleMilestone,
    delete_milestone,
    new_milestone_id,
    read_project_manifest_start_date,
    read_project_schedule,
    set_project_range,
    upsert_milestone,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font
from monostudio.ui_qt.toolbar_separators import apply_pill_segment_positions


def _section_title(text: str, parent: QWidget) -> QLabel:
    lbl = QLabel(text, parent)
    lbl.setObjectName("DialogSectionTitle")
    lbl.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
    return lbl


def _hint(text: str, parent: QWidget) -> QLabel:
    lbl = QLabel(text, parent)
    lbl.setObjectName("DialogHint")
    lbl.setWordWrap(True)
    lbl.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
    return lbl


def _field_label(text: str, parent: QWidget) -> QLabel:
    lbl = QLabel(text.upper(), parent)
    lbl.setObjectName("DialogHint")
    lbl.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
    return lbl


def _section_pill(parent: QWidget, label: str, tooltip: str) -> QPushButton:
    btn = QPushButton(label, parent)
    btn.setObjectName("Tier3Pill")
    btn.setCheckable(True)
    btn.setFlat(True)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setMinimumHeight(32)
    return btn


def _configure_date_field(field: MonosDateEdit, *, min_width: int = 184) -> None:
    field.setMinimumWidth(min_width)
    field.setMinimumHeight(36)
    field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def _configure_form_field(field: QWidget) -> None:
    field.setMinimumHeight(36)
    field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def _range_toggle_row(parent: QWidget, label: str, *, label_width: int = 192) -> tuple[QWidget, QCheckBox]:
    """Checkbox + label in a fixed-width column so date fields align."""
    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    chk = QCheckBox(row)
    text = QLabel(label, row)
    text.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
    lay.addWidget(chk, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(text, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addStretch(1)
    row.setFixedWidth(label_width)
    return row, chk


class ScheduleMilestoneDialog(MonosDialog):
    """Production range (IN/OUT) and project milestones for the schedule timeline."""

    schedule_changed = Signal()

    def __init__(self, *, parent=None, project_root: Path) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self.setObjectName("ScheduleMilestoneDialog")
        self.setWindowTitle("Timeline markers")
        self.setModal(True)
        self.setMinimumSize(620, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 20)
        root.setSpacing(16)

        header = _section_title("Timeline markers", self)
        root.addWidget(header)
        root.addWidget(
            _hint(
                "Green IN and amber OUT frame the schedule. Milestones appear as purple lines.",
                self,
            )
        )

        nav_row = QHBoxLayout()
        nav_row.setSpacing(0)
        self._section_pills = QWidget(self)
        self._section_pills.setObjectName("Tier3Container")
        self._section_pills.setAttribute(Qt.WA_StyledBackground, True)
        pills_lay = QHBoxLayout(self._section_pills)
        pills_lay.setContentsMargins(8, 8, 8, 8)
        pills_lay.setSpacing(6)
        self._pill_range = _section_pill(
            self._section_pills,
            "Production range",
            "Green IN and amber OUT markers for the whole schedule",
        )
        self._pill_milestones = _section_pill(
            self._section_pills,
            "Milestones",
            "Named dates shown as vertical lines on the timeline",
        )
        self._pill_range.setChecked(True)
        self._section_group = QButtonGroup(self)
        self._section_group.setExclusive(True)
        self._section_group.addButton(self._pill_range, 0)
        self._section_group.addButton(self._pill_milestones, 1)
        self._section_group.idClicked.connect(self._on_section_changed)
        pills_lay.addWidget(self._pill_range)
        pills_lay.addWidget(self._pill_milestones)
        apply_pill_segment_positions([self._pill_range, self._pill_milestones])
        nav_row.addWidget(self._section_pills, 0, Qt.AlignmentFlag.AlignLeft)
        nav_row.addStretch(1)
        root.addLayout(nav_row)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_range_tab())
        self._stack.addWidget(self._build_milestones_tab())
        root.addWidget(self._stack, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 12, 0, 0)
        footer.setSpacing(12)
        footer.addStretch(1)
        self._btn_close = QPushButton("Close", self)
        self._btn_close.setObjectName("DialogDestructiveButton")
        self._btn_close.setDefault(True)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setMinimumWidth(120)
        self._btn_close.clicked.connect(self._on_close)
        footer.addWidget(self._btn_close)
        root.addLayout(footer)

        self._editing_id: str | None = None
        self._load_range()
        self._reload_list()
        self._begin_new_milestone(focus=False)
        self._range_footer_hint.setVisible(self._stack.currentIndex() == 0)

    def _build_range_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(16)

        lay.addWidget(
            _hint(
                "Optional frame for the whole production. Uncheck to hide a marker on the timeline.",
                page,
            )
        )

        card = QFrame(page)
        card.setObjectName("ScheduleMilestoneFormCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 18, 18, 18)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)

        in_row, self._chk_in = _range_toggle_row(card, "Show start (IN)")
        self._chk_in.setCursor(Qt.CursorShape.PointingHandCursor)
        self._date_in = MonosDateEdit(card)
        self._date_in.setDate(QDate.currentDate())
        self._date_in.setEnabled(False)
        _configure_date_field(self._date_in)
        self._chk_in.toggled.connect(self._date_in.setEnabled)

        out_row, self._chk_out = _range_toggle_row(card, "Show deadline (OUT)")
        self._chk_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self._date_out = MonosDateEdit(card)
        self._date_out.setDate(QDate.currentDate())
        self._date_out.setEnabled(False)
        _configure_date_field(self._date_out)
        self._chk_out.toggled.connect(self._date_out.setEnabled)

        grid.addWidget(in_row, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self._date_in, 0, 1)
        grid.addWidget(out_row, 1, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self._date_out, 1, 1)
        grid.setColumnStretch(1, 1)
        card_lay.addLayout(grid)

        lay.addWidget(card)
        lay.addStretch(1)
        self._range_footer_hint = _hint(
            "Production range is saved when you close this dialog.",
            page,
        )
        lay.addWidget(self._range_footer_hint)
        return page

    def _build_milestones_tab(self) -> QWidget:
        page = QWidget(self)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 16, 0, 0)
        page_lay.setSpacing(16)

        columns = QHBoxLayout()
        columns.setSpacing(20)
        columns.setAlignment(Qt.AlignmentFlag.AlignTop)

        panel_min_h = 260

        # --- List column ---
        list_col = QWidget(page)
        list_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        list_lay = QVBoxLayout(list_col)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(0)

        list_body = QFrame(list_col)
        list_body.setObjectName("ScheduleMilestoneListBody")
        list_body.setAttribute(Qt.WA_StyledBackground, True)
        list_body.setMinimumHeight(panel_min_h + 46)
        list_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        list_body_lay = QVBoxLayout(list_body)
        list_body_lay.setContentsMargins(14, 14, 14, 14)
        list_body_lay.setSpacing(12)

        list_hdr = QWidget(list_body)
        list_hdr.setFixedHeight(32)
        list_hdr_lay = QHBoxLayout(list_hdr)
        list_hdr_lay.setContentsMargins(0, 0, 0, 0)
        list_hdr_lay.setSpacing(8)
        self._list_title = _section_title("Saved", list_hdr)
        list_hdr_lay.addWidget(self._list_title, 1, Qt.AlignmentFlag.AlignVCenter)

        self._btn_add = QPushButton("Add", list_hdr)
        self._btn_add.setObjectName("DialogSecondaryButton")
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.setFixedHeight(32)
        self._btn_add.setIcon(
            lucide_icon("plus", size=14, color_hex=MONOS_COLORS["text_label"])
        )
        self._btn_add.setIconSize(QSize(14, 14))
        self._btn_add.clicked.connect(self._on_add_clicked)
        list_hdr_lay.addWidget(self._btn_add, 0, Qt.AlignmentFlag.AlignVCenter)

        self._btn_delete = QToolButton(list_hdr)
        self._btn_delete.setObjectName("ScheduleIconToolBtn")
        self._btn_delete.setToolTip("Delete selected milestone")
        self._btn_delete.setAutoRaise(True)
        self._btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_delete.setIcon(
            lucide_icon("trash-2", size=16, color_hex="#f87171")
        )
        self._btn_delete.setIconSize(QSize(16, 16))
        self._btn_delete.setFixedSize(32, 32)
        self._btn_delete.clicked.connect(self._on_delete)
        list_hdr_lay.addWidget(self._btn_delete, 0, Qt.AlignmentFlag.AlignVCenter)
        list_body_lay.addWidget(list_hdr)

        self._list = QListWidget(list_body)
        self._list.setObjectName("ScheduleMilestoneList")
        self._list.currentItemChanged.connect(self._on_pick)
        list_body_lay.addWidget(self._list, 1)

        self._empty_hint = _hint(
            "No milestones yet.\nClick Add to create one.",
            list_body,
        )
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        list_body_lay.addWidget(self._empty_hint, 1)
        list_lay.addWidget(list_body, 1)

        columns.addWidget(list_col, 1)

        # --- Form column ---
        form_col = QFrame(page)
        form_col.setObjectName("ScheduleMilestoneFormCard")
        form_col.setAttribute(Qt.WA_StyledBackground, True)
        form_col.setMinimumHeight(panel_min_h + 46)
        form_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        form_lay = QVBoxLayout(form_col)
        form_lay.setContentsMargins(18, 18, 18, 18)
        form_lay.setSpacing(14)

        self._form_title = _section_title("New milestone", form_col)
        form_lay.addWidget(self._form_title)

        self._form_subtitle = _hint(
            "Name and date appear as a vertical line on the timeline.",
            form_col,
        )
        form_lay.addWidget(self._form_subtitle)

        form = QGridLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(14)
        form.setColumnMinimumWidth(0, 52)
        form.setColumnStretch(1, 1)

        self._label = QLineEdit(form_col)
        self._label.setPlaceholderText("e.g. Anim lock, Beta delivery…")
        _configure_form_field(self._label)
        self._date = MonosDateEdit(form_col)
        self._date.setDate(QDate.currentDate())
        _configure_form_field(self._date)

        form.addWidget(
            _field_label("Name", form_col),
            0,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        form.addWidget(self._label, 0, 1)
        form.addWidget(
            _field_label("Date", form_col),
            1,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        form.addWidget(self._date, 1, 1)
        form_lay.addLayout(form)

        form_lay.addStretch(1)

        self._btn_save_ms = QPushButton("Add milestone", form_col)
        self._btn_save_ms.setObjectName("DialogPrimaryButton")
        self._btn_save_ms.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save_ms.setMinimumHeight(36)
        self._btn_save_ms.clicked.connect(self._on_save_milestone)
        form_lay.addWidget(self._btn_save_ms)

        columns.addWidget(form_col, 1)
        page_lay.addLayout(columns, 1)
        return page

    def _sync_list_empty_state(self) -> None:
        n = self._list.count()
        self._list_title.setText(f"Saved ({n})" if n else "Saved")
        has_items = n > 0
        self._list.setVisible(has_items)
        self._empty_hint.setVisible(not has_items)
        self._btn_delete.setEnabled(has_items and self._editing_id is not None)

    def _sync_form_mode(self) -> None:
        if self._editing_id:
            self._form_title.setText("Edit milestone")
            self._btn_save_ms.setText("Save changes")
            self._btn_delete.setEnabled(True)
        else:
            self._form_title.setText("New milestone")
            self._btn_save_ms.setText("Add milestone")
            self._btn_delete.setEnabled(False)

    def _load_range(self) -> None:
        schedule = read_project_schedule(self._project_root)
        start_iso = schedule.project_start or read_project_manifest_start_date(
            self._project_root
        )
        if start_iso:
            d = QDate.fromString(start_iso[:10], "yyyy-MM-dd")
            if d.isValid():
                self._chk_in.setChecked(True)
                self._date_in.setDate(d)
        if schedule.project_end:
            d = QDate.fromString(schedule.project_end[:10], "yyyy-MM-dd")
            if d.isValid():
                self._chk_out.setChecked(True)
                self._date_out.setDate(d)

    def _reload_list(self) -> None:
        self._list.blockSignals(True)
        try:
            self._list.clear()
            schedule = read_project_schedule(self._project_root)
            for m in schedule.milestones:
                item = QListWidgetItem(f"{m.date}  ·  {m.label}")
                item.setData(Qt.ItemDataRole.UserRole, m.id)
                self._list.addItem(item)
        finally:
            self._list.blockSignals(False)
        self._sync_list_empty_state()

    def _on_pick(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            return
        mid = current.data(Qt.ItemDataRole.UserRole)
        self._editing_id = str(mid) if mid else None
        schedule = read_project_schedule(self._project_root)
        for m in schedule.milestones:
            if m.id == self._editing_id:
                self._label.setText(m.label)
                d = QDate.fromString((m.date or "")[:10], "yyyy-MM-dd")
                if d.isValid():
                    self._date.setDate(d)
                break
        self._sync_form_mode()

    def _begin_new_milestone(self, *, focus: bool = True) -> None:
        self._list.blockSignals(True)
        try:
            self._list.clearSelection()
            self._list.setCurrentRow(-1)
        finally:
            self._list.blockSignals(False)
        self._editing_id = None
        self._label.clear()
        self._date.setDate(QDate.currentDate())
        self._sync_form_mode()
        if focus:
            self._label.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_section_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._range_footer_hint.setVisible(index == 0)

    def _show_milestones_section(self) -> None:
        self._pill_milestones.setChecked(True)
        self._stack.setCurrentIndex(1)

    def _on_add_clicked(self) -> None:
        self._show_milestones_section()
        self._begin_new_milestone(focus=True)

    @staticmethod
    def _qdate_to_date(qd: QDate) -> date | None:
        if not qd.isValid():
            return None
        py = qd.toPython()
        if isinstance(py, date):
            return py
        return date(qd.year(), qd.month(), qd.day())

    def _persist_milestone(self, *, show_errors: bool) -> bool:
        label = self._label.text().strip()
        if not label:
            if show_errors:
                QMessageBox.warning(self, "Milestones", "Enter a name for the milestone.")
            return False
        d = self._qdate_to_date(self._date.date())
        if d is None:
            if show_errors:
                QMessageBox.warning(self, "Milestones", "Choose a valid date.")
            return False
        mid = self._editing_id or new_milestone_id()
        ms = ScheduleMilestone(id=mid, label=label, date=d.isoformat())
        try:
            upsert_milestone(self._project_root, ms)
        except OSError as ex:
            if show_errors:
                QMessageBox.critical(self, "Milestones", f"Could not save:\n{ex}")
            return False
        self._editing_id = mid
        self._reload_list()
        self._list.blockSignals(True)
        try:
            for i in range(self._list.count()):
                it = self._list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == mid:
                    self._list.setCurrentItem(it)
                    break
        finally:
            self._list.blockSignals(False)
        self._sync_form_mode()
        self.schedule_changed.emit()
        return True

    def _on_save_milestone(self) -> None:
        self._persist_milestone(show_errors=True)

    def _persist_production_range(self) -> bool:
        ps: str | None = None
        pe: str | None = None
        if self._chk_in.isChecked():
            d = self._qdate_to_date(self._date_in.date())
            if d is not None:
                ps = d.isoformat()
        if self._chk_out.isChecked():
            d = self._qdate_to_date(self._date_out.date())
            if d is not None:
                pe = d.isoformat()
        if ps and pe and ps > pe:
            QMessageBox.warning(
                self,
                "Production range",
                "Start (IN) must be on or before the deadline (OUT).",
            )
            return False
        try:
            set_project_range(self._project_root, project_start=ps, project_end=pe)
        except OSError as ex:
            QMessageBox.critical(self, "Production range", f"Could not save:\n{ex}")
            return False
        self.schedule_changed.emit()
        return True

    def _on_close(self) -> None:
        """Save pending milestone (if form filled), production range, then close."""
        if self._label.text().strip():
            if not self._persist_milestone(show_errors=True):
                return
        if not self._persist_production_range():
            return
        self.accept()

    def _on_delete(self) -> None:
        if not self._editing_id:
            return
        if (
            QMessageBox.question(
                self,
                "Delete milestone",
                "Remove this milestone from the schedule?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            delete_milestone(self._project_root, self._editing_id)
        except OSError as ex:
            QMessageBox.critical(self, "Milestones", f"Could not delete:\n{ex}")
            return
        self.schedule_changed.emit()
        self._reload_list()
        self._begin_new_milestone(focus=True)
