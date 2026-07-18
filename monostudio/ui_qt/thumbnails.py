from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QObject, QTimer, QSettings
from PySide6.QtGui import QImage, QImageReader, QPixmap

from monostudio.core.ffmpeg_resolve import resolve_ffprobe_executable, resolve_ffmpeg_executable
from monostudio.core.subprocess_win import hide_console_subprocess_kwargs

if TYPE_CHECKING:
    from monostudio.core.models import Asset, Shot
    from monostudio.ui_qt.app_state import AppState
    from monostudio.ui_qt.worker_manager import WorkerManager

logger = logging.getLogger(__name__)

DEFAULT_THUMB_SIZE_PX = 384

# Extensions that Qt can load as image — use file itself as thumbnail
_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tga", ".tif", ".tiff",
    ".exr", ".hdr", ".ico", ".svg", ".ppm", ".xbm", ".xpm",
})
# Video: extract one frame via ffmpeg (fast seek -ss before -i)
_VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg",
    ".ts",  # MPEG Transport Stream
})
DEFAULT_MEMORY_CACHE_MAX = 200

# Settings: rules for scanning frames under work/<render|preview|playblast|flipbook>/...
SETTINGS_KEY_THUMB_SEQ_IGNORE_EXT = "pipeline/thumbnail_sequence_ignore_extensions"
SETTINGS_KEY_THUMB_SEQ_IGNORE_TOKENS = "pipeline/thumbnail_sequence_ignore_tokens"
DEFAULT_THUMB_SEQ_IGNORE_TOKENS = "cryptomatte,z"


def _parse_ignore_extensions(raw: str) -> frozenset[str]:
    result: set[str] = set()
    for part in (raw or "").split(","):
        ext = (part or "").strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        result.add(ext)
    return frozenset(result)


def _parse_ignore_tokens(raw: str) -> frozenset[str]:
    result: set[str] = set()
    for part in (raw or "").split(","):
        tok = (part or "").strip()
        if not tok:
            continue
        result.add(tok)
    return frozenset(result)


def get_thumbnail_sequence_ignore_extensions(settings: QSettings | None) -> frozenset[str]:
    """Parse pipeline/thumbnail_sequence_ignore_extensions (comma-separated). Normalized to lowercase with leading dot."""
    if settings is None:
        return frozenset()
    raw = settings.value(SETTINGS_KEY_THUMB_SEQ_IGNORE_EXT, "", str) or ""
    return _parse_ignore_extensions(raw)


def get_thumbnail_sequence_ignore_tokens(settings: QSettings | None) -> frozenset[str]:
    """
    Parse pipeline/thumbnail_sequence_ignore_tokens (comma-separated).
    Tokens are substring-matched against filename (case-insensitive).
    """
    if settings is None:
        return _parse_ignore_tokens(DEFAULT_THUMB_SEQ_IGNORE_TOKENS)
    raw = settings.value(SETTINGS_KEY_THUMB_SEQ_IGNORE_TOKENS, DEFAULT_THUMB_SEQ_IGNORE_TOKENS, str) or DEFAULT_THUMB_SEQ_IGNORE_TOKENS
    return _parse_ignore_tokens(raw)


def _get_video_max_dimension(video_path: Path) -> int | None:
    """Largest video stream dimension via ffprobe; None if unavailable."""
    ffprobe = resolve_ffprobe_executable()
    if not ffprobe:
        return None
    path_str = str(video_path.resolve())
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                path_str,
            ],
            capture_output=True,
            timeout=5,
            text=True,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout or not proc.stdout.strip():
            return None
        parts = proc.stdout.strip().split("x")
        if len(parts) != 2:
            return None
        w, h = int(parts[0]), int(parts[1])
        if w < 1 or h < 1:
            return None
        return max(w, h)
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        logger.debug("ffprobe dimensions failed for %s: %s", path_str, e)
        return None


def media_source_max_side(path: Path) -> int | None:
    """Largest native dimension of a readable image/video file; None if unknown."""
    if not path.is_file():
        return None
    ext = (path.suffix or "").strip().lower()
    if ext in _VIDEO_EXTENSIONS:
        return _get_video_max_dimension(path)
    try:
        from PySide6.QtGui import QImageReader

        reader = QImageReader(str(path))
        sz = reader.size()
        if sz.isValid() and sz.width() > 0 and sz.height() > 0:
            return max(sz.width(), sz.height())
    except Exception:
        pass
    return None


def clamp_decode_side_for_media(requested: int, path: Path) -> int:
    """Cap decode size to native media dimensions (never upscale beyond source)."""
    side = max(1, int(requested))
    src_max = media_source_max_side(path)
    if src_max is not None:
        side = min(side, src_max)
    return side


