"""Explorer drop acceptance — must be valid for Windows OLE / frameless MainWindow."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QWidget

from monostudio.ui_qt.external_drop_host import (
    accept_explorer_file_drag,
    finish_explorer_file_drop,
)


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _url_mime(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def test_accept_explorer_file_drag_prefers_copy_when_possible(tmp_path: Path) -> None:
    _ensure_app()
    src = tmp_path / "shot.mp4"
    src.write_bytes(b"x")
    mime = _url_mime(src)
    event = QDragEnterEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    # Proposed is often Move from Explorer on same volume; we still prefer Copy for external.
    event.setDropAction(Qt.DropAction.MoveAction)
    assert accept_explorer_file_drag(event, internal_move=False) is True
    assert event.isAccepted()
    assert event.dropAction() == Qt.DropAction.CopyAction


def test_accept_explorer_file_drag_internal_move(tmp_path: Path) -> None:
    _ensure_app()
    src = tmp_path / "a.txt"
    src.write_text("a", encoding="utf-8")
    mime = _url_mime(src)
    event = QDragEnterEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert accept_explorer_file_drag(event, internal_move=True) is True
    assert event.isAccepted()
    assert event.dropAction() == Qt.DropAction.MoveAction


def test_finish_explorer_file_drop_returns_paths(tmp_path: Path) -> None:
    _ensure_app()
    src = tmp_path / "ref.png"
    src.write_bytes(b"png")
    mime = _url_mime(src)
    host = QWidget()
    event = QDropEvent(
        QPoint(2, 2),
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    paths = finish_explorer_file_drop(event, internal_move=False)
    assert paths == [src]
    assert event.isAccepted()
    assert event.dropAction() == Qt.DropAction.CopyAction
    host.deleteLater()
