from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DccWorkState:
    """
    Filesystem-derived state for (department, dcc): resolved paths only; no manual flags.
    Populated by scan; UI derives status via resolve_dcc_status().
    """
    work_file_path: Path | None  # Path to work file if it exists on disk; None otherwise
    work_folder_exists: bool  # True if the DCC work folder exists (for "empty" vs "new")


@dataclass(frozen=True)
class Department:
    name: str
    path: Path
    work_path: Path
    publish_path: Path
    work_exists: bool
    # True only when at least one recognized work file exists under work_path.
    work_file_exists: bool
    # Primary DCC for this department (e.g. for "Open with"); first from work_file_dccs or resolved from meta.
    work_file_dcc: str | None
    # All DCC ids that have work files in this department (e.g. ["blender", "maya"] when both .blend and .ma exist).
    work_file_dccs: tuple[str, ...]
    publish_exists: bool
    latest_publish_version: str | None
    publish_version_count: int


@dataclass(frozen=True)
class Asset:
    asset_type: str  # char / prop / env (folder name)
    name: str
    path: Path
    departments: tuple[Department, ...]
    # Per (department_id, dcc_id): resolved work file path + folder existence. Source of truth from scan.
    dcc_work_states: tuple[tuple[tuple[str, str], DccWorkState], ...] = ()


@dataclass(frozen=True)
class Shot:
    name: str
    path: Path
    departments: tuple[Department, ...]
    dcc_work_states: tuple[tuple[tuple[str, str], DccWorkState], ...] = ()


@dataclass(frozen=True)
class ProjectIndex:
    root: Path
    assets: tuple[Asset, ...]
    shots: tuple[Shot, ...]


@dataclass
class InboxItem:
    """
    Single file or folder in the inbox tree. Used for inbox/<source>/<date>/... structure.
    """
    path: Path
    relative_path: str  # from inbox root, for meta key and tree display
    name: str
    is_dir: bool
    source: str | None  # "client" | "freelancer", from path or meta
    added_at: str | None  # ISO8601
    description: str | None
    children: list["InboxItem"]  # empty = no children; populated when is_dir

