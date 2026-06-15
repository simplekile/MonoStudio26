"""Export dialog for batch video range cut."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from monostudio.core.video_media import (
    ExportMode,
    VideoFrameRange,
    _output_name_for_range,
    export_naming_template_for_mode,
    export_video_ranges,
)
from monostudio.ui_qt.style import MonosDialog, monos_font
from monostudio.ui_qt.video_preview_settings import (
    EXPORT_NAMING_RANGE,
    EXPORT_NAMING_RANGE_INDEX,
    EXPORT_NAMING_SOURCE_INDEX,
    read_video_export_naming_mode,
    write_video_export_naming_mode,
)


class _ExportSignaler(QObject):
    progress = Signal(int, int, object)
    finished = Signal(object, object)  # list[Path] | None, error str | None


class _ExportRunnable(QRunnable):
    def __init__(
        self,
        src: Path,
        ranges: list[VideoFrameRange],
        output_dir: Path,
        *,
        fps: float,
        mode: ExportMode,
        reencode: bool,
        naming_mode: str,
        signaler: _ExportSignaler,
        cancel_flag: list[bool],
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._src = src
        self._ranges = ranges
        self._output_dir = output_dir
        self._fps = fps
        self._mode = mode
        self._reencode = reencode
        self._naming_mode = naming_mode
        self._signaler = signaler
        self._cancel_flag = cancel_flag

    def run(self) -> None:
        try:
            def progress(cur, total, _path):
                self._signaler.progress.emit(cur, total, _path)

            def cancel():
                return bool(self._cancel_flag and self._cancel_flag[0])

            outputs = export_video_ranges(
                self._src,
                self._ranges,
                self._output_dir,
                fps=self._fps,
                mode=self._mode,
                reencode=self._reencode,
                naming_mode=self._naming_mode,
                progress_callback=progress,
                cancel_check=cancel,
            )
            self._signaler.finished.emit(outputs, None)
        except Exception as e:
            self._signaler.finished.emit(None, str(e))


class VideoExportDialog(MonosDialog):
    """Confirm export settings and run FFmpeg in background."""

    def __init__(
        self,
        src: Path,
        ranges: list[VideoFrameRange],
        *,
        fps: float,
        default_output_dir: Path | None = None,
        settings: QSettings | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VideoExportDialog")
        self.setWindowTitle("Export video ranges")
        self.setModal(True)
        self._src = src
        self._ranges = list(ranges)
        self._fps = fps
        self._settings = settings
        self._cancel_flag = [False]
        self._pool = QThreadPool.globalInstance()
        self._signaler = _ExportSignaler(self)
        self._signaler.progress.connect(self._on_progress)
        self._signaler.finished.connect(self._on_finished)
        self._outputs: list[Path] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hint = QLabel(f"Export {len(ranges)} range(s) from {src.name}", self)
        hint.setObjectName("DialogHint")
        root.addWidget(hint)

        out_row = QHBoxLayout()
        self._out_field = QLineEdit(self)
        self._out_field.setObjectName("DialogLineEdit")
        default = default_output_dir or (src.parent / f"{src.stem}_cuts")
        self._out_field.setText(str(default))
        browse = QPushButton("Browse…", self)
        browse.setObjectName("DialogSecondaryButton")
        browse.clicked.connect(self._browse)
        out_row.addWidget(self._out_field, 1)
        out_row.addWidget(browse, 0)
        root.addLayout(out_row)

        self._mode_combo = QComboBox(self)
        self._mode_combo.addItem("Separate files (one per range)", "separate")
        self._mode_combo.addItem("Single concat file", "concat")
        root.addWidget(QLabel("Output mode", self))
        root.addWidget(self._mode_combo)

        self._naming_combo = QComboBox(self)
        self._naming_combo.addItem("Range names", EXPORT_NAMING_RANGE)
        self._naming_combo.addItem("Source + index", EXPORT_NAMING_SOURCE_INDEX)
        self._naming_combo.addItem("Range names + index", EXPORT_NAMING_RANGE_INDEX)
        saved = read_video_export_naming_mode(settings)
        idx = max(0, self._naming_combo.findData(saved))
        self._naming_combo.setCurrentIndex(idx)
        self._naming_combo.currentIndexChanged.connect(self._update_naming_preview)
        root.addWidget(QLabel("File naming", self))
        root.addWidget(self._naming_combo)

        self._naming_preview = QLabel("", self)
        self._naming_preview.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        self._naming_preview.setWordWrap(True)
        root.addWidget(self._naming_preview)
        self._update_naming_preview()

        self._quality_combo = QComboBox(self)
        self._quality_combo.addItem("Fast cut (stream copy, keyframe-aligned)", "copy")
        self._quality_combo.addItem("Accurate cut (re-encode)", "reencode")
        root.addWidget(QLabel("Quality", self))
        root.addWidget(self._quality_combo)

        self._progress = QProgressBar(self)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status = QLabel("", self)
        self._status.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        root.addWidget(self._status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok:
            ok.setText("Export")
            ok.setObjectName("DialogPrimaryButton")
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel:
            cancel.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._start_export)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._buttons = buttons
        self._exporting = False

    def outputs(self) -> list[Path]:
        return list(self._outputs)

    def _naming_mode(self) -> str:
        return self._naming_combo.currentData(Qt.ItemDataRole.UserRole) or EXPORT_NAMING_RANGE

    def _update_naming_preview(self) -> None:
        mode = self._naming_mode()
        template = export_naming_template_for_mode(mode)
        suffix = self._src.suffix or ".mp4"
        stem = self._src.stem
        used: set[str] = set()
        lines: list[str] = []
        for i, rng in enumerate(self._ranges[:2], start=1):
            name = _output_name_for_range(stem, suffix, i, rng, template, used_names=used)
            lines.append(name)
        if len(self._ranges) > 2:
            lines.append("…")
        if not lines:
            lines.append(f"{stem}_001{suffix}")
        self._naming_preview.setText("Preview: " + " · ".join(lines))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self._out_field.text())
        if d:
            self._out_field.setText(d)

    def _start_export(self) -> None:
        if self._exporting:
            return
        out = Path(self._out_field.text().strip())
        if not out:
            self._status.setText("Choose an output folder.")
            return
        mode = self._mode_combo.currentData(Qt.ItemDataRole.UserRole) or "separate"
        reencode = self._quality_combo.currentData(Qt.ItemDataRole.UserRole) == "reencode"
        naming = self._naming_mode()
        if self._settings is not None:
            write_video_export_naming_mode(self._settings, naming)
        self._exporting = True
        self._cancel_flag[0] = False
        self._progress.setVisible(True)
        self._progress.setMaximum(len(self._ranges) + (1 if mode == "concat" else 0))
        self._progress.setValue(0)
        self._buttons.setEnabled(False)
        job = _ExportRunnable(
            self._src,
            self._ranges,
            out,
            fps=self._fps,
            mode=mode,
            reencode=reencode,
            naming_mode=naming,
            signaler=self._signaler,
            cancel_flag=self._cancel_flag,
        )
        self._pool.start(job)

    def _on_progress(self, cur: int, total: int, _path) -> None:
        self._progress.setMaximum(max(1, total))
        self._progress.setValue(cur)
        self._status.setText(f"Processing {cur}/{total}…")

    def _on_finished(self, outputs: object, error: object) -> None:
        self._exporting = False
        self._buttons.setEnabled(True)
        if error:
            self._status.setText(str(error))
            return
        self._outputs = list(outputs) if outputs else []
        self.accept()

    def reject(self) -> None:  # noqa: D102
        if self._exporting:
            self._cancel_flag[0] = True
        super().reject()
