"""MonoSelect — premium field trigger + floating card popup (design system)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import (
    QEvent,
    Property,
    QPoint,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.dialog_tier.reference import (
    T,
    _FIELD_ALIGN,
    _FocusShell,
    _chroma_color,
    _configure_field_shell_layout,
    _surface_stroke,
    _paint_rounded_chrome,
    _paint_disclosure_body_chrome,
    _paint_disclosure_header_chrome,
    _sync_disclosure_body_mask,
    _prepare_shell_input,
    _text_style,
    _tier_font,
)
from monostudio.ui_qt.surfaces import SURFACE_FIELD, SURFACE_POPUP
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import (
    max_popup_height_in_widget,
    position_overlay_below_anchor_aligned,
)


@dataclass(frozen=True, slots=True)
class MonoSelectOption:
    """One selectable row in a MonoSelect popup.

    Future-friendly fields (``badge``, ``disabled``, ``separator``) are part of the
    stable item model — render only what each row needs.
    """

    value: str
    label: str
    secondary: str = ""
    icon: str | None = None
    badge: str | None = None
    disabled: bool = False
    separator: bool = False

    @property
    def subtitle(self) -> str:
        return self.secondary


def mono_select_options_from_strings(
    items: list[str],
    *,
    icon_map: dict[str, str] | None = None,
) -> list[MonoSelectOption]:
    """Build options from legacy strings (``Label — value`` or plain text)."""
    icons = icon_map or {}
    out: list[MonoSelectOption] = []
    for raw in items:
        text = raw.strip()
        if "—" in text:
            label, value = (part.strip() for part in text.split("—", 1))
            secondary = f"Prefix: {value}" if value and not value.startswith("Prefix:") else value
            out.append(
                MonoSelectOption(
                    value=value,
                    label=label,
                    secondary=secondary,
                    icon=icons.get(label) or icons.get(value),
                )
            )
        else:
            out.append(MonoSelectOption(value=text, label=text, icon=icons.get(text)))
    return out


_ASSET_TYPE_ICONS = {
    "Character": "user",
    "Prop": "package",
    "Environment": "trees",
}


def _item_height(option: MonoSelectOption) -> int:
    return int(T["mono_select_item_h_dual"] if option.secondary else T["mono_select_item_h"])


def _row_tint(alpha_key: str) -> QColor:
    return _chroma_color(float(T[alpha_key]))


class _MonoSelectShell(_FocusShell):
    """Field shell that flattens its bottom edge while the list is open."""

    toggle_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, readonly: bool = False) -> None:
        super().__init__(parent, readonly=readonly)
        self._expanded = False

    def set_expanded(self, on: bool) -> None:
        if on != self._expanded:
            self._expanded = on
            self.update()

    def outline_stroke_color(self) -> QColor:
        return self._stroke_color()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._expanded:
            super().paintEvent(event)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_disclosure_header_chrome(
            p,
            QRectF(self.rect()),
            fill=QColor(T[SURFACE_FIELD]),
            stroke=self._stroke_color(),
            radius=float(T["radius_sm"]),
        )
        p.end()
        QFrame.paintEvent(self, event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, (_MonoSelectTrigger, _AnimatedChevron)):
                self.toggle_requested.emit()
        super().mouseReleaseEvent(event)


class _MonoSelectSeparator(QWidget):
    """Non-interactive group divider inside the popup list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(int(T["mono_select_separator_h"]))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        y = self.height() * 0.5
        inset = int(T["mono_select_row_inset_x"])
        pen = QPen(_chroma_color(float(T["hairline_a"])), 1.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLine(inset, y, self.width() - inset, y)
        p.end()


class _AnimatedChevron(QWidget):
    """Trailing chevron with smooth open/close rotation."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self._open = False
        self._anim: QPropertyAnimation | None = None
        chevron_slot = int(T["mono_select_chevron_slot"])
        self.setFixedSize(chevron_slot, chevron_slot)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet("background: transparent; border: none;")

    def _get_angle(self) -> float:
        return self._angle

    def _set_angle(self, value: float) -> None:
        self._angle = value
        self.update()

    angle = Property(float, _get_angle, _set_angle)

    def set_open(self, open_: bool, *, animate: bool = True) -> None:
        target = 180.0 if open_ else 0.0
        if open_ == self._open:
            if self._anim is not None or abs(self._angle - target) < 0.5:
                return
        self._open = open_
        if not animate:
            if self._anim is not None:
                self._anim.stop()
                self._anim = None
            self._set_angle(target)
            return
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self._anim = QPropertyAnimation(self, b"angle", self)
        self._anim.setDuration(int(T["mono_select_anim_ms"]))
        self._anim.setStartValue(self._angle)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        icon = lucide_icon("chevron-down", size=int(T["mono_select_chevron_size"]), color_hex=T["label"])
        px = icon.pixmap(int(T["mono_select_chevron_size"]), int(T["mono_select_chevron_size"]))
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self._angle)
        half = int(T["mono_select_chevron_size"]) // 2
        p.drawPixmap(-half, -half, px)
        p.end()


class MonoSelectItem(QFrame):
    """Single row inside the MonoSelect popup."""

    clicked = Signal(str)

    def __init__(
        self,
        option: MonoSelectOption,
        *,
        selected: bool = False,
        highlighted: bool = False,
        on_hover: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._option = option
        self._selected = selected
        self._highlighted = highlighted
        self._disabled = option.disabled
        self._on_hover = on_hover
        self.setObjectName("MonoSelectItem")
        if self._disabled:
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(_item_height(option))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

        lay = QHBoxLayout(self)
        pad_x = int(T["mono_select_row_pad_x"])
        lay.setContentsMargins(pad_x, 0, pad_x, 0)
        lay.setSpacing(8)

        if option.icon:
            icon_lbl = QLabel()
            icon_size = int(T["mono_select_icon_size"])
            icon_slot = int(T["mono_select_icon_slot"])
            icon_color = T["meta"] if self._disabled else T["label"]
            icon_lbl.setPixmap(lucide_icon(option.icon, size=icon_size, color_hex=icon_color).pixmap(icon_size, icon_size))
            icon_lbl.setFixedSize(icon_slot, icon_slot)
            icon_lbl.setStyleSheet("background: transparent; border: none;")
            lay.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0 if not option.secondary else 1)
        primary = QLabel(option.label)
        primary.setFont(_tier_font(13))
        primary_color = T["meta"] if self._disabled else T["body"]
        primary.setStyleSheet(_text_style(color=primary_color))
        text_col.addWidget(primary)
        if option.secondary:
            secondary = QLabel(option.secondary)
            secondary.setFont(_tier_font(11))
            secondary.setStyleSheet(_text_style(color=T["meta"]))
            text_col.addWidget(secondary)
        lay.addLayout(text_col, stretch=1)

        self._check = QLabel()
        check_size = int(T["mono_select_check_size"])
        self._check.setFixedSize(check_size + 2, check_size + 2)
        self._check.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(self._check, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._sync_check()

    def option(self) -> MonoSelectOption:
        return self._option

    def set_selected(self, on: bool) -> None:
        if self._selected != on:
            self._selected = on
            self._sync_check()
            self.update()

    def set_highlighted(self, on: bool) -> None:
        if self._highlighted != on:
            self._highlighted = on
            self.update()

    def _sync_check(self) -> None:
        if self._selected:
            check_size = int(T["mono_select_check_size"])
            self._check.setPixmap(
                lucide_icon("check", size=check_size, color_hex=T["meta"]).pixmap(check_size, check_size)
            )
        else:
            self._check.clear()

    def _row_alpha_key(self) -> str | None:
        if self._disabled:
            return None
        if self._highlighted:
            return "mono_select_row_highlight_a"
        if self.underMouse():
            return "mono_select_row_hover_a"
        if self._selected:
            return "mono_select_row_selected_a"
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._disabled:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._option.value)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        key = self._row_alpha_key()
        if key is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            inset_x = float(T["mono_select_row_inset_x"])
            inset_y = float(T["mono_select_row_inset_y"])
            rect = QRectF(self.rect()).adjusted(inset_x, inset_y, -inset_x, -inset_y)
            path = QPainterPath()
            path.addRoundedRect(rect, float(T["mono_select_row_radius"]), float(T["mono_select_row_radius"]))
            p.fillPath(path, _row_tint(key))
            p.end()
        super().paintEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._on_hover is not None:
            self._on_hover()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class _MonoSelectPopupCard(QFrame):
    """Popup surface — chrome painted on this widget so borders are not covered by children."""

    def __init__(self, popup: "_MonoSelectPopup") -> None:
        super().__init__(popup)
        self._popup = popup
        self.setObjectName("MonoSelectPopupCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = QRectF(self.rect())
        radius = float(T["radius_sm"])
        if self._popup._attached:
            _paint_disclosure_body_chrome(
                p,
                bounds,
                fill=QColor(T[SURFACE_POPUP]),
                stroke=self._popup._outline_stroke(),
                radius=radius,
                separator=self._popup._separator_color(),
            )
        else:
            _paint_rounded_chrome(
                p,
                bounds,
                fill=QColor(T[SURFACE_POPUP]),
                stroke=_surface_stroke(SURFACE_POPUP),
                radius=float(T["mono_select_popup_radius"]),
            )
        p.end()
        super().paintEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._popup._attached:
            _sync_disclosure_body_mask(self, radius=float(T["radius_sm"]), enabled=True)


class _MonoSelectPopup(QFrame):
    """Attached list panel — child overlay on the dialog; height reveal via ``panel_height``."""

    option_chosen = Signal(str)
    dismissed = Signal()
    opened = Signal()
    closed = Signal()

    def __init__(self, owner: "MonoSelect", host: QWidget) -> None:
        super().__init__(host)
        self._owner = owner
        self.setObjectName("MonoSelectPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._items: list[MonoSelectItem] = []
        self._selectable: list[MonoSelectItem] = []
        self._highlight = 0
        self._anchor: QWidget | None = None
        self._attached = False
        self._open_anim: QPropertyAnimation | None = None
        self._panel_height = 0.0
        self._target_panel_height = 0
        self._outer_lay = QVBoxLayout(self)
        self._outer_lay.setContentsMargins(0, 0, 0, 0)
        self._outer_lay.setSpacing(0)

        self._card = _MonoSelectPopupCard(self)
        self._card_lay = QVBoxLayout(self._card)
        self._card_lay.setContentsMargins(0, 0, 0, 0)
        self._card_lay.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("MonoSelectPopupScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setViewportMargins(0, 0, T["scroll_gutter"], 0)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.viewport().setStyleSheet("background: transparent;")

        self._list_host = QWidget()
        self._list_host.setObjectName("MonoSelectPopupList")
        self._list_host.setStyleSheet("background: transparent; border: none;")
        self._list_lay = QVBoxLayout(self._list_host)
        pad_inner = int(T["mono_select_popup_pad"])
        self._list_lay.setContentsMargins(pad_inner, pad_inner, pad_inner, pad_inner)
        self._list_lay.setSpacing(1)
        self._scroll.setWidget(self._list_host)
        self._card_lay.addWidget(self._scroll)
        self._outer_lay.addWidget(self._card)
        self.hide()

    def _sync_card_shape(self) -> None:
        if self._attached:
            _sync_disclosure_body_mask(self._card, radius=float(T["radius_sm"]), enabled=True)
        else:
            _sync_disclosure_body_mask(self._card, radius=float(T["radius_sm"]), enabled=False)

    def _get_panel_height(self) -> float:
        return self._panel_height

    def _set_panel_height(self, value: float) -> None:
        self._panel_height = value
        self.setFixedHeight(max(0, int(round(value))))

    panel_height = Property(float, _get_panel_height, _set_panel_height)

    def _stop_open_animation(self) -> None:
        anim = self._open_anim
        self._open_anim = None
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass

    def _play_open_animation(self) -> None:
        duration = int(T["mono_select_anim_ms"])
        self._stop_open_animation()
        anim = QPropertyAnimation(self, b"panel_height", self)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(float(self._target_panel_height))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._stop_open_animation)
        self._open_anim = anim
        anim.start()

    def _outline_stroke(self) -> QColor:
        anchor = self._anchor
        if isinstance(anchor, _MonoSelectShell):
            return anchor.outline_stroke_color()
        return _surface_stroke(SURFACE_POPUP)

    def _separator_color(self) -> QColor:
        return _chroma_color(float(T["mono_select_disclosure_separator_a"]))

    def _natural_content_width(self, options: list[MonoSelectOption]) -> int:
        fm_primary = QFontMetrics(_tier_font(13))
        fm_secondary = QFontMetrics(_tier_font(11))
        has_icon = any(opt.icon for opt in options if not opt.separator)
        icon_w = int(T["mono_select_icon_slot"]) + 8 if has_icon else 0
        check_w = int(T["mono_select_check_size"]) + 10
        margins = int(T["mono_select_row_pad_x"]) * 2
        pad_inner = int(T["mono_select_popup_pad"]) * 2
        widest = 0
        for opt in options:
            if opt.separator:
                text_w = 0
            else:
                text_w = fm_primary.horizontalAdvance(opt.label)
                if opt.secondary:
                    text_w = max(text_w, fm_secondary.horizontalAdvance(opt.secondary))
            widest = max(widest, margins + icon_w + text_w + check_w)
        lo = int(T["mono_select_popup_min_w"])
        hi = int(T["mono_select_popup_max_w"])
        return max(lo, min(widest + pad_inner, hi))

    def set_options(
        self,
        options: list[MonoSelectOption],
        *,
        current_value: str,
        highlight_index: int,
    ) -> None:
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._items.clear()
        self._selectable.clear()
        selectable_indices = [i for i, opt in enumerate(options) if not opt.separator and not opt.disabled]
        if highlight_index in selectable_indices:
            self._highlight = selectable_indices.index(highlight_index)
        else:
            self._highlight = 0
        for i, opt in enumerate(options):
            if opt.separator:
                self._list_lay.addWidget(_MonoSelectSeparator())
                continue
            row = MonoSelectItem(
                opt,
                selected=opt.value == current_value,
                highlighted=False,
                on_hover=lambda idx=i: self._set_highlight_by_option_index(idx),
            )
            row.clicked.connect(self._on_row_clicked)
            self._list_lay.addWidget(row)
            self._items.append(row)
            if not opt.disabled:
                self._selectable.append(row)
        if self._selectable:
            self._sync_highlight_from_option_index(highlight_index)
        self._content_width = self._natural_content_width(options)
        self._sync_scroll_height()

    def _option_index_for_row(self, row: MonoSelectItem) -> int:
        return self._list_lay.indexOf(row)

    def _set_highlight_by_option_index(self, option_index: int) -> None:
        row = self._list_lay.itemAt(option_index).widget()
        if not isinstance(row, MonoSelectItem):
            return
        if row in self._selectable:
            self._set_highlight(self._selectable.index(row))

    def _sync_highlight_from_option_index(self, option_index: int) -> None:
        row = self._list_lay.itemAt(option_index).widget()
        if isinstance(row, MonoSelectItem) and row in self._selectable:
            self._set_highlight(self._selectable.index(row), force=True)

    def _sync_scroll_height(self) -> None:
        pad = int(T["mono_select_popup_pad"])
        max_rows = int(T["mono_select_popup_max_visible"])
        row_widgets: list[QWidget] = []
        for i in range(self._list_lay.count()):
            w = self._list_lay.itemAt(i).widget()
            if w is not None:
                row_widgets.append(w)
        visible = row_widgets[:max_rows]
        total_h = pad * 2
        for i, w in enumerate(visible):
            total_h += w.height()
            if i < len(visible) - 1:
                total_h += 1
        self._scroll.setFixedHeight(total_h)

    def _on_row_clicked(self, value: str) -> None:
        self.option_chosen.emit(value)
        self.hide()

    def _set_highlight(self, index: int, *, force: bool = False) -> None:
        if not self._selectable:
            return
        index = max(0, min(index, len(self._selectable) - 1))
        if not force and index == self._highlight:
            return
        self._selectable[self._highlight].set_highlighted(False)
        self._highlight = index
        self._selectable[self._highlight].set_highlighted(True)

    def _next_highlight(self, delta: int) -> None:
        if not self._selectable:
            return
        self._set_highlight(self._highlight + delta)

    def _resolve_card_width(self, anchor_width: int, *, attached: bool) -> int:
        """Card matches the field width when attached (one continuous outline)."""
        if attached:
            return anchor_width
        natural = int(self._content_width)
        floor_w = max(int(T["mono_select_popup_min_w"]), anchor_width)
        hi = int(T["mono_select_popup_max_w"])
        return max(floor_w, min(natural, hi))

    def _apply_content_width(self, card_width: int) -> None:
        self._card.setFixedWidth(card_width)
        self._list_host.setMinimumWidth(card_width - int(T["mono_select_popup_pad"]) * 2)
        self.setFixedWidth(card_width)

    def _measure_panel_height(self) -> int:
        self._card.adjustSize()
        return max(1, self._card.sizeHint().height())

    def show_for_anchor(self, anchor: QWidget) -> None:
        host = self._owner.window()
        if host is None:
            return
        if self.parentWidget() is not host:
            self.setParent(host)

        self._anchor = anchor
        self._attached = True
        self._sync_card_shape()
        gap = int(T["mono_select_popup_gap"])
        seam = int(T["mono_select_seam_overlap"])
        card_w = self._resolve_card_width(anchor.width(), attached=True)
        self._apply_content_width(card_w)
        pad = int(T["mono_select_popup_pad"])
        top_pad = int(T["mono_select_popup_pad_top"])
        self._list_lay.setContentsMargins(pad, top_pad, pad, pad)
        self._scroll.setMaximumHeight(16777215)
        self.adjustSize()

        target_h = self._measure_panel_height()
        self.setFixedHeight(target_h)
        position_overlay_below_anchor_aligned(
            self,
            anchor,
            host,
            content_inset=0,
            gap=gap,
            seam_overlap=seam,
        )
        cap = max_popup_height_in_widget(host, top_offset=self.y())
        if target_h > cap:
            self._scroll.setMaximumHeight(max(1, cap - top_pad - pad))
            self.adjustSize()
            target_h = self._measure_panel_height()
            self.setFixedHeight(target_h)
            position_overlay_below_anchor_aligned(
                self,
                anchor,
                host,
                content_inset=0,
                gap=gap,
                seam_overlap=seam,
            )

        self._target_panel_height = target_h
        self._panel_height = 0.0
        self.setFixedHeight(0)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self.opened.emit()
        self._play_open_animation()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End):
            if not self._selectable:
                super().keyPressEvent(event)
                return
            if key == Qt.Key.Key_Up:
                self._next_highlight(-1)
            elif key == Qt.Key.Key_Down:
                self._next_highlight(1)
            elif key == Qt.Key.Key_Home:
                self._set_highlight(0)
            else:
                self._set_highlight(len(self._selectable) - 1)
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self._selectable:
                self.option_chosen.emit(self._selectable[self._highlight].option().value)
                self.hide()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def hide(self) -> None:  # noqa: N802
        self._stop_open_animation()
        super().hide()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._attached = False
        self._stop_open_animation()
        self._panel_height = 0.0
        self._target_panel_height = 0
        self.setFixedHeight(0)
        self._sync_card_shape()
        self.closed.emit()
        self.dismissed.emit()
        super().hideEvent(event)


class _MonoSelectTrigger(QWidget):
    """Focus target inside FieldShell — keyboard + click to open."""

    activate = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")
        _prepare_shell_input(self, readonly=True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._leading = QLabel()
        self._leading.setFixedSize(20, 20)
        self._leading.setStyleSheet("background: transparent; border: none;")
        self._leading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._leading.hide()
        lay.addWidget(self._leading, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._label = QLabel()
        self._label.setFont(_tier_font(13))
        self._label.setStyleSheet(_text_style(color=T["body"]))
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._label, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_display(self, *, label: str, icon: str | None) -> None:
        self._label.setText(label)
        if icon:
            self._leading.setPixmap(
                lucide_icon(icon, size=15, color_hex=T["meta"]).pixmap(15, 15)
            )
            self._leading.show()
        else:
            self._leading.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activate.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Down,
            Qt.Key.Key_Up,
        ):
            self.activate.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MonoSelect(QWidget):
    """Reusable selector: FieldShell trigger → overlay list panel → MonoSelectItem rows."""

    value_changed = Signal(str)
    currentIndexChanged = Signal(int)
    currentTextChanged = Signal(str)

    def __init__(
        self,
        options: list[MonoSelectOption] | list[str] | None = None,
        *,
        current_value: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._options: list[MonoSelectOption] = []
        self._index = 0
        self._popup: _MonoSelectPopup | None = None
        self._outside_filter_installed = False
        self._min_width = 280

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._shell = _MonoSelectShell(readonly=True)
        sl = QHBoxLayout(self._shell)
        _configure_field_shell_layout(sl, left=10, right=4)
        self._trigger = _MonoSelectTrigger()
        sl.addWidget(self._trigger, stretch=1, alignment=_FIELD_ALIGN)
        self._chevron = _AnimatedChevron()
        sl.addWidget(self._chevron, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._shell)

        self._shell.bind_focus(self._trigger)
        self._trigger.activate.connect(self._show_popup_toggle)
        self._shell.toggle_requested.connect(self._show_popup_toggle)
        self._chevron.clicked.connect(self._show_popup_toggle)
        self._trigger.installEventFilter(self)
        self.set_options(options or [], current_value=current_value)

    def setMinimumWidth(self, w: int) -> None:  # noqa: N802
        self._min_width = w
        super().setMinimumWidth(w)

    def options(self) -> list[MonoSelectOption]:
        return list(self._options)

    def set_options(
        self,
        options: list[MonoSelectOption] | list[str],
        *,
        current_value: str | None = None,
    ) -> None:
        if options and isinstance(options[0], str):
            parsed = mono_select_options_from_strings(
                options,  # type: ignore[arg-type]
                icon_map=_ASSET_TYPE_ICONS,
            )
        else:
            parsed = list(options)  # type: ignore[arg-type]
        self._options = parsed
        if not self._options:
            self._index = 0
            self._refresh_trigger()
            return
        if current_value is not None:
            self._index = self._index_for_value(current_value)
        else:
            self._index = min(self._index, len(self._options) - 1)
        self._refresh_trigger()

    def _index_for_value(self, value: str) -> int:
        for i, opt in enumerate(self._options):
            if opt.value == value:
                return i
        return 0

    def _option_at(self, index: int) -> MonoSelectOption:
        return self._options[index]

    def _refresh_trigger(self) -> None:
        if not self._options:
            self._trigger.set_display(label="", icon=None)
            return
        opt = self._option_at(self._index)
        self._trigger.set_display(label=opt.label, icon=opt.icon)

    def current_index(self) -> int:
        return self._index

    def current_value(self) -> str:
        if not self._options:
            return ""
        return self._option_at(self._index).value

    def currentText(self) -> str:  # noqa: N802 — legacy harness alias
        if not self._options:
            return ""
        opt = self._option_at(self._index)
        if opt.secondary and opt.secondary.startswith("Prefix: "):
            return f"{opt.label} — {opt.secondary.removeprefix('Prefix: ')}"
        return opt.label

    def set_current_index(self, index: int) -> None:
        if not self._options:
            return
        index = max(0, min(index, len(self._options) - 1))
        if index == self._index:
            return
        self._index = index
        self._refresh_trigger()
        opt = self._option_at(self._index)
        self.value_changed.emit(opt.value)
        self.currentIndexChanged.emit(self._index)
        self.currentTextChanged.emit(self.currentText())

    def set_current_value(self, value: str) -> None:
        self.set_current_index(self._index_for_value(value))

    def _ensure_popup(self) -> _MonoSelectPopup:
        if self._popup is None:
            host = self.window()
            if host is None:
                raise RuntimeError("MonoSelect must be in a window before opening")
            self._popup = _MonoSelectPopup(self, host)
            self._popup.option_chosen.connect(self._on_option_chosen)
            self._popup.opened.connect(self._on_popup_opened)
            self._popup.closed.connect(self._on_popup_closed)
        return self._popup

    def _sync_popup_outline(self) -> None:
        popup = self._popup
        if popup is not None and popup.isVisible():
            popup.update()
            popup._card.update()

    def _on_popup_opened(self) -> None:
        self._chevron.set_open(True)
        if isinstance(self._shell, _MonoSelectShell):
            self._shell.set_expanded(True)

    def _on_popup_closed(self) -> None:
        self._chevron.set_open(False)
        if isinstance(self._shell, _MonoSelectShell):
            self._shell.set_expanded(False)
        if not self._trigger.hasFocus():
            self._shell.set_focused(False)
        self._remove_outside_filter()

    def _install_outside_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and not self._outside_filter_installed:
            app.installEventFilter(self)
            self._outside_filter_installed = True

    def _remove_outside_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and self._outside_filter_installed:
            app.removeEventFilter(self)
            self._outside_filter_installed = False

    def _contains_global_point(self, widget: QWidget | None, global_pos: QPoint) -> bool:
        if widget is None or not widget.isVisible():
            return False
        top_left = widget.mapToGlobal(QPoint(0, 0))
        return QRectF(top_left.x(), top_left.y(), widget.width(), widget.height()).contains(
            global_pos.x(), global_pos.y()
        )

    def _is_popup_hit(self, global_pos: QPoint) -> bool:
        popup = self._popup
        if popup is not None and self._contains_global_point(popup, global_pos):
            return True
        return self._contains_global_point(self._shell, global_pos)

    def _show_popup_toggle(self) -> None:
        popup = self._ensure_popup()
        if popup.isVisible():
            popup.hide()
            return
        self._shell.set_focused(True)
        self._trigger.setFocus(Qt.FocusReason.MouseFocusReason)
        self._open_popup()

    def _open_popup(self) -> None:
        if not self._options:
            return
        popup = self._ensure_popup()
        if popup.isVisible():
            return
        popup.set_options(
            self._options,
            current_value=self.current_value(),
            highlight_index=self._index,
        )
        popup.show_for_anchor(self._shell)
        self._install_outside_filter()
        self._shell.set_focused(True)
        self._sync_popup_outline()

    def _on_option_chosen(self, value: str) -> None:
        self.set_current_value(value)
        self._trigger.setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        popup = self._popup
        if popup is not None and popup.isVisible() and self._outside_filter_installed:
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton:
                    if not self._is_popup_hit(event.globalPosition().toPoint()):
                        popup.hide()

        if obj is self._trigger:
            if event.type() == QEvent.Type.FocusIn:
                self._shell.set_focused(True)
                self._sync_popup_outline()
            elif event.type() == QEvent.Type.FocusOut:
                popup = self._popup
                if popup is None or not popup.isVisible():
                    self._shell.set_focused(False)
                else:
                    self._sync_popup_outline()
        return super().eventFilter(obj, event)


__all__ = [
    "MonoSelect",
    "MonoSelectItem",
    "MonoSelectOption",
    "mono_select_options_from_strings",
]
