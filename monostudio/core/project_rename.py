from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from monostudio.core.project_risk import ProjectRenameImpact, assess_force_rename_project_id, can_force_rename_project


@dataclass(frozen=True)
class ForceRenameResult:
    old_root: Path
    new_root: Path
    impact: ProjectRenameImpact
    log_path: Path


def _is_safe_folder_name(name: str) -> bool:
    if not name:
        return False
    if name in (".", ".."):
        return False
    # Keep it strict and deterministic (matches dialog validator).
    for ch in name:
        if not (ch.islower() or ch.isdigit() or ch == "_"):
            return False
    return True


def _manifest_path(project_root: Path) -> Path:
    return project_root / ".monostudio" / "project.json"


def _read_manifest_text(project_root: Path) -> str | None:
    p = _manifest_path(project_root)
    try:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_manifest_json(project_root: Path, data: dict) -> None:
    from monostudio.core.atomic_write import atomic_write_text
    p = _manifest_path(project_root)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(p, content, encoding="utf-8")


def _append_force_rename_log(project_root: Path, payload: dict) -> Path:
    logs_dir = project_root / ".monostudio" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "force_rename_project_id.jsonl"
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    return path


def force_rename_project_id(
    *,
    project_root: Path,
    new_project_id: str,
    impact: ProjectRenameImpact | None = None,
) -> ForceRenameResult:
    """
    Execute the dangerous operation:
    - Rename the project root folder (Project ID)
    - Update internal metadata (project.json -> id)
    - Append project-level log record
    - Roll back if any step fails (best-effort)
    """

    if not can_force_rename_project():
        raise PermissionError("Force Rename Project ID is not permitted.")

    old_root = project_root
    if not old_root.is_dir():
        raise FileNotFoundError("Project root folder does not exist.")

    new_id = (new_project_id or "").strip()
    if not _is_safe_folder_name(new_id):
        raise ValueError("Invalid new Project ID.")
    if new_id == old_root.name:
        raise ValueError("New Project ID must be different from current Project ID.")

    new_root = old_root.parent / new_id
    if new_root.exists():
        raise FileExistsError("Target project folder already exists.")

    # Snapshot manifest content for rollback (best-effort).
    manifest_before_text = _read_manifest_text(old_root)

    # Always compute impact if not provided (risk level is a required log field).
    computed_impact = impact or assess_force_rename_project_id(old_root)

    renamed = False
    try:
        # 1) Rename root folder
        old_root.rename(new_root)
        renamed = True

        # 2) Update internal metadata references (project.json)
        manifest_path = _manifest_path(new_root)
        data: dict = {}
        try:
            if manifest_path.is_file():
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            # If manifest exists but is unreadable, treat as failure (avoid partial state).
            raise OSError("Failed to read project manifest after rename.")

        data["id"] = new_root.name
        _write_manifest_json(new_root, data)

        # 3) Append log record (REQUIRED)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from_project_id": old_root.name,
            "to_project_id": new_root.name,
            "risk_level": computed_impact.risk_level.value,
            "asset_count": computed_impact.asset_count,
            "shot_count": computed_impact.shot_count,
            "publish_version_count": computed_impact.total_publish_versions,
            "external_refs": computed_impact.external_references.value,
            "has_render_cache": computed_impact.has_render_cache,
        }
        log_path = _append_force_rename_log(new_root, payload)

        return ForceRenameResult(old_root=old_root, new_root=new_root, impact=computed_impact, log_path=log_path)

    except Exception as e:
        # Rollback best-effort: rename back + restore manifest.
        if renamed:
            try:
                new_root.rename(old_root)
            except Exception:
                # Could not rollback; re-raise the original error.
                raise e

            # Restore manifest content if we had it.
            if manifest_before_text is not None:
                try:
                    from monostudio.core.atomic_write import atomic_write_text
                    p = _manifest_path(old_root)
                    atomic_write_text(p, manifest_before_text, encoding="utf-8")
                except Exception:
                    # Rollback incomplete; re-raise original error.
                    raise e

        raise

