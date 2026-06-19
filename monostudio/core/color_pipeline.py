"""Review color pipeline — OCIO passthrough stub (phase 0).

Future: GPU display transform in ReviewCompositorWidget.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ColorPipelineConfig:
    enabled: bool = False
    ocio_config_path: Path | None = None
    input_colorspace: str = ""
    display_view: str = ""


class ColorPipeline:
    """Passthrough until OCIO GPU processor is wired (plan_review_compositor_ocio_v1)."""

    def __init__(self, config: ColorPipelineConfig | None = None) -> None:
        self._config = config or ColorPipelineConfig()

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    @property
    def config(self) -> ColorPipelineConfig:
        return self._config

    def display_transform_needed(self) -> bool:
        return self.enabled and self._config.ocio_config_path is not None
