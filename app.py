from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import time
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QIcon, QImageReader
from PySide6.QtWidgets import QApplication, QSplashScreen

from monostudio.core.access_control import (
    read_splash_display_ms,
    read_verbose_debug_enabled,
    restore_remembered_access,
)
from monostudio.core.app_paths import (
    get_app_base_path,
    migrate_app_settings_if_needed,
    write_install_path_for_tools,
)
from monostudio.core.windows_toast import register_aumid_on_startup
from monostudio.core.crash_recovery import install_crash_logging
from monostudio.core.pipeline_types_and_presets import ensure_user_default_config_dir
from monostudio.core.single_instance import acquire_single_instance
from monostudio.core.tray_preferences import read_start_minimized_to_tray, read_startup_splash_ms
from monostudio.ui_qt.main_window import MainWindow
from monostudio.core.version import get_app_version

from monostudio.ui_qt.splash import (
    SPLASH_DISMISS_DELAY_MS,
    dismiss_splash_to_main_window,
    ensure_splash_fonts,
    make_splash_pixmap,
    splash_tail_status,
)
from monostudio.ui_qt.style import apply_dark_theme

SPLASH_LOADING_UPDATE_MS = 50


def _parse_launch_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--startup", action="store_true", help="Launched from Windows autostart")
    parser.add_argument("--minimized", action="store_true", help="Alias for startup-to-tray flow")
    known, _rest = parser.parse_known_args(argv[1:])
    return known


