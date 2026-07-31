"""Build PNG flipbook proxies for heavy image sequences (EXR/DPX/HDR)."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Callable

from PySide6.QtGui import QImage

from monostudio.core.sequence_proxy_cache import (
    SequenceFrameStat,
    SequenceProxyManifest,
    collect_sequence_frame_stats,
    sequence_proxy_paths,
    write_sequence_proxy_manifest,
)
from monostudio.core.video_proxy import proxy_scale_dimensions

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]
CancelCheck = Callable[[], bool]
FrameBuiltCallback = Callable[[int], None]

_MIN_TRUSTED_NATIVE_SIDE = 256


def resolve_plate_native_dimensions(path: Path) -> tuple[int, int]:
    """Native plate size — never trust tiny probe decodes (EXR QImageReader / 64px fallback)."""
    from monostudio.ui_qt.sequence_preview_decode import (
        PREVIEW_MAX_SIDE_DEFAULT,
        load_preview_frame_qimage,
        probe_preview_image_dimensions,
    )

    dims = probe_preview_image_dimensions(path)
    if dims is not None and max(dims) >= _MIN_TRUSTED_NATIVE_SIDE:
        return dims
    img = load_preview_frame_qimage(path, PREVIEW_MAX_SIDE_DEFAULT)
    if img is not None and not img.isNull():
        w, h = img.width(), img.height()
        if max(w, h) >= 64:
            return w, h
    return 1920, 1080


def decode_max_side_for_scale(native_w: int, native_h: int, scale: float) -> int:
    sw, sh = proxy_scale_dimensions(max(1, native_w), max(1, native_h), scale)
    return max(64, min(2048, max(sw, sh)))


def build_sequence_proxy(
    frames: list[Path],
    *,
    scale: float,
    ocio_token: str,
    progress_callback: ProgressCallback | None = None,
    frame_built_callback: FrameBuiltCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> SequenceProxyManifest:
    """Decode each source frame to PNG under the sequence proxy cache."""
    if not frames:
        raise ValueError("No frames to proxy")
    digest, stats = collect_sequence_frame_stats(frames)
    proxy_root, manifest_path = sequence_proxy_paths(digest, scale=scale, ocio_token=ocio_token)
    frames_dir = proxy_root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

    native_w, native_h = resolve_plate_native_dimensions(frames[0])
    max_side = decode_max_side_for_scale(native_w, native_h, scale)
    sw, sh = 0, 0

    total = len(frames)
    folder_str = ""
    try:
        folder_str = str(frames[0].parent.resolve())
    except OSError:
        folder_str = str(frames[0].parent)

    for i, (src, stat) in enumerate(zip(frames, stats)):
        if cancel_check and cancel_check():
            raise RuntimeError("Sequence proxy build cancelled")
        out = frames_dir / stat.proxy_name
        if out.is_file():
            try:
                if out.stat().st_size >= 32:
                    if i == 0 and sw <= 0:
                        from PySide6.QtGui import QImageReader

                        reader = QImageReader(str(out))
                        sz = reader.size()
                        if sz.isValid():
                            sw, sh = sz.width(), sz.height()
                    if frame_built_callback:
                        frame_built_callback(i)
                    if progress_callback:
                        progress_callback((i + 1) / max(1, total))
                    continue
            except OSError:
                pass
        img = load_preview_frame_qimage(src, max_side)
        if img is None or img.isNull():
            raise RuntimeError(f"Failed to decode frame {i + 1}/{total}: {src.name}")
        if not _save_proxy_png(img, out):
            raise RuntimeError(f"Failed to write proxy PNG: {out.name}")
        if i == 0:
            sw, sh = img.width(), img.height()
        if frame_built_callback:
            frame_built_callback(i)
        if progress_callback:
            progress_callback((i + 1) / max(1, total))

    manifest = SequenceProxyManifest(
        sequence_folder=folder_str,
        frame_count=total,
        scale=float(scale),
        ocio_token=ocio_token,
        proxy_dir=str(proxy_root),
        frames=stats,
        width=sw,
        height=sh,
        created_at=time.time(),
    )
    write_sequence_proxy_manifest(manifest_path, manifest)
    return manifest


def _save_proxy_png(img: QImage, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.part{path.suffix}")
    try:
        ok = img.save(str(part), "PNG")
        if not ok or not part.is_file():
            return False
        if part.stat().st_size < 32:
            part.unlink(missing_ok=True)
            return False
        if path.is_file():
            path.unlink()
        part.replace(path)
        return True
    except OSError as e:
        logger.debug("save sequence proxy png %s: %s", path, e)
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        return False
