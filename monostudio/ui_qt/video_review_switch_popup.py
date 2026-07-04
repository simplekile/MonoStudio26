"""Scrollable thumbnail popup for switching review videos / sources."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QFont, QImage, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.sequence_preview import list_sequence_frames
from monostudio.core.video_media import extract_video_frame_png_bytes, is_video_path
from monostudio.ui_qt.popup_position import max_popup_height_for_anchor, position_popup_near_anchor
from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage
from monostudio.ui_qt.style import monos_font

_SWITCH_POPUP_W = 360
_SWITCH_ROW_H = 56
_SWITCH_THUMB_W = 88
_SWITCH_THUMB_H = 50
_SWITCH_VISIBLE_ROWS = 8
_SWITCH_POPUP_PAD = 4
_THUMB_CACHE_MAX = 96

_THUMB_PIXMAP_CACHE: OrderedDict[str, QPixmap] = OrderedDict()
_BLANK_THUMB: QPixmap | None = None


@dataclass
class VideoReviewSwitchItem:
    label: str
    subtitle: str = ""
    checked: bool = False
    thumb_path: Path | None = None
    sequence_folder: Path | None = None
    on_activate: Callable[[], None] | None = None


def _blank_thumb() -> QPixmap:
    global _BLANK_THUMB
    if _BLANK_THUMB is None or _BLANK_THUMB.isNull():
        pix = QPixmap(_SWITCH_THUMB_W, _SWITCH_THUMB_H)
        pix.fill(Qt.GlobalColor.transparent)
        _BLANK_THUMB = pix
    return _BLANK_THUMB


def _scale_thumb(pix: QPixmap, w: int, h: int) -> QPixmap:
    if pix.isNull():
        return _blank_thumb()
    return pix.scaled(
        w,
        h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    ).copy(0, 0, w, h)


def _thumb_cache_key(item: VideoReviewSwitchItem) -> str | None:
    if item.thumb_path is not None:
        try:
            return f"v:{item.thumb_path.resolve()}".casefold()
        except OSError:
            return f"v:{item.thumb_path}".casefold()
    if item.sequence_folder is not None:
        try:
            return f"s:{item.sequence_folder.resolve()}".casefold()
        except OSError:
            return f"s:{item.sequence_folder}".casefold()
    return None


def _store_thumb_cache(key: str, pix: QPixmap) -> None:
    if key in _THUMB_PIXMAP_CACHE:
        _THUMB_PIXMAP_CACHE.move_to_end(key)
        return
    _THUMB_PIXMAP_CACHE[key] = pix
    while len(_THUMB_PIXMAP_CACHE) > _THUMB_CACHE_MAX:
        _THUMB_PIXMAP_CACHE.popitem(last=False)


def _cached_thumb_pixmap(key: str | None) -> QPixmap | None:
    if not key:
        return None
    pix = _THUMB_PIXMAP_CACHE.get(key)
    if pix is not None and not pix.isNull():
        _THUMB_PIXMAP_CACHE.move_to_end(key)
        return pix
    return None


def _decode_thumb_image(item: VideoReviewSwitchItem) -> QImage | None:
    if item.thumb_path is not None and is_video_path(item.thumb_path):
        try:
            data = extract_video_frame_png_bytes(item.thumb_path, 0.0, width=176, keyframe_aligned=True)
            if data:
                img = QImage()
                if img.loadFromData(data):
                    return img
        except Exception:
            pass
    folder = item.sequence_folder
    if folder is not None and folder.is_dir():
        try:
            frames = list_sequence_frames(folder)
            if frames:
                img = load_preview_frame_qimage(frames[0], max_side=176)
                if img is not None and not img.isNull():
                    return img
        except Exception:
            pass
    if item.thumb_path is not None and item.thumb_path.is_file():
        try:
            img = QImage(str(item.thumb_path))
            if not img.isNull():
                return img
        except Exception:
            pass
    return None


def _image_to_thumb_pixmap(img: QImage) -> QPixmap:
    return _scale_thumb(QPixmap.fromImage(img), _SWITCH_THUMB_W, _SWITCH_THUMB_H)


def _wheel_vertical_delta(event: QWheelEvent) -> float:
    dy = float(event.angleDelta().y())
    if dy == 0.0:
        dy = float(event.pixelDelta().y())
    return dy


class _SwitchThumbSignaler(QObject):
    ready = Signal(int, str, object)


class _SwitchThumbRunnable(QRunnable):
    def __init__(
        self,
        *,
        token: int,
        cache_key: str,
        item: VideoReviewSwitchItem,
        signaler: _SwitchThumbSignaler,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._token = token
        self._cache_key = cache_key
        self._item = item
        self._signaler = signaler

    def run(self) -> None:
        img = _decode_thumb_image(self._item)
        self._signaler.ready.emit(self._token, self._cache_key, img)


class _VideoReviewSwitchRow(QFrame):
    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewSwitchRow")
        self.setFixedHeight(_SWITCH_ROW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._thumb_cache_key: str | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)

        self._thumb = QLabel(self)
        self._thumb.setObjectName("VideoReviewSwitchThumb")
        self._thumb.setFixedSize(_SWITCH_THUMB_W, _SWITCH_THUMB_H)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._thumb, 0)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 2, 0, 2)
        text_col.setSpacing(2)
        self._title = QLabel(self)
        self._title.setObjectName("VideoReviewSwitchTitle")
        self._title.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        self._title.setWordWrap(False)
        text_col.addWidget(self._title)
        self._subtitle = QLabel(self)
        self._subtitle.setObjectName("VideoReviewSwitchSubtitle")
        self._subtitle.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        self._subtitle.setWordWrap(False)
        text_col.addWidget(self._subtitle)
        lay.addLayout(text_col, 1)

        self._check = QLabel(self)
        self._check.setObjectName("VideoReviewSwitchCheck")
        self._check.setFixedWidth(16)
        self._check.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self._check, 0)

    def set_item(self, item: VideoReviewSwitchItem, *, cache_key: str | None = None) -> None:
        self._thumb_cache_key = cache_key
        self._title.setText(item.label)
        if item.subtitle.strip():
            self._subtitle.setText(item.subtitle)
            self._subtitle.show()
        else:
            self._subtitle.hide()
        self._thumb.setPixmap(_blank_thumb())
        self.setProperty("active", "true" if item.checked else "false")
        self._check.setText("●" if item.checked else "")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_thumb_pixmap(self, pix: QPixmap) -> None:
        if pix.isNull():
            return
        self._thumb.setPixmap(pix)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mouseReleaseEvent(event)


class VideoReviewSwitchPopup(QFrame):
    """Popup list of review sources with thumbnails."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("VideoReviewSwitchPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(_SWITCH_POPUP_W)

        root = QVBoxLayout(self)
        root.setContentsMargins(_SWITCH_POPUP_PAD, _SWITCH_POPUP_PAD, _SWITCH_POPUP_PAD, _SWITCH_POPUP_PAD)
        root.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("VideoReviewSwitchScroll")
        self._scroll.setWidgetResizable(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        vsb = self._scroll.verticalScrollBar()
        if vsb is not None:
            vsb.setSingleStep(max(8, _SWITCH_ROW_H // 2))

        self._list_host = QWidget(self._scroll)
        self._list_host.setObjectName("VideoReviewSwitchListHost")
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll)

        self._rows: list[_VideoReviewSwitchRow] = []
        self._items: list[VideoReviewSwitchItem] = []
        self._thumb_token = 0
        self._thumb_signaler = _SwitchThumbSignaler(self)
        self._thumb_signaler.ready.connect(self._on_thumb_ready, Qt.ConnectionType.QueuedConnection)
        self._thumb_pool = QThreadPool(self)
        self._thumb_pool.setMaxThreadCount(4)
        self._mmb_scroll_active = False
        self._mmb_scroll_press_y = 0
        self._mmb_scroll_origin = 0
        self._list_host.installEventFilter(self)
        self._scroll.installEventFilter(self)
        self._scroll.viewport().installEventFilter(self)
        self.installEventFilter(self)

    def _apply_wheel_to_scroll(self, event: QWheelEvent) -> bool:
        bar = self._scroll.verticalScrollBar()
        if bar is None or bar.maximum() <= 0:
            return False
        delta = _wheel_vertical_delta(event)
        if delta == 0.0:
            return False
        bar.setValue(bar.value() - int(delta))
        event.accept()
        return True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if not self.isVisible():
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            if self._apply_wheel_to_scroll(event):
                return True
        if isinstance(event, QMouseEvent):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.MiddleButton:
                self._mmb_scroll_active = True
                self._mmb_scroll_press_y = int(event.globalPosition().y())
                bar = self._scroll.verticalScrollBar()
                self._mmb_scroll_origin = bar.value() if bar is not None else 0
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
            if et == QEvent.Type.MouseMove and self._mmb_scroll_active:
                if bool(event.buttons() & Qt.MouseButton.MiddleButton):
                    dy = int(event.globalPosition().y()) - self._mmb_scroll_press_y
                    bar = self._scroll.verticalScrollBar()
                    if bar is not None:
                        bar.setValue(self._mmb_scroll_origin - dy)
                    event.accept()
                    return True
                self._mmb_scroll_active = False
                self.unsetCursor()
            if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.MiddleButton:
                self._mmb_scroll_active = False
                self.unsetCursor()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._apply_wheel_to_scroll(event):
            super().wheelEvent(event)

    def set_items(self, items: list[VideoReviewSwitchItem]) -> None:
        self._thumb_token += 1
        token = self._thumb_token
        self._items = list(items)
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()
        for idx, entry in enumerate(items):
            cache_key = _thumb_cache_key(entry)
            row = _VideoReviewSwitchRow(self._list_host)
            row.set_item(entry, cache_key=cache_key)
            row.installEventFilter(self)
            cached = _cached_thumb_pixmap(cache_key)
            if cached is not None:
                row.set_thumb_pixmap(cached)
            elif cache_key is not None:
                self._thumb_pool.start(
                    _SwitchThumbRunnable(
                        token=token,
                        cache_key=cache_key,
                        item=entry,
                        signaler=self._thumb_signaler,
                    )
                )
            row.activated.connect(lambda i=idx: self._on_row_activated(i))
            self._list_lay.addWidget(row)
            self._rows.append(row)

    def _on_thumb_ready(self, token: int, cache_key: str, img: object) -> None:
        if token != self._thumb_token:
            return
        if not isinstance(img, QImage) or img.isNull():
            return
        pix = _image_to_thumb_pixmap(img)
        _store_thumb_cache(cache_key, pix)
        for row in self._rows:
            if row._thumb_cache_key == cache_key:
                row.set_thumb_pixmap(pix)

    def _on_row_activated(self, index: int) -> None:
        if 0 <= index < len(self._items):
            cb = self._items[index].on_activate
            if cb is not None:
                cb()
        self.hide()

    def _adjust_scroll_geometry(self, anchor: QWidget) -> None:
        n = len(self._items)
        inner_w = max(1, self.width() - 2 * _SWITCH_POPUP_PAD)
        content_h = max(_SWITCH_ROW_H, n * _SWITCH_ROW_H)
        self._list_host.setFixedWidth(inner_w)
        self._list_host.setFixedHeight(content_h)
        cap = max_popup_height_for_anchor(anchor)
        viewport_h = min(
            content_h,
            max(_SWITCH_ROW_H, cap - 2 * _SWITCH_POPUP_PAD),
            _SWITCH_VISIBLE_ROWS * _SWITCH_ROW_H,
        )
        self._scroll.setFixedHeight(viewport_h)
        self.setFixedHeight(viewport_h + 2 * _SWITCH_POPUP_PAD)

    def popup_near_anchor(self, anchor: QWidget) -> None:
        self._adjust_scroll_geometry(anchor)
        position_popup_near_anchor(self, anchor)
        self.show()
        self.raise_()
        bar = self._scroll.verticalScrollBar()
        if bar is not None:
            bar.setValue(0)
        self._scroll.viewport().setFocus(Qt.FocusReason.PopupFocusReason)
