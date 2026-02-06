from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, QRect, QSettings, QSize, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QApplication,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStackedWidget,
    QTableView,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.view_items import ViewItem, display_name_for_item
from monostudio.ui_qt.thumbnails import ThumbnailCache
from monostudio.ui_qt.style import MONOS_COLORS, THUMB_TAG_STYLE, monos_font
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.dcc_status import resolve_dcc_status
from monostudio.core.workspace_reader import ProjectQuickStats
from monostudio.core.models import Asset, Shot

import logging
_dcc_debug_log = logging.getLogger("monostudio.dcc_debug")

# Lucide icon names for type/department thumb badges (icon-only, no text).
_TYPE_ICON_MAP: dict[str, str] = {
    "project": "layout-dashboard",
    "shot": "clapperboard",
    "_characters": "user",
    "character": "user",
    "_props": "package",
    "prop": "package",
    "_environment": "trees",
    "environment": "trees",
}
_DEPT_ICON_MAP: dict[str, str] = {
    "layout": "layout-dashboard",
    "model": "box",
    "modeling": "box",
    "rig": "bone",
    "rigging": "bone",
    "surfacing": "palette",
    "grooming": "scissors",
    "lookdev": "sparkles",
    "anim": "clapperboard",
    "animation": "clapperboard",
    "fx": "zap",
    "lighting": "lightbulb",
    "comp": "sliders-horizontal",
}

# Labels for type badge tooltip (readable names).
_TYPE_TOOLTIP_MAP: dict[str, str] = {
    "project": "Project",
    "shot": "Shot",
    "character": "Character",
    "_characters": "Character",
    "prop": "Prop",
    "_props": "Prop",
    "environment": "Environment",
    "_environment": "Environment",
}


def _work_file_version_from_path(path: Path) -> int | None:
    """Parse work file version from path stem (e.g. prefix_v003 -> 3). Returns int or None."""
    stem = (path.stem or "").strip()
    idx = stem.rfind("_v")
    if idx < 0 or len(stem) < idx + 5:
        return None
    mid = stem[idx + 2 : idx + 5]
    if len(mid) == 3 and mid.isdigit():
        return int(mid)
    return None


def _card_work_file_version(ref: Asset | Shot, active_department: str | None) -> str | None:
    """
    Work file version for card meta when a department is selected.
    Returns None when department is "all" (no filter) → caller should hide version.
    Returns "v001" or "—" when department is set.
    """
    dep = (active_department or "").strip()
    if not dep:
        return None
    states = getattr(ref, "dcc_work_states", ()) or ()
    max_ver: int | None = None
    for (dept_id, _dcc_id), state in states:
        if (dept_id or "").strip().casefold() != dep.casefold():
            continue
        path = getattr(state, "work_file_path", None)
        if path is None:
            continue
        v = _work_file_version_from_path(path)
        if v is not None and (max_ver is None or v > max_ver):
            max_ver = v
    if max_ver is not None:
        return f"v{max_ver:03d}"
    return "—"


def _item_last_opened_dcc(item_path: Path, active_department: str) -> str | None:
    """Read last-opened DCC for this item from .monostudio/open.json. Returns dcc_id or None."""
    if not item_path or not isinstance(item_path, Path):
        return None
    meta_path = item_path / ".monostudio" / "open.json"
    try:
        if not meta_path.is_file():
            return None
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    dep = (active_department or "").strip().casefold()
    by_dep = data.get("last_open_by_department")
    if isinstance(by_dep, dict):
        node = by_dep.get(dep) or by_dep.get(active_department)
        if isinstance(node, dict):
            dcc = node.get("dcc")
            if isinstance(dcc, str) and dcc.strip():
                return dcc.strip()
    last_open = data.get("last_open")
    if isinstance(last_open, dict) and (last_open.get("department") or "").strip().casefold() == dep:
        dcc = last_open.get("dcc")
        if isinstance(dcc, str) and dcc.strip():
            return dcc.strip()
    return None


def _thumb_badge_rects(cell_rect: QRect, gap_px: int, has_dept: bool) -> tuple[QRect, QRect | None]:
    """Compute type and department badge rects (matches delegate layout). Used for tooltip hit-test."""
    r = cell_rect.adjusted(0, 0, -gap_px, -gap_px)
    border_px = 1
    inner = r.adjusted(border_px, border_px, -border_px, -border_px)
    thumb_w = inner.width()
    thumb_h = max(1, int(thumb_w * 9 / 16))
    thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))
    chip_r = (16 + 5 * 2) // 2
    chip_h = chip_r * 2
    gap = 4
    ix, iy = thumb.left() + 12, thumb.top() + 12
    type_rect = QRect(ix, iy, chip_h, chip_h)
    dept_rect = QRect(ix + chip_h + gap, iy, chip_h, chip_h) if has_dept else None
    return type_rect, dept_rect


class _ClearOnEmptyClickListView(QListView):
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if not self.indexAt(event.pos()).isValid():
            self.clearSelection()
        super().mousePressEvent(event)


class _ClearOnEmptyClickTableView(QTableView):
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if not self.indexAt(event.pos()).isValid():
            self.clearSelection()
        super().mousePressEvent(event)


