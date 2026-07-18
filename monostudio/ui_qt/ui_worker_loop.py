"""Submit WorkerManager tasks without blocking the Qt GUI thread.

Prefer :func:`run_worker_async`: heavy work on the pool, results applied on the main
thread via *on_result*. That keeps the event loop free so loading animations can tick.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from monostudio.ui_qt.worker_manager import WorkerTask

if TYPE_CHECKING:
    from monostudio.ui_qt.worker_manager import WorkerManager


def run_worker_async(
    worker_manager: WorkerManager,
    category: str,
    fn: Callable[[], object],
    *,
    on_result: Callable[[object | None, str | None], None],
    replace_existing: bool = True,
) -> None:
    """Submit *fn* on the worker pool; call *on_result(result, error)* on the main thread when done."""
    def on_finished(cat: str, result: object, error: object) -> None:
        if cat != category:
            return
        try:
            worker_manager.taskFinished.disconnect(on_finished)
        except (TypeError, RuntimeError):
            pass
        err = error if isinstance(error, str) or error is None else str(error)
        on_result(result, err)

    worker_manager.taskFinished.connect(on_finished)
    task = WorkerTask(category, fn, manager=worker_manager)
    worker_manager.submit_task(task, category=category, replace_existing=replace_existing)
