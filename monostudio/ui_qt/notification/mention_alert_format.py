"""Rich/plain copy for @mention user notifications."""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from monostudio.core.notification_copy import copy_item_fallback, copy_someone, pick_copy
from monostudio.ui_qt.notification.store import NotificationEntry, UserAlertPayload
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from PySide6.QtGui import QFont

_MENTION_SPLIT = " mentioned you in "
_LEGACY_DEPT_RE = re.compile(r"^(.+?) mentioned you in (.+?)(?: · (.+))?$")


def _esc(text: str) -> str:
    return html.escape((text or "").strip(), quote=True)


def department_display_label(department_id: str, department_label: str = "") -> str:
    label = (department_label or "").strip()
    if label:
        return label
    did = (department_id or "").strip()
    if not did:
        return ""
    return did.replace("_", " ").title()


def aggregated_mention_popup_message(senders: list[str], *, vietnamese: bool | None = None) -> str:
    """
    Short popup copy when multiple @mentions arrive together.
    +N = additional mentions (2 total → +1). Same person only: no "and others".
    """
    names = [(s or "").strip() or copy_someone(vietnamese=vietnamese) for s in senders]
    n = len(names)
    if n == 0:
        return pick_copy("Mention mới", "New mentions", vietnamese=vietnamese)
    if n == 1:
        return pick_copy(
            f"{names[0]} đã nhắc bạn",
            f"{names[0]} mentioned you",
            vietnamese=vietnamese,
        )

    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)

    extra = n - 1
    if len(unique) == 1:
        return pick_copy(
            f"{unique[0]} đã nhắc bạn +{extra}",
            f"{unique[0]} mentioned you +{extra}",
            vietnamese=vietnamese,
        )

    return pick_copy(
        f"{unique[0]} và người khác đã nhắc bạn +{extra}",
        f"{unique[0]} and others mentioned you +{extra}",
        vietnamese=vietnamese,
    )


def mention_alert_plain_message(
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
        f"{sender} đã nhắc bạn trong {asset}",
        f"{sender} mentioned you in {asset}",
        vietnamese=vietnamese,
    )
    if dept:
        msg += f" · {dept}"
    return msg


def mention_alert_rich_html(
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
            f'đã nhắc <span style="color:{accent};font-weight:600">bạn</span> trong {asset_html}'
        ),
        (
            f'<span style="color:{body};font-weight:600">{sender}</span> '
            f'mentioned <span style="color:{accent};font-weight:600">you</span> in {asset_html}'
        ),
        vietnamese=vietnamese,
    )


def _parse_legacy_message(message: str) -> tuple[str, str, str] | None:
    text = (message or "").strip()
    if not text:
        return None
    m = _LEGACY_DEPT_RE.match(text)
    if m:
        return (m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip())
    if _MENTION_SPLIT in text:
        sender, rest = text.split(_MENTION_SPLIT, 1)
        asset, _, dept = rest.partition(" · ")
        return (sender.strip(), asset.strip(), dept.strip())
    return None


def _fields_from_entry(entry: NotificationEntry) -> tuple[str, str, str, str] | None:
    if entry.kind != "user":
        return None
    p = entry.payload
    if isinstance(p, dict):
        p = UserAlertPayload.from_dict(p)
    if p.mention_inbox_id or p.from_name or p.item_display:
        from_name = (p.from_name or "").strip()
        item_display = (p.item_display or "").strip()
        dept_label = (p.department_label or "").strip()
        if not from_name or not item_display:
            legacy = _parse_legacy_message(entry.message)
            if legacy:
                from_name = from_name or legacy[0]
                item_display = item_display or legacy[1]
                dept_label = dept_label or legacy[2]
        return (
            from_name or "Someone",
            item_display or "an item",
            p.department,
            dept_label,
        )
    legacy = _parse_legacy_message(entry.message)
    if legacy:
        return (legacy[0], legacy[1], "", legacy[2])
    return None


def mention_alert_html_for_entry(entry: NotificationEntry) -> str | None:
    fields = _fields_from_entry(entry)
    if fields is None:
        return None
    from_name, item_display, dept_id, dept_label = fields
    return mention_alert_rich_html(
        from_name=from_name,
        item_display=item_display,
        department_id=dept_id,
        department_label=dept_label,
    )


def apply_notification_message_label(label: QLabel, entry: NotificationEntry) -> None:
    from monostudio.ui_qt.notification.assign_alert_format import (
        apply_assign_notification_message_label,
    )

    p = entry.payload
    if isinstance(p, dict):
        p = UserAlertPayload.from_dict(p)
    assign_ids = [i for i in (p.assign_inbox_ids or ()) if (i or "").strip()]
    if (
        (p.assign_inbox_id or "").strip() or len(assign_ids) > 1
    ) and apply_assign_notification_message_label(label, entry):
        return
    rich = mention_alert_html_for_entry(entry)
    label.setFont(monos_font("Inter", 15, QFont.Weight.Normal))
    if rich:
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(rich)
        label.setStyleSheet("color: #d4d4d8; background: transparent; border: none;")
        label.setWordWrap(True)
        return
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(entry.message)
    label.setStyleSheet(
        f"color: {MONOS_COLORS['text_primary']}; background: transparent; border: none;"
    )
    label.setWordWrap(True)
