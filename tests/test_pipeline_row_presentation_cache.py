"""Tests for pipeline row presentation cache (Phase 0)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from monostudio.core.models import Asset, Department, DccWorkState
from monostudio.ui_qt.pipeline_row_presentation_cache import (
    PipelineRowPresentationCache,
    compute_dcc_badges,
)


@pytest.fixture(scope="module", autouse=True)
def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _asset(tmp_path: Path, *, dep: str = "model", work_file: Path | None = None) -> Asset:
    root = tmp_path / "assets" / "char" / "hero"
    work = root / dep / "work"
    work.mkdir(parents=True, exist_ok=True)
    states: tuple = ()
    if work_file is not None:
        states = (
            (
                (dep, "maya"),
                DccWorkState(work_file_path=work_file, work_folder_exists=True),
            ),
        )
    return Asset(
        name="hero",
        path=root,
        asset_type="char",
        departments=(
            Department(
                name=dep,
                path=root / dep,
                work_path=work,
                publish_path=root / dep / "publish",
                work_exists=True,
                work_file_exists=work_file is not None,
                work_file_dcc="maya",
                work_file_dccs=("maya",),
                publish_exists=False,
                latest_publish_version=None,
                publish_version_count=0,
            ),
        ),
        dcc_work_states=states,
    )


def test_compute_dcc_badges_without_department_filter(tmp_path: Path) -> None:
    """No active department => all dept DCC states visible (matches grid delegate)."""
    wf = tmp_path / "assets" / "char" / "hero" / "model" / "work" / "hero_model_v001.ma"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text("x")
    asset = _asset(tmp_path, work_file=wf)
    badges = compute_dcc_badges(asset, active_department=None, dept_registry=None)
    assert any(dcc_id == "maya" for _ic, dcc_id, _st in badges)


def test_cache_reuses_health_entry(tmp_path: Path) -> None:
    wf = tmp_path / "assets" / "char" / "hero" / "model" / "work" / "hero_model_v001.ma"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text("x")
    asset = _asset(tmp_path, work_file=wf)
    cache = PipelineRowPresentationCache()
    path = str(asset.path)
    h1 = cache.health_for(asset, path=path, active_department="model", active_dcc_id="maya")
    h2 = cache.health_for(asset, path=path, active_department="model", active_dcc_id="maya")
    assert h1 is h2


def test_invalidate_path_clears_dcc_cache(tmp_path: Path) -> None:
    wf = tmp_path / "assets" / "char" / "hero" / "model" / "work" / "hero_model_v001.ma"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text("x")
    asset = _asset(tmp_path, work_file=wf)
    cache = PipelineRowPresentationCache()
    path = str(asset.path.resolve())
    b1 = cache.dcc_badges_for(asset, path=path, active_department="model", dept_registry=None)
    cache.invalidate_path(path)
    b2 = cache.dcc_badges_for(asset, path=path, active_department="model", dept_registry=None)
    assert b1 == b2
    assert len(b1) >= 1


def test_dcc_badges_exists_from_work_state(tmp_path: Path) -> None:
    wf = tmp_path / "assets" / "char" / "hero" / "model" / "work" / "hero_model_v001.ma"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text("x")
    asset = _asset(tmp_path, work_file=wf)
    badges = compute_dcc_badges(asset, active_department="model", dept_registry=None)
    assert any(dcc_id == "maya" and status == "exists" for _ic, dcc_id, status in badges)
