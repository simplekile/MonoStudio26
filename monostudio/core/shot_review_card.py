"""Derive render/review summary for shot cards in Main View review mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from monostudio.core.item_comments import count_open_notes, read_item_comments_for_department
from monostudio.core.item_status import _status_json_path, read_item_status_overrides
from monostudio.core.production_status import ProductionStatusRegistry, aggregate_status_id_for_item
from monostudio.core.review_media import _collect_sequence_dirs_in_root, _collect_videos_in_root, _mtime_ns
from monostudio.core.sequence_preview import _sequence_roots_by_priority, work_file_folder_name_candidates


@dataclass(frozen=True)
class RenderCardSummary:
    has_render: bool
    render_date: datetime | None


@dataclass(frozen=True)
class ReviewCardSummary:
    has_review: bool
    review_date: datetime | None
    has_notes: bool
    note_count: int
    has_media: bool
    has_review_status: bool


def format_review_card_date(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    try:
        local = dt.astimezone() if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone()
        return local.strftime("%b %d")
    except (OSError, OverflowError, ValueError):
        return "—"


def _parse_iso_ts(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _latest_note_ts(item_root: Path, department_id: str | None) -> datetime | None:
    best: datetime | None = None
    for entry in read_item_comments_for_department(item_root, department_id):
        dt = _parse_iso_ts(entry.at)
        if dt is None:
            continue
        if best is None or dt > best:
            best = dt
    return best


def _latest_media_mtime_ns(work_path: Path | None, work_file_path: Path | None) -> int:
    if work_path is None or not work_path.is_dir():
        return 0
    names = work_file_folder_name_candidates(work_file_path)
    best = 0
    for root in _sequence_roots_by_priority(work_path):
        for vid in _collect_videos_in_root(root, names):
            best = max(best, _mtime_ns(vid))
        for folder in _collect_sequence_dirs_in_root(root, names):
            best = max(best, _mtime_ns(folder))
    return best


def _ns_to_local_dt(ns: int) -> datetime | None:
    if ns <= 0:
        return None
    try:
        return datetime.fromtimestamp(ns / 1_000_000_000)
    except (OSError, OverflowError, ValueError):
        return None


def _status_json_mtime(item_root: Path) -> datetime | None:
    path = _status_json_path(item_root)
    try:
        if not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _department_status_id(
    ref: object,
    department_id: str | None,
    *,
    registry: ProductionStatusRegistry,
    hidden_departments: set[str] | None = None,
) -> str:
    return aggregate_status_id_for_item(
        ref,  # type: ignore[arg-type]
        active_department=department_id,
        hidden_departments=hidden_departments or set(),
        registry=registry,
    )


def resolve_render_summary(
    work_path: Path | None,
    work_file_path: Path | None,
) -> RenderCardSummary:
    ns = _latest_media_mtime_ns(work_path, work_file_path)
    dt = _ns_to_local_dt(ns)
    return RenderCardSummary(has_render=ns > 0, render_date=dt)


def resolve_review_summary(
    *,
    item_root: Path,
    work_path: Path | None,
    work_file_path: Path | None,
    department_id: str | None,
    registry: ProductionStatusRegistry,
    ref: object | None = None,
    hidden_departments: set[str] | None = None,
) -> ReviewCardSummary:
    dept = (department_id or "").strip() or None
    entries = read_item_comments_for_department(item_root, dept)
    has_notes = bool(entries)
    note_count = count_open_notes(item_root, dept)
    note_ts = _latest_note_ts(item_root, dept)

    media_ns = _latest_media_mtime_ns(work_path, work_file_path)
    has_media = media_ns > 0
    media_dt = _ns_to_local_dt(media_ns)

    has_review_status = False
    status_dt: datetime | None = None
    sid = ""
    if ref is not None:
        sid = _department_status_id(
            ref,
            dept,
            registry=registry,
            hidden_departments=hidden_departments,
        )
    elif dept:
        overrides = read_item_status_overrides(item_root, [dept])
        sid = overrides.get(dept, "")
    if sid and registry.category_for(sid) == "review":
        has_review_status = True
        status_dt = _status_json_mtime(item_root)

    has_review = has_notes or has_media or has_review_status

    candidates: list[datetime] = []
    if note_ts is not None:
        candidates.append(note_ts)
    if media_dt is not None:
        candidates.append(media_dt)
    if has_review_status and status_dt is not None:
        candidates.append(status_dt)

    review_date = max(candidates) if candidates else None
    return ReviewCardSummary(
        has_review=has_review,
        review_date=review_date,
        has_notes=has_notes,
        note_count=note_count,
        has_media=has_media,
        has_review_status=has_review_status,
    )
