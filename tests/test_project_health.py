"""Tests for project-wide health scan and autosave heuristics."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.item_health_scan import (
    invalid_work_files_split_in_folder,
    workfile_extensions_set,
)
from monostudio.core.project_health import (
    format_byte_size,
    is_blender_backup_filename,
    is_probable_autosave_filename,
    path_size_bytes,
)


def test_is_probable_autosave_dot_after_version():
    work_exts = workfile_extensions_set()
    prefix = "char_aya_modelling"
    assert is_probable_autosave_filename(
        f"{prefix}_v001.240101.blend", prefix, work_exts
    )
    assert not is_probable_autosave_filename(
        f"{prefix}_v001.blend", prefix, work_exts
    )


def test_blend1_is_blender_backup_not_autosave():
    work_exts = workfile_extensions_set()
    prefix = "char_aya_modelling"
    name = "char_aya_modelling_v001.blend1"
    assert is_blender_backup_filename(name)
    assert not is_probable_autosave_filename(name, prefix, work_exts)
    assert is_blender_backup_filename("scene.blend2")
    assert is_blender_backup_filename("scene.blend3")


def test_format_byte_size():
    assert format_byte_size(0) == "0 B"
    assert format_byte_size(512) == "512 B"
    assert format_byte_size(2048) == "2 KB"
    assert "MB" in format_byte_size(5 * 1024 * 1024)


def test_path_size_bytes_file(tmp_path: Path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 100)
    assert path_size_bytes(f) == 100


def test_invalid_work_files_split_finds_autosave_style(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    prefix = "char_test_modelling"
    work_exts = workfile_extensions_set()
    if ".blend" not in work_exts:
        return  # DCC registry not available in this environment
    (work / f"{prefix}_v001.blend").write_text("ok", encoding="utf-8")
    (work / f"{prefix}_v001.240101.blend").write_text("autosave", encoding="utf-8")
    name_bad, ext_bad = invalid_work_files_split_in_folder(work, prefix, work_exts)
    assert len(ext_bad) == 0
    assert len(name_bad) == 1
    assert "240101" in name_bad[0].name
    assert is_probable_autosave_filename(name_bad[0].name, prefix, work_exts)
