"""Explorer-style path bar for Inbox/Outbox file tree (back, forward, crumbs, path edit)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, monos_font


def _crumb_label(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return "Folder"
    return text.replace("_", " ")


class _ClickableStretch(QWidget):
    """Fills empty space in the address bar; click to edit full path."""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(8)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class InboxBrowseBar(QWidget):
    """Back / forward + Windows Explorer-style address bar."""

    navigate_requested = Signal(object)  # Path

    _CRUMB_PAD_X = 6
    _SEP_W = 14

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InboxBrowseBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._date_root = Path()
        self._current = Path()
        self._on_back_cb = None
        self._on_forward_cb = None
        self._all_segments: list[tuple[str, Path | None]] = []
        self._edit_mode = False

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(28)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._btn_back = self._make_nav_button("chevron-left", "Back")
        self._btn_forward = self._make_nav_button("chevron-right", "Forward")
        self._btn_back.clicked.connect(self._on_back)
        self._btn_forward.clicked.connect(self._on_forward)
        lay.addWidget(self._btn_back, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._btn_forward, 0, Qt.AlignmentFlag.AlignVCenter)

        self._path_field = QFrame(self)
        self._path_field.setObjectName("InboxExplorerPathField")
        self._path_field.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._path_field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._path_field.setFixedHeight(24)
        field_lay = QVBoxLayout(self._path_field)
        field_lay.setContentsMargins(6, 0, 6, 0)
        field_lay.setSpacing(0)

        self._stack = QStackedWidget(self._path_field)
        field_lay.addWidget(self._stack)

        self._crumb_row = QWidget(self._stack)
        self._crumb_row.setFixedHeight(24)
        crumb_lay = QHBoxLayout(self._crumb_row)
        crumb_lay.setContentsMargins(0, 0, 0, 0)
        crumb_lay.setSpacing(0)

        self._overflow_btn = QToolButton(self._crumb_row)
        self._overflow_btn.setObjectName("InboxBrowseOverflowBtn")
        self._overflow_btn.setText("…")
        self._overflow_btn.setToolTip("Show parent folders")
        self._overflow_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overflow_btn.setVisible(False)
        self._overflow_btn.setAutoRaise(True)
        self._overflow_btn.setFixedHeight(22)
        crumb_lay.addWidget(self._overflow_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._crumb_host = QWidget(self._crumb_row)
        self._crumb_host.setObjectName("InboxBrowseCrumbHost")
        self._crumb_lay = QHBoxLayout(self._crumb_host)
        self._crumb_lay.setContentsMargins(0, 0, 0, 0)
        self._crumb_lay.setSpacing(0)
        crumb_lay.addWidget(self._crumb_host, 0)

        self._click_stretch = _ClickableStretch(self._crumb_row)
        self._click_stretch.clicked.connect(self._enter_edit_mode)
        crumb_lay.addWidget(self._click_stretch, 1)

        self._path_edit = QLineEdit(self._stack)
        self._path_edit.setObjectName("InboxExplorerPathEdit")
        self._path_edit.setFixedHeight(24)
        self._path_edit.setFont(monos_font("JetBrains Mono", 11, QFont.Weight.Normal))
        self._path_edit.returnPressed.connect(self._commit_edit)
        self._path_edit.editingFinished.connect(self._on_edit_finished)
        self._path_edit.installEventFilter(self)
        self._committing_edit = False

        self._stack.addWidget(self._crumb_row)
        self._stack.addWidget(self._path_edit)
        self._stack.setCurrentWidget(self._crumb_row)

        lay.addWidget(self._path_field, 1)

    def _make_nav_button(self, icon_name: str, tip: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("InboxBrowseNavButton")
        btn.setToolTip(tip)
        btn.setAutoRaise(False)
        btn.setFixedSize(24, 24)
        btn.setIconSize(QSize(16, 16))
        icon_muted = MONOS_COLORS.get("text_label", "#a1a1aa")
        ic = lucide_icon(icon_name, size=16, color_hex=icon_muted)
        if not ic.isNull():
            btn.setIcon(ic)
        return btn

    def set_handlers(self, *, on_back, on_forward) -> None:
        self._on_back_cb = on_back
        self._on_forward_cb = on_forward

    def set_state(
        self,
        *,
        date_root: Path,
        current: Path,
        can_back: bool,
        can_forward: bool,
    ) -> None:
        self._date_root = Path(date_root)
        self._current = Path(current)
        self._btn_back.setEnabled(can_back)
        self._btn_forward.setEnabled(can_forward)
        if self._edit_mode:
            self._path_edit.setText(str(self._current))
        else:
            self._rebuild_segments()
            QTimer.singleShot(0, self._relayout_crumbs)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._edit_mode:
            self._relayout_crumbs()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._path_edit and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._exit_edit_mode()
                return True
        return super().eventFilter(obj, event)

    def _on_back(self) -> None:
        if callable(self._on_back_cb):
            self._on_back_cb()

    def _on_forward(self) -> None:
        if callable(self._on_forward_cb):
            self._on_forward_cb()

    def _enter_edit_mode(self) -> None:
        if self._edit_mode:
            return
        self._edit_mode = True
        self._path_edit.setText(str(self._current))
        self._stack.setCurrentWidget(self._path_edit)
        self._path_edit.setFocus(Qt.FocusReason.MouseFocusReason)
        self._path_edit.selectAll()

    def _exit_edit_mode(self) -> None:
        if not self._edit_mode:
            return
        self._edit_mode = False
        self._stack.setCurrentWidget(self._crumb_row)
        QTimer.singleShot(0, self._relayout_crumbs)

    def _commit_edit(self) -> None:
        raw = self._path_edit.text().strip().strip('"')
        if not raw:
            self._exit_edit_mode()
            return
        path = Path(raw)
        if not path.is_absolute():
            path = self._date_root / raw
        if not path.is_dir():
            self._path_edit.selectAll()
            return
        try:
            path.resolve().relative_to(self._date_root.resolve())
        except ValueError:
            self._path_edit.selectAll()
            return
        self._committing_edit = True
        self._exit_edit_mode()
        self.navigate_requested.emit(path)

    def _on_edit_finished(self) -> None:
        if self._committing_edit:
            self._committing_edit = False
            return
        if self._edit_mode:
            self._exit_edit_mode()

    def _build_segments(self) -> list[tuple[str, Path | None]]:
        date_root = self._date_root
        current = self._current
        try:
            date_res = date_root.resolve()
            cur_res = current.resolve()
        except OSError:
            date_res = date_root
            cur_res = current

        root_label = _crumb_label(date_root.name)
        segments: list[tuple[str, Path | None]] = [(root_label, date_root)]
        try:
            rel = cur_res.relative_to(date_res)
        except ValueError:
            rel_parts: tuple[str, ...] = ()
        else:
            rel_parts = rel.parts

        acc = date_root
        for i, part in enumerate(rel_parts):
            acc = acc / part
            label = _crumb_label(part)
            if i == len(rel_parts) - 1:
                segments.append((label, None))
            else:
                segments.append((label, acc))
        if not rel_parts:
            segments = [(root_label, None)]
        return segments

    def _clear_crumb_widgets(self) -> None:
        while self._crumb_lay.count():
            item = self._crumb_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _measure_segment(self, fm: QFontMetrics, label: str, *, with_sep: bool) -> int:
        w = fm.horizontalAdvance(label) + self._CRUMB_PAD_X * 2
        if with_sep:
            w += self._SEP_W
        return w

    def _relayout_crumbs(self) -> None:
        if self._edit_mode:
            return
        self._all_segments = self._build_segments()
        self._clear_crumb_widgets()

        fm = QFontMetrics(monos_font("Inter", 11, QFont.Weight.Medium))
        overflow_w = 28 if len(self._all_segments) > 2 else 0
        available = max(80, self._path_field.width() - overflow_w - 16)

        if len(self._all_segments) == 1:
            label, _ = self._all_segments[0]
            self._overflow_btn.setVisible(False)
            self._add_current(fm, label, available)
            return

        visible_count = 0
        used = 0
        for i in range(len(self._all_segments) - 1, -1, -1):
            label, _ = self._all_segments[i]
            need_sep = visible_count > 0
            w = self._measure_segment(fm, label, with_sep=need_sep)
            if visible_count > 0 and used + w > available:
                break
            if visible_count == 0 and w > available:
                visible_count = 1
                used = w
                break
            used += w
            visible_count += 1

        if visible_count <= 0:
            visible_count = 1

        hidden = self._all_segments[: len(self._all_segments) - visible_count]
        visible = self._all_segments[len(self._all_segments) - visible_count :]

        if hidden:
            self._overflow_btn.setVisible(True)
            menu = QMenu(self._overflow_btn)
            for label, path in hidden:
                if path is None:
                    continue
                act = menu.addAction(label)
                target = Path(path)
                act.triggered.connect(lambda _checked=False, p=target: self.navigate_requested.emit(p))
            self._overflow_btn.setMenu(menu)
        else:
            self._overflow_btn.setVisible(False)
            self._overflow_btn.setMenu(None)

        for i, (label, path) in enumerate(visible):
            if i > 0:
                self._add_sep()
            if path is None:
                self._add_current(fm, label, available)
            else:
                self._add_link(label, path)

    def _rebuild_segments(self) -> None:
        self._all_segments = self._build_segments()

    def _add_sep(self) -> None:
        sep = QLabel("›", self._crumb_host)
        sep.setObjectName("InboxBrowseCrumbSep")
        sep.setFixedWidth(self._SEP_W)
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep.setFont(monos_font("Inter", 10))
        self._crumb_lay.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)

    def _add_link(self, label: str, path: Path) -> None:
        btn = QPushButton(label, self._crumb_host)
        btn.setObjectName("InboxBrowseCrumbLink")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        target = Path(path)
        btn.setToolTip(str(target))
        btn.clicked.connect(lambda _checked=False, p=target: self.navigate_requested.emit(p))
        self._crumb_lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _add_current(self, fm: QFontMetrics, label: str, max_w: int) -> None:
        elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, max(40, max_w))
        lb = QLabel(elided, self._crumb_host)
        lb.setObjectName("InboxBrowseCrumbCurrent")
        lb.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
        lb.setToolTip(label if elided != label else "")
        self._crumb_lay.addWidget(lb, 0, Qt.AlignmentFlag.AlignVCenter)
