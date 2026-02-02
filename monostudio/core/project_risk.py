from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path

from monostudio.core.fs_reader import build_project_index
from monostudio.core.models import ProjectIndex
from monostudio.core.risk_analyzer import ProjectSnapshot, RiskLevel, analyze_project_risk


class ExternalReferencesStatus(str, Enum):
    YES = "Yes"
    NO = "No"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class ProjectRenameImpact:
    asset_count: int | None
    shot_count: int | None
    total_publish_versions: int | None
    external_references: ExternalReferencesStatus
    has_render_cache: bool | None
    risk_level: RiskLevel


def can_force_rename_project() -> bool:
    """
    Future hook for role-based permissions (Lead/Admin).
    No permission system yet: assume trusted local user.
    """

    return True


def assess_force_rename_project_id(
    project_root: Path,
    *,
    project_index: ProjectIndex | None = None,
    max_ref_scan_files: int = 250,
    max_ref_scan_total_bytes: int = 10 * 1024 * 1024,
    max_ref_scan_file_bytes: int = 2 * 1024 * 1024,
) -> ProjectRenameImpact:
    """
    Risk assessment for the migration-level operation "Force Rename Project ID".

    This is a best-effort evaluation. If the system cannot safely evaluate any metric,
    the result becomes CRITICAL with ExternalReferencesStatus.UNKNOWN.
    """

    try:
        idx = project_index or build_project_index(project_root)
        asset_count: int | None = len(idx.assets)
        shot_count: int | None = len(idx.shots)
        total_publish_versions: int | None = _sum_publish_versions(idx)
    except Exception:
        asset_count = None
        shot_count = None
        total_publish_versions = None

    external = detect_external_references(
        project_root,
        max_files=max_ref_scan_files,
        max_total_bytes=max_ref_scan_total_bytes,
        max_file_bytes=max_ref_scan_file_bytes,
    )

    has_external_refs: bool | None
    if external == ExternalReferencesStatus.YES:
        has_external_refs = True
    elif external == ExternalReferencesStatus.NO:
        has_external_refs = False
    else:
        has_external_refs = None

    render_cache = detect_render_cache(project_root)

    snapshot = ProjectSnapshot(
        project_id=project_root.name,
        asset_count=asset_count,
        shot_count=shot_count,
        publish_version_count=total_publish_versions,
        has_external_references=has_external_refs,
        has_render_cache=render_cache,
    )
    report = analyze_project_risk(snapshot)

    return ProjectRenameImpact(
        asset_count=asset_count,
        shot_count=shot_count,
        total_publish_versions=total_publish_versions,
        external_references=external,
        has_render_cache=render_cache,
        risk_level=report.risk_level,
    )


def _sum_publish_versions(idx: ProjectIndex) -> int:
    total = 0
    for a in idx.assets:
        for d in a.departments:
            total += int(d.publish_version_count or 0)
    for s in idx.shots:
        for d in s.departments:
            total += int(d.publish_version_count or 0)
    return total


def detect_render_cache(project_root: Path) -> bool | None:
    """
    Best-effort heuristic for render caches:
    - True if common cache folders exist and contain any entries
    - False if none exist
    - None if cannot evaluate safely
    """

    candidates = (
        project_root / "render_cache",
        project_root / "renders",
        project_root / "render",
        project_root / "cache",
        project_root / ".cache",
    )
    try:
        any_found = False
        for p in candidates:
            try:
                if not p.is_dir():
                    continue
            except OSError:
                return None
            any_found = True
            try:
                for _ in p.iterdir():
                    return True
            except OSError:
                return None
        return False if not any_found else False
    except Exception:
        return None


def detect_external_references(
    project_root: Path,
    *,
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
) -> ExternalReferencesStatus:
    """
    Best-effort heuristic:
    - Scan a limited set of text-like files for occurrences of the absolute project root path.
    - If any match found -> YES
    - If scan completes within limits and no match -> NO
    - If scan exceeds limits or encounters filesystem errors -> UNKNOWN
    """

    root_str = str(project_root)
    root_str_slash = root_str.replace("\\", "/")

    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".cache",
    }
    text_exts = {
        ".ma",
        ".nk",
        ".usd",
        ".usda",
        ".json",
        ".txt",
        ".ini",
        ".yml",
        ".yaml",
        ".py",
        ".mel",
        ".ps1",
        ".bat",
    }

    scanned_files = 0
    scanned_bytes = 0
    had_errors = False

    try:
        for dirpath, dirnames, filenames in os.walk(project_root, topdown=True):
            # Prune
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]

            for fn in filenames:
                if scanned_files >= max_files:
                    return ExternalReferencesStatus.UNKNOWN
                p = Path(dirpath) / fn
                if p.suffix.lower() not in text_exts:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    had_errors = True
                    continue
                if size > max_file_bytes:
                    # Too large to scan deterministically.
                    return ExternalReferencesStatus.UNKNOWN
                if scanned_bytes + size > max_total_bytes:
                    return ExternalReferencesStatus.UNKNOWN

                try:
                    data = p.read_bytes()
                except OSError:
                    had_errors = True
                    continue

                scanned_files += 1
                scanned_bytes += size

                try:
                    text = data.decode("utf-8", errors="ignore")
                except Exception:
                    had_errors = True
                    continue

                if root_str in text or root_str_slash in text:
                    return ExternalReferencesStatus.YES
    except OSError:
        return ExternalReferencesStatus.UNKNOWN

    if had_errors:
        return ExternalReferencesStatus.UNKNOWN
    return ExternalReferencesStatus.NO

