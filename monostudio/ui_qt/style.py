from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt

from monostudio.core.app_paths import get_app_base_path
from PySide6.QtGui import (
    QBitmap,
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QPainter,
    QPalette,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QWidget
from PySide6.QtWidgets import QStyle, QProxyStyle


# Dialog panel: background, radius, border (paintEvent draws with antialiasing for smooth corners)
_MONOS_DIALOG_BG = "#18181b"
_MONOS_DIALOG_RADIUS = 12
# Border: solid light so it's always visible (lighter than #18181b)
_MONOS_DIALOG_BORDER = "#3f3f46"
# Overlay behind modal dialog: white 15% opacity
_MONOS_DIALOG_OVERLAY_CSS = "background: rgba(0, 0, 0, 0.55);"

# Menu popup: same round-corner standard (radius 12, border lighter than bg)
_MONOS_MENU_BG = "#1c1c1f"
_MONOS_MENU_RADIUS = 12
_MONOS_MENU_BORDER = "#3f3f46"


class _DialogBorderOverlay(QWidget):
    """Vẽ viền bo góc luôn nằm trên cùng, không bị content đè khi repaint."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:
        r = self.rect()
        if r.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(_MONOS_DIALOG_BORDER), 2))
        rect = r.adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(rect, _MONOS_DIALOG_RADIUS - 1, _MONOS_DIALOG_RADIUS - 1)
        painter.end()


class MonosMenu(QMenu):
    """
    QMenu bo góc chuẩn MONOS: Frameless + mask + paintEvent.
    Dùng nền opaque (không WA_TranslucentBackground) để tránh lỗi UpdateLayeredWindowIndirect
    trên Windows (dirty rect offset âm). setMask vẫn cắt shape bo góc.
    rounded=False: vẽ chữ nhật không bo góc (vd. ProjectSwitchMenu).
    """

    def __init__(self, parent=None, *, rounded: bool = True) -> None:
        super().__init__(parent)
        self._menu_rounded = rounded
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        pal = self.palette()
        pal.setColor(pal.ColorRole.Window, QColor(_MONOS_MENU_BG))
        pal.setColor(pal.ColorRole.Base, QColor(_MONOS_MENU_BG))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

    def paintEvent(self, event) -> None:
        r = self.rect()
        if r.isEmpty():
            super().paintEvent(event)
            return
        rect = r.adjusted(0, 0, -1, -1)
        radius = _MONOS_MENU_RADIUS if self._menu_rounded else 0
        overflow = 2 if self._menu_rounded else 0
        fill_rect = rect.adjusted(-overflow, -overflow, overflow, overflow)
        radius_tràn = radius + overflow if self._menu_rounded else 0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        bg = QColor(_MONOS_MENU_BG)
        painter.setBrush(QBrush(bg))
        painter.setPen(Qt.PenStyle.NoPen)
        if radius_tràn > 0:
            painter.drawRoundedRect(fill_rect, radius_tràn, radius_tràn)
        else:
            painter.drawRect(fill_rect)
        painter.end()
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        border_rect = rect.adjusted(1, 1, -1, -1)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(_MONOS_MENU_BORDER), 1))
        if radius > 0:
            painter.drawRoundedRect(border_rect, radius - 1, radius - 1)
        else:
            painter.drawRect(border_rect)
        painter.end()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_rounded_mask()

    def _update_rounded_mask(self) -> None:
        r = self.rect()
        if r.isEmpty():
            return
        w, h = r.width(), r.height()
        bitmap = QBitmap(w, h)
        bitmap.fill(Qt.GlobalColor.color0)
        painter = QPainter(bitmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.GlobalColor.color1)
        painter.setPen(Qt.PenStyle.NoPen)
        mask_rect = r.adjusted(0, 0, -1, -1)
        if self._menu_rounded:
            painter.drawRoundedRect(mask_rect, _MONOS_MENU_RADIUS, _MONOS_MENU_RADIUS)
        else:
            painter.drawRect(mask_rect)
        painter.end()
        self.setMask(bitmap)


class MonosDialog(QDialog):
    """
    Base dialog for MONOS: borderless window, rounded corners, border,
    and a 15% white overlay behind the dialog when shown.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._overlay: QWidget | None = None
        self._border_overlay: _DialogBorderOverlay | None = None
        flags = self.windowFlags()
        self.setWindowFlags(
            (flags | Qt.FramelessWindowHint) & ~Qt.WindowContextHelpButtonHint
        )
        # Transparent background so we draw rounded rect in paintEvent with antialiasing (smooth corners)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        pal = self.palette()
        pal.setColor(pal.ColorRole.Window, QColor(_MONOS_DIALOG_BG))
        pal.setColor(pal.ColorRole.Base, QColor(_MONOS_DIALOG_BG))
        self.setPalette(pal)
        self.setAutoFillBackground(False)
        self.finished.connect(self._hide_overlay)

    def paintEvent(self, event) -> None:
        # 1) Fill rounded background (antialiased).
        r = self.rect()
        if not r.isEmpty():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = r.adjusted(0, 0, -1, -1)
            painter.setBrush(QBrush(QColor(_MONOS_DIALOG_BG)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, _MONOS_DIALOG_RADIUS, _MONOS_DIALOG_RADIUS)
            painter.end()
        # 2) Draw content (children) on top of background.
        super().paintEvent(event)

    def _update_rounded_mask(self) -> None:
        # Frameless + WA_TranslucentBackground + mask bo góc (QBitmap + drawRoundedRect).
        # Mask phải set sau khi dialog đã có kích thước (showEvent / resizeEvent).
        r = self.rect()
        if r.isEmpty():
            return
        w, h = r.width(), r.height()
        bitmap = QBitmap(w, h)
        bitmap.fill(Qt.GlobalColor.color0)
        painter = QPainter(bitmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.GlobalColor.color1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(r, _MONOS_DIALOG_RADIUS, _MONOS_DIALOG_RADIUS)
        painter.end()
        self.setMask(bitmap)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_rounded_mask()
        if self._border_overlay is not None:
            self._border_overlay.setGeometry(self.rect())
            self._border_overlay.raise_()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_rounded_mask()
        # Viền vẽ bằng overlay luôn nằm trên content, không bị repaint đè
        if self._border_overlay is None:
            self._border_overlay = _DialogBorderOverlay(self)
        self._border_overlay.setGeometry(self.rect())
        self._border_overlay.raise_()
        self._border_overlay.show()
        # Overlay phủ toàn bộ cửa sổ chính (top-level), không chỉ parent trực tiếp (vd. sidebar)
        parent = self.parent()
        while isinstance(parent, QWidget) and parent.parent() and isinstance(parent.parent(), QWidget):
            parent = parent.parent()
        if isinstance(parent, QWidget) and parent.isWidgetType():
            if self._overlay is None:
                self._overlay = QWidget(parent)
                self._overlay.setStyleSheet(_MONOS_DIALOG_OVERLAY_CSS)
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self._overlay.setGeometry(0, 0, parent.width(), parent.height())
            self._overlay.raise_()
            self._overlay.show()

    def _hide_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None


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
    "amber_400": "#fbbf24",  # lighter orange (active project)
    "red_500": "#ef4444",
    "waiting": "#71717a",  # Zinc-500
    # Card
    "card_bg": "#18181b",
    "card_hover": "#1f1f22",
}

# Deterministic accent palette for projects (visually distinct on dark bg).
PROJECT_ACCENT_PALETTE: tuple[str, ...] = (
    "#60a5fa",  # Blue-400
    "#34d399",  # Emerald-400
    "#fbbf24",  # Amber-400
    "#f87171",  # Rose-400
    "#a78bfa",  # Violet-400
    "#22d3ee",  # Cyan-400
    "#fb923c",  # Orange-400
    "#f472b6",  # Pink-400
    "#4ade80",  # Green-400
    "#e879f9",  # Fuchsia-400
)


def project_accent_color(project_name: str) -> str:
    """Return a deterministic accent hex from the palette, based on project name hash."""
    h = 0
    for ch in project_name.lower().strip():
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return PROJECT_ACCENT_PALETTE[h % len(PROJECT_ACCENT_PALETTE)]


# File-type icon colors (Inbox tree / mapping list: folder, image, video, DCC, …)
FILE_TYPE_ICON_COLORS: dict[str, str] = {
    "folder": "#f59e0b",    # Amber-500
    "image": "#10b981",     # Emerald-500
    "video": "#8b5cf6",     # Violet-500
    "audio": "#f97316",     # Orange-500
    "dcc": "#3b82f6",       # Blue-500
    "archive": "#64748b",   # Slate-500
    "document": "#a1a1aa", # Zinc-400
    "file": "#a1a1aa",     # Zinc-400 default
}

# Extension sets for file_icon_spec_for_path (đồng bộ với inbox_split_view)
_FILE_EXT_IMAGE = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".ico", ".svg", ".pur"})  # .pur = PureRef
_FILE_EXT_VIDEO = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg", ".ts"})
_FILE_EXT_AUDIO = frozenset({".mp3", ".wav", ".aiff", ".aif", ".ogg", ".flac", ".m4a", ".wma", ".aac"})
_FILE_EXT_ARCHIVE = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".zst"})
_FILE_EXT_DOCUMENT = frozenset({".pdf", ".txt", ".rtf", ".md", ".odt", ".xls", ".xlsx", ".csv"})
_FILE_EXT_DCC = frozenset({".blend", ".ma", ".mb", ".hip", ".hiplc", ".hipnc"})
_FILE_EXT_SPP = frozenset({".spp"})  # Substance Painter → brand:substancepainter
_FILE_EXT_PS = frozenset({".psd", ".psb"})
_FILE_EXT_3DSMAX = frozenset({".max"})
_FILE_EXT_ZBRUSH = frozenset({".zbr", ".ztl", ".zpr"})
_FILE_EXT_FBX = frozenset({".fbx"})
_FILE_EXT_OBJ = frozenset({".obj"})
_FILE_EXT_ABC = frozenset({".abc"})
_FILE_EXT_USD = frozenset({".usd", ".usda", ".usdc"})
_FILE_EXT_UNITY = frozenset({".unity", ".prefab"})
_FILE_EXT_UNREAL = frozenset({".uproject", ".umap"})
_FILE_EXT_PPTX = frozenset({".pptx", ".ppt"})
_FILE_EXT_DOC = frozenset({".doc", ".docx"})


