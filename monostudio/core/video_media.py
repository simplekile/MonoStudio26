"""Video probe, frame math, range model, and FFmpeg trim/export."""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

ExportProgressCallback = Callable[[int, int, Path | None], None]
ExportCancelCheck = Callable[[], bool]
ExportSubProgressCallback = Callable[[float], None]

from monostudio.core.ffmpeg_resolve import resolve_ffmpeg_executable, resolve_ffprobe_executable
from monostudio.core.subprocess_win import hide_console_subprocess_kwargs

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg", ".ts",
})

ExportMode = Literal["separate", "concat"]
ExportFormat = Literal["source", "mp4", "mov", "mkv", "webm", "gif"]

EXPORT_FORMAT_SOURCE = "source"
EXPORT_FORMAT_MP4 = "mp4"
EXPORT_FORMAT_MOV = "mov"
EXPORT_FORMAT_MKV = "mkv"
EXPORT_FORMAT_WEBM = "webm"
EXPORT_FORMAT_GIF = "gif"

_EXPORT_FORMAT_SUFFIX: dict[str, str] = {
    EXPORT_FORMAT_MP4: ".mp4",
    EXPORT_FORMAT_MOV: ".mov",
    EXPORT_FORMAT_MKV: ".mkv",
    EXPORT_FORMAT_WEBM: ".webm",
    EXPORT_FORMAT_GIF: ".gif",
}

_EXPORT_GIF_MAX_FPS = 24.0
_EXPORT_GIF_MAX_WIDTH = 720


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration_sec: float
    fps: float
    width: int
    height: int
    frame_count: int
    video_codec: str
    has_audio: bool


@dataclass(frozen=True)
class VideoFrameRange:
    id: str
    in_frame: int
    out_frame: int
    label: str = ""

    def start_sec(self, fps: float) -> float:
        return frame_to_sec(self.in_frame, fps)

    def end_sec_exclusive(self, fps: float) -> float:
        return frame_to_sec(self.out_frame + 1, fps)

    def duration_sec(self, fps: float) -> float:
        return max(0.0, self.end_sec_exclusive(fps) - self.start_sec(fps))


@dataclass(frozen=True)
class VideoReviewMarker:
    id: str
    frame: int
    label: str = ""
    created_at: float = 0.0


ListSortMode = Literal["timeline", "name", "modified"]


def new_marker_id() -> str:
    return uuid.uuid4().hex[:8]


def validate_marker_frame(frame: int, *, total_frames: int) -> bool:
    if total_frames <= 0:
        return int(frame) >= 0
    return 0 <= int(frame) < total_frames


def sort_video_ranges(
    ranges: Sequence[VideoFrameRange],
    mode: ListSortMode,
) -> list[VideoFrameRange]:
    items = list(ranges)
    if mode == "timeline":
        return sorted(items, key=lambda r: (r.in_frame, r.out_frame, r.id))
    if mode == "name":
        return sorted(items, key=lambda r: ((r.label or "").strip().lower() or "zzz", r.in_frame))
    # modified — preserve working-list order, newest (last) first
    return list(reversed(items))


def sort_video_markers(
    markers: Sequence[VideoReviewMarker],
    mode: ListSortMode,
) -> list[VideoReviewMarker]:
    items = list(markers)
    if mode == "timeline":
        return sorted(items, key=lambda m: (m.frame, m.id))
    if mode == "name":
        return sorted(items, key=lambda m: ((m.label or "").strip().lower() or "zzz", m.frame))
    return sorted(items, key=lambda m: (-float(m.created_at or 0.0), m.frame))


def is_video_path(path: Path) -> bool:
    return path.is_file() and (path.suffix or "").strip().lower() in VIDEO_EXTENSIONS


def frame_to_sec(frame: int, fps: float) -> float:
    rate = max(1e-6, float(fps))
    return max(0, int(frame)) / rate


def sec_to_frame(sec: float, fps: float) -> int:
    rate = max(1e-6, float(fps))
    return max(0, int(round(float(sec) * rate)))


def format_timecode(sec: float, *, fps: float | None = None) -> str:
    s = max(0.0, float(sec))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    ss = s % 60.0
    if fps is not None and fps > 0:
        frame = int(round((ss - int(ss)) * fps))
        return f"{h:02d}:{m:02d}:{int(ss):02d}.{frame:02d}"
    return f"{h:02d}:{m:02d}:{ss:06.3f}"


def format_frame_label(frame: int) -> str:
    return f"{frame:04d}"


TimeDisplayMode = Literal["frame", "timecode"]


def format_position_display(
    frame: int,
    fps: float,
    *,
    mode: TimeDisplayMode = "timecode",
) -> str:
    if mode == "frame":
        return format_frame_label(frame)
    return format_timecode(frame / max(1e-6, fps), fps=fps)


def format_range_span_display(
    rng: VideoFrameRange,
    fps: float,
    *,
    mode: TimeDisplayMode = "timecode",
) -> str:
    if mode == "frame":
        return f"{format_frame_label(rng.in_frame)}–{format_frame_label(rng.out_frame)}"
    tc_in = format_timecode(rng.start_sec(fps), fps=fps)
    tc_out = format_timecode(rng.end_sec_exclusive(fps) - 1.0 / fps, fps=fps)
    return f"{tc_in}–{tc_out}"


def format_ruler_tick(t_sec: float, fps: float, *, mode: TimeDisplayMode = "timecode") -> str:
    if mode == "frame":
        return format_frame_label(int(round(t_sec * fps)))
    return format_timecode(t_sec, fps=fps)


def new_range_id() -> str:
    return uuid.uuid4().hex[:8]


def validate_range(in_frame: int, out_frame: int, *, total_frames: int) -> bool:
    if total_frames <= 0:
        return in_frame >= 0 and out_frame >= in_frame
    return 0 <= in_frame <= out_frame < total_frames


def paths_under_project_root(paths: Sequence[Path], project_root: Path | None) -> list[Path]:
    """Return paths that live inside ``project_root`` (for thumbnail refresh after export)."""
    if project_root is None:
        return []
    try:
        root = project_root.resolve()
    except OSError:
        return []
    under: list[Path] = []
    for raw in paths:
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        if p == root or root in p.parents:
            under.append(p)
    return under


