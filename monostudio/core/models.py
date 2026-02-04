from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Department:
    name: str
    path: Path
    work_path: Path
    publish_path: Path
    work_exists: bool
    # True only when a recognized work file exists under work_path (e.g. <item>.blend).
    work_file_exists: bool
    # Resolved DCC id for the work file (e.g. "blender"). None if unknown.
    work_file_dcc: str | None
    publish_exists: bool
    latest_publish_version: str | None
    publish_version_count: int


@dataclass(frozen=True)
class Asset:
    asset_type: str  # char / prop / env (folder name)
    name: str
    path: Path
    departments: tuple[Department, ...]


@dataclass(frozen=True)
class Shot:
    name: str
    path: Path
    departments: tuple[Department, ...]


@dataclass(frozen=True)
class ProjectIndex:
    root: Path
    assets: tuple[Asset, ...]
    shots: tuple[Shot, ...]

