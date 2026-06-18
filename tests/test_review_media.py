"""Tests for review media resolver."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.review_media import ReviewResolveAction, resolve_entity_review_media
from monostudio.ui_qt.video_preview_context import PreviewContext, ReviewMediaKind


def test_resolve_entity_sequence_when_frames_present(tmp_path: Path) -> None:
    seq_dir = tmp_path / "render" / "shot_v001"
    seq_dir.mkdir(parents=True)
    f0 = seq_dir / "shot.1001.png"
    f0.write_bytes(b"x")
    frames = [f0]
    result = resolve_entity_review_media(
        thumb_path=None,
        work_path=tmp_path,
        work_file_path=None,
        sequence_frames=frames,
        sequence_folder=seq_dir,
        fps=24,
        context=PreviewContext.entity,
    )
    assert result.action == ReviewResolveAction.open_player
    assert result.request is not None
    assert result.request.media_kind == ReviewMediaKind.sequence
    assert result.request.sequence_folder == seq_dir
    assert result.request.frames == frames


def test_list_entity_review_sources_sequence_has_no_eager_frames(tmp_path: Path) -> None:
    from monostudio.core.review_media import list_entity_review_sources
    from monostudio.ui_qt.video_preview_context import PreviewContext, ReviewMediaKind

    work = tmp_path / "work"
    render = work / "render" / "shot_v001"
    render.mkdir(parents=True)
    (render / "shot.1001.png").write_bytes(b"x")

    sources = list_entity_review_sources(
        work_path=work,
        work_file_path=None,
        fps=24,
        context=PreviewContext.entity,
    )
    seq = next(s for s in sources if s.request.media_kind == ReviewMediaKind.sequence)
    assert seq.request.frames is None
    assert seq.request.sequence_folder == render


def test_list_entity_review_sources(tmp_path: Path) -> None:
    from monostudio.core.review_media import list_entity_review_sources
    from monostudio.ui_qt.video_preview_context import PreviewContext, ReviewMediaKind

    work = tmp_path / "work"
    render = work / "render" / "shot_v001"
    render.mkdir(parents=True)
    (render / "shot.1001.png").write_bytes(b"x")
    playblast = work / "playblast"
    playblast.mkdir(parents=True)
    vid = playblast / "blast.mp4"
    vid.write_bytes(b"fake")

    sources = list_entity_review_sources(
        work_path=work,
        work_file_path=None,
        fps=24,
        context=PreviewContext.entity,
    )
    kinds = {s.request.media_kind for s in sources}
    assert ReviewMediaKind.video in kinds
    assert ReviewMediaKind.sequence in kinds
    assert len(sources) >= 2


def test_sequence_markers_sidecar_path(tmp_path: Path) -> None:
    from monostudio.core.video_media import (
        load_sequence_markers_sidecar,
        save_sequence_markers_sidecar,
        VideoReviewMarker,
    )

    folder = tmp_path / "seq"
    folder.mkdir()
    save_sequence_markers_sidecar(folder, [VideoReviewMarker("m1", 5, "note", 0.0)])
    loaded = load_sequence_markers_sidecar(folder, total_frames=10)
    assert len(loaded) == 1
    assert loaded[0].frame == 5
