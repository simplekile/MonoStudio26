from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from monostudio.core.crash_recovery import install_crash_logging
from monostudio.core.pipeline_types_and_presets import ensure_user_default_config_dir
from monostudio.ui_qt.main_window import MainWindow
from monostudio.ui_qt.style import apply_dark_theme


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
    # Qt6 (PySide6) enables high-DPI scaling/pixmaps by default.
    # These application attributes are deprecated and emit warnings in Qt6.

    app = QApplication(sys.argv)
    # Global scroll speed tuning (Qt uses "lines per wheel step" for mouse wheels).
    # Default is often 3 on Windows; lower = slower / more precise.
    QApplication.setWheelScrollLines(1)
    ensure_user_default_config_dir()
    apply_dark_theme(app)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

