"""Reusable layout blocks for Settings pages (cards, field rows, segmented controls)."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.app_paths import get_app_base_path
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import monos_font


@lru_cache(maxsize=48)
def _lucide_png_for_qss(name: str, size: int, color_hex: str) -> str:
    """Write Lucide icon to cache; return absolute path for QSS url() (Windows-safe)."""
    cache_dir = get_app_base_path() / "monostudio_data" / "cache" / "qss_icons"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = color_hex.replace("#", "")
    out = cache_dir / f"{name}-{size}-{safe}.png"
    if not out.is_file():
        pm = lucide_icon(name, size=size, color_hex=color_hex).pixmap(size, size)
        if pm.isNull():
            return ""
        pm.save(str(out), "PNG")
    return out.resolve().as_posix()


def _qss_image_url(path: str) -> str:
    if not path:
        return "none"
    return f'url("{path}")'


def _apply_combo_dropdown_icon(combo: QComboBox) -> None:
    chevron = _qss_image_url(_lucide_png_for_qss("chevron-down", 12, "#a1a1aa"))
    combo.setStyleSheet(
        f"""
        QComboBox#SettingsComboBox::down-arrow {{
            image: {chevron};
            width: 12px;
            height: 12px;
            border: none;
            margin-right: 4px;
        }}
        """
    )


def _apply_spin_step_icons(spin: QSpinBox) -> None:
    up = _qss_image_url(_lucide_png_for_qss("chevron-up", 12, "#a1a1aa"))
    down = _qss_image_url(_lucide_png_for_qss("chevron-down", 12, "#a1a1aa"))
    spin.setStyleSheet(
        f"""
        QSpinBox#SettingsSpinBox::up-arrow {{
            image: {up};
            width: 12px;
            height: 12px;
            border: none;
        }}
        QSpinBox#SettingsSpinBox::down-arrow {{
            image: {down};
            width: 12px;
            height: 12px;
            border: none;
        }}
        """
    )


def settings_divider(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("SettingsSectionDivider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def add_settings_section(
    parent: QWidget,
    title: str,
    description: str = "",
) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(parent)
    card.setObjectName("SettingsSectionCard")
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(10)

    title_l = QLabel(title, card)
    title_l.setObjectName("SettingsSectionTitle")
    title_l.setFont(monos_font("Inter", 14, QFont.Weight.Bold))
    layout.addWidget(title_l)

    if description.strip():
        desc = QLabel(description.strip(), card)
        desc.setObjectName("SettingsSectionDesc")
        desc.setWordWrap(True)
        desc.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        layout.addWidget(desc)

    return card, layout


def add_settings_subsection_title(layout: QVBoxLayout, text: str) -> None:
    lab = QLabel(text.upper(), layout.parentWidget())
    lab.setObjectName("SettingsSubsectionTitle")
    lab.setFont(monos_font("Inter", 11, QFont.Weight.Bold))
    layout.addWidget(lab)


def style_settings_combo(
    combo: QComboBox,
    *,
    width: int | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
) -> None:
    combo.setObjectName("SettingsComboBox")
    combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if width is not None:
        combo.setFixedWidth(width)
    if min_width is not None:
        combo.setMinimumWidth(min_width)
    if max_width is not None:
        combo.setMaximumWidth(max_width)
    _apply_combo_dropdown_icon(combo)


def style_settings_spin(spin: QSpinBox, *, width: int = 88) -> None:
    spin.setObjectName("SettingsSpinBox")
    spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    spin.setFixedWidth(width)
    spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
    _apply_spin_step_icons(spin)


def style_settings_line_edit(
    field: QLineEdit,
    *,
    min_width: int = 280,
) -> None:
    """Path / text field — grows with row, no max-width cap."""
    field.setObjectName("SettingsLineEdit")
    field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    field.setMinimumWidth(min_width)


def add_settings_field_row(
    layout: QVBoxLayout,
    label: str,
    widget: QWidget,
    *,
    label_min_width: int = 176,
) -> None:
    row = QWidget(layout.parentWidget())
    row.setObjectName("SettingsFieldRow")
    row_l = QHBoxLayout(row)
    row_l.setContentsMargins(0, 4, 0, 4)
    row_l.setSpacing(16)
    lab = QLabel(label, row)
    lab.setObjectName("SettingsFieldLabel")
    lab.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
    lab.setMinimumWidth(label_min_width)
    lab.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    row_l.addWidget(lab, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    if widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding:
        row_l.addWidget(widget, 1, align)
    else:
        row_l.addWidget(widget, 0, align)
        row_l.addStretch(1)
    layout.addWidget(row)


def add_settings_helper(layout: QVBoxLayout, text: str) -> QLabel:
    lab = QLabel(text, layout.parentWidget())
    lab.setObjectName("DialogHelper")
    lab.setWordWrap(True)
    lab.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
    layout.addWidget(lab)
    return lab


class SettingsSegmentedControl(QWidget):
    """Three-way segmented control (Tier3 pill style)."""

    value_changed = Signal()

    def __init__(
        self,
        options: list[tuple[str, str, str]],
        parent: QWidget | None = None,
    ) -> None:
        """
        options: (label, tooltip, value_key) — value_key is stored in button property.
        """
        super().__init__(parent)
        self.setObjectName("SettingsFieldControl")
        self._value_keys: list[str] = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        bar = QWidget(self)
        bar.setObjectName("Tier3Container")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(6, 6, 6, 6)
        bar_l.setSpacing(4)

        self._group = QButtonGroup(self)
        self._buttons: list[QPushButton] = []
        for label, tooltip, key in options:
            btn = QPushButton(label, bar)
            btn.setObjectName("Tier3Pill")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("segment_value", key)
            self._value_keys.append(key)
            self._group.addButton(btn)
            self._buttons.append(btn)
            bar_l.addWidget(btn, 0)
            btn.toggled.connect(self._on_toggled)

        outer.addWidget(bar, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addStretch(1)

    def _on_toggled(self, checked: bool) -> None:
        if checked:
            self.value_changed.emit()

    def set_value(self, key: str) -> None:
        want = (key or "").strip()
        for btn in self._buttons:
            if btn.property("segment_value") == want:
                btn.setChecked(True)
                return
        if self._buttons:
            self._buttons[-1].setChecked(True)

    def value(self) -> str:
        for btn in self._buttons:
            if btn.isChecked():
                v = btn.property("segment_value")
                return str(v) if v is not None else ""
        return self._value_keys[-1] if self._value_keys else ""
