"""
Stress diagnostics dialog (internal use only).

- Shown only when MONOS_STRESS=1 or MONOS_PROFILE=1.
- Run stress scenarios and view aggregated metrics summary.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.stress_profiler import enabled, get_summary, log_summary, reset_metrics
from monostudio.ui_qt.stress_runner import (
    run_asset_load_stress,
    run_fs_event_stress,
    run_interaction_stress,
    run_thumbnail_stress,
)
from monostudio.ui_qt.style import MonosDialog

if TYPE_CHECKING:
    from monostudio.ui_qt.app_state import AppState
    from monostudio.ui_qt.fs_watcher import FsEventCollector
    from monostudio.ui_qt.main_view import MainView
    from monostudio.ui_qt.thumbnails import ThumbnailManager

logger = logging.getLogger("monos.stress_diagnostics")


def _format_summary(s: dict) -> str:
    lines = [
        "=== MONOS Profiler Summary ===",
        f"AppState emissions: {s.get('app_state_emissions', {})}",
        f"Worker submits: {s.get('worker_submits', 0)}  finishes: {s.get('worker_finishes', 0)}",
        f"Pending queue avg: {s.get('pending_queue_avg', 0):.1f}  max: {s.get('pending_queue_max', 0)}",
        f"Paint count: {s.get('paint_count', 0)}  avg ms: {s.get('paint_avg_ms', 0):.2f}  max ms: {s.get('paint_max_ms', 0):.2f}",
        f"Inspector updates: {s.get('inspector_updates', {})}",
        f"Thumbnail hits: {s.get('thumbnail_hits', 0)}  misses: {s.get('thumbnail_misses', 0)}  hit_ratio: {s.get('thumbnail_hit_ratio', 0):.2%}",
        f"UI blocking count: {s.get('ui_blocking_count', 0)}  avg ms: {s.get('ui_blocking_avg_ms', 0):.2f}  max ms: {s.get('ui_blocking_max_ms', 0):.2f}",
    ]
    return "\n".join(lines)


class StressDiagnosticsDialog(MonosDialog):
    """Run stress scenarios and view metrics. Only when MONOS_STRESS=1."""

    def __init__(
        self,
        *,
        app_state: "AppState",
        main_view: "MainView | None" = None,
        thumbnail_manager: "ThumbnailManager | None" = None,
        fs_collector: "FsEventCollector | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stress diagnostics")
        self.setModal(False)
        self.setMinimumSize(520, 420)
        self._app_state = app_state
        self._main_view = main_view
        self._thumbnail_manager = thumbnail_manager
        self._fs_collector = fs_collector

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel("Internal diagnostics. Scenarios run when MONOS_STRESS=1 or MONOS_PROFILE=1.")
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        grp = QGroupBox("Scenarios")
        grp_layout = QHBoxLayout(grp)
        btn_asset = QPushButton("Asset load (1k)")
        btn_asset.setToolTip("Inject 1000 fake assets, measure, restore")
        btn_asset.clicked.connect(self._run_asset_stress)
        grp_layout.addWidget(btn_asset)
        btn_fs = QPushButton("FS events (500)")
        btn_fs.setToolTip("Simulate 500 path events into FsEventCollector")
        btn_fs.clicked.connect(self._run_fs_stress)
        grp_layout.addWidget(btn_fs)
        btn_thumb = QPushButton("Thumbnail stress")
        btn_thumb.setToolTip("Request thumbnails for current assets (up to 500)")
        btn_thumb.clicked.connect(self._run_thumbnail_stress)
        grp_layout.addWidget(btn_thumb)
        btn_interact = QPushButton("Interaction (100)")
        btn_interact.setToolTip("Rapid selection changes")
        btn_interact.clicked.connect(self._run_interaction_stress)
        grp_layout.addWidget(btn_interact)
        grp_layout.addStretch()
        root.addWidget(grp)

        summary_grp = QGroupBox("Summary")
        summary_layout = QVBoxLayout(summary_grp)
        self._summary_text = QPlainTextEdit(self)
        self._summary_text.setReadOnly(True)
        self._summary_text.setPlaceholderText("Run a scenario or click Refresh summary.")
        summary_layout.addWidget(self._summary_text)
        btn_refresh = QPushButton("Refresh summary")
        btn_refresh.clicked.connect(self._refresh_summary)
        summary_layout.addWidget(btn_refresh)
        root.addWidget(summary_grp)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._refresh_summary()

    def _refresh_summary(self) -> None:
        s = get_summary()
        self._summary_text.setPlainText(_format_summary(s))

    def _run_asset_stress(self) -> None:
        run_asset_load_stress(self._app_state, self._main_view, count=1000)
        self._refresh_summary()

    def _run_fs_stress(self) -> None:
        if self._fs_collector is None:
            self._summary_text.appendPlainText("FsEventCollector not available.")
            return
        run_fs_event_stress(self._fs_collector, n_events=500)
        self._refresh_summary()

    def _run_thumbnail_stress(self) -> None:
        if self._thumbnail_manager is None:
            self._summary_text.appendPlainText("ThumbnailManager not available.")
            return
        ids = [str(a.path) for a in self._app_state.assets().values()][:500]
        if not ids:
            self._summary_text.appendPlainText("No assets; using fake IDs for cache misses.")
            ids = [f"/stress/asset_{i}" for i in range(200)]
        run_thumbnail_stress(self._thumbnail_manager, ids)
        self._refresh_summary()

    def _run_interaction_stress(self) -> None:
        run_interaction_stress(self._app_state, self._main_view, n_selections=100)
        self._refresh_summary()
