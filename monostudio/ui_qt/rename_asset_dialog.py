from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from monostudio.core.asset_rename import _is_safe_single_folder_name, _normalize_asset_name_for_type
from monostudio.ui_qt.style import MonosDialog


class RenameAssetDialog(MonosDialog):
    def __init__(self, *, project_root: Path, asset_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rename Asset")
        self.setModal(True)

        self._project_root = Path(project_root)
        self._asset_path = Path(asset_path)

        self._current_name = self._asset_path.name

        title = QLabel("Current name")
        title.setObjectName("DialogHint")
        self._current = QLineEdit(self._current_name)
        self._current.setReadOnly(True)
        self._current.setProperty("mono", True)

        new_lbl = QLabel("New name")
        new_lbl.setObjectName("DialogHint")
        self._new_name = QLineEdit(self._current_name)
        self._new_name.textChanged.connect(self._update_ok_enabled)
        self._new_name.textChanged.connect(self._update_preview)

        self._preview = QLabel("")
        self._preview.setObjectName("DialogHelper")
        self._preview.setWordWrap(True)
        self._preview.setVisible(False)

        self._exists_warning = QLabel("A folder with this name already exists.")
        self._exists_warning.setObjectName("DialogWarning")
        self._exists_warning.setWordWrap(True)
        self._exists_warning.setVisible(False)

        btn_row = QWidget()
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(10)
        self._ok_btn = QPushButton("Rename")
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        btn_l.addWidget(self._ok_btn)
        btn_l.addWidget(cancel_btn)
        btn_l.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(title)
        layout.addWidget(self._current)
        layout.addSpacing(8)
        layout.addWidget(new_lbl)
        layout.addWidget(self._new_name)
        layout.addWidget(self._preview)
        layout.addWidget(self._exists_warning)
        layout.addSpacing(10)
        layout.addWidget(btn_row)

        self._update_preview()
        self._update_ok_enabled()

    def final_name(self) -> str:
        raw = self._new_name.text().strip()
        final = _normalize_asset_name_for_type(
            project_root=self._project_root,
            type_folder=self._asset_path.parent.name,
            raw_name=raw,
        )
        return final.strip()

    def _update_preview(self) -> None:
        final = self.final_name()
        raw = self._new_name.text().strip()
        if not raw:
            self._preview.setVisible(False)
            return
        if final and final != raw:
            self._preview.setText(f"Final folder name: {final}")
            self._preview.setVisible(True)
        else:
            self._preview.setVisible(False)

    def _update_ok_enabled(self) -> None:
        final = self.final_name()
        ok = True

        raw = self._new_name.text().strip()
        if not raw:
            ok = False

        if not final or not _is_safe_single_folder_name(final):
            ok = False

        if final == self._current_name:
            ok = False

        exists = False
        if ok:
            target = self._asset_path.parent / final
            try:
                exists = target.exists()
            except OSError:
                exists = True
            if exists:
                ok = False

        self._exists_warning.setVisible(bool(exists))
        self._ok_btn.setEnabled(bool(ok))

