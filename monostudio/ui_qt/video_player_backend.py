"""Video playback backends: mpv embed, Qt Multimedia, external app."""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt, QTimer, Signal, QUrl
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from monostudio.core.mpv_resolve import ensure_mpv_dll_path, mpv_available, prepare_mpv_python_bindings
from monostudio.ui_qt.video_preview_settings import (
    BACKEND_AUTO,
    BACKEND_EXTERNAL,
    BACKEND_MPV,
    BACKEND_QT,
    read_video_external_player_exe,
    read_video_player_backend,
)

logger = logging.getLogger(__name__)

_SPEED_STEPS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


class VideoPlayerBackend(ABC):
    name: str = "base"

    def __init__(self) -> None:
        self._on_position: Callable[[float], None] | None = None
        self._on_duration: Callable[[float], None] | None = None
        self._on_ended: Callable[[], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._volume = 100

    def set_callbacks(
        self,
        *,
        on_position: Callable[[float], None] | None = None,
        on_duration: Callable[[float], None] | None = None,
        on_ended: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_position = on_position
        self._on_duration = on_duration
        self._on_ended = on_ended
        self._on_error = on_error

    @abstractmethod
    def attach_to_widget(self, widget: QWidget) -> None: ...

    @abstractmethod
    def load(self, path: Path) -> None: ...

    @abstractmethod
    def play(self) -> None: ...

    @abstractmethod
    def pause(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def seek(self, sec: float, *, precise: bool = False) -> None: ...

    @abstractmethod
    def duration(self) -> float: ...

    @abstractmethod
    def position(self) -> float: ...

    @abstractmethod
    def set_volume(self, volume: int) -> None: ...

    @abstractmethod
    def set_speed(self, speed: float) -> None: ...

    @abstractmethod
    def is_playing(self) -> bool: ...

    @abstractmethod
    def frame_step(self, direction: int) -> None: ...

    @abstractmethod
    def release(self) -> None: ...

    def prime_for_scrub(self) -> None:
        """Prepare decoder so paused seek/scrub shows frames (Qt backend)."""

    def layout_video(self) -> None:
        """Reposition embedded video after the host widget resizes."""

    @abstractmethod
    def supports_embed(self) -> bool: ...


class MpvEmbeddedBackend(VideoPlayerBackend):
    name = "mpv"

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self._settings = settings
        self._widget: QWidget | None = None
        self._player = None
        self._speed = 1.0
        self._duration = 0.0
        self._playing = False
        self._poll: QTimer | None = None
        self._pending_path: Path | None = None
        self._eof_notified = False
        self._scrub_primed = False
        self._prime_pending = False
        self._attached_wid: int | None = None

    def supports_embed(self) -> bool:
        return True

    @staticmethod
    def _mpv_error_benign(exc: BaseException) -> bool:
        """mpv returns -12 when seek/command runs before the file is ready."""
        if isinstance(exc, SystemError) and len(getattr(exc, "args", ())) >= 2:
            return exc.args[1] == -12
        return "-12" in str(exc)

    def _file_ready(self) -> bool:
        if self._player is None:
            return False
        try:
            if not bool(self._player.seekable):
                return False
            path = self._player.path
            return path is not None and str(path).strip() != ""
        except Exception:
            return False

    def _ensure_poll(self) -> None:
        if self._poll is not None and not self._poll.isActive():
            self._poll.start()

    def _widget_ready(self) -> bool:
        widget = self._widget
        return widget is not None and widget.isVisible() and widget.winId() != 0

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        if not self._widget_ready():
            return False
        ensure_mpv_dll_path(self._settings)
        prepare_mpv_python_bindings()
        try:
            import mpv

            wid = str(int(self._widget.winId()))  # type: ignore[union-attr]
            self._player = mpv.MPV(
                wid=wid,
                vo="gpu",
                keep_open="yes",
                idle="yes",
                hr_seek="yes",
                hr_seek_framedrop="no",
                input_default_bindings=False,
                input_vo_keyboard=False,
            )
            if self._poll is None and self._widget is not None:
                self._poll = QTimer(self._widget)
                self._poll.setInterval(100)
                self._poll.timeout.connect(self._tick_poll)
            if self._pending_path is not None:
                pending = self._pending_path
                self._pending_path = None
                self._load_path(pending)
            try:
                self._player.volume = self._volume
                self._player.speed = self._speed
            except Exception:
                pass
            self._ensure_poll()
            return True
        except Exception as e:
            logger.warning("mpv init failed: %s", e)
            if self._on_error:
                self._on_error(str(e))
            return False

    def _tick_poll(self) -> None:
        if self._player is None:
            return
        self._try_prime_for_scrub()
        try:
            pos = self._player.time_pos
            if pos is not None and self._on_position:
                self._on_position(float(pos))
            if self._duration <= 0.0:
                d = self._player.duration
                if d is not None:
                    self._duration = float(d)
                    if self._on_duration:
                        self._on_duration(self._duration)
            eof = bool(getattr(self._player, "eof_reached", False))
            if eof and not self._eof_notified:
                self._eof_notified = True
                if self._on_ended:
                    self._on_ended()
            elif not eof:
                self._eof_notified = False
        except Exception:
            pass

    def attach_to_widget(self, widget: QWidget) -> None:
        widget.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._widget = widget
        if not self._widget_ready():
            return
        wid = int(widget.winId())
        if self._player is not None:
            if self._attached_wid != wid:
                try:
                    self._player.wid = str(wid)
                    self._attached_wid = wid
                except Exception as e:
                    logger.debug("mpv wid attach: %s", e)
            if self._poll is not None and self._playing:
                self._poll.start()
            return
        if self._ensure_player() and self._player is not None:
            self._attached_wid = wid
            if self._poll is not None and self._playing:
                self._poll.start()

    def layout_video(self) -> None:
        return

    def _load_path(self, path: Path) -> None:
        if self._player is None:
            return
        try:
            self._player.command("loadfile", str(path), "replace")
            self._duration = 0.0
            self._eof_notified = False
            self._ensure_poll()
        except Exception as e:
            if self._mpv_error_benign(e):
                logger.debug("mpv loadfile deferred: %s", e)
                return
            if self._on_error:
                self._on_error(str(e))

    def load(self, path: Path) -> None:
        self._scrub_primed = False
        self._prime_pending = False
        self._pending_path = path
        if not self._ensure_player():
            return
        self._pending_path = None
        self._load_path(path)

    def prime_for_scrub(self) -> None:
        if self._scrub_primed:
            return
        self._prime_pending = True
        self._try_prime_for_scrub()

    def _try_prime_for_scrub(self) -> None:
        if not self._prime_pending or self._scrub_primed:
            return
        if not self._ensure_player() or self._player is None:
            return
        if not self._file_ready():
            self._ensure_poll()
            return
        self._prime_pending = False
        self._scrub_primed = True
        try:
            self._player.pause = True
            self._player.seek(0, reference="absolute", precision="keyframes")
            if self._on_position:
                self._on_position(0.0)
        except Exception as e:
            self._scrub_primed = False
            self._prime_pending = True
            if self._mpv_error_benign(e):
                logger.debug("mpv prime_for_scrub deferred: %s", e)
                self._ensure_poll()
                return
            if self._on_error:
                self._on_error(str(e))

    def play(self) -> None:
        if not self._ensure_player() or self._player is None:
            return
        try:
            self._player.pause = False
            self._playing = True
            self._eof_notified = False
            if self._poll:
                self._poll.start()
        except Exception as e:
            if self._on_error:
                self._on_error(str(e))

    def pause(self) -> None:
        if self._player is None:
            return
        try:
            self._player.pause = True
            self._playing = False
        except Exception as e:
            if self._on_error:
                self._on_error(str(e))

    def stop(self) -> None:
        self.pause()
        if self._poll:
            self._poll.stop()

    def seek(self, sec: float, *, precise: bool = False) -> None:
        if not self._ensure_player() or self._player is None:
            return
        if not self._file_ready():
            self._prime_pending = True
            self._ensure_poll()
            return
        try:
            self._eof_notified = False
            if precise:
                self._player.pause = True
            self._player.seek(
                max(0.0, float(sec)),
                reference="absolute",
                precision="exact" if precise else "keyframes",
            )
        except Exception as e:
            if self._mpv_error_benign(e):
                logger.debug("mpv seek deferred: %s", e)
                self._prime_pending = True
                self._ensure_poll()
                return
            if self._on_error:
                self._on_error(str(e))

    def duration(self) -> float:
        if self._player is None:
            return self._duration
        try:
            d = self._player.duration
            if d is not None:
                self._duration = float(d)
        except Exception:
            pass
        return self._duration

    def position(self) -> float:
        if self._player is None:
            return 0.0
        try:
            p = self._player.time_pos
            return float(p) if p is not None else 0.0
        except Exception:
            return 0.0

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        if self._player is None:
            return
        try:
            self._player.volume = self._volume
        except Exception:
            pass

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, min(4.0, float(speed)))
        if self._player is None:
            return
        try:
            self._player.speed = self._speed
        except Exception:
            pass

    def is_playing(self) -> bool:
        if self._player is None:
            return self._playing
        try:
            return not bool(self._player.pause)
        except Exception:
            return self._playing

    def frame_step(self, direction: int) -> None:
        if not self._ensure_player() or self._player is None:
            return
        self.pause()
        try:
            cmd = "frame-step" if direction >= 0 else "frame-back-step"
            self._player.command(cmd)
        except Exception as e:
            if self._mpv_error_benign(e):
                logger.debug("mpv frame_step deferred: %s", e)
                return
            if self._on_error:
                self._on_error(str(e))

    def release(self) -> None:
        if self._poll:
            self._poll.stop()
            self._poll = None
        if self._player is not None:
            try:
                self._player.command("quit")
            except Exception:
                pass
            try:
                self._player.terminate()
            except Exception:
                pass
            self._player = None
        self._pending_path = None
        self._playing = False
        self._prime_pending = False
        self._attached_wid = None


class QtMultimediaBackend(VideoPlayerBackend):
    name = "qt"

    def __init__(self) -> None:
        super().__init__()
        self._widget: QWidget | None = None
        self._player = None
        self._video = None
        self._speed = 1.0
        self._primed = False
        self._prime_playing = False

    def supports_embed(self) -> bool:
        return True

    def _ensure(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget

            parent = self._widget
            self._player = QMediaPlayer(parent)
            self._audio = QAudioOutput(parent)
            self._player.setAudioOutput(self._audio)
            self._video = QVideoWidget(parent)
            self._video.setObjectName("VideoPreviewSurface")
            self._video.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
            self._player.setVideoOutput(self._video)
            self._player.positionChanged.connect(lambda ms: self._on_position and self._on_position(ms / 1000.0))
            self._player.durationChanged.connect(lambda ms: self._on_duration and self._on_duration(ms / 1000.0))
            self._player.playbackStateChanged.connect(self._on_state)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            self._audio.setVolume(self._volume / 100.0)
            return True
        except Exception as e:
            logger.warning("Qt Multimedia init failed: %s", e)
            if self._on_error:
                self._on_error(str(e))
            return False

    def _media_ready(self) -> bool:
        if self._player is None:
            return False
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            return self._player.mediaStatus() in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
        except Exception:
            return False

    def _on_media_status(self, status) -> None:
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            if status in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            ):
                self.prime_for_scrub()
        except Exception:
            pass

    def prime_for_scrub(self) -> None:
        if self._primed or self._player is None or not self._media_ready():
            return
        self._primed = True
        try:
            self._prime_playing = True
            self._player.setPosition(0)
            self._player.play()
            self._player.pause()
            self._prime_playing = False
            if self._on_position:
                self._on_position(0.0)
        except Exception as e:
            self._primed = False
            logger.debug("Qt prime_for_scrub: %s", e)

    def _embed_video_widget(self) -> None:
        widget = self._widget
        if widget is None or self._video is None:
            return
        lay = widget.layout()
        if lay is not None and lay.indexOf(self._video) >= 0:
            lay.removeWidget(self._video)
        self._video.setParent(widget)
        self._layout_embedded_video()
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _layout_embedded_video(self) -> None:
        widget = self._widget
        if widget is None or self._video is None:
            return
        cr = widget.contentsRect()
        clip = 1
        h = max(0, cr.height() - clip)
        self._video.setGeometry(cr.left(), cr.top(), cr.width(), h)
        self._video.show()

    def _on_state(self, state) -> None:
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            if self._prime_playing:
                return
            if state == QMediaPlayer.PlaybackState.StoppedState and self._on_ended:
                self._on_ended()
        except Exception:
            pass

    def attach_to_widget(self, widget: QWidget) -> None:
        self._widget = widget
        if not self._ensure():
            return
        self._embed_video_widget()

    def layout_video(self) -> None:
        self._layout_embedded_video()

    def load(self, path: Path) -> None:
        if not self._ensure() or self._player is None:
            return
        self._primed = False
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        if self._media_ready():
            self.prime_for_scrub()

    def play(self) -> None:
        if self._player:
            self._player.play()

    def pause(self) -> None:
        if self._player:
            self._player.pause()

    def stop(self) -> None:
        if self._player:
            self._player.stop()

    def seek(self, sec: float, *, precise: bool = False) -> None:
        if self._player is None:
            return
        if not self._primed:
            self.prime_for_scrub()
        self._player.setPosition(int(max(0.0, sec) * 1000))

    def duration(self) -> float:
        if self._player:
            return self._player.duration() / 1000.0
        return 0.0

    def position(self) -> float:
        if self._player:
            return self._player.position() / 1000.0
        return 0.0

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        if getattr(self, "_audio", None):
            self._audio.setVolume(self._volume / 100.0)

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, min(4.0, float(speed)))
        if self._player:
            self._player.setPlaybackRate(self._speed)

    def is_playing(self) -> bool:
        if not self._player:
            return False
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        except Exception:
            return False

    def frame_step(self, direction: int) -> None:
        fps = 24.0
        step = 1.0 / fps
        self.seek(self.position() + (step if direction >= 0 else -step))

    def release(self) -> None:
        if self._player:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._video:
            self._video.deleteLater()
            self._video = None


class ExternalPlayerBackend(VideoPlayerBackend):
    name = "external"

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self._settings = settings
        self._path: Path | None = None

    def supports_embed(self) -> bool:
        return False

    def attach_to_widget(self, widget: QWidget) -> None:
        pass

    def layout_video(self) -> None:
        return

    def load(self, path: Path) -> None:
        self._path = path

    def play(self) -> None:
        if self._path is None:
            return
        exe = read_video_external_player_exe(self._settings)
        try:
            if exe and Path(exe).is_file():
                subprocess.Popen([exe, str(self._path)], cwd=str(self._path.parent))
            else:
                import os

                os.startfile(str(self._path.resolve()))
        except OSError as e:
            if self._on_error:
                self._on_error(str(e))

    def pause(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def seek(self, sec: float, *, precise: bool = False) -> None:
        pass

    def duration(self) -> float:
        return 0.0

    def position(self) -> float:
        return 0.0

    def set_volume(self, volume: int) -> None:
        self._volume = volume

    def set_speed(self, speed: float) -> None:
        pass

    def is_playing(self) -> bool:
        return False

    def frame_step(self, direction: int) -> None:
        pass

    def release(self) -> None:
        pass


def create_video_player_backend(settings: QSettings | None) -> VideoPlayerBackend:
    pref = read_video_player_backend(settings)
    if pref == BACKEND_EXTERNAL:
        return ExternalPlayerBackend(settings)
    if pref == BACKEND_MPV:
        if mpv_available(settings):
            return MpvEmbeddedBackend(settings)
        logger.info("mpv not available; falling back to Qt Multimedia")
    if pref == BACKEND_QT:
        return QtMultimediaBackend()
    # auto — same preference order as before
    if pref == BACKEND_AUTO and mpv_available(settings):
        return MpvEmbeddedBackend(settings)
    try:
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: F401

        return QtMultimediaBackend()
    except ImportError:
        return ExternalPlayerBackend(settings)


def next_speed(current: float, *, direction: int) -> float:
    steps = list(_SPEED_STEPS)
    if current in steps:
        idx = steps.index(current)
    else:
        idx = min(range(len(steps)), key=lambda i: abs(steps[i] - current))
    idx = max(0, min(len(steps) - 1, idx + direction))
    return steps[idx]
