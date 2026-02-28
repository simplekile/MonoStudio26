from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
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

# Extensions that Qt can load as image — use file itself as thumbnail
_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tga", ".tif", ".tiff",
    ".exr", ".hdr", ".ico", ".svg", ".ppm", ".xbm", ".xpm",
})
# Video: extract one frame via ffmpeg (fast seek -ss before -i)
_VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg",
    ".ts",  # MPEG Transport Stream
})
DEFAULT_MEMORY_CACHE_MAX = 200


def _get_video_duration_seconds(video_path: Path) -> float | None:
    """Get video duration in seconds via ffprobe; None if unavailable or invalid."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    path_str = str(video_path.resolve())
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path_str,
            ],
            capture_output=True,
            timeout=5,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout or not proc.stdout.strip():
            return None
        return float(proc.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        logger.debug("ffprobe duration failed for %s: %s", path_str, e)
        return None


def _load_video_frame_via_ffmpeg(video_path: Path, size_px: int) -> QPixmap | None:
    """
    Extract one frame from video at 1/4 duration using ffmpeg (fast: -ss before -i).
    Falls back to frame at 0s if duration unknown. Returns scaled QPixmap or None.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    path_str = str(video_path.resolve())
    seek_sec = 0.0
    duration = _get_video_duration_seconds(video_path)
    if duration is not None and duration > 0:
        seek_sec = duration / 4.0
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel", "error",
                "-ss", str(seek_sec),
                "-i", path_str,
                "-vframes", "1",
                "-f", "image2pipe",
                "-c:v", "png",
                "-",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        img = QImage()
        if not img.loadFromData(proc.stdout):
            return None
        pix = QPixmap.fromImage(img)
        if pix.isNull():
            return None
        return pix.scaled(
            size_px,
            size_px,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        logger.debug("Video thumbnail ffmpeg failed for %s: %s", path_str, e)
        return None


_DEPT_THUMB_CACHE_SEP = "::dept::"


def _thumbnail_disk_cache_dir() -> Path:
    """Thumbnail disk cache root: Windows temp (or system temp) / MonoStudio26 / thumbnails. Not deleted by app."""
    return Path(tempfile.gettempdir()) / "MonoStudio26" / "thumbnails"


def _disk_cache_path(source_path: Path, mtime_ns: int, size_px: int) -> Path:
    """Path to cached PNG for this source file; same path+mtime+size always yields same file."""
    raw = f"{source_path.resolve()!s}\n{mtime_ns}\n{size_px}"
    h = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]
    return _thumbnail_disk_cache_dir() / f"{h}.png"


def resolve_department_thumbnail_path(item_root: Path, department: str) -> Path | None:
    """Resolve department-specific thumbnail from .meta/ folder. Returns None if not found."""
    dep = (department or "").strip()
    if not dep:
        return None
    meta = item_root / ".meta"
    for name in (
        f"thumb_{dep}.user.png",
        f"thumb_{dep}.user.jpg",
        f"thumb_{dep}.png",
        f"thumb_{dep}.jpg",
    ):
        p = meta / name
        if p.is_file():
            return p
    return None


def resolve_thumbnail_path(item_root: Path, department: str | None = None) -> Path | None:
    """
    Resolve thumbnail path with department fallback:
      1. department thumb in .meta/  (if department given)
      2. entity-level thumb
      3. direct file (image/video)
    """
    if item_root.is_file():
        ext = (item_root.suffix or "").strip().lower()
        if ext in _IMAGE_EXTENSIONS or ext in _VIDEO_EXTENSIONS:
            return item_root
        return None
    dep = (department or "").strip()
    if dep:
        dept_thumb = resolve_department_thumbnail_path(item_root, dep)
        if dept_thumb is not None:
            return dept_thumb
    for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
        p = item_root / name
        if p.is_file():
            return p
    return None


def make_department_cache_key(entity_path: str, department: str | None) -> str:
    """Build cache key: entity path alone, or entity::dept::department when filtered."""
    dep = (department or "").strip()
    if dep:
        return f"{entity_path}{_DEPT_THUMB_CACHE_SEP}{dep}"
    return entity_path


