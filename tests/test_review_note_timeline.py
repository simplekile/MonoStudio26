from monostudio.ui_qt.review_note_timeline import parse_note_anchor_frame


def test_parse_note_anchor_frame_from_playhead_prefix() -> None:
    text = "[0120 · 00:00:05.00 · Lighting] fix shoulder"
    assert parse_note_anchor_frame(text) == 120


def test_parse_note_anchor_frame_from_playhead_pill_html() -> None:
    html = '<p><a href="monos-time:playhead/120"> F0120 · 00:00:05.00 </a> fix this</p>'
    assert parse_note_anchor_frame("", body_html=html) == 120


def test_parse_note_anchor_frame_from_range_chip() -> None:
    text = "see [0120–0180 · Blocking v2] here"
    assert parse_note_anchor_frame(text) == 120



def test_parse_note_anchor_frame_from_html_prefix() -> None:
    html = '<p style="color:#71717a;">[0300 · 00:00:12.50]</p><p>note body</p>'
    assert parse_note_anchor_frame("", body_html=html) == 300


def test_parse_note_anchor_frame_from_time_pill_html() -> None:
    html = '<p><a href="monos-time:range/r1/240">0240–0300 · Act 2</a></p>'
    assert parse_note_anchor_frame("", body_html=html) == 240
