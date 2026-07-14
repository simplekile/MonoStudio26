"""Tray mini panel: list mode pills, dept/type filters, list view with thumbs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, Shot
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.nav_pill_widgets import IconPillWidget
from monostudio.ui_qt.notification.mention_alert_format import (
    _fields_from_entry,
    mention_alert_plain_message,
)
from monostudio.ui_qt.notification.notification_row_widget import (
    _sender_visual,
    display_time_for_entry,
    format_notification_time,
)
from monostudio.ui_qt.notification.store import NotificationEntry, all_entries
from monostudio.ui_qt.recent_tasks_store import RecentTask
from monostudio.ui_qt.main_view import (
    _list_dcc_badge_info,
    _resolve_publish_root_folder_any,
    _resolve_work_root_folder_any,
    _resolved_work_path_for_copy,
)
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu, monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio

if TYPE_CHECKING:
    from monostudio.ui_qt.main_window import MainWindow
    from monostudio.ui_qt.thumbnails import ThumbnailManager

ListMode = Literal["recent", "assets", "shots", "noti"]

_POPUP_W = 320
_MAX_ROWS = 10
_LIST_VISIBLE_ROWS = 4
_THUMB_W = 72
_THUMB_H = max(1, int(round(_THUMB_W * 9 / 16)))
_ROW_H = _THUMB_H + 18
_NOTI_ROW_H = 48
_NOTI_AVATAR_PX = 36
_HEADER_H = 28
_PILL_ROW_H = 44
_FILTER_ROW_H = 36
_FOOTER_H = 36
_LIST_AREA_H = _LIST_VISIBLE_ROWS * _ROW_H + 8
_POPUP_FIXED_H = _HEADER_H + _PILL_ROW_H + _FILTER_ROW_H + _LIST_AREA_H + _FOOTER_H
_PILL_ICON_SIZE = 18
_PILL_SEGMENT_W = 40

_CLR_ACTIVE = MONOS_COLORS.get("blue_400", "#60a5fa")
_CLR_LABEL = MONOS_COLORS.get("text_label", "#a1a1aa")
_CLR_MUTED = MONOS_COLORS.get("text_meta", "#71717a")

_TRAY_DCC_BADGE_SIZE = 12
_TRAY_DCC_BADGE_PAD = 2
_TRAY_DCC_BADGE_GAP = 2
_TRAY_DCC_BADGE_MAX = 3

def _scale_thumb_pixmap_16_9(pix: QPixmap, box_w: int, box_h: int) -> QPixmap:
    """Fill a 16:9 box like main list/grid: scale to height, center-crop width when needed."""
    if pix.isNull() or pix.width() <= 0 or pix.height() <= 0:
        return pix
    scale = box_h / pix.height()
    scaled_w = max(1, int(pix.width() * scale))
    scaled = pix.scaled(
        scaled_w,
        box_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if scaled_w >= box_w:
        x = (scaled_w - box_w) // 2
        return scaled.copy(x, 0, box_w, box_h)
    scale_w = box_w / pix.width()
    scaled_h = max(1, int(pix.height() * scale_w))
    scaled2 = pix.scaled(
        box_w,
        scaled_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if scaled_h >= box_h:
        y = (scaled_h - box_h) // 2
        return scaled2.copy(0, y, box_w, box_h)
    out = QPixmap(box_w, box_h)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        painter.drawPixmap((box_w - scaled2.width()) // 2, (box_h - scaled2.height()) // 2, scaled2)
    finally:
        painter.end()
    return out


def _view_item_for_tray_entry(ent: _TrayListEntry) -> ViewItem | None:
    ref = ent.pipeline_ref
    if not isinstance(ref, (Asset, Shot)) or ent.path is None:
        return None
    kind = ViewItemKind.ASSET if isinstance(ref, Asset) else ViewItemKind.SHOT
    type_badge = getattr(ref, "asset_type", "") if isinstance(ref, Asset) else "shot"
    return ViewItem(
        kind=kind,
        name=ref.name,
        type_badge=type_badge,
        path=ent.path,
        ref=ref,
        type_folder=type_badge if isinstance(ref, Asset) else "",
    )


def _tray_dcc_badge_info(
    ent: _TrayListEntry,
    window: MainWindow | None,
    *,
    department: str | None = None,
) -> tuple[list[tuple[QIcon | None, str, str]], str | None]:
    """DCC badges for tray thumb (same source as grid card). Returns (badges, active_dcc_id)."""
    dept = (department or ent.department or "").strip()
    if not dept or window is None:
        return [], None
    vi = _view_item_for_tray_entry(ent)
    if vi is None:
        return [], None
    dept_reg = getattr(window, "_dept_registry", None)
    badges = _list_dcc_badge_info(vi, dept, dept_registry=dept_reg)
    active = window._main_view.get_active_dcc(ent.path, dept)
    existing = {(dcc_id or "").strip() for _ic, dcc_id, st in badges if st == "exists"}
    if active and active not in existing:
        active = None
    if not active:
        for _ic, dcc_id, st in badges:
            if st == "exists" and (dcc_id or "").strip():
                active = (dcc_id or "").strip()
                break
    return badges, active


class _TrayThumbFrame(QFrame):
    """16:9 thumb with DCC badges (bottom-right), matching grid card layout."""

    dcc_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrayMiniThumb")
        self.setFixedSize(_THUMB_W, _THUMB_H)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._pixmap: QPixmap | None = None
        self._badges: list[tuple[QIcon | None, str, str]] = []
        self._active_dcc: str | None = None
        self._badge_hit_rects: list[tuple[QRect, str]] = []

    def set_thumb_pixmap(self, pix: QPixmap | None) -> None:
        self._pixmap = pix
        self.update()

    def set_dcc_badges(
        self,
        badges: list[tuple[QIcon | None, str, str]],
        *,
        active_dcc: str | None,
    ) -> None:
        self._badges = badges
        self._active_dcc = (active_dcc or "").strip() or None
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            thumb = QRect(0, 0, _THUMB_W, _THUMB_H)
            p.fillRect(thumb, QColor("#27272a"))
            if self._pixmap is not None and not self._pixmap.isNull():
                p.drawPixmap(thumb, self._pixmap)
            self._badge_hit_rects = []
            if not self._badges:
                return
            size = _TRAY_DCC_BADGE_SIZE
            pad = _TRAY_DCC_BADGE_PAD
            gap = _TRAY_DCC_BADGE_GAP
            chip_h = size + pad * 2
            chip_r = chip_h // 2
            creating_w = 44
            shown = self._badges[:_TRAY_DCC_BADGE_MAX]
            widths = [creating_w if st == "creating" else chip_h for _ic, _did, st in shown]
            row_w = sum(widths) + max(0, len(widths) - 1) * gap
            base_x = thumb.right() - 4 - row_w
            base_y = thumb.bottom() - 4 - chip_h
            dcc_bg = QColor(0, 0, 0, 160)
            pen_active = QPen(QColor(_CLR_ACTIVE), 2)
            creating_font = monos_font("Inter", 8, QFont.Weight.Medium)
            x_cursor = base_x
            for i, (dcc_icon, dcc_id, badge_status) in enumerate(shown):
                w = widths[i]
                bg_rect = QRect(x_cursor, base_y, w, chip_h)
                did = (dcc_id or "").strip()
                is_active = bool(self._active_dcc and did == self._active_dcc)
                if did and badge_status != "creating":
                    self._badge_hit_rects.append((bg_rect, did))
                if badge_status == "creating":
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(dcc_bg)
                    p.drawRoundedRect(bg_rect, chip_r, chip_r)
                    if is_active:
                        p.setPen(pen_active)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRoundedRect(bg_rect, chip_r, chip_r)
                    p.setFont(creating_font)
                    p.setPen(QColor(255, 255, 255))
                    p.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, "…")
                else:
                    cx = x_cursor + chip_r
                    cy = base_y + chip_r
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(dcc_bg)
                    p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                    if is_active:
                        p.setPen(pen_active)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                    if dcc_icon is not None and not dcc_icon.isNull():
                        pix = dcc_icon.pixmap(size, size)
                        if not pix.isNull():
                            p.drawPixmap(x_cursor + pad, base_y + pad, pix)
                x_cursor += w + gap
        finally:
            p.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            for rect, dcc_id in self._badge_hit_rects:
                if rect.contains(pos):
                    self.dcc_clicked.emit(dcc_id)
                    event.accept()
                    return
        super().mousePressEvent(event)


_LIST_PILL_SEGMENTS = (
    ("recent", "timer", "Recent tasks"),
    ("assets", "box", "Assets"),
    ("shots", "clapperboard", "Shots"),
    ("noti", "bell", "Notifications"),
)


@dataclass(frozen=True)
class _TrayListEntry:
    kind: str  # recent | asset | shot
    title: str
    subtitle: str
    icon: object
    path: Path | None = None
    task: RecentTask | None = None
    department: str | None = None
    type_id: str | None = None
    pipeline_ref: Asset | Shot | None = None


@dataclass(frozen=True)
class _TrayNotiEntry:
    entry: NotificationEntry


def _plain_message_for_entry(entry: NotificationEntry) -> str:
    fields = _fields_from_entry(entry)
    if fields is not None:
        from_name, item_display, dept_id, dept_label = fields
        return mention_alert_plain_message(
            from_name=from_name,
            item_display=item_display,
            department_id=dept_id,
            department_label=dept_label,
        )
    return (entry.message or "").strip() or "Notification"


class _TrayNotiListRow(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrayMiniNotiRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        self._avatar = QLabel(self)
        self._avatar.setObjectName("TrayMiniNotiAvatar")
        self._avatar.setFixedSize(_NOTI_AVATAR_PX, _NOTI_AVATAR_PX)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self._title = QLabel(self)
        self._title.setObjectName("TrayMiniRowTitle")
        self._title.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        self._sub = QLabel(self)
        self._sub.setObjectName("TrayMiniRowSub")
        self._sub.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        text_col.addWidget(self._title)
        text_col.addWidget(self._sub)
        lay.addWidget(self._avatar, 0)
        lay.addLayout(text_col, 1)

    def set_content(
        self,
        *,
        entry: NotificationEntry,
        workspace_root: Path | None,
        project_root: Path | None,
    ) -> None:
        unread = not entry.read
        self.setProperty("unread", "true" if unread else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        msg = _plain_message_for_entry(entry)
        self._title.setText(msg)
        self._title.setStyleSheet(
            f"color: {'#fafafa' if unread else '#d4d4d8'}; background: transparent; border: none;"
        )
        when = format_notification_time(display_time_for_entry(entry, project_root))
        self._sub.setText(when)
        time_color = _CLR_ACTIVE if unread else _CLR_MUTED
        self._sub.setStyleSheet(f"color: {time_color}; background: transparent; border: none;")
        img, initials, color_hex, _badge = _sender_visual(entry, workspace_root, project_root)
        dpr = effective_device_pixel_ratio(self)
        self._avatar.setPixmap(
            avatar_pixmap_for(img, initials, color_hex, _NOTI_AVATAR_PX, dpr=dpr)
        )


class _TrayMiniListRow(QWidget):
    dcc_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TrayMiniListRow")
        self.setMinimumHeight(_ROW_H)
        self.setMaximumHeight(_ROW_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        self._thumb = _TrayThumbFrame(self)
        self._thumb.dcc_clicked.connect(self.dcc_clicked.emit)
        self._type_icon = QLabel(self)
        self._type_icon.setFixedSize(16, 16)
        self._type_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        self._title = QLabel(self)
        self._title.setObjectName("TrayMiniRowTitle")
        self._title.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
        self._sub = QLabel(self)
        self._sub.setObjectName("TrayMiniRowSub")
        self._sub.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        text_col.addWidget(self._title)
        text_col.addWidget(self._sub)
        lay.addWidget(self._thumb, 0)
        lay.addWidget(self._type_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(text_col, 1)

    def set_content(
        self,
        *,
        title: str,
        subtitle: str,
        row_icon,
        thumb_pixmap=None,
        dcc_badges: list[tuple[QIcon | None, str, str]] | None = None,
        active_dcc: str | None = None,
    ) -> None:
        self._title.setText(title)
        self._sub.setText(subtitle)
        self._sub.setVisible(bool(subtitle))
        if row_icon is not None and not row_icon.isNull():
            self._type_icon.setPixmap(row_icon.pixmap(16, 16))
            self._type_icon.setVisible(True)
        else:
            self._type_icon.clear()
            self._type_icon.setVisible(False)
        if thumb_pixmap is not None and not thumb_pixmap.isNull():
            thumb_px = _scale_thumb_pixmap_16_9(thumb_pixmap, _THUMB_W, _THUMB_H)
        else:
            thumb_px = lucide_icon("image", size=_THUMB_W, color_hex=_CLR_MUTED).pixmap(
                _THUMB_W, _THUMB_H
            )
        self._thumb.set_thumb_pixmap(thumb_px)
        self._thumb.set_dcc_badges(dcc_badges or [], active_dcc=active_dcc)


class TrayMiniPopup(QFrame):
    task_selected = Signal(object)
    task_open_file_requested = Signal(object)
    entity_selected = Signal(str, object, object, object)  # kind, path, dept, type_id
    entity_open_file_requested = Signal(str, object, object, object)
    notification_selected = Signal(object)
    open_monos_requested = Signal()
    open_notifications_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("TrayMiniPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(_POPUP_W, _POPUP_FIXED_H)

        self._list_mode: ListMode = "recent"
        self._dept_id: str | None = None
        self._type_id: str | None = None
        self._dept_options: list[tuple[str, str, str | None]] = []
        self._type_options: list[tuple[str, str, str | None]] = []
        self._thumb_mgr: ThumbnailManager | None = None
        self._window: MainWindow | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = QLabel("MONOS", self)
        self._header.setObjectName("TrayMiniPopupHeader")
        self._header.setFixedHeight(_HEADER_H)
        self._header.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        root.addWidget(self._header)

        self._list_pill = IconPillWidget(
            _LIST_PILL_SEGMENTS,
            icon_size=_PILL_ICON_SIZE,
            segment_width=_PILL_SEGMENT_W,
            parent=self,
        )
        self._list_pill.setFixedHeight(_PILL_ROW_H)
        self._list_pill.setMinimumWidth(
            len(_LIST_PILL_SEGMENTS) * _PILL_SEGMENT_W + (len(_LIST_PILL_SEGMENTS) - 1) * 2 + 12
        )
        self._list_pill.segment_clicked.connect(self._on_list_mode_clicked)
        root.addWidget(self._list_pill, 0, Qt.AlignmentFlag.AlignHCenter)

        self._filter_row = QWidget(self)
        fr_l = QHBoxLayout(self._filter_row)
        fr_l.setContentsMargins(8, 4, 8, 4)
        fr_l.setSpacing(6)
        self._btn_dept = self._make_filter_pill(self._filter_row, "Dept")
        self._btn_type = self._make_filter_pill(self._filter_row, "Type")
        self._btn_dept.clicked.connect(self._open_dept_menu)
        self._btn_type.clicked.connect(self._open_type_menu)
        fr_l.addWidget(self._btn_dept, 1)
        fr_l.addWidget(self._btn_type, 1)

        self._filter_stack = QStackedWidget(self)
        self._filter_stack.setFixedHeight(_FILTER_ROW_H)
        self._filter_stack.addWidget(self._filter_row)
        self._filter_stack.addWidget(QWidget(self))
        root.addWidget(self._filter_stack)

        self._body_stack = QStackedWidget(self)
        self._body_stack.setFixedHeight(_LIST_AREA_H)

        self._list = QListWidget(self)
        self._list.setObjectName("TrayMiniPopupList")
        self._list.setFixedHeight(_LIST_AREA_H)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.customContextMenuRequested.connect(self._on_list_context_menu)

        self._empty_panel = QWidget(self)
        empty_lay = QVBoxLayout(self._empty_panel)
        empty_lay.setContentsMargins(12, 0, 12, 0)
        self._empty = QLabel("Nothing to show", self._empty_panel)
        self._empty.setObjectName("TrayMiniPopupEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        empty_lay.addStretch(1)
        empty_lay.addWidget(self._empty)
        empty_lay.addStretch(1)

        self._body_stack.addWidget(self._list)
        self._body_stack.addWidget(self._empty_panel)
        root.addWidget(self._body_stack)

        self._open_btn = QPushButton("Open MONOS", self)
        self._open_btn.setObjectName("TrayMiniPopupOpenButton")
        self._open_btn.setFixedHeight(_FOOTER_H)
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(self._on_open_monos)
        root.addWidget(self._open_btn)

        self._list_pill.set_active_segment("recent")
        self._update_mode_ui()

    @staticmethod
    def _make_filter_pill(parent: QWidget, label: str) -> QPushButton:
        btn = QPushButton(label, parent)
        btn.setObjectName("TrayMiniFilterPill")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFlat(True)
        return btn

    def reload_from_window(self, window: MainWindow) -> None:
        self._window = window
        self._thumb_mgr = getattr(window, "_thumbnail_manager", None)
        pr = getattr(window, "_project_root", None)
        title = f"MONOS — {pr.name}" if pr is not None else "MONOS"
        self._header.setText(title.upper())

        if self._list_mode in ("assets", "shots"):
            filters = window._filter_panel.filters()
            if self._list_mode == "assets":
                filters.set_mode("assets")
            else:
                filters.set_mode("shots")
            self._dept_options, self._type_options = filters.filter_option_lists()
            self._dept_id = filters.current_department()
            self._type_id = filters.current_type()
            self._update_filter_labels()
        self._update_mode_ui()
        self._rebuild_list()

    def _on_list_mode_clicked(self, mode_key: str) -> None:
        key = (mode_key or "").strip().lower()
        if key not in ("recent", "assets", "shots", "noti"):
            return
        self._list_mode = key  # type: ignore[assignment]
        self._list_pill.set_active_segment(key)
        self._update_mode_ui()
        if self._window is not None:
            if key != "noti":
                self.reload_from_window(self._window)
            else:
                self._rebuild_list()

    @staticmethod
    def _mode_shows_filters(mode: ListMode) -> bool:
        return mode in ("assets", "shots")

    def _apply_list_area_height(self) -> None:
        """When filters hidden, reclaim filter band height for the list (no empty gap)."""
        shows_filters = self._mode_shows_filters(self._list_mode)
        filter_h = _FILTER_ROW_H if shows_filters else 0
        list_h = _LIST_AREA_H + (0 if shows_filters else _FILTER_ROW_H)
        self._filter_stack.setFixedHeight(filter_h)
        self._filter_stack.setVisible(shows_filters)
        self._body_stack.setFixedHeight(list_h)
        self._list.setFixedHeight(list_h)

    def _update_mode_ui(self) -> None:
        is_noti = self._list_mode == "noti"
        self._filter_stack.setCurrentIndex(0 if self._mode_shows_filters(self._list_mode) else 1)
        self._apply_list_area_height()
        if is_noti:
            self._open_btn.setText("All notifications")
            self._empty.setText("No notifications")
        else:
            self._open_btn.setText("Open MONOS")
            self._empty.setText("Nothing to show")

    def _update_filter_labels(self) -> None:
        dept_label = "Dept · All"
        for did, label, _ic in self._dept_options:
            if did == self._dept_id:
                dept_label = f"Dept · {label}"
                break
        type_label = "Type · All"
        for tid, label, _ic in self._type_options:
            if tid == self._type_id:
                type_label = f"Type · {label}"
                break
        self._btn_dept.setText(dept_label)
        self._btn_type.setText(type_label)

    def _open_dept_menu(self) -> None:
        menu = MonosMenu(self)
        all_act = menu.addAction("All departments")
        all_act.triggered.connect(lambda: self._set_dept(None))
        if self._dept_options:
            menu.addSeparator()
        for did, label, icon_name in self._dept_options[:16]:
            ic = lucide_icon(icon_name or "layers", size=16, color_hex=_CLR_LABEL) if icon_name else None
            act = menu.addAction(ic, label) if ic and not ic.isNull() else menu.addAction(label)
            act.triggered.connect(lambda _c=False, d=did: self._set_dept(d))
        menu.popup(self._btn_dept.mapToGlobal(self._btn_dept.rect().bottomLeft()))

    def _open_type_menu(self) -> None:
        menu = MonosMenu(self)
        all_act = menu.addAction("All types")
        all_act.triggered.connect(lambda: self._set_type(None))
        if self._type_options:
            menu.addSeparator()
        for tid, label, icon_name in self._type_options[:16]:
            ic = lucide_icon(icon_name or "folder", size=16, color_hex=_CLR_LABEL) if icon_name else None
            act = menu.addAction(ic, label) if ic and not ic.isNull() else menu.addAction(label)
            act.triggered.connect(lambda _c=False, t=tid: self._set_type(t))
        menu.popup(self._btn_type.mapToGlobal(self._btn_type.rect().bottomLeft()))

    def _sidebar_filters(self):
        if self._window is None:
            return None
        return self._window._filter_panel.filters()

    def _apply_tray_active_dcc(self, ent: _TrayListEntry, dcc_id: str) -> None:
        """Sync active DCC with grid card badge click + Inspector."""
        win = self._window
        if win is None or ent.path is None:
            return
        dept = (ent.department or "").strip()
        did = (dcc_id or "").strip()
        if not dept or not did:
            return
        mv = win._main_view
        mv.set_active_dcc(ent.path, dept, did)
        mv.active_dcc_changed.emit(ent.path, dept, did)
        try:
            mv.invalidate_thumbnail(ent.path, dept)
        except Exception:
            pass
        if self._list_mode != "noti":
            self._rebuild_list()

    def _sync_sidebar_mode(self) -> None:
        filters = self._sidebar_filters()
        if filters is None or not self._mode_shows_filters(self._list_mode):
            return
        if self._list_mode == "assets":
            filters.set_mode("assets")
        else:
            filters.set_mode("shots")

    @staticmethod
    def _resolve_type_for_folder(
        win: MainWindow,
        type_folder: str,
    ) -> tuple[str | None, str, str | None]:
        from monostudio.ui_qt.sidebar import _title_case_label

        folder = (type_folder or "").strip()
        filters = win._filter_panel.filters()
        type_id: str | None = folder or None
        pr = getattr(win, "_project_root", None)
        if pr is not None and folder:
            try:
                from monostudio.core.type_registry import TypeRegistry

                resolved = TypeRegistry.for_project(pr).get_type_by_folder(folder)
                if resolved:
                    type_id = resolved
            except Exception:
                pass
        label = filters._type_label_by_id.get(type_id or "", "") if type_id else ""
        display = _title_case_label(label or folder or "Asset")
        icon_name = filters._type_icon_by_id.get(type_id or "", "box") if type_id else "box"
        return type_id, display, icon_name

    @staticmethod
    def _dept_display_label(win: MainWindow, dept_id: str | None) -> str:
        from monostudio.ui_qt.sidebar import _title_case_label

        did = (dept_id or "").strip()
        if not did:
            return ""
        filters = win._filter_panel.filters()
        return _title_case_label(filters._dept_label_by_id.get(did, did))

    @staticmethod
    def _meta_subtitle(win: MainWindow, *, dept_id: str | None, type_label: str) -> str:
        parts: list[str] = []
        dept = TrayMiniPopup._dept_display_label(win, dept_id)
        if dept:
            parts.append(dept)
        tl = (type_label or "").strip()
        if tl:
            parts.append(tl)
        return " · ".join(parts)

    def _set_dept(self, dept_id: str | None) -> None:
        self._dept_id = (dept_id or "").strip() or None
        filters = self._sidebar_filters()
        if filters is not None:
            self._sync_sidebar_mode()
            filters.set_selected_department(self._dept_id, emit=True)
        self._update_filter_labels()
        self._rebuild_list()

    def _set_type(self, type_id: str | None) -> None:
        self._type_id = (type_id or "").strip() or None
        filters = self._sidebar_filters()
        if filters is not None:
            self._sync_sidebar_mode()
            filters.set_selected_type(self._type_id, emit=True)
            self._dept_id = filters.current_department()
        self._update_filter_labels()
        self._rebuild_list()

    def _resolve_ent_department(self, ent: _TrayListEntry) -> str | None:
        """Match MainView active department when tray row has no dept (e.g. All departments)."""
        dept = (ent.department or "").strip() or None
        if dept:
            return dept
        win = self._window
        if win is None:
            return None
        dept = (getattr(win, "current_department", None) or "").strip() or None
        if dept:
            return dept
        filters = self._sidebar_filters()
        if filters is not None:
            dept = (filters.current_department() or "").strip() or None
        return dept

    def _thumbnail_request_kwargs(self, ent: _TrayListEntry) -> dict[str, object]:
        """Same inputs as MainView._thumbnail_request_extras + department for ThumbnailManager."""
        if ent.path is None:
            return {}
        win = self._window
        path_key = str(ent.path)
        pipeline_ref = ent.pipeline_ref
        item_type = ent.kind if ent.kind in ("asset", "shot") else "asset"
        if ent.kind == "recent" and ent.task is not None:
            item_type = ent.task.item_type
        if pipeline_ref is None and win is not None:
            pipeline_ref = win._pipeline_ref_for_path(ent.path, item_type)
        dept = self._resolve_ent_department(ent)
        active_dcc: str | None = None
        if pipeline_ref is not None and dept and win is not None:
            active_dcc = win._main_view.get_active_dcc(ent.path, dept)
        return {
            "path_key": path_key,
            "department": dept,
            "pipeline_ref": pipeline_ref,
            "active_dcc_id": active_dcc,
        }

    def _tray_placeholder_pixmap(self, ent: _TrayListEntry) -> QPixmap:
        """Placeholder when thumb not cached yet — same icon path as main grid/list."""
        win = self._window
        vi = _view_item_for_tray_entry(ent)
        if win is not None and vi is not None:
            icon = win._main_view._icon_for_item(vi)
            if icon is not None and not icon.isNull():
                side = max(_THUMB_W, _THUMB_H)
                sq = icon.pixmap(side, side)
                if sq is not None and not sq.isNull():
                    return _scale_thumb_pixmap_16_9(sq, _THUMB_W, _THUMB_H)
        return lucide_icon("image", size=_THUMB_W, color_hex=_CLR_MUTED).pixmap(_THUMB_W, _THUMB_H)

    def _rebuild_list(self) -> None:
        if getattr(self, "_rebuild_list_busy", False):
            return
        self._rebuild_list_busy = True
        try:
            self._rebuild_list_impl()
        finally:
            self._rebuild_list_busy = False

    def _rebuild_list_impl(self) -> None:
        if self._list_mode == "noti":
            self._rebuild_noti_list()
            return
        entries = self._collect_entries()
        self._list.clear()
        if not entries:
            self._show_body_empty()
            return
        self._show_body_list()
        for ent in entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, ent)
            item.setSizeHint(QSize(0, _ROW_H))
            row = _TrayMiniListRow(self._list)
            row.dcc_clicked.connect(
                lambda dcc_id, e=ent: self._apply_tray_active_dcc(e, dcc_id)
            )
            thumb_kw = self._thumbnail_request_kwargs(ent)
            thumb = None
            if self._thumb_mgr is not None and thumb_kw:
                thumb = self._thumb_mgr.request_thumbnail(
                    str(thumb_kw["path_key"]),
                    thumb_kw.get("department"),  # type: ignore[arg-type]
                    pipeline_ref=thumb_kw.get("pipeline_ref"),  # type: ignore[arg-type]
                    active_dcc_id=thumb_kw.get("active_dcc_id"),  # type: ignore[arg-type]
                )
            thumb_display = (
                thumb
                if thumb is not None and not thumb.isNull()
                else self._tray_placeholder_pixmap(ent)
            )
            dept = self._resolve_ent_department(ent)
            badges, active_dcc = _tray_dcc_badge_info(ent, self._window, department=dept)
            row.set_content(
                title=ent.title,
                subtitle=ent.subtitle,
                row_icon=ent.icon,
                thumb_pixmap=thumb_display,
                dcc_badges=badges,
                active_dcc=active_dcc,
            )
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
        self._list.scrollToTop()

    def _rebuild_noti_list(self) -> None:
        entries = self._entries_noti_for_window()
        self._list.clear()
        if not entries:
            self._show_body_empty("No notifications")
            return
        self._show_body_list()
        ws = getattr(self._window, "_workspace_root", None) if self._window else None
        workspace_root = Path(ws) if ws else None
        project_root = getattr(self._window, "_project_root", None) if self._window else None
        for ent in entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, ent)
            item.setSizeHint(QSize(0, _NOTI_ROW_H))
            row = _TrayNotiListRow(self._list)
            row.set_content(
                entry=ent.entry,
                workspace_root=workspace_root,
                project_root=project_root,
            )
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
        self._list.scrollToTop()

    def _show_body_empty(self, message: str | None = None) -> None:
        if message:
            self._empty.setText(message)
        elif self._list_mode == "noti":
            self._empty.setText("No notifications")
        else:
            self._empty.setText("Nothing to show")
        self._body_stack.setCurrentIndex(1)

    def _show_body_list(self) -> None:
        self._body_stack.setCurrentIndex(0)

    def _collect_entries(self) -> list[_TrayListEntry]:
        win = self._window
        if win is None:
            return []
        if self._list_mode == "recent":
            return self._entries_recent(win)
        if self._list_mode == "assets":
            return self._entries_assets(win)
        if self._list_mode == "shots":
            return self._entries_shots(win)
        return []

    @staticmethod
    def _entries_noti(
        *,
        user_id: str = "",
        project_root: Path | None = None,
    ) -> list[_TrayNotiEntry]:
        items = all_entries(user_id=user_id, project_root=project_root)
        unread = [e for e in items if not e.read]
        read = [e for e in items if e.read]
        ordered = unread + read
        return [_TrayNotiEntry(entry=e) for e in ordered[:_MAX_ROWS]]

    def _entries_noti_for_window(self) -> list[_TrayNotiEntry]:
        win = self._window
        if win is None:
            return []
        from monostudio.core.user_identity import get_current_user

        ws = getattr(win, "_workspace_root", None)
        user = get_current_user(ws)
        uid = user.id if user is not None else ""
        project_root = getattr(win, "_project_root", None)
        return self._entries_noti(user_id=uid, project_root=project_root)

    def _entries_recent(self, win: MainWindow) -> list[_TrayListEntry]:
        store = getattr(win, "_recent_tasks_store", None)
        pr = getattr(win, "_project_root", None)
        if store is None or pr is None:
            return []
        out: list[_TrayListEntry] = []
        for task in store.get_for_project(pr):
            if task.item_type not in ("asset", "shot"):
                continue
            dept = (task.department or "").strip() or None
            title = (task.item_name or "").strip() or "Item"
            if task.item_type == "shot":
                type_label = "Shot"
                row_icon = lucide_icon("clapperboard", size=16, color_hex=_CLR_ACTIVE)
            else:
                _tid, type_label, icon_name = self._resolve_type_for_folder(win, task.asset_type or "")
                row_icon = lucide_icon(icon_name or "box", size=16, color_hex=_CLR_ACTIVE)
            sub = self._meta_subtitle(win, dept_id=dept, type_label=type_label)
            path = Path(task.item_path) if task.item_path else None
            pipeline_ref = (
                win._pipeline_ref_for_path(path, task.item_type) if path is not None else None
            )
            out.append(
                _TrayListEntry(
                    kind="recent",
                    title=title,
                    subtitle=sub,
                    icon=row_icon,
                    path=path,
                    task=task,
                    department=dept,
                    type_id=task.asset_type if task.item_type == "asset" else None,
                    pipeline_ref=pipeline_ref,
                )
            )
            if len(out) >= _MAX_ROWS:
                break
        return out

    def _entries_assets(self, win: MainWindow) -> list[_TrayListEntry]:
        idx = getattr(win, "_project_index", None)
        if idx is None:
            return []
        out: list[_TrayListEntry] = []
        for asset in idx.assets:
            if self._type_id and not self._asset_matches_type_id(win, asset.asset_type, self._type_id):
                continue
            if self._dept_id and not self._entity_has_department(asset, self._dept_id):
                continue
            _tid, type_label, icon_name = self._resolve_type_for_folder(win, asset.asset_type)
            out.append(
                _TrayListEntry(
                    kind="asset",
                    title=asset.name,
                    subtitle=self._meta_subtitle(win, dept_id=self._dept_id, type_label=type_label),
                    icon=lucide_icon(icon_name or "box", size=16, color_hex=_CLR_ACTIVE),
                    path=asset.path,
                    department=self._dept_id,
                    type_id=self._type_id or _tid or asset.asset_type,
                    pipeline_ref=asset,
                )
            )
            if len(out) >= _MAX_ROWS:
                break
        return out

    def _entries_shots(self, win: MainWindow) -> list[_TrayListEntry]:
        idx = getattr(win, "_project_index", None)
        if idx is None:
            return []
        out: list[_TrayListEntry] = []
        for shot in idx.shots:
            if self._dept_id and not self._entity_has_department(shot, self._dept_id):
                continue
            out.append(
                _TrayListEntry(
                    kind="shot",
                    title=shot.name,
                    subtitle=self._meta_subtitle(win, dept_id=self._dept_id, type_label="Shot"),
                    icon=lucide_icon("clapperboard", size=16, color_hex=_CLR_ACTIVE),
                    path=shot.path,
                    department=self._dept_id,
                    pipeline_ref=shot,
                )
            )
            if len(out) >= _MAX_ROWS:
                break
        return out

    @staticmethod
    def _entity_has_department(entity: Asset | Shot, dept_id: str) -> bool:
        did = (dept_id or "").strip()
        if not did:
            return True
        for d in getattr(entity, "departments", ()) or ():
            if (d.name or "").strip() == did:
                return True
        return False

    @staticmethod
    def _asset_matches_type_id(win: MainWindow, asset_type_folder: str, type_id: str) -> bool:
        tid = (type_id or "").strip()
        if not tid:
            return True
        folder = (asset_type_folder or "").strip()
        if folder == tid:
            return True
        try:
            pr = getattr(win, "_project_root", None)
            if pr is not None:
                from monostudio.core.type_registry import TypeRegistry

                reg = TypeRegistry.for_project(pr)
                return reg.get_type_folder(tid) == folder
        except Exception:
            pass
        return False

    @staticmethod
    def _task_row_icon(task: RecentTask):
        dcc = (task.dcc or "").strip()
        if dcc:
            try:
                from monostudio.ui_qt.sidebar import _task_dcc_icon

                return _task_dcc_icon(dcc, is_selected=False)
            except Exception:
                pass
        if task.item_type == "shot":
            return lucide_icon("clapperboard", size=16, color_hex=_CLR_ACTIVE)
        return lucide_icon("box", size=16, color_hex=_CLR_ACTIVE)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        ent = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(ent, _TrayNotiEntry):
            self.notification_selected.emit(ent.entry)
            self.hide()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        ent = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(ent, _TrayListEntry):
            self._open_list_entry(ent)

    def _open_list_entry(self, ent: _TrayListEntry) -> None:
        """Double-click: open work file in DCC (same as main view double-click)."""
        if ent.kind == "recent" and ent.task is not None:
            self.task_open_file_requested.emit(ent.task)
        elif ent.path is not None:
            self.entity_open_file_requested.emit(
                ent.kind, ent.path, ent.department, ent.type_id
            )
        self.hide()

    @staticmethod
    def _dept_norm(dept_id: str | None) -> str:
        return (dept_id or "").strip().casefold()

    def _folder_for_open(self, ent: _TrayListEntry) -> Path | None:
        if ent.path is None:
            return None
        ref = ent.pipeline_ref
        dept = self._dept_norm(ent.department)
        if dept and isinstance(ref, (Asset, Shot)):
            for d in getattr(ref, "departments", ()) or ():
                if (d.name or "").strip().casefold() == dept:
                    return Path(d.path)
        return ent.path

    def _copy_path_text(self, ent: _TrayListEntry, *, work: bool) -> str | None:
        ref = ent.pipeline_ref
        if work:
            dept = (ent.department or "").strip()
            if not dept or not isinstance(ref, (Asset, Shot)):
                return None
            wp = _resolved_work_path_for_copy(ref, dept, None)
            return str(wp) if wp is not None else None
        if ent.path is None:
            return None
        try:
            return str(ent.path.resolve())
        except OSError:
            return str(ent.path)

    @staticmethod
    def _copy_to_clipboard(path_text: str) -> None:
        text = (path_text or "").strip()
        if not text:
            return
        app = QApplication.instance()
        if app is None:
            return
        cb = app.clipboard()
        if cb is None:
            return
        cb.setText(text)
        from monostudio.ui_qt.notification import notify as notification_service

        notification_service.success(f"Copied: {text}")

    @staticmethod
    def _shell_open_folder(folder: Path | None) -> None:
        if folder is None:
            return
        try:
            if not folder.exists():
                return
        except OSError:
            return
        from monostudio.core.shell_open import open_folder

        open_folder(folder)

    def _on_list_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        ent = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(ent, _TrayNotiEntry):
            menu = MonosMenu(self)
            open_act = menu.addAction(
                lucide_icon("bell", size=16, color_hex=_CLR_LABEL),
                "Open in MONOS",
            )
            chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
            if chosen is open_act:
                self.notification_selected.emit(ent.entry)
                self.hide()
            return
        if not isinstance(ent, _TrayListEntry) or ent.path is None:
            return

        dept = (ent.department or "").strip()
        ref = ent.pipeline_ref
        has_entity = isinstance(ref, (Asset, Shot))
        _ic = MONOS_COLORS.get("text_label", _CLR_LABEL)

        menu = MonosMenu(self)
        open_monos = menu.addAction(
            lucide_icon("layout-dashboard", size=16, color_hex=_ic),
            "Open in MONOS",
        )
        change_dcc_acts: list[tuple[object, str]] = []
        if dept and has_entity:
            badges, active_dcc = _tray_dcc_badge_info(ent, self._window)
            pickable = [
                (ic, did, st)
                for ic, did, st in badges
                if st == "exists" and (did or "").strip()
            ]
            if pickable:
                change_menu = menu.addMenu("Change DCC")
                change_menu.setIcon(lucide_icon("layers", size=16, color_hex=_ic))
                dcc_group = QActionGroup(change_menu)
                dcc_group.setExclusive(True)
                try:
                    from monostudio.core.dcc_registry import get_default_dcc_registry

                    dcc_reg = get_default_dcc_registry()
                except Exception:
                    dcc_reg = None
                for ic, did, _st in pickable:
                    label = did
                    if dcc_reg is not None:
                        try:
                            info = dcc_reg.get_dcc_info(did)
                            if isinstance(info, dict) and info.get("label"):
                                label = str(info["label"])
                        except Exception:
                            pass
                    act = change_menu.addAction(ic, label) if ic and not ic.isNull() else change_menu.addAction(label)
                    act.setCheckable(True)
                    dcc_group.addAction(act)
                    act.setChecked((did or "").strip() == (active_dcc or ""))
                    change_dcc_acts.append((act, (did or "").strip()))
        menu.addSeparator()
        copy_path = menu.addAction(lucide_icon("copy", size=16, color_hex=_ic), "Copy Path")
        copy_work: object | None = None
        if dept and has_entity:
            copy_work = menu.addAction(lucide_icon("copy", size=16, color_hex=_ic), "Copy Work Path")
            if self._copy_path_text(ent, work=True) is None:
                copy_work.setEnabled(False)  # type: ignore[union-attr]
                copy_work.setToolTip("No work path in this department.")  # type: ignore[union-attr]
        menu.addSeparator()
        open_folder_act = menu.addAction(
            lucide_icon("folder-open", size=16, color_hex=_ic),
            "Open Folder",
        )
        open_work_act = None
        open_pub_act = None
        if has_entity:
            open_work_act = menu.addAction(
                lucide_icon("folder", size=16, color_hex=_ic),
                "Open Work Folder",
            )
            open_pub_act = menu.addAction(
                lucide_icon("folder-open", size=16, color_hex=_ic),
                "Open Publish Folder",
            )
            work_root = _resolve_work_root_folder_any(ref, dept or None)
            pub_root = _resolve_publish_root_folder_any(ref, dept or None)
            if work_root is None:
                open_work_act.setEnabled(False)
                open_work_act.setToolTip("No work folder for this department.")
            if pub_root is None:
                open_pub_act.setEnabled(False)
                open_pub_act.setToolTip("No publish folder for this department.")

        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is open_monos:
            self._open_list_entry(ent)
            return
        for act, did in change_dcc_acts:
            if chosen is act:
                self._apply_tray_active_dcc(ent, did)
                return
        if chosen is copy_path:
            path_text = self._copy_path_text(ent, work=False)
            if path_text:
                self._copy_to_clipboard(path_text)
            return
        if copy_work is not None and chosen is copy_work:
            path_text = self._copy_path_text(ent, work=True)
            if path_text:
                self._copy_to_clipboard(path_text)
            return
        if chosen is open_folder_act:
            self._shell_open_folder(self._folder_for_open(ent))
            return
        if open_work_act is not None and chosen is open_work_act and has_entity:
            self._shell_open_folder(_resolve_work_root_folder_any(ref, dept or None))
            return
        if open_pub_act is not None and chosen is open_pub_act and has_entity:
            self._shell_open_folder(_resolve_publish_root_folder_any(ref, dept or None))

    def _on_open_monos(self) -> None:
        if self._list_mode == "noti":
            self.open_notifications_requested.emit()
        else:
            self.open_monos_requested.emit()
        self.hide()

    def refresh_thumbnails(self) -> None:
        if not self.isVisible() or self._window is None:
            return
        self._rebuild_list()

    def refresh_notifications(self) -> None:
        if not self.isVisible() or self._list_mode != "noti":
            return
        self._rebuild_list()
