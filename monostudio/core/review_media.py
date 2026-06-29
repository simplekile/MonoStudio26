"""Resolve entity review media (video file or image sequence) for the unified review player."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from monostudio.core.sequence_preview import (
    _sequence_roots_by_priority,
    list_sequence_frames,
    resolve_sequence_folder,
    sequence_folder_has_frames,
    work_file_folder_name_candidates,
)
from monostudio.core.video_media import is_video_path, list_video_siblings, resolve_work_preview_video

_ROOT_DISPLAY = {
    "render": "Render",
    "preview": "Preview",
    "playblast": "Playblast",
    "flipbook": "Flipbook",
}


class ReviewResolveAction(StrEnum):
    open_player = "open_player"
    open_external = "open_external"


@dataclass(frozen=True)
class ReviewResolveResult:
    action: ReviewResolveAction
    request: object | None = None  # ReviewOpenRequest when action == open_player
    external_path: Path | None = None


@dataclass(frozen=True)
class EntityReviewSource:
    label: str
    request: object  # ReviewOpenRequest


def _mtime_ns(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def _root_display_name(root: Path) -> str:
    return _ROOT_DISPLAY.get(root.name.casefold(), root.name)


def _collect_videos_in_root(root: Path, names: tuple[str, ...]) -> list[Path]:
    hits: list[Path] = []

    def consider_file(p: Path) -> None:
        if is_video_path(p):
            hits.append(p)

    try:
        for p in root.iterdir():
            if p.is_file():
                consider_file(p)
                continue
            if not p.is_dir():
                continue
            if names and p.name.casefold() not in {n.casefold() for n in names}:
                continue
            try:
                for child in p.iterdir():
                    if child.is_file():
                        consider_file(child)
            except OSError:
                pass
    except OSError:
        return []
    for n in names:
        for ext in (".mp4", ".mov", ".mkv", ".webm"):
            consider_file(root / f"{n}{ext}")
    hits.sort(key=_mtime_ns, reverse=True)
    return hits


def _collect_sequence_dirs_in_root(root: Path, names: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    name_cf = {n.casefold() for n in names} if names else None

    def add_folder(p: Path) -> None:
        try:
            key = str(p.resolve()).casefold()
        except OSError:
            key = str(p).casefold()
        if key in seen:
            return
        if not sequence_folder_has_frames(p):
            return
        seen.add(key)
        out.append(p)

    try:
        children = list(root.iterdir())
    except OSError:
        return []
    if name_cf:
        for c in children:
            if c.is_dir() and c.name.casefold() in name_cf:
                add_folder(c)
    else:
        for c in children:
            if c.is_dir():
                add_folder(c)
    out.sort(key=_mtime_ns, reverse=True)
    return out


def list_entity_review_sources(
    *,
    work_path: Path | None,
    work_file_path: Path | None,
    fps: int,
    context: object,
    entity_path: Path | None = None,
    department_id: str | None = None,
    department_label: str | None = None,
) -> list[EntityReviewSource]:
    """All reviewable videos and image sequences under department work/."""
    from monostudio.ui_qt.video_preview_context import (
        PreviewContext,
        ReviewMediaKind,
        ReviewOpenRequest,
    )

    if work_path is None or not work_path.is_dir():
        return []

    ctx = context if isinstance(context, PreviewContext) else PreviewContext.entity
    names = work_file_folder_name_candidates(work_file_path)
    sources: list[EntityReviewSource] = []
    seen_keys: set[str] = set()

    def add(req: ReviewOpenRequest, label: str) -> None:
        key = str(req.media_key).casefold()
        if key in seen_keys:
            return
        seen_keys.add(key)
        sources.append(EntityReviewSource(label=label, request=req))

    for root in _sequence_roots_by_priority(work_path):
        rd = _root_display_name(root)
        for vid in _collect_videos_in_root(root, names):
            label = f"{rd} · {vid.name}"
            sibs = list_video_siblings(vid)
            add(
                ReviewOpenRequest(
                    media_kind=ReviewMediaKind.video,
                    path=vid,
                    context=ctx,
                    sibling_paths=sibs,
                    entity_path=entity_path,
                    department_id=department_id,
                    department_label=department_label,
                    work_path=work_path,
                    work_file_path=work_file_path,
                    source_label=label,
                ),
                label,
            )
        for folder in _collect_sequence_dirs_in_root(root, names):
            if not sequence_folder_has_frames(folder):
                continue
            label = f"{rd} · {folder.name}"
            add(
                ReviewOpenRequest(
                    media_kind=ReviewMediaKind.sequence,
                    context=ctx,
                    frames=None,
                    sequence_folder=folder,
                    fps=max(1, min(60, int(fps))),
                    entity_path=entity_path,
                    department_id=department_id,
                    department_label=department_label,
                    work_path=work_path,
                    work_file_path=work_file_path,
                    source_label=label,
                ),
                label,
            )
    return sources


def _video_source_label_for_path(work_path: Path, path: Path) -> str:
    for root in _sequence_roots_by_priority(work_path):
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return f"{_root_display_name(root)} · {path.name}"
    return path.name


def _sequence_source_label_for_folder(
    work_path: Path,
    folder: Path,
    frame_count: int | None = None,
) -> str:
    for root in _sequence_roots_by_priority(work_path):
        try:
            folder.relative_to(root)
        except ValueError:
            continue
        base = f"{_root_display_name(root)} · {folder.name}"
        if frame_count is not None and frame_count > 0:
            return f"{base} · {frame_count} fr"
        return base
    base = folder.name
    if frame_count is not None and frame_count > 0:
        return f"{base} · {frame_count} fr"
    return base


def resolve_entity_review_media(
    *,
    thumb_path: Path | None,
    work_path: Path | None,
    work_file_path: Path | None,
    sequence_frames: list[Path] | None,
    sequence_folder: Path | None,
    fps: int,
    context: object,
    entity_path: Path | None = None,
    department_id: str | None = None,
    department_label: str | None = None,
    sibling_paths: list[Path] | None = None,
) -> ReviewResolveResult:
    """
    Pick review media for Inspector double-click / context menu.

    Priority: thumb video → playblast MP4 → image sequence → external fallback.
    """
    from monostudio.ui_qt.video_preview_context import (
        PreviewContext,
        ReviewMediaKind,
        ReviewOpenRequest,
    )
    from monostudio.ui_qt.thumbnails import is_video_preview_path

    ctx = context if isinstance(context, PreviewContext) else PreviewContext.entity
    common = {
        "context": ctx,
        "entity_path": entity_path,
        "department_id": department_id,
        "department_label": department_label,
        "work_path": work_path,
        "work_file_path": work_file_path,
    }

    if thumb_path is not None and is_video_preview_path(thumb_path):
        src = thumb_path.name
        if work_path is not None:
            src = _video_source_label_for_path(work_path, thumb_path)
        return ReviewResolveResult(
            action=ReviewResolveAction.open_player,
            request=ReviewOpenRequest(
                media_kind=ReviewMediaKind.video,
                path=thumb_path,
                sibling_paths=sibling_paths,
                source_label=src,
                **common,
            ),
        )

    if work_path is not None:
        blast = resolve_work_preview_video(work_path, work_file_path)
        if blast is not None:
            sibs = sibling_paths if sibling_paths is not None else list_video_siblings(blast)
            return ReviewResolveResult(
                action=ReviewResolveAction.open_player,
                request=ReviewOpenRequest(
                    media_kind=ReviewMediaKind.video,
                    path=blast,
                    sibling_paths=sibs,
                    source_label=_video_source_label_for_path(work_path, blast),
                    **common,
                ),
            )

    frames = list(sequence_frames) if sequence_frames else None
    folder = sequence_folder
    if folder is None and work_path is not None:
        folder = resolve_sequence_folder(work_path, work_file_path)

    if folder is not None:
        if not frames:
            if not sequence_folder_has_frames(folder):
                folder = None
        else:
            n = len(frames)

    if folder is not None:
        src = (
            _sequence_source_label_for_folder(
                work_path,
                folder,
                n if frames else None,
            )
            if work_path is not None
            else (
                f"{folder.name} · {n} fr"
                if frames and n > 0
                else folder.name
            )
        )
        return ReviewResolveResult(
            action=ReviewResolveAction.open_player,
            request=ReviewOpenRequest(
                media_kind=ReviewMediaKind.sequence,
                frames=frames,
                sequence_folder=folder,
                fps=max(1, min(60, int(fps))),
                source_label=src,
                **common,
            ),
        )

    if thumb_path is not None and thumb_path.is_file():
        if is_video_path(thumb_path):
            return ReviewResolveResult(
                action=ReviewResolveAction.open_player,
                request=ReviewOpenRequest(
                    media_kind=ReviewMediaKind.video,
                    path=thumb_path,
                    sibling_paths=sibling_paths,
                    source_label=thumb_path.name,
                    **common,
                ),
            )
        return ReviewResolveResult(
            action=ReviewResolveAction.open_external,
            external_path=thumb_path,
        )

    return ReviewResolveResult(action=ReviewResolveAction.open_external)
