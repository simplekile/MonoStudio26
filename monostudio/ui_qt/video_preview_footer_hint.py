"""Blender-style footer shortcut hints — content-width keycaps (min square) + mouse icons."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from monostudio.ui_qt.style import monos_font

_CAP_PX = 24
_CAP_PAD_X = 6
_ICON_PX = 16
_CAP_GAP = 4
_KEYS_ACTION_GAP = 6
_GROUP_GAP = 20
_BORDER_W = 2
_CAP_RADIUS = 6


class _CapKind(Enum):
    KEY = auto()
    MOUSE_LEFT = auto()
    MOUSE_RIGHT = auto()
    WHEEL = auto()


@dataclass(frozen=True)
class _CapSpec:
    kind: _CapKind
    label: str = ""


def _mouse_svg(*, button: str) -> str:
    stroke = "#52525b"
    fill = "#18181b"
    hi = "#d4d4d8"
    if button == "left":
        left_fill, right_fill = hi, fill
    else:
        left_fill, right_fill = fill, hi
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'>"
        f"<rect x='3' y='1.5' width='10' height='13' rx='5' fill='{fill}' stroke='{stroke}'/>"
        f"<path d='M8 1.5v6' stroke='{stroke}'/>"
        f"<path d='M3 7.5h10' stroke='{stroke}' opacity='0.35'/>"
        f"<path d='M3 1.5c0 0 2.2 0 5 0v6H3z' fill='{left_fill}' opacity='0.7'/>"
        f"<path d='M8 1.5c2.8 0 5 0 5 0v6H8z' fill='{right_fill}' opacity='0.7'/>"
        "</svg>"
    )


def _wheel_svg() -> str:
    stroke = "#52525b"
    fill = "#18181b"
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'>"
        f"<rect x='4' y='2' width='8' height='12' rx='4' fill='{fill}' stroke='{stroke}'/>"
        f"<rect x='7' y='4' width='2' height='4' rx='1' fill='#d4d4d8'/>"
        "</svg>"
    )


def _pixmap_from_svg(svg: str, *, size: int = _ICON_PX) -> QPixmap:
    pm = QPixmap()
    pm.loadFromData(svg.encode("utf-8"))
    if pm.isNull():
        return QPixmap(size, size)
    return pm.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


_MOUSE_LEFT_PM: QPixmap | None = None
_MOUSE_RIGHT_PM: QPixmap | None = None
_WHEEL_PM: QPixmap | None = None


def _ensure_pixmaps() -> None:
    """Lazy QPixmap init (must happen after QGuiApplication exists)."""
    global _MOUSE_LEFT_PM, _MOUSE_RIGHT_PM, _WHEEL_PM
    if _MOUSE_LEFT_PM is not None and _MOUSE_RIGHT_PM is not None and _WHEEL_PM is not None:
        return
    if QGuiApplication.instance() is None:
        return
    _MOUSE_LEFT_PM = _pixmap_from_svg(_mouse_svg(button="left"))
    _MOUSE_RIGHT_PM = _pixmap_from_svg(_mouse_svg(button="right"))
    _WHEEL_PM = _pixmap_from_svg(_wheel_svg())


def _pixmap_for_kind(kind: _CapKind) -> QPixmap | None:
    _ensure_pixmaps()
    if kind == _CapKind.MOUSE_LEFT:
        return _MOUSE_LEFT_PM
    if kind == _CapKind.MOUSE_RIGHT:
        return _MOUSE_RIGHT_PM
    if kind == _CapKind.WHEEL:
        return _WHEEL_PM
    return None


def _token_cap_specs(token: str) -> list[_CapSpec]:
    t = token.strip()
    if t == "LMB":
        return [_CapSpec(_CapKind.MOUSE_LEFT)]
    if t == "RMB":
        return [_CapSpec(_CapKind.MOUSE_RIGHT)]
    if t == "Wheel":
        return [_CapSpec(_CapKind.WHEEL)]
    if t == "Alt+Wheel":
        return [_CapSpec(_CapKind.KEY, "⎇"), _CapSpec(_CapKind.WHEEL)]
    if t == "I/O":
        return [_CapSpec(_CapKind.KEY, "I"), _CapSpec(_CapKind.KEY, "O")]
    if t == "[ / ]":
        return [_CapSpec(_CapKind.KEY, "["), _CapSpec(_CapKind.KEY, "]")]
    if t == ", / .":
        return [_CapSpec(_CapKind.KEY, ","), _CapSpec(_CapKind.KEY, ".")]
    key_map = {
        "Enter": "⏎",
        "Esc": "⎋",
        "Space": "␣",
        "Del": "⌫",
        "Alt": "⎇",
        "Drag": "↔",
        "Dbl-click": "2×",
        "Dbl-click outside": "2×",
        "Ctrl": "⌃",
    }
    if t == "Ctrl+Z":
        return [_CapSpec(_CapKind.KEY, "⌃"), _CapSpec(_CapKind.KEY, "Z")]
    if t in key_map:
        return [_CapSpec(_CapKind.KEY, key_map[t])]
    return [_CapSpec(_CapKind.KEY, t)]


def _cap_width_for_spec(spec: _CapSpec, font: QFont) -> int:
    if spec.kind in (_CapKind.MOUSE_LEFT, _CapKind.MOUSE_RIGHT, _CapKind.WHEEL):
        return _CAP_PX
    if spec.label == "␣":
        return max(_CAP_PX, 20)
    fm = QFontMetrics(font)
    text_w = fm.horizontalAdvance(spec.label)
    return max(_CAP_PX, text_w + _CAP_PAD_X * 2)


class _HintCap(QWidget):
    """Keycap — width follows label; height fixed; min 1:1 aspect."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewHintCap")
        self.setFixedHeight(_CAP_PX)
        self._spec: _CapSpec | None = None
        self._font = monos_font("JetBrains Mono", 11, QFont.Weight.Bold)

    def set_spec(self, spec: _CapSpec) -> None:
        self._spec = spec
        self.setFixedWidth(_cap_width_for_spec(spec, self._font))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        half = _BORDER_W / 2.0
        rect = QRectF(half, half, self.width() - _BORDER_W, self.height() - _BORDER_W)
        painter.setPen(QPen(QColor(255, 255, 255, 56), _BORDER_W))
        painter.setBrush(QColor(255, 255, 255, 15))
        painter.drawRoundedRect(rect, _CAP_RADIUS, _CAP_RADIUS)

        spec = self._spec
        if spec is None:
            return

        if spec.kind in (_CapKind.MOUSE_LEFT, _CapKind.MOUSE_RIGHT, _CapKind.WHEEL):
            pm = _pixmap_for_kind(spec.kind)
            if pm is not None and not pm.isNull():
                x = (self.width() - pm.width()) // 2
                y = (self.height() - pm.height()) // 2
                painter.drawPixmap(x, y, pm)
            return

        if spec.label == "␣":
            painter.setPen(QPen(QColor("#e4e4e7"), 2))
            mid_y = self.height() / 2.0
            painter.drawLine(int(5), int(mid_y), int(self.width() - 5), int(mid_y))
            return

        painter.setFont(self._font)
        painter.setPen(QColor("#e4e4e7"))
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), spec.label)


