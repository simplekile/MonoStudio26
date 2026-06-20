"""Video preview dialog — playback, multi-range review, export."""

from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QEvent, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication, QKeySequence, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QColor, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.note_time_anchors import parse_time_href
from monostudio.core.review_draw import (
    ReviewDrawLayer,
    ReviewDrawLayerKeyframe,
    ReviewDrawStroke,
    apply_eraser_to_strokes,
    delete_layer_from_document,
    delete_keyframe_on_layer,
    display_keyframe_on_layer,
    draw_visible_at,
    ensure_keyframe_on_layer,
    ensure_layer_in_document,
    hold_frames_for_keyframe,
    keyframe_at_exact_on_layer,
    layers_content_equal,
    load_draw_layers_for_preview,
    make_draw_layer,
    move_keyframe_on_layer,
    onion_has_neighbors,
    save_draw_local_draft,
    save_sequence_draw_sidecar,
    save_video_draw_sidecar,
    set_keyframe_hold,
    set_layer_default_hold,
)
from monostudio.core.review_media import EntityReviewSource, list_entity_review_sources
from monostudio.core.video_proxy import (
    format_heavy_proxy_message,
    is_heavy_source_for_proxy,
)
from monostudio.core.video_proxy_cache import (
    ProxyManifest,
    clear_proxy_cache_for_source,
    is_full_proxy_ready,
    list_cached_range_spans,
    lookup_full_proxy,
    lookup_range_proxy,
)
from monostudio.core.video_media import (
    VideoFrameRange,
    VideoInfo,
    VideoReviewMarker,
    export_sequence_markers_png,
    export_sequence_ranges,
    export_video_markers_png,
    extract_video_frame_png_bytes,
    fallback_scrub_snap_frames,
    format_frame_label,
    format_position_display,
    format_timecode,
    TimeDisplayMode,
    list_video_siblings,
    load_sequence_markers_for_preview,
    load_sequence_preview_session_local_draft,
    load_sequence_ranges_for_preview,
    load_video_markers_for_preview,
    load_video_preview_session_local_draft,
    load_video_ranges_for_preview,
    markers_content_equal,
    new_marker_id,
    new_range_id,
    probe_video,
    probe_video_keyframe_frames,
    ranges_content_equal,
    save_sequence_markers_local_draft,
    save_sequence_markers_sidecar,
    save_sequence_preview_session_local_draft,
    save_sequence_ranges_local_draft,
    save_sequence_ranges_sidecar,
    save_video_markers_local_draft,
    save_video_markers_sidecar,
    save_video_preview_session_local_draft,
    save_video_ranges_local_draft,
    save_video_ranges_sidecar,
    sec_to_frame,
    snap_frame_to_nearest_keyframe,
    validate_marker_frame,
    validate_range,
)
from monostudio.ui_qt.delete_confirm_dialog import ask_delete
from monostudio.ui_qt.inspector_preview_settings import write_sequence_preview_fps
from monostudio.ui_qt.review_draw_overlay import ReviewDrawOverlay
from monostudio.ui_qt.review_onion_layer import ReviewOnionLayer
from monostudio.ui_qt.video_review_draw_panel import VideoReviewDrawTransportActions
from monostudio.ui_qt.video_review_draw_brush_strip import VideoReviewDrawBrushStrip
from monostudio.ui_qt.video_review_draw_quick_popup import VideoReviewDrawQuickPopup
from monostudio.ui_qt.video_review_note_rail import (
    NOTE_RAIL_DEFAULT_W,
    NOTE_RAIL_MAX_W,
    NOTE_RAIL_MIN_W,
    VideoReviewNoteRail,
)
from monostudio.ui_qt.review_note_timeline import build_timeline_note_markers, parse_note_anchor_frame
from monostudio.ui_qt.review_playback_backend import SequenceDecodeBackend
from monostudio.ui_qt.dialog_geometry import (
    apply_dialog_geometry,
    clamp_dialog_to_bounds,
    main_window_bounds,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import position_popup_above_rect, position_popup_near_anchor, position_popup_near_global_point
from monostudio.ui_qt.review_tools_panel import (
    ReviewToolMode,
    ReviewToolsPanel,
    ReviewWorkspace,
    TOOLS_PANEL_DEFAULT_W,
    TOOLS_PANEL_MAX_W,
    TOOLS_PANEL_MIN_W,
)
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, MonosMenu, monos_font
from monostudio.ui_qt.video_export_dialog import VideoExportDialog
from monostudio.ui_qt.video_preview_footer_hint import VideoPreviewFooterHintBar
from monostudio.ui_qt.video_proxy_build_worker import ProxyBuildRunnable, ProxyBuildSignaler
from monostudio.ui_qt.video_proxy_heavy_dialog import ask_build_full_proxy
from monostudio.ui_qt.video_player_backend import (
    ExternalPlayerBackend,
    PLAYBACK_SPEED_STEPS,
    VideoPlayerBackend,
    create_sequence_placeholder_backend,
    create_video_player_backend,
)
from monostudio.ui_qt.video_preview_context import (
    PreviewContext,
    ReviewMediaKind,
    ReviewOpenRequest,
    VideoPreviewOpenRequest,
)
from monostudio.ui_qt.video_preview_scrubber import VideoPreviewScrubber
from monostudio.ui_qt.video_preview_settings import (
    PROXY_SCALE_STEPS,
    read_review_note_rail_open,
    read_review_note_rail_width,
    read_review_tool_mode,
    read_review_tools_panel_width,
    read_review_workspace,
    read_video_preview_loop,
    read_video_preview_playback_speed,
    read_video_preview_precise_scrub_drag,
    read_video_preview_time_display,
    read_video_preview_volume,
    read_review_video_draw_overlay_fix,
    write_review_note_rail_open,
    write_review_note_rail_width,
    write_review_tool_mode,
    write_review_tools_panel_width,
    write_review_workspace,
    write_video_preview_loop,
    write_video_preview_playback_speed,
    write_video_preview_precise_scrub_drag,
    write_video_preview_time_display,
    write_video_preview_volume,
    TIME_DISPLAY_FRAME,
    TIME_DISPLAY_TIMECODE,
    PROXY_SCALE_FULL,
)

logger = logging.getLogger(__name__)

_HOVER_PREVIEW_W = 160
_HOVER_PREVIEW_H = 90
_HOVER_ENCODE_W = 128
_HOVER_FETCH_DEBOUNCE_MS = 30
_SCRUB_SEEK_INTERVAL_KEYFRAME_MS = 66
_SCRUB_SEEK_INTERVAL_KEYFRAME_MPV_MS = 50
_SCRUB_SEEK_INTERVAL_PRECISE_MS = 120
_SCRUB_SEEK_INTERVAL_PRECISE_MPV_MS = 90
_VIDEO_SCRUB_DRAG_THRESHOLD_PX = 6
_VIEWER_PLATE_ZOOM_MIN = 0.1
_VIEWER_PLATE_ZOOM_FIT = 1.0
_VIEWER_PLATE_ZOOM_MAX = 4.0
_VIEWER_PLATE_ZOOM_WHEEL_FACTOR = 1.12
_VIEWER_WHEEL_COALESCE_MS = 24
_PREVIEW_CHROME_PAD_H = 12
_PREVIEW_CHROME_PAD_V = 8
_RANGE_UNDO_MAX = 50
_PROXY_SCALE_LABELS = {1.0: "1", 0.5: "½", 0.25: "¼", 0.125: "⅛"}


@dataclass(frozen=True)
class _RangeEditSnapshot:
    ranges: tuple[VideoFrameRange, ...]
    draft_in: int | None
    draft_out: int | None
    active_range_id: str | None
    range_edit_unlocked: bool
_PREVIEW_TOPBAR_H = 40
_PREVIEW_CLOSE_BTN = 28
_PREVIEW_CLOSE_INSET = 8
_FULLSCREEN_EDGE_PX = 48
_FULLSCREEN_CHROME_HIDE_MS = 700
_DIALOG_BORDER_INSET = 1  # top-only root inset; sides/bottom flush under raised border
_VIDEO_NATIVE_CLIP_BOTTOM = 1  # mpv / QVideoWidget HWND bleed above timeline divider
_PREVIEW_TIMELINE_H = 64  # ~80% of prior 80px chrome — scrubber fills block height
_SHELL_RADIUS = 12  # match MonosDialog outer corner radius
_FOOTER_CHROME = "#1e2124"
_SHELL_LINE = QColor(255, 255, 255, 15)


def _hide_native_qt_window(widget: QWidget | None) -> None:
    """Hide a QWidget's native HWND — orphaned embed hosts block clicks on Windows."""
    if widget is None:
        return
    try:
        widget.hide()
    except Exception:
        pass
    if sys.platform != "win32":
        return
    try:
        wid = int(widget.winId())
        if wid:
            import ctypes

            ctypes.windll.user32.ShowWindow(wid, 0)
    except Exception:
        pass


def _bottom_shell_path(
    width: float,
    height: float,
    radius: float,
    *,
    bottom_left: bool,
    bottom_right: bool,
    bleed_left: float = 0.0,
    bleed_bottom: float = 0.0,
    bleed_right: float = 0.0,
) -> QPainterPath:
    if width <= 0 or height <= 0:
        return QPainterPath()
    rect = QRectF(-bleed_left, 0.0, width + bleed_left + bleed_right, height + bleed_bottom)
    r = min(max(0.0, radius), rect.width() / 2, rect.height() / 2)
    path = QPainterPath()
    if r <= 0 or (not bottom_left and not bottom_right):
        path.addRect(rect)
        return path
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
    path.moveTo(left, top)
    path.lineTo(right, top)
    if bottom_right:
        path.lineTo(right, bottom - r)
        path.arcTo(right - 2 * r, bottom - 2 * r, 2 * r, 2 * r, 0, -90)
    else:
        path.lineTo(right, bottom)
    if bottom_left:
        path.lineTo(left + r, bottom)
        path.arcTo(left, bottom - 2 * r, 2 * r, 2 * r, 270, -90)
    else:
        path.lineTo(left, bottom)
    path.lineTo(left, top)
    path.closeSubpath()
    return path


class _VideoPreviewFooterBar(QFrame):
    """Footer chrome with painted bottom corner(s) — QSS radius does not clip child stack."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewFooter")
        self._wide_bottom = False

    def set_wide_bottom(self, wide: bool) -> None:
        wide = bool(wide)
        if self._wide_bottom == wide:
            return
        self._wide_bottom = wide
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        path = _bottom_shell_path(
            w,
            h,
            _SHELL_RADIUS,
            bottom_left=True,
            bottom_right=self._wide_bottom,
            bleed_left=1.0,
            bleed_bottom=1.0,
            bleed_right=1.0 if self._wide_bottom else 0.0,
        )
        painter.fillPath(path, QColor(_FOOTER_CHROME))
        painter.setPen(QPen(_SHELL_LINE, 1))
        painter.drawLine(0, 0, int(w), 0)
        super().paintEvent(event)


class _HoverFrameSignaler(QObject):
    ready = Signal(int, int, object)  # token, frame, bytes | None


class _KeyframeProbeSignaler(QObject):
    ready = Signal(int, object, object)  # token, path, list[int]


class _SequenceListSignaler(QObject):
    ready = Signal(int, object)  # token, list[Path]


class _SequenceListRunnable(QRunnable):
    def __init__(
        self,
        token: int,
        folder: Path,
        signaler: _SequenceListSignaler,
        *,
        precached_frames: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._token = token
        self._folder = folder
        self._signaler = signaler
        self._precached_frames = precached_frames

    def run(self) -> None:
        if self._precached_frames is not None:
            frames = list(self._precached_frames)
        else:
            from monostudio.core.sequence_preview import list_sequence_frames

            try:
                frames = list_sequence_frames(self._folder)
            except Exception:
                frames = []
        self._signaler.ready.emit(self._token, frames)


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


class _SequenceHoverRunnable(QRunnable):
    def __init__(
        self,
        path: Path,
        frame: int,
        token: int,
        signaler: _HoverFrameSignaler,
        *,
        max_side: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._frame = frame
        self._token = token
        self._signaler = signaler
        self._max_side = max_side

    def run(self) -> None:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

        img = load_preview_frame_qimage(self._path, self._max_side)
        if img is None or img.isNull():
            self._signaler.ready.emit(self._token, self._frame, None)
            return
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not img.save(buf, "PNG"):
            self._signaler.ready.emit(self._token, self._frame, None)
            return
        self._signaler.ready.emit(self._token, self._frame, bytes(ba))


class _OnionPlateSignaler(QObject):
    ready = Signal(int, object, object)  # token, prev QPixmap | None, next QPixmap | None


class _OnionPlatesRunnable(QRunnable):
    def __init__(
        self,
        *,
        token: int,
        prev_frame: int,
        next_frame: int,
        current_frame: int,
        signaler: _OnionPlateSignaler,
        sequence_frames: list[Path] | None = None,
        video_path: Path | None = None,
        fps: float = 24.0,
        max_side: int = 960,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._token = token
        self._prev_frame = prev_frame
        self._next_frame = next_frame
        self._current_frame = current_frame
        self._signaler = signaler
        self._sequence_frames = sequence_frames
        self._video_path = video_path
        self._fps = fps
        self._max_side = max_side

    def _decode_sequence_pix(self, frame: int) -> QPixmap | None:
        frames = self._sequence_frames
        if not frames:
            return None
        idx = max(0, min(len(frames) - 1, int(frame)))
        from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

        img = load_preview_frame_qimage(frames[idx], self._max_side)
        if img is None or img.isNull():
            return None
        pix = QPixmap.fromImage(img)
        return pix if not pix.isNull() else None

    def _decode_video_pix(self, frame: int) -> QPixmap | None:
        path = self._video_path
        if path is None:
            return None
        sec = frame / max(1e-6, self._fps)
        data = extract_video_frame_png_bytes(
            path,
            sec,
            width=min(1280, self._max_side),
            keyframe_aligned=True,
        )
        if not data:
            return None
        pix = QPixmap()
        if not pix.loadFromData(data):
            return None
        return pix

    def run(self) -> None:
        prev_pix = None
        next_pix = None
        if self._sequence_frames is not None:
            if self._prev_frame != self._current_frame:
                prev_pix = self._decode_sequence_pix(self._prev_frame)
            if self._next_frame != self._current_frame:
                next_pix = self._decode_sequence_pix(self._next_frame)
        elif self._video_path is not None:
            if self._prev_frame != self._current_frame:
                prev_pix = self._decode_video_pix(self._prev_frame)
            if self._next_frame != self._current_frame:
                next_pix = self._decode_video_pix(self._next_frame)
        self._signaler.ready.emit(self._token, prev_pix, next_pix)


class VideoPreviewDialog(MonosDialog):
    """Non-modal video player with multi-range list and FFmpeg export."""

    closed = Signal()
    export_completed = Signal(object)  # list[Path]
    open_all_notes_requested = Signal()
    notes_changed = Signal()

    def __init__(
        self,
        path: Path | None = None,
        *,
        request: ReviewOpenRequest | VideoPreviewOpenRequest | None = None,
        sibling_paths: list[Path] | None = None,
        settings=None,
        parent=None,
        geometry_anchor: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewDialog")
        self.set_host_dim_overlay_enabled(False)
        self.set_dialog_border_overlay_enabled(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self._geometry_anchor = geometry_anchor or parent
        self._closing = False
        if request is None:
            if path is None:
                raise ValueError("VideoPreviewDialog requires path or request")
            review_req = ReviewOpenRequest(
                media_kind=ReviewMediaKind.video,
                path=path,
                context=PreviewContext.entity,
                sibling_paths=sibling_paths,
            )
        elif isinstance(request, VideoPreviewOpenRequest):
            review_req = request.to_review_request()
            if sibling_paths is not None:
                review_req = ReviewOpenRequest(
                    media_kind=review_req.media_kind,
                    context=review_req.context,
                    path=review_req.path,
                    sibling_paths=sibling_paths,
                    frames=review_req.frames,
                    sequence_folder=review_req.sequence_folder,
                    fps=review_req.fps,
                    entity_path=review_req.entity_path,
                    department_id=review_req.department_id,
                )
        else:
            review_req = request
        self._review_request = review_req
        self._media_kind = review_req.media_kind
        self._context = review_req.context
        self._profile_key = review_req.settings_profile_key
        self._entity_path = review_req.entity_path
        self._department_id = review_req.department_id
        self._department_label = review_req.department_label
        self._work_path = review_req.work_path
        self._work_file_path = review_req.work_file_path
        self._source_label = review_req.source_label
        self._entity_sources: list = []
        self._settings = settings
        self._geometry_applied = False
        self._locked_size: QSize | None = None
        self.setMinimumSize(1280, 720)
        self._sequence_frames: list[Path] = []
        self._sequence_folder: Path | None = None
        self._seq_backend: SequenceDecodeBackend | None = None
        self._seq_label_parented = False
        if self._media_kind == ReviewMediaKind.video:
            vid_path = review_req.path
            assert vid_path is not None
            self._paths = list(sibling_paths or review_req.sibling_paths or list_video_siblings(vid_path))
            if vid_path not in self._paths:
                self._paths = [vid_path] + self._paths
            self._path_index = max(0, self._paths.index(vid_path))
            self._path: Path | None = vid_path
        else:
            self._paths = []
            self._path_index = 0
            self._path = None
            self._sequence_frames = list(review_req.frames or ())
            self._sequence_folder = review_req.sequence_folder
        self._info: VideoInfo | None = None
        self._ranges: list[VideoFrameRange] = []
        self._published_ranges: list[VideoFrameRange] = []
        self._markers: list[VideoReviewMarker] = []
        self._published_markers: list[VideoReviewMarker] = []
        self._draw_layers: list[ReviewDrawLayer] = []
        self._published_draw_layers: list[ReviewDrawLayer] = []
        self._active_keyframe_frame: int | None = None
        self._active_layer_id: str | None = None
        self._draw_keyframe_edit_unlocked = False
        self._onion_enabled = False
        self._onion_span = 2
        self._scrubber_display_force_ranges = False
        self._scrubber_display_force_markers = False
        self._scrubber_display_force_draw_keys = False
        self._active_range_id: str | None = None
        self._active_marker_id: str | None = None
        self._range_edit_unlocked = False
        self._range_edit_cancel_snapshot: _RangeEditSnapshot | None = None
        self._draft_in: int | None = None
        self._draft_out: int | None = None
        self._range_undo_stack: list[_RangeEditSnapshot] = []
        self._range_redo_stack: list[_RangeEditSnapshot] = []
        self._applying_range_undo = False
        self._loop_playback = read_video_preview_loop(settings)
        self._proxy_enabled = False
        self._proxy_scale = PROXY_SCALE_FULL
        self._proxy_mode: Literal["off", "full", "range"] = "off"
        self._full_proxy_ready = False
        self._full_proxy_manifest: ProxyManifest | None = None
        self._range_proxy_manifest: ProxyManifest | None = None
        self._cached_spans: list[tuple[int, int]] = []
        self._playback_path: Path | None = None
        self._proxy_build_token = 0
        self._proxy_build_active_token = 0
        self._proxy_cancel_flag: list[bool] = [False]
        self._proxy_heavy_ack_key: str | None = None
        self._proxy_build_signaler = ProxyBuildSignaler(self)
        self._proxy_build_signaler.progress.connect(self._on_proxy_build_progress)
        self._proxy_build_signaler.finished.connect(self._on_proxy_build_finished)
        self._speed = read_video_preview_playback_speed(settings)
        self._volume = read_video_preview_volume(settings)
        self._volume_muted = False
        self._volume_before_mute = self._volume if self._volume > 0 else 80
        self._scrubbing = False
        self._was_playing_before_scrub = False
        self._playback_primed = False
        self._frame_paint_gen = 0
        self._video_attached = False
        self._video_scrub_pending = False
        self._video_scrub_active = False
        self._video_scrub_start_x = 0
        self._video_scrub_origin_frame = 0
        self._viewer_plate_zoom = _VIEWER_PLATE_ZOOM_FIT
        self._viewer_plate_pan = QPointF(0.0, 0.0)
        self._video_pan_active = False
        self._video_pan_press = QPointF()
        self._video_pan_origin = QPointF()
        self._viewer_last_host_size: QSize | None = None
        self._viewer_wheel_log_accum = 0.0
        self._viewer_wheel_coalesce = QTimer(self)
        self._viewer_wheel_coalesce.setSingleShot(True)
        self._viewer_wheel_coalesce.setInterval(_VIEWER_WHEEL_COALESCE_MS)
        self._viewer_wheel_coalesce.timeout.connect(self._apply_pending_viewer_wheel_zoom)
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
        self._hover_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._hover_label.setFixedSize(_HOVER_PREVIEW_W + 8, _HOVER_PREVIEW_H + 8)
        self._hover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hover_label.hide()
        self._hover_token = 0
        self._keyframe_probe_token = 0
        self._pending_hover_frame: int | None = None
        self._hover_key_frames: list[int] = []
        self._hover_debounce = QTimer(self)
        self._hover_debounce.setSingleShot(True)
        self._hover_debounce.setInterval(_HOVER_FETCH_DEBOUNCE_MS)
        self._hover_debounce.timeout.connect(self._start_hover_fetch)

        self._onion_refresh_timer = QTimer(self)
        self._onion_refresh_timer.setSingleShot(True)
        self._onion_refresh_timer.setInterval(40)
        self._onion_refresh_timer.timeout.connect(self._refresh_draw_onion)
        self._onion_plate_token = 0
        self._onion_plate_signaler = _OnionPlateSignaler(self)
        self._onion_plate_signaler.ready.connect(self._on_onion_plates_ready)

        self._sequence_load_token = 0
        self._draw_keyframe_select_guard = False
        self._in_programmatic_seek = False
        self._last_playhead_ui_frame: int | None = None
        self._sequence_list_signaler = _SequenceListSignaler(self)
        self._sequence_list_signaler.ready.connect(
            self._on_sequence_frames_listed,
            Qt.ConnectionType.QueuedConnection,
        )

        self._restore_frame: int | None = None
        self._pending_time_anchor: str | None = None
        self._session_persist_timer = QTimer(self)
        self._session_persist_timer.setSingleShot(True)
        self._session_persist_timer.setInterval(400)
        self._session_persist_timer.timeout.connect(self._persist_preview_session)
        self._fs_bottom_revealed = False
        self._fs_right_revealed = False
        self._fs_app_filter_installed = False
        self._fs_chrome_hide_timer = QTimer(self)
        self._fs_chrome_hide_timer.setSingleShot(True)
        self._fs_chrome_hide_timer.timeout.connect(self._on_fullscreen_chrome_hide_timeout)
        self._status_log = ""
        self._footer_pointer_zone = ""
        self._app_event_filter_installed = False
        if self._media_kind == ReviewMediaKind.sequence:
            self._backend = create_sequence_placeholder_backend()
        else:
            self._backend = create_video_player_backend(settings)
        if self._media_kind == ReviewMediaKind.video:
            self._backend.set_callbacks(
                on_position=self._on_backend_position,
                on_duration=self._on_backend_duration,
                on_ended=self._on_backend_ended,
                on_error=self._on_backend_error,
            )

        if isinstance(self._backend, ExternalPlayerBackend) and self._media_kind == ReviewMediaKind.video:
            assert self._path is not None
            self._backend.load(self._path)
            self._backend.play()
            self.close()
            return

        self._build_ui()
        self._bind_shortcuts()
        self.apply_profile(self._context)
        self._restore_workspace_from_settings()
        if self._media_kind == ReviewMediaKind.sequence:
            QTimer.singleShot(0, self._deferred_initial_sequence_load)
        else:
            self.load_media(self._review_request)
        self._apply_dialog_geometry_once()
        QTimer.singleShot(0, self._refresh_title_elide)
        self.finished.connect(lambda _: self.closed.emit())

    def _ensure_video_backend(self) -> None:
        if getattr(self._backend, "name", "") != "noop":
            return
        placeholder = self._backend
        self._backend = create_video_player_backend(self._settings)
        self._backend.set_callbacks(
            on_position=self._on_backend_position,
            on_duration=self._on_backend_duration,
            on_ended=self._on_backend_ended,
            on_error=self._on_backend_error,
        )
        placeholder.release()

    def _deferred_initial_sequence_load(self) -> None:
        if self._review_request is not None:
            self.load_media(self._review_request)

    def _build_ui(self) -> None:
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)
        self._apply_dialog_content_inset()

        self._top_bar = QWidget(self)
        self._top_bar.setObjectName("VideoPreviewTopBar")
        self._top_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._top_bar.setFixedHeight(_PREVIEW_TOPBAR_H)
        top_lay = QHBoxLayout(self._top_bar)
        top_lay.setContentsMargins(
            _PREVIEW_CHROME_PAD_H,
            0,
            _PREVIEW_CHROME_PAD_H + _PREVIEW_CLOSE_BTN + _PREVIEW_CLOSE_INSET,
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
        self._title_btn.clicked.connect(self._show_title_picker_menu)
        top_lay.addWidget(self._title_btn, 1)

        self._btn_close = QToolButton(self)
        self._btn_close.setObjectName("VideoPreviewDialogCloseBtn")
        self._btn_close.setAutoRaise(False)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setFixedSize(_PREVIEW_CLOSE_BTN, _PREVIEW_CLOSE_BTN)
        self._btn_close.setIcon(lucide_icon("x", size=16, color_hex="#fafafa"))
        self._btn_close.setToolTip("Close")
        self._btn_close.clicked.connect(self.close)

        body = QSplitter(Qt.Orientation.Horizontal, self)
        body.setObjectName("VideoPreviewBodySplit")
        body.setChildrenCollapsible(False)
        body.setHandleWidth(6)
        self._body_splitter = body
        self._note_rail_saved_w = read_review_note_rail_width(
            self._settings,
            profile=self._profile_key,
            default=NOTE_RAIL_DEFAULT_W,
            min_w=NOTE_RAIL_MIN_W,
            max_w=NOTE_RAIL_MAX_W,
        )
        self._tools_panel_saved_w = read_review_tools_panel_width(
            self._settings,
            profile=self._profile_key,
            default=TOOLS_PANEL_DEFAULT_W,
            min_w=TOOLS_PANEL_MIN_W,
            max_w=TOOLS_PANEL_MAX_W,
        )
        self._note_rail_open_pref = read_review_note_rail_open(
            self._settings,
            profile=self._profile_key,
        )

        self._note_rail = VideoReviewNoteRail(self)
        self._note_rail.apply_context(self._context)
        body.addWidget(self._note_rail)

        self._main_column = QWidget(self)
        self._main_column.setObjectName("VideoPreviewMainColumn")
        main_lay = QVBoxLayout(self._main_column)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

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
        self._surface.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self._surface.setMinimumSize(1, 1)
        if not self._is_sequence_mode():
            self._surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._surface.setMouseTracking(True)
        self._surface_wrap.setMouseTracking(True)
        self._surface_wrap.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._surface.installEventFilter(self)
        self._surface_wrap.installEventFilter(self)
        self._surface.setToolTip(
            "Wheel — Zoom · MMB drag — Scrub · Alt+MMB — Pan"
        )

        self._onion_layer = ReviewOnionLayer(self._surface_wrap)
        self._onion_layer.hide()

        self._draw_overlay = ReviewDrawOverlay(self._surface_wrap)
        self._draw_overlay.stroke_committed.connect(self._on_draw_stroke_committed)
        self._draw_overlay.installEventFilter(self)
        self._draw_overlay.hide()
        self._draw_quick_popup: VideoReviewDrawQuickPopup | None = None

        self._hud = QLabel("", self._viewer)
        self._hud.setObjectName("VideoPreviewHud")
        self._hud.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._hud.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud.mousePressEvent = lambda _e: self._copy_timecode()  # type: ignore[method-assign]

        self._draw_brush_strip = VideoReviewDrawBrushStrip(self._viewer)
        self._draw_brush_strip.hide()

        self._proxy_build_overlay = QWidget(self._viewer)
        self._proxy_build_overlay.setObjectName("VideoPreviewProxyBuildOverlay")
        self._proxy_build_overlay.hide()
        proxy_overlay_lay = QVBoxLayout(self._proxy_build_overlay)
        proxy_overlay_lay.setContentsMargins(16, 12, 16, 12)
        proxy_overlay_lay.setSpacing(8)
        self._proxy_build_label = QLabel("Building proxy…", self._proxy_build_overlay)
        self._proxy_build_label.setObjectName("DialogBody")
        self._proxy_progress = QProgressBar(self._proxy_build_overlay)
        self._proxy_progress.setObjectName("VideoPreviewProxyProgress")
        self._proxy_progress.setRange(0, 1000)
        self._proxy_progress.setValue(0)
        self._proxy_build_cancel_btn = QPushButton("Cancel", self._proxy_build_overlay)
        self._proxy_build_cancel_btn.clicked.connect(self._cancel_proxy_build)
        proxy_overlay_lay.addWidget(self._proxy_build_label)
        proxy_overlay_lay.addWidget(self._proxy_progress)
        proxy_overlay_lay.addWidget(self._proxy_build_cancel_btn, 0, Qt.AlignmentFlag.AlignRight)

        self._sequence_loading_overlay = QWidget(self._viewer)
        self._sequence_loading_overlay.setObjectName("VideoPreviewSequenceLoadingOverlay")
        self._sequence_loading_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._sequence_loading_overlay.hide()
        seq_load_lay = QVBoxLayout(self._sequence_loading_overlay)
        seq_load_lay.setContentsMargins(16, 12, 16, 12)
        self._sequence_loading_label = QLabel("Loading sequence…", self._sequence_loading_overlay)
        self._sequence_loading_label.setObjectName("DialogBody")
        self._sequence_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seq_load_lay.addStretch(1)
        seq_load_lay.addWidget(self._sequence_loading_label)
        seq_load_lay.addStretch(1)

        viewer_lay.addWidget(self._surface_wrap, 1)

        self._viewer_divider = QFrame(self._viewer)
        self._viewer_divider.setObjectName("VideoPreviewTierDivider")
        self._viewer_divider.setFrameShape(QFrame.Shape.NoFrame)
        self._viewer_divider.setFixedHeight(1)
        self._viewer_divider.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        viewer_lay.addWidget(self._viewer_divider, 0)

        self._timeline_block = QWidget(self._viewer)
        self._timeline_block.setObjectName("VideoPreviewTimelineBlock")
        self._timeline_block.setFixedHeight(_PREVIEW_TIMELINE_H)
        self._timeline_block.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        timeline_lay = QHBoxLayout(self._timeline_block)
        timeline_lay.setContentsMargins(0, 0, 0, 0)
        timeline_lay.setSpacing(0)

        self._scrubber = VideoPreviewScrubber(self._timeline_block)
        self._scrubber.setMinimumHeight(_PREVIEW_TIMELINE_H)
        self._scrubber.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self._scrubber.sliderPressed.connect(self._on_scrub_pressed)
        self._scrubber.seek_released.connect(lambda f: self._on_scrub_released(int(f)))
        self._scrubber.frame_preview.connect(self._on_scrub_frame_preview)
        self._scrubber.hover_frame.connect(self._on_scrub_hover_frame)
        self._scrubber.footer_context_changed.connect(self._on_scrubber_footer_context)
        self._scrubber.valueChanged.connect(self._on_scrub_value)
        self._scrubber.in_out_changed.connect(self._on_scrub_in_out)
        self._scrubber.range_handles_drag_started.connect(self._push_range_undo)
        self._scrubber.range_highlighted.connect(self._on_range_highlighted)
        self._scrubber.note_marker_clicked.connect(self._on_timeline_note_clicked)
        self._scrubber.range_edit_requested.connect(self._on_range_edit_requested)
        self._scrubber.range_deselected.connect(self._on_range_deselected)
        self._scrubber.marker_highlighted.connect(self._on_marker_highlighted)
        self._scrubber.marker_deselected.connect(self._on_marker_deselected)
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
        self._scrubber.draw_keyframe_highlighted.connect(
            lambda frame, layer_id: self._on_draw_keyframe_selected(layer_id, int(frame))
        )
        self._scrubber.draw_keyframe_move_requested.connect(self._on_draw_keyframe_move_requested)
        self._scrubber.timeline_display_force_toggled.connect(self._on_scrubber_display_force_toggled)

        self._timeline_zoom = QWidget(self._timeline_block)
        self._timeline_zoom.setObjectName("VideoPreviewTimelineZoom")
        self._timeline_zoom.setFixedWidth(36)
        self._timeline_zoom.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        zoom_lay = QVBoxLayout(self._timeline_zoom)
        zoom_lay.setContentsMargins(4, 2, 4, 2)
        zoom_lay.setSpacing(2)
        self._btn_tl_menu = self._tool_btn("more-horizontal", "Timeline menu", compact=True)
        zoom_lay.addWidget(self._btn_tl_menu, 0, Qt.AlignmentFlag.AlignHCenter)
        zoom_lay.addStretch(1)

        timeline_lay.addWidget(self._timeline_zoom, 0)
        timeline_lay.addWidget(self._scrubber, 1)
        self._btn_tl_menu.clicked.connect(self._show_timeline_menu)

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

        self._transport_controls = QWidget(self._transport)
        self._transport_controls.setObjectName("VideoPreviewTransportControls")
        transport_btn_lay = QHBoxLayout(self._transport_controls)
        transport_btn_lay.setContentsMargins(0, 0, 0, 0)
        transport_btn_lay.setSpacing(2)
        self._btn_prev_file = self._tool_btn("skip-back", "Previous file", compact=True)
        self._btn_prev_file.clicked.connect(self._prev_file)
        self._btn_play = self._tool_btn("play", "Play / pause (Space)", compact=True)
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_next_file = self._tool_btn("skip-forward", "Next file", compact=True)
        self._btn_next_file.clicked.connect(self._next_file)
        self._btn_tools_panel = self._tool_btn("sliders-horizontal", "Tools panel (T)", compact=True)
        self._btn_tools_panel.setCheckable(True)
        self._btn_tools_panel.clicked.connect(self._toggle_tools_workspace)
        transport_btn_lay.addWidget(self._btn_prev_file)
        transport_btn_lay.addWidget(self._btn_play)
        transport_btn_lay.addWidget(self._btn_next_file)
        transport_btn_lay.addWidget(self._btn_tools_panel)
        tlay.addWidget(self._transport_controls)

        self._position_box = QWidget(self._transport)
        self._position_box.setObjectName("VideoPreviewPositionBox")
        self._position_box.setFixedWidth(136)
        self._position_box.setToolTip("Current position — type frame and press Enter")
        pos_lay = QHBoxLayout(self._position_box)
        pos_lay.setContentsMargins(8, 2, 8, 2)
        pos_lay.setSpacing(0)
        self._frame_input = QLineEdit(self._position_box)
        self._frame_input.setObjectName("VideoPreviewFrameInput")
        self._frame_input.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._frame_input.setFixedWidth(52)
        self._frame_input.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._frame_input.setPlaceholderText("0")
        self._frame_input.returnPressed.connect(self._on_frame_input_commit)
        self._frame_input.editingFinished.connect(self._on_frame_input_commit)
        self._position_suffix = QLabel(" / 0000", self._position_box)
        self._position_suffix.setObjectName("VideoPreviewPositionSuffix")
        self._position_suffix.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._timecode_position = QLabel("00:00:00 / 00:00:00", self._position_box)
        self._timecode_position.setObjectName("VideoPreviewTimecodePosition")
        self._timecode_position.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._timecode_position.setFixedWidth(120)
        pos_lay.addWidget(self._frame_input)
        pos_lay.addWidget(self._position_suffix)
        pos_lay.addWidget(self._timecode_position)
        tlay.addWidget(self._position_box)
        self._sync_position_controls_visibility()

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
        self._chk_loop.setChecked(self._loop_playback)
        self._chk_loop.toggled.connect(self._on_loop_toggled)
        tlay.addWidget(self._chk_loop)

        self._fps_label = QLabel("FPS", self._transport)
        self._fps_label.setObjectName("VideoPreviewSequenceFpsLabel")
        self._fps_label.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        self._fps_label.setStyleSheet(f"color: {MONOS_COLORS.get('text_label', '#a1a1aa')};")
        self._fps_spin = QSpinBox(self._transport)
        self._fps_spin.setObjectName("SequencePreviewFpsSpin")
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setValue(24)
        self._fps_spin.setFixedWidth(56)
        self._fps_spin.setToolTip("Playback frame rate for image sequence")
        self._fps_spin.valueChanged.connect(self._on_sequence_fps_changed)
        tlay.addWidget(self._fps_label)
        tlay.addWidget(self._fps_spin)
        self._fps_label.hide()
        self._fps_spin.hide()

        self._chk_proxy = QCheckBox("Proxy", self._transport)
        self._chk_proxy.setObjectName("VideoPreviewProxyCheck")
        self._chk_proxy.setToolTip(
            "Scrub and playback use H.264 proxy when cached. "
            "No range: full timeline. With range: cached segment (source outside span). "
            "Export always uses original."
        )
        self._chk_proxy.setChecked(self._proxy_enabled)
        self._chk_proxy.toggled.connect(self._on_proxy_toggled)
        tlay.addWidget(self._chk_proxy)

        self._cmb_proxy_scale = QComboBox(self._transport)
        self._cmb_proxy_scale.setObjectName("VideoPreviewProxyScaleCombo")
        self._cmb_proxy_scale.setToolTip("Proxy resolution scale")
        for scale in PROXY_SCALE_STEPS:
            self._cmb_proxy_scale.addItem(_PROXY_SCALE_LABELS.get(scale, str(scale)), scale)
        scale_idx = (
            PROXY_SCALE_STEPS.index(self._proxy_scale)
            if self._proxy_scale in PROXY_SCALE_STEPS
            else PROXY_SCALE_STEPS.index(1.0)
        )
        self._cmb_proxy_scale.setCurrentIndex(scale_idx)
        self._cmb_proxy_scale.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._cmb_proxy_scale.currentIndexChanged.connect(self._on_proxy_scale_changed)
        tlay.addWidget(self._cmb_proxy_scale)

        self._btn_proxy_menu = QToolButton(self._transport)
        self._btn_proxy_menu.setObjectName("VideoPreviewProxyMenuBtn")
        self._btn_proxy_menu.setText("…")
        self._btn_proxy_menu.setToolTip("Proxy actions")
        self._btn_proxy_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_proxy_menu.clicked.connect(self._show_proxy_menu)
        tlay.addWidget(self._btn_proxy_menu)

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
        self._btn_add_marker = self._action_btn("flag", "Marker", "Add marker at playhead (K)")
        self._btn_add_marker.clicked.connect(self._add_marker_at_playhead)
        self._btn_add_marker.hide()
        tlay.addWidget(self._btn_in)
        tlay.addWidget(self._btn_out)
        tlay.addWidget(self._btn_add)
        tlay.addWidget(self._btn_add_marker)

        self._draw_transport = VideoReviewDrawTransportActions(self._transport)
        self._draw_transport.hide()
        self._draw_transport.keyframe_add_requested.connect(self._add_draw_keyframe_at_playhead)
        self._draw_transport.layer_add_requested.connect(self._add_draw_layer_on_keyframe)
        self._draw_transport.undo_stroke_requested.connect(self._undo_draw_stroke)
        tlay.addWidget(self._draw_transport)

        tlay.addStretch(1)

        self._speed_icon = self._transport_icon("gauge")
        self._speed_icon.setToolTip("Reset to 1×")
        self._speed_icon.clicked.connect(self._reset_playback_speed)
        tlay.addWidget(self._speed_icon)
        self._cmb_speed = QComboBox(self._transport)
        self._cmb_speed.setObjectName("VideoPreviewSpeedCombo")
        self._cmb_speed.setToolTip("Playback speed")
        for speed in PLAYBACK_SPEED_STEPS:
            label = "1×" if speed == 1.0 else f"{speed:g}×"
            self._cmb_speed.addItem(label, speed)
        speed_idx = (
            PLAYBACK_SPEED_STEPS.index(self._speed)
            if self._speed in PLAYBACK_SPEED_STEPS
            else PLAYBACK_SPEED_STEPS.index(1.0)
        )
        self._cmb_speed.setCurrentIndex(speed_idx)
        self._cmb_speed.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._cmb_speed.currentIndexChanged.connect(self._on_speed_changed)
        tlay.addWidget(self._cmb_speed)

        self._volume_icon = self._transport_icon("volume-2")
        self._volume_icon.setToolTip("")
        self._volume_icon.clicked.connect(self._toggle_volume_mute)
        tlay.addWidget(self._volume_icon)
        self._volume_slider = QSlider(Qt.Orientation.Horizontal, self._transport)
        self._volume_slider.setObjectName("VideoPreviewVolumeSlider")
        self._volume_slider.setMaximum(100)
        self._volume_slider.setValue(self._volume)
        self._volume_slider.setFixedWidth(80)
        self._volume_slider.setToolTip("")
        self._volume_slider.valueChanged.connect(self._on_volume)
        self._volume_slider.sliderPressed.connect(self._on_volume_slider_pressed)
        self._volume_slider.sliderReleased.connect(self._on_volume_slider_released)
        self._volume_slider.sliderMoved.connect(self._on_volume_slider_moved)
        self._volume_slider.installEventFilter(self)
        self._volume_icon.installEventFilter(self)
        self._volume_dragging = False
        tlay.addWidget(self._volume_slider)

        self._btn_sync = self._action_btn(
            "sync",
            "Sync",
            "Sync ranges to project sidecar",
            primary=True,
        )
        self._btn_sync.clicked.connect(self._sync_ranges)
        self._btn_export = self._action_btn("download", "Export…", "Export marked ranges")
        self._btn_export.clicked.connect(self._on_transport_export_clicked)
        tlay.addWidget(self._btn_sync)
        tlay.addWidget(self._btn_export)

        main_lay.addWidget(self._transport, 0)

        self._footer = _VideoPreviewFooterBar(self._main_column)
        footer_lay = QHBoxLayout(self._footer)
        footer_lay.setContentsMargins(
            _PREVIEW_CHROME_PAD_H,
            9,
            _PREVIEW_CHROME_PAD_H,
            9,
        )
        footer_lay.setSpacing(12)
        footer_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._footer_hint = VideoPreviewFooterHintBar(self._footer)
        footer_lay.addWidget(self._footer_hint, 1)
        self._footer_label = QLabel("", self._footer)
        self._footer_label.setObjectName("VideoPreviewFooterLog")
        self._footer_label.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        self._footer_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._footer_label.setWordWrap(False)
        self._footer_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._footer_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        footer_lay.addWidget(self._footer_label, 0)
        main_lay.addWidget(self._footer, 0)

        # Stabilize footer height (24px keycaps + 8px vertical padding).
        self._footer.setFixedHeight(42)
        self._footer_label.setFixedHeight(24)

        body.addWidget(self._main_column)

        self._tools_panel = ReviewToolsPanel(self)
        self._tools_panel.workspace_changed.connect(self._on_tools_workspace_changed)
        self._tools_panel.tool_mode_changed.connect(self._on_tools_mode_changed)
        self._tools_panel.range_selected.connect(self._on_range_selected)
        self._tools_panel.range_delete_requested.connect(self._delete_range)
        self._tools_panel.range_delete_all_requested.connect(self._delete_all_ranges)
        self._tools_panel.range_duplicate_requested.connect(self._duplicate_range)
        self._tools_panel.go_to_in_requested.connect(self._go_to_range_in)
        self._tools_panel.go_to_out_requested.connect(self._go_to_range_out)
        self._tools_panel.range_label_changed.connect(self._on_range_label_changed)
        self._tools_panel.marker_selected.connect(self._on_marker_selected)
        self._tools_panel.marker_deselected.connect(self._on_marker_deselected)
        self._tools_panel.marker_delete_requested.connect(self._delete_marker)
        self._tools_panel.marker_delete_all_requested.connect(self._delete_all_markers)
        self._tools_panel.marker_label_changed.connect(self._on_marker_label_changed)
        self._tools_panel.marker_export_requested.connect(self._export_markers_png)
        self._note_rail.panel().open_all_notes_requested.connect(self.open_all_notes_requested.emit)
        self._note_rail.panel().note_added.connect(self._on_review_note_added)
        self._note_rail.panel().time_anchor_requested.connect(self._on_note_time_anchor)
        self._tools_panel.draw_keyframe_selected.connect(self._on_draw_keyframe_selected)
        self._tools_panel.draw_layer_selected.connect(self._on_draw_layer_selected)
        self._tools_panel.draw_keyframe_add_requested.connect(self._add_draw_keyframe_at_playhead)
        self._tools_panel.draw_layer_add_requested.connect(self._add_draw_layer_on_keyframe)
        self._tools_panel.draw_undo_stroke_requested.connect(self._undo_draw_stroke)
        self._draw_brush_strip.tool_changed.connect(self._on_draw_tool_changed)
        self._draw_brush_strip.color_changed.connect(self._on_draw_color_changed)
        self._draw_brush_strip.width_changed.connect(self._on_draw_width_changed)
        self._draw_brush_strip.onion_enabled_changed.connect(self._on_draw_onion_enabled)
        self._draw_brush_strip.onion_span_changed.connect(self._on_draw_onion_span_changed)
        self._tools_panel.draw_keyframe_edit_frame_changed.connect(self._on_draw_keyframe_edit_frame_changed)
        self._tools_panel.draw_keyframe_hold_changed.connect(self._on_draw_keyframe_hold_changed)
        self._tools_panel.draw_keyframe_delete_requested.connect(self._delete_active_draw_keyframe)
        self._tools_panel.draw_layer_visibility_toggle_requested.connect(
            self._toggle_active_draw_layer_visibility
        )
        self._tools_panel.draw_layer_default_hold_changed.connect(
            self._on_draw_layer_default_hold_changed
        )
        self._tools_panel.draw_layer_delete_requested.connect(self._delete_draw_layer)
        self._tools_panel.draw_keyframe_visibility_toggle_requested.connect(
            self._toggle_active_draw_keyframe_visibility
        )
        body.addWidget(self._tools_panel)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        body.splitterMoved.connect(self._on_body_splitter_moved)
        self._note_rail.open_changed.connect(self._on_note_rail_open_changed)
        self._side_panel_layout_persist_timer = QTimer(self)
        self._side_panel_layout_persist_timer.setSingleShot(True)
        self._side_panel_layout_persist_timer.setInterval(400)
        self._side_panel_layout_persist_timer.timeout.connect(self._persist_side_panel_layout)

        self._root_layout.addWidget(self._top_bar, 0)
        self._root_layout.addWidget(body, 1)

        self._sync_tools_panel_button()
        self._sync_shell_corner_radius()
        self._sync_media_capabilities()
        QTimer.singleShot(0, self._sync_body_splitter_sizes)

    def _on_note_rail_open_changed(self, open: bool) -> None:
        self._sync_body_splitter_sizes()
        if self._context == PreviewContext.entity:
            self._note_rail_open_pref = open
            self._schedule_side_panel_layout_persist()

    def _on_body_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self._body_splitter.sizes()
        if len(sizes) < 3:
            return
        if self._note_rail.is_open() and sizes[0] >= NOTE_RAIL_MIN_W:
            self._note_rail_saved_w = sizes[0]
        if self._tools_panel.workspace() == ReviewWorkspace.tools and sizes[2] >= TOOLS_PANEL_MIN_W:
            self._tools_panel_saved_w = sizes[2]
        self._schedule_side_panel_layout_persist()

    def _schedule_side_panel_layout_persist(self) -> None:
        if self._settings is None:
            return
        self._side_panel_layout_persist_timer.start()

    def _persist_side_panel_layout(self) -> None:
        if self._settings is None:
            return
        write_review_note_rail_width(
            self._settings,
            self._profile_key,
            self._note_rail_saved_w,
            min_w=NOTE_RAIL_MIN_W,
            max_w=NOTE_RAIL_MAX_W,
        )
        write_review_tools_panel_width(
            self._settings,
            self._profile_key,
            self._tools_panel_saved_w,
            min_w=TOOLS_PANEL_MIN_W,
            max_w=TOOLS_PANEL_MAX_W,
        )
        if self._context == PreviewContext.entity:
            write_review_note_rail_open(
                self._settings,
                self._profile_key,
                bool(getattr(self, "_note_rail_open_pref", self._note_rail.is_open())),
            )

    def _sync_body_splitter_sizes(self) -> None:
        splitter = getattr(self, "_body_splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        total = max(sum(sizes), splitter.width(), 800)
        left = self._note_rail_saved_w if self._note_rail.is_open() else 0
        right = (
            self._tools_panel_saved_w
            if self._tools_panel.workspace() == ReviewWorkspace.tools
            else 0
        )
        center = max(240, total - left - right)
        splitter.blockSignals(True)
        splitter.setSizes([left, center, right])
        splitter.blockSignals(False)

    def _is_sequence_mode(self) -> bool:
        return self._media_kind == ReviewMediaKind.sequence

    def _media_key(self) -> Path | None:
        if self._is_sequence_mode():
            return self._sequence_folder
        return self._path

    def _sync_media_capabilities(self) -> None:
        seq = self._is_sequence_mode()
        for w in (
            self._chk_proxy,
            self._cmb_proxy_scale,
            self._btn_proxy_menu,
            self._chk_precise_scrub,
            self._speed_icon,
            self._cmb_speed,
            self._volume_icon,
            self._volume_slider,
            self._btn_prev_file,
            self._btn_next_file,
        ):
            w.setVisible(not seq)
        self._fps_label.setVisible(seq)
        self._fps_spin.setVisible(seq)
        self._sync_title_btn_enabled()
        if seq:
            self._surface.setToolTip("Middle-drag horizontally to scrub frames")
        else:
            self._surface.setToolTip(
                "Middle-drag horizontally to scrub — wraps at selected range In/Out or video ends"
            )

    def load_media(self, request: ReviewOpenRequest) -> None:
        """Load or switch review media without closing the dialog."""
        self._last_playhead_ui_frame = None
        prev_key = self._media_key()
        next_key = request.media_key
        if prev_key is not None and prev_key != next_key:
            self._persist_ranges_local()
            self._persist_markers_local()
            self._persist_draw_local()
            self._persist_preview_session()
        self._review_request = request
        self._media_kind = request.media_kind
        self._context = request.context
        self._profile_key = request.settings_profile_key
        self._entity_path = request.entity_path
        self._department_id = request.department_id
        self._department_label = request.department_label
        self._work_path = request.work_path
        self._work_file_path = request.work_file_path
        self._source_label = request.source_label
        if request.media_kind == ReviewMediaKind.sequence:
            self._path = None
            self._paths = []
            self._sequence_frames = list(request.frames or ())
            self._sequence_folder = request.sequence_folder
            self._info = None
            self._load_sequence(request)
        else:
            self._sequence_load_token += 1
            self._show_sequence_loading(False)
            assert request.path is not None
            self._sequence_frames = []
            self._sequence_folder = None
            self._paths = list(request.sibling_paths or list_video_siblings(request.path))
            if request.path not in self._paths:
                self._paths = [request.path] + self._paths
            self._path_index = max(0, self._paths.index(request.path))
            self._load_file(request.path)
        self._sync_media_capabilities()
        self.apply_profile(self._context)
        self._update_top_bar()
        self._update_footer()

    def _release_sequence_backend(self) -> None:
        if self._seq_backend is None:
            return
        try:
            self._seq_backend.frame_changed.disconnect(self._on_seq_frame_changed)
            self._seq_backend.playback_ended.disconnect(self._on_seq_playback_ended)
        except Exception:
            pass
        self._seq_backend.release()
        label = self._seq_backend.display_target()
        label.setParent(None)
        label.hide()
        self._seq_backend.deleteLater()
        self._seq_backend = None
        self._seq_label_parented = False

    def _attach_sequence_display(self) -> None:
        if self._seq_backend is None or self._seq_label_parented:
            return
        label = self._seq_backend.display_target()
        label.setParent(self._surface)
        lay = self._surface.layout()
        if lay is None:
            lay = QVBoxLayout(self._surface)
            lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(label, 1)
        self._seq_label_parented = True
        label.show()
        self._video_attached = False

    def _load_sequence(self, request: ReviewOpenRequest) -> None:
        assert request.sequence_folder is not None
        self._set_surface_native_for_mode()
        self._sequence_load_token += 1
        token = self._sequence_load_token
        folder = request.sequence_folder
        self._sequence_folder = folder
        self._release_sequence_backend()
        self.setWindowTitle(f"{folder.name} · sequence")
        self._show_sequence_loading(True)
        precached = list(request.frames or ()) or None
        QThreadPool.globalInstance().start(
            _SequenceListRunnable(
                token,
                folder,
                self._sequence_list_signaler,
                precached_frames=precached,
            )
        )

    def _show_sequence_loading(self, loading: bool) -> None:
        if not hasattr(self, "_sequence_loading_overlay"):
            return
        self._sequence_loading_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not loading,
        )
        self._sequence_loading_overlay.setVisible(loading)
        if loading:
            self._sequence_loading_overlay.raise_()
            self._position_sequence_loading_overlay()

    def _position_sequence_loading_overlay(self) -> None:
        if not hasattr(self, "_sequence_loading_overlay") or not self._viewer:
            return
        rect = self._video_overlay_rect()
        if rect.isNull():
            self._sequence_loading_overlay.setGeometry(self._viewer.rect())
        else:
            self._sequence_loading_overlay.setGeometry(rect)

    def _on_sequence_frames_listed(self, token: int, frames: object) -> None:
        if token != self._sequence_load_token:
            return
        if not isinstance(frames, list) or not frames:
            self._show_sequence_loading(False)
            self._status_log = "No frames in sequence folder"
            self._update_footer()
            return
        req = self._review_request
        if req is None or req.sequence_folder is None:
            return
        QTimer.singleShot(
            0,
            lambda t=token, r=req, f=frames: self._complete_sequence_load(r, f, t),
        )

    def _complete_sequence_load(
        self,
        request: ReviewOpenRequest,
        frames: list[Path],
        token: int,
    ) -> None:
        if token != self._sequence_load_token:
            return
        assert request.sequence_folder is not None
        if not frames:
            self._show_sequence_loading(False)
            return
        self._sequence_frames = frames
        n = len(frames)
        fps = max(1, min(60, int(request.fps)))
        self._fps_spin.blockSignals(True)
        self._fps_spin.setValue(fps)
        self._fps_spin.blockSignals(False)
        folder = request.sequence_folder
        self.setWindowTitle(f"{folder.name} · sequence")
        self._status_log = ""
        self._hover_key_frames = []
        self._clear_range_undo_stacks()
        self._active_range_id = None
        self._range_edit_unlocked = False
        self._draft_in = None
        self._draft_out = None
        self._reset_viewer_plate_transform()
        self._playback_primed = True
        self._cancel_proxy_build()
        total = max(1, n)
        self._range_list().set_fps(float(fps))
        self._scrubber.set_frame_count(total, refit_view=True)
        self._scrubber.clear_overlap_cycle()
        self._seq_backend = SequenceDecodeBackend(frames, fps=fps, parent=self)
        self._seq_backend.frame_changed.connect(self._on_seq_frame_changed)
        self._seq_backend.playback_ended.connect(self._on_seq_playback_ended)
        self._attach_sequence_display()
        self._sync_sequence_playback_loop()
        self._seek_frame(0)
        self._show_sequence_loading(False)
        self._update_top_bar()
        self._update_footer()
        QTimer.singleShot(
            0,
            lambda t=token, r=request, f=frames: self._complete_sequence_metadata(r, f, t),
        )

    def _complete_sequence_metadata(
        self,
        request: ReviewOpenRequest,
        frames: list[Path],
        token: int,
    ) -> None:
        if token != self._sequence_load_token:
            return
        assert request.sequence_folder is not None
        folder = request.sequence_folder
        total = max(1, len(frames))
        self._ranges = []
        self._published_ranges = []
        self._markers = []
        self._published_markers = []
        self._draw_layers = []
        self._published_draw_layers = []
        self._active_keyframe_frame = None
        self._active_layer_id = None
        self._active_marker_id = None
        published, working, _ = load_sequence_ranges_for_preview(folder, total_frames=total)
        self._published_ranges = published
        self._ranges = working
        pub_m, work_m, _ = load_sequence_markers_for_preview(folder, total_frames=total)
        self._published_markers = pub_m
        self._markers = work_m
        if self._markers and self._active_marker_id is None:
            self._active_marker_id = self._markers[0].id
        self._load_draw_keyframes_from_sidecar()
        if not self._consume_pending_time_anchor():
            saved_frame = load_sequence_preview_session_local_draft(folder, total_frames=total)
            if saved_frame is not None and saved_frame != self._current_frame():
                self._seek_frame(saved_frame)
        self._sync_range_ui()
        QTimer.singleShot(0, lambda: self._open_review_tools_panel(ReviewToolMode.ranges))
        self._update_top_bar()
        self._update_footer()

    def _open_review_tools_panel(self, mode: ReviewToolMode = ReviewToolMode.ranges) -> None:
        """Show sidebar tools (ranges / markers / draw)."""
        if mode == ReviewToolMode.note:
            mode = ReviewToolMode.ranges
        self._tools_panel.set_workspace(ReviewWorkspace.tools)
        self._tools_panel.activate_tool_mode(mode)
        self._sync_shell_corner_radius()
        self._sync_tools_panel_button()
        self._raise_video_overlays()
        self._update_note_frame_hint()
        self._sync_transport_tool_controls()
        self._sync_scrubber_timeline_display()

    def _on_sequence_fps_changed(self, value: int) -> None:
        if not self._is_sequence_mode() or self._seq_backend is None:
            return
        fps = max(1, min(60, int(value)))
        self._seq_backend.set_fps(fps)
        self._range_list().set_fps(float(fps))
        self._scrubber.set_fps(float(fps))
        if self._settings is not None:
            write_sequence_preview_fps(self._settings, fps)

    def _on_seq_frame_changed(self, frame: int) -> None:
        if self._scrubbing or self._in_programmatic_seek:
            return
        self._apply_playhead_ui(int(frame))

    def _on_seq_playback_ended(self) -> None:
        if self._loop_playback:
            start, _ = self._loop_bounds()
            self._seek_frame(start)
            if self._seq_backend is not None:
                self._seq_backend.play()
                self._set_play_icon(True)
                self._update_loop_timer()
        else:
            self._set_play_icon(False)

    def _sync_shell_corner_radius(self) -> None:
        """Footer spans full width when tools body is closed — paint matching bottom corners."""
        tools_side = self._tools_panel.workspace() == ReviewWorkspace.tools
        self._footer.set_wide_bottom(not tools_side)

    def _video_overlay_rect(self) -> QRect:
        if not self._surface_wrap or not self._viewer:
            return QRect()
        top_left = self._surface_wrap.mapTo(self._viewer, QPoint(0, 0))
        return QRect(top_left, self._surface_wrap.size())

    def _reset_viewer_plate_transform(self) -> None:
        self._viewer_plate_zoom = _VIEWER_PLATE_ZOOM_FIT
        self._viewer_plate_pan = QPointF(0.0, 0.0)
        self._viewer_wheel_log_accum = 0.0
        self._viewer_wheel_coalesce.stop()
        if self._video_pan_active and hasattr(self, "_surface_wrap"):
            if QWidget.mouseGrabber() is self._surface_wrap:
                self._surface_wrap.releaseMouse()
        self._video_pan_active = False
        if hasattr(self, "_surface_wrap"):
            self._surface_wrap.unsetCursor()
        if hasattr(self, "_surface"):
            self._sync_viewer_viewport_transform()

    def _viewer_wrap_content_rect(self) -> QRect:
        wrap = self._surface_wrap
        return QRect(0, 0, wrap.width(), max(1, wrap.height() - _VIDEO_NATIVE_CLIP_BOTTOM))

    def _viewer_plate_aspect(self) -> float:
        if self._info is not None and self._info.width > 0 and self._info.height > 0:
            return self._info.width / self._info.height
        area = self._viewer_wrap_content_rect()
        return area.width() / max(1, area.height())

    def _viewer_base_plate_rect(self) -> QRect:
        area = self._viewer_wrap_content_rect()
        w, h = area.width(), area.height()
        if w <= 0 or h <= 0:
            return QRect()
        aspect = self._viewer_plate_aspect()
        if w / h > aspect:
            ph = h
            pw = int(round(ph * aspect))
        else:
            pw = w
            ph = int(round(pw / aspect))
        x = area.x() + (w - pw) // 2
        y = area.y() + (h - ph) // 2
        return QRect(x, y, pw, ph)

    def _viewer_plate_geometry_f(self) -> QRectF:
        base = QRectF(self._viewer_base_plate_rect())
        if base.isEmpty():
            return QRectF()
        zoom = self._viewer_plate_zoom
        pw = base.width() * zoom
        ph = base.height() * zoom
        cx = base.center().x() + self._viewer_plate_pan.x()
        cy = base.center().y() + self._viewer_plate_pan.y()
        return QRectF(cx - pw / 2.0, cy - ph / 2.0, pw, ph)

    def _viewer_plate_geometry(self) -> QRect:
        geom = self._viewer_plate_geometry_f()
        if geom.isEmpty():
            return QRect()
        return QRect(
            int(round(geom.x())),
            int(round(geom.y())),
            max(1, int(round(geom.width()))),
            max(1, int(round(geom.height()))),
        )

    def _viewer_video_content_rect_f(self, plate: QRectF | None = None) -> QRectF:
        if plate is None:
            plate = self._viewer_plate_geometry_f()
        if plate.isEmpty():
            return plate
        aspect = self._viewer_plate_aspect()
        pw, ph = plate.width(), plate.height()
        if pw <= 1e-6 or ph <= 1e-6:
            return plate
        if pw / ph > aspect:
            ch = ph
            cw = ch * aspect
        else:
            cw = pw
            ch = cw / aspect
        return QRectF(
            plate.x() + (pw - cw) / 2.0,
            plate.y() + (ph - ch) / 2.0,
            cw,
            ch,
        )

    def _viewer_is_zoomed(self) -> bool:
        return abs(self._viewer_plate_zoom - _VIEWER_PLATE_ZOOM_FIT) > 1e-6

    def _viewer_at_fit_zoom(self) -> bool:
        return abs(self._viewer_plate_zoom - _VIEWER_PLATE_ZOOM_FIT) <= 1e-6

    def _clear_backend_viewport_transform(self) -> None:
        if not self._video_attached or getattr(self._backend, "name", "") != "mpv":
            return
        host = self._viewer_wrap_content_rect()
        self._backend.set_viewport_transform(
            1.0,
            QPointF(0.0, 0.0),
            host.width(),
            host.height(),
            self._viewer_plate_aspect(),
        )

    def _sync_viewer_viewport_transform(self) -> None:
        if hasattr(self, "_draw_overlay"):
            self._draw_overlay.set_viewport_transform(
                self._viewer_plate_zoom,
                QPointF(self._viewer_plate_pan),
                self._viewer_plate_aspect(),
                enabled=False,
            )
        self._clear_backend_viewport_transform()

    @staticmethod
    def _alt_mod_active(event: QEvent) -> bool:
        mods = event.modifiers() if hasattr(event, "modifiers") else Qt.KeyboardModifier.NoModifier
        kb = QApplication.keyboardModifiers()
        return bool(mods & Qt.KeyboardModifier.AltModifier) or bool(
            kb & Qt.KeyboardModifier.AltModifier
        )

    def _cursor_pos_in_surface_wrap(self) -> QPointF:
        local = self._surface_wrap.mapFromGlobal(QCursor.pos())
        return QPointF(local)

    def _surface_wrap_global_rect(self) -> QRect:
        if not hasattr(self, "_surface_wrap") or not self._surface_wrap.isVisible():
            return QRect()
        return QRect(self._surface_wrap.mapToGlobal(QPoint(0, 0)), self._surface_wrap.size())

    def _cursor_in_surface_wrap(self, gpos: QPoint | None = None) -> bool:
        rect = self._surface_wrap_global_rect()
        if rect.isEmpty():
            return False
        pt = gpos if gpos is not None else QCursor.pos()
        return rect.contains(pt)

    def _install_app_event_filter(self) -> None:
        app = QApplication.instance()
        if app is None or self._app_event_filter_installed:
            return
        app.installEventFilter(self)
        self._app_event_filter_installed = True

    def _remove_app_event_filter(self) -> None:
        app = QApplication.instance()
        if app is None or not self._app_event_filter_installed:
            return
        app.removeEventFilter(self)
        self._app_event_filter_installed = False

    def _clamp_viewer_plate_pan(self) -> None:
        if self._viewer_at_fit_zoom():
            self._viewer_plate_pan = QPointF(0.0, 0.0)
            return
        if self._viewer_plate_zoom <= _VIEWER_PLATE_ZOOM_MIN + 1e-6:
            self._viewer_plate_pan = QPointF(0.0, 0.0)
            return
        base = self._viewer_base_plate_rect()
        area = self._viewer_wrap_content_rect()
        if base.isEmpty() or area.isEmpty():
            return
        pw = max(1, int(round(base.width() * self._viewer_plate_zoom)))
        ph = max(1, int(round(base.height() * self._viewer_plate_zoom)))
        cx_min = area.left() + pw / 2.0
        cx_max = area.right() + 1.0 - pw / 2.0
        cy_min = area.top() + ph / 2.0
        cy_max = area.bottom() + 1.0 - ph / 2.0
        base_cx = base.center().x()
        base_cy = base.center().y()
        lo_x = min(cx_min - base_cx, cx_max - base_cx)
        hi_x = max(cx_min - base_cx, cx_max - base_cx)
        lo_y = min(cy_min - base_cy, cy_max - base_cy)
        hi_y = max(cy_min - base_cy, cy_max - base_cy)
        pan_x = min(max(self._viewer_plate_pan.x(), lo_x), hi_x)
        pan_y = min(max(self._viewer_plate_pan.y(), lo_y), hi_y)
        self._viewer_plate_pan = QPointF(pan_x, pan_y)

    def _zoom_viewer_plate(self, factor: float, anchor_in_wrap: QPointF | None = None) -> None:
        old_zoom = self._viewer_plate_zoom
        new_zoom = max(
            _VIEWER_PLATE_ZOOM_MIN,
            min(_VIEWER_PLATE_ZOOM_MAX, old_zoom * factor),
        )
        if abs(new_zoom - old_zoom) < 1e-4:
            return
        anchor = anchor_in_wrap if anchor_in_wrap is not None else self._cursor_pos_in_surface_wrap()
        old_plate = self._viewer_plate_geometry_f()
        old_content = self._viewer_video_content_rect_f(old_plate)
        if old_content.isEmpty():
            return
        ux = (anchor.x() - old_content.x()) / old_content.width()
        uy = (anchor.y() - old_content.y()) / old_content.height()
        ux = max(0.0, min(1.0, ux))
        uy = max(0.0, min(1.0, uy))
        self._viewer_plate_zoom = new_zoom
        if self._viewer_at_fit_zoom() or new_zoom <= _VIEWER_PLATE_ZOOM_MIN + 1e-6:
            self._viewer_plate_pan = QPointF(0.0, 0.0)
        else:
            base = QRectF(self._viewer_base_plate_rect())
            aspect = self._viewer_plate_aspect()
            new_pw = base.width() * new_zoom
            new_ph = base.height() * new_zoom
            if new_pw / max(1e-6, new_ph) > aspect:
                new_ch = new_ph
                new_cw = new_ch * aspect
            else:
                new_cw = new_pw
                new_ch = new_cw / aspect
            new_content_x = anchor.x() - ux * new_cw
            new_content_y = anchor.y() - uy * new_ch
            new_plate_x = new_content_x - (new_pw - new_cw) / 2.0
            new_plate_y = new_content_y - (new_ph - new_ch) / 2.0
            new_cx = new_plate_x + new_pw / 2.0
            new_cy = new_plate_y + new_ph / 2.0
            self._viewer_plate_pan = QPointF(
                new_cx - base.center().x(),
                new_cy - base.center().y(),
            )
            self._clamp_viewer_plate_pan()
        self._apply_viewer_plate_geometry()
        self._refresh_footer_hint()

    def _apply_viewer_plate_geometry(self) -> None:
        if not hasattr(self, "_surface"):
            return
        rect = self._viewer_plate_geometry()
        if rect.isEmpty():
            return
        self._surface.setGeometry(rect)
        if hasattr(self, "_onion_layer"):
            self._onion_layer.setGeometry(rect)
        if hasattr(self, "_draw_overlay"):
            self._draw_overlay.setGeometry(rect)
        self._sync_viewer_viewport_transform()
        host = self._viewer_wrap_content_rect()
        host_size = QSize(host.width(), host.height())
        if self._is_sequence_mode() and self._seq_backend is not None:
            self._seq_backend.resize_display()
        elif self._video_attached and host_size != self._viewer_last_host_size:
            self._viewer_last_host_size = host_size
            self._backend.layout_video()

    def _mouse_event_pos_in_wrap(self, event: QMouseEvent, watched: QObject) -> QPointF:
        if self._video_pan_active or watched is self._surface_wrap:
            return QPointF(self._surface_wrap.mapFromGlobal(event.globalPosition().toPoint()))
        if isinstance(watched, QWidget):
            pt = watched.mapTo(self._surface_wrap, event.position().toPoint())
            return QPointF(pt)
        return event.position()

    def _set_surface_native_for_mode(self) -> None:
        """mpv needs a native leaf surface; sequence uses QLabel (no native)."""
        if not hasattr(self, "_surface"):
            return
        self._surface.setAttribute(
            Qt.WidgetAttribute.WA_NativeWindow,
            not self._is_sequence_mode(),
        )

    def _sync_video_backend(self) -> None:
        if self._is_sequence_mode():
            self._set_surface_native_for_mode()
            self._attach_sequence_display()
            return
        if not self._video_attached:
            self._backend.attach_to_widget(self._surface)
            self._video_attached = True
        self._backend.layout_video()
        QTimer.singleShot(0, self._sync_native_video_zorder)

    def _apply_dialog_content_inset(self) -> None:
        if self._fullscreen:
            self._root_layout.setContentsMargins(0, 0, 0, 0)
            return
        # Top inset only — footer/tools flush left/right/bottom under raised border.
        self._root_layout.setContentsMargins(0, _DIALOG_BORDER_INSET, 0, 0)

    def _update_rounded_mask(self) -> None:
        # Mask + native mpv HWND on Windows breaks hit-testing for the whole dialog.
        return

    def raise_border_overlay(self) -> None:
        """No full-dialog border widget — it occludes embedded mpv on Windows."""
        self._raise_video_chrome_overlays()

    def _raise_video_chrome_overlays(self) -> None:
        if self._hud:
            self._hud.raise_()
        if hasattr(self, "_draw_brush_strip") and self._draw_brush_strip.isVisible():
            self._position_draw_brush_strip()
            self._draw_brush_strip.raise_()
        if hasattr(self, "_onion_layer"):
            self._sync_viewport_overlay_geometry()
            self._onion_layer.raise_()
        if hasattr(self, "_draw_overlay"):
            self._sync_viewport_overlay_geometry()
            self._draw_overlay.raise_()
        overlay = getattr(self, "_proxy_build_overlay", None)
        if overlay and overlay.isVisible():
            overlay.raise_()
        seq_overlay = getattr(self, "_sequence_loading_overlay", None)
        if seq_overlay and seq_overlay.isVisible():
            self._position_sequence_loading_overlay()
            seq_overlay.raise_()
        btn = getattr(self, "_btn_close", None)
        if btn is not None and btn.isVisible():
            btn.raise_()
        self._sync_native_video_zorder()

    def _review_video_draw_overlay_fix_enabled(self) -> bool:
        return read_review_video_draw_overlay_fix(self._settings)

    def _draw_overlay_above_video(self) -> bool:
        if self._is_sequence_mode() or not self._review_video_draw_overlay_fix_enabled():
            return False
        overlay = getattr(self, "_draw_overlay", None)
        return overlay is not None and overlay.isVisible()

    def _mpv_mouse_pass_through_for_draw(self) -> bool:
        return self._draw_overlay_above_video() and self._draw_tool_active()

    def _sync_native_video_zorder(self) -> None:
        if sys.platform != "win32" or self._is_sequence_mode():
            return
        surface = getattr(self, "_surface", None)
        if surface is None or not surface.isVisible():
            return
        if self._draw_overlay_above_video():
            from monostudio.ui_qt.win32_video_overlay import sync_video_draw_overlay_zorder

            sync_video_draw_overlay_zorder(
                surface,
                self._draw_overlay,
                mouse_pass_through=self._mpv_mouse_pass_through_for_draw(),
            )
            return
        try:
            from monostudio.ui_qt.win32_video_overlay import (
                raise_embedded_video_surface,
                restore_embedded_video_input,
            )

            restore_embedded_video_input(surface)
            raise_embedded_video_surface(surface)
        except Exception:
            pass

    def _deferred_video_attach(self) -> None:
        if self._closing or not self.isVisible():
            return
        self._apply_viewer_plate_geometry()
        self._sync_video_backend()
        self._position_hud()
        self._position_sequence_loading_overlay()
        self._raise_video_chrome_overlays()
        QTimer.singleShot(0, self._sync_native_video_zorder)
        QTimer.singleShot(80, self._raise_video_chrome_overlays)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if self._fullscreen and event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            gpos = event.globalPosition().toPoint()
            if self.frameGeometry().contains(gpos):
                self._update_fullscreen_chrome(gpos)
        if (
            self.isVisible()
            and not self._closing
            and event.type() == QEvent.Type.Wheel
            and isinstance(event, QWheelEvent)
            and self._cursor_in_surface_wrap(event.globalPosition().toPoint())
        ):
            if self._filter_video_surface_wheel(event, self._surface_wrap):
                return True
        if (
            self._video_pan_active
            and isinstance(event, QMouseEvent)
            and event.type()
            in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease)
        ):
            handled = self._filter_video_surface_mouse(event, self._surface_wrap)
            if handled:
                return True
        draw_overlay = getattr(self, "_draw_overlay", None)
        surface_hosts = (self._surface_wrap, self._surface) + (
            (draw_overlay,) if draw_overlay is not None else ()
        )
        if watched in surface_hosts:
            if event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonRelease,
            ) and isinstance(event, QMouseEvent):
                handled = self._filter_video_surface_mouse(event, watched)
                if handled is not None:
                    return handled
        if watched is self._surface_wrap or watched is self._surface or watched is draw_overlay:
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if (
                    event.button() == Qt.MouseButton.RightButton
                ):
                    self._show_draw_quick_popup(event.globalPosition().toPoint())
                    return True
        if watched is self._surface_wrap:
            if event.type() == QEvent.Type.Enter:
                if not self._text_editing_focused():
                    self._surface_wrap.setFocus(Qt.FocusReason.MouseFocusReason)
                if not self._scrubber.underMouse():
                    self._set_footer_pointer_zone("video")
            elif event.type() == QEvent.Type.Leave:
                if not self._scrubber.underMouse():
                    self._set_footer_pointer_zone("")
            elif event.type() == QEvent.Type.Resize:
                self._apply_viewer_plate_geometry()
                self._sync_video_backend()
                self._position_hud()
                self._raise_video_overlays()
        vol_slider = getattr(self, "_volume_slider", None)
        vol_icon = getattr(self, "_volume_icon", None)
        if vol_slider is not None and watched in (vol_slider, vol_icon):
            if event.type() == QEvent.Type.Enter:
                self._show_volume_tooltip(watched)
            elif event.type() == QEvent.Type.Leave:
                if not self._volume_dragging:
                    QToolTip.hideText()
            elif (
                watched is vol_slider
                and event.type() == QEvent.Type.MouseMove
                and isinstance(event, QMouseEvent)
            ):
                self._show_volume_tooltip(vol_slider, vol_slider.value(), event.globalPos())
        return super().eventFilter(watched, event)

    def _filter_video_surface_wheel(self, event: QWheelEvent, watched: QObject) -> bool:
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return False
        delta_y = VideoPreviewScrubber._wheel_vertical_delta(event)
        if delta_y == 0:
            return False
        steps = delta_y / 120.0
        factor = math.pow(_VIEWER_PLATE_ZOOM_WHEEL_FACTOR, steps)
        self._viewer_wheel_log_accum += math.log(factor)
        if not self._viewer_wheel_coalesce.isActive():
            self._apply_pending_viewer_wheel_zoom()
        self._viewer_wheel_coalesce.start()
        return True

    def _apply_pending_viewer_wheel_zoom(self) -> None:
        if abs(self._viewer_wheel_log_accum) < 1e-9:
            return
        factor = math.exp(self._viewer_wheel_log_accum)
        self._viewer_wheel_log_accum = 0.0
        self._zoom_viewer_plate(factor, self._cursor_pos_in_surface_wrap())

    def _filter_video_surface_mouse(self, event: QEvent, watched: QObject) -> bool | None:
        surface = self._surface
        et = event.type()
        if not isinstance(event, QMouseEvent):
            return None
        mmb_pressed = event.button() == Qt.MouseButton.MiddleButton
        mmb_held = bool(event.buttons() & Qt.MouseButton.MiddleButton)
        alt_held = self._alt_mod_active(event)
        pos_wrap = self._mouse_event_pos_in_wrap(event, watched)
        if et == QEvent.Type.MouseButtonPress and mmb_pressed and alt_held:
            if self._viewer_is_zoomed():
                self._video_pan_active = True
                self._video_pan_press = pos_wrap
                self._video_pan_origin = QPointF(self._viewer_plate_pan)
                self._video_scrub_pending = False
                self._video_scrub_active = False
                self._surface_wrap.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._surface_wrap.grabMouse()
            return True
        if et == QEvent.Type.MouseMove and mmb_held and self._video_pan_active:
            delta = pos_wrap - self._video_pan_press
            self._viewer_plate_pan = self._video_pan_origin + delta
            self._clamp_viewer_plate_pan()
            self._apply_viewer_plate_geometry()
            return True
        if et == QEvent.Type.MouseButtonRelease and mmb_pressed and self._video_pan_active:
            self._video_pan_active = False
            if QWidget.mouseGrabber() is self._surface_wrap:
                self._surface_wrap.releaseMouse()
            self._surface_wrap.unsetCursor()
            return True
        if self._draw_tool_active():
            if et == QEvent.Type.MouseButtonPress and not mmb_pressed:
                return False
            if et == QEvent.Type.MouseMove and not mmb_held:
                return False
            if et == QEvent.Type.MouseButtonRelease and not mmb_pressed:
                return False
        if et == QEvent.Type.MouseButtonPress:
            if not mmb_pressed:
                return False
            self._video_scrub_pending = True
            self._video_scrub_active = False
            self._video_scrub_start_x = int(pos_wrap.x())
            self._video_scrub_origin_frame = self._current_frame()
            return False
        if et == QEvent.Type.MouseMove:
            if not mmb_held:
                return False
            if not self._video_scrub_pending and not self._video_scrub_active:
                return False
            dx = int(pos_wrap.x()) - self._video_scrub_start_x
            if not self._video_scrub_active:
                if abs(dx) < _VIDEO_SCRUB_DRAG_THRESHOLD_PX:
                    return False
                self._video_scrub_active = True
                self._on_scrub_pressed()
                surface.setCursor(Qt.CursorShape.SizeHorCursor)
            frame = self._frame_from_video_scrub_dx(dx, surface.width())
            self._apply_video_scrub_frame(frame)
            return True
        if et == QEvent.Type.MouseButtonRelease:
            if not mmb_pressed:
                return False
            if self._video_scrub_active:
                dx = int(pos_wrap.x()) - self._video_scrub_start_x
                frame = self._frame_from_video_scrub_dx(dx, surface.width())
                self._on_scrub_released(frame)
                surface.unsetCursor()
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
        self._scrubber.set_position_frame(frame)
        if self._scrubbing:
            self._on_scrub_frame_preview(frame)
        elif self._draw_playhead_sync_needed(frame):
            self._sync_draw_playhead(frame)

    def _tool_btn(self, icon: str, tip: str, *, compact: bool = False) -> QToolButton:
        btn = QToolButton(self)
        if compact:
            btn.setObjectName("VideoPreviewTransportTool")
        btn.setIcon(lucide_icon(icon, size=18, color_hex=MONOS_COLORS["text_label"]))
        btn.setIconSize(QSize(18, 18))
        btn.setToolTip(tip)
        btn.setAutoRaise(True)
        size = 28 if compact else 32
        btn.setFixedSize(size, size)
        return btn

    def _transport_icon(self, icon: str) -> QToolButton:
        btn = QToolButton(self._transport)
        btn.setObjectName("VideoPreviewTransportTool")
        btn.setIcon(lucide_icon(icon, size=16, color_hex=MONOS_COLORS["text_meta"]))
        btn.setIconSize(QSize(16, 16))
        btn.setAutoRaise(True)
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
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
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, self._on_enter)
        QShortcut(QKeySequence(Qt.Key.Key_A), self, self._add_draft_range)
        QShortcut(QKeySequence(Qt.Key.Key_C), self, self._copy_timecode)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._frame_step(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._frame_step(1))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self._jump_sec(-1))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self._jump_sec(1))
        QShortcut(QKeySequence(Qt.Key.Key_BracketLeft), self, self._select_prev_list_item)
        QShortcut(QKeySequence(Qt.Key.Key_BracketRight), self, self._select_next_list_item)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_file)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_file)
        sc_del = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        sc_del.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_del.activated.connect(self._delete_active_item)
        sc_bs = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        sc_bs.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_bs.activated.connect(self._delete_active_item)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, self._select_prev_range)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, self._select_next_range)
        QShortcut(QKeySequence("Ctrl+F"), self, self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._on_escape)
        QShortcut(QKeySequence(Qt.Key.Key_E), self, self._on_edit_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Z), self, self._on_fit_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self._focus_timeline_range)
        QShortcut(QKeySequence(Qt.Key.Key_Tab), self, self._cycle_workspace)
        sc_tools = QShortcut(QKeySequence(Qt.Key.Key_T), self)
        sc_tools.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_tools.activated.connect(self._toggle_tools_workspace)
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
        QShortcut(QKeySequence(Qt.Key.Key_M), self, lambda: self._activate_tool(ReviewToolMode.markers))
        QShortcut(QKeySequence(Qt.Key.Key_K), self, self._add_marker_at_playhead)
        QShortcut(QKeySequence(Qt.Key.Key_Comma), self, self._select_prev_marker_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Period), self, self._select_next_marker_shortcut)
        QShortcut(QKeySequence(Qt.Key.Key_N), self, self._toggle_note_rail)
        QShortcut(QKeySequence(Qt.Key.Key_D), self, self._toggle_draw_tool)

    def _fit_viewer_viewport(self) -> None:
        self._reset_viewer_plate_transform()
        self._apply_viewer_plate_geometry()

    def _fit_timeline_view(self) -> None:
        self._scrubber.reset_view()

    def _on_fit_shortcut(self) -> None:
        if self._text_editing_focused():
            return
        zone = self._footer_pointer_zone
        if zone == "video":
            self._fit_viewer_viewport()
            return
        if zone in ("track", "ruler", "handle_in", "handle_out", "range_move", "range_overlap", "range"):
            self._fit_timeline_view()
            return
        self._fit_viewer_viewport()
        self._fit_timeline_view()

    def _on_enter(self) -> None:
        """Enter key state machine for ranges.

        - Draft present: add range (highlight only)
        - Highlighted (not editing): enter edit mode
        - Editing: confirm edit (exit edit mode)
        """
        if self._text_editing_focused():
            return
        if self._range_edit_unlocked:
            self._confirm_range_edit()
            return
        if not self._ranges_tool_active():
            return
        if self._draft_in is not None and self._draft_out is not None:
            self._add_draft_range()
            return
        if self._active_range_id is not None:
            self._on_range_edit_requested(self._active_range_id)

    def _confirm_range_edit(self) -> None:
        if not self._range_edit_unlocked:
            return
        self._range_edit_unlocked = False
        self._range_edit_cancel_snapshot = None
        self._sync_range_ui()

    def _cancel_range_edit(self) -> None:
        if not self._range_edit_unlocked:
            return
        snap = self._range_edit_cancel_snapshot
        self._range_edit_cancel_snapshot = None
        if snap is not None:
            self._restore_range_snapshot(snap)
        self._range_edit_unlocked = False
        self._sync_range_ui()

    def apply_profile(self, context: PreviewContext) -> None:
        self._context = context
        self._profile_key = context.value
        self._tools_panel.apply_context(context)
        if hasattr(self, "_note_rail"):
            self._note_rail.apply_context(context)
            if context == PreviewContext.entity:
                self._note_rail.set_open(bool(getattr(self, "_note_rail_open_pref", False)))
        show_sync = context != PreviewContext.inbox
        self._btn_sync.setVisible(show_sync)
        self._btn_sync.setEnabled(show_sync)
        if self._entity_path is not None:
            self._sync_note_panel_context()
        self._sync_transport_tool_controls()

    def _sync_note_panel_context(self) -> None:
        panel = self._note_rail.panel()
        anchor = self._geometry_anchor
        workspace_root = getattr(anchor, "_workspace_root", None) if anchor is not None else None
        project_root = getattr(anchor, "_project_root", None) if anchor is not None else None
        display_name = self._entity_path.name if self._entity_path is not None else ""
        panel.set_context(
            self._entity_path,
            department_id=self._department_id,
            department_label=self._department_label,
            workspace_root=workspace_root,
            project_root=project_root,
            item_display_name=display_name,
        )
        self._update_note_frame_hint()
        self._sync_timeline_note_markers()

    def _sync_timeline_note_markers(self) -> None:
        if not hasattr(self, "_scrubber"):
            return
        if self._context != PreviewContext.entity or self._entity_path is None:
            self._scrubber.set_note_markers([])
            return
        panel = self._note_rail.panel()
        anchor = self._geometry_anchor
        workspace_root = getattr(anchor, "_workspace_root", None) if anchor is not None else None
        max_frame = max(0, self._total_frames() - 1)
        markers = build_timeline_note_markers(
            panel.entries(),
            workspace_root,
            widget_for_dpr=self,
            avatar_px=VideoPreviewScrubber._NOTE_AVATAR_INNER_PX,
            max_frame=max_frame,
        )
        self._scrubber.set_note_markers(markers)

    def _on_timeline_note_clicked(self, note_id: str) -> None:
        if self._context != PreviewContext.entity:
            return
        panel = self._note_rail.panel()
        entry = next((e for e in panel.entries() if e.id == note_id), None)
        if entry is None:
            return
        frame = parse_note_anchor_frame(entry.text, body_html=entry.body_html)
        if frame is not None:
            self._seek_frame(frame)
        if not self._note_rail_open():
            self._note_rail.set_open(True)
            self._sync_note_panel_context()
        panel._open_note_view(note_id)

    def _on_review_note_added(self) -> None:
        self._sync_timeline_note_markers()
        self.notes_changed.emit()

    def _restore_workspace_from_settings(self) -> None:
        ws_name = read_review_workspace(self._settings, profile=self._profile_key)
        try:
            ws = ReviewWorkspace(ws_name)
            if ws == ReviewWorkspace.review:
                ws = ReviewWorkspace.focus
        except ValueError:
            ws = ReviewWorkspace.tools
        self._tools_panel.set_workspace(ws)
        saved_mode_name = read_review_tool_mode(self._settings, profile=self._profile_key)
        legacy_note_mode = saved_mode_name == ReviewToolMode.note.value
        if ws == ReviewWorkspace.tools:
            try:
                mode = ReviewToolMode(saved_mode_name)
            except ValueError:
                mode = ReviewToolMode.ranges
            if mode == ReviewToolMode.note:
                mode = ReviewToolMode.ranges
            self._tools_panel.activate_tool_mode(mode)
        if self._context == PreviewContext.entity:
            rail_open = read_review_note_rail_open(self._settings, profile=self._profile_key)
            if legacy_note_mode:
                rail_open = True
            self._note_rail_open_pref = rail_open
            self._note_rail.set_open(rail_open)
        self._sync_tools_panel_button()
        QTimer.singleShot(0, self._sync_body_splitter_sizes)

    def _on_tools_workspace_changed(self, ws_name: str) -> None:
        if self._settings is not None:
            write_review_workspace(self._settings, self._profile_key, ws_name)
        self._sync_shell_corner_radius()
        self._sync_tools_panel_button()
        self._sync_body_splitter_sizes()
        self._schedule_side_panel_layout_persist()

    def _on_tools_mode_changed(self, mode_name: str) -> None:
        if self._settings is not None:
            if mode_name == ReviewToolMode.note.value:
                mode_name = ReviewToolMode.ranges.value
            write_review_tool_mode(self._settings, self._profile_key, mode_name)
        self._apply_timeline_list_mode()
        self._sync_range_ui()
        self._sync_draw_overlay_state()
        self._sync_transport_tool_controls()
        self._refresh_footer_hint()
        self._sync_scrubber_timeline_display()

    def _cycle_workspace(self) -> None:
        self._tools_panel.cycle_workspace()

    def _activate_tool(self, mode: ReviewToolMode) -> None:
        if mode == ReviewToolMode.note:
            self._toggle_note_rail()
            return
        if self._tools_panel.workspace() != ReviewWorkspace.tools:
            self._open_review_tools_panel(mode)
            return
        self._tools_panel.activate_tool_mode(mode)
        if mode == ReviewToolMode.draw:
            self._sync_draw_overlay_state()
        self._sync_transport_tool_controls()
        self._refresh_footer_hint()
        self._sync_scrubber_timeline_display()

    def _scrubber_mode_timeline_defaults(self) -> tuple[bool, bool, bool, bool, bool, bool]:
        """Mode defaults: show ranges, markers, draw keys, interact ranges, markers, draw keys."""
        mode = self._tools_panel.tool_mode()
        if mode == ReviewToolMode.markers:
            return (False, True, False, False, True, False)
        if mode == ReviewToolMode.draw:
            return (False, False, True, False, False, True)
        return (True, False, False, True, False, False)

    def _sync_scrubber_timeline_display(self) -> None:
        if not hasattr(self, "_scrubber"):
            return
        dr, dm, dk, ir, im, ik = self._scrubber_mode_timeline_defaults()
        show_ranges = dr or self._scrubber_display_force_ranges
        show_markers = dm or self._scrubber_display_force_markers
        show_keys = dk or self._scrubber_display_force_draw_keys
        self._scrubber.set_timeline_layers(
            show_ranges=show_ranges,
            show_markers=show_markers,
            show_draw_keyframes=show_keys,
            interact_ranges=ir,
            interact_markers=im,
            interact_draw_keyframes=ik,
        )
        self._scrubber.set_timeline_display_forces(
            force_ranges=self._scrubber_display_force_ranges,
            force_markers=self._scrubber_display_force_markers,
            force_draw_keys=self._scrubber_display_force_draw_keys,
            draw_keys_enabled=True,
        )

    def _on_scrubber_display_force_toggled(self, key: str, checked: bool) -> None:
        if key == "ranges":
            self._scrubber_display_force_ranges = bool(checked)
        elif key == "markers":
            self._scrubber_display_force_markers = bool(checked)
        elif key == "draw_keys":
            self._scrubber_display_force_draw_keys = bool(checked)
        self._sync_scrubber_timeline_display()

    def _show_timeline_menu(self) -> None:
        menu = MonosMenu(self)
        act_fit = menu.addAction("Fit timeline (Z)")
        act_focus = menu.addAction("Focus to selected range (F)")
        act_focus.setEnabled(self._active_range_id is not None)
        act_disp_ranges, act_disp_markers, act_disp_keys = self._scrubber._add_timeline_display_submenu(menu)
        position_popup_near_anchor(menu, self._btn_tl_menu)
        chosen = menu.exec(menu.mapToGlobal(QPoint(0, 0)))
        if self._scrubber._handle_timeline_display_choice(
            chosen, act_disp_ranges, act_disp_markers, act_disp_keys
        ):
            return
        if chosen == act_fit:
            self._scrubber.reset_view()
        elif chosen == act_focus:
            self._focus_timeline_range()

    def _note_rail_open(self) -> bool:
        return hasattr(self, "_note_rail") and self._note_rail.is_open()

    def _toggle_note_rail(self) -> None:
        if self._context != PreviewContext.entity:
            return
        self._note_rail.toggle()
        if self._note_rail.is_open():
            self._sync_note_panel_context()
        self._refresh_footer_hint()

    def _note_compose_focused(self) -> bool:
        if not self._note_rail_open():
            return False
        editor = self._note_rail.panel()._editor
        fw = self.focusWidget()
        if fw is None:
            return False
        if fw is editor or editor.isAncestorOf(fw):
            return True
        popup = editor._mention_popup
        return popup.isVisible() and (fw is popup or popup.isAncestorOf(fw))

    def _notes_tool_active(self) -> bool:
        return self._note_rail_open()

    def _sync_transport_tool_controls(self) -> None:
        if not hasattr(self, "_draw_transport"):
            return
        mode = self._tools_panel.tool_mode()
        ranges = mode == ReviewToolMode.ranges
        markers = mode == ReviewToolMode.markers
        draw = mode == ReviewToolMode.draw

        self._btn_in.setVisible(ranges)
        self._btn_out.setVisible(ranges)
        self._btn_add.setVisible(ranges)
        self._btn_add_marker.setVisible(markers)

        self._draw_transport.setVisible(draw)
        if draw and hasattr(self, "_draw_brush_strip"):
            self._draw_brush_strip.set_onion_enabled(self._onion_enabled)
            self._draw_brush_strip.set_onion_span(self._onion_span)

        show_export = ranges or markers
        self._btn_export.setVisible(show_export)
        if markers:
            self._btn_export.setText("Export PNG…")
            self._btn_export.setToolTip("Export marker contact sheet (PNG)")
            self._btn_export.setEnabled(len(self._markers) > 0)
        elif ranges:
            self._btn_export.setText("Export…")
            self._btn_export.setToolTip("Export marked ranges")
            self._btn_export.setEnabled(len(self._ranges) > 0)

    def _on_transport_export_clicked(self) -> None:
        if self._markers_tool_active():
            self._export_markers_png()
            return
        self._export()

    def _toggle_tools_workspace(self) -> None:
        if self._tools_panel.workspace() == ReviewWorkspace.tools:
            self._tools_panel.set_workspace(ReviewWorkspace.focus)
        else:
            self._open_review_tools_panel(self._tools_panel.tool_mode())

    def _sync_tools_panel_button(self) -> None:
        btn = getattr(self, "_btn_tools_panel", None)
        if btn is not None:
            btn.setChecked(self._tools_panel.workspace() == ReviewWorkspace.tools)

    def _range_list(self):
        return self._tools_panel.range_list_widget()

    def _marker_list(self):
        return self._tools_panel.marker_list_widget()

    def _ranges_tool_active(self) -> bool:
        return self._tools_panel.tool_mode() == ReviewToolMode.ranges

    def _markers_tool_active(self) -> bool:
        return self._tools_panel.tool_mode() == ReviewToolMode.markers

    def _draw_tool_active(self) -> bool:
        panel = getattr(self, "_tools_panel", None)
        if panel is None:
            return False
        return panel.tool_mode() == ReviewToolMode.draw

    def _apply_timeline_list_mode(self) -> None:
        if self._tools_panel.tool_mode() == ReviewToolMode.markers:
            self._scrubber.set_timeline_list_mode("markers")
        else:
            self._scrubber.set_timeline_list_mode("ranges")

    def _active_marker(self) -> VideoReviewMarker | None:
        if not self._active_marker_id:
            return None
        return next((m for m in self._markers if m.id == self._active_marker_id), None)

    def _active_marker_label(self) -> str:
        m = self._active_marker()
        return (m.label if m else "") or ""

    def _active_range_label(self) -> str:
        rng = self._active_range()
        return (rng.label if rng else "") or ""

    def _review_sources_fps(self) -> int:
        return max(1, min(60, int(round(self._fps()))))

    def _refresh_entity_sources(self) -> None:
        if self._context != PreviewContext.entity or self._work_path is None:
            self._entity_sources = []
            return
        self._entity_sources = list_entity_review_sources(
            work_path=self._work_path,
            work_file_path=self._work_file_path,
            fps=self._review_sources_fps(),
            context=self._context,
            entity_path=self._entity_path,
            department_id=self._department_id,
            department_label=self._department_label,
        )

    def _title_picker_has_choices(self) -> bool:
        if self._context == PreviewContext.entity and self._work_path is not None:
            return True
        if len(self._entity_sources) > 1:
            return True
        return not self._is_sequence_mode() and len(self._paths) > 1

    def _sync_title_btn_enabled(self) -> None:
        if self._title_picker_has_choices():
            self._title_btn.setEnabled(True)
        elif self._is_sequence_mode():
            self._title_btn.setEnabled(bool(self._sequence_folder))
        else:
            self._title_btn.setEnabled(self._path is not None)

    def _entity_review_title(self) -> str:
        parts: list[str] = []
        if self._entity_path is not None:
            parts.append(self._entity_path.name)
        dept = (self._department_label or "").strip()
        if not dept and self._department_id:
            dept = self._department_id.replace("_", " ").title()
        if dept:
            parts.append(dept)
        src = (self._source_label or "").strip()
        if src:
            parts.append(src)
        elif self._is_sequence_mode() and self._sequence_folder is not None:
            parts.append(self._sequence_folder.name)
        elif self._path is not None:
            parts.append(self._path.name)
        return " · ".join(parts)

    def _update_top_bar(self) -> None:
        if self._context == PreviewContext.entity and self._entity_path is not None:
            n_sources = len(self._entity_sources)
            self._file_counter.setText("")
            self._file_counter.setVisible(False)
            tip = self._entity_review_title()
            if n_sources > 1:
                tip += "\nClick to switch review source"
            elif not self._is_sequence_mode() and len(self._paths) > 1:
                tip += "\nClick to choose another video in this folder"
            self._title_btn.setToolTip(tip)
            self._refresh_title_elide()
            self._sync_title_btn_enabled()
            QTimer.singleShot(0, self._refresh_title_elide)
            return
        if self._is_sequence_mode():
            if self._sequence_folder is None:
                self._file_counter.setText("")
                self._title_btn.setText("")
                self._title_btn.setToolTip("")
                return
            n = len(self._sequence_frames)
            self._file_counter.setText(f"{n} fr" if n else "")
            self._file_counter.setVisible(bool(n))
            tip = str(self._sequence_folder)
            self._title_btn.setToolTip(tip)
            self._refresh_title_elide()
            QTimer.singleShot(0, self._refresh_title_elide)
            return
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
        close_reserve = _PREVIEW_CLOSE_BTN + _PREVIEW_CLOSE_INSET
        return max(48, bar_w - counter_w - close_reserve - 12)

    def _refresh_title_elide(self) -> None:
        if self._context == PreviewContext.entity and self._entity_path is not None:
            avail = self._title_button_available_width()
            text = self._title_btn.fontMetrics().elidedText(
                self._entity_review_title(),
                Qt.TextElideMode.ElideMiddle,
                avail,
            )
            self._title_btn.setText(text)
            return
        if self._is_sequence_mode():
            if self._sequence_folder is None:
                self._title_btn.setText("")
                return
            avail = self._title_button_available_width()
            text = self._title_btn.fontMetrics().elidedText(
                self._sequence_folder.name,
                Qt.TextElideMode.ElideMiddle,
                avail,
            )
            self._title_btn.setText(text)
            return
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
        self._update_footer_meta()
        self._refresh_footer_hint()

    def _update_footer_meta(self) -> None:
        if self._is_sequence_mode():
            if self._sequence_folder is None:
                self._footer_label.setText("")
                return
            n = len(self._sequence_frames)
            parts = [
                f"{n} fr",
                f"{self._fps():.3f} fps",
                "sequence",
            ]
            if self._status_log:
                parts.append(self._status_log)
            self._footer_label.setText(" · ".join(parts))
            return
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
        proxy_tag = self._proxy_footer_tag()
        if proxy_tag:
            parts.append(proxy_tag)
        self._footer_label.setText(" · ".join(parts))

    def _on_scrubber_footer_context(self, zone: str) -> None:
        self._set_footer_pointer_zone(zone)

    def _set_footer_pointer_zone(self, zone: str) -> None:
        if zone == self._footer_pointer_zone:
            return
        self._footer_pointer_zone = zone
        self._refresh_footer_hint()

    def _refresh_footer_hint(self) -> None:
        if not hasattr(self, "_footer_hint"):
            return
        if self._is_sequence_mode():
            if self._sequence_folder is None:
                self._footer_hint.set_parts([])
                return
            self._footer_hint.set_parts(self._footer_hint_parts())
            return
        if self._path is None or self._info is None:
            self._footer_hint.set_parts([])
            return
        self._footer_hint.set_parts(self._footer_hint_parts())

    def _footer_range_cycle_hint(self) -> str | None:
        if not self._ranges_tool_active():
            return None
        if len(self._ranges) < 2:
            return None
        return "[ / ] — Prev/Next range"

    def _footer_marker_hint(self) -> str | None:
        if not self._markers_tool_active():
            return None
        if len(self._markers) < 2:
            return None
        return ", / . — Prev/Next marker"

    def _footer_video_navigation_hints(self) -> list[str]:
        parts = ["Wheel — Zoom", "MMB drag — Scrub", "Alt+MMB — Pan"]
        if self._viewer_is_zoomed():
            parts.append("Z — Fit")
        return parts

    def _footer_timeline_navigation_hints(self, zone: str) -> list[str]:
        parts = ["LMB drag — Scrub", "LMB — Select", "RMB — Menu"]
        if zone == "ruler":
            parts += ["Alt+MMB — Pan", "Alt+Wheel — Zoom", "Wheel — Pan"]
        if self._scrubber.is_zoomed():
            parts.append("Z — Fit")
        return parts

    def _footer_hints_proxy(self) -> list[str]:
        parts = ["Space — Play/Pause", "Scrub/play use proxy when cached"]
        mode = self._tools_panel.tool_mode()
        if mode == ReviewToolMode.draw:
            parts.append("D — Exit draw")
            return parts
        if mode == ReviewToolMode.markers:
            parts.append("K — Add marker")
            return parts
        if self._note_rail_open():
            parts.append("N — Close notes")
            return parts
        has_draft = self._draft_in is not None or self._draft_out is not None
        has_sel = self._active_range_id is not None
        editing = self._range_edit_unlocked and has_sel
        if editing:
            parts += ["I/O — Set In/Out", "Enter — Confirm", "Esc — Cancel"]
        elif has_draft:
            parts += ["Enter — Add & highlight range", "Esc — Clear draft"]
        elif has_sel:
            parts += ["Enter — Edit range", "Dbl-click outside — Deselect", "Esc — Deselect"]
        else:
            parts.append("Esc — Turn off proxy")
        return parts

    def _footer_hints_draw(self, zone: str) -> list[str]:
        if zone == "video":
            parts = ["LMB — Draw", "RMB — Tool menu", "E — Eraser"]
            parts += self._footer_video_navigation_hints()
            parts += ["O — Onion", "D — Exit draw", "Ctrl+Z — Undo stroke"]
            return parts
        if zone in ("handle_in", "handle_out", "range_move", "range_overlap", "range"):
            parts = ["LMB — Select keyframe", "LMB drag — Scrub", "D — Exit draw"]
            if self._can_edit_draw_keyframe() or self._draw_keyframe_edit_unlocked:
                parts.insert(1, "E — Edit keyframe")
            return parts
        if zone in ("track", "ruler"):
            parts = ["LMB — Select keyframe", "LMB drag — Scrub"]
            if self._can_edit_draw_keyframe() or self._draw_keyframe_edit_unlocked:
                parts.append("E — Edit keyframe")
            if zone == "ruler":
                parts += ["Alt+MMB — Pan", "Alt+Wheel — Zoom", "Wheel — Pan"]
            if self._scrubber.is_zoomed():
                parts.append("Z — Fit")
            parts.append("D — Exit draw")
            return parts
        return ["Space — Play/Pause", "D — Exit draw"]

    def _footer_hints_markers(self, zone: str) -> list[str]:
        marker_jump = self._footer_marker_hint()
        if zone == "video":
            parts = self._footer_video_navigation_hints()
            parts += ["K — Add marker", "Del — Delete marker"]
            if marker_jump:
                parts.append(marker_jump)
            return parts
        if zone in ("track", "ruler"):
            parts = ["K — Add marker", "LMB — Select marker", "Del — Delete", "LMB drag — Scrub"]
            if zone == "ruler":
                parts += ["Alt+MMB — Pan", "Alt+Wheel — Zoom", "Wheel — Pan"]
            if self._scrubber.is_zoomed():
                parts.append("Z — Fit")
            if marker_jump:
                parts.append(marker_jump)
            return parts
        parts = ["Space — Play/Pause", "K — Add marker"]
        if marker_jump:
            parts.append(marker_jump)
        return parts

    def _footer_hints_note(self, zone: str) -> list[str]:
        parts: list[str] = []
        if zone == "video":
            parts = self._footer_video_navigation_hints()
        elif zone in ("track", "ruler"):
            parts = self._footer_timeline_navigation_hints(zone)
        else:
            parts = ["Space — Play/Pause"]
        parts += ["Ctrl+Enter — Add note", "Click range — Insert", "Shift+click — Keep focus", "N — Close notes"]
        return parts

    def _footer_hints_ranges(self, zone: str) -> list[str]:
        has_draft = self._draft_in is not None or self._draft_out is not None
        has_sel = self._active_range_id is not None
        editing = self._range_edit_unlocked and has_sel
        range_cycle = self._footer_range_cycle_hint()

        if zone == "video":
            parts = self._footer_video_navigation_hints()
            if editing:
                parts += ["I/O — Set on range", "Enter — Confirm", "Esc — Cancel"]
            elif has_draft:
                parts += ["Enter — Add & highlight range", "Esc — Clear draft"]
            elif has_sel:
                parts += ["Enter — Edit range", "Esc — Deselect"]
            else:
                parts += ["I — Mark In", "O — Mark Out"]
            if range_cycle:
                parts.append(range_cycle)
            return parts

        if zone == "handle_in":
            return ["Drag — Trim In", "Enter — Confirm", "Esc — Cancel"]
        if zone == "handle_out":
            return ["Drag — Trim Out", "Enter — Confirm", "Esc — Cancel"]
        if zone == "range_move":
            return ["Drag — Move range", "Enter — Confirm", "Esc — Cancel"]

        if zone == "range_overlap":
            return [
                "LMB — Cycle overlapping",
                "E — Edit",
                "Del — Delete",
                "Esc — Deselect",
            ]

        if zone == "range":
            if editing:
                parts = ["I/O — Set In/Out", "Del — Delete", "Enter — Confirm", "Esc — Cancel"]
            elif has_sel:
                parts = ["Enter — Edit range", "Del — Delete", "Dbl-click outside — Deselect", "Esc — Deselect"]
            else:
                parts = ["LMB — Select", "Dbl-click — Edit", "E — Edit"]
            if range_cycle:
                parts.append(range_cycle)
            return parts

        if zone in ("track", "ruler"):
            parts = self._footer_timeline_navigation_hints(zone)
            if has_draft:
                parts += ["Enter — Add & highlight range", "Esc — Clear draft"]
            elif editing:
                parts += ["I/O — Set on range", "Enter — Confirm", "Esc — Cancel"]
            else:
                if has_sel:
                    parts.append("Enter — Edit range")
                else:
                    parts.append("I/O — Mark In/Out")
            if has_sel and not editing:
                parts.append("Dbl-click outside — Deselect")
            if range_cycle:
                parts.append(range_cycle)
            return parts

        parts = ["Space — Play/Pause"]
        if has_sel and not editing:
            parts += ["Esc — Deselect", "Enter — Edit range"]
        elif has_draft:
            parts += ["Enter — Add & highlight range", "Esc — Clear draft"]
        elif editing:
            parts += ["Enter — Confirm", "Esc — Cancel"]
        else:
            parts += ["I/O — Draft range", "E — Edit range"]
        if range_cycle:
            parts.append(range_cycle)
        return parts

    def _footer_hint_parts(self) -> list[str]:
        zone = self._footer_pointer_zone

        if self._fullscreen:
            return ["Esc — exit fullscreen", "Bottom edge — timeline", "Right edge — tools"]

        if self._proxy_enabled:
            return self._footer_hints_proxy()

        mode = self._tools_panel.tool_mode()
        if mode == ReviewToolMode.draw:
            parts = self._footer_hints_draw(zone)
        elif mode == ReviewToolMode.markers:
            parts = self._footer_hints_markers(zone)
        else:
            parts = self._footer_hints_ranges(zone)
        if self._note_rail_open():
            note_bits = self._footer_hints_note(zone)
            parts = note_bits[:2] + parts
        return parts

    def _show_title_picker_menu(self) -> None:
        if self._context == PreviewContext.entity and self._work_path is not None:
            self._refresh_entity_sources()
            if len(self._entity_sources) > 1:
                self._show_entity_source_picker(self._entity_sources)
                return
        if not self._is_sequence_mode() and len(self._paths) > 1:
            self._show_video_picker_menu()

    def _show_entity_source_picker(self, sources: list[EntityReviewSource]) -> None:
        menu = MonosMenu(self)
        current_key = str(self._media_key()).casefold() if self._media_key() is not None else ""
        for src in sources:
            act = menu.addAction(src.label)
            act.setCheckable(True)
            act.setChecked(str(src.request.media_key).casefold() == current_key)
            act.triggered.connect(lambda checked=False, req=src.request: self.load_media(req))
        position_popup_near_anchor(menu, self._title_btn)
        menu.popup(menu.mapToGlobal(QPoint(0, 0)))

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
        if fw is None:
            return False
        if isinstance(fw, (QLineEdit, QTextEdit)):
            return True
        if self._notes_tool_active() and hasattr(self, "_note_rail"):
            editor = self._note_rail.panel()._editor
            popup = editor._mention_popup
            if popup.isVisible() and (fw is popup or popup.isAncestorOf(fw)):
                return True
        return False

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
        if self._text_editing_focused():
            return
        if self._draw_tool_active():
            self._undo_draw_stroke()
            return
        if not self._ranges_tool_active() or not self._range_undo_stack:
            return
        self._range_redo_stack.append(self._capture_range_snapshot())
        self._restore_range_snapshot(self._range_undo_stack.pop())

    def _redo_range_edit(self) -> None:
        if self._text_editing_focused() or not self._ranges_tool_active() or not self._range_redo_stack:
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
        panel = self._note_rail.panel()
        if panel.compose_active():
            editor = panel._editor
            if self._note_compose_focused() and editor._mention_popup.isVisible():
                editor._hide_mention_popup()
                return
            panel.cancel_compose()
            return
        if self._note_compose_focused():
            editor = self._note_rail.panel()._editor
            if editor._mention_popup.isVisible():
                editor._hide_mention_popup()
                return
            editor.clearFocus()
            if hasattr(self, "_surface_wrap"):
                self._surface_wrap.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if self._text_editing_focused():
            fw = self.focusWidget()
            if fw is not None:
                fw.clearFocus()
            return
        popup = self._draw_quick_popup
        if popup is not None:
            try:
                popup.close()
            except RuntimeError:
                pass
            self._draw_quick_popup = None
            return
        if self._draw_keyframe_edit_unlocked:
            self._exit_draw_keyframe_edit()
            return
        if self._range_edit_unlocked:
            self._cancel_range_edit()
            return
        if self._markers_tool_active() and self._active_marker_id is not None:
            self._on_marker_deselected()
            return
        if self._ranges_tool_active() and self._active_range_id is not None:
            self._scrubber.clear_overlap_cycle()
            self._on_range_deselected()
            return
        if self._proxy_enabled:
            self._chk_proxy.setChecked(False)
            return
        if self._ranges_tool_active() and (self._draft_in is not None or self._draft_out is not None):
            self._draft_in = None
            self._draft_out = None
            self._sync_range_ui()
            return
        if self._fullscreen:
            self._toggle_fullscreen()
            return
        if self._viewer_is_zoomed():
            self._fit_viewer_viewport()
            return
        if self._scrubber.is_zoomed():
            self._fit_timeline_view()
            return

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self._fullscreen = False
            self._teardown_fullscreen_chrome_tracking()
            self._set_fullscreen_bottom_chrome(False, force=True)
            self._set_fullscreen_right_chrome(False, force=True)
            self.showNormal()
            self._apply_dialog_content_inset()
            self._top_bar.show()
            self._timeline_block.show()
            self._viewer_divider.show()
            self._transport.show()
            self._footer.show()
            self._refresh_footer_hint()
            self._tools_panel.show()
            self._note_rail.show()
            self._restore_locked_size()
            self._sync_body_splitter_sizes()
        else:
            self._fullscreen = True
            self._fs_bottom_revealed = False
            self._fs_right_revealed = False
            self._top_bar.hide()
            self._timeline_block.hide()
            self._viewer_divider.hide()
            self._transport.hide()
            self._footer.hide()
            self._tools_panel.hide()
            self._note_rail.hide()
            self._apply_dialog_content_inset()
            self.showFullScreen()
            self._setup_fullscreen_chrome_tracking()
        self._position_hud()
        self._position_close_btn()
        self._raise_video_overlays()

    def _resolve_overlay_host(self) -> QWidget | None:
        anchor = getattr(self, "_geometry_anchor", None)
        if anchor is not None:
            win = anchor.window()
            return win if win is not None else anchor
        return super()._resolve_overlay_host()

    def _setup_fullscreen_chrome_tracking(self) -> None:
        app = QApplication.instance()
        if app is not None and not self._fs_app_filter_installed:
            app.installEventFilter(self)
            self._fs_app_filter_installed = True

    def _teardown_fullscreen_chrome_tracking(self) -> None:
        self._fs_chrome_hide_timer.stop()
        app = QApplication.instance()
        if app is not None and self._fs_app_filter_installed:
            app.removeEventFilter(self)
            self._fs_app_filter_installed = False

    def _global_point_over_widget(self, gpos: QPoint, widget: QWidget) -> bool:
        if not widget.isVisible():
            return False
        top_left = widget.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, widget.size()).contains(gpos)

    def _cursor_over_fullscreen_bottom_chrome(self, gpos: QPoint) -> bool:
        return any(
            self._global_point_over_widget(gpos, w)
            for w in (self._timeline_block, self._transport)
        )

    def _cursor_over_fullscreen_right_chrome(self, gpos: QPoint) -> bool:
        return self._global_point_over_widget(gpos, self._tools_panel)

    def _update_fullscreen_chrome(self, gpos: QPoint) -> None:
        if not self._fullscreen:
            return
        local = self.mapFromGlobal(gpos)
        w, h = max(1, self.width()), max(1, self.height())
        edge = _FULLSCREEN_EDGE_PX
        near_bottom = local.y() >= h - edge
        near_right = local.x() >= w - edge
        if not near_bottom:
            near_bottom = self._cursor_over_fullscreen_bottom_chrome(gpos)
        if not near_right:
            near_right = self._cursor_over_fullscreen_right_chrome(gpos)
        self._set_fullscreen_bottom_chrome(near_bottom)
        self._set_fullscreen_right_chrome(near_right)
        if near_bottom or near_right:
            self._fs_chrome_hide_timer.stop()
        else:
            if not self._fs_chrome_hide_timer.isActive():
                self._fs_chrome_hide_timer.start(_FULLSCREEN_CHROME_HIDE_MS)

    def _on_fullscreen_chrome_hide_timeout(self) -> None:
        if not self._fullscreen:
            return
        gpos = QCursor.pos()
        local = self.mapFromGlobal(gpos)
        edge = _FULLSCREEN_EDGE_PX
        if local.y() >= self.height() - edge or local.x() >= self.width() - edge:
            return
        if self._cursor_over_fullscreen_bottom_chrome(gpos) or self._cursor_over_fullscreen_right_chrome(gpos):
            return
        self._set_fullscreen_bottom_chrome(False)
        self._set_fullscreen_right_chrome(False)

    def _set_fullscreen_bottom_chrome(self, visible: bool, *, force: bool = False) -> None:
        if not force and (not self._fullscreen or visible == self._fs_bottom_revealed):
            return
        self._fs_bottom_revealed = visible
        if visible:
            self._timeline_block.show()
            self._viewer_divider.show()
            self._transport.show()
        elif self._fullscreen:
            self._timeline_block.hide()
            self._viewer_divider.hide()
            self._transport.hide()
        self._position_hud()
        if self._video_attached:
            self._sync_video_backend()

    def _set_fullscreen_right_chrome(self, visible: bool, *, force: bool = False) -> None:
        if not force and (not self._fullscreen or visible == self._fs_right_revealed):
            return
        self._fs_right_revealed = visible
        if visible:
            if self._tools_panel.workspace() != ReviewWorkspace.tools:
                self._tools_panel.set_workspace(ReviewWorkspace.tools)
            self._tools_panel.show()
        elif self._fullscreen:
            self._tools_panel.hide()
        self._raise_video_overlays()

    def _main_bounds(self) -> QRect:
        return main_window_bounds(self._geometry_anchor)

    def _restore_locked_size(self) -> None:
        if self._locked_size is None:
            return
        bounds = self._main_bounds()
        w, h = self._locked_size.width(), self._locked_size.height()
        x = bounds.x() + max(0, (bounds.width() - w) // 2)
        y = bounds.y() + max(0, (bounds.height() - h) // 2)
        self.setGeometry(x, y, w, h)
        self.setFixedSize(self._locked_size)

    def _apply_dialog_geometry_once(self) -> None:
        if self._geometry_applied:
            return
        self._geometry_applied = True
        bounds = self._main_bounds()
        self._locked_size = apply_dialog_geometry(
            None,
            "",
            self,
            bounds=bounds,
            default_fraction=self._review_request.geometry_fraction,
            min_size=QSize(self.minimumWidth(), self.minimumHeight()),
            lock_size=True,
            margin=4,
        )

    def set_pending_time_anchor(self, href: str | None) -> None:
        h = (href or "").strip()
        self._pending_time_anchor = h or None

    def apply_time_anchor(self, href: str) -> bool:
        anchor = parse_time_href(href)
        if anchor is None:
            return False
        self._on_note_time_anchor(href)
        return True

    def _consume_pending_time_anchor(self) -> bool:
        href = self._pending_time_anchor
        if not href:
            return False
        self._pending_time_anchor = None
        return self.apply_time_anchor(href)

    def _prime_playback(self) -> None:
        if self._closing or not self.isVisible():
            return
        if self._is_sequence_mode():
            return
        if self._playback_primed:
            return
        self._playback_primed = True
        if self._consume_pending_time_anchor():
            self._sync_playback_loop()
            return
        frame = self._restore_frame if self._restore_frame is not None else 0
        self._restore_frame = None
        sec = frame / max(1e-6, self._fps())
        self._backend.prime_for_scrub(initial_sec=sec)
        self._seek_frame(frame)
        self._sync_playback_loop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._border_overlay is not None:
            self._border_overlay.hide()
        self._install_app_event_filter()
        self._apply_dialog_geometry_once()
        self._update_scrub_seek_interval()
        QTimer.singleShot(0, self._deferred_video_attach)
        QTimer.singleShot(0, self._prime_playback)
        self._refresh_title_elide()
        QTimer.singleShot(0, self._position_close_btn)

    def _position_close_btn(self) -> None:
        if not getattr(self, "_btn_close", None):
            return
        top_inset = 0 if self._fullscreen else _DIALOG_BORDER_INSET
        x = self.width() - self._btn_close.width() - _PREVIEW_CLOSE_INSET - top_inset
        if self._fullscreen:
            y = _PREVIEW_CLOSE_INSET
        else:
            y = top_inset + max(0, (_PREVIEW_TOPBAR_H - self._btn_close.height()) // 2)
        self._btn_close.move(x, y)
        self._btn_close.show()
        self.raise_border_overlay()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._is_sequence_mode() and self._seq_backend is not None:
            self._seq_backend.resize_display()
        elif self._video_attached:
            self._backend.layout_video()
        self._position_hud()
        self._position_proxy_overlay()
        self._position_close_btn()
        self._refresh_title_elide()
        self._sync_viewport_overlay_geometry()
        self._raise_video_overlays()
        self.raise_border_overlay()

    def _position_proxy_overlay(self) -> None:
        overlay = getattr(self, "_proxy_build_overlay", None)
        if overlay is None or not self._viewer:
            return
        if not overlay.isVisible():
            return
        area = self._video_overlay_rect()
        overlay.adjustSize()
        w = min(320, max(240, area.width() - 32))
        overlay.setFixedWidth(w)
        overlay.move(
            area.left() + (area.width() - w) // 2,
            area.top() + area.height() // 2 - overlay.height() // 2,
        )

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
        self._position_draw_brush_strip()

    def _position_draw_brush_strip(self) -> None:
        strip = getattr(self, "_draw_brush_strip", None)
        if strip is None or not self._viewer:
            return
        if not strip.isVisible():
            return
        strip.adjustSize()
        area = self._video_overlay_rect()
        m = 8
        strip.move(area.left() + m, area.top() + m)
        strip.raise_()

    def _onion_decode_max_side(self) -> int:
        if not hasattr(self, "_surface"):
            return 960
        w = max(1, self._surface.width())
        h = max(1, self._surface.height())
        dpr = max(1.0, float(self._surface.devicePixelRatioF()))
        return max(480, min(1920, int(max(w, h) * dpr)))

    def _on_onion_plates_ready(self, token: int, prev_pix: object, next_pix: object) -> None:
        # Plate onion disabled — draw stroke onion uses ReviewDrawOverlay only.
        return

    def _raise_video_overlays(self) -> None:
        self._raise_video_chrome_overlays()

    def _shutdown_embedded_video(self) -> None:
        if self._is_sequence_mode():
            return
        try:
            self._backend.stop()
        except Exception:
            pass
        surface = getattr(self, "_surface", None)
        if surface is not None:
            for child in surface.findChildren(QWidget):
                if child is surface:
                    continue
                child.hide()
                child.setParent(None)
                child.deleteLater()
            _hide_native_qt_window(surface)
        wrap = getattr(self, "_surface_wrap", None)
        if wrap is not None:
            wrap.hide()
        viewer = getattr(self, "_viewer", None)
        if viewer is not None:
            viewer.hide()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        self._remove_app_event_filter()
        self._viewer_wheel_coalesce.stop()
        self._hide_overlay()
        self._teardown_fullscreen_chrome_tracking()
        self._cancel_proxy_build()
        self._loop_timer.stop()
        self._scrub_seek_timer.stop()
        self._hover_debounce.stop()
        self._onion_refresh_timer.stop()
        if getattr(self, "_onion_layer", None) is not None:
            self._onion_layer.clear_ghosts()
        self._hide_hover_preview()
        if getattr(self, "_hover_label", None) is not None:
            self._hover_label.close()
        self._session_persist_timer.stop()
        self._persist_ranges_local()
        self._persist_markers_local()
        self._persist_draw_local()
        self._persist_preview_session()
        if self._is_sequence_mode():
            self._release_sequence_backend()
        if self._settings is not None:
            write_review_workspace(self._settings, self._profile_key, self._tools_panel.workspace().value)
            write_review_tool_mode(self._settings, self._profile_key, self._tools_panel.tool_mode().value)
            self._persist_side_panel_layout()
            write_video_preview_precise_scrub_drag(self._settings, self._precise_scrub_drag())
            write_video_preview_time_display(self._settings, self._time_display_mode)
            write_video_preview_playback_speed(self._settings, self._speed)
            write_video_preview_volume(self._settings, self._volume)
            write_video_preview_loop(self._settings, self._loop_playback)
        self.hide()
        self._shutdown_embedded_video()
        _hide_native_qt_window(self)
        self._backend.release()
        self._video_attached = False
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        super().closeEvent(event)

    def release_player(self) -> None:
        """Stop playback and release handles (before file move)."""
        self._persist_ranges_local()
        self._persist_markers_local()
        self._persist_draw_local()
        self._persist_preview_session()
        self._loop_timer.stop()
        if self._is_sequence_mode():
            self._release_sequence_backend()
            return
        self._backend.stop()
        self._backend.release()

    def current_path(self) -> Path | None:
        return self._path

    def _load_file(self, path: Path) -> None:
        self._media_kind = ReviewMediaKind.video
        self._set_surface_native_for_mode()
        self._ensure_video_backend()
        self._release_sequence_backend()
        if self._path and self._path != path:
            self._persist_ranges_local()
            self._persist_markers_local()
            self._persist_preview_session()
        self._path = path
        self._status_log = ""
        self._hover_key_frames = []
        self._reset_viewer_plate_transform()
        self._info = probe_video(path)
        self._ranges = []
        self._published_ranges = []
        self._markers = []
        self._published_markers = []
        self._draw_layers = []
        self._published_draw_layers = []
        self._active_keyframe_frame = None
        self._active_layer_id = None
        self._active_marker_id = None
        self._clear_range_undo_stacks()
        self._active_range_id = None
        self._range_edit_unlocked = False
        self._draft_in = None
        self._draft_out = None
        parent_hint = path.parent.name
        self.setWindowTitle(f"{path.name} · {parent_hint}")
        if self._info is None:
            self._hud.setText("Could not probe video")
            self._update_top_bar()
            self._update_footer()
            return
        self._range_list().set_fps(self._info.fps)
        self._scrubber.set_frame_count(self._info.frame_count, refit_view=True)
        self._scrubber.clear_overlap_cycle()
        published, working, _from_local = load_video_ranges_for_preview(
            path,
            total_frames=self._info.frame_count,
        )
        self._published_ranges = published
        self._ranges = working
        pub_m, work_m, _from_local_m = load_video_markers_for_preview(
            path,
            total_frames=self._info.frame_count,
        )
        self._published_markers = pub_m
        self._markers = work_m
        if self._markers and self._active_marker_id is None:
            self._active_marker_id = self._markers[0].id
        self._load_draw_keyframes_from_sidecar()
        self._playback_primed = False
        saved_frame = load_video_preview_session_local_draft(
            path,
            total_frames=self._info.frame_count,
        )
        self._restore_frame = saved_frame
        start_frame = saved_frame if saved_frame is not None else 0
        start_sec = start_frame / max(1e-6, self._info.fps)
        self._playback_path = None
        self._full_proxy_ready = False
        self._full_proxy_manifest = None
        self._range_proxy_manifest = None
        self._cancel_proxy_build()
        self._backend.load(path, start_sec=start_sec)
        self._playback_path = path
        self._backend.set_volume(0 if self._volume_muted else self._volume)
        self._backend.set_speed(self._speed)
        self._backend.configure_position_poll(self._info.fps)
        if self.isVisible():
            QTimer.singleShot(80, self._prime_playback)
        self._sync_range_ui()
        self._update_top_bar()
        self._update_footer()
        if saved_frame is not None:
            sec = saved_frame / max(1e-6, self._info.fps)
            self._scrubber.set_position_frame(saved_frame)
            self._update_position_display(sec)
        else:
            self._update_position_display(0.0)
        self._sync_proxy_state()
        self._start_keyframe_probe()

    def _start_keyframe_probe(self) -> None:
        if self._is_sequence_mode():
            return
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
        if self._is_sequence_mode() and self._seq_backend is not None:
            return self._seq_backend.fps()
        return self._info.fps if self._info else 24.0

    def _total_frames(self) -> int:
        if self._is_sequence_mode():
            return max(1, len(self._sequence_frames))
        return self._info.frame_count if self._info else 1

    def _current_frame(self) -> int:
        return int(self._scrubber.value())

    def _schedule_preview_session_persist(self) -> None:
        if self._media_key() is None:
            return
        if not self._is_sequence_mode() and self._info is None:
            return
        self._session_persist_timer.start()

    def _on_time_display_changed(self, _index: int) -> None:
        mode = self._cmb_time_display.currentData()
        if not isinstance(mode, str) or mode == self._time_display_mode:
            return
        self._time_display_mode = mode  # type: ignore[assignment]
        self._scrubber.set_time_display_mode(self._time_display_mode)
        panel = self._range_list()
        panel.set_display_mode(self._time_display_mode)
        panel.refresh_display()
        self._sync_position_controls_visibility()
        frame = self._current_frame()
        self._update_hud(frame)
        self._update_position_display(self._backend.position())
        self._update_note_frame_hint(frame)
        if self._settings is not None:
            write_video_preview_time_display(self._settings, self._time_display_mode)

    def _sync_range_ui(self) -> None:
        panel = self._range_list()
        panel.set_published_ranges(self._published_ranges)
        panel.set_display_mode(self._time_display_mode)
        panel.set_ranges(self._ranges, active_id=self._active_range_id)
        panel.set_draft_hint(self._draft_in, self._draft_out)
        marker_panel = self._marker_list()
        marker_panel.set_fps(self._fps())
        marker_panel.set_display_mode(self._time_display_mode)
        marker_panel.set_published_markers(self._published_markers)
        marker_panel.set_markers(self._markers, active_id=self._active_marker_id)
        if self._tools_panel.tool_mode() == ReviewToolMode.markers:
            self._tools_panel.set_active_marker_id(
                self._active_marker_id,
                label=self._active_marker_label(),
            )
        else:
            self._tools_panel.set_active_range_id(
                self._active_range_id if self._range_edit_unlocked else None,
                label=self._active_range_label() if self._range_edit_unlocked else "",
            )
        self._apply_timeline_list_mode()
        self._scrubber.set_fps(self._fps())
        self._scrubber.set_time_display_mode(self._time_display_mode)
        edit_id = self._active_range_id if self._range_edit_unlocked else None
        self._scrubber.set_ranges(
            self._ranges,
            highlight_id=self._active_range_id,
            edit_id=edit_id,
        )
        self._scrubber.set_markers(
            self._markers,
            highlight_id=self._active_marker_id,
        )
        self._scrubber.set_draft(self._draft_in, self._draft_out)
        self._sync_scrubber_timeline_display()
        if hasattr(self, "_draw_overlay") and self._draw_layers:
            self._sync_draw_playhead()
        has_selection = self._active_range_id is not None
        can_edit = self._range_edit_unlocked and has_selection
        in_tip = "Set In on range (I)" if can_edit else "Mark draft In for new range (I)"
        out_tip = "Set Out on range (O)" if can_edit else "Mark draft Out for new range (O)"
        self._btn_in.setToolTip(in_tip)
        self._btn_out.setToolTip(out_tip)
        self._update_sync_button()
        self._sync_transport_tool_controls()
        self._refresh_footer_hint()
        self._sync_playback_loop()

    def _update_note_frame_hint(self, frame: int | None = None) -> None:
        if self._context != PreviewContext.entity:
            return
        f = self._current_frame() if frame is None else int(frame)
        panel = self._note_rail.panel()
        panel.set_playhead(f, self._fps())

    def _update_sync_button(self) -> None:
        ranges_dirty = not ranges_content_equal(self._ranges, self._published_ranges)
        markers_dirty = not markers_content_equal(self._markers, self._published_markers)
        draw_dirty = not layers_content_equal(self._draw_layers, self._published_draw_layers)
        dirty = ranges_dirty or markers_dirty or draw_dirty
        self._btn_sync.setEnabled(dirty)
        if dirty:
            tips: list[str] = []
            if ranges_dirty:
                tips.append("ranges")
            if markers_dirty:
                tips.append("markers")
            if draw_dirty:
                tips.append("draw")
            self._btn_sync.setToolTip(f"Sync local {' and '.join(tips)} to project sidecar")
        else:
            self._btn_sync.setToolTip("All ranges, markers, and draw clips synced with project")

    def _update_hud(self, frame: int) -> None:
        fps = self._fps()
        pos = format_position_display(frame, fps, mode=self._time_display_mode)
        self._hud.setText(f"{pos} · {fps:.3f}fps  ⎘")
        self._position_hud()

    def _sync_position_controls_visibility(self) -> None:
        frame_mode = self._time_display_mode == TIME_DISPLAY_FRAME
        self._frame_input.setVisible(frame_mode)
        self._position_suffix.setVisible(frame_mode)
        self._timecode_position.setVisible(not frame_mode)

    def _update_position_display(self, pos_sec: float) -> None:
        fps = self._fps()
        max_frame = max(0, self._total_frames() - 1)
        if self._time_display_mode == TIME_DISPLAY_FRAME:
            frame = sec_to_frame(pos_sec, fps)
            if not self._frame_input.hasFocus():
                self._frame_input.blockSignals(True)
                self._frame_input.setText(str(frame))
                self._frame_input.blockSignals(False)
            self._position_suffix.setText(f" / {format_frame_label(max_frame)}")
            return
        dur = self._backend.duration() or (self._info.duration_sec if self._info else 0.0)
        self._timecode_position.setText(
            f"{format_timecode(pos_sec, fps=fps)} / {format_timecode(dur, fps=fps)}"
        )

    def _on_frame_input_commit(self) -> None:
        if self._time_display_mode != TIME_DISPLAY_FRAME:
            return
        raw = self._frame_input.text().strip()
        if not raw:
            self._update_position_display(self._backend.position())
            return
        try:
            frame = int(raw)
        except ValueError:
            self._update_position_display(self._backend.position())
            return
        max_frame = max(0, self._total_frames() - 1)
        self._seek_frame(max(0, min(max_frame, frame)))
        self._update_position_display(self._backend.position())

    def _reset_playback_speed(self) -> None:
        if self._speed == 1.0:
            return
        self._cmb_speed.setCurrentIndex(PLAYBACK_SPEED_STEPS.index(1.0))

    def _on_speed_changed(self, _index: int) -> None:
        speed = self._cmb_speed.currentData()
        if speed is None:
            return
        self._speed = float(speed)
        self._backend.set_speed(self._speed)
        if self._settings is not None:
            write_video_preview_playback_speed(self._settings, self._speed)

    def _on_backend_position(self, sec: float) -> None:
        if self._is_sequence_mode():
            return
        if self._scrubbing:
            return
        frame = self._source_frame_from_backend(sec)
        if self._proxy_enabled and self._backend.is_playing():
            prev_path = self._playback_path
            self._ensure_playback_clip(frame)
            if self._playback_path != prev_path:
                sec = self._backend.position()
                frame = self._source_frame_from_backend(sec)
        fps = max(1e-6, self._fps())
        source_sec = frame / fps
        if self._loop_playback and not self._backend_native_loop():
            start, end = self._loop_bounds()
            if frame > end or source_sec >= (end + 1) / fps - 1e-6:
                self._restart_loop_at(start)
                return
        elif self._backend_native_loop() and self._active_range() is not None:
            _, end = self._loop_bounds()
            if frame > end:
                source_sec = end / fps
        self._apply_playhead_ui(frame, pos_sec=source_sec)

    def _on_backend_duration(self, sec: float) -> None:
        if not self._info or sec <= 0:
            return
        # Range/full proxy clips are shorter than source — ignore their duration
        # so the scrubber keeps the full source timeline (avoids faux "zoom to bar").
        if self._path is not None and self._playback_path not in (None, self._path):
            return
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
        self._set_play_icon(False)
        if self._loop_playback:
            start, _ = self._loop_bounds()
            self._restart_loop_at(start, resume=True)
            self._update_loop_timer()

    def _on_backend_error(self, msg: str) -> None:
        logger.warning("video player: %s", msg)
        self._status_log = msg.strip()[:240]
        self._hud.setText(msg[:120])
        self._update_footer()

    def _set_play_icon(self, playing: bool) -> None:
        icon = "pause" if playing else "play"
        self._btn_play.setIcon(lucide_icon(icon, size=18, color_hex=MONOS_COLORS["text_label"]))

    def _playback_sec_for_frame(self, frame: int) -> float:
        if self._proxy_enabled:
            return self._backend_sec_for_source_frame(frame)
        return frame / max(1e-6, self._fps())

    def _ensure_playback_clip_for_frame(self, frame: int) -> None:
        if self._path is None:
            return
        if self._proxy_enabled:
            self._ensure_playback_clip(frame)
        elif self._playback_path != self._path:
            self._ensure_source_at_frame(frame)

    def _start_playback_at_frame(self, frame: int) -> None:
        """Ensure clip + resume — single seek (no keyframe pre-seek flash)."""
        self._ensure_playback_clip_for_frame(frame)
        sec = self._playback_sec_for_frame(frame)
        if self._backend.position_matches(sec):
            self._backend.play()
        else:
            self._backend.play_from(sec)

    def _toggle_play(self) -> None:
        self._frame_paint_gen += 1
        if self._is_sequence_mode():
            if self._seq_backend is None:
                return
            if self._seq_backend.is_playing():
                self._seq_backend.pause()
                self._loop_timer.stop()
                self._set_play_icon(False)
            else:
                self._sync_sequence_playback_loop()
                self._seq_backend.play()
                self._update_loop_timer()
                self._set_play_icon(True)
            return
        if self._backend.is_playing():
            self._backend.pause()
            self._loop_timer.stop()
            self._set_play_icon(False)
        else:
            self._start_playback_at_frame(self._current_frame())
            self._update_loop_timer()
            self._set_play_icon(True)

    def _volume_tooltip_text(self, value: int | None = None) -> str:
        if self._volume_muted:
            return "0"
        vol = self._volume if value is None else int(value)
        return str(max(0, min(100, vol)))

    def _sync_volume_icon(self) -> None:
        icon = "volume-x" if self._volume_muted else "volume-2"
        self._volume_icon.setIcon(
            lucide_icon(icon, size=16, color_hex=MONOS_COLORS["text_meta"])
        )

    def _effective_volume(self) -> int:
        return 0 if self._volume_muted else self._volume

    def _apply_volume_to_backend(self) -> None:
        self._backend.set_volume(self._effective_volume())

    def _toggle_volume_mute(self) -> None:
        if self._volume_muted:
            self._volume_muted = False
            restore = self._volume_before_mute if self._volume_before_mute > 0 else 80
            if self._volume <= 0:
                self._volume = restore
                self._volume_slider.blockSignals(True)
                self._volume_slider.setValue(restore)
                self._volume_slider.blockSignals(False)
        else:
            if self._volume > 0:
                self._volume_before_mute = self._volume
            self._volume_muted = True
        self._sync_volume_icon()
        self._apply_volume_to_backend()
        if self._volume_icon.underMouse():
            self._show_volume_tooltip(self._volume_icon)
        if self._settings is not None and not self._volume_muted:
            write_video_preview_volume(self._settings, self._volume)

    def _show_volume_tooltip(
        self,
        widget: QWidget,
        value: int | None = None,
        global_pos: QPoint | None = None,
    ) -> None:
        gp = global_pos
        if gp is None:
            gp = widget.mapToGlobal(QPoint(widget.width() // 2, widget.height() // 2))
        QToolTip.showText(gp, self._volume_tooltip_text(value), widget)

    def _on_volume_slider_pressed(self) -> None:
        self._volume_dragging = True
        self._show_volume_tooltip(self._volume_slider, self._volume_slider.value())

    def _on_volume_slider_moved(self, value: int) -> None:
        self._show_volume_tooltip(self._volume_slider, value)

    def _on_volume_slider_released(self) -> None:
        self._volume_dragging = False
        if not self._volume_slider.underMouse() and not self._volume_icon.underMouse():
            QToolTip.hideText()

    def _on_volume(self, v: int) -> None:
        self._volume = v
        if self._volume_muted and v > 0:
            self._volume_muted = False
            self._sync_volume_icon()
        if v > 0:
            self._volume_before_mute = v
        self._apply_volume_to_backend()
        if (
            self._volume_dragging
            or self._volume_slider.underMouse()
            or self._volume_icon.underMouse()
        ):
            self._show_volume_tooltip(self._volume_slider, v)
        if self._settings is not None and not self._volume_muted:
            write_video_preview_volume(self._settings, v)

    def _on_scrub_value(self, frame: int) -> None:
        if not self._scrubbing:
            return
        self._update_hud(frame)

    def _persist_ranges_local(self) -> None:
        key = self._media_key()
        if key is None:
            return
        try:
            if self._is_sequence_mode():
                save_sequence_ranges_local_draft(key, self._ranges)
            else:
                save_video_ranges_local_draft(key, self._ranges)
        except Exception:
            pass

    def _persist_markers_local(self) -> None:
        key = self._media_key()
        if key is None:
            return
        try:
            if self._is_sequence_mode():
                save_sequence_markers_local_draft(key, self._markers)
            else:
                save_video_markers_local_draft(key, self._markers)
        except Exception:
            pass

    def _persist_preview_session(self) -> None:
        key = self._media_key()
        if key is None:
            return
        try:
            if self._is_sequence_mode():
                save_sequence_preview_session_local_draft(
                    key,
                    frame=self._current_frame(),
                )
            elif self._info is not None:
                save_video_preview_session_local_draft(
                    key,
                    frame=self._current_frame(),
                )
        except Exception:
            pass

    def _sync_ranges(self) -> None:
        key = self._media_key()
        if key is None:
            return
        try:
            if self._is_sequence_mode():
                save_sequence_ranges_sidecar(key, self._ranges)
                save_sequence_ranges_local_draft(key, self._ranges)
                save_sequence_markers_sidecar(key, self._markers)
                save_sequence_markers_local_draft(key, self._markers)
            else:
                save_video_ranges_sidecar(key, self._ranges)
                save_video_ranges_local_draft(key, self._ranges)
                save_video_markers_sidecar(key, self._markers)
                save_video_markers_local_draft(key, self._markers)
            if self._is_sequence_mode():
                save_sequence_draw_sidecar(key, self._draw_layers)
            else:
                save_video_draw_sidecar(key, self._draw_layers)
            save_draw_local_draft(key, self._draw_layers, sequence=self._is_sequence_mode())
        except Exception:
            logger.warning("sync review data failed for %s", key, exc_info=True)
            return
        self._published_ranges = list(self._ranges)
        self._published_markers = list(self._markers)
        self._published_draw_layers = list(self._draw_layers)
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
        if self._is_sequence_mode():
            self._was_playing_before_scrub = (
                self._seq_backend.is_playing() if self._seq_backend else False
            )
            if self._was_playing_before_scrub and self._seq_backend is not None:
                self._seq_backend.pause()
                self._set_play_icon(False)
            return
        self._was_playing_before_scrub = self._backend.is_playing()
        if self._was_playing_before_scrub:
            self._backend.pause()
            self._loop_timer.stop()
            self._set_play_icon(False)

    def _on_scrub_frame_preview(self, frame: int) -> None:
        if not self._scrubbing:
            return
        self._pending_scrub_frame = frame
        self._update_hud(frame)
        if self._draw_playhead_sync_needed(frame):
            self._sync_draw_playhead(frame)
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
        if self._is_sequence_mode():
            self._apply_playback_for_frame(int(frame), precise=True)
            return
        if self._backend.is_playing():
            self._backend.pause()
            self._loop_timer.stop()
            self._set_play_icon(False)
        seek_frame = int(frame) if precise else self._hover_snap_frame(frame)
        if not precise and seek_frame == self._last_scrub_video_frame:
            return
        if not precise:
            self._last_scrub_video_frame = seek_frame
        elif seek_frame == self._last_scrub_video_frame:
            return
        else:
            self._last_scrub_video_frame = seek_frame
        self._apply_playback_for_frame(seek_frame, precise=precise)

    def _on_scrub_released(self, frame: int | None = None) -> None:
        self._scrubbing = False
        self._scrub_seek_timer.stop()
        self._pending_scrub_frame = None
        self._last_scrub_video_frame = None
        f = int(frame) if frame is not None else self._scrubber.value()
        self._apply_playback_for_frame(f, precise=True)
        self._scrubber.set_position_frame(f)
        self._update_hud(f)
        self._hide_hover_preview()
        self._loop_timer.stop()
        self._set_play_icon(False)
        self._was_playing_before_scrub = False
        self._update_position_display(f / max(1e-6, self._fps()))
        self._last_playhead_ui_frame = f
        if self._draw_playhead_sync_needed(f):
            self._sync_draw_playhead(f)
        self._persist_preview_session()

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
        if self._draw_tool_active():
            self._toggle_onion_skin()
            return
        self._mark_out_at_frame(self._current_frame())

    def _mark_in_at_frame(self, frame: int) -> None:
        if not self._ranges_tool_active() and not self._range_edit_unlocked:
            return
        self._push_range_undo()
        frame = int(frame)
        if self._active_range_id is not None and self._range_edit_unlocked:
            self._set_active_range_in_out(in_frame=frame)
            return
        self._draft_in = frame
        self._sync_range_ui()

    def _mark_out_at_frame(self, frame: int) -> None:
        if not self._ranges_tool_active() and not self._range_edit_unlocked:
            return
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
        if not self._ranges_tool_active():
            return
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
        self._range_edit_unlocked = False
        self._range_edit_cancel_snapshot = None
        self._draft_in = None
        self._draft_out = None
        self._pending_range_label = ""
        self._sync_range_ui()
        self._persist_ranges_local()
        self._seek_frame(in_f)

    def _focus_timeline_range(self) -> None:
        if not self._ranges_tool_active():
            return
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
        if not self._ranges_tool_active():
            return
        if self._active_range_id:
            self._on_range_edit_requested(self._active_range_id)

    def _pointer_over_scrubber(self, zone: str | None = None) -> bool:
        zone = self._footer_pointer_zone if zone is None else zone
        return zone in (
            "handle_in",
            "handle_out",
            "range_move",
            "range_overlap",
            "range",
            "track",
            "ruler",
        )

    def _on_edit_shortcut(self) -> None:
        if self._text_editing_focused():
            return
        zone = self._footer_pointer_zone
        if zone == "video" and self._draw_tool_active():
            if self._draw_keyframe_edit_unlocked:
                self._exit_draw_keyframe_edit()
            self._draw_brush_strip.set_active_tool("eraser")
            return
        if not self._pointer_over_scrubber(zone):
            return
        if self._draw_keyframe_edit_unlocked:
            self._exit_draw_keyframe_edit()
            return
        if self._can_edit_draw_keyframe():
            self._enter_draw_keyframe_edit()
            return
        self._edit_highlighted_range()

    def _can_edit_draw_keyframe(self) -> bool:
        layer = self._active_draw_layer()
        if layer is None:
            return False
        if self._active_keyframe_frame is not None:
            return keyframe_at_exact_on_layer(layer, self._active_keyframe_frame) is not None
        frame = self._current_frame()
        return display_keyframe_on_layer(
            layer,
            frame,
            total_frames=self._total_frames(),
        ) is not None

    def _resolve_draw_keyframe_edit_target(self) -> ReviewDrawLayerKeyframe | None:
        layer = self._active_draw_layer()
        if layer is None:
            layer = ensure_layer_in_document(self._draw_layers, self._active_layer_id)
        if self._active_keyframe_frame is not None:
            kf = keyframe_at_exact_on_layer(layer, self._active_keyframe_frame)
            if kf is not None:
                return kf
        return display_keyframe_on_layer(
            layer,
            self._current_frame(),
            total_frames=self._total_frames(),
        )

    def _enter_draw_keyframe_edit(self) -> None:
        kf = self._resolve_draw_keyframe_edit_target()
        if kf is None:
            return
        layer = self._active_draw_layer()
        if layer is None:
            layer = ensure_layer_in_document(self._draw_layers, self._active_layer_id)
            self._active_layer_id = layer.id
        self._draw_keyframe_edit_unlocked = True
        self._active_keyframe_frame = int(kf.frame)
        if not self._draw_tool_active():
            self._activate_tool(ReviewToolMode.draw)
        self._tools_panel.set_workspace(ReviewWorkspace.tools)
        self._sync_draw_ui()
        self._seek_frame(int(kf.frame))

    def _exit_draw_keyframe_edit(self) -> None:
        if not self._draw_keyframe_edit_unlocked:
            return
        self._draw_keyframe_edit_unlocked = False
        self._sync_draw_ui()

    def _on_draw_keyframe_move_requested(self, new_frame: int) -> None:
        if not self._draw_keyframe_edit_unlocked or self._active_keyframe_frame is None:
            return
        layer = self._active_draw_layer()
        if layer is None:
            return
        old_frame = int(self._active_keyframe_frame)
        target = max(0, min(self._total_frames() - 1, int(new_frame)))
        if target == old_frame:
            return
        if not move_keyframe_on_layer(layer, old_frame, target):
            return
        self._active_keyframe_frame = target
        kf = self._active_layer_keyframe()
        if kf is not None:
            panel = self._tools_panel.draw_panel()
            panel.set_keyframe_edit_mode(
                True,
                frame=target,
                hold=hold_frames_for_keyframe(kf),
                max_frame=self._total_frames() - 1,
                layer_id=self._active_layer_id,
            )
        self._sync_draw_ui()
        self._sync_draw_playhead(target)

    def _on_draw_keyframe_move_finished(self) -> None:
        if not self._draw_keyframe_edit_unlocked:
            return
        self._persist_draw_local()

    def _on_draw_keyframe_edit_frame_changed(self, frame: int) -> None:
        if not self._draw_keyframe_edit_unlocked or self._active_keyframe_frame is None:
            return
        layer = self._active_draw_layer()
        if layer is None:
            return
        old_frame = int(self._active_keyframe_frame)
        target = max(0, min(self._total_frames() - 1, int(frame)))
        if target == old_frame:
            return
        if not move_keyframe_on_layer(layer, old_frame, target):
            panel = self._tools_panel.draw_panel()
            kf = self._active_layer_keyframe()
            if kf is not None:
                panel.set_keyframe_edit_mode(
                    True,
                    frame=int(kf.frame),
                    hold=hold_frames_for_keyframe(kf),
                    max_frame=self._total_frames() - 1,
                    layer_id=self._active_layer_id,
                )
            return
        self._active_keyframe_frame = target
        self._persist_draw_local()
        self._sync_draw_ui()
        self._seek_frame(target)

    def _on_draw_keyframe_hold_changed(self, hold: int) -> None:
        if not self._draw_keyframe_edit_unlocked:
            return
        kf = self._active_layer_keyframe()
        if kf is None:
            return
        set_keyframe_hold(kf, hold)
        self._persist_draw_local()
        self._sync_draw_ui()
        self._sync_draw_playhead()

    def _adjust_draw_keyframe_hold(self, delta: int) -> None:
        if not self._draw_keyframe_edit_unlocked:
            return
        kf = self._active_layer_keyframe()
        if kf is None:
            return
        next_hold = hold_frames_for_keyframe(kf) + int(delta)
        set_keyframe_hold(kf, next_hold)
        panel = self._tools_panel.draw_panel()
        panel.sync_keyframe_edit_hold(hold_frames_for_keyframe(kf))
        self._persist_draw_local()
        self._sync_draw_ui()
        self._sync_draw_playhead()

    def _on_draw_layer_default_hold_changed(self, layer_id: str, hold: int) -> None:
        layer = next((item for item in self._draw_layers if item.id == layer_id), None)
        if layer is None:
            return
        set_layer_default_hold(layer, hold)
        self._persist_draw_local()
        self._sync_draw_ui()

    def _delete_draw_layer(self, layer_id: str) -> None:
        layer = next((item for item in self._draw_layers if item.id == layer_id), None)
        if layer is None:
            return
        kf_count = len(layer.keyframes)
        stroke_count = sum(len(kf.strokes) for kf in layer.keyframes)
        name = layer.name or "Layer"
        detail = ""
        if kf_count or stroke_count:
            detail = (
                f" ({kf_count} keyframe{'s' if kf_count != 1 else ''}, "
                f"{stroke_count} stroke{'s' if stroke_count != 1 else ''})"
            )
        if not ask_delete(self, "Delete layer", f'Delete "{name}"{detail}?'):
            return
        if not delete_layer_from_document(self._draw_layers, layer_id):
            return
        if not self._draw_layers:
            fresh = ensure_layer_in_document(self._draw_layers, None)
            self._active_layer_id = fresh.id
            self._active_keyframe_frame = None
            self._draw_keyframe_edit_unlocked = False
        elif self._active_layer_id == layer_id:
            self._draw_keyframe_edit_unlocked = False
            self._active_layer_id = self._draw_layers[0].id
            next_layer = self._active_draw_layer()
            if next_layer is not None and next_layer.keyframes:
                self._active_keyframe_frame = int(next_layer.keyframes[0].frame)
            else:
                self._active_keyframe_frame = None
        self._persist_draw_local()
        self._sync_draw_ui()

    def _delete_active_draw_keyframe(self) -> None:
        if not self._draw_keyframe_edit_unlocked or self._active_keyframe_frame is None:
            return
        layer = self._active_draw_layer()
        if layer is None:
            return
        frame = int(self._active_keyframe_frame)
        if not delete_keyframe_on_layer(layer, frame):
            return
        self._active_keyframe_frame = None
        self._draw_keyframe_edit_unlocked = False
        self._persist_draw_local()
        self._sync_draw_ui()

    def _on_range_selected(self, range_id: str, shift_held: bool = False) -> None:
        self._try_insert_range_into_note(range_id)
        self._apply_range_selection(range_id)

    def _on_range_highlighted(
        self, range_id: str, shift_held: bool = False, insert_note: bool = True
    ) -> None:
        if insert_note:
            self._try_insert_range_into_note(range_id)
        self._apply_range_highlight(range_id)

    def _try_insert_range_into_note(self, range_id: str) -> bool:
        if self._context != PreviewContext.entity or not self._note_rail_open():
            return False
        panel = self._note_rail.panel()
        if not panel.compose_active() or not panel.auto_add_enabled():
            return False
        rng = next((r for r in self._ranges if r.id == range_id), None)
        if rng is None:
            return False
        return panel.insert_range_reference(rng, self._fps())

    def _apply_range_selection(self, range_id: str) -> None:
        self._scrubber.clear_overlap_cycle()
        self._active_range_id = range_id
        self._range_edit_unlocked = False
        rng = next((r for r in self._ranges if r.id == range_id), None)
        self._sync_range_ui()
        if rng is not None:
            self._seek_frame(rng.in_frame)
        self._sync_proxy_state()
        self._sync_playback_loop()

    def _apply_range_highlight(self, range_id: str) -> None:
        self._active_range_id = range_id
        self._range_edit_unlocked = False
        rng = next((r for r in self._ranges if r.id == range_id), None)
        self._sync_range_ui()
        if rng is not None:
            self._seek_frame(rng.in_frame)
        self._sync_proxy_state()
        self._sync_playback_loop()

    def _on_range_edit_requested(self, range_id: str) -> None:
        if not self._range_edit_unlocked:
            self._range_edit_cancel_snapshot = self._capture_range_snapshot()
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
        self._sync_proxy_state()
        self._sync_playback_loop()

    def _delete_range(self, range_id: str) -> None:
        self._push_range_undo()
        self._ranges = [r for r in self._ranges if r.id != range_id]
        if self._active_range_id == range_id:
            self._active_range_id = None
            self._range_edit_unlocked = False
        self._sync_range_ui()
        self._persist_ranges_local()
        if self._proxy_enabled:
            self._sync_proxy_state()

    def _delete_all_ranges(self) -> None:
        if not self._ranges:
            return
        n = len(self._ranges)
        if not ask_delete(
            self,
            "Delete all ranges",
            f"Delete all {n} range{'s' if n != 1 else ''}? This cannot be undone from the list.",
        ):
            return
        self._push_range_undo()
        self._ranges = []
        self._active_range_id = None
        self._range_edit_unlocked = False
        self._sync_range_ui()
        self._persist_ranges_local()
        if self._proxy_enabled:
            self._sync_proxy_state()

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
        self._apply_range_selection(range_id)

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
        self._hover_label.show()
        self._hover_debounce.stop()
        self._hover_debounce.start()

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
        self._hover_label.show()
        self._hover_debounce.stop()
        self._hover_debounce.start()

    def _start_hover_fetch(self) -> None:
        frame = self._pending_hover_frame
        if frame is None:
            return
        if self._is_sequence_mode():
            if not self._sequence_frames:
                return
            idx = max(0, min(len(self._sequence_frames) - 1, int(frame)))
            path = self._sequence_frames[idx]
            self._hover_token += 1
            token = self._hover_token
            self._hover_pool.start(
                _SequenceHoverRunnable(
                    path,
                    int(frame),
                    token,
                    self._hover_signaler,
                    max_side=max(_HOVER_PREVIEW_W, _HOVER_PREVIEW_H) * 2,
                )
            )
            return
        if self._path is None or self._info is None:
            return
        for_drag = self._scrubbing
        lookup = self._thumb_lookup_frame(frame, for_drag=for_drag)
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
        if self._closing or not self.isVisible():
            return
        if token != self._hover_token:
            return
        if not png_bytes:
            return
        scaled = self._pixmap_from_hover_png(bytes(png_bytes))
        if scaled is None or scaled.isNull():
            return
        pending = self._pending_hover_frame
        if pending is None:
            return
        if self._is_sequence_mode():
            if frame != int(pending):
                return
        elif frame != self._thumb_lookup_frame(pending, for_drag=self._scrubbing):
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
            bounds=self._main_bounds(),
        )

    def _hide_hover_preview(self) -> None:
        self._hover_debounce.stop()
        self._pending_hover_frame = None
        self._hover_token += 1
        self._hover_label.hide()

    def _delete_active_item(self) -> None:
        if self._text_editing_focused():
            return
        if self._draw_keyframe_edit_unlocked:
            self._delete_active_draw_keyframe()
            return
        if self._markers_tool_active():
            if self._active_marker_id:
                self._delete_marker(self._active_marker_id)
            return
        if self._ranges_tool_active() and self._active_range_id:
            self._delete_range(self._active_range_id)

    def _add_marker_at_playhead(self) -> None:
        if not self._markers_tool_active():
            return
        if not self._is_sequence_mode() and self._info is None:
            return
        frame = self._current_frame()
        if not validate_marker_frame(frame, total_frames=self._total_frames()):
            return
        existing = next((m for m in self._markers if m.frame == frame), None)
        if existing is not None:
            self._active_marker_id = existing.id
            self._sync_range_ui()
            self._seek_frame(frame)
            return
        rid = new_marker_id()
        self._markers.append(VideoReviewMarker(rid, frame, "", time.time()))
        self._active_marker_id = rid
        self._sync_range_ui()
        self._persist_markers_local()
        self._activate_tool(ReviewToolMode.markers)

    def _try_insert_marker_into_note(self, marker_id: str) -> bool:
        if self._context != PreviewContext.entity or not self._note_rail_open():
            return False
        panel = self._note_rail.panel()
        if not panel.compose_active() or not panel.auto_add_enabled():
            return False
        marker = next((m for m in self._markers if m.id == marker_id), None)
        if marker is None:
            return False
        return panel.insert_marker_reference(marker, self._fps())

    def _apply_marker_selection(self, marker_id: str) -> None:
        self._active_marker_id = marker_id
        self._sync_range_ui()
        marker = next((m for m in self._markers if m.id == marker_id), None)
        if marker is not None:
            self._seek_frame(marker.frame)

    def _on_marker_highlighted(self, marker_id: str, shift_held: bool = False) -> None:
        self._try_insert_marker_into_note(marker_id)
        self._apply_marker_selection(marker_id)

    def _on_marker_selected(self, marker_id: str) -> None:
        self._on_marker_highlighted(marker_id, shift_held=False)

    def _on_note_time_anchor(self, href: str) -> None:
        anchor = parse_time_href(href)
        if anchor is None:
            return
        self._seek_frame(anchor.frame)
        if anchor.kind == "range" and anchor.ref_id:
            self._apply_range_selection(anchor.ref_id)
        elif anchor.kind == "marker" and anchor.ref_id:
            self._apply_marker_selection(anchor.ref_id)

    def _on_marker_deselected(self) -> None:
        if self._active_marker_id is None:
            return
        self._active_marker_id = None
        self._sync_range_ui()

    def _delete_marker(self, marker_id: str) -> None:
        self._markers = [m for m in self._markers if m.id != marker_id]
        if self._active_marker_id == marker_id:
            self._active_marker_id = self._markers[0].id if self._markers else None
        self._sync_range_ui()
        self._persist_markers_local()

    def _delete_all_markers(self) -> None:
        if not self._markers:
            return
        n = len(self._markers)
        if not ask_delete(
            self,
            "Delete all markers",
            f"Delete all {n} marker{'s' if n != 1 else ''}? This cannot be undone from the list.",
        ):
            return
        self._markers = []
        self._active_marker_id = None
        self._sync_range_ui()
        self._persist_markers_local()

    def _on_marker_label_changed(self, marker_id: str, label: str) -> None:
        updated: list[VideoReviewMarker] = []
        now = time.time()
        for m in self._markers:
            if m.id == marker_id:
                updated.append(
                    VideoReviewMarker(m.id, m.frame, label.strip()[:80], created_at=now)
                )
            else:
                updated.append(m)
        self._markers = updated
        self._sync_range_ui()
        self._persist_markers_local()

    def _load_draw_keyframes_from_sidecar(self) -> None:
        key = self._media_key()
        if key is None:
            self._draw_layers = []
            self._published_draw_layers = []
            self._active_keyframe_frame = None
            self._active_layer_id = None
            return
        pub, work, _ = load_draw_layers_for_preview(
            key,
            sequence=self._is_sequence_mode(),
            total_frames=self._total_frames(),
        )
        self._published_draw_layers = pub
        self._draw_layers = work
        if work:
            layer = work[0]
            self._active_layer_id = layer.id
            if layer.keyframes:
                self._active_keyframe_frame = int(layer.keyframes[0].frame)
            else:
                self._active_keyframe_frame = None
        else:
            self._active_keyframe_frame = None
            self._active_layer_id = None

    def _active_layer_keyframe(self) -> ReviewDrawLayerKeyframe | None:
        if self._active_keyframe_frame is None:
            return None
        layer = self._active_draw_layer()
        if layer is None:
            return None
        return keyframe_at_exact_on_layer(layer, self._active_keyframe_frame)

    def _active_draw_layer(self) -> ReviewDrawLayer | None:
        if not self._active_layer_id:
            return None
        return next((layer for layer in self._draw_layers if layer.id == self._active_layer_id), None)

    def _draw_playhead_sync_needed(self, frame: int | None = None) -> bool:
        if self._draw_tool_active() or self._onion_enabled:
            return True
        if not self._draw_layers:
            return False
        f = int(self._current_frame() if frame is None else frame)
        return draw_visible_at(self._draw_layers, f, total_frames=self._total_frames())

    def _apply_playhead_ui(self, frame: int, *, pos_sec: float | None = None) -> None:
        frame = int(frame)
        if frame == self._last_playhead_ui_frame:
            return
        self._last_playhead_ui_frame = frame
        if frame != self._scrubber.value():
            self._scrubber.set_position_frame(frame)
            self._update_hud(frame)
        sec = pos_sec if pos_sec is not None else frame / max(1e-6, self._fps())
        self._update_position_display(sec)
        if (
            self._context == PreviewContext.entity
            and self._note_rail_open()
        ):
            self._update_note_frame_hint(frame)
        if self._draw_playhead_sync_needed(frame):
            self._sync_draw_playhead(frame)
        self._schedule_preview_session_persist()

    def _sync_draw_ui(self) -> None:
        panel = self._tools_panel.draw_panel()
        panel.set_layers(
            self._draw_layers,
            active_frame=self._active_keyframe_frame,
            active_layer_id=self._active_layer_id,
        )
        kf = self._active_layer_keyframe()
        panel.set_keyframe_edit_mode(
            self._draw_keyframe_edit_unlocked,
            frame=int(kf.frame) if kf is not None else self._active_keyframe_frame,
            hold=hold_frames_for_keyframe(kf) if kf is not None else 1,
            max_frame=max(0, self._total_frames() - 1),
            layer_id=self._active_layer_id,
        )
        self._scrubber.set_draw_layers(
            self._draw_layers,
            highlight_frame=self._active_keyframe_frame,
            highlight_layer_id=self._active_layer_id,
            edit_mode=self._draw_keyframe_edit_unlocked,
        )
        self._sync_scrubber_timeline_display()
        self._sync_draw_viewport()

    def _sync_draw_playhead(self, frame: int | None = None) -> None:
        """Update draw overlay for timeline playhead (play / scrub) without full panel sync."""
        if not self._draw_playhead_sync_needed(frame):
            return
        if not hasattr(self, "_draw_overlay"):
            return
        f = int(self._current_frame() if frame is None else frame)
        self._draw_overlay.set_layers(
            self._draw_layers,
            active_layer_id=self._active_layer_id,
        )
        self._draw_overlay.set_total_frames(self._total_frames())
        self._draw_overlay.set_current_frame(f)
        show_overlay = (
            self._draw_tool_active()
            or draw_visible_at(self._draw_layers, f, total_frames=self._total_frames())
            or (
                self._onion_enabled
                and onion_has_neighbors(
                    self._draw_layers,
                    f,
                    span=self._onion_span,
                    active_layer_id=self._active_layer_id,
                )
            )
        )
        if self._draw_overlay.isVisible() != show_overlay:
            self._draw_overlay.setVisible(show_overlay)
        if self._onion_enabled:
            self._draw_overlay.set_onion(enabled=True, span=self._onion_span)
            self._schedule_onion_refresh()
        self._sync_native_video_zorder()

    def _sync_draw_viewport(self) -> None:
        if not hasattr(self, "_draw_overlay"):
            return
        frame = self._current_frame()
        self._draw_overlay.set_layers(
            self._draw_layers,
            active_layer_id=self._active_layer_id,
        )
        self._draw_overlay.set_total_frames(self._total_frames())
        active = self._draw_tool_active()
        self._draw_overlay.set_draw_active(active)
        self._draw_overlay.set_onion(enabled=self._onion_enabled, span=self._onion_span)
        if hasattr(self, "_draw_brush_strip"):
            self._draw_brush_strip.setVisible(active)
            if active:
                self._position_draw_brush_strip()
        self._sync_draw_playhead(frame)
        if hasattr(self, "_surface"):
            self._sync_viewport_overlay_geometry()
            if active:
                self._surface.setToolTip(
                    "Draw — Pen/Arrow/Rect (D to exit) · Wheel — Zoom · Alt+MMB — Pan"
                )
            else:
                self._surface.setToolTip(
                    "Wheel — Zoom · MMB drag — Scrub · Alt+MMB — Pan"
                )
        self._schedule_onion_refresh()
        self._sync_native_video_zorder()

    def _sync_viewport_overlay_geometry(self) -> None:
        self._apply_viewer_plate_geometry()

    def _sync_draw_overlay_state(self) -> None:
        if self._draw_tool_active() and self._backend.is_playing():
            self._backend.pause()
        self._sync_draw_ui()

    def _toggle_draw_tool(self) -> None:
        if self._draw_tool_active():
            self._activate_tool(ReviewToolMode.ranges)
        else:
            self._activate_tool(ReviewToolMode.draw)
            self._sync_draw_overlay_state()

    def _toggle_onion_skin(self) -> None:
        if not self._draw_tool_active():
            return
        self._onion_enabled = not self._onion_enabled
        if hasattr(self, "_draw_brush_strip"):
            self._draw_brush_strip.set_onion_enabled(self._onion_enabled)
        self._schedule_onion_refresh()

    def _add_draw_keyframe_at_playhead(self) -> None:
        frame = self._current_frame()
        layer = ensure_layer_in_document(self._draw_layers, self._active_layer_id)
        kf, _ = ensure_keyframe_on_layer(layer, frame)
        self._active_layer_id = layer.id
        self._active_keyframe_frame = int(kf.frame)
        self._persist_draw_local()
        self._sync_draw_ui()

    def _add_draw_layer_on_keyframe(self) -> None:
        layer = make_draw_layer(name=f"Layer {len(self._draw_layers) + 1}")
        self._draw_layers.append(layer)
        frame = self._active_keyframe_frame if self._active_keyframe_frame is not None else self._current_frame()
        kf, _ = ensure_keyframe_on_layer(layer, frame)
        self._active_layer_id = layer.id
        self._active_keyframe_frame = int(kf.frame)
        self._persist_draw_local()
        self._sync_draw_ui()

    def _on_draw_keyframe_selected(self, layer_id: str, frame: int) -> None:
        if self._draw_keyframe_select_guard:
            return
        self._draw_keyframe_select_guard = True
        try:
            self._active_keyframe_frame = int(frame)
            if layer_id:
                self._active_layer_id = layer_id
            elif self._active_layer_id is None and self._draw_layers:
                self._active_layer_id = self._draw_layers[0].id
            if self._current_frame() != int(frame):
                self._seek_frame(int(frame))
            self._sync_draw_ui()
            if not self._draw_tool_active():
                self._activate_tool(ReviewToolMode.draw)
        finally:
            self._draw_keyframe_select_guard = False

    def _on_draw_layer_selected(self, layer_id: str) -> None:
        self._active_layer_id = layer_id
        layer = self._active_draw_layer()
        if layer is not None and layer.keyframes:
            if self._active_keyframe_frame is None or keyframe_at_exact_on_layer(
                layer, self._active_keyframe_frame
            ) is None:
                self._active_keyframe_frame = int(layer.keyframes[0].frame)
        else:
            self._active_keyframe_frame = None
        self._sync_draw_ui()

    def _toggle_active_draw_layer_visibility(self, layer_id: str) -> None:
        layer = next((item for item in self._draw_layers if item.id == layer_id), None)
        if layer is None:
            return
        layer.visible = not layer.visible
        self._persist_draw_local()
        self._sync_draw_ui()

    def _toggle_active_draw_keyframe_visibility(self, layer_id: str, frame: int) -> None:
        layer = next((item for item in self._draw_layers if item.id == layer_id), None)
        if layer is None:
            return
        kf = keyframe_at_exact_on_layer(layer, frame)
        if kf is None:
            return
        kf.visible = not kf.visible
        self._persist_draw_local()
        self._sync_draw_ui()

    def _on_draw_stroke_committed(self, stroke: object) -> None:
        if not isinstance(stroke, ReviewDrawStroke):
            return
        frame = self._current_frame()
        layer = ensure_layer_in_document(self._draw_layers, self._active_layer_id)
        kf, _ = ensure_keyframe_on_layer(layer, frame)
        if stroke.tool == "eraser":
            kf.strokes = apply_eraser_to_strokes(kf.strokes, stroke.points, stroke.width_px)
        else:
            kf.strokes.append(stroke)
        self._active_keyframe_frame = int(kf.frame)
        self._active_layer_id = layer.id
        self._persist_draw_local()
        self._sync_draw_ui()

    def _undo_draw_stroke(self) -> None:
        layer = self._active_draw_layer()
        kf = self._active_layer_keyframe()
        if layer is None or kf is None or not kf.strokes:
            return
        kf.strokes = list(kf.strokes[:-1])
        self._persist_draw_local()
        self._sync_draw_ui()

    def _show_draw_quick_popup(self, global_pos: QPoint) -> None:
        popup = self._draw_quick_popup
        if popup is not None:
            try:
                popup.close()
            except RuntimeError:
                pass
            self._draw_quick_popup = None
        popup = VideoReviewDrawQuickPopup(self)
        popup.set_state(
            tool=self._draw_brush_strip.active_tool(),
            color=self._draw_overlay.color(),
            width=self._draw_overlay.width_px(),
        )
        popup.tool_changed.connect(self._on_draw_quick_tool_changed)
        popup.color_changed.connect(self._on_draw_quick_color_changed)
        popup.width_changed.connect(self._on_draw_quick_width_changed)
        popup.destroyed.connect(lambda *_: setattr(self, "_draw_quick_popup", None))
        popup.show_at(global_pos)
        self._draw_quick_popup = popup

    def _on_draw_quick_tool_changed(self, tool: str) -> None:
        if not self._draw_tool_active():
            self._activate_tool(ReviewToolMode.draw)
        self._draw_brush_strip.set_active_tool(tool)

    def _on_draw_quick_color_changed(self, color: str) -> None:
        if not self._draw_tool_active():
            self._activate_tool(ReviewToolMode.draw)
        self._draw_brush_strip.set_active_color(color)

    def _on_draw_quick_width_changed(self, width: float) -> None:
        if not self._draw_tool_active():
            self._activate_tool(ReviewToolMode.draw)
        self._draw_brush_strip.set_active_width(width)

    def _on_draw_tool_changed(self, tool: str) -> None:
        if tool in ("pen", "arrow", "rect", "eraser"):
            self._draw_overlay.set_tool(tool)

    def _on_draw_color_changed(self, color: str) -> None:
        self._draw_overlay.set_color(color)

    def _on_draw_width_changed(self, width: float) -> None:
        self._draw_overlay.set_width_px(width)

    def _on_draw_onion_enabled(self, enabled: bool) -> None:
        self._onion_enabled = bool(enabled)
        if hasattr(self, "_draw_brush_strip"):
            self._draw_brush_strip.set_onion_enabled(self._onion_enabled)
        self._schedule_onion_refresh()

    def _on_draw_onion_span_changed(self, span: int) -> None:
        self._onion_span = max(1, min(5, int(span)))
        if hasattr(self, "_draw_brush_strip"):
            self._draw_brush_strip.set_onion_span(self._onion_span)
        self._schedule_onion_refresh()

    def _schedule_onion_refresh(self) -> None:
        if not hasattr(self, "_onion_refresh_timer"):
            return
        self._onion_refresh_timer.start()

    def _refresh_draw_onion(self) -> None:
        if not hasattr(self, "_draw_overlay"):
            return
        if hasattr(self, "_onion_layer"):
            self._onion_layer.set_onion_enabled(False)
            self._onion_layer.clear_ghosts()
        if not self._onion_enabled:
            self._draw_overlay.set_onion(enabled=False, span=self._onion_span)
            self._draw_overlay.update()
            return
        self._draw_overlay.set_onion(enabled=True, span=self._onion_span)
        self._draw_overlay.set_layers(
            self._draw_layers,
            active_layer_id=self._active_layer_id,
        )
        self._draw_overlay.set_current_frame(self._current_frame())
        self._draw_overlay.update()
        if hasattr(self, "_draw_overlay"):
            self._draw_overlay.raise_()

    def _persist_draw_local(self) -> None:
        key = self._media_key()
        if key is None:
            return
        try:
            save_draw_local_draft(key, self._draw_layers, sequence=self._is_sequence_mode())
        except Exception:
            pass
        self._update_sync_button()

    def _export_markers_png(self) -> None:
        if not self._markers:
            return
        if self._is_sequence_mode():
            if not self._sequence_frames or self._sequence_folder is None:
                return
            start_dir = str(self._sequence_folder)
        elif self._path is None:
            return
        else:
            start_dir = str(self._path.parent)
        folder = QFileDialog.getExistingDirectory(
            self,
            "Export markers as PNG",
            start_dir,
        )
        if not folder:
            return
        ordered = self._marker_list().ordered_markers()
        try:
            if self._is_sequence_mode():
                outs = export_sequence_markers_png(
                    self._sequence_frames,
                    ordered,
                    Path(folder),
                )
            else:
                assert self._path is not None
                outs = export_video_markers_png(
                    self._path,
                    ordered,
                    Path(folder),
                    fps=self._fps(),
                )
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Export complete", f"Exported {len(outs)} PNG file(s).")

    def _select_prev_marker(self) -> None:
        ordered = self._marker_list().ordered_markers()
        if not ordered:
            return
        ids = [m.id for m in ordered]
        if self._active_marker_id not in ids:
            self._on_marker_selected(ids[0])
            return
        idx = max(0, ids.index(self._active_marker_id) - 1)
        self._on_marker_selected(ids[idx])

    def _select_next_marker(self) -> None:
        ordered = self._marker_list().ordered_markers()
        if not ordered:
            return
        ids = [m.id for m in ordered]
        if self._active_marker_id not in ids:
            self._on_marker_selected(ids[0])
            return
        idx = min(len(ids) - 1, ids.index(self._active_marker_id) + 1)
        self._on_marker_selected(ids[idx])

    def _select_prev_list_item(self) -> None:
        if self._draw_keyframe_edit_unlocked:
            self._adjust_draw_keyframe_hold(-1)
            return
        if not self._ranges_tool_active():
            return
        self._select_prev_range()

    def _select_next_list_item(self) -> None:
        if self._draw_keyframe_edit_unlocked:
            self._adjust_draw_keyframe_hold(1)
            return
        if not self._ranges_tool_active():
            return
        self._select_next_range()

    def _select_prev_marker_shortcut(self) -> None:
        if not self._markers_tool_active():
            return
        self._select_prev_marker()

    def _select_next_marker_shortcut(self) -> None:
        if not self._markers_tool_active():
            return
        self._select_next_marker()

    def _select_prev_range(self) -> None:
        if not self._ranges_tool_active():
            return
        ordered = self._range_list().ordered_ranges()
        if not ordered:
            return
        ids = [r.id for r in ordered]
        if self._active_range_id not in ids:
            self._apply_range_selection(ids[0])
            return
        idx = max(0, ids.index(self._active_range_id) - 1)
        self._apply_range_selection(ids[idx])

    def _select_next_range(self) -> None:
        if not self._ranges_tool_active():
            return
        ordered = self._range_list().ordered_ranges()
        if not ordered:
            return
        ids = [r.id for r in ordered]
        if self._active_range_id not in ids:
            self._apply_range_selection(ids[0])
            return
        idx = min(len(ids) - 1, ids.index(self._active_range_id) + 1)
        self._apply_range_selection(ids[idx])

    def _active_range(self) -> VideoFrameRange | None:
        if not self._active_range_id:
            return None
        return next((r for r in self._ranges if r.id == self._active_range_id), None)

    def _seek_frame(self, frame: int) -> None:
        self._in_programmatic_seek = True
        try:
            self._last_playhead_ui_frame = None
            self._apply_playback_for_frame(frame, precise=True)
            self._scrubber.set_position_frame(frame)
            self._update_hud(frame)
            self._update_position_display(frame / max(1e-6, self._fps()))
            self._schedule_preview_session_persist()
            self._sync_draw_viewport()
        finally:
            self._in_programmatic_seek = False

    def _sync_sequence_playback_loop(self) -> None:
        if self._seq_backend is None:
            return
        start, end = self._loop_bounds()
        self._seq_backend.set_loop_region(start, end, enabled=self._loop_playback)

    def _loop_bounds(self) -> tuple[int, int]:
        rng = self._active_range()
        if rng is not None:
            lo, hi = sorted((rng.in_frame, rng.out_frame))
            return lo, hi
        return 0, max(0, self._total_frames() - 1)

    def _backend_native_loop(self) -> bool:
        if self._is_sequence_mode():
            return False
        return getattr(self._backend, "name", "") == "mpv" and self._loop_playback

    def _sync_playback_loop(self) -> None:
        if self._is_sequence_mode():
            self._sync_sequence_playback_loop()
            return
        fps = max(1e-6, self._fps())
        if self._loop_playback:
            rng = self._active_range()
            if rng is not None:
                lo, hi = sorted((rng.in_frame, rng.out_frame))
                on_range_proxy = (
                    self._proxy_enabled
                    and self._proxy_mode == "range"
                    and self._range_proxy_manifest is not None
                    and self._playback_path is not None
                    and self._path is not None
                    and self._playback_path != self._path
                )
                if on_range_proxy:
                    self._backend.configure_playback_loop(
                        enabled=True,
                        range_start_sec=0.0,
                        range_end_sec=(hi - lo + 1) / fps,
                    )
                else:
                    self._backend.configure_playback_loop(
                        enabled=True,
                        range_start_sec=lo / fps,
                        range_end_sec=(hi + 1) / fps,
                    )
            else:
                self._backend.configure_playback_loop(enabled=True)
        else:
            self._backend.configure_playback_loop(enabled=False)

    def _loop_past_end(self, sec: float, end_frame: int) -> bool:
        fps = max(1e-6, self._fps())
        return sec >= (end_frame + 1) / fps - 1e-6

    def _restart_loop_at(self, start_frame: int, *, resume: bool | None = None) -> None:
        if self._is_sequence_mode():
            if resume is None:
                resume = bool(self._seq_backend and self._seq_backend.is_playing())
            self._seek_frame(start_frame)
            if resume and self._seq_backend is not None:
                self._seq_backend.play()
                self._set_play_icon(True)
            return
        if resume is None:
            resume = self._backend.is_playing()
        fps = max(1e-6, self._fps())
        self._ensure_playback_clip_for_frame(start_frame)
        sec = self._playback_sec_for_frame(start_frame)
        self._scrubber.set_position_frame(start_frame)
        self._update_hud(start_frame)
        self._update_position_display(start_frame / fps)
        if resume:
            if self._backend.position_matches(sec):
                self._backend.play()
            else:
                self._backend.play_from(sec)
            self._set_play_icon(True)
        else:
            self._backend.seek(sec, precise=True)

    def _update_loop_timer(self) -> None:
        if self._is_sequence_mode():
            playing = bool(self._seq_backend and self._seq_backend.is_playing())
        else:
            playing = self._backend.is_playing()
        if (
            self._loop_playback
            and not self._backend_native_loop()
            and playing
        ):
            self._loop_timer.start()
        else:
            self._loop_timer.stop()

    def _seek_loop_start(self) -> None:
        start, _ = self._loop_bounds()
        self._seek_frame(start)

    def _check_loop(self) -> None:
        if not self._loop_playback or self._backend_native_loop() or self._scrubbing:
            return
        start, end = self._loop_bounds()
        if self._is_sequence_mode():
            if self._seq_backend and self._seq_backend.is_playing() and self._current_frame() > end:
                self._restart_loop_at(start)
            return
        sec = self._backend.position()
        if self._loop_past_end(sec, end) or self._current_frame() > end:
            self._restart_loop_at(start)

    def _on_loop_toggled(self, checked: bool) -> None:
        self._loop_playback = checked
        self._sync_playback_loop()
        if checked:
            self._seek_loop_start()
        self._update_loop_timer()
        if self._settings is not None:
            write_video_preview_loop(self._settings, checked)

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

    def _source_file_size(self) -> int:
        if self._path is None:
            return 0
        try:
            return int(self._path.stat().st_size)
        except OSError:
            return 0

    def _proxy_scale_label(self) -> str:
        return _PROXY_SCALE_LABELS.get(self._proxy_scale, str(self._proxy_scale))

    def _proxy_heavy_session_key(self, mode: str) -> str:
        if self._path is None:
            return ""
        from monostudio.core.video_proxy_cache import source_digest

        digest, _, _ = source_digest(self._path)
        return f"{digest}|{mode}|{self._proxy_scale}"

    def _source_frame_from_backend(self, sec: float) -> int:
        fps = max(1e-6, self._fps())
        if self._path is None or self._playback_path in (None, self._path):
            return sec_to_frame(sec, fps)
        if self._proxy_mode == "full" and self._full_proxy_manifest:
            return sec_to_frame(sec, fps)
        if self._proxy_mode == "range" and self._range_proxy_manifest:
            local = sec_to_frame(sec, fps)
            return self._range_proxy_manifest.in_frame + local
        return sec_to_frame(sec, fps)

    def _playback_clip_for_frame(self, frame: int) -> tuple[Path | None, bool]:
        if self._path is None or not self._proxy_enabled:
            return self._path, False
        if self._proxy_mode == "full" and self._full_proxy_ready and self._full_proxy_manifest:
            return Path(self._full_proxy_manifest.proxy_path), True
        if self._proxy_mode == "range" and self._range_proxy_manifest:
            m = self._range_proxy_manifest
            lo, hi = sorted((m.in_frame, m.out_frame))
            if lo <= frame <= hi:
                return Path(m.proxy_path), True
        return self._path, False

    def _backend_sec_for_source_frame(self, frame: int) -> float:
        fps = max(1e-6, self._fps())
        _, is_proxy = self._playback_clip_for_frame(frame)
        if not is_proxy:
            return frame / fps
        if self._proxy_mode == "full":
            return frame / fps
        if self._range_proxy_manifest:
            return (frame - self._range_proxy_manifest.in_frame) / fps
        return frame / fps

    def _apply_playback_for_frame(self, frame: int, *, precise: bool = False) -> None:
        """Resolve proxy vs source clip and seek (scrub + play)."""
        if self._is_sequence_mode():
            if self._seq_backend is not None:
                self._seq_backend.seek_frame(frame, exact=precise)
            return
        if self._path is None:
            return
        if self._proxy_enabled:
            self._ensure_playback_clip(frame)
            sec = self._backend_sec_for_source_frame(frame)
        else:
            if self._playback_path != self._path:
                self._ensure_source_at_frame(frame)
            sec = frame / max(1e-6, self._fps())
        self._backend.seek(sec, precise=precise)

    def _ensure_source_at_frame(self, frame: int) -> None:
        """Load source file for scrub — single timeline, no proxy offset."""
        if self._path is None:
            return
        if self._playback_path == self._path:
            return
        sec = frame / max(1e-6, self._fps())
        self._backend.load(self._path, start_sec=sec)
        self._playback_path = self._path
        self._backend.set_volume(0 if self._volume_muted else self._volume)
        self._backend.set_speed(self._speed)
        if self._info is not None:
            self._backend.configure_position_poll(self._info.fps)
        self._sync_playback_loop()

    def _ensure_playback_clip(self, frame: int) -> None:
        if self._path is None:
            return
        if not self._proxy_enabled:
            if self._playback_path != self._path:
                self._ensure_source_at_frame(frame)
            return
        clip_path, _ = self._playback_clip_for_frame(frame)
        if clip_path is None:
            return
        if self._playback_path == clip_path:
            return
        was_playing = self._backend.is_playing()
        sec = self._backend_sec_for_source_frame(frame)
        self._backend.load(clip_path, start_sec=sec)
        self._playback_path = clip_path
        self._backend.set_volume(0 if self._volume_muted else self._volume)
        self._backend.set_speed(self._speed)
        self._backend.configure_position_poll(self._info.fps if self._info else 24.0)
        self._sync_playback_loop()
        if was_playing:
            self._backend.play()
            self._set_play_icon(True)

    def _reload_source_at_frame(self, frame: int) -> None:
        if self._path is None:
            return
        sec = frame / max(1e-6, self._fps())
        was_playing = self._backend.is_playing()
        self._backend.stop()
        self._backend.load(self._path, start_sec=sec)
        self._playback_path = self._path
        self._backend.set_volume(0 if self._volume_muted else self._volume)
        self._backend.set_speed(self._speed)
        self._sync_playback_loop()
        if was_playing:
            self._backend.play()
            self._set_play_icon(True)
        else:
            self._backend.seek(sec, precise=True)

    def _detach_playback_for_proxy_build(self) -> None:
        """Release mpv handles on proxy cache files before FFmpeg writes."""
        if self._path is None:
            return
        if self._playback_path in (None, self._path):
            return
        self._reload_source_at_frame(self._current_frame())

    def _refresh_proxy_ruler(self) -> None:
        if self._path is None:
            self._scrubber.set_proxy_full_timeline(ready=False)
            self._scrubber.set_proxy_spans([])
            return
        spans = list_cached_range_spans(self._path, self._ranges, scale=self._proxy_scale)
        self._cached_spans = spans
        if self._proxy_mode == "full" and self._full_proxy_ready:
            self._scrubber.set_proxy_full_timeline(ready=True)
            self._scrubber.set_proxy_spans([])
        elif self._proxy_enabled:
            self._scrubber.set_proxy_full_timeline(ready=False)
            self._scrubber.set_proxy_spans(spans)
        else:
            self._scrubber.set_proxy_full_timeline(ready=False)
            self._scrubber.set_proxy_spans([])

    def _proxy_footer_tag(self) -> str | None:
        if not self._proxy_enabled or self._proxy_mode == "off":
            return None
        scale = self._proxy_scale_label()
        if self._proxy_mode == "full":
            return f"PROXY {scale} · Full"
        rng = self._active_range()
        if rng and rng.label.strip():
            return f'PROXY {scale} · "{rng.label.strip()}"'
        if rng:
            return f"PROXY {scale} · Range"
        return f"PROXY {scale}"

    def _set_proxy_build_overlay(self, visible: bool, *, label: str = "Building proxy…") -> None:
        overlay = getattr(self, "_proxy_build_overlay", None)
        if overlay is None:
            return
        if visible:
            # Build progress is shown on the timeline ruler — no viewer overlay.
            return
        overlay.hide()

    def _cancel_proxy_build(self) -> None:
        self._proxy_cancel_flag[0] = True
        self._proxy_build_token += 1
        self._scrubber.clear_proxy_build_progress()
        self._set_proxy_build_overlay(False)

    def _proxy_build_ruler_span(self, mode: Literal["full", "range"], rng: VideoFrameRange | None) -> tuple[int, int]:
        if mode == "full":
            return 0, max(0, self._total_frames() - 1)
        if rng is None:
            return 0, max(0, self._total_frames() - 1)
        lo, hi = sorted((rng.in_frame, rng.out_frame))
        return lo, hi

    def _start_proxy_build(self, mode: Literal["full", "range"], rng: VideoFrameRange | None = None) -> None:
        if self._path is None or self._info is None:
            return
        self._detach_playback_for_proxy_build()
        self._proxy_cancel_flag[0] = True
        self._proxy_build_token += 1
        token = self._proxy_build_token
        self._proxy_build_active_token = token
        self._proxy_cancel_flag[0] = False
        label = "Building full proxy…" if mode == "full" else "Building range proxy…"
        lo, hi = self._proxy_build_ruler_span(mode, rng)
        self._scrubber.set_proxy_build_progress(lo, hi, 0.0)
        self._set_proxy_build_overlay(True, label=label)
        self._status_log = label
        self._update_footer()
        QThreadPool.globalInstance().start(
            ProxyBuildRunnable(
                mode=mode,
                src=self._path,
                src_info=self._info,
                scale=self._proxy_scale,
                rng=rng,
                signaler=self._proxy_build_signaler,
                cancel_flag=self._proxy_cancel_flag,
            )
        )

    def _on_proxy_build_progress(self, fraction: float) -> None:
        frac = max(0.0, min(1.0, float(fraction)))
        self._scrubber.set_proxy_build_fraction(frac)

    def _on_proxy_build_finished(self, manifest_obj: object, error: object) -> None:
        token = self._proxy_build_active_token
        self._set_proxy_build_overlay(False)
        self._scrubber.clear_proxy_build_progress()
        if error:
            self._status_log = str(error)[:240]
            self._update_footer()
            if self._proxy_enabled and "cancel" not in str(error).lower():
                QMessageBox.warning(self, "Proxy build", str(error))
            return
        if token != self._proxy_build_active_token:
            return
        if not isinstance(manifest_obj, ProxyManifest):
            return
        manifest = manifest_obj
        if manifest.mode == "full":
            self._full_proxy_manifest = manifest
            self._full_proxy_ready = True
        elif self._active_range_id and manifest.range_id == self._active_range_id:
            self._range_proxy_manifest = manifest
        self._status_log = ""
        self._sync_proxy_state()
        if self._proxy_enabled:
            self._apply_playback_for_frame(self._current_frame())
        if self._proxy_enabled and self._backend.is_playing():
            sec = self._backend_sec_for_source_frame(self._current_frame())
            self._backend.seek(sec, precise=True)
        self._update_footer()

    def _maybe_confirm_heavy_full(self) -> bool:
        if self._path is None or self._info is None:
            return False
        key = self._proxy_heavy_session_key("full")
        if key and key == self._proxy_heavy_ack_key:
            return True
        heavy, reason = is_heavy_source_for_proxy(
            self._info,
            file_size_bytes=self._source_file_size(),
        )
        if not heavy:
            return True
        detail = format_heavy_proxy_message(
            self._info,
            file_size_bytes=self._source_file_size(),
            reason=reason,
        )
        detail = f"{detail} — may take several minutes."
        if ask_build_full_proxy(self, detail=detail):
            self._proxy_heavy_ack_key = key
            return True
        return False

    def _sync_proxy_state(self) -> None:
        self._cmb_proxy_scale.setEnabled(self._proxy_enabled)
        self._btn_proxy_menu.setEnabled(self._path is not None)
        if not self._proxy_enabled or self._path is None or self._info is None:
            self._proxy_mode = "off"
            self._cancel_proxy_build()
            self._scrubber.clear_proxy_build_progress()
            self._full_proxy_ready = False
            self._full_proxy_manifest = None
            self._range_proxy_manifest = None
            self._refresh_proxy_ruler()
            frame = self._current_frame()
            if self._playback_path != self._path:
                self._reload_source_at_frame(frame)
            self._update_footer()
            return

        if self._active_range_id:
            self._proxy_mode = "range"
            self._full_proxy_ready = is_full_proxy_ready(self._path, scale=self._proxy_scale)
            if self._full_proxy_ready:
                self._full_proxy_manifest = lookup_full_proxy(self._path, scale=self._proxy_scale)
            rng = self._active_range()
            self._refresh_proxy_ruler()
            if rng is None:
                return
            manifest = lookup_range_proxy(self._path, rng, scale=self._proxy_scale)
            if manifest:
                self._range_proxy_manifest = manifest
                self._apply_playback_for_frame(self._current_frame())
            else:
                self._range_proxy_manifest = None
                lo, hi = sorted((rng.in_frame, rng.out_frame))
                fps = max(1e-6, self._fps())
                range_sec = (hi - lo + 1) / fps
                heavy, reason = is_heavy_source_for_proxy(
                    self._info,
                    file_size_bytes=self._source_file_size(),
                )
                if heavy and range_sec > 120:
                    self._status_log = f"Heavy source — building range proxy ({reason})"
                self._start_proxy_build("range", rng)
        else:
            self._proxy_mode = "full"
            self._range_proxy_manifest = None
            manifest = lookup_full_proxy(self._path, scale=self._proxy_scale)
            if manifest:
                self._full_proxy_manifest = manifest
                self._full_proxy_ready = True
                self._refresh_proxy_ruler()
                self._apply_playback_for_frame(self._current_frame())
            else:
                self._full_proxy_ready = False
                self._full_proxy_manifest = None
                self._refresh_proxy_ruler()
                if not self._maybe_confirm_heavy_full():
                    self._chk_proxy.blockSignals(True)
                    self._chk_proxy.setChecked(False)
                    self._chk_proxy.blockSignals(False)
                    self._proxy_enabled = False
                    self._sync_proxy_state()
                    return
                self._start_proxy_build("full")
        self._update_footer()

    def _on_proxy_toggled(self, checked: bool) -> None:
        self._proxy_enabled = checked
        self._sync_proxy_state()

    def _on_proxy_scale_changed(self, _index: int) -> None:
        scale = self._cmb_proxy_scale.currentData()
        if scale is None:
            return
        self._proxy_scale = float(scale)
        if self._proxy_enabled:
            self._sync_proxy_state()

    def _show_proxy_menu(self) -> None:
        menu = MonosMenu(self)
        clear_act = menu.addAction("Clear proxy for this video")
        clear_act.triggered.connect(self._clear_proxy_for_current_video)
        position_popup_near_anchor(menu, self._btn_proxy_menu)
        menu.popup(menu.mapToGlobal(QPoint(0, 0)))

    def _clear_proxy_for_current_video(self) -> None:
        if self._path is None:
            return
        self._cancel_proxy_build()
        clear_proxy_cache_for_source(self._path)
        self._full_proxy_ready = False
        self._full_proxy_manifest = None
        self._range_proxy_manifest = None
        frame = self._current_frame()
        self._reload_source_at_frame(frame)
        if self._proxy_enabled:
            self._sync_proxy_state()
        else:
            self._refresh_proxy_ruler()
            self._update_footer()

    def _export(self) -> None:
        if not self._ranges:
            return
        if self._is_sequence_mode():
            if not self._sequence_frames or self._sequence_folder is None:
                return
            default_dir = self._sequence_folder.parent / f"{self._sequence_folder.name}_cuts"
            folder = QFileDialog.getExistingDirectory(
                self,
                "Export sequence ranges",
                str(default_dir if default_dir.is_dir() else self._sequence_folder.parent),
            )
            if not folder:
                return
            try:
                outs = export_sequence_ranges(
                    self._sequence_frames,
                    self._ranges,
                    Path(folder),
                    fps=self._fps(),
                )
            except Exception as exc:
                QMessageBox.warning(self, "Export failed", str(exc))
                return
            if outs:
                self._hud.setText(f"Exported {len(outs)} file(s)")
                self.export_completed.emit(outs)
            return
        if self._path is None or self._info is None:
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
