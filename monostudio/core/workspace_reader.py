from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredProject:
    name: str
    root: Path


@dataclass(frozen=True)
class ProjectQuickStats:
    status: str  # READY | PROGRESS | WAITING | BLOCKED
    assets_count: int | None
    shots_count: int | None
    last_modified: str | None  # formatted timestamp


def read_project_quick_stats(project_root: Path) -> ProjectQuickStats:
    """
    Lightweight, read-only project stats for the Projects browser.
    Avoid deep scans: counts only folder structure.
    """

    def _count_shots() -> int | None:
        shots_dir = project_root / "shots"
        try:
            if not shots_dir.is_dir():
                return 0
            return sum(1 for p in shots_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return None

    def _count_assets() -> int | None:
        assets_dir = project_root / "assets"
        try:
            if not assets_dir.is_dir():
                return 0
            total = 0
            for t in assets_dir.iterdir():
                if not t.is_dir() or t.name.startswith("."):
                    continue
                try:
                    total += sum(1 for p in t.iterdir() if p.is_dir() and not p.name.startswith("."))
                except OSError:
                    continue
            return total
        except OSError:
            return None

    # Status is derived (no user-facing enforcement):
    # - BLOCKED: required folders missing
    # - PROGRESS: has any assets/shots
    # - WAITING: structure exists but empty / unknown
    try:
        has_assets_dir = (project_root / "assets").is_dir()
        has_shots_dir = (project_root / "shots").is_dir()
    except OSError:
        has_assets_dir = False
        has_shots_dir = False

    assets_count = _count_assets()
    shots_count = _count_shots()

    if not has_assets_dir or not has_shots_dir:
        status = "BLOCKED"
    elif (assets_count or 0) > 0 or (shots_count or 0) > 0:
        status = "PROGRESS"
    else:
        status = "WAITING"

    try:
        import datetime as _dt

        last_modified = _dt.datetime.fromtimestamp(project_root.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        last_modified = None

    return ProjectQuickStats(
        status=status,
        assets_count=assets_count,
        shots_count=shots_count,
        last_modified=last_modified,
    )


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

