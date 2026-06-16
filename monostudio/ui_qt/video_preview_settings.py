"""QSettings keys for video preview player."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QSettings, QByteArray

SETTINGS_ORG = "MonoStudio26"
SETTINGS_APP = "MonoStudio26"

KEY_VIDEO_PLAYER_BACKEND = "tools/video_player_backend"
KEY_VIDEO_EXTERNAL_PLAYER_EXE = "tools/video_external_player_exe"
KEY_MPV_DIRECTORY = "tools/mpv_directory"
KEY_VIDEO_PREVIEW_GEOMETRY = "ui/video_preview_geometry"
KEY_VIDEO_PREVIEW_RANGE_PANEL = "ui/video_preview_range_panel_visible"
KEY_SEQUENCE_PREVIEW_GEOMETRY = "ui/sequence_preview_geometry"
KEY_VIDEO_EXPORT_NAMING_MODE = "ui/video_export_naming_mode"
KEY_VIDEO_EXPORT_FORMAT = "ui/video_export_format"
KEY_VIDEO_PREVIEW_PRECISE_SCRUB_DRAG = "ui/video_preview_precise_scrub_drag"
KEY_VIDEO_PREVIEW_TIME_DISPLAY = "ui/video_preview_time_display"
KEY_VIDEO_PREVIEW_PLAYBACK_SPEED = "ui/video_preview_playback_speed"
KEY_VIDEO_PREVIEW_VOLUME = "ui/video_preview_volume"
KEY_VIDEO_PREVIEW_LOOP = "ui/video_preview_loop"
KEY_VIDEO_PREVIEW_PROXY_ENABLED = "ui/video_preview_proxy_enabled"
KEY_VIDEO_PREVIEW_PROXY_SCALE = "ui/video_preview_proxy_scale"

PROXY_SCALE_FULL = 1.0
PROXY_SCALE_HALF = 0.5
PROXY_SCALE_QUARTER = 0.25
PROXY_SCALE_EIGHTH = 0.125
PROXY_SCALE_STEPS = (
    PROXY_SCALE_FULL,
    PROXY_SCALE_HALF,
    PROXY_SCALE_QUARTER,
    PROXY_SCALE_EIGHTH,
)

TIME_DISPLAY_FRAME = "frame"
TIME_DISPLAY_TIMECODE = "timecode"

BACKEND_AUTO = "auto"
BACKEND_MPV = "mpv"
BACKEND_QT = "qt"
BACKEND_EXTERNAL = "external"

EXPORT_NAMING_RANGE = "range_names"
EXPORT_NAMING_SOURCE_INDEX = "source_index"
EXPORT_NAMING_RANGE_INDEX = "range_names_index"

EXPORT_FORMAT_SOURCE = "source"
EXPORT_FORMAT_MP4 = "mp4"
EXPORT_FORMAT_MOV = "mov"
EXPORT_FORMAT_MKV = "mkv"
EXPORT_FORMAT_WEBM = "webm"
EXPORT_FORMAT_GIF = "gif"

ReviewWorkspaceId = Literal["focus", "review", "tools", "theater"]
ReviewToolModeId = Literal["ranges", "markers", "note", "draw"]

VideoPlayerBackendId = Literal["auto", "mpv", "qt", "external"]

_VALID_BACKENDS = frozenset({BACKEND_AUTO, BACKEND_MPV, BACKEND_QT, BACKEND_EXTERNAL})
_VALID_EXPORT_NAMING = frozenset({EXPORT_NAMING_RANGE, EXPORT_NAMING_SOURCE_INDEX, EXPORT_NAMING_RANGE_INDEX})
_VALID_EXPORT_FORMAT = frozenset({
    EXPORT_FORMAT_SOURCE,
    EXPORT_FORMAT_MP4,
    EXPORT_FORMAT_MOV,
    EXPORT_FORMAT_MKV,
    EXPORT_FORMAT_WEBM,
    EXPORT_FORMAT_GIF,
})
_VALID_WORKSPACES = frozenset({"focus", "review", "tools", "theater"})
_VALID_TOOL_MODES = frozenset({"ranges", "markers", "note", "draw"})
_VALID_TIME_DISPLAY = frozenset({TIME_DISPLAY_FRAME, TIME_DISPLAY_TIMECODE})


def default_qsettings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def geometry_key_for_profile(profile: str) -> str:
    p = (profile or "entity").strip().lower()
    if p in ("inbox", "project_guide", "entity"):
        return f"{KEY_VIDEO_PREVIEW_GEOMETRY}/{p}"
    return KEY_VIDEO_PREVIEW_GEOMETRY


def workspace_key_for_profile(profile: str) -> str:
    return f"ui/video_preview_last_workspace/{(profile or 'entity').strip().lower()}"


def tool_mode_key_for_profile(profile: str) -> str:
    return f"ui/video_preview_last_tool_mode/{(profile or 'entity').strip().lower()}"


def read_video_player_backend(settings: QSettings | None) -> str:
    if settings is None:
        return BACKEND_MPV
    v = settings.value(KEY_VIDEO_PLAYER_BACKEND, BACKEND_MPV)
    s = (v or BACKEND_MPV).strip().lower() if isinstance(v, str) else str(v or BACKEND_MPV).strip().lower()
    return s if s in _VALID_BACKENDS else BACKEND_MPV


def write_video_player_backend(settings: QSettings, backend: str) -> None:
    b = (backend or BACKEND_AUTO).strip().lower()
    if b in _VALID_BACKENDS:
        settings.setValue(KEY_VIDEO_PLAYER_BACKEND, b)


def read_video_external_player_exe(settings: QSettings | None) -> str:
    if settings is None:
        return ""
    v = settings.value(KEY_VIDEO_EXTERNAL_PLAYER_EXE, "")
    return (v or "").strip() if isinstance(v, str) else str(v or "").strip()


def write_video_external_player_exe(settings: QSettings, path: str) -> None:
    settings.setValue(KEY_VIDEO_EXTERNAL_PLAYER_EXE, (path or "").strip())


def read_mpv_directory(settings: QSettings | None) -> str:
    if settings is None:
        return ""
    v = settings.value(KEY_MPV_DIRECTORY, "")
    return (v or "").strip() if isinstance(v, str) else str(v or "").strip()


def write_mpv_directory(settings: QSettings, directory: str) -> None:
    d = (directory or "").strip()
    if d:
        settings.setValue(KEY_MPV_DIRECTORY, d)
    else:
        settings.remove(KEY_MPV_DIRECTORY)


def read_video_preview_geometry(settings: QSettings | None, *, profile: str | None = None) -> bytes | None:
    if settings is None:
        return None
    keys = []
    if profile:
        keys.append(geometry_key_for_profile(profile))
    keys.append(KEY_VIDEO_PREVIEW_GEOMETRY)
    for key in keys:
        v = settings.value(key)
        if isinstance(v, QByteArray) and len(v) > 0:
            return bytes(v)
    return None


def write_video_preview_geometry(settings: QSettings, geometry: bytes, *, profile: str | None = None) -> None:
    key = geometry_key_for_profile(profile) if profile else KEY_VIDEO_PREVIEW_GEOMETRY
    settings.setValue(key, QByteArray(geometry))
    settings.setValue(KEY_VIDEO_PREVIEW_GEOMETRY, QByteArray(geometry))


def read_video_preview_range_panel_visible(settings: QSettings | None) -> bool:
    if settings is None:
        return True
    v = settings.value(KEY_VIDEO_PREVIEW_RANGE_PANEL, True)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no")


def write_video_preview_range_panel_visible(settings: QSettings, visible: bool) -> None:
    settings.setValue(KEY_VIDEO_PREVIEW_RANGE_PANEL, bool(visible))


def read_video_export_naming_mode(settings: QSettings | None) -> str:
    if settings is None:
        return EXPORT_NAMING_RANGE
    v = settings.value(KEY_VIDEO_EXPORT_NAMING_MODE, EXPORT_NAMING_RANGE)
    s = (v or EXPORT_NAMING_RANGE).strip().lower() if isinstance(v, str) else str(v or "").strip().lower()
    return s if s in _VALID_EXPORT_NAMING else EXPORT_NAMING_RANGE


def write_video_export_naming_mode(settings: QSettings, mode: str) -> None:
    m = (mode or EXPORT_NAMING_RANGE).strip().lower()
    if m in _VALID_EXPORT_NAMING:
        settings.setValue(KEY_VIDEO_EXPORT_NAMING_MODE, m)


def read_video_export_format(settings: QSettings | None) -> str:
    if settings is None:
        return EXPORT_FORMAT_SOURCE
    v = settings.value(KEY_VIDEO_EXPORT_FORMAT, EXPORT_FORMAT_SOURCE)
    s = (v or EXPORT_FORMAT_SOURCE).strip().lower() if isinstance(v, str) else str(v or "").strip().lower()
    return s if s in _VALID_EXPORT_FORMAT else EXPORT_FORMAT_SOURCE


def write_video_export_format(settings: QSettings, fmt: str) -> None:
    f = (fmt or EXPORT_FORMAT_SOURCE).strip().lower()
    if f in _VALID_EXPORT_FORMAT:
        settings.setValue(KEY_VIDEO_EXPORT_FORMAT, f)


def read_review_workspace(settings: QSettings | None, *, profile: str) -> str:
    if settings is None:
        return "focus"
    v = settings.value(workspace_key_for_profile(profile), "focus")
    s = (v or "focus").strip().lower() if isinstance(v, str) else "focus"
    return s if s in _VALID_WORKSPACES else "focus"


def write_review_workspace(settings: QSettings, profile: str, workspace: str) -> None:
    w = (workspace or "focus").strip().lower()
    if w in _VALID_WORKSPACES:
        settings.setValue(workspace_key_for_profile(profile), w)


def read_review_tool_mode(settings: QSettings | None, *, profile: str) -> str:
    if settings is None:
        return "ranges"
    v = settings.value(tool_mode_key_for_profile(profile), "ranges")
    s = (v or "ranges").strip().lower() if isinstance(v, str) else "ranges"
    return s if s in _VALID_TOOL_MODES else "ranges"


def write_review_tool_mode(settings: QSettings, profile: str, mode: str) -> None:
    m = (mode or "ranges").strip().lower()
    if m in _VALID_TOOL_MODES:
        settings.setValue(tool_mode_key_for_profile(profile), m)


def read_video_preview_precise_scrub_drag(settings: QSettings | None) -> bool:
    if settings is None:
        return False
    v = settings.value(KEY_VIDEO_PREVIEW_PRECISE_SCRUB_DRAG, False)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes")


def write_video_preview_precise_scrub_drag(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_VIDEO_PREVIEW_PRECISE_SCRUB_DRAG, bool(enabled))


def read_video_preview_time_display(settings: QSettings | None) -> str:
    if settings is None:
        return TIME_DISPLAY_TIMECODE
    v = settings.value(KEY_VIDEO_PREVIEW_TIME_DISPLAY, TIME_DISPLAY_TIMECODE)
    s = (v or TIME_DISPLAY_TIMECODE).strip().lower() if isinstance(v, str) else TIME_DISPLAY_TIMECODE
    return s if s in _VALID_TIME_DISPLAY else TIME_DISPLAY_TIMECODE


def write_video_preview_time_display(settings: QSettings, mode: str) -> None:
    m = (mode or TIME_DISPLAY_TIMECODE).strip().lower()
    if m in _VALID_TIME_DISPLAY:
        settings.setValue(KEY_VIDEO_PREVIEW_TIME_DISPLAY, m)


def read_video_preview_playback_speed(settings: QSettings | None) -> float:
    if settings is None:
        return 1.0
    v = settings.value(KEY_VIDEO_PREVIEW_PLAYBACK_SPEED, 1.0)
    try:
        speed = float(v)
    except (TypeError, ValueError):
        return 1.0
    from monostudio.ui_qt.video_player_backend import PLAYBACK_SPEED_STEPS

    if speed in PLAYBACK_SPEED_STEPS:
        return speed
    return min(PLAYBACK_SPEED_STEPS, key=lambda s: abs(s - speed))


def write_video_preview_playback_speed(settings: QSettings, speed: float) -> None:
    from monostudio.ui_qt.video_player_backend import PLAYBACK_SPEED_STEPS

    if speed in PLAYBACK_SPEED_STEPS:
        settings.setValue(KEY_VIDEO_PREVIEW_PLAYBACK_SPEED, speed)


def read_video_preview_volume(settings: QSettings | None) -> int:
    if settings is None:
        return 80
    v = settings.value(KEY_VIDEO_PREVIEW_VOLUME, 80)
    try:
        vol = int(v)
    except (TypeError, ValueError):
        return 80
    return max(0, min(100, vol))


def write_video_preview_volume(settings: QSettings, volume: int) -> None:
    settings.setValue(KEY_VIDEO_PREVIEW_VOLUME, max(0, min(100, int(volume))))


def read_video_preview_loop(settings: QSettings | None) -> bool:
    if settings is None:
        return False
    v = settings.value(KEY_VIDEO_PREVIEW_LOOP, False)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes")


def write_video_preview_loop(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_VIDEO_PREVIEW_LOOP, bool(enabled))


def read_video_preview_proxy_enabled(settings: QSettings | None) -> bool:
    if settings is None:
        return False
    v = settings.value(KEY_VIDEO_PREVIEW_PROXY_ENABLED, False)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes")


def write_video_preview_proxy_enabled(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_VIDEO_PREVIEW_PROXY_ENABLED, bool(enabled))


def read_video_preview_proxy_scale(settings: QSettings | None) -> float:
    if settings is None:
        return PROXY_SCALE_FULL
    v = settings.value(KEY_VIDEO_PREVIEW_PROXY_SCALE, PROXY_SCALE_FULL)
    try:
        scale = float(v)
    except (TypeError, ValueError):
        return PROXY_SCALE_FULL
    if scale in PROXY_SCALE_STEPS:
        return scale
    return PROXY_SCALE_FULL


def write_video_preview_proxy_scale(settings: QSettings, scale: float) -> None:
    s = float(scale)
    if s not in PROXY_SCALE_STEPS:
        s = PROXY_SCALE_FULL
    settings.setValue(KEY_VIDEO_PREVIEW_PROXY_SCALE, s)
