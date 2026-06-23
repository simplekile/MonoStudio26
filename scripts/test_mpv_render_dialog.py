"""Reproduce VideoPreviewDialog + mpv_render on Windows."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.DEBUG)

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.video_player_backend import BACKEND_MPV
from monostudio.ui_qt.video_preview_dialog import VideoPreviewDialog
from monostudio.ui_qt.video_preview_settings import (
    write_review_use_gpu_compositor,
    write_video_player_backend,
)

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
settings = QSettings("MonoStudio26", "MonoStudio26")
write_review_use_gpu_compositor(settings, True)
write_video_player_backend(settings, BACKEND_MPV)

video = Path(
    r"D:/Dropbox/0thers.Perspectives/-1305039768627426641training_dance.MP4"
)
if not video.is_file():
    print("sample video missing", flush=True)
    raise SystemExit(1)

print("creating dialog...", flush=True)
dlg = VideoPreviewDialog(video, sibling_paths=[video], settings=settings)
print("backend:", dlg._backend.name, flush=True)
dlg.show()

_tick = {"n": 0}


def _status() -> None:
    i = _tick["n"]
    _tick["n"] += 1
    rw = getattr(dlg._backend, "_render_widget", None)
    render_ready = rw.is_render_ready() if rw is not None else False
    boot = getattr(dlg._backend, "_render_bootstrapped", False)
    print(
        f"tick {i:02d} attached={dlg._video_attached} "
        f"file_ready={dlg._backend.file_ready()} "
        f"render_ready={render_ready} boot={boot}",
        flush=True,
    )
    if i < 60:
        QTimer.singleShot(100, _status)
    else:
        app.quit()


QTimer.singleShot(500, _status)
app.exec()
print("done ok", flush=True)
