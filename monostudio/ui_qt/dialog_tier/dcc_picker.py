"""DccPicker — tier grid for choosing a DCC (Create New / Open With visual)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QObject,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QMouseEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.dialog_tier.reference import (
    T,
    _paint_rounded_chrome,
    _text_style,
    _tier_font,
)
from monostudio.ui_qt.ios_switch import IosSwitch
from monostudio.ui_qt.lucide_icons import _read_svg_text, _render_lucide_pixmap
from monostudio.ui_qt.surfaces import SURFACE_DIALOG, SURFACE_POPUP, SURFACE_TOOLTIP, surface_border_color


_DCC_COLS = 4
_DCC_CARD_SIZE = 72  # design reference at ~456px field width
_DCC_CARD_SIZE_MIN = 48
_DCC_ICON_REF = 26
_DCC_GAP = 4
_DCC_VISIBLE_ROWS = 2
_DCC_FADE_H = 24
_DCC_FADE_BLEED = 2
_IMPORT_PILL_RADIUS = 12.0
_DEPT_STAR_COLOR = "#FACC15"


def _filled_star_pixmap(size: int) -> QPixmap:
    svg = _read_svg_text("star")
    if not svg:
        return QPixmap()
    svg = (
        svg.replace('fill="none"', f'fill="{_DEPT_STAR_COLOR}"')
        .replace("currentColor", _DEPT_STAR_COLOR)
        .replace('stroke-width="2"', 'stroke-width="0"')
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    return _render_lucide_pixmap(renderer, size)


def _grid_content_width(card_size: int) -> int:
    return _DCC_COLS * card_size + (_DCC_COLS - 1) * _DCC_GAP


def _grid_content_height(rows: int, card_size: int) -> int:
    if rows <= 0:
        return 0
    return rows * card_size + (rows - 1) * _DCC_GAP


def _card_size_for_width(available: int) -> int:
    gaps = (_DCC_COLS - 1) * _DCC_GAP
    if available <= gaps:
        return _DCC_CARD_SIZE_MIN
    return max(_DCC_CARD_SIZE_MIN, (available - gaps) // _DCC_COLS)


@dataclass(frozen=True, slots=True)
class DccPickerItem:
    dcc_id: str
    label: str
    icon_slug: str = ""
    color_hex: str | None = None
    disabled: bool = False
    department_default: bool = False
    last_used: bool = False


class DccPickerCard(QFrame):
    """Square tier card — brand icon + label."""

    clicked_card = Signal(str)

    def __init__(self, item: DccPickerItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._selected = False
        self._hovered = False
        self.setObjectName("DccPickerCard")
        self._card_size = _DCC_CARD_SIZE
        slug = (item.icon_slug or item.dcc_id or "layers").strip()
        self._icon_slug = slug
        self._icon_color = item.color_hex or T["label"]
        self.setCursor(
            Qt.CursorShape.ForbiddenCursor if item.disabled else Qt.CursorShape.PointingHandCursor
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        if item.disabled:
            self.setEnabled(False)
            self.setToolTip("Work file already exists for this DCC.")
        elif item.last_used:
            self.setToolTip("Last used for this department.")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)

        content = QWidget(self)
        content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content.setStyleSheet("background: transparent;")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(3)
        content_lay.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        icon_px = self._icon_slot_for(self._card_size)
        disabled_scale = 0.55 if item.disabled else 1.0
        pixmap_px = int(icon_px * disabled_scale)
        self._icon = QLabel(content)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFixedSize(icon_px, icon_px)
        self._icon.setPixmap(
            brand_icon(slug, size=pixmap_px, color_hex=self._icon_color).pixmap(pixmap_px, pixmap_px)
        )
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content_lay.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignHCenter)

        label_font = _tier_font(10, QFont.Weight.Medium)
        label_h = QFontMetrics(label_font).height()
        self._label = QLabel(item.label, content)
        self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._label.setFont(label_font)
        label_color = T["meta"] if item.disabled else T["body"]
        self._label.setStyleSheet(_text_style(color=label_color))
        self._label.setFixedHeight(label_h)
        self._label.setMaximumWidth(self._card_size - 12)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content_lay.addWidget(self._label, alignment=Qt.AlignmentFlag.AlignHCenter)

        lay.addStretch(1)
        lay.addWidget(content, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

        self._badge = QLabel(self)
        self._badge.setFixedSize(13, 13)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setPixmap(_filled_star_pixmap(10))
        self._badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._badge.hide()
        self.apply_metrics(_DCC_CARD_SIZE)

    @staticmethod
    def _icon_slot_for(card_size: int) -> int:
        return max(18, int(round(card_size * _DCC_ICON_REF / _DCC_CARD_SIZE)))

    def apply_metrics(self, size: int) -> None:
        if size == self._card_size and self.width() == size:
            return
        self._card_size = size
        self.setFixedSize(size, size)
        icon_slot = self._icon_slot_for(size)
        disabled_scale = 0.55 if self._item.disabled else 1.0
        pixmap_px = max(10, int(icon_slot * disabled_scale))
        self._icon.setFixedSize(icon_slot, icon_slot)
        self._icon.setPixmap(
            brand_icon(self._icon_slug, size=pixmap_px, color_hex=self._icon_color).pixmap(
                pixmap_px, pixmap_px
            )
        )
        font_px = 10 if size >= 68 else 9
        label_font = _tier_font(font_px, QFont.Weight.Medium)
        self._label.setFont(label_font)
        self._label.setFixedHeight(QFontMetrics(label_font).height())
        self._label.setMaximumWidth(max(1, size - 12))
        self._sync_badge()
        self._sync_label_elide()

    def dcc_id(self) -> str:
        return self._item.dcc_id

    def set_selected(self, on: bool) -> None:
        if self._selected != on:
            self._selected = on
            self.update()

    def is_selected(self) -> bool:
        return self._selected

    def _sync_badge(self) -> None:
        show = self._item.department_default and not self._item.disabled
        self._badge.setVisible(show)
        if show:
            self._badge.move(self.width() - 16, 5)

    def _sync_label_elide(self) -> None:
        fm = QFontMetrics(self._label.font())
        w = max(1, self._label.maximumWidth())
        self._label.setText(fm.elidedText(self._item.label, Qt.TextElideMode.ElideRight, w))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_badge()
        self._sync_label_elide()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._item.disabled and event.button() == Qt.MouseButton.LeftButton:
            self.clicked_card.emit(self._item.dcc_id)
        super().mouseReleaseEvent(event)

    def _stroke_color(self) -> QColor:
        if self._item.disabled:
            c = surface_border_color(SURFACE_POPUP, T, hover=False)
            c.setAlpha(int(c.alpha() * 0.6))
            return c
        if self._selected:
            c = QColor(59, 130, 246)
            c.setAlpha(int(255 * 0.92))
            return c
        return surface_border_color(SURFACE_POPUP, T, hover=self._hovered)

    def _fill_color(self) -> QColor:
        if self._selected and not self._item.disabled:
            tint = QColor(37, 99, 235)
            tint.setAlpha(int(255 * 0.22))
            return tint
        if self._hovered and not self._item.disabled:
            return QColor(T[SURFACE_TOOLTIP])
        return QColor(T[SURFACE_POPUP])

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_rounded_chrome(
            p,
            QRectF(self.rect()),
            fill=self._fill_color(),
            stroke=self._stroke_color(),
            radius=float(T["radius_sm"]),
        )
        p.end()
        super().paintEvent(event)


class _DccScrollFadeOverlay(QWidget):
    """Bottom fade — hints more items; hides when scrolled to end."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        h = self.height()
        w = self.width()
        bg = QColor(T[SURFACE_DIALOG])
        grad = QLinearGradient(0, 0, 0, h)
        top = QColor(bg)
        top.setAlpha(0)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bg)
        p.fillRect(self.rect(), grad)
        p.fillRect(0, h - _DCC_FADE_BLEED, w, _DCC_FADE_BLEED, bg)
        p.end()


