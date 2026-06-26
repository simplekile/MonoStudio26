from pathlib import Path

from monostudio.core.video_media import _report_export_progress


def test_report_export_progress_scales_steps():
    seen: list[tuple[int, int, Path | None]] = []

    def cb(cur: int, total: int, path: Path | None) -> None:
        seen.append((cur, total, path))

    dst = Path("clip_001.mp4")
    _report_export_progress(cb, step_done=0.0, total_steps=2, path=dst)
    _report_export_progress(cb, step_done=0.5, total_steps=2, path=dst)
    _report_export_progress(cb, step_done=1.0, total_steps=2, path=dst)
    _report_export_progress(cb, step_done=2.0, total_steps=2, path=dst)

    assert seen == [
        (0, 200, dst),
        (50, 200, dst),
        (100, 200, dst),
        (200, 200, dst),
    ]
