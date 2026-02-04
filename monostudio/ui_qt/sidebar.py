from __future__ import annotations

import json
from enum import Enum

from PySide6.QtCore import QRect, QSize, Qt, Signal, QSettings
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, ProjectIndex, Shot
from monostudio.core.pipeline_types_and_presets import load_pipeline_types_and_presets
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font


class SidebarContext(str, Enum):
    DASHBOARD = "Dashboard"
    PROJECTS = "Projects"
    SHOTS = "Shots"
    ASSETS = "Assets"
    LIBRARY = "Library"
    DEPARTMENTS = "Departments"


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


class _SidebarDotItemDelegate(QStyledItemDelegate):
    """
    Sidebar list item delegate:
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarFilterPanel")

        self._settings: QSettings | None = None

        self._active_department: str | None = None
        self._active_type: str | None = None

        self._mode: str = "assets"  # "assets" | "shots" (UI-only context)
        # Default number of items shown per section (user can pick any count).
        self._max_visible = 6
        self._all_departments: list[str] = []
        self._all_types: list[str] = []  # type_ids
        self._type_label_by_id: dict[str, str] = {}
        self._type_icon_by_id: dict[str, str] = {}
        self._dept_label_by_id: dict[str, str] = {}
        self._dept_icon_by_id: dict[str, str] = {}
        # None = not configured yet (will default to first N once). [] is a valid "show none".
        self._visible_departments: list[str] | None = None
        self._visible_types: list[str] | None = None  # type_ids

        # Per-page state (Assets vs Shots). Keep UI selections when switching pages.
        self._state_by_mode: dict[str, dict[str, object]] = {}

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
        self._dept_list.setUniformItemSizes(True)
        self._dept_list.setFocusPolicy(Qt.NoFocus)
        self._dept_list.setIconSize(QSize(16, 16))
        self._dept_list.setItemDelegate(_SidebarDotItemDelegate(self._dept_list))
        self._dept_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dept_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dept_list.itemClicked.connect(self._on_department_clicked)

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
        self._type_list.setUniformItemSizes(True)
        self._type_list.setFocusPolicy(Qt.NoFocus)
        self._type_list.setIconSize(QSize(16, 16))
        self._type_list.setItemDelegate(_SidebarDotItemDelegate(self._type_list))
        self._type_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._type_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._type_list.itemClicked.connect(self._on_type_clicked)

        root.addWidget(dept_header_row, 0)
        root.addWidget(self._dept_list, 0)
        root.addWidget(type_header_row, 0)
        root.addWidget(self._type_list, 0)
        root.addStretch(1)

        # Load from pipeline metadata (single source of truth), scoped by current mode.
        self.reload_from_pipeline_metadata()

    def reload_from_pipeline_metadata(self) -> None:
        """
        UI-only: load departments/types from pipeline metadata JSON for current mode.
        """
        meta = load_pipeline_types_and_presets()

        # Types: stable ids + display names.
        types_out: list[tuple[str, str]] = []
        type_icons: dict[str, str] = {}
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
        types_out.sort(key=lambda x: x[1].lower())
        self._all_types = [tid for tid, _ in types_out]
        self._type_label_by_id = {tid: name for tid, name in types_out}
        self._type_icon_by_id = type_icons

        # Departments: union across all types, ordered by departments definition in JSON.
        seen: set[str] = set()
        depts: list[str] = []
        dept_labels: dict[str, str] = {}
        dept_icons: dict[str, str] = {}
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

        # If current selections are no longer valid in this mode, clear locally.
        # (No intent signals here; signals are reserved for user clicks.)
        if self._active_type is not None and self._active_type not in set(self._all_types):
            self._active_type = None
        if self._active_department is not None and self._active_department not in set(self._all_departments):
            self._active_department = None

        self.set_departments(self._all_departments)
        self.set_types(self._all_types)

    def current_department(self) -> str | None:
        return self._active_department

    def current_type(self) -> str | None:
        return self._active_type

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
        # Apply stored state for current mode (if any) and refresh lists.
        self._apply_state(self._state_by_mode.get(self._mode))
        self.reload_from_pipeline_metadata()

    def _settings_key(self, mode: str, field: str) -> str:
        return f"sidebar/filters/{mode}/{field}"

    def _load_state_for_mode(self, mode: str) -> None:
        if self._settings is None:
            return
        if mode not in ("assets", "shots"):
            return

        dep = self._settings.value(self._settings_key(mode, "active_department"), "", str)
        typ = self._settings.value(self._settings_key(mode, "active_type"), "", str)
        vd_raw = self._settings.value(self._settings_key(mode, "visible_departments"), "", str)
        vt_raw = self._settings.value(self._settings_key(mode, "visible_types"), "", str)

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

        state: dict[str, object] = {
            "active_department": dep.strip() if dep and dep.strip() else None,
            "active_type": typ.strip() if typ and typ.strip() else None,
            "visible_departments": load_list(vd_raw),
            "visible_types": load_list(vt_raw),
        }
        self._state_by_mode[mode] = state

    def _save_state_for_mode(self, mode: str) -> None:
        if self._settings is None:
            return
        if mode not in ("assets", "shots"):
            return
        state = self._state_by_mode.get(mode)
        if not state:
            state = self._snapshot_state()
            self._state_by_mode[mode] = state
        self._settings.setValue(self._settings_key(mode, "active_department"), state.get("active_department") or "")
        self._settings.setValue(self._settings_key(mode, "active_type"), state.get("active_type") or "")
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
        UI-only: switch between assets/shots modes.
        This controls which types/departments are listed from pipeline metadata.
        """
        m = (mode or "").strip().lower()
        if m not in ("assets", "shots"):
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
            "visible_departments": list(self._visible_departments) if self._visible_departments is not None else None,
            "visible_types": list(self._visible_types) if self._visible_types is not None else None,
        }

    def _apply_state(self, state: dict[str, object] | None) -> None:
        if not state:
            self._active_department = None
            self._active_type = None
            self._visible_departments = None
            self._visible_types = None
            return
        self._active_department = state.get("active_department") if isinstance(state.get("active_department"), str) else None
        self._active_type = state.get("active_type") if isinstance(state.get("active_type"), str) else None

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

        self._dept_list.blockSignals(True)
        try:
            self._dept_list.clear()
            for v in (self._visible_departments or []):
                label = self._dept_label_by_id.get(v, v)
                it = QListWidgetItem(_title_case_label(label))
                it.setData(Qt.UserRole, v)
                icon_name = self._dept_icon_by_id.get(v)
                if icon_name:
                    it.setIcon(_lucide_two_state_icon(icon_name, fallback_name="layers"))
                self._dept_list.addItem(it)
            self._sync_selection()
            self._fit_list_height(self._dept_list)
        finally:
            self._dept_list.blockSignals(False)

    def set_types(self, values: list[str]) -> None:
        cleaned = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        self._all_types = cleaned
        if self._visible_types is None:
            self._visible_types = cleaned[: self._max_visible]
        else:
            self._visible_types = [v for v in self._visible_types if v in cleaned]

        self._type_list.blockSignals(True)
        try:
            self._type_list.clear()
            for v in (self._visible_types or []):
                label = self._type_label_by_id.get(v, v)
                it = QListWidgetItem(_title_case_label(label))
                it.setData(Qt.UserRole, v)  # stable id
                icon_name = self._type_icon_by_id.get(v)
                if icon_name:
                    it.setIcon(_lucide_two_state_icon(icon_name, fallback_name="folder"))
                self._type_list.addItem(it)
            self._sync_selection()
            self._fit_list_height(self._type_list)
        finally:
            self._type_list.blockSignals(False)

    def _sync_selection(self) -> None:
        self._dept_list.blockSignals(True)
        self._type_list.blockSignals(True)
        try:
            self._dept_list.clearSelection()
            self._type_list.clearSelection()

            if self._active_department is not None:
                for i in range(self._dept_list.count()):
                    it = self._dept_list.item(i)
                    if it is not None and it.data(Qt.UserRole) == self._active_department:
                        it.setSelected(True)
                        self._dept_list.setCurrentItem(it)
                        break

            if self._active_type is not None:
                for i in range(self._type_list.count()):
                    it = self._type_list.item(i)
                    if it is not None and it.data(Qt.UserRole) == self._active_type:
                        it.setSelected(True)
                        self._type_list.setCurrentItem(it)
                        break
        finally:
            self._dept_list.blockSignals(False)
            self._type_list.blockSignals(False)

    def _on_department_clicked(self, item: QListWidgetItem) -> None:
        v = item.data(Qt.UserRole)
        clicked = v if isinstance(v, str) else None
        if clicked is not None and clicked == self._active_department:
            self._active_department = None
            self._sync_selection()
            self.departmentClicked.emit(None)
            self._state_by_mode[self._mode] = self._snapshot_state()
            self._save_state_for_mode(self._mode)
            return
        self._active_department = clicked
        self._sync_selection()
        self.departmentClicked.emit(clicked)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def _on_type_clicked(self, item: QListWidgetItem) -> None:
        v = item.data(Qt.UserRole)
        clicked = v if isinstance(v, str) else None
        if clicked is not None and clicked == self._active_type:
            self._active_type = None
            self._sync_selection()
            self.typeClicked.emit(None)
            self._state_by_mode[self._mode] = self._snapshot_state()
            self._save_state_for_mode(self._mode)
            return
        self._active_type = clicked
        self._sync_selection()
        self.typeClicked.emit(clicked)
        self._state_by_mode[self._mode] = self._snapshot_state()
        self._save_state_for_mode(self._mode)

    def _open_department_picker(self) -> None:
        dlg = _FilterPickDialog(
            title="Select Departments",
            items=[(d, _title_case_label(self._dept_label_by_id.get(d, d)), self._dept_icon_by_id.get(d)) for d in self._all_departments],
            selected=set(self._visible_departments or []),
            max_selected=None,
            parent=self,
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
        dlg = _FilterPickDialog(
            title="Select Types",
            items=[
                (tid, _title_case_label(self._type_label_by_id.get(tid, tid)), self._type_icon_by_id.get(tid))
                for tid in self._all_types
            ],
            selected=set(self._visible_types or []),
            max_selected=None,
            parent=self,
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
        """
        rows = int(w.count())
        if rows <= 0:
            w.setFixedHeight(0)
            return

        row_h = max(1, int(w.sizeHintForRow(0)))
        # QSS adds padding; approximate to keep all rows visible.
        extra = 2 * int(w.frameWidth()) + 12 + 4
        w.setFixedHeight(rows * row_h + extra)


class _FilterPickDialog(MonosDialog):
    """
    Picker dialog for Departments / Types.
    Selectable list (multi-select), same style as department preset in Settings.
    """

    def __init__(
        self,
        *,
        title: str,
        items: list[tuple[str, str, str | None]],  # (id, label, icon_name)
        selected: set[str],
        max_selected: int | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("SidebarFilterPickDialog")
        # Force QSS background to paint (otherwise dialog can appear black on Windows).
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._items: list[tuple[str, str, str | None]] = [
            (i, lbl, (ic.strip() if isinstance(ic, str) and ic.strip() else None))
            for (i, lbl, ic) in items
            if isinstance(i, str) and i.strip() and isinstance(lbl, str) and lbl.strip()
        ]
        _selected = set(selected)

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
        root.addWidget(self._list, 1)
        root.addWidget(btn_row, 0)

        self._sync_hint()

    def selected_items(self) -> list[str]:
        """Return selected item ids in list order."""
        out: list[str] = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.isSelected():
                v = it.data(Qt.UserRole)
                if isinstance(v, str) and v:
                    out.append(v)
        return out

    def _sync_hint(self) -> None:
        n = len(self.selected_items())
        self._hint.setText(f"Selected {n}")


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
    settings_requested = Signal()

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

        # --- Block 1: Brand + Primary Nav (top fixed)
        top = QWidget(self)
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(16, 16, 16, 16)  # p-4
        top_layout.setSpacing(12)

        brand = QWidget(top)
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)  # icon-to-text ~12px

        icon_box = QLabel("M", brand)
        icon_box.setObjectName("SidebarBrandIcon")
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setFixedSize(28, 28)

        brand_label = QLabel("MONOS", brand)
        brand_label.setObjectName("SidebarBrandLabel")
        f_brand = monos_font("Inter", 16, QFont.Weight.ExtraBold)  # 800
        f_brand.setLetterSpacing(QFont.PercentageSpacing, 97)  # tracking-tight
        brand_label.setFont(f_brand)

        brand_layout.addWidget(icon_box)
        brand_layout.addWidget(brand_label, 1)

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

        # Primary nav items (Lucide, order locked by spec)
        self._add_nav_item(SidebarContext.DASHBOARD.value, "layout-dashboard")
        self._add_nav_item(SidebarContext.PROJECTS.value, "folder-kanban")
        self._add_nav_item(SidebarContext.SHOTS.value, "clapperboard")
        self._add_nav_item(SidebarContext.ASSETS.value, "box")
        self._add_nav_item(SidebarContext.LIBRARY.value, "library")
        self._add_nav_item(SidebarContext.DEPARTMENTS.value, "layers")

        top_layout.addWidget(brand, 0)
        top_layout.addWidget(self._nav, 0)

        # --- Block 2+3: Scrollable center (Hierarchy + Recent Tasks)
        scroll = QScrollArea(self)
        scroll.setObjectName("SidebarScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_inner = QWidget(scroll)
        scroll_layout = QVBoxLayout(scroll_inner)
        scroll_layout.setContentsMargins(16, 0, 16, 16)  # side padding only
        scroll_layout.setSpacing(24)  # mt-6 between sections

        f_h = monos_font("Inter", 10, QFont.Weight.ExtraBold)  # 800
        f_h.setLetterSpacing(QFont.PercentageSpacing, 112)  # tracking-widest-ish

        # Section: FILTERS (metadata-driven; mock data for now)
        self._filters = SidebarWidget(scroll_inner)

        # Section: RECENT TASKS (placeholder UI only)
        tasks_block = QWidget(scroll_inner)
        tasks_layout = QVBoxLayout(tasks_block)
        tasks_layout.setContentsMargins(0, 0, 0, 0)
        tasks_layout.setSpacing(8)

        tasks_header = QLabel("RECENT TASKS", tasks_block)
        tasks_header.setObjectName("SidebarSectionHeader")
        tasks_header.setFont(f_h)

        tasks_empty = QLabel("No tasks", tasks_block)
        tasks_empty.setObjectName("SidebarMutedText")

        tasks_layout.addWidget(tasks_header, 0)
        tasks_layout.addWidget(tasks_empty, 0)
        tasks_layout.addStretch(1)

        scroll_layout.addWidget(self._filters, 1)
        scroll_layout.addWidget(tasks_block, 0)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_inner)

        # --- Block 4: Global Settings (bottom fixed)
        bottom = QWidget(self)
        bottom.setObjectName("SidebarBottom")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 12, 16, 16)
        bottom_layout.setSpacing(8)

        self._settings_btn = QPushButton("Global Settings", bottom)
        self._settings_btn.setObjectName("SidebarSettingsButton")
        self._settings_btn.setCursor(Qt.PointingHandCursor)
        _settings_icon = lucide_icon("sliders-horizontal", size=16, color_hex=MONOS_COLORS["text_label"])
        if not _settings_icon.isNull():
            self._settings_btn.setIcon(_settings_icon)
            self._settings_btn.setIconSize(QSize(16, 16))
        self._settings_btn.clicked.connect(self.settings_requested.emit)

        bottom_layout.addWidget(self._settings_btn, 0)

        root.addWidget(top, 0)
        root.addWidget(scroll, 1)
        root.addWidget(bottom, 0)

        # Default context: Assets (keeps existing workflow stable)
        self.set_current_context(SidebarContext.ASSETS.value)

        # Start with empty hierarchy until MainWindow provides an index.
        self.set_project_index(None)

    def filters(self) -> SidebarWidget:
        return self._filters

    def _add_nav_item(self, label: str, icon_name: str) -> None:
        it = QListWidgetItem("")
        it.setData(Qt.UserRole, label)
        it.setSizeHint(QSize(0, 36))  # keep height locked
        self._nav.addItem(it)

        w = _SidebarNavItemWidget(label, icon_name, parent=self._nav)
        self._nav.setItemWidget(it, w)
        self._nav_widgets[label] = w

    def current_context(self) -> str:
        item = self._nav.currentItem()
        if item is None:
            return SidebarContext.ASSETS.value
        v = item.data(Qt.UserRole)
        return str(v) if isinstance(v, str) and v else SidebarContext.ASSETS.value

    def set_current_context(self, context_name: str) -> None:
        for i in range(self._nav.count()):
            it = self._nav.item(i)
            if it is not None and it.data(Qt.UserRole) == context_name:
                if self._nav.currentRow() != i:
                    self._nav.setCurrentRow(i)
                return

    def set_projects_count(self, value: int | None) -> None:
        # Workspace discovery can feed this (no project scans).
        self._projects_count = value
        self._sync_nav_badges()

    def set_project_index(self, project_index: ProjectIndex | None) -> None:
        """
        UI-only:
        - Keep nav badges (Assets/Shots counts) in sync from already-loaded memory.
        - No hierarchy tree (replaced by metadata-driven filter panel).
        """
        self._project_index = project_index
        self._sync_nav_badges()

    def _on_current_nav_item_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        context = current.data(Qt.UserRole)
        if not isinstance(context, str) or not context:
            return

        self._last_context_text = context
        self._sync_nav_active_states()
        # Filter panel departments/types depend on current browser page.
        if context == SidebarContext.SHOTS.value:
            self._filters.set_mode("shots")
        elif context == SidebarContext.ASSETS.value:
            self._filters.set_mode("assets")
        self.context_changed.emit(context)

    def _on_nav_item_clicked(self, item: QListWidgetItem) -> None:
        # Click reloads current view (only when clicking already-selected item).
        context = item.data(Qt.UserRole)
        if not isinstance(context, str) or not context:
            return
        if context == self._last_context_text:
            self.context_clicked.emit(context)

    def _on_nav_context_menu_requested(self, pos) -> None:
        item = self._nav.itemAt(pos)
        if item is None:
            return
        context = item.data(Qt.UserRole)
        if not isinstance(context, str) or not context:
            return
        self.context_menu_requested.emit(context, self._nav.viewport().mapToGlobal(pos))

    def _sync_nav_active_states(self) -> None:
        current = self.current_context()
        for name, w in self._nav_widgets.items():
            w.set_active(name == current)

    def _sync_nav_badges(self) -> None:
        # Counts are UI-only, derived from already-loaded memory.
        assets_count = len(self._project_index.assets) if self._project_index is not None else None
        shots_count = len(self._project_index.shots) if self._project_index is not None else None

        for name, w in self._nav_widgets.items():
            if name == SidebarContext.ASSETS.value:
                w.set_count_badge(assets_count)
            elif name == SidebarContext.SHOTS.value:
                w.set_count_badge(shots_count)
            elif name == SidebarContext.PROJECTS.value:
                w.set_count_badge(self._projects_count)
            else:
                w.set_count_badge(None)

