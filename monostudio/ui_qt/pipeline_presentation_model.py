"""QAbstractListModel exposing PipelineRowSnapshot roles for QML."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from monostudio.ui_qt.pipeline_snapshot import PipelineRowSnapshot
from monostudio.ui_qt.pipeline_snapshot_store import PipelineSnapshotStore
from monostudio.ui_qt.view_items import ViewItem

_ROLE_PATH = Qt.UserRole + 1
_ROLE_SNAPSHOT = Qt.UserRole + 2
_ROLE_THUMB_SOURCE = Qt.UserRole + 3
_ROLE_THUMB_OPACITY = Qt.UserRole + 4
_ROLE_SELECTED = Qt.UserRole + 5
_ROLE_STATUS_LABEL = Qt.UserRole + 6
_ROLE_STATUS_COLOR = Qt.UserRole + 7
_ROLE_ALERT_LABEL = Qt.UserRole + 8
_ROLE_DCC_NAMES = Qt.UserRole + 9


class PipelinePresentationModel(QAbstractListModel):
    """Shared model for QML grid + list (Sprint 1 scaffold)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[ViewItem] = []
        self._store = PipelineSnapshotStore()
        self._thumb_opacity: dict[str, float] = {}
        self._selected_paths: set[str] = set()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._items)

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return {
            _ROLE_PATH: b"path",
            _ROLE_SNAPSHOT: b"snapshot",
            _ROLE_THUMB_SOURCE: b"thumbSource",
            _ROLE_THUMB_OPACITY: b"thumbOpacity",
            _ROLE_SELECTED: b"selected",
            _ROLE_STATUS_LABEL: b"statusLabel",
            _ROLE_STATUS_COLOR: b"statusColor",
            _ROLE_ALERT_LABEL: b"alertLabel",
            _ROLE_DCC_NAMES: b"dccNames",
            Qt.DisplayRole: b"displayName",
        }

    @property
    def snapshot_store(self) -> PipelineSnapshotStore:
        return self._store

    def set_items(self, items: list[ViewItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._store.invalidate_all()
        self.endResetModel()

    def item_at(self, row: int) -> ViewItem | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def set_selected_paths(self, paths: set[str]) -> None:
        if paths == self._selected_paths:
            return
        self._selected_paths = set(paths)
        if self._items:
            tl = self.index(0)
            br = self.index(len(self._items) - 1)
            self.dataChanged.emit(tl, br, [_ROLE_SELECTED])

    def set_thumb_opacity(self, path: str, opacity: float) -> None:
        self._thumb_opacity[path] = max(0.0, min(1.0, opacity))
        for row, item in enumerate(self._items):
            if str(item.path) == path:
                ix = self.index(row)
                self.dataChanged.emit(ix, ix, [_ROLE_THUMB_OPACITY])
                break

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._items):
            return None
        item = self._items[row]
        path = str(item.path) if item.path else ""
        snap = self._store.snapshot_for(item)
        flat = self._flat_fields_from_snapshot(snap)
        if role == Qt.DisplayRole:
            return flat["displayName"] or item.name
        if role == _ROLE_PATH:
            return path
        if role == _ROLE_SNAPSHOT:
            return snap.to_variant_map() if snap else {}
        if role == _ROLE_THUMB_SOURCE:
            return flat["thumbSource"]
        if role == _ROLE_THUMB_OPACITY:
            return self._thumb_opacity.get(path, 1.0)
        if role == _ROLE_SELECTED:
            return path in self._selected_paths
        if role == _ROLE_STATUS_LABEL:
            return flat["statusLabel"]
        if role == _ROLE_STATUS_COLOR:
            return flat["statusColor"]
        if role == _ROLE_ALERT_LABEL:
            return flat["alertLabel"]
        if role == _ROLE_DCC_NAMES:
            return flat["dccNames"]
        return None

    def snapshot_at(self, row: int) -> PipelineRowSnapshot | None:
        item = self.item_at(row)
        if item is None:
            return None
        return self._store.snapshot_for(item)

    def notify_path_changed(self, path: str) -> None:
        self._store.invalidate_path(path)
        for row, item in enumerate(self._items):
            if str(item.path) == path:
                ix = self.index(row)
                self.dataChanged.emit(
                    ix,
                    ix,
                    [
                        _ROLE_SNAPSHOT,
                        _ROLE_THUMB_SOURCE,
                        _ROLE_STATUS_LABEL,
                        _ROLE_STATUS_COLOR,
                        _ROLE_ALERT_LABEL,
                        _ROLE_DCC_NAMES,
                        Qt.DisplayRole,
                    ],
                )
                break

    @staticmethod
    def _alert_label_from_snapshot(snap: PipelineRowSnapshot | None) -> str:
        if snap is None or snap.alerts is None:
            return ""
        alerts = snap.alerts
        if alerts.kind == "notes" and alerts.notes_count > 0:
            return str(alerts.notes_count)
        if alerts.kind == "health" and alerts.level in ("warn", "error"):
            return "!"
        return ""

    def _flat_fields_from_snapshot(self, snap: PipelineRowSnapshot | None) -> dict[str, object]:
        if snap is None:
            return {
                "displayName": "",
                "statusLabel": "",
                "statusColor": "",
                "alertLabel": "",
                "dccNames": "",
                "thumbSource": "",
            }
        m = snap.to_variant_map()
        return {
            "displayName": m.get("displayName", ""),
            "statusLabel": m.get("statusLabel", ""),
            "statusColor": m.get("statusColor", ""),
            "alertLabel": self._alert_label_from_snapshot(snap),
            "dccNames": m.get("dccNames", ""),
            "thumbSource": f"image://thumb/{snap.thumb_token}" if snap.thumb_token else "",
        }
