"""MONOS Surface Tokens — semantic fills every component must use.

Each token maps to a **nominal elevation level** and is derived via ``elevate()`` /
``depress()`` — never hardcoded hex.

Hierarchy (dark UI)::

    E-1  SURFACE_FIELD   = depress(SURFACE_DIALOG, 1)
     E0  SURFACE_APP
     E1  SURFACE_DIALOG  = elevate(SURFACE_APP, 1)
     E1  SURFACE_CARD    = elevate(SURFACE_APP, 1)   # same tier as dialog
     E2  SURFACE_POPUP   = elevate(SURFACE_DIALOG, 1)  # one step above dialog
     E3  SURFACE_TOOLTIP = elevate(SURFACE_POPUP, 1)

See ``.cursor/rules/plan_surface_tokens_v1.mdc``.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

from monostudio.ui_qt.elevation import (
    ELEVATION_APP,
    ELEVATION_DIALOG,
    ELEVATION_FLOATING,
    ELEVATION_RECESSED,
    ELEVATION_TRANSIENT,
    ELEVATION_LIGHTNESS_STEP,
    depress,
    elevate,
    elevation_stroke_color,
    inject_elevation_into_tokens,
)

# ---------------------------------------------------------------------------
# Semantic surface token keys
# ---------------------------------------------------------------------------

SURFACE_APP = "surface_app"
SURFACE_DIALOG = "surface_dialog"
SURFACE_CARD = "surface_card"
SURFACE_FIELD = "surface_field"
SURFACE_POPUP = "surface_popup"
SURFACE_TOOLTIP = "surface_tooltip"
SURFACE_OVERLAY = "surface_overlay"

ALL_SURFACE_TOKENS: tuple[str, ...] = (
    SURFACE_APP,
    SURFACE_DIALOG,
    SURFACE_CARD,
    SURFACE_FIELD,
    SURFACE_POPUP,
    SURFACE_TOOLTIP,
    SURFACE_OVERLAY,
)

# ---------------------------------------------------------------------------
# Nominal elevation level per token (relationships encoded in code)
# ---------------------------------------------------------------------------

SURFACE_ELEVATION_LEVEL: dict[str, int] = {
    SURFACE_FIELD: ELEVATION_RECESSED,
    SURFACE_APP: ELEVATION_APP,
    SURFACE_DIALOG: ELEVATION_DIALOG,
    SURFACE_CARD: ELEVATION_DIALOG,
    SURFACE_POPUP: ELEVATION_FLOATING,
    SURFACE_TOOLTIP: ELEVATION_TRANSIENT,
}

# How each token is derived from its reference parent (steps, sign).
# (parent_token, steps, direction)  direction: +1 elevate, -1 depress
SURFACE_DERIVATION: dict[str, tuple[str | None, int, int]] = {
    SURFACE_APP: (None, 0, 0),
    SURFACE_DIALOG: (SURFACE_APP, 1, 1),
    SURFACE_CARD: (SURFACE_APP, 1, 1),
    SURFACE_FIELD: (SURFACE_DIALOG, 1, -1),
    SURFACE_POPUP: (SURFACE_DIALOG, 1, 1),
    SURFACE_TOOLTIP: (SURFACE_POPUP, 1, 1),
}

# Ordered list for docs / iteration (not a linear brightness ladder).
SEMANTIC_SURFACE_LADDER: tuple[str, ...] = (
    SURFACE_APP,
    SURFACE_DIALOG,
    SURFACE_CARD,
    SURFACE_FIELD,
    SURFACE_POPUP,
    SURFACE_TOOLTIP,
)

# Back-compat alias: parent of each token in the derivation graph.
SURFACE_PARENT: dict[str, str | None] = {
    token: spec[0] for token, spec in SURFACE_DERIVATION.items()
}

_DEFAULT_OVERLAY_SCRIM_DARK = "rgba(0, 0, 0, 0.50)"
_DEFAULT_OVERLAY_SCRIM_LIGHT = "rgba(0, 0, 0, 0.32)"


def surface_parent(token: str) -> str | None:
    if token not in SURFACE_DERIVATION:
        raise KeyError(f"unknown surface token: {token!r}")
    return SURFACE_DERIVATION[token][0]


def surface_elevation_level(token: str) -> int:
    if token not in SURFACE_ELEVATION_LEVEL:
        raise KeyError(f"unknown surface token: {token!r}")
    return SURFACE_ELEVATION_LEVEL[token]


def surface_ladder_index(token: str) -> int:
    """Border lookup index — uses nominal elevation (0…3)."""
    level = surface_elevation_level(token)
    if level < 0:
        raise KeyError(f"token {token!r} has no positive elevation border level")
    return level


def _apply_derivation(
    parent_hex: str,
    steps: int,
    direction: int,
    *,
    step: float,
) -> str:
    if direction > 0:
        return elevate(parent_hex, steps, step=step)
    if direction < 0:
        return depress(parent_hex, steps, step=step)
    return parent_hex


def build_semantic_surfaces(
    base_app: str,
    *,
    step: float | None = None,
) -> dict[str, str]:
    """Build all semantic surface fills from elevation relationships."""
    per_step = ELEVATION_LIGHTNESS_STEP if step is None else step
    values: dict[str, str] = {SURFACE_APP: base_app}

    # Pass 1: surfaces that only depend on APP.
    for token in (SURFACE_DIALOG, SURFACE_CARD):
        parent, steps, direction = SURFACE_DERIVATION[token]
        assert parent == SURFACE_APP
        values[token] = _apply_derivation(values[SURFACE_APP], steps, direction, step=per_step)

    # Pass 2: field + popup (depend on dialog).
    for token in (SURFACE_FIELD, SURFACE_POPUP):
        parent, steps, direction = SURFACE_DERIVATION[token]
        assert parent == SURFACE_DIALOG
        values[token] = _apply_derivation(values[SURFACE_DIALOG], steps, direction, step=per_step)

    # Pass 3: tooltip (depends on popup).
    parent, steps, direction = SURFACE_DERIVATION[SURFACE_TOOLTIP]
    assert parent == SURFACE_POPUP
    values[SURFACE_TOOLTIP] = _apply_derivation(values[SURFACE_POPUP], steps, direction, step=per_step)

    return values


# Back-compat alias
build_semantic_surface_ladder = build_semantic_surfaces


def inject_surface_tokens(tokens: dict, *, theme: str = "dark") -> None:
    """Map semantic surface tokens from elevation relationships."""
    overlay = tokens.get(
        "overlay_scrim",
        _DEFAULT_OVERLAY_SCRIM_DARK if theme == "dark" else _DEFAULT_OVERLAY_SCRIM_LIGHT,
    )

    step = float(tokens.get("elevation_step", ELEVATION_LIGHTNESS_STEP))
    app = str(tokens.get("bg") or tokens.get("elevation_base") or tokens.get("app_bg") or tokens.get("elev_0"))
    surfaces = build_semantic_surfaces(app, step=step)

    for token, hex_color in surfaces.items():
        tokens[token] = hex_color
    tokens[SURFACE_OVERLAY] = overlay

    # Sync coarse elev_* keys.
    tokens["elev_0"] = surfaces[SURFACE_APP]
    tokens["elev_1"] = surfaces[SURFACE_DIALOG]
    tokens["elev_card"] = surfaces[SURFACE_CARD]
    tokens["elev_2"] = surfaces[SURFACE_POPUP]
    tokens["elev_3"] = surfaces[SURFACE_TOOLTIP]

    tokens["bg"] = tokens[SURFACE_APP]
    tokens["panel"] = tokens[SURFACE_DIALOG]
    tokens["field"] = tokens[SURFACE_FIELD]
    tokens["field_readonly"] = tokens[SURFACE_CARD]


def inject_design_system_tokens(tokens: dict, *, theme: str = "dark") -> None:
    inject_elevation_into_tokens(tokens, theme=theme)
    inject_surface_tokens(tokens, theme=theme)


def build_app_token_stack(
    app_bg: str,
    *,
    theme: str = "dark",
) -> dict:
    tokens: dict = {
        "app_bg": app_bg,
        "bg": app_bg,
        "chroma": "#ffffff" if theme == "dark" else "#18181b",
        "border_idle_a": 0.08 if theme == "dark" else 0.10,
        "border_hover_a": 0.14 if theme == "dark" else 0.16,
        "elevation_step": ELEVATION_LIGHTNESS_STEP,
    }
    inject_design_system_tokens(tokens, theme=theme)
    return tokens


def surface_color(token: str, tokens: dict) -> str:
    if token not in ALL_SURFACE_TOKENS:
        raise KeyError(f"unknown surface token: {token!r}")
    return str(tokens[token])


def surface_qcolor(token: str, tokens: dict) -> QColor:
    return QColor(surface_color(token, tokens))


def surface_border_color(
    token: str,
    tokens: dict,
    *,
    hover: bool = False,
) -> QColor:
    if token in (SURFACE_FIELD, SURFACE_OVERLAY):
        alpha = tokens["border_hover_a"] if hover else tokens["border_idle_a"]
        c = QColor(tokens["chroma"])
        c.setAlpha(int(round(float(alpha) * 255)))
        return c
    if token in SURFACE_ELEVATION_LEVEL and surface_elevation_level(token) >= 0:
        return elevation_stroke_color(surface_elevation_level(token), tokens, hover=hover)
    raise KeyError(f"unknown surface token for border: {token!r}")


def surface_border_rgba(
    token: str,
    tokens: dict,
    *,
    hover: bool = False,
) -> str:
    c = surface_border_color(token, tokens, hover=hover)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha() / 255:.3f})"
