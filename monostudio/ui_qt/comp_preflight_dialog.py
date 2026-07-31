"""Detail configuration dialogs for Fusion comp preflight (opened from hub)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.comp_loader_io import parse_comp_global_range, read_comp_text
from monostudio.core.comp_fusion_scripts import find_project_root, fusion_render_webhook_urls
from monostudio.core.comp_render_paths import CompSaverSpec
from monostudio.core.comp_saver_io import CompSaverAudit, CompSaverAuditStatus
from monostudio.core.comp_upstream_render_check import UpstreamRenderIssue, UpstreamRenderStatus
from monostudio.ui_qt.comp_preflight_models import CompPreflightPlan
from monostudio.ui_qt.style import MonosDialog

_SCROLL_MAX_H = 360
_CARD_PAD = 12
_CONTENT_INDENT = 24


def _mono_label(text: str, *, word_wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("DialogHint")
    lbl.setProperty("mono", True)
    lbl.setWordWrap(word_wrap)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("PipelineWorkflowGroupTitle")
    return lbl


def _preflight_option_checkbox(text: str, parent: QWidget) -> QCheckBox:
    cb = QCheckBox(text, parent)
    cb.setObjectName("CompPreflightOptionCheck")
    return cb


class _ElidedMonoPathLabel(QLabel):
    """Mono path label — middle-elide on resize, full path in tooltip."""

    def __init__(self, full_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = full_text
        self.setObjectName("DialogHint")
        self.setProperty("mono", True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setToolTip(full_text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        w = max(1, self.width())
        fm = QFontMetrics(self.font())
        self.setText(fm.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, w))


class _LoaderIssueCard(QFrame):
    def __init__(
        self,
        *,
        issue: UpstreamRenderIssue,
        checked: bool,
        parent: QWidget | None = None,
        apply_enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self.issue = issue
        self.setObjectName("CompPreflightLoaderCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(_CARD_PAD, _CARD_PAD, _CARD_PAD, _CARD_PAD)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        dept = (issue.department or "upstream").title()
        self._cb: QCheckBox | None
        if apply_enabled:
            self._cb = QCheckBox(dept, self)
            self._cb.setChecked(checked)
            header.addWidget(self._cb, 0)
        else:
            self._cb = None
            dept_lbl = QLabel(dept, self)
            dept_lbl.setObjectName("DialogBody")
            header.addWidget(dept_lbl, 0)

        header.addStretch(1)

        if issue.status == UpstreamRenderStatus.WRONG_ENTITY:
            latest = issue.latest_version
            if latest is not None:
                badge_text = (
                    f"{issue.entity_name} → {issue.expected_entity_name} · "
                    f"v{issue.comp_version:03d} → v{latest:03d}"
                )
            else:
                badge_text = f"{issue.entity_name} → {issue.expected_entity_name}"
            mismatch_lbl = QLabel(badge_text, self)
            mismatch_lbl.setObjectName("CompPreflightVersionBadge")
            mismatch_lbl.setProperty("warning", True)
            mismatch_lbl.setToolTip(issue.message)
            mismatch_lbl.style().unpolish(mismatch_lbl)
            mismatch_lbl.style().polish(mismatch_lbl)
            header.addWidget(mismatch_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            latest = issue.latest_version
            if latest is not None:
                version_lbl = QLabel(f"v{issue.comp_version:03d} → v{latest:03d}", self)
                version_lbl.setObjectName("CompPreflightVersionBadge")
                downgrade = latest < issue.comp_version
                if downgrade:
                    version_lbl.setProperty("warning", True)
                    version_lbl.setToolTip(
                        "Latest render on disk is older than what the comp references."
                    )
                    version_lbl.style().unpolish(version_lbl)
                    version_lbl.style().polish(version_lbl)
                header.addWidget(version_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        count_lbl = QLabel(f"{issue.loader_count} loader(s)", self)
        count_lbl.setObjectName("CompPreflightLoaderCount")
        header.addWidget(count_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(_CONTENT_INDENT, 0, 0, 0)
        path_lbl = _ElidedMonoPathLabel(issue.sample_loader_path, self)
        path_row.addWidget(path_lbl, 1)
        root.addLayout(path_row)

        if issue.message and (
            not apply_enabled
            or issue.status == UpstreamRenderStatus.WRONG_ENTITY
            or (
                issue.latest_version is not None
                and issue.latest_version < issue.comp_version
            )
        ):
            hint_row = QHBoxLayout()
            hint_row.setContentsMargins(_CONTENT_INDENT, 0, 0, 0)
            hint = QLabel(issue.message, self)
            hint.setObjectName("DialogHint")
            hint.setWordWrap(True)
            hint_row.addWidget(hint, 1)
            root.addLayout(hint_row)

    def is_checked(self) -> bool:
        return bool(self._cb and self._cb.isChecked())

    def set_checked(self, checked: bool) -> None:
        if self._cb is not None:
            self._cb.setChecked(checked)


class _SaverPathCompareCard(QFrame):
    """Expected vs current Saver output path."""

    def __init__(
        self,
        *,
        expected: str,
        current: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CompPreflightLoaderCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(_CARD_PAD, _CARD_PAD, _CARD_PAD, _CARD_PAD)
        root.setSpacing(8)

        self._add_path_row(root, "Expected", expected, warning=False)
        if current:
            mismatch = current.strip().casefold() != expected.strip().casefold()
            self._add_path_row(root, "Current", current, warning=mismatch)

    def _add_path_row(
        self,
        parent_layout: QVBoxLayout,
        label: str,
        path: str,
        *,
        warning: bool,
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        title = QLabel(label)
        title.setObjectName("DialogHint")
        title.setFixedWidth(56)
        row.addWidget(title, 0, Qt.AlignmentFlag.AlignTop)
        path_lbl = _ElidedMonoPathLabel(path, self)
        if warning:
            path_lbl.setProperty("warning", True)
            path_lbl.style().unpolish(path_lbl)
            path_lbl.style().polish(path_lbl)
        row.addWidget(path_lbl, 1)
        parent_layout.addLayout(row)


class UpstreamPreflightDetailDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        comp_path: Path,
        issues: list[UpstreamRenderIssue],
        plan: CompPreflightPlan,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Render Loaders")
        self.setModal(True)
        self.setMinimumWidth(720)

        self._plan = plan
        self._issues = issues
        self._wrong_entity_issues = [
            i for i in issues if i.status == UpstreamRenderStatus.WRONG_ENTITY
        ]
        self._stale_issues = [i for i in issues if i.status == UpstreamRenderStatus.STALE]
        self._path_update_issues = self._wrong_entity_issues + self._stale_issues

        global_range = None
        try:
            global_range = parse_comp_global_range(read_comp_text(comp_path))
        except OSError:
            global_range = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        intro = QLabel(
            "Choose departments to update. Changes apply when you confirm on the main dialog."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        if self._path_update_issues:
            loader_total = sum(i.loader_count for i in self._path_update_issues)
            summary = QLabel(
                f"{len(self._path_update_issues)} update(s) · {loader_total} loader(s)"
            )
            summary.setObjectName("DialogHint")
            root.addWidget(summary)

        scroll = QScrollArea(self)
        scroll.setObjectName("CompPreflightScroll")
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        body = QWidget(scroll)
        body.setObjectName("CompPreflightScrollBody")
        body.setAutoFillBackground(False)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)

        self._issue_cards: list[_LoaderIssueCard] = []
        if self._path_update_issues:
            paths_header = QHBoxLayout()
            paths_header.addWidget(_section_title("Update loader paths"))
            paths_header.addStretch(1)
            select_all_btn = QPushButton("Select all", body)
            select_all_btn.setFlat(True)
            select_all_btn.setObjectName("DialogHintButton")
            clear_btn = QPushButton("Clear", body)
            clear_btn.setFlat(True)
            clear_btn.setObjectName("DialogHintButton")
            paths_header.addWidget(select_all_btn)
            paths_header.addWidget(clear_btn)
            body_layout.addLayout(paths_header)

            cards_col = QVBoxLayout()
            cards_col.setSpacing(8)
            for issue in self._path_update_issues:
                card = _LoaderIssueCard(
                    issue=issue,
                    checked=issue in plan.upstream_selected,
                    parent=body,
                )
                cards_col.addWidget(card)
                self._issue_cards.append(card)
            body_layout.addLayout(cards_col)

            select_all_btn.clicked.connect(lambda: self._set_all_issues(True))
            clear_btn.clicked.connect(lambda: self._set_all_issues(False))

        info_issues = [
            i
            for i in issues
            if i.status not in (UpstreamRenderStatus.STALE, UpstreamRenderStatus.WRONG_ENTITY)
        ]
        if info_issues:
            body_layout.addWidget(_section_title("Other issues"))
            for issue in info_issues:
                body_layout.addWidget(_mono_label(issue.message))
                info_path_row = QHBoxLayout()
                info_path_row.addWidget(_ElidedMonoPathLabel(issue.sample_loader_path, body), 1)
                body_layout.addLayout(info_path_row)

        body_layout.addWidget(_section_title("Frame range"))

        self._sync_range_cb = _preflight_option_checkbox("Sync frame range to comp GlobalRange", body)
        self._sync_range_cb.setChecked(plan.sync_loader_range)
        if global_range is not None:
            g0, g1 = global_range
            range_hint = QLabel(f"{g0}–{g1} · all versioned render Loaders")
            range_hint.setObjectName("DialogHint")
        else:
            self._sync_range_cb.setEnabled(False)
            self._sync_range_cb.setToolTip("Comp GlobalRange not found in file.")
            range_hint = QLabel("GlobalRange not found in comp file.")
            range_hint.setObjectName("DialogHint")
        body_layout.addWidget(self._sync_range_cb)
        range_hint_row = QHBoxLayout()
        range_hint_row.setContentsMargins(_CONTENT_INDENT, 0, 0, 0)
        range_hint_row.addWidget(range_hint)
        body_layout.addLayout(range_hint_row)

        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.adjustSize()
        content_h = body.sizeHint().height()
        if content_h > _SCROLL_MAX_H:
            scroll.setMinimumHeight(200)
            scroll.setMaximumHeight(_SCROLL_MAX_H)
            root.addWidget(scroll, 1)
        else:
            scroll.setMinimumHeight(content_h)
            scroll.setMaximumHeight(content_h)
            root.addWidget(scroll, 0)

        root.addWidget(_section_title("Opening"))
        opening_path = _ElidedMonoPathLabel(comp_path.name, self)
        opening_path.setToolTip(str(comp_path))
        opening_row = QHBoxLayout()
        opening_row.addWidget(opening_path, 1)
        root.addLayout(opening_row)

        btn_row = QHBoxLayout()
        back_btn = QPushButton("Back", self)
        back_btn.setObjectName("DialogSecondaryButton")
        back_btn.clicked.connect(self.reject)
        btn_row.addWidget(back_btn)
        btn_row.addStretch(1)
        done_btn = QPushButton("Done", self)
        done_btn.setObjectName("DialogPrimaryButton")
        done_btn.setDefault(True)
        done_btn.clicked.connect(self._on_done)
        btn_row.addWidget(done_btn)
        root.addLayout(btn_row)

        self.adjustSize()
        self.resize(max(720, self.width()), min(self.sizeHint().height(), 640))

    def _set_all_issues(self, checked: bool) -> None:
        for card in self._issue_cards:
            card.set_checked(checked)

    def _on_done(self) -> None:
        self._plan.upstream_selected = [card.issue for card in self._issue_cards if card.is_checked()]
        self._plan.sync_loader_range = self._sync_range_cb.isChecked()
        self.accept()


class SaverPreflightDetailDialog(MonosDialog):
    def __init__(
        self,
        *,
        parent=None,
        comp_path: Path,
        spec: CompSaverSpec,
        audit: CompSaverAudit,
        plan: CompPreflightPlan,
        saver_already_connected: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Comp output")
        self.setModal(True)
        self.setMinimumWidth(720)

        self._plan = plan
        self._saver_already_connected = saver_already_connected
        saver_label = audit.managed_tool_var or spec.saver_node_name

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        if audit.status == CompSaverAuditStatus.MISSING_MANAGED:
            intro = QLabel(
                "No managed Saver found. Choose what to add when you confirm on the main dialog."
            )
            summary = QLabel("MONOS_Output · Saver1 · or sole Saver in the flow")
        elif audit.status == CompSaverAuditStatus.MISMATCH:
            intro = QLabel(
                f'Saver "{saver_label}" output path does not match the pipeline. '
                "Choose fixes to apply on the main dialog."
            )
            summary = QLabel("Output path mismatch")
        elif not audit.has_end_render_script:
            intro = QLabel(
                f'Saver "{saver_label}" path is OK. Optionally add End Render Script before opening Fusion.'
            )
            summary = QLabel("Discord notify not configured on this Saver")
        else:
            intro = QLabel("Review comp output options before opening Fusion.")
            summary = QLabel("Saver path OK")
        intro.setWordWrap(True)
        summary.setObjectName("DialogHint")
        root.addWidget(intro)
        root.addWidget(summary)

        path_card = _SaverPathCompareCard(
            expected=spec.saver_path_fusion,
            current=audit.current_path,
            parent=self,
        )
        path_row = QHBoxLayout()
        path_row.addWidget(path_card, 1)
        root.addLayout(path_row)

        root.addWidget(_section_title("Apply when confirmed"))

        options = QFrame(self)
        options.setObjectName("CompPreflightLoaderCard")
        options.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(_CARD_PAD, _CARD_PAD, _CARD_PAD, _CARD_PAD)
        options_layout.setSpacing(8)

        self._update_path_cb = _preflight_option_checkbox(
            "Update Saver output path to pipeline standard",
            options,
        )
        if audit.status == CompSaverAuditStatus.MISSING_MANAGED:
            self._update_path_cb.setText("Add managed Saver (MONOS_Output) with standard output path")
        self._update_path_cb.setChecked(plan.saver_update_path)
        if audit.status == CompSaverAuditStatus.OK:
            self._update_path_cb.setEnabled(False)
        options_layout.addWidget(self._update_path_cb)

        self._create_folder_cb = _preflight_option_checkbox(
            "Create comp render output folder if missing",
            options,
        )
        self._create_folder_cb.setChecked(plan.saver_create_folder)
        options_layout.addWidget(self._create_folder_cb)

        self._connect_cb = _preflight_option_checkbox(
            "Connect Saver to rightmost node in the flow",
            options,
        )
        self._connect_cb.setChecked(
            plan.saver_connect_rightmost and not saver_already_connected
        )
        if saver_already_connected:
            self._connect_cb.setEnabled(False)
            self._connect_cb.setToolTip("Saver is already connected in the comp flow.")
        options_layout.addWidget(self._connect_cb)

        self._end_render_cb = _preflight_option_checkbox(
            "Add End Render Script (Discord notify)",
            options,
        )
        self._end_render_cb.setChecked(plan.saver_end_render_script)
        self._end_render_cb.setToolTip(
            "Installs discord.py under the project. Discord notify goes to every workspace "
            "channel with “Fusion render finished” enabled (Settings → Integrations)."
        )
        options_layout.addWidget(self._end_render_cb)
        end_hint_row = QHBoxLayout()
        end_hint_row.setContentsMargins(_CONTENT_INDENT, 0, 0, 0)
        end_hint = QLabel("Project script: .monostudio/fusion/discord.py")
        end_hint.setObjectName("DialogHint")
        end_hint_row.addWidget(end_hint, 1)
        options_layout.addLayout(end_hint_row)
        project_root = find_project_root(comp_path)
        if project_root is not None and not fusion_render_webhook_urls(project_root):
            no_webhook = QLabel(
                "No Discord webhook for “Fusion render finished”. "
                "Enable it in Settings → Integrations, save, then re-apply."
            )
            no_webhook.setObjectName("DialogHint")
            no_webhook.setProperty("warning", True)
            no_webhook.setWordWrap(True)
            options_layout.addWidget(no_webhook)

        opt_row = QHBoxLayout()
        opt_row.addWidget(options, 1)
        root.addLayout(opt_row)

        root.addWidget(_section_title("Opening"))
        opening_path = _ElidedMonoPathLabel(comp_path.name, self)
        opening_path.setToolTip(str(comp_path))
        opening_row = QHBoxLayout()
        opening_row.addWidget(opening_path, 1)
        root.addLayout(opening_row)

        btn_row = QHBoxLayout()
        back_btn = QPushButton("Back", self)
        back_btn.setObjectName("DialogSecondaryButton")
        back_btn.clicked.connect(self.reject)
        btn_row.addWidget(back_btn)
        btn_row.addStretch(1)
        done_btn = QPushButton("Done", self)
        done_btn.setObjectName("DialogPrimaryButton")
        done_btn.setDefault(True)
        done_btn.clicked.connect(self._on_done)
        btn_row.addWidget(done_btn)
        root.addLayout(btn_row)

        self.adjustSize()
        self.resize(max(720, self.width()), min(self.sizeHint().height(), 560))

    def _on_done(self) -> None:
        self._plan.saver_update_path = self._update_path_cb.isChecked()
        self._plan.saver_create_folder = self._create_folder_cb.isChecked()
        if self._saver_already_connected:
            self._plan.saver_connect_rightmost = False
        else:
            self._plan.saver_connect_rightmost = self._connect_cb.isChecked()
        self._plan.saver_end_render_script = self._end_render_cb.isChecked()
        self.accept()
