from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.dcc_registry import DccRegistry
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.style import MonosDialog


# Card 1:1 (square); icon size inside card
_DCC_CARD_SIZE = 100
_DCC_CARD_ICON_SIZE = 44
_DCC_CARDS_PER_ROW = 4


@dataclass(frozen=True)
class OpenResolverChoice:
    department: str  # logical department ID
    dcc: str
    remember_for_item: bool


class DccCard(QFrame):
    """Clickable card showing DCC icon + label. Used in Open Resolver dialog."""

    clicked_card = Signal(str)  # emits dcc_id

    def __init__(self, dcc_id: str, label: str, icon_slug: str, color_hex: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DccCard")
        self._dcc_id = dcc_id
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = brand_icon(icon_slug, size=_DCC_CARD_ICON_SIZE, color_hex=color_hex or "#e4e4e7")
        self._icon_label = QLabel(self)
        self._icon_label.setPixmap(icon.pixmap(QSize(_DCC_CARD_ICON_SIZE, _DCC_CARD_ICON_SIZE)))
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedSize(_DCC_CARD_ICON_SIZE, _DCC_CARD_ICON_SIZE)
        layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self._text_label = QLabel(label, self)
        self._text_label.setObjectName("DccCardLabel")
        self._text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text_label.setWordWrap(True)
        layout.addWidget(self._text_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setFixedSize(_DCC_CARD_SIZE, _DCC_CARD_SIZE)

    def dcc_id(self) -> str:
        return self._dcc_id

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)

    def is_selected(self) -> bool:
        return self._selected

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked_card.emit(self._dcc_id)
        super().mousePressEvent(event)


class OpenResolverDialog(MonosDialog):
    """
    Fallback-only dialog: choose Department + DCC explicitly.

    - Uses logical department IDs; displays labels from DepartmentRegistry.
    - Shows only when Smart Open cannot resolve context
      OR when user explicitly chooses "Open With..."
    """

    def __init__(
        self,
        *,
        title: str,
        department_registry: DepartmentRegistry,
        available_department_ids: list[str],
        dcc_registry: DccRegistry,
        initial_department: str | None = None,
        initial_dcc: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or "Open With…")
        self.setModal(True)

        self._choice: OpenResolverChoice | None = None
        self._dcc_registry = dcc_registry
        self._dept_registry = department_registry
        self._initial_dcc = (initial_dcc or "").strip() or None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel("Choose a Department and a DCC to open this item.", self)
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        root.addWidget(hint, 0)

        grp = QGroupBox("Open Context", self)

        self._dept = QComboBox(self)
        self._dept.setEditable(False)
        for dept_id in available_department_ids:
            if isinstance(dept_id, str) and dept_id.strip():
                label = department_registry.get_department_label(dept_id)
                self._dept.addItem(label or dept_id, dept_id)

        self._no_dcc_hint = QLabel("", self)
        self._no_dcc_hint.setWordWrap(True)
        self._no_dcc_hint.setObjectName("DialogHint")
        self._no_dcc_hint.setVisible(False)

        # DCC cards container (grid of DccCard)
        self._cards_container = QWidget(self)
        self._cards_layout = QGridLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        self._dcc_cards: list[DccCard] = []
        self._selected_dcc_id: str | None = None

        scroll = QScrollArea(self)
        scroll.setObjectName("OpenResolverScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._cards_container)
        scroll.setMinimumHeight(140)
        scroll.setMaximumHeight(220)

        grp_layout = QVBoxLayout(grp)
        grp_layout.setContentsMargins(12, 12, 12, 12)
        grp_layout.setSpacing(10)
        grp_layout.addWidget(QLabel("Department", grp), 0)
        grp_layout.addWidget(self._dept, 0)
        grp_layout.addWidget(QLabel("DCC", grp), 0)
        grp_layout.addWidget(scroll, 1)

        # Initial selection by logical ID.
        if initial_department:
            for i in range(self._dept.count()):
                if self._dept.itemData(i) == initial_department:
                    self._dept.setCurrentIndex(i)
                    break

        self._remember = QCheckBox("Remember as default for this item", self)

        wrap = QWidget(self)
        wrap_l = QVBoxLayout(wrap)
        wrap_l.setContentsMargins(0, 0, 0, 0)
        wrap_l.setSpacing(8)
        wrap_l.addWidget(grp, 0)
        wrap_l.addWidget(self._remember, 0)
        wrap_l.addWidget(self._no_dcc_hint, 0)
        root.addWidget(wrap, 0)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok = buttons.button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setText("Open")
            ok.setDefault(True)
        self._btn_ok = ok
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons, 0)

        def on_card_clicked(dcc_id: str) -> None:
            self._selected_dcc_id = (dcc_id or "").strip() or None
            for card in self._dcc_cards:
                card.set_selected(card.dcc_id() == self._selected_dcc_id)
            if self._btn_ok is not None:
                self._btn_ok.setEnabled(bool(self._selected_dcc_id))

        def sync_dcc_list(_idx: int | None = None) -> None:
            # Show all supported DCCs regardless of department
            # Clear existing cards
            for card in self._dcc_cards:
                card.clicked_card.disconnect()
                card.setParent(None)
                card.deleteLater()
            self._dcc_cards.clear()
            self._selected_dcc_id = None

            dcc_ids = self._dcc_registry.get_all_dccs()
            for col, dcc_id in enumerate(dcc_ids):
                info = self._dcc_registry.get_dcc_info(dcc_id)
                label = info.get("label") if isinstance(info, dict) else None
                lab = label if isinstance(label, str) and label.strip() else dcc_id
                icon_slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
                slug = (icon_slug or dcc_id or "").strip()
                color_hex = info.get("brand_color_hex") if isinstance(info, dict) else None
                card = DccCard(
                    dcc_id=dcc_id,
                    label=lab,
                    icon_slug=slug,
                    color_hex=str(color_hex).strip() if isinstance(color_hex, str) else None,
                    parent=self._cards_container,
                )
                card.clicked_card.connect(on_card_clicked)
                row = col // _DCC_CARDS_PER_ROW
                c = col % _DCC_CARDS_PER_ROW
                self._cards_layout.addWidget(card, row, c)
                self._dcc_cards.append(card)

            # Apply initial or default selection
            if self._initial_dcc:
                for card in self._dcc_cards:
                    if card.dcc_id() == self._initial_dcc:
                        self._selected_dcc_id = card.dcc_id()
                        card.set_selected(True)
                        break
                self._initial_dcc = None
            if self._selected_dcc_id is None and self._dcc_cards:
                self._dcc_cards[0].set_selected(True)
                self._selected_dcc_id = self._dcc_cards[0].dcc_id()

            has = len(self._dcc_cards) > 0
            self._no_dcc_hint.setVisible(not has)
            if not has:
                self._no_dcc_hint.setText("No DCCs registered.")
            if self._btn_ok is not None:
                self._btn_ok.setEnabled(has and bool(self._selected_dcc_id))

        self._dept.currentIndexChanged.connect(sync_dcc_list)
        sync_dcc_list(None)

    def choice(self) -> OpenResolverChoice | None:
        return self._choice

    def _on_accept(self) -> None:
        dept_id = self._dept.currentData()
        dept = (dept_id or "").strip() if isinstance(dept_id, str) else ""
        dcc = (self._selected_dcc_id or "").strip() or None
        if not dept or not dcc:
            return
        self._choice = OpenResolverChoice(department=dept, dcc=dcc, remember_for_item=bool(self._remember.isChecked()))
        self.accept()

