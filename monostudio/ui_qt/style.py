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
    "text_primary": "#cccccc",  # Zinc-50
    # Name highlight colors (cards)
    "text_primary_highlight": "#60a5fa",  # Blue-400 (hover/highlight)
    "text_primary_selected": "#fafafa",  # Zinc-50 (selected)
    "text_label": "#a1a1aa",  # Zinc-400
    "text_meta": "#71717a",  # Zinc-500
    "placeholder": "#3f3f46",  # Zinc-700
    # Accent
    "blue_600": "#2563eb",
    "blue_500": "#3b82f6",
    "blue_400": "#60a5fa",
    # Semantic
    "emerald_500": "#10b981",
    "amber_500": "#f59e0b",
    "red_500": "#ef4444",
    "waiting": "#71717a",  # Zinc-500
    # Card
    "card_bg": "#18181b",
    "card_hover": "#1f1f22",
}

# Thumb overlay tag spec (used by custom painters, not QSS).
# Keep ALL tags consistent: same font/padding/radius/alpha; only color changes.
THUMB_TAG_STYLE: dict[str, object] = {
    "font_size": 6,  # QFont point size (kept small, scan-friendly)
    "font_weight": int(QFont.Weight.ExtraBold),
    "pad_x": 5,
    "pad_y": 1,
    "radius": 2,
    "border_px": 1,
    "bg_alpha": 160,
    "border_alpha": 80,
    # Text color for contextual tags (dept/type). Status tag keeps semantic text colors.
    "ctx_text_color_key": "text_primary",
    # Context tag base colors (only alpha differs per tag)
    "dept_color_key": "blue_600",  # #2563eb
    "type_color_key": "emerald_500",  # #10b981
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

    font = QFont("Inter", 13)
    font.setWeight(QFont.Weight.Medium)  # 13px @ 500 (Inter renders cleaner in Qt)
    font.setHintingPreference(QFont.PreferFullHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)


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

        /* ===============================
           MONOS :: Scrollbar (Dark / Minimal / DPI-safe)
           - scrollbar is a tool, not a UI element
           - no arrows
           - hover = clearer
           =============================== */

        /* ScrollArea / Viewport cleanup (prevents Qt default borders) */
        QAbstractScrollArea {
            background: transparent;
            border: none;
        }
        QAbstractScrollArea::corner {
            background: transparent;
        }

        /* Vertical scrollbar */
        QScrollBar:vertical {
            background: transparent;
            width: 8px;              /* DPI-safe: 6–8px */
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.22);
            min-height: 28px;        /* easy grab at high DPI */
            border-radius: 4px;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(255, 255, 255, 0.45);
        }
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: none;
        }

        /* Horizontal scrollbar */
        QScrollBar:horizontal {
            background: transparent;
            height: 8px;
            margin: 0px;
        }
        QScrollBar::handle:horizontal {
            background: rgba(255, 255, 255, 0.22);
            min-width: 28px;
            border-radius: 4px;
        }
        QScrollBar::handle:horizontal:hover {
            background: rgba(255, 255, 255, 0.45);
        }
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {
            height: 0px;
            width: 0px;
        }
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {
            background: none;
        }

        QWidget#TopBar {
            background-color: #18181b; /* Zinc-900 */
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QToolButton#ProjectSwitch {
            padding: 6px 12px;
            border-radius: 8px;
            border: 1px solid #27272a;
            background: #1c1c1f;
            color: #e2e2e2; /* off-white */
            font-family: "Inter", "Inter UI", "Segoe UI", "San Francisco", sans-serif;
            font-size: 12px; /* px only */
            font-weight: 500;
            -qt-subpixel-positioned: true;
            text-align: left;
        }
        QToolButton#ProjectSwitch[state="active"] {
            color: #f4f4f5; /* Zinc-100 */
            font-weight: 600;
        }
        QToolButton#ProjectSwitch[state="empty"] {
            color: #a1a1aa; /* Zinc-400 */
            font-weight: 500;
        }
        QToolButton#ProjectSwitch[state="disabled"],
        QToolButton#ProjectSwitch:disabled {
            color: rgba(161, 161, 170, 0.55);
            background: rgba(28, 28, 31, 0.65);
            border: 1px solid rgba(39, 39, 42, 0.60);
        }
        QToolButton#ProjectSwitch:hover {
            background: #27272a; /* Zinc-800 */
            color: #ffffff;
        }
        QToolButton#ProjectSwitch::menu-indicator {
            image: none;
            width: 0px;
        }
        QMenu#ProjectSwitchMenu {
            background: #1c1c1f;
            border: 1px solid #27272a;
            border-radius: 12px;
            padding: 4px;
            margin-top: 8px;
            font-family: "Inter", "Inter UI", "Segoe UI", "San Francisco", sans-serif;
            font-size: 12px;
            -qt-subpixel-positioned: true;
            outline: none;
        }
        QMenu#ProjectSwitchMenu::item {
            min-height: 32px;
            padding: 8px 12px;
            margin: 2px;
            border-radius: 8px;
            color: #a1a1aa; /* Zinc-400 */
            background: transparent;
            border: 1px solid transparent;
            font-weight: 400;
            text-align: left;
        }
        QMenu#ProjectSwitchMenu::item:selected {
            background: rgba(37, 99, 235, 0.10);
            border: 1px solid rgba(37, 99, 235, 0.30);
            color: #ffffff;
            font-weight: 600;
        }
        QMenu#ProjectSwitchMenu::item:checked {
            background: rgba(37, 99, 235, 0.10);
            border: 1px solid rgba(37, 99, 235, 0.30);
            color: #60a5fa;
            font-weight: 600;
        }
        QMenu#ProjectSwitchMenu::indicator {
            width: 0px;
            height: 0px;
        }
        QMenu#ProjectSwitchMenu::separator {
            height: 1px;
            margin: 6px 6px;
            background: rgba(39, 39, 42, 0.70);
        }
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
            background-color: #151518;
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

        /* --- Inspector --- */
        QWidget#InspectorPanel {
            background-color: #18181b;
        }
        QScrollArea#InspectorScrollArea {
            background: transparent;
        }
        QScrollArea#InspectorScrollArea QWidget {
            background: transparent;
        }
        QScrollArea#InspectorScrollArea QWidget#qt_scrollarea_viewport {
            background-color: #18181b;
        }
        QWidget#InspectorContent {
            background: transparent;
        }
        QWidget#InspectorHeader {
            background-color: #18181b;
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QLabel#InspectorHeaderTitle {
            color: #71717a;
        }
        QToolButton#InspectorCloseButton {
            padding: 6px;
            border-radius: 8px;
        }
        QToolButton#InspectorCloseButton:hover {
            background: rgba(255, 255, 255, 0.04);
        }
        QFrame#InspectorMiniCard,
        QFrame#InspectorDeptCard {
            background: #121214;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 12px;
        }
        QToolButton#InspectorManageButton {
            color: #a1a1aa;
        }
        QToolButton#InspectorManageButton:disabled {
            color: rgba(161, 161, 170, 0.45);
        }

        /* --- Badges (QSS + dynamic properties) --- */
        QLabel#StatusBadge {
            font-family: "Inter";
            font-size: 10px;
            font-weight: bold;
            padding: 2px 8px;
            border-radius: 4px;
            border: 1px solid transparent;
            background-color: rgba(255, 255, 255, 100);
        }
        QLabel#StatusBadge[status="ready"] {
            color: #10b981;
            background-color: rgba(16, 185, 129, 100);
            border: 1px solid rgba(16, 185, 129, 50);
        }
        QLabel#StatusBadge[status="progress"] {
            color: #3b82f6;
            background-color: rgba(59, 130, 246, 100);
            border: 1px solid rgba(59, 130, 246, 50);
        }
        QLabel#StatusBadge[status="blocked"] {
            color: #ef4444;
            background-color: rgba(239, 68, 68, 100);
            border: 1px solid rgba(239, 68, 68, 50);
        }
        QLabel#StatusBadge[status="waiting"] {
            color: #71717a;
            background-color: rgba(113, 113, 122, 100);
            border: 1px solid rgba(113, 113, 122, 50);
        }

        QLabel#RiskBadge {
            font-family: "Inter";
            font-size: 10px;
            font-weight: bold;
            padding: 2px 8px;
            border-radius: 4px;
            border: 1px solid transparent;
            background-color: rgba(255, 255, 255, 100);
        }
        QLabel#RiskBadge[risk="safe"] {
            color: #10b981;
            background-color: rgba(16, 185, 129, 100);
            border: 1px solid rgba(16, 185, 129, 50);
        }
        QLabel#RiskBadge[risk="medium"] {
            color: #f59e0b;
            background-color: rgba(245, 158, 11, 100);
            border: 1px solid rgba(245, 158, 11, 50);
        }
        QLabel#RiskBadge[risk="high"] {
            color: #fb923c;
            background-color: rgba(251, 146, 60, 100);
            border: 1px solid rgba(251, 146, 60, 50);
        }
        QLabel#RiskBadge[risk="critical"] {
            color: #ef4444;
            background-color: rgba(239, 68, 68, 100);
            border: 1px solid rgba(239, 68, 68, 50);
        }

        /* --- MONOS Sidebar (fixed 256px) --- */
        QWidget#SidebarContainer {
            background-color: #18181b;
        }
        QLabel#SidebarBrandIcon {
            background: #2563eb; /* Blue-600 */
            color: #ffffff;
            border-radius: 6px;
            font-weight: 700;
        }
        QLabel#SidebarBrandLabel {
            color: #dddddd;
            font-size: 16px;
            font-weight: 800;
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
        QScrollArea#SidebarScrollArea QWidget {
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

        /* Sidebar filter lists (replaces Hierarchy tree) */
        QListWidget#SidebarFilterList {
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 10px;
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa; /* Zinc-400 */
            padding: 6px;
        }
        QListWidget#SidebarFilterList::item {
            height: 28px;
            padding: 3px 10px;
            border-radius: 6px;
        }
        QListWidget#SidebarFilterList::item:hover {
            background: rgba(130, 130, 130, 0.10);
            color: #fafafa;
        }
        QListWidget#SidebarFilterList::item:selected {
            background: rgba(59, 130, 246, 0.10);
            color: #60a5fa; /* Blue-400 */
            border: 1px solid rgba(59, 130, 246, 0.30);
        }

        QToolButton#SidebarFilterAddButton {
            width: 24px;
            height: 24px;
            border-radius: 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
            font-weight: 700;
        }
        QToolButton#SidebarFilterAddButton:hover {
            background: rgba(24, 24, 27, 0.55);
            border: 1px solid rgba(39, 39, 42, 0.70);
            color: #fafafa;
        }

        /* Picker dialog (UI-only) */
        QLabel#SidebarFilterPickHint {
            color: #71717a;
            font-size: 11px;
            font-weight: 600;
        }
        QListWidget#SidebarFilterPickList {
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 10px;
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
            padding: 6px;
        }
        QListWidget#SidebarFilterPickList::item {
            height: 28px;
            padding: 6px 10px;
            border-radius: 8px;
        }
        QPushButton#SidebarFilterPickCancel,
        QPushButton#SidebarFilterPickDone {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
        }
        QPushButton#SidebarFilterPickCancel:hover,
        QPushButton#SidebarFilterPickDone:hover {
            background: rgba(24, 24, 27, 0.55);
            border: 1px solid rgba(39, 39, 42, 0.70);
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

        /* --- Metadata-driven navigation (SidebarWidget + AssetGridWidget) --- */
        QWidget#MetadataNavRoot {
            background: #121214;
        }
        QWidget#MetadataSidebar {
            background: #18181b;
            border-right: 1px solid rgba(39, 39, 42, 0.50);
            min-width: 256px;
            max-width: 256px;
        }
        QLabel#MetadataSidebarSectionTitle {
            color: #71717a; /* Zinc-500 */
        }
        QListWidget#MetadataSidebarList {
            border: 1px solid rgba(39, 39, 42, 0.45);
            border-radius: 10px;
            background: rgba(24, 24, 27, 0.45);
            padding: 6px;
            color: #a1a1aa;
        }
        QListWidget#MetadataSidebarList::item {
            height: 28px;
            padding: 6px 10px;
            border-radius: 8px;
        }
        QListWidget#MetadataSidebarList::item:selected {
            background: rgba(59, 130, 246, 0.10);
            color: #fafafa;
        }
        QPushButton#MetadataSidebarAddMore {
            padding: 8px 10px;
            border-radius: 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
            text-align: left;
        }
        QPushButton#MetadataSidebarAddMore:hover {
            background: rgba(24, 24, 27, 0.55);
            border: 1px solid rgba(39, 39, 42, 0.70);
            color: #fafafa;
        }

        QWidget#AssetGrid {
            background: #121214;
        }
        QFrame#AssetCard {
            background: #18181b;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 4px;
        }
        QFrame#AssetCard:hover {
            background: #1f1f22;
        }
        QLabel#AssetCardThumb {
            background: #27272a;
            border-radius: 4px;
            color: rgba(161, 161, 170, 0.85);
            font-family: "Inter";
            font-size: 22px;
            font-weight: 700;
        }
        QLabel#AssetCardName {
            color: #fafafa;
            font-family: "Inter";
            font-size: 13px;
            font-weight: 600;
        }
        QLabel#AssetCardTagLeft,
        QLabel#AssetCardTagRight {
            padding: 2px 6px;
            border-radius: 4px;
            color: #ffffff;
            font-family: "Inter";
            font-size: 10px;
            font-weight: 800;
        }
        QLabel#AssetCardTagLeft {
            background: #2563eb; /* Electric Blue */
        }
        QLabel#AssetCardTagRight {
            background: #10b981; /* Emerald */
        }
        """
    )

