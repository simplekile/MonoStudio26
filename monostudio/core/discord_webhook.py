"""Discord incoming webhook delivery (background queue, no Qt)."""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.integrations_config import (
    discord_defaults,
    is_valid_discord_webhook_url,
    load_integrations,
    webhook_urls_for_event,
)
from monostudio.core.notification_copy import (
    copy_file_word,
    copy_item_fallback,
    copy_more_suffix,
    copy_project_fallback,
    copy_someone,
    copy_schedule_due_headline,
    copy_source_label,
    copy_team_label,
    pick_copy,
)
from monostudio.core.notification_preferences import (
    read_discord_disabled_locally,
    read_notification_vietnamese,
)

_log = logging.getLogger("monostudio.discord_webhook")

_DEDUPE_TTL_S = 60.0
_MAX_PER_MINUTE = 25
_RETRY_DELAY_S = 2.0
_FAILED_POST_MAX_ATTEMPTS = 8
_FAILED_POST_BASE_DELAY_S = 30.0

_dedupe: dict[str, float] = {}
_rate: dict[str, deque[float]] = {}
_task_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()
_failed_retry_timers: dict[str, threading.Timer] = {}
_failed_retry_lock = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _discord_embed_timestamp(raw: str | None) -> str:
    """Normalize payload timestamp for Discord embed (ISO 8601 UTC)."""
    s = (raw or "").strip()
    if not s:
        return _utc_now_iso()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return _utc_now_iso()
    # Naive inbox_meta timestamps are local; Z/offset values are absolute UTC.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _department_label(department_id: str = "", department_label: str = "") -> str:
    label = (department_label or "").strip()
    if label:
        return label
    did = (department_id or "").strip()
    if not did:
        return ""
    return did.replace("_", " ").title()


def _hex_color_to_int(color_hex: str, *, fallback: int = 0x3B82F6) -> int:
    raw = (color_hex or "").strip().lstrip("#")
    if len(raw) not in (3, 6):
        return fallback
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    try:
        return int(raw, 16)
    except ValueError:
        return fallback


def _truncate(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)].rstrip() + "…"


def _discord_md(text: str) -> str:
    """Escape user text for Discord markdown (minimal)."""
    t = (text or "").strip()
    return t.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def _target_display_names(
    workspace_root: Path | str | None,
    to_user_ids: object,
    *,
    fallback_names: tuple[str, ...] = (),
) -> list[str]:
    ids: list[str] = []
    if isinstance(to_user_ids, (list, tuple)):
        ids = [str(u).strip() for u in to_user_ids if str(u).strip()]
    names: list[str] = []
    if workspace_root is not None:
        from monostudio.core.user_identity import get_user

        root = Path(workspace_root)
        for uid in ids:
            user = get_user(root, uid)
            names.append(user.name if user is not None else uid)
    if not names and fallback_names:
        names = [n.strip() for n in fallback_names if (n or "").strip()]
    return names


def _discord_ping_ids(
    workspace_root: Path | str | None,
    to_user_ids: object,
) -> list[str]:
    if workspace_root is None:
        return []
    from monostudio.core.user_identity import get_user, normalize_discord_user_id

    root = Path(workspace_root)
    ids: list[str] = []
    if not isinstance(to_user_ids, (list, tuple)):
        return ids
    for uid in to_user_ids:
        user = get_user(root, str(uid).strip())
        if user is None:
            continue
        did = normalize_discord_user_id(user.discord_user_id)
        if did and did not in ids:
            ids.append(did)
    return ids


def _sender_embed_color(
    workspace_root: Path | str | None,
    from_user_id: str,
    color_hex: str = "",
) -> int:
    explicit = (color_hex or "").strip()
    if explicit:
        return _hex_color_to_int(explicit)
    uid = (from_user_id or "").strip()
    if workspace_root is not None and uid:
        from monostudio.core.user_identity import get_user

        user = get_user(Path(workspace_root), uid)
        if user is not None and user.color_hex:
            return _hex_color_to_int(user.color_hex)
    return _hex_color_to_int("#3b82f6")


def _mention_snippet_block(snippet: str) -> str:
    text = _truncate(snippet, 480)
    if not text:
        return ""
    return f"> {_discord_md(text)}"


def _embed_snippet(snippet: str, *, target_names: tuple[str, ...] = ()) -> str:
    """Note quote for embed — skip when body is only @mention text."""
    s = (snippet or "").strip()
    if not s:
        return ""
    for name in target_names:
        n = (name or "").strip()
        if not n:
            continue
        if s == n or s.lower() == n.lower() or s.lower() == f"@{n.lower()}":
            return ""
    if s.startswith("@") and "\n" not in s and len(s) < 120:
        tail = s[1:].strip()
        if tail and not any(c in tail for c in ".!?"):
            if not target_names or any(tail.lower() == t.lower() for t in target_names if t):
                return ""
    return _mention_snippet_block(s)


