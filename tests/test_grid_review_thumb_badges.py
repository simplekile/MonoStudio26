"""Tests for review-mode grid thumb badges."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from monostudio.core.models import Department, DepartmentReviewIndex, DccWorkState, Shot
from monostudio.core.project_schedule import ProjectSchedule
from monostudio.ui_qt.grid_review_thumb_badges import (
    resolve_grid_review_render_badge,
    resolve_grid_schedule_deadline_badge,
)


def _shot_with_dep(
    tmp_path: Path,
    *,
    dep: str = "anim",
    review_index: DepartmentReviewIndex | None = None,
    work_file: Path | None = None,
) -> Shot:
    root = tmp_path / "shots" / "seq" / "shot_010"
    work = root / dep / "work"
    work.mkdir(parents=True, exist_ok=True)
    idx = review_index or DepartmentReviewIndex()
    shot = Shot(
        name="shot_010",
        path=root,
        departments=(
            Department(
                name=dep,
                path=root / dep,
                work_path=work,
                publish_path=root / dep / "publish",
                work_exists=True,
                work_file_exists=work_file is not None,
                work_file_dcc="blender",
                work_file_dccs=("blender",),
                publish_exists=False,
                latest_publish_version=None,
                publish_version_count=0,
                review_index=idx,
            ),
        ),
    )
    states: tuple = ()
    if work_file is not None:
        states = (
            (
                ("anim", "blender"),
                DccWorkState(work_file_path=work_file, work_folder_exists=True),
            ),
        )
    return Shot(
        name=shot.name,
        path=shot.path,
        departments=shot.departments,
        dcc_work_states=states,
    )


def test_render_badge_none_when_no_media(tmp_path: Path) -> None:
    wf = tmp_path / "shots" / "seq" / "shot_010" / "anim" / "work" / "shot_010_anim_v003.blend"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(b"x")
    shot = _shot_with_dep(tmp_path, work_file=wf)
    badge = resolve_grid_review_render_badge(shot, "anim")
    assert badge.state == "none"
    assert badge.version_text == "—"
    assert "No render" in badge.tooltip


def test_render_badge_current_when_sequence_matches_latest(tmp_path: Path) -> None:
    root = tmp_path / "shots" / "seq" / "shot_010"
    work = root / "anim" / "work"
    wf = work / "shot_010_anim_v003.blend"
    seq = work / "render" / "shot_010_anim_v003"
    seq.mkdir(parents=True)
    (seq / "shot_010_anim_v003.0001.png").write_bytes(b"x")
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(b"x")
    shot = _shot_with_dep(tmp_path, work_file=wf)
    badge = resolve_grid_review_render_badge(shot, "anim")
    assert badge.state == "current"
    assert badge.version_text == "v003"
    assert "matches latest" in badge.tooltip.lower()


def test_render_badge_outdated_when_only_older_sequence(tmp_path: Path) -> None:
    root = tmp_path / "shots" / "seq" / "shot_010"
    work = root / "anim" / "work"
    wf = work / "shot_010_anim_v005.blend"
    seq = work / "render" / "shot_010_anim_v003"
    seq.mkdir(parents=True)
    (seq / "shot_010_anim_v003.0001.png").write_bytes(b"x")
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_bytes(b"x")
    shot = _shot_with_dep(tmp_path, work_file=wf)
    badge = resolve_grid_review_render_badge(shot, "anim")
    assert badge.state == "outdated"
    assert badge.version_text == "v003"
    assert "older preview" in badge.tooltip.lower()


def test_schedule_badge_overdue() -> None:
    from monostudio.core.schedule_planner import PlannedBar, STATUS_PROGRESS

    today = date.today()
    due = today - timedelta(days=2)
    bar = PlannedBar(
        bar_id="k",
        entity_kind="shot",
        entity_name="shot_010",
        entity_rel="shots/seq/shot_010",
        department="anim",
        department_label="Anim",
        start=due - timedelta(days=5),
        due=due,
        source="override",
        status=STATUS_PROGRESS,
        status_id="wip",
        color_hex="#3b82f6",
        overdue=True,
        goal_met=False,
        target_status_id="",
        target_status_label="",
        assignee_id="",
        assignee_ids=(),
        target_workflow_order=0,
    )
    bars = {bar.store_key: bar}
    schedule = ProjectSchedule()
    badge = resolve_grid_schedule_deadline_badge(
        bars,
        schedule,
        entity_kind="shot",
        entity_rel="shots/seq/shot_010",
        active_department="anim",
    )
    assert badge is not None
    assert badge.state == "overdue"
    assert "Overdue" in badge.tooltip


def test_schedule_badge_unscheduled_when_no_plan() -> None:
    badge = resolve_grid_schedule_deadline_badge(
        {},
        ProjectSchedule(),
        entity_kind="shot",
        entity_rel="shots/seq/shot_010",
        active_department="anim",
    )
    assert badge is not None
    assert badge.state == "unscheduled"
