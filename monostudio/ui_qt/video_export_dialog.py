"""Export dialog for batch video range cut."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Signal, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
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
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.settings_section_widgets import style_settings_combo
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font, page_badge_accent_color
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

_ROW_ICON_PX = 36
_ROW_ICON_INNER = 18
_ROW_GAP = 12
_DIALOG_WIDTH = 680
_COMBO_WIDTH = 272
_PREVIEW_MAX_LINES = 3
_ZONE_PAD_H = 16
_ZONE_PAD_V = 14


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


def _source_breadcrumb(src: Path) -> str:
    parts = list(src.parent.parts)
    if len(parts) > 4:
        parts = parts[-3:]
    return " › ".join(parts + [src.name])


def _elide_middle(text: str, width: int, font: QFont) -> str:
    if width <= 8:
        return text
    return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideMiddle, width)


class _ElidingLabel(QLabel):
    """Single-line label that elides on resize; full text in tooltip."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
    ) -> None:
        super().__init__(parent)
        self._raw = ""
        self._elide_mode = elide_mode
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setWordWrap(False)

    def set_full_text(self, text: str) -> None:
        self._raw = (text or "").strip()
        tip = self._raw
        self.setToolTip(tip if tip else "")
        self._refresh_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_elide()

    def _refresh_elide(self) -> None:
        if not self._raw:
            super().setText("")
            return
        width = max(1, self.width())
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._raw, self._elide_mode, width))