def parse_department_cache_key(cache_key: str) -> tuple[str, str | None]:
    """Split cache key back into (entity_path, department_or_None)."""
    if _DEPT_THUMB_CACHE_SEP in cache_key:
        parts = cache_key.split(_DEPT_THUMB_CACHE_SEP, 1)
        return (parts[0], parts[1] if len(parts) > 1 else None)
    return (cache_key, None)


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

    def resolve_thumbnail_file(self, item_root: Path, department: str | None = None) -> Path | None:
        return resolve_thumbnail_path(item_root, department=department)

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

        # Disk cache in Windows temp: read first; never deleted by app
        dc_path = _disk_cache_path(file_path, mtime_ns, self._size_px)
        try:
            if dc_path.is_file():
                pix = QPixmap(str(dc_path))
                if not pix.isNull():
                    self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=pix)
                    return pix
        except OSError:
            pass

        ext = (file_path.suffix or "").strip().lower()
        if file_path.is_file() and ext in _VIDEO_EXTENSIONS:
            pix = _load_video_frame_via_ffmpeg(file_path, self._size_px)
            if pix is not None:
                self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=pix)
                try:
                    dc_path.parent.mkdir(parents=True, exist_ok=True)
                    pix.save(str(dc_path), "PNG")
                except OSError:
                    pass
            return pix

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
        try:
            dc_path.parent.mkdir(parents=True, exist_ok=True)
            scaled.save(str(dc_path), "PNG")
        except OSError:
            pass
        return scaled


def _load_thumbnail_image_worker(file_path: str, size_px: int, cache_key: str | None = None) -> tuple[str, QImage] | None:
    """
    Run in worker thread: load file, decode to QImage, scale.
    Returns (cache_key, QImage). cache_key is provided explicitly or derived from parent path.
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
        key = cache_key if cache_key else str(p.parent)
        return (key, scaled)
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

    def request_thumbnail(self, asset_id: str, department: str | None = None) -> QPixmap | None:
        """
        Return pixmap from memory cache if present; else return None (caller shows placeholder)
        and schedule async load if not already pending. Duplicate requests coalesced.
        When department is given, looks for department-specific thumb first (fallback to entity).
        """
        if not asset_id or not str(asset_id).strip():
            return None
        cache_key = make_department_cache_key(str(asset_id).strip(), department)
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            try:
                from monostudio.ui_qt.stress_profiler import enabled, record_thumbnail_hit
                if enabled():
                    record_thumbnail_hit()
            except Exception:
                pass
            return self._cache[cache_key]
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_thumbnail_miss
            if enabled():
                record_thumbnail_miss()
        except Exception:
            pass
        if cache_key not in self._pending:
            self._pending.add(cache_key)
            self._schedule_load(str(asset_id).strip(), department, cache_key)
        return None

    def _schedule_load(self, entity_path: str, department: str | None, cache_key: str) -> None:
        dep = (department or "").strip() or None
        path = resolve_thumbnail_path(Path(entity_path), department=dep)
        if path is None:
            self._pending.discard(cache_key)
            return
        file_path = str(path)
        size_px = self._size_px
        key = cache_key

        def run() -> object:
            return _load_thumbnail_image_worker(file_path, size_px, cache_key=key)

        from monostudio.ui_qt.worker_manager import WorkerTask
        task = WorkerTask("thumbnail_load", run, manager=self._worker_manager)
        task._schedule_category = f"thumbnail_load:{cache_key}"
        self._worker_manager.submit_task(
            task,
            category=f"thumbnail_load:{cache_key}",
            replace_existing=True,
        )

    def _on_task_finished(self, category: str, result: object, error: str | None) -> None:
        if not category.startswith("thumbnail_load:") or error is not None:
            return
        if result is None:
            cache_key = category.replace("thumbnail_load:", "", 1) if ":" in category else ""
            self._pending.discard(cache_key)
            return
        pair = result if isinstance(result, tuple) and len(result) == 2 else None
        if pair is None:
            return
        cache_key, qimg = pair
        if not isinstance(cache_key, str) or not isinstance(qimg, QImage) or qimg.isNull():
            self._pending.discard(cache_key if isinstance(cache_key, str) else "")
            return
        self._pending.discard(cache_key)
        pix = QPixmap.fromImage(qimg)
        if pix.isNull():
            return
        self._cache[cache_key] = pix
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._max_memory:
            self._cache.popitem(last=False)
        entity_path, _ = parse_department_cache_key(cache_key)
        self._app_state.notify_thumbnail_ready([cache_key, entity_path])

    def invalidate(self, asset_id: str, department: str | None = None) -> None:
        """Remove from memory cache; allow reload on next request. Emit so UI refreshes."""
        aid = (asset_id or "").strip()
        if not aid:
            return
        cache_key = make_department_cache_key(aid, department)
        self._cache.pop(cache_key, None)
        self._pending.discard(cache_key)
        # Also invalidate entity-level key if department was given (ensures fallback refreshes).
        if department:
            self._cache.pop(aid, None)
            self._pending.discard(aid)
        self._app_state.invalidate_thumbnails([cache_key, aid])

