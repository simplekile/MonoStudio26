# Virtual Qt models for MainView: shared row list (`_all_items`); tile model = grid, list model = list rows.

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

    def rowCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def _emit_data_changed(self, top_row: int, bottom_row: int, roles: list[int]) -> None:
        """Notify views; fall back to reset when Qt C++ rowCount binding fails during emit."""
        if not self._rows or top_row < 0 or bottom_row < top_row:
            return
        bottom_row = min(bottom_row, len(self._rows) - 1)
        if top_row > bottom_row:
            return
        tl = self.createIndex(top_row, 0)
        br = self.createIndex(bottom_row, 0)
        try:
            self.dataChanged.emit(tl, br, roles)
        except NotImplementedError:
            self.beginResetModel()
            self.endResetModel()

    def refresh_preserving_thumbs(self) -> None:
        """Full view refresh without clearing thumbnail slot state."""
        self.beginResetModel()
        self.endResetModel()

    def _model_index(self, row: int, column: int = 0) -> QModelIndex:
        """createIndex path — avoids QAbstractItemModel.index() when rowCount binding is flaky."""
        if row < 0 or row >= len(self._rows):
            return QModelIndex()
        return self.createIndex(row, column)

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
        """No-op — structural changes use bind_rows()."""
        return

    def insert_rows_at(self, row: int, items: list[ViewItem]) -> None:
        if not items:
            return
        row = max(0, min(row, len(self._rows)))
        for offset, vi in enumerate(items):
            self._rows.insert(row + offset, vi)
        self.beginResetModel()
        self.endResetModel()

    def append_rows(self, items: list[ViewItem]) -> None:
        """Incremental row insert without resetting the whole model."""
        if not items:
            return
        first = len(self._rows)
        last = first + len(items) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._rows.extend(items)
        self.endInsertRows()

    def remove_row_at(self, row: int) -> None:
        if row < 0 or row >= len(self._rows):
            return
        del self._rows[row]
        self.beginResetModel()
        self.endResetModel()

    def notify_remove_rows(self, row: int, count: int = 1) -> None:
        """No-op — structural changes use bind_rows()."""
        return

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
        self._emit_data_changed(
            row,
            row,
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
        self._emit_data_changed(
            row,
            row,
            [Qt.ItemDataRole.DecorationRole, self._thumb_state_role],
        )

    def set_row_thumbnail(self, row: int, icon: QIcon, state: str | None) -> None:
        vi = self.view_item_at(row)
        if vi is None:
            return
        k = str(vi.path)
        self._icon_override_by_path[k] = icon
        self._thumb_state_by_path[k] = state
        self._emit_data_changed(
            row,
            row,
            [Qt.ItemDataRole.DecorationRole, self._thumb_state_role],
        )

    def set_thumb_state_only(self, row: int, state: str | None) -> None:
        vi = self.view_item_at(row)
        if vi is None:
            return
        self._thumb_state_by_path[str(vi.path)] = state
        self._emit_data_changed(row, row, [self._thumb_state_role])

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
        self._emit_data_changed(0, rc - 1, [self._thumb_state_role])

    def emit_all_rows_user_role_changed(self) -> None:
        rc = self.row_count()
        if rc <= 0:
            return
        self._emit_data_changed(0, rc - 1, [Qt.ItemDataRole.UserRole])

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

    def rowCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]  # noqa: N802
        if parent.isValid():
            return 0
        return self._tile_model.row_count()

    def _model_index(self, row: int, column: int = 0) -> QModelIndex:
        if row < 0 or row >= self._tile_model.row_count():
            return QModelIndex()
        return self.createIndex(row, column)

    def notify_insert_rows(self, row: int, count: int = 1) -> None:
        """Mirror tile model insertRows for list view."""
        if count <= 0:
            return
        first = max(0, int(row))
        last = first + int(count) - 1
        if last < first:
            return
        self.beginInsertRows(QModelIndex(), first, last)
        self.endInsertRows()

    def notify_remove_rows(self, row: int, count: int = 1) -> None:
        """No-op — tile model bind_rows() + reset_structure() refresh both views."""
        return

    def _emit_data_changed(self, top_row: int, bottom_row: int, roles: list[int]) -> None:
        rc = self._tile_model.row_count()
        if rc <= 0 or top_row < 0 or bottom_row < top_row:
            return
        bottom_row = min(bottom_row, rc - 1)
        if top_row > bottom_row:
            return
        tl = self.createIndex(top_row, 0)
        br = self.createIndex(bottom_row, 0)
        try:
            self.dataChanged.emit(tl, br, roles)
        except NotImplementedError:
            self.beginResetModel()
            self.endResetModel()

    def notify_thumb_column(self, row: int) -> None:
        if row < 0 or row >= self._tile_model.row_count():
            return
        self._emit_data_changed(row, row, [Qt.ItemDataRole.DecorationRole])

    def refresh_row(self, row: int) -> None:
        if row < 0 or row >= self._tile_model.row_count():
            return
        self._emit_data_changed(
            row,
            row,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.DecorationRole,
                Qt.ItemDataRole.UserRole,
            ],
        )

    def refresh_index_column(self) -> None:
        rc = self._tile_model.row_count()
        if rc <= 0:
            return
        self._emit_data_changed(0, rc - 1, [Qt.ItemDataRole.DisplayRole])

    def emit_all_user_role_changed(self) -> None:
        rc = self._tile_model.row_count()
        if rc <= 0:
            return
        self._emit_data_changed(0, rc - 1, [Qt.ItemDataRole.UserRole])

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
            tix = self._tile_model._model_index(row, 0)
            return self._tile_model.data(tix, Qt.ItemDataRole.DecorationRole)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        default = super().flags(index)
        if not index.isValid():
            return default
        return default | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
