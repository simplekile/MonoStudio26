from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

import time
from PySide6.QtCore import QByteArray, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QSettings
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.version import get_app_version
from monostudio.core.project_guide_tags import (
    ALL_TAG_IDS,
    DEFAULT_TAG_DEFINITIONS,
    TAG_COLOR_BY_ID,
    TAG_COLOR_PALETTE,
    TAG_LABEL_BY_ID,
    add_tag_definition,
    build_color_map,
    build_label_map,
    delete_tag_definition,
    paths_with_tag,
    read_tag_definitions,
    recolor_tag_definition,
    rename_tag_definition,
)
from monostudio.core.models import Asset, ProjectIndex, Shot
from monostudio.core.pipeline_types_and_presets import load_pipeline_types_and_presets
from monostudio.core.app_paths import get_app_base_path
from monostudio.core.workspace_reader import DiscoveredProject
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.recent_tasks_store import RecentTask
from monostudio.ui_qt.style import (
    MONOS_COLORS,
    SIDEBAR_DEPT_LIST_STYLE,
    MonosDialog,
    MonosMenu,
    monos_font,
    project_accent_color,
)


class SidebarContext(str, Enum):
    PROJECTS = "Projects"
    SHOTS = "Shots"
    ASSETS = "Assets"
    INBOX = "Inbox"
    PROJECT_GUIDE = "Project Guide"
    OUTBOX = "Outbox"


# Single nav item that holds the scope pill (Project | Shot | Asset).
_NAV_SCOPE_ITEM_ROLE = "_scope"

# Tag list: UserRole = tag_id, UserRole+1 = item count for badge
TAG_COUNT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _is_shot_type(type_id: str) -> bool:
    # Pipeline convention: shot types are "shot" or prefixed "shot_".
    return bool(type_id == "shot" or type_id.startswith("shot_"))


def _title_case_label(value: str) -> str:
    # UI display only (ids remain unchanged). Keep simple + deterministic.
    return (value or "").strip().replace("_", " ").title()


def _lucide_two_state_icon(icon_name: str, *, fallback_name: str) -> QIcon:
    """
    Build a 2-state QIcon:
    - Normal: Zinc-400 (text_label)
    - Selected: Blue-400
    """
    normal = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_label"])
    if normal.isNull():
        normal = lucide_icon(fallback_name, size=16, color_hex=MONOS_COLORS["text_label"])

    selected = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["blue_400"])
    if selected.isNull():
        selected = lucide_icon(fallback_name, size=16, color_hex=MONOS_COLORS["blue_400"])

    out = QIcon()
    out.addPixmap(normal.pixmap(16, 16), QIcon.Normal, QIcon.Off)
    out.addPixmap(selected.pixmap(16, 16), QIcon.Selected, QIcon.Off)
    return out


def _load_logo_pixmap(size: int, color_hex: str) -> QPixmap:
    """Load app logo from monostudio_data/icons/logo.svg; render at size with fill color (black & white)."""
    base = get_app_base_path()
    logo_path = base / "monostudio_data" / "icons" / "logo.svg"
    if not logo_path.is_file():
        return QPixmap()
    try:
        svg = logo_path.read_text(encoding="utf-8").replace("currentColor", color_hex)
    except OSError:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(p, QRect(0, 0, size, size))
    finally:
        p.end()
    return pix


# Row kinds: section (container header), dept (selectable), spacer (gap between containers).
_DEPT_ROW_SECTION = "section"
_DEPT_ROW_DEPT = "dept"
_DEPT_ROW_SPACER = "spacer"

# Section order for type list (Assets then Shots).
_TYPE_SECTION_ORDER = ("Assets", "Shots")


def _container_gradient(rect: QRectF) -> QLinearGradient:
    """Gradient cho container (giống page): start → end từ trái sang phải."""
    g = QLinearGradient(QPointF(rect.left(), 0), QPointF(rect.right(), 0))
    start = str(SIDEBAR_DEPT_LIST_STYLE.get("container_gradient_start", "#121214"))
    end = str(SIDEBAR_DEPT_LIST_STYLE.get("container_gradient_end", "#1b1b1b"))
    g.setColorAt(0.0, QColor(start))
    g.setColorAt(1.0, QColor(end))
    return g


def _rounded_rect_path(rect: QRectF, radius: float, round_top: bool, round_bottom: bool) -> QPainterPath:
    """Path for rect with optional rounded top and/or bottom corners."""
    path = QPainterPath()
    r = min(radius, rect.width() / 2, rect.height() / 2)
    if r <= 0:
        path.addRect(rect)
        return path
    if round_top and round_bottom:
        path.addRoundedRect(rect, r, r)
        return path
    if round_top:
        path.moveTo(rect.left() + r, rect.top())
        path.lineTo(rect.right() - r, rect.top())
        path.arcTo(rect.right() - 2 * r, rect.top(), 2 * r, 2 * r, 90, -90)
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + r)
        path.arcTo(rect.left(), rect.top(), 2 * r, 2 * r, 180, -90)
        path.closeSubpath()
        return path
    if round_bottom:
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom() - r)
        path.arcTo(rect.right() - 2 * r, rect.bottom() - 2 * r, 2 * r, 2 * r, 0, -90)
        path.lineTo(rect.left() + r, rect.bottom())
        path.arcTo(rect.left(), rect.bottom() - 2 * r, 2 * r, 2 * r, 270, -90)
        path.closeSubpath()
        return path
    path.addRect(rect)
    return path


