from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QTextOption
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QLineEdit

from monostudio.core.pipeline_types_and_presets import (
    TypeDef,
    filter_departments_for_entity_scope,
    load_department_vocabulary,
    load_pipeline_types_and_presets_for_project,
)
from monostudio.core.structure_registry import StructureRegistry
from monostudio.core.type_registry import TypeRegistry
from monostudio.ui_qt.style import MonosDialog


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


def _global_create_work_publish_subfolders_default() -> bool:
    """Read global default from Settings (General → Behavior). Applied when opening Create Asset/Shot dialogs."""
    try:
        s = QSettings("MonoStudio26", "MonoStudio26")
        v = s.value("pipeline/create_work_publish_subfolders", True)
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("true", "1")
    except Exception:
        return True


def _is_shot_type_id(type_id: str) -> bool:
    # Convention (deterministic):
    # - Shot-capable types must use id == "shot" or start with "shot_".
    return type_id == "shot" or type_id.startswith("shot_")


_BATCH_CREATE_DIALOG_WIDTH = 420
_BATCH_INPUT_LINE_COUNT = 10
_BATCH_INPUT_VERTICAL_PAD_PX = 12  # QSS padding 6px top + 6px bottom
_BATCH_INPUT_BORDER_PX = 2  # QSS border 1px top + bottom


