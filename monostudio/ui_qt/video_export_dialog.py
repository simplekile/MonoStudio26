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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.video_media import (
    ExportMode,
    VideoFrameRange,
    _output_name_for_range,
    export_format_changes_container,
    export_format_is_gif,
    export_naming_template_for_mode,
    export_video_ranges,
    resolve_export_suffix,
)
from monostudio.ui_qt.style import MonosDialog, monos_font
from monostudio.ui_qt.video_preview_settings import (
    EXPORT_FORMAT_GIF,
    EXPORT_FORMAT_MKV,
    EXPORT_FORMAT_MOV,
    EXPORT_FORMAT_MP4,
    EXPORT_FORMAT_SOURCE,
    EXPORT_FORMAT_WEBM,
    EXPORT_NAMING_RANGE,
    EXPORT_NAMING_RANGE_INDEX,
    EXPORT_NAMING_SOURCE_INDEX,
    read_video_export_format,
    read_video_export_naming_mode,
    write_video_export_format,
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
        output_format: str,
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
        self._output_format = output_format
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
                output_format=self._output_format,
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

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, 1)

        settings_page = QWidget(self)
        settings_lay = QVBoxLayout(settings_page)
        settings_lay.setContentsMargins(0, 0, 0, 0)
        settings_lay.setSpacing(10)

        hint = QLabel(f"Export {len(ranges)} range(s) from {src.name}", settings_page)
        hint.setObjectName("DialogHint")
        settings_lay.addWidget(hint)

        out_row = QHBoxLayout()
        self._out_field = QLineEdit(settings_page)
        self._out_field.setObjectName("DialogLineEdit")
        default = default_output_dir or (src.parent / f"{src.stem}_cuts")
        self._out_field.setText(str(default))
        browse = QPushButton("Browse…", settings_page)
        browse.setObjectName("DialogSecondaryButton")
        browse.clicked.connect(self._browse)
        out_row.addWidget(self._out_field, 1)
        out_row.addWidget(browse, 0)
        settings_lay.addLayout(out_row)

        self._mode_combo = QComboBox(settings_page)
        self._mode_combo.addItem("Separate files (one per range)", "separate")
        self._mode_combo.addItem("Single concat file", "concat")
        settings_lay.addWidget(QLabel("Output mode", settings_page))
        settings_lay.addWidget(self._mode_combo)

        self._format_combo = QComboBox(settings_page)
        src_suffix = src.suffix or ".mp4"
        self._format_combo.addItem(f"Same as source ({src_suffix})", EXPORT_FORMAT_SOURCE)
        self._format_combo.addItem("MP4 (.mp4)", EXPORT_FORMAT_MP4)
        self._format_combo.addItem("QuickTime (.mov)", EXPORT_FORMAT_MOV)
        self._format_combo.addItem("Matroska (.mkv)", EXPORT_FORMAT_MKV)
        self._format_combo.addItem("WebM (.webm)", EXPORT_FORMAT_WEBM)
        self._format_combo.addItem("GIF (.gif)", EXPORT_FORMAT_GIF)
        saved_fmt = read_video_export_format(self._settings)
        fmt_idx = max(0, self._format_combo.findData(saved_fmt))
        self._format_combo.setCurrentIndex(fmt_idx)
        self._format_combo.currentIndexChanged.connect(self._update_export_hints)
        self._mode_combo.currentIndexChanged.connect(self._update_export_hints)
        settings_lay.addWidget(QLabel("Format", settings_page))
        settings_lay.addWidget(self._format_combo)

        self._naming_combo = QComboBox(settings_page)
        self._naming_combo.addItem("Range names", EXPORT_NAMING_RANGE)
        self._naming_combo.addItem("Source + index", EXPORT_NAMING_SOURCE_INDEX)
        self._naming_combo.addItem("Range names + index", EXPORT_NAMING_RANGE_INDEX)
        saved = read_video_export_naming_mode(self._settings)
        idx = max(0, self._naming_combo.findData(saved))
        self._naming_combo.setCurrentIndex(idx)
        self._naming_combo.currentIndexChanged.connect(self._update_export_hints)
        settings_lay.addWidget(QLabel("File naming", settings_page))
        settings_lay.addWidget(self._naming_combo)

        self._naming_preview = QLabel("", settings_page)
        self._naming_preview.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        self._naming_preview.setWordWrap(True)
        settings_lay.addWidget(self._naming_preview)

        self._quality_combo = QComboBox(settings_page)
        self._quality_combo.addItem("Fast cut (stream copy, keyframe-aligned)", "copy")
        self._quality_combo.addItem("Accurate cut (re-encode)", "reencode")
        self._quality_combo.currentIndexChanged.connect(self._update_export_hints)
        settings_lay.addWidget(QLabel("Quality", settings_page))
        settings_lay.addWidget(self._quality_combo)

        self._format_hint = QLabel("", settings_page)
        self._format_hint.setObjectName("DialogHint")
        self._format_hint.setWordWrap(True)
        settings_lay.addWidget(self._format_hint)

        self._status = QLabel("", settings_page)
        self._status.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        settings_lay.addWidget(self._status)
        settings_lay.addStretch(1)
        self._update_export_hints()

        progress = QWidget(self)
        progress_lay = QVBoxLayout(progress)
        progress_lay.setContentsMargins(0, 8, 0, 8)
        progress_lay.setSpacing(12)
        self._progress_title = QLabel("Exporting…", progress)
        self._progress_title.setObjectName("VideoExportProgressTitle")
        self._progress_title.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        progress_lay.addWidget(self._progress_title)
        self._progress = QProgressBar(progress)
        self._progress.setObjectName("VideoExportProgress")
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%p%")
        self._progress.setFixedHeight(10)
        progress_lay.addWidget(self._progress)
        self._progress_status = QLabel("Preparing…", progress)
        self._progress_status.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        progress_lay.addWidget(self._progress_status)
        self._progress_file = QLabel("", progress)
        self._progress_file.setObjectName("VideoExportProgressFile")
        self._progress_file.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        self._progress_file.setWordWrap(True)
        progress_lay.addWidget(self._progress_file)
        progress_lay.addStretch(1)

        self._stack.addWidget(settings_page)
        self._stack.addWidget(progress)

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
        self._export_btn = ok
        self._cancel_btn = cancel
        self._exporting = False

    def _export_total_steps(self) -> int:
        mode = self._mode_combo.currentData(Qt.ItemDataRole.UserRole) or "separate"
        return len(self._ranges) + (1 if mode == "concat" else 0)

    def _show_progress_page(self) -> None:
        total = max(1, self._export_total_steps())
        self._stack.setCurrentIndex(1)
        self._progress.setMinimum(0)
        self._progress.setMaximum(total)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._progress_status.setText(f"Starting export (0/{total})…")
        self._progress_file.setText("")
        if self._export_btn is not None:
            self._export_btn.setEnabled(False)
        if self._cancel_btn is not None:
            self._cancel_btn.setText("Cancel")
            self._cancel_btn.setEnabled(True)

    def _show_settings_page(self) -> None:
        self._stack.setCurrentIndex(0)
        if self._export_btn is not None:
            self._export_btn.setEnabled(True)
        if self._cancel_btn is not None:
            self._cancel_btn.setText("Cancel")
            self._cancel_btn.setEnabled(True)

    def outputs(self) -> list[Path]:
        return list(self._outputs)

    def _naming_mode(self) -> str:
        return self._naming_combo.currentData(Qt.ItemDataRole.UserRole) or EXPORT_NAMING_RANGE

    def _format_mode(self) -> str:
        return self._format_combo.currentData(Qt.ItemDataRole.UserRole) or EXPORT_FORMAT_SOURCE

    def _quality_is_reencode(self) -> bool:
        return self._quality_combo.currentData(Qt.ItemDataRole.UserRole) == "reencode"

    def _update_export_hints(self) -> None:
        self._update_naming_preview()
        fmt = self._format_mode()
        is_gif = export_format_is_gif(fmt)
        concat_idx = self._mode_combo.findData("concat")
        if concat_idx >= 0:
            model = self._mode_combo.model()
            item = model.item(concat_idx)
            if item is not None:
                item.setEnabled(not is_gif)
        if is_gif and (self._mode_combo.currentData(Qt.ItemDataRole.UserRole) or "") == "concat":
            self._mode_combo.setCurrentIndex(0)
        self._quality_combo.setEnabled(not is_gif)
        hints: list[str] = []
        if is_gif:
            hints.append("GIF: one file per range · max 24 fps · max width 720 px · no audio.")
        elif not self._quality_is_reencode() and export_format_changes_container(fmt, self._src):
            hints.append("Different format will re-encode for this export.")
        self._format_hint.setText(" ".join(hints))

    def _update_naming_preview(self) -> None:
        mode = self._naming_mode()
        template = export_naming_template_for_mode(mode)
        suffix = resolve_export_suffix(self._format_mode(), self._src)
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
        reencode = self._quality_is_reencode()
        naming = self._naming_mode()
        output_format = self._format_mode()
        if self._settings is not None:
            write_video_export_naming_mode(self._settings, naming)
            write_video_export_format(self._settings, output_format)
        self._exporting = True
        self._cancel_flag[0] = False
        self._show_progress_page()
        job = _ExportRunnable(
            self._src,
            self._ranges,
            out,
            fps=self._fps,
            mode=mode,
            reencode=reencode,
            output_format=output_format,
            naming_mode=naming,
            signaler=self._signaler,
            cancel_flag=self._cancel_flag,
        )
        self._pool.start(job)

    def _on_progress(self, cur: int, total: int, path) -> None:
        total = max(1, int(total))
        cur = max(0, int(cur))
        if path is None:
            self._progress.setMinimum(0)
            self._progress.setMaximum(0)
            step = min(total, cur + 1)
            self._progress_status.setText(f"Encoding {step}/{total}…")
            self._progress_file.setText("")
        else:
            self._progress.setMinimum(0)
            self._progress.setMaximum(total)
            self._progress.setValue(cur)
            pct = int(round(100 * cur / total))
            self._progress_status.setText(f"Completed {cur}/{total} ({pct}%)")
            self._progress_file.setText(Path(path).name)

    def _on_finished(self, outputs: object, error: object) -> None:
        self._exporting = False
        if error:
            self._show_settings_page()
            self._status.setText(str(error))
            return
        self._outputs = list(outputs) if outputs else []
        self.accept()

    def reject(self) -> None:  # noqa: D102
        if self._exporting:
            self._cancel_flag[0] = True
        super().reject()
