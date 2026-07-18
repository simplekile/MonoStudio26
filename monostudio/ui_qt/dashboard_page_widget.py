"""Dashboard page: project overview as a modern bento grid.

Cards: hero (project + health ring), KPI tiles, pipeline health, department
load, next 7 days, and recent notes. All data is derived from
the existing DashboardSnapshot (no extra filesystem scans)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, QSize, Qt, QTimer, Signal, QEvent
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.dashboard_layout import (
    DashboardWidgetSlot,
    load_dashboard_layout,
    save_dashboard_layout,
)
from monostudio.core.fs_reader import ProjectIndex
from monostudio.core.item_comments import ItemCommentEntry
from monostudio.core.production_status import CATEGORY_COLOR_HEX
from monostudio.core.schedule_planner import group_upcoming_due_for_week
from monostudio.core.project_dashboard_stats import (
    DashboardDeptStat,
    DashboardNoteRow,
    DashboardSnapshot,
    build_dashboard_snapshot,
)
from monostudio.core.user_identity import get_current_user, get_current_user_display_name
from monostudio.ui_qt.dashboard_bento_host import DashboardBentoHost
from monostudio.ui_qt.dashboard_responsive_row import (
    DashboardElidedLabel,
    DashboardEntityBadges,
    DashboardResponsiveMixin,
    uniform_dept_chip_width,
)
from monostudio.ui_qt.dashboard_week_strip import DashboardWeekStrip, DASHBOARD_WEEK_STRIP_HEIGHT
from monostudio.ui_qt.dept_workload_popover import DeptWorkloadPopover
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.nav_pill_widgets import UnreadDotBadge
from monostudio.ui_qt.note_author_row import NoteAuthorRow
from monostudio.ui_qt.style import (
    MONOS_COLORS,
    monos_font,
    page_scope_accent,
    page_scope_icon,
    schedule_attention_accent,
    schedule_attention_icon,
)

_COLOR_DONE = CATEGORY_COLOR_HEX.get("done", "#10b981")
_COLOR_PROGRESS = CATEGORY_COLOR_HEX.get("in_progress", "#f59e0b")
_COLOR_WAITING = CATEGORY_COLOR_HEX.get("not_started", "#71717a")
_COLOR_OVERDUE = schedule_attention_accent("overdue")
_COLOR_DUE_SOON = "#60a5fa"


def _dept_workload_attention_badge(text: str, *, accent: str, parent=None) -> QLabel:
    lab = QLabel(text, parent)
    lab.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
    lab.setStyleSheet(
        f"color: {accent}; background: {_hex_to_rgba(accent, 0.14)};"
        "border-radius: 5px; padding: 2px 6px;"
    )
    return lab


_SCOPE_BADGE_LABEL = {"shot": "Shots", "asset": "Assets"}


def _dept_workload_scope_badges(
    *,
    scope: str,
    scheduled: int,
    due_soon: int = 0,
    overdue: int = 0,
    parent=None,
) -> list[QLabel]:
    """Muted scope + count; colored pills only for overdue / due soon."""
    scope_key = (scope or "").strip().lower()
    label = _SCOPE_BADGE_LABEL.get(scope_key, scope_key.title() or "Items")
    meta = MONOS_COLORS.get("text_meta", "#71717a")
    widgets: list[QLabel] = []
    meta_lab = QLabel(f"{label} · {scheduled} scheduled", parent)
    meta_lab.setFont(monos_font("Inter", 10, QFont.Weight.Medium))
    meta_lab.setStyleSheet(f"color: {meta}; background: transparent;")
    widgets.append(meta_lab)
    if overdue > 0:
        widgets.append(
            _dept_workload_attention_badge(f"{overdue} overdue", accent=_COLOR_OVERDUE, parent=parent)
        )
    if due_soon > 0:
        widgets.append(
            _dept_workload_attention_badge(f"{due_soon} soon", accent=_COLOR_DUE_SOON, parent=parent)
        )
    return widgets


def _dept_workload_meta(stat: DashboardDeptStat) -> str:
    parts: list[str] = []
    if stat.overdue:
        parts.append(f"{stat.overdue} overdue")
    if stat.due_soon:
        parts.append(f"{stat.due_soon} due soon")
    if parts:
        return " · ".join(parts)
    if stat.in_progress:
        return f"{stat.in_progress} active"
    return f"{stat.done}/{stat.total} done"


def _dept_workload_bar_segments(stat: DashboardDeptStat) -> list[tuple[float, str]]:
    return [
        (stat.done, _COLOR_DONE),
        (stat.in_progress, _COLOR_PROGRESS),
        (stat.overdue, _COLOR_OVERDUE),
        (stat.waiting, _COLOR_WAITING),
    ]


_DEPT_ROW_H = 44
_DEPT_LIST_MAX_ROWS = 8
_DEPT_LIST_VIEWPORT_H = _DEPT_LIST_MAX_ROWS * (_DEPT_ROW_H + 4)
_NEXT_DAY_ROW_PREVIEW = 4
_NEXT_OVERDUE_PREVIEW = 5
_NEXT_UNFILTERED_MAX_ROWS = 8
# Fixed list viewport height (stable bento card when switching strip days).
_NEXT_ROW_H = 40
_NOTE_ROW_H = _NEXT_ROW_H
_CARD_HEADER_H = 26
_CARD_SECTION_SPACING = 10
_NEXT_LIST_VIEWPORT_H = _NEXT_UNFILTERED_MAX_ROWS * (_NEXT_ROW_H + 4)
# Notes card has no week strip — reclaim that vertical space in the list viewport.
_NOTES_LIST_VIEWPORT_H = _NEXT_LIST_VIEWPORT_H + DASHBOARD_WEEK_STRIP_HEIGHT + _CARD_SECTION_SPACING
_NOTES_VISIBLE_MAX = max(8, _NOTES_LIST_VIEWPORT_H // (_NOTE_ROW_H + 4))


def _hex_to_rgba(hex_str: str, alpha: float) -> str:
    s = (hex_str or "").lstrip("#")
    if len(s) != 6:
        return f"rgba(113, 113, 122, {alpha})"
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return f"rgba(113, 113, 122, {alpha})"
    return f"rgba({r}, {g}, {b}, {alpha})"


def _relative_due(due: date, today: date) -> tuple[str, bool]:
    """Return (label, is_overdue) using friendly relative wording."""
    delta = (due - today).days
    if delta < 0:
        n = -delta
        return (f"{n}d overdue", True)
    if delta == 0:
        return ("Today", False)
    if delta == 1:
        return ("Tomorrow", False)
    return (f"in {delta}d", False)


class _HealthRing(QWidget):
    """Donut showing completion split (done/progress/waiting), DPI-aware paint."""

    _OUTER = 92

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._OUTER, self._OUTER)
        self._segments: list[tuple[float, str]] = []
        self._sub_base_pt = 7.0
        self._center = QWidget(self)
        self._center.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QVBoxLayout(self._center)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value = QLabel("—", self._center)
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value.setStyleSheet("background: transparent; color: #fafafa;")
        self._value.setFont(monos_font("Inter", 14, QFont.Weight.ExtraBold))
        self._sub = QLabel("no plan", self._center)
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub.setStyleSheet("background: transparent; color: #71717a;")
        self._sub.setFont(monos_font("Inter", int(self._sub_base_pt), QFont.Weight.DemiBold))
        lay.addWidget(self._value, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._sub, 0, Qt.AlignmentFlag.AlignHCenter)
        self._position_center_labels()

    def _ring_metrics(self) -> tuple[int, float, float]:
        side = min(self.width(), self.height())
        thickness = max(6, int(side * 0.07))
        margin = thickness / 2 + 2
        return side, thickness, margin

    def _inner_hole_side(self) -> int:
        side, thickness, margin = self._ring_metrics()
        return max(36, int(side - 2 * (margin + thickness)))

    def _position_center_labels(self) -> None:
        inner = self._inner_hole_side()
        x = (self.width() - inner) // 2
        y = (self.height() - inner) // 2
        self._center.setGeometry(x, y, inner, inner)
        self._fit_sub_font()

    def _fit_sub_font(self) -> None:
        text = (self._sub.text() or "").strip()
        if not text:
            self._sub.setVisible(False)
            return
        self._sub.setVisible(True)
        max_w = max(24, self._center.width() - 8)
        pt = self._sub_base_pt
        font = monos_font("Inter", int(pt), QFont.Weight.DemiBold)
        while pt > 5.0:
            metrics = QFontMetrics(font)
            if metrics.horizontalAdvance(text) <= max_w:
                break
            pt -= 0.5
            font = monos_font("Inter", max(5, int(round(pt))), QFont.Weight.DemiBold)
        self._sub.setFont(font)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_center_labels()

    def set_data(self, segments: list[tuple[float, str]], center: str, sub: str) -> None:
        self._segments = [(max(0.0, float(v)), c) for v, c in segments]
        self._value.setText(center)
        sub_clean = (sub or "").strip()
        if sub_clean.lower() == "complete":
            sub_clean = "done"
        self._sub.setText(sub_clean)
        pct = (center or "").strip()
        tip = f"{pct} {sub_clean}".strip() if sub_clean else pct
        self.setToolTip(tip)
        self._fit_sub_font()
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _side, thickness, margin = self._ring_metrics()
        rect = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)
        track = QPen(QColor("#27272a"), thickness)
        track.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        total = sum(v for v, _ in self._segments)
        if total > 0:
            start = 90 * 16
            for value, color in self._segments:
                if value <= 0:
                    continue
                span = -int(round(value / total * 360 * 16))
                pen = QPen(QColor(color), thickness)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                p.setPen(pen)
                p.drawArc(rect, start, span)
                start += span
        p.end()


class _StackedBar(QWidget):
    """Thin horizontal stacked bar with rounded caps."""

    def __init__(self, parent=None, *, height: int = 10) -> None:
        super().__init__(parent)
        self._segments: list[tuple[float, str]] = []
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_segments(self, segments: list[tuple[float, str]]) -> None:
        self._segments = [(max(0.0, float(v)), c) for v, c in segments]
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        radius = h / 2.0
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 0, w, h), QColor("#27272a"))
        total = sum(v for v, _ in self._segments)
        if total > 0:
            x = 0.0
            for value, color in self._segments:
                if value <= 0:
                    continue
                seg_w = value / total * w
                p.fillRect(QRectF(x, 0, seg_w + 0.5, h), QColor(color))
                x += seg_w
        p.end()


class _StageStepper(QWidget):
    """Pipeline lifecycle stepper: No Plan → In Progress → At Risk → Complete."""

    _STAGES = ("No Plan", "In Progress", "At Risk", "Complete")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active = 0
        self.setMinimumHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_active(self, index: int) -> None:
        self._active = max(0, min(int(index), len(self._STAGES) - 1))
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        n = len(self._STAGES)
        pad = 48.0
        w = float(self.width())
        node_y = 18.0
        usable = max(1.0, w - 2 * pad)
        xs = [pad + usable * i / (n - 1) for i in range(n)]
        active_color = QColor("#3b82f6")
        idle = QColor("#3f3f46")

        line_idle = QPen(idle, 2)
        line_idle.setCapStyle(Qt.PenCapStyle.FlatCap)
        line_act = QPen(active_color, 2)
        line_act.setCapStyle(Qt.PenCapStyle.FlatCap)
        for i in range(n - 1):
            p.setPen(line_act if i < self._active else line_idle)
            p.drawLine(int(xs[i]), int(node_y), int(xs[i + 1]), int(node_y))

        p.setPen(Qt.PenStyle.NoPen)
        for i, x in enumerate(xs):
            if i == self._active:
                p.setBrush(QColor(59, 130, 246, 60))
                p.drawEllipse(QRectF(x - 11, node_y - 11, 22, 22))
                p.setBrush(active_color)
                p.drawEllipse(QRectF(x - 6, node_y - 6, 12, 12))
            elif i < self._active:
                p.setBrush(active_color)
                p.drawEllipse(QRectF(x - 5, node_y - 5, 10, 10))
            else:
                p.setPen(QPen(idle, 2))
                p.setBrush(QColor("#18181b"))
                p.drawEllipse(QRectF(x - 5, node_y - 5, 10, 10))
                p.setPen(Qt.PenStyle.NoPen)

        p.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        col_w = usable / (n - 1)
        for i, x in enumerate(xs):
            p.setPen(QColor("#fafafa") if i == self._active else QColor("#71717a"))
            rect = QRectF(x - col_w / 2.0, node_y + 12.0, col_w, 18.0)
            p.drawText(rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), self._STAGES[i])
        p.end()


class _MetricTile(QFrame):
    clicked = Signal()

    def __init__(
        self,
        label: str,
        icon_name: str,
        accent_hex: str,
        link_text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardMetricTile")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._accent = accent_hex
        self.setProperty("clickable", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        icon_box = QLabel(self)
        icon_box.setFixedSize(34, 34)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setStyleSheet(
            f"background-color: {_hex_to_rgba(accent_hex, 0.15)}; border-radius: 9px;"
        )
        icon = lucide_icon(icon_name, size=18, color_hex=accent_hex)
        if not icon.isNull():
            icon_box.setPixmap(icon.pixmap(18, 18))
        cap = QLabel(label.upper(), self)
        cap.setObjectName("DashboardTileLabel")
        top.addWidget(icon_box, 0, Qt.AlignVCenter)
        top.addWidget(cap, 1, Qt.AlignVCenter)
        lay.addLayout(top)

        self._value = QLabel("—", self)
        self._value.setFont(monos_font("Inter", 26, QFont.Weight.Bold))
        self._value.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_primary', '#fafafa')}; background: transparent;"
        )
        lay.addWidget(self._value)

        self._link = QPushButton(f"{link_text}  \u2192", self)
        self._link.setObjectName("DashboardTileLink")
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.setFlat(True)
        self._link.clicked.connect(self.clicked.emit)
        lay.addWidget(self._link, 0, Qt.AlignLeft)

    def set_value(self, text: str) -> None:
        self._value.setText(text)

    def set_tone(self, danger: bool, *, accent_hex: str | None = None) -> None:
        accent = (accent_hex or schedule_attention_accent("overdue")).strip()
        if danger:
            tone = (
                "danger-unscheduled"
                if accent == schedule_attention_accent("unscheduled")
                else "danger-overdue"
            )
        else:
            tone = ""
        self.setProperty("tone", tone)
        self._value.setStyleSheet(
            f"color: {accent if danger else MONOS_COLORS.get('text_primary', '#fafafa')}; background: transparent;"
        )
        st = self.style()
        if st is not None:
            st.unpolish(self)
            st.polish(self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            QTimer.singleShot(0, self.clicked.emit)


class _ClickableRow(QFrame):
    clicked = Signal()

    def __init__(self, parent=None, *, clickable: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._clickable = clickable
        self.setProperty("clickable", "true" if clickable else "false")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if not self._clickable or event.button() != Qt.MouseButton.LeftButton:
            return
        row = self

        def _emit() -> None:
            try:
                from shiboken6 import isValid

                if isValid(row):
                    row.clicked.emit()
            except RuntimeError:
                pass

        QTimer.singleShot(0, _emit)


class _NoteDashboardRow(DashboardResponsiveMixin, _ClickableRow):
    """Note row with context menu: open notes / jump to department."""

    open_notes = Signal()
    go_to_department = Signal()

    def __init__(self, parent=None, *, has_department: bool = False) -> None:
        super().__init__(parent)
        self._has_department = bool(has_department)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self._trailing_meta = None
        self._entity_badges = None

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._apply_responsive_layout()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu(self)
        menu.addAction("Open notes…", self.open_notes.emit)
        go = menu.addAction("Go to department", self.go_to_department.emit)
        go.setEnabled(self._has_department)
        if not self._has_department:
            go.setToolTip("This note has no department tag")
        menu.exec(event.globalPos())


def _chip(text: str, color_hex: str, parent=None) -> QLabel:
    lab = QLabel(text, parent)
    lab.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
    lab.setStyleSheet(
        f"background-color: {_hex_to_rgba(color_hex, 0.16)}; color: {color_hex};"
        "border-radius: 6px; padding: 2px 8px;"
    )
    return lab


class _ScheduleDueRow(DashboardResponsiveMixin, _ClickableRow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._trailing_meta = None
        self._entity_badges = None

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_responsive_layout()


class _DeptLoadRow(DashboardResponsiveMixin, _ClickableRow):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._trailing_meta = None
        self._entity_badges = None

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_responsive_layout()


def _dot(color_hex: str, size: int = 9, parent=None) -> QLabel:
    d = QLabel(parent)
    d.setFixedSize(size, size)
    d.setStyleSheet(f"background-color: {color_hex}; border-radius: {size // 2}px;")
    return d


def _card(
    title: str | None,
    *,
    object_name: str = "DashboardCard",
    right_widget: QWidget | None = None,
    body_spacing: int = _CARD_SECTION_SPACING,
    header_height: int = _CARD_HEADER_H,
) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName(object_name)
    card.setAttribute(Qt.WA_StyledBackground, True)
    body = QVBoxLayout(card)
    body.setContentsMargins(16, 14, 16, 14)
    body.setSpacing(body_spacing)
    if title:
        header_host = QWidget(card)
        header_host.setObjectName("DashboardCardHeader")
        header_host.setFixedHeight(header_height)
        header = QHBoxLayout(header_host)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        t = QLabel(title, header_host)
        t.setObjectName("DashboardCardTitle")
        t.setFixedHeight(header_height)
        t.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        t.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.addWidget(t, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        if right_widget is not None:
            right_widget.setFixedHeight(header_height)
            right_widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            header.addWidget(right_widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        body.addWidget(header_host)
    return card, body


def _empty_state(icon_name: str, text: str) -> QWidget:
    """Centered faded-icon illustration + hint, matching the bento empty look."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 4, 0, 4)
    v.setSpacing(10)
    v.addStretch(1)
    ic = QLabel(w)
    icon = lucide_icon(icon_name, size=40, color_hex="#3f3f46")
    if not icon.isNull():
        ic.setPixmap(icon.pixmap(40, 40))
    ic.setAlignment(Qt.AlignCenter)
    v.addWidget(ic, 0, Qt.AlignCenter)
    lab = QLabel(text, w)
    lab.setObjectName("DashboardEmptyHint")
    lab.setAlignment(Qt.AlignCenter)
    v.addWidget(lab, 0, Qt.AlignCenter)
    v.addStretch(1)
    return w


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            # Hide before orphaning — setParent(None) would otherwise flash a top-level window.
            w.hide()
            w.setParent(None)
            w.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                _clear_layout(sub)


