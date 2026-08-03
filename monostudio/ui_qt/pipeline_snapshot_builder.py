"""Build PipelineRowSnapshot from ViewItem + presentation context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.models import Asset, Shot
from monostudio.ui_qt.pipeline_row_presentation_cache import PipelineRowPresentationCache
from monostudio.ui_qt.pipeline_snapshot import (
    AlertChip,
    DccBadgeSnapshot,
    DimState,
    PipelineRowSnapshot,
    StatusChip,
)
from monostudio.ui_qt.view_items import ViewItem, display_name_for_item

_MAX_DCC = 4


@dataclass(slots=True)
class SnapshotBuildContext:
    active_department: str | None
    browser_mode: str = "work"
    show_publish: bool = False
    dept_registry: object | None = None
    get_active_dcc: Callable[[str, str], str | None] | None = None
    notes_badge_state: Callable[[str], tuple[int, str]] | None = None
    status_for_item: Callable[[ViewItem], StatusChip | None] | None = None
    thumb_token_for: Callable[[ViewItem], str] | None = None
    dim_for_item: Callable[[ViewItem], DimState] | None = None


def _dcc_snapshots_from_cache(
    item: ViewItem,
    ctx: SnapshotBuildContext,
    cache: PipelineRowPresentationCache,
) -> tuple[DccBadgeSnapshot, ...]:
    if ctx.show_publish or not isinstance(item.ref, (Asset, Shot)) or not item.path:
        return ()
    try:
        reg = get_default_dcc_registry()
    except Exception:
        reg = None
    badges = cache.dcc_badges_for(
        item.ref,
        path=str(item.path),
        active_department=ctx.active_department,
        dept_registry=ctx.dept_registry,
    )
    out: list[DccBadgeSnapshot] = []
    for icon, dcc_id, status in badges[:_MAX_DCC]:
        slug = ""
        color = ""
        if status == "exists" and reg is not None:
            try:
                info = reg.get_dcc_info(dcc_id)
            except Exception:
                info = None
            if isinstance(info, dict):
                slug = str(info.get("brand_icon_slug") or "")
                color = str(info.get("brand_color_hex") or "")
        out.append(
            DccBadgeSnapshot(
                dcc_id=dcc_id,
                status=status,  # type: ignore[arg-type]
                brand_slug=slug,
                color_hex=color,
            )
        )
    return tuple(out)


def _alert_from_health_and_notes(
    item: ViewItem,
    ctx: SnapshotBuildContext,
    cache: PipelineRowPresentationCache,
) -> AlertChip | None:
    if not isinstance(item.ref, (Asset, Shot)) or not item.path:
        return None
    dept = (ctx.active_department or "").strip()
    dcc = None
    if dept and ctx.get_active_dcc:
        try:
            dcc = ctx.get_active_dcc(str(item.path), dept)
        except Exception:
            dcc = None
    health = cache.health_for(
        item.ref,
        path=str(item.path),
        active_department=dept or None,
        active_dcc_id=dcc,
    )
    notes_count = 0
    notes_mode = "empty"
    if ctx.notes_badge_state:
        try:
            notes_count, notes_mode = ctx.notes_badge_state(str(item.path))
        except Exception:
            pass
    # One alert slot: prefer health warn/error over notes
    if health is not None and health.level in ("warn", "error"):
        return AlertChip(
            kind="health",
            level=health.level,
            icon_name=health.icon_name,
            color_hex=health.color_hex,
        )
    if notes_count > 0 or notes_mode not in ("", "empty"):
        return AlertChip(kind="notes", notes_count=notes_count, notes_mode=notes_mode)
    if health is not None:
        return AlertChip(
            kind="health",
            level=health.level,
            icon_name=health.icon_name,
            color_hex=health.color_hex,
        )
    return None


def build_row_snapshot(
    item: ViewItem,
    ctx: SnapshotBuildContext,
    *,
    cache: PipelineRowPresentationCache | None = None,
) -> PipelineRowSnapshot:
    """Precompute presentation state for one grid/list row."""
    row_cache = cache or PipelineRowPresentationCache()
    path = str(item.path) if item.path else ""
    display = display_name_for_item(item)
    dim: DimState = "none"
    if ctx.dim_for_item:
        try:
            dim = ctx.dim_for_item(item)
        except Exception:
            dim = "none"
    thumb_token = path
    if ctx.thumb_token_for:
        try:
            thumb_token = ctx.thumb_token_for(item) or path
        except Exception:
            thumb_token = path
    status: StatusChip | None = None
    if ctx.status_for_item:
        try:
            status = ctx.status_for_item(item)
        except Exception:
            status = None
    alerts = _alert_from_health_and_notes(item, ctx, row_cache)
    dcc_stack = _dcc_snapshots_from_cache(item, ctx, row_cache)
    meta: tuple[str, ...] = ()
    return PipelineRowSnapshot(
        path=path,
        display_name=display,
        dim=dim,
        thumb_token=thumb_token,
        status=status,
        alerts=alerts,
        dcc_stack=dcc_stack,
        meta=meta,
    )
