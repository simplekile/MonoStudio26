"""MONOS Dialog Tier Design System — golden reference implementation (FROZEN).

Visual design is approved. Do not change layout, spacing, typography, radius,
colors, hierarchy, or visual language without explicit design review.

Harness: scripts/test_dialog_tiers.py
Rule: .cursor/rules/plan_dialog_tier_golden_reference_v1.mdc
"""

from __future__ import annotations

import math
import os
import sys

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal, QEvent, QDate, QTimer, QSize, QObject
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.calendar_date_picker import run_date_picker_dialog
from monostudio.ui_qt.elevation import elevation_stroke_color, inject_elevation_into_tokens
from monostudio.ui_qt.surfaces import (
    SURFACE_APP,
    SURFACE_CARD,
    SURFACE_DIALOG,
    SURFACE_FIELD,
    SURFACE_POPUP,
    inject_design_system_tokens,
    surface_border_color,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, apply_dark_theme, monos_font

T_METRICS: dict = {
    "field_h": 40,
    "field_readonly_h": 34,
    "chrome_pad_x": 32,
    "chrome_close_inset": 16,
    "form_pad_top": 16,
    "form_spacing_y": 24,
    "card_pad_l": 12,
    "card_pad_t": 10,
    "card_pad_r": 12,
    "card_pad_b": 10,
    "card_action_inset_r": 10,
    "radius_l1": 16,
    "radius_l2": 16,
    "radius_sm": 8,
    "field_label_gap": 8,
    "field_hint_gap": 6,
    "field_readonly_block_gap": 16,
    "close_idle_a": 0.55,
    "close_hover_bg_a": 0.05,
    "hairline_a": 0.05,
    "selection_bg_a": 0.45,
    "cta_border_idle_a": 58,
    "cta_border_pressed_a": 92,
    "cta_border_grad_lo_a": 145,
    "cta_border_grad_hi_a": 175,
    "l1_sidebar_pad_x": 20,
    "l1_sidebar_brand_top": 20,
    "l1_sidebar_pad_b": 24,
    "l1_w": 720,
    "l1_h": 500,
    "l1_sidebar_frac": 0.32,
    "l2_w": 520,
    "l2_h": 500,
    "l2_h_compact": 460,
    "l2_h_tall": 540,
    "l2_topbar_h": 96,
    "l1_grad_fps": 16,
    "l1_grad_drift": 0.028,
    "l1_grad_stops": 20,
    "l1_grad_supersample": 1,
    "l1_noise_tile": 128,
    "label_tracking": 0.5,
    "ui_font": "Inter",
    "elevation_step": 0.04,
    # (offset_y, blur_radius, opacity) — ambient → middle → contact
    "dialog_shadow_layers": (
        (12.0, 48.0, 0.10),
        (4.0, 20.0, 0.08),
        (1.0, 4.0, 0.06),
    ),
    "dialog_shadow_margin_pad": 10.0,
    "mono_select_popup_radius": 12,
    "mono_select_item_h": 36,
    "mono_select_item_h_dual": 44,
    "mono_select_popup_pad": 5,
    "mono_select_popup_pad_top": 3,
    "mono_select_popup_min_w": 200,
    "mono_select_popup_max_w": 320,
    "mono_select_popup_max_visible": 7,
    "mono_select_popup_gap": 0,
    "mono_select_seam_overlap": 1,
    "mono_select_disclosure_separator_a": 0.08,
    "mono_select_row_pad_x": 8,
    "mono_select_row_inset_x": 3,
    "mono_select_row_inset_y": 1,
    "mono_select_row_radius": 5,
    "mono_select_icon_size": 15,
    "mono_select_icon_slot": 18,
    "mono_select_check_size": 12,
    "mono_select_chevron_size": 13,
    "mono_select_chevron_slot": 18,
    "mono_select_separator_h": 9,
    "mono_select_row_selected_a": 0.035,
    "mono_select_row_hover_a": 0.065,
    "mono_select_row_highlight_a": 0.085,
    "mono_select_anim_ms": 140,
    "mono_select_anim_slide_px": 2.0,
    "mono_select_shadow_layers": (
        (3.0, 14.0, 0.06),
        (1.0, 5.0, 0.04),
    ),
    "mono_select_shadow_layers_attached": (
        (2.0, 10.0, 0.04),
    ),
    "mono_select_shadow_pad": 5.0,
    "card_footnote_top": 6,
    "card_footnote_divider_a": 0.04,
}

# Brand accents — shared across themes (not surface-dependent).
_ACCENTS: dict = {
    "blue": "#2563eb",
    "blue_hi": "#3b82f6",
    "purple": "#7c3aed",
    "cta_rim": "#a5b4fc",
    "cta_border_grad_lo": "#93c5fd",
    "cta_border_grad_hi": "#c4b5fd",
}

# Semantic color roles. ``chroma`` is the neutral used for alpha hairlines / borders.
_THEME_DARK: dict = {
    **_ACCENTS,
    "bg": "#0f1016",
    "sidebar": "#0c0d13",
    "inset": "#12141c",
    "raised": "#1c1f2a",
    "line": "#262a34",
    "line_hi": "#3f3f46",
    "dialog_border": "#3d4556",
    "text": "#fafafa",
    "body": "#d4d4d8",
    "label": "#a1a1aa",
    "meta": "#71717a",
    "mono": "#9ca3af",
    "ghost_text_idle": "#71717a",
    "shadow_chroma": "#000000",
    "shadow_strength": 1.0,
    "chroma": "#ffffff",
    "on_accent": "#ffffff",
    "on_cta": "#f4f4f5",
    "cta_arrow_idle": "#d4d4d8",
    "cta_arrow_hover": "#ffffff",
    "cta_arrow_pressed": "#e4e4e7",
    "cta_arrow_disabled": "#71717a",
    "surface_hover": "#22252e",
    "overlay_hover": "rgba(255, 255, 255, 0.06)",
    "overlay_pressed": "rgba(255, 255, 255, 0.10)",
    "overlay_field_hover": "rgba(255, 255, 255, 0.10)",
    "overlay_field_pressed": "rgba(255, 255, 255, 0.14)",
    "cta_disabled_left": "#3f3f46",
    "cta_disabled_right": "#52525b",
    "cta_disabled_border_a": 80,
    "highlight_on_accent": "#ffffff",
    "border_idle_a": 0.08,
    "border_hover_a": 0.14,
    "focus_blue_a": 0.42,
    "nebula_a": (42, 58, 50),
    "l1_noise_alpha": 14,
    "overlay_scrim": "rgba(0, 0, 0, 0.50)",
}

_THEME_LIGHT: dict = {
    **_ACCENTS,
    "bg": "#f4f4f5",
    "sidebar": "#ececef",
    "inset": "#fafafa",
    "raised": "#f4f4f5",
    "line": "#e4e4e7",
    "line_hi": "#d4d4d8",
    "dialog_border": "#a1a1aa",
    "text": "#18181b",
    "body": "#3f3f46",
    "label": "#52525b",
    "meta": "#71717a",
    "mono": "#52525b",
    "ghost_text_idle": "#71717a",
    "shadow_chroma": "#000000",
    "shadow_strength": 0.72,
    "chroma": "#18181b",
    "on_accent": "#ffffff",
    "on_cta": "#ffffff",
    "cta_arrow_idle": "#e0e7ff",
    "cta_arrow_hover": "#ffffff",
    "cta_arrow_pressed": "#c7d2fe",
    "cta_arrow_disabled": "#a1a1aa",
    "surface_hover": "#e4e4e7",
    "overlay_hover": "rgba(24, 24, 27, 0.04)",
    "overlay_pressed": "rgba(24, 24, 27, 0.08)",
    "overlay_field_hover": "rgba(24, 24, 27, 0.06)",
    "overlay_field_pressed": "rgba(24, 24, 27, 0.10)",
    "cta_disabled_left": "#d4d4d8",
    "cta_disabled_right": "#e4e4e7",
    "cta_disabled_border_a": 96,
    "highlight_on_accent": "#ffffff",
    "border_idle_a": 0.10,
    "border_hover_a": 0.16,
    "focus_blue_a": 0.48,
    "nebula_a": (22, 30, 26),
    "l1_noise_alpha": 8,
    "overlay_scrim": "rgba(0, 0, 0, 0.32)",
}

T: dict = {}
CURRENT_THEME = "dark"
TIERS_QSS = ""
_NOISE_TILE: QImage | None = None


def _chroma_color(alpha: float) -> QColor:
    """Alpha overlay on ``T['chroma']`` — works on dark (white) and light (ink)."""
    c = QColor(T["chroma"])
    c.setAlpha(int(255 * alpha))
    return c


def _elevation_stroke(level: int, *, hover: bool = False) -> QColor:
    """Border stroke for a surface elevation level."""
    return elevation_stroke_color(level, T, hover=hover)


def _surface_stroke(surface: str, *, hover: bool = False) -> QColor:
    """Border stroke for a semantic surface token."""
    return surface_border_color(surface, T, hover=hover)


def _chroma_color_alpha_byte(alpha_byte: int) -> QColor:
    c = QColor(T["chroma"])
    c.setAlpha(max(0, min(255, alpha_byte)))
    return c


def _accent_highlight_alpha_byte(alpha_byte: int) -> QColor:
    """Specular on brand gradient controls — always light, independent of surface chroma."""
    c = QColor(T["highlight_on_accent"])
    c.setAlpha(max(0, min(255, alpha_byte)))
    return c


def _color_alpha(hex_color: str, alpha_byte: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, alpha_byte)))
    return c


def _cta_border_solid(*, pressed: bool) -> QColor:
    c = QColor(T["cta_rim"])
    c.setAlpha(T["cta_border_pressed_a"] if pressed else T["cta_border_idle_a"])
    return c


def _cta_border_gradient() -> tuple[QColor, QColor]:
    """Hover rim — follows fill gradient (blue → purple), not flat white."""
    return (
        _color_alpha(T["cta_border_grad_lo"], T["cta_border_grad_lo_a"]),
        _color_alpha(T["cta_border_grad_hi"], T["cta_border_grad_hi_a"]),
    )


