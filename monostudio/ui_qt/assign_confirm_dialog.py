"""Dialog for assignee to confirm a schedule assignment."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QVBoxLayout

from monostudio.core.notification_copy import pick_copy
from monostudio.ui_qt.style import MonosDialog, monos_font


class AssignConfirmDialog(MonosDialog):
    """Ask assignee to confirm they accept the scheduled work."""

    def __init__(
        self,
        parent=None,
        *,
        from_name: str = "",
        item_display: str = "",
        department_label: str = "",
        schedule_label: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AssignConfirmDialog")
        self.setWindowTitle(pick_copy("Xác nhận giao việc", "Confirm assignment"))
        self._confirmed = False

        sender = (from_name or "").strip() or pick_copy("Ai đó", "Someone")
        item = (item_display or "").strip() or pick_copy("một mục", "an item")
        dept = (department_label or "").strip()
        when = (schedule_label or "").strip()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel(pick_copy("Xác nhận nhận việc?", "Accept this assignment?"), self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 14, QFont.Weight.DemiBold))
        root.addWidget(title)

        lines = [
            pick_copy(
                f"{sender} giao {item} cho bạn.",
                f"{sender} assigned {item} to you.",
            ),
        ]
        if dept:
            lines.append(pick_copy(f"Phòng ban: {dept}", f"Department: {dept}"))
        if when:
            lines.append(pick_copy(f"Lịch: {when}", f"Schedule: {when}"))

        body = QLabel("\n".join(lines), self)
        body.setWordWrap(True)
        body.setObjectName("DialogHint")
        root.addWidget(body)

        hint = QLabel(
            pick_copy(
                "Nhấn Xác nhận để đồng ý nhận việc. Trạng thái sẽ cập nhật trên Discord.",
                "Press Confirm to accept. Status will update on Discord.",
            ),
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        buttons = QDialogButtonBox(self)
        cancel = buttons.addButton(
            pick_copy("Để sau", "Later"),
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        confirm = buttons.addButton(
            pick_copy("Xác nhận", "Confirm"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        confirm.setObjectName("DialogPrimaryButton")
        cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.setMinimumWidth(420)

    def _on_accept(self) -> None:
        self._confirmed = True
        self.accept()

    @staticmethod
    def ask(
        parent,
        *,
        from_name: str = "",
        item_display: str = "",
        department_label: str = "",
        schedule_label: str = "",
    ) -> bool:
        dlg = AssignConfirmDialog(
            parent,
            from_name=from_name,
            item_display=item_display,
            department_label=department_label,
            schedule_label=schedule_label,
        )
        dlg.exec()
        return dlg._confirmed
