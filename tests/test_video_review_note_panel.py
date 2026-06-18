from monostudio.core.video_media import VideoFrameRange
from monostudio.ui_qt.video_review_note_panel import format_range_note_reference


def test_format_range_note_reference_with_label() -> None:
    rng = VideoFrameRange("abc", 120, 180, "Blocking v2")
    assert format_range_note_reference(rng, 24.0) == "[0120–0180 · Blocking v2]"


def test_format_range_note_reference_without_label() -> None:
    rng = VideoFrameRange("abc", 0, 10, "")
    assert format_range_note_reference(rng, 24.0) == "[0000–0010]"
