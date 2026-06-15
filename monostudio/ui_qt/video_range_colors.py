"""Shared frame-range colors for timeline and range list."""

from __future__ import annotations

import hashlib

from PySide6.QtGui import QColor

from monostudio.ui_qt.style import MONOS_COLORS

_RANGE_PALETTE: tuple[tuple[int, int, int], ...] = (
    (34, 197, 94),
    (234, 179, 8),
    (168, 85, 247),
    (249, 115, 22),
    (236, 72, 153),
    (20, 184, 166),
)


def range_palette_rgb(range_id: str) -> tuple[int, int, int]:
    digest = hashlib.md5(range_id.encode("utf-8")).hexdigest()
    idx = int(digest[:2], 16) % len(_RANGE_PALETTE)
    return _RANGE_PALETTE[idx]


def range_color_hex(range_id: str, *, active: bool = False) -> str:
    if active:
        return MONOS_COLORS.get("blue_400", "#60a5fa")
    r, g, b = range_palette_rgb(range_id)
    return f"#{r:02x}{g:02x}{b:02x}"


def range_color_qcolor(range_id: str, *, active: bool = False, alpha: int = 255) -> QColor:
    if active:
        return QColor(MONOS_COLORS.get("blue_400", "#60a5fa"))
    r, g, b = range_palette_rgb(range_id)
    return QColor(r, g, b, alpha)
