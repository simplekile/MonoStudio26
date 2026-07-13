"""SequenceDecodeBackend display readiness (flipbook smaller than viewport bucket)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.review_playback_backend import SequenceDecodeBackend


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_frame_meets_target_accepts_small_plate_at_current_bucket(qt_app) -> None:
    frames = [Path("frame_0001.exr")]
    backend = SequenceDecodeBackend(frames, fps=24)
    backend.set_viewport_size(1200, 800, 1.0)
    backend.prime_display()
    bucket = backend._target_decode_side()
    small = QPixmap(640, 360)
    assert not small.isNull()
    backend._buffer[0] = small
    backend._buffer_decode_side[0] = bucket
    assert backend._frame_meets_target(0)


def test_frame_meets_target_rejects_stale_smaller_bucket(qt_app) -> None:
    frames = [Path("frame_0001.exr")]
    backend = SequenceDecodeBackend(frames, fps=24)
    backend.set_viewport_size(400, 300, 1.0)
    backend.prime_display()
    small_bucket = backend._target_decode_side()
    backend.set_viewport_size(1200, 800, 1.0)
    backend._decode_bucket = backend._decode_bucket_for_label()
    large_bucket = backend._target_decode_side()
    assert large_bucket > small_bucket + 32
    pix = QPixmap(640, 360)
    backend._buffer[0] = pix
    backend._buffer_decode_side[0] = small_bucket
    assert not backend._frame_meets_target(0)
