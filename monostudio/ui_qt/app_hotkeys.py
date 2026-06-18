"""Central registry for app keyboard shortcuts (defaults, persistence, settings UI)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_SETTINGS_PREFIX = "ui/hotkeys"
_HOTKEY_ACTION_ID_PROP = "_monos_hotkey_action_id"


@dataclass(frozen=True)
class HotkeyAction:
    action_id: str
    label: str
    category: str
    default: str
    editable: bool = True


def _nav_quick_actions() -> list[HotkeyAction]:
    rows: list[HotkeyAction] = []
    for slot in range(1, 10):
        rows.append(
            HotkeyAction(
                f"nav_quick.assign.{slot}",
                f"Save quick view slot {slot}",
                "Navigation",
                f"Ctrl+{slot}",
            )
        )
    for slot in range(1, 10):
        rows.append(
            HotkeyAction(
                f"nav_quick.recall.{slot}",
                f"Recall quick view slot {slot}",
                "Navigation",
                str(slot),
            )
        )
    for slot in range(1, 10):
        rows.append(
            HotkeyAction(
                f"nav_quick.recall_numpad.{slot}",
                f"Recall quick view slot {slot} (numpad)",
                "Navigation",
                f"Num+{slot}",
            )
        )
    return rows


HOTKEY_ACTIONS: tuple[HotkeyAction, ...] = (
    HotkeyAction("global.command_palette", "Command palette", "Global", "`"),
    HotkeyAction("global.nav_quick_picker", "Quick view picker", "Navigation", "Ctrl+`"),
    HotkeyAction("inspector.tab_pipeline", "Inspector — Pipeline tab", "Inspector", "Alt+1"),
    HotkeyAction("inspector.tab_reference", "Inspector — Reference tab", "Inspector", "Alt+2"),
    HotkeyAction("inspector.tab_details", "Inspector — Details tab", "Inspector", "Alt+3"),
    HotkeyAction("inspector.open_reference", "Open reference folder", "Inspector", "Alt+R"),
    HotkeyAction("inspector.open_concept", "Open concept folder", "Inspector", "Alt+C"),
    HotkeyAction("main_view.search", "Search in current view", "Main View", "Ctrl+F"),
    HotkeyAction("main_view.toggle_publish", "Toggle Published mode", "Main View", "P"),
    HotkeyAction("main_view.cycle_view_mode", "Cycle grid / list view", "Main View", "Tab"),
    *_nav_quick_actions(),
    HotkeyAction("schedule.select_tool", "Schedule — select tool", "Schedule", "Q"),
    HotkeyAction("schedule.draw_tool", "Schedule — draw tool", "Schedule", "W"),
    HotkeyAction("schedule.today", "Schedule — go to today", "Schedule", "F"),
    HotkeyAction("schedule.undo", "Schedule — undo", "Schedule", "Ctrl+Z"),
    HotkeyAction("schedule.redo", "Schedule — redo", "Schedule", "Ctrl+Shift+Z"),
    HotkeyAction("schedule.save", "Schedule — save", "Schedule", "Ctrl+S"),
    HotkeyAction("schedule.cycle_layout", "Schedule — cycle layout view", "Schedule", "Tab"),
    HotkeyAction(
        "explorer.cycle_view_mode",
        "Inbox / Outbox / Project Guide — cycle grid / list",
        "Explorer",
        "Tab",
    ),
)

_HOTKEY_BY_ID: dict[str, HotkeyAction] = {a.action_id: a for a in HOTKEY_ACTIONS}

_CATEGORY_ORDER: tuple[str, ...] = (
    "Global",
    "Main View",
    "Inspector",
    "Navigation",
    "Schedule",
    "Explorer",
)


def hotkey_action(action_id: str) -> HotkeyAction | None:
    return _HOTKEY_BY_ID.get(action_id)


def iter_hotkey_actions() -> list[HotkeyAction]:
    order = {name: idx for idx, name in enumerate(_CATEGORY_ORDER)}
    return sorted(HOTKEY_ACTIONS, key=lambda a: (order.get(a.category, 99), a.label.casefold()))


def hotkey_settings_key(action_id: str) -> str:
    return f"{_SETTINGS_PREFIX}/{action_id}"


def read_hotkey_sequence(settings: QSettings | None, action_id: str) -> QKeySequence:
    action = _HOTKEY_BY_ID.get(action_id)
    default = action.default if action else ""
    if settings is None:
        return QKeySequence(default)
    raw = settings.value(hotkey_settings_key(action_id), default, str)
    text = (raw or "").strip() if isinstance(raw, str) else default
    if not text:
        return QKeySequence()
    return QKeySequence(text)


def write_hotkey_sequence(settings: QSettings, action_id: str, sequence: QKeySequence | str) -> None:
    if isinstance(sequence, QKeySequence):
        text = sequence.toString(QKeySequence.SequenceFormat.PortableText).strip()
    else:
        text = (sequence or "").strip()
    settings.setValue(hotkey_settings_key(action_id), text)


def reset_hotkey(settings: QSettings, action_id: str) -> None:
    action = _HOTKEY_BY_ID.get(action_id)
    if action is None:
        return
    settings.setValue(hotkey_settings_key(action_id), action.default)


def reset_all_hotkeys(settings: QSettings) -> None:
    for action in HOTKEY_ACTIONS:
        settings.setValue(hotkey_settings_key(action.action_id), action.default)


def format_hotkey_display(settings: QSettings | None, action_id: str) -> str:
    seq = read_hotkey_sequence(settings, action_id)
    text = seq.toString(QKeySequence.SequenceFormat.NativeText)
    return text or "—"


def tooltip_with_hotkey(base: str, settings: QSettings | None, action_id: str) -> str:
    text = (base or "").strip()
    seq = format_hotkey_display(settings, action_id)
    if not seq or seq == "—":
        return text
    return f"{text} ({seq})"


_TOOLTIP_REGISTRY: list[tuple[QWidget, str, str]] = []


def register_hotkey_tooltip(
    widget: QWidget,
    base: str,
    settings: QSettings | None,
    action_id: str,
) -> None:
    """Attach a tooltip that includes the bound shortcut; tracked for refresh after Settings save."""
    for idx, (existing, _, _) in enumerate(_TOOLTIP_REGISTRY):
        if existing is widget:
            _TOOLTIP_REGISTRY[idx] = (widget, base, action_id)
            widget.setToolTip(tooltip_with_hotkey(base, settings, action_id))
            return
    _TOOLTIP_REGISTRY.append((widget, base, action_id))
    widget.setToolTip(tooltip_with_hotkey(base, settings, action_id))


def refresh_all_hotkey_tooltips(settings: QSettings | None) -> None:
    kept: list[tuple[QWidget, str, str]] = []
    for widget, base, action_id in _TOOLTIP_REGISTRY:
        try:
            widget.setToolTip(tooltip_with_hotkey(base, settings, action_id))
            kept.append((widget, base, action_id))
        except RuntimeError:
            continue
    _TOOLTIP_REGISTRY.clear()
    _TOOLTIP_REGISTRY.extend(kept)


def format_nav_quick_hint(settings: QSettings | None) -> str:
    assign_first = format_hotkey_display(settings, "nav_quick.assign.1")
    assign_last = format_hotkey_display(settings, "nav_quick.assign.9")
    if assign_first.endswith("1") and assign_last.endswith("9") and len(assign_first) == len(assign_last):
        assign_range = assign_first[:-1] + "1–9"
    elif assign_first == assign_last:
        assign_range = assign_first
    else:
        assign_range = f"{assign_first} … {assign_last}"

    recall_first = format_hotkey_display(settings, "nav_quick.recall.1")
    recall_last = format_hotkey_display(settings, "nav_quick.recall.9")
    if recall_first.isdigit() and recall_last.isdigit() and recall_first != recall_last:
        recall_range = f"{recall_first}–{recall_last}"
    elif recall_first == recall_last:
        recall_range = recall_first
    else:
        recall_range = f"{recall_first} … {recall_last}"
    picker = format_hotkey_display(settings, "global.nav_quick_picker")
    return f"{picker} picker · {assign_range} assign · {recall_range} go"


def bind_hotkey(
    settings: QSettings | None,
    action_id: str,
    parent: QWidget,
    callback: Callable[[], None],
    *,
    context: Qt.ShortcutContext = Qt.ShortcutContext.WindowShortcut,
    auto_repeat: bool = True,
) -> QShortcut:
    sc = QShortcut(read_hotkey_sequence(settings, action_id), parent)
    sc.setContext(context)
    sc.setAutoRepeat(auto_repeat)
    sc.activated.connect(callback)
    sc.setProperty(_HOTKEY_ACTION_ID_PROP, action_id)
    return sc


def reload_bound_shortcuts(settings: QSettings | None, shortcuts: Iterable[QShortcut]) -> None:
    for sc in shortcuts:
        action_id = sc.property(_HOTKEY_ACTION_ID_PROP)
        if not action_id:
            continue
        sc.setKey(read_hotkey_sequence(settings, str(action_id)))


class HotkeysSettingsWidget(QWidget):
    """General → Hotkeys: edit all registered shortcuts."""

    def __init__(self, settings: QSettings | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._editors: dict[str, QKeySequenceEdit] = {}

        scroll = QScrollArea(self)
        scroll.setObjectName("SettingsPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "Shortcuts apply after Save. Some keys are context-specific (Schedule, Explorer) "
            "and only work on their page.",
            inner,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        layout.addWidget(hint)

        table = QTableWidget(len(HOTKEY_ACTIONS), 3, inner)
        table.setHorizontalHeaderLabels(["Action", "Category", "Shortcut"])
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.setSelectionMode(table.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for row, action in enumerate(iter_hotkey_actions()):
            label_item = QTableWidgetItem(action.label)
            label_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 0, label_item)

            cat_item = QTableWidgetItem(action.category)
            cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(row, 1, cat_item)

            editor = QKeySequenceEdit(table)
            editor.setObjectName("SettingsHotkeyEditor")
            editor.setKeySequence(read_hotkey_sequence(settings, action.action_id))
            editor.setEnabled(action.editable)
            editor.setClearButtonEnabled(True)
            editor.setMaximumWidth(220)
            self._editors[action.action_id] = editor
            table.setCellWidget(row, 2, editor)
            table.setRowHeight(row, 40)

        layout.addWidget(table, 1)

        btn_row = QWidget(inner)
        btn_row_l = QHBoxLayout(btn_row)
        btn_row_l.setContentsMargins(0, 0, 0, 0)
        reset_btn = QPushButton("Reset all to defaults", btn_row)
        reset_btn.setObjectName("SettingsInlineActionButton")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_all)
        btn_row_l.addWidget(reset_btn, 0)
        btn_row_l.addStretch(1)
        layout.addWidget(btn_row)

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _reset_all(self) -> None:
        if self._settings is not None:
            reset_all_hotkeys(self._settings)
        for action in HOTKEY_ACTIONS:
            editor = self._editors.get(action.action_id)
            if editor is not None:
                editor.setKeySequence(QKeySequence(action.default))

    def persist(self, settings: QSettings) -> None:
        for action_id, editor in self._editors.items():
            write_hotkey_sequence(settings, action_id, editor.keySequence())
