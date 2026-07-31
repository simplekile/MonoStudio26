"""Fusion comp preflight UI when opening from MonoStudio."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from monostudio.core.comp_render_paths import CompSaverSpec
from monostudio.core.comp_saver_io import CompSaverAuditStatus, ensure_render_dir
from monostudio.ui_qt.comp_preflight_hub import (
    CompPreflightLoadingDialog,
    apply_preflight_plan,
    run_comp_preflight_hub,
    scan_comp_preflight,
)

_log = logging.getLogger("monostudio.comp_saver_preflight")

_FUSION_COMP_PREFLIGHT_KEY = "integrations/fusion_comp_preflight"
# Lucide: preflight gate before Fusion — shield + check (verified/safe open).
FUSION_COMP_PREFLIGHT_ICON = "shield-check"
_MIN_LOADING_MS = 1000


def _active_window() -> QWidget | None:
    app = QApplication.instance()
    if app is None:
        return None
    w = app.activeWindow()
    return w if isinstance(w, QWidget) else None


def _settings_bool(settings: Any, key: str, *, default: bool = True) -> bool:
    raw = settings.value(key, default)
    if isinstance(raw, str):
        return raw.strip().lower() not in ("0", "false", "no", "off")
    return bool(raw)


def fusion_comp_preflight_enabled(settings: Any) -> bool:
    """Whether to run Saver + upstream Loader checks before opening Fusion."""
    if settings is None:
        return True
    if settings.contains(_FUSION_COMP_PREFLIGHT_KEY):
        return _settings_bool(settings, _FUSION_COMP_PREFLIGHT_KEY, default=True)
    saver = _settings_bool(settings, "integrations/fusion_saver_preflight", default=True)
    upstream = _settings_bool(settings, "integrations/fusion_upstream_render_preflight", default=True)
    return saver and upstream


def run_comp_open_preflight(
    *,
    comp_path: Path,
    spec: CompSaverSpec,
    entity_name: str | None,
    project_root: Path,
    settings: Any = None,
    parent: QWidget | None = None,
    workspace_root: Path | None = None,
) -> Path | None:
    """
    Run comp checks before Fusion opens.

    Returns the comp path to open in Fusion, or None if the user cancelled.
    """
    if not fusion_comp_preflight_enabled(settings):
        return comp_path
    if not comp_path.is_file():
        return comp_path

    parent = parent or _active_window()
    app = QApplication.instance()

    loading = CompPreflightLoadingDialog(parent=parent)
    loading.show()
    if app is not None:
        app.processEvents()

    t0 = time.perf_counter()
    scan = scan_comp_preflight(
        comp_path=comp_path,
        spec=spec,
        entity_name=entity_name or None,
    )
    if scan.saver_audit.status == CompSaverAuditStatus.UNREADABLE:
        loading.close()
        _log.warning(
            "comp saver preflight unreadable: %s — %s",
            comp_path,
            scan.saver_audit.message,
        )
        return comp_path

    hub_result: list[str] = []
    hub_plan: list = []

    def _show_hub() -> None:
        loading.close()
        result, plan = run_comp_preflight_hub(parent=parent, scan=scan)
        hub_result.append(result)
        hub_plan.append(plan)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    delay_ms = max(0, _MIN_LOADING_MS - elapsed_ms)

    if delay_ms > 0:
        loop = QEventLoop()
        QTimer.singleShot(delay_ms, lambda: (_show_hub(), loop.quit()))
        loop.exec()
    else:
        _show_hub()

    if not hub_result:
        return comp_path

    result = hub_result[0]
    if result == "cancel":
        return None
    if result == "skip":
        if scan.saver_audit.status == CompSaverAuditStatus.OK:
            ensure_render_dir(spec)
        return comp_path

    plan = hub_plan[0] if hub_plan else None
    if plan is None:
        return comp_path

    apply_result = apply_preflight_plan(
        scan,
        plan,
        project_root=project_root,
        workspace_root=workspace_root,
    )
    if not apply_result.ok:
        warn = QMessageBox(parent)
        warn.setIcon(QMessageBox.Icon.Warning)
        warn.setWindowTitle("Fusion comp check")
        warn.setText("Some comp updates could not be applied. Opening Fusion anyway.")
        warn.exec()
    elif apply_result.discord_notify_skipped:
        warn = QMessageBox(parent)
        warn.setIcon(QMessageBox.Icon.Warning)
        warn.setWindowTitle("Fusion comp check")
        warn.setText(
            "End Render Script was added, but no Discord webhook is configured for "
            "“Fusion render finished”.\n\n"
            "Settings → Integrations → enable “Fusion render finished” on a channel, "
            "then save and re-apply the comp check (or open the comp again)."
        )
        warn.exec()
    return apply_result.target_path