def _stroke_pill_border(
    painter: QPainter,
    bounds: QRectF,
    *,
    dpr: float,
    color: QColor | None = None,
    gradient: tuple[QColor, QColor] | None = None,
) -> None:
    """Cosmetic 1px inset stroke — solid or horizontal gradient (hover)."""
    inset = _device_px(dpr)
    stroke_r = bounds.adjusted(inset, inset, -inset, -inset)
    if stroke_r.width() <= 0 or stroke_r.height() <= 0:
        return
    radius = stroke_r.height() / 2.0
    if gradient is not None:
        lo, hi = gradient
        brush = QLinearGradient(stroke_r.topLeft(), QPointF(stroke_r.right(), stroke_r.top()))
        brush.setColorAt(0.0, lo)
        brush.setColorAt(1.0, hi)
        pen = QPen(QBrush(brush), 1.0)
    elif color is not None:
        pen = QPen(color, 1.0)
    else:
        return
    pen.setCosmetic(True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(pen)
    painter.drawRoundedRect(stroke_r, radius, radius)


def set_tier_theme(name: str) -> None:
    global CURRENT_THEME, _NOISE_TILE, TIERS_QSS
    if name not in ("dark", "light"):
        raise ValueError(f"unknown tier theme: {name}")
    CURRENT_THEME = name
    colors = _THEME_DARK if name == "dark" else _THEME_LIGHT
    T.clear()
    T.update(T_METRICS)
    T.update(colors)
    inject_design_system_tokens(T, theme=name)
    _NOISE_TILE = None
    _invalidate_tier_shadow_cache()
    TIERS_QSS = _build_tiers_qss()


def _apply_tier_light_app_theme(app: QApplication) -> None:
    """Minimal light shell for harness — production app stays dark-only for now."""
    from monostudio.ui_qt.style import _MonosAppStyle, _install_fonts

    app.setStyle(_MonosAppStyle())
    _install_fonts(app)

    palette = QPalette()
    app_bg = QColor("#f4f4f5")
    panel = QColor("#ffffff")
    surface = QColor("#e4e4e7")
    text = QColor("#18181b")
    label = QColor("#52525b")
    meta = QColor("#71717a")
    placeholder = QColor("#a1a1aa")
    accent = QColor(T["blue"])

    palette.setColor(QPalette.ColorRole.Window, app_bg)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, panel)
    palette.setColor(QPalette.ColorRole.AlternateBase, surface)
    palette.setColor(QPalette.ColorRole.ToolTipBase, panel)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, surface)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, text)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(T["on_accent"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, placeholder)
    app.setPalette(palette)
    app.setStyleSheet('QWidget { font-family: "Inter"; font-size: 13px; }')


def apply_tier_app_theme(app: QApplication, theme: str) -> None:
    set_tier_theme(theme)
    if theme == "dark":
        apply_dark_theme(app)
    else:
        _apply_tier_light_app_theme(app)
    _patch_app_stylesheet_for_text(app)
    app.setStyleSheet(app.styleSheet() + "\n" + TIERS_QSS)


def _installed_families() -> set[str]:
    return set(QFontDatabase.families())


def _resolve_ui_family(weight: QFont.Weight) -> tuple[str, QFont.Weight]:
    """Prefer static Inter faces — variable Inter + synthetic bold can look jagged."""
    base = T["ui_font"]
    families = _installed_families()
    if base != "Inter":
        return base, weight
    if weight >= QFont.Weight.Bold and "Inter SemiBold" in families:
        return "Inter SemiBold", QFont.Weight.Normal
    if weight >= QFont.Weight.DemiBold and "Inter SemiBold" in families:
        return "Inter SemiBold", QFont.Weight.Normal
    if weight >= QFont.Weight.Medium and "Inter Medium" in families:
        return "Inter Medium", QFont.Weight.Normal
    return "Inter", weight


def _resolve_mono_family() -> str:
    families = _installed_families()
    for name in ("JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New"):
        if name in families:
            return name
    return "monospace"


def _tier_inter_font(size_px: int, css_weight: int) -> QFont:
    """Inter at pixel size with CSS weight (400/500/600/700) via static faces when available."""
    point_size = max(1, round(size_px * 72 / 96))
    families = _installed_families()
    base = T["ui_font"]
    if base == "Inter":
        if css_weight >= 700 and "Inter Bold" in families:
            return monos_font("Inter Bold", point_size, QFont.Weight.Normal)
        if css_weight >= 600 and "Inter SemiBold" in families:
            return monos_font("Inter SemiBold", point_size, QFont.Weight.Normal)
        if css_weight >= 500 and "Inter Medium" in families:
            return monos_font("Inter Medium", point_size, QFont.Weight.Normal)
    qt_weight = {
        700: QFont.Weight.Bold,
        600: QFont.Weight.DemiBold,
        500: QFont.Weight.Medium,
        400: QFont.Weight.Normal,
    }.get(css_weight, QFont.Weight.Normal)
    return monos_font(base, point_size, qt_weight)


def _tier_font(
    size_px: int,
    weight: QFont.Weight = QFont.Weight.Medium,
    *,
    mono: bool = False,
) -> QFont:
    """Point-sized font with MONOS antialiasing — avoid setPixelSize at 125–150% DPI."""
    if mono:
        family = _resolve_mono_family()
        face_weight = weight
    else:
        family, face_weight = _resolve_ui_family(weight)
    point_size = max(1, round(size_px * 72 / 96))
    return monos_font(family, point_size, face_weight)


def _text_style(*, color: str, bg: str = "transparent", letter_spacing: float | None = None) -> str:
    parts = [f"color: {color}", f"background: {bg}"]
    if letter_spacing is not None:
        parts.append(f"letter-spacing: {letter_spacing}px")
    return "; ".join(parts) + ";"


def _hairline_color() -> QColor:
    return _chroma_color(T["hairline_a"])


def _tier_selection_rgba() -> str:
    c = QColor(T["blue"])
    alpha = int(255 * T["selection_bg_a"])
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def _card_content_margins() -> tuple[int, int, int, int]:
    return (T["card_pad_l"], T["card_pad_t"], T["card_pad_r"], T["card_pad_b"])


def _metadata_value_style() -> str:
    """Read-only metadata values — single luminance across MetadataCard / path rows."""
    return _text_style(color=T["text"])


def _dpr(*, painter: QPainter | None = None, widget: QWidget | None = None) -> float:
    if widget is not None:
        v = widget.devicePixelRatioF()
        if v > 0:
            return v
    if painter is not None:
        device = painter.device()
        if device is not None:
            v = device.devicePixelRatioF()
            if v > 0:
                return v
    screen = QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _snap_rect_to_device(rect: QRectF, dpr: float) -> QRectF:
    """Align rect edges to device pixels — reduces fuzzy fills at 125%/150%/175%."""
    if dpr <= 1.0:
        return QRectF(rect)
    left = round(rect.left() * dpr) / dpr
    top = round(rect.top() * dpr) / dpr
    right = round(rect.right() * dpr) / dpr
    bottom = round(rect.bottom() * dpr) / dpr
    return QRectF(left, top, max(0.0, right - left), max(0.0, bottom - top))


def _device_px(dpr: float, pixels: float = 1.0) -> float:
    """Logical length for N physical pixels (cosmetic 1px stroke inset)."""
    return pixels / max(1.0, dpr)


def _paint_rounded_chrome(
    painter: QPainter,
    bounds: QRectF,
    *,
    fill: QColor,
    stroke: QColor,
    radius: float,
) -> None:
    """Fill + 1px cosmetic stroke — DPI-snapped fill; stroke inset by 1 device px."""
    dpr = _dpr(painter=painter)
    fill_bounds = _snap_rect_to_device(bounds, dpr)
    fill_shape = QPainterPath()
    fill_shape.addRoundedRect(fill_bounds, radius, radius)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(fill_shape)

    inset = _device_px(dpr)
    stroke_rect = fill_bounds.adjusted(inset, inset, -inset, -inset)
    stroke_shape = QPainterPath()
    stroke_shape.addRoundedRect(stroke_rect, radius, radius)
    pen = QPen(stroke, 1.0)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(stroke_shape)


def _rounded_rect_path(
    rect: QRectF,
    radius: float,
    *,
    top_left: bool = True,
    top_right: bool = True,
    bottom_left: bool = True,
    bottom_right: bool = True,
) -> QPainterPath:
    """Rounded rect with per-corner control (for attached popups / expanded fields)."""
    path = QPainterPath()
    if not rect.isValid():
        return path
    r = min(float(radius), rect.width() / 2.0, rect.height() / 2.0)
    if r <= 0 or not any((top_left, top_right, bottom_left, bottom_right)):
        path.addRect(rect)
        return path

    tl = r if top_left else 0.0
    tr = r if top_right else 0.0
    bl = r if bottom_left else 0.0
    br = r if bottom_right else 0.0
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()

    path.moveTo(x + tl, y)
    path.lineTo(x + w - tr, y)
    if tr > 0:
        path.arcTo(x + w - 2 * tr, y, 2 * tr, 2 * tr, 90, -90)
    path.lineTo(x + w, y + h - br)
    if br > 0:
        path.arcTo(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br, 0, -90)
    path.lineTo(x + bl, y + h)
    if bl > 0:
        path.arcTo(x, y + h - 2 * bl, 2 * bl, 2 * bl, -90, -90)
    path.lineTo(x, y + tl)
    if tl > 0:
        path.arcTo(x, y, 2 * tl, 2 * tl, 180, -90)
    path.closeSubpath()
    return path


def _paint_rounded_chrome_corners(
    painter: QPainter,
    bounds: QRectF,
    *,
    fill: QColor,
    stroke: QColor,
    radius: float,
    top_left: bool = True,
    top_right: bool = True,
    bottom_left: bool = True,
    bottom_right: bool = True,
) -> None:
    """Fill + cosmetic stroke with selective corner radii."""
    dpr = _dpr(painter=painter)
    fill_bounds = _snap_rect_to_device(bounds, dpr)
    inset = _device_px(dpr)
    stroke_radius = max(0.0, float(radius) - inset)
    corners = (top_left, top_right, bottom_left, bottom_right)
    fill_shape = _rounded_rect_path(fill_bounds, radius, top_left=top_left, top_right=top_right, bottom_left=bottom_left, bottom_right=bottom_right)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(fill_shape)

    stroke_rect = fill_bounds.adjusted(inset, inset, -inset, -inset)
    stroke_shape = _rounded_rect_path(
        stroke_rect,
        stroke_radius,
        top_left=top_left,
        top_right=top_right,
        bottom_left=bottom_left,
        bottom_right=bottom_right,
    )
    pen = QPen(stroke, 1.0)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(stroke_shape)


def _cosmetic_stroke_pen(color: QColor) -> QPen:
    pen = QPen(color, 1.0)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return pen


def _disclosure_stroke_box(bounds: QRectF, radius: float, *, painter: QPainter) -> tuple[QRectF, float, float, float, float, float, float]:
    """Return (fill_bounds, x, y, x2, y2, corner_r, inset) — shared fill + border geometry."""
    dpr = _dpr(painter=painter)
    fill_bounds = _snap_rect_to_device(bounds, dpr)
    inset = _device_px(dpr)
    corner_r = max(0.0, float(radius) - inset)
    stroke_rect = fill_bounds.adjusted(inset, inset, -inset, -inset)
    x = stroke_rect.x()
    y = stroke_rect.y()
    x2 = x + stroke_rect.width()
    y2 = y + stroke_rect.height()
    return fill_bounds, x, y, x2, y2, corner_r, inset


def _paint_disclosure_header_border(
    painter: QPainter,
    *,
    x: float,
    y: float,
    x2: float,
    y2: float,
    r: float,
    stroke: QColor,
) -> None:
    """Stroke selector header with independent primitives (no QPainterPath stroke)."""
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = _cosmetic_stroke_pen(stroke)
    painter.setPen(pen)

    if r <= 0:
        painter.drawLine(QPointF(x, y), QPointF(x2, y))
        painter.drawLine(QPointF(x2, y), QPointF(x2, y2))
        painter.drawLine(QPointF(x, y2), QPointF(x, y))
        return

    painter.drawLine(QPointF(x + r, y), QPointF(x2 - r, y))
    painter.drawArc(QRectF(x2 - 2 * r, y, 2 * r, 2 * r), 90 * 16, -90 * 16)
    painter.drawLine(QPointF(x2, y + r), QPointF(x2, y2))
    painter.drawLine(QPointF(x, y2), QPointF(x, y + r))
    painter.drawArc(QRectF(x, y, 2 * r, 2 * r), 180 * 16, -90 * 16)


def _paint_disclosure_header_chrome(
    painter: QPainter,
    bounds: QRectF,
    *,
    fill: QColor,
    stroke: QColor,
    radius: float,
) -> None:
    """Open disclosure header — dialog fill path; open bottom border via primitives."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    fill_bounds, x, y, x2, y2, r, _inset = _disclosure_stroke_box(bounds, radius, painter=painter)
    fill_shape = _rounded_rect_path(
        fill_bounds,
        radius,
        bottom_left=False,
        bottom_right=False,
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(fill_shape)
    _paint_disclosure_header_border(painter, x=x, y=y, x2=x2, y2=y2, r=r, stroke=stroke)


def _paint_disclosure_body_chrome(
    painter: QPainter,
    bounds: QRectF,
    *,
    fill: QColor,
    stroke: QColor,
    radius: float,
    separator: QColor,
) -> None:
    """Disclosure body — dialog fill+stroke; no top border (separator only)."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    dpr = _dpr(painter=painter)
    fill_bounds, x, y, x2, y2, corner_r, inset = _disclosure_stroke_box(bounds, radius, painter=painter)

    fill_shape = _rounded_rect_path(fill_bounds, radius, top_left=False, top_right=False)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(fill_shape)

    stroke_rect = fill_bounds.adjusted(inset, inset, -inset, -inset)
    stroke_shape = _rounded_rect_path(
        stroke_rect,
        corner_r,
        top_left=False,
        top_right=False,
    )
    painter.save()
    clip_top = y + inset
    painter.setClipRect(
        QRectF(fill_bounds.left(), clip_top, fill_bounds.width(), fill_bounds.bottom() - clip_top),
        Qt.ClipOperation.IntersectClip,
    )
    painter.setPen(_cosmetic_stroke_pen(stroke))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(stroke_shape)
    painter.restore()

    painter.setPen(_cosmetic_stroke_pen(separator))
    painter.drawLine(QPointF(x, y), QPointF(x2, y))


def _sync_disclosure_body_mask(widget: QWidget, *, radius: float, enabled: bool) -> None:
    """Clip popup card children to rounded bottom corners (prevents square BG bleed)."""
    if not enabled:
        widget.clearMask()
        return
    path = _rounded_rect_path(QRectF(widget.rect()), radius, top_left=False, top_right=False)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


def _tier_dialog_shadow_extent() -> float:
    """Logical bleed needed for the largest shadow layer."""
    layers = T["dialog_shadow_layers"]
    pad = float(T["dialog_shadow_margin_pad"])
    extent = 0.0
    for offset_y, blur, _opacity in layers:
        extent = max(extent, float(offset_y) + float(blur) * 1.08)
    return extent + pad


def _tier_dialog_shadow_margin() -> int:
    return int(math.ceil(_tier_dialog_shadow_extent()))


def _tier_dialog_frame_rect(widget: QWidget) -> QRectF:
    m = _tier_dialog_shadow_margin()
    return QRectF(m, m, max(0, widget.width() - 2 * m), max(0, widget.height() - 2 * m))


_TIER_SHADOW_CACHE: dict[tuple[int, int, float, float, str], QPixmap] = {}
_TIER_SHADOW_CACHE_MAX = 16


def _invalidate_tier_shadow_cache() -> None:
    _TIER_SHADOW_CACHE.clear()


def _tier_shadow_cache_key(width: int, height: int, radius: float, dpr: float) -> tuple[int, int, float, float, str]:
    return (width, height, round(radius, 2), round(dpr, 3), CURRENT_THEME)


def _shadow_layer_ring_steps(blur: float) -> int:
    return max(12, min(40, int(round(blur * 0.72))))


def _paint_tier_shadow_layer(
    painter: QPainter,
    frame: QRectF,
    *,
    radius: float,
    offset_y: float,
    blur: float,
    opacity: float,
) -> None:
    """One CSS-like shadow: offset + Gaussian blur approximated by soft rings."""
    if frame.isEmpty() or blur <= 0.0 or opacity <= 0.0:
        return
    chroma = QColor(T["shadow_chroma"])
    strength = float(T["shadow_strength"])
    peak = opacity * strength
    if peak <= 0.0:
        return

    shadow_frame = frame.translated(0.0, offset_y)
    steps = _shadow_layer_ring_steps(blur)
    sigma = max(blur / 2.8, 1.0)
    rings: list[tuple[float, float]] = []
    weight_sum = 0.0
    for i in range(steps + 1):
        expand = (i / steps) * blur
        weight = math.exp(-0.5 * (expand / sigma) ** 2)
        rings.append((expand, weight))
        weight_sum += weight
    if weight_sum <= 0.0:
        return

    painter.setPen(Qt.PenStyle.NoPen)
    for expand, weight in rings:
        alpha = peak * (weight / weight_sum)
        if alpha < 0.001:
            continue
        color = QColor(chroma)
        color.setAlphaF(min(1.0, alpha))
        layer = shadow_frame.adjusted(-expand, -expand, expand, expand)
        corner = radius + expand * 0.28
        painter.setBrush(color)
        painter.drawRoundedRect(layer, corner, corner)


def _render_tier_shadow_pixmap(width: int, height: int, radius: float, dpr: float) -> QPixmap:
    margin = _tier_dialog_shadow_margin()
    frame = QRectF(margin, margin, max(0, width - 2 * margin), max(0, height - 2 * margin))
    img_w = max(1, int(round(width * dpr)))
    img_h = max(1, int(round(height * dpr)))
    image = QImage(img_w, img_h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(dpr, dpr)
    for offset_y, blur, opacity in T["dialog_shadow_layers"]:
        _paint_tier_shadow_layer(
            painter,
            frame,
            radius=radius,
            offset_y=float(offset_y),
            blur=float(blur),
            opacity=float(opacity),
        )
    painter.end()

    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _tier_dialog_shadow_pixmap(width: int, height: int, radius: float, dpr: float) -> QPixmap:
    key = _tier_shadow_cache_key(width, height, radius, dpr)
    cached = _TIER_SHADOW_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_TIER_SHADOW_CACHE) >= _TIER_SHADOW_CACHE_MAX:
        _invalidate_tier_shadow_cache()
    pixmap = _render_tier_shadow_pixmap(width, height, radius, dpr)
    _TIER_SHADOW_CACHE[key] = pixmap
    return pixmap


def _paint_tier_dialog_shadow(
    painter: QPainter,
    frame: QRectF,
    *,
    radius: float,
    widget: QWidget | None = None,
) -> None:
    """Cached premium elevation — ambient + middle + contact; paint before chrome."""
    if frame.isEmpty() or widget is None:
        return
    dpr = _dpr(painter=painter, widget=widget)
    pixmap = _tier_dialog_shadow_pixmap(widget.width(), widget.height(), radius, dpr)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.drawPixmap(0, 0, pixmap)
    painter.restore()


_TIER_BTN_FONT: dict[str, tuple[int, int]] = {
    "primary": (13, 600),
    "secondary": (13, 600),
    "inline": (12, 600),
    "ghost": (13, 600),
    "ghost-bold": (13, 700),
    "link": (12, 600),
}


def configure_tier_text_rendering() -> None:
    """Windows 150% scaling: Round DPI + DirectWrite; avoid PassThrough half-pixel layout."""
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=directwrite")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.Round
    )


