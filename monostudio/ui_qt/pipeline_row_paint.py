# Shared row/cell paint helpers for Grid and Pipeline List views.

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
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


def list_dcc_badge_info(
    item,
    active_department: str | None,
    *,
    dept_registry=None,
) -> list[tuple[QIcon | None, str, str]]:
    """Return [(icon or None, dcc_id, status), ...] for list DCC column. status: exists|creating."""
    from monostudio.core.models import Asset, Shot
    from monostudio.core.dcc_registry import get_default_dcc_registry
    from monostudio.ui_qt.brand_icons import brand_icon
    from monostudio.ui_qt.main_view import _dcc_ids_for_item

    out: list[tuple[QIcon | None, str, str]] = []
    ref = getattr(item, "ref", None)
    if not isinstance(ref, (Asset, Shot)):
        return out
    try:
        reg = get_default_dcc_registry()
    except Exception:
        return out
    ids_with_status = _dcc_ids_for_item(item, active_department, dept_registry=dept_registry)
    for dcc_id, status in ids_with_status:
        if status == "creating":
            out.append((None, dcc_id, "creating"))
            continue
        try:
            info = reg.get_dcc_info(dcc_id) if dcc_id else None
        except Exception:
            info = None
        slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
        color = info.get("brand_color_hex") if isinstance(info, dict) else None
        if isinstance(slug, str) and slug.strip():
            ic = brand_icon(slug.strip(), size=14, color_hex=(color if isinstance(color, str) else None))
        else:
            ic = lucide_icon("layers", size=14, color_hex=MONOS_COLORS["text_label"])
        out.append((ic, dcc_id, "exists"))
    return out


_LIST_ROW_SELECTION_OVERLAY = QColor(59, 130, 246, 42)


def paint_list_row_selection_overlay(painter: QPainter, rect: QRect) -> None:
    """Blue wash on top of publish/review row tint — distinct from mode background."""
    painter.fillRect(rect, _LIST_ROW_SELECTION_OVERLAY)


def list_row_dim_opacity(
    item,
    *,
    show_publish: bool,
    active_department: str | None,
    hover: bool,
) -> float:
    """Content/row opacity for dimmed pipeline items (parity with grid cards)."""
    ref = getattr(item, "ref", None)
    from monostudio.core.models import Asset, Shot

    if not isinstance(ref, (Asset, Shot)):
        return 1.0
    from monostudio.ui_qt.main_view import (
        _item_has_publish_for_department,
        _item_has_work_for_department,
    )

    if show_publish and not _item_has_publish_for_department(ref, active_department):
        return 0.45 if hover else 0.1
    if not show_publish and not _item_has_work_for_department(ref, active_department):
        return 0.8 if hover else 0.4
    return 1.0


def notes_badge_tooltip_text(open_n: int, visual_mode: str) -> str:
    if visual_mode == "open" or open_n > 0:
        return f"Notes ({open_n} open)"
    if visual_mode == "all_done":
        return "Notes (all completed)"
    return "Notes"


def _painter_device_pixel_ratio(painter: QPainter) -> float:
    dev = painter.device()
    if dev is not None:
        return max(1.0, float(dev.devicePixelRatioF()))
    return 1.0


