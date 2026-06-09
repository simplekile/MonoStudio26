"""Workspace-level integrations config (Discord webhooks, etc.)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.access_control import is_admin_capable
from monostudio.core.atomic_write import atomic_write_text

INTEGRATIONS_SCHEMA = 1
INTEGRATIONS_FILENAME = "integrations.json"

_DISCORD_WEBHOOK_URL_RE = re.compile(
    r"^https://(?:discord(?:app)?\.com|discord\.com)/api/webhooks/\d+/[\w-]+$",
    re.IGNORECASE,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def integrations_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).resolve() / ".monostudio" / INTEGRATIONS_FILENAME


def default_integrations() -> dict[str, Any]:
    return {
        "schema": INTEGRATIONS_SCHEMA,
        "updated_at": _utc_now_iso(),
        "discord": {
            "enabled": False,
            "webhooks": [],
            "defaults": {
                "username": "MONOS",
                "avatar_url": "",
            },
        },
    }


def is_valid_discord_webhook_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    return bool(_DISCORD_WEBHOOK_URL_RE.match(u))


def mask_webhook_url(url: str) -> str:
    """Mask secret token; keep webhook id suffix for recognition."""
    u = (url or "").strip()
    if not u:
        return ""
    if "/" not in u:
        return "••••••••"
    token = u.rsplit("/", 1)[-1]
    if len(token) <= 8:
        return "••••••••/" + token
    return "••••••••/" + token[-8:]


def load_integrations(workspace_root: Path | str | None) -> dict[str, Any]:
    if workspace_root is None:
        return default_integrations()
    path = integrations_path(workspace_root)
    if not path.is_file():
        return default_integrations()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_integrations()
    if not isinstance(data, dict):
        return default_integrations()
    if int(data.get("schema") or 0) != INTEGRATIONS_SCHEMA:
        merged = default_integrations()
        if isinstance(data.get("discord"), dict):
            merged["discord"] = _normalize_discord_block(data["discord"])
        return merged
    discord = data.get("discord")
    if not isinstance(discord, dict):
        data["discord"] = default_integrations()["discord"]
    else:
        data["discord"] = _normalize_discord_block(discord)
    return data


def _normalize_discord_block(discord: dict[str, Any]) -> dict[str, Any]:
    base = default_integrations()["discord"]
    out = dict(base)
    out["enabled"] = bool(discord.get("enabled"))
    defaults = discord.get("defaults")
    if isinstance(defaults, dict):
        out["defaults"] = {
            "username": str(defaults.get("username") or "MONOS").strip() or "MONOS",
            "avatar_url": str(defaults.get("avatar_url") or "").strip(),
        }
    webhooks: list[dict[str, Any]] = []
    raw = discord.get("webhooks")
    if isinstance(raw, list):
        for wh in raw:
            if not isinstance(wh, dict):
                continue
            url = str(wh.get("url") or "").strip()
            if not is_valid_discord_webhook_url(url):
                continue
            events_raw = wh.get("events") if isinstance(wh.get("events"), dict) else {}
            webhooks.append(
                {
                    "id": str(wh.get("id") or f"wh_{uuid.uuid4().hex[:6]}"),
                    "label": str(wh.get("label") or "").strip(),
                    "url": url,
                    "events": {
                        "mention": bool(events_raw.get("mention")),
                        "note_done": bool(events_raw.get("note_done")),
                        "inbox_received": bool(events_raw.get("inbox_received")),
                        "inbox_distributed": bool(events_raw.get("inbox_distributed")),
                        "outbox_received": bool(events_raw.get("outbox_received")),
                        "schedule_due": bool(events_raw.get("schedule_due")),
                        "schedule_assigned": bool(
                            events_raw.get("schedule_assigned")
                            if "schedule_assigned" in events_raw
                            else events_raw.get("schedule_due")
                        ),
                    },
                }
            )
    out["webhooks"] = webhooks
    return out


def get_primary_webhook(config: dict[str, Any]) -> dict[str, Any] | None:
    discord = config.get("discord")
    if not isinstance(discord, dict) or not discord.get("enabled"):
        return None
    webhooks = discord.get("webhooks")
    if not isinstance(webhooks, list) or not webhooks:
        return None
    first = webhooks[0]
    return first if isinstance(first, dict) else None


def is_event_enabled(config: dict[str, Any], event: str) -> bool:
    wh = get_primary_webhook(config)
    if wh is None:
        return False
    events = wh.get("events")
    if not isinstance(events, dict):
        return False
    return bool(events.get(event))


def webhook_urls_for_event(config: dict[str, Any], event: str) -> list[str]:
    discord = config.get("discord")
    if not isinstance(discord, dict) or not discord.get("enabled"):
        return []
    webhooks = discord.get("webhooks")
    if not isinstance(webhooks, list):
        return []
    urls: list[str] = []
    all_urls: list[str] = []
    for wh in webhooks:
        if not isinstance(wh, dict):
            continue
        url = str(wh.get("url") or "").strip()
        if not is_valid_discord_webhook_url(url):
            continue
        all_urls.append(url)
        events = wh.get("events")
        if isinstance(events, dict) and events.get(event):
            urls.append(url)
    if not urls and all_urls:
        # Mention: legacy configs may omit the flag while Discord is already enabled.
        if event == "mention":
            return list(all_urls)
        if event == "schedule_due" and is_event_enabled(config, "schedule_due"):
            return list(all_urls)
        if event == "schedule_assigned" and is_event_enabled(config, "schedule_assigned"):
            return list(all_urls)
        if event in ("inbox_received", "inbox_distributed", "outbox_received") and (
            is_event_enabled(config, "inbox_received")
            or is_event_enabled(config, "inbox_distributed")
            or is_event_enabled(config, "outbox_received")
        ):
            return list(all_urls)
    return urls


def discord_defaults(config: dict[str, Any]) -> dict[str, str]:
    discord = config.get("discord")
    if not isinstance(discord, dict):
        return {"username": "MONOS", "avatar_url": ""}
    defaults = discord.get("defaults")
    if not isinstance(defaults, dict):
        return {"username": "MONOS", "avatar_url": ""}
    return {
        "username": str(defaults.get("username") or "MONOS").strip() or "MONOS",
        "avatar_url": str(defaults.get("avatar_url") or "").strip(),
    }


def build_integrations_from_ui(
    *,
    enabled: bool,
    webhook_url: str,
    label: str,
    mention_enabled: bool,
    inbox_enabled: bool = False,
    schedule_due_enabled: bool = False,
    schedule_assigned_enabled: bool = False,
    note_done_enabled: bool = False,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build integrations dict from Settings UI fields."""
    base = load_integrations(None) if existing is None else dict(existing)
    base["schema"] = INTEGRATIONS_SCHEMA
    base["updated_at"] = _utc_now_iso()

    url = (webhook_url or "").strip()
    wh_id = f"wh_{uuid.uuid4().hex[:6]}"
    if existing:
        prev = get_primary_webhook(existing)
        if prev:
            wh_id = str(prev.get("id") or wh_id)

    webhooks: list[dict[str, Any]] = []
    if url and is_valid_discord_webhook_url(url):
        webhooks.append(
            {
                "id": wh_id,
                "label": (label or "").strip(),
                "url": url,
                "events": {
                    "mention": bool(mention_enabled),
                    "note_done": bool(note_done_enabled),
                    "inbox_received": bool(inbox_enabled),
                    "inbox_distributed": bool(inbox_enabled),
                    "outbox_received": bool(inbox_enabled),
                    "schedule_due": bool(schedule_due_enabled),
                    "schedule_assigned": bool(schedule_assigned_enabled),
                },
            }
        )

    discord = _normalize_discord_block(
        {
            "enabled": bool(enabled) and bool(webhooks),
            "webhooks": webhooks,
            "defaults": discord_defaults(base),
        }
    )
    base["discord"] = discord
    return base


def write_integrations(
    workspace_root: Path | str,
    config: dict[str, Any],
    *,
    require_admin: bool = True,
) -> None:
    if require_admin and not is_admin_capable():
        raise PermissionError("Administrator access required to change integrations.")
    path = integrations_path(workspace_root)
    payload = dict(config)
    payload["schema"] = INTEGRATIONS_SCHEMA
    payload["updated_at"] = _utc_now_iso()
    if isinstance(payload.get("discord"), dict):
        payload["discord"] = _normalize_discord_block(payload["discord"])
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content, encoding="utf-8")
