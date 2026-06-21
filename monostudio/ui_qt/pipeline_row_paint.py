# Shared row/cell paint helpers for Grid and Pipeline List views.

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyleOptionViewItem

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

THUMB_HEALTH_ICON_PX = 14
THUMB_HEALTH_CHIP_PAD_PX = 4
LIST_SPECIAL_FOLDER_ICON_PX = 16
LIST_SPECIAL_FOLDER_CHIP_PAD_PX = 6


def list_header_column_width(header_label: str, *, min_content_px: int = 0) -> int:
    """Width for a list header column — fits uppercase header text."""
    font = monos_font("Inter", 12, QFont.Weight.ExtraBold)
    fm = QFontMetrics(font)
    text = header_label.upper()
    text_w = fm.horizontalAdvance(text) + max(0, len(text) - 1)
    return max(min_content_px, text_w + 30 + 6)


def list_health_chip_rect(cell_rect: QRect) -> QRect:
    chip = THUMB_HEALTH_ICON_PX + THUMB_HEALTH_CHIP_PAD_PX * 2
    return QRect(
        cell_rect.left() + max(0, (cell_rect.width() - chip) // 2),
        cell_rect.top() + max(0, (cell_rect.height() - chip) // 2),
        chip,
        chip,
    )


def list_special_folder_chip_rect(cell_rect: QRect) -> QRect:
    chip = LIST_SPECIAL_FOLDER_ICON_PX + LIST_SPECIAL_FOLDER_CHIP_PAD_PX * 2
    return QRect(
        cell_rect.left() + max(0, (cell_rect.width() - chip) // 2),
        cell_rect.top() + max(0, (cell_rect.height() - chip) // 2),
        chip,
        chip,
    )


def list_status_pill_natural_width(line: str, fm: QFontMetrics) -> int:
    chip_pad_x = 8
    dot_r = 3
    return fm.horizontalAdvance(line) + chip_pad_x * 2 + dot_r * 2 + 6


def list_status_pill_rect_for_cell(cell_rect: QRect, line: str, fm: QFontMetrics) -> QRect:
    chip_h = max(16, fm.height() + 4)
    tw = list_status_pill_natural_width(line, fm)
    tw = min(tw, max(1, cell_rect.width() - 16))
    x = cell_rect.left() + 8
    y = cell_rect.top() + max(0, (cell_rect.height() - chip_h) // 2)
    return QRect(x, y, tw, chip_h)


def paint_status_pill_chip(
    painter: QPainter,
    pill_rect: QRect,
    line: str,
    color_hex: str,
    *,
    fm: QFontMetrics,
    font: QFont | None = None,
    hovered: bool = False,
) -> None:
    chip_pad_x = 8
    dot_r = 3
    if font is not None:
        painter.setFont(font)
    painter.setPen(Qt.PenStyle.NoPen)
    qc = QColor(color_hex)
    bg = QColor(qc)
    bg.setAlpha(72 if hovered else 42)
    painter.setBrush(bg)
    painter.drawRoundedRect(pill_rect, 8, 8)
    if hovered:
        bc = QColor(qc)
        bc.setAlpha(140)
        painter.setPen(QPen(bc, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(pill_rect, 8, 8)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(qc)
    painter.drawEllipse(
        QPoint(pill_rect.left() + chip_pad_x + dot_r, pill_rect.center().y()),
        dot_r,
        dot_r,
    )
    painter.setPen(QColor(MONOS_COLORS["text_primary"] if hovered else MONOS_COLORS["text_label"]))
    text_rect = pill_rect.adjusted(chip_pad_x + dot_r * 2 + 6, 0, -4, 0)
    elided = fm.elidedText(line, Qt.TextElideMode.ElideRight, text_rect.width())
    painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)


def paint_list_special_folder_icon(
    painter: QPainter,
    chip_rect: QRect,
    icon_name: str,
    *,
    has_files: bool,
    hovered: bool,
) -> None:
    if hovered:
        painter.fillRect(chip_rect, QColor(255, 255, 255, 18))
    color = MONOS_COLORS["text_label"] if has_files else MONOS_COLORS["text_meta"]
    pix = lucide_icon(icon_name, size=LIST_SPECIAL_FOLDER_ICON_PX, color_hex=color).pixmap(
        LIST_SPECIAL_FOLDER_ICON_PX, LIST_SPECIAL_FOLDER_ICON_PX
    )
    if pix.isNull():
        return
    x = chip_rect.x() + (chip_rect.width() - pix.width()) // 2
    y = chip_rect.y() + (chip_rect.height() - pix.height()) // 2
    painter.drawPixmap(x, y, pix)


def paint_health_icon_chip(painter, chip_rect, health, *, hovered: bool) -> None:
    icon_name = getattr(health, "icon_name", None)
    color_hex = getattr(health, "color_hex", None)
    if not icon_name or not color_hex:
        return
    chip_bg = QColor(0, 0, 0, 220 if hovered else 168)
    if hovered:
        ring = QColor(color_hex)
        ring.setAlpha(230)
        painter.setPen(QPen(ring, 2))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(chip_bg)
    painter.drawEllipse(chip_rect)
    icon_px = THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
    icon = lucide_icon(icon_name, size=icon_px, color_hex=color_hex)
    pix = icon.pixmap(icon_px, icon_px)
    if not pix.isNull():
        pad = max(2, THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
        dest = chip_rect.adjusted(pad, pad, -pad, -pad)
        painter.drawPixmap(dest, pix)


def paint_note_icon_chip(
    painter: QPainter,
    chip_rect: QRect,
    open_count: int,
    *,
    visual_mode: str = "empty",
    hovered: bool,
) -> None:
    icon_name = "message-circle"
    if open_count > 0 or visual_mode == "open":
        chip_bg = QColor(234, 179, 8, 235 if hovered else 215)
        if hovered:
            ring = QColor(255, 255, 255, 140)
            painter.setPen(QPen(ring, 2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(chip_bg)
        painter.drawEllipse(chip_rect)
        icon_px = THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
        icon = lucide_icon(icon_name, size=icon_px, color_hex="#18181b")
        pix = icon.pixmap(icon_px, icon_px)
        if not pix.isNull():
            pad = max(2, THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
            dest = chip_rect.adjusted(pad, pad, -pad, -pad)
            painter.drawPixmap(dest, pix)
        label = "9+" if open_count > 9 else str(open_count)
        bf = monos_font("Inter", 9, QFont.Weight.Bold)
        painter.setFont(bf)
        fm = QFontMetrics(bf)
        pill_w = max(15, fm.horizontalAdvance(label) + 6)
        pill_h = max(15, fm.height())
        bx = chip_rect.right() - pill_w + 5
        by = chip_rect.top() - max(2, pill_h // 2 - 2)
        badge = QRect(bx, by, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#dc2626"))
        painter.drawRoundedRect(badge, 999, 999)
        painter.setPen(QColor("#fafafa"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        return

    if visual_mode == "all_done":
        em = QColor(MONOS_COLORS.get("emerald_500", "#10b981"))
        chip_bg = QColor(em)
        chip_bg.setAlpha(230 if hovered else 200)
        if hovered:
            ring = QColor(255, 255, 255, 130)
            painter.setPen(QPen(ring, 2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(chip_bg)
        painter.drawEllipse(chip_rect)
        icon_px = THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
        icon = lucide_icon(icon_name, size=icon_px, color_hex="#fafafa")
        pix = icon.pixmap(icon_px, icon_px)
        if not pix.isNull():
            pad = max(2, THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
            dest = chip_rect.adjusted(pad, pad, -pad, -pad)
            painter.drawPixmap(dest, pix)
        return

    chip_bg = QColor(0, 0, 0, 220 if hovered else 168)
    if hovered:
        ring = QColor(MONOS_COLORS.get("text_meta", "#a1a1aa"))
        ring.setAlpha(200)
        painter.setPen(QPen(ring, 2))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(chip_bg)
    painter.drawEllipse(chip_rect)
    icon_px = THUMB_HEALTH_ICON_PX + (2 if hovered else 0)
    icon = lucide_icon(icon_name, size=icon_px, color_hex=MONOS_COLORS.get("text_meta", "#a1a1aa"))
    pix = icon.pixmap(icon_px, icon_px)
    if not pix.isNull():
        pad = max(2, THUMB_HEALTH_CHIP_PAD_PX - (1 if hovered else 0))
        dest = chip_rect.adjusted(pad, pad, -pad, -pad)
        painter.drawPixmap(dest, pix)


def notes_badge_tooltip_text(open_n: int, visual_mode: str) -> str:
    if visual_mode == "open" or open_n > 0:
        return f"Notes ({open_n} open)"
    if visual_mode == "all_done":
        return "Notes (all completed)"
    return "Notes"
