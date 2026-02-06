from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
_HEADER_ICON_SIZE = 28


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
        self._last_used = False

    def dcc_id(self) -> str:
        return self._dcc_id

    def set_last_used(self, last_used: bool) -> None:
        if self._last_used != last_used:
            self._last_used = last_used
            self.setProperty("last_used", "true" if last_used else "false")
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

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
        icon: QIcon | None = None,
        hint_text: str | None = None,
        primary_button_text: str = "Open",
        allowed_dcc_ids: list[str] | None = None,
        disabled_dcc_ids: list[str] | None = None,
        show_department_picker: bool = False,
        item_name: str = "",
        type_folder: str = "",
        department_label: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or "Open With…")
        self.setModal(True)

        self._choice: OpenResolverChoice | None = None
        self._dcc_registry = dcc_registry
        self._dept_registry = department_registry
        self._initial_dcc = (initial_dcc or "").strip() or None
        self._allowed_dcc_ids: set[str] = set(allowed_dcc_ids) if allowed_dcc_ids else set()
        self._disabled_dcc_ids: set[str] = set(disabled_dcc_ids) if disabled_dcc_ids else set()
        self._show_department_picker = show_department_picker
        self._fixed_department = (initial_department or "").strip() or None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Header: icon + bold title (distinguishes Open With vs Create New)
        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 8)
        header_layout.setSpacing(10)
        if icon and not icon.isNull():
            icon_label = QLabel(header)
            icon_label.setObjectName("OpenResolverDialogHeaderIcon")
            pix = icon.pixmap(QSize(_HEADER_ICON_SIZE, _HEADER_ICON_SIZE))
            icon_label.setPixmap(pix)
            icon_label.setFixedSize(_HEADER_ICON_SIZE, _HEADER_ICON_SIZE)
            header_layout.addWidget(icon_label, 0)
        title_label = QLabel(title or "Open With…", header)
        title_label.setObjectName("OpenResolverDialogTitle")
        header_layout.addWidget(title_label, 1)
        root.addWidget(header, 0)

        # Context: asset name, type folder, department (so user sees what is being opened/created)
        if item_name or type_folder or department_label:
            ctx = QWidget(self)
            ctx.setObjectName("OpenResolverContext")
            ctx_l = QVBoxLayout(ctx)
            ctx_l.setContentsMargins(0, 0, 0, 8)
            ctx_l.setSpacing(4)
            if item_name:
                row = QWidget(ctx)
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                k1 = QLabel("Asset / Shot:", ctx)
                k1.setObjectName("DialogHint")
                v1 = QLabel(item_name, ctx)
                v1.setObjectName("OpenResolverContextValue")
                row_l.addWidget(k1, 0)
                row_l.addWidget(v1, 0)
                row_l.addStretch(1)
                ctx_l.addWidget(row, 0)
            if type_folder:
                row = QWidget(ctx)
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                k2 = QLabel("Type folder:", ctx)
                k2.setObjectName("DialogHint")
                v2 = QLabel(type_folder, ctx)
                v2.setObjectName("OpenResolverContextValue")
                row_l.addWidget(k2, 0)
                row_l.addWidget(v2, 0)
                row_l.addStretch(1)
                ctx_l.addWidget(row, 0)
            if department_label:
                row = QWidget(ctx)
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                k3 = QLabel("Department:", ctx)
                k3.setObjectName("DialogHint")
                v3 = QLabel(department_label, ctx)
                v3.setObjectName("OpenResolverContextValue")
                row_l.addWidget(k3, 0)
                row_l.addWidget(v3, 0)
                row_l.addStretch(1)
                ctx_l.addWidget(row, 0)
            root.addWidget(ctx, 0)

        hint = QLabel(hint_text or "Choose a DCC to open.", self)
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
        self._dept_label = QLabel("Department", grp)
        grp_layout.addWidget(self._dept_label, 0)
        grp_layout.addWidget(self._dept, 0)
        grp_layout.addWidget(QLabel("DCC", grp), 0)
        grp_layout.addWidget(scroll, 1)

        if not self._show_department_picker:
            self._dept_label.setVisible(False)
            self._dept.setVisible(False)

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

        button_row = QWidget(self)
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        self._btn_ok = QPushButton(primary_button_text, self)
        self._btn_ok.setObjectName("DialogPrimaryButton")
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        button_row_l.addWidget(self._btn_ok)
        button_row_l.addWidget(cancel_btn)
        button_row_l.addStretch(1)
        root.addWidget(button_row, 0)

        def on_card_clicked(dcc_id: str) -> None:
            if dcc_id in self._disabled_dcc_ids:
                return
            self._selected_dcc_id = (dcc_id or "").strip() or None
            for card in self._dcc_cards:
                card.set_selected(card.isEnabled() and card.dcc_id() == self._selected_dcc_id)
            if self._btn_ok is not None:
                self._btn_ok.setEnabled(bool(self._selected_dcc_id))

        def sync_dcc_list(_idx: int | None = None) -> None:
            # When _allowed_dcc_ids is set (Open With), only show DCCs that have created work files.
            for card in self._dcc_cards:
                card.clicked_card.disconnect()
                card.setParent(None)
                card.deleteLater()
            self._dcc_cards.clear()
            self._selected_dcc_id = None

            dcc_ids = self._dcc_registry.get_all_dccs()
            if self._allowed_dcc_ids:
                dcc_ids = [d for d in dcc_ids if d in self._allowed_dcc_ids]
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
                if dcc_id in self._disabled_dcc_ids:
                    card.setEnabled(False)
                    card.setToolTip("DCC folder already exists for this department.")
                card.clicked_card.connect(on_card_clicked)
                row = col // _DCC_CARDS_PER_ROW
                c = col % _DCC_CARDS_PER_ROW
                self._cards_layout.addWidget(card, row, c)
                self._dcc_cards.append(card)
                # Mark card that was opened most recently (for green border)
                if self._initial_dcc and card.dcc_id() == self._initial_dcc:
                    card.set_last_used(True)

            # Apply initial or default selection (only enabled cards)
            enabled_cards = [c for c in self._dcc_cards if c.isEnabled()]
            if self._initial_dcc:
                for card in self._dcc_cards:
                    if card.dcc_id() == self._initial_dcc and card.isEnabled():
                        self._selected_dcc_id = card.dcc_id()
                        card.set_selected(True)
                        break
                self._initial_dcc = None
            if self._selected_dcc_id is None and enabled_cards:
                enabled_cards[0].set_selected(True)
                self._selected_dcc_id = enabled_cards[0].dcc_id()

            has = len(enabled_cards) > 0
            self._no_dcc_hint.setVisible(not has)
            if not has:
                if self._disabled_dcc_ids and self._dcc_cards:
                    self._no_dcc_hint.setText(
                        "All DCCs already have a folder for this department."
                    )
                elif self._allowed_dcc_ids:
                    self._no_dcc_hint.setText("No DCCs with work files yet.")
                else:
                    self._no_dcc_hint.setText("No DCCs registered.")
            if self._btn_ok is not None:
                self._btn_ok.setEnabled(has and bool(self._selected_dcc_id))

        self._dept.currentIndexChanged.connect(sync_dcc_list)
        sync_dcc_list(None)

    def choice(self) -> OpenResolverChoice | None:
        return self._choice

    def _on_accept(self) -> None:
        if self._show_department_picker:
            dept_id = self._dept.currentData()
            dept = (dept_id or "").strip() if isinstance(dept_id, str) else ""
        else:
            dept = self._fixed_department or ""
        dcc = (self._selected_dcc_id or "").strip() or None
        if not dept or not dcc:
            return
        self._choice = OpenResolverChoice(department=dept, dcc=dcc, remember_for_item=bool(self._remember.isChecked()))
        self.accept()

