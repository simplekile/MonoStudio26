"""Middle-mouse pipeline item drag preview — shared by grid, list, and inspector."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QApplication, QWidget

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.view_items import ViewItem, display_name_for_item

_DRAG_MAX_W = 220
_DRAG_MIN_W = 120
_DRAG_WIDTH_SCALE = 0.62
_INNER_PAD = 8
_GAP_THUMB_TEXT = 8
_STACK_OFFSET = 10
_HOTSPOT = QPoint(24, 24)
_DEFAULT_CARD = QSize(320, 260)


def pipeline_drag_hotspot() -> QPoint:
    return QPoint(_HOTSPOT)


def pipeline_drag_target_size(base_rect: QRect) -> QSize:
    """Compact card size derived from live grid cell dimensions."""
    if not base_rect.isValid() or base_rect.isEmpty():
        return QSize(_DRAG_MIN_W, 1)
    base_logical_w = max(1, int(base_rect.width()))
    target_w = min(_DRAG_MAX_W, max(_DRAG_MIN_W, int(base_logical_w * _DRAG_WIDTH_SCALE)))
    name_font = QFont("Inter")
    name_font.setPointSize(11)
    name_font.setWeight(QFont.Weight.DemiBold)
    name_h = max(14, QFontMetrics(name_font).height())
    inner_w = max(1, target_w - _INNER_PAD * 2)
    thumb_h = max(1, int(inner_w * 9 / 16))
    content_h = _INNER_PAD + thumb_h + _GAP_THUMB_TEXT + name_h + _INNER_PAD
    max_h = max(1, int(base_rect.height() * (target_w / base_logical_w)))
    target_h = min(max_h, content_h)
    return QSize(target_w, max(1, target_h))


def _card_size_from_delegate(delegate) -> QSize | None:
    sz = getattr(delegate, "card_size", None)
    if isinstance(sz, QSize) and sz.isValid() and sz.width() > 0 and sz.height() > 0:
        return sz
    return None


def resolve_grid_card_base_rect(widget: QWidget) -> QRect:
    """Grid card dimensions from MainView (slider scale), for drag preview parity."""
    seen: set[int] = set()
    queue: list[QWidget] = []
    node: QWidget | None = widget
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        queue.append(node)
        node = node.parentWidget()
    win = widget.window()
    if isinstance(win, QWidget) and id(win) not in seen:
        queue.append(win)

    for candidate in queue:
        main_view = getattr(candidate, "_main_view", None)
        if main_view is not None:
            sz = _card_size_from_delegate(getattr(main_view, "_grid_delegate", None))
            if sz is not None:
                return QRect(0, 0, sz.width(), sz.height())
        sz = _card_size_from_delegate(getattr(candidate, "_grid_delegate", None))
        if sz is not None:
            return QRect(0, 0, sz.width(), sz.height())

    return QRect(0, 0, _DEFAULT_CARD.width(), _DEFAULT_CARD.height())


def _grid_card_size_from_view(view: QAbstractItemView) -> QSize:
    model = view.model()
    parent = model.parent() if model is not None else None
    if parent is not None:
        sz = _card_size_from_delegate(getattr(parent, "_grid_delegate", None))
        if sz is not None:
            return sz
    return _DEFAULT_CARD


def drag_base_rect_for_view(view: QAbstractItemView, first_index) -> QRect:
    if getattr(view, "uses_grid_card_drag_preview", False):
        return resolve_grid_card_base_rect(view)
    try:
        return view.visualRect(first_index)
    except Exception:
        return QRect()


def render_pipeline_drag_card_pixmap(
    *,
    logical_size: QSize,
    dpr: float,
    name: str,
    thumb_pixmap: QPixmap | None = None,
    thumb_icon: QIcon | None = None,
    folder_fallback: bool = False,
) -> QPixmap | None:
    w = max(1, int(logical_size.width() * dpr))
    h = max(1, int(logical_size.height() * dpr))
    pm = QPixmap(w, h)
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        card = QRect(0, 0, logical_size.width(), logical_size.height())
        painter.setPen(QPen(QColor("#3f3f46"), 1))
        painter.setBrush(QColor("#18181b"))
        painter.drawRoundedRect(card.adjusted(0, 0, -1, -1), 12, 12)

        inner = card.adjusted(_INNER_PAD, _INNER_PAD, -_INNER_PAD, -_INNER_PAD)
        if inner.width() > 0 and inner.height() > 0:
            thumb_w = inner.width()
            thumb_h = max(1, int(thumb_w * 9 / 16))
            thumb = QRect(inner.left(), inner.top(), thumb_w, min(thumb_h, inner.height()))

            src_pix: QPixmap | None = None
            if thumb_pixmap is not None and not thumb_pixmap.isNull():
                src_pix = thumb_pixmap
            elif isinstance(thumb_icon, QIcon):
                src_pix = thumb_icon.pixmap(256, 256)

            if src_pix is not None and not src_pix.isNull():
                scaled = src_pix.scaled(
                    thumb.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                sx = max(0, (scaled.width() - thumb.width()) // 2)
                sy = max(0, (scaled.height() - thumb.height()) // 2)
                crop = scaled.copy(QRect(QPoint(sx, sy), thumb.size()))
                painter.drawPixmap(thumb, crop)
            elif folder_fallback:
                painter.fillRect(thumb, QColor(MONOS_COLORS["content_bg"]))
                fc = lucide_icon("folder", size=44, color_hex=MONOS_COLORS["text_meta"])
                fp = fc.pixmap(44, 44)
                if not fp.isNull():
                    painter.drawPixmap(
                        thumb.center().x() - 22,
                        thumb.center().y() - 22,
                        fp,
                    )

            label = (name or "").strip() or "—"
            text_rect = QRect(
                inner.left(),
                thumb.bottom() + _GAP_THUMB_TEXT,
                inner.width(),
                max(1, inner.bottom() - (thumb.bottom() + _GAP_THUMB_TEXT)),
            )
            font = QFont("Inter")
            font.setPointSize(11)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor("#e4e4e7"))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, label)
    finally:
        painter.end()
    return pm if not pm.isNull() else None


def render_pipeline_drag_card(index, *, logical_size: QSize, dpr: float) -> QPixmap | None:
    if not index.isValid():
        return None
    icon = index.data(Qt.ItemDataRole.DecorationRole)
    item = index.data(Qt.ItemDataRole.UserRole)
    name = display_name_for_item(item) if isinstance(item, ViewItem) else None
    return render_pipeline_drag_card_pixmap(
        logical_size=logical_size,
        dpr=dpr,
        name=(name or "").strip() or "—",
        thumb_icon=icon if isinstance(icon, QIcon) else None,
    )


def build_single_pipeline_drag_pixmap(
    source: QWidget,
    *,
    base_rect: QRect,
    name: str,
    thumb_pixmap: QPixmap | None = None,
    thumb_icon: QIcon | None = None,
    folder_fallback: bool = False,
) -> tuple[QPixmap | None, QPoint]:
    dpr = float(getattr(source, "devicePixelRatioF", lambda: 1.0)())
    if dpr <= 0:
        dpr = 1.0
    target_size = pipeline_drag_target_size(base_rect)
    pm = render_pipeline_drag_card_pixmap(
        logical_size=target_size,
        dpr=dpr,
        name=name,
        thumb_pixmap=thumb_pixmap,
        thumb_icon=thumb_icon,
        folder_fallback=folder_fallback,
    )
    return pm, _HOTSPOT


def build_pipeline_drag_pixmap(
    indexes,
    source: QWidget,
    *,
    base_rect: QRect,
) -> tuple[QPixmap | None, QPoint]:
    if not indexes:
        return None, _HOTSPOT
    dpr = float(getattr(source, "devicePixelRatioF", lambda: 1.0)())
    if dpr <= 0:
        dpr = 1.0
    target_size = pipeline_drag_target_size(base_rect)

    cards: list[QPixmap] = []
    for idx in indexes[:3]:
        card = render_pipeline_drag_card(idx, logical_size=target_size, dpr=dpr)
        if card is not None:
            cards.append(card)
    if not cards:
        return None, _HOTSPOT

    layers = len(cards)
    out_w = int(cards[0].width() / dpr) + _STACK_OFFSET * (layers - 1)
    out_h = int(cards[0].height() / dpr) + _STACK_OFFSET * (layers - 1)
    out = QPixmap(max(1, int(out_w * dpr)), max(1, int(out_h * dpr)))
    out.setDevicePixelRatio(dpr)
    out.fill(QColor(0, 0, 0, 0))

    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i in range(layers - 1, -1, -1):
            painter.setOpacity(0.78 if i > 0 else 1.0)
            painter.drawPixmap(_STACK_OFFSET * i, _STACK_OFFSET * i, cards[i])
        painter.setOpacity(1.0)

        total = len(indexes)
        if total > 1:
            badge = str(total)
            font = QFont("Inter")
            font.setPointSize(10)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            fm = QFontMetrics(font)
            pad_x, pad_y = 8, 4
            bw = fm.horizontalAdvance(badge) + pad_x * 2
            bh = fm.height() + pad_y * 2
            bx = int(out.width() / max(dpr, 1.0)) - bw - 6
            by = 6
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(37, 99, 235, 220))
            painter.drawRoundedRect(QRect(bx, by, bw, bh), 9, 9)
            painter.setPen(QColor("#fafafa"))
            painter.drawText(QRect(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, badge)
    finally:
        painter.end()
    return out, _HOTSPOT


def start_pipeline_item_drag(view: QAbstractItemView, supportedActions) -> None:
    buttons = QApplication.mouseButtons()
    if not (buttons & Qt.MouseButton.MiddleButton) or (buttons & Qt.MouseButton.LeftButton):
        return
    model = view.model()
    if model is None:
        return
    indexes = view.selectedIndexes()
    if not indexes:
        return
    mime = model.mimeData(indexes)
    if mime is None:
        return

    base_rect = drag_base_rect_for_view(view, indexes[0])
    if not base_rect.isValid() or base_rect.isEmpty():
        return

    pixmap, hot_spot = build_pipeline_drag_pixmap(indexes, view, base_rect=base_rect)
    if pixmap is None or pixmap.isNull():
        return

    drag = QDrag(view)
    drag.setMimeData(mime)
    drag.setPixmap(pixmap)
    drag.setHotSpot(hot_spot)
    delegate = view.itemDelegate()
    # List view repaints all row backgrounds in paintEvent; toggling fast_paint there causes a
    # visible hitch before drag.exec (grid cards do not). Rubber-band still sets fast_paint on list.
    use_fast_paint = (
        hasattr(delegate, "set_fast_paint")
        and not getattr(view, "uses_grid_card_drag_preview", False)
    )
    if use_fast_paint:
        delegate.set_fast_paint(True)
    try:
        drag.exec(supportedActions)
    finally:
        if use_fast_paint:
            delegate.set_fast_paint(False)
