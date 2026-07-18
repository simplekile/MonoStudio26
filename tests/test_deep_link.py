"""Unit tests for monostudio:// deep links (short entity hash)."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.deep_link import (
    ENTITY_ID_PARAM,
    build_open_deep_link,
    entity_path_short_id,
    parse_open_deep_link,
    resolve_entity_short_id,
)


def test_entity_path_short_id_stable() -> None:
    rel = (
        "_project_guide/reference/_video/"
        "Obito Uchiha [Ronin] CGI Animation - _The Wandering Savior_ _ Naruto Mobile.mkv"
    )
    a = entity_path_short_id(rel)
    b = entity_path_short_id(rel.replace("/", "\\"))
    assert len(a) == 10
    assert a == b
    assert a == entity_path_short_id("  " + rel + "  ")


def test_build_open_uses_short_entity_id_and_page_alias() -> None:
    rel = "project_guide/reference/_video/long named clip.mkv"
    url = build_open_deep_link("260425_grn_vp94", "Project Guide", entity=rel)
    assert "entity=" not in url
    assert f"{ENTITY_ID_PARAM}=" in url
    assert "page=guide" in url
    assert "Project%20Guide" not in url
    assert entity_path_short_id(rel) in url
    assert len(url) < 120


def test_parse_short_and_legacy_entity() -> None:
    rel = "inbox/client/2026-07-15/file.mov"
    short = build_open_deep_link("proj_a", "Inbox", entity=rel)
    parsed = parse_open_deep_link(short)
    assert parsed is not None
    assert parsed.page == "Inbox"
    assert parsed.entity is None
    assert parsed.entity_id == entity_path_short_id(rel)

    legacy = (
        "monostudio://open?project=proj_a&page=Project%20Guide"
        "&entity=_project_guide%2Freference%2Fclip.mkv"
    )
    legacy_parsed = parse_open_deep_link(legacy)
    assert legacy_parsed is not None
    assert legacy_parsed.page == "Project Guide"
    assert legacy_parsed.entity == "_project_guide/reference/clip.mkv"
    assert legacy_parsed.entity_id is None


def test_resolve_entity_short_id_on_disk(tmp_path: Path) -> None:
    guide = tmp_path / "project_guide" / "reference" / "_video"
    guide.mkdir(parents=True)
    name = "Obito Uchiha [Ronin] CGI Animation - clip.mkv"
    target = guide / name
    target.write_bytes(b"x")
    rel = f"project_guide/reference/_video/{name}"
    eid = entity_path_short_id(rel)
    found = resolve_entity_short_id(tmp_path, "Project Guide", eid)
    assert found == rel.replace("\\", "/")


def test_resolve_entity_short_id_assets_shallow(tmp_path: Path) -> None:
    asset = tmp_path / "assets" / "Character" / "char_hero"
    (asset / "modelling" / "work").mkdir(parents=True)
    (asset / "modelling" / "work" / "char_hero_modelling_v001.ma").write_bytes(b"x")
    rel = "assets/Character/char_hero"
    eid = entity_path_short_id(rel)
    found = resolve_entity_short_id(tmp_path, "Assets", eid)
    assert found == rel
    # Nested work file should not be required / matched for Assets page hash of entity root
    nested_rel = "assets/Character/char_hero/modelling/work/char_hero_modelling_v001.ma"
    assert resolve_entity_short_id(tmp_path, "Assets", entity_path_short_id(nested_rel)) is None


def test_resolve_entity_short_id_shots_flat(tmp_path: Path) -> None:
    """Shots are direct children of the shots folder (not seq/shot)."""
    shot = tmp_path / "shots" / "sh005"
    (shot / "groom" / "work").mkdir(parents=True)
    rel = "shots/sh005"
    eid = entity_path_short_id(rel)
    assert eid == "f334e27982"
    found = resolve_entity_short_id(tmp_path, "Shots", eid)
    assert found == rel
    # Dept folder under the shot must not be matched as the entity
    nested = "shots/sh005/groom"
    assert resolve_entity_short_id(tmp_path, "Shots", entity_path_short_id(nested)) is None


def test_resolve_entity_short_id_shots_custom_folder(tmp_path: Path) -> None:
    """StructureRegistry may rename shots → 02_shots (mono2026 preset)."""
    pipeline = tmp_path / ".monostudio" / "pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "structure.json").write_text(
        '{"folders":{"shots":{"label":"Shots","folder":"02_shots"}}}',
        encoding="utf-8",
    )
    shot = tmp_path / "02_shots" / "sh005"
    shot.mkdir(parents=True)
    rel = "02_shots/sh005"
    eid = entity_path_short_id(rel)
    assert eid == "3324aaf353"
    found = resolve_entity_short_id(tmp_path, "Shots", eid)
    assert found == rel


def test_legacy_entity_path_flag() -> None:
    rel = "shots/sh005"
    url = build_open_deep_link("p", "Shots", entity=rel, legacy_entity_path=True)
    assert "entity=" in url
    parsed = parse_open_deep_link(url)
    assert parsed is not None
    assert parsed.entity == rel
    assert parsed.entity_id is None


def test_extract_monos_from_html_href() -> None:
    from monostudio.core.deep_link import extract_monos_deep_link_from_text

    url = "monostudio://open?project=p&page=inbox&entity_id=abcdefghij"
    html = f'<a href="{url}">Inbox · clip.mov</a>'
    assert extract_monos_deep_link_from_text(html) == url