def build_mention_body(
    payload: dict[str, Any],
    defaults: dict[str, str] | None = None,
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any]:
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    from_name = (payload.get("from_name") or "").strip() or copy_someone(vietnamese=vietnamese)
    from_uid = str(payload.get("from_user_id") or "").strip()
    item_display = (payload.get("item_display") or "").strip() or copy_item_fallback(vietnamese=vietnamese)
    snippet = str(payload.get("snippet") or "").strip()
    project_name = (payload.get("project_name") or "").strip() or copy_project_fallback(vietnamese=vietnamese)
    dept = _department_label(
        str(payload.get("department") or ""),
        str(payload.get("department_label") or ""),
    )
    targets = _target_display_names(workspace_root, payload.get("to_user_ids"))
    targets_label = ", ".join(_discord_md(n) for n in targets) if targets else copy_team_label(vietnamese=vietnamese)
    ping_ids = _discord_ping_ids(workspace_root, payload.get("to_user_ids"))

    description = _embed_snippet(snippet, target_names=tuple(targets))

    footer_parts = [_truncate(from_name, 64), f"`{_truncate(project_name, 120)}`"]
    if dept:
        footer_parts.insert(1, _truncate(dept, 64))

    embed: dict[str, Any] = {
        "title": _truncate(item_display, 256),
        "color": _sender_embed_color(
            workspace_root,
            from_uid,
            str(payload.get("color_hex") or ""),
        ),
        "footer": {"text": " · ".join(footer_parts)},
        "timestamp": _utc_now_iso(),
    }
    if description:
        embed["description"] = _truncate(description, 4096)

    if ping_ids:
        content = " ".join(f"<@{did}>" for did in ping_ids)
        allowed: dict[str, Any] = {"parse": [], "users": ping_ids}
    else:
        content = f"**{_discord_md(from_name)}** → **{targets_label}**"
        allowed = {"parse": []}

    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "content": content,
        "allowed_mentions": allowed,
        "embeds": [embed],
    }
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def _mask_url_for_log(url: str) -> str:
    u = (url or "").strip()
    if not u or "/" not in u:
        return "(invalid)"
    return u.rsplit("/", 1)[0] + "/••••"


