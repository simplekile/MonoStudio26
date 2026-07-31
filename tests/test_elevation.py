"""Tests for MONOS elevation system."""

from __future__ import annotations

from PySide6.QtGui import QColor

from monostudio.ui_qt.elevation import (
    border_hover_alpha,
    border_idle_alpha,
    build_elevation_token_set,
    depress,
    elevate,
    surface_at_level,
    surface_lightness,
)
from monostudio.ui_qt.surfaces import SURFACE_DIALOG, SURFACE_POPUP, inject_design_system_tokens


def test_surfaces_increase_in_lightness_only() -> None:
    base = "#0f1016"
    lights: list[float] = []
    saturations: list[float] = []
    for level in range(4):
        c = QColor(surface_at_level(base, level))
        _h, s, lightness, _a = c.getHslF()
        lights.append(lightness)
        saturations.append(s)
    assert lights == sorted(lights)
    assert lights[-1] - lights[0] >= 0.10
    assert max(saturations) - min(saturations) < 0.03


def test_elevate_and_depress() -> None:
    base = "#0f1016"
    up = elevate(base, 1)
    down = depress(base, 1)
    assert surface_lightness(up) > surface_lightness(base)
    assert surface_lightness(down) < surface_lightness(base)


def test_border_alpha_increases_with_level() -> None:
    alphas = [border_idle_alpha(level) for level in range(4)]
    assert alphas == sorted(alphas)
    assert border_hover_alpha(2) > border_idle_alpha(2)


def test_inject_design_system_tokens() -> None:
    tokens = {"bg": "#0f1016", "chroma": "#ffffff", "border_idle_a": 0.08, "border_hover_a": 0.14}
    inject_design_system_tokens(tokens, theme="dark")
    assert tokens["panel"] == tokens[SURFACE_DIALOG]
    assert tokens[SURFACE_POPUP] == elevate(tokens[SURFACE_DIALOG], 1)
    assert "elev_border_idle_2" in tokens


def test_build_elevation_token_set_keys() -> None:
    keys = build_elevation_token_set("#09090b").keys()
    assert "elev_0" in keys and "elev_border_hover_3" in keys
