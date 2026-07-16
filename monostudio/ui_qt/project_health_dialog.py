"""Project-wide health scan and bulk cleanup dialog."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.fs_reader import build_project_index
from monostudio.core.project_health import (
    ProjectHealthScan,
    delete_project_health_files,
    delete_project_health_folders,
    format_byte_size,
    scan_project_health,
)
from monostudio.ui_qt.item_health_dialog import _watcher_allows_mutation_or_notify
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification import notify
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font

_MAX_PATH_ROWS = 200


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                _clear_layout(sub)


class _PathSection(QFrame):
    """One scan category: title + count/size meta + mono path rows."""

    def __init__(
        self,
        *,
        title: str,
        count: int,
        size_bytes: int,
        paths: tuple[str, ...],
        parent: QWidget | None = None,
        max_rows: int = _MAX_PATH_ROWS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ProjectHealthPathSection")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title_lab = QLabel(title, self)
        title_lab.setFont(monos_font("Inter", 12, QFont.Weight.DemiBold))
        title_lab.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_primary', '#fafafa')}; background: transparent;"
        )
        header.addWidget(title_lab, 0)
        header.addStretch(1)
        meta = QLabel(f"{count} · {format_byte_size(size_bytes)}", self)
        meta.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        meta.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_meta', '#71717a')}; background: transparent;"
        )
        header.addWidget(meta, 0)
        outer.addLayout(header)

        visible = list(paths)[:max_rows]
        for raw in visible:
            row = QLabel(raw, self)
            row.setFont(monos_font("JetBrains Mono", 12))
            row.setStyleSheet(
                f"color: {MONOS_COLORS.get('text_secondary', '#d4d4d8')}; background: transparent;"
            )
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.setWordWrap(False)
            row.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            outer.addWidget(row)
        if len(paths) > max_rows:
            more = QLabel(f"… and {len(paths) - max_rows} more", self)
            more.setObjectName("DialogHint")
            outer.addWidget(more)


class ProjectHealthDialog(MonosDialog):
    """Scan the open project for autosaves / stray work files / DCC backups."""

    def __init__(
        self,
        *,
        parent: QWidget | None,
        project_root: Path,
        on_cleaned: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._on_cleaned = on_cleaned
        self._scan: ProjectHealthScan | None = None

        self.setWindowTitle("Project health cleanup")
        self.setModal(True)
        self.setMinimumSize(640, 480)
        self.resize(760, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        hint = QLabel(
            "Scan every asset and shot for the same issues shown in Item Health: "
            "DCC autosaves, Blender/Houdini backups, and stray work-folder files. "
            "Deleted items are removed permanently (not moved to Trash).",
            self,
        )
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._summary = QLabel("Click Rescan to analyze the project.", self)
        self._summary.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._size_total = QLabel("", self)
        self._size_total.setFont(monos_font("JetBrains Mono", 12, QFont.Weight.Medium))
        self._size_total.setStyleSheet(
            f"color: {MONOS_COLORS.get('text_label', '#a1a1aa')}; background: transparent;"
        )
        self._size_total.setVisible(False)
        root.addWidget(self._size_total)

        checks = QVBoxLayout()
        checks.setContentsMargins(0, 0, 0, 0)
        checks.setSpacing(6)
        self._autosave_cb = QCheckBox("Autosave / incremental work files", self)
        self._blender_cb = QCheckBox("Blender backup files (.blend1–.blend3)", self)
        self._wrong_ext_cb = QCheckBox("Non-work extension files in work folders", self)
        self._houdini_cb = QCheckBox("Houdini backup folders", self)
        for cb in (self._autosave_cb, self._blender_cb, self._wrong_ext_cb, self._houdini_cb):
            cb.setEnabled(False)
            checks.addWidget(cb)
        root.addLayout(checks)

        self._rename_hint = QLabel("", self)
        self._rename_hint.setObjectName("DialogHint")
        self._rename_hint.setWordWrap(True)
        self._rename_hint.setVisible(False)
        root.addWidget(self._rename_hint)

        scroll = QScrollArea(self)
        scroll.setObjectName("ProjectHealthScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._list_host = QWidget()
        self._list_host.setObjectName("ProjectHealthListHost")
        self._list_l = QVBoxLayout(self._list_host)
        self._list_l.setContentsMargins(0, 0, 4, 0)
        self._list_l.setSpacing(10)
        empty = QLabel("Paths to clean will appear here after a scan.", self._list_host)
        empty.setObjectName("DialogHint")
        self._list_l.addWidget(empty)
        self._list_l.addStretch(1)
        scroll.setWidget(self._list_host)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._rescan_btn = QPushButton("Rescan project", self)
        self._rescan_btn.setObjectName("DialogSecondaryButton")
        self._rescan_btn.setIcon(
            lucide_icon("refresh-cw", size=14, color_hex=MONOS_COLORS["text_label"])
        )
        self._rescan_btn.clicked.connect(self._run_scan)
        actions.addWidget(self._rescan_btn)

        actions.addStretch(1)

        self._clean_btn = QPushButton("Clean selected", self)
        self._clean_btn.setObjectName("DialogDestructiveButton")
        self._clean_btn.setEnabled(False)
        self._clean_btn.clicked.connect(self._on_clean)
        actions.addWidget(self._clean_btn)

        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogSecondaryButton")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        root.addLayout(actions)

        self._run_scan()

    def _run_scan(self) -> None:
        self._rescan_btn.setEnabled(False)
        self._clean_btn.setEnabled(False)
        try:
            index = build_project_index(self._project_root)
            scan = scan_project_health(index)
        except OSError as e:
            QMessageBox.critical(self, "Scan failed", str(e))
            self._summary.setText("Could not scan the project.")
            self._size_total.setVisible(False)
            self._rescan_btn.setEnabled(True)
            return
        finally:
            self._rescan_btn.setEnabled(True)

        self._scan = scan
        self._apply_scan_to_ui(scan)

    def _set_cb(self, cb: QCheckBox, label: str, count: int, size_bytes: int) -> None:
        if count > 0:
            cb.setText(f"{label} ({count} · {format_byte_size(size_bytes)})")
            cb.setChecked(True)
            cb.setEnabled(True)
        else:
            cb.setText(label)
            cb.setChecked(False)
            cb.setEnabled(False)

    def _apply_scan_to_ui(self, scan: ProjectHealthScan) -> None:
        n_auto = len(scan.autosave_files)
        n_blend = len(scan.blender_backup_files)
        n_ext = len(scan.wrong_ext_files)
        n_hou = len(scan.houdini_backup_dirs)
        n_rename = len(scan.rename_candidates)

        if scan.is_empty():
            self._summary.setText("No cleanable issues found in work folders.")
            self._size_total.setVisible(False)
        else:
            parts: list[str] = []
            if n_auto:
                parts.append(f"{n_auto} autosave")
            if n_blend:
                parts.append(f"{n_blend} Blender backup")
            if n_ext:
                parts.append(f"{n_ext} non-work")
            if n_hou:
                parts.append(f"{n_hou} Houdini backup")
            self._summary.setText("Found: " + ", ".join(parts) + ".")
            total_b = scan.total_deletable_bytes
            self._size_total.setText(f"Reclaimable: {format_byte_size(total_b)}")
            self._size_total.setVisible(True)

        self._set_cb(
            self._autosave_cb,
            "Autosave / incremental work files",
            n_auto,
            scan.autosave_bytes,
        )
        self._set_cb(
            self._blender_cb,
            "Blender backup files (.blend1–.blend3)",
            n_blend,
            scan.blender_backup_bytes,
        )
        self._set_cb(
            self._wrong_ext_cb,
            "Non-work extension files in work folders",
            n_ext,
            scan.wrong_ext_bytes,
        )
        self._set_cb(
            self._houdini_cb,
            "Houdini backup folders",
            n_hou,
            scan.houdini_backup_bytes,
        )

        if n_rename:
            self._rename_hint.setText(
                f"{n_rename} misnamed work file{'s' if n_rename != 1 else ''} "
                f"({format_byte_size(scan.rename_bytes)}) look like typos — "
                "not deleted here. Use Item Health → Fix name on each entity."
            )
            self._rename_hint.setVisible(True)
        else:
            self._rename_hint.setVisible(False)

        _clear_layout(self._list_l)
        sections: list[tuple[str, int, int, tuple[str, ...]]] = []
        if n_auto:
            sections.append(
                ("Autosave / incremental", n_auto, scan.autosave_bytes, scan.autosave_files)
            )
        if n_blend:
            sections.append(
                (
                    "Blender backups",
                    n_blend,
                    scan.blender_backup_bytes,
                    scan.blender_backup_files,
                )
            )
        if n_ext:
            sections.append(
                ("Non-work extensions", n_ext, scan.wrong_ext_bytes, scan.wrong_ext_files)
            )
        if n_hou:
            sections.append(
                (
                    "Houdini backup folders",
                    n_hou,
                    scan.houdini_backup_bytes,
                    scan.houdini_backup_dirs,
                )
            )
        if n_rename:
            sections.append(
                (
                    "Rename manually (not deleted)",
                    n_rename,
                    scan.rename_bytes,
                    scan.rename_candidates,
                )
            )

        if not sections:
            empty = QLabel("Nothing to list.", self._list_host)
            empty.setObjectName("DialogHint")
            self._list_l.addWidget(empty)
        else:
            for title, count, size_b, paths in sections:
                self._list_l.addWidget(
                    _PathSection(
                        title=title,
                        count=count,
                        size_bytes=size_b,
                        paths=paths,
                        parent=self._list_host,
                        max_rows=40 if title.startswith("Rename") else _MAX_PATH_ROWS,
                    )
                )
        self._list_l.addStretch(1)
        self._clean_btn.setEnabled(scan.total_deletable > 0)

    def _paths_for_clean(self) -> tuple[list[Path], list[Path], int]:
        scan = self._scan
        if scan is None:
            return [], [], 0
        files: list[Path] = []
        folders: list[Path] = []
        size = 0
        if self._autosave_cb.isChecked() and self._autosave_cb.isEnabled():
            files.extend(Path(p) for p in scan.autosave_files)
            size += scan.autosave_bytes
        if self._blender_cb.isChecked() and self._blender_cb.isEnabled():
            files.extend(Path(p) for p in scan.blender_backup_files)
            size += scan.blender_backup_bytes
        if self._wrong_ext_cb.isChecked() and self._wrong_ext_cb.isEnabled():
            files.extend(Path(p) for p in scan.wrong_ext_files)
            size += scan.wrong_ext_bytes
        if self._houdini_cb.isChecked() and self._houdini_cb.isEnabled():
            folders.extend(Path(p) for p in scan.houdini_backup_dirs)
            size += scan.houdini_backup_bytes
        return files, folders, size

    def _on_clean(self) -> None:
        if self._scan is None:
            return
        files, folders, size_bytes = self._paths_for_clean()
        if not files and not folders:
            notify.info("Nothing selected to clean.")
            return

        n_files = len(files)
        n_folders = len(folders)
        parts: list[str] = []
        if n_files:
            parts.append(f"{n_files} file{'s' if n_files != 1 else ''}")
        if n_folders:
            parts.append(f"{n_folders} folder{'s' if n_folders != 1 else ''}")
        msg = (
            f"Permanently delete {' and '.join(parts)} "
            f"({format_byte_size(size_bytes)})?\n\n"
            "This cannot be undone. Pause the file watcher first if it is enabled."
        )
        if QMessageBox.question(self, "Confirm cleanup", msg) != QMessageBox.StandardButton.Yes:
            return

        touch = list(files) + list(folders)
        if not _watcher_allows_mutation_or_notify(self, touch):
            return

        file_failures = delete_project_health_files(files)
        folder_failures = delete_project_health_folders(folders)
        failures = file_failures + folder_failures
        if failures:
            detail = "\n".join(f"{p}\n  {err}" for p, err in failures[:8])
            if len(failures) > 8:
                detail += f"\n… and {len(failures) - 8} more"
            QMessageBox.warning(
                self,
                "Cleanup incomplete",
                f"Some paths could not be removed:\n\n{detail}",
            )
        else:
            notify.success(
                f"Project health cleanup finished — freed {format_byte_size(size_bytes)}."
            )

        if self._on_cleaned is not None:
            self._on_cleaned()
        self._run_scan()
