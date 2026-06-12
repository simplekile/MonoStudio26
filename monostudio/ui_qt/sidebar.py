from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

import time
from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QSettings
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
    QPen,
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
    normalize_tag_department_id,
    paths_with_tag,
    read_tag_definitions,
    recolor_tag_definition,
    rename_tag_definition,
    save_tag_definitions,
)
from monostudio.core.models import Asset, ProjectIndex, Shot
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.pipeline_types_and_presets import (
    department_icon_name,
    load_pipeline_types_and_presets_for_project,
    order_department_ids_grouped_by_parent,
    resolve_department_ids_for_ui,
)
from monostudio.core.app_paths import get_app_base_path
from monostudio.core.shell_open import open_folder as shell_open_folder
from monostudio.core.workspace_reader import DiscoveredProject
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import max_popup_height_for_anchor, position_popup_near_anchor
from monostudio.ui_qt.dashboard_widget_palette import DashboardWidgetPalette
from monostudio.ui_qt.recent_tasks_store import RecentTask
from monostudio.ui_qt.toolbar_separators import add_widgets_with_icon_separators, apply_pill_segment_positions
from monostudio.ui_qt.style import (
    MONOS_COLORS,
    SIDEBAR_DEPT_LIST_STYLE,
    MonosDialog,
    MonosMenu,
    clear_stuck_widget_hover,
    monos_font,
    project_accent_color,
)


class SidebarContext(str, Enum):
    PROJECTS = "Projects"
    DASHBOARD = "Dashboard"
    SHOTS = "Shots"
    ASSETS = "Assets"
    INBOX = "Inbox"
    PROJECT_GUIDE = "Project Guide"
    SCHEDULE = "Schedule"
    OUTBOX = "Outbox"
    TRASH = "Trash"


# Single nav item that holds the scope pill (Project | Shot | Asset).
_NAV_SCOPE_ITEM_ROLE = "_scope"

# Tag list: UserRole = tag_id, UserRole+1 = item count for badge
TAG_COUNT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _is_shot_type(type_id: str) -> bool:
    # Pipeline convention: shot types are "shot" or prefixed "shot_".
    return bool(type_id == "shot" or type_id.startswith("shot_"))


def _filter_mode_for_nav_context(context_name: str) -> str | None:
    """SidebarWidget mode expected for a nav page (None = filters hidden, e.g. Trash)."""
    ctx = (context_name or "").strip()
    if ctx == SidebarContext.ASSETS.value:
        return "assets"
    if ctx == SidebarContext.SHOTS.value:
        return "shots"
    if ctx == SidebarContext.SCHEDULE.value:
        return "schedule"
    if ctx == SidebarContext.DASHBOARD.value:
        return None
    if ctx in (SidebarContext.INBOX.value, SidebarContext.OUTBOX.value):
        return "inbox"
    if ctx == SidebarContext.PROJECT_GUIDE.value:
        return "reference"
    return None


def _title_case_label(value: str) -> str:
    # UI display only (ids remain unchanged). Keep simple + deterministic.
    return (value or "").strip().replace("_", " ").title()


def _sidebar_filter_list_container(parent: QWidget | None = None) -> QFrame:
    """Raised panel wrapping a sidebar filter / recent list (QSS: SidebarFilterListContainer)."""
    frame = QFrame(parent)
    frame.setObjectName("SidebarFilterListContainer")
    frame.setAttribute(Qt.WA_StyledBackground, True)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    return frame


def _update_filter_section_chevron(btn: QToolButton, *, expanded: bool) -> None:
    name = "chevron-down" if expanded else "chevron-right"
    icon = lucide_icon(name, size=12, color_hex=MONOS_COLORS["text_label"])
    if not icon.isNull():
        btn.setIcon(icon)
        btn.setIconSize(QSize(12, 12))


def _make_filter_section_chevron(parent: QWidget, *, expanded: bool, on_toggle) -> QToolButton:
    btn = QToolButton(parent)
    btn.setObjectName("SidebarFilterSectionChevron")
    btn.setAutoRaise(True)
    btn.setFixedSize(16, 16)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.clicked.connect(on_toggle)
    _update_filter_section_chevron(btn, expanded=expanded)
    return btn


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


# Row kinds for sidebar filter / recent-task tree lists.
_FILTER_ROW_GROUP = "group"
_FILTER_ROW_LEAF = "leaf"
# Legacy aliases (picker dialog still uses section/spacer in places).
_DEPT_ROW_SECTION = "section"
_DEPT_ROW_DEPT = "leaf"
_DEPT_ROW_SPACER = "spacer"

_FILTER_GROUP_KEY_SEP = ":"


def _filter_group_key(scope: str, group_id: str) -> str:
    return f"{scope}{_FILTER_GROUP_KEY_SEP}{group_id}"


def _paint_filter_count_badge(
    painter: QPainter,
    *,
    rect: QRect,
    count: int,
    selected: bool,
) -> None:
    badge_font = monos_font("Inter", 9, QFont.Weight.Medium)
    fm = QFontMetrics(badge_font)
    text = str(count)
    pad_x = 6
    badge_w = max(22, fm.horizontalAdvance(text) + pad_x * 2)
    badge_h = 18
    badge_x = rect.right() - badge_w
    badge_y = rect.center().y() - badge_h // 2
    badge_rect = QRect(badge_x, badge_y, badge_w, badge_h)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    if selected:
        painter.setBrush(QColor(37, 99, 235, 120))
        painter.setPen(QPen(QColor(96, 165, 250, 90), 1))
    else:
        painter.setBrush(QColor(39, 39, 42))
    painter.drawRoundedRect(badge_rect, 4, 4)
    painter.setFont(badge_font)
    painter.setPen(QColor(MONOS_COLORS["blue_400"] if selected else "#d4d4d8"))
    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)


def _paint_filter_chevron(
    painter: QPainter,
    *,
    rect: QRect,
    expanded: bool,
    x: int,
    cy: int,
    selected: bool = False,
) -> int:
    name = "chevron-down" if expanded else "chevron-right"
    color = MONOS_COLORS["blue_400"] if selected else MONOS_COLORS["text_label"]
    icon = lucide_icon(name, size=12, color_hex=color)
    if icon.isNull():
        return x
    ir = QRect(x, cy - 6, 12, 12)
    icon.paint(painter, ir, Qt.AlignCenter)
    return x + 12 + 4


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


def _filter_row_highlight_rect(rect: QRect) -> QRect:
    ix = int(SIDEBAR_DEPT_LIST_STYLE.get("row_highlight_inset_x", 2))
    iy = int(SIDEBAR_DEPT_LIST_STYLE.get("row_highlight_inset_y", 0))
    return rect.adjusted(ix, iy, -ix, -iy)


def _filter_row_highlight_radius() -> int:
    return int(SIDEBAR_DEPT_LIST_STYLE.get("row_highlight_radius_px", 6))


def _filter_row_content_pad_left(*, indent: int = 0) -> int:
    base = int(SIDEBAR_DEPT_LIST_STYLE.get("row_content_pad_left_px", 12))
    return base + indent * int(SIDEBAR_DEPT_LIST_STYLE.get("indent_step_px", 16))


def _filter_row_content_pad_right() -> int:
    return int(SIDEBAR_DEPT_LIST_STYLE.get("row_content_pad_right_px", 12))


def _paint_filter_lead_dot(painter: QPainter, *, x: int, cy: int, selected: bool) -> int:
    """Small circular socket before leaf row icon; returns x after dot + gap."""
    dot_r = int(SIDEBAR_DEPT_LIST_STYLE.get("row_lead_dot_radius_px", 3))
    gap = int(SIDEBAR_DEPT_LIST_STYLE.get("row_lead_dot_gap_px", 6))
    dot_color = MONOS_COLORS["blue_400"] if selected else MONOS_COLORS["text_meta"]
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(dot_color))
    painter.drawEllipse(x, cy - dot_r, dot_r * 2, dot_r * 2)
    return x + dot_r * 2 + gap


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


