"""
Centralized WorkerManager — schedules and executes background tasks using a shared QThreadPool.

- UI and controllers submit tasks via WorkerManager only.
- Tasks are stateless, disposable, and never touch UI.
- Results are forwarded via taskFinished; AppState (or a coordinator) updates state.

Task categories (examples): "filesystem_scan", "thumbnail_load", "metadata_read".
Use category for replace_existing and debounce; WorkerManager does not interpret payloads.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, QTimer, QRunnable, Signal, QEvent, QCoreApplication

logger = logging.getLogger(__name__)

_TASK_FINISHED_TYPE = QEvent.Type(QEvent.registerEventType())


class _TaskFinishedEvent(QEvent):
    """Carries task result from pool thread to WorkerManager (main thread) via postEvent."""

    def __init__(self, category: str, result: object, error: str | None) -> None:
        super().__init__(_TASK_FINISHED_TYPE)
        self.category = category
        self.result = result
        self.error = error

class WorkerTask(QRunnable):
    """
    Base background task. Stateless, disposable, short-lived.
    Must NOT touch UI widgets or emit UI signals.
    """

    def __init__(
        self,
        category: str,
        run_fn: Callable[[], object],
        *,
        manager: "WorkerManager",
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._category = category
        self._run_fn = run_fn
        self._manager = manager
        self._cancelled = False

    @property
    def category(self) -> str:
        return self._category

    def cancel(self) -> None:
        """Mark as cancelled; run() will not invoke the callback if cancelled before execution."""
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        result: object = None
        error: str | None = None
        try:
            result = self._run_fn()
        except Exception as e:
            error = str(e)
            logger.exception("WorkerTask %s failed", self._category)
        if self._cancelled:
            return
        schedule_cat = getattr(self, "_schedule_category", self._category)
        app = QCoreApplication.instance()
        if app is not None:
            app.postEvent(self._manager, _TaskFinishedEvent(schedule_cat, result, error))


class WorkerManager(QObject):
    """
    Centralized task execution using a shared QThreadPool.
    Long-lived (app lifetime). Forwards results via taskFinished; does not update UI or state.
    """

    taskFinished = Signal(str, object, object)  # category, result, error (error is str | None)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        n = max(1, (os.cpu_count() or 2) - 1)
        self._pool.setMaxThreadCount(n)
        self._pending: dict[str, WorkerTask] = {}
        self._debounce_timers: dict[str, QTimer] = {}
        self._debounce_pending: dict[str, WorkerTask] = {}

    def customEvent(self, event: QEvent) -> None:
        if event.type() == _TASK_FINISHED_TYPE and isinstance(event, _TaskFinishedEvent):
            self._pending.pop(event.category, None)
            try:
                from monostudio.core.crash_recovery import set_crash_context
                next_cat = next(iter(self._pending), None) if self._pending else None
                set_crash_context(current_task_category=next_cat or "")
            except Exception:
                pass
            try:
                from monostudio.ui_qt.stress_profiler import enabled, record_worker_finish
                if enabled():
                    record_worker_finish(event.category, len(self._pending))
            except Exception:
                pass
            self.taskFinished.emit(event.category, event.result, event.error)
            return
        super().customEvent(event)

    def thread_pool(self) -> QThreadPool:
        return self._pool

    def submit_task(
        self,
        task: WorkerTask,
        *,
        category: str | None = None,
        replace_existing: bool = True,
        debounce_ms: int | None = None,
    ) -> None:
        """
        Schedule a task. Uses category (or task.category) for replace and debounce.
        - category: optional override for scheduling key; else task.category.
        - replace_existing: cancel previous pending task in the same category.
        - debounce_ms: delay execution and coalesce repeated submissions in this category.
        """
        cat = (category or "").strip() or task.category
        setattr(task, "_schedule_category", cat)
        if replace_existing and cat in self._pending:
            old = self._pending.pop(cat, None)
            if old is not None:
                old.cancel()
        queue_len = len(self._pending) + len(self._debounce_pending)
        try:
            from monostudio.core.crash_recovery import set_crash_context
            set_crash_context(current_task_category=cat)
        except Exception:
            pass
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_worker_submit
            if enabled():
                record_worker_submit(cat, queue_len)
        except Exception:
            pass
        if debounce_ms is not None and debounce_ms > 0:
            self._submit_debounced(task, category=cat, delay_ms=debounce_ms)
            return
        self._pending[cat] = task
        self._pool.start(task)

    def _submit_debounced(self, task: WorkerTask, *, category: str, delay_ms: int) -> None:
        if category in self._debounce_timers:
            self._debounce_timers[category].stop()
        self._debounce_pending[category] = task
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(delay_ms)

        def on_timeout() -> None:
            self._debounce_timers.pop(category, None)
            t = self._debounce_pending.pop(category, None)
            if t is not None and not getattr(t, "_cancelled", True):
                self._pending[category] = t
                self._pool.start(t)
            timer.deleteLater()

        timer.timeout.connect(on_timeout)
        self._debounce_timers[category] = timer
        timer.start()

    def cancel_category(self, category: str) -> None:
        """Cancel pending task in this category (before execution). Debounced task is also cancelled."""
        if category in self._debounce_timers:
            self._debounce_timers[category].stop()
            self._debounce_timers.pop(category, None)
            t = self._debounce_pending.pop(category, None)
            if t is not None:
                t.cancel()
        old = self._pending.pop(category, None)
        if old is not None:
            old.cancel()

    def has_pending(self, category: str) -> bool:
        return category in self._pending or category in self._debounce_pending