def _ensure_comtypes_on_windows() -> None:
    """Install comtypes if missing on Windows (needed for shell thumbnail in Inspector).
    Skipped in frozen builds (comtypes must be bundled by PyInstaller).
    """
    if sys.platform != "win32":
        return
    if getattr(sys, "frozen", False):
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
    launch_args = _parse_launch_args(sys.argv)
    launch_deep_link = None
    try:
        from monostudio.core.deep_link import extract_deep_link_from_argv

        launch_deep_link = extract_deep_link_from_argv(sys.argv)
    except Exception:
        pass
    startup_launch = bool(launch_args.startup or launch_args.minimized)

    install_crash_logging()
    # DCC / pending_create / assets-diff tracing: quiet by default (no DEBUG to console or log file).
    _dcc_log = logging.getLogger("monostudio.dcc_debug")
    _dcc_log.setLevel(logging.WARNING)
    _dcc_log.propagate = False
    try:
        _log_path = Path.cwd() / "monostudio_dcc_debug.log"
        _fh = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
        _fh.setLevel(logging.DEBUG)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        # Fs watcher diagnostics still append here when that logger emits DEBUG.
        _watcher_log = logging.getLogger("monostudio.fs_watcher")
        _watcher_log.setLevel(logging.DEBUG)
        _watcher_log.addHandler(_fh)
    except Exception:
        pass
    # Qt6 (PySide6) enables high-DPI scaling/pixmaps by default.
    # These application attributes are deprecated and emit warnings in Qt6.

    app = QApplication(sys.argv)
    try:
        from monostudio.core.url_protocol import register_monostudio_url_protocol

        register_monostudio_url_protocol()
    except Exception:
        pass
    instance_guard = acquire_single_instance(deep_link=launch_deep_link)
    if instance_guard is None:
        return 0

    from monostudio.core.tray_preferences import read_tray_enabled

    if read_tray_enabled():
        app.setQuitOnLastWindowClosed(False)
    register_aumid_on_startup()
    restore_remembered_access()

    _boot_settings = QSettings("MonoStudio26", "MonoStudio26")
    if startup_launch and read_start_minimized_to_tray(_boot_settings):
        _splash_display_ms = read_startup_splash_ms(_boot_settings)
    else:
        _splash_display_ms = read_splash_display_ms(_boot_settings)

    hide_to_tray_after_splash = startup_launch and read_start_minimized_to_tray(_boot_settings)

    # Verbose UI debug: env override or General → Access (developer) + Save Settings.
    if os.environ.get("MONOS_DEBUG_PROJECT_GUIDE_DROP") or read_verbose_debug_enabled(_boot_settings):
        _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        _sh = logging.StreamHandler(sys.stderr)
        _sh.setLevel(logging.DEBUG)
        _sh.setFormatter(_fmt)
        for _name in (
            "monostudio.ui_qt.reference_page_widget",
            "monostudio.ui_qt.inbox_split_view",
            "monostudio.ui_qt.main_window",
            "monostudio.dcc_debug",
            "monostudio.ui_qt.app_state",
        ):
            _log = logging.getLogger(_name)
            _log.setLevel(logging.DEBUG)
            _log.addHandler(_sh)

    # Resolve version once (git commit count)
    _version = get_app_version()

    # Splash first — show immediately (before theme/icon loading)
    ensure_splash_fonts()
    splash_start = time.monotonic()
    _icon = QIcon()
    _splash_status = ""
    splash = QSplashScreen(
        make_splash_pixmap(_icon, 0.0, "Starting…", _version),
        Qt.WindowType.WindowStaysOnTopHint,
    )
    splash.show()
    app.processEvents()

    def _splash_step(status: str) -> None:
        nonlocal _splash_status
        _splash_status = status
        splash.setPixmap(make_splash_pixmap(_icon, 0.0, status, _version))
        app.processEvents()

    # Init steps while splash is visible
    _splash_step("Starting…")
    _splash_step("Preparing environment…")
    QImageReader.setAllocationLimit(0)
    QApplication.setWheelScrollLines(1)

    _splash_step("Loading config…")
    ensure_user_default_config_dir()

    _splash_step("Migrating settings…")
    migrate_app_settings_if_needed()
    write_install_path_for_tools()  # so MonoFXSuite etc. can discover install dir for "Under MonoStudio"

    _splash_step("Applying theme…")
    apply_dark_theme(app)

    _splash_step("Loading icons…")
    _icon_path = get_app_base_path() / "monostudio_data" / "icons" / "app.ico"
    _icon = QIcon(str(_icon_path)) if _icon_path.is_file() else QIcon()
    if not _icon.isNull():
        app.setWindowIcon(_icon)

    _splash_step("Checking dependencies…")
    _ensure_comtypes_on_windows()

    _splash_step("Building interface…")
    window = MainWindow(splash_status=_splash_step)
    window.launch_hidden_to_tray = hide_to_tray_after_splash
    if launch_deep_link:
        window.set_pending_deep_link(launch_deep_link)
    if not _icon.isNull():
        window.setWindowIcon(_icon)
    instance_guard.set_on_raise(lambda: window.present())
    instance_guard.set_on_deep_link(lambda url: window.handle_deep_link(url))
    _splash_step("Starting background services…")
    try:
        from monostudio.core.deep_link_server import start_deep_link_server

        start_deep_link_server(window.handle_deep_link)
    except Exception:
        pass

    _splash_init_done_at = time.monotonic()

    _splash_ready_at: float | None = None

    # Keep splash visible until at least configured minimum has passed
    def _tick_splash() -> None:
        nonlocal _splash_ready_at
        elapsed = (time.monotonic() - splash_start) * 1000
        if _splash_display_ms <= 0:
            progress = 1.0
        else:
            progress = min(1.0, elapsed / _splash_display_ms)
        if progress >= 1.0:
            status = "Ready"
        elif _splash_init_done_at is not None:
            tail_elapsed_ms = (time.monotonic() - _splash_init_done_at) * 1000
            status = splash_tail_status(tail_elapsed_ms)
        else:
            status = _splash_status
        splash.setPixmap(make_splash_pixmap(_icon, progress, status, _version))
        if progress >= 1.0:
            if _splash_ready_at is None:
                _splash_ready_at = time.monotonic()
            if (time.monotonic() - _splash_ready_at) * 1000 < SPLASH_DISMISS_DELAY_MS:
                return
            _splash_timer.stop()
            dismiss_splash_to_main_window(splash, window, show_main=not hide_to_tray_after_splash)
            window.complete_startup()

    _splash_timer = QTimer(splash)
    _splash_timer.timeout.connect(_tick_splash)
    _splash_timer.start(SPLASH_LOADING_UPDATE_MS)
    _tick_splash()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
