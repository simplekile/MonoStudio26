from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage


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
    - Scales to keep aspect ratio, max one dimension 1024 (no crop, no letterbox).
    - Writes a user-override thumbnail file (does NOT overwrite auto thumbnails)
    - Writes lightweight metadata marker (source + updated_at)
    - Emits a signal for UI refresh
    """

    thumbnailUpdated = Signal(str)  # item_id (string id; call sites decide semantics)

    USER_THUMB_BASENAME = "thumbnail.user"
    MAX_THUMB_SIZE_PX = 1024

    def paste_thumbnail(
        self,
        *,
        item_root: Path,
        kind: ThumbnailKind,
        item_id: str,
        department: str | None = None,
        fmt: ThumbnailFormat = "png",
        jpg_quality: int = 85,
    ) -> ClipboardThumbnailResult:
        """
        Paste from system clipboard into a thumbnail file.
        Image is scaled to keep aspect ratio with max dimension 1024.
        When department is given, writes to .meta/thumb_{dept}.user.{ext}.

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
            raise RuntimeError("Clipboard does not contain an image.")

        img = img.convertToFormat(QImage.Format_RGBA8888)
        if img.isNull():
            raise RuntimeError("Clipboard image is invalid.")

        out = self._scale_to_max(img, self.MAX_THUMB_SIZE_PX)

        dep = (department or "").strip()
        if dep:
            target_path = self._department_thumbnail_path(root, dep, fmt=fmt)
        else:
            target_path = self._user_thumbnail_path(root, fmt=fmt)
        self._write_image_atomic(out=out, target_path=target_path, fmt=fmt, jpg_quality=jpg_quality)
        self._write_thumbnail_metadata(root, source="clipboard", department=dep or None)

        self.thumbnailUpdated.emit(iid)
        return ClipboardThumbnailResult(item_id=iid, kind=kind, thumbnail_path=target_path)

    @staticmethod
    def _scale_to_max(img: QImage, max_px: int) -> QImage:
        """Scale image to keep aspect ratio; longest side = max_px (no upscale)."""
        if max_px <= 0:
            raise RuntimeError("Invalid max thumbnail size.")
        w, h = img.width(), img.height()
        if w <= 0 or h <= 0:
            raise RuntimeError("Invalid image size.")
        scale = min(1.0, max_px / max(w, h))
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        scaled = img.scaled(
            new_w, new_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if scaled.isNull():
            raise RuntimeError("Failed to scale clipboard image.")
        return scaled

    @classmethod
    def _user_thumbnail_path(cls, item_root: Path, *, fmt: ThumbnailFormat) -> Path:
        ext = "png" if fmt == "png" else "jpg"
        return Path(item_root) / f"{cls.USER_THUMB_BASENAME}.{ext}"

    @staticmethod
    def _department_thumbnail_path(item_root: Path, department: str, *, fmt: ThumbnailFormat) -> Path:
        ext = "png" if fmt == "png" else "jpg"
        meta_dir = Path(item_root) / ".meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        return meta_dir / f"thumb_{department}.user.{ext}"

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
    def _write_thumbnail_metadata(item_root: Path, *, source: str, department: str | None = None) -> None:
        """
        Lightweight per-item metadata marker.
        Does NOT store binary data; does NOT duplicate resolution.
        """
        root = Path(item_root)
        meta_dir = root / ".monostudio"
        meta_path = meta_dir / "thumbnail.json"

        payload: dict = {
            "source": str(source),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if department:
            payload["department"] = department

        try:
            from monostudio.core.atomic_write import atomic_write_text
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            atomic_write_text(meta_path, content, encoding="utf-8")
        except (PermissionError, OSError):
            pass

