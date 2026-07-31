"""Data models for Fusion comp open preflight (scan + user plan)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from monostudio.core.comp_render_paths import CompSaverSpec
from monostudio.core.comp_saver_io import CompSaverAudit, CompSaverAuditStatus
from monostudio.core.comp_upstream_render_check import UpstreamRenderIssue, UpstreamRenderStatus

CompPreflightApplyMode = Literal["new_version", "current_version"]


def is_upstream_version_upgrade(issue: UpstreamRenderIssue) -> bool:
    """True when applying would bump Loader paths to a newer render folder on disk."""
    return (
        issue.status == UpstreamRenderStatus.STALE
        and issue.latest_version is not None
        and issue.latest_version > issue.comp_version
    )


@dataclass(frozen=True)
class CompPreflightApplyResult:
    ok: bool
    target_path: Path
    discord_notify_skipped: bool = False


@dataclass
class CompPreflightScan:
    comp_path: Path
    spec: CompSaverSpec
    entity_name: str | None
    saver_audit: CompSaverAudit
    upstream_issues: list[UpstreamRenderIssue] = field(default_factory=list)
    saver_already_connected: bool = False

    @property
    def saver_needs_attention(self) -> bool:
        return self.saver_audit.status not in (
            CompSaverAuditStatus.OK,
            CompSaverAuditStatus.UNREADABLE,
        )

    @property
    def saver_missing_end_render_script(self) -> bool:
        audit = self.saver_audit
        if audit.status in (
            CompSaverAuditStatus.UNREADABLE,
            CompSaverAuditStatus.MISSING_MANAGED,
        ):
            return False
        return not audit.has_end_render_script

    @property
    def saver_shows_in_hub(self) -> bool:
        return self.saver_needs_attention or self.saver_missing_end_render_script

    @property
    def upstream_actionable(self) -> list[UpstreamRenderIssue]:
        return [
            i
            for i in self.upstream_issues
            if i.status in (UpstreamRenderStatus.STALE, UpstreamRenderStatus.MISSING_ON_DISK)
        ]

    @property
    def has_issues(self) -> bool:
        return self.saver_shows_in_hub or bool(self.upstream_actionable)

    @property
    def readable(self) -> bool:
        return self.saver_audit.status != CompSaverAuditStatus.UNREADABLE


@dataclass
class CompPreflightPlan:
    apply_mode: CompPreflightApplyMode = "new_version"

    saver_update_path: bool = False
    saver_create_folder: bool = True
    saver_connect_rightmost: bool = True
    saver_end_render_script: bool = False
    saver_reviewed: bool = False
    saver_apply_enabled: bool = True

    upstream_selected: list[UpstreamRenderIssue] = field(default_factory=list)
    sync_loader_range: bool = False
    upstream_reviewed: bool = False
    upstream_apply_enabled: bool = True

    def init_saver_defaults(self, scan: CompPreflightScan) -> None:
        if scan.saver_audit.status == CompSaverAuditStatus.MISSING_MANAGED:
            self.saver_update_path = True
        elif scan.saver_needs_attention:
            self.saver_update_path = True
        self.saver_create_folder = True
        self.saver_connect_rightmost = not scan.saver_already_connected
        self.saver_end_render_script = False

    def init_upstream_defaults(self, scan: CompPreflightScan) -> None:
        self.upstream_selected = [
            i for i in scan.upstream_actionable if is_upstream_version_upgrade(i)
        ]
        self.sync_loader_range = False

    def saver_will_apply(self) -> bool:
        if not self.saver_apply_enabled:
            return False
        return self.saver_reviewed and (
            self.saver_update_path
            or self.saver_create_folder
            or self.saver_connect_rightmost
            or self.saver_end_render_script
        )

    def upstream_will_apply(self) -> bool:
        if not self.upstream_apply_enabled:
            return False
        if not self.upstream_reviewed:
            return False
        return bool(self.upstream_selected) or self.sync_loader_range

    @property
    def apply_to_new_version(self) -> bool:
        return self.apply_mode == "new_version"

    def modifies_comp_file(self) -> bool:
        if self.saver_will_apply() and (
            self.saver_update_path or self.saver_connect_rightmost or self.saver_end_render_script
        ):
            return True
        return self.upstream_will_apply()

    def will_apply_anything(self) -> bool:
        if self.saver_will_apply():
            return True
        return self.upstream_will_apply()
