"""Persisted Discord outbox: inbox batches + failed POST retries."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.atomic_write import atomic_write_text

_log = logging.getLogger("monostudio.discord_outbox")

OUTBOX_SCHEMA = 3
OUTBOX_FILENAME = "discord_outbox.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def outbox_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).resolve() / ".monostudio" / OUTBOX_FILENAME


def default_outbox() -> dict[str, Any]:
    return {
        "schema": OUTBOX_SCHEMA,
        "updated_at": _utc_now_iso(),
        "inbox_received": [],
        "inbox_distributed": [],
        "outbox_received": [],
        "failed_posts": [],
    }


def _normalize_outbox(raw: dict[str, Any]) -> dict[str, Any]:
    out = default_outbox()
    for key in ("inbox_received", "inbox_distributed", "outbox_received", "failed_posts"):
        items = raw.get(key)
        if isinstance(items, list):
            out[key] = [i for i in items if isinstance(i, dict)]
    return out


def load_outbox(workspace_root: Path | str | None) -> dict[str, Any]:
    if workspace_root is None:
        return default_outbox()
    path = outbox_path(workspace_root)
    if not path.is_file():
        return default_outbox()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_outbox()
    if not isinstance(raw, dict):
        return default_outbox()
    schema = int(raw.get("schema") or 0)
    if schema not in (1, 2, 3):
        return _normalize_outbox(raw)
    return _normalize_outbox(raw)


def save_outbox(workspace_root: Path | str, data: dict[str, Any]) -> None:
    root = Path(workspace_root).resolve()
    payload = _normalize_outbox(dict(data))
    payload["schema"] = OUTBOX_SCHEMA
    payload["updated_at"] = _utc_now_iso()
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(outbox_path(root), content, encoding="utf-8")


def upsert_inbox_received_entry(
    workspace_root: Path | str,
    *,
    bucket_key: str,
    project_root: Path | str,
    project_name: str,
    actor_name: str,
    source: str,
    date_str: str,
    window_started_at: str,
    last_added_at: str = "",
    batch_id: str,
    pending_names: list[str],
    flush_after: str,
) -> None:
    """Merge or create a persisted inbox_received batch (keyed by bucket_key)."""
    key = (bucket_key or "").strip()
    if not key:
        return
    data = load_outbox(workspace_root)
    items: list[dict[str, Any]] = [
        i for i in data.get("inbox_received", []) if isinstance(i, dict)
    ]
    proj = str(Path(project_root).resolve())
    names: list[str] = []
    seen: set[str] = set()
    for raw in pending_names:
        n = str(raw or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    existing: dict[str, Any] | None = None
    rest: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("bucket_key") or "").strip() == key:
            existing = item
        else:
            rest.append(item)

    if existing is None:
        existing = {
            "bucket_key": key,
            "workspace_root": str(Path(workspace_root).resolve()),
            "project_root": proj,
            "project_name": (project_name or "").strip(),
            "actor_name": (actor_name or "").strip(),
            "source": (source or "client").strip().lower(),
            "date_str": (date_str or "").strip(),
            "window_started_at": (window_started_at or _utc_now_iso()).strip(),
            "last_added_at": (last_added_at or _utc_now_iso()).strip(),
            "batch_id": (batch_id or "").strip(),
            "pending_names": list(names),
            "flush_after": (flush_after or _utc_now_iso()).strip(),
        }
    else:
        merged_names = list(existing.get("pending_names") or [])
        seen_m = set(str(x) for x in merged_names)
        for n in names:
            if n not in seen_m:
                seen_m.add(n)
                merged_names.append(n)
        existing["pending_names"] = merged_names
        existing["flush_after"] = (flush_after or existing.get("flush_after") or _utc_now_iso()).strip()
        if actor_name:
            existing["actor_name"] = actor_name.strip()
        if last_added_at:
            existing["last_added_at"] = last_added_at.strip()
        if not str(existing.get("window_started_at") or "").strip():
            existing["window_started_at"] = window_started_at
        if not str(existing.get("batch_id") or "").strip():
            existing["batch_id"] = batch_id
        existing["project_root"] = proj or existing.get("project_root", "")

    rest.append(existing)
    data["inbox_received"] = rest
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.warning("Could not persist discord outbox for %s", key, exc_info=True)


def remove_inbox_received_entry(workspace_root: Path | str, bucket_key: str) -> None:
    key = (bucket_key or "").strip()
    if not key:
        return
    data = load_outbox(workspace_root)
    items = data.get("inbox_received")
    if not isinstance(items, list):
        return
    data["inbox_received"] = [
        i
        for i in items
        if isinstance(i, dict) and str(i.get("bucket_key") or "").strip() != key
    ]
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.debug("Could not update discord outbox after remove", exc_info=True)


def list_inbox_received_entries(workspace_root: Path | str | None) -> list[dict[str, Any]]:
    if workspace_root is None:
        return []
    items = load_outbox(workspace_root).get("inbox_received")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def upsert_outbox_received_entry(
    workspace_root: Path | str,
    *,
    bucket_key: str,
    project_root: Path | str,
    project_name: str,
    actor_name: str,
    source: str,
    date_str: str,
    window_started_at: str,
    last_added_at: str = "",
    batch_id: str,
    pending_names: list[str],
    flush_after: str,
) -> None:
    """Merge or create a persisted outbox_received batch (keyed by bucket_key)."""
    key = (bucket_key or "").strip()
    if not key:
        return
    data = load_outbox(workspace_root)
    items: list[dict[str, Any]] = [
        i for i in data.get("outbox_received", []) if isinstance(i, dict)
    ]
    proj = str(Path(project_root).resolve())
    names: list[str] = []
    seen: set[str] = set()
    for raw in pending_names:
        n = str(raw or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    existing: dict[str, Any] | None = None
    rest: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("bucket_key") or "").strip() == key:
            existing = item
        else:
            rest.append(item)

    if existing is None:
        existing = {
            "bucket_key": key,
            "workspace_root": str(Path(workspace_root).resolve()),
            "project_root": proj,
            "project_name": (project_name or "").strip(),
            "actor_name": (actor_name or "").strip(),
            "source": (source or "client").strip().lower(),
            "date_str": (date_str or "").strip(),
            "window_started_at": (window_started_at or _utc_now_iso()).strip(),
            "last_added_at": (last_added_at or _utc_now_iso()).strip(),
            "batch_id": (batch_id or "").strip(),
            "pending_names": list(names),
            "flush_after": (flush_after or _utc_now_iso()).strip(),
        }
    else:
        merged_names = list(existing.get("pending_names") or [])
        seen_m = set(str(x) for x in merged_names)
        for n in names:
            if n not in seen_m:
                seen_m.add(n)
                merged_names.append(n)
        existing["pending_names"] = merged_names
        existing["flush_after"] = (flush_after or existing.get("flush_after") or _utc_now_iso()).strip()
        if actor_name:
            existing["actor_name"] = actor_name.strip()
        if last_added_at:
            existing["last_added_at"] = last_added_at.strip()
        if not str(existing.get("window_started_at") or "").strip():
            existing["window_started_at"] = window_started_at
        if not str(existing.get("batch_id") or "").strip():
            existing["batch_id"] = batch_id
        existing["project_root"] = proj or existing.get("project_root", "")

    rest.append(existing)
    data["outbox_received"] = rest
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.warning("Could not persist discord outbox_received for %s", key, exc_info=True)


def remove_outbox_received_entry(workspace_root: Path | str, bucket_key: str) -> None:
    key = (bucket_key or "").strip()
    if not key:
        return
    data = load_outbox(workspace_root)
    items = data.get("outbox_received")
    if not isinstance(items, list):
        return
    data["outbox_received"] = [
        i
        for i in items
        if isinstance(i, dict) and str(i.get("bucket_key") or "").strip() != key
    ]
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.debug("Could not update discord outbox_received after remove", exc_info=True)


def list_outbox_received_entries(workspace_root: Path | str | None) -> list[dict[str, Any]]:
    if workspace_root is None:
        return []
    items = load_outbox(workspace_root).get("outbox_received")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def upsert_inbox_distributed_entry(
    workspace_root: Path | str,
    *,
    bucket_key: str,
    project_root: Path | str,
    project_name: str,
    actor_name: str,
    source: str,
    dest_label: str,
    window_started_at: str,
    batch_id: str,
    pending_entity_names: list[str],
    flush_after: str,
) -> None:
    key = (bucket_key or "").strip()
    if not key:
        return
    data = load_outbox(workspace_root)
    items: list[dict[str, Any]] = [
        i for i in data.get("inbox_distributed", []) if isinstance(i, dict)
    ]
    proj = str(Path(project_root).resolve())
    names: list[str] = []
    seen: set[str] = set()
    for raw in pending_entity_names:
        n = str(raw or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    existing: dict[str, Any] | None = None
    rest: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("bucket_key") or "").strip() == key:
            existing = item
        else:
            rest.append(item)

    if existing is None:
        existing = {
            "bucket_key": key,
            "workspace_root": str(Path(workspace_root).resolve()),
            "project_root": proj,
            "project_name": (project_name or "").strip(),
            "actor_name": (actor_name or "").strip(),
            "source": (source or "client").strip().lower(),
            "dest_label": (dest_label or "").strip(),
            "window_started_at": (window_started_at or _utc_now_iso()).strip(),
            "batch_id": (batch_id or "").strip(),
            "pending_entity_names": list(names),
            "flush_after": (flush_after or _utc_now_iso()).strip(),
        }
    else:
        merged = list(existing.get("pending_entity_names") or [])
        seen_m = set(str(x) for x in merged)
        for n in names:
            if n not in seen_m:
                seen_m.add(n)
                merged.append(n)
        existing["pending_entity_names"] = merged
        existing["flush_after"] = (flush_after or existing.get("flush_after") or _utc_now_iso()).strip()
        if actor_name:
            existing["actor_name"] = actor_name.strip()
        if not str(existing.get("window_started_at") or "").strip():
            existing["window_started_at"] = window_started_at
        if not str(existing.get("batch_id") or "").strip():
            existing["batch_id"] = batch_id
        existing["project_root"] = proj or existing.get("project_root", "")

    rest.append(existing)
    data["inbox_distributed"] = rest
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.warning("Could not persist discord distributed outbox for %s", key, exc_info=True)


def remove_inbox_distributed_entry(workspace_root: Path | str, bucket_key: str) -> None:
    key = (bucket_key or "").strip()
    if not key:
        return
    data = load_outbox(workspace_root)
    items = data.get("inbox_distributed")
    if not isinstance(items, list):
        return
    data["inbox_distributed"] = [
        i
        for i in items
        if isinstance(i, dict) and str(i.get("bucket_key") or "").strip() != key
    ]
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.debug("Could not update discord distributed outbox after remove", exc_info=True)


def list_inbox_distributed_entries(workspace_root: Path | str | None) -> list[dict[str, Any]]:
    if workspace_root is None:
        return []
    items = load_outbox(workspace_root).get("inbox_distributed")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def upsert_failed_post(
    workspace_root: Path | str,
    *,
    post_id: str = "",
    event: str,
    payload: dict[str, Any],
    dedupe_key: str = "",
    attempts: int = 1,
    next_retry_at: str,
    last_error: str = "",
) -> str:
    pid = (post_id or "").strip() or str(uuid.uuid4())
    data = load_outbox(workspace_root)
    items: list[dict[str, Any]] = [
        i for i in data.get("failed_posts", []) if isinstance(i, dict)
    ]
    existing: dict[str, Any] | None = None
    rest: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("post_id") or "").strip() == pid:
            existing = item
        else:
            rest.append(item)
    created_at = (
        str(existing.get("created_at") or "").strip() if existing else _utc_now_iso()
    )
    entry = {
        "post_id": pid,
        "workspace_root": str(Path(workspace_root).resolve()),
        "event": (event or "").strip(),
        "payload": dict(payload) if isinstance(payload, dict) else {},
        "dedupe_key": (dedupe_key or "").strip(),
        "attempts": max(1, int(attempts)),
        "next_retry_at": (next_retry_at or _utc_now_iso()).strip(),
        "created_at": created_at,
        "last_error": (last_error or "").strip()[:500],
    }
    rest.append(entry)
    data["failed_posts"] = rest
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.warning("Could not persist failed Discord post %s", pid, exc_info=True)
    return pid


def remove_failed_post(workspace_root: Path | str, post_id: str) -> None:
    pid = (post_id or "").strip()
    if not pid:
        return
    data = load_outbox(workspace_root)
    items = data.get("failed_posts")
    if not isinstance(items, list):
        return
    data["failed_posts"] = [
        i
        for i in items
        if isinstance(i, dict) and str(i.get("post_id") or "").strip() != pid
    ]
    try:
        save_outbox(workspace_root, data)
    except OSError:
        _log.debug("Could not remove failed Discord post", exc_info=True)


def list_failed_posts(workspace_root: Path | str | None) -> list[dict[str, Any]]:
    if workspace_root is None:
        return []
    items = load_outbox(workspace_root).get("failed_posts")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]