def file_icon_spec_for_path(path: Path) -> tuple[str, str]:
    """Return (lucide_icon_name, color_hex) for path. Đồng bộ với Inbox tree / mapping list."""
    colors = FILE_TYPE_ICON_COLORS
    try:
        if path.is_dir():
            return ("folder", colors["folder"])
    except OSError:
        pass
    ext = (path.suffix or "").strip().lower()
    if not ext.startswith("."):
        ext = "." + ext if ext else ""
    if ext in _FILE_EXT_IMAGE:
        return ("file-image", colors["image"])
    if ext in _FILE_EXT_VIDEO:
        return ("file-video", colors["video"])
    if ext in _FILE_EXT_AUDIO:
        return ("file-music", colors["audio"])
    if ext in _FILE_EXT_PS:
        return ("brand:photoshop", colors["dcc"])
    if ext in _FILE_EXT_3DSMAX:
        return ("brand:3dsmax", colors["dcc"])
    if ext in _FILE_EXT_ZBRUSH:
        return ("zbrush", colors["dcc"])
    if ext in _FILE_EXT_FBX:
        return ("box", colors["dcc"])
    if ext in _FILE_EXT_USD:
        return ("brand:usd", colors["dcc"])
    if ext in _FILE_EXT_OBJ or ext in _FILE_EXT_ABC:
        return ("box", colors["dcc"])
    if ext in _FILE_EXT_UNITY:
        return ("brand:unity", colors["dcc"])
    if ext in _FILE_EXT_UNREAL:
        return ("brand:unrealengine", colors["dcc"])
    if ext in _FILE_EXT_PPTX or ext in _FILE_EXT_DOC:
        return ("file-text", colors["document"])
    if ext in _FILE_EXT_SPP:
        return ("brand:substancepainter", colors["dcc"])
    if ext in _FILE_EXT_DCC:
        return ("box", colors["dcc"])
    if ext in _FILE_EXT_ARCHIVE:
        return ("file-archive", colors["archive"])
    if ext in _FILE_EXT_DOCUMENT:
        return ("file-text", colors["document"])
    return ("file", colors["file"])

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

# Sidebar department list: container + section header + spacer (used by _SidebarDeptListDelegate).
# Container BG gradient giống page: trái → phải #121214 → #1b1b1b.
SIDEBAR_DEPT_LIST_STYLE: dict[str, object] = {
    "section_row_height_px": 20,
    "spacer_row_height_px": 4,  # gap between containers
    "section_font_size_px": 7,
    "section_title_color_key": "text_meta",
    "container_radius_px": 6,  # rounded corners for container blocks
    "container_gradient_start": "#27272f",  # Zinc-900, sáng hơn
    "container_gradient_end": "#18181b",  # Zinc-800
}


def monos_font(
    family: str = "Inter",
    point_size: int = 13,
    weight: QFont.Weight | None = None,
    italic: bool = False,
) -> QFont:
    """
    QFont với hinting + antialiasing chuẩn MONOS (giảm răng cưa).
    - PreferVerticalHinting: chữ sắc trên màn hình, layout vẫn scale được.
    - PreferAntialias: bật khử răng cưa.
    """
    f = QFont(family, point_size)
    if weight is not None:
        f.setWeight(weight)
    if italic:
        f.setItalic(True)
    f.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def _install_fonts(app: QApplication) -> None:
    """
    Typography v1:
    - UI font: Inter (bundled in repo)
    - Technical font: JetBrains Mono (system-installed; used via stylesheet on marked fields)
    """
    base = get_app_base_path()
    inter_regular = base / "fonts" / "Inter" / "Inter-VariableFont_opsz,wght.ttf"
    inter_italic = base / "fonts" / "Inter" / "Inter-Italic-VariableFont_opsz,wght.ttf"

    for p in (inter_regular, inter_italic):
        try:
            if p.exists():
                QFontDatabase.addApplicationFont(str(p))
        except OSError:
            pass

    font = monos_font("Inter", 13, QFont.Weight.Medium)
    app.setFont(font)