def _patch_app_stylesheet_for_text(app: QApplication) -> None:
    """Global QWidget font-size in QSS rebuilds QFont without AA hints — drop it for tier test."""
    qss = app.styleSheet()
    qss = qss.replace(
        'QWidget { font-family: "Inter"; font-size: 13px; }',
        "/* tier test: typography via monos_font / setFont */",
    )
    app.setStyleSheet(qss)


def _grad_center(
    sb_rect: QRectF,
    fx: float,
    fy: float,
    *,
    phase: float,
    seed: float,
    amp: float = T_METRICS["l1_grad_drift"],
) -> tuple[float, float]:
    """Subtle drifting gradient focal point (fractions of sidebar rect)."""
    t = phase + seed
    return (
        sb_rect.left() + sb_rect.width() * (fx + math.sin(t) * amp),
        sb_rect.top() + sb_rect.height() * (fy + math.cos(t * 0.87) * amp),
    )


def _grad_radius(sb_rect: QRectF, scale: float, *, phase: float, seed: float) -> float:
    pulse = 1.0 + 0.05 * math.sin(phase * 0.65 + seed)
    return sb_rect.width() * scale * pulse


def _set_smooth_alpha_stops(
    grad: QRadialGradient,
    rgb: tuple[int, int, int],
    peak_alpha: int,
    *,
    stops: int | None = None,
    power: float = 2.15,
    dither: int = 2,
) -> None:
    """Many eased stops + micro-dither to reduce 8-bit gradient banding on dark UI."""
    n = stops if stops is not None else T["l1_grad_stops"]
    r, g, b = rgb
    for i in range(n + 1):
        t = i / n
        alpha = peak_alpha * ((1.0 - t) ** power)
        noise = (math.sin(i * 12.9898 + r * 0.17 + g * 0.31 + b * 0.53) * 43758.5453) % 1.0
        alpha_i = int(alpha) + int((noise - 0.5) * 2 * dither)
        grad.setColorAt(t, QColor(r, g, b, max(0, min(255, alpha_i))))


def _paint_sidebar_nebula(painter: QPainter, sb_rect: QRectF, *, grad_phase: float) -> None:
    """Paint the three radial blobs (coords in sb_rect space)."""
    a0, a1, a2 = T["nebula_a"]
    x0, y0 = _grad_center(sb_rect, 0.08, 0.06, phase=grad_phase, seed=0.0)
    g0 = QRadialGradient(x0, y0, _grad_radius(sb_rect, 1.10, phase=grad_phase, seed=0.0))
    _set_smooth_alpha_stops(g0, (59, 130, 246), a0)
    painter.fillRect(sb_rect, QBrush(g0))

    x1, y1 = _grad_center(sb_rect, 0.15, 0.95, phase=grad_phase, seed=2.1)
    g1 = QRadialGradient(x1, y1, _grad_radius(sb_rect, 0.90, phase=grad_phase, seed=2.1))
    _set_smooth_alpha_stops(g1, (37, 99, 235), a1)
    painter.fillRect(sb_rect, QBrush(g1))

    x2, y2 = _grad_center(sb_rect, 0.9, 0.9, phase=grad_phase, seed=4.2)
    g2 = QRadialGradient(x2, y2, _grad_radius(sb_rect, 0.80, phase=grad_phase, seed=4.2))
    _set_smooth_alpha_stops(g2, (124, 58, 237), a2)
    painter.fillRect(sb_rect, QBrush(g2))


def _build_noise_tile(size: int, base_alpha: int) -> QImage:
    """Precomputed monochrome grain tile (generated once)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    for y in range(size):
        for x in range(size):
            h = math.sin(x * 12.9898 + y * 78.233) * 43758.5453
            v = int((h - math.floor(h)) * 255)
            ha = math.sin(x * 4.127 + y * 19.417 + 2.31) * 43758.5453
            a = base_alpha + int((ha - math.floor(ha)) * 10) - 5
            img.setPixelColor(x, y, QColor(v, v, v, max(4, min(36, a))))
    return img


def _noise_tile() -> QImage:
    global _NOISE_TILE
    if _NOISE_TILE is None:
        _NOISE_TILE = _build_noise_tile(T["l1_noise_tile"], T["l1_noise_alpha"])
    return _NOISE_TILE


def _paint_noise_overlay(
    painter: QPainter,
    rect: QRectF,
    shape: QPainterPath | None = None,
    *,
    grad_phase: float = 0.0,
) -> None:
    """Film-grain dither over gradients — intersects existing clip (rounded dialog corners)."""
    tile = _noise_tile()
    tile_px = QPixmap.fromImage(tile)
    tile_w, tile_h = tile.width(), tile.height()
    ox = int((grad_phase * 9.0) % tile_w)
    oy = int((grad_phase * 6.5) % tile_h)
    painter.save()
    if shape is not None and not shape.isEmpty():
        painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SoftLight)
    painter.drawTiledPixmap(rect.toRect(), tile_px, QPoint(ox, oy))
    painter.restore()


def _build_tiers_qss() -> str:
    sel_bg = _tier_selection_rgba()
    return f"""
QWidget#TierRoot {{ background: transparent; }}

QLineEdit#TierInput::selection,
QDateEdit#TierDateEdit::selection,
QLabel#TierSelectable::selection {{
    background-color: {sel_bg};
    color: {T["on_accent"]};
}}

QLineEdit#TierInput {{
    background: transparent;
    color: {T["text"]};
    border: none;
    padding: 0;
    margin: 0;
    min-height: 0;
}}
QLineEdit#TierInput:focus {{
    border: none;
    outline: none;
}}
QLineEdit#TierInput[readonly="true"] {{
    color: {T["mono"]};
    background: transparent;
}}

QDateEdit#TierDateEdit {{
    background: transparent;
    color: {T["text"]};
    border: none;
    padding: 0;
    margin: 0;
    min-height: 0;
}}
QDateEdit#TierDateEdit:focus {{
    border: none;
    outline: none;
}}

