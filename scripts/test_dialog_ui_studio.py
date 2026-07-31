"""
MONOS Dialog UI — Studio Minimal (alternative to v2 chromatic).

Philosophy: monochrome zinc, typography + spacing for hierarchy.
Color only where it carries meaning: primary CTA, focus, destructive, status.

Run:
    python scripts/test_dialog_ui_studio.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, apply_dark_theme

# ---------------------------------------------------------------------------
# Tokens — zinc monochrome, one accent
# ---------------------------------------------------------------------------

C = {
    "dialog": "#18181b",
    "inset": "#121214",
    "raised": "#1e2124",
    "line": "#27272a",
    "line_strong": "#3f3f46",
    "text": "#fafafa",
    "body": "#d4d4d8",
    "label": "#a1a1aa",
    "meta": "#71717a",
    "mono": "#8b8b96",
    "accent": "#2563eb",
    "accent_hover": "#3b82f6",
    "danger": "#ef4444",
    "danger_hover": "#f87171",
    "radius": 12,
    "radius_sm": 6,
}

STUDIO_QSS = f"""
QWidget#StudioDialogRoot {{ background: transparent; }}

QLabel.StudioTitle {{
    color: {C["text"]};
    font-size: 14px;
    font-weight: 600;
}}
QLabel.StudioSubtitle {{
    color: {C["meta"]};
    font-size: 12px;
    font-weight: 500;
}}
QLabel.StudioSection {{
    color: {C["meta"]};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel.StudioFieldLabel {{
    color: {C["label"]};
    font-size: 12px;
    font-weight: 500;
}}
QLabel.StudioHint {{
    color: {C["meta"]};
    font-size: 11px;
    font-weight: 500;
}}
QLabel.StudioMono {{
    color: {C["mono"]};
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    font-weight: 500;
}}

QLineEdit, QComboBox {{
    background: {C["inset"]};
    color: {C["text"]};
    border: 1px solid {C["line"]};
    border-radius: {C["radius_sm"]}px;
    padding: 8px 10px;
    font-size: 13px;
    font-weight: 500;
    selection-background-color: {C["accent"]};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {C["accent"]};
    background: {C["inset"]};
}}
QLineEdit[mono="true"] {{
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    color: {C["mono"]};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {C["raised"]};
    color: {C["text"]};
    border: 1px solid {C["line_strong"]};
    selection-background-color: {C["accent"]};
    outline: none;
}}

QCheckBox {{
    color: {C["body"]};
    font-size: 13px;
    font-weight: 500;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border-radius: 4px;
    border: 1px solid {C["line_strong"]};
    background: {C["inset"]};
}}
QCheckBox::indicator:checked {{
    background: {C["accent"]};
    border-color: {C["accent"]};
}}

QPushButton#StudioClose {{
    background: transparent;
    border: none;
    border-radius: 6px;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
}}
QPushButton#StudioClose:hover {{
    background: {C["raised"]};
}}

QPushButton[studioRole="primary"] {{
    background: {C["accent"]};
    color: #fff;
    border: none;
    border-radius: {C["radius_sm"]}px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    min-width: 80px;
}}
QPushButton[studioRole="primary"]:hover {{ background: {C["accent_hover"]}; }}
QPushButton[studioRole="primary"]:disabled {{
    background: #1e3a5f;
    color: #6b7280;
}}

QPushButton[studioRole="secondary"] {{
    background: transparent;
    color: {C["label"]};
    border: 1px solid {C["line_strong"]};
    border-radius: {C["radius_sm"]}px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    min-width: 72px;
}}
QPushButton[studioRole="secondary"]:hover {{
    color: {C["text"]};
    background: {C["raised"]};
}}

