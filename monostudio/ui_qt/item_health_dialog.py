from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.fs_reader import _parse_workfile_version, work_file_prefix
from monostudio.core.models import Asset, Shot
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.main_view import (
    HealthIssue,
    ItemHealth,
    _ITEM_HEALTH_COLORS,
    _department_for_item,
    _workfile_extensions_set,
    assess_view_item_health,
)
from monostudio.ui_qt.notification import notify
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font


def _resolved_path(p: Path) -> Path:
    try:
        return p.resolve()
    except OSError:
        return p


def _path_under_any_watched_root(file_path: Path, watched_roots: Sequence[Path]) -> bool:
    fr = _resolved_path(file_path)
    for wp in watched_roots:
        wr = _resolved_path(wp)
        try:
            if fr == wr or fr.is_relative_to(wr):
                return True
        except (ValueError, OSError):
            continue
    return False


def _paths_overlap_active_qfilesystem_watcher(win: QWidget, paths: Sequence[Path]) -> bool:
    """True if the main window's watcher is active and covers at least one of ``paths``."""
    if getattr(win, "_watcher_manually_disabled", False):
        return False
    watcher = getattr(win, "_fs_watcher", None)
    if watcher is None:
        return False
    raw = list(watcher.directories()) + list(watcher.files())
    if not raw:
        return False
    watched_roots = [Path(s) for s in raw if s]
    for p in paths:
        if _path_under_any_watched_root(p, watched_roots):
            return True
    return False


def _watcher_allows_mutation_or_notify(parent: QWidget, paths: Sequence[Path]) -> bool:
    """Return True if rename/delete may proceed; otherwise toast and return False."""
    win = parent.window()
    if getattr(win, "_watcher_manually_disabled", False):
        return True
    if not paths:
        return True
    if not _paths_overlap_active_qfilesystem_watcher(win, paths):
        return True
    notify.warning(
        "Pause the file watcher (eye icon in the top bar) before renaming or deleting files "
        "inside watched project folders."
    )
    return False


def _is_valid_pipeline_work_filename(name: str, prefix: str, work_exts: frozenset[str]) -> bool:
    ext = (Path(name).suffix or "").lower()
    if ext not in work_exts:
        return False
    if _parse_workfile_version(name, prefix, ext) is not None:
        return True
    return name == prefix + ext


