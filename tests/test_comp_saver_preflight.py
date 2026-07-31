"""Tests for Fusion comp open preflight entry (settings + DCC adapter wiring)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from monostudio.core.comp_render_paths import build_comp_saver_spec
from monostudio.core.dcc_fusion import FusionDccAdapter
from monostudio.ui_qt.comp_saver_preflight import (
    fusion_comp_preflight_enabled,
    run_comp_open_preflight,
)


class _FakeSettings:
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled

    def contains(self, key: str) -> bool:
        return key == "integrations/fusion_comp_preflight"

    def value(self, key: str, default: object = None) -> object:
        if key == "integrations/fusion_comp_preflight":
            return self._enabled
        return default


def test_fusion_comp_preflight_enabled_reads_setting() -> None:
    assert fusion_comp_preflight_enabled(_FakeSettings(enabled=True)) is True
    assert fusion_comp_preflight_enabled(_FakeSettings(enabled=False)) is False


def test_run_comp_open_preflight_passes_settings_when_disabled(tmp_path: Path) -> None:
    """Regression: deleting settings before enabled check skipped all preflight."""
    comp = tmp_path / "sh001_comp_v001.comp"
    comp.write_text("Composition { }\n", encoding="utf-8")
    spec = build_comp_saver_spec(
        entity_name="sh001",
        department="comp",
        work_file=comp,
        work_path=comp.parent,
    )
    settings = _FakeSettings(enabled=False)
    received: list[object] = []

    def _capture_enabled(s: object) -> bool:
        received.append(s)
        return False

    import monostudio.ui_qt.comp_saver_preflight as mod

    original = mod.fusion_comp_preflight_enabled
    mod.fusion_comp_preflight_enabled = _capture_enabled
    try:
        result = run_comp_open_preflight(
            comp_path=comp,
            spec=spec,
            entity_name="sh001",
            project_root=tmp_path,
            settings=settings,
        )
    finally:
        mod.fusion_comp_preflight_enabled = original

    assert result == comp
    assert received == [settings]


def test_dcc_fusion_preflight_receives_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    comp = tmp_path / "sh001_comp_v001.comp"
    comp.write_text("Composition { }\n", encoding="utf-8")
    spec = build_comp_saver_spec(
        entity_name="sh001",
        department="comp",
        work_file=comp,
        work_path=comp.parent,
    )
    settings = MagicMock()
    settings.contains.return_value = True
    settings.value.return_value = False

    calls: list[dict] = []

    def _fake_run(**kwargs: object) -> Path:
        calls.append(dict(kwargs))
        return comp

    monkeypatch.setattr(
        "monostudio.ui_qt.comp_saver_preflight.run_comp_open_preflight",
        _fake_run,
    )

    adapter = FusionDccAdapter(
        fusion_executable="",
        repo_root=tmp_path,
        settings=settings,
    )
    ctx = {
        "project_root": str(tmp_path),
        "entity_id": "sh001",
        "comp_render": spec.as_context_dict(),
    }
    out = adapter._maybe_run_saver_preflight(comp, ctx)
    assert out == comp
    assert len(calls) == 1
    assert calls[0]["settings"] is settings
