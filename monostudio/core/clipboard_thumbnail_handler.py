from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter


ThumbnailKind = Literal["asset", "shot"]
ThumbnailFormat = Literal["png", "jpg"]


@dataclass(frozen=True)
class ClipboardThumbnailResult:
    item_id: str
    kind: ThumbnailKind
    thumbnail_path: Path


class ClipboardThumbnailHandler(QObject):
    """
    Explicit, localized clipboard thumbnail override.

    - Reads image from system clipboard (Qt clipboard API)
    - Normalizes to a stable internal format (RGBA8888)
    - Resizes deterministically (asset=512x512, shot=512x288)
    - Writes a user-override thumbnail file (does NOT overwrite auto thumbnails)
    - Writes lightweight metadata marker (source + updated_at)
    - Emits a signal for UI refresh
    """

    thumbnailUpdated = Signal(str)  # item_id (string id; call sites decide semantics)

    USER_THUMB_BASENAME = "thumbnail.user"

    def paste_thumbnail(
        self,
        *,
        item_root: Path,
        kind: ThumbnailKind,
        item_id: str,
        fmt: ThumbnailFormat = "png",
        jpg_quality: int = 85,
        crop_to_fill: bool = True,
    ) -> ClipboardThumbnailResult:
        """
        Paste from system clipboard into a standardized thumbnail file.

        Raises RuntimeError with explicit message on failure.
        """
        root = Path(item_root)
        if not root.is_dir():
            raise RuntimeError(f"Item folder does not exist: {str(root)!r}")
        if kind not in ("asset", "shot"):
            raise RuntimeError(f"Unsupported thumbnail kind: {kind!r}")
        iid = (item_id or "").strip()
        if not iid:
            raise RuntimeError("Item ID is required.")

        cb = QGuiApplication.clipboard()
        if cb is None:
            raise RuntimeError("System clipboard is unavailable.")

        img = cb.image()
        if img.isNull() or img.width() <= 0 or img.height() <= 0:
            # Explicit requirement: raise RuntimeError when clipboard has no image.
            raise RuntimeError("Clipboard does not contain an image.")

        img = img.convertToFormat(QImage.Format_RGBA8888)
        if img.isNull():
            raise RuntimeError("Clipboard image is invalid.")

        target = self._target_size(kind)
        out = self._resize(img=img, target=target, crop_to_fill=crop_to_fill)

        target_path = self._user_thumbnail_path(root, fmt=fmt)
        self._write_image_atomic(out=out, target_path=target_path, fmt=fmt, jpg_quality=jpg_quality)
        self._write_thumbnail_metadata(root, source="clipboard")

        self.thumbnailUpdated.emit(iid)
        return ClipboardThumbnailResult(item_id=iid, kind=kind, thumbnail_path=target_path)

    @staticmethod
    def _target_size(kind: ThumbnailKind) -> QSize:
        if kind == "asset":
            return QSize(512, 512)
        # shot
        return QSize(512, 288)

    @classmethod
    def _user_thumbnail_path(cls, item_root: Path, *, fmt: ThumbnailFormat) -> Path:
        ext = "png" if fmt == "png" else "jpg"
        return Path(item_root) / f"{cls.USER_THUMB_BASENAME}.{ext}"

    @staticmethod
    def _resize(*, img: QImage, target: QSize, crop_to_fill: bool) -> QImage:
        tw = int(target.width())
        th = int(target.height())
        if tw <= 0 or th <= 0:
            raise RuntimeError("Invalid thumbnail target size.")

        mode = Qt.KeepAspectRatioByExpanding if crop_to_fill else Qt.KeepAspectRatio
        scaled = img.scaled(tw, th, mode, Qt.SmoothTransformation)
        if scaled.isNull():
            raise RuntimeError("Failed to resize clipboard image.")

        if not crop_to_fill:
            # Preserve aspect ratio without stretching; keep exact output size
            # by rendering onto a transparent canvas (no prompts / no letterbox color choices).
            canvas = QImage(tw, th, QImage.Format_RGBA8888)
            canvas.fill(Qt.transparent)
            p = QPainter(canvas)
            try:
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                x = int((tw - scaled.width()) // 2)
                y = int((th - scaled.height()) // 2)
                p.drawImage(x, y, scaled)
            finally:
                p.end()
            return canvas

        sx = max(0, (scaled.width() - tw) // 2)
        sy = max(0, (scaled.height() - th) // 2)
        out = scaled.copy(sx, sy, tw, th)
        if out.isNull():
            raise RuntimeError("Failed to crop resized clipboard image.")
        return out

    @staticmethod
    def _write_image_atomic(*, out: QImage, target_path: Path, fmt: ThumbnailFormat, jpg_quality: int) -> None:
        target_path = Path(target_path)
        tmp = target_path.with_suffix(target_path.suffix + ".tmp")
        try:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

            ok: bool
            if fmt == "jpg":
                q = max(1, min(100, int(jpg_quality)))
                ok = out.save(str(tmp), "JPG", q)
            else:
                ok = out.save(str(tmp), "PNG")
            if not ok or not tmp.is_file():
                raise RuntimeError("Failed to write thumbnail image (write returned false).")

            os.replace(str(tmp), str(target_path))
        except PermissionError as e:
            raise RuntimeError(f"Write permission denied for thumbnail: {str(target_path)!r}") from e
        except OSError as e:
            raise RuntimeError(f"Failed to write thumbnail: {str(target_path)!r}") from e
        finally:
            # Cleanup temp file if something went wrong.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def _write_thumbnail_metadata(item_root: Path, *, source: str) -> None:
        """
        Lightweight per-item metadata marker.
        Does NOT store binary data; does NOT duplicate resolution.
        """
        root = Path(item_root)
        meta_dir = root / ".monostudio"
        meta_path = meta_dir / "thumbnail.json"

        payload = {
            "source": str(source),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        try:
            from monostudio.core.atomic_write import atomic_write_text
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            atomic_write_text(meta_path, content, encoding="utf-8")
        except (PermissionError, OSError):
            # Metadata should never block the thumbnail write; fail silently.
            pass

