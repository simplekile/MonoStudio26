"""Middle-mouse drag local files/folders out to Explorer (URI list + card preview)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QMimeData, QUrl
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from monostudio.ui_qt.inbox_list_row_paint import load_explorer_thumbnail
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import FILE_TYPE_ICON_COLORS

_MIDDLE_DRAG_THRESHOLD = 8
_DRAG_CARD_W = 136
_DRAG_INNER_PAD = 6
_DRAG_STACK_OFFSET = 6
_DRAG_CARD_RADIUS = 8
_DRAG_THUMB_RADIUS = 4
_DRAG_HOTSPOT = QPoint(16, 16)


def paths_to_file_urls(paths: list[Path]) -> list[QUrl]:
    urls: list[QUrl] = []
    seen: set[str] = set()
    for raw in paths:
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        if not p.exists():
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        urls.append(QUrl.fromLocalFile(key))
    return urls


def _icon_for_path(path: Path, *, size: int = 28) -> QIcon:
    colors = FILE_TYPE_ICON_COLORS
    icon_px = max(16, size)
    if path.is_dir():
        return lucide_icon("folder", size=icon_px, color_hex=colors["folder"])
    ext = (path.suffix or "").lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
        return lucide_icon("file-image", size=icon_px, color_hex=colors["image"])
    if ext in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        return lucide_icon("file-video", size=icon_px, color_hex=colors["video"])
    if ext in {".zip", ".7z", ".rar"}:
        return lucide_icon("file-archive", size=icon_px, color_hex=colors["archive"])
    if ext in {".pdf", ".txt", ".md", ".doc", ".docx"}:
        return lucide_icon("file-text", size=icon_px, color_hex=colors["document"])
    return lucide_icon("file", size=icon_px, color_hex=colors["file"])


def _draw_thumb_fallback_icon(
    painter: QPainter,
    thumb: QRect,
    path: Path,
    *,
    dpr: float,
) -> None:
    """Lucide icon centered in thumb when no preview image is available."""
    icon_logical = min(44, max(36, min(thumb.width(), thumb.height()) - 18))
    icon = _icon_for_path(path, size=icon_logical)
    px = max(1, int(icon_logical * dpr))
    icon_px = icon.pixmap(px, px)
    if icon_px.isNull():
        return
    icon_px.setDevicePixelRatio(dpr)
    ix = thumb.x() + (thumb.width() - icon_logical) // 2
    iy = thumb.y() + (thumb.height() - icon_logical) // 2
    painter.drawPixmap(ix, iy, icon_px)


def _render_drag_card(path: Path, *, logical_size: QSize, dpr: float) -> QPixmap | None:
    w = max(1, int(logical_size.width() * dpr))
    h = max(1, int(logical_size.height() * dpr))
    pm = QPixmap(w, h)
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        card = QRect(0, 0, logical_size.width(), logical_size.height())
        painter.setPen(QPen(QColor("#3f3f46"), 1))
        painter.setBrush(QColor("#18181b"))
        painter.drawRoundedRect(card.adjusted(0, 0, -1, -1), _DRAG_CARD_RADIUS, _DRAG_CARD_RADIUS)

        inner = card.adjusted(_DRAG_INNER_PAD, _DRAG_INNER_PAD, -_DRAG_INNER_PAD, -_DRAG_INNER_PAD)
        thumb_h = max(1, int(inner.width() * 9 / 16))
        thumb = QRect(inner.left(), inner.top(), inner.width(), min(thumb_h, inner.height()))

        thumb_pix = load_explorer_thumbnail(path, size=max(thumb.width(), thumb.height()) * 2)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(thumb), _DRAG_THUMB_RADIUS, _DRAG_THUMB_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        if thumb_pix is not None and not thumb_pix.isNull():
            scaled = thumb_pix.scaled(
                thumb.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            sx = max(0, (scaled.width() - thumb.width()) // 2)
            sy = max(0, (scaled.height() - thumb.height()) // 2)
            painter.drawPixmap(thumb, scaled.copy(QRect(QPoint(sx, sy), thumb.size())))
        else:
            painter.fillRect(thumb, QColor("#27272a"))
            _draw_thumb_fallback_icon(painter, thumb, path, dpr=dpr)
        painter.restore()

        name = (path.name or str(path)).strip() or "—"
        text_rect = QRect(
            inner.left(),
            thumb.bottom() + 6,
            inner.width(),
            max(1, inner.bottom() - (thumb.bottom() + 6)),
        )
        font = QFont("Inter")
        font.setPointSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#e4e4e7"))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextSingleLine,
            QFontMetrics(font).elidedText(name, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
    finally:
        painter.end()
    return pm if not pm.isNull() else None


def build_explorer_drag_pixmap(
    paths: list[Path],
    source: QWidget,
) -> tuple[QPixmap | None, QPoint]:
    """Card-style drag pixmap (stack + count badge), aligned with Assets middle-drag."""
    if not paths:
        return None, _DRAG_HOTSPOT
    dpr = float(getattr(source, "devicePixelRatioF", lambda: 1.0)())
    if dpr <= 0:
        dpr = 1.0

    name_font = QFont("Inter")
    name_font.setPointSize(10)
    name_font.setWeight(QFont.Weight.DemiBold)
    name_h = max(12, QFontMetrics(name_font).height())
    inner_w = max(1, _DRAG_CARD_W - _DRAG_INNER_PAD * 2)
    thumb_h = max(1, int(inner_w * 9 / 16))
    logical_h = _DRAG_INNER_PAD + thumb_h + 6 + name_h + _DRAG_INNER_PAD
    target_size = QSize(_DRAG_CARD_W, logical_h)

    cards: list[QPixmap] = []
    for path in paths[:3]:
        card = _render_drag_card(path, logical_size=target_size, dpr=dpr)
        if card is not None:
            cards.append(card)
    if not cards:
        return None, _DRAG_HOTSPOT

    layers = len(cards)
    out_w = int(cards[0].width() / dpr) + _DRAG_STACK_OFFSET * (layers - 1)
    out_h = int(cards[0].height() / dpr) + _DRAG_STACK_OFFSET * (layers - 1)
    out = QPixmap(max(1, int(out_w * dpr)), max(1, int(out_h * dpr)))
    out.setDevicePixelRatio(dpr)
    out.fill(QColor(0, 0, 0, 0))

    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i in range(layers - 1, -1, -1):
            painter.setOpacity(0.78 if i > 0 else 1.0)
            painter.drawPixmap(_DRAG_STACK_OFFSET * i, _DRAG_STACK_OFFSET * i, cards[i])
        painter.setOpacity(1.0)

        total = len(paths)
        if total > 1:
            badge = str(total)
            font = QFont("Inter")
            font.setPointSize(9)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            fm = QFontMetrics(font)
            pad_x, pad_y = 6, 3
            bw = fm.horizontalAdvance(badge) + pad_x * 2
            bh = fm.height() + pad_y * 2
            bx = int(out.width() / max(dpr, 1.0)) - bw - 4
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(37, 99, 235, 220)))
            painter.drawRoundedRect(QRect(bx, 4, bw, bh), 7, 7)
            painter.setPen(QColor("#fafafa"))
            painter.drawText(QRect(bx, 4, bw, bh), Qt.AlignmentFlag.AlignCenter, badge)
    finally:
        painter.end()
    return out, _DRAG_HOTSPOT


def start_file_url_drag(source: QWidget, paths: list[Path]) -> bool:
    urls = paths_to_file_urls(paths)
    if not urls:
        return False
    mime = QMimeData()
    mime.setUrls(urls)
    drag = QDrag(source)
    drag.setMimeData(mime)
    pixmap, hot_spot = build_explorer_drag_pixmap(paths, source)
    if pixmap is not None and not pixmap.isNull():
        drag.setPixmap(pixmap)
        drag.setHotSpot(hot_spot)
    drag.exec(
        Qt.DropAction.MoveAction | Qt.DropAction.CopyAction,
        Qt.DropAction.MoveAction,
    )
    return True


class MiddleMouseDragTracker:
    """Track middle-button press + movement threshold before starting QDrag."""

    def __init__(self) -> None:
        self._start: QPoint | None = None

    def clear(self) -> None:
        self._start = None

    def on_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._start = event.position().toPoint()

    def on_mouse_release(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._start = None

    def try_start_drag(
        self,
        event: QMouseEvent,
        *,
        source: QWidget,
        resolve_paths: Callable[[QPoint | None], list[Path]],
    ) -> bool:
        if self._start is None or not (event.buttons() & Qt.MouseButton.MiddleButton):
            return False
        pos = event.position().toPoint()
        if (pos - self._start).manhattanLength() <= _MIDDLE_DRAG_THRESHOLD:
            return False
        anchor = self._start
        self._start = None
        return start_file_url_drag(source, resolve_paths(anchor))


def collect_tree_drag_paths(
    *,
    selected_indexes,
    path_for_index: Callable[[object], Path | None],
    anchor_pos: QPoint | None,
    index_at: Callable[[QPoint], object],
) -> list[Path]:
    """Selected tree rows (column 0), or single item under *anchor_pos*."""
    seen: set[str] = set()
    paths: list[Path] = []
    for idx in selected_indexes:
        if idx.column() != 0:
            continue
        p = path_for_index(idx)
        if p is None:
            continue
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen and p.exists():
            seen.add(key)
            paths.append(p)
    if not paths and anchor_pos is not None:
        idx = index_at(anchor_pos)
        if idx.isValid():
            p = path_for_index(idx)
            if p is not None and p.exists():
                paths = [p]
    return paths