class WorkNamingFixDialog(MonosDialog):
    """Rename files that use a work extension but do not match prefix / version pattern."""

    def __init__(
        self,
        *,
        parent: QWidget | None,
        paths: tuple[str, ...],
        prefix: str,
        on_applied: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fix work file names")
        self.setModal(True)
        self.setMinimumSize(520, 360)
        self.resize(640, 480)
        self._on_applied = on_applied
        self._prefix = (prefix or "").strip()
        self._work_exts = _workfile_extensions_set()
        self._rows: list[tuple[Path, QLabel, QLineEdit]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hint = QLabel(
            "Each row: current file and the new filename (same folder). "
            "Use Auto to suggest the next free version per extension, then edit or Apply.",
            self,
        )
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(4, 4, 4, 4)
        body_l.setSpacing(10)

        mono = monos_font("JetBrains Mono", 11)
        for raw in paths:
            p = Path(raw)
            row = QWidget(body)
            row_l = QVBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(4)
            pl = QLabel(str(p), row)
            pl.setFont(mono)
            pl.setObjectName("DialogHint")
            pl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            pl.setWordWrap(False)
            ed = QLineEdit(p.name, row)
            ed.setFont(mono)
            ed.setObjectName("DialogBodyInput")
            row_l.addWidget(pl)
            row_l.addWidget(ed)
            body_l.addWidget(row)
            self._rows.append((p, pl, ed))
        body_l.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        auto_btn = QPushButton("Auto", self)
        auto_btn.setObjectName("DialogSecondaryButton")
        auto_btn.clicked.connect(self._on_auto)
        actions.addWidget(auto_btn)
        actions.addStretch(1)
        apply_btn = QPushButton("Apply renames", self)
        apply_btn.setObjectName("DialogPrimaryButton")
        apply_btn.clicked.connect(self._on_apply)
        actions.addWidget(apply_btn)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)
        root.addLayout(actions)

    def _on_auto(self) -> None:
        from collections import defaultdict

        groups: dict[Path, list[tuple[Path, QLineEdit]]] = defaultdict(list)
        for p, _pl, ed in self._rows:
            groups[p.parent].append((p, ed))
        for parent, items in groups.items():
            try:
                kids = list(parent.iterdir())
            except OSError:
                kids = []
            reserved = {child.name.casefold() for child in kids if child.is_file()}
            max_by_ext: dict[str, int] = {}
            for child in kids:
                if not child.is_file():
                    continue
                ext = (child.suffix or "").lower()
                if ext not in self._work_exts:
                    continue
                v = _parse_workfile_version(child.name, self._prefix, ext)
                if v is not None:
                    max_by_ext[ext] = max(max_by_ext.get(ext, 0), v)
            for p, ed in sorted(items, key=lambda t: t[0].name.casefold()):
                ext = (p.suffix or "").lower()
                if ext not in self._work_exts:
                    continue
                n = max_by_ext.get(ext, 0) + 1
                cand = f"{self._prefix}_v{n:03d}{ext}"
                while cand.casefold() in reserved:
                    n += 1
                    cand = f"{self._prefix}_v{n:03d}{ext}"
                max_by_ext[ext] = n
                reserved.add(cand.casefold())
                reserved.discard(p.name.casefold())
                ed.setText(cand)

    def _on_apply(self) -> None:
        if not self._prefix:
            QMessageBox.warning(self, "Fix names", "Missing work file prefix.")
            return
        renames: list[tuple[Path, Path]] = []
        for p, _pl, ed in self._rows:
            new_name = ed.text().strip()
            if not new_name or new_name == p.name:
                continue
            if not _is_valid_pipeline_work_filename(new_name, self._prefix, self._work_exts):
                QMessageBox.warning(
                    self,
                    "Invalid name",
                    f"“{new_name}” does not match {self._prefix}_v### with a registered work extension.",
                )
                return
            dst = p.parent / new_name
            if dst.exists():
                QMessageBox.warning(self, "Already exists", f"A file already exists:\n{dst}")
                return
            renames.append((p, dst))
        if not renames:
            self.accept()
            return
        touch_paths: list[Path] = []
        for src, dst in renames:
            touch_paths.append(src)
            touch_paths.append(dst)
        if not _watcher_allows_mutation_or_notify(self, touch_paths):
            return
        for src, dst in renames:
            try:
                src.rename(dst)
            except OSError as e:
                QMessageBox.critical(self, "Rename failed", f"{src}\n{e!s}")
                return
        if self._on_applied is not None:
            self._on_applied()
        self.accept()


class WorkInvalidExtCleanDialog(MonosDialog):
    """Confirm permanent delete of work-folder files that use a non-work extension."""

    def __init__(
        self,
        *,
        parent: QWidget | None,
        paths: tuple[str, ...],
        on_deleted: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clean non-work files")
        self.setModal(True)
        self.setMinimumSize(480, 320)
        self.resize(560, 420)
        self._on_deleted = on_deleted
        self._paths = paths

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        warn = QLabel(
            "These files start with the work prefix but use an extension that is not a "
            "registered DCC work type. They will be permanently deleted (not moved to Trash).",
            self,
        )
        warn.setObjectName("DialogHint")
        warn.setWordWrap(True)
        root.addWidget(warn)

        list_box = QTextEdit(self)
        list_box.setReadOnly(True)
        list_box.setPlainText("\n".join(paths))
        mono = monos_font("JetBrains Mono", 11)
        list_box.setFont(mono)
        list_box.setMinimumHeight(180)
        root.addWidget(list_box, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        del_btn = QPushButton("Delete listed files", self)
        del_btn.setObjectName("DialogDestructiveButton")
        del_btn.clicked.connect(self._on_delete)
        actions.addWidget(del_btn)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)
        root.addLayout(actions)

    def _on_delete(self) -> None:
        targets = [Path(raw) for raw in self._paths]
        if not _watcher_allows_mutation_or_notify(self, targets):
            return
        for raw in self._paths:
            p = Path(raw)
            try:
                if p.is_file():
                    p.unlink()
            except OSError as e:
                QMessageBox.critical(self, "Delete failed", f"{p}\n{e!s}")
                return
        if self._on_deleted is not None:
            self._on_deleted()
        self.accept()


class HoudiniBackupCleanDialog(MonosDialog):
    """Confirm permanent delete of Houdini automatic backup folders in work."""

    def __init__(
        self,
        *,
        parent: QWidget | None,
        paths: tuple[str, ...],
        on_deleted: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clean Houdini backup folders")
        self.setModal(True)
        self.setMinimumSize(480, 320)
        self.resize(560, 420)
        self._on_deleted = on_deleted
        self._paths = paths

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        warn = QLabel(
            "Houdini stores automatic scene copies in a backup/ subfolder next to the work file. "
            "The folders below will be permanently deleted (not moved to Trash).",
            self,
        )
        warn.setObjectName("DialogHint")
        warn.setWordWrap(True)
        root.addWidget(warn)

        list_box = QTextEdit(self)
        list_box.setReadOnly(True)
        list_box.setPlainText("\n".join(paths))
        mono = monos_font("JetBrains Mono", 11)
        list_box.setFont(mono)
        list_box.setMinimumHeight(180)
        root.addWidget(list_box, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        del_btn = QPushButton("Delete listed folders", self)
        del_btn.setObjectName("DialogDestructiveButton")
        del_btn.clicked.connect(self._on_delete)
        actions.addWidget(del_btn)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)
        root.addLayout(actions)

    def _on_delete(self) -> None:
        for raw in self._paths:
            p = Path(raw)
            try:
                if p.is_dir():
                    shutil.rmtree(p)
            except OSError as e:
                QMessageBox.critical(self, "Delete failed", f"{p}\n{e!s}")
                return
        if self._on_deleted is not None:
            self._on_deleted()
        self.accept()


def _dialog_start_system_move(widget: QWidget, event: QMouseEvent) -> bool:
    """Begin native window drag for frameless MonosDialog (Windows-friendly)."""
    if event.button() != Qt.MouseButton.LeftButton:
        return False
    win = widget.window()
    if win is None:
        return False
    wh = win.windowHandle()
    if wh is None:
        return False
    try:
        wh.startSystemMove()
        return True
    except (AttributeError, RuntimeError):
        return False


class _DialogSizeGrip(QToolButton):
    """Resize handle — resizes dialog even when embedded in title bar layout."""

    def __init__(self, resize_target: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ItemHealthDialogSizeGrip")
        self._target = resize_target
        self.setToolTip("Drag to resize")
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAutoRaise(True)
        self.setFixedSize(28, 28)
        self.setIcon(lucide_icon("maximize-2", size=16, color_hex=MONOS_COLORS["text_meta"]))
        self._origin: QPoint | None = None
        self._start_size: QPoint | None = None  # width, height as QPoint.x/y

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._start_size = QPoint(self._target.width(), self._target.height())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._origin is not None
            and self._start_size is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = event.globalPosition().toPoint() - self._origin
            min_sz = self._target.minimumSize()
            w = max(min_sz.width(), self._start_size.x() + delta.x())
            h = max(min_sz.height(), self._start_size.y() + delta.y())
            self._target.resize(w, h)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = None
            self._start_size = None
        super().mouseReleaseEvent(event)


class _DialogMoveGrip(QToolButton):
    """Six-dot grip — drag to reposition the parent dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ItemHealthDialogMoveGrip")
        self.setToolTip("Drag to move")
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAutoRaise(True)
        self.setFixedSize(28, 28)
        self.setIcon(lucide_icon("grip-vertical", size=16, color_hex=MONOS_COLORS["text_meta"]))
        self._manual_origin: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not _dialog_start_system_move(self, event):
                self._manual_origin = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._manual_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win is not None:
                delta = event.globalPosition().toPoint() - self._manual_origin
                win.move(win.x() + delta.x(), win.y() + delta.y())
                self._manual_origin = event.globalPosition().toPoint()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._manual_origin = None
        super().mouseReleaseEvent(event)


class _ItemHealthTitleBar(QWidget):
    """Top bar: health icon, title, move grip."""

    def __init__(
        self,
        *,
        item_name: str,
        health: ItemHealth,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manual_origin: QPoint | None = None
        self.setObjectName("ItemHealthDialogTitleBar")
        self.setFixedHeight(44)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(10)

        self._health_icon_label = QLabel(self)
        self._health_icon_label.setPixmap(
            lucide_icon(health.icon_name, size=20, color_hex=health.color_hex).pixmap(20, 20)
        )
        self._health_icon_label.setFixedSize(22, 22)
        lay.addWidget(self._health_icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        meta = QLabel("ITEM HEALTH", self)
        meta_font = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        meta.setFont(meta_font)
        meta.setObjectName("DialogHint")
        text_col.addWidget(meta)
        title = QLabel((item_name or "—").strip(), self)
        title.setObjectName("DialogSectionTitle")
        text_col.addWidget(title)
        lay.addLayout(text_col, 1)

        lay.addWidget(_DialogMoveGrip(self), 0, Qt.AlignmentFlag.AlignVCenter)

        win = self.window()
        if win is not None:
            lay.addWidget(_DialogSizeGrip(win, parent=self), 0, Qt.AlignmentFlag.AlignVCenter)

    def apply_health(self, health: ItemHealth) -> None:
        self._health_icon_label.setPixmap(
            lucide_icon(health.icon_name, size=20, color_hex=health.color_hex).pixmap(20, 20)
        )

    def _is_on_chrome(self, pos: QPoint) -> bool:
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, (QToolButton, _DialogSizeGrip)):
                return True
            child = child.parentWidget()
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._is_on_chrome(event.position().toPoint())
        ):
            if not _dialog_start_system_move(self, event):
                self._manual_origin = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._manual_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win is not None:
                delta = event.globalPosition().toPoint() - self._manual_origin
                win.move(win.x() + delta.x(), win.y() + delta.y())
                self._manual_origin = event.globalPosition().toPoint()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._manual_origin = None
        super().mouseReleaseEvent(event)


class _HealthBadFilesSection(QWidget):
    """Collapsible list of files that failed a health check."""

    def __init__(
        self,
        files: tuple[str, ...],
        parent: QWidget | None = None,
        *,
        expanded: bool = True,
        section_title: str | None = None,
    ) -> None:
        super().__init__(parent)
        n = len(files)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header_btn = QToolButton(self)
        title = section_title if section_title is not None else f"Problem files ({n})"
        self._header_btn.setText(title)
        self._header_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header_btn.setAutoRaise(True)
        self._header_btn.setCheckable(True)
        self._header_btn.setChecked(expanded)
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.setObjectName("ViewOptionsSubmenuHeader")
        self._header_btn.clicked.connect(self._on_toggle)
        outer.addWidget(self._header_btn)

        self._body = QWidget(self)
        body_l = QVBoxLayout(self._body)
        body_l.setContentsMargins(20, 4, 0, 0)
        body_l.setSpacing(4)
        mono = monos_font("JetBrains Mono", 11)
        for path in files:
            row = QLabel(path, self._body)
            row.setFont(mono)
            row.setObjectName("DialogHint")
            row.setWordWrap(False)
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            body_l.addWidget(row)
        self._body.setVisible(expanded)
        outer.addWidget(self._body)
        self._sync_chevron()

    def _sync_chevron(self) -> None:
        icon = "chevron-down" if self._header_btn.isChecked() else "chevron-right"
        self._header_btn.setIcon(
            lucide_icon(icon, size=14, color_hex=MONOS_COLORS["text_label"])
        )

    def _on_toggle(self) -> None:
        self._body.setVisible(self._header_btn.isChecked())
        self._sync_chevron()


class ItemHealthDialog(MonosDialog):
    """Detailed item health report for the focused department."""

    def __init__(
        self,
        *,
        parent=None,
        item_name: str,
        department: str,
        health: ItemHealth,
        naming_prefix: str | None = None,
        on_repaired: Callable[[], None] | None = None,
        health_refresh: tuple[Asset | Shot, str, str | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._naming_prefix = (naming_prefix or "").strip()
        self._on_repaired = on_repaired
        self._health_refresh_ctx = health_refresh
        self.setWindowTitle("Item health")
        self.setModal(True)
        self.setObjectName("ItemHealthDialog")
        self.setMinimumSize(480, 320)
        self.resize(560, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._title_bar = _ItemHealthTitleBar(item_name=item_name, health=health, parent=self)
        root.addWidget(self._title_bar)

        content = QWidget(self)
        content_l = QVBoxLayout(content)
        content_l.setContentsMargins(12, 12, 12, 12)
        content_l.setSpacing(12)

        dept_label = QLabel(f"Department · {(department or '—').replace('_', ' ').title()}")
        dept_label.setObjectName("DialogHint")
        content_l.addWidget(dept_label)

        summary_row = QWidget()
        summary_l = QHBoxLayout(summary_row)
        summary_l.setContentsMargins(0, 0, 0, 0)
        summary_l.setSpacing(8)
        self._summary_icon = QLabel()
        self._summary_icon.setPixmap(
            lucide_icon(health.icon_name, size=20, color_hex=health.color_hex).pixmap(20, 20)
        )
        self._summary_icon.setFixedSize(24, 24)
        summary_l.addWidget(self._summary_icon, 0, Qt.AlignmentFlag.AlignTop)
        self._summary_text = QLabel(self._summary_line(health))
        self._summary_text.setWordWrap(True)
        summary_l.addWidget(self._summary_text, 1)
        content_l.addWidget(summary_row)

        scroll = QScrollArea(content)
        scroll.setObjectName("ItemHealthScroll")
        scroll.setWidgetResizable(False)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._scroll_body = QWidget()
        self._scroll_body.setObjectName("ItemHealthScrollBody")
        self._scroll_body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._scroll_l = QVBoxLayout(self._scroll_body)
        self._scroll_l.setContentsMargins(8, 8, 8, 8)
        self._scroll_l.setSpacing(12)
        for issue in health.issues:
            self._scroll_l.addWidget(self._issue_block(issue))
        self._scroll_l.addStretch(1)
        scroll.setWidget(self._scroll_body)
        self._scroll_body.adjustSize()
        self._scroll_body.setMinimumWidth(self._scroll_content_min_width(health))
        self._scroll_area = scroll
        content_l.addWidget(scroll, 1)

        btn_row = QWidget(content)
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(10)
        btn_l.addStretch(1)
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("DialogPrimaryButton")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_l.addWidget(ok_btn)
        content_l.addWidget(btn_row)
        root.addWidget(content, 1)

    @staticmethod
    def _scroll_content_min_width(health: ItemHealth) -> int:
        """Min width so long mono paths can scroll horizontally instead of wrapping."""
        mono = monos_font("JetBrains Mono", 11)
        fm = QFontMetrics(mono)
        pad = 8 + 8 + 30 + 20 + 24  # scroll margins, issue indent, icon column, breathing
        w = 0
        for issue in health.issues:
            if issue.detail:
                for line in issue.detail.splitlines():
                    w = max(w, fm.horizontalAdvance(line))
            for path in issue.bad_files:
                w = max(w, fm.horizontalAdvance(path))
            for path in issue.bad_files_wrong_name:
                w = max(w, fm.horizontalAdvance(path))
            for path in issue.bad_files_wrong_ext:
                w = max(w, fm.horizontalAdvance(path))
        return max(280, w + pad)

    @staticmethod
    def _summary_line(health: ItemHealth) -> str:
        if health.level == "ok":
            return "All checks passed."
        if health.level == "error":
            return "Critical issues need attention."
        return "Some checks need attention."

    def _issue_block(self, issue: HealthIssue) -> QWidget:
        block = QWidget()
        block_l = QVBoxLayout(block)
        block_l.setContentsMargins(0, 0, 0, 0)
        block_l.setSpacing(6)
        block_l.addWidget(self._issue_row(issue))
        if issue.issue_id == "work_file_naming" and (
            issue.bad_files_wrong_name or issue.bad_files_wrong_ext
        ):
            if issue.bad_files_wrong_name:
                sec = _HealthBadFilesSection(
                    issue.bad_files_wrong_name,
                    expanded=issue.level != "ok",
                    section_title=f"Wrong name — registered work extension ({len(issue.bad_files_wrong_name)})",
                )
                sec.setContentsMargins(30, 0, 0, 0)
                block_l.addWidget(sec)
            if issue.bad_files_wrong_ext:
                sec_e = _HealthBadFilesSection(
                    issue.bad_files_wrong_ext,
                    expanded=issue.level != "ok",
                    section_title=f"Wrong extension — not a DCC work type ({len(issue.bad_files_wrong_ext)})",
                )
                sec_e.setContentsMargins(30, 0, 0, 0)
                block_l.addWidget(sec_e)
            actions = QHBoxLayout()
            actions.setContentsMargins(30, 0, 0, 0)
            actions.setSpacing(8)
            if issue.bad_files_wrong_name:
                fix_btn = QPushButton("Fix name…", block)
                fix_btn.setObjectName("DialogSecondaryButton")
                fix_btn.clicked.connect(
                    lambda _checked=False, t=tuple(issue.bad_files_wrong_name): self._open_fix_naming_dialog(t)
                )
                actions.addWidget(fix_btn)
            if issue.bad_files_wrong_ext:
                clean_btn = QPushButton("Clean…", block)
                clean_btn.setObjectName("DialogDestructiveButton")
                clean_btn.clicked.connect(
                    lambda _checked=False, t=tuple(issue.bad_files_wrong_ext): self._open_clean_wrong_ext_dialog(
                        t
                    )
                )
                actions.addWidget(clean_btn)
            actions.addStretch(1)
            block_l.addLayout(actions)
        elif issue.issue_id == "houdini_backup_folder" and issue.bad_files:
            sec = _HealthBadFilesSection(
                issue.bad_files,
                expanded=issue.level != "ok",
                section_title=f"Backup folders ({len(issue.bad_files)})",
            )
            sec.setContentsMargins(30, 0, 0, 0)
            block_l.addWidget(sec)
            actions = QHBoxLayout()
            actions.setContentsMargins(30, 0, 0, 0)
            actions.setSpacing(8)
            clean_btn = QPushButton("Clean…", block)
            clean_btn.setObjectName("DialogDestructiveButton")
            clean_btn.clicked.connect(
                lambda _checked=False, t=tuple(issue.bad_files): self._open_clean_houdini_backup_dialog(t)
            )
            actions.addWidget(clean_btn)
            actions.addStretch(1)
            block_l.addLayout(actions)
        elif issue.bad_files:
            files_section = _HealthBadFilesSection(
                issue.bad_files,
                expanded=issue.level != "ok",
            )
            files_section.setContentsMargins(30, 0, 0, 0)
            block_l.addWidget(files_section)
        return block

    def _after_repair(self) -> None:
        self._refresh_issues_from_disk()
        self._poke_inspector_health_chip()
        self._poke_main_view_health_repaint()
        if self._on_repaired is not None:
            self._on_repaired()

    def _poke_inspector_health_chip(self) -> None:
        p: QWidget | None = self.parentWidget()
        for _ in range(16):
            if p is None:
                return
            st = getattr(p, "_asset_status", None)
            if st is not None:
                h = getattr(st, "_health", None)
                if h is not None and callable(getattr(h, "_refresh", None)):
                    try:
                        h._refresh()
                    except Exception:
                        pass
                return
            p = p.parentWidget()

    def _sync_naming_prefix_from_ctx(self) -> None:
        ctx = self._health_refresh_ctx
        if ctx is None:
            return
        ref, dep, _dcc = ctx
        dept_obj = _department_for_item(ref, dep)
        self._naming_prefix = (
            work_file_prefix(name=ref.name, department=dept_obj.name).strip() if dept_obj else ""
        )

    def _refresh_issues_from_disk(self) -> None:
        ctx = self._health_refresh_ctx
        if ctx is None:
            return
        ref, department, active_dcc_id = ctx
        health = assess_view_item_health(ref, department, active_dcc_id=active_dcc_id)
        if health is None:
            self.accept()
            return
        self._sync_naming_prefix_from_ctx()
        self._title_bar.apply_health(health)
        self._summary_icon.setPixmap(
            lucide_icon(health.icon_name, size=20, color_hex=health.color_hex).pixmap(20, 20)
        )
        self._summary_text.setText(self._summary_line(health))
        while self._scroll_l.count():
            item = self._scroll_l.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for issue in health.issues:
            self._scroll_l.addWidget(self._issue_block(issue))
        self._scroll_l.addStretch(1)
        self._scroll_body.setMinimumWidth(self._scroll_content_min_width(health))
        self._scroll_body.adjustSize()
        self._scroll_area.updateGeometry()

    def _poke_main_view_health_repaint(self) -> None:
        win = self.window()
        tv = getattr(win, "_main_view", None)
        if tv is None:
            return
        for name in ("_tile_view", "_list_view"):
            view = getattr(tv, name, None)
            if view is not None:
                try:
                    view.viewport().update()
                except Exception:
                    pass

    def _open_fix_naming_dialog(self, paths: tuple[str, ...]) -> None:
        if not paths:
            return
        if not self._naming_prefix:
            QMessageBox.warning(
                self,
                "Fix name",
                "Could not resolve work file prefix for this item. Re-open health from the main view.",
            )
            return
        dlg = WorkNamingFixDialog(
            parent=self,
            paths=paths,
            prefix=self._naming_prefix,
            on_applied=self._after_repair,
        )
        dlg.exec()

    def _open_clean_wrong_ext_dialog(self, paths: tuple[str, ...]) -> None:
        if not paths:
            return
        dlg = WorkInvalidExtCleanDialog(
            parent=self,
            paths=paths,
            on_deleted=self._after_repair,
        )
        dlg.exec()

    def _open_clean_houdini_backup_dialog(self, paths: tuple[str, ...]) -> None:
        if not paths:
            return
        dlg = HoudiniBackupCleanDialog(
            parent=self,
            paths=paths,
            on_deleted=self._after_repair,
        )
        dlg.exec()

    def _issue_row(self, issue: HealthIssue) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        icon_name = "heart" if issue.level == "ok" else "triangle-alert"
        color = _ITEM_HEALTH_COLORS.get(issue.level, MONOS_COLORS["text_label"])
        icon = QLabel()
        icon.setPixmap(lucide_icon(icon_name, size=16, color_hex=color).pixmap(16, 16))
        icon.setFixedSize(20, 20)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        title = QLabel(issue.title)
        title_font = monos_font("Inter", 13, QFont.Weight.DemiBold)
        title.setFont(title_font)
        text_col.addWidget(title)
        if issue.detail:
            detail = QLabel(issue.detail)
            detail.setObjectName("DialogHint")
            detail.setWordWrap(False)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            text_col.addWidget(detail)
        lay.addLayout(text_col, 1)
        return row
