"""Tests for comp render path conventions."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.comp_render_paths import (
    build_comp_saver_spec,
    fusion_saver_path,
    saver_paths_match,
    try_build_comp_saver_spec_from_work_file,
)


def test_fusion_saver_path() -> None:
    path = fusion_saver_path(
        fusion_path_map="Comp",
        render_dir_relative="render/sh009_comp_v002",
        stem="sh009_comp_v002",
    )
    assert path == r"Comp:\render\sh009_comp_v002\sh009_comp_v002.0000.exr"


def test_saver_paths_match_slash_and_case() -> None:
    a = r"Comp:\render\sh009_comp_v002\sh009_comp_v002.0000.exr"
    b = "Comp:/render/sh009_comp_v002/sh009_comp_v002.0000.exr"
    assert saver_paths_match(a, b)


def test_saver_paths_match_frame_token_variants() -> None:
    expected = r"Comp:\render\sh002_comp_v006\sh002_comp_v006.####.exr"
    fusion = r"Comp:\render\sh002_comp_v006\sh002_comp_v006.0000.exr"
    assert saver_paths_match(expected, fusion)


def test_build_comp_saver_spec(tmp_path: Path) -> None:
    work = tmp_path / "fusion" / "work"
    work.mkdir(parents=True)
    work_file = work / "sh009_comp_v002.comp"
    work_file.write_text("Composition { }\n", encoding="utf-8")
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=work_file,
        work_path=work,
    )
    assert spec.prefix == "sh009_comp"
    assert spec.stem == "sh009_comp_v002"
    assert spec.work_version == 2
    assert spec.render_dir_absolute == (work / "render" / "sh009_comp_v002").resolve()
    assert "sh009_comp_v002" in spec.saver_path_fusion


def test_try_build_from_work_file(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh010_comp_v001.comp"
    comp.write_text("", encoding="utf-8")
    spec = try_build_comp_saver_spec_from_work_file(comp, work)
    assert spec is not None
    assert spec.stem == "sh010_comp_v001"
