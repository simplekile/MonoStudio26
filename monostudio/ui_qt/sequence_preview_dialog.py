"""Flipbook dialog for playblast / preview sequences (worker decode + small RAM buffer)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.sequence_preview_decode import PREVIEW_MAX_SIDE_DEFAULT, load_preview_frame_qimage
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font
from PySide6.QtGui import QFont


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
    """

    _BUFFER_CAP = 6
    _PREFETCH_LIGHT = 3
    _PREFETCH_HEAVY = 1

    def __init__(
        self,
        frames: list[Path],
        *,
        sequence_folder: Path,
        fps: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._frames = list(frames)
        self._sequence_folder = sequence_folder
        self._fps = max(1, min(60, int(fps)))
        self._n = len(self._frames)
        self.setWindowTitle("Sequence preview")
        self.setMinimumSize(640, 480)
        self.resize(960, 540)

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

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumSize(320, 180)
        self._label.setStyleSheet(f"background-color: {MONOS_COLORS['content_bg']};")
        self._status = QLabel("", self)
        self._status.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        self._status.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")

        self._btn_play = QPushButton(self)
        self._btn_play.setObjectName("DialogSecondaryButton")
        self._btn_play.setIcon(lucide_icon("play", size=18, color_hex=MONOS_COLORS["text_label"]))
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_close = QPushButton("Close", self)
        self._btn_close.setObjectName("DialogSecondaryButton")
        self._btn_close.clicked.connect(self.close)

        row = QHBoxLayout()
        row.addWidget(self._btn_play)
        row.addStretch(1)
        row.addWidget(self._btn_close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._status)
        layout.addLayout(row)

        self._tick_timer = QTimer(self)
        self._tick_timer.setSingleShot(True)
        self._tick_timer.timeout.connect(self._on_tick)

        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(self._on_tick)

        if self._n == 0:
            self._status.setText("No frames")
            self._btn_play.setEnabled(False)
        else:
            self._status.setText("Loading…")
            self._request_decode(0)
            for k in range(1, min(self._prefetch_n + 1, self._n)):
                self._request_decode(k)

    def _detect_heavy_sequence(self) -> bool:
        heavy = {".exr", ".hdr"}
        return bool(self._frames) and all(p.suffix.lower() in heavy for p in self._frames)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._playing = False
        self._tick_timer.stop()
        self._poll_timer.stop()
        self._pool.clear()
        super().closeEvent(event)

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
            self._status.setText(f"Frame {idx + 1} / {self._n}")
        elif self._current == 0 and 0 not in self._buffer and not self._in_flight:
            self._status.setText("Could not decode frame")

    def _trim_buffer(self) -> None:
        while len(self._buffer) > self._BUFFER_CAP:
            # Drop furthest from current
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
            self._btn_play.setIcon(lucide_icon("pause", size=18, color_hex=MONOS_COLORS["text_label"]))
            self._schedule_next_tick()
        else:
            self._btn_play.setIcon(lucide_icon("play", size=18, color_hex=MONOS_COLORS["text_label"]))
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
            self._status.setText(f"Frame {self._current + 1} / {self._n}")
            for k in range(1, self._prefetch_n + 1):
                j = (self._current + k) % self._n
                self._request_decode(j)
            self._schedule_next_tick()
        else:
            self._request_decode(nxt)
            self._poll_timer.start(16)
