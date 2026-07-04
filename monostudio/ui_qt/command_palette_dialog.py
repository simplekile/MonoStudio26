"""Command palette — Spotlight-style jump search with fuzzy matching."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QSettings, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.nav_quick_view import (
    SLOT_COUNT,
    describe_nav_quick_slot,
    load_nav_quick_slot,
)
from monostudio.ui_qt.palette_stars_store import _norm_path, _norm_root
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog

_PALETTE_WIDTH = 560
_MAX_RESULTS = 25
_MAX_VISIBLE_ROWS = 8
_EMPTY_SUGGESTION_LIMIT = 12
_EMPTY_RECENT_LIMIT = 4
_EMPTY_ENTITY_LIMIT = 6
_ROW_HEIGHT = 60
_ICON_SIZE = 32
_THUMB_RADIUS = 6
_ICON_GAP = 12
_ROW_PAD_H = 10
_ROW_RADIUS = 8
_STARRED_SUBTITLE_PREFIX = "Starred · "
_MARGIN = 12
_LAYOUT_SPACING = 8
_MAX_LIST_HEIGHT = _ROW_HEIGHT * _MAX_VISIBLE_ROWS

from monostudio.ui_qt.sidebar import INTERNAL_CHECK_NAV_ICON

_PAGE_ICONS: dict[str, str] = {
    "Dashboard": "house",
    "Assets": "box",
    "Shots": "clapperboard",
    "Inbox": "inbox",
    "Project Guide": "library",
    "Schedule": "calendar",
    "Internal check": INTERNAL_CHECK_NAV_ICON,
    "Delivery": "send",
    "Trash": "trash-2",
}
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".exr", ".tif", ".tiff", ".bmp"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm"})


def _inbox_icon_for_path(path: str) -> str:
    suffix = ""
    dot = path.rfind(".")
    if dot >= 0:
        suffix = path[dot:].casefold()
    if suffix in _IMAGE_SUFFIXES:
        return "file-image"
    if suffix in _VIDEO_SUFFIXES:
        return "film"
    return "file"


_KIND_FALLBACK_ICONS: dict[str, str] = {
    "page": "layout-dashboard",
    "quick": "pin",
    "project": "folder",
    "entity": "box",
    "inbox": "inbox",
    "guide": "library",
    "action": "zap",
    "recent": "history",
}


def _fuzzy_subsequence_score(query: str, text: str) -> int | None:
    """Score subsequence match (lower = better). None when query chars are not in order."""
    if not query or not text:
        return None
    score = 0
    ti = 0
    last = -1
    for ch in query:
        found = False
        while ti < len(text):
            if text[ti] == ch:
                if last >= 0:
                    gap = ti - last - 1
                    score += gap * 3
                    if gap == 0:
                        score -= 2
                if ti == 0 or text[ti - 1] in " _-./\\":
                    score -= 4
                last = ti
                ti += 1
                found = True
                break
            ti += 1
        if not found:
            return None
    return max(0, score)


@dataclass(frozen=True)
class _PaletteRow:
    title: str
    subtitle: str
    kind: str  # page | quick | entity | project | inbox | action | recent
    payload: dict[str, Any]
    icon_name: str | None = None
    search_text: str = ""
    starred: bool = False


class _CommandPaletteDelegate(QStyledItemDelegate):
    """Two-line row: large icon, title, subtitle (macOS Spotlight style)."""

    def _list_widget(self) -> _PaletteListWidget | None:
        parent = self.parent()
        return parent if isinstance(parent, _PaletteListWidget) else None

    def _resolve_thumb_pixmap(self, row: _PaletteRow) -> QPixmap | None:
        lw = self._list_widget()
        if lw is None:
            return None
        if row.kind in ("inbox", "guide"):
            path_str = row.payload.get("path")
            loader = lw._explorer_loader
            if not path_str or loader is None:
                return None
            from monostudio.ui_qt.thumbnails import is_direct_media_preview_path

            p = Path(str(path_str))
            if not is_direct_media_preview_path(p):
                return None
            pix = loader.get_or_request(p)
            return pix if pix is not None and not pix.isNull() else None

        if row.kind not in ("entity", "recent"):
            return None
        mgr = lw._thumb_manager
        if mgr is None or not hasattr(mgr, "request_thumbnail"):
            return None
        path = row.payload.get("path")
        if not path:
            task = row.payload.get("task")
            path = getattr(task, "item_path", None) if task is not None else None
        if not path:
            return None
        from monostudio.core.models import Asset, Shot

        ref = row.payload.get("pipeline_ref")
        dept = row.payload.get("department") or lw._thumb_department
        dept = dept.strip() if isinstance(dept, str) and dept.strip() else None
        active_dcc = row.payload.get("active_dcc_id")
        active_dcc = active_dcc.strip() if isinstance(active_dcc, str) and active_dcc.strip() else None
        pipeline_ref = ref if isinstance(ref, (Asset, Shot)) else None
        pix = mgr.request_thumbnail(
            str(path),
            dept,
            pipeline_ref=pipeline_ref,
            active_dcc_id=active_dcc,
        )
        return pix if pix is not None and not pix.isNull() else None

    def _paint_row_icon(
        self,
        painter: QPainter,
        *,
        rect: QRect,
        row: _PaletteRow,
        fallback_icon,
        is_active: bool,
    ) -> int:
        """Draw thumb or Lucide icon; return x after icon block."""
        x = rect.left() + _ROW_PAD_H
        cy = rect.center().y()
        ir = QRect(x, cy - _ICON_SIZE // 2, _ICON_SIZE, _ICON_SIZE)
        thumb = self._resolve_thumb_pixmap(row)
        if thumb is not None:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            clip = QPainterPath()
            clip.addRoundedRect(ir, _THUMB_RADIUS, _THUMB_RADIUS)
            painter.setClipPath(clip)
            from monostudio.ui_qt.pipeline_row_paint import list_thumb_cover_paint

            list_thumb_cover_paint(painter, ir, QIcon(thumb), fast=False)
            painter.restore()
        elif fallback_icon is not None and not fallback_icon.isNull():
            fallback_icon.paint(painter, ir, Qt.AlignmentFlag.AlignCenter)
        return x + _ICON_SIZE + _ICON_GAP

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        return QSize(option.rect.width() if option.rect.width() > 0 else _PALETTE_WIDTH, _ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        row = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        if not isinstance(row, _PaletteRow):
            style = opt.widget.style() if opt.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
            return

        r = opt.rect
        is_active = bool(opt.state & QStyle.StateFlag.State_Selected)

        painter.save()
        try:
            highlight = r.adjusted(_ROW_PAD_H // 2, 2, -(_ROW_PAD_H // 2), -2)
            if is_active:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(37, 99, 235, 90))
                painter.drawRoundedRect(highlight, _ROW_RADIUS, _ROW_RADIUS)

            icon_color = MONOS_COLORS["blue_400"] if is_active else MONOS_COLORS["text_label"]
            fallback = _KIND_FALLBACK_ICONS.get(row.kind, "circle")
            icon = lucide_icon((row.icon_name or "").strip() or fallback, size=_ICON_SIZE, color_hex=icon_color)
            cy = r.center().y()
            x = self._paint_row_icon(painter, rect=r, row=row, fallback_icon=icon, is_active=is_active)

            lw = self._list_widget()
            show_stars = bool(lw._show_stars) if lw is not None else False
            text_right = r.right() - _ROW_PAD_H
            star_reserve = 20 if (row.starred and show_stars) else 0
            text_w = max(0, text_right - x - star_reserve)
            title_color = QColor("#fafafa" if is_active else "#e4e4e7")
            subtitle_color = QColor("#a1a1aa" if not is_active else "#d4d4d8")

            title_font = QFont("Inter", 14)
            title_font.setWeight(QFont.Weight.DemiBold)
            subtitle_font = QFont("Inter", 12)
            subtitle_font.setWeight(QFont.Weight.Medium)

            painter.setFont(title_font)
            title_fm = painter.fontMetrics()
            painter.setFont(subtitle_font)
            sub_fm = painter.fontMetrics()
            line_gap = 2
            block_h = title_fm.height() + line_gap + sub_fm.height()
            y_top = cy - block_h // 2

            title_text = title_fm.elidedText(row.title, Qt.TextElideMode.ElideRight, text_w)
            painter.setFont(title_font)
            painter.setPen(title_color)
            painter.drawText(
                x,
                y_top,
                text_w,
                title_fm.height(),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                title_text,
            )

            subtitle = row.subtitle
            if not show_stars and subtitle.startswith(_STARRED_SUBTITLE_PREFIX):
                subtitle = subtitle[len(_STARRED_SUBTITLE_PREFIX) :]
            subtitle_text = sub_fm.elidedText(subtitle, Qt.TextElideMode.ElideRight, text_w)
            painter.setFont(subtitle_font)
            painter.setPen(subtitle_color)
            painter.drawText(
                x,
                y_top + title_fm.height() + line_gap,
                text_w,
                sub_fm.height(),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                subtitle_text,
            )
            if row.starred and show_stars:
                star_color = "#fde68a" if is_active else "#fbbf24"
                star = lucide_icon("star", size=16, color_hex=star_color)
                if not star.isNull():
                    sx = r.right() - _ROW_PAD_H - 16
                    star.paint(painter, QRect(sx, cy - 8, 16, 16), Qt.AlignmentFlag.AlignCenter)
        finally:
            painter.restore()


class _SpotlightBackdrop(QWidget):
    """Fullscreen dim layer; left-click outside the panel closes the palette."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel: QWidget | None = None
        self._dismiss = None

    def set_panel(self, panel: QWidget) -> None:
        self._panel = panel

    def set_dismiss(self, dismiss) -> None:
        self._dismiss = dismiss

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._panel is not None
            and self._dismiss is not None
            and not self._panel.geometry().contains(event.pos())
        ):
            self._dismiss()
            event.accept()
            return
        super().mousePressEvent(event)


