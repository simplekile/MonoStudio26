"""Date display formats for schedule timeline bar labels."""

from __future__ import annotations

from datetime import date

DATE_FMT_MD = "md"
DATE_FMT_DM = "dm"
DATE_FMT_ISO = "iso"
DATE_FMT_MDY = "mdy"
DATE_FMT_DMY = "dmy"
DATE_FMT_MON_D = "mon_d"
DATE_FMT_D_MON = "d_mon"
DATE_FMT_MON_D_Y = "mon_d_y"
DATE_FMT_DEFAULT = DATE_FMT_MD

SCHEDULE_DATE_FORMAT_KEY = "schedule/date_display_format"

_VALID_DATE_FORMATS = frozenset(
    {
        DATE_FMT_MD,
        DATE_FMT_DM,
        DATE_FMT_ISO,
        DATE_FMT_MDY,
        DATE_FMT_DMY,
        DATE_FMT_MON_D,
        DATE_FMT_D_MON,
        DATE_FMT_MON_D_Y,
    }
)

# Preview span for View options combo (fixed sample dates).
_PREVIEW_START = date(2026, 6, 1)
_PREVIEW_DUE = date(2026, 6, 5)


def normalize_date_display_format(fmt: str | None) -> str:
    f = (fmt or "").strip()
    return f if f in _VALID_DATE_FORMATS else DATE_FMT_DEFAULT


def format_schedule_date(d: date, fmt_id: str | None = None) -> str:
    fid = normalize_date_display_format(fmt_id)
    if fid == DATE_FMT_MON_D:
        return f"{d.strftime('%b')} {d.day}"
    if fid == DATE_FMT_D_MON:
        return f"{d.day} {d.strftime('%b')}"
    if fid == DATE_FMT_MON_D_Y:
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    patterns = {
        DATE_FMT_MD: "%m/%d",
        DATE_FMT_DM: "%d/%m",
        DATE_FMT_ISO: "%Y-%m-%d",
        DATE_FMT_MDY: "%m/%d/%Y",
        DATE_FMT_DMY: "%d/%m/%Y",
    }
    return d.strftime(patterns[fid])


def format_schedule_date_span(
    start: date,
    due: date,
    fmt_id: str | None = None,
) -> str:
    a = format_schedule_date(start, fmt_id)
    b = format_schedule_date(due, fmt_id)
    if start == due:
        return a
    return f"{a}–{b}"


def date_format_preview(fmt_id: str) -> str:
    return format_schedule_date_span(_PREVIEW_START, _PREVIEW_DUE, fmt_id)


def min_bar_width_for_date_format(fmt_id: str | None) -> int:
    fid = normalize_date_display_format(fmt_id)
    return {
        DATE_FMT_MD: 52,
        DATE_FMT_DM: 52,
        DATE_FMT_ISO: 96,
        DATE_FMT_MDY: 76,
        DATE_FMT_DMY: 76,
        DATE_FMT_MON_D: 64,
        DATE_FMT_D_MON: 64,
        DATE_FMT_MON_D_Y: 88,
    }[fid]
