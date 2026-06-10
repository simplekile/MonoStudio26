"""Build ViewItems for the workspace project browser / picker."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from monostudio.core.workspace_reader import DiscoveredProject, ProjectQuickStats
from monostudio.ui_qt.view_items import ViewItem, ViewItemKind

if TYPE_CHECKING:
    from monostudio.ui_qt.main_view import MainView


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


def filter_project_view_items(items: list[ViewItem], query: str) -> list[ViewItem]:
    q = (query or "").strip().casefold()
    if not q:
        return items
    out: list[ViewItem] = []
    for it in items:
        name = (it.name or "").casefold()
        path = str(it.path or "").casefold()
        if q in name or q in path:
            out.append(it)
    return out


def project_browser_empty_message(*, workspace_root: Path | None) -> str:
    if workspace_root is None:
        return "Set a workspace folder in Settings to browse projects."
    return "No projects found in this workspace"


def project_picker_empty_message(*, workspace_root: Path | None) -> str:
    """Alias kept for callers that still use the picker name."""
    return project_browser_empty_message(workspace_root=workspace_root)


def populate_project_browser_main_view(
    main_view: MainView,
    *,
    workspace_root: Path | None,
    workspace_projects: list[DiscoveredProject],
    quick_stats_by_root: dict[str, ProjectQuickStats],
    status_by_root: dict[str, str],
    search_query: str = "",
    preserve_selection_id: str | None = None,
) -> None:
    """Load workspace projects into MainView — same data path as the Projects page."""
    items = build_project_view_items(
        workspace_projects,
        quick_stats_by_root=quick_stats_by_root,
        status_by_root=status_by_root,
    )
    items = filter_project_view_items(items, search_query)
    q = (search_query or "").strip()
    if workspace_root is None:
        main_view.set_empty_override(project_browser_empty_message(workspace_root=None))
    elif not items and q:
        main_view.set_empty_override(f'No matches for "{q}"')
    elif not items:
        main_view.set_empty_override(project_browser_empty_message(workspace_root=workspace_root))
    else:
        main_view.set_empty_override(None)
    main_view.set_active_department(None)
    main_view.set_selected_asset_type(None)
    main_view.set_items(items, preserve_selection_id=preserve_selection_id)
