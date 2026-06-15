from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, QSize, Qt, QRegularExpression, QSettings, Signal, QStandardPaths, QThread, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QPainter,
    QPixmap,
    QRegularExpressionValidator,
    QShowEvent,
    QTextBlockFormat,
    QTextCursor,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.dcc_affinity import resolve_affinity_executable
from monostudio.core.dcc_blender import resolve_blender_executable
from monostudio.core.dcc_houdini import resolve_houdini_executable
from monostudio.core.dcc_maya import resolve_maya_executable
from monostudio.core.dcc_rizomuv import resolve_rizomuv_executable
from monostudio.core.dcc_substance_painter import resolve_substance_painter_executable
from monostudio.core.department_registry import DepartmentRegistry
from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.fs_reader import read_use_dcc_folders, save_use_dcc_folders
from monostudio.core.project_create_defaults import read_create_default_dcc_map, write_create_default_dcc_map
from monostudio.core.pipeline_types_and_presets import (
    PipelineTypesAndPresets,
    load_department_vocabulary,
    load_pipeline_types_and_presets,
    load_pipeline_types_and_presets_for_project,
    save_pipeline_types_and_presets,
    save_pipeline_types_and_presets_to_project,
)
from monostudio.ui_qt.inspector_preview_settings import (
    THUMB_SOURCE_RENDER_SEQUENCE,
    THUMB_SOURCE_USER,
    THUMB_SOURCE_USER_THEN_RENDER,
    read_inspector_thumbnail_open_exe,
    read_inspector_thumbnail_source,
    read_sequence_preview_fps,
    write_inspector_thumbnail_open_exe,
    write_inspector_thumbnail_source,
    write_sequence_preview_fps,
)
from monostudio.ui_qt.video_preview_settings import (
    BACKEND_AUTO,
    BACKEND_EXTERNAL,
    BACKEND_MPV,
    BACKEND_QT,
    read_mpv_directory,
    read_video_external_player_exe,
    read_video_player_backend,
    write_mpv_directory,
    write_video_external_player_exe,
    write_video_player_backend,
)
from monostudio.core.mpv_resolve import format_mpv_detect_status, resolve_mpv_dll
from monostudio.core.mpv_install import (
    MPV_BUILDS_PAGE,
    MPV_WIN64_7Z_NAME,
)
from monostudio.ui_qt.pipeline_structure_editor import PipelineStructureEditorWidget
from monostudio.ui_qt.settings_section_widgets import (
    SettingsSegmentedControl,
    add_settings_field_row,
    add_settings_helper,
    add_settings_section,
    add_settings_subsection_title,
    settings_divider,
    style_settings_combo,
    style_settings_line_edit,
    style_settings_spin,
)
from monostudio.core.update_checker import (
    CheckResult,
    ExtraRepoRelease,
    EXTRA_REPOS,
    UpdateInfo,
    fetch_extra_repos,
    get_cached_check_result,
    get_cached_extra_repos,
    run_full_update_check,
    download_installer,
    get_extra_tool_installed_version,
    is_newer_than,
    launch_installer,
    run_installer_and_exit,
)
from monostudio.core.access_control import (
    AccessRole,
    admin_key_configured,
    bundled_access_keys_module_path,
    clear_session,
    dev_key_configured,
    forget_remembered_access,
    has_access_restrictions,
    KEY_ACCESS_REMEMBER,
    is_admin_capable,
    is_dev_session,
    read_access_remember_preferred,
    read_splash_display_ms,
    read_verbose_debug_enabled,
    session_role,
    try_unlock,
    write_access_remember_preferred,
    write_splash_display_ms,
    write_verbose_debug_enabled,
)
from monostudio.core.app_paths import get_app_base_path
from monostudio.core.ffmpeg_resolve import (
    FFMPEG_GYAN_BUILDS_PAGE,
    FFMPEG_GYAN_RELEASE_ESSENTIALS_ZIP,
    get_ffmpeg_version_short,
    resolve_ffmpeg_executable,
    validate_ffmpeg_executable,
    write_ffmpeg_executable_path,
)
from monostudio.core.version import get_app_version
from monostudio.ui_qt.force_rename_project_id_dialog import ForceRenameProjectIdDialog
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font

# Icon size for update list rows
_UPDATE_ROW_ICON_SIZE = 24
# Download/Latest action button — width from label (loading bar matches button width)
_UPDATE_ACTION_HEIGHT = 28
_UPDATE_ACTION_PADDING_X = 24  # matches QSS padding 6px 12px on update action buttons
_UPDATE_ACTION_WIDTH_MIN = 88  # "Latest"
_UPDATE_CANCEL_GAP = 4
_UPDATE_CANCEL_BTN_WIDTH = 20
_UPDATE_CANCEL_ICON_SIZE = 16
_UPDATE_STATUS_ICON_SIZE = 32


def _configure_update_cancel_btn(btn: QToolButton) -> None:
    btn.setObjectName("UpdateDownloadCancelBtn")
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    btn.setIconSize(QSize(_UPDATE_CANCEL_ICON_SIZE, _UPDATE_CANCEL_ICON_SIZE))
    btn.setIcon(lucide_icon("x", size=_UPDATE_CANCEL_ICON_SIZE, color_hex="#a1a1aa"))
    btn.setFixedSize(_UPDATE_CANCEL_BTN_WIDTH, _UPDATE_ACTION_HEIGHT)
    btn.setToolTip("Cancel download")


def _measure_update_action_width(btn: QPushButton) -> int:
    if not btn.icon().isNull() and btn.iconSize().width() <= 0:
        btn.setIconSize(QSize(16, 16))
    btn.ensurePolished()
    hint_w = btn.sizeHint().width()
    if hint_w > 0:
        return max(_UPDATE_ACTION_WIDTH_MIN, hint_w)
    fm = btn.fontMetrics()
    w = fm.horizontalAdvance(btn.text()) + _UPDATE_ACTION_PADDING_X + 4
    if not btn.icon().isNull():
        w += btn.iconSize().width() + 4
    return max(_UPDATE_ACTION_WIDTH_MIN, w)


def _apply_update_tool_action_slot(
    get_btn: QPushButton,
    install_btn: QPushButton,
    prog: QProgressBar,
    stack: QStackedWidget,
    outer: QWidget,
    *,
    leading_width: int = 28,
    leading_gap: int = 6,
) -> int:
    """Size Get/Install stacked action slot (+ leading locate button) on Updates tool rows."""
    action_w = max(_measure_update_action_width(get_btn), _measure_update_action_width(install_btn))
    slot_w = action_w + _UPDATE_CANCEL_GAP + _UPDATE_CANCEL_BTN_WIDTH
    for btn in (get_btn, install_btn):
        btn.setFixedSize(action_w, _UPDATE_ACTION_HEIGHT)
    prog.setFixedSize(action_w, _UPDATE_ACTION_HEIGHT)
    stack.setFixedSize(slot_w, _UPDATE_ACTION_HEIGHT)
    outer.setFixedSize(leading_width + leading_gap + slot_w, _UPDATE_ACTION_HEIGHT)
    return action_w


def _apply_update_action_width(
    action_btn: QPushButton,
    *,
    loading_widget: QWidget | None = None,
    progress_bar: QProgressBar | None = None,
) -> int:
    """Size action button and its loading slot so Download labels are not clipped."""
    width = _measure_update_action_width(action_btn)
    action_btn.setFixedSize(width, _UPDATE_ACTION_HEIGHT)
    slot_w = width + _UPDATE_CANCEL_GAP + _UPDATE_CANCEL_BTN_WIDTH
    if loading_widget is not None:
        loading_widget.setFixedSize(slot_w, _UPDATE_ACTION_HEIGHT)
    if progress_bar is not None:
        progress_bar.setFixedSize(width, _UPDATE_ACTION_HEIGHT)
    container = action_btn.parentWidget()
    if container is not None and loading_widget is not None:
        container.setFixedSize(slot_w, _UPDATE_ACTION_HEIGHT)
    return width


def _update_product_icon_pixmap(product_id: str, size: int = _UPDATE_ROW_ICON_SIZE) -> QPixmap:
    """Icon for update list row: MonoStudio uses logo.svg if present, else fallback; others use fallback."""
    if product_id == "monostudio":
        base = get_app_base_path()
        logo_path = base / "monostudio_data" / "icons" / "logo.svg"
        if logo_path.is_file():
            try:
                svg = logo_path.read_text(encoding="utf-8").replace("currentColor", "#e4e4e7")
                renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
                if renderer.isValid():
                    pix = QPixmap(size, size)
                    pix.fill(Qt.GlobalColor.transparent)
                    p = QPainter(pix)
                    try:
                        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                        renderer.render(p, QRect(0, 0, size, size))
                    finally:
                        p.end()
                    return pix
            except OSError:
                pass
    # Fallback: Lucide package (app) or box (other products)
    icon = lucide_icon("package" if product_id == "monostudio" else "box", size=size, color_hex="#a1a1aa")
    return icon.pixmap(size, size)


class _UpdateCheckWorker(QThread):
    """Runs full update check (MonoStudio + extra repos) in background; emits check_finished(result, error_message, extra_repos)."""

    check_finished = Signal(object, str, object)  # CheckResult | None, error str, dict[str, ExtraRepoRelease]

    def __init__(self, manifest_url: str, current_version: str, parent=None, *, skip_cache: bool = False) -> None:
        super().__init__(parent)
        self._manifest_url = manifest_url
        self._current_version = current_version
        self._skip_cache = skip_cache

    def run(self) -> None:
        result, extra, err = run_full_update_check(
            self._current_version,
            self._manifest_url,
            extra_timeout=10,
            skip_cache=self._skip_cache,
        )
        self.check_finished.emit(result, err, extra)


class _ExtraReposFetchWorker(QThread):
    """Fetches only extra repos (e.g. MonoFXSuite) in background; emits extra_repos_fetched(extra_repos)."""

    extra_repos_fetched = Signal(object)  # dict[str, ExtraRepoRelease]

    def run(self) -> None:
        try:
            extra = fetch_extra_repos(timeout=10)
            self.extra_repos_fetched.emit(extra)
        except Exception:
            self.extra_repos_fetched.emit({})