class _HintGroup(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewHintGroup")
        self.setFixedHeight(_CAP_PX)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(_CAP_GAP)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_plain(self, text: str) -> None:
        self._clear()
        lab = QLabel(text, self)
        lab.setObjectName("VideoPreviewHintPlain")
        lab.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        self._lay.addWidget(lab)

    def set_shortcut(self, token: str, action: str) -> None:
        self._clear()
        for spec in _token_cap_specs(token):
            cap = _HintCap(self)
            cap.set_spec(spec)
            self._lay.addWidget(cap)
        self._lay.addSpacing(_KEYS_ACTION_GAP)
        act = QLabel(action, self)
        act.setObjectName("VideoPreviewHintAction")
        act.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        act.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._lay.addWidget(act)

    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()


class VideoPreviewFooterHintBar(QWidget):
    """Horizontal row of shortcut hint groups (content-sized caps, stable layout)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoPreviewFooterHintBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(_CAP_PX)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(_GROUP_GAP)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_parts(self, parts: list[str]) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for part in parts:
            p = (part or "").strip()
            if not p:
                continue
            group = _HintGroup(self)
            if " — " in p:
                token, action = (s.strip() for s in p.split(" — ", 1))
                group.set_shortcut(token, action)
            else:
                group.set_plain(p)
            self._lay.addWidget(group)
        self._lay.addStretch(1)
