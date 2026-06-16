"""Disk cache for video preview proxies (full timeline + per-range)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from monostudio.core.video_media import VideoFrameRange

logger = logging.getLogger(__name__)

PROXY_CACHE_VERSION = 1


@dataclass(frozen=True)
class ProxyManifest:
    mode: Literal["full", "range"]
    source_path: str
    source_mtime_ns: int
    source_size: int
    scale: float
    proxy_path: str
    range_id: str | None = None
    in_frame: int = 0
    out_frame: int = 0
    clip_duration_sec: float = 0.0
    clip_frame_count: int = 0
    width: int = 0
    height: int = 0
    fps: float = 24.0
    codec: str = "h264"
    created_at: float = 0.0


def proxy_cache_dir() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        return Path(localappdata) / "MonoStudio" / "cache" / "video_proxy"
    from monostudio.core.app_paths import get_app_base_path

    return get_app_base_path() / "monostudio_data" / "cache" / "video_proxy"


def source_digest(video_path: Path) -> tuple[str, int, int]:
    """Return (digest, mtime_ns, size_bytes) for cache keying."""
    try:
        resolved = video_path.resolve()
    except OSError:
        resolved = video_path
    try:
        st = resolved.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
    except OSError:
        mtime_ns = 0
        size = 0
    try:
        key_src = str(resolved).casefold()
    except OSError:
        key_src = str(video_path).casefold()
    digest = hashlib.sha256(f"{key_src}|{mtime_ns}|{size}".encode("utf-8")).hexdigest()[:32]
    return digest, mtime_ns, size


def _scale_tag(scale: float) -> str:
    pct = int(round(float(scale) * 100))
    return f"s{pct:03d}"


def full_proxy_paths(digest: str, scale: float) -> tuple[Path, Path]:
    base = proxy_cache_dir()
    tag = _scale_tag(scale)
    return base / f"{digest}_full_{tag}.mp4", base / f"{digest}_full_{tag}.json"


def range_proxy_paths(digest: str, range_id: str, scale: float) -> tuple[Path, Path]:
    base = proxy_cache_dir()
    tag = _scale_tag(scale)
    safe_rid = hashlib.sha256(range_id.encode("utf-8")).hexdigest()[:12]
    return (
        base / f"{digest}_r{safe_rid}_{tag}.mp4",
        base / f"{digest}_r{safe_rid}_{tag}.json",
    )


def _read_manifest(path: Path) -> ProxyManifest | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("read proxy manifest %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        return None
    try:
        mode = str(data.get("mode", "full"))
        if mode not in ("full", "range"):
            return None
        return ProxyManifest(
            mode=mode,  # type: ignore[arg-type]
            source_path=str(data.get("source_path", "")),
            source_mtime_ns=int(data.get("source_mtime_ns", 0)),
            source_size=int(data.get("source_size", 0)),
            scale=float(data.get("scale", 1.0)),
            proxy_path=str(data.get("proxy_path", "")),
            range_id=data.get("range_id"),
            in_frame=int(data.get("in_frame", 0)),
            out_frame=int(data.get("out_frame", 0)),
            clip_duration_sec=float(data.get("clip_duration_sec", 0)),
            clip_frame_count=int(data.get("clip_frame_count", 0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            fps=float(data.get("fps", 24.0)),
            codec=str(data.get("codec", "h264")),
            created_at=float(data.get("created_at", 0)),
        )
    except (TypeError, ValueError):
        return None


def _manifest_valid(manifest: ProxyManifest, *, mtime_ns: int, size: int, scale: float) -> bool:
    proxy = Path(manifest.proxy_path)
    if not proxy.is_file():
        return False
    if manifest.source_mtime_ns != mtime_ns or manifest.source_size != size:
        return False
    if abs(manifest.scale - float(scale)) > 1e-6:
        return False
    return True


def write_manifest(path: Path, manifest: ProxyManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PROXY_CACHE_VERSION,
        "mode": manifest.mode,
        "source_path": manifest.source_path,
        "source_mtime_ns": manifest.source_mtime_ns,
        "source_size": manifest.source_size,
        "scale": manifest.scale,
        "proxy_path": manifest.proxy_path,
        "range_id": manifest.range_id,
        "in_frame": manifest.in_frame,
        "out_frame": manifest.out_frame,
        "clip_duration_sec": manifest.clip_duration_sec,
        "clip_frame_count": manifest.clip_frame_count,
        "width": manifest.width,
        "height": manifest.height,
        "fps": manifest.fps,
        "codec": manifest.codec,
        "created_at": manifest.created_at,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def lookup_full_proxy(video_path: Path, *, scale: float) -> ProxyManifest | None:
    digest, mtime_ns, size = source_digest(video_path)
    mp4, manifest_path = full_proxy_paths(digest, scale)
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return None
    if not _manifest_valid(manifest, mtime_ns=mtime_ns, size=size, scale=scale):
        return None
    if not mp4.is_file():
        return None
    return manifest


def lookup_range_proxy(
    video_path: Path,
    rng: VideoFrameRange,
    *,
    scale: float,
) -> ProxyManifest | None:
    digest, mtime_ns, size = source_digest(video_path)
    mp4, manifest_path = range_proxy_paths(digest, rng.id, scale)
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return None
    if not _manifest_valid(manifest, mtime_ns=mtime_ns, size=size, scale=scale):
        return None
    lo, hi = sorted((rng.in_frame, rng.out_frame))
    if manifest.in_frame != lo or manifest.out_frame != hi:
        return None
    if not mp4.is_file():
        return None
    return manifest


def list_cached_range_spans(
    video_path: Path,
    ranges: list[VideoFrameRange],
    *,
    scale: float,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rng in ranges:
        manifest = lookup_range_proxy(video_path, rng, scale=scale)
        if manifest is not None:
            lo, hi = sorted((manifest.in_frame, manifest.out_frame))
            spans.append((lo, hi))
    return spans


def is_full_proxy_ready(video_path: Path, *, scale: float) -> bool:
    return lookup_full_proxy(video_path, scale=scale) is not None


def clear_proxy_cache_for_source(video_path: Path) -> int:
    digest, _, _ = source_digest(video_path)
    base = proxy_cache_dir()
    if not base.is_dir():
        return 0
    removed = 0
    prefix = f"{digest}_"
    for p in base.iterdir():
        if not p.name.startswith(prefix):
            continue
        try:
            p.unlink()
            removed += 1
        except OSError as e:
            logger.debug("clear proxy %s: %s", p, e)
    return removed


def clear_all_proxy_cache() -> int:
    base = proxy_cache_dir()
    if not base.is_dir():
        return 0
    removed = 0
    for p in base.iterdir():
        name = p.name
        if p.suffix in (".mp4", ".json") or name.endswith(".part.mp4"):
            try:
                p.unlink()
                removed += 1
            except OSError as e:
                logger.debug("clear all proxy %s: %s", p, e)
    return removed


def proxy_cache_disk_usage() -> int:
    base = proxy_cache_dir()
    if not base.is_dir():
        return 0
    total = 0
    for p in base.iterdir():
        if not p.is_file():
            continue
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total
