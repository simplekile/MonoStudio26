from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QVBoxLayout,
)

from monostudio.core.pipeline_types_and_presets import TypeDef, load_department_vocabulary, load_pipeline_types_and_presets

_MUTED_HELPER_STYLE = "color: #A9ABB0; font-size: 11px;"


def _debug_dialogs_enabled() -> bool:
    # Debug is OFF by default. Enable via env:
    #   MONOSTUDIO26_DEBUG_DIALOGS=1
    return os.environ.get("MONOSTUDIO26_DEBUG_DIALOGS", "").strip() == "1"


def _debug_dialog(tag: str, **fields: object) -> None:
    # No-op unless debug is explicitly enabled.
    if not _debug_dialogs_enabled():
        return
    parts = " ".join(f"{k}={v!r}" for k, v in fields.items())
    print(f"[{tag}] {parts}")


@dataclass(frozen=True)
class _DepartmentTemplates:
    asset_types: dict[str, list[str]]
    shot_types: dict[str, list[str]]
    asset_prefixes: dict[str, str]
    shot_prefixes: dict[str, str]
    asset_paddings: dict[str, int]
    shot_paddings: dict[str, int]


def _safe_read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_shot_type_id(type_id: str) -> bool:
    # Convention (deterministic):
    # - Shot-capable types must use id == "shot" or start with "shot_".
    return type_id == "shot" or type_id.startswith("shot_")


def _field_block_with_preview(label_text: str, field: QWidget, preview: QLabel, helper_text: str) -> QWidget:
    """
    Field grouping:
    - Label
    - Input field
    - Preview (below field)
    - Helper text
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
    layout.addSpacing(7)
    layout.addWidget(field)
    layout.addSpacing(5)
    layout.addWidget(preview)
    layout.addSpacing(5)
    layout.addWidget(helper)
    return block


def _field_block(label_text: str, field: QWidget, helper_text: str) -> QWidget:
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


def _name_block_with_prefix_preview(label_text: str, field: QLineEdit, preview: QLabel, helper_text: str) -> QWidget:
    """
    Name field block with an optional prefix preview line BELOW the input.
    Preview is read-only and hidden when not applicable.
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
    layout.addSpacing(5)  # input -> preview (4–6px)
    layout.addWidget(preview)
    layout.addSpacing(5)  # preview -> helper (4–6px)
    layout.addWidget(helper)
    return block