QPushButton[studioRole="ghost"] {{
    background: transparent;
    color: {C["meta"]};
    border: none;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton[studioRole="ghost"]:hover {{ color: {C["label"]}; }}

QPushButton[studioRole="danger"] {{
    background: {C["danger"]};
    color: #fff;
    border: none;
    border-radius: {C["radius_sm"]}px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton[studioRole="danger"]:hover {{ background: {C["danger_hover"]}; }}
QPushButton[studioRole="danger"]:disabled {{
    background: #3f1d1d;
    color: #6b7280;
}}

QWidget#StudioLauncher {{ background: {C["inset"]}; }}
QLabel#StudioLauncherTitle {{
    color: {C["text"]};
    font-size: 18px;
    font-weight: 600;
}}
QLabel#StudioLauncherSub {{
    color: {C["meta"]};
    font-size: 13px;
}}
QPushButton.StudioLauncherItem {{
    background: {C["dialog"]};
    border: 1px solid {C["line"]};
    border-radius: 8px;
    padding: 14px 16px;
    text-align: left;
}}
QPushButton.StudioLauncherItem:hover {{
    border-color: {C["line_strong"]};
    background: {C["raised"]};
}}
"""


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def _btn(label: str, role: str) -> QPushButton:
    b = QPushButton(label)
    b.setProperty("studioRole", role)
    b.style().unpolish(b)
    b.style().polish(b)
    return b


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {C['line']}; border: none; max-height: 1px;")
    return line


def _section(title: str) -> QLabel:
    lbl = QLabel(title.upper())
    lbl.setProperty("class", "StudioSection")
    lbl.setStyleSheet(
        f"color: {C['meta']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; background: transparent;"
    )
    return lbl


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("class", "StudioFieldLabel")
    lbl.setStyleSheet(f"color: {C['label']}; font-size: 12px; font-weight: 500; background: transparent;")
    return lbl


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {C['meta']}; font-size: 11px; font-weight: 500; background: transparent;")
    return lbl


def _mono(text: str, *, selectable: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setProperty("class", "StudioMono")
    lbl.setStyleSheet(
        f"color: {C['mono']}; font-family: 'JetBrains Mono', monospace; "
        f"font-size: 12px; background: transparent;"
    )
    return lbl


def _field_block(label: str, widget: QWidget, *, hint: str = "") -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(_field_label(label))
    lay.addWidget(widget)
    if hint:
        lay.addWidget(_hint(hint))
    return w


class StudioNote(QFrame):
    """Muted inset note — no colored frame, left hairline only."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {C['raised']}; border: none; border-radius: {C['radius_sm']}px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        bar = QFrame()
        bar.setFixedWidth(2)
        bar.setStyleSheet(f"background: {C['line_strong']}; border: none; border-radius: 1px;")
        lay.addWidget(bar)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {C['label']}; font-size: 12px; font-weight: 500; background: transparent;")
        lay.addWidget(body, stretch=1)


