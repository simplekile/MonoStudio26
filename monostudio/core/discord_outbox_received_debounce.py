"""Debounce outbox_received Discord webhooks — batch, reconcile on disk, persist outbox."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monostudio.core.discord_inbox_debounce import resolve_project_root
from monostudio.core.discord_outbox import (
    list_outbox_received_entries,
    remove_outbox_received_entry,
    upsert_outbox_received_entry,
)
from monostudio.core.outbox_reader import (
    META_KEY_ADDED_AT,
    get_outbox_root,
    read_outbox_meta,
)

_log = logging.getLogger("monostudio.discord_outbox_received_debounce")

OUTBOX_RECEIVED_DEBOUNCE_S = 45.0


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
    date_str: str,
) -> str:
    ws = str(workspace_root.resolve())
    return (
        f"outbox|{ws}|{(project_name or '').strip()}|"
        f"{(source or '').strip().lower()}|{(date_str or '').strip()}"
    )


def reconcile_outbox_received(
    project_root: Path | str,
    *,
    source: str,
    date_str: str,
    window_started_at: str,
    pending_names: list[str],
) -> tuple[int, list[str]]:
    root = Path(project_root).resolve()
    src = (source or "client").strip().lower()
    date = (date_str or "").strip()
    if not date:
        return 0, []

    outbox_root = get_outbox_root(root)
    date_dir = outbox_root / src / date
    if not date_dir.is_dir():
        return 0, []

    window_start = _parse_iso_dt(window_started_at)
    meta = read_outbox_meta(root)
    prefix = f"{src}/{date}/"
    names: list[str] = []
    seen: set[str] = set()

    if window_start is not None:
        for rel, entry in meta.items():
            if not isinstance(entry, dict):
                continue
            rel_norm = str(rel or "").replace("\\", "/").strip()
            if not rel_norm.startswith(prefix):
                continue
            added = _parse_iso_dt(str(entry.get(META_KEY_ADDED_AT) or ""))
            if added is None or added < window_start:
                continue
            full = outbox_root / rel_norm
            try:
                if not full.exists():
                    continue
            except OSError:
                continue
            name = full.name
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    for raw in pending_names:
        n = str(raw or "").strip()
        if not n or n in seen:
            continue
        candidate = date_dir / n
        try:
            if candidate.exists():
                seen.add(n)
                names.append(n)
        except OSError:
            continue

    return len(names), names


def _format_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_last_added_at(
    project_root: Path,
    *,
    source: str,
    date_str: str,
    names: list[str],
    fallback: str = "",
) -> str:
    root = Path(project_root).resolve()
    src = (source or "client").strip().lower()
    date = (date_str or "").strip()
    if not names or not date:
        fb = _parse_iso_dt(fallback)
        return _format_iso_utc(fb) if fb is not None else _utc_now_iso()

    outbox_root = get_outbox_root(root)
    name_set = {str(n).strip() for n in names if str(n).strip()}
    meta = read_outbox_meta(root)
    prefix = f"{src}/{date}/"
    latest: datetime | None = None

    for rel, entry in meta.items():
        if not isinstance(entry, dict):
            continue
        rel_norm = str(rel or "").replace("\\", "/").strip()
        if not rel_norm.startswith(prefix):
            continue
        if Path(rel_norm).name not in name_set:
            continue
        added = _parse_iso_dt(str(entry.get(META_KEY_ADDED_AT) or ""))
        if added is not None and (latest is None or added > latest):
            latest = added

    date_dir = outbox_root / src / date
    for name in name_set:
        candidate = date_dir / name
        try:
            if not candidate.exists():
                continue
            mtime = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            if latest is None or mtime > latest:
                latest = mtime
        except OSError:
            continue

    if latest is not None:
        return _format_iso_utc(latest)
    fb = _parse_iso_dt(fallback)
    return _format_iso_utc(fb) if fb is not None else _utc_now_iso()


@dataclass
class _OutboxBatch:
    workspace_root: Path
    project_root: Path
    project_name: str
    actor_name: str
    source: str
    date_str: str
    window_started_at: str = ""
    last_added_at: str = ""
    batch_id: str = ""
    pending_names: list[str] = field(default_factory=list)

    def extend(self, file_names: list[str]) -> None:
        seen = set(self.pending_names)
        for raw in file_names:
            n = str(raw or "").strip()
            if n and n not in seen:
                seen.add(n)
                self.pending_names.append(n)


_lock = threading.Lock()
_buckets: dict[str, _OutboxBatch] = {}
_timers: dict[str, threading.Timer] = {}


def _entry_to_batch(entry: dict) -> _OutboxBatch | None:
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
    names_raw = entry.get("pending_names")
    names = [str(n).strip() for n in names_raw if str(n).strip()] if isinstance(names_raw, list) else []
    return _OutboxBatch(
        workspace_root=ws,
        project_root=project_root,
        project_name=str(entry.get("project_name") or project_root.name).strip(),
        actor_name=str(entry.get("actor_name") or "").strip(),
        source=str(entry.get("source") or "client").strip().lower(),
        date_str=str(entry.get("date_str") or "").strip(),
        window_started_at=str(entry.get("window_started_at") or _utc_now_iso()).strip(),
        last_added_at=str(entry.get("last_added_at") or "").strip(),
        batch_id=str(entry.get("batch_id") or "").strip(),
        pending_names=names,
    )


def _dispatch_reconciled_batch(batch: _OutboxBatch, *, bucket_key: str) -> bool:
    count, names = reconcile_outbox_received(
        batch.project_root,
        source=batch.source,
        date_str=batch.date_str,
        window_started_at=batch.window_started_at,
        pending_names=batch.pending_names,
    )
    if count <= 0:
        _log.debug(
            "Discord outbox_received skipped after reconcile (empty) source=%s date=%s",
            batch.source,
            batch.date_str,
        )
        try:
            remove_outbox_received_entry(batch.workspace_root, bucket_key)
        except Exception:
            pass
        return False

    from monostudio.core.discord_webhook import dispatch_discord_event

    batch_id = (batch.batch_id or bucket_key).strip()
    last_added_at = _resolve_last_added_at(
        batch.project_root,
        source=batch.source,
        date_str=batch.date_str,
        names=names,
        fallback=batch.last_added_at or batch.window_started_at,
    )
    dispatch_discord_event(
        batch.workspace_root,
        "outbox_received",
        {
            "actor_name": batch.actor_name,
            "count": count,
            "source": batch.source,
            "date_str": batch.date_str,
            "file_names": names,
            "project_name": batch.project_name,
            "batch_id": batch_id,
            "last_added_at": last_added_at,
        },
        dedupe_key=f"outbox_received:{batch_id}",
        project_root=batch.project_root,
    )
    try:
        remove_outbox_received_entry(batch.workspace_root, bucket_key)
    except Exception:
        pass
    _log.debug(
        "Discord outbox_received flushed count=%s source=%s date=%s (reconciled)",
        count,
        batch.source,
        batch.date_str,
    )
    return True


def _outbox_entry_for_key(workspace_root: Path, bucket_key: str) -> dict[str, Any] | None:
    key = (bucket_key or "").strip()
    if not key:
        return None
    for entry in list_outbox_received_entries(workspace_root):
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
                remove_outbox_received_entry(ws, key)
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
    batch: _OutboxBatch,
    *,
    bucket_key: str,
    flush_after: str,
) -> None:
    try:
        upsert_outbox_received_entry(
            batch.workspace_root,
            bucket_key=bucket_key,
            project_root=batch.project_root,
            project_name=batch.project_name,
            actor_name=batch.actor_name,
            source=batch.source,
            date_str=batch.date_str,
            window_started_at=batch.window_started_at,
            last_added_at=batch.last_added_at,
            batch_id=batch.batch_id,
            pending_names=batch.pending_names,
            flush_after=flush_after,
        )
    except Exception:
        _log.debug("discord outbox_received persist failed", exc_info=True)


def enqueue_outbox_received(
    workspace_root: Path | str | None,
    *,
    project_root: Path | str | None,
    project_name: str,
    actor_name: str,
    source: str,
    date_str: str,
    count: int,
    file_names: list[str],
    debounce_s: float | None = None,
) -> None:
    if workspace_root is None or count <= 0:
        return
    ws = Path(workspace_root).resolve()
    proj = Path(project_root).resolve() if project_root is not None else None
    if proj is None or not proj.is_dir():
        proj = resolve_project_root(ws, project_name)
    if proj is None:
        _log.debug("outbox_received enqueue skipped — project not resolved for %s", project_name)
        return

    delay = OUTBOX_RECEIVED_DEBOUNCE_S if debounce_s is None else max(1.0, float(debounce_s))
    key = _bucket_key(ws, project_name, source, date_str)
    flush_after_dt = datetime.now(timezone.utc).timestamp() + delay
    flush_after = datetime.fromtimestamp(flush_after_dt, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _lock:
        batch = _buckets.get(key)
        if batch is None:
            now = _utc_now_iso()
            batch = _OutboxBatch(
                workspace_root=ws,
                project_root=proj,
                project_name=(project_name or proj.name).strip(),
                actor_name=(actor_name or "").strip(),
                source=(source or "client").strip().lower(),
                date_str=(date_str or "").strip(),
                window_started_at=now,
                batch_id=now,
            )
            _buckets[key] = batch
        elif actor_name:
            batch.actor_name = actor_name.strip()
        batch.extend(file_names)
        batch.last_added_at = _utc_now_iso()

        _persist_batch(batch, bucket_key=key, flush_after=flush_after)

        old_timer = _timers.pop(key, None)
        if old_timer is not None:
            old_timer.cancel()
        timer = threading.Timer(delay, _flush, args=(key, ws))
        timer.daemon = True
        _timers[key] = timer
        timer.start()


def flush_all_pending_outbox_received(workspace_root: Path | str | None) -> int:
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

    for entry in list_outbox_received_entries(ws):
        key = str(entry.get("bucket_key") or "").strip()
        if not key or key in flushed_keys:
            continue
        if _flush_from_outbox_entry(entry):
            sent += 1

    return sent


def restore_outbox_received_outbox(workspace_root: Path | str | None) -> None:
    if workspace_root is None:
        return
    ws = Path(workspace_root).resolve()
    now = datetime.now(timezone.utc)

    for entry in list_outbox_received_entries(ws):
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
                remove_outbox_received_entry(ws, key)
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
            "Restored outbox_received debounce key=%s remaining=%.1fs",
            key,
            remaining,
        )
