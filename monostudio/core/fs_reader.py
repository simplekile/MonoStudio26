from __future__ import annotations

from pathlib import Path

from monostudio.core.models import Asset, Department, ProjectIndex, Shot


def _iter_dirs(path: Path) -> list[Path]:
    try:
        return sorted([p for p in path.iterdir() if p.is_dir()])
    except FileNotFoundError:
        return []


def _parse_version_dir_name(name: str) -> int | None:
    # Folder-name based version detection only.
    # Naming spec v1: v### (zero-padded). Ignore anything else silently.
    if len(name) != 4 or name[0].lower() != "v":
        return None
    digits = name[1:]
    if not digits.isdigit():
        return None
    return int(digits)


def _scan_publish_versions(publish_path: Path) -> tuple[str | None, int]:
    try:
        candidates: list[tuple[int, str]] = []
        for p in publish_path.iterdir():
            if not p.is_dir():
                continue
            v = _parse_version_dir_name(p.name)
            if v is None:
                continue
            candidates.append((v, p.name))

        if not candidates:
            return None, 0
        latest = max(candidates, key=lambda t: t[0])[1]
        return latest, len(candidates)
    except FileNotFoundError:
        return None, 0


def build_project_index(project_root: Path) -> ProjectIndex:
    """
    Phase 1: Filesystem is source of truth.
    - Scan only: project_root/assets and project_root/shots (top-level)
    - Map folders 1:1 to in-memory model objects
    - Skip missing folders silently
    - No validation, no auto-creation, no publish/version logic
    """
    assets_dir = project_root / "assets"
    shots_dir = project_root / "shots"

    assets: list[Asset] = []
    for asset_type_dir in _iter_dirs(assets_dir):
        asset_type = asset_type_dir.name
        for asset_dir in _iter_dirs(asset_type_dir):
            departments: list[Department] = []
            for dept_dir in _iter_dirs(asset_dir):
                dept_name = dept_dir.name
                work_path = dept_dir / "work"
                publish_path = dept_dir / "publish"
                work_exists = work_path.is_dir()
                publish_exists = publish_path.is_dir()
                if publish_exists:
                    latest_version, version_count = _scan_publish_versions(publish_path)
                else:
                    latest_version, version_count = None, 0
                departments.append(
                    Department(
                        name=dept_name,
                        path=dept_dir,
                        work_path=work_path,
                        publish_path=publish_path,
                        work_exists=work_exists,
                        publish_exists=publish_exists,
                        latest_publish_version=latest_version,
                        publish_version_count=version_count,
                    )
                )

            assets.append(
                Asset(
                    asset_type=asset_type,
                    name=asset_dir.name,
                    path=asset_dir,
                    departments=tuple(departments),
                )
            )

    shots: list[Shot] = []
    for shot_dir in _iter_dirs(shots_dir):
        departments: list[Department] = []
        for dept_dir in _iter_dirs(shot_dir):
            dept_name = dept_dir.name
            work_path = dept_dir / "work"
            publish_path = dept_dir / "publish"
            work_exists = work_path.is_dir()
            publish_exists = publish_path.is_dir()
            if publish_exists:
                latest_version, version_count = _scan_publish_versions(publish_path)
            else:
                latest_version, version_count = None, 0
            departments.append(
                Department(
                    name=dept_name,
                    path=dept_dir,
                    work_path=work_path,
                    publish_path=publish_path,
                    work_exists=work_exists,
                    publish_exists=publish_exists,
                    latest_publish_version=latest_version,
                    publish_version_count=version_count,
                )
            )
        shots.append(
            Shot(
                name=shot_dir.name,
                path=shot_dir,
                departments=tuple(departments),
            )
        )

    # Sort deterministically for stable UI ordering
    assets_sorted = tuple(sorted(assets, key=lambda a: (a.asset_type, a.name)))
    shots_sorted = tuple(sorted(shots, key=lambda s: s.name))

    return ProjectIndex(root=project_root, assets=assets_sorted, shots=shots_sorted)