class _BatchNamesInput(QPlainTextEdit):
    """Multi-line batch input: fixed width and fixed height (10 lines)."""

    def __init__(self, parent=None, *, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("DialogBatchNamesInput")
        self.setPlaceholderText(placeholder)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTabChangesFocus(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        input_w = _BATCH_CREATE_DIALOG_WIDTH - 24  # dialog side margins (12 + 12)
        self.setFixedWidth(input_w)
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.document().setDefaultTextOption(opt)
        self.document().setDocumentMargin(0)
        line_h = self.fontMetrics().lineSpacing()
        chrome = _BATCH_INPUT_VERTICAL_PAD_PX + _BATCH_INPUT_BORDER_PX
        self.setFixedHeight(_BATCH_INPUT_LINE_COUNT * line_h + chrome)

    def text(self) -> str:
        return self.toPlainText()


def parse_comma_separated_tokens(text: str) -> list[str]:
    """Split comma- or newline-separated user input into non-empty trimmed tokens."""
    tokens: list[str] = []
    for line in text.splitlines():
        for part in line.split(","):
            part = part.strip()
            if part:
                tokens.append(part)
    if not tokens and text.strip():
        tokens.append(text.strip())
    return tokens


def final_asset_name_from_base(base: str, type_def: TypeDef | None) -> str:
    """Apply type short_name prefix to a single asset base name."""
    base = base.strip()
    if not base:
        return ""
    if type_def is None:
        return base
    short = type_def.short_name.strip()
    if not short:
        return base
    prefix = short if short.endswith("_") else f"{short}_"
    return base if base.startswith(prefix) else f"{prefix}{base}"


def final_shot_name_from_token(token: str, type_def: TypeDef | None, *, padding: int = 3) -> str:
    """
    Build final shot folder name from one batch token.
    Token format: digits with optional suffix (e.g. 10, 010, 10a).
    """
    token = token.strip()
    if not token or type_def is None:
        return ""
    short = type_def.short_name.strip()
    if not short:
        return ""
    m = re.fullmatch(r"(\d+)([a-z0-9_]*)", token)
    if not m:
        return ""
    try:
        num = str(int(m.group(1))).zfill(padding)
    except ValueError:
        return ""
    return f"{short}{num}{m.group(2)}"


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
    helper.setObjectName("DialogHelper")
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
    helper.setObjectName("DialogHelper")
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
    helper.setObjectName("DialogHelper")
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


class CreateAssetDialog(MonosDialog):
    """
    Unified Type + Department Preset System (project settings driven).

    Create Asset (UPDATED):
    - Remove Type selector
    - Remove department checkboxes
    - Add ONE selector: Type / Preset (flattened)
    - Departments are read-only preview from selected preset
    - Final asset folder name is derived from type.short_name
    """

    def __init__(self, project_root: Path, parent=None, *, initial_type_id: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Asset")
        self.setModal(True)

        self._project_root = project_root
        self._initial_type_id = (initial_type_id or "").strip() or None
        self._types: dict[str, TypeDef] = load_pipeline_types_and_presets_for_project(project_root).types
        self._dept_vocab: set[str] = set(load_department_vocabulary())

        self._selected_type_id: str | None = None

        # Type selector (Type itself is the preset)
        self._type_preview = QLabel("")
        self._type_preview.setVisible(False)
        self._type_preview.setWordWrap(True)
        self._type_preview.setObjectName("DialogHelper")

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
        self._final_name_preview.setObjectName("DialogHelper")

        # Asset already exists warning (shown when target path exists)
        self._exists_warning = QLabel("This asset already exists.")
        self._exists_warning.setVisible(False)
        self._exists_warning.setWordWrap(True)
        self._exists_warning.setObjectName("DialogWarning")

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        self._ok_btn = QPushButton("Create Asset")
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        button_row_l.addWidget(self._ok_btn)
        button_row_l.addWidget(cancel_btn)
        button_row_l.addStretch(1)

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
        layout.addWidget(self._exists_warning)

        layout.addSpacing(12)  # top margin above button row (>= 12px)
        layout.addWidget(button_row)

        self._update_ok_enabled()
        self._update_type_preview()
        self._update_final_name_preview()
        # Prefer filter type if valid asset type; else fallback to first in list
        type_to_set = None
        if self._initial_type_id and self._initial_type_id in self._types and not _is_shot_type_id(self._initial_type_id):
            type_to_set = self._initial_type_id
        if type_to_set is None:
            type_to_set = self._get_first_asset_type_id()
        if type_to_set is not None:
            self._set_type(type_to_set)

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
            raw = [d for d in raw if d in self._dept_vocab]
        return filter_departments_for_entity_scope(list(raw), "asset")

    def create_subfolders(self) -> bool:
        return _global_create_work_publish_subfolders_default()

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

    def _get_first_asset_type_id(self) -> str | None:
        allowed = [(type_id, t) for type_id, t in self._types.items() if not _is_shot_type_id(type_id)]
        allowed.sort(key=lambda kv: kv[1].name.lower())
        return allowed[0][0] if allowed else None

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

    def _get_asset_target_path(self) -> Path | None:
        """Path that would be used for the asset folder; None if type/name not ready."""
        asset_name = self.asset_name()
        if not asset_name or not self._selected_type_id:
            return None
        try:
            struct_reg = StructureRegistry.for_project(self._project_root)
            type_reg = TypeRegistry.for_project(self._project_root)
            type_folder = type_reg.get_type_folder(self._selected_type_id)
            assets_folder = struct_reg.get_folder("assets")
            return self._project_root / assets_folder / type_folder / asset_name
        except Exception:
            return None

    def _asset_exists(self) -> bool:
        path = self._get_asset_target_path()
        return path is not None and path.exists()

    def _update_final_name_preview(self) -> None:
        final_name = self.asset_name()
        if not final_name:
            self._final_name_preview.setVisible(False)
            self._debug_name_fields()
            self._update_exists_warning()
            return
        self._final_name_preview.setText(f"Final folder name: {final_name}")
        self._final_name_preview.setVisible(True)
        self._debug_name_fields()
        self._update_exists_warning()

    def _update_exists_warning(self) -> None:
        self._exists_warning.setVisible(self._asset_exists())

    def _update_ok_enabled(self) -> None:
        can_create = bool(self._selected_type_id and self._asset_name.text().strip()) and not self._asset_exists()
        self._ok_btn.setEnabled(can_create)

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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._asset_name.setFocus()


class CreateShotDialog(MonosDialog):
    """
    Unified Type + Department Preset System (project settings driven).

    Create Shot (UPDATED):
    - Type selector
    - Shot Number: [ <type.short_name> ][ <number> ][ suffix ]
    - Preset selector depends on Type (optional)
    - Departments are read-only preview from selected preset
    """

    def __init__(self, project_root: Path, parent=None, *, initial_type_id: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Shot")
        self.setModal(True)

        self._project_root = project_root
        self._initial_type_id = (initial_type_id or "").strip() or None
        self._types: dict[str, TypeDef] = load_pipeline_types_and_presets_for_project(project_root).types
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
        self._final_name_preview.setObjectName("DialogHelper")

        # Type departments preview (read-only)
        self._type_preview = QLabel("")
        self._type_preview.setVisible(False)
        self._type_preview.setWordWrap(True)
        self._type_preview.setObjectName("DialogHelper")

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        self._ok_btn = QPushButton("Create Shot")
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        button_row_l.addWidget(self._ok_btn)
        button_row_l.addWidget(cancel_btn)
        button_row_l.addStretch(1)

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
        self._prefix_label.setObjectName("DialogPrefixChip")
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

        layout.addSpacing(12)  # top margin above button row (>= 12px)
        layout.addWidget(button_row)

        self._update_final_name_preview()
        self._update_ok_enabled()
        # Prefer filter type if valid shot type; else fallback to first in list
        type_to_set = None
        if self._initial_type_id and self._initial_type_id in self._types and _is_shot_type_id(self._initial_type_id):
            type_to_set = self._initial_type_id
        if type_to_set is None:
            type_to_set = self._get_first_shot_type_id()
        if type_to_set is not None:
            self._set_type(type_to_set)

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
        return _global_create_work_publish_subfolders_default()

    def _build_type_menu(self) -> None:
        self._type_menu.clear()
        self._selected_type_id = None
        allowed = [
            (type_id, t)
            for type_id, t in sorted(self._types.items(), key=lambda kv: kv[1].name.lower())
            if _is_shot_type_id(type_id)
        ]
        self._type_button.setEnabled(bool(allowed))
        if not allowed:
            self._type_button.setText("No types")
            return
        self._type_button.setText("Select Type…")
        for type_id, t in allowed:
            act = QAction(t.name, self._type_menu)
            act.triggered.connect(lambda checked=False, tid=type_id: self._set_type(tid))
            self._type_menu.addAction(act)

    def _get_first_shot_type_id(self) -> str | None:
        allowed = [
            (type_id, t)
            for type_id, t in sorted(self._types.items(), key=lambda kv: kv[1].name.lower())
            if _is_shot_type_id(type_id)
        ]
        return allowed[0][0] if allowed else None

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
        self._ok_btn.setEnabled(bool(self._selected_type_id) and bool(self.shot_name()))

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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._shot_number.setFocus()


class BatchCreateAssetDialog(MonosDialog):
    """Create multiple assets from comma-separated base names."""

    def __init__(self, project_root: Path, parent=None, *, initial_type_id: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Batch Create Assets")
        self.setModal(True)

        self._project_root = project_root
        self._initial_type_id = (initial_type_id or "").strip() or None
        self._types: dict[str, TypeDef] = load_pipeline_types_and_presets_for_project(project_root).types
        self._dept_vocab: set[str] = set(load_department_vocabulary())
        self._selected_type_id: str | None = None

        self._type_preview = QLabel("")
        self._type_preview.setVisible(False)
        self._type_preview.setWordWrap(True)
        self._type_preview.setObjectName("DialogHelper")

        self._type_button = QToolButton()
        self._type_button.setPopupMode(QToolButton.InstantPopup)
        self._type_button.setText("Select Type…")
        self._type_menu = QMenu(self._type_button)
        self._type_button.setMenu(self._type_menu)
        self._build_type_menu()

        self._names_input = _BatchNamesInput(placeholder="e.g. aya, bob, zen")
        self._names_input.textChanged.connect(self._update_preview_and_ok)

        self._preview = QLabel("")
        self._preview.setVisible(False)
        self._preview.setWordWrap(True)
        self._preview.setObjectName("DialogHelper")

        self._warning = QLabel("")
        self._warning.setVisible(False)
        self._warning.setWordWrap(True)
        self._warning.setObjectName("DialogWarning")

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        self._ok_btn = QPushButton("Create Assets")
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        button_row_l.addWidget(self._ok_btn)
        button_row_l.addWidget(cancel_btn)
        button_row_l.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
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
                "Asset Names",
                self._names_input,
                self._preview,
                "One name per line or comma-separated. Type prefix is applied to each.",
            )
        )
        layout.addWidget(self._warning)
        layout.addSpacing(12)
        layout.addWidget(button_row)

        self.setFixedWidth(_BATCH_CREATE_DIALOG_WIDTH)

        type_to_set = None
        if self._initial_type_id and self._initial_type_id in self._types and not _is_shot_type_id(self._initial_type_id):
            type_to_set = self._initial_type_id
        if type_to_set is None:
            type_to_set = self._get_first_asset_type_id()
        if type_to_set is not None:
            self._set_type(type_to_set)
        self._update_preview_and_ok()

    def asset_type(self) -> str:
        return (self._selected_type_id or "").strip()

    def asset_names(self) -> list[str]:
        type_def = self._types.get(self._selected_type_id or "")
        names: list[str] = []
        seen: set[str] = set()
        for token in parse_comma_separated_tokens(self._names_input.text()):
            final = final_asset_name_from_base(token, type_def)
            if not final or final in seen:
                continue
            seen.add(final)
            names.append(final)
        return names

    def selected_departments(self) -> list[str]:
        t = self._types.get(self._selected_type_id or "")
        if t is None:
            return []
        raw = t.departments
        if not raw:
            return []
        if self._dept_vocab:
            raw = [d for d in raw if d in self._dept_vocab]
        return filter_departments_for_entity_scope(list(raw), "asset")

    def create_subfolders(self) -> bool:
        return _global_create_work_publish_subfolders_default()

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

    def _get_first_asset_type_id(self) -> str | None:
        allowed = [(type_id, t) for type_id, t in self._types.items() if not _is_shot_type_id(type_id)]
        allowed.sort(key=lambda kv: kv[1].name.lower())
        return allowed[0][0] if allowed else None

    def _set_type(self, type_id: str) -> None:
        self._selected_type_id = type_id
        t = self._types.get(type_id)
        if t is None:
            return
        self._type_button.setText(t.name)
        depts = self.selected_departments()
        if depts:
            self._type_preview.setText("Departments: " + " / ".join(depts))
            self._type_preview.setVisible(True)
        else:
            self._type_preview.setText("Departments:")
            self._type_preview.setVisible(True)
        self._update_preview_and_ok()

    def _resolve_final_names(self) -> tuple[list[str], list[str], list[str]]:
        """Return (valid unique final names, invalid tokens, already-existing names)."""
        type_def = self._types.get(self._selected_type_id or "")
        tokens = parse_comma_separated_tokens(self._names_input.text())
        valid: list[str] = []
        invalid: list[str] = []
        existing: list[str] = []
        seen: set[str] = set()
        duplicates: set[str] = set()

        for token in tokens:
            final = final_asset_name_from_base(token, type_def)
            if not final:
                invalid.append(token)
                continue
            if final in seen:
                duplicates.add(final)
                continue
            seen.add(final)
            path = self._asset_target_path(final)
            if path is not None and path.exists():
                existing.append(final)
                continue
            valid.append(final)

        if duplicates:
            invalid.extend(sorted(duplicates))
        return valid, invalid, existing

    def _asset_target_path(self, asset_name: str) -> Path | None:
        if not asset_name or not self._selected_type_id:
            return None
        try:
            struct_reg = StructureRegistry.for_project(self._project_root)
            type_reg = TypeRegistry.for_project(self._project_root)
            type_folder = type_reg.get_type_folder(self._selected_type_id)
            assets_folder = struct_reg.get_folder("assets")
            return self._project_root / assets_folder / type_folder / asset_name
        except Exception:
            return None

    def _update_preview_and_ok(self) -> None:
        valid, invalid, existing = self._resolve_final_names()
        warnings: list[str] = []
        if invalid:
            warnings.append(f"Invalid or duplicate: {', '.join(invalid)}")
        if existing:
            warnings.append(f"Already exists: {', '.join(existing)}")
        if warnings:
            self._warning.setText("\n".join(warnings))
            self._warning.setVisible(True)
        else:
            self._warning.setVisible(False)

        if valid:
            preview_text = f"Will create {len(valid)} asset(s): " + ", ".join(valid)
            self._preview.setText(preview_text)
            self._preview.setVisible(True)
        else:
            self._preview.setVisible(False)

        self._ok_btn.setEnabled(bool(self._selected_type_id and valid))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._names_input.setFocus()


class BatchCreateShotDialog(MonosDialog):
    """Create multiple shots from comma-separated shot numbers."""

    def __init__(self, project_root: Path, parent=None, *, initial_type_id: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Batch Create Shots")
        self.setModal(True)

        self._project_root = project_root
        self._initial_type_id = (initial_type_id or "").strip() or None
        self._types: dict[str, TypeDef] = load_pipeline_types_and_presets_for_project(project_root).types
        self._dept_vocab: set[str] = set(load_department_vocabulary())
        self._selected_type_id: str | None = None
        self._padding: int = 3

        self._type_preview = QLabel("")
        self._type_preview.setVisible(False)
        self._type_preview.setWordWrap(True)
        self._type_preview.setObjectName("DialogHelper")

        self._type_button = QToolButton()
        self._type_button.setPopupMode(QToolButton.InstantPopup)
        self._type_button.setText("Select Type…")
        self._type_menu = QMenu(self._type_button)
        self._type_button.setMenu(self._type_menu)
        self._build_type_menu()

        self._numbers_input = _BatchNamesInput(placeholder="e.g. 10, 20, 30 or 10a, 20b")
        self._numbers_input.textChanged.connect(self._update_preview_and_ok)

        self._preview = QLabel("")
        self._preview.setVisible(False)
        self._preview.setWordWrap(True)
        self._preview.setObjectName("DialogHelper")

        self._warning = QLabel("")
        self._warning.setVisible(False)
        self._warning.setWordWrap(True)
        self._warning.setObjectName("DialogWarning")

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        self._ok_btn = QPushButton("Create Shots")
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        button_row_l.addWidget(self._ok_btn)
        button_row_l.addWidget(cancel_btn)
        button_row_l.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
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
        layout.addWidget(
            _field_block_with_preview(
                "Shot Numbers",
                self._numbers_input,
                self._preview,
                "One number per line or comma-separated. Optional suffix per number (e.g. 10a).",
            )
        )
        layout.addWidget(self._warning)
        layout.addSpacing(12)
        layout.addWidget(button_row)

        self.setFixedWidth(_BATCH_CREATE_DIALOG_WIDTH)

        type_to_set = None
        if self._initial_type_id and self._initial_type_id in self._types and _is_shot_type_id(self._initial_type_id):
            type_to_set = self._initial_type_id
        if type_to_set is None:
            type_to_set = self._get_first_shot_type_id()
        if type_to_set is not None:
            self._set_type(type_to_set)
        self._update_preview_and_ok()

    def shot_names(self) -> list[str]:
        type_def = self._types.get(self._selected_type_id or "")
        names: list[str] = []
        seen: set[str] = set()
        for token in parse_comma_separated_tokens(self._numbers_input.text()):
            final = final_shot_name_from_token(token, type_def, padding=self._padding)
            if not final or final in seen:
                continue
            seen.add(final)
            names.append(final)
        return names

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
        return _global_create_work_publish_subfolders_default()

    def _build_type_menu(self) -> None:
        self._type_menu.clear()
        self._selected_type_id = None
        allowed = [
            (type_id, t)
            for type_id, t in sorted(self._types.items(), key=lambda kv: kv[1].name.lower())
            if _is_shot_type_id(type_id)
        ]
        self._type_button.setEnabled(bool(allowed))
        if not allowed:
            self._type_button.setText("No types")
            return
        self._type_button.setText("Select Type…")
        for type_id, t in allowed:
            act = QAction(t.name, self._type_menu)
            act.triggered.connect(lambda checked=False, tid=type_id: self._set_type(tid))
            self._type_menu.addAction(act)

    def _get_first_shot_type_id(self) -> str | None:
        allowed = [
            (type_id, t)
            for type_id, t in sorted(self._types.items(), key=lambda kv: kv[1].name.lower())
            if _is_shot_type_id(type_id)
        ]
        return allowed[0][0] if allowed else None

    def _set_type(self, type_id: str) -> None:
        self._selected_type_id = type_id
        t = self._types.get(type_id)
        if t is None:
            return
        self._type_button.setText(t.name)
        depts = self.selected_departments()
        if depts:
            self._type_preview.setText("Departments: " + " / ".join(depts))
            self._type_preview.setVisible(True)
        else:
            self._type_preview.setText("Departments:")
            self._type_preview.setVisible(True)
        self._update_preview_and_ok()

    def _shot_target_path(self, shot_name: str) -> Path | None:
        if not shot_name:
            return None
        try:
            struct_reg = StructureRegistry.for_project(self._project_root)
            shots_folder = struct_reg.get_folder("shots")
            return self._project_root / shots_folder / shot_name
        except Exception:
            return None

    def _resolve_final_names(self) -> tuple[list[str], list[str], list[str]]:
        type_def = self._types.get(self._selected_type_id or "")
        tokens = parse_comma_separated_tokens(self._numbers_input.text())
        valid: list[str] = []
        invalid: list[str] = []
        existing: list[str] = []
        seen: set[str] = set()
        duplicates: set[str] = set()

        for token in tokens:
            final = final_shot_name_from_token(token, type_def, padding=self._padding)
            if not final:
                invalid.append(token)
                continue
            if final in seen:
                duplicates.add(final)
                continue
            seen.add(final)
            path = self._shot_target_path(final)
            if path is not None and path.exists():
                existing.append(final)
                continue
            valid.append(final)

        if duplicates:
            invalid.extend(sorted(duplicates))
        return valid, invalid, existing

    def _update_preview_and_ok(self) -> None:
        valid, invalid, existing = self._resolve_final_names()
        warnings: list[str] = []
        if invalid:
            warnings.append(f"Invalid or duplicate: {', '.join(invalid)}")
        if existing:
            warnings.append(f"Already exists: {', '.join(existing)}")
        if warnings:
            self._warning.setText("\n".join(warnings))
            self._warning.setVisible(True)
        else:
            self._warning.setVisible(False)

        if valid:
            preview_text = f"Will create {len(valid)} shot(s): " + ", ".join(valid)
            self._preview.setText(preview_text)
            self._preview.setVisible(True)
        else:
            self._preview.setVisible(False)

        self._ok_btn.setEnabled(bool(self._selected_type_id and valid))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._numbers_input.setFocus()
