"""Loading UI for project switch — top strip overlay + main-view empty state."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QElapsedTimer, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from monostudio.ui_qt.style import MONOS_COLORS, monos_font

SCANNING_EMPTY_MESSAGE = "Scanning project…"
PREPARING_VIEW_MESSAGE = "Preparing view…"
FINISHING_SETUP_MESSAGE = "Finishing setup…"

_LOADING_EMPTY_PREFIXES = (
    "scanning project",
    "preparing view",
    "finishing setup",
    "loading ",
)

_TRACK_COLOR = QColor("#3f3f46")
_GLOW_COLOR = QColor(37, 99, 235, 72)
_CHUNK_CORE = QColor("#2563eb")
_CHUNK_BRIGHT = QColor("#60a5fa")


def is_scanning_empty_message(message: str | None) -> bool:
    return is_loading_empty_message(message)


def is_loading_empty_message(message: str | None) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    return any(text.startswith(prefix) for prefix in _LOADING_EMPTY_PREFIXES)


class _AnimatedLoadingStrip(QWidget):
    """Track with a sliding shimmer chunk (custom paint, ~60fps)."""

    _HEIGHT = 4
    _TICK_MS = 16
    _LOOP_MS = 1800
    _CHUNK_RATIO = 0.34
    _MIN_CHUNK_PX = 72

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self._HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self.update)

    def start(self) -> None:
        self._elapsed.start()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def _phase(self) -> float:
        if not self._elapsed.isValid():
            return 0.0
        return (self._elapsed.elapsed() % self._LOOP_MS) / float(self._LOOP_MS)

    def paintEvent(self, event) -> None:  # noqa: N802
        w = self.width()
        if w <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = float(self._HEIGHT)

        p.fillRect(0, 0, w, self._HEIGHT, _TRACK_COLOR)

        chunk_w = max(self._MIN_CHUNK_PX, int(w * self._CHUNK_RATIO))
        travel = max(1, w - chunk_w)
        x = travel * self._phase()

        glow_path = QPainterPath()
        glow_path.addRoundedRect(x - 6, -1.5, chunk_w + 12, h + 3, 3, 3)
        p.fillPath(glow_path, _GLOW_COLOR)

        grad = QLinearGradient(x, 0, x + chunk_w, 0)
        grad.setColorAt(0.0, QColor(37, 99, 235, 0))
        grad.setColorAt(0.22, _CHUNK_CORE)
        grad.setColorAt(0.5, _CHUNK_BRIGHT)
        grad.setColorAt(0.78, _CHUNK_CORE)
        grad.setColorAt(1.0, QColor(37, 99, 235, 0))

        chunk_path = QPainterPath()
        chunk_path.addRoundedRect(x, 0.0, chunk_w, h, 2.0, 2.0)
        p.fillPath(chunk_path, grad)
        p.end()


class MainViewLoadingPlaceholder(QWidget):
    """Centered empty state with optional animated loading bar."""

    _STRIP_WIDTH = 300

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            f"background: {MONOS_COLORS['content_bg']}; color: {MONOS_COLORS['text_meta']};"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch(1)

        center = QWidget(self)
        center_l = QVBoxLayout(center)
        center_l.setContentsMargins(0, 0, 0, 0)
        center_l.setSpacing(14)
        center_l.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        strip_host = QWidget(center)
        strip_host.setFixedSize(self._STRIP_WIDTH, _AnimatedLoadingStrip._HEIGHT)
        strip_l = QHBoxLayout(strip_host)
        strip_l.setContentsMargins(0, 0, 0, 0)
        self._strip = _AnimatedLoadingStrip(strip_host)
        strip_l.addWidget(self._strip)

        self._label = QLabel(center)
        self._label.setObjectName("MainViewEmptyLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
        self._label.setStyleSheet(f"color: {MONOS_COLORS['text_meta']}; background: transparent;")

        center_l.addWidget(strip_host, 0, Qt.AlignmentFlag.AlignHCenter)
        center_l.addWidget(self._label, 0, Qt.AlignmentFlag.AlignHCenter)

        root.addWidget(center, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addStretch(1)

        self._strip_host = strip_host
        self.set_content("")

    def set_content(self, text: str, *, loading: bool = False) -> None:
        self._label.setText(text)
        self._strip_host.setVisible(loading)
        if loading:
            self._strip.start()
        else:
            self._strip.stop()

    def tick_animation(self) -> None:
        if self._strip_host.isVisible():
            self._strip.repaint()


class PageLoadingBar(QWidget):
    """Thin animated strip at the top of the content stack (no label — avoids header overlap)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageLoadingBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._resize_filtered = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._strip = _AnimatedLoadingStrip(self)
        root.addWidget(self._strip)
        self.setFixedHeight(_AnimatedLoadingStrip._HEIGHT)
        self.hide()

    def show_loading(self, message: str | None = None) -> None:
        del message  # status text lives in MainView empty state only
        parent = self.parentWidget()
        if parent is not None and not self._resize_filtered:
            parent.installEventFilter(self)
            self._resize_filtered = True
        self._sync_geometry()
        self._strip.start()
        self.show()
        self.raise_()

    def hide_loading(self) -> None:
        self._strip.stop()
        self.hide()

    def tick_animation(self) -> None:
        if self.isVisible():
            self._strip.repaint()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._sync_geometry()
        return False

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), _AnimatedLoadingStrip._HEIGHT)