class _ExportRowIcon(QFrame):
    def __init__(self, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoExportRowIcon")
        self.setFixedSize(_ROW_ICON_PX, _ROW_ICON_PX)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(self)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setPixmap(
            lucide_icon(icon_name, size=_ROW_ICON_INNER, color_hex=page_badge_accent_color("outbox")).pixmap(
                _ROW_ICON_INNER, _ROW_ICON_INNER
            )
        )
        lay.addWidget(lab)


class _ExportFilePreview(QWidget):
    """Mono file-name preview lines with middle elision on resize."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoExportFilePreview")
        self._font = monos_font("JetBrains Mono", 11, QFont.Weight.Normal)
        self._raw_lines: list[str] = []
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 4, 0, 0)
        self._lay.setSpacing(2)
        self._labels: list[QLabel] = []

    def set_lines(self, lines: list[str]) -> None:
        self._raw_lines = list(lines)
        while len(self._labels) < len(lines):
            lab = QLabel(self)
            lab.setObjectName("VideoExportPreviewLine")
            lab.setFont(self._font)
            self._labels.append(lab)
            self._lay.addWidget(lab)
        for i, lab in enumerate(self._labels):
            if i < len(lines):
                lab.setText(lines[i])
                lab.show()
            else:
                lab.hide()
        self._refresh_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_elide()

    def _refresh_elide(self) -> None:
        width = max(1, self.width() - 4)
        for i, lab in enumerate(self._labels):
            if i >= len(self._raw_lines):
                continue
            lab.setText(_elide_middle(self._raw_lines[i], width, self._font))


class _ExportSettingRow(QFrame):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        icon: str,
        title: str,
        description: str = "",
        control: QWidget | None = None,
        badge: str | None = None,
        body: QWidget | None = None,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VideoExportRow")
        pad_v = 8 if compact else 10
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, pad_v, 0, pad_v)
        outer.setSpacing(_ROW_GAP)
        outer.addWidget(_ExportRowIcon(icon, self), 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        if title or badge:
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(8)
            if title:
                title_lab = QLabel(title, self)
                title_lab.setObjectName("VideoExportRowTitle")
                title_lab.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
                title_row.addWidget(title_lab, 0)
            if badge:
                badge_lab = QLabel(badge, self)
                badge_lab.setObjectName("VideoExportBadge")
                badge_lab.setFont(monos_font("Inter", 10, QFont.Weight.Bold))
                title_row.addWidget(badge_lab, 0)
            title_row.addStretch(1)
            text_col.addLayout(title_row)

        if description:
            desc = QLabel(description, self)
            desc.setObjectName("VideoExportRowDesc")
            desc.setWordWrap(True)
            desc.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
            text_col.addWidget(desc)

        if body is not None:
            text_col.addWidget(body)

        text_wrap = QWidget(self)
        text_wrap.setObjectName("VideoExportRowText")
        text_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_wrap.setLayout(text_col)
        outer.addWidget(text_wrap, 1)

        if control is not None:
            control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            outer.addWidget(control, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)


def _export_divider(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("VideoExportDivider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


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
        self.setMinimumWidth(_DIALOG_WIDTH)
        self.resize(_DIALOG_WIDTH, 640)
        self._src = src
        self._ranges = list(ranges)
        self._fps = fps
        self._settings = settings
        self._default_output_dir = default_output_dir or (src.parent / f"{src.stem}_cuts")
        self._output_dir = self._default_output_dir
        self._cancel_flag = [False]
        self._pool = QThreadPool.globalInstance()
        self._signaler = _ExportSignaler(self)
        self._signaler.progress.connect(self._on_progress)
        self._signaler.finished.connect(self._on_finished)
        self._outputs: list[Path] = []
        self._header_title_text = f"Export {len(ranges)} range(s) from {src.stem}"
        self._header_breadcrumb_text = _source_breadcrumb(src)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header_frame = QFrame(self)
        header_frame.setObjectName("VideoExportHeader")
        header_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_lay = QHBoxLayout(header_frame)
        header_lay.setContentsMargins(_ZONE_PAD_H, _ZONE_PAD_V, _ZONE_PAD_H, _ZONE_PAD_V)
        header_lay.setSpacing(12)
        header_icon = _ExportRowIcon("upload", header_frame)
        header_lay.addWidget(header_icon, 0, Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)
        self._title_label = _ElidingLabel(
            header_frame,
            elide_mode=Qt.TextElideMode.ElideRight,
        )
        self._title_label.setObjectName("VideoExportDialogTitle")
        self._title_label.setFont(monos_font("Inter", 15, QFont.Weight.Bold))
        self._title_label.set_full_text(self._header_title_text)
        title_col.addWidget(self._title_label)
        self._breadcrumb_label = _ElidingLabel(header_frame)
        self._breadcrumb_label.setObjectName("VideoExportBreadcrumb")
        self._breadcrumb_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        self._breadcrumb_label.set_full_text(self._header_breadcrumb_text)
        title_col.addWidget(self._breadcrumb_label)
        title_wrap = QWidget(header_frame)
        title_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title_wrap.setLayout(title_col)
        header_lay.addWidget(title_wrap, 1)

        close_btn = QToolButton(header_frame)
        close_btn.setObjectName("VideoPreviewDialogCloseBtn")
        close_btn.setAutoRaise(False)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(28, 28)
        close_btn.setIcon(lucide_icon("x", size=16, color_hex="#fafafa"))
        close_btn.clicked.connect(self.reject)
        header_lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header_frame)

        body_frame = QFrame(self)
        body_frame.setObjectName("VideoExportBody")
        body_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body_lay = QVBoxLayout(body_frame)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._stack = QStackedWidget(body_frame)
        body_lay.addWidget(self._stack, 1)
        root.addWidget(body_frame, 1)

        settings_page = QWidget(self)
        settings_outer = QVBoxLayout(settings_page)
        settings_outer.setContentsMargins(0, 0, 0, 0)
        settings_outer.setSpacing(0)

        scroll = QScrollArea(settings_page)
        scroll.setObjectName("VideoExportScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scroll_body = QWidget(scroll)
        scroll_body.setObjectName("VideoExportScrollBody")
        settings_lay = QVBoxLayout(scroll_body)
        settings_lay.setContentsMargins(_ZONE_PAD_H, 12, _ZONE_PAD_H, 12)
        settings_lay.setSpacing(0)

        dest_prefix = QLabel("", scroll_body)
        dest_prefix.setObjectName("VideoExportDestPrefix")
        dest_prefix.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        self._dest_summary = _ElidingLabel(scroll_body)
        self._dest_summary.setObjectName("VideoExportDestSummary")
        self._dest_summary.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        dest_text_row = QHBoxLayout()
        dest_text_row.setContentsMargins(0, 0, 0, 0)
        dest_text_row.setSpacing(6)
        dest_text_row.addWidget(dest_prefix, 0)
        dest_text_row.addWidget(self._dest_summary, 1)
        dest_text_wrap = QWidget(scroll_body)
        dest_text_wrap.setLayout(dest_text_row)
        self._dest_prefix_label = dest_prefix
        dest_prefix.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        browse = QPushButton("Browse…", scroll_body)
        browse.setObjectName("SettingsInlineActionButton")
        browse.setFixedWidth(96)
        browse.clicked.connect(self._browse)
        dest_row = _ExportSettingRow(
            scroll_body,
            icon="film",
            title="",
            description="",
            control=browse,
            body=dest_text_wrap,
            compact=True,
        )
        dest_frame = QFrame(scroll_body)
        dest_frame.setObjectName("VideoExportDestRow")
        dest_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dest_frame_lay = QVBoxLayout(dest_frame)
        dest_frame_lay.setContentsMargins(12, 4, 12, 4)
        dest_frame_lay.setSpacing(0)
        dest_frame_lay.addWidget(dest_row)
        settings_lay.addWidget(dest_frame)
        settings_lay.addWidget(_export_divider(scroll_body))

        self._mode_combo = QComboBox(scroll_body)
        self._mode_combo.addItem("Separate files (one per range)", "separate")
        self._mode_combo.addItem("Single concat file", "concat")
        style_settings_combo(self._mode_combo, width=_COMBO_WIDTH)
        self._mode_combo.currentIndexChanged.connect(self._update_export_hints)
        settings_lay.addWidget(
            _ExportSettingRow(
                scroll_body,
                icon="layers",
                title="Output mode",
                description="Choose how to export the ranges.",
                control=self._mode_combo,
            )
        )
        settings_lay.addWidget(_export_divider(scroll_body))

        self._format_combo = QComboBox(scroll_body)
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
        style_settings_combo(self._format_combo, width=_COMBO_WIDTH)
        self._format_combo.currentIndexChanged.connect(self._update_export_hints)
        settings_lay.addWidget(
            _ExportSettingRow(
                scroll_body,
                icon="file-video",
                title="Format",
                description="Choose the output file format.",
                control=self._format_combo,
            )
        )
        settings_lay.addWidget(_export_divider(scroll_body))

        self._naming_combo = QComboBox(scroll_body)
        self._naming_combo.addItem("Range names", EXPORT_NAMING_RANGE)
        self._naming_combo.addItem("Source + index", EXPORT_NAMING_SOURCE_INDEX)
        self._naming_combo.addItem("Range names + index", EXPORT_NAMING_RANGE_INDEX)
        saved = read_video_export_naming_mode(self._settings)
        idx = max(0, self._naming_combo.findData(saved))
        self._naming_combo.setCurrentIndex(idx)
        style_settings_combo(self._naming_combo, width=_COMBO_WIDTH)
        self._naming_combo.currentIndexChanged.connect(self._update_export_hints)
        settings_lay.addWidget(
            _ExportSettingRow(
                scroll_body,
                icon="file-text",
                title="File naming",
                description="Define how the output files should be named.",
                control=self._naming_combo,
            )
        )
        settings_lay.addWidget(_export_divider(scroll_body))

        self._preview_desc = QLabel("", scroll_body)
        self._preview_desc.setObjectName("VideoExportRowDesc")
        self._preview_desc.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        self._file_preview = _ExportFilePreview(scroll_body)
        preview_body = QWidget(scroll_body)
        preview_body_lay = QVBoxLayout(preview_body)
        preview_body_lay.setContentsMargins(0, 0, 0, 0)
        preview_body_lay.setSpacing(2)
        preview_body_lay.addWidget(self._preview_desc)
        preview_body_lay.addWidget(self._file_preview)
        settings_lay.addWidget(
            _ExportSettingRow(
                scroll_body,
                icon="eye",
                title="Preview",
                description="",
                body=preview_body,
            )
        )
        settings_lay.addWidget(_export_divider(scroll_body))

        self._quality_combo = QComboBox(scroll_body)
        self._quality_combo.addItem("Fast cut (stream copy, keyframe-aligned)", "copy")
        self._quality_combo.addItem("Accurate cut (re-encode)", "reencode")
        style_settings_combo(self._quality_combo, width=_COMBO_WIDTH)
        self._quality_combo.currentIndexChanged.connect(self._update_export_hints)
        settings_lay.addWidget(
            _ExportSettingRow(
                scroll_body,
                icon="gauge",
                title="Quality",
                description="Choose the export quality and speed.",
                control=self._quality_combo,
                badge="Recommended",
            )
        )

        self._format_hint = QLabel("", scroll_body)
        self._format_hint.setObjectName("DialogHint")
        self._format_hint.setWordWrap(True)
        settings_lay.addSpacing(8)
        settings_lay.addWidget(self._format_hint)

        self._status = QLabel("", scroll_body)
        self._status.setObjectName("DialogHint")
        self._status.setWordWrap(True)
        settings_lay.addWidget(self._status)
        settings_lay.addStretch(1)

        scroll.setWidget(scroll_body)
        settings_outer.addWidget(scroll, 1)

        progress = QWidget(self)
        progress_lay = QVBoxLayout(progress)
        progress_lay.setContentsMargins(_ZONE_PAD_H, 16, _ZONE_PAD_H, 16)
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

        footer_frame = QFrame(self)
        footer_frame.setObjectName("VideoExportFooter")
        footer_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer = QHBoxLayout(footer_frame)
        footer.setContentsMargins(_ZONE_PAD_H, 12, _ZONE_PAD_H, _ZONE_PAD_V)
        footer.setSpacing(8)
        reset_btn = QPushButton("Reset to defaults", footer_frame)
        reset_btn.setObjectName("DialogSecondaryButton")
        reset_btn.setIcon(lucide_icon("refresh-cw", size=14, color_hex=MONOS_COLORS["text_label"]))
        reset_btn.clicked.connect(self._reset_defaults)
        footer.addWidget(reset_btn, 0)
        footer.addStretch(1)
        self._cancel_btn = QPushButton("Cancel", footer_frame)
        self._cancel_btn.setObjectName("DialogSecondaryButton")
        self._cancel_btn.clicked.connect(self.reject)
        footer.addWidget(self._cancel_btn, 0)
        self._export_btn = QPushButton("Export", footer_frame)
        self._export_btn.setObjectName("DialogPrimaryButton")
        self._export_btn.setIcon(lucide_icon("upload", size=14, color_hex="#fafafa"))
        self._export_btn.clicked.connect(self._start_export)
        footer.addWidget(self._export_btn, 0)
        root.addWidget(footer_frame)

        self._exporting = False
        self._update_destination_summary()
        self._update_export_hints()

    def _destination_path_text(self) -> str:
        chain = list(self._output_dir.parts)
        if len(chain) <= 3:
            return " | ".join(chain)
        return " | ".join([chain[-3], "…", chain[-1]])

    def _update_destination_summary(self) -> None:
        n = len(self._ranges)
        self._dest_prefix_label.setText(f"{n} range(s) selected |")
        path_text = self._destination_path_text()
        self._dest_summary.set_full_text(path_text)
        self._dest_summary.setToolTip(str(self._output_dir))

    def _reset_defaults(self) -> None:
        self._output_dir = self._default_output_dir
        self._update_destination_summary()
        self._mode_combo.setCurrentIndex(0)
        self._format_combo.setCurrentIndex(0)
        self._naming_combo.setCurrentIndex(0)
        self._quality_combo.setCurrentIndex(0)
        self._status.clear()
        self._update_export_hints()

    def _export_total_steps(self) -> int:
        mode = self._mode_combo.currentData(Qt.ItemDataRole.UserRole) or "separate"
        return len(self._ranges) + (1 if mode == "concat" else 0)

    def _show_progress_page(self) -> None:
        total_steps = max(1, self._export_total_steps())
        total_units = total_steps * 100
        self._stack.setCurrentIndex(1)
        self._progress.setMinimum(0)
        self._progress.setMaximum(total_units)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._progress_status.setText("Starting export… 0%")
        self._progress_file.setText("")
        self._export_btn.setEnabled(False)
        self._cancel_btn.setText("Cancel")
        self._cancel_btn.setEnabled(True)

    def _show_settings_page(self) -> None:
        self._stack.setCurrentIndex(0)
        self._export_btn.setEnabled(True)
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
        for i, rng in enumerate(self._ranges, start=1):
            name = _output_name_for_range(stem, suffix, i, rng, template, used_names=used)
            lines.append(name)
            if len(lines) >= _PREVIEW_MAX_LINES:
                break
        remaining = len(self._ranges) - len(lines)
        if remaining > 0:
            lines.append(f"… +{remaining} more")
        if not lines:
            lines.append(f"{stem}_001{suffix}")
        count = len(self._ranges)
        noun = "file" if count == 1 else "files"
        self._preview_desc.setText(f"{count} {noun} will be exported:")
        self._file_preview.set_lines(lines)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", str(self._output_dir))
        if d:
            self._output_dir = Path(d)
            self._update_destination_summary()
            self._update_naming_preview()

    def _start_export(self) -> None:
        if self._exporting:
            return
        out = self._output_dir
        if not str(out).strip():
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
        cur = max(0, min(int(cur), total))
        self._progress.setMinimum(0)
        self._progress.setMaximum(total)
        self._progress.setValue(cur)
        pct = int(round(100 * cur / total))
        range_count = max(1, len(self._ranges))
        total_steps = max(1, self._export_total_steps())
        joining = total_steps > range_count and cur >= int(total * range_count / total_steps)
        if cur >= total:
            self._progress_status.setText(f"Finishing… {pct}%")
        elif joining:
            self._progress_status.setText(f"Joining clips · {pct}%")
            if path is not None:
                self._progress_file.setText(Path(path).name)
        elif path is not None:
            step_idx = min(range_count, max(1, int(cur * range_count / total) + 1))
            self._progress_status.setText(f"Encoding {step_idx}/{range_count} · {pct}%")
            self._progress_file.setText(Path(path).name)
        else:
            self._progress_status.setText(f"Encoding… {pct}%")
            self._progress_file.setText("")

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
