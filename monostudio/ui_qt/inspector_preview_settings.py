"""QSettings keys for Inspector preview (per-entity thumbnail source + sequence FPS).

Thumbnail source is stored separately for Assets and Shots
(``inspector/thumbnail_source_asset`` / ``inspector/thumbnail_source_shot``).
Legacy ``inspector/thumbnail_source`` is read as fallback when the new keys are unset.
"""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QSettings

SETTINGS_ORG = "MonoStudio26"
SETTINGS_APP = "MonoStudio26"

# Per-entity thumbnail source (grid + Inspector for Asset / Shot).
KEY_THUMBNAIL_SOURCE_ASSET = "inspector/thumbnail_source_asset"
KEY_THUMBNAIL_SOURCE_SHOT = "inspector/thumbnail_source_shot"
# Legacy single key — used as fallback when per-entity keys are unset (migration).
KEY_THUMBNAIL_SOURCE_LEGACY = "inspector/thumbnail_source"

KEY_SEQUENCE_PREVIEW_FPS = "inspector/sequence_preview_fps"
KEY_THUMBNAIL_OPEN_EXE = "inspector/thumbnail_open_exe"

THUMB_SOURCE_USER = "user"
THUMB_SOURCE_RENDER_SEQUENCE = "render_sequence"
THUMB_SOURCE_USER_THEN_RENDER = "user_then_render"

_VALID_SOURCES = frozenset({THUMB_SOURCE_USER, THUMB_SOURCE_RENDER_SEQUENCE, THUMB_SOURCE_USER_THEN_RENDER})

ThumbnailSourceEntity = Literal["asset", "shot"]


def default_qsettings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def _normalize_thumb_source_value(v: object) -> str | None:
    if isinstance(v, str) and v in _VALID_SOURCES:
        return v
    if isinstance(v, str):
        s = v.strip()
        if s in _VALID_SOURCES:
            return s
    return None


def read_inspector_thumbnail_source(
    settings: QSettings | None,
    *,
    entity: ThumbnailSourceEntity,
) -> str:
    """Read thumbnail source mode for assets or shots (separate settings)."""
    if settings is None:
        return THUMB_SOURCE_USER_THEN_RENDER
    key = KEY_THUMBNAIL_SOURCE_ASSET if entity == "asset" else KEY_THUMBNAIL_SOURCE_SHOT
    got = _normalize_thumb_source_value(settings.value(key, None))
    if got is not None:
        return got
    leg = _normalize_thumb_source_value(settings.value(KEY_THUMBNAIL_SOURCE_LEGACY, None))
    if leg is not None:
        return leg
    return THUMB_SOURCE_USER_THEN_RENDER


def read_sequence_preview_fps(settings: QSettings | None) -> int:
    default_fps = 30
    if settings is None:
        return default_fps
    v = settings.value(KEY_SEQUENCE_PREVIEW_FPS, default_fps)
    try:
        n = int(v) if not isinstance(v, int) else v
    except (TypeError, ValueError):
        return default_fps
    return max(1, min(60, n))


def write_inspector_thumbnail_source(
    settings: QSettings,
    value: str,
    *,
    entity: ThumbnailSourceEntity,
) -> None:
    if value not in _VALID_SOURCES:
        return
    key = KEY_THUMBNAIL_SOURCE_ASSET if entity == "asset" else KEY_THUMBNAIL_SOURCE_SHOT
    settings.setValue(key, value)


def write_sequence_preview_fps(settings: QSettings, fps: int) -> None:
    settings.setValue(KEY_SEQUENCE_PREVIEW_FPS, max(1, min(60, int(fps))))


def read_inspector_thumbnail_open_exe(settings: QSettings | None) -> str:
    """Path to .exe used to open thumbnail image files; empty = OS default handler."""
    if settings is None:
        return ""
    v = settings.value(KEY_THUMBNAIL_OPEN_EXE, "")
    if isinstance(v, str):
        return v.strip()
    return str(v).strip() if v is not None else ""


def write_inspector_thumbnail_open_exe(settings: QSettings, path: str) -> None:
    settings.setValue(KEY_THUMBNAIL_OPEN_EXE, (path or "").strip())
