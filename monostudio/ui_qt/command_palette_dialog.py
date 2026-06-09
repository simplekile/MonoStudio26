"""Ctrl+` command palette — Spotlight-style jump search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt, QSettings, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.nav_quick_view import (
    SLOT_COUNT,
    VALID_NAV_CONTEXTS,
    describe_nav_quick_slot,
    load_nav_quick_slot,
)
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog

_PALETTE_WIDTH = 560
_MAX_RESULTS = 5
_ROW_HEIGHT = 60
_ICON_SIZE = 32
_ICON_GAP = 12
_ROW_PAD_H = 10
_ROW_RADIUS = 8
_MARGIN = 12
_LAYOUT_SPACING = 8

_PAGE_ICONS: dict[str, str] = {
    "Dashboard": "house",
    "Assets": "box",
    "Shots": "clapperboard",
    "Inbox": "inbox",
    "Project Guide": "folder-open",
    "Schedule": "calendar",
    "Outbox": "send",
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
}


@dataclass(frozen=True)
class _PaletteRow:
    title: str
    subtitle: str
    kind: str  # page | quick | entity | project | inbox
    payload: dict[str, Any]
    icon_name: str | None = None
    search_text: str = ""


class _CommandPaletteDelegate(QStyledItemDelegate):
    """Two-line row: large icon, title, subtitle (macOS Spotlight style)."""

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
        is_selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        try:
            highlight = r.adjusted(_ROW_PAD_H // 2, 2, -(_ROW_PAD_H // 2), -2)
            if is_selected:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(37, 99, 235, 90))
                painter.drawRoundedRect(highlight, _ROW_RADIUS, _ROW_RADIUS)
            elif is_hovered:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 14))
                painter.drawRoundedRect(highlight, _ROW_RADIUS, _ROW_RADIUS)

            icon_color = MONOS_COLORS["blue_400"] if is_selected else MONOS_COLORS["text_label"]
            fallback = _KIND_FALLBACK_ICONS.get(row.kind, "circle")
            icon = lucide_icon((row.icon_name or "").strip() or fallback, size=_ICON_SIZE, color_hex=icon_color)
            x = r.left() + _ROW_PAD_H
            cy = r.center().y()
            if not icon.isNull():
                ir = QRect(x, cy - _ICON_SIZE // 2, _ICON_SIZE, _ICON_SIZE)
                icon.paint(painter, ir, Qt.AlignmentFlag.AlignCenter)
            x += _ICON_SIZE + _ICON_GAP

            text_right = r.right() - _ROW_PAD_H
            text_w = max(0, text_right - x)
            title_color = QColor("#fafafa" if is_selected else "#e4e4e7")
            subtitle_color = QColor("#a1a1aa" if not is_selected else "#d4d4d8")

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

            subtitle_text = sub_fm.elidedText(row.subtitle, Qt.TextElideMode.ElideRight, text_w)
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
        finally:
            painter.restore()


class CommandPaletteDialog(MonosDialog):
    """Spotlight-style jump search: type to filter, max 5 results, dynamic height."""

    page_selected = Signal(str)
    quick_slot_selected = Signal(object)
    entity_selected = Signal(object)
    project_selected = Signal(str)
    inbox_selected = Signal(str)

    def __init__(
        self,
        *,
        settings: QSettings,
        entities: list[dict[str, Any]] | None = None,
        projects: list[dict[str, Any]] | None = None,
        inbox_items: list[dict[str, Any]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Go to…")
        self._settings = settings
        self._rows = self._build_rows(entities or [], projects or [], inbox_items or [])

        root = QVBoxLayout(self)
        root.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        root.setSpacing(_LAYOUT_SPACING)

        self._search = QLineEdit(self)
        self._search.setObjectName("CommandPaletteSearch")
        self._search.setPlaceholderText("Search pages, projects, assets, inbox…")
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)

        self._empty = QLabel("No matches", self)
        self._empty.setObjectName("CommandPaletteEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        root.addWidget(self._empty)

        self._list = QListWidget(self)
        self._list.setObjectName("CommandPaletteList")
        self._list.setItemDelegate(_CommandPaletteDelegate(self._list))
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.hide()
        root.addWidget(self._list)

        self._hint = QLabel("↑↓ navigate · Enter open · Esc close", self)
        self._hint.setObjectName("DialogHint")
        root.addWidget(self._hint)

        self._list.itemActivated.connect(self._on_item_activated)
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)
        self._apply_filter("")

    def _build_rows(
        self,
        entities: list[dict[str, Any]],
        projects: list[dict[str, Any]],
        inbox_items: list[dict[str, Any]],
    ) -> list[_PaletteRow]:
        rows: list[_PaletteRow] = []
        for ctx in (
            "Dashboard",
            "Assets",
            "Shots",
            "Inbox",
            "Project Guide",
            "Schedule",
            "Outbox",
            "Trash",
        ):
            if ctx in VALID_NAV_CONTEXTS:
                rows.append(
                    _PaletteRow(
                        title=ctx,
                        subtitle="Page",
                        kind="page",
                        payload={"context": ctx},
                        icon_name=_PAGE_ICONS.get(ctx),
                    )
                )
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
            payload: dict[str, Any] = {"context": ctx, "path": path}
            if isinstance(type_id, str) and type_id.strip():
                payload["type_id"] = type_id.strip()
            rows.append(
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="entity",
                    payload=payload,
                    icon_name=icon_name.strip(),
                    search_text=search_text,
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
                _PaletteRow(
                    title=title,
                    subtitle=subtitle,
                    kind="inbox",
                    payload={"path": path},
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

    def _row_matches(self, row: _PaletteRow, q: str) -> bool:
        tokens = [t for t in q.split() if t]
        if not tokens:
            return False
        haystacks = self._row_haystacks(row)
        return all(any(token in hay for hay in haystacks) for token in tokens)

    def _row_rank(self, row: _PaletteRow, q: str) -> tuple[int, str]:
        title = row.title.casefold()
        subtitle = row.subtitle.casefold()
        if title == q:
            return (0, title)
        if title.startswith(q):
            return (1, title)
        if q in title:
            return (2, title)
        if q in subtitle:
            return (3, title)
        return (4, title)

    def _filtered_rows(self, q: str) -> list[_PaletteRow]:
        q = (q or "").strip().casefold()
        if not q:
            return []
        matched = [row for row in self._rows if self._row_matches(row, q)]
        matched.sort(key=lambda row: self._row_rank(row, q))
        return matched[:_MAX_RESULTS]

    def _populate_list(self, rows: list[_PaletteRow]) -> None:
        self._list.clear()
        for row in rows:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setSizeHint(QSize(_PALETTE_WIDTH - _MARGIN * 2, _ROW_HEIGHT))
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _sync_layout(self, *, query: str, results: list[_PaletteRow]) -> None:
        q = (query or "").strip()
        has_query = bool(q)
        count = len(results)

        self._list.setVisible(count > 0)
        self._empty.setVisible(has_query and count == 0)
        self._hint.setVisible(has_query)

        if count > 0:
            list_h = count * _ROW_HEIGHT
            self._list.setFixedHeight(list_h)
        else:
            self._list.setFixedHeight(0)

        search_h = max(self._search.sizeHint().height(), 36)
        footer_h = self._hint.sizeHint().height() if has_query else 0
        empty_h = self._empty.sizeHint().height() + 8 if has_query and count == 0 else 0
        total_h = (
            _MARGIN * 2
            + search_h
            + (empty_h if empty_h else 0)
            + (_LAYOUT_SPACING if count > 0 or empty_h else 0)
            + (count * _ROW_HEIGHT if count > 0 else 0)
            + (_LAYOUT_SPACING if has_query and footer_h else 0)
            + footer_h
        )
        self.setFixedSize(_PALETTE_WIDTH, max(total_h, _MARGIN * 2 + search_h + 8))

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip()
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
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._list.count() > 0:
            row = self._list.currentRow()
            if row < 0:
                row = 0
            if key == Qt.Key.Key_Down:
                self._list.setCurrentRow(min(row + 1, self._list.count() - 1))
            else:
                self._list.setCurrentRow(max(row - 1, 0))
            event.accept()
            return
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
        self.accept()
