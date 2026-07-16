"""Pomodoro plugin host — engine, window, notifications."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QSettings, QTimer, Signal
from PySide6.QtWidgets import QWidget

from monostudio.plugins.pomodoro.engine import Phase, PomodoroEngine
from monostudio.plugins.pomodoro.store import read_prefs
from monostudio.plugins.pomodoro.ui.window import PomodoroWindow, _fmt_mmss


class PomodoroPlugin(QObject):
    """Lifecycle owner for the Focus timer feature."""

    state_changed = Signal()
    phase_boundary = Signal()

    def __init__(
        self,
        *,
        settings: QSettings,
        parent: QWidget | None = None,
        notify_tray: Callable[[str, str], None] | None = None,
        notify_in_app: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._notify_tray = notify_tray
        self._notify_in_app = notify_in_app
        self._engine = PomodoroEngine(read_prefs(settings), self)
        self._window: PomodoroWindow | None = None
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._last_boundary_key: tuple[str, bool] | None = None
        self._engine.state_changed.connect(self._on_engine_state)
        self._engine.phase_completed.connect(self._on_phase_completed)

    def engine(self) -> PomodoroEngine:
        return self._engine

    def reload_prefs(self) -> None:
        prefs = read_prefs(self._settings)
        self._engine.set_prefs(prefs)
        if self._window is not None:
            self._window.reload_prefs_ui()
        self.state_changed.emit()

    def status_text(self) -> str:
        snap = self._engine.snapshot()
        if snap.phase == Phase.IDLE:
            return "Focus timer"
        label = {
            Phase.FOCUS: "Focus",
            Phase.SHORT_BREAK: "Break",
            Phase.LONG_BREAK: "Long break",
        }.get(snap.phase, "Timer")
        pause = " (paused)" if snap.paused else ""
        return f"{label} {_fmt_mmss(snap.remaining_sec)}{pause}"

    def topbar_tooltip(self) -> str:
        return self.status_text()

    def toggle_window(self, anchor: QWidget | None = None) -> None:
        win = self._ensure_window()
        if win.isVisible():
            win.hide()
            return
        if anchor is not None:
            geo = anchor.mapToGlobal(anchor.rect().bottomRight())
            win.move(geo.x() - win.width(), geo.y() + 8)
        win.show()
        win.raise_()
        win.activateWindow()

    def show_window(self, anchor: QWidget | None = None) -> None:
        win = self._ensure_window()
        if anchor is not None and not win.isVisible():
            geo = anchor.mapToGlobal(anchor.rect().bottomRight())
            win.move(geo.x() - win.width(), geo.y() + 8)
        win.show()
        win.raise_()
        win.activateWindow()

    def _ensure_window(self) -> PomodoroWindow:
        if self._window is None:
            parent = self.parent()
            host = parent if isinstance(parent, QWidget) else None
            self._window = PomodoroWindow(self._engine, self._settings, parent=host)
            self._window.prefs_changed.connect(self.state_changed.emit)
        return self._window

    def _on_tick(self) -> None:
        self._engine.tick()

    def _on_engine_state(self) -> None:
        snap = self._engine.snapshot()
        if self._engine.is_active() and not snap.paused:
            if not self._tick.isActive():
                self._tick.start()
        else:
            if self._tick.isActive():
                self._tick.stop()
        key = (snap.phase.value, snap.paused)
        if key != self._last_boundary_key:
            self._last_boundary_key = key
            self.phase_boundary.emit()
        self.state_changed.emit()

    def _on_phase_completed(self, completed: str, next_phase: str) -> None:
        prefs = self._engine.prefs()
        title, body = self._messages(completed, next_phase)
        if prefs.sound_enabled:
            from monostudio.plugins.pomodoro.sound import play_alert_sound

            play_alert_sound(prefs.custom_sound_path, loop_count=prefs.sound_loop_count)
        else:
            from monostudio.plugins.pomodoro.sound import stop_alert_sound

            stop_alert_sound()
        if prefs.in_app_notify and self._notify_in_app is not None:
            self._notify_in_app(body)
        if prefs.tray_notify and self._notify_tray is not None:
            self._notify_tray(title, body)
        # phase_boundary already emitted from _on_engine_state on phase change
        self.state_changed.emit()

    @staticmethod
    def _messages(completed: str, next_phase: str) -> tuple[str, str]:
        if completed == Phase.FOCUS.value:
            if next_phase == Phase.SHORT_BREAK.value:
                return ("Focus done", "Short break started.")
            if next_phase == Phase.LONG_BREAK.value:
                return ("Focus done", "Long break started.")
            return ("Focus done", "Take a break when you are ready.")
        if completed == Phase.SHORT_BREAK.value:
            return ("Break over", "Ready for the next focus.")
        if completed == Phase.LONG_BREAK.value:
            return ("Long break over", "Ready for the next focus.")
        return ("Focus timer", "Phase complete.")
