"""Tests for comp upstream render Loader checks."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.comp_upstream_render_check import (
    UpstreamRenderStatus,
    apply_upstream_render_updates,
    audit_comp_upstream_renders,
    find_latest_render_version,
    parse_pipeline_render_loader_path,
)


def test_parse_pipeline_render_loader_path() -> None:
    p = parse_pipeline_render_loader_path(
        r"D:\proj\02_shots\sh009\03_lighting\houdini\work\render\sh009_lighting_v002\sh009_lighting_v002.0065.exr"
    )
    assert p is not None
    assert p.version == 2
    assert p.base_prefix == "sh009_lighting"
    assert p.department == "lighting"
    assert p.entity_name == "sh009"


def test_find_latest_render_version(tmp_path: Path) -> None:
    render = tmp_path / "render"
    v1 = render / "sh009_lighting_v001"
    v1.mkdir(parents=True)
    (v1 / "sh009_lighting_v001.0001.exr").write_bytes(b"x")
    v3 = render / "sh009_lighting_v003"
    v3.mkdir(parents=True)
    (v3 / "sh009_lighting_v003.0001.exr").write_bytes(b"x")
    hit = find_latest_render_version(render, "sh009_lighting")
    assert hit is not None
    assert hit[0] == 3


def test_find_latest_skips_empty_higher_version(tmp_path: Path) -> None:
    render = tmp_path / "render"
    (render / "sh009_lighting_v004").mkdir(parents=True)
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    (v2 / "sh009_lighting_v002.0065.exr").write_bytes(b"x")
    hit = find_latest_render_version(render, "sh009_lighting")
    assert hit is not None
    assert hit[0] == 2


def test_audit_upstream_ok_when_latest(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    seq = render / "sh009_lighting_v002"
    seq.mkdir(parents=True)
    frame = seq / "sh009_lighting_v002.0001.exr"
    frame.write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    loader = str(frame).replace("\\", "\\\\")
    comp.write_text(
        f'Composition {{\n\tTools = {{\n\t\tL1 = Loader {{\n'
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", }} }},\n'
        f"\t\t}},\n\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert issues == []


def test_audit_upstream_stale(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    (render / "sh009_lighting_v001").mkdir(parents=True)
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    frame = v2 / "sh009_lighting_v002.0001.exr"
    frame.write_bytes(b"x")
    v3 = render / "sh009_lighting_v003"
    v3.mkdir(parents=True)
    (v3 / "sh009_lighting_v003.0001.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    loader = str(frame).replace("\\", "\\\\")
    comp.write_text(
        f'Composition {{\n\tTools = {{\n\t\tL1 = Loader {{\n'
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", }} }},\n'
        f"\t\t}},\n\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert len(issues) == 1
    assert issues[0].status == UpstreamRenderStatus.STALE
    assert issues[0].comp_version == 2
    assert issues[0].latest_version == 3


def test_audit_missing_v001_with_v002_on_disk_is_stale(tmp_path: Path) -> None:
    """v001 frame missing but v002 folder exists → offer update to latest."""
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v1 = render / "sh009_lighting_v001"
    v1.mkdir(parents=True)
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    (v2 / "sh009_lighting_v002.0065.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    missing = str((v1 / "sh009_lighting_v001.0065.exr")).replace("\\", "\\\\")
    comp.write_text(
        f'Composition {{\n\tTools = {{\n\t\tL1 = Loader {{\n'
        f'\t\t\tClips = {{ Clip {{ Filename = "{missing}", }} }},\n'
        f"\t\t}},\n\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert len(issues) == 1
    assert issues[0].status == UpstreamRenderStatus.STALE
    assert issues[0].comp_version == 1
    assert issues[0].latest_version == 2


def test_audit_mixed_versions_flags_stale_v001(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v1 = render / "sh009_lighting_v001"
    v1.mkdir(parents=True)
    f1 = v1 / "sh009_lighting_v001.0001.exr"
    f1.write_bytes(b"x")
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    f2 = v2 / "sh009_lighting_v002.0001.exr"
    f2.write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    p1 = str(f1).replace("\\", "\\\\")
    p2 = str(f2).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n\tTools = ordered() {{\n"
        f"\t\tL_old = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = \"{p1}\", }} }},\n\t\t}},\n"
        f"\t\tL_new = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = \"{p2}\", }} }},\n\t\t}},\n"
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    stale = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    assert len(stale) == 1
    assert stale[0].comp_version == 1
    assert stale[0].latest_version == 2
    assert stale[0].loader_count == 2


def test_audit_comp_v004_missing_falls_back_to_latest_valid(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    (render / "sh009_lighting_v004").mkdir(parents=True)
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    frame = v2 / "sh009_lighting_v002.0065.exr"
    frame.write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    missing_v4 = str((render / "sh009_lighting_v004" / "sh009_lighting_v004.0065.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n\tTools = ordered() {{\n"
        f"\t\tcombinedemission = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{missing_v4}", }} }},\n'
        f"\t\t}},\n"
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    stale = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    assert len(stale) == 1
    assert stale[0].comp_version == 4
    assert stale[0].latest_version == 2


def test_apply_fallback_from_missing_v004_to_v002(tmp_path: Path) -> None:
    from monostudio.core.comp_upstream_render_check import apply_upstream_render_updates

    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    (render / "sh009_lighting_v004").mkdir(parents=True)
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    frame = v2 / "sh009_lighting_v002.0065.exr"
    frame.write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    missing_v4 = str((render / "sh009_lighting_v004" / "sh009_lighting_v004.0065.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n\tTools = ordered() {{\n"
        f"\t\tcombinedemission = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{missing_v4}", }} }},\n'
        f"\t\t}},\n"
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    stale = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    result = apply_upstream_render_updates(
        comp,
        stale,
        selected_issues=stale,
        entity_name="sh009",
    )
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "sh009_lighting_v002" in text
    assert "sh009_lighting_v004" not in text


def test_audit_path_folder_filename_mismatch_is_stale(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    v4 = render / "sh009_lighting_v004"
    v4.mkdir(parents=True)
    (v4 / "sh009_lighting_v004.0065.exr").write_bytes(b"x")
    (v4 / "sh009_lighting_v004.0065.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    bad = str(v2 / "sh009_lighting_v003.0065.exr").replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n\tTools = ordered() {{\n"
        f"\t\tsss = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{bad}", }} }},\n'
        f"\t\t}},\n"
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    stale = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    assert len(stale) == 1
    assert stale[0].latest_version == 4


def test_apply_fixes_folder_filename_mismatch(tmp_path: Path) -> None:
    from monostudio.core.comp_upstream_render_check import apply_upstream_render_updates

    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    v4 = render / "sh009_lighting_v004"
    v4.mkdir(parents=True)
    (v4 / "sh009_lighting_v004.0065.exr").write_bytes(b"x")
    (v4 / "sh009_lighting_v004.0065.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    bad = str(v2 / "sh009_lighting_v003.0065.exr").replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n\tTools = ordered() {{\n"
        f"\t\tsss = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{bad}", }} }},\n'
        f"\t\t}},\n"
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    stale = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    result = apply_upstream_render_updates(
        comp,
        stale,
        selected_issues=stale,
        entity_name="sh009",
    )
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "sh009_lighting_v004" in text
    assert "sh009_lighting_v002" not in text
    assert "sh009_lighting_v003" not in text


def test_sync_range_on_latest_loader(tmp_path: Path) -> None:
    from monostudio.core.comp_upstream_render_check import apply_upstream_render_updates

    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v4 = render / "sh009_lighting_v004"
    v4.mkdir(parents=True)
    (v4 / "sh009_lighting_v004.0065.exr").write_bytes(b"x")
    frame = v4 / "sh009_lighting_v004.0065.exr"
    frame.write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    loader = str(frame).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 75 }},\n"
        f"\tTools = ordered() {{\n"
        f"\t\tcombinedemission = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", '
        f"LengthSetManually = true, TrimIn = 0, TrimOut = 0, Length = 0, "
        f"GlobalStart = 0, GlobalEnd = 1, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    range_issues = [i for i in issues if i.status == UpstreamRenderStatus.RANGE_MISMATCH]
    assert len(range_issues) == 1
    assert range_issues[0].loader_range_start == 65
    assert range_issues[0].loader_range_end == 65
    assert range_issues[0].comp_range_start == 0
    assert range_issues[0].comp_range_end == 75
    result = apply_upstream_render_updates(
        comp,
        [],
        sync_loader_range=True,
        entity_name="sh009",
    )
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "TrimOut = 75" in text
    assert "GlobalEnd = 75" in text
    assert "LengthSetManually = false" in text
    issues_after = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert [i for i in issues_after if i.status == UpstreamRenderStatus.RANGE_MISMATCH]


def test_audit_upstream_range_mismatch_against_disk(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    for frame_no in (1, 5, 9):
        (v2 / f"sh009_lighting_v002.{frame_no:04d}.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    loader = str((v2 / "sh009_lighting_v002.0005.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 75 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", '
        f"TrimIn = 0, TrimOut = 75, Length = 76, GlobalStart = 0, GlobalEnd = 75, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert not [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    range_issues = [i for i in issues if i.status == UpstreamRenderStatus.RANGE_MISMATCH]
    assert len(range_issues) == 1
    assert range_issues[0].loader_range_start == 1
    assert range_issues[0].loader_range_end == 9
    assert range_issues[0].department == "lighting"
    assert "render on disk" in range_issues[0].message


def test_apply_clamp_comp_range_to_disk(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    for frame_no in (0, 25, 55):
        (v2 / f"sh009_lighting_v002.{frame_no:04d}.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    loader = str((v2 / "sh009_lighting_v002.0025.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 75 }},\n"
        f"\tRenderRange = {{ 0, 75 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", '
        f"TrimIn = 0, TrimOut = 75, Length = 76, GlobalStart = 0, GlobalEnd = 75, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert [i for i in issues if i.status == UpstreamRenderStatus.RANGE_MISMATCH]
    result = apply_upstream_render_updates(
        comp,
        issues,
        clamp_comp_range_to_disk=True,
        entity_name="sh009",
    )
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "GlobalRange = { 0, 55 }" in text
    assert "RenderRange = { 0, 55 }" in text
    assert "TrimOut = 55" in text
    assert "GlobalEnd = 55" in text
    issues_after = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert not [i for i in issues_after if i.status == UpstreamRenderStatus.RANGE_MISMATCH]


def test_audit_range_mismatch_uses_latest_render_on_first_open(tmp_path: Path) -> None:
    """Stale loader path must not hide a wider frame span on the latest render folder."""
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "02_shots" / "sh008" / "03_lighting" / "houdini" / "work" / "render"
    v5 = render / "sh008_lighting_v005"
    v6 = render / "sh008_lighting_v006"
    v5.mkdir(parents=True)
    v6.mkdir(parents=True)
    for frame_no in (0, 54):
        (v5 / f"sh008_lighting_v005.{frame_no:04d}.exr").write_bytes(b"x")
    for frame_no in (0, 129):
        (v6 / f"sh008_lighting_v006.{frame_no:04d}.exr").write_bytes(b"x")
    comp = work / "sh008_comp_v002.comp"
    comp.parent.mkdir(parents=True)
    loader = str((v5 / "sh008_lighting_v005.0032.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 54 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", '
        f"TrimIn = 0, TrimOut = 54, Length = 55, GlobalStart = 0, GlobalEnd = 54, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh008")
    stale = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    range_issues = [i for i in issues if i.status == UpstreamRenderStatus.RANGE_MISMATCH]
    assert len(stale) == 1
    assert len(range_issues) == 1
    assert range_issues[0].loader_range_end == 129
    assert "Latest render sh008_lighting_v006" in range_issues[0].message
    assert "Loaders reference sh008_lighting_v005" in range_issues[0].message


def test_audit_upstream_range_mismatch_when_disk_wider_than_comp(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "02_shots" / "sh008" / "03_lighting" / "houdini" / "work" / "render"
    v6 = render / "sh008_lighting_v006"
    v6.mkdir(parents=True)
    for frame_no in (0, 65, 129):
        (v6 / f"sh008_lighting_v006.{frame_no:04d}.exr").write_bytes(b"x")
    comp = work / "sh008_comp_v002.comp"
    comp.parent.mkdir(parents=True)
    loader = str((v6 / "sh008_lighting_v006.0065.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 54 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", '
        f"TrimIn = 0, TrimOut = 54, Length = 55, GlobalStart = 0, GlobalEnd = 54, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh008")
    range_issues = [i for i in issues if i.status == UpstreamRenderStatus.RANGE_MISMATCH]
    assert len(range_issues) == 1
    assert range_issues[0].loader_range_start == 0
    assert range_issues[0].loader_range_end == 129
    assert range_issues[0].comp_range_end == 54
    assert "130 frames" in range_issues[0].message
    assert "wider than comp" in range_issues[0].message


def test_apply_clamp_expands_comp_range_when_disk_wider(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v6 = render / "sh008_lighting_v006"
    v6.mkdir(parents=True)
    for frame_no in (0, 129):
        (v6 / f"sh008_lighting_v006.{frame_no:04d}.exr").write_bytes(b"x")
    comp = work / "sh008_comp_v002.comp"
    comp.parent.mkdir(parents=True)
    loader = str((v6 / "sh008_lighting_v006.0065.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 54 }},\n"
        f"\tRenderRange = {{ 0, 54 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", '
        f"TrimIn = 0, TrimOut = 54, Length = 55, GlobalStart = 0, GlobalEnd = 54, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    result = apply_upstream_render_updates(
        comp,
        [],
        clamp_comp_range_to_disk=True,
        entity_name="sh008",
    )
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "GlobalRange = { 0, 129 }" in text
    assert "TrimOut = 129" in text


def test_audit_upstream_range_ok_when_disk_covers_comp(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    (v2 / "sh009_lighting_v002.0000.exr").write_bytes(b"x")
    (v2 / "sh009_lighting_v002.0075.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    loader = str((v2 / "sh009_lighting_v002.0000.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 75 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", '
        f"TrimIn = 0, TrimOut = 75, Length = 76, GlobalStart = 0, GlobalEnd = 75, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert not [i for i in issues if i.status == UpstreamRenderStatus.RANGE_MISMATCH]


def test_apply_upstream_with_range_sync(tmp_path: Path) -> None:
    from monostudio.core.comp_upstream_render_check import apply_upstream_render_updates

    work = tmp_path / "comp" / "fusion" / "work"
    render = tmp_path / "lighting" / "houdini" / "work" / "render"
    v1 = render / "sh009_lighting_v001"
    v1.mkdir(parents=True)
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    f1 = v1 / "sh009_lighting_v001.0001.exr"
    f1.write_bytes(b"x")
    (v2 / "sh009_lighting_v002.0001.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    p1 = str(f1).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 75 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{p1}", '
        f"TrimIn = 0, TrimOut = 9, Length = 10, GlobalStart = 0, GlobalEnd = 9, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    stale = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    assert len(stale) == 1
    result = apply_upstream_render_updates(
        comp,
        stale,
        selected_issues=stale,
        sync_loader_range=True,
        entity_name="sh009",
    )
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "sh009_lighting_v002" in text
    assert "TrimOut = 75" in text
    assert "Length = 76" in text


def test_audit_stale_comp_department_loader(tmp_path: Path) -> None:
    """Any versioned work/render Loader is checked, not only lighting/fx/anim."""
    work = tmp_path / "04_comp" / "fusion" / "work"
    render = work / "render"
    v1 = render / "sh009_comp_v001"
    v1.mkdir(parents=True)
    frame = v1 / "sh009_comp_v001.0001.exr"
    frame.write_bytes(b"x")
    v2 = render / "sh009_comp_v002"
    v2.mkdir(parents=True)
    (v2 / "sh009_comp_v002.0001.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    loader = str(frame).replace("\\", "\\\\")
    comp.write_text(
        f'Composition {{\n\tTools = {{\n\t\tL1 = Loader {{\n'
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", }} }},\n'
        f"\t\t}},\n\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert len(issues) == 1
    assert issues[0].status == UpstreamRenderStatus.STALE
    assert issues[0].base_prefix == "sh009_comp"
    assert issues[0].department == "comp"
    assert issues[0].latest_version == 2


def test_audit_skips_non_versioned_loader(tmp_path: Path) -> None:
    work = tmp_path / "04_comp" / "fusion" / "work"
    ref = tmp_path / "reference" / "plate.exr"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    loader = str(ref).replace("\\", "\\\\")
    comp.write_text(
        f'Composition {{\n\tTools = {{\n\t\tL1 = Loader {{\n'
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", }} }},\n'
        f"\t\t}},\n\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009")
    assert issues == []


def test_audit_respects_department_filter(tmp_path: Path) -> None:
    work = tmp_path / "comp" / "fusion" / "work"
    lighting_render = tmp_path / "lighting" / "houdini" / "work" / "render"
    model_render = tmp_path / "model" / "maya" / "work" / "render"
    for render, prefix in (
        (lighting_render, "sh009_lighting"),
        (model_render, "sh009_model"),
    ):
        v1 = render / f"{prefix}_v001"
        v1.mkdir(parents=True)
        (v1 / f"{prefix}_v001.0001.exr").write_bytes(b"x")
        v2 = render / f"{prefix}_v002"
        v2.mkdir(parents=True)
        (v2 / f"{prefix}_v002.0001.exr").write_bytes(b"x")
    comp = work / "sh009_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    lighting_loader = str((lighting_render / "sh009_lighting_v001" / "sh009_lighting_v001.0001.exr")).replace(
        "\\", "\\\\"
    )
    model_loader = str((model_render / "sh009_model_v001" / "sh009_model_v001.0001.exr")).replace("\\", "\\\\")
    comp.write_text(
        f'Composition {{\n\tTools = {{\n'
        f'\t\tL1 = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = "{lighting_loader}", }} }},\n\t\t}},\n'
        f'\t\tL2 = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = "{model_loader}", }} }},\n\t\t}},\n'
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh009", departments=("lighting",))
    assert len(issues) == 1
    assert issues[0].department == "lighting"


def test_audit_wrong_entity_loader(tmp_path: Path) -> None:
    work = tmp_path / "04_comp" / "fusion" / "work"
    sh002_render = tmp_path / "02_shots" / "sh002" / "03_lighting" / "houdini" / "work" / "render"
    sh003_render = tmp_path / "02_shots" / "sh003" / "03_lighting" / "houdini" / "work" / "render"
    for render, prefix in (
        (sh002_render, "sh002_lighting"),
        (sh003_render, "sh003_lighting"),
    ):
        folder = render / f"{prefix}_v001"
        folder.mkdir(parents=True)
        (folder / f"{prefix}_v001.0001.exr").write_bytes(b"x")
    comp = work / "sh002_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    wrong_loader = str((sh003_render / "sh003_lighting_v001" / "sh003_lighting_v001.0001.exr")).replace(
        "\\", "\\\\"
    )
    right_loader = str((sh002_render / "sh002_lighting_v001" / "sh002_lighting_v001.0001.exr")).replace(
        "\\", "\\\\"
    )
    comp.write_text(
        f'Composition {{\n\tTools = {{\n'
        f'\t\tL_wrong = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = "{wrong_loader}", }} }},\n\t\t}},\n'
        f'\t\tL_ok = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = "{right_loader}", }} }},\n\t\t}},\n'
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh002")
    wrong = [i for i in issues if i.status == UpstreamRenderStatus.WRONG_ENTITY]
    assert len(wrong) == 1
    assert wrong[0].entity_name == "sh003"
    assert wrong[0].expected_entity_name == "sh002"
    assert wrong[0].base_prefix == "sh003_lighting"
    assert wrong[0].loader_count == 1
    assert not [i for i in issues if i.status == UpstreamRenderStatus.STALE]


def test_apply_wrong_entity_loader_retarget(tmp_path: Path) -> None:
    work = tmp_path / "04_comp" / "fusion" / "work"
    sh002_render = tmp_path / "02_shots" / "sh002" / "03_lighting" / "houdini" / "work" / "render"
    sh003_render = tmp_path / "02_shots" / "sh003" / "03_lighting" / "houdini" / "work" / "render"
    for render, prefix in (
        (sh002_render, "sh002_lighting"),
        (sh003_render, "sh003_lighting"),
    ):
        folder = render / f"{prefix}_v001"
        folder.mkdir(parents=True)
        (folder / f"{prefix}_v001.0001.exr").write_bytes(b"x")
    comp = work / "sh002_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    wrong_loader = str((sh003_render / "sh003_lighting_v001" / "sh003_lighting_v001.0001.exr")).replace(
        "\\", "\\\\"
    )
    comp.write_text(
        f'Composition {{\n\tTools = {{\n'
        f'\t\tL_wrong = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = "{wrong_loader}", }} }},\n\t\t}},\n'
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh002")
    wrong = [i for i in issues if i.status == UpstreamRenderStatus.WRONG_ENTITY]
    assert len(wrong) == 1

    result = apply_upstream_render_updates(comp, issues, selected_issues=wrong)
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "sh002_lighting_v001" in text
    assert "sh003_lighting" not in text


def test_audit_same_version_missing_frame_is_frame_ref_not_stale(tmp_path: Path) -> None:
    """Copied comp paths often keep a frame number that does not exist in the version folder."""
    work = tmp_path / "04_comp" / "fusion" / "work"
    render = tmp_path / "02_shots" / "sh003" / "03_lighting" / "houdini" / "work" / "render"
    v1 = render / "sh003_lighting_v001"
    v1.mkdir(parents=True)
    for frame_no in (0, 25, 50):
        (v1 / f"sh003_lighting_v001.{frame_no:04d}.exr").write_bytes(b"x")
    comp = work / "sh003_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    missing = str((v1 / "sh003_lighting_v001.0065.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n\tGlobalRange = {{ 0, 50 }},\n\tTools = {{\n"
        f"\t\tL1 = Loader {{\n\t\t\tClips = {{ Clip {{ Filename = \"{missing}\", }} }},\n\t\t}},\n"
        f"\t}},\n}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh003")
    assert not [i for i in issues if i.status == UpstreamRenderStatus.STALE]
    frame_ref = [i for i in issues if i.status == UpstreamRenderStatus.FRAME_REF]
    assert len(frame_ref) == 1
    assert frame_ref[0].comp_version == 1
    assert frame_ref[0].latest_version == 1
    assert "reference a frame file" in frame_ref[0].message
    assert "frame 65 → 50" in frame_ref[0].apply_summary or "65" in frame_ref[0].apply_summary


def test_apply_frame_ref_repair_points_to_existing_frame(tmp_path: Path) -> None:
    work = tmp_path / "04_comp" / "fusion" / "work"
    render = tmp_path / "02_shots" / "sh003" / "03_lighting" / "houdini" / "work" / "render"
    v1 = render / "sh003_lighting_v001"
    v1.mkdir(parents=True)
    for frame_no in (0, 50):
        (v1 / f"sh003_lighting_v001.{frame_no:04d}.exr").write_bytes(b"x")
    comp = work / "sh003_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    missing = str((v1 / "sh003_lighting_v001.0065.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n\tTools = {{\n\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{missing}", }} }},\n\t\t}},\n\t}},\n}}\n',
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh003")
    frame_ref = [i for i in issues if i.status == UpstreamRenderStatus.FRAME_REF]
    assert len(frame_ref) == 1
    result = apply_upstream_render_updates(comp, issues, selected_issues=frame_ref)
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "sh003_lighting_v001.0050.exr" in text
    assert "0065" not in text


def test_audit_range_mismatch_wrong_entity_uses_retarget_disk(tmp_path: Path) -> None:
    """Wrong-entity loaders must check frames on disk for the comp shot, not the old link."""
    work = tmp_path / "04_comp" / "fusion" / "work"
    sh003_render = tmp_path / "02_shots" / "sh003" / "03_lighting" / "houdini" / "work" / "render"
    sh004_render = tmp_path / "02_shots" / "sh004" / "03_lighting" / "houdini" / "work" / "render"
    sh003_folder = sh003_render / "sh003_lighting_v001"
    sh003_folder.mkdir(parents=True)
    for frame_no in (0, 65, 129):
        (sh003_folder / f"sh003_lighting_v001.{frame_no:04d}.exr").write_bytes(b"x")
    sh004_folder = sh004_render / "sh004_lighting_v001"
    sh004_folder.mkdir(parents=True)
    (sh004_folder / "sh004_lighting_v001.0001.exr").write_bytes(b"x")
    comp = work / "sh003_comp_v001.comp"
    comp.parent.mkdir(parents=True)
    wrong_loader = str((sh004_folder / "sh004_lighting_v001.0001.exr")).replace("\\", "\\\\")
    comp.write_text(
        f"Composition {{\n"
        f"\tGlobalRange = {{ 0, 55 }},\n"
        f"\tTools = {{\n"
        f"\t\tL1 = Loader {{\n"
        f'\t\t\tClips = {{ Clip {{ Filename = "{wrong_loader}", '
        f"TrimIn = 0, TrimOut = 55, Length = 56, GlobalStart = 0, GlobalEnd = 55, }} }},\n"
        f"\t\t}},\n"
        f"\t}},\n"
        f"}}\n",
        encoding="utf-8",
    )
    issues = audit_comp_upstream_renders(comp, entity_name="sh003")
    wrong = [i for i in issues if i.status == UpstreamRenderStatus.WRONG_ENTITY]
    range_issues = [i for i in issues if i.status == UpstreamRenderStatus.RANGE_MISMATCH]
    assert len(wrong) == 1
    assert len(range_issues) == 1
    assert range_issues[0].entity_name == "sh003"
    assert range_issues[0].loader_range_start == 0
    assert range_issues[0].loader_range_end == 129
    assert "After retarget to sh003_lighting" in range_issues[0].message

    from monostudio.core.comp_upstream_render_check import resolve_comp_range_from_disk

    disk_range = resolve_comp_range_from_disk(
        comp.read_text(encoding="utf-8"),
        entity_name="sh003",
        retarget_wrong_entity=True,
    )
    assert disk_range == (0, 129)
