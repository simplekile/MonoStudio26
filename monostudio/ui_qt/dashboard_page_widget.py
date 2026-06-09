"""Dashboard page: project overview as a modern bento grid.

Cards: hero (project + health ring), KPI tiles, pipeline health, department
load, next 7 days, needs attention, and recent notes. All data is derived from
the existing DashboardSnapshot (no extra filesystem scans)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, QSize, Qt, QTimer, Signal
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

from monostudio.core.dashboard_layout import load_dashboard_layout, save_dashboard_layout
from monostudio.core.fs_reader import ProjectIndex
from monostudio.core.item_comments import ItemCommentEntry
from monostudio.core.production_status import CATEGORY_COLOR_HEX
from monostudio.core.project_dashboard_stats import (
    DashboardNoteRow,
    DashboardSnapshot,
    build_dashboard_snapshot,
)
from monostudio.core.user_identity import get_current_user, get_current_user_display_name
from monostudio.ui_qt.dashboard_bento_host import DashboardBentoHost
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.nav_pill_widgets import UnreadDotBadge
from monostudio.ui_qt.note_author_row import NoteAuthorRow
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

_COLOR_DONE = CATEGORY_COLOR_HEX.get("done", "#10b981")
_COLOR_PROGRESS = CATEGORY_COLOR_HEX.get("in_progress", "#f59e0b")
_COLOR_WAITING = CATEGORY_COLOR_HEX.get("not_started", "#71717a")
_COLOR_BLOCKED = CATEGORY_COLOR_HEX.get("blocked", "#ef4444")
_RED = "#ef4444"
_DEPT_LOAD_PREVIEW = 8
_DASHBOARD_ENTITY_COL_W = 248


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

    def set_tone(self, danger: bool) -> None:
        self.setProperty("tone", "danger" if danger else "")
        self._value.setStyleSheet(
            f"color: {_RED if danger else MONOS_COLORS.get('text_primary', '#fafafa')}; background: transparent;"
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


class _NoteDashboardRow(_ClickableRow):
    """Note row with context menu: open notes / jump to department."""

    open_notes = Signal()
    go_to_department = Signal()

    def __init__(self, parent=None, *, has_department: bool = False) -> None:
        super().__init__(parent)
        self._has_department = bool(has_department)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

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


def _uniform_dept_btn_width(labels: list[str]) -> int:
    """Width for dept buttons so every row matches the longest label."""
    clean = [(lbl or "").strip() for lbl in labels if (lbl or "").strip()]
    if not clean:
        return 0
    font = monos_font("Inter", 10, QFont.Weight.Bold)
    metrics = QFontMetrics(font)
    # Match DashboardNoteDeptBtn QSS: horizontal padding 2×8.
    pad_border = 16
    return max(metrics.horizontalAdvance(lbl) for lbl in clean) + pad_border


def _style_entity_name_btn(btn: QPushButton, entity_kind: str) -> None:
    btn.setObjectName("DashboardEntityNameBtn")
    btn.setFlat(True)
    tone = "shot" if (entity_kind or "").strip().lower() == "shot" else "asset"
    btn.setProperty("chipTone", tone)
    st = btn.style()
    if st is not None:
        st.unpolish(btn)
        st.polish(btn)


class _DashboardEntityBlock(QWidget):
    """Fixed-width entity column: name chip (fit content) · department · stretch."""

    _NAME_PAD = 16

    def __init__(
        self,
        *,
        entity_kind: str,
        entity_name: str,
        department: str,
        dept_label: str,
        dept_btn_width: int,
        on_entity_click,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setFixedWidth(_DASHBOARD_ENTITY_COL_W)
        self._full_name = (entity_name or "").strip()
        self._name_font = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        self._dept_btn_width = max(0, int(dept_btn_width))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._name_btn = QPushButton(self)
        self._name_btn.setFont(self._name_font)
        _style_entity_name_btn(self._name_btn, entity_kind)
        self._name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name_btn.setToolTip("Open this asset/shot in the main view")
        self._name_btn.clicked.connect(on_entity_click)
        lay.addWidget(self._name_btn, 0, Qt.AlignVCenter)

        self._dept_btn: QPushButton | None = None
        if (department or "").strip():
            self._dept_btn = QPushButton(dept_label, self)
            self._dept_btn.setObjectName("DashboardNoteDeptBtn")
            self._dept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._dept_btn.setToolTip("Open this asset/shot in the main view")
            self._dept_btn.clicked.connect(on_entity_click)
            if self._dept_btn_width > 0:
                self._dept_btn.setFixedWidth(self._dept_btn_width)
            lay.addWidget(self._dept_btn, 0, Qt.AlignVCenter)

        lay.addStretch(1)
        self._refresh_name_chip()

    def _max_name_chip_width(self) -> int:
        used = 6 if self._dept_btn is not None else 0
        if self._dept_btn is not None:
            used += self._dept_btn_width
        return max(40, self.width() - used)

    def _refresh_name_chip(self) -> None:
        max_w = self._max_name_chip_width()
        text_max = max(24, max_w - self._NAME_PAD)
        metrics = QFontMetrics(self._name_font)
        elided = metrics.elidedText(self._full_name, Qt.TextElideMode.ElideRight, text_max)
        self._name_btn.setText(elided)
        chip_w = min(metrics.horizontalAdvance(elided) + self._NAME_PAD, max_w)
        self._name_btn.setFixedWidth(max(chip_w, 40))
        tip = self._full_name if elided != self._full_name else ""
        self._name_btn.setToolTip(tip or "Open this asset/shot in the main view")

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_name_chip()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_name_chip()


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
) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName(object_name)
    card.setAttribute(Qt.WA_StyledBackground, True)
    body = QVBoxLayout(card)
    body.setContentsMargins(16, 14, 16, 14)
    body.setSpacing(10)
    if title:
        header = QHBoxLayout()
        header.setSpacing(8)
        t = QLabel(title, card)
        t.setObjectName("DashboardCardTitle")
        header.addWidget(t, 0, Qt.AlignVCenter)
        header.addStretch(1)
        if right_widget is not None:
            header.addWidget(right_widget, 0, Qt.AlignVCenter)
        body.addLayout(header)
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project_root: Path | None = None
        self._workspace_root: Path | None = None
        self._snapshot: DashboardSnapshot | None = None
        # Shared sidebar/Schedule department filter (None allowed = no whitelist).
        self._allowed_departments: set[str] | None = None
        self._hidden_departments: set[str] = set()
        self._respect_hidden: bool = True
        self._dept_scope: str = "all"
        self._notes_filter: str = "all"  # "all" | "mentions"
        self._dept_load_expanded: bool = False
        self._settings = QSettings("MonoStudio26", "MonoStudio26")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
            "needs_attention": self._attention_card,
            "recent_notes": self._notes_card,
        }
        self._bento = DashboardBentoHost(
            self._widget_by_id,
            initial_slots=load_dashboard_layout(self._settings),
            parent=self._grid_host,
        )
        grid_outer.addWidget(self._bento)
        self._bento.layout_committed.connect(self._persist_bento_layout)
        self._bento.edit_mode_changed.connect(self._on_bento_edit_mode_changed)

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
        self._btn_customize.setToolTip("Rearrange and show or hide dashboard widgets")
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
        self._tile_assets = _MetricTile("Assets", "layers", "#3b82f6", "View all assets")
        self._tile_shots = _MetricTile("Shots", "clapperboard", "#14b8a6", "View all shots")
        self._tile_notes = _MetricTile("Open notes", "file-text", "#8b5cf6", "View notes")
        self._tile_overdue = _MetricTile("Overdue", "triangle-alert", "#f97316", "View tasks")
        self._tile_unscheduled = _MetricTile("Unscheduled", "calendar", "#ef4444", "View unscheduled")
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

        # Department load card
        self._dept_card, dlb = _card("Department Load")
        self._dept_list_host = QWidget(self._dept_card)
        self._dept_list = QVBoxLayout(self._dept_list_host)
        self._dept_list.setContentsMargins(0, 0, 0, 0)
        self._dept_list.setSpacing(4)
        dlb.addWidget(self._dept_list_host)
        self._dept_show_more_btn = QPushButton("", self._dept_card)
        self._dept_show_more_btn.setObjectName("DashboardTileLink")
        self._dept_show_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dept_show_more_btn.setFlat(True)
        self._dept_show_more_btn.setVisible(False)
        self._dept_show_more_btn.clicked.connect(self._toggle_dept_load_expanded)
        dlb.addWidget(self._dept_show_more_btn, 0, Qt.AlignLeft)
        dlb.addStretch(1)

        # Next 7 days card
        self._next_card, nxb = _card("Next 7 Days")
        self._next_list_host = QWidget(self._next_card)
        self._next_list = QVBoxLayout(self._next_list_host)
        self._next_list.setContentsMargins(0, 0, 0, 0)
        self._next_list.setSpacing(2)
        nxb.addWidget(self._next_list_host)
        nxb.addStretch(1)

        # Needs attention card
        self._attention_badge = QLabel("0")
        self._attention_badge.setObjectName("DashboardAttentionBadge")
        self._attention_badge.setVisible(False)
        self._attention_card, atb = _card(
            "Needs Attention", right_widget=self._attention_badge
        )
        self._attention_list_host = QWidget(self._attention_card)
        self._attention_list = QVBoxLayout(self._attention_list_host)
        self._attention_list.setContentsMargins(0, 0, 0, 0)
        self._attention_list.setSpacing(4)
        atb.addWidget(self._attention_list_host)
        atb.addStretch(1)

        # Recent notes card (All open vs Mentions me)
        self._notes_filter_host = self._build_notes_filter_bar()
        self._notes_card, ntb = _card("Recent Notes", right_widget=self._notes_filter_host)
        self._notes_list_host = QWidget(self._notes_card)
        self._notes_list = QVBoxLayout(self._notes_list_host)
        self._notes_list.setContentsMargins(0, 0, 0, 0)
        self._notes_list.setSpacing(2)
        ntb.addWidget(self._notes_list_host)
        ntb.addStretch(1)

    def _enter_customize_mode(self) -> None:
        self._bento.enter_edit_mode()

    def _persist_bento_layout(self, slots: object) -> None:
        save_dashboard_layout(self._settings, list(slots))  # type: ignore[arg-type]

    def _on_bento_edit_mode_changed(self, enabled: bool) -> None:
        self._btn_customize.setVisible(not enabled)

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
        self._btn_notes_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_notes_mentions = QPushButton("Mentions me", host)
        self._btn_notes_mentions.setObjectName("DashboardNotesFilterBtn")
        self._btn_notes_mentions.setCheckable(True)
        self._btn_notes_mentions.setCursor(Qt.CursorShape.PointingHandCursor)
        self._notes_filter_group = QButtonGroup(host)
        self._notes_filter_group.setExclusive(True)
        self._notes_filter_group.addButton(self._btn_notes_all, 0)
        self._notes_filter_group.addButton(self._btn_notes_mentions, 1)
        self._notes_filter_group.idClicked.connect(self._on_notes_filter_clicked)
        self._mentions_unread_dot = UnreadDotBadge(self._btn_notes_mentions)
        row.addWidget(self._btn_notes_all)
        row.addWidget(self._btn_notes_mentions)
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

    def _add_note_row(self, note: DashboardNoteRow, *, dept_btn_width: int = 0) -> None:
        row = _NoteDashboardRow(self._notes_list_host, has_department=bool(note.department))
        row.clicked.connect(lambda n=note: self.open_notes_entity_requested.emit(n))
        row.open_notes.connect(lambda n=note: self.open_notes_entity_requested.emit(n))
        row.go_to_department.connect(lambda n=note: self.note_go_to_department_requested.emit(n))
        rl = QHBoxLayout(row)
        rl.setContentsMargins(6, 5, 6, 5)
        rl.setSpacing(8)
        entity_block = _DashboardEntityBlock(
            entity_kind=note.entity_kind,
            entity_name=note.entity_name,
            department=note.department,
            dept_label=self._dept_label(note.department),
            dept_btn_width=dept_btn_width,
            on_entity_click=lambda _=False, n=note: self.note_go_to_department_requested.emit(n),
            parent=row,
        )
        rl.addWidget(entity_block, 0, Qt.AlignVCenter)
        text = note.text.replace("\n", " ").strip()
        if len(text) > 90:
            text = text[:89].rstrip() + "…"
        body = QLabel(text, row)
        body.setFont(monos_font("Inter", 12))
        body.setStyleSheet("color: #d4d4d8; background: transparent;")
        rl.addWidget(body, 1)
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

        rl.addWidget(
            NoteAuthorRow.for_entry(
                stub,
                self._workspace_root,
                avatar_size=22,
                name_only=True,
                on_author_click=on_author,
                parent=row,
            ),
            0,
            Qt.AlignRight | Qt.AlignVCenter,
        )
        when = QLabel(note.at[:16].replace("T", " "), row)
        when.setObjectName("DashboardMutedMeta")
        rl.addWidget(when, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._notes_list.addWidget(row)

    def _emit_unscheduled(self) -> None:
        keys = list(self._snapshot.unscheduled_entities) if self._snapshot else []
        if keys:
            self.unscheduled_entities_requested.emit(keys)
        else:
            self.open_schedule_requested.emit()

    def _on_overdue_tile_clicked(self) -> None:
        keys = list(self._snapshot.overdue_entities) if self._snapshot else []
        if keys:
            self.overdue_entities_requested.emit(keys)
        else:
            self.open_schedule_requested.emit()

    # --- public API --------------------------------------------------------
    def set_project_root(self, path: Path | None) -> None:
        self._project_root = Path(path) if path else None

    def set_workspace_root(self, path: Path | None) -> None:
        self._workspace_root = Path(path) if path else None

    def set_dept_filter(
        self,
        *,
        allowed_department_ids: list[str] | set[str] | None,
        hidden_departments: set[str] | None = None,
        respect_hidden: bool = True,
        dept_scope: str = "leaf",
    ) -> None:
        """Mirror Schedule's department visibility so hidden/out-of-list depts disappear."""
        self._allowed_departments = (
            None if allowed_department_ids is None else set(allowed_department_ids)
        )
        self._hidden_departments = set(hidden_departments or set())
        self._respect_hidden = bool(respect_hidden)
        self._dept_scope = (dept_scope or "all").strip() or "all"

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
            allowed_departments=self._allowed_departments,
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
        self._update_attention(snap)
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
        self._tile_overdue.set_tone(snap.overdue_count > 0)
        self._tile_unscheduled.set_value(str(snap.unscheduled_count))
        self._tile_unscheduled.set_tone(snap.unscheduled_count > 0)

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

    def _toggle_dept_load_expanded(self) -> None:
        self._dept_load_expanded = not self._dept_load_expanded
        if self._snapshot is not None:
            self._update_dept_load(self._snapshot)

    def _add_dept_load_row(self, s) -> None:
        row = _ClickableRow(self._dept_list_host)
        row.clicked.connect(
            lambda _checked=False, dep_id=s.department: self.schedule_jump_requested.emit(
                "", "", dep_id, ""
            )
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(6, 5, 6, 5)
        rl.setSpacing(8)
        rl.addWidget(_dot(s.color_hex, 9), 0, Qt.AlignVCenter)
        name = QLabel(s.department_label, row)
        name.setFont(monos_font("Inter", 12, QFont.Weight.DemiBold))
        name.setStyleSheet("color: #e4e4e7; background: transparent;")
        rl.addWidget(name, 1)
        bar = _StackedBar(row, height=6)
        bar.setFixedWidth(96)
        done_part = s.done
        rest = max(0, s.total - s.done)
        bar.set_segments([(done_part, _COLOR_DONE), (rest, _COLOR_WAITING)])
        rl.addWidget(bar, 0, Qt.AlignVCenter)
        meta = QLabel(f"{s.done}/{s.total}", row)
        meta.setObjectName("DashboardMutedMeta")
        meta.setMinimumWidth(44)
        meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rl.addWidget(meta, 0)
        if s.overdue > 0:
            rl.addWidget(_chip(f"{s.overdue} late", _RED, row), 0)
        self._dept_list.addWidget(row)

    def _update_dept_load(self, snap: DashboardSnapshot) -> None:
        _clear_layout(self._dept_list)
        stats = [s for s in snap.dept_stats if s.total > 0]
        if not stats:
            self._dept_show_more_btn.setVisible(False)
            self._dept_list.addWidget(_empty_state("layers", "No scheduled work yet"))
            return
        total = len(stats)
        if total <= _DEPT_LOAD_PREVIEW:
            visible = stats
            self._dept_show_more_btn.setVisible(False)
        elif self._dept_load_expanded:
            visible = stats
            self._dept_show_more_btn.setText("Show less")
            self._dept_show_more_btn.setVisible(True)
        else:
            visible = stats[:_DEPT_LOAD_PREVIEW]
            hidden = total - _DEPT_LOAD_PREVIEW
            self._dept_show_more_btn.setText(f"Show all ({total})")
            self._dept_show_more_btn.setToolTip(f"{hidden} more department{'s' if hidden != 1 else ''}")
            self._dept_show_more_btn.setVisible(True)
        for s in visible:
            self._add_dept_load_row(s)

    def _add_upcoming_due_row(self, item, *, dept_btn_width: int, today: date) -> None:
        row = _ClickableRow(self._next_list_host)
        row.clicked.connect(
            lambda _checked=False, it=item: self.schedule_jump_requested.emit(
                it.entity_kind,
                it.entity_rel,
                it.department,
                it.due.isoformat(),
            )
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(6, 5, 6, 5)
        rl.setSpacing(8)
        entity_block = _DashboardEntityBlock(
            entity_kind=item.entity_kind,
            entity_name=item.entity_name,
            department=item.department,
            dept_label=item.department_label,
            dept_btn_width=dept_btn_width,
            on_entity_click=lambda _=False, it=item: self.dashboard_entity_nav_requested.emit(
                it.entity_kind,
                it.entity_rel,
                it.department or "",
                it.entity_name,
            ),
            parent=row,
        )
        rl.addWidget(entity_block, 0, Qt.AlignVCenter)
        rl.addStretch(1)
        due_text, is_overdue = _relative_due(item.due, today)
        due = QLabel(due_text, row)
        due.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.DemiBold))
        due.setStyleSheet(
            f"color: {_RED if is_overdue else '#a1a1aa'}; background: transparent;"
        )
        rl.addWidget(due, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._next_list.addWidget(row)

    def _update_next_7_days(self, snap: DashboardSnapshot) -> None:
        _clear_layout(self._next_list)
        rows = list(snap.upcoming_due)
        if not rows:
            self._next_list.addWidget(_empty_state("calendar", "Nothing due in the next 7 days"))
            return
        today = date.today()
        visible = rows[:10]
        dept_btn_width = _uniform_dept_btn_width(
            [item.department_label for item in visible if (item.department or "").strip()]
        )
        for item in visible:
            self._add_upcoming_due_row(item, dept_btn_width=dept_btn_width, today=today)

    def _update_attention(self, snap: DashboardSnapshot) -> None:
        _clear_layout(self._attention_list)
        alerts = [
            ("triangle-alert", _RED, "Overdue tasks", snap.overdue_count, "overdue"),
            ("hand", _COLOR_BLOCKED, "Blocked tasks", snap.blocked_count, "blocked"),
            (
                "clock",
                _COLOR_PROGRESS,
                "Unscheduled entities",
                snap.unscheduled_count,
                "unscheduled",
            ),
        ]
        active = [a for a in alerts if a[3] > 0]
        total_attention = sum(a[3] for a in active)
        self._attention_badge.setText(str(total_attention))
        self._attention_badge.setVisible(total_attention > 0)
        if not active:
            self._attention_list.addWidget(self._empty_hint("All clear — nothing needs attention"))
            return
        for icon_name, color, label, count, alert_id in active:
            row = _ClickableRow(self._attention_list_host)
            if alert_id == "unscheduled":
                keys = list(snap.unscheduled_entities)

                def _on_unscheduled(_checked: bool = False, ents: list = keys) -> None:
                    self.unscheduled_entities_requested.emit(ents)

                row.clicked.connect(_on_unscheduled)
            elif alert_id == "overdue":
                keys = list(snap.overdue_entities)

                def _on_overdue(_checked: bool = False, ents: list = keys) -> None:
                    self.overdue_entities_requested.emit(ents)

                row.clicked.connect(_on_overdue)
            else:
                row.clicked.connect(self.open_schedule_requested.emit)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 6, 6, 6)
            rl.setSpacing(10)
            ic = QLabel(row)
            icon = lucide_icon(icon_name, size=16, color_hex=color)
            if not icon.isNull():
                ic.setPixmap(icon.pixmap(16, 16))
            ic.setFixedSize(16, 16)
            rl.addWidget(ic, 0, Qt.AlignVCenter)
            lab = QLabel(label, row)
            lab.setFont(monos_font("Inter", 12, QFont.Weight.DemiBold))
            lab.setStyleSheet("color: #e4e4e7; background: transparent;")
            rl.addWidget(lab, 1)
            rl.addWidget(_chip(str(count), color, row), 0, Qt.AlignVCenter)
            self._attention_list.addWidget(row)

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
                return
            if not notes:
                self._notes_list.addWidget(self._empty_hint("No open notes mention you"))
                return
        else:
            notes = list(snap.open_notes)
            if not notes:
                self._notes_list.addWidget(self._empty_hint("No open notes"))
                return
        visible = notes[:8]
        dept_btn_width = _uniform_dept_btn_width(
            [self._dept_label(n.department) for n in visible if (n.department or "").strip()]
        )
        for note in visible:
            self._add_note_row(note, dept_btn_width=dept_btn_width)

    def _empty_hint(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("DashboardEmptyHint")
        return lab
