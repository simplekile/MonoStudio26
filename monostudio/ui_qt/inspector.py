from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal, QSize, QPoint, QTimer
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
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, Department, Shot, ProjectIndex
from monostudio.core.inbox_reader import load_inbox_destinations, resolve_destination_path
from monostudio.core.type_registry import TypeRegistry
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, file_icon_spec_for_path, monos_font
from monostudio.ui_qt.thumbnails import ThumbnailCache
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind, display_name_for_item


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
    prev_status = getattr(prev, "user_status", None) or ""
    cur_status = getattr(cur, "user_status", None) or ""
    out["status"] = (prev_status != cur_status)
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
    open_folder_requested = Signal(object)  # emits ViewItem — mở folder trong explorer
    status_change_requested = Signal(object, str)  # (ViewItem, status: ready|progress|waiting|blocked)
    inbox_distribute_finished = Signal(list)  # emits list of paths after distribute from Inbox

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
        self._preview.paste_requested.connect(self._on_paste_requested)
        self._asset_status.open_folder_clicked.connect(self._on_open_folder_requested)
        self._asset_status.status_change_requested.connect(self.status_change_requested.emit)

        self._inbox_destination = _InboxDestinationBlock()
        self._inbox_destination.distribute_finished.connect(self.inbox_distribute_finished.emit)

        for w in (
            self._empty,
            self._preview,
            self._asset_status,
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

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager
        self._preview.set_thumbnail_manager(manager)

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

    def set_item(self, item: ViewItem | None) -> None:
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

        scroll_bar = self._scroll.verticalScrollBar()
        scroll_pos = scroll_bar.value() if scroll_bar else 0

        diff = _inspector_diff(prev, item)
        full_update = diff.get("item", True) or not prev or str(prev.path) != str(item.path)

        if full_update:
            self._preview.set_item(item)
            self._asset_status.set_item(item)
            self._dept_pipeline.set_item(item)
            self._tech.set_item(item)
            self._stakeholders.set_item(item)
        else:
            if diff.get("name") or diff.get("type"):
                self._asset_status.update_identity(item)
            if diff.get("status"):
                self._asset_status.update_status(item)
            if diff.get("thumbnail"):
                self._preview.update_thumbnail_only()
            if diff.get("departments"):
                self._dept_pipeline.set_item(item)
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
        self.open_folder_requested.emit(item)

    def _on_paste_requested(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.paste_thumbnail_requested.emit(item)


    def _on_department_focused(self, department_name: str) -> None:
        """Update Tech row with the clicked department's work path (no focus state)."""
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        dep = (department_name or "").strip()
        if not dep:
            self._tech.set_resolved_path(None)
            return
        ref = getattr(item, "ref", None)
        if isinstance(ref, (Asset, Shot)) and ref.departments:
            for d in ref.departments:
                if (d.name or "").strip().casefold() == dep.casefold():
                    self._tech.set_resolved_path(d.work_path)
                    break
            else:
                self._tech.set_resolved_path(None)
        else:
            self._tech.set_resolved_path(None)

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


def _path_for_version(item: ViewItem) -> Path | None:
    """
    Path dùng để rút version: ưu tiên path FILE work (vd .blend) từ dcc_work_states.
    Nếu có nhiều work file thì lấy version cao nhất; không có thì dùng item.path.
    """
    ref = item.ref
    if not isinstance(ref, (Asset, Shot)):
        return item.path
    states = getattr(ref, "dcc_work_states", None) or ()
    paths_with_version: list[tuple[Path, int]] = []
    for key_st in states:
        if isinstance(key_st, (tuple, list)) and len(key_st) >= 2:
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
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._pix = pix
        self._has_image = bool(pix and not pix.isNull())
        if self._has_image:
            self._placeholder_file_icon = ()
        self.update()

    def set_placeholder_kind(self, kind: str, *, letter: str = "") -> None:
        self._placeholder_kind = (kind or "").strip().lower()
        self._placeholder_letter = (letter or "").strip()[:1].upper()
        self._placeholder_file_icon = ()
        self.update()

    def set_placeholder_file_icon(self, icon_name: str, color_hex: str) -> None:
        """Inbox: hiển thị icon theo loại file (folder, file-text, box/DCC, …) khi không có thumbnail."""
        self._placeholder_file_icon = ((icon_name or "file").strip(), (color_hex or "").strip())
        self.update()

    def set_placeholder_letter(self, letter: str) -> None:
        self._placeholder_letter = (letter or "").strip()[:1].upper()
        self.update()

    def heightForWidth(self, w: int) -> int:  # type: ignore[override]
        return max(1, int(w * 9 / 16))

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return True

    def sizeHint(self) -> QSize:  # type: ignore[override]
        # Stable default size for layouts (16:9).
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


class _PreviewContainer(QWidget):
    """Container for thumbnail with Paste button centered on top (icon only)."""
    paste_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        self._w = _PreviewWidget(self)
        l.addWidget(self._w, 0)
        self._btn_paste = QToolButton(self)
        self._btn_paste.setCursor(Qt.PointingHandCursor)
        self._btn_paste.setToolTip("Paste thumbnail from clipboard")
        self._btn_paste.setIcon(lucide_icon("upload", size=20, color_hex=MONOS_COLORS["text_label"]))
        self._btn_paste.setIconSize(QSize(24, 24))
        self._btn_paste.setFixedSize(44, 44)
        self._btn_paste.setStyleSheet(
            "QToolButton { border: none; border-radius: 22px; background: rgba(0,0,0,0.6); } "
            "QToolButton:hover { background: rgba(0,0,0,0.8); } "
            "QToolButton:disabled { background: rgba(0,0,0,0.3); }"
        )
        self._btn_paste.clicked.connect(self.paste_requested.emit)
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        r = self._w.geometry()
        x = r.x() + (r.width() - self._btn_paste.width()) // 2
        y = r.y() + (r.height() - self._btn_paste.height()) // 2
        self._btn_paste.move(x, y)
        self._btn_paste.raise_()

    def set_paste_enabled(self, enabled: bool) -> None:
        on = bool(enabled)
        self._btn_paste.setEnabled(on)
        self._btn_paste.setVisible(on)


class _InspectorPreview(QWidget):
    paste_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thumbs = ThumbnailCache(size_px=512)
        self._thumbnail_manager: object | None = None
        self._item: ViewItem | None = None
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        self._container = _PreviewContainer(self)
        l.addWidget(self._container, 0)
        self._container.paste_requested.connect(self.paste_requested.emit)
        self._container._w.context_menu_requested.connect(self._open_context_menu)
        self._set_paste_enabled(False)

    def _set_paste_enabled(self, enabled: bool) -> None:
        self._container.set_paste_enabled(enabled)

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager

    def set_item(self, item: ViewItem) -> None:
        self._item = item
        can_paste = item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT)
        self._set_paste_enabled(can_paste)
        w = self._container._w
        w.set_placeholder_kind(item.kind.value, letter=(display_name_for_item(item) or "").strip()[:1])
        w.set_pixmap(None)  # Show placeholder immediately; load thumbnail async.
        path = item.path
        asset_id = str(path)

        def load() -> None:
            # Inbox page: use ThumbnailCache (image/video file preview). Asset/shot: use manager.
            is_inbox = getattr(self, "_item", None) and getattr(self._item, "kind", None) == ViewItemKind.INBOX_ITEM
            if not is_inbox:
                mgr = getattr(self, "_thumbnail_manager", None)
                if mgr is not None and hasattr(mgr, "request_thumbnail"):
                    pix = mgr.request_thumbnail(asset_id)
                    if getattr(self, "_item", None) and self._item.path == path:
                        self._container._w.set_pixmap(pix)
                    return
            thumb = self._thumbs.resolve_thumbnail_file(path)
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
                self._container._w.set_pixmap(pix)

        QTimer.singleShot(0, load)

    def update_thumbnail_only(self) -> None:
        """Update thumbnail image only (e.g. after thumbnailsChanged). Does not rebuild; uses manager or cache."""
        item = self._item
        if item is None:
            return
        path = item.path
        asset_id = str(path)

        def load() -> None:
            is_inbox = getattr(self, "_item", None) and getattr(self._item, "kind", None) == ViewItemKind.INBOX_ITEM
            if not is_inbox:
                mgr = getattr(self, "_thumbnail_manager", None)
                if mgr is not None and hasattr(mgr, "request_thumbnail"):
                    pix = mgr.request_thumbnail(asset_id)
                    self._container._w.set_pixmap(pix)
                    return
            thumb = self._thumbs.resolve_thumbnail_file(path)
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
            self._container._w.set_pixmap(pix)

        QTimer.singleShot(0, load)

    def refresh_thumbnail(self) -> None:
        item = self._item
        if item is None:
            return
        mgr = getattr(self, "_thumbnail_manager", None)
        if mgr is not None and hasattr(mgr, "invalidate"):
            mgr.invalidate(str(item.path))
        # Force cache miss for legacy ThumbnailCache.
        for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
            self._thumbs.invalidate_file(item.path / name)
        self.set_item(item)

    def _open_context_menu(self, global_pos: object) -> None:
        gp = global_pos if isinstance(global_pos, QPoint) else QPoint(0, 0)
        item = self._item
        can_paste = bool(item and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))

        menu = QMenu(self)
        act = QAction(lucide_icon("upload", size=16, color_hex=MONOS_COLORS["text_label"]), "Paste thumbnail from Clipboard", menu)
        act.setEnabled(can_paste)
        act.triggered.connect(self.paste_requested.emit)
        menu.addAction(act)
        menu.exec(gp)


