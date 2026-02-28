"""
Notification overlay: transparent full-window layer.
- Sidebar toasts (page / department / type): top-left of main view.
- General toasts: below the noti icon (anchor widget) when set, else top-right of window.
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

GENERAL_TOAST_GAP_PX = 8  # gap below noti icon

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
    General toasts: top-right of window.
    """

    def __init__(self, parent: QMainWindow | None = None, main_view: QWidget | None = None) -> None:
        super().__init__(parent)
        self._main_view = main_view
        # Y-position (in overlay coords) to align sidebar toasts; X always based on main_view left.
        self._sidebar_anchor_y: int | None = None
        # When set, general toasts are positioned below this widget (e.g. topbar noti button); else in layout top-right.
        self._general_anchor_widget: QWidget | None = None
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

        # Top row: sidebar toasts (left) + general toasts (right)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(MARGIN_PX, MARGIN_PX, MARGIN_PX, 0)
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
            top_row.addWidget(self._sidebar_container, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        top_row.addStretch()
        # General stack: top-right in layout; if _general_anchor_widget is set later, container is repositioned below it
        self._top_row_layout = top_row
        self._container = QWidget(self)
        self._container.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._container.setAutoFillBackground(False)
        self._container.setStyleSheet("background: transparent;")
        self._container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        top_row.addWidget(self._container, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top_row)

        layout.addStretch()

    def set_general_toast_anchor_widget(self, widget: QWidget | None) -> None:
        """Position general toasts below this widget (e.g. topbar noti button) so they don't overlap."""
        if widget is self._general_anchor_widget:
            return
        self._general_anchor_widget = widget
        if widget is not None:
            self._top_row_layout.removeWidget(self._container)
            self._container.setParent(self)
            self._container.raise_()
            self._update_general_container_geometry()
        else:
            self._top_row_layout.addWidget(self._container, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

    def set_sidebar_anchor_y_from_global(self, global_y: int | None) -> None:
        """
        Optional vertical anchor for sidebar toasts.
        global_y is screen Y coordinate of the related item row (or cursor fallback).
        X position remains aligned to main_view's left edge.
        """
        if global_y is None:
            self._sidebar_anchor_y = None
            return
        # Map Y into overlay coordinates; X is irrelevant here.
        pt = self.mapFromGlobal(QPoint(0, global_y))
        self._sidebar_anchor_y = pt.y()
        self._update_sidebar_container_geometry()

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
        if is_sidebar:
            container.layout().addWidget(toast, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            # General: insert at 0 so newest appears at top (top-right stack)
            container.layout().insertWidget(0, toast, 0, Qt.AlignmentFlag.AlignRight)

        toast.show()
        container.layout().activate()
        target_y = toast.y()
        toast.set_entered_y(target_y)
        toast.start_enter_animation()
        if is_sidebar:
            self._sidebar_container.raise_()
            if self._main_view is not None:
                self._update_sidebar_container_geometry()
        else:
            if self._general_anchor_widget is not None:
                self._update_general_container_geometry()

    def _update_sidebar_container_geometry(self) -> None:
        """
        Position sidebar container.
        - X: always just inside main_view (left + margin), outside sidebar.
        - Y: anchored to provided row if available, else near top of main_view.
        """
        if self._main_view is None:
            return
        main_top_left = self.mapFromGlobal(self._main_view.mapToGlobal(QPoint(0, 0)))
        # X: sát hơn với biên trái main_view (ngoài sidebar) so với MARGIN_PX mặc định.
        x = main_top_left.x() + max(8, MARGIN_PX // 2)

        self._sidebar_container.layout().activate()
        sh = self._sidebar_container.sizeHint()
        w = max(200, sh.width() if sh.isValid() else 200)
        h = max(40, sh.height() if sh.isValid() else 40)

        if self._sidebar_anchor_y is not None:
            # Căn giữa toast theo hàng (anchor_y ~ tâm hàng).
            center_y = max(MARGIN_PX, self._sidebar_anchor_y)
            y = center_y - h // 2
        else:
            y = main_top_left.y() + MARGIN_PX

        self._sidebar_container.setGeometry(QRect(x, y, w, h))

    def _update_general_container_geometry(self) -> None:
        """Position general toast container below the anchor widget (noti icon), right-aligned with it."""
        w = self._general_anchor_widget
        if w is None or not w.isVisible():
            return
        self._container.layout().activate()
        sh = self._container.sizeHint()
        cw = max(280, sh.width() if sh.isValid() else 280)
        ch = max(60, sh.height() if sh.isValid() else 60)
        # Anchor bottom-right in overlay coords
        anchor_br = self.mapFromGlobal(w.mapToGlobal(w.rect().bottomRight()))
        x = anchor_br.x() - cw
        y = anchor_br.y() + GENERAL_TOAST_GAP_PX
        self._container.setGeometry(QRect(x, y, cw, ch))
        self._container.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._main_view is not None:
            self._update_sidebar_container_geometry()
        if self._general_anchor_widget is not None:
            self._update_general_container_geometry()

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