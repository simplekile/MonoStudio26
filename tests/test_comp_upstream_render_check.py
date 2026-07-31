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
    assert issues == []
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
