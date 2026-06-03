"""Read-only schedule edit history for the current project."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.schedule_history import ScheduleHistoryEntry, read_schedule_history
from monostudio.core.user_identity import StudioUser, avatar_path, get_user, read_roster
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio
from monostudio.ui_qt.user_profile_view_dialog import open_studio_user_profile

_ROW_H = 34
_AVATAR_PX = 20


def _format_saved_at(iso_str: str) -> str:
    s = (iso_str or "").strip()
    if not s:
        return "—"
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s[:16] if len(s) >= 16 else s


def _resolve_history_user(
    workspace_root: Path | None,
    entry: ScheduleHistoryEntry,
) -> StudioUser | None:
    if workspace_root is None:
        return None
    uid = (entry.user_id or "").strip()
    if uid:
        user = get_user(workspace_root, uid)
        if user is not None:
            return user
    name_key = (entry.user_name or "").strip().casefold()
    if not name_key:
        return None
    for user in read_roster(workspace_root):
        if (user.name or "").strip().casefold() == name_key:
            return user
    return None


def _edited_by_column_width(entries: tuple[ScheduleHistoryEntry, ...]) -> int:
    font = monos_font("Inter", 11, QFont.Weight.DemiBold)
    fm = QFontMetrics(font)
    names = [(e.user_name or "Artist").strip() or "Artist" for e in entries]
    names.extend(("Artist", "Edited by"))
    text_w = max(fm.horizontalAdvance(name) for name in names)
    return text_w + _AVATAR_PX + 6 + 28


class _AuthorLinkLabel(QLabel):
    def __init__(self, text: str, *, on_click, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("ScheduleHistoryAuthorLink")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self._on_click = on_click

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


class _ScheduleHistoryAuthorCell(QWidget):
    def __init__(
        self,
        *,
        workspace_root: Path | None,
        entry: ScheduleHistoryEntry,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        user = _resolve_history_user(workspace_root, entry)
        name = (entry.user_name or "Artist").strip() or "Artist"
        initials = user.initials if user else (name[:2].upper() if name else "?")
        color = user.color_hex if user else "#71717a"
        img = avatar_path(workspace_root, user) if user and workspace_root else None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        avatar = QLabel(self)
        avatar.setObjectName("ScheduleHistoryAvatar")
        avatar.setFixedSize(_AVATAR_PX, _AVATAR_PX)
        dpr = effective_device_pixel_ratio(self)
        avatar.setPixmap(
            avatar_pixmap_for(img, initials, color, _AVATAR_PX, dpr=dpr)
        )
        lay.addWidget(avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        uid = (user.id if user else entry.user_id or "").strip()
        if uid and workspace_root is not None:
            link = _AuthorLinkLabel(
                name,
                on_click=lambda u=uid: open_studio_user_profile(
                    workspace_root, u, parent=self.window()
                ),
                parent=self,
            )
            lay.addWidget(link, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            lbl = QLabel(name, self)
            lbl.setObjectName("ScheduleHistoryAuthorName")
            lbl.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
            lbl.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
            lay.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)


class ScheduleHistoryDialog(MonosDialog):
    """Saved schedule edits with editor name and summary."""

    _COL_WHEN = 0
    _COL_WHO = 1
    _COL_SUMMARY = 2

    def __init__(
        self,
        *,
        project_root: Path,
        workspace_root: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.setObjectName("ScheduleHistoryDialog")
        self.setWindowTitle("Schedule history")
        self.setModal(False)
        self.setMinimumSize(620, 400)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(10)

        title = QLabel("Schedule history", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        root.addWidget(title)

        hint = QLabel(
            "Each row is a save to schedule.json — who changed what and when.",
            self,
        )
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        hint.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        root.addWidget(hint)

        self._table = QTableWidget(self)
        self._table.setObjectName("ScheduleHistoryTable")
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["When", "Edited by", "Summary"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setFont(monos_font("Inter", 10))
        hdr = self._table.horizontalHeader()
        hdr.setDefaultAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(self._COL_WHEN, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_WHO, QHeaderView.ResizeMode.Interactive)
        hdr.setMinimumSectionSize(96)
        hdr.setSectionResizeMode(self._COL_SUMMARY, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self._table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        refresh_btn = QPushButton("Refresh", self)
        refresh_btn.setObjectName("DialogSecondaryButton")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        icon = lucide_icon("refresh-cw", size=14, color_hex=MONOS_COLORS["text_label"])
        if not icon.isNull():
            refresh_btn.setIcon(icon)
        refresh_btn.clicked.connect(self._reload)
        footer.addWidget(refresh_btn)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogDestructiveButton")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._reload()

    def _reload(self) -> None:
        entries = read_schedule_history(self._project_root, limit=100)
        self._table.setRowCount(len(entries))
        align_center = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        mono = monos_font("JetBrains Mono", 10)
        body = monos_font("Inter", 10, QFont.Weight.Medium)

        for row, entry in enumerate(entries):
            self._table.setRowHeight(row, _ROW_H)

            when = QTableWidgetItem(_format_saved_at(entry.at))
            when.setFont(mono)
            when.setTextAlignment(align_center)
            self._table.setItem(row, self._COL_WHEN, when)

            self._table.setCellWidget(
                row,
                self._COL_WHO,
                _ScheduleHistoryAuthorCell(
                    workspace_root=self._workspace_root,
                    entry=entry,
                    parent=self._table,
                ),
            )

            summary = QTableWidgetItem(entry.summary)
            summary.setFont(body)
            summary.setTextAlignment(align_center)
            summary.setToolTip(entry.summary)
            self._table.setItem(row, self._COL_SUMMARY, summary)

        self._table.setColumnWidth(
            self._COL_WHO,
            _edited_by_column_width(tuple(entries)),
        )