def _downscale_pixmap_max_side(pix: QPixmap, max_side: int) -> QPixmap:
    mx = max(pix.width(), pix.height())
    if mx <= max_side:
        return pix
    return pix.scaled(
        max_side,
        max_side,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _downscale_image_max_side(img: QImage, max_side: int) -> QImage:
    mx = max(img.width(), img.height())
    if mx <= max_side:
        return img
    return img.scaled(
        max_side,
        max_side,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _get_video_duration_seconds(video_path: Path) -> float | None:
    """Get video duration in seconds via ffprobe; None if unavailable or invalid."""
    ffprobe = resolve_ffprobe_executable()
    if not ffprobe:
        return None
    path_str = str(video_path.resolve())
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path_str,
            ],
            capture_output=True,
            timeout=5,
            text=True,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout or not proc.stdout.strip():
            return None
        return float(proc.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        logger.debug("ffprobe duration failed for %s: %s", path_str, e)
        return None


def _load_video_frame_via_ffmpeg(video_path: Path, size_px: int) -> QPixmap | None:
    """
    Extract one frame from video at 1/4 duration using ffmpeg (fast: -ss before -i).
    Falls back to frame at 0s if duration unknown. Returns scaled QPixmap or None.
    """
    ffmpeg = resolve_ffmpeg_executable()
    if not ffmpeg:
        return None
    path_str = str(video_path.resolve())
    seek_sec = 0.0
    duration = _get_video_duration_seconds(video_path)
    if duration is not None and duration > 0:
        seek_sec = duration / 4.0
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel", "error",
                "-ss", str(seek_sec),
                "-i", path_str,
                "-vframes", "1",
                "-f", "image2pipe",
                "-c:v", "png",
                "-",
            ],
            capture_output=True,
            timeout=10,
            check=False,
            **hide_console_subprocess_kwargs(),
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        img = QImage()
        if not img.loadFromData(proc.stdout):
            return None
        pix = QPixmap.fromImage(img)
        if pix.isNull():
            return None
        return _downscale_pixmap_max_side(pix, size_px)
    except (subprocess.TimeoutExpired, OSError, ValueError) as e:
        logger.debug("Video thumbnail ffmpeg failed for %s: %s", path_str, e)
        return None


_DEPT_THUMB_CACHE_SEP = "::dept::"
_THUMB_ACTIVE_DCC_MARKER = "::adc::"
_THUMB_SOURCE_MODE_MARKER = "::tsm::"
_THUMB_SCAN_RULES_MARKER = "::sr::"


def active_dcc_segment_for_thumbnail_cache(active_dcc_id: str | None) -> str:
    """Normalized active DCC for grid/list thumbnail cache key; __none__ when no selection in open.json."""
    s = (active_dcc_id or "").strip().casefold()
    return s if s else "__none__"


def _thumbnail_disk_cache_dir() -> Path:
    """Thumbnail disk cache root: Windows temp (or system temp) / MonoStudio26 / thumbnails. Not deleted by app."""
    return Path(tempfile.gettempdir()) / "MonoStudio26" / "thumbnails"


REF_PREVIEW_DISK_CACHE_VARIANT = "ref_cover"
EXPLORER_PREVIEW_DISK_CACHE_VARIANT = "explorer"
INSPECTOR_INBOX_PREVIEW_DISK_CACHE_VARIANT = "inbox_hd"
EXPLORER_GRID_CARD_WIDTH_PX = 200

_PLATE_THUMB_EXTENSIONS = frozenset({".exr", ".dpx", ".hdr"})


def _plate_display_cache_token(source_path: Path) -> str:
    """Bust disk cache when OCIO / plate decode changes (EXR/DPX/HDR thumbs)."""
    if (source_path.suffix or "").lower() not in _PLATE_THUMB_EXTENSIONS:
        return ""
    try:
        from monostudio.core.ocio_display import PLATE_DECODE_CACHE_REV
        from monostudio.ui_qt.ocio_preview_settings import ocio_preview_cache_token

        return f"plate|{PLATE_DECODE_CACHE_REV}|{ocio_preview_cache_token()}"
    except Exception:
        return "plate"


def _disk_cache_path(source_path: Path, mtime_ns: int, size_px: int, *, variant: str = "") -> Path:
    """Path to cached PNG for this source file; same path+mtime+size+variant yields same file."""
    plate_tok = _plate_display_cache_token(source_path)
    raw = f"{source_path.resolve()!s}\n{mtime_ns}\n{size_px}\n{variant}\n{plate_tok}"
    h = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]
    return _thumbnail_disk_cache_dir() / f"{h}.png"


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MIN_PNG_CACHE_BYTES = 24


