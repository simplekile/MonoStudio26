"""OCIO display transform for sequence review frames (v1: ACEScg → display/view)."""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtGui import QImage

from monostudio.core.ffmpeg_resolve import resolve_ffmpeg_executable
from monostudio.core.subprocess_win import hide_console_subprocess_kwargs

if TYPE_CHECKING:
    from monostudio.ui_qt.ocio_preview_settings import OcioPreviewState

logger = logging.getLogger(__name__)

# Bump when plate decode / QImage export changes (invalidates sequence frame LRU).
PLATE_DECODE_CACHE_REV = "3"

_OCIO_PLATE_EXTENSIONS = frozenset({".exr", ".dpx", ".hdr"})

_SHOWINFO_DIMS_RE = re.compile(r"\bs:(\d+)x(\d+)\b")

_ocio_import_error: str | None = None
try:
    import PyOpenColorIO as ocio  # type: ignore[import-untyped]
except ImportError as e:
    ocio = None  # type: ignore[assignment]
    _ocio_import_error = str(e)

_processor_lock = threading.Lock()
_processor_key: tuple[object, ...] | None = None
_processor_cpu: object | None = None


def is_ocio_runtime_available() -> bool:
    return ocio is not None


def ocio_runtime_status() -> str:
    if ocio is None:
        hint = _ocio_import_error or "not installed"
        return f"OpenColorIO unavailable ({hint}). Install: pip install opencolorio"
    return "OpenColorIO ready"


def is_ocio_plate_path(path: Path) -> bool:
    return path.suffix.lower() in _OCIO_PLATE_EXTENSIONS


def should_apply_ocio_for_path(path: Path, state: OcioPreviewState) -> bool:
    return bool(state.enabled and state.config_path is not None and is_ocio_plate_path(path))


def _processor_cache_key(state: OcioPreviewState) -> tuple[object, ...] | None:
    if state.config_path is None:
        return None
    try:
        mtime = int(state.config_path.stat().st_mtime_ns)
    except OSError:
        mtime = 0
    return (
        str(state.config_path),
        mtime,
        state.input_colorspace,
        state.display,
        state.view,
    )


def _get_cpu_processor(state: OcioPreviewState):
    if ocio is None or state.config_path is None:
        return None
    key = _processor_cache_key(state)
    global _processor_key, _processor_cpu
    with _processor_lock:
        if key is not None and key == _processor_key and _processor_cpu is not None:
            return _processor_cpu
        try:
            cfg = ocio.Config.CreateFromFile(str(state.config_path))
            dt = ocio.DisplayViewTransform()
            dt.setSrc(state.input_colorspace)
            dt.setDisplay(state.display)
            dt.setView(state.view)
            cpu = cfg.getProcessor(dt).getDefaultCPUProcessor()
        except Exception as e:
            logger.warning("OCIO processor build failed: %s", e)
            _processor_key = key
            _processor_cpu = None
            return None
        _processor_key = key
        _processor_cpu = cpu
        return cpu


def invalidate_ocio_processor_cache() -> None:
    global _processor_key, _processor_cpu
    with _processor_lock:
        _processor_key = None
        _processor_cpu = None


def _probe_image_size(path: Path) -> tuple[int, int] | None:
    from monostudio.core.ffmpeg_resolve import resolve_ffprobe_executable

    ffprobe = resolve_ffprobe_executable()
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(path.resolve()),
            ],
            capture_output=True,
            timeout=15,
            text=True,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        parts = proc.stdout.strip().split("x")
        if len(parts) != 2:
            return None
        w, h = int(parts[0]), int(parts[1])
        if w < 1 or h < 1:
            return None
        return w, h
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _scaled_output_dims(sw: int, sh: int, max_side: int) -> tuple[int, int]:
    side = max(64, int(max_side))
    m = max(sw, sh)
    if m <= side:
        return sw, sh
    if sw >= sh:
        dw = side
        dh = max(1, int(sh * side / sw))
    else:
        dh = side
        dw = max(1, int(sw * side / sh))
    return dw, dh


def _parse_showinfo_dims(stderr: bytes | str) -> tuple[int, int] | None:
    text = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
    last: tuple[int, int] | None = None
    for m in _SHOWINFO_DIMS_RE.finditer(text):
        w, h = int(m.group(1)), int(m.group(2))
        if w > 0 and h > 0:
            last = (w, h)
    return last


