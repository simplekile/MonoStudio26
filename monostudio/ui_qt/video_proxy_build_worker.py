"""Background proxy build worker for Video Preview."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, QRunnable, Signal

from monostudio.core.video_media import VideoFrameRange, VideoInfo
from monostudio.core.video_proxy import (
    build_video_proxy_full,
    build_video_proxy_range,
    proxy_scale_dimensions,
)
from monostudio.core.video_proxy_cache import (
    ProxyManifest,
    full_proxy_paths,
    range_proxy_paths,
    source_digest,
    write_manifest,
)


class ProxyBuildSignaler(QObject):
    progress = Signal(float)
    finished = Signal(object, object)  # ProxyManifest | None, error str | None


class ProxyBuildRunnable(QRunnable):
    def __init__(
        self,
        *,
        mode: Literal["full", "range"],
        src: Path,
        src_info: VideoInfo,
        scale: float,
        rng: VideoFrameRange | None,
        signaler: ProxyBuildSignaler,
        cancel_flag: list[bool],
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._mode = mode
        self._src = src
        self._src_info = src_info
        self._scale = scale
        self._rng = rng
        self._signaler = signaler
        self._cancel_flag = cancel_flag

    def run(self) -> None:
        try:
            digest, mtime_ns, size = source_digest(self._src)
            try:
                source_path = str(self._src.resolve())
            except OSError:
                source_path = str(self._src)

            if self._mode == "full":
                mp4, manifest_path = full_proxy_paths(digest, self._scale)
                build_video_proxy_full(
                    self._src,
                    mp4,
                    scale=self._scale,
                    src_info=self._src_info,
                    progress_callback=self._signaler.progress.emit,
                    cancel_check=self._cancelled,
                )
                w, h = proxy_scale_dimensions(
                    self._src_info.width, self._src_info.height, self._scale
                )
                manifest = ProxyManifest(
                    mode="full",
                    source_path=source_path,
                    source_mtime_ns=mtime_ns,
                    source_size=size,
                    scale=self._scale,
                    proxy_path=str(mp4),
                    range_id=None,
                    in_frame=0,
                    out_frame=max(0, self._src_info.frame_count - 1),
                    clip_duration_sec=self._src_info.duration_sec,
                    clip_frame_count=self._src_info.frame_count,
                    width=w,
                    height=h,
                    fps=self._src_info.fps,
                    created_at=time.time(),
                )
            else:
                if self._rng is None:
                    raise ValueError("Range required for range proxy build")
                mp4, manifest_path = range_proxy_paths(digest, self._rng.id, self._scale)
                lo, hi = sorted((self._rng.in_frame, self._rng.out_frame))
                fps = max(1e-6, self._src_info.fps)
                start_sec = lo / fps
                end_sec = (hi + 1) / fps
                build_video_proxy_range(
                    self._src,
                    mp4,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    scale=self._scale,
                    src_info=self._src_info,
                    progress_callback=self._signaler.progress.emit,
                    cancel_check=self._cancelled,
                )
                w, h = proxy_scale_dimensions(
                    self._src_info.width, self._src_info.height, self._scale
                )
                clip_frames = hi - lo + 1
                manifest = ProxyManifest(
                    mode="range",
                    source_path=source_path,
                    source_mtime_ns=mtime_ns,
                    source_size=size,
                    scale=self._scale,
                    proxy_path=str(mp4),
                    range_id=self._rng.id,
                    in_frame=lo,
                    out_frame=hi,
                    clip_duration_sec=clip_frames / fps,
                    clip_frame_count=clip_frames,
                    width=w,
                    height=h,
                    fps=self._src_info.fps,
                    created_at=time.time(),
                )
            write_manifest(manifest_path, manifest)
            self._signaler.finished.emit(manifest, None)
        except Exception as e:
            self._signaler.finished.emit(None, str(e))

    def _cancelled(self) -> bool:
        return bool(self._cancel_flag and self._cancel_flag[0])
