"""Rich/plain copy for schedule assign user notifications."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel

from monostudio.core.notification_copy import copy_item_fallback, copy_someone, pick_copy
from monostudio.ui_qt.notification.mention_alert_format import department_display_label
from monostudio.ui_qt.notification.store import NotificationEntry, UserAlertPayload
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


def _esc(text: str) -> str:
    return html.escape((text or "").strip(), quote=True)


def assign_alert_bulk_plain_message(
    *,
    from_name: str,
    count: int,
    vietnamese: bool | None = None,
) -> str:
    sender = (from_name or "").strip() or copy_someone(vietnamese=vietnamese)
    n = max(1, int(count))
    return pick_copy(
        f"{sender} đã giao {n} công việc cho bạn",
        f"{sender} assigned {n} tasks to you",
        vietnamese=vietnamese,
    )


def assign_alert_bulk_rich_html(
    *,
    from_name: str,
    count: int,
    vietnamese: bool | None = None,
) -> str:
    sender = _esc(from_name or copy_someone(vietnamese=vietnamese))
    n = max(1, int(count))
    accent = MONOS_COLORS.get("text_primary_highlight", "#60a5fa")
    body = MONOS_COLORS.get("text_primary", "#d4d4d8")
    count_html = f'<span style="color:{body};font-weight:600">{n}</span>'
    return pick_copy(
        (
            f'<span style="color:{body};font-weight:600">{sender}</span> '
            f'đã giao {count_html} công việc cho '
            f'<span style="color:{accent};font-weight:600">bạn</span>'
        ),
        (
            f'<span style="color:{body};font-weight:600">{sender}</span> '
            f'assigned {count_html} tasks to '
            f'<span style="color:{accent};font-weight:600">you</span>'
        ),
        vietnamese=vietnamese,
    )


def assign_alert_plain_message(
    *,
    from_name: str,
    item_display: str,
    department_id: str = "",
    department_label: str = "",
    vietnamese: bool | None = None,
) -> str:
    sender = (from_name or "").strip() or copy_someone(vietnamese=vietnamese)
    asset = (item_display or "").strip() or copy_item_fallback(vietnamese=vietnamese)
    dept = department_display_label(department_id, department_label)
    msg = pick_copy(
        f"{sender} đã giao {asset} cho bạn",
        f"{sender} assigned {asset} to you",
        vietnamese=vietnamese,
    )
    if dept:
        msg += f" · {dept}"
    return msg


def assign_alert_rich_html(
    *,
    from_name: str,
    item_display: str,
    department_id: str = "",
    department_label: str = "",
    vietnamese: bool | None = None,
) -> str:
    sender = _esc(from_name or copy_someone(vietnamese=vietnamese))
    asset = _esc(item_display or copy_item_fallback(vietnamese=vietnamese))
    dept = _esc(department_display_label(department_id, department_label))
    accent = MONOS_COLORS.get("text_primary_highlight", "#60a5fa")
    body = MONOS_COLORS.get("text_primary", "#d4d4d8")
    meta = MONOS_COLORS.get("text_meta", "#a1a1aa")
    asset_html = f'<span style="color:{body};font-weight:600">{asset}</span>'
    if dept:
        asset_html += f' <span style="color:{meta};font-weight:500">· {dept}</span>'
    return pick_copy(
        (
            f'<span style="color:{body};font-weight:600">{sender}</span> '
            f'đã giao {asset_html} cho <span style="color:{accent};font-weight:600">bạn</span>'
        ),
        (
            f'<span style="color:{body};font-weight:600">{sender}</span> '
            f'assigned {asset_html} to <span style="color:{accent};font-weight:600">you</span>'
        ),
        vietnamese=vietnamese,
    )


def assign_bulk_count_from_entry(entry: NotificationEntry) -> int:
    if entry.kind != "user":
        return 0
    p = entry.payload
    if isinstance(p, dict):
        p = UserAlertPayload.from_dict(p)
    ids = [i for i in (p.assign_inbox_ids or ()) if (i or "").strip()]
    if len(ids) > 1:
        return len(ids)
    return 0


def assign_fields_from_entry(entry: NotificationEntry) -> tuple[str, str, str, str] | None:
    if entry.kind != "user":
        return None
    p = entry.payload
    if isinstance(p, dict):
        p = UserAlertPayload.from_dict(p)
    bulk_n = assign_bulk_count_from_entry(entry)
    if bulk_n > 1:
        return (
            (p.from_name or "").strip() or "Someone",
            str(bulk_n),
            "",
            "",
        )
    if not (p.assign_inbox_id or (p.from_name and p.item_display)):
        return None
    return (
        (p.from_name or "").strip() or "Someone",
        (p.item_display or "").strip() or "an item",
        p.department,
        (p.department_label or "").strip(),
    )


def assign_alert_html_for_entry(entry: NotificationEntry) -> str | None:
    bulk_n = assign_bulk_count_from_entry(entry)
    if bulk_n > 1:
        p = entry.payload
        if isinstance(p, dict):
            p = UserAlertPayload.from_dict(p)
        return assign_alert_bulk_rich_html(
            from_name=(p.from_name or "").strip() or "Someone",
            count=bulk_n,
        )
    fields = assign_fields_from_entry(entry)
    if fields is None:
        return None
    from_name, item_display, dept_id, dept_label = fields
    return assign_alert_rich_html(
        from_name=from_name,
        item_display=item_display,
        department_id=dept_id,
        department_label=dept_label,
    )


def apply_assign_notification_message_label(label: QLabel, entry: NotificationEntry) -> bool:
    rich = assign_alert_html_for_entry(entry)
    if rich is None:
        return False
    label.setFont(monos_font("Inter", 15, QFont.Weight.Normal))
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(rich)
    label.setStyleSheet("color: #d4d4d8; background: transparent; border: none;")
    label.setWordWrap(True)
    return True
