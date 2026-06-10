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
    status: str  # READY | PROGRESS | WAITING | BLOCKED | AT_RISK
    assets_count: int | None
    shots_count: int | None
    last_modified: str | None  # formatted timestamp


_PROJECT_STATUS_LABELS: dict[str, str] = {
    "READY": "Done",
    "PROGRESS": "In progress",
    "AT_RISK": "At risk",
    "WAITING": "Waiting",
    "BLOCKED": "Blocked",
}

_PROJECT_STATUS_COLORS: dict[str, str] = {
    "READY": "#10b981",
    "PROGRESS": "#f59e0b",
    "AT_RISK": "#ef4444",
    "WAITING": "#71717a",
    "BLOCKED": "#ef4444",
}

PROJECT_BROWSER_STATUS_KEYS: tuple[str, ...] = ("WAITING", "PROGRESS", "AT_RISK", "BLOCKED", "READY")
_BROWSER_STATUS_JSON_KEY = "browser_status"


def _project_manifest_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / "project.json"


def read_project_status_override(project_root: Path) -> str | None:
    """Manual project browser status from project.json, or None for automatic."""
    path = _project_manifest_path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = str(data.get(_BROWSER_STATUS_JSON_KEY) or "").strip().upper()
    if raw in PROJECT_BROWSER_STATUS_KEYS:
        return raw
    return None


def write_project_status_override(project_root: Path, status: str | None) -> bool:
    """
    Set or clear manual project browser status in project.json.
    ``None`` removes the override (automatic derived status).
    """
    root = Path(project_root)
    path = _project_manifest_path(root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if status is None:
        data.pop(_BROWSER_STATUS_JSON_KEY, None)
    else:
        key = str(status).strip().upper()
        if key not in PROJECT_BROWSER_STATUS_KEYS:
            return False
        data[_BROWSER_STATUS_JSON_KEY] = key
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def project_status_menu_entries() -> tuple[tuple[str, str], ...]:
    """(status_key, label) pairs for the project browser status menu."""
    return tuple((key, _PROJECT_STATUS_LABELS[key]) for key in PROJECT_BROWSER_STATUS_KEYS)


def project_status_label(status: str) -> str:
    key = (status or "WAITING").strip().upper()
    return _PROJECT_STATUS_LABELS.get(key, "Waiting")


def project_status_paint_key(status: str) -> str:
    key = (status or "WAITING").strip().upper()
    if key == "READY":
        return "ready"
    if key in ("AT_RISK", "BLOCKED"):
        return "blocked"
    if key == "PROGRESS":
        return "progress"
    return "waiting"


def project_status_color_hex(status: str) -> str:
    key = (status or "WAITING").strip().upper()
    return _PROJECT_STATUS_COLORS.get(key, "#71717a")


def project_status_display_labels() -> tuple[str, ...]:
    """All user-facing project status pill labels (for column width layout)."""
    return tuple(_PROJECT_STATUS_LABELS.values())


def _derive_project_status_light(
    *,
    has_assets_dir: bool,
    has_shots_dir: bool,
    assets_count: int | None,
    shots_count: int | None,
) -> str:
    """Fast heuristic from folder presence and counts only (safe for startup)."""
    if not has_assets_dir or not has_shots_dir:
        return "BLOCKED"
    if (assets_count or 0) > 0 or (shots_count or 0) > 0:
        return "PROGRESS"
    return "WAITING"


def _derive_project_status_schedule(project_root: Path) -> str | None:
    """Schedule-aware status; returns None when deep scan fails or is inconclusive."""
    try:
        from monostudio.core.project_lifecycle import is_project_done_for_notifications

        if is_project_done_for_notifications(project_root):
            return "READY"
    except Exception:
        pass

    try:
        from monostudio.core.fs_reader import build_project_index
        from monostudio.core.project_schedule import read_project_schedule
        from monostudio.core.schedule_planner import (
            STATUS_DONE,
            STATUS_EXCLUDED,
            build_planned_bars,
            count_overdue_bars,
        )

        index = build_project_index(project_root)
        schedule = read_project_schedule(project_root)
        bars = build_planned_bars(
            project_root,
            index,
            schedule,
            include_shots=True,
            include_assets=True,
        )
        in_scope = [b for b in bars.values() if b.status != STATUS_EXCLUDED]
        if in_scope:
            if count_overdue_bars(bars) > 0:
                return "AT_RISK"
            if all(b.status == STATUS_DONE for b in in_scope):
                return "READY"
            return "PROGRESS"
    except Exception:
        return None
    return None


def read_project_quick_stats(project_root: Path, *, schedule_aware: bool = False) -> ProjectQuickStats:
    """
    Read-only project stats for the Projects browser.

    Default (``schedule_aware=False``): folder counts + light status — no deep index scan.
    Use ``schedule_aware=True`` when the UI can afford a full pipeline read (project picker).
    """
    from monostudio.core.structure_registry import StructureRegistry

    struct_reg = StructureRegistry.for_project(project_root)
    _assets_folder = struct_reg.get_folder("assets")
    _shots_folder = struct_reg.get_folder("shots")

    def _count_shots() -> int | None:
        shots_dir = project_root / _shots_folder
        try:
            if not shots_dir.is_dir():
                return 0
            return sum(1 for p in shots_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return None

    def _count_assets() -> int | None:
        assets_dir = project_root / _assets_folder
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

    try:
        has_assets_dir = (project_root / _assets_folder).is_dir()
        has_shots_dir = (project_root / _shots_folder).is_dir()
    except OSError:
        has_assets_dir = False
        has_shots_dir = False

    assets_count = _count_assets()
    shots_count = _count_shots()

    override = read_project_status_override(project_root)
    if override is not None:
        try:
            import datetime as _dt

            last_modified = _dt.datetime.fromtimestamp(project_root.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_modified = None
        return ProjectQuickStats(
            status=override,
            assets_count=assets_count,
            shots_count=shots_count,
            last_modified=last_modified,
        )

    light = _derive_project_status_light(
        has_assets_dir=has_assets_dir,
        has_shots_dir=has_shots_dir,
        assets_count=assets_count,
        shots_count=shots_count,
    )
    if schedule_aware:
        status = _derive_project_status_schedule(project_root) or light
    else:
        status = light

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

