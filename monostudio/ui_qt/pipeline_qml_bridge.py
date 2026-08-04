"""Qt Quick bridge — intents from QML grid to MainView."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot


class PipelineQmlBridge(QObject):
    """Expose grid intents and layout knobs to QML."""

    rowActivated = Signal(int)
    rowContextMenuRequested = Signal(int, float, float)
    cardWidthChanged = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._card_width = 200

    def get_card_width(self) -> int:
        return self._card_width

    def set_card_width(self, width: int) -> None:
        w = max(120, int(width))
        if w == self._card_width:
            return
        self._card_width = w
        self.cardWidthChanged.emit(w)

    cardWidth = Property(int, get_card_width, set_card_width, notify=cardWidthChanged)  # type: ignore[assignment]

    @Slot(int)
    def activateRow(self, row: int) -> None:
        if row >= 0:
            self.rowActivated.emit(row)

    @Slot(int, float, float)
    def requestRowContextMenu(self, row: int, x: float, y: float) -> None:
        if row >= 0:
            self.rowContextMenuRequested.emit(row, x, y)


def read_pipeline_use_qml_grid(settings) -> bool:
    """Feature flag: QML grid instead of Widget QListView (dev / opt-in)."""
    import os

    env = (os.environ.get("MONOS_PIPELINE_USE_QML_GRID") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return bool(settings.value("main_view/use_qml_grid", False, type=bool))
