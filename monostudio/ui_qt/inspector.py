from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal, QSize, QPoint, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.models import Asset, Department, Shot
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
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
    open_requested = Signal(object)  # emits ViewItem (asset/shot only)
    open_with_requested = Signal(object)  # emits ViewItem (asset/shot only)
    status_change_requested = Signal(object, str)  # (ViewItem, status: ready|progress|waiting|blocked)

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
        self._asset_status.open_clicked.connect(self._on_open_clicked)
        self._asset_status.open_with_clicked.connect(self._on_open_with_clicked)
        self._asset_status.status_change_requested.connect(self.status_change_requested.emit)

        for w in (
            self._empty,
            self._preview,
            self._asset_status,
            self._dept_pipeline,
            self._tech,
            self._stakeholders,
        ):
            self._content_layout.addWidget(w, 0)

        self._content_layout.addStretch(1)
        self._scroll.setWidget(content)

        self._current_item: ViewItem | None = None
        self._previous_item: ViewItem | None = None
        self._thumbnail_manager: object | None = None
        self.set_item(None)

    def set_thumbnail_manager(self, manager: object | None) -> None:
        """Use ThumbnailManager for async loading; None to use legacy ThumbnailCache only."""
        self._thumbnail_manager = manager
        self._preview.set_thumbnail_manager(manager)

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

    def _on_paste_requested(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.paste_thumbnail_requested.emit(item)

    def _on_open_clicked(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.open_requested.emit(item)

    def _on_open_with_clicked(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.open_with_requested.emit(item)

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
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._pix = pix
        self._has_image = bool(pix and not pix.isNull())
        self.update()

    def set_placeholder_kind(self, kind: str, *, letter: str = "") -> None:
        self._placeholder_kind = (kind or "").strip().lower()
        self._placeholder_letter = (letter or "").strip()[:1].upper()
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
        self._btn_paste.setEnabled(bool(enabled))


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
            mgr = getattr(self, "_thumbnail_manager", None)
            if mgr is not None and hasattr(mgr, "request_thumbnail"):
                pix = mgr.request_thumbnail(asset_id)
                if getattr(self, "_item", None) and self._item.path == path:
                    self._container._w.set_pixmap(pix)
                return
            thumb = self._thumbs.resolve_thumbnail_file(path)
            if thumb is None:
                if getattr(self, "_item", None) and self._item.path == path:
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
            mgr = getattr(self, "_thumbnail_manager", None)
            if mgr is not None and hasattr(mgr, "request_thumbnail"):
                pix = mgr.request_thumbnail(asset_id)
                self._container._w.set_pixmap(pix)
                return
            thumb = self._thumbs.resolve_thumbnail_file(path)
            if thumb is None:
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
        meta_l.setSpacing(8)

        self._meta_left = QLabel("", self)
        self._meta_left.setStyleSheet(f"color: {MONOS_COLORS['text_label']};")

        dot = QLabel("·", self)
        dot.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._meta_version = QLabel("", self)
        self._meta_version.setProperty("mono", True)
        self._meta_version.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        meta_l.addWidget(self._meta_left, 0)
        meta_l.addWidget(dot, 0)
        meta_l.addWidget(self._meta_version, 0)
        meta_l.addStretch(1)

        l.addWidget(self._name, 0)
        l.addWidget(meta_row, 0)

    def set_item(self, item: ViewItem) -> None:
        self._name.setText(display_name_for_item(item))
        kind = item.kind.value.upper()
        version = "—"
        ref = item.ref
        if isinstance(ref, Department):
            if ref.latest_publish_version and _V_RE.match(ref.latest_publish_version):
                version = ref.latest_publish_version
        elif isinstance(ref, (Asset, Shot)):
            version = _infer_latest_version_from_departments(ref.departments)

        self._meta_left.setText(kind)
        self._meta_version.setText(version)


class _InspectorAssetStatusBlock(QWidget):
    """One container: row1 = Asset info (name+meta) | Status combo; row2 = Open, Open With."""
    open_clicked = Signal()
    open_with_clicked = Signal()
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
        self._btn_open = QToolButton(row2)
        self._btn_open.setText("Open")
        self._btn_open.setCursor(Qt.PointingHandCursor)
        self._btn_open.setAutoRaise(True)
        self._btn_open.setIcon(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_open.clicked.connect(self.open_clicked.emit)
        self._btn_open_with = QToolButton(row2)
        self._btn_open_with.setText("Open With…")
        self._btn_open_with.setCursor(Qt.PointingHandCursor)
        self._btn_open_with.setAutoRaise(True)
        self._btn_open_with.setIcon(lucide_icon("layers", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_open_with.clicked.connect(self.open_with_clicked.emit)
        row2_l.addWidget(self._btn_open, 0)
        row2_l.addWidget(self._btn_open_with, 0)
        row2_l.addStretch(1)

        l.addWidget(row1, 0)
        l.addWidget(row2, 0)

        self._health.status_change_requested.connect(self.status_change_requested.emit)

    def set_item(self, item: ViewItem) -> None:
        self._identity.set_item(item)
        self._health.set_item(item)
        is_asset_or_shot = bool(item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))
        self._btn_open.setEnabled(is_asset_or_shot)
        self._btn_open_with.setEnabled(is_asset_or_shot)

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


class _ThinProgress(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(4)
        self._value = 0

    def set_value(self, v: int) -> None:
        self._value = max(0, min(100, int(v)))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            r = self.rect()
            bg = QColor(MONOS_COLORS["border"])
            fg = QColor(MONOS_COLORS["blue_600"])
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 2, 2)
            if self._value > 0:
                w = max(1, int(r.width() * (self._value / 100.0)))
                fr = r.adjusted(0, 0, -(r.width() - w), 0)
                p.setBrush(fg)
                p.drawRoundedRect(fr, 2, 2)
        finally:
            p.end()


class _DeptCard(QFrame):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDeptCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(76)
        self.setCursor(Qt.PointingHandCursor)

        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        top = QWidget(self)
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.setSpacing(8)

        self._name = QLabel("", self)
        self._name.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        f = monos_font("Inter", 12, QFont.Weight.Medium)
        self._name.setFont(f)

        self._pill = QLabel("", self)
        self._pill.setObjectName("InspectorStatusPill")
        self._pill.setStyleSheet(
            f"padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.06); color: {MONOS_COLORS['text_label']};"
        )

        top_l.addWidget(self._name, 1)
        top_l.addWidget(self._pill, 0, Qt.AlignRight)

        mid = QWidget(self)
        mid_l = QHBoxLayout(mid)
        mid_l.setContentsMargins(0, 0, 0, 0)
        mid_l.setSpacing(10)

        self._progress = _ThinProgress(self)
        self._progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._assignee_avatar = QLabel("—", self)
        self._assignee_avatar.setFixedSize(20, 20)
        self._assignee_avatar.setAlignment(Qt.AlignCenter)
        self._assignee_avatar.setStyleSheet(
            f"border-radius: 10px; background: {MONOS_COLORS['content_bg']}; color: {MONOS_COLORS['text_meta']};"
        )
        self._assignee = QLabel("—", self)
        self._assignee.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        actions = QWidget(self)
        act_l = QHBoxLayout(actions)
        act_l.setContentsMargins(0, 0, 0, 0)
        act_l.setSpacing(2)

        self._btn_open = QToolButton(self)
        self._btn_open.setAutoRaise(True)
        self._btn_open.setIcon(lucide_icon("folder-open", size=16, color_hex=MONOS_COLORS["text_label"]))
        self._btn_open.setToolTip("Open folder")

        self._btn_publish = QToolButton(self)
        self._btn_publish.setAutoRaise(True)
        self._btn_publish.setEnabled(False)
        self._btn_publish.setIcon(lucide_icon("upload", size=16, color_hex=MONOS_COLORS["text_meta"]))
        self._btn_publish.setToolTip("Publish (not available yet)")

        self._btn_comment = QToolButton(self)
        self._btn_comment.setAutoRaise(True)
        self._btn_comment.setEnabled(False)
        self._btn_comment.setIcon(lucide_icon("message-circle", size=16, color_hex=MONOS_COLORS["text_meta"]))
        self._btn_comment.setToolTip("Comment (not available yet)")

        self._btn_more = QToolButton(self)
        self._btn_more.setAutoRaise(True)
        self._btn_more.setEnabled(False)
        self._btn_more.setIcon(lucide_icon("ellipsis", size=16, color_hex=MONOS_COLORS["text_meta"]))
        self._btn_more.setToolTip("More (not available yet)")

        for b in (self._btn_open, self._btn_publish, self._btn_comment, self._btn_more):
            act_l.addWidget(b, 0)

        mid_l.addWidget(self._progress, 1)
        mid_l.addWidget(self._assignee_avatar, 0)
        mid_l.addWidget(self._assignee, 0)
        mid_l.addStretch(1)
        mid_l.addWidget(actions, 0, Qt.AlignRight)

        l.addWidget(top, 0)
        l.addWidget(mid, 0)

        self._dept: Department | None = None
        self._btn_open.clicked.connect(self._open_folder)

    def set_department(self, dept: Department) -> None:
        self._dept = dept
        self._name.setText(dept.name)
        status = _status_from_department(dept)
        self._pill.setText(status)
        self._pill.setStyleSheet(
            f"padding: 2px 8px; border-radius: 999px; background: rgba(255,255,255,0.06); color: {_status_color(status)};"
        )
        self._progress.set_value(100 if status == "READY" else (50 if status == "PROGRESS" else 0))
        self._assignee_avatar.setText("—")
        self._assignee.setText("—")

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
        self._list_l.setSpacing(12)

        self._dept_cards: list[_DeptCard] = []
        self._dept_click_handlers: list[object] = []  # stored slots for disconnect
        for _ in range(_MAX_DEPT_CARDS):
            card = _DeptCard(self)
            card.setVisible(False)
            self._dept_cards.append(card)
            self._dept_click_handlers.append(None)
            self._list_l.addWidget(card, 0)

        self._empty = QLabel("—", self)
        self._empty.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        l.addWidget(hdr, 0)
        l.addWidget(self._list, 0)
        l.addWidget(self._empty, 0)

    def set_item(self, item: ViewItem) -> None:
        # Reuse card pool: never delete or create widgets; update and show/hide only.
        depts: tuple[Department, ...] = ()
        ref = item.ref
        if isinstance(ref, Department):
            depts = (ref,)
        elif isinstance(ref, (Asset, Shot)):
            depts = ref.departments

        if not depts:
            for c in self._dept_cards:
                c.setVisible(False)
            self._empty.setVisible(True)
            return

        self._empty.setVisible(False)
        for i, d in enumerate(depts):
            card = self._dept_cards[i]
            card.set_department(d)
            card.setVisible(True)
            # Disconnect previous slot if any (avoids RuntimeWarning when none connected).
            old_handler = self._dept_click_handlers[i]
            if old_handler is not None:
                try:
                    card.clicked.disconnect(old_handler)
                except (TypeError, RuntimeError):
                    pass
                self._dept_click_handlers[i] = None

            def on_clicked(idx: int = i) -> None:
                dept_name = depts[idx].name if idx < len(depts) else None
                if dept_name:
                    self.department_focused.emit(dept_name)

            card.clicked.connect(on_clicked)
            self._dept_click_handlers[i] = on_clicked
        for j in range(len(depts), len(self._dept_cards)):
            self._dept_cards[j].setVisible(False)


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

