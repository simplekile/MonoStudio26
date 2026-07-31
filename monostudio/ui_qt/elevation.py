"""MONOS Elevation System — depth via surface luminance, not shadow.

Higher elevation → slightly brighter surface (hue/sat unchanged).
Lower elevation → slightly darker surface.

Primary API: ``elevate()`` / ``depress()`` — semantic mapping in ``surfaces.py``.

See ``.cursor/rules/plan_elevation_system_v1.mdc``.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

# Nominal elevation indices (for border alpha lookup).
ELEVATION_RECESSED = -1
ELEVATION_APP = 0
ELEVATION_DIALOG = 1
ELEVATION_FLOATING = 2
ELEVATION_TRANSIENT = 3

ELEVATION_LEVELS = (-1, 0, 1, 2, 3)

# HSL lightness delta for one ``elevate(..., 1)`` / ``depress(..., 1)`` step (~4%).
ELEVATION_LIGHTNESS_STEP = 0.04

# Border chroma alpha per nominal elevation (idle). Index: recessed uses field rules.
_ELEVATION_BORDER_IDLE: dict[int, float] = {
    0: 0.06,
    1: 0.08,
    2: 0.10,
    3: 0.12,
}
ELEVATION_BORDER_HOVER_DELTA = 0.04


def _adjust_lightness(base_hex: str, delta: float) -> str:
    color = QColor(base_hex)
    if not color.isValid():
        raise ValueError(f"invalid base color: {base_hex!r}")
    h, s, lightness, a = color.getHslF()
    if h < 0:
        h = 0.0
    new_l = max(0.0, min(1.0, lightness + delta))
    out = QColor()
    out.setHslF(h, s, new_l, a)
    return out.name(QColor.NameFormat.HexRgb)


def surface_lift(base_hex: str, lightness_delta: float) -> str:
    """Brighten *base_hex* by *lightness_delta* (HSL lightness only)."""
    if lightness_delta <= 0:
        raise ValueError("lightness_delta must be positive")
    return _adjust_lightness(base_hex, lightness_delta)


def surface_depress(base_hex: str, lightness_delta: float) -> str:
    """Darken *base_hex* by *lightness_delta* (HSL lightness only)."""
    if lightness_delta <= 0:
        raise ValueError("lightness_delta must be positive")
    return _adjust_lightness(base_hex, -lightness_delta)


def elevate(base_hex: str, steps: int = 1, *, step: float | None = None) -> str:
    """Raise a surface *steps* tiers above *base_hex* (hue/sat unchanged)."""
    if steps < 1:
        raise ValueError("steps must be >= 1")
    per_step = ELEVATION_LIGHTNESS_STEP if step is None else step
    return surface_lift(base_hex, per_step * steps)


def depress(base_hex: str, steps: int = 1, *, step: float | None = None) -> str:
    """Lower a surface *steps* tiers below *base_hex* (hue/sat unchanged)."""
    if steps < 1:
        raise ValueError("steps must be >= 1")
    per_step = ELEVATION_LIGHTNESS_STEP if step is None else step
    return surface_depress(base_hex, per_step * steps)


def surface_at_level(base_hex: str, level: int, *, step: float = ELEVATION_LIGHTNESS_STEP) -> str:
    """Return surface *level* steps above *base_hex* (``level`` must be >= 0)."""
    if level < 0:
        raise ValueError("use depress() for negative elevation")
    if level == 0:
        c = QColor(base_hex)
        if not c.isValid():
            raise ValueError(f"invalid base color: {base_hex!r}")
        return c.name(QColor.NameFormat.HexRgb)
    return elevate(base_hex, level, step=step)


def surface_lightness(hex_color: str) -> float:
    c = QColor(hex_color)
    _h, _s, lightness, _a = c.getHslF()
    return lightness


def border_idle_alpha(level: int) -> float:
    if level not in _ELEVATION_BORDER_IDLE:
        raise KeyError(f"no border alpha for elevation level {level}")
    return _ELEVATION_BORDER_IDLE[level]


def border_hover_alpha(level: int) -> float:
    return min(1.0, border_idle_alpha(level) + ELEVATION_BORDER_HOVER_DELTA)


def elevation_border_rgba(
    level: int,
    *,
    hover: bool = False,
    chroma_hex: str = "#ffffff",
) -> str:
    alpha = border_hover_alpha(level) if hover else border_idle_alpha(level)
    c = QColor(chroma_hex)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.3f})"


def elevation_border_qcolor(
    level: int,
    *,
    hover: bool = False,
    chroma_hex: str = "#ffffff",
) -> QColor:
    c = QColor(chroma_hex)
    alpha = border_hover_alpha(level) if hover else border_idle_alpha(level)
    c.setAlpha(int(round(alpha * 255)))
    return c


def build_elevation_token_set(
    base_hex: str,
    *,
    step: float = ELEVATION_LIGHTNESS_STEP,
) -> dict[str, str | float]:
    """Coarse ``elev_*`` keys — superseded by ``surfaces.build_semantic_surfaces``."""
    out: dict[str, str | float] = {
        "elevation_base": base_hex,
        "elevation_step": step,
    }
    for level in (0, 1, 2, 3):
        out[f"elev_{level}"] = surface_at_level(base_hex, level, step=step)
        out[f"elev_border_idle_{level}"] = border_idle_alpha(level)
        out[f"elev_border_hover_{level}"] = border_hover_alpha(level)
    return out


def inject_elevation_into_tokens(tokens: dict, *, theme: str = "dark") -> None:
    base = tokens.get("elevation_base") or tokens.get("bg") or tokens.get("app_bg")
    if not base:
        raise KeyError("tokens must define elevation_base, bg, or app_bg")
    step = float(tokens.get("elevation_step", ELEVATION_LIGHTNESS_STEP))
    tokens.update(build_elevation_token_set(str(base), step=step))


def elevation_stroke_color(
    level: int,
    tokens: dict,
    *,
    hover: bool = False,
) -> QColor:
    chroma = tokens.get("chroma", "#ffffff")
    return elevation_border_qcolor(level, hover=hover, chroma_hex=str(chroma))


def elevation_surface(level: int, tokens: dict) -> str:
    return str(tokens[f"elev_{max(0, min(3, level))}"])