def list_video_siblings(path: Path) -> list[Path]:
    if not path.is_file():
        return []
    try:
        parent = path.parent
        siblings = [p for p in parent.iterdir() if is_video_path(p)]
        siblings.sort(key=lambda p: p.name.casefold())
        return siblings
    except OSError:
        return [path]


def _parse_fps(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw or raw in ("0/0", "N/A"):
        return None
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            n, d = float(num), float(den)
            if d <= 0:
                return None
            return n / d
        except ValueError:
            return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None


def probe_video(path: Path) -> VideoInfo | None:
    ffprobe = resolve_ffprobe_executable()
    if not ffprobe:
        return None
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    path_str = str(resolved)
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate",
                "-of", "json",
                path_str,
            ],
            capture_output=True,
            timeout=15,
            text=True,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("probe_video failed for %s: %s", path_str, e)
        return None

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    duration = 0.0
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    width = height = 0
    fps = 24.0
    video_codec = ""
    has_audio = False
    for st in streams:
        ctype = (st.get("codec_type") or "").strip().lower()
        if ctype == "audio":
            has_audio = True
        if ctype != "video":
            continue
        try:
            width = int(st.get("width") or 0)
            height = int(st.get("height") or 0)
        except (TypeError, ValueError):
            pass
        video_codec = (st.get("codec_name") or "").strip()
        for key in ("avg_frame_rate", "r_frame_rate"):
            parsed = _parse_fps(str(st.get(key) or ""))
            if parsed is not None:
                fps = parsed
                break

    if fps <= 0:
        fps = 24.0
    frame_count = max(1, sec_to_frame(duration, fps)) if duration > 0 else 1
    if duration <= 0 and frame_count > 0:
        duration = frame_to_sec(frame_count - 1, fps)

    return VideoInfo(
        path=resolved,
        duration_sec=max(0.0, duration),
        fps=fps,
        width=max(0, width),
        height=max(0, height),
        frame_count=frame_count,
        video_codec=video_codec,
        has_audio=has_audio,
    )


def resolve_export_suffix(output_format: str, src: Path) -> str:
    key = (output_format or EXPORT_FORMAT_SOURCE).strip().lower()
    if key == EXPORT_FORMAT_SOURCE:
        return src.suffix or ".mp4"
    return _EXPORT_FORMAT_SUFFIX.get(key, ".mp4")


def export_format_changes_container(output_format: str, src: Path) -> bool:
    if export_format_is_gif(output_format):
        return True
    out = resolve_export_suffix(output_format, src).lower()
    src_ext = (src.suffix or ".mp4").lower()
    return out != src_ext


def export_format_is_gif(output_format: str) -> bool:
    return (output_format or "").strip().lower() == EXPORT_FORMAT_GIF


def _gif_export_fps(fps: float) -> float:
    return min(_EXPORT_GIF_MAX_FPS, max(1.0, float(fps)))


def _gif_video_filter(fps: float) -> str:
    gif_fps = _gif_export_fps(fps)
    return (
        f"fps={gif_fps:.3f},"
        f"scale={_EXPORT_GIF_MAX_WIDTH}:-1:flags=lanczos:force_original_aspect_ratio=decrease,"
        "split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3"
    )


def _ffmpeg_output_args(dst: Path, *, reencode: bool, src: Path) -> list[str]:
    out_ext = dst.suffix.lower()
    src_ext = (src.suffix or ".mp4").lower()
    if not reencode and out_ext == src_ext:
        return ["-c", "copy"]
    if out_ext == ".webm":
        return ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]


_EXPORT_PROGRESS_SCALE = 100


def _run_ffmpeg_with_progress(
    args: list[str],
    *,
    duration_sec: float,
    progress_callback: ExportSubProgressCallback | None = None,
    cancel_check: ExportCancelCheck | None = None,
) -> None:
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **hide_console_subprocess_kwargs(),
    )
    out_time_re = re.compile(r"^out_time_ms=(\d+)")
    stderr_tail: list[str] = []
    try:
        assert proc.stdout is not None
        assert proc.stderr is not None
        for line in proc.stdout:
            if cancel_check and cancel_check():
                proc.terminate()
                raise RuntimeError("Export cancelled")
            m = out_time_re.match(line.strip())
            if m and progress_callback and duration_sec > 0:
                out_ms = int(m.group(1))
                progress_callback(min(1.0, (out_ms / 1_000_000.0) / duration_sec))
        stderr_text = proc.stderr.read() or ""
        if stderr_text:
            stderr_tail = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()][-4:]
        code = proc.wait()
        if code != 0:
            detail = stderr_tail[-1] if stderr_tail else f"exit code {code}"
            raise RuntimeError(detail)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        time.sleep(0.05)


def _report_export_progress(
    progress_callback: ExportProgressCallback | None,
    *,
    step_done: float,
    total_steps: int,
    path: Path | None,
) -> None:
    if not progress_callback:
        return
    total_units = max(1, total_steps * _EXPORT_PROGRESS_SCALE)
    cur_units = min(total_units, max(0, int(step_done * _EXPORT_PROGRESS_SCALE)))
    progress_callback(cur_units, total_units, path)