def _remove_disk_cache_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _is_plausible_png_cache(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        if path.stat().st_size < _MIN_PNG_CACHE_BYTES:
            return False
        with path.open("rb") as fh:
            return fh.read(8) == _PNG_SIGNATURE
    except OSError:
        return False


def _read_disk_cache_qimage(path: Path) -> QImage | None:
    """Read a cached PNG; drop corrupt/partial files (avoids repeated libpng errors)."""
    if not _is_plausible_png_cache(path):
        _remove_disk_cache_file(path)
        return None
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        _remove_disk_cache_file(path)
        return None
    return img


def _read_disk_cache_pixmap(path: Path) -> QPixmap | None:
    img = _read_disk_cache_qimage(path)
    if img is None or img.isNull():
        return None
    pix = QPixmap.fromImage(img)
    return pix if not pix.isNull() else None


def _write_disk_cache_qimage(path: Path, img: QImage) -> None:
    if img.isNull():
        return
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.part")
        if not img.save(str(tmp), "PNG"):
            _remove_disk_cache_file(tmp)
            return
        tmp.replace(path)
        tmp = None
    except OSError:
        pass
    finally:
        if tmp is not None:
            _remove_disk_cache_file(tmp)


def _write_disk_cache_pixmap(path: Path, pix: QPixmap) -> None:
    if pix.isNull():
        return
    _write_disk_cache_qimage(path, pix.toImage())


def _qimage_cover_square(img: QImage, side: int) -> QImage:
    """Center-crop to a square after aspect-fill scale (Inspector ref grid)."""
    from PySide6.QtCore import Qt

    side = max(1, int(side))
    scaled = img.scaled(
        side,
        side,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    sx = max(0, (scaled.width() - side) // 2)
    sy = max(0, (scaled.height() - side) // 2)
    return scaled.copy(sx, sy, side, side)


def resolve_department_thumbnail_path(item_root: Path, department: str) -> Path | None:
    """Resolve department-specific thumbnail from .meta/ folder. Returns None if not found."""
    dep = (department or "").strip()
    if not dep:
        return None
    meta = item_root / ".meta"
    for name in (
        f"thumb_{dep}.user.png",
        f"thumb_{dep}.user.jpg",
        f"thumb_{dep}.png",
        f"thumb_{dep}.jpg",
    ):
        p = meta / name
        if p.is_file():
            return p
    return None


def resolve_user_only_thumbnail_path(item_root: Path, department: str | None) -> Path | None:
    """Only user-provided thumbnails (paste / explicit .user. files), not auto thumb_{dept}.png."""
    dep = (department or "").strip()
    if dep:
        meta = item_root / ".meta"
        for name in (f"thumb_{dep}.user.png", f"thumb_{dep}.user.jpg"):
            p = meta / name
            if p.is_file():
                return p
    for name in ("thumbnail.user.png", "thumbnail.user.jpg"):
        p = item_root / name
        if p.is_file():
            return p
    return None


def resolve_thumbnail_path(item_root: Path, department: str | None = None) -> Path | None:
    """
    Resolve thumbnail path with department fallback:
      1. department thumb in .meta/  (if department given)
      2. entity-level thumb
      3. direct file (image/video)
    """
    if item_root.is_file():
        ext = (item_root.suffix or "").strip().lower()
        if ext in _IMAGE_EXTENSIONS or ext in _VIDEO_EXTENSIONS:
            return item_root
        return None
    dep = (department or "").strip()
    if dep:
        dept_thumb = resolve_department_thumbnail_path(item_root, dep)
        if dept_thumb is not None:
            return dept_thumb
    for name in ("thumbnail.user.png", "thumbnail.user.jpg", "thumbnail.png", "thumbnail.jpg"):
        p = item_root / name
        if p.is_file():
            return p
    return None


def thumb_source_fingerprint(source: Path | None) -> str:
    """
    Cheap identity for a resolved thumbnail source file.

    Includes file mtime and parent-dir mtime so overwritten frames and new
    sequence files invalidate in-memory pixmap caches (ThumbnailManager / inspector).
    """
    if source is None:
        return ""
    try:
        resolved = str(source.resolve())
    except OSError:
        resolved = str(source)
    try:
        st = source.stat()
        file_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
    except OSError:
        file_ns = -1
    parent_ns = -1
    try:
        if source.is_file():
            pst = source.parent.stat()
            parent_ns = int(getattr(pst, "st_mtime_ns", int(pst.st_mtime * 1_000_000_000)))
    except OSError:
        pass
    return f"{resolved}|{file_ns}|{parent_ns}"


def make_department_cache_key(
    entity_path: str,
    department: str | None,
    *,
    thumb_active_dcc_sig: str | None = None,
    thumb_source_mode: str | None = None,
    thumb_scan_rules_sig: str | None = None,
) -> str:
    """Build cache key: entity or entity::dept::dep; optional ::adc::, ::tsm::, ::sr:: (in that order)."""
    dep = (department or "").strip()
    if dep:
        base = f"{entity_path}{_DEPT_THUMB_CACHE_SEP}{dep}"
    else:
        base = entity_path
    if thumb_active_dcc_sig is not None:
        base = f"{base}{_THUMB_ACTIVE_DCC_MARKER}{thumb_active_dcc_sig}"
    if thumb_source_mode:
        base = f"{base}{_THUMB_SOURCE_MODE_MARKER}{thumb_source_mode}"
    if thumb_scan_rules_sig:
        base = f"{base}{_THUMB_SCAN_RULES_MARKER}{thumb_scan_rules_sig}"
    return base


def parse_department_cache_key(cache_key: str) -> tuple[str, str | None]:
    """Split cache key into (entity_path, department_or_None). Strips ::sr::, ::tsm::, ::adc:: suffixes."""
    s = cache_key
    if _THUMB_SCAN_RULES_MARKER in s:
        s = s.split(_THUMB_SCAN_RULES_MARKER, 1)[0]
    if _THUMB_SOURCE_MODE_MARKER in s:
        s = s.split(_THUMB_SOURCE_MODE_MARKER, 1)[0]
    if _THUMB_ACTIVE_DCC_MARKER in s:
        s = s.split(_THUMB_ACTIVE_DCC_MARKER, 1)[0]
    if _DEPT_THUMB_CACHE_SEP in s:
        parts = s.split(_DEPT_THUMB_CACHE_SEP, 1)
        return (parts[0], parts[1] if len(parts) > 1 else None)
    return (s, None)


@dataclass
class _CachedPixmap:
    mtime_ns: int
    pixmap: QPixmap


class ThumbnailCache:
    """
    Read-only thumbnail cache.
    Cache key uses: file path + modification time.
    """

    def __init__(self, *, size_px: int, cache_variant: str = "") -> None:
        self._size_px = size_px
        self._cache_variant = cache_variant
        self._cache: dict[str, _CachedPixmap] = {}

    def _disk_cache_path_for(self, file_path: Path, mtime_ns: int) -> Path:
        return _disk_cache_path(file_path, mtime_ns, self._size_px, variant=self._cache_variant)

    def resolve_thumbnail_file(self, item_root: Path, department: str | None = None) -> Path | None:
        return resolve_thumbnail_path(item_root, department=department)

    def invalidate_file(self, file_path: Path) -> None:
        # Best-effort; safe if missing.
        try:
            self._cache.pop(str(file_path), None)
        except Exception:
            pass

    def peek_thumbnail_pixmap(self, file_path: Path) -> QPixmap | None:
        """Return a cached pixmap only (memory or disk PNG); never decodes source on miss."""
        key = str(file_path)
        try:
            stat = file_path.stat()
        except OSError:
            return None
        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        cached = self._cache.get(key)
        if cached is not None and cached.mtime_ns == mtime_ns:
            return cached.pixmap
        try:
            dc_path = self._disk_cache_path_for(file_path, mtime_ns)
            pix = _read_disk_cache_pixmap(dc_path)
            if pix is not None and not pix.isNull():
                self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=pix)
                return pix
        except OSError:
            pass
        return None

    def adopt_decoded_thumbnail(self, file_path: Path, image: QImage) -> QPixmap | None:
        """Store a worker-decoded image in memory + disk cache; returns pixmap for UI."""
        if image.isNull():
            return None
        try:
            stat = file_path.stat()
        except OSError:
            return None
        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        pix = QPixmap.fromImage(image)
        if pix.isNull():
            return None
        self._cache[str(file_path)] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=pix)
        try:
            dc_path = self._disk_cache_path_for(file_path, mtime_ns)
            _write_disk_cache_pixmap(dc_path, pix)
        except OSError:
            pass
        return pix

    def load_thumbnail_pixmap(self, file_path: Path) -> QPixmap | None:
        key = str(file_path)
        try:
            stat = file_path.stat()
        except FileNotFoundError:
            return None

        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        cached = self._cache.get(key)
        if cached is not None and cached.mtime_ns == mtime_ns:
            return cached.pixmap

        # Disk cache in Windows temp: read first; never deleted by app
        dc_path = self._disk_cache_path_for(file_path, mtime_ns)
        try:
            pix = _read_disk_cache_pixmap(dc_path)
            if pix is not None and not pix.isNull():
                self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=pix)
                return pix
        except OSError:
            pass

        ext = (file_path.suffix or "").strip().lower()
        if file_path.is_file() and ext in _VIDEO_EXTENSIONS:
            pix = _load_video_frame_via_ffmpeg(file_path, self._size_px)
            if pix is not None:
                self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=pix)
                try:
                    _write_disk_cache_pixmap(dc_path, pix)
                except OSError:
                    pass
            return pix

        if ext in _PLATE_THUMB_EXTENSIONS:
            from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

            img = load_preview_frame_qimage(file_path, self._size_px)
            if img is not None and not img.isNull():
                pix = QPixmap.fromImage(img)
                if not pix.isNull():
                    scaled = _downscale_pixmap_max_side(pix, self._size_px)
                    self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=scaled)
                    try:
                        _write_disk_cache_pixmap(dc_path, scaled)
                    except OSError:
                        pass
                    return scaled
            return None

        pix = QPixmap(key)
        if pix.isNull():
            return None

        scaled = _downscale_pixmap_max_side(pix, self._size_px)
        self._cache[key] = _CachedPixmap(mtime_ns=mtime_ns, pixmap=scaled)
        try:
            _write_disk_cache_pixmap(dc_path, scaled)
        except OSError:
            pass
        return scaled


