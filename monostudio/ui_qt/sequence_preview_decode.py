"""Decode a single frame for sequence preview (Qt + ffmpeg fallback for DPX/EXR/HDR).

FFmpeg must support the format (OpenEXR for .exr). Decoded frames are LRU-cached
(path + mtime + size bucket) so looping the flipbook or re-playing avoids repeat work.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from monostudio.core.subprocess_win import hide_console_subprocess_kwargs

PREVIEW_MAX_SIDE_DEFAULT = 1920

# Inspector/dialog keep only ~6 pixmaps; without this cache each loop re-decodes (DPX = ffmpeg per frame).
_MAX_DECODED_FRAME_CACHE = 72
_decoded_frame_cache: OrderedDict[tuple[str, int, int], QImage] = OrderedDict()
_decoded_frame_cache_lock = threading.Lock()


def _quantize_decode_max_side(max_side: int) -> int:
    ms = max(1, int(max_side))
    return max(256, min(2048, ((ms + 31) // 32) * 32))


def _decode_cache_key(path: Path, max_side: int) -> tuple[str, int, int] | None:
    try:
        resolved = str(path.resolve())
        st = path.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", st.st_mtime * 1_000_000_000))
    except OSError:
        return None
    return (resolved, mtime_ns, _quantize_decode_max_side(max_side))


def _decode_cache_get(key: tuple[str, int, int]) -> QImage | None:
    with _decoded_frame_cache_lock:
        img = _decoded_frame_cache.get(key)
        if img is None or img.isNull():
            return None
        _decoded_frame_cache.move_to_end(key)
        return QImage(img)


def _decode_cache_put(key: tuple[str, int, int], img: QImage) -> None:
    if img.isNull():
        return
    store = QImage(img)
    if store.isNull():
        return
    with _decoded_frame_cache_lock:
        _decoded_frame_cache[key] = store
        _decoded_frame_cache.move_to_end(key)
        while len(_decoded_frame_cache) > _MAX_DECODED_FRAME_CACHE:
            _decoded_frame_cache.popitem(last=False)


def _scale_qimage(img: QImage, max_side: int) -> QImage:
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    m = max(w, h)
    if m <= max_side:
        return img
    if w >= h:
        nw = max_side
        nh = max(1, int(h * max_side / w))
    else:
        nh = max_side
        nw = max(1, int(w * max_side / h))
    return img.scaled(nw, nh, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _load_via_ffmpeg(path: Path, max_side: int) -> QImage | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    vf = f"scale='min({max_side},iw)':-1"
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path.resolve()),
                "-vf",
                vf,
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-",
            ],
            capture_output=True,
            timeout=120,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        img = QImage()
        if not img.loadFromData(proc.stdout):
            return None
        return img
    except (subprocess.TimeoutExpired, OSError):
        return None


def _load_preview_frame_qimage_uncached(path: Path, max_side: int) -> QImage | None:
    ext = path.suffix.lower()
    img = QImage(str(path))
    if not img.isNull():
        return _scale_qimage(img, max_side)
    if ext in (".dpx", ".exr", ".hdr"):
        return _load_via_ffmpeg(path, max_side)
    return None


def load_preview_frame_qimage(path: Path, max_side: int = PREVIEW_MAX_SIDE_DEFAULT) -> QImage | None:
    """Load and downscale one frame for flipbook / preview (LRU cache across workers / loops)."""
    key = _decode_cache_key(path, max_side)
    if key is not None:
        hit = _decode_cache_get(key)
        if hit is not None and not hit.isNull():
            return hit
    out = _load_preview_frame_qimage_uncached(path, max_side)
    if key is not None and out is not None and not out.isNull():
        _decode_cache_put(key, out)
    return out