def build_test_body(
    *,
    user_name: str = "",
    machine: str = "",
    defaults: dict[str, str] | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any]:
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    who = (user_name or "").strip() or copy_someone(vietnamese=vietnamese)
    host = (machine or "").strip() or socket.gethostname()
    content = pick_copy(
        f"**Kiểm tra webhook** từ `{_discord_md(host)}`",
        f"**Webhook test** from `{_discord_md(host)}`",
        vietnamese=vietnamese,
    )
    author = pick_copy("MONOS · Thử", "MONOS · Test", vietnamese=vietnamese)
    title = pick_copy("Kết nối OK", "Connection OK", vietnamese=vietnamese)
    description = pick_copy(
        f"Gửi bởi **{_discord_md(who)}**.\nTích hợp Discord sẵn sàng cho @mention.",
        f"Sent by **{_discord_md(who)}**.\nDiscord integration is ready for @mentions.",
        vietnamese=vietnamese,
    )
    footer = "MONOS Pipeline"
    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "content": content,
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "author": {"name": author},
                "title": title,
                "description": description,
                "color": _hex_color_to_int("#3b82f6"),
                "footer": {"text": footer},
                "timestamp": _utc_now_iso(),
            }
        ],
    }
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def build_note_done_body(
    payload: dict[str, Any],
    defaults: dict[str, str] | None = None,
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any]:
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    from_name = (payload.get("from_name") or "").strip() or copy_someone(vietnamese=vietnamese)
    item_display = (payload.get("item_display") or "").strip() or copy_item_fallback(vietnamese=vietnamese)
    snippet = str(payload.get("snippet") or "").strip()
    project_name = (payload.get("project_name") or "").strip() or copy_project_fallback(vietnamese=vietnamese)
    dept = _department_label(
        str(payload.get("department") or ""),
        str(payload.get("department_label") or ""),
    )

    content = pick_copy(
        f"**{_discord_md(from_name)}** đã đánh dấu hoàn thành một ghi chú trên **{_discord_md(item_display)}**",
        f"**{_discord_md(from_name)}** marked a note done on **{_discord_md(item_display)}**",
        vietnamese=vietnamese,
    )
    description = _embed_snippet(snippet)
    footer_parts = [_truncate(from_name, 64), f"`{_truncate(project_name, 120)}`"]
    if dept:
        footer_parts.insert(1, _truncate(dept, 64))

    embed: dict[str, Any] = {
        "title": _truncate(item_display, 256),
        "color": _hex_color_to_int("#10b981"),
        "footer": {"text": " · ".join(footer_parts)},
        "timestamp": _utc_now_iso(),
    }
    if description:
        embed["description"] = _truncate(description, 4096)

    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "content": content,
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def build_inbox_received_body(
    payload: dict[str, Any],
    defaults: dict[str, str] | None = None,
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any]:
    del workspace_root
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    actor = (payload.get("actor_name") or "").strip() or copy_someone(vietnamese=vietnamese)
    count = max(0, int(payload.get("count") or 0))
    project_name = (payload.get("project_name") or "").strip() or copy_project_fallback(vietnamese=vietnamese)
    source = (payload.get("source") or "client").strip().lower()
    source_label = copy_source_label(source, vietnamese=vietnamese)
    date_str = (payload.get("date_str") or "").strip()
    file_names = payload.get("file_names")
    names: list[str] = []
    if isinstance(file_names, list):
        for raw in file_names:
            n = str(raw or "").strip()
            if n and n not in names:
                names.append(n)

    file_word = copy_file_word(count, vietnamese=vietnamese)
    date_part = f" · `{_truncate(date_str, 32)}`" if date_str else ""
    content = pick_copy(
        f"**{_discord_md(actor)}** đã thêm **{count}** {file_word} vào Inbox · {source_label}{date_part}",
        f"**{_discord_md(actor)}** added **{count}** {file_word} to Inbox · {source_label}{date_part}",
        vietnamese=vietnamese,
    )
    footer = f"Inbox · `{_truncate(project_name, 120)}`"
    title = date_str or f"{count} {file_word}"

    lines: list[str] = []
    for name in names[:8]:
        lines.append(f"• `{_truncate(_discord_md(name), 120)}`")
    extra = len(names) - 8
    if extra > 0:
        lines.append(copy_more_suffix(extra, vietnamese=vietnamese))

    embed: dict[str, Any] = {
        "title": _truncate(title, 256),
        "color": _hex_color_to_int("#6366f1"),
        "footer": {"text": footer},
        "timestamp": _discord_embed_timestamp(str(payload.get("last_added_at") or "")),
    }
    if lines:
        embed["description"] = _truncate("\n".join(lines), 4096)

    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "content": content,
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def build_outbox_received_body(
    payload: dict[str, Any],
    defaults: dict[str, str] | None = None,
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any]:
    del workspace_root
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    actor = (payload.get("actor_name") or "").strip() or copy_someone(vietnamese=vietnamese)
    count = max(0, int(payload.get("count") or 0))
    project_name = (payload.get("project_name") or "").strip() or copy_project_fallback(vietnamese=vietnamese)
    source = (payload.get("source") or "client").strip().lower()
    source_label = copy_source_label(source, vietnamese=vietnamese)
    date_str = (payload.get("date_str") or "").strip()
    file_names = payload.get("file_names")
    names: list[str] = []
    if isinstance(file_names, list):
        for raw in file_names:
            n = str(raw or "").strip()
            if n and n not in names:
                names.append(n)

    file_word = copy_file_word(count, vietnamese=vietnamese)
    date_part = f" · `{_truncate(date_str, 32)}`" if date_str else ""
    content = pick_copy(
        f"**{_discord_md(actor)}** đã thêm **{count}** {file_word} vào Outbox · {source_label}{date_part}",
        f"**{_discord_md(actor)}** added **{count}** {file_word} to Outbox · {source_label}{date_part}",
        vietnamese=vietnamese,
    )
    footer = f"Outbox · `{_truncate(project_name, 120)}`"
    title = date_str or f"{count} {file_word}"

    lines: list[str] = []
    for name in names[:8]:
        lines.append(f"• `{_truncate(_discord_md(name), 120)}`")
    extra = len(names) - 8
    if extra > 0:
        lines.append(copy_more_suffix(extra, vietnamese=vietnamese))

    embed: dict[str, Any] = {
        "title": _truncate(title, 256),
        "color": _hex_color_to_int("#a855f7"),
        "footer": {"text": footer},
        "timestamp": _discord_embed_timestamp(str(payload.get("last_added_at") or "")),
    }
    if lines:
        embed["description"] = _truncate("\n".join(lines), 4096)

    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "content": content,
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def build_inbox_distributed_body(
    payload: dict[str, Any],
    defaults: dict[str, str] | None = None,
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any]:
    del workspace_root  # reserved for future actor color lookup
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    actor = (payload.get("actor_name") or "").strip() or copy_someone(vietnamese=vietnamese)
    count = max(0, int(payload.get("count") or 0))
    dest = (payload.get("dest_label") or payload.get("destination_label") or "").strip() or "pipeline"
    project_name = (payload.get("project_name") or "").strip() or copy_project_fallback(vietnamese=vietnamese)
    source = (payload.get("source") or "client").strip().lower()
    source_label = copy_source_label(source, vietnamese=vietnamese)
    entity_names = payload.get("entity_names")
    names: list[str] = []
    if isinstance(entity_names, list):
        seen: set[str] = set()
        for raw in entity_names:
            n = str(raw or "").strip()
            if not n or n in seen:
                continue
            seen.add(n)
            names.append(n)

    file_word = copy_file_word(count, vietnamese=vietnamese)
    content = f"**{_discord_md(actor)}** → **{_discord_md(dest)}** · {count} {file_word}"

    lines: list[str] = []
    for name in names[:8]:
        lines.append(f"• {_discord_md(name)}")
    extra = len(names) - 8
    if extra > 0:
        lines.append(copy_more_suffix(extra, vietnamese=vietnamese))

    embed: dict[str, Any] = {
        "title": _truncate(dest, 256),
        "color": _hex_color_to_int("#22c55e"),
        "footer": {"text": f"{source_label} · `{_truncate(project_name, 120)}`"},
        "timestamp": _utc_now_iso(),
    }
    if lines:
        embed["description"] = _truncate("\n".join(lines), 4096)

    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "content": content,
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def build_schedule_due_body(
    payload: dict[str, Any],
    defaults: dict[str, str] | None = None,
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any]:
    del workspace_root
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    project_name = (payload.get("project_name") or "").strip() or copy_project_fallback(vietnamese=vietnamese)
    items_raw = payload.get("items")
    items: list[dict[str, Any]] = [i for i in items_raw if isinstance(i, dict)] if isinstance(items_raw, list) else []
    overdue_count = int(payload.get("overdue_count") or sum(1 for i in items if i.get("overdue")))
    due_today_count = int(payload.get("due_today_count") or sum(1 for i in items if not i.get("overdue")))

    summary = copy_schedule_due_headline(
        overdue_count=overdue_count,
        due_today_count=due_today_count,
        vietnamese=vietnamese,
    )
    footer = pick_copy(
        "Lịch · hôm nay & quá hạn",
        "Schedule · due today & overdue",
        vietnamese=vietnamese,
    )

    lines: list[str] = []
    for item in items[:12]:
        name = (item.get("entity_name") or "?").strip()
        dept = (item.get("department_label") or item.get("department") or "").strip()
        due = (item.get("due") or "").strip()
        prefix = "⚠ " if item.get("overdue") else "• "
        line = f"{prefix}**{_discord_md(name)}**"
        if dept:
            line += f" · {_discord_md(dept)}"
        if due:
            line += f" · `{due}`"
        lines.append(line)
    extra = len(items) - 12
    if extra > 0:
        lines.append(copy_more_suffix(extra, vietnamese=vietnamese))

    embed: dict[str, Any] = {
        "title": _truncate(project_name, 256),
        "color": _hex_color_to_int("#f97316" if overdue_count else "#eab308"),
        "footer": {"text": footer},
        "timestamp": _utc_now_iso(),
    }
    if lines:
        embed["description"] = _truncate("\n".join(lines), 4096)

    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "content": summary,
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def format_schedule_dates(
    start: str,
    due: str,
    *,
    vietnamese: bool | None = None,
) -> str:
    start_s = (start or "").strip()
    due_s = (due or "").strip()
    if start_s and due_s and start_s != due_s:
        return pick_copy(
            f"{start_s} → {due_s}",
            f"{start_s} → {due_s}",
            vietnamese=vietnamese,
        )
    if due_s:
        return due_s
    if start_s:
        return start_s
    return pick_copy("Chưa đặt hạn", "No due date", vietnamese=vietnamese)


