"""Fusion comp scripts — project deploy + Saver EndRenderScript generation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from monostudio.core.app_paths import get_app_base_path


def find_project_root(path: Path) -> Path | None:
    """Walk parents until ``.monostudio/project.json`` is found."""
    try:
        current = path.resolve()
    except OSError:
        return None
    if current.is_file():
        current = current.parent
    for _ in range(48):
        marker = current / ".monostudio" / "project.json"
        if marker.is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_workspace_root_from_project(project_root: Path) -> Path | None:
    """Parent folder with workspace ``.monostudio/integrations.json`` (typical layout)."""
    parent = project_root.parent
    if (parent / ".monostudio" / "integrations.json").is_file():
        return parent
    return None


def project_fusion_discord_script_path(project_root: Path) -> Path:
    return project_root / ".monostudio" / "fusion" / "discord.py"


def _bundled_discord_script_template() -> Path:
    return get_app_base_path() / "monostudio_data" / "fusion" / "discord.py"


def fusion_render_webhook_urls(
    project_root: Path,
    *,
    workspace_root: Path | None = None,
) -> list[str]:
    """Discord webhook URLs subscribed to ``fusion_render_finished``."""
    return _resolve_discord_webhook_urls(
        project_root=project_root,
        workspace_root=workspace_root,
    )


def refresh_fusion_discord_webhooks_for_workspace(workspace_root: Path) -> int:
    """Rewrite ``.monostudio/fusion/webhooks.json`` for projects that use Fusion notify."""
    from monostudio.core.workspace_reader import discover_projects

    ws = Path(workspace_root).resolve()
    updated = 0
    for proj in discover_projects(ws):
        root = Path(proj.root)
        fusion_dir = root / ".monostudio" / "fusion"
        if not fusion_dir.is_dir():
            continue
        ensure_project_fusion_discord_script(root, workspace_root=ws)
        updated += 1
    return updated


def _resolve_discord_webhook_urls(
    *,
    project_root: Path,
    workspace_root: Path | None = None,
) -> list[str]:
    ws = workspace_root or find_workspace_root_from_project(project_root)
    if ws is not None:
        try:
            from monostudio.core.integrations_config import load_integrations, webhook_urls_for_event

            urls = webhook_urls_for_event(load_integrations(ws), "fusion_render_finished")
            if urls:
                return list(dict.fromkeys(urls))
        except Exception:
            pass
    webhooks_json = project_root / ".monostudio" / "fusion" / "webhooks.json"
    if webhooks_json.is_file():
        try:
            raw = json.loads(webhooks_json.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [str(u).strip() for u in raw if str(u).strip()]
        except (OSError, json.JSONDecodeError):
            pass
    legacy = project_root / ".monostudio" / "fusion" / "webhook.url"
    try:
        line = legacy.read_text(encoding="utf-8").strip()
        return [line] if line else []
    except OSError:
        return []


def ensure_project_fusion_discord_script(
    project_root: Path,
    *,
    workspace_root: Path | None = None,
) -> Path:
    """Copy bundled ``discord.py`` into the project and refresh webhook list."""
    dest_dir = project_root / ".monostudio" / "fusion"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_py = dest_dir / "discord.py"
    template = _bundled_discord_script_template()
    if template.is_file():
        shutil.copy2(template, dest_py)
    urls = _resolve_discord_webhook_urls(project_root=project_root, workspace_root=workspace_root)
    (dest_dir / "webhooks.json").write_text(
        json.dumps(urls, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    first = urls[0] if urls else ""
    (dest_dir / "webhook.url").write_text(f"{first}\n", encoding="utf-8")
    return dest_py


def build_saver_end_render_lua(discord_py: Path) -> str:
    """Lua run by Fusion Saver after render — resolves Comp: paths, calls project discord.py."""
    # Inside Lua [[...]] long strings, Windows paths do not need backslash doubling.
    py_path = str(discord_py.resolve())
    return (
        "local output = tostring(self.Clip.Filename)\n\n"
        'if output:match("^Comp:[/\\\\]") then\n'
        "    local compDir = tostring(comp.Filename):match(\"^(.*)[/\\\\][^/\\\\]+$\")\n"
        "    if compDir then\n"
        "        output = compDir .. output:sub(6)\n"
        "    end\n"
        "end\n\n"
        "local saver = tostring(self.Name)\n"
        "local compname = tostring(comp.Name)\n\n"
        "local cmd = string.format(\n"
        f'    [[cmd /c python "{py_path}" "%s" "%s" "%s"]],\n'
        "    output,\n"
        "    saver,\n"
        "    compname\n"
        ")\n\n"
        "os.execute(cmd)"
    )
