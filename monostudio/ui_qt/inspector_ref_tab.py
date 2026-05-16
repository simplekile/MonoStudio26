"""Inspector tab: browse entity reference/ concept folders."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QSize,
    QThreadPool,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.entity_folders import (
    EntitySpecialFolderId,
    count_special_folder_files,
    ensure_entity_special_folder,
    is_ref_preview_image,
    list_special_folder_files,
)
from monostudio.core.models import Asset, Shot
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu, monos_font
from monostudio.ui_qt.thumbnails import ThumbnailCache, decode_ref_preview_qimage_worker

# Fixed square thumbs; QListView IconMode wraps columns like Main View grid.
_REF_GRID_GAP = 12
_REF_THUMB_SIZE = 120
_REF_GRID_SYNC_DEBOUNCE_MS = 0  # singleShot(0) like MainView._schedule_grid_layout_sync
_REF_CONTENT_H_MARGIN = 24
_REF_MAX_GRID_COLUMNS = 6
_REF_THUMB_DECODE_SIZE = 256
_REF_THUMB_PREFETCH_CHUNK = 8
_REF_THUMB_PREFETCH_ALL_MAX_ROWS = 32


def _ref_path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def ref_thumb_row_visible_in_scroll(
    list_view: QListView,
    scroll_area: QScrollArea | None,
    row: int,
    model: QAbstractListModel,
) -> bool:
    """True when the list row intersects the Ref tab scroll viewport (for lazy thumb load)."""
    if scroll_area is None or model.rowCount() <= 0:
        return True
    idx = model.index(row, 0)
    if not idx.isValid():
        return False
    item_rect = list_view.visualRect(idx)
    if not item_rect.isValid():
        return False
    top_left = list_view.viewport().mapTo(scroll_area.viewport(), item_rect.topLeft())
    bottom_right = list_view.viewport().mapTo(scroll_area.viewport(), item_rect.bottomRight())
    item_in_scroll = QRect(top_left, bottom_right).normalized()
    return item_in_scroll.intersects(scroll_area.viewport().rect())


def _ref_grid_columns_for_width(available_w: int) -> int:
    if available_w <= 0:
        return 1
    unit = _REF_THUMB_SIZE + _REF_GRID_GAP
    cols = max(1, (int(available_w) + _REF_GRID_GAP) // unit)
    return min(cols, _REF_MAX_GRID_COLUMNS)


def _reveal_in_explorer(path: Path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["explorer", "/select,", str(path.resolve())],
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}),
            )
            return
    except (OSError, ValueError):
        pass
    parent = path.parent if path.is_file() else path
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(parent)))


@dataclass(frozen=True)
class _RefThumbEntry:
    path: Path
    placeholder_ext: str = ""


def _paint_ref_thumb_cell(
    p: QPainter,
    rect: QRect,
    *,
    pixmap: QPixmap | None,
    placeholder_ext: str,
    selected: bool,
    hovered: bool,
) -> None:
    g = _REF_GRID_GAP
    outer = rect.adjusted(0, 0, -g, -g)
    if outer.width() <= 0 or outer.height() <= 0:
        return
    radius = 8
    border_px = 2 if selected else 1
    if selected:
        fill = QColor(MONOS_COLORS.get("card_bg", "#121214"))
    elif hovered:
        fill = QColor(MONOS_COLORS.get("card_hover", "#1f1f23"))
    else:
        fill = QColor(MONOS_COLORS.get("card_bg", "#121214"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(outer, radius, radius)

    inner = outer.adjusted(border_px, border_px, -border_px, -border_px)
    inner_radius = max(0, radius - border_px)
    clip = QPainterPath()
    clip.addRoundedRect(inner, inner_radius, inner_radius)
    p.setClipPath(clip)
    thumb = inner
    if pixmap is not None and not pixmap.isNull():
        scaled = pixmap.scaled(
            thumb.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = max(0, (scaled.width() - thumb.width()) // 2)
        sy = max(0, (scaled.height() - thumb.height()) // 2)
        crop = scaled.copy(QRect(QPoint(sx, sy), thumb.size()))
        p.drawPixmap(thumb, crop)
    elif placeholder_ext:
        p.setPen(QColor(MONOS_COLORS.get("text_muted", "#71717a")))
        p.setFont(monos_font("JetBrains Mono", 10))
        p.drawText(thumb, Qt.AlignmentFlag.AlignCenter, placeholder_ext.upper())
    p.setClipping(False)

    if selected:
        border_color = QColor("#60a5fa")
    elif hovered:
        border_color = QColor("#3f3f46")
    else:
        border_color = QColor("#27272a")
    inset = border_px // 2
    border_rect = outer.adjusted(inset, inset, -inset, -inset)
    p.setPen(QPen(border_color, border_px))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(border_rect, radius, radius)


class _RefThumbDecodeBridge(QObject):
    """Cross-thread delivery of decoded QImage (converted to QPixmap on GUI thread)."""

    decoded = Signal(str, int, object)  # path key, generation, QImage | None


class _RefThumbDecodeRunnable(QRunnable):
    def __init__(self, path_key: str, size_px: int, gen: int, bridge: _RefThumbDecodeBridge) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path_key = path_key
        self._size_px = size_px
        self._gen = gen
        self._bridge = bridge

    def run(self) -> None:
        result = decode_ref_preview_qimage_worker(self._path_key, self._size_px)
        image: QImage | None = result[1] if result else None
        self._bridge.decoded.emit(self._path_key, self._gen, image)


class _RefThumbListModel(QAbstractListModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[_RefThumbEntry] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._entries):
            return None
        if role == Qt.ItemDataRole.UserRole:
            return self._entries[index.row()]
        return None

    def reset_entries(self, entries: list[_RefThumbEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entry_at(self, row: int) -> _RefThumbEntry | None:
        if row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]


class _RefThumbDelegate(QStyledItemDelegate):
    def __init__(self, section: "_InspectorRefSection") -> None:
        super().__init__(section._list)
        self._section = section

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        entry = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, _RefThumbEntry):
            super().paint(painter, option, index)
            return
        pm = None
        key = _ref_path_key(entry.path)
        if is_ref_preview_image(entry.path):
            pm = self._section._thumb_pixmaps.get(key)
            if (
                (pm is None or pm.isNull())
                and key not in self._section._thumb_pending
                and key not in self._section._thumb_missing
            ):
                self._section._request_thumb_for_path(entry.path, self._section._thumb_prefetch_gen)
        selected = self._section._selected_path == entry.path
        hovered = self._section._hovered_row == index.row()
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            _paint_ref_thumb_cell(
                painter,
                option.rect,
                pixmap=pm,
                placeholder_ext=entry.placeholder_ext if pm is None or pm.isNull() else "",
                selected=selected,
                hovered=hovered,
            )
        finally:
            painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # type: ignore[override]
        cell = _REF_THUMB_SIZE + _REF_GRID_GAP
        return QSize(cell, cell)


class _RefSectionHeader(QWidget):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _InspectorRefSection(QWidget):
    open_folder_clicked = Signal(str)

    def __init__(
        self,
        section_id: EntitySpecialFolderId,
        *,
        title: str,
        icon_name: str,
        empty_hint: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorRefSection")
        self._section_id = section_id
        self._folder_path: Path | None = None
        self._files: list[Path] = []
        self._expanded = True
        self._thumb_cache = ThumbnailCache(size_px=_REF_THUMB_DECODE_SIZE)
        self._thumb_bridge = _RefThumbDecodeBridge(self)
        self._thumb_bridge.decoded.connect(self._on_thumb_decoded)
        self._thumb_pixmaps: dict[str, QPixmap] = {}
        self._thumb_pending: set[str] = set()
        self._thumb_missing: set[str] = set()
        self._thumb_prefetch_gen = 0
        self._thumb_prefetch_scheduled = False
        self._selected_path: Path | None = None
        self._hovered_row: int | None = None
        self._grid_last: tuple[int, int] | None = None  # (cols, list_height)
        self._grid_sync_scheduled = False

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        hdr = _RefSectionHeader(self)
        hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        hdr_l.setSpacing(6)
        ic = QLabel(hdr)
        ic.setPixmap(lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_meta"]).pixmap(16, 16))
        title_lbl = QLabel(title, hdr)
        title_lbl.setObjectName("InspectorSectionTitle")
        title_lbl.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        title_lbl.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        hdr_l.addWidget(ic, 0)
        hdr_l.addWidget(title_lbl, 0)
        hdr_l.addStretch(1)
        self._count_pill = QLabel("empty", hdr)
        self._count_pill.setObjectName("InspectorRefCountPill")
        self._count_pill.setStyleSheet(
            "background: rgba(255,255,255,0.06); color: #71717a; border-radius: 4px; padding: 2px 8px;"
        )
        self._count_pill.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        self._chevron = QLabel(hdr)
        self._chevron.setPixmap(
            lucide_icon("chevron-down", size=16, color_hex=MONOS_COLORS["text_meta"]).pixmap(16, 16)
        )
        hdr_l.addWidget(self._count_pill, 0)
        hdr_l.addWidget(self._chevron, 0)
        hdr.clicked.connect(self._on_header_clicked)
        lay.addWidget(hdr, 0)

        self._body = QWidget(self)
        body_l = QVBoxLayout(self._body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(8)

        self._empty_block = QLabel(empty_hint, self._body)
        self._empty_block.setWordWrap(True)
        self._empty_block.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_block.setFont(monos_font("Inter", 12))
        self._empty_block.setStyleSheet(f"color: {MONOS_COLORS.get('text_muted', '#71717a')}; padding: 8px 0;")
        self._empty_block.setVisible(False)
        body_l.addWidget(self._empty_block, 0)

        self._model = _RefThumbListModel(self)
        self._list = QListView(self._body)
        self._list.setObjectName("InspectorRefGrid")
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setUniformItemSizes(True)
        self._list.setSpacing(0)
        self._list.setFlow(QListView.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._list.setMouseTracking(True)
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setModel(self._model)
        self._delegate = _RefThumbDelegate(self)
        self._list.setItemDelegate(self._delegate)
        self._list.clicked.connect(self._on_list_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list.entered.connect(self._on_list_entered)
        self._list.viewportEntered.connect(lambda: self._set_hovered_row(None))
        body_l.addWidget(self._list, 0)
        self._ref_scroll_area: QScrollArea | None = None

        self._other_link = QToolButton(self._body)
        self._other_link.setAutoRaise(True)
        self._other_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._other_link.setStyleSheet(f"color: {MONOS_COLORS.get('text_muted', '#71717a')}; text-align: left;")
        self._other_link.setVisible(False)
        self._other_link.clicked.connect(lambda: self.open_folder_clicked.emit(self._section_id))
        body_l.addWidget(self._other_link, 0)

        lay.addWidget(self._body, 0)

    def _set_hovered_row(self, row: int | None) -> None:
        if self._hovered_row == row:
            return
        prev = self._hovered_row
        self._hovered_row = row
        rc = self._model.rowCount()
        if prev is not None and 0 <= prev < rc:
            ix = self._model.index(prev, 0)
            self._list.viewport().update(self._list.visualRect(ix))
        if row is not None and 0 <= row < rc:
            ix = self._model.index(row, 0)
            self._list.viewport().update(self._list.visualRect(ix))

    def _on_list_entered(self, index: QModelIndex) -> None:
        self._set_hovered_row(index.row() if index.isValid() else None)

    def _on_header_clicked(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        icon = "chevron-down" if expanded else "chevron-right"
        self._chevron.setPixmap(lucide_icon(icon, size=16, color_hex=MONOS_COLORS["text_meta"]).pixmap(16, 16))
        self._body.setVisible(expanded)
        if expanded and not self._files:
            self._empty_block.setVisible(True)
            self._list.setVisible(False)
        elif expanded:
            self._empty_block.setVisible(False)
            self._list.setVisible(self._model.rowCount() > 0)
        if expanded:
            self._schedule_grid_sync()
            self._schedule_thumb_prefetch(force=True)

    def bind_ref_scroll_area(self, scroll: QScrollArea) -> None:
        self._ref_scroll_area = scroll

    def _ref_scroll(self) -> QScrollArea | None:
        if self._ref_scroll_area is not None:
            return self._ref_scroll_area
        host = self.parentWidget()
        while host is not None:
            if isinstance(host, QScrollArea) and host.objectName() == "InspectorRefScrollArea":
                self._ref_scroll_area = host
                return host
            host = host.parentWidget()
        return None

    def _schedule_thumb_prefetch(self, *, force: bool = False) -> None:
        if force:
            self._thumb_prefetch_scheduled = False
        if self._thumb_prefetch_scheduled or not self._expanded:
            return
        self._thumb_prefetch_scheduled = True
        QTimer.singleShot(0, self._prefetch_visible_thumbnails)

    def _prefetch_visible_thumbnails(self) -> None:
        self._thumb_prefetch_scheduled = False
        self._prefetch_thumbs_chunk(self._thumb_prefetch_gen, 0)

    def _prefetch_thumbs_chunk(self, gen: int, start_row: int) -> None:
        if gen != self._thumb_prefetch_gen or not self._expanded:
            return
        rc = self._model.rowCount()
        chunk = max(1, _REF_THUMB_PREFETCH_CHUNK)
        end = min(start_row + chunk, rc)
        scroll = self._ref_scroll()
        prefetch_all = rc <= _REF_THUMB_PREFETCH_ALL_MAX_ROWS
        for row in range(start_row, end):
            if not prefetch_all and not ref_thumb_row_visible_in_scroll(self._list, scroll, row, self._model):
                continue
            entry = self._model.entry_at(row)
            if entry is None or not is_ref_preview_image(entry.path):
                continue
            self._request_thumb_for_path(entry.path, gen)
        if end < rc:
            QTimer.singleShot(0, lambda g=gen, nxt=end: self._prefetch_thumbs_chunk(g, nxt))

    def _request_thumb_for_path(self, path: Path, gen: int) -> None:
        key = _ref_path_key(path)
        if gen != self._thumb_prefetch_gen:
            return
        if key in self._thumb_pixmaps or key in self._thumb_pending:
            return
        pm = self._thumb_cache.peek_thumbnail_pixmap(path)
        if pm is not None and not pm.isNull():
            self._thumb_pixmaps[key] = pm
            self._repaint_row_for_path(path)
            return
        self._thumb_pending.add(key)
        QThreadPool.globalInstance().start(
            _RefThumbDecodeRunnable(key, _REF_THUMB_DECODE_SIZE, gen, self._thumb_bridge)
        )

    def _on_thumb_decoded(self, path_key: str, gen: int, image: object) -> None:
        if gen != self._thumb_prefetch_gen:
            return
        self._thumb_pending.discard(path_key)
        path = Path(path_key)
        pm: QPixmap | None = None
        if isinstance(image, QImage) and not image.isNull():
            pm = self._thumb_cache.adopt_decoded_thumbnail(path, image)
        if pm is None or pm.isNull():
            pm = self._thumb_cache.load_thumbnail_pixmap(path)
        if pm is not None and not pm.isNull():
            self._thumb_pixmaps[path_key] = pm
            self._thumb_missing.discard(path_key)
        else:
            self._thumb_missing.add(path_key)
        self._repaint_row_for_path(path)

    def _repaint_row_for_path(self, path: Path | str) -> None:
        target = _ref_path_key(path) if isinstance(path, Path) else path
        for row in range(self._model.rowCount()):
            ent = self._model.entry_at(row)
            if ent is not None and _ref_path_key(ent.path) == target:
                ix = self._model.index(row, 0)
                self._list.viewport().update(self._list.visualRect(ix))
                break

    def _reset_thumb_load_state(self, keep_paths: set[str] | None = None) -> None:
        self._thumb_prefetch_gen += 1
        self._thumb_pending.clear()
        if keep_paths is None:
            self._thumb_pixmaps.clear()
            self._thumb_missing.clear()
        else:
            for key in list(self._thumb_pixmaps.keys()):
                if key not in keep_paths:
                    del self._thumb_pixmaps[key]
            self._thumb_missing = {k for k in self._thumb_missing if k not in keep_paths}

    def set_folder_path(self, path: Path | None) -> None:
        self._folder_path = path

    def apply_scan(self, files: list[Path], *, expanded: bool | None = None) -> int:
        self._files = list(files)
        n = len(files)
        self._count_pill.setText("empty" if n == 0 else (f"{n} file" if n == 1 else f"{n} files"))
        if expanded is not None:
            self.set_expanded(expanded)
        elif n == 0:
            self.set_expanded(False)
        else:
            self.set_expanded(True)
        self._rebuild_entries()
        return n

    def _content_inner_width(self) -> int:
        host = self.parentWidget()
        while host is not None:
            if host.objectName() == "InspectorRefContent":
                return max(0, host.width())
            host = host.parentWidget()
        return max(0, self.width())

    def _schedule_grid_sync(self) -> None:
        if self._grid_sync_scheduled:
            return
        self._grid_sync_scheduled = True
        QTimer.singleShot(_REF_GRID_SYNC_DEBOUNCE_MS, self._sync_list_layout)

    def sync_list_layout(self) -> None:
        """Main View pattern: fixed thumb size, column count from width, setGridSize."""
        self._schedule_grid_sync()

    def _sync_list_layout(self) -> None:
        self._grid_sync_scheduled = False
        inner_w = max(1, self._content_inner_width())
        gap = _REF_GRID_GAP
        cell_w = _REF_THUMB_SIZE + gap
        cols = _ref_grid_columns_for_width(inner_w)
        row_count = self._model.rowCount()
        rows = max(1, (row_count + cols - 1) // cols) if row_count else 0
        list_h = rows * cell_w if row_count else 0
        sig = (cols, list_h)
        if self._grid_last == sig:
            return
        self._grid_last = sig
        self._list.setGridSize(QSize(cell_w, cell_w))
        self._list.setFixedHeight(list_h)
        self._list.setVisible(self._expanded and row_count > 0)
        if row_count > 0:
            self._schedule_thumb_prefetch()

    def _rebuild_entries(self) -> None:
        self._other_link.setVisible(False)
        self._empty_block.setVisible(self._expanded and not self._files)
        entries: list[_RefThumbEntry] = []
        if self._files and self._expanded:
            images = [p for p in self._files if is_ref_preview_image(p)]
            others = [p for p in self._files if not is_ref_preview_image(p)]
            keep_keys = {_ref_path_key(p) for p in images}
            self._reset_thumb_load_state(keep_keys)
            for path in images:
                key = _ref_path_key(path)
                pm = self._thumb_pixmaps.get(key) or self._thumb_cache.peek_thumbnail_pixmap(path)
                if pm is not None and not pm.isNull():
                    self._thumb_pixmaps[key] = pm
                entries.append(
                    _RefThumbEntry(
                        path=path,
                        placeholder_ext="" if key in self._thumb_pixmaps else path.suffix,
                    )
                )
            if others:
                self._other_link.setText(
                    f"+{len(others)} other file{'s' if len(others) != 1 else ''} — open folder"
                )
                self._other_link.setVisible(True)
        self._model.reset_entries(entries)
        self._grid_last = None
        self._schedule_grid_sync()
        self._schedule_thumb_prefetch(force=True)
        if self._selected_path is not None:
            for row in range(self._model.rowCount()):
                ent = self._model.entry_at(row)
                if ent is not None and ent.path == self._selected_path:
                    self._list.setCurrentIndex(self._model.index(row, 0))
                    break
            else:
                self._selected_path = None

    def _on_list_clicked(self, index: QModelIndex) -> None:
        entry = self._model.entry_at(index.row())
        if entry is None:
            return
        self._selected_path = entry.path
        self._list.viewport().update()
        self._open_file(entry.path)

    def _open_file(self, path: Path) -> None:
        try:
            if path.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        except (OSError, ValueError):
            pass

    def _on_list_context_menu(self, pos) -> None:
        index = self._list.indexAt(pos)
        entry = self._model.entry_at(index.row()) if index.isValid() else None
        if entry is None:
            return
        menu = MonosMenu(parent=self)
        menu.addAction(lucide_icon("file", size=16, color_hex=MONOS_COLORS["text_label"]), "Open")
        menu.addAction(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]), "Reveal in Explorer")
        menu.addAction(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]), "Copy file path")
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        text = chosen.text()
        if text == "Open":
            self._open_file(entry.path)
        elif text == "Reveal in Explorer":
            _reveal_in_explorer(entry.path)
        elif text == "Copy file path":
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(str(entry.path.resolve()))


class InspectorRefTab(QWidget):
    """Tab body: CONCEPT (top) + REFERENCE (bottom)."""

    open_folder_requested = Signal(object)
    total_file_count_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorRefTab")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._entity: Asset | Shot | None = None
        self._paths: dict[EntitySpecialFolderId, Path | None] = {}
        self._scanned_key: str | None = None
        self._visible_once = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._ref_scroll = QScrollArea(self)
        self._ref_scroll.setObjectName("InspectorRefScrollArea")
        self._ref_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._ref_scroll.setWidgetResizable(True)
        self._ref_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._ref_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._ref_scroll.viewport().setAutoFillBackground(False)

        content = QWidget()
        content.setObjectName("InspectorRefContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._ref_content = content
        self._content_lay = QVBoxLayout(content)
        self._content_lay.setContentsMargins(12, 12, 12, 12)
        self._content_lay.setSpacing(16)

        self._concept = _InspectorRefSection(
            "concept",
            title="CONCEPT",
            icon_name="lightbulb",
            empty_hint="No concept art yet",
            parent=content,
        )
        self._reference = _InspectorRefSection(
            "reference",
            title="REFERENCE",
            icon_name="eye",
            empty_hint="No reference images yet",
            parent=content,
        )
        for sec in (self._concept, self._reference):
            sec.open_folder_clicked.connect(self._on_open_folder)
            sec.bind_ref_scroll_area(self._ref_scroll)

        self._content_lay.addWidget(self._concept, 0)
        self._content_lay.addWidget(self._reference, 0)
        self._content_lay.addStretch(1)
        self._ref_scroll.setWidget(content)
        self._ref_scroll.viewport().installEventFilter(self)
        sb = self._ref_scroll.verticalScrollBar()
        sb.valueChanged.connect(self._on_ref_scroll_moved)
        outer.addWidget(self._ref_scroll, 1)

    def _on_ref_scroll_moved(self, _value: int) -> None:
        self._concept._schedule_thumb_prefetch()
        self._reference._schedule_thumb_prefetch()

    def _content_inner_width(self) -> int:
        w = self._ref_content.width()
        if w > 0:
            return w
        vw = self._ref_scroll.viewport().width()
        return max(0, vw - _REF_CONTENT_H_MARGIN) if vw > _REF_CONTENT_H_MARGIN else max(0, vw)

    def _sync_all_sections(self) -> None:
        self._concept.sync_list_layout()
        self._reference.sync_list_layout()

    def _schedule_sync_all(self) -> None:
        QTimer.singleShot(0, self._sync_all_sections)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self._ref_scroll.viewport():
            if event.type() == QEvent.Type.Resize:
                self._schedule_sync_all()
            elif event.type() == QEvent.Type.Show:
                self._concept._schedule_thumb_prefetch(force=True)
                self._reference._schedule_thumb_prefetch(force=True)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._schedule_sync_all()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._schedule_sync_all()

    def clear(self) -> None:
        """No asset/shot selected — empty both sections."""
        self._entity = None
        self._paths = {}
        self._scanned_key = None
        for sec in (self._concept, self._reference):
            sec.set_folder_path(None)
            sec.apply_scan([], expanded=False)
        self.total_file_count_changed.emit(0)

    def set_entity_paths(self, entity: Asset | Shot | None, paths: dict[EntitySpecialFolderId, Path | None]) -> None:
        if entity is None:
            self.clear()
            return
        self._entity = entity
        self._paths = dict(paths)
        key = str(entity.path)
        if key != self._scanned_key:
            self._scanned_key = None
        for sec, fid in ((self._concept, "concept"), (self._reference, "reference")):
            sec.set_folder_path(paths.get(fid))

    def notify_tab_visible(self) -> None:
        self._visible_once = True
        self._scan_if_needed()
        self._schedule_sync_all()
        self._concept._schedule_thumb_prefetch(force=True)
        self._reference._schedule_thumb_prefetch(force=True)

    def refresh_from_disk(self) -> None:
        self._scanned_key = None
        for sec in (self._concept, self._reference):
            for path in sec._files:
                sec._thumb_cache.invalidate_file(path)
        self._scan_if_needed()

    def total_preview_file_count(self) -> int:
        total = 0
        for fid in ("concept", "reference"):
            p = self._paths.get(fid)
            if p is not None:
                total += count_special_folder_files(p)
        return total

    def _scan_if_needed(self) -> None:
        if self._entity is None:
            self.clear()
            return
        key = str(self._entity.path)
        if self._scanned_key == key:
            return
        self._scanned_key = key
        total = 0
        for sec, fid in ((self._concept, "concept"), (self._reference, "reference")):
            folder = self._paths.get(fid)
            files = list_special_folder_files(folder) if folder is not None else []
            total += sec.apply_scan(files)
        self.total_file_count_changed.emit(total)

    def _on_open_folder(self, folder_id: str) -> None:
        path = self._paths.get(folder_id)  # type: ignore[arg-type]
        if path is None and self._entity is not None:
            path = self._entity.path / folder_id
        if path is None:
            return
        if ensure_entity_special_folder(path):
            self.open_folder_requested.emit(path.resolve())
            self._paths[folder_id] = path.resolve()  # type: ignore[index]
            QTimer.singleShot(0, self.refresh_from_disk)
