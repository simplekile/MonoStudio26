"""Play phase-end alert: custom audio file, or system beep fallback."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWidgets import QApplication

_log = logging.getLogger("monostudio.pomodoro.sound")

_AUDIO_EXTS = frozenset({".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".aiff", ".aif"})
_BEEP_LOOP_INTERVAL_MS = 1400
_BEEP_LOOP_MAX_MS = 45_000
_MAX_FINITE_LOOPS = 99

# Keep players alive so playback is not GC'd mid-play.
_holder: QObject | None = None
_effect = None
_player = None
_audio_out = None
_loops_remaining = 0  # 0 = not looping; -1 = infinite; >0 = remaining replay after current
_beep_timer: QTimer | None = None
_beep_deadline_timer: QTimer | None = None
_beep_left = 0
_media_loop_connected = False


def is_supported_sound_path(path: str | Path) -> bool:
    p = Path(path)
    return p.suffix.lower() in _AUDIO_EXTS


def sound_file_filter() -> str:
    return (
        "Audio (*.wav *.mp3 *.ogg *.flac *.m4a *.aac *.aiff *.aif);;"
        "WAV (*.wav);;All files (*.*)"
    )


def clamp_loop_count(n: int) -> int:
    """0 = until stopped; 1..99 = play that many times."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return 1
    if v <= 0:
        return 0
    return min(_MAX_FINITE_LOOPS, v)


def play_alert_sound(path: str | None = None, *, loop_count: int = 1) -> None:
    """Play custom sound if path is a readable file; otherwise system beep.

    loop_count:
      1  → play once
      N  → play N times
      0  → loop until stop_alert_sound() (beep has a safety timeout)
    """
    stop_alert_sound()
    global _loops_remaining
    count = clamp_loop_count(loop_count)
    if count == 0:
        _loops_remaining = -1
    elif count <= 1:
        _loops_remaining = 0
    else:
        # Current play + (count - 1) remaining restarts after EndOfMedia / beep ticks.
        _loops_remaining = count - 1

    raw = (path or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file() and is_supported_sound_path(p):
            if _play_file(p, loop_count=count):
                return
            _log.warning("Custom sound failed; falling back to beep: %s", p)
    _start_beep(loop_count=count)


def stop_alert_sound() -> None:
    """Stop any looping or one-shot alert playback."""
    global _loops_remaining, _beep_left
    _loops_remaining = 0
    _beep_left = 0
    _stop_beep_loop()
    try:
        if _effect is not None:
            _effect.setLoopCount(1)
            _effect.stop()
    except Exception:
        pass
    try:
        if _player is not None:
            _player.stop()
    except Exception:
        pass


def _beep() -> None:
    app = QApplication.instance()
    if app is not None:
        app.beep()


def _on_beep_tick() -> None:
    global _beep_left, _loops_remaining
    if _beep_left == 0 and _loops_remaining == 0:
        _stop_beep_loop()
        return
    _beep()
    if _beep_left > 0:
        _beep_left -= 1
        if _beep_left <= 0:
            _stop_beep_loop()
            _loops_remaining = 0
    elif _loops_remaining < 0:
        pass  # infinite until deadline / stop
    else:
        _stop_beep_loop()


def _start_beep(*, loop_count: int) -> None:
    global _beep_timer, _beep_deadline_timer, _beep_left
    _beep()
    if loop_count == 1:
        return
    holder = _ensure_holder()
    if loop_count == 0:
        _beep_left = -1
    else:
        _beep_left = max(0, loop_count - 1)
    if _beep_timer is None:
        _beep_timer = QTimer(holder)
        _beep_timer.setInterval(_BEEP_LOOP_INTERVAL_MS)
        _beep_timer.timeout.connect(_on_beep_tick)
    _beep_timer.start()
    if loop_count == 0:
        if _beep_deadline_timer is None:
            _beep_deadline_timer = QTimer(holder)
            _beep_deadline_timer.setSingleShot(True)
            _beep_deadline_timer.timeout.connect(stop_alert_sound)
        _beep_deadline_timer.start(_BEEP_LOOP_MAX_MS)


def _stop_beep_loop() -> None:
    if _beep_timer is not None:
        _beep_timer.stop()
    if _beep_deadline_timer is not None:
        _beep_deadline_timer.stop()


def _ensure_holder() -> QObject:
    global _holder
    if _holder is None:
        app = QApplication.instance()
        _holder = QObject(app)
    return _holder


def _play_file(path: Path, *, loop_count: int) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        if _play_sound_effect(path, loop_count=loop_count):
            return True
    return _play_media_player(path, loop_count=loop_count)


def _play_sound_effect(path: Path, *, loop_count: int) -> bool:
    global _effect
    try:
        from PySide6.QtMultimedia import QSoundEffect
    except Exception:
        return False
    try:
        holder = _ensure_holder()
        if _effect is None:
            _effect = QSoundEffect(holder)
        _effect.stop()
        _effect.setSource(QUrl.fromLocalFile(str(path.resolve())))
        _effect.setVolume(0.9)
        if loop_count == 0:
            _effect.setLoopCount(QSoundEffect.Loop.Infinite)
        else:
            _effect.setLoopCount(max(1, loop_count))
        _effect.play()
        return True
    except Exception as e:
        _log.debug("QSoundEffect failed: %s", e)
        return False


def _on_media_status(status) -> None:
    global _loops_remaining
    if _player is None:
        return
    try:
        from PySide6.QtMultimedia import QMediaPlayer

        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if _loops_remaining < 0:
            _player.setPosition(0)
            _player.play()
            return
        if _loops_remaining > 0:
            _loops_remaining -= 1
            _player.setPosition(0)
            _player.play()
    except Exception:
        pass


def _play_media_player(path: Path, *, loop_count: int) -> bool:
    global _player, _audio_out, _media_loop_connected
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    except Exception:
        return False
    try:
        holder = _ensure_holder()
        if _player is None:
            _player = QMediaPlayer(holder)
            _audio_out = QAudioOutput(holder)
            _player.setAudioOutput(_audio_out)
        assert _audio_out is not None
        if not _media_loop_connected:
            _player.mediaStatusChanged.connect(_on_media_status)
            _media_loop_connected = True
        _player.stop()
        _audio_out.setVolume(0.9)
        # Prefer native loops when finite / infinite; EndOfMedia handler covers fallback.
        try:
            if loop_count == 0:
                _player.setLoops(QMediaPlayer.Loops.Infinite)
            else:
                _player.setLoops(max(1, loop_count))
            # Native loops handles count → disable EndOfMedia fallback.
            global _loops_remaining
            _loops_remaining = 0
        except Exception:
            pass
        _player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        _player.play()
        return True
    except Exception as e:
        _log.debug("QMediaPlayer failed: %s", e)
        return False
