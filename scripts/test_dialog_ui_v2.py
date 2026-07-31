"""
MONOS Dialog UI v2 — standalone visual playground.

Run:
    python scripts/test_dialog_ui_v2.py

For a monochrome alternative (recommended if v2 feels too colorful):
    python scripts/test_dialog_ui_studio.py

Self-contained: does not modify global style.py. Experiment with layout,
typography, surfaces, and button hierarchy before rolling into production.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QMouseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, apply_dark_theme

# ---------------------------------------------------------------------------
# Design tokens (v2) — semantic color codes per section / state
# ---------------------------------------------------------------------------
#
# | Key          | Border    | Accent    | Label     | Use case              |
# |--------------|-----------|-----------|-----------|------------------------|
# | destination  | #2563eb   | #3b82f6   | #93c5fd   | Target path, dept, dest |
# | options      | #7c3aed   | #8b5cf6   | #c4b5fd   | Toggles, preferences   |
# | source       | #059669   | #10b981   | #6ee7b7   | Input files, upstream  |
# | identity     | #d97706   | #f59e0b   | #fcd34d   | Names, IDs, rename     |
# | neutral      | #52525b   | #a1a1aa   | #d4d4d8   | Generic blocks         |
# | info         | #2563eb   | #3b82f6   | #93c5fd   | Callouts               |
# | warning      | #d97706   | #f59e0b   | #fcd34d   | Warnings               |
# | danger       | #dc2626   | #ef4444   | #fca5a5   | Destructive            |
#
# Borders: 2px solid hex (not 1px rgba) + QPainter AA — avoids jagged edges on Win.

TOK = {
    "bg": "#0c0c0f",
    "surface": "#141418",
    "card": "#1a1a20",
    "card_hover": "#202028",
    "elevated": "#26262e",
    "border": "#3f3f46",           # 2px shell / divider
    "border_soft": "#2a2a32",      # inner separation
    "border_focus": "#3b82f6",
    "accent": "#3b82f6",
    "accent_soft": "#1a2d4d",
    "accent_hover": "#60a5fa",
    "success": "#10b981",
    "success_soft": "#142a22",
    "warning": "#f59e0b",
    "warning_soft": "#2a2010",
    "danger": "#ef4444",
    "danger_soft": "#2a1418",
    "text": "#fafafa",
    "text_secondary": "#c4c4cc",
    "text_muted": "#8b8b96",
    "mono": "#a1a1b0",
    "radius_dialog": 16,
    "radius_card": 12,
    "radius_input": 8,
    "radius_btn": 8,
    "radius_pill": 6,
    "border_w": 2,                 # px — always 2 for crisp AA strokes
}

SEM: dict[str, dict[str, str]] = {
    "destination": {
        "bg": "#101820",
        "border": "#2563eb",
        "accent": "#3b82f6",
        "label": "#93c5fd",
        "soft": "#152238",
    },
    "options": {
        "bg": "#14101e",
        "border": "#7c3aed",
        "accent": "#8b5cf6",
        "label": "#c4b5fd",
        "soft": "#1e1530",
    },
    "source": {
        "bg": "#0e1812",
        "border": "#059669",
        "accent": "#10b981",
        "label": "#6ee7b7",
        "soft": "#122820",
    },
    "identity": {
        "bg": "#18140e",
        "border": "#d97706",
        "accent": "#f59e0b",
        "label": "#fcd34d",
        "soft": "#241c10",
    },
    "neutral": {
        "bg": "#18181e",
        "border": "#52525b",
        "accent": "#a1a1aa",
        "label": "#d4d4d8",
        "soft": "#1e1e24",
    },
    "info": {
        "bg": "#101820",
        "border": "#2563eb",
        "accent": "#3b82f6",
        "label": "#93c5fd",
        "soft": "#152238",
    },
    "warning": {
        "bg": "#1a1408",
        "border": "#d97706",
        "accent": "#f59e0b",
        "label": "#fcd34d",
        "soft": "#2a2010",
    },
    "danger": {
        "bg": "#1a0e10",
        "border": "#dc2626",
        "accent": "#ef4444",
        "label": "#fca5a5",
        "soft": "#2a1418",
    },
}


def _sem(section: str) -> dict[str, str]:
    return SEM.get(section, SEM["neutral"])


_BW = TOK["border_w"]

DIALOG_V2_QSS = f"""
/* ---- Shell ---- */
QWidget#ModernDialogRoot {{ background: transparent; }}
QWidget#ModernDialogHeader {{ background: transparent; }}
QWidget#ModernDialogBody {{ background: transparent; }}
QWidget#ModernDialogFooter {{
    background: transparent;
    border-top: {_BW}px solid {TOK["border_soft"]};
}}
QFrame#ModernHeaderRule {{
    background: {TOK["border_soft"]};
    max-height: {_BW}px;
    border: none;
}}

/* ---- Typography ---- */
QLabel.ModernDialogTitle {{
    color: {TOK["text"]};
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.2px;
}}
QLabel.ModernDialogSubtitle {{
    color: {TOK["text_muted"]};
    font-size: 12px;
    font-weight: 500;
}}
QLabel.ModernFieldLabel {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QLabel.ModernFieldHint {{
    color: {TOK["text_muted"]};
    font-size: 11px;
    font-weight: 500;
}}
QLabel.ModernFooterHint {{
    color: {TOK["text_muted"]};
    font-size: 11px;
    font-weight: 500;
}}

/* ---- Icon badge ---- */
QFrame#ModernIconBadge {{
    border-radius: 10px;
    min-width: 40px; max-width: 40px;
    min-height: 40px; max-height: 40px;
    border: {_BW}px solid {TOK["accent"]};
    background: {_sem("destination")["soft"]};
}}
QFrame#ModernIconBadge[variant="danger"] {{
    border-color: {_sem("danger")["border"]};
    background: {_sem("danger")["soft"]};
}}
QFrame#ModernIconBadge[variant="success"] {{
    border-color: {_sem("source")["border"]};
    background: {_sem("source")["soft"]};
}}
QFrame#ModernIconBadge[variant="warning"] {{
    border-color: {_sem("warning")["border"]};
    background: {_sem("warning")["soft"]};
}}

/* ---- Close button ---- */
QPushButton#ModernCloseButton {{
    background: transparent;
    border: {_BW}px solid transparent;
    border-radius: 8px;
    padding: 6px;
    min-width: 32px; max-width: 32px;
    min-height: 32px; max-height: 32px;
}}
QPushButton#ModernCloseButton:hover {{
    background: #1e1e26;
    border-color: {TOK["border"]};
}}

/* ---- Section cards (bg/border painted in paintEvent; QSS for children) ---- */
QFrame#ModernSectionCard {{
    background: transparent;
    border: none;
}}
QLabel.ModernSectionTitle {{
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.4px;
}}

