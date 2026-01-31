from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


MONOS_COLORS: dict[str, str] = {
    # Base / layering
    "app_bg": "#09090b",  # Zinc-950
    "panel": "#18181b",  # Zinc-900
    "surface": "#27272a",  # Zinc-800
    "content_bg": "#121214",  # app content container behind cards/rows
    "border": "#27272a",  # Zinc-800
    # Text
    "text_primary": "#fafafa",  # Zinc-50
    "text_label": "#a1a1aa",  # Zinc-400
    "text_meta": "#71717a",  # Zinc-500
    "placeholder": "#3f3f46",  # Zinc-700
    # Accent
    "blue_600": "#2563eb",
    "blue_400": "#60a5fa",
    # Card
    "card_bg": "#18181b",
    "card_hover": "#1f1f22",
}


def _install_fonts(app: QApplication) -> None:
    """
    Typography v1:
    - UI font: Inter (bundled in repo)
    - Technical font: JetBrains Mono (system-installed; used via stylesheet on marked fields)
    """
    repo_root = Path(__file__).resolve().parents[2]
    inter_regular = repo_root / "fonts" / "Inter" / "Inter-VariableFont_opsz,wght.ttf"
    inter_italic = repo_root / "fonts" / "Inter" / "Inter-Italic-VariableFont_opsz,wght.ttf"

    for p in (inter_regular, inter_italic):
        try:
            if p.exists():
                QFontDatabase.addApplicationFont(str(p))
        except OSError:
            pass

    app.setFont(QFont("Inter", 13))


