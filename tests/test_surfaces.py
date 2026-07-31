"""Tests for MONOS surface tokens."""

from __future__ import annotations

from PySide6.QtGui import QColor

from monostudio.ui_qt.elevation import (
    ELEVATION_LIGHTNESS_STEP,
    depress,
    elevate,
    surface_lightness,
)
from monostudio.ui_qt.surfaces import (
    SURFACE_APP,
    SURFACE_CARD,
    SURFACE_DIALOG,
    SURFACE_DERIVATION,
    SURFACE_ELEVATION_LEVEL,
    SURFACE_FIELD,
    SURFACE_POPUP,
    SURFACE_TOOLTIP,
    build_semantic_surfaces,
    inject_design_system_tokens,
    surface_color,
    surface_elevation_level,
    surface_parent,
)


def _base_tokens() -> dict:
    return {
        "bg": "#0f1016",
        "chroma": "#ffffff",
        "border_idle_a": 0.08,
        "border_hover_a": 0.14,
    }


def _lightness(hex_color: str) -> float:
    return surface_lightness(hex_color)


def test_elevation_levels() -> None:
    assert surface_elevation_level(SURFACE_FIELD) == -1
    assert surface_elevation_level(SURFACE_DIALOG) == 1
    assert surface_elevation_level(SURFACE_CARD) == 1
    assert surface_elevation_level(SURFACE_POPUP) == 2
    assert surface_elevation_level(SURFACE_TOOLTIP) == 3


def test_derivation_graph() -> None:
    assert surface_parent(SURFACE_DIALOG) == SURFACE_APP
    assert surface_parent(SURFACE_CARD) == SURFACE_APP
    assert surface_parent(SURFACE_FIELD) == SURFACE_DIALOG
    assert surface_parent(SURFACE_POPUP) == SURFACE_DIALOG
    assert surface_parent(SURFACE_TOOLTIP) == SURFACE_POPUP


def test_field_dialog_popup_hierarchy() -> None:
    s = build_semantic_surfaces("#0f1016", step=ELEVATION_LIGHTNESS_STEP)
    assert _lightness(s[SURFACE_FIELD]) < _lightness(s[SURFACE_DIALOG])
    assert _lightness(s[SURFACE_DIALOG]) < _lightness(s[SURFACE_POPUP])
    assert _lightness(s[SURFACE_POPUP]) < _lightness(s[SURFACE_TOOLTIP])


def test_popup_one_step_above_dialog() -> None:
    s = build_semantic_surfaces("#0f1016", step=ELEVATION_LIGHTNESS_STEP)
    assert s[SURFACE_POPUP] == elevate(s[SURFACE_DIALOG], 1, step=ELEVATION_LIGHTNESS_STEP)
    assert surface_parent(SURFACE_POPUP) == SURFACE_DIALOG
    assert surface_parent(SURFACE_POPUP) != SURFACE_CARD  # popup parent is dialog, not card


def test_field_depressed_from_dialog() -> None:
    s = build_semantic_surfaces("#0f1016", step=ELEVATION_LIGHTNESS_STEP)
    assert s[SURFACE_FIELD] == depress(s[SURFACE_DIALOG], 1, step=ELEVATION_LIGHTNESS_STEP)


def test_card_same_tier_as_dialog() -> None:
    s = build_semantic_surfaces("#0f1016", step=ELEVATION_LIGHTNESS_STEP)
    assert s[SURFACE_CARD] == s[SURFACE_DIALOG]


def test_inject_without_hardcoded_field() -> None:
    tokens = _base_tokens()
    inject_design_system_tokens(tokens, theme="dark")
    assert tokens[SURFACE_FIELD] == depress(tokens[SURFACE_DIALOG], 1, step=ELEVATION_LIGHTNESS_STEP)
    assert surface_color(SURFACE_POPUP, tokens) == elevate(
        tokens[SURFACE_DIALOG], 1, step=ELEVATION_LIGHTNESS_STEP
    )


def test_legacy_aliases() -> None:
    tokens = _base_tokens()
    inject_design_system_tokens(tokens, theme="dark")
    assert tokens["panel"] == tokens[SURFACE_DIALOG]
    assert tokens["field"] == tokens[SURFACE_FIELD]
    assert tokens["field_readonly"] == tokens[SURFACE_CARD]


def test_step_tuning_shifts_hierarchy() -> None:
    a = build_semantic_surfaces("#0f1016", step=0.04)
    b = build_semantic_surfaces("#0f1016", step=0.05)
    assert _lightness(b[SURFACE_POPUP]) > _lightness(a[SURFACE_POPUP])
    assert _lightness(b[SURFACE_FIELD]) < _lightness(b[SURFACE_DIALOG])
