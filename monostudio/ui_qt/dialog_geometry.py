"""Fit, center, and clamp dialog geometry within a bounds rect."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from monostudio.ui_qt.popup_position import DEFAULT_POPUP_MARGIN

_MIN_RESTORE_W = 400
_MIN_RESTORE_H = 300
_MEDIA_MIN_CONTENT_W = 320
_MEDIA_MIN_CONTENT_H = 180
_MEDIA_ABS_MIN_W = 640
_MEDIA_ABS_MIN_H = 480


def host_window_for_geometry(anchor: QWidget | None) -> QWidget | None:
    """Top-level window to fit against — parent app window, not the dialog itself."""
    if anchor is None:
        return None
    if anchor.isWindow():
        parent = anchor.parentWidget()
        while parent is not None:
            if parent.isWindow():
                return parent
            parent = parent.parentWidget()
    return anchor.window()


def main_window_bounds(anchor: QWidget | None) -> "QRect":
    from PySide6.QtCore import QRect

    win = host_window_for_geometry(anchor)
    if win is not None and win.isVisible():
        fg = win.frameGeometry()
        if fg.isValid() and fg.width() > 0 and fg.height() > 0:
            return fg
    screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center())) if anchor else None
    if screen is None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
    if screen is not None:
        return screen.availableGeometry()
    return QRect(0, 0, 1280, 720)


def center_dialog_in_bounds(dialog: QWidget, bounds) -> None:
    if not bounds.isValid():
        return
    dialog.adjustSize()
    w = max(dialog.width(), dialog.minimumWidth())
    h = max(dialog.height(), dialog.minimumHeight())
    x = bounds.x() + max(0, (bounds.width() - w) // 2)
    y = bounds.y() + max(0, (bounds.height() - h) // 2)
    dialog.setGeometry(x, y, w, h)


def fit_dialog_fraction(
    dialog: QWidget,
    bounds,
    *,
    width_frac: float = 0.9,
    height_frac: float = 0.9,
    margin: int = DEFAULT_POPUP_MARGIN,
) -> None:
    if not bounds.isValid():
        return
    wf = max(0.5, min(1.0, float(width_frac)))
    hf = max(0.5, min(1.0, float(height_frac)))
    avail_w = max(320, bounds.width() - margin * 2)
    avail_h = max(240, bounds.height() - margin * 2)
    w = int(avail_w * wf)
    h = int(avail_h * hf)
    w = max(w, dialog.minimumWidth())
    h = max(h, dialog.minimumHeight())
    x = bounds.x() + (bounds.width() - w) // 2
    y = bounds.y() + (bounds.height() - h) // 2
    dialog.setGeometry(x, y, w, h)


def clamp_dialog_to_bounds(dialog: QWidget, bounds, *, margin: int = DEFAULT_POPUP_MARGIN) -> None:
    if not bounds.isValid():
        return
    g = dialog.frameGeometry()
    w = max(g.width(), dialog.minimumWidth())
    h = max(g.height(), dialog.minimumHeight())
    x, y = g.x(), g.y()
    left = bounds.left() + margin
    top = bounds.top() + margin
    right = bounds.right() - margin + 1
    bottom = bounds.bottom() - margin + 1
    max_w = max(320, right - left)
    max_h = max(240, bottom - top)
    if w > max_w:
        w = max_w
    if h > max_h:
        h = max_h
    if x < left:
        x = left
    if y < top:
        y = top
    if x + w > right:
        x = max(left, right - w)
    if y + h > bottom:
        y = max(top, bottom - h)
    dialog.setGeometry(x, y, w, h)


def geometry_valid_on_screen(dialog: QWidget, bounds) -> bool:
    return _geometry_valid_on_screen(dialog, bounds)


def fit_dialog_to_media(
    dialog: QWidget,
    bounds,
    *,
    media_width: int,
    media_height: int,
    chrome_width: int,
    chrome_height: int,
    margin: int = DEFAULT_POPUP_MARGIN,
) -> None:
    """Size dialog so the viewer area matches media pixels, scaled down to fit bounds."""
    if not bounds.isValid() or media_width <= 0 or media_height <= 0:
        return
    avail_w = max(_MEDIA_MIN_CONTENT_W, bounds.width() - margin * 2)
    avail_h = max(_MEDIA_MIN_CONTENT_H, bounds.height() - margin * 2)
    max_content_w = max(_MEDIA_MIN_CONTENT_W, avail_w - max(0, chrome_width))
    max_content_h = max(_MEDIA_MIN_CONTENT_H, avail_h - max(0, chrome_height))
    scale = min(1.0, max_content_w / media_width, max_content_h / media_height)
    content_w = max(1, int(media_width * scale))
    content_h = max(1, int(media_height * scale))
    win_w = content_w + max(0, chrome_width)
    win_h = content_h + max(0, chrome_height)
    min_size, max_size = media_window_size_limits(
        bounds,
        media_width=media_width,
        media_height=media_height,
        chrome_width=chrome_width,
        chrome_height=chrome_height,
        margin=margin,
    )
    win_w = max(min_size.width(), min(win_w, max_size.width()))
    win_h = max(min_size.height(), min(win_h, max_size.height()))
    x = bounds.x() + max(0, (bounds.width() - win_w) // 2)
    y = bounds.y() + max(0, (bounds.height() - win_h) // 2)
    dialog.setGeometry(x, y, win_w, win_h)


def media_window_size_limits(
    bounds,
    *,
    media_width: int,
    media_height: int,
    chrome_width: int,
    chrome_height: int,
    margin: int = DEFAULT_POPUP_MARGIN,
    min_content_width: int = _MEDIA_MIN_CONTENT_W,
    min_content_height: int = _MEDIA_MIN_CONTENT_H,
    abs_min_width: int = _MEDIA_ABS_MIN_W,
    abs_min_height: int = _MEDIA_ABS_MIN_H,
) -> tuple[QSize, QSize]:
    """Return (minimumSize, maximumSize) for a review player sized around media pixels."""
    avail_w = max(_MEDIA_MIN_CONTENT_W, bounds.width() - margin * 2) if bounds.isValid() else 1920
    avail_h = max(_MEDIA_MIN_CONTENT_H, bounds.height() - margin * 2) if bounds.isValid() else 1080
    if media_width > 0 and media_height > 0:
        min_content_w = media_width if media_width < min_content_width else min_content_width
        min_content_h = media_height if media_height < min_content_height else min_content_height
    else:
        min_content_w = min_content_width
        min_content_h = min_content_height
    min_w = max(abs_min_width, chrome_width + min_content_w)
    min_h = max(abs_min_height, chrome_height + min_content_h)
    max_w = max(min_w, avail_w)
    max_h = max(min_h, avail_h)
    return QSize(min_w, min_h), QSize(max_w, max_h)


def _geometry_valid_on_screen(dialog: QWidget, bounds) -> bool:
    if not bounds.isValid():
        return False
    g = dialog.frameGeometry()
    if g.width() < _MIN_RESTORE_W or g.height() < _MIN_RESTORE_H:
        return False
    cx = g.center().x()
    cy = g.center().y()
    return bounds.contains(cx, cy)


def apply_dialog_geometry(
    settings: QSettings | None,
    key: str,
    dialog: QWidget,
    *,
    bounds,
    default_fraction: float = 0.9,
    min_size: QSize | None = None,
    lock_size: bool = False,
    margin: int = DEFAULT_POPUP_MARGIN,
) -> QSize | None:
    """Apply geometry. When ``lock_size`` is True, skip restore and fix dialog size."""
    if min_size is not None:
        dialog.setMinimumSize(min_size)
    if lock_size:
        fit_dialog_fraction(
            dialog,
            bounds,
            width_frac=default_fraction,
            height_frac=default_fraction,
            margin=margin,
        )
        clamp_dialog_to_bounds(dialog, bounds, margin=margin)
        locked = QSize(dialog.width(), dialog.height())
        dialog.setFixedSize(locked)
        return locked
    restored = False
    if settings is not None:
        raw = settings.value(key)
        if isinstance(raw, QByteArray) and len(raw) > 0:
            dialog.restoreGeometry(bytes(raw))
            restored = True
    if restored and geometry_valid_on_screen(dialog, bounds):
        clamp_dialog_to_bounds(dialog, bounds)
        return None
    fit_dialog_fraction(dialog, bounds, width_frac=default_fraction, height_frac=default_fraction)
    clamp_dialog_to_bounds(dialog, bounds)
    return None


def save_dialog_geometry(settings: QSettings | None, key: str, dialog: QWidget) -> None:
    if settings is None:
        return
    settings.setValue(key, QByteArray(bytes(dialog.saveGeometry())))