QComboBox#TierCombo {{
    background: transparent;
    color: {T["text"]};
    border: none;
    border-radius: {T["radius_sm"]}px;
    padding: 9px 12px;
    min-height: 20px;
}}
QComboBox#TierCombo:focus {{ border: none; }}
QComboBox#TierCombo::drop-down {{ border: none; width: 28px; }}
QComboBox#TierCombo QAbstractItemView {{
    background: {T["raised"]};
    border: 1px solid {T["line_hi"]};
    selection-background-color: {T["blue"]};
}}

QPushButton#TierClose {{
    background: transparent;
    border: none;
    padding: 0;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
}}

QPushButton[tierBtn="primary"] {{
    background: {T["blue"]};
    color: {T["on_accent"]};
    border: none;
    border-radius: {T["radius_sm"]}px;
    padding: 9px 18px;
}}
QPushButton[tierBtn="primary"]:hover {{ background: {T["blue_hi"]}; }}

QPushButton[tierBtn="secondary"] {{
    background: {T["raised"]};
    color: {T["body"]};
    border: 1px solid {T["line_hi"]};
    border-radius: {T["radius_sm"]}px;
    padding: 9px 16px;
}}
QPushButton[tierBtn="secondary"]:hover {{
    background: {T["surface_hover"]};
    color: {T["text"]};
}}

QPushButton[tierBtn="inline"] {{
    background: {T["raised"]};
    color: {T["label"]};
    border: 1px solid {T["line_hi"]};
    border-radius: 6px;
    padding: 6px 12px;
}}
QPushButton[tierBtn="inline"]:hover {{ color: {T["text"]}; }}

QDialog QPushButton[tierBtn="ghost"] {{
    background: transparent;
    color: {T["ghost_text_idle"]};
    border: none;
    border-radius: {T["radius_sm"]}px;
    padding: 10px 20px;
    min-height: 42px;
}}
QDialog QPushButton[tierBtn="ghost"]:hover {{
    background: transparent;
    color: {T["text"]};
}}
QDialog QPushButton[tierBtn="ghost"]:pressed {{
    background: transparent;
    color: {T["text"]};
}}

QPushButton[tierBtn="ghost-bold"] {{
    background: transparent;
    color: {T["text"]};
    border: none;
    padding: 9px 12px;
}}
QPushButton[tierBtn="ghost-bold"]:hover {{ color: {T["text"]}; }}

QPushButton[tierBtn="link"] {{
    background: transparent;
    color: {T["label"]};
    border: none;
    padding: 6px 10px;
}}
QPushButton[tierBtn="link"]:hover {{ color: {T["text"]}; }}

QFrame#TierFieldShell {{
    background: transparent;
    border: none;
}}

QPushButton[tierBtn="icon"] {{
    background: transparent;
    border: none;
    border-radius: 4px;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
}}
QPushButton[tierBtn="icon"]:hover {{ background: {T["raised"]}; }}

QPushButton[tierBtn="fieldIcon"] {{
    background: transparent;
    border: none;
    border-radius: 4px;
    min-width: 22px; max-width: 22px;
    min-height: 22px; max-height: 22px;
    padding: 0;
    margin: 0;
}}
QPushButton[tierBtn="fieldAction"] {{
    background: transparent;
    border: none;
    border-radius: 4px;
    min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
    padding: 0;
    margin: 0;
}}
QPushButton[tierBtn="fieldAction"]:hover {{
    background: {T["overlay_field_hover"]};
}}
QPushButton[tierBtn="fieldAction"]:pressed {{
    background: {T["overlay_field_pressed"]};
}}

QWidget#TierRightPane {{ background: transparent; }}
QWidget#TierL2Body {{ background: transparent; }}

QScrollArea#DccPickerScroll {{
    background: transparent;
    border: none;
}}
QScrollArea#DccPickerScroll QScrollBar:vertical {{
    background: transparent;
    width: 5px;
    margin: 0px;
}}
QScrollArea#DccPickerScroll QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 0.16);
    min-height: 20px;
    border-radius: 2px;
}}
QScrollArea#DccPickerScroll QScrollBar::handle:vertical:hover {{
    background: rgba(255, 255, 255, 0.38);
}}
QScrollArea#DccPickerScroll QScrollBar::add-line:vertical,
QScrollArea#DccPickerScroll QScrollBar::sub-line:vertical {{
    height: 0px;
    width: 0px;
}}
QScrollArea#DccPickerScroll QScrollBar::add-page:vertical,
QScrollArea#DccPickerScroll QScrollBar::sub-page:vertical {{
    background: none;
}}

QWidget#TierLauncher {{ background: {T[SURFACE_APP]}; }}
QPushButton.TierLauncherBtn {{
    background: {T[SURFACE_DIALOG]};
    border: 1px solid {T["line"]};
    border-radius: 10px;
    padding: 16px;
    text-align: left;
}}
QPushButton.TierLauncherBtn:hover {{
    border-color: {T["line_hi"]};
    background: {T["raised"]};
}}

