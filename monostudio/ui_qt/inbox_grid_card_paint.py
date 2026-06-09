"""Shared grid card label painting for Inbox/Outbox browsers."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QColor

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
) -> None:
    """Draw wrapped, elided title + optional meta in the lower band of a grid card."""
    pad = 8
    inner = rect.adjusted(pad, pad, -pad, -pad)
    meta_h = 18 if meta else 0
    gap = 4 if meta else 0
    text_top = inner.top() + icon_band_h
    text_bottom = inner.bottom() - meta_h - gap
    text_h = max(18, text_bottom - text_top)
    title_rect = QRect(inner.left(), text_top, inner.width(), text_h)

    title_font = monos_font("Inter", title_px, QFont.Weight.DemiBold)
    p.setFont(title_font)
    p.setPen(title_color)
    title_fm = p.fontMetrics()
    line_h = title_fm.height()
    lines_cap = max_title_lines if max_title_lines > 0 else max(1, text_h // max(1, line_h))
    title_lines = _fit_wrapped_elided_lines(title_fm, str(title), title_rect.width(), lines_cap)
    _draw_centered_lines(p, title_rect, title_lines, title_fm)

    if meta:
        meta_rect = QRect(inner.left(), text_bottom + gap, inner.width(), meta_h)
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