def list_thumb_cover_paint(
    painter: QPainter, cell_rect: QRect, icon: QIcon, *, fast: bool = False
) -> bool:
    """Draw list thumb with object-fit: cover — HiDPI-sharp, center-crop."""
    if icon.isNull() or cell_rect.width() <= 0 or cell_rect.height() <= 0:
        return False
    cell_w = cell_rect.width()
    cell_h = cell_rect.height()
    dpr = _painter_device_pixel_ratio(painter)
    tw = max(1, int(round(cell_w * dpr)))
    th = max(1, int(round(cell_h * dpr)))

    cache = getattr(list_thumb_cover_paint, "_pix_cache", None)
    if cache is None:
        cache = {}
        setattr(list_thumb_cover_paint, "_pix_cache", cache)
    key = (int(icon.cacheKey()), tw, th)
    pix = cache.get(key)
    if pix is None:
        from monostudio.ui_qt.thumbnails import explorer_list_icon_decode_px

        decode_side = explorer_list_icon_decode_px(dpr=dpr, icon_logical=max(cell_w, cell_h))
        src = icon.pixmap(decode_side, decode_side)
        if src.isNull() or src.width() <= 0 or src.height() <= 0:
            return False
        mode = (
            Qt.TransformationMode.FastTransformation
            if fast
            else Qt.TransformationMode.SmoothTransformation
        )
        scaled = src.scaled(
            QSize(tw, th),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            mode,
        )
        if scaled.isNull():
            return False
        sx = max(0, (scaled.width() - tw) // 2)
        sy = max(0, (scaled.height() - th) // 2)
        scaled.setDevicePixelRatio(dpr)
        pix = scaled
        cache[key] = (pix, sx, sy, tw, th)
        if len(cache) > 512:
            cache.clear()
    else:
        pix, sx, sy, tw, th = pix

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not fast)
    painter.setClipRect(cell_rect, Qt.ClipOperation.IntersectClip)
    painter.drawPixmap(cell_rect, pix, QRect(sx, sy, tw, th))
    painter.restore()
    return True


LIST_ASSIGNEE_AVATAR_PX = 24
LIST_ASSIGNEE_STACK_STEP_PX = 16


def list_assignee_avatar_stack_width(count: int, *, max_show: int = 3) -> int:
    """Horizontal width for stacked assignee avatars in a list cell."""
    if count <= 0:
        return LIST_ASSIGNEE_AVATAR_PX
    shown = min(count, max_show)
    extra = 1 if count > max_show else 0
    n = shown + extra
    if n <= 1:
        return LIST_ASSIGNEE_AVATAR_PX
    return LIST_ASSIGNEE_AVATAR_PX + (n - 1) * LIST_ASSIGNEE_STACK_STEP_PX


def paint_list_assignee_avatars(
    painter: QPainter,
    cell_rect: QRect,
    users: list,
    workspace_root,
    *,
    dpr: float = 1.0,
    pixmap_cache: dict | None = None,
) -> None:
    """Paint stacked circular assignee avatars (up to 3 + overflow badge)."""
    from monostudio.core.user_identity import avatar_path
    from monostudio.ui_qt.user_avatar import avatar_pixmap_for

    size = LIST_ASSIGNEE_AVATAR_PX
    cache = pixmap_cache if pixmap_cache is not None else {}

    if not users:
        icon = lucide_icon("user", size=14, color_hex=MONOS_COLORS["text_meta"])
        chip = QRect(
            cell_rect.left() + max(0, (cell_rect.width() - size) // 2),
            cell_rect.top() + max(0, (cell_rect.height() - size) // 2),
            size,
            size,
        )
        icon.paint(painter, chip, Qt.AlignmentFlag.AlignCenter)
        return

    max_show = 3
    shown = users[:max_show]
    extra = len(users) - max_show
    total_w = list_assignee_avatar_stack_width(len(users))
    x0 = cell_rect.left() + max(0, (cell_rect.width() - total_w) // 2)
    y0 = cell_rect.top() + max(0, (cell_rect.height() - size) // 2)

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    for i, user in enumerate(shown):
        x = x0 + i * LIST_ASSIGNEE_STACK_STEP_PX
        rect = QRect(x, y0, size, size)
        key = (user.id, dpr)
        pm = cache.get(key)
        if pm is None:
            pm = avatar_pixmap_for(
                avatar_path(workspace_root, user),
                user.initials,
                user.color_hex,
                size,
                dpr=dpr,
            )
            cache[key] = pm
            if len(cache) > 256:
                cache.clear()
        painter.drawPixmap(rect, pm)

    if extra > 0:
        x = x0 + max_show * LIST_ASSIGNEE_STACK_STEP_PX
        badge = QRect(x, y0, size, size)
        painter.setPen(QPen(QColor("#3f3f46")))
        painter.setBrush(QColor(63, 63, 70, 140))
        painter.drawEllipse(badge)
        painter.setPen(QColor(MONOS_COLORS["text_meta"]))
        painter.setFont(monos_font("Inter", 9, QFont.Weight.DemiBold))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, f"+{extra}")


def list_dcc_badge_rects(
    cell_rect: QRect,
    dcc_list: list[tuple[str, str]],
) -> list[tuple[QRect, str]]:
    """DCC badge hit/layout rects inside a list row cell (horizontal chips)."""
    if not dcc_list:
        return []
    size = 14
    pad = 4
    gap = 3
    max_show = 6
    chip_h = size + pad * 2
    chip_w = chip_h
    creating_w = 44
    entries = dcc_list[:max_show]
    widths = [creating_w if st == "creating" else chip_w for (_, st) in entries]
    base_x = cell_rect.left() + 4
    base_y = cell_rect.top() + max(0, (cell_rect.height() - chip_h) // 2)
    result: list[tuple[QRect, str]] = []
    x_cursor = base_x
    for i, (dcc_id, _st) in enumerate(entries):
        w = widths[i]
        result.append((QRect(x_cursor, base_y, w, chip_h), dcc_id))
        x_cursor += w + gap
    return result
