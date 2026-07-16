"""Pomodoro state machine — pure timing logic, no UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, Signal

from monostudio.plugins.pomodoro.store import PomodoroPrefs


class Phase(str, Enum):
    IDLE = "idle"
    FOCUS = "focus"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"


@dataclass(frozen=True)
class EngineSnapshot:
    phase: Phase
    remaining_sec: int
    total_sec: int
    paused: bool
    focuses_until_long: int
    session_focus_index: int


class PomodoroEngine(QObject):
    """Tick-driven timer. Call tick() once per second while running."""

    state_changed = Signal()
    phase_completed = Signal(str, str)  # completed_phase, next_phase

    def __init__(self, prefs: PomodoroPrefs | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs or PomodoroPrefs()
        self._phase = Phase.IDLE
        self._remaining_sec = 0
        self._total_sec = 0
        self._paused = False
        self._focuses_since_long = 0

    def prefs(self) -> PomodoroPrefs:
        return self._prefs

    def set_prefs(self, prefs: PomodoroPrefs) -> None:
        self._prefs = prefs

    def snapshot(self) -> EngineSnapshot:
        every = max(1, self._prefs.long_break_every)
        if self._phase == Phase.FOCUS:
            idx = min(every, self._focuses_since_long + 1)
        else:
            idx = (self._focuses_since_long % every) + 1 if self._focuses_since_long else 1
        return EngineSnapshot(
            phase=self._phase,
            remaining_sec=max(0, int(self._remaining_sec)),
            total_sec=max(0, int(self._total_sec)),
            paused=bool(self._paused),
            focuses_until_long=self._focuses_since_long,
            session_focus_index=idx,
        )

    def is_active(self) -> bool:
        return self._phase != Phase.IDLE

    def start_focus(self) -> None:
        self._begin(Phase.FOCUS, self._prefs.focus_minutes * 60)

    def pause(self) -> None:
        if self._phase == Phase.IDLE or self._paused:
            return
        self._paused = True
        self.state_changed.emit()

    def resume(self) -> None:
        if self._phase == Phase.IDLE or not self._paused:
            return
        self._paused = False
        self.state_changed.emit()

    def toggle_pause(self) -> None:
        if self._phase == Phase.IDLE:
            return
        if self._paused:
            self.resume()
        else:
            self.pause()

    def reset(self) -> None:
        self._phase = Phase.IDLE
        self._remaining_sec = 0
        self._total_sec = 0
        self._paused = False
        self.state_changed.emit()

    def skip(self) -> None:
        """End phase early; after Focus always enter a break."""
        self.complete_or_skip(prefer_break=True)

    def tick(self) -> None:
        if self._phase == Phase.IDLE or self._paused:
            return
        if self._remaining_sec <= 0:
            self.complete_or_skip(prefer_break=False)
            return
        self._remaining_sec -= 1
        self.state_changed.emit()
        if self._remaining_sec <= 0:
            self.complete_or_skip(prefer_break=False)

    def complete_or_skip(self, *, prefer_break: bool) -> None:
        """
        End current phase.
        prefer_break=True (Skip): after Focus always enter a break.
        prefer_break=False (timer finished): honor auto_start_break.
        """
        if self._phase == Phase.IDLE:
            return
        completed = self._phase

        if completed == Phase.FOCUS:
            self._focuses_since_long += 1
            every = max(1, self._prefs.long_break_every)
            use_long = self._focuses_since_long >= every
            start_break = prefer_break or self._prefs.auto_start_break
            if start_break:
                if use_long:
                    self._focuses_since_long = 0
                    next_phase = Phase.LONG_BREAK
                    self._begin(Phase.LONG_BREAK, self._prefs.long_break_minutes * 60)
                else:
                    next_phase = Phase.SHORT_BREAK
                    self._begin(Phase.SHORT_BREAK, self._prefs.short_break_minutes * 60)
                self.phase_completed.emit(completed.value, next_phase.value)
                return
            self._go_idle()
            self.phase_completed.emit(completed.value, Phase.IDLE.value)
            return

        if completed == Phase.LONG_BREAK:
            self._focuses_since_long = 0
        self._go_idle()
        self.phase_completed.emit(completed.value, Phase.IDLE.value)

    def _begin(self, phase: Phase, seconds: int) -> None:
        self._phase = phase
        self._total_sec = max(1, int(seconds))
        self._remaining_sec = self._total_sec
        self._paused = False
        self.state_changed.emit()

    def _go_idle(self) -> None:
        self._phase = Phase.IDLE
        self._remaining_sec = 0
        self._total_sec = 0
        self._paused = False
        self.state_changed.emit()