def _resolve_assign_inbox_id(payload: dict[str, Any]) -> str:
    ids = payload.get("assign_inbox_ids")
    if isinstance(ids, list) and ids:
        iid = str(ids[0] or "").strip()
        if iid:
            return iid
    return str(payload.get("assign_inbox_id") or "").strip()


def _assign_action_urls(inbox_id: str) -> tuple[str, str, str, str]:
    """Return (open_http, confirm_http, open_deep, confirm_deep) for assign inbox row."""
    iid = (inbox_id or "").strip()
    from monostudio.core.deep_link import build_assign_deep_link
    from monostudio.core.deep_link_server import active_deep_link_port, assign_http_url

    port = active_deep_link_port() or 39247
    open_http = assign_http_url(iid, action="open", port=port)
    confirm_http = assign_http_url(iid, action="confirm", port=port)
    open_deep = build_assign_deep_link(iid, action="open")
    confirm_deep = build_assign_deep_link(iid, action="confirm")
    return open_http, confirm_http, open_deep, confirm_deep


def _append_assign_action_links(
    *,
    content: str,
    embed: dict[str, Any],
    fields: list[dict[str, Any]],
    inbox_id: str,
    vietnamese: bool | None,
    include_components: bool,
) -> tuple[str, dict[str, Any], list[dict[str, Any]] | None]:
    """Add assign action links inside the embed (field + optional link buttons)."""
    iid = (inbox_id or "").strip()
    if not iid:
        return content, embed, None
    open_http, confirm_http, _, _ = _assign_action_urls(iid)
    fields = list(fields)
    fields.append(
        {
            "name": pick_copy("Thao tác", "Actions", vietnamese=vietnamese),
            "value": pick_copy(
                f"[Mở MONOS]({open_http}) · [Xác nhận]({confirm_http})",
                f"[Open MONOS]({open_http}) · [Confirm]({confirm_http})",
                vietnamese=vietnamese,
            ),
            "inline": False,
        }
    )
    embed["fields"] = fields
    components: list[dict[str, Any]] | None = None
    if include_components:
        components = [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": pick_copy("Mo MONOS", "Open MONOS", vietnamese=vietnamese)[:80],
                        "url": open_http,
                    },
                    {
                        "type": 2,
                        "style": 5,
                        "label": pick_copy("Xac nhan", "Confirm", vietnamese=vietnamese)[:80],
                        "url": confirm_http,
                    },
                ],
            }
        ]
    return content, embed, components


def build_schedule_assigned_body(
    payload: dict[str, Any],
    defaults: dict[str, str] | None = None,
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
    confirmed: bool = False,
    confirmer_name: str = "",
    include_components: bool = True,
) -> dict[str, Any]:
    d = defaults or {"username": "MONOS", "avatar_url": ""}
    from_name = (payload.get("from_name") or "").strip() or copy_someone(vietnamese=vietnamese)
    from_uid = str(payload.get("from_user_id") or "").strip()
    item_display = (payload.get("item_display") or "").strip() or copy_item_fallback(vietnamese=vietnamese)
    project_name = (payload.get("project_name") or "").strip() or copy_project_fallback(vietnamese=vietnamese)
    dept = _department_label(
        str(payload.get("department") or ""),
        str(payload.get("department_label") or ""),
    )
    due = (payload.get("due") or "").strip()
    start = (payload.get("start") or "").strip()
    dates_label = format_schedule_dates(start, due, vietnamese=vietnamese)
    ping_ids = _discord_ping_ids(workspace_root, payload.get("to_user_ids"))
    confirmer = (confirmer_name or "").strip()

    if confirmed and confirmer:
        status_value = pick_copy(
            f"✅ Đã xác nhận bởi **{_discord_md(confirmer)}**",
            f"✅ Confirmed by **{_discord_md(confirmer)}**",
            vietnamese=vietnamese,
        )
        embed_color = _hex_color_to_int("#10b981")
    else:
        status_value = pick_copy(
            "⏳ **Chờ xác nhận** — dùng **Thao tác** bên dưới",
            "⏳ **Pending** — use **Actions** below",
            vietnamese=vietnamese,
        )
        embed_color = _sender_embed_color(
            workspace_root,
            from_uid,
            str(payload.get("color_hex") or ""),
        )

    fields: list[dict[str, Any]] = []
    if dept:
        fields.append(
            {
                "name": pick_copy("Phòng ban", "Department", vietnamese=vietnamese),
                "value": _discord_md(dept),
                "inline": True,
            }
        )
    fields.append(
        {
            "name": pick_copy("Thời gian", "Schedule", vietnamese=vietnamese),
            "value": f"`{dates_label}`",
            "inline": True,
        }
    )
    fields.append(
        {
            "name": pick_copy("Dự án", "Project", vietnamese=vietnamese),
            "value": _discord_md(project_name),
            "inline": False,
        }
    )
    fields.append(
        {
            "name": pick_copy("Trạng thái", "Status", vietnamese=vietnamese),
            "value": status_value,
            "inline": False,
        }
    )

    if dept:
        summary = pick_copy(
            f"**{_discord_md(from_name)}** giao công việc **{_discord_md(dept)}** của **{_discord_md(item_display)}** cho bạn",
            f"**{_discord_md(from_name)}** assigned the **{_discord_md(dept)}** work for **{_discord_md(item_display)}** to you",
            vietnamese=vietnamese,
        )
    else:
        summary = pick_copy(
            f"**{_discord_md(from_name)}** giao công việc **{_discord_md(item_display)}** cho bạn",
            f"**{_discord_md(from_name)}** assigned **{_discord_md(item_display)}** to you",
            vietnamese=vietnamese,
        )

    embed: dict[str, Any] = {
        "author": {
            "name": pick_copy("MONOS · Giao việc", "MONOS · Assignment", vietnamese=vietnamese),
        },
        "title": pick_copy(
            f"Bạn được giao: {_discord_md(item_display)}",
            f"You were assigned: {_discord_md(item_display)}",
            vietnamese=vietnamese,
        ),
        "description": summary,
        "fields": fields,
        "color": embed_color,
        "footer": {"text": _truncate(from_name, 64)},
        "timestamp": _utc_now_iso(),
    }

    if ping_ids:
        content = " ".join(f"<@{did}>" for did in ping_ids)
        allowed: dict[str, Any] = {"parse": [], "users": ping_ids}
    else:
        content = ""
        allowed = {"parse": []}

    body: dict[str, Any] = {
        "username": d.get("username") or "MONOS",
        "allowed_mentions": allowed,
        "embeds": [embed],
    }
    if content:
        body["content"] = content
    inbox_id = _resolve_assign_inbox_id(payload)
    if not confirmed and inbox_id:
        content, embed, components = _append_assign_action_links(
            content=content,
            embed=embed,
            fields=fields,
            inbox_id=inbox_id,
            vietnamese=vietnamese,
            include_components=include_components,
        )
        body["embeds"] = [embed]
        if content:
            body["content"] = content
        elif "content" in body:
            del body["content"]
        if components:
            body["components"] = components
    avatar = (d.get("avatar_url") or "").strip()
    if avatar:
        body["avatar_url"] = avatar
    return body