def apply_dark_theme(app: QApplication) -> None:
    """
    MONOS Deep Dark UI palette (Tailwind v4 inspired):
    - Base: Zinc-950/900/800 layering
    - Accent: Blue-600 / Blue-400
    - Semantic: Emerald/Amber/Red
    No animations. (Gradients may be used sparingly for active navigation states.)
    """
    app.setStyle("Fusion")
    _install_fonts(app)

    palette = QPalette()
    app_bg = QColor("#09090b")  # Zinc-950
    panel = QColor("#18181b")  # Zinc-900
    surface = QColor("#27272a")  # Zinc-800
    text = QColor("#fafafa")  # Zinc-50
    label = QColor("#a1a1aa")  # Zinc-400
    meta = QColor("#71717a")  # Zinc-500
    placeholder = QColor("#3f3f46")  # Zinc-700
    accent = QColor("#2563eb")  # Blue-600

    palette.setColor(QPalette.Window, app_bg)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, surface)
    palette.setColor(QPalette.AlternateBase, panel)
    palette.setColor(QPalette.ToolTipBase, panel)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, surface)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, text)

    palette.setColor(QPalette.Highlight, accent)
    palette.setColor(QPalette.HighlightedText, text)
    palette.setColor(QPalette.PlaceholderText, placeholder)

    app.setPalette(palette)

    # Wide spacing + simple controls. Keep minimal, spec-first.
    app.setStyleSheet(
        """
        QWidget { font-family: "Inter"; font-size: 13px; }
        /* Panel separators (splitter handles) */
        QSplitter::handle {
            background: rgba(39, 39, 42, 0.50);
        }
        QSplitter::handle:horizontal {
            width: 1px;
        }
        QSplitter::handle:vertical {
            height: 1px;
        }
        QLabel[mono="true"], QLineEdit[mono="true"] {
            font-family: "JetBrains Mono", "Consolas", "Courier New", monospace;
        }
        QLineEdit {
            padding: 6px 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 6px;
            background: #27272a; /* Zinc-800 */
        }
        QLineEdit:focus {
            border: 1px solid #2563eb; /* Blue-600 */
        }
        QListWidget, QTableView, QTreeView, QListView {
            solid rgba(39, 39, 42, 0.50);
            background: #27272a; /* Zinc-800 */
        }

        /* --- Asset Browser containers (custom mapping) --- */
        QListView#MainViewGrid {
            background: #121214;
            solid #27272a;

        }
        QTableView#MainViewList {
            background: #121214;
            solid #27272a;

        }
        QTableView#MainViewList::item {
            background: #18181b;
            color: #a1a1aa;
        }
        QTableView#MainViewList::item:hover {
            background: #1f1f22;
            color: #fafafa;
        }
        QTableView#MainViewList::item:selected {
            background: rgba(59, 130, 246, 0.10);
            color: #60a5fa;
        }

        /* --- MainView Asset Browser header --- */
        QWidget#MainViewHeader {
            background: rgba(24, 24, 27, 0.50); /* glass layer (no blur in Qt) */
            border-top: 1px solid rgba(39, 39, 42, 0.50);
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QLabel#MainViewContextTitle {
            color: #fafafa;
        }
        QToolButton {
            padding: 6px 10px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa; /* Zinc-400 */
        }
        QToolButton:checked {
            background: rgba(59, 130, 246, 0.10); /* active glow */
            border: 1px solid rgba(37, 99, 235, 0.50); /* Blue-600 */
            color: #60a5fa; /* Blue-400 action text */
        }
        QPushButton#MainViewPrimaryAction {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(37, 99, 235, 0.70); /* Blue-600 */
            background: rgba(37, 99, 235, 0.22); /* Blue-600 */
            color: #fafafa;
            text-align: center;
        }
        QPushButton#MainViewPrimaryAction:disabled {
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: rgba(250, 250, 250, 0.45);
        }

        /* --- MONOS Sidebar (fixed 256px) --- */
        QWidget#SidebarContainer {
            background: #18181b;
        }
        QLabel#SidebarBrandIcon {
            background: #2563eb; /* Blue-600 */
            color: #ffffff;
            border-radius: 6px;
            font-weight: 700;
        }
        QLabel#SidebarBrandLabel {
            color: #fafafa;
        }
        QLabel#SidebarSectionHeader {
            color: #71717a; /* Zinc-500 */
        }
        QLabel#SidebarMutedText {
            color: #71717a; /* Zinc-500 */
        }

        QScrollArea#SidebarScrollArea {
            background: transparent;
        }

        QListWidget#SidebarPrimaryNav {
            border: none;
            border-radius: 0px;
            background: transparent;
        }
        QListWidget#SidebarPrimaryNav::item {
            background: transparent; /* we render custom item widgets */
            border: none;
            padding: 0px;
        }
        QListWidget#SidebarPrimaryNav::item:selected {
            background: transparent;
        }

        /* Primary Nav item widget (Alignment Matrix) */
        QWidget#SidebarNavItem {
            border-radius: 8px;
            background: transparent;
        }
        QWidget#SidebarNavItem:hover {
            background: rgba(255, 255, 255, 0.03);
        }
        QWidget#SidebarNavItem[active="true"] {
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 0,
                stop: 0 rgba(59, 130, 246, 0.10), /* active glow */
                stop: 1 rgba(59, 130, 246, 0.00)
            );
        }
        QFrame#SidebarNavIndicator {
            background: transparent;
            border-radius: 1px;
        }
        QFrame#SidebarNavIndicator[active="true"] {
            background: #2563eb; /* Blue-600, 2px wide */
        }
        QLabel#SidebarNavLabel {
            color: #a1a1aa; /* Zinc-400 */
        }
        QWidget#SidebarNavItem[active="true"] QLabel#SidebarNavLabel {
            color: #fafafa;
        }
        QLabel#SidebarNavBadge {
            padding-left: 6px;
            padding-right: 6px;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.06);
            color: #a1a1aa;
        }
        QWidget#SidebarNavItem[active="true"] QLabel#SidebarNavBadge {
            background: rgba(59, 130, 246, 0.10); /* active glow */
            color: #60a5fa; /* Blue-400 */
        }

        QTreeWidget#SidebarHierarchyTree {
            border: none;
            border-radius: 0px;
            background: transparent;
            color: #a1a1aa; /* Zinc-400 */
        }
        QTreeWidget#SidebarHierarchyTree::item {
            height: 28px;
            padding-left: 6px; /* icon-to-text tighter gap */
            border-radius: 6px;
        }
        QTreeWidget#SidebarHierarchyTree::item:selected {
            background: rgba(59, 130, 246, 0.10); /* active glow */
            color: #fafafa;
        }

        QWidget#SidebarBottom {
            border-top: 1px solid rgba(39, 39, 42, 0.50);
        }
        QPushButton#SidebarSettingsButton {
            padding: 8px 12px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
            text-align: left;
        }
        QPushButton#SidebarSettingsButton:hover {
            background: rgba(24, 24, 27, 0.55);
            border: 1px solid rgba(39, 39, 42, 0.70);
        }
        """
    )

