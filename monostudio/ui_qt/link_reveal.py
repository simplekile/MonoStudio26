"""Transient highlight when navigating via monostudio:// deep link."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen

from monostudio.ui_qt.style import MONOS_COLORS

# Bright → dim pulses, then a short fade-out on the last pulse.
_FLASH_COUNT = 3
_HOLD_MS = 220
_DIM_MS = 140
_DIM_ALPHA = 0.12
_FADE_INTERVAL_MS = 50
_FADE_STEPS = 8
_FLASH_END = 0.15


def _resolved_path(path: Path | str) -> Path:
    p = Path(path)
    try:
        return p.resolve()
    except OSError:
        return p


def path_reveal_key(path: Path | str) -> str:
    return f"path:{_resolved_path(path).as_posix()}"


def trash_reveal_key(trash_id: str) -> str:
    return f"trash:{(trash_id or '').strip()}"


def paint_link_reveal_row_overlay(painter: QPainter, rect: QRect, alpha: float) -> None:
    if alpha <= 0.01:
        return
    p = painter
    p.save()
    try:
        fill = QColor(MONOS_COLORS["blue_400"])
        fill.setAlpha(int(28 * alpha))
        p.fillRect(rect, fill)
        bar = QColor(MONOS_COLORS["blue_400"])
        bar.setAlpha(int(255 * alpha))
        p.fillRect(rect.left(), rect.top(), 4, rect.height(), bar)
    finally:
        p.restore()


def paint_link_reveal_card_border(
    painter: QPainter,
    outer: QRect,
    alpha: float,
    *,
    radius: int = 12,
) -> None:
    if alpha <= 0.01:
        return
    p = painter
    p.save()
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        border = QColor(MONOS_COLORS["blue_400"])
        border.setAlpha(int(255 * alpha))
        p.setPen(QPen(border, 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        stroke_inset = 2
        border_rect = outer.adjusted(stroke_inset, stroke_inset, -stroke_inset, -stroke_inset)
        p.drawRoundedRect(QRectF(border_rect), radius, radius)
    finally:
        p.restore()


class LinkRevealController(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._path_targets: dict[str, Path] = {}
        self._trash_targets: dict[str, str] = {}
        # Hot-path set for Path == without resolve() on every painted cell.
        self._path_match: set[Path] = set()
        self._global_alpha = 0.0
        self._fade_step = 0
        self._pulses_left = 0
        self._phase_timer = QTimer(self)
        self._phase_timer.setSingleShot(True)
        self._phase_timer.timeout.connect(self._on_phase_timeout)
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(_FADE_INTERVAL_MS)
        self._fade_timer.timeout.connect(self._on_fade_tick)

    def is_active(self) -> bool:
        return self._global_alpha > 0.01 or bool(self._path_targets or self._trash_targets)

    def current_alpha(self) -> float:
        return self._global_alpha

    def any_active_path(self) -> Path | None:
        if not self._path_targets:
            return None
        return next(iter(self._path_targets.values()))

    def any_active_trash_id(self) -> str | None:
        if not self._trash_targets:
            return None
        return next(iter(self._trash_targets.values()))

    def reveal_path(self, path: Path | str) -> None:
        resolved = _resolved_path(path)
        key = f"path:{resolved.as_posix()}"
        self._path_targets[key] = resolved
        self._path_match.add(resolved)
        # Also accept the unresolved form when callers pass absolute paths as-is.
        raw = Path(path)
        if raw != resolved:
            self._path_match.add(raw)
        self._begin_flash()

    def reveal_trash(self, trash_id: str) -> None:
        tid = (trash_id or "").strip()
        if not tid:
            return
        key = trash_reveal_key(tid)
        self._trash_targets[key] = tid
        self._begin_flash()

    def alpha_for_path(self, path: Path | str) -> float:
        """Paint hot path: Path == first; resolve only if needed."""
        if self._global_alpha <= 0.01 or not self._path_match:
            return 0.0
        p = path if isinstance(path, Path) else Path(path)
        # Prefer == over set hash — Windows case-fold equality is not always hash-safe.
        if any(p == t for t in self._path_match):
            return self._global_alpha
        # Slow path (relative vs absolute / symlink)
        try:
            resolved = p.resolve()
        except OSError:
            return 0.0
        if any(resolved == t for t in self._path_match):
            return self._global_alpha
        if f"path:{resolved.as_posix()}" in self._path_targets:
            return self._global_alpha
        return 0.0

    def alpha_for_trash(self, trash_id: str) -> float:
        if self._global_alpha <= 0.01:
            return 0.0
        if trash_reveal_key(trash_id) not in self._trash_targets:
            return 0.0
        return self._global_alpha

    def matches_path(self, path: Path | str) -> bool:
        return self.alpha_for_path(path) > 0.01

    def _stop_timers(self) -> None:
        self._phase_timer.stop()
        self._fade_timer.stop()

    def _begin_flash(self) -> None:
        self._stop_timers()
        self._fade_step = 0
        self._pulses_left = max(1, int(_FLASH_COUNT))
        self._show_bright()

    def _show_bright(self) -> None:
        self._global_alpha = 1.0
        self.changed.emit()
        self._phase_timer.start(_HOLD_MS)

    def _show_dim(self) -> None:
        self._global_alpha = _DIM_ALPHA
        self.changed.emit()
        self._phase_timer.start(_DIM_MS)

    def _on_phase_timeout(self) -> None:
        if not self.is_active():
            self._clear()
            return
        # Currently bright → either fade out (last pulse) or dim then pulse again.
        if self._global_alpha >= 0.99:
            self._pulses_left -= 1
            if self._pulses_left <= 0:
                self._begin_fade()
                return
            self._show_dim()
            return
        # Currently dim → next bright pulse.
        self._show_bright()

    def _begin_fade(self) -> None:
        if not self.is_active():
            return
        self._fade_step = 0
        self._on_fade_tick()
        if self._global_alpha > 0.01:
            self._fade_timer.start()

    def _fade_alpha(self, step: int) -> float:
        t = min(1.0, step / float(_FADE_STEPS))
        if t < _FLASH_END:
            return 1.0
        fade = (t - _FLASH_END) / (1.0 - _FLASH_END)
        return max(0.0, 1.0 - fade**0.75)

    def _on_fade_tick(self) -> None:
        if not self.is_active():
            self._fade_timer.stop()
            self._clear()
            return
        self._fade_step += 1
        self._global_alpha = self._fade_alpha(self._fade_step)
        self.changed.emit()
        if self._fade_step >= _FADE_STEPS or self._global_alpha <= 0.01:
            self._fade_timer.stop()
            self._clear()
            self.changed.emit()

    def _clear(self) -> None:
        self._stop_timers()
        self._path_targets.clear()
        self._trash_targets.clear()
        self._path_match.clear()
        self._global_alpha = 0.0
        self._fade_step = 0
        self._pulses_left = 0


_instance: LinkRevealController | None = None


def link_reveal() -> LinkRevealController:
    global _instance
    if _instance is None:
        _instance = LinkRevealController()
    return _instance
