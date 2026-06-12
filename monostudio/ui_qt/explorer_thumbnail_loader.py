"""Async thumbnail loading for Inbox / Outbox / Project Guide explorer (no UI-thread decode)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QWidget

from monostudio.ui_qt.thumbnails import (
    EXPLORER_PREVIEW_DISK_CACHE_VARIANT,
    ThumbnailCache,
    decode_explorer_preview_qimage_worker,
    explorer_thumb_decode_px,
    is_direct_media_preview_path,
)


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


class _ExplorerThumbDecodeBridge(QObject):
    decoded = Signal(str, int, object)  # path key, generation, QImage | None


class _ExplorerThumbDecodeRunnable(QRunnable):
    def __init__(
        self,
        path_key: str,
        size_px: int,
        gen: int,
        bridge: _ExplorerThumbDecodeBridge,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path_key = path_key
        self._size_px = size_px
        self._gen = gen
        self._bridge = bridge

    def run(self) -> None:
        result = decode_explorer_preview_qimage_worker(self._path_key, self._size_px)
        image: QImage | None = result[1] if result else None
        self._bridge.decoded.emit(self._path_key, self._gen, image)


class ExplorerThumbnailLoader(QObject):
    """Shared cache + background decode for explorer list/grid delegates."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = _ExplorerThumbDecodeBridge(self)
        self._bridge.decoded.connect(self._on_decoded)
        self._decode_px = explorer_thumb_decode_px()
        self._disk_cache = ThumbnailCache(
            size_px=self._decode_px,
            cache_variant=EXPLORER_PREVIEW_DISK_CACHE_VARIANT,
        )
        self._pixmaps: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._missing: set[str] = set()
        self._gen = 0
        self._views: list[QWidget] = []
        self._loading_angle = 0.0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(50)
        self._loading_timer.timeout.connect(self._on_loading_tick)

    @property
    def loading_angle(self) -> float:
        return self._loading_angle

    def is_pending(self, path: Path) -> bool:
        return _path_key(path) in self._pending

    def register_view(self, view: QWidget) -> None:
        if view not in self._views:
            self._views.append(view)

    def _view_dpr(self) -> float:
        dpr = 1.0
        for view in self._views:
            if view is not None:
                dpr = max(dpr, float(view.devicePixelRatioF()))
        return dpr

    def _pixmap_for_ui(self, pm: QPixmap) -> QPixmap:
        if pm.isNull():
            return pm
        out = QPixmap(pm)
        out.setDevicePixelRatio(max(1.0, self._view_dpr()))
        return out

    def _current_decode_px(self) -> int:
        return explorer_thumb_decode_px(dpr=self._view_dpr())

    def _sync_decode_bucket(self) -> None:
        """Drop RAM thumbs when DPR / layout bucket changes so we re-decode at sharp size."""
        bucket = self._current_decode_px()
        if bucket == self._decode_px:
            return
        self._decode_px = bucket
        self._pixmaps.clear()
        self._pending.clear()
        self._missing.clear()
        self._disk_cache = ThumbnailCache(
            size_px=bucket,
            cache_variant=EXPLORER_PREVIEW_DISK_CACHE_VARIANT,
        )

    def _ensure_disk_cache(self, size_px: int) -> None:
        self._sync_decode_bucket()
        if self._disk_cache._size_px >= size_px:
            return
        self._decode_px = size_px
        self._disk_cache = ThumbnailCache(
            size_px=size_px,
            cache_variant=EXPLORER_PREVIEW_DISK_CACHE_VARIANT,
        )

    def invalidate_all(self) -> None:
        self._gen += 1
        self._pending.clear()
        self._pixmaps.clear()
        self._missing.clear()
        self._stop_loading_timer()
        self._repaint_views()

    def peek(self, path: Path) -> QPixmap | None:
        if not is_direct_media_preview_path(path):
            return None
        key = _path_key(path)
        cached = self._pixmaps.get(key)
        if cached is not None and not cached.isNull():
            return cached
        self._ensure_disk_cache(self._current_decode_px())
        pm = self._disk_cache.peek_thumbnail_pixmap(path)
        if pm is not None and not pm.isNull():
            ui_pm = self._pixmap_for_ui(pm)
            self._pixmaps[key] = ui_pm
            return ui_pm
        return None

    def request(self, path: Path) -> None:
        """Return cached pixmap if ready; otherwise schedule background decode."""
        if not is_direct_media_preview_path(path):
            return
        key = _path_key(path)
        if key in self._pixmaps or key in self._pending or key in self._missing:
            return
        decode_px = self._current_decode_px()
        self._ensure_disk_cache(decode_px)
        pm = self._disk_cache.peek_thumbnail_pixmap(path)
        if pm is not None and not pm.isNull():
            self._pixmaps[key] = self._pixmap_for_ui(pm)
            self._repaint_views()
            return
        self._pending.add(key)
        self._start_loading_timer()
        QThreadPool.globalInstance().start(
            _ExplorerThumbDecodeRunnable(key, decode_px, self._gen, self._bridge)
        )

    def get_or_request(self, path: Path) -> QPixmap | None:
        pm = self.peek(path)
        if pm is None:
            self.request(path)
        return pm

    def _on_decoded(self, path_key: str, gen: int, image: object) -> None:
        if gen != self._gen:
            return
        self._pending.discard(path_key)
        path = Path(path_key)
        pm: QPixmap | None = None
        if isinstance(image, QImage) and not image.isNull():
            pm = self._disk_cache.adopt_decoded_thumbnail(path, image)
            if pm is not None and not pm.isNull():
                pm = self._pixmap_for_ui(pm)
        if pm is not None and not pm.isNull():
            self._pixmaps[path_key] = pm
            self._missing.discard(path_key)
        else:
            self._missing.add(path_key)
        if not self._pending:
            self._stop_loading_timer()
        self._repaint_views()

    def _start_loading_timer(self) -> None:
        self._loading_angle = 0.0
        if not self._loading_timer.isActive():
            self._loading_timer.start()

    def _stop_loading_timer(self) -> None:
        if self._loading_timer.isActive():
            self._loading_timer.stop()

    def _on_loading_tick(self) -> None:
        if not self._pending:
            self._stop_loading_timer()
            return
        self._loading_angle = (self._loading_angle + 30.0) % 360.0
        self._repaint_views()

    def _repaint_views(self) -> None:
        for view in self._views:
            if isinstance(view, QAbstractItemView):
                view.viewport().update()
            else:
                view.update()
