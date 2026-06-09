"""Build ViewItems for the workspace project browser / picker."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.workspace_reader import DiscoveredProject, ProjectQuickStats
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind


def build_project_view_items(
    workspace_projects: list[DiscoveredProject],
    *,
    quick_stats_by_root: dict[str, ProjectQuickStats],
    status_by_root: dict[str, str],
) -> list[ViewItem]:
    items: list[ViewItem] = []
    for proj in workspace_projects:
        key = str(proj.root)
        stats = quick_stats_by_root.get(key)
        if stats is None:
            stats = ProjectQuickStats(
                status=status_by_root.get(key, "WAITING"),
                assets_count=None,
                shots_count=None,
                last_modified=None,
            )
        items.append(
            ViewItem(
                kind=ViewItemKind.PROJECT,
                name=proj.name or proj.root.name,
                type_badge="project",
                path=proj.root,
                ref=stats,
            )
        )
    return items


def project_picker_empty_message(*, workspace_root: Path | None) -> str:
    if workspace_root is None:
        return "Set a workspace folder in Settings to browse projects."
    return "No projects found in this workspace"
