"""Compact schedule summary for Inspector (asset / shot)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from monostudio.core.models import Asset, Shot
from monostudio.core.project_schedule import read_project_schedule
from monostudio.core.schedule_planner import PlannedBar, summarize_entity_from_ref
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.view_items import ViewItem


class InspectorScheduleBlock(QWidget):
    open_schedule_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorScheduleBlock")
        self._project_root: Path | None = None
        self._active_department: str | None = None
        self._dept_labels: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)
        title = QLabel("SCHEDULE", self)
        title.setObjectName("InspectorSectionTitle")
        title.setFont(monos_font("Inter", 10, QFont.Weight.ExtraBold))
        title.setStyleSheet(f"color: {MONOS_COLORS['text_meta']};")
        self._btn_open = QPushButton("Open", self)
        self._btn_open.setObjectName("DialogSecondaryButton")
        self._btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open.clicked.connect(self.open_schedule_requested.emit)
        hdr.addWidget(title, 1)
        hdr.addWidget(self._btn_open, 0)
        root.addLayout(hdr)

        card = QFrame(self)
        card.setObjectName("InspectorScheduleCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(12, 10, 12, 10)
        card_l.setSpacing(6)

        self._delivery = QLabel("Delivery · —", card)
        self._delivery.setFont(monos_font("Inter", 13, QFont.Weight.Medium))
        self._delivery.setStyleSheet(f"color: {MONOS_COLORS.get('text_primary', '#fafafa')};")

        self._span = QLabel("", card)
        self._span.setFont(monos_font("JetBrains Mono", 11))
        self._span.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')};")
        self._span.setVisible(False)

        self._focus = QLabel("", card)
        self._focus.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        self._focus.setVisible(False)

        self._dept_lines = QLabel("", card)
        self._dept_lines.setWordWrap(True)
        self._dept_lines.setFont(monos_font("JetBrains Mono", 11))
        self._dept_lines.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')};")

        self._empty = QLabel("No plan yet — use Schedule to auto-plan or draw bars.", card)
        self._empty.setWordWrap(True)
        self._empty.setObjectName("DialogHint")

        card_l.addWidget(self._delivery)
        card_l.addWidget(self._span)
        card_l.addWidget(self._focus)
        card_l.addWidget(self._dept_lines)
        card_l.addWidget(self._empty)
        root.addWidget(card)

    def set_dept_labels(self, labels: dict[str, str]) -> None:
        self._dept_labels = dict(labels or {})

    def set_project_root(self, path: Path | str | None) -> None:
        self._project_root = Path(path) if path else None

    def set_active_department(self, department: str | None) -> None:
        self._active_department = (department or "").strip() or None

    def set_item(self, item: ViewItem | None, *, bars: dict[tuple[str, str, str], PlannedBar] | None = None) -> None:
        if item is None or self._project_root is None:
            self._clear()
            return
        ref = item.ref
        if not isinstance(ref, (Asset, Shot)):
            self._clear()
            return

        schedule = read_project_schedule(self._project_root)
        summary = summarize_entity_from_ref(
            self._project_root,
            ref,
            schedule,
            active_department=self._active_department,
        )

        if not summary.has_plan:
            self._delivery.setText("Delivery · —")
            self._delivery.setStyleSheet(f"color: {MONOS_COLORS.get('text_meta', '#71717a')};")
            self._span.setVisible(False)
            self._focus.setVisible(False)
            self._dept_lines.setVisible(False)
            self._empty.setVisible(True)
            return

        self._empty.setVisible(False)
        delivery_txt = summary.delivery.isoformat() if summary.delivery else "—"
        del_color = MONOS_COLORS.get("text_primary", "#fafafa")
        if summary.delivery and summary.delivery < date.today() and summary.any_overdue:
            del_color = "#ef4444"
        self._delivery.setText(f"Delivery · {delivery_txt}")
        self._delivery.setStyleSheet(f"color: {del_color};")

        if summary.span_start and summary.span_end:
            self._span.setText(
                f"{summary.span_start.isoformat()} → {summary.span_end.isoformat()}"
            )
            self._span.setVisible(True)
        else:
            self._span.setVisible(False)

        dep = self._active_department
        if dep and summary.focus_due is not None:
            label = self._dept_labels.get(dep, dep)
            color = "#ef4444" if summary.focus_overdue else MONOS_COLORS.get("text_primary", "#fafafa")
            self._focus.setText(f"{label} · due {summary.focus_due.isoformat()}")
            self._focus.setStyleSheet(f"color: {color};")
            self._focus.setVisible(True)
        else:
            self._focus.setVisible(False)

        kind = "shot" if isinstance(ref, Shot) else "asset"
        try:
            rel = ref.path.resolve().relative_to(self._project_root.resolve()).as_posix()
        except (OSError, ValueError):
            rel = ref.path.as_posix()
        lines: list[str] = []
        entity_bars = sorted(
            [b for k, b in (bars or {}).items() if k[0] == kind and k[1] == rel.replace("\\", "/")],
            key=lambda b: b.due,
        )
        if not entity_bars and bars is None:
            from monostudio.core.schedule_planner import build_planned_bars
            from monostudio.core.models import ProjectIndex

            idx = ProjectIndex(
                root=self._project_root,
                assets=(ref,) if kind == "asset" else (),
                shots=(ref,) if kind == "shot" else (),
            )
            built = build_planned_bars(
                self._project_root,
                idx,
                schedule,
                include_shots=kind == "shot",
                include_assets=kind == "asset",
            )
            entity_bars = sorted(
                [b for k, b in built.items() if k[0] == kind and k[1] == rel.replace("\\", "/")],
                key=lambda b: b.due,
            )
        for bar in entity_bars[:8]:
            tag = self._dept_labels.get(bar.department, bar.department_label or bar.department)
            due_s = bar.due.isoformat()
            if bar.overdue:
                lines.append(f'<span style="color:#ef4444">{tag} · {due_s}</span>')
            elif bar.status == "done":
                lines.append(f'<span style="color:#52525b">{tag} · {due_s}</span>')
            else:
                lines.append(f"{tag} · {due_s}")
        if len(entity_bars) > 8:
            lines.append(f"+{len(entity_bars) - 8} more")
        self._dept_lines.setText("<br>".join(lines) if lines else "")
        self._dept_lines.setVisible(bool(lines))

    def _clear(self) -> None:
        self._delivery.setText("Delivery · —")
        self._span.setVisible(False)
        self._focus.setVisible(False)
        self._dept_lines.setVisible(False)
        self._empty.setVisible(True)
