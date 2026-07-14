"""Review player settings — compact dialog (not app Settings)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
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

from monostudio.core.ocio_display import is_ocio_runtime_available, ocio_runtime_status
from monostudio.ui_qt.inspector_preview_settings import read_sequence_preview_fps, write_sequence_preview_fps
from monostudio.ui_qt.ocio_preview_settings import (
    DEFAULT_DISPLAY,
    DEFAULT_INPUT_COLORSPACE,
    DEFAULT_VIEW,
    KEY_OCIO_CONFIG_PATH,
    KEY_OCIO_SEQUENCE_ENABLED,
    default_bundled_ocio_config_path,
    resolve_ocio_config_path,
    write_ocio_colorspace_triplet,
    write_ocio_config_path,
    write_ocio_sequence_enabled,
)
from monostudio.ui_qt.settings_section_widgets import (
    add_settings_field_row,
    add_settings_helper,
    add_settings_section,
    add_settings_subsection_title,
    settings_divider,
    style_settings_combo,
    style_settings_line_edit,
    style_settings_spin,
)
from monostudio.ui_qt.style import MonosDialog
from monostudio.ui_qt.video_preview_settings import (
    PROXY_SCALE_STEPS,
    TIME_DISPLAY_FRAME,
    TIME_DISPLAY_TIMECODE,
    read_video_preview_loop,
    read_video_preview_precise_scrub_drag,
    read_video_preview_proxy_enabled,
    read_video_preview_proxy_scale,
    read_video_preview_time_display,
    write_video_preview_loop,
    write_video_preview_precise_scrub_drag,
    write_video_preview_proxy_enabled,
    write_video_preview_proxy_scale,
    write_video_preview_time_display,
)

_PROXY_SCALE_LABELS = {1.0: "1", 0.5: "½", 0.25: "¼", 0.125: "⅛"}


class VideoPlayerSettingsDialog(MonosDialog):
    """Player-local preferences: playback, sequences, OCIO."""

    settings_saved = Signal()

    def __init__(
        self,
        settings: QSettings,
        parent: QWidget | None = None,
        *,
        sequence_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._sequence_mode = sequence_mode
        self.setWindowTitle("Player settings")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        scroll = QScrollArea(self)
        scroll.setObjectName("SettingsPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        playback_card, playback_l = add_settings_section(
            inner,
            "Playback",
            "Timeline display and scrub behavior for the review player.",
        )

        self._time_display_combo = QComboBox(playback_card)
        style_settings_combo(self._time_display_combo, width=160)
        self._time_display_combo.addItem("Frames", TIME_DISPLAY_FRAME)
        self._time_display_combo.addItem("Timecode", TIME_DISPLAY_TIMECODE)
        cur_display = read_video_preview_time_display(settings)
        for i in range(self._time_display_combo.count()):
            if self._time_display_combo.itemData(i) == cur_display:
                self._time_display_combo.setCurrentIndex(i)
                break
        add_settings_field_row(playback_l, "Time display", self._time_display_combo)

        self._loop_cb = QCheckBox("Loop playback by default", playback_card)
        self._loop_cb.setChecked(read_video_preview_loop(settings))
        playback_l.addWidget(self._loop_cb)

        self._precise_scrub_cb = QCheckBox("Exact scrub (video)", playback_card)
        self._precise_scrub_cb.setChecked(read_video_preview_precise_scrub_drag(settings))
        self._precise_scrub_cb.setToolTip(
            "Drag scrub to every frame on video. Off = snap to keyframes for speed."
        )
        playback_l.addWidget(self._precise_scrub_cb)

        layout.addWidget(playback_card)

        seq_card, seq_l = add_settings_section(
            inner,
            "Image sequences",
            "Flipbook FPS, decode resolution, and plate color.",
        )

        self._sequence_fps_spin = QSpinBox(seq_card)
        self._sequence_fps_spin.setRange(1, 60)
        style_settings_spin(self._sequence_fps_spin, width=72)
        self._sequence_fps_spin.setValue(read_sequence_preview_fps(settings))
        add_settings_field_row(seq_l, "Default FPS", self._sequence_fps_spin)
        add_settings_helper(
            seq_l,
            "Default when opening a sequence. Change FPS on the transport bar while playing.",
        )

        self._preview_scale_combo = QComboBox(seq_card)
        style_settings_combo(self._preview_scale_combo, width=88)
        for scale in PROXY_SCALE_STEPS:
            self._preview_scale_combo.addItem(_PROXY_SCALE_LABELS.get(scale, str(scale)), scale)
        cur_scale = read_video_preview_proxy_scale(settings)
        for i in range(self._preview_scale_combo.count()):
            if self._preview_scale_combo.itemData(i) == cur_scale:
                self._preview_scale_combo.setCurrentIndex(i)
                break
        add_settings_field_row(seq_l, "Preview resolution", self._preview_scale_combo)
        add_settings_helper(
            seq_l,
            "Decode scale for EXR/DPX sequences — lower is faster. Also used as video proxy scale.",
        )

        seq_l.addWidget(settings_divider(seq_card))
        add_settings_subsection_title(seq_l, "OCIO (ACES 1.3)")

        self._ocio_cb = QCheckBox("Display transform for EXR / DPX / HDR", seq_card)
        self._ocio_cb.setChecked(bool(settings.value(KEY_OCIO_SEQUENCE_ENABLED, False)))
        self._ocio_cb.toggled.connect(self._sync_ocio_ui)
        seq_l.addWidget(self._ocio_cb)

        self._ocio_status = QLabel("", seq_card)
        self._ocio_status.setObjectName("DialogHint")
        self._ocio_status.setWordWrap(True)
        seq_l.addWidget(self._ocio_status)

        ocio_row = QWidget(seq_card)
        ocio_row_l = QHBoxLayout(ocio_row)
        ocio_row_l.setContentsMargins(0, 0, 0, 0)
        ocio_row_l.setSpacing(8)
        self._ocio_config_field = QLineEdit(ocio_row)
        style_settings_line_edit(self._ocio_config_field, min_width=180)
        self._ocio_config_field.setPlaceholderText("Bundled ACES 1.3 (leave empty)")
        v = settings.value(KEY_OCIO_CONFIG_PATH, "")
        self._ocio_config_field.setText(v if isinstance(v, str) else str(v or ""))
        self._ocio_config_field.textChanged.connect(self._sync_ocio_ui)
        btn_browse = QPushButton("Browse…", ocio_row)
        btn_browse.setObjectName("SettingsCategoryActionButton")
        btn_browse.clicked.connect(self._browse_ocio_config)
        btn_default = QPushButton("Default", ocio_row)
        btn_default.setObjectName("SettingsCategoryActionButton")
        btn_default.clicked.connect(lambda: self._ocio_config_field.setText(""))
        ocio_row_l.addWidget(self._ocio_config_field, 1)
        ocio_row_l.addWidget(btn_browse, 0)
        ocio_row_l.addWidget(btn_default, 0)
        add_settings_field_row(seq_l, "OCIO config", ocio_row)
        add_settings_helper(
            seq_l,
            f"Input {DEFAULT_INPUT_COLORSPACE} → {DEFAULT_DISPLAY} / {DEFAULT_VIEW}. "
            "Requires opencolorio.",
        )
        self._sync_ocio_ui()

        layout.addWidget(seq_card)

        if not sequence_mode:
            proxy_card, proxy_l = add_settings_section(
                inner,
                "Video proxy",
                "H.264 cache for faster video scrub and playback.",
            )
            self._proxy_cb = QCheckBox("Use proxy when available", proxy_card)
            self._proxy_cb.setChecked(read_video_preview_proxy_enabled(settings))
            self._proxy_cb.setToolTip(
                "Prefer cached H.264 proxy for scrub/play. "
                "Does not build proxy automatically when opening a video — "
                "build from the Proxy checkbox or … menu in the player."
            )
            proxy_l.addWidget(self._proxy_cb)
            layout.addWidget(proxy_card)
        else:
            self._proxy_cb = None

        layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save")
            save_btn.setObjectName("DialogPrimaryButton")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setObjectName("DialogSecondaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _sync_ocio_ui(self) -> None:
        enabled = self._ocio_cb.isChecked()
        self._ocio_config_field.setEnabled(enabled)
        parts: list[str] = [ocio_runtime_status()]
        bundled = default_bundled_ocio_config_path()
        if bundled.is_file():
            parts.append(f"Bundled: {bundled.name}")
        elif enabled:
            parts.append("Bundled ACES config missing — run scripts/fetch_aces_ocio_config.ps1")
        if enabled:
            custom = (self._ocio_config_field.text() or "").strip()
            if custom:
                p = Path(custom)
                if p.is_file():
                    parts.append(f"Override: {p.name}")
                else:
                    parts.append("Override path not found.")
            else:
                cfg = resolve_ocio_config_path(self._settings)
                if cfg is None:
                    parts.append("No valid OCIO config path.")
            if not is_ocio_runtime_available():
                parts.append("Install: pip install opencolorio")
        self._ocio_status.setText("\n".join(parts))

    def _browse_ocio_config(self) -> None:
        start = ""
        t = (self._ocio_config_field.text() or "").strip()
        if t and Path(t).parent.is_dir():
            start = t
        elif default_bundled_ocio_config_path().parent.is_dir():
            start = str(default_bundled_ocio_config_path().parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select OCIO config",
            start,
            "OCIO config (config.ocio *.ocio);;All files (*.*)",
        )
        if path:
            self._ocio_config_field.setText(path.strip())
            self._sync_ocio_ui()

    def _on_save(self) -> None:
        write_video_preview_time_display(
            self._settings,
            str(self._time_display_combo.currentData() or TIME_DISPLAY_TIMECODE),
        )
        write_video_preview_loop(self._settings, self._loop_cb.isChecked())
        write_video_preview_precise_scrub_drag(self._settings, self._precise_scrub_cb.isChecked())
        write_sequence_preview_fps(self._settings, self._sequence_fps_spin.value())
        scale = self._preview_scale_combo.currentData()
        if scale is not None:
            write_video_preview_proxy_scale(self._settings, float(scale))
        if self._proxy_cb is not None:
            write_video_preview_proxy_enabled(self._settings, self._proxy_cb.isChecked())
        write_ocio_sequence_enabled(self._settings, self._ocio_cb.isChecked())
        write_ocio_config_path(self._settings, (self._ocio_config_field.text() or "").strip())
        write_ocio_colorspace_triplet(
            self._settings,
            input_colorspace=DEFAULT_INPUT_COLORSPACE,
            display=DEFAULT_DISPLAY,
            view=DEFAULT_VIEW,
        )
        from monostudio.ui_qt.sequence_preview_decode import invalidate_decoded_frame_cache

        invalidate_decoded_frame_cache()
        self.settings_saved.emit()
        self.accept()
