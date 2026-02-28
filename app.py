from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.crash_recovery import install_crash_logging
from monostudio.core.pipeline_types_and_presets import ensure_user_default_config_dir
from monostudio.ui_qt.main_window import MainWindow
from monostudio.ui_qt.style import apply_dark_theme

APP_MAJOR_VERSION = 26

SPLASH_DISPLAY_MS = 2000
SPLASH_LOADING_UPDATE_MS = 50
SPLASH_SIZE = (460, 280)
SPLASH_BG = "#121214"
SPLASH_ICON_SIZE = 88
SPLASH_TITLE_COLOR = "#fafafa"  # Zinc-100
SPLASH_SUBTITLE_COLOR = "#71717a"  # Zinc-500
SPLASH_STATUS_COLOR = "#52525b"   # Zinc-600 (status text)
SPLASH_LOADING_COLOR = "#3f3f46"  # track
SPLASH_LOADING_FILL = "#2563eb"   # Electric Blue (active)


def _get_app_version() -> str:
    """Return version string like 'v26.21' (major + git commit count)."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(get_app_base_path()),
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"v{APP_MAJOR_VERSION}.{result.stdout.strip()}"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return f"v{APP_MAJOR_VERSION}"


def _make_splash_pixmap(
    icon: QIcon,
    loading_progress: float = 0.0,
    status_text: str = "",
    version: str = "",
) -> QPixmap:
    from PySide6.QtGui import QBrush

    w, h = SPLASH_SIZE
    _app = QApplication.instance()
    dpr = _app.primaryScreen().devicePixelRatio() if _app and _app.primaryScreen() else 1.0
    pix = QPixmap(int(w * dpr), int(h * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(QColor(SPLASH_BG))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    # Logo centered
    icon_pix = icon.pixmap(SPLASH_ICON_SIZE, SPLASH_ICON_SIZE)
    if not icon_pix.isNull():
        ix = (w - SPLASH_ICON_SIZE) // 2
        iy = 48
        painter.drawPixmap(ix, iy, SPLASH_ICON_SIZE, SPLASH_ICON_SIZE, icon_pix)

    # Title: MONOS (italic)
    title_y = 48 + SPLASH_ICON_SIZE + 20
    title_font = QFont("Inter", 16, QFont.Weight.Bold)
    title_font.setItalic(True)
    title_font.setLetterSpacing(QFont.PercentageSpacing, 98)
    painter.setFont(title_font)
    painter.setPen(QColor(SPLASH_TITLE_COLOR))
    painter.drawText(0, title_y, w, 24, Qt.AlignmentFlag.AlignCenter, "MONOS")

    # Subtitle: Mono Studio v26.xx
    sub_font = QFont("Inter", 11, QFont.Weight.Normal)
    painter.setFont(sub_font)
    painter.setPen(QColor(SPLASH_SUBTITLE_COLOR))
    subtitle = f"Mono Studio {version}" if version else "Mono Studio"
    painter.drawText(0, title_y + 26, w, 18, Qt.AlignmentFlag.AlignCenter, subtitle)

    # Status text (above loading bar)
    bar_margin = 32
    bar_y = h - 20
    if status_text:
        status_font = QFont("Inter", 10, QFont.Weight.Normal)
        painter.setFont(status_font)
        painter.setPen(QColor(SPLASH_STATUS_COLOR))
        painter.drawText(bar_margin, bar_y - 18, w - 2 * bar_margin, 16,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         status_text)

    # Loading bar
    bar_height = 3
    bar_width = w - 2 * bar_margin
    bar_x = bar_margin
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(SPLASH_LOADING_COLOR)))
    painter.drawRoundedRect(bar_x, bar_y, bar_width, bar_height, 2, 2)
    if loading_progress > 0:
        fill_width = max(0, min(bar_width, int(bar_width * loading_progress)))
        if fill_width > 0:
            painter.setBrush(QBrush(QColor(SPLASH_LOADING_FILL)))
            painter.drawRoundedRect(bar_x, bar_y, fill_width, bar_height, 2, 2)

    painter.end()
    return pix


def _ensure_comtypes_on_windows() -> None:
    """Install comtypes if missing on Windows (needed for shell thumbnail in Inspector)."""
    if sys.platform != "win32":
        return
    try:
        import comtypes  # noqa: F401
        return
    except ImportError:
        pass
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "comtypes"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def main() -> int:
    install_crash_logging()
    # DCC status / pending_create / assets diff debugging (Blender/subprocess spam stdout)
    _dcc_log = logging.getLogger("monostudio.dcc_debug")
    _dcc_log.setLevel(logging.DEBUG)
    try:
        _log_path = Path.cwd() / "monostudio_dcc_debug.log"
        _fh = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
        _fh.setLevel(logging.DEBUG)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _dcc_log.addHandler(_fh)
        # Same file for fs watcher debug (to verify watcher is receiving events)
        _watcher_log = logging.getLogger("monostudio.fs_watcher")
        _watcher_log.setLevel(logging.DEBUG)
        _watcher_log.addHandler(_fh)
    except Exception:
        pass
    # Project Guide drag-drop debug (set MONOS_DEBUG_PROJECT_GUIDE_DROP=1)
    if os.environ.get("MONOS_DEBUG_PROJECT_GUIDE_DROP"):
        _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        _sh = logging.StreamHandler(sys.stderr)
        _sh.setLevel(logging.DEBUG)
        _sh.setFormatter(_fmt)
        for _name in ("monostudio.ui_qt.reference_page_widget", "monostudio.ui_qt.inbox_split_view", "monostudio.ui_qt.main_window"):
            _log = logging.getLogger(_name)
            _log.setLevel(logging.DEBUG)
            _log.addHandler(_sh)
    # Qt6 (PySide6) enables high-DPI scaling/pixmaps by default.
    # These application attributes are deprecated and emit warnings in Qt6.

    app = QApplication(sys.argv)

    # Resolve version once (git commit count)
    _version = _get_app_version()

    # Splash first — show immediately (before theme/font/icon loading)
    splash_start = time.monotonic()
    _icon = QIcon()
    _splash_status = ""
    splash = QSplashScreen(
        _make_splash_pixmap(_icon, 0.0, "Starting…", _version),
        Qt.WindowType.WindowStaysOnTopHint,
    )
    splash.show()
    app.processEvents()

    def _splash_step(status: str, progress: float) -> None:
        nonlocal _splash_status
        _splash_status = status
        splash.setPixmap(_make_splash_pixmap(_icon, progress, status, _version))
        app.processEvents()

    # Init steps while splash is visible
    _splash_step("Loading config…", 0.10)
    QImageReader.setAllocationLimit(0)
    QApplication.setWheelScrollLines(1)
    ensure_user_default_config_dir()

    _splash_step("Applying theme…", 0.25)
    apply_dark_theme(app)

    _splash_step("Loading icons…", 0.40)
    _icon_path = get_app_base_path() / "monostudio_data" / "icons" / "app.ico"
    _icon = QIcon(str(_icon_path)) if _icon_path.is_file() else QIcon()
    if not _icon.isNull():
        app.setWindowIcon(_icon)
    _splash_step("Loading icons…", 0.45)

    _splash_step("Checking dependencies…", 0.50)
    _ensure_comtypes_on_windows()

    _splash_step("Building interface…", 0.65)
    window = MainWindow()
    if not _icon.isNull():
        window.setWindowIcon(_icon)

    _splash_step("Almost ready…", 0.90)

    # Keep splash visible until at least SPLASH_DISPLAY_MS has passed
    def _tick_splash() -> None:
        elapsed = (time.monotonic() - splash_start) * 1000
        progress = min(1.0, elapsed / SPLASH_DISPLAY_MS)
        status = "Ready" if progress >= 1.0 else _splash_status
        splash.setPixmap(_make_splash_pixmap(_icon, progress, status, _version))
        if progress >= 1.0:
            _splash_timer.stop()
            window.show()
            splash.finish(window)

    _splash_timer = QTimer(splash)
    _splash_timer.timeout.connect(_tick_splash)
    _splash_timer.start(SPLASH_LOADING_UPDATE_MS)
    _tick_splash()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