def post_webhook(url: str, body: dict[str, Any], *, timeout: float = 10.0) -> tuple[bool, str]:
    if not is_valid_discord_webhook_url(url):
        return False, "Invalid webhook URL."
    endpoint = url if "?" in url else f"{url}?wait=false"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "MONOS/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            if code in (200, 204):
                return True, ""
            return False, f"Discord returned HTTP {code}."
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return False, "Discord rate limit (429)."
        return False, f"Discord HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, str(e.reason or e)
    except TimeoutError:
        return False, "Request timed out."
    except OSError as e:
        return False, str(e)


def post_webhook_message(
    url: str,
    body: dict[str, Any],
    *,
    wait: bool = False,
    timeout: float = 10.0,
) -> tuple[bool, str, str]:
    """POST webhook; when *wait* is True, return Discord message id from response."""
    if not is_valid_discord_webhook_url(url):
        return False, "Invalid webhook URL.", ""
    base = (url or "").strip().split("?", 1)[0]
    endpoint = f"{base}?wait=true" if wait else f"{base}?wait=false"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "MONOS/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
            if code not in (200, 204):
                return False, f"Discord returned HTTP {code}.", ""
            if not wait or not raw:
                return True, "", ""
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return True, "", ""
            if isinstance(parsed, dict):
                return True, "", str(parsed.get("id") or "").strip()
            return True, "", ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            raw = e.read()
            if raw:
                detail = raw.decode("utf-8", errors="replace")[:400]
        except OSError:
            pass
        if e.code == 429:
            return False, "Discord rate limit (429).", ""
        msg = f"Discord HTTP {e.code}: {e.reason}"
        if detail:
            msg = f"{msg} — {detail}"
        return False, msg, ""
    except urllib.error.URLError as e:
        return False, str(e.reason or e), ""
    except TimeoutError:
        return False, "Request timed out.", ""
    except OSError as e:
        return False, str(e), ""