class _SidebarFilterTreeDelegate(QStyledItemDelegate):
    """Flat tree rows: group (chevron + toggle) and leaf (selectable). No gradients."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        data = opt.index.data(Qt.UserRole) if opt.index.isValid() else None
        if not isinstance(data, dict):
            super().paint(painter, option, index)
            return
        row_type = data.get("type")
        if row_type == _DEPT_ROW_SPACER:
            return
        if row_type == _DEPT_ROW_SECTION:
            self._paint_legacy_section(painter, opt, str(data.get("section_label", "")))
            return
        if row_type == _FILTER_ROW_GROUP:
            self._paint_group_row(painter, opt, data)
            return
        self._paint_leaf_row(painter, opt, data)

    def _paint_legacy_section(self, painter: QPainter, opt: QStyleOptionViewItem, title: str) -> None:
        if not title:
            return
        r = opt.rect
        painter.save()
        try:
            fs = int(SIDEBAR_DEPT_LIST_STYLE.get("section_font_size_px", 9))
            f = monos_font("Inter", fs, QFont.Weight.DemiBold)
            painter.setFont(f)
            painter.setPen(QColor(MONOS_COLORS["text_meta"]))
            text_r = r.adjusted(10, 0, -10, 0)
            painter.drawText(text_r, Qt.AlignVCenter | Qt.AlignLeft, title.upper())
        finally:
            painter.restore()

    def _row_metrics(self, *, selected: bool = False) -> tuple[int, int, QFont]:
        row_px = int(SIDEBAR_DEPT_LIST_STYLE.get("row_font_size_px", 11))
        icon_px = int(SIDEBAR_DEPT_LIST_STYLE.get("row_icon_size_px", 14))
        weight = QFont.Weight.DemiBold if selected else QFont.Weight.Medium
        body_font = monos_font("Inter", row_px, weight)
        return row_px, icon_px, body_font

    def _paint_group_row(self, painter: QPainter, opt: QStyleOptionViewItem, data: dict) -> None:
        r = opt.rect
        is_hovered = bool(opt.state & QStyle.State_MouseOver)
        painter.save()
        try:
            if is_hovered:
                hover = _filter_row_highlight_rect(r)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 14))
                painter.drawRoundedRect(hover, _filter_row_highlight_radius(), _filter_row_highlight_radius())
            indent = int(data.get("indent") or 0)
            pad_left = _filter_row_content_pad_left(indent=indent)
            cy = r.center().y()
            x = pad_left
            expanded = bool(data.get("expanded", True))
            x = _paint_filter_chevron(painter, rect=r, expanded=expanded, x=x, cy=cy)
            _, icon_px, body_font = self._row_metrics()
            if not opt.icon.isNull():
                ir = QRect(x, cy - icon_px // 2, icon_px, icon_px)
                opt.icon.paint(painter, ir, Qt.AlignCenter)
                x = ir.right() + 6
            count = data.get("count")
            pad_right = _filter_row_content_pad_right()
            text_right = r.right() - pad_right
            if count is not None:
                fm_b = QFontMetrics(monos_font("Inter", 10, QFont.Weight.Medium))
                text_right -= max(22, fm_b.horizontalAdvance(str(count)) + 12)
            label = opt.text or ""
            fm = QFontMetrics(body_font)
            text_rect = QRect(x, r.top(), max(0, text_right - x), r.height())
            painter.setFont(body_font)
            painter.setPen(QColor(MONOS_COLORS["text_label"]))
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, fm.elidedText(label, Qt.ElideRight, text_rect.width()))
            if count is not None:
                try:
                    _paint_filter_count_badge(painter, rect=r.adjusted(0, 0, -pad_right, 0), count=int(count), selected=False)
                except (TypeError, ValueError):
                    pass
        finally:
            painter.restore()

    def _paint_leaf_row(self, painter: QPainter, opt: QStyleOptionViewItem, data: dict) -> None:
        r = opt.rect
        scope_row = bool(data.get("scope_id"))
        if scope_row:
            is_selected = bool(data.get("scope_active"))
        else:
            is_selected = bool(opt.state & QStyle.State_Selected)
        is_hovered = bool(opt.state & QStyle.State_MouseOver)
        painter.save()
        try:
            sel_rect = _filter_row_highlight_rect(r)
            radius = _filter_row_highlight_radius()
            if is_selected:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(37, 99, 235, 100))
                painter.drawRoundedRect(sel_rect, radius, radius)
            elif is_hovered:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 14))
                painter.drawRoundedRect(sel_rect, radius, radius)
            indent = int(data.get("indent") or 0)
            pad_left = _filter_row_content_pad_left(indent=indent)
            cy = r.center().y()
            x = pad_left
            # Child rows under a group: chevron column spacer for alignment.
            if indent > 0:
                x += 16
            x = _paint_filter_lead_dot(painter, x=x, cy=cy, selected=is_selected)
            _, icon_px, body_font = self._row_metrics(selected=is_selected)
            if not opt.icon.isNull():
                ir = QRect(x, cy - icon_px // 2, icon_px, icon_px)
                opt.icon.paint(painter, ir, Qt.AlignCenter, QIcon.Selected if is_selected else QIcon.Normal)
                x = ir.right() + 6
            count = data.get("count")
            pad_right = _filter_row_content_pad_right()
            text_right = r.right() - pad_right
            if count is not None:
                fm_b = QFontMetrics(monos_font("Inter", 10, QFont.Weight.Medium))
                text_right -= max(22, fm_b.horizontalAdvance(str(count)) + 12)
            fm = QFontMetrics(body_font)
            text_rect = QRect(x, r.top(), max(0, text_right - x), r.height())
            if scope_row and not is_selected:
                pen_color = QColor(MONOS_COLORS["text_meta"])
            elif is_selected:
                pen_color = QColor(MONOS_COLORS["blue_400"])
            else:
                pen_color = QColor(MONOS_COLORS["text_label"])
            painter.setFont(body_font)
            painter.setPen(pen_color)
            painter.drawText(
                text_rect,
                Qt.AlignVCenter | Qt.AlignLeft,
                fm.elidedText(opt.text, Qt.ElideRight, text_rect.width()),
            )
            if count is not None:
                try:
                    _paint_filter_count_badge(
                        painter, rect=r.adjusted(0, 0, -pad_right, 0), count=int(count), selected=is_selected
                    )
                except (TypeError, ValueError):
                    pass
        finally:
            painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        data = index.data(Qt.UserRole) if index.isValid() else None
        if isinstance(data, dict):
            if data.get("type") == _DEPT_ROW_SECTION:
                return QSize(-1, int(SIDEBAR_DEPT_LIST_STYLE.get("section_row_height_px", 20)))
            if data.get("type") == _DEPT_ROW_SPACER:
                return QSize(-1, int(SIDEBAR_DEPT_LIST_STYLE.get("spacer_row_height_px", 4)))
        h = int(SIDEBAR_DEPT_LIST_STYLE.get("dept_row_height_px", 28))
        return QSize(-1, h)


# Back-compat alias for picker dialog.
_SidebarDeptListDelegate = _SidebarFilterTreeDelegate


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


# Scope pill: one nav block for Projects | Shot | Asset (single pill, three segments).
_SCOPE_PILL_CONTEXTS = (
    (SidebarContext.PROJECTS.value, "folder-kanban"),
    (SidebarContext.ASSETS.value, "box"),
    (SidebarContext.SHOTS.value, "clapperboard"),
)
_SCOPE_PILL_TOOLTIPS: dict[str, str] = {
    SidebarContext.PROJECTS.value: "Projects",
    SidebarContext.SHOTS.value: "Shot",
    SidebarContext.ASSETS.value: "Asset",
}
# None = icon-only segment (Projects); str = visible label beside icon.
_SCOPE_PILL_LABELS: dict[str, str | None] = {
    SidebarContext.PROJECTS.value: None,
    SidebarContext.SHOTS.value: "Shot",
    SidebarContext.ASSETS.value: "Asset",
}
_SCOPE_PILL_ICON_SIZE = 18
_SCOPE_PILL_SEGMENT_ICON_W = 36
_SCOPE_PILL_SEGMENT_LABELED_MIN_W = 52

class _SidebarScopePillWidget(QWidget):
    """
    One pill with three segments: Projects, Asset, Shot.
    Emits segment_clicked(context_name). set_active_segment(name), set_badges(projects, shots, assets).
    """

    segment_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarScopePill")
        self.setProperty("display", "mixed")
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
        _seg_font = monos_font("Inter", 13, QFont.Weight.DemiBold)
        _seg_font.setLetterSpacing(QFont.PercentageSpacing, 97)
        segment_btns: list[QToolButton] = []
        for _i, (ctx, icon_name) in enumerate(_SCOPE_PILL_CONTEXTS):
            btn = QToolButton(self)
            btn.setObjectName("SidebarScopePillSegment")
            btn.setProperty("segment", ctx)
            btn.setProperty("active", "false")
            label = _SCOPE_PILL_LABELS.get(ctx)
            btn.setProperty("labeled", "true" if label else "false")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoRaise(True)
            btn.setToolTip(_SCOPE_PILL_TOOLTIPS.get(ctx, ctx))
            ic = lucide_icon(icon_name, size=_SCOPE_PILL_ICON_SIZE, color_hex=MONOS_COLORS["pill_segment_inactive_fg"])
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(_SCOPE_PILL_ICON_SIZE, _SCOPE_PILL_ICON_SIZE))
            if label:
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                btn.setText(label)
                btn.setFont(_seg_font)
                btn.setFixedHeight(32)
                btn.setMinimumWidth(_SCOPE_PILL_SEGMENT_LABELED_MIN_W)
                btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            else:
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                btn.setFixedSize(_SCOPE_PILL_SEGMENT_ICON_W, 32)
                btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked=False, c=ctx: self.segment_clicked.emit(c))
            self._buttons[ctx] = btn
            segment_btns.append(btn)
        add_widgets_with_icon_separators(layout, segment_btns, self, sep_height=20)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_active_segment(self, context_name: str | None) -> None:
        """Set which segment is active. Pass None or unknown name to clear (no segment active)."""
        self._active = context_name or ""
        active_ctx = context_name if context_name in self._buttons else None
        for ctx, btn in self._buttons.items():
            is_active = ctx == active_ctx
            btn.setProperty("active", "true" if is_active else "false")
            color = MONOS_COLORS["pill_segment_active_fg"] if is_active else MONOS_COLORS["pill_segment_inactive_fg"]
            ic_name = next((ic for c, ic in _SCOPE_PILL_CONTEXTS if c == ctx), "box")
            ic = lucide_icon(ic_name, size=_SCOPE_PILL_ICON_SIZE, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(_SCOPE_PILL_ICON_SIZE, _SCOPE_PILL_ICON_SIZE))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_badges(self, projects_count: int | None, shots_count: int | None, assets_count: int | None) -> None:
        self._badges[SidebarContext.PROJECTS.value] = projects_count
        self._badges[SidebarContext.SHOTS.value] = shots_count
        self._badges[SidebarContext.ASSETS.value] = assets_count
        for ctx, btn in self._buttons.items():
            count = self._badges.get(ctx)
            label = _SCOPE_PILL_TOOLTIPS.get(ctx, ctx)
            tip = f"{label} ({count})" if count is not None else label
            btn.setToolTip(tip)


_SIDEBAR_TYPE_LIST_MAX_HEIGHT_PX = int(SIDEBAR_DEPT_LIST_STYLE.get("max_list_height_px", 280))
_SIDEBAR_DEPT_LIST_MAX_HEIGHT_PX = _SIDEBAR_TYPE_LIST_MAX_HEIGHT_PX
_SIDEBAR_TAG_LIST_MAX_HEIGHT_PX = _SIDEBAR_DEPT_LIST_MAX_HEIGHT_PX


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
    entityScopeChanged = Signal(bool, bool)  # include_shots, include_assets (schedule)
    tagClicked = Signal(object)  # list[str] — active tag ids (empty = clear)
    tagsDefinitionsChanged = Signal()  # emitted when user modifies tag definitions

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MetadataNavRoot")

        self._settings: QSettings | None = None

        self._active_department: str | None = None
        self._active_type: str | None = None
        self._active_tags: list[str] = []

        self._mode: str = "assets"  # assets | shots | schedule | inbox | reference
        self._include_shots: bool = True
        self._include_assets: bool = False
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
        self._collapsed_filter_groups: set[str] = set()
        self._dept_section_expanded = True
        self._type_section_expanded = True
        self._dept_list_max_height_px: int | None = None
        self._type_list_max_height_px: int | None = None
        self._tag_list_max_height_px: int = _SIDEBAR_TAG_LIST_MAX_HEIGHT_PX
        self._filters_dept_stretch_callback = None
        self._filters_layout_callback = None
        # Schedule: which dept ids apply to shot vs asset entities (index + pipeline types).
        self._schedule_dept_shot_ids: set[str] = set()
        self._schedule_dept_asset_ids: set[str] = set()
        self._header_filter_popup: QFrame | None = None
        self._header_filter_popup_scope: str | None = None
        self._header_filter_popup_anchor: QWidget | None = None
        self._header_filter_popup_closed_at = 0.0
        self._HEADER_FILTER_POPUP_REOPEN_GRACE = 0.25

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        f_h = monos_font("Inter", 11, QFont.Weight.ExtraBold)  # 800 — DEPARTMENTS/TYPES title
        f_h.setLetterSpacing(QFont.PercentageSpacing, 108)

        # --- SCOPE (Schedule: Shots / Assets multi-toggle)
        scope_header_row = QWidget(self)
        scope_header_row.setObjectName("SidebarFilterHeaderRow")
        scope_header_row_l = QHBoxLayout(scope_header_row)
        scope_header_row_l.setContentsMargins(0, 0, 0, 0)
        scope_header_row_l.setSpacing(8)
        scope_icon = QLabel(scope_header_row)
        scope_icon.setObjectName("SidebarFilterHeaderIcon")
        scope_icon.setFixedSize(16, 16)
        scope_icon.setAlignment(Qt.AlignCenter)
        scope_icon.setPixmap(
            lucide_icon("scan", size=16, color_hex=MONOS_COLORS["text_label"]).pixmap(16, 16)
        )
        scope_header = QLabel("SCOPE", scope_header_row)
        scope_header.setObjectName("SidebarSectionHeader")
        scope_header.setFont(f_h)
        scope_header_row_l.addWidget(scope_icon, 0, Qt.AlignVCenter)
        scope_header_row_l.addWidget(scope_header, 0, Qt.AlignVCenter)
        scope_header_row_l.addStretch(1)
        self._scope_list_container = _sidebar_filter_list_container(self)
        self._scope_list = QListWidget(self._scope_list_container)
        self._scope_list_container.layout().addWidget(self._scope_list)
        self._scope_list.setObjectName("SidebarFilterList")
        self._scope_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._scope_list.setUniformItemSizes(False)
        self._scope_list.setFocusPolicy(Qt.NoFocus)
        self._scope_list.setIconSize(QSize(16, 16))
        self._scope_list.setItemDelegate(_SidebarFilterTreeDelegate(self._scope_list))
        self._scope_list.setSpacing(int(SIDEBAR_DEPT_LIST_STYLE.get("list_row_spacing_px", 3)))
        self._scope_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scope_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scope_list.setMaximumHeight(88)
        self._scope_list.itemClicked.connect(self._on_scope_clicked)
        self._scope_section = QWidget(self)
        self._scope_section.setObjectName("SidebarFilterScopeSection")
        scope_section_lay = QVBoxLayout(self._scope_section)
        scope_section_lay.setContentsMargins(0, 0, 0, 0)
        scope_section_lay.setSpacing(4)
        scope_section_lay.addWidget(scope_header_row, 0)
        scope_section_lay.addWidget(self._scope_list_container, 0)
        self._scope_section.setVisible(False)
        self._rebuild_scope_list()

        # --- Header rows (label + "+" button, right-aligned)
        dept_header_row = QWidget(self)
        self._dept_header_row = dept_header_row
        dept_header_row.setObjectName("SidebarFilterHeaderRow")
        dept_header_row_l = QHBoxLayout(dept_header_row)
        dept_header_row_l.setContentsMargins(0, 0, 0, 0)
        dept_header_row_l.setSpacing(6)

        self._btn_dept_section_chevron = _make_filter_section_chevron(
            dept_header_row,
            expanded=True,
            on_toggle=self._toggle_dept_section_expanded,
        )

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
        self._btn_dept_pick.setFixedSize(18, 18)
        self._btn_dept_pick.setCursor(Qt.PointingHandCursor)
        self._btn_dept_pick.clicked.connect(self._open_department_picker)

        dept_header_row_l.addWidget(self._btn_dept_section_chevron, 0, Qt.AlignVCenter)
        dept_header_row_l.addWidget(dept_icon, 0, Qt.AlignVCenter)
        dept_header_row_l.addWidget(dept_header, 0, Qt.AlignVCenter)
        dept_header_row_l.addStretch(1)
        dept_header_row_l.addWidget(self._btn_dept_pick, 0, Qt.AlignVCenter)
        dept_header_row.setFixedHeight(20)

        self._dept_list_container = _sidebar_filter_list_container(self)
        self._dept_list_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._dept_list = QListWidget(self._dept_list_container)
        self._dept_list_container.layout().addWidget(self._dept_list, 0)
        self._dept_list.setObjectName("SidebarFilterList")
        self._dept_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._dept_list.setUniformItemSizes(False)  # section/spacer/dept have different heights
        self._dept_list.setFocusPolicy(Qt.NoFocus)
        self._dept_list.setIconSize(QSize(12, 12))
        self._dept_list.setItemDelegate(_SidebarFilterTreeDelegate(self._dept_list))
        self._dept_list.setSpacing(int(SIDEBAR_DEPT_LIST_STYLE.get("list_row_spacing_px", 3)))
        self._dept_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._dept_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dept_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._dept_list.itemClicked.connect(self._on_department_clicked)

        self._dept_section = QWidget(self)
        self._dept_section.setObjectName("SidebarFilterDeptSection")
        self._dept_section.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        dept_section_lay = QVBoxLayout(self._dept_section)
        dept_section_lay.setContentsMargins(0, 0, 0, 0)
        dept_section_lay.setSpacing(4)
        dept_section_lay.addWidget(dept_header_row, 0)
        dept_section_lay.addWidget(self._dept_list_container, 0)

        type_header_row = QWidget(self)
        type_header_row.setObjectName("SidebarFilterHeaderRow")
        type_header_row_l = QHBoxLayout(type_header_row)
        type_header_row_l.setContentsMargins(0, 0, 0, 0)
        type_header_row_l.setSpacing(6)

        self._btn_type_section_chevron = _make_filter_section_chevron(
            type_header_row,
            expanded=True,
            on_toggle=self._toggle_type_section_expanded,
        )

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
        self._btn_type_pick.setFixedSize(18, 18)
        self._btn_type_pick.setCursor(Qt.PointingHandCursor)
        self._btn_type_pick.clicked.connect(self._open_type_picker)

        type_header_row_l.addWidget(self._btn_type_section_chevron, 0, Qt.AlignVCenter)
        type_header_row_l.addWidget(type_icon, 0, Qt.AlignVCenter)
        type_header_row_l.addWidget(type_header, 0, Qt.AlignVCenter)
        type_header_row_l.addStretch(1)
        type_header_row_l.addWidget(self._btn_type_pick, 0, Qt.AlignVCenter)

        type_header_row.setFixedHeight(20)

        self._type_list_container = _sidebar_filter_list_container(self)
        self._type_list = QListWidget(self._type_list_container)
        self._type_list_container.layout().addWidget(self._type_list)
        self._type_list.setObjectName("SidebarFilterList")
        self._type_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._type_list.setUniformItemSizes(False)
        self._type_list.setFocusPolicy(Qt.NoFocus)
        self._type_list.setIconSize(QSize(12, 12))
        self._type_list.setItemDelegate(_SidebarFilterTreeDelegate(self._type_list))
        self._type_list.setSpacing(int(SIDEBAR_DEPT_LIST_STYLE.get("list_row_spacing_px", 3)))
        self._type_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._type_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._type_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._type_list.itemClicked.connect(self._on_type_clicked)

        self._type_section = QWidget(self)
        self._type_section.setObjectName("SidebarFilterTypeSection")
        self._type_section.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        type_section_lay = QVBoxLayout(self._type_section)
        type_section_lay.setContentsMargins(0, 0, 0, 0)
        type_section_lay.setSpacing(4)
        type_section_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        type_section_lay.addWidget(type_header_row, 0, Qt.AlignmentFlag.AlignTop)
        type_section_lay.addWidget(self._type_list_container, 0, Qt.AlignmentFlag.AlignTop)

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
        self._btn_tag_pick.setFixedSize(18, 18)
        self._btn_tag_pick.setCursor(Qt.PointingHandCursor)
        self._btn_tag_pick.clicked.connect(self._open_tag_picker)

        tag_header_row_l.addWidget(tag_header_icon, 0, Qt.AlignVCenter)
        tag_header_row_l.addWidget(tag_header_label, 0, Qt.AlignVCenter)
        tag_header_row_l.addStretch(1)
        tag_header_row_l.addWidget(self._btn_tag_pick, 0, Qt.AlignVCenter)
        tag_header_row.setFixedHeight(20)

        self._tag_list_container = _sidebar_filter_list_container(self)
        self._tag_list = QListWidget(self._tag_list_container)
        self._tag_list_container.layout().addWidget(self._tag_list)
        self._tag_list.setObjectName("SidebarTagList")
        self._tag_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tag_list.setFocusPolicy(Qt.NoFocus)
        self._tag_list.setIconSize(QSize(20, 16))
        self._tag_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setItemDelegate(_TagListDelegate(self._tag_list))
        self._tag_list.itemClicked.connect(self._on_tag_clicked)

        self._tag_definitions: list[dict[str, str]] = list(DEFAULT_TAG_DEFINITIONS)
        self._tag_color_map: dict[str, str] = dict(TAG_COLOR_BY_ID)
        self._tag_label_map: dict[str, str] = dict(TAG_LABEL_BY_ID)
        self._visible_tags: list[str] = list(ALL_TAG_IDS)
        self._visible_tags_by_department: dict[str, list[str]] = {}
        self._active_tags_by_department: dict[str, list[str]] = {}
        self._tag_department: str | None = None
        self._tag_item_tags: dict[str, list[str]] = {}  # path -> tag_ids (from Project Guide tree)
        self._project_root: Path | None = None
        self._rebuild_tag_list()

        self._tag_section = QWidget(self)
        self._tag_section.setObjectName("SidebarFilterTagSection")
        tag_section_lay = QVBoxLayout(self._tag_section)
        tag_section_lay.setContentsMargins(0, 0, 0, 0)
        tag_section_lay.setSpacing(4)
        tag_section_lay.addWidget(tag_header_row, 0)
        tag_section_lay.addWidget(self._tag_list_container, 0)

        self._tag_section.setVisible(False)

        root.addWidget(self._dept_section, 0)
        root.addWidget(self._type_section, 0)
        root.addWidget(self._tag_section, 0)
        root.addStretch(1)

        # Load from pipeline metadata (single source of truth), scoped by current mode.
        self.reload_from_pipeline_metadata()

    def scope_section(self) -> QWidget:
        return self._scope_section

    def dept_section(self) -> QWidget:
        return self._dept_section

    def type_section(self) -> QWidget:
        return self._type_section

    def tag_section(self) -> QWidget:
        return self._tag_section

    def set_filters_dept_stretch_callback(self, callback) -> None:
        """Deprecated: use set_filters_layout_callback."""
        self._filters_dept_stretch_callback = callback

    def set_filters_layout_callback(self, callback) -> None:
        """Sidebar syncs filter section stretch/alignment when mode or dept expand changes."""
        self._filters_layout_callback = callback

    def _notify_filters_layout(self) -> None:
        cb = self._filters_layout_callback or self._filters_dept_stretch_callback
        if cb is not None:
            cb()

    def reload_from_pipeline_metadata(self) -> None:
        """
        UI-only: load departments/types from pipeline metadata JSON for current mode.
        For mode "inbox": only Source (Client/Freelancer); no departments.
        """
        if self._mode == "inbox":
            self._all_types = ["client", "freelancer"]
            self._type_label_by_id = {"client": "Client", "freelancer": "Freelancer"}
            self._type_icon_by_id = {"client": "package", "freelancer": "user"}
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
            self._scope_section.setVisible(False)
            self._tag_section.setVisible(False)
            self._notify_filters_layout()
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
            self._scope_section.setVisible(False)
            self._tag_section.setVisible(True)
            self.sync_tags_for_department(self._active_department, emit_filter=False)
            self._notify_filters_layout()
            return

        meta = load_pipeline_types_and_presets_for_project(self._project_root)
        registry: DepartmentRegistry | None = None
        if self._project_root is not None:
            try:
                registry = DepartmentRegistry.for_project(self._project_root)
            except OSError:
                registry = None

        # Types: stable ids + display names.
        types_out: list[tuple[str, str]] = []
        type_icons: dict[str, str] = {}
        # Rebuild per-type department mapping for current mode.
        self._dept_ids_by_type = {}
        for type_id, t in meta.types.items():
            if self._mode == "shots":
                if not _is_shot_type(type_id):
                    continue
            elif self._mode == "assets":
                if _is_shot_type(type_id):
                    continue
            types_out.append((type_id, t.name))
            if t.icon_name:
                type_icons[type_id] = t.icon_name
            # Per-type department list (for type tabs in Select Departments dialog and sidebar filtering).
            raw_ids: list[str] = []
            for d in getattr(t, "departments", []) or []:
                if isinstance(d, str) and d.strip() and d.strip() not in raw_ids:
                    raw_ids.append(d.strip())
            dept_ids = resolve_department_ids_for_ui(raw_ids, meta=meta, registry=registry)
            if dept_ids:
                self._dept_ids_by_type[type_id] = dept_ids
        types_out.sort(key=lambda x: x[1].lower())
        self._all_types = [tid for tid, _ in types_out]
        self._type_label_by_id = {tid: name for tid, name in types_out}
        self._type_icon_by_id = type_icons

        # Departments: union across all types (leaf-only after parent expansion).
        seen: set[str] = set()
        for ids in self._dept_ids_by_type.values():
            seen.update(ids)
        depts: list[str] = []
        dept_labels: dict[str, str] = {}
        dept_icons: dict[str, str] = {}
        dept_parent: dict[str, str] = {}
        for did in seen:
            dd = meta.departments.get(did)
            explicit_icon = dd.icon_name if dd is not None else None
            icon_slug = department_icon_name(did, explicit=explicit_icon)
            if dd is not None:
                dept_labels[did] = dd.name
                if getattr(dd, "parent", None) and dd.parent.strip():
                    dept_parent[did] = (dd.parent or "").strip()
            if registry is not None:
                if did not in dept_labels:
                    dept_labels[did] = registry.get_department_label(did)
                parent = registry.get_parent(did)
                if parent:
                    dept_parent[did] = parent
                    if parent not in dept_labels:
                        dept_labels[parent] = registry.get_department_label(parent)
            if icon_slug:
                dept_icons[did] = icon_slug

        order_source = (
            registry.get_departments() if registry is not None else list(meta.departments.keys())
        )
        depts = [dept_id for dept_id in order_source if dept_id in seen]
        missing = [d for d in seen if d not in depts]
        missing.sort(key=lambda s: s.lower())
        depts.extend(missing)
        depts = order_department_ids_grouped_by_parent(depts, dept_parent, order_source)

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
        self._scope_section.setVisible(self._mode == "schedule")
        self._tag_section.setVisible(False)
        if self._mode == "schedule":
            self._rebuild_schedule_dept_scope_sets()
            self._rebuild_scope_list()
        self._notify_filters_layout()

    def update_schedule_dept_scope_sets(
        self,
        *,
        on_shots: set[str] | None = None,
        on_assets: set[str] | None = None,
    ) -> None:
        """Refresh shot/asset department id sets (from ProjectIndex + pipeline types)."""
        self._rebuild_schedule_dept_scope_sets(on_shots=on_shots, on_assets=on_assets)
        if self._mode == "schedule":
            self.set_departments(self._all_departments)
            self.set_types(self._all_types)

    def _rebuild_schedule_dept_scope_sets(
        self,
        *,
        on_shots: set[str] | None = None,
        on_assets: set[str] | None = None,
    ) -> None:
        shots_set: set[str] = set(on_shots or ())
        assets_set: set[str] = set(on_assets or ())
        for type_id, dept_ids in self._dept_ids_by_type.items():
            if _is_shot_type(type_id):
                shots_set.update(dept_ids)
            else:
                assets_set.update(dept_ids)
        self._schedule_dept_shot_ids = {d.strip() for d in shots_set if d and d.strip()}
        self._schedule_dept_asset_ids = {d.strip() for d in assets_set if d and d.strip()}

    def visible_department_ids(self) -> list[str]:
        """Department ids shown in filter list (+ picker); Schedule timeline uses this whitelist."""
        return list(self._visible_departments or [])

    def current_department(self) -> str | None:
        return self._active_department

    def current_type(self) -> str | None:
        return self._active_type

    def current_tags(self) -> list[str]:
        return list(self._active_tags)

    def current_tag(self) -> str | None:
        return self._active_tags[0] if self._active_tags else None

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

    def get_tag_color(self, tag_id: str | None) -> str | None:
        if not tag_id:
            return None
        return self._tag_color_map.get(tag_id)

    def clear_active_tag(self) -> None:
        """Clear sidebar tag filter and emit tagClicked([])."""
        if not self._active_tags:
            return
        self._active_tags = []
        if self._mode == "reference" and self._tag_department:
            self._active_tags_by_department[self._tag_department] = []
        self._sync_tag_selection()
        self.tagClicked.emit([])

    def set_tag_item_tags(self, item_tags: dict[str, list[str]]) -> None:
        """Set item->tag_ids map from Project Guide tree (for tag count badges)."""
        self._tag_item_tags = dict(item_tags) if item_tags else {}
        dept = self._tag_department or self._active_department
        for i in range(self._tag_list.count()):
            item = self._tag_list.item(i)
            if item is None:
                continue
            tid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(tid, str):
                count = len(paths_with_tag(self._tag_item_tags, tid, department_id=dept))
                item.setData(TAG_COUNT_ROLE, count)
        self._tag_list.viewport().update()

    def sync_tags_for_department(self, department_id: str | None, *, emit_filter: bool = False) -> None:
        """Load per-department tag slots and restore that department's active tag filter."""
        if self._mode != "reference":
            return
        prev_dept = self._tag_department
        dept = normalize_tag_department_id(department_id or self._active_department)
        if prev_dept and prev_dept != dept:
            self._active_tags_by_department[prev_dept] = list(self._active_tags)
        self._load_department_tag_defs(dept)
        restored = self._active_tags_by_department.get(dept, [])
        valid_ids = {d["id"] for d in self._tag_definitions}
        self._active_tags = [t for t in restored if t in valid_ids]
        if len(self._active_tags) != len(restored):
            self._active_tags_by_department[dept] = list(self._active_tags)
        self._rebuild_tag_list()
        if emit_filter:
            self.tagClicked.emit(list(self._active_tags))

    def _load_department_tag_defs(self, department_id: str) -> None:
        dept = normalize_tag_department_id(department_id)
        self._tag_department = dept
        if self._project_root:
            self._tag_definitions = read_tag_definitions(self._project_root, dept)
        else:
            self._tag_definitions = list(DEFAULT_TAG_DEFINITIONS)
        if dept not in self._visible_tags_by_department:
            self._visible_tags_by_department[dept] = [d["id"] for d in self._tag_definitions]
        self._visible_tags = list(self._visible_tags_by_department[dept])
        self._tag_color_map = build_color_map(self._tag_definitions)
        self._tag_label_map = build_label_map(self._tag_definitions)

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
        self._load_state_for_mode("schedule")
        # Apply stored state for current mode (if any) and refresh lists.
        self._apply_state(self._state_by_mode.get(self._mode))
        self.reload_from_pipeline_metadata()

    def _settings_key(self, mode: str, field: str) -> str:
        return f"sidebar/filters/{mode}/{field}"

    def _load_state_for_mode(self, mode: str) -> None:
        if self._settings is None:
            return
        if mode not in ("assets", "shots", "inbox", "reference", "schedule"):
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

        inc_shots = True
        inc_assets = False
        if mode == "schedule":
            inc_shots = bool(self._settings.value(self._settings_key(mode, "include_shots"), True, type=bool))
            inc_assets = bool(self._settings.value(self._settings_key(mode, "include_assets"), False, type=bool))
        state: dict[str, object] = {
            "active_department": dep.strip() if dep and dep.strip() else None,
            "active_type": typ.strip() if typ and typ.strip() else None,
            "department_by_type": load_department_by_type(dbt_raw) if mode in ("assets", "shots", "schedule") else {},
            "visible_departments": load_list(vd_raw) if mode != "inbox" else None,
            "visible_types": load_list(vt_raw),
            "include_shots": inc_shots,
            "include_assets": inc_assets,
        }
        self._state_by_mode[mode] = state

    def _save_state_for_mode(self, mode: str) -> None:
        if self._settings is None:
            return
        if mode not in ("assets", "shots", "inbox", "reference", "schedule"):
            return
        state = self._state_by_mode.get(mode)
        if not state:
            state = self._snapshot_state()
            self._state_by_mode[mode] = state
        self._settings.setValue(self._settings_key(mode, "active_department"), state.get("active_department") or "")
        self._settings.setValue(self._settings_key(mode, "active_type"), state.get("active_type") or "")
        if mode == "schedule":
            self._settings.setValue(
                self._settings_key(mode, "include_shots"),
                bool(state.get("include_shots", True)),
            )
            self._settings.setValue(
                self._settings_key(mode, "include_assets"),
                bool(state.get("include_assets", False)),
            )
        if mode in ("assets", "shots", "schedule"):
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
        if m not in ("assets", "shots", "schedule", "inbox", "reference"):
            return
        if self._mode == m:
            # Re-apply stored page state — mode may already match after a stray set_mode()
            # while nav context was elsewhere (tray, entity jump, metadata reload).
            self._apply_state(self._state_by_mode.get(self._mode))
            self.reload_from_pipeline_metadata()
            self._notify_filters_layout()
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
            "include_shots": self._include_shots,
            "include_assets": self._include_assets,
        }

    def _apply_state(self, state: dict[str, object] | None) -> None:
        if not state:
            self._active_department = None
            self._active_type = None
            self._department_by_type = {}
            self._visible_departments = None
            self._visible_types = None
            if self._mode == "schedule":
                self._include_shots = True
                self._include_assets = False
                self._rebuild_scope_list()
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
        if self._mode == "schedule":
            self._include_shots = bool(state.get("include_shots", True))
            self._include_assets = bool(state.get("include_assets", False))
            if not self._include_shots and not self._include_assets:
                self._include_shots = True
            self._rebuild_scope_list()

    def entity_scope(self) -> tuple[bool, bool]:
        """Schedule mode: (include_shots, include_assets)."""
        return self._include_shots, self._include_assets

    def set_entity_scope(self, *, include_shots: bool, include_assets: bool, emit: bool = True) -> None:
        shots = bool(include_shots)
        assets = bool(include_assets)
        if not shots and not assets:
            shots = True
        if shots == self._include_shots and assets == self._include_assets:
            return
        self._include_shots = shots
        self._include_assets = assets
        if self._mode == "schedule":
            self._sanitize_schedule_type_for_scope()
            self._sanitize_schedule_department_for_scope()
            self.set_types(self._all_types)
            self.set_departments(self._all_departments)
        self._rebuild_scope_list()
        if emit:
            self.entityScopeChanged.emit(self._include_shots, self._include_assets)

    def _sanitize_schedule_type_for_scope(self) -> None:
        """Clear type filter when it does not match shot/asset scope."""
        if self._active_type is None:
            return
        if _is_shot_type(self._active_type) and not self._include_shots:
            self._active_type = None
        elif not _is_shot_type(self._active_type) and not self._include_assets:
            self._active_type = None

    def _sanitize_schedule_department_for_scope(self) -> None:
        """Clear department selection when hidden by scope or picker whitelist."""
        dep = self._active_department
        if not dep:
            return
        visible = self._visible_departments or []
        if dep not in visible:
            self._active_department = None
            return
        on_shot = dep in self._schedule_dept_shot_ids
        on_asset = dep in self._schedule_dept_asset_ids
        if self._include_shots and on_shot:
            return
        if self._include_assets and on_asset:
            return
        self._active_department = None

    def _rebuild_scope_list(self) -> None:
        rows = (
            ("assets", "Assets", "box", self._include_assets),
            ("shots", "Shots", "clapperboard", self._include_shots),
        )
        self._scope_list.blockSignals(True)
        try:
            self._scope_list.clear()
            for i, (scope_id, label, icon_name, selected) in enumerate(rows):
                it = QListWidgetItem(label)
                it.setData(
                    Qt.UserRole,
                    {
                        "type": _FILTER_ROW_LEAF,
                        "scope_id": scope_id,
                        "scope_active": bool(selected),
                        "indent": 0,
                    },
                )
                it.setIcon(_lucide_two_state_icon(icon_name, fallback_name="box"))
                self._scope_list.addItem(it)
        finally:
            self._scope_list.blockSignals(False)
        self._scope_list.viewport().update()

    def _on_scope_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) or {}
        if not isinstance(data, dict) or data.get("type") != _FILTER_ROW_LEAF:
            return
        scope_id = (data.get("scope_id") or "").strip().lower()
        if scope_id == "shots":
            new_shots = not self._include_shots
            new_assets = self._include_assets
        elif scope_id == "assets":
            new_assets = not self._include_assets
            new_shots = self._include_shots
        else:
            return
        if not new_shots and not new_assets:
            self._rebuild_scope_list()
            return
        self.set_entity_scope(include_shots=new_shots, include_assets=new_assets)

    def _is_filter_group_expanded(self, scope: str, group_id: str) -> bool:
        return _filter_group_key(scope, group_id) not in self._collapsed_filter_groups

    def _toggle_filter_group(self, scope: str, group_id: str) -> None:
        key = _filter_group_key(scope, group_id)
        if key in self._collapsed_filter_groups:
            self._collapsed_filter_groups.discard(key)
        else:
            self._collapsed_filter_groups.add(key)
        if scope == "dept":
            self.set_departments(self._all_departments)
        elif scope == "type":
            self.set_types(self._all_types)
        self._sync_selection()

    def _toggle_dept_section_expanded(self, _checked: bool = False) -> None:
        self._dept_section_expanded = not self._dept_section_expanded
        self._dept_list_container.setVisible(self._dept_section_expanded)
        self._btn_dept_pick.setVisible(self._dept_section_expanded)
        _update_filter_section_chevron(self._btn_dept_section_chevron, expanded=self._dept_section_expanded)
        self._notify_filters_layout()

    def _toggle_type_section_expanded(self, _checked: bool = False) -> None:
        self._type_section_expanded = not self._type_section_expanded
        self._type_list_container.setVisible(self._type_section_expanded)
        self._btn_type_pick.setVisible(self._type_section_expanded)
        _update_filter_section_chevron(self._btn_type_section_chevron, expanded=self._type_section_expanded)
        self._notify_filters_layout()

    def _append_filter_group_row(
        self,
        list_w: QListWidget,
        *,
        scope: str,
        group_id: str,
        label: str,
        icon_name: str | None,
        indent: int = 0,
    ) -> None:
        expanded = self._is_filter_group_expanded(scope, group_id)
        it = QListWidgetItem(label)
        it.setData(
            Qt.UserRole,
            {
                "type": _FILTER_ROW_GROUP,
                "scope": scope,
                "group_id": group_id,
                "expanded": expanded,
                "indent": indent,
            },
        )
        if icon_name:
            it.setIcon(_lucide_two_state_icon(icon_name, fallback_name="layers"))
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        list_w.addItem(it)

    def _append_filter_leaf_row(
        self,
        list_w: QListWidget,
        *,
        scope: str,
        item_id: str,
        label: str,
        count_map: dict[str, int],
        icon_name: str | None,
        indent: int = 0,
        extra: dict | None = None,
    ) -> None:
        role: dict = {
            "type": _FILTER_ROW_LEAF,
            "scope": scope,
            "item_id": item_id,
            "indent": indent,
        }
        if item_id in count_map:
            role["count"] = count_map[item_id]
        if extra:
            role.update(extra)
        it = QListWidgetItem(label)
        it.setData(Qt.UserRole, role)
        if icon_name:
            fb = "folder" if scope == "type" else "layers"
            it.setIcon(_lucide_two_state_icon(icon_name, fallback_name=fb))
        list_w.addItem(it)

    def _build_dept_tree_rows(self, list_w: QListWidget, visible: list[str]) -> None:
        parents_with_children = {self._dept_parent[d] for d in visible if self._dept_parent.get(d)}
        children_by_parent: dict[str, list[str]] = {}
        for dept_id in visible:
            parent_id = self._dept_parent.get(dept_id)
            if parent_id and parent_id in parents_with_children:
                children_by_parent.setdefault(parent_id, []).append(dept_id)

        emitted_groups: set[str] = set()
        for dept_id in visible:
            parent_id = self._dept_parent.get(dept_id)
            if parent_id and parent_id in parents_with_children:
                if parent_id in emitted_groups:
                    continue
                emitted_groups.add(parent_id)
                label = _title_case_label(self._dept_label_by_id.get(parent_id, parent_id))
                child_ids = children_by_parent.get(parent_id, [])
                icon = self._dept_icon_by_id.get(parent_id) or self._dept_icon_by_id.get(dept_id)
                self._append_filter_group_row(
                    list_w,
                    scope="dept",
                    group_id=parent_id,
                    label=label,
                    icon_name=icon,
                )
                if self._is_filter_group_expanded("dept", parent_id):
                    for child_id in child_ids:
                        child_label = _title_case_label(self._dept_label_by_id.get(child_id, child_id))
                        self._append_filter_leaf_row(
                            list_w,
                            scope="dept",
                            item_id=child_id,
                            label=child_label,
                            count_map=self._count_by_department,
                            icon_name=self._dept_icon_by_id.get(child_id),
                            indent=1,
                        )
            else:
                label = _title_case_label(self._dept_label_by_id.get(dept_id, dept_id))
                self._append_filter_leaf_row(
                    list_w,
                    scope="dept",
                    item_id=dept_id,
                    label=label,
                    count_map=self._count_by_department,
                    icon_name=self._dept_icon_by_id.get(dept_id),
                    indent=0,
                )

    def _build_type_flat_rows(self, list_w: QListWidget, type_ids: list[str]) -> None:
        for type_id in type_ids:
            label = _title_case_label(self._type_label_by_id.get(type_id, type_id))
            self._append_filter_leaf_row(
                list_w,
                scope="type",
                item_id=type_id,
                label=label,
                count_map=self._count_by_type,
                icon_name=self._type_icon_by_id.get(type_id),
                indent=0,
            )

    def _populate_dept_list_schedule(self, visible: list[str]) -> None:
        """Schedule: departments grouped by Shots vs Assets (picker list = timeline whitelist)."""
        if self._active_type and self._active_type in self._dept_ids_by_type:
            allowed = set(self._dept_ids_by_type.get(self._active_type, []))
            visible = [d for d in visible if d in allowed]

        shot_ids = [d for d in visible if d in self._schedule_dept_shot_ids]
        asset_ids = [d for d in visible if d in self._schedule_dept_asset_ids]
        sections: list[tuple[str, list[str]]] = []
        if self._include_assets and asset_ids:
            sections.append(("Assets", asset_ids))
        if self._include_shots and shot_ids:
            sections.append(("Shots", shot_ids))

        self._dept_list.blockSignals(True)
        try:
            self._dept_list.clear()
            for section_label, dept_ids in sections:
                if not dept_ids:
                    continue
                self._append_filter_group_row(
                    self._dept_list,
                    scope="dept",
                    group_id=f"schedule:{section_label}",
                    label=section_label,
                    icon_name="box" if section_label == "Assets" else "clapperboard",
                )
                if self._is_filter_group_expanded("dept", f"schedule:{section_label}"):
                    for dept_id in dept_ids:
                        label = _title_case_label(self._dept_label_by_id.get(dept_id, dept_id))
                        self._append_filter_leaf_row(
                            self._dept_list,
                            scope="dept",
                            item_id=dept_id,
                            label=label,
                            count_map=self._count_by_department,
                            icon_name=self._dept_icon_by_id.get(dept_id),
                            indent=1,
                        )
            self._sync_selection()
        finally:
            self._dept_list.blockSignals(False)

    def set_departments(self, values: list[str]) -> None:
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        self._all_departments = cleaned
        # If never configured, default to first N (one-time). If configured to [], keep empty.
        if self._visible_departments is None:
            if self._mode == "schedule":
                eligible = [
                    d
                    for d in cleaned
                    if d in self._schedule_dept_shot_ids or d in self._schedule_dept_asset_ids
                ]
                self._visible_departments = eligible if eligible else cleaned[: self._max_visible]
            else:
                self._visible_departments = cleaned[: self._max_visible]
        else:
            # Keep only still-valid items (no auto-fill).
            self._visible_departments = [v for v in self._visible_departments if v in cleaned]

        visible = self._visible_departments or []
        if self._mode == "schedule":
            self._populate_dept_list_schedule(visible)
            self._notify_filters_layout()
            return
        # When a type is active in assets/shots mode, restrict visible departments to those
        # supported by that type. If no type is active, show all departments that pass the
        # Select Departments filter.
        if self._mode in ("assets", "shots") and self._active_type and self._active_type in self._dept_ids_by_type:
            allowed = set(self._dept_ids_by_type.get(self._active_type, []))
            visible = [d for d in visible if d in allowed]

        self._dept_list.blockSignals(True)
        try:
            self._dept_list.clear()
            self._build_dept_tree_rows(self._dept_list, visible)
            self._sync_selection()
        finally:
            self._dept_list.blockSignals(False)
        self._notify_filters_layout()

    def set_dept_list_max_height(self, px: int | None) -> None:
        """Cap department list height (scroll when content exceeds); None = fit content only."""
        self._dept_list_max_height_px = px
        if self._dept_section_expanded and self._all_departments:
            self._fit_dept_list()

    def set_type_list_max_height(self, px: int | None) -> None:
        """Cap type list height (scroll when content exceeds); None = fit content only."""
        self._type_list_max_height_px = px
        if self._type_section_expanded and self._all_types:
            self._fit_type_list()

    def _fit_dept_list(self) -> None:
        self._fit_list_height(
            self._dept_list,
            max_height_px=self._dept_list_max_height_px,
            allow_shrink=False,
        )
        self._sync_list_container_height(self._dept_list, self._dept_list_container)
        QTimer.singleShot(0, self._deferred_refine_dept_list)

    def _fit_type_list(self) -> None:
        allow_shrink = self._type_list_max_height_px is None
        self._fit_list_height(
            self._type_list,
            max_height_px=self._type_list_max_height_px,
            allow_shrink=allow_shrink,
        )
        self._sync_list_container_height(self._type_list, self._type_list_container)
        QTimer.singleShot(0, self._deferred_refine_type_list)

    def _deferred_refine_dept_list(self) -> None:
        if SidebarWidget._ensure_list_fully_visible(self._dept_list, self._dept_list_max_height_px):
            self._sync_list_container_height(self._dept_list, self._dept_list_container)

    def _deferred_refine_type_list(self) -> None:
        if SidebarWidget._ensure_list_fully_visible(self._type_list, self._type_list_max_height_px):
            self._sync_list_container_height(self._type_list, self._type_list_container)

    @staticmethod
    def _sync_list_container_height(list_w: QListWidget, container: QFrame) -> None:
        h = int(list_w.height())
        # QFrame border (1px) is inside the widget rect; match list to inner client area.
        container.setFixedHeight(h)
        container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        list_w.updateGeometry()

    def set_types(self, values: list[str]) -> None:
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        self._all_types = cleaned
        if self._visible_types is None:
            self._visible_types = cleaned[: self._max_visible]
        else:
            # Keep user picker selection; only drop ids no longer in pipeline metadata.
            self._visible_types = [v for v in self._visible_types if v in cleaned]
        if self._mode == "schedule" and self._visible_types is None and cleaned:
            self._visible_types = cleaned[: self._max_visible]

        visible = self._visible_types or []
        if self._mode == "schedule":
            scoped: list[str] = []
            for type_id in visible:
                if _is_shot_type(type_id) and not self._include_shots:
                    continue
                if not _is_shot_type(type_id) and not self._include_assets:
                    continue
                scoped.append(type_id)
            visible = scoped
        # Flat list — scope (Assets/Shots pill or schedule toggles) already defines context.
        flat_types = list(visible)
        if self._mode == "schedule":
            asset_types = [t for t in visible if not _is_shot_type(t)]
            shot_types = [t for t in visible if _is_shot_type(t)]
            flat_types = []
            if self._include_assets:
                flat_types.extend(asset_types)
            if self._include_shots:
                flat_types.extend(shot_types)

        self._type_list.blockSignals(True)
        try:
            self._type_list.clear()
            self._build_type_flat_rows(self._type_list, flat_types)
            self._sync_selection()
        finally:
            self._type_list.blockSignals(False)
        self._fit_type_list()
        self._notify_filters_layout()

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

    def set_selected_type(self, type_id: str | None, *, emit: bool = True) -> None:
        """Set type filter programmatically (e.g. tray mini popup). Use emit=False to avoid main-view reload churn."""
        tid = (type_id or "").strip() or None
        if self._active_type:
            self._department_by_type[self._active_type] = self._active_department
        self._active_type = tid
        if tid:
            allowed_list = self._dept_ids_by_type.get(tid, [])
            allowed_set = set(allowed_list)
            restored = self._department_by_type.get(tid)
            if restored and restored in allowed_set:
                self._active_department = restored
            else:
                self._active_department = allowed_list[0] if allowed_list else None
        self.set_departments(self._all_departments)
        self._sync_selection()
        if emit:
            self.typeClicked.emit(tid)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def export_filter_snapshot(self) -> dict[str, object]:
        """Capture sidebar filter state for nav quick-view assign."""
        return dict(self._snapshot_state())

    def import_filter_snapshot(self, state: dict[str, object] | None, *, emit: bool = False) -> None:
        """Restore filter state from nav quick-view recall (after page switch)."""
        if not state:
            return
        self._apply_state(state)
        self.reload_from_pipeline_metadata()
        self._sync_selection()
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)
        if emit:
            if self._mode == "schedule":
                self.entityScopeChanged.emit(self._include_shots, self._include_assets)
            self.typeClicked.emit(self._active_type)
            self.departmentClicked.emit(self._active_department)

    def filter_option_lists(self) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str, str | None]]]:
        """(dept_id, label, lucide_icon), (type_id, label, lucide_icon) — same visible scope as sidebar lists."""
        dept_ids = list(self._visible_departments or self._all_departments)
        if self._mode in ("assets", "shots") and self._active_type and self._active_type in self._dept_ids_by_type:
            allowed = set(self._dept_ids_by_type.get(self._active_type, []))
            dept_ids = [d for d in dept_ids if d in allowed]
        depts: list[tuple[str, str, str | None]] = []
        for did in dept_ids:
            label = _title_case_label(self._dept_label_by_id.get(did, did))
            icon = self._dept_icon_by_id.get(did)
            depts.append((did, label, icon))
        type_ids = list(self._visible_types or self._all_types)
        types: list[tuple[str, str, str | None]] = []
        for tid in type_ids:
            label = _title_case_label(self._type_label_by_id.get(tid, tid))
            icon = self._type_icon_by_id.get(tid)
            types.append((tid, label, icon))
        return depts, types

    def show_type_filter_popup(self, anchor: QWidget) -> None:
        """Popup to pick active type (header badge click)."""
        self._show_header_filter_popup(anchor, scope="type")

    def show_department_filter_popup(self, anchor: QWidget) -> None:
        """Popup to pick active department (header badge click)."""
        self._show_header_filter_popup(anchor, scope="dept")

    def _close_header_filter_popup(self, *, record_closed_at: bool = False) -> None:
        popup = self._header_filter_popup
        if popup is not None and popup.isVisible():
            popup.close()
        self._header_filter_popup = None
        if record_closed_at:
            self._header_filter_popup_closed_at = time.monotonic()

    def _show_header_filter_popup(self, anchor: QWidget, *, scope: str) -> None:
        if anchor is None or not anchor.isVisible():
            return

        popup = self._header_filter_popup
        same_target = (
            self._header_filter_popup_scope == scope and self._header_filter_popup_anchor is anchor
        )
        # Toggle off when the same badge is clicked while its list is still open.
        if popup is not None and popup.isVisible() and same_target:
            self._close_header_filter_popup(record_closed_at=True)
            return
        # Qt Popup closes on outside click before the badge handler runs — suppress immediate reopen.
        if (time.monotonic() - self._header_filter_popup_closed_at) < self._HEADER_FILTER_POPUP_REOPEN_GRACE:
            if same_target:
                return

        depts, types = self.filter_option_lists()
        items = types if scope == "type" else depts
        if not items:
            return

        self._close_header_filter_popup()
        active = self._active_type if scope == "type" else self._active_department
        count_map = self._count_by_type if scope == "type" else self._count_by_department
        sidebar = self

        class _HeaderFilterPickerPopup(QFrame):
            def __init__(self, parent, badge_anchor: QWidget) -> None:
                super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
                self._badge_anchor = badge_anchor

            def hideEvent(self, event) -> None:  # noqa: N802
                sidebar._header_filter_popup = None
                sidebar._header_filter_popup_closed_at = time.monotonic()
                QTimer.singleShot(0, lambda: clear_stuck_widget_hover(self._badge_anchor))
                super().hideEvent(event)

        popup = _HeaderFilterPickerPopup(anchor.window(), anchor)
        popup.setObjectName("HeaderFilterPickerPopup")
        popup.setAttribute(Qt.WA_StyledBackground, True)

        lay = QVBoxLayout(popup)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(0)

        container = _sidebar_filter_list_container(popup)
        list_w = QListWidget(container)
        container.layout().addWidget(list_w, 0)
        list_w.setObjectName("SidebarFilterList")
        list_w.setSelectionMode(QAbstractItemView.SingleSelection)
        list_w.setUniformItemSizes(False)
        list_w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        list_w.setIconSize(QSize(16, 16))
        list_w.setItemDelegate(_SidebarFilterTreeDelegate(list_w))
        list_w.setSpacing(int(SIDEBAR_DEPT_LIST_STYLE.get("list_row_spacing_px", 3)))
        list_w.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        list_w.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        for item_id, label, icon_name in items:
            self._append_filter_leaf_row(
                list_w,
                scope=scope,
                item_id=item_id,
                label=label,
                count_map=count_map,
                icon_name=icon_name,
            )

        for i in range(list_w.count()):
            it = list_w.item(i)
            if it is None:
                continue
            data = it.data(Qt.UserRole)
            if isinstance(data, dict) and data.get("item_id") == active:
                it.setSelected(True)
                list_w.setCurrentItem(it)
                break

        def _on_pick(item: QListWidgetItem) -> None:
            if scope == "type":
                picked = item.data(Qt.UserRole)
                if isinstance(picked, dict) and picked.get("item_id") != self._active_type:
                    self._on_type_clicked(item)
            else:
                picked = item.data(Qt.UserRole)
                if isinstance(picked, dict) and picked.get("item_id") != self._active_department:
                    self._on_department_clicked(item)
            popup.close()

        list_w.itemClicked.connect(_on_pick)
        lay.addWidget(container, 0)

        popup.setMinimumWidth(max(int(anchor.width()), 220))
        max_list_h = min(320, max_popup_height_for_anchor(anchor, gap=4) - 24)
        SidebarWidget._fit_list_height(list_w, max_height_px=max(120, max_list_h))

        position_popup_near_anchor(popup, anchor)
        self._header_filter_popup = popup
        self._header_filter_popup_scope = scope
        self._header_filter_popup_anchor = anchor
        popup.show()
        QTimer.singleShot(0, lambda: clear_stuck_widget_hover(anchor))

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
                    if isinstance(data, dict) and data.get("type") == _FILTER_ROW_LEAF:
                        item_id = data.get("item_id") or data.get("dept_id")
                        if item_id == self._active_department:
                            it.setSelected(True)
                            self._dept_list.setCurrentItem(it)
                            break

            if self._active_type is not None:
                for i in range(self._type_list.count()):
                    it = self._type_list.item(i)
                    if it is None:
                        continue
                    data = it.data(Qt.UserRole)
                    if isinstance(data, dict) and data.get("type") == _FILTER_ROW_LEAF:
                        item_id = data.get("item_id") or data.get("dept_id")
                        if item_id == self._active_type:
                            it.setSelected(True)
                            self._type_list.setCurrentItem(it)
                            break
        finally:
            self._dept_list.blockSignals(False)
            self._type_list.blockSignals(False)
        # Custom delegate: force repaint so the previous row loses selected styling.
        self._dept_list.viewport().update()
        self._type_list.viewport().update()

    def _on_department_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        if data.get("type") == _FILTER_ROW_GROUP:
            scope = str(data.get("scope") or "dept")
            group_id = data.get("group_id")
            if isinstance(group_id, str) and group_id:
                self._toggle_filter_group(scope, group_id)
            return
        if data.get("type") != _FILTER_ROW_LEAF:
            return
        clicked = data.get("item_id") if isinstance(data.get("item_id"), str) else None
        if clicked is None:
            clicked = data.get("dept_id") if isinstance(data.get("dept_id"), str) else None
        if clicked is None:
            return
        if clicked == self._active_department:
            # Project Guide: department required (same as Inbox source type).
            if self._mode == "reference":
                return
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
        if self._mode == "reference":
            self.sync_tags_for_department(clicked, emit_filter=True)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def _on_type_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return
        if data.get("type") == _FILTER_ROW_GROUP:
            scope = str(data.get("scope") or "type")
            group_id = data.get("group_id")
            if isinstance(group_id, str) and group_id:
                self._toggle_filter_group(scope, group_id)
            return
        if data.get("type") != _FILTER_ROW_LEAF:
            return
        clicked = data.get("item_id") if isinstance(data.get("item_id"), str) else None
        if clicked is None:
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
        if not isinstance(tag_id, str) or not tag_id:
            return
        if tag_id in self._active_tags:
            self._active_tags = [t for t in self._active_tags if t != tag_id]
        else:
            self._active_tags = [*self._active_tags, tag_id]
        self._sync_tag_selection()
        if self._mode == "reference" and self._tag_department:
            self._active_tags_by_department[self._tag_department] = list(self._active_tags)
        self.tagClicked.emit(list(self._active_tags))

    def _sync_tag_selection(self) -> None:
        active = set(self._active_tags)
        row_px = int(SIDEBAR_DEPT_LIST_STYLE.get("row_font_size_px", 11))
        self._tag_list.clearSelection()
        self._tag_list.setCurrentRow(-1)
        for i in range(self._tag_list.count()):
            item = self._tag_list.item(i)
            if item is None:
                continue
            tid = item.data(Qt.ItemDataRole.UserRole)
            is_active = isinstance(tid, str) and tid in active
            weight = QFont.Weight.DemiBold if is_active else QFont.Weight.Medium
            item.setFont(monos_font("Inter", row_px, weight))
            if is_active:
                item.setForeground(QColor(MONOS_COLORS.get("blue_400", "#60a5fa")))
            else:
                item.setForeground(QColor(MONOS_COLORS.get("text_body", "#d4d4d8")))

    def set_project_root(self, project_root: Path | None) -> None:
        self._project_root = project_root
        if self._mode == "reference":
            self.sync_tags_for_department(self._active_department, emit_filter=False)
        elif project_root is not None:
            dept = normalize_tag_department_id(self._active_department)
            self._load_department_tag_defs(dept)
            self._rebuild_tag_list()
        else:
            self._tag_definitions = list(DEFAULT_TAG_DEFINITIONS)
            self._tag_color_map = build_color_map(self._tag_definitions)
            self._tag_label_map = build_label_map(self._tag_definitions)
            self._rebuild_tag_list()

    def _fit_tag_list(self) -> None:
        self._fit_list_height(
            self._tag_list,
            max_height_px=self._tag_list_max_height_px,
            allow_shrink=False,
        )
        self._sync_list_container_height(self._tag_list, self._tag_list_container)
        self._notify_filters_layout()

    def _rebuild_tag_list(self) -> None:
        self._tag_list.clear()
        row_px = int(SIDEBAR_DEPT_LIST_STYLE.get("row_font_size_px", 11))
        f_tag = monos_font("Inter", row_px, QFont.Weight.Medium)
        visible_set = set(self._visible_tags)
        dept = self._tag_department or self._active_department
        for tdef in self._tag_definitions:
            tid = tdef["id"]
            if tid not in visible_set:
                continue
            item = QListWidgetItem(tdef["label"])
            item.setData(Qt.ItemDataRole.UserRole, tid)
            count = len(paths_with_tag(self._tag_item_tags, tid, department_id=dept))
            item.setData(TAG_COUNT_ROLE, count)
            item.setFont(f_tag)
            item.setForeground(QColor(MONOS_COLORS.get("text_body", "#d4d4d8")))
            item.setIcon(self._tag_dot_icon(tdef["color"]))
            self._tag_list.addItem(item)
        self._fit_tag_list()
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
        dept = self._tag_department or normalize_tag_department_id(self._active_department)
        # Use top-level window as parent so dialog survives when filter is in compact popup (reparent).
        dlg = _TagPickerDialog(
            tag_definitions=self._tag_definitions,
            visible_tags=list(self._visible_tags),
            project_root=self._project_root,
            department_id=dept,
            item_tags=self._tag_item_tags,
            parent=self.window(),
        )
        if dlg.exec() != QDialog.Accepted:
            return
        self._tag_definitions = dlg.tag_definitions()
        self._tag_color_map = build_color_map(self._tag_definitions)
        self._tag_label_map = build_label_map(self._tag_definitions)
        self._visible_tags = dlg.visible_tag_ids()
        self._visible_tags_by_department[dept] = list(self._visible_tags)
        if self._project_root:
            save_tag_definitions(self._project_root, dept, self._tag_definitions)
        valid_ids = {d["id"] for d in self._tag_definitions}
        pruned = [t for t in self._active_tags if t in valid_ids]
        if pruned != self._active_tags:
            self._active_tags = pruned
            self._active_tags_by_department[dept] = list(self._active_tags)
            self.tagClicked.emit(list(self._active_tags))
        self._rebuild_tag_list()
        self.tagsDefinitionsChanged.emit()

    def _open_department_picker(self) -> None:
        # Build per-type tabs for departments: each tab shows departments that type supports.
        type_tabs: list[tuple[str, str]] = []
        dept_ids_by_type: dict[str, list[str]] = {}
        if self._mode == "schedule":
            shot_list = [d for d in self._all_departments if d in self._schedule_dept_shot_ids]
            asset_list = [d for d in self._all_departments if d in self._schedule_dept_asset_ids]
            if asset_list:
                type_tabs.append(("assets", "Assets"))
                dept_ids_by_type["assets"] = asset_list
            if shot_list:
                type_tabs.append(("shots", "Shots"))
                dept_ids_by_type["shots"] = shot_list
        elif self._mode in ("assets", "shots") and self._dept_ids_by_type:
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
        if self._mode == "schedule":
            self.entityScopeChanged.emit(self._include_shots, self._include_assets)

    def _open_type_picker(self) -> None:
        type_ids = list(self._all_types)
        if self._mode == "schedule":
            type_ids = [
                tid
                for tid in type_ids
                if (_is_shot_type(tid) and self._include_shots)
                or (not _is_shot_type(tid) and self._include_assets)
            ]
        # Use top-level window as parent so dialog survives when filter is in compact popup (reparent).
        dlg = _FilterPickDialog(
            title="Select Types",
            items=[
                (tid, _title_case_label(self._type_label_by_id.get(tid, tid)), self._type_icon_by_id.get(tid))
                for tid in type_ids
            ],
            selected=set(self._visible_types or []),
            max_selected=None,
            parent=self.window(),
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
        if self._mode == "schedule":
            self.entityScopeChanged.emit(self._include_shots, self._include_assets)

    @staticmethod
    def _list_content_height(w: QListWidget) -> int:
        """Natural list height from row sizes only (immune to current widget height)."""
        rows = int(w.count())
        if rows <= 0:
            return 0

        row_heights = sum(max(1, int(w.sizeHintForRow(i))) for i in range(rows))
        gaps = max(0, rows - 1) * int(w.spacing())
        margins = w.contentsMargins()
        frame = 2 * int(w.frameWidth())
        # Nested QSS: QFrame#SidebarFilterListContainer QListWidget#SidebarFilterList { padding: 4px; }
        list_pad = 8
        slack = 4
        return row_heights + gaps + list_pad + margins.top() + margins.bottom() + frame + slack

    @staticmethod
    def _fit_list_height(
        w: QListWidget, *, max_height_px: int | None = None, allow_shrink: bool = False
    ) -> None:
        """
        Size list to fit rows; max_height_px caps height and enables scroll when content exceeds.
        allow_shrink: only for compact type lists — never shrink department lists after ensure.
        """
        rows = int(w.count())
        if rows <= 0:
            w.setFixedHeight(0)
            w.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            return

        content_h = SidebarWidget._list_content_height(w)
        cap = max_height_px
        if cap is not None:
            target = min(content_h, cap)
        else:
            target = content_h

        w.setMinimumHeight(0)
        w.setMaximumHeight(cap if cap is not None else 16777215)
        w.setFixedHeight(target)
        w.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if cap is not None and content_h > cap else Qt.ScrollBarAlwaysOff
        )

        SidebarWidget._ensure_list_fully_visible(w, cap)
        if allow_shrink:
            SidebarWidget._shrink_list_if_oversized(w, content_h, cap)
        elif cap is not None and int(w.height()) > cap:
            w.setFixedHeight(cap)
            w.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    @staticmethod
    def _ensure_list_fully_visible(w: QListWidget, cap: int | None) -> bool:
        rows = int(w.count())
        if rows <= 0:
            return False
        last = w.item(rows - 1)
        if last is None:
            return False
        rect = w.visualItemRect(last)
        if not rect.isValid():
            return False
        clip = int(rect.bottom()) + 1 - int(w.viewport().height())
        if clip <= 0:
            return False
        expanded = int(w.height()) + clip
        if cap is not None and expanded > cap:
            w.setFixedHeight(cap)
            w.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            return False
        w.setFixedHeight(expanded)
        w.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return True

    @staticmethod
    def _shrink_list_if_oversized(
        w: QListWidget, content_h: int, cap: int | None
    ) -> None:
        """Remove dead space when list is taller than its rows (Type with few items)."""
        if int(w.verticalScrollBar().maximum()) > 0:
            return
        natural = content_h if cap is None else min(content_h, cap)
        if int(w.height()) <= natural + 2:
            return
        w.setFixedHeight(natural)
        w.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)


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
            apply_pill_segment_positions(self._tab_buttons)
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
        department_id: str,
        item_tags: dict[str, list[str]],
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
        self._department_id = normalize_tag_department_id(department_id)
        self._item_tags = item_tags

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
            rename_tag_definition(self._project_root, self._department_id, tag_id, new_label.strip())

    def _recolor_tag(self, tag_id: str, new_color: str, item: QListWidgetItem) -> None:
        for d in self._defs:
            if d["id"] == tag_id:
                d["color"] = new_color
                break
        item.setIcon(self._dot_icon(new_color))
        if self._project_root:
            recolor_tag_definition(self._project_root, self._department_id, tag_id, new_color)

    def _delete_tag(self, tag_id: str) -> None:
        if self._project_root:
            _, self._defs = delete_tag_definition(
                self._project_root, self._department_id, tag_id, self._item_tags,
            )
        else:
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
            _, self._defs = add_tag_definition(
                self._project_root, self._department_id, label.strip(), color,
            )
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


