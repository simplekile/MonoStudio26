"""
Single toast widget for the MonoStudio26 notification system.
Renders icon + short message, supports fade/slide animation via QPropertyAnimation.
"""

from __future__ import annotations

from typing import Callable, Literal

from PySide6.QtCore import QPoint, QPropertyAnimation, QEasingCurve, Qt, QTimer, QParallelAnimationGroup
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QGraphicsOpacityEffect, QSizePolicy, QPushButton

from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.lucide_icons import lucide_icon


ToastType = Literal["info", "success", "warning", "error", "important"]

# Duration (ms) per type; error/important also allow manual close
DURATION_MS = {
    "info": 1800,
    "success": 2200,
    "warning": 3000,
    "error": 4000,
    # Important = sticky: only closes when user clicks the close icon
    "important": 0,
}

# Colors (MONOS dark theme)
TOAST_COLORS: dict[ToastType, tuple[str, str]] = {
    "info": (MONOS_COLORS["blue_600"], MONOS_COLORS["text_label"]),
    "success": (MONOS_COLORS["emerald_500"], MONOS_COLORS["text_primary"]),
    "warning": (MONOS_COLORS["amber_500"], MONOS_COLORS["text_primary"]),
    "error": (MONOS_COLORS["red_500"], MONOS_COLORS["text_primary"]),
    "important": (MONOS_COLORS["blue_400"], MONOS_COLORS["text_primary_selected"]),
}

# Lucide icon names per type (fallback: no icon if file missing)
TOAST_ICONS: dict[ToastType, str] = {
    "info": "message-circle",
    "success": "square-check",
    "warning": "zap",
    "error": "x",
    "important": "info",
}


class ToastWidget(QFrame):
    """
    One toast: icon + message, rounded panel, optional close for error.
    Animates in (fade + slide up) and out (fade).
    """

    def __init__(
        self,
        message: str,
        toast_type: ToastType,
        duration_ms: int,
        parent: QFrame | None = None,
        *,
        on_dismiss: Callable[["ToastWidget"], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._toast_type = toast_type
        self._duration_ms = duration_ms
        self._entered_y: int = 0  # target Y for layout
        self._on_dismiss = on_dismiss

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        accent_hex, text_hex = TOAST_COLORS[toast_type]
        self._accent_color = QColor(accent_hex)
        self._text_color = QColor(text_hex)

        # Rounded panel: nổi bật hơn — nền sáng, viền trái accent, viền tổng thể rõ
        self.setObjectName("ToastWidget")
        self.setStyleSheet(
            f"""
            ToastWidget {{
                background-color: #1c1c1f;
                border: 1px solid rgba(255, 255, 255, 0.22);
                border-left: 4px solid {accent_hex};
                border-radius: 8px;
            }}
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        icon_name = TOAST_ICONS.get(toast_type, "message-circle")
        icon = lucide_icon(icon_name, size=18, color_hex=accent_hex)
        icon_label = QLabel(self)
        icon_label.setPixmap(icon.pixmap(18, 18))
        icon_label.setScaledContents(False)
        layout.addWidget(icon_label)

        msg_label = QLabel(message, self)
        msg_label.setFont(monos_font(point_size=13, weight=QFont.Weight.Medium))
        msg_label.setStyleSheet(f"color: {text_hex}; background: transparent; border: none;")
        msg_label.setWordWrap(False)
        layout.addWidget(msg_label, 1)

        self._close_btn: QPushButton | None = None
        # Show explicit close button for error and important toasts,
        # or whenever duration is non-positive (sticky toast).
        if toast_type in ("error", "important") or duration_ms <= 0:
            close_icon = lucide_icon("x", size=14, color_hex=MONOS_COLORS["text_meta"])
            self._close_btn = QPushButton(self)
            self._close_btn.setIcon(close_icon)
            self._close_btn.setFlat(True)
            self._close_btn.setFixedSize(24, 24)
            self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._close_btn.setStyleSheet(
                "background: transparent; border: none;"
                "border-radius: 4px;"
            )
            self._close_btn.clicked.connect(self._on_close_clicked)
            layout.addWidget(self._close_btn, 0)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._enter_group: QParallelAnimationGroup | None = None
        self._exit_anim: QPropertyAnimation | None = None
        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self.dismiss)

    def set_entered_y(self, y: int) -> None:
        self._entered_y = y

    def start_enter_animation(self) -> None:
        """Fade in + slide up (~10px)."""
        start_y = self._entered_y + 10
        self.move(self.x(), start_y)

        opacity_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        opacity_anim.setDuration(200)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        pos_anim = QPropertyAnimation(self, b"pos")
        pos_anim.setDuration(200)
        pos_anim.setStartValue(QPoint(self.x(), start_y))
        pos_anim.setEndValue(QPoint(self.x(), self._entered_y))
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._enter_group = QParallelAnimationGroup(self)
        self._enter_group.addAnimation(opacity_anim)
        self._enter_group.addAnimation(pos_anim)
        self._enter_group.start()

        if self._duration_ms > 0:
            self._auto_close_timer.start(self._duration_ms)

    def dismiss(self) -> None:
        """Start exit animation (fade out). Safe to call when already dismissing."""
        if self._exit_anim and self._exit_anim.state() == QPropertyAnimation.State.Running:
            return
        self._auto_close_timer.stop()
        self._exit_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._exit_anim.setDuration(180)
        self._exit_anim.setStartValue(1.0)
        self._exit_anim.setEndValue(0.0)
        self._exit_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._exit_anim.finished.connect(self._on_exit_finished)
        self._exit_anim.start()

    def _on_close_clicked(self) -> None:
        # For explicit user close, remove immediately instead of waiting for fade,
        # to avoid any animation issues keeping the toast around.
        self._auto_close_timer.stop()
        if self._on_dismiss:
            self._on_dismiss(self)
        self.deleteLater()

    def _on_exit_finished(self) -> None:
        if self._on_dismiss:
            self._on_dismiss(self)
        self.deleteLater()

    def mouseReleaseEvent(self, event) -> None:
        # Fallback: allow clicking anywhere on a sticky toast to close it,
        # in case the close button hit area is problematic.
        if event.button() == Qt.MouseButton.LeftButton and (
            self._toast_type in ("error", "important") or self._duration_ms <= 0
        ):
            self._on_close_clicked()
            return
        super().mouseReleaseEvent(event)
