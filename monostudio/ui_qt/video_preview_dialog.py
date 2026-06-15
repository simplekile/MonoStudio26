"""Video preview dialog — playback, multi-range review, export."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QEvent, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QMouseEvent, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.video_media import (
    VideoFrameRange,
    VideoInfo,
    extract_video_frame_png_bytes,
    fallback_scrub_snap_frames,
    format_frame_label,
    format_position_display,
    format_timecode,
    TimeDisplayMode,
    list_video_siblings,
    load_video_ranges_for_preview,
    new_range_id,
    probe_video,
    probe_video_keyframe_frames,
    ranges_content_equal,
    save_video_ranges_local_draft,
    save_video_ranges_sidecar,
    sec_to_frame,
    snap_frame_to_nearest_keyframe,
    validate_range,
)
from monostudio.ui_qt.dialog_geometry import (
    apply_dialog_geometry,
    clamp_dialog_to_bounds,
    main_window_bounds,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import position_popup_above_rect, position_popup_near_anchor
from monostudio.ui_qt.review_tools_panel import (
    ReviewToolMode,
    ReviewToolsPanel,
    ReviewToolStrip,
    ReviewWorkspace,
)
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, MonosMenu, monos_font
from monostudio.ui_qt.video_export_dialog import VideoExportDialog
from monostudio.ui_qt.video_player_backend import (
    ExternalPlayerBackend,
    VideoPlayerBackend,
    create_video_player_backend,
    next_speed,
)
from monostudio.ui_qt.video_preview_context import PreviewContext, VideoPreviewOpenRequest
from monostudio.ui_qt.video_preview_scrubber import VideoPreviewScrubber
from monostudio.ui_qt.video_preview_settings import (
    read_review_tool_mode,
    read_review_workspace,
    read_video_preview_precise_scrub_drag,
    read_video_preview_time_display,
    write_review_tool_mode,
    write_review_workspace,
    write_video_preview_precise_scrub_drag,
    write_video_preview_time_display,
    TIME_DISPLAY_FRAME,
    TIME_DISPLAY_TIMECODE,
)

logger = logging.getLogger(__name__)

_HOVER_PREVIEW_W = 160
_HOVER_PREVIEW_H = 90
_HOVER_ENCODE_W = 128
_HOVER_CACHE_MAX = 128
_HOVER_FETCH_DEBOUNCE_MS = 30
_SCRUB_SEEK_INTERVAL_KEYFRAME_MS = 66
_SCRUB_SEEK_INTERVAL_KEYFRAME_MPV_MS = 50
_SCRUB_SEEK_INTERVAL_PRECISE_MS = 120
_SCRUB_SEEK_INTERVAL_PRECISE_MPV_MS = 90
_VIDEO_SCRUB_DRAG_THRESHOLD_PX = 6
_PREVIEW_CHROME_PAD_H = 12
_PREVIEW_CHROME_PAD_V = 8
_RANGE_UNDO_MAX = 50


@dataclass(frozen=True)
class _RangeEditSnapshot:
    ranges: tuple[VideoFrameRange, ...]
    draft_in: int | None
    draft_out: int | None
    active_range_id: str | None
    range_edit_unlocked: bool
_PREVIEW_TOPBAR_H = 40
_DIALOG_BORDER_INSET = 1  # match _MONOS_DIALOG_BORDER_INSET — native embed vs MonosDialog border
_VIDEO_NATIVE_CLIP_BOTTOM = 1  # mpv / QVideoWidget HWND bleed above timeline divider


class _HoverFrameSignaler(QObject):
    ready = Signal(int, int, object)  # token, frame, bytes | None


class _KeyframeProbeSignaler(QObject):
    ready = Signal(int, object, object)  # token, path, list[int]


class _KeyframeProbeRunnable(QRunnable):
    def __init__(
        self,
        path: Path,
        fps: float,
        frame_count: int,
        token: int,
        signaler: _KeyframeProbeSignaler,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._fps = fps
        self._frame_count = frame_count
        self._token = token
        self._signaler = signaler

    def run(self) -> None:
        frames = probe_video_keyframe_frames(
            self._path,
            fps=self._fps,
            frame_count=self._frame_count,
        )
        self._signaler.ready.emit(self._token, self._path, frames)


class _HoverFrameRunnable(QRunnable):
    def __init__(
        self,
        path: Path,
        sec: float,
        frame: int,
        token: int,
        signaler: _HoverFrameSignaler,
        *,
        keyframe_aligned: bool = True,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._sec = sec
        self._frame = frame
        self._token = token
        self._signaler = signaler
        self._keyframe_aligned = keyframe_aligned

    def run(self) -> None:
        data = extract_video_frame_png_bytes(
            self._path,
            self._sec,
            width=_HOVER_ENCODE_W,
            keyframe_aligned=self._keyframe_aligned,
        )
        self._signaler.ready.emit(self._token, self._frame, data)


class VideoPreviewDialog(MonosDialog):
    """Non-modal video player with multi-range list and FFmpeg export."""

    closed = Signal()
    export_completed = Signal(object)  # list[Path]
    open_all_notes_requested = Signal()

    def __init__(
        self,
        path: Path,
        *,
        request: VideoPreviewOpenRequest | None = None,
        sibling_paths: list[Path] | None = None,
        settings=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewDialog")
        self.set_host_dim_overlay_enabled(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        if request is not None:
            self._request = request
            path = request.path
            sibling_paths = request.sibling_paths or sibling_paths
        else:
            self._request = VideoPreviewOpenRequest(path=path, context=PreviewContext.entity)
        self._context = self._request.context
        self._profile_key = self._request.settings_profile_key
        self._entity_path = self._request.entity_path
        self._department_id = self._request.department_id
        self._settings = settings
        self._geometry_applied = False
        self._locked_size: QSize | None = None
        min_w, min_h = (1280, 720) if self._context == PreviewContext.entity else (960, 540)
        self.setMinimumSize(min_w, min_h)
        self._paths = list(sibling_paths) if sibling_paths else list_video_siblings(path)
        if path not in self._paths:
            self._paths = [path] + self._paths
        self._path_index = max(0, self._paths.index(path)) if path in self._paths else 0
        self._path = path
        self._info: VideoInfo | None = None
        self._ranges: list[VideoFrameRange] = []
        self._published_ranges: list[VideoFrameRange] = []
        self._active_range_id: str | None = None
        self._range_edit_unlocked = False
        self._draft_in: int | None = None
        self._draft_out: int | None = None
        self._range_undo_stack: list[_RangeEditSnapshot] = []
        self._range_redo_stack: list[_RangeEditSnapshot] = []
        self._applying_range_undo = False
        self._loop_playback = False
        self._speed = 1.0
        self._volume = 80
        self._scrubbing = False
        self._playback_primed = False
        self._video_attached = False
        self._was_playing_before_scrub = False
        self._video_scrub_pending = False
        self._video_scrub_active = False
        self._video_scrub_start_x = 0
        self._video_scrub_origin_frame = 0
        self._pending_scrub_frame: int | None = None
        self._last_scrub_video_frame: int | None = None
        self._fullscreen = False
        self._pending_range_label = ""
        self._time_display_mode: TimeDisplayMode = read_video_preview_time_display(settings)
        self._loop_timer = QTimer(self)
        self._loop_timer.setInterval(50)
        self._loop_timer.timeout.connect(self._check_loop)
        self._scrub_seek_timer = QTimer(self)
        self._scrub_seek_timer.setSingleShot(True)
        self._scrub_seek_timer.setInterval(_SCRUB_SEEK_INTERVAL_KEYFRAME_MS)
        self._scrub_seek_timer.timeout.connect(self._flush_scrub_preview)
        self._hover_signaler = _HoverFrameSignaler(self)
        self._hover_signaler.ready.connect(self._on_hover_frame_ready)
        self._keyframe_probe_signaler = _KeyframeProbeSignaler(self)
        self._keyframe_probe_signaler.ready.connect(self._on_keyframes_ready)
        self._hover_pool = QThreadPool.globalInstance()
        self._hover_label = QLabel()
        self._hover_label.setObjectName("VideoPreviewScrubHover")
        self._hover_label.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self._hover_label.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._hover_label.setFixedSize(_HOVER_PREVIEW_W + 8, _HOVER_PREVIEW_H + 8)
        self._hover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hover_label.hide()
        self._hover_token = 0
        self._keyframe_probe_token = 0
        self._pending_hover_frame: int | None = None
        self._hover_key_frames: list[int] = []
        self._hover_pixmap_cache: OrderedDict[int, QPixmap] = OrderedDict()
        self._hover_debounce = QTimer(self)
        self._hover_debounce.setSingleShot(True)
        self._hover_debounce.setInterval(_HOVER_FETCH_DEBOUNCE_MS)
        self._hover_debounce.timeout.connect(self._start_hover_fetch)

        self._status_log = ""
        self._backend: VideoPlayerBackend = create_video_player_backend(settings)
        self._backend.set_callbacks(
            on_position=self._on_backend_position,
            on_duration=self._on_backend_duration,
            on_ended=self._on_backend_ended,
            on_error=self._on_backend_error,
        )

        if isinstance(self._backend, ExternalPlayerBackend):
            self._backend.load(path)
            self._backend.play()
            self.close()
            return

        self._build_ui()
        self._bind_shortcuts()
        self.apply_profile(self._context)
        self._restore_workspace_from_settings()
        self._load_file(path)
        self._apply_dialog_geometry_once()
        QTimer.singleShot(0, self._refresh_title_elide)
        self.finished.connect(lambda _: self.closed.emit())

    def _build_ui(self) -> None:
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)
        self._apply_dialog_content_inset()

        body = QHBoxLayout()
        body.setSpacing(0)

        self._main_column = QWidget(self)
        self._main_column.setObjectName("VideoPreviewMainColumn")
        main_lay = QVBoxLayout(self._main_column)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        self._top_bar = QWidget(self._main_column)
        self._top_bar.setObjectName("VideoPreviewTopBar")
        self._top_bar.setFixedHeight(_PREVIEW_TOPBAR_H)
        top_lay = QHBoxLayout(self._top_bar)
        top_lay.setContentsMargins(
            _PREVIEW_CHROME_PAD_H,
            0,
            _PREVIEW_CHROME_PAD_H,
            0,
        )
        top_lay.setSpacing(8)
        self._file_counter = QLabel("", self._top_bar)
        self._file_counter.setObjectName("VideoPreviewFileCounter")
        self._file_counter.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._file_counter.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self._file_counter.setFixedWidth(52)
        top_lay.addWidget(self._file_counter, 0)
        self._title_btn = QPushButton("", self._top_bar)
        self._title_btn.setObjectName("VideoPreviewTitleButton")
        self._title_btn.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        self._title_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_btn.setFlat(True)
        self._title_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title_btn.clicked.connect(self._show_video_picker_menu)
        top_lay.addWidget(self._title_btn, 1)
        main_lay.addWidget(self._top_bar, 0)

        self._viewer = QWidget(self._main_column)
        self._viewer.setObjectName("VideoPreviewViewer")
        self._viewer.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        viewer_lay = QVBoxLayout(self._viewer)
        viewer_lay.setContentsMargins(0, 0, 0, 0)
        viewer_lay.setSpacing(0)

        self._surface_wrap = QWidget(self._viewer)
        self._surface_wrap.setObjectName("VideoPreviewSurfaceWrap")
        self._surface_wrap.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self._surface_wrap.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        wrap_lay = QVBoxLayout(self._surface_wrap)
        wrap_lay.setContentsMargins(0, 0, 0, _VIDEO_NATIVE_CLIP_BOTTOM)
        wrap_lay.setSpacing(0)
        self._surface = QWidget(self._surface_wrap)
        self._surface.setObjectName("VideoPreviewSurface")
        self._surface.setMinimumSize(480, 270)
        self._surface.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        wrap_lay.addWidget(self._surface, 1)
        self._surface.installEventFilter(self)
        self._surface_wrap.installEventFilter(self)
        self._surface.setToolTip(
            "Drag horizontally to scrub — wraps at selected range In/Out or video ends"
        )

        self._hud = QLabel("", self._viewer)
        self._hud.setObjectName("VideoPreviewHud")
        self._hud.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._hud.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud.mousePressEvent = lambda _e: self._copy_timecode()  # type: ignore[method-assign]

        self._tool_strip = ReviewToolStrip(self._viewer)
        self._tool_strip.hide()
        self._strip_toggle = QToolButton(self._viewer)
        self._strip_toggle.setObjectName("VideoReviewToolStripToggle")
        self._strip_toggle.setText("T")
        self._strip_toggle.setFont(monos_font("Inter", 11, QFont.Weight.Bold))
        self._strip_toggle.setToolTip("Tools strip (T)")
        self._strip_toggle.setFixedSize(28, 28)
        self._strip_toggle.clicked.connect(self._toggle_tool_strip)

        viewer_lay.addWidget(self._surface_wrap, 1)

        self._viewer_divider = QFrame(self._viewer)
        self._viewer_divider.setObjectName("VideoPreviewTierDivider")
        self._viewer_divider.setFrameShape(QFrame.Shape.NoFrame)
        self._viewer_divider.setFixedHeight(1)
        viewer_lay.addWidget(self._viewer_divider, 0)

        self._timeline_block = QWidget(self._viewer)
        self._timeline_block.setObjectName("VideoPreviewTimelineBlock")
        timeline_lay = QHBoxLayout(self._timeline_block)
        timeline_lay.setContentsMargins(0, 0, 0, 0)
        timeline_lay.setSpacing(0)

        self._scrubber = VideoPreviewScrubber(self._timeline_block)
        self._scrubber.setToolTip(
            "Timeline — right-click menu · Alt+wheel zoom · wheel pan when zoomed · "
            "middle-drag pan · Alt+F fit · F focus range"
        )
        self._scrubber.sliderPressed.connect(self._on_scrub_pressed)
        self._scrubber.seek_released.connect(lambda f: self._on_scrub_released(int(f)))
        self._scrubber.frame_preview.connect(self._on_scrub_frame_preview)
        self._scrubber.hover_frame.connect(self._on_scrub_hover_frame)
        self._scrubber.valueChanged.connect(self._on_scrub_value)
        self._scrubber.in_out_changed.connect(self._on_scrub_in_out)
        self._scrubber.range_handles_drag_started.connect(self._push_range_undo)
        self._scrubber.range_highlighted.connect(self._on_range_highlighted)
        self._scrubber.range_edit_requested.connect(self._on_range_edit_requested)
        self._scrubber.range_deselected.connect(self._on_range_deselected)
        self._scrubber.go_to_in_requested.connect(self._go_to_range_in)
        self._scrubber.go_to_out_requested.connect(self._go_to_range_out)
        self._scrubber.range_duplicate_requested.connect(self._duplicate_range)
        self._scrubber.range_delete_requested.connect(self._delete_range)
        self._scrubber.range_rename_requested.connect(self._on_range_label_changed)
        self._scrubber.focus_range_requested.connect(self._focus_range_by_id)
        self._scrubber.seek_to_frame.connect(self._seek_frame)
        self._scrubber.mark_in_at_frame.connect(self._mark_in_at_frame)
        self._scrubber.mark_out_at_frame.connect(self._mark_out_at_frame)
        self._scrubber.add_range_requested.connect(self._add_draft_range)
        self._scrubber.fit_timeline_requested.connect(self._scrubber.reset_view)

        self._timeline_zoom = QWidget(self._timeline_block)
        self._timeline_zoom.setObjectName("VideoPreviewTimelineZoom")
        self._timeline_zoom.setFixedWidth(44)
        zoom_lay = QVBoxLayout(self._timeline_zoom)
        zoom_lay.setContentsMargins(6, 6, 6, 6)
        zoom_lay.setSpacing(4)
        self._btn_tl_fit = self._tool_btn("maximize-2", "Fit timeline (Alt+F)")
        self._btn_tl_focus = self._tool_btn("scan", "Focus to selected range (F)")
        zoom_lay.addWidget(self._btn_tl_fit, 0, Qt.AlignmentFlag.AlignHCenter)
        zoom_lay.addWidget(self._btn_tl_focus, 0, Qt.AlignmentFlag.AlignHCenter)
        self._current_frame_label = QLabel("0000", self._timeline_zoom)
        self._current_frame_label.setObjectName("VideoPreviewCurrentFrame")
        self._current_frame_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._current_frame_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._current_frame_label.setToolTip("Current frame")
        zoom_lay.addWidget(self._current_frame_label, 0, Qt.AlignmentFlag.AlignHCenter)
        zoom_lay.addStretch(1)

        timeline_lay.addWidget(self._timeline_zoom, 0)
        timeline_lay.addWidget(self._scrubber, 1)
        self._btn_tl_fit.clicked.connect(self._scrubber.reset_view)
        self._btn_tl_focus.clicked.connect(self._focus_timeline_range)

        viewer_lay.addWidget(self._timeline_block, 0)
        main_lay.addWidget(self._viewer, 1)

        self._transport = QWidget(self._main_column)
        self._transport.setObjectName("VideoPreviewTransport")
        tlay = QHBoxLayout(self._transport)
        tlay.setContentsMargins(
            _PREVIEW_CHROME_PAD_H,
            _PREVIEW_CHROME_PAD_V,
            _PREVIEW_CHROME_PAD_H,
            _PREVIEW_CHROME_PAD_V,
        )
        tlay.setSpacing(8)

        self._btn_prev_file = self._tool_btn("skip-back", "Previous file")
        self._btn_prev_file.clicked.connect(self._prev_file)
        self._btn_play = self._tool_btn("play", "Play / pause (Space)")
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_next_file = self._tool_btn("skip-forward", "Next file")
        self._btn_next_file.clicked.connect(self._next_file)
        self._btn_tool_strip = self._tool_btn("sliders-horizontal", "Tools strip (T)")
        self._btn_tool_strip.setCheckable(True)
        self._btn_tool_strip.clicked.connect(self._toggle_tool_strip)
        tlay.addWidget(self._btn_prev_file)
        tlay.addWidget(self._btn_play)
        tlay.addWidget(self._btn_next_file)
        tlay.addWidget(self._btn_tool_strip)

        self._time_label = QLabel("00:00:00 / 00:00:00", self._transport)
        self._time_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        tlay.addWidget(self._time_label)

        self._chk_precise_scrub = QCheckBox("Exact scrub", self._transport)
        self._chk_precise_scrub.setObjectName("VideoPreviewPreciseScrubCheck")
        self._chk_precise_scrub.setToolTip(
            "Drag scrub to every frame (slower on heavy codecs). "
            "Off = snap to keyframes for speed."
        )
        self._chk_precise_scrub.setChecked(read_video_preview_precise_scrub_drag(self._settings))
        self._chk_precise_scrub.toggled.connect(self._on_precise_scrub_toggled)
        if self._chk_precise_scrub.isChecked():
            self._update_scrub_seek_interval()
        tlay.addWidget(self._chk_precise_scrub)

        self._chk_loop = QCheckBox("Loop", self._transport)
        self._chk_loop.setObjectName("VideoPreviewLoopCheck")
        self._chk_loop.setToolTip(
            "Loop playback (L) — full video, or highlighted range when selected"
        )
        self._chk_loop.toggled.connect(self._on_loop_toggled)
        tlay.addWidget(self._chk_loop)

        self._cmb_time_display = QComboBox(self._transport)
        self._cmb_time_display.setObjectName("VideoPreviewTimeDisplayCombo")
        self._cmb_time_display.addItem("Frames", TIME_DISPLAY_FRAME)
        self._cmb_time_display.addItem("Timecode", TIME_DISPLAY_TIMECODE)
        display_idx = (
            0 if self._time_display_mode == TIME_DISPLAY_FRAME else 1
        )
        self._cmb_time_display.setCurrentIndex(display_idx)
        self._cmb_time_display.setToolTip("Timeline and position display — Frames or Timecode")
        self._cmb_time_display.currentIndexChanged.connect(self._on_time_display_changed)
        tlay.addWidget(self._cmb_time_display)

        self._btn_in = self._action_btn("flag", "In", "Mark In (I)")
        self._btn_in.clicked.connect(self._mark_in)
        self._btn_out = self._action_btn("chevron-right", "Out", "Mark Out (O)")
        self._btn_out.clicked.connect(self._mark_out)
        self._btn_add = self._action_btn("plus", "+ Range", "Add range from In/Out (Enter)")
        self._btn_add.clicked.connect(self._add_draft_range)
        tlay.addWidget(self._btn_in)
        tlay.addWidget(self._btn_out)
        tlay.addWidget(self._btn_add)

        tlay.addStretch(1)

        self._speed_label = QLabel("1.0×", self._transport)
        self._speed_label.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        tlay.addWidget(self._speed_label)
        vol = QSlider(Qt.Orientation.Horizontal, self._transport)
        vol.setMaximum(100)
        vol.setValue(self._volume)
        vol.setFixedWidth(80)
        vol.valueChanged.connect(self._on_volume)
        tlay.addWidget(vol)

        self._btn_sync = self._action_btn(
            "sync",
            "Sync",
            "Sync ranges to project sidecar",
            primary=True,
        )
        self._btn_sync.clicked.connect(self._sync_ranges)
        self._btn_export = self._action_btn("download", "Export…", "Export marked ranges")
        self._btn_export.clicked.connect(self._export)
        self._btn_close = self._action_btn("x", "Close", "Close (Esc)")
        self._btn_close.clicked.connect(self.close)
        tlay.addWidget(self._btn_sync)
        tlay.addWidget(self._btn_export)
        tlay.addWidget(self._btn_close)

        main_lay.addWidget(self._transport, 0)

        self._footer = QWidget(self._main_column)
        self._footer.setObjectName("VideoPreviewFooter")
        footer_lay = QHBoxLayout(self._footer)
        footer_lay.setContentsMargins(
            _PREVIEW_CHROME_PAD_H,
            _PREVIEW_CHROME_PAD_V,
            _PREVIEW_CHROME_PAD_H,
            _PREVIEW_CHROME_PAD_V,
        )
        footer_lay.setSpacing(0)
        self._footer_label = QLabel("", self._footer)
        self._footer_label.setObjectName("VideoPreviewFooterLog")
        self._footer_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._footer_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._footer_label.setWordWrap(True)
        footer_lay.addWidget(self._footer_label, 1)
        main_lay.addWidget(self._footer, 0)

        body.addWidget(self._main_column, 1)

        self._tools_panel = ReviewToolsPanel(self)
        self._tools_panel.bind_strip(self._tool_strip)
        self._tools_panel.strip_visibility_changed.connect(self._on_tool_strip_visibility_changed)
        self._tools_panel.workspace_changed.connect(self._on_tools_workspace_changed)
        self._tools_panel.tool_mode_changed.connect(self._on_tools_mode_changed)
        self._tools_panel.range_selected.connect(self._on_range_selected)
        self._tools_panel.range_delete_requested.connect(self._delete_range)
        self._tools_panel.range_duplicate_requested.connect(self._duplicate_range)
        self._tools_panel.go_to_in_requested.connect(self._go_to_range_in)
        self._tools_panel.go_to_out_requested.connect(self._go_to_range_out)
        self._tools_panel.range_label_changed.connect(self._on_range_label_changed)
        self._tools_panel.open_all_notes_requested.connect(self.open_all_notes_requested.emit)
        body.addWidget(self._tools_panel, 0)

        self._root_layout.addLayout(body, 1)

        self._on_tool_strip_visibility_changed(self._tools_panel.strip_visible())

    def _video_overlay_rect(self) -> QRect:
        if not self._surface_wrap or not self._viewer:
            return QRect()
        top_left = self._surface_wrap.mapTo(self._viewer, QPoint(0, 0))
        return QRect(top_left, self._surface_wrap.size())

    def _sync_video_backend(self) -> None:
        if not self._video_attached:
            self._backend.attach_to_widget(self._surface)
            self._video_attached = True
        self._backend.layout_video()

    def _apply_dialog_content_inset(self) -> None:
        inset = 0 if self._fullscreen else _DIALOG_BORDER_INSET
        self._root_layout.setContentsMargins(inset, inset, inset, inset)

    def _update_rounded_mask(self) -> None:
        # Mask + native mpv HWND on Windows breaks hit-testing for the whole dialog.
        return

    def raise_border_overlay(self) -> None:
        """Sync border stripe geometry but keep it under Qt widgets (raising blocks clicks)."""
        if self._border_overlay is None:
            return
        self._border_overlay.setGeometry(self.rect())
        self._border_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._border_overlay.lower()
        if getattr(self, "_main_column", None) is not None:
            self._main_column.raise_()

    def _deferred_video_attach(self) -> None:
        if not self.isVisible():
            return
        self._sync_video_backend()
        self._position_hud()
        self._position_tool_strip()
        self._position_strip_toggle()
        self._raise_video_overlays()
        self.raise_border_overlay()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._surface_wrap and event.type() == QEvent.Type.Resize:
            self._sync_video_backend()
            self._position_hud()
            self._position_tool_strip()
            self._position_strip_toggle()
            self._raise_video_overlays()
        if watched is self._surface and self._info is not None:
            handled = self._filter_video_surface_mouse(event)
            if handled is not None:
                return handled
        return super().eventFilter(watched, event)

    def _filter_video_surface_mouse(self, event: QEvent) -> bool | None:
        et = event.type()
        if et == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                self._video_scrub_pending = True
                self._video_scrub_active = False
                self._video_scrub_start_x = int(event.position().x())
                self._video_scrub_origin_frame = self._current_frame()
            return False
        if et == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                return False
            if not self._video_scrub_pending and not self._video_scrub_active:
                return False
            dx = int(event.position().x()) - self._video_scrub_start_x
            if not self._video_scrub_active:
                if abs(dx) < _VIDEO_SCRUB_DRAG_THRESHOLD_PX:
                    return False
                self._video_scrub_active = True
                self._on_scrub_pressed()
                self._surface.setCursor(Qt.CursorShape.SizeHorCursor)
            frame = self._frame_from_video_scrub_dx(dx, self._surface.width())
            self._apply_video_scrub_frame(frame)
            return True
        if et == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            if self._video_scrub_active:
                dx = int(event.position().x()) - self._video_scrub_start_x
                frame = self._frame_from_video_scrub_dx(dx, self._surface.width())
                self._on_scrub_released(frame)
                self._surface.unsetCursor()
                self._video_scrub_active = False
                self._video_scrub_pending = False
                return True
            self._video_scrub_pending = False
            return False
        return None

    def _wrap_frame_in_bounds(self, frame: int) -> int:
        start, end = self._loop_bounds()
        span = end - start + 1
        if span <= 1:
            return max(0, min(end, int(frame)))
        offset = (int(frame) - start) % span
        return start + offset

    def _frame_from_video_scrub_dx(self, dx: int, width: int) -> int:
        start, end = self._loop_bounds()
        span = end - start + 1
        w = max(1, int(width))
        delta = int(round(dx / w * span))
        return self._wrap_frame_in_bounds(self._video_scrub_origin_frame + delta)

    def _apply_video_scrub_frame(self, frame: int) -> None:
        frame = self._wrap_frame_in_bounds(frame)
        self._scrubber.setValue(frame)
        self._on_scrub_frame_preview(frame)

    def _tool_btn(self, icon: str, tip: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setIcon(lucide_icon(icon, size=18, color_hex=MONOS_COLORS["text_label"]))
        btn.setIconSize(QSize(18, 18))
        btn.setToolTip(tip)
        btn.setAutoRaise(True)
        btn.setFixedSize(32, 32)
        return btn

    def _action_btn(
        self,
        icon: str,
        label: str,
        tip: str = "",
        *,
        primary: bool = False,
    ) -> QPushButton:
        btn = QPushButton(label, self._transport)
        btn.setObjectName("DialogPrimaryButton" if primary else "DialogSecondaryButton")
        icon_color = "#fafafa" if primary else MONOS_COLORS["text_label"]
        btn.setIcon(lucide_icon(icon, size=16, color_hex=icon_color))
        btn.setIconSize(QSize(16, 16))
        if tip:
            btn.setToolTip(tip)
        return btn

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self._mark_in)
        QShortcut(QKeySequence(Qt.Key.Key_O), self, self._mark_out)
        QShortcut(QKeySequence(Qt.Key.Key_L), self, self._toggle_loop_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, self._add_draft_range)
        QShortcut(QKeySequence(Qt.Key.Key_A), self, self._add_draft_range)
        QShortcut(QKeySequence(Qt.Key.Key_C), self, self._copy_timecode)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._frame_step(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._frame_step(1))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self._jump_sec(-1))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self._jump_sec(1))
        QShortcut(QKeySequence(Qt.Key.Key_BracketLeft), self, lambda: self._change_speed(-1))
        QShortcut(QKeySequence(Qt.Key.Key_BracketRight), self, lambda: self._change_speed(1))
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_file)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_file)
        sc_del = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        sc_del.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_del.activated.connect(self._delete_active_range)
        sc_bs = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        sc_bs.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_bs.activated.connect(self._delete_active_range)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, self._select_prev_range)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, self._select_next_range)
        QShortcut(QKeySequence("Ctrl+F"), self, self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._on_escape)
        QShortcut(QKeySequence(Qt.Key.Key_E), self, self._edit_highlighted_range)
        QShortcut(QKeySequence("Alt+F"), self, self._scrubber.reset_view)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self._focus_timeline_range)
        QShortcut(QKeySequence(Qt.Key.Key_Tab), self, self._cycle_workspace)
        sc_tools = QShortcut(QKeySequence(Qt.Key.Key_T), self)
        sc_tools.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_tools.activated.connect(self._toggle_tool_strip)
        sc_undo = QShortcut(QKeySequence.StandardKey.Undo, self)
        sc_undo.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_undo.activated.connect(self._undo_range_edit)
        sc_redo = QShortcut(QKeySequence.StandardKey.Redo, self)
        sc_redo.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_redo.activated.connect(self._redo_range_edit)
        sc_redo_y = QShortcut(QKeySequence("Ctrl+Y"), self)
        sc_redo_y.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_redo_y.activated.connect(self._redo_range_edit)
        QShortcut(QKeySequence(Qt.Key.Key_R), self, lambda: self._activate_tool(ReviewToolMode.ranges))
        QShortcut(QKeySequence(Qt.Key.Key_N), self, lambda: self._activate_tool(ReviewToolMode.note))

    def apply_profile(self, context: PreviewContext) -> None:
        self._context = context
        self._profile_key = context.value
        self._tools_panel.apply_context(context)
        show_sync = context != PreviewContext.inbox
        self._btn_sync.setVisible(show_sync)
        self._btn_sync.setEnabled(show_sync)
        if self._entity_path is not None:
            self._tools_panel.note_panel().set_entity(self._entity_path)

    def _restore_workspace_from_settings(self) -> None:
        ws_name = read_review_workspace(self._settings, profile=self._profile_key)
        strip_visible = False
        try:
            ws = ReviewWorkspace(ws_name)
            if ws == ReviewWorkspace.review:
                ws = ReviewWorkspace.focus
                strip_visible = True
        except ValueError:
            ws = ReviewWorkspace.tools
        self._tools_panel.set_workspace(ws)
        self._tools_panel.set_strip_visible(strip_visible)
        if ws == ReviewWorkspace.tools:
            mode_name = read_review_tool_mode(self._settings, profile=self._profile_key)
            try:
                mode = ReviewToolMode(mode_name)
            except ValueError:
                mode = ReviewToolMode.ranges
            self._tools_panel.activate_tool_mode(mode)

    def _on_tools_workspace_changed(self, ws_name: str) -> None:
        if self._settings is not None:
            write_review_workspace(self._settings, self._profile_key, ws_name)

    def _on_tools_mode_changed(self, mode_name: str) -> None:
        if self._settings is not None:
            write_review_tool_mode(self._settings, self._profile_key, mode_name)

    def _cycle_workspace(self) -> None:
        self._tools_panel.cycle_workspace()

    def _activate_tool(self, mode: ReviewToolMode) -> None:
        if mode == ReviewToolMode.note and self._context != PreviewContext.entity:
            return
        self._tools_panel.activate_tool_mode(mode)

    def _toggle_tool_strip(self) -> None:
        self._tools_panel.toggle_strip()

    def _on_tool_strip_visibility_changed(self, visible: bool) -> None:
        self._strip_toggle.setVisible(not visible)
        if hasattr(self, "_btn_tool_strip"):
            self._btn_tool_strip.setChecked(visible)
        if visible:
            self._position_tool_strip()
        self._position_strip_toggle()
        self._raise_video_overlays()

    def _range_list(self):
        return self._tools_panel.range_list_widget()

    def _active_range_label(self) -> str:
        rng = self._active_range()
        return (rng.label if rng else "") or ""

    def _update_top_bar(self) -> None:
        if self._path is None:
            self._file_counter.setText("")
            self._title_btn.setText("")
            self._title_btn.setToolTip("")
            return
        n = len(self._paths)
        self._file_counter.setText(f"{self._path_index + 1}/{n}" if n > 1 else "")
        self._file_counter.setVisible(n > 1)
        tip = str(self._path)
        if n > 1:
            tip += "\nClick to choose another video in this folder"
        self._title_btn.setToolTip(tip)
        self._refresh_title_elide()
        QTimer.singleShot(0, self._refresh_title_elide)

    def _title_button_available_width(self) -> int:
        btn_w = self._title_btn.width()
        if btn_w >= 80:
            return max(48, btn_w - 12)
        bar_w = self._top_bar.width() if self._top_bar is not None else 0
        if bar_w < 80:
            bar_w = self.width()
        counter_w = self._file_counter.width() if self._file_counter.isVisible() else 0
        lay = self._top_bar.layout()
        if lay is not None:
            m = lay.contentsMargins()
            bar_w -= m.left() + m.right() + lay.spacing()
        return max(48, bar_w - counter_w - 12)

    def _refresh_title_elide(self) -> None:
        if self._path is None:
            self._title_btn.setText("")
            return
        avail = self._title_button_available_width()
        text = self._title_btn.fontMetrics().elidedText(
            self._path.name,
            Qt.TextElideMode.ElideMiddle,
            avail,
        )
        self._title_btn.setText(text)

    def _update_footer(self) -> None:
        if self._path is None:
            self._footer_label.setText("")
            return
        if self._info is None:
            self._footer_label.setText(self._status_log or "Could not probe video")
            return
        dur = format_timecode(self._info.duration_sec, fps=self._info.fps)
        codec = self._info.video_codec.strip() or "—"
        audio = "audio" if self._info.has_audio else "no audio"
        backend = getattr(self._backend, "name", "") or "—"
        parts = [
            f"{self._info.width}×{self._info.height}",
            f"{self._info.fps:.3f} fps",
            dur,
            f"{self._info.frame_count} fr",
            codec,
            audio,
            backend,
        ]
        if self._status_log:
            parts.append(self._status_log)
        self._footer_label.setText(" · ".join(parts))

    def _show_video_picker_menu(self) -> None:
        if len(self._paths) <= 1:
            return
        menu = MonosMenu(self)
        for i, p in enumerate(self._paths):
            act = menu.addAction(p.name)
            act.setCheckable(True)
            act.setChecked(i == self._path_index)
            act.triggered.connect(lambda checked=False, idx=i: self._switch_to_video_index(idx))
        position_popup_near_anchor(menu, self._title_btn)
        menu.popup(menu.mapToGlobal(QPoint(0, 0)))

    def _switch_to_video_index(self, index: int) -> None:
        if index < 0 or index >= len(self._paths) or index == self._path_index:
            return
        self._path_index = index
        self._load_file(self._paths[index])

    def _text_editing_focused(self) -> bool:
        fw = self.focusWidget()
        return isinstance(fw, (QLineEdit, QTextEdit))

    def _capture_range_snapshot(self) -> _RangeEditSnapshot:
        return _RangeEditSnapshot(
            ranges=tuple(self._ranges),
            draft_in=self._draft_in,
            draft_out=self._draft_out,
            active_range_id=self._active_range_id,
            range_edit_unlocked=self._range_edit_unlocked,
        )

    def _push_range_undo(self) -> None:
        if self._applying_range_undo:
            return
        snap = self._capture_range_snapshot()
        if self._range_undo_stack and self._range_undo_stack[-1] == snap:
            return
        self._range_undo_stack.append(snap)
        if len(self._range_undo_stack) > _RANGE_UNDO_MAX:
            self._range_undo_stack.pop(0)
        self._range_redo_stack.clear()

    def _clear_range_undo_stacks(self) -> None:
        self._range_undo_stack.clear()
        self._range_redo_stack.clear()

    def _restore_range_snapshot(self, snap: _RangeEditSnapshot) -> None:
        self._applying_range_undo = True
        try:
            self._ranges = list(snap.ranges)
            self._draft_in = snap.draft_in
            self._draft_out = snap.draft_out
            self._active_range_id = snap.active_range_id
            self._range_edit_unlocked = snap.range_edit_unlocked
            self._sync_range_ui()
            self._persist_ranges_local()
            self._update_sync_button()
        finally:
            self._applying_range_undo = False

    def _undo_range_edit(self) -> None:
        if self._text_editing_focused() or not self._range_undo_stack:
            return
        self._range_redo_stack.append(self._capture_range_snapshot())
        self._restore_range_snapshot(self._range_undo_stack.pop())

    def _redo_range_edit(self) -> None:
        if self._text_editing_focused() or not self._range_redo_stack:
            return
        self._range_undo_stack.append(self._capture_range_snapshot())
        self._restore_range_snapshot(self._range_redo_stack.pop())

    def _on_range_label_changed(self, range_id: str, label: str) -> None:
        self._push_range_undo()
        updated: list[VideoFrameRange] = []
        for rng in self._ranges:
            if rng.id == range_id:
                updated.append(VideoFrameRange(rng.id, rng.in_frame, rng.out_frame, label.strip()[:80]))
            else:
                updated.append(rng)
        self._ranges = updated
        self._sync_range_ui()
        self._persist_ranges_local()
        self._update_sync_button()

    def _on_escape(self) -> None:
        if self._fullscreen:
            self._toggle_fullscreen()
            return
        if self._tools_panel.retreat_workspace_or_mode():
            if self._settings is not None:
                write_review_workspace(
                    self._settings,
                    self._profile_key,
                    self._tools_panel.workspace().value,
                )
            return
        self.close()

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self._fullscreen = False
            self.showNormal()
            self._apply_dialog_content_inset()
            self._top_bar.show()
            self._timeline_block.show()
            self._viewer_divider.show()
            self._transport.show()
            self._footer.show()
            self._tools_panel.show()
            self._restore_locked_size()
        else:
            self._fullscreen = True
            self._top_bar.hide()
            self._timeline_block.hide()
            self._viewer_divider.hide()
            self._transport.hide()
            self._footer.hide()
            self._tools_panel.hide()
            self._apply_dialog_content_inset()
            self.showFullScreen()
        self._position_hud()
        self._position_tool_strip()
        self._position_strip_toggle()
        self._raise_video_overlays()

    def _restore_locked_size(self) -> None:
        if self._locked_size is None:
            return
        bounds = main_window_bounds(self)
        w, h = self._locked_size.width(), self._locked_size.height()
        x = bounds.x() + max(0, (bounds.width() - w) // 2)
        y = bounds.y() + max(0, (bounds.height() - h) // 2)
        self.setGeometry(x, y, w, h)
        self.setFixedSize(self._locked_size)

    def _apply_dialog_geometry_once(self) -> None:
        if self._geometry_applied:
            return
        self._geometry_applied = True
        bounds = main_window_bounds(self)
        self._locked_size = apply_dialog_geometry(
            None,
            "",
            self,
            bounds=bounds,
            default_fraction=self._request.geometry_fraction,
            min_size=QSize(self.minimumWidth(), self.minimumHeight()),
            lock_size=True,
            margin=4,
        )

    def _prime_playback(self) -> None:
        if self._playback_primed:
            return
        self._playback_primed = True
        self._backend.prime_for_scrub()
        self._seek_frame(0)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_dialog_geometry_once()
        self._update_scrub_seek_interval()
        QTimer.singleShot(0, self._deferred_video_attach)
        QTimer.singleShot(0, self._prime_playback)
        self._refresh_title_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._video_attached:
            self._backend.layout_video()
        self._position_hud()
        self._position_tool_strip()
        self._position_strip_toggle()
        self._refresh_title_elide()
        self._raise_video_overlays()
        self.raise_border_overlay()

    def _position_hud(self) -> None:
        if not self._hud or not self._viewer:
            return
        self._hud.adjustSize()
        area = self._video_overlay_rect()
        m = 8
        clip = _VIDEO_NATIVE_CLIP_BOTTOM
        self._hud.move(
            area.left() + m,
            area.top() + max(m, area.height() - clip - self._hud.height() - m),
        )

    def _position_tool_strip(self) -> None:
        if not self._tool_strip.isVisible() or not self._viewer:
            return
        area = self._video_overlay_rect()
        m = 8
        self._tool_strip.adjustSize()
        x = area.right() - self._tool_strip.width() - m
        y = area.top() + m
        self._tool_strip.move(max(area.left() + m, x), y)

    def _position_strip_toggle(self) -> None:
        if not self._strip_toggle or not self._viewer:
            return
        if not self._strip_toggle.isVisible():
            return
        area = self._video_overlay_rect()
        m = 8
        x = area.right() - self._strip_toggle.width() - m
        y = area.top() + m
        self._strip_toggle.move(max(area.left() + m, x), y)

    def _raise_video_overlays(self) -> None:
        if self._hud:
            self._hud.raise_()
        if self._tool_strip and self._tool_strip.isVisible():
            self._tool_strip.raise_()
        if self._strip_toggle and self._strip_toggle.isVisible():
            self._strip_toggle.raise_()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._loop_timer.stop()
        self._scrub_seek_timer.stop()
        self._hover_debounce.stop()
        self._hide_hover_preview()
        self._persist_ranges_local()
        if self._settings is not None and not self._fullscreen:
            write_review_workspace(self._settings, self._profile_key, self._tools_panel.workspace().value)
            write_review_tool_mode(self._settings, self._profile_key, self._tools_panel.tool_mode().value)
            write_video_preview_precise_scrub_drag(self._settings, self._precise_scrub_drag())
            write_video_preview_time_display(self._settings, self._time_display_mode)
        self._backend.release()
        self._video_attached = False
        super().closeEvent(event)

    def release_player(self) -> None:
        """Stop playback and release handles (before file move)."""
        self._loop_timer.stop()
        self._backend.stop()
        self._backend.release()

    def current_path(self) -> Path | None:
        return self._path

    def _load_file(self, path: Path) -> None:
        if self._path and self._path != path:
            self._persist_ranges_local()
        self._path = path
        self._status_log = ""
        self._hover_pixmap_cache.clear()
        self._hover_key_frames = []
        self._info = probe_video(path)
        self._ranges = []
        self._published_ranges = []
        self._clear_range_undo_stacks()
        self._active_range_id = None
        self._range_edit_unlocked = False
        self._draft_in = None
        self._draft_out = None
        self._loop_playback = False
        self._chk_loop.setChecked(False)
        parent_hint = path.parent.name
        self.setWindowTitle(f"{path.name} · {parent_hint}")
        if self._info is None:
            self._hud.setText("Could not probe video")
            self._update_top_bar()
            self._update_footer()
            return
        self._range_list().set_fps(self._info.fps)
        self._scrubber.set_frame_count(self._info.frame_count)
        self._scrubber.clear_overlap_cycle()
        published, working, _from_local = load_video_ranges_for_preview(
            path,
            total_frames=self._info.frame_count,
        )
        self._published_ranges = published
        self._ranges = working
        self._playback_primed = False
        self._backend.load(path)
        self._backend.set_volume(self._volume)
        self._backend.set_speed(self._speed)
        if self.isVisible():
            QTimer.singleShot(0, self._prime_playback)
        self._sync_range_ui()
        self._update_top_bar()
        self._update_footer()
        self._update_time_label(0.0)
        self._start_keyframe_probe()

    def _start_keyframe_probe(self) -> None:
        if self._path is None or self._info is None:
            return
        self._hover_key_frames = fallback_scrub_snap_frames(
            self._info.fps,
            self._info.frame_count,
        )
        self._keyframe_probe_token += 1
        token = self._keyframe_probe_token
        self._hover_pool.start(
            _KeyframeProbeRunnable(
                self._path,
                self._info.fps,
                self._info.frame_count,
                token,
                self._keyframe_probe_signaler,
            )
        )

    def _on_keyframes_ready(self, token: int, path: object, frames: object) -> None:
        if token != self._keyframe_probe_token or path != self._path:
            return
        if isinstance(frames, list) and frames:
            self._hover_key_frames = sorted({max(0, int(f)) for f in frames})

    def _hover_snap_frame(self, frame: int) -> int:
        if self._hover_key_frames:
            return snap_frame_to_nearest_keyframe(frame, self._hover_key_frames)
        if self._info is None:
            return max(0, int(frame))
        step = max(1, int(round(self._fps())))
        return max(0, (int(frame) // step) * step)

    def _fps(self) -> float:
        return self._info.fps if self._info else 24.0

    def _total_frames(self) -> int:
        return self._info.frame_count if self._info else 1

    def _current_frame(self) -> int:
        return sec_to_frame(self._backend.position(), self._fps())

    def _on_time_display_changed(self, _index: int) -> None:
        mode = self._cmb_time_display.currentData()
        if not isinstance(mode, str) or mode == self._time_display_mode:
            return
        self._time_display_mode = mode  # type: ignore[assignment]
        self._scrubber.set_time_display_mode(self._time_display_mode)
        panel = self._range_list()
        panel.set_display_mode(self._time_display_mode)
        panel.refresh_display()
        frame = self._current_frame()
        self._update_hud(frame)
        self._update_time_label(self._backend.position())
        self._update_note_frame_hint(frame)
        if self._settings is not None:
            write_video_preview_time_display(self._settings, self._time_display_mode)

    def _sync_range_ui(self) -> None:
        panel = self._range_list()
        panel.set_published_ranges(self._published_ranges)
        panel.set_display_mode(self._time_display_mode)
        panel.set_ranges(self._ranges, active_id=self._active_range_id)
        panel.set_draft_hint(self._draft_in, self._draft_out)
        self._tools_panel.set_active_range_id(
            self._active_range_id if self._range_edit_unlocked else None,
            label=self._active_range_label() if self._range_edit_unlocked else "",
        )
        self._scrubber.set_fps(self._fps())
        self._scrubber.set_time_display_mode(self._time_display_mode)
        edit_id = self._active_range_id if self._range_edit_unlocked else None
        self._scrubber.set_ranges(
            self._ranges,
            highlight_id=self._active_range_id,
            edit_id=edit_id,
        )
        self._scrubber.set_draft(self._draft_in, self._draft_out)
        has_selection = self._active_range_id is not None
        can_edit = self._range_edit_unlocked and has_selection
        in_tip = "Set In on range (I)" if can_edit else "Mark draft In for new range (I)"
        out_tip = "Set Out on range (O)" if can_edit else "Mark draft Out for new range (O)"
        self._btn_in.setToolTip(in_tip)
        self._btn_out.setToolTip(out_tip)
        self._btn_export.setEnabled(len(self._ranges) > 0)
        self._btn_tl_focus.setEnabled(self._active_range_id is not None)
        self._update_sync_button()

    def _update_note_frame_hint(self, frame: int | None = None) -> None:
        f = self._current_frame() if frame is None else int(frame)
        hint = format_position_display(f, self._fps(), mode=self._time_display_mode)
        self._tools_panel.note_panel().set_frame_hint(hint)

    def _update_sync_button(self) -> None:
        dirty = not ranges_content_equal(self._ranges, self._published_ranges)
        self._btn_sync.setEnabled(dirty)
        if dirty:
            self._btn_sync.setToolTip("Sync local range changes to project sidecar")
        else:
            self._btn_sync.setToolTip("All ranges synced with project")

    def _update_hud(self, frame: int) -> None:
        fps = self._fps()
        pos = format_position_display(frame, fps, mode=self._time_display_mode)
        self._hud.setText(f"{pos} · {fps:.3f}fps  ⎘")
        self._current_frame_label.setText(format_frame_label(frame))
        self._position_hud()

    def _update_time_label(self, pos_sec: float) -> None:
        fps = self._fps()
        max_frame = max(0, self._total_frames() - 1)
        if self._time_display_mode == "frame":
            cur = format_position_display(sec_to_frame(pos_sec, fps), fps, mode="frame")
            end = format_frame_label(max_frame)
            self._time_label.setText(f"{cur} / {end}")
            return
        dur = self._backend.duration() or (self._info.duration_sec if self._info else 0.0)
        self._time_label.setText(
            f"{format_timecode(pos_sec, fps=fps)} / {format_timecode(dur, fps=fps)}"
        )

    def _on_backend_position(self, sec: float) -> None:
        if self._scrubbing:
            return
        frame = sec_to_frame(sec, self._fps())
        self._scrubber.set_position_frame(frame)
        self._update_hud(frame)
        self._update_note_frame_hint(frame)
        self._update_time_label(sec)

    def _on_backend_duration(self, sec: float) -> None:
        if self._info and sec > 0:
            self._info = VideoInfo(
                path=self._info.path,
                duration_sec=sec,
                fps=self._info.fps,
                width=self._info.width,
                height=self._info.height,
                frame_count=max(1, sec_to_frame(sec, self._info.fps)),
                video_codec=self._info.video_codec,
                has_audio=self._info.has_audio,
            )
            self._scrubber.set_frame_count(self._info.frame_count)

    def _on_backend_ended(self) -> None:
        if self._loop_playback:
            self._seek_loop_start()
            self._backend.play()
        else:
            self._set_play_icon(False)

    def _on_backend_error(self, msg: str) -> None:
        logger.warning("video player: %s", msg)
        self._status_log = msg.strip()[:240]
        self._hud.setText(msg[:120])
        self._update_footer()

    def _set_play_icon(self, playing: bool) -> None:
        icon = "pause" if playing else "play"
        self._btn_play.setIcon(lucide_icon(icon, size=18, color_hex=MONOS_COLORS["text_label"]))

    def _toggle_play(self) -> None:
        if self._backend.is_playing():
            self._backend.pause()
            self._loop_timer.stop()
            self._set_play_icon(False)
        else:
            self._seek_frame(self._scrubber.value())
            self._backend.play()
            if self._loop_playback:
                self._loop_timer.start()
            self._set_play_icon(True)

    def _on_volume(self, v: int) -> None:
        self._volume = v
        self._backend.set_volume(v)

    def _on_scrub_value(self, frame: int) -> None:
        if not self._scrubbing:
            return
        self._update_hud(frame)

    def _persist_ranges_local(self) -> None:
        if self._path is None:
            return
        try:
            save_video_ranges_local_draft(self._path, self._ranges)
        except Exception:
            pass

    def _sync_ranges(self) -> None:
        if self._path is None:
            return
        try:
            save_video_ranges_sidecar(self._path, self._ranges)
            save_video_ranges_local_draft(self._path, self._ranges)
        except Exception:
            logger.warning("sync video ranges failed for %s", self._path, exc_info=True)
            return
        self._published_ranges = list(self._ranges)
        self._sync_range_ui()

    def _precise_scrub_drag(self) -> bool:
        return self._chk_precise_scrub.isChecked()

    def _thumb_lookup_frame(self, frame: int, *, for_drag: bool = False) -> int:
        if for_drag and self._precise_scrub_drag():
            return max(0, int(frame))
        return self._hover_snap_frame(frame)

    def _on_precise_scrub_toggled(self, checked: bool) -> None:
        if self._settings is not None:
            write_video_preview_precise_scrub_drag(self._settings, checked)
        self._update_scrub_seek_interval()

    def _update_scrub_seek_interval(self) -> None:
        mpv = getattr(self._backend, "name", "") == "mpv"
        if self._precise_scrub_drag():
            interval = _SCRUB_SEEK_INTERVAL_PRECISE_MPV_MS if mpv else _SCRUB_SEEK_INTERVAL_PRECISE_MS
        else:
            interval = _SCRUB_SEEK_INTERVAL_KEYFRAME_MPV_MS if mpv else _SCRUB_SEEK_INTERVAL_KEYFRAME_MS
        self._scrub_seek_timer.setInterval(interval)

    def _on_scrub_pressed(self) -> None:
        self._scrubbing = True
        self._last_scrub_video_frame = None
        self._was_playing_before_scrub = self._backend.is_playing()
        if self._was_playing_before_scrub:
            self._backend.pause()

    def _on_scrub_frame_preview(self, frame: int) -> None:
        if not self._scrubbing:
            return
        self._pending_scrub_frame = frame
        self._update_hud(frame)
        self._show_scrub_thumb(frame)
        precise = self._precise_scrub_drag()
        if not self._scrub_seek_timer.isActive():
            self._apply_scrub_preview(frame, precise=precise)
            self._scrub_seek_timer.start()

    def _flush_scrub_preview(self) -> None:
        if self._pending_scrub_frame is not None and self._scrubbing:
            self._apply_scrub_preview(
                self._pending_scrub_frame,
                precise=self._precise_scrub_drag(),
            )

    def _apply_scrub_preview(self, frame: int, *, precise: bool = False) -> None:
        seek_frame = int(frame) if precise else self._hover_snap_frame(frame)
        if not precise and seek_frame == self._last_scrub_video_frame:
            return
        if not precise:
            self._last_scrub_video_frame = seek_frame
        elif seek_frame == self._last_scrub_video_frame:
            return
        else:
            self._last_scrub_video_frame = seek_frame
        sec = seek_frame / max(1e-6, self._fps())
        self._backend.seek(sec, precise=precise)

    def _on_scrub_released(self, frame: int | None = None) -> None:
        self._scrubbing = False
        self._scrub_seek_timer.stop()
        self._pending_scrub_frame = None
        self._last_scrub_video_frame = None
        f = int(frame) if frame is not None else self._scrubber.value()
        sec = f / max(1e-6, self._fps())
        self._backend.seek(sec, precise=True)
        self._scrubber.set_position_frame(f)
        self._update_hud(f)
        self._hide_hover_preview()
        if self._was_playing_before_scrub:
            self._backend.play()
        self._was_playing_before_scrub = False

    def _on_scrub_in_out(self, in_f: int, out_f: int) -> None:
        if not self._range_edit_unlocked or self._active_range_id is None:
            return
        updated: list[VideoFrameRange] = []
        for rng in self._ranges:
            if rng.id == self._active_range_id:
                updated.append(VideoFrameRange(rng.id, in_f, out_f, rng.label))
            else:
                updated.append(rng)
        self._ranges = updated
        self._sync_range_ui()
        self._persist_ranges_local()

    def _mark_in(self) -> None:
        self._mark_in_at_frame(self._current_frame())

    def _mark_out(self) -> None:
        self._mark_out_at_frame(self._current_frame())

    def _mark_in_at_frame(self, frame: int) -> None:
        self._push_range_undo()
        frame = int(frame)
        if self._active_range_id is not None and self._range_edit_unlocked:
            self._set_active_range_in_out(in_frame=frame)
            return
        self._draft_in = frame
        self._sync_range_ui()

    def _mark_out_at_frame(self, frame: int) -> None:
        self._push_range_undo()
        frame = int(frame)
        if self._active_range_id is not None and self._range_edit_unlocked:
            self._set_active_range_in_out(out_frame=frame)
            return
        self._draft_out = frame
        self._sync_range_ui()

    def _set_active_range_in_out(
        self,
        *,
        in_frame: int | None = None,
        out_frame: int | None = None,
    ) -> None:
        if self._active_range_id is None:
            return
        rng = self._active_range()
        if rng is None:
            return
        in_f = rng.in_frame if in_frame is None else int(in_frame)
        out_f = rng.out_frame if out_frame is None else int(out_frame)
        if in_f > out_f:
            if in_frame is not None:
                out_f = in_f
            else:
                in_f = out_f
        if not validate_range(in_f, out_f, total_frames=self._total_frames()):
            return
        updated: list[VideoFrameRange] = []
        for item in self._ranges:
            if item.id == self._active_range_id:
                updated.append(VideoFrameRange(item.id, in_f, out_f, item.label))
            else:
                updated.append(item)
        self._ranges = updated
        self._sync_range_ui()
        self._persist_ranges_local()

    def _add_draft_range(self) -> None:
        if self._draft_in is None or self._draft_out is None:
            return
        self._push_range_undo()
        in_f, out_f = self._draft_in, self._draft_out
        if in_f > out_f:
            in_f, out_f = out_f, in_f
        if not validate_range(in_f, out_f, total_frames=self._total_frames()):
            return
        rid = new_range_id()
        label = self._pending_range_label.strip()[:80] if self._pending_range_label else ""
        self._ranges.append(VideoFrameRange(rid, in_f, out_f, label))
        self._active_range_id = rid
        self._range_edit_unlocked = True
        self._draft_in = None
        self._draft_out = None
        self._pending_range_label = ""
        self._sync_range_ui()
        self._persist_ranges_local()
        self._seek_frame(in_f)
        if label:
            self._tools_panel.activate_tool_mode(ReviewToolMode.ranges)
        else:
            self._tools_panel.set_active_range_id(rid, label="")
            self._tools_panel.focus_range_name_field()

    def _focus_timeline_range(self) -> None:
        rng = self._active_range()
        if rng is None:
            return
        self._scrubber.focus_frame_range(rng.in_frame, rng.out_frame)

    def _focus_range_by_id(self, range_id: str) -> None:
        rng = next((r for r in self._ranges if r.id == range_id), None)
        if rng is None:
            return
        self._active_range_id = range_id
        self._sync_range_ui()
        self._scrubber.focus_frame_range(rng.in_frame, rng.out_frame)

    def _edit_highlighted_range(self) -> None:
        if self._active_range_id:
            self._on_range_edit_requested(self._active_range_id)

    def _on_range_selected(self, range_id: str) -> None:
        self._scrubber.clear_overlap_cycle()
        self._active_range_id = range_id
        self._range_edit_unlocked = False
        rng = next((r for r in self._ranges if r.id == range_id), None)
        self._sync_range_ui()
        if rng is not None:
            self._seek_frame(rng.in_frame)

    def _on_range_highlighted(self, range_id: str) -> None:
        self._active_range_id = range_id
        self._range_edit_unlocked = False
        rng = next((r for r in self._ranges if r.id == range_id), None)
        self._sync_range_ui()
        if rng is not None:
            self._seek_frame(rng.in_frame)

    def _on_range_edit_requested(self, range_id: str) -> None:
        self._active_range_id = range_id
        self._range_edit_unlocked = True
        self._sync_range_ui()
        self._tools_panel.activate_tool_mode(ReviewToolMode.ranges)
        self._tools_panel.set_workspace(ReviewWorkspace.tools)

    def _on_range_deselected(self) -> None:
        if self._active_range_id is None and not self._range_edit_unlocked:
            return
        self._active_range_id = None
        self._range_edit_unlocked = False
        self._sync_range_ui()

    def _delete_range(self, range_id: str) -> None:
        self._push_range_undo()
        self._ranges = [r for r in self._ranges if r.id != range_id]
        if self._active_range_id == range_id:
            self._active_range_id = None
            self._range_edit_unlocked = False
        self._sync_range_ui()
        self._persist_ranges_local()

    def _duplicate_range(self, range_id: str) -> None:
        rng = next((r for r in self._ranges if r.id == range_id), None)
        if rng is None:
            return
        self._push_range_undo()
        rid = new_range_id()
        self._ranges.append(VideoFrameRange(rid, rng.in_frame, rng.out_frame, rng.label))
        self._active_range_id = rid
        self._range_edit_unlocked = False
        self._sync_range_ui()
        self._persist_ranges_local()

    def _go_to_range_in(self, range_id: str) -> None:
        self._on_range_selected(range_id)

    def _go_to_range_out(self, range_id: str) -> None:
        self._active_range_id = range_id
        self._range_edit_unlocked = False
        rng = next((r for r in self._ranges if r.id == range_id), None)
        self._sync_range_ui()
        if rng is not None:
            self._seek_frame(rng.out_frame)

    def _show_scrub_thumb(self, frame: int) -> None:
        self._pending_hover_frame = frame
        self._position_hover_preview(frame)
        lookup = self._thumb_lookup_frame(frame, for_drag=True)
        cached = self._hover_cache_get(lookup)
        if cached is not None:
            self._hover_label.setPixmap(cached)
            self._hover_label.show()
            return
        self._hover_label.show()
        self._hover_debounce.stop()
        self._hover_debounce.start()

    def _hover_cache_get(self, frame: int) -> QPixmap | None:
        pix = self._hover_pixmap_cache.get(frame)
        if pix is not None:
            self._hover_pixmap_cache.move_to_end(frame)
        return pix

    def _hover_cache_put(self, frame: int, pix: QPixmap) -> None:
        self._hover_pixmap_cache[frame] = pix
        self._hover_pixmap_cache.move_to_end(frame)
        while len(self._hover_pixmap_cache) > _HOVER_CACHE_MAX:
            self._hover_pixmap_cache.popitem(last=False)

    def _pixmap_from_hover_png(self, png_bytes: bytes) -> QPixmap | None:
        pix = QPixmap()
        if not pix.loadFromData(png_bytes):
            return None
        return pix.scaled(
            _HOVER_PREVIEW_W,
            _HOVER_PREVIEW_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _on_scrub_hover_frame(self, frame: int) -> None:
        if self._scrubbing:
            return
        if frame < 0:
            self._hide_hover_preview()
            return
        self._pending_hover_frame = frame
        self._position_hover_preview(frame)
        snap = self._hover_snap_frame(frame)
        cached = self._hover_cache_get(snap)
        if cached is not None:
            self._hover_label.setPixmap(cached)
            self._hover_label.show()
            self._hover_debounce.stop()
            return
        self._hover_label.show()
        self._hover_debounce.stop()
        self._hover_debounce.start()

    def _start_hover_fetch(self) -> None:
        frame = self._pending_hover_frame
        if frame is None or self._path is None or self._info is None:
            return
        for_drag = self._scrubbing
        lookup = self._thumb_lookup_frame(frame, for_drag=for_drag)
        cached = self._hover_cache_get(lookup)
        if cached is not None:
            if frame == self._pending_hover_frame:
                self._hover_label.setPixmap(cached)
                self._position_hover_preview(frame)
                self._hover_label.show()
            return
        self._hover_token += 1
        token = self._hover_token
        sec = lookup / max(1e-6, self._fps())
        keyframe_aligned = not (for_drag and self._precise_scrub_drag())
        self._hover_pool.start(
            _HoverFrameRunnable(
                self._path,
                sec,
                lookup,
                token,
                self._hover_signaler,
                keyframe_aligned=keyframe_aligned,
            )
        )

    def _on_hover_frame_ready(self, token: int, frame: int, png_bytes: object) -> None:
        if token != self._hover_token:
            return
        if not png_bytes:
            return
        scaled = self._pixmap_from_hover_png(bytes(png_bytes))
        if scaled is None or scaled.isNull():
            return
        self._hover_cache_put(frame, scaled)
        pending = self._pending_hover_frame
        if pending is None:
            return
        if frame != self._thumb_lookup_frame(pending, for_drag=self._scrubbing):
            return
        self._hover_label.setPixmap(scaled)
        self._position_hover_preview(pending)
        self._hover_label.show()

    def _scrubber_x_for_frame(self, frame: int) -> int:
        return self._scrubber.x_for_frame(frame)

    def _position_hover_preview(self, frame: int) -> None:
        gx = self._scrubber_x_for_frame(frame)
        scrub_global = QRect(self._scrubber.mapToGlobal(QPoint(0, 0)), self._scrubber.size())
        anchor = QRect(scrub_global.left() + gx - 1, scrub_global.top(), 2, scrub_global.height())
        position_popup_above_rect(
            self._hover_label,
            anchor,
            gap=8,
            bounds=main_window_bounds(self),
        )

    def _hide_hover_preview(self) -> None:
        self._hover_debounce.stop()
        self._pending_hover_frame = None
        self._hover_token += 1
        self._hover_label.hide()

    def _delete_active_range(self) -> None:
        if self._text_editing_focused():
            return
        if self._active_range_id:
            self._delete_range(self._active_range_id)

    def _select_prev_range(self) -> None:
        if not self._ranges:
            return
        ids = [r.id for r in self._ranges]
        if self._active_range_id not in ids:
            self._on_range_selected(ids[0])
            return
        idx = max(0, ids.index(self._active_range_id) - 1)
        self._on_range_selected(ids[idx])

    def _select_next_range(self) -> None:
        if not self._ranges:
            return
        ids = [r.id for r in self._ranges]
        if self._active_range_id not in ids:
            self._on_range_selected(ids[0])
            return
        idx = min(len(ids) - 1, ids.index(self._active_range_id) + 1)
        self._on_range_selected(ids[idx])

    def _active_range(self) -> VideoFrameRange | None:
        if not self._active_range_id:
            return None
        return next((r for r in self._ranges if r.id == self._active_range_id), None)

    def _seek_frame(self, frame: int) -> None:
        sec = frame / max(1e-6, self._fps())
        self._backend.seek(sec, precise=True)
        self._scrubber.set_position_frame(frame)
        self._update_hud(frame)

    def _loop_bounds(self) -> tuple[int, int]:
        rng = self._active_range()
        if rng is not None:
            lo, hi = sorted((rng.in_frame, rng.out_frame))
            return lo, hi
        return 0, max(0, self._total_frames() - 1)

    def _seek_loop_start(self) -> None:
        start, _ = self._loop_bounds()
        self._seek_frame(start)

    def _check_loop(self) -> None:
        if not self._loop_playback:
            return
        start, end = self._loop_bounds()
        if self._current_frame() > end:
            self._seek_frame(start)
            if not self._backend.is_playing():
                self._backend.play()

    def _on_loop_toggled(self, checked: bool) -> None:
        self._loop_playback = checked
        if checked:
            self._seek_loop_start()
            self._loop_timer.start()
        else:
            self._loop_timer.stop()

    def _toggle_loop_shortcut(self) -> None:
        self._chk_loop.setChecked(not self._chk_loop.isChecked())

    def _frame_step(self, direction: int) -> None:
        cur = self._scrubber.value()
        nxt = max(0, min(self._total_frames() - 1, cur + direction))
        if nxt == cur:
            return
        self._seek_frame(nxt)

    def _jump_sec(self, delta: int) -> None:
        self._backend.seek(max(0.0, self._backend.position() + float(delta)))

    def _change_speed(self, direction: int) -> None:
        self._speed = next_speed(self._speed, direction=direction)
        self._backend.set_speed(self._speed)
        self._speed_label.setText(f"{self._speed:.2g}×")

    def _copy_timecode(self) -> None:
        frame = self._current_frame()
        text = format_position_display(frame, self._fps(), mode=self._time_display_mode)
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(text)

    def _prev_file(self) -> None:
        if len(self._paths) <= 1:
            return
        self._path_index = (self._path_index - 1) % len(self._paths)
        self._load_file(self._paths[self._path_index])

    def _next_file(self) -> None:
        if len(self._paths) <= 1:
            return
        self._path_index = (self._path_index + 1) % len(self._paths)
        self._load_file(self._paths[self._path_index])

    def _export(self) -> None:
        if not self._ranges or self._info is None:
            return
        dlg = VideoExportDialog(
            self._path,
            self._ranges,
            fps=self._info.fps,
            default_output_dir=self._path.parent / f"{self._path.stem}_cuts",
            settings=self._settings,
            parent=self,
        )
        if dlg.exec() == VideoExportDialog.DialogCode.Accepted:
            outs = dlg.outputs()
            if outs:
                self._hud.setText(f"Exported {len(outs)} file(s)")
                self.export_completed.emit(outs)
