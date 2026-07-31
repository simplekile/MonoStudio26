"""One-shot extractor: scripts/test_dialog_tiers.py -> monostudio/ui_qt/dialog_tier/reference.py"""
from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = root / "scripts" / "test_dialog_tiers.py"
text = src.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)

start = next(i for i, line in enumerate(lines) if line.startswith("T_METRICS:"))
demo_start = next(i for i, line in enumerate(lines) if line.startswith("class NewProjectTier1Demo"))
tier2_start = next(i for i, line in enumerate(lines) if line.startswith("class _ComboField"))
tier2_end = next(i for i, line in enumerate(lines) if line.startswith("class NewAssetTier2Demo"))

header = '''\
"""MONOS Dialog Tier Design System — golden reference implementation (FROZEN).

Visual design is approved. Do not change layout, spacing, typography, radius,
colors, hierarchy, or visual language without explicit design review.

Harness: scripts/test_dialog_tiers.py
Rule: .cursor/rules/plan_dialog_tier_golden_reference_v1.mdc
"""

from __future__ import annotations

import math
import os
import sys

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal, QEvent, QDate, QTimer, QSize, QObject
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.calendar_date_picker import run_date_picker_dialog
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, apply_dark_theme, monos_font

'''

body = "".join(lines[start:demo_start]) + "".join(lines[tier2_start:tier2_end])
body = body.replace("def _set_tier_theme(", "def set_tier_theme(")
body = body.replace("_set_tier_theme(", "set_tier_theme(")
body = body.replace("def _apply_tier_app_theme(", "def apply_tier_app_theme(")
body = body.replace("_apply_tier_app_theme(", "apply_tier_app_theme(")
body = body.replace("def _configure_tier_text_rendering(", "def configure_tier_text_rendering(")
body = body.replace("_configure_tier_text_rendering(", "configure_tier_text_rendering(")
body = body.replace("global _CURRENT_THEME", "global CURRENT_THEME")
body = body.replace("_CURRENT_THEME", "CURRENT_THEME")

footer = '''

# ---------------------------------------------------------------------------
# Design system public names (golden reference vocabulary)
# ---------------------------------------------------------------------------

GradientPrimaryButton = _GradientCtaButton
GhostButton = _TierGhostButton
DialogCloseButton = _TierCloseButton
FieldShell = _FocusShell
MetadataCard = _MetaCardFrame
ProjectIdMetadataCard = _ProjectIdMetaCard
WorkspacePicker = _WorkspaceCard
FieldRow = _FieldRow
DialogHairline = _TierHairline
InfoStrip = _InfoStrip
PlainFieldInput = _PlainFieldInput
PathField = _PathField
DateField = _DateField
ComboField = _ComboField
InputWithSuffix = _InputWithSuffix

tier_btn = _tier_btn
tier_font = _tier_font
tier_close_button = _tier_close_button
tier_footer_divider = _tier_footer_divider
tier_chrome_band_height = _tier_chrome_band_height
make_field_label = _make_field_label
stack_field_column = _stack_field_column

__all__ = [
    "T",
    "T_METRICS",
    "TIERS_QSS",
    "CURRENT_THEME",
    "set_tier_theme",
    "apply_tier_app_theme",
    "configure_tier_text_rendering",
    "Tier1Dialog",
    "Tier2Dialog",
    "GradientPrimaryButton",
    "GhostButton",
    "DialogCloseButton",
    "FieldShell",
    "MetadataCard",
    "ProjectIdMetadataCard",
    "WorkspacePicker",
    "FieldRow",
    "DialogHairline",
    "InfoStrip",
    "PlainFieldInput",
    "PathField",
    "DateField",
    "ComboField",
    "InputWithSuffix",
    "tier_btn",
    "tier_font",
    "tier_close_button",
    "tier_footer_divider",
    "tier_chrome_band_height",
    "make_field_label",
    "stack_field_column",
]
'''

out_dir = root / "monostudio" / "ui_qt" / "dialog_tier"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "reference.py"
out_path.write_text(header + body + footer, encoding="utf-8")
print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
print(f"extracted tier1 + tier2 (skipped demos at lines {demo_start + 1}-{tier2_start}, {tier2_end + 1}+)")
