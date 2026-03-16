from __future__ import annotations

from PySide6.QtCore import Qt, QRect, Signal, QPoint
from PySide6.QtGui import QFont, QPainter, QPainterPath, QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


class ImportantNotificationBanner(QFrame):
    """
    Persistent, non-modal banner for important notifications (update available, walkthrough, ...).
    - Anchored near the top of the main window, below the TopBar.
    - Only dismissed when user clicks the X button.
    """

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ImportantNotificationBanner")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAutoFillBackground(False)
        # Arrow (callout tail) position in local coords; updated by geometry helper.
        self._arrow_x: int | None = None
        self._arrow_up: bool = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(10)

        icon = lucide_icon("info", size=18, color_hex=MONOS_COLORS["blue_400"])
        icon_label = QLabel(self)
        icon_label.setPixmap(icon.pixmap(18, 18))
        icon_label.setScaledContents(False)
        layout.addWidget(icon_label, 0, Qt.AlignVCenter)

        self._message_label = QLabel(self)
        self._message_label.setObjectName("ImportantNotificationBannerMessage")
        self._message_label.setFont(monos_font(point_size=13, weight=QFont.Weight.Medium))
        self._message_label.setStyleSheet(
            "color: #ffffff; background: transparent; border: none;"
        )
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label, 1, Qt.AlignVCenter)

        close_btn = QToolButton(self)
        close_btn.setObjectName("ImportantNotificationBannerCloseButton")
        close_btn.setIcon(lucide_icon("x", size=16, color_hex="#ffffff"))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setAutoRaise(True)
        close_btn.clicked.connect(self._on_close_clicked)
        layout.addWidget(close_btn, 0, Qt.AlignVCenter)

        # Simple dark panel spec; detailed styling can be moved to QSS later.
        self.setStyleSheet(
            """
            QFrame#ImportantNotificationBanner {
                /* Background drawn in paintEvent (rounded + arrow). */
                background-color: transparent;
                border: none;
            }
            QToolButton#ImportantNotificationBannerCloseButton {
                border: none;
                background: transparent;
                padding: 4px;
                border-radius: 6px;
            }
            QToolButton#ImportantNotificationBannerCloseButton:hover {
                background: rgba(15, 23, 42, 0.18);
            }
            """
        )

    def set_message(self, message: str) -> None:
        self._message_label.setText(message)
        self._message_label.adjustSize()
        self.adjustSize()

    def update_geometry_for_parent(self, anchor_widget: QWidget | None = None) -> None:
        """
        Position banner either:
        - As a callout under anchor_widget (update button) with arrow pointing up, or
        - Centered under top bar if no anchor.
        """
        parent = self.parent()
        if not isinstance(parent, QWidget):
            return
        prect = parent.rect()
        if prect.isEmpty():
            return
        margin = 24
        hint = self.sizeHint()
        # Bubble width ôm theo nội dung, giới hạn bởi max 520px và trừ margin hai bên.
        max_allowed = prect.width() - margin * 2
        bubble_width = min(hint.width(), 520, max_allowed if max_allowed > 0 else hint.width())
        height = hint.height()

        if anchor_widget is not None and anchor_widget.isVisible():
            # Map anchor bottom + center X into parent coords.
            rect = anchor_widget.rect()
            center_global = anchor_widget.mapToGlobal(rect.center())
            bottom_global = anchor_widget.mapToGlobal(rect.bottomLeft())
            center_in_parent = parent.mapFromGlobal(center_global)
            bottom_in_parent = parent.mapFromGlobal(bottom_global)

            # Place banner một khoảng dưới đáy nút update để arrow tách rõ khỏi TopBar.
            x = max(
                prect.x() + margin,
                min(center_in_parent.x() - bubble_width // 2, prect.right() - margin - bubble_width),
            )
            y = bottom_in_parent.y() + 16
            self._arrow_up = True
            # Arrow x in local coords.
            self._arrow_x = center_in_parent.x() - x
        else:
            # Fallback: centered under TopBar (56px).
            x = prect.x() + (prect.width() - bubble_width) // 2
            y = prect.y() + 56 + 8
            self._arrow_up = False
            self._arrow_x = None

        self.setGeometry(QRect(x, y, bubble_width, height))

    def _on_close_clicked(self) -> None:
        self.hide()
        self.closed.emit()
        self.deleteLater()

    def paintEvent(self, event) -> None:
        """Draw rounded rectangle + optional small arrow pointing to anchor."""
        r = self.rect()
        if r.isEmpty():
            return
        radius = 10
        arrow_h = 8
        arrow_w = 14
        bg = QColor("#2563eb")

        top = r.top()
        left = r.left()
        right = r.right()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)

        # 1) Draw arrow first (background layer)
        if self._arrow_up and self._arrow_x is not None:
            ax = max(left + radius, min(right - radius, left + self._arrow_x))
            tip = QPoint(ax, top)
            base_y = top + arrow_h
            left_base = QPoint(ax - arrow_w // 2, base_y)
            right_base = QPoint(ax + arrow_w // 2, base_y)
            arrow_path = QPainterPath()
            arrow_path.moveTo(tip)
            arrow_path.lineTo(left_base)
            arrow_path.lineTo(right_base)
            arrow_path.closeSubpath()
            p.drawPath(arrow_path)

        # 2) Draw rounded bubble on top so its fill covers any arrow shadow at the join
        if self._arrow_up and self._arrow_x is not None:
            overlap = 2
            bubble_rect = QRect(left, top + arrow_h - overlap, r.width(), r.height() - arrow_h + overlap)
        else:
            bubble_rect = QRect(left, top, r.width(), r.height() - arrow_h)

        bubble_path = QPainterPath()
        bubble_path.addRoundedRect(bubble_rect, radius, radius)
        p.drawPath(bubble_path)
        p.end()
        super().paintEvent(event)

