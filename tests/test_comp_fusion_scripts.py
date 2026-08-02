"""Tests for Fusion Saver EndRenderScript + project discord.py deploy."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.comp_fusion_scripts import (
    build_saver_end_render_lua,
    ensure_project_fusion_discord_script,
    project_fusion_discord_script_path,
)
from monostudio.core.comp_render_paths import build_comp_saver_spec
from monostudio.core.comp_saver_io import apply_comp_saver_fix, apply_end_render_script_to_saver_block, read_comp_text


def test_write_fusion_render_actor(tmp_path: Path) -> None:
    import json

    from monostudio.core.comp_fusion_scripts import (
        fusion_render_actor_path,
        write_fusion_render_actor,
    )

    project = tmp_path / "proj"
    (project / ".monostudio").mkdir(parents=True)
    write_fusion_render_actor(project)
    actor = fusion_render_actor_path(project)
    assert actor.is_file()
    data = json.loads(actor.read_text(encoding="utf-8"))
    assert str(data.get("display_name") or "").strip()


def test_refresh_fusion_discord_webhooks_for_workspace(tmp_path: Path) -> None:
    import json

    from monostudio.core.comp_fusion_scripts import refresh_fusion_discord_webhooks_for_workspace
    from monostudio.core.integrations_config import build_integrations_from_webhooks, write_integrations

    workspace = tmp_path / "ws"
    workspace.mkdir()
    project = workspace / "proj_a"
    fusion_dir = project / ".monostudio" / "fusion"
    fusion_dir.mkdir(parents=True)
    (project / ".monostudio" / "project.json").write_text("{}", encoding="utf-8")
    (fusion_dir / "discord.py").write_text("# stub\n", encoding="utf-8")
    (fusion_dir / "webhooks.json").write_text("[]\n", encoding="utf-8")

    url = "https://discord.com/api/webhooks/111111111/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    config = build_integrations_from_webhooks(
        enabled=True,
        webhooks=[{"url": url, "events": {"fusion_render_finished": True}}],
    )
    write_integrations(workspace, config, require_admin=False)

    assert refresh_fusion_discord_webhooks_for_workspace(workspace) == 1
    urls = json.loads((fusion_dir / "webhooks.json").read_text(encoding="utf-8"))
    assert urls == [url]


def test_build_saver_end_render_lua_format_string(tmp_path: Path) -> None:
    py = tmp_path / ".monostudio" / "fusion" / "discord.py"
    py.parent.mkdir(parents=True)
    py.write_text("# stub\n", encoding="utf-8")
    (py.parent / "notify.cmd").write_text("@echo off\n", encoding="utf-8")
    lua = build_saver_end_render_lua(py)
    assert 'string.format(\n    [[cmd /c call "' in lua
    assert "notify.cmd" in lua
    assert '"%s" "%s" "%s"]],\n' in lua
    assert "]]]," not in lua
    assert "cmd /c python" not in lua


def test_ensure_project_fusion_discord_script(tmp_path: Path) -> None:
    (tmp_path / ".monostudio").mkdir()
    (tmp_path / ".monostudio" / "project.json").write_text("{}", encoding="utf-8")
    py = ensure_project_fusion_discord_script(tmp_path)
    assert py == project_fusion_discord_script_path(tmp_path)
    assert py.is_file()
    assert (py.parent / "webhook.url").is_file()
    assert (py.parent / "webhooks.json").is_file()
    assert (py.parent / "notify.cmd").is_file()
    assert (py.parent / "python.path").is_file()


def test_ensure_project_fusion_discord_script_multi_webhook(tmp_path: Path) -> None:
    import json

    from monostudio.core.integrations_config import build_integrations_from_webhooks, write_integrations

    project = tmp_path / "proj"
    workspace = tmp_path / "ws"
    project.mkdir()
    workspace.mkdir()
    (project / ".monostudio").mkdir()
    (project / ".monostudio" / "project.json").write_text(
        json.dumps({"workspace": str(workspace)}),
        encoding="utf-8",
    )
    url_a = "https://discord.com/api/webhooks/111111111/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    url_b = "https://discord.com/api/webhooks/222222222/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    config = build_integrations_from_webhooks(
        enabled=True,
        webhooks=[
            {"url": url_a, "events": {"fusion_render_finished": True}},
            {"url": url_b, "events": {"fusion_render_finished": True, "mention": True}},
            {
                "url": "https://discord.com/api/webhooks/333333333/cccccccccccccccccccccccccccccccc",
                "events": {"mention": True},
            },
        ],
    )
    write_integrations(workspace, config, require_admin=False)
    py = ensure_project_fusion_discord_script(project, workspace_root=workspace)
    urls = json.loads((py.parent / "webhooks.json").read_text(encoding="utf-8"))
    assert urls == [url_a, url_b]


def test_apply_end_render_script_to_block() -> None:
    block = (
        "\t\tSaver1 = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tClip = Input { Value = Clip { Filename = "x.exr", }, },\n'
        "\t\t\t},\n"
        "\t\t},\n"
    )
    lua = 'print("hi")'
    out = apply_end_render_script_to_saver_block(block, lua)
    assert "EndRenderScripts = Input { Value = 1" in out
    assert "EndRenderScript = Input { Value =" in out
    assert "print(\\\"hi\\\")" in out or "print(\\\\\"hi\\\\\")" in out


def test_apply_end_render_script_when_enable_already_present() -> None:
    """Fusion comps often enable EndRenderScripts before other Saver inputs."""
    block = (
        "\t\tSaver1_1_1 = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tClip = Input { Value = Clip { Filename = "x.exr", }, },\n'
        "\t\t\t\tEndRenderScripts = Input { Value = 1, },\n"
        '\t\t\t\t["OpenEXRFormat.ZipCompressionLevel"] = Input { Value = 4, }\n'
        "\t\t\t},\n"
        "\t\t},\n"
    )
    lua = 'print("hi")'
    out = apply_end_render_script_to_saver_block(block, lua)
    assert out.count("EndRenderScripts = Input { Value = 1") == 1
    assert "EndRenderScript = Input { Value =" in out
    assert out.index("EndRenderScript") < out.index("ZipCompressionLevel")
    assert 'Input { Value = 4, }' in out


def test_apply_end_render_script_when_enable_line_missing_field_comma() -> None:
    """Fusion may save EndRenderScripts without a trailing field comma."""
    block = (
        "\t\tMONOS_Output = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tClip = Input { Value = Clip { Filename = "x.exr", }, },\n'
        "\t\t\t\tInput = Input { SourceOp = \"Merge2\", },\n"
        "\t\t\t\tEndRenderScripts = Input { Value = 1, }\n"
        "\t\t\t},\n"
        "\t\t},\n"
    )
    lua = 'print("hi")'
    out = apply_end_render_script_to_saver_block(block, lua)
    assert "EndRenderScripts = Input { Value = 1, }," in out
    assert "EndRenderScript = Input { Value =" in out


def test_repair_end_render_script_field_comma() -> None:
    from monostudio.core.comp_saver_io import repair_end_render_script_field_comma

    broken = (
        "\t\t\t\tEndRenderScripts = Input { Value = 1, }\n"
        '\t\t\t\tEndRenderScript = Input { Value = "x", },\n'
    )
    fixed = repair_end_render_script_field_comma(broken)
    assert "EndRenderScripts = Input { Value = 1, },\n" in fixed


def test_apply_end_render_script_into_inputs_not_viewinfo() -> None:
    block = (
        "\t\tMONOS_Output = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tClip = Input { Value = Clip { Filename = "x.exr", }, },\n'
        '\t\t\t\tInput = Input { SourceOp = "Merge2", },\n'
        "\t\t\t},\n"
        "\t\t\tViewInfo = OperatorInfo {\n"
        "\t\t\t\tPos = { 1, 2 },\n"
        "\t\t\t\tFlags = { ShowPic = true },\n"
        "\t\t\t},\n"
        "\t\t},\n"
    )
    out = apply_end_render_script_to_saver_block(block, 'print("hi")')
    inputs_part = out.split("ViewInfo", 1)[0]
    assert "EndRenderScript" in inputs_part
    assert "EndRenderScripts" in inputs_part
    assert "EndRenderScript" not in out.split("ViewInfo", 1)[1]


def test_repair_misplaced_end_render_script_block() -> None:
    from monostudio.core.comp_saver_io import repair_misplaced_saver_end_render_script_block

    misplaced = (
        "\t\tMONOS_Output = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tClip = Input { Value = Clip { Filename = "x.exr", }, },\n'
        "\t\t\t},\n"
        "\t\t\tViewInfo = OperatorInfo {\n"
        "\t\t\t\tPos = { 1, 2 },\n"
        "\t\t\t\tFlags = { ShowPic = true },\t\t\t\tEndRenderScripts = Input { Value = 1, },\n"
        '\t\t\t\tEndRenderScript = Input { Value = "print(\\\"hi\\\")", },\n'
        "\n"
        "\t\t\t},\n"
        "\t\t},\n"
    )
    fixed = repair_misplaced_saver_end_render_script_block(misplaced)
    inputs_part = fixed.split("ViewInfo", 1)[0]
    viewinfo_part = fixed.split("ViewInfo", 1)[1]
    assert "EndRenderScript" in inputs_part
    assert "EndRenderScript" not in viewinfo_part
    assert "\t\t\t}," in fixed
    assert ",\n\t\t\t\t," not in fixed


def test_apply_end_render_script_re_sub_preserves_escaped_newlines() -> None:
    lua = 'print("hi")\nsecond line'
    block = (
        "\t\tSaver1 = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tEndRenderScript = Input { Value = "old", },\n'
        "\t\t\t},\n"
        "\t\t},\n"
    )
    out = apply_end_render_script_to_saver_block(block, lua)
    value_line = [ln for ln in out.splitlines() if "EndRenderScript = Input" in ln][0]
    assert value_line.count('"') >= 2
    assert "\\n" in value_line
    assert value_line.strip().endswith('", },') or value_line.strip().endswith('", }')


def test_repair_end_render_script_multiline_value() -> None:
    from monostudio.core.comp_saver_io import repair_end_render_script_value

    broken = (
        "Composition {\n\tTools = {\n"
        "\t\tMONOS_Output = Saver {\n"
        "\t\t\tInputs = {\n"
        '\t\t\t\tEndRenderScript = Input { Value = "line1\n\nline2", },\n'
        "\t\t\t},\n"
        "\t\t},\n"
        "\t},\n"
        "}\n"
    )
    fixed = repair_end_render_script_value(broken)
    assert "line1\\n\\nline2" in fixed
    assert 'Value = "line1\n\nline2"' not in fixed


def test_apply_end_render_script_on_comp(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    work = project / "04_comp" / "fusion" / "work"
    work.mkdir(parents=True)
    (project / ".monostudio").mkdir()
    (project / ".monostudio" / "project.json").write_text("{}", encoding="utf-8")
    comp = work / "sh002_comp_v006.comp"
    comp.write_text(
        'Composition {\n\tTools = {\n\t\tMONOS_Output = Saver {\n'
        '\t\t\tInputs = {\n\t\t\t\tClip = Input {\n\t\t\t\t\tValue = Clip {\n'
        '\t\t\t\t\t\tFilename = "Comp:\\\\render\\\\sh002_comp_v006\\\\sh002_comp_v006.0000.exr",\n'
        "\t\t\t\t\t},\n\t\t\t\t},\n\t\t\t},\n"
        '\t\t\tName = "MONOS_Output",\n\t\t},\n\t},\n}\n',
        encoding="utf-8",
    )
    spec = build_comp_saver_spec(
        entity_name="sh002",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    result = apply_comp_saver_fix(
        comp,
        spec,
        end_render_script=True,
        project_root=project,
    )
    assert result == "updated"
    text = read_comp_text(comp)
    assert "EndRenderScript = Input" in text
    assert "notify.cmd" in text
    assert project_fusion_discord_script_path(project).is_file()
    lua = build_saver_end_render_lua(project_fusion_discord_script_path(project))
    assert "Comp:[/\\\\]" in lua
    assert '[[cmd /c call "' in lua
    assert "notify.cmd" in lua
    assert ']]],' not in lua
    assert lua.count("]],") == 1
