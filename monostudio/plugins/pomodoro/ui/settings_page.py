"""Pomodoro settings page — durations, notify toggles, custom sound."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from monostudio.plugins.pomodoro.sound import play_alert_sound, sound_file_filter
from monostudio.plugins.pomodoro.store import PomodoroPrefs, read_prefs, write_prefs
from monostudio.ui_qt.settings_section_widgets import (
    add_settings_section,
    style_settings_line_edit,
    style_settings_spin,
)
from monostudio.ui_qt.style import monos_font


class PomodoroSettingsPage(QWidget):
    """Embeddable Settings → General → Focus timer content."""

    def __init__(self, settings: QSettings | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        prefs = read_prefs(settings)

        scroll = QScrollArea(self)
        scroll.setObjectName("SettingsPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 16)
        layout.setSpacing(16)

        dur_card, dur_l = add_settings_section(
            inner,
            "Durations",
            "Classic Pomodoro lengths. Changes apply to the next phase you start.",
        )
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._focus_spin = QSpinBox(dur_card)
        self._focus_spin.setRange(1, 180)
        self._focus_spin.setSuffix(" min")
        self._focus_spin.setValue(prefs.focus_minutes)
        style_settings_spin(self._focus_spin, width=100)
        form.addRow("Focus", self._focus_spin)

        self._short_spin = QSpinBox(dur_card)
        self._short_spin.setRange(1, 60)
        self._short_spin.setSuffix(" min")
        self._short_spin.setValue(prefs.short_break_minutes)
        style_settings_spin(self._short_spin, width=100)
        form.addRow("Short break", self._short_spin)

        self._long_spin = QSpinBox(dur_card)
        self._long_spin.setRange(1, 60)
        self._long_spin.setSuffix(" min")
        self._long_spin.setValue(prefs.long_break_minutes)
        style_settings_spin(self._long_spin, width=100)
        form.addRow("Long break", self._long_spin)

        self._every_spin = QSpinBox(dur_card)
        self._every_spin.setRange(1, 12)
        self._every_spin.setValue(prefs.long_break_every)
        style_settings_spin(self._every_spin, width=100)
        form.addRow("Long break every N focuses", self._every_spin)

        dur_l.addLayout(form)
        layout.addWidget(dur_card)

        beh_card, beh_l = add_settings_section(
            inner,
            "Behavior",
            "When a focus ends, optionally start the break automatically. "
            "The next focus never starts by itself.",
        )
        self._auto_break_cb = QCheckBox("Auto-start break after focus", beh_card)
        self._auto_break_cb.setChecked(prefs.auto_start_break)
        beh_l.addWidget(self._auto_break_cb)
        self._always_on_top_cb = QCheckBox("Keep timer window always on top", beh_card)
        self._always_on_top_cb.setChecked(prefs.always_on_top)
        beh_l.addWidget(self._always_on_top_cb)
        self._checklist_visible_cb = QCheckBox("Show task list by default", beh_card)
        self._checklist_visible_cb.setChecked(prefs.checklist_visible)
        beh_l.addWidget(self._checklist_visible_cb)
        layout.addWidget(beh_card)

        noti_card, noti_l = add_settings_section(
            inner,
            "Notifications",
            "Fired when a phase ends. Empty custom sound uses the system beep.",
        )
        self._sound_cb = QCheckBox("Play sound when phase ends", noti_card)
        self._sound_cb.setChecked(prefs.sound_enabled)
        noti_l.addWidget(self._sound_cb)

        loop_row = QWidget(noti_card)
        loop_l = QHBoxLayout(loop_row)
        loop_l.setContentsMargins(0, 2, 0, 0)
        loop_l.setSpacing(12)
        loop_lab = QLabel("Play count", loop_row)
        loop_lab.setObjectName("SettingsFieldLabel")
        loop_l.addWidget(loop_lab, 0)
        self._sound_loop_spin = QSpinBox(loop_row)
        self._sound_loop_spin.setRange(0, 99)
        self._sound_loop_spin.setSpecialValueText("Until stopped")
        self._sound_loop_spin.setValue(prefs.sound_loop_count)
        self._sound_loop_spin.setToolTip(
            "1 = once. Higher = repeat that many times. "
            "Until stopped = keep looping until Start / Skip / Reset "
            "(system beep max 45s)."
        )
        style_settings_spin(self._sound_loop_spin, width=120)
        loop_l.addWidget(self._sound_loop_spin, 0)
        loop_l.addStretch(1)
        noti_l.addWidget(loop_row)

        sound_row = QWidget(noti_card)
        sound_l = QHBoxLayout(sound_row)
        sound_l.setContentsMargins(0, 4, 0, 0)
        sound_l.setSpacing(8)
        self._sound_path = QLineEdit(sound_row)
        self._sound_path.setPlaceholderText("Custom sound file (optional)")
        self._sound_path.setText(prefs.custom_sound_path)
        self._sound_path.setProperty("mono", True)
        style_settings_line_edit(self._sound_path, min_width=160)
        sound_l.addWidget(self._sound_path, 1)

        browse_btn = QPushButton("Browse…", sound_row)
        browse_btn.setObjectName("DialogSecondaryButton")
        browse_btn.clicked.connect(self._browse_sound)
        sound_l.addWidget(browse_btn, 0)

        clear_btn = QPushButton("Clear", sound_row)
        clear_btn.setObjectName("DialogSecondaryButton")
        clear_btn.clicked.connect(self._sound_path.clear)
        sound_l.addWidget(clear_btn, 0)

        test_btn = QPushButton("Test", sound_row)
        test_btn.setObjectName("DialogSecondaryButton")
        test_btn.clicked.connect(self._test_sound)
        sound_l.addWidget(test_btn, 0)
        noti_l.addWidget(sound_row)

        sound_hint = QLabel(
            "WAV recommended. Also supports MP3 / OGG / FLAC / M4A when Qt Multimedia is available.",
            noti_card,
        )
        sound_hint.setObjectName("DialogHelper")
        sound_hint.setWordWrap(True)
        noti_l.addWidget(sound_hint)

        self._tray_cb = QCheckBox("Tray notification", noti_card)
        self._tray_cb.setChecked(prefs.tray_notify)
        noti_l.addWidget(self._tray_cb)
        self._in_app_cb = QCheckBox("In-app toast", noti_card)
        self._in_app_cb.setChecked(prefs.in_app_notify)
        noti_l.addWidget(self._in_app_cb)
        layout.addWidget(noti_card)

        hint = QLabel(
            "Open the Focus timer from the timer icon in the top bar, or from the system tray menu.",
            inner,
        )
        hint.setObjectName("DialogHelper")
        hint.setWordWrap(True)
        hint.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        layout.addWidget(hint)
        layout.addStretch(1)

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self._sound_cb.toggled.connect(self._sync_sound_controls)
        self._sync_sound_controls(self._sound_cb.isChecked())

    def _sync_sound_controls(self, enabled: bool) -> None:
        self._sound_loop_spin.setEnabled(enabled)
        self._sound_path.setEnabled(enabled)
        parent = self._sound_path.parentWidget()
        if parent is not None:
            for child in parent.findChildren(QPushButton):
                child.setEnabled(enabled)

    def _browse_sound(self) -> None:
        start = (self._sound_path.text() or "").strip()
        start_dir = str(Path(start).parent) if start and Path(start).parent.is_dir() else ""
        path, _flt = QFileDialog.getOpenFileName(
            self,
            "Select alert sound",
            start_dir,
            sound_file_filter(),
        )
        if path:
            self._sound_path.setText(path.strip())

    def _test_sound(self) -> None:
        from monostudio.plugins.pomodoro.sound import stop_alert_sound

        stop_alert_sound()
        play_alert_sound(
            (self._sound_path.text() or "").strip(),
            loop_count=int(self._sound_loop_spin.value()),
        )

    def collect_prefs(self) -> PomodoroPrefs:
        return PomodoroPrefs(
            focus_minutes=int(self._focus_spin.value()),
            short_break_minutes=int(self._short_spin.value()),
            long_break_minutes=int(self._long_spin.value()),
            long_break_every=int(self._every_spin.value()),
            auto_start_break=self._auto_break_cb.isChecked(),
            sound_enabled=self._sound_cb.isChecked(),
            custom_sound_path=(self._sound_path.text() or "").strip(),
            sound_loop_count=int(self._sound_loop_spin.value()),
            tray_notify=self._tray_cb.isChecked(),
            in_app_notify=self._in_app_cb.isChecked(),
            always_on_top=self._always_on_top_cb.isChecked(),
            checklist_visible=self._checklist_visible_cb.isChecked(),
        )

    def save(self) -> None:
        if self._settings is None:
            return
        write_prefs(self._settings, self.collect_prefs())
