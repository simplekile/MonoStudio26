"""
Notification overlay: transparent full-window layer.
- Sidebar toasts (page / department / type): top-left of main view.
- General toasts: bottom-right.
Max visible per stack from settings (default 1).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings, QRect, QPoint
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import (
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QSizePolicy,
)

from monostudio.ui_qt.notification.toast import (
    ToastWidget,
    ToastType,
    DURATION_MS,
)

MARGIN_PX = 24
SETTINGS_KEY_MAX_VISIBLE = "notification/max_visible"
DEFAULT_MAX_VISIBLE = 1


def _get_max_visible() -> int:
    try:
        s = QSettings("MonoStudio26", "MonoStudio26")
        v = s.value(SETTINGS_KEY_MAX_VISIBLE, DEFAULT_MAX_VISIBLE, int)
        return max(1, min(3, int(v) if v is not None else DEFAULT_MAX_VISIBLE))
    except Exception:
        return DEFAULT_MAX_VISIBLE


class NotificationOverlayWidget(QWidget):
    """
    Transparent overlay; sidebar toasts live in main view (top-left of asset/shot area).
    General toasts: bottom-right of window.
    """

    def __init__(self, parent: QMainWindow | None = None, main_view: QWidget | None = None) -> None:
        super().__init__(parent)
        self._main_view = main_view
        self._toasts: list[ToastWidget] = []
        self._sidebar_toasts: list[ToastWidget] = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        self.setPalette(pal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar stack: always child of overlay so it stays on top; position at main view top-left
        self._sidebar_container = QWidget(self)
        self._sidebar_container.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._sidebar_container.setAutoFillBackground(False)
        self._sidebar_container.setStyleSheet("background: transparent;")
        self._sidebar_container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        sidebar_layout = QVBoxLayout(self._sidebar_container)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(8)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._sidebar_container.raise_()

        if main_view is None:
            top_row = QHBoxLayout()
            top_row.setContentsMargins(MARGIN_PX, MARGIN_PX, MARGIN_PX, 0)
            top_row.addWidget(self._sidebar_container, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            top_row.addStretch()
            layout.addLayout(top_row)

        layout.addStretch()

        # General stack: bottom-right
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(MARGIN_PX, 0, MARGIN_PX, MARGIN_PX)
        bottom_row.addStretch()
        self._container = QWidget(self)
        self._container.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._container.setAutoFillBackground(False)
        self._container.setStyleSheet("background: transparent;")
        self._container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        bottom_row.addWidget(self._container, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(bottom_row)

    def show_toast(self, toast_type: ToastType, message: str, *, category: str = "general") -> None:
        is_sidebar = category == "sidebar"
        container = self._sidebar_container if is_sidebar else self._container
        toasts = self._sidebar_toasts if is_sidebar else self._toasts
        max_visible = _get_max_visible()

        while len(toasts) >= max_visible and toasts:
            oldest = toasts.pop(0)
            self._dismiss_toast_out_of_layout(container, oldest)

        duration_ms = DURATION_MS.get(toast_type, DURATION_MS["info"])
        toast = ToastWidget(
            message=message,
            toast_type=toast_type,
            duration_ms=duration_ms,
            parent=container,
            on_dismiss=lambda t, lst=toasts: self._toast_dismissed(t, lst),
        )
        toasts.append(toast)
        container.layout().addWidget(toast, 0, Qt.AlignmentFlag.AlignLeft if is_sidebar else Qt.AlignmentFlag.AlignRight)

        toast.show()
        container.layout().activate()
        target_y = toast.y()
        toast.set_entered_y(target_y)
        toast.start_enter_animation()
        if is_sidebar:
            self._sidebar_container.raise_()
            if self._main_view is not None:
                self._update_sidebar_container_geometry()

    def _update_sidebar_container_geometry(self) -> None:
        """Position sidebar container at top-left of main view (in overlay coords)."""
        if self._main_view is None or not self._main_view.isVisible():
            return
        pt = self.mapFromGlobal(self._main_view.mapToGlobal(QPoint(0, 0)))
        x = pt.x() + MARGIN_PX
        y = pt.y() + MARGIN_PX
        self._sidebar_container.layout().activate()
        sh = self._sidebar_container.sizeHint()
        w = max(200, sh.width() if sh.isValid() else 200)
        h = max(40, sh.height() if sh.isValid() else 40)
        self._sidebar_container.setGeometry(QRect(x, y, w, h))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._main_view is not None:
            self._update_sidebar_container_geometry()

    def _dismiss_toast_out_of_layout(self, container: QWidget, oldest: ToastWidget) -> None:
        """
        Remove toast from layout so the layout can reflow before we add the next one.
        Reparent to overlay and keep same visual position so it can fade out in place.
        Use global coords so it works when container is inside main_view (different hierarchy).
        """
        container.layout().removeWidget(oldest)
        global_top_left = oldest.mapToGlobal(QPoint(0, 0))
        pos_in_overlay = self.mapFromGlobal(global_top_left)
        oldest.setParent(self)
        oldest.setGeometry(QRect(pos_in_overlay.x(), pos_in_overlay.y(), oldest.width(), oldest.height()))
        oldest.raise_()
        oldest.dismiss()

    def _toast_dismissed(self, toast: ToastWidget, toasts_list: list) -> None:
        if toast in toasts_list:
            toasts_list.remove(toast)

    def toast_dismissed(self, toast: ToastWidget) -> None:
        """Legacy single-list callback; prefer _toast_dismissed with explicit list."""
        if toast in self._toasts:
            self._toasts.remove(toast)
        if toast in self._sidebar_toasts:
            self._sidebar_toasts.remove(toast)