"""
Central Application State (AppState) — single source of truth for runtime data.

- UI widgets render from AppState only; they do not own data.
- Background workers update AppState only; they never touch UI.
- All updates are diffed; signals carry diffs, not full datasets.
- Batched/debounced updates coalesce rapid changes into a single emission.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from monostudio.core.models import Asset, Shot

_dcc_debug_log = logging.getLogger("monostudio.dcc_debug")

if TYPE_CHECKING:
    pass


def _asset_id(asset: Asset) -> str:
    """Stable ID for an asset (path string)."""
    return str(asset.path)


def _shot_id(shot: Shot) -> str:
    """Stable ID for a shot (path string)."""
    return str(shot.path)


def diff_asset_dicts(
    prev: dict[str, Asset],
    new: dict[str, Asset],
) -> tuple[list[str], list[str], list[str]]:
    """
    Compare previous and new asset dicts keyed by stable ID.
    Returns (added_ids, removed_ids, updated_ids).
    Updated = same ID present in both but value changed (shallow equality).
    """
    prev_ids = set(prev.keys())
    new_ids = set(new.keys())
    added = list(new_ids - prev_ids)
    removed = list(prev_ids - new_ids)
    updated: list[str] = []
    for sid in prev_ids & new_ids:
        if prev[sid] != new[sid]:
            updated.append(sid)
    return (added, removed, updated)


def diff_shot_dicts(
    prev: dict[str, Shot],
    new: dict[str, Shot],
) -> tuple[list[str], list[str], list[str]]:
    """
    Compare previous and new shot dicts keyed by stable ID.
    Returns (added_ids, removed_ids, updated_ids).
    """
    prev_ids = set(prev.keys())
    new_ids = set(new.keys())
    added = list(new_ids - prev_ids)
    removed = list(prev_ids - new_ids)
    updated = [sid for sid in (prev_ids & new_ids) if prev[sid] != new[sid]]
    return (added, removed, updated)


def _list_to_asset_dict(items: list[Asset]) -> dict[str, Asset]:
    out: dict[str, Asset] = {}
    for a in items:
        out[_asset_id(a)] = a
    return out


def _list_to_shot_dict(items: list[Shot]) -> dict[str, Shot]:
    out: dict[str, Shot] = {}
    for s in items:
        out[_shot_id(s)] = s
    return out


# Debounce interval (ms) for batching rapid updates
_BATCH_MS = 80


class AppState(QObject):
    """
    Single source of truth for runtime data.
    Emits signals only when state changes meaningfully; signals carry diffs.
    """

    assetsChanged = Signal(object, object, object)  # added: list[str], removed: list[str], updated: list[str]
    shotsChanged = Signal(object, object, object)  # added, removed, updated
    selectionChanged = Signal(object)  # selection_id: str | None
    filtersChanged = Signal()
    thumbnailsChanged = Signal(object)  # asset_ids: list[str]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._assets: dict[str, Asset] = {}
        self._shots: dict[str, Shot] = {}
        self._prev_assets: dict[str, Asset] = {}
        self._prev_shots: dict[str, Shot] = {}

        self._selection_id: str | None = None
        self._filter_department: str | None = None
        self._filter_type: str | None = None

        self._batch_timer: QTimer | None = None
        self._pending_assets: dict[str, Asset] | None = None
        self._pending_shots: dict[str, Shot] | None = None

    # ---------- Read API ----------

    def assets(self) -> dict[str, Asset]:
        return dict(self._assets)

    def shots(self) -> dict[str, Shot]:
        return dict(self._shots)

    def get_asset(self, item_id: str) -> Asset | None:
        return self._assets.get(item_id)

    def get_shot(self, item_id: str) -> Shot | None:
        return self._shots.get(item_id)

    def get_assets_in_order(self) -> list[Asset]:
        """Stable display order: by (asset_type, name)."""
        return sorted(self._assets.values(), key=lambda a: (a.asset_type, a.name))

    def get_shots_in_order(self) -> list[Shot]:
        """Stable display order: by name."""
        return sorted(self._shots.values(), key=lambda s: s.name)

    def selection_id(self) -> str | None:
        return self._selection_id

    def filter_department(self) -> str | None:
        return self._filter_department

    def filter_type(self) -> str | None:
        return self._filter_type

    # ---------- Update API (call from workers or main thread) ----------

    def update_assets(self, new_assets: list[Asset] | dict[str, Asset]) -> None:
        """Accept raw data only; compute diff and emit only if non-empty (possibly debounced)."""
        if isinstance(new_assets, list):
            new_dict = _list_to_asset_dict(new_assets)
        else:
            new_dict = dict(new_assets)
        self._pending_assets = new_dict
        self._schedule_batch()

    def update_shots(self, new_shots: list[Shot] | dict[str, Shot]) -> None:
        if isinstance(new_shots, list):
            new_dict = _list_to_shot_dict(new_shots)
        else:
            new_dict = dict(new_shots)
        self._pending_shots = new_dict
        self._schedule_batch()

    def set_selection(self, selection_id: str | None) -> None:
        sid = (selection_id or "").strip() or None
        if sid == self._selection_id:
            return
        self._selection_id = sid
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_app_state_emit
            if enabled():
                record_app_state_emit("selectionChanged")
        except Exception:
            pass
        self.selectionChanged.emit(sid)

    def set_filters(self, department: str | None, type_id: str | None) -> None:
        dep = (department or "").strip() or None
        typ = (type_id or "").strip() or None
        if dep == self._filter_department and typ == self._filter_type:
            return
        self._filter_department = dep
        self._filter_type = typ
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_app_state_emit
            if enabled():
                record_app_state_emit("filtersChanged")
        except Exception:
            pass
        self.filtersChanged.emit()

    def invalidate_thumbnails(self, item_ids: list[str]) -> None:
        """Emit so UI can invalidate cache and refresh for these items (e.g. after paste)."""
        if not item_ids:
            return
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_app_state_emit
            if enabled():
                record_app_state_emit("thumbnailsChanged")
        except Exception:
            pass
        self.thumbnailsChanged.emit(list(item_ids))

    def notify_thumbnail_ready(self, asset_ids: list[str]) -> None:
        """Emit so UI repaints for these items (thumbnail loaded in cache). Do not invalidate cache."""
        if not asset_ids:
            return
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_app_state_emit
            if enabled():
                record_app_state_emit("thumbnailReady")
        except Exception:
            pass
        self.thumbnailsChanged.emit(list(asset_ids))

    def _schedule_batch(self) -> None:
        if self._batch_timer is not None:
            return
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._commit_batch)
        self._batch_timer.start(_BATCH_MS)

    def _commit_batch(self) -> None:
        if self._batch_timer is not None:
            self._batch_timer.stop()
            self._batch_timer = None

        if self._pending_assets is not None:
            new_assets = self._pending_assets
            self._pending_assets = None
            prev = self._prev_assets
            self._assets = new_assets
            self._prev_assets = dict(new_assets)
            added, removed, updated = diff_asset_dicts(prev, new_assets)
            _dcc_debug_log.debug("AppState _commit_batch assets diff added=%s removed=%s updated=%s", added, removed, updated)
            if added or removed or updated:
                try:
                    from monostudio.ui_qt.stress_profiler import enabled, record_app_state_emit
                    if enabled():
                        record_app_state_emit("assetsChanged")
                except Exception:
                    pass
                self.assetsChanged.emit(added, removed, updated)

        if self._pending_shots is not None:
            new_shots = self._pending_shots
            self._pending_shots = None
            prev = self._prev_shots
            self._shots = new_shots
            self._prev_shots = dict(new_shots)
            added, removed, updated = diff_shot_dicts(prev, new_shots)
            if added or removed or updated:
                try:
                    from monostudio.ui_qt.stress_profiler import enabled, record_app_state_emit
                    if enabled():
                        record_app_state_emit("shotsChanged")
                except Exception:
                    pass
                self.shotsChanged.emit(added, removed, updated)

    def commit_immediate(self) -> None:
        """Flush any pending batched updates immediately (e.g. before closing or sync point)."""
        if self._pending_assets is not None or self._pending_shots is not None:
            self._commit_batch()

    def clear_project_data(self) -> None:
        """Clear assets/shots and previous snapshots; emit diffs so UI clears."""
        prev_a = self._prev_assets
        prev_s = self._prev_shots
        self._assets = {}
        self._shots = {}
        self._prev_assets = {}
        self._prev_shots = {}
        self._pending_assets = None
        self._pending_shots = None
        if self._batch_timer is not None:
            self._batch_timer.stop()
            self._batch_timer = None
        removed_a = list(prev_a.keys())
        removed_s = list(prev_s.keys())
        if removed_a:
            self.assetsChanged.emit([], removed_a, [])
        if removed_s:
            self.shotsChanged.emit([], removed_s, [])
        if self._selection_id is not None:
            self._selection_id = None
            self.selectionChanged.emit(None)
