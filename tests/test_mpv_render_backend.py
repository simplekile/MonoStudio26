from PySide6.QtCore import QSettings

from monostudio.core.mpv_resolve import mpv_available
from monostudio.ui_qt.video_player_backend import MpvEmbeddedBackend, MpvRenderBackend, _create_mpv_backend
from monostudio.ui_qt.video_preview_settings import (
    read_review_use_gpu_compositor,
    write_review_use_gpu_compositor,
)


def test_create_mpv_backend_uses_render_when_compositor_on() -> None:
    settings = QSettings("MonoStudio26Test", "MpvBackendFactory")
    settings.clear()
    write_review_use_gpu_compositor(settings, True)
    if not mpv_available(settings):
        return
    backend = _create_mpv_backend(settings)
    assert isinstance(backend, MpvRenderBackend)
    assert backend.name == "mpv_render"


def test_create_mpv_backend_uses_embed_when_compositor_off() -> None:
    settings = QSettings("MonoStudio26Test", "MpvBackendFactoryEmbed")
    settings.clear()
    write_review_use_gpu_compositor(settings, False)
    if not mpv_available(settings):
        return
    backend = _create_mpv_backend(settings)
    assert isinstance(backend, MpvEmbeddedBackend)
    assert backend.name == "mpv"


def test_gpu_compositor_default_on() -> None:
    settings = QSettings("MonoStudio26Test", "MpvBackendDefault")
    settings.clear()
    assert read_review_use_gpu_compositor(settings) is True
