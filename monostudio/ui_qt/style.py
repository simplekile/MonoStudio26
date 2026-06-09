from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QEvent, QObject

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
# Shell chrome: sidebar, inspector, top bar, app footer
_MONOS_CHROME_BG = "#181a1d"
# Main view content surface + cards (tight step from chrome for low contrast)
_MONOS_CONTENT_BG = "#151618"
_MONOS_CONTENT_CARD_BG = "#191b1e"
_MONOS_CONTENT_CARD_HOVER = "#1d1f23"
# Top bar + app footer — same shell chrome as sidebar / inspector
_MONOS_TOPBAR_BG = _MONOS_CHROME_BG
_MONOS_DIALOG_RADIUS = 12
# Border: solid light so it's always visible (lighter than #18181b)
_MONOS_DIALOG_BORDER = "#3f3f46"
# Overlay behind modal dialog: white 15% opacity
_MONOS_DIALOG_OVERLAY_CSS = "background: rgba(0, 0, 0, 0.55);"

# Menu popup: same round-corner standard (radius 12, border lighter than bg)
_MONOS_MENU_BG = "#1c1c1f"
_MONOS_MENU_RADIUS = 12
_MONOS_MENU_BORDER = "#3f3f46"


def clear_stuck_widget_hover(widget: QWidget | None) -> None:
    """Clear Qt :hover stuck after popup opens/closes (badge still looks hovered until mouse re-enters)."""
    if widget is None:
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))
    try:
        st = widget.style()
        if st:
            st.unpolish(widget)
            st.polish(widget)
    except Exception:
        pass
    widget.update()


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


def monos_modal_parent(parent: QWidget | None = None) -> QWidget | None:
    """Best parent for a nested MonosDialog — active modal, else outermost QDialog ancestor."""
    modal = QApplication.activeModalWidget()
    if isinstance(modal, QDialog):
        return modal
    if parent is None:
        return None
    outermost: QWidget | None = None
    w: QWidget | None = parent if isinstance(parent, QWidget) else None
    while w is not None:
        if isinstance(w, QDialog):
            outermost = w
        w = w.parentWidget()
    return outermost or parent


