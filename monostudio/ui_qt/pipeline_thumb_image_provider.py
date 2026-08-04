"""Async thumbnail image provider for QML Main View."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtQuick import QQuickImageProvider


class PipelineThumbImageProvider(QQuickImageProvider):
    """Resolve ``image://thumb/<token>`` via injected resolver (ThumbnailManager facade)."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Image)
        self._resolver: Callable[[str], QPixmap | QImage | None] | None = None

    def set_resolver(self, resolver: Callable[[str], QPixmap | QImage | None] | None) -> None:
        self._resolver = resolver

    def requestImage(  # noqa: N802
        self,
        id: str,
        size: QSize,
        requested_size: QSize,
    ) -> QImage:
        token = (id or "").strip().lstrip("/")
        if not token or self._resolver is None:
            return QImage()
        try:
            result = self._resolver(token)
        except Exception:
            return QImage()
        if result is None:
            return QImage()
        if isinstance(result, QPixmap):
            if result.isNull():
                return QImage()
            img = result.toImage()
        else:
            img = result
        if img.isNull():
            return QImage()
        if requested_size.isValid():
            size[:] = requested_size
        else:
            size[:] = img.size()
        return img