/* Section-tinted field labels */
QFrame#ModernSectionCard[section="destination"] QLabel.ModernFieldLabel {{ color: {_sem("destination")["label"]}; }}
QFrame#ModernSectionCard[section="options"] QLabel.ModernFieldLabel {{ color: {_sem("options")["label"]}; }}
QFrame#ModernSectionCard[section="source"] QLabel.ModernFieldLabel {{ color: {_sem("source")["label"]}; }}
QFrame#ModernSectionCard[section="identity"] QLabel.ModernFieldLabel {{ color: {_sem("identity")["label"]}; }}
QFrame#ModernSectionCard[section="neutral"] QLabel.ModernFieldLabel {{ color: {_sem("neutral")["label"]}; }}
QFrame#ModernSectionCard[section="danger"] QLabel.ModernFieldLabel {{ color: {_sem("danger")["label"]}; }}

/* Section-tinted checkboxes */
QFrame#ModernSectionCard[section="destination"] QCheckBox::indicator:checked {{
    background: {_sem("destination")["accent"]}; border-color: {_sem("destination")["accent"]};
}}
QFrame#ModernSectionCard[section="options"] QCheckBox::indicator:checked {{
    background: {_sem("options")["accent"]}; border-color: {_sem("options")["accent"]};
}}
QFrame#ModernSectionCard[section="source"] QCheckBox::indicator:checked {{
    background: {_sem("source")["accent"]}; border-color: {_sem("source")["accent"]};
}}
QFrame#ModernSectionCard[section="identity"] QCheckBox::indicator:checked {{
    background: {_sem("identity")["accent"]}; border-color: {_sem("identity")["accent"]};
}}

/* ---- Callouts (border painted) ---- */
QFrame#ModernCallout {{ background: transparent; border: none; }}

/* ---- Inputs — 2px solid borders ---- */
QLineEdit, QComboBox {{
    background: {TOK["surface"]};
    color: {TOK["text"]};
    border: {_BW}px solid {TOK["border"]};
    border-radius: {TOK["radius_input"]}px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 500;
    min-height: 20px;
}}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{
    border-color: {TOK["border_focus"]};
    background: {TOK["elevated"]};
}}
QFrame#ModernSectionCard[section="destination"] QLineEdit:focus,
QFrame#ModernSectionCard[section="destination"] QComboBox:focus {{
    border-color: {_sem("destination")["accent"]};
}}
QFrame#ModernSectionCard[section="options"] QLineEdit:focus,
QFrame#ModernSectionCard[section="options"] QComboBox:focus {{
    border-color: {_sem("options")["accent"]};
}}
QFrame#ModernSectionCard[section="source"] QLineEdit:focus,
QFrame#ModernSectionCard[section="source"] QComboBox:focus {{
    border-color: {_sem("source")["accent"]};
}}
QLineEdit[mono="true"] {{
    font-family: "JetBrains Mono", "Cascadia Mono", monospace;
    font-size: 12px;
    color: {TOK["mono"]};
}}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{
    background: {TOK["card"]};
    color: {TOK["text"]};
    border: {_BW}px solid {TOK["border"]};
    selection-background-color: {_sem("destination")["soft"]};
    selection-color: {TOK["text"]};
    outline: none;
}}

QCheckBox {{
    color: {TOK["text_secondary"]};
    font-size: 13px;
    font-weight: 500;
    spacing: 10px;
}}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border-radius: 5px;
    border: {_BW}px solid {TOK["border"]};
    background: {TOK["surface"]};
}}
QCheckBox::indicator:hover {{
    border-color: {TOK["accent_hover"]};
}}

/* ---- Status badge ---- */
QLabel#ModernStatusBadge {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
    padding: 4px 10px;
    border-radius: {TOK["radius_pill"]}px;
    border: {_BW}px solid {_sem("destination")["border"]};
    background: {_sem("destination")["soft"]};
    color: {_sem("destination")["label"]};
}}
QLabel#ModernStatusBadge[status="ready"] {{
    border-color: {_sem("source")["border"]};
    background: {_sem("source")["soft"]};
    color: {_sem("source")["label"]};
}}
QLabel#ModernStatusBadge[status="risk"] {{
    border-color: {_sem("danger")["border"]};
    background: {_sem("danger")["soft"]};
    color: {_sem("danger")["label"]};
}}

/* ---- Launcher ---- */
QWidget#DialogV2Launcher {{ background: {TOK["bg"]}; }}
QLabel#LauncherTitle {{
    color: {TOK["text"]};
    font-size: 20px;
    font-weight: 700;
}}
QLabel#LauncherSubtitle {{
    color: {TOK["text_muted"]};
    font-size: 13px;
}}
QPushButton.LauncherCard {{
    background: {TOK["card"]};
    color: {TOK["text"]};
    border: {_BW}px solid {TOK["border"]};
    border-radius: 12px;
    padding: 16px 20px;
    font-size: 14px;
    font-weight: 600;
    text-align: left;
}}
QPushButton.LauncherCard:hover {{
    background: {TOK["card_hover"]};
    border-color: {_sem("destination")["accent"]};
}}

/* ---- Option cards ---- */
QFrame#ModernOptionCard {{
    background: {TOK["surface"]};
    border: {_BW}px solid {TOK["border"]};
    border-radius: {TOK["radius_card"]}px;
}}
QFrame#ModernOptionCard:hover {{
    background: {TOK["card_hover"]};
    border-color: #71717a;
}}
QFrame#ModernOptionCard[selected="true"] {{
    background: {_sem("destination")["soft"]};
    border-color: {_sem("destination")["accent"]};
}}

/* ---- Preflight row ---- */
QFrame#ModernPreflightRow {{
    background: {TOK["surface"]};
    border: {_BW}px solid {TOK["border"]};
    border-radius: {TOK["radius_input"]}px;
}}
QFrame#ModernPreflightRow[status="ok"] {{
    border-color: {_sem("source")["border"]};
    background: {_sem("source")["soft"]};
}}
QFrame#ModernPreflightRow[status="warn"] {{
    border-color: {_sem("warning")["border"]};
    background: {_sem("warning")["soft"]};
}}
QFrame#ModernPreflightRow[status="fail"] {{
    border-color: {_sem("danger")["border"]};
    background: {_sem("danger")["soft"]};
}}

/* ---- Progress ---- */
QProgressBar#ModernProgress {{
    background: {TOK["surface"]};
    border: {_BW}px solid {TOK["border"]};
    border-radius: 6px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar#ModernProgress::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {_sem("destination")["accent"]}, stop:1 {_sem("destination")["label"]});
    border-radius: 4px;
}}

