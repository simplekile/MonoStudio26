"""
Inbox Drop Dialog: when user drops files/folders on Inbox page, show dialog to choose
client/freelancer, existing or new date folder, and optional description.
"""
from __future__ import annotations

from datetime import date, datetime
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

from monostudio.core.inbox_date_folder import (
    date_folder_sort_key,
    default_date_folder_suffix,
    format_date_folder_name,
    sanitize_date_folder_suffix,
)
from monostudio.core.inbox_reader import scan_inbox
from monostudio.core.outbox_reader import scan_outbox
from monostudio.ui_qt.calendar_date_picker import run_date_picker_dialog
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font


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
            # Sort by parsed date descending (newest first); legacy YYYY-MM-DD still works.
            out.sort(key=lambda x: date_folder_sort_key(x[0]), reverse=True)
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
        initial_date_str: str | None = None,
        prefer_existing_date: bool = False,
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
        self._initial_date_str = (initial_date_str or "").strip() or None
        self._prefer_existing_date = bool(prefer_existing_date and self._initial_date_str)

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
        self._new_date_edit.setPlaceholderText("Pick date below")
        self._new_date_edit.setReadOnly(True)
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
        suffix_row = QHBoxLayout()
        suffix_label = QLabel("Tag", self)
        suffix_label.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        suffix_label.setFixedWidth(36)
        suffix_row.addWidget(suffix_label, 0)
        self._suffix_edit = QLineEdit(self)
        self._suffix_edit.setObjectName("InboxDropSuffixEdit")
        self._suffix_edit.setPlaceholderText("Stb")
        self._suffix_edit.setMaxLength(6)
        self._suffix_edit.setText(default_date_folder_suffix(self._project_root))
        suffix_row.addWidget(self._suffix_edit, 1)
        form_layout.addLayout(suffix_row, 0)
        self._folder_preview = QLabel(self)
        self._folder_preview.setObjectName("DialogHint")
        self._folder_preview.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        form_layout.addWidget(self._folder_preview, 0)
        self._new_date_edit.textChanged.connect(self._update_folder_preview)
        self._suffix_edit.textChanged.connect(self._update_folder_preview)
        self._update_folder_preview()
        self._radio_existing.toggled.connect(self._on_date_mode_changed)
        self._radio_new.toggled.connect(self._on_date_mode_changed)
        self._on_source_changed()
        self._on_date_mode_changed()
        if self._prefer_existing_date and self._initial_date_str:
            self._radio_existing.setChecked(True)
            idx = self._existing_combo.findText(self._initial_date_str)
            if idx >= 0:
                self._existing_combo.setCurrentIndex(idx)

        # Description
        desc_label = QLabel("Description (optional)", self)
        desc_label.setObjectName("DialogSectionTitle")
        desc_label.setFont(monos_font("Inter", 11, QFont.Weight.Bold))
        form_layout.addWidget(desc_label, 0)
        self._description_edit = QLineEdit(self)
        self._description_edit.setPlaceholderText(
            "e.g. Batch review nhân vật A — applies to whole folder and files inside"
        )
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
        self._suffix_edit.setEnabled(not use_existing)
        self._suffix_edit.setVisible(not use_existing)
        self._folder_preview.setVisible(not use_existing)

    def _update_folder_preview(self) -> None:
        picked = self._parse_new_date_edit()
        if picked is None or not picked.isValid():
            self._folder_preview.setText("")
            return
        py_date = date(picked.year(), picked.month(), picked.day())
        tag = sanitize_date_folder_suffix(self._suffix_edit.text())
        self._folder_preview.setText(f"Folder: {format_date_folder_name(py_date, tag)}")

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
        initial = self._parse_new_date_edit() or QDate.currentDate()
        picked = run_date_picker_dialog(self, initial=initial, title="Choose date")
        if picked is not None and picked.isValid():
            self._new_date_edit.setText(picked.toString("yyyy-MM-dd"))

    def _get_date_str(self) -> str | None:
        if self._radio_new.isChecked():
            picked = self._parse_new_date_edit()
            if picked is None or not picked.isValid():
                return None
            py_date = date(picked.year(), picked.month(), picked.day())
            tag = sanitize_date_folder_suffix(self._suffix_edit.text())
            if not (self._suffix_edit.text() or "").strip():
                return None
            return format_date_folder_name(py_date, tag)
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
                if not self._parse_new_date_edit():
                    QMessageBox.warning(
                        self,
                        title,
                        "Pick a date using the calendar icon.",
                    )
                elif not (self._suffix_edit.text() or "").strip():
                    QMessageBox.warning(
                        self,
                        title,
                        "Enter a short tag for the folder name (e.g. Stb).",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        title,
                        "Could not build folder name. Check date and tag.",
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
