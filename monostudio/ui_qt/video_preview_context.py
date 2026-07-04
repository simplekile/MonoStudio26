"""Context for opening the unified review player from different app surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PreviewContext(StrEnum):
    inbox = "inbox"
    project_guide = "project_guide"
    entity = "entity"
    entity_ref = "entity_ref"  # Inspector Ref tab — reference/concept browse, no draw
    internal_check = "internal_check"
    delivery = "delivery"


class ReviewMediaKind(StrEnum):
    video = "video"
    sequence = "sequence"


@dataclass(frozen=True)
class ReviewOpenRequest:
    media_kind: ReviewMediaKind
    context: PreviewContext
  # video
    path: Path | None = None
    sibling_paths: list[Path] | None = None
  # sequence
    frames: list[Path] | None = None
    sequence_folder: Path | None = None
    fps: int = 24
  # entity (shots/assets)
    entity_path: Path | None = None
    department_id: str | None = None
    department_label: str | None = None
    work_path: Path | None = None
    work_file_path: Path | None = None
    source_label: str | None = None

    def __post_init__(self) -> None:
        if self.media_kind == ReviewMediaKind.video:
            if self.path is None:
                raise ValueError("ReviewOpenRequest video requires path")
        elif self.media_kind == ReviewMediaKind.sequence:
            if self.sequence_folder is None:
                raise ValueError("ReviewOpenRequest sequence requires sequence_folder")

    @property
    def geometry_fraction(self) -> float:
        """Fallback fraction of main-window area when media size is unknown."""
        if self.context == PreviewContext.entity:
            return 0.95
        return 0.92

    @property
    def settings_profile_key(self) -> str:
        return self.context.value

    @property
    def media_key(self) -> Path:
        """Sidecar / session key: video path or sequence folder."""
        if self.media_kind == ReviewMediaKind.video:
            assert self.path is not None
            return self.path
        assert self.sequence_folder is not None
        return self.sequence_folder


@dataclass(frozen=True)
class VideoPreviewOpenRequest:
    """Backward-compatible video-only open request."""

    path: Path
    context: PreviewContext
    sibling_paths: list[Path] | None = None
    entity_path: Path | None = None
    department_id: str | None = None
    department_label: str | None = None
    work_path: Path | None = None
    work_file_path: Path | None = None
    source_label: str | None = None

    @property
    def geometry_fraction(self) -> float:
        if self.context == PreviewContext.entity:
            return 0.95
        return 0.92

    @property
    def settings_profile_key(self) -> str:
        return self.context.value

    def to_review_request(self) -> ReviewOpenRequest:
        return ReviewOpenRequest(
            media_kind=ReviewMediaKind.video,
            path=self.path,
            context=self.context,
            sibling_paths=self.sibling_paths,
            entity_path=self.entity_path,
            department_id=self.department_id,
            department_label=self.department_label,
            work_path=self.work_path,
            work_file_path=self.work_file_path,
            source_label=self.source_label,
        )
