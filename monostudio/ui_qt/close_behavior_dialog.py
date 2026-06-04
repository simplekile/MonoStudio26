"""First-run (or explicit) dialog: close window → tray vs quit."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from monostudio.core.tray_preferences import CloseAction
from monostudio.ui_qt.style import MonosDialog


class CloseBehaviorDialog(MonosDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Close MONOS")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._action: CloseAction = "minimize"
        self._remember = True

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hint = QLabel(
            "When you close the window, MONOS can keep running in the system tray "
            "so notifications and background tasks continue.",
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        self._radio_tray = QRadioButton("Hide to system tray (recommended)", self)
        self._radio_tray.setChecked(True)
        self._radio_quit = QRadioButton("Quit MONOS completely", self)
        root.addWidget(self._radio_tray)
        root.addWidget(self._radio_quit)

        self._remember_cb = QCheckBox("Remember my choice", self)
        self._remember_cb.setChecked(True)
        root.addWidget(self._remember_cb)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("Continue")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        self._action = "quit" if self._radio_quit.isChecked() else "minimize"
        self._remember = self._remember_cb.isChecked()
        self.accept()

    def chosen_action(self) -> CloseAction:
        return self._action

    def remember_choice(self) -> bool:
        return self._remember
