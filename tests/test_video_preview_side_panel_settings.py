from PySide6.QtCore import QSettings

from monostudio.ui_qt.video_preview_settings import (
    read_review_note_rail_open,
    read_review_note_rail_width,
    read_review_tools_panel_width,
    write_review_note_rail_open,
    write_review_note_rail_width,
    write_review_tools_panel_width,
)


def test_review_side_panel_layout_roundtrip() -> None:
    settings = QSettings("MonoStudio26Test", "SidePanelLayout")
    settings.clear()
    profile = "entity"

    write_review_note_rail_open(settings, profile, True)
    write_review_note_rail_width(settings, profile, 312, min_w=200, max_w=480)
    write_review_tools_panel_width(settings, profile, 288, min_w=200, max_w=480)

    assert read_review_note_rail_open(settings, profile=profile) is True
    assert read_review_note_rail_width(settings, profile=profile) == 312
    assert read_review_tools_panel_width(settings, profile=profile) == 288