class _IdentityBlock(QWidget):
    open_clicked = Signal()
    open_with_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorIdentity")

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

        self._meta_type = QLabel("", self)
        self._meta_type.setStyleSheet(f"color: {MONOS_COLORS['text_label']};")

        dot1 = QLabel("·", self)
        dot1.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._meta_dept = QLabel("", self)
        self._meta_dept.setStyleSheet(f"color: {MONOS_COLORS['text_label']};")

        dot2 = QLabel("·", self)
        dot2.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._meta_version = QLabel("—", self)
        self._meta_version.setProperty("mono", True)
        self._meta_version.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        meta_l.addWidget(self._meta_type, 0)
        meta_l.addWidget(dot1, 0)
        meta_l.addWidget(self._meta_dept, 0)
        meta_l.addWidget(dot2, 0)
        meta_l.addWidget(self._meta_version, 0)
        meta_l.addStretch(1)

        l.addWidget(self._name, 0)
        l.addWidget(meta_row, 0)

    def set_item(self, item: ViewItem) -> None:
        self._name.setText(display_name_for_item(item))
        ref = item.ref
        # Type: asset_type (char/prop/env) for Asset, else kind (ASSET/SHOT/DEPARTMENT)
        if isinstance(ref, Asset) and (ref.asset_type or "").strip():
            type_str = (ref.asset_type or "").strip().upper()
        else:
            type_str = item.kind.value.upper()
        # Department: display name (label); có subdepartment thì ưu tiên subdepartment, không hiện parent
        dept_str = "—"
        registry = None
        label_resolver = None
        p = self.parent()
        while p:
            if getattr(p, "_department_registry", None) is not None:
                registry = getattr(p, "_department_registry", None)
            if getattr(p, "_department_label_resolver", None) is not None:
                label_resolver = getattr(p, "_department_label_resolver", None)
            if registry is not None and label_resolver is not None:
                break
            p = p.parent()
        if isinstance(ref, Department):
            raw = ref.name or "—"
            dept_str = (_department_display_name(raw, label_resolver) if raw != "—" else "—")
        elif isinstance(ref, (Asset, Shot)) and ref.departments:
            # Có subdepartment thì lấy subdepartment (leaf), không hiện department (parent)
            dept_to_show = None
            if registry is not None and hasattr(registry, "is_subdepartment"):
                subdepts = [d for d in ref.departments if registry.is_subdepartment(d.name or "")]
                dept_to_show = subdepts[0] if subdepts else ref.departments[0]
            else:
                dept_to_show = ref.departments[0]
            raw = dept_to_show.name or "—"
            dept_str = _department_display_name(raw, label_resolver) if raw else "—"
        # Version: Department = latest_publish_version; Asset/Shot = rút từ path FILE; hiển thị uppercase
        version = "—"
        if isinstance(ref, Department):
            if ref.latest_publish_version and _V_RE.match(ref.latest_publish_version):
                version = (ref.latest_publish_version or "").upper()
        elif isinstance(ref, (Asset, Shot)):
            path_for_version = _path_for_version(item)
            v = _version_from_path(path_for_version)
            version = v.upper() if v and v != "—" else (v or "—")

        self._meta_type.setText(type_str)
        self._meta_dept.setText(dept_str)
        self._meta_version.setText(version)


