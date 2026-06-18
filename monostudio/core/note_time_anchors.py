"""Structured timeline anchors embedded in note HTML (clickable pills)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from monostudio.core.video_media import VideoFrameRange, VideoReviewMarker, format_frame_label, format_range_span_display

TIME_HREF_PREFIX = "monos-time:"
_TIME_HREF_RE = re.compile(
    r'monos-time:(range|marker|frame|playhead)/([^"/\s]+)(?:/(\d+))?',
    re.IGNORECASE,
)

NoteTimeKind = Literal["range", "marker", "frame", "playhead"]

PLAYHEAD_ANCHOR_ID = "playhead"


@dataclass(frozen=True)
class NoteTimeAnchor:
    kind: NoteTimeKind
    frame: int
    ref_id: str = ""
    label: str = ""


def is_time_note_href(href: str) -> bool:
    return (href or "").strip().lower().startswith(TIME_HREF_PREFIX)


def time_href_for_range(range_id: str, in_frame: int) -> str:
    rid = (range_id or "").strip()
    return f"{TIME_HREF_PREFIX}range/{rid}/{max(0, int(in_frame))}"


def time_href_for_marker(marker_id: str, frame: int) -> str:
    mid = (marker_id or "").strip()
    return f"{TIME_HREF_PREFIX}marker/{mid}/{max(0, int(frame))}"


def time_href_for_frame(frame: int) -> str:
    return f"{TIME_HREF_PREFIX}frame/{max(0, int(frame))}"


def time_href_for_playhead(frame: int) -> str:
    return f"{TIME_HREF_PREFIX}playhead/{max(0, int(frame))}"


def is_playhead_time_href(href: str) -> bool:
    h = (href or "").strip().lower()
    return h.startswith(f"{TIME_HREF_PREFIX}playhead/")


def parse_time_href(href: str) -> NoteTimeAnchor | None:
    h = (href or "").strip()
    if not is_time_note_href(h):
        return None
    rest = h[len(TIME_HREF_PREFIX) :]
    parts = [p for p in rest.split("/") if p != ""]
    if not parts:
        return None
    kind = parts[0].lower()
    if kind == "playhead" and len(parts) >= 2:
        try:
            frame = int(parts[1])
        except ValueError:
            return None
        return NoteTimeAnchor(kind="playhead", frame=frame, ref_id=PLAYHEAD_ANCHOR_ID)
    if kind == "frame" and len(parts) >= 2:
        try:
            frame = int(parts[1])
        except ValueError:
            return None
        return NoteTimeAnchor(kind="frame", frame=frame)
    if kind in ("range", "marker") and len(parts) >= 3:
        try:
            frame = int(parts[2])
        except ValueError:
            return None
        return NoteTimeAnchor(kind=kind, ref_id=parts[1], frame=frame)  # type: ignore[arg-type]
    return None


def parse_time_href_from_html(html: str) -> NoteTimeAnchor | None:
    for m in _TIME_HREF_RE.finditer(html or ""):
        href = m.group(0)
        anchor = parse_time_href(href)
        if anchor is not None:
            return anchor
    return None


def format_range_pill_label(rng: VideoFrameRange, fps: float) -> str:
    span = format_range_span_display(rng, fps, mode="frame")
    label = (rng.label or "").strip()
    if label:
        return f"{span} · {label}"
    return span


def format_marker_pill_label(marker: VideoReviewMarker, fps: float) -> str:
    frame_lbl = format_frame_label(marker.frame)
    label = (marker.label or "").strip()
    if label:
        return f"F{frame_lbl} · {label}"
    return f"F{frame_lbl}"


def format_frame_pill_label(frame: int, fps: float) -> str:
    from monostudio.core.video_media import format_timecode

    frame_lbl = format_frame_label(frame)
    tc = format_timecode(frame / max(1e-6, fps), fps=fps)
    return f"F{frame_lbl} · {tc}"