class _DownloadWorker(QThread):
    """Downloads installer to path; emits progress(read, total) and download_finished(success, path, error_message). Supports cancel()."""

    download_finished = Signal(bool, str, str)
    progress = Signal(int, int)  # read, total (0 = unknown)

    def __init__(self, url: str, dest_path: Path, fallback_url: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._dest_path = dest_path
        self._fallback_url = (fallback_url or "").strip() or None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _progress_callback(self, read: int, total: int | None) -> None:
        if self._cancelled:
            raise RuntimeError("Cancelled")
        self.progress.emit(read, total or 0)

    def run(self) -> None:
        try:
            download_installer(
                self._url,
                self._dest_path,
                fallback_url=self._fallback_url,
                progress_callback=self._progress_callback,
            )
            if self._cancelled:
                self.download_finished.emit(False, str(self._dest_path), "Cancelled")
            else:
                self.download_finished.emit(True, str(self._dest_path), "")
        except Exception as e:
            self.download_finished.emit(False, str(self._dest_path), str(e))


class _FfmpegZipDownloadWorker(QThread):
    """Download ffmpeg-release-essentials.zip to temp; validate zip signature."""

    download_finished = Signal(bool, str, str)
    progress = Signal(int, int)

    def __init__(self, url: str, dest_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._dest_path = dest_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _progress_callback(self, read: int, total: int | None) -> None:
        if self._cancelled:
            raise RuntimeError("Cancelled")
        self.progress.emit(read, total or 0)

    def run(self) -> None:
        from monostudio.core.ffmpeg_install import is_plausible_zip
        from monostudio.core.update_checker import download_to_file

        try:
            download_to_file(self._url, self._dest_path, timeout=900, progress_callback=self._progress_callback)
            if self._cancelled:
                self.download_finished.emit(False, str(self._dest_path), "Cancelled")
                return
            if not is_plausible_zip(self._dest_path):
                self.download_finished.emit(
                    False,
                    str(self._dest_path),
                    "Downloaded file is not a valid zip (try again or use Official builds).",
                )
                return
            self.download_finished.emit(True, str(self._dest_path), "")
        except RuntimeError as e:
            if "Cancelled" in str(e):
                self.download_finished.emit(False, str(self._dest_path), "Cancelled")
            else:
                self.download_finished.emit(False, str(self._dest_path), str(e))
        except Exception as e:
            self.download_finished.emit(False, str(self._dest_path), str(e))


class _FfmpegInstallWorker(QThread):
    """Extract Gyan zip on a background thread (no QSettings here — main thread registers path)."""

    ok = Signal(str)
    err = Signal(str)

    def __init__(self, zip_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._zip_path = zip_path

    def run(self) -> None:
        try:
            from monostudio.core.ffmpeg_install import extract_gyan_ffmpeg_essentials_zip

            p = extract_gyan_ffmpeg_essentials_zip(self._zip_path)
            self.ok.emit(str(p.resolve()))
        except Exception as e:
            self.err.emit(str(e).replace("\n", " ")[:400])


class _Mpv7zDownloadWorker(QThread):
    """Download mpv portable .7z to temp."""

    download_finished = Signal(bool, str, str)
    progress = Signal(int, int)

    def __init__(self, dest_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._dest_path = dest_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _progress_callback(self, read: int, total: int | None) -> None:
        if self._cancelled:
            raise RuntimeError("Cancelled")
        self.progress.emit(read, total or 0)

    def run(self) -> None:
        from monostudio.core.mpv_install import download_mpv_win64_7z

        try:
            download_mpv_win64_7z(self._dest_path, progress_callback=self._progress_callback)
            if self._cancelled:
                self.download_finished.emit(False, str(self._dest_path), "Cancelled")
                return
            self.download_finished.emit(True, str(self._dest_path), "")
        except RuntimeError as e:
            if "Cancelled" in str(e):
                self.download_finished.emit(False, str(self._dest_path), "Cancelled")
            else:
                self.download_finished.emit(False, str(self._dest_path), str(e))
        except Exception as e:
            self.download_finished.emit(False, str(self._dest_path), str(e))


class _MpvInstallWorker(QThread):
    ok = Signal(str)
    err = Signal(str)

    def __init__(self, archive_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._archive_path = archive_path

    def run(self) -> None:
        try:
            from monostudio.core.mpv_install import extract_mpv_portable_7z

            p = extract_mpv_portable_7z(self._archive_path)
            self.ok.emit(str(p.resolve()))
        except Exception as e:
            self.err.emit(str(e).replace("\n", " ")[:400])


def _is_valid_type_id(type_id: str) -> bool:
    if not type_id:
        return False
    if type_id.lower() != type_id:
        return False
    if " " in type_id:
        return False
    for ch in type_id:
        if not (ch.islower() or ch.isdigit() or ch == "_"):
            return False
    return True


@dataclass(frozen=True)
class _TypeKey:
    type_id: str


class SettingsDialog(MonosDialog):
    """
    Settings UI — 3-tier hierarchy:
      Tier 1 (left column): General | Pipeline | DCCs | Project
      Tier 2 (horizontal tabs): Modules per category (e.g. Pipeline → Mapping Folders | Categories | Statuses)
      Tier 3 (pill tabs): Detail split (e.g. Categories → Asset Depts | Shot Depts)
    """

    workspace_root_selected = Signal(str)
    project_root_selected = Signal(str)
    access_session_changed = Signal()
    nav_quick_slots_changed = Signal()
    hotkeys_changed = Signal()

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        project_root: Path | None = None,
        settings: QSettings | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)

        # Default 16:9 aspect ratio
        self.setMinimumSize(800, 450)
        self.resize(1280, 720)

        self._workspace_root = workspace_root
        self._project_root = project_root
        self._settings = settings
        self._project_root_renamed_to: Path | None = None

        self._vocab = load_department_vocabulary()
        self._vocab_set = set(self._vocab)
        self._config: PipelineTypesAndPresets = load_pipeline_types_and_presets_for_project(project_root)

        # Optional integrations UI fields.
        self._blender_exe_field: QLineEdit | None = None
        self._maya_exe_field: QLineEdit | None = None
        self._houdini_exe_field: QLineEdit | None = None
        self._houdini_workfile_ext_combo: QComboBox | None = None
        self._substance_painter_exe_field: QLineEdit | None = None
        self._affinity_exe_field: QLineEdit | None = None
        self._rizomuv_exe_field: QLineEdit | None = None
        self._pipeline_editor: PipelineStructureEditorWidget | None = None
        self._create_default_combos: dict[str, QComboBox] = {}
        self._create_default_form_layout: QFormLayout | None = None
        self._use_dcc_folders_cb: QCheckBox | None = None
        self._notification_max_visible_combo: QComboBox | None = None
        self._mention_delivery_combo: QComboBox | None = None
        self._notification_vietnamese_cb: QCheckBox | None = None
        self._discord_disabled_locally_cb: QCheckBox | None = None
        self._publish_ignore_ext_field: QLineEdit | None = None
        self._inspector_thumb_segment_asset: SettingsSegmentedControl | None = None
        self._inspector_thumb_segment_shot: SettingsSegmentedControl | None = None
        self._inspector_sequence_fps_spin: QSpinBox | None = None
        self._inspector_thumb_open_exe_field: QLineEdit | None = None

        self._ffmpeg_pending_zip: Path | None = None
        self._ffmpeg_download_worker: _FfmpegZipDownloadWorker | None = None
        self._ffmpeg_install_worker: _FfmpegInstallWorker | None = None

        self._mpv_pending_7z: Path | None = None
        self._mpv_download_worker: _Mpv7zDownloadWorker | None = None
        self._mpv_install_worker: _MpvInstallWorker | None = None
        self._mpv_detect_helper: QLabel | None = None

        self._pipeline_access_banner: QLabel | None = None
        self._access_status_label: QLabel | None = None
        self._access_unlock_field: QLineEdit | None = None
        self._access_remember_cb: QCheckBox | None = None
        self._access_keys_info_label: QLabel | None = None
        self._access_debug_cb: QCheckBox | None = None
        self._access_splash_spin: QSpinBox | None = None
        self._hotkeys_widget: HotkeysSettingsWidget | None = None

        self._discord_integrations_banner: QLabel | None = None
        self._discord_enabled_cb: QCheckBox | None = None
        self._discord_webhook_field: QLineEdit | None = None
        self._discord_url_replace_btn: QPushButton | None = None
        self._discord_label_field: QLineEdit | None = None
        self._discord_mention_cb: QCheckBox | None = None
        self._discord_note_done_cb: QCheckBox | None = None
        self._discord_inbox_cb: QCheckBox | None = None
        self._discord_schedule_cb: QCheckBox | None = None
        self._discord_schedule_assigned_cb: QCheckBox | None = None
        self._discord_test_btn: QPushButton | None = None
        self._discord_test_notifications_btn: QPushButton | None = None
        self._discord_stored_url: str = ""
        self._discord_url_editing: bool = False

        # Tier 1: left nav — General | Pipeline | DCCs | Project
        self._content_stack = QStackedWidget(self)
        self._content_stack.addWidget(self._build_general_page())
        self._content_stack.addWidget(self._build_pipeline_page())
        self._content_stack.addWidget(self._build_dccs_page())
        self._content_stack.addWidget(self._build_project_page())

        self._nav = QListWidget(self)
        self._nav.setObjectName("SettingsNav")
        self._nav.setSelectionMode(QAbstractItemView.SingleSelection)
        self._nav.setUniformItemSizes(True)
        self._nav.setSpacing(2)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._nav.setFocusPolicy(Qt.StrongFocus)
        self._nav.setIconSize(QSize(16, 16))
        _nav_icons = [
            ("General", "sliders-horizontal"),
            ("Pipeline", "layers"),
            ("DCCs", "zap"),
            ("Project", "folder"),
        ]
        for label, icon_name in _nav_icons:
            it = QListWidgetItem(label)
            ic = lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_label"])
            if not ic.isNull():
                it.setIcon(ic)
            self._nav.addItem(it)
        self._nav.setCurrentRow(0)
        self._nav.currentRowChanged.connect(self._on_settings_nav_row_changed)

        nav_frame = QFrame(self)
        nav_frame.setObjectName("SettingsNavFrame")
        nav_frame.setFixedWidth(140)
        nav_layout = QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.addWidget(self._nav)

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(nav_frame, 0)
        content_layout.addWidget(self._content_stack, 1)

        btn_save = QPushButton("Save")
        btn_save.setObjectName("DialogPrimaryButton")
        btn_save.clicked.connect(self._on_save)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("DialogSecondaryButton")
        btn_cancel.clicked.connect(self.reject)

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        button_row_l.addStretch(1)
        button_row_l.addWidget(btn_save)
        button_row_l.addWidget(btn_cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addLayout(content_layout, 1)
        layout.addWidget(button_row, 0)

    def open_pipeline_types_and_presets(self) -> None:
        self._nav.setCurrentRow(1)
        self._content_stack.setCurrentIndex(1)
        if getattr(self, "_pipeline_tier2_stack", None) is not None:
            self._pipeline_tier2_stack.setCurrentIndex(0)
        if getattr(self, "_pipeline_tier2_buttons", None) and len(self._pipeline_tier2_buttons) > 0:
            self._pipeline_tier2_buttons[0].setChecked(True)

    def open_to_updates_tab(self) -> None:
        """Switch to General → Updates and apply cached check result if any (from startup)."""
        self._nav.setCurrentRow(0)
        self._content_stack.setCurrentIndex(0)
        stack = getattr(self, "_general_tier2_stack", None)
        buttons = getattr(self, "_general_tier2_buttons", None)
        if stack is not None and buttons is not None and len(buttons) > 4:
            stack.setCurrentIndex(4)
            for i, b in enumerate(buttons):
                b.setChecked(i == 4)
        self._apply_cached_update_result(get_cached_check_result())
        self._refresh_ffmpeg_update_row()

    def open_to_ui_tab(self) -> None:
        """Switch to General → UI (system tray and notification options)."""
        self._nav.setCurrentRow(0)
        self._content_stack.setCurrentIndex(0)
        stack = getattr(self, "_general_tier2_stack", None)
        buttons = getattr(self, "_general_tier2_buttons", None)
        if stack is not None and buttons is not None and len(buttons) > 1:
            stack.setCurrentIndex(1)
            for i, b in enumerate(buttons):
                b.setChecked(i == 1)

    def _load_persisted_last_check_time(self) -> None:
        """Load last check time from settings so 'Last checked' is visible across sessions."""
        if self._update_last_checked_time is not None:
            return
        if not self._settings:
            return
        last_check_str = self._settings.value("updates/last_check_time", None, str)
        if last_check_str:
            try:
                self._update_last_checked_time = datetime.fromisoformat(last_check_str)
            except (ValueError, TypeError):
                pass

    def _apply_cached_update_result(self, result: CheckResult | None) -> None:
        """Apply cached update check result to Updates tab UI (no new network check)."""
        self._load_persisted_last_check_time()
        extra = get_cached_extra_repos()
        if result is not None:
            if result.latest_notes:
                self._update_changelog.setMarkdown(result.latest_notes)
            else:
                self._update_changelog.setPlainText("No release notes for this version.")
            self._apply_changelog_line_height()
            self._update_latest_html_url = result.latest_html_url
            if result.update_available and result.update_info is not None:
                self._pending_update_info = result.update_info
            else:
                self._pending_update_info = None
            self._apply_monostudio_row(result)
        else:
            self._apply_monostudio_row(None)
        self._apply_extra_repos_ui(extra)
        msg, icon_name, icon_color = self._compute_update_summary(result, extra)
        self._set_update_status_display(msg, icon_name, icon_color)

    def _build_tier2_page_buttons(
        self,
        items: list[tuple[str, QWidget]],
        *,
        store_stack: str | None = None,
        store_buttons: str | None = None,
    ) -> QWidget:
        """Tier 2: horizontal page buttons + stacked content (thay QTabWidget để đồng bộ style UI)."""
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        btn_row = QWidget(container)
        btn_row.setObjectName("SettingsPageButtonBar")  # bar chứa Tier2Tab
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 8)
        btn_l.setSpacing(6)

        group = QButtonGroup(container)
        stack = QStackedWidget(container)
        stack.setObjectName("SettingsPageStack")
        buttons: list[QPushButton] = []

        for i, (label, page) in enumerate(items):
            stack.addWidget(page)
            btn = QPushButton(label, btn_row)
            btn.setObjectName("Tier2Tab")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda _c=False, idx=i: self._on_page_button_clicked(stack, buttons, idx))
            group.addButton(btn)
            btn_l.addWidget(btn, 0)
            buttons.append(btn)

        btn_l.addStretch(1)
        layout.addWidget(btn_row, 0)
        layout.addWidget(stack, 1)

        stack.setCurrentIndex(0)
        if store_stack == "pipeline":
            self._pipeline_tier2_stack = stack
        if store_buttons == "pipeline":
            self._pipeline_tier2_buttons = buttons
        if store_stack == "general":
            self._general_tier2_stack = stack
            stack.currentChanged.connect(self._on_general_tier2_changed)
        if store_buttons == "general":
            self._general_tier2_buttons = buttons

        return container

    def _on_general_tier2_changed(self, index: int) -> None:
        """When General → Updates tab is shown, apply cached result; if no extra repos yet, fetch in background."""
        if index == 4:
            self._apply_cached_update_result(get_cached_check_result())
            self._refresh_ffmpeg_update_row()
            if not get_cached_extra_repos() and not getattr(self, "_extra_repos_fetch_worker", None):
                w = _ExtraReposFetchWorker(self)
                self._extra_repos_fetch_worker = w
                w.extra_repos_fetched.connect(self._on_extra_repos_fetched)
                w.finished.connect(lambda: setattr(self, "_extra_repos_fetch_worker", None))
                w.start()

    def _on_extra_repos_fetched(self, extra_repos: dict) -> None:
        """Apply extra repos data from background fetch (so Download/Latest shows without clicking Check)."""
        self._apply_extra_repos_ui(extra_repos)

    def _on_page_button_clicked(
        self,
        stack: QStackedWidget,
        buttons: list[QPushButton],
        index: int,
    ) -> None:
        stack.setCurrentIndex(index)
        for i, b in enumerate(buttons):
            b.setChecked(i == index)

    def _build_general_page(self) -> QWidget:
        """Tier 2: General → Workspace | UI | Behavior | Hotkeys | Updates | Access (nút page ngang)."""
        return self._build_tier2_page_buttons(
            [
                ("Workspace", self._build_app_workspace_tab()),
                ("UI", self._build_ui_tab()),
                ("Behavior", self._build_behavior_tab()),
                ("Hotkeys", self._build_hotkeys_tab()),
                ("Updates", self._build_updates_tab()),
                ("Access", self._build_access_tab()),
            ],
            store_stack="general",
            store_buttons="general",
        )

    def _build_ui_tab(self) -> QWidget:
        """General → UI: notifications and other UI options."""
        scroll = QScrollArea()
        scroll.setObjectName("SettingsPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 16)
        layout.setSpacing(16)

        noti_card, noti_l = add_settings_section(
            inner,
            "Notifications",
            "@mention popups only — bell history always stays in the app. "
            "Use Send test when Windows notification is selected.",
        )

        self._notification_max_visible_combo = QComboBox(noti_card)
        self._notification_max_visible_combo.addItems(["1", "2", "3"])
        style_settings_combo(self._notification_max_visible_combo, width=88)
        try:
            cur = 1
            if self._settings is not None:
                v = self._settings.value("notification/max_visible", 1, int)
                cur = max(1, min(3, int(v) if v is not None else 1))
        except Exception:
            cur = 1
        self._notification_max_visible_combo.setCurrentIndex(cur - 1)
        add_settings_field_row(noti_l, "Max visible toasts", self._notification_max_visible_combo)

        from monostudio.core.notification_preferences import read_mention_delivery

        self._mention_delivery_combo = QComboBox(noti_card)
        style_settings_combo(self._mention_delivery_combo, width=248)
        self._mention_delivery_combo.addItem("In-app toast (MONOS)", "builtin")
        win_idx = self._mention_delivery_combo.count()
        self._mention_delivery_combo.addItem("Windows notification", "windows")
        delivery = read_mention_delivery(self._settings)
        self._mention_delivery_combo.setCurrentIndex(
            win_idx if delivery == "windows" else 0
        )
        if sys.platform != "win32":
            self._mention_delivery_combo.setCurrentIndex(0)
            self._mention_delivery_combo.setEnabled(False)
        add_settings_field_row(noti_l, "@mention popup", self._mention_delivery_combo)
        self._mention_delivery_combo.currentIndexChanged.connect(
            self._refresh_windows_noti_status
        )

        from monostudio.core.notification_preferences import read_notification_vietnamese

        self._notification_vietnamese_cb = QCheckBox(
            "Thông báo bằng tiếng Việt (@mention, Discord webhook)",
            noti_card,
        )
        self._notification_vietnamese_cb.setToolTip(
            "Bật mặc định. Bỏ chọn để dùng bản tiếng Anh."
        )
        try:
            if self._settings is not None:
                self._notification_vietnamese_cb.setChecked(read_notification_vietnamese(self._settings))
        except Exception:
            pass
        noti_l.addWidget(self._notification_vietnamese_cb)

        from monostudio.core.notification_preferences import read_discord_disabled_locally

        self._discord_disabled_locally_cb = QCheckBox(
            "Disable Discord webhooks on this machine only",
            noti_card,
        )
        self._discord_disabled_locally_cb.setToolTip(
            "Workspace webhook settings stay synced; this machine will not POST to Discord."
        )
        try:
            if self._settings is not None:
                self._discord_disabled_locally_cb.setChecked(
                    read_discord_disabled_locally(self._settings)
                )
        except Exception:
            pass
        noti_l.addWidget(self._discord_disabled_locally_cb)

        self._windows_noti_status = QLabel("", noti_card)
        self._windows_noti_status.setWordWrap(True)
        self._windows_noti_status.setObjectName("DialogHelper")
        noti_l.addWidget(self._windows_noti_status)

        test_row = QWidget(noti_card)
        test_row_l = QHBoxLayout(test_row)
        test_row_l.setContentsMargins(0, 4, 0, 0)
        self._test_windows_noti_btn = QPushButton("Send test notification", test_row)
        self._test_windows_noti_btn.setObjectName("SettingsInlineActionButton")
        self._test_windows_noti_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_windows_noti_btn.clicked.connect(self._on_test_windows_notification)
        if sys.platform != "win32":
            self._test_windows_noti_btn.setEnabled(False)
        test_row_l.addWidget(self._test_windows_noti_btn, 0)
        test_row_l.addStretch(1)
        noti_l.addWidget(test_row)
        self._refresh_windows_noti_status()
        layout.addWidget(noti_card)

        qv_card, qv_l = add_settings_section(
            inner,
            "Quick view",
            "Houdini-style page bookmarks: Ctrl+1–9 assigns the current page and filters; "
            "press 1–9 to return. Slots are stored per machine.",
        )
        from monostudio.ui_qt.nav_quick_view import SLOT_COUNT

        self._nav_quick_slot_labels: list[QLabel] = []
        slots_grid = QWidget(qv_card)
        slots_grid_l = QVBoxLayout(slots_grid)
        slots_grid_l.setContentsMargins(0, 0, 0, 0)
        slots_grid_l.setSpacing(4)
        for slot in range(1, SLOT_COUNT + 1):
            row = QWidget(slots_grid)
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(8)
            num = QLabel(f"{slot}", row)
            num.setObjectName("SettingsMonoValue")
            num.setFixedWidth(20)
            num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            summary = QLabel("", row)
            summary.setObjectName("DialogHelper")
            summary.setWordWrap(True)
            self._nav_quick_slot_labels.append(summary)
            clear_btn = QPushButton("Clear", row)
            clear_btn.setObjectName("DialogSecondaryButton")
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.clicked.connect(lambda _checked=False, s=slot: self._on_clear_nav_quick_slot(s))
            row_l.addWidget(num, 0)
            row_l.addWidget(summary, 1)
            row_l.addWidget(clear_btn, 0)
            slots_grid_l.addWidget(row)
        qv_l.addWidget(slots_grid)

        clear_all_row = QWidget(qv_card)
        clear_all_l = QHBoxLayout(clear_all_row)
        clear_all_l.setContentsMargins(0, 8, 0, 0)
        clear_all_btn = QPushButton("Clear all slots", clear_all_row)
        clear_all_btn.setObjectName("DialogSecondaryButton")
        clear_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_all_btn.clicked.connect(self._on_clear_all_nav_quick_slots)
        clear_all_l.addWidget(clear_all_btn, 0)
        clear_all_l.addStretch(1)
        qv_l.addWidget(clear_all_row)
        self._refresh_nav_quick_slot_labels()
        layout.addWidget(qv_card)

        insp_card, insp_l = add_settings_section(
            inner,
            "Inspector preview",
            "Thumbnail source for grid, list, and Inspector. Assets and Shots can use different modes.",
        )

        _tip_u = (
            "Only user thumbnails (pasted or .user.* files).\n"
            "Work render/preview sequences are ignored."
        )
        _tip_r = (
            "Image sequence under the active work file folder:\n"
            "work/render → preview → playblast → flipbook, then <work name>/."
        )
        _tip_s = "Prefer the Render sequence when it exists;\notherwise use the User thumbnail."
        _seg_opts = [
            ("User", _tip_u, "user"),
            ("Render", _tip_r, "render"),
            ("Smart", _tip_s, "smart"),
        ]

        add_settings_subsection_title(insp_l, "Thumbnail source — Assets")
        self._inspector_thumb_segment_asset = SettingsSegmentedControl(_seg_opts, insp_card)
        try:
            ma = read_inspector_thumbnail_source(self._settings, entity="asset")
            if ma == THUMB_SOURCE_USER:
                self._inspector_thumb_segment_asset.set_value("user")
            elif ma == THUMB_SOURCE_RENDER_SEQUENCE:
                self._inspector_thumb_segment_asset.set_value("render")
            else:
                self._inspector_thumb_segment_asset.set_value("smart")
        except Exception:
            self._inspector_thumb_segment_asset.set_value("smart")
        insp_l.addWidget(self._inspector_thumb_segment_asset)

        add_settings_subsection_title(insp_l, "Thumbnail source — Shots")
        self._inspector_thumb_segment_shot = SettingsSegmentedControl(_seg_opts, insp_card)
        try:
            ms = read_inspector_thumbnail_source(self._settings, entity="shot")
            if ms == THUMB_SOURCE_USER:
                self._inspector_thumb_segment_shot.set_value("user")
            elif ms == THUMB_SOURCE_RENDER_SEQUENCE:
                self._inspector_thumb_segment_shot.set_value("render")
            else:
                self._inspector_thumb_segment_shot.set_value("smart")
        except Exception:
            self._inspector_thumb_segment_shot.set_value("smart")
        insp_l.addWidget(self._inspector_thumb_segment_shot)

        add_settings_helper(
            insp_l,
            "Render path: work/render → preview → playblast → flipbook/<work name>/.",
        )

        insp_l.addWidget(settings_divider(insp_card))

        self._inspector_sequence_fps_spin = QSpinBox(insp_card)
        self._inspector_sequence_fps_spin.setRange(1, 60)
        style_settings_spin(self._inspector_sequence_fps_spin, width=72)
        try:
            self._inspector_sequence_fps_spin.setValue(read_sequence_preview_fps(self._settings))
        except Exception:
            self._inspector_sequence_fps_spin.setValue(30)
        add_settings_field_row(insp_l, "Sequence playback FPS", self._inspector_sequence_fps_spin)

        add_settings_subsection_title(insp_l, "Open thumbnail with")
        thumb_app_row = QWidget(insp_card)
        thumb_app_row_l = QHBoxLayout(thumb_app_row)
        thumb_app_row_l.setContentsMargins(0, 0, 0, 0)
        thumb_app_row_l.setSpacing(8)
        self._inspector_thumb_open_exe_field = QLineEdit(thumb_app_row)
        style_settings_line_edit(self._inspector_thumb_open_exe_field, min_width=200)
        self._inspector_thumb_open_exe_field.setPlaceholderText(
            "System default for file type (Windows “Open with”)"
        )
        try:
            self._inspector_thumb_open_exe_field.setText(read_inspector_thumbnail_open_exe(self._settings))
        except Exception:
            self._inspector_thumb_open_exe_field.setText("")
        btn_thumb_browse = QPushButton("Browse…", thumb_app_row)
        btn_thumb_browse.setObjectName("SettingsCategoryActionButton")
        btn_thumb_browse.clicked.connect(self._browse_inspector_thumbnail_open_exe)
        btn_thumb_clear = QPushButton("Clear", thumb_app_row)
        btn_thumb_clear.setObjectName("SettingsCategoryActionButton")
        btn_thumb_clear.clicked.connect(lambda: self._inspector_thumb_open_exe_field.setText(""))
        thumb_app_row_l.addWidget(self._inspector_thumb_open_exe_field, 1)
        thumb_app_row_l.addWidget(btn_thumb_browse, 0)
        thumb_app_row_l.addWidget(btn_thumb_clear, 0)
        insp_l.addWidget(thumb_app_row)
        add_settings_helper(
            insp_l,
            "Double-click the Inspector thumbnail (or Open thumbnail file) runs this app with the image path. "
            "Hover the preview for sequence play/pause.",
        )

        insp_l.addWidget(settings_divider(insp_card))
        add_settings_subsection_title(insp_l, "Video preview player")

        self._video_player_backend_combo = QComboBox(insp_card)
        style_settings_combo(self._video_player_backend_combo, width=220)
        for label, val in (
            ("mpv embed (default)", BACKEND_MPV),
            ("Auto (mpv → Qt → external)", BACKEND_AUTO),
            ("Qt Multimedia only", BACKEND_QT),
            ("External app only", BACKEND_EXTERNAL),
        ):
            self._video_player_backend_combo.addItem(label, val)
        try:
            cur = read_video_player_backend(self._settings)
            for i in range(self._video_player_backend_combo.count()):
                if self._video_player_backend_combo.itemData(i) == cur:
                    self._video_player_backend_combo.setCurrentIndex(i)
                    break
        except Exception:
            pass
        add_settings_field_row(insp_l, "Playback backend", self._video_player_backend_combo)

        mpv_row = QWidget(insp_card)
        mpv_row_l = QHBoxLayout(mpv_row)
        mpv_row_l.setContentsMargins(0, 0, 0, 0)
        mpv_row_l.setSpacing(8)
        self._mpv_dir_field = QLineEdit(mpv_row)
        style_settings_line_edit(self._mpv_dir_field, min_width=200)
        self._mpv_dir_field.setPlaceholderText("Folder containing mpv-2.dll (optional)")
        try:
            self._mpv_dir_field.setText(read_mpv_directory(self._settings))
        except Exception:
            self._mpv_dir_field.setText("")
        btn_mpv_browse = QPushButton("Browse…", mpv_row)
        btn_mpv_browse.setObjectName("SettingsCategoryActionButton")
        btn_mpv_browse.clicked.connect(self._browse_mpv_directory)
        btn_mpv_clear = QPushButton("Clear", mpv_row)
        btn_mpv_clear.setObjectName("SettingsCategoryActionButton")
        btn_mpv_clear.clicked.connect(lambda: self._mpv_dir_field.setText(""))
        mpv_row_l.addWidget(self._mpv_dir_field, 1)
        mpv_row_l.addWidget(btn_mpv_browse, 0)
        mpv_row_l.addWidget(btn_mpv_clear, 0)
        insp_l.addWidget(mpv_row)
        add_settings_field_row(insp_l, "libmpv folder", mpv_row)

        vid_ext_row = QWidget(insp_card)
        vid_ext_row_l = QHBoxLayout(vid_ext_row)
        vid_ext_row_l.setContentsMargins(0, 0, 0, 0)
        vid_ext_row_l.setSpacing(8)
        self._video_external_player_field = QLineEdit(vid_ext_row)
        style_settings_line_edit(self._video_external_player_field, min_width=200)
        self._video_external_player_field.setPlaceholderText("External player .exe (fallback)")
        try:
            self._video_external_player_field.setText(read_video_external_player_exe(self._settings))
        except Exception:
            self._video_external_player_field.setText("")
        btn_vid_browse = QPushButton("Browse…", vid_ext_row)
        btn_vid_browse.setObjectName("SettingsCategoryActionButton")
        btn_vid_browse.clicked.connect(self._browse_video_external_player)
        btn_vid_clear = QPushButton("Clear", vid_ext_row)
        btn_vid_clear.setObjectName("SettingsCategoryActionButton")
        btn_vid_clear.clicked.connect(lambda: self._video_external_player_field.setText(""))
        vid_ext_row_l.addWidget(self._video_external_player_field, 1)
        vid_ext_row_l.addWidget(btn_vid_browse, 0)
        vid_ext_row_l.addWidget(btn_vid_clear, 0)
        insp_l.addWidget(vid_ext_row)
        add_settings_field_row(insp_l, "External player", vid_ext_row)

        mpv_status = "not found"
        try:
            mpv_status = format_mpv_detect_status(self._settings)
        except Exception:
            pass
        self._mpv_detect_helper = QLabel(
            "Embedded playback uses libmpv (mpv-2.dll). Install via Settings → Updates → libmpv, "
            "or bundle at build time. Double-click a video in Inbox or Project Guide to preview. "
            f"Detected: {mpv_status}",
            insp_card,
        )
        self._mpv_detect_helper.setWordWrap(True)
        self._mpv_detect_helper.setObjectName("DialogHelper")
        insp_l.addWidget(self._mpv_detect_helper)
        layout.addWidget(insp_card)

        tray_card, tray_l = add_settings_section(
            inner,
            "System tray",
            "Run MONOS in the background and open it quickly from the notification area.",
        )

        from monostudio.core.tray_preferences import (
            read_close_action,
            read_start_minimized_to_tray,
            read_start_with_windows,
            read_tray_enabled,
        )

        self._tray_enabled_cb = QCheckBox("Show MONOS icon in the system tray", tray_card)
        try:
            self._tray_enabled_cb.setChecked(read_tray_enabled(self._settings))
        except Exception:
            self._tray_enabled_cb.setChecked(True)
        tray_l.addWidget(self._tray_enabled_cb)

        self._tray_close_action_combo = QComboBox(tray_card)
        style_settings_combo(self._tray_close_action_combo, width=280)
        self._tray_close_action_combo.addItem("Ask when I close the window", "unset")
        self._tray_close_action_combo.addItem("Hide to system tray", "minimize")
        self._tray_close_action_combo.addItem("Quit MONOS completely", "quit")
        try:
            cur = read_close_action(self._settings)
            for i in range(self._tray_close_action_combo.count()):
                if self._tray_close_action_combo.itemData(i) == cur:
                    self._tray_close_action_combo.setCurrentIndex(i)
                    break
        except Exception:
            pass
        add_settings_field_row(tray_l, "When I close the window", self._tray_close_action_combo)

        self._tray_start_windows_cb = QCheckBox("Start MONOS when Windows starts", tray_card)
        self._tray_start_windows_cb.setEnabled(sys.platform == "win32")
        try:
            self._tray_start_windows_cb.setChecked(read_start_with_windows(self._settings))
        except Exception:
            self._tray_start_windows_cb.setChecked(False)
        tray_l.addWidget(self._tray_start_windows_cb)

        self._tray_start_minimized_cb = QCheckBox(
            "After Windows sign-in, show a short splash then stay in the tray",
            tray_card,
        )
        try:
            self._tray_start_minimized_cb.setChecked(read_start_minimized_to_tray(self._settings))
        except Exception:
            self._tray_start_minimized_cb.setChecked(True)
        tray_l.addWidget(self._tray_start_minimized_cb)

        self._tray_autostart_status = QLabel("", tray_card)
        self._tray_autostart_status.setWordWrap(True)
        self._tray_autostart_status.setObjectName("DialogHelper")
        tray_l.addWidget(self._tray_autostart_status)
        self._tray_start_windows_cb.toggled.connect(self._on_tray_start_windows_toggled)
        self._tray_start_windows_cb.toggled.connect(self._refresh_tray_autostart_status)
        self._on_tray_start_windows_toggled(self._tray_start_windows_cb.isChecked())
        self._refresh_tray_autostart_status()

        layout.addWidget(tray_card)
        layout.addStretch(1)

        scroll.setWidget(inner)
        return scroll

    def _on_tray_start_windows_toggled(self, checked: bool) -> None:
        if self._tray_start_minimized_cb is not None:
            self._tray_start_minimized_cb.setEnabled(bool(checked))

    def _refresh_tray_autostart_status(self) -> None:
        label = getattr(self, "_tray_autostart_status", None)
        if label is None:
            return
        if sys.platform != "win32":
            label.setText("Windows startup registration is only available on Windows.")
            return
        from monostudio.core.windows_autostart import is_autostart_enabled

        if is_autostart_enabled():
            label.setText("Registered in Windows startup (current user). Save Settings to apply changes.")
        else:
            label.setText("Not registered for Windows startup. Enable the option above and save.")

    def _write_inspector_thumb_segment(
        self,
        segment: SettingsSegmentedControl | None,
        entity: str,
    ) -> None:
        if self._settings is None or segment is None:
            return
        mode = segment.value()
        if mode == "user":
            write_inspector_thumbnail_source(self._settings, THUMB_SOURCE_USER, entity=entity)
        elif mode == "render":
            write_inspector_thumbnail_source(
                self._settings, THUMB_SOURCE_RENDER_SEQUENCE, entity=entity
            )
        else:
            write_inspector_thumbnail_source(
                self._settings, THUMB_SOURCE_USER_THEN_RENDER, entity=entity
            )

    def _refresh_windows_noti_status(self) -> None:
        label = getattr(self, "_windows_noti_status", None)
        btn = getattr(self, "_test_windows_noti_btn", None)
        if label is None:
            return
        if sys.platform != "win32":
            label.setText("Windows notifications are only available on Windows.")
            if btn is not None:
                btn.setEnabled(False)
            return
        combo = getattr(self, "_mention_delivery_combo", None)
        if combo is not None and combo.currentIndex() != 1:
            label.setText("Select “Windows notification” to enable Action Center @mention popups.")
            if btn is not None:
                btn.setEnabled(False)
            return
        if btn is not None:
            btn.setEnabled(True)
        from monostudio.core.windows_toast import toast_readiness

        _ready, msg = toast_readiness()
        label.setText(msg)

    def _refresh_nav_quick_slot_labels(self) -> None:
        from monostudio.ui_qt.nav_quick_view import describe_nav_quick_slot, load_nav_quick_slot

        labels = getattr(self, "_nav_quick_slot_labels", None)
        if not labels or self._settings is None:
            return
        for idx, summary in enumerate(labels, start=1):
            payload = load_nav_quick_slot(self._settings, idx)
            summary.setText(describe_nav_quick_slot(payload))

    def _on_clear_nav_quick_slot(self, slot: int) -> None:
        from monostudio.ui_qt.nav_quick_view import clear_nav_quick_slot

        if self._settings is None:
            return
        clear_nav_quick_slot(self._settings, slot)
        self._refresh_nav_quick_slot_labels()
        self.nav_quick_slots_changed.emit()

    def _on_clear_all_nav_quick_slots(self) -> None:
        from monostudio.ui_qt.nav_quick_view import clear_all_nav_quick_slots

        if self._settings is None:
            return
        clear_all_nav_quick_slots(self._settings)
        self._refresh_nav_quick_slot_labels()
        self.nav_quick_slots_changed.emit()

    def _on_test_windows_notification(self) -> None:
        from monostudio.core.notification_copy import pick_copy
        from monostudio.core.notification_preferences import read_notification_vietnamese
        from monostudio.core.windows_toast import show_mention_toast, toast_readiness

        ready, msg = toast_readiness()
        if not ready:
            QMessageBox.warning(self, "Windows notification", msg)
            self._refresh_windows_noti_status()
            return
        vi = read_notification_vietnamese(self._settings)
        body = pick_copy("Thông báo thử từ MONOS.", "Test notification from MONOS.", vietnamese=vi)
        if show_mention_toast("MONOS", body):
            QMessageBox.information(
                self,
                "Windows notification",
                "Test sent. Check the Windows Action Center (bell icon on the taskbar). "
                "If nothing appears, open Settings → System → Notifications and allow MONOS.",
            )
        else:
            QMessageBox.warning(
                self,
                "Windows notification",
                "Could not send the test toast. @mentions will fall back to in-app MONOS toasts.",
            )
        self._refresh_windows_noti_status()

    def _browse_inspector_thumbnail_open_exe(self) -> None:
        start = ""
        try:
            if self._inspector_thumb_open_exe_field is not None:
                t = (self._inspector_thumb_open_exe_field.text() or "").strip()
                if t:
                    p = Path(t)
                    if p.parent.is_dir():
                        start = str(p.parent)
        except Exception:
            start = ""
        path, _flt = QFileDialog.getOpenFileName(
            self,
            "Select application for thumbnails",
            start,
            "Executable (*.exe);;All files (*.*)",
        )
        if path and self._inspector_thumb_open_exe_field is not None:
            self._inspector_thumb_open_exe_field.setText(path.strip())

    def _browse_mpv_directory(self) -> None:
        start = ""
        try:
            if self._mpv_dir_field is not None:
                t = (self._mpv_dir_field.text() or "").strip()
                if t:
                    p = Path(t)
                    start = str(p if p.is_dir() else p.parent)
        except Exception:
            start = ""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select folder containing mpv-2.dll",
            start,
        )
        if folder and self._mpv_dir_field is not None:
            self._mpv_dir_field.setText(folder.strip())

    def _browse_video_external_player(self) -> None:
        start = ""
        try:
            if self._video_external_player_field is not None:
                t = (self._video_external_player_field.text() or "").strip()
                if t:
                    p = Path(t)
                    if p.parent.is_dir():
                        start = str(p.parent)
        except Exception:
            start = ""
        path, _flt = QFileDialog.getOpenFileName(
            self,
            "Select external video player",
            start,
            "Executable (*.exe);;All files (*.*)",
        )
        if path and self._video_external_player_field is not None:
            self._video_external_player_field.setText(path.strip())

    def _build_behavior_tab(self) -> QWidget:
        """General → Behavior: global pipeline options (create asset/shot)."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        grp = QGroupBox("New Asset / Shot", root)
        grp_layout = QVBoxLayout(grp)
        self._create_work_publish_subfolders_cb = QCheckBox(
            "Create work/ and publish/ inside departments",
            grp,
        )
        try:
            if self._settings is not None:
                v = self._settings.value("pipeline/create_work_publish_subfolders", True, type=bool)
                self._create_work_publish_subfolders_cb.setChecked(bool(v))
            else:
                self._create_work_publish_subfolders_cb.setChecked(True)
        except Exception:
            self._create_work_publish_subfolders_cb.setChecked(True)
        hint = QLabel(
            "When creating a new asset or shot, create work/ and publish/ subfolders inside each department folder. This setting applies globally to all projects.",
            grp,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        grp_layout.addWidget(self._create_work_publish_subfolders_cb)
        grp_layout.addWidget(hint)
        layout.addWidget(grp)
        layout.addStretch(1)
        return root

    def _build_hotkeys_tab(self) -> QWidget:
        from monostudio.ui_qt.app_hotkeys import HotkeysSettingsWidget

        self._hotkeys_widget = HotkeysSettingsWidget(self._settings, self)
        return self._hotkeys_widget

    def _build_access_tab(self) -> QWidget:
        """General → Access: shared key source info, unlock session, developer-only diagnostics."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        grp_src = QGroupBox("Bundled keys (repository / build only)", root)
        gk = QVBoxLayout(grp_src)
        self._access_keys_info_label = QLabel("", grp_src)
        self._access_keys_info_label.setWordWrap(True)
        self._access_keys_info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._access_keys_info_label.setProperty("mono", True)
        gk.addWidget(self._access_keys_info_label)
        hint_k = QLabel(
            "Keys are defined only in monostudio/core/access_keys_bundled.py in source control. "
            "They ship inside the app; users cannot change them from Settings.",
            grp_src,
        )
        hint_k.setWordWrap(True)
        hint_k.setObjectName("DialogHelper")
        gk.addWidget(hint_k)
        layout.addWidget(grp_src)

        grp_unlock = QGroupBox("Session unlock", root)
        gl = QVBoxLayout(grp_unlock)
        self._access_status_label = QLabel("Locked — enter a key to unlock.", grp_unlock)
        self._access_status_label.setObjectName("DialogHelper")
        gl.addWidget(self._access_status_label)
        row = QHBoxLayout()
        self._access_unlock_field = QLineEdit(grp_unlock)
        self._access_unlock_field.setPlaceholderText("Administrator or developer key")
        self._access_unlock_field.setEchoMode(QLineEdit.EchoMode.Password)
        btn_apply = QPushButton("Unlock", grp_unlock)
        btn_apply.setObjectName("DialogPrimaryButton")
        btn_apply.clicked.connect(self._on_access_unlock_clicked)
        btn_lock = QPushButton("Lock session", grp_unlock)
        btn_lock.setObjectName("DialogSecondaryButton")
        btn_lock.clicked.connect(self._on_access_lock_clicked)
        row.addWidget(self._access_unlock_field, 1)
        row.addWidget(btn_apply, 0)
        row.addWidget(btn_lock, 0)
        gl.addLayout(row)
        self._access_remember_cb = QCheckBox(
            "Remember unlock on this device (restore after restart)", grp_unlock
        )
        self._access_remember_cb.setChecked(read_access_remember_preferred())
        gl.addWidget(self._access_remember_cb)
        hint_u = QLabel(
            "Administrator: pipeline structure and scan rules. Developer: same, plus debug logging and splash timing. "
            "Unlock lasts for this session; use Remember to skip re-entering the key after restart. "
            "Lock session clears remembered unlock on this machine.",
            grp_unlock,
        )
        hint_u.setWordWrap(True)
        hint_u.setObjectName("DialogHelper")
        gl.addWidget(hint_u)
        layout.addWidget(grp_unlock)

        grp_dev = QGroupBox("Developer", root)
        gd = QVBoxLayout(grp_dev)
        self._access_debug_cb = QCheckBox("Verbose debug logging (extra loggers → stderr)", grp_dev)
        try:
            if self._settings is not None:
                self._access_debug_cb.setChecked(read_verbose_debug_enabled(self._settings))
        except Exception:
            pass
        splash_row = QHBoxLayout()
        self._access_splash_spin = QSpinBox(grp_dev)
        self._access_splash_spin.setRange(0, 60_000)
        self._access_splash_spin.setSingleStep(100)
        try:
            if self._settings is not None:
                self._access_splash_spin.setValue(read_splash_display_ms(self._settings))
            else:
                self._access_splash_spin.setValue(2000)
        except Exception:
            self._access_splash_spin.setValue(2000)
        splash_row.addWidget(QLabel("Splash minimum display (ms):", grp_dev))
        splash_row.addWidget(self._access_splash_spin, 1)
        gd.addWidget(self._access_debug_cb)
        gd.addLayout(splash_row)
        hint_d = QLabel(
            "Applies after you save Settings and restart the app.",
            grp_dev,
        )
        hint_d.setWordWrap(True)
        hint_d.setObjectName("DialogHelper")
        gd.addWidget(hint_d)
        layout.addWidget(grp_dev)

        layout.addStretch(1)
        return root

    def _on_settings_nav_row_changed(self, row: int) -> None:
        self._content_stack.setCurrentIndex(row)
        if row == 1:
            self._refresh_pipeline_access_lock()
        if row == 3:
            self._refresh_integrations_access_lock()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._refresh_access_tab_state()
        self._refresh_pipeline_access_lock()
        self._refresh_discord_integrations_ui()
        self._refresh_integrations_access_lock()

    def _refresh_access_tab_state(self) -> None:
        if self._access_status_label is None:
            return
        s = self._settings
        a_cfg = admin_key_configured()
        d_cfg = dev_key_configured()
        if self._access_keys_info_label is not None:
            try:
                mod_path = bundled_access_keys_module_path()
            except Exception:
                mod_path = None
            lines = [
                f"Module: {mod_path}" if mod_path else "Module: monostudio.core.access_keys_bundled",
                f"Administrator key: {'configured' if a_cfg else 'not configured'}",
                f"Developer key: {'configured' if d_cfg else 'not configured'}",
            ]
            self._access_keys_info_label.setText("\n".join(lines))
        role = session_role()
        if not has_access_restrictions():
            self._access_status_label.setText("No keys configured — pipeline and scan rules are not restricted.")
        elif role == AccessRole.DEV:
            self._access_status_label.setText("Session: Developer (full access).")
        elif role == AccessRole.ADMIN:
            self._access_status_label.setText("Session: Administrator (pipeline & scan rules).")
        else:
            self._access_status_label.setText("Restricted — unlock with an administrator or developer key.")
        if role != AccessRole.NONE and has_access_restrictions():
            from monostudio.core.user_identity import _read_app_settings

            remembered = isinstance(_read_app_settings().get(KEY_ACCESS_REMEMBER), dict)
            if remembered:
                self._access_status_label.setText(
                    self._access_status_label.text() + " · remembered on this device"
                )
        if self._access_unlock_field:
            self._access_unlock_field.setEnabled(True)
        dev_on = is_dev_session()
        if self._access_debug_cb:
            self._access_debug_cb.setEnabled(dev_on and s is not None)
        if self._access_splash_spin:
            self._access_splash_spin.setEnabled(dev_on and s is not None)

    def _refresh_pipeline_access_lock(self) -> None:
        admin_ok = is_admin_capable()
        if self._pipeline_editor is not None:
            self._pipeline_editor.setEnabled(admin_ok)
        if self._publish_ignore_ext_field is not None:
            self._publish_ignore_ext_field.setEnabled(admin_ok)
        if self._pipeline_access_banner is not None:
            if has_access_restrictions() and not admin_ok:
                self._pipeline_access_banner.setText(
                    "Pipeline structure and scan rules are locked. Unlock in General → Access with an administrator or developer key."
                )
                self._pipeline_access_banner.setVisible(True)
            else:
                self._pipeline_access_banner.setVisible(False)

    def _refresh_integrations_access_lock(self) -> None:
        admin_ok = is_admin_capable()
        widgets = (
            self._discord_enabled_cb,
            self._discord_webhook_field,
            self._discord_url_replace_btn,
            self._discord_label_field,
            self._discord_mention_cb,
            self._discord_note_done_cb,
            self._discord_inbox_cb,
            self._discord_schedule_cb,
            self._discord_schedule_assigned_cb,
            self._discord_test_btn,
            self._discord_test_notifications_btn,
        )
        for w in widgets:
            if w is not None:
                w.setEnabled(admin_ok)
        if self._discord_integrations_banner is not None:
            if has_access_restrictions() and not admin_ok:
                self._discord_integrations_banner.setText(
                    "Discord integration is locked. Unlock in General → Access with an administrator or developer key."
                )
                self._discord_integrations_banner.setVisible(True)
            else:
                self._discord_integrations_banner.setVisible(False)

    def _refresh_discord_integrations_ui(self) -> None:
        from monostudio.core.integrations_config import (
            get_primary_webhook,
            is_event_enabled,
            load_integrations,
            mask_webhook_url,
        )

        if self._discord_enabled_cb is None:
            return
        if self._workspace_root is None:
            self._discord_stored_url = ""
            self._discord_url_editing = False
            self._discord_enabled_cb.setChecked(False)
            if self._discord_mention_cb is not None:
                self._discord_mention_cb.setChecked(True)
            if self._discord_note_done_cb is not None:
                self._discord_note_done_cb.setChecked(False)
            if self._discord_inbox_cb is not None:
                self._discord_inbox_cb.setChecked(False)
            if self._discord_schedule_cb is not None:
                self._discord_schedule_cb.setChecked(False)
            if self._discord_schedule_assigned_cb is not None:
                self._discord_schedule_assigned_cb.setChecked(False)
            if self._discord_label_field is not None:
                self._discord_label_field.clear()
            if self._discord_webhook_field is not None:
                self._discord_webhook_field.clear()
                self._discord_webhook_field.setReadOnly(True)
                self._discord_webhook_field.setPlaceholderText("Select a workspace first")
            return

        config = load_integrations(self._workspace_root)
        discord = config.get("discord") if isinstance(config.get("discord"), dict) else {}
        wh = get_primary_webhook(config)
        self._discord_stored_url = str(wh.get("url") or "").strip() if wh else ""
        self._discord_url_editing = False
        self._discord_enabled_cb.setChecked(bool(discord.get("enabled")))
        if self._discord_mention_cb is not None:
            self._discord_mention_cb.setChecked(is_event_enabled(config, "mention"))
        if self._discord_note_done_cb is not None:
            self._discord_note_done_cb.setChecked(is_event_enabled(config, "note_done"))
        if self._discord_inbox_cb is not None:
            self._discord_inbox_cb.setChecked(
                is_event_enabled(config, "inbox_received")
                or is_event_enabled(config, "inbox_distributed")
                or is_event_enabled(config, "outbox_received")
            )
        if self._discord_schedule_cb is not None:
            self._discord_schedule_cb.setChecked(is_event_enabled(config, "schedule_due"))
        if self._discord_schedule_assigned_cb is not None:
            self._discord_schedule_assigned_cb.setChecked(is_event_enabled(config, "schedule_assigned"))
        if self._discord_label_field is not None:
            self._discord_label_field.setText(str(wh.get("label") or "").strip() if wh else "")
        if self._discord_webhook_field is not None:
            if self._discord_stored_url:
                self._discord_webhook_field.setText(mask_webhook_url(self._discord_stored_url))
                self._discord_webhook_field.setReadOnly(True)
                self._discord_webhook_field.setPlaceholderText("")
            else:
                self._discord_webhook_field.clear()
                self._discord_webhook_field.setReadOnly(False)
                self._discord_webhook_field.setPlaceholderText(
                    "https://discord.com/api/webhooks/…"
                )

    def _discord_effective_webhook_url(self) -> str:
        from monostudio.core.integrations_config import is_valid_discord_webhook_url

        if self._discord_url_editing and self._discord_webhook_field is not None:
            candidate = (self._discord_webhook_field.text() or "").strip()
            if is_valid_discord_webhook_url(candidate):
                return candidate
        if self._discord_stored_url:
            return self._discord_stored_url
        if self._discord_webhook_field is not None:
            candidate = (self._discord_webhook_field.text() or "").strip()
            if is_valid_discord_webhook_url(candidate):
                return candidate
        return ""

    def _on_discord_replace_url(self) -> None:
        if self._discord_webhook_field is None:
            return
        self._discord_url_editing = True
        self._discord_webhook_field.setReadOnly(False)
        self._discord_webhook_field.clear()
        self._discord_webhook_field.setPlaceholderText("https://discord.com/api/webhooks/…")
        self._discord_webhook_field.setFocus()

    def _on_discord_send_test(self) -> None:
        from monostudio.core.discord_webhook import send_test_webhook
        from monostudio.core.user_identity import get_current_user_display_name

        url = self._discord_effective_webhook_url()
        ok, err = send_test_webhook(
            self._workspace_root,
            user_name=get_current_user_display_name(self._workspace_root),
            url_override=url,
        )
        if ok:
            QMessageBox.information(self, "Discord", "Test message sent.")
        else:
            QMessageBox.warning(self, "Discord", err or "Could not send test message.")

    def _on_discord_test_notifications(self) -> None:
        from monostudio.core.user_identity import get_current_user_display_name
        from monostudio.ui_qt.discord_notification_test_dialog import DiscordNotificationTestDialog

        dlg = DiscordNotificationTestDialog(
            self._workspace_root,
            url_resolver=self._discord_effective_webhook_url,
            user_name=get_current_user_display_name(self._workspace_root),
            parent=self,
        )
        dlg.exec()

    def _persist_discord_integrations(self) -> bool:
        from monostudio.core.integrations_config import (
            build_integrations_from_ui,
            is_valid_discord_webhook_url,
            load_integrations,
            write_integrations,
        )

        if self._workspace_root is None or self._discord_enabled_cb is None:
            return True
        enabled = self._discord_enabled_cb.isChecked()
        url = self._discord_effective_webhook_url()
        label = (self._discord_label_field.text() or "").strip() if self._discord_label_field else ""
        mention = bool(self._discord_mention_cb and self._discord_mention_cb.isChecked())
        note_done = bool(self._discord_note_done_cb and self._discord_note_done_cb.isChecked())
        inbox_enabled = bool(self._discord_inbox_cb and self._discord_inbox_cb.isChecked())
        schedule_due = bool(self._discord_schedule_cb and self._discord_schedule_cb.isChecked())
        schedule_assigned = bool(
            self._discord_schedule_assigned_cb and self._discord_schedule_assigned_cb.isChecked()
        )
        if enabled and not is_valid_discord_webhook_url(url):
            QMessageBox.warning(
                self,
                "Discord",
                "Enable Discord requires a valid webhook URL.\n"
                "Create an Incoming Webhook in Discord channel settings, then paste the URL here.",
            )
            return False
        existing = load_integrations(self._workspace_root)
        config = build_integrations_from_ui(
            enabled=enabled,
            webhook_url=url,
            label=label,
            mention_enabled=mention,
            inbox_enabled=inbox_enabled,
            schedule_due_enabled=schedule_due,
            schedule_assigned_enabled=schedule_assigned,
            note_done_enabled=note_done,
            existing=existing,
        )
        try:
            write_integrations(self._workspace_root, config, require_admin=True)
        except PermissionError:
            QMessageBox.warning(
                self,
                "Discord",
                "Administrator access is required to save Discord integration settings.",
            )
            return False
        except OSError as ex:
            QMessageBox.warning(self, "Discord", str(ex) or "Could not save integrations.")
            return False
        self._discord_stored_url = url
        self._discord_url_editing = False
        self._refresh_discord_integrations_ui()
        return True

    def _on_access_unlock_clicked(self) -> None:
        if self._access_unlock_field is None:
            return
        entered = (self._access_unlock_field.text() or "").strip()
        remember = bool(self._access_remember_cb and self._access_remember_cb.isChecked())
        role = try_unlock(entered, remember=remember)
        if role is None:
            QMessageBox.warning(self, "Access", "Key does not match any configured administrator or developer key.")
        else:
            self._access_unlock_field.clear()
            write_access_remember_preferred(remember)
            if not remember:
                forget_remembered_access()
        self.access_session_changed.emit()
        self._refresh_access_tab_state()
        self._refresh_pipeline_access_lock()
        self._refresh_integrations_access_lock()

    def _on_access_lock_clicked(self) -> None:
        clear_session()
        forget_remembered_access()
        self.access_session_changed.emit()
        self._refresh_access_tab_state()
        self._refresh_pipeline_access_lock()
        self._refresh_integrations_access_lock()

    def _build_updates_tab(self) -> QWidget:
        """General → Updates: one list (MonoStudio + other products), each row: icon, name, version, View release notes, action button."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Status row (Windows Update style): left = icon + message + last checked, right = Check button
        status_row = QWidget(root)
        status_row.setObjectName("UpdateStatusRow")
        status_row_l = QHBoxLayout(status_row)
        status_row_l.setContentsMargins(0, 0, 0, 0)
        status_row_l.setSpacing(12)

        self._update_status_icon = QLabel(status_row)
        self._update_status_icon.setFixedSize(_UPDATE_STATUS_ICON_SIZE, _UPDATE_STATUS_ICON_SIZE)
        self._update_status_icon.setScaledContents(True)
        self._update_status_icon.setObjectName("UpdateStatusIcon")
        status_row_l.addWidget(self._update_status_icon)

        status_text_col = QWidget(status_row)
        status_text_col.setObjectName("UpdateStatusTextCol")
        status_text_l = QVBoxLayout(status_text_col)
        status_text_l.setContentsMargins(0, 0, 0, 0)
        status_text_l.setSpacing(2)
        self._update_status_label = QLabel("", status_text_col)
        self._update_status_label.setWordWrap(True)
        self._update_status_label.setObjectName("UpdateStatusMessage")
        status_text_l.addWidget(self._update_status_label)
        self._update_last_checked_label = QLabel("", status_text_col)
        self._update_last_checked_label.setObjectName("UpdateStatusLastChecked")
        status_text_l.addWidget(self._update_last_checked_label)
        status_row_l.addWidget(status_text_col, 1)

        self._update_check_btn = QPushButton("Check for updates", status_row)
        self._update_check_btn.setObjectName("DialogPrimaryButton")
        self._update_check_btn.clicked.connect(self._on_check_for_updates)
        status_row_l.addWidget(self._update_check_btn, 0)
        layout.addWidget(status_row)

        self._update_last_checked_time: datetime | None = None
        if self._settings:
            last_check_str = self._settings.value("updates/last_check_time", None, str)
            if last_check_str:
                try:
                    self._update_last_checked_time = datetime.fromisoformat(last_check_str)
                except (ValueError, TypeError):
                    pass
        self._set_update_status_display(
            "You're up to date",
            "square-check",
            MONOS_COLORS.get("emerald_500", "#10b981"),
        )

        # Unified product list: MonoStudio 26 first, then EXTRA_REPOS (e.g. MonoFXSuite)
        list_container = QFrame(root)
        list_container.setObjectName("UpdateProductList")
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        # (product_id, display_name, repo_or_none). repo for fallback "View on GitHub" URL.
        products: list[tuple[str, str, str | None]] = [("monostudio", "MonoStudio 26", None)] + [
            (display_name, display_name, repo) for display_name, repo in EXTRA_REPOS
        ]

        self._update_monostudio_version_label: QLabel | None = None
        self._update_monostudio_link_btn: QPushButton | None = None
        self._update_monostudio_action_btn: QPushButton | None = None
        self._update_extra_cards: dict[str, tuple[QLabel, QPushButton, QPushButton]] = {}
        self._update_extra_loading: dict[str, tuple[QWidget, QProgressBar, QToolButton]] = {}
        self._update_extra_html_url: dict[str, str] = {}
        self._update_extra_fallback_url: dict[str, str] = {}
        self._update_extra_download_url: dict[str, str] = {}
        self._update_download_product: str = ""  # "monostudio" or extra display_name

        for product_id, display_name, repo in products:
            row = QWidget(list_container)
            row.setObjectName("UpdateProductListRow")
            row.setFixedHeight(44)
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(12, 0, 12, 0)
            row_l.setSpacing(12)

            icon_l = QLabel(row)
            icon_l.setFixedSize(_UPDATE_ROW_ICON_SIZE, _UPDATE_ROW_ICON_SIZE)
            icon_l.setScaledContents(True)
            icon_l.setPixmap(_update_product_icon_pixmap(product_id))
            row_l.addWidget(icon_l)

            name_l = QLabel(display_name, row)
            name_l.setObjectName("UpdateProductListName")
            row_l.addWidget(name_l)

            ver_l = QLabel(
                get_app_version() if product_id == "monostudio" else (get_extra_tool_installed_version(display_name) or "—"),
                row,
            )
            ver_l.setObjectName("UpdateProductListVersion")
            ver_l.setProperty("mono", True)
            row_l.addWidget(ver_l)

            row_l.addStretch(1)

            link_btn = QPushButton("View release notes", row)
            link_btn.setObjectName("UpdateProductListLink")
            link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            link_btn.setVisible(False)
            row_l.addWidget(link_btn)

            if product_id == "monostudio":
                self._update_monostudio_version_label = ver_l
                self._update_monostudio_link_btn = link_btn
                action_btn = QPushButton("Latest", row)
                action_btn.setObjectName("UpdateProductListBtnLatest")
                action_btn.clicked.connect(self._on_download_and_install)
                link_btn.clicked.connect(self._on_view_release_on_github)
                self._update_monostudio_action_btn = action_btn

                loading_widget = QWidget(row)
                loading_widget.setObjectName("UpdateDownloadLoading")
                loading_l = QHBoxLayout(loading_widget)
                loading_l.setContentsMargins(0, 0, 0, 0)
                loading_l.setSpacing(_UPDATE_CANCEL_GAP)
                progress_bar = QProgressBar(loading_widget)
                progress_bar.setObjectName("UpdateDownloadProgress")
                progress_bar.setMinimum(0)
                progress_bar.setMaximum(0)
                loading_l.addWidget(progress_bar)
                cancel_btn = QToolButton(loading_widget)
                _configure_update_cancel_btn(cancel_btn)
                loading_l.addWidget(cancel_btn)
                loading_widget.hide()

                self._update_monostudio_loading_widget = loading_widget
                self._update_monostudio_progress_bar = progress_bar
                self._update_monostudio_cancel_btn = cancel_btn

                action_container = QWidget(row)
                action_container_l = QHBoxLayout(action_container)
                action_container_l.setContentsMargins(0, 0, 0, 0)
                action_container_l.setSpacing(0)
                action_container_l.addWidget(action_btn)
                action_container_l.addWidget(loading_widget)
                _apply_update_action_width(
                    action_btn,
                    loading_widget=loading_widget,
                    progress_bar=progress_bar,
                )
                row_l.addWidget(action_container)
            else:
                action_btn = QPushButton("View on GitHub", row)
                action_btn.setObjectName("SettingsCategoryActionButton")
                if repo:
                    fallback_url = f"https://github.com/{repo}/releases"
                    self._update_extra_fallback_url[display_name] = fallback_url
                    self._update_extra_html_url[display_name] = fallback_url
                    action_btn.setVisible(True)
                else:
                    action_btn.setVisible(False)
                action_btn.clicked.connect(lambda checked=False, n=display_name: self._on_extra_repo_action_clicked(n))
                link_btn.clicked.connect(lambda checked=False, n=display_name: self._on_extra_repo_release_link_clicked(n))
                self._update_extra_cards[display_name] = (ver_l, link_btn, action_btn)

                loading_widget = QWidget(row)
                loading_widget.setObjectName("UpdateDownloadLoading")
                loading_l = QHBoxLayout(loading_widget)
                loading_l.setContentsMargins(0, 0, 0, 0)
                loading_l.setSpacing(_UPDATE_CANCEL_GAP)
                progress_bar = QProgressBar(loading_widget)
                progress_bar.setObjectName("UpdateDownloadProgress")
                progress_bar.setMinimum(0)
                progress_bar.setMaximum(0)
                loading_l.addWidget(progress_bar)
                cancel_btn = QToolButton(loading_widget)
                _configure_update_cancel_btn(cancel_btn)
                loading_l.addWidget(cancel_btn)
                loading_widget.hide()
                self._update_extra_loading[display_name] = (loading_widget, progress_bar, cancel_btn)

                action_container = QWidget(row)
                action_container_l = QHBoxLayout(action_container)
                action_container_l.setContentsMargins(0, 0, 0, 0)
                action_container_l.setSpacing(0)
                action_container_l.addWidget(action_btn)
                action_container_l.addWidget(loading_widget)
                _apply_update_action_width(
                    action_btn,
                    loading_widget=loading_widget,
                    progress_bar=progress_bar,
                )
                row_l.addWidget(action_container)

            list_layout.addWidget(row)

        self._build_ffmpeg_update_row(list_container, list_layout)
        self._build_mpv_update_row(list_container, list_layout)
        self._refresh_ffmpeg_update_row()
        self._refresh_mpv_update_row()

        layout.addWidget(list_container)

        # Release notes
        notes_label = QLabel("RELEASE NOTES", root)
        notes_label.setObjectName("UpdateSectionLabel")
        layout.addWidget(notes_label)
        self._update_changelog = QTextEdit(root)
        self._update_changelog.setReadOnly(True)
        self._update_changelog.setPlaceholderText("Click \"Check for updates\" to fetch the latest release notes from GitHub.")
        self._update_changelog.setMinimumHeight(200)
        self._update_changelog.setObjectName("UpdateChangelog")
        layout.addWidget(self._update_changelog, 1)

        hint = QLabel(
            "Updates are delivered via GitHub Releases. Download runs the installer and closes the app. "
            "FFmpeg is used for DPX / EXR / video thumbnails — Get FFmpeg (download to temp) then Install, or locate ffmpeg.exe. "
            "libmpv (mpv-2.dll) powers embedded video preview — Get libmpv then Install (requires 7-Zip).",
            root,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        layout.addWidget(hint, 0)

        self._pending_update_info: UpdateInfo | None = None
        self._update_latest_html_url: str = ""
        self._update_check_worker: _UpdateCheckWorker | None = None
        self._update_download_worker: _DownloadWorker | None = None
        return root

    def _build_ffmpeg_update_row(self, list_container: QWidget, list_layout: QVBoxLayout) -> None:
        """FFmpeg row: Get → download to temp → Install (extract to LocalAppData) + locate + official link."""
        row = QWidget(list_container)
        row.setObjectName("UpdateProductListRow")
        row.setFixedHeight(44)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(12, 0, 12, 0)
        row_l.setSpacing(12)

        icon_l = QLabel(row)
        icon_l.setFixedSize(_UPDATE_ROW_ICON_SIZE, _UPDATE_ROW_ICON_SIZE)
        icon_l.setScaledContents(True)
        ic = lucide_icon("clapperboard", size=_UPDATE_ROW_ICON_SIZE, color_hex="#a1a1aa")
        pm = ic.pixmap(_UPDATE_ROW_ICON_SIZE, _UPDATE_ROW_ICON_SIZE)
        if not pm.isNull():
            icon_l.setPixmap(pm)
        row_l.addWidget(icon_l)

        name_l = QLabel("FFmpeg", row)
        name_l.setObjectName("UpdateProductListName")
        row_l.addWidget(name_l)

        ver_l = QLabel("—", row)
        ver_l.setObjectName("UpdateProductListVersion")
        ver_l.setProperty("mono", True)
        row_l.addWidget(ver_l)
        self._ffmpeg_version_label = ver_l

        row_l.addStretch(1)

        link_btn = QPushButton("Official builds", row)
        link_btn.setObjectName("UpdateProductListLink")
        link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        link_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(FFMPEG_GYAN_BUILDS_PAGE))
        )
        row_l.addWidget(link_btn)

        outer = QWidget(row)
        outer_l = QHBoxLayout(outer)
        outer_l.setContentsMargins(0, 0, 0, 0)
        outer_l.setSpacing(6)

        locate_tb = QToolButton(outer)
        locate_tb.setObjectName("UpdateDownloadCancelBtn")
        locate_tb.setIcon(lucide_icon("search", size=16, color_hex="#a1a1aa"))
        locate_tb.setFixedSize(28, _UPDATE_ACTION_HEIGHT)
        locate_tb.setToolTip("Locate ffmpeg.exe on this PC (saved in settings)")
        locate_tb.clicked.connect(self._on_ffmpeg_locate_clicked)
        outer_l.addWidget(locate_tb)

        stack = QStackedWidget(outer)

        page_get = QWidget(stack)
        pg_l = QHBoxLayout(page_get)
        pg_l.setContentsMargins(0, 0, 0, 0)
        get_btn = QPushButton("Get FFmpeg", page_get)
        get_btn.setObjectName("UpdateProductListBtnLatest")
        get_btn.setIcon(lucide_icon("download", size=16, color_hex="#a1a1aa"))
        get_btn.setIconSize(QSize(16, 16))
        get_btn.setToolTip(
            "Download ffmpeg-release-essentials.zip to temp, then click Install. "
            "Other packages: Official builds."
        )
        get_btn.clicked.connect(self._on_ffmpeg_download_clicked)
        pg_l.addWidget(get_btn)
        stack.addWidget(page_get)

        page_load = QWidget(stack)
        pl_l = QHBoxLayout(page_load)
        pl_l.setContentsMargins(0, 0, 0, 0)
        pl_l.setSpacing(_UPDATE_CANCEL_GAP)
        prog = QProgressBar(page_load)
        prog.setObjectName("UpdateDownloadProgress")
        prog.setMinimum(0)
        prog.setMaximum(0)
        cancel_tb = QToolButton(page_load)
        _configure_update_cancel_btn(cancel_tb)
        pl_l.addWidget(prog)
        pl_l.addWidget(cancel_tb)
        stack.addWidget(page_load)

        page_inst = QWidget(stack)
        pi_l = QHBoxLayout(page_inst)
        pi_l.setContentsMargins(0, 0, 0, 0)
        install_btn = QPushButton("Install", page_inst)
        install_btn.setObjectName("UpdateProductListBtnDownload")
        install_btn.setIcon(lucide_icon("package", size=16, color_hex="#fafafa"))
        install_btn.setIconSize(QSize(16, 16))
        install_btn.setToolTip("Extract to %LOCALAPPDATA%\\MonoStudio\\tools\\ffmpeg and register ffmpeg.exe")
        install_btn.clicked.connect(self._on_ffmpeg_install_clicked)
        pi_l.addWidget(install_btn)
        stack.addWidget(page_inst)

        _apply_update_tool_action_slot(get_btn, install_btn, prog, stack, outer)

        outer_l.addWidget(stack)
        row_l.addWidget(outer)

        self._ffmpeg_action_stack = stack
        self._ffmpeg_get_btn = get_btn
        self._ffmpeg_progress_bar = prog
        self._ffmpeg_cancel_btn = cancel_tb
        self._ffmpeg_install_btn = install_btn
        stack.setCurrentIndex(0)
        list_layout.addWidget(row)

    def _build_mpv_update_row(self, list_container: QWidget, list_layout: QVBoxLayout) -> None:
        """libmpv row: Get → download .7z → Install (extract to LocalAppData) + locate folder."""
        row = QWidget(list_container)
        row.setObjectName("UpdateProductListRow")
        row.setProperty("last", "true")
        row.setFixedHeight(44)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(12, 0, 12, 0)
        row_l.setSpacing(12)

        icon_l = QLabel(row)
        icon_l.setFixedSize(_UPDATE_ROW_ICON_SIZE, _UPDATE_ROW_ICON_SIZE)
        icon_l.setScaledContents(True)
        ic = lucide_icon("play", size=_UPDATE_ROW_ICON_SIZE, color_hex="#a1a1aa")
        pm = ic.pixmap(_UPDATE_ROW_ICON_SIZE, _UPDATE_ROW_ICON_SIZE)
        if not pm.isNull():
            icon_l.setPixmap(pm)
        row_l.addWidget(icon_l)

        name_l = QLabel("libmpv", row)
        name_l.setObjectName("UpdateProductListName")
        row_l.addWidget(name_l)

        ver_l = QLabel("—", row)
        ver_l.setObjectName("UpdateProductListVersion")
        ver_l.setProperty("mono", True)
        row_l.addWidget(ver_l)
        self._mpv_version_label = ver_l

        row_l.addStretch(1)

        link_btn = QPushButton("Official builds", row)
        link_btn.setObjectName("UpdateProductListLink")
        link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        link_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(MPV_BUILDS_PAGE)))
        row_l.addWidget(link_btn)

        outer = QWidget(row)
        outer_l = QHBoxLayout(outer)
        outer_l.setContentsMargins(0, 0, 0, 0)
        outer_l.setSpacing(6)

        locate_tb = QToolButton(outer)
        locate_tb.setObjectName("UpdateDownloadCancelBtn")
        locate_tb.setIcon(lucide_icon("search", size=16, color_hex="#a1a1aa"))
        locate_tb.setFixedSize(28, _UPDATE_ACTION_HEIGHT)
        locate_tb.setToolTip("Locate folder containing mpv-2.dll (override)")
        locate_tb.clicked.connect(self._on_mpv_locate_clicked)
        outer_l.addWidget(locate_tb)

        stack = QStackedWidget(outer)

        page_get = QWidget(stack)
        pg_l = QHBoxLayout(page_get)
        pg_l.setContentsMargins(0, 0, 0, 0)
        get_btn = QPushButton("Get libmpv", page_get)
        get_btn.setObjectName("UpdateProductListBtnLatest")
        get_btn.setIcon(lucide_icon("download", size=16, color_hex="#a1a1aa"))
        get_btn.setIconSize(QSize(16, 16))
        get_btn.setToolTip(f"Download {MPV_WIN64_7Z_NAME} to temp, then click Install (needs 7-Zip).")
        get_btn.clicked.connect(self._on_mpv_download_clicked)
        pg_l.addWidget(get_btn)
        stack.addWidget(page_get)

        page_load = QWidget(stack)
        pl_l = QHBoxLayout(page_load)
        pl_l.setContentsMargins(0, 0, 0, 0)
        pl_l.setSpacing(_UPDATE_CANCEL_GAP)
        prog = QProgressBar(page_load)
        prog.setObjectName("UpdateDownloadProgress")
        prog.setMinimum(0)
        prog.setMaximum(0)
        cancel_tb = QToolButton(page_load)
        _configure_update_cancel_btn(cancel_tb)
        pl_l.addWidget(prog)
        pl_l.addWidget(cancel_tb)
        stack.addWidget(page_load)

        page_inst = QWidget(stack)
        pi_l = QHBoxLayout(page_inst)
        pi_l.setContentsMargins(0, 0, 0, 0)
        install_btn = QPushButton("Install", page_inst)
        install_btn.setObjectName("UpdateProductListBtnDownload")
        install_btn.setIcon(lucide_icon("package", size=16, color_hex="#fafafa"))
        install_btn.setIconSize(QSize(16, 16))
        install_btn.setToolTip("Extract to %LOCALAPPDATA%\\MonoStudio\\tools\\mpv (mpv-2.dll)")
        install_btn.clicked.connect(self._on_mpv_install_clicked)
        pi_l.addWidget(install_btn)
        stack.addWidget(page_inst)

        _apply_update_tool_action_slot(get_btn, install_btn, prog, stack, outer)

        outer_l.addWidget(stack)
        row_l.addWidget(outer)

        self._mpv_action_stack = stack
        self._mpv_get_btn = get_btn
        self._mpv_progress_bar = prog
        self._mpv_cancel_btn = cancel_tb
        self._mpv_install_btn = install_btn
        stack.setCurrentIndex(0)
        list_layout.addWidget(row)

    def _refresh_mpv_update_row(self) -> None:
        lab = getattr(self, "_mpv_version_label", None)
        if lab is None:
            return
        try:
            status = format_mpv_detect_status(self._settings)
        except Exception:
            status = "not found"
        if status.startswith("not found"):
            lab.setText("Not found")
        elif status.startswith("bundled:"):
            lab.setText("Bundled")
        elif status.startswith("installed:"):
            lab.setText("Installed")
        elif status.startswith("manual:"):
            lab.setText("Manual")
        else:
            lab.setText("OK")
        helper = getattr(self, "_mpv_detect_helper", None)
        if helper is not None:
            helper.setText(
                "Embedded playback uses libmpv (mpv-2.dll). Install via Settings → Updates → libmpv, "
                "or bundle at build time. Double-click a video in Inbox or Project Guide to preview. "
                f"Detected: {status}"
            )

    def _refresh_ffmpeg_update_row(self) -> None:
        lab = getattr(self, "_ffmpeg_version_label", None)
        if lab is None:
            return
        s = self._settings
        exe = resolve_ffmpeg_executable(s)
        if exe:
            short = get_ffmpeg_version_short(exe)
            lab.setText(short or "OK")
        else:
            lab.setText("Not found")

    def _on_ffmpeg_locate_clicked(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Select ffmpeg.exe",
            "",
            "ffmpeg (ffmpeg.exe);;All files (*.*)",
        )
        if not path_str:
            return
        p = Path(path_str)
        if not validate_ffmpeg_executable(p):
            QMessageBox.warning(
                self,
                "FFmpeg",
                "The selected file does not run as ffmpeg (ffmpeg -version failed).",
            )
            return
        s = self._settings or QSettings("MonoStudio26", "MonoStudio26")
        write_ffmpeg_executable_path(s, str(p.resolve()))
        s.sync()
        self._refresh_ffmpeg_update_row()

    def _on_ffmpeg_download_clicked(self) -> None:
        stack = getattr(self, "_ffmpeg_action_stack", None)
        if stack is None:
            return
        dest = Path(tempfile.gettempdir()) / "MonoStudio26" / "ffmpeg-release-essentials.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        stack.setCurrentIndex(1)
        bar = getattr(self, "_ffmpeg_progress_bar", None)
        if bar is not None:
            bar.setMinimum(0)
            bar.setMaximum(0)
            bar.setValue(0)
        cancel = getattr(self, "_ffmpeg_cancel_btn", None)
        if cancel is not None:
            try:
                cancel.clicked.disconnect(self._on_ffmpeg_download_cancel_clicked)
            except (TypeError, RuntimeError):
                pass
            cancel.clicked.connect(self._on_ffmpeg_download_cancel_clicked)
        self._ffmpeg_download_worker = _FfmpegZipDownloadWorker(
            FFMPEG_GYAN_RELEASE_ESSENTIALS_ZIP,
            dest,
            self,
        )
        self._ffmpeg_download_worker.progress.connect(self._on_ffmpeg_zip_download_progress)
        self._ffmpeg_download_worker.download_finished.connect(self._on_ffmpeg_zip_download_finished)
        self._ffmpeg_download_worker.start()

    def _on_ffmpeg_download_cancel_clicked(self) -> None:
        if self._ffmpeg_download_worker is not None:
            self._ffmpeg_download_worker.cancel()

    def _on_ffmpeg_zip_download_progress(self, read: int, total: int) -> None:
        bar = getattr(self, "_ffmpeg_progress_bar", None)
        if bar is None:
            return
        if total > 0:
            bar.setMaximum(total)
            bar.setValue(read)
        else:
            bar.setMaximum(0)
            bar.setMinimum(0)

    def _on_ffmpeg_zip_download_finished(self, success: bool, path_str: str, error_message: str = "") -> None:
        self._ffmpeg_download_worker = None
        stack = getattr(self, "_ffmpeg_action_stack", None)
        cancel = getattr(self, "_ffmpeg_cancel_btn", None)
        if cancel is not None:
            try:
                cancel.clicked.disconnect(self._on_ffmpeg_download_cancel_clicked)
            except (TypeError, RuntimeError):
                pass
        bar = getattr(self, "_ffmpeg_progress_bar", None)
        if bar is not None:
            bar.setMinimum(0)
            bar.setMaximum(0)
        if stack is None:
            return
        p = Path(path_str)
        if success:
            self._ffmpeg_pending_zip = p
            stack.setCurrentIndex(2)
            return
        stack.setCurrentIndex(0)
        self._ffmpeg_pending_zip = None
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        err = (error_message or "").strip()
        if err != "Cancelled":
            QMessageBox.warning(
                self,
                "FFmpeg download",
                err[:400] if err else "Download failed.",
            )

    def _on_ffmpeg_install_clicked(self) -> None:
        z = self._ffmpeg_pending_zip
        stack = getattr(self, "_ffmpeg_action_stack", None)
        if z is None or not z.is_file():
            if stack is not None:
                stack.setCurrentIndex(0)
            QMessageBox.warning(self, "FFmpeg", "No downloaded package. Click Get FFmpeg first.")
            return
        btn = getattr(self, "_ffmpeg_install_btn", None)
        if btn is not None:
            btn.setEnabled(False)
        self._ffmpeg_install_worker = _FfmpegInstallWorker(z, self)
        self._ffmpeg_install_worker.ok.connect(self._on_ffmpeg_install_ok)
        self._ffmpeg_install_worker.err.connect(self._on_ffmpeg_install_err)
        self._ffmpeg_install_worker.finished.connect(self._on_ffmpeg_install_thread_finished)
        self._ffmpeg_install_worker.start()

    def _on_ffmpeg_install_ok(self, exe: str) -> None:
        s = self._settings or QSettings("MonoStudio26", "MonoStudio26")
        write_ffmpeg_executable_path(s, exe)
        s.sync()
        try:
            if self._ffmpeg_pending_zip is not None:
                self._ffmpeg_pending_zip.unlink(missing_ok=True)
        except OSError:
            pass
        self._ffmpeg_pending_zip = None
        stack = getattr(self, "_ffmpeg_action_stack", None)
        if stack is not None:
            stack.setCurrentIndex(0)
        self._refresh_ffmpeg_update_row()
        QMessageBox.information(self, "FFmpeg", f"Installed to Local AppData tools folder.\n{exe}")

    def _on_ffmpeg_install_err(self, msg: str) -> None:
        QMessageBox.warning(self, "FFmpeg install", msg)

    def _on_ffmpeg_install_thread_finished(self) -> None:
        self._ffmpeg_install_worker = None
        btn = getattr(self, "_ffmpeg_install_btn", None)
        if btn is not None:
            btn.setEnabled(True)

    def _on_mpv_locate_clicked(self) -> None:
        from monostudio.core.mpv_resolve import find_mpv_dll_under

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select folder containing mpv-2.dll",
            self._mpv_dir_field.text().strip() if getattr(self, "_mpv_dir_field", None) else "",
        )
        if not folder:
            return
        p = Path(folder)
        if find_mpv_dll_under(p) is None:
            QMessageBox.warning(self, "libmpv", "mpv-2.dll was not found in the selected folder.")
            return
        s = self._settings or QSettings("MonoStudio26", "MonoStudio26")
        write_mpv_directory(s, folder)
        s.sync()
        if getattr(self, "_mpv_dir_field", None) is not None:
            self._mpv_dir_field.setText(folder)
        self._refresh_mpv_update_row()

    def _on_mpv_download_clicked(self) -> None:
        stack = getattr(self, "_mpv_action_stack", None)
        if stack is None:
            return
        dest = Path(tempfile.gettempdir()) / "MonoStudio26" / MPV_WIN64_7Z_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        stack.setCurrentIndex(1)
        bar = getattr(self, "_mpv_progress_bar", None)
        if bar is not None:
            bar.setMinimum(0)
            bar.setMaximum(0)
            bar.setValue(0)
        cancel = getattr(self, "_mpv_cancel_btn", None)
        if cancel is not None:
            try:
                cancel.clicked.disconnect(self._on_mpv_download_cancel_clicked)
            except (TypeError, RuntimeError):
                pass
            cancel.clicked.connect(self._on_mpv_download_cancel_clicked)
        self._mpv_download_worker = _Mpv7zDownloadWorker(dest, self)
        self._mpv_download_worker.progress.connect(self._on_mpv_7z_download_progress)
        self._mpv_download_worker.download_finished.connect(self._on_mpv_7z_download_finished)
        self._mpv_download_worker.start()

    def _on_mpv_download_cancel_clicked(self) -> None:
        if self._mpv_download_worker is not None:
            self._mpv_download_worker.cancel()

    def _on_mpv_7z_download_progress(self, read: int, total: int) -> None:
        bar = getattr(self, "_mpv_progress_bar", None)
        if bar is None:
            return
        if total > 0:
            bar.setMaximum(total)
            bar.setValue(read)
        else:
            bar.setMaximum(0)
            bar.setMinimum(0)

    def _on_mpv_7z_download_finished(self, success: bool, path_str: str, error_message: str = "") -> None:
        self._mpv_download_worker = None
        stack = getattr(self, "_mpv_action_stack", None)
        cancel = getattr(self, "_mpv_cancel_btn", None)
        if cancel is not None:
            try:
                cancel.clicked.disconnect(self._on_mpv_download_cancel_clicked)
            except (TypeError, RuntimeError):
                pass
        bar = getattr(self, "_mpv_progress_bar", None)
        if bar is not None:
            bar.setMinimum(0)
            bar.setMaximum(0)
        if stack is None:
            return
        p = Path(path_str)
        if success:
            self._mpv_pending_7z = p
            stack.setCurrentIndex(2)
            return
        stack.setCurrentIndex(0)
        self._mpv_pending_7z = None
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        err = (error_message or "").strip()
        if err != "Cancelled":
            QMessageBox.warning(self, "libmpv download", err[:400] if err else "Download failed.")

    def _on_mpv_install_clicked(self) -> None:
        z = self._mpv_pending_7z
        stack = getattr(self, "_mpv_action_stack", None)
        if z is None or not z.is_file():
            if stack is not None:
                stack.setCurrentIndex(0)
            QMessageBox.warning(self, "libmpv", "No downloaded package. Click Get libmpv first.")
            return
        btn = getattr(self, "_mpv_install_btn", None)
        if btn is not None:
            btn.setEnabled(False)
        self._mpv_install_worker = _MpvInstallWorker(z, self)
        self._mpv_install_worker.ok.connect(self._on_mpv_install_ok)
        self._mpv_install_worker.err.connect(self._on_mpv_install_err)
        self._mpv_install_worker.finished.connect(self._on_mpv_install_thread_finished)
        self._mpv_install_worker.start()

    def _on_mpv_install_ok(self, dll_path: str) -> None:
        s = self._settings or QSettings("MonoStudio26", "MonoStudio26")
        write_mpv_directory(s, "")
        s.sync()
        if getattr(self, "_mpv_dir_field", None) is not None:
            self._mpv_dir_field.setText("")
        try:
            if self._mpv_pending_7z is not None:
                self._mpv_pending_7z.unlink(missing_ok=True)
        except OSError:
            pass
        self._mpv_pending_7z = None
        stack = getattr(self, "_mpv_action_stack", None)
        if stack is not None:
            stack.setCurrentIndex(0)
        self._refresh_mpv_update_row()
        QMessageBox.information(
            self,
            "libmpv",
            f"Installed to Local AppData tools folder.\n{dll_path}\n\nSet Playback backend to Auto or mpv embed.",
        )

    def _on_mpv_install_err(self, msg: str) -> None:
        QMessageBox.warning(self, "libmpv install", msg)

    def _on_mpv_install_thread_finished(self) -> None:
        self._mpv_install_worker = None
        btn = getattr(self, "_mpv_install_btn", None)
        if btn is not None:
            btn.setEnabled(True)

    def _format_last_checked(self, dt: datetime) -> str:
        """Format last check time like 'Today, 8:25 AM' or 'Yesterday, 3:00 PM'."""
        now = datetime.now()
        if dt.date() == now.date():
            return f"Last checked: Today, {dt.strftime('%I:%M %p').lstrip('0')}"
        if (now.date() - dt.date()).days == 1:
            return f"Last checked: Yesterday, {dt.strftime('%I:%M %p').lstrip('0')}"
        return f"Last checked: {dt.strftime('%b %d, %I:%M %p').replace(' 0', ' ')}"

    def _refresh_last_checked_label(self) -> None:
        if self._update_last_checked_time is None:
            self._update_last_checked_label.setText("")
            self._update_last_checked_label.setVisible(False)
        else:
            self._update_last_checked_label.setText(self._format_last_checked(self._update_last_checked_time))
            self._update_last_checked_label.setVisible(True)

    def _set_update_status_display(self, message: str, icon_name: str, icon_color_hex: str) -> None:
        """Set status message, icon (lucide name + color), and refresh last-checked line."""
        self._update_status_label.setText(message)
        icon = lucide_icon(icon_name, size=_UPDATE_STATUS_ICON_SIZE, color_hex=icon_color_hex)
        self._update_status_icon.setPixmap(icon.pixmap(_UPDATE_STATUS_ICON_SIZE, _UPDATE_STATUS_ICON_SIZE))
        self._refresh_last_checked_label()

    def _compute_update_summary(
        self,
        result: CheckResult | None,
        extra_repos: dict[str, ExtraRepoRelease],
    ) -> tuple[str, str, str]:
        """Return (message, icon_name, icon_color_hex) for overall status (all apps, Windows Update style)."""
        products_with_update: list[str] = []
        if result and result.update_available:
            products_with_update.append("MonoStudio 26")
        for name, info in extra_repos.items():
            if not info or not info.version:
                continue
            installed = get_extra_tool_installed_version(name) or ""
            if installed and is_newer_than(installed, info.version):
                products_with_update.append(name)
        if products_with_update:
            if len(products_with_update) == 1:
                msg = f"Update available for {products_with_update[0]}."
            elif len(products_with_update) == 2:
                msg = f"Updates available for {products_with_update[0]} and {products_with_update[1]}."
            else:
                msg = f"Updates available for {len(products_with_update)} products."
            return (msg, "refresh-cw", MONOS_COLORS.get("blue_400", "#60a5fa"))
        return ("You're up to date", "square-check", MONOS_COLORS.get("emerald_500", "#10b981"))

    def _on_check_for_updates(self) -> None:
        self._update_check_btn.setEnabled(False)
        self._set_update_status_display("Checking…", "loader-2", MONOS_COLORS.get("blue_400", "#60a5fa"))
        self._update_changelog.clear()
        self._pending_update_info = None
        self._apply_monostudio_row(None)
        self._apply_extra_repos_ui({})
        self._update_check_worker = _UpdateCheckWorker(
            None,  # use default: GitHub Releases API
            get_app_version(),
            self,
            skip_cache=True,  # user clicked "Check for updates" → always fetch fresh
        )
        self._update_check_worker.check_finished.connect(self._on_update_check_finished)
        self._update_check_worker.finished.connect(self._on_update_check_thread_finished)
        self._update_check_worker.start()

    def _on_update_check_finished(
        self,
        result: CheckResult | None,
        error_message: str,
        extra_repos: dict[str, ExtraRepoRelease] | None = None,
    ) -> None:
        extra = extra_repos or {}
        if error_message:
            self._set_update_status_display(
                f"Check failed: {error_message}",
                "refresh-cw",
                MONOS_COLORS.get("text_meta", "#71717a"),
            )
            self._update_changelog.clear()
            self._apply_monostudio_row(None)
            self._apply_extra_repos_ui(extra)
            return
        if result is None:
            self._apply_extra_repos_ui(extra)
            msg, icon_name, icon_color = self._compute_update_summary(None, extra)
            self._set_update_status_display(msg, icon_name, icon_color)
            return
        self._update_last_checked_time = datetime.now()
        if self._settings:
            self._settings.setValue("updates/last_check_time", self._update_last_checked_time.isoformat())
        if result.latest_notes:
            self._update_changelog.setMarkdown(result.latest_notes)
        else:
            self._update_changelog.setPlainText("No release notes for this version.")
        self._apply_changelog_line_height()
        self._update_latest_html_url = result.latest_html_url
        if result.update_available and result.update_info is not None:
            self._pending_update_info = result.update_info
        else:
            self._pending_update_info = None
        self._apply_monostudio_row(result)
        self._apply_extra_repos_ui(extra)
        msg, icon_name, icon_color = self._compute_update_summary(result, extra)
        self._set_update_status_display(msg, icon_name, icon_color)

    def _apply_changelog_line_height(self) -> None:
        """Áp dụng line-height 165% cho mọi block trong release notes (QTextDocument không hỗ trợ line-height qua CSS)."""
        doc = self._update_changelog.document()
        block = doc.firstBlock()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            # ProportionalHeight = 1 (QTextBlockFormat.LineHeightTypes)
            fmt.setLineHeight(165.0, 1)
            cursor.setBlockFormat(fmt)
            block = block.next()

    def _on_update_check_thread_finished(self) -> None:
        self._update_check_btn.setEnabled(True)
        self._update_check_worker = None

    def _on_download_and_install(self) -> None:
        info = self._pending_update_info
        if info is None:
            return
        import tempfile
        dest = Path(tempfile.gettempdir()) / "MonoStudio26_Setup.exe"
        primary = info.url
        fallback = (info.asset_api_url or "").strip() or None
        self._update_download_product = "monostudio"
        if self._update_monostudio_action_btn:
            self._update_monostudio_action_btn.hide()
        if getattr(self, "_update_monostudio_loading_widget", None):
            self._update_monostudio_loading_widget.show()
        if getattr(self, "_update_monostudio_progress_bar", None):
            self._update_monostudio_progress_bar.setMinimum(0)
            self._update_monostudio_progress_bar.setMaximum(0)
        self._set_update_status_display("Downloading…", "loader-2", MONOS_COLORS.get("blue_400", "#60a5fa"))
        self._update_download_worker = _DownloadWorker(primary, dest, fallback_url=fallback, parent=self)
        self._update_download_worker.progress.connect(self._on_download_progress)
        self._update_download_worker.download_finished.connect(self._on_download_finished)
        if getattr(self, "_update_monostudio_cancel_btn", None):
            self._update_monostudio_cancel_btn.clicked.connect(self._on_cancel_download)
        self._update_download_worker.start()

    def _on_view_release_on_github(self) -> None:
        url = None
        if self._pending_update_info and self._pending_update_info.html_url:
            url = self._pending_update_info.html_url
        elif getattr(self, "_update_latest_html_url", None):
            url = self._update_latest_html_url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _apply_monostudio_row(self, result: CheckResult | None) -> None:
        """Update MonoStudio row: version, View release notes link, Download vX.X.X / Latest button."""
        ver_l = getattr(self, "_update_monostudio_version_label", None)
        link_btn = getattr(self, "_update_monostudio_link_btn", None)
        action_btn = getattr(self, "_update_monostudio_action_btn", None)
        if ver_l is None or link_btn is None or action_btn is None:
            return
        ver_l.setText(get_app_version())
        if result and result.latest_html_url:
            link_btn.setVisible(True)
        else:
            link_btn.setVisible(False)
        if result and result.update_available and result.update_info is not None:
            action_btn.setText(f"Download {result.latest_version}")
            action_btn.setObjectName("UpdateProductListBtnDownload")
            action_btn.setEnabled(True)
            action_btn.setStyleSheet("")  # force re-apply stylesheet
            action_btn.style().unpolish(action_btn)
            action_btn.style().polish(action_btn)
        else:
            action_btn.setText("Latest")
            action_btn.setObjectName("UpdateProductListBtnLatest")
            action_btn.setEnabled(False)
            action_btn.setStyleSheet("")
            action_btn.style().unpolish(action_btn)
            action_btn.style().polish(action_btn)
        _apply_update_action_width(
            action_btn,
            loading_widget=getattr(self, "_update_monostudio_loading_widget", None),
            progress_bar=getattr(self, "_update_monostudio_progress_bar", None),
        )

    def _apply_extra_repos_ui(self, extra_repos: dict[str, ExtraRepoRelease]) -> None:
        """Update extra-repo rows: version, release notes link; Download vX.X.X (when update available) or Latest, like MonoStudio."""
        fallbacks = getattr(self, "_update_extra_fallback_url", {})
        for name, (ver_l, link_btn, action_btn) in getattr(self, "_update_extra_cards", {}).items():
            info = extra_repos.get(name)
            if info:
                installed = get_extra_tool_installed_version(name) or ""
                ver_l.setText(installed or "—")
                self._update_extra_html_url[name] = info.html_url or fallbacks.get(name, "")
                download_url = getattr(info, "download_url", "") or ""
                self._update_extra_download_url[name] = download_url
                link_btn.setVisible(bool(info.html_url))
                action_btn.setVisible(True)
                # Like MonoStudio: compare installed vs latest — only show Download when update available
                update_available = bool(installed and info.version and is_newer_than(installed, info.version))
                if update_available and download_url:
                    action_btn.setText(f"Download {info.version}")
                    action_btn.setObjectName("UpdateProductListBtnDownload")
                    action_btn.setEnabled(True)
                elif download_url:
                    action_btn.setText("Latest")
                    action_btn.setObjectName("UpdateProductListBtnLatest")
                    action_btn.setEnabled(False)
                else:
                    action_btn.setText("View on GitHub")
                    action_btn.setObjectName("SettingsCategoryActionButton")
                    action_btn.setEnabled(True)
                action_btn.style().unpolish(action_btn)
                action_btn.style().polish(action_btn)
            else:
                # No API data yet (user hasn't clicked Check) — still show installed version
                ver_l.setText(get_extra_tool_installed_version(name) or "—")
                self._update_extra_html_url[name] = fallbacks.get(name, "")
                self._update_extra_download_url[name] = ""
                link_btn.setVisible(False)
                action_btn.setVisible(bool(fallbacks.get(name)))
                action_btn.setText("View on GitHub")
                action_btn.setObjectName("SettingsCategoryActionButton")
                action_btn.setEnabled(True)
            loading_tuple = getattr(self, "_update_extra_loading", {}).get(name)
            if loading_tuple:
                lw, pb, _ = loading_tuple
                _apply_update_action_width(action_btn, loading_widget=lw, progress_bar=pb)
            else:
                _apply_update_action_width(action_btn)

    def _on_extra_repo_release_link_clicked(self, name: str) -> None:
        url = self._update_extra_html_url.get(name)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _on_extra_repo_action_clicked(self, name: str) -> None:
        """Download installer if URL available, else open GitHub releases page."""
        download_url = self._update_extra_download_url.get(name)
        if download_url:
            self._start_extra_repo_download(name, download_url)
        else:
            url = self._update_extra_html_url.get(name)
            if url:
                QDesktopServices.openUrl(QUrl(url))

    def _start_extra_repo_download(self, name: str, url: str) -> None:
        """Start download of extra-repo installer; show loading in that product's row only."""
        import re
        import tempfile
        safe = re.sub(r"[^\w\-]", "", name)[:32] or "Tool"
        dest = Path(tempfile.gettempdir()) / f"{safe}_Setup.exe"
        self._update_download_product = name
        cards = getattr(self, "_update_extra_cards", {})
        loading_map = getattr(self, "_update_extra_loading", {})
        if name in cards:
            _, _, action_btn = cards[name]
            action_btn.hide()
        if name in loading_map:
            loading_widget, progress_bar, cancel_btn = loading_map[name]
            progress_bar.setMinimum(0)
            progress_bar.setMaximum(0)
            loading_widget.show()
            cancel_btn.clicked.connect(self._on_cancel_download)
        self._set_update_status_display(f"Downloading {name}…", "loader-2", MONOS_COLORS.get("blue_400", "#60a5fa"))
        self._update_download_worker = _DownloadWorker(url, dest, parent=self)
        self._update_download_worker.progress.connect(self._on_download_progress)
        self._update_download_worker.download_finished.connect(self._on_download_finished)
        self._update_download_worker.start()

    def _on_extra_repo_github_clicked(self, name: str) -> None:
        url = self._update_extra_html_url.get(name)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _on_download_progress(self, read: int, total: int) -> None:
        product = getattr(self, "_update_download_product", "") or "monostudio"
        if product == "monostudio":
            bar = getattr(self, "_update_monostudio_progress_bar", None)
        else:
            loading_map = getattr(self, "_update_extra_loading", {})
            bar = loading_map.get(product, (None, None, None))[1] if product in loading_map else None
        if not bar:
            return
        if total > 0:
            bar.setMinimum(0)
            bar.setMaximum(total)
            bar.setValue(read)
        else:
            bar.setMinimum(0)
            bar.setMaximum(0)

    def _on_cancel_download(self) -> None:
        if self._update_download_worker:
            self._update_download_worker.cancel()

    def _on_download_finished(self, success: bool, path: str, error_message: str = "") -> None:
        product = getattr(self, "_update_download_product", "") or "monostudio"
        if product == "monostudio":
            if getattr(self, "_update_monostudio_loading_widget", None):
                self._update_monostudio_loading_widget.hide()
            if getattr(self, "_update_monostudio_action_btn", None):
                self._update_monostudio_action_btn.show()
                self._update_monostudio_action_btn.setEnabled(True)
            if getattr(self, "_update_monostudio_cancel_btn", None):
                try:
                    self._update_monostudio_cancel_btn.clicked.disconnect(self._on_cancel_download)
                except Exception:
                    pass
        else:
            cards = getattr(self, "_update_extra_cards", {})
            loading_map = getattr(self, "_update_extra_loading", {})
            if product in cards:
                _, _, action_btn = cards[product]
                action_btn.show()
            if product in loading_map:
                loading_widget, _, cancel_btn = loading_map[product]
                loading_widget.hide()
                try:
                    cancel_btn.clicked.disconnect(self._on_cancel_download)
                except Exception:
                    pass
        if self._update_download_worker:
            try:
                self._update_download_worker.progress.disconnect(self._on_download_progress)
            except Exception:
                pass
            self._update_download_worker = None
        zinc = MONOS_COLORS.get("text_meta", "#71717a")
        if success:
            self._set_update_status_display("Launching installer…", "loader-2", MONOS_COLORS.get("blue_400", "#60a5fa"))
            try:
                if product == "monostudio":
                    run_installer_and_exit(Path(path))
                else:
                    launch_installer(Path(path))
                    self._set_update_status_display(
                        "Installer launched. You can continue using MonoStudio.",
                        "square-check",
                        MONOS_COLORS.get("emerald_500", "#10b981"),
                    )
            except (OSError, RuntimeError) as e:
                msg = str(e).replace("\n", " ")[:200]
                self._set_update_status_display(
                    f"Cannot run installer: {msg} Download from the release page below instead.",
                    "refresh-cw",
                    zinc,
                )
        else:
            if (error_message or "").strip() == "Cancelled":
                self._set_update_status_display("Download cancelled.", "refresh-cw", zinc)
            else:
                msg = (error_message.strip() or "Download failed.").replace("\n", " ")[:200]
                self._set_update_status_display(
                    f"Download failed: {msg} Or get the installer from the release page below.",
                    "refresh-cw",
                    zinc,
                )
        self._update_download_product = ""

    def _build_pipeline_page(self) -> QWidget:
        """Tier 2: Pipeline → Pipeline structure | Scan rules | Statuses."""
        outer = QWidget(self)
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)
        self._pipeline_access_banner = QLabel(outer)
        self._pipeline_access_banner.setWordWrap(True)
        self._pipeline_access_banner.setObjectName("DialogHelper")
        self._pipeline_access_banner.setVisible(False)
        ol.addWidget(self._pipeline_access_banner, 0)
        inner = self._build_tier2_page_buttons(
            [
                ("Pipeline structure", self._build_pipeline_structure_page()),
                ("Create defaults", self._build_pipeline_create_defaults_tab()),
                ("Scan rules", self._build_pipeline_scan_rules_tab()),
                ("Statuses", self._placeholder("Pipeline → Statuses (placeholder)")),
            ],
            store_stack="pipeline",
            store_buttons="pipeline",
        )
        ol.addWidget(inner, 1)
        return outer

    def _build_dccs_page(self) -> QWidget:
        """DCCs: single page (Blender / integrations)."""
        return self._build_project_integrations_tab()

    def _build_project_page(self) -> QWidget:
        """Tier 2: Project → Overview | Integrations | Advanced (nút page ngang)."""
        return self._build_tier2_page_buttons([
            ("Overview", self._placeholder("Project → Overview (placeholder)")),
            ("Integrations", self._build_workspace_discord_integrations_tab()),
            ("Advanced", self._build_project_advanced_tab()),
        ])

    def _build_pipeline_scan_rules_tab(self) -> QWidget:
        """Pipeline → Scan rules: rules for file/folder scanning (e.g. ignore extensions per context)."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        default_ext = ".tmp,.bak,.mtl,.mb.bak,.ma.bak,.blend1,Thumbs.db,.DS_Store"
        grp = QGroupBox("Publish", root)
        grp_layout = QVBoxLayout(grp)
        form = QFormLayout()
        self._publish_ignore_ext_field = QLineEdit(grp)
        self._publish_ignore_ext_field.setPlaceholderText(default_ext)
        self._publish_ignore_ext_field.setProperty("mono", True)
        try:
            if self._settings is not None:
                v = self._settings.value("pipeline/publish_ignore_extensions", default_ext, str)
                self._publish_ignore_ext_field.setText((v or default_ext).strip())
            else:
                self._publish_ignore_ext_field.setText(default_ext)
        except Exception:
            self._publish_ignore_ext_field.setText(default_ext)
        form.addRow("Ignore extensions (comma-separated):", self._publish_ignore_ext_field)
        hint = QLabel(
            "File extensions to exclude when listing files inside publish version folders (e.g. v001). "
            "Used for primary file, drag-and-drop, and copy path. Use leading dot (e.g. .tmp) or not; stored normalized.",
            grp,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        grp_layout.addLayout(form)
        grp_layout.addWidget(hint)
        layout.addWidget(grp)

        # Thumbnail / render-sequence scan rules.
        default_thumb_tokens = "cryptomatte,z"
        grp_t = QGroupBox("Thumbnails (Render/Preview sequences)", root)
        grp_t_layout = QVBoxLayout(grp_t)
        form_t = QFormLayout()

        self._thumb_seq_ignore_ext_field = QLineEdit(grp_t)
        self._thumb_seq_ignore_ext_field.setPlaceholderText("")
        self._thumb_seq_ignore_ext_field.setProperty("mono", True)
        try:
            if self._settings is not None:
                v = self._settings.value("pipeline/thumbnail_sequence_ignore_extensions", "", str)
                self._thumb_seq_ignore_ext_field.setText((v or "").strip())
            else:
                self._thumb_seq_ignore_ext_field.setText("")
        except Exception:
            self._thumb_seq_ignore_ext_field.setText("")
        form_t.addRow("Ignore extensions (comma-separated):", self._thumb_seq_ignore_ext_field)

        self._thumb_seq_ignore_tokens_field = QLineEdit(grp_t)
        self._thumb_seq_ignore_tokens_field.setPlaceholderText(default_thumb_tokens)
        self._thumb_seq_ignore_tokens_field.setProperty("mono", True)
        try:
            if self._settings is not None:
                v = self._settings.value("pipeline/thumbnail_sequence_ignore_tokens", default_thumb_tokens, str)
                self._thumb_seq_ignore_tokens_field.setText((v or default_thumb_tokens).strip())
            else:
                self._thumb_seq_ignore_tokens_field.setText(default_thumb_tokens)
        except Exception:
            self._thumb_seq_ignore_tokens_field.setText(default_thumb_tokens)
        form_t.addRow("Ignore filename tokens (comma-separated):", self._thumb_seq_ignore_tokens_field)

        hint_t = QLabel(
            "Applies when picking a representative frame from work/<render|preview|playblast|flipbook>/<work_name>/. "
            "Extensions are compared as lowercase with leading dot. Tokens are substring-matched case-insensitively. "
            "Default token: cryptomatte (from legacy behavior).",
            grp_t,
        )
        hint_t.setWordWrap(True)
        hint_t.setObjectName("DialogHelper")
        grp_t_layout.addLayout(form_t)
        grp_t_layout.addWidget(hint_t)
        layout.addWidget(grp_t)
        layout.addStretch(1)
        return root

    def _build_app_workspace_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        grp = QGroupBox("Workspace & Project")
        form = QFormLayout(grp)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        self._workspace_path = QLineEdit(str(self._workspace_root) if self._workspace_root else "", self)
        self._workspace_path.setReadOnly(True)
        self._workspace_path.setProperty("mono", True)

        btn_workspace = QPushButton("Open Workspace…", self)
        btn_workspace.clicked.connect(self._pick_workspace_root)

        row_ws = QWidget(self)
        row_ws_l = QHBoxLayout(row_ws)
        row_ws_l.setContentsMargins(0, 0, 0, 0)
        row_ws_l.setSpacing(8)
        row_ws_l.addWidget(self._workspace_path, 1)
        row_ws_l.addWidget(btn_workspace, 0)
        form.addRow("Workspace Root", row_ws)

        self._project_path = QLineEdit(str(self._project_root) if self._project_root else "", self)
        self._project_path.setReadOnly(True)
        self._project_path.setProperty("mono", True)

        btn_project = QPushButton("Open Project Root…", self)
        btn_project.clicked.connect(self._pick_project_root)

        row_prj = QWidget(self)
        row_prj_l = QHBoxLayout(row_prj)
        row_prj_l.setContentsMargins(0, 0, 0, 0)
        row_prj_l.setSpacing(8)
        row_prj_l.addWidget(self._project_path, 1)
        row_prj_l.addWidget(btn_project, 0)
        form.addRow("Project Root", row_prj)

        self._use_dcc_folders_cb = QCheckBox("Use DCC folders (department/<dcc>/work)", grp)
        self._use_dcc_folders_cb.setChecked(
            read_use_dcc_folders(self._project_root) if self._project_root else True
        )
        self._use_dcc_folders_cb.setToolTip(
            "Store work files in department/<dcc>/work (e.g. modeling/blender/work). Default: on."
        )
        self._use_dcc_folders_cb.setEnabled(self._project_root is not None)
        form.addRow("", self._use_dcc_folders_cb)

        layout.addWidget(grp)
        layout.addStretch(1)
        return root

    def _pick_workspace_root(self) -> None:
        start = str(self._workspace_root) if self._workspace_root else ""
        folder = QFileDialog.getExistingDirectory(self, "Open Workspace", start)
        if not folder:
            return
        self._workspace_root = Path(folder)
        self._workspace_path.setText(folder)
        self.workspace_root_selected.emit(folder)

    def _pick_project_root(self) -> None:
        start = str(self._project_root) if self._project_root else ""
        folder = QFileDialog.getExistingDirectory(self, "Open Project Root", start)
        if not folder:
            return
        self._project_root = Path(folder)
        self._project_path.setText(folder)
        if self._use_dcc_folders_cb is not None:
            self._use_dcc_folders_cb.setEnabled(True)
            self._use_dcc_folders_cb.setChecked(read_use_dcc_folders(self._project_root))
        self.project_root_selected.emit(folder)
        self._reload_pipeline_editor_for_project()

    def _build_pipeline_structure_page(self) -> QWidget:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self._pipeline_editor = PipelineStructureEditorWidget(self)
        self._pipeline_editor.set_project_root(self._project_root)
        self._pipeline_editor.config_changed.connect(self._on_pipeline_editor_config_changed)
        outer.addWidget(self._pipeline_editor, 1)
        hint = QLabel(
            "Tree colors: root / structure / asset type / shot type / departments / subdepartments. "
            "Under each type, open Workflow to assign leaf departments. "
            "Missing factory subdepartments (e.g. FX → groom, destruction) are merged in memory from mono2026; "
            "use Save to project to persist. Reset factory reloads app preset + mono2026. "
            "Use Save in this section for project pipeline JSON; bottom Save also saves all Settings tabs.",
            root,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        outer.addWidget(hint)
        return root

    def _on_pipeline_editor_config_changed(self) -> None:
        if self._pipeline_editor is not None:
            self._config = self._pipeline_editor.build_pipeline_types_and_presets()

    def _reload_pipeline_editor_for_project(self) -> None:
        self._config = load_pipeline_types_and_presets_for_project(self._project_root)
        if self._pipeline_editor is not None:
            self._pipeline_editor.set_project_root(self._project_root)
        self._reload_pipeline_create_defaults_form()

    def _build_pipeline_create_defaults_tab(self) -> QWidget:
        """Default DCC for Create New, per department (project.json)."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        hint = QLabel(
            "Choose which DCC is pre-selected in Create New for each department. "
            "Saved in the open project (.monostudio/project.json).",
            root,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        layout.addWidget(hint, 0)
        scroll = QScrollArea(root)
        scroll.setObjectName("OpenResolverScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget(scroll)
        form = QFormLayout(inner)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        self._create_default_form_layout = form
        self._create_default_combos = {}
        self._reload_pipeline_create_defaults_form()
        return root

    def _reload_pipeline_create_defaults_form(self) -> None:
        form = self._create_default_form_layout
        if form is None:
            return
        while form.rowCount() > 0:
            form.removeRow(0)
        self._create_default_combos.clear()
        if self._project_root is None:
            lab = QLabel("Open a project to set create defaults per department.")
            lab.setObjectName("DialogHelper")
            lab.setWordWrap(True)
            form.addRow(lab)
            return
        try:
            dre = DepartmentRegistry.for_project(self._project_root)
            reg = get_default_dcc_registry()
        except Exception:
            lab = QLabel("Could not load department or DCC registry for this project.")
            lab.setObjectName("DialogWarning")
            lab.setWordWrap(True)
            form.addRow(lab)
            return
        saved = read_create_default_dcc_map(self._project_root)
        for dep_id in dre.get_departments():
            dep_label = dre.get_department_label(dep_id) or dep_id
            cb = QComboBox()
            cb.addItem("(none)", "")
            for dcc_id in dre.supported_dcc_ids(reg, dep_id):
                try:
                    info = reg.get_dcc_info(dcc_id)
                    dn = info.get("label") if isinstance(info, dict) else None
                    dlab = dn.strip() if isinstance(dn, str) and dn.strip() else dcc_id
                except Exception:
                    dlab = dcc_id
                cb.addItem(dlab, dcc_id)
            cur = saved.get(dep_id) or ""
            if not cur:
                for k, v in saved.items():
                    if k.strip().casefold() == dep_id.strip().casefold():
                        cur = v
                        break
            idx = 0
            if cur:
                for i in range(cb.count()):
                    data = cb.itemData(i)
                    if isinstance(data, str) and data.strip().casefold() == cur.strip().casefold():
                        idx = i
                        break
            cb.setCurrentIndex(idx)
            self._create_default_combos[dep_id] = cb
            row_label = QLabel(dep_label)
            row_label.setObjectName("DialogLabelPrimary")
            form.addRow(row_label, cb)

    def _build_project_integrations_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        grp = QGroupBox("DCC")
        form = QFormLayout(grp)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        field = QLineEdit(self)
        field.setPlaceholderText("Auto-detect, or browse to blender.exe")
        field.setProperty("mono", True)
        self._blender_exe_field = field

        # Load current setting (if available).
        if self._settings is not None:
            cur = (self._settings.value("integrations/blender_exe", "", str) or "").strip()
            field.setText(cur)
        else:
            field.setEnabled(False)

        btn_browse = QPushButton("Browse…", self)
        btn_auto = QPushButton("Auto Detect", self)

        if self._settings is None:
            btn_browse.setEnabled(False)
            btn_auto.setEnabled(False)
            btn_browse.setToolTip("Settings store is not available.")
            btn_auto.setToolTip("Settings store is not available.")

        def on_browse() -> None:
            start = field.text().strip()
            start_dir = str(Path(start).parent) if start else ""
            path, _flt = QFileDialog.getOpenFileName(
                self,
                "Select Blender Executable",
                start_dir,
                "Blender (blender.exe);;Executables (*.exe);;All files (*.*)",
            )
            if not path:
                return
            field.setText(path)

        def on_auto_detect() -> None:
            found = resolve_blender_executable(field.text().strip() or "blender")
            if not found:
                QMessageBox.information(self, "Auto Detect", "Blender was not found. Browse to 'blender.exe' instead.")
                return
            field.setText(found)

        btn_browse.clicked.connect(on_browse)
        btn_auto.clicked.connect(on_auto_detect)

        row = QWidget(self)
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(8)
        row_l.addWidget(field, 1)
        row_l.addWidget(btn_auto, 0)
        row_l.addWidget(btn_browse, 0)

        form.addRow("Blender Executable", row)

        # Maya
        field_maya = QLineEdit(self)
        field_maya.setPlaceholderText("Auto-detect, or browse to maya.exe")
        field_maya.setProperty("mono", True)
        self._maya_exe_field = field_maya
        if self._settings is not None:
            cur_maya = (self._settings.value("integrations/maya_exe", "", str) or "").strip()
            field_maya.setText(cur_maya)
        else:
            field_maya.setEnabled(False)
        btn_browse_maya = QPushButton("Browse…", self)
        btn_auto_maya = QPushButton("Auto Detect", self)
        if self._settings is None:
            btn_browse_maya.setEnabled(False)
            btn_auto_maya.setEnabled(False)

        def on_browse_maya() -> None:
            start = field_maya.text().strip()
            start_dir = str(Path(start).parent) if start else ""
            path, _flt = QFileDialog.getOpenFileName(
                self,
                "Select Maya Executable",
                start_dir,
                "Maya (maya.exe);;Executables (*.exe);;All files (*.*)",
            )
            if path:
                field_maya.setText(path)

        def on_auto_detect_maya() -> None:
            found = resolve_maya_executable(field_maya.text().strip() or "maya")
            if not found:
                QMessageBox.information(self, "Auto Detect", "Maya was not found. Browse to 'maya.exe' instead.")
                return
            field_maya.setText(found)

        btn_browse_maya.clicked.connect(on_browse_maya)
        btn_auto_maya.clicked.connect(on_auto_detect_maya)
        row_maya = QWidget(self)
        row_maya_l = QHBoxLayout(row_maya)
        row_maya_l.setContentsMargins(0, 0, 0, 0)
        row_maya_l.setSpacing(8)
        row_maya_l.addWidget(field_maya, 1)
        row_maya_l.addWidget(btn_auto_maya, 0)
        row_maya_l.addWidget(btn_browse_maya, 0)
        form.addRow("Maya Executable", row_maya)

        # Houdini
        field_houdini = QLineEdit(self)
        field_houdini.setPlaceholderText("Auto-detect, or browse to houdini.exe")
        field_houdini.setProperty("mono", True)
        self._houdini_exe_field = field_houdini
        if self._settings is not None:
            cur_h = (self._settings.value("integrations/houdini_exe", "", str) or "").strip()
            field_houdini.setText(cur_h)
        else:
            field_houdini.setEnabled(False)
        btn_browse_houdini = QPushButton("Browse…", self)
        btn_auto_houdini = QPushButton("Auto Detect", self)
        if self._settings is None:
            btn_browse_houdini.setEnabled(False)
            btn_auto_houdini.setEnabled(False)

        def on_browse_houdini() -> None:
            start = field_houdini.text().strip()
            start_dir = str(Path(start).parent) if start else ""
            path, _flt = QFileDialog.getOpenFileName(
                self,
                "Select Houdini Executable",
                start_dir,
                "Houdini (houdini.exe);;Executables (*.exe);;All files (*.*)",
            )
            if path:
                field_houdini.setText(path)

        def on_auto_detect_houdini() -> None:
            found = resolve_houdini_executable(field_houdini.text().strip() or "houdini")
            if not found:
                QMessageBox.information(self, "Auto Detect", "Houdini was not found. Browse to 'houdini.exe' or set HFS.")
                return
            field_houdini.setText(found)

        btn_browse_houdini.clicked.connect(on_browse_houdini)
        btn_auto_houdini.clicked.connect(on_auto_detect_houdini)
        row_houdini = QWidget(self)
        row_houdini_l = QHBoxLayout(row_houdini)
        row_houdini_l.setContentsMargins(0, 0, 0, 0)
        row_houdini_l.setSpacing(8)
        row_houdini_l.addWidget(field_houdini, 1)
        row_houdini_l.addWidget(btn_auto_houdini, 0)
        row_houdini_l.addWidget(btn_browse_houdini, 0)
        form.addRow("Houdini Executable", row_houdini)

        # Houdini new file extension (Indie .hiplc / Commercial .hip / Non-Commercial .hipnc)
        combo_houdini_ext = QComboBox(self)
        combo_houdini_ext.setProperty("mono", True)
        combo_houdini_ext.addItem("Indie (.hiplc)", ".hiplc")
        combo_houdini_ext.addItem("Commercial (.hip)", ".hip")
        combo_houdini_ext.addItem("Non-Commercial (.hipnc)", ".hipnc")
        self._houdini_workfile_ext_combo = combo_houdini_ext
        if self._settings is not None:
            cur_ext = (self._settings.value("integrations/houdini_workfile_ext", ".hiplc", str) or ".hiplc").strip().lower()
            for i in range(combo_houdini_ext.count()):
                if (combo_houdini_ext.itemData(i) or "").strip().lower() == cur_ext:
                    combo_houdini_ext.setCurrentIndex(i)
                    break
        else:
            combo_houdini_ext.setEnabled(False)
        form.addRow("Houdini new file extension", combo_houdini_ext)

        # Substance Painter
        field_sp = QLineEdit(self)
        field_sp.setPlaceholderText("Auto-detect, or browse to Adobe Substance 3D Painter.exe")
        field_sp.setProperty("mono", True)
        self._substance_painter_exe_field = field_sp
        if self._settings is not None:
            cur_sp = (self._settings.value("integrations/substance_painter_exe", "", str) or "").strip()
            field_sp.setText(cur_sp)
        else:
            field_sp.setEnabled(False)
        btn_browse_sp = QPushButton("Browse…", self)
        btn_auto_sp = QPushButton("Auto Detect", self)
        if self._settings is None:
            btn_browse_sp.setEnabled(False)
            btn_auto_sp.setEnabled(False)

        def on_browse_sp() -> None:
            start = field_sp.text().strip()
            start_dir = str(Path(start).parent) if start else ""
            path, _flt = QFileDialog.getOpenFileName(
                self,
                "Select Substance Painter Executable",
                start_dir,
                "Substance Painter (*.exe);;Executables (*.exe);;All files (*.*)",
            )
            if path:
                field_sp.setText(path)

        def on_auto_detect_sp() -> None:
            found = resolve_substance_painter_executable(field_sp.text().strip() or "substancepainter")
            if not found:
                QMessageBox.information(
                    self,
                    "Auto Detect",
                    "Substance Painter was not found. Browse to 'Adobe Substance 3D Painter.exe'.",
                )
                return
            field_sp.setText(found)

        btn_browse_sp.clicked.connect(on_browse_sp)
        btn_auto_sp.clicked.connect(on_auto_detect_sp)
        row_sp = QWidget(self)
        row_sp_l = QHBoxLayout(row_sp)
        row_sp_l.setContentsMargins(0, 0, 0, 0)
        row_sp_l.setSpacing(8)
        row_sp_l.addWidget(field_sp, 1)
        row_sp_l.addWidget(btn_auto_sp, 0)
        row_sp_l.addWidget(btn_browse_sp, 0)
        form.addRow("Substance Painter Executable", row_sp)

        # Affinity by Canva
        field_affinity = QLineEdit(self)
        field_affinity.setPlaceholderText("Auto-detect Affinity by Canva (MSIX), or browse to Affinity.exe")
        field_affinity.setProperty("mono", True)
        self._affinity_exe_field = field_affinity
        if self._settings is not None:
            cur_affinity = (self._settings.value("integrations/affinity_exe", "", str) or "").strip()
            if not cur_affinity:
                cur_affinity = (self._settings.value("integrations/affinity_photo_exe", "", str) or "").strip()
            field_affinity.setText(cur_affinity)
        else:
            field_affinity.setEnabled(False)
        btn_browse_affinity = QPushButton("Browse…", self)
        btn_auto_affinity = QPushButton("Auto Detect", self)
        if self._settings is None:
            btn_browse_affinity.setEnabled(False)
            btn_auto_affinity.setEnabled(False)

        def on_browse_affinity() -> None:
            start = field_affinity.text().strip()
            start_dir = str(Path(start).parent) if start else ""
            path, _flt = QFileDialog.getOpenFileName(
                self,
                "Select Affinity Executable",
                start_dir,
                "Affinity (Affinity.exe Photo.exe);;Executables (*.exe);;All files (*.*)",
            )
            if path:
                field_affinity.setText(path)

        def on_auto_detect_affinity() -> None:
            found = resolve_affinity_executable(field_affinity.text().strip() or "Affinity.exe")
            if not found:
                QMessageBox.information(
                    self,
                    "Auto Detect",
                    "Affinity was not found. Install Affinity by Canva, or browse to Affinity.exe.",
                )
                return
            field_affinity.setText(found)

        btn_browse_affinity.clicked.connect(on_browse_affinity)
        btn_auto_affinity.clicked.connect(on_auto_detect_affinity)
        row_affinity = QWidget(self)
        row_affinity_l = QHBoxLayout(row_affinity)
        row_affinity_l.setContentsMargins(0, 0, 0, 0)
        row_affinity_l.setSpacing(8)
        row_affinity_l.addWidget(field_affinity, 1)
        row_affinity_l.addWidget(btn_auto_affinity, 0)
        row_affinity_l.addWidget(btn_browse_affinity, 0)
        form.addRow("Affinity Executable", row_affinity)

        # RizomUV
        field_rz = QLineEdit(self)
        field_rz.setPlaceholderText("Auto-detect, or browse to rizomuv_vs.exe")
        field_rz.setProperty("mono", True)
        self._rizomuv_exe_field = field_rz
        if self._settings is not None:
            cur_rz = (self._settings.value("integrations/rizomuv_exe", "", str) or "").strip()
            field_rz.setText(cur_rz)
        else:
            field_rz.setEnabled(False)
        btn_browse_rz = QPushButton("Browse…", self)
        btn_auto_rz = QPushButton("Auto Detect", self)
        if self._settings is None:
            btn_browse_rz.setEnabled(False)
            btn_auto_rz.setEnabled(False)

        def on_browse_rz() -> None:
            start = field_rz.text().strip()
            start_dir = str(Path(start).parent) if start else ""
            path, _flt = QFileDialog.getOpenFileName(
                self,
                "Select RizomUV Executable",
                start_dir,
                "RizomUV (rizomuv_vs.exe rizomuv.exe);;Executables (*.exe);;All files (*.*)",
            )
            if path:
                field_rz.setText(path)

        def on_auto_detect_rz() -> None:
            found = resolve_rizomuv_executable(field_rz.text().strip() or "rizomuv")
            if not found:
                QMessageBox.information(
                    self,
                    "Auto Detect",
                    "RizomUV was not found. Browse to 'rizomuv_vs.exe' or 'rizomuv.exe'.",
                )
                return
            field_rz.setText(found)

        btn_browse_rz.clicked.connect(on_browse_rz)
        btn_auto_rz.clicked.connect(on_auto_detect_rz)
        row_rz = QWidget(self)
        row_rz_l = QHBoxLayout(row_rz)
        row_rz_l.setContentsMargins(0, 0, 0, 0)
        row_rz_l.setSpacing(8)
        row_rz_l.addWidget(field_rz, 1)
        row_rz_l.addWidget(btn_auto_rz, 0)
        row_rz_l.addWidget(btn_browse_rz, 0)
        form.addRow("RizomUV Executable", row_rz)

        hint = QLabel(
            "If empty, MonoStudio will try to auto-detect Blender, Maya, Houdini, Substance Painter, Affinity, and RizomUV.\n"
            "Env vars: MONOSTUDIO_BLENDER_EXE, MONOSTUDIO_MAYA_EXE, MONOSTUDIO_HOUDINI_EXE, "
            "MONOSTUDIO_SUBSTANCE_PAINTER_EXE, MONOSTUDIO_AFFINITY_EXE, MONOSTUDIO_RIZOMUV_EXE (or HFS for Houdini)."
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")

        layout.addWidget(grp, 0)
        layout.addWidget(hint, 0)
        layout.addStretch(1)
        return root

    def _build_workspace_discord_integrations_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("SettingsPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 16)
        layout.setSpacing(12)

        self._discord_integrations_banner = QLabel(inner)
        self._discord_integrations_banner.setWordWrap(True)
        self._discord_integrations_banner.setObjectName("DialogHelper")
        self._discord_integrations_banner.setVisible(False)
        layout.addWidget(self._discord_integrations_banner)

        card, card_l = add_settings_section(
            inner,
            "Discord",
            "Post pipeline alerts to a Discord channel via Incoming Webhook. "
            "URL is stored in workspace .monostudio/integrations.json (synced).",
        )

        self._discord_enabled_cb = QCheckBox("Enable Discord notifications", card)
        card_l.addWidget(self._discord_enabled_cb)

        self._discord_webhook_field = QLineEdit(card)
        self._discord_webhook_field.setProperty("mono", True)
        style_settings_line_edit(self._discord_webhook_field, min_width=320)
        self._discord_url_replace_btn = QPushButton("Replace…", card)
        self._discord_url_replace_btn.setObjectName("SettingsInlineActionButton")
        self._discord_url_replace_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._discord_url_replace_btn.clicked.connect(self._on_discord_replace_url)
        url_row = QWidget(card)
        url_row_l = QHBoxLayout(url_row)
        url_row_l.setContentsMargins(0, 0, 0, 0)
        url_row_l.setSpacing(8)
        url_row_l.addWidget(self._discord_webhook_field, 1)
        url_row_l.addWidget(self._discord_url_replace_btn, 0)
        add_settings_field_row(card_l, "Webhook URL", url_row)

        self._discord_label_field = QLineEdit(card)
        self._discord_label_field.setPlaceholderText("#pipeline-general")
        style_settings_line_edit(self._discord_label_field, min_width=200)
        add_settings_field_row(card_l, "Channel label", self._discord_label_field)

        add_settings_subsection_title(card_l, "Events")
        self._discord_mention_cb = QCheckBox("@mentions in notes", card)
        self._discord_mention_cb.setChecked(True)
        card_l.addWidget(self._discord_mention_cb)

        self._discord_note_done_cb = QCheckBox("Note marked done", card)
        card_l.addWidget(self._discord_note_done_cb)

        self._discord_inbox_cb = QCheckBox("Inbox & Outbox (drop & distribute)", card)
        card_l.addWidget(self._discord_inbox_cb)

        self._discord_schedule_cb = QCheckBox("Schedule due reminders (daily)", card)
        card_l.addWidget(self._discord_schedule_cb)

        self._discord_schedule_assigned_cb = QCheckBox("Schedule assignments", card)
        card_l.addWidget(self._discord_schedule_assigned_cb)

        test_row = QWidget(card)
        test_row_l = QHBoxLayout(test_row)
        test_row_l.setContentsMargins(0, 4, 0, 0)
        self._discord_test_btn = QPushButton("Send test message", test_row)
        self._discord_test_btn.setObjectName("SettingsInlineActionButton")
        self._discord_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._discord_test_btn.clicked.connect(self._on_discord_send_test)
        test_row_l.addWidget(self._discord_test_btn, 0)
        self._discord_test_notifications_btn = QPushButton("Test notifications…", test_row)
        self._discord_test_notifications_btn.setObjectName("SettingsInlineActionButton")
        self._discord_test_notifications_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._discord_test_notifications_btn.clicked.connect(self._on_discord_test_notifications)
        test_row_l.addWidget(self._discord_test_notifications_btn, 0)
        test_row_l.addStretch(1)
        card_l.addWidget(test_row)

        layout.addWidget(card)
        layout.addStretch(1)
        scroll.setWidget(inner)
        self._refresh_discord_integrations_ui()
        return scroll

    def _build_project_advanced_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        title = QLabel("Advanced (dangerous)")
        title.setObjectName("DialogSectionTitle")
        desc = QLabel(
            "Force Rename Project ID is a migration-level operation.\n"
            "It can break external references and cached data.\n"
            "Use only when you understand the impact."
        )
        desc.setWordWrap(True)
        desc.setObjectName("DialogHint")

        btn = QPushButton("⚠️ Force Rename Project ID…")
        btn.setEnabled(self._project_root is not None)
        if self._project_root is None:
            btn.setToolTip("Select a project to use advanced operations.")
        btn.clicked.connect(self._open_force_rename_project_id)

        layout.addWidget(title, 0)
        layout.addWidget(desc, 0)
        layout.addWidget(btn, 0)
        layout.addStretch(1)
        return root

    def _open_force_rename_project_id(self) -> None:
        if self._project_root is None:
            return
        dlg = ForceRenameProjectIdDialog(project_root=self._project_root, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        renamed_to = dlg.renamed_to()
        if renamed_to is None:
            return
        # Persist result for caller (MainWindow) to refresh state.
        self._project_root_renamed_to = renamed_to
        self._project_root = renamed_to

    def project_root_renamed_to(self) -> Path | None:
        return self._project_root_renamed_to

    @staticmethod
    def _placeholder(text: str) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(12, 12, 12, 12)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setObjectName("DialogHelper")
        l.addWidget(lab)
        l.addStretch(1)
        return w

    @staticmethod
    def _field(label: str, widget: QWidget) -> QWidget:
        block = QWidget()
        l = QVBoxLayout(block)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)
        lab = QLabel(label)
        lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        l.addWidget(lab)
        l.addWidget(widget)
        return block

    def _on_save(self) -> None:
        _admin_save = is_admin_capable()
        if _admin_save:
            if self._project_root is not None and self._pipeline_editor is not None:
                self._config = self._pipeline_editor.build_pipeline_types_and_presets()
                if not self._pipeline_editor.save_all_to_project(self._project_root):
                    QMessageBox.critical(self, "Settings", "Failed to save pipeline configuration to project.")
                    return
            elif self._project_root is not None:
                if not save_pipeline_types_and_presets_to_project(self._project_root, self._config):
                    QMessageBox.critical(self, "Settings", "Failed to save Pipeline Types & Presets to project.")
                    return
            else:
                if not save_pipeline_types_and_presets(self._config):
                    QMessageBox.critical(self, "Settings", "Failed to save Pipeline Types & Presets.")
                    return

        if self._project_root is not None and self._create_default_combos:
            mapping: dict[str, str] = {}
            for dep_id, cb in self._create_default_combos.items():
                raw = cb.currentData()
                dcc = (raw or "").strip() if isinstance(raw, str) else ""
                if dcc:
                    mapping[dep_id] = dcc
            if not write_create_default_dcc_map(self._project_root, mapping):
                QMessageBox.warning(
                    self,
                    "Settings",
                    "Failed to save Create defaults (default DCC per department).",
                )

        # Persist project-level use_dcc_folders when project is set.
        if self._project_root is not None and self._use_dcc_folders_cb is not None:
            if not save_use_dcc_folders(self._project_root, self._use_dcc_folders_cb.isChecked()):
                QMessageBox.warning(
                    self,
                    "Settings",
                    "Failed to save Use DCC folders to project.",
                )

        # System tray and Windows autostart.
        try:
            if self._settings is not None:
                from monostudio.core.tray_preferences import (
                    write_close_action,
                    write_close_prompt_shown,
                    write_start_minimized_to_tray,
                    write_start_with_windows,
                    write_tray_enabled,
                )
                from monostudio.core.windows_autostart import set_autostart

                if getattr(self, "_tray_enabled_cb", None) is not None:
                    write_tray_enabled(self._settings, self._tray_enabled_cb.isChecked())
                combo = getattr(self, "_tray_close_action_combo", None)
                if combo is not None:
                    data = combo.currentData()
                    if data in ("unset", "minimize", "quit"):
                        write_close_action(self._settings, data)
                        if data != "unset":
                            write_close_prompt_shown(self._settings, True)
                start_cb = getattr(self, "_tray_start_windows_cb", None)
                if start_cb is not None and sys.platform == "win32":
                    want = start_cb.isChecked()
                    write_start_with_windows(self._settings, want)
                    ok, msg = set_autostart(want)
                    if not ok:
                        QMessageBox.warning(self, "Windows startup", msg)
                    elif want:
                        write_start_with_windows(self._settings, True)
                min_cb = getattr(self, "_tray_start_minimized_cb", None)
                if min_cb is not None:
                    write_start_minimized_to_tray(self._settings, min_cb.isChecked())
                self._refresh_tray_autostart_status()
        except Exception:
            pass

        # Persist notification UI setting.
        try:
            if self._settings is not None and self._notification_max_visible_combo is not None:
                idx = self._notification_max_visible_combo.currentIndex()
                self._settings.setValue("notification/max_visible", idx + 1)
            if self._settings is not None and self._mention_delivery_combo is not None:
                from monostudio.core.notification_preferences import (
                    write_mention_delivery,
                    write_notification_vietnamese,
                )

                idx = self._mention_delivery_combo.currentIndex()
                mode = "windows" if idx == 1 else "builtin"
                data = self._mention_delivery_combo.currentData()
                if data in ("builtin", "windows"):
                    mode = data
                write_mention_delivery(self._settings, mode)
            if self._settings is not None and self._notification_vietnamese_cb is not None:
                from monostudio.core.notification_preferences import (
                    write_discord_disabled_locally,
                    write_notification_vietnamese,
                )

                write_notification_vietnamese(
                    self._settings,
                    self._notification_vietnamese_cb.isChecked(),
                )
                if self._discord_disabled_locally_cb is not None:
                    write_discord_disabled_locally(
                        self._settings,
                        self._discord_disabled_locally_cb.isChecked(),
                    )
        except Exception:
            pass

        # Inspector preview: thumbnail source (asset vs shot) + sequence playback FPS.
        try:
            if self._settings is not None:
                self._write_inspector_thumb_segment(self._inspector_thumb_segment_asset, "asset")
                self._write_inspector_thumb_segment(self._inspector_thumb_segment_shot, "shot")
            if self._settings is not None and self._inspector_sequence_fps_spin is not None:
                write_sequence_preview_fps(self._settings, self._inspector_sequence_fps_spin.value())
            if self._settings is not None and self._inspector_thumb_open_exe_field is not None:
                write_inspector_thumbnail_open_exe(
                    self._settings,
                    (self._inspector_thumb_open_exe_field.text() or "").strip(),
                )
            if self._settings is not None and getattr(self, "_video_player_backend_combo", None) is not None:
                backend = self._video_player_backend_combo.currentData()
                if isinstance(backend, str):
                    write_video_player_backend(self._settings, backend)
            if self._settings is not None and getattr(self, "_mpv_dir_field", None) is not None:
                write_mpv_directory(self._settings, (self._mpv_dir_field.text() or "").strip())
            if self._settings is not None and getattr(self, "_video_external_player_field", None) is not None:
                write_video_external_player_exe(
                    self._settings,
                    (self._video_external_player_field.text() or "").strip(),
                )
        except Exception:
            pass

        # Workspace Discord integrations (admin only).
        if _admin_save:
            if not self._persist_discord_integrations():
                return

        # Persist global pipeline behavior (create work/publish subfolders).
        try:
            if self._settings is not None and self._create_work_publish_subfolders_cb is not None:
                self._settings.setValue(
                    "pipeline/create_work_publish_subfolders",
                    self._create_work_publish_subfolders_cb.isChecked(),
                )
        except Exception:
            pass

        # Persist publish ignore extensions (same access tier as pipeline / scan rules).
        try:
            if (
                _admin_save
                and self._settings is not None
                and self._publish_ignore_ext_field is not None
            ):
                self._settings.setValue(
                    "pipeline/publish_ignore_extensions",
                    (self._publish_ignore_ext_field.text() or "").strip(),
                )
        except Exception:
            pass

        # Persist thumbnail sequence scan rules (same access tier as pipeline / scan rules).
        try:
            if _admin_save and self._settings is not None:
                if getattr(self, "_thumb_seq_ignore_ext_field", None) is not None:
                    self._settings.setValue(
                        "pipeline/thumbnail_sequence_ignore_extensions",
                        (self._thumb_seq_ignore_ext_field.text() or "").strip(),
                    )
                if getattr(self, "_thumb_seq_ignore_tokens_field", None) is not None:
                    self._settings.setValue(
                        "pipeline/thumbnail_sequence_ignore_tokens",
                        (self._thumb_seq_ignore_tokens_field.text() or "").strip(),
                    )
        except Exception:
            pass

        # Developer-only persisted diagnostics.
        try:
            if self._settings is not None and is_dev_session():
                if self._access_debug_cb is not None:
                    write_verbose_debug_enabled(self._settings, self._access_debug_cb.isChecked())
                if self._access_splash_spin is not None:
                    write_splash_display_ms(self._settings, self._access_splash_spin.value())
        except Exception:
            pass

        # Persist integrations (best-effort; should not block saving pipeline config).
        try:
            if self._settings is not None and self._blender_exe_field is not None:
                self._settings.setValue("integrations/blender_exe", (self._blender_exe_field.text() or "").strip())
            if self._settings is not None and self._maya_exe_field is not None:
                self._settings.setValue("integrations/maya_exe", (self._maya_exe_field.text() or "").strip())
            if self._settings is not None and self._houdini_exe_field is not None:
                self._settings.setValue("integrations/houdini_exe", (self._houdini_exe_field.text() or "").strip())
            if self._settings is not None and self._houdini_workfile_ext_combo is not None:
                ext = self._houdini_workfile_ext_combo.currentData()
                self._settings.setValue("integrations/houdini_workfile_ext", (ext if isinstance(ext, str) else ".hiplc"))
            if self._settings is not None and self._substance_painter_exe_field is not None:
                self._settings.setValue(
                    "integrations/substance_painter_exe",
                    (self._substance_painter_exe_field.text() or "").strip(),
                )
            if self._settings is not None and self._affinity_exe_field is not None:
                self._settings.setValue(
                    "integrations/affinity_exe",
                    (self._affinity_exe_field.text() or "").strip(),
                )
            if self._settings is not None and self._rizomuv_exe_field is not None:
                self._settings.setValue("integrations/rizomuv_exe", (self._rizomuv_exe_field.text() or "").strip())
        except Exception:
            pass

        try:
            if self._settings is not None and self._hotkeys_widget is not None:
                self._hotkeys_widget.persist(self._settings)
                self.hotkeys_changed.emit()
        except Exception:
            pass

        self.accept()

