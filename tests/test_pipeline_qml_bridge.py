"""Tests for Sprint 2 QML bridge and view host."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.pipeline_qml_bridge import PipelineQmlBridge, read_pipeline_use_qml_grid
from monostudio.ui_qt.pipeline_view_host import PipelineGridViewHost


@pytest.fixture(scope="module", autouse=True)
def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_read_pipeline_use_qml_grid_env(monkeypatch) -> None:
    settings = QSettings()
    monkeypatch.delenv("MONOS_PIPELINE_USE_QML_GRID", raising=False)
    settings.setValue("main_view/use_qml_grid", False)
    assert read_pipeline_use_qml_grid(settings) is False
    monkeypatch.setenv("MONOS_PIPELINE_USE_QML_GRID", "1")
    assert read_pipeline_use_qml_grid(settings) is True


def test_bridge_row_activated_signal() -> None:
    bridge = PipelineQmlBridge()
    seen: list[int] = []
    bridge.rowActivated.connect(seen.append)
    bridge.activateRow(3)
    assert seen == [3]


def test_pipeline_grid_view_host_loads() -> None:
    host = PipelineGridViewHost()
    assert host.is_ready()
    assert host.presentation_model is not None
    assert host.bridge is not None
