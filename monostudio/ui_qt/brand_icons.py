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


def _read_svg_text(slug: str) -> str | None:
    p = _brand_svg_path(slug)
    try:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


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


@lru_cache(maxsize=256)
def brand_icon(slug: str, *, size: int = 16, color_hex: str | None = None) -> QIcon:
    """
    Render a brand SVG (from monostudio_data/icons/brands) into a QIcon at fixed size.
    Cached to avoid per-paint parsing.
    """
    svg = _read_svg_text(slug)
    if not svg:
        return QIcon()
    color = (color_hex or MONOS_COLORS["text_primary"]).strip()
    svg = _apply_fill(svg, color)

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        renderer.render(p, QRect(0, 0, size, size))
    finally:
        p.end()
    return QIcon(pix)

