"""Compact iOS-style toggle — pill track + round thumb."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from monostudio.ui_qt.style import MONOS_COLORS

IOS_SWITCH_W = 32
IOS_SWITCH_H = 18
_IOS_THUMB = 14
_IOS_THUMB_PAD = 2
_IOS_TRACK_OFF = "#52525b"
_IOS_TRACK_OFF_HOVER = "#71717a"
_IOS_ON_COLOR = MONOS_COLORS.get("blue_500", "#3b82f6")
_IOS_THUMB_COLOR = "#fafafa"
_IOS_ANIM_MS = 150


class IosSwitch(QWidget):
    """Compact iOS-style toggle — pill track + round thumb."""

    toggled = Signal(bool)

    def __init__(self, *, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = checked
        self._hover = False
        self._progress = 1.0 if checked else 0.0
        self._anim: QPropertyAnimation | None = None
        self.setFixedSize(IOS_SWITCH_W, IOS_SWITCH_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    toggle_progress = Property(float, _get_progress, _set_progress)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool, *, emit: bool = True) -> None:
        on = bool(on)
        if self._checked == on:
            return
        self._checked = on
        self._animate_to(1.0 if on else 0.0)
        if emit:
            self.toggled.emit(on)

    def _animate_to(self, target: float) -> None:
        if self._anim is not None:
            self._anim.stop()
        anim = QPropertyAnimation(self, b"toggle_progress", self)
        anim.setDuration(_IOS_ANIM_MS)
        anim.setStartValue(self._progress)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._clear_anim)
        self._anim = anim
        anim.start()

    def _clear_anim(self) -> None:
        self._anim = None

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._checked)
            event.accept()
            return
        super().keyPressEvent(event)

    def _track_off_color(self) -> QColor:
        return QColor(_IOS_TRACK_OFF_HOVER if self._hover else _IOS_TRACK_OFF)

    def _track_color(self) -> QColor:
        off = self._track_off_color()
        on = QColor(_IOS_ON_COLOR)
        t = self._progress
        return QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        radius = track.height() / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._track_color())
        p.drawRoundedRect(track, radius, radius)

        left_x = track.left() + _IOS_THUMB_PAD
        right_x = track.right() - _IOS_THUMB_PAD - _IOS_THUMB
        thumb_x = left_x + (right_x - left_x) * self._progress
        thumb = QRectF(thumb_x, track.center().y() - _IOS_THUMB / 2.0, _IOS_THUMB, _IOS_THUMB)
        if self._hover or self.hasFocus():
            glow = QColor(0, 0, 0, 36)
            p.setBrush(glow)
            p.drawEllipse(thumb.adjusted(-0.5, 1.0, 0.5, 2.0))
        p.setBrush(QColor(_IOS_THUMB_COLOR))
        p.drawEllipse(thumb)
        p.end()
