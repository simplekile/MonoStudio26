from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QIcon
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
from monostudio.core.project_create_defaults import read_create_default_dcc, write_create_default_dcc
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.style import MonosDialog, MonosMenu


# Card 1:1 (square); icon + label use fixed px per scale (see typography: compact labels)
_DCC_CARD_SIZE_PRIMARY = 100
_DCC_CARD_SIZE_COMPACT = 58
_DCC_CARD_ICON_SIZE = 44
_DCC_CARD_ICON_SIZE_COMPACT = _DCC_CARD_ICON_SIZE // 2
_DCC_CARD_LABEL_PX_PRIMARY = 11
_DCC_CARD_LABEL_PX_COMPACT = 9
_DCC_CARD_MARGIN_PRIMARY = 12
_DCC_CARD_MARGIN_COMPACT = 6
_DCC_CARD_SPACING_PRIMARY = 8
_DCC_CARD_SPACING_COMPACT = 4
_DCC_CARDS_PER_ROW = 4
_HEADER_ICON_SIZE = 28


@dataclass(frozen=True)
class OpenResolverChoice:
    department: str  # logical department ID
    dcc: str
    import_source: bool = False


class DccCard(QFrame):
    """Clickable card showing DCC icon + label. Used in Open Resolver dialog."""

    clicked_card = Signal(str)  # emits dcc_id

    def __init__(self, dcc_id: str, label: str, icon_slug: str, color_hex: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DccCard")
        self._dcc_id = dcc_id
        self._icon_slug = (icon_slug or dcc_id or "").strip()
        self._color_hex = (color_hex or "#e4e4e7").strip() if isinstance(color_hex, str) else "#e4e4e7"
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(
            _DCC_CARD_MARGIN_PRIMARY,
            _DCC_CARD_MARGIN_PRIMARY,
            _DCC_CARD_MARGIN_PRIMARY,
            _DCC_CARD_MARGIN_PRIMARY,
        )
        self._layout.setSpacing(_DCC_CARD_SPACING_PRIMARY)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel(self)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self._text_label = QLabel(label, self)
        self._text_label.setObjectName("DccCardLabel")
        self._text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text_label.setWordWrap(True)
        self._layout.addWidget(self._text_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self._last_used = False
        self._dept_default = False
        self._scale_primary = True
        self.set_icon_primary(True)

    def _set_label_font_px(self, px: int) -> None:
        scale = "primary" if px >= _DCC_CARD_LABEL_PX_PRIMARY else "compact"
        self._text_label.setProperty("labelScale", scale)
        f = QFont("Inter")
        f.setPixelSize(max(8, int(px)))
        f.setWeight(QFont.Weight.Medium)
        self._text_label.setFont(f)
        st = self._text_label.style()
        if st is not None:
            st.unpolish(self._text_label)
            st.polish(self._text_label)

    def _reapply_label_font_after_style(self) -> None:
        """QFrame polish / global QSS can reset QLabel to 13px; restore fixed card label size."""
        px = _DCC_CARD_LABEL_PX_PRIMARY if self._scale_primary else _DCC_CARD_LABEL_PX_COMPACT
        self._set_label_font_px(px)

    def _apply_icon_pixmap(self, px: int) -> None:
        icon = brand_icon(self._icon_slug, size=px, color_hex=self._color_hex)
        self._icon_label.setPixmap(icon.pixmap(QSize(px, px)))
        self._icon_label.setFixedSize(px, px)

    def set_icon_primary(self, primary: bool) -> None:
        """Primary: full card + icon; non-primary: 50% icon, smaller card, fixed smaller label."""
        self._scale_primary = primary
        if primary:
            self._layout.setContentsMargins(
                _DCC_CARD_MARGIN_PRIMARY,
                _DCC_CARD_MARGIN_PRIMARY,
                _DCC_CARD_MARGIN_PRIMARY,
                _DCC_CARD_MARGIN_PRIMARY,
            )
            self._layout.setSpacing(_DCC_CARD_SPACING_PRIMARY)
            self._apply_icon_pixmap(_DCC_CARD_ICON_SIZE)
            self._set_label_font_px(_DCC_CARD_LABEL_PX_PRIMARY)
            inner = _DCC_CARD_SIZE_PRIMARY - 2 * _DCC_CARD_MARGIN_PRIMARY
            self._text_label.setMaximumWidth(inner)
            self.setFixedSize(_DCC_CARD_SIZE_PRIMARY, _DCC_CARD_SIZE_PRIMARY)
        else:
            self._layout.setContentsMargins(
                _DCC_CARD_MARGIN_COMPACT,
                _DCC_CARD_MARGIN_COMPACT,
                _DCC_CARD_MARGIN_COMPACT,
                _DCC_CARD_MARGIN_COMPACT,
            )
            self._layout.setSpacing(_DCC_CARD_SPACING_COMPACT)
            self._apply_icon_pixmap(_DCC_CARD_ICON_SIZE_COMPACT)
            self._set_label_font_px(_DCC_CARD_LABEL_PX_COMPACT)
            inner = _DCC_CARD_SIZE_COMPACT - 2 * _DCC_CARD_MARGIN_COMPACT
            self._text_label.setMaximumWidth(inner)
            self.setFixedSize(_DCC_CARD_SIZE_COMPACT, _DCC_CARD_SIZE_COMPACT)

    def dcc_id(self) -> str:
        return self._dcc_id

    def set_last_used(self, last_used: bool) -> None:
        if self._last_used != last_used:
            self._last_used = last_used
            self.setProperty("last_used", "true" if last_used else "false")
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
            self._reapply_label_font_after_style()

    def set_department_default(self, is_default: bool) -> None:
        if self._dept_default != is_default:
            self._dept_default = is_default
            self.setProperty("dept_default", "true" if is_default else "false")
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
            self._reapply_label_font_after_style()

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)
            self._reapply_label_font_after_style()

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
        project_root: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or "Open With…")
        self.setModal(True)

        self._choice: OpenResolverChoice | None = None
        self._dcc_registry = dcc_registry
        self._dept_registry = department_registry
        self._initial_dcc = (initial_dcc or "").strip() or None
        self._project_root = project_root
        self._registry_dcc_order: list[str] = []
        self._hint_last_used_dcc: str | None = None
        # Keep `None` vs empty distinct:
        # - None: no filtering (show all registered DCCs)
        # - empty set: explicit filter with zero matches (show no DCC cards)
        self._allowed_dcc_ids: set[str] | None = None
        if allowed_dcc_ids is not None:
            self._allowed_dcc_ids = {
                d.strip()
                for d in allowed_dcc_ids
                if isinstance(d, str) and d.strip()
            }
        self._disabled_dcc_ids: set[str] = set(disabled_dcc_ids) if disabled_dcc_ids else set()
        self._is_create_mode = (primary_button_text or "").strip().casefold() == "create"
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
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._cards_container)
        scroll.setMinimumHeight(140)
        scroll.setMaximumHeight(220)
        scroll.viewport().setAutoFillBackground(False)

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

        self._import_source_cb = QCheckBox("Import source file", self)
        self._import_source_cb.setToolTip("Browse or drag-drop a file to copy into the work folder with the correct naming.")
        self._import_source_cb.setVisible(self._is_create_mode)

        wrap = QWidget(self)
        wrap_l = QVBoxLayout(wrap)
        wrap_l.setContentsMargins(0, 0, 0, 0)
        wrap_l.setSpacing(8)
        wrap_l.addWidget(grp, 0)
        wrap_l.addWidget(self._import_source_cb, 0)
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

        def _sync_import_checkbox(dcc_id: str | None) -> None:
            if not self._is_create_mode or dcc_id is None:
                return
            forced = self._dcc_registry.requires_import(dcc_id)
            if forced:
                self._import_source_cb.setChecked(True)
                self._import_source_cb.setEnabled(False)
            else:
                self._import_source_cb.setEnabled(True)

        def on_card_clicked(dcc_id: str) -> None:
            if dcc_id in self._disabled_dcc_ids:
                return
            self._selected_dcc_id = (dcc_id or "").strip() or None
            self._apply_dcc_card_selection()
            if self._btn_ok is not None:
                self._btn_ok.setEnabled(bool(self._selected_dcc_id))
            _sync_import_checkbox(self._selected_dcc_id)

        def sync_dcc_list(_idx: int | None = None) -> None:
            hint_last_used = (self._initial_dcc or "").strip() or None
            # When _allowed_dcc_ids is set (Open With), only show DCCs that have created work files.
            for card in self._dcc_cards:
                card.clicked_card.disconnect()
                card.setParent(None)
                card.deleteLater()
            self._dcc_cards.clear()
            self._selected_dcc_id = None

            dcc_ids = self._dcc_registry.get_all_dccs()
            if self._allowed_dcc_ids is not None:
                dcc_ids = [d for d in dcc_ids if d in self._allowed_dcc_ids]
            self._registry_dcc_order = list(dcc_ids)
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
                if (
                    self._is_create_mode
                    and self._project_root is not None
                    and self._create_default_context_dept() is not None
                ):
                    card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                    card.customContextMenuRequested.connect(
                        lambda pos, c=card: self._on_dcc_card_context_menu(c, pos)
                    )
                row = col // _DCC_CARDS_PER_ROW
                c = col % _DCC_CARDS_PER_ROW
                self._cards_layout.addWidget(card, row, c)
                self._dcc_cards.append(card)

            self._hint_last_used_dcc = hint_last_used

            # Apply initial or default selection (only enabled cards)
            enabled_cards = [c for c in self._dcc_cards if c.isEnabled()]
            dept_ctx = self._create_default_context_dept()
            used_saved_default = False
            if (
                self._is_create_mode
                and self._project_root is not None
                and dept_ctx
                and enabled_cards
            ):
                saved = read_create_default_dcc(self._project_root, dept_ctx)
                if saved and any(c.dcc_id() == saved and c.isEnabled() for c in self._dcc_cards):
                    self._selected_dcc_id = saved
                    used_saved_default = True
                    self._initial_dcc = None
            if not used_saved_default and self._initial_dcc:
                for card in self._dcc_cards:
                    if card.dcc_id() == self._initial_dcc and card.isEnabled():
                        self._selected_dcc_id = card.dcc_id()
                        break
                self._initial_dcc = None
            if self._selected_dcc_id is None and enabled_cards:
                self._selected_dcc_id = enabled_cards[0].dcc_id()

            has = len(enabled_cards) > 0
            self._no_dcc_hint.setVisible(not has)
            if not has:
                if self._disabled_dcc_ids and self._dcc_cards:
                    self._no_dcc_hint.setText(
                        "All DCCs already have a folder for this department."
                    )
                elif self._allowed_dcc_ids is not None:
                    if self._is_create_mode:
                        self._no_dcc_hint.setText("No DCCs configured for this department.")
                    else:
                        self._no_dcc_hint.setText("No DCCs with work files yet.")
                else:
                    self._no_dcc_hint.setText("No DCCs registered.")
            if self._btn_ok is not None:
                self._btn_ok.setEnabled(has and bool(self._selected_dcc_id))
            _sync_import_checkbox(self._selected_dcc_id)
            self._apply_dcc_card_selection()

        self._dept.currentIndexChanged.connect(sync_dcc_list)
        sync_dcc_list(None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_dcc_card_grid()

    def _apply_dcc_card_selection(self) -> None:
        sid = self._selected_dcc_id
        for card in self._dcc_cards:
            card.set_selected(card.isEnabled() and card.dcc_id() == sid)
        self._sync_last_used_visual()
        self._sync_department_default_visual()
        self._sync_dcc_card_grid()

    def _on_dcc_card_context_menu(self, card: DccCard, pos) -> None:
        if not self._is_create_mode or self._project_root is None or not card.isEnabled():
            return
        dept = self._create_default_context_dept()
        if not dept:
            return
        dcc_id = card.dcc_id()
        current = read_create_default_dcc(self._project_root, dept)
        menu = MonosMenu(self)
        set_act = menu.addAction("Set as default for this department")
        if current and current.casefold() == (dcc_id or "").casefold():
            set_act.setEnabled(False)
            set_act.setToolTip("This DCC is already the create default for this department.")
        chosen = menu.exec(card.mapToGlobal(pos))
        if chosen is not set_act or not set_act.isEnabled():
            return
        if write_create_default_dcc(self._project_root, dept, dcc_id):
            self._sync_department_default_visual()
            self._sync_dcc_card_grid()

    def _sync_last_used_visual(self) -> None:
        """Light ring only for last-opened DCC when it is not the current selection."""
        hint = self._hint_last_used_dcc
        sel = self._selected_dcc_id
        for card in self._dcc_cards:
            card.set_last_used(
                bool(
                    hint
                    and card.isEnabled()
                    and card.dcc_id() == hint
                    and card.dcc_id() != sel
                )
            )

    def _create_default_context_dept(self) -> str | None:
        if self._show_department_picker:
            raw = self._dept.currentData()
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            return None
        return self._fixed_department

    def _sync_department_default_visual(self) -> None:
        """Mark the card that matches project create-default for this department."""
        dept = self._create_default_context_dept() if self._is_create_mode else None
        saved: str | None = None
        if dept and self._project_root is not None:
            saved = read_create_default_dcc(self._project_root, dept)
        saved_cf = (saved or "").strip().casefold()
        for card in self._dcc_cards:
            card.set_department_default(
                bool(saved_cf and card.isEnabled() and (card.dcc_id() or "").strip().casefold() == saved_cf)
            )

    def _sync_dcc_card_grid(self) -> None:
        """Create mode: department default DCC full size on the left; others compact. Open With: uniform size."""
        if not self._dcc_cards:
            return
        if not self._is_create_mode:
            self._place_cards_in_order(self._registry_dcc_order, primary_dcc_id=None)
            return
        dept = self._create_default_context_dept()
        default_dcc: str | None = None
        if dept and self._project_root is not None:
            default_dcc = (read_create_default_dcc(self._project_root, dept) or "").strip() or None
        if default_dcc and any(
            c.isEnabled() and (c.dcc_id() or "").strip().casefold() == default_dcc.casefold()
            for c in self._dcc_cards
        ):
            order = [default_dcc] + [d for d in self._registry_dcc_order if d != default_dcc]
            self._place_cards_in_order(order, primary_dcc_id=default_dcc)
            return
        self._place_cards_in_order(self._registry_dcc_order, primary_dcc_id=None)

    def _place_cards_in_order(self, order: list[str], primary_dcc_id: str | None) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self._cards_layout.removeWidget(w)
        by_id = {c.dcc_id(): c for c in self._dcc_cards}
        placed = 0
        for dcc_id in order:
            card = by_id.get(dcc_id)
            if card is None:
                continue
            row = placed // _DCC_CARDS_PER_ROW
            col = placed % _DCC_CARDS_PER_ROW
            self._cards_layout.addWidget(card, row, col)
            placed += 1
        primary = (primary_dcc_id or "").strip() or None
        for card in self._dcc_cards:
            if primary is None:
                card.set_icon_primary(True)
            else:
                card.set_icon_primary(card.dcc_id() == primary)

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
        self._choice = OpenResolverChoice(
            department=dept,
            dcc=dcc,
            import_source=bool(self._import_source_cb.isChecked()),
        )
        self.accept()
