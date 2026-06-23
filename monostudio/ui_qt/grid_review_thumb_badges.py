"""Review-mode grid thumb badges: render version (color state) + schedule deadline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter

from monostudio.core.models import Asset, Shot
from monostudio.core.project_schedule import ProjectSchedule
from monostudio.core.schedule_planner import (
    STATUS_DONE,
    BarStore,
    bars_for_row,
    next_unmet_goal_in_row,
    primary_bar_for_row,
    summarize_entity_schedule,
)
from monostudio.ui_qt.style import MONOS_COLORS, monos_font, schedule_attention_accent

RenderBadgeState = Literal["none", "current", "outdated"]
ScheduleBadgeState = Literal["overdue", "due_today", "on_track", "unscheduled", "done"]

_REVIEW_RENDER_COLORS: dict[RenderBadgeState, str] = {
    "none": MONOS_COLORS.get("zinc_600", "#52525b"),
    "current": MONOS_COLORS.get("emerald_500", "#10b981"),
    "outdated": MONOS_COLORS.get("red_500", "#ef4444"),
}

_SCHEDULE_COLORS: dict[ScheduleBadgeState, str] = {
    "overdue": schedule_attention_accent("overdue"),
    "due_today": MONOS_COLORS.get("amber_500", "#f59e0b"),
    "on_track": MONOS_COLORS.get("blue_500", "#3b82f6"),
    "unscheduled": schedule_attention_accent("unscheduled"),
    "done": MONOS_COLORS.get("emerald_500", "#10b981"),
}

_SCHEDULE_LABELS: dict[ScheduleBadgeState, str] = {
    "overdue": "Overdue",
    "due_today": "Today",
    "on_track": "Due",
    "unscheduled": "No plan",
    "done": "Done",
}


@dataclass(frozen=True)
class GridReviewRenderBadge:
    state: RenderBadgeState
    version_text: str
    tooltip: str
    bg_hex: str


@dataclass(frozen=True)
class GridScheduleDeadlineBadge:
    state: ScheduleBadgeState
    label_text: str
    tooltip: str
    accent_hex: str


def _norm_department(dep: str | None) -> str:
    return (dep or "").strip().casefold()


def _work_file_version_from_path(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    stem = (path.stem or "").strip()
    idx = stem.rfind("_v")
    if idx < 0 or len(stem) < idx + 5:
        return None
    mid = stem[idx + 2 : idx + 5]
    if len(mid) == 3 and mid.isdigit():
        return int(mid)
    return None


def _version_from_folder_name(name: str) -> int | None:
    m = re.search(r"(?:^|_)v(\d{3,})(?:_|$)", name or "", flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _department_work_path(ref: Asset | Shot, active_department: str | None) -> Path | None:
    dep_cf = _norm_department(active_department)
    if not dep_cf:
        return None
    for d in getattr(ref, "departments", ()) or ():
        if (d.name or "").strip().casefold() != dep_cf:
            continue
        wp = getattr(d, "work_path", None)
        if isinstance(wp, Path) and wp.is_dir():
            return wp
    return None


def _work_file_path(
    ref: Asset | Shot,
    active_department: str | None,
    active_dcc_id: str | None = None,
) -> Path | None:
    dep_cf = _norm_department(active_department)
    if not dep_cf:
        return None
    dcc_pref = (active_dcc_id or "").strip() or None
    fallback: Path | None = None
    for (dept_id, dcc_id), state in getattr(ref, "dcc_work_states", ()) or ():
        if (dept_id or "").strip().casefold() != dep_cf:
            continue
        wp = getattr(state, "work_file_path", None)
        if not isinstance(wp, Path) or not wp.is_file():
            continue
        if dcc_pref and (dcc_id or "").strip() == dcc_pref:
            return wp
        if fallback is None:
            fallback = wp
    return fallback


def _department_review_index(ref: Asset | Shot, active_department: str | None):
    dep_cf = _norm_department(active_department)
    if not dep_cf:
        return None
    for d in getattr(ref, "departments", ()) or ():
        if (d.name or "").strip().casefold() != dep_cf:
            continue
        return getattr(d, "review_index", None)
    return None


def _format_schedule_date(d: date) -> str:
    return d.strftime("%b %d, %Y")


def resolve_grid_review_render_badge(
    ref: Asset | Shot,
    active_department: str | None,
    active_dcc_id: str | None = None,
) -> GridReviewRenderBadge:
    """Render preview state for review grid thumb (mirrors Inspector version badge rules)."""
    dep = (active_department or "").strip()
    gray = _REVIEW_RENDER_COLORS["none"]
    green = _REVIEW_RENDER_COLORS["current"]
    red = _REVIEW_RENDER_COLORS["outdated"]

    if not dep:
        return GridReviewRenderBadge("none", "—", "Select a department to review render status.", gray)

    work_path = _department_work_path(ref, dep)
    work_file = _work_file_path(ref, dep, active_dcc_id)
    latest_v = _work_file_version_from_path(work_file)
    label_latest = f"v{latest_v:03d}" if latest_v is not None else ""

    if work_path is None or not work_path.is_dir():
        tip = "No work folder for this department."
        if label_latest:
            tip = f"Latest work: {label_latest}\n{tip}"
        return GridReviewRenderBadge("none", "—", tip, gray)

    from monostudio.core.sequence_preview import (
        resolve_best_available_sequence_folder,
        resolve_sequence_folder,
        sequence_folder_has_frames,
    )
    from monostudio.core.video_media import resolve_work_preview_video

    cur = resolve_sequence_folder(work_path, work_file)
    if cur is not None and cur.is_dir() and sequence_folder_has_frames(cur):
        badge_text = label_latest if label_latest else "—"
        tip = (
            f"Preview: {badge_text} (matches latest work)."
            if label_latest
            else "Preview matches latest work."
        )
        return GridReviewRenderBadge("current", badge_text, tip, green)

    preview_vid = resolve_work_preview_video(work_path, work_file)
    if preview_vid is not None and preview_vid.is_file():
        badge_text = label_latest if label_latest else "—"
        tip = (
            f"Playblast: {badge_text} (matches latest work)."
            if label_latest
            else "Playblast matches latest work."
        )
        return GridReviewRenderBadge("current", badge_text, tip, green)

    best = resolve_best_available_sequence_folder(work_path)
    best_ok = best is not None and best.is_dir() and sequence_folder_has_frames(best)
    idx = _department_review_index(ref, dep)
    has_render_scan = bool(idx and idx.has_render)

    if not best_ok and not has_render_scan:
        tip = "No render or playblast for latest work yet."
        if label_latest:
            tip = f"Latest work: {label_latest}\n{tip}"
        return GridReviewRenderBadge("none", "—", tip, gray)

    fallback_v = _version_from_folder_name(best.name) if best is not None else None
    badge_text = f"v{fallback_v:03d}" if fallback_v is not None else (label_latest or "—")

    if label_latest and badge_text != label_latest:
        tip = (
            f"Preview: {badge_text} (older preview).\n"
            f"Latest work: {label_latest} (no preview yet)."
        )
    elif label_latest:
        tip = (
            f"Preview: older preview.\n"
            f"Latest work: {label_latest} (no preview yet)."
        )
    else:
        tip = "Preview: older preview (latest work has no preview yet)."
    return GridReviewRenderBadge("outdated", badge_text, tip, red)


def resolve_grid_schedule_deadline_badge(
    bars: BarStore,
    schedule: ProjectSchedule | None,
    *,
    entity_kind: str,
    entity_rel: str,
    active_department: str | None,
) -> GridScheduleDeadlineBadge | None:
    """Compact schedule/deadline chip for review grid thumb."""
    dep = (active_department or "").strip()
    if not dep:
        return None
    if schedule is None:
        return GridScheduleDeadlineBadge(
            "unscheduled",
            _SCHEDULE_LABELS["unscheduled"],
            "Not on schedule — open Schedule to plan delivery.",
            _SCHEDULE_COLORS["unscheduled"],
        )

    rel = entity_rel.replace("\\", "/")
    summary = summarize_entity_schedule(
        bars,
        schedule,
        entity_kind=entity_kind,
        entity_rel=rel,
        active_department=dep,
    )
    row_bars = bars_for_row(bars, entity_kind, rel, dep)
    focus_bar = next_unmet_goal_in_row(row_bars) or primary_bar_for_row(bars, entity_kind, rel, dep)

    if focus_bar is not None and (focus_bar.goal_met or focus_bar.status == STATUS_DONE):
        due_txt = _format_schedule_date(focus_bar.due)
        return GridScheduleDeadlineBadge(
            "done",
            _SCHEDULE_LABELS["done"],
            f"Done — due {due_txt}",
            _SCHEDULE_COLORS["done"],
        )

    if summary.focus_due is not None:
        due_txt = _format_schedule_date(summary.focus_due)
        if summary.focus_overdue:
            return GridScheduleDeadlineBadge(
                "overdue",
                _SCHEDULE_LABELS["overdue"],
                f"Overdue — due {due_txt}",
                _SCHEDULE_COLORS["overdue"],
            )
        if summary.focus_due == date.today():
            return GridScheduleDeadlineBadge(
                "due_today",
                _SCHEDULE_LABELS["due_today"],
                f"Due today — {due_txt}",
                _SCHEDULE_COLORS["due_today"],
            )
        return GridScheduleDeadlineBadge(
            "on_track",
            _SCHEDULE_LABELS["on_track"],
            f"On track — due {due_txt}",
            _SCHEDULE_COLORS["on_track"],
        )

    if not summary.has_plan:
        return GridScheduleDeadlineBadge(
            "unscheduled",
            _SCHEDULE_LABELS["unscheduled"],
            "Not on schedule — open Schedule to plan delivery.",
            _SCHEDULE_COLORS["unscheduled"],
        )
    return None


def layout_grid_review_thumb_badges(
    thumb: QRect,
    *,
    render_label: str,
    schedule_label: str | None,
    font: QFont,
) -> tuple[QRect, QRect | None]:
    """Thumb badge positions: schedule bottom-left, render bottom-right."""
    fm = QFontMetrics(font)
    pill_pad_x = 10
    pill_h = 24
    gap = 4
    label = (render_label or "").strip() or "—"
    text_w = fm.horizontalAdvance(label)
    pill_w = text_w + pill_pad_x * 2
    render_x = thumb.right() - 12 - pill_w
    render_y = thumb.bottom() - 12 - pill_h
    render_rect = QRect(render_x, render_y, pill_w, pill_h)

    schedule_rect: QRect | None = None
    sl = (schedule_label or "").strip()
    if sl:
        sw = fm.horizontalAdvance(sl)
        sched_w = sw + pill_pad_x * 2
        sched_x = thumb.left() + 12
        sched_y = render_y
        schedule_rect = QRect(sched_x, sched_y, sched_w, pill_h)
        # Prevent overlap when both pills are visible (clamp schedule width).
        max_w = render_rect.left() - gap - schedule_rect.left()
        if max_w < schedule_rect.width():
            schedule_rect.setWidth(max(0, int(max_w)))
    return render_rect, schedule_rect


def paint_grid_review_render_pill(
    painter: QPainter,
    rect: QRect,
    badge: GridReviewRenderBadge,
    *,
    hovered: bool = False,
) -> None:
    label = (badge.version_text or "").strip() or "—"
    pill_r = rect.height() // 2
    bg = QColor(badge.bg_hex)
    if hovered:
        bg = bg.lighter(112)
    bg.setAlpha(220 if hovered else 200)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg)
    painter.drawRoundedRect(rect, pill_r, pill_r)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)


def paint_grid_schedule_deadline_chip(
    painter: QPainter,
    rect: QRect,
    badge: GridScheduleDeadlineBadge,
    *,
    hovered: bool = False,
) -> None:
    label = (badge.label_text or "").strip()
    if not label:
        return
    pill_r = rect.height() // 2
    bg = QColor(badge.accent_hex)
    if hovered:
        bg = bg.lighter(112)
    bg.setAlpha(220 if hovered else 200)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg)
    painter.drawRoundedRect(rect, pill_r, pill_r)
    painter.setPen(QColor("#ffffff"))
    text_pad_left = 10
    text_rect = rect.adjusted(text_pad_left, 0, -6, 0)
    painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), label)


def review_badge_font() -> QFont:
    return monos_font("Inter", 9, QFont.Weight.Bold)
