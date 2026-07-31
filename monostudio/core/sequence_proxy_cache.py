"""Disk cache for image-sequence review proxies (EXR/DPX → PNG flipbook)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SEQUENCE_PROXY_CACHE_VERSION = 2

_HEAVY_PLATE_EXTENSIONS = frozenset({".exr", ".hdr", ".dpx"})


@dataclass(frozen=True)
class SequenceFrameStat:
    source_path: str
    mtime_ns: int
    size: int
    proxy_name: str


@dataclass(frozen=True)
class SequenceProxyManifest:
    sequence_folder: str
    frame_count: int
    scale: float
    ocio_token: str
    proxy_dir: str
    frames: tuple[SequenceFrameStat, ...]
    width: int = 0
    height: int = 0
    created_at: float = 0.0

    def proxy_frame_paths(self) -> list[Path]:
        base = Path(self.proxy_dir)
        return [base / "frames" / stat.proxy_name for stat in self.frames]


def sequence_proxy_cache_dir() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if localappdata:
        return Path(localappdata) / "MonoStudio" / "cache" / "sequence_proxy"
    from monostudio.core.app_paths import get_app_base_path

    return get_app_base_path() / "monostudio_data" / "cache" / "sequence_proxy"


def is_heavy_plate_sequence(frames: list[Path]) -> bool:
    """True when sequence looks like EXR/DPX/HDR plates (benefits from PNG proxy)."""
    if not frames:
        return False
    for p in (frames[0], frames[-1]):
        if p.suffix.lower() not in _HEAVY_PLATE_EXTENSIONS:
            return False
    return True


def _frame_stat(path: Path, *, proxy_name: str) -> SequenceFrameStat | None:
    try:
        resolved = path.resolve()
        st = resolved.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
    except OSError:
        return None
    return SequenceFrameStat(
        source_path=str(resolved),
        mtime_ns=mtime_ns,
        size=size,
        proxy_name=proxy_name,
    )


def collect_sequence_frame_stats(frames: list[Path]) -> tuple[str, tuple[SequenceFrameStat, ...]]:
    """Return (digest, per-frame stats) for cache keying and manifest validation."""
    parts: list[str] = []
    stats: list[SequenceFrameStat] = []
    for i, frame in enumerate(frames):
        proxy_name = f"{i:06d}.png"
        stat = _frame_stat(frame, proxy_name=proxy_name)
        if stat is None:
            raise OSError(f"Cannot stat sequence frame: {frame}")
        stats.append(stat)
        parts.append(f"{frame.name}|{stat.mtime_ns}|{stat.size}")
    folder_key = ""
    try:
        folder_key = str(frames[0].parent.resolve()).casefold()
    except OSError:
        folder_key = str(frames[0].parent).casefold()
    payload = f"{folder_key}|{len(frames)}|" + "|".join(parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return digest, tuple(stats)


def _scale_tag(scale: float) -> str:
    pct = int(round(float(scale) * 100))
    return f"s{pct:03d}"


def _ocio_tag(ocio_token: str) -> str:
    return hashlib.sha256(ocio_token.encode("utf-8")).hexdigest()[:10]


def sequence_proxy_paths(
    digest: str,
    *,
    scale: float,
    ocio_token: str,
) -> tuple[Path, Path]:
    base = sequence_proxy_cache_dir()
    tag = _scale_tag(scale)
    ocio = _ocio_tag(ocio_token)
    root = base / f"{digest}_{tag}_{ocio}"
    return root, root / "manifest.json"


def _read_manifest(path: Path) -> SequenceProxyManifest | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("read sequence proxy manifest %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        return None
    try:
        if int(data.get("version", 0)) < SEQUENCE_PROXY_CACHE_VERSION:
            return None
        raw_frames = data.get("frames", [])
        if not isinstance(raw_frames, list):
            return None
        frames: list[SequenceFrameStat] = []
        for item in raw_frames:
            if not isinstance(item, dict):
                return None
            frames.append(
                SequenceFrameStat(
                    source_path=str(item.get("source_path", "")),
                    mtime_ns=int(item.get("mtime_ns", 0)),
                    size=int(item.get("size", 0)),
                    proxy_name=str(item.get("proxy_name", "")),
                )
            )
        return SequenceProxyManifest(
            sequence_folder=str(data.get("sequence_folder", "")),
            frame_count=int(data.get("frame_count", 0)),
            scale=float(data.get("scale", 1.0)),
            ocio_token=str(data.get("ocio_token", "")),
            proxy_dir=str(data.get("proxy_dir", "")),
            frames=tuple(frames),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            created_at=float(data.get("created_at", 0.0)),
        )
    except (TypeError, ValueError):
        return None


def _stats_match(
    current: tuple[SequenceFrameStat, ...],
    stored: tuple[SequenceFrameStat, ...],
) -> bool:
    if len(current) != len(stored):
        return False
    for cur, old in zip(current, stored):
        if cur.source_path != old.source_path:
            return False
        if cur.mtime_ns != old.mtime_ns or cur.size != old.size:
            return False
    return True


def _manifest_valid(
    manifest: SequenceProxyManifest,
    *,
    stats: tuple[SequenceFrameStat, ...],
    scale: float,
    ocio_token: str,
) -> bool:
    if manifest.frame_count != len(stats):
        return False
    if abs(manifest.scale - float(scale)) > 1e-6:
        return False
    if manifest.ocio_token != ocio_token:
        return False
    if not _stats_match(stats, manifest.frames):
        return False
    proxy_dir = Path(manifest.proxy_dir)
    if not proxy_dir.is_dir():
        return False
    for stat in manifest.frames:
        png = proxy_dir / "frames" / stat.proxy_name
        if not png.is_file() or png.stat().st_size < 32:
            return False
    return True


def write_sequence_proxy_manifest(path: Path, manifest: SequenceProxyManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SEQUENCE_PROXY_CACHE_VERSION,
        "sequence_folder": manifest.sequence_folder,
        "frame_count": manifest.frame_count,
        "scale": manifest.scale,
        "ocio_token": manifest.ocio_token,
        "proxy_dir": manifest.proxy_dir,
        "width": manifest.width,
        "height": manifest.height,
        "created_at": manifest.created_at,
        "frames": [
            {
                "source_path": f.source_path,
                "mtime_ns": f.mtime_ns,
                "size": f.size,
                "proxy_name": f.proxy_name,
            }
            for f in manifest.frames
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def lookup_sequence_proxy(
    frames: list[Path],
    *,
    scale: float,
    ocio_token: str,
) -> SequenceProxyManifest | None:
    if not frames:
        return None
    try:
        digest, stats = collect_sequence_frame_stats(frames)
    except OSError:
        return None
    proxy_dir, manifest_path = sequence_proxy_paths(digest, scale=scale, ocio_token=ocio_token)
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return None
    if not _manifest_valid(manifest, stats=stats, scale=scale, ocio_token=ocio_token):
        return None
    return manifest


def is_sequence_proxy_ready(
    frames: list[Path],
    *,
    scale: float,
    ocio_token: str,
) -> bool:
    return lookup_sequence_proxy(frames, scale=scale, ocio_token=ocio_token) is not None


def clear_sequence_proxy_for_frames(frames: list[Path]) -> int:
    if not frames:
        return 0
    try:
        digest, _ = collect_sequence_frame_stats(frames)
    except OSError:
        return 0
    base = sequence_proxy_cache_dir()
    if not base.is_dir():
        return 0
    removed = 0
    prefix = f"{digest}_"
    for p in list(base.iterdir()):
        if not p.is_dir() or not p.name.startswith(prefix):
            continue
        try:
            shutil.rmtree(p)
            removed += 1
        except OSError as e:
            logger.debug("clear sequence proxy %s: %s", p, e)
    return removed


def clear_all_sequence_proxy_cache() -> int:
    base = sequence_proxy_cache_dir()
    if not base.is_dir():
        return 0
    removed = 0
    for p in list(base.iterdir()):
        if not p.is_dir():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
            continue
        try:
            shutil.rmtree(p)
            removed += 1
        except OSError as e:
            logger.debug("clear all sequence proxy %s: %s", p, e)
    return removed


def expected_proxy_frame_paths(
    frames: list[Path],
    *,
    scale: float,
    ocio_token: str,
) -> tuple[Path, list[Path]]:
    """Return (proxy_root, per-frame PNG paths) for a sequence before/during build."""
    digest, stats = collect_sequence_frame_stats(frames)
    proxy_root, _manifest_path = sequence_proxy_paths(digest, scale=scale, ocio_token=ocio_token)
    paths = [proxy_root / "frames" / stat.proxy_name for stat in stats]
    return proxy_root, paths


def count_ready_proxy_frames(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        try:
            if p.is_file() and p.stat().st_size >= 32:
                n += 1
        except OSError:
            pass
    return n


def sequence_proxy_cache_disk_usage() -> int:
    base = sequence_proxy_cache_dir()
    if not base.is_dir():
        return 0
    total = 0
    for root, _dirs, files in os.walk(base):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total
