"""Tests for comp preflight apply target (new version vs in-place)."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.comp_render_paths import rebuild_comp_saver_spec, resolve_next_comp_work_path
from monostudio.core.comp_saver_io import CompSaverAuditStatus, audit_comp_saver
from monostudio.ui_qt.comp_preflight_hub import apply_preflight_plan
from monostudio.ui_qt.comp_preflight_models import CompPreflightPlan, CompPreflightScan


def _minimal_comp(path: Path, loader: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'Composition {{\n\tTools = {{\n\t\tL1 = Loader {{\n'
        f'\t\t\tClips = {{ Clip {{ Filename = "{loader}", }} }},\n'
        f"\t\t}},\n\t}},\n}}\n",
        encoding="utf-8",
    )


def test_resolve_next_comp_work_path(tmp_path: Path) -> None:
    work = tmp_path / "fusion" / "work"
    work.mkdir(parents=True)
    (work / "sh009_comp_v001.comp").write_text("Composition { }\n", encoding="utf-8")
    (work / "sh009_comp_v003.comp").write_text("Composition { }\n", encoding="utf-8")
    nxt = resolve_next_comp_work_path(work / "sh009_comp_v003.comp", prefix="sh009_comp")
    assert nxt.name == "sh009_comp_v004.comp"


def test_rebuild_spec_for_new_version(tmp_path: Path) -> None:
    from monostudio.core.comp_render_paths import build_comp_saver_spec

    work = tmp_path / "fusion" / "work"
    v3 = work / "sh009_comp_v003.comp"
    v3.parent.mkdir(parents=True)
    v3.write_text("Composition { }\n", encoding="utf-8")
    base = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=v3,
        work_path=work,
    )
    v4 = work / "sh009_comp_v004.comp"
    v4.write_text("Composition { }\n", encoding="utf-8")
    rebuilt = rebuild_comp_saver_spec(v4, base, entity_name="sh009")
    assert rebuilt.work_version == 4
    assert rebuilt.stem == "sh009_comp_v004"
    assert "sh009_comp_v004" in rebuilt.saver_path_fusion


def test_apply_preflight_to_new_version(tmp_path: Path) -> None:
    work = tmp_path / "04_comp" / "fusion" / "work"
    render = work / "render"
    v1 = render / "sh009_lighting_v001"
    v1.mkdir(parents=True)
    (v1 / "sh009_lighting_v001.0001.exr").write_bytes(b"x")
    v2 = render / "sh009_lighting_v002"
    v2.mkdir(parents=True)
    (v2 / "sh009_lighting_v002.0001.exr").write_bytes(b"x")

    comp_v3 = work / "sh009_comp_v003.comp"
    loader = str((v1 / "sh009_lighting_v001.0001.exr")).replace("\\", "\\\\")
    _minimal_comp(comp_v3, loader)

    from monostudio.core.comp_render_paths import build_comp_saver_spec

    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp_v3,
        work_path=work,
    )
    audit = audit_comp_saver(comp_v3, spec)
    upstream = __import__(
        "monostudio.core.comp_upstream_render_check",
        fromlist=["audit_comp_upstream_renders"],
    ).audit_comp_upstream_renders(comp_v3, entity_name="sh009")
    scan = CompPreflightScan(
        comp_path=comp_v3,
        spec=spec,
        entity_name="sh009",
        saver_audit=audit,
        upstream_issues=upstream,
    )
    plan = CompPreflightPlan()
    plan.init_upstream_defaults(scan)
    plan.upstream_reviewed = True

    original_v3 = comp_v3.read_text(encoding="utf-8")
    result = apply_preflight_plan(scan, plan)
    assert result.ok
    assert result.target_path.name == "sh009_comp_v004.comp"
    assert result.target_path.is_file()
    assert comp_v3.read_text(encoding="utf-8") == original_v3
    text = result.target_path.read_text(encoding="utf-8")
    assert "sh009_lighting_v002" in text


def test_init_upstream_defaults_skips_downgrade(tmp_path: Path) -> None:
    from monostudio.core.comp_render_paths import build_comp_saver_spec
    from monostudio.core.comp_saver_io import CompSaverAudit, CompSaverAuditStatus
    from monostudio.core.comp_upstream_render_check import UpstreamRenderIssue, UpstreamRenderStatus
    from monostudio.ui_qt.comp_preflight_models import CompPreflightPlan, is_upstream_version_upgrade

    upgrade = UpstreamRenderIssue(
        status=UpstreamRenderStatus.STALE,
        base_prefix="sh009_lighting",
        department="lighting",
        entity_name="sh009",
        comp_version=1,
        latest_version=2,
        latest_folder=None,
        sample_loader_path="x.exr",
        loader_count=1,
        message="upgrade",
    )
    downgrade = UpstreamRenderIssue(
        status=UpstreamRenderStatus.STALE,
        base_prefix="sh009_particles",
        department="particles",
        entity_name="sh009",
        comp_version=2,
        latest_version=1,
        latest_folder=None,
        sample_loader_path="y.exr",
        loader_count=3,
        message="downgrade",
    )
    assert is_upstream_version_upgrade(upgrade) is True
    assert is_upstream_version_upgrade(downgrade) is False

    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v003.comp"
    comp.write_text("Composition { }\n", encoding="utf-8")
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    scan = CompPreflightScan(
        comp_path=comp,
        spec=spec,
        entity_name="sh009",
        saver_audit=CompSaverAudit(
            comp_path=comp,
            status=CompSaverAuditStatus.OK,
            expected_path="",
            current_path=None,
            managed_tool_var=None,
        ),
        upstream_issues=[upgrade, downgrade],
    )
    plan = CompPreflightPlan()
    plan.init_upstream_defaults(scan)
    assert plan.upstream_selected == [upgrade]


def test_scan_has_issues_when_only_missing_end_render_script(tmp_path: Path) -> None:
    from monostudio.core.comp_render_paths import build_comp_saver_spec
    from monostudio.core.comp_saver_io import CompSaverAudit, CompSaverAuditStatus
    from monostudio.ui_qt.comp_preflight_hub import scan_comp_preflight
    from monostudio.ui_qt.comp_preflight_models import CompPreflightScan

    work = tmp_path / "work"
    work.mkdir()
    comp = work / "sh009_comp_v002.comp"
    comp.write_text(
        """Composition {
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
""",
        encoding="utf-8",
    )
    spec = build_comp_saver_spec(
        entity_name="sh009",
        department="comp",
        work_file=comp,
        work_path=work,
    )
    scan = scan_comp_preflight(comp_path=comp, spec=spec, entity_name="sh009")
    assert scan.saver_audit.status == CompSaverAuditStatus.OK
    assert scan.saver_missing_end_render_script is True
    assert scan.saver_shows_in_hub is True
    assert scan.has_issues is True
    assert not scan.upstream_actionable

    audit = CompSaverAudit(
        comp_path=comp,
        status=CompSaverAuditStatus.OK,
        expected_path=spec.saver_path_fusion,
        current_path=spec.saver_path_fusion,
        managed_tool_var="MONOS_Output",
        has_end_render_script=True,
    )
    scan_ok_script = CompPreflightScan(
        comp_path=comp,
        spec=spec,
        entity_name="sh009",
        saver_audit=audit,
        upstream_issues=[],
    )
    assert scan_ok_script.has_issues is False