class CreateAssetDialog(QDialog):
    """
    Unified Type + Department Preset System (project settings driven).

    Create Asset (UPDATED):
    - Remove Type selector
    - Remove department checkboxes
    - Add ONE selector: Type / Preset (flattened)
    - Departments are read-only preview from selected preset
    - Final asset folder name is derived from type.short_name
    """

    def __init__(self, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Asset")
        self.setModal(True)

        self._project_root = project_root
        self._types: dict[str, TypeDef] = load_pipeline_types_and_presets().types
        self._dept_vocab: set[str] = set(load_department_vocabulary())

        self._selected_type_id: str | None = None

        # Type selector (Type itself is the preset)
        self._type_preview = QLabel("")
        self._type_preview.setVisible(False)
        self._type_preview.setWordWrap(True)
        self._type_preview.setStyleSheet(_MUTED_HELPER_STYLE)

        self._type_button = QToolButton()
        self._type_button.setPopupMode(QToolButton.InstantPopup)
        self._type_button.setText("Select Type…")
        self._type_menu = QMenu(self._type_button)
        self._type_button.setMenu(self._type_menu)
        self._build_type_menu()

        # Asset name (base)
        self._asset_name = QLineEdit()
        self._asset_name.setPlaceholderText("e.g. aya")
        self._asset_name.textChanged.connect(self._update_ok_enabled)
        self._asset_name.textChanged.connect(self._update_final_name_preview)

        # Final folder name preview
        self._final_name_preview = QLabel("")
        self._final_name_preview.setVisible(False)
        self._final_name_preview.setWordWrap(True)
        self._final_name_preview.setStyleSheet(_MUTED_HELPER_STYLE)

        self._subfolders = QCheckBox("Create work/ and publish/ inside departments")
        self._subfolders.setChecked(True)  # user request: default ON

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setText("Create Asset")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)  # >= 12px all sides
        layout.setSpacing(0)

        layout.addWidget(
            _field_block_with_preview(
                "Type",
                self._type_button,
                self._type_preview,
                "Type is defined in Pipeline Settings.",
            )
        )

        layout.addSpacing(14)
        layout.addWidget(
            _field_block_with_preview(
                "Asset Name",
                self._asset_name,
                self._final_name_preview,
                "Final folder name derives from Type short_name + '_' + name.",
            )
        )

        layout.addSpacing(12)
        layout.addWidget(self._subfolders)

        layout.addSpacing(12)  # top margin above button row (>= 12px)
        layout.addWidget(self._buttons)

        self._update_ok_enabled()
        self._update_type_preview()
        self._update_final_name_preview()

    def asset_type(self) -> str:
        return (self._selected_type_id or "").strip()

    def asset_name(self) -> str:
        base = self._asset_name.text().strip()
        if not base:
            return ""
        t = self._types.get(self._selected_type_id or "")
        if t is None:
            return base
        short = t.short_name.strip()
        if not short:
            return base
        prefix = short if short.endswith("_") else f"{short}_"
        return base if base.startswith(prefix) else f"{prefix}{base}"

    def selected_departments(self) -> list[str]:
        t = self._types.get(self._selected_type_id or "")
        if t is None:
            return []
        raw = t.departments
        if not raw:
            return []
        if self._dept_vocab:
            return [d for d in raw if d in self._dept_vocab]
        return list(raw)

    def create_subfolders(self) -> bool:
        return bool(self._subfolders.isChecked())

    def _build_type_menu(self) -> None:
        self._type_menu.clear()
        self._selected_type_id = None
        allowed = [(type_id, t) for type_id, t in self._types.items() if not _is_shot_type_id(type_id)]
        allowed.sort(key=lambda kv: kv[1].name.lower())

        self._type_button.setEnabled(bool(allowed))
        if not allowed:
            self._type_button.setText("No asset types")
            self._type_preview.setVisible(False)
            return
        self._type_button.setText("Select Type…")
        for type_id, t in allowed:
            act = QAction(t.name, self._type_menu)
            act.triggered.connect(lambda checked=False, tid=type_id: self._set_type(tid))
            self._type_menu.addAction(act)

    def _set_type(self, type_id: str) -> None:
        self._selected_type_id = type_id
        t = self._types.get(type_id)
        if t is None:
            return
        self._type_button.setText(t.name)
        self._update_type_preview()
        self._update_final_name_preview()
        self._update_ok_enabled()

    def _update_type_preview(self) -> None:
        if not self._selected_type_id:
            self._type_preview.setVisible(False)
            return
        depts = self.selected_departments()
        if not depts:
            self._type_preview.setText("Departments:")
            self._type_preview.setVisible(True)
            return
        self._type_preview.setText("Departments: " + " / ".join(depts))
        self._type_preview.setVisible(True)

    def _update_final_name_preview(self) -> None:
        final_name = self.asset_name()
        if not final_name:
            self._final_name_preview.setVisible(False)
            self._debug_name_fields()
            return
        self._final_name_preview.setText(f"Final folder name: {final_name}")
        self._final_name_preview.setVisible(True)
        self._debug_name_fields()

    def _update_ok_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is None:
            return
        ok.setEnabled(bool(self._selected_type_id and self._asset_name.text().strip()))

    def _debug_name_fields(self) -> None:
        t = self._types.get(self._selected_type_id or "")
        _debug_dialog(
            "CreateAssetDialog",
            type_id=self._selected_type_id,
            type_name=(t.name if t else None),
            short_name=(t.short_name if t else None),
            name_input=self._asset_name.text(),
            final_name=self.asset_name(),
        )