class _TagListDelegate(QStyledItemDelegate):
    """Paints tag list item: default icon + text, then a small rounded count badge on the right."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        super().paint(painter, option, index)
        count = index.data(TAG_COUNT_ROLE)
        if count is None or (isinstance(count, int) and count <= 0):
            return
        try:
            n = int(count)
        except (TypeError, ValueError):
            return
        rect = option.rect
        pad = 4
        font = QFont(option.font)
        font.setPointSize(max(8, font.pointSize() - 2))
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(str(n))
        badge_w = max(14, tw + 8)
        badge_h = 14
        badge_x = rect.right() - badge_w - pad
        badge_y = rect.center().y() - badge_h // 2
        badge_rect = QRect(badge_x, badge_y, badge_w, badge_h)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        border = QColor(MONOS_COLORS.get("border_subtle", "#3f3f46"))
        painter.setPen(border)
        painter.setBrush(QColor(39, 39, 42, 180))
        painter.drawRoundedRect(badge_rect, 7, 7)
        painter.setPen(QColor(MONOS_COLORS.get("text_body", "#d4d4d8")))
        painter.setFont(font)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(n))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        s = super().sizeHint(option, index)
        return s


class _SidebarDeptListDelegate(QStyledItemDelegate):
    """
    Department list: section = container header (fill + title 7px); dept = fill + dot + icon + text.
    Section non-interactive. UserRole: dict with type + section_label or dept_id (+ optional in_section).
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        data = opt.index.data(Qt.UserRole) if opt.index.isValid() else None
        if isinstance(data, dict):
            if data.get("type") == _DEPT_ROW_SECTION:
                self._paint_section(painter, opt, data.get("section_label", ""))
                return
            if data.get("type") == _DEPT_ROW_SPACER:
                return  # empty row, list background shows through
        self._paint_dept_row(painter, opt, data if isinstance(data, dict) else None)

    def _paint_section(self, painter: QPainter, opt: QStyleOptionViewItem, title: str) -> None:
        r = opt.rect
        radius = float(SIDEBAR_DEPT_LIST_STYLE.get("container_radius_px", 6))
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_container_gradient(QRectF(r)))
            path = _rounded_rect_path(QRectF(r), radius, round_top=True, round_bottom=False)
            painter.drawPath(path)
            if title:
                fs = int(SIDEBAR_DEPT_LIST_STYLE["section_font_size_px"])
                f = QFont(opt.font.family(), fs, QFont.Weight.Normal)
                painter.setFont(f)
                key = str(SIDEBAR_DEPT_LIST_STYLE["section_title_color_key"])
                painter.setPen(QColor(MONOS_COLORS.get(key, MONOS_COLORS["text_meta"])))
                # Cùng padding trái như dept (10px) để title nằm chung container
                text_r = r.adjusted(10, 0, -10, 0)
                painter.drawText(text_r, Qt.AlignVCenter | Qt.AlignLeft, title)
        finally:
            painter.restore()

    def _paint_dept_row(self, painter: QPainter, opt: QStyleOptionViewItem, data: dict | None) -> None:
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        r = opt.rect
        radius = float(SIDEBAR_DEPT_LIST_STYLE.get("container_radius_px", 6))
        round_top = bool(data.get("round_top")) if isinstance(data, dict) else False
        round_bottom = bool(data.get("round_bottom")) if isinstance(data, dict) else False
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_container_gradient(QRectF(r)))
            path = _rounded_rect_path(QRectF(r), radius, round_top, round_bottom)
            painter.drawPath(path)
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, opt, painter, widget)
            inner = r.adjusted(10, 0, -10, 0)
            dot_r = 4
            dot_gap = 10
            dot_cx = inner.left() + dot_r
            dot_cy = r.center().y()
            is_selected = bool(opt.state & QStyle.State_Selected)
            dot_color = QColor(MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_meta"])
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(dot_color)
            painter.drawEllipse(dot_cx - dot_r, dot_cy - dot_r, dot_r * 2, dot_r * 2)
            x = dot_cx + dot_r + dot_gap
            icon_size = opt.decorationSize if opt.decorationSize.isValid() else QSize(16, 16)
            if not opt.icon.isNull():
                ir = QRect(x, r.center().y() - icon_size.height() // 2, icon_size.width(), icon_size.height())
                opt.icon.paint(painter, ir, Qt.AlignCenter, QIcon.Selected if is_selected else QIcon.Normal)
                x = ir.right() + 8
            text_right = inner.right()
            count = data.get("count") if isinstance(data, dict) else None
            if count is not None:
                badge_font = QFont(opt.font.family(), 8, QFont.Weight.Medium)
                badge_fm = QFontMetrics(badge_font)
                badge_text = str(count)
                badge_pad_h = 4
                badge_pad_v = 1
                badge_w = badge_fm.horizontalAdvance(badge_text) + badge_pad_h * 2
                badge_h = min(badge_fm.height() + badge_pad_v * 2, r.height() - 6)
                badge_w = max(badge_w, 14)
                text_right = inner.right() - badge_w - 2
                badge_rect = QRect(inner.right() - badge_w, r.center().y() - badge_h // 2, badge_w, badge_h)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                is_zero = count == 0
                if is_zero:
                    painter.setBrush(QColor(39, 39, 42))
                    painter.drawRoundedRect(badge_rect, 3, 3)
                    painter.setPen(QColor(MONOS_COLORS.get("text_meta", "#71717a")))
                else:
                    painter.setBrush(QColor(MONOS_COLORS.get("blue_700", "#075985")))
                    painter.drawRoundedRect(badge_rect, 3, 3)
                    painter.setPen(QColor(255, 255, 255, 180))
                painter.setFont(badge_font)
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
            text_rect = QRect(x, r.top(), max(0, text_right - x), r.height())
            fm = QFontMetrics(opt.font)
            text = fm.elidedText(opt.text, Qt.ElideRight, text_rect.width())
            pen_color = QColor(MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_label"])
            painter.setPen(pen_color)
            painter.setFont(opt.font)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        finally:
            painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        data = index.data(Qt.UserRole) if index.isValid() else None
        if isinstance(data, dict):
            if data.get("type") == _DEPT_ROW_SECTION:
                return QSize(-1, int(SIDEBAR_DEPT_LIST_STYLE["section_row_height_px"]))
            if data.get("type") == _DEPT_ROW_SPACER:
                return QSize(-1, int(SIDEBAR_DEPT_LIST_STYLE["spacer_row_height_px"]))
        return super().sizeHint(option, index)


class _SidebarDotItemDelegate(QStyledItemDelegate):
    """
    Sidebar list item delegate (e.g. Types list):
    - Draw a small leading dot (grey)
    - When selected, dot turns blue
    - Keeps existing icon (metadata) + text
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        widget = opt.widget
        style = widget.style() if widget else QApplication.style()

        # Background / selection (respects QSS).
        painter.save()
        try:
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, opt, painter, widget)

            r = opt.rect
            # Mirror QSS: padding: 6px 10px (horizontal = 10px).
            inner = r.adjusted(10, 0, -10, 0)

            # Dot (before the icon).
            dot_r = 4  # radius px
            dot_gap = 10  # gap after dot
            dot_cx = inner.left() + dot_r
            dot_cy = r.center().y()
            is_selected = bool(opt.state & QStyle.State_Selected)
            dot_color = QColor(MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_meta"])
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(dot_color)
            painter.drawEllipse(dot_cx - dot_r, dot_cy - dot_r, dot_r * 2, dot_r * 2)

            x = dot_cx + dot_r + dot_gap

            # Icon (optional).
            icon_size = opt.decorationSize if opt.decorationSize.isValid() else QSize(16, 16)
            if not opt.icon.isNull():
                ir = QRect(x, r.center().y() - icon_size.height() // 2, icon_size.width(), icon_size.height())
                opt.icon.paint(painter, ir, Qt.AlignCenter, QIcon.Selected if is_selected else QIcon.Normal)
                x = ir.right() + 8  # gap between icon and text

            # Text.
            text_rect = QRect(x, r.top(), max(0, inner.right() - x), r.height())
            fm = QFontMetrics(opt.font)
            text = fm.elidedText(opt.text, Qt.ElideRight, text_rect.width())
            pen = QColor(MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_label"])
            painter.setPen(pen)
            painter.setFont(opt.font)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        finally:
            painter.restore()


class _SidebarNavItemWidget(QWidget):
    """
    Primary Nav item (Alignment Matrix locked):
    - height: 36px
    - padding: px-3 (12px) / py-2 (8px)
    - left group: [ icon_container 24x24 ] gap 12px [ label 13px ]
    - right group: [ count badge ] flush-right
    - active indicator: 2px x 16px at left-0, vertically centered, zero-shift
    """

    def __init__(self, context_name: str, icon_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarNavItem")
        self.setProperty("active", False)
        # Required for QWidget background/border rules in QSS to actually paint.
        # (Otherwise :hover / [active="true"] background may not render.)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self._context_name = context_name
        self._icon_name = icon_name

        # Fixed height
        self.setMinimumHeight(36)
        self.setMaximumHeight(36)

        # Indicator is absolute-positioned (no text shift).
        self._indicator = QFrame(self)
        self._indicator.setObjectName("SidebarNavIndicator")
        self._indicator.setProperty("active", False)
        self._indicator.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)  # px-3 / py-2
        layout.setSpacing(0)

        left = QWidget(self)
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)  # gap-3

        self._icon_container = QLabel(left)
        self._icon_container.setObjectName("SidebarNavIconContainer")
        self._icon_container.setAlignment(Qt.AlignCenter)
        self._icon_container.setFixedSize(24, 24)
        self._sync_icon(active=False)

        self._label = QLabel(context_name, left)
        self._label.setObjectName("SidebarNavLabel")
        f_label = monos_font("Inter", 13, QFont.Weight.DemiBold)
        f_label.setLetterSpacing(QFont.PercentageSpacing, 97)  # tracking-tight
        self._label.setFont(f_label)

        left_layout.addWidget(self._icon_container)
        left_layout.addWidget(self._label, 1)

        self._badge = QLabel("", self)
        self._badge.setObjectName("SidebarNavBadge")
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setProperty("shape", "pill")
        f_badge = monos_font("Inter", 10, QFont.Weight.DemiBold)
        self._badge.setFont(f_badge)
        self._badge.setVisible(False)

        layout.addWidget(left, 1)
        layout.addWidget(self._badge, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.setMouseTracking(True)

    def context_name(self) -> str:
        return self._context_name

    def set_active(self, active: bool) -> None:
        self.setProperty("active", bool(active))
        self._indicator.setProperty("active", bool(active))
        self._sync_icon(active=bool(active))
        # Force re-style for dynamic properties.
        self.style().unpolish(self._indicator)
        self.style().polish(self._indicator)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _sync_icon(self, *, active: bool) -> None:
        # Default icon color = label; active icon color = action text (Blue-400).
        color = MONOS_COLORS["blue_400"] if active else MONOS_COLORS["text_label"]
        ic = lucide_icon(self._icon_name, size=16, color_hex=color)
        self._icon_container.setPixmap(ic.pixmap(16, 16))

    def set_count_badge(self, value: int | None) -> None:
        if value is None:
            self._badge.setVisible(False)
            self._badge.setText("")
            self._badge.setProperty("shape", "pill")
            self.style().unpolish(self._badge)
            self.style().polish(self._badge)
            return
        s = str(int(value))
        self._badge.setText(s)
        # 1-digit: dot badge (1:1). 2+ digits: pill badge.
        self._badge.setProperty("shape", "dot" if len(s) == 1 else "pill")
        self.style().unpolish(self._badge)
        self.style().polish(self._badge)
        self._badge.setVisible(True)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # 2px x 16px, flush left, vertically centered
        y = max(0, (self.height() - 16) // 2)
        self._indicator.setGeometry(0, y, 2, 16)


# Scope pill: one nav block for Project | Shot | Asset (single pill, three segments).
_SCOPE_PILL_CONTEXTS = (
    (SidebarContext.PROJECTS.value, "folder-kanban"),
    (SidebarContext.SHOTS.value, "clapperboard"),
    (SidebarContext.ASSETS.value, "box"),
)


class _SidebarScopePillWidget(QWidget):
    """
    One pill with three segments: Project, Shot, Asset.
    Emits segment_clicked(context_name). set_active_segment(name), set_badges(projects, shots, assets).
    """

    segment_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarScopePill")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(40)
        self.setMaximumHeight(40)
        self._active: str = SidebarContext.ASSETS.value
        self._badges: dict[str, int | None] = {
            SidebarContext.PROJECTS.value: None,
            SidebarContext.SHOTS.value: None,
            SidebarContext.ASSETS.value: None,
        }

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._buttons: dict[str, QToolButton] = {}
        for i, (ctx, icon_name) in enumerate(_SCOPE_PILL_CONTEXTS):
            btn = QToolButton(self)
            btn.setObjectName("SidebarScopePillSegment")
            btn.setProperty("segment", ctx)
            btn.setProperty("active", "false")
            btn.setProperty("position", "left" if i == 0 else ("right" if i == 2 else "center"))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            label = ctx.rstrip("s") if ctx.endswith("s") else ctx  # "Projects" -> "Project"
            btn.setText(label)
            f = monos_font("Inter", 13, QFont.Weight.DemiBold)
            f.setLetterSpacing(QFont.PercentageSpacing, 97)
            btn.setFont(f)
            ic = lucide_icon(icon_name, size=15, color_hex=MONOS_COLORS["text_label"])
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(15, 15))
            btn.setFixedHeight(32)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked=False, c=ctx: self.segment_clicked.emit(c))
            self._buttons[ctx] = btn
            layout.addWidget(btn, 0, Qt.AlignVCenter)

    def set_active_segment(self, context_name: str | None) -> None:
        """Set which segment is active. Pass None or unknown name to clear (no segment active)."""
        self._active = context_name or ""
        active_ctx = context_name if context_name in self._buttons else None
        for ctx, btn in self._buttons.items():
            is_active = ctx == active_ctx
            btn.setProperty("active", "true" if is_active else "false")
            color = MONOS_COLORS["blue_400"] if is_active else MONOS_COLORS["text_label"]
            ic_name = next((ic for c, ic in _SCOPE_PILL_CONTEXTS if c == ctx), "box")
            ic = lucide_icon(ic_name, size=15, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(15, 15))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_badges(self, projects_count: int | None, shots_count: int | None, assets_count: int | None) -> None:
        self._badges[SidebarContext.PROJECTS.value] = projects_count
        self._badges[SidebarContext.SHOTS.value] = shots_count
        self._badges[SidebarContext.ASSETS.value] = assets_count
        for ctx, btn in self._buttons.items():
            count = self._badges.get(ctx)
            tip = ctx
            if count is not None:
                tip = f"{ctx}: {count}"
            btn.setToolTip(tip)
            # Optional: set text to "Project (3)" etc.; keeping label only for now, tooltip has count.


_SIDEBAR_TYPE_LIST_MAX_HEIGHT_PX = 180


class SidebarWidget(QWidget):
    """
    Metadata-driven filter sidebar (UI-only, mock data for now).

    Structure:
    - DEPARTMENTS (single-select, toggle-to-none)
    - TYPES       (single-select, toggle-to-none)

    Emits intents only; does NOT filter data.
    """

    departmentClicked = Signal(object)  # str | None
    typeClicked = Signal(object)  # str | None
    tagClicked = Signal(object)  # str | None  (tag_id or None for "All")
    tagsDefinitionsChanged = Signal()  # emitted when user modifies tag definitions

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarFilterPanel")

        self._settings: QSettings | None = None

        self._active_department: str | None = None
        self._active_type: str | None = None
        self._active_tag: str | None = None

        self._mode: str = "assets"  # "assets" | "shots" (UI-only context)
        # Default number of items shown per section (user can pick any count).
        self._max_visible = 6
        self._all_departments: list[str] = []
        self._all_types: list[str] = []  # type_ids
        self._type_label_by_id: dict[str, str] = {}
        self._type_icon_by_id: dict[str, str] = {}
        self._dept_label_by_id: dict[str, str] = {}
        self._dept_icon_by_id: dict[str, str] = {}
        self._dept_parent: dict[str, str] = {}  # dept_id -> parent_id for subdepartment grouping
        # Mapping from type_id -> list of department ids that type supports (for per-type dept views).
        self._dept_ids_by_type: dict[str, list[str]] = {}
        # None = not configured yet (will default to first N once). [] is a valid "show none".
        self._visible_departments: list[str] | None = None
        self._visible_types: list[str] | None = None  # type_ids

        # Per-page state (Assets vs Shots). Keep UI selections when switching pages.
        self._state_by_mode: dict[str, dict[str, object]] = {}
        # Per-type department selection (type_id -> department_id); restored when switching type.
        self._department_by_type: dict[str, str | None] = {}
        # Item counts for label display (set by sidebar container from ProjectIndex).
        self._count_by_type: dict[str, int] = {}
        self._count_by_department: dict[str, int] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        f_h = monos_font("Inter", 10, QFont.Weight.ExtraBold)  # 800
        f_h.setLetterSpacing(QFont.PercentageSpacing, 112)  # tracking-widest-ish

        # --- Header rows (label + "+" button, right-aligned)
        dept_header_row = QWidget(self)
        dept_header_row.setObjectName("SidebarFilterHeaderRow")
        dept_header_row_l = QHBoxLayout(dept_header_row)
        dept_header_row_l.setContentsMargins(0, 0, 0, 0)
        dept_header_row_l.setSpacing(8)

        dept_icon = QLabel(dept_header_row)
        dept_icon.setObjectName("SidebarFilterHeaderIcon")
        dept_icon.setFixedSize(16, 16)
        dept_icon.setAlignment(Qt.AlignCenter)
        dept_icon.setPixmap(lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"]).pixmap(16, 16))

        dept_header = QLabel("DEPARTMENTS", dept_header_row)
        dept_header.setObjectName("SidebarSectionHeader")
        dept_header.setFont(f_h)

        self._btn_dept_pick = QToolButton(dept_header_row)
        self._btn_dept_pick.setObjectName("SidebarFilterAddButton")
        self._btn_dept_pick.setText("+")
        self._btn_dept_pick.setCursor(Qt.PointingHandCursor)
        self._btn_dept_pick.clicked.connect(self._open_department_picker)

        dept_header_row_l.addWidget(dept_icon, 0, Qt.AlignVCenter)
        dept_header_row_l.addWidget(dept_header, 0, Qt.AlignVCenter)
        dept_header_row_l.addStretch(1)
        dept_header_row_l.addWidget(self._btn_dept_pick, 0, Qt.AlignVCenter)

        self._dept_list = QListWidget(self)
        self._dept_list.setObjectName("SidebarFilterList")
        self._dept_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._dept_list.setUniformItemSizes(False)  # section/spacer/dept have different heights
        self._dept_list.setFocusPolicy(Qt.NoFocus)
        self._dept_list.setIconSize(QSize(16, 16))
        self._dept_list.setItemDelegate(_SidebarDeptListDelegate(self._dept_list))
        self._dept_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._dept_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dept_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._dept_list.itemClicked.connect(self._on_department_clicked)

        self._dept_section = QWidget(self)
        self._dept_section.setObjectName("SidebarFilterDeptSection")
        dept_section_lay = QVBoxLayout(self._dept_section)
        dept_section_lay.setContentsMargins(0, 0, 0, 0)
        dept_section_lay.setSpacing(0)
        dept_section_lay.addWidget(dept_header_row, 0)
        dept_section_lay.addWidget(self._dept_list, 1)

        type_header_row = QWidget(self)
        type_header_row.setObjectName("SidebarFilterHeaderRow")
        type_header_row_l = QHBoxLayout(type_header_row)
        type_header_row_l.setContentsMargins(0, 0, 0, 0)
        type_header_row_l.setSpacing(8)

        type_icon = QLabel(type_header_row)
        type_icon.setObjectName("SidebarFilterHeaderIcon")
        type_icon.setFixedSize(16, 16)
        type_icon.setAlignment(Qt.AlignCenter)
        type_icon.setPixmap(lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]).pixmap(16, 16))

        type_header = QLabel("TYPES", type_header_row)
        type_header.setObjectName("SidebarSectionHeader")
        type_header.setFont(f_h)

        self._btn_type_pick = QToolButton(type_header_row)
        self._btn_type_pick.setObjectName("SidebarFilterAddButton")
        self._btn_type_pick.setText("+")
        self._btn_type_pick.setCursor(Qt.PointingHandCursor)
        self._btn_type_pick.clicked.connect(self._open_type_picker)

        type_header_row_l.addWidget(type_icon, 0, Qt.AlignVCenter)
        type_header_row_l.addWidget(type_header, 0, Qt.AlignVCenter)
        type_header_row_l.addStretch(1)
        type_header_row_l.addWidget(self._btn_type_pick, 0, Qt.AlignVCenter)

        self._type_list = QListWidget(self)
        self._type_list.setObjectName("SidebarFilterList")
        self._type_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._type_list.setUniformItemSizes(False)  # section/spacer/type like departments
        self._type_list.setFocusPolicy(Qt.NoFocus)
        self._type_list.setIconSize(QSize(16, 16))
        self._type_list.setItemDelegate(_SidebarDeptListDelegate(self._type_list))
        self._type_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._type_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._type_list.setMaximumHeight(_SIDEBAR_TYPE_LIST_MAX_HEIGHT_PX)
        self._type_list.itemClicked.connect(self._on_type_clicked)

        self._type_section = QWidget(self)
        self._type_section.setObjectName("SidebarFilterTypeSection")
        type_section_lay = QVBoxLayout(self._type_section)
        type_section_lay.setContentsMargins(0, 0, 0, 0)
        type_section_lay.setSpacing(0)
        type_section_lay.addWidget(type_header_row, 0)
        type_section_lay.addWidget(self._type_list, 0)

        # --- TAGS section (visible only in "reference" mode) ---
        tag_header_row = QWidget(self)
        tag_header_row.setObjectName("SidebarFilterHeaderRow")
        tag_header_row_l = QHBoxLayout(tag_header_row)
        tag_header_row_l.setContentsMargins(0, 0, 0, 0)
        tag_header_row_l.setSpacing(8)

        tag_header_icon = QLabel(tag_header_row)
        tag_header_icon.setObjectName("SidebarFilterHeaderIcon")
        tag_header_icon.setFixedSize(16, 16)
        tag_header_icon.setAlignment(Qt.AlignCenter)
        tag_header_icon.setPixmap(lucide_icon("tag", size=16, color_hex=MONOS_COLORS["text_label"]).pixmap(16, 16))

        tag_header_label = QLabel("TAGS", tag_header_row)
        tag_header_label.setObjectName("SidebarSectionHeader")
        tag_header_label.setFont(f_h)

        self._btn_tag_pick = QToolButton(tag_header_row)
        self._btn_tag_pick.setObjectName("SidebarFilterAddButton")
        self._btn_tag_pick.setText("+")
        self._btn_tag_pick.setCursor(Qt.PointingHandCursor)
        self._btn_tag_pick.clicked.connect(self._open_tag_picker)

        tag_header_row_l.addWidget(tag_header_icon, 0, Qt.AlignVCenter)
        tag_header_row_l.addWidget(tag_header_label, 0, Qt.AlignVCenter)
        tag_header_row_l.addStretch(1)
        tag_header_row_l.addWidget(self._btn_tag_pick, 0, Qt.AlignVCenter)

        self._tag_list = QListWidget(self)
        self._tag_list.setObjectName("SidebarTagList")
        self._tag_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tag_list.setFocusPolicy(Qt.NoFocus)
        self._tag_list.setIconSize(QSize(20, 16))
        self._tag_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setItemDelegate(_TagListDelegate(self._tag_list))
        self._tag_list.itemClicked.connect(self._on_tag_clicked)

        self._tag_definitions: list[dict[str, str]] = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map: dict[str, str] = dict(TAG_COLOR_BY_ID)
        self._tag_label_map: dict[str, str] = dict(TAG_LABEL_BY_ID)
        self._visible_tags: list[str] = list(ALL_TAG_IDS)
        self._tag_item_tags: dict[str, list[str]] = {}  # path -> tag_ids (from Project Guide tree)
        self._project_root: Path | None = None
        self._rebuild_tag_list()

        self._tag_section = QWidget(self)
        self._tag_section.setObjectName("SidebarFilterTagSection")
        tag_section_lay = QVBoxLayout(self._tag_section)
        tag_section_lay.setContentsMargins(0, 0, 0, 0)
        tag_section_lay.setSpacing(0)
        tag_section_lay.addWidget(tag_header_row, 0)
        tag_section_lay.addWidget(self._tag_list, 0)

        self._tag_section.setVisible(False)

        root.addWidget(self._dept_section, 0)
        root.addWidget(self._type_section, 0)
        root.addWidget(self._tag_section, 0)
        root.addStretch(1)

        # Load from pipeline metadata (single source of truth), scoped by current mode.
        self.reload_from_pipeline_metadata()

    def dept_section(self) -> QWidget:
        return self._dept_section

    def type_section(self) -> QWidget:
        return self._type_section

    def tag_section(self) -> QWidget:
        return self._tag_section

    def reload_from_pipeline_metadata(self) -> None:
        """
        UI-only: load departments/types from pipeline metadata JSON for current mode.
        For mode "inbox": only Source (Client/Freelancer); no departments.
        """
        if self._mode == "inbox":
            self._all_types = ["client", "freelancer"]
            self._type_label_by_id = {"client": "Client", "freelancer": "Freelancer"}
            self._type_icon_by_id = {}
            self._all_departments = []
            self._dept_label_by_id = {}
            self._dept_icon_by_id = {}
            self._dept_parent = {}
            if self._active_type is not None and self._active_type not in set(self._all_types):
                self._active_type = None
            # Inbox: bắt buộc chọn một trong hai (Client/Freelancer), không cho unselect.
            if self._active_type is None and self._all_types:
                self._active_type = self._all_types[0]
            self._active_department = None
            self.set_departments([])
            self.set_types(self._all_types)
            self._dept_section.setVisible(False)
            self._type_section.setVisible(True)
            self._tag_section.setVisible(False)
            return

        if self._mode == "reference":
            # Reference page: departments only (reference, script, storyboard, guideline, concept).
            ref_depts = ["reference", "script", "storyboard", "guideline", "concept"]
            self._all_departments = ref_depts
            self._dept_label_by_id = {d: d.replace("_", " ").title() for d in ref_depts}
            self._dept_icon_by_id = {}
            self._dept_parent = {}
            self._all_types = []
            self._type_label_by_id = {}
            self._type_icon_by_id = {}
            self._dept_ids_by_type = {}
            if self._active_department is not None and self._active_department not in set(self._all_departments):
                self._active_department = None
            if self._active_department is None and self._all_departments:
                self._active_department = self._all_departments[0]
            self._active_type = None
            self.set_departments(self._all_departments)
            self.set_types([])
            self._dept_section.setVisible(True)
            self._type_section.setVisible(False)
            self._tag_section.setVisible(True)
            self._sync_tag_selection()
            return

        meta = load_pipeline_types_and_presets()

        # Types: stable ids + display names.
        types_out: list[tuple[str, str]] = []
        type_icons: dict[str, str] = {}
        # Rebuild per-type department mapping for current mode.
        self._dept_ids_by_type = {}
        for type_id, t in meta.types.items():
            if self._mode == "shots":
                if not _is_shot_type(type_id):
                    continue
            else:
                if _is_shot_type(type_id):
                    continue
            types_out.append((type_id, t.name))
            if t.icon_name:
                type_icons[type_id] = t.icon_name
            # Per-type department list (for type tabs in Select Departments dialog and sidebar filtering).
            dept_ids: list[str] = []
            for d in getattr(t, "departments", []) or []:
                if isinstance(d, str) and d.strip():
                    did = d.strip()
                    if did not in dept_ids:
                        dept_ids.append(did)
            if dept_ids:
                self._dept_ids_by_type[type_id] = dept_ids
        types_out.sort(key=lambda x: x[1].lower())
        self._all_types = [tid for tid, _ in types_out]
        self._type_label_by_id = {tid: name for tid, name in types_out}
        self._type_icon_by_id = type_icons

        # Departments: union across all types, ordered by departments definition in JSON.
        seen: set[str] = set()
        depts: list[str] = []
        dept_labels: dict[str, str] = {}
        dept_icons: dict[str, str] = {}
        dept_parent: dict[str, str] = {}
        for type_id, t in meta.types.items():
            if self._mode == "shots":
                if not _is_shot_type(type_id):
                    continue
            else:
                if _is_shot_type(type_id):
                    continue
            for d in t.departments:
                if isinstance(d, str) and d.strip() and d not in seen:
                    seen.add(d)
                    dd = meta.departments.get(d)
                    if dd is not None:
                        dept_labels[d] = dd.name
                        if dd.icon_name:
                            dept_icons[d] = dd.icon_name
                        if getattr(dd, "parent", None) and dd.parent.strip():
                            dept_parent[d] = (dd.parent or "").strip()

        # Primary order = JSON order from meta.departments keys.
        for dept_id in meta.departments.keys():
            if dept_id in seen:
                depts.append(dept_id)

        # Back-compat: departments referenced by types but missing in meta.departments.
        missing = [d for d in seen if d not in meta.departments]
        missing.sort(key=lambda s: s.lower())
        depts.extend(missing)

        self._all_departments = depts
        self._dept_label_by_id = dept_labels
        self._dept_icon_by_id = dept_icons
        self._dept_parent = dept_parent

        # If current selections are no longer valid in this mode, clear locally.
        # (No intent signals here; signals are reserved for user clicks.)
        if self._active_type is not None and self._active_type not in set(self._all_types):
            self._active_type = None
        if self._active_department is not None and self._active_department not in set(self._all_departments):
            self._active_department = None
        # When a type is active, department must be in that type's allowed list (per-type restore).
        if self._active_type and self._active_type in self._dept_ids_by_type:
            allowed = set(self._dept_ids_by_type[self._active_type])
            if self._active_department not in allowed:
                self._active_department = self._dept_ids_by_type[self._active_type][0] if self._dept_ids_by_type[self._active_type] else None

        self.set_departments(self._all_departments)
        self.set_types(self._all_types)
        self._dept_section.setVisible(True)
        self._type_section.setVisible(True)
        self._tag_section.setVisible(False)

    def current_department(self) -> str | None:
        return self._active_department

    def current_type(self) -> str | None:
        return self._active_type

    def current_tag(self) -> str | None:
        return self._active_tag

    def get_department_display(self, dept_id: str | None) -> tuple[str | None, str | None]:
        """Return (label, icon_name) for pipeline display (header + thumb badge). Subdepartment-safe."""
        did = (dept_id or "").strip() or None
        if not did:
            return (None, None)
        return (self._dept_label_by_id.get(did), self._dept_icon_by_id.get(did))

    def get_type_display(self, type_id: str | None) -> tuple[str | None, str | None]:
        """Return (label, icon_name) for pipeline type (e.g. recent task row icon)."""
        tid = (type_id or "").strip() or None
        if not tid:
            return (None, None)
        return (self._type_label_by_id.get(tid), self._type_icon_by_id.get(tid))

    def get_tag_display(self, tag_id: str | None) -> str | None:
        """Return display label for tag (for notifications)."""
        if not tag_id:
            return None
        return self._tag_label_map.get(tag_id) or tag_id

    def set_tag_item_tags(self, item_tags: dict[str, list[str]]) -> None:
        """Set item->tag_ids map from Project Guide tree (for tag count badges)."""
        self._tag_item_tags = dict(item_tags) if item_tags else {}
        for i in range(self._tag_list.count()):
            item = self._tag_list.item(i)
            if item is None:
                continue
            tid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(tid, str):
                count = len(paths_with_tag(self._tag_item_tags, tid))
                item.setData(TAG_COUNT_ROLE, count)
        self._tag_list.viewport().update()

    def set_settings(self, settings: QSettings) -> None:
        """
        Persist selections per page (assets vs shots):
        - active_department
        - active_type
        - visible_departments (max 5)
        - visible_types (max 5)
        """
        self._settings = settings
        self._load_state_for_mode("assets")
        self._load_state_for_mode("shots")
        self._load_state_for_mode("inbox")
        self._load_state_for_mode("reference")
        # Apply stored state for current mode (if any) and refresh lists.
        self._apply_state(self._state_by_mode.get(self._mode))
        self.reload_from_pipeline_metadata()

    def _settings_key(self, mode: str, field: str) -> str:
        return f"sidebar/filters/{mode}/{field}"

    def _load_state_for_mode(self, mode: str) -> None:
        if self._settings is None:
            return
        if mode not in ("assets", "shots", "inbox", "reference"):
            return

        dep = self._settings.value(self._settings_key(mode, "active_department"), "", str) if mode != "inbox" else ""
        typ = self._settings.value(self._settings_key(mode, "active_type"), "", str)
        vd_raw = self._settings.value(self._settings_key(mode, "visible_departments"), "", str)
        vt_raw = self._settings.value(self._settings_key(mode, "visible_types"), "", str)
        dbt_raw = self._settings.value(self._settings_key(mode, "department_by_type"), "", str) if mode != "inbox" else ""

        def load_list(raw: str) -> list[str] | None:
            s = (raw or "").strip()
            if not s:
                return None
            try:
                data = json.loads(s)
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, str) and x.strip()]
            except json.JSONDecodeError:
                pass
            return None

        def load_department_by_type(raw: str) -> dict[str, str | None]:
            s = (raw or "").strip()
            if not s:
                return {}
            try:
                data = json.loads(s)
                if isinstance(data, dict):
                    return {k: (v if isinstance(v, str) and v.strip() else None) for k, v in data.items() if isinstance(k, str) and k.strip()}
            except json.JSONDecodeError:
                pass
            return {}

        state: dict[str, object] = {
            "active_department": dep.strip() if dep and dep.strip() else None,
            "active_type": typ.strip() if typ and typ.strip() else None,
            "department_by_type": load_department_by_type(dbt_raw) if mode in ("assets", "shots") else {},
            "visible_departments": load_list(vd_raw) if mode != "inbox" else None,
            "visible_types": load_list(vt_raw),
        }
        self._state_by_mode[mode] = state

    def _save_state_for_mode(self, mode: str) -> None:
        if self._settings is None:
            return
        if mode not in ("assets", "shots", "inbox", "reference"):
            return
        state = self._state_by_mode.get(mode)
        if not state:
            state = self._snapshot_state()
            self._state_by_mode[mode] = state
        self._settings.setValue(self._settings_key(mode, "active_department"), state.get("active_department") or "")
        self._settings.setValue(self._settings_key(mode, "active_type"), state.get("active_type") or "")
        if mode in ("assets", "shots"):
            dbt = state.get("department_by_type")
            self._settings.setValue(
                self._settings_key(mode, "department_by_type"),
                json.dumps(dbt if isinstance(dbt, dict) else {}, ensure_ascii=False),
            )
        self._settings.setValue(
            self._settings_key(mode, "visible_departments"),
            json.dumps(state.get("visible_departments"), ensure_ascii=False),
        )
        self._settings.setValue(
            self._settings_key(mode, "visible_types"),
            json.dumps(state.get("visible_types"), ensure_ascii=False),
        )

    def set_mode(self, mode: str) -> None:
        """
        UI-only: switch between assets/shots/inbox modes.
        Inbox: only Source (Client/Freelancer) list; no departments.
        """
        m = (mode or "").strip().lower()
        if m not in ("assets", "shots", "inbox", "reference"):
            return
        if self._mode == m:
            return
        # Snapshot outgoing mode state.
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

        # Switch + restore incoming mode state (or defaults).
        self._mode = m
        self._apply_state(self._state_by_mode.get(self._mode))
        self.reload_from_pipeline_metadata()
        self._save_state_for_mode(self._mode)

    def _snapshot_state(self) -> dict[str, object]:
        return {
            "active_department": self._active_department,
            "active_type": self._active_type,
            "department_by_type": dict(self._department_by_type),
            "visible_departments": list(self._visible_departments) if self._visible_departments is not None else None,
            "visible_types": list(self._visible_types) if self._visible_types is not None else None,
        }

    def _apply_state(self, state: dict[str, object] | None) -> None:
        if not state:
            self._active_department = None
            self._active_type = None
            self._department_by_type = {}
            self._visible_departments = None
            self._visible_types = None
            return
        self._active_type = state.get("active_type") if isinstance(state.get("active_type"), str) else None
        dbt = state.get("department_by_type")
        if isinstance(dbt, dict):
            self._department_by_type = {k: v if isinstance(v, str) and v.strip() else None for k, v in dbt.items() if isinstance(k, str) and k.strip()}
        else:
            self._department_by_type = {}
        # Restore department for current type when available; else fallback to legacy active_department.
        if self._active_type and self._active_type in self._department_by_type and self._department_by_type[self._active_type]:
            self._active_department = self._department_by_type[self._active_type]
        else:
            self._active_department = state.get("active_department") if isinstance(state.get("active_department"), str) else None

        vd = state.get("visible_departments")
        if vd is None:
            self._visible_departments = None
        elif isinstance(vd, list):
            self._visible_departments = [x for x in vd if isinstance(x, str) and x.strip()]

        vt = state.get("visible_types")
        if vt is None:
            self._visible_types = None
        elif isinstance(vt, list):
            self._visible_types = [x for x in vt if isinstance(x, str) and x.strip()]

    def set_departments(self, values: list[str]) -> None:
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        self._all_departments = cleaned
        # If never configured, default to first N (one-time). If configured to [], keep empty.
        if self._visible_departments is None:
            self._visible_departments = cleaned[: self._max_visible]
        else:
            # Keep only still-valid items (no auto-fill).
            self._visible_departments = [v for v in self._visible_departments if v in cleaned]

        visible = self._visible_departments or []
        # When a type is active in assets/shots mode, restrict visible departments to those
        # supported by that type. If no type is active, show all departments that pass the
        # Select Departments filter.
        if self._mode in ("assets", "shots") and self._active_type and self._active_type in self._dept_ids_by_type:
            allowed = set(self._dept_ids_by_type.get(self._active_type, []))
            visible = [d for d in visible if d in allowed]
        parents_with_children = {
            self._dept_parent[d] for d in visible if self._dept_parent.get(d)
        }
        sections_emitted: set[str] = set()

        self._dept_list.blockSignals(True)
        try:
            self._dept_list.clear()
            next_dept_round_top = True  # first dept in list or after section/spacer
            for i, dept_id in enumerate(visible):
                parent_id = self._dept_parent.get(dept_id)
                is_in_section = bool(parent_id and parent_id in parents_with_children)
                # Spacer before each new container so blocks are clearly separated.
                if self._dept_list.count() > 0:
                    if is_in_section:
                        if parent_id not in sections_emitted:
                            spacer = QListWidgetItem("")
                            spacer.setData(Qt.UserRole, {"type": _DEPT_ROW_SPACER})
                            spacer.setFlags(Qt.ItemFlag.ItemIsEnabled)
                            self._dept_list.addItem(spacer)
                    else:
                        spacer = QListWidgetItem("")
                        spacer.setData(Qt.UserRole, {"type": _DEPT_ROW_SPACER})
                        spacer.setFlags(Qt.ItemFlag.ItemIsEnabled)
                        self._dept_list.addItem(spacer)
                        next_dept_round_top = True  # standalone dept after spacer

                if is_in_section and parent_id not in sections_emitted:
                    section_label = _title_case_label(
                        self._dept_label_by_id.get(parent_id, parent_id)
                    )
                    section_item = QListWidgetItem("")
                    section_item.setData(
                        Qt.UserRole,
                        {"type": _DEPT_ROW_SECTION, "section_label": section_label},
                    )
                    section_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self._dept_list.addItem(section_item)
                    sections_emitted.add(parent_id)
                    next_dept_round_top = False  # subdept đầu nối liền section, không bo góc trên

                # Last in block: no next dept or next dept has different parent
                if i + 1 >= len(visible):
                    last_in_block = True
                else:
                    next_parent = self._dept_parent.get(visible[i + 1])
                    last_in_block = next_parent != parent_id

                label = _title_case_label(self._dept_label_by_id.get(dept_id, dept_id))
                count = self._count_by_department.get(dept_id)
                it = QListWidgetItem(label)
                role: dict = {
                    "type": _DEPT_ROW_DEPT,
                    "dept_id": dept_id,
                    "round_top": next_dept_round_top,
                    "round_bottom": last_in_block,
                }
                if count is not None:
                    role["count"] = count
                it.setData(Qt.UserRole, role)
                next_dept_round_top = False
                icon_name = self._dept_icon_by_id.get(dept_id)
                if icon_name:
                    it.setIcon(_lucide_two_state_icon(icon_name, fallback_name="layers"))
                self._dept_list.addItem(it)
            self._sync_selection()
        finally:
            self._dept_list.blockSignals(False)

    def set_types(self, values: list[str]) -> None:
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        self._all_types = cleaned
        if self._visible_types is None:
            self._visible_types = cleaned[: self._max_visible]
        else:
            self._visible_types = [v for v in self._visible_types if v in cleaned]
        # After Assets→Shots→Assets restore, ensure all types are visible so user can select any (e.g. Character).
        if self._mode in ("assets", "shots") and cleaned and (not self._visible_types or len(self._visible_types) < len(cleaned)):
            self._visible_types = cleaned[: self._max_visible]

        visible = self._visible_types or []
        # Group by section: Assets vs Shots (same structure as department list).
        asset_types = [t for t in visible if not _is_shot_type(t)]
        shot_types = [t for t in visible if _is_shot_type(t)]
        sections_with_types: list[tuple[str, list[str]]] = []
        if asset_types:
            sections_with_types.append(("Assets", asset_types))
        if shot_types:
            sections_with_types.append(("Shots", shot_types))

        self._type_list.blockSignals(True)
        try:
            self._type_list.clear()
            next_type_round_top = True
            for sec_idx, (section_label, type_ids) in enumerate(sections_with_types):
                if self._type_list.count() > 0:
                    spacer = QListWidgetItem("")
                    spacer.setData(Qt.UserRole, {"type": _DEPT_ROW_SPACER})
                    spacer.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self._type_list.addItem(spacer)
                    next_type_round_top = True
                section_item = QListWidgetItem("")
                section_item.setData(
                    Qt.UserRole,
                    {"type": _DEPT_ROW_SECTION, "section_label": section_label},
                )
                section_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._type_list.addItem(section_item)
                next_type_round_top = False
                for i, type_id in enumerate(type_ids):
                    last_in_block = i + 1 >= len(type_ids)
                    label = _title_case_label(self._type_label_by_id.get(type_id, type_id))
                    count = self._count_by_type.get(type_id)
                    it = QListWidgetItem(label)
                    role = {
                        "type": _DEPT_ROW_DEPT,
                        "dept_id": type_id,
                        "round_top": next_type_round_top,
                        "round_bottom": last_in_block,
                    }
                    if count is not None:
                        role["count"] = count
                    it.setData(Qt.UserRole, role)
                    next_type_round_top = False
                    icon_name = self._type_icon_by_id.get(type_id)
                    if icon_name:
                        it.setIcon(_lucide_two_state_icon(icon_name, fallback_name="folder"))
                    self._type_list.addItem(it)
            self._sync_selection()
        finally:
            self._type_list.blockSignals(False)

    def set_item_counts(
        self,
        count_by_type: dict[str, int] | None = None,
        count_by_department: dict[str, int] | None = None,
    ) -> None:
        """Set counts for types and departments (from ProjectIndex). None = clear counts."""
        self._count_by_type = dict(count_by_type) if count_by_type is not None else {}
        self._count_by_department = dict(count_by_department) if count_by_department is not None else {}

    def refresh_list_counts(self) -> None:
        """Rebuild department and type lists so counts are visible. Call after set_item_counts."""
        if self._all_departments:
            self.set_departments(self._all_departments)
        if self._all_types:
            self.set_types(self._all_types)

    def set_selected_department(self, dept_id: str | None, *, emit: bool = True) -> None:
        """Set department selection. If emit=True, emit departmentClicked (user click).
        Use emit=False when syncing from Recent Task so controller does not treat same dept as toggle-off."""
        self._active_department = (dept_id or "").strip() or None
        self._sync_selection()
        if emit:
            self.departmentClicked.emit(self._active_department)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def _sync_selection(self) -> None:
        self._dept_list.blockSignals(True)
        self._type_list.blockSignals(True)
        try:
            self._dept_list.clearSelection()
            self._type_list.clearSelection()

            if self._active_department is not None:
                for i in range(self._dept_list.count()):
                    it = self._dept_list.item(i)
                    if it is None:
                        continue
                    data = it.data(Qt.UserRole)
                    if isinstance(data, dict) and data.get("type") == _DEPT_ROW_DEPT:
                        if data.get("dept_id") == self._active_department:
                            it.setSelected(True)
                            self._dept_list.setCurrentItem(it)
                            break

            if self._active_type is not None:
                for i in range(self._type_list.count()):
                    it = self._type_list.item(i)
                    if it is None:
                        continue
                    data = it.data(Qt.UserRole)
                    if isinstance(data, dict) and data.get("type") == _DEPT_ROW_DEPT:
                        if data.get("dept_id") == self._active_type:
                            it.setSelected(True)
                            self._type_list.setCurrentItem(it)
                            break
        finally:
            self._dept_list.blockSignals(False)
            self._type_list.blockSignals(False)

    def _on_department_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict) or data.get("type") != _DEPT_ROW_DEPT:
            return
        clicked = data.get("dept_id") if isinstance(data.get("dept_id"), str) else None
        if clicked is None:
            return
        if clicked == self._active_department:
            self._active_department = None
            self._sync_selection()
            self.departmentClicked.emit(None)
            self._state_by_mode[self._mode] = self._snapshot_state()
            self._save_state_for_mode(self._mode)
            return
        self._active_department = clicked
        if self._active_type:
            self._department_by_type[self._active_type] = clicked
        self._sync_selection()
        self.departmentClicked.emit(clicked)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def _on_type_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict) or data.get("type") != _DEPT_ROW_DEPT:
            return
        clicked = data.get("dept_id") if isinstance(data.get("dept_id"), str) else None
        if clicked is None:
            return
        # Inbox: không cho unselect type (bắt buộc Client hoặc Freelancer).
        if clicked == self._active_type and self._mode != "inbox":
            # Save current department for this type before clearing type.
            if self._active_type:
                self._department_by_type[self._active_type] = self._active_department
            # Toggle off current type (show all departments subject to Select Departments filter).
            self._active_type = None
            self.set_departments(self._all_departments)
            self._sync_selection()
            self.typeClicked.emit(None)
            self._state_by_mode[self._mode] = self._snapshot_state()
            self._save_state_for_mode(self._mode)
            return
        if clicked == self._active_type:
            return
        # Save current department for current type before switching.
        if self._active_type:
            self._department_by_type[self._active_type] = self._active_department
        self._active_type = clicked
        # Restore department for the new type; validate it is allowed for this type.
        allowed_list = self._dept_ids_by_type.get(clicked, [])
        allowed_set = set(allowed_list)
        restored = self._department_by_type.get(clicked) if clicked else None
        if restored and restored in allowed_set:
            self._active_department = restored
        else:
            self._active_department = allowed_list[0] if allowed_list else None
        # When switching type, refresh department list to show only departments for this type
        # that also pass the Select Departments visibility filter.
        self.set_departments(self._all_departments)
        self._sync_selection()
        self.typeClicked.emit(clicked)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def _on_tag_clicked(self, item: QListWidgetItem) -> None:
        tag_id = item.data(Qt.ItemDataRole.UserRole)  # str | None
        if tag_id == self._active_tag:
            self._active_tag = None
            self._sync_tag_selection()
            self.tagClicked.emit(None)
            return
        self._active_tag = tag_id
        self._sync_tag_selection()
        self.tagClicked.emit(tag_id)

    def _sync_tag_selection(self) -> None:
        if self._active_tag is None:
            self._tag_list.clearSelection()
            self._tag_list.setCurrentRow(-1)
        for i in range(self._tag_list.count()):
            item = self._tag_list.item(i)
            if item is None:
                continue
            tid = item.data(Qt.ItemDataRole.UserRole)
            is_active = tid == self._active_tag
            if is_active:
                item.setForeground(QColor(MONOS_COLORS.get("blue_400", "#60a5fa")))
            else:
                item.setForeground(QColor(MONOS_COLORS.get("text_body", "#d4d4d8")))

    def set_project_root(self, project_root: Path | None) -> None:
        self._project_root = project_root
        if project_root is not None:
            self._tag_definitions = read_tag_definitions(project_root)
        else:
            self._tag_definitions = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map = build_color_map(self._tag_definitions)
        self._tag_label_map = build_label_map(self._tag_definitions)
        all_ids = [d["id"] for d in self._tag_definitions]
        self._visible_tags = [tid for tid in self._visible_tags if tid in set(all_ids)]
        for tid in all_ids:
            if tid not in self._visible_tags:
                self._visible_tags.append(tid)
        self._rebuild_tag_list()

    def _rebuild_tag_list(self) -> None:
        self._tag_list.clear()
        f_tag = monos_font("Inter", 10, QFont.Weight.Normal)
        visible_set = set(self._visible_tags)
        for tdef in self._tag_definitions:
            tid = tdef["id"]
            if tid not in visible_set:
                continue
            item = QListWidgetItem(tdef["label"])
            item.setData(Qt.ItemDataRole.UserRole, tid)
            count = len(paths_with_tag(self._tag_item_tags, tid))
            item.setData(TAG_COUNT_ROLE, count)
            item.setFont(f_tag)
            item.setForeground(QColor(MONOS_COLORS.get("text_body", "#d4d4d8")))
            item.setIcon(self._tag_dot_icon(tdef["color"]))
            self._tag_list.addItem(item)
        row_h = self._tag_list.sizeHintForRow(0) if self._tag_list.count() > 0 else 26
        self._tag_list.setFixedHeight(row_h * self._tag_list.count() + 4)
        self._sync_tag_selection()

    @staticmethod
    def _tag_dot_icon(color_hex: str) -> QIcon:
        src = lucide_icon("tag-filled", size=12, color_hex=color_hex)
        src_px = src.pixmap(12, 12)
        canvas = QPixmap(20, 16)
        canvas.fill(QColor(0, 0, 0, 0))
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(2, 2, src_px)
        p.end()
        return QIcon(canvas)

    def _open_tag_picker(self) -> None:
        # Use top-level window as parent so dialog survives when filter is in compact popup (reparent).
        dlg = _TagPickerDialog(
            tag_definitions=self._tag_definitions,
            visible_tags=list(self._visible_tags),
            project_root=self._project_root,
            parent=self.window(),
        )
        if dlg.exec() != QDialog.Accepted:
            return
        self._tag_definitions = dlg.tag_definitions()
        self._tag_color_map = build_color_map(self._tag_definitions)
        self._tag_label_map = build_label_map(self._tag_definitions)
        self._visible_tags = dlg.visible_tag_ids()
        if self._active_tag and self._active_tag not in {d["id"] for d in self._tag_definitions}:
            self._active_tag = None
            self.tagClicked.emit(None)
        self._rebuild_tag_list()
        self.tagsDefinitionsChanged.emit()

    def _open_department_picker(self) -> None:
        # Build per-type tabs for departments: each tab shows departments that type supports.
        type_tabs: list[tuple[str, str]] = []
        dept_ids_by_type: dict[str, list[str]] = {}
        if self._mode in ("assets", "shots") and self._dept_ids_by_type:
            for type_id in self._all_types:
                dept_list = [d for d in self._dept_ids_by_type.get(type_id, []) if d in self._all_departments]
                if not dept_list:
                    continue
                label = self._type_label_by_id.get(type_id, type_id)
                type_tabs.append((type_id, label))
                dept_ids_by_type[type_id] = dept_list

        # Use top-level window as parent so dialog survives when filter is in compact popup (reparent).
        dlg = _FilterPickDialog(
            title="Select Departments",
            items=[(d, _title_case_label(self._dept_label_by_id.get(d, d)), self._dept_icon_by_id.get(d)) for d in self._all_departments],
            selected=set(self._visible_departments or []),
            max_selected=None,
            parent=self.window(),
            dept_parent=self._dept_parent,
            dept_label_by_id=self._dept_label_by_id,
            type_section_by_id=None,
            list_min_height_px=_FILTER_PICK_LIST_MIN_HEIGHT_DEPT_PX,
            type_tabs=type_tabs if type_tabs else None,
            dept_ids_by_type=dept_ids_by_type if dept_ids_by_type else None,
            current_type_id=self._active_type,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        picked = dlg.selected_items()
        self._visible_departments = picked
        # If active selection is no longer visible, clear and emit intent.
        if self._active_department is not None and self._active_department not in set(picked):
            self._active_department = None
            self.departmentClicked.emit(None)
        self.set_departments(self._all_departments)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def _open_type_picker(self) -> None:
        type_section = {tid: ("Shots" if _is_shot_type(tid) else "Assets") for tid in self._all_types}
        # Use top-level window as parent so dialog survives when filter is in compact popup (reparent).
        dlg = _FilterPickDialog(
            title="Select Types",
            items=[
                (tid, _title_case_label(self._type_label_by_id.get(tid, tid)), self._type_icon_by_id.get(tid))
                for tid in self._all_types
            ],
            selected=set(self._visible_types or []),
            max_selected=None,
            parent=self.window(),
            type_section_by_id=type_section,
            list_min_height_px=_FILTER_PICK_LIST_MIN_HEIGHT_TYPE_PX,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        picked = dlg.selected_items()
        self._visible_types = picked
        if self._active_type is not None and self._active_type not in set(picked):
            self._active_type = None
            self.typeClicked.emit(None)
        self.set_types(self._all_types)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    @staticmethod
    def _fit_list_height(w: QListWidget) -> None:
        """
        No-scroll policy: make the list tall enough to show all items.
        Sums row heights (supports variable heights for section/separator/dept rows).
        """
        rows = int(w.count())
        if rows <= 0:
            w.setFixedHeight(0)
            return

        total_h = sum(max(1, int(w.sizeHintForRow(i))) for i in range(rows))
        extra = 2 * int(w.frameWidth()) + 12 + 4
        w.setFixedHeight(total_h + extra)


# Default list min heights for picker dialogs (from SIDEBAR_DEPT_LIST_STYLE usage).
_FILTER_PICK_LIST_MIN_HEIGHT_DEPT_PX = 580  # department dialog: double height
_FILTER_PICK_LIST_MIN_HEIGHT_TYPE_PX = 240   # type dialog: unchanged


class _FilterPickDialog(MonosDialog):
    """
    Picker dialog for Departments / Types.
    Same container/section/spacer/row structure as sidebar list when dept_parent or type_section_by_id is set.
    """

    def __init__(
        self,
        *,
        title: str,
        items: list[tuple[str, str, str | None]],  # (id, label, icon_name)
        selected: set[str],
        max_selected: int | None,
        parent=None,
        dept_parent: dict[str, str] | None = None,
        dept_label_by_id: dict[str, str] | None = None,
        type_section_by_id: dict[str, str] | None = None,
        list_min_height_px: int | None = None,
        type_tabs: list[tuple[str, str]] | None = None,  # (type_id, label) for department tabs
        dept_ids_by_type: dict[str, list[str]] | None = None,
        current_type_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("SidebarFilterPickDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._items: list[tuple[str, str, str | None]] = [
            (i, lbl, (ic.strip() if isinstance(ic, str) and ic.strip() else None))
            for (i, lbl, ic) in items
            if isinstance(i, str) and i.strip() and isinstance(lbl, str) and lbl.strip()
        ]
        _selected = set(selected)
        self._dept_parent = dept_parent or {}
        self._dept_label_by_id = dept_label_by_id or {}
        self._type_section_by_id = type_section_by_id or {}
        # Optional per-type tabs for department picker.
        self._type_tabs: list[tuple[str, str]] = list(type_tabs or [])
        self._dept_ids_by_type: dict[str, list[str]] = {
            tid: list(ids) for tid, ids in (dept_ids_by_type or {}).items()
        }
        self._current_type_id: str | None = None
        if self._type_tabs and self._dept_ids_by_type:
            if current_type_id and current_type_id in self._dept_ids_by_type:
                self._current_type_id = current_type_id
            elif self._type_tabs:
                self._current_type_id = self._type_tabs[0][0]
        # Global selection set (used when type tabs are enabled).
        self._selected_ids: set[str] = set(_selected)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._hint = QLabel("", self)
        self._hint.setObjectName("SidebarFilterPickHint")

        self._list = QListWidget(self)
        self._list.setObjectName("SelectableListMulti")
        self._list.setSelectionMode(QAbstractItemView.MultiSelection)
        self._list.setFocusPolicy(Qt.StrongFocus)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setIconSize(QSize(16, 16))
        self._list.selectionModel().selectionChanged.connect(self._sync_hint)

        # Optional type tabs row (for Select Departments dialog).
        tabs_row: QWidget | None = None
        if self._type_tabs and self._dept_ids_by_type and self._current_type_id:
            tabs_row = QWidget(self)
            tabs_row_l = QHBoxLayout(tabs_row)
            tabs_row_l.setContentsMargins(0, 0, 0, 0)
            tabs_row_l.setSpacing(4)
            self._tab_buttons: list[QPushButton] = []
            self._tab_group = QButtonGroup(tabs_row)
            for tid, label in self._type_tabs:
                btn = QPushButton(label, tabs_row)
                btn.setObjectName("Tier3Pill")
                btn.setCheckable(True)
                btn.setFlat(True)
                btn.setChecked(tid == self._current_type_id)
                btn.clicked.connect(lambda _c=False, type_id=tid: self._on_tab_clicked(type_id))
                self._tab_group.addButton(btn)
                tabs_row_l.addWidget(btn, 0)
                self._tab_buttons.append(btn)
            tabs_row_l.addStretch(1)

        # Build initial list content.
        use_structured = bool(self._dept_parent) or bool(self._type_section_by_id)
        if self._type_tabs and self._dept_ids_by_type and self._current_type_id:
            # Tabbed department picker: build list for current type only.
            self._list.setUniformItemSizes(False)
            self._list.setItemDelegate(_SidebarDeptListDelegate(self._list))
            self._rebuild_tab_list(self._current_type_id)
        else:
            if use_structured:
                self._list.setUniformItemSizes(False)
                self._list.setItemDelegate(_SidebarDeptListDelegate(self._list))
                self._build_structured_list(_selected)
            else:
                self._build_flat_list(_selected)

        if list_min_height_px is not None:
            self._list.setMinimumHeight(list_min_height_px)
            self._list.setMaximumHeight(list_min_height_px)

        # Buttons
        btn_row = QWidget(self)
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(8)

        btn_l.addStretch(1)
        cancel = QPushButton("Cancel", btn_row)
        cancel.setObjectName("SidebarFilterPickCancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Done", btn_row)
        ok.setObjectName("SidebarFilterPickDone")
        ok.clicked.connect(self.accept)
        btn_l.addWidget(cancel, 0)
        btn_l.addWidget(ok, 0)

        root.addWidget(self._hint, 0)
        if tabs_row is not None:
            root.addWidget(tabs_row, 0)
        root.addWidget(self._list, 1)
        root.addWidget(btn_row, 0)

        self._sync_hint()

    def _build_flat_list(self, _selected: set[str]) -> None:
        for item_id, label, icon_name in self._items:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, item_id)
            if icon_name:
                ic = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_label"])
                if ic.isNull():
                    ic = lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"])
                it.setIcon(ic)
            self._list.addItem(it)
            if item_id in _selected:
                it.setSelected(True)

    def _flush_current_tab_selection(self) -> None:
        """
        Write current list selection state into _selected_ids for the current tab's
        departments. Call before switching tab so we don't lose selections.
        """
        if not self._type_tabs or not self._dept_ids_by_type or not self._current_type_id:
            return
        allowed = set(self._dept_ids_by_type.get(self._current_type_id, []))
        for i in range(self._list.count()):
            it = self._list.item(i)
            if not it:
                continue
            v = it.data(Qt.UserRole)
            if not isinstance(v, dict) or v.get("type") != _DEPT_ROW_DEPT:
                continue
            dept_id = v.get("dept_id")
            if not isinstance(dept_id, str) or dept_id not in allowed:
                continue
            if it.isSelected():
                self._selected_ids.add(dept_id)
            else:
                self._selected_ids.discard(dept_id)

    def _rebuild_tab_list(self, type_id: str) -> None:
        """
        Rebuild department list for a given type tab.
        Uses the same structured department layout but limits items to departments
        supported by the selected type. Selection is driven by self._selected_ids.
        Blocks list signals during build so _sync_hint does not run on partial state.
        """
        allowed = set(self._dept_ids_by_type.get(type_id, []))
        sm = self._list.selectionModel()
        if sm:
            sm.blockSignals(True)
        try:
            self._list.clear()
            if not allowed:
                return
            original_items = self._items
            self._items = [tup for tup in original_items if tup[0] in allowed]
            try:
                self._build_structured_list_depts(self._selected_ids)
            finally:
                self._items = original_items
        finally:
            if sm:
                sm.blockSignals(False)
        self._sync_hint()

    def _build_structured_list(self, _selected: set[str]) -> None:
        if self._type_section_by_id:
            self._build_structured_list_types(_selected)
        else:
            self._build_structured_list_depts(_selected)

    def _build_structured_list_depts(self, _selected: set[str]) -> None:
        visible = [i[0] for i in self._items]
        parents_with_children = {self._dept_parent[d] for d in visible if self._dept_parent.get(d)}
        sections_emitted: set[str] = set()
        next_round_top = True
        for i, (dept_id, label, icon_name) in enumerate(self._items):
            parent_id = self._dept_parent.get(dept_id)
            is_in_section = bool(parent_id and parent_id in parents_with_children)
            if self._list.count() > 0:
                if is_in_section and parent_id not in sections_emitted:
                    spacer = QListWidgetItem("")
                    spacer.setData(Qt.UserRole, {"type": _DEPT_ROW_SPACER})
                    spacer.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self._list.addItem(spacer)
                elif not is_in_section:
                    spacer = QListWidgetItem("")
                    spacer.setData(Qt.UserRole, {"type": _DEPT_ROW_SPACER})
                    spacer.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self._list.addItem(spacer)
                    next_round_top = True
            if is_in_section and parent_id not in sections_emitted:
                section_label = _title_case_label(self._dept_label_by_id.get(parent_id, parent_id))
                section_item = QListWidgetItem("")
                section_item.setData(Qt.UserRole, {"type": _DEPT_ROW_SECTION, "section_label": section_label})
                section_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._list.addItem(section_item)
                sections_emitted.add(parent_id)
                next_round_top = False
            if i + 1 >= len(self._items):
                last_in_block = True
            else:
                next_parent = self._dept_parent.get(self._items[i + 1][0])
                last_in_block = next_parent != parent_id
            it = QListWidgetItem(_title_case_label(label))
            it.setData(
                Qt.UserRole,
                {"type": _DEPT_ROW_DEPT, "dept_id": dept_id, "round_top": next_round_top, "round_bottom": last_in_block},
            )
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if icon_name:
                ic = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_label"])
                if ic.isNull():
                    ic = lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"])
                it.setIcon(ic)
            self._list.addItem(it)
            if dept_id in _selected:
                it.setSelected(True)
            next_round_top = False

    def _build_structured_list_types(self, _selected: set[str]) -> None:
        asset_ids = [i[0] for i in self._items if self._type_section_by_id.get(i[0], "Assets") == "Assets"]
        shot_ids = [i[0] for i in self._items if self._type_section_by_id.get(i[0], "Assets") == "Shots"]
        item_by_id = {i[0]: i for i in self._items}
        for section_label, id_list in [("Assets", asset_ids), ("Shots", shot_ids)]:
            if not id_list:
                continue
            if self._list.count() > 0:
                spacer = QListWidgetItem("")
                spacer.setData(Qt.UserRole, {"type": _DEPT_ROW_SPACER})
                spacer.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._list.addItem(spacer)
            section_item = QListWidgetItem("")
            section_item.setData(Qt.UserRole, {"type": _DEPT_ROW_SECTION, "section_label": section_label})
            section_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(section_item)
            for idx, type_id in enumerate(id_list):
                tup = item_by_id.get(type_id)
                if not tup:
                    continue
                _, label, icon_name = tup
                last_in_block = idx + 1 >= len(id_list)
                it = QListWidgetItem(_title_case_label(label))
                it.setData(
                    Qt.UserRole,
                    {"type": _DEPT_ROW_DEPT, "dept_id": type_id, "round_top": False, "round_bottom": last_in_block},
                )
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if icon_name:
                    ic = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_label"])
                    if ic.isNull():
                        ic = lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"])
                    it.setIcon(ic)
                self._list.addItem(it)
                if type_id in _selected:
                    it.setSelected(True)

    def selected_items(self) -> list[str]:
        """Return selected item ids in list order."""
        # When type tabs are enabled, return union of selected department ids across all tabs
        # in the stable order of self._items. Otherwise, read directly from the current list.
        if self._type_tabs and self._dept_ids_by_type:
            selected: list[str] = []
            if not self._selected_ids:
                return selected
            seen: set[str] = set()
            for item_id, _label, _icon in self._items:
                if item_id in self._selected_ids and item_id not in seen:
                    selected.append(item_id)
                    seen.add(item_id)
            return selected

        out: list[str] = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if not it or not it.isSelected():
                continue
            v = it.data(Qt.UserRole)
            if isinstance(v, str) and v:
                out.append(v)
            if isinstance(v, dict) and v.get("type") == _DEPT_ROW_DEPT:
                sid = v.get("dept_id")
                if isinstance(sid, str) and sid:
                    out.append(sid)
        return out

    def _sync_hint(self) -> None:
        # Keep global selection set in sync when using type tabs.
        if self._type_tabs and self._dept_ids_by_type and self._current_type_id:
            allowed = set(self._dept_ids_by_type.get(self._current_type_id, []))
            # Update self._selected_ids based on current tab's visible rows.
            for i in range(self._list.count()):
                it = self._list.item(i)
                if not it:
                    continue
                v = it.data(Qt.UserRole)
                if isinstance(v, dict) and v.get("type") == _DEPT_ROW_DEPT:
                    dept_id = v.get("dept_id")
                    if isinstance(dept_id, str) and dept_id in allowed:
                        if it.isSelected():
                            self._selected_ids.add(dept_id)
                        else:
                            self._selected_ids.discard(dept_id)
        n = len(self.selected_items())
        self._hint.setText(f"Selected {n}")

    def _on_tab_clicked(self, type_id: str) -> None:
        """
        Handle switching between type tabs in Select Departments dialog.
        Flush current tab selection to _selected_ids before rebuilding so tabs don't overwrite each other.
        """
        if type_id == self._current_type_id:
            return
        self._flush_current_tab_selection()
        self._current_type_id = type_id
        self._rebuild_tab_list(type_id)


class _TagPickerDialog(MonosDialog):
    """
    Manage tags: toggle visibility (checkbox), right-click to rename / recolor / delete.
    """

    def __init__(
        self,
        *,
        tag_definitions: list[dict[str, str]],
        visible_tags: list[str],
        project_root: Path | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Tags")
        self.setModal(True)
        self.setObjectName("SidebarFilterPickDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._defs = [dict(d) for d in tag_definitions]
        self._visible = set(visible_tags)
        self._project_root = project_root

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._hint = QLabel("Right-click tag to rename, change color, or delete", self)
        self._hint.setObjectName("SidebarFilterPickHint")

        self._list = QListWidget(self)
        self._list.setObjectName("SelectableListMulti")
        self._list.setSelectionMode(QAbstractItemView.MultiSelection)
        self._list.setFocusPolicy(Qt.StrongFocus)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setIconSize(QSize(16, 16))
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.setMinimumHeight(260)

        self._populate()

        btn_row = QWidget(self)
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(8)

        add_btn = QPushButton("+ New Tag", btn_row)
        add_btn.setObjectName("SidebarFilterPickCancel")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_tag)

        btn_l.addWidget(add_btn, 0)
        btn_l.addStretch(1)
        cancel = QPushButton("Cancel", btn_row)
        cancel.setObjectName("SidebarFilterPickCancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Done", btn_row)
        ok.setObjectName("SidebarFilterPickDone")
        ok.clicked.connect(self.accept)
        btn_l.addWidget(cancel, 0)
        btn_l.addWidget(ok, 0)

        root.addWidget(self._hint, 0)
        root.addWidget(self._list, 1)
        root.addWidget(btn_row, 0)

    def _populate(self) -> None:
        self._list.clear()
        for d in self._defs:
            it = QListWidgetItem(d["label"])
            it.setData(Qt.UserRole, d["id"])
            it.setIcon(self._dot_icon(d["color"]))
            self._list.addItem(it)
            if d["id"] in self._visible:
                it.setSelected(True)

    @staticmethod
    def _dot_icon(color_hex: str) -> QIcon:
        return lucide_icon("tag-filled", size=14, color_hex=color_hex)

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        tag_id = item.data(Qt.UserRole)
        menu = QMenu(self)
        rename_act = menu.addAction("Rename")
        color_menu = menu.addMenu("Change Color")
        for c in TAG_COLOR_PALETTE:
            px = QPixmap(14, 14)
            px.fill(QColor(0, 0, 0, 0))
            cp = QPainter(px)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            cp.setBrush(QColor(c))
            cp.setPen(Qt.PenStyle.NoPen)
            cp.drawEllipse(1, 1, 12, 12)
            cp.end()
            act = color_menu.addAction(QIcon(px), c)
            act.setData(c)
        menu.addSeparator()
        delete_act = menu.addAction("Delete")

        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == rename_act:
            self._rename_tag(tag_id, item)
        elif chosen == delete_act:
            self._delete_tag(tag_id)
        elif chosen.data():
            self._recolor_tag(tag_id, chosen.data(), item)

    def _rename_tag(self, tag_id: str, item: QListWidgetItem) -> None:
        old_label = item.text()
        new_label, ok = QInputDialog.getText(
            self, "Rename Tag", "New name:", QLineEdit.EchoMode.Normal, old_label,
        )
        if not ok or not new_label.strip() or new_label.strip() == old_label:
            return
        for d in self._defs:
            if d["id"] == tag_id:
                d["label"] = new_label.strip()
                break
        item.setText(new_label.strip())
        if self._project_root:
            rename_tag_definition(self._project_root, tag_id, new_label.strip())

    def _recolor_tag(self, tag_id: str, new_color: str, item: QListWidgetItem) -> None:
        for d in self._defs:
            if d["id"] == tag_id:
                d["color"] = new_color
                break
        item.setIcon(self._dot_icon(new_color))
        if self._project_root:
            recolor_tag_definition(self._project_root, tag_id, new_color)

    def _delete_tag(self, tag_id: str) -> None:
        self._defs = [d for d in self._defs if d["id"] != tag_id]
        self._visible.discard(tag_id)
        self._populate()

    def _add_tag(self) -> None:
        label, ok = QInputDialog.getText(
            self, "New Tag", "Tag name:", QLineEdit.EchoMode.Normal, "",
        )
        if not ok or not label.strip():
            return
        color = TAG_COLOR_PALETTE[len(self._defs) % len(TAG_COLOR_PALETTE)]
        if self._project_root:
            _, self._defs = add_tag_definition(self._project_root, label.strip(), color)
        else:
            import uuid as _uuid
            new_id = f"tag_{_uuid.uuid4().hex[:8]}"
            self._defs.append({"id": new_id, "color": color, "label": label.strip()})
        self._visible.add(self._defs[-1]["id"])
        self._populate()

    def tag_definitions(self) -> list[dict[str, str]]:
        return list(self._defs)

    def visible_tag_ids(self) -> list[str]:
        out: list[str] = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.isSelected():
                tid = it.data(Qt.UserRole)
                if isinstance(tid, str):
                    out.append(tid)
        return out


# --- Recent Task row: icon type + item name + department icon + DCC icon (right)
_TASK_ROW_HEIGHT = 26
_TASK_ICON_SIZE = 14
_TASK_SMALL_ICON_SIZE = 12  # department + DCC icons
_TASK_ICON_GAP = 8
_TASK_RIGHT_MARGIN = 4


def _task_dcc_icon(dcc_id: str, is_selected: bool) -> QIcon:
    """DCC icon from registry brand_icon_slug; fallback lucide layers."""
    if not (dcc_id or "").strip():
        return QIcon()
    try:
        reg = get_default_dcc_registry()
        info = reg.get_dcc_info((dcc_id or "").strip())
        slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
        color = info.get("brand_color_hex") if isinstance(info, dict) else None
    except Exception:
        slug = None
        color = None
    slug = (slug or dcc_id or "").strip()
    if not slug:
        return lucide_icon("layers", size=_TASK_SMALL_ICON_SIZE, color_hex=MONOS_COLORS["text_label"])
    hex_color = (color if isinstance(color, str) else None) or (MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_label"])
    return brand_icon(slug, size=_TASK_SMALL_ICON_SIZE, color_hex=hex_color)


def _task_dept_icon(sidebar_widget: QWidget | None, dept_id: str, is_selected: bool) -> QIcon:
    """Department icon from sidebar filters pipeline (label, icon_name); fallback lucide layers."""
    if not (dept_id or "").strip() or not sidebar_widget:
        return QIcon()
    filters = getattr(sidebar_widget, "filters", None)
    if not callable(filters):
        return lucide_icon("layers", size=_TASK_SMALL_ICON_SIZE, color_hex=MONOS_COLORS["text_label"])
    try:
        panel = filters()
        _, icon_name = panel.get_department_display((dept_id or "").strip())
    except Exception:
        icon_name = None
    name = (icon_name or "").strip() or "layers"
    color = MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_label"]
    return lucide_icon(name, size=_TASK_SMALL_ICON_SIZE, color_hex=color)


class _SidebarRecentTaskDelegate(QStyledItemDelegate):
    """
    Paints one recent task row: icon (task's own type) + item name + department icon + DCC icon (right).
    UserRole = RecentTask.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        task = index.data(Qt.UserRole) if index.isValid() else None
        if not isinstance(task, RecentTask):
            style = opt.widget.style() if opt.widget else QApplication.style()
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
            return

        r = opt.rect
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        is_selected = bool(opt.state & QStyle.State_Selected)
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, opt, painter, widget)

        # Resolve sidebar (for context + department icon)
        sidebar_widget: QWidget | None = None
        w = widget
        while w:
            if getattr(w, "current_context", None) is not None and getattr(w, "filters", None) is not None:
                sidebar_widget = w
                break
            w = w.parentWidget() if hasattr(w, "parentWidget") else None

        painter.save()
        try:
            x = r.left() + 4
            cy = r.center().y()

            # 1) Icon = this task's own item type (asset type or shot), never current filter state.
            type_icon_name = "package"
            if (task.item_type or "").strip().lower() == "shot":
                type_icon_name = "clapperboard"
            else:
                asset_type_id = (getattr(task, "asset_type", "") or "").strip()
                if sidebar_widget and asset_type_id:
                    try:
                        filters = getattr(sidebar_widget, "filters", None)
                        if callable(filters):
                            panel = filters()
                            _, icon = panel.get_type_display(asset_type_id)
                            type_icon_name = (icon or "").strip() or "package"
                    except Exception:
                        type_icon_name = "package"
            color = MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_label"]
            icon = lucide_icon(type_icon_name, size=_TASK_ICON_SIZE, color_hex=color)
            if not icon.isNull():
                ir = QRect(x, cy - _TASK_ICON_SIZE // 2, _TASK_ICON_SIZE, _TASK_ICON_SIZE)
                icon.paint(painter, ir, Qt.AlignCenter, QIcon.Selected if is_selected else QIcon.Normal)
            x += _TASK_ICON_SIZE + _TASK_ICON_GAP

            # 2) Right side: department icon + DCC icon (both right-aligned)
            has_dept = bool((task.department or "").strip())
            has_dcc = bool((task.dcc or "").strip())
            right_w = 0
            if has_dcc:
                right_w += _TASK_SMALL_ICON_SIZE + _TASK_ICON_GAP
            if has_dept:
                right_w += _TASK_SMALL_ICON_SIZE
            if has_dcc or has_dept:
                right_w += _TASK_RIGHT_MARGIN

            # 3) Item name only (elided)
            text_w = max(0, r.width() - (x - r.left()) - right_w)
            name_str = (task.item_name or "").strip()
            fm = QFontMetrics(opt.font)
            elided = fm.elidedText(name_str, Qt.TextElideMode.ElideRight, text_w)
            text_rect = QRect(x, r.top(), text_w, r.height())
            primary_color = MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_primary"]
            painter.setPen(QColor(primary_color))
            painter.setFont(opt.font)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

            # 4) Department icon then DCC icon (right-aligned, dept left of DCC)
            right_x = r.right() - _TASK_RIGHT_MARGIN
            if has_dcc:
                dcc_icon = _task_dcc_icon(task.dcc, is_selected)
                if not dcc_icon.isNull():
                    ix = right_x - _TASK_SMALL_ICON_SIZE
                    iy = cy - _TASK_SMALL_ICON_SIZE // 2
                    dcc_icon.paint(painter, QRect(ix, iy, _TASK_SMALL_ICON_SIZE, _TASK_SMALL_ICON_SIZE), Qt.AlignCenter)
                right_x -= _TASK_SMALL_ICON_SIZE + _TASK_ICON_GAP
            if has_dept:
                dept_icon = _task_dept_icon(sidebar_widget, task.department, is_selected)
                if not dept_icon.isNull():
                    ix = right_x - _TASK_SMALL_ICON_SIZE
                    iy = cy - _TASK_SMALL_ICON_SIZE // 2
                    dept_icon.paint(painter, QRect(ix, iy, _TASK_SMALL_ICON_SIZE, _TASK_SMALL_ICON_SIZE), Qt.AlignCenter)
        finally:
            painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        return QSize(-1, _TASK_ROW_HEIGHT)


class Sidebar(QWidget):
    """
    MONOS Sidebar (fixed 256px) with 4 blocks:
    1) Brand + Primary Nav (top fixed)
    2) Hierarchy (scrollable): search + tree
    3) Recent Tasks (bottom of scroll) — placeholder only
    4) Global Settings (bottom fixed)
    """

    context_changed = Signal(str)  # emitted when selection changes (nav)
    context_clicked = Signal(str)  # emitted when clicking already-selected nav item
    context_menu_requested = Signal(str, object)  # (context_text, global_pos) for nav items
    project_switch_requested = Signal(str)  # project root path
    settings_requested = Signal()
    recent_task_clicked = Signal(object)  # RecentTask
    recent_task_double_clicked = Signal(object)  # RecentTask
    clear_recent_tasks_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarContainer")
        # Ensure application-level QSS background renders for this container.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumWidth(256)
        self.setMaximumWidth(256)

        self._last_context_text: str | None = None
        self._project_index: ProjectIndex | None = None
        self._projects_count: int | None = None

        self._nav_widgets: dict[str, _SidebarNavItemWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Block 1: Project switcher + separator aligned with top bar bottom (56px)
        _TOP_BAR_HEIGHT = 56
        top_block_56 = QWidget(self)
        top_block_56.setFixedHeight(_TOP_BAR_HEIGHT)
        top_block_56.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        top_block_56_layout = QVBoxLayout(top_block_56)
        top_block_56_layout.setContentsMargins(16, 11, 16, 0)
        top_block_56_layout.setSpacing(0)

        self._project_menu = MonosMenu(self, rounded=False)
        self._project_menu.setObjectName("ProjectSwitchMenu")
        self._project_menu.setWindowOpacity(1.0)
        self._project_menu_closed_at = 0.0
        self._project_menu.aboutToHide.connect(self._on_sidebar_project_menu_closed)
        _shadow = QGraphicsDropShadowEffect(self._project_menu)
        _shadow.setBlurRadius(15)
        _shadow.setOffset(0, 8)
        _shadow.setColor(QColor(0, 0, 0, int(255 * 0.40)))
        self._project_menu.setGraphicsEffect(_shadow)

        self._project_switch = QToolButton(top_block_56)
        self._project_switch.setObjectName("SidebarProjectSwitch")
        self._project_switch.setProperty("state", "empty")
        self._project_switch.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._project_switch.setIcon(self._project_dot_icon("#71717a"))
        self._project_switch.setText("SELECT PROJECT")
        self._project_switch.setPopupMode(QToolButton.InstantPopup)
        self._project_switch.setCursor(Qt.PointingHandCursor)
        self._project_switch.setFocusPolicy(Qt.NoFocus)
        self._project_switch.setFixedHeight(34)
        try:
            bf = self._project_switch.font()
            bf.setPointSize(11)
            bf.setWeight(QFont.Weight.DemiBold)
            self._project_switch.setFont(bf)
        except Exception:
            pass
        self._project_switch.clicked.connect(self._show_sidebar_project_menu)

        top_block_56_layout.addWidget(self._project_switch, 0)
        top_block_56_layout.addStretch(1)
        sep_top = QFrame(top_block_56)
        sep_top.setObjectName("SidebarNavSeparator")
        sep_top.setFrameShape(QFrame.Shape.HLine)
        sep_top.setFrameShadow(QFrame.Shadow.Sunken)
        sep_top.setFixedHeight(1)
        top_block_56_layout.addWidget(sep_top, 0)

        # --- Primary Nav (scope pill + footer nav)
        top = QWidget(self)
        top.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(16, 12, 16, 16)
        top_layout.setSpacing(12)

        self._nav = QListWidget(top)
        self._nav.setObjectName("SidebarPrimaryNav")
        self._nav.setSelectionMode(QAbstractItemView.SingleSelection)
        self._nav.setUniformItemSizes(True)
        self._nav.setSpacing(4)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._nav.setFocusPolicy(Qt.NoFocus)
        self._nav.setContextMenuPolicy(Qt.CustomContextMenu)
        self._nav.customContextMenuRequested.connect(self._on_nav_context_menu_requested)

        self._nav.currentItemChanged.connect(self._on_current_nav_item_changed)
        self._nav.itemClicked.connect(self._on_nav_item_clicked)

        self._nav.setMaximumHeight(0)
        self._nav.setVisible(False)

        # Scope pill: outside list so it is never clipped by list viewport
        self._scope_context: str = SidebarContext.ASSETS.value
        scope_pill_wrapper = QWidget(top)
        scope_pill_wrapper.setFixedHeight(44)
        scope_wrap_layout = QVBoxLayout(scope_pill_wrapper)
        scope_wrap_layout.setContentsMargins(0, 0, 0, 0)
        scope_wrap_layout.setSpacing(0)
        self._scope_pill = _SidebarScopePillWidget(parent=scope_pill_wrapper)
        self._scope_pill.segment_clicked.connect(self._on_scope_segment_clicked)
        # Limit width so pill is centered in nav block (sidebar content 224px)
        self._scope_pill.setMaximumWidth(210)
        self._scope_pill.setSizePolicy(QSizePolicy.Policy.Preferred, self._scope_pill.sizePolicy().verticalPolicy())
        scope_pill_wrapper.setContextMenuPolicy(Qt.CustomContextMenu)
        scope_pill_wrapper.customContextMenuRequested.connect(
            lambda pos: self.context_menu_requested.emit(self._scope_context, scope_pill_wrapper.mapToGlobal(pos))
        )
        scope_wrap_layout.addWidget(self._scope_pill, 0, Qt.AlignmentFlag.AlignHCenter)

        nav_container = QWidget(top)
        nav_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        nav_container_layout = QVBoxLayout(nav_container)
        nav_container_layout.setSpacing(0)
        nav_container_layout.setContentsMargins(0, 0, 0, 0)
        nav_container_layout.addWidget(scope_pill_wrapper, 0)

        # Inbox / Project Guide / Outbox (nav page buttons) — tight to scope
        self._footer_context: str | None = None
        self._footer_buttons: dict[str, QToolButton] = {}
        nav_pages_row = QWidget(nav_container)
        nav_pages_layout = QHBoxLayout(nav_pages_row)
        nav_pages_layout.setContentsMargins(0, 0, 0, 0)
        nav_pages_layout.setSpacing(10)
        nav_pages_layout.addStretch(1)
        _footer_items = (
            (SidebarContext.INBOX.value, "inbox", "Inbox"),
            (SidebarContext.PROJECT_GUIDE.value, "folder-open", "Project Guide"),
            (SidebarContext.OUTBOX.value, "send", "Outbox"),
        )
        for context_name, icon_name, tooltip_text in _footer_items:
            btn = QToolButton(nav_pages_row)
            btn.setObjectName("SidebarFooterNavButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setFixedSize(36, 36)
            btn.setToolTip(tooltip_text)
            ic = lucide_icon(icon_name, size=18, color_hex=MONOS_COLORS["text_label"])
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(18, 18))
            btn.setProperty("active", "false")
            btn.clicked.connect(lambda checked=False, c=context_name: self._on_footer_nav_clicked(c))
            self._footer_buttons[context_name] = btn
            nav_pages_layout.addWidget(btn, 0)
        nav_pages_layout.addStretch(1)
        nav_container_layout.addWidget(nav_pages_row, 0)
        top_layout.addWidget(nav_container, 0)

        sep_below_nav = QFrame(top)
        sep_below_nav.setObjectName("SidebarNavSeparator")
        sep_below_nav.setFrameShape(QFrame.Shape.HLine)
        sep_below_nav.setFrameShadow(QFrame.Shadow.Sunken)
        sep_below_nav.setFixedHeight(1)
        top_layout.addWidget(sep_below_nav, 0)
        top_layout.addSpacing(8)  # gap below nav so scroll area does not overlap
        # Cap top block height so logo + nav never stretch (margins + brand + separators + scope + nav row + spacing)
        top.setMaximumHeight(200)

        # --- Block 2: Filters (dept/type lists scroll individually; no common scroll)
        self._filters_center = QWidget(self)
        self._filters_center.setObjectName("SidebarFiltersCenter")
        self._filters_center.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        scroll_layout = QVBoxLayout(self._filters_center)
        scroll_layout.setContentsMargins(16, 8, 16, 16)  # 8px top so content doesn’t sit under nav pill
        scroll_layout.setSpacing(24)  # mt-6 between sections

        f_h = monos_font("Inter", 10, QFont.Weight.ExtraBold)  # 800
        f_h.setLetterSpacing(QFont.PercentageSpacing, 112)  # tracking-widest-ish

        # Section: FILTERS — Dept list stretches down to Types; Types+Tags snap bottom.
        self._filters = SidebarWidget(self._filters_center)
        scroll_layout.addWidget(self._filters.dept_section(), 1)
        scroll_layout.addWidget(self._filters.type_section(), 0)
        scroll_layout.addWidget(self._filters.tag_section(), 0)
        self._filters.setFixedSize(0, 0)
        self._filters.hide()

        self._sep_above_tasks = QFrame(self)
        self._sep_above_tasks.setObjectName("SidebarNavSeparator")
        self._sep_above_tasks.setFrameShape(QFrame.Shape.HLine)
        self._sep_above_tasks.setFrameShadow(QFrame.Shadow.Sunken)
        self._sep_above_tasks.setFixedHeight(1)

        # --- Block 3: Recent Tasks (header always visible; list hidden when collapsed)
        self._tasks_block = QWidget(self)
        self._tasks_block.setObjectName("SidebarRecentTasksBlock")
        _tasks_list_max = 5 * _TASK_ROW_HEIGHT + 4 * 2
        self._tasks_block_h_expanded = 12 + 20 + 8 + _tasks_list_max + 8  # margins + header + spacing + list + bottom
        self._tasks_block_h_collapsed = 12 + 20 + 8  # margins + header + bottom padding
        self._tasks_block.setFixedHeight(self._tasks_block_h_expanded)
        self._tasks_block.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        tasks_layout = QVBoxLayout(self._tasks_block)
        tasks_layout.setContentsMargins(16, 12, 16, 8)  # align with sidebar padding
        tasks_layout.setSpacing(8)

        tasks_header_row = QWidget(self._tasks_block)
        tasks_header_row.setObjectName("SidebarRecentTasksHeaderRow")
        tasks_header_layout = QHBoxLayout(tasks_header_row)
        tasks_header_layout.setContentsMargins(0, 0, 0, 0)
        tasks_header_layout.setSpacing(8)
        self._tasks_header_btn = QPushButton("RECENT TASKS", tasks_header_row)
        self._tasks_header_btn.setObjectName("SidebarRecentTasksHeaderButton")
        self._tasks_header_btn.setFlat(True)
        self._tasks_header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tasks_header_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tasks_header_btn.setFont(f_h)
        self._tasks_header_btn.clicked.connect(self._on_recent_tasks_visibility_toggled)
        tasks_header_layout.addWidget(self._tasks_header_btn, 1)
        self._tasks_clear_btn = QToolButton(tasks_header_row)
        self._tasks_clear_btn.setObjectName("SidebarRecentTasksClearButton")
        self._tasks_clear_btn.setCursor(Qt.PointingHandCursor)
        self._tasks_clear_btn.setFocusPolicy(Qt.NoFocus)
        self._tasks_clear_btn.setAutoRaise(True)
        self._tasks_clear_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._tasks_clear_btn.setFixedSize(18, 18)
        self._tasks_clear_btn.setToolTip("Clear recent tasks")
        self._tasks_clear_btn.setEnabled(False)
        _clear_icon = lucide_icon("trash-2", size=14, color_hex=MONOS_COLORS["text_label"])
        if not _clear_icon.isNull():
            self._tasks_clear_btn.setIcon(_clear_icon)
            self._tasks_clear_btn.setIconSize(QSize(14, 14))
        self._tasks_clear_btn.clicked.connect(self.clear_recent_tasks_requested.emit)
        tasks_header_layout.addWidget(self._tasks_clear_btn, 0)

        self._tasks_stacked = QStackedWidget(self._tasks_block)
        self._tasks_stacked.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._tasks_empty = QLabel("No tasks", self._tasks_block)
        self._tasks_empty.setObjectName("SidebarMutedText")
        self._tasks_list = QListWidget(self._tasks_block)
        self._tasks_list.setObjectName("SidebarRecentTasksList")
        self._tasks_list.setItemDelegate(_SidebarRecentTaskDelegate(self._tasks_list))
        self._tasks_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tasks_list.setFocusPolicy(Qt.NoFocus)
        self._tasks_list.setSpacing(2)
        self._tasks_list.setMaximumHeight(5 * _TASK_ROW_HEIGHT + 4 * 2)  # 5 tasks + gaps
        self._tasks_list.itemClicked.connect(self._on_recent_task_item_clicked)
        self._tasks_list.itemDoubleClicked.connect(self._on_recent_task_item_double_clicked)
        self._tasks_stacked.addWidget(self._tasks_empty)
        self._tasks_stacked.addWidget(self._tasks_list)

        tasks_layout.addWidget(tasks_header_row, 0)
        tasks_layout.addWidget(self._tasks_stacked, 0)

        # --- Block 4: Footer (logo + name + version, small)
        bottom = QWidget(self)
        bottom.setObjectName("SidebarBottom")
        bottom.setFixedHeight(_SIDEBAR_FOOTER_HEIGHT)
        bottom.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 4, 12, 8)
        bottom_layout.setSpacing(6)
        bottom_layout.addStretch(1)
        _logo_footer = QLabel(bottom)
        _logo_footer.setObjectName("SidebarFooterLogo")
        _logo_footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _logo_footer.setFixedSize(16, 16)
        _logo_pix_footer = _load_logo_pixmap(12, "#71717a")
        if not _logo_pix_footer.isNull():
            _logo_footer.setPixmap(_logo_pix_footer)
        _name_footer = QLabel("MONOS", bottom)
        _name_footer.setObjectName("SidebarFooterName")
        _name_footer.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        _ver_footer = QLabel(get_app_version(), bottom)
        _ver_footer.setObjectName("SidebarFooterVersion")
        _ver_footer.setFont(monos_font("JetBrains Mono", 8, QFont.Weight.Normal))
        _ver_footer.setStyleSheet("color: #52525b;")
        bottom_layout.addWidget(_logo_footer, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom_layout.addWidget(_name_footer, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom_layout.addWidget(_ver_footer, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom_layout.addStretch(1)

        root.addWidget(top_block_56, 0)
        root.addWidget(top, 0)
        root.addWidget(self._filters_center, 1)
        root.addWidget(self._sep_above_tasks, 0)
        root.addWidget(self._tasks_block, 0)
        root.addWidget(bottom, 0)

        self._apply_recent_tasks_visibility()

        # Default context: Assets (keeps existing workflow stable)
        self.set_current_context(SidebarContext.ASSETS.value)

        # Start with empty hierarchy until MainWindow provides an index.
        self.set_project_index(None)

    _RECENT_TASKS_VISIBLE_KEY = "sidebar/recent_tasks_visible"
    _APP_SETTINGS_ORG, _APP_SETTINGS_APP = "MonoStudio26", "MonoStudio26"

    def filters(self) -> SidebarWidget:
        return self._filters

    _FILTERS_CENTER_LAYOUT_INDEX = 1  # index in root layout for filters_center

    def take_filters_center(self) -> QWidget | None:
        """Remove the filter panel from sidebar layout and return it (for compact filter popup)."""
        w = self._filters_center
        lay = self.layout()
        if lay is not None:
            lay.removeWidget(w)
        return w

    def restore_filters_center(self, widget: QWidget) -> None:
        """Put the filter panel back into sidebar layout."""
        lay = self.layout()
        if lay is not None:
            lay.insertWidget(self._FILTERS_CENTER_LAYOUT_INDEX, widget)

    def _sidebar_settings(self) -> QSettings:
        return QSettings(self._APP_SETTINGS_ORG, self._APP_SETTINGS_APP)

    def _recent_tasks_visible_from_settings(self) -> bool:
        raw = self._sidebar_settings().value(self._RECENT_TASKS_VISIBLE_KEY, True)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.lower() not in ("false", "0", "no", "")
        return bool(raw)

    def _apply_recent_tasks_visibility(self) -> None:
        visible = self._recent_tasks_visible_from_settings()
        self._sep_above_tasks.setVisible(visible)
        self._tasks_stacked.setVisible(visible)
        self._tasks_block.setFixedHeight(
            self._tasks_block_h_expanded if visible else self._tasks_block_h_collapsed
        )
        if hasattr(self, "_tasks_header_btn") and self._tasks_header_btn is not None:
            self._tasks_header_btn.setToolTip("Hide list" if visible else "Show list")

    def _on_recent_tasks_visibility_toggled(self, _checked: bool = False) -> None:
        visible = self._recent_tasks_visible_from_settings()
        s = self._sidebar_settings()
        s.setValue(self._RECENT_TASKS_VISIBLE_KEY, not visible)
        s.sync()
        self._apply_recent_tasks_visibility()

    _POPUP_REOPEN_GRACE = 0.25

    def _clear_tool_button_hover(self, btn: QToolButton) -> None:
        """Clear stuck hover/pressed state (after popup closes), same as TopBar."""
        QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
        btn.setDown(False)
        try:
            st = btn.style()
            if st:
                st.unpolish(btn)
                st.polish(btn)
        except Exception:
            pass
        btn.update()

    def _show_sidebar_project_menu(self) -> None:
        """Same as noti: if menu is open, close it; if just closed (grace), don't reopen."""
        if self._project_menu.isVisible():
            self._project_menu.close()
            return
        if (time.monotonic() - self._project_menu_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        pos = self._project_switch.mapToGlobal(self._project_switch.rect().bottomLeft())
        self._project_menu.popup(pos)

    def _on_sidebar_project_menu_closed(self) -> None:
        self._project_menu_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._project_switch))

    @staticmethod
    def _project_dot_icon(color_hex: str, *, diameter: int = 6) -> QIcon:
        try:
            dpr = float(QApplication.primaryScreen().devicePixelRatio())
        except Exception:
            dpr = 1.0
        canvas = max(16, diameter + 8)
        dev_w = int(round(canvas * dpr))
        pm = QPixmap(dev_w, dev_w)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color_hex))
        cx = canvas / 2.0
        cy = canvas / 2.0
        r = diameter / 2.0
        p.drawEllipse(QRectF(cx - r, cy - r, diameter, diameter))
        p.end()
        return QIcon(pm)

    def set_projects(
        self,
        projects: list[DiscoveredProject],
        *,
        current_root: Path | None,
        status_by_root: dict[str, str] | None = None,
    ) -> None:
        self._project_menu.clear()
        current = str(current_root) if current_root else None
        if not projects:
            empty = QAction("No projects", self._project_menu)
            empty.setEnabled(False)
            self._project_menu.addAction(empty)
            self._project_switch.setEnabled(False)
            self._project_switch.setText("NO PROJECTS")
            self._project_switch.setIcon(self._project_dot_icon("#52525b"))
            self._project_switch.setProperty("state", "disabled")
            if self._project_switch.style():
                self._project_switch.style().unpolish(self._project_switch)
                self._project_switch.style().polish(self._project_switch)
            return
        self._project_switch.setEnabled(True)
        if current_root is None:
            self._project_switch.setText("SELECT PROJECT")
            self._project_switch.setIcon(self._project_dot_icon("#71717a"))
            self._project_switch.setProperty("state", "empty")
        else:
            folder_name = current_root.name or ""
            self._project_switch.setText(folder_name.upper())
            accent = project_accent_color(folder_name)
            self._project_switch.setIcon(self._project_dot_icon(accent, diameter=8))
            self._project_switch.setProperty("state", "active")
        if self._project_switch.style():
            self._project_switch.style().unpolish(self._project_switch)
            self._project_switch.style().polish(self._project_switch)
        group = QActionGroup(self._project_menu)
        group.setExclusive(True)
        for proj in projects:
            label = proj.root.name
            accent = project_accent_color(label)
            is_current = current == str(proj.root)
            dot = self._project_dot_icon(accent, diameter=8 if is_current else 6)
            act = QAction(label, self._project_menu, checkable=True)
            act.setIcon(dot)
            act.setChecked(is_current)
            if is_current:
                f = act.font()
                f.setWeight(QFont.Weight.DemiBold)
                act.setFont(f)
            act.triggered.connect(lambda checked=False, p=str(proj.root): self.project_switch_requested.emit(p))
            group.addAction(act)
            self._project_menu.addAction(act)

    def set_recent_tasks(self, tasks: list[RecentTask]) -> None:
        self._tasks_list.clear()
        self._tasks_clear_btn.setEnabled(bool(tasks))
        if not tasks:
            self._tasks_stacked.setCurrentWidget(self._tasks_empty)
            return
        self._tasks_stacked.setCurrentWidget(self._tasks_list)
        for t in tasks:
            it = QListWidgetItem("")
            it.setData(Qt.UserRole, t)
            it.setSizeHint(QSize(0, _TASK_ROW_HEIGHT))
            base_tt = f"{t.item_name}\n{t.department}" + (f" · {t.dcc}" if t.dcc else "")
            base_html = base_tt.replace("\n", "<br/>")
            hint_html = '<span style="font-size:80%; color:#71717a;">Double-click to open</span>'
            it.setToolTip(f"<html>{base_html}<br/><br/>{hint_html}</html>")
            self._tasks_list.addItem(it)
        # Focus the first (most recently opened) task
        self._tasks_list.setCurrentRow(0)

    def _on_recent_task_item_clicked(self, item: QListWidgetItem) -> None:
        task = item.data(Qt.UserRole) if item else None
        if isinstance(task, RecentTask):
            self.recent_task_clicked.emit(task)

    def _on_recent_task_item_double_clicked(self, item: QListWidgetItem) -> None:
        task = item.data(Qt.UserRole) if item else None
        if isinstance(task, RecentTask):
            self.recent_task_double_clicked.emit(task)

    def _add_nav_item(self, label: str, icon_name: str) -> None:
        it = QListWidgetItem("")
        it.setData(Qt.UserRole, label)
        it.setSizeHint(QSize(0, 36))  # keep height locked
        self._nav.addItem(it)

        w = _SidebarNavItemWidget(label, icon_name, parent=self._nav)
        self._nav.setItemWidget(it, w)
        self._nav_widgets[label] = w

    def current_context(self) -> str:
        if self._footer_context is not None:
            return self._footer_context
        return self._scope_context

    def set_current_context(self, context_name: str) -> None:
        if context_name in (SidebarContext.PROJECTS.value, SidebarContext.SHOTS.value, SidebarContext.ASSETS.value):
            self._footer_context = None
            self._scope_context = context_name
            self._scope_pill.set_active_segment(context_name)
            self._previous_context_text = getattr(self, "_last_context_text", None)
            self._last_context_text = context_name
            self._sync_nav_active_states()
            if context_name == SidebarContext.PROJECTS.value:
                self._filters.setVisible(False)
            else:
                self._filters.setVisible(True)
                if context_name == SidebarContext.SHOTS.value:
                    self._filters.set_mode("shots")
                elif context_name == SidebarContext.ASSETS.value:
                    self._filters.set_mode("assets")
            self.context_changed.emit(context_name)
            return
        if context_name in (SidebarContext.INBOX.value, SidebarContext.PROJECT_GUIDE.value, SidebarContext.OUTBOX.value):
            self._footer_context = context_name
            self._nav.setCurrentRow(-1)
            self._previous_context_text = getattr(self, "_last_context_text", None)
            self._last_context_text = context_name
            self._sync_nav_active_states()
            if context_name == SidebarContext.INBOX.value:
                self._filters.setVisible(True)
                self._filters.set_mode("inbox")
            elif context_name == SidebarContext.PROJECT_GUIDE.value:
                self._filters.setVisible(True)
                self._filters.set_mode("reference")
            elif context_name == SidebarContext.OUTBOX.value:
                self._filters.setVisible(True)
                self._filters.set_mode("inbox")  # Source filter (Client/Freelancer) like Inbox
            self.context_changed.emit(context_name)
            return

    def set_projects_count(self, value: int | None) -> None:
        # Workspace discovery can feed this (no project scans).
        self._projects_count = value
        self._sync_nav_badges()

    def set_project_index(self, project_index: ProjectIndex | None) -> None:
        """
        UI-only:
        - Keep nav badges (Assets/Shots counts) in sync from already-loaded memory.
        - Push type/department counts to filter panel for label display.
        """
        self._project_index = project_index
        self._sync_nav_badges()
        self._push_filter_counts()

    def _compute_filter_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        """Compute count_by_type and count_by_department from current project index and scope (Assets vs Shots)."""
        count_by_type: dict[str, int] = {}
        count_by_department: dict[str, int] = {}
        pi = self._project_index
        if pi is None:
            return count_by_type, count_by_department

        def norm(s: str) -> str:
            return (s or "").strip().casefold()

        ctx = self.current_context()
        filter_types = getattr(self._filters, "_all_types", []) or []
        filter_depts = getattr(self._filters, "_all_departments", []) or []

        if ctx == SidebarContext.ASSETS.value:
            by_type_norm: dict[str, int] = {}
            by_dept_norm: dict[str, int] = {}
            active_type = self._filters.current_type()
            active_type_norm = norm(active_type or "")
            for a in pi.assets:
                at = norm(a.asset_type or "")
                if at:
                    by_type_norm[at] = by_type_norm.get(at, 0) + 1
                # Department count: only assets of the currently selected type (if any), and only if dept has work file.
                if active_type_norm and at != active_type_norm:
                    continue
                for d in a.departments:
                    if not getattr(d, "work_file_exists", False):
                        continue
                    dn = norm(getattr(d, "name", None) or "")
                    if dn:
                        by_dept_norm[dn] = by_dept_norm.get(dn, 0) + 1
            for tid in filter_types:
                count_by_type[tid] = by_type_norm.get(norm(tid), 0)
            for did in filter_depts:
                count_by_department[did] = by_dept_norm.get(norm(did), 0)
        elif ctx == SidebarContext.SHOTS.value:
            shots_list = list(pi.shots)
            for type_id in filter_types:
                if _is_shot_type(type_id):
                    count_by_type[type_id] = len(shots_list)
            by_dept_norm: dict[str, int] = {}
            for s in shots_list:
                for d in s.departments:
                    if not getattr(d, "work_file_exists", False):
                        continue
                    dn = norm(getattr(d, "name", None) or "")
                    if dn:
                        by_dept_norm[dn] = by_dept_norm.get(dn, 0) + 1
            for did in filter_depts:
                count_by_department[did] = by_dept_norm.get(norm(did), 0)

        return count_by_type, count_by_department

    def _push_filter_counts(self) -> None:
        """Update filter panel with current type/department counts and refresh list labels."""
        ctx = self.current_context()
        if ctx not in (SidebarContext.ASSETS.value, SidebarContext.SHOTS.value):
            self._filters.set_item_counts(None, None)
            return
        count_by_type, count_by_department = self._compute_filter_counts()
        self._filters.set_item_counts(count_by_type, count_by_department)
        self._filters.refresh_list_counts()

    def _on_current_nav_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        self._footer_context = None
        raw = current.data(Qt.UserRole)
        context = self._scope_context if raw == _NAV_SCOPE_ITEM_ROLE else raw
        if not isinstance(context, str) or not context:
            return
        self._previous_context_text = getattr(self, "_last_context_text", None)
        self._last_context_text = context
        self._sync_nav_active_states()
        if context == SidebarContext.PROJECTS.value:
            self._filters.setVisible(False)
        else:
            self._filters.setVisible(True)
            if context == SidebarContext.SHOTS.value:
                self._filters.set_mode("shots")
            elif context == SidebarContext.ASSETS.value:
                self._filters.set_mode("assets")
            elif context == SidebarContext.OUTBOX.value:
                self._filters.set_mode("inbox")  # Source (Client/Freelancer)
        self.context_changed.emit(context)

    def _on_scope_segment_clicked(self, context_name: str) -> None:
        # Always clear nav page (footer) when a scope segment is chosen, so that
        # current_context() reflects scope. Otherwise: Inbox -> click Assets (same segment)
        # would leave _footer_context="Inbox", and clicking Inbox again would be treated as "already selected".
        was_on_nav_page = self._footer_context is not None
        self._footer_context = None
        if context_name == self._scope_context:
            self._sync_nav_active_states()
            if was_on_nav_page:
                # Was on Inbox/Project Guide/Outbox; now on scope — update filters visibility (e.g. hide DEPARTMENTS for Projects).
                if self._scope_context == SidebarContext.PROJECTS.value:
                    self._filters.setVisible(False)
                else:
                    self._filters.setVisible(True)
                    if self._scope_context == SidebarContext.SHOTS.value:
                        self._filters.set_mode("shots")
                    elif self._scope_context == SidebarContext.ASSETS.value:
                        self._filters.set_mode("assets")
                self.context_changed.emit(context_name)
            else:
                self.context_clicked.emit(context_name)
            return
        self._scope_context = context_name
        self._scope_pill.set_active_segment(context_name)
        self._previous_context_text = getattr(self, "_last_context_text", None)
        self._last_context_text = context_name
        self._sync_nav_active_states()
        if self._scope_context == SidebarContext.PROJECTS.value:
            self._filters.setVisible(False)
        else:
            self._filters.setVisible(True)
            if self._scope_context == SidebarContext.SHOTS.value:
                self._filters.set_mode("shots")
            elif self._scope_context == SidebarContext.ASSETS.value:
                self._filters.set_mode("assets")
        self.context_changed.emit(context_name)

    def _on_footer_nav_clicked(self, context_name: str) -> None:
        if context_name == self.current_context():
            self.context_clicked.emit(context_name)
            return
        self.set_current_context(context_name)

    def _on_nav_item_clicked(self, item: QListWidgetItem) -> None:
        # Emit only when clicking the already-selected item (reload). When switching, currentItemChanged
        # runs first and updates _last_context_text, so we must compare to _previous_context_text.
        context = item.data(Qt.UserRole)
        if context == _NAV_SCOPE_ITEM_ROLE:
            context = self._scope_context
        if not isinstance(context, str) or not context:
            return
        prev = getattr(self, "_previous_context_text", None)
        if context == self._last_context_text and context == prev:
            self.context_clicked.emit(context)

    def _on_nav_context_menu_requested(self, pos) -> None:
        item = self._nav.itemAt(pos)
        if item is None:
            return
        raw = item.data(Qt.UserRole)
        context = self._scope_context if raw == _NAV_SCOPE_ITEM_ROLE else raw
        if not isinstance(context, str) or not context:
            return
        self.context_menu_requested.emit(context, self._nav.viewport().mapToGlobal(pos))

    def _sync_nav_active_states(self) -> None:
        # When a footer context (Inbox / Project Guide / Outbox) is active, clear scope pill and nav so only the footer button looks active.
        if self._footer_context is not None:
            self._scope_pill.set_active_segment(None)
        else:
            self._scope_pill.set_active_segment(self._scope_context)
        for name, w in self._nav_widgets.items():
            w.set_active(name == self.current_context())
        self._sync_footer_active_states()

    def _sync_footer_active_states(self) -> None:
        ctx = self.current_context()
        for name, btn in self._footer_buttons.items():
            active = name == ctx
            btn.setProperty("active", "true" if active else "false")
            color = MONOS_COLORS["blue_400"] if active else MONOS_COLORS["text_label"]
            icon_name = "inbox" if name == SidebarContext.INBOX.value else ("folder-open" if name == SidebarContext.PROJECT_GUIDE.value else "send")
            ic = lucide_icon(icon_name, size=16, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(16, 16))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _sync_nav_badges(self) -> None:
        # Counts are UI-only, derived from already-loaded memory.
        assets_count = len(self._project_index.assets) if self._project_index is not None else None
        shots_count = len(self._project_index.shots) if self._project_index is not None else None
        self._scope_pill.set_badges(self._projects_count, shots_count, assets_count)

        for name, w in self._nav_widgets.items():
            w.set_count_badge(None)


# --- SidebarCompact: icon-only vertical sidebar for narrow windows ---

_SIDEBAR_COMPACT_WIDTH = 56
_SIDEBAR_FOOTER_HEIGHT = 32  # Same height for normal and compact footer


def _sep_line(parent: QWidget, object_name: str = "SidebarNavSeparator") -> QFrame:
    s = QFrame(parent)
    s.setObjectName(object_name)
    s.setFixedHeight(1)
    s.setFrameShape(QFrame.Shape.HLine)
    return s


class SidebarCompact(QWidget):
    """
    Icon-only vertical sidebar (56px) for narrow windows.
    Layout: project switcher → sep → scope (P/S/A) → sep → Inbox/Guide/Outbox → sep → recent tasks → stretch → sep → footer logo.
    Recent Tasks: click opens popup list.
    """

    context_changed = Signal(str)
    context_clicked = Signal(str)
    context_menu_requested = Signal(str, object)
    project_switch_requested = Signal(str)
    filter_requested = Signal()  # compact: open filter popup (MainWindow shows full filter panel)
    recent_task_clicked = Signal(object)
    recent_task_double_clicked = Signal(object)
    clear_recent_tasks_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarCompact")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumWidth(_SIDEBAR_COMPACT_WIDTH)
        self.setMaximumWidth(_SIDEBAR_COMPACT_WIDTH)

        self._scope_context: str = SidebarContext.ASSETS.value
        self._footer_context: str | None = None
        self._last_context_text: str | None = None
        self._filter_source: SidebarWidget | None = None

        self._project_menu_closed_at = 0.0
        self._project_menu = MonosMenu(self, rounded=False)
        self._project_menu.setObjectName("ProjectSwitchMenu")
        self._project_menu.setWindowOpacity(1.0)
        self._project_menu.aboutToHide.connect(self._on_compact_project_menu_closed)
        _shadow = QGraphicsDropShadowEffect(self._project_menu)
        _shadow.setBlurRadius(15)
        _shadow.setOffset(0, 8)
        _shadow.setColor(QColor(0, 0, 0, int(255 * 0.40)))
        self._project_menu.setGraphicsEffect(_shadow)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Project switcher + separator aligned with top bar bottom (56px)
        _TOP_BAR_HEIGHT = 56
        top_block_56 = QWidget(self)
        top_block_56.setFixedHeight(_TOP_BAR_HEIGHT)
        top_block_56.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        top_block_56_layout = QVBoxLayout(top_block_56)
        top_block_56_layout.setContentsMargins(0, 8, 0, 0)
        top_block_56_layout.setSpacing(0)
        self._project_switch = QToolButton(top_block_56)
        self._project_switch.setObjectName("SidebarCompactProjectSwitch")
        self._project_switch.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._project_switch.setIcon(self._project_dot_icon("#71717a"))
        self._project_switch.setFixedSize(40, 40)
        self._project_switch.setCursor(Qt.PointingHandCursor)
        self._project_switch.setFocusPolicy(Qt.NoFocus)
        self._project_switch.setToolTip("Switch project")
        self._project_switch.setPopupMode(QToolButton.InstantPopup)
        self._project_switch.clicked.connect(self._show_project_menu)
        top_block_56_layout.addWidget(self._project_switch, 0, Qt.AlignmentFlag.AlignHCenter)
        top_block_56_layout.addStretch(1)
        top_block_56_layout.addWidget(_sep_line(top_block_56), 0)
        root.addWidget(top_block_56, 0)

        # Scope: Projects, Shots, Assets (icon only)
        scope_btns: dict[str, QToolButton] = {}
        _scope_tooltips = {
            SidebarContext.PROJECTS.value: "Projects",
            SidebarContext.SHOTS.value: "Shots",
            SidebarContext.ASSETS.value: "Assets",
        }
        for ctx_name, icon_name in [
            (SidebarContext.PROJECTS.value, "folder-kanban"),
            (SidebarContext.SHOTS.value, "clapperboard"),
            (SidebarContext.ASSETS.value, "box"),
        ]:
            btn = QToolButton(self)
            btn.setObjectName("SidebarCompactScopeButton")
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            btn.setFixedSize(40, 40)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(_scope_tooltips.get(ctx_name, ""))
            ic = lucide_icon(icon_name, size=20, color_hex=MONOS_COLORS["text_label"])
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(20, 20))
            btn.setProperty("active", "false")
            btn.clicked.connect(lambda checked=False, c=ctx_name: self._on_scope_clicked(c))
            scope_btns[ctx_name] = btn
            root.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self._scope_buttons = scope_btns
        root.addWidget(_sep_line(self), 0)

        # Footer nav: Inbox, Project Guide, Outbox
        footer_btns: dict[str, QToolButton] = {}
        _footer_tooltips = {
            SidebarContext.INBOX.value: "Inbox",
            SidebarContext.PROJECT_GUIDE.value: "Project Guide",
            SidebarContext.OUTBOX.value: "Outbox",
        }
        for ctx_name, icon_name in [
            (SidebarContext.INBOX.value, "inbox"),
            (SidebarContext.PROJECT_GUIDE.value, "folder-open"),
            (SidebarContext.OUTBOX.value, "send"),
        ]:
            btn = QToolButton(self)
            btn.setObjectName("SidebarCompactFooterNavButton")
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            btn.setFixedSize(40, 40)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(_footer_tooltips.get(ctx_name, ""))
            ic = lucide_icon(icon_name, size=20, color_hex=MONOS_COLORS["text_label"])
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(20, 20))
            btn.setProperty("active", "false")
            btn.clicked.connect(lambda checked=False, c=ctx_name: self._on_footer_nav_clicked(c))
            footer_btns[ctx_name] = btn
            root.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self._footer_buttons = footer_btns
        root.addWidget(_sep_line(self), 0)

        # Filter (Departments / Types): icon opens popup — MainWindow will show full filter panel in popup
        self._filter_btn = QToolButton(self)
        self._filter_btn.setObjectName("SidebarCompactFilterButton")
        self._filter_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._filter_btn.setFixedSize(40, 40)
        self._filter_btn.setCursor(Qt.PointingHandCursor)
        self._filter_btn.setFocusPolicy(Qt.NoFocus)
        self._filter_btn.setToolTip("Departments & types")
        _fic = lucide_icon("sliders-horizontal", size=20, color_hex=MONOS_COLORS["text_label"])
        if not _fic.isNull():
            self._filter_btn.setIcon(_fic)
            self._filter_btn.setIconSize(QSize(20, 20))
        self._filter_btn.clicked.connect(self._on_filter_clicked)
        root.addWidget(self._filter_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        root.addStretch(1)

        # Recent tasks (icon only; click → popup) — at bottom above logo
        self._recent_tasks_btn = QToolButton(self)
        self._recent_tasks_btn.setObjectName("SidebarCompactRecentTasksButton")
        self._recent_tasks_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._recent_tasks_btn.setFixedSize(40, 40)
        self._recent_tasks_btn.setCursor(Qt.PointingHandCursor)
        self._recent_tasks_btn.setFocusPolicy(Qt.NoFocus)
        self._recent_tasks_btn.setToolTip("Recent tasks")
        ic = lucide_icon("calendar", size=20, color_hex=MONOS_COLORS["text_label"])
        if not ic.isNull():
            self._recent_tasks_btn.setIcon(ic)
            self._recent_tasks_btn.setIconSize(QSize(20, 20))
        self._recent_tasks_btn.clicked.connect(self._show_recent_tasks_popup)
        root.addWidget(self._recent_tasks_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        # Footer: logo icon only (same height as normal sidebar footer); full width so BG covers sidebar
        _footer_wrap = QWidget(self)
        _footer_wrap.setObjectName("SidebarCompactFooter")
        _footer_wrap.setFixedHeight(_SIDEBAR_FOOTER_HEIGHT)
        _footer_wrap.setMinimumWidth(_SIDEBAR_COMPACT_WIDTH)
        _footer_layout = QVBoxLayout(_footer_wrap)
        _footer_layout.setContentsMargins(0, 0, 0, 0)
        _footer_layout.setSpacing(0)
        _footer_layout.addStretch(1)
        _logo = QLabel(_footer_wrap)
        _logo.setObjectName("SidebarCompactFooterLogo")
        _logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _logo.setFixedSize(24, 24)
        _px = _load_logo_pixmap(20, "#71717a")
        if not _px.isNull():
            _logo.setPixmap(_px)
        _footer_layout.addWidget(_logo, 0, Qt.AlignmentFlag.AlignHCenter)
        _footer_layout.addStretch(1)
        root.addWidget(_footer_wrap, 0)

        self._recent_tasks_popup: QFrame | None = None
        self._recent_tasks_list: QListWidget | None = None
        self._recent_tasks: list[RecentTask] = []
        self._recent_tasks_popup_closed_at = 0.0

        self._sync_active_states()

    @staticmethod
    def _project_dot_icon(color_hex: str, *, diameter: int = 6) -> QIcon:
        try:
            dpr = float(QApplication.primaryScreen().devicePixelRatio())
        except Exception:
            dpr = 1.0
        canvas = max(16, diameter + 8)
        dev_w = int(round(canvas * dpr))
        pm = QPixmap(dev_w, dev_w)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color_hex))
        cx = canvas / 2.0
        cy = canvas / 2.0
        r = diameter / 2.0
        p.drawEllipse(QRectF(cx - r, cy - r, diameter, diameter))
        p.end()
        return QIcon(pm)

    def set_filter_source(self, filters: SidebarWidget | None) -> None:
        self._filter_source = filters

    def _on_filter_clicked(self) -> None:
        self.filter_requested.emit()

    def _clear_tool_button_hover(self, btn: QToolButton) -> None:
        """Clear stuck hover/pressed state (after popup closes), same as TopBar."""
        QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
        btn.setDown(False)
        try:
            st = btn.style()
            if st:
                st.unpolish(btn)
                st.polish(btn)
        except Exception:
            pass
        btn.update()

    def filters(self) -> SidebarWidget | None:
        return self._filter_source

    def current_context(self) -> str:
        if self._footer_context is not None:
            return self._footer_context
        return self._scope_context

    def set_current_context(self, context_name: str) -> None:
        if context_name in (SidebarContext.PROJECTS.value, SidebarContext.SHOTS.value, SidebarContext.ASSETS.value):
            self._footer_context = None
            self._scope_context = context_name
            self._last_context_text = context_name
            self._sync_active_states()
            self.context_changed.emit(context_name)
            return
        if context_name in (SidebarContext.INBOX.value, SidebarContext.PROJECT_GUIDE.value, SidebarContext.OUTBOX.value):
            self._footer_context = context_name
            self._last_context_text = context_name
            self._sync_active_states()
            self.context_changed.emit(context_name)

    def _sync_active_states(self) -> None:
        ctx = self.current_context()
        for name, btn in self._scope_buttons.items():
            active = name == ctx
            btn.setProperty("active", "true" if active else "false")
            color = MONOS_COLORS["blue_400"] if active else MONOS_COLORS["text_label"]
            icon_name = "folder-kanban" if name == SidebarContext.PROJECTS.value else ("clapperboard" if name == SidebarContext.SHOTS.value else "box")
            ic = lucide_icon(icon_name, size=20, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(20, 20))
            if btn.style():
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        for name, btn in self._footer_buttons.items():
            active = name == ctx
            btn.setProperty("active", "true" if active else "false")
            color = MONOS_COLORS["blue_400"] if active else MONOS_COLORS["text_label"]
            icon_name = "inbox" if name == SidebarContext.INBOX.value else ("folder-open" if name == SidebarContext.PROJECT_GUIDE.value else "send")
            ic = lucide_icon(icon_name, size=20, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(20, 20))
            if btn.style():
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def _on_scope_clicked(self, context_name: str) -> None:
        if context_name == self.current_context():
            self.context_clicked.emit(context_name)
            return
        self.set_current_context(context_name)

    def _on_footer_nav_clicked(self, context_name: str) -> None:
        if context_name == self.current_context():
            self.context_clicked.emit(context_name)
            return
        self.set_current_context(context_name)

    def _show_project_menu(self) -> None:
        """Same as noti: if menu is open, close it; if just closed (grace), don't reopen."""
        if self._project_menu.isVisible():
            self._project_menu.close()
            return
        if (time.monotonic() - self._project_menu_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        pos = self._project_switch.mapToGlobal(self._project_switch.rect().bottomLeft())
        self._project_menu.popup(pos)

    def _on_compact_project_menu_closed(self) -> None:
        self._project_menu_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._project_switch))

    def set_projects(
        self,
        projects: list[DiscoveredProject],
        *,
        current_root: Path | None,
        status_by_root: dict[str, str] | None = None,
    ) -> None:
        self._project_menu.clear()
        if not projects:
            self._project_switch.setEnabled(False)
            self._project_switch.setIcon(self._project_dot_icon("#52525b"))
            self._project_switch.setProperty("state", "disabled")
            if self._project_switch.style():
                self._project_switch.style().unpolish(self._project_switch)
                self._project_switch.style().polish(self._project_switch)
            return
        self._project_switch.setEnabled(True)
        current = str(current_root) if current_root else None
        if current_root is None:
            self._project_switch.setIcon(self._project_dot_icon("#71717a"))
            self._project_switch.setProperty("state", "empty")
        else:
            folder_name = current_root.name or ""
            accent = project_accent_color(folder_name)
            self._project_switch.setIcon(self._project_dot_icon(accent, diameter=8))
            self._project_switch.setProperty("state", "active")
        if self._project_switch.style():
            self._project_switch.style().unpolish(self._project_switch)
            self._project_switch.style().polish(self._project_switch)
        group = QActionGroup(self._project_menu)
        group.setExclusive(True)
        for proj in projects:
            label = proj.root.name
            accent = project_accent_color(label)
            is_current = current == str(proj.root)
            dot = self._project_dot_icon(accent, diameter=8 if is_current else 6)
            act = QAction(label, self._project_menu, checkable=True)
            act.setIcon(dot)
            act.setChecked(is_current)
            if is_current:
                f = act.font()
                f.setWeight(QFont.Weight.DemiBold)
                act.setFont(f)
            act.triggered.connect(lambda checked=False, p=str(proj.root): self.project_switch_requested.emit(p))
            group.addAction(act)
            self._project_menu.addAction(act)

    def set_recent_tasks(self, tasks: list[RecentTask]) -> None:
        self._recent_tasks = list(tasks) if tasks else []
        if self._recent_tasks_list is not None:
            self._recent_tasks_list.clear()
            for t in self._recent_tasks:
                it = QListWidgetItem("")
                it.setData(Qt.UserRole, t)
                it.setSizeHint(QSize(0, _TASK_ROW_HEIGHT))
                base_tt = f"{t.item_name}\n{t.department}" + (f" · {t.dcc}" if t.dcc else "")
                base_html = base_tt.replace("\n", "<br/>")
                hint_html = '<span style="font-size:80%; color:#71717a;">Double-click to open</span>'
                it.setToolTip(f"<html>{base_html}<br/><br/>{hint_html}</html>")
                self._recent_tasks_list.addItem(it)
            if self._recent_tasks_list.count():
                self._recent_tasks_list.setCurrentRow(0)

    _POPUP_REOPEN_GRACE = 0.25

    def _show_recent_tasks_popup(self) -> None:
        if not self._recent_tasks:
            return
        # Same as noti button: if popup is open, close it (toggle); if just closed, don't reopen
        if self._recent_tasks_popup is not None and self._recent_tasks_popup.isVisible():
            self._recent_tasks_popup.close()
            return
        if (time.monotonic() - self._recent_tasks_popup_closed_at) < self._POPUP_REOPEN_GRACE:
            return

        class _RecentTasksPopupFrame(QFrame):
            def __init__(self, parent, on_hide_cb):
                super().__init__(parent)
                self._on_hide_cb = on_hide_cb

            def hideEvent(self, event):
                self._on_hide_cb()
                super().hideEvent(event)

        def _on_recent_popup_hidden():
            self._recent_tasks_popup_closed_at = time.monotonic()
            self._recent_tasks_popup = None
            self._recent_tasks_list = None
            QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._recent_tasks_btn))

        popup = _RecentTasksPopupFrame(self, _on_recent_popup_hidden)
        popup.setObjectName("SidebarCompactRecentTasksPopup")
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.FramelessWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground, False)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        lst = QListWidget(popup)
        lst.setObjectName("SidebarRecentTasksList")
        lst.setItemDelegate(_SidebarRecentTaskDelegate(lst))
        lst.setSelectionMode(QAbstractItemView.SingleSelection)
        lst.setFocusPolicy(Qt.NoFocus)
        lst.setSpacing(2)
        max_h = 5 * _TASK_ROW_HEIGHT + 4 * 2
        lst.setMinimumWidth(220)
        lst.setMaximumHeight(max_h)
        for t in self._recent_tasks:
            it = QListWidgetItem("")
            it.setData(Qt.UserRole, t)
            it.setSizeHint(QSize(0, _TASK_ROW_HEIGHT))
            base_tt = f"{t.item_name}\n{t.department}" + (f" · {t.dcc}" if t.dcc else "")
            base_html = base_tt.replace("\n", "<br/>")
            hint_html = '<span style="font-size:80%; color:#71717a;">Double-click to open</span>'
            it.setToolTip(f"<html>{base_html}<br/><br/>{hint_html}</html>")
            lst.addItem(it)
        if lst.count():
            lst.setCurrentRow(0)
        lst.itemClicked.connect(self._on_popup_task_clicked)
        lst.itemDoubleClicked.connect(self._on_popup_task_double_clicked)
        self._recent_tasks_list = lst
        layout.addWidget(lst)
        clear_btn = QToolButton(popup)
        clear_btn.setText("Clear")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(lambda: (popup.close(), self.clear_recent_tasks_requested.emit()))
        layout.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignRight)
        self._recent_tasks_popup = popup
        pos = self._recent_tasks_btn.mapToGlobal(self._recent_tasks_btn.rect().bottomLeft())
        popup.move(pos.x(), pos.y() + 4)
        popup.show()

    def _on_popup_task_clicked(self, item: QListWidgetItem) -> None:
        task = item.data(Qt.UserRole) if item else None
        if isinstance(task, RecentTask):
            self.recent_task_clicked.emit(task)

    def _on_popup_task_double_clicked(self, item: QListWidgetItem) -> None:
        task = item.data(Qt.UserRole) if item else None
        if isinstance(task, RecentTask):
            self.recent_task_double_clicked.emit(task)
        if self._recent_tasks_popup:
            self._recent_tasks_popup.close()

