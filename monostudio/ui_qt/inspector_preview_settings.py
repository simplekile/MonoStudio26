"""QSettings keys for Inspector preview (thumbnail source + sequence playback FPS)."""

from __future__ import annotations

from PySide6.QtCore import QSettings

SETTINGS_ORG = "MonoStudio26"
SETTINGS_APP = "MonoStudio26"

KEY_THUMBNAIL_SOURCE = "inspector/thumbnail_source"
KEY_SEQUENCE_PREVIEW_FPS = "inspector/sequence_preview_fps"
KEY_THUMBNAIL_OPEN_EXE = "inspector/thumbnail_open_exe"

# Values for KEY_THUMBNAIL_SOURCE
THUMB_SOURCE_USER = "user"
THUMB_SOURCE_RENDER_SEQUENCE = "render_sequence"
THUMB_SOURCE_USER_THEN_RENDER = "user_then_render"

_VALID_SOURCES = frozenset({THUMB_SOURCE_USER, THUMB_SOURCE_RENDER_SEQUENCE, THUMB_SOURCE_USER_THEN_RENDER})


def default_qsettings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def read_inspector_thumbnail_source(settings: QSettings | None) -> str:
    if settings is None:
        return THUMB_SOURCE_USER_THEN_RENDER
    v = settings.value(KEY_THUMBNAIL_SOURCE, THUMB_SOURCE_USER_THEN_RENDER)
    if isinstance(v, str) and v in _VALID_SOURCES:
        return v
    if isinstance(v, str):
        s = v.strip()
        if s in _VALID_SOURCES:
            return s
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


def write_inspector_thumbnail_source(settings: QSettings, value: str) -> None:
    if value in _VALID_SOURCES:
        settings.setValue(KEY_THUMBNAIL_SOURCE, value)


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
