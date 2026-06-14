"""Shared responsive row helpers for dashboard bento list cards.

Rows use a fixed leading affordance (avatar, dot, …), expanding elided body,
and trailing chips/meta that shrink or hide as the card narrows.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from monostudio.ui_qt.style import monos_font


def style_entity_name_btn(btn: QPushButton, entity_kind: str) -> None:
    btn.setObjectName("DashboardEntityNameBtn")
    btn.setFlat(True)
    tone = "shot" if (entity_kind or "").strip().lower() == "shot" else "asset"
    btn.setProperty("chipTone", tone)
    st = btn.style()
    if st is not None:
        st.unpolish(btn)
        st.polish(btn)


def uniform_dept_chip_width(labels: list[str]) -> int:
    """Preferred width for department chips so rows align when space allows."""
    clean = [(lbl or "").strip() for lbl in labels if (lbl or "").strip()]
    if not clean:
        return 0
    font = monos_font("Inter", 10, QFont.Weight.Bold)
    metrics = QFontMetrics(font)
    pad_border = 16
    return max(metrics.horizontalAdvance(lbl) for lbl in clean) + pad_border


class DashboardElidedLabel(QLabel):
    """Label that elides full text to the current widget width."""

    def __init__(
        self,
        text: str = "",
        *,
        font: QFont | None = None,
        object_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        if font is not None:
            self.setFont(font)
        self._full_text = (text or "").strip()
        self._elide_budget: int | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self._refresh_elide()

    def set_full_text(self, text: str) -> None:
        self._full_text = (text or "").strip()
        self._refresh_elide()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_elide()

    def set_elide_budget(self, width: int | None) -> None:
        self._elide_budget = None if width is None else max(0, int(width))
        self._refresh_elide()

    def _refresh_elide(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            width = self.width()
        if width <= 0 and self._elide_budget is not None:
            width = self._elide_budget
        if width <= 0:
            self.setText(self._full_text)
            return
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
        self.setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")


class DashboardEntityBadges(QWidget):
    """Entity + department chips for dashboard rows; shrinks with card width."""

    _NAME_PAD = 16
    _MIN_NAME_CHIP = 36
    _MIN_DEPT_CHIP = 28

    def __init__(
        self,
        *,
        entity_kind: str,
        entity_name: str,
        department: str,
        dept_label: str,
        dept_chip_width: int = 0,
        on_entity_click: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardEntityBadges")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

        self._full_name = (entity_name or "").strip()
        self._full_dept = (dept_label or "").strip()
        self._dept_font = monos_font("Inter", 10, QFont.Weight.Bold)
        self._name_font = monos_font("Inter", 10, QFont.Weight.ExtraBold)
        self._dept_chip_width = max(0, int(dept_chip_width))
        self._layout_budget: int | None = None
        self._dept_visible = True
        self._hide_dept_compact = False
        self._has_department = bool((department or "").strip())

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._name_btn = QPushButton(self)
        self._name_btn.setFont(self._name_font)
        style_entity_name_btn(self._name_btn, entity_kind)
        self._name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name_btn.setToolTip("Open this asset/shot in the main view")
        if on_entity_click is not None:
            self._name_btn.clicked.connect(on_entity_click)
        lay.addWidget(self._name_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._dept_btn: QPushButton | None = None
        if self._has_department:
            self._dept_btn = QPushButton(dept_label, self)
            self._dept_btn.setObjectName("DashboardNoteDeptBtn")
            self._dept_btn.setFont(self._dept_font)
            self._dept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._dept_btn.setToolTip("Open this asset/shot in the main view")
            if on_entity_click is not None:
                self._dept_btn.clicked.connect(on_entity_click)
            lay.addWidget(self._dept_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._refresh_chips()

    def set_layout_budget(self, budget: int | None) -> None:
        self._layout_budget = None if budget is None else max(self._MIN_NAME_CHIP, int(budget))
        self._refresh_chips()

    def set_compact(self, *, hide_dept: bool, hide_name: bool = False) -> None:
        self._hide_dept_compact = bool(hide_dept)
        if self._dept_btn is not None:
            self._dept_btn.setVisible(self._has_department and not hide_dept)
        self._name_btn.setVisible(not hide_name)
        self._refresh_chips()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_chips()

    def _natural_preferred_width(self) -> int:
        name_metrics = QFontMetrics(self._name_font)
        name_w = name_metrics.horizontalAdvance(self._full_name) + self._NAME_PAD
        name_w = max(self._MIN_NAME_CHIP, name_w)
        spacing = 0
        dept_w = 0
        if (
            self._dept_btn is not None
            and self._has_department
            and self._dept_btn.isVisible()
            and not self._hide_dept_compact
        ):
            dept_metrics = QFontMetrics(self._dept_font)
            preferred = self._dept_chip_width or dept_metrics.horizontalAdvance(self._full_dept) + self._NAME_PAD
            dept_w = max(self._MIN_DEPT_CHIP, preferred)
            spacing = 6
        return name_w + dept_w + spacing

    def _effective_budget(self) -> int:
        if self._layout_budget is not None:
            return self._layout_budget
        width = self.contentsRect().width() or self.width()
        if width > 0:
            return width
        return self._natural_preferred_width()

    def _fit_chip(
        self,
        btn: QPushButton,
        full_text: str,
        *,
        font: QFont,
        budget: int,
        min_w: int,
    ) -> int:
        metrics = QFontMetrics(font)
        text_max = max(8, budget - self._NAME_PAD)
        elided = metrics.elidedText(full_text, Qt.TextElideMode.ElideRight, text_max)
        btn.setText(elided)
        chip_w = min(metrics.horizontalAdvance(elided) + self._NAME_PAD, budget)
        chip_w = max(min_w, chip_w)
        btn.setFixedWidth(chip_w)
        btn.setToolTip(full_text if elided != full_text else btn.toolTip())
        return chip_w

    def _refresh_chips(self) -> None:
        if not self._name_btn.isVisible():
            self.setFixedWidth(0)
            return

        budget = self._effective_budget()
        spacing = 6 if self._dept_btn is not None and self._dept_btn.isVisible() else 0
        show_dept = (
            self._dept_btn is not None
            and self._has_department
            and self._dept_btn.isVisible()
            and not self._hide_dept_compact
        )

        dept_w = 0
        if show_dept and self._dept_btn is not None:
            preferred = self._dept_chip_width or QFontMetrics(self._dept_font).horizontalAdvance(
                self._full_dept
            ) + self._NAME_PAD
            dept_cap = max(self._MIN_DEPT_CHIP, min(preferred, budget - self._MIN_NAME_CHIP - spacing))
            if dept_cap >= self._MIN_DEPT_CHIP:
                dept_w = self._fit_chip(
                    self._dept_btn,
                    self._full_dept,
                    font=self._dept_font,
                    budget=dept_cap,
                    min_w=self._MIN_DEPT_CHIP,
                )
                self._dept_btn.setVisible(True)
            else:
                self._dept_btn.setVisible(False)
                show_dept = False

        name_budget = max(self._MIN_NAME_CHIP, budget - dept_w - (spacing if show_dept else 0))
        name_w = self._fit_chip(
            self._name_btn,
            self._full_name,
            font=self._name_font,
            budget=name_budget,
            min_w=self._MIN_NAME_CHIP,
        )
        total_w = name_w + (dept_w + spacing if show_dept else 0)
        self.setFixedWidth(max(self._MIN_NAME_CHIP, total_w))
        tip = self._full_name if self._name_btn.text() != self._full_name else ""
        self._name_btn.setToolTip(tip or "Open this asset/shot in the main view")


class DashboardResponsiveMixin:
    """Trailing meta / dept chips hide when the host row narrows."""

    _HIDE_TRAILING_META_BELOW = 420
    _HIDE_DEPT_CHIP_BELOW = 260
    _MIN_BODY_WIDTH = 40

    def bind_responsive_parts(
        self,
        *,
        trailing_meta: QLabel | None = None,
        entity_badges: DashboardEntityBadges | None = None,
        leading_width: int = 0,
        body: DashboardElidedLabel | None = None,
        trailing_host: QWidget | None = None,
        body_fills_row: bool = False,
    ) -> None:
        self._trailing_meta = trailing_meta
        self._entity_badges = entity_badges
        self._responsive_body = body
        self._trailing_host = trailing_host
        self._body_fills_row = bool(body_fills_row)
        self._leading_width = max(0, int(leading_width))
        self._apply_responsive_layout()

    def _row_content_width(self) -> int:
        width = int(getattr(self, "width", lambda: 0)())
        layout = getattr(self, "layout", lambda: None)()
        if layout is None:
            return width
        margins = layout.contentsMargins()
        return max(0, width - margins.left() - margins.right())

    def _apply_responsive_layout(self) -> None:
        content_w = self._row_content_width()
        if content_w <= 0:
            return
        trailing_meta = getattr(self, "_trailing_meta", None)
        entity_badges = getattr(self, "_entity_badges", None)
        trailing_host = getattr(self, "_trailing_host", None)
        layout = getattr(self, "layout", lambda: None)()
        spacing = layout.spacing() if layout is not None else 8

        leading = int(getattr(self, "_leading_width", 0))
        body_fills = bool(getattr(self, "_body_fills_row", False))
        trailing_w = 0
        if trailing_host is not None:
            if trailing_meta is not None:
                pin = bool(getattr(self, "_trailing_meta_pinned", False))
                show_meta = pin or content_w >= self._HIDE_TRAILING_META_BELOW
                trailing_meta.setVisible(show_meta)
            hide_dept = content_w < self._HIDE_DEPT_CHIP_BELOW
            if entity_badges is not None:
                entity_badges.set_compact(hide_dept=hide_dept)
                if body_fills:
                    entity_badges.set_layout_budget(None)
                else:
                    meta_w = trailing_meta.sizeHint().width() if trailing_meta is not None and trailing_meta.isVisible() else 0
                    gaps = spacing * 2
                    reserved = leading + self._MIN_BODY_WIDTH + gaps
                    trailing_budget = max(96, content_w - reserved)
                    badge_budget = max(72, trailing_budget - meta_w - (6 if meta_w else 0))
                    entity_badges.set_layout_budget(badge_budget)
            trailing_host.setMaximumWidth(16777215)
            trailing_host.adjustSize()
            trailing_w = trailing_host.sizeHint().width()
        else:
            meta_w = 0
            if trailing_meta is not None:
                pin = bool(getattr(self, "_trailing_meta_pinned", False))
                show_meta = pin or content_w >= self._HIDE_TRAILING_META_BELOW
                trailing_meta.setVisible(show_meta)
                if show_meta:
                    meta_w = trailing_meta.sizeHint().width()
            hide_dept = content_w < self._HIDE_DEPT_CHIP_BELOW
            if entity_badges is not None:
                entity_badges.set_compact(hide_dept=hide_dept)
                gaps = spacing * 2 if meta_w else spacing
                reserved = leading + meta_w + gaps + self._MIN_BODY_WIDTH
                badges_budget = max(72, content_w - reserved)
                entity_badges.set_layout_budget(badges_budget)
                trailing_w = entity_badges.width() + (meta_w + spacing if meta_w else 0)

        body = getattr(self, "_responsive_body", None)
        if body is not None:
            if body_fills:
                gaps = spacing * 2 if trailing_w else spacing
                body_w = max(self._MIN_BODY_WIDTH, content_w - leading - trailing_w - gaps)
                body.setMaximumWidth(16777215)
                body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                if hasattr(body, "set_elide_budget"):
                    body.set_elide_budget(body_w)
                else:
                    body._refresh_elide()
            else:
                gaps = spacing * (2 if trailing_w else 1)
                body_w = max(self._MIN_BODY_WIDTH, content_w - leading - trailing_w - gaps)
                body.setMaximumWidth(body_w)
                body._refresh_elide()
                body.updateGeometry()
