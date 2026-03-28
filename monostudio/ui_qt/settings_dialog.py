from __future__ import annotations

import json
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
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.dcc_blender import resolve_blender_executable
from monostudio.core.dcc_houdini import resolve_houdini_executable
from monostudio.core.dcc_maya import resolve_maya_executable
from monostudio.core.dcc_rizomuv import resolve_rizomuv_executable
from monostudio.core.dcc_substance_painter import resolve_substance_painter_executable
from monostudio.core.fs_reader import read_use_dcc_folders, save_use_dcc_folders
from monostudio.core.pipeline_types_and_presets import (
    PipelineTypesAndPresets,
    load_department_vocabulary,
    load_pipeline_types_and_presets,
    load_pipeline_types_and_presets_for_project,
    save_pipeline_types_and_presets,
    save_pipeline_types_and_presets_to_project,
)
from monostudio.ui_qt.pipeline_structure_editor import PipelineStructureEditorWidget
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
from monostudio.core.app_paths import get_app_base_path
from monostudio.core.version import get_app_version
from monostudio.ui_qt.force_rename_project_id_dialog import ForceRenameProjectIdDialog
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS, MonosDialog, monos_font

# Icon size for update list rows
_UPDATE_ROW_ICON_SIZE = 24
# Fixed size for Download/Latest button and loading bar (same size so layout doesn't jump)
_UPDATE_ACTION_WIDTH = 128  # 96 + 1/3
_UPDATE_ACTION_HEIGHT = 28
_UPDATE_STATUS_ICON_SIZE = 32


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
        self._rizomuv_exe_field: QLineEdit | None = None
        self._pipeline_editor: PipelineStructureEditorWidget | None = None
        self._use_dcc_folders_cb: QCheckBox | None = None
        self._notification_max_visible_combo: QComboBox | None = None
        self._publish_ignore_ext_field: QLineEdit | None = None

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
        self._nav.currentRowChanged.connect(self._content_stack.setCurrentIndex)

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
        if stack is not None and buttons is not None and len(buttons) > 3:
            stack.setCurrentIndex(3)
            for i, b in enumerate(buttons):
                b.setChecked(i == 3)
        self._apply_cached_update_result(get_cached_check_result())

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
        if index == 3:
            self._apply_cached_update_result(get_cached_check_result())
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
        """Tier 2: General → Workspace | UI | Behavior | Updates (nút page ngang)."""
        return self._build_tier2_page_buttons(
            [
                ("Workspace", self._build_app_workspace_tab()),
                ("UI", self._build_ui_tab()),
                ("Behavior", self._build_behavior_tab()),
                ("Updates", self._build_updates_tab()),
            ],
            store_stack="general",
            store_buttons="general",
        )

    def _build_ui_tab(self) -> QWidget:
        """General → UI: notifications and other UI options."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        grp = QGroupBox("Notifications", root)
        grp_layout = QVBoxLayout(grp)
        form = QFormLayout()
        self._notification_max_visible_combo = QComboBox(grp)
        self._notification_max_visible_combo.addItems(["1", "2", "3"])
        try:
            cur = 1
            if self._settings is not None:
                v = self._settings.value("notification/max_visible", 1, int)
                cur = max(1, min(3, int(v) if v is not None else 1))
        except Exception:
            cur = 1
        self._notification_max_visible_combo.setCurrentIndex(cur - 1)
        form.addRow("Max visible toasts:", self._notification_max_visible_combo)
        hint = QLabel("Sidebar toasts (page, department, type) appear bottom-left; others bottom-right.", grp)
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        grp_layout.addLayout(form)
        grp_layout.addWidget(hint)
        layout.addWidget(grp)
        layout.addStretch(1)
        return root

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

        for idx, (product_id, display_name, repo) in enumerate(products):
            row = QWidget(list_container)
            row.setObjectName("UpdateProductListRow")
            row.setFixedHeight(44)
            if idx == len(products) - 1:
                row.setProperty("last", "true")
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
                action_btn.setFixedSize(_UPDATE_ACTION_WIDTH, _UPDATE_ACTION_HEIGHT)
                action_btn.clicked.connect(self._on_download_and_install)
                link_btn.clicked.connect(self._on_view_release_on_github)
                self._update_monostudio_action_btn = action_btn

                loading_widget = QWidget(row)
                loading_widget.setObjectName("UpdateDownloadLoading")
                loading_widget.setFixedSize(_UPDATE_ACTION_WIDTH + 6 + 24, _UPDATE_ACTION_HEIGHT)
                loading_l = QHBoxLayout(loading_widget)
                loading_l.setContentsMargins(0, 0, 0, 0)
                loading_l.setSpacing(6)
                progress_bar = QProgressBar(loading_widget)
                progress_bar.setObjectName("UpdateDownloadProgress")
                progress_bar.setMinimum(0)
                progress_bar.setMaximum(0)
                progress_bar.setFixedSize(_UPDATE_ACTION_WIDTH, _UPDATE_ACTION_HEIGHT)
                loading_l.addWidget(progress_bar)
                cancel_btn = QToolButton(loading_widget)
                cancel_btn.setObjectName("UpdateDownloadCancelBtn")
                cancel_btn.setIcon(lucide_icon("x", size=14, color_hex="#a1a1aa"))
                cancel_btn.setFixedSize(24, _UPDATE_ACTION_HEIGHT)
                cancel_btn.setToolTip("Cancel download")
                loading_l.addWidget(cancel_btn)
                loading_widget.hide()

                self._update_monostudio_loading_widget = loading_widget
                self._update_monostudio_progress_bar = progress_bar
                self._update_monostudio_cancel_btn = cancel_btn

                action_container = QWidget(row)
                action_container.setFixedSize(_UPDATE_ACTION_WIDTH + 6 + 24, _UPDATE_ACTION_HEIGHT)
                action_container_l = QHBoxLayout(action_container)
                action_container_l.setContentsMargins(0, 0, 0, 0)
                action_container_l.setSpacing(0)
                action_container_l.addWidget(action_btn)
                action_container_l.addWidget(loading_widget)
                row_l.addWidget(action_container)
            else:
                action_btn = QPushButton("View on GitHub", row)
                action_btn.setObjectName("SettingsCategoryActionButton")
                action_btn.setFixedSize(_UPDATE_ACTION_WIDTH, _UPDATE_ACTION_HEIGHT)
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
                loading_widget.setFixedSize(_UPDATE_ACTION_WIDTH + 6 + 24, _UPDATE_ACTION_HEIGHT)
                loading_l = QHBoxLayout(loading_widget)
                loading_l.setContentsMargins(0, 0, 0, 0)
                loading_l.setSpacing(6)
                progress_bar = QProgressBar(loading_widget)
                progress_bar.setObjectName("UpdateDownloadProgress")
                progress_bar.setMinimum(0)
                progress_bar.setMaximum(0)
                progress_bar.setFixedSize(_UPDATE_ACTION_WIDTH, _UPDATE_ACTION_HEIGHT)
                loading_l.addWidget(progress_bar)
                cancel_btn = QToolButton(loading_widget)
                cancel_btn.setObjectName("UpdateDownloadCancelBtn")
                cancel_btn.setIcon(lucide_icon("x", size=14, color_hex="#a1a1aa"))
                cancel_btn.setFixedSize(24, _UPDATE_ACTION_HEIGHT)
                cancel_btn.setToolTip("Cancel download")
                loading_l.addWidget(cancel_btn)
                loading_widget.hide()
                self._update_extra_loading[display_name] = (loading_widget, progress_bar, cancel_btn)

                action_container = QWidget(row)
                action_container.setFixedSize(_UPDATE_ACTION_WIDTH + 6 + 24, _UPDATE_ACTION_HEIGHT)
                action_container_l = QHBoxLayout(action_container)
                action_container_l.setContentsMargins(0, 0, 0, 0)
                action_container_l.setSpacing(0)
                action_container_l.addWidget(action_btn)
                action_container_l.addWidget(loading_widget)
                row_l.addWidget(action_container)

            list_layout.addWidget(row)

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
            "Updates are delivered via GitHub Releases. Download runs the installer and closes the app.",
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
        return self._build_tier2_page_buttons([
            ("Pipeline structure", self._build_pipeline_structure_page()),
            ("Scan rules", self._build_pipeline_scan_rules_tab()),
            ("Statuses", self._placeholder("Pipeline → Statuses (placeholder)")),
        ], store_stack="pipeline", store_buttons="pipeline")

    def _build_dccs_page(self) -> QWidget:
        """DCCs: single page (Blender / integrations)."""
        return self._build_project_integrations_tab()

    def _build_project_page(self) -> QWidget:
        """Tier 2: Project → Overview | Integrations | Advanced (nút page ngang)."""
        return self._build_tier2_page_buttons([
            ("Overview", self._placeholder("Project → Overview (placeholder)")),
            ("Integrations", self._placeholder("Project → Integrations (placeholder)")),
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
            "If empty, MonoStudio will try to auto-detect Blender, Maya, Houdini, Substance Painter, and RizomUV.\n"
            "Env vars: MONOSTUDIO_BLENDER_EXE, MONOSTUDIO_MAYA_EXE, MONOSTUDIO_HOUDINI_EXE, MONOSTUDIO_SUBSTANCE_PAINTER_EXE, MONOSTUDIO_RIZOMUV_EXE (or HFS for Houdini)."
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")

        layout.addWidget(grp, 0)
        layout.addWidget(hint, 0)
        layout.addStretch(1)
        return root

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

        # Persist project-level use_dcc_folders when project is set.
        if self._project_root is not None and self._use_dcc_folders_cb is not None:
            if not save_use_dcc_folders(self._project_root, self._use_dcc_folders_cb.isChecked()):
                QMessageBox.warning(
                    self,
                    "Settings",
                    "Failed to save Use DCC folders to project.",
                )

        # Persist notification UI setting.
        try:
            if self._settings is not None and self._notification_max_visible_combo is not None:
                idx = self._notification_max_visible_combo.currentIndex()
                self._settings.setValue("notification/max_visible", idx + 1)
        except Exception:
            pass

        # Persist global pipeline behavior (create work/publish subfolders).
        try:
            if self._settings is not None and self._create_work_publish_subfolders_cb is not None:
                self._settings.setValue(
                    "pipeline/create_work_publish_subfolders",
                    self._create_work_publish_subfolders_cb.isChecked(),
                )
        except Exception:
            pass

        # Persist publish ignore extensions.
        try:
            if self._settings is not None and self._publish_ignore_ext_field is not None:
                self._settings.setValue(
                    "pipeline/publish_ignore_extensions",
                    (self._publish_ignore_ext_field.text() or "").strip(),
                )
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
            if self._settings is not None and self._rizomuv_exe_field is not None:
                self._settings.setValue("integrations/rizomuv_exe", (self._rizomuv_exe_field.text() or "").strip())
        except Exception:
            pass
        self.accept()

