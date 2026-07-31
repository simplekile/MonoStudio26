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
    lua = build_saver_end_render_lua(py)
    assert 'string.format(\n    [[cmd /c python "' in lua
    assert '"%s" "%s" "%s"]],\n' in lua
    assert "]]]," not in lua
    assert "\\\\Dropbox\\\\" not in lua


def test_ensure_project_fusion_discord_script(tmp_path: Path) -> None:
    (tmp_path / ".monostudio").mkdir()
    (tmp_path / ".monostudio" / "project.json").write_text("{}", encoding="utf-8")
    py = ensure_project_fusion_discord_script(tmp_path)
    assert py == project_fusion_discord_script_path(tmp_path)
    assert py.is_file()
    assert (py.parent / "webhook.url").is_file()
    assert (py.parent / "webhooks.json").is_file()


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
    assert "discord.py" in text
    assert project_fusion_discord_script_path(project).is_file()
    lua = build_saver_end_render_lua(project_fusion_discord_script_path(project))
    assert "Comp:[/\\\\]" in lua
    assert '[[cmd /c python "' in lua
    assert ']]],' not in lua
    assert lua.count("]],") == 1
