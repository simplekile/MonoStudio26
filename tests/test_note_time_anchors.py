from monostudio.core.note_time_anchors import (
    format_marker_pill_label,
    format_range_pill_label,
    is_time_note_href,
    parse_time_href,
    parse_time_href_from_html,
    time_href_for_marker,
    time_href_for_playhead,
    time_href_for_range,
)
from monostudio.core.video_media import VideoFrameRange, VideoReviewMarker


def test_time_href_for_range() -> None:
    assert time_href_for_range("rng1", 120) == "monos-time:range/rng1/120"


def test_time_href_for_marker() -> None:
    assert time_href_for_marker("mk1", 45) == "monos-time:marker/mk1/45"


def test_parse_time_href_range() -> None:
    anchor = parse_time_href("monos-time:range/abc/180")
    assert anchor is not None
    assert anchor.kind == "range"
    assert anchor.ref_id == "abc"
    assert anchor.frame == 180


def test_parse_time_href_from_html() -> None:
    html = '<p>See <a href="monos-time:marker/m1/90">F0090 · Hero</a></p>'
    anchor = parse_time_href_from_html(html)
    assert anchor is not None
    assert anchor.kind == "marker"
    assert anchor.ref_id == "m1"
    assert anchor.frame == 90


def test_is_time_note_href() -> None:
    assert is_time_note_href("monos-time:frame/12")
    assert not is_time_note_href("monos-mention:user/1")


def test_time_href_for_playhead() -> None:
    assert time_href_for_playhead(120) == "monos-time:playhead/120"


def test_parse_time_href_playhead() -> None:
    anchor = parse_time_href("monos-time:playhead/90")
    assert anchor is not None
    assert anchor.kind == "playhead"
    assert anchor.frame == 90
    assert anchor.ref_id == "playhead"


def test_format_range_pill_label() -> None:
    rng = VideoFrameRange("id", 120, 180, "Blocking")
    assert format_range_pill_label(rng, 24.0) == "0120–0180 · Blocking"


def test_format_marker_pill_label() -> None:
    marker = VideoReviewMarker("id", 30, "Beat", 0.0)
    assert format_marker_pill_label(marker, 24.0) == "F0030 · Beat"
