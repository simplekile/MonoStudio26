"""App-wide footer: branding (left) + activity log (right)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, QRect
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.version import get_app_version
from monostudio.ui_qt.activity_log import activity_log
from monostudio.ui_qt.style import monos_font

_APP_FOOTER_HEIGHT = 28


def _load_logo_pixmap(size: int, color_hex: str) -> QPixmap:
    base = get_app_base_path()
    logo_path = base / "monostudio_data" / "icons" / "logo.svg"
    if not logo_path.is_file():
        return QPixmap()
    try:
        svg = logo_path.read_text(encoding="utf-8").replace("currentColor", color_hex)
    except OSError:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(p, QRect(0, 0, size, size))
    finally:
        p.end()
    return pix


class AppFooter(QWidget):
    """Fixed-height footer spanning the full main window width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppFooter")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(_APP_FOOTER_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 0, 16, 0)
        root.setSpacing(8)

        brand = QWidget(self)
        brand.setObjectName("AppFooterBrand")
        brand_l = QHBoxLayout(brand)
        brand_l.setContentsMargins(0, 0, 0, 0)
        brand_l.setSpacing(6)

        logo = QLabel(brand)
        logo.setObjectName("AppFooterLogo")
        logo.setFixedSize(16, 16)
        logo_px = _load_logo_pixmap(14, "#71717a")
        if not logo_px.isNull():
            logo.setPixmap(logo_px)

        name = QLabel("MONOS", brand)
        name.setObjectName("AppFooterName")
        name.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))

        version = QLabel(get_app_version(), brand)
        version.setObjectName("AppFooterVersion")
        version.setFont(monos_font("JetBrains Mono", 8, QFont.Weight.Normal))

        brand_l.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_l.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_l.addWidget(version, 0, Qt.AlignmentFlag.AlignVCenter)

        self._log_label = QLabel("", self)
        self._log_label.setObjectName("AppFooterLog")
        self._log_label.setFont(monos_font("Inter", 11, QFont.Weight.Normal))
        self._log_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._log_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        root.addWidget(brand, 0)
        root.addWidget(self._log_label, 1)

        activity_log.message_changed.connect(self._on_log_message)
        latest = activity_log.latest()
        if latest is not None:
            self._on_log_message(latest.message, latest.level)

    def _on_log_message(self, message: str, level: str) -> None:
        self._log_label.setText(message)
        self._log_label.setProperty("level", level)
        self._log_label.style().unpolish(self._log_label)
        self._log_label.style().polish(self._log_label)
