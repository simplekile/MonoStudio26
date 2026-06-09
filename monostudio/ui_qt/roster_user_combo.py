"""Combo box for picking a studio roster user (assignee, etc.)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QWidget

from monostudio.core.user_identity import StudioUser, read_roster


class RosterUserCombo(QComboBox):
    """Active roster users + an explicit unassigned entry."""

    _EMPTY_HINT = "(Add team in Settings → Team)"

    def __init__(
        self,
        workspace_root: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace_root: Path | None = None
        self.setMinimumWidth(200)
        self.set_workspace_root(workspace_root)

    def set_workspace_root(self, workspace_root: Path | None) -> None:
        prev = self.selected_user_id()
        try:
            self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        except OSError:
            self._workspace_root = None
        self.reload()
        self.set_user_id(prev)

    def showPopup(self) -> None:  # type: ignore[override]
        self.reload()
        super().showPopup()

    def reload(self) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItem("(Unassigned)", None)
        users = [
            u for u in read_roster(self._workspace_root)
            if u.active and (u.name or "").strip()
        ]
        users.sort(key=lambda u: (u.name or "").casefold())
        for user in users:
            self.addItem(user.name.strip(), user.id)
        if not users:
            self.addItem(self._EMPTY_HINT, None)
            model = self.model()
            if model is not None:
                hint_item = model.item(self.count() - 1)
                if hint_item is not None:
                    hint_item.setEnabled(False)
        self.blockSignals(False)

    def set_user_id(self, user_id: str | None) -> None:
        uid = (user_id or "").strip()
        if not uid:
            self.setCurrentIndex(0)
            return
        ix = self.findData(uid, Qt.ItemDataRole.UserRole)
        self.setCurrentIndex(ix if ix >= 0 else 0)

    def selected_user_id(self) -> str | None:
        data = self.currentData(Qt.ItemDataRole.UserRole)
        if data is None:
            return None
        uid = str(data).strip()
        return uid or None

    def selected_user(self) -> StudioUser | None:
        uid = self.selected_user_id()
        if not uid:
            return None
        for user in read_roster(self._workspace_root):
            if user.id == uid:
                return user
        return None
