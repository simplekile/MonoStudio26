from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ViewItemKind(str, Enum):
    PROJECT = "project"
    ASSET = "asset"
    SHOT = "shot"
    DEPARTMENT = "department"
    INBOX_ITEM = "inbox_item"
    INBOX_SECTION = "inbox_section"  # section title when showing both Client and Freelancer


@dataclass(frozen=True)
class ViewItem:
    kind: ViewItemKind
    name: str
    type_badge: str
    path: Path
    departments_count: int | None = None
    ref: object | None = None
    # Folder name for this type (e.g. "char") for stripping prefix from name; empty if N/A.
    type_folder: str = ""
    # User-set status (ready|progress|waiting|blocked); None = use computed status.
    user_status: str | None = None


def display_name_for_item(item: ViewItem) -> str:
    """
    Name to show in UI: strip type prefix when present (e.g. char_Aya → Aya).
    Uses type_folder (e.g. "char") when set, else type_badge, to build prefix.
    Fallback: if name is "prefix_suffix" and prefix appears in type_badge (e.g. "char" in "_characters"), strip it.
    Full name remains in item.name for ID/path.
    """
    name = (item.name or "").strip()
    if not name:
        return name
    type_for_prefix = (item.type_folder or item.type_badge or "").strip()
    if type_for_prefix:
        prefix = type_for_prefix.lower() + "_"
        if name.lower().startswith(prefix):
            return name[len(prefix) :]
    # Fallback: name like "char_Aya" and type_badge "_characters" — first segment "char" matches type
    if "_" in name and (item.type_badge or "").strip():
        first_segment = name.split("_", 1)[0].lower()
        if first_segment and (item.type_badge or "").lower().find(first_segment) >= 0:
            return name.split("_", 1)[1]
    return name

