from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal, QSize, QPoint
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.thumbnails import ThumbnailCache
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind


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
        self._identity = _IdentityBlock()
        self._health = _ProductionHealth()
        self._dept_pipeline = _DepartmentPipeline()
        self._tech = _TechnicalSpecs()
        self._stakeholders = _Stakeholders()

        self._dept_pipeline.manage_clicked.connect(self.manage_departments_requested.emit)
        self._preview.paste_requested.connect(self._on_paste_requested)

        for w in (
            self._empty,
            self._preview,
            self._identity,
            self._health,
            self._dept_pipeline,
            self._tech,
            self._stakeholders,
        ):
            self._content_layout.addWidget(w, 0)

        self._content_layout.addStretch(1)
        self._scroll.setWidget(content)

        self._current_item: ViewItem | None = None
        self.set_item(None)

    def set_item(self, item: ViewItem | None) -> None:
        # Show/hide sections per selection. Inspector header remains stable.
        self._current_item = item
        has_item = item is not None
        self._empty.setVisible(not has_item)
        for w in (self._preview, self._identity, self._health, self._dept_pipeline, self._tech, self._stakeholders):
            w.setVisible(has_item)

        if item is None:
            self._empty.set_message("Select an item to view details")
            return

        self._preview.set_item(item)
        self._identity.set_item(item)
        self._health.set_item(item)
        self._dept_pipeline.set_item(item)
        self._tech.set_item(item)
        self._stakeholders.set_item(item)

    def refresh_thumbnail(self) -> None:
        # Best-effort; safe no-op if nothing selected.
        try:
            self._preview.refresh_thumbnail()
        except Exception:
            pass

    def _on_paste_requested(self) -> None:
        item = self._current_item
        if item is None:
            return
        if item.kind not in (ViewItemKind.ASSET, ViewItemKind.SHOT):
            return
        self.paste_thumbnail_requested.emit(item)

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
        f = QFont("Inter", 10)
        f.setWeight(QFont.Weight.ExtraBold)
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
        self._placeholder_letter: str = ""
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._pix = pix
        self._has_image = bool(pix and not pix.isNull())
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

            # Neutral placeholder (no illustration/mascot)
            p.setClipping(False)
            p.setPen(QPen(QColor(MONOS_COLORS["border"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0, 0, -1, -1), radius, radius)

            if self._placeholder_letter:
                p.setPen(QColor(MONOS_COLORS["text_meta"]))
                f = QFont("Inter", 28)
                f.setWeight(QFont.Weight.DemiBold)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, self._placeholder_letter)
                return

            p.setClipping(False)
            p.setPen(QColor(MONOS_COLORS["text_meta"]))
            f = QFont("Inter", 11)
            f.setWeight(QFont.Weight.DemiBold)
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


class _InspectorPreview(QWidget):
    paste_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Same thumbnail discovery rules as cards: prefer thumbnail.user.(png|jpg), then thumbnail.(png|jpg).
        self._thumbs = ThumbnailCache(size_px=512)
        self._item: ViewItem | None = None
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(8)
        self._w = _PreviewWidget(self)
        l.addWidget(self._w, 0)

        actions = QWidget(self)
        actions_l = QHBoxLayout(actions)
        actions_l.setContentsMargins(0, 0, 0, 0)
        actions_l.setSpacing(8)

        title = QLabel("THUMBNAIL", actions)
        title.setObjectName("InspectorSectionTitle")
        f = QFont("Inter", 10)
        f.setWeight(QFont.Weight.ExtraBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._btn_paste = QToolButton(actions)
        self._btn_paste.setText("Paste")
        self._btn_paste.setCursor(Qt.PointingHandCursor)
        self._btn_paste.setAutoRaise(True)
        self._btn_paste.setToolTip("Paste thumbnail from clipboard")
        self._btn_paste.clicked.connect(self.paste_requested.emit)

        actions_l.addWidget(title, 0, Qt.AlignVCenter)
        actions_l.addStretch(1)
        actions_l.addWidget(self._btn_paste, 0, Qt.AlignVCenter)
        l.addWidget(actions, 0)

        self._w.context_menu_requested.connect(self._open_context_menu)
        self._set_paste_enabled(False)

    def _set_paste_enabled(self, enabled: bool) -> None:
        self._btn_paste.setEnabled(bool(enabled))

    def set_item(self, item: ViewItem) -> None:
        self._item = item
        can_paste = item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT)
        self._set_paste_enabled(can_paste)
        # Match card behavior: use thumbnail.png/jpg if present; else neutral placeholder.
        self._w.set_placeholder_letter((item.name or "").strip()[:1])
        thumb = self._thumbs.resolve_thumbnail_file(item.path)
        if thumb is None:
            self._w.set_pixmap(None)
            return
        pix = self._thumbs.load_thumbnail_pixmap(thumb)
        self._w.set_pixmap(pix)

    def refresh_thumbnail(self) -> None:
        item = self._item
        if item is None:
            return
        # Force cache miss for both user + auto candidates to ensure immediate refresh.
        for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
            self._thumbs.invalidate_file(item.path / name)
        self.set_item(item)

    def _open_context_menu(self, global_pos: object) -> None:
        gp = global_pos if isinstance(global_pos, QPoint) else QPoint(0, 0)
        item = self._item
        can_paste = bool(item and item.kind in (ViewItemKind.ASSET, ViewItemKind.SHOT))

        menu = QMenu(self)
        act = QAction("Paste thumbnail from Clipboard", menu)
        act.setEnabled(can_paste)
        act.triggered.connect(self.paste_requested.emit)
        menu.addAction(act)
        menu.exec(gp)


class _IdentityBlock(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorIdentity")

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        self._name = QLabel("", self)
        self._name.setObjectName("InspectorPrimaryName")
        f = QFont("Inter", 15)
        f.setWeight(QFont.Weight.DemiBold)
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
        self._name.setText(item.name)

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
        f = QFont("Inter", 10)
        f.setWeight(QFont.Weight.ExtraBold)
        hdr.setFont(f)
        hdr.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._body = QWidget(self)
        self._body_l = QHBoxLayout(self._body)
        self._body_l.setContentsMargins(0, 0, 0, 0)
        self._body_l.setSpacing(8)

        l.addWidget(hdr, 0)
        l.addWidget(self._body, 0)


class _ProductionHealth(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorProductionHealth")

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        title = QLabel("PRODUCTION HEALTH", self)
        title.setObjectName("InspectorSectionTitle")
        f = QFont("Inter", 10)
        f.setWeight(QFont.Weight.ExtraBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        row = QWidget(self)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(12)

        self._card_status = _MiniInfoCard("STATUS", row)
        self._status_dot = QLabel("", row)
        self._status_dot.setFixedSize(8, 8)
        self._status_label = QLabel("—", row)
        self._status_label.setStyleSheet(f"color: {MONOS_COLORS['text_label']};")
        self._card_status._body_l.addWidget(self._status_dot, 0, Qt.AlignVCenter)
        self._card_status._body_l.addWidget(self._status_label, 0, Qt.AlignVCenter)
        self._card_status._body_l.addStretch(1)

        self._card_assignee = _MiniInfoCard("ASSIGNEE", row)
        self._assignee_avatar = QLabel("", row)
        self._assignee_avatar.setFixedSize(24, 24)
        self._assignee_avatar.setAlignment(Qt.AlignCenter)
        self._assignee_avatar.setStyleSheet(
            f"border-radius: 12px; background: {MONOS_COLORS['content_bg']}; color: {MONOS_COLORS['text_meta']};"
        )
        self._assignee_name = QLabel("—", row)
        self._assignee_name.setStyleSheet(f"color: {MONOS_COLORS['text_label']};")
        self._card_assignee._body_l.addWidget(self._assignee_avatar, 0)
        self._card_assignee._body_l.addWidget(self._assignee_name, 0)
        self._card_assignee._body_l.addStretch(1)

        row_l.addWidget(self._card_status, 1)
        row_l.addWidget(self._card_assignee, 1)

        l.addWidget(title, 0)
        l.addWidget(row, 0)

    def set_item(self, item: ViewItem) -> None:
        status = "WAITING"
        ref = item.ref
        if isinstance(ref, Department):
            status = _status_from_department(ref)
        elif isinstance(ref, (Asset, Shot)):
            # Best-effort: READY if any dept has publish versions; else PROGRESS if any work exists.
            if any(d.publish_version_count > 0 for d in ref.departments):
                status = "READY"
            elif any(d.work_exists for d in ref.departments):
                status = "PROGRESS"
            else:
                status = "WAITING"

        color = _status_color(status)
        self._status_dot.setStyleSheet(f"border-radius: 4px; background: {color};")
        self._status_label.setText(status)

        # Assignee unknown in current data model: show placeholders.
        self._assignee_avatar.setText("—")
        self._assignee_name.setText("—")


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
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorDeptCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(76)

        l = QVBoxLayout(self)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(8)

        top = QWidget(self)
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.setSpacing(8)

        self._name = QLabel("", self)
        self._name.setStyleSheet(f"color: {MONOS_COLORS['text_primary']};")
        f = QFont("Inter", 12)
        f.setWeight(QFont.Weight.Medium)
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


class _DepartmentPipeline(QWidget):
    manage_clicked = Signal()

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
        f = QFont("Inter", 10)
        f.setWeight(QFont.Weight.ExtraBold)
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

        self._empty = QLabel("—", self)
        self._empty.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        l.addWidget(hdr, 0)
        l.addWidget(self._list, 0)
        l.addWidget(self._empty, 0)

    def set_item(self, item: ViewItem) -> None:
        # Clear
        while self._list_l.count():
            w = self._list_l.takeAt(0).widget()
            if w is not None:
                w.setParent(None)

        depts: tuple[Department, ...] = ()
        ref = item.ref
        if isinstance(ref, Department):
            depts = (ref,)
        elif isinstance(ref, (Asset, Shot)):
            depts = ref.departments

        if not depts:
            self._empty.setVisible(True)
            return

        self._empty.setVisible(False)
        for d in depts:
            card = _DeptCard(self)
            card.set_department(d)
            self._list_l.addWidget(card, 0)


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

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)

        title = QLabel("TECHNICAL SPECS", self)
        title.setObjectName("InspectorSectionTitle")
        f = QFont("Inter", 10)
        f.setWeight(QFont.Weight.ExtraBold)
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
        self._frame.set_value("—")
        self._fps.set_value("—")
        self._res.set_value("—")
        self._src.setText(str(item.path))
        self._modified.set_value(_format_mtime(item.path))

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

