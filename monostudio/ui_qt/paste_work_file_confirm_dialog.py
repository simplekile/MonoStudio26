"""Confirm before pasting a copied work file as the next version."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font


def ask_paste_work_file(
    parent,
    *,
    source_name: str,
    destination_name: str,
) -> bool:
    """Show MONOS-styled paste confirmation; returns True if user confirmed Paste."""
    dlg = PasteWorkFileConfirmDialog(
        parent=parent,
        source_name=source_name,
        destination_name=destination_name,
    )
    return dlg.exec() == QDialog.DialogCode.Accepted


class PasteWorkFileConfirmDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        source_name: str = "",
        destination_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Paste Work File?")
        self.setModal(True)
        self.setObjectName("PasteWorkFileConfirmDialog")
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Paste work file as next version?", self)
        title.setObjectName("DialogSectionTitle")
        title.setFont(monos_font("Inter", 14, QFont.Weight.DemiBold))
        root.addWidget(title)

        src = (source_name or "").strip() or "(unknown)"
        dst = (destination_name or "").strip() or "(unknown)"
        label_hex = MONOS_COLORS.get("text_label", "#a1a1aa")
        from_hex = MONOS_COLORS.get("amber_400", "#fbbf24")
        into_hex = MONOS_COLORS.get("emerald_500", "#10b981")

        src_lbl = QLabel(self)
        src_lbl.setObjectName("PasteWorkFileFrom")
        src_lbl.setWordWrap(True)
        src_lbl.setTextFormat(Qt.TextFormat.RichText)
        src_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        src_lbl.setText(
            f'<span style="color:{label_hex}">From:</span> '
            f'<span style="color:{from_hex}">{html.escape(src)}</span>'
        )
        root.addWidget(src_lbl)

        into_lbl = QLabel(self)
        into_lbl.setObjectName("PasteWorkFileInto")
        into_lbl.setWordWrap(True)
        into_lbl.setTextFormat(Qt.TextFormat.RichText)
        into_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        into_lbl.setText(
            f'<span style="color:{label_hex}">Into:</span> '
            f'<span style="color:{into_hex}">{html.escape(dst)}</span>'
        )
        root.addWidget(into_lbl)

        hint = QLabel(
            "Creates the next work-file version in the target work folder "
            "(and missing DCC/work folders if needed).",
            self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        buttons = QDialogButtonBox(self)
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        paste = buttons.addButton("Paste", QDialogButtonBox.ButtonRole.AcceptRole)
        paste.setObjectName("DialogPrimaryButton")
        cancel.setObjectName("DialogSecondaryButton")
        paste.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
