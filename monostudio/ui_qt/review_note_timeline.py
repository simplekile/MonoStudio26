"""Timeline note markers — frame anchors + author avatars."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtGui import QColor, QPixmap

from monostudio.core.item_comments import ItemCommentEntry, entry_author_visual, entry_preview_text
from monostudio.core.note_time_anchors import parse_time_href_from_html
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio

_FRAME_RANGE_RE = re.compile(r"\[(\d{4})\s*[–-]\s*(\d{4})")
_FRAME_TAG_RE = re.compile(r"\[F?(\d{4})\b")


def parse_note_anchor_frame(text: str, *, body_html: str = "") -> int | None:
    """Extract primary timeline frame from a note prefix, time pill, or range chip."""
    html = (body_html or "").strip()
    if html:
        anchor = parse_time_href_from_html(html)
        if anchor is not None:
            return anchor.frame
    for src in ((text or "").strip(), html):
        if not src:
            continue
        m = _FRAME_RANGE_RE.search(src)
        if m:
            return int(m.group(1))
        m = _FRAME_TAG_RE.search(src)
        if m:
            return int(m.group(1))
    return None


@dataclass(frozen=True)
class ReviewTimelineNoteMarker:
    note_id: str
    frame: int
    pixmap: QPixmap
    ring_color: QColor
    done: bool
    tooltip: str


def build_timeline_note_markers(
    entries: list[ItemCommentEntry],
    workspace_root,
    *,
    widget_for_dpr=None,
    avatar_px: int = 18,
    max_frame: int | None = None,
) -> list[ReviewTimelineNoteMarker]:
    dpr = effective_device_pixel_ratio(widget_for_dpr)
    out: list[ReviewTimelineNoteMarker] = []
    for entry in entries:
        frame = parse_note_anchor_frame(entry.text, body_html=entry.body_html)
        if frame is None:
            continue
        if max_frame is not None and frame > max_frame:
            continue
        visual = entry_author_visual(entry, workspace_root)
        ring = QColor(visual.color_hex if QColor(visual.color_hex).isValid() else "#3b82f6")
        preview = entry_preview_text(entry, max_chars=80)
        tooltip = f"{visual.name}\n{frame:04d}"
        if preview:
            tooltip = f"{visual.name}\n{frame:04d}\n{preview}"
        out.append(
            ReviewTimelineNoteMarker(
                note_id=entry.id,
                frame=int(frame),
                pixmap=avatar_pixmap_for(
                    visual.image_path,
                    visual.initials,
                    visual.color_hex,
                    avatar_px,
                    dpr=dpr,
                ),
                ring_color=ring,
                done=bool(entry.done),
                tooltip=tooltip,
            )
        )
    out.sort(key=lambda m: (m.frame, m.note_id))
    return out
