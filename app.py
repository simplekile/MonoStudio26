from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.main_window import MainWindow
from monostudio.ui_qt.style import apply_dark_theme


def main() -> int:
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

