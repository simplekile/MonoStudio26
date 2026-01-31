from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from monostudio.ui_qt.style import MONOS_COLORS


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _lucide_icons_dir() -> Path:
    return _repo_root() / "monostudio_data" / "icons" / "lucide"


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


@lru_cache(maxsize=512)
def lucide_icon(name: str, *, size: int = 16, color_hex: str | None = None) -> QIcon:
    """
    Render a Lucide SVG into a QIcon at a fixed size.
    - Uses cached pixmaps (no per-paint parsing)
    - Replaces "currentColor" with provided color
    """
    svg = _read_svg_text(name)
    if not svg:
        return QIcon()

    color = (color_hex or MONOS_COLORS["text_label"]).strip()
    svg = svg.replace("currentColor", color)

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

