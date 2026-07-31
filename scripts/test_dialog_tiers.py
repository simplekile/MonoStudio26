"""
MONOS Dialog tiers — harness for the golden reference (visual design FROZEN).

Implementation: monostudio/ui_qt/dialog_tier/
Rule: .cursor/rules/plan_dialog_tier_golden_reference_v1.mdc

Run:
    python scripts/test_dialog_tiers.py
    python scripts/test_dialog_tiers.py --theme light
    python scripts/test_dialog_tiers.py --scale 1.25
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont, QGuiApplication, QShowEvent
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from monostudio.ui_qt.dialog_tier import (
    CURRENT_THEME,
    DateField,
    DccPickerGrid,
    DccPickerItem,
    FieldRow,
    ImportSourceCard,
    MetadataCard,
    MonoSelect,
    MonoSelectOption,
    PlainFieldInput,
    ProjectIdMetadataCard,
    T,
    Tier1Dialog,
    Tier2Dialog,
    WorkspacePicker,
    apply_tier_app_theme,
    configure_tier_text_rendering,
    tier_btn,
    tier_font,
)


# ---------------------------------------------------------------------------
# Golden reference demos (compose shells + components — do not restyle)
# ---------------------------------------------------------------------------


class NewProjectTier1Demo(Tier1Dialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.set_sidebar_brand(
            icon="folder-kanban",
            title="New Project",
            subtitle="Create a new project in your workspace",
            icon_size=28,
        )

        self._name_wrap = PlainFieldInput("Forest Spirit", leading_icon="pencil")
        self._name = self._name_wrap.input
        self.add_field(FieldRow("Project name", self._name_wrap))

        self._start_wrap = DateField(QDate.currentDate())
        self.add_field(FieldRow("Start date", self._start_wrap))

        self.add_form_stretch()

        self._id_card = ProjectIdMetadataCard("260721_forest_spirit")
        self.add_field(self._id_card)

        self.add_form_spacing(T["field_readonly_block_gap"])
        self.add_field(
            FieldRow(
                "Workspace",
                WorkspacePicker("D:\\Dropbox\\MonoStudio\\Workspace"),
                hint="All project data will be created in this workspace.",
            )
        )

        self.add_footer_btn("Cancel", "ghost", slot=self.reject)
        self.add_footer_cta("Create Project", slot=self.accept)
        self._name.textChanged.connect(self._sync_id)

    def _sync_id(self, text: str) -> None:
        slug = text.strip().lower().replace(" ", "_") or "untitled"
        self._id_card.set_value(f"260721_{slug}")

    def showEvent(self, e: QShowEvent) -> None:  # noqa: N802
        super().showEvent(e)
        self._name.setFocus()
        self._name.selectAll()


class NewAssetTier2Demo(Tier2Dialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, l2_height=T["l2_h_compact"])
        self.setWindowTitle("Create Asset")
        self.set_topbar_brand(
            icon="box",
            title="Create Asset",
            subtitle="Add a new asset folder to the project.",
            icon_size=28,
        )

        self._type = MonoSelect(
            [
                MonoSelectOption("char_", "Character", secondary="Prefix: char_", icon="user"),
                MonoSelectOption("prop_", "Prop", secondary="Prefix: prop_", icon="package"),
                MonoSelectOption("env_", "Environment", secondary="Prefix: env_", icon="trees"),
            ]
        )
        self._type.setMinimumWidth(280)
        self.add_field(FieldRow("Type / Preset", self._type))

        self._name_wrap = PlainFieldInput("", placeholder="e.g. aya", leading_icon="pencil")
        self._name = self._name_wrap.input
        self.add_field(FieldRow("Asset name", self._name_wrap))

        self.add_form_spacing(T["field_readonly_block_gap"])

        self._folder_card = MetadataCard(
            "Asset folder",
            value="char_aya",
            footnote="Generated from preset and name",
            mono=True,
        )
        self.add_field(self._folder_card)

        self.add_form_spacing(T["field_readonly_block_gap"])
        self.add_field(
            MetadataCard(
                "On create",
                body="Work and publish subfolders are added from the selected preset.",
            )
        )

        self.add_footer_btn("Cancel", "ghost", slot=self.reject)
        self.add_footer_cta("Create Asset", slot=self.accept)
        self._name.textChanged.connect(self._sync_folder_preview)
        self._type.value_changed.connect(lambda _v: self._sync_folder_preview(self._name.text()))

    def _preset_prefix(self) -> str:
        return self._type.current_value()

    def _sync_folder_preview(self, text: str) -> None:
        slug = text.strip() or "…"
        self._folder_card.set_value(f"{self._preset_prefix()}{slug}")

    def showEvent(self, e: QShowEvent) -> None:  # noqa: N802
        super().showEvent(e)
        self._name.setFocus()


class NewShotTier2Demo(Tier2Dialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, l2_height=T["l2_h_compact"])
        self.setWindowTitle("Create Shot")
        self.set_topbar_brand(
            icon="clapperboard",
            title="Create Shot",
            subtitle="Add a new shot to the sequence.",
            icon_size=28,
        )

        self._seq = MonoSelect(["SEQ_010", "SEQ_020", "SEQ_030"])
        self._seq.setMinimumWidth(280)
        self.add_field(FieldRow("Sequence", self._seq))

        self._name_wrap = PlainFieldInput("", placeholder="e.g. 010", leading_icon="pencil")
        self._name = self._name_wrap.input
        self.add_field(FieldRow("Shot name", self._name_wrap))

        self.add_form_spacing(T["field_readonly_block_gap"])

        self._folder_card = MetadataCard(
            "Shot folder",
            value="shot_010_010",
            footnote="Generated from sequence and name",
            mono=True,
        )
        self.add_field(self._folder_card)

        self.add_form_spacing(T["field_readonly_block_gap"])
        self.add_field(
            MetadataCard(
                "On create",
                body="Department folders are added from the shot preset.",
            )
        )

        self.add_footer_btn("Cancel", "ghost", slot=self.reject)
        self.add_footer_cta("Create Shot", slot=self.accept)
        self._name.textChanged.connect(self._sync_folder_preview)
        self._seq.value_changed.connect(lambda _v: self._sync_folder_preview(self._name.text()))

    def _sync_folder_preview(self, text: str) -> None:
        seq_text = self._seq.current_value()
        seq_num = seq_text.split("_")[-1] if "_" in seq_text else "010"
        slug = text.strip() or "…"
        self._folder_card.set_value(f"shot_{seq_num}_{slug}")

    def showEvent(self, e: QShowEvent) -> None:  # noqa: N802
        super().showEvent(e)
        self._name.setFocus()


def _mock_dcc_picker_items() -> list[DccPickerItem]:
    return [
        DccPickerItem("blender", "Blender", "blender", "#E87D0D", department_default=True),
        DccPickerItem("maya", "Maya", "autodeskmaya", "#37A5CC", last_used=True),
        DccPickerItem("houdini", "Houdini", "houdini", "#FF4713", disabled=True),
        DccPickerItem("substance_painter", "Substance", "substancepainter", "#eeeeee"),
        DccPickerItem("affinity", "Affinity", "affinity", "#a7f175"),
        DccPickerItem("rizomuv", "RizomUV", "rizomuv", "#FF6600"),
        DccPickerItem("fusion", "Fusion", "fusion", "#FF6A00"),
        DccPickerItem("nuke", "Nuke", "nuke", "#F5B941"),
        DccPickerItem("mari", "Mari", "mari", "#F68D2E"),
        DccPickerItem("zbrush", "ZBrush", "zbrush", "#E74C3C"),
    ]


class CreateNewDccTier2Demo(Tier2Dialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, l2_height=T["l2_h_tall"])
        self.setWindowTitle("Create New")
        self.set_topbar_brand(
            icon="file-plus",
            title="Create New",
            subtitle="char_aya · Modeling",
            icon_size=28,
        )

        self.add_field(
            MetadataCard(
                "Context",
                value="char_aya",
                footnote="Department · Modeling · Type · character",
                mono=True,
            )
        )

        self._dcc_grid = DccPickerGrid()
        self._dcc_grid.set_items(_mock_dcc_picker_items(), selected_id="blender")
        self.add_field(
            FieldRow(
                "DCC",
                self._dcc_grid,
                hint="Right-click a DCC to set department default.",
            )
        )

        self.add_form_spacing(T["field_readonly_block_gap"])

        self._import_card = ImportSourceCard()
        self.add_field(FieldRow("Import", self._import_card))

        self.add_footer_btn("Cancel", "ghost", slot=self.reject)
        self.add_footer_cta("Create", slot=self.accept)


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


class TierLauncher(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TierLauncher")
        self.setWindowTitle("MONOS Dialog Tiers")
        self.resize(480, 400)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        root.setSpacing(16)

        head = QHBoxLayout()
        self._title_lbl = QLabel("Dialog Tiers")
        self._title_lbl.setFont(tier_font(18, QFont.Weight.DemiBold))
        head.addWidget(self._title_lbl)
        head.addStretch()
        self._theme_btn = tier_btn("Light theme", "link")
        self._theme_btn.setToolTip("Toggle theme to validate semantic tokens (not product support)")
        self._theme_btn.clicked.connect(self._toggle_theme)
        head.addWidget(self._theme_btn)
        root.addLayout(head)

        self._sub_lbl = QLabel(
            "Tier 1 — major workspace actions (New Project).\n"
            "Tier 2 — in-project creates · compact / standard / tall height by content."
        )
        self._sub_lbl.setWordWrap(True)
        self._sub_lbl.setFont(tier_font(13))
        root.addWidget(self._sub_lbl)

        self._cards: list[QPushButton] = []
        self._cards.append(
            self._make_card("New Project", "Two-pane · sidebar context · icon field rows", "Tier 1", NewProjectTier1Demo)
        )
        self._cards.append(
            self._make_card(
                "Create Asset", "Top bar · L1 field + metadata cards · ghost + gradient CTA", "Tier 2", NewAssetTier2Demo
            )
        )
        self._cards.append(
            self._make_card("Create Shot", "Same L2 shell · shot folder preview card", "Tier 2", NewShotTier2Demo)
        )
        self._cards.append(
            self._make_card(
                "Create New (DCC)",
                "DCC picker grid · context card · import source row",
                "Tier 2",
                CreateNewDccTier2Demo,
            )
        )
        for card in self._cards:
            root.addWidget(card)
        root.addStretch()

        self._dpi_label = QLabel("")
        self._dpi_label.setFont(tier_font(11))
        root.addWidget(self._dpi_label)

        self._sync_theme_label()
        self._restyle()

    def _make_card(self, title: str, desc: str, tier: str, cls: type) -> QPushButton:
        from monostudio.ui_qt.dialog_tier.reference import _text_style

        btn = QPushButton()
        btn.setProperty("class", "TierLauncherBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(btn)
        lay.setSpacing(4)
        tier_lbl = QLabel(tier.upper())
        tier_lbl.setObjectName("tierCardTier")
        tier_lbl.setFont(tier_font(10, QFont.Weight.Bold))
        tl = QLabel(title)
        tl.setObjectName("tierCardTitle")
        tl.setFont(tier_font(14, QFont.Weight.DemiBold))
        dl = QLabel(desc)
        dl.setObjectName("tierCardDesc")
        dl.setFont(tier_font(12))
        lay.addWidget(tier_lbl)
        lay.addWidget(tl)
        lay.addWidget(dl)
        btn.clicked.connect(lambda _=False, c=cls: self._open(c))
        return btn

    def _sync_theme_label(self) -> None:
        if CURRENT_THEME == "dark":
            self._theme_btn.setText("Light theme")
        else:
            self._theme_btn.setText("Dark theme")

    def _restyle(self) -> None:
        from monostudio.ui_qt.dialog_tier.reference import _text_style

        self.setStyleSheet(_text_style(color=T["text"], bg=T["bg"]))
        self._title_lbl.setStyleSheet(_text_style(color=T["text"]))
        self._sub_lbl.setStyleSheet(_text_style(color=T["meta"]))
        self._dpi_label.setStyleSheet(_text_style(color=T["meta"]))
        for card in self._cards:
            for lbl in card.findChildren(QLabel):
                role = lbl.objectName()
                if role == "tierCardTier":
                    lbl.setStyleSheet(_text_style(color=T["blue_hi"], letter_spacing=1.0))
                elif role == "tierCardTitle":
                    lbl.setStyleSheet(_text_style(color=T["text"]))
                elif role == "tierCardDesc":
                    lbl.setStyleSheet(_text_style(color=T["meta"]))
            card.style().unpolish(card)
            card.style().polish(card)
        self._theme_btn.style().unpolish(self._theme_btn)
        self._theme_btn.style().polish(self._theme_btn)
        self._refresh_dpi_label()
        self.update()

    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        next_theme = "light" if CURRENT_THEME == "dark" else "dark"
        apply_tier_app_theme(app, next_theme)
        self._sync_theme_label()
        self._restyle()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_dpi_label()

    def _refresh_dpi_label(self) -> None:
        screen = QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio()) if screen is not None else 1.0
        forced = os.environ.get("QT_SCALE_FACTOR")
        pct = int(round(dpr * 100))
        extra = f" · QT_SCALE_FACTOR={forced}" if forced else ""
        self._dpi_label.setText(f"theme={CURRENT_THEME} · DPI ~{pct}% (dpr={dpr:.2f}){extra}")

    def _open(self, cls: type) -> None:
        code = cls(parent=self).exec()
        print(cls.__name__, "->", "ok" if code == QDialog.DialogCode.Accepted else "cancel", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="MONOS dialog tier mockups (L1/L2 harness)")
    parser.add_argument(
        "--theme",
        choices=("dark", "light"),
        default="dark",
        help="Semantic token palette — light is for token validation, not product support.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        metavar="FACTOR",
        help="Force Qt UI scale before launch (1.0=100%%, 1.25=125%%, 1.5=150%%, 1.75=175%%, 2.0=200%%).",
    )
    args = parser.parse_args()
    if args.scale is not None:
        os.environ["QT_SCALE_FACTOR"] = str(args.scale)

    configure_tier_text_rendering()
    app = QApplication(sys.argv)
    apply_tier_app_theme(app, args.theme)
    w = TierLauncher()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