QPushButton[tierBtn="gradientCta"] {{
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
}}
"""


set_tier_theme("dark")


def _tier_btn(label: str, role: str) -> QPushButton:
    if role == "ghost":
        return _TierGhostButton(label)
    b = QPushButton(label)
    b.setProperty("tierBtn", role)
    size_px, css_weight = _TIER_BTN_FONT.get(role, (13, 600))
    b.setFont(_tier_inter_font(size_px, css_weight))
    b.style().unpolish(b)
    b.style().polish(b)
    return b


def _cta_chrome_color(c: QColor, *, sat: float = 0.82, bright: float = 0.93) -> QColor:
    """Slightly desaturate + darken CTA gradient stops."""
    h, s, v, a = c.getHsvF()
    return QColor.fromHsvF(h, max(0.0, min(1.0, s * sat)), max(0.0, min(1.0, v * bright)), a)


class _GradientCtaButton(QPushButton):
    """Pill CTA — cursor-following radial glow, layered chrome (L1)."""

    _H = 42  # ~8% below 46
    _ARROW = 15
    _SMOOTH = 0.15

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("tierBtn", "gradientCta")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self._H)
        self.setMinimumWidth(182)  # ~13% below 210
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._hover = False
        self._pressed = False
        self._glow_x = 0.5
        self._glow_y = 0.5
        self._target_glow_x = 0.5
        self._target_glow_y = 0.5

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick_animation)

        self._inner = QWidget(self)
        self._inner.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._inner.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self._inner)
        lay.setContentsMargins(16, 0, 14, 0)
        lay.setSpacing(8)

        txt = QLabel(label)
        txt.setFont(_tier_inter_font(13, 600))
        txt.setStyleSheet(_text_style(color=T["on_cta"]))
        txt.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(txt)
        lay.addStretch()

        self._arrow = QLabel()
        self._arrow.setFixedSize(self._ARROW, self._ARROW)
        self._arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._arrow.setStyleSheet("background: transparent;")
        lay.addWidget(self._arrow, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._sync_arrow_icon()

        self.style().unpolish(self)
        self.style().polish(self)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._inner.setGeometry(0, 0, self.width(), self.height())

    def _sync_arrow_icon(self) -> None:
        if not self.isEnabled():
            color = T["cta_arrow_disabled"]
        elif self._pressed:
            color = T["cta_arrow_pressed"]
        elif self._hover:
            color = T["cta_arrow_hover"]
        else:
            color = T["cta_arrow_idle"]
        px = lucide_icon("arrow-right", size=self._ARROW, color_hex=color).pixmap(
            self._ARROW, self._ARROW
        )
        self._arrow.setPixmap(px)

    def _ensure_anim_timer(self) -> None:
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _tick_animation(self) -> None:
        prev_gx, prev_gy = self._glow_x, self._glow_y
        self._glow_x += (self._target_glow_x - self._glow_x) * self._SMOOTH
        self._glow_y += (self._target_glow_y - self._glow_y) * self._SMOOTH

        moved = (
            abs(self._glow_x - prev_gx) > 0.0005
            or abs(self._glow_y - prev_gy) > 0.0005
        )
        if moved:
            self.update()

        settled = (
            abs(self._glow_x - self._target_glow_x) < 0.002
            and abs(self._glow_y - self._target_glow_y) < 0.002
        )
        if settled and not self._hover:
            self._anim_timer.stop()

    def _set_glow_target_from_pos(self, pos: QPointF) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        self._target_glow_x = max(0.0, min(1.0, pos.x() / w))
        self._target_glow_y = max(0.0, min(1.0, pos.y() / h))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._sync_arrow_icon()
        self._ensure_anim_timer()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._pressed = False
        self._target_glow_x = 0.5
        self._target_glow_y = 0.5
        self._sync_arrow_icon()
        self._ensure_anim_timer()
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._hover and self.isEnabled():
            self._set_glow_target_from_pos(event.position())
            self._ensure_anim_timer()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.isEnabled():
            self._pressed = True
            self._set_glow_target_from_pos(event.position())
            self._sync_arrow_icon()
            self._ensure_anim_timer()
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._pressed = False
        self._sync_arrow_icon()
        self._ensure_anim_timer()
        self.update()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_arrow_icon()
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = self.isEnabled()
        dpr = _dpr(painter=p, widget=self)
        px = _device_px(dpr)
        r = _snap_rect_to_device(QRectF(self.rect()), dpr)
        r = r.adjusted(px, px, -px, -px)
        radius = r.height() / 2.0

        if enabled and not self._pressed:
            shadow_y = r.bottom() + (3.0 if self._hover else 2.0)
            shadow_alpha = 28 if self._hover else 16
            shadow = QRadialGradient(r.center().x(), shadow_y, r.width() * 0.55)
            shadow.setColorAt(0.0, QColor(37, 99, 235, shadow_alpha))
            shadow.setColorAt(1.0, QColor(37, 99, 235, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(shadow)
            p.drawRoundedRect(r.adjusted(2, 4, -2, 6), radius, radius)

        if not enabled:
            base_left = _cta_chrome_color(QColor(T["cta_disabled_left"]))
            base_right = _cta_chrome_color(QColor(T["cta_disabled_right"]))
        elif self._pressed:
            base_left = QColor(29, 78, 216)
            base_right = QColor(37, 99, 235)
        elif self._hover:
            base_left = QColor(37, 99, 235)
            base_right = QColor(96, 165, 250)
        else:
            base_left = QColor(37, 99, 235)
            base_right = QColor(59, 130, 246)

        base = QLinearGradient(r.topLeft(), QPointF(r.right(), r.top()))
        base.setColorAt(0.0, base_left)
        base.setColorAt(1.0, base_right)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(r, radius, radius)

        if enabled and (self._hover or abs(self._glow_x - 0.5) > 0.01 or abs(self._glow_y - 0.5) > 0.01):
            cx = r.left() + r.width() * self._glow_x
            cy = r.top() + r.height() * self._glow_y
            glow_r = max(r.width(), r.height()) * (0.95 if self._pressed else 1.05)
            radial = QRadialGradient(cx, cy, glow_r)
            peak = 38 if self._pressed else (48 if self._hover else 0)
            radial.setColorAt(0.0, _accent_highlight_alpha_byte(peak))
            radial.setColorAt(0.22, QColor(147, 197, 253, int(peak * 0.55)))
            radial.setColorAt(0.55, QColor(124, 58, 237, int(peak * 0.2)))
            radial.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(radial)
            p.drawRoundedRect(r, radius, radius)

        if enabled:
            sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + r.height() * 0.5))
            sheen_alpha = 12 if self._pressed else (28 if self._hover else 18)
            sheen.setColorAt(0.0, _accent_highlight_alpha_byte(sheen_alpha))
            sheen.setColorAt(0.45, _accent_highlight_alpha_byte(4))
            sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(sheen)
            p.drawRoundedRect(r, radius, radius)

        if enabled and self._hover and not self._pressed:
            _stroke_pill_border(p, r, dpr=dpr, gradient=_cta_border_gradient())
        elif enabled:
            _stroke_pill_border(p, r, dpr=dpr, color=_cta_border_solid(pressed=self._pressed))
        else:
            border = QColor(T["cta_disabled_right"])
            border.setAlpha(T["cta_disabled_border_a"])
            _stroke_pill_border(p, r, dpr=dpr, color=border)

        if self.hasFocus() and enabled:
            focus_pen = QPen(QColor(96, 165, 250, 90), 2.0)
            focus_pen.setCosmetic(True)
            p.setPen(focus_pen)
            inset = r.adjusted(-2.5, -2.5, 2.5, 2.5)
            p.drawRoundedRect(inset, radius + 2.5, radius + 2.5)

        p.end()
        super().paintEvent(event)


class _TierGhostButton(QPushButton):
    """Ghost footer action — programmatic hover so global QDialog QPushButton rules cannot win."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setProperty("tierBtn", "ghost")
        size_px, css_weight = _TIER_BTN_FONT.get("ghost", (13, 600))
        self.setFont(_tier_inter_font(size_px, css_weight))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hover = False
        self._pressed = False
        self._sync_style()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._sync_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._pressed = False
        self._sync_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._sync_style()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._pressed = False
        self._sync_style()
        super().mouseReleaseEvent(event)

    def refresh_tier_theme(self) -> None:
        self._sync_style()

    def _sync_style(self) -> None:
        color = T["text"] if (self._hover or self._pressed) else T["ghost_text_idle"]
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                color: {color};
                border: none;
                border-radius: {T["radius_sm"]}px;
                padding: 10px 20px;
                min-height: 42px;
            }}
            """
        )


class _TierCloseButton(QPushButton):
    """Close — muted × (55%), hover circular chroma wash + full-opacity ×. No traffic-light red."""

    _SIZE = 28
    _ICON = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TierClose")
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False
        self._pressed = False

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dpr = _dpr(painter=p, widget=self)
        bounds = _snap_rect_to_device(QRectF(self.rect()), dpr)
        cx = bounds.center().x()
        cy = bounds.center().y()

        if self._hover or self._pressed:
            bg_a = T["close_hover_bg_a"] * (1.6 if self._pressed else 1.0)
            radius = min(bounds.width(), bounds.height()) / 2.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_chroma_color(bg_a))
            p.drawEllipse(QPointF(cx, cy), radius, radius)
            icon_opacity = 1.0
        else:
            icon_opacity = T["close_idle_a"]

        pix = lucide_icon("x", size=self._ICON, color_hex=T["chroma"]).pixmap(self._ICON, self._ICON)
        p.setOpacity(icon_opacity)
        p.drawPixmap(
            int(cx - self._ICON / 2),
            int(cy - self._ICON / 2),
            pix,
        )
        p.end()


def _tier_close_button(parent: QWidget | None = None) -> _TierCloseButton:
    btn = _TierCloseButton(parent)
    return btn


def _tier_chrome_band_height() -> int:
    """Top chrome strip — equal inset + close hit area (outer circle to frame)."""
    return T["chrome_close_inset"] + _TierCloseButton._SIZE


def _install_tier_chrome_close(dialog: QDialog) -> _TierCloseButton:
    close = _tier_close_button(dialog)
    close.clicked.connect(dialog.reject)
    return close


def _position_tier_chrome_close(dialog: QWidget, close: _TierCloseButton) -> None:
    frame = _tier_dialog_frame_rect(dialog)
    inset = T["chrome_close_inset"]
    close.move(int(frame.right()) - inset - close.width() + 1, int(frame.top()) + inset)


def _raise_tier_chrome_layers(
    dialog: QWidget,
    *,
    border: _TierBorderOverlay,
    close: _TierCloseButton,
) -> None:
    frame = _tier_dialog_frame_rect(dialog)
    border.setGeometry(int(frame.x()), int(frame.y()), int(frame.width()), int(frame.height()))
    border.raise_()
    _position_tier_chrome_close(dialog, close)
    close.raise_()


def _tier_footer_divider() -> QWidget:
    return _TierHairline()


class _TierHairline(QWidget):
    """1 device-pixel horizontal rule — avoids blurry QFrame at 125%/175% DPI."""

    def __init__(self, parent: QWidget | None = None, *, alpha: float | None = None) -> None:
        super().__init__(parent)
        self._alpha = alpha
        self.setFixedHeight(1)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        dpr = _dpr(painter=p, widget=self)
        line_y = round((self.height() * 0.5) * dpr) / dpr
        alpha = float(T["hairline_a"] if self._alpha is None else self._alpha)
        pen = QPen(_chroma_color(alpha), 1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLine(QPointF(0.0, line_y), QPointF(float(self.width()), line_y))
        p.end()


def _icon_btn(icon: str, color: str = T["label"]) -> QPushButton:
    b = QPushButton()
    b.setProperty("tierBtn", "icon")
    b.setIcon(lucide_icon(icon, size=14, color_hex=color))
    b.setIconSize(b.iconSize())
    b.style().unpolish(b)
    b.style().polish(b)
    return b


def _field_leading_icon(icon: str) -> QPushButton:
    """Muted inline icon inside a field shell (no separate bordered tile)."""
    ic = QPushButton()
    ic.setProperty("tierBtn", "fieldIcon")
    ic.setFlat(True)
    ic._field_icon_name = icon  # noqa: SLF001
    ic.setIcon(lucide_icon(icon, size=15, color_hex=T["meta"]))
    ic.setIconSize(QSize(15, 15))
    ic.setFixedSize(22, 22)
    ic.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    ic.setEnabled(False)
    ic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    ic.style().unpolish(ic)
    ic.style().polish(ic)
    return ic


def _sync_field_leading_icon(button: QPushButton, *, focused: bool) -> None:
    icon_name = getattr(button, "_field_icon_name", None)
    if not icon_name:
        return
    color = T["text"] if focused else T["meta"]
    button.setIcon(lucide_icon(icon_name, size=15, color_hex=color))


def _field_trailing_icon(icon: str, *, color: str = T["label"]) -> QPushButton:
    ic = QPushButton()
    ic.setProperty("tierBtn", "fieldAction")
    ic.setIcon(lucide_icon(icon, size=13, color_hex=color))
    ic.setIconSize(QSize(13, 13))
    ic.setFixedSize(18, 18)
    ic.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    ic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    ic.setCursor(Qt.CursorShape.PointingHandCursor)
    ic.setToolTip("Copy" if icon == "copy" else "Pick date…" if icon == "calendar" else "")
    ic._field_icon_name = icon  # noqa: SLF001
    ic._field_icon_color = color  # noqa: SLF001
    ic.installEventFilter(_FieldActionHoverFilter(ic))
    ic.style().unpolish(ic)
    ic.style().polish(ic)
    return ic


class _FieldActionHoverFilter(QObject):
    """Brighten field action icons on hover."""

    def __init__(self, button: QPushButton) -> None:
        super().__init__(button)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if not isinstance(obj, QPushButton):
            return False
        icon_name = getattr(obj, "_field_icon_name", None)
        if not icon_name:
            return False
        base_color = getattr(obj, "_field_icon_color", T["label"])
        if event.type() == QEvent.Type.Enter:
            obj.setIcon(lucide_icon(icon_name, size=13, color_hex=T["text"]))
            return False
        if event.type() == QEvent.Type.Leave:
            obj.setIcon(lucide_icon(icon_name, size=13, color_hex=base_color))
            return False
        return False


def _field_link_btn(label: str, *, compact: bool = False) -> QPushButton:
    b = _tier_btn(label, "link")
    b.setFixedHeight(24 if compact else 28)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


def _shell_height(*, readonly: bool = False) -> int:
    return T["field_readonly_h"] if readonly else T["field_h"]


_FIELD_ALIGN = Qt.AlignmentFlag.AlignVCenter
_FIELD_INPUT_STYLE = "border: none; background: transparent; padding: 0; margin: 0;"
_FIELD_INPUT_STYLE_MONO = "border: none; background: transparent; padding: 0; margin: 0;"


def _prepare_shell_input(widget: QWidget, *, readonly: bool = False) -> None:
    inner_h = _shell_height(readonly=readonly) - 2
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    widget.setMinimumHeight(inner_h)
    widget.setMaximumHeight(inner_h)


def _apply_field_input_font(edit: QLineEdit, *, mono: bool = False, readonly: bool = False) -> None:
    edit.setFont(_tier_font(12 if mono else 13, mono=mono))
    if mono:
        edit.setStyleSheet(_FIELD_INPUT_STYLE_MONO + f" color: {T['mono']};")
    else:
        edit.setStyleSheet(_FIELD_INPUT_STYLE)
    _prepare_shell_input(edit, readonly=readonly)


def _configure_field_shell_layout(
    sl: QHBoxLayout,
    *,
    left: int = 10,
    right: int = 6,
) -> None:
    sl.setContentsMargins(left, 0, right, 0)
    sl.setSpacing(8)
    sl.setAlignment(_FIELD_ALIGN)


def _add_shell_widget(sl: QHBoxLayout, widget: QWidget, *, stretch: int = 0) -> None:
    sl.addWidget(widget, stretch, _FIELD_ALIGN)


def _corner_radius(rect: QRectF, radius: float) -> float:
    return max(0.0, min(radius, rect.width() / 2, rect.height() / 2))


def _rounded_rect_left(rect: QRectF, radius: float) -> QPainterPath:
    """Rect with rounded top-left + bottom-left only."""
    r = _corner_radius(rect, radius)
    if r <= 0:
        path = QPainterPath()
        path.addRect(rect)
        return path
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    path.moveTo(x + r, y)
    path.lineTo(x + w, y)
    path.lineTo(x + w, y + h)
    path.lineTo(x + r, y + h)
    path.arcTo(x, y + h - 2 * r, 2 * r, 2 * r, 270.0, -90.0)
    path.lineTo(x, y + r)
    path.arcTo(x, y, 2 * r, 2 * r, 180.0, -90.0)
    path.closeSubpath()
    return path


def _rounded_rect_right(rect: QRectF, radius: float) -> QPainterPath:
    """Rect with rounded top-right + bottom-right only."""
    r = _corner_radius(rect, radius)
    if r <= 0:
        path = QPainterPath()
        path.addRect(rect)
        return path
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w - r, y)
    path.arcTo(x + w - 2 * r, y, 2 * r, 2 * r, 90.0, -90.0)
    path.lineTo(x + w, y + h - r)
    path.arcTo(x + w - 2 * r, y + h - 2 * r, 2 * r, 2 * r, 0.0, -90.0)
    path.lineTo(x, y + h)
    path.closeSubpath()
    return path


def _paint_l1_dialog_background(
    painter: QPainter,
    bounds: QRectF,
    *,
    seam_x: float,
    radius: int,
    seam_overlap: float = 1.0,
    widget: QWidget | None = None,
    grad_phase: float = 0.0,
) -> None:
    """L1 chrome — one outer rounded clip; sidebar nebula shares corner math with panel."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    dpr = _dpr(painter=painter, widget=widget)
    bounds = _snap_rect_to_device(bounds, dpr)
    r = float(radius)

    outer = QPainterPath()
    outer.addRoundedRect(bounds, r, r)
    painter.setClipPath(outer)

    painter.fillRect(bounds, QColor(T[SURFACE_DIALOG]))

    seam = seam_x - seam_overlap
    left_w = max(0.0, seam + seam_overlap - bounds.left())
    if left_w > 0:
        left_rect = QRectF(bounds.left(), bounds.top(), left_w, bounds.height())
        painter.fillRect(left_rect, QColor(T["sidebar"]))
        painter.save()
        painter.setClipRect(left_rect, Qt.ClipOperation.IntersectClip)
        _paint_sidebar_nebula(painter, left_rect, grad_phase=grad_phase)
        _paint_noise_overlay(painter, left_rect, grad_phase=grad_phase)
        painter.restore()

    painter.fillRect(QRectF(seam, bounds.top(), seam_overlap, bounds.height()), _hairline_color())