/* ---- Risk meter ---- */
QFrame#ModernRiskMeter {{
    border-radius: {TOK["radius_card"]}px;
    border: {_BW}px solid {TOK["border"]};
    background: {TOK["surface"]};
}}
QFrame#ModernRiskMeter[level="low"] {{
    border-color: {_sem("source")["border"]};
    background: {_sem("source")["soft"]};
}}
QFrame#ModernRiskMeter[level="medium"] {{
    border-color: {_sem("warning")["border"]};
    background: {_sem("warning")["soft"]};
}}
QFrame#ModernRiskMeter[level="high"] {{
    border-color: {_sem("danger")["border"]};
    background: {_sem("danger")["soft"]};
}}
"""


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def _paint_smooth_frame(
  painter,
  rect,
  *,
  bg: str,
  border: str,
  radius: int,
  border_w: int = _BW,
  accent_stripe: str | None = None,
) -> None:
  """Antialiased fill + 2px border (+ optional left accent stripe)."""
  from PySide6.QtCore import QRectF
  from PySide6.QtGui import QBrush, QColor, QPainter, QPen

  painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
  inset = border_w / 2.0 + 0.5
  r = QRectF(rect).adjusted(inset, inset, -inset, -inset)

  painter.setPen(Qt.PenStyle.NoPen)
  painter.setBrush(QBrush(QColor(bg)))
  painter.drawRoundedRect(r, radius, radius)

  if accent_stripe:
    stripe = QRectF(r.left() + 1, r.top() + 4, 4, r.height() - 8)
    painter.setBrush(QBrush(QColor(accent_stripe)))
    painter.drawRoundedRect(stripe, 2, 2)

  pen = QPen(QColor(border), float(border_w))
  pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
  painter.setBrush(Qt.BrushStyle.NoBrush)
  painter.setPen(pen)
  painter.drawRoundedRect(r, radius, radius)


class _DragHeader(QWidget):
  """Title bar region — drag to move frameless dialog."""

  def __init__(self, dialog: QWidget, parent: QWidget | None = None) -> None:
    super().__init__(parent)
    self._dialog = dialog
    self._drag_origin: QPoint | None = None
    self._window_origin: QPoint | None = None

  def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
    if event.button() == Qt.MouseButton.LeftButton:
      self._drag_origin = event.globalPosition().toPoint()
      self._window_origin = self._dialog.frameGeometry().topLeft()
    super().mousePressEvent(event)

  def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
    if (
      event.buttons() & Qt.MouseButton.LeftButton
      and self._drag_origin is not None
      and self._window_origin is not None
    ):
      delta = event.globalPosition().toPoint() - self._drag_origin
      self._dialog.move(self._window_origin + delta)
    super().mouseMoveEvent(event)

  def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
    self._drag_origin = None
    self._window_origin = None
    super().mouseReleaseEvent(event)


def _status_badge(text: str, *, status: str = "default", parent: QWidget | None = None) -> QLabel:
  badge = QLabel(text.upper(), parent)
  badge.setObjectName("ModernStatusBadge")
  badge.setProperty("status", status)
  badge.style().unpolish(badge)
  badge.style().polish(badge)
  return badge


def _icon_badge(icon_name: str, *, variant: str = "default", color: str | None = None) -> QFrame:
  frame = QFrame()
  frame.setObjectName("ModernIconBadge")
  if variant != "default":
    frame.setProperty("variant", variant)
    frame.style().unpolish(frame)
    frame.style().polish(frame)
  lay = QHBoxLayout(frame)
  lay.setContentsMargins(0, 0, 0, 0)
  if variant == "danger":
    icon_color = _sem("danger")["accent"]
  elif variant == "warning":
    icon_color = _sem("warning")["accent"]
  elif variant == "success":
    icon_color = _sem("source")["accent"]
  else:
    icon_color = _sem("destination")["accent"]
  btn = QPushButton()
  btn.setFlat(True)
  btn.setEnabled(False)
  btn.setIcon(lucide_icon(icon_name, size=18, color_hex=icon_color or _sem("destination")["accent"]))
  btn.setIconSize(btn.iconSize())
  lay.addStretch()
  lay.addWidget(btn)
  lay.addStretch()
  return frame


class ModernCallout(QFrame):
  def __init__(
    self,
    title: str,
    body: str,
    *,
    variant: str = "info",
    parent: QWidget | None = None,
  ) -> None:
    super().__init__(parent)
    self.setObjectName("ModernCallout")
    self._variant = variant if variant in SEM else "info"
    self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    root = QHBoxLayout(self)
    root.setContentsMargins(16, 14, 16, 14)
    root.setSpacing(12)

    sem = _sem(self._variant)
    icon_map = {"info": "circle-help", "warning": "triangle-alert", "danger": "triangle-alert"}
    icon_btn = QPushButton()
    icon_btn.setFlat(True)
    icon_btn.setEnabled(False)
    icon_btn.setIcon(
      lucide_icon(icon_map.get(self._variant, "circle-help"), size=16, color_hex=sem["accent"])
    )
    icon_btn.setFixedSize(20, 20)
    root.addWidget(icon_btn, alignment=Qt.AlignmentFlag.AlignTop)

    text_col = QVBoxLayout()
    text_col.setSpacing(4)
    t = QLabel(title)
    t.setStyleSheet(f"color: {sem['label']}; font-size: 12px; font-weight: 700; background: transparent;")
    b = QLabel(body)
    b.setWordWrap(True)
    b.setStyleSheet(f"color: {TOK['text_secondary']}; font-size: 12px; font-weight: 500; background: transparent;")
    text_col.addWidget(t)
    text_col.addWidget(b)
    root.addLayout(text_col, stretch=1)

  def paintEvent(self, event) -> None:  # noqa: N802
    from PySide6.QtGui import QPainter

    sem = _sem(self._variant)
    p = QPainter(self)
    _paint_smooth_frame(
      p, self.rect(),
      bg=sem["soft"],
      border=sem["border"],
      radius=TOK["radius_card"],
      accent_stripe=sem["accent"],
    )
    p.end()
    super().paintEvent(event)


class ModernSectionCard(QFrame):
  def __init__(
    self,
    title: str | None = None,
    *,
    section_type: str = "neutral",
    parent: QWidget | None = None,
  ) -> None:
    super().__init__(parent)
    self.setObjectName("ModernSectionCard")
    self._section_type = section_type if section_type in SEM else "neutral"
    self.setProperty("section", self._section_type)
    self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
    self.style().unpolish(self)
    self.style().polish(self)

    sem = _sem(self._section_type)
    self._layout = QVBoxLayout(self)
    self._layout.setContentsMargins(18, 16, 16, 16)
    self._layout.setSpacing(12)
    if title:
      lbl = QLabel(title.upper())
      lbl.setProperty("class", "ModernSectionTitle")
      lbl.setStyleSheet(
        f"color: {sem['label']}; font-size: 10px; font-weight: 800; "
        f"letter-spacing: 1.4px; background: transparent;"
      )
      self._layout.addWidget(lbl)

  def paintEvent(self, event) -> None:  # noqa: N802
    from PySide6.QtGui import QPainter

    sem = _sem(self._section_type)
    p = QPainter(self)
    _paint_smooth_frame(
      p, self.rect(),
      bg=sem["bg"],
      border=sem["border"],
      radius=TOK["radius_card"],
      accent_stripe=sem["accent"],
    )
    p.end()
    super().paintEvent(event)

  def add_widget(self, widget: QWidget) -> None:
    self._layout.addWidget(widget)

  def add_layout(self, layout) -> None:
    self._layout.addLayout(layout)


def _field_label(text: str, *, section: str = "neutral") -> QLabel:
  sem = _sem(section)
  lbl = QLabel(text)
  lbl.setProperty("class", "ModernFieldLabel")
  lbl.setStyleSheet(
    f"color: {sem['label']}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px; background: transparent;"
  )
  return lbl


def _form_field(label: str, widget: QWidget, *, hint: str = "", section: str = "neutral") -> QWidget:
  wrap = QWidget()
  wrap.setStyleSheet("background: transparent;")
  lay = QVBoxLayout(wrap)
  lay.setContentsMargins(0, 0, 0, 0)
  lay.setSpacing(6)
  lay.addWidget(_field_label(label, section=section))
  lay.addWidget(widget)
  if hint:
    h = QLabel(hint)
    h.setWordWrap(True)
    h.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 11px; font-weight: 500; background: transparent;")
    lay.addWidget(h)
  return wrap


def _mono_block(text: str, *, small: bool = False, section: str = "source") -> QLabel:
  sem = _sem(section)
  lbl = QLabel(text)
  lbl.setWordWrap(True)
  lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
  size = 11 if small else 12
  lbl.setStyleSheet(
    f"color: {TOK['mono']}; font-family: 'JetBrains Mono', monospace; font-size: {size}px; "
    f"padding: 10px 12px; background: {TOK['surface']}; "
    f"border: {_BW}px solid {sem['border']}; border-radius: {TOK['radius_input']}px;"
  )
  return lbl


def _option_card(
  title: str,
  desc: str,
  icon_name: str,
  *,
  selected: bool = False,
  on_click=None,
) -> QFrame:
  card = QFrame()
  card.setObjectName("ModernOptionCard")
  card.setProperty("selected", "true" if selected else "false")
  card.setCursor(Qt.CursorShape.PointingHandCursor)
  card.style().unpolish(card)
  card.style().polish(card)

  lay = QHBoxLayout(card)
  lay.setContentsMargins(14, 12, 14, 12)
  lay.setSpacing(12)

  icon_frame = QFrame()
  icon_frame.setFixedSize(36, 36)
  icon_frame.setStyleSheet(
    f"background: {TOK['elevated']}; border-radius: 8px; border: {_BW}px solid {TOK['border']};"
  )
  icon_lay = QHBoxLayout(icon_frame)
  icon_lay.setContentsMargins(0, 0, 0, 0)
  ib = QPushButton()
  ib.setFlat(True)
  ib.setEnabled(False)
  ib.setIcon(lucide_icon(icon_name, size=16, color_hex=TOK["text_secondary"]))
  icon_lay.addStretch()
  icon_lay.addWidget(ib)
  icon_lay.addStretch()
  lay.addWidget(icon_frame)

  text_col = QVBoxLayout()
  text_col.setSpacing(2)
  t = QLabel(title)
  t.setProperty("class", "ModernOptionTitle")
  t.setStyleSheet(f"color: {TOK['text']}; font-size: 13px; font-weight: 600; background: transparent;")
  d = QLabel(desc)
  d.setWordWrap(True)
  d.setProperty("class", "ModernOptionDesc")
  d.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 11px; font-weight: 500; background: transparent;")
  text_col.addWidget(t)
  text_col.addWidget(d)
  lay.addLayout(text_col, stretch=1)

  if on_click is not None:
    card.mousePressEvent = lambda e, c=card, fn=on_click: (  # type: ignore[method-assign]
      fn(c) if e.button() == Qt.MouseButton.LeftButton else None
    )
  return card


def _preflight_row(label: str, detail: str, *, status: str = "ok") -> QFrame:
  row = QFrame()
  row.setObjectName("ModernPreflightRow")
  row.setProperty("status", status)
  row.style().unpolish(row)
  row.style().polish(row)

  icon_names = {"ok": "circle-check", "warn": "triangle-alert", "fail": "x"}
  icon_colors = {"ok": TOK["success"], "warn": TOK["warning"], "fail": TOK["danger"]}

  lay = QHBoxLayout(row)
  lay.setContentsMargins(12, 10, 12, 10)
  lay.setSpacing(10)

  ib = QPushButton()
  ib.setFlat(True)
  ib.setEnabled(False)
  ib.setFixedSize(20, 20)
  ib.setIcon(lucide_icon(icon_names.get(status, "info"), size=16, color_hex=icon_colors.get(status, TOK["accent_hover"])))
  lay.addWidget(ib, alignment=Qt.AlignmentFlag.AlignTop)

  col = QVBoxLayout()
  col.setSpacing(2)
  t = QLabel(label)
  t.setStyleSheet(f"color: {TOK['text']}; font-size: 12px; font-weight: 600;")
  d = QLabel(detail)
  d.setWordWrap(True)
  d.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 11px; font-weight: 500;")
  col.addWidget(t)
  col.addWidget(d)
  lay.addLayout(col, stretch=1)
  return row


def _risk_meter(level: str, label: str, detail: str) -> QFrame:
  frame = QFrame()
  frame.setObjectName("ModernRiskMeter")
  frame.setProperty("level", level)
  frame.style().unpolish(frame)
  frame.style().polish(frame)
  lay = QHBoxLayout(frame)
  lay.setContentsMargins(14, 12, 14, 12)
  lay.setSpacing(12)
  badge_status = {"low": "ready", "medium": "default", "high": "risk"}
  lay.addWidget(_status_badge(label, status=badge_status.get(level, "default")))
  col = QVBoxLayout()
  col.setSpacing(2)
  t = QLabel(detail)
  t.setWordWrap(True)
  t.setStyleSheet(f"color: {TOK['text_secondary']}; font-size: 12px; font-weight: 500;")
  col.addWidget(t)
  lay.addLayout(col, stretch=1)
  return frame


class ModernDialogShell(MonosDialog):
  """Frameless dialog with header / scrollable body / footer."""

  def __init__(
    self,
    *,
    title: str,
    subtitle: str = "",
    icon: str = "sparkles",
    icon_variant: str = "default",
    parent: QWidget | None = None,
  ) -> None:
    super().__init__(parent)
    self.set_dialog_border_overlay_enabled(False)
    self.setModal(True)
    self.setObjectName("ModernDialogRoot")
    self.setMinimumWidth(480)

    self._body_layout: QVBoxLayout

    outer = QVBoxLayout(self)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    # --- Header ---
    header = _DragHeader(self)
    header.setObjectName("ModernDialogHeader")
    header_lay = QHBoxLayout(header)
    header_lay.setContentsMargins(20, 18, 16, 14)
    header_lay.setSpacing(14)

    header_lay.addWidget(_icon_badge(icon, variant=icon_variant))

    title_col = QVBoxLayout()
    title_col.setSpacing(2)
    title_lbl = QLabel(title)
    title_lbl.setProperty("class", "ModernDialogTitle")
    title_lbl.setStyleSheet(
      f"color: {TOK['text']}; font-size: 15px; font-weight: 700; letter-spacing: -0.2px;"
    )
    title_col.addWidget(title_lbl)
    if subtitle:
      sub = QLabel(subtitle)
      sub.setWordWrap(True)
      sub.setProperty("class", "ModernDialogSubtitle")
      sub.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 12px; font-weight: 500;")
      title_col.addWidget(sub)
    header_lay.addLayout(title_col, stretch=1)

    close_btn = QPushButton()
    close_btn.setObjectName("ModernCloseButton")
    close_btn.setIcon(lucide_icon("x", size=16, color_hex=TOK["text_secondary"]))
    close_btn.setIconSize(close_btn.iconSize())
    close_btn.setToolTip("Close (Esc)")
    close_btn.clicked.connect(self.reject)
    header_lay.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)

    outer.addWidget(header)

    sep = QFrame()
    sep.setObjectName("ModernHeaderRule")
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFixedHeight(_BW)
    outer.addWidget(sep)

    # --- Body (scrollable) ---
    body_host = QWidget()
    body_host.setObjectName("ModernDialogBody")
    body_host_lay = QVBoxLayout(body_host)
    body_host_lay.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

    body_inner = QWidget()
    self._body_layout = QVBoxLayout(body_inner)
    self._body_layout.setContentsMargins(20, 16, 20, 8)
    self._body_layout.setSpacing(16)
    scroll.setWidget(body_inner)
    body_host_lay.addWidget(scroll)
    outer.addWidget(body_host, stretch=1)

    # --- Footer ---
    self._footer = QWidget()
    self._footer.setObjectName("ModernDialogFooter")
    footer_lay = QHBoxLayout(self._footer)
    footer_lay.setContentsMargins(20, 14, 20, 16)
    footer_lay.setSpacing(8)

    self._footer_hint = QLabel("")
    self._footer_hint.setProperty("class", "ModernFooterHint")
    self._footer_hint.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 11px; font-weight: 500;")
    footer_lay.addWidget(self._footer_hint)
    footer_lay.addStretch(1)

    self._footer_actions = QHBoxLayout()
    self._footer_actions.setSpacing(8)
    footer_lay.addLayout(self._footer_actions)

    outer.addWidget(self._footer)

  def add_body_widget(self, widget: QWidget) -> None:
    self._body_layout.addWidget(widget)

  def add_body_stretch(self) -> None:
    self._body_layout.addStretch(1)

  def set_footer_hint(self, text: str) -> None:
    self._footer_hint.setText(text)
    self._footer_hint.setVisible(bool(text))

  def add_footer_button(self, label: str, *, role: str = "secondary", on_click=None) -> QPushButton:
    btn = QPushButton(label)
    if role == "primary":
      btn.setProperty("class", "ModernBtnPrimary")
      btn.setStyleSheet(DIALOG_V2_QSS)  # ensure class applies
      btn.setObjectName("")  # class-based
    elif role == "danger":
      btn.setProperty("class", "ModernBtnDanger")
    elif role == "ghost":
      btn.setProperty("class", "ModernBtnGhost")
    else:
      btn.setProperty("class", "ModernBtnSecondary")
    btn.setProperty("class", btn.property("class"))  # refresh
    # Re-apply via object name trick — QSS uses class selectors
    cls = btn.property("class")
    btn.setProperty("class", cls)
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    if on_click is not None:
      btn.clicked.connect(on_click)
    self._footer_actions.addWidget(btn)
    return btn

  def paintEvent(self, event) -> None:  # noqa: N802
    from PySide6.QtGui import QPainter

    r = self.rect()
    if not r.isEmpty():
      p = QPainter(self)
      _paint_smooth_frame(
        p, r,
        bg=TOK["bg"],
        border=TOK["border"],
        radius=TOK["radius_dialog"],
        border_w=_BW,
      )
      p.end()
    super(MonosDialog, self).paintEvent(event)


# Fix button styling — apply classes via dynamic property in QSS
def _make_btn(label: str, role: str, *, shortcut: str = "") -> QPushButton:
  btn = QPushButton(label + (f"  ({shortcut})" if shortcut else ""))
  class_map = {
    "primary": "ModernBtnPrimary",
    "secondary": "ModernBtnSecondary",
    "danger": "ModernBtnDanger",
    "ghost": "ModernBtnGhost",
  }
  btn.setProperty("btnRole", class_map.get(role, "ModernBtnSecondary"))
  return btn


# Patch QSS for btnRole property
DIALOG_V2_QSS += """
QPushButton[btnRole="ModernBtnPrimary"] {
    background: """ + TOK["accent"] + """;
    color: #ffffff;
    border: none;
    border-radius: """ + str(TOK["radius_btn"]) + """px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
    min-width: 88px;
}
QPushButton[btnRole="ModernBtnPrimary"]:hover { background: """ + TOK["accent_hover"] + """; }
QPushButton[btnRole="ModernBtnPrimary"]:disabled {
    background: rgba(59,130,246,0.35);
    color: rgba(255,255,255,0.55);
}
QPushButton[btnRole="ModernBtnSecondary"] {
    background: transparent;
    color: """ + TOK["text_secondary"] + """;
    border: """ + str(_BW) + """px solid """ + TOK["border"] + """;
    border-radius: """ + str(TOK["radius_btn"]) + """px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    min-width: 80px;
}
QPushButton[btnRole="ModernBtnSecondary"]:hover {
    color: """ + TOK["text"] + """;
    border-color: rgba(255,255,255,0.18);
    background: rgba(255,255,255,0.04);
}
QPushButton[btnRole="ModernBtnDanger"] {
    background: """ + TOK["danger"] + """;
    color: #ffffff;
    border: none;
    border-radius: """ + str(TOK["radius_btn"]) + """px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
    min-width: 88px;
}
QPushButton[btnRole="ModernBtnDanger"]:hover { background: #f87171; }
QPushButton[btnRole="ModernBtnDanger"]:disabled {
    background: rgba(239,68,68,0.35);
    color: rgba(255,255,255,0.55);
}
QPushButton[btnRole="ModernBtnGhost"] {
    background: transparent;
    color: """ + TOK["text_muted"] + """;
    border: none;
    border-radius: """ + str(TOK["radius_btn"]) + """px;
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton[btnRole="ModernBtnGhost"]:hover {
    color: """ + TOK["text_secondary"] + """;
    background: rgba(255,255,255,0.04);
}
"""


# ---------------------------------------------------------------------------
# Demo dialogs
# ---------------------------------------------------------------------------


class RenameAssetDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Rename Asset",
      subtitle="Updates folder name and matching work files across departments.",
      icon="pencil",
      icon_variant="default",
    )
    self.setMinimumWidth(520)

    callout = ModernCallout(
      "Pipeline sync",
      "Work files using the old prefix will be renamed automatically. "
      "External DCC references are not updated.",
      variant="info",
    )
    self.add_body_widget(callout)

    card = ModernSectionCard("Identity", section_type="identity")
    current = _mono_block("char_aya_prototype", section="identity")
    card.add_widget(_form_field("Current name", current, section="identity"))

    self._new_name = QLineEdit("char_aya_prototype_v2")
    self._new_name.setPlaceholderText("char_base_name")
    preview_row = QHBoxLayout()
    preview_row.setSpacing(8)
    preview_lbl = _field_label("Preview", section="identity")
    self._preview = QLabel("char_aya_prototype_v2")
    self._preview.setStyleSheet(
      f"color: {_sem('identity')['label']}; font-family: 'JetBrains Mono', monospace; "
      f"font-size: 12px; background: transparent;"
    )
    preview_row.addWidget(preview_lbl)
    preview_row.addStretch()
    preview_row.addWidget(self._preview)
    card.add_widget(
      _form_field(
        "New name",
        self._new_name,
        hint="Type prefix is applied automatically for Character assets.",
        section="identity",
      )
    )
    card.add_layout(preview_row)
    self.add_body_widget(card)

    self._new_name.textChanged.connect(self._on_name_changed)
    self._primary = _make_btn("Rename", "primary", shortcut="Enter")
    self._primary.clicked.connect(self.accept)
    self._primary.setEnabled(False)
    cancel = _make_btn("Cancel", "secondary", shortcut="Esc")
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(self._primary)
    self.set_footer_hint("Changes apply immediately on confirm")

    self._on_name_changed(self._new_name.text())

  def _on_name_changed(self, text: str) -> None:
    name = text.strip()
    self._preview.setText(name or "—")
    valid = bool(name) and name != "char_aya_prototype"
    self._primary.setEnabled(valid)

  def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
    super().showEvent(event)
    self._new_name.setFocus()
    self._new_name.selectAll()


class DeleteConfirmDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Move to Trash",
      subtitle="This asset will be removed from the pipeline view.",
      icon="trash-2",
      icon_variant="danger",
    )
    self.setMinimumWidth(540)

    self.add_body_widget(
      ModernCallout(
        "Cannot be undone from main view",
        "You can restore this item from Project Trash within the retention period.",
        variant="warning",
      )
    )

    card = ModernSectionCard("Target", section_type="danger")
    path = _mono_block("D:/Projects/Demo/assets/character/char_aya_prototype", section="danger")
    meta = QHBoxLayout()
    meta.addWidget(_status_badge("12 work files", status="ready"))
    meta.addWidget(_status_badge("3 publishes", status="risk"))
    meta.addStretch()
    card.add_widget(path)
    card.add_layout(meta)
    self.add_body_widget(card)

    self._confirm = QLineEdit()
    self._confirm.setPlaceholderText("Type asset name to confirm")
    self.add_body_widget(
      _form_field("Confirmation", self._confirm, hint="Type char_aya_prototype exactly to enable delete.", section="danger")
    )

    self._delete_btn = _make_btn("Move to Trash", "danger")
    self._delete_btn.setEnabled(False)
    self._delete_btn.clicked.connect(self.accept)
    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(self._delete_btn)
    self._confirm.textChanged.connect(self._on_confirm)

  def _on_confirm(self, text: str) -> None:
    self._delete_btn.setEnabled(text.strip() == "char_aya_prototype")


class ImportSourceDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Import Reference",
      subtitle="Choose how files land in the project structure.",
      icon="download",
      icon_variant="success",
    )
    self.setMinimumWidth(560)
    self.setMinimumHeight(420)

    scope_card = ModernSectionCard("Destination", section_type="destination")
    self._dept = QComboBox()
    self._dept.addItems(["Concept", "Texture", "Sculpt", "Reference"])
    self._subfolder = QLineEdit()
    self._subfolder.setPlaceholderText("Optional subfolder")
    scope_card.add_widget(_form_field("Department", self._dept, section="destination"))
    scope_card.add_widget(
      _form_field(
        "Subfolder",
        self._subfolder,
        hint="Leave empty to import at department root.",
        section="destination",
      )
    )
    self.add_body_widget(scope_card)

    opts_card = ModernSectionCard("Options", section_type="options")
    self._copy = QCheckBox("Copy files (keep source intact)")
    self._copy.setChecked(True)
    self._version = QCheckBox("Auto-increment version if name exists")
    self._version.setChecked(True)
    self._thumb = QCheckBox("Generate thumbnail preview")
    opts_card.add_widget(self._copy)
    opts_card.add_widget(self._version)
    opts_card.add_widget(self._thumb)
    self.add_body_widget(opts_card)

    path_card = ModernSectionCard("Source", section_type="source")
    src = QLabel("3 files selected · 148.2 MB")
    src.setStyleSheet(
      f"color: {_sem('source')['label']}; font-size: 13px; font-weight: 600; background: transparent;"
    )
    path_card.add_widget(src)
    file_list = QLabel("concept_ref_01.psd, concept_ref_02.png, notes.txt")
    file_list.setWordWrap(True)
    file_list.setStyleSheet(
      f"color: {TOK['text_secondary']}; font-size: 12px; font-weight: 500; background: transparent;"
    )
    path_card.add_widget(file_list)
    self.add_body_widget(path_card)

    import_btn = _make_btn("Import", "primary", shortcut="Enter")
    import_btn.clicked.connect(self.accept)
    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(_make_btn("Browse…", "ghost"))
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(import_btn)
    self.set_footer_hint("Files will be copied to publish folder")


class NewProjectDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="New Project",
      subtitle="Create a pipeline workspace with auto-generated project ID.",
      icon="folder-plus",
      icon_variant="success",
    )
    self.setMinimumWidth(500)

    card = ModernSectionCard("Project details", section_type="identity")
    self._name = QLineEdit("Forest Spirit")
    self._name.setPlaceholderText("e.g. Forest Spirit")
    self._id_preview = QLineEdit("260721_forest_spirit")
    self._id_preview.setReadOnly(True)
    self._id_preview.setProperty("mono", True)
    card.add_widget(_form_field("Project name", self._name, section="identity"))
    card.add_widget(
      _form_field(
        "Project ID",
        self._id_preview,
        hint="Generated from today's date + sanitized name. Immutable after creation.",
        section="identity",
      )
    )
    self.add_body_widget(card)

    ws_card = ModernSectionCard("Workspace", section_type="destination")
    ws_card.add_widget(_mono_block("D:/Dropbox/MonoStudio/Workspace", small=True, section="destination"))
    self.add_body_widget(ws_card)

    self._name.textChanged.connect(self._sync_id)
    create = _make_btn("Create", "primary", shortcut="Enter")
    create.clicked.connect(self.accept)
    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(create)
    self.set_footer_hint("Safe operation — empty project scaffold only")

  def _sync_id(self, text: str) -> None:
    slug = text.strip().lower().replace(" ", "_") or "untitled"
    self._id_preview.setText(f"260721_{slug}")

  def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
    super().showEvent(event)
    self._name.setFocus()
    self._name.selectAll()


class CompPreflightDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Comp Saver Preflight",
      subtitle="Review upstream renders before saving the comp file.",
      icon="clipboard-check",
      icon_variant="default",
    )
    self.setMinimumWidth(560)

    summary = QHBoxLayout()
    summary.addWidget(_status_badge("2 passed", status="ready"))
    summary.addWidget(_status_badge("1 warning", status="default"))
    summary.addWidget(_status_badge("1 blocked", status="risk"))
    summary.addStretch()
    wrap = QWidget()
    wrap.setLayout(summary)
    self.add_body_widget(wrap)

    checks = ModernSectionCard("Checks", section_type="neutral")
    checks.add_widget(_preflight_row("Upstream plate found", "shot_010_comp_v003.exr · 1920×1080", status="ok"))
    checks.add_widget(_preflight_row("LUT applied", "show_lut_v2.cube loaded in comp", status="ok"))
    checks.add_widget(
      _preflight_row(
        "Frame range mismatch",
        "Comp ends at 120 but plate has 118 frames — may cause black tail.",
        status="warn",
      )
    )
    checks.add_widget(
      _preflight_row(
        "Missing denoise pass",
        "Expected denoise render not found in publish folder.",
        status="fail",
      )
    )
    self.add_body_widget(checks)

    self._force = QCheckBox("Save anyway (skip blocked checks)")
    self.add_body_widget(self._force)

    save = _make_btn("Save Comp", "primary")
    save.setEnabled(False)
    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self.reject)
    self._force.toggled.connect(save.setEnabled)
    save.clicked.connect(self.accept)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(save)
    self.set_footer_hint("Blocked checks require explicit override")


class ForceRenameProjectDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Force Rename Project ID",
      subtitle="Renames the project root folder — may break external references.",
      icon="triangle-alert",
      icon_variant="warning",
    )
    self.setMinimumWidth(580)

    self.add_body_widget(_risk_meter("high", "HIGH RISK", "24 publish versions detected. External DCC references may break."))

    id_card = ModernSectionCard("Identifiers", section_type="identity")
    self._current_id = _mono_block("250101_forest_spirit")
    self._new_id = QLineEdit("260721_forest_spirit_v2")
    self._new_id.setProperty("mono", True)
    id_card.add_widget(_form_field("Current ID", self._current_id, section="identity"))
    id_card.add_widget(_form_field("New ID", self._new_id, section="identity"))
    self.add_body_widget(id_card)

    impact = ModernSectionCard("Impact analysis", section_type="warning")
    grid = QHBoxLayout()
    for label, val in [("Assets", "18"), ("Shots", "42"), ("Publishes", "24")]:
      cell = QVBoxLayout()
      cell.setSpacing(4)
      k = QLabel(label.upper())
      k.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;")
      v = QLabel(val)
      v.setStyleSheet(f"color: {TOK['text']}; font-size: 20px; font-weight: 700;")
      cell.addWidget(k)
      cell.addWidget(v)
      grid.addLayout(cell)
    grid.addStretch()
    impact.add_layout(grid)
    self.add_body_widget(impact)

    self._ack = QCheckBox("I understand this may break references and cached data")
    self._confirm = QLineEdit()
    self._confirm.setPlaceholderText("Type current project ID to confirm")
    self.add_body_widget(self._ack)
    self.add_body_widget(_form_field("Confirmation", self._confirm))

    rename = _make_btn("Force Rename", "danger")
    rename.setEnabled(False)
    rename.clicked.connect(self.accept)
    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(rename)

    def _validate() -> None:
      ok = self._ack.isChecked() and self._confirm.text().strip() == "250101_forest_spirit"
      rename.setEnabled(ok)

    self._ack.toggled.connect(lambda _v: _validate())
    self._confirm.textChanged.connect(lambda _t: _validate())


class QuickAlertDemoDialog(ModernDialogShell):
  """Compact alert — minimal content, single focus action."""

  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Unsaved changes",
      subtitle="",
      icon="save",
      icon_variant="warning",
    )
    self.setMinimumWidth(400)
    self.setMaximumWidth(440)

    msg = QLabel("Your note has unsaved edits. Save before switching tasks?")
    msg.setWordWrap(True)
    msg.setStyleSheet(f"color: {TOK['text_secondary']}; font-size: 13px; font-weight: 500;")
    self.add_body_widget(msg)

    discard = _make_btn("Don't Save", "ghost")
    discard.clicked.connect(self.reject)
    cancel = _make_btn("Cancel", "secondary")
    save = _make_btn("Save", "primary", shortcut="Enter")
    save.clicked.connect(self.accept)
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(discard)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(save)


class ChoicePickerDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Open With",
      subtitle="Choose how to launch this work file.",
      icon="external-link",
      icon_variant="default",
    )
    self.setMinimumWidth(520)
    self._selected = "fusion"

    options_wrap = QWidget()
    options_lay = QVBoxLayout(options_wrap)
    options_lay.setContentsMargins(0, 0, 0, 0)
    options_lay.setSpacing(8)

    self._cards: dict[str, QFrame] = {}

    def _select(key: str, card: QFrame) -> None:
      self._selected = key
      for k, c in self._cards.items():
        c.setProperty("selected", "true" if k == key else "false")
        c.style().unpolish(c)
        c.style().polish(c)

    specs = [
      ("fusion", "Blackmagic Fusion", "Open comp script with pipeline loader.", "sparkles"),
      ("resolve", "DaVinci Resolve", "Import timeline and apply studio LUT.", "clapperboard"),
      ("folder", "Reveal in Explorer", "Show file location without launching DCC.", "folder-open"),
    ]
    for key, title, desc, icon in specs:
      card = _option_card(title, desc, icon, selected=(key == self._selected), on_click=lambda c, k=key: _select(k, c))
      self._cards[key] = card
      options_lay.addWidget(card)

    self.add_body_widget(options_wrap)

    open_btn = _make_btn("Open", "primary", shortcut="Enter")
    open_btn.clicked.connect(self.accept)
    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(open_btn)
    self.set_footer_hint("Default: Fusion")


class ProgressDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Building proxy cache",
      subtitle="Generating review proxy for shot_010_anim_v004.mov",
      icon="loader-2",
      icon_variant="default",
    )
    self.setMinimumWidth(480)

    self._status = QLabel("Preparing frames…")
    self._status.setStyleSheet(f"color: {TOK['text_secondary']}; font-size: 13px; font-weight: 500;")
    self.add_body_widget(self._status)

    self._bar = QProgressBar()
    self._bar.setObjectName("ModernProgress")
    self._bar.setRange(0, 100)
    self._bar.setValue(0)
    self._bar.setTextVisible(False)
    self._bar.setFixedHeight(8)
    self.add_body_widget(self._bar)

    detail = QLabel("Frame 0 / 240 · ETA —")
    detail.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 11px; font-weight: 500; font-family: 'JetBrains Mono', monospace;")
    self._detail = detail
    self.add_body_widget(detail)

    self._pct = 0
    self._timer = QTimer(self)
    self._timer.setInterval(80)
    self._timer.timeout.connect(self._tick)

    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self._on_cancel)
    self._footer_actions.addWidget(cancel)
    self.set_footer_hint("You can continue working — runs in background")

  def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
    super().showEvent(event)
    self._pct = 0
    self._bar.setValue(0)
    self._timer.start()

  def _tick(self) -> None:
    self._pct = min(100, self._pct + 2)
    self._bar.setValue(self._pct)
    frame = int(240 * self._pct / 100)
    eta = max(0, int((100 - self._pct) * 0.4))
    self._detail.setText(f"Frame {frame} / 240 · ETA {eta}s")
    phases = [
      (20, "Extracting keyframes…"),
      (55, "Encoding H.264 proxy…"),
      (85, "Writing cache index…"),
      (100, "Finalizing…"),
    ]
    for threshold, msg in phases:
      if self._pct <= threshold:
        self._status.setText(msg)
        break
    if self._pct >= 100:
      self._timer.stop()
      self._status.setText("Complete")
      self.set_footer_hint("Proxy ready")
      QTimer.singleShot(600, self.accept)

  def _on_cancel(self) -> None:
    self._timer.stop()
    self.reject()


class InboxDropDemoDialog(ModernDialogShell):
  def __init__(self, parent: QWidget | None = None) -> None:
    super().__init__(
      parent=parent,
      title="Add to Inbox",
      subtitle="5 files dropped — choose client and destination date.",
      icon="inbox",
      icon_variant="default",
    )
    self.setMinimumWidth(540)

    self.add_body_widget(
      ModernCallout(
        "External delivery",
        "Files will be organized under inbox/client/date before distribution.",
        variant="info",
      )
    )

    meta = ModernSectionCard("Delivery", section_type="destination")
    self._client = QComboBox()
    self._client.addItems(["Freelancer — Minh", "Client — Studio Alpha", "Internal review"])
    self._date = QComboBox()
    self._date.addItems(["Today (2026-07-21)", "2026-07-18 (existing)", "Pick new date…"])
    meta.add_widget(_form_field("Source", self._client, section="destination"))
    meta.add_widget(_form_field("Date folder", self._date, section="destination"))
    self.add_body_widget(meta)

    files = ModernSectionCard("Files", section_type="source")
    files.add_widget(_mono_block("hero_concept_v2.psd\nref_board_01.png\nref_board_02.png\n+ 2 more", small=True))
    self.add_body_widget(files)

    add_btn = _make_btn("Add to Inbox", "primary", shortcut="Enter")
    add_btn.clicked.connect(self.accept)
    cancel = _make_btn("Cancel", "secondary")
    cancel.clicked.connect(self.reject)
    self._footer_actions.addWidget(cancel)
    self._footer_actions.addWidget(add_btn)
    self.set_footer_hint("Original files are copied, not moved")


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


class DialogV2Launcher(QWidget):
  result = Signal(str)

  def __init__(self) -> None:
    super().__init__()
    self.setObjectName("DialogV2Launcher")
    self.setWindowTitle("MONOS Dialog UI v2 — Test Launcher")
    self.resize(680, 560)

    root = QVBoxLayout(self)
    root.setContentsMargins(32, 28, 32, 24)
    root.setSpacing(20)

    header = QVBoxLayout()
    header.setSpacing(6)
    title = QLabel("Dialog UI v2")
    title.setObjectName("LauncherTitle")
    sub = QLabel("Standalone playground — pick a sample to preview layout patterns.")
    sub.setObjectName("LauncherSubtitle")
    sub.setWordWrap(True)
    header.addWidget(title)
    header.addWidget(sub)
    root.addLayout(header)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

    scroll_body = QWidget()
    grid = QVBoxLayout(scroll_body)
    grid.setContentsMargins(0, 0, 8, 0)
    grid.setSpacing(8)

    demos: list[tuple[str, str, type[ModernDialogShell]]] = [
      ("Rename Asset", "Form · validation · mono preview · info callout", RenameAssetDemoDialog),
      ("Delete Confirm", "Destructive · type-to-confirm · warning callout", DeleteConfirmDemoDialog),
      ("Import Source", "Multi-section cards · checkboxes · combo", ImportSourceDemoDialog),
      ("New Project", "Auto ID preview · workspace path · safe create", NewProjectDemoDialog),
      ("Comp Preflight", "Check list · pass/warn/fail · override checkbox", CompPreflightDemoDialog),
      ("Force Rename ID", "Risk meter · impact stats · dual confirmation", ForceRenameProjectDemoDialog),
      ("Quick Alert", "Compact · 3-button footer · no scroll content", QuickAlertDemoDialog),
      ("Open With", "Selectable option cards · single choice", ChoicePickerDemoDialog),
      ("Progress", "Animated bar · ETA · auto-complete demo", ProgressDemoDialog),
      ("Inbox Drop", "Client picker · date folder · file list", InboxDropDemoDialog),
    ]

    for label, desc, cls in demos:
      btn = QPushButton()
      btn.setProperty("class", "LauncherCard")
      btn.setCursor(Qt.CursorShape.PointingHandCursor)
      btn_lay = QVBoxLayout(btn)
      btn_lay.setContentsMargins(4, 2, 4, 2)
      btn_lay.setSpacing(4)
      t = QLabel(label)
      t.setStyleSheet(f"color: {TOK['text']}; font-size: 14px; font-weight: 600; background: transparent;")
      d = QLabel(desc)
      d.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 12px; font-weight: 500; background: transparent;")
      btn_lay.addWidget(t)
      btn_lay.addWidget(d)
      btn.clicked.connect(lambda _c=False, c=cls, n=label: self._open_demo(c, n))
      grid.addWidget(btn)

    scroll.setWidget(scroll_body)
    root.addWidget(scroll, stretch=1)

    foot = QLabel("Run: python scripts/test_dialog_ui_v2.py")
    foot.setStyleSheet(f"color: {TOK['text_muted']}; font-size: 11px;")
    root.addWidget(foot)

  def _open_demo(self, cls: type[ModernDialogShell], name: str) -> None:
    dlg = cls(parent=self)
    code = dlg.exec()
    result = "accepted" if code == QDialog.DialogCode.Accepted else "cancelled"
    self.result.emit(f"{name}: {result}")


def main() -> int:
  app = QApplication(sys.argv)
  apply_dark_theme(app)
  app.setStyleSheet(app.styleSheet() + "\n" + DIALOG_V2_QSS)

  launcher = DialogV2Launcher()
  launcher.result.connect(lambda msg: print(msg, flush=True))
  launcher.show()

  return app.exec()


if __name__ == "__main__":
  raise SystemExit(main())