class MonosDialog(QDialog):
    """
    Base dialog for MONOS: borderless window, rounded corners, border,
    and a 15% white overlay behind the dialog when shown.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._overlay: QWidget | None = None
        self._overlay_host: QWidget | None = None
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

    def _resolve_overlay_host(self) -> QWidget | None:
        """Dim layer target: outermost parent QDialog (nested modals), else top-level host."""
        outermost: QWidget | None = None
        w: QWidget | None = self.parentWidget()
        while isinstance(w, QWidget):
            if isinstance(w, QDialog):
                outermost = w
            w = w.parentWidget()
        if outermost is not None:
            return outermost
        w = self.parentWidget()
        while isinstance(w, QWidget) and w.parentWidget() is not None:
            w = w.parentWidget()
        return w if isinstance(w, QWidget) else None

    def _sync_overlay_geometry(self) -> None:
        host = self._overlay_host
        if self._overlay is None or host is None:
            return
        self._overlay.setGeometry(0, 0, host.width(), host.height())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._overlay_host and event.type() == QEvent.Type.Resize:
            self._sync_overlay_geometry()
        return super().eventFilter(watched, event)

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
        host = self._resolve_overlay_host()
        self._overlay_host = host
        if host is not None:
            if self._overlay is None:
                self._overlay = QWidget(host)
                self._overlay.setStyleSheet(_MONOS_DIALOG_OVERLAY_CSS)
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            host.installEventFilter(self)
            self._sync_overlay_geometry()
            self._overlay.show()
            self._overlay.raise_()
        self.raise_()
        self.activateWindow()

    def _hide_overlay(self) -> None:
        if self._overlay_host is not None:
            self._overlay_host.removeEventFilter(self)
            self._overlay_host = None
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None


MONOS_COLORS: dict[str, str] = {
    # Base / layering
    "app_bg": "#09090b",  # Zinc-950
    "panel": "#18181b",  # Zinc-900
    "chrome_bg": "#181a1d",  # sidebar / inspector shell
    "topbar_bg": "#181a1d",  # top bar + app footer (matches chrome_bg)
    "surface": "#27272a",  # Zinc-800
    "content_bg": "#151618",  # main view / asset browser surface
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
    "card_bg": "#191b1e",
    "card_hover": "#1d1f23",
    # Segmented pills (scope, inspector tabs, grid/list, settings tier-3)
    "pill_container_bg": "#1c1e22",
    "pill_segment_active_bg": "#2563eb",
    "pill_segment_active_hover_bg": "#3b82f6",
    "pill_segment_inactive_fg": "#71717a",
    "pill_segment_active_fg": "#fafafa",
    "pill_segment_hover_fg": "#d4d4d8",
    "pill_segment_outer_radius_px": 8,
    "pill_segment_join_radius_px": 2,
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

# Sidebar filter tree list (flat rows, no gradient).
SIDEBAR_DEPT_LIST_STYLE: dict[str, object] = {
    "section_row_height_px": 20,
    "spacer_row_height_px": 4,
    "dept_row_height_px": 32,
    "list_row_spacing_px": 3,
    "row_font_size_px": 10,
    "count_font_size_px": 9,
    "row_icon_size_px": 12,
    "indent_step_px": 16,
    "section_font_size_px": 9,
    "section_title_color_key": "text_meta",
    "max_list_height_px": 280,
    "list_container_bg": "#1c1e22",
    "list_container_border": "rgba(255, 255, 255, 0.06)",
    "row_highlight_inset_x": 4,
    "row_highlight_inset_y": 0,
    "row_highlight_radius_px": 6,
    "row_content_pad_left_px": 12,
    "row_content_pad_right_px": 12,
    "row_lead_dot_radius_px": 3,
    "row_lead_dot_gap_px": 6,
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
            background-color: #151618;
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

        /* View options — status filter dropdown (multi-select, stays open) */
        QMenu#FilterStatusMenu::item {
            padding: 6px 28px 6px 20px;
            border-radius: 6px;
            margin: 1px 6px;
            color: #c4c4c8;
            font-size: 12px;
            font-weight: 500;
        }
        QMenu#FilterStatusMenu::item:selected {
            background-color: rgba(255, 255, 255, 0.06);
            color: #d4d4d8;
        }
        QMenu#FilterStatusMenu::item:checked {
            background-color: rgba(37, 99, 235, 0.22);
            color: #d4d4d8;
        }
        QMenu#FilterStatusMenu::item:checked:selected {
            background-color: rgba(37, 99, 235, 0.36);
            color: #e4e4e7;
        }
        QMenu#FilterStatusMenu::indicator:checked {
            image: none;
            background-color: #3b82f6;
            border: 1px solid #60a5fa;
            border-radius: 3px;
        }
        QMenu#FilterStatusMenu::indicator:unchecked {
            background-color: transparent;
            border: 1px solid #52525b;
            border-radius: 3px;
        }
        QMenu#FilterStatusMenu::indicator:unchecked:selected {
            border-color: #71717a;
        }

        /* Production status picker: category section headers + tooltips */
        QMenu#ProductionStatusMenu::item {
            padding: 6px 28px 6px 20px;
        }
        QLabel#ProductionStatusMenuSectionLabel {
            font-family: "Inter", "Inter UI", "Segoe UI", "San Francisco", sans-serif;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.14em;
            color: #71717a;
            background: transparent;
        }
        QWidget#ProductionStatusMenuSection_default {
            background-color: rgba(113, 113, 122, 0.14);
            border-top: none;
            border-bottom: none;
        }
        QWidget#ProductionStatusMenuSection_blocked {
            background-color: rgba(239, 68, 68, 0.14);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }
        QWidget#ProductionStatusMenuSection_hold {
            background-color: rgba(251, 191, 36, 0.12);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }
        QWidget#ProductionStatusMenuSection_review {
            background-color: rgba(96, 165, 250, 0.12);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }
        QWidget#ProductionStatusMenuSection_in_progress {
            background-color: rgba(245, 158, 11, 0.12);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }
        QWidget#ProductionStatusMenuSection_not_started {
            background-color: rgba(113, 113, 122, 0.16);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }
        QWidget#ProductionStatusMenuSection_done {
            background-color: rgba(16, 185, 129, 0.12);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }
        QWidget#ProductionStatusMenuSection_na {
            background-color: rgba(82, 82, 91, 0.22);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }
        QWidget#ProductionStatusMenuSection_other {
            background-color: rgba(161, 161, 170, 0.12);
            border-top: 1px solid rgba(255, 255, 255, 0.07);
        }

        QWidget#TopBar {
            background-color: #181a1d;
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QWidget#TopBarPanelGroup {
            background-color: rgba(39, 39, 42, 0.85);
            border-radius: 6px;
        }
        QWidget#TopBarPanelGroup[autoMode="true"] {
            background-color: rgba(39, 39, 42, 0.45);
            border-radius: 4px;
        }
        QToolButton#TopBarPanelAutoBtn {
            border: none;
            border-radius: 4px;
            padding: 0 4px;
            margin: 0;
            background: transparent;
            color: #a1a1aa;
            font-family: "Inter", "Inter UI", "Segoe UI", "San Francisco", sans-serif;
            font-size: 8px;
            font-weight: 600;
        }
        QToolButton#TopBarPanelAutoBtn:hover {
            background: rgba(255, 255, 255, 0.06);
            color: #e4e4e7;
        }
        QToolButton#TopBarPanelAutoBtn[active="true"] {
            background: rgba(37, 99, 235, 0.18);
            color: #fafafa;
        }
        QWidget#TopBarPanelGroup[autoMode="true"] QToolButton#TopBarPanelAutoBtn {
            color: rgba(161, 161, 170, 0.65);
            font-size: 7px;
            padding: 0 3px;
        }
        QWidget#TopBarPanelGroup[autoMode="true"] QToolButton#TopBarPanelGlyphBtn {
            background: transparent;
        }
        QWidget#TopBarPanelGroup[autoMode="true"] QToolButton#TopBarPanelAutoBtn[active="true"] {
            background: rgba(37, 99, 235, 0.12);
            color: rgba(250, 250, 250, 0.85);
        }
        QToolButton#TopBarPanelGlyphBtn {
            border: none;
            border-radius: 4px;
            margin: 0;
            background: transparent;
        }
        QToolButton#TopBarPanelGlyphBtn:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QToolButton#TopBarPanelGlyphBtn:checked {
            background: rgba(37, 99, 235, 0.12);
        }
        QToolButton#TopBarPanelGlyphBtn:disabled {
            background: transparent;
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
        QToolButton#TopBarUpdateBtn,
        QToolButton#TopBarWatcherBtn,
        QToolButton#TopBarAlwaysOnTopBtn,
        QToolButton#TopBarSettingsBtn,
        QToolButton#TopBarUserBtn {
            border: none;
            border-radius: 0;
            background: transparent;
            color: #d4d4d8;
            padding: 0px;
            margin: 0px;
        }
        QToolButton#WindowMinBtn:hover,
        QToolButton#WindowMaxBtn:hover,
        QToolButton#TopBarNotiBtn:hover,
        QToolButton#TopBarUpdateBtn:hover,
        QToolButton#TopBarWatcherBtn:hover,
        QToolButton#TopBarAlwaysOnTopBtn:hover,
        QToolButton#TopBarSettingsBtn:hover {
            background: rgba(255, 255, 255, 0.08);
            color: #e4e4e7;
            border-radius: 8px;
        }
        /* Avatar hover/press drawn circular in _UserAvatarButton.paintEvent */
        QToolButton#TopBarUserBtn:hover,
        QToolButton#TopBarUserBtn:pressed {
            background: transparent;
            border-radius: 0px;
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
            background: #151618;
            solid #27272a;

        }
        QTableView#MainViewList {
            background: #151618;
            solid #27272a;

        }
        QTableView#MainViewList::item {
            background: #191b1e;
            color: #a1a1aa;
        }
        QTableView#MainViewList::item:hover {
            background: #1d1f23;
            color: #fafafa;
        }
        QTableView#MainViewList::item:selected {
            background: rgba(59, 130, 246, 0.10);
            color: #60a5fa;
        }

        /* --- Inbox tree pane: file tree (modern flat look, full-row selection) --- */
        QTreeView#InboxSplitTree {
            background-color: #151618;
            border: none;
            outline: none;
            color: #a1a1aa;
            font-size: 13px;
            padding: 0;
            selection-background-color: transparent;
            selection-color: #60a5fa;
            show-decoration-selected: 0;
        }
        QTreeView#InboxSplitTree::item {
            padding: 0;
            border: none;
            border-radius: 0;
            margin: 0;
            background: transparent;
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

        /* --- Inbox mapping list (flat rows + dividers like MainViewList) --- */
        QListWidget#InboxMappingList {
            background-color: #151618;
            border: none;
            outline: none;
            color: #a1a1aa;
            font-size: 13px;
            padding: 0;
        }
        QListWidget#InboxMappingList::item {
            padding: 0;
            margin: 0;
            border: none;
            border-radius: 0;
            background: transparent;
        }
        QListWidget#InboxMappingList::item:hover {
            background: transparent;
            color: #fafafa;
        }
        QListWidget#InboxMappingList::item:selected {
            background: transparent;
            color: #60a5fa;
        }

        /* --- Inbox / Outbox page UX --- */
        QWidget#InboxContentToolbar,
        QWidget#InboxTreeToolbar {
            background-color: #151618;
            border-bottom: 1px solid rgba(39, 39, 42, 0.55);
        }
        QWidget#InboxPathBarToolbar {
            background: transparent;
            border: none;
        }
        QLabel#InboxSectionTitle {
            color: #71717a;
            font-family: "Inter";
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.08em;
        }
        QLabel#InboxCountChip {
            background: rgba(63, 63, 70, 0.55);
            color: #d4d4d8;
            border-radius: 10px;
            padding: 2px 8px;
            font-family: "Inter";
            font-size: 11px;
            font-weight: 600;
        }
        QLabel#InboxToolbarHint,
        QLabel#InboxSelectionHintText {
            color: #71717a;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 500;
        }
        QWidget#InboxSelectionHintBar {
            background-color: rgba(37, 99, 235, 0.08);
            border-top: 1px solid rgba(59, 130, 246, 0.18);
        }
        QWidget#InboxBrowseBar {
            background: transparent;
        }
        QWidget#InboxPathBarRow {
            background: transparent;
            border-bottom: 1px solid rgba(39, 39, 42, 0.55);
            padding-bottom: 6px;
        }
        QFrame#InboxExplorerPathField {
            background: rgba(18, 18, 20, 0.92);
            border: 1px solid rgba(63, 63, 70, 0.75);
            border-radius: 5px;
            min-height: 0px;
            max-height: 24px;
        }
        QLineEdit#InboxExplorerPathEdit {
            background: transparent;
            border: none;
            color: #e4e4e7;
            font-family: "JetBrains Mono";
            font-size: 11px;
            padding: 0px 2px;
            selection-background-color: rgba(59, 130, 246, 0.35);
        }
        QToolButton#InboxBrowseOverflowBtn {
            color: #a1a1aa;
            border: none;
            background: transparent;
            padding: 0 4px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 600;
        }
        QToolButton#InboxBrowseOverflowBtn:hover {
            color: #60a5fa;
            background: rgba(63, 63, 70, 0.45);
            border-radius: 4px;
        }
        QToolButton#InboxBrowseOverflowBtn::menu-indicator {
            image: none;
            width: 0px;
        }
        QWidget#InboxBrowseCrumbHost {
            background: transparent;
        }
        QToolButton#InboxBrowseNavButton {
            border: 1px solid rgba(63, 63, 70, 0.55);
            border-radius: 5px;
            background: rgba(24, 24, 27, 0.65);
            padding: 0px;
            margin: 0px;
        }
        QToolButton#InboxBrowseNavButton:hover:enabled {
            background: rgba(39, 39, 42, 0.85);
            border-color: rgba(82, 82, 91, 0.75);
        }
        QToolButton#InboxBrowseNavButton:disabled {
            opacity: 0.35;
        }
        QLabel#InboxBrowseCrumbSep {
            color: #52525b;
            padding: 0 2px;
        }
        QPushButton#InboxBrowseCrumbLink {
            color: #a1a1aa;
            border: none;
            background: transparent;
            padding: 0px 4px;
            min-height: 0px;
            max-height: 22px;
            font-family: "Inter";
            font-size: 11px;
            font-weight: 500;
        }
        QPushButton#InboxBrowseCrumbLink:hover {
            color: #60a5fa;
        }
        QLabel#InboxBrowseCrumbCurrent {
            color: #e4e4e7;
            padding: 0px 4px;
            min-height: 0px;
            max-height: 22px;
            font-family: "Inter";
            font-size: 11px;
            font-weight: 600;
        }
        QWidget#ExplorerDropZone {
            background-color: transparent;
            border: none;
            border-radius: 8px;
        }
        QWidget#ExplorerDropZone[dropHighlight="true"] {
            background-color: rgba(39, 58, 90, 0.16);
            border: 1px dashed #3b82f6;
            border-radius: 8px;
        }
        QWidget#InboxEmptyState,
        QWidget#InboxTreeEmptyOverlay {
            background-color: #151618;
        }
        QLabel#InboxEmptyStateTitle {
            color: #e4e4e7;
        }
        QLabel#InboxEmptyStateSubtitle {
            color: #71717a;
        }
        QPushButton#InboxHeaderButton {
            background: rgba(39, 39, 42, 0.65);
            color: #d4d4d8;
            border: 1px solid rgba(63, 63, 70, 0.8);
            border-radius: 8px;
            padding: 6px 12px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#InboxHeaderButton:hover {
            background: rgba(63, 63, 70, 0.75);
            color: #fafafa;
        }
        QPushButton#InboxToolbarButton {
            background: transparent;
            color: #a1a1aa;
            border: none;
            border-radius: 6px;
            padding: 4px 10px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#InboxToolbarButton:hover {
            background: rgba(255, 255, 255, 0.06);
            color: #fafafa;
        }
        QPushButton#InboxPrimaryButton {
            background-color: #2563eb;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 6px 12px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#InboxPrimaryButton:hover {
            background-color: #3b82f6;
        }
        QPushButton#InboxPrimaryButton:pressed {
            background-color: #1d4ed8;
        }

        /* --- MONOS Deep Dark Table (Pipeline: Departments, Types mapping) --- */
        QTableWidget, QTableView {
            background-color: #151618;
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
            background-color: #171819;
            border-top: 1px solid rgba(39, 39, 42, 0.50);
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QLabel#MainViewContextTitle {
            color: #cccccc;
            font-weight: 700;
        }
        QPushButton#MainViewBreadcrumbLink {
            border: none;
            background: transparent;
            color: #cccccc;
            font-weight: 700;
            padding: 0 2px 0 0;
        }
        QPushButton#MainViewBreadcrumbLink:hover {
            color: #60a5fa;
        }
        QPushButton#MainViewBreadcrumbBadgeLink {
            background: rgba(63, 63, 70, 0.45);
            border: none;
            border-radius: 6px;
            color: #cccccc;
            font-weight: 700;
            padding: 4px 10px 4px 8px;
        }
        QPushButton#MainViewBreadcrumbBadgeLink:hover {
            background: rgba(63, 63, 70, 0.65);
            color: #fafafa;
        }
        QWidget#MainViewBreadcrumbRoot[navLink="true"]:hover QLabel#MainViewContextTitle {
            color: #60a5fa;
        }
        QWidget#MainViewTypeBadge[navLink="true"]:hover {
            background: rgba(82, 82, 91, 0.78);
        }
        QWidget#MainViewTypeBadge[badgeKind="asset"][navLink="true"]:hover {
            background: rgba(5, 150, 105, 0.98);
        }
        QWidget#MainViewTypeBadge[badgeKind="shot"][navLink="true"]:hover {
            background: rgba(217, 119, 6, 0.98);
        }
        QLabel#MainViewBreadcrumbCurrent {
            color: #a1a1aa;
            font-weight: 600;
        }
        QWidget#MainViewBreadcrumbCurrentChip {
            background: transparent;
        }
        QLabel#MainViewTitleChevron {
            background: transparent;
        }
        QWidget#MainViewTypeBadge {
            background: rgba(63, 63, 70, 0.45);
            border: none;
            border-radius: 6px;
        }
        QWidget#MainViewTypeBadge[badgeKind="asset"] {
            background: rgba(16, 185, 129, 0.86);
        }
        QWidget#MainViewTypeBadge[badgeKind="shot"] {
            background: rgba(245, 158, 11, 0.86);
        }
        QWidget#MainViewTypeBadge[badgeKind="client"] {
            background: rgba(59, 130, 246, 0.86);
        }
        QWidget#MainViewTypeBadge[badgeKind="freelancer"] {
            background: rgba(168, 85, 247, 0.86);
        }
        QLabel#MainViewTypeBadgeLabel {
            color: #e4e4e7;
            font-weight: 700;
        }
        QWidget#MainViewTypeBadge[badgeKind="asset"] QLabel#MainViewTypeBadgeLabel,
        QWidget#MainViewTypeBadge[badgeKind="shot"] QLabel#MainViewTypeBadgeLabel,
        QWidget#MainViewTypeBadge[badgeKind="client"] QLabel#MainViewTypeBadgeLabel,
        QWidget#MainViewTypeBadge[badgeKind="freelancer"] QLabel#MainViewTypeBadgeLabel {
            color: #ffffff;
        }
        QWidget#MainViewTypeBadge[badgeKind="client"][navLink="true"]:hover {
            background: rgba(37, 99, 235, 0.95);
        }
        QWidget#MainViewTypeBadge[badgeKind="freelancer"][navLink="true"]:hover {
            background: rgba(147, 51, 234, 0.95);
        }
        QWidget#MainViewDepartmentBadge {
            background: rgba(59, 130, 246, 0.86);
            border: none;
            border-radius: 6px;
        }
        QWidget#MainViewDepartmentBadge[navLink="true"]:hover {
            background: rgba(37, 99, 235, 0.95);
        }
        QLabel#MainViewDepartmentBadgeLabel,
        QWidget#MainViewDepartmentBadge QLabel#MainViewTypeBadgeLabel {
            color: #ffffff;
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
        QFrame#TrayMiniPopup {
            background-color: #18181b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
        }
        QLabel#TrayMiniPopupHeader {
            color: #71717a;
            padding: 8px 12px 4px 12px;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.08em;
        }
        QLabel#TrayMiniPopupEmpty {
            color: #71717a;
            padding: 12px;
            font-size: 12px;
        }
        QListWidget#TrayMiniPopupList {
            background: transparent;
            border: none;
            outline: none;
            padding: 2px 4px;
        }
        QListWidget#TrayMiniPopupList::item {
            padding: 2px 4px;
            border-radius: 4px;
            color: #e4e4e7;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 13px;
            font-weight: 500;
        }
        QListWidget#TrayMiniPopupList::item:hover {
            background-color: rgba(255, 255, 255, 0.06);
        }
        QListWidget#TrayMiniPopupList::item:selected {
            background-color: rgba(37, 99, 235, 0.22);
            color: #fafafa;
        }
        QLineEdit#CommandPaletteSearch {
            background-color: #1c1c1f;
            border: 1px solid #3f3f46;
            border-radius: 10px;
            color: #fafafa;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 15px;
            font-weight: 500;
            padding: 10px 14px;
            min-height: 22px;
        }
        QLineEdit#CommandPaletteSearch:focus {
            border-color: #52525b;
        }
        QLabel#CommandPaletteEmpty {
            color: #71717a;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 13px;
            font-weight: 500;
            padding: 8px 4px 4px 4px;
        }
        QListWidget#CommandPaletteList {
            background: transparent;
            border: none;
            outline: none;
            padding: 0;
        }
        QListWidget#CommandPaletteList::item {
            background: transparent;
            border: none;
            padding: 0;
            margin: 0;
        }
        QListWidget#CommandPaletteList::item:selected {
            background: transparent;
        }
        QPushButton#TrayMiniPopupOpenButton {
            background: transparent;
            border: none;
            border-top: 1px solid #3f3f46;
            color: #60a5fa;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 12px;
            font-weight: 600;
            padding: 0 12px;
            text-align: left;
        }
        QPushButton#TrayMiniPopupOpenButton:hover {
            background-color: rgba(37, 99, 235, 0.12);
            color: #93c5fd;
        }
        QPushButton#TrayMiniFilterPill {
            background-color: #27272a;
            border: 1px solid #3f3f46;
            border-radius: 8px;
            color: #d4d4d8;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            text-align: left;
        }
        QPushButton#TrayMiniFilterPill:hover {
            background-color: #3f3f46;
            color: #fafafa;
        }
        QWidget#TrayMiniListRow {
            background: transparent;
        }
        QLabel#TrayMiniThumb,
        QFrame#TrayMiniThumb {
            background-color: #27272a;
            border: 1px solid #3f3f46;
            border-radius: 4px;
            min-width: 72px;
            max-width: 72px;
            min-height: 41px;
            max-height: 41px;
        }
        QLabel#TrayMiniRowTitle {
            color: #fafafa;
        }
        QLabel#TrayMiniRowSub {
            color: #71717a;
        }
        QWidget#TrayMiniNotiRow[unread="true"] {
            background: rgba(59, 130, 246, 0.08);
            border-radius: 6px;
        }
        QLabel#TrayMiniNotiAvatar {
            background: transparent;
            border: none;
        }
        QFrame#MainViewOptionsPopup {
            background-color: #1c1c1f;
            border: 1px solid #2a2a2d;
            border-radius: 8px;
        }
        QFrame#MainViewOptionsPopup QSlider {
            margin: 0px;
            padding: 0px;
        }
        QFrame#MainViewOptionsPopup QCheckBox,
        QFrame#MainViewOptionsPopup QRadioButton {
            padding: 0px;
            margin: 0px;
            min-height: 18px;
            max-height: 18px;
            spacing: 6px;
            color: #c4c4c8;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 12px;
            font-weight: 500;
        }
        QFrame#MainViewOptionsPopup QCheckBox:hover,
        QFrame#MainViewOptionsPopup QRadioButton:hover {
            color: #d4d4d8;
        }
        QFrame#MainViewOptionsPopup QCheckBox::indicator,
        QFrame#MainViewOptionsPopup QRadioButton::indicator {
            width: 14px;
            height: 14px;
        }
        QFrame#MainViewOptionsPopup QLabel#ViewOptionsSectionLabel,
        QFrame#MainViewOptionsPopup QLabel#ViewOptionsGroupLabel,
        QFrame#MainViewOptionsPopup QLabel#MainViewOptionsSizeLabel {
            margin: 0px;
            padding: 0px;
            min-height: 18px;
            max-height: 18px;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
        }
        /* Block headers: Assets, Shots, Card size */
        QFrame#MainViewOptionsPopup QLabel#ViewOptionsSectionLabel,
        QFrame#MainViewOptionsPopup QLabel#MainViewOptionsSizeLabel {
            color: #71717a;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.06em;
        }
        /* In-submenu field labels: Work folder, Sort by, Production status */
        QFrame#MainViewOptionsPopup QLabel#ViewOptionsGroupLabel {
            color: #52525b;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 0.04em;
        }
        /* Collapsible rows: Filter, Sort, Metadata */
        QToolButton#ViewOptionsSubmenuHeader {
            color: #8b9cb3;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 11px;
            font-weight: 600;
            border: none;
            background: transparent;
            min-height: 18px;
            max-height: 18px;
            padding: 0px;
            margin: 0px;
        }
        QToolButton#ViewOptionsSubmenuHeader:hover {
            color: #a8b8cc;
        }
        QToolButton#ViewOptionsFilterStatusButton {
            color: #c4c4c8;
            background-color: #27272a;
            border: 1px solid #3f3f46;
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 18px;
            max-height: 18px;
            font-family: "Inter", "Inter UI", "Segoe UI", sans-serif;
            font-size: 12px;
            font-weight: 500;
            text-align: left;
        }
        QToolButton#ViewOptionsFilterStatusButton:hover {
            color: #d4d4d8;
            background-color: #3f3f46;
            border-color: #52525b;
        }
        QToolButton#ViewOptionsFilterStatusButton::menu-indicator {
            subcontrol-origin: padding;
            subcontrol-position: center right;
            width: 12px;
            height: 12px;
            right: 6px;
        }
        QToolButton#MainViewOptionsButton {
            padding: 6px;
            border: none;
            background: transparent;
        }
        QToolButton#MainViewOptionsButton:hover {
            background: rgba(255, 255, 255, 0.08);
            border-radius: 6px;
        }

        QFrame#ToolbarIconSeparator {
            background-color: rgba(63, 63, 70, 0.85);
            border: none;
            max-width: 1px;
            min-width: 1px;
        }

        QWidget#ScheduleToolGroup {
            background-color: rgba(39, 39, 42, 0.85);
            border-radius: 6px;
        }
        QToolButton#ScheduleToolBtn {
            border: none;
            border-radius: 6px;
            padding: 6px;
            margin: 0;
            background: transparent;
        }
        QToolButton#ScheduleToolBtn:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QToolButton#ScheduleToolBtn:checked {
            background: rgba(37, 99, 235, 0.18);
        }
        QToolButton#ScheduleHeaderActionBtn {
            border: none;
            border-radius: 6px;
            padding: 6px;
            background: transparent;
        }
        QToolButton#ScheduleHeaderActionBtn:hover {
            background: rgba(255, 255, 255, 0.08);
        }

        /* Schedule timeline corner: search field + filter button */
        QWidget#ScheduleCornerSearch {
            background-color: #0d0d0f;
            border-right: 1px solid #2a2a2c;
            border-bottom: 1px solid #2a2a2c;
        }
        QLineEdit#ScheduleSearchEdit {
            background-color: #18181b;
            border: 1px solid #2a2a2c;
            border-radius: 8px;
            padding: 4px 8px;
            color: #e4e4e7;
            font-family: "Inter";
            font-size: 12px;
            selection-background-color: rgba(37, 99, 235, 0.35);
        }
        QLineEdit#ScheduleSearchEdit:focus {
            border: 1px solid rgba(96, 165, 250, 0.55);
        }
        QToolButton#ScheduleSearchFilterBtn {
            border: none;
            border-radius: 6px;
            padding: 4px;
            background: transparent;
        }
        QToolButton#ScheduleSearchFilterBtn:hover {
            background: rgba(255, 255, 255, 0.08);
        }

        /* --- Inspector --- */
        QWidget#InspectorPanel {
            background-color: #181a1d;
        }
        QScrollArea#InspectorScrollArea {
            background: transparent;
        }
        QScrollArea#InspectorScrollArea QWidget {
            background: transparent;
        }
        QScrollArea#InspectorScrollArea QWidget#qt_scrollarea_viewport {
            background-color: #181a1d;
        }
        QStackedWidget#InspectorBodyStack {
            background-color: #181a1d;
        }
        QScrollArea#InspectorRefScrollArea,
        QScrollArea#InspectorDetailsScrollArea {
            background: transparent;
            border: none;
        }
        QScrollArea#InspectorRefScrollArea QWidget,
        QScrollArea#InspectorDetailsScrollArea QWidget {
            background: transparent;
        }
        QScrollArea#InspectorRefScrollArea QWidget#InspectorRefSectionContainer {
            background-color: #1f1f22;
            border: 1px solid rgba(39, 39, 42, 0.65);
            border-radius: 8px;
        }
        QScrollArea#InspectorRefScrollArea QWidget#InspectorRefSectionContainer[sectionHover="true"] {
            background-color: #27272a;
            border: 1px solid rgba(63, 63, 70, 0.80);
        }
        QScrollArea#InspectorRefScrollArea QWidget#InspectorRefSectionContainer[dropHighlight="true"] {
            background-color: #27272a;
            border: 1px dashed #3b82f6;
        }
        QScrollArea#InspectorRefScrollArea QWidget#qt_scrollarea_viewport,
        QScrollArea#InspectorDetailsScrollArea QWidget#qt_scrollarea_viewport {
            background-color: #181a1d;
        }
        QWidget#InspectorContent,
        QWidget#InspectorRefContent,
        QWidget#InspectorDetailsContent {
            background: transparent;
        }
        QWidget#InspectorRefTab {
            background-color: #181a1d;
        }
        QWidget#InspectorRefSectionContainer {
            background-color: #1f1f22;
            border: 1px solid rgba(39, 39, 42, 0.65);
            border-radius: 8px;
        }
        QWidget#InspectorRefSectionContainer[sectionHover="true"] {
            background-color: #27272a;
            border: 1px solid rgba(63, 63, 70, 0.80);
        }
        QWidget#InspectorRefSectionContainer[dropHighlight="true"] {
            background-color: #27272a;
            border: 1px dashed #3b82f6;
        }
        QWidget#InspectorRefSection {
            background: transparent;
            border: none;
        }
        QListView#InspectorRefGrid {
            background: transparent;
            border: none;
            outline: none;
        }
        QListView#InspectorRefGrid::item {
            background: transparent;
            border: none;
            padding: 0px;
        }
        QListView#InspectorRefGrid::item:selected,
        QListView#InspectorRefGrid::item:hover {
            background: transparent;
            border: none;
        }
        QWidget#InspectorHeader {
            background-color: #181a1d;
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QWidget#InspectorHeader[tabMode="true"] {
            background-color: #181a1d;
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
        }
        QLabel#InspectorHeaderTitle {
            color: #71717a;
            padding: 10px 0px;
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
        QToolButton#InspectorItemNotesBadgeButton {
            background: transparent;
            border: none;
            border-radius: 8px;
            padding: 4px;
        }
        QWidget#InspectorPanel QToolButton#InspectorItemNotesBadgeButton:hover {
            background: rgba(255, 255, 255, 0.10);
            border: none;
        }
        QLabel#InspectorItemNotesBadgeCount {
            font-family: "Inter";
            font-size: 9px;
            font-weight: 800;
            color: #fafafa;
            background-color: #2563eb;
            border-radius: 8px;
            min-width: 14px;
            min-height: 14px;
            padding: 0 3px;
        }
        QFrame#InspectorMiniCard,
        QFrame#InspectorDeptCard {
            background: #151618;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 12px;
        }
        QFrame#InspectorDeptCard:hover {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(63, 63, 70, 0.80);
        }
        /* Sidebar-focused department (persistent) — yellow border */
        QFrame#InspectorDeptCard[sidebarFocused="true"] {
            border: 1px solid #fbbf24;  /* amber-400 */
            background: rgba(251, 191, 36, 0.06);
        }
        /* Temporarily focused from Inspector click — blue border overrides yellow */
        QFrame#InspectorDeptCard[focused="true"] {
            border: 1px solid rgba(59, 130, 246, 0.95); /* blue-500-ish */
            background: rgba(37, 99, 235, 0.20);        /* subtle blue fill */
        }
        QFrame#InspectorRefThumbCell {
            border: none;
            background: transparent;
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
        /* Folder icon on each department row (lighter hover) */
        QToolButton#InspectorDeptOpenButton,
        QToolButton#InspectorDeptStatusMenuButton {
            border: none;
            padding: 0 6px;
            background: transparent;
            color: #e4e4e7;
            border-radius: 999px;
        }
        /* All other tool buttons inside Inspector: ensure hover is visible */
        QWidget#InspectorPanel QToolButton:hover {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(63, 63, 70, 0.80);
            color: #e4e4e7;
        }
        /* Scope pill segments (sidebar + inspector tabs): match SidebarScopePill QSS, not generic hover */
        QWidget#InspectorPanel QToolButton#SidebarScopePillSegment:hover {
            background: rgba(255, 255, 255, 0.08);
            border: none;
            color: #d4d4d8;
        }
        QWidget#InspectorPanel QToolButton#SidebarScopePillSegment[active="true"]:hover {
            background-color: #3b82f6;
            border: none;
            color: #fafafa;
        }
        /* Dept folder / status menu: pill-style hover */
        QWidget#InspectorPanel QToolButton#InspectorDeptOpenButton:hover,
        QWidget#InspectorPanel QToolButton#InspectorDeptStatusMenuButton:hover {
            background: rgba(255, 255, 255, 0.08);
            border: none;
            border-radius: 999px;
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
        QScrollArea#ItemHealthScroll {
            background-color: #151618;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
            margin-left: 4px;
            margin-right: 4px;
        }
        QScrollArea#ItemHealthScroll::viewport {
            background-color: #151618;
            border-radius: 8px;
        }
        QScrollArea#ItemHealthScroll QWidget#ItemHealthScrollBody {
            background: transparent;
        }
        QWidget#ItemHealthDialogTitleBar {
            background-color: #151618;
            border-bottom: 1px solid rgba(39, 39, 42, 0.50);
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
        }
        QToolButton#ItemHealthDialogMoveGrip {
            border: none;
            border-radius: 6px;
            padding: 4px;
            background: transparent;
        }
        QToolButton#ItemHealthDialogMoveGrip:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QToolButton#ItemHealthDialogSizeGrip {
            border: none;
            border-radius: 6px;
            padding: 4px;
            background: transparent;
        }
        QToolButton#ItemHealthDialogSizeGrip:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QScrollArea#ItemNotesScroll {
            background-color: #151618;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
        }
        QScrollArea#ItemNotesScroll::viewport {
            background-color: #151618;
            border-radius: 8px;
        }
        QSplitter#ItemNotesSplit::handle {
            background: rgba(63, 63, 70, 0.55);
            width: 1px;
            margin: 0 4px;
        }
        QWidget#ItemNotesComposePanel,
        QWidget#ItemNotesListPanel {
            background: transparent;
        }
        QWidget#ItemNotesListHost {
            background: transparent;
        }
        QFrame#ItemNotesCard {
            background: #18181b;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 8px;
        }
        QFrame#ItemNotesCard:hover {
            background: #1c1c20;
            border: 1px solid rgba(63, 63, 70, 0.72);
        }
        QFrame#ItemNotesCardDone {
            background: rgba(16, 185, 129, 0.16);
            border: 1px solid rgba(52, 211, 153, 0.42);
            border-radius: 8px;
        }
        QFrame#ItemNotesCardDone:hover {
            background: rgba(16, 185, 129, 0.22);
            border: 1px solid rgba(52, 211, 153, 0.55);
        }
        /* User profile (@mention click) — single MonosDialog border only */
        QLabel#UserProfileViewName {
            color: #fafafa;
            background: transparent;
        }
        QLabel#UserProfileViewSubtitle {
            color: #a1a1aa;
            background: transparent;
        }
        QLabel#UserProfileViewAvatar {
            background: transparent;
        }
        QLabel#UserProfileViewAvatar[avatarSize="96"] {
            border-radius: 48px;
        }
        QLabel#UserProfileViewAvatar[avatarSize="64"] {
            border-radius: 32px;
        }
        QLabel#UserProfileViewAvatar[clickable="true"]:hover {
            outline: 2px solid rgba(96, 165, 250, 0.55);
        }
        QPushButton#UserProfileActionBtn {
            background: transparent;
            border: 1px solid #52525b;
            border-radius: 20px;
            padding: 0px;
        }
        QPushButton#UserProfileActionBtn:hover:enabled {
            border-color: #a1a1aa;
            background: rgba(255, 255, 255, 0.06);
        }
        QPushButton#UserProfileActionBtn:disabled {
            border-color: #3f3f46;
            opacity: 0.35;
        }
        QFrame#UserProfileActionDivider {
            background: #3f3f46;
            border: none;
            max-width: 1px;
            min-width: 1px;
        }
        QDialog#UserProfileViewDialog QPushButton#UserProfileCloseBtn {
            background: rgba(239, 68, 68, 0.12);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.45);
            border-radius: 8px;
            padding: 8px 16px;
            font-family: "Inter";
            font-size: 13px;
            font-weight: 600;
        }
        QDialog#UserProfileViewDialog QPushButton#UserProfileCloseBtn:hover {
            background: rgba(239, 68, 68, 0.22);
            border-color: #ef4444;
            color: #fca5a5;
        }
        QLabel#ItemNotesPreviewLabel {
            background: transparent;
            border: none;
        }
        QLabel#ItemNotesMeta {
            color: #a1a1aa;
            font-size: 11px;
            font-weight: 600;
        }
        QLabel#NoteAuthorAvatar {
            background: transparent;
        }
        QLabel#NoteAuthorNameLink {
            color: #93c5fd;
            background: transparent;
            padding: 0 2px;
        }
        QLabel#NoteAuthorNameLink:hover {
            color: #bfdbfe;
            text-decoration: underline;
        }
        QLabel#ItemNotesMetaTime {
            color: #71717a;
            background: transparent;
        }
        QLabel#NoteSeenByLabel {
            color: #71717a;
            font-family: "Inter";
            font-size: 11px;
            font-weight: 500;
            background: transparent;
        }
        QWidget#NoteSeenByRow {
            background: transparent;
        }
        QWidget#NoteSeenByAvatarStack {
            background: transparent;
        }
        QLabel#NoteSeenByAvatar {
            background: transparent;
            padding: 0px;
            border: none;
        }
        QLabel#NoteSeenByAvatar:hover {
            background: rgba(96, 165, 250, 0.14);
            border-radius: 10px;
        }
        QLabel#NoteSeenByMore {
            color: #a1a1aa;
            background: rgba(63, 63, 70, 0.55);
            border: 1px solid rgba(63, 63, 70, 0.9);
            border-radius: 10px;
        }
        QWidget#NoteAuthorRow {
            background: transparent;
        }

        /* @mention autocomplete (note compose) */
        QFrame#NoteMentionPopup {
            background-color: #18181b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
        }
        QListWidget#NoteMentionList {
            background: transparent;
            border: none;
            outline: none;
            padding: 2px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 500;
            color: #d4d4d8;
        }
        QListWidget#NoteMentionList::item {
            height: 30px;
            padding: 0px 4px;
            border-radius: 6px;
        }
        QListWidget#NoteMentionList::item:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QListWidget#NoteMentionList::item:selected {
            background: rgba(59, 130, 246, 0.14);
            border: 1px solid rgba(59, 130, 246, 0.35);
        }
        QListWidget#NoteMentionList::item:selected:focus {
            background: rgba(59, 130, 246, 0.18);
            border: 1px solid rgba(59, 130, 246, 0.45);
        }

        /* Assignee picker (Inspector schedule) — match InspectorScheduleSubCard (#25282c) */
        QFrame#AssigneePickerPopup {
            background-color: #25282c;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
        }
        QFrame#AssigneePickerPopupFooter {
            background-color: #25282c;
            border: none;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
        }
        QListWidget#AssigneePickerList {
            background-color: #25282c;
            border: none;
            outline: none;
            padding: 2px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 500;
            color: #d4d4d8;
        }
        QListWidget#AssigneePickerList::viewport {
            background-color: #25282c;
        }
        QListWidget#AssigneePickerList::item {
            height: 52px;
            padding: 0px 4px;
            border-radius: 6px;
            background: transparent;
            border: none;
        }
        QListWidget#AssigneePickerList::item:hover {
            background: rgba(255, 255, 255, 0.06);
        }
        QListWidget#AssigneePickerList::item:selected {
            background: rgba(59, 130, 246, 0.12);
            border: 1px solid rgba(59, 130, 246, 0.28);
        }
        QPushButton#AssigneePickerProfileBtn {
            background: transparent;
            border: none;
            border-radius: 6px;
            padding: 0px;
        }
        QPushButton#AssigneePickerProfileBtn:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QFrame#AssigneePickerRow {
            background: #2a2d31;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
        }
        QFrame#AssigneePickerRow:hover {
            background: #2f3338;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        QFrame#AssigneePickerRow QWidget,
        QFrame#AssigneePickerRow QLabel {
            background: transparent;
            border: none;
        }
        QLabel#AssigneePickerRowAvatar {
            background: transparent;
        }
        QLabel#AssigneePickerName {
            color: #fafafa;
            background: transparent;
        }
        QLabel#AssigneePickerMeta {
            color: #a1a1aa;
            background: transparent;
        }
        QLabel#AssigneePickerChevron {
            background: transparent;
        }
        QWidget#AssigneePickerListRow,
        QWidget#AssigneePickerListRow QWidget,
        QWidget#AssigneePickerListRow QLabel {
            background: transparent;
            border: none;
        }
        QLabel#AssigneePickerListName {
            color: #fafafa;
            background: transparent;
        }
        QLabel#AssigneePickerListMeta {
            color: #a1a1aa;
            background: transparent;
        }

        /* Notification list dialog (Show all) */
        QListWidget#NotificationList {
            background: transparent;
            border: none;
            outline: none;
            padding: 0px;
        }
        QListWidget#NotificationList::viewport {
            background: transparent;
        }
        QListWidget#NotificationList::item {
            background: transparent;
            border: none;
            padding: 0px;
        }

        QLabel#NoteImageViewerLabel {
            background: transparent;
            color: #a1a1aa;
        }
        QLabel#NoteImageViewerZoomValue {
            color: #a1a1aa;
            background: transparent;
        }
        QSlider#NoteImageViewerZoomSlider {
            min-height: 20px;
        }
        QSlider#NoteImageViewerZoomSlider::groove:horizontal {
            background: #27272a;
            height: 6px;
            border-radius: 3px;
        }
        QSlider#NoteImageViewerZoomSlider::handle:horizontal {
            background: #a1a1aa;
            width: 14px;
            height: 14px;
            margin: -4px 0;
            border-radius: 7px;
        }
        QSlider#NoteImageViewerZoomSlider::handle:horizontal:hover {
            background: #fafafa;
        }
        QSlider#NoteImageViewerZoomSlider::sub-page:horizontal {
            background: rgba(59, 130, 246, 0.45);
            border-radius: 3px;
        }
        QSlider#NoteImageViewerZoomSlider::add-page:horizontal {
            background: #27272a;
            border-radius: 3px;
        }

        QLabel#ItemNotesPreviewMore {
            color: #52525b;
            background: transparent;
            padding: 0px;
        }
        QFrame#ItemNotesHRule {
            background-color: rgba(39, 39, 42, 0.80);
            border: none;
            max-height: 1px;
        }
        QLineEdit#ItemNotesLineInput {
            background-color: #151618;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 10px;
            padding: 10px 14px;
            color: #e4e4e7;
            font-family: "Inter";
            font-size: 13px;
            min-height: 20px;
        }
        QLineEdit#ItemNotesLineInput:focus {
            border: 1px solid #2563eb;
        }
        QLineEdit#ItemNotesLineInput::placeholder {
            color: #71717a;
        }
        QToolButton#ItemNotesAddPlusButton {
            background-color: #27272a;
            border: 1px solid rgba(63, 63, 70, 0.80);
            border-radius: 10px;
        }
        QToolButton#ItemNotesAddPlusButton:hover {
            background-color: #3f3f46;
            border: 1px solid rgba(82, 82, 91, 0.90);
        }
        QToolButton#ItemNotesDeleteButton {
            border: none;
            background: transparent;
            padding: 4px;
            border-radius: 8px;
        }
        QToolButton#ItemNotesDeleteButton:hover {
            background: rgba(239, 68, 68, 0.15);
        }
        QToolButton#ItemNotesOpenButton {
            border: none;
            background: transparent;
            padding: 4px;
            border-radius: 8px;
        }
        QToolButton#ItemNotesOpenButton:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QToolButton#ItemNotesDoneCheck {
            border: none;
            background: transparent;
            padding: 6px;
            border-radius: 8px;
        }
        QToolButton#ItemNotesDoneCheck:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QPushButton#ItemNotesAddButton {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid rgba(16, 185, 129, 0.70);
            background: rgba(16, 185, 129, 0.22);
            color: #fafafa;
            font-family: "Inter";
            font-size: 13px;
            font-weight: 600;
        }
        QPushButton#ItemNotesAddButton:hover {
            background: rgba(16, 185, 129, 0.38);
            border-color: rgba(52, 211, 153, 0.85);
        }
        QPushButton#ItemNotesAddButton:pressed {
            background: rgba(16, 185, 129, 0.48);
        }
        QPushButton#ItemNotesAddButton:disabled {
            border: 1px solid rgba(39, 39, 42, 0.50);
            background: rgba(24, 24, 27, 0.35);
            color: rgba(250, 250, 250, 0.45);
        }
        QPlainTextEdit#ItemNotesAddEditor,
        QTextEdit#ItemNotesAddEditor {
            background-color: #0a0a0c;
            border: 1px solid rgba(39, 39, 42, 0.65);
            border-radius: 8px;
            padding: 8px 10px;
            color: #e4e4e7;
            font-family: "Inter";
            font-size: 11px;
        }
        QPlainTextEdit#ItemNotesAddEditor:focus,
        QTextEdit#ItemNotesAddEditor:focus {
            background-color: #0c0c0e;
            border: 1px solid #2563eb;
        }
        QPlainTextEdit#ItemNotesAddEditor QAbstractScrollArea::viewport,
        QTextEdit#ItemNotesAddEditor QAbstractScrollArea::viewport {
            background-color: #0a0a0c;
        }
        QPlainTextEdit#ItemNotesAddEditor:focus QAbstractScrollArea::viewport,
        QTextEdit#ItemNotesAddEditor:focus QAbstractScrollArea::viewport {
            background-color: #0c0c0e;
        }
        QTextBrowser#ItemNotesBodyBrowser {
            background: transparent;
            border: none;
            color: #d4d4d8;
            font-family: "Inter";
            font-size: 11px;
        }
        /* MONOS calendar (custom month grid) */
        QWidget#MonosCalendar {
            background-color: #18181b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
        }
        QLabel#MonosCalendarWeekday {
            color: #71717a;
            font-family: "Inter";
            font-size: 10px;
            font-weight: 600;
        }
        QLabel#MonosCalendarMonthLabel {
            color: #e4e4e7;
            font-family: "Inter";
            font-size: 13px;
            font-weight: 600;
        }
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
        QPushButton#MonosCalendarDayBtn {
            background: transparent;
            border: none;
            border-radius: 8px;
            color: #d4d4d8;
            font-family: "Inter";
            font-size: 13px;
            font-weight: 500;
        }
        QPushButton#MonosCalendarDayBtn:hover {
            background: rgba(255, 255, 255, 0.06);
            color: #fafafa;
        }
        QPushButton#MonosCalendarDayBtn[weekend="true"] {
            color: #f87171;
        }
        QPushButton#MonosCalendarDayBtn[weekend="true"]:hover {
            color: #fca5a5;
        }
        QPushButton#MonosCalendarDayBtn[today="true"] {
            border: 1px solid #52525b;
        }
        QPushButton#MonosCalendarDayBtn[selected="true"] {
            background-color: #2563eb;
            color: #ffffff;
            font-weight: 600;
        }
        QPushButton#MonosCalendarDayBtn[selected="true"]:hover {
            background-color: #3b82f6;
            color: #ffffff;
        }
        QPushButton#MonosCalendarDayBtn[selected="true"][weekend="true"] {
            color: #ffffff;
        }
        QPushButton#MonosCalendarDayBtn:disabled {
            background: transparent;
            border: none;
            color: transparent;
        }
        /* MonosDateEdit: calendar button inside field (matches QLineEdit width) */
        QWidget#MonosDateEditHost {
            background: transparent;
        }
        QWidget#MonosDateEdit QDateEdit#MonosDateEditField {
            min-height: 32px;
            padding: 6px 30px 6px 10px;
            border: 1px solid rgba(39, 39, 42, 0.50);
            border-radius: 6px;
            background: #27272a;
        }
        QWidget#MonosDateEdit QDateEdit#MonosDateEditField:focus {
            border: 1px solid #2563eb;
        }
        QWidget#MonosDateEdit QToolButton#MonosDateEditCalendarBtn {
            padding: 0;
            margin: 0 6px 0 0;
            min-width: 24px;
            max-width: 24px;
            min-height: 24px;
            max-height: 24px;
            background: transparent;
            border: none;
            border-radius: 4px;
        }
        QWidget#MonosDateEdit QToolButton#MonosDateEditCalendarBtn:hover {
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
            background-color: #151618;
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
        /* Updates tab: status row (Windows Update style) */
        QWidget#UpdateStatusRow {
            min-height: 44px;
        }
        QLabel#UpdateStatusMessage {
            font-size: 13px;
            font-weight: 600;
            color: #fafafa;
            min-height: 20px;
        }
        QLabel#UpdateStatusLastChecked {
            font-size: 11px;
            color: #71717a;
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
        QFrame#DccCard[dept_default="true"] {
            border: 2px solid rgba(161, 161, 170, 0.75);
        }
        QFrame#DccCard[dept_default="true"][selected="true"] {
            border: 2px solid #2563eb;
            background-color: rgba(37, 99, 235, 0.12);
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
            font-weight: 500;
        }
        QLabel#DccCardLabel[labelScale="primary"] {
            font-size: 11px;
        }
        QLabel#DccCardLabel[labelScale="compact"] {
            font-size: 9px;
        }
        QScrollArea#OpenResolverScroll {
            background: transparent;
            border: none;
        }
        QScrollArea#OpenResolverScroll > QWidget > QWidget {
            background: transparent;
        }
        QLabel#DialogHelper {
            color: #a1a1aa;
            font-size: 11px;
        }
        /* Settings → General → UI: section cards */
        QScrollArea#SettingsPageScroll {
            background: transparent;
            border: none;
        }
        QScrollArea#SettingsPageScroll > QWidget > QWidget {
            background: transparent;
        }
        QFrame#SettingsSectionCard {
            background-color: #18181b;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 10px;
        }
        QLabel#SettingsSectionTitle {
            color: #fafafa;
        }
        QLabel#SettingsSectionDesc {
            color: #71717a;
            line-height: 1.45;
        }
        QLabel#SettingsSubsectionTitle {
            color: #71717a;
            letter-spacing: 0.08em;
        }
        QLabel#SettingsFieldLabel {
            color: #d4d4d8;
        }
        QFrame#SettingsSectionDivider {
            background-color: rgba(39, 39, 42, 0.45);
            border: none;
            max-height: 1px;
        }
        QPushButton#SettingsInlineActionButton {
            padding: 8px 14px;
            border-radius: 8px;
            border: 1px solid rgba(59, 130, 246, 0.35);
            background: rgba(37, 99, 235, 0.12);
            color: #93c5fd;
            font-size: 12px;
            font-weight: 500;
        }
        QPushButton#SettingsInlineActionButton:hover {
            background: rgba(37, 99, 235, 0.22);
            border-color: rgba(59, 130, 246, 0.55);
            color: #bfdbfe;
        }
        QPushButton#SettingsInlineActionButton:disabled {
            color: rgba(161, 161, 170, 0.55);
            background: rgba(24, 24, 27, 0.35);
            border-color: rgba(39, 39, 42, 0.45);
        }
        QFrame#SettingsSectionCard QComboBox#SettingsComboBox {
            padding: 6px 10px;
            padding-right: 28px;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 8px;
            background-color: #27272a;
            color: #fafafa;
            font-size: 13px;
            font-weight: 500;
            min-height: 28px;
        }
        QFrame#SettingsSectionCard QComboBox#SettingsComboBox:hover {
            border-color: rgba(63, 63, 70, 0.90);
            background-color: #2a2a2e;
        }
        QFrame#SettingsSectionCard QComboBox#SettingsComboBox:focus {
            border: 1px solid #2563eb;
        }
        QFrame#SettingsSectionCard QComboBox#SettingsComboBox:disabled {
            color: #71717a;
            background-color: #1f1f22;
        }
        QFrame#SettingsSectionCard QComboBox#SettingsComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 26px;
            border: none;
            border-left: 1px solid rgba(39, 39, 42, 0.55);
            border-top-right-radius: 7px;
            border-bottom-right-radius: 7px;
            background-color: #1f1f22;
        }
        QFrame#SettingsSectionCard QComboBox#SettingsComboBox::down-arrow {
            width: 12px;
            height: 12px;
            border: none;
        }
        QFrame#SettingsSectionCard QComboBox#SettingsComboBox QAbstractItemView {
            background-color: #18181b;
            color: #fafafa;
            border: 1px solid #3f3f46;
            border-radius: 8px;
            padding: 4px;
            outline: none;
            selection-background-color: rgba(59, 130, 246, 0.22);
            selection-color: #93c5fd;
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox {
            padding: 6px 8px;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 8px;
            background-color: #27272a;
            color: #fafafa;
            font-size: 13px;
            font-weight: 500;
            min-height: 28px;
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox:hover {
            border-color: rgba(63, 63, 70, 0.90);
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox:focus {
            border: 1px solid #2563eb;
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::up-button,
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::down-button {
            width: 22px;
            border: none;
            background-color: #1f1f22;
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::up-button {
            border-left: 1px solid rgba(39, 39, 42, 0.45);
            border-top-right-radius: 7px;
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::down-button {
            border-left: 1px solid rgba(39, 39, 42, 0.45);
            border-bottom-right-radius: 7px;
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::up-button:hover,
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::down-button:hover {
            background-color: #27272a;
        }
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::up-arrow,
        QFrame#SettingsSectionCard QSpinBox#SettingsSpinBox::down-arrow {
            width: 12px;
            height: 12px;
            border: none;
        }
        QFrame#SettingsSectionCard QLineEdit#SettingsLineEdit {
            padding: 6px 10px;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 8px;
            background-color: #27272a;
            color: #e4e4e7;
            font-size: 12px;
            font-family: "JetBrains Mono", "Consolas", "Courier New", monospace;
            min-height: 28px;
        }
        QFrame#SettingsSectionCard QLineEdit#SettingsLineEdit:focus {
            border: 1px solid #2563eb;
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
        /* Pipeline Settings → Workflow panel: department group (title for subdept checkboxes) */
        QLabel#PipelineWorkflowGroupTitle {
            color: #71717a;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.06em;
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
            background: #151618;
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
            background: #151618;
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
            background: #151618;
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
            background: #151618;
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
            background: #151618;
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
            background: #151618;
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

        /* Settings Tier 3 + main-view Grid|List: same pill tokens as SidebarScopePill */
        QWidget#Tier3Container {
            background-color: #1c1e22;
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 12px;
        }
        QPushButton#Tier3Pill {
            background: transparent;
            border: none;
            border-radius: 0;
            color: #71717a;
            padding: 5px 15px;
            font-size: 11px;
            font-weight: 500;
            min-height: 22px;
        }
        QPushButton#Tier3Pill[position="left"] {
            border-top-left-radius: 8px;
            border-bottom-left-radius: 8px;
            border-top-right-radius: 2px;
            border-bottom-right-radius: 2px;
        }
        QPushButton#Tier3Pill[position="center"] {
            border-radius: 2px;
        }
        QPushButton#Tier3Pill[position="right"] {
            border-top-left-radius: 2px;
            border-bottom-left-radius: 2px;
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
        }
        QPushButton#Tier3Pill[position="solo"] {
            border-radius: 8px;
        }
        QPushButton#Tier3Pill:checked {
            background-color: #2563eb;
            border: none;
            color: #fafafa;
            font-weight: 600;
        }
        QPushButton#Tier3Pill:checked:hover {
            background-color: #3b82f6;
            color: #fafafa;
        }
        QPushButton#Tier3Pill:hover:!checked {
            background-color: rgba(255, 255, 255, 0.08);
            border: none;
            color: #d4d4d8;
        }

        QStackedWidget#SettingsPageStack {
            background: #151618;
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

        QTableWidget#ScheduleHistoryTable {
            background-color: #0d0d0f;
            border: 1px solid #27272a;
            border-radius: 8px;
            gridline-color: transparent;
            font-size: 10px;
        }
        QTableWidget#ScheduleHistoryTable::item {
            padding: 4px 8px;
            border-bottom: 1px solid #1e1e20;
        }
        QTableWidget#ScheduleHistoryTable::item:selected {
            background-color: rgba(37, 99, 235, 0.12);
        }
        QLabel#ScheduleHistoryAvatar {
            background: transparent;
        }
        QLabel#ScheduleHistoryAuthorName {
            color: #d4d4d8;
            background: transparent;
        }
        QLabel#ScheduleHistoryAuthorLink {
            color: #93c5fd;
            background: transparent;
            padding: 0 4px;
        }
        QLabel#ScheduleHistoryAuthorLink:hover {
            color: #bfdbfe;
            text-decoration: underline;
        }
        QDialog#ScheduleHistoryDialog QHeaderView::section {
            background-color: #18181b;
            color: #71717a;
            padding: 6px 8px;
            border: none;
            border-bottom: 1px solid #27272a;
            font-size: 9px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }

        /* Schedule — Timeline markers dialog */
        QDialog#ScheduleMilestoneDialog QPushButton#Tier3Pill {
            padding: 7px 20px;
            font-size: 12px;
            min-height: 28px;
        }
        QDialog#ScheduleMilestoneDialog QLineEdit {
            padding: 8px 12px;
        }
        QDialog#ScheduleMilestoneDialog QWidget#MonosDateEdit {
            min-width: 0;
        }
        QFrame#ScheduleMilestoneFormCard {
            background-color: #18181b;
            border: 1px solid #27272a;
            border-radius: 8px;
        }
        QFrame#ScheduleMilestoneListBody {
            background-color: #0d0d0f;
            border: 1px solid #27272a;
            border-radius: 8px;
        }
        QListWidget#ScheduleMilestoneList {
            background: transparent;
            border: none;
            outline: none;
            padding: 2px 0 0 0;
        }
        QListWidget#ScheduleMilestoneList::item {
            color: #d4d4d8;
            padding: 8px 10px;
            margin-bottom: 2px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
        }
        QListWidget#ScheduleMilestoneList::item:hover {
            background: rgba(255, 255, 255, 0.04);
            color: #fafafa;
        }
        QListWidget#ScheduleMilestoneList::item:selected {
            background: rgba(37, 99, 235, 0.12);
            color: #93c5fd;
        }
        QToolButton#ScheduleIconToolBtn {
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 4px;
            background: transparent;
        }
        QToolButton#ScheduleIconToolBtn:hover:enabled {
            background: rgba(239, 68, 68, 0.12);
            border-color: rgba(239, 68, 68, 0.35);
        }
        QToolButton#ScheduleIconToolBtn:disabled {
            opacity: 0.35;
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
            background-color: #151618;
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
            background-color: #151618;
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
        QWidget#SidebarContainer,
        QWidget#SidebarFilterPanel {
            background-color: #181a1d;
        }
        QLabel#SidebarProjectNameLabel {
            color: #fafafa;
            font-family: "Inter", sans-serif;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.04em;
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
            color: #71717a; /* Zinc-500 — Level 2 section */
            font-family: "Inter", sans-serif;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 0.08em;
        }
        QWidget#SidebarFilterHeaderRow {
            min-height: 20px;
            max-height: 20px;
        }
        QWidget#SidebarFilterDeptSection,
        QWidget#SidebarFilterTypeSection {
            background: transparent;
        }
        QFrame#SidebarFilterListContainer {
            background-color: #1e2124;
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 8px;
        }
        QWidget#SidebarFilterDeptSection QFrame#SidebarFilterListContainer,
        QWidget#SidebarFilterTypeSection QFrame#SidebarFilterListContainer,
        QWidget#SidebarFilterScopeSection QFrame#SidebarFilterListContainer,
        QWidget#SidebarRecentTasksBlock QFrame#SidebarFilterListContainer {
            background-color: #1c1e22;
            border: 1px solid rgba(255, 255, 255, 0.04);
        }
        QFrame#SidebarFilterListContainer QListWidget#SidebarFilterList,
        QFrame#SidebarFilterListContainer QListWidget#SidebarTagList,
        QFrame#SidebarFilterListContainer QListWidget#SidebarRecentTasksList {
            background: transparent;
            border: none;
            padding: 4px;
        }
        QPushButton#SidebarRecentTasksHeaderButton {
            background: transparent;
            border: none;
            color: #71717a;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.05em;
            text-align: left;
            padding: 0;
        }
        QPushButton#SidebarRecentTasksHeaderButton:hover {
            color: #a1a1aa;
        }
        QToolButton#SidebarProjectSwitch {
            padding: 6px 10px;
            border: none;
            border-radius: 6px;
            background: transparent;
            color: #71717a;
            font-family: "Inter", sans-serif;
            font-size: 11px;
            font-weight: 600;
            text-align: left;
        }
        QToolButton#SidebarProjectSwitch[state="active"] {
            color: #a1a1aa;
        }
        QToolButton#SidebarProjectSwitch[state="empty"] {
            color: #71717a;
        }
        QToolButton#SidebarProjectSwitch:hover {
            background: transparent;
            color: #e2e2e2;
        }
        QToolButton#SidebarProjectSwitch::menu-indicator { image: none; width: 0; }
        QLabel#SidebarMutedText {
            color: #71717a; /* Zinc-500 */
            font-family: "Inter", sans-serif;
            font-size: 12px;
            font-weight: 500;
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

        /* Scope pill: Projects | Asset | Shot, inspector tabs, title-bar nav */
        QWidget#SidebarScopePill {
            background-color: #1c1e22;
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 12px;
        }
        QWidget#SidebarScopePill[display="iconOnly"] QToolButton#SidebarScopePillSegment,
        QWidget#SidebarScopePill[display="mixed"] QToolButton#SidebarScopePillSegment[labeled="false"] {
            padding: 0;
            min-width: 36px;
            max-width: 36px;
        }
        QWidget#SidebarScopePill[display="iconOnly"] QToolButton#SidebarScopePillSegment[active="true"],
        QWidget#SidebarScopePill[display="mixed"] QToolButton#SidebarScopePillSegment[labeled="false"][active="true"] {
            font-style: normal;
        }
        QWidget#SidebarScopePill[display="mixed"] QToolButton#SidebarScopePillSegment[labeled="true"] {
            padding: 0 8px 0 6px;
            min-width: 48px;
        }
        QToolButton#SidebarScopePillSegment {
            background: transparent;
            border: none;
            color: #71717a;
            padding: 0 10px;
            margin: 0;
            font-size: 13px;
            font-weight: 500;
            min-height: 32px;
            border-radius: 0;
        }
        QToolButton#SidebarScopePillSegment[position="left"] {
            border-top-left-radius: 8px;
            border-bottom-left-radius: 8px;
            border-top-right-radius: 2px;
            border-bottom-right-radius: 2px;
        }
        QToolButton#SidebarScopePillSegment[position="center"] {
            border-radius: 2px;
        }
        QToolButton#SidebarScopePillSegment[position="right"] {
            border-top-left-radius: 2px;
            border-bottom-left-radius: 2px;
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
        }
        QToolButton#SidebarScopePillSegment[position="solo"] {
            border-radius: 8px;
        }
        QToolButton#SidebarScopePillSegment[active="true"] {
            background-color: #2563eb;
            color: #fafafa;
            font-weight: 600;
            font-style: normal;
        }
        QToolButton#SidebarScopePillSegment:hover {
            background-color: rgba(255, 255, 255, 0.08);
            color: #d4d4d8;
        }
        QToolButton#SidebarScopePillSegment[active="true"]:hover {
            background-color: #3b82f6;
            color: #fafafa;
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

        /* Sidebar filter lists — row chrome painted by _SidebarDeptListDelegate */
        QListWidget#SidebarFilterList {
            background: transparent;
            border: none;
            border-radius: 0;
            color: #d4d4d8; /* Zinc-300 body */
            padding: 0;
        }
        QListWidget#SidebarFilterList::item {
            height: 32px;
            padding: 0;
            border: none;
            background: transparent;
        }
        QListWidget#SidebarFilterList::item:hover,
        QListWidget#SidebarFilterList::item:selected {
            background: transparent;
            border: none;
            color: inherit;
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

        /* Recent tasks list (sidebar) — rows painted by delegate; container via SidebarFilterListContainer */
        QListWidget#SidebarRecentTasksList {
            border: none;
            border-radius: 0;
            background: transparent;
            color: #a1a1aa;
            padding: 0;
        }
        QListWidget#SidebarRecentTasksList::item {
            height: 28px;
            padding: 0;
            border: none;
            background: transparent;
        }
        QListWidget#SidebarRecentTasksList::item:hover,
        QListWidget#SidebarRecentTasksList::item:selected {
            background: transparent;
            border: none;
            color: inherit;
        }

        QToolButton#SidebarFilterAddButton {
            min-width: 18px;
            max-width: 18px;
            min-height: 18px;
            max-height: 18px;
            border-radius: 4px;
            border: none;
            background: rgba(39, 39, 42, 0.85);
            color: #a1a1aa;
            font-weight: 600;
            font-size: 14px;
            padding: 0px;
        }
        QToolButton#SidebarFilterAddButton:hover {
            background: rgba(63, 63, 70, 0.95);
            border: none;
            color: #fafafa;
        }

        QToolButton#SidebarFilterSectionChevron {
            min-width: 16px;
            max-width: 16px;
            min-height: 16px;
            max-height: 16px;
            border: none;
            background: transparent;
            padding: 0px;
        }
        QToolButton#SidebarFilterSectionChevron:hover {
            background: rgba(255, 255, 255, 0.06);
            border-radius: 4px;
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
            background-color: #181a1d;
            border-top: 1px solid rgba(39, 39, 42, 0.50);
        }
        QWidget#AppFooter {
            background-color: #181a1d;
            border-top: 1px solid rgba(39, 39, 42, 0.50);
        }
        QLabel#AppFooterName {
            color: #a1a1aa;
        }
        QLabel#AppFooterVersion {
            color: #52525b;
        }
        QLabel#AppFooterLog {
            color: #71717a;
        }
        QLabel#AppFooterLog[level="success"] {
            color: #86efac;
        }
        QLabel#AppFooterLog[level="warning"] {
            color: #fcd34d;
        }
        QLabel#AppFooterLog[level="error"] {
            color: #fca5a5;
        }
        QLabel#SidebarFooterName {
            color: #a1a1aa;
        }
        QLabel#SidebarFooterVersion {
            color: #52525b;
        }
        QFrame#SidebarNavSeparator {
            background-color: rgba(63, 63, 70, 0.6);
            border: none;
            max-height: 1px;
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

        /* --- SidebarNavRail / SidebarCompact (icon-only narrow sidebar) --- */
        QWidget#SidebarNavRail {
            background-color: #1e2124;
            border-right: 1px solid rgba(255, 255, 255, 0.06);
        }
        QFrame#NavRailFlyout {
            background-color: #2563eb;
            border: none;
            border-radius: 8px;
        }
        QFrame#NavRailFlyout[active="true"] {
            background-color: #3b82f6;
        }
        QLabel#NavRailFlyoutLabel {
            color: #fafafa;
            font-family: "Inter", sans-serif;
            font-size: 13px;
            font-weight: 500;
            background: transparent;
        }
        QFrame#NavRailFlyout[active="true"] QLabel#NavRailFlyoutLabel {
            color: #fafafa;
            font-weight: 600;
        }
        QWidget#NavRailExpandItem {
            background: transparent;
        }
        QWidget#SidebarCompact {
            background-color: #181a1d;
        }
        QToolButton#SidebarCompactProjectSwitch {
            background: transparent;
            border: none;
            border-radius: 6px;
        }
        QToolButton#SidebarCompactProjectSwitch:hover {
            background: rgba(255, 255, 255, 0.06);
        }
        QToolButton#SidebarCompactProjectSwitch[state="active"] { }
        QToolButton#SidebarCompactProjectSwitch[state="empty"] { }
        QToolButton#SidebarCompactScopeButton,
        QToolButton#SidebarCompactFooterNavButton,
        QToolButton#SidebarCompactRecentTasksButton,
        QToolButton#SidebarCompactFilterButton {
            background: transparent;
            border: none;
            border-radius: 6px;
            margin: 4px 0;
        }
        QToolButton#SidebarCompactScopeButton:hover,
        QToolButton#SidebarCompactFooterNavButton:hover,
        QToolButton#SidebarCompactRecentTasksButton:hover,
        QToolButton#SidebarCompactFilterButton:hover {
            background: rgba(255, 255, 255, 0.06);
        }
        QWidget#SidebarCompactFooter {
            background-color: #181a1d;
            border-top: 1px solid rgba(39, 39, 42, 0.50);
        }
        QFrame#SidebarCompactRecentTasksPopup {
            background-color: #18181b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
        }
        QFrame#SidebarCompactFilterPopup {
            background-color: #18181b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
        }
        QFrame#HeaderFilterPickerPopup {
            background-color: #18181b;
            border: 1px solid #3f3f46;
            border-radius: 8px;
        }

        /* --- Metadata-driven navigation (SidebarWidget + AssetGridWidget) --- */
        QWidget#MetadataNavRoot {
            background: #151618;
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
            background: #151618;
        }
        QFrame#AssetCard {
            background: #191b1e;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 4px;
        }
        QFrame#AssetCard:hover {
            background: #1d1f23;
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

        /* --- Dashboard (bento) --- */
        QWidget#DashboardRoot {
            background: #151618;
        }
        QFrame#DashboardCard {
            background: #18181b;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 12px;
        }
        QFrame#DashboardHeaderCard {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #1c1c20, stop:1 #161618);
            border: 1px solid rgba(39, 39, 42, 0.65);
            border-radius: 12px;
        }
        QLabel#DashboardCardTitle {
            color: #e4e4e7; /* Zinc-200 */
            font-family: "Inter";
            font-size: 14px;
            font-weight: 700;
            letter-spacing: -0.1px;
        }
        QPushButton#DashboardNotesFilterBtn {
            background: transparent;
            border: 1px solid rgba(63, 63, 70, 0.8);
            border-radius: 6px;
            padding: 4px 10px;
            color: #a1a1aa;
            font-family: "Inter";
            font-size: 11px;
            font-weight: 600;
        }
        QPushButton#DashboardNotesFilterBtn:hover {
            color: #e4e4e7;
            border-color: rgba(113, 113, 122, 0.9);
        }
        QPushButton#DashboardNotesFilterBtn:checked {
            background: rgba(59, 130, 246, 0.18);
            border-color: rgba(96, 165, 250, 0.55);
            color: #fafafa;
        }
        QPushButton#DashboardNoteDeptBtn {
            background: rgba(59, 130, 246, 0.16);
            border: none;
            border-radius: 6px;
            padding: 2px 8px;
            color: #3b82f6;
            font-family: "Inter";
            font-size: 10px;
            font-weight: 700;
        }
        QPushButton#DashboardNoteDeptBtn:hover {
            background: rgba(59, 130, 246, 0.24);
            color: #3b82f6;
        }
        QPushButton#DashboardEntityNameBtn {
            border-width: 0px;
            border-radius: 6px;
            padding: 2px 8px;
            font-family: "Inter";
            font-size: 10px;
            font-weight: 800;
        }
        QPushButton#DashboardEntityNameBtn[chipTone="asset"] {
            background: rgba(16, 185, 129, 0.16);
            color: #10b981;
        }
        QPushButton#DashboardEntityNameBtn[chipTone="asset"]:hover {
            background: rgba(16, 185, 129, 0.24);
        }
        QPushButton#DashboardEntityNameBtn[chipTone="shot"] {
            background: rgba(245, 158, 11, 0.16);
            color: #f59e0b;
        }
        QPushButton#DashboardEntityNameBtn[chipTone="shot"]:hover {
            background: rgba(245, 158, 11, 0.24);
        }
        QLabel#DashboardProjectTitle {
            color: #fafafa;
            font-family: "Inter";
            font-size: 20px;
            font-weight: 800;
            letter-spacing: -0.3px;
        }
        QLabel#DashboardProjectPath {
            color: #71717a;
            font-family: "JetBrains Mono";
            font-size: 11px;
        }
        QLabel#DashboardWelcomeSub {
            color: #71717a;
            font-family: "Inter";
            font-size: 13px;
            font-weight: 500;
        }
        QLabel#DashboardAttentionBadge {
            color: #f59e0b;
            background: rgba(245, 158, 11, 0.14);
            font-family: "Inter";
            font-size: 11px;
            font-weight: 800;
            border-radius: 6px;
            padding: 1px 8px;
        }
        /* KPI tile */
        QFrame#DashboardMetricTile {
            background: #18181b;
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 12px;
        }
        QFrame#DashboardMetricTile[clickable="true"]:hover {
            background: #1f1f22;
            border: 1px solid rgba(96, 165, 250, 0.45);
        }
        QFrame#DashboardMetricTile[tone="danger"] {
            border: 1px solid rgba(239, 68, 68, 0.35);
        }
        QLabel#DashboardTileLabel {
            color: #71717a;
            font-family: "Inter";
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 1px;
        }
        QPushButton#DashboardTileLink {
            background: transparent;
            border: none;
            padding: 0px;
            text-align: left;
            color: #60a5fa;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#DashboardTileLink:hover {
            color: #93c5fd;
        }
        QPushButton#DashboardPrimaryButton {
            padding: 7px 14px;
            border-radius: 8px;
            border: 1px solid #2563eb;
            background: #2563eb;
            color: #ffffff;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 700;
        }
        QPushButton#DashboardPrimaryButton:hover {
            background: #1d4ed8;
            border-color: #1d4ed8;
        }
        /* Identity picker rows */
        QWidget#IdentityUserRow {
            background: rgba(24, 24, 27, 0.5);
            border: 1px solid rgba(39, 39, 42, 0.55);
            border-radius: 10px;
        }
        QWidget#IdentityUserRow:hover {
            background: #1f1f22;
            border: 1px solid rgba(96, 165, 250, 0.35);
        }
        QWidget#IdentityUserRow[selected="true"] {
            background: rgba(37, 99, 235, 0.12);
            border: 1px solid #2563eb;
        }
        /* Generic clickable row used by Dept load / Next 7 days / Attention / Notes */
        QFrame#DashboardRow {
            background: transparent;
            border: none;
            border-radius: 8px;
        }
        QFrame#DashboardRow[clickable="true"]:hover {
            background: rgba(255, 255, 255, 0.04);
        }
        QLabel#DashboardChip {
            color: #fafafa;
            font-family: "Inter";
            font-size: 10px;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 6px;
        }
        QLabel#DashboardEmptyHint {
            color: #52525b;
            font-family: "Inter";
            font-size: 12px;
        }
        QLabel#DashboardMutedMeta {
            color: #71717a;
            font-family: "JetBrains Mono";
            font-size: 11px;
        }
        QPushButton#DashboardGhostButton {
            padding: 6px 12px;
            border-radius: 8px;
            border: 1px solid rgba(96, 165, 250, 0.35);
            background: rgba(96, 165, 250, 0.10);
            color: #93c5fd;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 600;
        }
        QPushButton#DashboardGhostButton:hover {
            background: rgba(96, 165, 250, 0.18);
            border-color: rgba(96, 165, 250, 0.55);
            color: #dbeafe;
        }

        /* --- Dashboard customize (bento edit mode) --- */
        QWidget#DashboardEditBar {
            background: transparent;
        }
        QFrame#DashboardBentoChrome[editMode="true"] {
            border: 1px dashed rgba(255, 255, 255, 0.14);
            border-radius: 10px;
            background: rgba(24, 24, 27, 0.35);
        }
        QWidget#DashboardBentoToolbar {
            background: rgba(255, 255, 255, 0.03);
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
        }
        QLabel#DashboardBentoChromeLabel {
            color: #a1a1aa;
            font-family: "Inter";
            font-size: 11px;
            font-weight: 600;
        }
        QToolButton#DashboardBentoDragHandle {
            background: transparent;
            border: none;
            padding: 2px;
        }
        QToolButton#DashboardBentoDragHandle:hover {
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
        }
        QToolButton#DashboardBentoChromeBtn {
            background: transparent;
            border: none;
            padding: 4px;
            border-radius: 4px;
        }
        QToolButton#DashboardBentoChromeBtn:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        QFrame#DashboardDropIndicator {
            background: #2563eb;
            border: none;
            border-radius: 2px;
            min-height: 4px;
            max-height: 4px;
        }
        QFrame#DashboardRowDropZone {
            background: transparent;
            border: none;
        }

        /* --- Inspector schedule (pipeline tab) --- */
        QFrame#InspectorScheduleCard {
            background: #181a1d;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
        }
        QFrame#InspectorScheduleSubCard {
            background: #25282c;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 6px;
        }
        QFrame#InspectorAssignConfirmBanner {
            background: rgba(59, 130, 246, 0.1);
            border: 1px solid rgba(59, 130, 246, 0.35);
            border-radius: 8px;
            padding: 8px;
        }
        QLabel#InspectorSubSectionTitle {
            color: #71717a;
            background: transparent;
        }
        QFrame#InspectorDeptStatusPanel {
            background: #2a2d31;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
        }
        QLabel#InspectorDeptMetricLabel {
            color: #a1a1aa;
            background: transparent;
        }
        QLabel#InspectorFieldLabel {
            color: #a1a1aa;
            background: transparent;
        }
        """
    )

