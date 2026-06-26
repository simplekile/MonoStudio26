"""Scrollable thumbnail popup for switching review videos / sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFont, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
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


@dataclass
class VideoReviewSwitchItem:
    label: str
    subtitle: str = ""
    checked: bool = False
    thumb_path: Path | None = None
    sequence_folder: Path | None = None
    on_activate: Callable[[], None] | None = None


def _scale_thumb(pix: QPixmap, w: int, h: int) -> QPixmap:
    if pix.isNull():
        out = QPixmap(w, h)
        out.fill(Qt.GlobalColor.transparent)
        return out
    return pix.scaled(
        w,
        h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    ).copy(0, 0, w, h)


def _thumb_pixmap_for_item(item: VideoReviewSwitchItem) -> QPixmap:
    blank = QPixmap(_SWITCH_THUMB_W, _SWITCH_THUMB_H)
    blank.fill(Qt.GlobalColor.transparent)
    if item.thumb_path is not None and is_video_path(item.thumb_path):
        try:
            data = extract_video_frame_png_bytes(item.thumb_path, 0.0, width=176, keyframe_aligned=True)
            if data:
                pix = QPixmap()
                if pix.loadFromData(data):
                    return _scale_thumb(pix, _SWITCH_THUMB_W, _SWITCH_THUMB_H)
        except Exception:
            pass
    folder = item.sequence_folder
    if folder is not None and folder.is_dir():
        try:
            frames = list_sequence_frames(folder)
            if frames:
                img = load_preview_frame_qimage(frames[0], max_side=176)
                if img is not None and not img.isNull():
                    pix = QPixmap.fromImage(img)
                    return _scale_thumb(pix, _SWITCH_THUMB_W, _SWITCH_THUMB_H)
        except Exception:
            pass
    if item.thumb_path is not None and item.thumb_path.is_file():
        try:
            img = QImage(str(item.thumb_path))
            if not img.isNull():
                pix = QPixmap.fromImage(img)
                return _scale_thumb(pix, _SWITCH_THUMB_W, _SWITCH_THUMB_H)
        except Exception:
            pass
    return blank


class _VideoReviewSwitchRow(QFrame):
    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewSwitchRow")
        self.setFixedHeight(_SWITCH_ROW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

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

    def set_item(self, item: VideoReviewSwitchItem) -> None:
        self._title.setText(item.label)
        if item.subtitle.strip():
            self._subtitle.setText(item.subtitle)
            self._subtitle.show()
        else:
            self._subtitle.hide()
        pix = _thumb_pixmap_for_item(item)
        self._thumb.setPixmap(pix)
        self.setProperty("active", "true" if item.checked else "false")
        self._check.setText("●" if item.checked else "")
        self.style().unpolish(self)
        self.style().polish(self)

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
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._list_host = QWidget(self._scroll)
        self._list_host.setObjectName("VideoReviewSwitchListHost")
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)
        self._list_lay.addStretch(1)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll)

        self._rows: list[_VideoReviewSwitchRow] = []
        self._items: list[VideoReviewSwitchItem] = []

    def set_items(self, items: list[VideoReviewSwitchItem]) -> None:
        self._items = list(items)
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()
        for idx, entry in enumerate(items):
            row = _VideoReviewSwitchRow(self._list_host)
            row.set_item(entry)
            row.activated.connect(lambda i=idx: self._on_row_activated(i))
            self._list_lay.insertWidget(idx, row)
            self._rows.append(row)
        list_h = max(_SWITCH_ROW_H, len(items) * _SWITCH_ROW_H)
        self._list_host.setMinimumHeight(list_h)
        self._list_host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _on_row_activated(self, index: int) -> None:
        if 0 <= index < len(self._items):
            cb = self._items[index].on_activate
            if cb is not None:
                cb()
        self.hide()

    def popup_near_anchor(self, anchor: QWidget) -> None:
        cap = max_popup_height_for_anchor(anchor)
        visible_h = min(
            cap - 2 * _SWITCH_POPUP_PAD,
            _SWITCH_VISIBLE_ROWS * _SWITCH_ROW_H,
        )
        self._scroll.setMaximumHeight(max(_SWITCH_ROW_H, visible_h))
        self.adjustSize()
        position_popup_near_anchor(self, anchor)
        self.show()
        self.raise_()