class CreateShotDialog(QDialog):
    """
    Unified Type + Department Preset System (project settings driven).

    Create Shot (UPDATED):
    - Type selector
    - Shot Number: [ <type.short_name> ][ <number> ][ suffix ]
    - Preset selector depends on Type (optional)
    - Departments are read-only preview from selected preset
    """

    def __init__(self, project_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Shot")
        self.setModal(True)

        self._project_root = project_root
        self._types: dict[str, TypeDef] = load_pipeline_types_and_presets().types
        self._dept_vocab: set[str] = set(load_department_vocabulary())

        self._selected_type_id: str | None = None
        self._padding: int = 3  # fixed width (v1)

        # Type selector (project-defined).
        self._type_button = QToolButton()
        self._type_button.setPopupMode(QToolButton.InstantPopup)
        self._type_button.setText("Select Type…")
        self._type_menu = QMenu(self._type_button)
        self._type_button.setMenu(self._type_menu)
        self._build_type_menu()

        # New input model: fixed prefix + numeric-only field.
        self._shot_number = QLineEdit()
        self._shot_number.setPlaceholderText("001")
        self._shot_number.textChanged.connect(self._update_ok_enabled)
        self._shot_number.textChanged.connect(self._update_final_name_preview)

        # Optional suffix (inbetween-safe, explicit).
        self._shot_suffix = QLineEdit()
        self._shot_suffix.setPlaceholderText("suffix")
        self._shot_suffix.textChanged.connect(self._update_ok_enabled)
        self._shot_suffix.textChanged.connect(self._update_final_name_preview)

        self._final_name_preview = QLabel("")
        self._final_name_preview.setVisible(False)
        self._final_name_preview.setWordWrap(True)
        self._final_name_preview.setStyleSheet(_MUTED_HELPER_STYLE)

        # Type departments preview (read-only)
        self._type_preview = QLabel("")
        self._type_preview.setVisible(False)
        self._type_preview.setWordWrap(True)
        self._type_preview.setStyleSheet(_MUTED_HELPER_STYLE)

        self._subfolders = QCheckBox("Create work/ and publish/ inside departments")
        self._subfolders.setChecked(True)  # user request: default ON

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setText("Create Shot")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)  # >= 12px all sides
        layout.setSpacing(0)

        layout.addWidget(
            _field_block(
                "Type",
                self._type_button,
                "Types are defined in Project → Types & Presets.",
            )
        )
        layout.addSpacing(8)
        layout.addWidget(self._type_preview)
        layout.addSpacing(14)

        # Shot Number block (composed input: [prefix][number][suffix]).
        label = QLabel("Shot Number")
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(label)
        layout.addSpacing(7)  # label -> input (6–8px)

        name_row = QWidget()
        name_row_layout = QHBoxLayout(name_row)
        name_row_layout.setContentsMargins(0, 0, 0, 0)
        name_row_layout.setSpacing(10)

        self._prefix_label = QLabel("")
        self._prefix_label.setAlignment(Qt.AlignCenter)
        self._prefix_label.setStyleSheet(
            "padding: 6px 10px; border: 1px solid #3A3D41; border-radius: 6px; background: #2B2D30;"
        )
        self._prefix_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self._prefix_label.setVisible(False)
        name_row_layout.addWidget(self._prefix_label, 0)

        # Numeric-only input (no rewriting during typing).
        # Keep it simple: digits only, empty allowed while editing.
        from PySide6.QtCore import QRegularExpression
        from PySide6.QtGui import QRegularExpressionValidator

        self._shot_number.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d*"), self._shot_number))
        name_row_layout.addWidget(self._shot_number, 1)

        # Suffix: [a-z0-9_]* (no spaces/dots/hyphens), empty allowed.
        self._shot_suffix.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[a-z0-9_]*"), self._shot_suffix)
        )
        name_row_layout.addWidget(self._shot_suffix, 1)

        layout.addWidget(name_row)
        layout.addSpacing(5)  # input -> preview (4–6px)
        layout.addWidget(self._final_name_preview)

        layout.addSpacing(14)

        layout.addSpacing(12)
        layout.addWidget(self._subfolders)

        layout.addSpacing(12)  # top margin above button row (>= 12px)
        layout.addWidget(self._buttons)

        self._update_final_name_preview()
        self._update_ok_enabled()

    def shot_name(self) -> str:
        # Must match the previewed final folder name exactly.
        t = self._types.get(self._selected_type_id or "")
        short = t.short_name.strip() if t else ""
        if not short:
            return ""
        num = self._shot_number.text().strip()
        if not num:
            return ""
        # Zero-pad based on numeric value (str(int(num))).
        try:
            num = str(int(num)).zfill(self._padding)
        except ValueError:
            return ""
        suffix = self._shot_suffix.text().strip()
        return f"{short}{num}{suffix}"

    def selected_departments(self) -> list[str]:
        t = self._types.get(self._selected_type_id or "")
        if t is None:
            return []
        raw = t.departments
        if not raw:
            return []
        if self._dept_vocab:
            return [d for d in raw if d in self._dept_vocab]
        return list(raw)

    def create_subfolders(self) -> bool:
        return bool(self._subfolders.isChecked())

    def _build_type_menu(self) -> None:
        self._type_menu.clear()
        self._selected_type_id = None
        self._type_button.setEnabled(bool(self._types))
        if not self._types:
            self._type_button.setText("No types")
            return
        self._type_button.setText("Select Type…")
        for type_id, t in sorted(self._types.items(), key=lambda kv: kv[1].name.lower()):
            if not _is_shot_type_id(type_id):
                continue
            act = QAction(t.name, self._type_menu)
            act.triggered.connect(lambda checked=False, tid=type_id: self._set_type(tid))
            self._type_menu.addAction(act)

    def _set_type(self, type_id: str) -> None:
        self._selected_type_id = type_id
        t = self._types.get(type_id)
        if t is None:
            return
        self._type_button.setText(t.name)
        self._prefix_label.setText(t.short_name)
        self._prefix_label.setVisible(True)
        self._update_type_preview()
        self._update_final_name_preview()
        self._update_ok_enabled()

    def _update_type_preview(self) -> None:
        if not self._selected_type_id:
            self._type_preview.setVisible(False)
            return
        depts = self.selected_departments()
        if not depts:
            self._type_preview.setText("Departments:")
            self._type_preview.setVisible(True)
            return
        self._type_preview.setText("Departments: " + " / ".join(depts))
        self._type_preview.setVisible(True)

    def _update_ok_enabled(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.Ok)
        if ok is None:
            return
        ok.setEnabled(bool(self._selected_type_id) and bool(self.shot_name()))

    def _update_final_name_preview(self) -> None:
        # Final folder name preview (read-only, computed; no side effects).
        final_name = self.shot_name()
        if not final_name:
            self._final_name_preview.setVisible(False)
            self._debug_name_fields()
            return
        self._final_name_preview.setText(f"Final folder name: {final_name}")
        self._final_name_preview.setVisible(True)
        self._debug_name_fields()

    def _debug_name_fields(self) -> None:
        t = self._types.get(self._selected_type_id or "")
        _debug_dialog(
            "CreateShotDialog",
            type_id=self._selected_type_id,
            type_name=(t.name if t else None),
            short_name=(t.short_name if t else None),
            padding=self._padding,
            number_input=self._shot_number.text(),
            suffix_input=self._shot_suffix.text(),
            preview_visible=self._final_name_preview.isVisible(),
            preview_text=self._final_name_preview.text(),
            final_name=self.shot_name(),
        )

