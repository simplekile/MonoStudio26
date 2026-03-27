from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from monostudio.core.app_paths import get_app_base_path
from monostudio.ui_qt.style import MONOS_COLORS


def _brands_dir() -> Path:
    return get_app_base_path() / "monostudio_data" / "icons" / "brands"


def _brand_svg_path(slug: str) -> Path:
    return _brands_dir() / f"{slug}.svg"


def _brand_png_path(slug: str) -> Path:
    return _brands_dir() / f"{slug}.png"


def _read_svg_text(slug: str) -> str | None:
    p = _brand_svg_path(slug)
    try:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_png(slug: str) -> QPixmap | None:
    p = _brand_png_path(slug)
    try:
        if not p.is_file():
            return None
        pm = QPixmap(str(p))
        if pm.isNull():
            return None
        return pm
    except OSError:
        return None


def _brand_files_cache_token(slug: str) -> tuple[int, int]:
    """
    Token to bust brand_icon cache when icon files change on disk.
    (mtime_ns for svg, mtime_ns for png; 0 when missing/unreadable)
    """
    svg_m = 0
    png_m = 0
    try:
        p = _brand_svg_path(slug)
        if p.is_file():
            svg_m = p.stat().st_mtime_ns
    except OSError:
        svg_m = 0
    try:
        p = _brand_png_path(slug)
        if p.is_file():
            png_m = p.stat().st_mtime_ns
    except OSError:
        png_m = 0
    return (svg_m, png_m)


def _apply_fill(svg: str, color_hex: str) -> str:
    """
    Simple Icons SVGs often omit `fill`; set a default fill on the root <svg>.
    This keeps the badge consistent with MONOS dark UI.
    """
    svg = (svg or "").strip()
    if not svg:
        return svg
    # Add/override root fill. This is safe for single-color brand marks.
    if "<svg" in svg:
        # If fill exists on root, replace it.
        if "fill=" in svg.split(">")[0]:
            head, rest = svg.split(">", 1)
            # naive replace in head only
            import re

            head = re.sub(r'fill=\"[^\"]*\"', f'fill=\"{color_hex}\"', head)
            return head + ">" + rest
        # else inject fill into the root tag
        return svg.replace("<svg", f"<svg fill=\"{color_hex}\"", 1)
    return svg


def _render_brand_pixmap(renderer: QSvgRenderer, size_px: int) -> QPixmap:
    """Render SVG into a pixmap at size_px x size_px (antialiased)."""
    pix = QPixmap(size_px, size_px)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(p, QRect(0, 0, size_px, size_px))
    finally:
        p.end()
    return pix


@lru_cache(maxsize=256)
def _brand_icon_cached(slug: str, size: int, color_hex: str | None, _token: tuple[int, int]) -> QIcon:
    """
    Render a brand SVG (from monostudio_data/icons/brands) into a QIcon at fixed size.
    Cached to avoid per-paint parsing.
    Adds @1x and @2x pixmaps so icons stay sharp on HiDPI.
    """
    svg = _read_svg_text(slug)
    if not svg:
        # Fallback: allow raster brand icon (e.g. official product icon) when SVG is unavailable.
        pm = _read_png(slug)
        if pm is None:
            return QIcon()
        pm_1x = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        out = QIcon(pm_1x)
        pm_2x = pm.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm_2x.setDevicePixelRatio(2.0)
        out.addPixmap(pm_2x)
        return out
    color = (color_hex or MONOS_COLORS["text_primary"]).strip()
    svg = _apply_fill(svg, color)

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()

    pix_1x = _render_brand_pixmap(renderer, size)
    out = QIcon(pix_1x)
    pix_2x = _render_brand_pixmap(renderer, size * 2)
    pix_2x.setDevicePixelRatio(2.0)
    out.addPixmap(pix_2x)
    return out


def brand_icon(slug: str, *, size: int = 16, color_hex: str | None = None) -> QIcon:
    # Include file mtimes in the cache key so changes are reflected without needing an app restart.
    return _brand_icon_cached(slug, int(size), color_hex, _brand_files_cache_token(slug))