class _GridCardDelegate(QStyledItemDelegate):
    """
    Grid card painter (Grid view):
    - 16:9 thumbnail
    - Status badge (top-left)
    - Name (Inter semibold)
    - Version + ID (JetBrains Mono)
    """

    def __init__(self, *, view: QListView) -> None:
        super().__init__(view)
        self._view = view
        self._hovered_row: int | None = None
        self._card_size = QSize(320, 260)
        self._gap_px = 24
        self._active_department: str | None = None
        self._active_department_icon_name: str | None = None  # from pipeline (subdepartment-safe)

        # Theme cache (no per-paint parsing / allocations)
        self._c_card_bg = QColor(MONOS_COLORS["card_bg"])
        self._c_card_hover = QColor(MONOS_COLORS["card_hover"])
        self._c_border = QColor(MONOS_COLORS["border"])
        self._c_text_primary = QColor(MONOS_COLORS["text_primary"])
        self._c_text_primary_highlight = QColor(MONOS_COLORS["text_primary_highlight"])
        self._c_text_primary_selected = QColor(MONOS_COLORS["text_primary_selected"])
        self._c_text_meta = QColor(MONOS_COLORS["text_meta"])
        self._pen_border = QPen(self._c_border, 1)
        self._c_selected = QColor(MONOS_COLORS["blue_600"])
        self._pen_selected = QPen(self._c_selected, 2)

        # Font cache (no per-paint allocations)
        # Shared thumb tag style (status + filter tags): same geometry, only color differs.
        self._font_thumb_tag = monos_font("Inter", int(THUMB_TAG_STYLE["font_size"]), QFont.Weight(int(THUMB_TAG_STYLE["font_weight"])))
        self._font_name = monos_font("Inter", 13, QFont.Weight.Medium)
        self._font_mono = monos_font("JetBrains Mono", 8)
        # Shared meta style (mono) for ALL cards.
        self._font_meta_mono = QFont(self._font_mono)
        self._font_meta = monos_font("Inter", 11)

        st = view.style()
        self._icon_eye = lucide_icon("eye", size=16, color_hex=MONOS_COLORS["text_primary"])
        self._icon_download = lucide_icon("download", size=16, color_hex=MONOS_COLORS["text_primary"])
        self._icon_more = lucide_icon("ellipsis", size=16, color_hex=MONOS_COLORS["text_primary"])

    @staticmethod
    def _norm(s: str | None) -> str:
        return (s or "").strip().casefold()

    def set_hovered_index(self, index) -> None:
        row = index.row() if index and index.isValid() else None
        if self._hovered_row == row:
            return
        self._hovered_row = row
        self._view.viewport().update()

    def set_card_size(self, size: QSize) -> None:
        if size.isValid() and size != self._card_size:
            self._card_size = size
            self._view.viewport().update()

    def set_gap_px(self, gap_px: int) -> None:
        if gap_px > 0 and gap_px != self._gap_px:
            self._gap_px = gap_px
            self._view.viewport().update()

    def set_active_department(self, department: str | None, *, icon_name: str | None = None) -> None:
        dep = (department or "").strip() or None
        if dep == self._active_department and icon_name == self._active_department_icon_name:
            return
        self._active_department = dep
        self._active_department_icon_name = (icon_name or "").strip() or None
        self._view.viewport().update()

    @staticmethod
    def _rounded_rect(p: QPainter, r: QRect, radius: int, *, fill: QColor, pen: QPen | None = None) -> None:
        p.setPen(Qt.NoPen if pen is None else pen)
        p.setBrush(fill)
        p.drawRoundedRect(r, radius, radius)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        _timer = QElapsedTimer()
        _timer.start()
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            super().paint(painter, option, index)
            return

        # Paint inside the grid cell leaving explicit gap on right/bottom.
        g = max(0, int(self._gap_px))
        r = option.rect.adjusted(0, 0, -g, -g)
        if r.width() <= 0 or r.height() <= 0:
            return
        p = painter
        p.save()
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)

            # Card background
            bg = self._c_card_bg
            hover = bool(self._hovered_row == index.row())
            if hover:
                bg = self._c_card_hover

            selected = bool(option.state & QStyle.State_Selected)
            border_px = 2 if selected else 1
            border_pen = self._pen_selected if selected else self._pen_border

            outer = r
            # Fill first (no border), then draw content, then draw border ON TOP.
            self._rounded_rect(p, outer, 12, fill=bg, pen=None)

            # Clip content inside the border so thumbnails never paint over it.
            inner = outer.adjusted(border_px, border_px, -border_px, -border_px)
            inner_radius = max(0, 12 - border_px)
            clip = QPainterPath()
            clip.addRoundedRect(inner, inner_radius, inner_radius)
            p.setClipPath(clip)

            # 16:9 thumbnail region (fixed aspect)
            thumb_w = inner.width()
            thumb_h = max(1, int(thumb_w * 9 / 16))
            thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))

            # Draw thumbnail from icon (center-crop)
            icon = index.data(Qt.DecorationRole)
            if isinstance(icon, QIcon):
                src = icon.pixmap(256, 256)
                if not src.isNull():
                    scaled = src.scaled(
                        thumb.size(),
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation,
                    )
                    sx = max(0, (scaled.width() - thumb.width()) // 2)
                    sy = max(0, (scaled.height() - thumb.height()) // 2)
                    crop = scaled.copy(QRect(QPoint(sx, sy), thumb.size()))
                    p.drawPixmap(thumb, crop)

            def status_key() -> str:
                # User-set status overrides computed (asset/shot only).
                if item.kind.value in ("asset", "shot") and getattr(item, "user_status", None):
                    return (item.user_status or "").strip().lower() or "waiting"
                if item.kind.value == "project":
                    stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
                    return (stats.status if stats else "WAITING").lower()
                if isinstance(item.ref, Asset):
                    if any(d.publish_version_count > 0 for d in item.ref.departments):
                        return "ready"
                    if any(d.work_exists for d in item.ref.departments):
                        return "progress"
                    return "waiting"
                if isinstance(item.ref, Shot):
                    if any(d.publish_version_count > 0 for d in item.ref.departments):
                        return "ready"
                    if any(d.work_exists for d in item.ref.departments):
                        return "progress"
                    return "waiting"
                return "waiting"

            def status_style(k: str) -> tuple[QColor, QColor, QColor]:
                # (text, bg, border) with higher alpha for readability over thumbnails.
                a_bg = int(THUMB_TAG_STYLE["bg_alpha"])
                a_border = int(THUMB_TAG_STYLE["border_alpha"])
                def with_alpha(base: QColor, a: int) -> QColor:
                    c2 = QColor(base)
                    c2.setAlpha(int(a))
                    return c2
                if k == "ready":
                    c = QColor(MONOS_COLORS["emerald_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "progress":
                    # Match Inspector: PROGRESS uses amber, not blue.
                    c = QColor(MONOS_COLORS["amber_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                if k == "blocked":
                    c = QColor(MONOS_COLORS["red_500"])
                    return (c, with_alpha(c, a_bg), with_alpha(c, a_border))
                c = QColor(MONOS_COLORS["waiting"])
                return (c, with_alpha(c, a_bg), with_alpha(c, a_border))

            # Unified thumb tag renderer (status + contextual filter tags).
            p.setFont(self._font_thumb_tag)
            metrics = p.fontMetrics()
            pad_x = int(THUMB_TAG_STYLE["pad_x"])
            pad_y = int(THUMB_TAG_STYLE["pad_y"])
            radius = int(THUMB_TAG_STYLE["radius"])
            border_px = int(THUMB_TAG_STYLE["border_px"])

            def draw_thumb_tag(*, x: int, y: int, text: str, text_color: QColor, bg_color: QColor, border_color: QColor) -> QRect:
                t = (text or "").strip().upper()
                w2 = metrics.horizontalAdvance(t) + pad_x * 2
                h2 = metrics.height() + pad_y * 2
                r2 = QRect(x, y, w2, h2)
                self._rounded_rect(p, r2, radius, fill=bg_color, pen=QPen(border_color, border_px))
                p.setPen(text_color)
                p.drawText(r2, Qt.AlignCenter, t)
                return r2

            # Status dot (right of thumb) — colored circle only, no text
            k = status_key()
            _text_c, _bg_c, border_c = status_style(k)
            dot_radius = 5
            dot_x = thumb.right() - 12 - dot_radius
            dot_y = thumb.top() + 12 + dot_radius
            p.setPen(QPen(border_c, 1))
            p.setBrush(border_c)
            p.drawEllipse(QPoint(dot_x, dot_y), dot_radius, dot_radius)

            # Type + Department icons (top-left, side by side, fully round, distinct colors)
            icon_size = 16
            pad = 5
            chip_r = (icon_size + pad * 2) // 2  # radius for circle
            chip_h = chip_r * 2
            gap = 4
            ix = thumb.left() + 12
            iy = thumb.top() + 12
            # Colors: project / shot / type (asset) / department — all different
            type_badge_raw = (item.type_badge or "").strip()
            type_badge_lower = type_badge_raw.lower()
            if item.kind.value == "project":
                type_chip_color = QColor("#8b5cf6")  # violet
            elif item.kind.value == "shot":
                type_chip_color = QColor(MONOS_COLORS["amber_500"])
            else:
                type_chip_color = QColor(MONOS_COLORS["emerald_500"])
            type_chip_color.setAlpha(220)
            type_icon_name = _TYPE_ICON_MAP.get(type_badge_lower) or _TYPE_ICON_MAP.get(type_badge_raw) or "box"
            type_icon = lucide_icon(type_icon_name, size=icon_size, color_hex="#ffffff")
            type_pix = type_icon.pixmap(icon_size, icon_size)
            if not type_pix.isNull():
                cx, cy = ix + chip_r, iy + chip_r
                p.setPen(Qt.NoPen)
                p.setBrush(type_chip_color)
                p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                p.drawPixmap(ix + pad, iy + pad, type_pix)
                ix += chip_h + gap
            dep = (self._active_department or "").strip()
            if dep:
                dept_key = self._norm(dep)
                dept_icon_name = (self._active_department_icon_name or "").strip() or _DEPT_ICON_MAP.get(dept_key, "layers")
                dept_chip_color = QColor(MONOS_COLORS["blue_500"])
                dept_chip_color.setAlpha(220)
                dept_icon = lucide_icon(dept_icon_name, size=icon_size, color_hex="#ffffff")
                dept_pix = dept_icon.pixmap(icon_size, icon_size)
                if not dept_pix.isNull():
                    cx, cy = ix + chip_r, iy + chip_r
                    p.setPen(Qt.NoPen)
                    p.setBrush(dept_chip_color)
                    p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                    p.drawPixmap(ix + pad, iy + pad, dept_pix)

            # DCC badges (bottom-right of thumb) — filesystem-driven; "exists" = icon, "creating" = "Creating…"
            # Prefer dcc_work_states (scan) so subdepartments show badges; fallback to registry for "creating" only.
            def dcc_badges_for_item() -> list[tuple[QIcon | None, str, str]]:
                """Returns (icon or None, dcc_id, status) with status in ("exists", "creating")."""
                out: list[tuple[QIcon | None, str, str]] = []
                ref = item.ref
                if not isinstance(ref, (Asset, Shot)):
                    return out
                try:
                    reg = get_default_dcc_registry()
                except Exception:
                    return out
                active_key = self._norm((self._active_department or "").strip())
                states = getattr(ref, "dcc_work_states", ()) or ()
                seen: set[tuple[str, str]] = set()

                def add_badge(dept_id: str, dcc_id: str, status: str) -> None:
                    if (dept_id, dcc_id) in seen:
                        return
                    seen.add((dept_id, dcc_id))
                    if status == "creating":
                        out.append((None, dcc_id, "creating"))
                        return
                    if status != "exists":
                        return
                    try:
                        info = reg.get_dcc_info(dcc_id) if dcc_id else None
                    except Exception:
                        info = None
                    slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
                    color = info.get("brand_color_hex") if isinstance(info, dict) else None
                    if isinstance(slug, str) and slug.strip():
                        ic = brand_icon(slug.strip(), size=14, color_hex=(color if isinstance(color, str) else None))
                    else:
                        ic = lucide_icon("layers", size=14, color_hex=MONOS_COLORS["text_label"])
                    out.append((ic, dcc_id, "exists"))

                for (dept_id, dcc_id), _state in states:
                    dept_id = (dept_id or "").strip()
                    dcc_id = (dcc_id or "").strip()
                    if not dept_id or not dcc_id:
                        continue
                    if active_key and self._norm(dept_id) != active_key:
                        continue
                    status = resolve_dcc_status(ref, dept_id, dcc_id)
                    if status in ("exists", "creating"):
                        add_badge(dept_id, dcc_id, status)
                for d in getattr(ref, "departments", ()) or ():
                    dept_name = getattr(d, "name", "") or ""
                    if active_key and self._norm(dept_name) != active_key:
                        continue
                    for dcc_id in reg.get_available_dccs(dept_name) or []:
                        dcc_id = (dcc_id or "").strip()
                        if not dcc_id:
                            continue
                        status = resolve_dcc_status(ref, dept_name, dcc_id)
                        if status == "creating":
                            add_badge(dept_name, dcc_id, "creating")
                return out

            dcc_list = dcc_badges_for_item()
            if dcc_list:
                size = 16
                pad = 4
                gap = 3
                max_show = 4
                chip_h = size + pad * 2
                chip_r = chip_h // 2
                creating_chip_w = 56
                widths = [creating_chip_w if s == "creating" else chip_h for (_, _, s) in dcc_list[:max_show]]
                row_w = sum(widths) + (len(widths) - 1) * gap
                base_x = thumb.right() - 12 - row_w
                base_y = thumb.bottom() - 12 - chip_h
                creating_font = monos_font("Inter", 9)
                dcc_bg = QColor(0, 0, 0, 160)
                last_used_dcc = _item_last_opened_dcc(item.path, self._active_department or "") if getattr(item, "path", None) else None
                x_cursor = base_x
                for i, (dcc_icon, _dcc_id, badge_status) in enumerate(dcc_list[:max_show]):
                    w = widths[i]
                    bg_rect = QRect(x_cursor, base_y, w, chip_h)
                    is_last_used = (last_used_dcc and (_dcc_id or "").strip() == last_used_dcc)
                    if badge_status == "creating":
                        # Pill: bo tròn, không border; chỉ border xanh dương 50% nếu là last-used
                        p.setPen(Qt.NoPen)
                        p.setBrush(dcc_bg)
                        p.drawRoundedRect(bg_rect, chip_r, chip_r)
                        if is_last_used:
                            p.setPen(QPen(QColor(37, 99, 235, 128), 2))
                            p.setBrush(Qt.NoBrush)
                            p.drawRoundedRect(bg_rect, chip_r, chip_r)
                        _dcc_debug_log.debug("paint DCC badge Creating… entity_path=%r dcc_id=%r", getattr(item.ref, "path", None), _dcc_id)
                        p.setFont(creating_font)
                        p.setPen(QColor(255, 255, 255))
                        p.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, "Creating…")
                    else:
                        # Icon: hình tròn, không border; chỉ border xanh dương 50% nếu là last-used
                        cx = x_cursor + chip_r
                        cy = base_y + chip_r
                        p.setPen(Qt.NoPen)
                        p.setBrush(dcc_bg)
                        p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                        if is_last_used:
                            p.setPen(QPen(QColor(37, 99, 235, 128), 2))
                            p.setBrush(Qt.NoBrush)
                            p.drawEllipse(QPoint(cx, cy), chip_r, chip_r)
                        if dcc_icon is not None and not dcc_icon.isNull():
                            pix = dcc_icon.pixmap(size, size)
                            if not pix.isNull():
                                p.drawPixmap(x_cursor + pad, base_y + pad, pix)
                    x_cursor += w + gap

            # Stop clipping before text to avoid rounded-corner cropping issues
            p.setClipping(False)

            # Text blocks under thumbnail
            # Spec: p-4 (16px) padding for info block
            y = thumb.bottom() + 16
            x = inner.left() + 16
            w = inner.width() - 32

            p.setFont(self._font_name)
            # Highlight state: hover uses highlight (blue), selected uses selected (fafafa).
            if selected:
                p.setPen(self._c_text_primary_selected)
            elif hover:
                p.setPen(self._c_text_primary_highlight)
            else:
                p.setPen(self._c_text_primary)
            name_rect = QRect(x, y, w, 20)
            p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, display_name_for_item(item))

            if item.kind.value == "project":
                stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
                shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
                assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
                # Match shot/asset meta style (mono, label blocks, spaced).
                meta = f"SHOTS {shots}   ASSETS {assets}"
                p.setFont(self._font_meta_mono)
                p.setPen(self._c_text_meta)
                meta_rect = QRect(x, y + 24, w, 16)
                p.drawText(meta_rect, Qt.AlignLeft | Qt.AlignVCenter, meta)
            else:
                # Meta: ID + version (from work file when department is set); assignee tạm ẩn.
                p.setFont(self._font_meta_mono)
                p.setPen(self._c_text_meta)
                active_dep = (self._active_department or "").strip()
                show_version = bool(active_dep)
                ver_str = _card_work_file_version(item.ref, self._active_department) if isinstance(item.ref, (Asset, Shot)) else None
                if show_version and ver_str is not None:
                    meta = f"ID {item.name}   {ver_str}" if ver_str != "—" else f"ID {item.name}   v —"
                else:
                    meta = f"ID {item.name}"
                meta_rect = QRect(x, y + 24, w, 16)
                p.drawText(meta_rect, Qt.AlignLeft | Qt.AlignVCenter, meta)

            # Border on top (selected border = 2px)
            p.setPen(border_pen)
            p.setBrush(Qt.NoBrush)
            # Keep stroke safely inside cell.
            stroke_inset = 1
            border_rect = outer.adjusted(stroke_inset, stroke_inset, -stroke_inset, -stroke_inset)
            p.drawRoundedRect(border_rect, 12, 12)

        finally:
            p.restore()
            try:
                from monostudio.ui_qt.stress_profiler import enabled, record_paint_ms
                if enabled():
                    record_paint_ms(float(_timer.elapsed()) if hasattr(_timer, "elapsed") else 0)
            except Exception:
                pass

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        # Responsive card size is controlled by MainView; keep uniform sizes for performance.
        return self._card_size


class MainView(QWidget):
    """
    Spec: Main View has Tile (default) and List mode; has Search + Filters.
    Phase 0: UI only (no filesystem model yet), so views start empty.
    """

    valid_selection_changed = Signal(bool)
    selection_id_changed = Signal(object)  # str | None — selection intent for AppState
    item_activated = Signal(object)  # emits ViewItem
    refresh_requested = Signal()
    root_context_menu_requested = Signal(object)  # emits global QPoint
    copy_inventory_requested = Signal(object)  # emits ViewItem (asset/shot only)
    delete_requested = Signal(object)  # emits ViewItem (asset/shot only)
    open_requested = Signal(object)  # emits ViewItem (asset/shot only)
    open_with_requested = Signal(object)  # emits ViewItem (asset/shot only)
    create_new_requested = Signal(object)  # emits ViewItem (asset/shot only)
    status_set_requested = Signal(object, str)  # (ViewItem, status: ready|progress|waiting|blocked)
    primary_action_requested = Signal()  # header primary action
    view_mode_changed = Signal(str)  # "tile" | "list"

    _SETTINGS_KEY_VIEW_MODE_PREFIX = "main_view/mode"
    _SETTINGS_KEY_CARD_SIZE_PREFIX = "main_view/card_size"
    _THUMBNAIL_SIZE_PX = 384  # backing cache size (square); painted as 16:9 in grid
    _THUMB_STATE_ROLE = Qt.UserRole + 1  # per-item state in tile model ("loaded"|"missing")
    _GRID_GAP_PX = 12
    _CARD_SIZE_PRESETS: dict[str, float] = {"small": 0.4, "medium": 0.6, "large": 1.00}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._project_root: str | None = None
        self._empty_override: str | None = None
        self._thumb_cache = ThumbnailCache(size_px=self._THUMBNAIL_SIZE_PX)
        self._thumbnail_manager: object | None = None
        self._thumb_prefetch_scheduled = False

        self._view_mode: str = "tile"
        self._browser_context: str = "asset"  # "project" | "asset" | "shot"
        self._card_size_preset: str = self._load_card_size_preset()
        # Header context (read-only)
        self._base_title: str = ""
        self._active_department: str | None = None
        self._active_department_label: str | None = None  # pipeline label (subdepartment-safe)
        self._active_department_icon_name: str | None = None  # pipeline icon (subdepartment-safe)

        header = QWidget(self)
        header.setObjectName("MainViewHeader")
        # Ensure QSS background is painted for this container.
        header.setAttribute(Qt.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(12)

        title_row = QWidget(header)
        title_row.setObjectName("MainViewTitleRow")
        title_row_l = QHBoxLayout(title_row)
        title_row_l.setContentsMargins(0, 0, 0, 0)
        title_row_l.setSpacing(8)

        self._context_title = QLabel("Asset", title_row)
        self._context_title.setObjectName("MainViewContextTitle")
        f_title = monos_font("Inter", 16, QFont.Weight.Bold)
        self._context_title.setFont(f_title)

        self._type_badge = QWidget(title_row)
        self._type_badge.setObjectName("MainViewTypeBadge")
        self._type_badge.setAttribute(Qt.WA_StyledBackground, True)
        type_badge_l = QHBoxLayout(self._type_badge)
        type_badge_l.setContentsMargins(8, 4, 10, 4)
        type_badge_l.setSpacing(6)
        self._type_icon = QLabel(self._type_badge)
        self._type_icon.setScaledContents(False)
        self._type_icon.setFixedSize(16, 16)
        type_font = monos_font("Inter", 13, QFont.Weight.Bold)
        self._type_label = QLabel(self._type_badge)
        self._type_label.setObjectName("MainViewTypeBadgeLabel")
        self._type_label.setFont(type_font)
        type_badge_l.addWidget(self._type_icon, 0, Qt.AlignVCenter)
        type_badge_l.addWidget(self._type_label, 0, Qt.AlignVCenter)

        self._department_badge = QWidget(title_row)
        self._department_badge.setObjectName("MainViewDepartmentBadge")
        self._department_badge.setAttribute(Qt.WA_StyledBackground, True)
        self._department_badge.setVisible(False)
        badge_l = QHBoxLayout(self._department_badge)
        badge_l.setContentsMargins(8, 4, 10, 4)
        badge_l.setSpacing(6)
        self._department_icon = QLabel(self._department_badge)
        self._department_icon.setScaledContents(False)
        self._department_icon.setFixedSize(16, 16)
        dep_font = monos_font("Inter", 13, QFont.Weight.Bold)
        self._department_label = QLabel(self._department_badge)
        self._department_label.setObjectName("MainViewDepartmentBadgeLabel")
        self._department_label.setFont(dep_font)
        badge_l.addWidget(self._department_icon, 0, Qt.AlignVCenter)
        badge_l.addWidget(self._department_label, 0, Qt.AlignVCenter)
        title_row_l.addWidget(self._context_title, 0, Qt.AlignVCenter)
        title_row_l.addWidget(self._type_badge, 0, Qt.AlignVCenter)
        title_row_l.addWidget(self._department_badge, 0, Qt.AlignVCenter)

        # Center: View toggle (Grid | List) — pill UI same as Settings Tier3 (Asset Depts | Shot Depts)
        toggle = QWidget(header)
        toggle.setObjectName("Tier3Container")
        toggle.setAttribute(Qt.WA_StyledBackground, True)
        toggle.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        toggle_layout = QHBoxLayout(toggle)
        toggle_layout.setContentsMargins(6, 6, 6, 6)
        toggle_layout.setSpacing(4)

        self._btn_grid = QPushButton("Grid", toggle)
        self._btn_grid.setObjectName("Tier3Pill")
        self._btn_grid.setCheckable(True)
        self._btn_grid.setFlat(True)

        self._btn_list = QPushButton("List", toggle)
        self._btn_list.setObjectName("Tier3Pill")
        self._btn_list.setCheckable(True)
        self._btn_list.setFlat(True)

        self._view_toggle_group = QButtonGroup(self)
        self._view_toggle_group.setExclusive(True)
        self._view_toggle_group.addButton(self._btn_grid, 0)
        self._view_toggle_group.addButton(self._btn_list, 1)
        self._btn_grid.clicked.connect(lambda: self.set_view_mode("tile", save=True))
        self._btn_list.clicked.connect(lambda: self.set_view_mode("list", save=True))

        toggle_layout.addWidget(self._btn_grid, 0)
        toggle_layout.addWidget(self._btn_list, 0)

        # Right: Card size (Small/Medium/Large) for grid mode
        self._card_size_menu = QMenu(self)
        self._card_size_menu.setObjectName("MainViewCardSizeMenu")
        self._card_size_action_group = QActionGroup(self)
        self._card_size_action_group.setExclusive(True)
        self._card_size_actions: dict[str, QAction] = {}

        def add_card_size_action(preset: str, label: str) -> None:
            act = QAction(label, self._card_size_menu)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, p=preset: self.set_card_size_preset(p, save=True))
            self._card_size_action_group.addAction(act)
            self._card_size_menu.addAction(act)
            self._card_size_actions[preset] = act

        add_card_size_action("small", "Small cards")
        add_card_size_action("medium", "Medium cards")
        add_card_size_action("large", "Large cards")

        self._btn_card_size = QToolButton(header)
        self._btn_card_size.setObjectName("MainViewCardSizeButton")
        self._btn_card_size.setAutoRaise(True)
        self._btn_card_size.setCursor(Qt.PointingHandCursor)
        self._btn_card_size.setIcon(lucide_icon("sliders-horizontal", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_card_size.setPopupMode(QToolButton.InstantPopup)
        self._btn_card_size.setMenu(self._card_size_menu)
        self._update_card_size_button()

        # Right: ONE primary action button
        self._primary_action = QPushButton("+", header)
        self._primary_action.setObjectName("MainViewPrimaryAction")
        self._primary_action.setCursor(Qt.PointingHandCursor)
        self._primary_action.setMinimumHeight(32)
        self._primary_action.clicked.connect(self.primary_action_requested.emit)

        # Tile view (IconMode) skeleton
        self._tile_model = QStandardItemModel(self)
        self._tile_view = _ClearOnEmptyClickListView()
        self._tile_view.setObjectName("MainViewGrid")
        self._tile_view.setViewMode(QListView.IconMode)
        self._tile_view.setResizeMode(QListView.Adjust)
        self._tile_view.setUniformItemSizes(True)
        # Use explicit gap in grid sizing (prevents "stuck together" rendering).
        self._tile_view.setSpacing(0)
        # Scrollbar only when content overflows (auto-hide when list not clipped).
        self._tile_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._tile_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tile_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tile_view.setSelectionMode(QAbstractItemView.SingleSelection)
        # Lock cards: no drag/drop/reorder.
        self._tile_view.setDragEnabled(False)
        self._tile_view.setAcceptDrops(False)
        self._tile_view.setDropIndicatorShown(False)
        self._tile_view.setDragDropMode(QAbstractItemView.NoDragDrop)
        self._tile_view.setIconSize(QSize(self._THUMBNAIL_SIZE_PX, self._THUMBNAIL_SIZE_PX))
        self._tile_view.setModel(self._tile_model)
        self._tile_view.doubleClicked.connect(self._on_tile_activated)
        self._tile_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tile_view.customContextMenuRequested.connect(self._on_tile_context_menu)
        self._tile_view.verticalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)
        self._tile_view.horizontalScrollBar().valueChanged.connect(self._schedule_thumbnail_prefetch)
        self._tile_view.setMouseTracking(True)
        self._tile_view.viewport().installEventFilter(self)
        # Left/top padding = 24px (right/bottom provided by per-cell gap).
        self._tile_view.setViewportMargins(24, 24, 0, 0)

        self._grid_delegate = _GridCardDelegate(view=self._tile_view)
        self._grid_delegate.set_gap_px(self._GRID_GAP_PX)
        self._tile_view.setItemDelegate(self._grid_delegate)
        self._tile_view.entered.connect(self._grid_delegate.set_hovered_index)
        self._tile_view.viewportEntered.connect(lambda: self._grid_delegate.set_hovered_index(None))
        self._grid_sync_scheduled = False
        self._grid_last: tuple[int, int, int] | None = None  # (cols, card_w, card_h)
        self._schedule_grid_layout_sync()

        self._tile_placeholder = QLabel("")
        self._tile_placeholder.setAlignment(Qt.AlignCenter)
        self._tile_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Empty state background must match content surface (not app_bg).
        self._tile_placeholder.setStyleSheet("background: #121214; color: #A9ABB0;")
        self._tile_placeholder.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tile_placeholder.customContextMenuRequested.connect(
            lambda p: self.root_context_menu_requested.emit(self._tile_placeholder.mapToGlobal(p))
        )

        tile_page = QStackedWidget()
        tile_page.addWidget(self._tile_placeholder)
        tile_page.addWidget(self._tile_view)
        tile_page.setCurrentIndex(0)
        self._tile_page = tile_page

        # List view skeleton
        self._list_model = QStandardItemModel(self)
        self._list_model.setHorizontalHeaderLabels(
            [
                "Name",
                "Type",
                "Departments count",
                "Path",
            ]
        )

        self._list_view = _ClearOnEmptyClickTableView()
        self._list_view.setObjectName("MainViewList")
        self._list_view.setModel(self._list_model)
        self._list_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list_view.setDragEnabled(False)
        self._list_view.setAcceptDrops(False)
        self._list_view.setDropIndicatorShown(False)
        self._list_view.setDragDropMode(QAbstractItemView.NoDragDrop)
        self._list_view.horizontalHeader().setStretchLastSection(True)
        self._list_view.setSortingEnabled(False)
        self._list_view.verticalHeader().setVisible(False)
        self._list_view.verticalHeader().setDefaultSectionSize(28)
        self._list_view.setShowGrid(False)
        self._list_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list_view.doubleClicked.connect(self._on_list_activated)
        self._list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_view.customContextMenuRequested.connect(self._on_list_context_menu)

        self._list_placeholder = QLabel("")
        self._list_placeholder.setAlignment(Qt.AlignCenter)
        self._list_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Empty state background must match content surface (not app_bg).
        self._list_placeholder.setStyleSheet("background: #121214; color: #A9ABB0;")
        self._list_placeholder.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list_placeholder.customContextMenuRequested.connect(
            lambda p: self.root_context_menu_requested.emit(self._list_placeholder.mapToGlobal(p))
        )

        list_page = QStackedWidget()
        list_page.addWidget(self._list_placeholder)
        list_page.addWidget(self._list_view)
        list_page.setCurrentIndex(0)
        self._list_page = list_page

        self._content = QStackedWidget()
        self._content.addWidget(tile_page)  # index 0 = tile
        self._content.addWidget(list_page)  # index 1 = list

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header, 0)
        layout.addWidget(self._content, 1)

        header_layout.addWidget(title_row, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)
        header_layout.addWidget(toggle, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)
        header_layout.addWidget(self._btn_card_size, 0, Qt.AlignVCenter)
        header_layout.addWidget(self._primary_action, 0, Qt.AlignVCenter)

        self._update_type_badge(self._browser_context)

        self._tile_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)
        self._list_view.selectionModel().selectionChanged.connect(self._on_any_selection_changed)

        self._tile_model.rowsInserted.connect(self._update_empty_states)
        self._tile_model.rowsRemoved.connect(self._update_empty_states)
        self._tile_model.modelReset.connect(self._update_empty_states)
        self._list_model.rowsInserted.connect(self._update_empty_states)
        self._list_model.rowsRemoved.connect(self._update_empty_states)
        self._list_model.modelReset.connect(self._update_empty_states)

        # View mode becomes context-aware; MainWindow sets initial browser context.
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())

        self._all_items: list[ViewItem] = []
        # AssetGrid: asset_id -> row index for O(1) lookup; _order = display order of asset_ids.
        self._items: dict[str, int] = {}
        self._order: list[str] = []
        self._selection_driven_by_state = False

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self._tile_view.viewport() and event.type() == QEvent.Resize:
            self._schedule_grid_layout_sync()
        if (
            watched is self._tile_view.viewport()
            and event.type() == QEvent.ToolTip
            and self._view_mode == "tile"
        ):
            pos = event.pos()
            index = self._tile_view.indexAt(pos)
            if index.isValid():
                item = index.data(Qt.UserRole)
                if isinstance(item, ViewItem):
                    cell_rect = self._tile_view.visualRect(index)
                    if cell_rect.contains(pos):
                        active_dep = (getattr(self._grid_delegate, "_active_department", None) or "").strip()
                        has_dept = bool(active_dep)
                        type_rect, dept_rect = _thumb_badge_rects(cell_rect, self._GRID_GAP_PX, has_dept)
                        if type_rect.contains(pos):
                            tt = _TYPE_TOOLTIP_MAP.get((item.type_badge or "").strip().lower()) or _TYPE_TOOLTIP_MAP.get((item.type_badge or "").strip()) or (item.type_badge or "Type")
                            QToolTip.showText(event.globalPos(), tt)
                            event.accept()
                            return True
                        if has_dept and dept_rect and dept_rect.contains(pos):
                            QToolTip.showText(event.globalPos(), active_dep)
                            event.accept()
                            return True
        return super().eventFilter(watched, event)

    def _schedule_grid_layout_sync(self) -> None:
        if getattr(self, "_grid_sync_scheduled", False):
            return
        self._grid_sync_scheduled = True
        QTimer.singleShot(0, self._sync_grid_layout)

    @staticmethod
    def _grid_columns_for_width(px: int) -> int:
        # Tailwind-like breakpoints:
        # <640: 1 col, <1024: 2 col, <1280: 3 col, <1536: 4 col, else: 5 col
        if px < 640:
            return 1
        if px < 1024:
            return 2
        if px < 1280:
            return 3
        if px < 1536:
            return 4
        return 5

    def _sync_grid_layout(self) -> None:
        """
        Responsive grid:
        - width adapts to viewport; column count by breakpoints
        - gap fixed at 24px
        - thumbnail fixed 16:9 inside card
        """
        self._grid_sync_scheduled = False
        try:
            vw = int(self._tile_view.viewport().width())
        except Exception:
            return
        if vw <= 0:
            return

        cols = self._grid_columns_for_width(vw)
        gap = self._GRID_GAP_PX

        # Available width inside the 24px left margin (right margin is 0 by design).
        inner_w = max(1, vw - 24)

        # Approximate: cols cards with (cols-1) gaps, then apply user card-size preset scale.
        base_w = max(240, int((inner_w - (cols - 1) * gap) / cols))
        card_w = max(200, int(base_w * self._card_scale()))
        thumb_h = int(card_w * 9 / 16)
        meta_h = 16 + 20 + 4 + 16 + 16  # p-4 + name + gap + meta + bottom breathing
        card_h = thumb_h + meta_h

        sig = (cols, card_w, card_h)
        if getattr(self, "_grid_last", None) == sig:
            return
        self._grid_last = sig

        # Grid cell includes explicit 24px gap on right/bottom.
        self._tile_view.setGridSize(QSize(card_w + gap, card_h + gap))
        self._grid_delegate.set_card_size(QSize(card_w, card_h))

    def set_context_title(self, title: str) -> None:
        self.update_title(base_title=title, department=self._active_department)

    def set_active_department(
        self,
        department: str | None,
        *,
        label: str | None = None,
        icon_name: str | None = None,
    ) -> None:
        self._active_department = (department or "").strip() or None
        self._active_department_label = (label or "").strip() or None
        self._active_department_icon_name = (icon_name or "").strip() or None
        self.update_title(base_title=self._base_title or self._context_title.text(), department=self._active_department)
        try:
            self._grid_delegate.set_active_department(self._active_department, icon_name=self._active_department_icon_name)
        except Exception:
            pass

    def update_title(self, *, base_title: str, department: str | None) -> None:
        """
        Title formatting:
        - Base title always shown (uppercased, bold)
        - If department active: show badge with icon + department name (bold, BG + border)
        """
        base = (base_title or "").strip()
        self._base_title = base
        base_up = base.upper() if base else ""
        self._context_title.setText(base_up)
        dep = (department or "").strip()
        if not dep:
            self._department_badge.setVisible(False)
            return
        dep_label = (self._active_department_label or "").strip() or dep
        dep_up = dep_label.upper()
        icon_name = (self._active_department_icon_name or "").strip() or "layers"
        icon = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        self._department_icon.setPixmap(icon.pixmap(16, 16))
        self._department_label.setText(dep_up)
        self._department_badge.setVisible(True)

    def _update_type_badge(self, context: str) -> None:
        """Update type badge from browser context (project | asset | shot) selected in sidebar."""
        _context_icon_map = {"project": "layout-dashboard", "asset": "box", "shot": "clapperboard"}
        _context_label_map = {"project": "Project", "asset": "Asset", "shot": "Shot"}
        if context not in _context_label_map:
            return
        icon_name = _context_icon_map.get(context, "box")
        label = _context_label_map[context]
        icon = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        self._type_icon.setPixmap(icon.pixmap(16, 16))
        self._type_label.setText(label.upper())
        self._type_badge.setVisible(True)

    def set_primary_action(self, *, label: str, enabled: bool, tooltip: str | None) -> None:
        # Always visible; enabled and tooltip reflect current context requirements.
        self._primary_action.setText(f"+ {label}".strip())
        self._primary_action.setEnabled(bool(enabled))
        self._primary_action.setToolTip(tooltip or "")

    def set_browser_context(self, context: str) -> None:
        """
        Asset Browser contexts:
        - "project" -> default Grid
        - "asset"   -> default Grid
        - "shot"    -> default List
        Persist view mode per-context when user toggles it.
        """
        if context not in ("project", "asset", "shot"):
            return
        self._browser_context = context
        self._card_size_preset = self._load_card_size_preset()
        self._update_card_size_button()

        title = "Project" if context == "project" else ("Shot" if context == "shot" else "Asset")
        self.set_context_title(title)
        self._update_type_badge(context)

        key = self._settings_key_view_mode()
        saved = self._settings.value(key, "", str)
        if saved in ("tile", "list"):
            self.set_view_mode(saved, save=False)
        else:
            default_mode = "list" if context == "shot" else "tile"
            self.set_view_mode(default_mode, save=False)
        self._schedule_grid_layout_sync()

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager

    def set_project_root(self, path: str | None) -> None:
        # Store only; no validation, no scanning (per requirements).
        self._project_root = path or None
        self._update_empty_states()

    def set_empty_override(self, message: str | None) -> None:
        # Allows higher-level flows (e.g. workspace discovery) to present a neutral empty state.
        self._empty_override = message
        self._update_empty_states()

    def clear(self) -> None:
        # Clear Main View (no filesystem scan in this phase).
        self._tile_view.clearSelection()
        self._list_view.clearSelection()
        self._tile_model.clear()
        self._list_model.clear()
        self._list_model.setHorizontalHeaderLabels(self._list_headers())
        self._apply_list_column_defaults()
        self._all_items = []
        self._items = {}
        self._order = []
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def set_items(self, items: list[ViewItem], preserve_selection_id: str | None = None) -> None:
        # Explicit input only; no hidden filtering here (filter tree lives in Sidebar).
        self._all_items = list(items)
        self._populate_views(items)
        self._order = [str(vi.path) for vi in self._all_items]
        self._rebuild_items_from_order()
        # Restore selection in same update to avoid flicker (Option C, plan_focus_system_v1).
        if preserve_selection_id and preserve_selection_id.strip():
            try:
                self.select_item_by_path(Path(preserve_selection_id))
            except (TypeError, OSError):
                pass

    def _paths_equal(self, a: Path | str, b: Path | str) -> bool:
        """Compare paths for equality (resolved when possible so absolute/relative match)."""
        try:
            pa, pb = Path(a), Path(b)
            if pa == pb:
                return True
            try:
                return pa.resolve() == pb.resolve()
            except OSError:
                return str(pa).strip() == str(pb).strip()
        except (TypeError, OSError):
            return False

    def select_item_by_path(self, path: Path) -> bool:
        """Select the row whose item has the given path; returns True if found and selected."""
        path = Path(path)
        for row in range(self._tile_model.rowCount()):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if isinstance(item, ViewItem) and self._paths_equal(item.path, path):
                self._tile_view.setCurrentIndex(idx)
                self._list_view.setCurrentIndex(self._list_model.index(row, 0))
                return True
        return False

    def invalidate_thumbnail(self, item_root: Path) -> None:
        """
        Force a thumbnail refresh for a specific item.
        Uses ThumbnailManager when set; else legacy cache invalidation.
        """
        root = Path(item_root)
        asset_id = str(root)
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is not None and hasattr(mgr, "invalidate"):
            mgr.invalidate(asset_id)
        else:
            for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
                self._thumb_cache.invalidate_file(root / name)

        # Reset row state and re-request or prefetch.
        try:
            rows = int(self._tile_model.rowCount())
        except Exception:
            rows = 0
        for row in range(rows):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            if item.path != root:
                continue
            std_item = self._tile_model.itemFromIndex(idx)
            if std_item is None:
                continue
            std_item.setData(None, self._THUMB_STATE_ROLE)
            if mgr is not None and hasattr(mgr, "request_thumbnail"):
                pix = mgr.request_thumbnail(asset_id)
                if pix is not None:
                    std_item.setIcon(QIcon(pix))
                    std_item.setData("loaded", self._THUMB_STATE_ROLE)
                else:
                    std_item.setIcon(self._icon_for_item(item))
            else:
                std_item.setIcon(self._icon_for_item(item))

        self._schedule_thumbnail_prefetch()

    def repaint_tiles_for_entity(self, entity_id: str) -> None:
        """Force repaint of tiles so delegate re-evaluates (e.g. pending 'creating' status)."""
        if not entity_id or not str(entity_id).strip():
            return
        self._tile_view.viewport().update()

    def repaint_tile_and_list_views(self) -> None:
        """Force repaint of grid and list so DCC status badges reflect latest AppState after scan."""
        rc = self._tile_model.rowCount()
        if rc > 0:
            tl = self._tile_model.index(0, 0)
            br = self._tile_model.index(rc - 1, 0)
            self._tile_model.dataChanged.emit(tl, br, [Qt.UserRole])
        self._tile_view.viewport().update()
        self._list_view.viewport().update()

    def refresh_thumbnails_for(self, asset_ids: list[str]) -> None:
        """
        Refresh tile thumbnails for the given asset ids (e.g. after thumbnail ready or invalidate).
        Uses ThumbnailManager when set; only updates visible/cached rows.
        """
        if not asset_ids:
            return
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is None or not hasattr(mgr, "request_thumbnail"):
            return
        for asset_id in asset_ids:
            if not asset_id or not str(asset_id).strip():
                continue
            row = self._row_for_item_id(asset_id)
            if row is None:
                continue
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            std_item = self._tile_model.itemFromIndex(idx)
            if std_item is None:
                continue
            pix = mgr.request_thumbnail(asset_id)
            if pix is not None:
                std_item.setIcon(QIcon(pix))
                std_item.setData("loaded", self._THUMB_STATE_ROLE)
            else:
                std_item.setIcon(self._icon_for_item(item))
                std_item.setData(None, self._THUMB_STATE_ROLE)

    def _row_for_item_id(self, item_id: str) -> int | None:
        """Return the model row index for the item with the given path id; path-normalized so updated_ids match."""
        if self._items and item_id in self._items:
            row = self._items[item_id]
            if row < self._tile_model.rowCount():
                return row
        try:
            target = Path(item_id).resolve()
        except Exception:
            return None
        for row in range(self._tile_model.rowCount()):
            idx = self._tile_model.index(row, 0)
            if not idx.isValid():
                continue
            item = idx.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            try:
                if Path(item.path).resolve() == target:
                    return row
            except Exception:
                if item.path == Path(item_id):
                    return row
        return None

    @staticmethod
    def _asset_sort_key(vi: ViewItem) -> tuple:
        """Deterministic sort key for grid order (asset_type, name)."""
        if isinstance(vi.ref, Asset):
            return (vi.ref.asset_type, vi.ref.name)
        return ((vi.type_badge or "").lower(), (vi.name or "").lower())

    def _rebuild_items_from_order(self) -> None:
        """Rebuild _items from _order so _items[asset_id] = row index."""
        self._items = {aid: row for row, aid in enumerate(self._order)}

    def _insert_row_at(self, row: int, item: ViewItem, one_based_index: int) -> None:
        """Insert one row at the given position in both tile and list models (asset/shot context)."""
        tile_entry = QStandardItem(display_name_for_item(item))
        tile_entry.setEditable(False)
        tile_entry.setData(item, Qt.UserRole)
        tile_entry.setData(None, self._THUMB_STATE_ROLE)
        tile_entry.setIcon(self._icon_for_item(item))
        self._tile_model.insertRow(row, tile_entry)
        self._insert_list_row_at(row, item, one_based_index)

    def _insert_list_row_at(self, row: int, item: ViewItem, one_based_index: int) -> None:
        """Insert list row at position (asset/shot context)."""
        mono = monos_font("JetBrains Mono", 11)
        if self._browser_context == "project":
            stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
            status = "WAITING" if not stats else stats.status
            shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
            assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
            updated = "—" if not stats or not stats.last_modified else stats.last_modified
            cells = [
                QStandardItem(str(one_based_index)),
                QStandardItem(display_name_for_item(item)),
                QStandardItem(status),
                QStandardItem(shots),
                QStandardItem(assets),
                QStandardItem(updated),
                QStandardItem(str(item.path)),
            ]
            cells[6].setFont(mono)
        else:
            status = "WAITING"
            if isinstance(item.ref, (Asset, Shot)):
                if any(d.publish_version_count > 0 for d in item.ref.departments):
                    status = "READY"
                elif any(d.work_exists for d in item.ref.departments):
                    status = "PROGRESS"
            cells = [
                QStandardItem(str(one_based_index)),
                QStandardItem(display_name_for_item(item)),
                QStandardItem(status),
                QStandardItem("—"),
                QStandardItem("—"),
                QStandardItem("—"),
            ]
            cells[3].setFont(mono)
        for c in cells:
            c.setEditable(False)
            c.setData(item, Qt.UserRole)
        self._list_model.insertRow(row, cells)

    def set_selection_from_state(self, selection_id: str | None) -> None:
        """Drive selection from AppState only; does not emit selection_id_changed back."""
        self._selection_driven_by_state = True
        try:
            if not selection_id or not selection_id.strip():
                self._tile_view.clearSelection()
                self._list_view.clearSelection()
            else:
                try:
                    found = self.select_item_by_path(Path(selection_id))
                    if not found:
                        self._tile_view.clearSelection()
                        self._list_view.clearSelection()
                except Exception:
                    self._tile_view.clearSelection()
                    self._list_view.clearSelection()
            self._update_empty_states()
            self.valid_selection_changed.emit(self.has_valid_selection())
        finally:
            self._selection_driven_by_state = False

    def apply_assets_diff(
        self,
        added_ids: list[str],
        removed_ids: list[str],
        updated_ids: list[str],
        view_item_resolver: Callable[[str], ViewItem | None],
    ) -> None:
        """Apply diff only: remove, update, add affected items. No full rebuild. Uses _items for O(1) lookup."""
        batch_size = len(added_ids) + len(removed_ids) + len(updated_ids)
        batch_update = batch_size > 8
        if batch_update:
            self._tile_view.setUpdatesEnabled(False)
            self._list_view.setUpdatesEnabled(False)
        try:
            self._apply_assets_diff_impl(
                added_ids, removed_ids, updated_ids, view_item_resolver
            )
        finally:
            if batch_update:
                self._tile_view.setUpdatesEnabled(True)
                self._list_view.setUpdatesEnabled(True)
                self._tile_view.viewport().update()
                self._list_view.viewport().update()
        self._renumber_list_indices()
        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def _apply_assets_diff_impl(
        self,
        added_ids: list[str],
        removed_ids: list[str],
        updated_ids: list[str],
        view_item_resolver: Callable[[str], ViewItem | None],
    ) -> None:
        removed_set = set(removed_ids)

        # 1. Remove: reverse row order so indices stay valid.
        rows_to_remove = []
        for rid in removed_ids:
            r = self._row_for_item_id(rid)
            if r is not None:
                rows_to_remove.append(r)
        rows_to_remove.sort(reverse=True)
        for r in rows_to_remove:
            self._tile_model.removeRow(r)
            self._list_model.removeRow(r)
        self._order = [aid for aid in self._order if aid not in removed_set]
        self._all_items = [vi for vi in self._all_items if str(vi.path) not in removed_set]
        self._rebuild_items_from_order()

        # 2. Update in place: only affected visuals (label, status, thumbnail). Use path-normalized row lookup.
        for uid in updated_ids:
            row = self._row_for_item_id(uid)
            if row is None or row >= self._tile_model.rowCount():
                _dcc_debug_log.debug("apply_assets_diff_impl skip update uid=%r row=%s model_rows=%d _order[:5]=%s", uid, row, self._tile_model.rowCount(), (self._order[:5] if self._order else []))
                continue
            _dcc_debug_log.debug("apply_assets_diff_impl updating row=%d uid=%r", row, uid)
            vi = view_item_resolver(uid)
            if vi is None:
                continue
            tile_item = self._tile_model.item(row, 0)
            if tile_item is not None:
                tile_item.setText(display_name_for_item(vi))
                tile_item.setData(vi, Qt.UserRole)
                tile_item.setData(None, self._THUMB_STATE_ROLE)
                tile_item.setIcon(self._icon_for_item(vi))
            self._set_list_row_for_item(row, vi, row + 1)
            if row < len(self._order):
                self._order[row] = uid
            if row < len(self._all_items):
                self._all_items[row] = vi

        if updated_ids:
            self._rebuild_items_from_order()

        # 3. Add: insert at correct sorted position (AppState order).
        for aid in added_ids:
            vi = view_item_resolver(aid)
            if vi is None:
                continue
            new_key = self._asset_sort_key(vi)
            insert_row = 0
            for i, existing_vi in enumerate(self._all_items):
                if self._asset_sort_key(existing_vi) > new_key:
                    insert_row = i
                    break
            else:
                insert_row = len(self._all_items)
            self._order.insert(insert_row, aid)
            self._all_items.insert(insert_row, vi)
            self._insert_row_at(insert_row, vi, insert_row + 1)
            self._rebuild_items_from_order()

    def apply_assets_diff_from_assets(
        self,
        added: list[Asset],
        removed: list[str],
        updated: list[Asset],
        view_item_builder: Callable[[Asset], ViewItem],
    ) -> None:
        """Apply diff from Asset lists only. Grid does not query AppState; data comes from signal/coordinator."""
        added_ids = [str(a.path) for a in added]
        removed_ids = list(removed)
        updated_ids = [str(a.path) for a in updated]
        _dcc_debug_log.debug("apply_assets_diff_from_assets added_ids=%s removed_ids=%s updated_ids=%s", added_ids, removed_ids, updated_ids)

        def resolver(item_id: str) -> ViewItem | None:
            for a in added:
                if str(a.path) == item_id:
                    return view_item_builder(a)
            for a in updated:
                if str(a.path) == item_id:
                    return view_item_builder(a)
            return None

        self.apply_assets_diff(added_ids, removed_ids, updated_ids, resolver)

    def apply_shots_diff(
        self,
        added_ids: list[str],
        removed_ids: list[str],
        updated_ids: list[str],
        view_item_resolver: Callable[[str], ViewItem | None],
    ) -> None:
        """Same as apply_assets_diff for shots context."""
        self.apply_assets_diff(added_ids, removed_ids, updated_ids, view_item_resolver)

    def _append_row_for_item(self, item: ViewItem, row_index: int) -> None:
        """Append one row to tile and list models (asset/shot context)."""
        tile_entry = QStandardItem(display_name_for_item(item))
        tile_entry.setEditable(False)
        tile_entry.setData(item, Qt.UserRole)
        tile_entry.setData(None, self._THUMB_STATE_ROLE)
        tile_entry.setIcon(self._icon_for_item(item))
        self._tile_model.appendRow(tile_entry)
        self._append_list_row_for_item(item, row_index)

    def _append_list_row_for_item(self, item: ViewItem, row_index: int) -> None:
        mono = monos_font("JetBrains Mono", 11)
        if self._browser_context == "project":
            stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
            status = "WAITING" if not stats else stats.status
            shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
            assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
            updated = "—" if not stats or not stats.last_modified else stats.last_modified
            c_index = QStandardItem(str(row_index))
            c_name = QStandardItem(display_name_for_item(item))
            c_status = QStandardItem(status)
            c_shots = QStandardItem(shots)
            c_assets = QStandardItem(assets)
            c_updated = QStandardItem(updated)
            c_path = QStandardItem(str(item.path))
            c_path.setFont(mono)
            for cell in (c_index, c_name, c_status, c_shots, c_assets, c_updated, c_path):
                cell.setEditable(False)
                cell.setData(item, Qt.UserRole)
            self._list_model.appendRow([c_index, c_name, c_status, c_shots, c_assets, c_updated, c_path])
        else:
            c_index = QStandardItem(str(row_index))
            c_name = QStandardItem(display_name_for_item(item))
            status = "WAITING"
            if isinstance(item.ref, (Asset, Shot)):
                if any(d.publish_version_count > 0 for d in item.ref.departments):
                    status = "READY"
                elif any(d.work_exists for d in item.ref.departments):
                    status = "PROGRESS"
            c_status = QStandardItem(status)
            c_version = QStandardItem("—")
            c_assignee = QStandardItem("—")
            c_updated = QStandardItem("—")
            c_version.setFont(mono)
            for cell in (c_index, c_name, c_status, c_version, c_assignee, c_updated):
                cell.setEditable(False)
                cell.setData(item, Qt.UserRole)
            self._list_model.appendRow([c_index, c_name, c_status, c_version, c_assignee, c_updated])

    def _set_list_row_for_item(self, row: int, item: ViewItem, row_index: int) -> None:
        """Replace list row at index with item (asset/shot context)."""
        if self._browser_context == "project":
            stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
            status = "WAITING" if not stats else stats.status
            shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
            assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
            updated = "—" if not stats or not stats.last_modified else stats.last_modified
            mono = monos_font("JetBrains Mono", 11)
            self._list_model.item(row, 0).setText(str(row_index))
            self._list_model.item(row, 1).setText(display_name_for_item(item))
            self._list_model.item(row, 1).setData(item, Qt.UserRole)
            self._list_model.item(row, 2).setText(status)
            self._list_model.item(row, 3).setText(shots)
            self._list_model.item(row, 4).setText(assets)
            self._list_model.item(row, 5).setText(updated)
            self._list_model.item(row, 6).setText(str(item.path))
            for c in range(7):
                self._list_model.item(row, c).setData(item, Qt.UserRole)
        else:
            status = "WAITING"
            if isinstance(item.ref, (Asset, Shot)):
                if any(d.publish_version_count > 0 for d in item.ref.departments):
                    status = "READY"
                elif any(d.work_exists for d in item.ref.departments):
                    status = "PROGRESS"
            self._list_model.item(row, 0).setText(str(row_index))
            self._list_model.item(row, 1).setText(display_name_for_item(item))
            self._list_model.item(row, 1).setData(item, Qt.UserRole)
            self._list_model.item(row, 2).setText(status)
            for c in range(6):
                self._list_model.item(row, c).setData(item, Qt.UserRole)

    def _renumber_list_indices(self) -> None:
        """Set first column to 1-based row index for all rows."""
        for row in range(self._list_model.rowCount()):
            it = self._list_model.item(row, 0)
            if it is not None:
                it.setText(str(row + 1))

    def _populate_views(self, items: list[ViewItem]) -> None:
        # Populate both Tile and List representations from the same items.
        self._tile_view.clearSelection()
        self._list_view.clearSelection()

        self._tile_model.clear()

        self._list_model.clear()
        self._list_model.setHorizontalHeaderLabels(self._list_headers())
        self._apply_list_column_defaults()

        mono = monos_font("JetBrains Mono", 11)

        for idx, item in enumerate(items, start=1):
            # Tile: Name only; metadata painted via icon and secondary lines (delegate-friendly).
            tile_entry = QStandardItem(display_name_for_item(item))
            tile_entry.setEditable(False)
            tile_entry.setData(item, Qt.UserRole)
            tile_entry.setData(None, self._THUMB_STATE_ROLE)
            tile_entry.setIcon(self._icon_for_item(item))
            self._tile_model.appendRow(tile_entry)

            if self._browser_context == "project":
                stats = item.ref if isinstance(item.ref, ProjectQuickStats) else None
                status = "WAITING" if not stats else stats.status
                shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
                assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
                updated = "—" if not stats or not stats.last_modified else stats.last_modified

                c_index = QStandardItem(str(idx))
                c_name = QStandardItem(display_name_for_item(item))
                c_status = QStandardItem(status)
                c_shots = QStandardItem(shots)
                c_assets = QStandardItem(assets)
                c_updated = QStandardItem(updated)
                c_path = QStandardItem(str(item.path))
                c_path.setFont(mono)

                for cell in (c_index, c_name, c_status, c_shots, c_assets, c_updated, c_path):
                    cell.setEditable(False)
                    cell.setData(item, Qt.UserRole)

                self._list_model.appendRow([c_index, c_name, c_status, c_shots, c_assets, c_updated, c_path])
            else:
                # List (high-density): Index, Name, Status, Version, Assignee, Last Updated
                c_index = QStandardItem(str(idx))
                c_name = QStandardItem(display_name_for_item(item))
                status = "WAITING"
                if isinstance(item.ref, (Asset, Shot)):
                    if any(d.publish_version_count > 0 for d in item.ref.departments):
                        status = "READY"
                    elif any(d.work_exists for d in item.ref.departments):
                        status = "PROGRESS"
                c_status = QStandardItem(status)
                c_version = QStandardItem("—")
                c_assignee = QStandardItem("—")
                c_updated = QStandardItem("—")

                c_version.setFont(mono)

                for cell in (c_index, c_name, c_status, c_version, c_assignee, c_updated):
                    cell.setEditable(False)
                    cell.setData(item, Qt.UserRole)

                self._list_model.appendRow([c_index, c_name, c_status, c_version, c_assignee, c_updated])

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()


    def _settings_key_view_mode(self) -> str:
        return f"{self._SETTINGS_KEY_VIEW_MODE_PREFIX}/{self._browser_context}"

    def _settings_key_card_size(self) -> str:
        return f"{self._SETTINGS_KEY_CARD_SIZE_PREFIX}/{self._browser_context}"

    def _load_card_size_preset(self) -> str:
        raw = (self._settings.value(self._settings_key_card_size(), "medium", str) or "").strip().lower()
        return raw if raw in self._CARD_SIZE_PRESETS else "medium"

    def _card_scale(self) -> float:
        return float(self._CARD_SIZE_PRESETS.get(self._card_size_preset, 0.80))

    def _update_card_size_button(self) -> None:
        # Keep menu check-state + tooltip in sync.
        for k, act in getattr(self, "_card_size_actions", {}).items():
            act.setChecked(bool(k == self._card_size_preset))
        label = self._card_size_preset.capitalize()
        if hasattr(self, "_btn_card_size"):
            self._btn_card_size.setToolTip(f"Card size: {label}")
            self._btn_card_size.setEnabled(self._view_mode == "tile")

    def set_card_size_preset(self, preset: str, *, save: bool = True) -> None:
        preset = (preset or "").strip().lower()
        if preset not in self._CARD_SIZE_PRESETS:
            return
        if self._card_size_preset == preset:
            return
        self._card_size_preset = preset
        if save:
            self._settings.setValue(self._settings_key_card_size(), preset)
        self._update_card_size_button()
        self._schedule_grid_layout_sync()

    def _list_headers(self) -> list[str]:
        if self._browser_context == "project":
            return ["#", "Name", "Status", "Shots", "Assets", "Last Updated", "Path"]
        return ["#", "Name", "Status", "Version", "Assignee", "Last Updated"]

    def _apply_list_column_defaults(self) -> None:
        # Keep layout stable: path is hidden by default in dense views.
        if self._browser_context == "project":
            self._list_view.setColumnHidden(6, True)
        else:
            # Existing behavior: no hidden columns in the new dense list.
            pass

    def _icon_for_item(self, item: ViewItem):
        # Placeholder by kind when no thumbnail: asset/shot/project get type icon, not text.
        if item.kind.value in ("asset", "shot", "project"):
            return self._placeholder_icon_for_kind(item.kind.value)
        return lucide_icon("folder", size=20, color_hex=MONOS_COLORS["text_label"])

    def _placeholder_icon_for_kind(self, kind: str) -> QIcon:
        """Icon placeholder for tile when user has not set thumbnail or image file is missing."""
        size = self._THUMBNAIL_SIZE_PX
        pix = QPixmap(size, size)
        pix.fill(QColor("#2B2D30"))

        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)
            p.fillRect(0, 0, size, size, QColor("#26282B"))
            p.setPen(QPen(QColor("#3A3D41"), 1))
            p.drawRect(0, 0, size - 1, size - 1)

            icon_name = "box" if kind == "asset" else "clapperboard" if kind == "shot" else "layout-dashboard"
            icon = lucide_icon(icon_name, size=128, color_hex="#A9ABB0")
            src = icon.pixmap(128, 128)
            if not src.isNull():
                x = (size - 128) // 2
                y = (size - 128) // 2
                p.drawPixmap(x, y, src)
        finally:
            p.end()

        return QIcon(pix)

    def set_view_mode(self, mode: str, *, save: bool = True) -> None:
        # Persistent per-context (stored in QSettings).
        if mode not in ("tile", "list"):
            return
        self._view_mode = mode
        self._content.setCurrentIndex(1 if mode == "list" else 0)
        if save:
            self._settings.setValue(self._settings_key_view_mode(), mode)

        # Sync toggle UI
        self._btn_grid.setChecked(mode == "tile")
        self._btn_list.setChecked(mode == "list")
        self.view_mode_changed.emit(mode)
        self._update_card_size_button()

        self._update_empty_states()
        self.valid_selection_changed.emit(self.has_valid_selection())
        self._schedule_thumbnail_prefetch()

    def has_valid_selection(self) -> bool:
        if self._view_mode == "list":
            sm = self._list_view.selectionModel()
            return bool(sm and sm.hasSelection())
        sm = self._tile_view.selectionModel()
        return bool(sm and sm.hasSelection())

    def selected_view_item(self) -> ViewItem | None:
        if self._view_mode == "list":
            sm = self._list_view.selectionModel()
            if sm is None:
                return None
            rows = sm.selectedRows()
            if not rows:
                return None
            item = rows[0].data(Qt.UserRole)
            return item if isinstance(item, ViewItem) else None

        sm = self._tile_view.selectionModel()
        if sm is None:
            return None
        indexes = sm.selectedIndexes()
        if not indexes:
            return None
        item = indexes[0].data(Qt.UserRole)
        return item if isinstance(item, ViewItem) else None

    def clear_selection(self) -> None:
        # Inspector "close" action: clear selection only (no rescan, no data mutation).
        try:
            self._tile_view.clearSelection()
        except Exception:
            pass
        try:
            self._list_view.clearSelection()
        except Exception:
            pass
        self.valid_selection_changed.emit(self.has_valid_selection())

    def _on_any_selection_changed(self, *_args) -> None:
        if getattr(self, "_selection_driven_by_state", False):
            return
        item = self.selected_view_item()
        sid = str(item.path) if item is not None else None
        self.selection_id_changed.emit(sid)
        self.valid_selection_changed.emit(self.has_valid_selection())

    def _update_empty_states(self) -> None:
        # Spec: empty states use placeholders; no popup.
        if self._empty_override:
            empty_text = self._empty_override
        elif self._project_root:
            empty_text = "Empty assets / shots"
        else:
            empty_text = "Select a project root to begin"

        self._tile_placeholder.setText(empty_text)
        self._list_placeholder.setText(empty_text)

        tile_has_rows = self._tile_model.rowCount() > 0
        list_has_rows = self._list_model.rowCount() > 0
        self._tile_page.setCurrentIndex(1 if tile_has_rows else 0)
        self._list_page.setCurrentIndex(1 if list_has_rows else 0)

    def _on_tile_activated(self, index) -> None:
        item = index.data(Qt.UserRole)
        if isinstance(item, ViewItem):
            self.item_activated.emit(item)

    def _on_list_activated(self, index) -> None:
        item = index.data(Qt.UserRole)
        if isinstance(item, ViewItem):
            self.item_activated.emit(item)

    def _on_tile_context_menu(self, pos) -> None:
        index = self._tile_view.indexAt(pos)
        if not index.isValid():
            self.root_context_menu_requested.emit(self._tile_view.viewport().mapToGlobal(pos))
            return
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return
        menu = self._build_item_context_menu(item)
        if menu is None:
            return
        chosen = menu.exec(self._tile_view.viewport().mapToGlobal(pos))
        self._dispatch_item_context_action(chosen, item)

    def _on_list_context_menu(self, pos) -> None:
        index = self._list_view.indexAt(pos)
        if not index.isValid():
            self.root_context_menu_requested.emit(self._list_view.viewport().mapToGlobal(pos))
            return
        item = index.data(Qt.UserRole)
        if not isinstance(item, ViewItem):
            return
        menu = self._build_item_context_menu(item)
        if menu is None:
            return
        chosen = menu.exec(self._list_view.viewport().mapToGlobal(pos))
        self._dispatch_item_context_action(chosen, item)

    def _build_item_context_menu(self, item: ViewItem) -> QMenu | None:
        # Candidate 1 + 2 helpers (explicit, silent).
        # Asset / Shot / Department only.
        if item.kind.value not in ("asset", "shot", "department"):
            return None

        menu = QMenu(self)

        open_action = None
        open_with_action = None
        create_new_action = None
        copy_inventory = None
        if item.kind.value in ("asset", "shot"):
            open_action = menu.addAction(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Open")
            open_with_action = menu.addAction(lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"]), "Open With…")
            create_new_action = menu.addAction(lucide_icon("file-plus", size=16, color_hex=MONOS_COLORS["text_label"]), "Create New…")
            menu.addSeparator()
            copy_inventory = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Inventory")
            menu.addSeparator()

        copy_full_path = menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy Full Path")
        open_folder = menu.addAction(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Folder")

        menu.addSeparator()

        delete_action = None
        refresh_action = None
        open_work = None
        open_publish = None

        if item.kind.value in ("asset", "shot"):
            # Set status submenu (user override)
            set_status_menu = QMenu("Set status", menu)
            for label, key in (("Ready", "ready"), ("Progress", "progress"), ("Waiting", "waiting"), ("Blocked", "blocked")):
                act = set_status_menu.addAction(label)
                act.setData(key)
            menu.addMenu(set_status_menu)
            # Existing v1 behavior: Refresh on Asset/Shot items.
            refresh_action = menu.addAction(lucide_icon("download", size=16, color_hex=MONOS_COLORS["text_label"]), "Refresh")
            delete_action = menu.addAction(lucide_icon("x", size=16, color_hex=MONOS_COLORS["text_label"]), "Delete…")
            if delete_action is not None:
                delete_action.setProperty("class", "danger-action")
        elif item.kind.value == "department":
            # Optional (already meaningful in UI): open work/publish folders
            open_work = menu.addAction(lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Work Folder")
            open_publish = menu.addAction(lucide_icon("folder", size=16, color_hex=MONOS_COLORS["text_label"]), "Open Publish Folder")

        # Store action ids on the menu for dispatch without global state
        menu.setProperty("_act_copy_full_path", copy_full_path)
        menu.setProperty("_act_open_folder", open_folder)
        menu.setProperty("_act_copy_inventory", copy_inventory)
        menu.setProperty("_act_open", open_action)
        menu.setProperty("_act_open_with", open_with_action)
        menu.setProperty("_act_create_new", create_new_action)
        menu.setProperty("_act_refresh", refresh_action)
        menu.setProperty("_act_delete", delete_action)
        menu.setProperty("_act_open_work", open_work)
        menu.setProperty("_act_open_publish", open_publish)
        return menu

    def _dispatch_item_context_action(self, chosen, item: ViewItem) -> None:
        if chosen is None:
            return

        # User-set status (submenu action carries data)
        status_val = chosen.data() if hasattr(chosen, "data") else None
        if isinstance(status_val, str) and status_val in ("ready", "progress", "waiting", "blocked"):
            self.status_set_requested.emit(item, status_val)
            return

        # Compare by label text; labels are fixed by spec.
        text = getattr(chosen, "text", lambda: "")()

        if text == "Copy Inventory":
            # v1.2 extension: delegate generation to MainWindow (in-memory index)
            self.copy_inventory_requested.emit(item)
            return
        if text == "Open":
            self.open_requested.emit(item)
            return
        if text == "Open With…":
            self.open_with_requested.emit(item)
            return
        if text == "Create New…":
            self.create_new_requested.emit(item)
            return
        if text == "Copy Full Path":
            path_str, _ = self._resolved_path_and_folder_for_item(item)
            self._copy_full_path(path_str)
            return
        if text == "Open Folder":
            _, folder = self._resolved_path_and_folder_for_item(item)
            self._open_folder(folder)
            return
        if text == "Refresh":
            self.refresh_requested.emit()
            return
        if text == "Delete…":
            self.delete_requested.emit(item)
            return
        if text == "Open Work Folder":
            if hasattr(item, "ref") and item.ref is not None and hasattr(item.ref, "work_path"):
                self._open_folder(Path(item.ref.work_path))
            return
        if text == "Open Publish Folder":
            if hasattr(item, "ref") and item.ref is not None and hasattr(item.ref, "publish_path"):
                self._open_folder(Path(item.ref.publish_path))
            return

    def _resolved_path_and_folder_for_item(self, item: ViewItem) -> tuple[str, Path]:
        """
        Resolve path (for copy) and folder (for open) from item and current department/type.
        When a department is selected and the item (asset/shot) has that department,
        returns the department's work folder; otherwise returns item root path.
        """
        default_path = Path(item.path)
        active_dep = (self._active_department or "").strip() or None
        if not active_dep or item.kind.value not in ("asset", "shot"):
            return (str(default_path), default_path)
        ref = getattr(item, "ref", None)
        if not isinstance(ref, (Asset, Shot)) or not ref.departments:
            return (str(default_path), default_path)
        for d in ref.departments:
            if (d.name or "").strip().casefold() == active_dep.casefold():
                return (str(d.work_path), d.work_path)
        return (str(default_path), default_path)

    def _copy_full_path(self, path_text: str) -> None:
        if not path_text:
            return
        cb = QApplication.clipboard()
        if cb is None:
            return
        cb.setText(path_text)

    def _open_folder(self, folder: Path) -> None:
        # Silent no-op if missing/invalid.
        try:
            if not folder.exists():
                return
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _schedule_thumbnail_prefetch(self) -> None:
        # Lazy loading: only attempt thumbnails for visible tile items.
        if self._thumb_prefetch_scheduled:
            return
        self._thumb_prefetch_scheduled = True
        QTimer.singleShot(0, self._prefetch_visible_thumbnails)

    def _prefetch_visible_thumbnails(self) -> None:
        self._thumb_prefetch_scheduled = False

        # Tile-only integration
        if self._view_mode != "tile":
            return
        if self._tile_model.rowCount() == 0:
            return
        if self._tile_page.currentIndex() != 1:
            return

        viewport = self._tile_view.viewport()
        vp_rect = viewport.rect()

        for row in range(self._tile_model.rowCount()):
            index = self._tile_model.index(row, 0)
            if not index.isValid():
                continue
            if not self._tile_view.visualRect(index).intersects(vp_rect):
                continue

            item = index.data(Qt.UserRole)
            if not isinstance(item, ViewItem):
                continue
            if item.kind.value not in ("asset", "shot", "project"):
                continue

            std_item = self._tile_model.itemFromIndex(index)
            if std_item is None:
                continue

            state = std_item.data(self._THUMB_STATE_ROLE)
            if state in ("loaded", "missing"):
                continue

            asset_id = str(item.path)
            mgr = getattr(self, "_thumbnail_manager", None)
            if mgr is not None and hasattr(mgr, "request_thumbnail"):
                pix = mgr.request_thumbnail(asset_id)
                if pix is not None:
                    std_item.setIcon(QIcon(pix))
                    std_item.setData("loaded", self._THUMB_STATE_ROLE)
                    continue
                thumb_file = self._thumb_cache.resolve_thumbnail_file(item.path)
                if thumb_file is None:
                    std_item.setData("missing", self._THUMB_STATE_ROLE)
                continue

            thumb_file = self._thumb_cache.resolve_thumbnail_file(item.path)
            if thumb_file is None:
                std_item.setData("missing", self._THUMB_STATE_ROLE)
                continue

            pix = self._thumb_cache.load_thumbnail_pixmap(thumb_file)
            if pix is None:
                std_item.setData("missing", self._THUMB_STATE_ROLE)
                continue

            std_item.setIcon(QIcon(pix))
            std_item.setData("loaded", self._THUMB_STATE_ROLE)

