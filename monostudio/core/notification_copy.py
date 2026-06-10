"""Bilingual user-facing notification strings — Vietnamese default, English optional."""

from __future__ import annotations

from monostudio.core.notification_preferences import read_notification_vietnamese


def pick_copy(vi: str, en: str, *, vietnamese: bool | None = None) -> str:
    """Pick localized string. When *vietnamese* is omitted, reads app preference (default VI)."""
    use_vi = read_notification_vietnamese() if vietnamese is None else vietnamese
    return vi if use_vi else en


def copy_someone(*, vietnamese: bool | None = None) -> str:
    return pick_copy("Ai đó", "Someone", vietnamese=vietnamese)


def copy_project_fallback(*, vietnamese: bool | None = None) -> str:
    return pick_copy("Dự án", "Project", vietnamese=vietnamese)


def copy_item_fallback(*, vietnamese: bool | None = None) -> str:
    return pick_copy("một mục", "an item", vietnamese=vietnamese)


def copy_team_label(*, vietnamese: bool | None = None) -> str:
    return pick_copy("nhóm", "team", vietnamese=vietnamese)


def copy_source_label(source: str, *, vietnamese: bool | None = None) -> str:
    """Inbox/outbox source folder slug — pipeline path segment, never localized."""
    del vietnamese
    key = (source or "client").strip().lower()
    if key in ("client", "freelancer"):
        return key
    return key or "client"


def copy_file_word(count: int, *, vietnamese: bool | None = None) -> str:
    if vietnamese is None:
        vietnamese = read_notification_vietnamese()
    if vietnamese:
        return "tệp"
    return "files" if count != 1 else "file"


def copy_more_suffix(extra: int, *, vietnamese: bool | None = None) -> str:
    return pick_copy(f"_+{extra} mục khác_", f"_+{extra} more_", vietnamese=vietnamese)


SCHEDULE_DUE_ICON_OVERDUE = "🔴"
SCHEDULE_DUE_ICON_DUE_TODAY = "⚠️"


def schedule_due_line_prefix(*, overdue: bool) -> str:
    icon = SCHEDULE_DUE_ICON_OVERDUE if overdue else SCHEDULE_DUE_ICON_DUE_TODAY
    return f"{icon} "


def copy_schedule_due_footer(
    *,
    overdue_count: int = 0,
    due_today_count: int = 0,
    vietnamese: bool | None = None,
) -> str:
    """Dynamic schedule-due embed footer with icon legend for groups present."""
    overdue_count = max(0, int(overdue_count))
    due_today_count = max(0, int(due_today_count))
    parts_vi: list[str] = []
    parts_en: list[str] = []
    if due_today_count:
        parts_vi.append(f"đến hạn hôm nay ({SCHEDULE_DUE_ICON_DUE_TODAY})")
        parts_en.append(f"due today ({SCHEDULE_DUE_ICON_DUE_TODAY})")
    if overdue_count:
        parts_vi.append(f"quá hạn ({SCHEDULE_DUE_ICON_OVERDUE})")
        parts_en.append(f"overdue ({SCHEDULE_DUE_ICON_OVERDUE})")
    if not parts_vi:
        return pick_copy("Lịch", "Schedule", vietnamese=vietnamese)
    vi_body = " · ".join(parts_vi)
    en_body = " · ".join(parts_en)
    return pick_copy(f"Lịch · {vi_body}", f"Schedule · {en_body}", vietnamese=vietnamese)


def copy_schedule_due_headline(
    *,
    overdue_count: int = 0,
    due_today_count: int = 0,
    vietnamese: bool | None = None,
) -> str:
    """Discord schedule-due summary line (overdue + due today task counts)."""
    overdue_count = max(0, int(overdue_count))
    due_today_count = max(0, int(due_today_count))
    if overdue_count and due_today_count:
        return pick_copy(
            f"**{overdue_count} task quá hạn**, **{due_today_count} task đến hạn hôm nay**",
            (
                f"**{overdue_count} overdue tasks**, "
                f"**{due_today_count} {'task' if due_today_count == 1 else 'tasks'} due today**"
            ),
            vietnamese=vietnamese,
        )
    if overdue_count:
        en = f"**{overdue_count} overdue {'task' if overdue_count == 1 else 'tasks'}**"
        return pick_copy(f"**{overdue_count} task quá hạn**", en, vietnamese=vietnamese)
    if due_today_count:
        en = f"**{due_today_count} {'task' if due_today_count == 1 else 'tasks'} due today**"
        return pick_copy(f"**{due_today_count} task đến hạn hôm nay**", en, vietnamese=vietnamese)
    return pick_copy("**Kiểm tra lịch**", "**Schedule check**", vietnamese=vietnamese)
