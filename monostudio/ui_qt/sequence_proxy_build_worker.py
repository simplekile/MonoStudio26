"""Background PNG proxy build worker for image sequences in Video Preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from monostudio.core.sequence_proxy import build_sequence_proxy
from monostudio.core.sequence_proxy_cache import SequenceProxyManifest


class SequenceProxyBuildSignaler(QObject):
    progress = Signal(float)
    frame_built = Signal(int, float)  # frame index, overall fraction
    finished = Signal(object, object)  # SequenceProxyManifest | None, error str | None


def _signaler_alive(signaler: QObject | None) -> bool:
    if signaler is None:
        return False
    try:
        from shiboken6 import isValid

        return bool(isValid(signaler))
    except Exception:
        return False


class SequenceProxyBuildRunnable(QRunnable):
    def __init__(
        self,
        *,
        frames: list[Path],
        scale: float,
        ocio_token: str,
        signaler: SequenceProxyBuildSignaler,
        cancel_flag: list[bool],
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._frames = list(frames)
        self._scale = scale
        self._ocio_token = ocio_token
        self._signaler = signaler
        self._cancel_flag = cancel_flag

    def _emit_progress(self, fraction: float) -> None:
        if not _signaler_alive(self._signaler):
            return
        try:
            self._signaler.progress.emit(float(fraction))
        except RuntimeError:
            return

    def _emit_frame_built(self, frame: int, fraction: float) -> None:
        if not _signaler_alive(self._signaler):
            return
        try:
            self._signaler.frame_built.emit(int(frame), float(fraction))
        except RuntimeError:
            return

    def _emit_finished(self, manifest: SequenceProxyManifest | None, error: str | None) -> None:
        if not _signaler_alive(self._signaler):
            return
        try:
            self._signaler.finished.emit(manifest, error)
        except RuntimeError:
            return

    def run(self) -> None:
        total = max(1, len(self._frames))

        def _on_frame_built(idx: int) -> None:
            self._emit_frame_built(idx, (idx + 1) / total)

        try:
            manifest = build_sequence_proxy(
                self._frames,
                scale=self._scale,
                ocio_token=self._ocio_token,
                progress_callback=self._emit_progress,
                frame_built_callback=_on_frame_built,
                cancel_check=self._cancelled,
            )
            self._emit_finished(manifest, None)
        except Exception as e:
            if self._cancelled() or "cancel" in str(e).lower():
                self._emit_finished(None, "Sequence proxy build cancelled")
            else:
                self._emit_finished(None, str(e))

    def _cancelled(self) -> bool:
        return bool(self._cancel_flag and self._cancel_flag[0])
