"""QSettings for OCIO display transform in sequence review (v1)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings

from monostudio.core.app_paths import get_app_base_path
from monostudio.ui_qt.inspector_preview_settings import SETTINGS_APP, SETTINGS_ORG

KEY_OCIO_SEQUENCE_ENABLED = "review/ocio_sequence_enabled"
KEY_OCIO_CONFIG_PATH = "review/ocio_config_path"
KEY_OCIO_INPUT_COLORSPACE = "review/ocio_input_colorspace"
KEY_OCIO_DISPLAY = "review/ocio_display"
KEY_OCIO_VIEW = "review/ocio_view"

DEFAULT_INPUT_COLORSPACE = "ACEScg"
DEFAULT_DISPLAY = "sRGB - Display"
DEFAULT_VIEW = "ACES 1.0 - SDR Video"

_BUNDLED_REL = Path("monostudio_data") / "ocio" / "aces_1.3" / "config.ocio"


def default_bundled_ocio_config_path() -> Path:
    return get_app_base_path() / _BUNDLED_REL


@dataclass(frozen=True)
class OcioPreviewState:
    enabled: bool
    config_path: Path | None
    input_colorspace: str
    display: str
    view: str

    def cache_token(self) -> str:
        if not self.enabled or self.config_path is None:
            return "off"
        try:
            mtime = int(self.config_path.stat().st_mtime_ns)
        except OSError:
            mtime = 0
        return "|".join(
            (
                "1",
                str(self.config_path),
                str(mtime),
                self.input_colorspace,
                self.display,
                self.view,
            )
        )


def resolve_ocio_config_path(settings: QSettings | None) -> Path | None:
    """Bundled ACES 1.3 cg-config, optional user override, then ``OCIO`` env."""
    custom = ""
    if settings is not None:
        v = settings.value(KEY_OCIO_CONFIG_PATH, "")
        if isinstance(v, str):
            custom = v.strip()
        elif v is not None:
            custom = str(v).strip()
    if custom:
        p = Path(custom)
        if p.is_file():
            return p.resolve()
    bundled = default_bundled_ocio_config_path()
    if bundled.is_file():
        return bundled.resolve()
    env = (os.environ.get("OCIO") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p.resolve()
    return None


def read_ocio_preview_state(settings: QSettings | None) -> OcioPreviewState:
    enabled = False
    input_cs = DEFAULT_INPUT_COLORSPACE
    display = DEFAULT_DISPLAY
    view = DEFAULT_VIEW
    if settings is not None:
        enabled = bool(settings.value(KEY_OCIO_SEQUENCE_ENABLED, False))
        v = settings.value(KEY_OCIO_INPUT_COLORSPACE, DEFAULT_INPUT_COLORSPACE)
        input_cs = (v if isinstance(v, str) else str(v or DEFAULT_INPUT_COLORSPACE)).strip() or DEFAULT_INPUT_COLORSPACE
        v = settings.value(KEY_OCIO_DISPLAY, DEFAULT_DISPLAY)
        display = (v if isinstance(v, str) else str(v or DEFAULT_DISPLAY)).strip() or DEFAULT_DISPLAY
        v = settings.value(KEY_OCIO_VIEW, DEFAULT_VIEW)
        view = (v if isinstance(v, str) else str(v or DEFAULT_VIEW)).strip() or DEFAULT_VIEW
    cfg = resolve_ocio_config_path(settings) if enabled else None
    if enabled and cfg is None:
        enabled = False
    return OcioPreviewState(
        enabled=enabled,
        config_path=cfg,
        input_colorspace=input_cs,
        display=display,
        view=view,
    )


def ocio_preview_cache_token(settings: QSettings | None = None) -> str:
    if settings is None:
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return read_ocio_preview_state(settings).cache_token()


def write_ocio_sequence_enabled(settings: QSettings, enabled: bool) -> None:
    settings.setValue(KEY_OCIO_SEQUENCE_ENABLED, bool(enabled))


def write_ocio_config_path(settings: QSettings, path: str) -> None:
    settings.setValue(KEY_OCIO_CONFIG_PATH, (path or "").strip())


def write_ocio_colorspace_triplet(
    settings: QSettings,
    *,
    input_colorspace: str,
    display: str,
    view: str,
) -> None:
    settings.setValue(KEY_OCIO_INPUT_COLORSPACE, (input_colorspace or DEFAULT_INPUT_COLORSPACE).strip())
    settings.setValue(KEY_OCIO_DISPLAY, (display or DEFAULT_DISPLAY).strip())
    settings.setValue(KEY_OCIO_VIEW, (view or DEFAULT_VIEW).strip())
