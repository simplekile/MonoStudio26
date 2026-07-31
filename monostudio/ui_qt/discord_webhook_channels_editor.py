"""Settings UI — multiple Discord webhook channels with per-channel event toggles."""

from __future__ import annotations

import uuid
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.integrations_config import is_valid_discord_webhook_url, mask_webhook_url
from monostudio.ui_qt.settings_section_widgets import (
    add_settings_field_row,
    add_settings_subsection_title,
    style_settings_line_edit,
)


def _new_webhook_id() -> str:
    return f"wh_{uuid.uuid4().hex[:6]}"


def _default_events() -> dict[str, bool]:
    return {
        "mention": True,
        "note_done": False,
        "inbox_received": False,
        "inbox_distributed": False,
        "outbox_received": False,
        "schedule_due": False,
        "schedule_assigned": False,
        "fusion_render_finished": False,
    }


class DiscordWebhookChannelRow(QFrame):
    """One Discord channel: label, URL, event checkboxes, test/remove."""

    removed = Signal(object)
    test_requested = Signal(object)

    def __init__(self, *, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsWebhookChannelCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._webhook_id = _new_webhook_id()
        self._stored_url = ""
        self._url_editing = False
        self._index = index

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QWidget(self)
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(0, 0, 0, 0)
        header_l.setSpacing(8)
        self._title_label = QLabel(self)
        self._title_label.setObjectName("SettingsFieldLabel")
        header_l.addWidget(self._title_label, 1)
        self._remove_btn = QPushButton("Remove", header)
        self._remove_btn.setObjectName("DialogSecondaryButton")
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.clicked.connect(lambda: self.removed.emit(self))
        header_l.addWidget(self._remove_btn, 0)
        layout.addWidget(header)

        self._label_field = QLineEdit(self)
        self._label_field.setPlaceholderText("#pipeline-general")
        style_settings_line_edit(self._label_field, min_width=200)
        add_settings_field_row(layout, "Channel label", self._label_field)

        self._url_field = QLineEdit(self)
        self._url_field.setProperty("mono", True)
        style_settings_line_edit(self._url_field, min_width=280)
        self._url_replace_btn = QPushButton("Replace…", self)
        self._url_replace_btn.setObjectName("SettingsInlineActionButton")
        self._url_replace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._url_replace_btn.clicked.connect(self._on_replace_url)
        url_row = QWidget(self)
        url_row_l = QHBoxLayout(url_row)
        url_row_l.setContentsMargins(0, 0, 0, 0)
        url_row_l.setSpacing(8)
        url_row_l.addWidget(self._url_field, 1)
        url_row_l.addWidget(self._url_replace_btn, 0)
        add_settings_field_row(layout, "Webhook URL", url_row)

        add_settings_subsection_title(layout, "Events")
        self._mention_cb = QCheckBox("@mentions in notes", self)
        self._mention_cb.setChecked(True)
        layout.addWidget(self._mention_cb)
        self._note_done_cb = QCheckBox("Note marked done", self)
        layout.addWidget(self._note_done_cb)
        self._inbox_cb = QCheckBox("Inbox & Outbox (drop & distribute)", self)
        layout.addWidget(self._inbox_cb)
        self._schedule_cb = QCheckBox("Schedule due reminders (daily)", self)
        layout.addWidget(self._schedule_cb)
        self._schedule_assigned_cb = QCheckBox("Schedule assignments", self)
        layout.addWidget(self._schedule_assigned_cb)
        self._fusion_render_cb = QCheckBox("Fusion render finished", self)
        layout.addWidget(self._fusion_render_cb)

        test_row = QWidget(self)
        test_row_l = QHBoxLayout(test_row)
        test_row_l.setContentsMargins(0, 2, 0, 0)
        self._test_btn = QPushButton("Send test message", test_row)
        self._test_btn.setObjectName("SettingsInlineActionButton")
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.clicked.connect(lambda: self.test_requested.emit(self))
        test_row_l.addWidget(self._test_btn, 0)
        test_row_l.addStretch(1)
        layout.addWidget(test_row)

        self._label_field.textChanged.connect(self._refresh_title)
        self._refresh_title()
        self._refresh_url_field()

    def set_index(self, index: int) -> None:
        self._index = index
        self._refresh_title()

    def set_removable(self, removable: bool) -> None:
        self._remove_btn.setVisible(removable)

    def _refresh_title(self) -> None:
        label = (self._label_field.text() or "").strip()
        if label:
            self._title_label.setText(label)
        else:
            self._title_label.setText(f"Channel {self._index + 1}")

    def _refresh_url_field(self) -> None:
        if self._url_editing:
            self._url_field.setReadOnly(False)
            self._url_field.setPlaceholderText("https://discord.com/api/webhooks/…")
            return
        if self._stored_url:
            self._url_field.setText(mask_webhook_url(self._stored_url))
            self._url_field.setReadOnly(True)
            self._url_field.setPlaceholderText("")
        else:
            self._url_field.clear()
            self._url_field.setReadOnly(False)
            self._url_field.setPlaceholderText("https://discord.com/api/webhooks/…")

    def _on_replace_url(self) -> None:
        self._url_editing = True
        self._url_field.setReadOnly(False)
        self._url_field.clear()
        self._url_field.setPlaceholderText("https://discord.com/api/webhooks/…")
        self._url_field.setFocus()

    def load_webhook(self, webhook: dict[str, Any] | None) -> None:
        wh = webhook if isinstance(webhook, dict) else {}
        self._webhook_id = str(wh.get("id") or _new_webhook_id())
        self._stored_url = str(wh.get("url") or "").strip()
        self._url_editing = False
        self._label_field.setText(str(wh.get("label") or "").strip())
        events = wh.get("events") if isinstance(wh.get("events"), dict) else {}
        defaults = _default_events()
        self._mention_cb.setChecked(bool(events.get("mention", defaults["mention"])))
        self._note_done_cb.setChecked(bool(events.get("note_done", defaults["note_done"])))
        inbox_on = bool(
            events.get("inbox_received")
            or events.get("inbox_distributed")
            or events.get("outbox_received")
        )
        self._inbox_cb.setChecked(inbox_on)
        self._schedule_cb.setChecked(bool(events.get("schedule_due", defaults["schedule_due"])))
        self._schedule_assigned_cb.setChecked(
            bool(events.get("schedule_assigned", defaults["schedule_assigned"]))
        )
        self._fusion_render_cb.setChecked(
            bool(events.get("fusion_render_finished", defaults["fusion_render_finished"]))
        )
        self._refresh_title()
        self._refresh_url_field()

    def clear_draft(self) -> None:
        self.load_webhook(None)

    def effective_url(self) -> str:
        if self._url_editing:
            candidate = (self._url_field.text() or "").strip()
            if is_valid_discord_webhook_url(candidate):
                return candidate
        if self._stored_url:
            return self._stored_url
        candidate = (self._url_field.text() or "").strip()
        if is_valid_discord_webhook_url(candidate):
            return candidate
        return ""

    def to_webhook_dict(self) -> dict[str, Any] | None:
        url = self.effective_url()
        if not is_valid_discord_webhook_url(url):
            return None
        inbox_on = self._inbox_cb.isChecked()
        return {
            "id": self._webhook_id,
            "label": (self._label_field.text() or "").strip(),
            "url": url,
            "events": {
                "mention": self._mention_cb.isChecked(),
                "note_done": self._note_done_cb.isChecked(),
                "inbox_received": inbox_on,
                "inbox_distributed": inbox_on,
                "outbox_received": inbox_on,
                "schedule_due": self._schedule_cb.isChecked(),
                "schedule_assigned": self._schedule_assigned_cb.isChecked(),
                "fusion_render_finished": self._fusion_render_cb.isChecked(),
            },
        }

    def set_admin_enabled(self, enabled: bool) -> None:
        for w in (
            self._label_field,
            self._url_field,
            self._url_replace_btn,
            self._mention_cb,
            self._note_done_cb,
            self._inbox_cb,
            self._schedule_cb,
            self._schedule_assigned_cb,
            self._fusion_render_cb,
            self._test_btn,
            self._remove_btn,
        ):
            w.setEnabled(enabled)


class DiscordWebhookChannelsEditor(QWidget):
    """List of Discord webhook channel rows."""

    channel_test_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[DiscordWebhookChannelRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._rows_host = QWidget(self)
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(10)
        layout.addWidget(self._rows_host)

        self._add_btn = QPushButton("Add channel…", self)
        self._add_btn.setObjectName("SettingsInlineActionButton")
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._on_add_channel)
        layout.addWidget(self._add_btn, 0, Qt.AlignmentFlag.AlignLeft)

    def _on_add_channel(self) -> None:
        self._append_row(None)

    def _append_row(self, webhook: dict[str, Any] | None) -> DiscordWebhookChannelRow:
        row = DiscordWebhookChannelRow(index=len(self._rows), parent=self._rows_host)
        row.load_webhook(webhook)
        row.removed.connect(self._on_row_removed)
        row.test_requested.connect(self.channel_test_requested.emit)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        self._sync_row_indices()
        return row

    def _on_row_removed(self, row: DiscordWebhookChannelRow) -> None:
        if row not in self._rows:
            return
        if len(self._rows) <= 1:
            row.clear_draft()
            return
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._sync_row_indices()

    def _sync_row_indices(self) -> None:
        removable = len(self._rows) > 1
        for i, row in enumerate(self._rows):
            row.set_index(i)
            row.set_removable(removable)

    def clear_rows(self) -> None:
        while self._rows:
            row = self._rows.pop()
            row.setParent(None)
            row.deleteLater()

    def load_webhooks(self, webhooks: list[dict[str, Any]] | None) -> None:
        self.clear_rows()
        items = [w for w in (webhooks or []) if isinstance(w, dict)]
        if not items:
            self._append_row(None)
            return
        for wh in items:
            self._append_row(wh)

    def to_webhook_dicts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in self._rows:
            wh = row.to_webhook_dict()
            if wh is not None:
                out.append(wh)
        return out

    def first_valid_url(self) -> str:
        for row in self._rows:
            url = row.effective_url()
            if url:
                return url
        return ""

    def set_admin_enabled(self, enabled: bool) -> None:
        self._add_btn.setEnabled(enabled)
        for row in self._rows:
            row.set_admin_enabled(enabled)

    def iter_rows(self) -> list[DiscordWebhookChannelRow]:
        return list(self._rows)