def export_video_trim(
    src: Path,
    dst: Path,
    start_sec: float,
    end_sec: float,
    *,
    reencode: bool = False,
    fps: float | None = None,
    progress_callback: ExportSubProgressCallback | None = None,
    cancel_check: ExportCancelCheck | None = None,
) -> None:
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found. Configure it in Settings → Tools.")
    if end_sec <= start_sec:
        raise ValueError("Invalid trim range: end must be after start.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.01, end_sec - start_sec)
    if dst.suffix.lower() == ".gif":
        if fps is None:
            raise ValueError("GIF export requires fps.")
        _export_video_trim_gif(
            ffmpeg,
            src,
            dst,
            start_sec,
            end_sec,
            fps=fps,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        return
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-ss",
        f"{start_sec:.6f}",
        "-to",
        f"{end_sec:.6f}",
        "-i",
        str(src),
    ]
    args.extend(_ffmpeg_output_args(dst, reencode=reencode, src=src))
    args.append(str(dst))
    try:
        _run_ffmpeg_with_progress(
            args,
            duration_sec=duration,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except RuntimeError as e:
        msg = str(e).strip()
        raise RuntimeError(msg or "FFmpeg trim failed") from e


def _export_video_trim_gif(
    ffmpeg: str,
    src: Path,
    dst: Path,
    start_sec: float,
    end_sec: float,
    *,
    fps: float,
    progress_callback: ExportSubProgressCallback | None = None,
    cancel_check: ExportCancelCheck | None = None,
) -> None:
    duration = max(0.01, end_sec - start_sec)
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-ss",
        f"{start_sec:.6f}",
        "-to",
        f"{end_sec:.6f}",
        "-i",
        str(src),
        "-vf",
        _gif_video_filter(fps),
        "-an",
        "-loop",
        "0",
        str(dst),
    ]
    try:
        _run_ffmpeg_with_progress(
            args,
            duration_sec=duration,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except RuntimeError as e:
        msg = str(e).strip()
        raise RuntimeError(msg or "FFmpeg GIF export failed") from e


def _output_name(stem: str, suffix: str, index: int, template: str) -> str:
    safe_stem = re.sub(r'[<>:"/\\|?*]', "_", stem) or "clip"
    return template.format(stem=safe_stem, index=index, suffix=suffix)


def sanitize_export_label(label: str, *, max_len: int = 64) -> str:
    s = (label or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = re.sub(r"_+", "_", s).strip("._ ")
    if len(s) > max_len:
        s = s[:max_len].rstrip("._ ")
    return s


def export_naming_template_for_mode(mode: str) -> str:
    m = (mode or "range_names").strip().lower()
    if m in ("source_index", "source + index"):
        return "{stem}_{index:03d}{suffix}"
    if m in ("range_names_index", "range names + index"):
        return "{label}_{index:03d}{suffix}"
    return "{label}{suffix}"


def _output_name_for_range(
    src_stem: str,
    suffix: str,
    index: int,
    rng: VideoFrameRange,
    template: str,
    *,
    used_names: set[str],
) -> str:
    safe_stem = re.sub(r'[<>:"/\\|?*]', "_", src_stem) or "clip"
    label = sanitize_export_label(rng.label)
    if "{label}" in template and not label:
        template = "{stem}_{index:03d}{suffix}"
    base = template.format(
        stem=safe_stem,
        index=index,
        suffix=suffix,
        label=label or f"{index:03d}",
        in_frame=rng.in_frame,
        out_frame=rng.out_frame,
    )
    base = re.sub(r'[<>:"/\\|?*]', "_", base)
    name = base
    if name in used_names:
        alt = f"{Path(base).stem}_{index:03d}{suffix}"
        name = alt
        if name in used_names:
            name = f"{Path(base).stem}_{rng.in_frame}-{rng.out_frame}{suffix}"
    used_names.add(name)
    return name


def export_video_ranges(
    src: Path,
    ranges: Sequence[VideoFrameRange],
    output_dir: Path,
    *,
    fps: float,
    mode: ExportMode = "separate",
    reencode: bool = False,
    output_format: str = EXPORT_FORMAT_SOURCE,
    name_template: str = "{stem}_{index:03d}{suffix}",
    naming_mode: str | None = None,
    progress_callback=None,
    cancel_check=None,
) -> list[Path]:
    if not ranges:
        return []
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found. Configure it in Settings → Tools.")
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = resolve_export_suffix(output_format, src)
    seg_suffix = src.suffix or ".mp4"
    stem = src.stem
    outputs: list[Path] = []
    if naming_mode:
        name_template = export_naming_template_for_mode(naming_mode)
    used_names: set[str] = set()

    if export_format_is_gif(output_format) and mode == "concat":
        raise ValueError("GIF export supports separate files only (one GIF per range).")

    if mode == "separate":
        total_steps = len(ranges)
        for i, rng in enumerate(ranges, start=1):
            if cancel_check and cancel_check():
                break
            out_name = _output_name_for_range(stem, suffix, i, rng, name_template, used_names=used_names)
            dst = output_dir / out_name
            step_base = float(i - 1)

            def _on_sub(sub: float, _base: float = step_base, _dst: Path = dst) -> None:
                _report_export_progress(
                    progress_callback,
                    step_done=_base + sub,
                    total_steps=total_steps,
                    path=_dst,
                )

            export_video_trim(
                src,
                dst,
                rng.start_sec(fps),
                rng.end_sec_exclusive(fps),
                reencode=reencode,
                fps=fps,
                progress_callback=_on_sub,
                cancel_check=cancel_check,
            )
            outputs.append(dst)
            _report_export_progress(
                progress_callback,
                step_done=float(i),
                total_steps=total_steps,
                path=dst,
            )
        return outputs

    # concat: cut segments then concat demuxer
    total_steps = len(ranges) + 1
    seg_dir = output_dir / f".{stem}_segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    list_file = seg_dir / "concat.txt"
    seg_paths: list[Path] = []
    for i, rng in enumerate(ranges, start=1):
        if cancel_check and cancel_check():
            break
        seg = seg_dir / f"seg_{i:03d}{seg_suffix}"
        step_base = float(i - 1)

        def _on_seg_sub(sub: float, _base: float = step_base, _seg: Path = seg) -> None:
            _report_export_progress(
                progress_callback,
                step_done=_base + sub,
                total_steps=total_steps,
                path=_seg,
            )

        export_video_trim(
            src,
            seg,
            rng.start_sec(fps),
            rng.end_sec_exclusive(fps),
            reencode=reencode,
            fps=fps,
            progress_callback=_on_seg_sub,
            cancel_check=cancel_check,
        )
        seg_paths.append(seg)
        _report_export_progress(
            progress_callback,
            step_done=float(i),
            total_steps=total_steps,
            path=seg,
        )

    if not seg_paths:
        return []
    lines = [f"file '{p.resolve().as_posix()}'" for p in seg_paths]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    final = output_dir / f"{stem}_concat{suffix}"
    concat_duration = sum(
        max(0.01, rng.end_sec_exclusive(fps) - rng.start_sec(fps)) for rng in ranges
    )
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
    ]
    args.extend(_ffmpeg_output_args(final, reencode=reencode, src=src))
    args.append(str(final))

    def _on_concat_sub(sub: float) -> None:
        _report_export_progress(
            progress_callback,
            step_done=float(len(ranges)) + sub,
            total_steps=total_steps,
            path=final,
        )

    try:
        _run_ffmpeg_with_progress(
            args,
            duration_sec=concat_duration,
            progress_callback=_on_concat_sub,
            cancel_check=cancel_check,
        )
    except RuntimeError as e:
        msg = str(e).strip()
        raise RuntimeError(msg or "FFmpeg concat failed") from e
    _report_export_progress(
        progress_callback,
        step_done=float(total_steps),
        total_steps=total_steps,
        path=final,
    )
    return [final]


def ranges_sidecar_path(video_path: Path) -> Path:
    """Published ranges live in unified review sidecar under ``.monos/``."""
    from monostudio.core.review_sidecar import video_review_sidecar_path

    return video_review_sidecar_path(video_path)


def ranges_local_draft_path(video_path: Path) -> Path:
    """Per-machine draft under ``%LOCALAPPDATA%\\MonoStudio\\cache\\video_ranges\\``."""
    try:
        key_src = str(video_path.resolve()).casefold()
    except OSError:
        key_src = str(video_path).casefold()
    digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        base = Path(localappdata) / "MonoStudio" / "cache" / "video_ranges"
    else:
        from monostudio.core.app_paths import get_app_base_path

        base = get_app_base_path() / "monostudio_data" / "cache" / "video_ranges"
    return base / f"{digest}.json"


def _ranges_payload(video_path: Path, ranges: Sequence[VideoFrameRange]) -> dict:
    try:
        source_path = str(video_path.resolve())
    except OSError:
        source_path = str(video_path)
    return {
        "version": 1,
        "source": video_path.name,
        "source_path": source_path,
        "ranges": [
            {
                "id": r.id,
                "in_frame": r.in_frame,
                "out_frame": r.out_frame,
                "label": r.label,
            }
            for r in ranges
        ],
    }


def _parse_ranges_payload(data: object, *, total_frames: int) -> list[VideoFrameRange]:
    raw = data.get("ranges") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out: list[VideoFrameRange] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            in_f = int(item.get("in_frame", item.get("in", 0)))
            out_f = int(item.get("out_frame", item.get("out", in_f)))
        except (TypeError, ValueError):
            continue
        if not validate_range(in_f, out_f, total_frames=total_frames):
            continue
        rid = str(item.get("id") or new_range_id())
        label = str(item.get("label") or "")
        out.append(VideoFrameRange(rid, in_f, out_f, label))
    return out


def ranges_content_equal(
    a: Sequence[VideoFrameRange],
    b: Sequence[VideoFrameRange],
) -> bool:
    """Compare range in/out/label only (ids may differ between draft and project)."""
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=True):
        if (
            ra.in_frame != rb.in_frame
            or ra.out_frame != rb.out_frame
            or ra.label != rb.label
        ):
            return False
    return True


def range_is_synced(rng: VideoFrameRange, published: Sequence[VideoFrameRange]) -> bool:
    """True when ``rng`` matches the published sidecar entry with the same id."""
    for pub in published:
        if pub.id != rng.id:
            continue
        return (
            pub.in_frame == rng.in_frame
            and pub.out_frame == rng.out_frame
            and pub.label == rng.label
        )
    return False


def _read_ranges_file(path: Path, *, total_frames: int) -> list[VideoFrameRange]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("load ranges %s: %s", path, e)
        return []
    return _parse_ranges_payload(data, total_frames=total_frames)


def _write_ranges_file(path: Path, video_path: Path, ranges: Sequence[VideoFrameRange]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _ranges_payload(video_path, ranges)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_video_ranges_sidecar(video_path: Path, *, total_frames: int) -> list[VideoFrameRange]:
    from monostudio.core.review_sidecar import (
        legacy_video_ranges_path,
        read_json_dict,
        video_review_sidecar_path,
    )

    unified = read_json_dict(video_review_sidecar_path(video_path))
    if unified is not None and "ranges" in unified:
        return _parse_ranges_payload(unified, total_frames=total_frames)
    return _read_ranges_file(legacy_video_ranges_path(video_path), total_frames=total_frames)


def load_video_ranges_local_draft(
    video_path: Path,
    *,
    total_frames: int,
) -> list[VideoFrameRange] | None:
    """Return draft ranges, or ``None`` when no local draft file exists."""
    path = ranges_local_draft_path(video_path)
    if not path.is_file():
        return None
    return _read_ranges_file(path, total_frames=total_frames)


def save_video_ranges_local_draft(video_path: Path, ranges: Sequence[VideoFrameRange]) -> None:
    try:
        _write_ranges_file(ranges_local_draft_path(video_path), video_path, ranges)
    except OSError as e:
        logger.debug("save local draft %s: %s", video_path, e)


def save_video_ranges_sidecar(video_path: Path, ranges: Sequence[VideoFrameRange]) -> None:
    from monostudio.core.review_sidecar import load_video_review_dict, write_video_review_dict

    try:
        data = load_video_review_dict(video_path)
        data["ranges"] = [
            {
                "id": r.id,
                "in_frame": r.in_frame,
                "out_frame": r.out_frame,
                "label": r.label,
            }
            for r in ranges
        ]
        write_video_review_dict(video_path, data, unlink_legacy=frozenset({"ranges"}))
    except OSError as e:
        logger.debug("save sidecar %s: %s", video_path, e)


def load_video_ranges_for_preview(
    video_path: Path,
    *,
    total_frames: int,
) -> tuple[list[VideoFrameRange], list[VideoFrameRange], bool]:
    """Load published project ranges and working copy (local draft if any).

    Returns ``(published, working, from_local_draft)``.
    """
    published = load_video_ranges_sidecar(video_path, total_frames=total_frames)
    local = load_video_ranges_local_draft(video_path, total_frames=total_frames)
    if local is not None:
        return published, local, True
    return published, list(published), False


def sequence_ranges_sidecar_path(sequence_folder: Path) -> Path:
    from monostudio.core.review_sidecar import sequence_review_sidecar_path

    return sequence_review_sidecar_path(sequence_folder)


def sequence_ranges_local_draft_path(sequence_folder: Path) -> Path:
    try:
        key_src = str(sequence_folder.resolve()).casefold()
    except OSError:
        key_src = str(sequence_folder).casefold()
    digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        base = Path(localappdata) / "MonoStudio" / "cache" / "video_ranges" / "seq"
    else:
        from monostudio.core.app_paths import get_app_base_path

        base = get_app_base_path() / "monostudio_data" / "cache" / "video_ranges" / "seq"
    return base / f"{digest}.json"


def load_sequence_ranges_local_draft(sequence_folder: Path, *, total_frames: int) -> list[VideoFrameRange] | None:
    path = sequence_ranges_local_draft_path(sequence_folder)
    if not path.is_file():
        return None
    return _read_ranges_file(path, total_frames=total_frames)


def save_sequence_ranges_local_draft(sequence_folder: Path, ranges: Sequence[VideoFrameRange]) -> None:
    try:
        _write_ranges_file(sequence_ranges_local_draft_path(sequence_folder), sequence_folder, ranges)
    except OSError as e:
        logger.debug("save seq local draft %s: %s", sequence_folder, e)


def load_sequence_ranges_sidecar(sequence_folder: Path, *, total_frames: int) -> list[VideoFrameRange]:
    from monostudio.core.review_sidecar import (
        legacy_sequence_ranges_path,
        read_json_dict,
        sequence_review_sidecar_path,
    )

    unified = read_json_dict(sequence_review_sidecar_path(sequence_folder))
    if unified is not None and "ranges" in unified:
        return _parse_ranges_payload(unified, total_frames=total_frames)
    return _read_ranges_file(legacy_sequence_ranges_path(sequence_folder), total_frames=total_frames)


def save_sequence_ranges_sidecar(sequence_folder: Path, ranges: Sequence[VideoFrameRange]) -> None:
    from monostudio.core.review_sidecar import load_sequence_review_dict, write_sequence_review_dict

    try:
        data = load_sequence_review_dict(sequence_folder)
        data["ranges"] = [
            {
                "id": r.id,
                "in_frame": r.in_frame,
                "out_frame": r.out_frame,
                "label": r.label,
            }
            for r in ranges
        ]
        write_sequence_review_dict(sequence_folder, data, unlink_legacy=frozenset({"ranges"}))
    except OSError as e:
        logger.debug("save seq sidecar %s: %s", sequence_folder, e)


def load_sequence_ranges_for_preview(
    sequence_folder: Path,
    *,
    total_frames: int,
) -> tuple[list[VideoFrameRange], list[VideoFrameRange], bool]:
    published = load_sequence_ranges_sidecar(sequence_folder, total_frames=total_frames)
    local = load_sequence_ranges_local_draft(sequence_folder, total_frames=total_frames)
    if local is not None:
        return published, local, True
    return published, list(published), False


def sequence_markers_sidecar_path(sequence_folder: Path) -> Path:
    from monostudio.core.review_sidecar import sequence_review_sidecar_path

    return sequence_review_sidecar_path(sequence_folder)


def sequence_markers_local_draft_path(sequence_folder: Path) -> Path:
    try:
        key_src = str(sequence_folder.resolve()).casefold()
    except OSError:
        key_src = str(sequence_folder).casefold()
    digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        base = Path(localappdata) / "MonoStudio" / "cache" / "video_markers" / "seq"
    else:
        from monostudio.core.app_paths import get_app_base_path

        base = get_app_base_path() / "monostudio_data" / "cache" / "video_markers" / "seq"
    return base / f"{digest}.json"


def _sequence_markers_payload(sequence_folder: Path, markers: Sequence[VideoReviewMarker]) -> dict:
    try:
        source_path = str(sequence_folder.resolve())
    except OSError:
        source_path = str(sequence_folder)
    return {
        "version": 1,
        "source": sequence_folder.name,
        "source_path": source_path,
        "markers": [
            {
                "id": m.id,
                "frame": m.frame,
                "label": m.label,
                "created_at": float(m.created_at or 0.0),
            }
            for m in markers
        ],
    }


def _write_sequence_markers_file(
    path: Path,
    sequence_folder: Path,
    markers: Sequence[VideoReviewMarker],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _sequence_markers_payload(sequence_folder, markers)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_sequence_markers_sidecar(
    sequence_folder: Path,
    *,
    total_frames: int,
) -> list[VideoReviewMarker]:
    from monostudio.core.review_sidecar import (
        legacy_sequence_markers_path,
        read_json_dict,
        sequence_review_sidecar_path,
    )

    unified = read_json_dict(sequence_review_sidecar_path(sequence_folder))
    if unified is not None and "markers" in unified:
        return _parse_markers_payload(unified, total_frames=total_frames)
    return _read_markers_file(
        legacy_sequence_markers_path(sequence_folder),
        total_frames=total_frames,
    )


def load_sequence_markers_local_draft(
    sequence_folder: Path,
    *,
    total_frames: int,
) -> list[VideoReviewMarker] | None:
    path = sequence_markers_local_draft_path(sequence_folder)
    if not path.is_file():
        return None
    return _read_markers_file(path, total_frames=total_frames)


def save_sequence_markers_local_draft(
    sequence_folder: Path,
    markers: Sequence[VideoReviewMarker],
) -> None:
    try:
        _write_sequence_markers_file(
            sequence_markers_local_draft_path(sequence_folder),
            sequence_folder,
            markers,
        )
    except OSError as e:
        logger.debug("save seq marker draft %s: %s", sequence_folder, e)


def save_sequence_markers_sidecar(
    sequence_folder: Path,
    markers: Sequence[VideoReviewMarker],
) -> None:
    from monostudio.core.review_sidecar import load_sequence_review_dict, write_sequence_review_dict

    try:
        data = load_sequence_review_dict(sequence_folder)
        data["markers"] = [
            {
                "id": m.id,
                "frame": m.frame,
                "label": m.label,
                "created_at": float(m.created_at or 0.0),
            }
            for m in markers
        ]
        write_sequence_review_dict(sequence_folder, data, unlink_legacy=frozenset({"markers"}))
    except OSError as e:
        logger.debug("save seq marker sidecar %s: %s", sequence_folder, e)


def load_sequence_markers_for_preview(
    sequence_folder: Path,
    *,
    total_frames: int,
) -> tuple[list[VideoReviewMarker], list[VideoReviewMarker], bool]:
    published = load_sequence_markers_sidecar(sequence_folder, total_frames=total_frames)
    local = load_sequence_markers_local_draft(sequence_folder, total_frames=total_frames)
    if local is not None:
        return published, local, True
    return published, list(published), False


def sequence_preview_session_local_draft_path(sequence_folder: Path) -> Path:
    try:
        key_src = str(sequence_folder.resolve()).casefold()
    except OSError:
        key_src = str(sequence_folder).casefold()
    digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        base = Path(localappdata) / "MonoStudio" / "cache" / "video_preview_session" / "seq"
    else:
        from monostudio.core.app_paths import get_app_base_path

        base = get_app_base_path() / "monostudio_data" / "cache" / "video_preview_session" / "seq"
    return base / f"{digest}.json"


def load_sequence_preview_session_local_draft(
    sequence_folder: Path,
    *,
    total_frames: int,
) -> int | None:
    path = sequence_preview_session_local_draft_path(sequence_folder)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("load seq preview session %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        return None
    try:
        frame = int(data.get("frame", 0))
    except (TypeError, ValueError):
        return None
    max_f = max(0, int(total_frames) - 1)
    return max(0, min(frame, max_f))


def save_sequence_preview_session_local_draft(
    sequence_folder: Path,
    *,
    frame: int,
) -> None:
    try:
        path = sequence_preview_session_local_draft_path(sequence_folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            source_path = str(sequence_folder.resolve())
        except OSError:
            source_path = str(sequence_folder)
        payload = {
            "version": 1,
            "source": sequence_folder.name,
            "source_path": source_path,
            "frame": max(0, int(frame)),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        logger.debug("save seq preview session %s: %s", sequence_folder, e)


def export_sequence_markers_png(
    frames: Sequence[Path],
    markers: Sequence[VideoReviewMarker],
    output_dir: Path,
    *,
    progress_callback=None,
) -> list[Path]:
    if not markers or not frames:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    folder_stem = frames[0].parent.name if frames else "sequence"
    outputs: list[Path] = []
    total = len(markers)
    for i, marker in enumerate(markers, start=1):
        if progress_callback:
            progress_callback(i - 1, total, None)
        idx = max(0, min(len(frames) - 1, int(marker.frame)))
        src = frames[idx]
        safe_label = re.sub(r"[^\w\-]+", "_", (marker.label or "").strip())[:32].strip("_")
        label_part = f"_{safe_label}" if safe_label else ""
        out_name = f"{folder_stem}_mk_{i:03d}_f{marker.frame:04d}{label_part}.png"
        dst = output_dir / out_name
        shutil.copy2(src, dst)
        outputs.append(dst)
        if progress_callback:
            progress_callback(i, total, dst)
    return outputs


def export_sequence_ranges(
    frames: Sequence[Path],
    ranges: Sequence[VideoFrameRange],
    output_dir: Path,
    *,
    fps: float,
    mode: ExportMode = "separate",
    reencode: bool = True,
    output_format: str = EXPORT_FORMAT_MP4,
    name_template: str = "{stem}_{index:03d}{suffix}",
    naming_mode: str | None = None,
    progress_callback=None,
    cancel_check=None,
) -> list[Path]:
    if not ranges or not frames:
        return []
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found. Configure it in Settings → Tools.")
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = resolve_export_suffix(output_format, frames[0] if frames else Path("out.mp4"))
    stem = frames[0].parent.name if frames else "sequence"
    outputs: list[Path] = []
    if naming_mode:
        name_template = export_naming_template_for_mode(naming_mode)
    used_names: set[str] = set()

    def _frame_path_for_index(frame_idx: int) -> Path | None:
        if frame_idx < 0 or frame_idx >= len(frames):
            return None
        return frames[frame_idx]

    def _export_range_to_video(
        rng: VideoFrameRange,
        dst: Path,
        *,
        progress_callback: ExportSubProgressCallback | None = None,
        cancel_check: ExportCancelCheck | None = None,
    ) -> None:
        in_f = max(0, min(len(frames) - 1, rng.in_frame))
        out_f = max(in_f, min(len(frames) - 1, rng.out_frame))
        seg_dir = output_dir / f".{dst.stem}_frames"
        seg_dir.mkdir(parents=True, exist_ok=True)
        list_file = seg_dir / "concat.txt"
        lines: list[str] = []
        for fi in range(in_f, out_f + 1):
            fp = _frame_path_for_index(fi)
            if fp is None:
                continue
            lines.append(f"file '{fp.resolve().as_posix()}'")
            lines.append(f"duration {1.0 / max(1e-6, fps)}")
        if not lines:
            raise RuntimeError(f"No frames for range {rng.in_frame}-{rng.out_frame}")
        last_fp = _frame_path_for_index(out_f)
        if last_fp is not None:
            lines.append(f"file '{last_fp.resolve().as_posix()}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        duration = max(0.01, (out_f - in_f + 1) / max(1e-6, fps))
        args = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-r",
            str(fps),
        ]
        args.extend(_ffmpeg_output_args(dst, reencode=reencode, src=frames[0]))
        args.append(str(dst))
        _run_ffmpeg_with_progress(
            args,
            duration_sec=duration,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

    if mode == "separate":
        total_steps = len(ranges)
        for i, rng in enumerate(ranges, start=1):
            if cancel_check and cancel_check():
                break
            out_name = _output_name_for_range(stem, suffix, i, rng, name_template, used_names=used_names)
            dst = output_dir / out_name
            step_base = float(i - 1)

            def _on_sub(
                sub: float,
                _base: float = step_base,
                _dst: Path = dst,
            ) -> None:
                _report_export_progress(
                    progress_callback,
                    step_done=_base + sub,
                    total_steps=total_steps,
                    path=_dst,
                )

            _export_range_to_video(
                rng,
                dst,
                progress_callback=_on_sub,
                cancel_check=cancel_check,
            )
            outputs.append(dst)
            _report_export_progress(
                progress_callback,
                step_done=float(i),
                total_steps=total_steps,
                path=dst,
            )
        return outputs

    total_steps = len(ranges) + 1
    seg_paths: list[Path] = []
    seg_dir = output_dir / f".{stem}_segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    for i, rng in enumerate(ranges, start=1):
        if cancel_check and cancel_check():
            break
        seg = seg_dir / f"seg_{i:03d}{suffix}"
        step_base = float(i - 1)

        def _on_seg_sub(
            sub: float,
            _base: float = step_base,
            _seg: Path = seg,
        ) -> None:
            _report_export_progress(
                progress_callback,
                step_done=_base + sub,
                total_steps=total_steps,
                path=_seg,
            )

        _export_range_to_video(
            rng,
            seg,
            progress_callback=_on_seg_sub,
            cancel_check=cancel_check,
        )
        seg_paths.append(seg)
        _report_export_progress(
            progress_callback,
            step_done=float(i),
            total_steps=total_steps,
            path=seg,
        )
    if not seg_paths:
        return []
    list_file = seg_dir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in seg_paths) + "\n",
        encoding="utf-8",
    )
    final = output_dir / f"{stem}_concat{suffix}"
    concat_duration = sum(
        max(0.01, (max(0, min(len(frames) - 1, rng.out_frame)) - max(0, min(len(frames) - 1, rng.in_frame)) + 1)
        / max(1e-6, fps))
        for rng in ranges
    )

    def _on_concat_sub(sub: float) -> None:
        _report_export_progress(
            progress_callback,
            step_done=float(len(ranges)) + sub,
            total_steps=total_steps,
            path=final,
        )

    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
    ]
    args.extend(_ffmpeg_output_args(final, reencode=reencode, src=frames[0]))
    args.append(str(final))
    _run_ffmpeg_with_progress(
        args,
        duration_sec=max(0.01, concat_duration),
        progress_callback=_on_concat_sub,
        cancel_check=cancel_check,
    )
    outputs.append(final)
    _report_export_progress(
        progress_callback,
        step_done=float(total_steps),
        total_steps=total_steps,
        path=final,
    )
    return outputs


def markers_sidecar_path(video_path: Path) -> Path:
    from monostudio.core.review_sidecar import video_review_sidecar_path

    return video_review_sidecar_path(video_path)


def markers_local_draft_path(video_path: Path) -> Path:
    try:
        key_src = str(video_path.resolve()).casefold()
    except OSError:
        key_src = str(video_path).casefold()
    digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        base = Path(localappdata) / "MonoStudio" / "cache" / "video_markers"
    else:
        from monostudio.core.app_paths import get_app_base_path

        base = get_app_base_path() / "monostudio_data" / "cache" / "video_markers"
    return base / f"{digest}.json"


def _markers_payload(video_path: Path, markers: Sequence[VideoReviewMarker]) -> dict:
    try:
        source_path = str(video_path.resolve())
    except OSError:
        source_path = str(video_path)
    return {
        "version": 1,
        "source": video_path.name,
        "source_path": source_path,
        "markers": [
            {
                "id": m.id,
                "frame": m.frame,
                "label": m.label,
                "created_at": float(m.created_at or 0.0),
            }
            for m in markers
        ],
    }


def _parse_markers_payload(data: object, *, total_frames: int) -> list[VideoReviewMarker]:
    raw = data.get("markers") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out: list[VideoReviewMarker] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            frame = int(item.get("frame", 0))
        except (TypeError, ValueError):
            continue
        if not validate_marker_frame(frame, total_frames=total_frames):
            continue
        rid = str(item.get("id") or new_marker_id())
        label = str(item.get("label") or "")
        try:
            created_at = float(item.get("created_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        out.append(VideoReviewMarker(rid, frame, label, created_at))
    return out


def markers_content_equal(
    a: Sequence[VideoReviewMarker],
    b: Sequence[VideoReviewMarker],
) -> bool:
    if len(a) != len(b):
        return False
    for ma, mb in zip(a, b, strict=True):
        if ma.frame != mb.frame or ma.label != mb.label:
            return False
    return True


def marker_is_synced(m: VideoReviewMarker, published: Sequence[VideoReviewMarker]) -> bool:
    for pub in published:
        if pub.id != m.id:
            continue
        return pub.frame == m.frame and pub.label == m.label
    return False


def _read_markers_file(path: Path, *, total_frames: int) -> list[VideoReviewMarker]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("load markers %s: %s", path, e)
        return []
    return _parse_markers_payload(data, total_frames=total_frames)


def _write_markers_file(path: Path, video_path: Path, markers: Sequence[VideoReviewMarker]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _markers_payload(video_path, markers)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_video_markers_sidecar(video_path: Path, *, total_frames: int) -> list[VideoReviewMarker]:
    from monostudio.core.review_sidecar import (
        legacy_video_markers_path,
        read_json_dict,
        video_review_sidecar_path,
    )

    unified = read_json_dict(video_review_sidecar_path(video_path))
    if unified is not None and "markers" in unified:
        return _parse_markers_payload(unified, total_frames=total_frames)
    return _read_markers_file(legacy_video_markers_path(video_path), total_frames=total_frames)


def load_video_markers_local_draft(
    video_path: Path,
    *,
    total_frames: int,
) -> list[VideoReviewMarker] | None:
    path = markers_local_draft_path(video_path)
    if not path.is_file():
        return None
    return _read_markers_file(path, total_frames=total_frames)


def save_video_markers_local_draft(video_path: Path, markers: Sequence[VideoReviewMarker]) -> None:
    try:
        _write_markers_file(markers_local_draft_path(video_path), video_path, markers)
    except OSError as e:
        logger.debug("save marker draft %s: %s", video_path, e)


def preview_session_local_draft_path(video_path: Path) -> Path:
    """Per-machine preview session (playhead frame) keyed by source video path."""
    try:
        key_src = str(video_path.resolve()).casefold()
    except OSError:
        key_src = str(video_path).casefold()
    digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        base = Path(localappdata) / "MonoStudio" / "cache" / "video_preview_session"
    else:
        from monostudio.core.app_paths import get_app_base_path

        base = get_app_base_path() / "monostudio_data" / "cache" / "video_preview_session"
    return base / f"{digest}.json"


def load_video_preview_session_local_draft(
    video_path: Path,
    *,
    total_frames: int,
) -> int | None:
    """Return saved playhead frame, or ``None`` when no session file exists."""
    path = preview_session_local_draft_path(video_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("load preview session %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        return None
    try:
        frame = int(data.get("frame", 0))
    except (TypeError, ValueError):
        return None
    max_f = max(0, int(total_frames) - 1)
    return max(0, min(frame, max_f))


def save_video_preview_session_local_draft(video_path: Path, *, frame: int) -> None:
    try:
        path = preview_session_local_draft_path(video_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            source_path = str(video_path.resolve())
        except OSError:
            source_path = str(video_path)
        payload = {
            "version": 1,
            "source": video_path.name,
            "source_path": source_path,
            "frame": max(0, int(frame)),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        logger.debug("save preview session %s: %s", video_path, e)


def save_video_markers_sidecar(video_path: Path, markers: Sequence[VideoReviewMarker]) -> None:
    from monostudio.core.review_sidecar import load_video_review_dict, write_video_review_dict

    try:
        data = load_video_review_dict(video_path)
        data["markers"] = [
            {
                "id": m.id,
                "frame": m.frame,
                "label": m.label,
                "created_at": float(m.created_at or 0.0),
            }
            for m in markers
        ]
        write_video_review_dict(video_path, data, unlink_legacy=frozenset({"markers"}))
    except OSError as e:
        logger.debug("save marker sidecar %s: %s", video_path, e)


def load_video_markers_for_preview(
    video_path: Path,
    *,
    total_frames: int,
) -> tuple[list[VideoReviewMarker], list[VideoReviewMarker], bool]:
    published = load_video_markers_sidecar(video_path, total_frames=total_frames)
    local = load_video_markers_local_draft(video_path, total_frames=total_frames)
    if local is not None:
        return published, local, True
    return published, list(published), False


def export_video_markers_png(
    src: Path,
    markers: Sequence[VideoReviewMarker],
    output_dir: Path,
    *,
    fps: float,
    progress_callback=None,
) -> list[Path]:
    if not markers:
        return []
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found. Configure it in Settings → Tools.")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    outputs: list[Path] = []
    total = len(markers)
    try:
        path_str = str(src.resolve())
    except OSError:
        path_str = str(src)
    for i, marker in enumerate(markers, start=1):
        if progress_callback:
            progress_callback(i - 1, total, None)
        safe_label = re.sub(r"[^\w\-]+", "_", (marker.label or "").strip())[:32].strip("_")
        label_part = f"_{safe_label}" if safe_label else ""
        out_name = f"{stem}_mk_{i:03d}_f{marker.frame:04d}{label_part}.png"
        dst = output_dir / out_name
        sec = frame_to_sec(marker.frame, fps)
        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, float(sec)):.6f}",
            "-i",
            path_str,
            "-an",
            "-sn",
            "-dn",
            "-vframes",
            "1",
            "-f",
            "image2",
            str(dst),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not dst.is_file():
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(err or f"FFmpeg export failed for frame {marker.frame}")
        outputs.append(dst)
        if progress_callback:
            progress_callback(i, total, dst)
    return outputs


def resolve_work_preview_video(work_path: Path, work_file_path: Path | None) -> Path | None:
    """Find newest playblast/preview video under department ``work/``."""
    from monostudio.core.sequence_preview import (
        _sequence_roots_by_priority,
        work_file_folder_name_candidates,
    )

    if not work_path.is_dir():
        return None
    names = work_file_folder_name_candidates(work_file_path)
    hits: list[Path] = []

    def consider_file(p: Path) -> None:
        if is_video_path(p):
            hits.append(p)

    for root in _sequence_roots_by_priority(work_path):
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
            continue
        for n in names:
            for ext in (".mp4", ".mov", ".mkv", ".webm"):
                consider_file(root / f"{n}{ext}")

    if not hits:
        return None

    def _mtime_ns(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0

    hits.sort(key=_mtime_ns, reverse=True)
    return hits[0]


def fallback_scrub_snap_frames(
    fps: float,
    frame_count: int,
    *,
    interval_sec: float = 1.0,
) -> list[int]:
    """Coarse snap grid until real keyframes are known."""
    total = max(1, int(frame_count))
    step = max(1, int(round(max(1e-6, float(fps)) * interval_sec)))
    return list(range(0, total, step))


def snap_frame_to_nearest_keyframe(frame: int, key_frames: Sequence[int]) -> int:
    if not key_frames:
        return max(0, int(frame))
    frame = max(0, int(frame))
    if frame <= key_frames[0]:
        return key_frames[0]
    if frame >= key_frames[-1]:
        return key_frames[-1]
    i = bisect.bisect_left(key_frames, frame)
    if i <= 0:
        return key_frames[0]
    if i >= len(key_frames):
        return key_frames[-1]
    before = key_frames[i - 1]
    after = key_frames[i]
    return before if frame - before <= after - frame else after


def probe_video_keyframe_frames(
    path: Path,
    *,
    fps: float,
    frame_count: int,
) -> list[int]:
    """Video keyframe indices for fast scrub thumbnails (packet demux via ffprobe)."""
    ffprobe = resolve_ffprobe_executable()
    if not ffprobe or not path.is_file():
        return []
    total = max(1, int(frame_count))
    rate = max(1e-6, float(fps))
    try:
        path_str = str(path.resolve())
    except OSError:
        path_str = str(path)
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "packet=pts_time,flags",
                "-of",
                "csv=p=0",
                path_str,
            ],
            capture_output=True,
            timeout=45,
            text=True,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout:
            return []
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("probe_video_keyframe_frames failed for %s: %s", path_str, e)
        return []

    key_frames: set[int] = {0}
    for line in proc.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2 or "K" not in parts[1]:
            continue
        try:
            t = float(parts[0])
        except ValueError:
            continue
        f = sec_to_frame(t, rate)
        if 0 <= f < total:
            key_frames.add(f)
    out = sorted(key_frames)
    if not out:
        return fallback_scrub_snap_frames(rate, total)
    return out


def extract_video_frame_png_bytes(
    video_path: Path,
    sec: float,
    *,
    width: int = 160,
    keyframe_aligned: bool = False,
) -> bytes | None:
    """Single frame at ``sec`` as PNG bytes (for scrub hover preview)."""
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg or not video_path.is_file():
        return None
    try:
        path_str = str(video_path.resolve())
    except OSError:
        path_str = str(video_path)
    w = max(64, min(480, int(width)))
    try:
        cmd: list[str] = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, float(sec)):.6f}",
            "-i",
            path_str,
            "-an",
            "-sn",
            "-dn",
        ]
        if keyframe_aligned:
            cmd.extend(["-skip_frame", "nokey"])
        cmd.extend(
            [
                "-vframes",
                "1",
                "-vf",
                f"scale={w}:-1",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-",
            ]
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=8,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("extract_video_frame_png_bytes failed for %s: %s", path_str, e)
        return None
