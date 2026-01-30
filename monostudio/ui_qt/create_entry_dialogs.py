from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QVBoxLayout,
)

_MUTED_HELPER_STYLE = "color: #A9ABB0; font-size: 11px;"


def _field_block(label_text: str, field: QLineEdit, helper_text: str) -> QWidget:
    """
    Field grouping (STRICT):
    - Label
    - Input field
    - Helper text (below)
    Wrapped in one vertical layout block with safe spacing.
    """
    label = QLabel(label_text)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    helper = QLabel(helper_text)
    helper.setWordWrap(True)
    helper.setStyleSheet(_MUTED_HELPER_STYLE)
    helper.setTextInteractionFlags(Qt.TextSelectableByMouse)
    helper.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    block = QWidget()
    layout = QVBoxLayout(block)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(label)
    layout.addSpacing(7)  # label -> input (6–8px)
    layout.addWidget(field)
    layout.addSpacing(5)  # input -> helper (4–6px)
    layout.addWidget(helper)
    return block


class CreateAssetDialog(QDialog):
    """Minimal dialog: Asset Type + Asset Name (both required)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Asset")
        self.setModal(True)

        self._asset_type = QLineEdit()
        self._asset_type.setPlaceholderText("e.g. char")
        self._asset_name = QLineEdit()
        self._asset_name.setPlaceholderText("e.g. char_aya")

        self._asset_type.textChanged.connect(self._update_ok_enabled)
        self._asset_name.textChanged.connect(self._update_ok_enabled)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        buttons_row = QWidget()
        buttons_layout = QHBoxLayout(buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(0)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self._buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)  # >= 12px all sides
        layout.setSpacing(0)

        layout.addWidget(
            _field_block(
                "Asset Type",
                self._asset_type,
                "Category folder (recommended: char / prop / env / fx).",
            )
        )
        layout.addSpacing(14)  # between field blocks (12–16px)
        layout.addWidget(
            _field_block(
                "Asset Name",
                self._asset_name,
                "Recommended format: <type>_<name> (example: char_aya).",
            )
        )

        layout.addSpacing(12)  # top margin above button row (>= 12px)
        layout.addWidget(buttons_row)

        self._update_ok_enabled()

    def asset_type(self) -> str:
        return self._asset_type.text().strip()

    def asset_name(self) -> str:
        return self._asset_name.text().strip()

    def _update_ok_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is None:
            return
        ok.setEnabled(bool(self.asset_type()) and bool(self.asset_name()))


class CreateShotDialog(QDialog):
    """Minimal dialog: Shot Name (required)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Shot")
        self.setModal(True)

        self._shot_name = QLineEdit()
        self._shot_name.setPlaceholderText("e.g. sh010")
        self._shot_name.textChanged.connect(self._update_ok_enabled)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        buttons_row = QWidget()
        buttons_layout = QHBoxLayout(buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(0)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self._buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)  # >= 12px all sides
        layout.setSpacing(0)

        layout.addWidget(
            _field_block(
                "Shot Name",
                self._shot_name,
                "Recommended format: sh### (zero-padded, example: sh010).",
            )
        )

        layout.addSpacing(12)  # top margin above button row (>= 12px)
        layout.addWidget(buttons_row)

        self._update_ok_enabled()

    def shot_name(self) -> str:
        return self._shot_name.text().strip()

    def _update_ok_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is None:
            return
        ok.setEnabled(bool(self.shot_name()))

