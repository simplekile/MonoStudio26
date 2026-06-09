"""Debounce inbox_distributed Discord webhooks — batch, reconcile on disk, persist outbox."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.discord_inbox_debounce import resolve_project_root
from monostudio.core.discord_outbox import (
    list_inbox_distributed_entries,
    remove_inbox_distributed_entry,
    upsert_inbox_distributed_entry,
)
from monostudio.core.inbox_reader import load_inbox_distributed

_log = logging.getLogger("monostudio.discord_inbox_distributed_debounce")

INBOX_DISTRIBUTED_DEBOUNCE_S = 45.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_dt(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc)


def _bucket_key(
    workspace_root: Path,
    project_name: str,
    source: str,
    dest_label: str,
) -> str:
    ws = str(workspace_root.resolve())
    return (
        f"{ws}|{(project_name or '').strip()}|"
        f"{(source or '').strip().lower()}|{(dest_label or '').strip()}"
    )


def reconcile_inbox_distributed(
    project_root: Path | str,
    *,
    source: str,
    dest_label: str,
    window_started_at: str,
    pending_entity_names: list[str],
) -> tuple[int, list[str]]:
    """
    Read inbox_distributed.json — return count + entity names still logged for this batch window.
    """
    root = Path(project_root).resolve()
    src = (source or "client").strip().lower()
    dest = (dest_label or "").strip()
    window_start = _parse_iso_dt(window_started_at)

    entries = load_inbox_distributed(root, src)
    names: list[str] = []
    seen: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = (entry.get("destination_label") or "").strip()
        if dest and label != dest:
            continue
        distributed_at = _parse_iso_dt(str(entry.get("distributed_at") or ""))
        if window_start is not None and distributed_at is not None and distributed_at < window_start:
            continue
        name = (entry.get("entity_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)

    for raw in pending_entity_names:
        n = str(raw or "").strip()
        if not n or n in seen:
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            label = (entry.get("destination_label") or "").strip()
            if dest and label != dest:
                continue
            distributed_at = _parse_iso_dt(str(entry.get("distributed_at") or ""))
            if window_start is not None and distributed_at is not None and distributed_at < window_start:
                continue
            if (entry.get("entity_name") or "").strip() != n:
                continue
            seen.add(n)
            names.append(n)
            break

    return len(names), names


@dataclass
class _DistributedBatch:
    workspace_root: Path
    project_root: Path
    project_name: str
    actor_name: str
    source: str
    dest_label: str
    window_started_at: str = ""
    batch_id: str = ""
    pending_entity_names: list[str] = field(default_factory=list)

    def extend(self, entity_names: list[str]) -> None:
        seen = set(self.pending_entity_names)
        for raw in entity_names:
            n = str(raw or "").strip()
            if n and n not in seen:
                seen.add(n)
                self.pending_entity_names.append(n)


_lock = threading.Lock()
_buckets: dict[str, _DistributedBatch] = {}
_timers: dict[str, threading.Timer] = {}


def _entry_to_batch(entry: dict) -> _DistributedBatch | None:
    ws_raw = str(entry.get("workspace_root") or "").strip()
    proj_raw = str(entry.get("project_root") or "").strip()
    if not ws_raw:
        return None
    ws = Path(ws_raw)
    project_root = Path(proj_raw) if proj_raw else None
    if project_root is None or not project_root.is_dir():
        project_root = resolve_project_root(ws, str(entry.get("project_name") or ""))
    if project_root is None:
        return None
    names_raw = entry.get("pending_entity_names")
    names = [str(n).strip() for n in names_raw if str(n).strip()] if isinstance(names_raw, list) else []
    return _DistributedBatch(
        workspace_root=ws,
        project_root=project_root,
        project_name=str(entry.get("project_name") or project_root.name).strip(),
        actor_name=str(entry.get("actor_name") or "").strip(),
        source=str(entry.get("source") or "client").strip().lower(),
        dest_label=str(entry.get("dest_label") or "").strip(),
        window_started_at=str(entry.get("window_started_at") or _utc_now_iso()).strip(),
        batch_id=str(entry.get("batch_id") or "").strip(),
        pending_entity_names=names,
    )


def _dispatch_reconciled_batch(batch: _DistributedBatch, *, bucket_key: str) -> bool:
    count, names = reconcile_inbox_distributed(
        batch.project_root,
        source=batch.source,
        dest_label=batch.dest_label,
        window_started_at=batch.window_started_at,
        pending_entity_names=batch.pending_entity_names,
    )
    if count <= 0:
        _log.debug(
            "Discord inbox_distributed skipped after reconcile (empty) dest=%s",
            batch.dest_label,
        )
        try:
            remove_inbox_distributed_entry(batch.workspace_root, bucket_key)
        except Exception:
            pass
        return False

    from monostudio.core.discord_webhook import dispatch_discord_event

    batch_id = (batch.batch_id or bucket_key).strip()
    dispatch_discord_event(
        batch.workspace_root,
        "inbox_distributed",
        {
            "actor_name": batch.actor_name,
            "count": count,
            "dest_label": batch.dest_label,
            "source": batch.source,
            "entity_names": names,
            "project_name": batch.project_name,
            "batch_id": batch_id,
        },
        dedupe_key=f"inbox_distributed:{batch_id}",
        project_root=batch.project_root,
    )
    try:
        remove_inbox_distributed_entry(batch.workspace_root, bucket_key)
    except Exception:
        pass
    _log.debug(
        "Discord inbox_distributed flushed count=%s dest=%s (reconciled)",
        count,
        batch.dest_label,
    )
    return True


def _outbox_entry_for_key(workspace_root: Path, bucket_key: str) -> dict[str, Any] | None:
    key = (bucket_key or "").strip()
    if not key:
        return None
    for entry in list_inbox_distributed_entries(workspace_root):
        if str(entry.get("bucket_key") or "").strip() == key:
            return entry
    return None


def _flush(key: str, workspace_root: Path | None = None) -> None:
    with _lock:
        batch = _buckets.pop(key, None)
        _timers.pop(key, None)
    if batch is None and workspace_root is not None:
        entry = _outbox_entry_for_key(workspace_root, key)
        if entry is not None:
            batch = _entry_to_batch(entry)
    if batch is None:
        return
    _dispatch_reconciled_batch(batch, bucket_key=key)


def _flush_from_outbox_entry(entry: dict[str, Any]) -> bool:
    key = str(entry.get("bucket_key") or "").strip()
    batch = _entry_to_batch(entry)
    if batch is None or not key:
        ws = str(entry.get("workspace_root") or "").strip()
        if ws and key:
            try:
                remove_inbox_distributed_entry(ws, key)
            except Exception:
                pass
        return False
    with _lock:
        _buckets.pop(key, None)
        timer = _timers.pop(key, None)
        if timer is not None:
            timer.cancel()
    return _dispatch_reconciled_batch(batch, bucket_key=key)


def _persist_batch(
    batch: _DistributedBatch,
    *,
    bucket_key: str,
    flush_after: str,
) -> None:
    try:
        upsert_inbox_distributed_entry(
            batch.workspace_root,
            bucket_key=bucket_key,
            project_root=batch.project_root,
            project_name=batch.project_name,
            actor_name=batch.actor_name,
            source=batch.source,
            dest_label=batch.dest_label,
            window_started_at=batch.window_started_at,
            batch_id=batch.batch_id,
            pending_entity_names=batch.pending_entity_names,
            flush_after=flush_after,
        )
    except Exception:
        _log.debug("discord distributed outbox persist failed", exc_info=True)


def enqueue_inbox_distributed(
    workspace_root: Path | str | None,
    *,
    project_root: Path | str | None,
    project_name: str,
    actor_name: str,
    source: str,
    dest_label: str,
    count: int,
    entity_names: list[str],
    debounce_s: float | None = None,
) -> None:
    """Queue inbox_distributed; merged per project+source+destination, sent after quiet period."""
    if workspace_root is None or count <= 0:
        return
    ws = Path(workspace_root).resolve()
    proj = Path(project_root).resolve() if project_root is not None else None
    if proj is None or not proj.is_dir():
        proj = resolve_project_root(ws, project_name)
    if proj is None:
        _log.debug("inbox_distributed enqueue skipped — project not resolved for %s", project_name)
        return

    delay = INBOX_DISTRIBUTED_DEBOUNCE_S if debounce_s is None else max(1.0, float(debounce_s))
    dest = (dest_label or "").strip() or "pipeline"
    key = _bucket_key(ws, project_name, source, dest)
    flush_after_dt = datetime.now(timezone.utc).timestamp() + delay
    flush_after = datetime.fromtimestamp(flush_after_dt, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _lock:
        batch = _buckets.get(key)
        if batch is None:
            now = _utc_now_iso()
            batch = _DistributedBatch(
                workspace_root=ws,
                project_root=proj,
                project_name=(project_name or proj.name).strip(),
                actor_name=(actor_name or "").strip(),
                source=(source or "client").strip().lower(),
                dest_label=dest,
                window_started_at=now,
                batch_id=now,
            )
            _buckets[key] = batch
        elif actor_name:
            batch.actor_name = actor_name.strip()
        batch.extend(entity_names)

        _persist_batch(batch, bucket_key=key, flush_after=flush_after)

        old_timer = _timers.pop(key, None)
        if old_timer is not None:
            old_timer.cancel()
        timer = threading.Timer(delay, _flush, args=(key, ws))
        timer.daemon = True
        _timers[key] = timer
        timer.start()


def flush_all_pending_inbox_distributed(workspace_root: Path | str | None) -> int:
    if workspace_root is None:
        return 0
    ws = Path(workspace_root).resolve()
    sent = 0
    flushed_keys: set[str] = set()

    with _lock:
        for timer in _timers.values():
            timer.cancel()
        memory_batches = {
            k: b for k, b in _buckets.items() if b.workspace_root.resolve() == ws
        }
        for key in list(_buckets.keys()):
            if key in memory_batches:
                _buckets.pop(key, None)
                _timers.pop(key, None)

    for key, batch in memory_batches.items():
        if _dispatch_reconciled_batch(batch, bucket_key=key):
            sent += 1
        flushed_keys.add(key)

    for entry in list_inbox_distributed_entries(ws):
        key = str(entry.get("bucket_key") or "").strip()
        if not key or key in flushed_keys:
            continue
        if _flush_from_outbox_entry(entry):
            sent += 1

    return sent


def restore_inbox_distributed_outbox(workspace_root: Path | str | None) -> None:
    if workspace_root is None:
        return
    ws = Path(workspace_root).resolve()
    now = datetime.now(timezone.utc)

    for entry in list_inbox_distributed_entries(ws):
        key = str(entry.get("bucket_key") or "").strip()
        if not key:
            continue
        flush_after = _parse_iso_dt(str(entry.get("flush_after") or ""))
        if flush_after is not None and flush_after <= now:
            _flush_from_outbox_entry(entry)
            continue

        batch = _entry_to_batch(entry)
        if batch is None:
            try:
                remove_inbox_distributed_entry(ws, key)
            except Exception:
                pass
            continue

        remaining = 1.0
        if flush_after is not None:
            remaining = max(1.0, (flush_after - now).total_seconds())

        with _lock:
            if key in _timers:
                continue
            _buckets[key] = batch
            timer = threading.Timer(remaining, _flush, args=(key, ws))
            timer.daemon = True
            _timers[key] = timer
            timer.start()
        _log.debug(
            "Restored inbox_distributed debounce key=%s remaining=%.1fs",
            key,
            remaining,
        )
