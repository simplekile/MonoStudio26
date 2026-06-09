"""Shared list-row chrome + Windows Explorer-style row layout for Inbox/Outbox."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from monostudio.ui_qt.style import monos_font

EXPLORER_LIST_ROW_H = 56
_EXPLORER_ICON_SIZE = 40
_EXPLORER_ICON_GAP = 12
_EXPLORER_RIGHT_COL_W = 240
_EXPLORER_TEXT_GAP = 16
_EXPLORER_LINE_H = 18

_ROW_BG = QColor("#191b1e")
_ROW_HOVER = QColor("#1d1f23")
_ROW_SELECTED = QColor(59, 130, 246, 26)
_ROW_LINE = QColor("#1e1e20")

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"})


def format_file_size(num_bytes: int) -> str:
    if num_bytes < 0:
        num_bytes = 0
    if num_bytes < 1024:
        return f"{num_bytes} B"
    value = float(num_bytes)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.2f} {unit}"
    return f"{value:.2f} PB"


def format_modified_time(timestamp: float) -> str:
    try:
        dt = datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return ""
    hour = dt.strftime("%I").lstrip("0") or "12"
    return dt.strftime(f"%Y-%m-%d {hour}:%M %p")


def explorer_type_label(path: Path) -> str:
    if path.is_dir():
        return "File folder"
    ext = (path.suffix or "").lstrip(".").upper()
    if ext:
        return f"{ext} File"
    return "File"


def explorer_path_stats(path: Path) -> tuple[str, str]:
    """Return (date_modified_label, size_label) for Explorer right column."""
    try:
        st = path.stat()
        date_label = format_modified_time(st.st_mtime)
    except OSError:
        date_label = ""
    if path.is_dir():
        return date_label, ""
    try:
        size_label = format_file_size(int(st.st_size))
    except (OSError, ValueError):
        size_label = ""
    return date_label, size_label


def load_explorer_thumbnail(path: Path, *, size: int = _EXPLORER_ICON_SIZE) -> QPixmap | None:
    if not path.is_file() or path.suffix.lower() not in _IMAGE_EXTS:
        return None
    try:
        pix = QPixmap(str(path))
    except Exception:
        return None
    if pix.isNull():
        return None
    return pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def paint_inbox_list_row_chrome(
    painter: QPainter,
    option: QStyleOptionViewItem,
    *,
    viewport_width: int,
) -> QRect:
    """Paint full-width row background + bottom divider. Returns inner content rect."""
    row_rect = option.rect
    full_w = max(viewport_width, row_rect.width())
    full_rect = QRect(0, row_rect.y(), full_w, row_rect.height())
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

    if selected:
        painter.fillRect(full_rect, _ROW_SELECTED)
    elif hover:
        painter.fillRect(full_rect, _ROW_HOVER)
    else:
        painter.fillRect(full_rect, _ROW_BG)

    painter.setPen(_ROW_LINE)
    painter.drawLine(full_rect.left(), full_rect.bottom(), full_rect.right(), full_rect.bottom())

    return row_rect.adjusted(12, 0, -12, 0)


def paint_explorer_list_row(
    painter: QPainter,
    option: QStyleOptionViewItem,
    *,
    viewport_width: int,
    name: str,
    type_label: str,
    date_label: str,
    size_label: str,
    icon: QIcon | None = None,
    thumbnail: QPixmap | None = None,
) -> None:
    """Paint Explorer-style details row: icon | name+type | date+size."""
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
    title_color = QColor("#60a5fa" if selected else ("#fafafa" if hover else "#e4e4e7"))
    meta_color = QColor("#93c5fd" if selected else "#71717a")

    rect = paint_inbox_list_row_chrome(painter, option, viewport_width=viewport_width)
    if rect.height() <= 0 or rect.width() <= 0:
        return

    icon_y = rect.top() + max(0, (rect.height() - _EXPLORER_ICON_SIZE) // 2)
    icon_rect = QRect(rect.left(), icon_y, _EXPLORER_ICON_SIZE, _EXPLORER_ICON_SIZE)
    if thumbnail is not None and not thumbnail.isNull():
        px = thumbnail
        ix = icon_rect.x() + (icon_rect.width() - px.width()) // 2
        iy = icon_rect.y() + (icon_rect.height() - px.height()) // 2
        painter.drawPixmap(ix, iy, px)
    elif icon is not None and not icon.isNull():
        icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)

    right_w = min(_EXPLORER_RIGHT_COL_W, max(120, rect.width() // 3))
    right_x = rect.right() - right_w
    left_x = rect.left() + _EXPLORER_ICON_SIZE + _EXPLORER_ICON_GAP
    left_w = max(40, right_x - left_x - _EXPLORER_TEXT_GAP)

    title_font = monos_font("Inter", 13, QFont.Weight.DemiBold)
    meta_font = monos_font("Inter", 11, QFont.Weight.Normal)
    title_fm = QFontMetrics(title_font)
    meta_fm = QFontMetrics(meta_font)

    title_rect = QRect(left_x, rect.top() + 8, left_w, _EXPLORER_LINE_H)
    type_rect = QRect(left_x, title_rect.bottom() + 2, left_w, _EXPLORER_LINE_H)
    painter.setPen(title_color)
    painter.setFont(title_font)
    painter.drawText(
        title_rect,
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        title_fm.elidedText(str(name), Qt.TextElideMode.ElideMiddle, left_w),
    )
    painter.setPen(meta_color)
    painter.setFont(meta_font)
    painter.drawText(
        type_rect,
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        f"Type: {type_label}",
    )

    if not date_label and not size_label:
        return

    painter.setFont(meta_font)
    if size_label:
        date_rect = QRect(right_x, rect.top() + 8, right_w, _EXPLORER_LINE_H)
        size_rect = QRect(right_x, date_rect.bottom() + 2, right_w, _EXPLORER_LINE_H)
        if date_label:
            painter.setPen(meta_color)
            painter.drawText(
                date_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                meta_fm.elidedText(f"Date modified: {date_label}", Qt.TextElideMode.ElideLeft, right_w),
            )
        painter.drawText(
            size_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            meta_fm.elidedText(f"Size: {size_label}", Qt.TextElideMode.ElideLeft, right_w),
        )
    else:
        date_rect = QRect(right_x, rect.top(), right_w, rect.height())
        painter.setPen(meta_color)
        painter.drawText(
            date_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            meta_fm.elidedText(f"Date modified: {date_label}", Qt.TextElideMode.ElideLeft, right_w),
        )


def explorer_list_row_size_hint(option: QStyleOptionViewItem) -> QSize:
    return QSize(max(option.rect.width(), 320), EXPLORER_LIST_ROW_H)
