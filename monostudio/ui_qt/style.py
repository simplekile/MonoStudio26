from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    """
    Dark theme, neutral gray base, single accent color.
    No gradients, no animations.
    """
    app.setStyle("Fusion")

    palette = QPalette()
    base = QColor("#1E1F22")  # neutral dark
    surface = QColor("#2B2D30")
    raised = QColor("#313337")
    text = QColor("#E6E6E6")
    muted_text = QColor("#A9ABB0")
    accent = QColor("#3DAEE9")  # single accent

    palette.setColor(QPalette.Window, base)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, surface)
    palette.setColor(QPalette.AlternateBase, raised)
    palette.setColor(QPalette.ToolTipBase, surface)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, raised)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor("#FFFFFF"))

    palette.setColor(QPalette.Highlight, accent)
    palette.setColor(QPalette.HighlightedText, QColor("#000000"))
    palette.setColor(QPalette.PlaceholderText, muted_text)

    app.setPalette(palette)

    # Wide spacing + simple controls. Keep minimal, spec-first.
    app.setStyleSheet(
        """
        QWidget { font-size: 12px; }
        QSplitter::handle { background: #26282B; }
        QLineEdit {
            padding: 6px 8px;
            border: 1px solid #3A3D41;
            border-radius: 6px;
            background: #2B2D30;
        }
        QLineEdit:focus {
            border: 1px solid #3DAEE9;
        }
        QListWidget, QTableView, QTreeView, QListView {
            border: 1px solid #3A3D41;
            border-radius: 8px;
            background: #2B2D30;
        }
        """
    )

