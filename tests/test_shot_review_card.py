"""Tests for shot review card summary resolver."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from monostudio.core.item_comments import write_item_comments_for_department, new_comment_entry
from monostudio.core.item_status import set_department_status_override
from monostudio.core.production_status import load_production_status_registry
from monostudio.core.shot_review_card import (
    format_review_card_date,
    merge_department_review_render,
    resolve_render_summary,
    resolve_review_summary,
    scan_department_review_light,
)


def test_resolve_render_summary_empty(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    summary = resolve_render_summary(work, None)
    assert summary.has_render is False
    assert summary.render_date is None


def test_resolve_render_summary_sequence(tmp_path: Path) -> None:
    work = tmp_path / "work"
    render = work / "render" / "shot_v001"
    render.mkdir(parents=True)
    frame = render / "shot.1001.png"
    frame.write_bytes(b"x")
    summary = resolve_render_summary(work, None)
    assert summary.has_render is True
    assert summary.render_date is not None


def test_resolve_review_summary_notes_only(tmp_path: Path) -> None:
    shot = tmp_path / "shot_010"
    shot.mkdir()
    entry = new_comment_entry("Fix lighting", department="lighting")
    write_item_comments_for_department(shot, "lighting", [entry])
    reg = load_production_status_registry(tmp_path)
    summary = resolve_review_summary(
        item_root=shot,
        work_path=None,
        work_file_path=None,
        department_id="lighting",
        registry=reg,
    )
    assert summary.has_review is True
    assert summary.has_notes is True
    assert summary.note_count == 1
    assert summary.review_date is not None


def test_resolve_review_summary_status_category(tmp_path: Path) -> None:
    shot = tmp_path / "shot_020"
    shot.mkdir()
    set_department_status_override(shot, "comp", "client_review")
    reg = load_production_status_registry(tmp_path)
    summary = resolve_review_summary(
        item_root=shot,
        work_path=None,
        work_file_path=None,
        department_id="comp",
        registry=reg,
    )
    assert summary.has_review is True
    assert summary.has_review_status is True


def test_format_review_card_date_none() -> None:
    assert format_review_card_date(None) == "—"


def test_format_review_card_date_local() -> None:
    dt = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)
    assert format_review_card_date(dt) == "Jun 18"


def test_scan_light_then_merge_render(tmp_path: Path) -> None:
    shot = tmp_path / "shot_030"
    work = shot / "01_light" / "work"
    render = work / "render" / "shot_v001"
    render.mkdir(parents=True)
    (render / "shot.1001.png").write_bytes(b"x")
    reg = load_production_status_registry(tmp_path)
    light = scan_department_review_light(
        item_root=shot,
        department_id="light",
        registry=reg,
    )
    assert light.render_scanned is False
    assert light.has_render is False
    merged = merge_department_review_render(light, work, None)
    assert merged.render_scanned is True
    assert merged.has_render is True
    assert merged.has_review is True
    assert merged.has_media is True
