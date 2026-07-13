"""Shared content toolbar for Inbox/Outbox pages (date folders + file tree)."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from monostudio.ui_qt.app_hotkeys import bind_hotkey, register_hotkey_tooltip
from monostudio.ui_qt.explorer_file_sort import (
    SORT_FIELD_DATE,
    SORT_FIELD_NAME,
    add_explorer_sort_submenu,
    normalize_explorer_sort_field,
    resolve_explorer_sort_action,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu
from monostudio.ui_qt.toolbar_separators import add_widgets_with_icon_separators


def bind_explorer_view_mode_tab_shortcut(
    host: QWidget,
    get_pane: Callable[[], object | None],
    settings: QSettings | None = None,
) -> list[QShortcut]:
    """Tab cycles grid/list on Inbox / Outbox / Project Guide explorer pages."""

    def _cycle() -> None:
        pane = get_pane()
        cycle = getattr(pane, "cycle_view_mode", None)
        if callable(cycle):
            cycle()

    if settings is None:
        settings = QSettings("MonoStudio26", "MonoStudio26")

    return [
        bind_hotkey(
            settings,
            "explorer.cycle_view_mode",
            host,
            _cycle,
            context=Qt.ShortcutContext.WidgetWithChildrenShortcut,
            auto_repeat=False,
        )
    ]


class InboxContentToolbar(QWidget):
    """Hint + Grid/List toggle + Sort (context lives in page header breadcrumb)."""

    view_mode_changed = Signal(str)  # "tile" | "list"
    sort_changed = Signal(str, bool)  # field, ascending

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InboxContentToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        lay.addStretch(1)

        self._hint_label = QLabel("", self)
        self._hint_label.setObjectName("InboxToolbarHint")
        lay.addWidget(self._hint_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._sort_btn = QToolButton(self)
        self._sort_btn.setObjectName("MainViewOptionsButton")
        self._sort_btn.setAutoRaise(True)
        self._sort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sort_btn.setToolTip("Sort grid items")
        self._sort_btn.setIcon(
            lucide_icon("sliders-horizontal", size=16, color_hex=MONOS_COLORS.get("text_label", "#a1a1aa"))
        )
        self._sort_btn.clicked.connect(self._show_sort_menu)
        lay.addWidget(self._sort_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        toggle = QWidget(self)
        toggle.setObjectName("Tier3Container")
        toggle.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        toggle_lay = QHBoxLayout(toggle)
        toggle_lay.setContentsMargins(6, 6, 6, 6)
        toggle_lay.setSpacing(4)

        self._btn_grid = QPushButton("Grid", toggle)
        self._btn_grid.setObjectName("Tier3Pill")
        self._btn_grid.setCheckable(True)
        self._btn_grid.setFlat(True)
        self._btn_list = QPushButton("List", toggle)
        self._btn_list.setObjectName("Tier3Pill")
        self._btn_list.setCheckable(True)
        self._btn_list.setFlat(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self._btn_grid, 0)
        group.addButton(self._btn_list, 1)
        self._btn_grid.clicked.connect(lambda: self.view_mode_changed.emit("tile"))
        self._btn_list.clicked.connect(lambda: self.view_mode_changed.emit("list"))
        add_widgets_with_icon_separators(toggle_lay, [self._btn_grid, self._btn_list], toggle, sep_height=18)
        self._view_toggle = toggle
        lay.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        self._inline = False
        self._settings = QSettings("MonoStudio26", "MonoStudio26")
        self._sort_settings_key = "explorer/sort"
        self._sort_field = SORT_FIELD_NAME
        self._sort_ascending = True
        self.sync_hotkey_tooltips(self._settings)

    def set_sort_settings_key(self, key: str) -> None:
        self._sort_settings_key = (key or "explorer/sort").strip() or "explorer/sort"
        self._load_sort_settings()

    def sort_field(self) -> str:
        return self._sort_field

    def sort_ascending(self) -> bool:
        return self._sort_ascending

    def _load_sort_settings(self) -> None:
        prefix = self._sort_settings_key
        self._sort_field = normalize_explorer_sort_field(
            str(self._settings.value(f"{prefix}/field", SORT_FIELD_NAME) or SORT_FIELD_NAME)
        )
        self._sort_ascending = bool(self._settings.value(f"{prefix}/ascending", True, type=bool))

    def _save_sort_settings(self) -> None:
        prefix = self._sort_settings_key
        self._settings.setValue(f"{prefix}/field", self._sort_field)
        self._settings.setValue(f"{prefix}/ascending", self._sort_ascending)

    def _update_sort_button_tooltip(self) -> None:
        field_lbl = "Name" if self._sort_field == SORT_FIELD_NAME else "Modified"
        order_lbl = "A→Z" if self._sort_ascending else "Z→A"
        if self._sort_field == SORT_FIELD_DATE:
            order_lbl = "oldest first" if self._sort_ascending else "newest first"
        self._sort_btn.setToolTip(f"Sort: {field_lbl} · {order_lbl}")

    def apply_sort_choice(self, field: str, ascending: bool) -> bool:
        field = normalize_explorer_sort_field(field)
        if field == self._sort_field and ascending == self._sort_ascending:
            return False
        self._sort_field = field
        self._sort_ascending = ascending
        self._save_sort_settings()
        self._update_sort_button_tooltip()
        self.sort_changed.emit(self._sort_field, self._sort_ascending)
        return True

    def _show_sort_menu(self) -> None:
        menu = MonosMenu(self)
        menu.setObjectName("FilterStatusMenu")
        section = add_explorer_sort_submenu(
            menu,
            field=self._sort_field,
            ascending=self._sort_ascending,
        )
        chosen = menu.exec(self._sort_btn.mapToGlobal(self._sort_btn.rect().bottomLeft()))
        resolved = resolve_explorer_sort_action(
            chosen,
            section,
            field=self._sort_field,
            ascending=self._sort_ascending,
        )
        if resolved is not None:
            new_field, new_asc = resolved
            self.apply_sort_choice(new_field, new_asc)

    def sync_hotkey_tooltips(self, settings: QSettings | None) -> None:
        if settings is not None:
            self._settings = settings
        register_hotkey_tooltip(
            self._btn_grid,
            "Grid view — Tab cycles Grid / List",
            self._settings,
            "explorer.cycle_view_mode",
        )
        register_hotkey_tooltip(
            self._btn_list,
            "List view — Tab cycles Grid / List",
            self._settings,
            "explorer.cycle_view_mode",
        )

    def set_inline(self, inline: bool) -> None:
        """Path-bar row: transparent, no extra vertical padding."""
        if self._inline == inline:
            return
        self._inline = inline
        self.setObjectName("InboxPathBarToolbar" if inline else "InboxContentToolbar")
        lay = self.layout()
        if inline:
            lay.setContentsMargins(0, 0, 0, 0)
        else:
            lay.setContentsMargins(16, 10, 16, 10)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_context(
        self,
        *,
        hint: str = "",
        show_toggle: bool = True,
        show_sort: bool = True,
        section_title: str = "",  # kept for callers; unused
        count: int | None = None,  # kept for callers; unused
    ) -> None:
        del section_title, count
        self._hint_label.setText(hint)
        self._view_toggle.setVisible(show_toggle)
        self._sort_btn.setVisible(show_sort)
        self._update_sort_button_tooltip()

    def set_view_mode(self, mode: str) -> None:
        self._btn_grid.setChecked(mode == "tile")
        self._btn_list.setChecked(mode == "list")

    def view_toggle(self) -> QWidget:
        return self._view_toggle
