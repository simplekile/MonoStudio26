"""Square bordered thumbnails for inline note images."""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QTextDocument

NOTE_IMG_BOX_SIZE = 30
NOTE_IMG_PAD = 2
NOTE_IMG_RADIUS = 5
_BG = QColor("#18181b")
_BORDER = QColor("#3f3f46")
_BORDER_HOVER = QColor("#71717a")
_HOVER_FILL = QColor(255, 255, 255, 14)


def note_thumb_device_pixel_ratio() -> float:
    """Render thumbs at device resolution so tiny inline images stay sharp."""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                return max(2.0, float(screen.devicePixelRatio()))
    except Exception:
        pass
    return 2.0


def _prepare_source_image(img: QImage) -> QImage:
    if img.isNull():
        return img
    if img.format() == QImage.Format.Format_ARGB32_Premultiplied:
        return img
    if img.hasAlphaChannel():
        return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    return img.convertToFormat(QImage.Format.Format_RGB32)


def _aspect_fit_rect(inner: float, img_w: int, img_h: int, ox: float, oy: float) -> QRectF:
    if img_w <= 0 or img_h <= 0:
        return QRectF(ox, oy, inner, inner)
    scale = min(inner / img_w, inner / img_h)
    w = img_w * scale
    h = img_h * scale
    return QRectF(ox + (inner - w) * 0.5, oy + (inner - h) * 0.5, w, h)


def iter_note_image_srcs(html: str) -> tuple[str, ...]:
    return tuple(m.group(1) for m in re.finditer(r'<img\b[^>]*\bsrc="([^"]+)"', html or "", re.IGNORECASE))


def resolve_note_image_path(item_root: Path, href: str) -> Path | None:
    href = (href or "").strip()
    if href.startswith("monos-img:"):
        href = href[len("monos-img:") :]
    if href.startswith("file:"):
        local = QUrl(href).toLocalFile()
        if local and Path(local).is_file():
            return Path(local)
    p = Path(href)
    if p.is_file():
        return p
    mono = Path(item_root) / ".monostudio"
    candidate = mono / href.replace("/", os.sep)
    if candidate.is_file():
        return candidate
    return None


def _resource_urls(item_root: Path, href: str, file_path: Path) -> tuple[QUrl, ...]:
    href = (href or "").strip()
    urls: list[QUrl] = []
    if href:
        urls.append(QUrl(href))
    file_url = QUrl.fromLocalFile(str(file_path.resolve()))
    if file_url not in urls:
        urls.append(file_url)
    rel = file_path
    try:
        mono = Path(item_root) / ".monostudio"
        rel = file_path.resolve().relative_to(mono.resolve())
        rel_url = QUrl(str(rel).replace("\\", "/"))
        if rel_url not in urls:
            urls.append(rel_url)
    except ValueError:
        pass
    return tuple(urls)


def square_framed_note_image(
    img: QImage,
    *,
    box_size: int = NOTE_IMG_BOX_SIZE,
    hovered: bool = False,
    device_pixel_ratio: float | None = None,
) -> QImage:
    """Return a square logical-pixel thumbnail for QTextDocument (no QImage DPR)."""
    logical = max(1, int(box_size))
    dpr = max(1.0, float(device_pixel_ratio or note_thumb_device_pixel_ratio()))
    physical = max(logical, int(round(logical * dpr)))

    if img.isNull():
        return QImage(logical, logical, QImage.Format.Format_ARGB32_Premultiplied)

    src = _prepare_source_image(img)
    canvas = QImage(physical, physical, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)

    border_px = max(1.0, dpr)
    pad_px = NOTE_IMG_PAD * dpr
    radius = min(NOTE_IMG_RADIUS * dpr, physical * 0.5)
    outer = QRectF(border_px * 0.5, border_px * 0.5, physical - border_px, physical - border_px)
    clip = QPainterPath()
    clip.addRoundedRect(outer, radius, radius)

    content_origin = border_px + pad_px
    content_size = max(1.0, physical - 2.0 * (border_px + pad_px))

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering, True)
    painter.setClipPath(clip)

    painter.fillPath(clip, _BG)
    if hovered:
        painter.fillPath(clip, _HOVER_FILL)

    dest = _aspect_fit_rect(content_size, src.width(), src.height(), content_origin, content_origin)
    src_rect = QRectF(0.0, 0.0, float(src.width()), float(src.height()))
    painter.drawImage(dest, src, src_rect)

    painter.setClipping(False)
    border = _BORDER_HOVER if hovered else _BORDER
    pen = QPen(border)
    pen.setWidthF(border_px)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(outer, radius, radius)
    painter.end()

    if physical != logical:
        canvas = canvas.scaled(
            logical,
            logical,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return canvas


def prime_framed_image_resources(
    document: QTextDocument,
    item_root: Path,
    html: str,
    *,
    hover_href: str | None = None,
    device_pixel_ratio: float | None = None,
) -> None:
    hover_key = (hover_href or "").strip()
    seen: set[str] = set()
    for src in iter_note_image_srcs(html):
        if src in seen:
            continue
        seen.add(src)
        p = resolve_note_image_path(item_root, src)
        if p is None:
            continue
        full = QImage(str(p))
        if full.isNull():
            continue
        hovered = src == hover_key
        framed = square_framed_note_image(
            full, hovered=hovered, device_pixel_ratio=device_pixel_ratio
        )
        for url in _resource_urls(item_root, src, p):
            document.addResource(QTextDocument.ResourceType.ImageResource, url, framed)


def set_note_image_resource_hover(
    document: QTextDocument,
    item_root: Path,
    href: str,
    *,
    hovered: bool,
    device_pixel_ratio: float | None = None,
) -> None:
    href = (href or "").strip()
    if not href:
        return
    p = resolve_note_image_path(item_root, href)
    if p is None:
        return
    full = QImage(str(p))
    if full.isNull():
        return
    framed = square_framed_note_image(full, hovered=hovered, device_pixel_ratio=device_pixel_ratio)
    for url in _resource_urls(item_root, href, p):
        document.addResource(QTextDocument.ResourceType.ImageResource, url, framed)
