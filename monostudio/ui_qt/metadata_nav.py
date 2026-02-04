from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset
from monostudio.core.pipeline_types_and_presets import PipelineTypesAndPresets, TypeDef, load_pipeline_types_and_presets
from monostudio.ui_qt.style import MonosDialog, monos_font

Mode = Literal["assets", "shots"]


@dataclass(frozen=True)
class FilterItem:
    """
    Metadata-driven filter item for sidebar lists.

    - id: stable identifier used for filtering logic
    - label: UI label
    - short: small label for compact tags (optional)
    - icon_name: metadata-driven icon key (optional)
    """

    id: str
    label: str
    short: str | None = None
    icon_name: str | None = None


def filter_assets(assets: list[Asset], current_department: str | None, current_category: str | None) -> list[Asset]:
    """
    AND-only filtering for assets.
    - If department is None → allow all
    - If category is None → allow all
    """

    out: list[Asset] = []
    for a in assets:
        if current_category is not None and a.asset_type != current_category:
            continue
        if current_department is not None and not any(d.name == current_department for d in a.departments):
            continue
        out.append(a)
    return out


def _is_shot_type(type_id: str) -> bool:
    """
    Legacy compatibility: shot types are 'shot' or prefixed with 'shot_'.
    """

    return bool(type_id == "shot" or type_id.startswith("shot_"))


def _types_for_mode(meta: PipelineTypesAndPresets, mode: Mode) -> list[TypeDef]:
    items: list[TypeDef] = []
    for type_id, t in meta.types.items():
        if mode == "shots":
            if _is_shot_type(type_id):
                items.append(t)
        else:
            if not _is_shot_type(type_id):
                items.append(t)
    items.sort(key=lambda x: x.name.lower())
    return items


def _categories_for_mode(meta: PipelineTypesAndPresets, mode: Mode) -> list[FilterItem]:
    """
    Current schema maps "categories" to pipeline "types".
    (Shots mode typically yields one category: "shot".)
    """

    return [FilterItem(id=t.type_id, label=t.name, short=t.short_name) for t in _types_for_mode(meta, mode)]


def _departments_for_mode(meta: PipelineTypesAndPresets, mode: Mode, category_id: str | None) -> list[FilterItem]:
    """
    Departments are derived from the metadata file:
    - If category is selected: departments come from that category/type node.
    - Else: union of departments across all categories in the current mode.
    """

    depts: list[str] = []
    if category_id and category_id in meta.types:
        depts = list(meta.types[category_id].departments)
    else:
        seen: set[str] = set()
        for t in _types_for_mode(meta, mode):
            for d in t.departments:
                if d not in seen:
                    seen.add(d)
                    depts.append(d)
    depts = [d for d in depts if isinstance(d, str) and d.strip()]
    depts.sort(key=lambda s: s.lower())
    # No per-department display/short/icon schema yet → use id as label/short.
    return [FilterItem(id=d, label=d, short=d) for d in depts]


class PipelineMetadataWorker(QObject):
    loaded = Signal(object)  # PipelineTypesAndPresets
    failed = Signal(str)
    finished = Signal()

    def run(self) -> None:
        try:
            self.loaded.emit(load_pipeline_types_and_presets())
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self.finished.emit()


