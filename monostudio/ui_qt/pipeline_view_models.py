# Virtual Qt models for MainView: shared row list (`_all_items`); tile model = grid, list model = list rows.

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget

from monostudio.ui_qt.view_items import ViewItem, display_name_for_item

PIPELINE_VIEW_THUMB_STATE_ROLE = Qt.ItemDataRole.UserRole + 1


class PipelineTileModel(QAbstractListModel):
    """One row per visible entity; `data(UserRole)` is the ViewItem; DecorationRole is thumbnail or placeholder."""

    def __init__(self, main_view: QWidget, *, thumb_state_role: int = PIPELINE_VIEW_THUMB_STATE_ROLE) -> None:
        super().__init__(main_view)
        self._mv = main_view
        self._thumb_state_role = int(thumb_state_role)
        self._rows: list[ViewItem] = []
        self._thumb_state_by_path: dict[str, str | None] = {}
        self._icon_override_by_path: dict[str, QIcon] = {}

    def thumb_state_role(self) -> int:
        return self._thumb_state_role

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._rows)

    def view_item_at(self, row: int) -> ViewItem | None:
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def row_count(self) -> int:
        return len(self._rows)

    def bind_rows(self, rows: list[ViewItem]) -> None:
        """Full reset; `rows` is typically MainView._all_items (same list reference over time)."""
        self.beginResetModel()
        self._rows = rows
        self._thumb_state_by_path.clear()
        self._icon_override_by_path.clear()
        self.endResetModel()

    def before_remove_row(self, row: int) -> None:
        vi = self.view_item_at(row)
        if vi is None:
            return
        k = str(vi.path)
        self._thumb_state_by_path.pop(k, None)
        self._icon_override_by_path.pop(k, None)

    def notify_insert_rows(self, row: int, count: int = 1) -> None:
        if count <= 0:
            return
        self.beginInsertRows(QModelIndex(), row, row + count - 1)
        self.endInsertRows()

    def notify_remove_rows(self, row: int, count: int = 1) -> None:
        if count <= 0:
            return
        self.beginRemoveRows(QModelIndex(), row, row + count - 1)
        self.endRemoveRows()

    def replace_row_view_item(self, row: int, vi: ViewItem) -> None:
        if row < 0 or row >= len(self._rows):
            return
        old = self._rows[row]
        old_k = str(old.path)
        new_k = str(vi.path)
        if old_k != new_k:
            self._thumb_state_by_path.pop(old_k, None)
            self._icon_override_by_path.pop(old_k, None)
        self._rows[row] = vi
        ix = self.index(row, 0)
        self.dataChanged.emit(
            ix,
            ix,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.DecorationRole,
                Qt.ItemDataRole.UserRole,
                self._thumb_state_role,
            ],
        )

    def reset_thumbnail_slot_row(self, row: int) -> None:
        vi = self.view_item_at(row)
        if vi is None:
            return
        k = str(vi.path)
        self._thumb_state_by_path.pop(k, None)
        self._icon_override_by_path.pop(k, None)
        ix = self.index(row, 0)
        self.dataChanged.emit(
            ix,
            ix,
            [Qt.ItemDataRole.DecorationRole, self._thumb_state_role],
        )

    def set_row_thumbnail(self, row: int, icon: QIcon, state: str | None) -> None:
        vi = self.view_item_at(row)
        if vi is None:
            return
        k = str(vi.path)
        self._icon_override_by_path[k] = icon
        self._thumb_state_by_path[k] = state
        ix = self.index(row, 0)
        self.dataChanged.emit(
            ix,
            ix,
            [Qt.ItemDataRole.DecorationRole, self._thumb_state_role],
        )

    def set_thumb_state_only(self, row: int, state: str | None) -> None:
        vi = self.view_item_at(row)
        if vi is None:
            return
        self._thumb_state_by_path[str(vi.path)] = state
        ix = self.index(row, 0)
        self.dataChanged.emit(ix, ix, [self._thumb_state_role])

    def thumbnail_state_for_row(self, row: int) -> str | None:
        vi = self.view_item_at(row)
        if vi is None:
            return None
        v = self._thumb_state_by_path.get(str(vi.path))
        return v if isinstance(v, str) else None

    def clear_thumbnail_state_all(self) -> None:
        self._thumb_state_by_path.clear()
        rc = self.row_count()
        if rc <= 0:
            return
        tl = self.index(0, 0)
        br = self.index(rc - 1, 0)
        self.dataChanged.emit(tl, br, [self._thumb_state_role])

    def emit_all_rows_user_role_changed(self) -> None:
        rc = self.row_count()
        if rc <= 0:
            return
        tl = self.index(0, 0)
        br = self.index(rc - 1, 0)
        self.dataChanged.emit(tl, br, [Qt.ItemDataRole.UserRole])

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid() or index.column() != 0:
            return None
        row = index.row()
        vi = self.view_item_at(row)
        if vi is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return display_name_for_item(vi)
        if role == Qt.ItemDataRole.UserRole:
            return vi
        if role == Qt.ItemDataRole.DecorationRole:
            k = str(vi.path)
            override = self._icon_override_by_path.get(k)
            if override is not None:
                return override
            return self._mv._icon_for_item(vi)
        if role == self._thumb_state_role:
            return self._thumb_state_by_path.get(str(vi.path))
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        default = super().flags(index)
        if not index.isValid():
            return default
        return default | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class PipelineListModel(QAbstractListModel):
    """Single-column list rows mirroring PipelineTileModel; UserRole holds ViewItem."""

    def __init__(self, main_view: QWidget, tile_model: PipelineTileModel) -> None:
        super().__init__(main_view)
        self._mv = main_view
        self._tile_model = tile_model

    def reset_structure(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return self._tile_model.row_count()

    def notify_insert_rows(self, row: int, count: int = 1) -> None:
        if count <= 0:
            return
        self.beginInsertRows(QModelIndex(), row, row + count - 1)
        self.endInsertRows()

    def notify_remove_rows(self, row: int, count: int = 1) -> None:
        if count <= 0:
            return
        self.beginRemoveRows(QModelIndex(), row, row + count - 1)
        self.endRemoveRows()

    def notify_thumb_column(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        ix = self.index(row)
        self.dataChanged.emit(ix, ix, [Qt.ItemDataRole.DecorationRole])

    def refresh_row(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        ix = self.index(row)
        self.dataChanged.emit(
            ix,
            ix,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.DecorationRole,
                Qt.ItemDataRole.UserRole,
            ],
        )

    def refresh_index_column(self) -> None:
        rc = self.rowCount()
        if rc <= 0:
            return
        self.dataChanged.emit(self.index(0), self.index(rc - 1), [Qt.ItemDataRole.DisplayRole])

    def emit_all_user_role_changed(self) -> None:
        rc = self.rowCount()
        if rc <= 0:
            return
        self.dataChanged.emit(
            self.index(0), self.index(rc - 1), [Qt.ItemDataRole.UserRole]
        )

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid() or index.column() != 0:
            return None
        row = index.row()
        vi = self._tile_model.view_item_at(row)
        if vi is None:
            return None
        if role == Qt.ItemDataRole.UserRole:
            return vi
        if role == Qt.ItemDataRole.DisplayRole:
            return display_name_for_item(vi)
        if role == Qt.ItemDataRole.DecorationRole:
            tix = self._tile_model.index(row, 0)
            return self._tile_model.data(tix, Qt.ItemDataRole.DecorationRole)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        default = super().flags(index)
        if not index.isValid():
            return default
        return default | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
