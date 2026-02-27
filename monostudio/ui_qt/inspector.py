from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal, QSize, QPoint, QTimer, QSettings, QEvent
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, Department, Shot, ProjectIndex
from monostudio.core.inbox_reader import load_inbox_destinations, resolve_destination_path
from monostudio.core.type_registry import TypeRegistry
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.dcc_status import resolve_dcc_status
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.style import MONOS_COLORS, file_icon_spec_for_path, monos_font
from monostudio.ui_qt.thumbnails import ThumbnailCache
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind, display_name_for_item

# Active DCC persistence + version parsing (cùng nguồn với main view)
def _inspector_get_active_dcc(item_path: Path | None, department: str | None) -> str | None:
    from monostudio.ui_qt.main_view import _item_active_dcc
    if not item_path or not department:
        return None
    return _item_active_dcc(item_path, department)


def _inspector_set_active_dcc(item_path: Path, department: str, dcc_id: str) -> None:
    from monostudio.ui_qt.main_view import _write_active_dcc
    _write_active_dcc(item_path, department, dcc_id)


def _work_file_version_from_path_for_inspector(path: Path | None) -> int | None:
    """Cùng logic main view: parse version từ path stem (hỗ trợ suffix như _fixNecklace)."""
    from monostudio.ui_qt.main_view import _work_file_version_from_path
    if not path:
        return None
    return _work_file_version_from_path(path)


def _inspector_diff(prev: ViewItem | None, cur: ViewItem | None) -> dict[str, bool]:
    """Shallow diff for Inspector: which fields changed. Used to update only affected sections."""
    if prev is None and cur is None:
        return {}
    if prev is None or cur is None:
        return {"item": True, "name": True, "type": True, "status": True, "thumbnail": True, "departments": True}
    if str(prev.path) != str(cur.path):
        return {"item": True, "name": True, "type": True, "status": True, "thumbnail": True, "departments": True}
    out: dict[str, bool] = {"item": False}
    out["name"] = (display_name_for_item(prev) != display_name_for_item(cur))
    out["type"] = ((prev.type_badge or "") != (cur.type_badge or ""))
    out["status"] = False
    # Thumbnail: assume changed if we are doing an incremental update (caller can force thumbnail refresh).
    out["thumbnail"] = False  # Only refresh when explicitly requested (e.g. thumbnailsChanged).
    prev_ref, cur_ref = prev.ref, cur.ref
    if isinstance(prev_ref, (Asset, Shot)) and isinstance(cur_ref, (Asset, Shot)):
        out["departments"] = len(prev_ref.departments) != len(cur_ref.departments) or any(
            p.name != c.name for p, c in zip(prev_ref.departments, cur_ref.departments)
        )
    else:
        out["departments"] = (prev_ref != cur_ref)
    return out


@dataclass(frozen=True)
class AssetShotInspectorData:
    # Spec 5.1
    name: str
    type: str
    absolute_path: str
    created_date: str = "—"
    last_modified: str = "—"


@dataclass(frozen=True)
class DepartmentInspectorData:
    # Spec 5.2 (excluding version fields, shown in Status section for Phase 2a)
    department_name: str
    work_path: str
    publish_path: str


@dataclass(frozen=True)
class DepartmentStatusData:
    work_exists: str  # "Yes" / "No"
    publish_exists: str  # "Yes" / "No"
    latest_version: str  # folder name or "—"
    version_count: str  # integer string


