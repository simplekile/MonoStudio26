"""Resolve-style startup splash — hero image + left gradient panel (QPainter)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QSplashScreen, QWidget

from monostudio.core.app_paths import get_app_base_path

SPLASH_SIZE = (960, 540)
SPLASH_BG = "#121214"
MONOS_MAIN_WINDOW_BG = "#151618"
SPLASH_TITLE_COLOR = "#fafafa"
SPLASH_SUBTITLE_COLOR = "#71717a"
SPLASH_STATUS_COLOR = "#a1a1aa"
SPLASH_LEGAL_COLOR = "#52525b"
SPLASH_LOADING_COLOR = "#3f3f46"
SPLASH_LOADING_FILL = "#2563eb"

SPLASH_MARGIN = 40
SPLASH_BAR_MARGIN = 32
SPLASH_BAR_HEIGHT = 2
SPLASH_BAR_BOTTOM = 20
SPLASH_ICON_SIZE = 48
SPLASH_ICON_MARGIN = 24
SPLASH_GRADIENT_END_RATIO = 0.42
SPLASH_DISMISS_DELAY_MS = 500
SPLASH_TAIL_INTERVAL_MS = 350
SPLASH_TAIL_STATUSES = (
    "Preparing workspace…",
    "Loading UI components…",
    "Applying preferences…",
    "Connecting services…",
    "Syncing pipeline…",
    "Finalizing…",
)

_HERO_BASENAME = "splash_hero"
_FONTS_LOADED = False


def _splash_hero_path() -> str | None:
    images_dir = get_app_base_path() / "monostudio_data" / "images"
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = images_dir / f"{_HERO_BASENAME}{ext}"
        if candidate.is_file():
            return str(candidate)
    return None


def splash_tail_status(tail_elapsed_ms: float) -> str:
    if not SPLASH_TAIL_STATUSES:
        return "Almost ready…"
    idx = int(tail_elapsed_ms // SPLASH_TAIL_INTERVAL_MS) % len(SPLASH_TAIL_STATUSES)
    return SPLASH_TAIL_STATUSES[idx]


def ensure_splash_fonts() -> None:
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    base = get_app_base_path()
    for name in (
        "Inter-VariableFont_opsz,wght.ttf",
        "Inter-Italic-VariableFont_opsz,wght.ttf",
    ):
        path = base / "fonts" / "Inter" / name
        try:
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        except OSError:
            pass
    _FONTS_LOADED = True


def _format_status(text: str) -> str:
    return text.strip().upper()


def _device_pixel_ratio() -> float:
    app = QApplication.instance()
    if app and app.primaryScreen():
        return app.primaryScreen().devicePixelRatio()
    return 1.0


def _draw_hero(painter: QPainter, w: int, h: int) -> None:
    from PySide6.QtGui import QColor

    hero_path = _splash_hero_path()
    if hero_path:
        hero = QPixmap(hero_path)
        if not hero.isNull():
            scaled = hero.scaled(
                w,
                h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x_off = max(0, scaled.width() - w)
            y_off = max(0, (scaled.height() - h) // 2)
            painter.drawPixmap(0, 0, scaled, x_off, y_off, w, h)
            return

    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0.0, QColor("#18181b"))
    grad.setColorAt(1.0, QColor("#27272a"))
    painter.fillRect(0, 0, w, h, grad)


def _draw_left_gradient(painter: QPainter, w: int, h: int) -> None:
    from PySide6.QtGui import QColor

    grad_end = int(w * SPLASH_GRADIENT_END_RATIO)
    overlay = QLinearGradient(0, 0, grad_end, 0)
    overlay.setColorAt(0.0, QColor(18, 18, 20, 245))
    overlay.setColorAt(0.55, QColor(18, 18, 20, 210))
    overlay.setColorAt(1.0, QColor(18, 18, 20, 0))
    painter.fillRect(0, 0, grad_end, h, overlay)


def make_splash_pixmap(
    icon: QIcon,
    loading_progress: float = 0.0,
    status_text: str = "",
    version: str = "",
) -> QPixmap:
    from PySide6.QtGui import QColor

    w, h = SPLASH_SIZE
    dpr = _device_pixel_ratio()
    pix = QPixmap(int(w * dpr), int(h * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(QColor(SPLASH_BG))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    _draw_hero(painter, w, h)
    _draw_left_gradient(painter, w, h)

    text_x = SPLASH_MARGIN
    text_w = int(w * SPLASH_GRADIENT_END_RATIO) - SPLASH_MARGIN * 2

    title_font = QFont("Inter", 40, QFont.Weight.Bold)
    title_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98)
    painter.setFont(title_font)
    painter.setPen(QColor(SPLASH_TITLE_COLOR))
    title_y = 168
    painter.drawText(text_x, title_y, text_w, 48, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "MONOS")

    sub_font = QFont("Inter", 11, QFont.Weight.ExtraBold)
    sub_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118)
    painter.setFont(sub_font)
    painter.setPen(QColor(SPLASH_SUBTITLE_COLOR))
    sub_y = title_y + 52
    painter.drawText(text_x, sub_y, text_w, 18, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "MONO STUDIO")
    if version:
        ver_font = QFont("Inter", 11, QFont.Weight.DemiBold)
        ver_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
        painter.setFont(ver_font)
        painter.drawText(text_x, sub_y + 22, text_w, 18, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, version.upper())

    bar_y = h - SPLASH_BAR_BOTTOM - SPLASH_BAR_HEIGHT
    legal_font = QFont("Inter", 8, QFont.Weight.Medium)
    legal_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
    painter.setFont(legal_font)
    painter.setPen(QColor(SPLASH_LEGAL_COLOR))
    year = datetime.now().year
    legal_y = bar_y - 36
    painter.drawText(
        text_x,
        legal_y,
        text_w,
        14,
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f"© {year} MONO STUDIO",
    )

    if status_text:
        status_font = QFont("Inter", 10, QFont.Weight.DemiBold)
        status_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        painter.setFont(status_font)
        painter.setPen(QColor(SPLASH_STATUS_COLOR))
        painter.drawText(
            text_x,
            legal_y - 22,
            text_w,
            16,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _format_status(status_text),
        )

    bar_width = w - 2 * SPLASH_BAR_MARGIN
    bar_x = SPLASH_BAR_MARGIN
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(SPLASH_LOADING_COLOR)))
    painter.drawRoundedRect(bar_x, bar_y, bar_width, SPLASH_BAR_HEIGHT, 1, 1)
    if loading_progress > 0:
        fill_width = max(0, min(bar_width, int(bar_width * loading_progress)))
        if fill_width > 0:
            painter.setBrush(QBrush(QColor(SPLASH_LOADING_FILL)))
            painter.drawRoundedRect(bar_x, bar_y, fill_width, SPLASH_BAR_HEIGHT, 1, 1)

    icon_pix = icon.pixmap(SPLASH_ICON_SIZE, SPLASH_ICON_SIZE)
    if not icon_pix.isNull():
        ix = w - SPLASH_ICON_MARGIN - SPLASH_ICON_SIZE
        iy = SPLASH_ICON_MARGIN
        painter.drawPixmap(ix, iy, SPLASH_ICON_SIZE, SPLASH_ICON_SIZE, icon_pix)

    painter.end()
    return pix


def _paint_widget_dark(widget: QWidget, bg: QColor) -> None:
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setAutoFillBackground(True)
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.Window, bg)
    widget.setPalette(pal)


def prepare_main_window_shell(window: QWidget) -> None:
    """Dark native shell before first show — avoids white flash behind splash on Windows."""
    bg = QColor(MONOS_MAIN_WINDOW_BG)
    _paint_widget_dark(window, bg)
    central = window.centralWidget()
    if central is not None:
        _paint_widget_dark(central, bg)


def _prime_main_window_paint(window: QWidget, app: QApplication) -> None:
    window.setWindowOpacity(0.0)
    window.show()
    app.processEvents()
    window.repaint()
    central = window.centralWidget()
    if central is not None:
        central.repaint()
    app.processEvents()
    if hasattr(window, "apply_pending_window_state"):
        window.apply_pending_window_state()
    app.processEvents()
    window.repaint()
    app.processEvents()


def dismiss_splash_to_main_window(
    splash: QSplashScreen,
    window: QWidget,
    *,
    show_main: bool,
) -> None:
    app = QApplication.instance()
    if app is None:
        splash.finish(window)
        if not show_main:
            window.hide()
        return

    prepare_main_window_shell(window)
    if show_main:
        _prime_main_window_paint(window, app)
        splash.finish(window)
        window.setWindowOpacity(1.0)
    else:
        window.setWindowOpacity(0.0)
        splash.finish(window)
        window.hide()
        window.setWindowOpacity(1.0)
