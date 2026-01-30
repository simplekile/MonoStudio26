from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredProject:
    name: str
    root: Path


def discover_projects(workspace_root: Path) -> list[DiscoveredProject]:
    """
    Workspace discovery (read-only):
    - Scan ONLY one level of subfolders
    - A folder is a project ONLY if .monostudio/project.json exists
    - Read project display name from project.json field "name" if present; else folder name
    - Ignore invalid/unexpected folders silently
    - No filesystem mutation
    """
    projects: list[DiscoveredProject] = []

    try:
        children = [p for p in workspace_root.iterdir() if p.is_dir()]
    except FileNotFoundError:
        return []

    for child in sorted(children, key=lambda p: p.name.lower()):
        manifest = child / ".monostudio" / "project.json"
        try:
            if not manifest.is_file():
                continue
        except OSError:
            continue

        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            name = child.name

        projects.append(DiscoveredProject(name=name.strip(), root=child))

    return projects

