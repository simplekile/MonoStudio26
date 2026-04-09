"""
Stress test scenarios for MONOS (internal diagnostics only).

- Run only when explicitly invoked (e.g. from Diagnostics menu when MONOS_STRESS=1).
- Scenarios: asset load, filesystem event, thumbnail, interaction.
- Metrics logged via stress_profiler; state restored where possible.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.stress_profiler import (
    enabled,
    get_summary,
    log_summary,
    reset_metrics,
)

if TYPE_CHECKING:
    from monostudio.ui_qt.app_state import AppState
    from monostudio.ui_qt.fs_watcher import FsEventCollector
    from monostudio.ui_qt.main_view import MainView
    from monostudio.ui_qt.thumbnails import ThumbnailManager

logger = logging.getLogger("monos.stress_runner")


def _make_fake_assets(count: int, base_path: Path) -> list:
    """Create fake Asset instances for stress (no disk)."""
    from monostudio.core.models import Asset, Department

    assets = []
    for i in range(count):
        asset_path = base_path / "assets" / "char" / f"stress_asset_{i:05d}"
        dept = Department(
            name="model",
            path=asset_path / "01_model",
            work_path=asset_path / "01_model" / "work",
            publish_path=asset_path / "01_model" / "publish",
            work_exists=False,
            work_file_exists=False,
            work_file_dcc=None,
            work_file_dccs=(),
            publish_exists=False,
            latest_publish_version=None,
            publish_version_count=0,
        )
        assets.append(
            Asset(
                asset_type="character",
                name=f"stress_asset_{i:05d}",
                path=asset_path,
                departments=(dept,),
                dcc_work_states=(),
                status_overrides=(),
            )
        )
    return assets


def run_asset_load_stress(
    app_state: "AppState",
    main_view: "MainView | None",
    count: int = 1000,
) -> dict:
    """
    Simulate 500–3000 assets; trigger grid rendering; measure scroll/selection.
    Saves current state, injects fake assets, runs, then restores. Returns summary.
    """
    if not enabled():
        logger.warning("Stress not enabled; set MONOS_STRESS=1")
        return {}
    reset_metrics()
    base = Path("/stress_fixture")
    fake = _make_fake_assets(count, base)
    fake_dict = {str(a.path): a for a in fake}
    prev_assets = dict(app_state.assets())
    prev_shots = dict(app_state.shots())
    try:
        app_state.update_assets(fake_dict)
        app_state.update_shots({})
        app_state.commit_immediate()
        if main_view is not None:
            QApplication.processEvents()
            t0 = time.perf_counter()
            for _ in range(3):
                QApplication.processEvents()
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("Asset load stress: %s assets, processEvents x3 took %.1f ms", count, elapsed)
    finally:
        app_state.update_assets(prev_assets)
        app_state.update_shots(prev_shots)
        app_state.commit_immediate()
    log_summary("Asset load stress")
    return get_summary()


def run_fs_event_stress(
    fs_collector: "FsEventCollector",
    n_events: int = 500,
) -> dict:
    """
    Simulate rapid file changes; trigger watcher path collection; validate debounce.
    Does not require real paths; collector will normalize and classify.
    """
    if not enabled():
        return {}
    reset_metrics()
    fake_base = Path("/stress_fs/project/assets/char/asset_001")
    for i in range(n_events):
        fs_collector.add_path(str(fake_base / f"file_{i}.txt"))
    QApplication.processEvents()
    log_summary("FS event stress")
    return get_summary()


def run_thumbnail_stress(
    thumbnail_manager: "ThumbnailManager",
    asset_ids: list[str],
) -> dict:
    """
    Force cache misses; request many thumbnails; measure UI responsiveness.
    Pass list of asset path strings (can be non-existent to force misses).
    """
    if not enabled():
        return {}
    reset_metrics()
    for aid in asset_ids[:500]:
        thumbnail_manager.request_thumbnail(aid)
    QApplication.processEvents()
    log_summary("Thumbnail stress")
    return get_summary()


def run_interaction_stress(
    app_state: "AppState",
    main_view: "MainView | None",
    n_selections: int = 100,
) -> dict:
    """
    Rapid selection changes and/or filter toggles; measure diff-based update cost.
    """
    if not enabled():
        return {}
    reset_metrics()
    assets = list(app_state.assets().values())
    if not assets:
        logger.info("Interaction stress: no assets, skipping selection stress")
        return get_summary()
    ids = [str(a.path) for a in assets]
    for i in range(min(n_selections, len(ids))):
        app_state.set_selection(ids[i % len(ids)])
        if i % 10 == 0:
            QApplication.processEvents()
    app_state.set_selection(None)
    QApplication.processEvents()
    log_summary("Interaction stress")
    return get_summary()
