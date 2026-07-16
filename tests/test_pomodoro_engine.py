"""Unit tests for Pomodoro engine state machine."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from monostudio.plugins.pomodoro.engine import Phase, PomodoroEngine
from monostudio.plugins.pomodoro.store import PomodoroPrefs


def _app() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_start_focus_and_tick_to_idle_without_auto_break() -> None:
    _app()
    eng = PomodoroEngine(PomodoroPrefs(focus_minutes=1, auto_start_break=False))
    eng.start_focus()
    snap = eng.snapshot()
    assert snap.phase == Phase.FOCUS
    assert snap.remaining_sec == 60
    for _ in range(60):
        eng.tick()
    snap = eng.snapshot()
    assert snap.phase == Phase.IDLE
    assert snap.focuses_until_long == 1


def test_auto_start_break_after_focus() -> None:
    _app()
    eng = PomodoroEngine(
        PomodoroPrefs(focus_minutes=1, short_break_minutes=1, auto_start_break=True, long_break_every=4)
    )
    eng.start_focus()
    for _ in range(60):
        eng.tick()
    snap = eng.snapshot()
    assert snap.phase == Phase.SHORT_BREAK
    assert snap.remaining_sec == 60


def test_skip_focus_starts_break_even_when_auto_off() -> None:
    _app()
    eng = PomodoroEngine(PomodoroPrefs(focus_minutes=25, short_break_minutes=5, auto_start_break=False))
    eng.start_focus()
    eng.skip()
    assert eng.snapshot().phase == Phase.SHORT_BREAK


def test_long_break_every_n() -> None:
    _app()
    eng = PomodoroEngine(
        PomodoroPrefs(
            focus_minutes=1,
            short_break_minutes=1,
            long_break_minutes=2,
            long_break_every=2,
            auto_start_break=True,
        )
    )
    eng.start_focus()
    for _ in range(60):
        eng.tick()
    assert eng.snapshot().phase == Phase.SHORT_BREAK
    for _ in range(60):
        eng.tick()
    assert eng.snapshot().phase == Phase.IDLE
    eng.start_focus()
    for _ in range(60):
        eng.tick()
    assert eng.snapshot().phase == Phase.LONG_BREAK
    assert eng.snapshot().remaining_sec == 120


def test_pause_resume() -> None:
    _app()
    eng = PomodoroEngine(PomodoroPrefs(focus_minutes=1))
    eng.start_focus()
    eng.tick()
    eng.pause()
    rem = eng.snapshot().remaining_sec
    eng.tick()
    assert eng.snapshot().remaining_sec == rem
    eng.resume()
    eng.tick()
    assert eng.snapshot().remaining_sec == rem - 1
