"""QQuickWidget host for production Pipeline grid."""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from monostudio.ui_qt.pipeline_presentation_model import PipelinePresentationModel
from monostudio.ui_qt.pipeline_qml_bridge import PipelineQmlBridge
from monostudio.ui_qt.pipeline_qml_theme import configure_pipeline_qml_engine, pipeline_qml_module_dir
from monostudio.ui_qt.pipeline_thumb_image_provider import PipelineThumbImageProvider

_log = logging.getLogger("monostudio.pipeline_qml")


class PipelineGridViewHost(QWidget):
    """Embeds ``PipelineGridHost.qml`` with model + bridge context properties."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PipelineGridViewHost")
        self._model = PipelinePresentationModel(self)
        self._bridge = PipelineQmlBridge(self)
        self._thumb_provider = PipelineThumbImageProvider()
        self._thumb_resolver = None

        self._view = QQuickWidget(self)
        self._view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        engine = self._view.engine()
        configure_pipeline_qml_engine(engine)
        engine.addImageProvider("thumb", self._thumb_provider)
        ctx = engine.rootContext()
        ctx.setContextProperty("pipelineModel", self._model)
        ctx.setContextProperty("pipelineBridge", self._bridge)

        url = QUrl.fromLocalFile(str(pipeline_qml_module_dir() / "PipelineGridHost.qml"))
        self._view.setSource(url)
        if self._view.status() != QQuickWidget.Status.Ready:
            for err in self._view.errors():
                _log.warning("Pipeline QML load: %s", err.toString())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

    @property
    def presentation_model(self) -> PipelinePresentationModel:
        return self._model

    @property
    def bridge(self) -> PipelineQmlBridge:
        return self._bridge

    def set_thumb_resolver(self, resolver) -> None:
        self._thumb_resolver = resolver
        self._thumb_provider.set_resolver(resolver)

    def resolve_thumb_pixmap(self, token: str) -> QPixmap | None:
        if self._thumb_resolver is None:
            return None
        try:
            result = self._thumb_resolver(token)
        except Exception:
            return None
        return result if isinstance(result, QPixmap) else None

    def set_card_width(self, width: int) -> None:
        self._bridge.set_card_width(width)

    def is_ready(self) -> bool:
        return self._view.status() == QQuickWidget.Status.Ready