class ManageFilterDialog(MonosDialog):
    """
    Stub dialog (Phase 0): no full logic yet.
    """

    def __init__(self, *, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        msg = QLabel("Not implemented yet.", self)
        msg.setObjectName("DialogHint")
        root.addWidget(msg, 0)

        btn = QPushButton("Close", self)
        btn.clicked.connect(self.accept)
        root.addWidget(btn, 0, Qt.AlignRight)


class SidebarWidget(QWidget):
    """
    Filter control surface only:
    - Departments list (exclusive, toggle-to-none)
    - Categories list (exclusive, toggle-to-none)
    """

    departmentIntent = Signal(object)  # str|None
    categoryIntent = Signal(object)  # str|None
    manageDepartmentsRequested = Signal()
    manageCategoriesRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MetadataSidebar")

        self._active_department: str | None = None
        self._active_category: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(16)

        self._dept_title = QLabel("DEPARTMENTS", self)
        self._dept_title.setObjectName("MetadataSidebarSectionTitle")
        self._dept_title.setFont(_section_title_font())

        self._dept_list = QListWidget(self)
        self._dept_list.setObjectName("MetadataSidebarList")
        self._dept_list.setSelectionMode(QListWidget.SingleSelection)
        self._dept_list.setFocusPolicy(Qt.NoFocus)
        self._dept_list.itemClicked.connect(self._on_department_clicked)

        self._dept_add_more = QPushButton("Add more…", self)
        self._dept_add_more.setObjectName("MetadataSidebarAddMore")
        self._dept_add_more.clicked.connect(self.manageDepartmentsRequested.emit)

        self._cat_title = QLabel("CATEGORIES", self)
        self._cat_title.setObjectName("MetadataSidebarSectionTitle")
        self._cat_title.setFont(_section_title_font())

        self._cat_list = QListWidget(self)
        self._cat_list.setObjectName("MetadataSidebarList")
        self._cat_list.setSelectionMode(QListWidget.SingleSelection)
        self._cat_list.setFocusPolicy(Qt.NoFocus)
        self._cat_list.itemClicked.connect(self._on_category_clicked)

        self._cat_add_more = QPushButton("Add more…", self)
        self._cat_add_more.setObjectName("MetadataSidebarAddMore")
        self._cat_add_more.clicked.connect(self.manageCategoriesRequested.emit)

        root.addWidget(self._dept_title, 0)
        root.addWidget(self._dept_list, 1)
        root.addWidget(self._dept_add_more, 0)
        root.addSpacing(6)
        root.addWidget(self._cat_title, 0)
        root.addWidget(self._cat_list, 1)
        root.addWidget(self._cat_add_more, 0)
        root.addStretch(1)

    def set_departments(self, items: list[FilterItem]) -> None:
        self._dept_list.blockSignals(True)
        try:
            self._dept_list.clear()
            for it in items:
                row = QListWidgetItem(it.label)
                row.setData(Qt.UserRole, it.id)
                self._dept_list.addItem(row)
                if self._active_department is not None and it.id == self._active_department:
                    row.setSelected(True)
                    self._dept_list.setCurrentItem(row)
        finally:
            self._dept_list.blockSignals(False)

    def set_categories(self, items: list[FilterItem]) -> None:
        self._cat_list.blockSignals(True)
        try:
            self._cat_list.clear()
            for it in items:
                row = QListWidgetItem(it.label)
                row.setData(Qt.UserRole, it.id)
                self._cat_list.addItem(row)
                if self._active_category is not None and it.id == self._active_category:
                    row.setSelected(True)
                    self._cat_list.setCurrentItem(row)
        finally:
            self._cat_list.blockSignals(False)

    def set_active(self, *, department: str | None, category: str | None) -> None:
        self._active_department = department
        self._active_category = category
        self._sync_selection()

    def _sync_selection(self) -> None:
        self._dept_list.blockSignals(True)
        self._cat_list.blockSignals(True)
        try:
            self._dept_list.clearSelection()
            self._cat_list.clearSelection()

            if self._active_department is not None:
                for i in range(self._dept_list.count()):
                    it = self._dept_list.item(i)
                    if it and it.data(Qt.UserRole) == self._active_department:
                        it.setSelected(True)
                        self._dept_list.setCurrentItem(it)
                        break

            if self._active_category is not None:
                for i in range(self._cat_list.count()):
                    it = self._cat_list.item(i)
                    if it and it.data(Qt.UserRole) == self._active_category:
                        it.setSelected(True)
                        self._cat_list.setCurrentItem(it)
                        break
        finally:
            self._dept_list.blockSignals(False)
            self._cat_list.blockSignals(False)

    def _on_department_clicked(self, item: QListWidgetItem) -> None:
        clicked = item.data(Qt.UserRole)
        clicked_id = clicked if isinstance(clicked, str) else None
        if clicked_id is not None and clicked_id == self._active_department:
            self._active_department = None
            self._sync_selection()
            self.departmentIntent.emit(None)
            return
        self._active_department = clicked_id
        self._sync_selection()
        self.departmentIntent.emit(clicked_id)

    def _on_category_clicked(self, item: QListWidgetItem) -> None:
        clicked = item.data(Qt.UserRole)
        clicked_id = clicked if isinstance(clicked, str) else None
        if clicked_id is not None and clicked_id == self._active_category:
            self._active_category = None
            self._sync_selection()
            self.categoryIntent.emit(None)
            return
        self._active_category = clicked_id
        self._sync_selection()
        self.categoryIntent.emit(clicked_id)


class AssetCard(QFrame):
    """
    Asset Card widget (thumbnail + name + contextual filter tags).
    Tags are CONTEXT FEEDBACK and appear only when the corresponding filter is active.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AssetCard")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self._thumb = QLabel(self)
        self._thumb.setObjectName("AssetCardThumb")
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setMinimumHeight(120)
        self._thumb.setScaledContents(False)

        self._tag_left = QLabel("", self._thumb)
        self._tag_left.setObjectName("AssetCardTagLeft")
        self._tag_left.setVisible(False)

        self._tag_right = QLabel("", self._thumb)
        self._tag_right.setObjectName("AssetCardTagRight")
        self._tag_right.setVisible(False)

        self._name = QLabel("", self)
        self._name.setObjectName("AssetCardName")
        self._name.setWordWrap(False)
        self._name.setTextInteractionFlags(Qt.NoTextInteraction)

        root.addWidget(self._thumb, 0)
        root.addWidget(self._name, 0)

        self._pix: QPixmap | None = None

    def set_data(self, *, name: str, thumbnail: QPixmap | None = None) -> None:
        self._name.setText(name or "")
        self._pix = thumbnail
        if thumbnail is not None and not thumbnail.isNull():
            self._thumb.setPixmap(thumbnail)
        else:
            # Neutral placeholder (single letter).
            letter = (name.strip()[:1] if name else "").upper()
            self._thumb.setText(letter or " ")

    def set_context_tags(self, *, department_short: str | None, category_label: str | None) -> None:
        if department_short:
            self._tag_left.setText(department_short.upper())
            self._tag_left.setVisible(True)
        else:
            self._tag_left.setVisible(False)
            self._tag_left.setText("")

        if category_label:
            self._tag_right.setText(category_label.upper())
            self._tag_right.setVisible(True)
        else:
            self._tag_right.setVisible(False)
            self._tag_right.setText("")

        self._reposition_tags()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition_tags()

    def _reposition_tags(self) -> None:
        pad = 8
        if self._tag_left.isVisible():
            self._tag_left.adjustSize()
            self._tag_left.move(pad, pad)
        if self._tag_right.isVisible():
            self._tag_right.adjustSize()
            self._tag_right.move(max(pad, self._thumb.width() - self._tag_right.width() - pad), pad)


class AssetGridWidget(QWidget):
    """
    Visual representation only:
    - scrollable grid of AssetCard widgets
    - receives already-filtered assets
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AssetGrid")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(self._scroll, 1)

        self._content = QWidget(self._scroll)
        self._grid = QGridLayout(self._content)
        self._grid.setContentsMargins(16, 16, 16, 16)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        self._scroll.setWidget(self._content)

        self._cards: list[AssetCard] = []
        self._card_w = 220
        self._card_h = 180

    def set_card_size(self, *, width: int, height: int) -> None:
        self._card_w = max(160, int(width))
        self._card_h = max(140, int(height))
        for c in self._cards:
            c.setFixedSize(self._card_w, self._card_h)
        self._reflow()

    def set_assets(
        self,
        assets: list[Asset],
        *,
        active_department: str | None,
        active_category: str | None,
        category_label_by_id: dict[str, str],
    ) -> None:
        # Clear existing widgets
        for c in self._cards:
            c.setParent(None)
        self._cards = []

        for a in assets:
            card = AssetCard(self._content)
            card.setFixedSize(self._card_w, self._card_h)
            card.set_data(name=a.name, thumbnail=None)
            dept_tag = active_department if active_department else None
            cat_tag = category_label_by_id.get(active_category, "") if active_category else None
            card.set_context_tags(department_short=dept_tag, category_label=cat_tag)
            self._cards.append(card)

        self._reflow()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self) -> None:
        # Simple grid reflow based on viewport width
        try:
            vw = int(self._scroll.viewport().width())
        except Exception:
            return
        if vw <= 0:
            return
        cols = max(1, int((vw - 16) / (self._card_w + self._grid.horizontalSpacing())))

        # Remove old placements
        while self._grid.count():
            it = self._grid.takeAt(0)
            if it:
                it.widget()

        for i, card in enumerate(self._cards):
            r = i // cols
            c = i % cols
            self._grid.addWidget(card, r, c)


