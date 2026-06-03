"""Full-size image viewer for inline note images — zoom slider, click-to-zoom, drag-to-pan."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QFont, QMouseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.style import MonosDialog, monos_font

_ZOOM_MIN = 25
_ZOOM_MAX = 400
_ZOOM_DEFAULT = 100
_ZOOM_CLICK_STEP = 25
_PAN_CLICK_THRESHOLD = 6


def _reference_window(parent: QWidget | None) -> QWidget | None:
    seen: set[int] = set()
    candidates: list[QWidget] = []
    if parent is not None:
        w = parent.window()
        if w is not None and id(w) not in seen:
            seen.add(id(w))
            candidates.append(w)
    active = QApplication.activeWindow()
    if active is not None and id(active) not in seen:
        candidates.append(active)
    for w in candidates:
        if w.isVisible() and w.width() >= 480 and w.height() >= 360:
            return w
    return candidates[0] if candidates else None


class NoteImageViewerDialog(MonosDialog):
    """Near full-app image preview with zoom slider, click +25%, drag pan."""

    _WINDOW_FILL = 0.92

    def __init__(self, image_path: Path, *, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image")
        self.setModal(True)
        self.setMinimumSize(520, 380)

        self._source_pix = QPixmap(str(image_path))
        self._zoom_pct = _ZOOM_DEFAULT
        self._pan_active = False
        self._pan_press = QPoint()
        self._scroll_at_press = QPoint()
        self._pan_moved = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(False)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.viewport().setMouseTracking(True)
        self._scroll.viewport().installEventFilter(self)

        self._image_label = QLabel(self._scroll)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setObjectName("NoteImageViewerLabel")
        if self._source_pix.isNull():
            self._image_label.setText("Could not load image.")
            self._image_label.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        self._scroll.setWidget(self._image_label)
        root.addWidget(self._scroll, 1)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(10)
        zoom_caption = QLabel("Zoom", self)
        zoom_caption.setObjectName("DialogHint")
        zoom_caption.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        zoom_row.addWidget(zoom_caption, 0)

        self._zoom_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._zoom_slider.setObjectName("NoteImageViewerZoomSlider")
        self._zoom_slider.setRange(_ZOOM_MIN, _ZOOM_MAX)
        self._zoom_slider.setValue(_ZOOM_DEFAULT)
        self._zoom_slider.setSingleStep(5)
        self._zoom_slider.setPageStep(_ZOOM_CLICK_STEP)
        self._zoom_slider.setEnabled(not self._source_pix.isNull())
        self._zoom_slider.valueChanged.connect(self._on_slider_changed)
        zoom_row.addWidget(self._zoom_slider, 1)

        self._zoom_value = QLabel(f"{_ZOOM_DEFAULT}%", self)
        self._zoom_value.setObjectName("NoteImageViewerZoomValue")
        self._zoom_value.setFont(monos_font("JetBrains Mono", 12, QFont.Weight.Normal))
        self._zoom_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._zoom_value.setFixedWidth(52)
        zoom_row.addWidget(self._zoom_value, 0)
        root.addLayout(zoom_row)

        hint = QLabel("Click image to zoom +25% · drag to pan", self)
        hint.setObjectName("DialogHint")
        hint.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        root.addWidget(hint)

        btn_row = QHBoxLayout()
        fit_btn = QPushButton("Fit", self)
        fit_btn.setObjectName("DialogSecondaryButton")
        fit_btn.setEnabled(not self._source_pix.isNull())
        fit_btn.setToolTip("Reset zoom to fit window")
        fit_btn.clicked.connect(self._reset_zoom_fit)
        btn_row.addWidget(fit_btn, 0)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogSecondaryButton")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._apply_near_app_geometry(parent)
        QTimer.singleShot(0, self._apply_zoom)

    def _apply_near_app_geometry(self, parent: QWidget | None) -> None:
        ref = _reference_window(parent)
        if ref is None:
            self.resize(960, 720)
            return
        g = ref.geometry()
        w = max(self.minimumWidth(), int(g.width() * self._WINDOW_FILL))
        h = max(self.minimumHeight(), int(g.height() * self._WINDOW_FILL))
        self.resize(w, h)
        x = g.x() + max(0, (g.width() - w) // 2)
        y = g.y() + max(0, (g.height() - h) // 2)
        self.move(x, y)

    def _viewport_size(self) -> tuple[int, int]:
        vp = self._scroll.viewport()
        if vp is None:
            return (1, 1)
        return max(1, vp.width()), max(1, vp.height())

    def _fit_scale(self) -> float:
        if self._source_pix.isNull():
            return 1.0
        vw, vh = self._viewport_size()
        sw, sh = self._source_pix.width(), self._source_pix.height()
        if sw <= 0 or sh <= 0:
            return 1.0
        return min(vw / sw, vh / sh)

    def _can_pan(self) -> bool:
        if self._source_pix.isNull():
            return False
        lw, lh = self._image_label.width(), self._image_label.height()
        vw, vh = self._viewport_size()
        return lw > vw or lh > vh

    def _sync_pan_cursor(self) -> None:
        vp = self._scroll.viewport()
        if vp is None:
            return
        if self._pan_active:
            vp.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._can_pan():
            vp.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            vp.setCursor(Qt.CursorShape.ArrowCursor)

    def _set_zoom_pct(self, pct: int, *, update_slider: bool = True) -> None:
        pct = max(_ZOOM_MIN, min(_ZOOM_MAX, int(pct)))
        self._zoom_pct = pct
        self._zoom_value.setText(f"{pct}%")
        if update_slider and self._zoom_slider.value() != pct:
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(pct)
            self._zoom_slider.blockSignals(False)
        self._apply_zoom()

    def _reset_zoom_fit(self) -> None:
        self._set_zoom_pct(_ZOOM_DEFAULT)

    def _bump_zoom(self, delta: int) -> None:
        self._set_zoom_pct(self._zoom_pct + delta)

    def _on_slider_changed(self, value: int) -> None:
        self._set_zoom_pct(value, update_slider=False)

    def _apply_zoom(self) -> None:
        if self._source_pix.isNull():
            return
        vw, vh = self._viewport_size()
        scale = self._fit_scale() * (self._zoom_pct / 100.0)
        tw = max(1, int(round(self._source_pix.width() * scale)))
        th = max(1, int(round(self._source_pix.height() * scale)))
        scaled = self._source_pix.scaled(
            tw,
            th,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())
        self._scroll.setWidget(self._image_label)
        self._sync_pan_cursor()

    def eventFilter(self, obj: object, event: QEvent) -> bool:  # type: ignore[override]
        if (
            obj is self._scroll.viewport()
            and not self._source_pix.isNull()
            and isinstance(event, QMouseEvent)
        ):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._pan_active = True
                self._pan_moved = False
                self._pan_press = event.position().toPoint()
                sb_h = self._scroll.horizontalScrollBar()
                sb_v = self._scroll.verticalScrollBar()
                self._scroll_at_press = QPoint(sb_h.value(), sb_v.value())
                self._sync_pan_cursor()
                event.accept()
                return True
            if et == QEvent.Type.MouseMove and self._pan_active:
                delta = event.position().toPoint() - self._pan_press
                if delta.manhattanLength() >= _PAN_CLICK_THRESHOLD:
                    self._pan_moved = True
                sb_h = self._scroll.horizontalScrollBar()
                sb_v = self._scroll.verticalScrollBar()
                sb_h.setValue(self._scroll_at_press.x() - delta.x())
                sb_v.setValue(self._scroll_at_press.y() - delta.y())
                event.accept()
                return True
            if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self._pan_active:
                    if not self._pan_moved:
                        self._bump_zoom(_ZOOM_CLICK_STEP)
                    self._pan_active = False
                    self._sync_pan_cursor()
                    event.accept()
                    return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_zoom)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_zoom)
