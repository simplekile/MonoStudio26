"""
Stress testing and profiling framework for MONOS (internal diagnostics only).

- Enabled only when MONOS_STRESS=1 or MONOS_PROFILE=1.
- Lightweight hooks in AppState, WorkerManager, AssetGrid, Inspector, ThumbnailManager.
- Metrics: UI blocking/paint time, worker queue length, AppState emissions, cache hit ratio.
- Aggregated summaries for bottleneck analysis. No production impact when disabled.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("monos.stress_profiler")

# Enable only via explicit env (never by default)
_STRESS_ENV = "MONOS_STRESS"
_PROFILE_ENV = "MONOS_PROFILE"


def _is_enabled() -> bool:
    v = os.environ.get(_STRESS_ENV, "") or os.environ.get(_PROFILE_ENV, "")
    return str(v).strip() in ("1", "true", "yes")


class _Metrics:
    """Thread-safe counters and samples for profiling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._app_state_emissions: dict[str, int] = {}
        self._worker_submits = 0
        self._worker_finishes = 0
        self._pending_samples: list[int] = []  # queue length when submit/finish
        self._paint_durations_ms: list[float] = []
        self._inspector_updates: dict[str, int] = {}
        self._thumbnail_hits = 0
        self._thumbnail_misses = 0
        self._ui_blocking_ms: list[float] = []

    def record_emit(self, signal_name: str) -> None:
        with self._lock:
            self._app_state_emissions[signal_name] = self._app_state_emissions.get(signal_name, 0) + 1

    def record_worker_submit(self, category: str, pending_count: int) -> None:
        with self._lock:
            self._worker_submits += 1
            self._pending_samples.append(pending_count)

    def record_worker_finish(self, category: str, pending_count: int) -> None:
        with self._lock:
            self._worker_finishes += 1
            self._pending_samples.append(pending_count)

    def record_paint_ms(self, ms: float) -> None:
        with self._lock:
            self._paint_durations_ms.append(ms)
            # Keep last N to avoid unbounded growth
            if len(self._paint_durations_ms) > 10_000:
                self._paint_durations_ms = self._paint_durations_ms[-5000:]

    def record_inspector_update(self, kind: str) -> None:
        with self._lock:
            self._inspector_updates[kind] = self._inspector_updates.get(kind, 0) + 1

    def record_thumbnail_hit(self) -> None:
        with self._lock:
            self._thumbnail_hits += 1

    def record_thumbnail_miss(self) -> None:
        with self._lock:
            self._thumbnail_misses += 1

    def record_ui_blocking_ms(self, ms: float) -> None:
        with self._lock:
            self._ui_blocking_ms.append(ms)
            if len(self._ui_blocking_ms) > 5_000:
                self._ui_blocking_ms = self._ui_blocking_ms[-2500:]

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            paint = self._paint_durations_ms
            pending = self._pending_samples
            hits = self._thumbnail_hits
            misses = self._thumbnail_misses
            total_thumb = hits + misses
        return {
            "app_state_emissions": dict(self._app_state_emissions),
            "worker_submits": self._worker_submits,
            "worker_finishes": self._worker_finishes,
            "pending_queue_avg": sum(pending) / len(pending) if pending else 0,
            "pending_queue_max": max(pending) if pending else 0,
            "paint_count": len(paint),
            "paint_avg_ms": sum(paint) / len(paint) if paint else 0,
            "paint_max_ms": max(paint) if paint else 0,
            "inspector_updates": dict(self._inspector_updates),
            "thumbnail_hits": hits,
            "thumbnail_misses": misses,
            "thumbnail_hit_ratio": hits / total_thumb if total_thumb else 0,
            "ui_blocking_count": len(self._ui_blocking_ms),
            "ui_blocking_avg_ms": sum(self._ui_blocking_ms) / len(self._ui_blocking_ms) if self._ui_blocking_ms else 0,
            "ui_blocking_max_ms": max(self._ui_blocking_ms) if self._ui_blocking_ms else 0,
        }

    def reset(self) -> None:
        with self._lock:
            self._app_state_emissions.clear()
            self._worker_submits = 0
            self._worker_finishes = 0
            self._pending_samples.clear()
            self._paint_durations_ms.clear()
            self._inspector_updates.clear()
            self._thumbnail_hits = 0
            self._thumbnail_misses = 0
            self._ui_blocking_ms.clear()


_metrics = _Metrics()
_enabled: bool | None = None


def enabled() -> bool:
    """True only when MONOS_STRESS=1 or MONOS_PROFILE=1. Cached for the process."""
    global _enabled
    if _enabled is None:
        _enabled = _is_enabled()
    return _enabled


def record_app_state_emit(signal_name: str) -> None:
    if not enabled():
        return
    _metrics.record_emit(signal_name)


def record_worker_submit(category: str, pending_count: int) -> None:
    if not enabled():
        return
    _metrics.record_worker_submit(category, pending_count)


def record_worker_finish(category: str, pending_count: int) -> None:
    if not enabled():
        return
    _metrics.record_worker_finish(category, pending_count)


def record_paint_ms(ms: float) -> None:
    if not enabled():
        return
    _metrics.record_paint_ms(ms)


def record_inspector_update(kind: str) -> None:
    if not enabled():
        return
    _metrics.record_inspector_update(kind)


def record_thumbnail_hit() -> None:
    if not enabled():
        return
    _metrics.record_thumbnail_hit()


def record_thumbnail_miss() -> None:
    if not enabled():
        return
    _metrics.record_thumbnail_miss()


def record_ui_blocking_ms(ms: float) -> None:
    if not enabled():
        return
    _metrics.record_ui_blocking_ms(ms)


def get_summary() -> dict[str, Any]:
    return _metrics.get_summary()


def reset_metrics() -> None:
    _metrics.reset()


def log_summary(prefix: str = "MONOS Profiler") -> None:
    """Log aggregated metrics to the monos.stress_profiler logger."""
    if not enabled():
        return
    s = get_summary()
    logger.info(
        "%s summary: app_state=%s worker_submits=%s worker_finishes=%s pending_max=%s paint_avg_ms=%.2f paint_max_ms=%.2f thumb_ratio=%.2f ui_block_max_ms=%.2f",
        prefix,
        s["app_state_emissions"],
        s["worker_submits"],
        s["worker_finishes"],
        s["pending_queue_max"],
        s["paint_avg_ms"],
        s["paint_max_ms"],
        s["thumbnail_hit_ratio"],
        s["ui_blocking_max_ms"],
    )
