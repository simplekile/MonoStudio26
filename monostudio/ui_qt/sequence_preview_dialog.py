"""Flipbook dialog for playblast / preview sequences (worker decode + small RAM buffer)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFont, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from monostudio.core.video_media import (
    VideoFrameRange,
    format_frame_label,
    load_sequence_ranges_for_preview,
    new_range_id,
    ranges_content_equal,
    save_sequence_ranges_local_draft,
    save_sequence_ranges_sidecar,
    validate_range,
)
from monostudio.ui_qt.dialog_geometry import apply_dialog_geometry, main_window_bounds
from monostudio.ui_qt.inspector_preview_settings import write_sequence_preview_fps
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.media_preview_transport import BasicMediaTransportRow
from monostudio.ui_qt.review_tools_panel import ReviewToolMode, ReviewToolsPanel, ReviewWorkspace
from monostudio.ui_qt.sequence_preview_decode import PREVIEW_MAX_SIDE_DEFAULT, load_preview_frame_qimage
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font
from monostudio.ui_qt.video_preview_context import PreviewContext

logger = logging.getLogger(__name__)


class _DecodeSignaler(QObject):
    frame_ready = Signal(int, object)  # index, QImage | None


class _DecodeRunnable(QRunnable):
    def __init__(self, idx: int, path: Path, max_side: int, signaler: _DecodeSignaler) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._idx = idx
        self._path = path
        self._max_side = max_side
        self._signaler = signaler

    def run(self) -> None:
        img = load_preview_frame_qimage(self._path, self._max_side)
        self._signaler.frame_ready.emit(self._idx, img)


class SequencePreviewDialog(MonosDialog):
    """
    Play / pause flipbook with decode on thread pool; back-pressure when decode lags.
    Entity context supports local/published frame ranges.
    """

    closed = Signal()

    _BUFFER_CAP = 6
    _PREFETCH_LIGHT = 3
    _PREFETCH_HEAVY = 1

    def __init__(
        self,
        frames: list[Path],
        *,
        sequence_folder: Path,
        fps: int,
        entity_path: Path | None = None,
        settings=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._frames = list(frames)
        self._sequence_folder = sequence_folder
        self._fps = max(1, min(60, int(fps)))
        self._n = len(self._frames)
        self._settings = settings
        self._geometry_applied = False
        self._locked_size: QSize | None = None
        self.setWindowTitle(sequence_folder.name or "Sequence preview")
        self.setMinimumSize(960, 540)

        self._current = 0
        self._playing = False
        self._buffer: dict[int, QPixmap] = {}
        self._in_flight: set[int] = set()
        self._signaler = _DecodeSignaler(self)
        self._signaler.frame_ready.connect(self._on_frame_ready, Qt.ConnectionType.QueuedConnection)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)

        self._heavy = self._detect_heavy_sequence()
        self._prefetch_n = self._PREFETCH_HEAVY if self._heavy else self._PREFETCH_LIGHT
        self._label_full_pix: QPixmap | None = None

        self._ranges: list[VideoFrameRange] = []
        self._published_ranges: list[VideoFrameRange] = []
        self._active_range_id: str | None = None
        self._draft_in: int | None = None
        self._draft_out: int | None = None

        self._build_ui()
        self._bind_shortcuts()
        self._load_ranges()

        self._tick_timer = QTimer(self)
        self._tick_timer.setSingleShot(True)
        self._tick_timer.timeout.connect(self._on_tick)
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(self._on_tick)

        self._apply_dialog_geometry_once()
        self.finished.connect(lambda _: self.closed.emit())

        if self._n == 0:
            self._status.setText("No frames")
            self._transport.btn_play.setEnabled(False)
        else:
            self._status.setText("Loading…")
            self._request_decode(0)
            for k in range(1, min(self._prefetch_n + 1, self._n)):
                self._request_decode(k)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self._meta = QLabel("", self)
        self._meta.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
        self._meta.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')};")
        left.addWidget(self._meta, 0)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumSize(320, 180)
        self._label.setStyleSheet(f"background-color: {MONOS_COLORS['content_bg']};")
        left.addWidget(self._label, 1)

        self._status = QLabel("", self)
        self._status.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Medium))
        left.addWidget(self._status, 0)

        self._transport = BasicMediaTransportRow(self)
        self._transport.btn_play.clicked.connect(self._toggle_play)
        self._transport.btn_close.clicked.connect(self.close)

        row_actions = QHBoxLayout()
        self._fps_label = QLabel("FPS", self)
        self._fps_label.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        self._fps_label.setStyleSheet(f"color: {MONOS_COLORS.get('text_label', '#a1a1aa')};")
        self._fps_spin = QSpinBox(self)
        self._fps_spin.setObjectName("SequencePreviewFpsSpin")
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setValue(self._fps)
        self._fps_spin.setFixedWidth(56)
        self._fps_spin.setToolTip("Playback frame rate for this sequence")
        self._fps_spin.valueChanged.connect(self._on_fps_changed)
        row_actions.addWidget(self._fps_label)
        row_actions.addWidget(self._fps_spin)
        row_actions.addSpacing(12)
        self._btn_in = QPushButton("In", self)
        self._btn_in.setObjectName("DialogSecondaryButton")
        self._btn_in.clicked.connect(self._mark_in)
        self._btn_out = QPushButton("Out", self)
        self._btn_out.setObjectName("DialogSecondaryButton")
        self._btn_out.clicked.connect(self._mark_out)
        self._btn_add = QPushButton("+ Range", self)
        self._btn_add.setObjectName("DialogSecondaryButton")
        self._btn_add.clicked.connect(self._add_range)
        self._btn_sync = QPushButton("Sync", self)
        self._btn_sync.setObjectName("DialogPrimaryButton")
        self._btn_sync.clicked.connect(self._sync_ranges)
        row_actions.addWidget(self._btn_in)
        row_actions.addWidget(self._btn_out)
        row_actions.addWidget(self._btn_add)
        row_actions.addStretch(1)
        row_actions.addWidget(self._btn_sync)
        left.addWidget(self._transport, 0)
        left.addLayout(row_actions)
        body.addLayout(left, 1)

        self._tools_panel = ReviewToolsPanel(self)
        self._tools_panel.apply_context(PreviewContext.entity)
        self._tools_panel.set_workspace(ReviewWorkspace.tools)
        self._tools_panel.activate_tool_mode(ReviewToolMode.ranges)
        self._tools_panel.range_selected.connect(self._on_range_selected)
        self._tools_panel.range_delete_requested.connect(self._delete_range)
        self._tools_panel.range_duplicate_requested.connect(self._duplicate_range)
        self._tools_panel.range_label_changed.connect(self._on_range_label_changed)
        self._tools_panel.go_to_in_requested.connect(self._go_to_range_in)
        self._tools_panel.go_to_out_requested.connect(self._go_to_range_out)
        body.addWidget(self._tools_panel, 0)
        root.addLayout(body, 1)
        self._update_meta()

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self._mark_in)
        QShortcut(QKeySequence(Qt.Key.Key_O), self, self._mark_out)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, self._add_range)

    def _update_meta(self) -> None:
        self._meta.setText(f"{self._n} frames  ·  {self._fps} fps  ·  {self._sequence_folder.name}")

    def _on_fps_changed(self, value: int) -> None:
        self._fps = max(1, min(60, int(value)))
        self._update_meta()
        self._sync_range_ui()
        if self._settings is not None:
            write_sequence_preview_fps(self._settings, self._fps)
        if self._playing:
            self._schedule_next_tick()

    def _load_ranges(self) -> None:
        published, working, _local = load_sequence_ranges_for_preview(
            self._sequence_folder,
            total_frames=max(1, self._n),
        )
        self._published_ranges = published
        self._ranges = working
        if self._ranges:
            self._active_range_id = self._ranges[0].id
        self._sync_range_ui()

    def _sync_range_ui(self) -> None:
        panel = self._tools_panel.range_list_widget()
        panel.set_fps(float(self._fps))
        panel.set_published_ranges(self._published_ranges)
        panel.set_ranges(self._ranges, active_id=self._active_range_id)
        panel.set_draft_hint(self._draft_in, self._draft_out)
        rng = self._range_by_id(self._active_range_id) if self._active_range_id else None
        self._tools_panel.set_active_range_id(self._active_range_id, label=(rng.label if rng else ""))
        dirty = not ranges_content_equal(self._ranges, self._published_ranges)
        self._btn_sync.setEnabled(dirty)
        if dirty:
            self._btn_sync.setToolTip("Sync local range changes to project sidecar")
        else:
            self._btn_sync.setToolTip("All ranges synced with project")

    def _range_by_id(self, range_id: str | None) -> VideoFrameRange | None:
        if not range_id:
            return None
        return next((r for r in self._ranges if r.id == range_id), None)

    def _persist_local(self) -> None:
        try:
            save_sequence_ranges_local_draft(self._sequence_folder, self._ranges)
        except Exception:
            logger.warning("save sequence range draft failed", exc_info=True)

    def _sync_ranges(self) -> None:
        try:
            save_sequence_ranges_sidecar(self._sequence_folder, self._ranges)
            save_sequence_ranges_local_draft(self._sequence_folder, self._ranges)
        except Exception:
            logger.warning("sync sequence ranges failed", exc_info=True)
            return
        self._published_ranges = list(self._ranges)
        self._sync_range_ui()

    def _mark_in(self) -> None:
        self._draft_in = self._current
        self._sync_range_ui()

    def _mark_out(self) -> None:
        self._draft_out = self._current
        self._sync_range_ui()

    def _add_range(self) -> None:
        if self._draft_in is None or self._draft_out is None:
            return
        in_f, out_f = self._draft_in, self._draft_out
        if in_f > out_f:
            in_f, out_f = out_f, in_f
        if not validate_range(in_f, out_f, total_frames=max(1, self._n)):
            return
        rid = new_range_id()
        self._ranges.append(VideoFrameRange(rid, in_f, out_f))
        self._active_range_id = rid
        self._draft_in = None
        self._draft_out = None
        self._sync_range_ui()
        self._persist_local()
        self._seek_frame(in_f)
        self._tools_panel.focus_range_name_field()

    def _on_range_selected(self, range_id: str) -> None:
        self._active_range_id = range_id
        self._sync_range_ui()
        rng = self._range_by_id(range_id)
        if rng is not None:
            self._seek_frame(rng.in_frame)

    def _delete_range(self, range_id: str) -> None:
        self._ranges = [r for r in self._ranges if r.id != range_id]
        if self._active_range_id == range_id:
            self._active_range_id = self._ranges[0].id if self._ranges else None
        self._sync_range_ui()
        self._persist_local()

    def _duplicate_range(self, range_id: str) -> None:
        rng = self._range_by_id(range_id)
        if rng is None:
            return
        rid = new_range_id()
        label = f"{rng.label} (copy)" if rng.label else ""
        self._ranges.append(VideoFrameRange(rid, rng.in_frame, rng.out_frame, label))
        self._active_range_id = rid
        self._sync_range_ui()
        self._persist_local()

    def _on_range_label_changed(self, range_id: str, label: str) -> None:
        updated: list[VideoFrameRange] = []
        for rng in self._ranges:
            if rng.id == range_id:
                updated.append(VideoFrameRange(rng.id, rng.in_frame, rng.out_frame, label.strip()[:80]))
            else:
                updated.append(rng)
        self._ranges = updated
        self._sync_range_ui()
        self._persist_local()

    def _go_to_range_in(self, range_id: str) -> None:
        self._on_range_selected(range_id)

    def _go_to_range_out(self, range_id: str) -> None:
        self._active_range_id = range_id
        self._sync_range_ui()
        rng = self._range_by_id(range_id)
        if rng is not None:
            self._seek_frame(rng.out_frame)

    def _seek_frame(self, frame: int) -> None:
        if self._n <= 0:
            return
        idx = max(0, min(self._n - 1, int(frame)))
        self._current = idx
        if idx in self._buffer:
            self._apply_pixmap(self._buffer[idx])
        else:
            self._request_decode(idx)
        self._status.setText(f"{format_frame_label(idx)} / {self._n - 1}  ·  {idx + 1}/{self._n}")

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
            default_fraction=0.95,
            min_size=QSize(960, 540),
            lock_size=True,
            margin=4,
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_dialog_geometry_once()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._playing = False
        self._tick_timer.stop()
        self._poll_timer.stop()
        self._pool.clear()
        self._persist_local()
        if self._settings is not None:
            write_sequence_preview_fps(self._settings, self._fps)
        super().closeEvent(event)

    def _detect_heavy_sequence(self) -> bool:
        heavy = {".exr", ".hdr"}
        return bool(self._frames) and all(p.suffix.lower() in heavy for p in self._frames)

    def _request_decode(self, idx: int) -> None:
        if idx < 0 or idx >= self._n:
            return
        if idx in self._buffer or idx in self._in_flight:
            return
        self._in_flight.add(idx)
        self._pool.start(_DecodeRunnable(idx, self._frames[idx], PREVIEW_MAX_SIDE_DEFAULT, self._signaler))

    def _on_frame_ready(self, idx: int, image: object) -> None:
        self._in_flight.discard(idx)
        if idx < 0 or idx >= self._n:
            return
        if isinstance(image, QImage) and not image.isNull():
            pix = QPixmap.fromImage(image)
            if not pix.isNull():
                self._buffer[idx] = pix
                self._trim_buffer()
        if idx == self._current and idx in self._buffer:
            self._apply_pixmap(self._buffer[idx])
            self._status.setText(f"{format_frame_label(idx)} / {self._n - 1}  ·  {idx + 1}/{self._n}")
        elif self._current == 0 and 0 not in self._buffer and not self._in_flight:
            self._status.setText("Could not decode frame")

    def _trim_buffer(self) -> None:
        while len(self._buffer) > self._BUFFER_CAP:
            best_k = None
            best_d = -1
            for k in self._buffer:
                d = abs(k - self._current)
                if d > best_d:
                    best_d = d
                    best_k = k
            if best_k is not None:
                del self._buffer[best_k]

    def _apply_pixmap(self, pix: QPixmap) -> None:
        self._label_full_pix = pix
        if pix.isNull():
            return
        self._label.setPixmap(
            pix.scaled(
                self._label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._label_full_pix is not None and not self._label_full_pix.isNull():
            self._label.setPixmap(
                self._label_full_pix.scaled(
                    self._label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        elif self._current in self._buffer:
            self._apply_pixmap(self._buffer[self._current])

    def _toggle_play(self) -> None:
        if self._n <= 0:
            return
        self._playing = not self._playing
        if self._playing:
            self._transport.btn_play.setIcon(lucide_icon("pause", size=18, color_hex=MONOS_COLORS["text_label"]))
            self._schedule_next_tick()
        else:
            self._transport.btn_play.setIcon(lucide_icon("play", size=18, color_hex=MONOS_COLORS["text_label"]))
            self._tick_timer.stop()
            self._poll_timer.stop()

    def _schedule_next_tick(self) -> None:
        if not self._playing:
            return
        ms = max(1, round(1000 / self._fps))
        self._tick_timer.start(ms)

    def _on_tick(self) -> None:
        if not self._playing or self._n <= 0:
            return
        nxt = (self._current + 1) % self._n
        if nxt in self._buffer:
            self._current = nxt
            self._apply_pixmap(self._buffer[self._current])
            self._status.setText(f"{format_frame_label(self._current)} / {self._n - 1}  ·  {self._current + 1}/{self._n}")
            for k in range(1, self._prefetch_n + 1):
                j = (self._current + k) % self._n
                self._request_decode(j)
            self._schedule_next_tick()
        else:
            self._request_decode(nxt)
            self._poll_timer.start(16)