class DccPickerGrid(QWidget):
    """Scrollable 5-column DCC card grid."""

    selection_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DccPickerGrid")
        self.setStyleSheet("background: transparent;")
        self._items: list[DccPickerItem] = []
        self._cards: list[DccPickerCard] = []
        self._selected_id: str | None = None
        self._layout_card_size = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("DccPickerScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setLineWidth(0)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setViewportMargins(0, 0, T["scroll_gutter"], 0)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.viewport().setStyleSheet("background: transparent;")

        self._host = QWidget()
        self._host.setObjectName("DccPickerHost")
        self._host.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_DCC_GAP)
        self._grid.setVerticalSpacing(_DCC_GAP)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for col in range(_DCC_COLS):
            self._grid.setColumnStretch(col, 0)
        self._scroll.setWidget(self._host)

        self._scroll.setFixedHeight(
            _DCC_CARD_SIZE * _DCC_VISIBLE_ROWS + _DCC_GAP * (_DCC_VISIBLE_ROWS - 1)
        )

        self._fade = _DccScrollFadeOverlay(self._scroll)
        self._fade.hide()
        self._scroll.verticalScrollBar().valueChanged.connect(self._sync_scroll_fade)
        self._scroll.verticalScrollBar().rangeChanged.connect(self._sync_scroll_fade)
        self._scroll.installEventFilter(self)
        self._scroll.viewport().installEventFilter(self)

        outer.addWidget(self._scroll)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj in (self._scroll, self._scroll.viewport()) and event.type() == QEvent.Type.Resize:
            self._relayout_for_width(self._viewport_width())
            self._position_fade()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout_for_width(self._viewport_width())
        self._position_fade()

    def _viewport_width(self) -> int:
        return max(1, self._scroll.viewport().width())

    def _position_fade(self) -> None:
        if not self._fade.isVisible():
            return
        vp = self._scroll.viewport()
        g = vp.geometry()
        fade_h = _DCC_FADE_H + _DCC_FADE_BLEED
        y = g.top() + g.height() - _DCC_FADE_H
        self._fade.setGeometry(g.left(), y, g.width(), fade_h)

    def _sync_scroll_fade(self, *_args) -> None:
        sb = self._scroll.verticalScrollBar()
        show = sb.maximum() > 0 and sb.value() < sb.maximum()
        self._fade.setVisible(show)
        if show:
            self._position_fade()
            self._fade.raise_()

    def selected_dcc_id(self) -> str | None:
        return self._selected_id

    def set_items(self, items: list[DccPickerItem], *, selected_id: str | None = None) -> None:
        self._items = list(items)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()

        enabled_ids = [it.dcc_id for it in items if not it.disabled]
        pick = selected_id
        if pick not in enabled_ids:
            for it in items:
                if not it.disabled:
                    pick = it.dcc_id
                    break
            else:
                pick = None
        self._selected_id = pick

        for index, spec in enumerate(items):
            card = DccPickerCard(spec, self._host)
            card.clicked_card.connect(self._on_card_clicked)
            row = index // _DCC_COLS
            col = index % _DCC_COLS
            self._grid.addWidget(card, row, col)
            self._cards.append(card)
        self._apply_selection()
        QTimer.singleShot(0, self._after_items_layout)

    def _after_items_layout(self) -> None:
        self._relayout_for_width(self._viewport_width())
        self._sync_scroll_fade()

    def _relayout_for_width(self, available: int) -> None:
        if not self._cards:
            return
        card_size = _card_size_for_width(available)
        if card_size == self._layout_card_size:
            return
        self._layout_card_size = card_size
        for card in self._cards:
            card.apply_metrics(card_size)
        for col in range(_DCC_COLS):
            self._grid.setColumnMinimumWidth(col, card_size)
        rows = max(1, (len(self._items) + _DCC_COLS - 1) // _DCC_COLS)
        grid_w = _grid_content_width(card_size)
        side = max(0, (available - grid_w) // 2)
        self._grid.setContentsMargins(side, 0, available - grid_w - side, 0)
        self._host.setMinimumHeight(_grid_content_height(rows, card_size))
        self._scroll.setFixedHeight(
            card_size * _DCC_VISIBLE_ROWS + _DCC_GAP * (_DCC_VISIBLE_ROWS - 1)
        )

    def _on_card_clicked(self, dcc_id: str) -> None:
        if dcc_id == self._selected_id:
            return
        self._selected_id = dcc_id
        self._apply_selection()
        self.selection_changed.emit(dcc_id)

    def _apply_selection(self) -> None:
        sid = self._selected_id
        for card in self._cards:
            card.set_selected(card.isEnabled() and card.dcc_id() == sid)


class _ImportPillFrame(QFrame):
    """Elevated pill row for import-source toggle."""

    def __init__(self, switch: IosSwitch, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._switch = switch
        self.setObjectName("ImportSourcePill")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")
        self._hover = False
        self.setMouseTracking(True)
        self.setMinimumHeight(52)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if not self._switch.geometry().contains(pos):
                self._switch.setChecked(not self._switch.isChecked())
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_rounded_chrome(
            p,
            QRectF(self.rect()),
            fill=QColor(T[SURFACE_POPUP]),
            stroke=surface_border_color(SURFACE_POPUP, T, hover=self._hover),
            radius=_IMPORT_PILL_RADIUS,
        )
        p.end()
        super().paintEvent(event)


class ImportSourceCard(QWidget):
    """Apple-style pill row — title + hint left, iOS switch right."""

    toggled = Signal(bool)

    def __init__(
        self,
        *,
        title: str = "Import source",
        hint: str = "Browse or drop a file into the work folder.",
        checked: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        self._switch = IosSwitch(checked=checked)
        self._switch.setToolTip("Copy a source file into the work folder with pipeline naming.")
        self._switch.toggled.connect(self.toggled.emit)

        self._pill = _ImportPillFrame(self._switch)
        self._pill.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self._pill)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setFont(_tier_font(13, QFont.Weight.Medium))
        title_lbl.setStyleSheet(_text_style(color=T["body"]))
        title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_col.addWidget(title_lbl)

        hint_lbl = QLabel(hint)
        hint_lbl.setWordWrap(True)
        hint_lbl.setFont(_tier_font(11))
        hint_lbl.setStyleSheet(_text_style(color=T["meta"]))
        hint_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_col.addWidget(hint_lbl)
        lay.addLayout(text_col, stretch=1)

        lay.addWidget(self._switch, alignment=Qt.AlignmentFlag.AlignVCenter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._pill)

    def is_checked(self) -> bool:
        return self._switch.isChecked()

    def set_checked(self, on: bool) -> None:
        self._switch.setChecked(on)


__all__ = ["DccPickerCard", "DccPickerGrid", "DccPickerItem", "ImportSourceCard"]
