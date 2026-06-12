"""Shared grid card label painting for Inbox/Outbox browsers."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QColor, QPixmap

from monostudio.ui_qt.style import monos_font


def _fit_wrapped_elided_lines(
    fm: QFontMetrics,
    text: str,
    width: int,
    max_lines: int,
) -> list[str]:
    """Word-wrap into at most *max_lines* rows; overflow becomes … on the last row."""
    text = str(text or "").strip()
    if not text or max_lines <= 0:
        return [text] if text else [""]
    if max_lines == 1 or width <= 0:
        return [fm.elidedText(text, Qt.TextElideMode.ElideRight, max(1, width))]

    words = text.split()
    if not words:
        return [fm.elidedText(text, Qt.TextElideMode.ElideRight, width)]

    lines: list[str] = []
    i = 0
    while i < len(words) and len(lines) < max_lines:
        line = words[i]
        i += 1
        while i < len(words):
            trial = f"{line} {words[i]}"
            if fm.horizontalAdvance(trial) <= width:
                line = trial
                i += 1
            else:
                break

        if len(lines) == max_lines - 1 and i < len(words):
            rest = " ".join([line, *words[i:]])
            lines.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, width))
            return lines

        if fm.horizontalAdvance(line) > width:
            line = fm.elidedText(line, Qt.TextElideMode.ElideRight, width)
        lines.append(line)

    return lines


def _draw_centered_lines(
    p: QPainter,
    rect: QRect,
    lines: list[str],
    fm: QFontMetrics,
) -> None:
    line_h = fm.height()
    if not lines:
        return
    total_h = line_h * len(lines)
    y = rect.top() + max(0, (rect.height() - total_h) // 2)
    for line in lines:
        line_rect = QRect(rect.left(), y, rect.width(), line_h)
        p.drawText(
            line_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            line,
        )
        y += line_h


def _draw_top_aligned_lines(
    p: QPainter,
    rect: QRect,
    lines: list[str],
    fm: QFontMetrics,
) -> None:
    line_h = fm.height()
    y = rect.top()
    for line in lines:
        if y + line_h > rect.bottom():
            break
        line_rect = QRect(rect.left(), y, rect.width(), line_h)
        p.drawText(
            line_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            line,
        )
        y += line_h


FILE_GRID_FALLBACK_ICON_MIN_PX = 48
FILE_GRID_FALLBACK_ICON_MAX_PX = 84
# Share of the 16:9 preview band used for folder / type icons (no image thumb).
FILE_GRID_FALLBACK_ICON_BAND_RATIO = 0.68


def grid_card_fallback_icon_px(band_rect: QRect) -> int:
    """Logical icon size for a grid card preview band (sharp @1x/@2x via QIcon.paint)."""
    side = min(max(1, band_rect.width()), max(1, band_rect.height()))
    px = int(side * FILE_GRID_FALLBACK_ICON_BAND_RATIO)
    return max(FILE_GRID_FALLBACK_ICON_MIN_PX, min(FILE_GRID_FALLBACK_ICON_MAX_PX, px))


def paint_grid_card_fill(p: QPainter, rect: QRect, *, bg: QColor, radius: int) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(rect, radius, radius)


def paint_grid_card_border(
    p: QPainter,
    rect: QRect,
    *,
    border: QColor,
    radius: int,
    width: int = 1,
) -> None:
    """Stroke on top of all card content (thumb, labels, drop highlight)."""
    p.setClipping(False)
    w = max(1, int(width))
    pen = QPen(border)
    pen.setWidthF(float(w))
    pen.setCosmetic(True)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    inset = max(1, (w + 1) // 2)
    p.drawRoundedRect(
        QRectF(rect).adjusted(inset, inset, -inset, -inset),
        radius,
        radius,
    )


def paint_grid_card_tag_badges(
    p: QPainter,
    thumb_rect: QRect,
    tag_ids: list[str],
    color_map: dict[str, str],
    *,
    pixmap_cache: dict[str, QPixmap],
    icon_px: int = 14,
    margin: int = 6,
    spacing: int = 3,
) -> None:
    """Colored tag icons on a dark plate (top-right of grid card thumbnail band)."""
    if not tag_ids or thumb_rect.width() <= 0 or thumb_rect.height() <= 0:
        return
    from monostudio.ui_qt.lucide_icons import lucide_icon

    visible_ids = [tid for tid in tag_ids if color_map.get(tid)]
    if not visible_ids:
        return

    pad_x, pad_y = 5, 4
    count = len(visible_ids)
    plate_w = count * icon_px + max(0, count - 1) * spacing + pad_x * 2
    plate_h = icon_px + pad_y * 2
    plate_right = thumb_rect.right() - margin
    plate_top = thumb_rect.top() + margin
    plate = QRect(plate_right - plate_w, plate_top, plate_w, plate_h)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    plate_path = QPainterPath()
    plate_path.addRoundedRect(QRectF(plate), 6.0, 6.0)
    p.fillPath(plate_path, QColor(24, 24, 27, 248))
    p.setPen(QPen(QColor(255, 255, 255, 72), 1.0))
    p.drawPath(plate_path)

    x = plate.right() - pad_x
    y = plate.top() + pad_y
    for tag_id in reversed(visible_ids):
        color_hex = color_map[tag_id]
        cached = pixmap_cache.get(color_hex)
        if cached is None:
            ic = lucide_icon("tag-filled", size=icon_px, color_hex=color_hex)
            cached = ic.pixmap(icon_px, icon_px)
            pixmap_cache[color_hex] = cached
        x -= icon_px
        p.drawPixmap(x, y, cached)
        x -= spacing
    p.restore()


def paint_grid_card_icon_band(
    p: QPainter,
    band_rect: QRect,
    icon: QIcon,
    *,
    icon_px: int | None = None,
    band_fill: QColor | None = None,
) -> None:
    """Center a type icon in the upper preview band when no image thumbnail is shown."""
    if band_rect.width() <= 0 or band_rect.height() <= 0:
        return
    if band_fill is not None and band_fill.alpha() > 0:
        p.fillRect(band_rect, band_fill)
    if icon.isNull():
        return
    side = icon_px if icon_px is not None else grid_card_fallback_icon_px(band_rect)
    side = min(side, band_rect.width(), band_rect.height())
    cx, cy = band_rect.center().x(), band_rect.center().y()
    icon_rect = QRect(cx - side // 2, cy - side // 2, side, side)
    icon.paint(p, icon_rect, Qt.AlignmentFlag.AlignCenter)


def paint_grid_card_labels(
    p: QPainter,
    rect: QRect,
    *,
    title: str,
    meta: str,
    title_color: QColor,
    meta_color: QColor,
    icon_band_h: int = 44,
    title_px: int = 12,
    meta_px: int = 10,
    max_title_lines: int = 2,
    band_from_card_top: bool = False,
    band_gap_after: int = 0,
    label_pad_h: int = 8,
    label_pad_bottom: int = 8,
) -> None:
    """Draw wrapped, elided title + optional meta in the lower band of a grid card."""
    meta_h = 18 if meta else 0
    gap = 4 if meta else 0
    if band_from_card_top:
        text_left = rect.left() + label_pad_h
        text_width = max(1, rect.width() - label_pad_h * 2)
        text_top = rect.top() + icon_band_h + max(0, band_gap_after)
        text_bottom = rect.bottom() - label_pad_bottom
        text_h = max(18, text_bottom - text_top - meta_h - gap)
    else:
        pad = 8
        inner = rect.adjusted(pad, pad, -pad, -pad)
        text_left = inner.left()
        text_width = inner.width()
        text_top = inner.top() + icon_band_h
        text_bottom = inner.bottom() - meta_h - gap
        text_h = max(18, text_bottom - text_top)
    title_rect = QRect(text_left, text_top, text_width, text_h)

    title_font = monos_font("Inter", title_px, QFont.Weight.DemiBold)
    p.setFont(title_font)
    p.setPen(title_color)
    title_fm = p.fontMetrics()
    line_h = max(1, title_fm.height())
    lines_cap = min(
        max_title_lines if max_title_lines > 0 else 2,
        max(1, text_h // line_h),
    )
    title_lines = _fit_wrapped_elided_lines(title_fm, str(title), title_rect.width(), lines_cap)
    _draw_top_aligned_lines(p, title_rect, title_lines, title_fm)

    if meta:
        meta_top = (text_top + text_h + gap) if band_from_card_top else (text_bottom + gap)
        meta_rect = QRect(text_left, meta_top, text_width, meta_h)
        meta_font = monos_font("Inter", meta_px, QFont.Weight.Normal)
        p.setFont(meta_font)
        p.setPen(meta_color)
        meta_fm = p.fontMetrics()
        elided_meta = meta_fm.elidedText(str(meta), Qt.TextElideMode.ElideRight, meta_rect.width())
        p.drawText(
            meta_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            elided_meta,
        )
