"""Tests for comp Loader range sync helpers."""

from __future__ import annotations

from monostudio.core.comp_loader_io import (
    parse_comp_global_range,
    parse_loader_block_clip_range,
    sync_loader_block_range,
    sync_loader_ranges_for_stems,
)


def test_parse_comp_global_range() -> None:
    text = "Composition {\n\tGlobalRange = { 0, 75 },\n}\n"
    assert parse_comp_global_range(text) == (0, 75)


def test_replace_comp_global_range() -> None:
    from monostudio.core.comp_loader_io import replace_comp_global_range

    text = "Composition {\n\tGlobalRange = { 0, 75 },\n\tRenderRange = { 0, 75 },\n}\n"
    out = replace_comp_global_range(text, 0, 55)
    assert "GlobalRange = { 0, 55 }" in out
    assert "RenderRange = { 0, 55 }" in out


def test_intersect_frame_ranges() -> None:
    from monostudio.core.comp_loader_io import intersect_frame_ranges

    assert intersect_frame_ranges([(0, 55), (0, 75)]) == (0, 55)
    assert intersect_frame_ranges([(10, 20), (0, 15)]) == (10, 15)
    assert intersect_frame_ranges([(0, 10), (20, 30)]) is None


def test_parse_loader_block_clip_range() -> None:
    block = (
        "Loader {\n"
        "\tClips = { Clip {\n"
        "\t\tTrimIn = 0,\n"
        "\t\tTrimOut = 75,\n"
        "\t\tGlobalStart = 0,\n"
        "\t\tGlobalEnd = 1,\n"
        "\t}, },\n"
        "}\n"
    )
    assert parse_loader_block_clip_range(block) == (0, 75)


def test_loader_block_range_matches_global() -> None:
    from monostudio.core.comp_loader_io import loader_block_range_matches_global

    block = "Clip { TrimIn = 0, TrimOut = 9, }"
    assert loader_block_range_matches_global(block, 0, 9)
    assert not loader_block_range_matches_global(block, 0, 75)


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


def test_sequence_folder_frame_extent(tmp_path: Path) -> None:
    from monostudio.core.sequence_preview import sequence_folder_frame_extent

    folder = tmp_path / "sh009_lighting_v002"
    folder.mkdir()
    (folder / "sh009_lighting_v002.0001.exr").write_bytes(b"x")
    (folder / "sh009_lighting_v002.0065.exr").write_bytes(b"x")
    (folder / "sh009_lighting_v002.0075.exr").write_bytes(b"x")
    (folder / "other.exr").write_bytes(b"x")
    assert sequence_folder_frame_extent(folder, base_prefix="sh009_lighting") == (1, 75)


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