# --- Recent Task row: type icon + item name + department icon + DCC brand icon (right)
_TASK_ROW_HEIGHT = 26
_TASK_ICON_SIZE = 14
_TASK_SMALL_ICON_SIZE = 12
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
    hex_color = (color if isinstance(color, str) else None) or (
        MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_label"]
    )
    return brand_icon(slug, size=_TASK_SMALL_ICON_SIZE, color_hex=hex_color)


def _task_dept_icon(sidebar_widget: QWidget | None, dept_id: str, is_selected: bool) -> QIcon:
    """Department icon from sidebar filters pipeline; fallback lucide layers."""
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
    """Paints one recent task row: type icon + name + department icon + DCC icon (right)."""

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
        is_selected = bool(opt.state & QStyle.State_Selected)
        is_hovered = bool(opt.state & QStyle.State_MouseOver)

        sidebar_widget: QWidget | None = None
        w = widget
        while w:
            if getattr(w, "current_context", None) is not None and getattr(w, "filters", None) is not None:
                sidebar_widget = w
                break
            w = w.parentWidget() if hasattr(w, "parentWidget") else None

        painter.save()
        try:
            highlight = _filter_row_highlight_rect(r)
            radius = _filter_row_highlight_radius()
            if is_selected:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(37, 99, 235, 100))
                painter.drawRoundedRect(highlight, radius, radius)
            elif is_hovered:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 14))
                painter.drawRoundedRect(highlight, radius, radius)

            x = r.left() + _filter_row_content_pad_left()
            cy = r.center().y()

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
            color = MONOS_COLORS["blue_400"] if is_selected else (
                MONOS_COLORS["text_label"] if not is_hovered else "#e4e4e7"
            )
            icon = lucide_icon(type_icon_name, size=_TASK_ICON_SIZE, color_hex=color)
            if not icon.isNull():
                ir = QRect(x, cy - _TASK_ICON_SIZE // 2, _TASK_ICON_SIZE, _TASK_ICON_SIZE)
                icon.paint(painter, ir, Qt.AlignCenter, QIcon.Selected if is_selected else QIcon.Normal)
            x += _TASK_ICON_SIZE + _TASK_ICON_GAP

            has_dept = bool((task.department or "").strip())
            has_dcc = bool((task.dcc or "").strip())
            right_w = 0
            if has_dcc:
                right_w += _TASK_SMALL_ICON_SIZE + _TASK_ICON_GAP
            if has_dept:
                right_w += _TASK_SMALL_ICON_SIZE
            if has_dcc or has_dept:
                right_w += _TASK_RIGHT_MARGIN

            text_w = max(0, r.width() - (x - r.left()) - right_w)
            name_str = (task.item_name or "").strip()
            fm = QFontMetrics(opt.font)
            elided = fm.elidedText(name_str, Qt.TextElideMode.ElideRight, text_w)
            text_rect = QRect(x, r.top(), text_w, r.height())
            if is_selected:
                primary_color = MONOS_COLORS["blue_400"]
            elif is_hovered:
                primary_color = "#e4e4e7"
            else:
                primary_color = MONOS_COLORS["text_primary"]
            painter.setPen(QColor(primary_color))
            painter.setFont(opt.font)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

            right_x = r.right() - _TASK_RIGHT_MARGIN
            if has_dcc:
                dcc_icon = _task_dcc_icon(task.dcc, is_selected)
                if not dcc_icon.isNull():
                    ix = right_x - _TASK_SMALL_ICON_SIZE
                    iy = cy - _TASK_SMALL_ICON_SIZE // 2
                    dcc_icon.paint(
                        painter, QRect(ix, iy, _TASK_SMALL_ICON_SIZE, _TASK_SMALL_ICON_SIZE), Qt.AlignCenter
                    )
                right_x -= _TASK_SMALL_ICON_SIZE + _TASK_ICON_GAP
            if has_dept:
                dept_icon = _task_dept_icon(sidebar_widget, task.department, is_selected)
                if not dept_icon.isNull():
                    ix = right_x - _TASK_SMALL_ICON_SIZE
                    iy = cy - _TASK_SMALL_ICON_SIZE // 2
                    dept_icon.paint(
                        painter, QRect(ix, iy, _TASK_SMALL_ICON_SIZE, _TASK_SMALL_ICON_SIZE), Qt.AlignCenter
                    )
        finally:
            painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        return QSize(-1, _TASK_ROW_HEIGHT)


def _populate_recent_tasks_flat(
    list_w: QListWidget,
    tasks: list[RecentTask],
) -> None:
    """Flat list of most-recent tasks (no department grouping)."""
    list_w.clear()
    for task in tasks:
        it = QListWidgetItem("")
        it.setData(Qt.UserRole, task)
        it.setSizeHint(QSize(0, _TASK_ROW_HEIGHT))
        base_tt = f"{task.item_name}\n{task.department}" + (f" · {task.dcc}" if task.dcc else "")
        hint_html = '<span style="font-size:80%; color:#71717a;">Double-click to open</span>'
        it.setToolTip(f"<html>{base_tt.replace(chr(10), '<br/>')}<br/><br/>{hint_html}</html>")
        list_w.addItem(it)


class Sidebar(QWidget):
    """
    Filter panel (256px): project name, department/type filters, recent tasks.
    Primary navigation lives in SidebarNavRail.
    On Dashboard customize, the filter block is replaced by a widget palette.
    """

    recent_task_clicked = Signal(object)  # RecentTask
    recent_task_double_clicked = Signal(object)  # RecentTask
    clear_recent_tasks_requested = Signal()
    dashboard_widget_visibility_toggled = Signal(str, bool)  # widget_id, visible

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarFilterPanel")
        # Ensure application-level QSS background renders for this container.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumWidth(256)
        self.setMaximumWidth(256)

        self._project_index: ProjectIndex | None = None
        self._scope_context: str = SidebarContext.ASSETS.value
        self._project_root: Path | None = None
        self._project_display_name_raw: str | None = None
        self._footer_context: str | None = None
        self._recent_tasks_cache: list[RecentTask] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Block 1: Project name (read-only) + separator aligned with top bar bottom (56px)
        _TOP_BAR_HEIGHT = 56
        top_block_56 = QWidget(self)
        top_block_56.setFixedHeight(_TOP_BAR_HEIGHT)
        top_block_56.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        top_block_56_layout = QVBoxLayout(top_block_56)
        top_block_56_layout.setContentsMargins(16, 8, 16, 0)
        top_block_56_layout.setSpacing(0)

        self._project_name_row = QWidget(top_block_56)
        self._project_name_row.setFixedHeight(40)
        self._project_name_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        project_name_row_layout = QHBoxLayout(self._project_name_row)
        project_name_row_layout.setContentsMargins(0, 0, 0, 0)
        project_name_row_layout.setSpacing(0)

        self._project_name_label = QLabel("SELECT PROJECT", self._project_name_row)
        self._project_name_label.setObjectName("SidebarProjectNameLabel")
        self._project_name_label.setWordWrap(False)
        self._project_name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._project_name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._project_name_label.setMinimumWidth(0)
        try:
            pf = self._project_name_label.font()
            pf.setPointSize(11)
            pf.setWeight(QFont.Weight.DemiBold)
            self._project_name_label.setFont(pf)
        except Exception:
            pass
        project_name_row_layout.addWidget(self._project_name_label, 1)

        self._project_name_row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._project_name_row.customContextMenuRequested.connect(self._on_project_name_context_menu)
        top_block_56_layout.addWidget(self._project_name_row, 0)
        top_block_56_layout.addStretch(1)
        sep_top = QFrame(top_block_56)
        sep_top.setObjectName("SidebarNavSeparator")
        sep_top.setFrameShape(QFrame.Shape.HLine)
        sep_top.setFrameShadow(QFrame.Shadow.Sunken)
        sep_top.setFixedHeight(1)
        top_block_56_layout.addWidget(sep_top, 0)

        # --- Block 2: Filters (dept/type lists scroll individually; no common scroll)
        self._filters_center = QWidget(self)
        self._filters_center.setObjectName("SidebarFiltersCenter")
        self._filters_center.setAttribute(Qt.WA_StyledBackground, True)
        self._filters_center.setAutoFillBackground(False)
        self._filters_center.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        scroll_layout = QVBoxLayout(self._filters_center)
        scroll_layout.setContentsMargins(16, 8, 16, 16)  # 8px top so content doesn’t sit under nav pill
        scroll_layout.setSpacing(16)

        f_h = monos_font("Inter", 11, QFont.Weight.ExtraBold)  # 800
        f_h.setLetterSpacing(QFont.PercentageSpacing, 108)

        # Section: FILTERS — lists size to content; spare space below tags.
        self._filters = SidebarWidget(self._filters_center)
        self._filters.departmentClicked.connect(self._on_filter_panel_counts_maybe)
        self._filters.typeClicked.connect(self._on_filter_panel_counts_maybe)
        self._filters.entityScopeChanged.connect(self._on_filter_panel_counts_maybe)
        scroll_layout.addWidget(self._filters.scope_section(), 0, Qt.AlignmentFlag.AlignTop)
        scroll_layout.addWidget(self._filters.type_section(), 0, Qt.AlignmentFlag.AlignTop)
        scroll_layout.addWidget(self._filters.dept_section(), 0, Qt.AlignmentFlag.AlignTop)
        scroll_layout.addWidget(self._filters.tag_section(), 0, Qt.AlignmentFlag.AlignTop)
        self._filters_tail_spacer = QWidget(self._filters_center)
        self._filters_tail_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        scroll_layout.addWidget(self._filters_tail_spacer, 1)
        self._filters_scroll_layout = scroll_layout
        self._filters.set_filters_layout_callback(self._sync_filters_center_layout)
        self._sync_filters_center_layout()
        self._filters.setFixedSize(0, 0)
        self._filters.hide()

        self._dashboard_widgets_center = QWidget(self)
        self._dashboard_widgets_center.setObjectName("SidebarDashboardWidgetsCenter")
        self._dashboard_widgets_center.setAttribute(Qt.WA_StyledBackground, True)
        self._dashboard_widgets_center.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        palette_layout = QVBoxLayout(self._dashboard_widgets_center)
        palette_layout.setContentsMargins(0, 0, 0, 0)
        palette_layout.setSpacing(0)
        self._dashboard_palette = DashboardWidgetPalette(self._dashboard_widgets_center)
        palette_layout.addWidget(self._dashboard_palette, 1)
        self._dashboard_palette.widget_visibility_toggled.connect(
            self.dashboard_widget_visibility_toggled.emit
        )
        self._dashboard_customize_mode = False

        self._center_stack = QStackedWidget(self)
        self._center_stack.setObjectName("SidebarCenterStack")
        self._center_stack.setAttribute(Qt.WA_StyledBackground, True)
        self._center_stack.addWidget(self._filters_center)
        self._center_stack.addWidget(self._dashboard_widgets_center)
        self._center_stack.setCurrentWidget(self._filters_center)

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
        self._tasks_empty = QLabel("No recent tasks")
        self._tasks_empty.setObjectName("SidebarMutedText")
        self._tasks_list_container = _sidebar_filter_list_container()
        self._tasks_list = QListWidget(self._tasks_list_container)
        self._tasks_list_container.layout().addWidget(self._tasks_list)
        self._tasks_list.setObjectName("SidebarRecentTasksList")
        self._tasks_list.setItemDelegate(_SidebarRecentTaskDelegate(self._tasks_list))
        self._tasks_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tasks_list.setFocusPolicy(Qt.NoFocus)
        self._tasks_list.setMouseTracking(True)
        self._tasks_list.setSpacing(2)
        self._tasks_list.setMaximumHeight(5 * _TASK_ROW_HEIGHT + 4 * 2)  # 5 tasks + gaps
        self._tasks_list.itemClicked.connect(self._on_recent_task_item_clicked)
        self._tasks_list.itemDoubleClicked.connect(self._on_recent_task_item_double_clicked)
        self._tasks_page_empty = self._tasks_stacked.addWidget(self._tasks_empty)
        self._tasks_page_list = self._tasks_stacked.addWidget(self._tasks_list_container)

        tasks_layout.addWidget(tasks_header_row, 0)
        tasks_layout.addWidget(self._tasks_stacked, 0)

        root.addWidget(top_block_56, 0)
        root.addWidget(self._center_stack, 1)
        root.addWidget(self._sep_above_tasks, 0)
        root.addWidget(self._tasks_block, 0)

        self._apply_recent_tasks_visibility()

        # Default filter mode: Assets
        self.sync_nav_context(SidebarContext.ASSETS.value, force=True)

        # Start with empty hierarchy until MainWindow provides an index.
        self.set_project_index(None)

    _RECENT_TASKS_VISIBLE_KEY = "sidebar/recent_tasks_visible"
    _APP_SETTINGS_ORG, _APP_SETTINGS_APP = "MonoStudio26", "MonoStudio26"

    def _sync_filters_center_layout(self) -> None:
        """Type above Department when both visible; single filter snaps top; tail spacer fills remainder."""
        lay = getattr(self, "_filters_scroll_layout", None)
        tail = getattr(self, "_filters_tail_spacer", None)
        if lay is None or tail is None:
            return

        scope_w = self._filters.scope_section()
        type_w = self._filters.type_section()
        dept_w = self._filters.dept_section()
        tag_w = self._filters.tag_section()

        type_idx = lay.indexOf(type_w)
        dept_idx = lay.indexOf(dept_w)
        tail_idx = lay.indexOf(tail)
        if min(type_idx, dept_idx, tail_idx) < 0:
            return

        for i in range(lay.count()):
            lay.setStretch(i, 0)

        lay.setAlignment(scope_w, Qt.AlignmentFlag.AlignTop)
        lay.setAlignment(type_w, Qt.AlignmentFlag.AlignTop)
        lay.setAlignment(dept_w, Qt.AlignmentFlag.AlignTop)
        lay.setAlignment(tag_w, Qt.AlignmentFlag.AlignTop)

        type_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        dept_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        tail.setVisible(True)
        tail.setMinimumHeight(0)
        tail.setMaximumHeight(16777215)
        lay.setStretch(tail_idx, 1)

        self._apply_filter_list_heights()

    def _apply_filter_list_heights(self) -> None:
        type_max, dept_max = self._compute_filter_list_max_heights()
        fw = self._filters
        # Type first so it compacts to row count before department claims remaining space.
        if fw.type_section().isVisible() and fw._type_section_expanded and fw._all_types:
            fw.set_type_list_max_height(type_max)
        else:
            fw.set_type_list_max_height(None)
        if fw.dept_section().isVisible() and fw._dept_section_expanded and fw._all_departments:
            fw.set_dept_list_max_height(dept_max)
        else:
            fw.set_dept_list_max_height(None)

    def _compute_filter_list_max_heights(self) -> tuple[int | None, int | None]:
        """Split filters_center height between type and department lists (top → bottom)."""
        center_h = int(self._filters_center.height())
        if center_h <= 1:
            return None, None

        lay = self._filters_scroll_layout
        margins = lay.contentsMargins()
        budget = center_h - margins.top() - margins.bottom()
        spacing = lay.spacing()
        fw = self._filters

        type_list = (
            fw.type_section().isVisible()
            and fw._type_section_expanded
            and bool(fw._all_types)
        )
        dept_list = (
            fw.dept_section().isVisible()
            and fw._dept_section_expanded
            and bool(fw._all_departments)
        )
        if not type_list and not dept_list:
            return None, None

        _HEADER_EXPANDED = 24
        _HEADER_COLLAPSED = 20
        _SECTION_LIST_GAP = 4  # dept/type section layout spacing below header
        visible_blocks: list[QWidget] = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is None or not w.isVisible() or w is self._filters_tail_spacer:
                continue
            visible_blocks.append(w)

        fixed = 0
        type_header = 0
        dept_header = 0
        for w in visible_blocks:
            if w is fw.type_section():
                if type_list:
                    type_header = _HEADER_EXPANDED + _SECTION_LIST_GAP
                else:
                    fixed += _HEADER_COLLAPSED
            elif w is fw.dept_section():
                if dept_list:
                    dept_header = _HEADER_EXPANDED + _SECTION_LIST_GAP
                else:
                    fixed += _HEADER_COLLAPSED
            else:
                fixed += int(w.sizeHint().height())

        fixed += type_header + dept_header
        if len(visible_blocks) > 1:
            fixed += spacing * (len(visible_blocks) - 1)

        list_budget = budget - fixed
        if list_budget <= 0:
            return None, None

        type_content = (
            SidebarWidget._list_content_height(fw._type_list) if type_list else 0
        )
        dept_content = (
            SidebarWidget._list_content_height(fw._dept_list) if dept_list else 0
        )

        if type_list and dept_list:
            if type_content + dept_content <= list_budget:
                return None, None
            return self._split_filter_list_budget(list_budget, type_content, dept_content)
        if type_list:
            return None if type_content <= list_budget else list_budget, None
        return None, None if dept_content <= list_budget else list_budget

    @staticmethod
    def _split_filter_list_budget(
        list_budget: int, type_content: int, _dept_content: int
    ) -> tuple[int, int]:
        """Type keeps natural height; department gets all remaining budget."""
        if type_content >= list_budget:
            half = list_budget // 2
            return half, list_budget - half
        return type_content, list_budget - type_content

    def filters(self) -> SidebarWidget:
        return self._filters

    def _on_filter_panel_counts_maybe(self, *_args) -> None:
        ctx = self.current_context()
        if ctx in (
            SidebarContext.ASSETS.value,
            SidebarContext.SHOTS.value,
            SidebarContext.SCHEDULE.value,
        ):
            self._push_filter_counts()

    _CENTER_STACK_LAYOUT_INDEX = 1  # index in root layout for center stack

    def _apply_center_stack_page(self) -> None:
        """QStackedWidget owns page visibility — never show() a non-current page."""
        if self._dashboard_customize_mode:
            self._center_stack.setCurrentWidget(self._dashboard_widgets_center)
        else:
            self._center_stack.setCurrentWidget(self._filters_center)

    def set_dashboard_customize_mode(self, enabled: bool) -> None:
        """Show widget palette (not filters) while dashboard customize is active."""
        self._dashboard_customize_mode = bool(enabled)
        self._apply_center_stack_page()
        # Give the widget list the full center column (hide recent tasks while customizing).
        show_tasks = not enabled
        self._sep_above_tasks.setVisible(show_tasks)
        self._tasks_block.setVisible(show_tasks)
        self.updateGeometry()

    def sync_dashboard_widget_slots(self, slots: object) -> None:
        self._dashboard_palette.sync_slots(slots)  # type: ignore[arg-type]

    def take_filters_center(self) -> QWidget | None:
        """Remove the filter panel from sidebar layout and return it (for compact filter popup)."""
        w = self._filters_center
        if self._center_stack.indexOf(w) >= 0:
            self._center_stack.removeWidget(w)
        else:
            lay = self.layout()
            if lay is not None:
                lay.removeWidget(w)
        return w

    def restore_filters_center(self, widget: QWidget) -> None:
        """Put the filter panel back into sidebar layout."""
        if self._center_stack.indexOf(widget) < 0:
            widget.setParent(self._center_stack)
            self._center_stack.insertWidget(0, widget)
        widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        widget.setMinimumWidth(0)
        widget.setMaximumWidth(16777215)
        widget.setAttribute(Qt.WA_StyledBackground, True)
        widget.setAutoFillBackground(False)
        # Do not call widget.show() — that leaks the filter page over the dashboard palette.
        self._apply_center_stack_page()
        parent = self._center_stack.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().activate()
        if not self._dashboard_customize_mode:
            self._sync_filters_center_layout()
        self._repolish_widget_style(widget)
        self._repolish_widget_style(self)
        self.updateGeometry()

    @staticmethod
    def _repolish_widget_style(widget: QWidget) -> None:
        st = widget.style()
        if st is not None:
            st.unpolish(widget)
            st.polish(widget)
        widget.update()

    def ensure_filters_center_attached(self) -> None:
        """Re-insert filters block if detached (e.g. compact popup without a clean close)."""
        self.restore_filters_center(self._filters_center)

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

    def set_project_display_name(
        self, name: str | None, *, project_root: Path | None = None
    ) -> None:
        self._project_root = project_root.resolve() if project_root is not None else None
        if not name:
            self._project_display_name_raw = None
            self._project_name_label.setText("SELECT PROJECT")
            self._project_name_label.setToolTip("")
        else:
            self._project_display_name_raw = name
            self._sync_project_name_label_text()
        self._project_name_row.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
            if self._project_display_name_raw
            else Qt.ContextMenuPolicy.NoContextMenu
        )

    def _sync_project_name_label_text(self) -> None:
        raw = self._project_display_name_raw
        if not raw:
            return
        display = raw.upper()
        label = self._project_name_label
        available = max(0, label.width())
        if available > 0:
            fm = QFontMetrics(label.font())
            if fm.horizontalAdvance(display) > available:
                label.setText(fm.elidedText(display, Qt.TextElideMode.ElideRight, available))
                label.setToolTip(display)
                return
        label.setText(display)
        label.setToolTip("")

    def _on_project_name_context_menu(self, pos) -> None:
        if not self._project_display_name_raw:
            return
        menu = MonosMenu(self)
        open_act = None
        root = self._project_root
        if root is not None and root.is_dir():
            open_act = menu.addAction(
                lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]),
                "Open folder",
            )
        copy_act = menu.addAction(
            lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]),
            "Copy name",
        )
        chosen = menu.exec(self._project_name_row.mapToGlobal(pos))
        if chosen is None:
            return
        if open_act is not None and chosen == open_act and root is not None:
            shell_open_folder(root)
        elif chosen == copy_act:
            QApplication.clipboard().setText(self._project_display_name_raw)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._project_display_name_raw:
            self._sync_project_name_label_text()
        if hasattr(self, "_filters_center") and self._filters_center.height() > 1:
            self._apply_filter_list_heights()

    def _rebuild_recent_tasks_list(self) -> None:
        tasks = list(self._recent_tasks_cache)
        self._tasks_clear_btn.setEnabled(bool(tasks))
        if not tasks:
            self._tasks_stacked.setCurrentIndex(self._tasks_page_empty)
            self._tasks_list.clear()
            return
        self._tasks_stacked.setCurrentIndex(self._tasks_page_list)
        _populate_recent_tasks_flat(self._tasks_list, tasks)
        if self._tasks_list.count():
            self._tasks_list.setCurrentRow(0)
        self._fit_recent_tasks_list_height()

    @staticmethod
    def _apply_recent_tasks_list_height(list_w: QListWidget, *, max_rows: int = 5, max_height_px: int | None = None) -> None:
        rows = int(list_w.count())
        if rows <= 0:
            list_w.setFixedHeight(0)
            return
        total_h = sum(max(1, int(list_w.sizeHintForRow(i))) for i in range(rows))
        gap = max(0, rows - 1) * int(list_w.spacing())
        cap = max_rows * _TASK_ROW_HEIGHT + max(0, max_rows - 1) * int(list_w.spacing())
        if max_height_px is not None:
            cap = min(cap, max(1, int(max_height_px)))
        content_h = total_h + gap
        list_w.setMaximumHeight(cap)
        list_w.setFixedHeight(min(content_h, cap))
        list_w.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if content_h > cap else Qt.ScrollBarAlwaysOff
        )

    def _fit_recent_tasks_list_height(self) -> None:
        self._apply_recent_tasks_list_height(self._tasks_list)
        # Container QSS padding 4px top + bottom
        container_h = self._tasks_list.height() + 8
        self._tasks_list_container.setFixedHeight(container_h)
        self._tasks_block_h_expanded = 12 + 20 + 8 + container_h + 8
        if self._recent_tasks_visible_from_settings():
            self._tasks_block.setFixedHeight(self._tasks_block_h_expanded)

    def set_recent_tasks(self, tasks: list[RecentTask]) -> None:
        self._recent_tasks_cache = list(tasks) if tasks else []
        self._rebuild_recent_tasks_list()

    def _recent_task_from_item(self, item: QListWidgetItem | None) -> RecentTask | None:
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        if isinstance(data, dict):
            task = data.get("task")
            if isinstance(task, RecentTask):
                return task
        if isinstance(data, RecentTask):
            return data
        return None

    def _on_recent_task_item_clicked(self, item: QListWidgetItem) -> None:
        task = self._recent_task_from_item(item)
        if task is not None:
            self.recent_task_clicked.emit(task)

    def _on_recent_task_item_double_clicked(self, item: QListWidgetItem) -> None:
        task = self._recent_task_from_item(item)
        if task is not None:
            self.recent_task_double_clicked.emit(task)

    def current_context(self) -> str:
        if self._footer_context is not None:
            return self._footer_context
        return self._scope_context

    def sync_nav_context(self, context_name: str, *, force: bool = False) -> None:
        """Update filter panel for nav context without emitting navigation signals."""
        expected_mode = _filter_mode_for_nav_context(context_name)
        mode_aligned = expected_mode is None or self._filters._mode == expected_mode
        if not force and context_name == self.current_context() and mode_aligned:
            return
        if context_name in (SidebarContext.SHOTS.value, SidebarContext.ASSETS.value):
            self._footer_context = None
            self._scope_context = context_name
            self._filters.setVisible(True)
            if context_name == SidebarContext.SHOTS.value:
                self._filters.set_mode("shots")
            elif context_name == SidebarContext.ASSETS.value:
                self._filters.set_mode("assets")
            self._sync_filters_center_layout()
            return
        if context_name in (
            SidebarContext.DASHBOARD.value,
            SidebarContext.INBOX.value,
            SidebarContext.PROJECT_GUIDE.value,
            SidebarContext.SCHEDULE.value,
            SidebarContext.OUTBOX.value,
            SidebarContext.TRASH.value,
        ):
            self._footer_context = context_name
            if context_name == SidebarContext.DASHBOARD.value:
                self._filters.setVisible(False)
                self._dashboard_customize_mode = False
                self._apply_center_stack_page()
            elif context_name == SidebarContext.INBOX.value:
                self._filters.setVisible(True)
                self._filters.set_mode("inbox")
            elif context_name == SidebarContext.PROJECT_GUIDE.value:
                self._filters.setVisible(True)
                self._filters.set_mode("reference")
            elif context_name == SidebarContext.SCHEDULE.value:
                self._filters.setVisible(True)
                self._filters.set_mode("schedule")
                self._push_filter_counts()
            elif context_name == SidebarContext.OUTBOX.value:
                self._filters.setVisible(True)
                self._filters.set_mode("inbox")
            elif context_name == SidebarContext.TRASH.value:
                self._filters.setVisible(False)
            self._sync_filters_center_layout()
            return

    def set_projects_count(self, value: int | None) -> None:
        pass

    def set_project_index(self, project_index: ProjectIndex | None) -> None:
        """
        UI-only:
        - Keep nav badges (Assets/Shots counts) in sync from already-loaded memory.
        - Push type/department counts to filter panel for label display.
        """
        self._project_index = project_index
        on_shots: set[str] = set()
        on_assets: set[str] = set()
        if project_index is not None:
            for shot in project_index.shots:
                for dep in shot.departments:
                    name = (getattr(dep, "name", None) or "").strip()
                    if name:
                        on_shots.add(name)
            for asset in project_index.assets:
                for dep in asset.departments:
                    name = (getattr(dep, "name", None) or "").strip()
                    if name:
                        on_assets.add(name)
        self._filters.update_schedule_dept_scope_sets(on_shots=on_shots, on_assets=on_assets)
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
        elif ctx in (SidebarContext.SCHEDULE.value, SidebarContext.DASHBOARD.value):
            include_shots, include_assets = self._filters.entity_scope()
            active_type = self._filters.current_type()
            active_type_norm = norm(active_type or "")
            active_is_shot_type = bool(active_type and _is_shot_type(active_type))
            by_type_norm: dict[str, int] = {}
            by_dept_norm: dict[str, int] = {}
            if include_assets:
                for a in pi.assets:
                    at = norm(a.asset_type or "")
                    if at:
                        by_type_norm[at] = by_type_norm.get(at, 0) + 1
                    if active_type and (active_is_shot_type or at != active_type_norm):
                        continue
                    for d in a.departments:
                        dn = norm(getattr(d, "name", None) or "")
                        if dn:
                            by_dept_norm[dn] = by_dept_norm.get(dn, 0) + 1
            shot_count = len(pi.shots) if include_shots else 0
            if include_shots and (not active_type or active_is_shot_type):
                for s in pi.shots:
                    for d in s.departments:
                        dn = norm(getattr(d, "name", None) or "")
                        if dn:
                            by_dept_norm[dn] = by_dept_norm.get(dn, 0) + 1
            for tid in filter_types:
                if _is_shot_type(tid):
                    count_by_type[tid] = shot_count
                else:
                    count_by_type[tid] = by_type_norm.get(norm(tid), 0)
            for did in filter_depts:
                count_by_department[did] = by_dept_norm.get(norm(did), 0)

        return count_by_type, count_by_department

    def _push_filter_counts(self) -> None:
        """Update filter panel with current type/department counts and refresh list labels."""
        ctx = self.current_context()
        if ctx not in (
            SidebarContext.ASSETS.value,
            SidebarContext.SHOTS.value,
            SidebarContext.SCHEDULE.value,
        ):
            self._filters.set_item_counts(None, None)
            return
        count_by_type, count_by_department = self._compute_filter_counts()
        self._filters.set_item_counts(count_by_type, count_by_department)
        self._filters.refresh_list_counts()

