"""Shared helpers for dragging files from Explorer/Finder into MONOS."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QWidget


def mime_has_local_files(mime: QMimeData | None) -> bool:
    if mime is None:
        return False
    if mime.hasUrls():
        return True
    if mime.hasFormat("text/uri-list"):
        return True
    for fmt in mime.formats():
        low = fmt.lower()
        if "filename" in low or "hdrop" in low or fmt == "Files":
            return True
    return False


def paths_from_mime(mime: QMimeData | None) -> list[Path]:
    if mime is None:
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    if mime.hasUrls():
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if not p.exists():
                continue
            key = str(p)
            if key not in seen:
                seen.add(key)
                paths.append(p)
    if paths:
        return paths
    if mime.hasFormat("text/uri-list"):
        try:
            raw = bytes(mime.data("text/uri-list")).decode("utf-8", errors="ignore")
        except (TypeError, ValueError, UnicodeDecodeError):
            raw = ""
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            url = QUrl(line)
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.exists():
                key = str(p)
                if key not in seen:
                    seen.add(key)
                    paths.append(p)
    return paths


def event_global_pos(event: QDragEnterEvent | QDragMoveEvent | QDropEvent, widget: QWidget) -> object:
    from PySide6.QtCore import QPoint

    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    if hasattr(event, "position"):
        return widget.mapToGlobal(event.position().toPoint())
    return widget.mapToGlobal(event.pos())


def accept_url_drag(event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
    """Match v26.13/v26.14 Explorer drop acceptance (acceptProposedAction)."""
    if not mime_has_local_files(event.mimeData()):
        return False
    event.acceptProposedAction()
    return True


def accept_external_file_drag(event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> bool:
    return accept_url_drag(event)


def paths_from_drop_event(event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> list[Path]:
    return paths_from_mime(event.mimeData())


def paths_under_root(paths: list[Path], root: Path) -> bool:
    if not paths:
        return False
    try:
        root_res = Path(root).resolve()
    except OSError:
        return False
    for raw in paths:
        try:
            Path(raw).resolve().relative_to(root_res)
        except (ValueError, OSError):
            return False
    return True


def drop_wants_copy(
    event: QDragEnterEvent | QDragMoveEvent | QDropEvent,
    *,
    paths: list[Path],
    storage_root: Path | None,
) -> bool:
    """External drops always copy; internal storage drops copy only with Ctrl held."""
    if storage_root is None or not paths_under_root(paths, storage_root):
        return True
    return bool(event.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
