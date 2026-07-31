"""Dialog tier design tokens — re-exported from golden reference.

Do not edit values here; change ``reference.py`` only after design review.
"""

from monostudio.ui_qt.dialog_tier.reference import CURRENT_THEME, T, T_METRICS, TIERS_QSS, set_tier_theme
from monostudio.ui_qt.elevation import ELEVATION_LIGHTNESS_STEP, elevate
from monostudio.ui_qt.surfaces import (
    SEMANTIC_SURFACE_LADDER,
    SURFACE_APP,
    SURFACE_CARD,
    SURFACE_DIALOG,
    SURFACE_FIELD,
    SURFACE_OVERLAY,
    SURFACE_PARENT,
    SURFACE_POPUP,
    SURFACE_TOOLTIP,
    build_semantic_surface_ladder,
    inject_design_system_tokens,
    surface_border_color,
    surface_color,
    surface_parent,
)

__all__ = [
    "CURRENT_THEME",
    "T",
    "T_METRICS",
    "TIERS_QSS",
    "set_tier_theme",
    "ELEVATION_LIGHTNESS_STEP",
    "elevate",
    "SEMANTIC_SURFACE_LADDER",
    "SURFACE_APP",
    "SURFACE_CARD",
    "SURFACE_DIALOG",
    "SURFACE_FIELD",
    "SURFACE_OVERLAY",
    "SURFACE_PARENT",
    "SURFACE_POPUP",
    "SURFACE_TOOLTIP",
    "build_semantic_surface_ladder",
    "inject_design_system_tokens",
    "surface_border_color",
    "surface_color",
    "surface_parent",
]
