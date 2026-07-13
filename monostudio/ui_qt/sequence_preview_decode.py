"""Decode a single frame for sequence preview (Qt + ffmpeg fallback for DPX/EXR/HDR).

FFmpeg must support the format (OpenEXR for .exr). Decoded frames are LRU-cached
(path + mtime + size bucket) so looping the flipbook or re-playing avoids repeat work.
"""

from __future__ import annotations

import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from monostudio.core.ffmpeg_resolve import resolve_ffmpeg_executable
from monostudio.core.subprocess_win import hide_console_subprocess_kwargs

PREVIEW_MAX_SIDE_DEFAULT = 1920

# Inspector/dialog keep only ~6 pixmaps; without this cache each loop re-decodes (DPX = ffmpeg per frame).
_MAX_DECODED_FRAME_CACHE = 72
_decoded_frame_cache: OrderedDict[tuple[str, int, int], QImage] = OrderedDict()
_decoded_frame_cache_lock = threading.Lock()
# Parallel QImage ctor/copy deadlocks shiboken on Py3.13 — serialize Qt image work only.
_qt_image_lock = threading.Lock()


def _quantize_decode_max_side(max_side: int) -> int:
    ms = max(1, int(max_side))
    return max(256, min(2048, ((ms + 31) // 32) * 32))


def _decode_cache_key(path: Path, max_side: int) -> tuple[str, int, int, str, str] | None:
    try:
        resolved = str(path.resolve())
        st = path.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", st.st_mtime * 1_000_000_000))
    except OSError:
        return None
    from monostudio.core.ocio_display import PLATE_DECODE_CACHE_REV
    from monostudio.ui_qt.ocio_preview_settings import ocio_preview_cache_token

    return (
        resolved,
        mtime_ns,
        _quantize_decode_max_side(max_side),
        ocio_preview_cache_token(),
        PLATE_DECODE_CACHE_REV,
    )


def invalidate_decoded_frame_cache() -> None:
    """Drop LRU frame cache (e.g. after OCIO / preview settings change)."""
    from monostudio.core.ocio_display import invalidate_ocio_processor_cache

    with _decoded_frame_cache_lock:
        _decoded_frame_cache.clear()
    invalidate_ocio_processor_cache()


def _decode_cache_get(key: tuple[str, int, int, str, str]) -> QImage | None:
    with _decoded_frame_cache_lock:
        img = _decoded_frame_cache.get(key)
        if img is None or img.isNull():
            return None
        _decoded_frame_cache.move_to_end(key)
    with _qt_image_lock:
        copy = QImage(img)
    return copy if not copy.isNull() else None


def _decode_cache_put(key: tuple[str, int, int, str, str], img: QImage) -> None:
    if img.isNull():
        return
    with _qt_image_lock:
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
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        return None
    ext = path.suffix.lower()
    if ext in (".exr", ".dpx", ".hdr"):
        vf = (
            f"scale='min({max_side},iw)':-1:flags=lanczos,"
            "zscale=transfer=linear:matrix=bt709,tonemap=hable,format=rgb24"
        )
    else:
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
        with _qt_image_lock:
            img = QImage()
            if not img.loadFromData(proc.stdout):
                return None
        return img
    except (subprocess.TimeoutExpired, OSError):
        return None


def _load_preview_frame_qimage_uncached(path: Path, max_side: int) -> QImage | None:
    import logging

    log = logging.getLogger(__name__)
    ext = path.suffix.lower()
    plate = ext in (".exr", ".dpx", ".hdr")
    try:
        from monostudio.core.ocio_display import load_ocio_preview_qimage, should_apply_ocio_for_path
        from monostudio.core.sequence_preview import path_matches_sequence_ignore_tokens
        from monostudio.ui_qt.inspector_preview_settings import default_qsettings
        from monostudio.ui_qt.ocio_preview_settings import read_ocio_preview_state
        from monostudio.ui_qt.thumbnails import get_thumbnail_sequence_ignore_tokens

        settings = default_qsettings()
        state = read_ocio_preview_state(settings)
        ignore_tokens = get_thumbnail_sequence_ignore_tokens(settings)
        non_color_aov = path_matches_sequence_ignore_tokens(path, ignore_tokens)
        if non_color_aov:
            log.debug("Skipping OCIO for non-color AOV %s", path.name)
        elif should_apply_ocio_for_path(path, state):
            ocio_img = load_ocio_preview_qimage(path, max_side, state)
            if ocio_img is not None and not ocio_img.isNull():
                return ocio_img
            if plate:
                log.warning(
                    "OCIO preview failed for %s — falling back to ffmpeg tonemap",
                    path.name,
                )
    except Exception as e:
        log.warning("OCIO preview decode skipped for %s: %s", path.name, e)
    if plate:
        return _load_via_ffmpeg(path, max_side)
    with _qt_image_lock:
        img = QImage(str(path))
        if not img.isNull():
            return _scale_qimage(img, max_side)
    return None


def probe_preview_image_dimensions(path: Path) -> tuple[int, int] | None:
    """Native width/height for a sequence frame (Qt reader, then tiny decode for EXR/DPX)."""
    if not path.is_file():
        return None
    with _qt_image_lock:
        reader_sz = None
        try:
            from PySide6.QtGui import QImageReader

            reader = QImageReader(str(path))
            sz = reader.size()
            if sz.isValid() and sz.width() > 0 and sz.height() > 0:
                reader_sz = (sz.width(), sz.height())
        except Exception:
            reader_sz = None
    if reader_sz is not None:
        return reader_sz
    img = _load_preview_frame_qimage_uncached(path, 64)
    if img is not None and not img.isNull() and img.width() > 0 and img.height() > 0:
        return img.width(), img.height()
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
