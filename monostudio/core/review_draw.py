"""Review draw — stacked layers, each with per-frame keyframes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

logger = logging.getLogger(__name__)

DrawTool = Literal["pen", "arrow", "rect", "eraser"]

REVIEW_DRAW_COLORS = ("#ef4444", "#eab308", "#22c55e", "#3b82f6", "#fafafa")


@dataclass
class ReviewDrawStroke:
    tool: DrawTool
    color: str
    width_px: float
    points: list[tuple[float, float]]

    def to_json(self) -> dict:
        return {
            "tool": self.tool,
            "color": self.color,
            "width_px": float(self.width_px),
            "points": [[float(x), float(y)] for x, y in self.points],
        }

    @classmethod
    def from_json(cls, data: object) -> ReviewDrawStroke | None:
        if not isinstance(data, dict):
            return None
        tool = data.get("tool")
        if tool not in ("pen", "arrow", "rect", "eraser"):
            return None
        color = str(data.get("color") or "#ef4444").strip() or "#ef4444"
        try:
            width_px = float(data.get("width_px") or 3.0)
        except (TypeError, ValueError):
            width_px = 3.0
        raw_pts = data.get("points")
        if not isinstance(raw_pts, list):
            return None
        points: list[tuple[float, float]] = []
        for pt in raw_pts:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            try:
                points.append((float(pt[0]), float(pt[1])))
            except (TypeError, ValueError):
                continue
        if not points:
            return None
        return cls(tool=tool, color=color, width_px=width_px, points=points)


@dataclass
class ReviewDrawLayerKeyframe:
    frame: int
    hold_frames: int = 1
    strokes: list[ReviewDrawStroke] = field(default_factory=list)
    visible: bool = True
    created_at: str = ""
    author: str | None = None

    def to_json(self) -> dict:
        return {
            "frame": int(self.frame),
            "hold_frames": max(1, int(self.hold_frames)),
            "strokes": [s.to_json() for s in self.strokes],
            "visible": bool(self.visible),
            "created_at": self.created_at or "",
            "author": self.author,
        }

    @classmethod
    def from_json(cls, data: object) -> ReviewDrawLayerKeyframe | None:
        if not isinstance(data, dict):
            return None
        try:
            frame = int(data.get("frame", 0))
        except (TypeError, ValueError):
            return None
        try:
            hold_frames = max(1, int(data.get("hold_frames", 1)))
        except (TypeError, ValueError):
            hold_frames = 1
        strokes: list[ReviewDrawStroke] = []
        raw = data.get("strokes")
        if isinstance(raw, list):
            for item in raw:
                stroke = ReviewDrawStroke.from_json(item)
                if stroke is not None:
                    strokes.append(stroke)
        return cls(
            frame=frame,
            hold_frames=hold_frames,
            strokes=strokes,
            visible=bool(data.get("visible", True)),
            created_at=str(data.get("created_at") or ""),
            author=(str(data["author"]).strip() or None) if data.get("author") else None,
        )


@dataclass
class ReviewDrawLayer:
    id: str
    name: str = ""
    visible: bool = True
    default_hold_frames: int = 1
    keyframes: list[ReviewDrawLayerKeyframe] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or "",
            "visible": bool(self.visible),
            "default_hold_frames": max(1, int(self.default_hold_frames)),
            "keyframes": [kf.to_json() for kf in self.keyframes],
        }

    @classmethod
    def from_json(cls, data: object) -> ReviewDrawLayer | None:
        if not isinstance(data, dict):
            return None
        lid = str(data.get("id") or "").strip()
        if not lid:
            return None
        try:
            default_hold_frames = max(1, int(data.get("default_hold_frames", 1)))
        except (TypeError, ValueError):
            default_hold_frames = 1
        keyframes: list[ReviewDrawLayerKeyframe] = []
        raw = data.get("keyframes")
        if isinstance(raw, list):
            for item in raw:
                kf = ReviewDrawLayerKeyframe.from_json(item)
                if kf is not None:
                    keyframes.append(kf)
        return cls(
            id=lid,
            name=str(data.get("name") or "").strip()[:40],
            visible=bool(data.get("visible", True)),
            default_hold_frames=default_hold_frames,
            keyframes=keyframes,
        )


# Legacy v2 — keyframe owns layers (migrated to v3 on load).
@dataclass
class _ReviewDrawV2Layer:
    id: str
    name: str = ""
    visible: bool = True
    strokes: list[ReviewDrawStroke] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: object) -> _ReviewDrawV2Layer | None:
        if not isinstance(data, dict):
            return None
        lid = str(data.get("id") or "").strip()
        if not lid:
            return None
        strokes: list[ReviewDrawStroke] = []
        raw = data.get("strokes")
        if isinstance(raw, list):
            for item in raw:
                stroke = ReviewDrawStroke.from_json(item)
                if stroke is not None:
                    strokes.append(stroke)
        return cls(
            id=lid,
            name=str(data.get("name") or "").strip()[:40],
            visible=bool(data.get("visible", True)),
            strokes=strokes,
        )


@dataclass
class ReviewDrawKeyframe:
    """Legacy v2 shape — do not use for new data."""

    frame: int
    layers: list[_ReviewDrawV2Layer] = field(default_factory=list)
    hold_frames: int = 1
    created_at: str = ""
    author: str | None = None

    @classmethod
    def from_json(cls, data: object) -> ReviewDrawKeyframe | None:
        if not isinstance(data, dict):
            return None
        try:
            frame = int(data.get("frame", 0))
        except (TypeError, ValueError):
            return None
        try:
            hold_frames = max(1, int(data.get("hold_frames", 1)))
        except (TypeError, ValueError):
            hold_frames = 1
        layers: list[_ReviewDrawV2Layer] = []
        raw = data.get("layers")
        if isinstance(raw, list):
            for item in raw:
                layer = _ReviewDrawV2Layer.from_json(item)
                if layer is not None:
                    layers.append(layer)
        return cls(
            frame=frame,
            hold_frames=hold_frames,
            layers=layers,
            created_at=str(data.get("created_at") or ""),
            author=(str(data["author"]).strip() or None) if data.get("author") else None,
        )


# Legacy v1 clip — migrated on load only.
@dataclass
class ReviewDrawClip:
    id: str
    start_frame: int
    end_frame: int
    label: str = ""
    strokes: list[ReviewDrawStroke] = field(default_factory=list)
    created_at: str = ""
    author: str | None = None

    @classmethod
    def from_json(cls, data: object) -> ReviewDrawClip | None:
        if not isinstance(data, dict):
            return None
        cid = str(data.get("id") or "").strip()
        if not cid:
            return None
        try:
            start = int(data.get("start_frame", 0))
            end = int(data.get("end_frame", start))
        except (TypeError, ValueError):
            return None
        strokes: list[ReviewDrawStroke] = []
        raw = data.get("strokes")
        if isinstance(raw, list):
            for item in raw:
                stroke = ReviewDrawStroke.from_json(item)
                if stroke is not None:
                    strokes.append(stroke)
        return cls(
            id=cid,
            start_frame=start,
            end_frame=end,
            label=str(data.get("label") or "").strip()[:80],
            strokes=strokes,
            created_at=str(data.get("created_at") or ""),
            author=(str(data["author"]).strip() or None) if data.get("author") else None,
        )


@dataclass(frozen=True)
class DrawTimelineMarker:
    layer_id: str
    keyframe: ReviewDrawLayerKeyframe


def new_draw_layer_id() -> str:
    return uuid.uuid4().hex[:8]


def make_draw_layer(
    *,
    name: str = "",
    keyframes: Sequence[ReviewDrawLayerKeyframe] | None = None,
) -> ReviewDrawLayer:
    kfs = list(keyframes or ())
    label = (name or "").strip() or f"Layer {len(kfs) + 1}"
    return ReviewDrawLayer(
        id=new_draw_layer_id(),
        name=label[:40],
        visible=True,
        keyframes=kfs,
    )


def make_layer_keyframe(
    frame: int,
    *,
    strokes: Sequence[ReviewDrawStroke] | None = None,
    hold_frames: int = 1,
) -> ReviewDrawLayerKeyframe:
    return ReviewDrawLayerKeyframe(
        frame=max(0, int(frame)),
        hold_frames=max(1, int(hold_frames)),
        strokes=list(strokes or ()),
        created_at=str(time.time()),
        author=None,
    )


def hold_frames_for_keyframe(kf: ReviewDrawLayerKeyframe) -> int:
    return max(1, int(kf.hold_frames))


def default_hold_frames_for_layer(layer: ReviewDrawLayer) -> int:
    return max(1, int(layer.default_hold_frames))


def set_layer_default_hold(layer: ReviewDrawLayer, hold_frames: int) -> None:
    layer.default_hold_frames = max(1, min(9999, int(hold_frames)))


def keyframe_hold_end(
    kf: ReviewDrawLayerKeyframe,
    layer_keyframes: Sequence[ReviewDrawLayerKeyframe],
    *,
    total_frames: int = 0,
) -> int:
    """Last frame (inclusive) this key holds on its layer, capped before the next key."""
    start = int(kf.frame)
    end = start + hold_frames_for_keyframe(kf) - 1
    for other in layer_keyframes:
        other_frame = int(other.frame)
        if other_frame > start:
            end = min(end, other_frame - 1)
            break
    if total_frames > 0:
        end = min(end, max(0, total_frames - 1))
    return max(start, end)


def keyframe_at_exact_on_layer(layer: ReviewDrawLayer, frame: int) -> ReviewDrawLayerKeyframe | None:
    f = int(frame)
    return next((kf for kf in layer.keyframes if int(kf.frame) == f), None)


def holding_keyframe_on_layer(layer: ReviewDrawLayer, frame: int) -> ReviewDrawLayerKeyframe | None:
    """Latest keyframe at or before playhead on this layer."""
    f = int(frame)
    candidates = [kf for kf in layer.keyframes if int(kf.frame) <= f]
    if not candidates:
        return None
    return max(candidates, key=lambda kf: int(kf.frame))


def layer_keyframe_has_visible_strokes(kf: ReviewDrawLayerKeyframe | None) -> bool:
    if kf is None or not kf.visible:
        return False
    return bool(kf.strokes)


def display_keyframe_on_layer(
    layer: ReviewDrawLayer,
    frame: int,
    *,
    total_frames: int = 0,
) -> ReviewDrawLayerKeyframe | None:
    """Layer keyframe whose anchor or hold range covers playhead."""
    if not layer.visible:
        return None
    exact = keyframe_at_exact_on_layer(layer, frame)
    if exact is not None and layer_keyframe_has_visible_strokes(exact):
        return exact
    f = int(frame)
    best: ReviewDrawLayerKeyframe | None = None
    best_start = -1
    for kf in layer.keyframes:
        if not layer_keyframe_has_visible_strokes(kf):
            continue
        start = int(kf.frame)
        end = keyframe_hold_end(kf, layer.keyframes, total_frames=total_frames)
        if start <= f <= end and start > best_start:
            best = kf
            best_start = start
    return best


def strokes_for_display(kf: ReviewDrawLayerKeyframe | None) -> list[ReviewDrawStroke]:
    if kf is None:
        return []
    return [stroke for stroke in kf.strokes if stroke.tool != "eraser"]


def composite_strokes_at(
    layers: Sequence[ReviewDrawLayer],
    frame: int,
    *,
    total_frames: int = 0,
) -> list[ReviewDrawStroke]:
    out: list[ReviewDrawStroke] = []
    for layer in layers:
        lk = display_keyframe_on_layer(layer, frame, total_frames=total_frames)
        out.extend(strokes_for_display(lk))
    return out


def draw_visible_at(
    layers: Sequence[ReviewDrawLayer],
    frame: int,
    *,
    total_frames: int = 0,
) -> bool:
    return bool(composite_strokes_at(layers, frame, total_frames=total_frames))


def move_keyframe_on_layer(
    layer: ReviewDrawLayer,
    old_frame: int,
    new_frame: int,
) -> bool:
    kf = keyframe_at_exact_on_layer(layer, old_frame)
    if kf is None:
        return False
    new_f = max(0, int(new_frame))
    old_f = int(kf.frame)
    if new_f == old_f:
        return True
    if keyframe_at_exact_on_layer(layer, new_f) is not None:
        return False
    kf.frame = new_f
    layer.keyframes.sort(key=lambda item: int(item.frame))
    return True


def delete_keyframe_on_layer(layer: ReviewDrawLayer, frame: int) -> bool:
    before = len(layer.keyframes)
    layer.keyframes[:] = [kf for kf in layer.keyframes if int(kf.frame) != int(frame)]
    return len(layer.keyframes) < before


def delete_layer_from_document(layers: list[ReviewDrawLayer], layer_id: str) -> bool:
    before = len(layers)
    layers[:] = [layer for layer in layers if layer.id != layer_id]
    return len(layers) < before


def set_keyframe_hold(kf: ReviewDrawLayerKeyframe, hold_frames: int) -> None:
    kf.hold_frames = max(1, min(9999, int(hold_frames)))


def _point_dist(ax: float, ay: float, bx: float, by: float) -> float:
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _point_to_segment_dist(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    dx = bx - ax
    dy = by - ay
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return _point_dist(px, py, ax, ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx = ax + t * dx
    cy = ay + t * dy
    return _point_dist(px, py, cx, cy)


def _eraser_hit_tolerance(eraser_width_px: float, stroke_width_px: float) -> float:
    return max(0.004, (float(eraser_width_px) + float(stroke_width_px)) * 0.55 / 1080.0)


def _stroke_hit_by_eraser(
    stroke: ReviewDrawStroke,
    eraser_points: Sequence[tuple[float, float]],
    eraser_width_px: float,
) -> bool:
    if stroke.tool == "eraser" or not eraser_points or not stroke.points:
        return False
    tol = _eraser_hit_tolerance(eraser_width_px, stroke.width_px)
    pts = stroke.points
    if stroke.tool == "pen":
        segments = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    elif stroke.tool in ("arrow", "rect") and len(pts) >= 2:
        segments = [(pts[0], pts[-1])]
    else:
        segments = []
    for ex, ey in eraser_points:
        if stroke.tool == "rect" and len(pts) >= 2:
            x0, y0 = pts[0]
            x1, y1 = pts[-1]
            lo_x, hi_x = sorted((x0, x1))
            lo_y, hi_y = sorted((y0, y1))
            lo_x -= tol
            hi_x += tol
            lo_y -= tol
            hi_y += tol
            if lo_x <= ex <= hi_x and lo_y <= ey <= hi_y:
                return True
        for sp in pts:
            if _point_dist(ex, ey, sp[0], sp[1]) <= tol:
                return True
        for (a, b) in segments:
            if _point_to_segment_dist(ex, ey, a[0], a[1], b[0], b[1]) <= tol:
                return True
    return False


def apply_eraser_to_strokes(
    strokes: list[ReviewDrawStroke],
    eraser_points: Sequence[tuple[float, float]],
    eraser_width_px: float,
) -> list[ReviewDrawStroke]:
    if not eraser_points:
        return list(strokes)
    return [
        stroke
        for stroke in strokes
        if not _stroke_hit_by_eraser(stroke, eraser_points, eraser_width_px)
    ]


def onion_strokes_prev_on_layer(
    layer: ReviewDrawLayer,
    frame: int,
    *,
    span: int = 2,
) -> list[ReviewDrawStroke]:
    f = int(frame)
    best: ReviewDrawLayerKeyframe | None = None
    for kf in layer.keyframes:
        kf_f = int(kf.frame)
        if not kf.visible or kf_f >= f or not layer_keyframe_has_visible_strokes(kf):
            continue
        if f - kf_f > max(1, int(span)):
            continue
        if best is None or kf_f > int(best.frame):
            best = kf
    return strokes_for_display(best)


def onion_strokes_next_on_layer(
    layer: ReviewDrawLayer,
    frame: int,
    *,
    span: int = 2,
) -> list[ReviewDrawStroke]:
    f = int(frame)
    best: ReviewDrawLayerKeyframe | None = None
    for kf in layer.keyframes:
        kf_f = int(kf.frame)
        if not kf.visible or kf_f <= f or not layer_keyframe_has_visible_strokes(kf):
            continue
        if kf_f - f > max(1, int(span)):
            continue
        if best is None or kf_f < int(best.frame):
            best = kf
    return strokes_for_display(best)


def onion_strokes_prev(
    layers: Sequence[ReviewDrawLayer],
    frame: int,
    *,
    span: int = 2,
    active_layer_id: str | None = None,
) -> list[ReviewDrawStroke]:
    if active_layer_id:
        layer = next((item for item in layers if item.id == active_layer_id), None)
        if layer is not None:
            return onion_strokes_prev_on_layer(layer, frame, span=span)
    out: list[ReviewDrawStroke] = []
    for layer in layers:
        out.extend(onion_strokes_prev_on_layer(layer, frame, span=span))
    return out


def onion_strokes_next(
    layers: Sequence[ReviewDrawLayer],
    frame: int,
    *,
    span: int = 2,
    active_layer_id: str | None = None,
) -> list[ReviewDrawStroke]:
    if active_layer_id:
        layer = next((item for item in layers if item.id == active_layer_id), None)
        if layer is not None:
            return onion_strokes_next_on_layer(layer, frame, span=span)
    out: list[ReviewDrawStroke] = []
    for layer in layers:
        out.extend(onion_strokes_next_on_layer(layer, frame, span=span))
    return out


def onion_has_neighbors(
    layers: Sequence[ReviewDrawLayer],
    frame: int,
    *,
    span: int = 2,
    active_layer_id: str | None = None,
) -> bool:
    return bool(
        onion_strokes_prev(layers, frame, span=span, active_layer_id=active_layer_id)
        or onion_strokes_next(layers, frame, span=span, active_layer_id=active_layer_id)
    )


def ensure_layer_in_document(
    layers: list[ReviewDrawLayer],
    layer_id: str | None = None,
) -> ReviewDrawLayer:
    if layer_id:
        found = next((layer for layer in layers if layer.id == layer_id), None)
        if found is not None:
            return found
    if layers:
        return layers[0]
    layer = make_draw_layer(name="Layer 1")
    layers.append(layer)
    return layer


def ensure_keyframe_on_layer(
    layer: ReviewDrawLayer,
    frame: int,
) -> tuple[ReviewDrawLayerKeyframe, bool]:
    existing = keyframe_at_exact_on_layer(layer, frame)
    if existing is not None:
        return existing, False
    kf = make_layer_keyframe(frame, hold_frames=default_hold_frames_for_layer(layer))
    layer.keyframes.append(kf)
    layer.keyframes.sort(key=lambda item: int(item.frame))
    return kf, True


def timeline_markers(layers: Sequence[ReviewDrawLayer]) -> list[DrawTimelineMarker]:
    out: list[DrawTimelineMarker] = []
    for layer in layers:
        for kf in layer.keyframes:
            if kf.visible:
                out.append(DrawTimelineMarker(layer_id=layer.id, keyframe=kf))
    out.sort(key=lambda item: (int(item.keyframe.frame), item.layer_id))
    return out


def timeline_markers_for_layer(layer: ReviewDrawLayer | None) -> list[DrawTimelineMarker]:
    if layer is None:
        return []
    out = [DrawTimelineMarker(layer_id=layer.id, keyframe=kf) for kf in layer.keyframes]
    out.sort(key=lambda item: int(item.keyframe.frame))
    return out


def layers_content_equal(a: Sequence[ReviewDrawLayer], b: Sequence[ReviewDrawLayer]) -> bool:
    if len(a) != len(b):
        return False
    for la, lb in zip(sorted(a, key=lambda layer: layer.id), sorted(b, key=lambda layer: layer.id), strict=True):
        if la.id != lb.id or la.name != lb.name or la.visible != lb.visible:
            return False
        if default_hold_frames_for_layer(la) != default_hold_frames_for_layer(lb):
            return False
        if len(la.keyframes) != len(lb.keyframes):
            return False
        for ka, kb in zip(
            sorted(la.keyframes, key=lambda k: k.frame),
            sorted(lb.keyframes, key=lambda k: k.frame),
            strict=True,
        ):
            if int(ka.frame) != int(kb.frame):
                return False
            if ka.visible != kb.visible:
                return False
            if hold_frames_for_keyframe(ka) != hold_frames_for_keyframe(kb):
                return False
            if len(ka.strokes) != len(kb.strokes):
                return False
            for sa, sb in zip(ka.strokes, kb.strokes, strict=True):
                if sa.tool != sb.tool or sa.color != sb.color or sa.width_px != sb.width_px:
                    return False
                if len(sa.points) != len(sb.points):
                    return False
                for pa, pb in zip(sa.points, sb.points, strict=True):
                    if abs(pa[0] - pb[0]) > 1e-5 or abs(pa[1] - pb[1]) > 1e-5:
                        return False
    return True


def _dedupe_layer_keyframes(keyframes: Sequence[ReviewDrawLayerKeyframe]) -> list[ReviewDrawLayerKeyframe]:
    by_frame: dict[int, ReviewDrawLayerKeyframe] = {}
    for kf in keyframes:
        frame = int(kf.frame)
        if frame in by_frame:
            by_frame[frame].strokes.extend(kf.strokes)
            by_frame[frame].hold_frames = max(
                hold_frames_for_keyframe(by_frame[frame]),
                hold_frames_for_keyframe(kf),
            )
        else:
            by_frame[frame] = ReviewDrawLayerKeyframe(
                frame=frame,
                hold_frames=hold_frames_for_keyframe(kf),
                strokes=list(kf.strokes),
                visible=kf.visible,
                created_at=kf.created_at,
                author=kf.author,
            )
    return sorted(by_frame.values(), key=lambda item: int(item.frame))


def _migrate_v2_keyframes_to_layers(keyframes: Sequence[ReviewDrawKeyframe]) -> list[ReviewDrawLayer]:
    if not keyframes:
        return []
    max_layers = max((len(kf.layers) for kf in keyframes), default=0)
    if max_layers == 0:
        return []
    layers: list[ReviewDrawLayer] = []
    for idx in range(max_layers):
        name = f"Layer {idx + 1}"
        visible = True
        for kf in keyframes:
            if idx < len(kf.layers):
                old = kf.layers[idx]
                if old.name:
                    name = old.name
                visible = old.visible
                break
        layers.append(
            ReviewDrawLayer(
                id=new_draw_layer_id(),
                name=name[:40],
                visible=visible,
                keyframes=[],
            )
        )
    for kf in keyframes:
        hold = max(1, int(kf.hold_frames))
        for idx, old_layer in enumerate(kf.layers):
            if idx >= len(layers):
                break
            layers[idx].keyframes.append(
                ReviewDrawLayerKeyframe(
                    frame=int(kf.frame),
                    hold_frames=hold,
                    strokes=list(old_layer.strokes),
                    created_at=kf.created_at,
                    author=kf.author,
                )
            )
    for layer in layers:
        layer.keyframes = _dedupe_layer_keyframes(layer.keyframes)
    return layers


def migrate_clips_to_layers(clips: Sequence[ReviewDrawClip]) -> list[ReviewDrawLayer]:
    layer = make_draw_layer(name="Layer 1")
    for clip in clips:
        hold = max(1, int(clip.end_frame) - int(clip.start_frame) + 1)
        layer.keyframes.append(
            ReviewDrawLayerKeyframe(
                frame=int(clip.start_frame),
                hold_frames=hold,
                strokes=list(clip.strokes),
                created_at=clip.created_at or str(time.time()),
                author=clip.author,
            )
        )
    layer.keyframes = _dedupe_layer_keyframes(layer.keyframes)
    return [layer] if layer.keyframes else []


def _local_draft_base() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        return Path(localappdata) / "MonoStudio" / "cache" / "review_draw"
    from monostudio.core.app_paths import get_app_base_path

    return get_app_base_path() / "monostudio_data" / "cache" / "review_draw"


def _draft_digest_key(media_key: Path) -> str:
    try:
        key_src = str(media_key.resolve()).casefold()
    except OSError:
        key_src = str(media_key).casefold()
    return hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]


def video_draw_sidecar_path(video_path: Path) -> Path:
    return Path(f"{video_path}.monos.draw.json")


def sequence_draw_sidecar_path(sequence_folder: Path) -> Path:
    return sequence_folder / ".monos.draw.json"


def draw_local_draft_path(media_key: Path, *, sequence: bool) -> Path:
    sub = "seq" if sequence else "vid"
    return _local_draft_base() / sub / f"{_draft_digest_key(media_key)}.json"


def _clamp_layers_to_total(layers: list[ReviewDrawLayer], total_frames: int) -> list[ReviewDrawLayer]:
    if total_frames <= 0:
        return layers
    max_frame = total_frames - 1
    out: list[ReviewDrawLayer] = []
    for layer in layers:
        kfs: list[ReviewDrawLayerKeyframe] = []
        for kf in layer.keyframes:
            frame = min(max_frame, max(0, int(kf.frame)))
            kfs.append(
                ReviewDrawLayerKeyframe(
                    frame=frame,
                    hold_frames=hold_frames_for_keyframe(kf),
                    strokes=list(kf.strokes),
                    visible=kf.visible,
                    created_at=kf.created_at,
                    author=kf.author,
                )
            )
        out.append(
            ReviewDrawLayer(
                id=layer.id,
                name=layer.name,
                visible=layer.visible,
                default_hold_frames=default_hold_frames_for_layer(layer),
                keyframes=_dedupe_layer_keyframes(kfs),
            )
        )
    return out


def _draw_payload(media_key: Path, layers: Sequence[ReviewDrawLayer]) -> dict:
    try:
        source_path = str(media_key.resolve())
    except OSError:
        source_path = str(media_key)
    return {
        "version": 3,
        "source": media_key.name,
        "source_path": source_path,
        "layers": [layer.to_json() for layer in layers],
    }


def _parse_draw_file(path: Path, *, total_frames: int) -> list[ReviewDrawLayer]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    version = int(data.get("version") or 1)
    if version >= 3:
        raw = data.get("layers")
        if not isinstance(raw, list):
            return []
        out: list[ReviewDrawLayer] = []
        for item in raw:
            layer = ReviewDrawLayer.from_json(item)
            if layer is not None:
                out.append(layer)
        return _clamp_layers_to_total(out, total_frames)
    if version >= 2:
        raw = data.get("keyframes")
        if not isinstance(raw, list):
            return []
        legacy: list[ReviewDrawKeyframe] = []
        for item in raw:
            kf = ReviewDrawKeyframe.from_json(item)
            if kf is not None:
                legacy.append(kf)
        return _clamp_layers_to_total(_migrate_v2_keyframes_to_layers(legacy), total_frames)
    raw_clips = data.get("clips")
    if not isinstance(raw_clips, list):
        return []
    clips: list[ReviewDrawClip] = []
    for item in raw_clips:
        clip = ReviewDrawClip.from_json(item)
        if clip is not None:
            clips.append(clip)
    return _clamp_layers_to_total(migrate_clips_to_layers(clips), total_frames)


def _layers_nonempty(layers: Sequence[ReviewDrawLayer]) -> bool:
    return any(layer.keyframes for layer in layers)


def _write_draw_file(path: Path, media_key: Path, layers: Sequence[ReviewDrawLayer]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_draw_payload(media_key, layers), indent=2), encoding="utf-8")


def load_video_draw_sidecar(video_path: Path, *, total_frames: int) -> list[ReviewDrawLayer]:
    return _parse_draw_file(video_draw_sidecar_path(video_path), total_frames=total_frames)


def load_sequence_draw_sidecar(sequence_folder: Path, *, total_frames: int) -> list[ReviewDrawLayer]:
    return _parse_draw_file(sequence_draw_sidecar_path(sequence_folder), total_frames=total_frames)


def load_draw_local_draft(media_key: Path, *, sequence: bool, total_frames: int) -> list[ReviewDrawLayer] | None:
    path = draw_local_draft_path(media_key, sequence=sequence)
    if not path.is_file():
        return None
    return _parse_draw_file(path, total_frames=total_frames)


def save_draw_local_draft(media_key: Path, layers: Sequence[ReviewDrawLayer], *, sequence: bool) -> None:
    try:
        _write_draw_file(draw_local_draft_path(media_key, sequence=sequence), media_key, layers)
    except OSError as e:
        logger.debug("save draw draft %s: %s", media_key, e)


def save_video_draw_sidecar(video_path: Path, layers: Sequence[ReviewDrawLayer]) -> None:
    path = video_draw_sidecar_path(video_path)
    if not _layers_nonempty(layers):
        try:
            if path.is_file():
                path.unlink()
        except OSError as e:
            logger.debug("remove draw sidecar %s: %s", path, e)
        return
    try:
        _write_draw_file(path, video_path, layers)
    except OSError as e:
        logger.debug("save draw sidecar %s: %s", path, e)


def save_sequence_draw_sidecar(sequence_folder: Path, layers: Sequence[ReviewDrawLayer]) -> None:
    path = sequence_draw_sidecar_path(sequence_folder)
    if not _layers_nonempty(layers):
        try:
            if path.is_file():
                path.unlink()
        except OSError as e:
            logger.debug("remove seq draw sidecar %s: %s", path, e)
        return
    try:
        _write_draw_file(path, sequence_folder, layers)
    except OSError as e:
        logger.debug("save seq draw sidecar %s: %s", path, e)


def load_draw_layers_for_preview(
    media_key: Path,
    *,
    sequence: bool,
    total_frames: int,
) -> tuple[list[ReviewDrawLayer], list[ReviewDrawLayer], bool]:
    if sequence:
        published = load_sequence_draw_sidecar(media_key, total_frames=total_frames)
    else:
        published = load_video_draw_sidecar(media_key, total_frames=total_frames)
    local = load_draw_local_draft(media_key, sequence=sequence, total_frames=total_frames)
    if local is not None:
        return published, local, True
    return published, list(published), False


# Back-compat aliases used during transition.
clips_visible_at = draw_visible_at  # noqa: E305
clips_content_equal = layers_content_equal  # noqa: E305
keyframes_content_equal = layers_content_equal  # noqa: E305
load_draw_clips_for_preview = load_draw_layers_for_preview  # noqa: E305
load_draw_keyframes_for_preview = load_draw_layers_for_preview  # noqa: E305