def _paint_l2_dialog_background(
    painter: QPainter,
    bounds: QRectF,
    *,
    seam_y: float,
    radius: int,
    seam_overlap: float = 1.0,
    widget: QWidget | None = None,
    grad_phase: float = 0.0,
) -> None:
    """L2 chrome — top bar strip (L1 sidebar nebula) + panel body below horizontal seam."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    dpr = _dpr(painter=painter, widget=widget)
    bounds = _snap_rect_to_device(bounds, dpr)
    r = float(radius)

    outer = QPainterPath()
    outer.addRoundedRect(bounds, r, r)
    painter.setClipPath(outer)

    painter.fillRect(bounds, QColor(T[SURFACE_DIALOG]))

    seam = seam_y + seam_overlap
    top_h = max(0.0, seam - bounds.top())
    if top_h > 0:
        top_rect = QRectF(bounds.left(), bounds.top(), bounds.width(), top_h)
        painter.fillRect(top_rect, QColor(T["sidebar"]))
        painter.save()
        painter.setClipRect(top_rect, Qt.ClipOperation.IntersectClip)
        _paint_sidebar_nebula(painter, top_rect, grad_phase=grad_phase)
        _paint_noise_overlay(painter, top_rect, grad_phase=grad_phase)
        painter.restore()

    painter.fillRect(QRectF(bounds.left(), seam_y, bounds.width(), seam_overlap), _hairline_color())


class _DragZone(QWidget):
    def __init__(self, dialog: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dlg = dialog
        self._o: QPoint | None = None
        self._w: QPoint | None = None

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._o = e.globalPosition().toPoint()
            self._w = self._dlg.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._o and self._w and e.buttons() & Qt.MouseButton.LeftButton:
            self._dlg.move(self._w + e.globalPosition().toPoint() - self._o)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._o = None
        self._w = None
        super().mouseReleaseEvent(e)


def _focus_border_color() -> QColor:
    return QColor(59, 130, 246, int(255 * T["focus_blue_a"]))


def _tier_rim_color(*, hovered: bool) -> QColor:
    alpha = T["border_hover_a"] if hovered else T["border_idle_a"]
    return _chroma_color(alpha)


class _TierBorderOverlay(QWidget):
    """Dialog rim drawn above content so it is never clipped by child widgets."""

    def __init__(self, parent: QWidget, *, radius: int) -> None:
        super().__init__(parent)
        self._radius = radius
        self._hover = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        parent.setMouseTracking(True)
        parent.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        parent.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.parent() and event.type() in (
            QEvent.Type.MouseMove,
            QEvent.Type.HoverMove,
            QEvent.Type.Enter,
            QEvent.Type.Leave,
        ):
            hovered = bool(obj.underMouse())
            if hovered != self._hover:
                self._hover = hovered
                self.update()
        return super().eventFilter(obj, event)

    def paintEvent(self, event) -> None:  # noqa: N802
        r = self.rect()
        if r.isEmpty():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(_tier_rim_color(hovered=self._hover), 1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        dpr = _dpr(painter=p, widget=self)
        inset = _snap_rect_to_device(QRectF(r), dpr)
        px = _device_px(dpr)
        inset = inset.adjusted(px, px, -px, -px)
        p.drawRoundedRect(inset, self._radius, self._radius)
        p.end()


class _TopBarPanel(QWidget):
    """L2 top bar — brand row; background painted by Tier2Dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")


class _SidebarPanel(QWidget):
    """L1 left rail — brand/content only; background painted by Tier1Dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")


class _TierRightPane(QWidget):
    """L1 right column — background painted by Tier1Dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)


