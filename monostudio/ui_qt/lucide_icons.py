from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from monostudio.core.app_paths import get_app_base_path
from monostudio.ui_qt.style import MONOS_COLORS


def _lucide_icons_dir() -> Path:
    return get_app_base_path() / "monostudio_data" / "icons" / "lucide"


def _lucide_svg_path(name: str) -> Path:
    # File names match lucide: "eye", "download", "layout-dashboard", ...
    return _lucide_icons_dir() / f"{name}.svg"


def _read_svg_text(name: str) -> str | None:
    p = _lucide_svg_path(name)
    try:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _render_lucide_pixmap(renderer: QSvgRenderer, size_px: int) -> QPixmap:
    """Render SVG into a pixmap at size_px x size_px (antialiased)."""
    pix = QPixmap(size_px, size_px)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(p, QRect(0, 0, size_px, size_px))
    finally:
        p.end()
    return pix


@lru_cache(maxsize=512)
def lucide_icon(name: str, *, size: int = 16, color_hex: str | None = None) -> QIcon:
    """
    Render a Lucide SVG into a QIcon at a fixed size.
    - Uses cached pixmaps (no per-paint parsing)
    - Replaces "currentColor" with provided color
    - Adds @1x and @2x pixmaps so icons stay sharp on HiDPI (no blur)
    """
    svg = _read_svg_text(name)
    if not svg:
        return QIcon()

    color = (color_hex or MONOS_COLORS["text_label"]).strip()
    svg = svg.replace("currentColor", color)

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()

    # @1x: logical size (e.g. 16x16)
    pix_1x = _render_lucide_pixmap(renderer, size)
    out = QIcon(pix_1x)

    # @2x: double resolution so HiDPI displays use crisp icon (no scaling blur)
    pix_2x = _render_lucide_pixmap(renderer, size * 2)
    pix_2x.setDevicePixelRatio(2.0)
    out.addPixmap(pix_2x)

    return out

