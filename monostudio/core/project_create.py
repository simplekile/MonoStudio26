from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from monostudio.core.department_registry import (
    DepartmentRegistry,
    get_project_pipeline_dir,
    save_project_departments,
)
from monostudio.core.project_id import generate_project_id
from monostudio.core.type_registry import save_project_types, TypeRegistry


@dataclass(frozen=True)
class CreatedProject:
    project_id: str
    display_name: str
    start_date: str  # YYYY-MM-DD
    root: Path


def create_new_project(
    *,
    workspace_root: Path,
    display_name: str,
    start_date: str,
    created_date: date | None = None,
) -> CreatedProject:
    """
    Safe project creation (read/write):
    - Creates a new project folder under workspace_root using an auto-generated Project ID.
    - Creates required structure: assets/, shots/, .monostudio/project.json,
      .monostudio/pipeline/types.json and departments.json (default TypeRegistry and
      DepartmentRegistry so scan and UI have config on disk).
    - Writes metadata deterministically.
    - On failure: best-effort rollback inside the new project folder only.
    """

    name = (display_name or "").strip()
    if not name:
        raise ValueError("Project Name is required.")

    if not workspace_root.is_dir():
        raise FileNotFoundError("Workspace root folder does not exist.")

    project_id = generate_project_id(name, created_date=created_date)
    if not project_id:
        raise ValueError("Failed to generate Project ID.")

    project_root = workspace_root / project_id
    if project_root.exists():
        raise FileExistsError("Target project folder already exists.")

    created_paths: list[Path] = []
    try:
        (project_root / "assets").mkdir(parents=True, exist_ok=False)
        created_paths.append(project_root / "assets")
        (project_root / "shots").mkdir(parents=True, exist_ok=False)
        created_paths.append(project_root / "shots")

        monostudio_dir = project_root / ".monostudio"
        monostudio_dir.mkdir(parents=True, exist_ok=False)
        created_paths.append(monostudio_dir)

        manifest = monostudio_dir / "project.json"
        manifest.write_text(
            json.dumps(
                {
                    "id": project_id,
                    "name": name,
                    "start_date": start_date,
                    "schema": 1,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # Create pipeline config so TypeRegistry and DepartmentRegistry load from disk.
        pipeline_dir = get_project_pipeline_dir(project_root)
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        type_reg = TypeRegistry.for_project(project_root)
        if not save_project_types(project_root, type_reg.get_raw_mapping()):
            pass  # non-fatal; app still uses in-memory default
        dept_reg = DepartmentRegistry.for_project(project_root)
        if not save_project_departments(project_root, dept_reg.get_raw_mapping()):
            pass  # non-fatal; app still uses in-memory default

        return CreatedProject(project_id=project_id, display_name=name, start_date=start_date, root=project_root)
    except Exception:
        # Rollback inside project_root only.
        try:
            if project_root.exists():
                shutil.rmtree(project_root)
        except Exception:
            # Best-effort; caller may need to handle partials manually.
            pass
        raise