def edit_webhook_message(
    url: str,
    message_id: str,
    body: dict[str, Any],
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """PATCH an existing webhook message."""
    mid = (message_id or "").strip()
    if not mid:
        return False, "Missing message id."
    if not is_valid_discord_webhook_url(url):
        return False, "Invalid webhook URL."
    base = (url or "").strip().split("?", 1)[0].rstrip("/")
    endpoint = f"{base}/messages/{mid}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "MONOS/1.0"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            if code in (200, 204):
                return True, ""
            return False, f"Discord returned HTTP {code}."
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return False, "Discord rate limit (429)."
        return False, f"Discord HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, str(e.reason or e)
    except TimeoutError:
        return False, "Request timed out."
    except OSError as e:
        return False, str(e)


def _discord_dispatch_blocked_locally() -> bool:
    if read_discord_disabled_locally():
        _log.debug("Discord skipped — disabled on this machine")
        return True
    return False


def post_schedule_assigned_discord(
    workspace_root: Path | str | None,
    payload: dict[str, Any],
    *,
    dedupe_key: str = "",
    project_root: Path | str | None = None,
) -> str:
    """Post schedule-assigned webhook synchronously; returns Discord message id (or 'sent')."""
    if workspace_root is None or _discord_dispatch_blocked_locally():
        return ""
    from monostudio.core.project_lifecycle import is_project_done_for_notifications

    resolved_root = _resolve_dispatch_project_root(
        workspace_root,
        payload,
        project_root=project_root,
    )
    if resolved_root is not None and is_project_done_for_notifications(resolved_root):
        _log.debug(
            "Discord skip schedule_assigned — project done: %s",
            resolved_root.name,
        )
        return ""
    key = (dedupe_key or "").strip()
    if key and not _dedupe_ok(key):
        return ""
    config = load_integrations(workspace_root)
    urls = webhook_urls_for_event(config, "schedule_assigned")
    if not urls:
        _log.debug("schedule_assigned: no enabled webhook URLs for workspace")
        return ""
    defaults = discord_defaults(config)
    vietnamese = read_notification_vietnamese()
    body_with_buttons = build_schedule_assigned_body(
        payload,
        defaults,
        workspace_root=workspace_root,
        vietnamese=vietnamese,
        include_components=True,
    )
    body_plain = build_schedule_assigned_body(
        payload,
        defaults,
        workspace_root=workspace_root,
        vietnamese=vietnamese,
        include_components=False,
    )
    for url in urls:
        if not _rate_ok(url):
            continue
        ok, err, message_id = post_webhook_message(url, body_with_buttons, wait=True)
        if ok:
            return message_id or "sent"
        if err:
            _log.warning("Discord schedule_assigned post failed (with buttons): %s", err)
        ok2, err2, message_id2 = post_webhook_message(url, body_plain, wait=True)
        if ok2:
            return message_id2 or "sent"
        if err2:
            _log.warning("Discord schedule_assigned post failed: %s", err2)
        ok3, err3 = post_webhook(url, body_plain)
        if ok3:
            return "sent"
        if err3:
            _log.warning("Discord schedule_assigned retry failed: %s", err3)
    _ensure_worker()
    _task_queue.put(
        {
            "urls": [u for u in urls if _rate_ok(u)],
            "body": body_with_buttons if body_with_buttons.get("components") else body_plain,
            "workspace_root": "",
            "event": "",
            "payload": {},
            "dedupe_key": "",
            "post_id": "",
            "attempts": 0,
        }
    )
    return "sent"


def update_schedule_assigned_discord_confirmed(
    workspace_root: Path | str | None,
    payload: dict[str, Any],
    *,
    discord_message_id: str,
    confirmer_name: str,
) -> None:
    """Update Discord assign embed after assignee confirms in MONOS."""
    mid = (discord_message_id or "").strip()
    if workspace_root is None or not mid or _discord_dispatch_blocked_locally():
        return
    config = load_integrations(workspace_root)
    urls = webhook_urls_for_event(config, "schedule_assigned")
    if not urls:
        return
    body = build_schedule_assigned_body(
        payload,
        discord_defaults(config),
        workspace_root=workspace_root,
        vietnamese=read_notification_vietnamese(),
        confirmed=True,
        confirmer_name=confirmer_name,
    )
    for url in urls:
        ok, err = edit_webhook_message(url, mid, body)
        if ok:
            return
        if err:
            _log.debug("Discord schedule_assigned edit failed: %s", err)


def _dedupe_ok(key: str) -> bool:
    now = time.monotonic()
    expired = [k for k, t in _dedupe.items() if now - t > _DEDUPE_TTL_S]
    for k in expired:
        _dedupe.pop(k, None)
    if key in _dedupe:
        return False
    _dedupe[key] = now
    return True


def _rate_ok(url: str) -> bool:
    now = time.monotonic()
    bucket = _rate.setdefault(url, deque())
    while bucket and now - bucket[0] > 60.0:
        bucket.popleft()
    if len(bucket) >= _MAX_PER_MINUTE:
        return False
    bucket.append(now)
    return True


def is_retryable_discord_error(err: str) -> bool:
    e = (err or "").strip().lower()
    if not e:
        return True
    if "429" in e or "rate limit" in e:
        return True
    if "timed out" in e or "timeout" in e:
        return True
    if "http 5" in e:
        return True
    if "http 4" in e:
        return False
    return True


def _post_with_immediate_retry(url: str, body: dict[str, Any]) -> tuple[bool, str]:
    ok, err = post_webhook(url, body)
    if ok:
        _log.debug("Discord webhook sent to %s", _mask_url_for_log(url))
        return True, ""
    _log.warning("Discord webhook failed (%s): %s", _mask_url_for_log(url), err)
    if is_retryable_discord_error(err):
        time.sleep(_RETRY_DELAY_S)
        ok2, err2 = post_webhook(url, body)
        if ok2:
            _log.debug("Discord webhook sent on retry to %s", _mask_url_for_log(url))
            return True, ""
        err = err2 or err
        _log.warning("Discord webhook retry failed (%s): %s", _mask_url_for_log(url), err)
    return False, err


def _failed_post_delay_s(attempts: int) -> float:
    return min(3600.0, _FAILED_POST_BASE_DELAY_S * (2 ** min(max(0, attempts - 1), 6)))


def _schedule_failed_post_retry(workspace_root: Path | str, post_id: str, delay_s: float) -> None:
    pid = (post_id or "").strip()
    if not pid:
        return
    timer_key = f"{Path(workspace_root).resolve()}|{pid}"

    def _run() -> None:
        with _failed_retry_lock:
            _failed_retry_timers.pop(timer_key, None)
        from monostudio.core.discord_outbox import list_failed_posts

        for entry in list_failed_posts(workspace_root):
            if str(entry.get("post_id") or "").strip() == pid:
                retry_failed_post_entry(entry)
                break

    with _failed_retry_lock:
        old = _failed_retry_timers.pop(timer_key, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(max(1.0, delay_s), _run)
        timer.daemon = True
        _failed_retry_timers[timer_key] = timer
        timer.start()


def _persist_failed_task(task: dict[str, Any], err: str) -> None:
    ws_raw = str(task.get("workspace_root") or "").strip()
    event = str(task.get("event") or "").strip()
    if not ws_raw or not event:
        return
    from monostudio.core.discord_outbox import remove_failed_post, upsert_failed_post

    post_id = str(task.get("post_id") or "").strip() or str(uuid.uuid4())
    attempts = int(task.get("attempts") or 0) + 1
    if attempts >= _FAILED_POST_MAX_ATTEMPTS:
        _log.warning(
            "Discord post dropped after %s attempts event=%s: %s",
            attempts,
            event,
            err,
        )
        remove_failed_post(ws_raw, post_id)
        return
    delay = _failed_post_delay_s(attempts)
    next_retry = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + delay,
        tz=timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = task.get("payload")
    upsert_failed_post(
        ws_raw,
        post_id=post_id,
        event=event,
        payload=payload if isinstance(payload, dict) else {},
        dedupe_key=str(task.get("dedupe_key") or "").strip(),
        attempts=attempts,
        next_retry_at=next_retry,
        last_error=err,
    )
    _schedule_failed_post_retry(ws_raw, post_id, delay)


def _normalize_queue_task(item: dict[str, Any] | tuple | None) -> dict[str, Any] | None:
    if item is None:
        return None
    if isinstance(item, dict):
        return item
    if isinstance(item, tuple) and len(item) == 2:
        url, body = item
        return {
            "url": url,
            "urls": [url],
            "body": body,
            "workspace_root": "",
            "event": "",
            "payload": {},
            "dedupe_key": "",
            "post_id": "",
            "attempts": 0,
        }
    return None


def _process_discord_task(task: dict[str, Any]) -> None:
    body = task.get("body")
    if not isinstance(body, dict):
        return
    urls: list[str] = []
    raw_urls = task.get("urls")
    if isinstance(raw_urls, list):
        urls = [str(u).strip() for u in raw_urls if str(u).strip()]
    single = str(task.get("url") or "").strip()
    if single and single not in urls:
        urls.insert(0, single)
    if not urls:
        return

    any_ok = False
    last_err = ""
    for url in urls:
        if not _rate_ok(url):
            last_err = "Discord rate limit"
            _log.warning("Discord rate limit skip for %s", _mask_url_for_log(url))
            continue
        ok, err = _post_with_immediate_retry(url, body)
        if ok:
            any_ok = True
        else:
            last_err = err
    if any_ok:
        post_id = str(task.get("post_id") or "").strip()
        ws_raw = str(task.get("workspace_root") or "").strip()
        if post_id and ws_raw:
            from monostudio.core.discord_outbox import remove_failed_post

            remove_failed_post(ws_raw, post_id)
        return
    if last_err and is_retryable_discord_error(last_err):
        _persist_failed_task(task, last_err)


def retry_failed_post_entry(entry: dict[str, Any]) -> bool:
    """Retry a persisted failed Discord POST. Returns True if delivered."""
    ws_raw = str(entry.get("workspace_root") or "").strip()
    event = str(entry.get("event") or "").strip()
    payload = entry.get("payload")
    post_id = str(entry.get("post_id") or "").strip()
    if not ws_raw or not event or not isinstance(payload, dict):
        if ws_raw and post_id:
            from monostudio.core.discord_outbox import remove_failed_post

            remove_failed_post(ws_raw, post_id)
        return False
    if _discord_dispatch_blocked_locally():
        return False

    config = load_integrations(ws_raw)
    urls = webhook_urls_for_event(config, event)
    if not urls:
        from monostudio.core.discord_outbox import remove_failed_post

        remove_failed_post(ws_raw, post_id)
        return False

    body = _build_body_for_event(
        event,
        payload,
        discord_defaults(config),
        workspace_root=ws_raw,
        vietnamese=read_notification_vietnamese(),
    )
    if body is None:
        from monostudio.core.discord_outbox import remove_failed_post

        remove_failed_post(ws_raw, post_id)
        return False

    task = {
        "workspace_root": ws_raw,
        "event": event,
        "payload": payload,
        "dedupe_key": str(entry.get("dedupe_key") or "").strip(),
        "body": body,
        "urls": urls,
        "post_id": post_id,
        "attempts": int(entry.get("attempts") or 0),
    }
    _process_discord_task(task)
    from monostudio.core.discord_outbox import list_failed_posts

    still = any(
        str(i.get("post_id") or "").strip() == post_id for i in list_failed_posts(ws_raw)
    )
    return not still


def restore_failed_posts(workspace_root: Path | str | None) -> None:
    """On workspace open: retry overdue failed posts or schedule backoff timers."""
    if workspace_root is None:
        return
    from monostudio.core.discord_outbox import list_failed_posts

    ws = Path(workspace_root).resolve()
    now = datetime.now(timezone.utc)
    for entry in list_failed_posts(ws):
        post_id = str(entry.get("post_id") or "").strip()
        if not post_id:
            continue
        next_retry = entry.get("next_retry_at")
        next_dt = None
        raw = str(next_retry or "").strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            next_dt = datetime.fromisoformat(raw) if raw else None
            if next_dt is not None and next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            next_dt = None
        if next_dt is None or next_dt <= now:
            retry_failed_post_entry(entry)
            continue
        remaining = max(1.0, (next_dt - now).total_seconds())
        _schedule_failed_post_retry(ws, post_id, remaining)


def flush_failed_posts(workspace_root: Path | str | None) -> int:
    """Retry all persisted failed posts immediately (app quit). Returns success count."""
    if workspace_root is None:
        return 0
    from monostudio.core.discord_outbox import list_failed_posts

    ws = Path(workspace_root).resolve()
    sent = 0
    for entry in list(list_failed_posts(ws)):
        if retry_failed_post_entry(entry):
            sent += 1
    return sent


def _worker_loop() -> None:
    while True:
        item = _task_queue.get()
        try:
            task = _normalize_queue_task(item)
            if task is None:
                return
            _process_discord_task(task)
        finally:
            _task_queue.task_done()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker_loop, name="monos-discord-webhook", daemon=True)
        t.start()
        _worker_started = True


def _build_body_for_event(
    event: str,
    payload: dict[str, Any],
    defaults: dict[str, str],
    *,
    workspace_root: Path | str | None = None,
    vietnamese: bool | None = None,
) -> dict[str, Any] | None:
    if event == "mention":
        return build_mention_body(payload, defaults, workspace_root=workspace_root, vietnamese=vietnamese)
    if event == "note_done":
        return build_note_done_body(payload, defaults, workspace_root=workspace_root, vietnamese=vietnamese)
    if event == "inbox_received":
        return build_inbox_received_body(payload, defaults, workspace_root=workspace_root, vietnamese=vietnamese)
    if event == "outbox_received":
        return build_outbox_received_body(payload, defaults, workspace_root=workspace_root, vietnamese=vietnamese)
    if event == "inbox_distributed":
        return build_inbox_distributed_body(payload, defaults, workspace_root=workspace_root, vietnamese=vietnamese)
    if event == "schedule_due":
        return build_schedule_due_body(payload, defaults, workspace_root=workspace_root, vietnamese=vietnamese)
    if event == "schedule_assigned":
        return build_schedule_assigned_body(payload, defaults, workspace_root=workspace_root, vietnamese=vietnamese)
    return None


def _resolve_dispatch_project_root(
    workspace_root: Path | str | None,
    payload: dict[str, Any],
    *,
    project_root: Path | str | None = None,
) -> Path | None:
    if project_root is not None:
        root = Path(project_root)
        try:
            if (root / ".monostudio" / "project.json").is_file():
                return root.resolve()
        except OSError:
            pass
    from monostudio.core.project_lifecycle import resolve_workspace_project_root

    name = str(payload.get("project_name") or "").strip()
    if not name:
        return None
    return resolve_workspace_project_root(workspace_root, name)


def dispatch_discord_event(
    workspace_root: Path | str | None,
    event: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str = "",
    project_root: Path | str | None = None,
) -> None:
    """Enqueue Discord webhook POST for enabled event (non-blocking)."""
    if workspace_root is None or _discord_dispatch_blocked_locally():
        return
    from monostudio.core.project_lifecycle import is_project_done_for_notifications

    resolved_root = _resolve_dispatch_project_root(
        workspace_root,
        payload,
        project_root=project_root,
    )
    if resolved_root is not None and is_project_done_for_notifications(resolved_root):
        _log.debug(
            "Discord skip event=%s — project done: %s",
            event,
            resolved_root.name,
        )
        return
    config = load_integrations(workspace_root)
    urls = webhook_urls_for_event(config, event)
    if not urls:
        _log.debug("Discord skip event=%s — no webhook URLs (enabled=%s)", event, bool(config.get("discord", {}).get("enabled") if isinstance(config.get("discord"), dict) else False))
        return
    body = _build_body_for_event(
        event,
        payload,
        discord_defaults(config),
        workspace_root=workspace_root,
        vietnamese=read_notification_vietnamese(),
    )
    if body is None:
        return

    key = (dedupe_key or "").strip()
    if not key and event == "mention":
        ids = payload.get("mention_ids")
        if isinstance(ids, list) and ids:
            key = f"{event}:{ids[0]}"
    if key and not _dedupe_ok(key):
        return

    if not key and event in ("inbox_received", "outbox_received", "inbox_distributed"):
        batch = str(payload.get("batch_id") or "").strip()
        if batch:
            key = f"{event}:{batch}"
            if not _dedupe_ok(key):
                return

    if not key and event == "schedule_assigned":
        ids = payload.get("assign_inbox_ids")
        if isinstance(ids, list) and ids:
            key = f"{event}:{ids[0]}"
            if not _dedupe_ok(key):
                return

    if not key and event == "note_done":
        note_id = str(payload.get("note_id") or "").strip()
        if note_id:
            key = f"{event}:{note_id}"
            if not _dedupe_ok(key):
                return

    _ensure_worker()
    _task_queue.put(
        {
            "workspace_root": str(Path(workspace_root).resolve()),
            "event": event,
            "payload": dict(payload),
            "dedupe_key": key,
            "body": body,
            "urls": list(urls),
            "post_id": "",
            "attempts": 0,
        }
    )


def send_test_webhook(
    workspace_root: Path | str | None,
    *,
    user_name: str = "",
    machine: str = "",
    url_override: str = "",
) -> tuple[bool, str]:
    """Synchronous test POST (Settings → Send test)."""
    url = (url_override or "").strip()
    defaults = {"username": "MONOS", "avatar_url": ""}
    if not url and workspace_root is not None:
        config = load_integrations(workspace_root)
        defaults = discord_defaults(config)
        discord = config.get("discord")
        if isinstance(discord, dict):
            for wh in discord.get("webhooks") or []:
                if isinstance(wh, dict):
                    candidate = str(wh.get("url") or "").strip()
                    if is_valid_discord_webhook_url(candidate):
                        url = candidate
                        break
    if not is_valid_discord_webhook_url(url):
        return False, "Enter a valid Discord webhook URL."
    if workspace_root is not None:
        defaults = discord_defaults(load_integrations(workspace_root))
    body = build_test_body(
        user_name=user_name,
        machine=machine,
        defaults=defaults,
        vietnamese=read_notification_vietnamese(),
    )
    ok, err = post_webhook(url, body)
    if ok:
        return True, ""
    return False, err or "Webhook test failed."
