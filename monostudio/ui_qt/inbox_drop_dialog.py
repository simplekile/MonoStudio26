"""
Inbox Drop Dialog: when user drops files/folders on Inbox page, show dialog to choose
client/freelancer, existing or new date folder, and optional description.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.inbox_reader import scan_inbox
from monostudio.core.outbox_reader import scan_outbox
from monostudio.ui_qt.calendar_date_picker import MonosCalendarWidget
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, MONOS_COLORS, monos_font


def _calendar_go_today(cal: MonosCalendarWidget) -> None:
    today = QDate.currentDate()
    cal.setSelectedDate(today)
    cal.setCurrentPage(today.year(), today.month())


def _get_date_folders_for_source(
    project_root: Path | None, source: str, *, target: str = "inbox"
) -> list[tuple[str, Path]]:
    """Return list of (date_str, path) for the given source (client/freelancer), newest first. target: 'inbox' | 'outbox'."""
    if not project_root or not source:
        return []
    try:
        nodes = scan_outbox(project_root) if target == "outbox" else scan_inbox(project_root)
    except Exception:
        return []
    source_lower = source.strip().lower()
    for node in nodes:
        if (node.name or "").lower() == source_lower and getattr(node, "children", None):
            out: list[tuple[str, Path]] = []
            for child in node.children:
                if getattr(child, "is_dir", True) and getattr(child, "path", None) and getattr(child, "name", None):
                    out.append((child.name, child.path))
            # Sort by date descending (newest first)
            out.sort(key=lambda x: x[0], reverse=True)
            return out
    return []


class InboxDropDialog(MonosDialog):
    """
    Dialog shown when files/folders are dropped on Inbox page.
    User selects: source (Client/Freelancer), date folder (existing list or new via calendar), optional description.
    On accept: caller gets (source, date_str, description) and calls add_to_inbox for each path.
    """

    def __init__(
        self,
        paths: list[Path],
        project_root: Path | None,
        initial_source: str | None,
        parent: QWidget | None = None,
        *,
        target: str = "inbox",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("InboxDropDialog")
        self._paths = [Path(p) for p in paths if p and Path(p).exists()]
        self._project_root = Path(project_root) if project_root else None
        self._initial_source = (initial_source or "").strip().lower() or "client"
        if self._initial_source not in ("client", "freelancer"):
            self._initial_source = "client"
        self._target = (target or "inbox").strip().lower() if target else "inbox"
        if self._target not in ("inbox", "outbox"):
            self._target = "inbox"

        self.setWindowTitle("Add to Outbox" if self._target == "outbox" else "Add to Inbox")
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        scroll = QScrollArea(self)
        scroll.setObjectName("InboxDropScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        form = QWidget(self)
        form.setObjectName("InboxDropForm")
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(12)

        # Items being added (read-only)
        label_items = QLabel(f"Adding {len(self._paths)} item(s):", self)
        label_items.setObjectName("DialogSectionTitle")
        label_items.setFont(monos_font("Inter", 11, QFont.Weight.Bold))
        form_layout.addWidget(label_items, 0)
        list_frame = QFrame(self)
        list_frame.setObjectName("InboxDropItemsList")
        list_lay = QVBoxLayout(list_frame)
        list_lay.setContentsMargins(8, 8, 8, 8)
        list_widget = QListWidget(self)
        list_widget.setObjectName("InboxDropItemsListWidget")
        list_widget.setMaximumHeight(100)
        for p in self._paths:
            item = QListWidgetItem(p.name or str(p))
            item.setToolTip(str(p))
            list_widget.addItem(item)
        list_lay.addWidget(list_widget)
        form_layout.addWidget(list_frame, 0)

        # Source: Client | Freelancer
        source_label = QLabel("Source", self)
        source_label.setObjectName("DialogSectionTitle")
        source_label.setFont(monos_font("Inter", 11, QFont.Weight.Bold))
        form_layout.addWidget(source_label, 0)
        self._source_client = QRadioButton("Client", self)
        self._source_freelancer = QRadioButton("Freelancer", self)
        self._source_group = QButtonGroup(self)
        self._source_group.addButton(self._source_client)
        self._source_group.addButton(self._source_freelancer)
        source_row = QHBoxLayout()
        source_row.addWidget(self._source_client)
        source_row.addWidget(self._source_freelancer)
        source_row.addStretch(1)
        form_layout.addLayout(source_row, 0)
        if self._initial_source == "freelancer":
            self._source_freelancer.setChecked(True)
        else:
            self._source_client.setChecked(True)
        self._source_client.toggled.connect(self._on_source_changed)
        self._source_freelancer.toggled.connect(self._on_source_changed)

        # Date folder: Existing vs New
        date_label = QLabel("Date folder", self)
        date_label.setObjectName("DialogSectionTitle")
        date_label.setFont(monos_font("Inter", 11, QFont.Weight.Bold))
        form_layout.addWidget(date_label, 0)
        self._radio_existing = QRadioButton("Existing date folder", self)
        self._radio_new = QRadioButton("New date folder", self)
        self._date_radio_group = QButtonGroup(self)
        self._date_radio_group.addButton(self._radio_existing)
        self._date_radio_group.addButton(self._radio_new)
        self._radio_new.setChecked(True)
        date_radio_row = QHBoxLayout()
        date_radio_row.addWidget(self._radio_existing)
        date_radio_row.addWidget(self._radio_new)
        date_radio_row.addStretch(1)
        form_layout.addLayout(date_radio_row, 0)

        self._existing_combo = QComboBox(self)
        self._existing_combo.setObjectName("InboxDropExistingDateCombo")
        self._existing_combo.setMinimumWidth(180)
        form_layout.addWidget(self._existing_combo, 0)
        # New date: line edit (yyyy-MM-dd) + calendar icon button → popup
        new_date_row = QHBoxLayout()
        self._new_date_edit = QLineEdit(self)
        self._new_date_edit.setObjectName("InboxDropNewDateEdit")
        self._new_date_edit.setPlaceholderText("yyyy-mm-dd")
        self._new_date_edit.setMinimumWidth(140)
        today_str = QDate.currentDate().toString("yyyy-MM-dd")
        self._new_date_edit.setText(today_str)
        new_date_row.addWidget(self._new_date_edit, 1)
        self._calendar_btn = QPushButton(self)
        self._calendar_btn.setObjectName("InboxDropCalendarBtn")
        self._calendar_btn.setToolTip("Choose date")
        cal_icon = lucide_icon("calendar", size=18, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        if not cal_icon.isNull():
            self._calendar_btn.setIcon(cal_icon)
        self._calendar_btn.setFixedSize(36, 36)
        self._calendar_btn.clicked.connect(self._on_open_calendar_popup)
        new_date_row.addWidget(self._calendar_btn, 0)
        form_layout.addLayout(new_date_row, 0)
        self._radio_existing.toggled.connect(self._on_date_mode_changed)
        self._radio_new.toggled.connect(self._on_date_mode_changed)
        self._on_source_changed()
        self._on_date_mode_changed()

        # Description
        desc_label = QLabel("Description (optional)", self)
        desc_label.setObjectName("DialogSectionTitle")
        desc_label.setFont(monos_font("Inter", 11, QFont.Weight.Bold))
        form_layout.addWidget(desc_label, 0)
        self._description_edit = QLineEdit(self)
        self._description_edit.setPlaceholderText("e.g. Batch from client review")
        self._description_edit.setObjectName("InboxDropDescription")
        form_layout.addWidget(self._description_edit, 0)
        form_layout.addStretch(1)
        scroll.setWidget(form)
        root.addWidget(scroll, 1)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            self,
        )
        buttons.setObjectName("InboxDropDialogButtons")
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("Add to Inbox")
            ok_btn.setDefault(True)
            ok_btn.setObjectName("DialogPrimaryButton")
            # Force primary (blue) look: QDialogButtonBox can ignore global QSS, so set on widget
            ok_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(37, 99, 235, 0.22);
                    border: 1px solid rgba(37, 99, 235, 0.70);
                    border-radius: 8px;
                    color: #fafafa;
                    padding: 8px 12px;
                    min-width: 80px;
                }
                QPushButton:hover {
                    background: rgba(37, 99, 235, 0.35);
                    border-color: rgba(59, 130, 246, 0.80);
                }
            """)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons, 0)

        self.setMinimumSize(420, 380)
        self.resize(440, 420)

    def _current_source(self) -> str:
        return "freelancer" if self._source_freelancer.isChecked() else "client"

    def _on_source_changed(self) -> None:
        source = self._current_source()
        folders = _get_date_folders_for_source(self._project_root, source, target=self._target)
        self._existing_combo.clear()
        for date_str, _path in folders:
            self._existing_combo.addItem(date_str, str(_path))
        if self._existing_combo.count() > 0:
            self._existing_combo.setCurrentIndex(0)

    def _on_date_mode_changed(self) -> None:
        use_existing = self._radio_existing.isChecked()
        self._existing_combo.setEnabled(use_existing)
        self._existing_combo.setVisible(use_existing)
        self._new_date_edit.setEnabled(not use_existing)
        self._calendar_btn.setEnabled(not use_existing)
        self._new_date_edit.setVisible(not use_existing)
        self._calendar_btn.setVisible(not use_existing)

    def _parse_new_date_edit(self) -> QDate | None:
        text = (self._new_date_edit.text() or "").strip()
        if not text:
            return None
        d = QDate.fromString(text, "yyyy-MM-dd")
        if d.isValid():
            return d
        try:
            dt = datetime.strptime(text, "%Y-%m-%d")
            return QDate(dt.year, dt.month, dt.day)
        except ValueError:
            return None

    def _on_open_calendar_popup(self) -> None:
        popup = MonosDialog(self)
        popup.setWindowTitle("Choose date")
        lay = QVBoxLayout(popup)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        cal = MonosCalendarWidget(popup)
        cal.setMinimumSize(420, 360)
        initial = self._parse_new_date_edit() or QDate.currentDate()
        cal.setSelectedDate(initial)
        cal.setCurrentPage(initial.year(), initial.month())
        lay.addWidget(cal.nav_bar(), 0)
        lay.addWidget(cal, 0)
        btn_row = QHBoxLayout()
        today_btn = QPushButton("Today", popup)
        today_btn.setObjectName("InboxDropCalendarTodayBtn")
        today_btn.setToolTip("Go to current date")
        today_btn.clicked.connect(lambda: _calendar_go_today(cal))
        btn_row.addWidget(today_btn, 0)
        btn_row.addStretch(1)
        ok_btn = QPushButton("OK", popup)
        ok_btn.setObjectName("DialogPrimaryButton")
        ok_btn.setDefault(True)
        def on_ok() -> None:
            d = cal.selectedDate()
            if d.isValid():
                self._new_date_edit.setText(d.toString("yyyy-MM-dd"))
            popup.accept()
        ok_btn.clicked.connect(on_ok)
        btn_row.addWidget(ok_btn, 0)
        cancel_btn = QPushButton("Cancel", popup)
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(popup.reject)
        btn_row.addWidget(cancel_btn, 0)
        lay.addLayout(btn_row, 0)
        popup.setMinimumSize(460, 480)
        popup.resize(480, 520)
        popup.exec()

    def _get_date_str(self) -> str | None:
        if self._radio_new.isChecked():
            text = (self._new_date_edit.text() or "").strip()
            if not text:
                return None
            d = self._parse_new_date_edit()
            return d.toString("yyyy-MM-dd") if d and d.isValid() else None
        idx = self._existing_combo.currentIndex()
        if idx >= 0:
            return self._existing_combo.itemText(idx) or None
        return None

    def _on_accept(self) -> None:
        source = self._current_source()
        date_str = self._get_date_str()
        if not date_str or not date_str.strip():
            title = "Add to Outbox" if self._target == "outbox" else "Add to Inbox"
            if self._radio_existing.isChecked() and self._existing_combo.count() == 0:
                QMessageBox.warning(
                    self,
                    title,
                    "No existing date folders for this source. Choose \"New date folder\" and enter or pick a date.",
                )
            elif self._radio_new.isChecked():
                QMessageBox.warning(
                    self,
                    title,
                    "Enter a date (yyyy-mm-dd) or click the calendar icon to pick a date.",
                )
            return
        self._result_source = source
        self._result_date_str = date_str.strip()
        self._result_description = (self._description_edit.text() or "").strip() or None
        self.accept()

    def result_values(self) -> tuple[str, str, str | None]:
        """After accept: (source, date_str, description)."""
        return (
            getattr(self, "_result_source", "client"),
            getattr(self, "_result_date_str", ""),
            getattr(self, "_result_description", None),
        )
