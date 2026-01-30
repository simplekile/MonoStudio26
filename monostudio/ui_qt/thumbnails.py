from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap


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
        # Spec: try thumbnail.png then thumbnail.jpg only.
        png = item_root / "thumbnail.png"
        if png.is_file():
            return png
        jpg = item_root / "thumbnail.jpg"
        if jpg.is_file():
            return jpg
        return None

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