class _FocusShell(QFrame):
    """Input shell that highlights border on child focus."""

    def __init__(self, parent: QWidget | None = None, *, readonly: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("TierFieldShell")
        self._readonly = readonly
        self._focused = False
        self._hovered = False
        self._focus_widget: QWidget | None = None
        self._leading_icon: QPushButton | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setProperty("readonly", "true" if readonly else "false")
        self.setFixedHeight(_shell_height(readonly=readonly))
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setMouseTracking(True)

    def _stroke_color(self) -> QColor:
        if self._focused:
            return _focus_border_color()
        alpha = T["border_hover_a"] if self._hovered else T["border_idle_a"]
        return _chroma_color(alpha)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def bind_focus(self, widget: QWidget) -> None:
        self._focus_widget = widget
        if isinstance(widget, QLineEdit):
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def bind_leading_icon(self, button: QPushButton) -> None:
        self._leading_icon = button

    def set_focused(self, on: bool) -> None:
        self._focused = on
        self.setProperty("focused", "true" if on else "false")
        if self._leading_icon is not None:
            _sync_field_leading_icon(self._leading_icon, focused=on)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        target = self._focus_widget
        if target is not None and event.button() == Qt.MouseButton.LeftButton:
            target.setFocus(Qt.FocusReason.MouseFocusReason)
            self.set_focused(True)
            if isinstance(target, QLineEdit):
                local = target.mapFrom(self, event.position().toPoint())
                target.setCursorPosition(target.cursorPositionAt(local))
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = QColor(T[SURFACE_FIELD])
        _paint_rounded_chrome(
            p,
            QRectF(self.rect()),
            fill=fill,
            stroke=self._stroke_color(),
            radius=float(T["radius_sm"]),
        )
        p.end()
        super().paintEvent(event)


def _make_field_label(text: str, *, style: str = "l1") -> QLabel:
    if style == "l2":
        lbl = QLabel(text)
        lbl.setFont(_tier_font(12))
        lbl.setStyleSheet(_text_style(color=T["label"]))
    else:
        lbl = QLabel(text.upper())
        lbl.setFont(_tier_font(10, QFont.Weight.Bold))
        lbl.setStyleSheet(_text_style(color=T["meta"], letter_spacing=T["label_tracking"]))
    return lbl


def _stack_field_column(
    label: QLabel,
    control: QWidget,
    *,
    hint: str = "",
) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(label)
    lay.addSpacing(T["field_label_gap"])
    lay.addWidget(control)
    if hint:
        h = QLabel(hint)
        h.setWordWrap(True)
        h.setFont(_tier_font(11))
        h.setStyleSheet(_text_style(color=T["meta"]))
        lay.addSpacing(T["field_hint_gap"])
        lay.addWidget(h)
    return lay


class _FieldRow(QWidget):
    """Label above a full-width field control."""

    def __init__(
        self,
        label: str,
        control: QWidget,
        *,
        hint: str = "",
        label_style: str = "l1",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = _stack_field_column(_make_field_label(label, style=label_style), control, hint=hint)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(lay)


class _InfoStrip(QFrame):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background: {T['raised']}; border: 1px solid {T['line']}; border-radius: {T['radius_sm']}px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        ic = _icon_btn("circle-help", T["meta"])
        ic.setEnabled(False)
        lay.addWidget(ic, alignment=Qt.AlignmentFlag.AlignTop)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setFont(_tier_font(12))
        body.setStyleSheet(_text_style(color=T["label"]))
        lay.addWidget(body, stretch=1)


class _InputWithSuffix(QWidget):
    """Input + trailing icon or link (copy…)."""

    def __init__(
        self,
        *,
        leading_icon: str | None = None,
        suffix_icon: str | None = None,
        suffix_label: str | None = None,
        readonly: bool = False,
        mono: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit()
        self.input.setObjectName("TierInput")
        self.input.setFrame(False)
        if readonly:
            self.input.setReadOnly(True)
            self.input.setProperty("readonly", True)
        if mono:
            self.input.setProperty("mono", True)
        _apply_field_input_font(self.input, mono=mono, readonly=readonly)
        self._shell = _FocusShell(readonly=readonly)
        sl = QHBoxLayout(self._shell)
        _configure_field_shell_layout(sl, right=6)
        if leading_icon:
            lead_ic = _field_leading_icon(leading_icon)
            _add_shell_widget(sl, lead_ic)
            self._shell.bind_leading_icon(lead_ic)
        _add_shell_widget(sl, self.input, stretch=1)
        if suffix_label:
            _add_shell_widget(sl, _field_link_btn(suffix_label, compact=readonly))
        elif suffix_icon:
            _add_shell_widget(sl, _field_trailing_icon(suffix_icon))
        lay.addWidget(self._shell)
        self._shell.bind_focus(self.input)
        self.input.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.input:
            if event.type() == QEvent.Type.FocusIn:
                self._shell.set_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._shell.set_focused(False)
        return super().eventFilter(obj, event)


class _PathField(QWidget):
    def __init__(
        self,
        path: str,
        *,
        browse_label: str = "Browse",
        leading_icon: str = "local",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._shell = _FocusShell(readonly=True)
        sl = QHBoxLayout(self._shell)
        _configure_field_shell_layout(sl, right=4)
        lead_ic = _field_leading_icon(leading_icon)
        _add_shell_widget(sl, lead_ic)
        self._shell.bind_leading_icon(lead_ic)
        self.path = QLineEdit(path)
        self.path.setObjectName("TierInput")
        self.path.setReadOnly(True)
        self.path.setProperty("readonly", True)
        self.path.setFrame(False)
        _apply_field_input_font(self.path, mono=True, readonly=True)
        _add_shell_widget(sl, self.path, stretch=1)
        _add_shell_widget(sl, _field_link_btn(browse_label, compact=True))
        lay.addWidget(self._shell)
        self._shell.bind_focus(self.path)


class _ElidedPathLabel(QLabel):
    """Read-only path with middle-elide — not an input."""

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TierSelectable")
        self._full_path = path
        self.setFont(_tier_font(12, mono=True))
        self.setStyleSheet(_metadata_value_style())
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._refresh_elide()

    def full_path(self) -> str:
        return self._full_path

    def set_path(self, path: str) -> None:
        self._full_path = path
        self._refresh_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_elide()

    def _refresh_elide(self) -> None:
        fm = QFontMetrics(self.font())
        w = max(1, self.width())
        elided = fm.elidedText(self._full_path, Qt.TextElideMode.ElideMiddle, w)
        self.setText(elided)
        self.setToolTip(self._full_path if elided != self._full_path else "")


class _MetaCardFrame(QFrame):
    """Metadata / path card — subtle border idle 8% → hover 14%."""

    def __init__(
        self,
        object_name: str,
        *,
        surface: str = SURFACE_CARD,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._surface = surface
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")
        self._hover = False
        self.setMouseTracking(True)

    def _stroke_color(self) -> QColor:
        return _surface_stroke(self._surface, hover=self._hover)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_rounded_chrome(
            p,
            QRectF(self.rect()),
            fill=QColor(T[self._surface]),
            stroke=self._stroke_color(),
            radius=float(T["radius_sm"]),
        )
        p.end()
        super().paintEvent(event)


class _DialogHero:
    """Approved brand block — gradient badge + title + subtitle (L1 sidebar / L2 top bar)."""

    @staticmethod
    def make_badge(icon: str, *, icon_size: int = 28) -> QFrame:
        badge = QFrame()
        badge.setFixedSize(52, 52)
        badge.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {T['blue']}, stop:1 {T['purple']});"
            f"border-radius: 14px; border: none;"
        )
        bl = QHBoxLayout(badge)
        bl.setContentsMargins(0, 0, 0, 0)
        ic = QLabel()
        ic.setPixmap(lucide_icon(icon, size=icon_size, color_hex=T["on_accent"]).pixmap(icon_size, icon_size))
        ic.setFixedSize(icon_size, icon_size)
        ic.setStyleSheet("background: transparent;")
        bl.addStretch()
        bl.addWidget(ic, alignment=Qt.AlignmentFlag.AlignCenter)
        bl.addStretch()
        return badge

    @classmethod
    def apply_sidebar(
        cls,
        body: QVBoxLayout,
        *,
        icon: str,
        title: str,
        subtitle: str,
        icon_size: int = 28,
    ) -> None:
        body.addWidget(cls.make_badge(icon, icon_size=icon_size), alignment=Qt.AlignmentFlag.AlignHCenter)
        body.addSpacing(4)
        t = QLabel(title)
        t.setWordWrap(True)
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        t.setFont(_tier_font(17, QFont.Weight.Bold))
        t.setStyleSheet(_text_style(color=T["text"]))
        body.addWidget(t)
        s = QLabel(subtitle)
        s.setWordWrap(True)
        s.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        s.setFont(_tier_font(12))
        s.setStyleSheet(_text_style(color=T["meta"]))
        body.addWidget(s)

    @classmethod
    def apply_topbar(
        cls,
        badge_lay: QHBoxLayout,
        title_lbl: QLabel,
        subtitle_lbl: QLabel,
        *,
        icon: str,
        title: str,
        subtitle: str,
        icon_size: int = 28,
    ) -> None:
        while badge_lay.count():
            item = badge_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        badge_lay.addWidget(cls.make_badge(icon, icon_size=icon_size))
        title_lbl.setText(title)
        subtitle_lbl.setText(subtitle)


class _MetadataCard(QWidget):
    """Read-only metadata block — Project ID card language (heading / value / body / footnote)."""

    def __init__(
        self,
        heading: str,
        *,
        value: str = "",
        body: str = "",
        footnote: str = "",
        mono: bool = False,
        copy_label: str | None = None,
        card_object_name: str = "MetadataCard",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._value_label: QLabel | None = None

        card = _MetaCardFrame(card_object_name, surface=SURFACE_POPUP)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(*_card_content_margins())
        lay.setSpacing(6)

        heading_lbl = QLabel(heading.upper())
        heading_lbl.setFont(_tier_font(10, QFont.Weight.Bold))
        heading_lbl.setStyleSheet(_text_style(color=T["meta"], letter_spacing=T["label_tracking"]))
        lay.addWidget(heading_lbl)

        if value:
            value_row = QHBoxLayout()
            value_row.setContentsMargins(0, 0, T["card_action_inset_r"] if copy_label else 0, 0)
            value_row.setSpacing(10)
            self._value_label = QLabel(value)
            self._value_label.setObjectName("TierSelectable")
            self._value_label.setFont(_tier_font(13, mono=mono))
            self._value_label.setStyleSheet(_metadata_value_style())
            self._value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._value_label.setWordWrap(True)
            value_row.addWidget(self._value_label, stretch=1)
            if copy_label:
                copy_btn = _field_link_btn(copy_label, compact=True)
                copy_btn.clicked.connect(self._copy_value)
                value_row.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
            lay.addLayout(value_row)

        if body:
            body_lbl = QLabel(body)
            body_lbl.setWordWrap(True)
            body_lbl.setFont(_tier_font(13))
            body_lbl.setStyleSheet(_text_style(color=T["body"]))
            lay.addWidget(body_lbl)

        if footnote:
            lay.addSpacing(int(T["card_footnote_top"]))
            rule = _TierHairline(alpha=float(T["card_footnote_divider_a"]))
            lay.addWidget(rule)
            lay.addSpacing(4)
            sub = QLabel(footnote)
            sub.setFont(_tier_font(10))
            sub.setStyleSheet(_text_style(color=T["meta"]))
            lay.addWidget(sub)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    def value(self) -> str:
        return self._value_label.text() if self._value_label is not None else ""

    def set_value(self, value: str) -> None:
        if self._value_label is not None:
            self._value_label.setText(value)

    def _copy_value(self) -> None:
        QGuiApplication.clipboard().setText(self.value())


class _WorkspaceCard(QWidget):
    """Path picker card — not a text input."""

    path_changed = Signal(str)

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        self._card = _MetaCardFrame("WorkspaceCard")

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(*_card_content_margins())
        card_lay.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(10)
        folder = QLabel()
        folder.setPixmap(lucide_icon("folder", size=16, color_hex=T["blue_hi"]).pixmap(16, 16))
        folder.setFixedSize(16, 16)
        folder.setStyleSheet("background: transparent;")
        row.addWidget(folder, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._path_label = _ElidedPathLabel(path)
        row.addWidget(self._path_label, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._copy_btn = _field_trailing_icon("copy", color=T["meta"])
        self._copy_btn.setToolTip("Copy path")
        self._copy_btn.setEnabled(False)
        self._copy_opacity = QGraphicsOpacityEffect(self._copy_btn)
        self._copy_opacity.setOpacity(0.0)
        self._copy_btn.setGraphicsEffect(self._copy_opacity)
        self._copy_btn.clicked.connect(self._copy_path)
        row.addWidget(self._copy_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        change_btn = _field_link_btn("Change folder", compact=True)
        change_btn.clicked.connect(self._browse)
        row.addWidget(change_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        card_lay.addLayout(row)

        self._card.installEventFilter(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._card:
            if event.type() == QEvent.Type.Enter:
                self._copy_opacity.setOpacity(1.0)
                self._copy_btn.setEnabled(True)
            elif event.type() == QEvent.Type.Leave:
                self._copy_opacity.setOpacity(0.0)
                self._copy_btn.setEnabled(False)
        return super().eventFilter(obj, event)

    @property
    def path(self) -> str:
        return self._path_label.full_path()

    def set_path(self, path: str) -> None:
        self._path_label.set_path(path)
        self.path_changed.emit(path)

    def _copy_path(self) -> None:
        QGuiApplication.clipboard().setText(self.path)

    def _browse(self) -> None:
        picked = QFileDialog.getExistingDirectory(
            self.window(),
            "Select Workspace",
            self.path,
        )
        if picked:
            self.set_path(picked)


class _ProjectIdMetaCard(_MetadataCard):
    """Immutable project ID — metadata card, not an input."""

    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(
            "PROJECT ID",
            value=value,
            footnote="Generated automatically",
            mono=True,
            copy_label="Copy",
            card_object_name="ProjectIdMeta",
            parent=parent,
        )


class _DateField(QWidget):
    """Date field with inline calendar icon + picker (TierFieldShell)."""

    def __init__(self, initial: QDate | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._shell = _FocusShell()
        sl = QHBoxLayout(self._shell)
        _configure_field_shell_layout(sl, right=4)
        lead_ic = _field_leading_icon("calendar")
        _add_shell_widget(sl, lead_ic)
        self._shell.bind_leading_icon(lead_ic)
        self.edit = QDateEdit()
        self.edit.setObjectName("TierDateEdit")
        self.edit.setCalendarPopup(False)
        self.edit.setDisplayFormat("yyyy-MM-dd")
        self.edit.setButtonSymbols(QDateEdit.ButtonSymbols.NoButtons)
        self.edit.setFrame(False)
        self.edit.setDate(initial or QDate.currentDate())
        self.edit.setFont(_tier_font(13))
        self.edit.setStyleSheet(_FIELD_INPUT_STYLE)
        _prepare_shell_input(self.edit)
        _add_shell_widget(sl, self.edit, stretch=1)
        pick = _field_trailing_icon("calendar")
        pick.setToolTip("Pick date…")
        pick.clicked.connect(self._open_picker)
        _add_shell_widget(sl, pick)
        lay.addWidget(self._shell)
        self._shell.bind_focus(self.edit)
        self.edit.installEventFilter(self)

    def date(self) -> QDate:
        return self.edit.date()

    def setDate(self, date: QDate) -> None:
        self.edit.setDate(date)

    def _open_picker(self) -> None:
        picked = run_date_picker_dialog(self.window(), initial=self.edit.date())
        if picked is not None and picked.isValid():
            self.edit.setDate(picked)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.edit:
            if event.type() == QEvent.Type.FocusIn:
                self._shell.set_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._shell.set_focused(False)
        return super().eventFilter(obj, event)


class _PlainFieldInput(QWidget):
    """Single-line field in a focus shell (L1 project name, L2 fields)."""

    def __init__(
        self,
        text: str = "",
        *,
        leading_icon: str | None = None,
        placeholder: str = "",
        readonly: bool = False,
        mono: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._shell = _FocusShell(readonly=readonly)
        sl = QHBoxLayout(self._shell)
        _configure_field_shell_layout(sl, right=10)
        if leading_icon:
            lead_ic = _field_leading_icon(leading_icon)
            _add_shell_widget(sl, lead_ic)
            self._shell.bind_leading_icon(lead_ic)
        self.input = QLineEdit(text)
        self.input.setObjectName("TierInput")
        self.input.setFrame(False)
        if placeholder:
            self.input.setPlaceholderText(placeholder)
        if readonly:
            self.input.setReadOnly(True)
            self.input.setProperty("readonly", True)
        if mono:
            self.input.setProperty("mono", True)
        _apply_field_input_font(self.input, mono=mono, readonly=readonly)
        _add_shell_widget(sl, self.input, stretch=1)
        lay.addWidget(self._shell)
        self._shell.bind_focus(self.input)
        self.input.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.input:
            if event.type() == QEvent.Type.FocusIn:
                self._shell.set_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._shell.set_focused(False)
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# Tier 1 — two-pane (New Project)
# ---------------------------------------------------------------------------


class Tier1Dialog(MonosDialog):
    """L1 shell: sidebar + content, subtle outer stroke only."""

    _tier_radius = T["radius_l1"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set_dialog_border_overlay_enabled(False)
        self.setModal(True)
        self.setObjectName("TierRoot")
        shadow_m = _tier_dialog_shadow_margin()
        self.resize(T["l1_w"] + 2 * shadow_m, T["l1_h"] + 2 * shadow_m)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(shadow_m, shadow_m, shadow_m, shadow_m)
        outer.setSpacing(0)

        self._sidebar = _SidebarPanel()
        self._sidebar.setFixedWidth(int(T["l1_w"] * T["l1_sidebar_frac"]))
        sb_lay = QVBoxLayout(self._sidebar)
        sb_lay.setContentsMargins(
            T["l1_sidebar_pad_x"],
            T["l1_sidebar_brand_top"],
            T["l1_sidebar_pad_x"],
            T["l1_sidebar_pad_b"],
        )
        sb_lay.setSpacing(0)
        self._sidebar_brand = QWidget()
        self._sidebar_brand.setStyleSheet("background: transparent;")
        self._sidebar_body = QVBoxLayout(self._sidebar_brand)
        self._sidebar_body.setSpacing(14)
        self._sidebar_body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        sb_lay.addStretch(1)
        sb_lay.addWidget(self._sidebar_brand)
        sb_lay.addStretch(1)
        outer.addWidget(self._sidebar)

        right = _TierRightPane()
        right.setObjectName("TierRightPane")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        top = _DragZone(self)
        top.setFixedHeight(_tier_chrome_band_height())
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(top)

        self._form = QVBoxLayout()
        form_w = QWidget()
        form_w.setStyleSheet("background: transparent;")
        form_w.setLayout(self._form)
        self._form.setContentsMargins(T["chrome_pad_x"], T["form_pad_top"], T["chrome_pad_x"], 8)
        self._form.setSpacing(T["form_spacing_y"])
        rl.addWidget(form_w, stretch=1)

        foot = QWidget()
        foot.setStyleSheet("background: transparent;")
        foot_lay = QVBoxLayout(foot)
        foot_lay.setContentsMargins(T["chrome_pad_x"], 0, T["chrome_pad_x"], 24)
        foot_lay.setSpacing(0)
        foot_lay.addWidget(_tier_footer_divider())
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        fl = QHBoxLayout(btn_row)
        fl.setContentsMargins(0, 16, 0, 0)
        fl.addStretch()
        self._foot = QHBoxLayout()
        self._foot.setSpacing(16)
        fl.addLayout(self._foot)
        foot_lay.addWidget(btn_row)
        rl.addWidget(foot)
        outer.addWidget(right, stretch=1)

        self._tier_border = _TierBorderOverlay(self, radius=T["radius_l1"])
        self._close = _install_tier_chrome_close(self)

        self._sidebar_grad_phase = 0.0
        self._sidebar_grad_timer = QTimer(self)
        self._sidebar_grad_timer.setInterval(max(16, int(1000 / T["l1_grad_fps"])))
        self._sidebar_grad_timer.timeout.connect(self._tick_sidebar_grad)

    def _tick_sidebar_grad(self) -> None:
        self._sidebar_grad_phase += 0.04
        self.update()

    def _raise_tier_border(self) -> None:
        _raise_tier_chrome_layers(self, border=self._tier_border, close=self._close)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._tier_border.show()
        self._sidebar_grad_timer.start()
        self._raise_tier_border()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._sidebar_grad_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._raise_tier_border()

    def paintEvent(self, event) -> None:  # noqa: N802
        frame = _tier_dialog_frame_rect(self)
        if not frame.isEmpty():
            p = QPainter(self)
            _paint_tier_dialog_shadow(p, frame, radius=float(self._tier_radius), widget=self)
            seam_x = float(self._sidebar.geometry().right())
            _paint_l1_dialog_background(
                p,
                frame,
                seam_x=seam_x,
                radius=self._tier_radius,
                widget=self,
                grad_phase=self._sidebar_grad_phase,
            )
            p.end()
        super(MonosDialog, self).paintEvent(event)

    def set_sidebar_brand(
        self,
        *,
        icon: str,
        title: str,
        subtitle: str,
        icon_size: int = 28,
    ) -> None:
        _DialogHero.apply_sidebar(
            self._sidebar_body,
            icon=icon,
            title=title,
            subtitle=subtitle,
            icon_size=icon_size,
        )

    def add_field(self, row: QWidget) -> None:
        self._form.addWidget(row)

    def add_form_stretch(self) -> None:
        self._form.addStretch(1)

    def add_form_spacing(self, px: int) -> None:
        self._form.addSpacing(px)

    def add_widget(self, w: QWidget) -> None:
        self._form.addWidget(w)

    def add_footer_btn(self, label: str, role: str, *, slot=None) -> QPushButton:
        b = _tier_btn(label, role)
        if slot:
            b.clicked.connect(slot)
        self._foot.addWidget(b)
        return b

    def add_footer_cta(self, label: str, *, slot=None) -> _GradientCtaButton:
        b = _GradientCtaButton(label)
        if slot:
            b.clicked.connect(slot)
        self._foot.addWidget(b)
        return b


class _ComboField(QWidget):
    def __init__(self, items: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._shell = _FocusShell()
        sl = QHBoxLayout(self._shell)
        _configure_field_shell_layout(sl, left=4, right=4)
        self.combo = QComboBox()
        self.combo.setObjectName("TierCombo")
        self.combo.setFont(_tier_font(13))
        self.combo.addItems(items)
        self.combo.setMinimumWidth(280)
        _prepare_shell_input(self.combo)
        sl.addWidget(self.combo, stretch=1, alignment=_FIELD_ALIGN)
        lay.addWidget(self._shell)
        self._shell.bind_focus(self.combo)
        self.combo.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.combo:
            if event.type() == QEvent.Type.FocusIn:
                self._shell.set_focused(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._shell.set_focused(False)
        return super().eventFilter(obj, event)


class Tier2Dialog(MonosDialog):
    """L2 shell: L1 vocabulary with contextual top bar (sidebar → horizontal strip) + form + footer.

    Height is content-tiered (compact / standard / tall) — narrower than L1, not a fixed square.
  """

    _tier_radius = T["radius_l2"]

    def __init__(self, parent: QWidget | None = None, *, l2_height: int | None = None) -> None:
        super().__init__(parent)
        self.set_dialog_border_overlay_enabled(False)
        self.setModal(True)
        self.setObjectName("TierRoot")
        self._l2_content_h = int(l2_height if l2_height is not None else T["l2_h"])
        self._apply_l2_size(self._l2_content_h)

        outer = QVBoxLayout(self)
        shadow_m = _tier_dialog_shadow_margin()
        outer.setContentsMargins(shadow_m, shadow_m, shadow_m, shadow_m)
        outer.setSpacing(0)

        top_drag = _DragZone(self)
        top_drag.setFixedHeight(T["l2_topbar_h"])
        self._topbar_drag = top_drag
        top_lay = QHBoxLayout(top_drag)
        top_lay.setContentsMargins(T["chrome_pad_x"], 0, T["chrome_pad_x"], 0)
        top_lay.setSpacing(14)
        top_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._topbar = _TopBarPanel()
        self._topbar_body = QHBoxLayout(self._topbar)
        self._topbar_body.setContentsMargins(0, 0, 0, 0)
        self._topbar_body.setSpacing(14)
        self._topbar_text = QVBoxLayout()
        self._topbar_text.setSpacing(4)
        self._topbar_text.setContentsMargins(0, 0, 0, 0)
        self._topbar_title = QLabel("")
        self._topbar_title.setFont(_tier_inter_font(17, 700))
        self._topbar_title.setStyleSheet(_text_style(color=T["text"]))
        self._topbar_subtitle = QLabel("")
        self._topbar_subtitle.setWordWrap(True)
        self._topbar_subtitle.setFont(_tier_font(12))
        self._topbar_subtitle.setStyleSheet(_text_style(color=T["meta"]))
        self._topbar_text.addWidget(self._topbar_title)
        self._topbar_text.addWidget(self._topbar_subtitle)
        self._topbar_badge_host = QWidget()
        self._topbar_badge_host.setStyleSheet("background: transparent;")
        self._topbar_badge_lay = QHBoxLayout(self._topbar_badge_host)
        self._topbar_badge_lay.setContentsMargins(0, 0, 0, 0)
        self._topbar_body.addWidget(self._topbar_badge_host, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._topbar_body.addLayout(self._topbar_text)
        self._topbar_body.addStretch(1)
        top_lay.addWidget(self._topbar, stretch=1)
        outer.addWidget(top_drag)

        body = _TierRightPane()
        body.setObjectName("TierL2Body")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._form = QVBoxLayout()
        fw = QWidget()
        fw.setStyleSheet("background: transparent;")
        fw.setLayout(self._form)
        self._form.setContentsMargins(T["chrome_pad_x"], T["form_pad_top"], T["chrome_pad_x"], 8)
        self._form.setSpacing(T["form_spacing_y"])
        body_lay.addWidget(fw, stretch=1)

        foot = QWidget()
        foot.setStyleSheet("background: transparent;")
        foot_lay = QVBoxLayout(foot)
        foot_lay.setContentsMargins(T["chrome_pad_x"], 0, T["chrome_pad_x"], 24)
        foot_lay.setSpacing(0)
        foot_lay.addWidget(_tier_footer_divider())
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        fl = QHBoxLayout(btn_row)
        fl.setContentsMargins(0, 16, 0, 0)
        fl.addStretch()
        self._foot = QHBoxLayout()
        self._foot.setSpacing(16)
        fl.addLayout(self._foot)
        foot_lay.addWidget(btn_row)
        body_lay.addWidget(foot)
        outer.addWidget(body, stretch=1)

        self._tier_border = _TierBorderOverlay(self, radius=T["radius_l2"])
        self._close = _install_tier_chrome_close(self)

        self._topbar_grad_phase = 0.0
        self._topbar_grad_timer = QTimer(self)
        self._topbar_grad_timer.setInterval(max(16, int(1000 / T["l1_grad_fps"])))
        self._topbar_grad_timer.timeout.connect(self._tick_topbar_grad)

    def _apply_l2_size(self, content_h: int) -> None:
        shadow_m = _tier_dialog_shadow_margin()
        self._l2_content_h = max(int(T["l2_topbar_h"]) + 120, int(content_h))
        self.resize(T["l2_w"] + 2 * shadow_m, self._l2_content_h + 2 * shadow_m)

    def set_l2_height(self, content_h: int) -> None:
        """Resize L2 shell — use ``l2_h_compact`` / ``l2_h`` / ``l2_h_tall`` from ``T``."""
        self._apply_l2_size(content_h)

    def _tick_topbar_grad(self) -> None:
        self._topbar_grad_phase += 0.04
        self.update()

    def _raise_tier_border(self) -> None:
        _raise_tier_chrome_layers(self, border=self._tier_border, close=self._close)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._tier_border.show()
        self._topbar_grad_timer.start()
        self._raise_tier_border()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._topbar_grad_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._raise_tier_border()

    def set_topbar_brand(
        self,
        *,
        icon: str,
        title: str,
        subtitle: str,
        icon_size: int = 28,
    ) -> None:
        _DialogHero.apply_topbar(
            self._topbar_badge_lay,
            self._topbar_title,
            self._topbar_subtitle,
            icon=icon,
            title=title,
            subtitle=subtitle,
            icon_size=icon_size,
        )

    def set_hero(self, *, icon: str, title: str, subtitle: str) -> None:
        """Alias for ``set_topbar_brand`` (harness compatibility)."""
        self.set_topbar_brand(icon=icon, title=title, subtitle=subtitle)

    def add_field(self, row: QWidget) -> None:
        self._form.addWidget(row)

    def add_form_stretch(self) -> None:
        self._form.addStretch(1)

    def add_form_spacing(self, px: int) -> None:
        self._form.addSpacing(px)

    def add_l2_field(self, label: str, widget: QWidget, *, hint: str = "") -> None:
        self.add_field(_FieldRow(label, widget, hint=hint, label_style="l1"))

    def add_widget(self, w: QWidget) -> None:
        self._form.addWidget(w)

    def add_footer_btn(self, label: str, role: str, *, slot=None) -> QPushButton:
        b = _tier_btn(label, role)
        if slot:
            b.clicked.connect(slot)
        self._foot.addWidget(b)
        return b

    def add_footer_cta(self, label: str, *, slot=None) -> _GradientCtaButton:
        b = _GradientCtaButton(label)
        if slot:
            b.clicked.connect(slot)
        self._foot.addWidget(b)
        return b

    def paintEvent(self, event) -> None:  # noqa: N802
        frame = _tier_dialog_frame_rect(self)
        if not frame.isEmpty():
            p = QPainter(self)
            _paint_tier_dialog_shadow(p, frame, radius=float(self._tier_radius), widget=self)
            seam_y = float(self._topbar_drag.geometry().bottom())
            _paint_l2_dialog_background(
                p,
                frame,
                seam_y=seam_y,
                radius=self._tier_radius,
                widget=self,
                grad_phase=self._topbar_grad_phase,
            )
            p.end()
        super(MonosDialog, self).paintEvent(event)




# ---------------------------------------------------------------------------
# Design system public names (golden reference vocabulary)
# ---------------------------------------------------------------------------

GradientPrimaryButton = _GradientCtaButton
GhostButton = _TierGhostButton
DialogCloseButton = _TierCloseButton
FieldShell = _FocusShell
MetadataCardFrame = _MetaCardFrame
MetadataCard = _MetadataCard
ProjectIdMetadataCard = _ProjectIdMetaCard
DialogHero = _DialogHero
WorkspacePicker = _WorkspaceCard
FieldRow = _FieldRow
DialogHairline = _TierHairline
InfoStrip = _InfoStrip
PlainFieldInput = _PlainFieldInput
PathField = _PathField
DateField = _DateField
ComboField = _ComboField
InputWithSuffix = _InputWithSuffix

from monostudio.ui_qt.dialog_tier.mono_select import (  # noqa: E402
    MonoSelect,
    MonoSelectItem,
    MonoSelectOption,
    mono_select_options_from_strings,
)

tier_btn = _tier_btn
tier_font = _tier_font
tier_close_button = _tier_close_button
tier_footer_divider = _tier_footer_divider
tier_chrome_band_height = _tier_chrome_band_height
make_field_label = _make_field_label
stack_field_column = _stack_field_column

__all__ = [
    "T",
    "T_METRICS",
    "TIERS_QSS",
    "CURRENT_THEME",
    "set_tier_theme",
    "apply_tier_app_theme",
    "configure_tier_text_rendering",
    "Tier1Dialog",
    "Tier2Dialog",
    "GradientPrimaryButton",
    "GhostButton",
    "DialogCloseButton",
    "FieldShell",
    "MetadataCard",
    "MetadataCardFrame",
    "DialogHero",
    "ProjectIdMetadataCard",
    "WorkspacePicker",
    "FieldRow",
    "DialogHairline",
    "InfoStrip",
    "PlainFieldInput",
    "PathField",
    "DateField",
    "ComboField",
    "InputWithSuffix",
    "MonoSelect",
    "MonoSelectItem",
    "MonoSelectOption",
    "mono_select_options_from_strings",
    "tier_btn",
    "tier_font",
    "tier_close_button",
    "tier_footer_divider",
    "tier_chrome_band_height",
    "make_field_label",
    "stack_field_column",
]
