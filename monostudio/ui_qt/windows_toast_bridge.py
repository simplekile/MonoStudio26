"""Marshal Windows toast activation onto the Qt main thread (WinRT callbacks are off-thread)."""

from __future__ import annotations

from PySide6.QtCore import QMetaObject, QObject, Qt, Slot
from PySide6.QtWidgets import QMainWindow

from monostudio.core.windows_toast import set_toast_focus_callback


class WindowsToastFocusBridge(QObject):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window

    def request_focus(self) -> None:
        """Safe to call from WinRT / worker threads."""
        QMetaObject.invokeMethod(self, "_focus", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _focus(self) -> None:
        w = self._window
        if w is None:
            return
        w.showNormal()
        w.raise_()
        w.activateWindow()
        top_bar = getattr(w, "_top_bar", None)
        if top_bar is not None and hasattr(top_bar, "open_noti_dropdown"):
            top_bar.open_noti_dropdown()


def install_windows_toast_focus(window: QMainWindow) -> WindowsToastFocusBridge:
    bridge = WindowsToastFocusBridge(window)
    set_toast_focus_callback(bridge.request_focus)
    return bridge
