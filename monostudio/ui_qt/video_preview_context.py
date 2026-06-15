"""Context for opening the video preview dialog from different app surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PreviewContext(StrEnum):
    inbox = "inbox"
    project_guide = "project_guide"
    entity = "entity"


@dataclass(frozen=True)
class VideoPreviewOpenRequest:
    path: Path
    context: PreviewContext
    sibling_paths: list[Path] | None = None
    entity_path: Path | None = None
    department_id: str | None = None

    @property
    def geometry_fraction(self) -> float:
        """Share of main-window client area used when the dialog opens (then size is locked)."""
        if self.context == PreviewContext.entity:
            return 0.95
        return 0.92

    @property
    def settings_profile_key(self) -> str:
        return self.context.value


@dataclass(frozen=True)
class SequencePreviewOpenRequest:
    frames: list[Path]
    sequence_folder: Path
    fps: int
    entity_path: Path | None = None