# --- SidebarCompact: icon-only vertical sidebar for narrow windows ---

_SIDEBAR_COMPACT_WIDTH = 68


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
        self._footer_buttons: dict[str, QToolButton] = {}
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
        top_block_56_layout.setContentsMargins(0, 0, 0, 0)
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
        top_block_56_layout.addStretch(1)
        top_block_56_layout.addWidget(self._project_switch, 0, Qt.AlignmentFlag.AlignHCenter)
        top_block_56_layout.addStretch(1)
        top_block_56_layout.addWidget(_sep_line(top_block_56), 0)
        root.addWidget(top_block_56, 0)

        # Scope: Projects, Assets, Shots (icon only)
        scope_btns: dict[str, QToolButton] = {}
        _scope_tooltips = {
            SidebarContext.PROJECTS.value: "Projects",
            SidebarContext.ASSETS.value: "Assets",
            SidebarContext.SHOTS.value: "Shots",
        }
        for ctx_name, icon_name in [
            (SidebarContext.PROJECTS.value, "folder-kanban"),
            (SidebarContext.ASSETS.value, "box"),
            (SidebarContext.SHOTS.value, "clapperboard"),
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

        # Footer nav: Inbox, Project Guide, Outbox, Trash (Dashboard/Schedule → title bar)
        footer_btns: dict[str, QToolButton] = {}
        _footer_tooltips = {
            SidebarContext.INBOX.value: "Inbox",
            SidebarContext.PROJECT_GUIDE.value: "Project Guide",
            SidebarContext.OUTBOX.value: "Outbox",
            SidebarContext.TRASH.value: "Trash",
        }
        for ctx_name, icon_name in [
            (SidebarContext.INBOX.value, "inbox"),
            (SidebarContext.PROJECT_GUIDE.value, "folder-open"),
            (SidebarContext.OUTBOX.value, "send"),
            (SidebarContext.TRASH.value, "trash-2"),
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

    def set_current_context(self, context_name: str, *, force: bool = False) -> None:
        if not force and context_name == self.current_context():
            return
        if context_name in (SidebarContext.PROJECTS.value, SidebarContext.SHOTS.value, SidebarContext.ASSETS.value):
            self._footer_context = None
            self._scope_context = context_name
            self._last_context_text = context_name
            self._sync_active_states()
            self.context_changed.emit(context_name)
            return
        if context_name in (
            SidebarContext.DASHBOARD.value,
            SidebarContext.INBOX.value,
            SidebarContext.PROJECT_GUIDE.value,
            SidebarContext.SCHEDULE.value,
            SidebarContext.OUTBOX.value,
            SidebarContext.TRASH.value,
        ):
            self._footer_context = context_name
            self._last_context_text = context_name
            self._sync_active_states()
            self.context_changed.emit(context_name)

    def _sync_active_states(self) -> None:
        ctx = self.current_context()
        on_scope = self._footer_context is None
        for name, btn in self._scope_buttons.items():
            active = on_scope and name == ctx
            btn.setProperty("active", "true" if active else "false")
            color = MONOS_COLORS["blue_400"] if active else MONOS_COLORS["text_label"]
            icon_name = (
                "folder-kanban"
                if name == SidebarContext.PROJECTS.value
                else ("clapperboard" if name == SidebarContext.SHOTS.value else "box")
            )
            ic = lucide_icon(icon_name, size=20, color_hex=color)
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(20, 20))
            if btn.style():
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        _fi = {
            SidebarContext.INBOX.value: "inbox",
            SidebarContext.PROJECT_GUIDE.value: "folder-open",
            SidebarContext.OUTBOX.value: "send",
            SidebarContext.TRASH.value: "trash-2",
        }
        for name, btn in self._footer_buttons.items():
            active = name == ctx
            btn.setProperty("active", "true" if active else "false")
            color = MONOS_COLORS["blue_400"] if active else MONOS_COLORS["text_label"]
            icon_name = _fi.get(name, "inbox")
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
        lst_container = _sidebar_filter_list_container(popup)
        lst = QListWidget(lst_container)
        lst_container.layout().addWidget(lst)
        lst.setObjectName("SidebarRecentTasksList")
        lst.setItemDelegate(_SidebarRecentTaskDelegate(lst))
        lst.setSelectionMode(QAbstractItemView.SingleSelection)
        lst.setFocusPolicy(Qt.NoFocus)
        lst.setMouseTracking(True)
        lst.setSpacing(2)
        lst.setMinimumWidth(220)
        anchor = self._recent_tasks_btn
        max_popup_h = max_popup_height_for_anchor(anchor, gap=4)
        list_max_h = max(80, max_popup_h - 56)
        _populate_recent_tasks_flat(lst, self._recent_tasks)
        Sidebar._apply_recent_tasks_list_height(lst, max_rows=10, max_height_px=list_max_h)
        if lst.count():
            lst.setCurrentRow(0)
        lst.itemClicked.connect(self._on_popup_task_clicked)
        lst.itemDoubleClicked.connect(self._on_popup_task_double_clicked)
        self._recent_tasks_list = lst
        layout.addWidget(lst_container)
        clear_btn = QToolButton(popup)
        clear_btn.setText("Clear")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(lambda: (popup.close(), self.clear_recent_tasks_requested.emit()))
        layout.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignRight)
        self._recent_tasks_popup = popup
        position_popup_near_anchor(popup, anchor)
        popup.show()

    def _on_popup_task_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) if item else None
        if isinstance(data, dict):
            task = data.get("task")
            if isinstance(task, RecentTask):
                self.recent_task_clicked.emit(task)
                return
        if isinstance(data, RecentTask):
            self.recent_task_clicked.emit(data)

    def _on_popup_task_double_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole) if item else None
        task = data.get("task") if isinstance(data, dict) else data
        if isinstance(task, RecentTask):
            self.recent_task_double_clicked.emit(task)
        if self._recent_tasks_popup:
            self._recent_tasks_popup.close()

