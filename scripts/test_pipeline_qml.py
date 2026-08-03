"""
MONOS Pipeline QML — golden harness (grid card stub).

Implementation: monostudio/ui_qt/qml/Monos/Pipeline/
Rule: .cursor/rules/plan_main_view_engine_v2.mdc §4.4, Phase D

Run:
    python scripts/test_pipeline_qml.py
    python scripts/test_pipeline_qml.py --verify-theme
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from monostudio.ui_qt.pipeline_qml_theme import (
    build_pipeline_theme_map,
    configure_pipeline_qml_engine,
    pipeline_harness_qml_url,
)
from monostudio.ui_qt.style import MONOS_COLORS, apply_dark_theme, monos_font


def _load_ui_fonts() -> None:
    for family, weight in (("Inter", QFont.Weight.Normal), ("JetBrains Mono", QFont.Weight.Normal)):
        try:
            monos_font(family, 13, weight)
        except Exception:
            pass


def verify_theme_parity() -> list[str]:
    """Return list of MONOS_COLORS keys whose hex differs from PipelineTheme map."""
    errors: list[str] = []
    m = build_pipeline_theme_map()
    pairs = {
        "contentBg": "content_bg",
        "cardBg": "card_bg",
        "cardHover": "card_hover",
        "cardBorder": "border",
        "cardSelectedBorder": "blue_600",
        "textPrimary": "text_primary",
        "textLabel": "text_label",
        "textMeta": "text_meta",
        "blue600": "blue_600",
        "emerald500": "emerald_500",
        "amber500": "amber_500",
        "red500": "red_500",
    }
    for qml_key, monos_key in pairs.items():
        expected = MONOS_COLORS.get(monos_key, "").lower()
        actual = str(m.get(qml_key, "")).lower()
        if expected != actual:
            errors.append(f"{qml_key}: map={actual} MONOS_COLORS[{monos_key}]={expected}")
    return errors


class PipelineQmlHarnessWindow(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MONOS Pipeline QML Harness")
        self.resize(960, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        hint = QLabel(
            "Pipeline grid harness — PipelineCard.qml (plan §6). "
            "Close window to exit.",
            self,
        )
        hint.setObjectName("DialogHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        self._view = QQuickWidget(self)
        self._view.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        configure_pipeline_qml_engine(self._view.engine())
        self._view.setSource(pipeline_harness_qml_url())
        if self._view.status() != QQuickWidget.Status.Ready:
            err = self._view.errors()
            msg = err[0].toString() if err else "unknown QML error"
            raise RuntimeError(f"QML failed to load: {msg}")
        layout.addWidget(self._view, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="MONOS Pipeline QML harness")
    parser.add_argument(
        "--verify-theme",
        action="store_true",
        help="Check MONOS_COLORS parity vs pipeline_qml_theme map and exit.",
    )
    args = parser.parse_args()

    if args.verify_theme:
        errs = verify_theme_parity()
        if errs:
            print("Theme parity FAILED:", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print("Theme parity OK (MONOS_COLORS <-> build_pipeline_theme_map).")
        return 0

    _load_ui_fonts()
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    w = PipelineQmlHarnessWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
