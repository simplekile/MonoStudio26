"""Project Guide filesystem helpers — palette search, department resolve."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from monostudio.core.project_create import PROJECT_GUIDE_DEPARTMENTS
from monostudio.core.structure_registry import StructureRegistry


def get_project_guide_root(project_root: Path) -> Path | None:
    """Return `<project_root>/<project_guide_folder>` when it exists."""
    root = Path(project_root)
    struct_reg = StructureRegistry.for_project(root)
    guide_root = root / struct_reg.get_folder("project_guide")
    return guide_root if guide_root.is_dir() else None


def resolve_project_guide_department(project_root: Path, item_path: Path) -> str | None:
    """First segment under project_guide (reference, script, …) for *item_path*, if valid."""
    guide_root = get_project_guide_root(project_root)
    if guide_root is None:
        return None
    try:
        rel = Path(item_path).resolve().relative_to(guide_root.resolve())
    except (OSError, ValueError):
        return None
    if not rel.parts:
        return None
    dept = rel.parts[0].casefold()
    return dept if dept in PROJECT_GUIDE_DEPARTMENTS else None


def flatten_project_guide_for_palette(project_root: Path) -> list[dict[str, Any]]:
    """
    Flat search rows for command palette.
    Searchable: filename, relative path, department, full path.
    """
    guide_root = get_project_guide_root(project_root)
    if guide_root is None:
        return []

    rows: list[dict[str, Any]] = []
    for dept in PROJECT_GUIDE_DEPARTMENTS:
        dept_root = guide_root / dept
        if not dept_root.is_dir():
            continue
        try:
            entries = sorted(dept_root.rglob("*"))
        except OSError:
            continue
        for path in entries:
            if any(part.startswith(".") for part in path.parts):
                continue
            if not path.exists():
                continue
            name = path.name.strip()
            if not name:
                continue
            try:
                rel = path.relative_to(dept_root).as_posix()
            except ValueError:
                continue
            subtitle_parts = ["Project Guide", dept]
            parent = Path(rel).parent.as_posix()
            if parent and parent != ".":
                subtitle_parts.append(parent)
            rows.append(
                {
                    "path": str(path),
                    "department": dept,
                    "title": name,
                    "subtitle": " · ".join(subtitle_parts),
                    "search_text": " ".join(
                        b for b in (name, rel, dept, "project guide", str(path)) if b
                    ).casefold(),
                }
            )
    return rows