class _DragHeader(QWidget):
    def __init__(self, dialog: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dialog = dialog
        self._origin: QPoint | None = None
        self._win: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._win = self._dialog.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._origin and self._win and event.buttons() & Qt.MouseButton.LeftButton:
            self._dialog.move(self._win + event.globalPosition().toPoint() - self._origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._origin = None
        self._win = None
        super().mouseReleaseEvent(event)


class StudioDialog(MonosDialog):
    """Minimal dialog — flat surface, hairline dividers, no chromatic sections."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.set_dialog_border_overlay_enabled(False)
        self.setModal(True)
        self.setObjectName("StudioDialogRoot")
        self.setMinimumWidth(440)

        self._body: QVBoxLayout

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = _DragHeader(self)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 16, 12, 12)
        hl.setSpacing(0)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"color: {C['text']}; font-size: 14px; font-weight: 600; background: transparent;")
        col.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setWordWrap(True)
            s.setStyleSheet(f"color: {C['meta']}; font-size: 12px; font-weight: 500; background: transparent;")
            col.addWidget(s)
        hl.addLayout(col, stretch=1)
        close = QPushButton()
        close.setObjectName("StudioClose")
        close.setIcon(lucide_icon("x", size=15, color_hex=C["label"]))
        close.clicked.connect(self.reject)
        hl.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)
        root.addWidget(_divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")

        body_w = QWidget()
        body_w.setStyleSheet("background: transparent;")
        self._body = QVBoxLayout(body_w)
        self._body.setContentsMargins(20, 16, 20, 8)
        self._body.setSpacing(16)
        scroll.setWidget(body_w)
        root.addWidget(scroll, stretch=1)

        root.addWidget(_divider())
        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 12, 20, 14)
        fl.setSpacing(8)
        self._foot_hint = QLabel("")
        self._foot_hint.setStyleSheet(f"color: {C['meta']}; font-size: 11px; background: transparent;")
        fl.addWidget(self._foot_hint)
        fl.addStretch()
        self._foot_btns = QHBoxLayout()
        self._foot_btns.setSpacing(8)
        fl.addLayout(self._foot_btns)
        root.addWidget(foot)

    def add(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_section(self, title: str) -> None:
        self._body.addWidget(_section(title))

    def add_divider(self) -> None:
        self._body.addWidget(_divider())

    def set_hint(self, text: str) -> None:
        self._foot_hint.setText(text)

    def add_button(self, label: str, role: str, *, slot=None) -> QPushButton:
        b = _btn(label, role)
        if slot:
            b.clicked.connect(slot)
        self._foot_btns.addWidget(b)
        return b

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QBrush, QColor, QPainter, QPen

        r = self.rect()
        if r.isEmpty():
            return super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inset = QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(C["dialog"])))
        p.drawRoundedRect(inset, C["radius"], C["radius"])
        pen = QPen(QColor(C["line_strong"]))
        pen.setWidthF(1.0)
        pen.setCosmetic(True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawRoundedRect(inset, C["radius"], C["radius"])
        p.end()
        super(MonosDialog, self).paintEvent(event)


# ---------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------


class RenameDemo(StudioDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(
            parent=parent,
            title="Rename Asset",
            subtitle="Updates folder name and matching work files.",
        )
        self.setMinimumWidth(480)
        self.add(StudioNote(
            "Work files with the old prefix are renamed automatically. "
            "External DCC references are not updated."
        ))
        self.add_section("Identity")
        self.add(_mono("char_aya_prototype"))
        self._name = QLineEdit("char_aya_prototype_v2")
        self.add(_field_block("New name", self._name, hint="Character prefix is applied automatically."))
        row = QHBoxLayout()
        row.addWidget(_hint("Preview"))
        row.addStretch()
        self._prev = _mono("char_aya_prototype_v2")
        row.addWidget(self._prev)
        wrap = QWidget()
        wrap.setLayout(row)
        self.add(wrap)
        self._ok = _btn("Rename", "primary")
        self._ok.clicked.connect(self.accept)
        self._ok.setEnabled(False)
        self.add_button("Cancel", "secondary", slot=self.reject)
        self._foot_btns.addWidget(self._ok)
        self.set_hint("Applies immediately")
        self._name.textChanged.connect(self._sync)

    def _sync(self, t: str) -> None:
        n = t.strip()
        self._prev.setText(n or "—")
        self._ok.setEnabled(bool(n) and n != "char_aya_prototype")

    def showEvent(self, e: QShowEvent) -> None:  # noqa: N802
        super().showEvent(e)
        self._name.setFocus()
        self._name.selectAll()


class ImportDemo(StudioDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(
            parent=parent,
            title="Import Reference",
            subtitle="Choose how files land in the project.",
        )
        self.setMinimumWidth(500)
        self.add_section("Destination")
        self._dept = QComboBox()
        self._dept.addItems(["Concept", "Texture", "Sculpt", "Reference"])
        self._sub = QLineEdit()
        self._sub.setPlaceholderText("Optional subfolder")
        self.add(_field_block("Department", self._dept))
        self.add(_field_block("Subfolder", self._sub, hint="Leave empty for department root."))
        self.add_divider()
        self.add_section("Options")
        self._c1 = QCheckBox("Copy files (keep source intact)")
        self._c1.setChecked(True)
        self._c2 = QCheckBox("Auto-increment version if name exists")
        self._c2.setChecked(True)
        self._c3 = QCheckBox("Generate thumbnail preview")
        self.add(self._c1)
        self.add(self._c2)
        self.add(self._c3)
        self.add_divider()
        self.add_section("Source")
        self.add(_hint("3 files · 148.2 MB"))
        self.add(_mono("concept_ref_01.psd, concept_ref_02.png, notes.txt"))
        self.add_button("Browse…", "ghost")
        self.add_button("Cancel", "secondary", slot=self.reject)
        imp = _btn("Import", "primary")
        imp.clicked.connect(self.accept)
        self._foot_btns.addWidget(imp)
        self.set_hint("Copied to publish folder")


class DeleteDemo(StudioDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(
            parent=parent,
            title="Move to Trash",
            subtitle="Removed from pipeline view. Restorable from Project Trash.",
        )
        self.setMinimumWidth(500)
        self.add_section("Target")
        self.add(_mono("D:/Projects/Demo/assets/character/char_aya_prototype"))
        self.add(_hint("12 work files · 3 publish versions"))
        self.add_divider()
        self._confirm = QLineEdit()
        self._confirm.setPlaceholderText("Type asset name to confirm")
        self.add(_field_block("Confirmation", self._confirm, hint="Type char_aya_prototype to enable."))
        del_btn = _btn("Move to Trash", "danger")
        del_btn.setEnabled(False)
        del_btn.clicked.connect(self.accept)
        self.add_button("Cancel", "secondary", slot=self.reject)
        self._foot_btns.addWidget(del_btn)
        self._del = del_btn
        self._confirm.textChanged.connect(
            lambda t: self._del.setEnabled(t.strip() == "char_aya_prototype")
        )


class AlertDemo(StudioDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent, title="Unsaved changes")
        self.setMaximumWidth(400)
        self.add(_hint("Your note has unsaved edits. Save before switching tasks?"))
        self.add_button("Don't Save", "ghost", slot=self.reject)
        self.add_button("Cancel", "secondary", slot=self.reject)
        save = _btn("Save", "primary")
        save.clicked.connect(self.accept)
        self._foot_btns.addWidget(save)


class NewProjectDemo(StudioDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(
            parent=parent,
            title="New Project",
            subtitle="Project ID is generated automatically and cannot be changed later.",
        )
        self._name = QLineEdit("Forest Spirit")
        self._id = QLineEdit("260721_forest_spirit")
        self._id.setReadOnly(True)
        self._id.setProperty("mono", True)
        self.add_section("Details")
        self.add(_field_block("Project name", self._name))
        self.add(_field_block("Project ID", self._id))
        self.add_divider()
        self.add_section("Workspace")
        self.add(_mono("D:/Dropbox/MonoStudio/Workspace"))
        self.add_button("Cancel", "secondary", slot=self.reject)
        create = _btn("Create", "primary")
        create.clicked.connect(self.accept)
        self._foot_btns.addWidget(create)
        self._name.textChanged.connect(self._sync_id)

    def _sync_id(self, t: str) -> None:
        slug = t.strip().lower().replace(" ", "_") or "untitled"
        self._id.setText(f"260721_{slug}")


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


class StudioLauncher(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("StudioLauncher")
        self.setWindowTitle("Dialog UI — Studio Minimal")
        self.resize(520, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        root.setSpacing(16)

        t = QLabel("Studio Minimal")
        t.setObjectName("StudioLauncherTitle")
        sub = QLabel(
            "Monochrome · typography hierarchy · dividers instead of colored boxes. "
            "Alternative to test_dialog_ui_v2.py."
        )
        sub.setObjectName("StudioLauncherSub")
        sub.setWordWrap(True)
        root.addWidget(t)
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(6)

        demos = [
            ("Rename Asset", RenameDemo),
            ("Import Reference", ImportDemo),
            ("Move to Trash", DeleteDemo),
            ("Unsaved Alert", AlertDemo),
            ("New Project", NewProjectDemo),
        ]
        for name, cls in demos:
            btn = QPushButton(name)
            btn.setProperty("class", "StudioLauncherItem")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, c=cls, n=name: self._open(c, n))
            bl.addWidget(btn)
        bl.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        foot = QLabel("python scripts/test_dialog_ui_studio.py")
        foot.setStyleSheet(f"color: {C['meta']}; font-size: 11px;")
        root.addWidget(foot)

    def _open(self, cls: type[StudioDialog], name: str) -> None:
        code = cls(parent=self).exec()
        print(f"{name}: {'ok' if code == QDialog.DialogCode.Accepted else 'cancel'}", flush=True)


def main() -> int:
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    app.setStyleSheet(app.styleSheet() + "\n" + STUDIO_QSS)
    w = StudioLauncher()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
