"""Smoke tests for video preview (headless where possible). Run: python scripts/test_video_preview_smoke.py"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Project root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from monostudio.core.ffmpeg_resolve import resolve_ffmpeg_executable
from monostudio.core.mpv_resolve import mpv_available, resolve_mpv_dll
from monostudio.core.video_media import (
    VideoFrameRange,
    export_video_ranges,
    is_video_path,
    list_video_siblings,
    probe_video,
)
from monostudio.ui_qt.thumbnails import is_video_preview_path
from monostudio.ui_qt.video_player_backend import (
    BACKEND_QT,
    QtMultimediaBackend,
    create_video_player_backend,
)
from monostudio.ui_qt.video_preview_settings import write_video_player_backend


def _find_sample_video() -> Path | None:
    candidates = [
        Path(r"D:\Dropbox\0thers.Perspectives\-1305039768627426641training_dance.MP4"),
    ]
    for base in (Path(r"D:\Dropbox"), Path(r"E:\00 Project\Pipeline\MonoStudio26")):
        if not base.exists():
            continue
        try:
            for p in base.rglob("*.mp4"):
                if p.is_file() and p.stat().st_size < 80_000_000:
                    return p
            for p in base.rglob("*.MP4"):
                if p.is_file() and p.stat().st_size < 80_000_000:
                    return p
        except OSError:
            pass
    for c in candidates:
        if c.is_file():
            return c
    return None


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("MonoStudio26", "MonoStudio26")
    failures: list[str] = []
    passed: list[str] = []

    def ok(msg: str) -> None:
        passed.append(msg)
        print(f"  OK  {msg}")

    def fail(msg: str) -> None:
        failures.append(msg)
        print(f"  FAIL {msg}")

    print("=== Environment ===")
    ff = resolve_ffmpeg_executable(settings)
    dll = resolve_mpv_dll(settings)
    print(f"  ffmpeg: {ff or 'NOT FOUND'}")
    print(f"  mpv-2.dll: {dll or 'NOT FOUND'}")
    print(f"  mpv_available: {mpv_available(settings)}")

    sample = _find_sample_video()
    if sample is None:
        fail("No sample video found on disk")
    else:
        ok(f"Sample video: {sample.name} ({sample.stat().st_size // 1024} KB)")

    print("\n=== Path helpers ===")
    if sample:
        if is_video_path(sample) and is_video_preview_path(sample):
            ok("is_video_path / is_video_preview_path")
        else:
            fail("video path detection")

    print("\n=== ffprobe ===")
    if sample:
        try:
            info = probe_video(sample)
            if info is None:
                fail("probe_video returned None")
            else:
                ok(f"probe fps={info.fps:.3f} frames={info.frame_count} dur={info.duration_sec:.2f}s")
                sibs = list_video_siblings(sample)
                ok(f"list_video_siblings -> {len(sibs)} files")
        except Exception as e:
            fail(f"probe_video: {e}")

    print("\n=== Qt Multimedia backend ===")
    try:
        write_video_player_backend(settings, BACKEND_QT)
        backend = create_video_player_backend(settings)
        if backend.name != "qt":
            fail(f"expected qt backend, got {backend.name}")
        else:
            ok(f"create_video_player_backend -> {backend.name}")
        if sample and isinstance(backend, QtMultimediaBackend):
            from PySide6.QtWidgets import QWidget

            w = QWidget()
            w.resize(640, 360)
            backend.attach_to_widget(w)
            backend.load(sample)
            for _ in range(20):
                app.processEvents()
            dur = backend.duration()
            if dur > 0:
                ok(f"Qt load duration={dur:.2f}s")
            else:
                fail("Qt duration is 0 (offscreen may not report duration — try manual test)")
            backend.release()
    except Exception as e:
        fail(f"Qt backend: {e}")

    print("\n=== VideoPreviewDialog (offscreen) ===")
    if sample:
        try:
            from monostudio.ui_qt.video_preview_dialog import VideoPreviewDialog

            write_video_player_backend(settings, BACKEND_QT)
            dlg = VideoPreviewDialog(sample, sibling_paths=[sample], settings=settings)
            dlg.show()
            app.processEvents()

            if dlg.windowTitle():
                ok("dialog constructed and shown")
            else:
                ok("dialog shown")

            def _close_dialog() -> None:
                dlg.close()
                app.quit()

            QTimer.singleShot(800, _close_dialog)
            app.exec()
            ok("dialog closed cleanly")
        except Exception as e:
            fail(f"VideoPreviewDialog: {e}")

    print("\n=== FFmpeg export (stream copy, 1 range) ===")
    if sample and ff:
        try:
            info = probe_video(sample)
            if info is None:
                fail("probe for export")
            else:
                out_end = min(30, max(1, info.frame_count - 1))
                rng = VideoFrameRange(id="t1", in_frame=0, out_frame=out_end, label="smoke")
                out_dir = Path(tempfile.mkdtemp(prefix="monos_vid_export_"))
                paths = export_video_ranges(
                    sample,
                    [rng],
                    out_dir,
                    fps=info.fps,
                    mode="separate",
                    reencode=False,
                )
                if paths and paths[0].is_file() and paths[0].stat().st_size > 0:
                    ok(f"export separate -> {paths[0].name} ({paths[0].stat().st_size // 1024} KB)")
                else:
                    fail("export produced no file")
        except Exception as e:
            fail(f"export_video_ranges: {e}")
    elif not ff:
        fail("ffmpeg missing — skip export test")

    print("\n=== Summary ===")
    print(f"  Passed: {len(passed)}")
    print(f"  Failed: {len(failures)}")
    for f in failures:
        print(f"    - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
