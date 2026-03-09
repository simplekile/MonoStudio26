from __future__ import annotations

import json
from dataclasses import dataclass
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
from monostudio.core.department_registry import (
    DepartmentRegistry,
    ensure_parent_from_preset,
    get_default_department_mapping,
    load_department_mapping_from_file,
    save_project_departments,
    write_departments_to_path,
)
from monostudio.core.fs_reader import (
    build_project_index,
    read_use_dcc_folders,
    save_use_dcc_folders,
)
from monostudio.core.structure_registry import StructureRegistry, save_project_structure, write_structure_to_path
from monostudio.core.type_registry import TypeRegistry, save_project_types, write_types_to_path
from monostudio.core.pipeline_types_and_presets import (
    PipelineTypesAndPresets,
    TypeDef,
    get_user_default_config_root,
    load_department_vocabulary,
    load_pipeline_types_and_presets,
    pipeline_department_presets_dir,
    save_pipeline_types_and_presets,
    save_pipeline_types_and_presets_to_project,
    save_pipeline_types_and_presets_to_user_default,
)
from monostudio.core.update_checker import (
    CheckResult,
    ExtraRepoRelease,
    EXTRA_REPOS,
    UpdateInfo,
    get_cached_check_result,
    get_cached_extra_repos,
    check_for_update,
    download_installer,
    fetch_extra_repos,
    get_extra_tool_installed_version,
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
_UPDATE_ACTION_WIDTH = 96
_UPDATE_ACTION_HEIGHT = 28


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
    """Runs update check in background; emits check_finished(result, error_message, extra_repos)."""

    check_finished = Signal(object, str, object)  # CheckResult | None, error str, dict[str, ExtraRepoRelease]

    def __init__(self, manifest_url: str, current_version: str, parent=None) -> None:
        super().__init__(parent)
        self._manifest_url = manifest_url
        self._current_version = current_version

    def run(self) -> None:
        try:
            result = check_for_update(self._current_version, self._manifest_url)
            extra = fetch_extra_repos(timeout=10)
            self.check_finished.emit(result, "", extra)
        except Exception as e:
            err = str(e) or "Check failed"
            self.check_finished.emit(None, err, {})


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
        self._config: PipelineTypesAndPresets = load_pipeline_types_and_presets()

        # Optional integrations UI fields.
        self._blender_exe_field: QLineEdit | None = None
        self._maya_exe_field: QLineEdit | None = None
        self._houdini_exe_field: QLineEdit | None = None
        self._houdini_workfile_ext_combo: QComboBox | None = None
        self._substance_painter_exe_field: QLineEdit | None = None
        self._rizomuv_exe_field: QLineEdit | None = None
        self._dept_mapping_table: QTableWidget | None = None
        self._type_mapping_table: QTableWidget | None = None
        self._structure_mapping_table: QTableWidget | None = None
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

        btn_save_as_default = QPushButton("Save as default")
        btn_save_as_default.setObjectName("SettingsCategoryActionButton")
        btn_save_as_default.clicked.connect(self._on_save_as_default)

        button_row = QWidget()
        button_row_l = QHBoxLayout(button_row)
        button_row_l.setContentsMargins(0, 0, 0, 0)
        button_row_l.setSpacing(10)
        button_row_l.addWidget(btn_save_as_default)
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
            self._pipeline_tier2_stack.setCurrentIndex(1)
        if getattr(self, "_pipeline_tier2_buttons", None) and len(self._pipeline_tier2_buttons) > 1:
            self._pipeline_tier2_buttons[1].setChecked(True)

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

    def _apply_cached_update_result(self, result: CheckResult | None) -> None:
        """Apply cached update check result to Updates tab UI (no new network check)."""
        if result is not None:
            if result.latest_notes:
                self._update_changelog.setMarkdown(result.latest_notes)
            else:
                self._update_changelog.setPlainText("No release notes for this version.")
            self._apply_changelog_line_height()
            self._update_latest_html_url = result.latest_html_url
            if result.update_available and result.update_info is not None:
                self._pending_update_info = result.update_info
                self._update_status_label.setText(f"Update {result.latest_version} available.")
            else:
                self._pending_update_info = None
                self._update_status_label.setText("You're on the latest version.")
            self._apply_monostudio_row(result)
        else:
            self._apply_monostudio_row(None)
        self._apply_extra_repos_ui(get_cached_extra_repos())

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
        """When General → Updates tab is shown, apply cached check result if any."""
        if index == 3:
            self._apply_cached_update_result(get_cached_check_result())

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

        # Check for updates + status
        self._update_check_btn = QPushButton("Check for updates", root)
        self._update_check_btn.setObjectName("DialogPrimaryButton")
        self._update_check_btn.clicked.connect(self._on_check_for_updates)
        layout.addWidget(self._update_check_btn)
        self._update_status_label = QLabel("", root)
        self._update_status_label.setWordWrap(True)
        self._update_status_label.setObjectName("UpdateStatusText")
        self._update_status_label.setMinimumHeight(20)
        layout.addWidget(self._update_status_label)

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

    def _on_check_for_updates(self) -> None:
        self._update_check_btn.setEnabled(False)
        self._update_status_label.setText("Checking…")
        self._update_changelog.clear()
        self._pending_update_info = None
        self._apply_monostudio_row(None)
        self._apply_extra_repos_ui({})
        self._update_check_worker = _UpdateCheckWorker(
            None,  # use default: GitHub Releases API
            get_app_version(),
            self,
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
        if error_message:
            self._update_status_label.setText(f"Check failed: {error_message}")
            self._update_changelog.clear()
            self._apply_monostudio_row(None)
            self._apply_extra_repos_ui(extra_repos or {})
            return
        if result is None:
            self._apply_extra_repos_ui(extra_repos or {})
            return
        if result.latest_notes:
            self._update_changelog.setMarkdown(result.latest_notes)
        else:
            self._update_changelog.setPlainText("No release notes for this version.")
        self._apply_changelog_line_height()
        self._update_latest_html_url = result.latest_html_url
        if result.update_available and result.update_info is not None:
            self._pending_update_info = result.update_info
            self._update_status_label.setText(f"Update {result.latest_version} available.")
        else:
            self._pending_update_info = None
            self._update_status_label.setText("You're on the latest version.")
        self._apply_monostudio_row(result)
        self._apply_extra_repos_ui(extra_repos or {})

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
        self._update_status_label.setText("Downloading…")
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
            action_btn.setStyleSheet("")  # force re-apply stylesheet
            action_btn.style().unpolish(action_btn)
            action_btn.style().polish(action_btn)
        else:
            action_btn.setText("Latest")
            action_btn.setObjectName("UpdateProductListBtnLatest")
            action_btn.setStyleSheet("")
            action_btn.style().unpolish(action_btn)
            action_btn.style().polish(action_btn)

    def _apply_extra_repos_ui(self, extra_repos: dict[str, ExtraRepoRelease]) -> None:
        """Update extra-repo rows: version, release notes link, Download (when asset URL present) or View on GitHub."""
        fallbacks = getattr(self, "_update_extra_fallback_url", {})
        for name, (ver_l, link_btn, action_btn) in getattr(self, "_update_extra_cards", {}).items():
            info = extra_repos.get(name)
            if info:
                # Show installed version so user sees current vs latest; keep latest from API for download/links
                ver_l.setText(get_extra_tool_installed_version(name) or "—")
                self._update_extra_html_url[name] = info.html_url or fallbacks.get(name, "")
                self._update_extra_download_url[name] = getattr(info, "download_url", "") or ""
                link_btn.setVisible(bool(info.html_url))
                action_btn.setVisible(True)
                if self._update_extra_download_url.get(name):
                    action_btn.setText("Download")
                    action_btn.setObjectName("UpdateProductListBtnDownload")
                else:
                    action_btn.setText("View on GitHub")
                    action_btn.setObjectName("SettingsCategoryActionButton")
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
        self._update_status_label.setText(f"Downloading {name}…")
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
        if success:
            self._update_status_label.setText("Launching installer…")
            try:
                if product == "monostudio":
                    run_installer_and_exit(Path(path))
                else:
                    launch_installer(Path(path))
                    self._update_status_label.setText("Installer launched. You can continue using MonoStudio.")
            except (OSError, RuntimeError) as e:
                msg = str(e).replace("\n", " ")[:200]
                self._update_status_label.setText(
                    f"Cannot run installer: {msg} Download from the release page below instead."
                )
        else:
            if (error_message or "").strip() == "Cancelled":
                self._update_status_label.setText("Download cancelled.")
            else:
                msg = (error_message.strip() or "Download failed.").replace("\n", " ")[:200]
                self._update_status_label.setText(f"Download failed: {msg} Or get the installer from the release page below.")
        self._update_download_product = ""

    def _build_pipeline_page(self) -> QWidget:
        """Tier 2: Pipeline → Mapping Folders | Categories | Scan rules | Statuses."""
        return self._build_tier2_page_buttons([
            ("Mapping Folders", self._build_mapping_folders_page()),
            ("Categories", self._build_categories_page()),
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

    def _build_categories_page(self) -> QWidget:
        """Pipeline → Categories: Tier 3 nút Asset Depts | Shot Depts (types & presets by kind)."""
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        stack = QStackedWidget(root)
        stack.setObjectName("SettingsPageStack")
        stack.addWidget(self._build_types_and_presets_kind(kind="asset"))
        stack.addWidget(self._build_types_and_presets_kind(kind="shot"))

        btn_row = QWidget(root)
        btn_row.setObjectName("Tier3Container")
        btn_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(6, 6, 6, 6)
        btn_l.setSpacing(4)

        group = QButtonGroup(root)
        buttons: list[QPushButton] = []
        for i, label in enumerate(("Asset Depts", "Shot Depts")):
            btn = QPushButton(label, btn_row)
            btn.setObjectName("Tier3Pill")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda _c=False, idx=i: self._on_page_button_clicked(stack, buttons, idx))
            group.addButton(btn)
            btn_l.addWidget(btn, 0)
            buttons.append(btn)

        outer.addWidget(btn_row, 0, Qt.AlignLeft)
        outer.addWidget(stack, 1)
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

    def _build_mapping_folders_page(self) -> QWidget:
        """
        Mapping Folders — Tier 3 segmented: Departments | Types | Structure.
        Shared preset system covers all three sections.
        """
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        if self._project_root is None:
            lab = QLabel("Select a project (Project Root) in General → Workspace to edit Mapping Folders.", root)
            lab.setWordWrap(True)
            lab.setObjectName("DialogHelper")
            outer.addWidget(lab)
            return root

        stack = QStackedWidget(root)
        stack.setObjectName("SettingsPageStack")
        stack.addWidget(self._build_department_mapping_section(root))
        stack.addWidget(self._build_type_mapping_section(root))
        stack.addWidget(self._build_structure_mapping_section(root))

        btn_row = QWidget(root)
        btn_row.setObjectName("Tier3Container")
        btn_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(6, 6, 6, 6)
        btn_l.setSpacing(4)

        group = QButtonGroup(root)
        buttons: list[QPushButton] = []
        for i, label in enumerate(("Departments", "Types", "Structure")):
            btn = QPushButton(label, btn_row)
            btn.setObjectName("Tier3Pill")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda _c=False, idx=i: self._on_page_button_clicked(stack, buttons, idx))
            group.addButton(btn)
            btn_l.addWidget(btn, 0)
            buttons.append(btn)

        outer.addWidget(btn_row, 0, Qt.AlignLeft)

        # Preset row — shared across all three sections
        preset_row = QWidget(root)
        preset_row_l = QHBoxLayout(preset_row)
        preset_row_l.setContentsMargins(0, 0, 0, 0)
        preset_row_l.setSpacing(8)
        preset_label = QLabel("Preset:", root)
        preset_combo = QComboBox(root)
        preset_combo.setMinimumWidth(200)
        preset_combo.setMaximumWidth(320)

        def _preset_list() -> list[tuple[str, Path]]:
            out: list[tuple[str, Path]] = []
            def _display_name(stem: str, suffix: str = "") -> str:
                name = stem.replace("_", " ").title()
                return f"{name}{suffix}" if suffix else name
            try:
                shipped = pipeline_department_presets_dir()
                if shipped.is_dir():
                    for p in sorted(shipped.glob("*.json")):
                        out.append((_display_name(p.stem), p))
            except Exception:
                pass
            try:
                user_base = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
                user_presets = user_base / "MonoStudio" / "department_presets"
                if user_presets.is_dir():
                    for p in sorted(user_presets.glob("*.json")):
                        out.append((_display_name(p.stem, " (saved)"), p))
            except Exception:
                pass
            return out

        for name, path in _preset_list():
            preset_combo.addItem(name, path)
        apply_preset_btn = QPushButton("Apply", root)
        apply_preset_btn.setToolTip("Load selected preset into all tables. Save to apply to project.")
        preset_row_l.addWidget(preset_label)
        preset_row_l.addWidget(preset_combo, 1)
        preset_row_l.addWidget(apply_preset_btn)
        preset_row_l.addStretch()
        outer.addWidget(preset_row)

        outer.addWidget(stack, 1)

        # Preset helpers -------------------------------------------------

        def _user_presets_dir() -> Path:
            try:
                base = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
                d = base / "MonoStudio" / "department_presets"
                d.mkdir(parents=True, exist_ok=True)
                return d
            except Exception:
                return Path()

        def _read_id_label_folder_table(table: QTableWidget) -> dict[str, dict]:
            out: dict[str, dict] = {}
            for row in range(table.rowCount()):
                id_item = table.item(row, 0)
                label_item = table.item(row, 1)
                folder_item = table.item(row, 2)
                if id_item is None or folder_item is None:
                    continue
                fid = (id_item.text() or "").strip()
                folder = (folder_item.text() or "").strip()
                if not fid or not folder:
                    continue
                label = (label_item.text() or "").strip() if label_item else fid
                out[fid] = {"label": label or fid, "folder": folder}
            return out

        def _dept_mapping_from_table() -> dict[str, dict]:
            table = self._dept_mapping_table
            if table is None:
                return {}
            out: dict[str, dict] = {}
            for row in range(table.rowCount()):
                id_item = table.item(row, 0)
                label_item = table.item(row, 1)
                shot_folder_item = table.item(row, 2)
                asset_folder_item = table.item(row, 3)
                order_item = table.item(row, 4)
                if id_item is None or shot_folder_item is None or asset_folder_item is None:
                    continue
                dept_id = (id_item.text() or "").strip()
                shot_folder = (shot_folder_item.text() or "").strip() or dept_id
                asset_folder = (asset_folder_item.text() or "").strip() or dept_id
                if not dept_id:
                    continue
                label = (label_item.text() or "").strip() if label_item else dept_id
                try:
                    order = int((order_item.text() or "999").strip()) if order_item else 999
                except ValueError:
                    order = 999
                out[dept_id] = {
                    "label": label or dept_id,
                    "folder": shot_folder,
                    "shot_folder": shot_folder,
                    "asset_folder": asset_folder,
                    "order": order,
                }
            return out

        def _build_full_preset() -> dict:
            payload: dict = {}
            dept = _dept_mapping_from_table()
            if dept:
                payload["departments"] = dept
            if self._type_mapping_table is not None:
                types = _read_id_label_folder_table(self._type_mapping_table)
                if types:
                    payload["types"] = types
            if self._structure_mapping_table is not None:
                struct = _read_id_label_folder_table(self._structure_mapping_table)
                if struct:
                    payload["folders"] = struct
            return payload

        def _load_preset_file(path: Path) -> dict | None:
            try:
                if not path.is_file():
                    return None
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
            return None

        def _repopulate_dept_table(mapping: dict[str, dict]) -> None:
            table = self._dept_mapping_table
            if table is None or not mapping:
                return
            temp_reg = DepartmentRegistry(mapping, None)
            try:
                pi = build_project_index(self._project_root, temp_reg) if self._project_root else None
            except Exception:
                pi = None
            dept_ids = sorted(mapping.keys(), key=lambda d: (mapping[d].get("order", 999), d))
            table.setRowCount(len(dept_ids))
            fp = monos_font("Inter", 11, QFont.Weight.Bold)
            fs = monos_font("JetBrains Mono", 10)
            cp = QColor("#EEEEEE")
            cs = QColor("#888888")
            for row, dept_id in enumerate(dept_ids):
                raw = mapping[dept_id]
                lv = raw.get("label", dept_id)
                sfv = raw.get("shot_folder") or raw.get("folder") or dept_id
                afv = raw.get("asset_folder") or raw.get("folder") or dept_id
                ov = raw.get("order", 999)
                el = temp_reg.get_mapping_edit_level(self._project_root, dept_id, pi) if self._project_root else "FREE"
                id_item = QTableWidgetItem(dept_id)
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
                id_item.setFont(fp)
                id_item.setForeground(cp)
                table.setItem(row, 0, id_item)
                li = QTableWidgetItem(lv)
                li.setForeground(cs)
                table.setItem(row, 1, li)
                si = QTableWidgetItem(sfv)
                si.setFont(fs)
                si.setForeground(cs)
                if el == "MIGRATION_REQUIRED":
                    si.setFlags(si.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 2, si)
                ai = QTableWidgetItem(afv)
                ai.setFont(fs)
                ai.setForeground(cs)
                if el == "MIGRATION_REQUIRED":
                    ai.setFlags(ai.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 3, ai)
                oi = QTableWidgetItem(str(ov))
                oi.setFont(fs)
                oi.setForeground(cs)
                oi.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 4, oi)
                badge = QLabel(el.replace("_", " "))
                badge.setAlignment(Qt.AlignCenter)
                if el == "MIGRATION_REQUIRED":
                    badge.setStyleSheet(
                        "background-color: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid #ef4444;"
                        " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                    )
                elif el == "WARNING":
                    badge.setStyleSheet(
                        "background-color: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b;"
                        " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                    )
                else:
                    badge.setStyleSheet(
                        "background-color: rgba(37, 99, 235, 0.15); color: #2563eb; border: 1px solid #2563eb;"
                        " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                    )
                table.setCellWidget(row, 5, badge)

        def _apply_preset_data(data: dict) -> None:
            dept_data = data.get("departments")
            if isinstance(dept_data, dict) and dept_data:
                _repopulate_dept_table(dept_data)
            types_data = data.get("types")
            if isinstance(types_data, dict) and types_data and self._type_mapping_table is not None:
                _repopulate_id_label_folder_table(self._type_mapping_table, types_data)
            folders_data = data.get("folders")
            if isinstance(folders_data, dict) and folders_data and self._structure_mapping_table is not None:
                _repopulate_id_label_folder_table(self._structure_mapping_table, folders_data)

        def _repopulate_id_label_folder_table(table: QTableWidget, mapping: dict[str, dict]) -> None:
            ids = sorted(mapping.keys())
            table.setRowCount(len(ids))
            for row, fid in enumerate(ids):
                entry = mapping[fid] if isinstance(mapping.get(fid), dict) else {}
                label_val = entry.get("label", fid)
                folder_val = entry.get("folder", fid)
                id_item = QTableWidgetItem(fid)
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 0, id_item)
                table.setItem(row, 1, QTableWidgetItem(label_val if isinstance(label_val, str) else fid))
                table.setItem(row, 2, QTableWidgetItem(folder_val if isinstance(folder_val, str) else fid))

        def _on_save_preset() -> None:
            payload = _build_full_preset()
            if not payload:
                QMessageBox.information(self, "Save preset", "No mapping data to save.")
                return
            initial_dir = str(_user_presets_dir()) if _user_presets_dir() else ""
            path, _ = QFileDialog.getSaveFileName(
                self, "Save mapping preset", initial_dir, "Mapping preset (*.json);;All files (*)",
            )
            if not path or not path.strip():
                return
            try:
                from monostudio.core.atomic_write import atomic_write_text
                content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
                atomic_write_text(Path(path), content, encoding="utf-8")
                QMessageBox.information(self, "Save preset", "Preset saved.")
                preset_combo.blockSignals(True)
                preset_combo.clear()
                for name, p in _preset_list():
                    preset_combo.addItem(name, p)
                preset_combo.blockSignals(False)
            except OSError as e:
                QMessageBox.warning(self, "Save preset", f"Failed to save: {e}")

        def _on_load_preset() -> None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Load mapping preset", "", "Mapping preset (*.json);;All files (*)",
            )
            if not path or not path.strip():
                return
            data = _load_preset_file(Path(path))
            if not data:
                QMessageBox.warning(self, "Load preset", "Invalid or empty preset file.")
                return
            _apply_preset_data(data)
            QMessageBox.information(self, "Load preset", "Preset loaded. Click Save to apply to project.")

        def _on_apply_preset() -> None:
            preset_path = preset_combo.currentData()
            if preset_path is None:
                return
            data = _load_preset_file(preset_path)
            if data:
                _apply_preset_data(data)

        def _on_reset() -> None:
            mono_path = pipeline_department_presets_dir() / "mono2026_preset.json"
            data = _load_preset_file(mono_path)
            if data:
                _apply_preset_data(data)
            else:
                default_dept = get_default_department_mapping()
                default_data: dict = {}
                if default_dept:
                    default_data["departments"] = default_dept
                from monostudio.core.type_registry import _default_type_mapping
                default_data["types"] = _default_type_mapping()
                from monostudio.core.structure_registry import _DEFAULT_MAPPING as _DEFAULT_STRUCTURE
                default_data["folders"] = dict(_DEFAULT_STRUCTURE)
                _apply_preset_data(default_data)

        apply_preset_btn.clicked.connect(_on_apply_preset)

        btn_actions = QWidget(root)
        btn_actions_l = QHBoxLayout(btn_actions)
        btn_actions_l.setContentsMargins(0, 0, 0, 0)
        btn_actions_l.setSpacing(8)

        reset_btn = QPushButton("Reset to default", root)
        reset_btn.setToolTip("Reset all mapping tables (Departments, Types, Structure) to built-in defaults.")
        reset_btn.clicked.connect(_on_reset)
        save_preset_btn = QPushButton("Save preset…", root)
        save_preset_btn.setToolTip("Save all mapping tables as a single JSON preset file.")
        save_preset_btn.clicked.connect(_on_save_preset)
        load_preset_btn = QPushButton("Load preset…", root)
        load_preset_btn.setToolTip("Load a preset file into all tables. Save to apply to project.")
        load_preset_btn.clicked.connect(_on_load_preset)

        btn_actions_l.addWidget(reset_btn)
        btn_actions_l.addWidget(save_preset_btn)
        btn_actions_l.addWidget(load_preset_btn)
        btn_actions_l.addStretch()
        outer.addWidget(btn_actions)

        return root

    def _build_department_mapping_section(self, parent: QWidget) -> QWidget:
        """Department mapping table section (no preset controls — handled by parent)."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        registry = DepartmentRegistry.for_project(self._project_root)
        try:
            project_index = build_project_index(self._project_root, registry)
        except Exception:
            project_index = None

        hint = QLabel(
            "Map logical department IDs to physical folder names per context. Same department (e.g. fx) can use different folders for shots (e.g. 02_fx) and assets (e.g. 06_fx).",
            root,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        layout.addWidget(hint)

        table = QTableWidget(root)
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["ID", "Label", "Folder (Shot)", "Folder (Asset)", "Order", "Status"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)
        table.setColumnWidth(0, 140)
        dept_ids = registry.get_departments()
        table.setRowCount(len(dept_ids))
        font_primary = monos_font("Inter", 11, QFont.Weight.Bold)
        font_secondary = monos_font("JetBrains Mono", 10)
        color_primary = QColor("#EEEEEE")
        color_secondary = QColor("#888888")
        for row, dept_id in enumerate(dept_ids):
            raw = registry.get_raw_mapping().get(dept_id, {})
            label_val = raw.get("label", dept_id)
            shot_folder_val = raw.get("shot_folder") or raw.get("folder") or dept_id
            asset_folder_val = raw.get("asset_folder") or raw.get("folder") or dept_id
            order_val = raw.get("order", 999)
            edit_level = registry.get_mapping_edit_level(self._project_root, dept_id, project_index)

            id_item = QTableWidgetItem(dept_id)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            id_item.setFont(font_primary)
            id_item.setForeground(color_primary)
            table.setItem(row, 0, id_item)

            label_item = QTableWidgetItem(label_val)
            label_item.setForeground(color_secondary)
            table.setItem(row, 1, label_item)

            shot_folder_item = QTableWidgetItem(shot_folder_val)
            shot_folder_item.setFont(font_secondary)
            shot_folder_item.setForeground(color_secondary)
            if edit_level == "MIGRATION_REQUIRED":
                shot_folder_item.setFlags(shot_folder_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 2, shot_folder_item)

            asset_folder_item = QTableWidgetItem(asset_folder_val)
            asset_folder_item.setFont(font_secondary)
            asset_folder_item.setForeground(color_secondary)
            if edit_level == "MIGRATION_REQUIRED":
                asset_folder_item.setFlags(asset_folder_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 3, asset_folder_item)

            order_item = QTableWidgetItem(str(order_val))
            order_item.setFont(font_secondary)
            order_item.setForeground(color_secondary)
            order_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 4, order_item)

            status_badge = QLabel(edit_level.replace("_", " "))
            status_badge.setAlignment(Qt.AlignCenter)
            if edit_level == "MIGRATION_REQUIRED":
                status_badge.setStyleSheet(
                    "background-color: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid #ef4444;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
                status_badge.setToolTip("Files exist in this department folder. Inline edit blocked; use Run Migration.")
            elif edit_level == "WARNING":
                status_badge.setStyleSheet(
                    "background-color: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
                status_badge.setToolTip("Assets exist but no files in this folder yet. Edit with caution.")
            else:
                status_badge.setStyleSheet(
                    "background-color: rgba(37, 99, 235, 0.15); color: #2563eb; border: 1px solid #2563eb;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
            table.setCellWidget(row, 5, status_badge)

        self._dept_mapping_table = table
        table.setMaximumWidth(960)
        layout.addWidget(table)

        run_mig_btn = QPushButton("Run Migration…", root)
        run_mig_btn.setMaximumWidth(200)
        run_mig_btn.setToolTip("Required when folder mapping is changed and files already exist in the department folder. Not implemented in this release.")
        run_mig_btn.setEnabled(True)
        def _on_run_migration() -> None:
            QMessageBox.information(
                self,
                "Run Migration",
                "Migration is out of scope in this release. Use an external tool to move files when changing department folder names.",
            )
        run_mig_btn.clicked.connect(_on_run_migration)
        layout.addWidget(run_mig_btn)

        mig_label = QLabel("Migration is out of scope: changing folder when files exist requires a separate migration tool.", root)
        mig_label.setWordWrap(True)
        mig_label.setObjectName("DialogHelper")
        layout.addWidget(mig_label)
        return root

    def _build_type_mapping_section(self, parent: QWidget) -> QWidget:
        """Type mapping table section with edit-level status."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        try:
            registry = TypeRegistry.for_project(self._project_root)
        except RuntimeError as e:
            lab = QLabel(f"Invalid type config: {e}", root)
            lab.setWordWrap(True)
            lab.setObjectName("DialogHelper")
            layout.addWidget(lab)
            return root

        hint = QLabel(
            "Map logical asset type IDs to physical folder names. Logical IDs are immutable; only folder names and labels can be edited.",
            root,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        layout.addWidget(hint)

        table = QTableWidget(root)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Logical ID", "Label", "Folder", "Status"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)
        type_ids = registry.get_types()
        table.setRowCount(len(type_ids))
        for row, type_id in enumerate(type_ids):
            raw = registry.get_raw_mapping().get(type_id, {})
            label_val = raw.get("label", type_id)
            folder_val = raw.get("folder", type_id)
            edit_level = registry.get_mapping_edit_level(self._project_root, type_id)

            id_item = QTableWidgetItem(type_id)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 0, id_item)
            table.setItem(row, 1, QTableWidgetItem(label_val))
            folder_item = QTableWidgetItem(folder_val)
            if edit_level == "MIGRATION_REQUIRED":
                folder_item.setFlags(folder_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 2, folder_item)

            status_badge = QLabel(edit_level.replace("_", " "))
            status_badge.setAlignment(Qt.AlignCenter)
            if edit_level == "MIGRATION_REQUIRED":
                status_badge.setStyleSheet(
                    "background-color: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid #ef4444;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
                status_badge.setToolTip("Assets with files exist under this type folder. Inline edit blocked.")
            elif edit_level == "WARNING":
                status_badge.setStyleSheet(
                    "background-color: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
                status_badge.setToolTip("Type folder exists but is empty. Edit with caution.")
            else:
                status_badge.setStyleSheet(
                    "background-color: rgba(37, 99, 235, 0.15); color: #2563eb; border: 1px solid #2563eb;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
            table.setCellWidget(row, 3, status_badge)

        self._type_mapping_table = table
        table.setMaximumWidth(960)
        layout.addWidget(table)
        return root

    def _build_structure_mapping_section(self, parent: QWidget) -> QWidget:
        """Structure mapping table section with edit-level status."""
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        try:
            registry = StructureRegistry.for_project(self._project_root)
        except RuntimeError as e:
            lab = QLabel(f"Invalid structure config: {e}", root)
            lab.setWordWrap(True)
            lab.setObjectName("DialogHelper")
            layout.addWidget(lab)
            return root

        hint = QLabel(
            "Map top-level project folder IDs to physical folder names. IDs are immutable; only folder names and labels can be edited.",
            root,
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHelper")
        layout.addWidget(hint)

        table = QTableWidget(root)
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Logical ID", "Label", "Folder", "Status"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setShowGrid(False)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)
        folder_ids = registry.get_ids()
        raw = registry.get_raw_mapping()
        table.setRowCount(len(folder_ids))
        for row, fid in enumerate(folder_ids):
            entry = raw.get(fid, {})
            label_val = entry.get("label", fid)
            folder_val = entry.get("folder", fid)
            edit_level = registry.get_mapping_edit_level(self._project_root, fid)

            id_item = QTableWidgetItem(fid)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 0, id_item)
            table.setItem(row, 1, QTableWidgetItem(label_val))
            folder_item = QTableWidgetItem(folder_val)
            if edit_level == "MIGRATION_REQUIRED":
                folder_item.setFlags(folder_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 2, folder_item)

            status_badge = QLabel(edit_level.replace("_", " "))
            status_badge.setAlignment(Qt.AlignCenter)
            if edit_level == "MIGRATION_REQUIRED":
                status_badge.setStyleSheet(
                    "background-color: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid #ef4444;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
                status_badge.setToolTip("This folder has content. Inline edit blocked; rename folder manually first.")
            elif edit_level == "WARNING":
                status_badge.setStyleSheet(
                    "background-color: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
                status_badge.setToolTip("Folder exists but is empty. Edit with caution.")
            else:
                status_badge.setStyleSheet(
                    "background-color: rgba(37, 99, 235, 0.15); color: #2563eb; border: 1px solid #2563eb;"
                    " border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 6px;"
                )
            table.setCellWidget(row, 3, status_badge)

        self._structure_mapping_table = table
        table.setMaximumWidth(960)
        layout.addWidget(table)
        return root

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

    def _build_types_and_presets_kind(self, *, kind: str) -> QWidget:
        """
        kind:
          - "asset": type_id must NOT be shot/shot_*
          - "shot":  type_id must be shot or shot_*
        """
        def is_shot_type_id(type_id: str) -> bool:
            return type_id == "shot" or type_id.startswith("shot_")

        def allow_type_id(type_id: str) -> bool:
            if not _is_valid_type_id(type_id):
                return False
            if kind == "shot":
                return is_shot_type_id(type_id)
            return not is_shot_type_id(type_id)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        kind_label = "Asset" if kind == "asset" else "Shot"
        # Left: Types + Selected Type (dọc) — cùng cấu trúc và style cho Asset và Shot
        types_box = QGroupBox(f"{kind_label} Types")
        types_box.setObjectName("SettingsCategoryGroup")
        types_l = QVBoxLayout(types_box)
        types_l.setContentsMargins(12, 12, 12, 12)
        types_l.setSpacing(10)

        types_list = QListWidget()
        types_list.setObjectName("SelectableList")
        types_list.setSelectionMode(QListWidget.SingleSelection)
        types_l.addWidget(types_list, 1)

        btn_row = QWidget()
        btn_l = QHBoxLayout(btn_row)
        btn_l.setContentsMargins(0, 0, 0, 0)
        btn_l.setSpacing(10)
        btn_type_create = QPushButton("Add Type")
        btn_type_create.setObjectName("SettingsCategoryActionButton")
        btn_type_delete = QPushButton("Delete Type")
        btn_type_delete.setObjectName("SettingsCategoryActionButton")
        btn_type_delete.setEnabled(False)
        btn_type_reset = QPushButton("Reset default")
        btn_type_reset.setObjectName("SettingsCategoryActionButton")
        btn_l.addWidget(btn_type_create)
        btn_l.addWidget(btn_type_delete)
        btn_l.addWidget(btn_type_reset)
        btn_l.addStretch(1)
        types_l.addWidget(btn_row)

        details = QGroupBox(f"Selected {kind_label} Type")
        details.setObjectName("SettingsCategoryGroup")
        details_l = QVBoxLayout(details)
        details_l.setContentsMargins(12, 12, 12, 12)
        details_l.setSpacing(10)

        type_id_field = QLineEdit()
        type_id_field.setReadOnly(True)
        type_name_field = QLineEdit()
        type_short_field = QLineEdit()
        type_short_field.setValidator(QRegularExpressionValidator(QRegularExpression(r"[a-z0-9_]+"), type_short_field))

        details_l.addWidget(self._field("Type ID (immutable)", type_id_field))
        details_l.addWidget(self._field("Name", type_name_field))
        details_l.addWidget(self._field("Short Name", type_short_field))

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(12)
        left_l.addWidget(types_box, 1)
        left_l.addWidget(details, 1)
        left.setMinimumWidth(280)

        # Right: Department Presets — cùng cấu trúc và style cho Asset và Shot
        presets = QGroupBox(f"Department Presets ({kind_label})")
        presets.setObjectName("SettingsCategoryGroup")
        presets_l = QVBoxLayout(presets)
        presets_l.setContentsMargins(12, 12, 12, 12)
        presets_l.setSpacing(10)

        presets_btn_row = QWidget()
        presets_btn_l = QHBoxLayout(presets_btn_row)
        presets_btn_l.setContentsMargins(0, 0, 0, 0)
        presets_btn_l.setSpacing(10)
        btn_presets_reset = QPushButton("Reset default")
        btn_presets_reset.setObjectName("SettingsCategoryActionButton")
        presets_btn_l.addWidget(btn_presets_reset)
        presets_btn_l.addStretch(1)
        presets_l.addWidget(presets_btn_row)

        dept_list = QListWidget()
        dept_list.setObjectName("SelectableListMulti")
        dept_list.setSelectionMode(QAbstractItemView.MultiSelection)
        dept_list.setIconSize(QSize(16, 16))

        def _dept_icon(dept_id: str):
            defn = self._config.departments.get(dept_id)
            icon_name = defn.icon_name if defn and defn.icon_name else "folder"
            return lucide_icon(icon_name, size=16, color_hex=MONOS_COLORS["text_label"])

        def _dept_display_name(dept_id: str) -> str:
            defn = self._config.departments.get(dept_id)
            return defn.name if defn else dept_id

        def _update_dept_list_icons() -> None:
            for row in range(dept_list.count()):
                it = dept_list.item(row)
                if it:
                    dept_id = it.data(Qt.UserRole)
                    if dept_id:
                        it.setIcon(_dept_icon(dept_id))

        def refresh_dept_list() -> None:
            for row in range(dept_list.count()):
                it = dept_list.item(row)
                if it:
                    dept_id = it.data(Qt.UserRole)
                    if dept_id:
                        it.setIcon(_dept_icon(dept_id))
                        it.setText(_dept_display_name(dept_id))

        for d in self._vocab:
            it = QListWidgetItem(_dept_icon(d), _dept_display_name(d))
            it.setData(Qt.UserRole, d)
            dept_list.addItem(it)
        presets_l.addWidget(dept_list, 2)
        presets.setMinimumWidth(260)

        layout.addWidget(left, 1)
        layout.addWidget(presets, 1)

        def current_type_id() -> str | None:
            it = types_list.currentItem()
            return it.data(Qt.UserRole) if it else None

        def current_preset() -> str | None:
            return None

        def refresh_types(select: str | None) -> None:
            types_list.blockSignals(True)
            types_list.clear()
            items = [(tid, t) for tid, t in self._config.types.items() if (is_shot_type_id(tid) if kind == "shot" else not is_shot_type_id(tid))]
            for tid, t in sorted(items, key=lambda kv: kv[1].name.lower()):
                item = QListWidgetItem(f"{t.name} ({tid})")
                item.setData(Qt.UserRole, tid)
                types_list.addItem(item)
                if select == tid:
                    types_list.setCurrentItem(item)
            if types_list.currentItem() is None and types_list.count() > 0:
                types_list.setCurrentItem(types_list.item(0))
            types_list.blockSignals(False)
            on_type_selected()

        def refresh_presets() -> None:
            on_preset_selected()

        def on_type_selected() -> None:
            tid = current_type_id()
            t = self._config.types.get(tid or "")
            has = t is not None
            btn_type_delete.setEnabled(has)

            type_id_field.blockSignals(True)
            type_name_field.blockSignals(True)
            type_short_field.blockSignals(True)
            type_id_field.setText(t.type_id if t else "")
            type_name_field.setText(t.name if t else "")
            type_short_field.setText(t.short_name if t else "")
            type_id_field.blockSignals(False)
            type_name_field.blockSignals(False)
            type_short_field.blockSignals(False)

            refresh_presets()

        def on_type_fields_changed() -> None:
            tid = current_type_id()
            if not tid:
                return
            t = self._config.types.get(tid)
            if t is None:
                return
            name = type_name_field.text().strip()
            short = type_short_field.text().strip()
            if not name or not short:
                return
            self._config.types[tid] = TypeDef(
                type_id=tid,
                name=name,
                short_name=short,
                departments=t.departments,
                icon_name=t.icon_name,
            )
            refresh_types(select=tid)

        def on_create_type() -> None:
            from PySide6.QtWidgets import QInputDialog

            type_id, ok = QInputDialog.getText(self, "Add Type", "Type ID (lowercase, immutable):")
            if not ok:
                return
            type_id = (type_id or "").strip()
            if not allow_type_id(type_id):
                return
            if type_id in self._config.types:
                return
            name, ok = QInputDialog.getText(self, "Add Type", "Name (display):")
            if not ok:
                return
            name = (name or "").strip()
            if not name:
                return
            short, ok = QInputDialog.getText(self, "     Type", "Short Name (prefix):")
            if not ok:
                return
            short = (short or "").strip()
            if not short:
                return
            self._config.types[type_id] = TypeDef(type_id=type_id, name=name, short_name=short, departments=[], icon_name=None)
            refresh_types(select=type_id)

        def on_delete_type() -> None:
            tid = current_type_id()
            if not tid or tid not in self._config.types:
                return
            res = QMessageBox.question(self, "Delete Type", f"Delete type '{tid}'?")
            if res != QMessageBox.Yes:
                return
            self._config.types.pop(tid, None)
            refresh_types(select=None)

        def on_reset_types_default() -> None:
            res = QMessageBox.question(
                self,
                "Reset Types",
                "Reload all types from monostudio_data/pipeline/types_and_presets.json? Changes are applied in memory until you save.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                return
            loaded = load_pipeline_types_and_presets()
            self._config = PipelineTypesAndPresets(types=loaded.types, departments=self._config.departments)
            refresh_types(select=None)

        def on_reset_presets_default() -> None:
            res = QMessageBox.question(
                self,
                "Reset Department Presets",
                "Reload all department definitions from monostudio_data/pipeline/types_and_presets.json? Changes are applied in memory until you save.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                return
            loaded = load_pipeline_types_and_presets()
            self._config = PipelineTypesAndPresets(types=self._config.types, departments=loaded.departments)
            refresh_dept_list()
            on_preset_selected()

        def on_preset_selected() -> None:
            tid = current_type_id()
            t = self._config.types.get(tid or "")
            has = bool(t)
            dept_list.setEnabled(has)
            dept_list.blockSignals(True)
            for row in range(dept_list.count()):
                it = dept_list.item(row)
                dept = it.data(Qt.UserRole) if it else None
                it.setSelected(bool(has and t and dept in (t.departments or [])))
            _update_dept_list_icons()
            dept_list.blockSignals(False)

        def on_dept_selection_changed() -> None:
            tid = current_type_id()
            t = self._config.types.get(tid or "")
            if t is None:
                return
            selected = []
            for it in dept_list.selectedItems():
                d = it.data(Qt.UserRole) if it else None
                if isinstance(d, str) and d in self._vocab_set:
                    selected.append(d)
            self._config.types[tid] = TypeDef(
                type_id=tid,
                name=t.name,
                short_name=t.short_name,
                departments=selected,
                icon_name=t.icon_name,
            )

        # wiring
        types_list.currentItemChanged.connect(lambda _c, _p: on_type_selected())
        type_name_field.textChanged.connect(lambda _t: on_type_fields_changed())
        type_short_field.textChanged.connect(lambda _t: on_type_fields_changed())
        btn_type_create.clicked.connect(on_create_type)
        btn_type_delete.clicked.connect(on_delete_type)
        btn_type_reset.clicked.connect(on_reset_types_default)
        btn_presets_reset.clicked.connect(on_reset_presets_default)
        dept_list.itemSelectionChanged.connect(on_dept_selection_changed)
        dept_list.itemSelectionChanged.connect(lambda: _update_dept_list_icons())

        refresh_types(select=None)
        return root

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

    def _on_save_as_default(self) -> None:
        """Save current Pipeline Types & Presets and folder mappings to Documents/.monostudio/ as user default."""
        if not save_pipeline_types_and_presets_to_user_default(self._config):
            QMessageBox.critical(
                self,
                "Save as default",
                "Failed to save to Documents/.monostudio/pipeline/",
            )
            return
        root = get_user_default_config_root()
        pipeline_dir = root / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        saved_parts: list[str] = ["types_and_presets"]

        # Read department mapping from UI table (not disk).
        if self._dept_mapping_table is not None:
            dept_mapping: dict[str, dict] = {}
            for row in range(self._dept_mapping_table.rowCount()):
                id_item = self._dept_mapping_table.item(row, 0)
                label_item = self._dept_mapping_table.item(row, 1)
                shot_folder_item = self._dept_mapping_table.item(row, 2)
                asset_folder_item = self._dept_mapping_table.item(row, 3)
                order_item = self._dept_mapping_table.item(row, 4)
                if id_item is None or shot_folder_item is None or asset_folder_item is None:
                    continue
                dept_id = (id_item.text() or "").strip()
                if not dept_id:
                    continue
                shot_folder = (shot_folder_item.text() or "").strip() or dept_id
                asset_folder = (asset_folder_item.text() or "").strip() or dept_id
                label = (label_item.text() or "").strip() if label_item else dept_id
                try:
                    order = int((order_item.text() or "999").strip()) if order_item else 999
                except ValueError:
                    order = 999
                dept_mapping[dept_id] = {
                    "label": label or dept_id,
                    "folder": shot_folder,
                    "shot_folder": shot_folder,
                    "asset_folder": asset_folder,
                    "order": order,
                }
            if dept_mapping:
                # User default should have "parent" so new projects get nested folder layout.
                dept_mapping = ensure_parent_from_preset(dept_mapping)
                write_departments_to_path(pipeline_dir / "departments.json", dept_mapping)
                saved_parts.append("departments")

        # Read type mapping from UI table (not disk).
        if self._type_mapping_table is not None:
            type_mapping: dict[str, dict] = {}
            for row in range(self._type_mapping_table.rowCount()):
                id_item = self._type_mapping_table.item(row, 0)
                label_item = self._type_mapping_table.item(row, 1)
                folder_item = self._type_mapping_table.item(row, 2)
                if id_item is None or folder_item is None:
                    continue
                type_id = (id_item.text() or "").strip()
                folder = (folder_item.text() or "").strip()
                if not type_id or not folder:
                    continue
                label = (label_item.text() or "").strip() if label_item else type_id
                type_mapping[type_id] = {"label": label or type_id, "folder": folder}
            if type_mapping:
                write_types_to_path(pipeline_dir / "types.json", type_mapping)
                saved_parts.append("types")

        # Read structure mapping from UI table (not disk).
        if self._structure_mapping_table is not None:
            struct_mapping: dict[str, dict[str, str]] = {}
            for row in range(self._structure_mapping_table.rowCount()):
                id_item = self._structure_mapping_table.item(row, 0)
                label_item = self._structure_mapping_table.item(row, 1)
                folder_item = self._structure_mapping_table.item(row, 2)
                if id_item is None or folder_item is None:
                    continue
                fid = (id_item.text() or "").strip()
                folder = (folder_item.text() or "").strip()
                if not fid or not folder:
                    continue
                label = (label_item.text() or "").strip() if label_item else fid
                struct_mapping[fid] = {"label": label or fid, "folder": folder}
            if struct_mapping:
                write_structure_to_path(pipeline_dir / "structure.json", struct_mapping)
                saved_parts.append("structure")

        QMessageBox.information(
            self,
            "Save as default",
            f"Pipeline config saved to:\n{pipeline_dir}\n({', '.join(saved_parts)})",
        )

    def _on_save(self) -> None:
        if self._project_root is not None:
            if not save_pipeline_types_and_presets_to_project(self._project_root, self._config):
                QMessageBox.critical(self, "Settings", "Failed to save Pipeline Types & Presets to project.")
                return
        else:
            if not save_pipeline_types_and_presets(self._config):
                QMessageBox.critical(self, "Settings", "Failed to save Pipeline Types & Presets.")
                return

        # Persist project-level department mapping when project is set and table was built.
        if self._project_root is not None and self._dept_mapping_table is not None:
            mapping: dict[str, dict] = {}
            for row in range(self._dept_mapping_table.rowCount()):
                id_item = self._dept_mapping_table.item(row, 0)
                label_item = self._dept_mapping_table.item(row, 1)
                shot_folder_item = self._dept_mapping_table.item(row, 2)
                asset_folder_item = self._dept_mapping_table.item(row, 3)
                order_item = self._dept_mapping_table.item(row, 4)
                if id_item is None or shot_folder_item is None or asset_folder_item is None:
                    continue
                dept_id = (id_item.text() or "").strip()
                shot_folder = (shot_folder_item.text() or "").strip() or dept_id
                asset_folder = (asset_folder_item.text() or "").strip() or dept_id
                if not dept_id:
                    continue
                label = (label_item.text() or "").strip() if label_item else dept_id
                try:
                    order = int((order_item.text() or "999").strip()) if order_item else 999
                except ValueError:
                    order = 999
                mapping[dept_id] = {
                    "label": label or dept_id,
                    "folder": shot_folder,
                    "shot_folder": shot_folder,
                    "asset_folder": asset_folder,
                    "order": order,
                }
            if mapping:
                # Table has no parent column; ensure nested layout (subdepartments under parent folder) from preset.
                mapping = ensure_parent_from_preset(mapping)
                if not save_project_departments(self._project_root, mapping):
                    QMessageBox.warning(self, "Settings", "Failed to save Department Mapping to project.")

        # Persist project-level type mapping when project is set and table was built.
        if self._project_root is not None and self._type_mapping_table is not None:
            type_mapping: dict[str, dict] = {}
            for row in range(self._type_mapping_table.rowCount()):
                id_item = self._type_mapping_table.item(row, 0)
                label_item = self._type_mapping_table.item(row, 1)
                folder_item = self._type_mapping_table.item(row, 2)
                if id_item is None or folder_item is None:
                    continue
                type_id = (id_item.text() or "").strip()
                folder = (folder_item.text() or "").strip()
                if not type_id or not folder:
                    continue
                label = (label_item.text() or "").strip() if label_item else type_id
                type_mapping[type_id] = {"label": label or type_id, "folder": folder}
            if type_mapping and not save_project_types(self._project_root, type_mapping):
                QMessageBox.warning(self, "Settings", "Failed to save Type Mapping to project.")

        # Persist project-level structure mapping when project is set and table was built.
        if self._project_root is not None and self._structure_mapping_table is not None:
            struct_mapping: dict[str, dict[str, str]] = {}
            for row in range(self._structure_mapping_table.rowCount()):
                id_item = self._structure_mapping_table.item(row, 0)
                label_item = self._structure_mapping_table.item(row, 1)
                folder_item = self._structure_mapping_table.item(row, 2)
                if id_item is None or folder_item is None:
                    continue
                fid = (id_item.text() or "").strip()
                folder = (folder_item.text() or "").strip()
                if not fid or not folder:
                    continue
                label = (label_item.text() or "").strip() if label_item else fid
                struct_mapping[fid] = {"label": label or fid, "folder": folder}
            if struct_mapping and not save_project_structure(self._project_root, struct_mapping):
                QMessageBox.warning(self, "Settings", "Failed to save Structure Mapping to project.")

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

