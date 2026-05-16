"""Per-entity special folders (reference, concept) under asset/shot roots."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from monostudio.core.inbox_reader import resolve_destination_path
from monostudio.core.models import Asset, Shot

EntitySpecialFolderId = Literal["reference", "concept"]

ENTITY_SPECIAL_FOLDER_IDS: tuple[EntitySpecialFolderId, ...] = ("reference", "concept")

REF_PREVIEW_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}
)


def entity_special_folder_path(
    project_root: Path | None,
    entity: Asset | Shot,
    folder_id: EntitySpecialFolderId,
    *,
    dept_registry: object | None = None,
) -> Path | None:
    """Resolve ``{entity}/reference`` or ``{entity}/concept`` via inbox destination presets."""
    root = project_root
    if root is None:
        try:
            root = entity.path.parent.parent.parent
        except (AttributeError, IndexError):
            root = None
    if root is None:
        template = (folder_id or "").strip()
        if not template:
            return None
        return (entity.path / template).resolve()
    return resolve_destination_path(root, folder_id, entity, dept_registry)


def ensure_entity_special_folder(path: Path) -> bool:
    """Create folder if missing. Returns True if path exists and is a directory afterward."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir()
    except OSError:
        return path.is_dir()


def list_special_folder_files(folder: Path) -> list[Path]:
    """Top-level files only, newest mtime first."""
    if not folder.is_dir():
        return []
    files: list[Path] = []
    try:
        for entry in folder.iterdir():
            if entry.is_file():
                files.append(entry)
    except OSError:
        return []
    try:
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files.sort(key=lambda p: p.name.casefold())
    return files


def count_special_folder_files(folder: Path) -> int:
    return len(list_special_folder_files(folder))


def is_ref_preview_image(path: Path) -> bool:
    return path.suffix.casefold() in REF_PREVIEW_IMAGE_EXTENSIONS


def entity_special_folder_paths(
    project_root: Path | None,
    entity: Asset | Shot,
    *,
    dept_registry: object | None = None,
) -> dict[EntitySpecialFolderId, Path | None]:
    return {
        fid: entity_special_folder_path(project_root, entity, fid, dept_registry=dept_registry)
        for fid in ENTITY_SPECIAL_FOLDER_IDS
    }


def entity_has_reference_files(
    project_root: Path | None,
    entity: Asset | Shot,
    *,
    dept_registry: object | None = None,
) -> bool:
    """True when top-level ``reference/`` has at least one file."""
    path = entity_special_folder_path(project_root, entity, "reference", dept_registry=dept_registry)
    if path is None:
        return False
    return count_special_folder_files(path) > 0
