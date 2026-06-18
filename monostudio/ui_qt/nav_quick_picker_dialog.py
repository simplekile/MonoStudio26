"""Ctrl+` quick view picker — Task View-style 3×3 grid of nav bookmarks."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QSettings, Signal, QSize, QTimer, QPoint, QMimeData
from PySide6.QtGui import (
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QColor,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.app_hotkeys import bind_hotkey, format_hotkey_display
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.nav_quick_view import (
    SLOT_COUNT,
    clear_nav_quick_slot,
    describe_nav_quick_slot,
    exchange_nav_quick_slots,
    load_nav_quick_slot,
)
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font

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
    "Delivery": "package",
    "Trash": "trash-2",
}

_MIME_SLOT = "application/x-monos-nav-quick-slot"
_DRAG_GHOST_OPACITY = 0.72

_GRID_COLS = 3
_TILE_W = 200
_TILE_H = 118
_TILE_GAP = 8
_PANEL_PAD_H = 16
_PANEL_PAD_V = 16
_PANEL_PAD_BOTTOM = 20
_ICON_SIZE = 32
_MARGIN_SCREEN = 40
_BACKDROP_ALPHA = 72


def _slot_from_mime(mime: QMimeData) -> int | None:
    if not mime.hasFormat(_MIME_SLOT):
        return None
    try:
        return int(bytes(mime.data(_MIME_SLOT)).decode("ascii"))
    except (TypeError, ValueError):
        return None


def _drag_ghost_pixmap(widget: QWidget) -> QPixmap:
    source = widget.grab()
    ghost = QPixmap(source.size())
    ghost.fill(Qt.GlobalColor.transparent)
    painter = QPainter(ghost)
    painter.setOpacity(_DRAG_GHOST_OPACITY)
    painter.drawPixmap(0, 0, source)
    painter.end()
    return ghost


class _SpotlightBackdrop(QWidget):
    """Fullscreen dim layer; left-click outside the panel closes the picker."""

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
        painter.fillRect(self.rect(), QColor(0, 0, 0, _BACKDROP_ALPHA))
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


class _NavQuickTile(QFrame):
    """Single quick-view slot card in the picker grid."""

    clicked = Signal(int)
    clear_requested = Signal(int)
    drop_requested = Signal(int, int)

    def __init__(self, slot: int, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slot = slot
        self._settings = settings
        self._filled = False
        self._press_pos: QPoint | None = None
        self._drag_started = False
        self.setObjectName("NavQuickTile")
        self.setProperty("slot", slot)
        self.setProperty("filled", False)
        self.setProperty("dragging", False)
        self.setProperty("dropTarget", False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAcceptDrops(True)
        self.setFixedSize(_TILE_W, _TILE_H)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(4)

        header = QWidget(self)
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(0, 0, 0, 0)
        header_l.setSpacing(4)

        self._slot_label = QLabel(str(slot), header)
        self._slot_label.setObjectName("NavQuickTileSlot")
        self._slot_label.setFont(monos_font("JetBrains Mono", 11))
        header_l.addWidget(self._slot_label, 0, Qt.AlignmentFlag.AlignLeft)

        header_l.addStretch(1)

        self._clear_btn = QToolButton(header)
        self._clear_btn.setObjectName("NavQuickTileClearBtn")
        self._clear_btn.setIcon(lucide_icon("x", size=14, color_hex=MONOS_COLORS["text_label"]))
        self._clear_btn.setIconSize(QSize(14, 14))
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setToolTip("Remove bookmark")
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        header_l.addWidget(self._clear_btn, 0, Qt.AlignmentFlag.AlignRight)

        root.addWidget(header, 0)

        self._icon = QLabel(self)
        self._icon.setFixedHeight(_ICON_SIZE)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignHCenter)

        self._title = QLabel("", self)
        self._title.setObjectName("NavQuickTileTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self._title)

        self._subtitle = QLabel("", self)
        self._subtitle.setObjectName("NavQuickTileSubtitle")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._subtitle.setWordWrap(True)
        self._subtitle.setMaximumHeight(32)
        root.addWidget(self._subtitle)

        self.set_empty()

    def slot(self) -> int:
        return self._slot

    def is_filled(self) -> bool:
        return self._filled

    def set_drop_target(self, active: bool) -> None:
        self.setProperty("dropTarget", bool(active))
        self._repolish()

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_payload(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            self.set_empty()
            return
        ctx = (payload.get("context") or "").strip() or "?"
        summary = describe_nav_quick_slot(payload)
        icon_name = _PAGE_ICONS.get(ctx, "pin")
        icon = lucide_icon(icon_name, size=_ICON_SIZE, color_hex=MONOS_COLORS["text_label"])
        self._icon.setPixmap(icon.pixmap(_ICON_SIZE, _ICON_SIZE))
        self._title.setText(ctx)
        filter_bits = summary
        if filter_bits.startswith(ctx):
            rest = filter_bits[len(ctx) :].lstrip(" ·")
            filter_bits = rest or ctx
        self._subtitle.setText(filter_bits if filter_bits != ctx else "")
        self._filled = True
        self.setProperty("filled", True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._repolish()
        self._sync_clear_btn_visible()

    def set_empty(self) -> None:
        assign_hint = format_hotkey_display(self._settings, f"nav_quick.assign.{self._slot}")
        self._icon.clear()
        self._title.setText("Empty")
        self._subtitle.setText(f"{assign_hint} to save")
        self._filled = False
        self.setProperty("filled", False)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._clear_btn.setVisible(False)
        self._repolish()

    def _on_clear_clicked(self) -> None:
        self.clear_requested.emit(self._slot)

    def _sync_clear_btn_visible(self) -> None:
        if not self._filled:
            self._clear_btn.setVisible(False)
            return
        under = self.underMouse() or self._clear_btn.underMouse()
        self._clear_btn.setVisible(bool(under))

    def _is_clear_btn_hit(self, pos: QPoint) -> bool:
        if not self._clear_btn.isVisible():
            return False
        local = self._clear_btn.mapFrom(self, pos)
        return self._clear_btn.rect().contains(local)

    def _start_drag(self, event: QMouseEvent) -> None:
        mime = QMimeData()
        mime.setData(_MIME_SLOT, str(self._slot).encode("ascii"))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(_drag_ghost_pixmap(self))
        drag.setHotSpot(event.pos())

        self.setProperty("dragging", True)
        self._repolish()
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.setProperty("dragging", False)
            self._repolish()

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._filled:
            self._clear_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        QTimer.singleShot(0, self._sync_clear_btn_visible)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_clear_btn_hit(event.pos()):
                return super().mousePressEvent(event)
            self._press_pos = event.pos()
            self._drag_started = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            not self._filled
            or self._press_pos is None
            or not (event.buttons() & Qt.MouseButton.LeftButton)
            or self._is_clear_btn_hit(self._press_pos)
        ):
            super().mouseMoveEvent(event)
            return
        if (event.pos() - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
            self._drag_started = True
            self._press_pos = None
            self._start_drag(event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._drag_started
            and self._filled
            and self._press_pos is not None
            and not self._is_clear_btn_hit(event.pos())
        ):
            self.clicked.emit(self._slot)
            event.accept()
            self._press_pos = None
            return
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        from_slot = _slot_from_mime(event.mimeData())
        if from_slot is not None and from_slot != self._slot:
            event.acceptProposedAction()
            self.set_drop_target(True)
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        from_slot = _slot_from_mime(event.mimeData())
        if from_slot is not None and from_slot != self._slot:
            event.acceptProposedAction()
            self.set_drop_target(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_drop_target(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        from_slot = _slot_from_mime(event.mimeData())
        self.set_drop_target(False)
        if from_slot is not None and from_slot != self._slot:
            self.drop_requested.emit(from_slot, self._slot)
            event.acceptProposedAction()
            return
        event.ignore()


class NavQuickPickerDialog(MonosDialog):
    """Full-window overlay with a 3×3 grid of quick-view bookmarks."""

    slot_selected = Signal(object)
    slot_cleared = Signal(int)
    slots_changed = Signal()

    def __init__(self, *, settings: QSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick view")
        self.set_host_dim_overlay_enabled(False)
        self._settings = settings
        self._payloads: dict[int, dict[str, Any]] = {}
        self._tiles: dict[int, _NavQuickTile] = {}

        self._backdrop = _SpotlightBackdrop(self)
        self._panel = QWidget(self._backdrop)
        self._panel.setObjectName("NavQuickPickerPanel")
        self._panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._backdrop.set_panel(self._panel)
        self._backdrop.set_dismiss(self.reject)

        root = QVBoxLayout(self._panel)
        root.setContentsMargins(_PANEL_PAD_H, _PANEL_PAD_V, _PANEL_PAD_H, _PANEL_PAD_BOTTOM)
        root.setSpacing(12)

        title = QLabel("Quick view", self._panel)
        title.setObjectName("NavQuickPickerTitle")
        root.addWidget(title)

        grid_host = QWidget(self._panel)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(_TILE_GAP)
        grid.setVerticalSpacing(_TILE_GAP)

        for slot in range(1, SLOT_COUNT + 1):
            tile = _NavQuickTile(slot, settings, grid_host)
            payload = load_nav_quick_slot(settings, slot)
            if payload is not None:
                self._payloads[slot] = payload
                tile.set_payload(payload)
            tile.clicked.connect(self._on_tile_clicked)
            tile.clear_requested.connect(self._on_tile_clear)
            tile.drop_requested.connect(self._on_tile_drop)
            self._tiles[slot] = tile
            row = (slot - 1) // _GRID_COLS
            col = (slot - 1) % _GRID_COLS
            grid.addWidget(tile, row, col)

        root.addWidget(grid_host)

        picker_key = format_hotkey_display(settings, "global.nav_quick_picker")
        self._hint = QLabel(
            f"1–9 jump · drag to swap · {picker_key} close · Esc",
            self._panel,
        )
        self._hint.setObjectName("DialogHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._hint)

        bind_hotkey(
            settings,
            "global.nav_quick_picker",
            self,
            self.reject,
            context=Qt.ShortcutContext.WindowShortcut,
            auto_repeat=False,
        )

        self._panel.adjustSize()
        panel_w = _GRID_COLS * _TILE_W + (_GRID_COLS - 1) * _TILE_GAP + _PANEL_PAD_H * 2
        grid_h = 3 * _TILE_H + 2 * _TILE_GAP
        panel_h = (
            _PANEL_PAD_V
            + _PANEL_PAD_BOTTOM
            + title.sizeHint().height()
            + root.spacing()
            + grid_h
            + root.spacing()
            + self._hint.sizeHint().height()
        )
        self._panel.setFixedSize(max(panel_w, self._panel.sizeHint().width()), panel_h)

    def _apply_slot_payload(self, slot: int, payload: dict[str, Any] | None) -> None:
        if payload is None:
            self._payloads.pop(slot, None)
            self._tiles[slot].set_empty()
        else:
            self._payloads[slot] = payload
            self._tiles[slot].set_payload(payload)

    def _on_tile_clicked(self, slot: int) -> None:
        self._activate_slot(slot)

    def _on_tile_clear(self, slot: int) -> None:
        clear_nav_quick_slot(self._settings, slot)
        self._apply_slot_payload(slot, None)
        self.slot_cleared.emit(slot)
        self.slots_changed.emit()

    def _on_tile_drop(self, from_slot: int, to_slot: int) -> None:
        if from_slot == to_slot or from_slot not in self._payloads:
            return
        exchange_nav_quick_slots(self._settings, from_slot, to_slot)
        src = self._payloads.get(from_slot)
        dst = self._payloads.get(to_slot)
        self._apply_slot_payload(from_slot, dst)
        self._apply_slot_payload(to_slot, src)
        self.slots_changed.emit()

    def _activate_slot(self, slot: int) -> None:
        payload = self._payloads.get(slot)
        if payload is None:
            return
        self.slot_selected.emit(payload)
        self.accept()

    def _slot_from_key(self, key: int) -> int | None:
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            return key - Qt.Key.Key_0
        return None

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        slot = self._slot_from_key(key)
        if slot is not None and 1 <= slot <= SLOT_COUNT:
            self._activate_slot(slot)
            event.accept()
            return
        super().keyPressEvent(event)

    def _top_level_host(self) -> QWidget | None:
        w: QWidget | None = self.parentWidget()
        top: QWidget | None = w
        while w is not None:
            top = w
            w = w.parentWidget()
        return top

    def _sync_layout(self) -> None:
        self._backdrop.setGeometry(self.rect())
        pw = self._panel.width()
        ph = self._panel.height()
        x = max(0, (self.width() - pw) // 2)
        y = max(_MARGIN_SCREEN, (self.height() - ph) // 2)
        y = min(y, max(_MARGIN_SCREEN, self.height() - ph - _MARGIN_SCREEN))
        self._panel.setGeometry(x, y, pw, ph)
        self._backdrop.lower()
        self._panel.raise_()

    def showEvent(self, event) -> None:  # noqa: N802
        host = self._top_level_host()
        if host is not None:
            self.setGeometry(host.frameGeometry())
        QDialog.showEvent(self, event)
        self._sync_layout()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def resizeEvent(self, event) -> None:  # noqa: N802
        QDialog.resizeEvent(self, event)
        self._sync_layout()

    def paintEvent(self, event) -> None:  # noqa: N802
        QDialog.paintEvent(self, event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._panel.sizeHint()