def _ffmpeg_raw_plane(path: Path, vf_chain: str, pix_fmt: str) -> tuple[bytes, int, int] | None:
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "info",
                "-i",
                str(path.resolve()),
                "-vf",
                f"{vf_chain},showinfo",
                "-frames:v",
                "1",
                "-pix_fmt",
                pix_fmt,
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            timeout=120,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("ffmpeg raw plane failed for %s: %s", path, e)
        return None
    if proc.returncode != 0 or not proc.stdout:
        err = (proc.stderr or b"").decode(errors="replace")[:240]
        logger.debug("ffmpeg raw plane rc=%s for %s: %s", proc.returncode, path, err)
        return None
    dims = _parse_showinfo_dims(proc.stderr or b"")
    if dims is None:
        return None
    w, h = dims
    bpp = 12 if pix_fmt.endswith("f32le") else 6
    expected = w * h * bpp
    if len(proc.stdout) < expected:
        return None
    return proc.stdout[:expected], w, h


def _decode_linear_from_raw_f32(raw: bytes, width: int, height: int) -> np.ndarray | None:
    expected = width * height * 12
    if len(raw) < expected or width < 1 or height < 1:
        return None
    gbr = np.frombuffer(raw[:expected], dtype=np.float32).reshape((height, width, 3))
    rgb = gbr[:, :, [2, 0, 1]]
    return np.ascontiguousarray(rgb)


def _decode_linear_from_raw_u16(raw: bytes, width: int, height: int) -> np.ndarray | None:
    expected = width * height * 6
    if len(raw) < expected or width < 1 or height < 1:
        return None
    u16 = np.frombuffer(raw[:expected], dtype=np.uint16).reshape((height, width, 3))
    return np.ascontiguousarray(u16.astype(np.float32) / 65535.0)


def _load_linear_rgb_f32(path: Path, max_side: int) -> np.ndarray | None:
    """Decode one plate to float32 RGB (linear/scene as stored in file)."""
    dims = _probe_image_size(path)
    if dims is None:
        return None
    sw, sh = dims
    if sw <= 0 or sh <= 0:
        return None
    dw, dh = _scaled_output_dims(sw, sh, max_side)
    vf = f"scale={dw}:{dh}:flags=lanczos"
    # rgb48le = stable RGB order on Windows ffmpeg; gbrpf32le keeps HDR headroom.
    attempts: tuple[str, str] = (
        ("rgb48le", "u16"),
        ("gbrpf32le", "f32"),
    )
    for pix_fmt, mode in attempts:
        got = _ffmpeg_raw_plane(path, vf, pix_fmt)
        if got is None:
            continue
        raw, width, height = got
        if mode == "u16":
            out = _decode_linear_from_raw_u16(raw, width, height)
        else:
            out = _decode_linear_from_raw_f32(raw, width, height)
        if out is not None:
            return out
    logger.warning("linear plate decode failed for %s", path.name)
    return None


def apply_ocio_display_transform(rgb_f32: np.ndarray, state: OcioPreviewState) -> np.ndarray | None:
    cpu = _get_cpu_processor(state)
    if cpu is None:
        return None
    out = np.array(rgb_f32, dtype=np.float32, copy=True)
    try:
        cpu.applyRGB(out)
    except Exception as e:
        logger.warning("OCIO applyRGB failed: %s", e)
        return None
    return out


def linear_rgb_to_qimage(rgb: np.ndarray) -> QImage | None:
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        return None
    clipped = np.clip(rgb, 0.0, 1.0)
    u8 = (clipped * 255.0 + 0.5).astype(np.uint8)
    h, w, _ = u8.shape
    if h < 1 or w < 1:
        return None
    try:
        header = f"P6\n{w} {h}\n255\n".encode("ascii")
        payload = header + u8.tobytes()
        qimg = QImage()
        if not qimg.loadFromData(payload, "PPM"):
            return None
    except Exception:
        return None
    return qimg if not qimg.isNull() else None


def load_ocio_preview_qimage(path: Path, max_side: int, state: OcioPreviewState) -> QImage | None:
    linear = _load_linear_rgb_f32(path, max_side)
    if linear is None:
        return None
    display = apply_ocio_display_transform(linear, state)
    if display is None:
        return None
    return linear_rgb_to_qimage(display)