class _PaletteListWidget(QListWidget):
    """Single active row — mouse move updates selection (no stuck Qt hover state)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._thumb_manager = None
        self._thumb_department: str | None = None
        self._explorer_loader = None
        self._show_stars = True

    def _set_active_row(self, row: int) -> None:
        if row < 0 or row >= self.count():
            return
        if self.currentRow() == row:
            return
        self.setCurrentRow(row)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self.indexAt(event.pos())
        if idx.isValid():
            self._set_active_row(idx.row())
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        idx = self.indexAt(event.pos())
        if idx.isValid():
            self._set_active_row(idx.row())
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.viewport().update()
        super().leaveEvent(event)


class CommandPaletteDialog(MonosDialog):
    """Spotlight-style jump search: fuzzy filter, recent suggestions, scrollable results."""

    page_selected = Signal(str)
    quick_slot_selected = Signal(object)
    entity_selected = Signal(object)
    project_selected = Signal(str)
    inbox_selected = Signal(str)
    guide_selected = Signal(str)
    action_selected = Signal(str)
    recent_task_selected = Signal(object)

    def __init__(
        self,
        *,
        settings: QSettings,
        entities: list[dict[str, Any]] | None = None,
        projects: list[dict[str, Any]] | None = None,
        inbox_items: list[dict[str, Any]] | None = None,
        guide_items: list[dict[str, Any]] | None = None,
        recent_tasks: list[dict[str, Any]] | None = None,
        current_context: str = "",
        project_root: str | None = None,
        starred_keys: frozenset[tuple[str, str, str]] | None = None,
        star_order: list[tuple[str, str, str]] | None = None,
        thumbnail_manager=None,
        thumb_department: str | None = None,
        app_state=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Go to…")
        self.set_host_dim_overlay_enabled(False)
        self._settings = settings
        self._app_state = app_state
        self._thumb_updates_connected = False
        self._current_context = (current_context or "").strip()
        self._project_root = _norm_root(project_root)
        self._starred_keys = starred_keys or frozenset()
        self._star_order = list(star_order or [])
        dept = (thumb_department or "").strip()
        self._thumb_department = dept or None
        self._show_stars = True
        self._rows = self._build_rows(
            entities or [],
            projects or [],
            inbox_items or [],
            guide_items or [],
            recent_tasks or [],
        )

        self._backdrop = _SpotlightBackdrop(self)
        self._panel = QWidget(self._backdrop)
        self._panel.setObjectName("CommandPalettePanel")
        self._panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._backdrop.set_panel(self._panel)
        self._backdrop.set_dismiss(self.reject)

        root = QVBoxLayout(self._panel)
        root.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        root.setSpacing(_LAYOUT_SPACING)

        self._search = QLineEdit(self._panel)
        self._search.setObjectName("CommandPaletteSearch")
        self._search.setPlaceholderText("Search assets, shots, inbox, project guide…")
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)

        self._empty = QLabel("No matches", self._panel)
        self._empty.setObjectName("CommandPaletteEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        root.addWidget(self._empty)

        from monostudio.ui_qt.explorer_thumbnail_loader import ExplorerThumbnailLoader

        self._list = _PaletteListWidget(self._panel)
        self._list._thumb_manager = thumbnail_manager
        self._list._thumb_department = self._thumb_department
        self._explorer_loader = ExplorerThumbnailLoader(self)
        self._explorer_loader.register_view(self._list.viewport())
        self._list._explorer_loader = self._explorer_loader
        self._list.setObjectName("CommandPaletteList")
        self._list.setItemDelegate(_CommandPaletteDelegate(self._list))
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.hide()
        root.addWidget(self._list)

        self._search.installEventFilter(self)

        self._hint = QLabel("↑↓ navigate · Enter open · Esc close", self._panel)
        self._hint.setObjectName("DialogHint")
        root.addWidget(self._hint)

        self._list.itemClicked.connect(self._on_item_activated)
        self._search.returnPressed.connect(self._activate_current)
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)
        self._apply_filter("")

    def _top_level_host(self) -> QWidget | None:
        w: QWidget | None = self.parentWidget()
        top: QWidget | None = w
        while w is not None:
            top = w
            w = w.parentWidget()
        return top

    def _sync_spotlight_layout(self) -> None:
        self._backdrop.setGeometry(self.rect())
        pw = self._panel.width()
        ph = self._panel.height()
        x = max(0, (self.width() - pw) // 2)
        y = max(48, min(self.height() // 5, self.height() - ph - 48))
        self._panel.setGeometry(x, y, pw, ph)
        self._backdrop.lower()
        self._panel.raise_()

    def showEvent(self, event) -> None:  # noqa: N802
        host = self._top_level_host()
        if host is not None:
            self.setGeometry(host.frameGeometry())
        self._connect_thumb_updates()
        QDialog.showEvent(self, event)
        self._sync_spotlight_layout()
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)

    def _connect_thumb_updates(self) -> None:
        if self._thumb_updates_connected or self._app_state is None:
            return
        self._app_state.thumbnailsChanged.connect(self._on_palette_thumbs_changed)
        self._thumb_updates_connected = True

    def _disconnect_thumb_updates(self) -> None:
        if not self._thumb_updates_connected or self._app_state is None:
            return
        try:
            self._app_state.thumbnailsChanged.disconnect(self._on_palette_thumbs_changed)
        except (RuntimeError, TypeError):
            pass
        self._thumb_updates_connected = False

    def _on_palette_thumbs_changed(self, _asset_ids=None) -> None:
        if self._list.isVisible():
            self._list.viewport().update()

    def _shutdown_explorer_loader(self) -> None:
        loader = getattr(self, "_explorer_loader", None)
        if loader is not None:
            loader.shutdown()

    def reject(self) -> None:
        self._disconnect_thumb_updates()
        self._shutdown_explorer_loader()
        super().reject()

    def accept(self) -> None:
        self._disconnect_thumb_updates()
        self._shutdown_explorer_loader()
        super().accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        QDialog.resizeEvent(self, event)
        self._sync_spotlight_layout()

    def paintEvent(self, event) -> None:  # noqa: N802
        QDialog.paintEvent(self, event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_current()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._list.count() > 0:
                row = self._list.currentRow()
                if row < 0:
                    row = 0
                if key == Qt.Key.Key_Down:
                    self._list.setCurrentRow(min(row + 1, self._list.count() - 1))
                else:
                    self._list.setCurrentRow(max(row - 1, 0))
                self._list.viewport().update()
                return True
        return super().eventFilter(watched, event)

    def _star_key_for_row(self, row: _PaletteRow) -> tuple[str, str, str] | None:
        if row.kind not in ("entity", "inbox"):
            return None
        path = (row.payload.get("path") or "").strip()
        if not path:
            return None
        root = _norm_root(row.payload.get("project_root") or self._project_root)
        if not root:
            return None
        return (root, row.kind, _norm_path(path))

    def _apply_star_meta(self, row: _PaletteRow) -> _PaletteRow:
        key = self._star_key_for_row(row)
        if key is None or key not in self._starred_keys:
            return row
        subtitle = row.subtitle
        if not subtitle.casefold().startswith("starred"):
            subtitle = f"{_STARRED_SUBTITLE_PREFIX}{subtitle}" if subtitle else "Starred"
        search_text = row.search_text
        if "starred" not in search_text:
            search_text = f"{search_text} starred".strip()
        return replace(row, starred=True, subtitle=subtitle, search_text=search_text)

    def _build_rows(
        self,
        entities: list[dict[str, Any]],
        projects: list[dict[str, Any]],
        inbox_items: list[dict[str, Any]],
        guide_items: list[dict[str, Any]],
        recent_tasks: list[dict[str, Any]],
    ) -> list[_PaletteRow]:
        rows: list[_PaletteRow] = []
        for slot in range(1, SLOT_COUNT + 1):
            payload = load_nav_quick_slot(self._settings, slot)
            if payload is None:
                continue
            summary = describe_nav_quick_slot(payload)
            qctx = (payload.get("context") or "").strip()
            rows.append(
                _PaletteRow(
                    title=f"Quick view {slot}",
                    subtitle=summary,
                    kind="quick",
                    payload=payload,
                    icon_name=_PAGE_ICONS.get(qctx, "pin"),
                    search_text=f"quick view slot {slot} {summary} {qctx}".casefold(),
                )
            )
        for task in recent_tasks:
            title = (task.get("title") or "").strip()
            if not title:
                continue
            subtitle = (task.get("subtitle") or "Recent task").strip()
            search_text = (task.get("search_text") or f"{title} {subtitle} recent").casefold()
            icon_name = task.get("icon_name")
            payload = task.get("payload")
            if not isinstance(payload, dict):
                payload = {"task": payload}
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="recent",
                    payload=payload,
                    icon_name=icon_name.strip() if isinstance(icon_name, str) and icon_name.strip() else "history",
                    search_text=search_text,
                )
            )
        for proj in projects:
            title = (proj.get("title") or "").strip()
            path = (proj.get("path") or "").strip()
            if not title or not path:
                continue
            subtitle = (proj.get("subtitle") or "Project").strip()
            search_text = (proj.get("search_text") or f"{title} {subtitle} {path}").casefold()
            icon_name = proj.get("icon_name")
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="project",
                    payload={"path": path},
                    icon_name=icon_name.strip() if isinstance(icon_name, str) and icon_name.strip() else "folder",
                    search_text=search_text,
                )
            )
        for ent in entities:
            title = (ent.get("title") or "").strip()
            path = (ent.get("path") or "").strip()
            ctx = (ent.get("context") or "").strip()
            if not title or not path or ctx not in ("Assets", "Shots"):
                continue
            subtitle = (ent.get("subtitle") or ctx).strip()
            search_text = (ent.get("search_text") or f"{title} {subtitle} {path}").casefold()
            icon_name = ent.get("icon_name")
            if not isinstance(icon_name, str) or not icon_name.strip():
                icon_name = "clapperboard" if ctx == "Shots" else "box"
            type_id = ent.get("type_id")
            payload: dict[str, Any] = {"context": ctx, "path": path, "project_root": self._project_root}
            if isinstance(type_id, str) and type_id.strip():
                payload["type_id"] = type_id.strip()
            dept = ent.get("department")
            if isinstance(dept, str) and dept.strip():
                payload["department"] = dept.strip()
            ref = ent.get("pipeline_ref")
            if ref is not None:
                payload["pipeline_ref"] = ref
            rows.append(
                self._apply_star_meta(
                    _PaletteRow(
                        title=title,
                        subtitle=subtitle,
                        kind="entity",
                        payload=payload,
                        icon_name=icon_name.strip(),
                        search_text=search_text,
                    )
                )
            )
        for item in inbox_items:
            title = (item.get("title") or "").strip()
            path = (item.get("path") or "").strip()
            if not title or not path:
                continue
            subtitle = (item.get("subtitle") or "Inbox").strip()
            search_text = (item.get("search_text") or f"{title} {subtitle} {path}").casefold()
            icon_name = item.get("icon_name")
            if not isinstance(icon_name, str) or not icon_name.strip():
                icon_name = _inbox_icon_for_path(path)
            rows.append(
                self._apply_star_meta(
                    _PaletteRow(
                        title=title,
                        subtitle=subtitle,
                        kind="inbox",
                        payload={"path": path, "project_root": self._project_root},
                        icon_name=icon_name.strip(),
                        search_text=search_text,
                    )
                )
            )
        for item in guide_items:
            title = (item.get("title") or "").strip()
            path = (item.get("path") or "").strip()
            if not title or not path:
                continue
            subtitle = (item.get("subtitle") or "Project Guide").strip()
            search_text = (item.get("search_text") or f"{title} {subtitle} {path}").casefold()
            icon_name = item.get("icon_name")
            if not isinstance(icon_name, str) or not icon_name.strip():
                icon_name = _inbox_icon_for_path(path)
            payload: dict[str, Any] = {"path": path, "project_root": self._project_root}
            dept = item.get("department")
            if isinstance(dept, str) and dept.strip():
                payload["department"] = dept.strip()
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="guide",
                    payload=payload,
                    icon_name=icon_name.strip(),
                    search_text=search_text,
                )
            )
        return rows

    def _row_haystacks(self, row: _PaletteRow) -> list[str]:
        bits = [row.title.casefold(), row.subtitle.casefold(), row.search_text.casefold()]
        path = str(row.payload.get("path", "")).casefold()
        if path:
            bits.append(path)
        return [b for b in bits if b]

    def _token_matches(self, token: str, row: _PaletteRow, haystacks: list[str]) -> bool:
        if any(token in hay for hay in haystacks):
            return True
        combined = " ".join(haystacks)
        if token in combined:
            return True
        title = row.title.casefold()
        compact_title = title.replace("_", "").replace("-", "")
        compact_token = token.replace("_", "").replace("-", "")
        for text in (title, compact_title, combined.replace("_", "")):
            if _fuzzy_subsequence_score(token, text) is not None:
                return True
            if compact_token and _fuzzy_subsequence_score(compact_token, text) is not None:
                return True
        return False

    def _match_tier(self, row: _PaletteRow, q: str) -> tuple[int, int, str]:
        """Lower tier = better match. (tier, kind_bias, title)."""
        title = row.title.casefold()
        subtitle = row.subtitle.casefold()
        tokens = [t for t in q.split() if t]
        combined = " ".join(self._row_haystacks(row))

        if title == q:
            tier = 0
        elif title.startswith(q):
            tier = 1
        elif q in title:
            tier = 2
        elif tokens and all(t in title for t in tokens):
            tier = 3
        elif q in subtitle:
            tier = 4
        else:
            fz = _fuzzy_subsequence_score(q.replace(" ", ""), title.replace("_", ""))
            if fz is not None:
                tier = 5 + min(fz, 20)
            elif tokens and all(t in combined for t in tokens):
                tier = 30
            else:
                tier = 40

        kind_bias = {
            "entity": 0,
            "inbox": 1,
            "guide": 1,
            "project": 2,
            "page": 3,
            "quick": 4,
            "action": 5,
            "recent": 6,
        }.get(row.kind, 9)
        ctx = self._current_context
        if ctx and row.kind == "entity" and (row.payload.get("context") or "").strip() == ctx:
            kind_bias = max(0, kind_bias - 2)
        if ctx == "Inbox" and row.kind == "inbox":
            kind_bias = max(0, kind_bias - 2)
        if ctx == "Project Guide" and row.kind == "guide":
            kind_bias = max(0, kind_bias - 2)
        # Recent/actions/pages only float up on strong name matches.
        if row.kind == "recent" and tier > 2:
            kind_bias += 8
        elif row.kind in ("action", "page") and tier > 4:
            kind_bias += 5

        return (tier, kind_bias, title)

    def _row_matches(self, row: _PaletteRow, q: str) -> bool:
        tokens = [t for t in q.split() if t]
        if not tokens:
            return True
        haystacks = self._row_haystacks(row)
        return all(self._token_matches(token, row, haystacks) for token in tokens)

    def _row_rank(self, row: _PaletteRow, q: str) -> tuple:
        tier = self._match_tier(row, q)
        star_rank = 0 if (row.starred and self._show_stars) else 1
        return (star_rank, tier[0], tier[1], tier[2])

    def _default_rows(self) -> list[_PaletteRow]:
        """Suggestions when the search box is empty."""
        out: list[_PaletteRow] = []
        seen: set[tuple[str, str]] = set()

        def add(row: _PaletteRow) -> None:
            key = (row.kind, row.title.casefold(), str(row.payload.get("path", "")))
            if key in seen:
                return
            seen.add(key)
            out.append(row)

        if self._star_order:
            star_map = {
                key: row
                for row in self._rows
                if row.starred and (key := self._star_key_for_row(row)) is not None
            }
            for key in self._star_order:
                row = star_map.get(key)
                if row is not None:
                    add(row)
                if len(out) >= _EMPTY_SUGGESTION_LIMIT:
                    return out[:_EMPTY_SUGGESTION_LIMIT]
        else:
            for row in self._rows:
                if row.starred:
                    add(row)
                if len(out) >= _EMPTY_SUGGESTION_LIMIT:
                    return out[:_EMPTY_SUGGESTION_LIMIT]

        recent_n = 0
        for row in self._rows:
            if row.kind == "recent":
                add(row)
                recent_n += 1
                if recent_n >= _EMPTY_RECENT_LIMIT:
                    break

        entity_n = 0
        for row in self._rows:
            if row.kind == "entity":
                add(row)
                entity_n += 1
                if entity_n >= _EMPTY_ENTITY_LIMIT:
                    break

        if self._current_context == "Inbox":
            inbox_n = 0
            for row in self._rows:
                if row.kind == "inbox":
                    add(row)
                    inbox_n += 1
                    if inbox_n >= 6 or len(out) >= _EMPTY_SUGGESTION_LIMIT:
                        break

        if self._current_context == "Project Guide":
            guide_n = 0
            for row in self._rows:
                if row.kind == "guide":
                    add(row)
                    guide_n += 1
                    if guide_n >= 6 or len(out) >= _EMPTY_SUGGESTION_LIMIT:
                        break

        for row in self._rows:
            if row.kind == "quick":
                add(row)
            if len(out) >= _EMPTY_SUGGESTION_LIMIT:
                return out[:_EMPTY_SUGGESTION_LIMIT]

        return out[:_EMPTY_SUGGESTION_LIMIT]

    def _filtered_rows(self, q: str) -> list[_PaletteRow]:
        q = (q or "").strip().casefold()
        if not q:
            return self._default_rows()
        matched = [row for row in self._rows if self._row_matches(row, q)]
        matched.sort(key=lambda row: self._row_rank(row, q))
        return matched[:_MAX_RESULTS]

    def _prefetch_row_thumbnails(self, rows: list[_PaletteRow]) -> None:
        mgr = self._list._thumb_manager
        loader = self._list._explorer_loader
        from monostudio.core.models import Asset, Shot
        from monostudio.ui_qt.thumbnails import is_direct_media_preview_path

        for row in rows:
            if row.kind in ("inbox", "guide"):
                path_str = row.payload.get("path")
                if loader is not None and path_str:
                    p = Path(str(path_str))
                    if is_direct_media_preview_path(p):
                        loader.request(p)
                continue
            if row.kind not in ("entity", "recent") or mgr is None:
                continue
            path = row.payload.get("path")
            if not path:
                task = row.payload.get("task")
                path = getattr(task, "item_path", None) if task is not None else None
            if not path:
                continue
            ref = row.payload.get("pipeline_ref")
            dept = row.payload.get("department") or self._thumb_department
            dept = dept.strip() if isinstance(dept, str) and dept.strip() else None
            active_dcc = row.payload.get("active_dcc_id")
            active_dcc = active_dcc.strip() if isinstance(active_dcc, str) and active_dcc.strip() else None
            pipeline_ref = ref if isinstance(ref, (Asset, Shot)) else None
            mgr.request_thumbnail(
                str(path),
                dept,
                pipeline_ref=pipeline_ref,
                active_dcc_id=active_dcc,
            )

    def _populate_list(self, rows: list[_PaletteRow]) -> None:
        self._list.blockSignals(True)
        try:
            self._list.clear()
            for row in rows:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setSizeHint(QSize(_PALETTE_WIDTH - _MARGIN * 2, _ROW_HEIGHT))
                self._list.addItem(item)
            if self._list.count() > 0:
                self._list.setCurrentRow(0)
        finally:
            self._list.blockSignals(False)
        self._prefetch_row_thumbnails(rows)
        self._list.viewport().update()

    def _sync_layout(self, *, query: str, results: list[_PaletteRow]) -> None:
        q = (query or "").strip()
        has_query = bool(q)
        count = len(results)
        showing_defaults = not has_query and count > 0

        self._list.setVisible(count > 0)
        self._empty.setVisible(has_query and count == 0)
        self._hint.setVisible(count > 0)

        if count > 0:
            list_h = min(count * _ROW_HEIGHT, _MAX_LIST_HEIGHT)
            self._list.setFixedHeight(list_h)
        else:
            self._list.setFixedHeight(0)

        if showing_defaults and not has_query:
            self._hint.setText("Starred first · type to search all")
        else:
            self._hint.setText("↑↓ navigate · Click or Enter open · Esc close")

        search_h = max(self._search.sizeHint().height(), 36)
        footer_h = self._hint.sizeHint().height() if count > 0 else 0
        empty_h = self._empty.sizeHint().height() + 8 if has_query and count == 0 else 0
        list_block = min(count * _ROW_HEIGHT, _MAX_LIST_HEIGHT) if count > 0 else 0
        total_h = (
            _MARGIN * 2
            + search_h
            + (empty_h if empty_h else 0)
            + (_LAYOUT_SPACING if count > 0 or empty_h else 0)
            + list_block
            + (_LAYOUT_SPACING if footer_h else 0)
            + footer_h
        )
        self._panel.setFixedSize(_PALETTE_WIDTH, max(total_h, _MARGIN * 2 + search_h + 8))
        if self.isVisible():
            self._sync_spotlight_layout()

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip()
        self._show_stars = not bool(q)
        self._list._show_stars = self._show_stars
        results = self._filtered_rows(q)
        self._populate_list(results)
        self._sync_layout(query=q, results=results)

    def _activate_current(self) -> None:
        if self._list.count() <= 0:
            return
        item = self._list.currentItem()
        if item is not None:
            self._on_item_activated(item)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._search.hasFocus() or self._list.hasFocus():
                self._activate_current()
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, _PaletteRow):
            return
        if row.kind == "page":
            ctx = (row.payload.get("context") or "").strip()
            if ctx:
                self.page_selected.emit(ctx)
        elif row.kind == "quick":
            self.quick_slot_selected.emit(row.payload)
        elif row.kind == "entity":
            self.entity_selected.emit(dict(row.payload))
        elif row.kind == "project":
            path = (row.payload.get("path") or "").strip()
            if path:
                self.project_selected.emit(path)
        elif row.kind == "inbox":
            path = (row.payload.get("path") or "").strip()
            if path:
                self.inbox_selected.emit(path)
        elif row.kind == "guide":
            path = (row.payload.get("path") or "").strip()
            if path:
                self.guide_selected.emit(path)
        elif row.kind == "action":
            action_id = (row.payload.get("id") or "").strip()
            if action_id:
                self.action_selected.emit(action_id)
        elif row.kind == "recent":
            task = row.payload.get("task")
            if task is not None:
                self.recent_task_selected.emit(task)
        self.accept()