class DashboardPageWidget(QWidget):
    """Project home: bento metrics, pipeline health, due work, open notes."""

    open_schedule_requested = Signal()
    schedule_jump_requested = Signal(str, str, str, str)  # kind, rel, dept, due_iso
    unscheduled_entities_requested = Signal(object)  # list[tuple[str, str]] kind, rel
    overdue_entities_requested = Signal(object)  # list[tuple[str, str]] kind, rel
    open_notes_entity_requested = Signal(object)  # DashboardNoteRow
    note_go_to_department_requested = Signal(object)  # DashboardNoteRow
    dashboard_entity_nav_requested = Signal(str, str, str, str)  # kind, rel, dept, name
    open_scope_requested = Signal(str)  # "Assets" | "Shots"
    customize_mode_changed = Signal(bool)
    dashboard_layout_changed = Signal(object)  # list[DashboardWidgetSlot]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardPage")
        self._project_root: Path | None = None
        self._workspace_root: Path | None = None
        self._snapshot: DashboardSnapshot | None = None
        # Shared sidebar/Schedule department filter (None allowed = no whitelist).
        self._allowed_departments: set[str] | None = None
        self._workload_departments: set[str] | None = None
        self._workload_department_order: tuple[str, ...] = ()
        self._workload_shot_departments: set[str] = set()
        self._workload_asset_departments: set[str] = set()
        self._hidden_departments: set[str] = set()
        self._respect_hidden: bool = True
        self._dept_scope: str = "all"
        self._include_shots: bool = True
        self._include_assets: bool = True
        self._notes_filter: str = "all"  # "all" | "mentions"
        self._next_week_filter_day: date | None = None
        self._next_week_expanded: bool = False
        self._settings = QSettings("MonoStudio26", "MonoStudio26")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("DashboardScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setAttribute(Qt.WA_StyledBackground, True)
        outer.addWidget(self._scroll, 1)

        body = QWidget(self._scroll)
        body.setObjectName("DashboardRoot")
        self._scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        self._empty = QLabel(
            "Select a project using the project switcher in the sidebar.",
            body,
        )
        self._empty.setWordWrap(True)
        self._empty.setObjectName("DialogHint")
        root.addWidget(self._empty)

        self._grid_host = QWidget(body)
        grid_outer = QVBoxLayout(self._grid_host)
        grid_outer.setContentsMargins(0, 0, 0, 0)
        grid_outer.setSpacing(0)
        root.addWidget(self._grid_host)
        root.addStretch(1)

        self._build_cards()
        self._widget_by_id = {
            "header": self._header_card,
            "kpi": self._kpi_host,
            "pipeline_health": self._health_card,
            "dept_load": self._dept_card,
            "next_7_days": self._next_card,
            "recent_notes": self._notes_card,
        }
        self._bento = DashboardBentoHost(
            self._widget_by_id,
            initial_slots=load_dashboard_layout(self._settings),
            parent=self._grid_host,
        )
        grid_outer.addWidget(self._bento)
        self._bento.layout_committed.connect(self._persist_bento_layout)
        self._bento.layout_changed.connect(self._on_bento_layout_changed)
        self._bento.edit_mode_changed.connect(self._on_bento_edit_mode_changed)

        self._dept_workload_popover = DeptWorkloadPopover(self)
        self._dept_workload_popover.open_schedule.connect(
            lambda dept_id: self.schedule_jump_requested.emit("", "", dept_id, "")
        )
        self._dept_workload_popover.entity_nav.connect(self.dashboard_entity_nav_requested.emit)

    # --- card construction -------------------------------------------------
    def _build_cards(self) -> None:
        # Hero / header card
        self._header_card, hb = _card(None, object_name="DashboardHeaderCard")
        hb.setContentsMargins(20, 18, 20, 18)
        hrow = QHBoxLayout()
        hrow.setSpacing(16)
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self._title = QLabel("Welcome back \U0001f44b", self._header_card)
        self._title.setObjectName("DashboardProjectTitle")
        self._subtitle = QLabel(
            "Here's what's happening with your project today.", self._header_card
        )
        self._subtitle.setObjectName("DashboardWelcomeSub")
        title_col.addStretch(1)
        title_col.addWidget(self._title)
        title_col.addWidget(self._subtitle)
        title_col.addStretch(1)

        self._ring = _HealthRing(self._header_card)
        self._btn_schedule = QPushButton("Open Schedule", self._header_card)
        self._btn_schedule.setObjectName("DashboardPrimaryButton")
        self._btn_schedule.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_schedule.setIcon(lucide_icon("calendar", size=14, color_hex="#ffffff"))
        self._btn_schedule.clicked.connect(self.open_schedule_requested.emit)

        self._btn_customize = QPushButton("Customize", self._header_card)
        self._btn_customize.setObjectName("DashboardGhostButton")
        self._btn_customize.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_customize.setIcon(lucide_icon("layout-dashboard", size=14, color_hex="#a1a1aa"))
        self._btn_customize.setToolTip(
            "Open the widget sidebar to add, remove, and rearrange dashboard cards"
        )
        self._btn_customize.clicked.connect(self._enter_customize_mode)

        actions_col = QVBoxLayout()
        actions_col.setSpacing(8)
        actions_col.addWidget(self._btn_customize, 0, Qt.AlignRight)
        actions_col.addWidget(self._btn_schedule, 0, Qt.AlignRight)

        hrow.addLayout(title_col, 1)
        hrow.addWidget(self._ring, 0, Qt.AlignVCenter)
        hrow.addLayout(actions_col, 0)
        hb.addLayout(hrow)

        # KPI tiles (plain host, tiles carry their own border)
        self._kpi_host = QWidget()
        kpi_row = QHBoxLayout(self._kpi_host)
        kpi_row.setContentsMargins(0, 0, 0, 0)
        kpi_row.setSpacing(12)
        self._tile_assets = _MetricTile(
            "Assets",
            page_scope_icon("asset"),
            page_scope_accent("asset"),
            "View all assets",
        )
        self._tile_shots = _MetricTile(
            "Shots",
            page_scope_icon("shot"),
            page_scope_accent("shot"),
            "View all shots",
        )
        self._tile_notes = _MetricTile("Open notes", "file-text", "#8b5cf6", "View notes")
        self._tile_overdue = _MetricTile(
            "Overdue",
            schedule_attention_icon("overdue"),
            schedule_attention_accent("overdue"),
            "View tasks",
        )
        self._tile_unscheduled = _MetricTile(
            "Unscheduled",
            schedule_attention_icon("unscheduled"),
            schedule_attention_accent("unscheduled"),
            "View unscheduled",
        )
        self._tile_assets.clicked.connect(lambda: self.open_scope_requested.emit("Assets"))
        self._tile_shots.clicked.connect(lambda: self.open_scope_requested.emit("Shots"))
        self._tile_notes.clicked.connect(self._scroll_to_notes)
        self._tile_overdue.clicked.connect(self._on_overdue_tile_clicked)
        self._tile_unscheduled.clicked.connect(self._emit_unscheduled)
        for t in (
            self._tile_assets,
            self._tile_shots,
            self._tile_notes,
            self._tile_overdue,
            self._tile_unscheduled,
        ):
            kpi_row.addWidget(t, 1)

        # Pipeline health card
        self._health_card, phb = _card("Pipeline Health")
        self._health_note = QLabel("", self._health_card)
        self._health_note.setObjectName("DashboardEmptyHint")
        phb.addWidget(self._health_note)
        self._health_stepper = _StageStepper(self._health_card)
        phb.addWidget(self._health_stepper)
        self._health_bar = _StackedBar(self._health_card, height=12)
        phb.addWidget(self._health_bar)
        self._health_legend = QHBoxLayout()
        self._health_legend.setSpacing(14)
        phb.addLayout(self._health_legend)
        phb.addStretch(1)

        # Department workload card
        self._dept_card, dlb = _card("Department workload")
        self._dept_list_scroll = QScrollArea(self._dept_card)
        self._dept_list_scroll.setObjectName("DashboardDeptListScroll")
        self._dept_list_scroll.setWidgetResizable(True)
        self._dept_list_scroll.setFrameShape(QFrame.NoFrame)
        self._dept_list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dept_list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._dept_list_scroll.setFixedHeight(_DEPT_LIST_VIEWPORT_H)
        self._dept_list_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._dept_list_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._dept_list_scroll.viewport().setAttribute(Qt.WA_StyledBackground, True)
        self._dept_list_scroll.viewport().setAutoFillBackground(False)
        self._dept_list_host = QWidget(self._dept_card)
        self._dept_list_host.setObjectName("DashboardDeptListHost")
        self._dept_list_host.setAttribute(Qt.WA_StyledBackground, True)
        self._dept_list_host.setAutoFillBackground(False)
        self._dept_list = QVBoxLayout(self._dept_list_host)
        self._dept_list.setContentsMargins(0, 0, 0, 0)
        self._dept_list.setSpacing(4)
        self._dept_list.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)
        self._dept_list_scroll.setWidget(self._dept_list_host)
        dlb.addWidget(self._dept_list_scroll)
        dlb.addStretch(1)

        # Next 7 days card
        self._next_card, nxb = _card("Next 7 Days")
        self._next_week_strip = DashboardWeekStrip(self._next_card)
        nxb.addWidget(self._next_week_strip)
        self._next_week_strip.day_clicked.connect(self._on_next_week_day_clicked)
        self._next_list_scroll = QScrollArea(self._next_card)
        self._next_list_scroll.setObjectName("DashboardNextListScroll")
        self._next_list_scroll.setWidgetResizable(True)
        self._next_list_scroll.setFrameShape(QFrame.NoFrame)
        self._next_list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._next_list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._next_list_scroll.setFixedHeight(_NEXT_LIST_VIEWPORT_H)
        self._next_list_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._next_list_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._next_list_scroll.viewport().setAttribute(Qt.WA_StyledBackground, True)
        self._next_list_host = QWidget(self._next_card)
        self._next_list_host.setObjectName("DashboardNextListHost")
        self._next_list_host.setAttribute(Qt.WA_StyledBackground, True)
        self._next_list = QVBoxLayout(self._next_list_host)
        self._next_list.setContentsMargins(0, 0, 0, 0)
        self._next_list.setSpacing(4)
        self._next_list.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)
        self._next_list_scroll.setWidget(self._next_list_host)
        nxb.addWidget(self._next_list_scroll)
        next_footer = QHBoxLayout()
        next_footer.setContentsMargins(0, 0, 0, 0)
        next_footer.setSpacing(8)
        self._next_show_more_btn = QPushButton(self._next_card)
        self._next_show_more_btn.setObjectName("DashboardTileLink")
        self._next_show_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_show_more_btn.setFlat(True)
        self._next_show_more_btn.setVisible(False)
        self._next_show_more_btn.clicked.connect(self._toggle_next_week_expanded)
        next_footer.addWidget(self._next_show_more_btn, 0, Qt.AlignLeft)
        next_footer.addStretch(1)
        self._next_schedule_link = QPushButton("Open Schedule  \u2192", self._next_card)
        self._next_schedule_link.setObjectName("DashboardTileLink")
        self._next_schedule_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_schedule_link.setFlat(True)
        self._next_schedule_link.clicked.connect(self.open_schedule_requested.emit)
        next_footer.addWidget(self._next_schedule_link, 0, Qt.AlignRight)
        nxb.addLayout(next_footer)

        # Recent notes — title + filters on header row; taller list fills week-strip slot
        self._notes_filter_host = self._build_notes_filter_bar()
        self._notes_card, ntb = _card("Recent Notes", right_widget=self._notes_filter_host)
        self._notes_list_scroll = QScrollArea(self._notes_card)
        self._notes_list_scroll.setObjectName("DashboardNotesListScroll")
        self._notes_list_scroll.setWidgetResizable(True)
        self._notes_list_scroll.setFrameShape(QFrame.NoFrame)
        self._notes_list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._notes_list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._notes_list_scroll.setFixedHeight(_NOTES_LIST_VIEWPORT_H)
        self._notes_list_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._notes_list_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._notes_list_scroll.viewport().setAttribute(Qt.WA_StyledBackground, True)
        self._notes_list_scroll.viewport().installEventFilter(self)
        self._notes_list_host = QWidget(self._notes_card)
        self._notes_list_host.setObjectName("DashboardNotesListHost")
        self._notes_list_host.setAttribute(Qt.WA_StyledBackground, True)
        self._notes_list_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._notes_list_host.setMinimumWidth(0)
        self._notes_list = QVBoxLayout(self._notes_list_host)
        self._notes_list.setContentsMargins(0, 0, 0, 0)
        self._notes_list.setSpacing(4)
        self._notes_list.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinAndMaxSize)
        self._notes_list_scroll.setWidget(self._notes_list_host)
        ntb.addWidget(self._notes_list_scroll)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if (
            hasattr(self, "_notes_list_scroll")
            and obj is self._notes_list_scroll.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._sync_notes_list_geometry()
        return super().eventFilter(obj, event)

    def _sync_notes_list_geometry(self) -> None:
        if not hasattr(self, "_notes_list_scroll"):
            return
        for i in range(self._notes_list.count()):
            item = self._notes_list.itemAt(i)
            if item is None:
                continue
            row = item.widget()
            if row is not None and hasattr(row, "_apply_responsive_layout"):
                row._apply_responsive_layout()

    def _enter_customize_mode(self) -> None:
        self._bento.enter_edit_mode()

    def _persist_bento_layout(self, slots: object) -> None:
        save_dashboard_layout(self._settings, list(slots))  # type: ignore[arg-type]

    def _on_bento_edit_mode_changed(self, enabled: bool) -> None:
        self._btn_customize.setVisible(not enabled)
        self.customize_mode_changed.emit(enabled)

    def _on_bento_layout_changed(self, slots: object) -> None:
        self.dashboard_layout_changed.emit(slots)

    def is_customize_mode(self) -> bool:
        return self._bento.is_edit_mode()

    def exit_customize_mode(self) -> None:
        self._bento.exit_edit_mode()

    def dashboard_slots(self) -> list[DashboardWidgetSlot]:
        return self._bento.slots()

    def set_dashboard_widget_visible(self, widget_id: str, visible: bool) -> None:
        if visible:
            self._bento.show_widget(widget_id)
        else:
            self._bento.hide_widget(widget_id)

    def _build_notes_filter_bar(self) -> QWidget:
        host = QWidget()
        host.setObjectName("DashboardNotesFilterBar")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._btn_notes_all = QPushButton("All open", host)
        self._btn_notes_all.setObjectName("DashboardNotesFilterBtn")
        self._btn_notes_all.setCheckable(True)
        self._btn_notes_all.setChecked(True)
        self._btn_notes_all.setFixedHeight(_CARD_HEADER_H)
        self._btn_notes_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_notes_mentions = QPushButton("Mentions me", host)
        self._btn_notes_mentions.setObjectName("DashboardNotesFilterBtn")
        self._btn_notes_mentions.setCheckable(True)
        self._btn_notes_mentions.setFixedHeight(_CARD_HEADER_H)
        self._btn_notes_mentions.setCursor(Qt.CursorShape.PointingHandCursor)
        self._notes_filter_group = QButtonGroup(host)
        self._notes_filter_group.setExclusive(True)
        self._notes_filter_group.addButton(self._btn_notes_all, 0)
        self._notes_filter_group.addButton(self._btn_notes_mentions, 1)
        self._notes_filter_group.idClicked.connect(self._on_notes_filter_clicked)
        self._mentions_unread_dot = UnreadDotBadge(self._btn_notes_mentions)
        row.addWidget(self._btn_notes_all, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._btn_notes_mentions, 0, Qt.AlignmentFlag.AlignVCenter)
        return host

    def _sync_mentions_unread_dot(self, has_unread: bool) -> None:
        dot = self._mentions_unread_dot
        btn = self._btn_notes_mentions
        dot.move(max(0, btn.width() - 10), 3)
        dot.setVisible(bool(has_unread))
        if has_unread:
            dot.raise_()

    def _on_notes_filter_clicked(self, button_id: int) -> None:
        mode = "all" if button_id == 0 else "mentions"
        self._notes_filter = mode
        if self._snapshot is not None:
            self._update_notes(self._snapshot)

    def _set_notes_filter(self, mode: str) -> None:
        mode = (mode or "all").strip().lower()
        if mode not in ("all", "mentions"):
            return
        self._notes_filter = mode
        btn = self._btn_notes_all if mode == "all" else self._btn_notes_mentions
        self._notes_filter_group.blockSignals(True)
        btn.setChecked(True)
        self._notes_filter_group.blockSignals(False)
        if self._snapshot is not None:
            self._update_notes(self._snapshot)

    def _scroll_to_notes(self) -> None:
        self._scroll.ensureWidgetVisible(self._notes_card)

    def _scroll_to_mention_notes(self) -> None:
        self._set_notes_filter("mentions")
        self._scroll_to_notes()

    def _dept_label(self, dept_id: str) -> str:
        did = (dept_id or "").strip()
        if not did:
            return ""
        if self._project_root is not None:
            try:
                from monostudio.core.department_registry import DepartmentRegistry

                return DepartmentRegistry.for_project(self._project_root).get_department_label(did)
            except OSError:
                pass
        return did.replace("_", " ").title()

    def _add_note_row(self, note: DashboardNoteRow) -> None:
        row = _NoteDashboardRow(self._notes_list_host, has_department=bool(note.department))
        row.setFixedHeight(_NOTE_ROW_H)
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.clicked.connect(lambda n=note: self.open_notes_entity_requested.emit(n))
        row.open_notes.connect(lambda n=note: self.open_notes_entity_requested.emit(n))
        row.go_to_department.connect(lambda n=note: self.note_go_to_department_requested.emit(n))
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 0, 8, 0)
        rl.setSpacing(8)
        stub = ItemCommentEntry(
            id=note.comment_id,
            at=note.at,
            author=note.author,
            text=".",
            author_id=note.author_id,
            department=note.department,
        )
        uid = (note.author_id or "").strip()
        on_author = None
        if uid:

            def _open_profile(u: str = uid) -> None:
                from monostudio.ui_qt.user_profile_view_dialog import open_studio_user_profile

                open_studio_user_profile(self._workspace_root, u, parent=self)

            on_author = _open_profile

        avatar = NoteAuthorRow.for_entry(
            stub,
            self._workspace_root,
            avatar_size=22,
            avatar_only=True,
            on_author_click=on_author,
            parent=row,
        )
        rl.addWidget(avatar, 0, Qt.AlignVCenter)
        body = DashboardElidedLabel(
            note.text.replace("\n", " ").strip(),
            font=monos_font("Inter", 12),
            parent=row,
        )
        body.setStyleSheet("color: #d4d4d8; background: transparent;")
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        rl.addWidget(body, 1, Qt.AlignVCenter)
        trailing = QWidget(row)
        trailing.setObjectName("DashboardNoteRowTrailing")
        trailing.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        tl = QHBoxLayout(trailing)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        badges = DashboardEntityBadges(
            entity_kind=note.entity_kind,
            entity_name=note.entity_name,
            department=note.department,
            dept_label=self._dept_label(note.department),
            dept_chip_width=0,
            on_entity_click=lambda _=False, n=note: self.note_go_to_department_requested.emit(n),
            parent=trailing,
        )
        tl.addWidget(badges, 0, Qt.AlignVCenter)
        when = QLabel(note.at[:16].replace("T", " "), trailing)
        when.setObjectName("DashboardMutedMeta")
        when.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        tl.addWidget(when, 0, Qt.AlignVCenter)
        rl.addWidget(trailing, 0, Qt.AlignVCenter)
        row.bind_responsive_parts(
            trailing_meta=when,
            entity_badges=badges,
            body=body,
            trailing_host=trailing,
            leading_width=avatar.sizeHint().width() + rl.spacing(),
            body_fills_row=True,
        )
        self._notes_list.addWidget(row)
        QTimer.singleShot(0, self._sync_notes_list_geometry)

    def _emit_unscheduled(self) -> None:
        keys = list(self._snapshot.unscheduled_entities) if self._snapshot else []
        if keys:
            self.unscheduled_entities_requested.emit(keys)
        else:
            self.open_schedule_requested.emit()

    def _on_overdue_tile_clicked(self) -> None:
        self.overdue_entities_requested.emit(
            list(self._snapshot.overdue_entities) if self._snapshot else []
        )

    def overdue_entity_rows(self):
        if self._snapshot is None:
            return ()
        return self._snapshot.overdue_entity_rows

    # --- public API --------------------------------------------------------
    def set_project_root(self, path: Path | None) -> None:
        self._project_root = Path(path) if path else None

    def set_workspace_root(self, path: Path | None) -> None:
        self._workspace_root = Path(path) if path else None

    def set_dept_filter(
        self,
        *,
        allowed_department_ids: list[str] | set[str] | None,
        workload_department_ids: list[str] | set[str] | None = None,
        workload_department_order: tuple[str, ...] | list[str] | None = None,
        workload_shot_department_ids: set[str] | list[str] | None = None,
        workload_asset_department_ids: set[str] | list[str] | None = None,
        hidden_departments: set[str] | None = None,
        respect_hidden: bool = True,
        dept_scope: str = "leaf",
        include_shots: bool = True,
        include_assets: bool = True,
    ) -> None:
        """Mirror Schedule's department visibility so hidden/out-of-list depts disappear."""
        self._allowed_departments = (
            None if allowed_department_ids is None else set(allowed_department_ids)
        )
        self._workload_departments = (
            None
            if workload_department_ids is None
            else set(workload_department_ids)
        )
        order = workload_department_order or workload_department_ids or ()
        self._workload_department_order = tuple(
            (d or "").strip() for d in order if (d or "").strip()
        )
        self._workload_shot_departments = set(workload_shot_department_ids or ())
        self._workload_asset_departments = set(workload_asset_department_ids or ())
        self._hidden_departments = set(hidden_departments or set())
        self._respect_hidden = bool(respect_hidden)
        self._dept_scope = (dept_scope or "all").strip() or "all"
        self._include_shots = bool(include_shots)
        self._include_assets = bool(include_assets)
        if not self._include_shots and not self._include_assets:
            self._include_shots = True

    def refresh(self, project_index: ProjectIndex | None) -> None:
        if self._project_root is None or project_index is None:
            self._snapshot = None
            self._empty.setVisible(True)
            self._grid_host.setVisible(False)
            return

        self._empty.setVisible(False)
        self._grid_host.setVisible(True)

        snap = build_dashboard_snapshot(
            self._project_root,
            assets=project_index.assets,
            shots=project_index.shots,
            workspace_root=self._workspace_root,
            project_index=project_index,
            include_shots=self._include_shots,
            include_assets=self._include_assets,
            allowed_departments=self._allowed_departments,
            workload_departments=self._workload_departments,
            workload_department_order=self._workload_department_order,
            workload_shot_departments=self._workload_shot_departments,
            workload_asset_departments=self._workload_asset_departments,
            hidden_departments=self._hidden_departments,
            respect_hidden=self._respect_hidden,
            dept_scope=self._dept_scope,
        )
        self._snapshot = snap
        if snap is None:
            return

        self._update_header(snap)
        self._update_kpis(snap)
        self._update_health(snap)
        self._update_dept_load(snap)
        self._update_next_7_days(snap)
        self._update_notes(snap)
        self._sync_mentions_unread_dot(snap.unread_mention_count > 0)

    def set_mentions_unread_dot(self, visible: bool) -> None:
        self._sync_mentions_unread_dot(visible)

    # --- per-card population ------------------------------------------------
    def _update_header(self, snap: DashboardSnapshot) -> None:
        user_name = get_current_user_display_name(self._workspace_root)
        self._title.setText(f"Welcome back, {user_name} \U0001f44b")
        self._title.setToolTip(str(self._project_root) if self._project_root else "")

        if snap.total_bars > 0:
            self._ring.set_data(
                [
                    (snap.done_count, _COLOR_DONE),
                    (snap.in_progress_count, _COLOR_PROGRESS),
                    (snap.waiting_count, _COLOR_WAITING),
                ],
                f"{int(round(snap.completion_pct))}%",
                "done",
            )
        else:
            self._ring.set_data([], "—", "no plan")

    def _update_kpis(self, snap: DashboardSnapshot) -> None:
        self._tile_assets.set_value(str(snap.assets_count))
        self._tile_shots.set_value(str(snap.shots_count))
        self._tile_notes.set_value(str(snap.open_notes_count))
        self._tile_overdue.set_value(str(snap.overdue_count))
        self._tile_overdue.set_tone(
            snap.overdue_count > 0,
            accent_hex=schedule_attention_accent("overdue"),
        )
        self._tile_unscheduled.set_value(str(snap.unscheduled_count))
        self._tile_unscheduled.set_tone(
            snap.unscheduled_count > 0,
            accent_hex=schedule_attention_accent("unscheduled"),
        )

    def _update_health(self, snap: DashboardSnapshot) -> None:
        _clear_layout(self._health_legend)
        if snap.total_bars <= 0:
            self._health_bar.set_segments([])
            self._health_bar.setVisible(False)
            self._health_note.setText("No plan yet — open Schedule to set delivery targets.")
            self._health_note.setVisible(True)
            self._health_stepper.set_active(0)
            return
        self._health_note.setVisible(False)
        self._health_bar.setVisible(True)
        # Lifecycle stage: At Risk if anything overdue, else Complete at 100%,
        # otherwise In Progress.
        if snap.overdue_count > 0:
            self._health_stepper.set_active(2)
        elif snap.completion_pct >= 100.0:
            self._health_stepper.set_active(3)
        else:
            self._health_stepper.set_active(1)
        self._health_bar.set_segments(
            [
                (snap.done_count, _COLOR_DONE),
                (snap.in_progress_count, _COLOR_PROGRESS),
                (snap.waiting_count, _COLOR_WAITING),
            ]
        )
        for label, count, color in (
            ("Done", snap.done_count, _COLOR_DONE),
            ("In progress", snap.in_progress_count, _COLOR_PROGRESS),
            ("Waiting", snap.waiting_count, _COLOR_WAITING),
        ):
            item = QHBoxLayout()
            item.setSpacing(6)
            item.addWidget(_dot(color, 8))
            lab = QLabel(f"{label} {count}")
            lab.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
            lab.setStyleSheet("color: #a1a1aa; background: transparent;")
            item.addWidget(lab)
            self._health_legend.addLayout(item)
        self._health_legend.addStretch(1)
        note = QLabel(f"based on {snap.total_bars} planned tasks")
        note.setObjectName("DashboardMutedMeta")
        self._health_legend.addWidget(note)

    def _toggle_next_week_expanded(self) -> None:
        self._next_week_expanded = not self._next_week_expanded
        if self._snapshot is not None:
            self._update_next_7_days(self._snapshot)

    def _on_next_week_day_clicked(self, day: object) -> None:
        if day is not None and not isinstance(day, date):
            return
        if self._next_week_filter_day == day:
            self._next_week_filter_day = None
        else:
            self._next_week_filter_day = day
        self._next_week_expanded = False
        if self._next_week_strip is not None:
            self._next_week_strip.set_selected(self._next_week_filter_day)
        if self._snapshot is not None:
            self._update_next_7_days(self._snapshot)

    def _add_next_empty_state(self, text: str) -> None:
        wrap = QWidget(self._next_list_host)
        wrap.setMinimumHeight(_NEXT_LIST_VIEWPORT_H)
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        lay.addWidget(_empty_state("calendar", text))
        lay.addStretch(1)
        self._next_list.addWidget(wrap)

    def _dept_chip_width_for_rows(self, rows) -> int:
        return uniform_dept_chip_width(
            [item.department_label for item in rows if (item.department or "").strip()]
        )

    def _add_upcoming_due_rows(
        self,
        rows,
        *,
        dept_chip_width: int,
        today: date,
        preview_limit: int | None,
        row_budget: list[int] | None = None,
    ) -> int:
        """Render rows; return count of hidden rows when capped."""
        total = len(rows)
        if total <= 0:
            return 0
        limit = total
        if preview_limit is not None:
            limit = min(limit, preview_limit)
        if row_budget is not None:
            limit = min(limit, max(0, row_budget[0]))
        visible = list(rows)[:limit]
        for item in visible:
            self._add_upcoming_due_row(item, dept_chip_width=dept_chip_width, today=today)
            if row_budget is not None:
                row_budget[0] -= 1
        return max(0, total - len(visible))

    def _open_dept_workload_popover(self, anchor: QWidget, stat: DashboardDeptStat) -> None:
        self._dept_workload_popover.show_for_department(
            anchor=anchor,
            stat=stat,
            snapshot=self._snapshot,
        )

    def _add_dept_load_row(self, s: DashboardDeptStat) -> None:
        row = _DeptLoadRow(self._dept_list_host)
        row.setFixedHeight(_DEPT_ROW_H)
        row.clicked.connect(
            lambda _checked=False, st=s, anchor=row: self._open_dept_workload_popover(anchor, st)
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 4, 8, 4)
        rl.setSpacing(8)
        dot_color = s.color_hex if s.total > 0 else "#52525b"
        rl.addWidget(_dot(dot_color, 9), 0, Qt.AlignTop)
        left = QWidget(row)
        left.setStyleSheet("background: transparent;")
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(3)
        name = DashboardElidedLabel(
            s.department_label,
            font=monos_font("Inter", 13, QFont.Weight.DemiBold),
            parent=left,
        )
        name.setStyleSheet("color: #fafafa; background: transparent;")
        left_l.addWidget(name, 0)
        chip_widgets: list[QLabel] = []
        if s.applies_to_shots:
            chip_widgets.extend(
                _dept_workload_scope_badges(
                    scope="shot",
                    scheduled=s.shots_total,
                    due_soon=s.shots_due_soon,
                    overdue=s.shots_overdue,
                    parent=left,
                )
            )
        if s.applies_to_assets:
            chip_widgets.extend(
                _dept_workload_scope_badges(
                    scope="asset",
                    scheduled=s.assets_total,
                    due_soon=s.assets_due_soon,
                    overdue=s.assets_overdue,
                    parent=left,
                )
            )
        if chip_widgets:
            chips_host = QWidget(left)
            chips_host.setStyleSheet("background: transparent;")
            chips_l = QHBoxLayout(chips_host)
            chips_l.setContentsMargins(0, 0, 0, 0)
            chips_l.setSpacing(4)
            for chip in chip_widgets:
                chips_l.addWidget(chip, 0)
            chips_l.addStretch(1)
            left_l.addWidget(chips_host, 0)
        rl.addWidget(left, 1)
        bar = _StackedBar(row, height=6)
        bar.setMinimumWidth(40)
        bar.setMaximumWidth(80)
        bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        if s.total > 0:
            bar.set_segments(_dept_workload_bar_segments(s))
        rl.addWidget(bar, 0, Qt.AlignVCenter)
        meta = QLabel("—" if s.total <= 0 else _dept_workload_meta(s), row)
        meta.setObjectName("DashboardMutedMeta")
        meta.setMinimumWidth(72)
        meta.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if s.total > 0:
            if s.overdue:
                meta.setStyleSheet(
                    f"color: {_COLOR_OVERDUE}; font-family: 'Inter'; font-size: 11px; font-weight: 600;"
                )
            elif s.due_soon:
                meta.setStyleSheet(
                    f"color: {_COLOR_DUE_SOON}; font-family: 'Inter'; font-size: 11px; font-weight: 600;"
                )
        rl.addWidget(meta, 0)
        row.bind_responsive_parts(trailing_meta=meta)
        self._dept_list.addWidget(row)

    def _update_dept_load(self, snap: DashboardSnapshot) -> None:
        _clear_layout(self._dept_list)
        stats = list(snap.dept_stats)
        if not stats:
            self._dept_list.addWidget(_empty_state("layers", "No departments in Schedule"))
            return
        for s in stats:
            self._add_dept_load_row(s)

    def _add_upcoming_due_row(self, item, *, dept_chip_width: int, today: date) -> None:
        row = _ScheduleDueRow(self._next_list_host)
        row.setFixedHeight(_NEXT_ROW_H)
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.clicked.connect(
            lambda _checked=False, it=item: self.schedule_jump_requested.emit(
                it.entity_kind,
                it.entity_rel,
                it.department,
                it.due.isoformat(),
            )
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 0, 8, 0)
        rl.setSpacing(8)
        badges = DashboardEntityBadges(
            entity_kind=item.entity_kind,
            entity_name=item.entity_name,
            department=item.department,
            dept_label=item.department_label,
            dept_chip_width=dept_chip_width,
            on_entity_click=lambda _=False, it=item: self.dashboard_entity_nav_requested.emit(
                it.entity_kind,
                it.entity_rel,
                it.department or "",
                it.entity_name,
            ),
            parent=row,
        )
        rl.addWidget(badges, 0, Qt.AlignVCenter)
        rl.addStretch(1)
        due_text, is_overdue = _relative_due(item.due, today)
        due = QLabel(due_text, row)
        due.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.DemiBold))
        due.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        due.setStyleSheet(
            f"color: {schedule_attention_accent('overdue') if is_overdue else '#a1a1aa'}; background: transparent;"
        )
        rl.addWidget(due, 0, Qt.AlignRight | Qt.AlignVCenter)
        row._trailing_meta_pinned = True  # type: ignore[attr-defined]
        row.bind_responsive_parts(trailing_meta=due, entity_badges=badges, leading_width=0)
        self._next_list.addWidget(row)

    def _update_next_7_days(self, snap: DashboardSnapshot) -> None:
        _clear_layout(self._next_list)
        if hasattr(self, "_next_list_scroll"):
            self._next_list_scroll.verticalScrollBar().setValue(0)
        rows = list(snap.upcoming_due)
        today = date.today()
        week = group_upcoming_due_for_week(rows, today=today)
        counts = {d: len(bucket) for d, bucket in week.by_day}
        self._next_week_strip.set_week(today=today, counts=counts)
        self._next_week_strip.set_selected(self._next_week_filter_day)

        has_overdue = bool(week.overdue)
        has_upcoming = any(bucket for _, bucket in week.by_day)
        if not has_overdue and not has_upcoming:
            self._next_show_more_btn.setVisible(False)
            self._add_next_empty_state("Nothing due in the next 7 days")
            return

        expanded = self._next_week_expanded
        filter_day = self._next_week_filter_day
        if expanded:
            day_preview: int | None = None
            overdue_preview: int | None = None
            row_budget: list[int] | None = None
        elif filter_day is not None:
            day_preview = _NEXT_DAY_ROW_PREVIEW
            overdue_preview = _NEXT_OVERDUE_PREVIEW
            row_budget = None
        else:
            day_preview = None
            overdue_preview = None
            row_budget = [_NEXT_UNFILTERED_MAX_ROWS]
        hidden_total = 0

        if has_overdue:
            if row_budget is None or row_budget[0] > 0:
                dept_w = self._dept_chip_width_for_rows(week.overdue)
                hidden_total += self._add_upcoming_due_rows(
                    week.overdue,
                    dept_chip_width=dept_w,
                    today=today,
                    preview_limit=overdue_preview,
                    row_budget=row_budget,
                )
            else:
                hidden_total += len(week.overdue)

        day_sections: list[tuple[date, tuple]] = []
        for d, bucket in week.by_day:
            if not bucket:
                continue
            if filter_day is not None and d != filter_day:
                continue
            day_sections.append((d, bucket))

        for _d, bucket in day_sections:
            if row_budget is not None and row_budget[0] <= 0:
                hidden_total += len(bucket)
                continue
            dept_w = self._dept_chip_width_for_rows(bucket)
            hidden_total += self._add_upcoming_due_rows(
                bucket,
                dept_chip_width=dept_w,
                today=today,
                preview_limit=day_preview,
                row_budget=row_budget,
            )

        if hidden_total > 0:
            self._next_show_more_btn.setText(f"+{hidden_total} more")
            self._next_show_more_btn.setToolTip(
                f"{hidden_total} more task{'s' if hidden_total != 1 else ''} in this view"
            )
            self._next_show_more_btn.setVisible(True)
        elif expanded:
            self._next_show_more_btn.setText("Show less")
            self._next_show_more_btn.setToolTip("")
            self._next_show_more_btn.setVisible(True)
        else:
            self._next_show_more_btn.setVisible(False)

    def _update_notes(self, snap: DashboardSnapshot) -> None:
        _clear_layout(self._notes_list)
        self._notes_filter_group.blockSignals(True)
        self._btn_notes_all.setChecked(self._notes_filter == "all")
        self._btn_notes_mentions.setChecked(self._notes_filter == "mentions")
        self._notes_filter_group.blockSignals(False)
        mention_n = snap.mention_notes_count
        self._btn_notes_mentions.setText(
            f"Mentions me ({mention_n})" if mention_n else "Mentions me"
        )
        self._sync_mentions_unread_dot(snap.unread_mention_count > 0)
        if self._notes_filter == "mentions":
            notes = list(snap.mention_notes)
            if not get_current_user(self._workspace_root):
                self._notes_list.addWidget(
                    self._empty_hint("Sign in to see notes that mention you")
                )
                self._sync_notes_list_geometry()
                return
            if not notes:
                self._notes_list.addWidget(self._empty_hint("No open notes mention you"))
                self._sync_notes_list_geometry()
                return
        else:
            notes = list(snap.open_notes)
            if not notes:
                self._notes_list.addWidget(self._empty_hint("No open notes"))
                self._sync_notes_list_geometry()
                return
        visible = notes[:_NOTES_VISIBLE_MAX]
        for note in visible:
            self._add_note_row(note)
        self._sync_notes_list_geometry()

    def _empty_hint(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("DashboardEmptyHint")
        return lab