class _MonosAppStyle(QProxyStyle):
    """App-wide style: no focus rect (yellow frame) on tooltips."""

    def __init__(self, base_key: str = "Fusion") -> None:
        super().__init__(base_key)

    def drawPrimitive(self, element, option, painter, widget):
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect and widget is not None:
            # Skip focus frame on tooltip (Qt.Tool) to avoid yellow border; check widget và top-level window
            from PySide6.QtCore import Qt as QtCore
            if widget.windowFlags() & QtCore.WindowType.Tool:
                return
            win = widget.window()
            if win is not None and win is not widget and (win.windowFlags() & QtCore.WindowType.Tool):
                return
        return super().drawPrimitive(element, option, painter, widget)


def apply_dark_theme(app: QApplication) -> None:
    """
    MONOS Deep Dark UI palette (Tailwind v4 inspired):
    - Base: Zinc-950/900/800 layering
    - Accent: Blue-600 / Blue-400
    - Semantic: Emerald/Amber/Red
    No animations. (Gradients may be used sparingly for active navigation states.)
    """
    app.setStyle(_MonosAppStyle())
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

        /* MONOS :: Main window (borderless) — outer border */
        QMainWindow#MonosMainWindow {
            border: 1px solid #3f3f46;
            background-color: #121214;
        }
        QSizeGrip {
            background: transparent;
            width: 20px;
            height: 20px;
        }
        QSizeGrip:hover {
            background: rgba(255, 255, 255, 0.08);
        }

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

        /* ---- MONOS :: Tooltip (Deep Dark, minimal) ---- */
        QToolTip {
            background-color: #18181b;
            color: #fafafa;
            border: 1px solid #3f3f46;
            border-radius: 8px;
            padding: 4px 6px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 500;
            outline: none;
        }

        /* Inbox Destination Block – card nhóm (WHERE / TARGET inside scroll, ACTION pinned bottom) */
        QWidget#InboxDestinationBlock {
            background: transparent;
        }
        QWidget#InboxActionWrapper {
            background: transparent;
        }
        QFrame#InboxDestCardWhere,
        QFrame#InboxDestCardTarget,
        QFrame#InboxDestCardAction {
            background-color: #18181b;
            border: 1px solid #27272a;
            border-radius: 8px;
        }
        QLabel#InboxDestCardTitle {
            color: #71717a;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.05em;
        }
        QLabel#InboxFieldLabel {
            color: #a1a1aa;
            font-size: 12px;
            font-weight: 500;
        }

        /* Scope toggle buttons (horizontal row) */
        QPushButton#InboxScopeButton {
            background: transparent;
            border: 1px solid #27272a;
            border-radius: 6px;
            color: #a1a1aa;
            padding: 5px 10px;
            font-size: 12px;
            font-weight: 500;
            text-align: left;
        }
        QPushButton#InboxScopeButton:hover {
            background-color: rgba(255, 255, 255, 0.05);
            color: #e4e4e7;
        }
        QPushButton#InboxScopeButton:checked {
            background-color: rgba(37, 99, 235, 0.15);
            border-color: #3b82f6;
            color: #93c5fd;
        }

        /* Destination / Type selectable item buttons (vertical list) */
        QPushButton#InboxDestItemButton,
        QPushButton#InboxTypeItemButton {
            background: transparent;
            border: none;
            border-radius: 4px;
            color: #a1a1aa;
            padding: 5px 8px;
            font-size: 12px;
            font-weight: 500;
            text-align: left;
        }
        QPushButton#InboxDestItemButton:hover,
        QPushButton#InboxTypeItemButton:hover {
            background-color: rgba(255, 255, 255, 0.05);
            color: #e4e4e7;
        }
        QPushButton#InboxDestItemButton:checked,
        QPushButton#InboxTypeItemButton:checked {
            background-color: rgba(37, 99, 235, 0.12);
            color: #93c5fd;
        }

        QPushButton#InboxDistributeButton {
            background-color: #2563eb;
            color: white;
            border: none;
            border-radius: 6px;
            min-height: 36px;
            font-weight: 600;
        }
        QPushButton#InboxDistributeButton:hover {
            background-color: #3b82f6;
        }
        QPushButton#InboxDistributeButton:pressed {
            background-color: #1d4ed8;
        }

        /* ---------- MONOS :: Context Menu (Deep Dark, Electric Blue) ---------- */
        QMenu {
            background-color: #18181b;
            border: 1px solid #2a2a2d;
            border-radius: 13px;
            padding: 5px 0px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 6px 28px 6px 24px;
            color: #e2e2e2;
            font-size: 13px;
            min-width: 180px;
        }
        QMenu::item:selected {
            background-color: #2563eb;
            color: white;
        }
        QMenu::item:selected:disabled {
            background-color: transparent;
        }
        QMenu::separator {
            height: 1px;
            background: #2a2a2d;
            margin: 4px 8px;
        }
        QMenu::icon {
            padding-left: 10px;
        }
        QMenu::right-arrow {
            width: 10px;
            height: 10px;
            padding-right: 10px;
        }
        QMenu::item:disabled {
            color: #555555;
        }
        QMenu::indicator {
            width: 14px;
            height: 14px;
            margin-left: 8px;
        }
        QMenu::item[class="danger-action"]:selected {
            background-color: #ef4444;
        }

        QWidget#TopBar {
            background-color: #18181b; /* Zinc-900 */
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QToolButton#ProjectSwitch {
            padding: 6px 12px;
            border: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
            background: #1c1c1f;
            color: #e2e2e2;
            font-family: "Inter", "Inter UI", "Segoe UI", "San Francisco", sans-serif;
            font-size: 24px; /* x2 from 12px */
            font-weight: 500;
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
            border: none;
        }
        QToolButton#ProjectSwitch:hover {
            background: #27272a; /* Zinc-800 */
            color: #60a5fa; /* Blue-400 */
        }
        QToolButton#ProjectSwitch::menu-indicator {
            image: none;
            width: 0px;
        }
        /* ProjectSwitchMenu: nền/viền vẽ trong MonosMenu.paintEvent (bỏ round) */
        QMenu#ProjectSwitchMenu {
            background-color: transparent;
            border: none;
            border-radius: 0;
            padding: 4px;
            margin-top: 8px;
            font-family: "Inter", "Inter UI", "Segoe UI", "San Francisco", sans-serif;
            font-size: 12px;
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
        /* Active = current project (checked) — 1px left bar Blue-400 (distinct from selected/hover) */
        QMenu#ProjectSwitchMenu::item:checked {
            background: rgba(37, 99, 235, 0.10);
            border: 1px solid rgba(37, 99, 235, 0.30);
            border-left: 1px solid #60a5fa;
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
        /* Borderless window buttons (TopBar) */
        QToolButton#WindowMinBtn,
        QToolButton#WindowMaxBtn,
        QToolButton#WindowCloseBtn,
        QToolButton#TopBarNotiBtn,
        QToolButton#TopBarUpdateBtn {
            border: none;
            border-radius: 0;
            background: transparent;
            color: #d4d4d8;
        }
        QToolButton#WindowMinBtn:hover,
        QToolButton#WindowMaxBtn:hover,
        QToolButton#TopBarNotiBtn:hover,
        QToolButton#TopBarUpdateBtn:hover {
            background: rgba(255, 255, 255, 0.08);
            color: #e4e4e7;
            border-radius: 8px;
        }
        QToolButton#WindowCloseBtn:hover {
            background: #ef4444;
            color: white;
            border-radius: 8px;
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

        /* --- Inbox tree pane: file tree (modern flat look, full-row selection) --- */
        QTreeView#InboxSplitTree {
            background-color: #121214;
            border: none;
            outline: none;
            color: #a1a1aa;
            font-size: 13px;
            padding: 8px 0;
            /* Nền selection do delegate vẽ full-row; tránh style vẽ thêm từng vùng */
            selection-background-color: transparent;
            selection-color: #60a5fa;
        }
        QTreeView#InboxSplitTree::item {
            padding: 6px 12px;
            border-radius: 4px;
            margin: 1px 8px;
        }
        QTreeView#InboxSplitTree::item:hover {
            background-color: transparent;
            color: #fafafa;
        }
        QTreeView#InboxSplitTree::item:selected {
            background-color: transparent;
            color: #60a5fa;
        }
        QTreeView#InboxSplitTree::item:selected:!active {
            background-color: transparent;
            color: #a1a1aa;
        }
        /* Branch: ẩn mũi tên mặc định, dùng Lucide chevron vẽ trong delegate */
        QTreeView#InboxSplitTree::branch {
            background: transparent;
            image: none;
        }
        QTreeView#InboxSplitTree::branch:has-children:!has-siblings:closed,
        QTreeView#InboxSplitTree::branch:has-children:has-siblings:closed,
        QTreeView#InboxSplitTree::branch:has-children:!has-siblings:open,
        QTreeView#InboxSplitTree::branch:has-children:has-siblings:open {
            background: transparent;
            image: none;
        }

        /* --- Inbox mapping list (match tree + list style) --- */
        QListWidget#InboxMappingList {
            background-color: #121214;
            border: none;
            outline: none;
            color: #a1a1aa;
            font-size: 13px;
            padding: 4px 0;
        }
        QListWidget#InboxMappingList::item {
            padding: 8px 12px;
            border-radius: 4px;
            margin: 2px 8px;
        }
        QListWidget#InboxMappingList::item:hover {
            background-color: rgba(255, 255, 255, 0.06);
            color: #fafafa;
        }
        QListWidget#InboxMappingList::item:selected {
            background-color: rgba(59, 130, 246, 0.12);
            color: #60a5fa;
        }

        /* --- MONOS Deep Dark Table (Pipeline: Departments, Types mapping) --- */
        QTableWidget, QTableView {
            background-color: #121214;
            border: 1px solid #2a2a2c;
            gridline-color: #2a2a2c;
            color: #eeeeee;
            font-size: 12px;
            selection-background-color: rgba(37, 99, 235, 0.15);
            selection-color: #2563eb;
            outline: none;
        }
        QHeaderView::section {
            background-color: #0d0d0f;
            color: #4a4a4c;
            padding: 10px 15px;
            border: none;
            border-bottom: 2px solid #2a2a2c;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        QHeaderView::section:hover {
            background-color: #1e1e20;
            color: #888888;
        }
        QTableWidget::item, QTableView::item {
            padding: 8px 15px;
            border-bottom: 1px solid #1e1e20;
        }
        QTableWidget::item:hover, QTableView::item:hover {
            background-color: rgba(255, 255, 255, 0.02);
        }
        QTableWidget::item:selected, QTableView::item:selected {
            background-color: rgba(37, 99, 235, 0.1);
            color: #ffffff;
            border-bottom: 1px solid #2563eb;
        }
        QTableCornerButton::section {
            background-color: #0d0d0f;
            border: none;
            border-bottom: 2px solid #2a2a2c;
        }

        /* --- MainView Asset Browser header --- */
        QWidget#MainViewHeader {
            background-color: #151518;
            border-top: 1px solid rgba(39, 39, 42, 0.50);
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QLabel#MainViewContextTitle {
            color: #cccccc;
            font-weight: 700;
        }
        QWidget#MainViewTypeBadge {
            background: rgba(63, 63, 70, 0.45);
            border: 1px solid rgba(63, 63, 70, 0.80);
            border-radius: 6px;
        }
        QLabel#MainViewTypeBadgeLabel {
            color: #cccccc;
            font-weight: 700;
        }
        QWidget#MainViewDepartmentBadge {
            background: rgba(59, 130, 246, 0.42);  /* Blue-500 tint, nổi bật hơn */
            border: 1px solid rgba(96, 165, 250, 0.55);  /* Blue-400 viền */
            border-radius: 6px;
        }
        QLabel#MainViewDepartmentBadgeLabel {
            color: #e0e7ff;  /* Indigo-100, dễ đọc trên nền xanh */
            font-weight: 700;
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
        QToolButton:hover {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(63, 63, 70, 0.80);
            color: #e4e4e7; /* Zinc-200 */
        }
        QPushButton#MainViewPrimaryAction {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(37, 99, 235, 0.70); /* Blue-600 */
            background: rgba(37, 99, 235, 0.22); /* Blue-600 */
            color: #fafafa;
            text-align: center;
        }
        QPushButton#MainViewPrimaryAction:hover {
            background: rgba(37, 99, 235, 0.45);
            border: 1px solid rgba(96, 165, 250, 0.70); /* Blue-400 */
            color: #ffffff;
        }
        QPushButton#MainViewPrimaryAction:disabled {
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: rgba(250, 250, 250, 0.45);
        }
        QLineEdit#MainViewSearchInput {
            background-color: #1c1c1f;
            border: 1px solid #2a2a2d;
            border-radius: 6px;
            color: #e4e4e7;
            font-size: 13px;
            padding: 6px 10px;
        }
        QLineEdit#MainViewSearchInput:focus {
            border: 1px solid rgba(63, 63, 70, 0.90);
        }
        QToolButton#MainViewSearchClear {
            padding: 4px;
            border: none;
            background: transparent;
        }
        QToolButton#MainViewSearchClear:hover {
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
        }
        QFrame#MainViewSearchPopup {
            background-color: #1c1c1f;
            border: 1px solid #2a2a2d;
            border-radius: 8px;
        }
        QToolButton#MainViewSearchIconButton {
            padding: 6px;
            border: none;
            background: transparent;
        }
        QToolButton#MainViewSearchIconButton:hover {
            background: rgba(255, 255, 255, 0.08);
            border-radius: 6px;
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
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
        }
        QToolButton#InspectorCloseButton:hover {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(63, 63, 70, 0.80);
            color: #e4e4e7;
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
        QToolButton#InspectorManageButton:hover {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(63, 63, 70, 0.80);
            color: #e4e4e7; /* Zinc-200 */
        }
        QToolButton#InspectorManageButton:disabled {
            color: rgba(161, 161, 170, 0.45);
        }
        /* All tool buttons inside Inspector: ensure hover is visible */
        QWidget#InspectorPanel QToolButton:hover {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(63, 63, 70, 0.80);
            color: #e4e4e7;
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

        /* --- Dialogs (MONOS): background + border drawn in MonosDialog.paintEvent (antialiased) --- */
        QDialog {
            background-color: transparent;
        }
        QDialog#SidebarFilterPickDialog {
            background-color: transparent;
        }
        /* MONOS calendar (Inbox drop: date picker) — Deep Dark, 8px radius embed */
        QCalendarWidget#MonosCalendar {
            background-color: #18181b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
            color: #d4d4d8;
            font-family: "Inter";
            font-size: 13px;
        }
        QCalendarWidget#MonosCalendar QWidget#qt_calendar_navigationbar {
            background: transparent;
            min-height: 36px;
        }
        QCalendarWidget#MonosCalendar QToolButton {
            background: transparent;
            color: #a1a1aa;
            border: none;
            border-radius: 4px;
            min-width: 28px;
            min-height: 28px;
        }
        QCalendarWidget#MonosCalendar QToolButton:hover {
            background: rgba(255, 255, 255, 0.06);
            color: #fafafa;
        }
        QCalendarWidget#MonosCalendar QAbstractItemView {
            background: transparent;
            selection-background-color: transparent;
            color: #d4d4d8;
            font-size: 13px;
            outline: none;
            border: none;
            gridline-color: transparent;
        }
        QCalendarWidget#MonosCalendar QAbstractItemView:focus {
            outline: none;
            border: none;
        }
        QCalendarWidget#MonosCalendar QAbstractItemView:disabled {
            color: #71717a;
        }
        /* Weekday header: avoid "..." elision; match code section size 56px */
        QCalendarWidget#MonosCalendar QHeaderView::section {
            font-size: 10px;
            font-weight: 600;
            color: #71717a;
            padding: 6px 2px;
            min-width: 52px;
        }
        /* Custom nav bar buttons (MonosCalendarWidget) */
        QPushButton#MonosCalendarPrevBtn,
        QPushButton#MonosCalendarNextBtn {
            background: transparent;
            border: none;
            border-radius: 6px;
        }
        QPushButton#MonosCalendarPrevBtn:hover,
        QPushButton#MonosCalendarNextBtn:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        /* Inbox Drop Dialog: panel bg #18181b (override app_bg #09090b) */
        QDialog#InboxDropDialog {
            background-color: #18181b;
        }
        /* Inbox Drop Dialog: nút Add to Inbox (primary) và Cancel (secondary) */
        QDialog#InboxDropDialog QDialogButtonBox QPushButton#DialogPrimaryButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(37, 99, 235, 0.70);
            background: rgba(37, 99, 235, 0.22);
            color: #fafafa;
        }
        QDialog#InboxDropDialog QDialogButtonBox QPushButton#DialogPrimaryButton:hover {
            background: rgba(37, 99, 235, 0.35);
            border-color: rgba(59, 130, 246, 0.80);
        }
        QDialog#InboxDropDialog QDialogButtonBox QPushButton#DialogSecondaryButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
        }
        QDialog#InboxDropDialog QDialogButtonBox QPushButton#DialogSecondaryButton:hover {
            background: rgba(24, 24, 27, 0.55);
            border-color: rgba(39, 39, 42, 0.70);
            color: #fafafa;
        }
        QScrollArea#InboxDropScroll {
            background-color: #18181b;
            border: none;
        }
        QScrollArea#InboxDropScroll::viewport {
            background-color: #18181b;
        }
        QScrollArea#InboxDropScroll QWidget#scrollAreaWidgetContents,
        QWidget#InboxDropForm {
            background-color: #18181b;
        }
        /* Inbox Drop Dialog: nút calendar (mở date picker) */
        QPushButton#InboxDropCalendarBtn {
            background: rgba(24, 24, 27, 0.35);
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
        }
        QPushButton#InboxDropCalendarBtn:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(63, 63, 70, 0.80);
        }
        /* Inbox Drop Dialog: items list frame */
        QFrame#InboxDropItemsList {
            background-color: #1e1e20;
            border: 1px solid #3f3f46;
            border-radius: 8px;
        }
        QLabel#DialogHint {
            color: #a1a1aa;
            font-size: 11px;
        }
        QTextEdit#UpdateChangelog {
            background-color: #121214;
            color: #e4e4e7;
            border: 1px solid #27272a;
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 12px;
            font-family: "Inter", sans-serif;
        }
        QFrame#UpdateVersionCard {
            background-color: #1e1e20;
            border: 1px solid #27272a;
            border-radius: 8px;
        }
        /* Product list (other products): one row per product, Maxon-style */
        QFrame#UpdateProductList {
            background-color: #18181b;
            border: 1px solid #27272a;
            border-radius: 8px;
        }
        QWidget#UpdateProductListRow {
            border-bottom: 1px solid #27272a;
            min-height: 44px;
        }
        QWidget#UpdateProductListRow[last="true"] {
            border-bottom: none;
        }
        QLabel#UpdateProductListName {
            font-size: 13px;
            font-weight: 600;
            color: #fafafa;
        }
        QLabel#UpdateProductListVersion {
            font-size: 12px;
            color: #a1a1aa;
            font-family: "JetBrains Mono", "Consolas", monospace;
        }
        /* Link-style button (View release notes) */
        QPushButton#UpdateProductListLink {
            background: transparent;
            border: none;
            color: #818cf8;
            font-size: 12px;
            text-decoration: underline;
        }
        QPushButton#UpdateProductListLink:hover {
            color: #a5b4fc;
        }
        /* Primary: Download vX.X.X */
        QPushButton#UpdateProductListBtnDownload {
            background-color: #6366f1;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 6px 14px;
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#UpdateProductListBtnDownload:hover {
            background-color: #4f46e5;
        }
        QPushButton#UpdateProductListBtnDownload:disabled {
            background-color: #4338ca;
            opacity: 0.7;
        }
        /* Muted: Latest (already up to date) */
        QPushButton#UpdateProductListBtnLatest {
            background-color: #27272a;
            color: #71717a;
            border: 1px solid #3f3f46;
            border-radius: 8px;
            padding: 6px 14px;
            font-size: 12px;
            font-weight: 500;
        }
        QPushButton#UpdateProductListBtnLatest:hover {
            background-color: #3f3f46;
            color: #a1a1aa;
        }
        /* Download loading: progress bar + cancel */
        QProgressBar#UpdateDownloadProgress {
            background-color: #27272a;
            border: none;
            border-radius: 4px;
            text-align: center;
            min-height: 8px;
        }
        QProgressBar#UpdateDownloadProgress::chunk {
            background-color: #6366f1;
            border-radius: 4px;
        }
        QToolButton#UpdateDownloadCancelBtn {
            background: transparent;
            border: none;
            border-radius: 4px;
        }
        QToolButton#UpdateDownloadCancelBtn:hover {
            background-color: rgba(255, 255, 255, 0.1);
        }
        QLabel#UpdateVersionValue {
            font-size: 18px;
            font-weight: 700;
            color: #fafafa;
            font-family: "JetBrains Mono", "Consolas", monospace;
        }
        QLabel#UpdateStatusText {
            font-size: 13px;
            color: #a1a1aa;
            min-height: 20px;
        }
        QLabel#UpdateSectionLabel {
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.08em;
            color: #71717a;
        }
        /* Open Resolver / Create New: header icon + bold title */
        QLabel#OpenResolverDialogTitle {
            font-weight: 700;
            font-size: 14px;
            color: #fafafa;
        }
        QLabel#OpenResolverContextValue {
            font-weight: 600;
            font-size: 13px;
            color: #e4e4e7;
        }
        /* Open Resolver: DCC selection cards — circular like type/department badges */
        QFrame#DccCard {
            background-color: #1e1e20;
            border: 1px solid #2a2a2d;
            border-radius: 50%;
        }
        QFrame#DccCard[last_used="true"] {
            border: 2px solid rgba(37, 99, 235, 0.5);
        }
        QFrame#DccCard[last_used="true"][selected="true"] {
            border: 2px solid #2563eb;
            background-color: rgba(37, 99, 235, 0.12);
        }
        QFrame#DccCard:hover {
            background-color: #27272a;
            border-color: #3f3f46;
        }
        QFrame#DccCard[selected="true"] {
            border: 2px solid #2563eb;
            background-color: rgba(37, 99, 235, 0.12);
        }
        QFrame#DccCard[selected="true"]:hover {
            background-color: rgba(37, 99, 235, 0.18);
        }
        QFrame#DccCard:disabled {
            background-color: #18181b;
            border-color: #27272a;
            opacity: 0.6;
        }
        QLabel#DccCardLabel {
            color: #e4e4e7;
            font-size: 13px;
            font-weight: 500;
        }
        QScrollArea#OpenResolverScroll {
            background: transparent;
            border: none;
        }
        QLabel#DialogHelper {
            color: #a1a1aa;
            font-size: 11px;
        }
        QLabel#DialogWarning {
            color: #f59e0b;
            font-size: 11px;
            font-weight: 600;
        }
        QLabel#DialogLabelMeta {
            color: #71717a;
        }
        QLabel#DialogLabelPrimary {
            color: #fafafa;
        }
        QLabel#DialogSectionTitle {
            font-weight: 700;
            color: #fafafa;
        }
        QLabel#DialogPrefixChip {
            padding: 6px 10px;
            border: 1px solid rgba(39, 39, 42, 0.70);
            border-radius: 6px;
            background: #27272a;
            color: #a1a1aa;
        }
        QDialog QGroupBox {
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
            background: #121214;
            padding-top: 12px;
            margin-top: 8px;
        }
        QDialog QGroupBox::title {
            color: #a1a1aa;
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            left: 8px;
        }
        /* Categories (Asset/Shot Depts): groupbox + title giống nhau cho cả hai trang */
        QGroupBox#SettingsCategoryGroup {
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
            background: #121214;
            padding-top: 12px;
            margin-top: 8px;
        }
        QGroupBox#SettingsCategoryGroup::title {
            color: #a1a1aa;
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            left: 8px;
            font-size: 13px;
            font-weight: 600;
        }

        /* Dialog: tabs (MONOS) — same as Tier 2 (font to, đậm) */
        QDialog QTabWidget::pane {
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-top: none;
            border-radius: 0 0 8px 8px;
            background: #121214;
            top: -1px;
            padding: 12px;
        }
        QDialog QTabWidget::tab-bar {
            alignment: left;
        }
        QDialog QTabWidget::tab {
            background: #18181b;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 8px 16px;
            margin-right: 2px;
            color: #a1a1aa;
            font-size: 13px;
            font-weight: 600;
        }
        QDialog QTabWidget::tab:selected {
            background: #121214;
            color: #fafafa;
            border-color: rgba(39, 39, 42, 0.50);
        }
        QDialog QTabWidget::tab:hover:!selected {
            background: #1f1f22;
            color: #fafafa;
        }
        QDialog QTabWidget::tab:selected:focus {
            outline: none;
        }
        QDialog QTabWidget QTabBar {
            outline: none;
        }
        QDialog QTabWidget QTabBar:focus {
            outline: none;
            border: none;
        }

        /* Settings Tier 2: horizontal module tabs (General/Pipeline/Project) — inherits QDialog QTabWidget, same spec */
        QTabWidget#SettingsTier2Tabs::pane {
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-top: none;
            border-radius: 0 0 8px 8px;
            background: #121214;
            top: -1px;
            padding: 12px;
        }
        QTabWidget#SettingsTier2Tabs::tab-bar {
            alignment: left;
        }
        QTabWidget#SettingsTier2Tabs::tab {
            background: #18181b;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 8px 16px;
            margin-right: 2px;
            color: #a1a1aa;
            font-size: 13px;
            font-weight: 600;
        }
        QTabWidget#SettingsTier2Tabs::tab:selected {
            background: #121214;
            color: #fafafa;
            border-color: rgba(39, 39, 42, 0.50);
        }
        QTabWidget#SettingsTier2Tabs::tab:hover:!selected {
            background: #1f1f22;
            color: #fafafa;
        }
        QTabWidget#SettingsTier2Tabs::tab:selected:focus {
            outline: none;
        }
        QTabWidget#SettingsTier2Tabs QTabBar {
            outline: none;
        }
        QTabWidget#SettingsTier2Tabs QTabBar:focus {
            outline: none;
            border: none;
        }

        /* Settings Tier 3: Segmented Control (The Filter) — one bar, segments joined */
        QTabWidget#SettingsPillTabs::pane {
            border: 1px solid rgba(39, 39, 42, 0.40);
            border-radius: 8px;
            background: #18181b;
            margin-top: 8px;
            padding: 12px;
        }
        QTabWidget#SettingsPillTabs::tab-bar {
            alignment: left;
        }
        QTabWidget#SettingsPillTabs::tab {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(39, 39, 42, 0.45);
            border-right: none;
            border-radius: 0;
            padding: 6px 14px;
            margin-right: 0;
            color: #a1a1aa;
            font-size: 12px;
            font-weight: 500;
        }
        QTabWidget#SettingsPillTabs::tab:first {
            border-top-left-radius: 6px;
            border-bottom-left-radius: 6px;
        }
        QTabWidget#SettingsPillTabs::tab:last {
            border-right: 1px solid rgba(39, 39, 42, 0.45);
            border-top-right-radius: 6px;
            border-bottom-right-radius: 6px;
        }
        QTabWidget#SettingsPillTabs::tab:selected {
            background: rgba(59, 130, 246, 0.18);
            border-color: rgba(37, 99, 235, 0.45);
            color: #60a5fa;
        }
        QTabWidget#SettingsPillTabs::tab:selected:first {
            border-right: none;
        }
        QTabWidget#SettingsPillTabs::tab:selected:last {
            border-right: 1px solid rgba(37, 99, 235, 0.45);
        }
        QTabWidget#SettingsPillTabs::tab:hover:!selected {
            background: rgba(255, 255, 255, 0.08);
            color: #fafafa;
        }

        /* Settings Tier 2: page tabs (underline style) */
        QWidget#SettingsPageButtonBar {
            background: transparent;
            border: none;
        }
        QPushButton#Tier2Tab {
            background: transparent;
            border: none;
            border-radius: 0px;
            color: #888888;
            font-weight: bold;
            font-size: 14px;
            padding-bottom: 8px;
        }
        QPushButton#Tier2Tab:checked {
            color: #fafafa;
            border-bottom: 2px solid #2563eb;
        }
        QPushButton#Tier2Tab:hover:!checked {
            color: #a1a1aa;
        }

        /* Settings Tier 3: pill container + pills (Asset Depts | Shot Depts) */
        QWidget#Tier3Container {
            background-color: #1e1e20;
            border-radius: 15px;
        }
        QPushButton#Tier3Pill {
            background: transparent;
            border: none;
            border-radius: 13px;
            color: #888888;
            padding: 5px 15px;
            font-size: 11px;
            min-height: 22px;
        }
        QPushButton#Tier3Pill:checked {
            background-color: #2a2a2c;
            border: none;
            border-radius: 13px;
            color: #fafafa;
        }
        QPushButton#Tier3Pill:hover:!checked {
            border: none;
            border-radius: 13px;
            color: #a1a1aa;
        }

        QStackedWidget#SettingsPageStack {
            background: #121214;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
            padding: 12px;
        }

        /* Selectable List (Settings — Departments, Types list) */
        QListWidget#SelectableList, QListView#SelectableList {
            background-color: #0d0d0f;
            border: none;
            outline: none;
            padding: 5px;
        }
        QListWidget#SelectableList::item, QListView#SelectableList::item {
            background-color: transparent;
            color: #888888;
            padding: 8px 12px;
            margin-bottom: 2px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            border-left: 3px solid transparent;
        }
        QListWidget#SelectableList::item:hover, QListView#SelectableList::item:hover {
            background-color: rgba(255, 255, 255, 0.03);
            color: #eeeeee;
        }
        QListWidget#SelectableList::item:selected, QListView#SelectableList::item:selected {
            background-color: rgba(37, 99, 235, 0.10);
            color: #2563eb;
            border-left: 3px solid #2563eb;
            font-weight: 700;
        }
        QListWidget#SelectableList QScrollBar:vertical, QListView#SelectableList QScrollBar:vertical {
            border: none;
            background: #0d0d0f;
            width: 8px;
            margin: 0px;
        }
        QListWidget#SelectableList QScrollBar::handle:vertical, QListView#SelectableList QScrollBar::handle:vertical {
            background: #2a2a2c;
            min-height: 20px;
            border-radius: 4px;
        }
        QListWidget#SelectableList QScrollBar::handle:vertical:hover, QListView#SelectableList QScrollBar::handle:vertical:hover {
            background: #3a3a3c;
        }
        QListWidget#SelectableList QScrollBar::add-line:vertical, QListWidget#SelectableList QScrollBar::sub-line:vertical,
        QListView#SelectableList QScrollBar::add-line:vertical, QListView#SelectableList QScrollBar::sub-line:vertical {
            height: 0px;
        }

        /* Selectable List (multi-select): checkbox icon at start, no border-left */
        QListWidget#SelectableListMulti, QListView#SelectableListMulti {
            background-color: #0d0d0f;
            border: none;
            outline: none;
            padding: 5px;
        }
        QListWidget#SelectableListMulti::item, QListView#SelectableListMulti::item {
            background-color: transparent;
            color: #888888;
            padding: 8px 12px;
            margin-bottom: 2px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
        }
        QListWidget#SelectableListMulti::item:hover, QListView#SelectableListMulti::item:hover {
            background-color: rgba(255, 255, 255, 0.03);
            color: #eeeeee;
        }
        QListWidget#SelectableListMulti::item:selected, QListView#SelectableListMulti::item:selected {
            background-color: rgba(37, 99, 235, 0.10);
            color: #2563eb;
            font-weight: 700;
        }
        QListWidget#SelectableListMulti QScrollBar:vertical, QListView#SelectableListMulti QScrollBar:vertical {
            border: none;
            background: #0d0d0f;
            width: 8px;
            margin: 0px;
        }
        QListWidget#SelectableListMulti QScrollBar::handle:vertical, QListView#SelectableListMulti QScrollBar::handle:vertical {
            background: #2a2a2c;
            min-height: 20px;
            border-radius: 4px;
        }
        QListWidget#SelectableListMulti QScrollBar::handle:vertical:hover, QListView#SelectableListMulti QScrollBar::handle:vertical:hover {
            background: #3a3a3c;
        }
        QListWidget#SelectableListMulti QScrollBar::add-line:vertical, QListWidget#SelectableListMulti QScrollBar::sub-line:vertical,
        QListView#SelectableListMulti QScrollBar::add-line:vertical, QListView#SelectableListMulti QScrollBar::sub-line:vertical {
            height: 0px;
        }

        /* Dialog: buttons (MONOS) */
        QDialog QPushButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
        }
        QDialog QPushButton:hover {
            background: rgba(24, 24, 27, 0.55);
            border-color: rgba(39, 39, 42, 0.70);
            color: #fafafa;
        }
        QDialog QPushButton:disabled {
            color: rgba(161, 161, 170, 0.5);
            background: rgba(24, 24, 27, 0.25);
        }
        /* Nút đồng ý trong dialog: ghi đè QDialog QPushButton, luôn màu primary */
        QDialog QPushButton#DialogPrimaryButton {
            background: rgba(37, 99, 235, 0.22);
            border: 1px solid rgba(37, 99, 235, 0.70);
            color: #fafafa;
        }
        QDialog QPushButton#DialogPrimaryButton:hover {
            background: rgba(37, 99, 235, 0.35);
            border-color: rgba(59, 130, 246, 0.80);
        }
        QPushButton#DialogPrimaryButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(37, 99, 235, 0.70);
            background: rgba(37, 99, 235, 0.22);
            color: #fafafa;
        }
        QPushButton#DialogPrimaryButton:hover {
            background: rgba(37, 99, 235, 0.35);
            border-color: rgba(59, 130, 246, 0.80);
        }
        QPushButton#DialogPrimaryButton:disabled {
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: rgba(250, 250, 250, 0.45);
        }
        /* Dialog: nút hủy (phải) — màu xám, giống Settings */
        QPushButton#DialogSecondaryButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
        }
        QPushButton#DialogSecondaryButton:hover {
            background: rgba(24, 24, 27, 0.55);
            border-color: rgba(39, 39, 42, 0.70);
            color: #fafafa;
        }
        QPushButton#DialogSecondaryButton:disabled {
            color: rgba(161, 161, 170, 0.5);
            background: rgba(24, 24, 27, 0.25);
        }
        /* Dialog: nút Delete (destructive) — đỏ */
        QPushButton#DialogDestructiveButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(239, 68, 68, 0.60);
            background: rgba(239, 68, 68, 0.18);
            color: #fca5a5;
        }
        QPushButton#DialogDestructiveButton:hover {
            background: rgba(239, 68, 68, 0.30);
            border-color: rgba(239, 68, 68, 0.85);
            color: #fafafa;
        }
        /* Categories (Asset/Shot Depts): nút Create/Delete Type — style giống nhau cho cả hai trang */
        QPushButton#SettingsCategoryActionButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
        }
        QPushButton#SettingsCategoryActionButton:hover {
            background: rgba(24, 24, 27, 0.55);
            border-color: rgba(39, 39, 42, 0.70);
            color: #fafafa;
        }
        QPushButton#SettingsCategoryActionButton:disabled {
            color: rgba(161, 161, 170, 0.5);
            background: rgba(24, 24, 27, 0.25);
        }

        /* --- Settings dialog: page-style nav (like main UI sidebar) --- */
        QFrame#SettingsNavFrame {
            background-color: #121214;
            border-right: 1px solid rgba(39, 39, 42, 0.50);
        }
        QListWidget#SettingsNav {
            border: none;
            border-radius: 0;
            background: transparent;
            padding: 8px 0;
            outline: none;
        }
        QListWidget#SettingsNav:focus {
            outline: none;
            border: none;
        }
        QListWidget#SettingsNav::item {
            height: 36px;
            padding-left: 16px;
            border-radius: 8px;
            margin: 2px 8px;
            color: #a1a1aa;
        }
        QListWidget#SettingsNav::item:hover {
            background: rgba(255, 255, 255, 0.04);
            color: #fafafa;
        }
        QListWidget#SettingsNav::item:selected {
            background: rgba(59, 130, 246, 0.12);
            color: #60a5fa;
            border: none;
            outline: none;
        }
        QListWidget#SettingsNav::item:selected:focus {
            border: none;
            outline: none;
        }

        /* Settings dialog: sub-nav (second column, same pattern as nav) */
        QFrame#SettingsSubNavFrame {
            background-color: #121214;
            border-right: 1px solid rgba(39, 39, 42, 0.50);
        }
        QListWidget#SettingsSubNav {
            border: none;
            border-radius: 0;
            background: transparent;
            padding: 8px 0;
            outline: none;
        }
        QListWidget#SettingsSubNav:focus {
            outline: none;
            border: none;
        }
        QListWidget#SettingsSubNav::item {
            height: 36px;
            padding-left: 16px;
            border-radius: 8px;
            margin: 2px 8px;
            color: #a1a1aa;
        }
        QListWidget#SettingsSubNav::item:hover {
            background: rgba(255, 255, 255, 0.04);
            color: #fafafa;
        }
        QListWidget#SettingsSubNav::item:selected {
            background: rgba(59, 130, 246, 0.12);
            color: #60a5fa;
            border: none;
            outline: none;
        }
        QListWidget#SettingsSubNav::item:selected:focus {
            border: none;
            outline: none;
        }

        /* --- MONOS Sidebar (fixed 256px) --- */
        QWidget#SidebarContainer {
            background-color: #18181b;
        }
        QLabel#SidebarBrandIcon {
            background: #27272a; /* Zinc-800, black & white */
            color: #ffffff;
            border-radius: 6px;
            font-weight: 700;
        }
        QLabel#SidebarBrandLabel {
            color: #dddddd;
            font-size: 16px;
            font-weight: 800;
            font-style: italic;
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

        /* Scope pill: Project | Shot | Asset (one pill, three segments) */
        QWidget#SidebarScopePill {
            background-color: #1e1e20;
            border-radius: 12px;
        }
        QToolButton#SidebarScopePillSegment {
            background: transparent;
            border: none;
            color: #a1a1aa;
            padding: 0 10px;
            margin: 0;
            font-size: 13px;
            min-height: 32px;
        }
        QToolButton#SidebarScopePillSegment[position="left"] {
            border-top-left-radius: 10px;
            border-bottom-left-radius: 10px;
        }
        QToolButton#SidebarScopePillSegment[position="center"] {
            border-radius: 0;
        }
        QToolButton#SidebarScopePillSegment[position="center"][active="true"] {
            border-radius: 8px;
        }
        QToolButton#SidebarScopePillSegment[position="right"] {
            border-top-right-radius: 10px;
            border-bottom-right-radius: 10px;
        }
        QToolButton#SidebarScopePillSegment[active="true"] {
            background-color: #2a2a2c;
            color: #60a5fa;
            font-weight: 700;
            font-style: italic;
        }
        QToolButton#SidebarScopePillSegment:hover {
            color: #fafafa;
        }
        QToolButton#SidebarScopePillSegment[active="true"]:hover {
            background-color: #2a2a2c;
            color: #60a5fa;
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
                stop: 0 rgba(59, 130, 246, 0.4), /* active glow */
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
            font-weight: 700;
            font-style: italic;
        }
        QLabel#SidebarNavBadge {
            min-height: 18px;
            padding: 0px 6px;
            border-radius: 9px; /* pill */
            background: rgba(255, 255, 255, 0.06);
            color: #a1a1aa;
        }
        QLabel#SidebarNavBadge[shape="dot"] {
            min-width: 18px;
            max-width: 18px;
            padding: 0px;
            border-radius: 9px; /* circle */
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
            border-radius: 10px;
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

        QListWidget#SidebarTagList {
            border-radius: 10px;
            color: #a1a1aa;
            padding: 6px;
            font-size: 10px;
            font-weight: 400;
        }
        QListWidget#SidebarTagList::item {
            height: 26px;
            padding: 2px 10px 2px 6px;
            border-radius: 6px;
        }
        QListWidget#SidebarTagList::item:hover {
            background: rgba(130, 130, 130, 0.10);
            color: #fafafa;
        }
        QListWidget#SidebarTagList::item:selected {
            background: rgba(59, 130, 246, 0.10);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.30);
        }

        /* Tag empty overlay (Project Guide tree) */
        QWidget#TagEmptyOverlay {
            background: transparent;
        }
        QLabel#TagEmptyOverlayText {
            color: #52525b;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 500;
            background: transparent;
        }

        /* Department empty overlay (Project Guide tree, same as Inbox) */
        QWidget#RefDeptEmptyOverlay {
            background: transparent;
        }
        QLabel#RefDeptEmptyOverlayText {
            color: #71717a;
            font-family: "Inter";
            font-size: 13px;
            font-weight: 500;
            background: transparent;
        }

        /* Recent tasks list (sidebar) */
        QListWidget#SidebarRecentTasksList {
            border: none;
            border-radius: 6px;
            background: transparent;
            color: #a1a1aa;
            padding: 4px 0;
        }
        QListWidget#SidebarRecentTasksList::item {
            height: 32px;
            padding: 4px 10px;
            border-radius: 6px;
        }
        QListWidget#SidebarRecentTasksList::item:hover {
            background: rgba(130, 130, 130, 0.10);
            color: #fafafa;
        }
        QListWidget#SidebarRecentTasksList::item:selected {
            background: rgba(59, 130, 246, 0.10);
            color: #60a5fa;
        }

        QToolButton#SidebarFilterAddButton {
            width: 16px;
            height: 16px;
            border-radius: 8px;
            border: 0px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: #a1a1aa;
            font-weight: 700;
            font-size: 18px;
        }
        QToolButton#SidebarFilterAddButton:hover {
            background: rgba(64, 64, 74, 0.55);
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
        QFrame#SidebarNavSeparator {
            background-color: rgba(63, 63, 70, 0.6);
            border: none;
            max-height: 1px;
        }
        QToolButton#SidebarFooterNavButton {
            background: transparent;
            border: none;
            border-radius: 6px;
        }
        QToolButton#SidebarFooterNavButton:hover {
            background: rgba(255, 255, 255, 0.06);
        }
        QToolButton#SidebarRecentTasksClearButton {
            background: transparent;
            border: none;
            border-radius: 4px;
            padding: 2px;
        }
        QToolButton#SidebarRecentTasksClearButton:hover:enabled {
            background: rgba(255, 255, 255, 0.06);
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