def is_direct_media_preview_path(path: Path) -> bool:
    """True when *path* is an image or video file the explorer can preview."""
    if not path.is_file():
        return False
    ext = (path.suffix or "").strip().lower()
    return ext in _IMAGE_EXTENSIONS or ext in _VIDEO_EXTENSIONS


def is_video_preview_path(path: Path) -> bool:
    """True when *path* is a video file supported by the video preview dialog."""
    if not path.is_file():
        return False
    return (path.suffix or "").strip().lower() in _VIDEO_EXTENSIONS


def explorer_grid_thumb_decode_px(*, dpr: float = 1.0, card_width: int = EXPLORER_GRID_CARD_WIDTH_PX) -> int:
    """Device-pixel decode size for 16:9 explorer grid thumb (1:1 with painted band, no upscale)."""
    dpr_v = max(1.0, float(dpr))
    thumb_h = max(1, int(card_width) * 9 // 16)
    dev_w = max(1, int(round(card_width * dpr_v)))
    dev_h = max(1, int(round(thumb_h * dpr_v)))
    side = max(dev_w, dev_h)
    return max(64, min(1024, ((side + 15) // 16) * 16))


def explorer_list_icon_decode_px(*, dpr: float = 1.0, icon_logical: int = 40) -> int:
    """Device-pixel decode size for explorer list-row thumb slot."""
    side = max(1, int(round(icon_logical * max(1.0, float(dpr)))))
    return max(32, min(256, ((side + 7) // 8) * 8))


def explorer_thumb_decode_px(*, dpr: float = 1.0, card_width: int = EXPLORER_GRID_CARD_WIDTH_PX, list_icon_logical: int = 40) -> int:
    """Max decode bucket for grid + list explorer views on the same loader."""
    return max(
        explorer_grid_thumb_decode_px(dpr=dpr, card_width=card_width),
        explorer_list_icon_decode_px(dpr=dpr, icon_logical=list_icon_logical),
    )


def peek_direct_file_preview(path: Path, *, size_px: int = 256) -> QPixmap | None:
    """Return a cached explorer preview only — never decodes on the calling thread."""
    if not is_direct_media_preview_path(path):
        return None
    cache = ThumbnailCache(size_px=max(256, int(size_px)), cache_variant=EXPLORER_PREVIEW_DISK_CACHE_VARIANT)
    return cache.peek_thumbnail_pixmap(path)


def decode_explorer_preview_qimage_worker(
    file_path: str,
    size_px: int,
    *,
    cache_variant: str = EXPLORER_PREVIEW_DISK_CACHE_VARIANT,
) -> tuple[str, QImage] | None:
    """Decode image/video preview in a worker thread (Inbox / Project Guide explorer)."""
    from PySide6.QtGui import QImageReader

    p = Path(file_path)
    if not is_direct_media_preview_path(p):
        return None
    try:
        stat = p.stat()
    except OSError:
        return None
    mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
    side = clamp_decode_side_for_media(size_px, p)
    try:
        key = str(p.resolve())
    except OSError:
        key = str(p)
    dc_path = _disk_cache_path(p, mtime_ns, side, variant=cache_variant)
    try:
        cached = _read_disk_cache_qimage(dc_path)
        if cached is not None and not cached.isNull():
            return (key, cached)
    except OSError:
        pass

    ext = (p.suffix or "").strip().lower()
    img: QImage | None = None
    if ext in _VIDEO_EXTENSIONS:
        pix = _load_video_frame_via_ffmpeg(p, side)
        if pix is not None and not pix.isNull():
            img = pix.toImage()
    elif ext in _PLATE_THUMB_EXTENSIONS:
        from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

        decoded = load_preview_frame_qimage(p, side)
        img = decoded if decoded is not None and not decoded.isNull() else None
    else:
        img = QImage(str(p))
        if img.isNull():
            reader = QImageReader(str(p))
            reader.setAutoTransform(True)
            img = reader.read()

    if img is None or img.isNull():
        return None

    scaled = _downscale_image_max_side(img, side)
    try:
        _write_disk_cache_qimage(dc_path, scaled)
    except OSError:
        pass
    return (key, scaled)


def _load_ref_preview_image_worker(file_path: str, size_px: int) -> tuple[str, QImage] | None:
    """Decode ref/concept preview as a square cover crop at ``size_px`` (worker thread)."""
    from PySide6.QtGui import QImageReader

    p = Path(file_path)
    if not p.is_file():
        return None
    try:
        stat = p.stat()
    except OSError:
        return None
    mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
    side = max(1, int(size_px))
    try:
        dc_path = _disk_cache_path(p, mtime_ns, side, variant=REF_PREVIEW_DISK_CACHE_VARIANT)
        cached = _read_disk_cache_qimage(dc_path)
        if cached is not None and not cached.isNull():
            return (str(p), cached)
    except OSError:
        pass
    try:
        ext = (p.suffix or "").strip().lower()
        if ext in _PLATE_THUMB_EXTENSIONS:
            from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

            img = load_preview_frame_qimage(p, side)
            if img is None or img.isNull():
                return None
        else:
            img = QImage(str(p))
            if img.isNull():
                reader = QImageReader(str(p))
                reader.setAutoTransform(True)
                img = reader.read()
            if img.isNull():
                return None
        sq = _qimage_cover_square(img, side)
        try:
            _write_disk_cache_qimage(dc_path, sq)
        except OSError:
            pass
        return (str(p), sq)
    except Exception as e:
        logger.warning("Ref preview load failed %s: %s", file_path, e)
        return None


def decode_ref_preview_qimage_worker(file_path: str, size_px: int) -> tuple[str, QImage] | None:
    """
    Worker-safe decode for entity reference/concept preview images.
    Square center-crop at ``size_px``; disk cache variant ``ref_cover``.
    """
    return _load_ref_preview_image_worker(file_path, size_px)


def _load_thumbnail_image_worker(file_path: str, size_px: int, cache_key: str | None = None) -> tuple[str, QImage] | None:
    """
    Run in worker thread: load file, decode to QImage, scale.
    Returns (cache_key, QImage). cache_key is provided explicitly or derived from parent path.
    """
    from PySide6.QtCore import Qt
    p = Path(file_path)
    if not p.is_file():
        return None
    try:
        ext = (p.suffix or "").strip().lower()
        if ext in _PLATE_THUMB_EXTENSIONS:
            from monostudio.ui_qt.sequence_preview_decode import load_preview_frame_qimage

            img = load_preview_frame_qimage(p, size_px) or QImage()
        else:
            img = QImage(str(p))
            if img.isNull():
                reader = QImageReader(str(p))
                reader.setAutoTransform(True)
                img = reader.read()
        if img.isNull():
            return None
        scaled = img.scaled(
            size_px,
            size_px,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        key = cache_key if cache_key else str(p.parent)
        return (key, scaled)
    except Exception as e:
        logger.warning("Thumbnail load failed %s: %s", file_path, e)
        return None


class ThumbnailManager(QObject):
    """
    Central async thumbnail loading and caching. Long-lived (app lifetime).
    - Memory cache (LRU). Never blocks UI. Schedules load via WorkerManager.
    - On load success notifies AppState so UI repaints via thumbnailsChanged.
    """

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        app_state: "AppState",
        worker_manager: "WorkerManager",
        size_px: int = DEFAULT_THUMB_SIZE_PX,
        max_memory: int = DEFAULT_MEMORY_CACHE_MAX,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._worker_manager = worker_manager
        self._size_px = size_px
        self._max_memory = max(1, max_memory)
        self._settings = settings
        # cache_key -> (pixmap, source fingerprint, source path str)
        self._cache: OrderedDict[str, tuple[QPixmap, str, str]] = OrderedDict()
        self._pending: set[str] = set()
        # cache_key -> resolved source path while load is in flight (for fingerprint on finish).
        self._pending_source: dict[str, str] = {}
        # Cache keys where resolve_grid_thumbnail_file returned no path, or worker decode failed — avoid prefetch spam.
        self._no_path_keys: set[str] = set()
        self._connect_worker()

    def _connect_worker(self) -> None:
        from monostudio.ui_qt.worker_manager import WorkerManager
        if isinstance(self._worker_manager, WorkerManager):
            self._worker_manager.taskFinished.connect(self._on_task_finished)

    def request_thumbnail(
        self,
        asset_id: str,
        department: str | None = None,
        *,
        pipeline_ref: "Asset | Shot | None" = None,
        active_dcc_id: str | None = None,
    ) -> QPixmap | None:
        """
        Return pixmap from memory cache if present; else return None (caller shows placeholder)
        and schedule async load if not already pending. Duplicate requests coalesced.
        When department is given, looks for department-specific thumb first (fallback to entity).
        With settings + pipeline_ref, thumbnail source mode (user / render sequence / …) applies.
        """
        if not asset_id or not str(asset_id).strip():
            return None
        from monostudio.core.models import Asset, Shot
        from monostudio.ui_qt.inspector_preview_settings import read_inspector_thumbnail_source

        if isinstance(pipeline_ref, Asset):
            mode = read_inspector_thumbnail_source(self._settings, entity="asset")
        elif isinstance(pipeline_ref, Shot):
            mode = read_inspector_thumbnail_source(self._settings, entity="shot")
        else:
            mode = None
        adc_sig: str | None = None
        if isinstance(pipeline_ref, (Asset, Shot)):
            adc_sig = active_dcc_segment_for_thumbnail_cache(active_dcc_id)
        cache_key = make_department_cache_key(
            str(asset_id).strip(),
            department,
            thumb_active_dcc_sig=adc_sig,
            thumb_source_mode=mode,
            thumb_scan_rules_sig=self._thumbnail_scan_rules_signature(),
        )
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if isinstance(entry, tuple) and len(entry) >= 2:
                pix = entry[0]
                cached_fp = str(entry[1] or "")
                cached_src = str(entry[2]) if len(entry) >= 3 else ""
            else:
                pix, cached_fp, cached_src = entry, "", ""  # type: ignore[misc]
            # Fast path: re-stat the same source file (no sequence resolve).
            if cached_src and cached_fp:
                live_fp = thumb_source_fingerprint(Path(cached_src))
                if live_fp == cached_fp:
                    self._cache.move_to_end(cache_key)
                    try:
                        from monostudio.ui_qt.stress_profiler import enabled, record_thumbnail_hit
                        if enabled():
                            record_thumbnail_hit()
                    except Exception:
                        pass
                    return pix
            # Source mtime changed or path missing — full resolve (may pick a new representative frame).
            live_fp = self._live_source_fingerprint(
                str(asset_id).strip(),
                department,
                pipeline_ref=pipeline_ref,
                active_dcc_id=active_dcc_id,
            )
            if cached_fp and live_fp == cached_fp:
                self._cache.move_to_end(cache_key)
                try:
                    from monostudio.ui_qt.stress_profiler import enabled, record_thumbnail_hit
                    if enabled():
                        record_thumbnail_hit()
                except Exception:
                    pass
                return pix
            # Source changed on disk — drop and reload.
            self._cache.pop(cache_key, None)
            self._no_path_keys.discard(cache_key)
        if cache_key in self._no_path_keys:
            # Re-check: a source may have appeared since we recorded a miss.
            live_fp = self._live_source_fingerprint(
                str(asset_id).strip(),
                department,
                pipeline_ref=pipeline_ref,
                active_dcc_id=active_dcc_id,
            )
            if not live_fp:
                return None
            self._no_path_keys.discard(cache_key)
        try:
            from monostudio.ui_qt.stress_profiler import enabled, record_thumbnail_miss
            if enabled():
                record_thumbnail_miss()
        except Exception:
            pass
        if cache_key not in self._pending:
            self._pending.add(cache_key)
            self._schedule_load(
                str(asset_id).strip(),
                department,
                cache_key,
                pipeline_ref=pipeline_ref,
                active_dcc_id=active_dcc_id,
            )
        return None

    def _live_source_fingerprint(
        self,
        entity_path: str,
        department: str | None,
        *,
        pipeline_ref: "Asset | Shot | None" = None,
        active_dcc_id: str | None = None,
    ) -> str:
        from monostudio.core.models import Asset, Shot
        from monostudio.ui_qt.inspector_preview_settings import (
            THUMB_SOURCE_USER_THEN_RENDER,
            read_inspector_thumbnail_source,
        )
        from monostudio.ui_qt.thumbnail_source_resolve import resolve_grid_thumbnail_file

        if isinstance(pipeline_ref, Asset):
            mode = read_inspector_thumbnail_source(self._settings, entity="asset")
        elif isinstance(pipeline_ref, Shot):
            mode = read_inspector_thumbnail_source(self._settings, entity="shot")
        else:
            mode = THUMB_SOURCE_USER_THEN_RENDER
        path = resolve_grid_thumbnail_file(
            Path(entity_path),
            (department or "").strip() or None,
            mode=mode,
            pipeline_ref=pipeline_ref,
            active_dcc_id=active_dcc_id,
            sequence_ignore_extensions=get_thumbnail_sequence_ignore_extensions(self._settings),
            sequence_ignore_name_tokens=get_thumbnail_sequence_ignore_tokens(self._settings),
        )
        return thumb_source_fingerprint(path)

    def _schedule_load(
        self,
        entity_path: str,
        department: str | None,
        cache_key: str,
        *,
        pipeline_ref: "Asset | Shot | None" = None,
        active_dcc_id: str | None = None,
    ) -> None:
        dep = (department or "").strip() or None
        from monostudio.core.models import Asset, Shot
        from monostudio.ui_qt.inspector_preview_settings import (
            THUMB_SOURCE_USER_THEN_RENDER,
            read_inspector_thumbnail_source,
        )
        from monostudio.ui_qt.thumbnail_source_resolve import resolve_grid_thumbnail_file

        seq_ign_ext = get_thumbnail_sequence_ignore_extensions(self._settings)
        seq_ign_tok = get_thumbnail_sequence_ignore_tokens(self._settings)

        if isinstance(pipeline_ref, Asset):
            mode = read_inspector_thumbnail_source(self._settings, entity="asset")
        elif isinstance(pipeline_ref, Shot):
            mode = read_inspector_thumbnail_source(self._settings, entity="shot")
        else:
            mode = THUMB_SOURCE_USER_THEN_RENDER
        path = resolve_grid_thumbnail_file(
            Path(entity_path),
            dep,
            mode=mode,
            pipeline_ref=pipeline_ref,
            active_dcc_id=active_dcc_id,
            sequence_ignore_extensions=seq_ign_ext,
            sequence_ignore_name_tokens=seq_ign_tok,
        )
        if path is None:
            self._pending.discard(cache_key)
            self._pending_source.pop(cache_key, None)
            self._no_path_keys.add(cache_key)
            return
        file_path = str(path)
        self._pending_source[cache_key] = file_path
        size_px = self._size_px
        key = cache_key

        def run() -> object:
            return _load_thumbnail_image_worker(file_path, size_px, cache_key=key)

        from monostudio.ui_qt.worker_manager import WorkerTask
        task = WorkerTask("thumbnail_load", run, manager=self._worker_manager)
        task._schedule_category = f"thumbnail_load:{cache_key}"
        self._worker_manager.submit_task(
            task,
            category=f"thumbnail_load:{cache_key}",
            replace_existing=True,
        )

    def _on_task_finished(self, category: str, result: object, error: str | None) -> None:
        if not category.startswith("thumbnail_load:"):
            return
        cache_key = category.replace("thumbnail_load:", "", 1) if ":" in category else ""
        if error is not None:
            self._pending.discard(cache_key)
            self._pending_source.pop(cache_key, None)
            return
        if result is None:
            self._pending.discard(cache_key)
            self._pending_source.pop(cache_key, None)
            if cache_key:
                self._no_path_keys.add(cache_key)
            return
        pair = result if isinstance(result, tuple) and len(result) == 2 else None
        if pair is None:
            self._pending.discard(cache_key)
            self._pending_source.pop(cache_key, None)
            if cache_key:
                self._no_path_keys.add(cache_key)
            return
        cache_key, qimg = pair
        if not isinstance(cache_key, str) or not isinstance(qimg, QImage) or qimg.isNull():
            ck = cache_key if isinstance(cache_key, str) else ""
            self._pending.discard(ck)
            self._pending_source.pop(ck, None)
            if ck:
                self._no_path_keys.add(ck)
            return
        self._pending.discard(cache_key)
        src = self._pending_source.pop(cache_key, None)
        pix = QPixmap.fromImage(qimg)
        if pix.isNull():
            self._pending.discard(cache_key)
            self._no_path_keys.add(cache_key)
            return
        self._no_path_keys.discard(cache_key)
        fp = thumb_source_fingerprint(Path(src)) if src else ""
        self._cache[cache_key] = (pix, fp, src or "")
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._max_memory:
            self._cache.popitem(last=False)
        entity_path, _ = parse_department_cache_key(cache_key)
        self._app_state.notify_thumbnail_ready([cache_key, entity_path])

    def _keys_for_entity_dep(self, entity_path: str, department: str | None) -> list[str]:
        dep_norm = (department or "").strip() or None
        seen: set[str] = set()
        out: list[str] = []
        for k in set(self._cache.keys()) | self._pending | self._no_path_keys:
            ep, d = parse_department_cache_key(k)
            if ep != entity_path:
                continue
            if dep_norm is None:
                if d is not None:
                    continue
            elif d != dep_norm:
                continue
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def invalidate(self, asset_id: str, department: str | None = None) -> None:
        """Remove from memory cache; allow reload on next request. Emit so UI refreshes."""
        aid = (asset_id or "").strip()
        if not aid:
            return
        keys = self._keys_for_entity_dep(aid, department)
        for cache_key in keys:
            self._cache.pop(cache_key, None)
            self._pending.discard(cache_key)
            self._pending_source.pop(cache_key, None)
            self._no_path_keys.discard(cache_key)
        self._app_state.invalidate_thumbnails(keys + [aid] if keys else [aid])

    def invalidate_entity(self, entity_path: str) -> None:
        """Drop all cached thumbnails for one asset/shot (every department and source mode)."""
        aid = (entity_path or "").strip()
        if not aid:
            return
        keys: list[str] = []
        for cache_key in set(self._cache.keys()) | self._pending | self._no_path_keys:
            ep, _ = parse_department_cache_key(cache_key)
            if ep != aid:
                continue
            keys.append(cache_key)
            self._cache.pop(cache_key, None)
            self._pending.discard(cache_key)
            self._pending_source.pop(cache_key, None)
            self._no_path_keys.discard(cache_key)
        self._app_state.invalidate_thumbnails(keys + [aid] if keys else [aid])

    def clear_memory_cache(self) -> None:
        """Drop all in-memory thumbnails and pending loads (e.g. thumbnail source mode changed)."""
        self._cache.clear()
        self._pending.clear()
        self._pending_source.clear()
        self._no_path_keys.clear()

    def _thumbnail_scan_rules_signature(self) -> str | None:
        """
        Compact signature for settings-driven scan rules affecting sequence thumbnails.
        Used in cache key so changing rules triggers reload without manual invalidate.
        """
        if self._settings is None:
            return None
        ext = sorted(get_thumbnail_sequence_ignore_extensions(self._settings))
        tok = sorted(t.casefold() for t in get_thumbnail_sequence_ignore_tokens(self._settings))
        if not ext and not tok:
            return None
        # Keep stable + short; human-readable is fine (not security-sensitive).
        return f"e={';'.join(ext)}|t={';'.join(tok)}"

