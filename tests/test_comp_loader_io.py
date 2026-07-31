"""Tests for comp Loader range sync helpers."""

from __future__ import annotations

from monostudio.core.comp_loader_io import (
    parse_comp_global_range,
    sync_loader_block_range,
    sync_loader_ranges_for_stems,
)


def test_parse_comp_global_range() -> None:
    text = "Composition {\n\tGlobalRange = { 0, 75 },\n}\n"
    assert parse_comp_global_range(text) == (0, 75)


def test_sync_loader_block_range() -> None:
    block = (
        "Loader {\n"
        "\tClip = Clip {\n"
        "\t\tTrimIn = 0,\n"
        "\t\tTrimOut = 9,\n"
        "\t\tLength = 10,\n"
        "\t\tGlobalStart = 0,\n"
        "\t\tGlobalEnd = 9,\n"
        "\t},\n"
        "}\n"
    )
    out = sync_loader_block_range(block, 0, 75)
    assert "TrimIn = 0" in out
    assert "TrimOut = 75" in out
    assert "Length = 76" in out
    assert "GlobalEnd = 75" in out


def test_normalize_render_path_versions() -> None:
    from monostudio.core.comp_loader_io import normalize_render_path_versions

    path = r"D:\proj\render\sh009_lighting_v002\sh009_lighting_v003.0065.exr"
    out = normalize_render_path_versions(path, "sh009_lighting", 4)
    assert out == r"D:\proj\render\sh009_lighting_v004\sh009_lighting_v004.0065.exr"


def test_sync_loader_ranges_for_stems() -> None:
    comp = (
        "Composition {\n"
        "\tGlobalRange = { 0, 75 },\n"
        "\tTools = {\n"
        "\t\tL1 = Loader {\n"
        '\t\t\tClips = { Clip { Filename = "D:\\\\render\\\\sh009_lighting_v002\\\\x.exr", '
        "TrimIn = 0, TrimOut = 9, Length = 10, GlobalStart = 0, GlobalEnd = 9, } },\n"
        "\t\t},\n"
        "\t\tL2 = Loader {\n"
        '\t\t\tClips = { Clip { Filename = "D:\\\\other\\\\plate.exr", '
        "TrimIn = 0, TrimOut = 9, Length = 10, GlobalStart = 0, GlobalEnd = 9, } },\n"
        "\t\t},\n"
        "\t},\n"
        "}\n"
    )
    out = sync_loader_ranges_for_stems(comp, ["sh009_lighting_v002"], 0, 75)
    assert "sh009_lighting_v002" in out
    assert "TrimOut = 75" in out
    # L2 unchanged
    assert out.count("TrimOut = 9") == 1
