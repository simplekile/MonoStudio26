"""Pipeline QML theme — sync MONOS_COLORS / dialog tokens into Qt Quick."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlEngine

from monostudio.ui_qt.style import MONOS_COLORS

_PIPELINE_QML_MODULE_DIR = Path(__file__).resolve().parent / "qml" / "Monos" / "Pipeline"
_PIPELINE_QML_IMPORT_ROOT = _PIPELINE_QML_MODULE_DIR.parent.parent


def pipeline_qml_module_dir() -> Path:
    return _PIPELINE_QML_MODULE_DIR


def pipeline_qml_import_root() -> Path:
    return _PIPELINE_QML_IMPORT_ROOT


def build_pipeline_theme_map() -> dict[str, str]:
    """Flat string map for QML context / parity checks. Keys are camelCase."""
    c = MONOS_COLORS
    return {
        "appBg": c["app_bg"],
        "contentBg": c["content_bg"],
        "panel": c["panel"],
        "chromeBg": c["chrome_bg"],
        "cardBg": c["card_bg"],
        "cardHover": c["card_hover"],
        "cardBorder": c["border"],
        "cardSelectedBorder": c["blue_600"],
        "textPrimary": c["text_primary"],
        "textPrimarySelected": c["text_primary_selected"],
        "textLabel": c["text_label"],
        "textMeta": c["text_meta"],
        "blue600": c["blue_600"],
        "blue500": c["blue_500"],
        "blue400": c["blue_400"],
        "emerald500": c["emerald_500"],
        "amber500": c["amber_500"],
        "red500": c["red_500"],
        "waiting": c["waiting"],
        "radiusCard": "12",
        "radiusPill": "8",
        "radiusChip": "4",
        "fontFamily": "Inter",
        "fontMono": "JetBrains Mono",
        "nameSize": "13",
        "metaSize": "11",
        "statusSize": "10",
    }


def configure_pipeline_qml_engine(engine: QQmlEngine) -> None:
    """Register QML import path and expose theme map on root context."""
    root = pipeline_qml_import_root()
    if str(root) not in engine.importPathList():
        engine.addImportPath(str(root))
    ctx = engine.rootContext()
    ctx.setContextProperty("MonosPipelineThemeMap", build_pipeline_theme_map())


def pipeline_harness_qml_url(name: str = "PipelineGridHarness.qml") -> QUrl:
    return QUrl.fromLocalFile(str(_PIPELINE_QML_MODULE_DIR / name))
