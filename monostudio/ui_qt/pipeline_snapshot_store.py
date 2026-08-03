"""Snapshot cache with facet-based invalidation."""

from __future__ import annotations

from enum import Flag, auto

from monostudio.ui_qt.pipeline_row_presentation_cache import PipelineRowPresentationCache
from monostudio.ui_qt.pipeline_snapshot import PipelineRowSnapshot
from monostudio.ui_qt.pipeline_snapshot_builder import SnapshotBuildContext, build_row_snapshot
from monostudio.ui_qt.view_items import ViewItem


class SnapshotFacet(Flag):
    NONE = 0
    DCC = auto()
    HEALTH = auto()
    DIM = auto()
    ALERTS = auto()
    STATUS = auto()
    THUMB = auto()
    ALL = DCC | HEALTH | DIM | ALERTS | STATUS | THUMB


_FACET_GROUPS: dict[SnapshotFacet, SnapshotFacet] = {
    SnapshotFacet.DCC: SnapshotFacet.DCC | SnapshotFacet.HEALTH | SnapshotFacet.DIM,
    SnapshotFacet.HEALTH: SnapshotFacet.HEALTH | SnapshotFacet.ALERTS,
    SnapshotFacet.DIM: SnapshotFacet.DIM,
    SnapshotFacet.ALERTS: SnapshotFacet.ALERTS,
    SnapshotFacet.STATUS: SnapshotFacet.STATUS,
    SnapshotFacet.THUMB: SnapshotFacet.THUMB,
}


class PipelineSnapshotStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, PipelineRowSnapshot] = {}
        self._dirty: dict[str, SnapshotFacet] = {}
        self._presentation_cache = PipelineRowPresentationCache()
        self._ctx: SnapshotBuildContext | None = None

    def set_context(self, ctx: SnapshotBuildContext) -> None:
        self._ctx = ctx
        self.invalidate_all()

    def get(self, path: str) -> PipelineRowSnapshot | None:
        return self._snapshots.get(path)

    def snapshot_for(self, item: ViewItem) -> PipelineRowSnapshot | None:
        if self._ctx is None:
            return None
        path = str(item.path) if item.path else ""
        if not path:
            return None
        facets = self._dirty.get(path, SnapshotFacet.NONE)
        if path not in self._snapshots or facets:
            snap = build_row_snapshot(item, self._ctx, cache=self._presentation_cache)
            self._snapshots[path] = snap
            self._dirty.pop(path, None)
        return self._snapshots[path]

    def invalidate_path(self, path: str, facet: SnapshotFacet = SnapshotFacet.ALL) -> None:
        if not path:
            return
        group = _FACET_GROUPS.get(facet, facet)
        self._dirty[path] = self._dirty.get(path, SnapshotFacet.NONE) | group
        if facet & (SnapshotFacet.DCC | SnapshotFacet.HEALTH | SnapshotFacet.ALL):
            self._presentation_cache.invalidate_path(path)

    def invalidate_all(self) -> None:
        self._snapshots.clear()
        self._dirty.clear()
        self._presentation_cache.clear()

    def paths_needing_rebuild(self) -> list[str]:
        return list(self._dirty.keys())
