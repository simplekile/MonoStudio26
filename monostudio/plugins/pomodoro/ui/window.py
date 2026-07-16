"""Standalone Focus timer window — countdown + user checklist."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.plugins.pomodoro.engine import EngineSnapshot, Phase, PomodoroEngine
from monostudio.plugins.pomodoro.store import (
    ChecklistItem,
    new_checklist_item,
    read_checklist,
    write_auto_start_break,
    write_always_on_top,
    write_checklist,
    write_checklist_visible,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu, monos_font

_BG = MONOS_COLORS.get("panel", "#18181b")
_RADIUS = 12
_TIME_PX = 64
_WIN_W = 300
_CHROME_BTN = 28
_CHROME_ICON = 18
_ADD_BTN = 32
_ADD_ICON = 18


def _fmt_mmss(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _phase_label(snap: EngineSnapshot) -> str:
    if snap.phase == Phase.IDLE:
        return "READY"
    if snap.phase == Phase.FOCUS:
        base = f"FOCUS · #{snap.session_focus_index}"
    elif snap.phase == Phase.SHORT_BREAK:
        base = "SHORT BREAK"
    else:
        base = "LONG BREAK"
    if snap.paused:
        return f"{base} · PAUSED"
    return base


class PomodoroWindow(QWidget):
    """Non-modal utility window for the Focus timer plugin."""

    closed = Signal()
    prefs_changed = Signal()

    def __init__(self, engine: PomodoroEngine, settings, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setObjectName("PomodoroWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(_WIN_W)

        self._engine = engine
        self._settings = settings
        self._drag_pos: QPoint | None = None
        self._items: list[ChecklistItem] = read_checklist(settings)
        self._suppress_check = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 16)
        root.setSpacing(0)

        # --- Chrome: list toggle · pin · close ---
        chrome = QHBoxLayout()
        chrome.setContentsMargins(0, 0, 0, 0)
        chrome.setSpacing(4)

        self._list_btn = QToolButton(self)
        self._list_btn.setObjectName("PomodoroListBtn")
        self._list_btn.setCheckable(True)
        self._list_btn.setToolTip("Show tasks")
        self._list_btn.setFixedSize(_CHROME_BTN, _CHROME_BTN)
        self._list_btn.setIconSize(QSize(_CHROME_ICON, _CHROME_ICON))
        self._list_btn.toggled.connect(self._on_list_toggled)
        chrome.addWidget(self._list_btn, 0)
        chrome.addStretch(1)

        self._pin_btn = QToolButton(self)
        self._pin_btn.setObjectName("PomodoroPinBtn")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setToolTip("Always on top")
        self._pin_btn.setFixedSize(_CHROME_BTN, _CHROME_BTN)
        self._pin_btn.setIconSize(QSize(_CHROME_ICON, _CHROME_ICON))
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        chrome.addWidget(self._pin_btn, 0)

        close_btn = QToolButton(self)
        close_btn.setObjectName("PomodoroCloseBtn")
        close_btn.setIcon(lucide_icon("x", size=_CHROME_ICON, color_hex="#a1a1aa"))
        close_btn.setIconSize(QSize(_CHROME_ICON, _CHROME_ICON))
        close_btn.setFixedSize(_CHROME_BTN, _CHROME_BTN)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.hide)
        chrome.addWidget(close_btn, 0)
        root.addLayout(chrome)

        # --- Hero: phase + large countdown ---
        hero = QVBoxLayout()
        hero.setContentsMargins(0, 8, 0, 16)
        hero.setSpacing(4)

        self._phase_label = QLabel("READY", self)
        self._phase_label.setObjectName("PomodoroPhase")
        self._phase_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._phase_label.setFont(monos_font("Inter", 10, QFont.Weight.Bold))
        hero.addWidget(self._phase_label)

        self._time_label = QLabel("25:00", self)
        self._time_label.setObjectName("PomodoroTime")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setMinimumHeight(78)
        self._time_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tf = monos_font("JetBrains Mono", _TIME_PX, QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -2.0)
        self._time_label.setFont(tf)
        hero.addWidget(self._time_label)

        self._progress = QProgressBar(self)
        self._progress.setObjectName("PomodoroProgress")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(3)
        prog_wrap = QHBoxLayout()
        prog_wrap.setContentsMargins(24, 8, 24, 0)
        prog_wrap.addWidget(self._progress)
        hero.addLayout(prog_wrap)
        root.addLayout(hero)

        # --- Primary controls (compact) ---
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        self._start_btn = QPushButton("Start", self)
        self._start_btn.setObjectName("DialogPrimaryButton")
        self._start_btn.setFixedHeight(32)
        self._start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self._start_btn, 1)

        self._pause_btn = QToolButton(self)
        self._pause_btn.setObjectName("PomodoroIconBtn")
        self._pause_btn.setFixedSize(32, 32)
        self._pause_btn.setToolTip("Pause")
        self._pause_btn.clicked.connect(self._on_pause)
        btn_row.addWidget(self._pause_btn, 0)

        self._skip_btn = QToolButton(self)
        self._skip_btn.setObjectName("PomodoroIconBtn")
        self._skip_btn.setIcon(lucide_icon("skip-forward", size=16, color_hex="#a1a1aa"))
        self._skip_btn.setFixedSize(32, 32)
        self._skip_btn.setToolTip("Skip")
        self._skip_btn.clicked.connect(self._on_skip)
        btn_row.addWidget(self._skip_btn, 0)

        self._reset_btn = QToolButton(self)
        self._reset_btn.setObjectName("PomodoroIconBtn")
        self._reset_btn.setIcon(lucide_icon("refresh-cw", size=16, color_hex="#a1a1aa"))
        self._reset_btn.setFixedSize(32, 32)
        self._reset_btn.setToolTip("Reset")
        self._reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(self._reset_btn, 0)
        root.addLayout(btn_row)

        # --- Checklist panel (toggleable) ---
        self._checklist_panel = QWidget(self)
        self._checklist_panel.setObjectName("PomodoroChecklistPanel")
        panel_l = QVBoxLayout(self._checklist_panel)
        panel_l.setContentsMargins(0, 16, 0, 0)
        panel_l.setSpacing(0)

        self._list = QListWidget(self._checklist_panel)
        self._list.setObjectName("PomodoroChecklist")
        self._list.setMinimumHeight(96)
        self._list.setMaximumHeight(160)
        self._list.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.itemSelectionChanged.connect(self._sync_delete_btn)
        panel_l.addWidget(self._list, 0)

        del_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self._list)
        del_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        del_shortcut.activated.connect(self._delete_selected)
        back_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self._list)
        back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        back_shortcut.activated.connect(self._delete_selected)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 6, 0, 0)
        add_row.setSpacing(6)
        self._add_edit = QLineEdit(self._checklist_panel)
        self._add_edit.setObjectName("PomodoroAddEdit")
        self._add_edit.setPlaceholderText("Add…")
        self._add_edit.setFixedHeight(_ADD_BTN)
        self._add_edit.returnPressed.connect(self._on_add)
        add_row.addWidget(self._add_edit, 1)
        add_btn = QToolButton(self._checklist_panel)
        add_btn.setObjectName("PomodoroIconBtn")
        add_btn.setIcon(lucide_icon("plus", size=_ADD_ICON, color_hex="#a1a1aa"))
        add_btn.setIconSize(QSize(_ADD_ICON, _ADD_ICON))
        add_btn.setFixedSize(_ADD_BTN, _ADD_BTN)
        add_btn.setToolTip("Add")
        add_btn.clicked.connect(self._on_add)
        add_row.addWidget(add_btn, 0)

        self._delete_btn = QToolButton(self._checklist_panel)
        self._delete_btn.setObjectName("PomodoroIconBtn")
        self._delete_btn.setIcon(lucide_icon("trash-2", size=_ADD_ICON, color_hex="#a1a1aa"))
        self._delete_btn.setIconSize(QSize(_ADD_ICON, _ADD_ICON))
        self._delete_btn.setFixedSize(_ADD_BTN, _ADD_BTN)
        self._delete_btn.setToolTip("Delete selected task")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        add_row.addWidget(self._delete_btn, 0)
        panel_l.addLayout(add_row)

        clear_row = QHBoxLayout()
        clear_row.setContentsMargins(0, 8, 0, 0)
        clear_row.addStretch(1)
        clear_btn = QPushButton("Clear done", self._checklist_panel)
        clear_btn.setObjectName("PomodoroGhostBtn")
        clear_btn.setFlat(True)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_done)
        clear_row.addWidget(clear_btn, 0)
        panel_l.addLayout(clear_row)

        root.addWidget(self._checklist_panel, 0)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 10, 0, 0)
        foot.setSpacing(8)
        self._auto_break_cb = QCheckBox("Auto-start break", self)
        self._auto_break_cb.setObjectName("PomodoroAutoBreakCheck")
        self._auto_break_cb.toggled.connect(self._on_auto_break_toggled)
        foot.addWidget(self._auto_break_cb, 1)
        root.addLayout(foot)

        prefs = engine.prefs()
        self._auto_break_cb.blockSignals(True)
        self._auto_break_cb.setChecked(prefs.auto_start_break)
        self._auto_break_cb.blockSignals(False)
        self._pin_btn.blockSignals(True)
        self._pin_btn.setChecked(prefs.always_on_top)
        self._pin_btn.blockSignals(False)
        self._pin_btn.setIcon(
            lucide_icon(
                "pin",
                size=_CHROME_ICON,
                color_hex="#60a5fa" if prefs.always_on_top else "#a1a1aa",
            )
        )
        self._pin_btn.setIconSize(QSize(_CHROME_ICON, _CHROME_ICON))
        self._apply_always_on_top(prefs.always_on_top)
        self._list_btn.blockSignals(True)
        self._list_btn.setChecked(prefs.checklist_visible)
        self._list_btn.blockSignals(False)
        self._apply_checklist_visible(prefs.checklist_visible, persist=False, animate=False)
        self._set_pause_icon(paused=False, enabled=False)

        self._rebuild_list()
        self.refresh_from_engine()
        engine.state_changed.connect(self.refresh_from_engine)

    def reload_prefs_ui(self) -> None:
        prefs = self._engine.prefs()
        self._auto_break_cb.blockSignals(True)
        self._auto_break_cb.setChecked(prefs.auto_start_break)
        self._auto_break_cb.blockSignals(False)
        self._pin_btn.blockSignals(True)
        self._pin_btn.setChecked(prefs.always_on_top)
        self._pin_btn.blockSignals(False)
        self._apply_always_on_top(prefs.always_on_top)
        self._list_btn.blockSignals(True)
        self._list_btn.setChecked(prefs.checklist_visible)
        self._list_btn.blockSignals(False)
        self._apply_checklist_visible(prefs.checklist_visible, persist=False, animate=False)
        self.refresh_from_engine()

    def refresh_from_engine(self) -> None:
        snap = self._engine.snapshot()
        prefs = self._engine.prefs()
        if snap.phase == Phase.IDLE:
            display_sec = prefs.focus_minutes * 60
        else:
            display_sec = snap.remaining_sec
        self._time_label.setText(_fmt_mmss(display_sec))
        self._phase_label.setText(_phase_label(snap))
        if snap.total_sec > 0 and snap.phase != Phase.IDLE:
            done = snap.total_sec - snap.remaining_sec
            self._progress.setValue(int(100 * done / snap.total_sec))
        else:
            self._progress.setValue(0)

        active = snap.phase != Phase.IDLE
        self._start_btn.setText("Start" if not active else "Restart")
        self._set_pause_icon(paused=snap.paused, enabled=active)
        self._set_control_icon(self._skip_btn, "skip-forward", enabled=active)
        self._set_control_icon(self._reset_btn, "refresh-cw", enabled=active)

        if snap.phase == Phase.FOCUS:
            color = MONOS_COLORS.get("blue_400", "#60a5fa")
            chunk = "#3b82f6"
        elif snap.phase in (Phase.SHORT_BREAK, Phase.LONG_BREAK):
            color = "#34d399"
            chunk = "#34d399"
        else:
            color = "#fafafa"
            chunk = "#3b82f6"
        self._time_label.setStyleSheet(f"color: {color}; background: transparent;")
        self._progress.setStyleSheet(
            "QProgressBar#PomodoroProgress {"
            " background: rgba(255,255,255,0.06); border: none; border-radius: 1px; max-height: 3px;"
            "}"
            f"QProgressBar#PomodoroProgress::chunk {{ background: {chunk}; border-radius: 1px; }}"
        )

    def _set_control_icon(self, btn: QToolButton, name: str, *, enabled: bool) -> None:
        color = "#a1a1aa" if enabled else "#3f3f46"
        btn.setIcon(lucide_icon(name, size=16, color_hex=color))
        btn.setIconSize(QSize(16, 16))
        btn.setEnabled(enabled)

    def _set_pause_icon(self, *, paused: bool, enabled: bool) -> None:
        name = "play" if paused else "pause"
        tip = "Resume" if paused else "Pause"
        color = "#a1a1aa" if enabled else "#3f3f46"
        self._pause_btn.setIcon(lucide_icon(name, size=16, color_hex=color))
        self._pause_btn.setIconSize(QSize(16, 16))
        self._pause_btn.setToolTip(tip)
        self._pause_btn.setEnabled(enabled)

    def _rebuild_list(self) -> None:
        self._suppress_check = True
        self._list.clear()
        for item in self._items:
            row = QListWidgetItem(item.text)
            row.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEditable
            )
            row.setCheckState(
                Qt.CheckState.Checked if item.done else Qt.CheckState.Unchecked
            )
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self._list.addItem(row)
        self._suppress_check = False
        self._sync_delete_btn()

    def _sync_delete_btn(self) -> None:
        btn = getattr(self, "_delete_btn", None)
        if btn is None:
            return
        btn.setEnabled(self._list.currentItem() is not None)

    def _on_list_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        self._list.setCurrentItem(item)
        menu = MonosMenu(self)
        act = menu.addAction(lucide_icon("trash-2", size=14, color_hex="#a1a1aa"), "Delete")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is act:
            self._delete_selected()

    def _delete_selected(self) -> None:
        row = self._list.currentItem()
        if row is None:
            return
        item_id = row.data(Qt.ItemDataRole.UserRole)
        self._persist_checklist()
        self._items = [i for i in self._items if i.id != item_id]
        self._rebuild_list()
        self._persist_checklist()
        self._sync_delete_btn()

    def _persist_checklist(self) -> None:
        for i in range(self._list.count()):
            row = self._list.item(i)
            if row is None:
                continue
            item_id = row.data(Qt.ItemDataRole.UserRole)
            text = (row.text() or "").strip()
            done = row.checkState() == Qt.CheckState.Checked
            for it in self._items:
                if it.id == item_id:
                    it.text = text or it.text
                    it.done = done
                    break
        if self._settings is not None:
            write_checklist(self._settings, self._items)

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        if self._suppress_check:
            return
        self._persist_checklist()

    def _on_add(self) -> None:
        text = (self._add_edit.text() or "").strip()
        if not text:
            return
        self._items.append(new_checklist_item(text))
        self._add_edit.clear()
        self._rebuild_list()
        self._persist_checklist()

    def _clear_done(self) -> None:
        self._persist_checklist()
        self._items = [i for i in self._items if not i.done]
        self._rebuild_list()
        self._persist_checklist()

    def _stop_alert_sound(self) -> None:
        from monostudio.plugins.pomodoro.sound import stop_alert_sound

        stop_alert_sound()

    def _on_start(self) -> None:
        self._stop_alert_sound()
        self._engine.start_focus()

    def _on_pause(self) -> None:
        self._stop_alert_sound()
        self._engine.toggle_pause()

    def _on_skip(self) -> None:
        self._stop_alert_sound()
        self._engine.skip()

    def _on_reset(self) -> None:
        self._stop_alert_sound()
        snap = self._engine.snapshot()
        if snap.phase != Phase.IDLE and (snap.total_sec - snap.remaining_sec) >= 120:
            r = QMessageBox.question(
                self,
                "Reset timer",
                "Reset the current session?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        self._engine.reset()

    def _on_list_toggled(self, checked: bool) -> None:
        self._apply_checklist_visible(bool(checked), persist=True, animate=True)

    def _apply_checklist_visible(
        self,
        visible: bool,
        *,
        persist: bool,
        animate: bool,
    ) -> None:
        del animate  # reserved
        self._checklist_panel.setVisible(visible)
        color = "#60a5fa" if visible else "#a1a1aa"
        self._list_btn.setIcon(
            lucide_icon("square-check", size=_CHROME_ICON, color_hex=color)
        )
        self._list_btn.setIconSize(QSize(_CHROME_ICON, _CHROME_ICON))
        self._list_btn.setToolTip("Hide tasks" if visible else "Show tasks")
        prefs = self._engine.prefs()
        prefs.checklist_visible = bool(visible)
        self._engine.set_prefs(prefs)
        if persist and self._settings is not None:
            write_checklist_visible(self._settings, bool(visible))
            self.prefs_changed.emit()
        self.setFixedWidth(_WIN_W)
        self.adjustSize()

    def _on_auto_break_toggled(self, checked: bool) -> None:
        prefs = self._engine.prefs()
        prefs.auto_start_break = bool(checked)
        self._engine.set_prefs(prefs)
        if self._settings is not None:
            write_auto_start_break(self._settings, bool(checked))
        self.prefs_changed.emit()

    def _on_pin_toggled(self, checked: bool) -> None:
        prefs = self._engine.prefs()
        prefs.always_on_top = bool(checked)
        self._engine.set_prefs(prefs)
        if self._settings is not None:
            write_always_on_top(self._settings, bool(checked))
        self._pin_btn.setIcon(
            lucide_icon(
                "pin",
                size=_CHROME_ICON,
                color_hex="#60a5fa" if checked else "#a1a1aa",
            )
        )
        self._pin_btn.setIconSize(QSize(_CHROME_ICON, _CHROME_ICON))
        self._apply_always_on_top(bool(checked))
        self.prefs_changed.emit()

    def _apply_always_on_top(self, on: bool) -> None:
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        visible = self.isVisible()
        self.setWindowFlags(flags | Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        if visible:
            self.show()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._persist_checklist()
        super().hideEvent(event)
        self.closed.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), _RADIUS, _RADIUS)
        p.fillPath(path, QColor(_BG))
        p.setPen(QColor(63, 63, 70))
        p.drawPath(path)
        p.end()