class _InspectorAssetStatusBlock(QWidget):
    """One container: row1 = Asset info (name+meta) | Status combo; row2 = Open folder."""
    open_folder_clicked = Signal()
    status_change_requested = Signal(object, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorAssetStatusBlock")
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
        self._btn_open_folder.setIcon(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_open_folder.clicked.connect(self._on_open_folder_clicked)
        row2_l.addWidget(self._btn_open_folder, 0)
        row2_l.addStretch(1)

        l.addWidget(row1, 0)
        l.addWidget(row2, 0)

        self._health.status_change_requested.connect(self.status_change_requested.emit)

    def _on_open_folder_clicked(self) -> None:
        self.open_folder_clicked.emit()

    def set_item(self, item: ViewItem) -> None:
        self._identity.set_item(item)
        self._health.set_item(item)
        is_asset_or_shot = bool(item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))
        self._btn_open_folder.setEnabled(is_asset_or_shot)

    def update_identity(self, item: ViewItem) -> None:
        self._identity.set_item(item)

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
    """Status combo only (no title, no assignee)."""
    status_change_requested = Signal(object, str)  # (ViewItem, status)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorProductionHealth")
        self._current_item: ViewItem | None = None

        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)

        self._status_combo = QComboBox(self)
        self._status_combo.setObjectName("InspectorStatusCombo")
        self._status_combo.setMinimumWidth(90)
        for label, key in (("Ready", "ready"), ("Progress", "progress"), ("Waiting", "waiting"), ("Blocked", "blocked")):
            self._status_combo.addItem(label, key)
        self._status_combo.currentIndexChanged.connect(self._on_status_combo_changed)
        l.addWidget(self._status_combo, 0, Qt.AlignVCenter)
        l.addStretch(1)

    def set_item(self, item: ViewItem) -> None:
        self._current_item = item
        status = "WAITING"
        ref = item.ref
        if isinstance(ref, Department):
            status = _status_from_department(ref)
            self._status_combo.setVisible(False)
        elif isinstance(ref, (Asset, Shot)):
            # User-set status overrides computed
            user = getattr(item, "user_status", None)
            if user:
                status = (user or "").strip().upper()
                if len(status) >= 2:
                    status = status[0] + status[1:].lower()  # Ready, Progress, Waiting, Blocked
                else:
                    status = status or "WAITING"
            else:
                if any(d.publish_version_count > 0 for d in ref.departments):
                    status = "READY"
                elif any(d.work_exists for d in ref.departments):
                    status = "PROGRESS"
                else:
                    status = "WAITING"
            self._status_combo.setVisible(True)
            key = status.lower()
            idx = self._status_combo.findData(key)
            if idx >= 0:
                self._status_combo.blockSignals(True)
                self._status_combo.setCurrentIndex(idx)
                self._status_combo.blockSignals(False)
        else:
            self._status_combo.setVisible(False)

        color = _status_color(status)
        self._status_combo.setStyleSheet(
            f"QComboBox#InspectorStatusCombo {{ "
            f"padding: 2px 8px; border-radius: 999px; border: none; "
            f"background: rgba(255,255,255,0.06); color: {color}; "
            f"font-size: 12px; min-width: 80px; }} "
            f"QComboBox#InspectorStatusCombo::drop-down {{ width: 0; border: none; }} "
            f"QComboBox#InspectorStatusCombo QAbstractItemView {{ "
            f"background: #18181b; color: #e2e2e2; selection-background-color: #2563eb; }}"
        )

    def _on_status_combo_changed(self) -> None:
        if self._current_item is None:
            return
        key = self._status_combo.currentData()
        if isinstance(key, str):
            self.status_change_requested.emit(self._current_item, key)


