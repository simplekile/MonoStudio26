"""Tests for pipeline snapshot layer (Sprint 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from monostudio.core.models import Asset, Department, DccWorkState
from monostudio.ui_qt.pipeline_presentation_model import PipelinePresentationModel
from monostudio.ui_qt.pipeline_snapshot import StatusChip
from monostudio.ui_qt.pipeline_snapshot_builder import SnapshotBuildContext, build_row_snapshot
from monostudio.ui_qt.pipeline_snapshot_store import PipelineSnapshotStore, SnapshotFacet
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind


@pytest.fixture(scope="module", autouse=True)
def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _view_item(tmp_path: Path) -> ViewItem:
    root = tmp_path / "assets" / "char" / "hero"
    work = root / "model" / "work"
    work.mkdir(parents=True)
    wf = work / "hero_model_v001.ma"
    wf.write_text("x")
    asset = Asset(
        name="hero",
        path=root,
        asset_type="char",
        departments=(
            Department(
                name="model",
                path=root / "model",
                work_path=work,
                publish_path=root / "model" / "publish",
                work_exists=True,
                work_file_exists=True,
                work_file_dcc="maya",
                work_file_dccs=("maya",),
                publish_exists=False,
                latest_publish_version=None,
                publish_version_count=0,
            ),
        ),
        dcc_work_states=(
            (("model", "maya"), DccWorkState(work_file_path=wf, work_folder_exists=True)),
        ),
    )
    return ViewItem(
        kind=ViewItemKind.ASSET,
        name="hero",
        type_badge="char",
        path=root,
        type_folder="char",
        ref=asset,
    )


def test_build_row_snapshot_has_dcc_stack(tmp_path: Path) -> None:
    item = _view_item(tmp_path)
    ctx = SnapshotBuildContext(active_department="model")
    snap = build_row_snapshot(item, ctx)
    assert snap.display_name == "hero"
    assert len(snap.dcc_stack) >= 1
    assert snap.dcc_stack[0].dcc_id == "maya"


def test_snapshot_variant_map_for_qml(tmp_path: Path) -> None:
    item = _view_item(tmp_path)
    ctx = SnapshotBuildContext(
        active_department="model",
        status_for_item=lambda _i: StatusChip(label="IN PROGRESS", color_hex="#f59e0b", status_key="progress"),
    )
    snap = build_row_snapshot(item, ctx)
    m = snap.to_variant_map()
    assert m["displayName"] == "hero"
    assert m["statusLabel"] == "IN PROGRESS"
    assert "maya" in m["dccNames"]


def test_store_invalidates_path(tmp_path: Path) -> None:
    item = _view_item(tmp_path)
    store = PipelineSnapshotStore()
    store.set_context(SnapshotBuildContext(active_department="model"))
    s1 = store.snapshot_for(item)
    store.invalidate_path(str(item.path), SnapshotFacet.STATUS)
    assert str(item.path) in store.paths_needing_rebuild()
    s2 = store.snapshot_for(item)
    assert s1.path == s2.path


def test_presentation_model_roles(tmp_path: Path) -> None:
    item = _view_item(tmp_path)
    model = PipelinePresentationModel()
    model.snapshot_store.set_context(SnapshotBuildContext(active_department="model"))
    model.set_items([item])
    ix = model.index(0)
    roles = {v.decode(): k for k, v in model.roleNames().items()}
    path_val = model.data(ix, roles["path"])
    assert path_val
    snap_map = model.data(ix, roles["snapshot"])
    assert isinstance(snap_map, dict)
    assert snap_map.get("displayName") == "hero"
    assert model.rowCount() == 1
