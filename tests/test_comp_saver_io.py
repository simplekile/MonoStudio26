"""Tests for Fusion comp Saver audit/fix."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.comp_render_paths import build_comp_saver_spec
from monostudio.core.comp_saver_io import (
    CompSaverAuditStatus,
    apply_comp_saver_fix,
    audit_comp_saver,
    repair_comp_file,
    trim_comp_text_to_valid_composition,
)

_SAMPLE_COMP_OK = """Composition {
	Tools = {
		MONOS_Output = Saver {
			Inputs = {
				Clip = Input {
					Value = Clip {
						Filename = "Comp:\\\\render\\\\sh009_comp_v002\\\\sh009_comp_v002.####.exr",
					},
				},
			},
			Name = "MONOS_Output",
		},
	},
}
"""

_SAMPLE_COMP_MISMATCH = """Composition {
	Tools = {
		MONOS_Output = Saver {
			Inputs = {
				Clip = Input {
					Value = Clip {
						Filename = "Comp:\\\\render\\\\sh008_comp_v001\\\\sh008_comp_v001.####.exr",
					},
				},
			},
			Name = "MONOS_Output",
		},
	},
}
"""

_SAMPLE_SAVER1 = """Composition {
	Tools = {
		Saver1 = Saver {
			Inputs = {
				Clip = Input {
					Value = Clip {
						Filename = "Comp:\\\\render\\\\sh009_comp_v002.0000.exr",
					},
				},
			},
		},
	},
}
"""


def _spec(tmp_path: Path, text: str) -> tuple[object, Path]:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    comp.write_text(text, encoding="utf-8")
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    return spec, comp


def test_audit_ok(tmp_path: Path) -> None:
    spec, comp = _spec(tmp_path, _SAMPLE_COMP_OK)
    audit = audit_comp_saver(comp, spec)
    assert audit.status == CompSaverAuditStatus.OK
    assert audit.has_end_render_script is False


def test_audit_ok_fusion_zero_frame_token(tmp_path: Path) -> None:
    """Fusion writes .0000.exr; pipeline spec uses the same token (not literal ####)."""
    text = """Composition {
	Tools = {
		Saver1_1_1 = Saver {
			Inputs = {
				Clip = Input {
					Value = Clip {
						Filename = "Comp:\\\\render\\\\sh002_comp_v006\\\\sh002_comp_v006.0000.exr",
					},
				},
			},
		},
	},
}
"""
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh002_comp_v006.comp"
    comp.write_text(text, encoding="utf-8")
    spec = build_comp_saver_spec(
        entity_name="sh002",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    assert ".0000.exr" in spec.saver_path_fusion
    audit = audit_comp_saver(comp, spec)
    assert audit.status == CompSaverAuditStatus.OK


def test_audit_saver1_mismatch(tmp_path: Path) -> None:
    spec, comp = _spec(tmp_path, _SAMPLE_SAVER1)
    audit = audit_comp_saver(comp, spec)
    assert audit.status == CompSaverAuditStatus.MISMATCH
    assert audit.managed_tool_var == "Saver1"


def test_apply_fix_saver1(tmp_path: Path) -> None:
    spec, comp = _spec(tmp_path, _SAMPLE_SAVER1)
    result = apply_comp_saver_fix(comp, spec)
    assert result == "updated"
    audit = audit_comp_saver(comp, spec)
    assert audit.status == CompSaverAuditStatus.OK


def test_apply_fix_monos_output(tmp_path: Path) -> None:
    spec, comp = _spec(tmp_path, _SAMPLE_COMP_MISMATCH)
    result = apply_comp_saver_fix(comp, spec)
    assert result == "updated"
    audit = audit_comp_saver(comp, spec)
    assert audit.status == CompSaverAuditStatus.OK


def test_trim_trailing_garbage() -> None:
    raw = "Composition {\n\tTools = {\n\t},\n}\n\n\tMONOS_Output = Saver { },\n"
    fixed = trim_comp_text_to_valid_composition(raw)
    assert "MONOS_Output" not in fixed
    assert fixed.rstrip().endswith("}")


def test_repair_comp_file(tmp_path: Path) -> None:
    comp = tmp_path / "bad.comp"
    comp.write_text(
        "Composition {\n\tTools = {\n\t},\n}\n\n\tMONOS_Output = Saver { },\n",
        encoding="utf-8",
    )
    assert repair_comp_file(comp) is True
    assert "MONOS_Output" not in comp.read_text(encoding="utf-8")
    assert comp.with_suffix(".comp.monos.bak").is_file()


def test_misplaced_monos_saver_detected_as_missing(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v003.comp"
    comp.write_text(
        "Composition {\n"
        "\tTools = {\n"
        "\t\tLoader1 = Loader {\n"
        '\t\t\tClips = { Clip { Filename = "x.exr", } },\n'
        "\t\t\tViewInfo = OperatorInfo { Pos = { -100, 0 }, },\n"
        "\t\t},\n"
        "\t},\n"
        "\tViewLUT = {\n"
        "\t\tTools = ordered() {\n"
        "\t\t\tOCIO = ViewOperator { CtrlWZoom = false },\n"
        "\t\t},\n"
        "\t\tMONOS_Output = Saver {\n"
        '\t\t\tInputs = { Clip = Input { Value = Clip { Filename = "Comp:\\\\render\\\\sh009_comp_v003\\\\sh009_comp_v003.####.exr", } } },\n'
        '\t\t\tName = "MONOS_Output",\n'
        "\t\t},\n"
        "\t},\n"
        "}\n",
        encoding="utf-8",
    )
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    audit = audit_comp_saver(comp, spec)
    assert audit.status == CompSaverAuditStatus.MISSING_MANAGED


def test_repair_removes_misplaced_monos_saver(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v003.comp"
    raw = (
        "Composition {\n"
        "\tTools = {\n"
        "\t},\n"
        "\tViewLUT = {\n"
        "\t\tTools = ordered() {\n"
        "\t\t\tOCIO = ViewOperator { CtrlWZoom = false },\n"
        "\t\t},\n"
        "\t\tMONOS_Output = Saver { Name = \"MONOS_Output\", },\n"
        "\t},\n"
        "}\n"
    )
    comp.write_text(raw, encoding="utf-8")
    assert repair_comp_file(comp) is True
    text = comp.read_text(encoding="utf-8")
    assert "MONOS_Output" not in text


def test_inject_saver_connects_to_rightmost(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    comp.write_text(
        "Composition {\n"
        "\tTools = {\n"
        "\t\tLoaderA = Loader {\n"
        '\t\t\tClips = { Clip { Filename = "a.exr", } },\n'
        "\t\t\tViewInfo = OperatorInfo { Pos = { -100, 10 }, },\n"
        "\t\t},\n"
        "\t\tMerge1 = Merge {\n"
        "\t\t\tViewInfo = OperatorInfo { Pos = { 500, 20 }, },\n"
        "\t\t},\n"
        "\t},\n"
        "}\n",
        encoding="utf-8",
    )
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    result = apply_comp_saver_fix(
        comp,
        spec,
        create_if_missing=True,
        connect_to_rightmost=True,
    )
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert 'SourceOp = "Merge1"' in text
    assert "MONOS_Output" in text


def test_connect_existing_saver_to_rightmost(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    comp.write_text(
        "Composition {\n"
        "\tTools = {\n"
        "\t\tCrop1 = Crop {\n"
        "\t\t\tViewInfo = OperatorInfo { Pos = { 1200, 0 }, },\n"
        "\t\t},\n"
        "\t\tMONOS_Output = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tClip = Input { Value = Clip { Filename = "Comp:\\\\render\\\\sh009_comp_v002\\\\sh009_comp_v002.####.exr", } },\n'
        "\t\t\t},\n"
        '\t\t\tName = "MONOS_Output",\n'
        "\t\t},\n"
        "\t},\n"
        "}\n",
        encoding="utf-8",
    )
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    result = apply_comp_saver_fix(comp, spec, connect_to_rightmost=True)
    assert result == "updated"
    assert 'SourceOp = "Crop1"' in comp.read_text(encoding="utf-8")


def test_skip_reconnect_when_saver_already_wired(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    comp.write_text(
        "Composition {\n"
        "\tTools = {\n"
        "\t\tCrop1 = Crop {\n"
        "\t\t\tViewInfo = OperatorInfo { Pos = { 1200, 0 }, },\n"
        "\t\t},\n"
        "\t\tMergeFar = Merge {\n"
        "\t\t\tViewInfo = OperatorInfo { Pos = { 2000, 0 }, },\n"
        "\t\t},\n"
        "\t\tMONOS_Output = Saver {\n"
        "\t\t\tInputs = {\n"
        "\t\t\t\tInput = Input {\n"
        '\t\t\t\t\tSourceOp = "Crop1",\n'
        '\t\t\t\t\tSource = "Output",\n'
        "\t\t\t\t},\n"
        '\t\t\t\tClip = Input { Value = Clip { Filename = "Comp:\\\\render\\\\sh009_comp_v002\\\\sh009_comp_v002.####.exr", } },\n'
        "\t\t\t},\n"
        '\t\t\tName = "MONOS_Output",\n'
        "\t\t},\n"
        "\t},\n"
        "}\n",
        encoding="utf-8",
    )
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    from monostudio.core.comp_saver_io import managed_saver_is_connected

    assert managed_saver_is_connected(comp.read_text(encoding="utf-8"))
    result = apply_comp_saver_fix(comp, spec, connect_to_rightmost=True)
    assert result == "unchanged"
    assert 'SourceOp = "Crop1"' in comp.read_text(encoding="utf-8")
    assert 'SourceOp = "MergeFar"' not in comp.read_text(encoding="utf-8")


def test_managed_saver_connected_with_dotted_tool_type(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    saver_path = spec.saver_path_fusion.replace("\\", "\\\\")
    comp.write_text(
        "Composition {\n"
        "\tTools = {\n"
        "\t\tXfChromaFuse1 = Fuse.XfChroma {\n"
        "\t\t\tViewInfo = OperatorInfo { Pos = { 800, 0 }, },\n"
        "\t\t},\n"
        "\t\tMONOS_Output = Saver {\n"
        "\t\t\tInputs = {\n"
        "\t\t\t\tInput = Input {\n"
        '\t\t\t\t\tSourceOp = "XfChromaFuse1",\n'
        '\t\t\t\t\tSource = "Output",\n'
        "\t\t\t\t},\n"
        f'\t\t\t\tClip = Input {{ Value = Clip {{ Filename = "{saver_path}", }} }},\n'
        "\t\t\t},\n"
        "\t\t},\n"
        "\t},\n"
        "}\n",
        encoding="utf-8",
    )
    from monostudio.core.comp_saver_io import managed_saver_is_connected

    text = comp.read_text(encoding="utf-8")
    assert managed_saver_is_connected(text)
    result = apply_comp_saver_fix(comp, spec, connect_to_rightmost=True)
    assert result == "unchanged"
    assert 'SourceOp = "XfChromaFuse1"' in comp.read_text(encoding="utf-8")


def test_inject_into_tools_block(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    comp.write_text("Composition {\n\tTools = {\n\t},\n}\n", encoding="utf-8")
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    audit = audit_comp_saver(comp, spec)
    assert audit.status == CompSaverAuditStatus.MISSING_MANAGED
    result = apply_comp_saver_fix(comp, spec, create_if_missing=True)
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "MONOS_Output" in text
    assert "ShowPic = true" in text
    assert "Pos = { 200, 0 }" in text
    assert text.count("Composition {") == 1
    assert "Tools = {" in text


def test_inject_saver_right_of_rightmost_tool(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    comp.write_text(
        "Composition {\n"
        "\tTools = ordered() {\n"
        "\t\tLoaderA = Loader {\n"
        "\t\t\tViewInfo = OperatorInfo { Pos = { -2000, 10 }, },\n"
        "\t\t},\n"
        "\t\tLoaderB = Loader {\n"
        "\t\t\tViewInfo = OperatorInfo { Pos = { -1500, 50.5 }, },\n"
        "\t\t},\n"
        "\t},\n"
        "}\n",
        encoding="utf-8",
    )
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    result = apply_comp_saver_fix(comp, spec, create_if_missing=True)
    assert result == "updated"
    text = comp.read_text(encoding="utf-8")
    assert "ShowPic = true" in text
    assert "Pos = { -1300, 50.5 }" in text
