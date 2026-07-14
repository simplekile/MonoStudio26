"""Review playback backends — video (mpv) and image sequence decode."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from monostudio.ui_qt.sequence_preview_decode import PREVIEW_MAX_SIDE_DEFAULT, load_preview_frame_qimage
from monostudio.ui_qt.video_player_backend import VideoPlayerBackend, create_video_player_backend

logger = logging.getLogger(__name__)


@runtime_checkable
class ReviewPlaybackBackend(Protocol):
    def frame_count(self) -> int: ...
    def fps(self) -> float: ...
    def current_frame(self) -> int: ...
    def seek_frame(self, frame: int, *, exact: bool = True) -> None: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def is_playing(self) -> bool: ...
    def display_target(self) -> QWidget: ...
    def release(self) -> None: ...


class _DecodeSignaler(QObject):
    frame_ready = Signal(int, object)  # index, QImage | None


class _DecodeGeneration:
    """Outlives backend QObject so pool runnables can drop stale work after release()."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = 0

    def bump(self) -> int:
        self.value += 1
        return self.value


class _DecodeRunnable(QRunnable):
    def __init__(
        self,
        idx: int,
        path: Path,
        max_side: int,
        signaler: _DecodeSignaler,
        generation: _DecodeGeneration,
        gen_at_start: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._idx = idx
        self._path = path
        self._max_side = max_side
        self._signaler = signaler
        self._generation = generation
        self._gen_at_start = gen_at_start

    def run(self) -> None:
        if self._generation.value != self._gen_at_start:
            return
        img = load_preview_frame_qimage(self._path, self._max_side)
        if self._generation.value != self._gen_at_start:
            return
        try:
            self._signaler.frame_ready.emit(self._idx, img)
        except RuntimeError:
            pass


class SequenceDecodeBackend(QObject):
    """Flipbook decode on thread pool with small RAM buffer."""

    frame_changed = Signal(int)
    playback_ended = Signal()

    _BUFFER_CAP = 20
    _PREFETCH_LIGHT = 8
    _PREFETCH_HEAVY = 3
    _SCALED_CACHE_CAP = 12

    def __init__(
        self,
        frames: list[Path],
        *,
        fps: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._frames = list(frames)
        self._fps = max(1, min(60, int(fps)))
        self._n = len(self._frames)
        self._current = 0
        self._playing = False
        self._buffer: dict[int, QPixmap] = {}
        self._buffer_decode_side: dict[int, int] = {}
        self._scaled_cache: dict[tuple[int, int, int], QPixmap] = {}
        self._in_flight: set[int] = set()
        self._in_flight_decode_side: dict[int, int] = {}
        self._decode_generation = _DecodeGeneration()
        self._decode_live = True
        self._signaler = _DecodeSignaler(self)
        self._signaler.frame_ready.connect(self._on_frame_ready, Qt.ConnectionType.QueuedConnection)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(6)
        self._heavy = self._detect_heavy_sequence()
        self._prefetch_n = self._PREFETCH_HEAVY if self._heavy else self._PREFETCH_LIGHT
        self._label_full_pix: QPixmap | None = None
        self._decode_bucket: int | None = None
        self._viewport_w = 0
        self._viewport_h = 0
        self._viewport_dpr = 0.0
        self._loop_start = 0
        self._loop_end: int | None = None
        self._loop_enabled = False
        self._pending_next: int | None = None
        self._preview_scale = 1.0

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._label.setStyleSheet("background-color: #121214;")

        self._tick_timer = QTimer(self)
        self._tick_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick_timer.setSingleShot(False)
        self._tick_timer.timeout.connect(self._on_tick)

    def set_viewport_size(self, width: int, height: int, dpr: float = 0.0) -> None:
        """Viewer plate size in logical pixels — used for decode + display scaling."""
        w = max(1, int(width))
        h = max(1, int(height))
        d = float(dpr) if dpr > 0 else max(1.0, float(self._label.devicePixelRatioF()))
        self._viewport_w = w
        self._viewport_h = h
        self._viewport_dpr = d

    def prime_display(self) -> None:
        """Decode first frames after the viewer has its final layout size."""
        if self._n <= 0:
            return
        self._decode_bucket = self._decode_bucket_for_label()
        self._scaled_cache.clear()
        self._buffer.clear()
        self._buffer_decode_side.clear()
        self._in_flight.clear()
        self._in_flight_decode_side.clear()
        self._decode_generation.bump()
        self._request_decode(self._current)
        self._prefetch_from(self._current)

    def prepare_for_playback(self) -> None:
        """Ensure decode bucket matches viewport before play / prefetch."""
        self._upgrade_decode_if_needed()

    def frame_count(self) -> int:
        return max(1, self._n)

    def fps(self) -> float:
        return float(self._fps)

    def set_fps(self, fps: int) -> None:
        self._fps = max(1, min(60, int(fps)))
        self._tick_timer.setInterval(max(1, round(1000 / self._fps)))

    def set_preview_scale(self, scale: float) -> None:
        """Decode resolution multiplier (1, ½, ¼, …) for heavy plate sequences."""
        clamped = max(0.125, min(1.0, float(scale)))
        if abs(clamped - self._preview_scale) < 1e-6:
            return
        self._preview_scale = clamped
        self._invalidate_decode_cache()
        self._request_decode(self._current)
        if self._current in self._buffer and self._frame_meets_target(self._current):
            self._apply_pixmap(self._current, self._buffer[self._current])
            self.frame_changed.emit(self._current)

    def invalidate_frame_cache(self) -> None:
        """Drop decoded frames after OCIO / preview settings change."""
        self._invalidate_decode_cache()
        self._request_decode(self._current)

    def current_frame(self) -> int:
        return self._current

    def current_frame_image(self) -> QImage | None:
        """Decoded buffer for the playhead (may be viewport-scaled)."""
        pix = self._buffer.get(self._current)
        if pix is None or pix.isNull():
            pix = self._label_full_pix
        if pix is None or pix.isNull():
            return None
        img = pix.toImage()
        return None if img.isNull() else img

    def display_target(self) -> QWidget:
        return self._label

    def is_playing(self) -> bool:
        return self._playing

    def seek_frame(self, frame: int, *, exact: bool = True) -> None:
        del exact
        if self._n <= 0:
            return
        idx = max(0, min(self._n - 1, int(frame)))
        if idx == self._current:
            if self._frame_meets_target(idx):
                return
            if idx in self._buffer:
                del self._buffer[idx]
                self._buffer_decode_side.pop(idx, None)
            self._request_decode(idx)
            return
        self._pending_next = None
        self._current = idx
        if self._frame_meets_target(idx):
            self._apply_pixmap(idx, self._buffer[idx])
        else:
            if idx in self._buffer:
                del self._buffer[idx]
                self._buffer_decode_side.pop(idx, None)
            self._request_decode(idx)
        self.frame_changed.emit(self._current)

    def play(self) -> None:
        if self._n <= 0:
            return
        self.prepare_for_playback()
        self._playing = True
        self._pending_next = None
        self._scaled_cache.clear()
        self._tick_timer.setInterval(max(1, round(1000 / self._fps)))
        if not self._tick_timer.isActive():
            self._tick_timer.start()
        self._prefetch_from(self._current)

    def pause(self) -> None:
        self._playing = False
        self._pending_next = None
        self._tick_timer.stop()

    def release(self) -> None:
        self.pause()
        self._decode_live = False
        self._decode_generation.bump()
        try:
            self._signaler.frame_ready.disconnect(self._on_frame_ready)
        except (TypeError, RuntimeError):
            pass
        self._pool.clear()
        self._buffer.clear()
        self._buffer_decode_side.clear()
        self._scaled_cache.clear()
        self._in_flight.clear()
        self._in_flight_decode_side.clear()

    def set_loop_region(self, start: int, end: int, *, enabled: bool) -> None:
        """Playback loop clamp — when enabled, wraps start..end inclusive."""
        self._loop_start = max(0, int(start))
        self._loop_end = max(self._loop_start, int(end))
        self._loop_enabled = bool(enabled)

    def _decode_bucket_for_label(self) -> int:
        return max(64, (self._decode_max_side() // 64) * 64)

    def _target_decode_side(self) -> int:
        if self._decode_bucket is not None:
            return self._decode_bucket
        return self._decode_bucket_for_label()

    def _buffer_side_for(self, idx: int) -> int:
        pix = self._buffer.get(idx)
        if pix is None or pix.isNull():
            return 0
        return max(pix.width(), pix.height())

    def _frame_meets_target(self, idx: int) -> bool:
        if idx not in self._buffer:
            return False
        pix = self._buffer.get(idx)
        if pix is None or pix.isNull():
            return False
        requested = self._buffer_decode_side.get(idx, 0)
        target = self._target_decode_side()
        # Viewport grew since this frame was decoded — need a higher-res pass.
        if requested < target - 32:
            return False
        actual = self._buffer_side_for(idx)
        if actual < 8:
            return False
        # Plates are never upscaled: a 640px flipbook decoded at a 1088px bucket is final.
        return True

    def _invalidate_decode_cache(self) -> None:
        self._decode_generation.bump()
        self._buffer.clear()
        self._buffer_decode_side.clear()
        self._in_flight.clear()
        self._in_flight_decode_side.clear()
        self._scaled_cache.clear()

    def _upgrade_decode_if_needed(self) -> bool:
        """Re-decode when the viewer grew or cached frames are below target resolution."""
        bucket = self._decode_bucket_for_label()
        prev = self._decode_bucket
        self._decode_bucket = bucket
        stale = any(
            not self._frame_meets_target(k)
            for k in list(self._buffer)
        )
        if prev is not None and bucket == prev and not stale:
            if self._frame_meets_target(self._current):
                return False
        self._invalidate_decode_cache()
        self._request_decode(self._current)
        if self._playing:
            self._prefetch_from(self._current)
        return True

    def resize_display(self) -> None:
        if self._upgrade_decode_if_needed():
            return
        self._scaled_cache.clear()
        if self._current in self._buffer:
            self._apply_pixmap(self._current, self._buffer[self._current])

    def cached_pixmap_at(self, frame: int) -> QPixmap | None:
        idx = max(0, min(self._n - 1, int(frame)))
        return self._buffer.get(idx)

    def _detect_heavy_sequence(self) -> bool:
        heavy = {".exr", ".hdr"}
        if not self._frames:
            return False
        for p in (self._frames[0], self._frames[-1]):
            if p.suffix.lower() not in heavy:
                return False
        return True

    def _display_logical_size(self) -> tuple[int, int]:
        if self._viewport_w > 0 and self._viewport_h > 0:
            return self._viewport_w, self._viewport_h
        w = max(1, self._label.width())
        h = max(1, self._label.height())
        parent = self._label.parentWidget()
        if parent is not None:
            if w < 32:
                w = max(w, parent.width())
            if h < 32:
                h = max(h, parent.height())
        return max(1, w), max(1, h)

    def _decode_max_side(self) -> int:
        w, h = self._display_logical_size()
        dpr = (
            self._viewport_dpr
            if self._viewport_dpr > 0
            else max(1.0, float(self._label.devicePixelRatioF()))
        )
        side = int(max(w, h) * dpr * self._preview_scale)
        return max(64, min(PREVIEW_MAX_SIDE_DEFAULT, ((side + 31) // 32) * 32))

    def _request_decode(self, idx: int) -> None:
        if not self._decode_live:
            return
        if idx < 0 or idx >= self._n:
            return
        if idx in self._buffer:
            if self._frame_meets_target(idx):
                return
            del self._buffer[idx]
            self._buffer_decode_side.pop(idx, None)
        if idx in self._in_flight:
            return
        self._in_flight.add(idx)
        decode_side = self._target_decode_side()
        self._in_flight_decode_side[idx] = decode_side
        gen = self._decode_generation.value
        self._pool.start(
            _DecodeRunnable(
                idx,
                self._frames[idx],
                decode_side,
                self._signaler,
                self._decode_generation,
                gen,
            )
        )

    def _on_frame_ready(self, idx: int, image: object) -> None:
        if not self._decode_live:
            self._in_flight.discard(idx)
            return
        self._in_flight.discard(idx)
        if idx < 0 or idx >= self._n:
            return
        if isinstance(image, QImage) and not image.isNull():
            pix = QPixmap.fromImage(image)
            if not pix.isNull():
                self._buffer[idx] = pix
                self._buffer_decode_side[idx] = self._in_flight_decode_side.pop(
                    idx, self._target_decode_side()
                )
                self._trim_buffer()
        if not self._playing:
            if idx == self._current and self._frame_meets_target(idx):
                self._apply_pixmap(idx, self._buffer[idx])
                self.frame_changed.emit(self._current)
            return
        pending = self._pending_next
        if pending is not None and idx == pending and self._frame_meets_target(idx):
            self._pending_next = None
            self._apply_pixmap(idx, self._buffer[idx])
        elif idx == self._current and self._frame_meets_target(idx):
            self._apply_pixmap(idx, self._buffer[idx])

    def _playback_end_frame(self) -> int:
        if self._n <= 0:
            return 0
        end = self._loop_end if self._loop_end is not None else (self._n - 1)
        return max(0, min(self._n - 1, end))

    def _playback_start_frame(self) -> int:
        if self._n <= 0:
            return 0
        return max(0, min(self._n - 1, self._loop_start))

    def _next_playback_frame(self) -> int | None:
        start = self._playback_start_frame()
        end = self._playback_end_frame()
        if self._current >= end:
            if self._loop_enabled:
                return start
            return None
        return self._current + 1

    def _finish_playback(self) -> None:
        if self._loop_enabled:
            self._advance_to(self._playback_start_frame())
            return
        end = self._playback_end_frame()
        self._playing = False
        self._pending_next = None
        self._tick_timer.stop()
        if end in self._buffer and self._frame_meets_target(end):
            self._show_frame(end)
        self.playback_ended.emit()

    def _advance_to(self, nxt: int) -> None:
        if nxt < 0 or nxt >= self._n:
            return
        if self._frame_meets_target(nxt):
            self._pending_next = None
            self._show_frame(nxt)
            self._prefetch_from(nxt)
            self._prefetch_loop_head_if_near_end(nxt)
            return
        if nxt in self._buffer:
            del self._buffer[nxt]
            self._buffer_decode_side.pop(nxt, None)
        self._pending_next = nxt
        if nxt != self._current:
            self._current = nxt
            self.frame_changed.emit(nxt)
        self._request_decode(nxt)
        self._prefetch_from(nxt)
        self._prefetch_loop_head_if_near_end(nxt)

    def _prefetch_loop_head_if_near_end(self, base: int) -> None:
        if not self._loop_enabled:
            return
        end = self._playback_end_frame()
        start = self._playback_start_frame()
        if base < end - self._prefetch_n:
            return
        for k in range(start, min(start + self._prefetch_n, end + 1)):
            self._request_decode(k)

    def _show_frame(self, idx: int) -> None:
        if idx < 0 or idx >= self._n or not self._frame_meets_target(idx):
            return
        self._current = idx
        self._apply_pixmap(idx, self._buffer[idx])
        self.frame_changed.emit(self._current)

    def _trim_buffer(self) -> None:
        while len(self._buffer) > self._BUFFER_CAP:
            best_k = None
            best_d = -1
            for k in self._buffer:
                d = abs(k - self._current)
                if d > best_d:
                    best_d = d
                    best_k = k
            if best_k is not None:
                del self._buffer[best_k]
                self._buffer_decode_side.pop(best_k, None)
            else:
                break

    def _trim_scaled_cache(self) -> None:
        while len(self._scaled_cache) > self._SCALED_CACHE_CAP:
            self._scaled_cache.pop(next(iter(self._scaled_cache)))

    def _apply_pixmap(self, idx: int, pix: QPixmap) -> None:
        self._label_full_pix = pix
        if pix.isNull():
            return
        lw, lh = self._display_logical_size()
        pix_side = max(pix.width(), pix.height())
        decode_tag = self._buffer_decode_side.get(idx, 0)
        key = (idx, lw, lh, pix_side, decode_tag)
        cached = self._scaled_cache.get(key)
        if cached is not None and not cached.isNull():
            self._label.setPixmap(cached)
            return
        scaled = pix.scaled(
            lw,
            lh,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.isNull():
            return
        self._scaled_cache[key] = scaled
        self._trim_scaled_cache()
        self._label.setPixmap(scaled)

    def _prefetch_from(self, base: int) -> None:
        end = self._playback_end_frame()
        start = self._playback_start_frame()
        for k in range(1, self._prefetch_n + 1):
            if self._loop_enabled:
                span = end - start + 1
                if span <= 0:
                    continue
                j = start + ((base - start + k) % span)
            else:
                j = base + k
                if j >= self._n:
                    break
            self._request_decode(j)

    def _on_tick(self) -> None:
        if not self._playing or self._n <= 0:
            return
        if self._pending_next is not None:
            self._advance_to(self._pending_next)
            return
        nxt = self._next_playback_frame()
        if nxt is None:
            self._finish_playback()
            return
        self._advance_to(nxt)


class VideoMpvBackend:
    """Thin adapter wrapping VideoPlayerBackend for frame-centric review API."""

    def __init__(self, settings=None) -> None:
        self._backend: VideoPlayerBackend = create_video_player_backend(settings)
        self._fps = 24.0
        self._frame_count = 1
        self._on_frame: callable | None = None

    @property
    def inner(self) -> VideoPlayerBackend:
        return self._backend

    def set_callbacks(
        self,
        *,
        on_position=None,
        on_duration=None,
        on_ended=None,
        on_error=None,
    ) -> None:
        self._backend.set_callbacks(
            on_position=on_position,
            on_duration=on_duration,
            on_ended=on_ended,
            on_error=on_error,
        )

    def set_timeline(self, *, fps: float, frame_count: int) -> None:
        self._fps = max(1e-6, float(fps))
        self._frame_count = max(1, int(frame_count))

    def frame_count(self) -> int:
        return self._frame_count

    def fps(self) -> float:
        return self._fps

    def current_frame(self) -> int:
        from monostudio.core.video_media import sec_to_frame

        return sec_to_frame(self._backend.position(), self._fps)

    def seek_frame(self, frame: int, *, exact: bool = True) -> None:
        sec = max(0, int(frame)) / self._fps
        self._backend.seek(sec, precise=exact)

    def play(self) -> None:
        self._backend.play()

    def pause(self) -> None:
        self._backend.pause()

    def is_playing(self) -> bool:
        return self._backend.is_playing()

    def display_target(self) -> QWidget:
        raise NotImplementedError("Video uses mpv attach_to_widget on surface")

    def release(self) -> None:
        self._backend.stop()
        self._backend.release()
