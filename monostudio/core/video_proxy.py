"""Build H.264 review proxies for Video Preview (FFmpeg)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from monostudio.core.ffmpeg_resolve import resolve_ffmpeg_executable
from monostudio.core.subprocess_win import hide_console_subprocess_kwargs
from monostudio.core.video_media import VideoInfo, VideoFrameRange

logger = logging.getLogger(__name__)

_HEAVY_CODECS = frozenset(
    {
        "prores",
        "dnxhd",
        "dnxhr",
        "hevc",
        "h265",
        "v210",
        "r210",
        "ffv1",
        "mjpeg",
        "mpeg2video",
        "av1",
        "vp9",
    }
)

ProgressCallback = Callable[[float], None]
CancelCheck = Callable[[], bool]


def proxy_scale_dimensions(width: int, height: int, scale: float) -> tuple[int, int]:
    sw = max(2, int(round(max(1, width) * float(scale))))
    sh = max(2, int(round(max(1, height) * float(scale))))
    sw = max(2, sw // 2 * 2)
    sh = max(2, sh // 2 * 2)
    return sw, sh


def is_heavy_source_for_proxy(
    info: VideoInfo,
    *,
    file_size_bytes: int,
) -> tuple[bool, str]:
    reasons: list[str] = []
    if info.duration_sec > 300:
        reasons.append(f"{int(info.duration_sec // 60)} min")
    if info.width * info.height >= 3840 * 2160:
        reasons.append(f"{info.width}×{info.height}")
    codec = (info.video_codec or "").strip().lower()
    if codec in _HEAVY_CODECS or any(c in codec for c in _HEAVY_CODECS):
        reasons.append(codec or "heavy codec")
    if file_size_bytes > 500 * 1024 * 1024:
        gb = file_size_bytes / (1024**3)
        reasons.append(f"{gb:.1f} GB")
    if not reasons:
        return False, ""
    return True, " · ".join(reasons)


def _gop_for_fps(fps: float) -> int:
    return max(12, int(round(max(1.0, fps))))


def _proxy_part_path(dst: Path) -> Path:
    """Unique temp path FFmpeg can mux as MP4 (avoids stale locked ``.part.mp4``)."""
    return dst.with_name(f"{dst.stem}.{uuid.uuid4().hex[:10]}.part{dst.suffix}")


def _finalize_proxy_part(part: Path, dst: Path) -> None:
    if not part.is_file():
        raise RuntimeError("Proxy build produced no output")
    try:
        if part.stat().st_size < 256:
            raise RuntimeError("Proxy build produced empty output")
    except OSError as e:
        raise RuntimeError(f"Proxy build output missing: {e}") from e

    dst.parent.mkdir(parents=True, exist_ok=True)
    last_err: OSError | None = None
    for attempt in range(12):
        try:
            if dst.is_file():
                dst.unlink()
            os.replace(part, dst)
            return
        except OSError as e:
            last_err = e
            if attempt + 1 < 12:
                time.sleep(0.15 * (attempt + 1))
    raise RuntimeError(f"Could not finalize proxy file: {last_err}") from last_err


def _remove_stale_proxy_parts(dst: Path) -> None:
    """Best-effort cleanup of old part files for this proxy target."""
    pattern = f"{dst.stem}.*.part{dst.suffix}"
    for stale in dst.parent.glob(pattern):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass
    legacy = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    try:
        legacy.unlink(missing_ok=True)
    except OSError:
        pass


def _scale_filter(width: int, height: int, scale: float) -> str | None:
    w, h = proxy_scale_dimensions(width, height, scale)
    if w == width and h == height:
        return None
    return f"scale={w}:{h}:flags=lanczos"


def _encode_args(dst: Path, *, fps: float) -> list[str]:
    gop = _gop_for_fps(fps)
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dst),
    ]


def _run_ffmpeg_with_progress(
    args: list[str],
    *,
    duration_sec: float,
    progress_callback: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> None:
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **hide_console_subprocess_kwargs(),
    )
    out_time_re = re.compile(r"^out_time_ms=(\d+)")
    stderr_tail: list[str] = []
    try:
        assert proc.stdout is not None
        assert proc.stderr is not None
        for line in proc.stdout:
            if cancel_check and cancel_check():
                proc.terminate()
                raise RuntimeError("Proxy build cancelled")
            m = out_time_re.match(line.strip())
            if m and progress_callback and duration_sec > 0:
                out_ms = int(m.group(1))
                progress_callback(min(1.0, (out_ms / 1_000_000.0) / duration_sec))
        stderr_text = proc.stderr.read() or ""
        if stderr_text:
            stderr_tail = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()][-4:]
        code = proc.wait()
        if code != 0:
            detail = stderr_tail[-1] if stderr_tail else f"exit code {code}"
            raise RuntimeError(f"FFmpeg proxy failed: {detail}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        # Windows: brief yield so FFmpeg releases the output handle before finalize.
        time.sleep(0.05)


def build_video_proxy_range(
    src: Path,
    dst: Path,
    *,
    start_sec: float,
    end_sec: float,
    scale: float,
    src_info: VideoInfo,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> None:
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found. Configure it in Settings → Tools.")
    if end_sec <= start_sec:
        raise ValueError("Invalid proxy range")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _remove_stale_proxy_parts(dst)
    part = _proxy_part_path(dst)
    vf = _scale_filter(src_info.width, src_info.height, scale)
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-ss",
        f"{max(0.0, start_sec):.6f}",
        "-to",
        f"{max(0.0, end_sec):.6f}",
        "-i",
        str(src),
    ]
    if vf:
        args.extend(["-vf", vf])
    args.extend(_encode_args(part, fps=src_info.fps))
    duration = max(0.01, end_sec - start_sec)
    _run_ffmpeg_with_progress(
        args,
        duration_sec=duration,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )
    _finalize_proxy_part(part, dst)


def build_video_proxy_full(
    src: Path,
    dst: Path,
    *,
    scale: float,
    src_info: VideoInfo,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> None:
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found. Configure it in Settings → Tools.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _remove_stale_proxy_parts(dst)
    part = _proxy_part_path(dst)
    vf = _scale_filter(src_info.width, src_info.height, scale)
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-i",
        str(src),
    ]
    if vf:
        args.extend(["-vf", vf])
    args.extend(_encode_args(part, fps=src_info.fps))
    duration = max(0.01, src_info.duration_sec)
    _run_ffmpeg_with_progress(
        args,
        duration_sec=duration,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )
    _finalize_proxy_part(part, dst)


def format_heavy_proxy_message(info: VideoInfo, *, file_size_bytes: int, reason: str) -> str:
    try:
        gb = file_size_bytes / (1024**3)
        size_s = f"{gb:.1f} GB" if gb >= 0.1 else f"{file_size_bytes // (1024 * 1024)} MB"
    except (TypeError, ValueError):
        size_s = "?"
    mins = int(info.duration_sec // 60)
    secs = int(info.duration_sec % 60)
    dur = f"{mins}:{secs:02d}" if mins else f"{secs}s"
    extra = f" — {reason}" if reason else ""
    return f"{info.width}×{info.height} · {dur} · {size_s}{extra}"
