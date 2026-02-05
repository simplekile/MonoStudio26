from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QObject, QTimer
from PySide6.QtGui import QPixmap, QImage

if TYPE_CHECKING:
    from monostudio.ui_qt.app_state import AppState
    from monostudio.ui_qt.worker_manager import WorkerManager

logger = logging.getLogger(__name__)

DEFAULT_THUMB_SIZE_PX = 384
DEFAULT_MEMORY_CACHE_MAX = 200


def resolve_thumbnail_path(item_root: Path) -> Path | None:
    """Resolve thumbnail file path under item root (user override then auto). No IO cache."""
    for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
        p = item_root / name
        if p.is_file():
            return p
    return None


@dataclass
class _CachedPixmap:
    mtime_ns: int
    pixmap: QPixmap


class ThumbnailCache:
    """
    Read-only thumbnail cache.
    Cache key uses: file path + modification time.
    """

    def __init__(self, *, size_px: int) -> None:
        self._size_px = size_px
        self._cache: dict[str, _CachedPixmap] = {}

    def resolve_thumbnail_file(self, item_root: Path) -> Path | None:
        # Spec (v1.2): prefer explicit user override, then auto/default.
        # - User override: thumbnail.user.(png|jpg)
        # - Auto/default:  thumbnail.(png|jpg)
        user_png = item_root / "thumbnail.user.png"
        if user_png.is_file():
            return user_png
        user_jpg = item_root / "thumbnail.user.jpg"
        if user_jpg.is_file():
            return user_jpg

        png = item_root / "thumbnail.png"
        if png.is_file():
            return png
        jpg = item_root / "thumbnail.jpg"
        if jpg.is_file():
            return jpg
        return None

    def invalidate_file(self, file_path: Path) -> None:
        # Best-effort; safe if missing.
        try:
            self._cache.pop(str(file_path), None)
        except Exception:
            pass

    def load_thumbnail_pixmap(self, file_path: Path) -> QPixmap | None:
        key = str(file_path)
        try:
            stat = file_path.stat()
        except FileNotFoundError:
            return None

        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        cached = self._cache.get(key)
        if cached is not None and cached.mtime_ns == mtime_ns:
            return cached.pixmap

        pix = QPixmap(key)
        if pix.isNull():
            return None

        scaled = pix.scaled(
            self._size_px,
            self._size_px,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=scaled)
        return scaled


def _load_thumbnail_image_worker(file_path: str, size_px: int) -> tuple[str, QImage] | None:
    """
    Run in worker thread: load file, decode to QImage, scale. Returns (asset_id, QImage).
    asset_id is the parent path (item root) as string; file_path is the resolved thumbnail path.
    """
    from PySide6.QtCore import Qt
    p = Path(file_path)
    if not p.is_file():
        return None
    try:
        img = QImage(file_path)
        if img.isNull():
            return None
        scaled = img.scaled(
            size_px,
            size_px,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # asset_id = item root (parent of thumbnail file is item root)
        item_root = p.parent
        return (str(item_root), scaled)
    except Exception as e:
        logger.warning("Thumbnail load failed %s: %s", file_path, e)
        return None


class ThumbnailManager(QObject):
    """
    Central async thumbnail loading and caching. Long-lived (app lifetime).
    - Memory cache (LRU). Never blocks UI. Schedules load via WorkerManager.
    - On load success notifies AppState so UI repaints via thumbnailsChanged.
    """

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        app_state: "AppState",
        worker_manager: "WorkerManager",
        size_px: int = DEFAULT_THUMB_SIZE_PX,
        max_memory: int = DEFAULT_MEMORY_CACHE_MAX,
    ) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._worker_manager = worker_manager
        self._size_px = size_px
        self._max_memory = max(1, max_memory)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._pending: set[str] = set()
        self._connect_worker()

    def _connect_worker(self) -> None:
        from monostudio.ui_qt.worker_manager import WorkerManager
        if isinstance(self._worker_manager, WorkerManager):
            self._worker_manager.taskFinished.connect(self._on_task_finished)

    def request_thumbnail(self, asset_id: str) -> QPixmap | None:
        """
        Return pixmap from memory cache if present; else return None (caller shows placeholder)
        and schedule async load if not already pending. Duplicate requests coalesced.
        """
        if not asset_id or not str(asset_id).strip():
            return None
        aid = str(asset_id).strip()
        if aid in self._cache:
            self._cache.move_to_end(aid)
            try:
                from monostudio.ui_qt.stress_profiler import enabled, record_thumbnail_hit
                if enabled():
                    record_thumbnail_hit()
            except Exception:
                pass
            return self._cache[aid]
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_thumbnail_miss
            if enabled():
                record_thumbnail_miss()
        except Exception:
            pass
        if aid not in self._pending:
            self._pending.add(aid)
            self._schedule_load(aid)
        return None

    def _schedule_load(self, asset_id: str) -> None:
        path = resolve_thumbnail_path(Path(asset_id))
        if path is None:
            self._pending.discard(asset_id)
            return
        file_path = str(path)
        size_px = self._size_px

        def run() -> object:
            return _load_thumbnail_image_worker(file_path, size_px)

        from monostudio.ui_qt.worker_manager import WorkerTask
        task = WorkerTask("thumbnail_load", run, manager=self._worker_manager)
        task._schedule_category = f"thumbnail_load:{asset_id}"
        self._worker_manager.submit_task(
            task,
            category=f"thumbnail_load:{asset_id}",
            replace_existing=True,
        )

    def _on_task_finished(self, category: str, result: object, error: str | None) -> None:
        if not category.startswith("thumbnail_load:") or error is not None:
            return
        if result is None:
            aid = category.replace("thumbnail_load:", "", 1) if ":" in category else ""
            self._pending.discard(aid)
            return
        pair = result if isinstance(result, tuple) and len(result) == 2 else None
        if pair is None:
            return
        asset_id, qimg = pair
        if not isinstance(asset_id, str) or not isinstance(qimg, QImage) or qimg.isNull():
            self._pending.discard(asset_id if isinstance(asset_id, str) else "")
            return
        self._pending.discard(asset_id)
        pix = QPixmap.fromImage(qimg)
        if pix.isNull():
            return
        self._cache[asset_id] = pix
        self._cache.move_to_end(asset_id)
        while len(self._cache) > self._max_memory:
            self._cache.popitem(last=False)
        self._app_state.notify_thumbnail_ready([asset_id])

    def invalidate(self, asset_id: str) -> None:
        """Remove from memory cache; allow reload on next request. Emit so UI refreshes."""
        aid = (asset_id or "").strip()
        if not aid:
            return
        self._cache.pop(aid, None)
        self._pending.discard(aid)
        self._app_state.invalidate_thumbnails([aid])

