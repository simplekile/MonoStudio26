"""Decode a single frame for sequence preview (light formats + EXR via ffmpeg)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

PREVIEW_MAX_SIDE_DEFAULT = 1920


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
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        img = QImage()
        if not img.loadFromData(proc.stdout):
            return None
        return img
    except (subprocess.TimeoutExpired, OSError):
        return None


def load_preview_frame_qimage(path: Path, max_side: int = PREVIEW_MAX_SIDE_DEFAULT) -> QImage | None:
    """Load and downscale one frame for flipbook / preview."""
    ext = path.suffix.lower()
    if ext in (".exr", ".hdr"):
        return None
    img = QImage(str(path))
    if img.isNull():
        return None
    return _scale_qimage(img, max_side)
