# Virtual Qt models for MainView Assets/Shots: each row index maps to one ViewItem in the shared
# row list owned by MainView (`_all_items`); tile model serves QListView, table model reads the same rows + tile thumbnails.

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import QWidget

from monostudio.core.models import Asset, Shot
from monostudio.core.workspace_reader import ProjectQuickStats
from monostudio.ui_qt.style import monos_font
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


class PipelineTableModel(QAbstractTableModel):
    """Table rows mirror PipelineTileModel rowCount; column layout follows MainView browser context."""

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

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        ctx = getattr(self._mv, "_browser_context", "asset")
        return 8 if ctx == "project" else 13

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
        ix = self.index(row, 1)
        self.dataChanged.emit(ix, ix, [Qt.ItemDataRole.DecorationRole])

    def refresh_row(self, row: int) -> None:
        cc = self.columnCount()
        if cc <= 0 or row < 0 or row >= self.rowCount():
            return
        tl = self.index(row, 0)
        br = self.index(row, cc - 1)
        self.dataChanged.emit(
            tl,
            br,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.DecorationRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.FontRole,
                Qt.ItemDataRole.UserRole,
            ],
        )

    def refresh_index_column(self) -> None:
        rc = self.rowCount()
        if rc <= 0:
            return
        self.dataChanged.emit(self.index(0, 0), self.index(rc - 1, 0), [Qt.ItemDataRole.DisplayRole])

    def refresh_column_for_all_rows(self, col: int, roles: list[int]) -> None:
        rc = self.rowCount()
        if rc <= 0 or col < 0 or col >= self.columnCount():
            return
        self.dataChanged.emit(self.index(0, col), self.index(rc - 1, col), roles)

    def emit_all_user_role_changed(self) -> None:
        rc = self.rowCount()
        cc = self.columnCount()
        if rc <= 0 or cc <= 0:
            return
        self.dataChanged.emit(self.index(0, 0), self.index(rc - 1, cc - 1), [Qt.ItemDataRole.UserRole])

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        headers = self._mv._list_headers()
        if section < 0 or section >= len(headers):
            return None
        return headers[section]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        vi = self._tile_model.view_item_at(row)
        if vi is None:
            return None
        ctx = getattr(self._mv, "_browser_context", "asset")

        if role == Qt.ItemDataRole.UserRole:
            return vi

        if ctx == "project":
            stats = vi.ref if isinstance(vi.ref, ProjectQuickStats) else None
            status = "WAITING" if not stats else stats.status
            shots = "—" if not stats or stats.shots_count is None else str(stats.shots_count)
            assets = "—" if not stats or stats.assets_count is None else str(stats.assets_count)
            updated = "—" if not stats or not stats.last_modified else stats.last_modified
            if col == 0:
                if role == Qt.ItemDataRole.DisplayRole:
                    return str(row + 1)
                return None
            if col == 1:
                if role == Qt.ItemDataRole.DecorationRole:
                    tix = self._tile_model.index(row, 0)
                    return self._tile_model.data(tix, Qt.ItemDataRole.DecorationRole)
                return None
            if col == 2:
                if role == Qt.ItemDataRole.DisplayRole:
                    return display_name_for_item(vi)
                if role == Qt.ItemDataRole.FontRole:
                    return monos_font("Inter", 13, QFont.Weight.Bold)
                return None
            if col == 3:
                if role == Qt.ItemDataRole.DisplayRole:
                    return ""
                if role == Qt.ItemDataRole.ForegroundRole:
                    return self._mv._status_foreground(status)
                return None
            if col == 4:
                return assets if role == Qt.ItemDataRole.DisplayRole else None
            if col == 5:
                return shots if role == Qt.ItemDataRole.DisplayRole else None
            if col == 6:
                return updated if role == Qt.ItemDataRole.DisplayRole else None
            if col == 7:
                if role == Qt.ItemDataRole.DisplayRole:
                    return str(vi.path)
                if role == Qt.ItemDataRole.FontRole:
                    return monos_font("JetBrains Mono", 11)
                return None
            return None

        # asset / shot — do NOT call aggregate_status / label_for unless column 6 needs it (Qt queries every cell).
        if col == 0:
            return str(row + 1) if role == Qt.ItemDataRole.DisplayRole else None
        if col == 1:
            if role == Qt.ItemDataRole.DecorationRole:
                tix = self._tile_model.index(row, 0)
                return self._tile_model.data(tix, Qt.ItemDataRole.DecorationRole)
            return None
        if col == 2:
            if role == Qt.ItemDataRole.DisplayRole:
                return display_name_for_item(vi)
            if role == Qt.ItemDataRole.FontRole:
                return monos_font("Inter", 13, QFont.Weight.Bold)
            return None
        if col in (3, 4, 5, 6, 7):
            return "" if role == Qt.ItemDataRole.DisplayRole else None
        if col == 8:
            if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.ForegroundRole:
                if isinstance(vi.ref, (Asset, Shot)):
                    st_lbl, st_col = self._mv._list_asset_shot_status_label_and_color(vi.ref)
                else:
                    st_lbl, st_col = "Waiting", QColor("#71717a")
                if role == Qt.ItemDataRole.DisplayRole:
                    return st_lbl
                return st_col
            return None
        if col == 9:
            if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.ForegroundRole:
                text, overdue = self._mv._list_due_text(vi)
                if role == Qt.ItemDataRole.DisplayRole:
                    return text
                if overdue:
                    return QColor("#ef4444")
                return QColor("#71717a")
            if role == Qt.ItemDataRole.FontRole:
                return monos_font("JetBrains Mono", 11)
            return None
        if col == 10:
            if role == Qt.ItemDataRole.DisplayRole:
                return self._mv._list_version_text(vi)
            if role == Qt.ItemDataRole.FontRole:
                return monos_font("JetBrains Mono", 11)
            return None
        if col == 11:
            return self._mv._list_last_updated(vi) if role == Qt.ItemDataRole.DisplayRole else None
        if col == 12:
            if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.ForegroundRole:
                text, color = self._mv._list_assignee_display(vi)
                if role == Qt.ItemDataRole.DisplayRole:
                    return text
                return color if color is not None else QColor("#71717a")
            return None
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # type: ignore[override]
        default = super().flags(index)
        if not index.isValid():
            return default
        return default | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