class InspectorPanel(QWidget):
    """
    MONOS Inspector (read-mostly):
    - Header is sticky (outside scroll)
    - Body is a vertical scroll area
    - Sections are read-only and scan-friendly
    - Data injection is via a single entrypoint: set_item(ViewItem | None)
    """

    close_requested = Signal()
    manage_departments_requested = Signal()
    paste_thumbnail_requested = Signal(object)  # emits ViewItem (asset/shot only)
    remove_thumbnail_requested = Signal(object)  # emits ViewItem (asset/shot only)
    open_folder_requested = Signal(object)  # emits ViewItem — mở folder trong explorer
    inbox_distribute_finished = Signal(list)  # list of dicts: {path, destination_id, destination_label, scope, entity_name, target_path}
    active_dcc_changed = Signal(object, str, str)  # path, department, dcc_id — đồng bộ với main view

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorPanel")
        # Ensure QSS background is painted for this container.
        self.setAttribute(Qt.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _InspectorHeader(self)
        self._header.close_clicked.connect(self.close_requested.emit)
        root.addWidget(self._header, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("InspectorScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)

        content = QWidget(self._scroll)
        content.setObjectName("InspectorContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(12, 12, 12, 12)
        self._content_layout.setSpacing(16)

        self._empty = _InspectorEmptyState()
        self._preview = _InspectorPreview()
        self._asset_status = _InspectorAssetStatusBlock()
        self._dept_pipeline = _DepartmentPipeline()
        self._tech = _TechnicalSpecs()
        self._stakeholders = _Stakeholders()

        self._dept_pipeline.manage_clicked.connect(self.manage_departments_requested.emit)
        self._dept_pipeline.department_focused.connect(self._on_department_focused)
        self._dept_pipeline.hidden_departments_changed.connect(self._on_hidden_departments_changed)
        self._preview.paste_requested.connect(self._on_paste_requested)
        self._preview.remove_requested.connect(self._on_remove_requested)
        self._show_publish: bool = False
        self._last_focused_department: str | None = None
        self._asset_status.open_folder_clicked.connect(self._on_open_folder_requested)
        self._asset_status._identity.active_dcc_changed.connect(self._on_identity_active_dcc_changed)

        self._inbox_destination = _InboxDestinationBlock()
        self._inbox_destination.distribute_finished.connect(self.inbox_distribute_finished.emit)

        self._separator = QFrame(content)
        self._separator.setFrameShape(QFrame.HLine)
        self._separator.setStyleSheet("color: #27272a; background: #27272a; max-height: 1px;")

        for w in (
            self._empty,
            self._preview,
            self._asset_status,
            self._separator,
            self._dept_pipeline,
            self._tech,
            self._stakeholders,
            self._inbox_destination,
        ):
            self._content_layout.addWidget(w, 0)

        self._content_layout.addStretch(1)
        self._inbox_destination.setVisible(False)
        self._scroll.setWidget(content)

        # ACTION card pinned below the scroll area (always visible at bottom when distributing)
        action_wrap = QWidget(self)
        action_wrap.setObjectName("InboxActionWrapper")
        aw_lay = QVBoxLayout(action_wrap)
        aw_lay.setContentsMargins(12, 8, 12, 12)
        aw_lay.setSpacing(0)
        aw_lay.addWidget(self._inbox_destination.action_card)
        self._inbox_action_wrapper = action_wrap
        self._inbox_action_wrapper.setVisible(False)
        root.addWidget(self._inbox_action_wrapper, 0)

        self._current_item: ViewItem | None = None
        self._previous_item: ViewItem | None = None
        self._thumbnail_manager: object | None = None
        self._department_label_resolver: object | None = None  # callable[[str], str] | None
        self._department_registry: object | None = None  # DepartmentRegistry | None (để biết subdepartment, display name)
        self._department_icon_map: dict[str, str] = {}  # dept_id -> lucide icon name
        self._type_short_name_map: dict[str, str] = {}  # type_id -> short_name
        self.set_item(None)

    def set_department_label_resolver(self, resolver: object | None) -> None:
        """Gán hàm dept_id -> label (từ DepartmentRegistry.get_department_label) để hiển thị tên thay ID; None để dùng id."""
        self._department_label_resolver = resolver

    def set_department_registry(self, registry: object | None) -> None:
        """Gán DepartmentRegistry: dùng cho display name + ưu tiên subdepartment trong meta."""
        self._department_registry = registry
        if registry is not None and hasattr(registry, "get_department_label"):
            self._department_label_resolver = registry.get_department_label
        else:
            self._department_label_resolver = None

    def set_department_icon_map(self, icon_map: dict[str, str]) -> None:
        self._department_icon_map = dict(icon_map) if icon_map else {}

    def set_type_short_name_map(self, m: dict[str, str]) -> None:
        self._type_short_name_map = dict(m) if m else {}

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager
        self._preview.set_thumbnail_manager(manager)

    def set_active_department(self, department: str | None) -> None:
        """Sync active department from sidebar for department-specific thumbnails."""
        self._preview.set_active_department(department)

    def set_inbox_mapping_selection(
        self,
        paths: list,
        project_root: Path | None,
        project_index: ProjectIndex | None,
    ) -> None:
        """Legacy: use set_inbox_distribute_paths. Inbox mapping list removed; distribute from tree selection."""
        self.set_inbox_distribute_paths(paths, project_root, project_index)

    def set_inbox_distribute_paths(
        self,
        paths: list,
        project_root: Path | None,
        project_index: ProjectIndex | None,
    ) -> None:
        """Inbox: tree selection → preview (first path) + block DESTINATION (all paths). Empty paths → hide block, clear preview."""
        path_list = [Path(p) for p in paths if p] if paths else []
        if not path_list:
            self._inbox_destination.setVisible(False)
            self._inbox_action_wrapper.setVisible(False)
            self._inbox_destination.set_data([], None, None)
            self.set_item(None)
            return
        self._inbox_destination.set_data(path_list, project_root, project_index)
        self._inbox_destination.setVisible(True)
        self._inbox_action_wrapper.setVisible(True)
        first = path_list[0]
        fake = ViewItem(
            kind=ViewItemKind.INBOX_ITEM,
            name=first.name,
            type_badge="",
            path=first,
            departments_count=None,
            ref=None,
        )
        self.set_item(fake)
        self._asset_status.setVisible(False)
        self._dept_pipeline.setVisible(False)
        self._tech.setVisible(False)
        self._stakeholders.setVisible(False)
        self._inbox_action_wrapper.setVisible(True)

    def set_inbox_tree_preview(self, path: Path | None) -> None:
        """Inbox/Reference: khi chọn file trong tree → hiện thumb + metadata, ẩn block distribute."""
        self._inbox_destination.setVisible(False)
        self._inbox_action_wrapper.setVisible(False)
        self._inbox_destination.set_data([], None, None)
        if not path or not path.exists():
            self.set_item(None)
            return
        fake = ViewItem(
            kind=ViewItemKind.INBOX_ITEM,
            name=path.name,
            type_badge="",
            path=path,
            departments_count=None,
            ref=None,
        )
        self.set_item(fake)

    def set_item(self, item: ViewItem | None, active_department_hint: str | None = None) -> None:
        # Diff-based: never rebuild layout. Update only changed sections; preserve scroll position.
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_inspector_update
            if enabled():
                record_inspector_update("set_item")
        except Exception:
            pass
        prev = self._current_item
        self._previous_item = prev
        self._current_item = item
        has_item = item is not None
        self._empty.setVisible(not has_item)
        for w in (self._preview, self._asset_status, self._dept_pipeline, self._tech, self._stakeholders):
            w.setVisible(has_item)
        if has_item:
            self._inbox_action_wrapper.setVisible(False)

        if item is None:
            self._empty.set_message("Select an item to view details")
            return

        # Đồng bộ department từ sidebar (active_department_hint): khi không chọn department thì None, không mặc định dept đầu
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)) and getattr(ref, "departments", None):
            hint = (active_department_hint or "").strip() or None
            hint_ok = hint and any((d.name or "").strip().casefold() == (hint or "").casefold() for d in ref.departments)
            if hint_ok:
                self._last_focused_department = hint
            else:
                # Sidebar không chọn department hoặc hint không khớp → không mặc định department đầu
                self._last_focused_department = None

        scroll_bar = self._scroll.verticalScrollBar()
        scroll_pos = scroll_bar.value() if scroll_bar else 0

        diff = _inspector_diff(prev, item)
        full_update = diff.get("item", True) or not prev or str(prev.path) != str(item.path)

        if full_update:
            self._preview.set_item(item)
            # Inbox: preview chỉ lấy height theo tỉ lệ ảnh (stretch 0)
            try:
                idx = self._content_layout.indexOf(self._preview)
                if idx >= 0:
                    self._content_layout.setStretchFactor(self._preview, 0)
            except Exception:
                pass
            self._dept_pipeline.set_item(item)
            self._asset_status.set_hidden_departments(self._dept_pipeline._hidden_departments)
            _ad = self._last_focused_department
            _ac = _inspector_get_active_dcc(getattr(item, "path", None), _ad) if item else None
            self._asset_status.set_item(item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
            self._tech.set_item(item)
            self._stakeholders.set_item(item)
        else:
            if diff.get("departments"):
                self._dept_pipeline.set_item(item)
                self._asset_status.set_hidden_departments(self._dept_pipeline._hidden_departments)
            if diff.get("name") or diff.get("type") or diff.get("departments"):
                _ad = self._last_focused_department
                _ac = _inspector_get_active_dcc(getattr(item, "path", None), _ad) if item else None
                self._asset_status.set_item(item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
            if diff.get("status"):
                self._asset_status.update_status(item)
            if diff.get("thumbnail"):
                self._preview.update_thumbnail_only()
            if diff.get("name") or diff.get("type"):
                self._tech.set_item(item)

        if scroll_bar and scroll_bar.value() != scroll_pos:
            scroll_bar.setValue(scroll_pos)

    def refresh_thumbnail(self) -> None:
        # Best-effort; safe no-op if nothing selected.
        try:
            self._preview.refresh_thumbnail()
        except Exception:
            pass

    def update_thumbnail_for_current(self) -> None:
        """Update thumbnail only for current item (e.g. after thumbnailsChanged). No layout change."""
        if self._current_item is None:
            return
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_inspector_update
            if enabled():
                record_inspector_update("update_thumbnail_only")
        except Exception:
            pass
        try:
            self._preview.update_thumbnail_only()
        except Exception:
            pass

    def _on_open_folder_requested(self) -> None:
        item = self._current_item
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)) and ref.departments and self._last_focused_department:
            dep = (self._last_focused_department or "").strip().casefold()
            for d in ref.departments:
                if (d.name or "").strip().casefold() == dep:
                    path = Path(d.publish_path) if self._show_publish else Path(d.work_path)
                    self.open_folder_requested.emit(path)
                    return
        self.open_folder_requested.emit(item.path)

    def _on_paste_requested(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.paste_thumbnail_requested.emit(item)

    def _on_remove_requested(self, item: ViewItem) -> None:
        if item is None or item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.remove_thumbnail_requested.emit(item)

    def _on_hidden_departments_changed(self, hidden: set) -> None:
        self._asset_status.set_hidden_departments(hidden)

    def _on_identity_active_dcc_changed(self, path, department: str, dcc_id: str) -> None:
        """Sync active DCC với main view: emit signal và refresh identity để version đúng DCC."""
        self.active_dcc_changed.emit(path, department, dcc_id)
        if self._current_item and getattr(self._current_item, "path", None) == path:
            self._asset_status.set_item(
                self._current_item,
                self._show_publish,
                active_department=department,
                active_dcc_id=dcc_id,
            )

    def _on_department_focused(self, department_name: str) -> None:
        """Update Tech row, status pill, and preview thumbnail with the clicked department."""
        self._last_focused_department = (department_name or "").strip() or None
        self._asset_status.set_focused_department(self._last_focused_department)
        self._preview.set_active_department(self._last_focused_department)
        self._preview.update_thumbnail_only()
        if self._current_item is not None:
            _ad = self._last_focused_department
            _ac = _inspector_get_active_dcc(getattr(self._current_item, "path", None), _ad) if self._current_item else None
            self._asset_status.set_item(self._current_item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        dep = self._last_focused_department
        if not dep:
            self._tech.set_resolved_path(None)
            return
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)) and ref.departments:
            for d in ref.departments:
                if (d.name or "").strip().casefold() == dep.casefold():
                    path = d.publish_path if self._show_publish else d.work_path
                    self._tech.set_resolved_path(path)
                    break
            else:
                self._tech.set_resolved_path(None)
        else:
            self._tech.set_resolved_path(None)

    def set_show_publish(self, show_publish: bool) -> None:
        if self._show_publish == show_publish:
            return
        self._show_publish = show_publish
        if self._current_item is not None:
            _ad = self._last_focused_department
            _ac = _inspector_get_active_dcc(getattr(self._current_item, "path", None), _ad) if self._current_item else None
            self._asset_status.set_item(self._current_item, self._show_publish, active_department=_ad, active_dcc_id=_ac)
        if self._last_focused_department and self._current_item is not None:
            self._on_department_focused(self._last_focused_department)

    # Backward compatibility (legacy call sites)
    def set_empty_state(self, _message: str | None = None) -> None:
        self.set_item(None)

    def set_asset_shot(self, data: AssetShotInspectorData) -> None:
        # Legacy: present minimal info without departments.
        fake = ViewItem(kind=ViewItemKind.ASSET, name=data.name, type_badge=data.type, path=Path(data.absolute_path))
        self.set_item(fake)

    def set_department(self, data: DepartmentInspectorData, status: DepartmentStatusData) -> None:
        fake = ViewItem(kind=ViewItemKind.DEPARTMENT, name=data.department_name, type_badge="Department", path=Path(data.publish_path))
        self.set_item(fake)


_V_RE = re.compile(r"^v(\d{3})$")


def _version_from_path(path: Path | None) -> str:
    """Rút version từ path: ưu tiên _v001 trong tên file (vd char_Zephy_01_sculpt_v002.blend → v002)."""
    if not path:
        return "—"
    name = path.name or ""
    # Tên file: ..._v002.blend hoặc ..._v002
    m = re.search(r"_v(\d{3})(?:\.\w+)?$", name) or re.search(r"_v(\d{3})\b", name)
    if m:
        return f"v{m.group(1)}"
    # Folder tên v001, v002
    if _V_RE.match(name):
        return name
    # Trong đường dẫn có segment v001/v002 (vd .../publish/v002)
    for part in path.parts:
        if _V_RE.match(part):
            return part
    return "—"


def _path_for_version(
    item: ViewItem,
    active_department: str | None = None,
    active_dcc_id: str | None = None,
) -> Path | None:
    """
    Path dùng để rút version: ưu tiên path FILE work từ dcc_work_states.
    Khi active_department + active_dcc_id cho trước: chỉ xét state (dept, dcc) đó.
    Không thì lấy version cao nhất trong department; không có thì item.path.
    """
    ref = item.ref
    if not isinstance(ref, (Asset, Shot)):
        return item.path
    states = getattr(ref, "dcc_work_states", None) or ()
    paths_with_version: list[tuple[Path, int]] = []
    dep_key = (active_department or "").strip().casefold() if active_department else None
    dcc_key = (active_dcc_id or "").strip().casefold() if active_dcc_id else None
    for key_st in states:
        if not isinstance(key_st, (tuple, list)) or len(key_st) < 2:
            continue
        dept_id = (key_st[0][0] or "").strip().casefold() if isinstance(key_st[0], (tuple, list)) and len(key_st[0]) >= 1 else ""
        dcc_id = (key_st[0][1] or "").strip().casefold() if isinstance(key_st[0], (tuple, list)) and len(key_st[0]) >= 2 else ""
        if dep_key is not None and dept_id != dep_key:
            continue
        if dcc_key is not None and dcc_id != dcc_key:
            continue
        st = key_st[1]
        wp = getattr(st, "work_file_path", None)
        if wp and isinstance(wp, Path):
            ver_str = _version_from_path(wp)
            if ver_str != "—" and _V_RE.match(ver_str):
                paths_with_version.append((wp, int(_V_RE.match(ver_str).group(1))))
    if paths_with_version:
        best = max(paths_with_version, key=lambda pv: pv[1])
        return best[0]
    return item.path


def _format_mtime(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _infer_latest_version_from_departments(depts: tuple[Department, ...]) -> str:
    best: int | None = None
    for d in depts:
        v = d.latest_publish_version or ""
        m = _V_RE.match(v)
        if not m:
            continue
        n = int(m.group(1))
        if best is None or n > best:
            best = n
    return f"v{best:03d}" if best is not None else "—"


def _status_from_department(dept: Department) -> str:
    if dept.publish_version_count > 0:
        return "READY"
    if dept.work_exists:
        return "PROGRESS"
    return "WAITING"


def _status_color(status: str) -> str:
    if status == "READY":
        return MONOS_COLORS["emerald_500"]
    if status == "PROGRESS":
        return MONOS_COLORS["amber_500"]
    if status == "BLOCKED":
        return MONOS_COLORS["red_500"]
    return MONOS_COLORS["waiting"]


class _InspectorHeader(QWidget):
    close_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel("INSPECTOR", self)
        title.setObjectName("InspectorHeaderTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        title.setFont(f)

        btn = QToolButton(self)
        btn.setObjectName("InspectorCloseButton")
        btn.setAutoRaise(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(lucide_icon("x", size=16, color_hex=MONOS_COLORS["text_label"]))
        btn.clicked.connect(self.close_clicked.emit)

        layout.addWidget(title, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        layout.addWidget(btn, 0, Qt.AlignVCenter)


class _InspectorEmptyState(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorEmpty")
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 80, 0, 0)
        l.setSpacing(8)
        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._label.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        l.addWidget(self._label, 0)
        l.addStretch(1)

    def set_message(self, text: str) -> None:
        self._label.setText(text)


class _PreviewWidget(QWidget):
    context_menu_requested = Signal(object)  # emits QPoint (global)
    image_changed = Signal(bool)  # has_image

    # Inbox: tỉ lệ theo ảnh input; Asset/Shot: 16:9
    INBOX_PREVIEW_MIN_HEIGHT = 120
    INBOX_PREVIEW_MAX_HEIGHT = 720

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorPreview")
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._pix: QPixmap | None = None
        self._has_image = False
        self._placeholder_kind: str = ""  # "asset" | "shot" | "project" for icon; else letter
        self._placeholder_letter: str = ""
        self._placeholder_file_icon: tuple[str, str] = ()  # (icon_name, color_hex) for Inbox file type
        self._inbox_mode = False  # True = Inbox (tỉ lệ theo ảnh), False = 16:9 (Asset/Shot)
        self._display_fit = False  # from path (user thumb); only used when no user override
        self._user_fit = False  # user toggle: False = fill (default), True = fit
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

    def get_user_fit(self) -> bool:
        return self._user_fit

    def set_user_fit(self, fit: bool) -> None:
        if self._user_fit == fit:
            return
        self._user_fit = fit
        self.update()

    def set_pixmap(self, pix: QPixmap | None, *, use_fit: bool = False) -> None:
        self._pix = pix
        self._has_image = bool(pix and not pix.isNull())
        self._display_fit = bool(use_fit)
        if self._has_image:
            self._placeholder_file_icon = ()
        if self._inbox_mode:
            self.updateGeometry()
        self.image_changed.emit(self._has_image)
        self.update()

    def set_placeholder_kind(self, kind: str, *, letter: str = "") -> None:
        self._placeholder_kind = (kind or "").strip().lower()
        self._placeholder_letter = (letter or "").strip()[:1].upper()
        self._placeholder_file_icon = ()
        self.updateGeometry()
        self.update()

    def set_placeholder_file_icon(self, icon_name: str, color_hex: str) -> None:
        """Inbox: hiển thị icon theo loại file (folder, file-text, box/DCC, …) khi không có thumbnail."""
        self._placeholder_file_icon = ((icon_name or "file").strip(), (color_hex or "").strip())
        self.update()

    def set_placeholder_letter(self, letter: str) -> None:
        self._placeholder_letter = (letter or "").strip()[:1].upper()
        self.update()

    def set_inbox_mode(self, on: bool) -> None:
        """Inbox: tỉ lệ theo ảnh input (heightForWidth từ pixmap); Asset/Shot: 16:9."""
        if self._inbox_mode == on:
            return
        self._inbox_mode = on
        if on:
            policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            policy.setHeightForWidth(True)
            self.setSizePolicy(policy)
            self.setMinimumHeight(self.INBOX_PREVIEW_MIN_HEIGHT)
        else:
            policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            policy.setHeightForWidth(True)
            self.setSizePolicy(policy)
            self.setMinimumHeight(0)
        self.updateGeometry()

    def heightForWidth(self, w: int) -> int:  # type: ignore[override]
        if self._inbox_mode:
            # Tỉ lệ theo ảnh đã load; chưa có ảnh thì dùng 16:9
            if self._has_image and self._pix is not None and self._pix.width() > 0:
                h = int(w * self._pix.height() / self._pix.width())
                return max(self.INBOX_PREVIEW_MIN_HEIGHT, min(h, self.INBOX_PREVIEW_MAX_HEIGHT))
            return max(self.INBOX_PREVIEW_MIN_HEIGHT, min(int(w * 9 / 16), self.INBOX_PREVIEW_MAX_HEIGHT))
        # Asset: 1:1; Shot/Project: 16:9
        if self._placeholder_kind == "asset":
            return max(1, w)
        return max(1, int(w * 9 / 16))

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return True

    def sizeHint(self) -> QSize:  # type: ignore[override]
        if self._inbox_mode:
            w = 320
            h = self.heightForWidth(w)
            return QSize(w, h)
        # Asset: 1:1; Shot/Project: 16:9
        if self._placeholder_kind == "asset":
            return QSize(320, 320)
        return QSize(320, 180)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)

            r = self.rect()
            radius = 8
            path = QPainterPath()
            path.addRoundedRect(r, radius, radius)
            p.setClipPath(path)

            # Background
            p.fillRect(r, QColor(MONOS_COLORS["content_bg"]))

            if self._has_image and self._pix is not None:
                # Inbox: fit. Asset/Shot: theo nút fill/fit (mặc định fill)
                use_fit = self._inbox_mode or self._user_fit
                if use_fit:
                    scaled = self._pix.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    x = r.x() + (r.width() - scaled.width()) // 2
                    y = r.y() + (r.height() - scaled.height()) // 2
                    p.drawPixmap(x, y, scaled)
                else:
                    scaled = self._pix.scaled(r.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    sx = max(0, (scaled.width() - r.width()) // 2)
                    sy = max(0, (scaled.height() - r.height()) // 2)
                    crop = scaled.copy(sx, sy, r.width(), r.height())
                    p.drawPixmap(r, crop)
                return

            # Placeholder: icon by kind (asset/shot/project) or letter/em-dash
            p.setClipping(False)
            p.setPen(QPen(QColor(MONOS_COLORS["border"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0, 0, -1, -1), radius, radius)

            if self._placeholder_kind in ("asset", "shot", "project"):
                icon_name = "box" if self._placeholder_kind == "asset" else "clapperboard" if self._placeholder_kind == "shot" else "layout-dashboard"
                icon = lucide_icon(icon_name, size=64, color_hex=MONOS_COLORS["text_meta"])
                src = icon.pixmap(64, 64)
                if not src.isNull():
                    x = r.x() + (r.width() - 64) // 2
                    y = r.y() + (r.height() - 64) // 2
                    p.drawPixmap(x, y, src)
                return

            if self._placeholder_file_icon:
                icon_name, color_hex = self._placeholder_file_icon
                color = color_hex or MONOS_COLORS["text_meta"]
                icon = lucide_icon(icon_name, size=64, color_hex=color)
                src = icon.pixmap(64, 64)
                if not src.isNull():
                    x = r.x() + (r.width() - 64) // 2
                    y = r.y() + (r.height() - 64) // 2
                    p.drawPixmap(x, y, src)
                return

            if self._placeholder_letter:
                p.setPen(QColor(MONOS_COLORS["text_meta"]))
                f = monos_font("Inter", 28, QFont.Weight.DemiBold)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, self._placeholder_letter)
                return

            p.setPen(QColor(MONOS_COLORS["text_meta"]))
            f = monos_font("Inter", 11, QFont.Weight.DemiBold)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, "—")
        finally:
            p.end()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        try:
            gp = event.globalPos()
        except Exception:
            gp = QPoint(0, 0)
        self.context_menu_requested.emit(gp)


def _thumb_button_style() -> str:
    return (
        "QToolButton { border: none; border-radius: 22px; background: rgba(0,0,0,0.35); } "
        "QToolButton:hover { background: rgba(0,0,0,0.55); } "
        "QToolButton:disabled { background: rgba(0,0,0,0.18); }"
    )


class _PreviewContainer(QWidget):
    """Container for thumbnail with Fill/Fit, Paste and Remove buttons at top-left."""
    paste_requested = Signal()
    remove_requested = Signal()

    _THUMB_BTN_MARGIN = 8
    _THUMB_BTN_GAP = 4
    _THUMB_BTN_SIZE = 44

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._container_layout = QVBoxLayout(self)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)
        self._w = _PreviewWidget(self)
        self._container_layout.addWidget(self._w, 0)
        self._inbox_mode = False

        self._btn_fill_fit = QToolButton(self)
        self._btn_fill_fit.setCursor(Qt.PointingHandCursor)
        self._btn_fill_fit.setIconSize(QSize(24, 24))
        self._btn_fill_fit.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_fill_fit.setStyleSheet(_thumb_button_style())
        self._btn_fill_fit.clicked.connect(self._on_fill_fit_clicked)
        self._update_fill_fit_icon()
        self._btn_fill_fit.setVisible(False)

        self._btn_paste = QToolButton(self)
        self._btn_paste.setCursor(Qt.PointingHandCursor)
        self._btn_paste.setToolTip("Paste thumbnail from clipboard")
        self._btn_paste.setIcon(lucide_icon("clipboard-paste", size=20, color_hex=MONOS_COLORS["text_label"]))
        self._btn_paste.setIconSize(QSize(24, 24))
        self._btn_paste.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_paste.setStyleSheet(_thumb_button_style())
        self._btn_paste.clicked.connect(self.paste_requested.emit)

        self._btn_remove = QToolButton(self)
        self._btn_remove.setCursor(Qt.PointingHandCursor)
        self._btn_remove.setToolTip("Remove thumbnail")
        self._btn_remove.setIcon(lucide_icon("trash-2", size=20, color_hex=MONOS_COLORS["text_label"]))
        self._btn_remove.setIconSize(QSize(24, 24))
        self._btn_remove.setFixedSize(self._THUMB_BTN_SIZE, self._THUMB_BTN_SIZE)
        self._btn_remove.setStyleSheet(_thumb_button_style())
        self._btn_remove.clicked.connect(self.remove_requested.emit)
        self._btn_remove.setVisible(False)

        self._w.image_changed.connect(self._on_preview_image_changed)

    def _update_fill_fit_icon(self) -> None:
        fit = self._w.get_user_fit()
        if fit:
            self._btn_fill_fit.setIcon(lucide_icon("crop", size=20, color_hex=MONOS_COLORS["text_label"]))
            self._btn_fill_fit.setToolTip("Fill (crop to fill)")
        else:
            self._btn_fill_fit.setIcon(lucide_icon("maximize-2", size=20, color_hex=MONOS_COLORS["text_label"]))
            self._btn_fill_fit.setToolTip("Fit (show full image)")

    def _on_fill_fit_clicked(self) -> None:
        self._w.set_user_fit(not self._w.get_user_fit())
        self._update_fill_fit_icon()

    def _on_preview_image_changed(self, has_image: bool) -> None:
        show = not self._inbox_mode and has_image
        self.set_show_fill_fit(show)
        self.set_show_remove(show)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        r = self._w.geometry()
        margin = self._THUMB_BTN_MARGIN
        gap = self._THUMB_BTN_GAP
        size = self._THUMB_BTN_SIZE
        x0 = r.x() + margin
        y0 = r.y() + margin
        self._btn_fill_fit.move(x0, y0)
        self._btn_fill_fit.raise_()
        self._btn_paste.move(x0 + size + gap, y0)
        self._btn_paste.raise_()
        self._btn_remove.move(x0 + (size + gap) * 2, y0)
        self._btn_remove.raise_()

    def set_paste_enabled(self, enabled: bool) -> None:
        on = bool(enabled)
        self._btn_paste.setEnabled(on)
        self._btn_paste.setVisible(on)

    def set_show_fill_fit(self, show: bool) -> None:
        self._btn_fill_fit.setVisible(bool(show))
        if show:
            self._update_fill_fit_icon()

    def set_show_remove(self, show: bool) -> None:
        self._btn_remove.setVisible(bool(show))

    def set_inbox_mode(self, on: bool) -> None:
        """Inbox: preview widget tự quyết height theo heightForWidth (tỉ lệ ảnh)."""
        self._inbox_mode = bool(on)
        self._container_layout.setStretchFactor(self._w, 0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        show = not self._inbox_mode and self._w._has_image
        self.set_show_fill_fit(show)
        self.set_show_remove(show)
        self.updateGeometry()


class _InspectorPreview(QWidget):
    paste_requested = Signal()
    remove_requested = Signal(object)  # emits ViewItem (asset/shot only)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thumbs = ThumbnailCache(size_px=1024)
        self._thumbnail_manager: object | None = None
        self._active_department: str | None = None
        self._item: ViewItem | None = None
        self._preview_layout = QVBoxLayout(self)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_layout.setSpacing(0)
        self._container = _PreviewContainer(self)
        self._preview_layout.addWidget(self._container, 0)
        self._container.paste_requested.connect(self.paste_requested.emit)
        self._container.remove_requested.connect(self._on_remove_requested)
        self._container._w.context_menu_requested.connect(self._open_context_menu)
        self._set_paste_enabled(False)

    def _on_remove_requested(self) -> None:
        if self._item is not None:
            self.remove_requested.emit(self._item)

    def _set_paste_enabled(self, enabled: bool) -> None:
        self._container.set_paste_enabled(enabled)

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager

    def set_active_department(self, department: str | None) -> None:
        self._active_department = (department or "").strip() or None

    def set_item(self, item: ViewItem) -> None:
        self._item = item
        can_paste = item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT)
        self._set_paste_enabled(can_paste)
        is_inbox = item.kind == ViewItemKind.INBOX_ITEM
        w = self._container._w
        w.set_inbox_mode(is_inbox)
        self._container.set_inbox_mode(is_inbox)
        self._preview_layout.setStretchFactor(self._container, 0)
        w.set_placeholder_kind(item.kind.value, letter=(display_name_for_item(item) or "").strip()[:1])
        w.set_user_fit(False)  # default fill when switching item
        w.set_pixmap(None)
        path = item.path
        asset_id = str(path)
        dept = self._active_department

        def load() -> None:
            is_inbox = getattr(self, "_item", None) and getattr(self._item, "kind", None) == ViewItemKind.INBOX_ITEM
            thumb = self._thumbs.resolve_thumbnail_file(path, department=dept)
            use_fit = thumb is not None and ".user." in str(thumb)
            # Inspector always uses local cache at 1024 (no Manager) so preview is 1024
            if thumb is None:
                if getattr(self, "_item", None) and self._item.path == path:
                    if is_inbox and path:
                        try:
                            icon_name, color_hex = file_icon_spec_for_path(path)
                            self._container._w.set_placeholder_file_icon(icon_name, color_hex)
                        except Exception:
                            pass
                    self._container._w.set_pixmap(None)
                return
            pix = self._thumbs.load_thumbnail_pixmap(thumb)
            if getattr(self, "_item", None) and self._item.path == path:
                self._container._w.set_pixmap(pix, use_fit=use_fit)

        QTimer.singleShot(0, load)

    def update_thumbnail_only(self) -> None:
        """Update thumbnail image only (e.g. after thumbnailsChanged or department change)."""
        item = self._item
        if item is None:
            return
        path = item.path
        asset_id = str(path)
        dept = self._active_department

        def load() -> None:
            is_inbox = getattr(self, "_item", None) and getattr(self._item, "kind", None) == ViewItemKind.INBOX_ITEM
            thumb = self._thumbs.resolve_thumbnail_file(path, department=dept)
            use_fit = thumb is not None and ".user." in str(thumb)
            # Inspector always uses local cache at 1024
            if thumb is None:
                if is_inbox and path:
                    try:
                        icon_name, color_hex = file_icon_spec_for_path(path)
                        self._container._w.set_placeholder_file_icon(icon_name, color_hex)
                    except Exception:
                        pass
                self._container._w.set_pixmap(None)
                return
            pix = self._thumbs.load_thumbnail_pixmap(thumb)
            self._container._w.set_pixmap(pix, use_fit=use_fit)

        QTimer.singleShot(0, load)

    def refresh_thumbnail(self) -> None:
        item = self._item
        if item is None:
            return
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is not None and hasattr(mgr, "invalidate"):
            mgr.invalidate(str(item.path), department=self._active_department)
        for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
            self._thumbs.invalidate_file(item.path / name)
        self.set_item(item)

    def _open_context_menu(self, global_pos: object) -> None:
        gp = global_pos if isinstance(global_pos, QPoint) else QPoint(0, 0)
        item = self._item
        can_paste = bool(item and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))

        menu = QMenu(self)
        act = QAction(lucide_icon("clipboard-paste", size=16, color_hex=MONOS_COLORS["text_label"]), "Paste thumbnail from Clipboard", menu)
        act.setEnabled(can_paste)
        act.triggered.connect(self.paste_requested.emit)
        menu.addAction(act)
        act_remove = QAction(lucide_icon("trash-2", size=16, color_hex=MONOS_COLORS["text_label"]), "Remove thumbnail", menu)
        act_remove.setEnabled(can_paste)
        act_remove.triggered.connect(lambda: self.remove_requested.emit(self._item) if self._item else None)
        menu.addAction(act_remove)
        menu.exec(gp)


class _IdentityBlock(QWidget):
    open_clicked = Signal()
    open_with_clicked = Signal()
    active_dcc_changed = Signal(object, str, str)  # path, department, dcc_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorIdentity")
        self._current_item: ViewItem | None = None
        self._active_department: str | None = None
        self._active_dcc_id: str | None = None
        self._show_publish = False

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        self._name = QLabel("", self)
        self._name.setObjectName("InspectorPrimaryName")
        f = monos_font("Inter", 15, QFont.Weight.DemiBold)
        self._name.setFont(f)

        meta_row = QWidget(self)
        meta_l = QHBoxLayout(meta_row)
        meta_l.setContentsMargins(0, 0, 0, 0)
        meta_l.setSpacing(6)

        self._meta_type_badge = QLabel("", self)
        self._meta_type_badge.setObjectName("InspectorTypeBadge")
        badge_font = monos_font("Inter", 10, QFont.Weight.Bold)
        self._meta_type_badge.setFont(badge_font)

        self._meta_dept_badge = QLabel("", self)
        self._meta_dept_badge.setObjectName("InspectorDeptBadge")
        dept_badge_font = monos_font("Inter", 10, QFont.Weight.Bold)
        self._meta_dept_badge.setFont(dept_badge_font)

        self._meta_version = QLabel("—", self)
        self._meta_version.setProperty("mono", True)
        self._meta_version.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        meta_l.addWidget(self._meta_type_badge, 0)
        meta_l.addWidget(self._meta_dept_badge, 0)
        meta_l.addWidget(self._meta_version, 0)
        meta_l.addStretch(1)

        l.addWidget(self._name, 0)
        l.addWidget(meta_row, 0)

        self._dcc_badges_row = QWidget(self)
        self._dcc_badges_l = QHBoxLayout(self._dcc_badges_row)
        self._dcc_badges_l.setContentsMargins(0, 4, 0, 0)
        self._dcc_badges_l.setSpacing(4)
        self._dcc_badges_row.setVisible(False)
        self._dcc_chip_buttons: list[QToolButton] = []
        self._dcc_more_label: QLabel | None = None  # "+N" khi số badge vượt giới hạn
        l.addWidget(self._dcc_badges_row, 0)

    def set_item(
        self,
        item: ViewItem,
        show_publish: bool = False,
        active_department: str | None = None,
        active_dcc_id: str | None = None,
    ) -> None:
        self._name.setText(display_name_for_item(item))
        ref = item.ref

        # Resolve short_name from pipeline metadata via parent walk
        type_short_map: dict[str, str] = {}
        registry = None
        label_resolver = None
        p = self.parent()
        while p:
            if getattr(p, "_department_registry", None) is not None:
                registry = getattr(p, "_department_registry", None)
            if getattr(p, "_department_label_resolver", None) is not None:
                label_resolver = getattr(p, "_department_label_resolver", None)
            m = getattr(p, "_type_short_name_map", None)
            if isinstance(m, dict) and m and not type_short_map:
                type_short_map = m
            if registry is not None and label_resolver is not None:
                break
            p = p.parent()

        # Type badge: prefer short_name, fall back to type_id
        if isinstance(ref, Asset) and (ref.asset_type or "").strip():
            type_id = (ref.asset_type or "").strip()
            badge_text = (type_short_map.get(type_id) or type_id).upper()
        else:
            badge_text = item.kind.value.upper()

        self._meta_type_badge.setText(badge_text)
        self._meta_type_badge.setStyleSheet(
            f"padding: 1px 6px; border-radius: 4px; "
            f"background: rgba(255,255,255,0.08); color: {MONOS_COLORS['text_label']}; "
            f"font-size: 10px;"
        )

        # Department badge: chỉ hiện khi có department đang chọn (asset/shot) hoặc item là Department
        dept_str = "—"
        show_dept_badge = False
        if isinstance(ref, Department):
            raw = ref.name or "—"
            dept_str = (_department_display_name(raw, label_resolver) if raw != "—" else "—")
            show_dept_badge = True
        elif isinstance(ref, (Asset, Shot)) and ref.departments:
            active_key = (active_department or "").strip().casefold()
            if active_key:
                dept_to_show = None
                for d in ref.departments:
                    dn = (getattr(d, "name", None) or "").strip()
                    if dn and dn.casefold() == active_key:
                        dept_to_show = d
                        break
                if dept_to_show is not None:
                    raw = dept_to_show.name or "—"
                    dept_str = _department_display_name(raw, label_resolver) if raw else "—"
                    show_dept_badge = True

        self._meta_dept_badge.setVisible(show_dept_badge)
        dept_badge_text = dept_str.replace("_", " ").title() if dept_str != "—" else "—"
        self._meta_dept_badge.setText(dept_badge_text)
        self._meta_dept_badge.setStyleSheet(
            f"padding: 1px 6px; border-radius: 4px; "
            f"background: rgba(255,255,255,0.08); color: {MONOS_COLORS['text_label']}; "
            f"font-size: 10px;"
        )

        # Version (đồng bộ với main view: theo active DCC khi có)
        self._current_item = item
        self._active_department = (active_department or "").strip() or None
        self._active_dcc_id = (active_dcc_id or "").strip() or None
        self._show_publish = show_publish

        version = "—"
        if isinstance(ref, Department):
            if ref.latest_publish_version and _V_RE.match(ref.latest_publish_version):
                version = (ref.latest_publish_version or "").upper()
        elif isinstance(ref, (Asset, Shot)):
            # Lấy trực tiếp từ main view: một nguồn duy nhất, đã hỗ trợ suffix (vd _v005_fixNecklace)
            from monostudio.ui_qt.main_view import _card_version_for_display
            version_str = _card_version_for_display(
                ref, self._active_department, show_publish, active_dcc_id=self._active_dcc_id
            )
            version = version_str if (version_str and version_str != "—") else "—"

        self._meta_version.setText(version)

        # DCC badges (chỉ khi asset/shot + department đang focus, không phải publish mode)
        self._update_dcc_badges()

    def _update_dcc_badges(self) -> None:
        """Build DCC badge chips for current item + active department; sync active state with main view."""
        while self._dcc_badges_l.count():
            it = self._dcc_badges_l.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._dcc_chip_buttons.clear()
        self._dcc_more_label = None

        item = self._current_item
        ref = getattr(item, "ref", None) if item else None
        dep = self._active_department
        if not item or not isinstance(ref, (Asset, Shot)) or self._show_publish:
            self._dcc_badges_row.setVisible(False)
            return

        try:
            reg = get_default_dcc_registry()
        except Exception:
            self._dcc_badges_row.setVisible(False)
            return

        active_key = (dep or "").strip().casefold()
        states = getattr(ref, "dcc_work_states", ()) or ()
        seen: set[tuple[str, str]] = set()
        badges: list[tuple[str, str, str]] = []  # (dcc_id, status, dept_id)

        for (dept_id, dcc_id), _state in states:
            dept_id = (dept_id or "").strip()
            dcc_id = (dcc_id or "").strip()
            if not dept_id or not dcc_id:
                continue
            if active_key and (dept_id or "").casefold() != active_key:
                continue
            if (dept_id, dcc_id) in seen:
                continue
            seen.add((dept_id, dcc_id))
            status = resolve_dcc_status(ref, dept_id, dcc_id)
            if status in ("exists", "creating"):
                badges.append((dcc_id, status, dept_id))

        for d in getattr(ref, "departments", ()) or ():
            dn = (getattr(d, "name", None) or "").strip()
            if active_key and (dn or "").casefold() != active_key:
                continue
            for dcc_id in (reg.get_available_dccs(dn) or []):
                dcc_id = (dcc_id or "").strip()
                if not dcc_id or (dn, dcc_id) in seen:
                    continue
                status = resolve_dcc_status(ref, dn, dcc_id)
                if status == "creating":
                    badges.append((dcc_id, status, dn))
                    seen.add((dn, dcc_id))

        if not badges:
            self._dcc_badges_row.setVisible(False)
            return

        # Giới hạn số badge hiển thị (khi không chọn department có thể rất nhiều)
        _MAX_DCC_BADGES_VISIBLE = 8
        badges_to_show = badges[: _MAX_DCC_BADGES_VISIBLE]
        overflow_count = len(badges) - len(badges_to_show)

        # Inspector badge: chip 32px, icon to (28px), padding tối thiểu; border active dùng rgba để giảm alpha
        chip_size = 32
        icon_size = 28
        amber_border_rgba = "rgba(251, 191, 36, 0.45)"  # amber_400 với alpha thấp
        active_dcc = self._active_dcc_id or (badges[0][0] if badges else None)
        for dcc_id, status, dept_id in badges_to_show:
            try:
                info = reg.get_dcc_info(dcc_id)
                info = info if isinstance(info, dict) else None
            except Exception:
                info = None
            dcc_name = (info or {}).get("label") if isinstance(info, dict) else None
            dcc_name = str(dcc_name).strip() if dcc_name else (dcc_id or "—")
            dept_display = (dept_id or "").replace("_", " ").strip().title() or "—"
            tooltip_text = f"{dcc_name} — {dept_display}"

            btn = QToolButton(self._dcc_badges_row)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tooltip_text)
            btn.setAutoRaise(True)
            btn.setFixedSize(chip_size, chip_size)
            btn.setProperty("dcc_id", dcc_id)
            is_active = (active_dcc or "").strip().casefold() == (dcc_id or "").strip().casefold()
            if status == "creating":
                btn.setText("…")
                btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
                btn.setStyleSheet(
                    "border-radius: 16px; border: 1px solid %s; color: %s; font-size: 12px;"
                    % (MONOS_COLORS["text_meta"], MONOS_COLORS["text_meta"])
                )
            else:
                slug = (info or {}).get("brand_icon_slug") if isinstance(info, dict) else None
                color = (info or {}).get("brand_color_hex") if isinstance(info, dict) else None
                if isinstance(slug, str) and slug.strip():
                    icon = brand_icon(slug.strip(), size=icon_size, color_hex=color if isinstance(color, str) else None)
                else:
                    icon = lucide_icon("layers", size=icon_size, color_hex=MONOS_COLORS["text_label"])
                btn.setIcon(icon)
                btn.setIconSize(QSize(icon_size, icon_size))
                btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
                if is_active:
                    btn.setStyleSheet(
                        "border-radius: 16px; border: 2px solid %s; background-color: #66000000; padding: 4px; margin: 0;"
                        % amber_border_rgba
                    )
                else:
                    btn.setStyleSheet(
                        "border-radius: 16px; border: 1px solid transparent; background-color: #0Fffffff; padding: 4px; margin: 0;"
                    )
            btn.clicked.connect(lambda checked=False, did=dcc_id: self._on_dcc_badge_clicked(did))
            btn.installEventFilter(self)
            self._dcc_badges_l.addWidget(btn, 0)
            self._dcc_chip_buttons.append(btn)

        # "+N" khi vượt quá _MAX_DCC_BADGES_VISIBLE; tooltip liệt kê các DCC còn lại
        if overflow_count > 0:
            more_lines = []
            for dcc_id, _status, dept_id in badges[_MAX_DCC_BADGES_VISIBLE:]:
                try:
                    info = reg.get_dcc_info(dcc_id)
                    dcc_name = (info or {}).get("label", dcc_id) if isinstance(info, dict) else dcc_id
                except Exception:
                    dcc_name = dcc_id
                dept_display = (dept_id or "").replace("_", " ").strip().title() or "—"
                more_lines.append(f"{dcc_name} — {dept_display}")
            self._dcc_more_label = QLabel(f"+{overflow_count}", self._dcc_badges_row)
            self._dcc_more_label.setStyleSheet(
                f"color: {MONOS_COLORS['text_meta']}; font-size: 11px; padding: 0 4px;"
            )
            self._dcc_more_label.setToolTip("\n".join(more_lines) if more_lines else f"+{overflow_count} more")
            self._dcc_badges_l.addWidget(self._dcc_more_label, 0)

        self._dcc_badges_l.addStretch(1)
        self._dcc_badges_row.setVisible(True)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        """Hiện tooltip giống MainView: QToolTip.showText(globalPos) thay vì tooltip mặc định."""
        if event.type() == QEvent.ToolTip and watched in self._dcc_chip_buttons:
            pos = getattr(event, "globalPos", None)
            if callable(pos):
                pos = pos()
            if pos is None:
                pos = watched.mapToGlobal(watched.rect().center())
            text = (watched.toolTip() or "").strip()
            if text:
                QToolTip.showText(pos, text)
            return True
        return super().eventFilter(watched, event)

    def _on_dcc_badge_clicked(self, dcc_id: str) -> None:
        if not self._current_item or not self._active_department:
            return
        path = getattr(self._current_item, "path", None)
        if not path:
            return
        _inspector_set_active_dcc(path, self._active_department, dcc_id)
        self._active_dcc_id = dcc_id
        self.active_dcc_changed.emit(path, self._active_department, dcc_id)
        self._update_dcc_badges()


class _InspectorAssetStatusBlock(QWidget):
    """One container: row1 = Asset info (name+meta) | Status pill; row2 = Open folder."""
    open_folder_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorAssetStatusBlock")
        self._last_show_publish = False
        self._last_active_department: str | None = None
        self._last_active_dcc_id: str | None = None
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        row1 = QWidget(self)
        row1_l = QHBoxLayout(row1)
        row1_l.setContentsMargins(0, 0, 0, 0)
        row1_l.setSpacing(12)
        self._identity = _IdentityBlock(self)
        self._health = _ProductionHealth(self)
        row1_l.addWidget(self._identity, 1)
        row1_l.addWidget(self._health, 0, Qt.AlignVCenter)

        row2 = QWidget(self)
        row2_l = QHBoxLayout(row2)
        row2_l.setContentsMargins(0, 0, 0, 0)
        row2_l.setSpacing(8)
        self._btn_open_folder = QToolButton(row2)
        self._btn_open_folder.setText("Open folder")
        self._btn_open_folder.setCursor(Qt.PointingHandCursor)
        self._btn_open_folder.setAutoRaise(True)
        self._btn_open_folder.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._btn_open_folder.setIcon(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_open_folder.clicked.connect(self._on_open_folder_clicked)
        row2_l.addWidget(self._btn_open_folder, 0)
        row2_l.addStretch(1)

        l.addWidget(row1, 0)
        l.addWidget(row2, 0)

    def _on_open_folder_clicked(self) -> None:
        self.open_folder_clicked.emit()

    def set_item(
        self,
        item: ViewItem,
        show_publish: bool = False,
        active_department: str | None = None,
        active_dcc_id: str | None = None,
    ) -> None:
        self._last_show_publish = show_publish
        self._last_active_department = active_department
        self._last_active_dcc_id = active_dcc_id
        self._identity.set_item(item, show_publish, active_department=active_department, active_dcc_id=active_dcc_id)
        self._health.set_item(item)
        is_asset_or_shot = bool(item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))
        self._btn_open_folder.setEnabled(is_asset_or_shot)

    def set_focused_department(self, dept_name: str | None) -> None:
        self._health.set_focused_department(dept_name)

    def set_hidden_departments(self, hidden: set[str]) -> None:
        self._health.set_hidden_departments(hidden)

    def update_identity(self, item: ViewItem) -> None:
        self._identity.set_item(
            item,
            self._last_show_publish,
            active_department=self._last_active_department,
            active_dcc_id=self._last_active_dcc_id,
        )

    def update_status(self, item: ViewItem) -> None:
        self._health.set_item(item)


class _MiniInfoCard(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorMiniCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(6)

        hdr = QLabel(title, self)
        hdr.setObjectName("InspectorMiniCardTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        hdr.setFont(f)
        hdr.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._body = QWidget(self)
        self._body_l = QHBoxLayout(self._body)
        self._body_l.setContentsMargins(0, 0, 0, 0)
        self._body_l.setSpacing(8)

        l.addWidget(hdr, 0)
        l.addWidget(self._body, 0)


class _ProductionHealth(QWidget):
    """Read-only status pill (computed from department data)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorProductionHealth")
        self._current_item: ViewItem | None = None
        self._focused_department: str | None = None
        self._hidden_departments: set[str] = set()

        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)

        self._pill = QLabel("", self)
        self._pill.setObjectName("InspectorStatusPillOverall")
        pill_font = monos_font("Inter", 9, QFont.Weight.DemiBold)
        self._pill.setFont(pill_font)
        l.addWidget(self._pill, 0, Qt.AlignVCenter)
        l.addStretch(1)

    def set_focused_department(self, dept_name: str | None) -> None:
        self._focused_department = (dept_name or "").strip() or None
        if self._current_item is not None:
            self._refresh()

    def set_hidden_departments(self, hidden: set[str]) -> None:
        self._hidden_departments = set(hidden)
        if self._current_item is not None:
            self._refresh()

    def set_item(self, item: ViewItem) -> None:
        self._current_item = item
        self._refresh()

    def _refresh(self) -> None:
        item = self._current_item
        if item is None:
            self._pill.setVisible(False)
            return
        status = "WAITING"
        ref = item.ref
        if isinstance(ref, Department):
            status = _status_from_department(ref)
            self._pill.setVisible(True)
        elif isinstance(ref, (Asset, Shot)):
            dep = (self._focused_department or "").strip()
            if dep:
                for d in ref.departments:
                    if (d.name or "").strip() == dep:
                        status = _status_from_department(d)
                        break
            else:
                visible = [d for d in ref.departments if (d.name or "") not in self._hidden_departments]
                if not visible:
                    visible = list(ref.departments)
                if visible and all(d.publish_version_count > 0 for d in visible):
                    status = "READY"
                elif any(d.work_exists for d in visible):
                    status = "PROGRESS"
                else:
                    status = "WAITING"
            self._pill.setVisible(True)
        else:
            self._pill.setVisible(False)
            return

        color = _status_color(status)
        display = status if status.isupper() else status.upper()
        if len(display) >= 2:
            display = display[0] + display[1:].lower()
        self._pill.setText(display)
        self._pill.setStyleSheet(
            f"padding: 1px 6px; border-radius: 8px; border: none; "
            f"background: rgba(255,255,255,0.06); color: {color}; "
            f"font-size: 10px;"
        )


class _DeptCard(QFrame):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDeptCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)

        l = QVBoxLayout(self)
        l.setContentsMargins(8, 4, 8, 4)
        l.setSpacing(0)

        row = QWidget(self)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(6)

        self._icon_label = QLabel("", self)
        self._icon_label.setFixedSize(14, 14)
        self._icon_label.setAlignment(Qt.AlignCenter)

        self._name = QLabel("", self)
        self._name.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        f = monos_font("Inter", 11, QFont.Weight.Medium)
        self._name.setFont(f)

        self._pill = QLabel("", self)
        self._pill.setObjectName("InspectorStatusPill")
        pill_font = monos_font("Inter", 9, QFont.Weight.DemiBold)
        self._pill.setFont(pill_font)
        self._pill.setStyleSheet(
            f"padding: 1px 6px; border-radius: 8px; border: none; background: rgba(255,255,255,0.06); color: {MONOS_COLORS['text_label']};"
        )

        self._btn_open = QToolButton(self)
        self._btn_open.setAutoRaise(True)
        self._btn_open.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_open.setFixedSize(20, 20)
        self._btn_open.setIconSize(QSize(14, 14))
        self._btn_open.setIcon(lucide_icon("folder-open", size=14, color_hex=MONOS_COLORS["text_label"]))
        self._btn_open.setToolTip("Open folder")
        self._btn_open.setStyleSheet("QToolButton { border: none; background: transparent; }")

        row_l.addWidget(self._icon_label, 0, Qt.AlignVCenter)
        row_l.addWidget(self._name, 1)
        row_l.addWidget(self._pill, 0, Qt.AlignVCenter)
        row_l.addWidget(self._btn_open, 0, Qt.AlignVCenter)

        l.addWidget(row, 0)

        self._dept: Department | None = None
        self._btn_open.clicked.connect(self._open_folder)

    def set_department(self, dept: Department, display_name: str | None = None, icon_name: str | None = None) -> None:
        self._dept = dept
        raw = (display_name or dept.name or "").strip()
        text = raw.replace("_", " ").title() if raw else ""
        self._name.setText(text)
        ico = lucide_icon((icon_name or "").strip() or "layers", size=14, color_hex=MONOS_COLORS["text_label"])
        self._icon_label.setPixmap(ico.pixmap(14, 14))
        status = _status_from_department(dept)
        self._pill.setText(status)
        self._pill.setStyleSheet(
            f"padding: 1px 6px; border-radius: 8px; border: none; background: rgba(255,255,255,0.06); color: {_status_color(status)};"
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # Clicking the card updates Tech row with this department's work path.
        if event and getattr(event, "button", lambda: None)() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def _open_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        if self._dept is None:
            return
        try:
            if not self._dept.path.exists():
                return
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._dept.path)))


_MAX_DEPT_CARDS = 32
_MAX_DEPT_SECTIONS = 16


def _department_display_name(dept_id: str, label_resolver: object | None) -> str:
    """Display name: label from registry if available, else dept_id; title-case."""
    if callable(label_resolver):
        label = label_resolver(dept_id)
        return (label or dept_id or "").strip()
    return (dept_id or "").strip()


class _DepartmentPipeline(QWidget):
    manage_clicked = Signal()
    department_focused = Signal(str)
    hidden_departments_changed = Signal(set)

    _SETTINGS_KEY_HIDDEN = "inspector/hidden_departments"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDepartmentPipeline")
        self._settings = QSettings()
        self._hidden_departments: set[str] = set(self._load_hidden())
        self._current_all_dept_ids: list[str] = []
        self._current_item: ViewItem | None = None

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        hdr = QWidget(self)
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        hdr_l.setSpacing(8)

        title = QLabel("DEPARTMENT PIPELINE", self)
        title.setObjectName("InspectorSectionTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._manage_btn = QToolButton(self)
        self._manage_btn.setObjectName("InspectorManageButton")
        self._manage_btn.setText("MANAGE")
        self._manage_btn.setAutoRaise(True)
        self._manage_btn.setCursor(Qt.PointingHandCursor)
        self._manage_btn.setToolTip("Toggle department visibility")
        self._manage_btn.clicked.connect(self._show_manage_menu)

        hdr_l.addWidget(title, 1)
        hdr_l.addWidget(self._manage_btn, 0, Qt.AlignRight)

        self._list = QWidget(self)
        self._list_l = QVBoxLayout(self._list)
        self._list_l.setContentsMargins(0, 0, 0, 0)
        self._list_l.setSpacing(4)

        self._section_titles: list[QLabel] = []
        for _ in range(_MAX_DEPT_SECTIONS):
            lbl = QLabel(self._list)
            lbl.setObjectName("InspectorSectionTitle")
            f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
            lbl.setVisible(False)
            self._section_titles.append(lbl)

        self._dept_cards: list[_DeptCard] = []
        self._dept_card_slots: list[object] = []
        for _ in range(_MAX_DEPT_CARDS):
            card = _DeptCard(self._list)
            card.setVisible(False)
            self._dept_cards.append(card)
            self._dept_card_slots.append(None)

        self._empty = QLabel("—", self)
        self._empty.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        l.addWidget(hdr, 0)
        l.addWidget(self._list, 0)
        l.addWidget(self._empty, 0)

    def _load_hidden(self) -> list[str]:
        raw = QSettings().value(self._SETTINGS_KEY_HIDDEN, [], list)
        return [s for s in raw if isinstance(s, str) and s.strip()]

    def _save_hidden(self) -> None:
        self._settings.setValue(self._SETTINGS_KEY_HIDDEN, sorted(self._hidden_departments))

    def _show_manage_menu(self) -> None:
        if not self._current_all_dept_ids:
            return
        from PySide6.QtWidgets import QDialog
        from monostudio.ui_qt.sidebar import _FilterPickDialog

        icon_map = self._resolve_icon_map()
        label_resolver = self._resolve_label()
        dept_parent = self._resolve_dept_parent()
        dept_label_by_id = self._resolve_dept_label_map()

        items: list[tuple[str, str, str | None]] = []
        for dept_id in self._current_all_dept_ids:
            display = _department_display_name(dept_id, label_resolver)
            icon_name = icon_map.get(dept_id)
            items.append((dept_id, display, icon_name))

        visible = set(self._current_all_dept_ids) - self._hidden_departments

        dlg = _FilterPickDialog(
            title="Select Departments",
            items=items,
            selected=visible,
            max_selected=None,
            parent=self,
            dept_parent=dept_parent,
            dept_label_by_id=dept_label_by_id,
            list_min_height_px=580,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        picked = set(dlg.selected_items())
        self._hidden_departments = set(self._current_all_dept_ids) - picked
        self._save_hidden()
        self.hidden_departments_changed.emit(self._hidden_departments)
        if self._current_item is not None:
            self.set_item(self._current_item)

    def _resolve_icon_map(self) -> dict[str, str]:
        p = self.parent()
        while p:
            m = getattr(p, "_department_icon_map", None)
            if isinstance(m, dict) and m:
                return m
            p = p.parent()
        return {}

    def _resolve_label(self) -> object | None:
        p = self.parent()
        while p:
            r = getattr(p, "_department_label_resolver", None)
            if r is not None:
                return r
            p = p.parent()
        return None

    def _resolve_registry(self) -> object | None:
        p = self.parent()
        while p:
            r = getattr(p, "_department_registry", None)
            if r is not None:
                return r
            p = p.parent()
        return None

    def _resolve_dept_parent(self) -> dict[str, str]:
        registry = self._resolve_registry()
        if registry is None or not hasattr(registry, "get_parent"):
            return {}
        out: dict[str, str] = {}
        for dept_id in self._current_all_dept_ids:
            parent = registry.get_parent(dept_id)
            if parent:
                out[dept_id] = parent
        return out

    def _resolve_dept_label_map(self) -> dict[str, str]:
        resolver = self._resolve_label()
        if not callable(resolver):
            return {}
        out: dict[str, str] = {}
        for dept_id in self._current_all_dept_ids:
            label = resolver(dept_id)
            if label:
                out[dept_id] = label
        registry = self._resolve_registry()
        if registry and hasattr(registry, "get_parent"):
            for dept_id in self._current_all_dept_ids:
                parent = registry.get_parent(dept_id)
                if parent and parent not in out:
                    label = resolver(parent)
                    if label:
                        out[parent] = label
        return out

    def set_item(self, item: ViewItem) -> None:
        self._current_item = item
        ref = item.ref
        if isinstance(ref, Department):
            depts = (ref,)
        elif isinstance(ref, (Asset, Shot)):
            depts = ref.departments
        else:
            depts = ()

        registry = None
        label_resolver = None
        icon_map: dict[str, str] = {}
        p = self.parent()
        while p:
            if getattr(p, "_department_registry", None) is not None:
                registry = getattr(p, "_department_registry", None)
            if getattr(p, "_department_label_resolver", None) is not None:
                label_resolver = getattr(p, "_department_label_resolver", None)
            m = getattr(p, "_department_icon_map", None)
            if isinstance(m, dict) and m and not icon_map:
                icon_map = m
            if registry is not None and label_resolver is not None:
                break
            p = p.parent()

        if not depts:
            self._current_all_dept_ids = []
            while self._list_l.count():
                self._list_l.takeAt(0)
            for c in self._dept_cards:
                c.setVisible(False)
            for s in self._section_titles:
                s.setVisible(False)
            self._empty.setVisible(True)
            return

        self._empty.setVisible(False)

        if registry and hasattr(registry, "get_departments"):
            ordered_ids = list(registry.get_departments())
            dept_by_id = {d.name: d for d in depts}
            ordered_depts = [dept_by_id[dept_id] for dept_id in ordered_ids if dept_id in dept_by_id]
        else:
            ordered_depts = list(depts)

        self._current_all_dept_ids = [d.name for d in ordered_depts if d.name]

        visible_depts = [d for d in ordered_depts if d.name not in self._hidden_departments]

        rows: list[tuple[str, object]] = []
        sections_emitted: set[str] = set()
        for d in visible_depts:
            dept_id = d.name or ""
            parent_id = registry.get_parent(dept_id) if registry and hasattr(registry, "get_parent") else None
            if parent_id and parent_id not in sections_emitted:
                parent_label = (registry.get_department_label(parent_id) or parent_id).strip().upper()
                rows.append(("section", parent_label))
                sections_emitted.add(parent_id)
            rows.append(("dept", d))

        for i, card in enumerate(self._dept_cards):
            slot = self._dept_card_slots[i] if i < len(self._dept_card_slots) else None
            if slot is not None:
                try:
                    card.clicked.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
                self._dept_card_slots[i] = None

        while self._list_l.count():
            self._list_l.takeAt(0)

        section_idx = 0
        card_idx = 0
        for typ, data in rows:
            if typ == "section":
                if section_idx >= len(self._section_titles):
                    break
                w = self._section_titles[section_idx]
                w.setText(str(data))
                w.setVisible(True)
                self._list_l.addWidget(w, 0)
                section_idx += 1
            else:
                if card_idx >= len(self._dept_cards):
                    break
                d = data
                card = self._dept_cards[card_idx]
                display_name = _department_display_name(d.name or "", label_resolver)
                dept_icon_name = icon_map.get(d.name or "", "layers")
                card.set_department(d, display_name, dept_icon_name)
                card.setVisible(True)
                dept_name = d.name

                def _emit(dept: str) -> None:
                    if dept:
                        self.department_focused.emit(dept)

                slot = lambda _d=dept_name: _emit(_d)
                card.clicked.connect(slot)
                if card_idx < len(self._dept_card_slots):
                    self._dept_card_slots[card_idx] = slot
                self._list_l.addWidget(card, 0)
                card_idx += 1

        for i in range(section_idx, len(self._section_titles)):
            self._section_titles[i].setVisible(False)
        for i in range(card_idx, len(self._dept_cards)):
            self._dept_cards[i].setVisible(False)


class _TechRow(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)
        self._k = QLabel(label, self)
        self._k.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._v = QLabel("—", self)
        self._v.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        l.addWidget(self._k, 1)
        l.addWidget(self._v, 0, Qt.AlignRight)

    def set_value(self, text: str) -> None:
        self._v.setText(text or "—")


class _TechnicalSpecs(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorTechnicalSpecs")
        self._last_item: ViewItem | None = None
        self._resolved_path: Path | None = None

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        title = QLabel("TECHNICAL SPECS", self)
        title.setObjectName("InspectorSectionTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._frame = _TechRow("Frame Range", self)
        self._fps = _TechRow("FPS", self)
        self._res = _TechRow("Resolution", self)
        self._modified = _TechRow("Last Modified", self)

        # Source Directory (monospace, copyable)
        src_row = QWidget(self)
        src_l = QHBoxLayout(src_row)
        src_l.setContentsMargins(0, 0, 0, 0)
        src_l.setSpacing(8)
        k = QLabel("Source Directory", self)
        k.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._src = QLineEdit(self)
        self._src.setReadOnly(True)
        self._src.setProperty("mono", True)
        self._src.setStyleSheet("padding: 6px 8px;")
        btn_copy = QToolButton(self)
        btn_copy.setAutoRaise(True)
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.setIcon(lucide_icon("copy", size=16, color_hex=MONOS_COLORS["text_label"]))
        btn_copy.setToolTip("Copy")
        btn_copy.clicked.connect(lambda: self._copy_text(self._src.text()))

        src_l.addWidget(k, 1)
        src_l.addWidget(self._src, 0)
        src_l.addWidget(btn_copy, 0)

        l.addWidget(title, 0)
        l.addWidget(self._frame, 0)
        l.addWidget(self._fps, 0)
        l.addWidget(self._res, 0)
        l.addWidget(src_row, 0)
        l.addWidget(self._modified, 0)

    def set_item(self, item: ViewItem) -> None:
        self._last_item = item
        self._resolved_path = None
        self._frame.set_value("—")
        self._fps.set_value("—")
        self._res.set_value("—")
        self._src.setText(str(item.path))
        self._modified.set_value(_format_mtime(item.path))

    def set_resolved_path(self, path: Path | None) -> None:
        """Update displayed path to a department work path (or reset to item path when None)."""
        self._resolved_path = path
        if path is not None:
            self._src.setText(str(path))
            self._modified.set_value(_format_mtime(path))
        elif self._last_item is not None:
            self._src.setText(str(self._last_item.path))
            self._modified.set_value(_format_mtime(self._last_item.path))

    @staticmethod
    def _copy_text(text: str) -> None:
        if not text:
            return
        cb = QApplication.clipboard()
        if cb is None:
            return
        cb.setText(text)


class _Stakeholders(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorStakeholders")
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)

        btn = QToolButton(self)
        btn.setText("STAKEHOLDERS")
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setArrowType(Qt.RightArrow)
        btn.setAutoRaise(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"color: {MONOS_COLORS['text_meta']}; font-weight: 800; font-size: 10px;")

        self._content = QLabel("—", self)
        self._content.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._content.setVisible(False)

        def sync() -> None:
            open_ = btn.isChecked()
            btn.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
            self._content.setVisible(open_)

        btn.toggled.connect(lambda _checked: sync())
        sync()

        l.addWidget(btn, 0)
        l.addWidget(self._content, 0)

    def set_item(self, _item: ViewItem) -> None:
        # No stakeholders data in current model.
        self._content.setText("—")


# Scope: user chọn trước (global / asset / shot), sau đó destination (script, texture, ...), cuối cùng entity.
_INBOX_SCOPE_PROJECT = "project"
_INBOX_SCOPE_ASSET = "asset"
_INBOX_SCOPE_SHOT = "shot"


class _InboxDestinationBlock(QWidget):
    """Flow: Scope (Global | Asset | Shot) → Destination (icon+label list) → Type → Entity."""

    distribute_finished = Signal(object)  # list[Path] đã distribute

    _SCOPE_ITEMS: list[tuple[str, str, str]] = [
        ("Global", _INBOX_SCOPE_PROJECT, "layers"),
        ("Asset", _INBOX_SCOPE_ASSET, "box"),
        ("Shot", _INBOX_SCOPE_SHOT, "clapperboard"),
    ]

    _DEST_ICON_MAP: dict[str, str] = {
        "global_reference": "eye",
        "reference_script": "file-text",
        "reference_storyboard": "layout-dashboard",
        "reference_guideline": "library",
        "reference_concept": "lightbulb",
        "reference": "eye",
        "concept": "lightbulb",
        "texture": "palette",
        "character_sculpt": "bone",
        "shot_reference": "clapperboard",
    }

    _TYPE_ICON_MAP: dict[str, str] = {
        "character": "user",
        "prop": "package",
        "environment": "trees",
        "vehicle": "car",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InboxDestinationBlock")
        self._paths: list[Path] = []
        self._project_root: Path | None = None
        self._project_index: ProjectIndex | None = None
        self._type_reg: TypeRegistry | None = None
        self._dept_reg: DepartmentRegistry | None = None
        self._destinations: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        title = QLabel("DESTINATION", self)
        title.setObjectName("InboxDestinationTitle")
        f = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112.0)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        root.addWidget(title, 0)

        # ── Card WHERE: Scope (toggle buttons) + Destination (selectable list) ──
        card_where = QFrame(self)
        card_where.setObjectName("InboxDestCardWhere")
        card_where.setFrameShape(QFrame.NoFrame)
        card_where.setAttribute(Qt.WA_StyledBackground, True)
        lw = QVBoxLayout(card_where)
        lw.setContentsMargins(12, 12, 12, 12)
        lw.setSpacing(8)

        title_where = QLabel("WHERE", card_where)
        title_where.setObjectName("InboxDestCardTitle")
        lw.addWidget(title_where, 0)

        lw.addWidget(self._make_section_label("Scope", "layers", card_where), 0)
        scope_row = QWidget(card_where)
        scope_lay = QHBoxLayout(scope_row)
        scope_lay.setContentsMargins(0, 0, 0, 0)
        scope_lay.setSpacing(4)
        self._scope_group = QButtonGroup(self)
        self._scope_group.setExclusive(True)
        for label, data, icon_name in self._SCOPE_ITEMS:
            btn = QPushButton(label, scope_row)
            btn.setObjectName("InboxScopeButton")
            btn.setIcon(lucide_icon(icon_name, size=14))
            btn.setCheckable(True)
            btn.setProperty("item_data", data)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._scope_group.addButton(btn)
            scope_lay.addWidget(btn)
        scope_lay.addStretch(1)
        self._scope_group.buttons()[0].setChecked(True)
        lw.addWidget(scope_row, 0)

        lw.addWidget(self._make_section_label("Destination", "folder-open", card_where), 0)
        self._dest_container = QWidget(card_where)
        self._dest_layout = QVBoxLayout(self._dest_container)
        self._dest_layout.setContentsMargins(0, 0, 0, 0)
        self._dest_layout.setSpacing(2)
        self._dest_group = QButtonGroup(self)
        self._dest_group.setExclusive(True)
        lw.addWidget(self._dest_container, 0)

        root.addWidget(card_where, 0)

        # ── Card TARGET: Type (selectable list, asset-only) + Entity (combo) ──
        card_target = QFrame(self)
        card_target.setObjectName("InboxDestCardTarget")
        card_target.setFrameShape(QFrame.NoFrame)
        card_target.setAttribute(Qt.WA_StyledBackground, True)
        lt = QVBoxLayout(card_target)
        lt.setContentsMargins(12, 12, 12, 12)
        lt.setSpacing(8)

        title_target = QLabel("TARGET", card_target)
        title_target.setObjectName("InboxDestCardTitle")
        lt.addWidget(title_target, 0)

        self._type_section = QWidget(card_target)
        ts_lay = QVBoxLayout(self._type_section)
        ts_lay.setContentsMargins(0, 0, 0, 0)
        ts_lay.setSpacing(4)
        ts_lay.addWidget(self._make_section_label("Type", "box", self._type_section))
        self._type_container = QWidget(self._type_section)
        self._type_layout = QVBoxLayout(self._type_container)
        self._type_layout.setContentsMargins(0, 0, 0, 0)
        self._type_layout.setSpacing(2)
        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        ts_lay.addWidget(self._type_container)
        lt.addWidget(self._type_section, 0)

        entity_l = QHBoxLayout()
        entity_l.addWidget(self._make_section_label("Entity", "package", card_target), 0)
        self._entity_combo = QComboBox(card_target)
        self._entity_combo.setObjectName("InboxEntityCombo")
        entity_l.addWidget(self._entity_combo, 1)
        lt.addLayout(entity_l, 0)

        root.addWidget(card_target, 0)

        # ── Card ACTION — placed externally by InspectorPanel (bottom-pinned) ──
        self._card_action = QFrame()
        self._card_action.setObjectName("InboxDestCardAction")
        self._card_action.setFrameShape(QFrame.NoFrame)
        self._card_action.setAttribute(Qt.WA_StyledBackground, True)
        la = QVBoxLayout(self._card_action)
        la.setContentsMargins(12, 12, 12, 12)
        la.setSpacing(8)
        title_action = QLabel("ACTION", self._card_action)
        title_action.setObjectName("InboxDestCardTitle")
        la.addWidget(title_action, 0)
        copy_move_l = QHBoxLayout()
        self._copy_radio = QRadioButton("Copy", self._card_action)
        self._move_radio = QRadioButton("Move", self._card_action)
        self._copy_radio.setChecked(True)
        copy_move_grp = QButtonGroup(self)
        copy_move_grp.addButton(self._copy_radio)
        copy_move_grp.addButton(self._move_radio)
        copy_move_l.addWidget(self._copy_radio, 0)
        copy_move_l.addWidget(self._move_radio, 0)
        copy_move_l.addStretch(1)
        la.addLayout(copy_move_l, 0)
        self._distribute_btn = QPushButton("Distribute", self._card_action)
        self._distribute_btn.setObjectName("InboxDistributeButton")
        self._distribute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._distribute_btn.clicked.connect(self._on_distribute)
        la.addWidget(self._distribute_btn, 0)

        # ── Signals ──
        self._scope_group.buttonClicked.connect(lambda _btn: self._on_scope_selection_changed())
        self._dest_group.buttonClicked.connect(lambda _btn: self._update_distribute_enabled())
        self._type_group.buttonClicked.connect(lambda _btn: self._refill_entity_combo())
        self._entity_combo.currentIndexChanged.connect(self._update_distribute_enabled)

    # ── helpers ──

    @property
    def action_card(self) -> QFrame:
        """The ACTION card widget (Copy/Move + Distribute). Placed externally by InspectorPanel."""
        return self._card_action

    @staticmethod
    def _make_section_label(text: str, icon_name: str, parent: QWidget) -> QWidget:
        """Build an [icon] Label widget for a section header."""
        w = QWidget(parent)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        ic = QLabel(w)
        ic.setPixmap(lucide_icon(icon_name, size=14, color_hex=MONOS_COLORS["text_meta"]).pixmap(14, 14))
        ic.setFixedSize(14, 14)
        lay.addWidget(ic, 0)
        lbl = QLabel(text, w)
        lbl.setObjectName("InboxFieldLabel")
        lay.addWidget(lbl, 0)
        return w

    def _make_item_button(self, label: str, data: str, icon_name: str,
                          obj_name: str, parent: QWidget) -> QPushButton:
        btn = QPushButton(label, parent)
        btn.setObjectName(obj_name)
        btn.setIcon(lucide_icon(icon_name, size=14))
        btn.setCheckable(True)
        btn.setProperty("item_data", data)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    # ── data ──

    def set_data(
        self,
        paths: list[Path],
        project_root: Path | None,
        project_index: ProjectIndex | None,
    ) -> None:
        self._paths = list(paths) if paths else []
        self._project_root = Path(project_root) if project_root else None
        self._project_index = project_index
        try:
            self._type_reg = TypeRegistry.for_project(self._project_root) if self._project_root else None
        except Exception:
            self._type_reg = None
        try:
            self._dept_reg = DepartmentRegistry.for_project(self._project_root) if self._project_root else None
        except Exception:
            self._dept_reg = None
        self._destinations = load_inbox_destinations()
        self._refill_dest_items()
        self._on_scope_changed()
        self._update_distribute_enabled()

    # ── scope ──

    def _current_scope(self) -> str:
        btn = self._scope_group.checkedButton()
        if btn:
            return (btn.property("item_data") or _INBOX_SCOPE_PROJECT).strip().lower()
        return _INBOX_SCOPE_PROJECT

    def _on_scope_selection_changed(self) -> None:
        self._refill_dest_items()
        self._on_scope_changed()

    def _on_scope_changed(self) -> None:
        scope = self._current_scope()
        if scope == _INBOX_SCOPE_PROJECT:
            self._type_section.setVisible(False)
            self._entity_combo.clear()
            self._entity_combo.addItem("Project (global)", None)
            self._update_distribute_enabled()
            return
        if scope == _INBOX_SCOPE_SHOT:
            self._type_section.setVisible(False)
            self._refill_entity_combo()
            self._update_distribute_enabled()
            return
        self._type_section.setVisible(True)
        self._refill_type_items()
        self._refill_entity_combo()
        self._update_distribute_enabled()

    # ── destination buttons ──

    def _refill_dest_items(self) -> None:
        for btn in list(self._dest_group.buttons()):
            self._dest_group.removeButton(btn)
            btn.deleteLater()
        scope = self._current_scope()
        first_btn: QPushButton | None = None
        for d in self._destinations:
            ctx = (d.get("context") or "both").strip().lower()
            match = (
                (scope == _INBOX_SCOPE_PROJECT and ctx == "project")
                or (scope == _INBOX_SCOPE_ASSET and ctx in ("asset", "both"))
                or (scope == _INBOX_SCOPE_SHOT and ctx in ("shot", "both"))
            )
            if not match:
                continue
            did = d.get("id", "")
            icon_name = self._DEST_ICON_MAP.get(did, "folder-open")
            btn = self._make_item_button(
                d.get("label", did), did, icon_name,
                "InboxDestItemButton", self._dest_container,
            )
            self._dest_group.addButton(btn)
            self._dest_layout.addWidget(btn)
            if first_btn is None:
                first_btn = btn
        if first_btn:
            first_btn.setChecked(True)
        self._update_distribute_enabled()

    # ── type buttons ──

    def _refill_type_items(self) -> None:
        for btn in list(self._type_group.buttons()):
            self._type_group.removeButton(btn)
            btn.deleteLater()
        first_btn: QPushButton | None = None
        if self._type_reg:
            for tid in self._type_reg.get_types():
                if (tid or "").lower() == "shot":
                    continue
                label = self._type_reg.get_type_label(tid) or tid
                icon_name = self._TYPE_ICON_MAP.get((tid or "").lower(), "box")
                btn = self._make_item_button(
                    label, tid, icon_name,
                    "InboxTypeItemButton", self._type_container,
                )
                self._type_group.addButton(btn)
                self._type_layout.addWidget(btn)
                if first_btn is None:
                    first_btn = btn
        if first_btn:
            first_btn.setChecked(True)
        self._refill_entity_combo()

    # ── entity combo ──

    def _refill_entity_combo(self) -> None:
        self._entity_combo.clear()
        scope = self._current_scope()
        if scope == _INBOX_SCOPE_PROJECT:
            self._entity_combo.addItem("Project (global)", None)
            self._update_distribute_enabled()
            return
        if not self._project_index:
            self._update_distribute_enabled()
            return
        if scope == _INBOX_SCOPE_SHOT:
            for s in self._project_index.shots:
                self._entity_combo.addItem(f"{s.name} (Shot)", s)
            self._update_distribute_enabled()
            return
        type_btn = self._type_group.checkedButton()
        type_id = type_btn.property("item_data") if type_btn else None
        if self._project_index and type_id:
            for a in self._project_index.assets:
                if (a.asset_type or "").strip().lower() != (type_id or "").strip().lower():
                    continue
                label = self._type_reg.get_type_label(type_id) if self._type_reg else type_id
                self._entity_combo.addItem(f"{a.name} ({label})", a)
        self._update_distribute_enabled()

    # ── distribute ──

    def _update_distribute_enabled(self) -> None:
        has_dest = self._dest_group.checkedButton() is not None
        self._distribute_btn.setEnabled(
            bool(
                self._paths
                and self._project_root
                and has_dest
                and self._entity_combo.count() > 0
            )
        )

    def _on_distribute(self) -> None:
        if not self._paths or not self._project_root:
            return
        dest_btn = self._dest_group.checkedButton()
        dest_id = dest_btn.property("item_data") if dest_btn else None
        entity = self._entity_combo.currentData()
        if not dest_id:
            return
        if entity is not None and not isinstance(entity, (Asset, Shot)):
            return
        move = self._move_radio.isChecked()
        dest_label = dest_btn.text().strip() if dest_btn else (dest_id or "")
        scope = self._current_scope()
        entity_name = ""
        if isinstance(entity, Asset):
            entity_name = getattr(entity, "name", "") or ""
        elif isinstance(entity, Shot):
            entity_name = getattr(entity, "name", "") or ""
        done: list[dict] = []
        for src in self._paths:
            dest_dir = resolve_destination_path(self._project_root, dest_id, entity, self._dept_reg)
            if not dest_dir:
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / src.name
            target_path_str = str(dest_path.resolve()) if dest_path else ""
            try:
                if src.is_dir():
                    if dest_path.exists():
                        shutil.rmtree(dest_path)
                    shutil.copytree(src, dest_path)
                else:
                    shutil.copy2(src, dest_path)
                if move:
                    if src.is_dir():
                        shutil.rmtree(src)
                    else:
                        src.unlink()
                done.append({
                    "path": src,
                    "destination_id": dest_id or "",
                    "destination_label": dest_label,
                    "scope": scope,
                    "entity_name": entity_name,
                    "target_path": target_path_str,
                })
            except OSError:
                pass
        if done:
            self.distribute_finished.emit(done)