class _DeptCard(QFrame):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDeptCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)

        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(0)

        row = QWidget(self)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(8)

        self._name = QLabel("", self)
        self._name.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        f = monos_font("Inter", 12, QFont.Weight.Medium)
        self._name.setFont(f)

        self._pill = QLabel("", self)
        self._pill.setObjectName("InspectorStatusPill")
        self._pill.setStyleSheet(
            f"padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.06); color: {MONOS_COLORS['text_label']};"
        )

        self._btn_open = QToolButton(self)
        self._btn_open.setAutoRaise(True)
        self._btn_open.setIcon(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_open.setToolTip("Open folder")

        row_l.addWidget(self._name, 1)
        row_l.addWidget(self._pill, 0, Qt.AlignVCenter)
        row_l.addWidget(self._btn_open, 0, Qt.AlignVCenter)

        l.addWidget(row, 0)

        self._dept: Department | None = None
        self._btn_open.clicked.connect(self._open_folder)

    def set_department(self, dept: Department, display_name: str | None = None) -> None:
        self._dept = dept
        text = (display_name or dept.name or "").strip().upper()
        self._name.setText(text)
        status = _status_from_department(dept)
        self._pill.setText(status)
        self._pill.setStyleSheet(
            f"padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.06); color: {_status_color(status)};"
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
    """Tên hiển thị: label từ registry nếu có, else dept_id; luôn uppercase."""
    if callable(label_resolver):
        label = label_resolver(dept_id)
        return (label or dept_id or "").strip().upper()
    return (dept_id or "").strip().upper()


class _DepartmentPipeline(QWidget):
    manage_clicked = Signal()
    department_focused = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDepartmentPipeline")
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

        manage = QToolButton(self)
        manage.setObjectName("InspectorManageButton")
        manage.setText("MANAGE")
        manage.setAutoRaise(True)
        manage.setCursor(Qt.PointingHandCursor)
        manage.setEnabled(False)
        manage.setToolTip("Not available yet")
        manage.clicked.connect(self.manage_clicked.emit)

        hdr_l.addWidget(title, 1)
        hdr_l.addWidget(manage, 0, Qt.AlignRight)

        self._list = QWidget(self)
        self._list_l = QVBoxLayout(self._list)
        self._list_l.setContentsMargins(0, 0, 0, 0)
        self._list_l.setSpacing(8)

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
        self._dept_card_slots: list[object] = []  # slot per card for clean disconnect
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

    def set_item(self, item: ViewItem) -> None:
        ref = item.ref
        if isinstance(ref, Department):
            depts = (ref,)
        elif isinstance(ref, (Asset, Shot)):
            depts = ref.departments
        else:
            depts = ()

        registry = None
        label_resolver = None
        p = self.parent()
        while p:
            if getattr(p, "_department_registry", None) is not None:
                registry = getattr(p, "_department_registry", None)
            if getattr(p, "_department_label_resolver", None) is not None:
                label_resolver = getattr(p, "_department_label_resolver", None)
            if registry is not None and label_resolver is not None:
                break
            p = p.parent()

        if not depts:
            while self._list_l.count():
                self._list_l.takeAt(0)
            for c in self._dept_cards:
                c.setVisible(False)
            for s in self._section_titles:
                s.setVisible(False)
            self._empty.setVisible(True)
            return

        self._empty.setVisible(False)

        # Thứ tự theo registry (giống sidebar)
        if registry and hasattr(registry, "get_departments"):
            ordered_ids = list(registry.get_departments())
            dept_by_id = {d.name: d for d in depts}
            ordered_depts = [dept_by_id[dept_id] for dept_id in ordered_ids if dept_id in dept_by_id]
        else:
            ordered_depts = list(depts)

        # Build rows: ("section", parent_label) hoặc ("dept", Department) — subdepartment nằm trong container có title = department (parent)
        rows: list[tuple[str, object]] = []
        sections_emitted: set[str] = set()
        for d in ordered_depts:
            dept_id = d.name or ""
            parent_id = registry.get_parent(dept_id) if registry and hasattr(registry, "get_parent") else None
            if parent_id and parent_id not in sections_emitted:
                parent_label = (registry.get_department_label(parent_id) or parent_id).strip().upper()
                rows.append(("section", parent_label))
                sections_emitted.add(parent_id)
            rows.append(("dept", d))

        # Disconnect only cards that had a slot connected (tránh RuntimeWarning khi disconnect None)
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
                card.set_department(d, display_name)
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
        "vehicle": "box",
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
        done: list[Path] = []
        for src in self._paths:
            dest_dir = resolve_destination_path(self._project_root, dest_id, entity)
            if not dest_dir:
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / src.name
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
                done.append(src)
            except OSError:
                pass
        if done:
            self.distribute_finished.emit(done)

