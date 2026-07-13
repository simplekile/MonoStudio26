"""Run a WorkerManager task while the Qt event loop keeps processing (loading animations, etc.)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.worker_manager import WorkerTask

if TYPE_CHECKING:
    from monostudio.ui_qt.worker_manager import WorkerManager

_PUMP_MS = 16


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


def run_worker_blocking_ui(
    worker_manager: WorkerManager,
    category: str,
    fn: Callable[[], object],
    *,
    replace_existing: bool = True,
    on_pump: Callable[[], None] | None = None,
) -> tuple[object | None, str | None]:
    """Execute *fn* on the worker pool; block caller until done but keep UI events flowing."""
    box: list[object | None] = [None, None]
    loop = QEventLoop()
    pump = QTimer()
    pump.setInterval(_PUMP_MS)

    def _pump() -> None:
        if on_pump is not None:
            on_pump()
        QApplication.processEvents()

    pump.timeout.connect(_pump)

    def on_finished(cat: str, result: object, error: object) -> None:
        if cat != category:
            return
        box[0] = result
        box[1] = error if isinstance(error, str) or error is None else str(error)
        loop.quit()

    worker_manager.taskFinished.connect(on_finished)
    pump.start()
    try:
        task = WorkerTask(category, fn, manager=worker_manager)
        worker_manager.submit_task(task, category=category, replace_existing=replace_existing)
        loop.exec()
    finally:
        pump.stop()
        worker_manager.taskFinished.disconnect(on_finished)
    return box[0], box[1]  # type: ignore[return-value]
