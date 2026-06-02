"""Circular avatar (image or initials) shared by identity UI and the top bar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from monostudio.core.user_identity import avatars_dir
from monostudio.ui_qt.style import monos_font


def effective_device_pixel_ratio(widget: QWidget | None = None) -> float:
    """Best-effort DPR for crisp pixmap generation (2.0 fallback)."""
    if widget is not None:
        try:
            wh = widget.windowHandle()
            if wh is not None:
                return max(1.0, float(wh.devicePixelRatio()))
        except RuntimeError:
            pass
        try:
            scr = widget.screen()
            if scr is not None:
                return max(1.0, float(scr.devicePixelRatio()))
        except RuntimeError:
            pass
    scr = QGuiApplication.primaryScreen()
    if scr is not None:
        return max(1.0, float(scr.devicePixelRatio()))
    return 2.0


def _readable_text_color(bg_hex: str) -> QColor:
    c = QColor(bg_hex)
    if not c.isValid():
        return QColor("#fafafa")
    # Relative luminance — pick dark text on bright fills.
    lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
    return QColor("#0b0b0d") if lum > 0.62 else QColor("#ffffff")


def avatar_pixmap(
    initials: str,
    color_hex: str,
    size: int = 28,
    *,
    dpr: float | None = None,
) -> QPixmap:
    """Filled circle with centered initials, DPI-aware."""
    ratio = dpr if dpr is not None else 2.0
    side_dev = max(1, int(round(size * ratio)))
    pix = QPixmap(side_dev, side_dev)
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0.5, 0.5, size - 1.0, size - 1.0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color_hex if QColor(color_hex).isValid() else "#3b82f6"))
    p.drawEllipse(rect)
    p.setPen(_readable_text_color(color_hex))
    font: QFont = monos_font("Inter", max(8, int(size * 0.42)), QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), (initials or "?")[:2])
    p.end()
    return pix


def avatar_pixmap_for(
    image_path: Path | None,
    initials: str,
    color_hex: str,
    size: int = 28,
    *,
    dpr: float | None = None,
) -> QPixmap:
    """Circular avatar from an image file if available, else initials."""
    ratio = dpr if dpr is not None else 2.0
    if image_path is not None:
        try:
            if Path(image_path).is_file():
                src = QPixmap(str(image_path))
                if not src.isNull():
                    return _circular_image(src, size, dpr=ratio)
        except OSError:
            pass
    return avatar_pixmap(initials, color_hex, size, dpr=ratio)


def _circular_image(src: QPixmap, size: int, *, dpr: float = 2.0) -> QPixmap:
    """Center-crop to a circle; render at ``size * dpr`` device pixels then set DPR."""
    side_dev = max(1, int(round(size * dpr)))
    scaled = src.scaled(
        side_dev,
        side_dev,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(side_dev, side_dev)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addEllipse(0, 0, side_dev, side_dev)
    p.setClipPath(path)
    x0 = max(0, (scaled.width() - side_dev) // 2)
    y0 = max(0, (scaled.height() - side_dev) // 2)
    p.drawPixmap(0, 0, scaled, x0, y0, side_dev, side_dev)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def save_avatar_image(workspace_root: Path, user_id: str, src_image: Path) -> str:
    """Scale + store an avatar as PNG under the shared avatars dir. Returns filename."""
    src = QPixmap(str(src_image))
    if src.isNull():
        raise ValueError("Unsupported or unreadable image.")
    out = _circular_image(src, 256)
    folder = avatars_dir(workspace_root)
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{user_id}.png"
    if not out.save(str(folder / filename), "PNG"):
        raise OSError("Could not save avatar image.")
    return filename
