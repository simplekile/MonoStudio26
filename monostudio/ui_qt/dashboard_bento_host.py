"""Dynamic bento grid host for Dashboard — layout, edit mode, drag-and-drop."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.dashboard_layout import (
    DASHBOARD_WIDGET_LABELS,
    LOCKED_FULL_WIDTH_IDS,
    MIME_WIDGET_TYPE,
    BentoPlacement,
    DashboardWidgetSlot,
    DEFAULT_LAYOUT,
    hidden_widget_ids,
    pack_bento_placements,
    reorder_slot,
    set_slot_visible,
    toggle_slot_span,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosMenu

_MAX_DROP_ZONES = 12


class DashboardDropIndicator(QFrame):
    """Thin blue bar shown while dragging over a drop target."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardDropIndicator")
        self.setFixedHeight(4)
        self.setVisible(False)


class DashboardBentoChrome(QFrame):
    """Wraps a dashboard widget with optional edit chrome (handle, span, hide)."""

    hide_requested = Signal(str)
    span_toggle_requested = Signal(str)
    drop_before_requested = Signal(str, str)  # dragged_id, target_id

    def __init__(
        self,
        widget_id: str,
        inner: QWidget,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._widget_id = widget_id
        self._inner = inner
        self._edit_mode = False
        self._drag_start: QPoint | None = None
        self.setObjectName("DashboardBentoChrome")
        self.setProperty("editMode", "false")
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._toolbar = QWidget(self)
        self._toolbar.setObjectName("DashboardBentoToolbar")
        self._toolbar.setVisible(False)
        tb = QHBoxLayout(self._toolbar)
        tb.setContentsMargins(8, 6, 8, 4)
        tb.setSpacing(6)

        self._handle = QToolButton(self._toolbar)
        self._handle.setObjectName("DashboardBentoDragHandle")
        self._handle.setIcon(lucide_icon("grip-vertical", size=14, color_hex="#71717a"))
        self._handle.setCursor(Qt.CursorShape.OpenHandCursor)
        self._handle.setToolTip("Drag to reorder")
        self._handle.setAutoRaise(True)

        self._label = QLabel(DASHBOARD_WIDGET_LABELS.get(widget_id, widget_id), self._toolbar)
        self._label.setObjectName("DashboardBentoChromeLabel")

        tb.addWidget(self._handle)
        tb.addWidget(self._label, 1)

        self._btn_span = QToolButton(self._toolbar)
        self._btn_span.setObjectName("DashboardBentoChromeBtn")
        self._btn_span.setAutoRaise(True)
        self._btn_span.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_span.setToolTip("Toggle half / full width")
        self._btn_span.clicked.connect(lambda: self.span_toggle_requested.emit(self._widget_id))

        self._btn_hide = QToolButton(self._toolbar)
        self._btn_hide.setObjectName("DashboardBentoChromeBtn")
        self._btn_hide.setIcon(lucide_icon("eye-off", size=14, color_hex="#a1a1aa"))
        self._btn_hide.setAutoRaise(True)
        self._btn_hide.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_hide.setToolTip("Hide widget")
        self._btn_hide.clicked.connect(lambda: self.hide_requested.emit(self._widget_id))

        tb.addWidget(self._btn_span)
        tb.addWidget(self._btn_hide)
        root.addWidget(self._toolbar)

        self._content = QWidget(self)
        content_lay = QVBoxLayout(self._content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        inner.setParent(self._content)
        content_lay.addWidget(inner)
        root.addWidget(self._content, 1)

        self._handle.installEventFilter(self)

    def widget_id(self) -> str:
        return self._widget_id

    def set_span(self, span: int) -> None:
        locked = self._widget_id in LOCKED_FULL_WIDTH_IDS
        if locked or span >= 2:
            self._btn_span.setIcon(lucide_icon("columns-2", size=14, color_hex="#60a5fa"))
            self._btn_span.setToolTip("Full width")
        else:
            self._btn_span.setIcon(lucide_icon("square", size=14, color_hex="#a1a1aa"))
            self._btn_span.setToolTip("Half width — click for full width")
        self._btn_span.setVisible(not locked)

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._toolbar.setVisible(enabled)
        self.setProperty("editMode", "true" if enabled else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self._handle and self._edit_mode:
            if event.type() == event.Type.MouseButtonPress:
                me = event
                if isinstance(me, QMouseEvent) and me.button() == Qt.MouseButton.LeftButton:
                    self._drag_start = me.position().toPoint()
                    return True
            if event.type() == event.Type.MouseMove:
                me = event
                if (
                    isinstance(me, QMouseEvent)
                    and self._drag_start is not None
                    and (me.buttons() & Qt.MouseButton.LeftButton)
                ):
                    if (me.position().toPoint() - self._drag_start).manhattanLength() >= 8:
                        self._start_drag()
                        self._drag_start = None
                    return True
            if event.type() == event.Type.MouseButtonRelease:
                self._drag_start = None
        return super().eventFilter(watched, event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        payload = QByteArray(self._widget_id.encode("utf-8"))
        drag.setMimeData(self._make_mime(payload))
        # Small drag pixmap — avoid grabbing the full card (heavy + flicker).
        ghost = QLabel(self._label.text())
        ghost.setObjectName("DashboardBentoChromeLabel")
        ghost.adjustSize()
        pixmap = ghost.grab()
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    @staticmethod
    def _make_mime(payload: QByteArray):
        from PySide6.QtCore import QMimeData

        mime = QMimeData()
        mime.setData(MIME_WIDGET_TYPE, payload)
        return mime

    @staticmethod
    def dragged_id_from_mime(mime) -> str | None:
        if mime is None or not mime.hasFormat(MIME_WIDGET_TYPE):
            return None
        raw = bytes(mime.data(MIME_WIDGET_TYPE)).decode("utf-8", errors="replace").strip()
        return raw or None

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if not self._edit_mode:
            event.ignore()
            return
        wid = self.dragged_id_from_mime(event.mimeData())
        if wid and wid != self._widget_id:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if not self._edit_mode:
            event.ignore()
            return
        wid = self.dragged_id_from_mime(event.mimeData())
        if wid and wid != self._widget_id:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        dragged = self.dragged_id_from_mime(event.mimeData())
        if dragged and dragged != self._widget_id:
            self.drop_before_requested.emit(dragged, self._widget_id)
            event.acceptProposedAction()
        else:
            event.ignore()


class DashboardBentoHost(QWidget):
    """Hosts dashboard widgets in a packable 2-column bento grid."""

    layout_changed = Signal(object)  # list[DashboardWidgetSlot]
    layout_committed = Signal(object)  # persist immediately (reset, done)
    edit_mode_changed = Signal(bool)

    def __init__(
        self,
        widget_map: dict[str, QWidget],
        *,
        initial_slots: list[DashboardWidgetSlot] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._widget_map = widget_map
        self._slots: list[DashboardWidgetSlot] = [
            DashboardWidgetSlot(s.id, s.span, s.visible) for s in DEFAULT_LAYOUT
        ]
        self._edit_mode = False
        self._chrome_by_id: dict[str, DashboardBentoChrome] = {}
        self._drop_zone_pool: list[_DashboardRowDropZone] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._edit_bar = QWidget(self)
        self._edit_bar.setObjectName("DashboardEditBar")
        self._edit_bar.setVisible(False)
        bar = QHBoxLayout(self._edit_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)

        hint = QLabel("Drag widgets to reorder. Toggle width or hide cards.", self._edit_bar)
        hint.setObjectName("DialogHint")
        bar.addWidget(hint, 1)

        self._btn_add = QPushButton("Add widget", self._edit_bar)
        self._btn_add.setObjectName("DashboardGhostButton")
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.clicked.connect(self._show_add_menu)

        self._btn_reset = QPushButton("Reset layout", self._edit_bar)
        self._btn_reset.setObjectName("DashboardGhostButton")
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.clicked.connect(self._confirm_reset)

        self._btn_done = QPushButton("Done", self._edit_bar)
        self._btn_done.setObjectName("DashboardPrimaryButton")
        self._btn_done.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_done.clicked.connect(self._finish_edit)

        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_reset)
        bar.addWidget(self._btn_done)
        root.addWidget(self._edit_bar)

        self._empty_layout_hint = QLabel(
            "No widgets visible. Click Customize, then Add widget to restore cards.",
            self,
        )
        self._empty_layout_hint.setObjectName("DashboardEmptyHint")
        self._empty_layout_hint.setWordWrap(True)
        self._empty_layout_hint.setVisible(False)
        root.addWidget(self._empty_layout_hint)

        self._grid_host = QWidget(self)
        self._grid_host.setAcceptDrops(True)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        root.addWidget(self._grid_host, 1)

        self._build_chrome()
        self.apply_layout(initial_slots if initial_slots is not None else self._slots)

    def _build_chrome(self) -> None:
        for wid, widget in self._widget_map.items():
            chrome = DashboardBentoChrome(wid, widget, parent=self._grid_host)
            chrome.hide()
            chrome.hide_requested.connect(self._on_hide_requested)
            chrome.span_toggle_requested.connect(self._on_span_toggle)
            chrome.drop_before_requested.connect(self._on_drop_before)
            self._chrome_by_id[wid] = chrome

    def _stash_widget(self, widget: QWidget | None) -> None:
        """Detach from grid without creating a transient top-level window."""
        if widget is None:
            return
        widget.setParent(self._grid_host)
        widget.hide()

    def _detach_grid_widgets(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            self._stash_widget(item.widget())

    def _drop_zone_at(self, index: int) -> _DashboardRowDropZone:
        while len(self._drop_zone_pool) <= index:
            zone = _DashboardRowDropZone(before_widget_id=None, host=self, parent=self._grid_host)
            zone.hide()
            self._drop_zone_pool.append(zone)
        return self._drop_zone_pool[index]

    def _hide_unused_drop_zones(self, used_count: int) -> None:
        for i in range(used_count, len(self._drop_zone_pool)):
            self._drop_zone_pool[i].hide()

    def slots(self) -> list[DashboardWidgetSlot]:
        return [DashboardWidgetSlot(s.id, s.span, s.visible) for s in self._slots]

    def set_slots(self, slots: list[DashboardWidgetSlot]) -> None:
        self.apply_layout(slots)

    def is_edit_mode(self) -> bool:
        return self._edit_mode

    def set_edit_mode(self, enabled: bool) -> None:
        if self._edit_mode == enabled:
            return
        self._edit_mode = enabled
        self._edit_bar.setVisible(enabled)
        for chrome in self._chrome_by_id.values():
            chrome.set_edit_mode(enabled)
        self.apply_layout(self._slots)
        self.edit_mode_changed.emit(enabled)

    def enter_edit_mode(self) -> None:
        self.set_edit_mode(True)

    def apply_layout(self, slots: list[DashboardWidgetSlot]) -> None:
        """One-shot grid layout (no resize relayout — avoids transient top-level flashes)."""
        self._slots = [DashboardWidgetSlot(s.id, s.span, s.visible) for s in slots]

        self.setUpdatesEnabled(False)
        self._grid_host.setUpdatesEnabled(False)
        try:
            self._detach_grid_widgets()

            placements = pack_bento_placements(self._slots)
            has_visible = bool(placements)
            self._empty_layout_hint.setVisible(not has_visible)
            self._grid_host.setVisible(has_visible)
            self._btn_add.setEnabled(bool(hidden_widget_ids(self._slots)))

            if not has_visible:
                for chrome in self._chrome_by_id.values():
                    chrome.hide()
                self._hide_unused_drop_zones(0)
                return

            rows_by_key: dict[int, list[BentoPlacement]] = {}
            for placement in placements:
                rows_by_key.setdefault(placement.grid_row, []).append(placement)
            sorted_keys = sorted(rows_by_key.keys())

            layout_row = 0
            drop_used = 0
            if self._edit_mode:
                first_id = rows_by_key[sorted_keys[0]][0].slot.id
                zone = self._drop_zone_at(drop_used)
                zone.set_before_widget_id(first_id)
                zone.show()
                self._grid.addWidget(zone, layout_row, 0, 1, 2)
                drop_used += 1
                layout_row += 1

            visible_ids = {p.slot.id for p in placements}
            for row_idx, row_key in enumerate(sorted_keys):
                for placement in rows_by_key[row_key]:
                    chrome = self._chrome_by_id.get(placement.slot.id)
                    if chrome is None:
                        continue
                    chrome.set_span(placement.col_span)
                    chrome.show()
                    self._grid.addWidget(
                        chrome,
                        layout_row,
                        placement.grid_col,
                        1,
                        placement.col_span,
                    )
                layout_row += 1
                if self._edit_mode:
                    if row_idx + 1 < len(sorted_keys):
                        next_id = rows_by_key[sorted_keys[row_idx + 1]][0].slot.id
                        zone = self._drop_zone_at(drop_used)
                        zone.set_before_widget_id(next_id)
                    else:
                        zone = self._drop_zone_at(drop_used)
                        zone.set_before_widget_id(None)
                    zone.show()
                    self._grid.addWidget(zone, layout_row, 0, 1, 2)
                    drop_used += 1
                    layout_row += 1

            for wid, chrome in self._chrome_by_id.items():
                if wid not in visible_ids:
                    chrome.hide()

            self._hide_unused_drop_zones(drop_used)
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 1)
        finally:
            self._grid_host.setUpdatesEnabled(True)
            self.setUpdatesEnabled(True)

    def _slot_index(self, widget_id: str) -> int:
        for i, s in enumerate(self._slots):
            if s.id == widget_id:
                return i
        return -1

    def _on_hide_requested(self, widget_id: str) -> None:
        self._slots = set_slot_visible(self._slots, widget_id, False)
        self.apply_layout(self._slots)
        self.layout_changed.emit(self.slots())

    def _on_span_toggle(self, widget_id: str) -> None:
        self._slots = toggle_slot_span(self._slots, widget_id)
        self.apply_layout(self._slots)
        self.layout_changed.emit(self.slots())

    def _on_drop_before(self, dragged_id: str, target_id: str) -> None:
        target_idx = self._slot_index(target_id)
        if target_idx < 0:
            return
        self._slots = reorder_slot(self._slots, dragged_id, target_idx)
        self.apply_layout(self._slots)
        self.layout_changed.emit(self.slots())

    def _on_drop_at_end(self, dragged_id: str) -> None:
        merged_len = len(self._slots)
        self._slots = reorder_slot(self._slots, dragged_id, merged_len)
        self.apply_layout(self._slots)
        self.layout_changed.emit(self.slots())

    def _show_add_menu(self) -> None:
        hidden = hidden_widget_ids(self._slots)
        if not hidden:
            return
        menu = MonosMenu(self)
        for wid in hidden:
            label = DASHBOARD_WIDGET_LABELS.get(wid, wid)
            act = menu.addAction(label)
            act.triggered.connect(lambda _checked=False, w=wid: self._show_widget(w))
        menu.exec(self._btn_add.mapToGlobal(self._btn_add.rect().bottomLeft()))

    def _show_widget(self, widget_id: str) -> None:
        self._slots = set_slot_visible(self._slots, widget_id, True)
        self.apply_layout(self._slots)
        self.layout_changed.emit(self.slots())

    def _confirm_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset dashboard layout",
            "Restore the default widget order and visibility?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._slots = [DashboardWidgetSlot(s.id, s.span, s.visible) for s in DEFAULT_LAYOUT]
        self.apply_layout(self._slots)
        self.layout_committed.emit(self.slots())

    def _finish_edit(self) -> None:
        self.set_edit_mode(False)
        self.layout_committed.emit(self.slots())


class _DashboardRowDropZone(QFrame):
    """Full-width drop target between bento rows."""

    def __init__(
        self,
        *,
        before_widget_id: str | None,
        host: DashboardBentoHost,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._before_widget_id = before_widget_id
        self._host = host
        self.setObjectName("DashboardRowDropZone")
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(12)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        self._indicator = DashboardDropIndicator(self)
        lay.addWidget(self._indicator)

    def set_before_widget_id(self, before_widget_id: str | None) -> None:
        self._before_widget_id = before_widget_id

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if not self._host.is_edit_mode():
            event.ignore()
            return
        if DashboardBentoChrome.dragged_id_from_mime(event.mimeData()):
            self._indicator.setVisible(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._indicator.setVisible(False)
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if DashboardBentoChrome.dragged_id_from_mime(event.mimeData()):
            self._indicator.setVisible(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self._indicator.setVisible(False)
        dragged = DashboardBentoChrome.dragged_id_from_mime(event.mimeData())
        if not dragged:
            event.ignore()
            return
        if self._before_widget_id is None:
            self._host._on_drop_at_end(dragged)
        else:
            self._host._on_drop_before(dragged, self._before_widget_id)
        event.acceptProposedAction()
