"""Fusion comp preflight hub — loading, issue list, apply / skip / cancel."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.comp_render_paths import CompSaverSpec, rebuild_comp_saver_spec, resolve_next_comp_work_path
from monostudio.core.comp_saver_io import (
    CompSaverAuditStatus,
    apply_comp_saver_fix,
    audit_comp_saver,
    ensure_render_dir,
    repair_comp_file,
)
from monostudio.core.comp_upstream_render_check import (
    UpstreamRenderStatus,
    apply_upstream_render_updates,
    audit_comp_upstream_renders,
)
from monostudio.ui_qt.comp_preflight_dialog import (
    SaverPreflightDetailDialog,
    UpstreamPreflightDetailDialog,
)
from monostudio.ui_qt.comp_preflight_models import (
    CompPreflightApplyResult,
    CompPreflightPlan,
    CompPreflightScan,
)
from monostudio.ui_qt.page_loading_bar import _AnimatedLoadingStrip
from monostudio.ui_qt.style import MonosDialog

PreflightHubResult = Literal["apply", "skip", "cancel"]


class _CompPreflightInplaceConfirmDialog(MonosDialog):
    """Confirm overwriting the current comp version in place."""

    def __init__(self, *, parent=None, comp_path: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modify current comp version?")
        self.setModal(True)
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        body = QLabel(
            "Changes will be written directly to the current comp file. "
            "This cannot be undone from MonoStudio.\n\n"
            f"{comp_path}"
        )
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        ok_btn = QPushButton("Modify current version", self)
        ok_btn.setObjectName("DialogPrimaryButton")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)


def scan_comp_preflight(
    *,
    comp_path: Path,
    spec: CompSaverSpec,
    entity_name: str | None,
) -> CompPreflightScan:
    repair_comp_file(comp_path)
    saver_audit = audit_comp_saver(comp_path, spec)
    upstream = audit_comp_upstream_renders(comp_path, entity_name=entity_name)
    try:
        from monostudio.core.comp_saver_io import managed_saver_is_connected, read_comp_text

        saver_already_connected = managed_saver_is_connected(read_comp_text(comp_path))
    except OSError:
        saver_already_connected = False
    return CompPreflightScan(
        comp_path=comp_path,
        spec=spec,
        entity_name=entity_name,
        saver_audit=saver_audit,
        upstream_issues=upstream,
        saver_already_connected=saver_already_connected,
    )


class CompPreflightLoadingDialog(MonosDialog):
    def __init__(self, *, parent=None, message: str = "Checking comp before Fusion opens…") -> None:
        super().__init__(parent)
        self.setWindowTitle("Fusion comp check")
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        lbl = QLabel(message)
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        self._strip = _AnimatedLoadingStrip(self)
        self._strip.start()
        root.addWidget(self._strip)


class _IssueRow(QFrame):
    def __init__(
        self,
        *,
        parent: QWidget | None,
        title: str,
        summary: str,
        status: str,
        configured: bool,
        checked: bool,
        on_checked_changed,
        on_click,
        skipped: bool = False,
    ) -> None:
        super().__init__(parent)
        self._on_click = on_click
        self.setObjectName("CompPreflightIssueRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        if skipped:
            self.setProperty("skipped", True)
            self.style().unpolish(self)
            self.style().polish(self)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)

        self._checkbox = QCheckBox(self)
        self._checkbox.setChecked(checked)
        self._checkbox.toggled.connect(on_checked_changed)
        row.addWidget(self._checkbox, 0, Qt.AlignmentFlag.AlignTop)

        self._content = QWidget(self)
        self._content.setCursor(Qt.CursorShape.PointingHandCursor)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        text_col = QVBoxLayout(self._content)
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("DialogSectionTitle")
        text_col.addWidget(title_lbl)
        summary_lbl = QLabel(summary)
        summary_lbl.setObjectName("DialogHint")
        summary_lbl.setWordWrap(True)
        text_col.addWidget(summary_lbl)
        row.addWidget(self._content, 1)

        self._status_lbl = QLabel(status)
        self._status_lbl.setObjectName("DialogHint")
        self._set_status_style(configured=configured, skipped=skipped)
        row.addWidget(
            self._status_lbl,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        def _open_detail(_event=None) -> None:
            if callable(self._on_click):
                self._on_click()

        self._content.mousePressEvent = _open_detail  # type: ignore[method-assign]

    def _set_status_style(self, *, configured: bool, skipped: bool) -> None:
        if skipped:
            self._status_lbl.setStyleSheet("color: #71717a;")
        elif configured:
            self._status_lbl.setStyleSheet("color: #4ade80;")
        else:
            self._status_lbl.setStyleSheet("color: #fbbf24;")


class CompPreflightHubDialog(MonosDialog):
    def __init__(self, *, parent=None, scan: CompPreflightScan) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fusion comp check")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(320)

        self._scan = scan
        self._plan = CompPreflightPlan()
        if scan.saver_shows_in_hub:
            self._plan.init_saver_defaults(scan)
        if scan.upstream_actionable:
            self._plan.init_upstream_defaults(scan)
        self._result: PreflightHubResult = "cancel"
        self._next_comp_path = resolve_next_comp_work_path(scan.comp_path, prefix=scan.spec.prefix)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel(
            "Review each item below, then apply fixes, open without changes, or cancel."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._issues_host = QVBoxLayout()
        self._issues_host.setSpacing(8)
        root.addLayout(self._issues_host)

        apply_title = QLabel("Apply changes to")
        apply_title.setObjectName("DialogSectionTitle")
        root.addWidget(apply_title)

        self._apply_new_rb = QRadioButton(
            f"New version — {self._next_comp_path.name} (recommended)",
            self,
        )
        self._apply_current_rb = QRadioButton(
            f"Current version — {scan.comp_path.name}",
            self,
        )
        self._apply_new_rb.setChecked(True)
        self._apply_new_rb.toggled.connect(self._on_apply_mode_changed)
        self._apply_current_rb.toggled.connect(self._on_apply_mode_changed)
        root.addWidget(self._apply_new_rb)
        root.addWidget(self._apply_current_rb)

        apply_hint = QLabel(
            "New version copies the comp, applies fixes there, and opens that file in Fusion."
        )
        apply_hint.setObjectName("DialogHint")
        apply_hint.setWordWrap(True)
        root.addWidget(apply_hint)

        path_lbl = QLabel(str(scan.comp_path))
        path_lbl.setObjectName("DialogHint")
        path_lbl.setWordWrap(True)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(path_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("DialogSecondaryButton")
        skip_btn = QPushButton("Open without changes", self)
        skip_btn.setObjectName("DialogSecondaryButton")
        self._apply_btn = QPushButton("Apply and open", self)
        self._apply_btn.setObjectName("DialogPrimaryButton")
        self._apply_btn.setDefault(True)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(self._apply_btn)
        root.addLayout(btn_row)

        cancel_btn.clicked.connect(self._on_cancel)
        skip_btn.clicked.connect(self._on_skip)
        self._apply_btn.clicked.connect(self._on_apply)

        self._rebuild_issue_rows()
        self._sync_apply_button()

    def result(self) -> PreflightHubResult:
        return self._result

    def plan(self) -> CompPreflightPlan:
        return self._plan

    def _on_apply_mode_changed(self) -> None:
        self._plan.apply_mode = "new_version" if self._apply_new_rb.isChecked() else "current_version"

    def _rebuild_issue_rows(self) -> None:
        while self._issues_host.count():
            item = self._issues_host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if self._scan.saver_shows_in_hub:
            self._issues_host.addWidget(self._make_saver_row())

        if self._scan.upstream_actionable:
            self._issues_host.addWidget(self._make_upstream_row())

        self._issues_host.addStretch(1)
        self._sync_apply_button()

    def _all_issues_reviewed(self) -> bool:
        if (
            self._scan.saver_shows_in_hub
            and self._plan.saver_apply_enabled
            and not self._plan.saver_reviewed
        ):
            return False
        if (
            self._scan.upstream_actionable
            and self._plan.upstream_apply_enabled
            and not self._plan.upstream_reviewed
        ):
            return False
        return True

    def _sync_apply_button(self) -> None:
        reviewed = self._all_issues_reviewed()
        self._apply_btn.setEnabled(reviewed)
        if reviewed:
            self._apply_btn.setToolTip("")
        else:
            self._apply_btn.setToolTip(
                "Open each issue above and choose what to apply before continuing."
            )

    def _make_saver_row(self) -> _IssueRow:
        audit = self._scan.saver_audit
        if audit.status == CompSaverAuditStatus.MISSING_MANAGED:
            summary = "Managed Saver (MONOS_Output) is missing from the comp flow."
        elif audit.status == CompSaverAuditStatus.MISMATCH:
            summary = "Saver output path does not match the pipeline convention."
        elif self._scan.saver_missing_end_render_script:
            summary = "End Render Script is not set on the managed Saver (optional Discord notify)."
        else:
            summary = "Review comp output settings."
        skipped = not self._plan.saver_apply_enabled
        if skipped:
            status = "Skipped"
            configured = False
        elif self._plan.saver_reviewed:
            parts: list[str] = []
            if self._plan.saver_update_path:
                parts.append("path")
            if self._plan.saver_create_folder:
                parts.append("folder")
            if self._plan.saver_connect_rightmost:
                parts.append("connect")
            if self._plan.saver_end_render_script:
                parts.append("notify")
            status = f"Configured ({', '.join(parts) or 'no changes'})"
            configured = True
        else:
            status = "Configure"
            configured = False
        return _IssueRow(
            parent=self,
            title="Comp output (Saver)",
            summary=summary,
            status=status,
            configured=configured,
            checked=self._plan.saver_apply_enabled,
            skipped=skipped,
            on_checked_changed=self._on_saver_apply_toggled,
            on_click=self._open_saver_detail,
        )

    def _make_upstream_row(self) -> _IssueRow:
        actionable = self._scan.upstream_actionable
        n = len(actionable)
        wrong_n = sum(1 for i in actionable if i.status == UpstreamRenderStatus.WRONG_ENTITY)
        if wrong_n:
            summary = (
                f"{n} Loader issue(s) — incl. {wrong_n} wrong shot/asset "
                f"+ versioned render folders under work/render."
            )
        else:
            summary = f"{n} Loader issue(s) — versioned render folders under work/render."
        skipped = not self._plan.upstream_apply_enabled
        if skipped:
            status = "Skipped"
            configured = False
        elif self._plan.upstream_reviewed:
            selected = len(self._plan.upstream_selected)
            sync = " + range sync" if self._plan.sync_loader_range else ""
            status = f"Configured ({selected} path update(s){sync})"
            configured = True
        else:
            status = "Configure"
            configured = False
        return _IssueRow(
            parent=self,
            title="Render Loaders",
            summary=summary,
            status=status,
            configured=configured,
            checked=self._plan.upstream_apply_enabled,
            skipped=skipped,
            on_checked_changed=self._on_upstream_apply_toggled,
            on_click=self._open_upstream_detail,
        )

    def _on_saver_apply_toggled(self, enabled: bool) -> None:
        self._plan.saver_apply_enabled = enabled
        self._rebuild_issue_rows()

    def _on_upstream_apply_toggled(self, enabled: bool) -> None:
        self._plan.upstream_apply_enabled = enabled
        self._rebuild_issue_rows()

    def _open_saver_detail(self) -> None:
        if not self._plan.saver_reviewed:
            self._plan.init_saver_defaults(self._scan)
        dialog = SaverPreflightDetailDialog(
            parent=self,
            comp_path=self._scan.comp_path,
            spec=self._scan.spec,
            audit=self._scan.saver_audit,
            plan=self._plan,
            saver_already_connected=self._scan.saver_already_connected,
        )
        if dialog.exec():
            self._plan.saver_reviewed = True
            self._rebuild_issue_rows()

    def _open_upstream_detail(self) -> None:
        if not self._plan.upstream_reviewed:
            self._plan.init_upstream_defaults(self._scan)
        dialog = UpstreamPreflightDetailDialog(
            parent=self,
            comp_path=self._scan.comp_path,
            issues=self._scan.upstream_actionable,
            plan=self._plan,
        )
        if dialog.exec():
            self._plan.upstream_reviewed = True
            self._rebuild_issue_rows()

    def _on_cancel(self) -> None:
        self._result = "cancel"
        self.reject()

    def _on_skip(self) -> None:
        self._result = "skip"
        self.accept()

    def _on_apply(self) -> None:
        self._on_apply_mode_changed()
        if self._plan.apply_mode == "current_version":
            dialog = _CompPreflightInplaceConfirmDialog(parent=self, comp_path=self._scan.comp_path)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        self._result = "apply"
        self.accept()


def _prepare_apply_target(
    scan: CompPreflightScan,
    plan: CompPreflightPlan,
) -> tuple[Path, CompSaverSpec] | None:
    """Resolve target comp path + saver spec; copy to new version when requested."""
    target = scan.comp_path
    spec = scan.spec
    if not plan.will_apply_anything():
        return target, spec
    if not plan.apply_to_new_version:
        return target, spec
    next_path = resolve_next_comp_work_path(target, prefix=spec.prefix)
    if next_path.exists():
        return None
    try:
        shutil.copy2(target, next_path)
    except OSError:
        return None
    target = next_path
    spec = rebuild_comp_saver_spec(target, scan.spec, entity_name=scan.entity_name)
    return target, spec


def apply_preflight_plan(
    scan: CompPreflightScan,
    plan: CompPreflightPlan,
    *,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> CompPreflightApplyResult:
    """Apply selected fixes. Returns target path to open in Fusion."""
    prepared = _prepare_apply_target(scan, plan)
    if prepared is None:
        return CompPreflightApplyResult(ok=False, target_path=scan.comp_path)
    comp_path, spec = prepared
    ok = True
    discord_notify_skipped = False

    if plan.saver_will_apply():
        create = (
            scan.saver_audit.status == CompSaverAuditStatus.MISSING_MANAGED
            and plan.saver_update_path
        )
        needs_saver_patch = (
            plan.saver_update_path
            or plan.saver_connect_rightmost
            or plan.saver_end_render_script
            or scan.saver_audit.status == CompSaverAuditStatus.MISSING_MANAGED
        )
        if needs_saver_patch:
            try:
                from monostudio.core.comp_saver_io import managed_saver_is_connected, read_comp_text

                already_connected = managed_saver_is_connected(read_comp_text(comp_path))
            except OSError:
                already_connected = scan.saver_already_connected
            result = apply_comp_saver_fix(
                comp_path,
                spec,
                create_if_missing=create,
                connect_to_rightmost=plan.saver_connect_rightmost and not already_connected,
                end_render_script=plan.saver_end_render_script,
                project_root=project_root,
                workspace_root=workspace_root,
            )
            if result == "failed":
                ok = False
        if plan.saver_create_folder:
            ensure_render_dir(spec)
        if plan.saver_end_render_script and project_root is not None:
            from monostudio.core.comp_fusion_scripts import fusion_render_webhook_urls

            if not fusion_render_webhook_urls(project_root, workspace_root=workspace_root):
                discord_notify_skipped = True

    if plan.upstream_will_apply():
        result = apply_upstream_render_updates(
            comp_path,
            scan.upstream_actionable,
            selected_issues=plan.upstream_selected,
            sync_loader_range=plan.sync_loader_range,
            entity_name=scan.entity_name,
        )
        if result == "failed":
            ok = False
    elif not plan.saver_will_apply() and scan.saver_audit.status == CompSaverAuditStatus.OK:
        ensure_render_dir(spec)

    return CompPreflightApplyResult(
        ok=ok,
        target_path=comp_path,
        discord_notify_skipped=discord_notify_skipped,
    )


def run_comp_preflight_hub(
    *,
    parent=None,
    scan: CompPreflightScan,
) -> tuple[PreflightHubResult, CompPreflightPlan]:
    if not scan.readable:
        return "skip", CompPreflightPlan()
    if not scan.has_issues:
        if scan.saver_audit.status == CompSaverAuditStatus.OK:
            ensure_render_dir(scan.spec)
        return "skip", CompPreflightPlan()

    hub = CompPreflightHubDialog(parent=parent, scan=scan)
    hub.exec()
    return hub.result(), hub.plan()
