"""Settings dialog — send sample Discord webhook notifications for every event type."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.discord_webhook_test import (
    list_discord_test_scenarios,
    scenario_description,
    scenario_label,
    send_all_discord_test_scenarios,
    send_discord_test_scenario,
)
from monostudio.core.notification_preferences import read_notification_vietnamese
from monostudio.ui_qt.style import MonosDialog, monos_font


class DiscordNotificationTestDialog(MonosDialog):
    def __init__(
        self,
        workspace_root: Path | str | None,
        *,
        url_resolver: Callable[[], str],
        user_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace_root = workspace_root
        self._url_resolver = url_resolver
        self._user_name = (user_name or "").strip()
        self._vietnamese = read_notification_vietnamese()
        self._row_status: dict[str, QLabel] = {}

        self.setWindowTitle("Test Discord notifications")
        self.setModal(True)
        self.setMinimumSize(520, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hint = QLabel(
            "Send sample messages to your webhook channel. "
            "Event checkboxes above are ignored — this bypasses the normal dispatch queue. "
            "Sending all may hit Discord rate limits; wait a few seconds between batches.",
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_host = QWidget(scroll)
        scroll_l = QVBoxLayout(scroll_host)
        scroll_l.setContentsMargins(0, 0, 0, 0)
        scroll_l.setSpacing(6)

        for scenario in list_discord_test_scenarios():
            scroll_l.addWidget(self._make_scenario_row(scenario.id))

        scroll_l.addStretch(1)
        scroll.setWidget(scroll_host)
        root.addWidget(scroll, 1)

        self._summary = QLabel("", self)
        self._summary.setObjectName("DialogHint")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        buttons = QDialogButtonBox(self)
        self._send_all_btn = QPushButton("Send all", self)
        self._send_all_btn.setObjectName("DialogPrimaryButton")
        self._send_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_all_btn.clicked.connect(self._on_send_all)
        buttons.addButton(self._send_all_btn, QDialogButtonBox.ButtonRole.ActionRole)

        close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setObjectName("DialogSecondaryButton")
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _make_scenario_row(self, scenario_id: str) -> QWidget:
        scenario = next(s for s in list_discord_test_scenarios() if s.id == scenario_id)
        row = QWidget(self)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 4, 0, 4)
        row_l.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title = QLabel(scenario_label(scenario, vietnamese=self._vietnamese), row)
        title.setFont(monos_font("Inter", 13, weight=QFont.Weight.DemiBold))
        desc = QLabel(scenario_description(scenario, vietnamese=self._vietnamese), row)
        desc.setObjectName("DialogHint")
        desc.setWordWrap(True)
        text_col.addWidget(title)
        text_col.addWidget(desc)
        row_l.addLayout(text_col, 1)

        send_btn = QPushButton("Send", row)
        send_btn.setObjectName("SettingsInlineActionButton")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setFixedWidth(72)
        send_btn.clicked.connect(lambda _checked=False, sid=scenario.id: self._on_send_one(sid))
        row_l.addWidget(send_btn, 0, Qt.AlignmentFlag.AlignTop)

        status = QLabel("", row)
        status.setObjectName("DialogHint")
        status.setMinimumWidth(80)
        status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_l.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
        self._row_status[scenario.id] = status

        return row

    def _set_row_status(self, scenario_id: str, ok: bool, err: str = "") -> None:
        label = self._row_status.get(scenario_id)
        if label is None:
            return
        if ok:
            label.setText("✓ Sent")
            label.setStyleSheet("color: #10b981;")
        else:
            short = (err or "Failed")[:48]
            label.setText(short)
            label.setStyleSheet("color: #ef4444;")

    def _on_send_one(self, scenario_id: str) -> None:
        ok, err = send_discord_test_scenario(
            self._workspace_root,
            scenario_id,
            url_override=self._url_resolver(),
            user_name=self._user_name,
        )
        self._set_row_status(scenario_id, ok, err)
        if not ok:
            QMessageBox.warning(self, "Discord", err or "Could not send test notification.")

    def _on_send_all(self) -> None:
        self._send_all_btn.setEnabled(False)
        try:
            ok_count, fail_count, errors = send_all_discord_test_scenarios(
                self._workspace_root,
                url_override=self._url_resolver(),
                user_name=self._user_name,
                on_progress=self._set_row_status,
            )
        finally:
            self._send_all_btn.setEnabled(True)

        total = ok_count + fail_count
        if fail_count == 0:
            self._summary.setText(f"Sent {ok_count}/{total} test notifications.")
            self._summary.setStyleSheet("color: #10b981;")
        else:
            detail = "; ".join(f"{sid}: {msg}" for sid, msg in errors[:3])
            if len(errors) > 3:
                detail += f" (+{len(errors) - 3} more)"
            self._summary.setText(f"Sent {ok_count}/{total}. Failed: {detail}")
            self._summary.setStyleSheet("color: #ef4444;")
