"""Explorer drop zones: accept drag, dashed highlight, forward from nested Qt views."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QRect
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from monostudio.ui_qt.external_drop import (
    event_global_pos,
    mime_has_local_files,
    paths_from_mime,
)

if TYPE_CHECKING:
    pass

EXPLORER_DROP_ZONE_OBJECT_NAME = "ExplorerDropZone"

DropPathsHandler = Callable[[list[Path], QDropEvent], None]
DragHoverHandler = Callable[[QDragEnterEvent | QDragMoveEvent], None]
DragLeaveHandler = Callable[[], None]

_EXPLORER_DROP_ROW_FILL = QColor(59, 130, 246, 110)
_EXPLORER_DROP_ROW_BORDER = QColor("#60a5fa")


def paint_explorer_drop_target_highlight(painter: QPainter, rect: QRect) -> None:
    """Dashed row/card highlight while dragging over an explorer drop target."""
    if rect.width() <= 0 or rect.height() <= 0:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    box = rect.adjusted(1, 1, -1, -2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_EXPLORER_DROP_ROW_FILL)
    painter.drawRoundedRect(box, 6, 6)
    pen = QPen(_EXPLORER_DROP_ROW_BORDER)
    pen.setStyle(Qt.PenStyle.DashLine)
    pen.setWidthF(2.0)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(box, 6, 6)
    painter.restore()


def set_explorer_drop_highlight(widget: QWidget, on: bool) -> None:
    widget.setProperty("dropHighlight", "true" if on else "false")
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def _accept_explorer_drop_action(
    event: QDragEnterEvent | QDragMoveEvent | QDropEvent,
    *,
    internal_move: bool = False,
) -> None:
    """Accept Explorer/OLE drops (frameless Windows needs a valid + proposable action).

    Prefer Move for internal (non-Ctrl) and Copy otherwise; fall back to
    acceptProposedAction when the preferred action is not in possibleActions
    (same pattern as MainWindow accept_url_drag / v26.13 Explorer drops).
    """
    mods = event.modifiers() if hasattr(event, "modifiers") else event.keyboardModifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    want_move = bool(internal_move) and not ctrl
    possible = event.possibleActions()
    if want_move and (possible & Qt.DropAction.MoveAction):
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        return
    if possible & Qt.DropAction.CopyAction:
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        return
    event.acceptProposedAction()


def accept_explorer_file_drag(
    event: QDragEnterEvent | QDragMoveEvent,
    *,
    internal_move: bool = False,
) -> bool:
    if not mime_has_local_files(event.mimeData()):
        return False
    _accept_explorer_drop_action(event, internal_move=internal_move)
    return True


def finish_explorer_file_drop(
    event: QDropEvent,
    *,
    internal_move: bool = False,
) -> list[Path]:
    paths = paths_from_mime(event.mimeData())
    if not paths:
        event.ignore()
        return []
    _accept_explorer_drop_action(event, internal_move=internal_move)
    return paths


class _ExplorerDropForwarder(QObject):
    """Route drag/drop from nested views (tree/grid) to the zone owner widget."""

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self._owner = owner

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if not isinstance(event, (QDragEnterEvent, QDragMoveEvent, QDropEvent)):
            if event.type() == QEvent.Type.DragLeave:
                leave = getattr(self._owner, "dragLeaveEvent", None)
                if callable(leave):
                    leave(event)
            return False
        et = event.type()
        if et == QEvent.Type.DragEnter and isinstance(event, QDragEnterEvent):
            self._owner.dragEnterEvent(event)
            return event.isAccepted()
        if et == QEvent.Type.DragMove and isinstance(event, QDragMoveEvent):
            self._owner.dragMoveEvent(event)
            return event.isAccepted()
        if et == QEvent.Type.Drop and isinstance(event, QDropEvent):
            self._owner.dropEvent(event)
            return event.isAccepted()
        return False


class ExplorerDropZone:
    """Highlight shell + nested-widget forwarding for Explorer file drops."""

    def __init__(
        self,
        owner: QWidget,
        *,
        highlight_widget: QWidget,
        on_drop: DropPathsHandler,
        enabled: Callable[[], bool] | None = None,
        on_drag_hover: DragHoverHandler | None = None,
        on_drag_leave: DragLeaveHandler | None = None,
        is_internal_drag: Callable[[list[Path]], bool] | None = None,
    ) -> None:
        self._owner = owner
        self._highlight = highlight_widget
        self._on_drop = on_drop
        self._enabled = enabled or (lambda: True)
        self._on_drag_hover = on_drag_hover
        self._on_drag_leave = on_drag_leave
        self._is_internal_drag = is_internal_drag
        self._forwarder = _ExplorerDropForwarder(owner)
        self._highlight.setObjectName(EXPLORER_DROP_ZONE_OBJECT_NAME)
        self._highlight.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mount(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.setAcceptDrops(True)
            widget.installEventFilter(self._forwarder)

    def _drag_is_internal(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
        if self._is_internal_drag is None:
            return False
        return self._is_internal_drag(paths_from_mime(event.mimeData()))

    def handle_drag_enter(self, event: QDragEnterEvent) -> bool:
        if not self._enabled():
            return False
        if not accept_explorer_file_drag(event, internal_move=self._drag_is_internal(event)):
            return False
        if self._on_drag_hover is not None:
            self._on_drag_hover(event)
        else:
            set_explorer_drop_highlight(self._highlight, True)
        return True

    def handle_drag_move(self, event: QDragMoveEvent) -> bool:
        return self.handle_drag_enter(event)

    def handle_drag_leave(self) -> None:
        if self._on_drag_leave is not None:
            self._on_drag_leave()
        else:
            set_explorer_drop_highlight(self._highlight, False)

    def handle_drop(self, event: QDropEvent) -> bool:
        if self._on_drag_leave is not None:
            self._on_drag_leave()
        else:
            set_explorer_drop_highlight(self._highlight, False)
        if not self._enabled():
            event.ignore()
            return False
        paths = finish_explorer_file_drop(event, internal_move=self._drag_is_internal(event))
        if not paths:
            return False
        self._on_drop(paths, event)
        return True

    @staticmethod
    def event_global_pos(event: QDropEvent, widget: QWidget):
        return event_global_pos(event, widget)
