from pathlib import Path

from PySide6.QtCore import QSettings

from monostudio.core.color_pipeline import ColorPipeline, ColorPipelineConfig
from monostudio.ui_qt.video_preview_settings import (
    read_review_use_gpu_compositor,
    read_review_video_draw_overlay_fix,
    write_review_use_gpu_compositor,
    write_review_video_draw_overlay_fix,
)


def test_review_video_draw_overlay_fix_roundtrip() -> None:
    settings = QSettings("MonoStudio26Test", "ReviewCompositor")
    settings.clear()

    write_review_video_draw_overlay_fix(settings, True)
    assert read_review_video_draw_overlay_fix(settings) is True

    write_review_video_draw_overlay_fix(settings, False)
    assert read_review_video_draw_overlay_fix(settings) is False


def test_review_use_gpu_compositor_defaults_off() -> None:
    settings = QSettings("MonoStudio26Test", "ReviewCompositorGpu")
    settings.clear()

    assert read_review_use_gpu_compositor(settings) is False
    write_review_use_gpu_compositor(settings, True)
    assert read_review_use_gpu_compositor(settings) is True


def test_color_pipeline_passthrough() -> None:
    pipe = ColorPipeline()
    assert pipe.enabled is False
    assert pipe.display_transform_needed() is False

    pipe_ocio = ColorPipeline(
        ColorPipelineConfig(enabled=True, ocio_config_path=Path("studio/config.ocio"))
    )
    assert pipe_ocio.display_transform_needed() is True