class AppController(QObject):
    """
    Centralized filter + metadata state.
    Owns pipeline metadata and emits changes via signals.
    """

    departmentChanged = Signal(object)  # str|None
    categoryChanged = Signal(object)  # str|None
    modeChanged = Signal(str)  # "assets"|"shots"
    metadataLoaded = Signal(object)  # PipelineTypesAndPresets
    metadataFailed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._meta: PipelineTypesAndPresets = PipelineTypesAndPresets(types={})

        self.current_department: str | None = None
        self.current_category: str | None = None
        self.current_mode: Mode = "assets"

        self._thread: QThread | None = None

    def load_metadata_async(self) -> None:
        if self._thread is not None:
            return
        worker = PipelineMetadataWorker()
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.loaded.connect(self._on_meta_loaded)
        worker.failed.connect(self.metadataFailed.emit)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_meta_thread_finished)
        self._thread = thread
        thread.start()

    def _on_meta_thread_finished(self) -> None:
        self._thread = None

    def _on_meta_loaded(self, meta_obj) -> None:
        meta = meta_obj if isinstance(meta_obj, PipelineTypesAndPresets) else PipelineTypesAndPresets(types={})
        self._meta = meta
        self.metadataLoaded.emit(meta)
        # Refresh dependent option lists.
        self._reconcile_filters()

    def set_mode(self, mode: Mode) -> None:
        if mode not in ("assets", "shots"):
            return
        if self.current_mode == mode:
            return
        self.current_mode = mode
        # When mode changes, clear filters (safe, explicit).
        self.current_department = None
        self.current_category = None
        self.modeChanged.emit(mode)
        self.departmentChanged.emit(None)
        self.categoryChanged.emit(None)

    def set_department(self, department_id: str | None) -> None:
        if department_id is not None and department_id == self.current_department:
            department_id = None
        self.current_department = department_id
        self.departmentChanged.emit(department_id)

    def set_category(self, category_id: str | None) -> None:
        if category_id is not None and category_id == self.current_category:
            category_id = None
        self.current_category = category_id
        self.categoryChanged.emit(category_id)
        # Optional: department options depend on selected category.
        self.departmentChanged.emit(self.current_department)

    def categories(self) -> list[FilterItem]:
        return _categories_for_mode(self._meta, self.current_mode)

    def departments(self) -> list[FilterItem]:
        return _departments_for_mode(self._meta, self.current_mode, self.current_category)

    def category_label_by_id(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for t in self._meta.types.values():
            out[t.type_id] = t.name
        return out

    def filter_assets(self, assets: list[Asset]) -> list[Asset]:
        return filter_assets(assets, self.current_department, self.current_category)

    def _reconcile_filters(self) -> None:
        # Drop filters that are no longer present in metadata for current mode.
        valid_categories = {c.id for c in _categories_for_mode(self._meta, self.current_mode)}
        valid_departments = {d.id for d in _departments_for_mode(self._meta, self.current_mode, self.current_category)}

        if self.current_category is not None and self.current_category not in valid_categories:
            self.current_category = None
            self.categoryChanged.emit(None)

        if self.current_department is not None and self.current_department not in valid_departments:
            self.current_department = None
            self.departmentChanged.emit(None)


def _section_title_font() -> QFont:
    f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
    f.setLetterSpacing(QFont.PercentageSpacing, 110)  # tracking-widest-ish
    return f


class MetadataNavMainWindow(QMainWindow):
    """
    Skeleton MainWindow for the metadata-driven navigation system.
    - Owns AppController (centralized filter state + metadata)
    - Connects SidebarWidget ↔ AppController ↔ AssetGridWidget via signals
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MONOS")

        self._controller = AppController(self)
        self._sidebar = SidebarWidget(self)
        self._grid = AssetGridWidget(self)

        self._assets_source: list[Asset] = []

        # Minimal top controls (mode switch) — filtering state lives in controller.
        top = QWidget(self)
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(12, 12, 12, 12)
        top_l.setSpacing(8)

        self._btn_assets = QPushButton("Assets", top)
        self._btn_assets.setCheckable(True)
        self._btn_shots = QPushButton("Shots", top)
        self._btn_shots.setCheckable(True)

        self._btn_assets.clicked.connect(self._on_mode_assets_clicked)
        self._btn_shots.clicked.connect(self._on_mode_shots_clicked)

        top_l.addWidget(self._btn_assets, 0)
        top_l.addWidget(self._btn_shots, 0)
        top_l.addStretch(1)

        center = QWidget(self)
        center.setObjectName("MetadataNavRoot")
        root = QVBoxLayout(center)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(top, 0)

        body = QWidget(center)
        body_l = QHBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(0)
        body_l.addWidget(self._sidebar, 0)
        body_l.addWidget(self._grid, 1)
        root.addWidget(body, 1)
        self.setCentralWidget(center)

        # Sidebar → controller intents
        self._sidebar.departmentIntent.connect(self._controller.set_department)
        self._sidebar.categoryIntent.connect(self._controller.set_category)
        self._sidebar.manageDepartmentsRequested.connect(self._open_manage_departments)
        self._sidebar.manageCategoriesRequested.connect(self._open_manage_categories)

        # Controller → UI updates
        self._controller.modeChanged.connect(self._sync_ui_from_state)
        self._controller.departmentChanged.connect(self._sync_ui_from_state)
        self._controller.categoryChanged.connect(self._sync_ui_from_state)
        self._controller.metadataLoaded.connect(self._sync_ui_from_state)

        # Default mode
        self._controller.set_mode("assets")
        self._controller.load_metadata_async()

    def set_assets_source(self, assets: list[Asset]) -> None:
        # View only — caller provides in-memory data.
        self._assets_source = list(assets)
        self._sync_ui_from_state()

    def _on_mode_assets_clicked(self) -> None:
        self._controller.set_mode("assets")

    def _on_mode_shots_clicked(self) -> None:
        self._controller.set_mode("shots")

    def _sync_ui_from_state(self, *_args) -> None:
        mode = self._controller.current_mode
        self._btn_assets.setChecked(mode == "assets")
        self._btn_shots.setChecked(mode == "shots")

        # Reload filter lists from metadata (metadata-driven; no hardcoded rules).
        self._sidebar.set_active(department=self._controller.current_department, category=self._controller.current_category)
        self._sidebar.set_categories(self._controller.categories())
        self._sidebar.set_departments(self._controller.departments())

        # Render already-filtered data
        filtered = self._controller.filter_assets(self._assets_source)
        self._grid.set_assets(
            filtered,
            active_department=self._controller.current_department,
            active_category=self._controller.current_category,
            category_label_by_id=self._controller.category_label_by_id(),
        )

    def _open_manage_departments(self) -> None:
        ManageFilterDialog(title="Manage Departments", parent=self).exec()

    def _open_manage_categories(self) -> None:
        ManageFilterDialog(title="Manage Categories", parent=self).exec()

