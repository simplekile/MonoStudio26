from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.main_window import MainWindow
from monostudio.ui_qt.style import apply_dark_theme


def main() -> int:
    # Qt6 (PySide6) enables high-DPI scaling/pixmaps by default.
    # These application attributes are deprecated and emit warnings in Qt6.

    app = QApplication(sys.argv)
    # Global scroll speed tuning (Qt uses "lines per wheel step" for mouse wheels).
    # Default is often 3 on Windows; lower = slower / more precise.
    QApplication.setWheelScrollLines(1)
    apply_dark_theme(app)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

